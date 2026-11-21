"""
Extract (tactic_state, next_tactic) pairs from Mathlib for policy network training.

Uses LeanDojo's tracing pipeline to instrument Mathlib and extract tactic data.

Usage:
    python extract_data.py --output train/mathlib_tactics.jsonl
    python extract_data.py --output train/mathlib_tactics.jsonl --limit 1000  # test run
    python extract_data.py --split --input train/mathlib_tactics.jsonl        # train/val split

Output format (JSONL):
    {"state": "⊢ h + 1 > 0", "tactic": "omega", "theorem": "Nat.succ_pos",
     "file": "Mathlib/Data/Nat/Basic.lean", "category": "other"}

Expected: ~100K-260K entries after filtering.
First run traces Mathlib (slow, 30-60+ minutes). Subsequent runs use LeanDojo cache.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import resource
import sys
from collections import Counter, defaultdict
from pathlib import Path

# LeanDojo's AST parser recurses deeply on some Mathlib files.
# Increase both Python recursion limit and OS stack size to avoid
# SIGSEGV from C-level stack overflow in Ray workers.
sys.setrecursionlimit(50000)
try:
    # Increase stack to 64MB (default 8MB is too small for deep ASTs)
    resource.setrlimit(resource.RLIMIT_STACK, (64 * 1024 * 1024, resource.RLIM_INFINITY))
except (ValueError, resource.error):
    pass  # May require root on some systems

try:
    from lean_dojo import LeanGitRepo, trace
except ImportError:
    print("ERROR: lean-dojo not installed. Run: pip install lean-dojo")
    raise


# Tactics to filter out (trivial/placeholder)
SKIP_TACTICS = frozenset({"sorry", "skip", "done", "stop", "admit"})

# Max state length in characters (outlier filter)
MAX_STATE_LENGTH = 2048

# Category patterns for tactic classification
CATEGORY_PATTERNS = [
    ("exact", re.compile(r"^\s*exact\b")),
    ("apply", re.compile(r"^\s*apply\b")),
    ("intro", re.compile(r"^\s*intro[s]?\b")),
    ("rewrite", re.compile(r"^\s*(rw|rewrite)\b")),
    ("simp", re.compile(r"^\s*(simp|simpa)\b")),
    ("omega", re.compile(r"^\s*omega\b")),
    ("norm_num", re.compile(r"^\s*norm_num\b")),
    ("ring", re.compile(r"^\s*ring\b")),
    ("linarith", re.compile(r"^\s*linarith\b")),
    ("constructor", re.compile(r"^\s*constructor\b")),
    ("cases", re.compile(r"^\s*(cases|rcases|obtain)\b")),
    ("induction", re.compile(r"^\s*induction\b")),
    ("have", re.compile(r"^\s*(have|let)\b")),
    ("ext", re.compile(r"^\s*(ext|funext)\b")),
    ("refine", re.compile(r"^\s*refine\b")),
    ("decide", re.compile(r"^\s*decide\b")),
    ("trivial", re.compile(r"^\s*trivial\b")),
    ("assumption", re.compile(r"^\s*assumption\b")),
    ("contradiction", re.compile(r"^\s*contradiction\b")),
    ("congr", re.compile(r"^\s*congr\b")),
    ("calc", re.compile(r"^\s*calc\b")),
]


def categorize_tactic(tactic: str) -> str:
    """Classify a tactic into a category based on its leading keyword."""
    for name, pattern in CATEGORY_PATTERNS:
        if pattern.match(tactic):
            return name
    return "other"


def should_skip(state: str, tactic: str) -> str | None:
    """Return reason to skip, or None if entry should be kept."""
    tactic_stripped = tactic.strip()
    if tactic_stripped.lower() in SKIP_TACTICS:
        return "trivial_tactic"
    if len(state) > MAX_STATE_LENGTH:
        return "state_too_long"
    if not state.strip():
        return "empty_state"
    if not tactic_stripped:
        return "empty_tactic"
    return None


def extract_tactics(repo_url: str, commit: str, output_path: str, limit: int = 0):
    """
    Trace a Lean repo and extract (state, tactic) pairs with filtering.

    Args:
        repo_url: GitHub URL of the Lean repo (e.g., Mathlib4)
        commit: Git commit hash to trace
        output_path: Path for output JSONL file
        limit: Max entries to extract (0 = unlimited)
    """
    print(f"Tracing {repo_url} @ {commit[:12]}...")
    print("(First trace takes 30-60+ minutes. Results are cached for subsequent runs.)")

    repo = LeanGitRepo(repo_url, commit)
    traced_repo = trace(repo)

    kept = 0
    skipped_reasons: Counter = Counter()
    category_counts: Counter = Counter()
    files_seen: set[str] = set()

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w") as f:
        for traced_file in traced_repo.get_traced_files():
            file_path = str(traced_file.path)
            for traced_theorem in traced_file.get_traced_theorems():
                for traced_tactic in traced_theorem.get_traced_tactics():
                    state = traced_tactic.state_before
                    tactic = traced_tactic.tactic

                    skip_reason = should_skip(state, tactic)
                    if skip_reason:
                        skipped_reasons[skip_reason] += 1
                        continue

                    category = categorize_tactic(tactic)
                    category_counts[category] += 1
                    files_seen.add(file_path)

                    entry = {
                        "state": state,
                        "tactic": tactic,
                        "theorem": traced_theorem.theorem.full_name,
                        "file": file_path,
                        "category": category,
                    }
                    f.write(json.dumps(entry) + "\n")
                    kept += 1

                    if kept % 10000 == 0:
                        print(f"  Extracted {kept} tactic pairs...")

                    if limit and kept >= limit:
                        break
                else:
                    continue
                break
            else:
                continue
            break

    total_processed = kept + sum(skipped_reasons.values())
    print(f"\nExtraction complete:")
    print(f"  Kept:    {kept:,}")
    print(f"  Skipped: {sum(skipped_reasons.values()):,}")
    for reason, count in skipped_reasons.most_common():
        print(f"    {reason}: {count:,}")
    print(f"  Files:   {len(files_seen):,}")
    print(f"  Output:  {output_path}")

    # Save stats alongside the data
    stats_path = out.parent / "data_stats.json"
    stats = {
        "total_processed": total_processed,
        "kept": kept,
        "skipped": dict(skipped_reasons),
        "categories": dict(category_counts.most_common()),
        "num_files": len(files_seen),
        "commit": commit,
        "repo_url": repo_url,
    }
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"  Stats:   {stats_path}")

    return kept


def split_data(input_path: str, train_ratio: float = 0.8):
    """
    Split extracted data into train/val by source file (no same-theorem leakage).

    Args:
        input_path: Path to mathlib_tactics.jsonl
        train_ratio: Fraction of files for training (default 0.8)
    """
    inp = Path(input_path)
    if not inp.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Group entries by source file
    by_file: dict[str, list[str]] = defaultdict(list)
    total = 0
    for line in open(inp):
        entry = json.loads(line)
        by_file[entry["file"]].append(line)
        total += 1

    # Sort files deterministically, split by file count
    files = sorted(by_file.keys())
    split_idx = int(len(files) * train_ratio)
    train_files = set(files[:split_idx])
    val_files = set(files[split_idx:])

    train_path = inp.parent / "mathlib_train.jsonl"
    val_path = inp.parent / "mathlib_val.jsonl"

    train_count = 0
    val_count = 0

    with open(train_path, "w") as ft, open(val_path, "w") as fv:
        for file_path, lines in by_file.items():
            if file_path in train_files:
                for line in lines:
                    ft.write(line)
                    train_count += 1
            else:
                for line in lines:
                    fv.write(line)
                    val_count += 1

    # Verify zero overlap
    assert train_files.isdisjoint(val_files), "File overlap between train and val!"

    print(f"\nSplit complete:")
    print(f"  Total entries: {total:,}")
    print(f"  Train: {train_count:,} ({len(train_files)} files)")
    print(f"  Val:   {val_count:,} ({len(val_files)} files)")
    print(f"  Train path: {train_path}")
    print(f"  Val path:   {val_path}")

    # Spot-check: category distribution in both splits
    train_cats: Counter = Counter()
    val_cats: Counter = Counter()
    for line in open(train_path):
        train_cats[json.loads(line)["category"]] += 1
    for line in open(val_path):
        val_cats[json.loads(line)["category"]] += 1

    print(f"\n  Category distribution (train / val):")
    all_cats = sorted(set(train_cats) | set(val_cats), key=lambda c: -(train_cats[c] + val_cats[c]))
    for cat in all_cats[:10]:
        t = train_cats[cat]
        v = val_cats[cat]
        print(f"    {cat:15s}: {t:>7,} / {v:>6,}  ({t/(t+v)*100:.0f}%/{v/(t+v)*100:.0f}%)")

    return train_count, val_count


def main():
    parser = argparse.ArgumentParser(description="Extract Mathlib tactic data")
    parser.add_argument("--output", type=str, default="train/mathlib_tactics.jsonl")
    parser.add_argument(
        "--repo",
        type=str,
        default="https://github.com/leanprover-community/mathlib4",
    )
    parser.add_argument(
        "--commit",
        type=str,
        default="c44e0c8ee63ca166450922a373c7409c5d26b00b",
        help="Mathlib commit (default: v4.19.0 toolchain bump)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max entries (0=unlimited)")
    parser.add_argument("--split", action="store_true", help="Split existing JSONL into train/val")
    parser.add_argument("--input", type=str, default="train/mathlib_tactics.jsonl",
                        help="Input for --split mode")
    args = parser.parse_args()

    if args.split:
        split_data(args.input)
    else:
        extract_tactics(args.repo, args.commit, args.output, args.limit)
        # Auto-split after extraction
        split_data(args.output)


if __name__ == "__main__":
    main()
