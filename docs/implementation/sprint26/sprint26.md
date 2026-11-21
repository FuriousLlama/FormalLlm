# Sprint 26: Vocabulary Expansion — Primitives, Skeletons, and Algorithmic Patterns

## Goal

Radically expand the system's expressive vocabulary so that it can synthesize programs requiring conditional transformation, multi-pass computation, tuple-state accumulation, left-fold semantics, and basic arithmetic beyond addition/multiplication. Target: solve 16+/20 current stretch tasks and add 15 new tasks exercising the expanded vocabulary. No retraining — pure search-space expansion.

## Motivation

Sprint 25 revealed the system's core limitation: **it's vocabulary-starved, not capability-starved**. The 13/20 unsolved stretch tasks fail not because MCTS can't search or the oracle can't verify, but because the candidate space literally cannot express the required programs. The system has conditional *filtering* but not conditional *mapping*. It has `acc + h` but not `acc + h.natAbs`. It can right-fold but not left-fold. It has single-value accumulators but not tuple state.

Every sprint where templates/expressions were expanded (S19, S21, S25) produced immediate rollout-1 solves. Every sprint where training data was scaled without expanding the search space (S23) failed. The evidence is overwhelming: **expand the vocabulary, expand the capability**.

## Context

### Current State (Sprint 25)
- **79/85** core tasks (93%), **7/20** stretch tasks (35%)
- **Grand total: 86/105**
- No retraining since S21 — all S24/S25 gains are search architecture
- GoalConditioned adapter: S21 vintage, val_loss 0.2756

### What the System Can Express
- Single-step library lookups (List.sum, List.length)
- Two-step compositions (filter then fold)
- Product decomposition to depth 3 (skeleton-guided)
- Right-fold recursion via List.rec/Nat.rec with ~40 step templates
- 650 pre-enumerated ExpandedFixed expressions

### What It Cannot Express (the 13 failures)
| Gap | Missing Pattern | Blocked Tasks |
|-----|----------------|---------------|
| Conditional map | `if p(h) then f(h) :: acc else h :: acc` | double_positives, replace_neg_zero, clamp_values |
| Arithmetic primitives | `h.natAbs`, `(k+1)^3`, `h - acc` | sum_abs_values, sum_cubes, alternating_sum |
| Left-fold semantics | `foldl` (process left-to-right, final value) | last_or_default, negate_sum |
| Two-pass composition | `let v = fold₁(xs); fold₂(xs, v)` | deviation_from_max, max_min_diff |
| Tuple accumulator | `fun h (a, b) => (f(a,h), g(b,h))` | is_sorted_asc, longest_nondec_prefix |
| Predecessor tracking | `fun h (acc, prev) => ...` | compress_consecutive, count_peaks |

## Architecture

### Overview

```
Goal Type Analyzer (extended)
    │
    ├── Product skeleton (S24, unchanged)
    │
    ├── Recursion skeleton (S25, EXTENDED with ~50 new step templates)
    │       ├── Conditional-map templates (NEW)
    │       ├── Arithmetic primitives (NEW)
    │       └── Tuple-accumulator templates (NEW)
    │
    ├── Foldl skeleton (NEW)
    │       └── Left-fold patterns for order-dependent accumulation
    │
    ├── Two-pass skeleton (NEW)
    │       └── fold₁ → fold₂ composition for multi-pass algorithms
    │
    └── Standard MCTS (fallback, unchanged)
```

### Design Principle

Same pattern as S24 (product) and S25 (recursion): **deterministic structural analysis → enumerate candidates → oracle verify**. Each new skeleton type is a self-contained module that activates based on goal structure analysis. No skeleton changes the MCTS search tree — they all short-circuit via `expand()` at rollout 1.

## New Vocabulary: Detailed Specification

### Category 1: Expanded Step-Function Templates (recursion_candidates.py)

#### 1a. Conditional-Map Templates (List → List)

Currently the system can only filter (discard elements). These templates keep all elements but transform conditionally:

```python
STEP_LIST_INT_TO_LIST_INT_CMAP = [
    # if positive, transform; else keep
    "fun h _ acc => (if decide (h > 0) then h * 2 else h) :: acc",
    "fun h _ acc => (if decide (h > 0) then h + 1 else h) :: acc",
    "fun h _ acc => (if decide (h > 0) then h * h else h) :: acc",
    # if negative, replace with constant
    "fun h _ acc => (if decide (h < 0) then 0 else h) :: acc",
    "fun h _ acc => (if decide (h < 0) then -h else h) :: acc",    # abs
    "fun h _ acc => (if decide (h < 0) then -1 else h) :: acc",
    # clamping patterns
    "fun h _ acc => (if decide (h > 5) then 5 else if decide (h < -5) then -5 else h) :: acc",
    "fun h _ acc => (if decide (h > 10) then 10 else if decide (h < -10) then -10 else h) :: acc",
    "fun h _ acc => (if decide (h > 0) then h else 0) :: acc",     # ReLU
    # general conditional preserve
    "fun h _ acc => (if decide (h % 2 == 0) then h * 2 else h) :: acc",
    "fun h _ acc => (if decide (h % 2 == 0) then h else h * 2) :: acc",
]
```

**Unblocks**: double_positives, replace_neg_zero, clamp_values (3 stretch tasks)

#### 1b. Arithmetic Primitive Templates

```python
# Add to STEP_LIST_INT_TO_INT
STEP_LIST_INT_TO_INT_ARITH = [
    "fun h _ acc => acc + h.natAbs",              # sum of absolute values
    "fun h _ acc => acc + h * h * h",             # sum of cubes (list)
    "fun h _ acc => acc + (if decide (h > 0) then h else -h)",  # manual abs sum
    "fun h _ acc => h - acc",                     # right-fold subtraction (alternating)
    "fun h _ acc => acc - h",                     # subtract each element
    "fun h _ acc => if decide (h > 0) then acc + h * h else acc",  # sum squares of positives
    "fun h _ acc => if decide (h < 0) then acc + h * h else acc",  # sum squares of negatives
]

# Add to STEP_NAT_TO_NAT
STEP_NAT_TO_NAT_ARITH = [
    "fun k acc => acc + (k + 1) * (k + 1) * (k + 1)",  # sum of cubes (Nat)
    "fun k acc => acc + (k + 1) * (k + 1)",              # sum of squares (Nat)
    "fun k acc => acc * (k + 2)",                         # double factorial-like
    "fun k acc => acc + 2 * (k + 1) - 1",                # sum of odds = perfect square
]
```

**Unblocks**: sum_abs_values, sum_cubes, alternating_sum (3 stretch tasks)

#### 1c. Tuple-Accumulator Step Templates

New category: step functions that maintain `(value, state)` pairs for predecessor tracking and multi-value accumulation.

```python
STEP_LIST_INT_TO_PAIR = {
    # (running_result, previous_element) — for sorted checks, consecutive dedup
    "(Int × Bool)": [
        "fun h _ (prev, sorted) => (h, sorted && decide (prev ≤ h))",
    ],
    "(Int × Int)": [
        "fun h _ (a, b) => (a + h, b + 1)",             # sum and count
        "fun h _ (a, b) => (min a h, max b h)",          # min and max
        "fun h _ (_, prev) => (h, prev)",                # shift register
    ],
    "(List Int × Int)": [
        "fun h _ (lst, prev) => (if decide (h == prev) then lst else h :: lst, h)",  # dedup consecutive
    ],
}
```

**Unblocks**: is_sorted_asc, compress_consecutive (2 stretch tasks)

### Category 2: Foldl Skeleton (NEW module)

`List.rec` is a right fold — it processes elements right-to-left. Several tasks require left-to-right semantics where the final accumulator value (not the reconstructed list) is the answer.

```
foldl_skeleton(xs, init, step) = xs.foldl step init
```

The goal analyzer detects foldl-amenable goals: scalar return type + list input + no list output.

**Implementation**: New file `foldl_candidates.py` with:

```python
FOLDL_TEMPLATES = {
    "List Int → Int": [
        "exact xs.foldl (fun acc h => acc + h) 0",           # sum (redundant, but complete)
        "exact xs.foldl (fun acc h => acc * h) 1",           # product
        "exact xs.foldl (fun acc h => if decide (h > acc) then h else acc) 0",  # max
        "exact xs.foldl (fun acc h => if decide (h < acc) then h else acc) 0",  # min
        "exact xs.foldl (fun acc _ => acc + 1) 0",           # length
        "exact xs.foldl (fun _ h => h) 0",                   # last element
        "exact xs.foldl (fun acc h => acc + h.natAbs) 0",    # sum abs
        "exact xs.foldl (fun acc h => acc - h) 0",           # alternating via foldl
        "exact xs.foldl (fun acc h => if decide (h > 0) then acc + h else acc) 0",
        "exact xs.foldl (fun acc h => if decide (h < 0) then acc + 1 else acc) 0",
    ],
    "List Int → Nat": [
        "exact xs.foldl (fun acc h => if decide (h > 0) then acc + 1 else acc) 0",
        "exact xs.foldl (fun acc h => if decide (h % 2 == 0) then acc + 1 else acc) 0",
    ],
    "List Int → Bool": [
        "exact xs.foldl (fun acc h => acc && decide (h > 0)) true",
        "exact xs.foldl (fun acc h => acc && decide (h % 2 == 0)) true",
        "exact xs.foldl (fun acc h => acc || decide (h > 0)) false",
    ],
    "List Int → Int (negate_sum)": [
        "exact -(xs.foldl (fun acc h => acc + h) 0)",        # negate the sum
    ],
}
```

**Unblocks**: last_or_default, negate_sum (2 stretch tasks)

**Integration**: `goal_analyzer.py` gets a new field `is_foldl_amenable` (scalar return + list input + not product + not recursive skeleton match). MCTS `expand()` tries foldl skeleton after recursion skeleton, before standard policy.

### Category 3: Two-Pass Skeleton (NEW module)

For algorithms that require two traversals: compute an aggregate, then use it in a second pass.

```
two_pass_skeleton(xs) = let v = pass₁(xs); pass₂(xs, v)
```

**Implementation**: New file `twopass_candidates.py`:

```python
PASS1_TEMPLATES = {
    "List Int → Int": [
        "xs.foldl (fun acc h => if decide (h > acc) then h else acc) 0",   # max
        "xs.foldl (fun acc h => if decide (h < acc) then h else acc) 0",   # min
        "xs.foldl (fun acc h => acc + h) 0",                               # sum
        "xs.length",
    ],
}

PASS2_TEMPLATES = {
    # pass₂ uses result of pass₁ as a parameter
    "List Int → Int → List Int": [
        "xs.map (fun h => h - v)",           # deviation from aggregate
        "xs.map (fun h => h * v)",           # scale by aggregate
        "xs.filter (fun h => decide (h > v))",  # filter above aggregate
        "xs.filter (fun h => decide (h < v))",  # filter below aggregate
    ],
    "List Int → Int → Int": [
        "xs.foldl (fun acc h => acc + (h - v).natAbs) 0",  # sum of deviations
    ],
}

def generate_twopass_candidates(goal_type, input_var, pass1_results, pass2_templates):
    """
    Generates: exact (let v := pass1(xs); pass2(xs, v))
    """
```

**Unblocks**: deviation_from_max, max_min_diff (2 stretch tasks)

**Integration**: Two-pass skeleton activates when:
1. Goal has list input and non-product return
2. Recursion skeleton returned None (not a simple fold)
3. Foldl skeleton returned None (not a simple accumulation)

It's the last skeleton before falling back to standard MCTS.

### Category 4: Expanded ExpandedFixed Expressions (expressions.jsonl)

Add ~80 new expressions to cover gaps:

```
# Conditional maps (keep all elements, transform some)
xs.map (fun h => if decide (h > 0) then h * 2 else h)        # double positives
xs.map (fun h => if decide (h < 0) then 0 else h)            # replace neg with 0
xs.map (fun h => if decide (h < 0) then -h else h)           # manual abs
xs.map (fun h => min 5 (max (-5) h))                         # clamp to [-5,5]
xs.map (fun h => max 0 h)                                     # ReLU

# Absolute value operations
xs.map (fun h => h.natAbs)                                    # map abs (→ List Nat)
xs.foldl (fun acc h => acc + h.natAbs) 0                     # sum of abs

# Arithmetic
(List.range n).foldl (fun acc i => acc + (i+1)*(i+1)*(i+1)) 0  # sum of cubes
(List.range n).foldl (fun acc i => acc + (i+1)*(i+1)) 0        # sum of squares

# Last element
xs.foldl (fun _ h => h) 0                                    # last or default 0

# Negate result
-(xs.foldl (fun acc h => acc + h) 0)                         # negate sum

# Two-pass patterns
xs.map (fun h => h - xs.foldl (fun a b => if decide (b > a) then b else a) 0)  # deviation from max
xs.foldl (fun a b => if decide (b > a) then b else a) 0 -
  xs.foldl (fun a b => if decide (b < a) then b else a) 0    # max - min

# Boolean folds
xs.foldl (fun acc h => acc && decide (h % 2 == 0)) true      # all even
xs.getLast? |>.getD 0                                         # last or default via Option

# Sorted check (approximate — needs Lean validation)
(xs.zip xs.tail).foldl (fun acc (a, b) => acc && decide (a ≤ b)) true
```

Each expression must be validated via Lean compilation and added with correct `outputs` field.

## New Benchmark Tasks (15 tasks)

### V3 Tasks: Vocabulary Validation (10 tasks)

These directly exercise the new vocabulary. Every task should be solvable at rollout 1 if the vocabulary expansion is correct.

| Task | Type | Pattern Exercised |
|------|------|------------------|
| abs_sum | List Int → Nat | foldl + natAbs |
| cube_sum | Nat → Nat | Nat.rec + cubic step |
| double_evens | List Int → List Int | conditional map (even → *2) |
| relu_list | List Int → List Int | conditional map (max 0 h) |
| last_element | List Int → Int | foldl last |
| neg_sum | List Int → Int | negate(foldl sum) |
| max_minus_min | List Int → Int | two-pass (max - min) |
| deviations | List Int → List Int | two-pass (h - max) |
| clamp_to_range | List Int → List Int | conditional map (clamp) |
| replace_neg | List Int → List Int | conditional map (neg → 0) |

### V3 Stretch Tasks: Compositional Vocabulary (5 tasks)

Combine multiple new vocabulary items. Solvable only if the expanded vocabulary composes correctly.

| Task | Type | Pattern |
|------|------|---------|
| sum_abs_positives | List Int → Int | filter(>0) + map(abs) + fold(+) |
| normalize_to_max | List Int → List Int | two-pass: max, then map(h*100/max) |
| running_abs_sum | List Int → List Int | scanl + natAbs |
| count_sign_changes | List Int → Nat | tuple accumulator (count, prev_sign) |
| sorted_abs | List Int → List Nat | map(abs) + sort |

## Phases

### Phase 0: Baseline Verification (1h)
- Run full 105-task benchmark with current system
- Confirm S25 results: 79/85 core, 7/20 stretch
- Record exact per-task pass/fail for regression detection

### Phase 1: Step-Template Expansion (3h)
**Files**: `recursion_candidates.py`
- Add conditional-map templates (Category 1a): ~11 templates
- Add arithmetic primitive templates (Category 1b): ~11 templates
- Add tuple-accumulator templates (Category 1c): ~8 templates
- Run unit tests on template generation
- Verify all templates type-check via Lean: `env.step(exact ...)` must return valid state or ProofFinished

**Exit criterion**: All new templates compile. `generate_recursion_candidates()` returns expanded sets.

### Phase 2: Foldl Skeleton Module (4h)
**Files**: NEW `foldl_candidates.py`, MODIFY `goal_analyzer.py`, MODIFY `mcts.py`
- Create `foldl_candidates.py` with `generate_foldl_candidates(goal_type, input_var)`
- Add `is_foldl_amenable` field to `GoalStructure`
- Detection logic: scalar return + list input + not product + not already handled by recursion
- Add `_expand_with_foldl_skeleton()` to MCTS
- Wire into `expand()` priority chain: product → recursion → foldl → standard

**Exit criterion**: `last_or_default` and `negate_sum` solve at rollout 1.

### Phase 3: Two-Pass Skeleton Module (5h)
**Files**: NEW `twopass_candidates.py`, MODIFY `goal_analyzer.py`, MODIFY `mcts.py`
- Create `twopass_candidates.py` with `generate_twopass_candidates()`
- Add `is_twopass_candidate` detection to goal analyzer
- Detection heuristic: list input + non-product return + not solved by recursion/foldl
- Two-pass expansion: enumerate pass₁ × pass₂ combinations, type-check, oracle verify
- Candidate cap: max 200 combinations (to avoid oracle overload)
- Wire into `expand()`: product → recursion → foldl → twopass → standard

**Exit criterion**: `deviation_from_max` and `max_min_diff` solve at rollout 1.

### Phase 4: ExpandedFixed Expression Expansion (4h)
**Files**: `expressions.jsonl`, validation script
- Write ~80 new expressions covering all new pattern categories
- Validate each via Lean compilation: `check_compile` with test inputs
- Compute `outputs` field for each expression on standard test cases
- Deduplicate against existing 650 expressions
- Update `expressions.jsonl` (target: 650 → ~730 expressions)

**Exit criterion**: All new expressions compile, have correct outputs, and load into ExpandedFixedPolicy.

### Phase 5: New Task Definitions (3h)
**Files**: NEW `TasksVocabulary.lean`, MODIFY `tasks.py`
- Write 15 Lean task definitions (10 V3 + 5 V3 stretch)
- Add `#eval` verification for each task
- Register in `tasks.py` as `VOCABULARY_TASKS` and `VOCABULARY_STRETCH_TASKS`
- Verify `lake build` succeeds

**Exit criterion**: `lake build` passes, all 15 tasks have valid test cases.

### Phase 6: Benchmark and Iterate (4h)
- Run full benchmark: 105 existing + 15 new = 120 tasks
- Target results:
  - Core 85: maintain 79/85 (no regression)
  - Existing stretch 20: 16+/20 (up from 7/20)
  - New V3 10: 10/10 (vocabulary validation)
  - New V3 stretch 5: 3+/5
- Debug any skeleton failures: check template type-checking, oracle verification, candidate ordering
- Iterate on failing templates

**Exit criterion**: Stretch tasks ≥ 16/20, V3 tasks ≥ 10/10, no core regressions.

### Phase 7: Documentation (1h)
- Update sprint26_status.md
- Update CLAUDE.md with new file references
- Record lessons learned

## Success Criteria

| Metric | Minimum | Target | Stretch |
|--------|---------|--------|---------|
| Core 85 baseline | 78/85 | 79/85 | 80/85 |
| Existing stretch 20 | 13/20 | 16/20 | 18/20 |
| New V3 tasks 10 | 8/10 | 10/10 | 10/10 |
| New V3 stretch 5 | 2/5 | 3/5 | 5/5 |
| Total (120 tasks) | 101/120 | 108/120 | 113/120 |
| Primary metric | Stretch 20 improvement | ≥ +9 from S25 | ≥ +11 |

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Template type-check failures | Medium | Low | Validate each template individually before integration |
| Foldl candidate explosion | Low | Medium | Cap at 100 candidates, type-filter aggressively |
| Two-pass combinatorial blowup | Medium | Medium | Cap pass₁×pass₂ at 200, pre-filter by type |
| Skeleton priority conflicts | Low | High | Clear precedence: product > recursion > foldl > twopass > standard |
| Oracle timeout on large batches | Low | Medium | Batch size cap, early-exit on first pass |
| Lean syntax errors in templates | Medium | Low | Phase 1 type-check validation catches these |
| Regression on core tasks | Low | High | Phase 0 baseline, Phase 6 regression check |
| Tuple-accumulator Lean syntax | High | Medium | Validate Lean product/pair syntax first; may need `Prod.mk` |

## Estimated Effort

| Phase | Hours | Depends On |
|-------|-------|-----------|
| Phase 0: Baseline | 1 | — |
| Phase 1: Step templates | 3 | Phase 0 |
| Phase 2: Foldl skeleton | 4 | Phase 1 |
| Phase 3: Two-pass skeleton | 5 | Phase 2 |
| Phase 4: EF expressions | 4 | Phase 1 |
| Phase 5: New tasks | 3 | Phase 4 |
| Phase 6: Benchmark | 4 | Phase 2, 3, 4, 5 |
| Phase 7: Documentation | 1 | Phase 6 |
| **Total** | **25** | |

**Critical path**: Phase 0 → 1 → 2 → 3 → 6 (17h)
**Parallel track**: Phase 4 and 5 can run alongside Phase 2–3

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `lean_mcts/search/recursion_candidates.py` | MODIFY | +30 step templates (conditional-map, arithmetic, tuple) |
| `lean_mcts/search/foldl_candidates.py` | NEW | Foldl skeleton candidate generation |
| `lean_mcts/search/twopass_candidates.py` | NEW | Two-pass composition candidate generation |
| `lean_mcts/search/goal_analyzer.py` | MODIFY | +is_foldl_amenable, +is_twopass_candidate fields |
| `lean_mcts/search/mcts.py` | MODIFY | Wire foldl + twopass skeletons into expand() |
| `lean_mcts/synth/expressions.jsonl` | MODIFY | +~80 new expressions |
| `lean_mcts/env/FormalNav/FormalNav/TasksVocabulary.lean` | NEW | 15 V3 task definitions |
| `lean_mcts/benchmark/tasks.py` | MODIFY | Register V3 tasks |
| `docs/implementation/sprint26/sprint26_status.md` | NEW | Status tracking |

## Non-Goals

- **Retraining the GoalConditioned adapter.** All gains should come from search-space expansion. Retraining is Sprint 27's job (once the expanded vocabulary generates training signal).
- **Windowed/sliding patterns.** `count_peaks` requires 3-element windows — this is architecturally incompatible with both right-fold and left-fold and needs a dedicated sliding-window skeleton (future sprint).
- **Polymorphic or generic templates.** Templates remain monomorphic (Int, Nat, Bool, List). Generic synthesis is a research problem.
- **Self-play or RL.** The expanded vocabulary must prove itself via direct template matching first. Self-play amplification is a natural follow-up.
- **Changing the oracle or daemon infrastructure.** Pantograph daemon stays as-is.

## Design Decisions

### Why foldl as a separate skeleton (not extending List.rec)?

`List.rec` is a right fold: it processes elements from the tail. For tasks like `last_or_default`, right-fold would need to carry the entire list position. `foldl` processes left-to-right and naturally produces "last wins" semantics. They're different computational patterns and should be separate search paths.

### Why two-pass as a skeleton (not just more expressions)?

Two-pass algorithms like `deviation_from_max` require let-binding an intermediate result. This is a structural pattern (like product decomposition), not just a new expression. The skeleton generates `let v := pass₁(xs); pass₂(xs, v)` — a shape that ExpandedFixed expressions can't naturally represent because each expression is a single closed term.

### Why 15 new tasks instead of just fixing stretch?

The V3 tasks serve as unit tests for the vocabulary expansion. If `abs_sum` doesn't solve at rollout 1, the natAbs template is broken. If `deviations` doesn't solve, the two-pass skeleton is broken. They're validation gates, not aspirational targets.

### Why no retraining?

Three reasons:
1. S24 and S25 proved that search-space expansion without retraining is the highest-leverage intervention
2. Retraining risks S23-style data dilution if not done carefully
3. Clean separation: Sprint 26 expands what's expressible, Sprint 27 teaches the model to prefer the new expressions

## Appendix: Expected Solve Trace for Key Stretch Tasks

### deviation_from_max (Two-Pass)

```
Goal: xs : List Int ⊢ List Int
→ Two-pass skeleton activates
→ pass₁ candidates: [max(xs), min(xs), sum(xs), length(xs)]
→ For pass₁ = max(xs):
    → pass₂ candidates: [xs.map (h - v), xs.map (h * v), xs.filter (h > v), ...]
    → Generate: exact (let v := xs.foldl (fun a b => if decide (b > a) then b else a) 0; xs.map (fun h => h - v))
    → Type-check: ✓
    → Oracle verify on test cases: ✓
→ Solved at rollout 1
```

### double_positives (Conditional-Map via Recursion)

```
Goal: xs : List Int ⊢ List Int
→ Recursion skeleton activates (List Int input, List Int return)
→ Step templates now include conditional-map:
    "fun h _ acc => (if decide (h > 0) then h * 2 else h) :: acc"
→ Generate: exact List.rec [] (fun h _ acc => (if decide (h > 0) then h * 2 else h) :: acc) xs
→ Type-check: ✓
→ Oracle verify: matches [4, -1, 6, 0, -2] for input [2, -1, 3, 0, -2]: ✓
→ NOTE: List.rec is right-fold, so output preserves order (cons builds from right)
→ Solved at rollout 1
```

### is_sorted_asc (Tuple Accumulator — Stretch)

```
Goal: xs : List Int ⊢ Bool
→ Recursion skeleton activates (List Int input, Bool return)
→ Simple step templates tried first: none match sorted-check semantics
→ Foldl skeleton activates as fallback
→ Foldl candidates include pair-state patterns:
    exact xs.zip xs.tail |>.foldl (fun acc (a, b) => acc && decide (a ≤ b)) true
→ Type-check: ✓ (requires xs.tail to exist — may need edge case handling)
→ Oracle verify: ✓
→ Solved at rollout 1
→ NOTE: This is a STRETCH solve — depends on Lean's zip/tail semantics
```

## Appendix: Stretch Task Expected Resolution

| Task | Current | Expected S26 | Via |
|------|---------|-------------|-----|
| alternating_sum | FAIL | PASS | Arithmetic step: `h - acc` |
| sum_even_only | PASS | PASS | Already solved |
| product_positive | PASS | PASS | Already solved |
| abs_of_negatives | PASS | PASS | Already solved |
| sum_cubes | FAIL | PASS | Nat.rec cubic step |
| last_or_default | FAIL | PASS | Foldl skeleton |
| double_positives | FAIL | PASS | Conditional-map template |
| clamp_values | FAIL | PASS | Conditional-map template |
| replace_neg_zero | FAIL | PASS | Conditional-map template |
| negate_sum | FAIL | PASS | Foldl + negate |
| deviation_from_max | FAIL | PASS | Two-pass skeleton |
| count_distinct | PASS | PASS | Already solved |
| is_sorted_asc | FAIL | MAYBE | Tuple accumulator (stretch) |
| max_min_diff | FAIL | PASS | Two-pass skeleton |
| sum_abs_values | FAIL | PASS | Arithmetic step: natAbs |
| longest_nondec_prefix | FAIL | FAIL | Complex 3-state (out of scope) |
| compress_consecutive | FAIL | MAYBE | Tuple accumulator (stretch) |
| count_peaks | FAIL | FAIL | Sliding window (out of scope) |
| *(S4 tasks 4-5)* | FAIL | FAIL | Beyond current architecture |

**Expected**: 7 → 16–18/20 (+9 to +11)
