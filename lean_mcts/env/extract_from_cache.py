"""
Extract tactic data from pre-cached LeanDojo AST files.

Bypasses the LeanDojo trace() build step (which can SIGSEGV non-deterministically)
and directly constructs a TracedRepo from cached *.ast.json files.

Usage:
    python env/extract_from_cache.py --output train/mathlib_tactics.jsonl
    python env/extract_from_cache.py --output train/mathlib_tactics.jsonl --limit 100
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
from collections import Counter
from pathlib import Path

# LeanDojo's AST parser recurses deeply on some Mathlib files.
sys.setrecursionlimit(50000)
try:
    resource.setrlimit(resource.RLIMIT_STACK, (64 * 1024 * 1024, resource.RLIM_INFINITY))
except (ValueError, resource.error):
    pass

from lean_dojo.data_extraction.traced_data import TracedRepo

# Reuse filtering/categorization from extract_data.py
from extract_data import categorize_tactic, should_skip, split_data

CACHE_DIR = Path.home() / ".cache/lean_dojo/repos"
MATHLIB_CACHE = CACHE_DIR / "gitpython-mathlib4-c44e0c8ee63ca166450922a373c7409c5d26b00b" / "mathlib4"


def extract_from_cache(output_path: str, limit: int = 0):
    """Extract tactic data from cached AST files."""
    if not MATHLIB_CACHE.exists():
        raise FileNotFoundError(f"Mathlib cache not found at {MATHLIB_CACHE}")

    # NUM_PROCS controls Ray parallelism
    os.environ.setdefault("NUM_PROCS", "8")

    # Check if trace.xml files already exist (from previous from_traced_files + save_to_disk)
    xml_count = len(list(MATHLIB_CACHE.glob("**/*.trace.xml")))
    if xml_count > 0:
        print(f"Found {xml_count} trace.xml files — loading from disk (fast)...")
        traced_repo = TracedRepo.load_from_disk(MATHLIB_CACHE, build_deps=True)
    else:
        ast_count = len(list(MATHLIB_CACHE.glob("**/*.ast.json")))
        print(f"Found {ast_count} AST files, no trace.xml — building from scratch...")
        traced_repo = TracedRepo.from_traced_files(MATHLIB_CACHE, build_deps=True)
        print("Saving trace metadata to disk...")
        traced_repo.save_to_disk()

    print(f"TracedRepo loaded: {len(traced_repo.traced_files)} traced files")

    # Symlink to expected cache location so trace() finds it in future
    expected_cache = Path.home() / ".cache/lean_dojo" / "leanprover-community-mathlib4-c44e0c8ee63ca166450922a373c7409c5d26b00b"
    if not expected_cache.exists():
        actual = CACHE_DIR / "gitpython-mathlib4-c44e0c8ee63ca166450922a373c7409c5d26b00b"
        expected_cache.symlink_to(actual)
        print(f"Symlinked {expected_cache} -> {actual}")

    # Extract tactic pairs
    kept = 0
    skipped_reasons: Counter = Counter()
    category_counts: Counter = Counter()
    files_seen: set[str] = set()

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w") as f:
        for traced_file in traced_repo.traced_files:
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

    # Save stats
    stats_path = out.parent / "data_stats.json"
    stats = {
        "total_processed": total_processed,
        "kept": kept,
        "skipped": dict(skipped_reasons),
        "categories": dict(category_counts.most_common()),
        "num_files": len(files_seen),
    }
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"  Stats:   {stats_path}")

    return kept


def main():
    parser = argparse.ArgumentParser(description="Extract tactic data from cached AST files")
    parser.add_argument("--output", type=str, default="train/mathlib_tactics.jsonl")
    parser.add_argument("--limit", type=int, default=0, help="Max entries (0=unlimited)")
    args = parser.parse_args()

    kept = extract_from_cache(args.output, args.limit)

    if kept > 0 and args.limit == 0:
        # Auto-split after full extraction
        split_data(args.output)


if __name__ == "__main__":
    main()
