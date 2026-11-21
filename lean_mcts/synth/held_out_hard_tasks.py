"""
Sprint 29 Held-Out Hard Tasks

6 compositional tasks modeled on Sprint 27's 16 classified failure patterns,
frozen in Phase 0.5 BEFORE library mining begins. These are the held-out set
for the new hard-tier half of the S29 primary metric — zero overlap with any
training data, verified by the Phase 1.4 leakage check.

Distribution:
- 2 Tier A: compositional wiring gap
    sum_at_odd_indices (index-tracking fold, mirrors mapWithIndex failure)
    zip_diff           (multi-op pipeline, mirrors removeDuplicatesKeepLast)
- 2 Tier B: tuple accumulator with field interaction
    longest_increasing_run (triple-field fold, mirrors longestPlateau)
    max_subarray_product   (dual-state Kadane variant, mirrors maxSubarraySum)
- 1 Tier C: structural recursion, multi-arg
    merge_sorted_desc      (two-list recursion with flipped comparison)
- 1 Tier D: multi-arg + index arithmetic + compositional
    window_sums            (sliding window, mirrors chunksOf failure)

Frozen behind git tag `s29-heldout-frozen`. Post-tag edits invalidate the
sprint's primary metric.

Reference implementations (for designer audit + leakage check in Phase 1.4):

1. sumAtOddIndices
    xs.zipIdx.foldl (fun acc (x, i) => if i % 2 == 1 then acc + x else acc) 0
    (or equivalent using enum / List.range)

2. zipDiff
    xs.zip xs.tail |>.map (fun (a, b) => b - a)

3. longestIncreasingRun
    match xs with
    | [] => 0
    | h :: t =>
      (t.foldl (fun (best, cur, prev) x =>
        let c := if x > prev then cur + 1 else 1
        (max best c, c, x)) (1, 1, h)).1

4. maxSubarrayProduct
    match xs with
    | [] => 0
    | h :: t =>
      let (_, _, best) := t.foldl (fun (mxE, mnE, best) x =>
        let a := mxE * x
        let b := mnE * x
        let mx := max x (max a b)
        let mn := min x (min a b)
        (mx, mn, max best mx)) (h, h, h)
      best

5. mergeSortedDesc (structural recursion on sum of list lengths)
    -- base cases
    | [], ys => ys
    | xs, [] => xs
    -- step
    | x :: xs', y :: ys' =>
        if x >= y then x :: mergeSortedDesc xs' (y :: ys')
        else y :: mergeSortedDesc (x :: xs') ys'

6. windowSums
    if k == 0 || k > xs.length then []
    else (List.range (xs.length - k + 1)).map
         (fun i => ((xs.drop i).take k).foldl (· + ·) 0)

None of the above reference implementations is constructible as a single
library call. The leakage check in Phase 1.4 must grep the training set for
the key function names and sub-expressions (e.g., `zipIdx`, `mxE * x`,
`mergeSortedDesc`, `List.range (xs.length - k + 1)`) to confirm zero
training-data contamination.
"""

from __future__ import annotations

from lean_mcts.benchmark.tasks import SynthesisTask


HELD_OUT_HARD_TASKS: list[SynthesisTask] = [
    # ===== Tier A: Compositional wiring gap =====
    SynthesisTask(
        name="sum_at_odd_indices",
        ghc_equivalent="sum_at_odd_indices",
        lean_file="FormalNav/TasksS29Heldout.lean",
        theorem_name="sumAtOddIndices",
        description="Sum of elements at 0-indexed odd positions (index-tracking fold)",
        test_cases=[
            {"input": "[1, 2, 3, 4, 5]", "expected": "6"},
            {"input": "[]", "expected": "0"},
            {"input": "[7]", "expected": "0"},
            {"input": "[1, 2]", "expected": "2"},
            {"input": "[10, 20, 30, 40]", "expected": "60"},
            {"input": "[-1, -2, -3, -4]", "expected": "-6"},
        ],
        category="s29_heldout_a",
        return_type="Int",
    ),
    SynthesisTask(
        name="zip_diff",
        ghc_equivalent="zip_diff",
        lean_file="FormalNav/TasksS29Heldout.lean",
        theorem_name="zipDiff",
        description="Pairwise differences between consecutive elements (b-a for each adjacent pair)",
        test_cases=[
            {"input": "[1, 5, 3, 8]", "expected": "[4, -2, 5]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[7]", "expected": "[]"},
            {"input": "[1, 2]", "expected": "[1]"},
            {"input": "[5, 5, 5]", "expected": "[0, 0]"},
            {"input": "[10, 1, -3]", "expected": "[-9, -4]"},
        ],
        category="s29_heldout_a",
        return_type="List Int",
    ),
    # ===== Tier B: Tuple accumulator with field interaction =====
    SynthesisTask(
        name="longest_increasing_run",
        ghc_equivalent="longest_increasing_run",
        lean_file="FormalNav/TasksS29Heldout.lean",
        theorem_name="longestIncreasingRun",
        description="Length of longest strictly-increasing contiguous subsequence",
        test_cases=[
            {"input": "[1, 2, 3, 1, 2, 3, 4]", "expected": "4"},
            {"input": "[]", "expected": "0"},
            {"input": "[5]", "expected": "1"},
            {"input": "[5, 5, 5]", "expected": "1"},
            {"input": "[1, 2, 3, 4, 5]", "expected": "5"},
            {"input": "[5, 4, 3, 2, 1]", "expected": "1"},
            {"input": "[3, 1, 4, 1, 5, 9, 2, 6]", "expected": "3"},
        ],
        category="s29_heldout_b",
    ),
    SynthesisTask(
        name="max_subarray_product",
        ghc_equivalent="max_subarray_product",
        lean_file="FormalNav/TasksS29Heldout.lean",
        theorem_name="maxSubarrayProduct",
        description="Maximum product of any contiguous subarray (dual tuple accumulator, signs interact)",
        test_cases=[
            {"input": "[2, 3, -2, 4]", "expected": "6"},
            {"input": "[-2, 0, -1]", "expected": "0"},
            {"input": "[2, -5, -2, -4, 3]", "expected": "24"},
            {"input": "[]", "expected": "0"},
            {"input": "[5]", "expected": "5"},
            {"input": "[-1, -2]", "expected": "2"},
            {"input": "[-2, 3, -4]", "expected": "24"},
        ],
        category="s29_heldout_b",
        return_type="Int",
    ),
    # ===== Tier C: Structural recursion, multi-arg =====
    SynthesisTask(
        name="merge_sorted_desc",
        ghc_equivalent="merge_sorted_desc",
        lean_file="FormalNav/TasksS29Heldout.lean",
        theorem_name="mergeSortedDesc",
        description="Merge two descending-sorted lists into a descending-sorted result",
        test_cases=[
            {"input": "[5, 3, 1]", "input2": "[4, 2]", "expected": "[5, 4, 3, 2, 1]"},
            {"input": "[]", "input2": "[3, 2, 1]", "expected": "[3, 2, 1]"},
            {"input": "[3, 2, 1]", "input2": "[]", "expected": "[3, 2, 1]"},
            {"input": "[]", "input2": "[]", "expected": "[]"},
            {"input": "[5]", "input2": "[3]", "expected": "[5, 3]"},
            {"input": "[5, 5]", "input2": "[5]", "expected": "[5, 5, 5]"},
            {"input": "[10, 5]", "input2": "[8, 3]", "expected": "[10, 8, 5, 3]"},
        ],
        category="s29_heldout_c",
        return_type="List Int",
        params_signature="(xs : List Int) (ys : List Int)",
    ),
    # ===== Tier D: Multi-arg + index arithmetic + compositional =====
    SynthesisTask(
        name="window_sums",
        ghc_equivalent="window_sums",
        lean_file="FormalNav/TasksS29Heldout.lean",
        theorem_name="windowSums",
        description="Sum of every contiguous k-element window ([] when k=0 or k>length)",
        test_cases=[
            {"input": "3", "input2": "[1, 2, 3, 4, 5]", "expected": "[6, 9, 12]"},
            {"input": "2", "input2": "[]", "expected": "[]"},
            {"input": "1", "input2": "[5]", "expected": "[5]"},
            {"input": "2", "input2": "[1, 2, 3]", "expected": "[3, 5]"},
            {"input": "5", "input2": "[1, 2, 3]", "expected": "[]"},
            {"input": "1", "input2": "[1, 2, 3, 4]", "expected": "[1, 2, 3, 4]"},
            {"input": "2", "input2": "[10, 20, 30]", "expected": "[30, 50]"},
        ],
        category="s29_heldout_d",
        return_type="List Int",
        params_signature="(k : Nat) (xs : List Int)",
    ),
]
