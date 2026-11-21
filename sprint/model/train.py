"""
train.py — Sprint 1 causal transformer LM on normalized AST token sequences.

Architecture: GPT-style (TransformerEncoder + causal mask)
  d_model=256, nhead=8, num_layers=4, ff=1024  →  ~3.4M params

Sprint 1 additions vs sprint 0:
  - 80/20 train/val split (split by function name so augmented variants
    of the same base function stay in the same fold)
  - Early stopping on validation loss (patience=200 epochs)
  - weight_decay=0.05
  - Reports val loss alongside train loss

Usage:
  python sprint/model/train.py
  python sprint/model/train.py --epochs 3000 --d-model 256 --num-layers 4
"""

import json
import math
import argparse
import sys
import time
import random
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader, Subset
from tokenizers import Tokenizer

# Make sibling corpus module importable (for normalize_type)
_SPRINT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SPRINT_ROOT / "corpus"))

# ── constants ────────────────────────────────────────────────────────────────

PAD_ID = 0
BOS_ID = 2
EOS_ID = 3

# ── dataset ──────────────────────────────────────────────────────────────────

class ASTDataset(Dataset):
    def __init__(self, sequences_path: str, tokenizer_path: str, max_len: int = 256,
                 few_shot_prob: float = 0.5, few_shot_k: int = 2,
                 grouping: str = "type"):
        """
        grouping:
          "type"   — context examples drawn from same type group (Sprint 5 default)
          "random" — context examples drawn randomly (Sprint 4 behaviour)
          "none"   — single-sequence training, no context prepended
        """
        tok = Tokenizer.from_file(tokenizer_path)
        raw = json.loads(Path(sequences_path).read_text())

        self.ids: list[list[int]] = []
        self.names: list[str] = []
        self.packages: list[str] = []
        for seq in raw:
            enc = tok.encode(" ".join(seq["tokens"]))
            ids = enc.ids[:max_len]
            self.ids.append(ids)
            self.names.append(seq["name"])
            self.packages.append(seq.get("package", "handwritten"))

        self.max_len = max_len
        self.vocab_size = tok.get_vocab_size()
        self.few_shot_prob = few_shot_prob
        self.few_shot_k = few_shot_k
        self.grouping = grouping

        # Build type-similarity index (used when grouping == "type")
        if grouping == "type":
            from normalize import normalize_type
            import re

            # Canonical type string for each sequence (joined tokens, e.g. "a -> a")
            type_keys: list[str] = []
            for seq in raw:
                toks = normalize_type(seq.get("type", ""))
                type_keys.append(" ".join(toks))
            self.type_keys = type_keys

            # exact_groups[key] = sorted list of all indices with that exact type
            exact_groups: dict[str, list[int]] = {}
            for i, key in enumerate(type_keys):
                exact_groups.setdefault(key, []).append(i)
            self.exact_groups = exact_groups

            # arity_groups[n] = sorted list of all indices with n arrows in type
            arity_groups: dict[int, list[int]] = {}
            for i, key in enumerate(type_keys):
                arity = key.count("->")
                arity_groups.setdefault(arity, []).append(i)
            self.arity_groups = arity_groups

            # Sprint 9: Pre-compute GlobalRef sets for pattern-aligned context
            ref_pattern = re.compile(r'^f[A-Z]')
            seq_refs: list[set[str]] = []
            for i, seq in enumerate(raw):
                tokens = seq["tokens"]
                name = seq["name"]
                if "BODY" in tokens:
                    body_start = tokens.index("BODY") + 1
                    body_tokens = tokens[body_start:]
                else:
                    body_tokens = tokens
                refs = {t for t in body_tokens if ref_pattern.match(t) and t != name}
                seq_refs.append(refs)
            self.seq_refs = seq_refs
            n_comp = sum(1 for refs in seq_refs if len(refs) >= 2)
            print(f"[dataset] pattern index: {n_comp} compositions (>=2 refs) "
                  f"out of {len(seq_refs)} sequences")

    def __len__(self) -> int:
        return len(self.ids)

    def _type_peers(self, idx: int, n: int) -> list[int]:
        """Return up to n context indices for idx using type-similarity grouping.

        Sprint 9: When idx is a composition (>=2 refs), prefer peers with high
        building-block overlap. This teaches the model to transfer composition
        patterns from context to output. Primitives use random selection.

        Priority: exact type match → same arity → nothing.
        """
        key = self.type_keys[idx]

        # Exact match peers (excluding self)
        peers = [i for i in self.exact_groups.get(key, []) if i != idx]

        # Pad with same-arity peers if needed
        if len(peers) < n:
            arity = key.count("->")
            extra = [i for i in self.arity_groups.get(arity, [])
                     if i != idx and i not in set(peers)]
            peers = peers + extra

        if not peers:
            return []
        if len(peers) <= n:
            return peers

        # Sprint 9: pattern-aligned selection for compositions
        my_refs = self.seq_refs[idx]
        if len(my_refs) >= 2:
            # Score peers by building-block overlap
            scored = [(p, len(self.seq_refs[p] & my_refs)) for p in peers]
            high = [p for p, s in scored if s >= 2]
            if len(high) >= n:
                return random.sample(high, n)
            if high:
                # Take all high-overlap, fill rest randomly from remaining
                rest = [p for p, s in scored if s < 2]
                n_fill = n - len(high)
                fill = random.sample(rest, min(n_fill, len(rest))) if rest else []
                return high + fill

        return random.sample(peers, n)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        target_ids = self.ids[idx]   # [BOS, DEF, TY, ..., BODY, ..., EOF, EOS]

        ids: list[int]
        if (self.grouping != "none"
                and self.few_shot_prob > 0
                and random.random() < self.few_shot_prob):
            n_ctx = random.randint(1, self.few_shot_k)

            if self.grouping == "type":
                ctx_indices = self._type_peers(idx, n_ctx)
            else:  # "random" — Sprint 4 behaviour
                ctx_indices = random.sample(
                    [i for i in range(len(self.ids)) if i != idx], n_ctx
                )

            if ctx_indices:
                # BOS once at front; context seqs stripped of BOS+EOS; target stripped of BOS only
                combined: list[int] = [BOS_ID]
                for ci in ctx_indices:
                    combined.extend(self.ids[ci][1:-1])   # strip BOS and EOS
                combined.extend(target_ids[1:])            # strip BOS, keep EOS
                ids = combined[:self.max_len]
            else:
                ids = target_ids
        else:
            ids = target_ids

        T = len(ids)
        padded = ids + [PAD_ID] * (self.max_len - T)
        t = torch.tensor(padded, dtype=torch.long)
        x = t[:-1]
        y = t[1:]
        mask = (y != PAD_ID).float()
        return x, y, mask


def train_val_split(dataset: ASTDataset, val_frac: float = 0.2, seed: int = 42
                    ) -> tuple[list[int], list[int]]:
    """
    Split indices 80/20 by package (not function name).

    All functions from the same Hackage package go to the same fold,
    preventing leakage from the same codebase. The 'handwritten' package
    always goes to training (these are seed corpus functions used as
    task dependencies).
    """
    rng = random.Random(seed)

    # Group indices by package
    pkg_groups: dict[str, list[int]] = {}
    for i, pkg in enumerate(dataset.packages):
        pkg_groups.setdefault(pkg, []).append(i)

    # handwritten always trains (needed as task deps)
    train_idx = list(pkg_groups.pop("handwritten", []))

    packages = sorted(pkg_groups.keys())
    rng.shuffle(packages)

    # Pick val packages by count of sequences (aim for val_frac of non-handwritten)
    total_non_hw = sum(len(idxs) for idxs in pkg_groups.values())
    target_val = round(total_non_hw * val_frac)

    val_idx: list[int] = []
    val_count = 0
    val_pkgs: set[str] = set()
    for pkg in packages:
        if val_count >= target_val:
            break
        val_pkgs.add(pkg)
        val_idx.extend(pkg_groups[pkg])
        val_count += len(pkg_groups[pkg])

    for pkg in packages:
        if pkg not in val_pkgs:
            train_idx.extend(pkg_groups[pkg])

    return train_idx, val_idx


# ── model ─────────────────────────────────────────────────────────────────────

class CausalTransformer(nn.Module):
    def __init__(
        self,
        vocab_size: int,
        d_model: int = 256,
        nhead: int = 8,
        num_layers: int = 4,
        max_len: int = 256,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.d_model = d_model
        self.tok_emb = nn.Embedding(vocab_size, d_model, padding_idx=PAD_ID)
        self.pos_emb = nn.Embedding(max_len, d_model)
        self.emb_drop = nn.Dropout(dropout)

        layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=4 * d_model,
            dropout=dropout,
            batch_first=True,
            norm_first=True,      # pre-norm (more stable)
        )
        self.transformer = nn.TransformerEncoder(layer, num_layers=num_layers,
                                                  enable_nested_tensor=False)
        self.ln_f = nn.LayerNorm(d_model)
        self.head = nn.Linear(d_model, vocab_size, bias=False)
        self.head.weight = self.tok_emb.weight   # weight tying
        self._init_weights()

    def _init_weights(self):
        for p in self.parameters():
            if p.dim() > 1:
                nn.init.xavier_uniform_(p)

    def forward(self, x: torch.Tensor, pad_mask: torch.Tensor | None = None) -> torch.Tensor:
        B, T = x.shape
        pos = torch.arange(T, device=x.device).unsqueeze(0)
        h = self.emb_drop(self.tok_emb(x) + self.pos_emb(pos))
        causal = nn.Transformer.generate_square_subsequent_mask(T, device=x.device)
        pad_mask_f = (
            pad_mask.float().masked_fill(pad_mask, float("-inf"))
            if pad_mask is not None else None
        )
        h = self.transformer(h, mask=causal, src_key_padding_mask=pad_mask_f, is_causal=True)
        h = self.ln_f(h)
        return self.head(h)

    def num_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


# ── eval helper ───────────────────────────────────────────────────────────────

@torch.no_grad()
def evaluate(model: CausalTransformer, loader: DataLoader,
             criterion: nn.CrossEntropyLoss, device: torch.device) -> float:
    model.eval()
    total_loss = 0.0
    total_tokens = 0
    for x, y, mask in loader:
        x, y, mask = x.to(device), y.to(device), mask.to(device)
        pad_mask = (x == PAD_ID)
        logits = model(x, pad_mask=pad_mask)
        B, T, V = logits.shape
        loss_per_token = criterion(logits.reshape(B * T, V), y.reshape(B * T))
        n_tokens = mask.sum()
        total_loss += (loss_per_token.reshape(B, T) * mask).sum().item()
        total_tokens += n_tokens.item()
    model.train()
    return total_loss / total_tokens if total_tokens > 0 else float("inf")


# ── training loop ─────────────────────────────────────────────────────────────

def train(args):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[train] device: {device}")

    dataset = ASTDataset(args.sequences, args.tokenizer, max_len=args.max_len,
                         few_shot_prob=args.few_shot_prob, few_shot_k=args.few_shot_k,
                         grouping=args.grouping)
    train_idx, val_idx = train_val_split(dataset, val_frac=0.2)

    train_set = Subset(dataset, train_idx)
    val_set   = Subset(dataset, val_idx)

    train_loader = DataLoader(train_set, batch_size=args.batch_size, shuffle=True,  drop_last=False)
    val_loader   = DataLoader(val_set,   batch_size=args.batch_size, shuffle=False, drop_last=False)

    n_pkgs = len(set(dataset.packages))
    val_pkgs = set(dataset.packages[i] for i in val_idx)
    print(f"[train] {len(dataset)} total sequences → "
          f"train={len(train_set)} val={len(val_set)}, vocab={dataset.vocab_size}")
    print(f"[train] split by package: {n_pkgs} packages total, "
          f"{len(val_pkgs)} in val ({', '.join(sorted(val_pkgs))})")
    print(f"[train] context grouping: {args.grouping}, "
          f"few_shot_prob={args.few_shot_prob}, few_shot_k={args.few_shot_k}")

    model = CausalTransformer(
        vocab_size=dataset.vocab_size,
        d_model=args.d_model,
        nhead=args.nhead,
        num_layers=args.num_layers,
        max_len=args.max_len,
        dropout=args.dropout,
    ).to(device)
    print(f"[train] params: {model.num_params():,}")
    print(f"[train] random baseline loss: {math.log(dataset.vocab_size):.3f} nats")

    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.lr, weight_decay=args.weight_decay
    )

    total_steps = args.epochs * len(train_loader)
    warmup_steps = min(200, total_steps // 10)

    def lr_lambda(step):
        if step < warmup_steps:
            return step / max(1, warmup_steps)
        progress = (step - warmup_steps) / max(1, total_steps - warmup_steps)
        return 0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * progress))

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)
    criterion = nn.CrossEntropyLoss(
        ignore_index=PAD_ID, reduction="none",
        label_smoothing=args.label_smoothing,
    )

    best_val_loss = float("inf")
    best_train_loss = float("inf")
    patience_count = 0
    step = 0
    t0 = time.time()

    for epoch in range(1, args.epochs + 1):
        model.train()
        epoch_loss = 0.0
        epoch_tokens = 0

        for x, y, mask in train_loader:
            x, y, mask = x.to(device), y.to(device), mask.to(device)
            pad_mask = (x == PAD_ID)
            logits = model(x, pad_mask=pad_mask)
            B, T, V = logits.shape
            loss_per_token = criterion(logits.reshape(B * T, V), y.reshape(B * T))
            n_tokens = mask.sum()
            loss = (loss_per_token.reshape(B, T) * mask).sum() / n_tokens

            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()

            epoch_loss += loss.item() * n_tokens.item()
            epoch_tokens += n_tokens.item()
            step += 1

        avg_train_loss = epoch_loss / epoch_tokens
        if avg_train_loss < best_train_loss:
            best_train_loss = avg_train_loss

        # Validate every log_every epochs (not every epoch — saves time)
        if epoch % args.log_every == 0 or epoch == args.epochs:
            val_loss = evaluate(model, val_loader, criterion, device)

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_count = 0
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                torch.save(
                    {
                        "epoch": epoch,
                        "model_state": model.state_dict(),
                        "args": vars(args),
                        "vocab_size": dataset.vocab_size,
                        "train_loss": avg_train_loss,
                        "val_loss": val_loss,
                    },
                    args.out,
                )
            else:
                patience_count += 1

            elapsed = time.time() - t0
            lr = scheduler.get_last_lr()[0]
            ratio = val_loss / avg_train_loss if avg_train_loss > 0 else float("inf")
            print(
                f"epoch {epoch:5d}/{args.epochs}  "
                f"train={avg_train_loss:.4f}  "
                f"val={val_loss:.4f}  "
                f"ratio={ratio:.2f}  "
                f"best_val={best_val_loss:.4f}  "
                f"lr={lr:.2e}  "
                f"elapsed={elapsed:.0f}s"
            )

            # Early stopping
            if patience_count >= args.patience:
                print(f"[train] early stopping at epoch {epoch} "
                      f"(val loss didn't improve for {args.patience} checks)")
                break

    print(f"\n[train] done.")
    print(f"[train] best train loss: {best_train_loss:.4f}")
    print(f"[train] best val loss:   {best_val_loss:.4f}")
    if best_train_loss > 0:
        print(f"[train] val/train ratio: {best_val_loss/best_train_loss:.2f}x")
    print(f"[train] checkpoint → {args.out}")


# ── CLI ───────────────────────────────────────────────────────────────────────

MODEL_PRESETS = {
    # 2L / d=128 / nhead=4 / ff=512  →  ~450K params  (right-sized, Sprint 4 target)
    "small": dict(d_model=128, nhead=4, num_layers=2),
    # 4L / d=256 / nhead=8 / ff=1024 →  ~3.3M params  (previous sprint default)
    "big":   dict(d_model=256, nhead=8, num_layers=4),
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sequences",     default="sprint/corpus/sequences_aug.json")
    p.add_argument("--tokenizer",     default="sprint/corpus/tokenizer.json")
    p.add_argument("--out",           default="sprint/model/checkpoint.pt")
    p.add_argument("--model-size",    choices=list(MODEL_PRESETS), default="small",
                   help="Preset: small=2L/d128/~450K params, big=4L/d256/~3.3M params")
    p.add_argument("--epochs",        type=int,   default=3000)
    p.add_argument("--batch-size",    type=int,   default=32)
    p.add_argument("--d-model",       type=int,   default=None,
                   help="Override preset d_model")
    p.add_argument("--nhead",         type=int,   default=None,
                   help="Override preset nhead")
    p.add_argument("--num-layers",    type=int,   default=None,
                   help="Override preset num_layers")
    p.add_argument("--max-len",       type=int,   default=256)
    p.add_argument("--few-shot-prob", type=float, default=0.5,
                   help="Fraction of training examples that use multi-function context (default: 0.5)")
    p.add_argument("--few-shot-k",    type=int,   default=2,
                   help="Max context sequences prepended when few-shot is active (default: 2)")
    p.add_argument("--grouping",      choices=["type", "random", "none"], default="type",
                   help="Context grouping strategy: type=type-grouped (Sprint 5), random=Sprint 4, none=single-sequence")
    p.add_argument("--lr",            type=float, default=1e-3)
    p.add_argument("--dropout",       type=float, default=0.1)
    p.add_argument("--weight-decay",  type=float, default=0.05)
    p.add_argument("--log-every",     type=int,   default=50)
    p.add_argument("--patience",      type=int,   default=20,
                   help="Early stopping: # of val checks without improvement")
    p.add_argument("--label-smoothing", type=float, default=0.0)
    args = p.parse_args()

    # Apply preset; explicit --d-model / --nhead / --num-layers override the preset
    preset = MODEL_PRESETS[args.model_size]
    if args.d_model    is None: args.d_model    = preset["d_model"]
    if args.nhead      is None: args.nhead      = preset["nhead"]
    if args.num_layers is None: args.num_layers = preset["num_layers"]

    train(args)


if __name__ == "__main__":
    main()
