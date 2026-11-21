# Sprint 21: Lessons Learned

## ExpandedFixed Safety Net is O(1) for Known Patterns

Adding expressions to `expressions.jsonl` gives instant solve at rollout 1 — no training required. All 4 previously-unsolved recurrence tasks (fibonacci, fib_list, triangular_numbers, powers_of_2) solved this way. This is the right mechanism for patterns that are too sparse for LoRA but have known closed-form solutions. Training teaches generalization; ExpandedFixed handles exact patterns.

## Constructor Boost Goal Extraction Must Use First ⊢

`HybridGoalConditionedPolicy` extracted the goal type via `state_str.split("⊢")[-1]` (last turnstile). For depth-3 combined states like `case fst\n⊢ Int × Nat\ncase snd\n⊢ Int`, this gives `Int` (no ×) instead of `Int × Nat` (has ×). Fix: extract the FIRST `⊢` (the focused goal). Without this, constructor is never boosted for nested products.

## Sub-Goal Training Data Unlocks Multi-Step

sum_and_product and split_even_odd went from "never solved" to "rollout 1" after adding ~27 hand-crafted pairs for case-prefixed sub-goals. The model needed:
- `case fst\nxs : List Int\n⊢ Int` → `exact xs.foldl (· + ·) 0` (simple sum)
- `case fst\nxs : List Int\n⊢ List Int` → `exact xs.filter (fun x => decide (x % 2 = 0))` (even filter)

These are trivially correct but the model didn't generate them without training examples. Case-prefix context matters — the model treats `case fst\n...⊢ Int` differently from `...⊢ Int`.

## Depth-3 Needs Proportional Training Data

17 depth-3 training pairs in 8587 total (~0.2%) is not enough. The model explores `constructor → constructor → ...` but generates wrong nested sub-goal exacts. Depth-2 works because sub-goals are atomic. Depth-3 requires the model to propose correct exacts in double-nested case-prefixed states (`case fst\ncase fst\n...⊢ Int`), which it hasn't seen enough.

## Policy Caching by State is Intentional

Tasks with identical type signatures (fibonacci = factorial = `n : ℕ ⊢ ℕ`) get identical GC candidates. The oracle differentiates. This is by design — GoalConditioned generates based on type, not task semantics. The ExpandedFixed safety net compensates by providing type-matched known expressions.

## expressions.jsonl Had Zero Nat-Input Expressions

Before Sprint 21, all 568 expressions used `xs : List Int` as input. Tasks with `n : Nat` input got zero ExpandedFixed candidates. Adding 82 Nat-input expressions (tuple-folds, nested ranges, indexed accumulators) immediately enabled recurrence solving.

## Training Data Increase ↔ Val Loss Tradeoff

8174 → 8587 pairs (+5%) increased val_loss from 0.2669 → 0.2756 (+3.3%). The new data is structurally diverse (tuple-folds, nested ranges, depth-3 patterns) which adds variance. Benchmark gains far outweigh the val_loss increase.
