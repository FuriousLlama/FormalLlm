import Mathlib.Tactic

/-!
# Minimal Synthesis Task

Used for integration testing of LeanDojo + MCTS loop.
Goal: `n : Nat ⊢ Nat`
Solution: `exact n`
-/

def synth_identity (n : Nat) : Nat := by sorry

theorem test_eq (n : Nat) : n = n := by sorry
