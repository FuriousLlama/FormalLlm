# Sprint 24: Hierarchical MCTS — Skeleton-Guided Search for Deep Multi-Step

## Goal

Replace the flat single-tactic-per-expansion MCTS with a **hierarchical search** that decomposes structured goals (nested products) into macro-actions and independent sub-goal searches. Target: deep multi-step 2/6 → 5+/6 while recovering S21 peak (55/62).

## Motivation

Across 24 sprints, the deep multi-step bottleneck has resisted every data-scaling approach:

| Sprint | Approach | Deep MS Result | Why It Failed |
|--------|----------|----------------|---------------|
| S21 | 17 depth-3 training pairs | 2/6 | Too sparse (0.2% of data) |
| S23 | 934 depth-3 pairs (55x increase) | 2/6 | Data dilution regressed other tiers |
| S23 | Self-play (3 rounds) | 2/6 | Only 27 new pairs; convergence too fast |

The problem is not data — it's search architecture. Depth-3 proofs require **5 sequential correct decisions** (constructor, constructor, exact, exact, exact). The current flat MCTS explores one tactic per node expansion. At each level, the constructor competes with ~64 other candidates. The probability of the correct 5-step path being explored within 200-500 rollouts is combinatorially small.

**The insight**: Depth-3 proof structure is **deterministic** from the goal type. If the goal is `(A × B) × C`, the proof skeleton is *always* `[constructor, constructor, exact, exact, exact]`. There's no search needed for the constructors — only for the three leaf exacts. And each leaf exact is an independent depth-1 problem the policy already solves well.

This sprint separates **structural reasoning** (deterministic, from type analysis) from **semantic reasoning** (learned, from the policy), and searches them at different levels.

## Context

### Current State (End of Sprint 23)

| Tier | S21 (peak) | S23 (current) | Mechanism |
|------|-----------|---------------|-----------|
| T2 | 10/10 | 10/10 | ExpandedFixed |
| Hard | 4/5 | 5/5 | GoalConditioned |
| V2 | 19/20 | 17/20 | EF + GC hybrid |
| Recurrence | 6/6 | 5/6 | EF templates |
| Fundamentals | 9/10 | 9/10 | EF templates |
| Multi-step | 5/5 | 3/5 | Constructor + sub-goal exacts |
| Deep multi-step | 2/6 | 2/6 | **Stuck** |
| **Total** | **55/62** | **51/62** | |

### The 6 Deep Multi-Step Tasks

| Task | Return Type | Depth | Required Proof | Status |
|------|-------------|-------|----------------|--------|
| fold_and_length | Int × Nat | 2 | constructor → exact × 2 | SOLVED (rollout 16) |
| recurrence_and_sum | Int × Nat | 2 | constructor → exact × 2 | SOLVED (rollout 16) |
| sum_count_max | (Int × Nat) × Int | **3** | constructor → constructor → exact × 3 | UNSOLVED |
| stats_triple | (Int × Int) × Nat | **3** | constructor → constructor → exact × 3 | UNSOLVED |
| partition_lengths | (Nat × Nat) × Nat | **3** | constructor → constructor → exact × 3 | UNSOLVED |
| filter_both_ways | List Int × List Int | 2 | constructor → exact × 2 | UNSOLVED |

**Observation**: The solved tasks (fold_and_length, recurrence_and_sum) are depth-2. All three depth-3 tasks are unsolved. filter_both_ways is depth-2 but unsolved — likely an expression gap, not a search issue.

### Why Flat MCTS Fails at Depth-3

Current expansion for `(Int × Nat) × Int`:

```
Rollout 1: root → try "exact xs.foldl ..." → type error (dead end)
Rollout 2: root → try "exact xs.length" → type error (dead end)
...
Rollout 8: root → try "constructor" → valid! child node created
            child → try "exact xs.foldl ..." → type error on (Int × Nat)
Rollout 9: child → try "exact xs.length" → type error on (Int × Nat)
...
Rollout 23: child → try "constructor" → valid! grandchild created
             grandchild → try "exact xs.foldl ..." → ✓ for Int
             BUT: two more sub-goals remain (Nat, Int)
Rollout 24-200: searching for right exacts for all 3 sub-goals...
```

The tree wastes rollouts on dead-end exacts at every level before discovering constructors. With 64 policy candidates per expansion and constructor at median priority, the expected rollouts to reach depth-3 is ~O(k²) where k is the rank of constructor among candidates. For k=32 (median of 64), that's ~1024 rollouts just to *reach* the three sub-goals — before any of them are solved.

### Hierarchical MCTS Eliminates This

```
Rollout 1: Skeleton planner sees (Int × Nat) × Int
           → deterministic skeleton: [constructor, constructor]
           → execute both constructors atomically
           → 3 independent sub-goals: Int, Nat, Int
           → policy proposes exacts for each sub-goal independently
           → oracle verifies the full tactic sequence
           → if any exact fails: try next candidate for that sub-goal only
```

Sub-goal search is O(1) for EF-covered types, O(k) for GC-covered types. Total search cost: O(k₁ + k₂ + k₃) instead of O(k₁ × k₂ × k₃).

### Infrastructure Available

- Tiered inference from S23 (keep)
- 8-worker Pantograph daemon
- GoalConditioned LoRA (S21 adapter as backup)
- ExpandedFixed with 650 expressions
- Constructor boost mechanism (median log-prob insertion)
- Self-play pipeline

## Architecture

```
                          ┌─────────────────────┐
                          │   Goal Structure     │  NEW: deterministic type analysis
                          │   Analyzer           │  recognizes product nesting depth
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │  Skeleton Planner    │  NEW: maps goal types to
                          │  (rule-based)        │  tactic macro-sequences
                          └──────────┬──────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
    ┌─────────▼─────────┐ ┌─────────▼─────────┐ ┌─────────▼─────────┐
    │  Sub-Goal Search  │ │  Sub-Goal Search  │ │  Sub-Goal Search  │
    │  (existing GC+EF) │ │  (existing GC+EF) │ │  (existing GC+EF) │
    │  ⊢ T1             │ │  ⊢ T2             │ │  ⊢ T3             │
    └─────────┬─────────┘ └─────────┬─────────┘ └─────────┬─────────┘
              │                      │                      │
              └──────────────────────┼──────────────────────┘
                                     │
                          ┌──────────▼──────────┐
                          │  Tactic Assembler   │  NEW: stitch constructor chain
                          │  + Oracle Verify    │  + sub-goal exacts into proof
                          └─────────────────────┘
```

**Key principle**: No changes to the existing policy, training, or ExpandedFixed. This sprint modifies **search only** — the MCTS expansion and rollout strategy. The policy is used as-is for sub-goal solving.

## Phases

### Phase 0: Restore S21 Baseline (1 hour)

Recover the S21 peak (55/62) before making architectural changes.

**Steps:**
1. Restore S21 adapter: copy `train/goal_cond_adapter_pre_s21/` → `train/goal_cond_adapter/`
   - If `_pre_s21` doesn't exist, use seed 1337 from S21
   - Verify with `run.py` on T2 + V2 quick check
2. Keep S23 tiered inference in `benchmark/run.py` (it's inference-time only)
3. Keep S23 Hard 5/5 (oracle Nat parameter fix from S17/S20 is in the codebase)
4. Verify baseline: run full benchmark, expect 55/62 ± 1

**Expected result**: 55/62 restored. This is the baseline for all subsequent measurements.

**Deliverable**: Benchmark log confirming 55/62 baseline with S21 adapter.

### Phase 1: Goal Structure Analyzer (2 hours)

Build a deterministic module that analyzes Lean goal types and returns proof structure metadata.

**File**: `lean_mcts/search/goal_analyzer.py`

#### Type Parser

Parse the goal type string from Pantograph state into a structured representation:

```python
@dataclass
class GoalStructure:
    """Analyzed structure of a Lean goal type."""
    raw_type: str           # e.g., "(Int × Nat) × Int"
    is_product: bool        # True if top-level ×
    product_depth: int      # 0=atomic, 1=A×B, 2=(A×B)×C, 3=((A×B)×C)×D
    leaf_types: list[str]   # ["Int", "Nat", "Int"] — left-to-right
    constructor_count: int  # Number of constructors needed
    skeleton: list[str]     # ["constructor", "constructor", "exact", "exact", "exact"]

def analyze_goal(state_str: str) -> GoalStructure:
    """Extract goal type from Pantograph state and analyze its structure.

    Handles:
    - Simple types: Int, Nat, Bool, List Int, List Nat
    - Depth-1 products: A × B
    - Depth-2 products: (A × B) × C
    - Depth-3 products: ((A × B) × C) × D
    - Left-associative nesting (Lean default)
    """
```

#### Skeleton Generation

The skeleton is deterministic from the goal type:

| Goal Type | Skeleton | Leaf Types |
|-----------|----------|------------|
| `Int` | `[exact]` | `[Int]` |
| `Int × Nat` | `[constructor, exact, exact]` | `[Int, Nat]` |
| `(Int × Nat) × Int` | `[constructor, constructor, exact, exact, exact]` | `[Int, Nat, Int]` |
| `((A × B) × C) × D` | `[constructor, constructor, constructor, exact × 4]` | `[A, B, C, D]` |

The pattern: for a product of depth `d`, the skeleton has `d` constructors followed by `d+1` exacts.

#### State Parsing

Extract the focused goal type from multi-goal Pantograph states:

```
Input: "case fst\nxs : List Int\n⊢ Int × Nat\ncase snd\nxs : List Int\n⊢ Int"
Output: GoalStructure(raw_type="Int × Nat", is_product=True, product_depth=1, ...)
```

Uses the existing first-⊢ extraction logic from `policy.py` (S21 fix).

#### Tests

Unit tests for:
- Atomic types → depth 0, no constructors
- Depth-1 products → 1 constructor, 2 exacts
- Depth-2 products → 2 constructors, 3 exacts
- Depth-3 products → 3 constructors, 4 exacts
- Mixed nesting: `(Int × Nat) × (Bool × Int)` → depth-2 on each side
- Edge cases: `List (Int × Nat)` is NOT a product (it's a List type)

**Deliverable**: `goal_analyzer.py` with `analyze_goal()` and unit tests.

### Phase 2: Skeleton-Guided MCTS Expansion (4-5 hours)

The core architectural change. Add a new expansion mode to `mcts.py` that uses skeletons to guide multi-step proof construction.

**File**: `lean_mcts/search/mcts.py` (modify existing)

#### New Concept: Macro Expansion

When the goal analyzer detects a product type with depth ≥ 1, MCTS uses **macro expansion** instead of single-tactic expansion:

```python
def expand_with_skeleton(
    node: Node,
    env: PantographClient,
    policy_fn: Callable,
    analyzer: GoalAnalyzer,
    oracle_fn: Callable,
    n_candidates: int = 32,
) -> list[Node]:
    """Skeleton-guided macro expansion for product types.

    Instead of trying one tactic at a time:
    1. Execute all constructors atomically (deterministic from type)
    2. Collect the resulting sub-goal states
    3. For each sub-goal, generate exact candidates via policy
    4. Try combinations (greedy: best candidate per sub-goal)
    5. Oracle-verify the full tactic sequence
    6. On failure: try next-best candidate per failed sub-goal

    Returns list of child nodes (solved or terminal).
    """
```

#### Algorithm Detail

```
SKELETON_EXPAND(node, env, policy, oracle):
  1. structure ← analyze_goal(node.state)
  2. IF NOT structure.is_product:
       RETURN standard_expand(node, env, policy)  # fallback

  3. # Phase A: Execute constructor chain
     state ← node.state
     constructor_tactics ← []
     FOR i IN range(structure.constructor_count):
       result ← env.step(state, "constructor")
       IF result.error:
         RETURN standard_expand(node, env, policy)  # can't constructor; fallback
       state ← result.new_state
       constructor_tactics.append("constructor")

  4. # Phase B: Identify sub-goal states
     sub_goals ← parse_multi_goal_state(state)
     # sub_goals = [("case fst", "⊢ Int"), ("case snd", "⊢ Nat"), ...]
     ASSERT len(sub_goals) == structure.leaf_count

  5. # Phase C: Generate candidates for each sub-goal
     candidates_per_goal ← []
     FOR sg IN sub_goals:
       sg_state ← format_sub_goal_state(sg, node.state)
       candidates ← policy(sg_state)  # [(tactic, log_prob), ...]
       candidates_per_goal.append(candidates)

  6. # Phase D: Greedy combination + oracle verification
     FOR combo IN greedy_combinations(candidates_per_goal, max_attempts=n_candidates):
       full_sequence ← constructor_tactics + [c.tactic for c in combo]
       result ← oracle.verify_sequence(node.state, full_sequence, task)
       IF result.passed:
         RETURN [create_solved_node(node, full_sequence)]

  7. # Phase E: If no combination works, create partial tree
     # (constructor chain worked, but no exact combination passed oracle)
     constructor_child ← create_child(node, constructor_tactics, state)
     RETURN [constructor_child]  # standard MCTS continues from here
```

#### Greedy Combination Strategy

Instead of trying all N^k combinations (exponential), use a greedy strategy:

```python
def greedy_combinations(candidates_per_goal: list[list], max_attempts: int):
    """Generate candidate combinations in priority order.

    Strategy:
    1. Best-of-each: top-1 candidate per sub-goal (1 combination)
    2. One-hot swap: for each sub-goal, try candidates 2..k while
       holding others at top-1 (k × n_goals combinations)
    3. Beam: expand top-b per sub-goal (b^n_goals combinations)

    For 3 sub-goals with 32 candidates each:
    - Step 1: 1 combo
    - Step 2: 3 × 31 = 93 combos
    - Step 3: 5^3 = 125 combos (beam=5)
    Total: ~219 combos (vs. 32^3 = 32,768 exhaustive)
    """
```

This leverages sub-goal **independence**: if the Int sub-goal is wrong, swapping the Nat sub-goal won't help. One-hot swap isolates which sub-goal is failing.

#### Integration with Existing MCTS Loop

The modification is minimal — add a branch in `expand()`:

```python
def expand(self, node, env, policy_fn, oracle_fn):
    structure = self.analyzer.analyze_goal(node.state)

    if structure.is_product and structure.product_depth >= 1:
        # Hierarchical path: macro expansion
        result = self.expand_with_skeleton(
            node, env, policy_fn, self.analyzer, oracle_fn
        )
        if result:
            return result
        # Fallback to standard if skeleton expansion fails

    # Standard path: single-tactic expansion (unchanged)
    return self.standard_expand(node, env, policy_fn, oracle_fn)
```

**Fallback is critical**: If `expand_with_skeleton` fails (constructor doesn't type-check, or the type is product-like but not decomposable), fall back to standard expansion. This ensures no regression on existing tasks.

#### State Execution via Daemon

The constructor chain needs sequential `goal_tactic` calls:

```python
async def execute_constructor_chain(self, env, initial_state, n_constructors):
    """Execute n constructors sequentially, return final multi-goal state."""
    state = initial_state
    for i in range(n_constructors):
        result = await env.goal_tactic(state.state_id, "constructor")
        if not result.ok:
            return None  # constructor failed; not a valid product decomposition
        state = result.new_state
    return state
```

Each `goal_tactic` call is ~5-10ms via the daemon (Unix socket, no subprocess). For depth-3: 2 constructor calls = ~10-20ms overhead. Negligible compared to policy inference (~200ms for 64 candidates).

#### Oracle Verification of Full Sequences

The oracle needs to verify the complete tactic sequence, not individual tactics:

```python
def verify_tactic_sequence(self, task, initial_state, tactics: list[str]):
    """Build Lean proof from tactic sequence and oracle-verify.

    Constructs:
      def _verify (xs : List Int) : (Int × Nat) × Int := by
        constructor
        constructor
        exact xs.foldl (· + ·) 0
        exact xs.length
        exact xs.foldl max 0
    """
```

This already works — the existing oracle builds `by <tactic1>\n<tactic2>\n...` proofs. The only change is passing a list of tactics instead of a single one.

**Deliverable**: Modified `mcts.py` with `expand_with_skeleton()`, `greedy_combinations()`, and fallback logic.

### Phase 3: Sub-Goal State Formatting (2 hours)

The tricky part: after executing constructors via the daemon, the resulting multi-goal state must be correctly parsed and formatted for the policy.

**File**: `lean_mcts/search/mcts.py` (add helper functions)

#### Multi-Goal State Parser

After 2 constructors on `(Int × Nat) × Int`, Pantograph returns a state like:

```
xs : List Int
⊢ Int
xs : List Int
⊢ Nat
xs : List Int
⊢ Int
```

Or with case prefixes:

```
case fst.fst
xs : List Int
⊢ Int
case fst.snd
xs : List Int
⊢ Nat
case snd
xs : List Int
⊢ Int
```

Need to parse this into 3 independent sub-goal states:

```python
def parse_sub_goals(multi_goal_state: str) -> list[SubGoal]:
    """Parse Pantograph multi-goal state into individual sub-goals.

    Returns list of SubGoal(case_prefix, context, goal_type) in order.
    Each can be formatted independently for the policy.
    """
```

#### Sub-Goal Formatting for Policy

Each sub-goal must be formatted as if it were a standalone goal state, matching the GoalConditioned training data format:

```python
def format_for_policy(sub_goal: SubGoal) -> str:
    """Format sub-goal state for GoalConditioned policy input.

    Produces:  "case fst\nxs : List Int\n⊢ Int"
    or:        "xs : List Int\n⊢ Int"  (no case prefix)

    Both formats are in training data (generate_compositional_data.py
    emits both). The policy handles either.
    """
```

#### Empirical Validation

Before Phase 2 integration, validate state formatting against the daemon:

1. Start a daemon session
2. Set up a `(Int × Nat) × Int` goal
3. Execute `constructor` twice
4. Capture the resulting multi-goal state
5. Parse it with the new parser
6. Verify each sub-goal matches expected types
7. Verify policy can generate valid exacts for each

This catches formatting bugs early (the S21 `split("⊢")[-1]` bug was exactly this class of problem).

**Deliverable**: `parse_sub_goals()`, `format_for_policy()`, and daemon validation script.

### Phase 4: filter_both_ways Investigation (1 hour)

The 6th deep multi-step task `filter_both_ways` (List Int × List Int) is depth-2, not depth-3. It should be solvable by the hierarchical search (constructor → 2 exacts). Its failure suggests an expression gap.

**Steps:**
1. Analyze the task: what exact expressions are needed for `List Int × List Int`?
2. Check if the required filter predicates exist in `expressions.jsonl`
3. If missing: add them (this is an EF coverage fix, not a search fix)
4. If present: run with hierarchical search to see if it solves

**Expected**: filter_both_ways either solves immediately with the right expressions, or reveals a formatting issue with List-type sub-goals.

**Deliverable**: filter_both_ways diagnosis + fix if expression gap.

### Phase 5: Benchmark + Ablation (3 hours)

#### Full Benchmark

Run all 62 tasks with the hierarchical MCTS on the S21 adapter:

| Tier | Expected (hierarchical) | Baseline (flat, S21) |
|------|------------------------|---------------------|
| T2 | 10/10 | 10/10 |
| Hard | 5/5 | 4/5 |
| V2 | 19/20 | 19/20 |
| Recurrence | 6/6 | 6/6 |
| Fundamentals | 9/10 | 9/10 |
| Multi-step | 5/5 | 5/5 |
| Deep multi-step | **5/6** | 2/6 |
| **Total** | **59/62** | 55/62 |

#### Ablation: Skeleton vs. Boost-Only

Compare three configurations:
1. **Flat MCTS + constructor at median** (S21 baseline): 2/6 deep MS
2. **Flat MCTS + constructor at top-1** (boost upgrade only): expected 3-4/6
3. **Hierarchical MCTS + skeleton** (full change): expected 5/6

The boost-only ablation isolates how much improvement comes from search architecture vs. simple priority reranking.

#### Rollout Budget Analysis

For each deep multi-step task, record:
- Rollout number at which the proof was found
- Number of skeleton expansions attempted
- Number of greedy combinations tried per skeleton expansion
- Which sub-goals were hardest (most candidate swaps needed)

This data informs whether the hierarchical approach generalizes or just happens to work on these 6 tasks.

#### Regression Check

Run all non-deep-multi-step tiers to verify no regression. The fallback path in `expand()` should make this a no-op for non-product goals.

**Deliverable**: Full benchmark results, ablation table, rollout analysis.

### Phase 6: Self-Play on Hierarchical Search (2 hours, optional)

If Phase 5 shows improvement, run 1-2 rounds of self-play with the hierarchical MCTS to:
1. Extract new (state, tactic) pairs from depth-3 successes
2. Retrain GC policy on expanded data (using hierarchical search to generate training signal)
3. Check if the retrained model improves sub-goal exact selection

**Key difference from S23**: Self-play with hierarchical MCTS generates **correct depth-3 tactic sequences** that the model can learn from, because the search actually finds them. S23's flat MCTS never found depth-3 proofs, so self-play had no signal to extract.

This phase is optional because the hierarchical search may be sufficient without retraining. The rule-based skeleton + existing policy may be all that's needed.

### Phase 7: Documentation (1 hour)

- Create `sprint24_status.md` with results
- Create `lessons_learned.md`
- Update `CLAUDE.md` with new architecture notes
- Update `MEMORY.md`

## Success Criteria

| Metric | Minimum | Target | Stretch |
|--------|---------|--------|---------|
| Deep multi-step | 4/6 | 5/6 | 6/6 |
| S21 baseline recovery | 53/62 | 55/62 | 55/62 |
| Total (with hierarchical) | 57/62 | 59/62 | 61/62 |
| No regression on T2+V2+Hard | 33/35 | 34/35 | 35/35 |
| Rollouts for depth-3 solve | < 200 | < 50 | < 10 |
| Greedy combos per solve | < 100 | < 30 | < 10 |

**Primary metric**: Deep multi-step improvement. 4/6 = minimum (proves architecture works). 5/6 = target (all depth-3 tasks solved). 6/6 = stretch (filter_both_ways also solved).

**Anti-regression metric**: Total score must not drop below S21 baseline (55/62). The hierarchical expansion has a fallback path, so regression should be impossible, but we verify.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Constructor doesn't type-check on some nested products | Low | Medium | Fallback to standard expansion. Validate on all 6 tasks in Phase 1 |
| Pantograph multi-goal state format is different than expected | Medium | High | Phase 3 empirical validation catches this before integration |
| Sub-goal states don't match policy training data format | Medium | High | Phase 3 formats both with and without case prefix; policy trained on both |
| Greedy combination strategy misses correct combo | Low | Medium | One-hot swap explores all single-variable failures; beam=5 covers interactions |
| Hierarchical expansion slows down non-product tasks | Low | Low | Fallback path is a single `analyze_goal()` call (~microseconds). Product detection is string matching for `×` |
| S21 adapter can't be restored (backup missing) | Low | Critical | Check backup exists in Phase 0 before any changes. Seed 1337 checkpoint as secondary backup |
| filter_both_ways needs expressions not in EF | Medium | Low | Phase 4 investigates independently. Even without it, 5/6 is the target |
| Self-play regression from S23 | N/A | N/A | Phase 6 is optional. No retraining unless Phase 5 results are positive |

## Estimated Effort

| Phase | Hours | Dependencies |
|-------|-------|-------------|
| Phase 0: Restore S21 baseline | 1 | None |
| Phase 1: Goal structure analyzer | 2 | None |
| Phase 2: Skeleton-guided MCTS | 4-5 | Phase 1 |
| Phase 3: Sub-goal state formatting | 2 | Phase 1 |
| Phase 4: filter_both_ways investigation | 1 | Phase 0 |
| Phase 5: Benchmark + ablation | 3 | Phase 0-3 |
| Phase 6: Self-play (optional) | 2 | Phase 5 positive |
| Phase 7: Documentation | 1 | Phase 5 |
| **Total** | **14-17** | |

Phases 1, 3, and 4 can run in parallel. Phase 2 depends on Phase 1. Phase 5 depends on Phase 0-3.

## Files to Create

| File | Purpose |
|------|---------|
| `lean_mcts/search/goal_analyzer.py` | Goal type parser + skeleton planner |
| `lean_mcts/search/test_goal_analyzer.py` | Unit tests for goal analyzer |
| `lean_mcts/search/test_skeleton_expand.py` | Integration tests for macro expansion |
| `docs/implementation/sprint24/sprint24.md` | This plan |
| `docs/implementation/sprint24/sprint24_status.md` | Status tracker |
| `docs/implementation/sprint24/lessons_learned.md` | Post-sprint findings |

## Files to Modify

| File | Changes |
|------|---------|
| `lean_mcts/search/mcts.py` | Add `expand_with_skeleton()`, `greedy_combinations()`, hook into `expand()` with fallback |
| `lean_mcts/benchmark/run.py` | Pass `GoalAnalyzer` to MCTS constructor; add `--hierarchical` flag |
| `lean_mcts/search/policy.py` | No changes (used as-is for sub-goal solving) |

## Non-Goals

- No changes to GoalConditioned policy or training pipeline
- No changes to ExpandedFixed expressions (except filter_both_ways if needed)
- No changes to Pantograph daemon or client
- No new Lean tasks
- No base model change
- No fragment vocabulary expansion
- No curriculum training or data augmentation
- No GHC Core pipeline changes

## Design Decisions

### Why rule-based skeletons instead of a learned planner?

The proof skeleton for product types is **fully determined by the goal type**. `(A × B) × C` always decomposes as `constructor → constructor → 3 exacts`. There is no ambiguity, no search, no learning needed. A learned planner would need training data to discover what a type-checker already knows. Rule-based is simpler, faster, and provably correct.

If future tasks require non-product structured proofs (e.g., `induction → constructor → exact`), a learned planner becomes necessary. But for Sprint 24's target (depth-3 products), rules suffice.

### Why greedy combinations instead of exhaustive search?

For 3 sub-goals with 32 candidates each, exhaustive search is 32,768 combinations. Greedy (best-of-each + one-hot swap) covers ~94 combinations. But sub-goals are independent — if the Int sub-goal fails, the Nat candidate doesn't matter. One-hot swap finds the failing sub-goal in ≤ 3 × 31 = 93 attempts. This is sound because oracle failures are per-sub-goal, not per-combination.

The exception is when two sub-goals interact (e.g., both return `Int` but must return *different* Ints). The beam extension (b=5) handles this by trying small combinations of non-top candidates.

### Why restore S21 instead of building on S23?

S23's data dilution caused a -4 regression (55→51). The hierarchical MCTS is a search-level change, not a data-level change. It works regardless of which adapter is loaded. Starting from the stronger baseline (55/62) means any improvement from hierarchical search is additive, not recovery.

### Why not just boost constructor to top-1 priority?

Boosting constructor priority is the simplest fix and will likely help (2/6 → 3-4/6). But it doesn't solve the fundamental problem: after the constructor chain completes, the flat MCTS still searches each sub-goal sequentially, sharing rollout budget. Hierarchical search solves sub-goals independently, using the full candidate set per sub-goal. The ablation in Phase 5 measures the delta.

### Why is this better than more training data?

Sprint 23 proved that 55x more depth-3 training data (17 → 934 pairs) didn't improve deep multi-step. The model can learn to propose constructor (it already does at median priority). The problem is that flat MCTS doesn't explore the constructor path deeply enough before budget exhaustion. This is a search problem, not a learning problem.

## Connection to Research

| Paper | How This Sprint Applies It |
|-------|---------------------------|
| HTPS (Lample et al.) | Hierarchical proof search: high-level tactic selection + low-level term generation. Our skeleton = high-level, policy = low-level |
| AlphaProof (DeepMind) | Separating "proof strategy" from "proof execution." Our skeleton planner is the strategy; MCTS + policy is execution |
| Lean Copilot | Premise selection as a separate module from tactic generation. Our goal analyzer is premise-selection-like (selecting the proof structure) |
| Curriculum Self-Play | If Phase 6 works: hierarchical search generates correct depth-3 proofs → self-play extracts them → model learns from successful search |

## Appendix: Expected Proof Traces

### sum_count_max: (Int × Nat) × Int

```
Goal: xs : List Int ⊢ (Int × Nat) × Int

Skeleton: [constructor, constructor, exact, exact, exact]

Step 1: constructor
  → Sub-goal A: case fst ⊢ Int × Nat
  → Sub-goal B: case snd ⊢ Int

Step 2: constructor (on sub-goal A)
  → Sub-goal A1: case fst.fst ⊢ Int
  → Sub-goal A2: case fst.snd ⊢ Nat
  → Sub-goal B: case snd ⊢ Int (unchanged)

Step 3-5: exact for each leaf
  A1: exact xs.foldl (· + ·) 0           # sum
  A2: exact xs.length                     # count
  B:  exact xs.foldl max 0               # max (or similar)

Full proof: by constructor; constructor; exact xs.foldl (· + ·) 0; exact xs.length; exact xs.foldl max 0
```

### stats_triple: (Int × Int) × Nat

```
Goal: xs : List Int ⊢ (Int × Int) × Nat

Skeleton: [constructor, constructor, exact, exact, exact]

Exacts:
  A1 (Int): exact xs.foldl min 0
  A2 (Int): exact xs.foldl max 0
  B  (Nat): exact xs.length

Full proof: by constructor; constructor; exact xs.foldl min 0; exact xs.foldl max 0; exact xs.length
```

### partition_lengths: (Nat × Nat) × Nat

```
Goal: xs : List Int ⊢ (Nat × Nat) × Nat

Skeleton: [constructor, constructor, exact, exact, exact]

Exacts:
  A1 (Nat): exact (xs.filter (fun x => decide (x > 0))).length
  A2 (Nat): exact (xs.filter (fun x => decide (x ≤ 0))).length
  B  (Nat): exact xs.length

Full proof: by constructor; constructor; exact (xs.filter ...).length; exact (xs.filter ...).length; exact xs.length
```

Note: partition_lengths has ambiguous sub-goals (all three are Nat). The greedy combinator will need to try multiple candidates for A1 and A2 since the policy sees identical types. This is where one-hot swap is essential — the oracle disambiguates which filter predicate is correct for each position.
