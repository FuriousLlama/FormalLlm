import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false

/-!
# Recurrence / Stateful-Fold Benchmark Tasks (Sprint 17)

6 tasks requiring tuple-accumulator folds, Fibonacci-class recurrences,
and stateful iteration patterns. These exercise patterns absent from
the Sprint 16 training data.

## Key patterns
- **Tuple-accumulator fold**: `foldl (fun (a, b) _ => ...) (init1, init2)`
- **Indexed fold**: `(List.range n).foldl (fun acc i => ...)`
- **Nested range**: `(List.range n).map (fun i => ... (List.range i) ...)`
-/

-- R1: nth Fibonacci number (0-indexed)
-- Reference: ((List.range n).foldl (fun (a, b) _ => (b, a + b)) (0, 1)).1
def fibonacci (n : Nat) : Nat := by sorry

-- R2: first n Fibonacci numbers
-- Reference: ((List.range n).foldl (fun (acc, ab) _ => (acc ++ [ab.1], (ab.2, ab.1 + ab.2))) ([], (0, 1))).1
def fibList (n : Nat) : List Nat := by sorry

-- R3: factorial
-- Reference: (List.range n).foldl (fun acc i => acc * (i + 1)) 1
def factorial (n : Nat) : Nat := by sorry

-- R4: first n triangular numbers
-- Reference: (List.range n).map (fun i => (List.range (i + 1)).foldl (· + ·) 0)
def triangularNumbers (n : Nat) : List Nat := by sorry

-- R5: running sum and count (tuple accumulator on list input)
-- Reference: xs.foldl (fun (s, c) x => (s + x, c + 1)) (0, 0)
def runningParts (xs : List Int) : Int × Nat := by sorry

-- R6: powers of 2 (first n)
-- Reference: (List.range n).map (fun i => (List.range i).foldl (fun a _ => a * 2) 1)
def powersOf2 (n : Nat) : List Nat := by sorry


-- ============================================================
-- Reference solutions (for oracle verification)
-- ============================================================

def fibonacci_ref (n : Nat) : Nat :=
  ((List.range n).foldl (fun (a, b) _ => (b, a + b)) (0, 1)).1

def fibList_ref (n : Nat) : List Nat :=
  ((List.range n).foldl
    (fun (acc, ab) _ => (acc ++ [ab.1], (ab.2, ab.1 + ab.2)))
    ([], (0, 1))).1

def factorial_ref (n : Nat) : Nat :=
  (List.range n).foldl (fun acc i => acc * (i + 1)) 1

def triangularNumbers_ref (n : Nat) : List Nat :=
  (List.range n).map (fun i => (List.range (i + 1)).foldl (· + ·) 0)

def runningParts_ref (xs : List Int) : Int × Nat :=
  xs.foldl (fun (s, c) x => (s + x, c + 1)) (0, 0)

def powersOf2_ref (n : Nat) : List Nat :=
  (List.range n).map (fun i => (List.range i).foldl (fun a _ => a * 2) 1)
