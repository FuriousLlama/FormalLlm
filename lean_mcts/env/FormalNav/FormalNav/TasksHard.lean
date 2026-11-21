import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

/-!
# Hard Benchmark Tasks (Sprint 12)

5 harder tasks requiring genuine compositional reasoning.
These CANNOT be solved by FixedPolicy (not in the baked-in tactic list).

## Categories
- **multi_type**: Different input type signatures
- **multi_arg**: Multiple input arguments
- **product_return**: Product return types
- **multi_step**: Multi-step pipelines
- **filtermap**: filterMap with option handling
-/

-- Task 11: multi_type — flatten a list of lists
-- Reference: xss.flatten
def flattenLists (xss : List (List Int)) : List Int := by sorry

-- Task 12: multi_arg — elementwise sum of two lists
-- Reference: xs.zipWith (· + ·) ys
def zipSum (xs : List Int) (ys : List Int) : List Int := by sorry

-- Task 13: product_return — partition into positives and non-positives
-- Reference: xs.partition (fun x => decide (x > 0))
def partitionPositive (xs : List Int) : List Int × List Int := by sorry

-- Task 14: multi_step — deduplicate and count occurrences
-- Reference: xs.eraseDups.map (fun x => (x, xs.filter (· == x) |>.length))
def dedupCount (xs : List Int) : List (Int × Nat) := by sorry

-- Task 15: filtermap — keep only positive elements, cast to Nat
-- Reference: xs.filterMap (fun x => if x > 0 then some x.toNat else none)
def filterMapAbs (xs : List Int) : List Nat := by sorry

/-!
## Reference solutions (for verification)
-/

def flattenLists_ref (xss : List (List Int)) : List Int :=
  xss.flatten

def zipSum_ref (xs : List Int) (ys : List Int) : List Int :=
  xs.zipWith (· + ·) ys

def partitionPositive_ref (xs : List Int) : List Int × List Int :=
  xs.partition (fun x => decide (x > 0))

def dedupCount_ref (xs : List Int) : List (Int × Nat) :=
  xs.eraseDups.map (fun x => (x, (xs.filter (· == x)).length))

def filterMapAbs_ref (xs : List Int) : List Nat :=
  xs.filterMap (fun x => if decide (x > 0) then some x.toNat else none)
