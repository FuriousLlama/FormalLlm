import Mathlib.Tactic
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false
set_option linter.unusedTactic false

/-!
# Diagnostic Benchmark Tasks (Sprint 27)

18 tasks in 4 tiers probing generalization beyond template matching:
- Tier A: Compositional generalization (5 tasks)
- Tier B: Algorithmic reasoning (5 tasks)
- Tier C: Structural abstraction (5 tasks)
- Tier D: Litmus test (3 tasks)

Every primitive needed is already in the system's vocabulary.
Solutions are short (1-5 lines). The barrier is composition, not vocabulary.
No task was designed alongside a skeleton — this is the key difference from S24-S26.
-/

-- ============================================================
-- Tier A: Compositional Generalization (5 tasks)
-- ============================================================

-- A1: Second-smallest element (min-direction tuple accumulator)
def secondSmallest (xs : List Int) : Int := by sorry

-- A2: Multiply each element by its 1-based index
def mapWithIndex (xs : List Int) : List Int := by sorry

-- A3: Take prefix while strictly increasing
def prefixWhileIncreasing (xs : List Int) : List Int := by sorry

-- A4: Remove duplicates keeping last occurrence
def removeDuplicatesKeepLast (xs : List Int) : List Int := by sorry

-- A5: Dot product of list with itself (sum of squares)
def dotProductSelf (xs : List Int) : Int := by sorry

-- ============================================================
-- Tier B: Algorithmic Reasoning (5 tasks)
-- ============================================================

-- B1: Maximum contiguous subarray sum (Kadane's algorithm)
def maxSubarraySum (xs : List Int) : Int := by sorry

-- B2: Length of longest run of identical values
def longestPlateau (xs : List Int) : Nat := by sorry

-- B3: 3-way partition around pivot
def dutchFlag (pivot : Int) (xs : List Int) : List Int := by sorry

-- B4: Boyer-Moore majority vote
def majorityElement (xs : List Int) : Int := by sorry

-- B5: First index where left-sum equals right-sum (-1 if none)
def equilibriumIndex (xs : List Int) : Int := by sorry

-- ============================================================
-- Tier C: Structural Abstraction (5 tasks)
-- ============================================================

-- C1: Split list into groups of n
def chunksOf (n : Nat) (xs : List Int) : List (List Int) := by sorry

-- C2: Matrix transpose
def diagTranspose (xss : List (List Int)) : List (List Int) := by sorry

-- C3: Insert element at sorted position
def insertSorted (x : Int) (xs : List Int) : List Int := by sorry

-- C4: Generate list by repeated function application
def unfoldN (n : Nat) (f : Int → Int) (seed : Int) : List Int := by sorry

-- C5: Interleave two lists element-by-element
def interleaveZip (xs : List Int) (ys : List Int) : List Int := by sorry

-- ============================================================
-- Tier D: Litmus Test (3 tasks)
-- ============================================================

-- D1: Sort by repeated insertion into sorted accumulator
def diagInsertionSort (xs : List Int) : List Int := by sorry

-- D2: Stack-based evaluator (push positive, 0 = pop two and add)
def evalStack (xs : List Int) : Int := by sorry

-- D3: All contiguous sublists
def allSublists (xs : List Int) : List (List Int) := by sorry


-- ============================================================
-- Reference solutions
-- ============================================================

-- Tier A refs

def secondSmallest_ref (xs : List Int) : Int :=
  match xs with
  | [] => 0
  | [x] => x
  | h :: t =>
    (t.foldl (fun (lo, lo2) x =>
      if decide (x < lo) then (x, lo)
      else if decide (x < lo2) then (lo, x)
      else (lo, lo2)) (h, h)).2

def mapWithIndex_ref (xs : List Int) : List Int :=
  (xs.foldl (fun (acc, idx) v => (acc ++ [v * idx], idx + 1)) ([], (1 : Int))).1

def prefixWhileIncreasing_ref (xs : List Int) : List Int :=
  match xs with
  | [] => []
  | h :: t =>
    let r := t.foldl (fun (acc, prev, stop) curr =>
      if decide (stop = (1 : Nat)) then (acc, prev, 1)
      else if decide (curr > prev) then (acc ++ [curr], curr, 0)
      else (acc, prev, (1 : Nat))) (([] : List Int), h, (0 : Nat))
    h :: r.1

def removeDuplicatesKeepLast_ref (xs : List Int) : List Int :=
  (xs.reverse.eraseDups).reverse

def dotProductSelf_ref (xs : List Int) : Int :=
  xs.foldl (fun acc h => acc + h * h) 0

-- Tier B refs

def maxSubarraySum_ref (xs : List Int) : Int :=
  (xs.foldl (fun (best, current) h =>
    let c := max h (current + h)
    (max best c, c)) ((0 : Int), (0 : Int))).1

def longestPlateau_ref (xs : List Int) : Nat :=
  match xs with
  | [] => 0
  | h :: t =>
    let r := t.foldl (fun (best, cur, prev) x =>
      if decide (x = prev) then (max best (cur + 1), cur + 1, x)
      else (best, 1, x)) ((1 : Nat), (1 : Nat), h)
    max r.1 r.2.1

def dutchFlag_ref (pivot : Int) (xs : List Int) : List Int :=
  xs.filter (fun x => decide (x < pivot))
  ++ xs.filter (fun x => decide (x = pivot))
  ++ xs.filter (fun x => decide (x > pivot))

def majorityElement_ref (xs : List Int) : Int :=
  (xs.foldl (fun (cand, count) h =>
    if decide (count = (0 : Int)) then (h, (1 : Int))
    else if decide (cand = h) then (cand, count + 1)
    else (cand, count - 1)) ((0 : Int), (0 : Int))).1

def equilibriumIndex_ref (xs : List Int) : Int :=
  let total := xs.foldl (· + ·) 0
  (xs.foldl (fun (idx, leftSum, result) h =>
    if decide (result ≥ 0) then (idx + 1, leftSum, result)
    else if decide (leftSum = total - leftSum - h) then (idx + 1, leftSum + h, idx)
    else (idx + 1, leftSum + h, result)) ((0 : Int), (0 : Int), (-1 : Int))).2.2

-- Tier C refs

def chunksOf_ref (n : Nat) (xs : List Int) : List (List Int) :=
  if decide (n = 0) then [xs]
  else
    List.range ((xs.length + n - 1) / n) |>.map (fun i => (xs.drop (i * n)).take n)

def diagTranspose_ref (xss : List (List Int)) : List (List Int) :=
  match xss with
  | [] => []
  | row :: _ =>
    List.range row.length |>.map (fun i => xss.filterMap (fun r => r[i]?))

def insertSorted_ref (x : Int) (xs : List Int) : List Int :=
  match xs with
  | [] => [x]
  | h :: t => if decide (x ≤ h) then x :: h :: t else h :: insertSorted_ref x t

def unfoldN_ref (n : Nat) (f : Int → Int) (seed : Int) : List Int :=
  let rec go : Nat → Int → List Int → List Int
    | 0, _, acc => acc
    | k + 1, v, acc => go k (f v) (acc ++ [v])
  go n seed []

def interleaveZip_ref (xs : List Int) (ys : List Int) : List Int :=
  let paired := (xs.zip ys).foldl (fun acc (a, b) => acc ++ [a, b]) []
  paired ++ xs.drop ys.length ++ ys.drop xs.length

-- Tier D refs

def diagInsertionSort_ref (xs : List Int) : List Int :=
  xs.foldl (fun sorted h =>
    sorted.filter (fun x => decide (x ≤ h)) ++ [h]
    ++ sorted.filter (fun x => decide (x > h))) []

def evalStack_ref (xs : List Int) : Int :=
  let stack := xs.foldl (fun stk h =>
    if decide (h = (0 : Int)) then
      match stk with
      | a :: b :: rest => (a + b) :: rest
      | _ => stk
    else h :: stk) ([] : List Int)
  match stack with
  | v :: _ => v
  | [] => 0

def allSublists_ref (xs : List Int) : List (List Int) :=
  let n := xs.length
  (List.range n).foldl (fun acc i =>
    acc ++ (List.range (n - i)).map (fun len =>
      (xs.drop i).take (len + 1))) []
