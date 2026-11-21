import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false

/-!
# Deep Multi-Step Benchmark Tasks (Sprint 21)

6 tasks requiring depth-3+ constructor decomposition or constructor + recurrence:
- Depth-3: nested product types like `(A × B) × C` requiring 2 constructors + 3 exacts
- Depth-2 mixed: product types where one component is a fold/filter

## Key pattern (depth-3)
1. `constructor` splits `⊢ (A × B) × C` into `case fst ⊢ A × B` and `case snd ⊢ C`
2. `constructor` on first sub-goal splits `⊢ A × B` into `case fst ⊢ A` and `case snd ⊢ B`
3. Three `exact` tactics solve A, B, C independently

## Key pattern (depth-2 mixed)
1. `constructor` splits `⊢ A × B` into `case fst ⊢ A` and `case snd ⊢ B`
2. One sub-goal uses a fold, the other uses something else
-/

-- D1: sum, count, max — depth 3 via (Int × Nat) × Int
-- Reference: constructor; constructor; exact xs.foldl (· + ·) 0; exact xs.length; exact xs.foldl max 0
def sumCountMax (xs : List Int) : (Int × Nat) × Int := by sorry

-- D2: min, max, count — depth 3 via (Int × Int) × Nat
-- Reference: constructor; constructor; exact xs.foldl min 0; exact xs.foldl max 0; exact xs.length
def statsTriple (xs : List Int) : (Int × Int) × Nat := by sorry

-- D3: positive count, negative count, total length — depth 3 via (Nat × Nat) × Nat
-- Reference: constructor; constructor; exact (xs.filter (fun x => decide (x > 0))).length; exact (xs.filter (fun x => decide (x < 0))).length; exact xs.length
def partitionLengths (xs : List Int) : (Nat × Nat) × Nat := by sorry

-- D4: sum + length — depth 2, fold + non-fold
-- Reference: constructor; exact xs.foldl (· + ·) 0; exact xs.length
def foldAndLength (xs : List Int) : Int × Nat := by sorry

-- D5: filter both ways — depth 2, two filters with related predicates
-- Reference: constructor; exact xs.filter (fun x => decide (x > 0)); exact xs.filter (fun x => decide (x < 0))
def filterBothWays (xs : List Int) : List Int × List Int := by sorry

-- D6: tuple-fold result + simple computation — depth 2
-- Reference: constructor; exact (xs.foldl (fun (s, c) x => (s + x, c + 1)) (0, 0)).1; exact xs.length
def recurrenceAndSum (xs : List Int) : Int × Nat := by sorry


-- ============================================================
-- Reference solutions (for oracle verification)
-- ============================================================

def sumCountMax_ref (xs : List Int) : (Int × Nat) × Int :=
  ((xs.foldl (· + ·) 0, xs.length), xs.foldl max 0)

def statsTriple_ref (xs : List Int) : (Int × Int) × Nat :=
  ((xs.foldl min 0, xs.foldl max 0), xs.length)

def partitionLengths_ref (xs : List Int) : (Nat × Nat) × Nat :=
  (((xs.filter (fun x => decide (x > 0))).length,
    (xs.filter (fun x => decide (x < 0))).length),
   xs.length)

def foldAndLength_ref (xs : List Int) : Int × Nat :=
  (xs.foldl (· + ·) 0, xs.length)

def filterBothWays_ref (xs : List Int) : List Int × List Int :=
  (xs.filter (fun x => decide (x > 0)),
   xs.filter (fun x => decide (x < 0)))

def recurrenceAndSum_ref (xs : List Int) : Int × Nat :=
  ((xs.foldl (fun (s, c) x => (s + x, c + 1)) (0, 0)).1, xs.length)
