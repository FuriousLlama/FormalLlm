import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false
set_option linter.unusedTactic false

/-!
# Stretch Benchmark (Sprint 25)

20 tasks of increasing difficulty to find where the system breaks.

## Tiers
- S1 (5): Novel step functions — single List.rec/Nat.rec, step NOT in templates
- S2 (5): Composition — multi-step library compositions, non-standard patterns
- S3 (5): Multi-pass / complex state — need intermediate computations
- S4 (5): Way harder — need match, foldl with complex state, or algorithms
-/

-- ============================================================
-- S1: Novel Step Functions (single eliminator, novel step)
-- ============================================================

-- S1.1: Alternating sum: 1 - 2 + 3 - 4 = -2
def alternatingSum (xs : List Int) : Int := by sorry

-- S1.2: Sum of even elements only
def sumEvenOnly (xs : List Int) : Int := by sorry

-- S1.3: Product of positive elements only (1 if none positive)
def productPositive (xs : List Int) : Int := by sorry

-- S1.4: Absolute values of negative elements only: [-3,1,-5] → [3,5]
def absOfNegatives (xs : List Int) : List Int := by sorry

-- S1.5: Sum of cubes: 1³ + 2³ + ... + n³
def sumCubes (n : Nat) : Nat := by sorry

-- ============================================================
-- S2: Composition / Non-standard Patterns
-- ============================================================

-- S2.1: Last element or 0 if empty (needs left fold, not right)
def lastOrDefault (xs : List Int) : Int := by sorry

-- S2.2: Double only positive elements, keep negatives: [-1,2,-3,4] → [-1,4,-3,8]
def doublePositives (xs : List Int) : List Int := by sorry

-- S2.3: Clamp each element to [-5, 5] range
def clampValues (xs : List Int) : List Int := by sorry

-- S2.4: Replace negatives with zero: [-1,2,-3] → [0,2,0]
def replaceNegZero (xs : List Int) : List Int := by sorry

-- S2.5: Negate the sum: [1,2,3] → -6
def negateSum (xs : List Int) : Int := by sorry

-- ============================================================
-- S3: Multi-pass / Complex State
-- ============================================================

-- S3.1: Each element minus the maximum: [3,1,5] → [-2,-4,0]
def deviationFromMax (xs : List Int) : List Int := by sorry

-- S3.2: Count unique elements: [1,2,2,3,1] → 3
def countDistinct (xs : List Int) : Nat := by sorry

-- S3.3: Is sorted ascending? 1 if yes, 0 if no
def isSortedAsc (xs : List Int) : Nat := by sorry

-- S3.4: Max minus min (0 for empty): [3,1,5] → 4
def maxMinDiff (xs : List Int) : Int := by sorry

-- S3.5: Sum of absolute values: [3,-1,-4,2] → 10
def sumAbsValues (xs : List Int) : Int := by sorry

-- ============================================================
-- S4: Way Harder
-- ============================================================

-- S4.1: Length of longest non-decreasing prefix: [1,2,3,1,2] → 3
def longestNondecPrefix (xs : List Int) : Nat := by sorry

-- S4.2: Remove consecutive duplicates: [1,1,2,3,3,2] → [1,2,3,2]
def compressConsecutive (xs : List Int) : List Int := by sorry

-- S4.3: Count peaks (element > both neighbors): [1,3,2,4,1] → 2
def countPeaks (xs : List Int) : Nat := by sorry

-- S4.4: Sliding window max (size 2): [3,1,4,1,5] → [3,4,4,5]
def slidingMax2 (xs : List Int) : List Int := by sorry

-- S4.5: Count inversions (i < j but xs[i] > xs[j]): [3,1,2] → 2
def countInversions (xs : List Int) : Nat := by sorry


-- ============================================================
-- Reference solutions
-- ============================================================

-- S1 refs

def alternatingSum_ref (xs : List Int) : Int :=
  List.rec 0 (fun h _ acc => h - acc) xs

def sumEvenOnly_ref (xs : List Int) : Int :=
  List.rec 0 (fun h _ acc => if decide (h % 2 = 0) then acc + h else acc) xs

def productPositive_ref (xs : List Int) : Int :=
  List.rec 1 (fun h _ acc => if decide (h > 0) then acc * h else acc) xs

def absOfNegatives_ref (xs : List Int) : List Int :=
  List.rec [] (fun h _ acc => if decide (h < 0) then (-h) :: acc else acc) xs

def sumCubes_ref (n : Nat) : Nat :=
  Nat.rec 0 (fun k acc => acc + (k + 1) * (k + 1) * (k + 1)) n

-- S2 refs

def lastOrDefault_ref (xs : List Int) : Int :=
  xs.foldl (fun _ h => h) 0

def doublePositives_ref (xs : List Int) : List Int :=
  xs.map (fun h => if decide (h > 0) then h * 2 else h)

def clampValues_ref (xs : List Int) : List Int :=
  xs.map (fun h => max (-5) (min 5 h))

def replaceNegZero_ref (xs : List Int) : List Int :=
  xs.map (fun h => if decide (h < 0) then 0 else h)

def negateSum_ref (xs : List Int) : Int :=
  -(xs.foldl (fun a b => a + b) 0)

-- S3 refs

def deviationFromMax_ref (xs : List Int) : List Int :=
  let mx := xs.foldl max 0
  xs.map (fun h => h - mx)

def countDistinct_ref (xs : List Int) : Nat :=
  xs.eraseDups.length

def isSortedAsc_ref (xs : List Int) : Nat :=
  if decide (xs = List.mergeSort xs) then 1 else 0

def maxMinDiff_ref (xs : List Int) : Int :=
  match xs with
  | [] => 0
  | h :: t => t.foldl max h - t.foldl min h

def sumAbsValues_ref (xs : List Int) : Int :=
  xs.foldl (fun acc h => acc + if decide (h ≥ 0) then h else -h) 0

-- S4 refs

def longestNondecPrefix_ref (xs : List Int) : Nat :=
  match xs with
  | [] => 0
  | h :: t =>
    (t.foldl (fun (len, prev, done) curr =>
      if done then (len, prev, done)
      else if decide (curr ≥ prev) then (len + 1, curr, false)
      else (len, curr, true)) (1, h, false)).1

def compressConsecutive_ref (xs : List Int) : List Int :=
  match xs with
  | [] => []
  | h :: t =>
    let (acc, _) := t.foldl (fun (acc, prev) curr =>
      if curr == prev then (acc, prev)
      else (acc ++ [curr], curr)) ([h], h)
    acc

def countPeaks_ref (xs : List Int) : Nat :=
  match xs with
  | [] => 0
  | [_] => 0
  | _ =>
    let triples := (xs.zip xs.tail).zip xs.tail.tail
    (triples.filter (fun ((a, b), c) => decide (b > a ∧ b > c))).length

def slidingMax2_ref (xs : List Int) : List Int :=
  (xs.zip xs.tail).map (fun (a, b) => max a b)

def countInversions_ref (xs : List Int) : Nat :=
  let pairs := xs.zipIdx
  (pairs.foldl (fun acc (a, i) =>
    acc + (pairs.filter (fun (b, j) => decide (j > i ∧ a > b))).length) 0)
