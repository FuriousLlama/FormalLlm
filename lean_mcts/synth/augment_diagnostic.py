"""Augment Sprint 27 diagnostic reference implementations for training.

Generates variations of 12 train-split diagnostic tasks by:
  - Flipping comparison operators (< → >, ≤ → ≥)
  - Changing accumulator initial values
  - Varying predicates (> 0, < 0, even, odd)
  - Swapping operations (+ → *, max → min)
  - Reversing accumulation direction

6 tasks are held out for clean generalization evaluation.

Usage:
    python -m lean_mcts.synth.augment_diagnostic
    python -m lean_mcts.synth.augment_diagnostic --verify --output train/diag_augmented.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Hold-out split: 6 tasks (≥1 per tier)
# ---------------------------------------------------------------------------

HOLDOUT_TASKS = {
    "map_with_index",          # Tier A (index-tracking accumulator)
    "prefix_while_increasing", # Tier A (early-stop fold)
    "max_subarray_sum",        # Tier B (Kadane's — algorithmic)
    "majority_element",        # Tier B (Boyer-Moore — algorithmic)
    "unfold_n",                # Tier C (recursive generation)
    "eval_stack",              # Tier D (stack machine)
}

# ---------------------------------------------------------------------------
# Reference implementations (12 train-split tasks)
# ---------------------------------------------------------------------------

# Each entry: (task_name, tier, goal_type, reference_tactic, description)
TRAIN_REFS = [
    # Tier A
    (
        "second_smallest", "A",
        "List Int → Int",
        "exact fun xs => match xs with | [] => 0 | [x] => x "
        "| h :: t => (t.foldl (fun (lo, lo2) x => "
        "if decide (x < lo) then (x, lo) "
        "else if decide (x < lo2) then (lo, x) "
        "else (lo, lo2)) (h, h)).2",
        "second smallest element via tuple accumulator fold"
    ),
    (
        "remove_duplicates_keep_last", "A",
        "List Int → List Int",
        "exact fun xs => (xs.reverse.eraseDups).reverse",
        "remove duplicates keeping last occurrence"
    ),
    (
        "dot_product_self", "A",
        "List Int → Int",
        "exact fun xs => xs.foldl (fun acc h => acc + h * h) 0",
        "sum of squares (dot product with self)"
    ),

    # Tier B
    (
        "longest_plateau", "B",
        "List Int → Nat",
        "exact fun xs => match xs with | [] => 0 "
        "| h :: t => let r := t.foldl (fun (best, cur, prev) x => "
        "if decide (x = prev) then (max best (cur + 1), cur + 1, x) "
        "else (best, 1, x)) ((1 : Nat), (1 : Nat), h); max r.1 r.2.1",
        "longest run of identical values"
    ),
    (
        "dutch_flag", "B",
        "Int → List Int → List Int",
        "exact fun pivot xs => "
        "xs.filter (fun x => decide (x < pivot)) "
        "++ xs.filter (fun x => decide (x = pivot)) "
        "++ xs.filter (fun x => decide (x > pivot))",
        "3-way partition around pivot"
    ),
    (
        "equilibrium_index", "B",
        "List Int → Int",
        "exact fun xs => let total := xs.foldl (· + ·) 0; "
        "(xs.foldl (fun (idx, leftSum, result) h => "
        "if decide (result ≥ 0) then (idx + 1, leftSum, result) "
        "else if decide (leftSum = total - leftSum - h) then (idx + 1, leftSum + h, idx) "
        "else (idx + 1, leftSum + h, result)) ((0 : Int), (0 : Int), (-1 : Int))).2.2",
        "first index where left-sum equals right-sum"
    ),

    # Tier C
    (
        "chunks_of", "C",
        "Nat → List Int → List (List Int)",
        "exact fun n xs => if decide (n = 0) then [xs] "
        "else List.range ((xs.length + n - 1) / n) |>.map (fun i => (xs.drop (i * n)).take n)",
        "split list into groups of n"
    ),
    (
        "diag_transpose", "C",
        "List (List Int) → List (List Int)",
        "exact fun xss => match xss with | [] => [] "
        "| row :: _ => List.range row.length |>.map (fun i => xss.filterMap (fun r => r[i]?))",
        "matrix transpose"
    ),
    (
        "insert_sorted", "C",
        "Int → List Int → List Int",
        "exact fun x xs => match xs with | [] => [x] "
        "| h :: t => if decide (x ≤ h) then x :: h :: t "
        "else h :: List.insertSorted_ref x t",
        "insert at sorted position"
    ),
    (
        "interleave_zip", "C",
        "List Int → List Int → List Int",
        "exact fun xs ys => "
        "let paired := (xs.zip ys).foldl (fun acc (a, b) => acc ++ [a, b]) []; "
        "paired ++ xs.drop ys.length ++ ys.drop xs.length",
        "interleave two lists element-by-element"
    ),

    # Tier D
    (
        "diag_insertion_sort", "D",
        "List Int → List Int",
        "exact fun xs => xs.foldl (fun sorted h => "
        "sorted.filter (fun x => decide (x ≤ h)) ++ [h] "
        "++ sorted.filter (fun x => decide (x > h))) []",
        "sort by repeated insertion"
    ),
    (
        "all_sublists", "D",
        "List Int → List (List Int)",
        "exact fun xs => let n := xs.length; "
        "(List.range n).foldl (fun acc i => "
        "acc ++ (List.range (n - i)).map (fun len => "
        "(xs.drop i).take (len + 1))) []",
        "all contiguous sublists"
    ),
]


# ---------------------------------------------------------------------------
# Augmentation rules
# ---------------------------------------------------------------------------

def augment_task(name: str, tier: str, goal_type: str,
                 tactic: str, desc: str) -> list[dict]:
    """Generate augmented variations of a single task.

    Returns list of (state, tactic) training pairs.
    """
    pairs = []

    # Always include the original
    pairs.append(_make_pair(name, goal_type, tactic, "original", desc))

    # Apply pattern-specific augmentations
    aug_fns = {
        "second_smallest": _aug_second_smallest,
        "dot_product_self": _aug_dot_product_self,
        "remove_duplicates_keep_last": _aug_remove_dups,
        "longest_plateau": _aug_longest_plateau,
        "dutch_flag": _aug_dutch_flag,
        "equilibrium_index": _aug_equilibrium,
        "chunks_of": _aug_chunks,
        "diag_transpose": _aug_transpose,
        "insert_sorted": _aug_insert_sorted,
        "interleave_zip": _aug_interleave,
        "diag_insertion_sort": _aug_insertion_sort,
        "all_sublists": _aug_all_sublists,
    }

    fn = aug_fns.get(name)
    if fn:
        pairs.extend(fn(name, goal_type))

    return pairs


def _make_pair(name: str, goal_type: str, tactic: str,
               variant: str, desc: str) -> dict:
    return {
        "state": f"⊢ {goal_type}",
        "tactic": tactic,
        "goal_type": goal_type,
        "source": "diagnostic_augmented",
        "name": f"{name}_{variant}",
        "kind": "AUGMENTED",
        "description": desc,
    }


# ---------------------------------------------------------------------------
# Per-task augmentations
# ---------------------------------------------------------------------------

def _aug_second_smallest(name: str, gt: str) -> list[dict]:
    """Variations: second_largest, largest via fold."""
    return [
        _make_pair(name, gt,
            "exact fun xs => match xs with | [] => 0 | [x] => x "
            "| h :: t => (t.foldl (fun (hi, hi2) x => "
            "if decide (x > hi) then (x, hi) "
            "else if decide (x > hi2) then (hi, x) "
            "else (hi, hi2)) (h, h)).2",
            "second_largest", "second largest element (flipped comparisons)"),
        _make_pair(name, gt,
            "exact fun xs => match xs with | [] => 0 | [x] => x "
            "| h :: t => t.foldl (fun best x => if decide (x > best) then x else best) h",
            "max_via_fold", "maximum element via fold"),
        _make_pair(name, gt,
            "exact fun xs => match xs with | [] => 0 | [x] => x "
            "| h :: t => t.foldl (fun best x => if decide (x < best) then x else best) h",
            "min_via_fold", "minimum element via fold"),
    ]


def _aug_dot_product_self(name: str, gt: str) -> list[dict]:
    """Variations: sum of cubes, sum of abs, product."""
    return [
        _make_pair(name, gt,
            "exact fun xs => xs.foldl (fun acc h => acc + h * h * h) 0",
            "sum_cubes", "sum of cubes"),
        _make_pair(name, gt,
            "exact fun xs => xs.foldl (fun acc h => acc + (if decide (h ≥ 0) then h else -h)) 0",
            "sum_abs", "sum of absolute values"),
        _make_pair(name, gt,
            "exact fun xs => xs.foldl (fun acc h => acc * h) 1",
            "product", "product of all elements"),
        _make_pair(name, gt,
            "exact fun xs => xs.foldl (fun acc h => acc + h) 0",
            "sum", "sum of all elements"),
    ]


def _aug_remove_dups(name: str, gt: str) -> list[dict]:
    """Variations: keep first, reverse twice."""
    return [
        _make_pair(name, "List Int → List Int",
            "exact fun xs => xs.eraseDups",
            "keep_first", "remove duplicates keeping first occurrence"),
        _make_pair(name, "List Int → List Int",
            "exact fun xs => xs.reverse.reverse",
            "double_reverse", "identity via double reverse"),
    ]


def _aug_longest_plateau(name: str, gt: str) -> list[dict]:
    """Variations: count distinct, count runs."""
    return [
        _make_pair(name, "List Int → Nat",
            "exact fun xs => match xs with | [] => 0 "
            "| h :: t => (t.foldl (fun (count, prev) x => "
            "if decide (x = prev) then (count, x) "
            "else (count + 1, x)) ((1 : Nat), h)).1",
            "count_distinct", "count distinct adjacent values"),
        _make_pair(name, "List Int → Nat",
            "exact fun xs => match xs with | [] => 0 "
            "| h :: t => (t.foldl (fun (runs, prev) x => "
            "if decide (x = prev) then (runs, x) "
            "else (runs + 1, x)) ((1 : Nat), h)).1",
            "count_runs", "count runs of identical values"),
    ]


def _aug_dutch_flag(name: str, gt: str) -> list[dict]:
    """Variations: 2-way partition, reversed order."""
    return [
        _make_pair(name, gt,
            "exact fun pivot xs => "
            "xs.filter (fun x => decide (x ≤ pivot)) "
            "++ xs.filter (fun x => decide (x > pivot))",
            "two_way", "2-way partition (≤ and >)"),
        _make_pair(name, gt,
            "exact fun pivot xs => "
            "xs.filter (fun x => decide (x > pivot)) "
            "++ xs.filter (fun x => decide (x = pivot)) "
            "++ xs.filter (fun x => decide (x < pivot))",
            "reversed", "3-way partition reversed (greater first)"),
        _make_pair(name, gt,
            "exact fun pivot xs => "
            "xs.filter (fun x => decide (x < pivot)) "
            "++ xs.filter (fun x => decide (x ≥ pivot))",
            "two_way_lt", "2-way partition (< and ≥)"),
    ]


def _aug_equilibrium(name: str, gt: str) -> list[dict]:
    """Variations: sum prefix."""
    return [
        _make_pair(name, gt,
            "exact fun xs => xs.foldl (fun (idx, leftSum) h => "
            "(idx + 1, leftSum + h)) ((0 : Int), (0 : Int)) |>.2",
            "total_sum", "total sum via fold with index tracking"),
    ]


def _aug_chunks(name: str, gt: str) -> list[dict]:
    """Variations: windows (sliding)."""
    return [
        _make_pair(name, gt,
            "exact fun n xs => if decide (n = 0) then [] "
            "else List.range (xs.length - n + 1) |>.map (fun i => (xs.drop i).take n)",
            "sliding_windows", "sliding windows of size n"),
    ]


def _aug_transpose(name: str, gt: str) -> list[dict]:
    """Variations: get column."""
    return [
        _make_pair(name, "Nat → List (List Int) → List Int",
            "exact fun i xss => xss.filterMap (fun r => r[i]?)",
            "get_column", "extract single column from matrix"),
    ]


def _aug_insert_sorted(name: str, gt: str) -> list[dict]:
    """Variations: insert at end, insert descending."""
    return [
        _make_pair(name, gt,
            "exact fun x xs => xs ++ [x]",
            "append", "insert at end (trivial)"),
        _make_pair(name, gt,
            "exact fun x xs => match xs with | [] => [x] "
            "| h :: t => if decide (x ≥ h) then x :: h :: t "
            "else h :: List.insertSorted_ref x t",
            "descending", "insert into descending-sorted list"),
    ]


def _aug_interleave(name: str, gt: str) -> list[dict]:
    """Variations: zip with sum, zip with product."""
    return [
        _make_pair(name, "List Int → List Int → List Int",
            "exact fun xs ys => (xs.zip ys).map (fun (a, b) => a + b)",
            "zip_sum", "pairwise sum (zip + map)"),
        _make_pair(name, "List Int → List Int → List Int",
            "exact fun xs ys => (xs.zip ys).map (fun (a, b) => a * b)",
            "zip_product", "pairwise product (zip + map)"),
    ]


def _aug_insertion_sort(name: str, gt: str) -> list[dict]:
    """Variations: sort descending."""
    return [
        _make_pair(name, gt,
            "exact fun xs => xs.foldl (fun sorted h => "
            "sorted.filter (fun x => decide (x ≥ h)) ++ [h] "
            "++ sorted.filter (fun x => decide (x < h))) []",
            "descending", "insertion sort descending"),
    ]


def _aug_all_sublists(name: str, gt: str) -> list[dict]:
    """Variations: all prefixes, all suffixes."""
    return [
        _make_pair(name, "List Int → List (List Int)",
            "exact fun xs => (List.range (xs.length + 1)).map (fun i => xs.take i)",
            "all_prefixes", "all prefixes"),
        _make_pair(name, "List Int → List (List Int)",
            "exact fun xs => (List.range (xs.length + 1)).map (fun i => xs.drop i)",
            "all_suffixes", "all suffixes"),
    ]


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _strip_fun_wrapper(tactic: str) -> tuple[str, str]:
    """Extract intro names from `exact fun <args> => <body>`.

    Returns (intro_names, stripped_tactic):
      'exact fun xs => xs.foldl ...'  ->  ('xs', 'exact xs.foldl ...')
      'exact fun pivot xs => ...'      ->  ('pivot xs', 'exact ...')

    If no fun wrapper, returns ('', tactic).
    """
    import re
    m = re.match(r'exact\s+fun\s+([^=]+?)\s*=>\s*(.+)$', tactic, re.DOTALL)
    if not m:
        return ("", tactic)
    args = m.group(1).strip()
    body = m.group(2).strip()
    return (args, f"exact {body}")


def verify_pairs(pairs: list[dict], project_root: Path,
                 verbose: bool = False) -> list[dict]:
    """Verify augmented pairs through Pantograph using intro-style format."""
    try:
        from pantograph import Server
    except ImportError:
        print("WARNING: pantograph not available, skipping verification")
        return pairs

    server = Server(
        imports=["FormalNav"],
        project_path=str(project_root / "lean_mcts" / "env" / "FormalNav"),
        timeout=30,
    )

    verified = []
    for p in pairs:
        goal_type = p["goal_type"]
        original_tactic = p["tactic"]
        intro_names, stripped_tactic = _strip_fun_wrapper(original_tactic)
        try:
            state = server.goal_start(expr=goal_type)
            if intro_names:
                state = server.goal_tactic(state, tactic=f"intro {intro_names}")
            goal_str = str(state)
            result = server.goal_tactic(state, tactic=stripped_tactic)
            if result.is_solved:
                p["state"] = goal_str
                p["tactic"] = stripped_tactic
                verified.append(p)
                if verbose:
                    print(f"  VERIFIED: {p['name']}")
            else:
                if verbose:
                    print(f"  PARTIAL:  {p['name']} — {str(result)[:60]}")
        except Exception as e:
            if verbose:
                print(f"  REJECT:   {p['name']} — {str(e)[:80]}")

    return verified


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Augment diagnostic reference implementations"
    )
    parser.add_argument(
        "--output", type=str, default="lean_mcts/train/diag_augmented.jsonl"
    )
    parser.add_argument("--holdout-output", type=str,
                        default="lean_mcts/synth/diag_holdout.txt")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent.parent

    # Write hold-out file
    holdout_path = project_root / args.holdout_output
    holdout_path.parent.mkdir(parents=True, exist_ok=True)
    with open(holdout_path, "w") as f:
        f.write("# Sprint 28 diagnostic hold-out tasks (6 of 18)\n")
        f.write("# These tasks have NO augmented variants in training data\n")
        for name in sorted(HOLDOUT_TASKS):
            f.write(f"{name}\n")
    print(f"Hold-out tasks written to {holdout_path}")

    # Generate augmented pairs from train-split tasks
    all_pairs = []
    for name, tier, goal_type, tactic, desc in TRAIN_REFS:
        pairs = augment_task(name, tier, goal_type, tactic, desc)
        all_pairs.extend(pairs)
        print(f"  {name} (Tier {tier}): {len(pairs)} variants")

    print(f"Total augmented pairs: {len(all_pairs)}")

    # Verify
    if args.verify:
        print("Verifying through Pantograph...")
        all_pairs = verify_pairs(all_pairs, project_root, verbose=args.verbose)
        print(f"After verification: {len(all_pairs)} verified")

    # Remove goal_type (internal)
    for p in all_pairs:
        p.pop("goal_type", None)

    # Write output
    output_path = project_root / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for p in all_pairs:
            f.write(json.dumps(p) + "\n")

    print(f"Output: {output_path} ({len(all_pairs)} pairs)")


if __name__ == "__main__":
    main()
