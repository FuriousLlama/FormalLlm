"""Sprint 29 Phase 1.3 — quality filter pass over mined library pairs.

Takes one or more JSONL files produced by extract_lean_defs.py and/or
pattern_miner.py, merges them, applies quality filters from sprint29.md,
and writes a deduplicated output corpus ready for train/val splitting.

Filter pipeline:
  1. Concatenate all input files into one pair stream
  2. Dedupe by (goal_type, tactic) — keep first occurrence
  3. Drop pairs whose tactic body is < 3 AST nodes (template-covered trivials)
  4. Drop pairs > MAX_TOKENS estimated tokens (decoder budget)
  5. Dedupe against an S28 reference corpus (keep-first-wins: new pairs
     already present in the baseline are dropped so we don't train on
     twice-seen data)
  6. Report category distribution

The "AST nodes" metric is approximated via a simple identifier/operator
count on the tactic body. It's not a real AST parser — for the S28 training
data it correlates well with triviality. A one-identifier tactic like
`exact xs.sum` has 2 nodes (xs, .sum). A three-step chain like
`exact (xs.filter p).map f |>.sum` has ~7.

Usage:
    python -m lean_mcts.synth.quality_filter \\
        --input lean_mcts/train/library_pairs_v3.jsonl \\
        --input lean_mcts/train/pattern_pairs.jsonl \\
        --baseline lean_mcts/train/library_pairs.jsonl \\
        --output lean_mcts/train/diverse_corpus_v2.jsonl

    # Relaxed mode (keep trivials for debugging):
    python -m lean_mcts.synth.quality_filter --input ... --output ... --no-trivial-filter

    # Drop-in stats mode:
    python -m lean_mcts.synth.quality_filter --input ... --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


# Rough upper bound on tokenized tactic length. Our Qwen-base tokenizer
# produces ~1.3 tokens per character of Lean on average, so 512 tokens
# ≈ 400 chars. We use 400 as a cheap pre-tokenizer-free cap.
MAX_TACTIC_CHARS = 400

# Minimum count of "interesting" tokens in the tactic body to keep it.
# A trivial one-call tactic like `exact xs.sum` has 2 (xs, sum). We want
# the body to reference at least 3 distinct identifiers or operators so
# the pair carries compositional signal beyond what the S22 template
# catalog already covers.
MIN_AST_NODES = 3


# Tokens that don't count toward the AST-node bound (punctuation, keywords,
# the leading `exact`, and very common syntactic noise).
_SKIP_TOKENS = {
    "exact", "fun", "=>", "let", "in", "do", "match", "with", "|",
    "if", "then", "else",
    ":", ":=", ":", "::", ",", ";", "?", "!",
    "(", ")", "[", "]", "{", "}",
    "→", "->", "<-",
    ".", "..",
}


def count_ast_nodes(tactic: str) -> int:
    """Cheap stand-in for an AST-node count: tokenize and count distinct
    non-trivial identifiers/operators."""
    # Normalize member access: xs.filter becomes two tokens (xs, filter)
    normalized = re.sub(r'\.(\w+)', r' \1', tactic)
    # Tokenize on whitespace and punctuation
    tokens = re.findall(r'[A-Za-z_]\w*|[+\-*/<>=!&|^~]+|\d+', normalized)
    significant = [t for t in tokens if t not in _SKIP_TOKENS]
    return len(significant)


def dedup_key(p: dict) -> tuple[str, str]:
    """Identity key for dedup — (state, tactic) captures whether the pair
    teaches the same lesson even if other metadata differs."""
    return (p.get("state", ""), p.get("tactic", ""))


def load_pairs(path: Path) -> list[dict]:
    pairs: list[dict] = []
    with open(path) as f:
        for line in f:
            if line.strip():
                try:
                    pairs.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"WARN: skipping malformed line in {path}: {e}", file=sys.stderr)
    return pairs


def filter_pairs(
    inputs: list[Path],
    baseline: Path | None,
    min_ast_nodes: int = MIN_AST_NODES,
    max_chars: int = MAX_TACTIC_CHARS,
    drop_trivials: bool = True,
    verbose: bool = False,
) -> tuple[list[dict], dict[str, int]]:
    """Run the full quality filter pipeline. Returns (filtered_pairs, stats)."""
    stats: dict[str, int] = {
        "loaded": 0,
        "dedup_self": 0,
        "dropped_trivial": 0,
        "dropped_too_long": 0,
        "dedup_vs_baseline": 0,
        "kept": 0,
    }

    # 1. Concatenate inputs
    all_pairs: list[dict] = []
    for p in inputs:
        loaded = load_pairs(p)
        all_pairs.extend(loaded)
        if verbose:
            print(f"  loaded {len(loaded):4d} from {p.name}")
    stats["loaded"] = len(all_pairs)

    # 2. Self-dedupe
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for pair in all_pairs:
        key = dedup_key(pair)
        if key in seen:
            continue
        seen.add(key)
        unique.append(pair)
    stats["dedup_self"] = stats["loaded"] - len(unique)

    # 3. Drop trivials (< min_ast_nodes)
    #    and 4. Drop oversized (> max_chars)
    kept: list[dict] = []
    for pair in unique:
        tactic = pair.get("tactic", "")
        if len(tactic) > max_chars:
            stats["dropped_too_long"] += 1
            continue
        if drop_trivials and count_ast_nodes(tactic) < min_ast_nodes:
            stats["dropped_trivial"] += 1
            continue
        kept.append(pair)

    # 5. Dedupe against S28 baseline (keep-first-wins: drop any v3 pair that
    #    already appears in the S28 corpus — we'd be training on duplicate
    #    signal otherwise)
    if baseline is not None and baseline.exists():
        baseline_pairs = load_pairs(baseline)
        baseline_keys = {dedup_key(p) for p in baseline_pairs}
        before = len(kept)
        kept = [p for p in kept if dedup_key(p) not in baseline_keys]
        stats["dedup_vs_baseline"] = before - len(kept)
        if verbose:
            print(f"  S28 baseline: {len(baseline_pairs)} pairs")

    stats["kept"] = len(kept)
    return kept, stats


def category_report(pairs: list[dict]) -> dict[str, int]:
    """Count pairs by kind (EQCOMP / SIMPLE / WHERE) and by pattern tag."""
    by_kind = Counter()
    by_pattern = Counter()
    by_source = Counter()
    for p in pairs:
        by_kind[p.get("kind", "?")] += 1
        by_source[p.get("source", "?")] += 1
        for pat in p.get("patterns", []) or []:
            by_pattern[pat] += 1
    return {
        "by_kind": dict(by_kind),
        "by_source": dict(by_source),
        "by_pattern": dict(by_pattern),
    }


def diversity_score(pairs: list[dict]) -> dict[str, float]:
    """Classify each pair's tactic by pattern category for the diversity gate.

    Matches `extract_lean_defs.audit_diversity`'s buckets so the numbers
    are comparable with the Phase 1.1 audit output.
    """
    patterns = {
        "let_rec": 0,
        "match": 0,
        "let_chain": 0,
        "foldl_foldr": 0,
        "lambda": 0,
        "simple_exact": 0,
    }
    for p in pairs:
        t = p.get("tactic", "")
        if "let rec" in t:
            patterns["let_rec"] += 1
        elif "match" in t:
            patterns["match"] += 1
        elif t.count("let ") >= 2:
            patterns["let_chain"] += 1
        elif "foldl" in t or "foldr" in t:
            patterns["foldl_foldr"] += 1
        elif "fun " in t:
            patterns["lambda"] += 1
        else:
            patterns["simple_exact"] += 1
    total = sum(patterns.values())
    return {
        "counts": patterns,
        "total": total,
        "non_trivial_pct": (
            100 * (total - patterns["simple_exact"]) / total if total else 0
        ),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Phase 1.3 quality filter for Sprint 29 mined library pairs"
    )
    parser.add_argument(
        "--input", action="append", required=True,
        help="Input JSONL file(s). Pass --input multiple times to concatenate "
             "extract_lean_defs and pattern_miner outputs."
    )
    parser.add_argument(
        "--baseline", type=str, default="lean_mcts/train/library_pairs.jsonl",
        help="S28 baseline corpus for dedup (default: library_pairs.jsonl). "
             "Pass empty string to disable baseline dedup."
    )
    parser.add_argument(
        "--output", type=str, required=True,
        help="Output JSONL for filtered corpus."
    )
    parser.add_argument(
        "--min-ast-nodes", type=int, default=MIN_AST_NODES,
        help=f"Minimum significant-token count per tactic (default {MIN_AST_NODES})"
    )
    parser.add_argument(
        "--max-chars", type=int, default=MAX_TACTIC_CHARS,
        help=f"Maximum tactic length in chars (default {MAX_TACTIC_CHARS})"
    )
    parser.add_argument("--no-trivial-filter", action="store_true",
                        help="Skip the <min-ast-nodes triviality filter")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report stats but don't write output")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent.parent
    inputs = [project_root / p for p in args.input]
    for p in inputs:
        if not p.exists():
            parser.error(f"input file not found: {p}")

    baseline_path = (project_root / args.baseline) if args.baseline else None

    kept, stats = filter_pairs(
        inputs=inputs,
        baseline=baseline_path,
        min_ast_nodes=args.min_ast_nodes,
        max_chars=args.max_chars,
        drop_trivials=not args.no_trivial_filter,
        verbose=args.verbose,
    )

    cats = category_report(kept)
    diversity = diversity_score(kept)

    print(f"\n=== quality filter stats ===")
    for k, v in stats.items():
        print(f"  {k:24s}: {v}")

    print(f"\n=== category report ===")
    for k, v in cats["by_kind"].items():
        print(f"  kind {k:12s}: {v}")
    for k, v in cats["by_source"].items():
        print(f"  source {k:12s}: {v}")
    if cats["by_pattern"]:
        for k, v in sorted(cats["by_pattern"].items(), key=lambda x: -x[1]):
            print(f"  pattern {k:24s}: {v}")

    print(f"\n=== diversity audit ===")
    for k, v in sorted(diversity["counts"].items(), key=lambda x: -x[1]):
        pct = 100 * v / diversity["total"] if diversity["total"] else 0
        print(f"  {k:14s}: {v:4d} ({pct:5.1f}%)")
    print(f"  non-trivial: {diversity['non_trivial_pct']:.1f}% "
          f"(gate: ≥50% for Phase 1.5)")

    if diversity["non_trivial_pct"] < 50 and diversity["total"] > 0:
        print("  WARNING: diversity gate NOT met")

    if args.dry_run:
        print("\n(dry run: output not written)")
        return

    if not kept:
        print("\nNo pairs survived the filter — nothing to write.", file=sys.stderr)
        sys.exit(1)

    output_path = project_root / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for p in kept:
            f.write(json.dumps(p) + "\n")
    print(f"\nWrote {len(kept)} pairs → {output_path}")


if __name__ == "__main__":
    main()
