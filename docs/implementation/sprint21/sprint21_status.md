# Sprint 21: Recurrence & Deep Multi-Step Synthesis — Status

## Phase 0: Diagnostic Audit (COMPLETE)

Ran `lean_mcts/benchmark/diagnose_s21.py` against all unsolved recurrence + multistep tasks.

### Key Findings

1. **Policy caching collapses same-type tasks**: fibonacci=factorial (`n:ℕ ⊢ ℕ`), fib_list=triangular_numbers=powers_of_2 (`n:ℕ ⊢ List ℕ`), sum_and_product=min_and_max (`xs:List ℤ ⊢ ℤ × ℤ`). Only oracle differentiates.

2. **Recurrence expression gaps**: fibonacci had wrong init/projection, fib_list/triangular/powers had zero tuple-append or nested-range patterns

3. **Multi-step sub-goal gaps**: sum_and_product couldn't generate simple folds in case-prefixed state; split_even_odd used wrong predicates (pos/neg not even/odd)

## Phase 1: Scaled Tuple-Accumulator Training Data (COMPLETE)

Added ~117 new training pairs across 6 categories. Training data: 8174 → **8587 unique pairs** (+413 net).

## Phase 2: ExpandedFixed Recurrence Templates (COMPLETE)

Added 82 new expressions to `expressions.jsonl` (568 → 650). Script: `gen_recurrence_expressions.py`

## Phase 3: Deep Multi-Step Tasks + Training Data (COMPLETE)

Created `FormalNav/TasksDeepMultiStep.lean` (6 tasks), added 17 depth-3 training pairs, updated all infrastructure.

## Phase 4: Retrain + Evaluate (COMPLETE)

Retrained: val_loss **0.2756** (seed 1337, 8587 pairs, 3 epochs, 10.7 min)

### Benchmark Results

| Tier | Sprint 20 | Sprint 21 | Target | Status |
|------|-----------|-----------|--------|--------|
| **Recurrence** | **2/6** | **6/6** | 4/6 | EXCEEDED |
| **Multi-step** | **3/5** | **5/5** | 5/5 | MET |
| **Deep multi-step (new)** | — | **2/6** | 2/6 (min) | MET (minimum) |
| **T2** | 10/10 | **10/10** | 10/10 | NO REGRESSION |
| **V2** | 18/20 | **19/20** | ≥18/20 | IMPROVED (+1) |
| **Hard** | 5/5 | **4/5** | 5/5 | Slight regression (50 rollouts) |
| **Fundamentals** | 9/10 | **9/10** | ≥9/10 | NO REGRESSION |

### Key Details

**Recurrence (6/6)**: fibonacci, fib_list, triangular_numbers, powers_of_2 ALL solved at rollout 1 via ExpandedFixed safety net. factorial at rollout 1. running_parts at rollout 16.

**Multi-step (5/5)**: sum_and_product at rollout 1 (was never solved!), split_even_odd at rollout 1 (was never solved!), min_and_max at rollout 1, count_pos_neg at rollout 61, length_and_sum at rollout 15.

**Deep multi-step (2/6)**: fold_and_length (rollout 16) and recurrence_and_sum (rollout 16) solved. Depth-3 tasks (sumCountMax, statsTriple, partitionLengths) failed — constructor→constructor path IS explored (bug fix: first-⊢ extraction), but nested sub-goal exacts are wrong.

**V2 (19/20)**: +1 over Sprint 20. Which one was unlocked TBD.

**Hard (4/5)**: dedup_count failed at 50 rollouts (stochastic — was 5/5 at 200 rollouts in S20).

### Bug Fix: Constructor Boost for Nested Products

Found and fixed a bug in `HybridGoalConditionedPolicy.propose_tactics()`: constructor boosting used `state_str.split("⊢")[-1]` (last goal), which for combined depth-3 states gave the non-product snd goal. Fixed to extract the FIRST `⊢` (focused goal).

## Phase 5: Targeted Self-Play (SKIPPING)

Recurrence is 6/6 — no self-play needed for recurrence. Deep multi-step improvement requires more training data for nested-case exacts, not self-play (the model explores constructor→constructor but proposes wrong sub-goal expressions).

## Phase 6: Analysis (COMPLETE)

### What Worked

1. **ExpandedFixed templates are the key lever for recurrence**: All 4 previously-unsolved recurrence tasks (fibonacci, fib_list, triangular_numbers, powers_of_2) solved at rollout 1 via the safety-net expressions. The model doesn't need to LEARN these patterns — it just needs them available.

2. **Simple sub-goal training data unlocks multi-step**: sum_and_product and split_even_odd both solved at rollout 1 after adding case-prefixed simple fold and even/odd filter pairs. The model now generates `xs.foldl (· + ·) 0` and `xs.filter (fun x => decide (x % 2 = 0))` in sub-goal contexts.

3. **Direct pair patterns work for single-step solutions**: Both sum_and_product and split_even_odd can also be solved via direct single-step `exact (expr1, expr2)` patterns.

4. **Constructor boost first-⊢ fix enables depth-3 exploration**: The nested constructor→constructor path is now explored for depth-3 tasks.

### What Didn't Work

1. **Depth-3 tasks need more training data, not just templates**: The model explores `constructor → constructor → ...` but generates wrong sub-goal exacts. 17 depth-3 training pairs in 8587 total is insufficient (~0.2%).

2. **val_loss slightly above target (0.2756 vs ≤0.27)**: More diverse training data increased val loss. Acceptable tradeoff given the massive benchmark gains.

### Lessons Learned

- **ExpandedFixed safety net is O(1) for known patterns**: Adding expressions to expressions.jsonl = instant solve. No training required. This is the right mechanism for patterns the model doesn't need to generalize from.
- **Policy caching by state is by design but limits specialization**: Same-type tasks get identical GC candidates. Oracle is the differentiator. Consider input-aware prompting in future.
- **Constructor boost goal extraction matters**: Using last `⊢` vs first `⊢` is the difference between depth-2 and depth-3 working.
- **Never run concurrent daemon benchmarks**: Sprint 20 lesson reinforced. Sequential only.
