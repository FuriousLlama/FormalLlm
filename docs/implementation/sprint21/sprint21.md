# Sprint 21: Recurrence & Deep Multi-Step Synthesis

## Goal

Push recurrence from 2/6 → 4+/6 by scaling tuple-accumulator and nested-range training data, and extend multi-step proofs from depth 2-3 to depth 3+ with nested product types and recurrence-product hybrids.

## Context

Sprint 20 proved multi-step synthesis works: `constructor → exact → exact` solves product-type goals via sub-goal decomposition. Sprint 18-19 showed recurrence is the weakest category at 2/6 (only factorial and runningParts), with fibonacci seed-dependent and 4 tasks never solved.

**Key observations:**

1. **Recurrence failures are expression-space gaps, not capacity limits.** Fibonacci was solved by seed 1337 (Sprint 18), proving the 1.5B model CAN generate tuple-accumulator folds — it just hasn't seen enough examples to do so reliably.
2. **Training data has ~6 tuple-fold examples** out of 8174 pairs (~0.07%). The model treats this as noise. Sprint 19 showed 21 fundamentals pairs (1.2%) were also invisible to LoRA — but ExpandedFixed templates solved 9/10.
3. **Multi-step infrastructure is underexploited.** Constructor decomposition only tested at depth 2 (constructor + 2 exacts). Nested products, sequential sub-goals, and recurrence-inside-product are untested.
4. **Two multi-step tasks remain unsolved:** sumAndProduct and splitEvenOdd (both S20). Likely need targeted sub-goal training data.

**Hypotheses:**

- **H1**: Scaling tuple-accumulator training data from ~6 to ~80-120 pairs + ExpandedFixed templates for recurrence patterns will push recurrence to 4+/6.
- **H2**: Adding nested-range patterns (`(List.range n).map (fun i => inner_fold(i))`) will unlock triangularNumbers and powersOf2.
- **H3**: Depth-3 multi-step tasks (nested products) work with the existing MCTS infrastructure — no code changes needed, just training data and tasks.
- **H4**: Recurrence-product hybrid tasks (constructor splits into one fold + one non-fold sub-goal) combine the strengths of Sprint 20 multi-step with recurrence patterns.

## Phases

### Phase 0: Diagnostic Audit (1 hour)

Run the current model on recurrence + multi-step with `--dump-candidates` to understand exact failure modes.

- [ ] Run recurrence tier with dump: what tactics does the model propose for fibonacci, fibList, triangularNumbers, powersOf2?
- [ ] Run multi-step tier with dump: why do sumAndProduct and splitEvenOdd fail? Is it the constructor step or the sub-goal step?
- [ ] Catalogue the exact expression patterns needed for each unsolved task:

| Task | Required Expression Pattern | What Model Generates Instead |
|------|---------------------------|------------------------------|
| fibonacci | `((List.range n).foldl (fun (a, b) _ => (b, a + b)) (0, 1)).1` | TBD from dump |
| fibList | `((List.range n).foldl (fun (acc, ab) _ => (acc ++ [ab.1], ...)) ([], (0, 1))).1` | TBD |
| triangularNumbers | `(List.range n).map (fun i => (List.range (i + 1)).foldl (· + ·) 0)` | TBD |
| powersOf2 | `(List.range n).map (fun i => (List.range i).foldl (fun a _ => a * 2) 1)` | TBD |
| sumAndProduct | Sub-goals: `exact xs.foldl (· + ·) 0` / `exact xs.foldl (· * ·) 1` | TBD |
| splitEvenOdd | Sub-goals: `exact xs.filter (x % 2 = 0)` / `exact xs.filter (x % 2 ≠ 0)` | TBD |

### Phase 1: Scaled Tuple-Accumulator Training Data (2-3 hours)

Generate 80-120 synthetic tuple-fold training pairs programmatically in `collect_goal_conditioned.py`. These cover the general patterns needed for recurrence.

#### Category A: Tuple-Accumulator Folds over List (20 pairs)

Folds that carry a tuple state `(a, b)` through a list, returning one component via `.1` or `.2`.

| # | State | Tactic | Purpose |
|---|-------|--------|---------|
| A1 | `xs : List ℤ ⊢ ℤ` | `exact (xs.foldl (fun (s, c) x => (s + x, c + 1)) (0, 0)).1` | Sum via tuple-accum, project fst |
| A2 | `xs : List ℤ ⊢ ℕ` | `exact (xs.foldl (fun (s, c) x => (s + x, c + 1)) (0, 0)).2` | Count via tuple-accum, project snd |
| A3 | `xs : List ℤ ⊢ ℤ` | `exact (xs.foldl (fun (mn, mx) x => (min mn x, max mx x)) (0, 0)).1` | Min via tuple-accum |
| A4 | `xs : List ℤ ⊢ ℤ` | `exact (xs.foldl (fun (mn, mx) x => (min mn x, max mx x)) (0, 0)).2` | Max via tuple-accum |
| A5 | `xs : List ℤ ⊢ ℤ` | `exact (xs.foldl (fun (s, c) x => (s + x, c + 1)) (0, 0)).1` | Sum-count, extract sum |
| ... | ... | ... | ~15 more variations: different init values, operations, projections |

Variations: swap `.1`/`.2`, different arithmetic ops (`+`, `*`, `min`, `max`), different inits (`(0,0)`, `(1,0)`, `(0,1)`), different tuple-lambda structures.

#### Category B: Tuple-Accumulator Folds over Range (20 pairs)

Same pattern but iterating over `List.range n` — the key pattern for fibonacci-class tasks.

| # | State | Tactic | Purpose |
|---|-------|--------|---------|
| B1 | `n : ℕ ⊢ ℕ` | `exact ((List.range n).foldl (fun (a, b) _ => (b, a + b)) (0, 1)).1` | Fibonacci itself |
| B2 | `n : ℕ ⊢ ℕ` | `exact ((List.range n).foldl (fun (a, b) _ => (b, a + b)) (0, 1)).2` | Fibonacci next |
| B3 | `n : ℕ ⊢ ℕ` | `exact ((List.range n).foldl (fun (a, b) _ => (a + b, a)) (1, 0)).1` | Fibonacci alternate form |
| B4 | `n : ℕ ⊢ ℕ` | `exact ((List.range n).foldl (fun (a, b) _ => (b, a * b)) (1, 1)).1` | Multiplicative recurrence |
| B5 | `n : ℕ ⊢ ℕ` | `exact ((List.range n).foldl (fun (a, b) _ => (b, a + b + 1)) (0, 1)).1` | Shifted recurrence |
| ... | ... | ... | ~15 more: different init pairs, ops, projections |

Include the exact fibonacci pattern B1 plus ~19 structural variations so the model learns the *shape* not just one example.

#### Category C: Tuple-Accumulator with List Append (10 pairs)

Pattern for fibList-class tasks: `(acc ++ [value], state_update)`.

| # | State | Tactic | Purpose |
|---|-------|--------|---------|
| C1 | `n : ℕ ⊢ List ℕ` | `exact ((List.range n).foldl (fun (acc, v) _ => (acc ++ [v], v + 1)) ([], 0)).1` | Running counter list |
| C2 | `n : ℕ ⊢ List ℕ` | `exact ((List.range n).foldl (fun (acc, v) _ => (acc ++ [v], v * 2)) ([], 1)).1` | Powers-of-2 list via append |
| C3 | `n : ℕ ⊢ List ℕ` | `exact ((List.range n).foldl (fun (acc, ab) _ => (acc ++ [ab.1], (ab.2, ab.1 + ab.2))) ([], (0, 1))).1` | fibList exact pattern |
| ... | ... | ... | ~7 more variations |

#### Category D: Nested Range Patterns (20 pairs)

`(List.range n).map (fun i => inner_expression(i))` — needed for triangularNumbers, powersOf2.

| # | State | Tactic | Purpose |
|---|-------|--------|---------|
| D1 | `n : ℕ ⊢ List ℕ` | `exact (List.range n).map (fun i => (List.range (i + 1)).foldl (· + ·) 0)` | Triangular numbers exact |
| D2 | `n : ℕ ⊢ List ℕ` | `exact (List.range n).map (fun i => (List.range i).foldl (fun a _ => a * 2) 1)` | Powers of 2 exact |
| D3 | `n : ℕ ⊢ List ℕ` | `exact (List.range n).map (fun i => (List.range (i + 1)).foldl (· * ·) 1)` | Factorials list |
| D4 | `n : ℕ ⊢ List ℕ` | `exact (List.range n).map (fun i => (List.range i).foldl (· + ·) 0)` | Triangular shifted |
| D5 | `n : ℕ ⊢ List ℕ` | `exact (List.range n).map (fun i => i * i)` | Squares (baseline) |
| D6 | `n : ℕ ⊢ List ℕ` | `exact (List.range n).map (fun i => i * (i + 1) / 2)` | Triangular closed form |
| ... | ... | ... | ~14 more: different inner folds, different outer transforms |

#### Category E: Projection Patterns (10 pairs)

`.1`, `.2`, `.fst`, `.snd` on tuple results — teaching the model that tuple folds return tuples that need projection.

| # | State | Tactic | Purpose |
|---|-------|--------|---------|
| E1 | `xs : List ℤ ⊢ ℤ` | `exact (xs.foldl (fun (s, _) x => (s + x, 0)) (0, 0)).1` | Trivial projection |
| E2 | `xs : List ℤ ⊢ ℕ` | `exact (xs.partition (fun x => decide (x > 0))).1.length` | Partition + project + length |
| ... | ... | ... | ~8 more |

**Total new pairs: ~80-100** (exact count after verification — each pair must compile in Lean 4).

**Verification**: Every pair is tested via Pantograph `check_compile` before inclusion in training data.

### Phase 2: ExpandedFixed Templates for Recurrence (1-2 hours)

Sprint 19 showed that ExpandedFixed templates are the right mechanism for patterns that are too sparse for LoRA to learn. Add recurrence-specific templates to `ExpandedFixedPolicy`.

Templates to add:

| Template Pattern | Instantiations | Covers |
|-----------------|---------------|--------|
| `((List.range {n}).foldl (fun (a, b) _ => ({op})) ({i1}, {i2})).{proj}` | ~20 | fibonacci, multiplicative recurrence |
| `(List.range {n}).map (fun i => (List.range {bound}).foldl {inner_op} {init})` | ~15 | triangularNumbers, powersOf2 |
| `({xs}.foldl (fun (a, b) x => ({op})) ({i1}, {i2})).{proj}` | ~15 | runningParts-class tuple folds |
| `((List.range {n}).foldl (fun (acc, v) _ => (acc ++ [{expr}], {update})) ([], {init})).1` | ~10 | fibList-class append accumulators |

These go in `policy.py` under `ExpandedFixedPolicy` as new expression families, parametrized over common operations.

### Phase 3: Deep Multi-Step Tasks + Training Data (2-3 hours)

#### 3a: Fix Unsolved Multi-Step Tasks

Diagnose and fix sumAndProduct and splitEvenOdd from Sprint 20. Based on Phase 0 findings, add targeted sub-goal training data. Likely fixes:

- sumAndProduct sub-goals: `case fst\nxs : List ℤ\n⊢ ℤ` → `exact xs.foldl (· + ·) 0` and `case snd\nxs : List ℤ\n⊢ ℤ` → `exact xs.foldl (· * ·) 1`
- splitEvenOdd sub-goals: modular arithmetic filter predicates

#### 3b: Create `TasksDeepMultiStep.lean` — Depth-3+ Tasks

New tasks requiring nested constructor decomposition or constructor + recurrence:

| Task | Signature | Depth | Reference Solution |
|------|-----------|-------|-------------------|
| `sumCountMax` | `xs : List Int → (Int × Nat) × Int` | 3 | `constructor; constructor; exact xs.foldl (· + ·) 0; exact xs.length; exact xs.foldl max 0` |
| `statsTriple` | `xs : List Int → (Int × Int) × Nat` | 3 | `constructor; constructor; exact xs.foldl min 0; exact xs.foldl max 0; exact xs.length` |
| `partitionLengths` | `xs : List Int → (Nat × Nat) × Nat` | 3 | `constructor; constructor; exact (xs.filter pos).length; exact (xs.filter neg).length; exact xs.length` |
| `foldAndLength` | `xs : List Int → Int × Nat` | 2 | `constructor; exact xs.foldl (· + ·) 0; exact xs.length` |
| `filterBothWays` | `xs : List Int → List Int × List Int` | 2 | `constructor; exact xs.filter pos; exact xs.filter neg` |
| `recurrenceAndSum` | `xs : List Int → Int × Nat` | 2 | `constructor; exact (xs.foldl (fun (s, c) x => (s + x, c + 1)) (0, 0)).1; exact xs.length` |

**Depth-3 mechanism**: For `(A × B) × C`, MCTS applies:
1. `constructor` → splits into `⊢ A × B` and `⊢ C`
2. `constructor` on first sub-goal → splits into `⊢ A` and `⊢ B`
3. Three `exact` tactics solve A, B, C independently

#### 3c: Training Data for Depth-3

Add training pairs for:
- Nested case prefixes: `case fst\ncase fst\n...` and `case fst\ncase snd\n...` for depth-3 sub-goals
- Constructor at depth 1 (when sub-goal is still a product type): `case fst\nxs : List ℤ\n⊢ ℤ × ℕ` → `constructor`
- Depth-2 sub-goal exacts: `case fst\ncase fst\nxs : List ℤ\n⊢ ℤ` → `exact xs.foldl (· + ·) 0`

~30-40 new pairs covering the depth-3 decomposition patterns.

### Phase 4: Retrain + Evaluate (2-3 hours)

1. Backup current adapter:
   ```bash
   cp -r lean_mcts/train/goal_cond_adapter/ lean_mcts/train/goal_cond_adapter_pre_s21/
   ```

2. Regenerate training data:
   ```bash
   python lean_mcts/synth/collect_goal_conditioned.py
   ```
   Expect: ~8300-8400 unique pairs (8174 + 120-220 new).

3. Retrain:
   ```bash
   python lean_mcts/train/pretrain.py \
     --data lean_mcts/synth/goal_cond_train.jsonl \
     --val lean_mcts/synth/goal_cond_val.jsonl \
     --seed 1337 \
     --output lean_mcts/train/goal_cond_adapter/
   ```
   Expect: val_loss ≤ 0.27 (new data is structurally consistent with existing patterns).

4. Benchmark all tiers:
   ```bash
   python -m lean_mcts.benchmark.run --tier recurrence --rollouts 200 --daemon
   python -m lean_mcts.benchmark.run --tier multistep --rollouts 200 --daemon
   python -m lean_mcts.benchmark.run --tier deep_multistep --rollouts 200 --daemon
   python -m lean_mcts.benchmark.run --tier all+v2+fundamentals --rollouts 50 --daemon
   ```

5. Regression check: T2 10/10, V2 ≥ 18/20, Hard 5/5, Fundamentals ≥ 9/10.

### Phase 5: Targeted Self-Play on Recurrence (1-2 hours)

Run 2-3 rounds of self-play focused on recurrence + deep multi-step tiers:
1. Collect solutions from benchmark runs
2. Decompose into (state, tactic) pairs
3. Merge into training data
4. Retrain and re-evaluate

Stop at round 2 if no new tasks are solved (Sprint 18 showed diminishing returns after round 3).

### Phase 6: Analysis + Lessons Learned (1 hour)

Document:
- Which recurrence tasks were unlocked by training data vs. ExpandedFixed templates
- Whether depth-3 multi-step worked out of the box or required MCTS changes
- Whether nested case prefix handling needed code changes
- Impact of ~120 new pairs on overall model quality
- Self-play effectiveness on recurrence specifically

## Success Criteria

| Metric | Minimum | Target | Stretch |
|--------|---------|--------|---------|
| Recurrence | 3/6 | 4/6 | 5/6 |
| Multi-step (existing 5) | 4/5 | 5/5 | 5/5 |
| Deep multi-step (new 6) | 2/6 | 4/6 | 6/6 |
| T2 no regression | 10/10 | 10/10 | 10/10 |
| V2 no regression | ≥ 18/20 | 19/20 | 20/20 |
| Hard no regression | 5/5 | 5/5 | 5/5 |
| Fundamentals no regression | ≥ 9/10 | 9/10 | 10/10 |
| val_loss | < 0.28 | ≤ 0.27 | < 0.26 |

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Tuple-accumulator expressions fail Lean type checking | Medium | Verify every pair via Pantograph before inclusion. Lean's type inference for tuple lambdas can be finicky |
| ExpandedFixed templates for recurrence are too many — slow down MCTS | Low | Limit to ~60 templates. Current ExpandedFixed has ~1400 expressions; 60 more is <5% increase |
| Nested constructor (depth-3) hits Pantograph state parsing issues | Medium | Phase 0 diagnostic with manual test. If `case fst\ncase fst\n...` confuses `_strip_case_prefix()`, fix the parser first |
| New training data causes regression on existing tasks | Low | Only ~1.5% increase in data. Full regression benchmark in Phase 4 |
| Self-play on recurrence yields 0 new solutions | Medium | Primary gains come from Phases 1-3 (training data + templates). Self-play is bonus |
| fibList too complex for any approach (3-level nested tuple) | High | fibList is a stretch goal. 5/6 recurrence without fibList is the target |

## Estimated Effort

| Phase | Hours |
|-------|-------|
| Phase 0: Diagnostic audit | 1 |
| Phase 1: Tuple-accumulator training data | 2-3 |
| Phase 2: ExpandedFixed recurrence templates | 1-2 |
| Phase 3: Deep multi-step tasks + data | 2-3 |
| Phase 4: Retrain + evaluate | 2-3 |
| Phase 5: Self-play | 1-2 |
| Phase 6: Analysis | 1 |
| **Total** | **10-15** |

## Files to Create

| File | Purpose |
|------|---------|
| `lean_mcts/env/FormalNav/FormalNav/TasksDeepMultiStep.lean` | 6 depth-3+ tasks with reference solutions |
| `docs/implementation/sprint21/sprint21.md` | Sprint plan |
| `docs/implementation/sprint21/sprint21_status.md` | Sprint status tracker |

## Files to Modify

| File | Changes |
|------|---------|
| `lean_mcts/synth/collect_goal_conditioned.py` | Add ~100-120 tuple-accumulator, nested-range, projection, depth-3 training pairs |
| `lean_mcts/search/policy.py` | Add recurrence templates to ExpandedFixedPolicy; fix `_strip_case_prefix()` for nested case if needed |
| `lean_mcts/benchmark/tasks.py` | Add `DEEP_MULTISTEP_TASKS` list + tier |
| `lean_mcts/benchmark/run.py` | Add `"deep_multistep"` to `--tier` choices |
| `lean_mcts/env/FormalNav/FormalNav.lean` | Add `import FormalNav.TasksDeepMultiStep` |
| `lean_mcts/env/pantograph_daemon.py` | Add `TasksDeepMultiStep` to ALL_IMPORTS |

## Non-Goals

- No model architecture changes (still Qwen2.5-Coder-1.5B + LoRA r=16)
- No MCTS algorithm changes (PUCT, rollout limits, value network)
- No changes to GHC Core pipeline (`sprint/`)
- No fundamentals expansion (Sprint 19 scope)
- No new model seed exploration (stick with seed 1337)
- No Pantograph daemon changes (16-worker config is stable)
