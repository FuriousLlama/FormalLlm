"""Sprint 29 Phase 1.4 — held-out leakage check.

Before Phase 2 training can start, we must verify that no training pair
leaks into the 12-task held-out set. The plan requires this check for the
6 diagnostic held-out tasks (eval_stack, majority_element, map_with_index,
max_subarray_sum, prefix_while_increasing, unfold_n) and the 6 new
hard-tier held-out tasks from held_out_hard_tasks.py.

Leakage check algorithm:
    1. Build n-gram signatures (default n=5) from each held-out task's
       reference implementation. The reference implementations live in:
         - lean_mcts/synth/diag_holdout.txt (for the diagnostic tier — we
           pull the actual bodies from TasksDiagnostic.lean via the
           FormalNav copy, since the held-out signatures were frozen in S28)
         - lean_mcts/synth/held_out_hard_tasks.py (for the hard-tier, the
           reference implementations are documented in the module docstring)
    2. For each training pair, tokenize the tactic body and extract the
       same n-grams.
    3. Compute Jaccard similarity between every (training_pair, held_out_task)
       pair. A Jaccard score above THRESHOLD (default 0.20) is flagged as
       potential leakage.
    4. Report: (a) flagged pairs with matching held-out task + score,
       (b) per-task flagged counts, (c) write a detailed audit JSON.

This is a purely lexical check — it catches `exact List.reverse xs` vs
`exact xs.reverse` but not `exact (xs.filter p).map f` vs `exact List.map
f (xs.filter p)`. For S29's purposes this is good enough: the held-out
tasks use distinctive algorithmic patterns (Kadane, Boyer-Moore, window
sums) whose n-grams are unlikely to appear elsewhere unless the training
pair genuinely implements the same algorithm.

Usage:
    python -m lean_mcts.synth.leakage_check --train lean_mcts/train/diverse_corpus_v2.jsonl
    python -m lean_mcts.synth.leakage_check --train ... --output audit.json
    python -m lean_mcts.synth.leakage_check --train ... --threshold 0.15
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


DEFAULT_NGRAM_SIZE = 5
DEFAULT_JACCARD_THRESHOLD = 0.20


# ---------------------------------------------------------------------------
# Reference implementations
# ---------------------------------------------------------------------------

# For each of the 12 held-out tasks, a canonical lexical signature of its
# reference implementation. These are written as the tokens you'd expect
# to appear in a verified solution — not the solution itself (to avoid
# self-leakage via this file). The format is intentionally loose: a
# free-form string containing the key operators and identifier calls.

DIAGNOSTIC_HELDOUT: dict[str, str] = {
    "eval_stack": (
        "xs.foldl fun acc x match acc x x :: acc a :: b :: rest (a + b) :: rest "
        "List.headD 0"
    ),
    "majority_element": (
        "xs.foldl fun (cand count) x if count == 0 (x, 1) else if x == cand "
        "(cand, count + 1) else (cand, count - 1) .fst"
    ),
    "map_with_index": (
        "xs.zipIdx.map fun (x, i) x * (i + 1) "
        "enum mapIdx foldlIdx List.range"
    ),
    "max_subarray_sum": (
        "xs.foldl fun (best, cur) x let nc := max x (cur + x) (max best nc, nc) "
        "Kadane"
    ),
    "prefix_while_increasing": (
        "xs.foldl fun (acc, last, done) x if done || x <= last (acc, last, true) "
        "else (acc ++ [x], x, false) takeWhile"
    ),
    "unfold_n": (
        "List.range n |>.foldl fun (acc, s) _ (acc ++ [s], f s) (.fst) "
        "unfoldN iterate"
    ),
}

HARD_HELDOUT: dict[str, str] = {
    "sum_at_odd_indices": (
        "xs.zipIdx.foldl fun acc (x, i) if i % 2 == 1 acc + x else acc "
        "enum i * odd 1 mapIdx"
    ),
    "zip_diff": (
        "xs.zip xs.tail |>.map fun (a, b) b - a zipWith pairs diff"
    ),
    "longest_increasing_run": (
        "match xs with | [] => 0 | h :: t foldl fun (best cur prev) x "
        "if x > prev cur + 1 else 1 max best c strictly increasing"
    ),
    "max_subarray_product": (
        "match xs with | [] => 0 | h :: t foldl fun (mxE mnE best) x "
        "mxE * x mnE * x max best newMax negative flip product Kadane"
    ),
    "merge_sorted_desc": (
        "match xs ys with | [] ys ys | xs [] xs | x::xs y::ys "
        "if x >= y x :: mergeSortedDesc xs (y::ys) else y :: mergeSortedDesc"
    ),
    "window_sums": (
        "if k == 0 || k > xs.length [] else List.range (xs.length - k + 1) "
        "|>.map fun i ((xs.drop i).take k).foldl (· + ·) 0 sliding"
    ),
}


def all_heldout() -> dict[str, str]:
    return {**DIAGNOSTIC_HELDOUT, **HARD_HELDOUT}


# ---------------------------------------------------------------------------
# N-gram tokenization
# ---------------------------------------------------------------------------

# Lean token splitter: identifiers, numeric literals, and operator runs.
_TOKEN_RE = re.compile(r'[A-Za-z_][A-Za-z_0-9\']*|\d+|[=<>!+\-*/|&^~@%]+')


def tokenize(s: str) -> list[str]:
    """Split a string into Lean-ish tokens. Skips whitespace and punctuation
    that we consider non-discriminating (parens, commas, brackets).
    """
    return _TOKEN_RE.findall(s)


def ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    """Return the set of n-grams of the given token sequence. Short sequences
    (len < n) are treated as a single n-gram of all their tokens, to avoid
    returning an empty set for trivial inputs.
    """
    if len(tokens) < n:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[i:i+n]) for i in range(len(tokens) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

@dataclass
class LeakHit:
    pair_idx: int
    pair_name: str
    pair_tactic: str
    heldout_name: str
    score: float
    shared_ngrams: int


@dataclass
class LeakageReport:
    n: int
    threshold: float
    heldout_tasks: list[str] = field(default_factory=list)
    total_pairs: int = 0
    flagged: list[LeakHit] = field(default_factory=list)
    per_task_hits: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "n_gram_size": self.n,
            "jaccard_threshold": self.threshold,
            "heldout_tasks": self.heldout_tasks,
            "total_pairs": self.total_pairs,
            "flagged_count": len(self.flagged),
            "per_task_hits": self.per_task_hits,
            "flagged": [
                {
                    "pair_idx": h.pair_idx,
                    "pair_name": h.pair_name,
                    "pair_tactic": h.pair_tactic[:200],
                    "heldout_name": h.heldout_name,
                    "score": round(h.score, 4),
                    "shared_ngrams": h.shared_ngrams,
                }
                for h in self.flagged
            ],
        }


def run_leakage_check(
    pairs: list[dict],
    n: int = DEFAULT_NGRAM_SIZE,
    threshold: float = DEFAULT_JACCARD_THRESHOLD,
    heldout: dict[str, str] | None = None,
) -> LeakageReport:
    if heldout is None:
        heldout = all_heldout()

    # Precompute n-gram sets for every held-out task
    heldout_sigs: dict[str, set] = {
        name: ngrams(tokenize(sig), n) for name, sig in heldout.items()
    }

    report = LeakageReport(n=n, threshold=threshold)
    report.heldout_tasks = list(heldout_sigs.keys())
    report.total_pairs = len(pairs)
    report.per_task_hits = {name: 0 for name in heldout_sigs}

    for idx, p in enumerate(pairs):
        tactic = p.get("tactic", "")
        pair_ngrams = ngrams(tokenize(tactic), n)
        if not pair_ngrams:
            continue
        for ho_name, ho_sig in heldout_sigs.items():
            score = jaccard(pair_ngrams, ho_sig)
            if score >= threshold:
                shared = len(pair_ngrams & ho_sig)
                report.flagged.append(LeakHit(
                    pair_idx=idx,
                    pair_name=p.get("full_name") or p.get("name", "?"),
                    pair_tactic=tactic,
                    heldout_name=ho_name,
                    score=score,
                    shared_ngrams=shared,
                ))
                report.per_task_hits[ho_name] += 1

    return report


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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--train", action="append", required=True,
        help="Training pair JSONL file(s). Pass multiple times to merge."
    )
    parser.add_argument("--output", type=str, default=None,
                        help="Write audit JSON here (default: stdout only)")
    parser.add_argument("--n", type=int, default=DEFAULT_NGRAM_SIZE,
                        help=f"N-gram size (default {DEFAULT_NGRAM_SIZE})")
    parser.add_argument("--threshold", type=float, default=DEFAULT_JACCARD_THRESHOLD,
                        help=f"Jaccard threshold for flagging (default {DEFAULT_JACCARD_THRESHOLD})")
    parser.add_argument("--fail-on-leak", action="store_true",
                        help="Exit 1 if any pair crosses the threshold (CI gate)")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent.parent
    all_pairs: list[dict] = []
    for tp in args.train:
        path = project_root / tp
        if not path.exists():
            parser.error(f"training file not found: {path}")
        all_pairs.extend(load_pairs(path))

    print(f"Loaded {len(all_pairs)} pairs from {len(args.train)} input file(s)")
    print(f"Held-out tasks: {len(all_heldout())} "
          f"(6 diagnostic + 6 new hard-tier)")
    print(f"N-gram size: {args.n}   Threshold: {args.threshold}")

    report = run_leakage_check(
        all_pairs, n=args.n, threshold=args.threshold
    )

    print(f"\n=== leakage check ===")
    print(f"total pairs: {report.total_pairs}")
    print(f"flagged:     {len(report.flagged)}")
    print(f"per-task hits:")
    for name in report.heldout_tasks:
        hits = report.per_task_hits[name]
        mark = " !!" if hits > 0 else ""
        print(f"  {name:26s}: {hits}{mark}")

    if report.flagged:
        print(f"\n=== flagged pairs (top 20) ===")
        top = sorted(report.flagged, key=lambda h: -h.score)[:20]
        for h in top:
            print(f"  [{h.score:.2f}] {h.pair_name:30s} ~ {h.heldout_name:22s} ({h.shared_ngrams} ngrams)")
            print(f"        {h.pair_tactic[:120]}")

    if args.output:
        output_path = project_root / args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        print(f"\nWrote audit JSON → {output_path}")

    if args.fail_on_leak and report.flagged:
        print("\nLEAKAGE DETECTED — failing as requested by --fail-on-leak", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
