"""Sprint 29 Phase 1.6 — stratified train/val split for the final S29 corpus.

Produces two output files from one or more input JSONLs:
  lean_mcts/train/diverse_train_v2.jsonl  (80% by default)
  lean_mcts/train/diverse_val_v2.jsonl    (20%)

Stratification is by the per-pair "kind" field (SIMPLE / EQCOMP / WHERE) so
the validation set has representative coverage of every pattern the model
needs to handle.

Usage:
    python -m lean_mcts.synth.split_train_val \\
        --input lean_mcts/train/library_pairs.jsonl \\
        --input lean_mcts/train/diverse_corpus_v3.jsonl \\
        --train-out lean_mcts/train/diverse_train_v2.jsonl \\
        --val-out lean_mcts/train/diverse_val_v2.jsonl \\
        --val-frac 0.20 \\
        --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path


def load_pairs(path: Path) -> list[dict]:
    out: list[dict] = []
    with open(path) as f:
        for line in f:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def stratified_split(
    pairs: list[dict],
    val_frac: float,
    stratify_key: str,
    seed: int,
) -> tuple[list[dict], list[dict]]:
    """Return (train, val) lists, stratified by `pairs[i][stratify_key]`."""
    rng = random.Random(seed)
    by_group: dict[str, list[dict]] = defaultdict(list)
    for p in pairs:
        key = p.get(stratify_key, "?")
        by_group[key].append(p)

    train: list[dict] = []
    val: list[dict] = []
    for group_key, group in sorted(by_group.items()):
        group = list(group)
        rng.shuffle(group)
        n_val = max(1, int(round(len(group) * val_frac))) if group else 0
        n_val = min(n_val, len(group) - 1 if len(group) > 1 else len(group))
        val.extend(group[:n_val])
        train.extend(group[n_val:])

    rng.shuffle(train)
    rng.shuffle(val)
    return train, val


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", action="append", required=True,
                        help="Input JSONL (pass multiple times to concatenate)")
    parser.add_argument("--train-out", type=str, required=True)
    parser.add_argument("--val-out", type=str, required=True)
    parser.add_argument("--val-frac", type=float, default=0.20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--stratify", type=str, default="kind",
                        help="Key to stratify by (default: kind)")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent.parent
    all_pairs: list[dict] = []
    seen_keys: set[tuple[str, str]] = set()
    for tp in args.input:
        path = project_root / tp
        if not path.exists():
            parser.error(f"input not found: {path}")
        for p in load_pairs(path):
            key = (p.get("state", ""), p.get("tactic", ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_pairs.append(p)

    print(f"Loaded {len(all_pairs)} unique pairs from {len(args.input)} inputs")

    train, val = stratified_split(
        all_pairs,
        val_frac=args.val_frac,
        stratify_key=args.stratify,
        seed=args.seed,
    )

    print(f"Split: {len(train)} train / {len(val)} val  "
          f"(val_frac={args.val_frac}, seed={args.seed}, stratified by {args.stratify})")

    # Report per-group distribution in the val split
    from collections import Counter
    train_counts = Counter(p.get(args.stratify, "?") for p in train)
    val_counts = Counter(p.get(args.stratify, "?") for p in val)
    print(f"\nDistribution by {args.stratify}:")
    for k in sorted(set(list(train_counts.keys()) + list(val_counts.keys()))):
        t = train_counts.get(k, 0)
        v = val_counts.get(k, 0)
        print(f"  {k:12s}: train={t:4d}  val={v:3d}")

    train_path = project_root / args.train_out
    val_path = project_root / args.val_out
    train_path.parent.mkdir(parents=True, exist_ok=True)
    val_path.parent.mkdir(parents=True, exist_ok=True)

    with open(train_path, "w") as f:
        for p in train:
            f.write(json.dumps(p) + "\n")
    with open(val_path, "w") as f:
        for p in val:
            f.write(json.dumps(p) + "\n")

    print(f"\nWrote {len(train)} train → {train_path}")
    print(f"Wrote {len(val)} val   → {val_path}")


if __name__ == "__main__":
    main()
