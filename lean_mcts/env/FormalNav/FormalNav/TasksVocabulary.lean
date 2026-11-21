import Mathlib.Tactic
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false
set_option linter.unusedTactic false

/-!
# V3 Vocabulary Validation Tasks (Sprint 26)

10 core tasks + 5 stretch tasks exercising the expanded vocabulary:
- Conditional maps (keep all elements, transform conditionally)
- Foldl patterns (left-to-right accumulation)
- Two-pass composition (aggregate → use aggregate)
- Arithmetic primitives (abs, cubes, negation)
-/

-- ============================================================
-- V3 Core: Vocabulary Validation (10 tasks)
-- ============================================================

-- V3.1: Sum of absolute values (foldl + conditional)
def absSumV3 (xs : List Int) : Int := by sorry

-- V3.2: Sum of cubes 1³ + 2³ + ... + n³ (Nat.rec with cubic step)
def cubeSumV3 (n : Nat) : Nat := by sorry

-- V3.3: Double even elements, keep odds: [1,2,3,4] → [1,4,3,8]
def doubleEvens (xs : List Int) : List Int := by sorry

-- V3.4: ReLU: replace negatives with 0, keep positives
def reluList (xs : List Int) : List Int := by sorry

-- V3.5: Last element or 0 if empty (foldl)
def lastElement (xs : List Int) : Int := by sorry

-- V3.6: Negate the sum: [1,2,3] → -6
def negSum (xs : List Int) : Int := by sorry

-- V3.7: Max minus min (0 for empty): [3,1,5] → 4
def maxMinusMin (xs : List Int) : Int := by sorry

-- V3.8: Deviation from max: [3,1,5] → [-2,-4,0]
def deviations (xs : List Int) : List Int := by sorry

-- V3.9: Clamp to [-5,5]: [7,-8,3] → [5,-5,3]
def clampToRange (xs : List Int) : List Int := by sorry

-- V3.10: Replace negatives with 0: [-1,2,-3] → [0,2,0]
def replaceNeg (xs : List Int) : List Int := by sorry

-- ============================================================
-- V3 Stretch: Compositional Vocabulary (5 tasks)
-- ============================================================

-- V3S.1: Sum of absolute values of positives only
def sumAbsPositives (xs : List Int) : Int := by sorry

-- V3S.2: Normalize: divide each element by max (integer division)
def normalizeToMax (xs : List Int) : List Int := by sorry

-- V3S.3: Count sign changes: [1,-2,3,-4] → 3
def countSignChanges (xs : List Int) : Nat := by sorry

-- V3S.4: Map absolute values: [-3,1,-5] → [3,1,5]
def sortedAbs (xs : List Int) : List Nat := by sorry

-- V3S.5: Sum of squares of positive elements
def sumSquaresPositive (xs : List Int) : Int := by sorry


-- ============================================================
-- Reference solutions
-- ============================================================

-- V3 core refs

def absSumV3_ref (xs : List Int) : Int :=
  xs.foldl (fun acc h => acc + if decide (h ≥ 0) then h else -h) 0

def cubeSumV3_ref (n : Nat) : Nat :=
  Nat.rec 0 (fun k acc => acc + (k + 1) * (k + 1) * (k + 1)) n

def doubleEvens_ref (xs : List Int) : List Int :=
  xs.map (fun h => if decide (h % 2 = 0) then h * 2 else h)

def reluList_ref (xs : List Int) : List Int :=
  xs.map (fun h => max 0 h)

def lastElement_ref (xs : List Int) : Int :=
  xs.foldl (fun _ h => h) 0

def negSum_ref (xs : List Int) : Int :=
  -(xs.foldl (fun a b => a + b) 0)

def maxMinusMin_ref (xs : List Int) : Int :=
  match xs with | [] => 0 | h :: t => t.foldl max h - t.foldl min h

def deviations_ref (xs : List Int) : List Int :=
  let mx := xs.foldl max 0
  xs.map (fun h => h - mx)

def clampToRange_ref (xs : List Int) : List Int :=
  xs.map (fun h => max (-5) (min 5 h))

def replaceNeg_ref (xs : List Int) : List Int :=
  xs.map (fun h => if decide (h < 0) then 0 else h)

-- V3 stretch refs

def sumAbsPositives_ref (xs : List Int) : Int :=
  xs.foldl (fun acc h => if decide (h > 0) then acc + h else acc) 0

def normalizeToMax_ref (xs : List Int) : List Int :=
  let mx := xs.foldl max 0
  if decide (mx = 0) then xs else xs.map (fun h => h / mx)

def countSignChanges_ref (xs : List Int) : Nat :=
  match xs with
  | [] => 0
  | h :: t =>
    (t.foldl (fun (n, prev) curr =>
      let prevSign := if decide (prev > 0) then 1 else if decide (prev < 0) then -1 else (0 : Int)
      let currSign := if decide (curr > 0) then 1 else if decide (curr < 0) then -1 else 0
      (if decide (prevSign ≠ currSign ∧ prevSign ≠ 0 ∧ currSign ≠ 0) then n + 1 else n, curr))
    (0, h)).1

def sortedAbs_ref (xs : List Int) : List Nat :=
  xs.map (fun h => if decide (h ≥ 0) then h.toNat else (-h).toNat)

def sumSquaresPositive_ref (xs : List Int) : Int :=
  xs.foldl (fun acc h => if decide (h > 0) then acc + h * h else acc) 0
