"""
generate.py — autoregressive sampling from a trained checkpoint.

Usage:
  # Greedy decode, prompt with function header
  python sprint0/model/generate.py --prompt "DEF:fId"

  # Temperature sampling, generate 5 programs
  python sprint0/model/generate.py --prompt "DEF:fMap" --temperature 0.8 --n 5

  # Generate from all DEF: prefixes in corpus
  python sprint0/model/generate.py --all
"""

import json
import argparse
from pathlib import Path

import torch
import torch.nn.functional as F
from tokenizers import Tokenizer

from train import CausalTransformer, PAD_ID, BOS_ID, EOS_ID


# ── sampling ──────────────────────────────────────────────────────────────────

@torch.inference_mode()
def generate(
    model: CausalTransformer,
    prompt_ids: list[int],
    max_new_tokens: int = 200,
    temperature: float = 1.0,
    top_k: int = 0,
) -> list[int]:
    """
    Autoregressive generation from prompt_ids.
    Returns the generated token ids (not including prompt).
    """
    device = next(model.parameters()).device
    ids = list(prompt_ids)
    generated = []

    for _ in range(max_new_tokens):
        x = torch.tensor([ids], dtype=torch.long, device=device)
        logits = model(x)                     # (1, T, V)
        logits = logits[0, -1, :]             # (V,) — last position

        if temperature == 0.0 or temperature < 1e-6:
            # Greedy
            next_id = logits.argmax().item()
        else:
            logits = logits / temperature
            if top_k > 0:
                # Zero out all but top-k
                values, _ = torch.topk(logits, top_k)
                logits[logits < values[-1]] = float("-inf")
            probs = F.softmax(logits, dim=-1)
            next_id = torch.multinomial(probs, 1).item()

        ids.append(next_id)
        generated.append(next_id)

        if next_id == EOS_ID:
            break

    return generated


@torch.inference_mode()
def generate_batch(
    model: CausalTransformer,
    prompt_ids: list[int],
    k: int,
    max_new_tokens: int = 200,
    temperature: float = 1.0,
    top_k: int = 0,
) -> list[list[int]]:
    """
    Generate k independent sequences from the same prompt in a single GPU sweep.
    Each step: one forward pass of shape (k, T, V) instead of k passes of (1, T, V).
    Returns list of k generated token-id lists (not including prompt).
    """
    device = next(model.parameters()).device
    max_len = model.pos_emb.num_embeddings
    T_prompt = len(prompt_ids)
    total_len = T_prompt + max_new_tokens

    # Pre-allocate full buffer to avoid torch.cat reallocation every step.
    buf = torch.full((k, total_len), PAD_ID, dtype=torch.long, device=device)
    buf[:, :T_prompt] = torch.tensor(prompt_ids, dtype=torch.long, device=device)
    cur_len = T_prompt

    generated: list[list[int]] = [[] for _ in range(k)]
    active = [True] * k

    for _ in range(max_new_tokens):
        if not any(active):
            break

        # Clip to model's positional embedding capacity
        start = max(0, cur_len - max_len)
        x = buf[:, start:cur_len]
        logits = model(x)           # (k, T, V)
        logits = logits[:, -1, :]   # (k, V) — last position only

        if temperature == 0.0 or temperature < 1e-6:
            next_ids = logits.argmax(dim=-1)   # (k,)
        else:
            logits = logits / temperature
            if top_k > 0:
                vals, _ = torch.topk(logits, top_k, dim=-1)
                logits[logits < vals[:, -1:]] = float("-inf")
            probs = F.softmax(logits, dim=-1)
            next_ids = torch.multinomial(probs, 1).squeeze(-1)  # (k,)

        buf[:, cur_len] = next_ids
        cur_len += 1

        for i in range(k):
            if active[i]:
                tok_id = int(next_ids[i].item())
                generated[i].append(tok_id)
                if tok_id == EOS_ID:
                    active[i] = False

    return generated


def ids_to_text(ids: list[int], tok: Tokenizer) -> str:
    return " ".join(tok.id_to_token(i) or "<unk>" for i in ids
                    if i not in (PAD_ID, BOS_ID, EOS_ID))


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--checkpoint",   default="sprint0/model/checkpoint.pt")
    p.add_argument("--tokenizer",    default="sprint0/corpus/tokenizer.json")
    p.add_argument("--sequences",    default="sprint0/corpus/sequences.json")
    p.add_argument("--prompt",       default=None,
                   help="Prompt string, e.g. 'DEF fId'")
    p.add_argument("--all",          action="store_true",
                   help="Generate for every DEF prefix in corpus")
    p.add_argument("--temperature",  type=float, default=0.0,
                   help="0=greedy, >0=sampling")
    p.add_argument("--top-k",        type=int,   default=0)
    p.add_argument("--n",            type=int,   default=1,
                   help="Number of samples per prompt")
    p.add_argument("--max-tokens",   type=int,   default=200)
    args = p.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Load checkpoint
    ckpt = torch.load(args.checkpoint, map_location=device, weights_only=False)
    cfg = ckpt["args"]
    tok = Tokenizer.from_file(args.tokenizer)

    model = CausalTransformer(
        vocab_size=ckpt["vocab_size"],
        d_model=cfg["d_model"],
        nhead=cfg["nhead"],
        num_layers=cfg["num_layers"],
        max_len=cfg["max_len"],
        dropout=0.0,   # no dropout at inference
    ).to(device)
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    loss_val = ckpt.get("val_loss", ckpt.get("loss", float("nan")))
    print(f"[generate] loaded checkpoint (epoch={ckpt['epoch']}, loss={loss_val:.4f})")

    # Build list of prompts
    prompts: list[str] = []
    if args.all:
        seqs = json.loads(Path(args.sequences).read_text())
        prompts = [f"DEF {s['name']}" for s in seqs]
    elif args.prompt:
        prompts = [args.prompt]
    else:
        prompts = ["DEF fId", "DEF fMap", "DEF fFoldr"]

    for prompt_str in prompts:
        # Encode prompt: BOS + prompt tokens (no EOS yet)
        enc = tok.encode(prompt_str)
        # enc.ids has BOS at start, EOS at end — strip EOS for prompting
        prompt_ids = enc.ids[:-1]  # [BOS, token...]

        print(f"\n--- {prompt_str} ---")
        for i in range(args.n):
            gen_ids = generate(
                model,
                prompt_ids,
                max_new_tokens=args.max_tokens,
                temperature=args.temperature,
                top_k=args.top_k,
            )
            full = prompt_ids[1:] + gen_ids  # strip BOS for display
            text = ids_to_text(full, tok)
            stopped = gen_ids[-1] == EOS_ID if gen_ids else False
            print(f"  [{i}] {text}")
            if not stopped:
                print(f"       (truncated at {args.max_tokens} tokens)")


if __name__ == "__main__":
    main()
