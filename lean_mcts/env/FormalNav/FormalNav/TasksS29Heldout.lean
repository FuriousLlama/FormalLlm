import Mathlib.Tactic
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false
set_option linter.unusedTactic false

/-!
# Sprint 29 Held-Out Hard Tasks

6 compositional tasks modeled on Sprint 27's 16 classified failure patterns,
frozen in Phase 0.5 **before** library mining begins. These are the new
hard-tier held-out set for the S29 primary metric — zero overlap with any
training data, verified by the Phase 1.4 leakage check.

Distribution mirrors Sprint 27's diagnostic structure:
- 2 Tier A: compositional wiring gap (index-tracking fold, pairwise pipeline)
- 2 Tier B: tuple accumulator with field interaction (triple-field fold, dual-state Kadane)
- 1 Tier C: structural recursion on two lists (multi-arg merge)
- 1 Tier D: multi-arg + index arithmetic + compositional (sliding window)

No task is constructible as a single library function call. Every solution
requires composing at least 3 primitives, and 4 of the 6 require a tuple
state or recursive decomposition the S27 system could not produce.

Frozen behind git tag `s29-heldout-frozen`. Any post-tag edit invalidates
the S29 primary metric and requires documenting the change in
`docs/implementation/sprint29/sprint29_status.md`.
-/

-- ============================================================
-- Tier A: Compositional wiring gap
-- ============================================================

-- A1: Sum of elements at 0-indexed odd positions.
-- Failure mode mirror: mapWithIndex (index-tracking fold)
def sumAtOddIndices (xs : List Int) : Int := by sorry

-- A2: Pairwise differences between consecutive elements: [b₀-a₀, b₁-a₁, ...]
-- Failure mode mirror: removeDuplicatesKeepLast (multi-op pipeline)
def zipDiff (xs : List Int) : List Int := by sorry

-- ============================================================
-- Tier B: Tuple accumulator with field interaction
-- ============================================================

-- B1: Length of longest strictly-increasing contiguous subsequence.
-- Failure mode mirror: longestPlateau (triple-field fold (best, cur, prev))
def longestIncreasingRun (xs : List Int) : Nat := by sorry

-- B2: Maximum product of any contiguous subarray.
-- Failure mode mirror: maxSubarraySum (Kadane), but with sign-flip interaction
-- requiring a dual (maxEnd, minEnd) state because negatives swap rankings.
def maxSubarrayProduct (xs : List Int) : Int := by sorry

-- ============================================================
-- Tier C: Structural recursion, multi-arg
-- ============================================================

-- C1: Merge two descending-sorted lists into a single descending-sorted list.
-- Failure mode mirror: interleaveZip (two-list recursion), insertSorted
-- (conditional step), with comparison direction flipped so stdlib merge doesn't
-- directly solve it.
def mergeSortedDesc (xs ys : List Int) : List Int := by sorry

-- ============================================================
-- Tier D: Multi-arg + index arithmetic + compositional
-- ============================================================

-- D1: Sliding k-element window sums. For xs of length n ≥ k ≥ 1, returns
-- n-k+1 values where out[i] = sum of xs[i..i+k-1]. Returns [] if k = 0 or k > n.
-- Failure mode mirror: chunksOf (multi-arg + index arithmetic + nested fold)
def windowSums (k : Nat) (xs : List Int) : List Int := by sorry
