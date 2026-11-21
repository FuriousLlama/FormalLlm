import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false
set_option linter.unusedTactic false

/-!
# Recursive Programs Benchmark (Sprint 25)

15 tasks requiring structural recursion via `List.rec` or `Nat.rec` eliminators.
These tasks test the recursion skeleton expansion: deterministic type analysis
→ (base, step) candidate generation → oracle verification.

## Tiers
- R1 (5): Simple `List.rec` with arithmetic step functions
- R2 (5): `List.rec` producing lists (right-fold cons builds correct order)
- R3 (5): `Nat.rec` patterns (natural number recursion)

## Key design choice
All solutions use `List.rec`/`Nat.rec` eliminators, NOT `match` + self-reference.
Eliminators are termination-free (Lean kernel accepts without decreasing-argument proof).

`List.rec` is a right fold: `List.rec base f [a,b,c] = f a [b,c] (f b [c] (f c [] base))`.
With `fun h _ acc => h :: acc`, cons builds lists in correct left-to-right order.
-/

-- ============================================================
-- R1: Simple Recursion (List.rec with arithmetic step)
-- ============================================================

-- R1.1: Sum only positive elements
def sumPositive (xs : List Int) : Int := by sorry

-- R1.2: Count elements < 0
def countNegative (xs : List Int) : Nat := by sorry

-- R1.3: Return first element or 0 if empty
def firstOrZero (xs : List Int) : Int := by sorry

-- R1.4: Product of absolute values
def productAbs (xs : List Int) : Int := by sorry

-- R1.5: Maximum element, 0 if empty
def maxOrZero (xs : List Int) : Int := by sorry

-- ============================================================
-- R2: List-Building Recursion (List.rec producing lists)
-- ============================================================

-- R2.1: Keep only positive elements (no library filter)
def keepPositive (xs : List Int) : List Int := by sorry

-- R2.2: Duplicate each element: [1,2,3] → [1,1,2,2,3,3]
def stutterRec (xs : List Int) : List Int := by sorry

-- R2.3: Negate each element via recursion: [1,-2,3] → [-1,2,-3]
def negateAll (xs : List Int) : List Int := by sorry

-- R2.4: Square each element via recursion: [1,-2,3] → [1,4,9]
def squareAll (xs : List Int) : List Int := by sorry

-- R2.5: Keep only even elements
def keepEven (xs : List Int) : List Int := by sorry

-- ============================================================
-- R3: Nat Recursion (Nat.rec patterns)
-- ============================================================

-- R3.1: n! (factorial)
def factorialRec (n : Nat) : Nat := by sorry

-- R3.2: Triangular number: 1 + 2 + ... + n
def triangularRec (n : Nat) : Nat := by sorry

-- R3.3: 2^n
def powerOfTwo (n : Nat) : Nat := by sorry

-- R3.4: n^2 via repeated addition: 1+3+5+...+(2n-1)
def squareNat (n : Nat) : Nat := by sorry

-- R3.5: Sum 1 + 2 + ... + n (same as triangular, alternate base)
def countDownSum (n : Nat) : Nat := by sorry


-- ============================================================
-- Reference solutions (for oracle verification)
-- ============================================================

def sumPositive_ref (xs : List Int) : Int :=
  List.rec 0 (fun h _ acc => if decide (h > 0) then acc + h else acc) xs

def countNegative_ref (xs : List Int) : Nat :=
  List.rec 0 (fun h _ acc => if decide (h < 0) then acc + 1 else acc) xs

def firstOrZero_ref (xs : List Int) : Int :=
  List.rec 0 (fun h _ _ => h) xs

def productAbs_ref (xs : List Int) : Int :=
  List.rec 1 (fun h _ acc => acc * (if decide (h < 0) then -h else h)) xs

def maxOrZero_ref (xs : List Int) : Int :=
  List.rec 0 (fun h _ acc => if decide (h > acc) then h else acc) xs

def keepPositive_ref (xs : List Int) : List Int :=
  List.rec [] (fun h _ acc => if decide (h > 0) then h :: acc else acc) xs

def stutterRec_ref (xs : List Int) : List Int :=
  List.rec [] (fun h _ acc => h :: h :: acc) xs

def negateAll_ref (xs : List Int) : List Int :=
  List.rec [] (fun h _ acc => (-h) :: acc) xs

def squareAll_ref (xs : List Int) : List Int :=
  List.rec [] (fun h _ acc => (h * h) :: acc) xs

def keepEven_ref (xs : List Int) : List Int :=
  List.rec [] (fun h _ acc => if decide (h % 2 = 0) then h :: acc else acc) xs

def factorialRec_ref (n : Nat) : Nat :=
  Nat.rec 1 (fun k acc => acc * (k + 1)) n

def triangularRec_ref (n : Nat) : Nat :=
  Nat.rec 0 (fun k acc => acc + (k + 1)) n

def powerOfTwo_ref (n : Nat) : Nat :=
  Nat.rec 1 (fun _ acc => acc * 2) n

def squareNat_ref (n : Nat) : Nat :=
  Nat.rec 0 (fun k acc => acc + 2 * k + 1) n

def countDownSum_ref (n : Nat) : Nat :=
  Nat.rec 0 (fun k acc => acc + k + 1) n
