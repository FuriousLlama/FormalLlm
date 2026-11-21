"""Build training curriculum for Sprint 23.

Merges compositional data (from generate_compositional_data.py) with
existing training data, applies uniform mixing, and outputs final
train/val splits.

Uses uniform sampling (fallback from the planned curriculum weighting,
per Sprint 23 plan: "data quality matters more than curriculum ordering").

Usage:
    python lean_mcts/train/build_curriculum.py
    python lean_mcts/train/build_curriculum.py --compositional-only
    python lean_mcts/train/build_curriculum.py --verbose
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    """Load JSONL file."""
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def dedup(records: list[dict]) -> list[dict]:
    """Deduplicate by (state, tactic) key."""
    seen: set[tuple[str, str]] = set()
    unique = []
    for r in records:
        key = (r["state"], r["tactic"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def write_split(
    records: list[dict],
    train_path: Path,
    val_path: Path,
    split_ratio: float = 0.8,
    seed: int = 42,
) -> tuple[int, int]:
    """Shuffle, split, and write train/val JSONL."""
    rng = random.Random(seed)
    rng.shuffle(records)

    split_idx = int(len(records) * split_ratio)
    train = records[:split_idx]
    val = records[split_idx:]

    train_path.parent.mkdir(parents=True, exist_ok=True)
    for path, data in [(train_path, train), (val_path, val)]:
        with open(path, "w") as f:
            for r in data:
                # Only write state and tactic (training format)
                out = {"state": r["state"], "tactic": r["tactic"]}
                f.write(json.dumps(out) + "\n")

    return len(train), len(val)


def analyze(records: list[dict], label: str = "Dataset"):
    """Print analysis of the dataset."""
    print(f"\n=== {label} ({len(records)} pairs) ===")

    # Return type distribution
    rt_counts = Counter()
    for r in records:
        if "⊢" in r["state"]:
            ret = r["state"].split("⊢")[-1].strip()
            rt_counts[ret] += 1

    print("Return types:")
    for ret, n in rt_counts.most_common(10):
        print(f"  {ret}: {n} ({100*n/len(records):.1f}%)")

    # Tactic type distribution
    constructor_count = sum(1 for r in records if r["tactic"] == "constructor")
    exact_count = sum(1 for r in records if r["tactic"].startswith("exact "))
    other_count = len(records) - constructor_count - exact_count
    print(f"Tactic types: exact={exact_count}, constructor={constructor_count}, other={other_count}")

    # Depth-3 nested content
    nested = sum(1 for r in records if "case fst.fst" in r["state"] or "case fst.snd" in r["state"])
    case_any = sum(1 for r in records if "case " in r["state"])
    print(f"Case-prefixed states: {case_any} (nested depth-3: {nested})")


def main():
    parser = argparse.ArgumentParser(description="Build Sprint 23 training curriculum")
    parser.add_argument(
        "--existing-train", type=str,
        default="lean_mcts/train/goal_cond_train.jsonl",
        help="Existing training data"
    )
    parser.add_argument(
        "--existing-val", type=str,
        default="lean_mcts/train/goal_cond_val.jsonl",
        help="Existing validation data"
    )
    parser.add_argument(
        "--compositional", type=str,
        default="lean_mcts/train/compositional_pairs.jsonl",
        help="Compositional training data"
    )
    parser.add_argument(
        "--output-train", type=str,
        default="lean_mcts/train/goal_cond_s23_train.jsonl",
        help="Output training JSONL"
    )
    parser.add_argument(
        "--output-val", type=str,
        default="lean_mcts/train/goal_cond_s23_val.jsonl",
        help="Output validation JSONL"
    )
    parser.add_argument(
        "--compositional-only", action="store_true",
        help="Use only compositional data (no existing)"
    )
    parser.add_argument("--split", type=float, default=0.8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent.parent
    comp_path = base.parent / args.compositional if not Path(args.compositional).is_absolute() else Path(args.compositional)
    out_train = base.parent / args.output_train if not Path(args.output_train).is_absolute() else Path(args.output_train)
    out_val = base.parent / args.output_val if not Path(args.output_val).is_absolute() else Path(args.output_val)

    all_records = []

    # Load compositional data
    print(f"Loading compositional data from {args.compositional}...")
    comp_data = load_jsonl(comp_path)
    print(f"  {len(comp_data)} compositional pairs")
    all_records.extend(comp_data)

    if args.verbose:
        analyze(comp_data, "Compositional data")

    # Load existing data
    if not args.compositional_only:
        for path_str in [args.existing_train, args.existing_val]:
            p = base.parent / path_str if not Path(path_str).is_absolute() else Path(path_str)
            if p.exists():
                data = load_jsonl(p)
                print(f"Loaded {len(data)} pairs from {path_str}")
                all_records.extend(data)

    # Dedup
    before = len(all_records)
    all_records = dedup(all_records)
    print(f"\nAfter dedup: {len(all_records)} (removed {before - len(all_records)} duplicates)")

    if args.verbose:
        analyze(all_records, "Merged dataset")

    # Write
    n_train, n_val = write_split(
        all_records, out_train, out_val,
        split_ratio=args.split, seed=args.seed,
    )
    print(f"\nWrote {n_train} train + {n_val} val = {n_train + n_val} total")
    print(f"  Train: {out_train}")
    print(f"  Val: {out_val}")


if __name__ == "__main__":
    main()
