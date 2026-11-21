import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

/-!
# Compositional Benchmark Tasks (Sprint 22)

8 tasks that require compositions NOT in expressions.jsonl.
Tests the compositional search engine's ability to discover
novel programs from fragment primitives.

Categories:
- depth-2 novel: both fragments exist but the pair was never enumerated
- depth-3 novel: three-step pipelines
- cross-type: Nat → List Nat → Nat via List.range bridge
- baseline: known compositions that must still work
-/

-- Task 1: sum_of_squares (baseline — in expressions.jsonl)
-- Reference: (xs.map (fun x => x * x)).foldl (fun a b => a + b) 0
def sumOfSquaresComp (xs : List Int) : Int := by sorry

-- Task 2: range_sum (cross-type: Nat → Nat via List.range bridge)
-- Reference: (List.range n).foldl (fun a b => a + b) 0
def rangeSum (n : Nat) : Nat := by sorry

-- Task 3: count_unique (depth-2 novel: dedup + length)
-- Reference: xs.eraseDups.length
def countUnique (xs : List Int) : Nat := by sorry

-- Task 4: reverse_filter (depth-2 novel: filter + reverse)
-- Reference: (xs.filter (fun x => decide (x > 0))).reverse
def reverseFilter (xs : List Int) : List Int := by sorry

-- Task 5: sum_positives_squared (depth-3 novel: filter → map → fold)
-- Reference: ((xs.filter (fun x => decide (x > 0))).map (fun x => x * x)).foldl (fun a b => a + b) 0
def sumPositivesSquared (xs : List Int) : Int := by sorry

-- Task 6: max_abs_comp (depth-2 novel: map natAbs + fold max)
-- Reference: (xs.map (fun x => x.natAbs)).foldl max 0
def maxAbsComp (xs : List Int) : Nat := by sorry

-- Task 7: sorted_positives (depth-2 novel: filter + sort)
-- Reference: List.mergeSort (xs.filter (fun x => decide (x > 0)))
def sortedPositives (xs : List Int) : List Int := by sorry

-- Task 8: length_evens (depth-2 novel: filter even + length)
-- Reference: (xs.filter (fun x => decide (x % 2 = 0))).length
def lengthEvens (xs : List Int) : Nat := by sorry
