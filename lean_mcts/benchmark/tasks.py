"""
Benchmark tasks for Lean MCTS evaluation.

15 tasks:
  - 10 T2-equivalent (compositional): filter+fold, map+fold, pipelines, accumulators
  - 5 T3-equivalent (hard): multi-type, multi-arg, product return, multi-step, filterMap

Each task has:
  - A Lean type signature (the synthesis target)
  - Test cases (input → expected output)
  - A reference solution (for verification, not shown to the model)
  - The corresponding GHC Core task name (for cross-pipeline comparison)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SynthesisTask:
    """A program synthesis task in Lean 4."""
    name: str                       # Task identifier
    ghc_equivalent: str             # Corresponding GHC Core T2 task name
    lean_file: str                  # Relative path to .lean file
    theorem_name: str               # Lean def/theorem name (the sorry to fill)
    description: str                # Human-readable description
    test_cases: list[dict]          # [{"input": ..., "expected": ...}]
    category: str                   # filter_fold, map_fold, pipeline, accumulator, ...
    return_type: str = "Nat"        # Lean return type (for oracle #eval)
    input_type: str = "List Int"    # Lean input type (default for most tasks)
    params_signature: str | None = None  # Custom param signature override for oracle


# Tasks will be populated when the Lean project is set up (Phase 2).
# The task definitions live in FormalNav/Tasks.lean; this file loads them.

TASKS = [
    SynthesisTask(
        name="count_positive",
        ghc_equivalent="count_positive",
        lean_file="FormalNav/Tasks.lean",
        theorem_name="countPositive",
        description="Count positive integers in a list: List.length (List.filter (· > 0) xs)",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "2"},
            {"input": "[]", "expected": "0"},
            {"input": "[-1, -2, -3]", "expected": "0"},
            {"input": "[1, 2, 3]", "expected": "3"},
        ],
        category="filter_fold",
    ),
    SynthesisTask(
        name="map_square",
        ghc_equivalent="map_square",
        lean_file="FormalNav/Tasks.lean",
        theorem_name="mapSquare",
        description="Square each element: List.map (· ^ 2) xs",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "[1, 4, 9]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[-2, 0, 3]", "expected": "[4, 0, 9]"},
        ],
        category="map_fold",
        return_type="List Int",
    ),
    SynthesisTask(
        name="sort_desc",
        ghc_equivalent="sort_desc",
        lean_file="FormalNav/Tasks.lean",
        theorem_name="sortDesc",
        description="Sort a list in descending order: List.reverse (List.mergeSort xs)",
        test_cases=[
            {"input": "[3, 1, 4, 1, 5]", "expected": "[5, 4, 3, 1, 1]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1]", "expected": "[1]"},
        ],
        category="pipeline",
        return_type="List Int",
    ),
    SynthesisTask(
        name="sum_doubles",
        ghc_equivalent="sum_doubles",
        lean_file="FormalNav/Tasks.lean",
        theorem_name="sumDoubles",
        description="Sum of doubled elements: (List.map (· * 2) xs).foldl (· + ·) 0",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "12"},
            {"input": "[]", "expected": "0"},
            {"input": "[5]", "expected": "10"},
        ],
        category="map_fold",
        return_type="Int",
    ),
    SynthesisTask(
        name="max_abs",
        ghc_equivalent="max_abs",
        lean_file="FormalNav/Tasks.lean",
        theorem_name="maxAbs",
        description="Maximum absolute value: (List.map Int.natAbs xs).foldl max 0",
        test_cases=[
            {"input": "[3, -5, 2, -1]", "expected": "5"},
            {"input": "[]", "expected": "0"},
            {"input": "[-10]", "expected": "10"},
        ],
        category="map_fold",
    ),
    SynthesisTask(
        name="take_positives",
        ghc_equivalent="take_positives",
        lean_file="FormalNav/Tasks.lean",
        theorem_name="takePositives",
        description="Take elements while positive: List.takeWhile (· > 0) xs",
        test_cases=[
            {"input": "[3, 1, -2, 4]", "expected": "[3, 1]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[-1, 2, 3]", "expected": "[]"},
            {"input": "[1, 2, 3]", "expected": "[1, 2, 3]"},
        ],
        category="pipeline",
        return_type="List Int",
    ),
    SynthesisTask(
        name="nub_sort",
        ghc_equivalent="nub_sort",
        lean_file="FormalNav/Tasks.lean",
        theorem_name="nubSort",
        description="Remove duplicates then sort: List.mergeSort (List.eraseDups xs)",
        test_cases=[
            {"input": "[3, 1, 3, 2, 1]", "expected": "[1, 2, 3]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5, 5, 5]", "expected": "[5]"},
        ],
        category="pipeline",
        return_type="List Int",
    ),
    SynthesisTask(
        name="drop_negatives",
        ghc_equivalent="drop_negatives",
        lean_file="FormalNav/Tasks.lean",
        theorem_name="dropNegatives",
        description="Drop leading negatives: List.dropWhile (· < 0) xs",
        test_cases=[
            {"input": "[-3, -1, 2, -4]", "expected": "[2, -4]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1, -2, 3]", "expected": "[1, -2, 3]"},
            {"input": "[-1, -2, -3]", "expected": "[]"},
        ],
        category="pipeline",
        return_type="List Int",
    ),
    SynthesisTask(
        name="count_even",
        ghc_equivalent="count_even",
        lean_file="FormalNav/Tasks.lean",
        theorem_name="countEven",
        description="Count even numbers: List.length (List.filter (fun x => x % 2 == 0) xs)",
        test_cases=[
            {"input": "[1, 2, 3, 4, 5]", "expected": "2"},
            {"input": "[]", "expected": "0"},
            {"input": "[2, 4, 6]", "expected": "3"},
            {"input": "[1, 3, 5]", "expected": "0"},
        ],
        category="filter_fold",
    ),
    SynthesisTask(
        name="accum_sum",
        ghc_equivalent="accum_sum",
        lean_file="FormalNav/Tasks.lean",
        theorem_name="accumSum",
        description="Running sum (scanl): List.scanl (· + ·) 0 xs |>.tail!",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "[1, 3, 6]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[5]"},
        ],
        category="accumulator",
        return_type="List Int",
    ),
]


HARD_TASKS = [
    SynthesisTask(
        name="flatten",
        ghc_equivalent="flatten",
        lean_file="FormalNav/TasksHard.lean",
        theorem_name="flattenLists",
        description="Flatten a list of lists: List.flatten xss",
        test_cases=[
            {"input": "[[1, 2], [3], [4, 5]]", "expected": "[1, 2, 3, 4, 5]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[[], [1], []]", "expected": "[1]"},
        ],
        category="multi_type",
        return_type="List Int",
        input_type="List (List Int)",
    ),
    SynthesisTask(
        name="zip_sum",
        ghc_equivalent="zip_sum",
        lean_file="FormalNav/TasksHard.lean",
        theorem_name="zipSum",
        description="Elementwise sum of two lists: List.zipWith (· + ·) xs ys",
        test_cases=[
            {"input": "[1, 2, 3]", "input2": "[4, 5, 6]", "expected": "[5, 7, 9]"},
            {"input": "[]", "input2": "[]", "expected": "[]"},
            {"input": "[1, 2]", "input2": "[3]", "expected": "[4]"},
        ],
        category="multi_arg",
        return_type="List Int",
    ),
    SynthesisTask(
        name="partition_positive",
        ghc_equivalent="partition_positive",
        lean_file="FormalNav/TasksHard.lean",
        theorem_name="partitionPositive",
        description="Partition into positives and non-positives",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "([3, 4], [-1, -2, 0])"},
            {"input": "[]", "expected": "([], [])"},
            {"input": "[1, 2, 3]", "expected": "([1, 2, 3], [])"},
        ],
        category="product_return",
        return_type="List Int × List Int",
    ),
    SynthesisTask(
        name="dedup_count",
        ghc_equivalent="dedup_count",
        lean_file="FormalNav/TasksHard.lean",
        theorem_name="dedupCount",
        description="Deduplicate and count occurrences of each element",
        test_cases=[
            {"input": "[1, 2, 1, 3, 2, 1]", "expected": "[(1, 3), (2, 2), (3, 1)]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[(5, 1)]"},
        ],
        category="multi_step",
        return_type="List (Int × Nat)",
    ),
    SynthesisTask(
        name="filter_map_abs",
        ghc_equivalent="filter_map_abs",
        lean_file="FormalNav/TasksHard.lean",
        theorem_name="filterMapAbs",
        description="Keep only positive elements, cast to Nat",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "[3, 4]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[-1, -2, -3]", "expected": "[]"},
            {"input": "[1, 2, 3]", "expected": "[1, 2, 3]"},
        ],
        category="filtermap",
        return_type="List Nat",
    ),
]

V2_TASKS = [
    # --- Category A: Multi-Type Input ---
    SynthesisTask(
        name="concat_all",
        ghc_equivalent="concat_all",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="concatAll",
        description="Flatten a list of lists: xss.flatten",
        test_cases=[
            {"input": "[[1, 2], [3], [4, 5]]", "expected": "[1, 2, 3, 4, 5]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[[], [1], []]", "expected": "[1]"},
        ],
        category="v2_multi_type",
        return_type="List Int",
        input_type="List (List Int)",
    ),
    SynthesisTask(
        name="longest_sublist",
        ghc_equivalent="longest_sublist",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="longestSublist",
        description="Length of longest sublist",
        test_cases=[
            {"input": "[[1, 2], [3], [4, 5]]", "expected": "2"},
            {"input": "[]", "expected": "0"},
            {"input": "[[1, 2, 3]]", "expected": "3"},
        ],
        category="v2_multi_type",
        return_type="Nat",
        input_type="List (List Int)",
    ),
    SynthesisTask(
        name="merge_sorted",
        ghc_equivalent="merge_sorted",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="mergeSorted",
        description="Merge two lists and sort: List.mergeSort (xs ++ ys)",
        test_cases=[
            {"input": "[3, 1]", "input2": "[2, 4]", "expected": "[1, 2, 3, 4]"},
            {"input": "[]", "input2": "[]", "expected": "[]"},
            {"input": "[5]", "input2": "[1, 3]", "expected": "[1, 3, 5]"},
        ],
        category="v2_multi_type",
        return_type="List Int",
    ),
    SynthesisTask(
        name="dot_product",
        ghc_equivalent="dot_product",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="synthDotProduct",
        description="Dot product of two integer lists",
        test_cases=[
            {"input": "[1, 2, 3]", "input2": "[4, 5, 6]", "expected": "32"},
            {"input": "[]", "input2": "[]", "expected": "0"},
            {"input": "[1]", "input2": "[10]", "expected": "10"},
        ],
        category="v2_multi_type",
        return_type="Int",
    ),
    SynthesisTask(
        name="symmetric_diff",
        ghc_equivalent="symmetric_diff",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="symmetricDiff",
        description="Symmetric difference of two lists",
        test_cases=[
            {"input": "[1, 2, 3]", "input2": "[2, 3, 4]", "expected": "[1, 4]"},
            {"input": "[]", "input2": "[]", "expected": "[]"},
            {"input": "[1, 2]", "input2": "[3, 4]", "expected": "[1, 2, 3, 4]"},
        ],
        category="v2_multi_type",
        return_type="List Int",
    ),
    # --- Category B: Novel Compositions ---
    SynthesisTask(
        name="count_unique_positive",
        ghc_equivalent="count_unique_positive",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="countUniquePositive",
        description="Count unique positive elements: filter→dedup→length",
        test_cases=[
            {"input": "[3, -1, 3, 4, -2, 4]", "expected": "2"},
            {"input": "[]", "expected": "0"},
            {"input": "[1, 2, 3]", "expected": "3"},
            {"input": "[-1, -2, -3]", "expected": "0"},
        ],
        category="v2_novel",
    ),
    SynthesisTask(
        name="sum_sorted_positives",
        ghc_equivalent="sum_sorted_positives",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="sumSortedPositives",
        description="Sum of sorted positive elements: filter→sort→fold",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "7"},
            {"input": "[]", "expected": "0"},
            {"input": "[-1, -2, -3]", "expected": "0"},
            {"input": "[5, 3, 1]", "expected": "9"},
        ],
        category="v2_novel",
        return_type="Int",
    ),
    SynthesisTask(
        name="max_even",
        ghc_equivalent="max_even",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="maxEven",
        description="Maximum even element (0 if none): filter even→fold max",
        test_cases=[
            {"input": "[3, 4, -2, 6, -1]", "expected": "6"},
            {"input": "[]", "expected": "0"},
            {"input": "[1, 3, 5]", "expected": "0"},
            {"input": "[2, 4, 6]", "expected": "6"},
        ],
        category="v2_novel",
        return_type="Int",
    ),
    SynthesisTask(
        name="reverse_evens",
        ghc_equivalent="reverse_evens",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="reverseEvens",
        description="Reverse of even elements: filter even→reverse",
        test_cases=[
            {"input": "[1, 2, 3, 4, 5, 6]", "expected": "[6, 4, 2]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1, 3, 5]", "expected": "[]"},
        ],
        category="v2_novel",
        return_type="List Int",
    ),
    SynthesisTask(
        name="square_positives_sum",
        ghc_equivalent="square_positives_sum",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="squarePositivesSum",
        description="Sum of squares of positives: filter→map→fold (3-step)",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "25"},
            {"input": "[]", "expected": "0"},
            {"input": "[-1, -2]", "expected": "0"},
            {"input": "[1, 2, 3]", "expected": "14"},
        ],
        category="v2_novel",
        return_type="Int",
    ),
    # --- Category C: Multi-Step Decomposition ---
    SynthesisTask(
        name="partition_and_count",
        ghc_equivalent="partition_and_count",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="partitionAndCount",
        description="Count positives and non-positives (product type)",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "(2, 3)"},
            {"input": "[]", "expected": "(0, 0)"},
            {"input": "[1, 2, 3]", "expected": "(3, 0)"},
        ],
        category="v2_multi_step",
        return_type="Nat × Nat",
    ),
    SynthesisTask(
        name="normalize",
        ghc_equivalent="normalize",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="synthNormalize",
        description="Sort unique elements: eraseDups→mergeSort",
        test_cases=[
            {"input": "[3, 1, 3, 2, 1]", "expected": "[1, 2, 3]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5, 5, 5]", "expected": "[5]"},
        ],
        category="v2_multi_step",
        return_type="List Int",
    ),
    SynthesisTask(
        name="stats",
        ghc_equivalent="stats",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="stats",
        description="Min and max of list (product type)",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "(-2, 4)"},
            {"input": "[5]", "expected": "(0, 5)"},
            {"input": "[-3, -1]", "expected": "(-3, 0)"},
        ],
        category="v2_multi_step",
        return_type="Int × Int",
    ),
    SynthesisTask(
        name="split_apply",
        ghc_equivalent="split_apply",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="splitApply",
        description="Double each AND add 1 to each (product type)",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "([2, 4, 6], [2, 3, 4])"},
            {"input": "[]", "expected": "([], [])"},
            {"input": "[0]", "expected": "([0], [1])"},
        ],
        category="v2_multi_step",
        return_type="List Int × List Int",
    ),
    SynthesisTask(
        name="filter_then_stats",
        ghc_equivalent="filter_then_stats",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="filterThenStats",
        description="Count and sum of positives (product type)",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "(2, 7)"},
            {"input": "[]", "expected": "(0, 0)"},
            {"input": "[1, 2, 3]", "expected": "(3, 6)"},
        ],
        category="v2_multi_step",
        return_type="Nat × Int",
    ),
    # --- Category D: Algorithmic ---
    SynthesisTask(
        name="prefix_sums",
        ghc_equivalent="prefix_sums",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="prefixSums",
        description="Prefix sums: scanl (+) 0 |>.drop 1",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "[1, 3, 6]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[5]"},
        ],
        category="v2_algorithmic",
        return_type="List Int",
    ),
    SynthesisTask(
        name="running_max",
        ghc_equivalent="running_max",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="runningMax",
        description="Running maximum: scanl max 0 |>.drop 1",
        test_cases=[
            {"input": "[3, 1, 4, 1, 5]", "expected": "[3, 3, 4, 4, 5]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1, 2, 3]", "expected": "[1, 2, 3]"},
        ],
        category="v2_algorithmic",
        return_type="List Int",
    ),
    SynthesisTask(
        name="first_n_squares",
        ghc_equivalent="first_n_squares",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="firstNSquares",
        description="First N squares: List.range n |>.map (· ^ 2)",
        test_cases=[
            {"input": "5", "expected": "[0, 1, 4, 9, 16]"},
            {"input": "0", "expected": "[]"},
            {"input": "3", "expected": "[0, 1, 4]"},
        ],
        category="v2_algorithmic",
        return_type="List Nat",
        input_type="Nat",
    ),
    SynthesisTask(
        name="every_other",
        ghc_equivalent="every_other",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="everyOther",
        description="Every other element (even indices) via zipIdx",
        test_cases=[
            {"input": "[10, 20, 30, 40, 50]", "expected": "[10, 30, 50]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1]", "expected": "[1]"},
        ],
        category="v2_algorithmic",
        return_type="List Int",
    ),
    SynthesisTask(
        name="pairwise_sums",
        ghc_equivalent="pairwise_sums",
        lean_file="FormalNav/TasksV2.lean",
        theorem_name="pairwiseSums",
        description="Pairwise sums of adjacent elements: zip + tail + map",
        test_cases=[
            {"input": "[1, 2, 3, 4]", "expected": "[3, 5, 7]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[]"},
        ],
        category="v2_algorithmic",
        return_type="List Int",
    ),
]

RECURRENCE_TASKS = [
    SynthesisTask(
        name="fibonacci",
        ghc_equivalent="fibonacci",
        lean_file="FormalNav/TasksRecurrence.lean",
        theorem_name="fibonacci",
        description="Nth Fibonacci number via tuple-accumulator fold",
        test_cases=[
            {"input": "0", "expected": "0"},
            {"input": "1", "expected": "1"},
            {"input": "7", "expected": "13"},
            {"input": "10", "expected": "55"},
        ],
        category="recurrence",
        return_type="Nat",
        input_type="Nat",
    ),
    SynthesisTask(
        name="fib_list",
        ghc_equivalent="fib_list",
        lean_file="FormalNav/TasksRecurrence.lean",
        theorem_name="fibList",
        description="First n Fibonacci numbers via tuple-accumulator fold",
        test_cases=[
            {"input": "0", "expected": "[]"},
            {"input": "1", "expected": "[0]"},
            {"input": "7", "expected": "[0, 1, 1, 2, 3, 5, 8]"},
        ],
        category="recurrence",
        return_type="List Nat",
        input_type="Nat",
    ),
    SynthesisTask(
        name="factorial",
        ghc_equivalent="factorial",
        lean_file="FormalNav/TasksRecurrence.lean",
        theorem_name="factorial",
        description="Factorial via indexed accumulator fold",
        test_cases=[
            {"input": "0", "expected": "1"},
            {"input": "1", "expected": "1"},
            {"input": "5", "expected": "120"},
        ],
        category="recurrence",
        return_type="Nat",
        input_type="Nat",
    ),
    SynthesisTask(
        name="triangular_numbers",
        ghc_equivalent="triangular_numbers",
        lean_file="FormalNav/TasksRecurrence.lean",
        theorem_name="triangularNumbers",
        description="First n triangular numbers via nested range fold",
        test_cases=[
            {"input": "0", "expected": "[]"},
            {"input": "1", "expected": "[0]"},
            {"input": "5", "expected": "[0, 1, 3, 6, 10]"},
        ],
        category="recurrence",
        return_type="List Nat",
        input_type="Nat",
    ),
    SynthesisTask(
        name="running_parts",
        ghc_equivalent="running_parts",
        lean_file="FormalNav/TasksRecurrence.lean",
        theorem_name="runningParts",
        description="Running sum and count via tuple-accumulator fold on list",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "(6, 3)"},
            {"input": "[]", "expected": "(0, 0)"},
            {"input": "[5]", "expected": "(5, 1)"},
        ],
        category="recurrence",
        return_type="Int × Nat",
    ),
    SynthesisTask(
        name="powers_of_2",
        ghc_equivalent="powers_of_2",
        lean_file="FormalNav/TasksRecurrence.lean",
        theorem_name="powersOf2",
        description="First n powers of 2 via nested range fold",
        test_cases=[
            {"input": "0", "expected": "[]"},
            {"input": "1", "expected": "[1]"},
            {"input": "5", "expected": "[1, 2, 4, 8, 16]"},
        ],
        category="recurrence",
        return_type="List Nat",
        input_type="Nat",
    ),
]

FUNDAMENTALS_TASKS = [
    SynthesisTask(
        name="stutter",
        ghc_equivalent="stutter",
        lean_file="FormalNav/TasksFundamentals.lean",
        theorem_name="stutter",
        description="Duplicate each element: [1,2,3] → [1,1,2,2,3,3]",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "[1, 1, 2, 2, 3, 3]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[5, 5]"},
            {"input": "[-1, 0]", "expected": "[-1, -1, 0, 0]"},
        ],
        category="fundamentals",
        return_type="List Int",
    ),
    SynthesisTask(
        name="alternate_sign",
        ghc_equivalent="alternate_sign",
        lean_file="FormalNav/TasksFundamentals.lean",
        theorem_name="alternateSign",
        description="Alternate signs: [1,2,3,4] → [1,-2,3,-4]",
        test_cases=[
            {"input": "[1, 2, 3, 4]", "expected": "[1, -2, 3, -4]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[5]"},
            {"input": "[1, 1, 1]", "expected": "[1, -1, 1]"},
        ],
        category="fundamentals",
        return_type="List Int",
    ),
    SynthesisTask(
        name="pair_diff",
        ghc_equivalent="pair_diff",
        lean_file="FormalNav/TasksFundamentals.lean",
        theorem_name="pairDiff",
        description="Consecutive differences: [5,3,1,4] → [2,2,-3]",
        test_cases=[
            {"input": "[5, 3, 1, 4]", "expected": "[2, 2, -3]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[]"},
            {"input": "[10, 7]", "expected": "[3]"},
        ],
        category="fundamentals",
        return_type="List Int",
    ),
    SynthesisTask(
        name="second_max",
        ghc_equivalent="second_max",
        lean_file="FormalNav/TasksFundamentals.lean",
        theorem_name="secondMax",
        description="Second largest element via tuple-state fold",
        test_cases=[
            {"input": "[3, 1, 4, 1, 5]", "expected": "4"},
            {"input": "[1, 2]", "expected": "1"},
            {"input": "[5]", "expected": "0"},
            {"input": "[3, 3, 3]", "expected": "3"},
        ],
        category="fundamentals",
        return_type="Int",
    ),
    SynthesisTask(
        name="match_head_or_sum",
        ghc_equivalent="match_head_or_sum",
        lean_file="FormalNav/TasksFundamentals.lean",
        theorem_name="matchHeadOrSum",
        description="Head if list <= 2 elements, sum if longer",
        test_cases=[
            {"input": "[5]", "expected": "5"},
            {"input": "[1, 2, 3, 4]", "expected": "10"},
            {"input": "[]", "expected": "0"},
            {"input": "[3, 7]", "expected": "3"},
        ],
        category="fundamentals",
        return_type="Int",
    ),
    SynthesisTask(
        name="all_even",
        ghc_equivalent="all_even",
        lean_file="FormalNav/TasksFundamentals.lean",
        theorem_name="allEven",
        description="1 if all elements even, 0 otherwise (List.rec + if)",
        test_cases=[
            {"input": "[2, 4, 6]", "expected": "1"},
            {"input": "[2, 3, 6]", "expected": "0"},
            {"input": "[]", "expected": "1"},
            {"input": "[0]", "expected": "1"},
        ],
        category="fundamentals",
        return_type="Nat",
    ),
    SynthesisTask(
        name="first_neg_idx",
        ghc_equivalent="first_neg_idx",
        lean_file="FormalNav/TasksFundamentals.lean",
        theorem_name="firstNegIdx",
        description="Index of first negative element (stateful fold)",
        test_cases=[
            {"input": "[3, 1, -4, 2]", "expected": "2"},
            {"input": "[3, 1, 4]", "expected": "3"},
            {"input": "[]", "expected": "0"},
            {"input": "[-1, 2, 3]", "expected": "0"},
        ],
        category="fundamentals",
        return_type="Nat",
    ),
    SynthesisTask(
        name="cumul_max",
        ghc_equivalent="cumul_max",
        lean_file="FormalNav/TasksFundamentals.lean",
        theorem_name="cumulMax",
        description="Cumulative maximum: [3,1,4,1,5] → [3,3,4,4,5]",
        test_cases=[
            {"input": "[3, 1, 4, 1, 5]", "expected": "[3, 3, 4, 4, 5]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[5]"},
            {"input": "[1, 2, 3]", "expected": "[1, 2, 3]"},
        ],
        category="fundamentals",
        return_type="List Int",
    ),
    SynthesisTask(
        name="rle",
        ghc_equivalent="rle",
        lean_file="FormalNav/TasksFundamentals.lean",
        theorem_name="rle",
        description="Run-length encode: [1,1,2,3,3,3] → [(1,2),(2,1),(3,3)]",
        test_cases=[
            {"input": "[1, 1, 2, 3, 3, 3]", "expected": "[(1, 2), (2, 1), (3, 3)]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[(5, 1)]"},
            {"input": "[1, 2, 3]", "expected": "[(1, 1), (2, 1), (3, 1)]"},
        ],
        category="fundamentals",
        return_type="List (Int × Nat)",
    ),
    SynthesisTask(
        name="interleave",
        ghc_equivalent="interleave",
        lean_file="FormalNav/TasksFundamentals.lean",
        theorem_name="interleave",
        description="Interleave two lists: [1,2,3] [10,20,30] → [1,10,2,20,3,30]",
        test_cases=[
            {"input": "[1, 2, 3]", "input2": "[10, 20, 30]", "expected": "[1, 10, 2, 20, 3, 30]"},
            {"input": "[]", "input2": "[]", "expected": "[]"},
            {"input": "[1]", "input2": "[2, 3]", "expected": "[1, 2, 3]"},
            {"input": "[1, 2]", "input2": "[]", "expected": "[1, 2]"},
        ],
        category="fundamentals",
        return_type="List Int",
        input_type="List Int, List Int",
    ),
]

MULTISTEP_TASKS = [
    SynthesisTask(
        name="sum_and_product",
        ghc_equivalent="sum_and_product",
        lean_file="FormalNav/TasksMultiStep.lean",
        theorem_name="sumAndProduct",
        description="Sum and product of list elements (constructor + two folds)",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "(6, 6)"},
            {"input": "[]", "expected": "(0, 1)"},
            {"input": "[2, -3, 4]", "expected": "(3, -24)"},
            {"input": "[5]", "expected": "(5, 5)"},
        ],
        category="multistep",
        return_type="Int × Int",
    ),
    SynthesisTask(
        name="count_pos_neg",
        ghc_equivalent="count_pos_neg",
        lean_file="FormalNav/TasksMultiStep.lean",
        theorem_name="countPosNeg",
        description="Count positives and negatives (different predicates per component)",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "(2, 2)"},
            {"input": "[]", "expected": "(0, 0)"},
            {"input": "[1, 2, 3]", "expected": "(3, 0)"},
            {"input": "[-1, -2]", "expected": "(0, 2)"},
        ],
        category="multistep",
        return_type="Nat × Nat",
    ),
    SynthesisTask(
        name="split_even_odd",
        ghc_equivalent="split_even_odd",
        lean_file="FormalNav/TasksMultiStep.lean",
        theorem_name="splitEvenOdd",
        description="Split into even and odd elements (different filters)",
        test_cases=[
            {"input": "[1, 2, 3, 4, 5, 6]", "expected": "([2, 4, 6], [1, 3, 5])"},
            {"input": "[]", "expected": "([], [])"},
            {"input": "[2, 4]", "expected": "([2, 4], [])"},
            {"input": "[1, 3]", "expected": "([], [1, 3])"},
        ],
        category="multistep",
        return_type="List Int × List Int",
    ),
    SynthesisTask(
        name="min_and_max",
        ghc_equivalent="min_and_max",
        lean_file="FormalNav/TasksMultiStep.lean",
        theorem_name="minAndMax",
        description="Minimum and maximum via different folds",
        test_cases=[
            {"input": "[3, 1, 4, 1, 5]", "expected": "(0, 5)"},
            {"input": "[]", "expected": "(0, 0)"},
            {"input": "[-2, 3, -1]", "expected": "(-2, 3)"},
            {"input": "[7]", "expected": "(0, 7)"},
        ],
        category="multistep",
        return_type="Int × Int",
    ),
    SynthesisTask(
        name="length_and_sum",
        ghc_equivalent="length_and_sum",
        lean_file="FormalNav/TasksMultiStep.lean",
        theorem_name="lengthAndSum",
        description="Length and sum (different return types per component)",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "(3, 6)"},
            {"input": "[]", "expected": "(0, 0)"},
            {"input": "[10]", "expected": "(1, 10)"},
            {"input": "[-1, -2, 3]", "expected": "(3, 0)"},
        ],
        category="multistep",
        return_type="Nat × Int",
    ),
]

DEEP_MULTISTEP_TASKS = [
    SynthesisTask(
        name="sum_count_max",
        ghc_equivalent="sum_count_max",
        lean_file="FormalNav/TasksDeepMultiStep.lean",
        theorem_name="sumCountMax",
        description="Sum, count, max via depth-3 nested product (Int × Nat) × Int",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "((6, 3), 3)"},
            {"input": "[]", "expected": "((0, 0), 0)"},
            {"input": "[-1, 5, -2]", "expected": "((2, 3), 5)"},
            {"input": "[10]", "expected": "((10, 1), 10)"},
        ],
        category="deep_multistep",
        return_type="(Int × Nat) × Int",
    ),
    SynthesisTask(
        name="stats_triple",
        ghc_equivalent="stats_triple",
        lean_file="FormalNav/TasksDeepMultiStep.lean",
        theorem_name="statsTriple",
        description="Min, max, count via depth-3 nested product (Int × Int) × Nat",
        test_cases=[
            {"input": "[3, 1, 4, 1, 5]", "expected": "((0, 5), 5)"},
            {"input": "[]", "expected": "((0, 0), 0)"},
            {"input": "[-2, 3, -1]", "expected": "((-2, 3), 3)"},
        ],
        category="deep_multistep",
        return_type="(Int × Int) × Nat",
    ),
    SynthesisTask(
        name="partition_lengths",
        ghc_equivalent="partition_lengths",
        lean_file="FormalNav/TasksDeepMultiStep.lean",
        theorem_name="partitionLengths",
        description="Positive count, negative count, total length via depth-3",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "((2, 2), 5)"},
            {"input": "[]", "expected": "((0, 0), 0)"},
            {"input": "[1, 2, 3]", "expected": "((3, 0), 3)"},
            {"input": "[-1, -2]", "expected": "((0, 2), 2)"},
        ],
        category="deep_multistep",
        return_type="(Nat × Nat) × Nat",
    ),
    SynthesisTask(
        name="fold_and_length",
        ghc_equivalent="fold_and_length",
        lean_file="FormalNav/TasksDeepMultiStep.lean",
        theorem_name="foldAndLength",
        description="Sum + length via depth-2 (fold + non-fold)",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "(6, 3)"},
            {"input": "[]", "expected": "(0, 0)"},
            {"input": "[10]", "expected": "(10, 1)"},
            {"input": "[-1, -2, 3]", "expected": "(0, 3)"},
        ],
        category="deep_multistep",
        return_type="Int × Nat",
    ),
    SynthesisTask(
        name="filter_both_ways",
        ghc_equivalent="filter_both_ways",
        lean_file="FormalNav/TasksDeepMultiStep.lean",
        theorem_name="filterBothWays",
        description="Filter positives and negatives (related predicates)",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "([3, 4], [-1, -2])"},
            {"input": "[]", "expected": "([], [])"},
            {"input": "[1, 2, 3]", "expected": "([1, 2, 3], [])"},
            {"input": "[-1, -2]", "expected": "([], [-1, -2])"},
        ],
        category="deep_multistep",
        return_type="List Int × List Int",
    ),
    SynthesisTask(
        name="recurrence_and_sum",
        ghc_equivalent="recurrence_and_sum",
        lean_file="FormalNav/TasksDeepMultiStep.lean",
        theorem_name="recurrenceAndSum",
        description="Tuple-fold projection + simple length (recurrence in multi-step)",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "(6, 3)"},
            {"input": "[]", "expected": "(0, 0)"},
            {"input": "[5]", "expected": "(5, 1)"},
        ],
        category="deep_multistep",
        return_type="Int × Nat",
    ),
]

COMPOSITIONAL_TASKS = [
    SynthesisTask(
        name="sum_of_squares_comp",
        ghc_equivalent="sum_of_squares_comp",
        lean_file="FormalNav/TasksCompositional.lean",
        theorem_name="sumOfSquaresComp",
        description="Sum of squares (baseline): (xs.map square).foldl (+) 0",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "14"},
            {"input": "[]", "expected": "0"},
            {"input": "[-2, 0, 3]", "expected": "13"},
            {"input": "[1]", "expected": "1"},
        ],
        category="compositional",
        return_type="Int",
    ),
    SynthesisTask(
        name="range_sum",
        ghc_equivalent="range_sum",
        lean_file="FormalNav/TasksCompositional.lean",
        theorem_name="rangeSum",
        description="Sum of 0..n-1 via List.range bridge (cross-type)",
        test_cases=[
            {"input": "5", "expected": "10"},
            {"input": "0", "expected": "0"},
            {"input": "1", "expected": "0"},
            {"input": "10", "expected": "45"},
        ],
        category="compositional",
        return_type="Nat",
        input_type="Nat",
    ),
    SynthesisTask(
        name="count_unique",
        ghc_equivalent="count_unique",
        lean_file="FormalNav/TasksCompositional.lean",
        theorem_name="countUnique",
        description="Count unique elements: eraseDups + length",
        test_cases=[
            {"input": "[3, 1, 3, 2, 1]", "expected": "3"},
            {"input": "[]", "expected": "0"},
            {"input": "[5, 5, 5]", "expected": "1"},
            {"input": "[1, 2, 3]", "expected": "3"},
        ],
        category="compositional",
    ),
    SynthesisTask(
        name="reverse_filter",
        ghc_equivalent="reverse_filter",
        lean_file="FormalNav/TasksCompositional.lean",
        theorem_name="reverseFilter",
        description="Reverse of positives: filter + reverse",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "[4, 3]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[-1, -2]", "expected": "[]"},
            {"input": "[1, 2, 3]", "expected": "[3, 2, 1]"},
        ],
        category="compositional",
        return_type="List Int",
    ),
    SynthesisTask(
        name="sum_positives_squared",
        ghc_equivalent="sum_positives_squared",
        lean_file="FormalNav/TasksCompositional.lean",
        theorem_name="sumPositivesSquared",
        description="Sum of squares of positives: filter → map → fold (3-step)",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "25"},
            {"input": "[]", "expected": "0"},
            {"input": "[-1, -2]", "expected": "0"},
            {"input": "[1, 2, 3]", "expected": "14"},
        ],
        category="compositional",
        return_type="Int",
    ),
    SynthesisTask(
        name="max_abs_comp",
        ghc_equivalent="max_abs_comp",
        lean_file="FormalNav/TasksCompositional.lean",
        theorem_name="maxAbsComp",
        description="Maximum absolute value: map natAbs + fold max",
        test_cases=[
            {"input": "[3, -5, 2, -1]", "expected": "5"},
            {"input": "[]", "expected": "0"},
            {"input": "[-10]", "expected": "10"},
            {"input": "[1, 2, 3]", "expected": "3"},
        ],
        category="compositional",
    ),
    SynthesisTask(
        name="sorted_positives",
        ghc_equivalent="sorted_positives",
        lean_file="FormalNav/TasksCompositional.lean",
        theorem_name="sortedPositives",
        description="Sort positive elements: filter + mergeSort",
        test_cases=[
            {"input": "[3, -1, 4, -2, 1]", "expected": "[1, 3, 4]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[-1, -2]", "expected": "[]"},
            {"input": "[5, 2, 3]", "expected": "[2, 3, 5]"},
        ],
        category="compositional",
        return_type="List Int",
    ),
    SynthesisTask(
        name="length_evens",
        ghc_equivalent="length_evens",
        lean_file="FormalNav/TasksCompositional.lean",
        theorem_name="lengthEvens",
        description="Count even elements: filter even + length",
        test_cases=[
            {"input": "[1, 2, 3, 4, 5]", "expected": "2"},
            {"input": "[]", "expected": "0"},
            {"input": "[2, 4, 6]", "expected": "3"},
            {"input": "[1, 3, 5]", "expected": "0"},
        ],
        category="compositional",
    ),
]

RECURSIVE_R1_TASKS = [
    SynthesisTask(
        name="sum_positive",
        ghc_equivalent="sum_positive",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="sumPositive",
        description="Sum only positive elements via List.rec",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "7"},
            {"input": "[]", "expected": "0"},
            {"input": "[-1, -2, -3]", "expected": "0"},
            {"input": "[1, 2, 3]", "expected": "6"},
        ],
        category="recursive_r1",
        return_type="Int",
    ),
    SynthesisTask(
        name="count_negative",
        ghc_equivalent="count_negative",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="countNegative",
        description="Count elements less than 0 via List.rec",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "2"},
            {"input": "[]", "expected": "0"},
            {"input": "[1, 2, 3]", "expected": "0"},
            {"input": "[-1, -2, -3]", "expected": "3"},
        ],
        category="recursive_r1",
        return_type="Nat",
    ),
    SynthesisTask(
        name="first_or_zero",
        ghc_equivalent="first_or_zero",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="firstOrZero",
        description="Return first element or 0 if empty via List.rec",
        test_cases=[
            {"input": "[3, -1, 4]", "expected": "3"},
            {"input": "[]", "expected": "0"},
            {"input": "[7]", "expected": "7"},
            {"input": "[-5, 2, 3]", "expected": "-5"},
        ],
        category="recursive_r1",
        return_type="Int",
    ),
    SynthesisTask(
        name="product_abs",
        ghc_equivalent="product_abs",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="productAbs",
        description="Product of absolute values via List.rec",
        test_cases=[
            {"input": "[2, -3, 4]", "expected": "24"},
            {"input": "[]", "expected": "1"},
            {"input": "[5]", "expected": "5"},
            {"input": "[-1, -2, -3]", "expected": "6"},
        ],
        category="recursive_r1",
        return_type="Int",
    ),
    SynthesisTask(
        name="max_or_zero",
        ghc_equivalent="max_or_zero",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="maxOrZero",
        description="Maximum element or 0 if empty via List.rec",
        test_cases=[
            {"input": "[3, -1, 4, -2, 5]", "expected": "5"},
            {"input": "[]", "expected": "0"},
            {"input": "[-3, -1, -5]", "expected": "0"},
            {"input": "[7]", "expected": "7"},
        ],
        category="recursive_r1",
        return_type="Int",
    ),
]

RECURSIVE_R2_TASKS = [
    SynthesisTask(
        name="keep_positive",
        ghc_equivalent="keep_positive",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="keepPositive",
        description="Keep only positive elements via List.rec",
        test_cases=[
            {"input": "[3, -1, 4, -2, 0]", "expected": "[3, 4]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[-1, -2]", "expected": "[]"},
            {"input": "[1, 2, 3]", "expected": "[1, 2, 3]"},
        ],
        category="recursive_r2",
        return_type="List Int",
    ),
    SynthesisTask(
        name="stutter_rec",
        ghc_equivalent="stutter_rec",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="stutterRec",
        description="Duplicate each element [1,2,3] → [1,1,2,2,3,3] via List.rec",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "[1, 1, 2, 2, 3, 3]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[5, 5]"},
            {"input": "[-1, 0]", "expected": "[-1, -1, 0, 0]"},
        ],
        category="recursive_r2",
        return_type="List Int",
    ),
    SynthesisTask(
        name="negate_all",
        ghc_equivalent="negate_all",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="negateAll",
        description="Negate each element via List.rec",
        test_cases=[
            {"input": "[1, -2, 3]", "expected": "[-1, 2, -3]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[0]", "expected": "[0]"},
            {"input": "[-5, 5]", "expected": "[5, -5]"},
        ],
        category="recursive_r2",
        return_type="List Int",
    ),
    SynthesisTask(
        name="square_all",
        ghc_equivalent="square_all",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="squareAll",
        description="Square each element via List.rec",
        test_cases=[
            {"input": "[1, -2, 3]", "expected": "[1, 4, 9]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[0, 5]", "expected": "[0, 25]"},
            {"input": "[-3]", "expected": "[9]"},
        ],
        category="recursive_r2",
        return_type="List Int",
    ),
    SynthesisTask(
        name="keep_even",
        ghc_equivalent="keep_even",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="keepEven",
        description="Keep only even elements via List.rec",
        test_cases=[
            {"input": "[1, 2, 3, 4, 5, 6]", "expected": "[2, 4, 6]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1, 3, 5]", "expected": "[]"},
            {"input": "[2, 4]", "expected": "[2, 4]"},
        ],
        category="recursive_r2",
        return_type="List Int",
    ),
]

RECURSIVE_R3_TASKS = [
    SynthesisTask(
        name="factorial_rec",
        ghc_equivalent="factorial_rec",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="factorialRec",
        description="Factorial via Nat.rec",
        test_cases=[
            {"input": "5", "expected": "120"},
            {"input": "0", "expected": "1"},
            {"input": "1", "expected": "1"},
            {"input": "7", "expected": "5040"},
        ],
        category="recursive_r3",
        return_type="Nat",
        input_type="Nat",
    ),
    SynthesisTask(
        name="triangular_rec",
        ghc_equivalent="triangular_rec",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="triangularRec",
        description="Triangular number 1+2+...+n via Nat.rec",
        test_cases=[
            {"input": "5", "expected": "15"},
            {"input": "0", "expected": "0"},
            {"input": "1", "expected": "1"},
            {"input": "10", "expected": "55"},
        ],
        category="recursive_r3",
        return_type="Nat",
        input_type="Nat",
    ),
    SynthesisTask(
        name="power_of_two",
        ghc_equivalent="power_of_two",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="powerOfTwo",
        description="2^n via Nat.rec",
        test_cases=[
            {"input": "10", "expected": "1024"},
            {"input": "0", "expected": "1"},
            {"input": "1", "expected": "2"},
            {"input": "5", "expected": "32"},
        ],
        category="recursive_r3",
        return_type="Nat",
        input_type="Nat",
    ),
    SynthesisTask(
        name="square_nat",
        ghc_equivalent="square_nat",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="squareNat",
        description="n^2 via repeated odd-number addition (Nat.rec)",
        test_cases=[
            {"input": "5", "expected": "25"},
            {"input": "0", "expected": "0"},
            {"input": "1", "expected": "1"},
            {"input": "7", "expected": "49"},
        ],
        category="recursive_r3",
        return_type="Nat",
        input_type="Nat",
    ),
    SynthesisTask(
        name="count_down_sum",
        ghc_equivalent="count_down_sum",
        lean_file="FormalNav/TasksRecursivePrograms.lean",
        theorem_name="countDownSum",
        description="Sum 1+2+...+n via Nat.rec (alternate to triangular)",
        test_cases=[
            {"input": "5", "expected": "15"},
            {"input": "0", "expected": "0"},
            {"input": "1", "expected": "1"},
            {"input": "100", "expected": "5050"},
        ],
        category="recursive_r3",
        return_type="Nat",
        input_type="Nat",
    ),
]

RECURSIVE_TASKS = RECURSIVE_R1_TASKS + RECURSIVE_R2_TASKS + RECURSIVE_R3_TASKS

STRETCH_S1_TASKS = [
    SynthesisTask(
        name="alternating_sum",
        ghc_equivalent="alternating_sum",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="alternatingSum",
        description="Alternating sum: 1-2+3-4 = -2 (novel right-fold step)",
        test_cases=[
            {"input": "[1, 2, 3, 4]", "expected": "-2"},
            {"input": "[]", "expected": "0"},
            {"input": "[5]", "expected": "5"},
            {"input": "[1, -1, 1, -1]", "expected": "4"},
        ],
        category="stretch_s1",
        return_type="Int",
    ),
    SynthesisTask(
        name="sum_even_only",
        ghc_equivalent="sum_even_only",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="sumEvenOnly",
        description="Sum only even elements (novel conditional step)",
        test_cases=[
            {"input": "[1, 2, 3, 4, 5, 6]", "expected": "12"},
            {"input": "[]", "expected": "0"},
            {"input": "[1, 3, 5]", "expected": "0"},
            {"input": "[2, 4]", "expected": "6"},
        ],
        category="stretch_s1",
        return_type="Int",
    ),
    SynthesisTask(
        name="product_positive",
        ghc_equivalent="product_positive",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="productPositive",
        description="Product of positive elements only (novel conditional multiply)",
        test_cases=[
            {"input": "[2, -3, 4]", "expected": "8"},
            {"input": "[]", "expected": "1"},
            {"input": "[-1, -2]", "expected": "1"},
            {"input": "[3, 5]", "expected": "15"},
        ],
        category="stretch_s1",
        return_type="Int",
    ),
    SynthesisTask(
        name="abs_of_negatives",
        ghc_equivalent="abs_of_negatives",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="absOfNegatives",
        description="Absolute values of negative elements only (filter+transform)",
        test_cases=[
            {"input": "[-3, 1, -5, 2]", "expected": "[3, 5]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1, 2, 3]", "expected": "[]"},
            {"input": "[-1, -2, -3]", "expected": "[1, 2, 3]"},
        ],
        category="stretch_s1",
        return_type="List Int",
    ),
    SynthesisTask(
        name="sum_cubes",
        ghc_equivalent="sum_cubes",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="sumCubes",
        description="Sum of cubes 1^3 + 2^3 + ... + n^3 (novel Nat.rec step)",
        test_cases=[
            {"input": "3", "expected": "36"},
            {"input": "0", "expected": "0"},
            {"input": "1", "expected": "1"},
            {"input": "5", "expected": "225"},
        ],
        category="stretch_s1",
        return_type="Nat",
        input_type="Nat",
    ),
]

STRETCH_S2_TASKS = [
    SynthesisTask(
        name="last_or_default",
        ghc_equivalent="last_or_default",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="lastOrDefault",
        description="Last element or 0 (requires foldl, not List.rec right-fold)",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "3"},
            {"input": "[]", "expected": "0"},
            {"input": "[5]", "expected": "5"},
            {"input": "[-1, -2, -3]", "expected": "-3"},
        ],
        category="stretch_s2",
        return_type="Int",
    ),
    SynthesisTask(
        name="double_positives",
        ghc_equivalent="double_positives",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="doublePositives",
        description="Double positive elements, keep negatives unchanged",
        test_cases=[
            {"input": "[-1, 2, -3, 4]", "expected": "[-1, 4, -3, 8]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1, 2, 3]", "expected": "[2, 4, 6]"},
            {"input": "[-1, -2]", "expected": "[-1, -2]"},
        ],
        category="stretch_s2",
        return_type="List Int",
    ),
    SynthesisTask(
        name="clamp_values",
        ghc_equivalent="clamp_values",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="clampValues",
        description="Clamp each element to [-5, 5] range",
        test_cases=[
            {"input": "[7, -8, 3, -2, 15]", "expected": "[5, -5, 3, -2, 5]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[0]", "expected": "[0]"},
            {"input": "[-3, 3]", "expected": "[-3, 3]"},
        ],
        category="stretch_s2",
        return_type="List Int",
    ),
    SynthesisTask(
        name="replace_neg_zero",
        ghc_equivalent="replace_neg_zero",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="replaceNegZero",
        description="Replace negatives with zero, keep non-negatives",
        test_cases=[
            {"input": "[-1, 2, -3, 4, 0]", "expected": "[0, 2, 0, 4, 0]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1, 2, 3]", "expected": "[1, 2, 3]"},
            {"input": "[-1, -2]", "expected": "[0, 0]"},
        ],
        category="stretch_s2",
        return_type="List Int",
    ),
    SynthesisTask(
        name="negate_sum",
        ghc_equivalent="negate_sum",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="negateSum",
        description="Negate the sum of all elements",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "-6"},
            {"input": "[]", "expected": "0"},
            {"input": "[-1, -2]", "expected": "3"},
            {"input": "[5]", "expected": "-5"},
        ],
        category="stretch_s2",
        return_type="Int",
    ),
]

STRETCH_S3_TASKS = [
    SynthesisTask(
        name="deviation_from_max",
        ghc_equivalent="deviation_from_max",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="deviationFromMax",
        description="Each element minus the maximum (multi-pass: max then map)",
        test_cases=[
            {"input": "[3, 1, 4, 1, 5]", "expected": "[-2, -4, -1, -4, 0]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[0]"},
            {"input": "[1, 1, 1]", "expected": "[0, 0, 0]"},
        ],
        category="stretch_s3",
        return_type="List Int",
    ),
    SynthesisTask(
        name="count_distinct",
        ghc_equivalent="count_distinct",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="countDistinct",
        description="Count unique elements (eraseDups + length composition)",
        test_cases=[
            {"input": "[1, 2, 2, 3, 1]", "expected": "3"},
            {"input": "[]", "expected": "0"},
            {"input": "[1, 1, 1]", "expected": "1"},
            {"input": "[1, 2, 3]", "expected": "3"},
        ],
        category="stretch_s3",
    ),
    SynthesisTask(
        name="is_sorted_asc",
        ghc_equivalent="is_sorted_asc",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="isSortedAsc",
        description="1 if list is sorted ascending, 0 otherwise",
        test_cases=[
            {"input": "[1, 2, 3, 4]", "expected": "1"},
            {"input": "[1, 3, 2]", "expected": "0"},
            {"input": "[]", "expected": "1"},
            {"input": "[5]", "expected": "1"},
        ],
        category="stretch_s3",
    ),
    SynthesisTask(
        name="max_min_diff",
        ghc_equivalent="max_min_diff",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="maxMinDiff",
        description="Max minus min (0 for empty list)",
        test_cases=[
            {"input": "[3, 1, 4, 1, 5]", "expected": "4"},
            {"input": "[]", "expected": "0"},
            {"input": "[5]", "expected": "0"},
            {"input": "[-3, 5, -1]", "expected": "8"},
        ],
        category="stretch_s3",
        return_type="Int",
    ),
    SynthesisTask(
        name="sum_abs_values",
        ghc_equivalent="sum_abs_values",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="sumAbsValues",
        description="Sum of absolute values (novel foldl step)",
        test_cases=[
            {"input": "[3, -1, -4, 2]", "expected": "10"},
            {"input": "[]", "expected": "0"},
            {"input": "[1, 2, 3]", "expected": "6"},
            {"input": "[-5]", "expected": "5"},
        ],
        category="stretch_s3",
        return_type="Int",
    ),
]

STRETCH_S4_TASKS = [
    SynthesisTask(
        name="longest_nondec_prefix",
        ghc_equivalent="longest_nondec_prefix",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="longestNondecPrefix",
        description="Length of longest non-decreasing prefix (complex state fold)",
        test_cases=[
            {"input": "[1, 2, 3, 1, 2]", "expected": "3"},
            {"input": "[5, 4, 3]", "expected": "1"},
            {"input": "[]", "expected": "0"},
            {"input": "[1, 1, 1]", "expected": "3"},
        ],
        category="stretch_s4",
    ),
    SynthesisTask(
        name="compress_consecutive",
        ghc_equivalent="compress_consecutive",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="compressConsecutive",
        description="Remove consecutive duplicates: [1,1,2,3,3,2] → [1,2,3,2]",
        test_cases=[
            {"input": "[1, 1, 2, 3, 3, 2]", "expected": "[1, 2, 3, 2]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5, 5, 5]", "expected": "[5]"},
            {"input": "[1, 2, 3]", "expected": "[1, 2, 3]"},
        ],
        category="stretch_s4",
        return_type="List Int",
    ),
    SynthesisTask(
        name="count_peaks",
        ghc_equivalent="count_peaks",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="countPeaks",
        description="Count elements strictly greater than both neighbors",
        test_cases=[
            {"input": "[1, 3, 2, 4, 1]", "expected": "2"},
            {"input": "[1, 2, 3]", "expected": "0"},
            {"input": "[]", "expected": "0"},
            {"input": "[1, 3, 1]", "expected": "1"},
        ],
        category="stretch_s4",
    ),
    SynthesisTask(
        name="sliding_max_2",
        ghc_equivalent="sliding_max_2",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="slidingMax2",
        description="Sliding window maximum (size 2): [3,1,4] → [3,4]",
        test_cases=[
            {"input": "[3, 1, 4, 1, 5]", "expected": "[3, 4, 4, 5]"},
            {"input": "[5]", "expected": "[]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1, 2]", "expected": "[2]"},
        ],
        category="stretch_s4",
        return_type="List Int",
    ),
    SynthesisTask(
        name="count_inversions",
        ghc_equivalent="count_inversions",
        lean_file="FormalNav/TasksStretch.lean",
        theorem_name="countInversions",
        description="Count pairs (i,j) where i<j but xs[i]>xs[j] (O(n²) algorithm)",
        test_cases=[
            {"input": "[3, 1, 2]", "expected": "2"},
            {"input": "[1, 2, 3]", "expected": "0"},
            {"input": "[]", "expected": "0"},
            {"input": "[3, 2, 1]", "expected": "3"},
        ],
        category="stretch_s4",
    ),
]

STRETCH_TASKS = STRETCH_S1_TASKS + STRETCH_S2_TASKS + STRETCH_S3_TASKS + STRETCH_S4_TASKS

# Sprint 26: V3 vocabulary validation tasks (10 core)
V3_TASKS = [
    SynthesisTask(
        name="abs_sum", ghc_equivalent="abs_sum",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="absSumV3",
        description="Sum of absolute values (foldl + conditional)",
        test_cases=[
            {"input": "[3, -1, -4, 2]", "expected": "10"},
            {"input": "[]", "expected": "0"},
            {"input": "[-5]", "expected": "5"},
            {"input": "[1, 2, 3]", "expected": "6"},
        ],
        category="v3_core", return_type="Int",
    ),
    SynthesisTask(
        name="cube_sum", ghc_equivalent="cube_sum",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="cubeSumV3",
        description="Sum of cubes 1³ + 2³ + ... + n³ (Nat.rec cubic step)",
        test_cases=[
            {"input": "3", "expected": "36"},
            {"input": "0", "expected": "0"},
            {"input": "1", "expected": "1"},
            {"input": "4", "expected": "100"},
        ],
        category="v3_core", return_type="Nat", input_type="Nat",
    ),
    SynthesisTask(
        name="double_evens", ghc_equivalent="double_evens",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="doubleEvens",
        description="Double even elements, keep odds unchanged",
        test_cases=[
            {"input": "[1, 2, 3, 4]", "expected": "[1, 4, 3, 8]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1, 3, 5]", "expected": "[1, 3, 5]"},
            {"input": "[2, 4]", "expected": "[4, 8]"},
        ],
        category="v3_core", return_type="List Int",
    ),
    SynthesisTask(
        name="relu_list", ghc_equivalent="relu_list",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="reluList",
        description="ReLU: replace negatives with 0",
        test_cases=[
            {"input": "[-1, 2, -3, 4]", "expected": "[0, 2, 0, 4]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1, 2, 3]", "expected": "[1, 2, 3]"},
            {"input": "[-5, -3]", "expected": "[0, 0]"},
        ],
        category="v3_core", return_type="List Int",
    ),
    SynthesisTask(
        name="last_element", ghc_equivalent="last_element",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="lastElement",
        description="Last element or 0 if empty (foldl)",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "3"},
            {"input": "[]", "expected": "0"},
            {"input": "[5]", "expected": "5"},
            {"input": "[-1, -2]", "expected": "-2"},
        ],
        category="v3_core", return_type="Int",
    ),
    SynthesisTask(
        name="neg_sum", ghc_equivalent="neg_sum",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="negSum",
        description="Negate the sum of a list",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "-6"},
            {"input": "[]", "expected": "0"},
            {"input": "[-1, -2]", "expected": "3"},
            {"input": "[5]", "expected": "-5"},
        ],
        category="v3_core", return_type="Int",
    ),
    SynthesisTask(
        name="max_minus_min", ghc_equivalent="max_minus_min",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="maxMinusMin",
        description="Max minus min (0 for empty list)",
        test_cases=[
            {"input": "[3, 1, 5]", "expected": "4"},
            {"input": "[]", "expected": "0"},
            {"input": "[5]", "expected": "0"},
            {"input": "[-3, 5, -1]", "expected": "8"},
        ],
        category="v3_core", return_type="Int",
    ),
    SynthesisTask(
        name="deviations_v3", ghc_equivalent="deviations_v3",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="deviations",
        description="Each element minus the maximum",
        test_cases=[
            {"input": "[3, 1, 4, 1, 5]", "expected": "[-2, -4, -1, -4, 0]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[0]"},
            {"input": "[1, 1, 1]", "expected": "[0, 0, 0]"},
        ],
        category="v3_core", return_type="List Int",
    ),
    SynthesisTask(
        name="clamp_to_range", ghc_equivalent="clamp_to_range",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="clampToRange",
        description="Clamp each element to [-5, 5]",
        test_cases=[
            {"input": "[7, -8, 3, -2, 15]", "expected": "[5, -5, 3, -2, 5]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[0]", "expected": "[0]"},
            {"input": "[-3, 3]", "expected": "[-3, 3]"},
        ],
        category="v3_core", return_type="List Int",
    ),
    SynthesisTask(
        name="replace_neg_v3", ghc_equivalent="replace_neg_v3",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="replaceNeg",
        description="Replace negatives with 0, keep non-negatives",
        test_cases=[
            {"input": "[-1, 2, -3, 4, 0]", "expected": "[0, 2, 0, 4, 0]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1, 2, 3]", "expected": "[1, 2, 3]"},
            {"input": "[-1, -2]", "expected": "[0, 0]"},
        ],
        category="v3_core", return_type="List Int",
    ),
]

# Sprint 26: V3 stretch tasks (5)
V3_STRETCH_TASKS = [
    SynthesisTask(
        name="sum_abs_positives", ghc_equivalent="sum_abs_positives",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="sumAbsPositives",
        description="Sum of absolute values of positives only",
        test_cases=[
            {"input": "[3, -1, 4, -2]", "expected": "7"},
            {"input": "[]", "expected": "0"},
            {"input": "[-1, -2]", "expected": "0"},
            {"input": "[1, 2, 3]", "expected": "6"},
        ],
        category="v3_stretch", return_type="Int",
    ),
    SynthesisTask(
        name="normalize_to_max", ghc_equivalent="normalize_to_max",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="normalizeToMax",
        description="Divide each element by max (integer division)",
        test_cases=[
            {"input": "[10, 5, 20]", "expected": "[0, 0, 1]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[4]", "expected": "[1]"},
            {"input": "[3, 6, 9]", "expected": "[0, 0, 1]"},
        ],
        category="v3_stretch", return_type="List Int",
    ),
    SynthesisTask(
        name="count_sign_changes", ghc_equivalent="count_sign_changes",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="countSignChanges",
        description="Count sign changes in a list",
        test_cases=[
            {"input": "[1, -2, 3, -4]", "expected": "3"},
            {"input": "[]", "expected": "0"},
            {"input": "[1, 2, 3]", "expected": "0"},
            {"input": "[-1, 1]", "expected": "1"},
        ],
        category="v3_stretch",
    ),
    SynthesisTask(
        name="sorted_abs", ghc_equivalent="sorted_abs",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="sortedAbs",
        description="Map absolute values to Nat list",
        test_cases=[
            {"input": "[-3, 1, -5]", "expected": "[3, 1, 5]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[0]", "expected": "[0]"},
            {"input": "[2, -2]", "expected": "[2, 2]"},
        ],
        category="v3_stretch", return_type="List Nat",
    ),
    SynthesisTask(
        name="sum_squares_positive", ghc_equivalent="sum_squares_positive",
        lean_file="FormalNav/TasksVocabulary.lean", theorem_name="sumSquaresPositive",
        description="Sum of squares of positive elements",
        test_cases=[
            {"input": "[3, -1, 2, -4]", "expected": "13"},
            {"input": "[]", "expected": "0"},
            {"input": "[-1, -2]", "expected": "0"},
            {"input": "[1, 2, 3]", "expected": "14"},
        ],
        category="v3_stretch", return_type="Int",
    ),
]

# Sprint 27: Diagnostic benchmark — generalization beyond template matching

DIAG_A_TASKS = [
    SynthesisTask(
        name="second_smallest", ghc_equivalent="second_smallest",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="secondSmallest",
        description="Second-smallest element (min-direction tuple accumulator)",
        test_cases=[
            {"input": "[3, 1, 4, 1, 5]", "expected": "1"},
            {"input": "[]", "expected": "0"},
            {"input": "[5]", "expected": "5"},
            {"input": "[2, 2]", "expected": "2"},
        ],
        category="diag_a", return_type="Int",
    ),
    SynthesisTask(
        name="map_with_index", ghc_equivalent="map_with_index",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="mapWithIndex",
        description="Multiply each element by its 1-based index",
        test_cases=[
            {"input": "[2, 3, 4]", "expected": "[2, 6, 12]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[5]"},
            {"input": "[1, 1, 1, 1]", "expected": "[1, 2, 3, 4]"},
        ],
        category="diag_a", return_type="List Int",
    ),
    SynthesisTask(
        name="prefix_while_increasing", ghc_equivalent="prefix_while_increasing",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="prefixWhileIncreasing",
        description="Take prefix while strictly increasing",
        test_cases=[
            {"input": "[1, 3, 2, 4]", "expected": "[1, 3]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[5]"},
            {"input": "[1, 2, 3, 4]", "expected": "[1, 2, 3, 4]"},
        ],
        category="diag_a", return_type="List Int",
    ),
    SynthesisTask(
        name="remove_duplicates_keep_last", ghc_equivalent="remove_duplicates_keep_last",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="removeDuplicatesKeepLast",
        description="Remove duplicates keeping last occurrence",
        test_cases=[
            {"input": "[1, 2, 1, 3]", "expected": "[2, 1, 3]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1, 1, 1]", "expected": "[1]"},
            {"input": "[1, 2, 3]", "expected": "[1, 2, 3]"},
        ],
        category="diag_a", return_type="List Int",
    ),
    SynthesisTask(
        name="dot_product_self", ghc_equivalent="dot_product_self",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="dotProductSelf",
        description="Dot product of list with itself (sum of squares)",
        test_cases=[
            {"input": "[2, 3]", "expected": "13"},
            {"input": "[]", "expected": "0"},
            {"input": "[4]", "expected": "16"},
            {"input": "[1, -2, 3]", "expected": "14"},
        ],
        category="diag_a", return_type="Int",
    ),
]

DIAG_B_TASKS = [
    SynthesisTask(
        name="max_subarray_sum", ghc_equivalent="max_subarray_sum",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="maxSubarraySum",
        description="Maximum contiguous subarray sum (Kadane's algorithm)",
        test_cases=[
            {"input": "[2, -1, 3, -2]", "expected": "4"},
            {"input": "[]", "expected": "0"},
            {"input": "[-1, -2, -3]", "expected": "0"},
            {"input": "[5]", "expected": "5"},
        ],
        category="diag_b", return_type="Int",
    ),
    SynthesisTask(
        name="longest_plateau", ghc_equivalent="longest_plateau",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="longestPlateau",
        description="Length of longest run of identical values",
        test_cases=[
            {"input": "[1, 1, 2, 2, 2, 1]", "expected": "3"},
            {"input": "[]", "expected": "0"},
            {"input": "[5]", "expected": "1"},
            {"input": "[4, 4, 4, 4]", "expected": "4"},
            {"input": "[1, 1, 1]", "expected": "3"},
            {"input": "[3, 1, 1, 1, 1, 2]", "expected": "4"},
        ],
        category="diag_b",
    ),
    SynthesisTask(
        name="dutch_flag", ghc_equivalent="dutch_flag",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="dutchFlag",
        description="3-way partition around pivot (less, equal, greater)",
        test_cases=[
            {"input": "3", "input2": "[5, 2, 3, 1, 4, 3]", "expected": "[2, 1, 3, 3, 5, 4]"},
            {"input": "1", "input2": "[]", "expected": "[]"},
            {"input": "5", "input2": "[5, 5, 5]", "expected": "[5, 5, 5]"},
            {"input": "3", "input2": "[1, 2, 3, 4, 5]", "expected": "[1, 2, 3, 4, 5]"},
        ],
        category="diag_b", return_type="List Int",
        params_signature="(pivot : Int) (xs : List Int)",
    ),
    SynthesisTask(
        name="majority_element", ghc_equivalent="majority_element",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="majorityElement",
        description="Boyer-Moore majority vote",
        test_cases=[
            {"input": "[3, 3, 4, 2, 3, 3, 5]", "expected": "3"},
            {"input": "[1]", "expected": "1"},
            {"input": "[1, 2, 1]", "expected": "1"},
            {"input": "[2, 2, 1, 1, 2]", "expected": "2"},
            {"input": "[1, 2, 2, 2, 1]", "expected": "2"},
            {"input": "[5, 3, 3, 3, 5, 3]", "expected": "3"},
        ],
        category="diag_b", return_type="Int",
    ),
    SynthesisTask(
        name="equilibrium_index", ghc_equivalent="equilibrium_index",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="equilibriumIndex",
        description="First index where left-sum equals right-sum (-1 if none)",
        test_cases=[
            {"input": "[1, 3, 5, 2, 2]", "expected": "2"},
            {"input": "[]", "expected": "-1"},
            {"input": "[1]", "expected": "0"},
            {"input": "[1, 2, 3]", "expected": "-1"},
        ],
        category="diag_b", return_type="Int",
    ),
]

DIAG_C_TASKS = [
    SynthesisTask(
        name="chunks_of", ghc_equivalent="chunks_of",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="chunksOf",
        description="Split list into groups of n",
        test_cases=[
            {"input": "2", "input2": "[1, 2, 3, 4, 5]", "expected": "[[1, 2], [3, 4], [5]]"},
            {"input": "3", "input2": "[1, 2, 3, 4, 5, 6]", "expected": "[[1, 2, 3], [4, 5, 6]]"},
            {"input": "2", "input2": "[]", "expected": "[]"},
            {"input": "1", "input2": "[1, 2]", "expected": "[[1], [2]]"},
        ],
        category="diag_c", return_type="List (List Int)",
        params_signature="(n : Nat) (xs : List Int)",
    ),
    SynthesisTask(
        name="diag_transpose", ghc_equivalent="diag_transpose",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="diagTranspose",
        description="Matrix transpose",
        test_cases=[
            {"input": "[[1, 2], [3, 4], [5, 6]]", "expected": "[[1, 3, 5], [2, 4, 6]]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[[1]]", "expected": "[[1]]"},
            {"input": "[[1, 2, 3]]", "expected": "[[1], [2], [3]]"},
        ],
        category="diag_c", return_type="List (List Int)",
        input_type="List (List Int)",
    ),
    SynthesisTask(
        name="insert_sorted", ghc_equivalent="insert_sorted",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="insertSorted",
        description="Insert element at sorted position in sorted list",
        test_cases=[
            {"input": "3", "input2": "[1, 2, 4, 5]", "expected": "[1, 2, 3, 4, 5]"},
            {"input": "1", "input2": "[]", "expected": "[1]"},
            {"input": "0", "input2": "[1, 2, 3]", "expected": "[0, 1, 2, 3]"},
            {"input": "5", "input2": "[1, 2, 3]", "expected": "[1, 2, 3, 5]"},
        ],
        category="diag_c", return_type="List Int",
        params_signature="(x : Int) (xs : List Int)",
    ),
    SynthesisTask(
        name="unfold_n", ghc_equivalent="unfold_n",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="unfoldN",
        description="Generate list by repeated function application",
        test_cases=[
            {"input": "4", "input2": "(fun x : Int => x + 1)", "input3": "0", "expected": "[0, 1, 2, 3]"},
            {"input": "0", "input2": "(fun x : Int => x + 1)", "input3": "5", "expected": "[]"},
            {"input": "3", "input2": "(fun x : Int => x * 2)", "input3": "1", "expected": "[1, 2, 4]"},
            {"input": "1", "input2": "(fun x : Int => x + 10)", "input3": "0", "expected": "[0]"},
        ],
        category="diag_c", return_type="List Int",
        params_signature="(n : Nat) (f : Int → Int) (seed : Int)",
    ),
    SynthesisTask(
        name="interleave_zip", ghc_equivalent="interleave_zip",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="interleaveZip",
        description="Interleave two lists element-by-element",
        test_cases=[
            {"input": "[1, 2, 3]", "input2": "[4, 5]", "expected": "[1, 4, 2, 5, 3]"},
            {"input": "[]", "input2": "[1, 2]", "expected": "[1, 2]"},
            {"input": "[1]", "input2": "[]", "expected": "[1]"},
            {"input": "[1, 2]", "input2": "[3, 4]", "expected": "[1, 3, 2, 4]"},
        ],
        category="diag_c", return_type="List Int",
    ),
]

DIAG_D_TASKS = [
    SynthesisTask(
        name="diag_insertion_sort", ghc_equivalent="diag_insertion_sort",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="diagInsertionSort",
        description="Sort by repeated insertion into sorted accumulator",
        test_cases=[
            {"input": "[3, 1, 2]", "expected": "[1, 2, 3]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[1]", "expected": "[1]"},
            {"input": "[3, 2, 1]", "expected": "[1, 2, 3]"},
        ],
        category="diag_d", return_type="List Int",
    ),
    SynthesisTask(
        name="eval_stack", ghc_equivalent="eval_stack",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="evalStack",
        description="Stack evaluator (push positive, 0 = pop two and add)",
        test_cases=[
            {"input": "[3, 4, 0]", "expected": "7"},
            {"input": "[]", "expected": "0"},
            {"input": "[5]", "expected": "5"},
            {"input": "[1, 2, 0, 3, 0]", "expected": "6"},
            {"input": "[1, 2]", "expected": "2"},
            {"input": "[5, 10, 0, 1]", "expected": "1"},
        ],
        category="diag_d", return_type="Int",
    ),
    SynthesisTask(
        name="all_sublists", ghc_equivalent="all_sublists",
        lean_file="FormalNav/TasksDiagnostic.lean", theorem_name="allSublists",
        description="All contiguous sublists",
        test_cases=[
            {"input": "[1, 2, 3]", "expected": "[[1], [1, 2], [1, 2, 3], [2], [2, 3], [3]]"},
            {"input": "[]", "expected": "[]"},
            {"input": "[5]", "expected": "[[5]]"},
            {"input": "[1, 2]", "expected": "[[1], [1, 2], [2]]"},
        ],
        category="diag_d", return_type="List (List Int)",
    ),
]

DIAG_TASKS = DIAG_A_TASKS + DIAG_B_TASKS + DIAG_C_TASKS + DIAG_D_TASKS


def _load_diag_holdout_tasks() -> list[SynthesisTask]:
    """Return the 6 Sprint 28 diagnostic held-out tasks listed in
    ``lean_mcts/synth/diag_holdout.txt``.

    These tasks span all 4 diagnostic tiers (A/B/C/D) and are the
    half of the Sprint 29 12-task primary metric that measures
    transfer to tasks with zero augmented variants in training data.
    They were frozen at the end of Sprint 28 and the list must not
    change for Sprint 29.
    """
    from pathlib import Path
    holdout_path = Path(__file__).resolve().parent.parent / "synth" / "diag_holdout.txt"
    names: list[str] = []
    with open(holdout_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                names.append(line)
    diag_by_name = {t.name: t for t in DIAG_TASKS}
    missing = [n for n in names if n not in diag_by_name]
    if missing:
        raise KeyError(
            f"diag_holdout.txt references tasks not in DIAG_TASKS: {missing}"
        )
    return [diag_by_name[n] for n in names]


DIAG_HOLDOUT_TASKS = _load_diag_holdout_tasks()


def _load_hard_holdout_tasks() -> list[SynthesisTask]:
    """Return the 6 Sprint 29 hard-tier held-out tasks from
    ``lean_mcts/synth/held_out_hard_tasks.py``.

    These are the new half of the Sprint 29 12-task primary metric,
    frozen in Phase 0.5 BEFORE library mining began (git tag
    ``s29-heldout-frozen``).
    """
    from lean_mcts.synth.held_out_hard_tasks import HELD_OUT_HARD_TASKS
    return list(HELD_OUT_HARD_TASKS)


HARD_HOLDOUT_TASKS = _load_hard_holdout_tasks()
HELD_OUT_12_TASKS = DIAG_HOLDOUT_TASKS + HARD_HOLDOUT_TASKS

ALL_TASKS = TASKS + HARD_TASKS + V2_TASKS


def load_generated_tasks(path: str = None) -> list[SynthesisTask]:
    """Load generated synthesis tasks from synth/synth_tasks.jsonl."""
    import json
    from pathlib import Path

    if path is None:
        base = Path(__file__).resolve().parent.parent
        path = str(base / "synth" / "synth_tasks.jsonl")

    tasks = []
    with open(path) as f:
        for line in f:
            entry = json.loads(line)
            tasks.append(SynthesisTask(
                name=entry["name"],
                ghc_equivalent=entry["name"],
                lean_file=entry["lean_file"],
                theorem_name=entry["theorem_name"],
                description=entry["description"],
                test_cases=entry["test_cases"],
                category=entry["category"],
                return_type=entry["return_type"],
                input_type=entry.get("input_type", "List Int"),
            ))
    return tasks


def get_task(name: str) -> SynthesisTask:
    """Look up a task by name."""
    all_pools = (ALL_TASKS + RECURRENCE_TASKS + FUNDAMENTALS_TASKS + MULTISTEP_TASKS
                 + DEEP_MULTISTEP_TASKS + COMPOSITIONAL_TASKS + RECURSIVE_TASKS
                 + STRETCH_TASKS + V3_TASKS + V3_STRETCH_TASKS + DIAG_TASKS
                 + HARD_HOLDOUT_TASKS)
    for task in all_pools:
        if task.name == name:
            return task
    raise KeyError(f"Unknown task: {name}. Available: {[t.name for t in all_pools]}")


def get_tasks(tier: str | None = None) -> list[SynthesisTask]:
    """Get tasks, optionally filtered by tier."""
    if tier == "t2":
        return TASKS
    elif tier == "hard":
        return HARD_TASKS
    elif tier == "v2":
        return V2_TASKS
    elif tier == "recurrence":
        return RECURRENCE_TASKS
    elif tier == "fundamentals":
        return FUNDAMENTALS_TASKS
    elif tier == "multistep":
        return MULTISTEP_TASKS
    elif tier == "deep_multistep":
        return DEEP_MULTISTEP_TASKS
    elif tier == "compositional":
        return COMPOSITIONAL_TASKS
    elif tier == "recursive_r1":
        return RECURSIVE_R1_TASKS
    elif tier == "recursive_r2":
        return RECURSIVE_R2_TASKS
    elif tier == "recursive_r3":
        return RECURSIVE_R3_TASKS
    elif tier == "recursive":
        return RECURSIVE_TASKS
    elif tier == "stretch_s1":
        return STRETCH_S1_TASKS
    elif tier == "stretch_s2":
        return STRETCH_S2_TASKS
    elif tier == "stretch_s3":
        return STRETCH_S3_TASKS
    elif tier == "stretch_s4":
        return STRETCH_S4_TASKS
    elif tier == "stretch":
        return STRETCH_TASKS
    elif tier == "v3":
        return V3_TASKS
    elif tier == "v3_stretch":
        return V3_STRETCH_TASKS
    elif tier == "v3_all":
        return V3_TASKS + V3_STRETCH_TASKS
    elif tier == "diag_a":
        return DIAG_A_TASKS
    elif tier == "diag_b":
        return DIAG_B_TASKS
    elif tier == "diag_c":
        return DIAG_C_TASKS
    elif tier == "diag_d":
        return DIAG_D_TASKS
    elif tier == "diag_all":
        return DIAG_TASKS
    elif tier == "diag_holdout":
        return DIAG_HOLDOUT_TASKS
    elif tier == "hard_holdout":
        return HARD_HOLDOUT_TASKS
    elif tier == "held_out_12":
        return HELD_OUT_12_TASKS
    elif tier == "all+v2":
        return TASKS + HARD_TASKS + V2_TASKS
    elif tier == "all+v2+recurrence":
        return TASKS + HARD_TASKS + V2_TASKS + RECURRENCE_TASKS
    elif tier == "all+v2+recurrence+fundamentals":
        return TASKS + HARD_TASKS + V2_TASKS + RECURRENCE_TASKS + FUNDAMENTALS_TASKS
    elif tier == "generated":
        return load_generated_tasks()
    elif tier == "all+generated":
        return ALL_TASKS + load_generated_tasks()
    return ALL_TASKS
