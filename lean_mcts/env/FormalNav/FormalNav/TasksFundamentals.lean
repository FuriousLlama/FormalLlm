import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false
set_option linter.unusedTactic false

/-!
# Fundamental Programming Patterns Benchmark (Sprint 19)

10 tasks that CANNOT be solved by combinator pipelines (filter/map/foldl chains).
Each requires fundamentals: match, List.rec, if/then/else branching, let bindings,
or stateful foldl with tuple accumulators.

## Difficulty gradient
- Easy: stutter, allEven, matchHeadOrSum
- Medium: alternateSign, pairDiff, cumulMax, firstNegIdx
- Hard: secondMax, rle, interleave
-/

-- F1: Duplicate each element: [1,2,3] → [1,1,2,2,3,3]
-- Requires List.rec with double-cons (no combinator does this)
def stutter (xs : List Int) : List Int := by sorry

-- F2: Alternate signs: [1,2,3,4] → [1,-2,3,-4]
-- Requires stateful fold with boolean toggle
def alternateSign (xs : List Int) : List Int := by sorry

-- F3: Consecutive differences: [5,3,1,4] → [2,2,-3]
-- Requires match + stateful fold tracking previous element
def pairDiff (xs : List Int) : List Int := by sorry

-- F4: Second largest element: [3,1,4,1,5] → 4
-- Requires tuple-state fold tracking (best, second)
def secondMax (xs : List Int) : Int := by sorry

-- F5: Head if short, sum if long (if + match + foldl)
-- Requires top-level if/then/else branching
def matchHeadOrSum (xs : List Int) : Int := by sorry

-- F6: All even? Returns 1 if all elements even, 0 otherwise
-- Requires List.rec with conditional short-circuit
def allEven (xs : List Int) : Nat := by sorry

-- F7: Index of first negative: [3,1,-4,2] → 2
-- Requires stateful fold with (index, found) pair
def firstNegIdx (xs : List Int) : Nat := by sorry

-- F8: Cumulative maximum: [3,1,4,1,5] → [3,3,4,4,5]
-- Requires stateful fold tracking running max
def cumulMax (xs : List Int) : List Int := by sorry

-- F9: Run-length encode: [1,1,2,3,3,3] → [(1,2),(2,1),(3,3)]
-- Requires match + stateful fold with triple accumulator
def rle (xs : List Int) : List (Int × Nat) := by sorry

-- F10: Interleave two lists: [1,2,3] [10,20,30] → [1,10,2,20,3,30]
-- Requires simultaneous recursion on two lists
def interleave (xs ys : List Int) : List Int := by sorry


-- ============================================================
-- Reference solutions (for oracle verification)
-- ============================================================

def stutter_ref (xs : List Int) : List Int :=
  List.rec [] (fun h _ acc => h :: h :: acc) xs

def alternateSign_ref (xs : List Int) : List Int :=
  (xs.foldl (fun (acc, pos) h => (acc ++ [if pos then h else -h], !pos)) ([], true)).1

def pairDiff_ref (xs : List Int) : List Int :=
  match xs with
  | [] => []
  | h :: t => (t.foldl (fun (acc, prev) curr => (acc ++ [prev - curr], curr)) ([], h)).1

def secondMax_ref (xs : List Int) : Int :=
  (xs.foldl (fun (best, second) h =>
    if h > best then (h, best)
    else if h > second then (best, h)
    else (best, second)) (0, 0)).2

def matchHeadOrSum_ref (xs : List Int) : Int :=
  if xs.length > 2 then xs.foldl (fun a b => a + b) 0
  else match xs with | [] => 0 | h :: _ => h

def allEven_ref (xs : List Int) : Nat :=
  List.rec 1 (fun h _ acc => if decide (h % 2 = 0) then acc else 0) xs

def firstNegIdx_ref (xs : List Int) : Nat :=
  (xs.foldl (fun (idx, found) h =>
    if found then (idx, true)
    else if decide (h < 0) then (idx, true)
    else (idx + 1, false)) (0, false)).1

def cumulMax_ref (xs : List Int) : List Int :=
  (xs.foldl (fun (acc, mx) h =>
    let newMax := if h > mx then h else mx
    (acc ++ [newMax], newMax)) ([], 0)).1

def rle_ref (xs : List Int) : List (Int × Nat) :=
  match xs with
  | [] => []
  | h :: t =>
    let (groups, cur, cnt) := t.foldl
      (fun (groups, cur, cnt) x =>
        if x == cur then (groups, cur, cnt + 1)
        else (groups ++ [(cur, cnt)], x, 1))
      ([], h, 1)
    groups ++ [(cur, cnt)]

def interleave_ref (xs ys : List Int) : List Int :=
  match xs, ys with
  | [], _ => ys
  | _, [] => xs
  | h1 :: t1, h2 :: t2 => h1 :: h2 :: interleave_ref t1 t2
