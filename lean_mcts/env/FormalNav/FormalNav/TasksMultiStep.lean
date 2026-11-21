import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false

/-!
# Multi-Step Benchmark Tasks (Sprint 20)

5 tasks that REQUIRE `constructor` + sub-goal solving (no single `exact` works
because each component of the product type needs different logic).

## Key pattern
1. `constructor` splits `⊢ A × B` into `case fst ⊢ A` and `case snd ⊢ B`
2. Each sub-goal is solved with a different `exact` expression

## Why these can't be single-step
Each task returns a product type where left and right components use
different operations (different folds, different filters, different types).
-/

-- M1: sum and product of list elements
-- Reference: constructor; exact xs.foldl (· + ·) 0; exact xs.foldl (· * ·) 1
def sumAndProduct (xs : List Int) : Int × Int := by sorry

-- M2: count positives and negatives (different predicates)
-- Reference: constructor; exact (xs.filter (fun x => decide (x > 0))).length; exact (xs.filter (fun x => decide (x < 0))).length
def countPosNeg (xs : List Int) : Nat × Nat := by sorry

-- M3: split into even and odd elements
-- Reference: constructor; exact xs.filter (fun x => decide (x % 2 = 0)); exact xs.filter (fun x => decide (x % 2 ≠ 0))
def splitEvenOdd (xs : List Int) : List Int × List Int := by sorry

-- M4: minimum and maximum via folds
-- Reference: constructor; exact xs.foldl min 0; exact xs.foldl max 0
def minAndMax (xs : List Int) : Int × Int := by sorry

-- M5: length and sum (different return types per component)
-- Reference: constructor; exact xs.length; exact xs.foldl (· + ·) 0
def lengthAndSum (xs : List Int) : Nat × Int := by sorry


-- ============================================================
-- Reference solutions (for oracle verification)
-- ============================================================

def sumAndProduct_ref (xs : List Int) : Int × Int :=
  (xs.foldl (· + ·) 0, xs.foldl (· * ·) 1)

def countPosNeg_ref (xs : List Int) : Nat × Nat :=
  ((xs.filter (fun x => decide (x > 0))).length,
   (xs.filter (fun x => decide (x < 0))).length)

def splitEvenOdd_ref (xs : List Int) : List Int × List Int :=
  (xs.filter (fun x => decide (x % 2 = 0)),
   xs.filter (fun x => decide (x % 2 ≠ 0)))

def minAndMax_ref (xs : List Int) : Int × Int :=
  (xs.foldl min 0, xs.foldl max 0)

def lengthAndSum_ref (xs : List Int) : Nat × Int :=
  (xs.length, xs.foldl (· + ·) 0)
