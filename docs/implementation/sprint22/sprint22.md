# Sprint 22: Compositional Fragment Synthesis via Partial Evaluation

## Goal

Replace the hand-curated expression library (expressions.jsonl) with a compositional search engine that constructs programs on-the-fly from typed fragments, guided by test-case partial evaluation. The system should match ExpandedFixed on existing benchmarks and solve novel tasks that require compositions not in the pre-enumerated set.

## Motivation

Through 21 sprints, the project converged on a pattern: every new task category (recurrence, multi-step, fundamentals) was solved by **manually adding expressions to expressions.jsonl**, not by the model learning to generalize. ExpandedFixed has 650 hand-crafted entries. Each new task type requires authoring new templates — this doesn't scale.

The key insight: expressions.jsonl entries are compositions of ~60 atomic fragments. Instead of pre-enumerating compositions, we can **construct them on-demand** by evaluating fragments on test inputs and chaining those whose intermediate values produce the expected output.

This is the shift from "search a curated expression space" to "construct programs from their specification."

## Context

### Current State (End of Sprint 21)

| Tier | Score | Mechanism |
|------|-------|-----------|
| T2 | 10/10 | ExpandedFixed (lookup) |
| V2 | 19/20 | ExpandedFixed + GoalConditioned |
| Hard | 4-5/5 | GoalConditioned (novel tactics) |
| Recurrence | 6/6 | ExpandedFixed (82 Nat-input templates) |
| Multi-step | 5/5 | Constructor + sub-goal exacts |
| Deep multi-step | 2/6 | Depth-3 body wiring insufficient |
| Fundamentals | 9/10 | ExpandedFixed (35 templates) |

### What We Have

- **60 Level-0 primitives** in expressions.jsonl with pre-computed outputs on 5 test inputs
- **Warm oracle** via Pantograph daemon: ~130-375ms per `check_compile` call
- **HybridGoalConditionedPolicy** that combines generative (GoalConditioned) + lookup (ExpandedFixed)
- **Oracle already stores I/O pairs** per expression: `{"outputs": {"[3,-1,4]": "2", ...}}`

### Key Observation

The expressions.jsonl format already has everything needed for compositional search:
```json
{
  "tag": "filter_gt0",
  "expr": "xs.filter (fun x => decide (x > 0))",
  "return_type": "List Int",
  "input_type": "List Int",
  "level": 0,
  "outputs": {"[3, -1, 4, -2, 0]": "[3, 4]", "[1, 2, 3]": "[1, 2, 3]", ...}
}
```

Fragment A outputs `[3, 4]` on input `[3, -1, 4]`. Fragment B (`xs.length`) takes `List Int` → `Nat`. Evaluating B on A's output gives `2`. If the task expects `2` for input `[3, -1, 4]`, then `(xs.filter (fun x => decide (x > 0))).length` is a candidate.

**No Lean evaluation needed for the composition step** — we can compose by chaining outputs purely in Python, then verify the final candidate via one oracle call.

## Architecture

```
                    ┌──────────────┐
                    │  Test Cases  │ (from SynthesisTask)
                    │  in → out    │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  Fragment    │ ~60 typed primitives
                    │  Catalog    │ with pre-computed outputs
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ Compositional│ Bottom-up: evaluate fragments on
                    │ Search       │ test inputs, chain by output match
                    └──────┬───────┘
                           │
                    ┌──────▼───────────┐
                    │ Candidate Tactics │ `exact composed_expr`
                    └──────┬───────────┘
                           │
         ┌─────────────────▼────────────────────┐
         │ CompositionalPolicy                   │
         │ (new class in policy.py)              │
         │ propose_tactics(state) → list[str]    │
         └─────────────────┬────────────────────┘
                           │
    ┌──────────────────────▼──────────────────────┐
    │ HybridCompositionalPolicy                    │
    │ GoalConditioned + Compositional + ExpandedFixed │
    └──────────────────────┬──────────────────────┘
                           │
                    ┌──────▼───────┐
                    │    MCTS      │ (unchanged)
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │   Oracle     │ (unchanged — final verification)
                    └──────────────┘
```

### Compositional Search Algorithm

```
COMPOSE(fragments, test_cases, max_depth=3):
  # Phase 1: Evaluate all fragments on task inputs
  memo = {}  # (fragment_tag, input_string) → output_string
  for frag in fragments:
    if frag.input_type != task.input_type: continue
    for tc in test_cases:
      memo[(frag.tag, tc.input)] = frag.outputs[tc.input]  # pre-computed

  # Phase 2: Check Level-0 solutions
  for frag in fragments:
    if all(memo[(frag.tag, tc.input)] == tc.expected for tc in test_cases):
      yield frag.expr  # direct hit

  # Phase 3: Compose depth 2..max_depth
  for depth in [2, 3, ...max_depth]:
    for (inner, outer) in type_compatible_pairs(fragments):
      # inner: input_type → mid_type
      # outer: mid_type → return_type
      intermediate = {tc.input: memo[(inner.tag, tc.input)] for tc in test_cases}
      # Evaluate outer on inner's outputs
      composed_out = evaluate_fragment(outer, intermediate)  # may need Lean call
      if all(composed_out[tc.input] == tc.expected for tc in test_cases):
        yield compose_expr(inner.expr, outer.expr)
```

**Key optimization**: Level-0 outputs are pre-computed (already in expressions.jsonl). Level-1 compositions need to evaluate `outer(inner_output)` — but if `inner_output` matches a known test-input pattern for `outer`, we can look it up. Only truly novel intermediate values need a Lean call.

### When Does Compositional Search Run?

**Per-task, before MCTS.** Given a `SynthesisTask`, run `COMPOSE` once to produce candidate tactics. These feed into `CompositionalPolicy.propose_tactics()` for every MCTS expansion on that task. This amortizes the search cost across all rollouts.

The search is fast because:
1. Level-0 outputs are cached (no Lean calls)
2. Level-1 composition is Python dict lookup + comparison (~60² = 3600 pairs, microseconds)
3. Level-2 needs ~60³ pairs but type filtering reduces this to ~500, and most prune at the first test case
4. Only final candidates get one oracle `check_compile` call for verification

## Phases

### Phase 0: Fragment Catalog (1-2 hours)

Extract Level-0 primitives from `expressions.jsonl` into a structured catalog with pre-computed outputs on a **standard set of test inputs** covering all input types.

**File**: `lean_mcts/synth/fragments.py`

```python
@dataclass
class Fragment:
    tag: str                    # e.g. "filter_gt0"
    expr: str                   # e.g. "xs.filter (fun x => decide (x > 0))"
    input_type: str             # e.g. "List Int"
    output_type: str            # e.g. "List Int"
    outputs: dict[str, str]     # {input_string: output_string}

class FragmentCatalog:
    def __init__(self, expressions_path: str):
        self.fragments = load_level0(expressions_path)

    def by_input_type(self, itype: str) -> list[Fragment]:
        ...
    def by_output_type(self, otype: str) -> list[Fragment]:
        ...
    def composable_pairs(self, input_type: str, output_type: str) -> list[tuple[Fragment, Fragment]]:
        """Return (inner, outer) pairs where inner.input_type == input_type,
           outer.output_type == output_type, inner.output_type == outer.input_type."""
        ...
```

Additionally:
- Evaluate fragments on **all standard test inputs for all tasks**, not just the 5 fixed inputs from enumerate.py. This ensures we have cached outputs for any benchmark task.
- Store the extended outputs in `fragment_cache.json` — one-time cost, reusable.

### Phase 1: Partial Evaluator (2-3 hours)

Build a thin evaluation service that can evaluate a Lean expression on concrete inputs.

**File**: `lean_mcts/synth/partial_eval.py`

```python
class PartialEvaluator:
    """Evaluate Lean expressions on concrete inputs via Pantograph daemon."""

    def __init__(self, client):
        self.client = client  # PantographClient
        self._cache = {}      # (expr, input_str) → output_str

    def evaluate(self, expr: str, input_type: str, return_type: str,
                 inputs: list[str]) -> dict[str, str]:
        """Evaluate expr on each input, return {input: output}."""
        ...

    def evaluate_batch(self, exprs: list[str], input_type: str, return_type: str,
                       inputs: list[str]) -> list[dict[str, str]]:
        """Batch evaluate multiple expressions."""
        ...
```

The evaluator builds a temp Lean `def` + `#eval` (same pattern as `_build_warm_eval_code` in oracle.py) and calls `check_compile`. Results are cached.

**Two modes:**
1. **Cached lookup**: If the expression is a known fragment and the input is in `fragment_cache.json`, return immediately (0 Lean calls)
2. **Live evaluation**: Build and compile via daemon (~150ms per call, batched)

### Phase 2: Compositional Search Engine (3-4 hours)

The core of the sprint. Given a task specification (input_type, return_type, test_cases), construct programs by composing fragments.

**File**: `lean_mcts/synth/compose.py`

```python
class CompositionSearch:
    """Bottom-up program construction from typed fragments."""

    def __init__(self, catalog: FragmentCatalog, evaluator: PartialEvaluator):
        self.catalog = catalog
        self.evaluator = evaluator

    def search(self, task: SynthesisTask, max_depth: int = 3,
               max_candidates: int = 50) -> list[str]:
        """Return tactic strings that satisfy the task's test cases."""
        ...
```

#### Depth-1 (direct)
For each fragment with matching `(input_type, return_type)`, check if pre-computed outputs match all test cases. Pure Python, no Lean calls.

#### Depth-2 (two-step composition)
For each `(inner, outer)` pair where:
- `inner.input_type == task.input_type`
- `outer.output_type == task.return_type`
- `inner.output_type == outer.input_type`

Compute `outer(inner(input))` for each test case:
1. Look up `inner.outputs[input]` (cached)
2. Evaluate `outer` on `inner`'s output (may be cached or need Lean call)
3. If all match expected → yield `exact inner.expr |> outer_transform` or equivalent

**Expression construction**: Given `inner = "xs.filter (fun x => decide (x > 0))"` and `outer = "xs.length"`, the composed expression is:
- Pipeline style: `(xs.filter (fun x => decide (x > 0))).length`
- The `outer.expr` uses `xs` as its variable; we substitute `xs` → `(inner.expr)` to get the composed form

This substitution needs care — the fragments use `xs` as their conventional input variable. For composition, we replace the outermost `xs` reference with the inner expression in parentheses.

#### Depth-3 (three-step composition)
Same as depth-2 but chains three fragments. Type filtering keeps this tractable:
- ~60 fragments × ~60 fragments × ~60 fragments = 216K
- After type filtering: ~500-2000 triples (most types don't chain)
- After first-test-case pruning: ~10-50 survivors
- After all-test-case verification: ~1-5 candidates

#### Pruning Strategy

1. **Type filter** (free): Only compose fragments with compatible types
2. **First-test-case filter** (cheap): Evaluate on test_cases[0] only; if it fails, skip remaining
3. **All-test-case filter** (still cheap if cached): Check remaining test cases
4. **Oracle verification** (one Lean call per candidate): Final type-check via `check_compile`

### Phase 3: CompositionalPolicy Integration (2-3 hours)

New policy class in `lean_mcts/search/policy.py`.

```python
class CompositionalPolicy:
    """Propose tactics by composing fragments against task test cases."""

    def __init__(self, catalog: FragmentCatalog, evaluator: PartialEvaluator,
                 max_depth: int = 3):
        self.search = CompositionSearch(catalog, evaluator)
        self.max_depth = max_depth
        self._task_cache = {}  # task_name → list[str]

    def set_task(self, task: SynthesisTask):
        """Pre-compute compositional candidates for this task."""
        if task.name not in self._task_cache:
            self._task_cache[task.name] = self.search.search(
                task, max_depth=self.max_depth)

    def propose_tactics(self, state: object, k: int = None) -> list[str]:
        """Return pre-computed compositional tactics (task must be set first)."""
        ...
```

Then a hybrid that replaces `HybridGoalConditionedPolicy`:

```python
class HybridCompositionalPolicy:
    """GoalConditioned (generative) + Compositional (spec-driven) + ExpandedFixed (fallback)."""

    def __init__(self, ...):
        self.gc = GoalConditionedPolicy(...)
        self.comp = CompositionalPolicy(...)
        self.fixed = ExpandedFixedPolicy(...)  # retained as final fallback

    def propose_tactics(self, state, k=None) -> list[tuple[str, float]]:
        # 1. GoalConditioned: novel tactics with log-probs (highest priority)
        # 2. Compositional: spec-derived tactics (medium priority)
        # 3. ExpandedFixed: curated fallback (lowest priority)
        ...
```

**Benchmark runner change**: Pass the `SynthesisTask` to the policy before MCTS starts, so `CompositionalPolicy.set_task()` can pre-compute candidates.

### Phase 4: Extend Fragment Vocabulary (1-2 hours)

The current 60 Level-0 fragments are heavily biased toward `List Int` operations. Add fragments for:

1. **Higher-order composition operators** (not in Level-0 today):
   - `List.range n` (Nat → List Nat) — bridge between Nat-input and list operations
   - `(·, ·)` tuple constructors — bridge to product types
   - `Prod.fst`, `Prod.snd` — destructors
   - `List.zip`, `List.unzip` — pair-list operations

2. **Partial application fragments**:
   - `fun x => ...` wrappers that turn two-arg operations into one-arg
   - `(· + 1)`, `(· * 2)`, etc. as standalone fragments

3. **Identity and plumbing**:
   - `id` — enables passthrough in one branch of a composition
   - `fun xs => (f xs, g xs)` — parallel composition (enables product types without `constructor`)

These don't change the search algorithm — they just expand what compositions are expressible.

### Phase 5: Novel Benchmark Tasks (1-2 hours)

Create tasks specifically designed to require compositions NOT in expressions.jsonl, to validate that compositional search finds them.

**File**: `lean_mcts/env/FormalNav/FormalNav/TasksCompositional.lean`

| Task | Signature | Required Composition | In expressions.jsonl? |
|------|-----------|---------------------|----------------------|
| `count_gt_median` | `List Int → Nat` | `(xs.filter (fun x => decide (x > xs.length / 2))).length` | No (dynamic threshold) |
| `sum_of_squares` | `List Int → Int` | `(xs.map (fun x => x * x)).foldl (· + ·) 0` | Yes (baseline) |
| `range_sum` | `Nat → Nat` | `(List.range n).foldl (· + ·) 0` | No (Nat→Nat via range) |
| `count_unique` | `List Int → Nat` | `xs.eraseDups.length` | No (dedup + length) |
| `reverse_filter` | `List Int → List Int` | `(xs.filter (fun x => decide (x > 0))).reverse` | No (filter + reverse) |
| `sum_positives_squared` | `List Int → Int` | `(xs.filter pos).map square \|>.foldl (+) 0` | No (3-step) |
| `max_abs` | `List Int → Nat` | `(xs.map natAbs).foldl max 0` | Maybe (check) |
| `sorted_positives` | `List Int → List Int` | `(xs.filter pos).mergeSort (· ≤ ·)` | No (filter + sort) |

These tasks test:
- **Depth-2 novel**: compositions where both fragments exist but the pair was never enumerated
- **Depth-3 novel**: three-step pipelines
- **Cross-type**: Nat → List Nat → Nat via `List.range` bridge
- **Baseline**: known compositions that must still work

### Phase 6: Benchmark + Comparison (2-3 hours)

Run full benchmarks comparing three configurations:

1. **ExpandedFixed (baseline)**: Current expressions.jsonl lookup
2. **Compositional only**: Fragment search, no expressions.jsonl
3. **HybridCompositional**: GoalConditioned + Compositional + ExpandedFixed fallback

For each configuration, run on:
- All existing tiers (T2, V2, Hard, Recurrence, Multi-step, Deep multi-step, Fundamentals)
- New compositional tasks (Phase 5)
- 50 rollouts for stable tiers, 200 rollouts for harder ones

**Key questions to answer:**
1. Does Compositional match ExpandedFixed on existing tiers? (should be ≥)
2. Does Compositional solve new tasks that ExpandedFixed cannot?
3. How many Lean calls does compositional search need per task? (target: <50)
4. What's the wall-clock overhead of pre-computing compositions? (target: <5s/task)

### Phase 7: Analysis + Lessons Learned (1 hour)

Document:
- Which compositions were found by search that weren't in expressions.jsonl
- Where compositional search failed (expression construction patterns it can't handle)
- Lean call count and latency breakdown
- Whether ExpandedFixed fallback was ever needed
- Path forward: what fragment vocabulary extensions would unlock more tasks

## Success Criteria

| Metric | Minimum | Target | Stretch |
|--------|---------|--------|---------|
| T2 (compositional, no expressions.jsonl) | 8/10 | 10/10 | 10/10 |
| V2 (compositional, no expressions.jsonl) | 15/20 | 18/20 | 19/20 |
| Novel compositional tasks | 4/8 | 6/8 | 8/8 |
| Existing tiers no regression (hybrid) | all pass | all pass | all pass |
| Lean calls per task (avg) | <100 | <50 | <20 |
| Pre-computation time per task | <10s | <5s | <2s |
| New Python LOC | <800 | ~500-600 | <500 |

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Expression substitution is fragile (composing `xs.filter(...)` into `xs.length` needs `(xs.filter(...)).length` not `xs.length`) | High | Build a small expression AST for composition, not string replacement. Test on all 60×60 pairs |
| Intermediate values don't match cached outputs (different test inputs than enumerate.py used) | Medium | Phase 0 evaluates fragments on ALL benchmark test inputs, not just the 5 fixed ones |
| Depth-3 search is too slow (>10s per task) | Low | First-test-case pruning eliminates >95% of triples. Type filtering reduces space 100x. If still slow, limit to depth 2 and use GoalConditioned for depth 3 |
| Fragments can't express all ExpandedFixed patterns (e.g., `scanl`, custom lambdas) | Medium | Keep ExpandedFixed as fallback in hybrid. Catalog which patterns need dedicated fragments |
| Product-type tasks need `constructor` tactic, not `exact` | Low | Compositional search is for `exact` expressions only. Constructor decomposition stays in MCTS + GoalConditioned (already works) |
| Novel tasks are too easy / don't test the approach | Low | Include at least 2 depth-3 tasks and 2 cross-type tasks that definitely aren't in expressions.jsonl |

## Estimated Effort

| Phase | Hours |
|-------|-------|
| Phase 0: Fragment catalog | 1-2 |
| Phase 1: Partial evaluator | 2-3 |
| Phase 2: Compositional search engine | 3-4 |
| Phase 3: Policy integration | 2-3 |
| Phase 4: Extend fragment vocabulary | 1-2 |
| Phase 5: Novel benchmark tasks | 1-2 |
| Phase 6: Benchmark + comparison | 2-3 |
| Phase 7: Analysis | 1 |
| **Total** | **13-20** |

## Files to Create

| File | Purpose |
|------|---------|
| `lean_mcts/synth/fragments.py` | Fragment catalog: typed primitives with cached outputs |
| `lean_mcts/synth/partial_eval.py` | Lean expression evaluator via Pantograph daemon |
| `lean_mcts/synth/compose.py` | Compositional search engine: bottom-up fragment composition |
| `lean_mcts/synth/fragment_cache.json` | Pre-computed fragment outputs on all benchmark test inputs |
| `lean_mcts/env/FormalNav/FormalNav/TasksCompositional.lean` | 8 novel benchmark tasks |
| `docs/implementation/sprint22/sprint22.md` | This plan |
| `docs/implementation/sprint22/sprint22_status.md` | Status tracker |
| `docs/implementation/sprint22/lessons_learned.md` | Post-sprint analysis |

## Files to Modify

| File | Changes |
|------|---------|
| `lean_mcts/search/policy.py` | Add `CompositionalPolicy` and `HybridCompositionalPolicy` classes |
| `lean_mcts/benchmark/tasks.py` | Add `COMPOSITIONAL_TASKS` list + tier |
| `lean_mcts/benchmark/run.py` | Add `"compositional"` tier; pass task to policy via `set_task()` before MCTS |
| `lean_mcts/env/FormalNav/FormalNav.lean` | Add `import FormalNav.TasksCompositional` |

## Non-Goals

- No changes to MCTS algorithm (PUCT, rollouts, backprop)
- No changes to oracle verification (still pass/fail on test cases)
- No changes to Pantograph daemon architecture
- No model retraining (GoalConditioned stays as-is — this sprint is about the non-learned component)
- No changes to constructor decomposition (multi-step stays in MCTS)
- No removal of ExpandedFixed (it becomes the fallback, not the primary)
- No GHC Core pipeline changes

## Design Decisions

### Why bottom-up composition (not top-down decomposition)?

Top-down (type-directed decomposition) requires knowing the "shape" of the program before filling in details. Bottom-up composition discovers programs by chaining concrete intermediate values — it's more robust because it verifies at each step rather than committing to a structure upfront. Bottom-up is the right foundation; top-down guidance can be layered on later as a heuristic over the search space.

### Why per-task pre-computation (not per-expansion)?

Running compositional search during each MCTS expansion would be redundant — the task's test cases don't change between rollouts. Pre-computing once and caching is both faster and simpler. The policy interface stays the same (`propose_tactics(state) → list[str]`).

### Why keep ExpandedFixed as fallback?

ExpandedFixed covers patterns that fragment composition can't express yet (e.g., custom lambdas like `fun (a, b) _ => (b, a + b)` for fibonacci). Until the fragment vocabulary is rich enough to subsume these, the fallback prevents regression. The sprint's success metric is how many tasks Compositional solves *without* falling back.

### Why not retrain the model this sprint?

This sprint validates the compositional search mechanism in isolation. Mixing in retraining would confound results — we wouldn't know if improvements came from better search or better training. Sprint 23 can use compositionally-discovered (state, tactic) pairs as new training data.
