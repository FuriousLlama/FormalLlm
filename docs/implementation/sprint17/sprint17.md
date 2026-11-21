# Sprint 17: Recurrence Synthesis & Hardening

## Goal

Extend the GoalConditioned policy to synthesize **stateful recurrence patterns** (Fibonacci-class problems requiring tuple-accumulator folds), fix known bugs blocking V2 20/20, and speed up inference for faster iteration.

## Context

Sprint 16 achieved T2: 10/10, V2: 18/20, Hard: 4/5 with GoalConditioned policy generating 8 novel tactics. Two clear gaps remain:

1. **Oracle bug** blocks `first_n_squares` (model generates correct tactic, oracle can't verify it)
2. **Tuple-accumulator folds** are absent from training data — the model has never seen `foldl (fun (a, b) _ => (b, a + b)) (0, 1)` patterns
3. **Inference is 6x slower** than ExpandedFixed (64 sequences per propose_tactics)
4. **partition_and_count regressed** at 50 rollouts due to PUCT budget dilution
5. **flatten** (Hard) remains unsolved — 0/42 oracle passes in Sprint 16

## Phases

### Phase 0: Oracle Bug Fix & Quick Wins (1 hour)

Fix the `first_n_squares` oracle bug. Root cause identified:

```python
# oracle.py lines 78-79 (both _build_eval_lean and _build_warm_eval_code)
# Current: uses 'xs' for ALL non-nested, non-two-arg types
params = f"(xs : {input_type})"
# When input_type="Nat", generates: (xs : Nat)
# But model tactics reference 'n' (from TasksV2.lean definition)
```

**Fix**: Map `input_type` to the correct parameter name:

```python
if input_type == "Nat":
    params = "(n : Nat)"
elif input_type == "List (List Int)":
    params = f"(xss : {input_type})"
else:
    params = f"(xs : {input_type})"
```

Apply to both `_build_eval_lean()` (line 79) and `_build_warm_eval_code()` (line 109).

**Validation**: Run single-task benchmark:
```bash
cd lean_mcts && python -m benchmark.run --tasks first_n_squares --policy hybrid-gc --rollouts 50
```

**Expected**: V2 → 19/20 (model already generates `exact (List.range n).map (fun i => i * i)`)

### Phase 1: Tuple-Accumulator Training Data (3-4 hours)

Add ~80-120 new (state, tactic) pairs covering stateful fold patterns to `collect_goal_conditioned.py`.

#### 1a. Core tuple-fold patterns (~30 pairs)

Patterns the model has never seen:

```lean
-- Fibonacci recurrence
exact ((List.range n).foldl (fun (a, b) _ => (b, a + b)) (0, 1)).1

-- Running pair accumulation
exact ((List.range n).foldl (fun (a, b) _ => (b, a + b)) (0, 1)).2

-- Factorial via accumulator
exact (List.range n).foldl (fun acc i => acc * (i + 1)) 1

-- Geometric sequence
exact (List.range n).map (fun i => (List.range i).foldl (fun a _ => a * 2) 1)

-- Triangular numbers
exact (List.range n).map (fun i => (List.range (i + 1)).foldl (· + ·) 0)
```

Enumerate systematically:
- **Binary recurrences**: `(a, b) → (b, a + b)`, `(b, a * b)`, `(b, a - b)`, `(b, max a b)`
- **Accumulator patterns**: `(sum, count) → (sum + x, count + 1)` for running average components
- **State machines**: `(prev, curr) → (curr, f curr)` for sequence generation

#### 1b. Lean task definitions for verification (~6 new tasks)

Add to `FormalNav/TasksHard.lean` or create `FormalNav/TasksRecurrence.lean`:

| Task | Signature | Reference solution |
|------|-----------|-------------------|
| `fibonacci` | `Nat → Nat` | `((List.range n).foldl (fun (a, b) _ => (b, a + b)) (0, 1)).1` |
| `fibList` | `Nat → List Nat` | Generate first n Fibonacci numbers |
| `runningAvgParts` | `List Int → (Int × Nat)` | `(sum, count)` via tuple fold |
| `factorial` | `Nat → Nat` | `(List.range n).foldl (fun a i => a * (i + 1)) 1` |
| `triangularNumbers` | `Nat → List Nat` | `(List.range n).map (fun i => ...)` |
| `collatzSteps` | `Nat → Nat` | Iterative Collatz step count |

#### 1c. Synthetic augmentation (~50-80 pairs)

Programmatically generate variants:
- Vary fold operations: `+`, `*`, `max`, `min`, bitwise
- Vary initial states: `(0, 1)`, `(1, 0)`, `(1, 1)`, `(0, 0)`
- Vary projection: `.1`, `.2`, combine with outer map/filter
- Vary input types: `Nat` input (range-based), `List Int` input (list fold)

#### 1d. Verify all new pairs

Run batch compilation check (existing infrastructure in `collect_goal_conditioned.py`).

### Phase 2: Adaptive Rollout Budget (2 hours)

Fix `partition_and_count` regression. The issue: GoalConditioned generates more candidates than ExpandedFixed, diluting PUCT exploration per candidate.

#### Approach: per-task adaptive budget

```python
# In benchmark/run.py or search/mcts.py
# If no solution found after base_rollouts, double budget and retry (up to 2x)
# This costs wall time only on hard tasks, keeps easy tasks fast
```

Alternatively, **candidate deduplication**: GoalConditioned often proposes near-duplicate tactics. Deduplicate before MCTS expansion:

```python
# In GoalConditionedPolicy.propose_tactics:
# After generation, deduplicate by normalized tactic string
# Reduces 64 candidates to ~20-30 unique, concentrating PUCT budget
```

Both approaches are complementary. Implement deduplication first (simpler, addresses root cause).

### Phase 3: Inference Speedup (3-4 hours)

GoalConditioned benchmark takes ~30 min vs ~5 min for ExpandedFixed. Target: < 15 min for V2.

#### 3a. KV-cache reuse across candidates

Currently each of 64 sequences is generated independently. The goal-state prefix is identical across all — cache it:

```python
# Encode goal state once, reuse KV cache for all 64 continuations
# Expected: ~2-3x speedup on generation
```

#### 3b. Reduce candidate count where possible

- For single-step goals (non-product types): 32 candidates (16 + 16) instead of 64
- For product-type goals: keep 64 (more diverse tactics needed)
- Empirically validate no regression on V2/Hard

#### 3c. Early termination

If oracle confirms a solution during MCTS, stop generating more candidates for that node:

```python
# Already partially implemented in MCTS — verify it's active
# Also: if first 10 candidates include a known-good ExpandedFixed tactic, skip remaining generation
```

### Phase 4: Retrain & Evaluate (2-3 hours)

#### 4a. Regenerate training data

```bash
cd lean_mcts && python -m synth.collect_goal_conditioned --output train/goal_cond_train.jsonl --val-output train/goal_cond_val.jsonl
```

Target: ~7500-8000 pairs (up from 6704).

#### 4b. Train

```bash
python -m train.pretrain --mode goal-cond --epochs 10 --lr 2e-4 --batch-size 4
```

Target: val_loss < 0.30 (currently 0.2759 with 6704 pairs).

#### 4c. Benchmark

Full benchmark with new adapter:

```bash
# V2 + Hard (25 tasks)
python -m benchmark.run --policy hybrid-gc --rollouts 200 --backend pantograph --tasks-version all

# New recurrence tasks
python -m benchmark.run --policy hybrid-gc --rollouts 200 --backend pantograph --tasks recurrence
```

### Phase 5: Self-Play Iteration (stretch, 2-3 hours)

Use successful novel tactics from Sprint 16 + Sprint 17 as new training data:
1. Collect all oracle-verified novel solutions from benchmark runs
2. Add to training set as high-quality (state, tactic) pairs
3. Retrain and re-benchmark
4. Repeat once if time permits

## Success Criteria

| Metric | Minimum | Target | Stretch |
|--------|---------|--------|---------|
| V2 | 19/20 | 20/20 | 20/20 |
| Hard | 4/5 | 5/5 | 5/5 |
| Recurrence tasks (new, /6) | 2/6 | 4/6 | 6/6 |
| `fibonacci` specifically | PASS | PASS | PASS |
| Inference time (V2, 50 rollouts) | < 25 min | < 15 min | < 10 min |
| val_loss | < 0.32 | < 0.29 | < 0.26 |
| Training pairs | > 7000 | > 7500 | > 8000 |

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Tuple-fold pairs don't compile in Lean 4 | Medium | Verify each pattern in `lake env lean` before adding to training |
| Fibonacci expressible but too long for 200-char filter | Low | Check char count; Fibonacci fold is ~70 chars |
| Retrain regresses existing tasks | Low | Validate V2 18/20 + Hard 4/5 baseline before/after |
| KV-cache reuse breaks generation quality | Medium | A/B test: same rollouts, compare solve rates |
| New tasks too easy (solved by ExpandedFixed) | Medium | Verify ExpandedFixed can't solve them before benchmarking |

## Estimated Effort

| Phase | Hours | Dependencies |
|-------|-------|-------------|
| Phase 0: Oracle fix | 1 | None |
| Phase 1: Training data | 3-4 | Phase 0 (for verifying Nat-input patterns) |
| Phase 2: Adaptive rollouts | 2 | None |
| Phase 3: Inference speedup | 3-4 | None |
| Phase 4: Retrain & eval | 2-3 | Phases 0-3 |
| Phase 5: Self-play (stretch) | 2-3 | Phase 4 |
| **Total** | **13-17** | |

## Files to Modify

| File | Changes |
|------|---------|
| `lean_mcts/eval/oracle.py` | Fix Nat input_type parameter name (lines 79, 109) |
| `lean_mcts/synth/collect_goal_conditioned.py` | Add tuple-fold synthetic pairs |
| `lean_mcts/synth/enumerate.py` | Add recurrence expression patterns |
| `lean_mcts/benchmark/tasks.py` | Add 6 recurrence tasks with test cases |
| `lean_mcts/env/FormalNav/FormalNav/TasksRecurrence.lean` | New Lean task definitions |
| `lean_mcts/env/FormalNav/FormalNav.lean` | Import TasksRecurrence |
| `lean_mcts/search/policy.py` | Candidate deduplication, candidate count tuning |
| `lean_mcts/search/mcts.py` | Adaptive rollout budget (optional) |
| `lean_mcts/train/pretrain.py` | Verify goal-cond mode handles new data |

## Non-Goals

- No new model architecture (still Qwen2.5-Coder-1.5B + LoRA)
- No multi-tactic `let rec` synthesis (single `exact` expressions only)
- No Mathlib proof tasks (staying in synthesis domain)
- No changes to Pantograph backend
- No changes to the GHC Core pipeline (sprint/)
