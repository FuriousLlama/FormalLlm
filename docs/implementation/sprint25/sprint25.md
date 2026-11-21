# Sprint 25: Program Synthesis — Match, Recursion, and the Lean-as-Language Pivot

## Goal

Extend the synthesis system from **library-function composition** to **structural recursion with pattern matching**, reframing the benchmark from "proof synthesis" to "program synthesis in Lean 4." Target: 15+ new programming tasks solvable via `match`/`if`/`List.rec`/`Nat.rec` patterns, while maintaining the existing 59/62 baseline.

## Motivation

### The Pivot

Across 24 sprints, the project has empirically demonstrated a thesis from `docs/theoretical/philosophical_foundation.md`: **formal mathematical structure enables more reliable exploration than natural language**. Every solution the system finds is guaranteed correct — type-checked by Lean's kernel, verified by oracle test cases. No hallucinations possible.

But the current system synthesizes only **library function compositions** (`xs.foldl (· + ·) 0`). Real programs require **structural recursion** — decomposing data and rebuilding it. In Lean 4, proofs and programs are identical (Curry-Howard), so the MCTS + oracle infrastructure applies unchanged. The vocabulary just needs to expand.

### Why Now

The system already handles recursive patterns — Sprint 14's `FixedPolicy` includes `List.rec`, `Nat.rec`, `match`, and `if/then/else` expressions. But these are **hardcoded as monolithic strings**, not composed by search. The policy proposes `"exact List.rec 0 (fun h _ acc => acc + h) xs"` as a single token, not as a structured term built from parts.

Sprint 25 makes two shifts:

1. **New benchmark tasks** that require `match` + recursive construction (not just folds)
2. **Skeleton analysis for recursive types** — detect `List α` or `Nat` input, propose `match` decomposition, search base case and recursive case independently

This is the same architectural insight as Sprint 24 (deterministic structure + independent sub-goal search), applied to recursion instead of products.

### Connection to the Philosophical Foundation

> *"Develop different functional machines for different regions of the landscape."*

Product types have rigid structure → skeleton planner handles them deterministically.
Recursive types have rigid structure too → `match xs with | [] => ? | h :: t => ?` is always the decomposition for `List α`. The base case and recursive case are **independent sub-goals**, just like product sub-goals.

The learned policy's job shifts from "propose the whole expression" to "fill in the `?` holes in a structurally determined skeleton." This is the hybrid the philosophical document calls for: formal structure constraining statistical exploration.

## Context

### Current State (End of Sprint 24)

| Tier | Result | Mechanism |
|------|--------|-----------|
| T2 | 10/10 | ExpandedFixed |
| Hard | 4/5 | GC hybrid |
| V2 | 19/20 | EF + GC |
| Recurrence | 6/6 | EF templates |
| Fundamentals | 9/10 | EF templates |
| Multi-step | 4/5 | Constructor + exacts |
| Deep multi-step | 6/6 | Skeleton-guided |
| **Total** | **59/62** | |

### What the System Can Synthesize Today

- **Library compositions**: `xs.filter (·>0) |>.foldl (·+·) 0`
- **Product decomposition**: `constructor` → independent `exact` per field
- **Monolithic recursive patterns**: `List.rec 0 (fun h _ acc => acc + h) xs` (but only as a single pre-written string in the policy)

### What It Cannot Synthesize

- **Custom recursive functions**: `match xs with | [] => [] | h :: t => f h :: recurse t`
- **Conditional recursion**: early termination, guarded recursion
- **Two-argument structural recursion**: `zip xs ys`
- **Nested pattern matching**: `match xs with | [] => 0 | [x] => x | x :: y :: _ => x + y`

### The Gap

The existing `FixedPolicy` already contains ~40 recursive expressions (lines 146-190 of `policy.py`). These solve 9/10 Fundamentals tasks. But they're **opaque strings** — the system doesn't understand their structure. It can't compose a new `List.rec` step function it hasn't seen before.

Sprint 25 decomposes these into **skeletons with holes**, so the search can fill in novel step functions.

## Architecture

```
                          ┌─────────────────────┐
                          │  Goal Type Analyzer  │  EXTENDED: detect List/Nat input types
                          │  (goal_analyzer.py)  │  + product detection (S24)
                          └──────────┬──────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │ Product goal   │ Recursive input │ Neither
                    ▼                ▼                  ▼
           ┌──────────────┐ ┌──────────────────┐ ┌─────────────┐
           │ S24 Skeleton │ │ NEW: Recursion   │ │ Standard    │
           │ (constructor │ │ Skeleton         │ │ MCTS        │
           │  chains)     │ │ (match → holes)  │ │ (unchanged) │
           └──────┬───────┘ └──────┬───────────┘ └──────┬──────┘
                  │                │                      │
                  ▼                ▼                      ▼
           Sub-goal search   Hole search            Single-tactic
           (per product      (base case +            expansion
            field)           recursive case)
                  │                │                      │
                  └────────────────┴──────────────────────┘
                                   │
                          ┌────────▼────────┐
                          │  Oracle Verify  │  UNCHANGED
                          └─────────────────┘
```

### Recursion Skeleton

For a goal `(xs : List Int) → Nat`:

```
Skeleton: match xs with
          | [] => <HOLE_BASE>           -- base case, type Nat
          | h :: t => <HOLE_STEP>       -- recursive case, type Nat
                                        -- available: h : Int, t : List Int, result of recurse(t) : Nat
```

For a goal `(n : Nat) → Int`:

```
Skeleton: match n with
          | 0 => <HOLE_BASE>           -- base case, type Int
          | k + 1 => <HOLE_STEP>       -- recursive case, type Int
                                       -- available: k : Nat, result of recurse(k) : Int
```

The skeleton is **deterministic** from the input type — same insight as Sprint 24's product skeletons.

### Hole Filling Strategy

1. **Base case holes**: Small constant expressions. Policy proposes: `0`, `1`, `[]`, `true`, `false`, `none`, `(0, 0)`.
2. **Step case holes**: Expressions using `h` (head element), `acc` (recursive result), and arithmetic/list operations.
   - Policy candidates from EF: `acc + h`, `acc + 1`, `h :: acc`, `if h > 0 then acc + h else acc`, etc.
   - Policy candidates from GC: novel combinations the LoRA proposes.
3. **Combination**: Base × Step candidates, verified as a complete `List.rec base step xs` or equivalent `match` expression by oracle.

### Why `List.rec` Over `match` + Self-Reference

`List.rec` and `Nat.rec` are **non-recursive from Lean's perspective** — they're eliminators that Lean's kernel accepts without a termination proof. `match` + self-reference requires Lean to verify structural recursion termination, which adds complexity.

**Strategy**: Use `List.rec base (fun h _ acc => step) xs` as the skeleton format. This:
- Always terminates (by construction)
- Doesn't require the function name (no self-reference)
- Decomposes into exactly 2 holes: `base` and `step`
- Is already in the policy vocabulary (just not decomposed)

Later sprints can add `match` + self-reference for patterns `List.rec` can't express (e.g., zip, interleave).

## New Benchmark Tasks

### Category: Recursive Programs (15 tasks)

#### Tier R1: Simple Recursion (5 tasks) — `List.rec` with arithmetic step

```lean
-- sum_positive: sum only positive elements
def sum_positive (xs : List Int) : Int := by sorry
-- List.rec 0 (fun h _ acc => if decide (h > 0) then acc + h else acc) xs

-- count_negative: count elements < 0
def count_negative (xs : List Int) : Nat := by sorry
-- List.rec 0 (fun h _ acc => if decide (h < 0) then acc + 1 else acc) xs

-- last_or_zero: return last element or 0 if empty
def last_or_zero (xs : List Int) : Int := by sorry
-- List.rec 0 (fun h _ _ => h) xs

-- product_abs: product of absolute values
def product_abs (xs : List Int) : Int := by sorry
-- List.rec 1 (fun h _ acc => acc * (if decide (h < 0) then -h else h)) xs

-- max_or_zero: maximum element, 0 if empty
def max_or_zero (xs : List Int) : Int := by sorry
-- List.rec 0 (fun h _ acc => if decide (h > acc) then h else acc) xs
```

**Expected**: 5/5 at rollout 1 — these are decompositions of existing EF expressions. The test is whether skeleton search finds them faster than monolithic matching.

#### Tier R2: List-Building Recursion (5 tasks) — `List.rec` producing lists

```lean
-- keep_positive: filter to positive elements (no library filter)
def keep_positive (xs : List Int) : List Int := by sorry
-- List.rec [] (fun h _ acc => if decide (h > 0) then h :: acc else acc) xs
-- NOTE: builds list in reverse; need .reverse or right-fold

-- stutter: duplicate each element [1,2] → [1,1,2,2]
def stutter (xs : List Int) : List Int := by sorry
-- List.rec [] (fun h _ acc => h :: h :: acc) xs  (reversed)

-- take_evens: keep elements at even indices
def take_evens (xs : List Int) : List Int := by sorry
-- Needs index tracking via tuple accumulator or List.rec with Bool flag

-- prefix_sums: [1,2,3] → [1,3,6]
def prefix_sums (xs : List Int) : List Int := by sorry
-- Tuple accumulator: (running_sum, result_list)

-- nub_sorted: remove consecutive duplicates from sorted list
def nub_sorted (xs : List Int) : List Int := by sorry
-- Needs previous-element tracking via tuple accumulator
```

**Expected**: 3-4/5 — list-building via `List.rec` produces reversed results, which is a known difficulty. The policy must learn to append `.reverse` or use right-folding patterns.

#### Tier R3: Nat Recursion (5 tasks) — `Nat.rec` patterns

```lean
-- factorial: n!
def factorial (n : Nat) : Nat := by sorry
-- Nat.rec 1 (fun k acc => acc * (k + 1)) n

-- triangular: 1 + 2 + ... + n
def triangular (n : Nat) : Nat := by sorry
-- Nat.rec 0 (fun k acc => acc + (k + 1)) n

-- power_of_two: 2^n
def power_of_two (n : Nat) : Nat := by sorry
-- Nat.rec 1 (fun _ acc => acc * 2) n

-- fib: Fibonacci (needs tuple accumulator or List.range trick)
def fib (n : Nat) : Nat := by sorry
-- (List.range n).foldl (fun (a, b) _ => (b, a + b)) (0, 1) |>.1
-- OR Nat.rec (0, 1) (fun _ (a, b) => (b, a + b)) n |>.1

-- count_down_sum: n + (n-1) + ... + 1 + 0 (same as triangular, alternate impl)
def count_down_sum (n : Nat) : Nat := by sorry
-- Nat.rec 0 (fun k acc => acc + k + 1) n
```

**Expected**: 4-5/5 — `Nat.rec` is simpler than `List.rec` (no element to process, just counter).

### Updated Benchmark Totals

| Tier | Count | Expected |
|------|-------|----------|
| Existing (T2+Hard+V2+Rec+Fund+MS+DMS) | 62 | 59/62 (no regression) |
| R1: Simple recursion | 5 | 5/5 |
| R2: List-building recursion | 5 | 3-4/5 |
| R3: Nat recursion | 5 | 4-5/5 |
| **Total** | **77** | **71-73/77** |

## Phases

### Phase 0: Baseline Verification (1 hour)

Confirm 59/62 still holds with current code and adapter.

**Deliverable**: Benchmark run log showing 59/62.

### Phase 1: New Task Definitions (3 hours)

Create Lean task files and register them in the benchmark.

**Files**:
- `lean_mcts/env/FormalNav/FormalNav/TasksRecursivePrograms.lean` — 15 task definitions with `sorry`
- `lean_mcts/env/FormalNav/FormalNav/TasksRecursivePrograms/` — reference solutions for validation
- `lean_mcts/benchmark/tasks.py` — add `recursive_r1`, `recursive_r2`, `recursive_r3` categories

**Steps**:
1. Write 15 `def` stubs with type signatures in Lean
2. Write reference implementations and verify they compile
3. Define 3-5 test cases per task with edge cases (empty list, single element, all negative, etc.)
4. Add task entries to `tasks.py` with `category`, `return_type`, `input_type`
5. Verify: `lake build` passes, all reference solutions type-check

### Phase 2: Recursion Skeleton Analyzer (4 hours)

Extend `goal_analyzer.py` to detect recursive input types and produce recursion skeletons.

**File**: `lean_mcts/search/goal_analyzer.py`

**Changes**:
```python
@dataclass
class GoalStructure:
    # Existing fields (Sprint 24)
    raw_type: str
    is_product: bool
    product_depth: int
    leaf_types: list[str]
    constructor_count: int
    skeleton: list[str]

    # NEW: recursion analysis
    is_recursive_input: bool        # True if primary input is List/Nat
    recursive_input_type: str       # "List" or "Nat" or None
    recursive_element_type: str     # "Int" for List Int, None for Nat
    recursion_skeleton: str         # "List.rec" or "Nat.rec" or None
```

**Algorithm**:
1. Parse input variables from state string (already done for product analysis)
2. Check if primary input type matches `List <T>` or `Nat`
3. If yes, set `is_recursive_input = True` and determine skeleton type
4. Determine base case type from return type (e.g., `Nat` → `0`, `List Int` → `[]`, `Int` → `0`)
5. Determine step case signature from input + return types

**Tests**: Extend `test_goal_analyzer.py` with 15+ cases for recursion detection.

### Phase 3: Step-Function Candidate Generation (4 hours)

Generate candidate base cases and step functions for recursion holes.

**File**: `lean_mcts/search/policy.py` (extend) or new `lean_mcts/search/recursion_candidates.py`

**Base case candidates** (by return type):
```python
BASE_CASES = {
    "Nat": ["0", "1"],
    "Int": ["0", "1", "-1"],
    "Bool": ["true", "false"],
    "List Int": ["[]"],
    "List Nat": ["[]"],
    "Option Int": ["none"],
}
```

**Step function templates** (for `List.rec`):
```python
# h : element type, acc : return type
STEP_TEMPLATES_INT_TO_INT = [
    "fun h _ acc => acc + h",
    "fun h _ acc => acc - h",
    "fun h _ acc => acc * h",
    "fun h _ acc => if decide (h > 0) then acc + h else acc",
    "fun h _ acc => if decide (h < 0) then acc + 1 else acc",
    "fun h _ acc => if decide (h > acc) then h else acc",
    "fun h _ acc => if decide (h < acc) then h else acc",
    # ... more from existing EF expressions, decomposed
]

STEP_TEMPLATES_INT_TO_LIST_INT = [
    "fun h _ acc => h :: acc",
    "fun h _ acc => if decide (h > 0) then h :: acc else acc",
    "fun h _ acc => if decide (h < 0) then h :: acc else acc",
    "fun h _ acc => if decide (h % 2 = 0) then h :: acc else acc",
    "fun h _ acc => h :: h :: acc",
    # ...
]
```

**Combination**: For each (base, step) pair, construct:
```python
f"exact List.rec {base} ({step}) xs"
```

This produces ~20-50 candidate expressions per task, all structurally valid. Type-checking filters invalid combinations.

### Phase 4: Recursion Skeleton Expansion in MCTS (5 hours)

Integrate recursion skeletons into the MCTS expansion logic, alongside Sprint 24's product skeletons.

**File**: `lean_mcts/search/mcts.py`

**Logic** (in `expand()`, after product skeleton check):

```python
if goal_structure.is_recursive_input and not goal_structure.is_product:
    # Phase A: Generate candidate expressions from recursion templates
    candidates = generate_recursion_candidates(
        input_type=goal_structure.recursive_input_type,
        element_type=goal_structure.recursive_element_type,
        return_type=goal_structure.raw_type,
        input_var=goal_structure.input_variable,
    )

    # Phase B: Add GC candidates (LoRA may propose novel step functions)
    gc_candidates = policy_fn(node.state)
    candidates.extend(gc_candidates)

    # Phase C: Type-check each candidate via env.step()
    valid = []
    for candidate in candidates:
        result = env.step(state, f"exact {candidate}")
        if result.is_proof_finished:
            valid.append(candidate)

    # Phase D: Oracle verify valid candidates
    for candidate in valid:
        if oracle_fn([f"exact {candidate}"]):
            # SOLVED
            return build_solved_node(candidate)

    # Phase E: Fallback to standard MCTS with valid tactics as priors
    node.untried_tactics = remaining_candidates
```

**Key difference from product skeletons**: No sub-goal decomposition needed. The recursion skeleton produces a **single `exact` expression** (`List.rec base step xs`). The search is over (base, step) pairs, not independent sub-goals.

**Fallback**: If no recursion candidate works, fall through to standard MCTS expansion (which already has `List.rec` expressions in FixedPolicy).

### Phase 5: Benchmark and Iterate (3 hours)

Run the full benchmark (77 tasks) and iterate on failures.

**Steps**:
1. Run existing 62 tasks → confirm 59/62 (no regression)
2. Run R1 (5 tasks) → target 5/5
3. Run R2 (5 tasks) → target 3/5, analyze failures
4. Run R3 (5 tasks) → target 4/5
5. For failures: check if the reference solution is in the candidate space
   - If yes: search issue (budget, ordering)
   - If no: vocabulary gap (need more step templates)
6. Iterate: add missing templates, re-run

**Deliverable**: Full benchmark results table, per-task breakdown with rollout counts.

### Phase 6: Expressions Decomposition (3 hours)

Decompose existing monolithic EF expressions into (base, step) pairs to expand the recursion candidate pool systematically.

**File**: New script `lean_mcts/synth/decompose_expressions.py`

**Logic**: Parse `expressions.jsonl` entries that use `List.rec` or folds, extract:
- The base case value
- The step function lambda
- The input variable

Output: `lean_mcts/synth/recursion_templates.json` — structured (base, step, input_type, return_type) records that Phase 4's candidate generator can recombine.

**Why**: The 650 EF expressions contain ~40 `List.rec`/fold patterns. Decomposing them lets the system **recombine** base cases and step functions it hasn't seen together — e.g., use the step function from `sum_positive` with the base case from `product`, producing a novel valid expression.

### Phase 7: Documentation and Status (1 hour)

Update sprint status, lessons learned, and CLAUDE.md.

## Success Criteria

| Metric | Minimum | Target | Stretch |
|--------|---------|--------|---------|
| Existing baseline | 58/62 | 59/62 | 59/62 |
| R1 (simple recursion) | 4/5 | 5/5 | 5/5 |
| R2 (list-building) | 2/5 | 3/5 | 5/5 |
| R3 (Nat recursion) | 3/5 | 4/5 | 5/5 |
| **Total** | **67/77** | **71/77** | **74/77** |
| New tasks solved at rollout 1 | 8/15 | 10/15 | 13/15 |

**Primary metric**: R2 (list-building) — this is the stretch tier where novel composition matters.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `List.rec` builds reversed lists | High | Medium | Add `.reverse` suffix to list-building candidates |
| Lean termination checker rejects patterns | Low | High | Stick to `List.rec`/`Nat.rec` eliminators (always terminate) |
| Candidate explosion (base × step) | Medium | Medium | Cap at 100 candidates per task; type-check filters most |
| GC model proposes garbage step functions | Medium | Low | EF candidates first; GC is fallback |
| Regression on existing 62 tasks | Low | High | Recursion skeleton only activates for new task types; product skeleton unchanged |
| `lake build` fails with new task file | Low | Medium | Test compilation incrementally |

## Estimated Effort

| Phase | Hours | Dependencies |
|-------|-------|-------------|
| Phase 0: Baseline | 1 | None |
| Phase 1: Task definitions | 3 | None |
| Phase 2: Skeleton analyzer | 4 | None |
| Phase 3: Step candidates | 4 | Phase 2 |
| Phase 4: MCTS integration | 5 | Phase 2, 3 |
| Phase 5: Benchmark | 3 | Phase 1, 4 |
| Phase 6: Expression decomposition | 3 | Phase 5 |
| Phase 7: Documentation | 1 | Phase 5 |
| **Total** | **24 hours** | |

**Critical path**: Phase 2 → Phase 3 → Phase 4 → Phase 5
**Parallelizable**: Phase 1 (tasks) and Phase 2 (analyzer) can proceed concurrently.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `lean_mcts/env/FormalNav/FormalNav/TasksRecursivePrograms.lean` | NEW | 15 task definitions |
| `lean_mcts/benchmark/tasks.py` | MODIFY | Register R1/R2/R3 categories |
| `lean_mcts/search/goal_analyzer.py` | MODIFY | Add recursion detection |
| `lean_mcts/search/test_goal_analyzer.py` | MODIFY | Add recursion test cases |
| `lean_mcts/search/recursion_candidates.py` | NEW | Base/step template generation |
| `lean_mcts/search/mcts.py` | MODIFY | Recursion skeleton expansion |
| `lean_mcts/synth/decompose_expressions.py` | NEW | Extract (base, step) from EF |
| `lean_mcts/synth/recursion_templates.json` | GENERATED | Structured recursion templates |
| `docs/implementation/sprint25/sprint25_status.md` | NEW | Progress tracking |
| `docs/implementation/sprint25/lessons_learned.md` | NEW | Findings |

## Non-Goals

- **`match` + self-reference**: Requires termination proof handling. Deferred to Sprint 26.
- **Polymorphic synthesis**: `∀ α, List α → List α`. Current tasks stay monomorphic (`List Int`).
- **Monadic programs**: `do`-notation, `IO`, `StateM`. Sprint 28+.
- **Retraining the LoRA**: This sprint is pure search architecture + vocabulary. No training data changes.
- **Two-argument recursion**: `zip xs ys`. Deferred to Sprint 26.
- **Solving the 3 remaining failures**: `split_even_odd`, `secondMax`, `rle` are architectural trade-offs, not targets.

## Design Decisions

### Why `List.rec`/`Nat.rec` instead of `match` + explicit recursion?

`List.rec` is a **termination-free eliminator** — Lean's kernel accepts it without a decreasing-argument proof. `match` + self-reference requires Lean to verify structural recursion, which constrains what patterns are expressible and can fail on non-trivial recursion schemes.

Using eliminators also decomposes cleanly into (base, step) holes, which maps directly onto the skeleton expansion architecture from Sprint 24.

### Why not extend the LoRA training data?

The Sprint 23 lesson: data scaling without architectural change doesn't help. Sprint 25 follows Sprint 24's approach — pure search/vocabulary expansion, same adapter. If the recursion skeleton works, Sprint 26 can generate recursion-specific training data from the new templates to improve the GC policy's step-function proposals.

### Why 15 tasks, not 50?

These 15 are chosen to test the **recursion skeleton architecture**, not to saturate coverage. Each task exercises a specific pattern (arithmetic step, conditional step, list-building step, Nat recursion). If the architecture works on 15, scaling to 50 is template expansion, not research.

### How does this connect to the programming vision?

Every task in R1/R2/R3 is a **real program** — factorial, filter, stutter, prefix sums. Solving them via `List.rec`/`Nat.rec` produces **executable Lean 4 code** that compiles to native binaries. The benchmark shift from "proofs about programs" to "programs that are proofs" is the first step toward Lean-as-programming-language synthesis.

## Appendix: Example Trace

### Task: `sum_positive`

```
Input:  xs : List Int
Goal:   ⊢ Int
State:  "xs : List Int\n⊢ Int"

Goal Analyzer:
  is_product: false
  is_recursive_input: true
  recursive_input_type: "List"
  recursive_element_type: "Int"
  return_type: "Int"
  recursion_skeleton: "List.rec"

Candidate Generation:
  Base cases: [0, 1, -1]
  Step templates (Int → Int):
    ["fun h _ acc => acc + h",
     "fun h _ acc => acc * h",
     "fun h _ acc => if decide (h > 0) then acc + h else acc",
     "fun h _ acc => if decide (h > acc) then h else acc",
     ...]

  Candidates (base × step):
    "exact List.rec 0 (fun h _ acc => acc + h) xs"                           -- sum
    "exact List.rec 0 (fun h _ acc => if decide (h > 0) then acc + h else acc) xs"  -- sum_positive ✓
    "exact List.rec 1 (fun h _ acc => acc * h) xs"                           -- product
    ...

Type-check via env.step():
  "exact List.rec 0 (fun h _ acc => acc + h) xs" → ProofFinished ✓
  "exact List.rec 0 (fun h _ acc => if decide (h > 0) then acc + h else acc) xs" → ProofFinished ✓
  ...

Oracle verify:
  sum: oracle([1, -2, 3]) = 2, expected 4 → FAIL
  sum_positive: oracle([1, -2, 3]) = 4, expected 4 → PASS ✓

SOLVED at rollout 1, tactic: "exact List.rec 0 (fun h _ acc => if decide (h > 0) then acc + h else acc) xs"
```

### Task: `stutter` (list-building, harder)

```
Input:  xs : List Int
Goal:   ⊢ List Int
State:  "xs : List Int\n⊢ List Int"

Candidate Generation:
  Base cases: [[]]
  Step templates (Int → List Int):
    ["fun h _ acc => h :: acc",
     "fun h _ acc => h :: h :: acc",           -- stutter! ✓ (but reversed)
     "fun h _ acc => if decide (h > 0) then h :: acc else acc",
     ...]

  Candidates:
    "exact List.rec [] (fun h _ acc => h :: h :: acc) xs"           -- reversed stutter
    "exact (List.rec [] (fun h _ acc => h :: h :: acc) xs).reverse" -- correct stutter ✓

Oracle verify:
  reversed: oracle([1, 2]) = [2, 2, 1, 1], expected [1, 1, 2, 2] → FAIL
  with .reverse: oracle([1, 2]) = [1, 1, 2, 2], expected [1, 1, 2, 2] → PASS ✓

SOLVED at rollout 1 (with .reverse suffix search)
```
