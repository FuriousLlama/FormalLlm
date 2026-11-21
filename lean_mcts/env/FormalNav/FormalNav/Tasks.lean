import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

/-!
# Benchmark Tasks for MCTS Synthesis

10 program synthesis tasks equivalent to GHC Core T2 (compositional).
Each function is defined with `by sorry` — the MCTS must fill in the body.

## Categories
- **filter_fold**: filter + fold/length compositions
- **map_fold**: map + fold compositions
- **pipeline**: two-function pipelines
- **accumulator**: accumulator patterns

## How MCTS uses these
Each `def` starts with goal `xs : List Int ⊢ <ReturnType>`.
The MCTS explores tactic sequences (primarily `exact <expr>`) to close the goal.
Lean's kernel validates each step, pruning invalid constructions immediately.
-/

-- Task 1: filter_fold
-- Reference: (xs.filter (fun x => decide (x > 0))).length
def countPositive (xs : List Int) : Nat := by sorry

-- Task 2: map_fold
-- Reference: xs.map (fun x => x * x)
def mapSquare (xs : List Int) : List Int := by sorry

-- Task 3: pipeline
-- Reference: (List.mergeSort xs).reverse
def sortDesc (xs : List Int) : List Int := by sorry

-- Task 4: map_fold
-- Reference: (xs.map (· * 2)).foldl (· + ·) 0
def sumDoubles (xs : List Int) : Int := by sorry

-- Task 5: map_fold
-- Reference: (xs.map (fun x => x.natAbs)).foldl max 0
def maxAbs (xs : List Int) : Nat := by sorry

-- Task 6: pipeline
-- Reference: xs.takeWhile (fun x => decide (x > 0))
def takePositives (xs : List Int) : List Int := by sorry

-- Task 7: pipeline
-- Reference: List.mergeSort (xs.eraseDups)
def nubSort (xs : List Int) : List Int := by sorry

-- Task 8: pipeline
-- Reference: xs.dropWhile (fun x => decide (x < 0))
def dropNegatives (xs : List Int) : List Int := by sorry

-- Task 9: filter_fold
-- Reference: (xs.filter (fun x => decide (x % 2 = 0))).length
def countEven (xs : List Int) : Nat := by sorry

-- Task 10: accumulator
-- Reference: (List.scanl (· + ·) 0 xs).drop 1
def accumSum (xs : List Int) : List Int := by sorry

/-!
## Reference solutions (for verification, not MCTS targets)

These are provided as separate definitions so we can test correctness
of MCTS-discovered solutions by comparing outputs.
-/

def countPositive_ref (xs : List Int) : Nat :=
  (xs.filter (fun x => decide (x > 0))).length

def mapSquare_ref (xs : List Int) : List Int :=
  xs.map (fun x => x * x)

def sortDesc_ref (xs : List Int) : List Int :=
  (List.mergeSort xs).reverse

def sumDoubles_ref (xs : List Int) : Int :=
  (xs.map (· * 2)).foldl (· + ·) 0

def maxAbs_ref (xs : List Int) : Nat :=
  (xs.map (fun x => x.natAbs)).foldl max 0

def takePositives_ref (xs : List Int) : List Int :=
  xs.takeWhile (fun x => decide (x > 0))

def nubSort_ref (xs : List Int) : List Int :=
  List.mergeSort (xs.eraseDups)

def dropNegatives_ref (xs : List Int) : List Int :=
  xs.dropWhile (fun x => decide (x < 0))

def countEven_ref (xs : List Int) : Nat :=
  (xs.filter (fun x => decide (x % 2 = 0))).length

def accumSum_ref (xs : List Int) : List Int :=
  (List.scanl (· + ·) 0 xs).drop 1
