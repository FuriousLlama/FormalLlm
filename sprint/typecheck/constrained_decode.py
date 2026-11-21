"""
constrained_decode.py — Token-level constrained generation.

After each model-proposed token, calls is_type_valid to mask the logit
distribution to structurally valid next tokens only.

Usage:
  python sprint0/typecheck/constrained_decode.py --prompt "DEF:fId"
  python sprint0/typecheck/constrained_decode.py --prompt "DEF:fSum" --temperature 0.8 --n 5
"""

import sys
import argparse
from functools import lru_cache
from pathlib import Path

import torch
import torch.nn.functional as F
from tokenizers import Tokenizer

sys.path.insert(0, str(Path(__file__).parent.parent / 'model'))
from train import CausalTransformer
from generate import ids_to_text

from checker import is_type_valid, type_check_sequence

PAD_ID, UNK_ID, BOS_ID, EOS_ID = 0, 1, 2, 3
_SKIP_IDS = {PAD_ID, UNK_ID, BOS_ID, EOS_ID}

# ---------------------------------------------------------------------------
# Mask cache: partial (tuple) → frozenset of valid token ids
# This eliminates re-computing O(V × |partial|) parse calls when multiple
# sequences share the same partial prefix (common when k > 1).
# ---------------------------------------------------------------------------
_mask_cache: dict[tuple, frozenset[int]] = {}


def _valid_ids_for(partial: list[str], id_to_tok: dict[int, str], vocab_size: int) -> frozenset[int]:
    key = tuple(partial)
    if key in _mask_cache:
        return _mask_cache[key]
    valid = frozenset(
        tid for tid in range(vocab_size)
        if (tok := id_to_tok.get(tid))
        and tok not in ('<pad>', '<bos>', '<unk>', '<eos>')
        and is_type_valid(partial, tok)
    )
    _mask_cache[key] = valid
    return valid


def clear_mask_cache() -> None:
    """Evict the mask cache (call between rounds to free memory)."""
    _mask_cache.clear()


@torch.inference_mode()
def generate_constrained_batch(
    model: CausalTransformer,
    prompt_ids: list[int],
    id_to_tok: dict[int, str],
    k: int,
    max_new_tokens: int = 200,
    temperature: float = 1.0,
    top_k: int = 0,
) -> list[list[int]]:
    """
    Generate k constrained sequences in parallel using a single (k, T, V) forward
    pass per step instead of k separate (1, T, V) passes.

    Per step:
      1. One model forward pass over all active sequences.
      2. For each sequence, look up valid_ids from the mask cache (keyed by its
         partial token list). Cache hits are O(1); misses compute and store.
      3. Build a (k, V) mask tensor and apply it before sampling.

    Falls back to unconstrained sampling for any sequence with no valid tokens.
    """
    device = next(model.parameters()).device
    vocab_size = len(id_to_tok)
    max_len = model.pos_emb.num_embeddings
    T_prompt = len(prompt_ids)
    total_len = T_prompt + max_new_tokens

    # Shared prompt prefix → all sequences start identical.
    prompt_str = [id_to_tok[i] for i in prompt_ids if i not in _SKIP_IDS]

    # For constrained validation, only track the LAST sequence (target function)
    # not the few-shot context. parse_sequence only validates the first DEF..EOF
    # block, so including context would leave the target sequence unchecked.
    last_def = -1
    for j in range(len(prompt_str) - 1, -1, -1):
        if prompt_str[j] == 'DEF':
            last_def = j
            break
    target_prefix = prompt_str[last_def:] if last_def >= 0 else list(prompt_str)

    # Per-sequence mutable state.
    partials: list[list[str]] = [list(target_prefix) for _ in range(k)]
    generated: list[list[int]] = [[] for _ in range(k)]
    active = [True] * k

    # Pre-allocated sequence buffer: (k, T_prompt + max_new_tokens)
    buf = torch.full((k, total_len), PAD_ID, dtype=torch.long, device=device)
    buf[:, :T_prompt] = torch.tensor(prompt_ids, dtype=torch.long, device=device)
    cur_len = T_prompt

    for _ in range(max_new_tokens):
        if not any(active):
            break

        start = max(0, cur_len - max_len)
        x = buf[:, start:cur_len]                 # (k, T_cur)
        logits = model(x)[:, -1, :]               # (k, V)

        # Build (k, V) validity mask from cache — one lookup per active sequence.
        mask = torch.zeros(k, vocab_size, dtype=torch.bool, device=device)
        for i in range(k):
            if not active[i]:
                continue
            valid_ids = _valid_ids_for(partials[i], id_to_tok, vocab_size)
            if valid_ids:
                mask[i, list(valid_ids)] = True
            if partials[i] and partials[i][-1] == 'EOF':
                mask[i, EOS_ID] = True

        # Mask logits: where mask has any valid token, zero out invalid ones.
        has_valid = mask.any(dim=-1, keepdim=True)          # (k, 1)
        logits = torch.where(has_valid, logits.masked_fill(~mask, float('-inf')), logits)

        if temperature < 1e-6:
            next_ids = logits.argmax(dim=-1)                # (k,)
        else:
            logits = logits / temperature
            if top_k > 0:
                vals, _ = torch.topk(logits, top_k, dim=-1)
                logits[logits < vals[:, -1:]] = float('-inf')
            probs = F.softmax(logits, dim=-1)
            next_ids = torch.multinomial(probs, 1).squeeze(-1)  # (k,)

        buf[:, cur_len] = next_ids
        cur_len += 1

        for i in range(k):
            if active[i]:
                tok_id = int(next_ids[i].item())
                next_tok = id_to_tok.get(tok_id, '<unk>')
                partials[i].append(next_tok)
                generated[i].append(tok_id)
                if tok_id == EOS_ID:
                    active[i] = False

    return generated


@torch.inference_mode()
def generate_constrained(
    model: CausalTransformer,
    prompt_ids: list[int],
    id_to_tok: dict[int, str],
    max_new_tokens: int = 200,
    temperature: float = 1.0,
    top_k: int = 0,
) -> list[int]:
    """
    Autoregressive generation with per-token structural masking.
    At each step, builds a validity mask over the vocab via is_type_valid,
    sets invalid logits to -inf, then samples from the remaining distribution.
    Falls back to unconstrained if no valid token exists.
    """
    device = next(model.parameters()).device
    vocab_size = len(id_to_tok)
    max_len = model.pos_emb.num_embeddings

    # Reconstruct partial token list from prompt (skip BOS/PAD/EOS)
    partial = [id_to_tok[i] for i in prompt_ids if i not in _SKIP_IDS]

    ids = list(prompt_ids)
    generated: list[int] = []

    for _ in range(max_new_tokens):
        clipped = ids if len(ids) <= max_len else ids[-max_len:]
        x      = torch.tensor([clipped], dtype=torch.long, device=device)
        logits = model(x)[0, -1, :]          # (V,)

        # Build validity mask — cached by partial prefix to avoid redundant parses
        valid_ids = _valid_ids_for(partial, id_to_tok, vocab_size)
        mask = torch.zeros(vocab_size, dtype=torch.bool, device=device)
        if valid_ids:
            mask[list(valid_ids)] = True

        # Allow EOS only after the grammar EOF token (sequence is complete)
        if partial and partial[-1] == 'EOF':
            mask[EOS_ID] = True

        if mask.any():
            logits = logits.masked_fill(~mask, float('-inf'))

        if temperature < 1e-6:
            next_id = int(logits.argmax())
        else:
            logits = logits / temperature
            if top_k > 0:
                k = min(top_k, int(mask.sum()) or top_k)
                vals, _ = torch.topk(logits, k)
                logits[logits < vals[-1]] = float('-inf')
            probs   = F.softmax(logits, dim=-1)
            next_id = int(torch.multinomial(probs, 1))

        next_tok = id_to_tok.get(next_id, '<unk>')
        partial.append(next_tok)
        ids.append(next_id)
        generated.append(next_id)

        if next_id == EOS_ID:
            break

    return generated


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--checkpoint',  default='sprint/model/checkpoint.pt')
    p.add_argument('--tokenizer',   default='sprint/corpus/tokenizer.json')
    p.add_argument('--prompt',      default='DEF:fId')
    p.add_argument('--temperature', type=float, default=0.0)
    p.add_argument('--top-k',       type=int,   default=0)
    p.add_argument('--n',           type=int,   default=3)
    p.add_argument('--max-tokens',  type=int,   default=200)
    args = p.parse_args()

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    ckpt   = torch.load(args.checkpoint, map_location=device, weights_only=False)
    cfg    = ckpt['args']
    tok    = Tokenizer.from_file(args.tokenizer)

    model = CausalTransformer(
        vocab_size=ckpt['vocab_size'],
        d_model=cfg['d_model'], nhead=cfg['nhead'],
        num_layers=cfg['num_layers'], max_len=cfg['max_len'], dropout=0.0,
    ).to(device)
    model.load_state_dict(ckpt['model_state'])
    model.eval()
    print(f"[constrained] loaded (epoch={ckpt['epoch']}, val_loss={ckpt.get('val_loss', ckpt.get('loss', 0)):.4f})")

    enc        = tok.encode(args.prompt)
    prompt_ids = enc.ids[:-1]           # strip EOS for prompting
    vsize      = tok.get_vocab_size()
    id_to_tok  = {i: tok.id_to_token(i) for i in range(vsize)}

    print(f"\nGenerating {args.n} constrained samples for: {args.prompt}")
    ok_count = 0
    for i in range(args.n):
        gen_ids = generate_constrained(
            model, prompt_ids, id_to_tok,
            max_new_tokens=args.max_tokens,
            temperature=args.temperature,
            top_k=args.top_k,
        )
        full_ids  = prompt_ids[1:] + gen_ids
        text      = ids_to_text(full_ids, tok)
        tokens    = text.split()
        ok, err   = type_check_sequence(tokens)
        status    = "✓" if ok else f"✗ ({err})"
        print(f"  [{i}] {text}")
        print(f"       {status}")
        if ok:
            ok_count += 1

    print(f"\n{ok_count}/{args.n} pass type check")


if __name__ == '__main__':
    main()
