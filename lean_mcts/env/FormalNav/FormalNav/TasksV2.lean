import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false

/-!
# Sprint 14 Benchmark Tasks (V2)

20 new tasks across 4 categories:

## Category A — Multi-Type Input (5 tasks)
Different input type signatures (List (List Int), two-arg xs ys).

## Category B — Novel Compositions (5 tasks)
Solutions NOT in any fixed policy. Tests genuine generalization.

## Category C — Multi-Step Decomposition (5 tasks)
Tasks where multi-step MCTS (depth > 1) is natural.

## Category D — Algorithmic (5 tasks)
Exercise new library vocabulary: range, scanl, zipIdx, Prod.fst/snd.
-/

-- ============================================================
-- Category A: Multi-Type Input
-- ============================================================

-- A1: flatten a list of lists
-- Reference: xss.flatten
def concatAll (xss : List (List Int)) : List Int := by sorry

-- A2: length of longest sublist
-- Reference: (xss.map List.length).foldl max 0
def longestSublist (xss : List (List Int)) : Nat := by sorry

-- A3: merge two lists and sort
-- Reference: List.mergeSort (xs ++ ys)
def mergeSorted (xs : List Int) (ys : List Int) : List Int := by sorry

-- A4: dot product of two lists
-- Reference: (List.zipWith (fun a b => a * b) xs ys).foldl (fun a b => a + b) 0
def synthDotProduct (xs : List Int) (ys : List Int) : Int := by sorry

-- A5: symmetric difference of two lists
-- Reference: (xs.filter (fun x => !(ys.contains x))) ++ (ys.filter (fun y => !(xs.contains y)))
def symmetricDiff (xs : List Int) (ys : List Int) : List Int := by sorry

-- ============================================================
-- Category B: Novel Compositions (NOT in any fixed policy)
-- ============================================================

-- B1: count unique positive elements
-- Reference: (xs.filter (fun x => decide (x > 0)) |>.eraseDups).length
def countUniquePositive (xs : List Int) : Nat := by sorry

-- B2: sum of sorted positives
-- Reference: (List.mergeSort (xs.filter (fun x => decide (x > 0)))).foldl (fun a b => a + b) 0
def sumSortedPositives (xs : List Int) : Int := by sorry

-- B3: maximum even element (0 if none)
-- Reference: (xs.filter (fun x => decide (x % 2 = 0))).foldl max 0
def maxEven (xs : List Int) : Int := by sorry

-- B4: reverse of even elements
-- Reference: (xs.filter (fun x => decide (x % 2 = 0))).reverse
def reverseEvens (xs : List Int) : List Int := by sorry

-- B5: sum of squares of positives
-- Reference: ((xs.filter (fun x => decide (x > 0))).map (fun x => x * x)).foldl (fun a b => a + b) 0
def squarePositivesSum (xs : List Int) : Int := by sorry

-- ============================================================
-- Category C: Multi-Step Decomposition
-- ============================================================

-- C1: count positives and non-positives
-- Multi-step: constructor; · exact (xs.filter (fun x => decide (x > 0))).length; · exact (xs.filter (fun x => decide (x ≤ 0))).length
def partitionAndCount (xs : List Int) : Nat × Nat := by sorry

-- C2: sort unique elements
-- Multi-step: have h := xs.eraseDups; exact List.mergeSort h
def synthNormalize (xs : List Int) : List Int := by sorry

-- C3: min and max of list
-- Multi-step: constructor; · exact xs.foldl min 0; · exact xs.foldl max 0
def stats (xs : List Int) : Int × Int := by sorry

-- C4: double each element AND add 1 to each element
-- Multi-step: constructor; · exact xs.map (fun x => x * 2); · exact xs.map (fun x => x + 1)
def splitApply (xs : List Int) : List Int × List Int := by sorry

-- C5: count and sum of positives
-- Multi-step: have h := xs.filter (fun x => decide (x > 0)); exact (h.length, h.foldl (fun a b => a + b) 0)
def filterThenStats (xs : List Int) : Nat × Int := by sorry

-- ============================================================
-- Category D: Algorithmic (new library vocabulary)
-- ============================================================

-- D1: prefix sums
-- Reference: (List.scanl (fun a b => a + b) 0 xs).drop 1
def prefixSums (xs : List Int) : List Int := by sorry

-- D2: running maximum
-- Reference: (List.scanl max 0 xs).drop 1
def runningMax (xs : List Int) : List Int := by sorry

-- D3: first N squares
-- Reference: (List.range n).map (fun i => i * i)
def firstNSquares (n : Nat) : List Nat := by sorry

-- D4: every other element (even indices)
-- Reference: (xs.zipIdx.filter (fun (_, i) => decide (i % 2 = 0))).map Prod.fst
def everyOther (xs : List Int) : List Int := by sorry

-- D5: pairwise sums of adjacent elements
-- Reference: (xs.zip xs.tail).map (fun (a, b) => a + b)
def pairwiseSums (xs : List Int) : List Int := by sorry


-- ============================================================
-- Reference solutions (for oracle verification)
-- ============================================================

-- Category A
def concatAll_ref (xss : List (List Int)) : List Int :=
  xss.flatten

def longestSublist_ref (xss : List (List Int)) : Nat :=
  (xss.map List.length).foldl max 0

def mergeSorted_ref (xs : List Int) (ys : List Int) : List Int :=
  List.mergeSort (xs ++ ys)

def synthDotProduct_ref (xs : List Int) (ys : List Int) : Int :=
  (List.zipWith (fun a b => a * b) xs ys).foldl (fun a b => a + b) 0

def symmetricDiff_ref (xs : List Int) (ys : List Int) : List Int :=
  (xs.filter (fun x => !(ys.contains x))) ++ (ys.filter (fun y => !(xs.contains y)))

-- Category B
def countUniquePositive_ref (xs : List Int) : Nat :=
  ((xs.filter (fun x => decide (x > 0))).eraseDups).length

def sumSortedPositives_ref (xs : List Int) : Int :=
  (List.mergeSort (xs.filter (fun x => decide (x > 0)))).foldl (fun a b => a + b) 0

def maxEven_ref (xs : List Int) : Int :=
  (xs.filter (fun x => decide (x % 2 = 0))).foldl max 0

def reverseEvens_ref (xs : List Int) : List Int :=
  (xs.filter (fun x => decide (x % 2 = 0))).reverse

def squarePositivesSum_ref (xs : List Int) : Int :=
  ((xs.filter (fun x => decide (x > 0))).map (fun x => x * x)).foldl (fun a b => a + b) 0

-- Category C
def partitionAndCount_ref (xs : List Int) : Nat × Nat :=
  ((xs.filter (fun x => decide (x > 0))).length,
   (xs.filter (fun x => decide (x ≤ 0))).length)

def synthNormalize_ref (xs : List Int) : List Int :=
  List.mergeSort (xs.eraseDups)

def stats_ref (xs : List Int) : Int × Int :=
  (xs.foldl min 0, xs.foldl max 0)

def splitApply_ref (xs : List Int) : List Int × List Int :=
  (xs.map (fun x => x * 2), xs.map (fun x => x + 1))

def filterThenStats_ref (xs : List Int) : Nat × Int :=
  let h := xs.filter (fun x => decide (x > 0))
  (h.length, h.foldl (fun a b => a + b) 0)

-- Category D
def prefixSums_ref (xs : List Int) : List Int :=
  (List.scanl (fun a b => a + b) 0 xs).drop 1

def runningMax_ref (xs : List Int) : List Int :=
  (List.scanl max 0 xs).drop 1

def firstNSquares_ref (n : Nat) : List Nat :=
  (List.range n).map (fun i => i * i)

def everyOther_ref (xs : List Int) : List Int :=
  (xs.zipIdx.filter (fun (_, i) => decide (i % 2 = 0))).map Prod.fst

def pairwiseSums_ref (xs : List Int) : List Int :=
  (xs.zip xs.tail).map (fun (a, b) => a + b)
