# Sprint 27: Generalization Diagnostic — Beyond Template Matching

## Goal

Design and run a diagnostic benchmark that measures whether the system can synthesize programs requiring novel composition of known primitives — not just match against the template catalog. Use the results to determine whether the next investment should be vocabulary expansion (more templates), model retraining (learn to generalize), or a search paradigm shift (move beyond MCTS).

## Motivation

### The Question

Across sprints 24-26 the system went from 59/62 to 107/120 with zero retraining — pure search-space expansion via hand-crafted skeleton modules. Every task either solves at rollout 1 (template match) or not at all (even at 500 rollouts). The GoalConditioned LoRA model, last trained in S21, contributes to solving maybe 3-5 tasks.

This raises a fundamental question: **is the system doing program synthesis, or is it doing verified template lookup?**

The current 120-task benchmark cannot answer this because tasks and templates are co-designed. Stretch tasks were added in the same sprint as the skeletons that solve them. V3 tasks were added alongside the foldl/two-pass modules. There's no separation between the "training set" (template vocabulary) and "test set" (benchmark tasks).

### Why It Matters

If the system is pure catalog lookup, then:
- Each new task category requires hand-crafting a new skeleton module
- Returns diminish as the remaining unsolved patterns get more complex
- The LoRA model is dead weight — replace it with more templates
- The project's contribution is "verified enumeration beats learning" (publishable but bounded)

If the system has latent generalization capacity (especially via the stale GoalConditioned model), then:
- Retraining on S24-S26 skeleton solutions could unlock novel compositions
- MCTS search beyond rollout 1 could find programs templates miss
- The project can scale to harder domains without hand-crafting every pattern

### What This Sprint Does

1. Define 18 diagnostic tasks organized into 4 tiers (A-D), each probing a different capability boundary
2. Every task uses ONLY primitives already in the system's vocabulary — the barrier is composition, not vocabulary
3. Run the current system unchanged to establish ground truth
4. Attempt targeted interventions (retrain, skeleton composition) based on results
5. The outcome determines the direction of Sprint 28+

## Context

### Current State (End of Sprint 26)

| Tier | Result | Mechanism |
|------|--------|-----------|
| Core 85 | 80-82/85 | EF / hybrid-gc |
| Stretch 20 | 14/20 | Skeletons (S26) |
| V3 15 | 13/15 | Skeletons (S26) |
| **Grand total** | **107-109/120** | **89-91%** |

- No retraining since S21 (5 sprints ago)
- GoalConditioned adapter: S21 vintage, val_loss 0.2756, 8587 training pairs
- All S24-S26 gains: deterministic skeleton search
- Binary solve pattern: rollout 1 or never for 97%+ of tasks

### What the System Can Express (vocabulary)

- ~15 core List methods (map, filter, fold, scan, take, drop, zip, partition, sort, etc.)
- ~12 predicates, ~10 transforms
- `List.rec`, `Nat.rec`, pattern matching, if/then/else
- Left-fold, right-fold, two-pass (aggregate then map)
- Product decomposition to depth-3
- 650 pre-enumerated ExpandedFixed expressions
- ~130 skeleton templates across 4 modules (product, recursion, foldl/map, two-pass)

### What It Cannot Wire (the gap)

Even though every primitive is available, the system cannot:
- Combine two known patterns that haven't been combined in a template
- Adapt a template to a slightly different type signature
- Chain skeletons (e.g., two-pass where each pass is a fold skeleton)
- Discover state machines beyond the ~15 tuple-accumulator templates

## Diagnostic Benchmark: 18 Tasks in 4 Tiers

### Design Principles

1. **Every primitive needed is already in the vocabulary** — no new library functions required
2. **Solutions are short** (1-5 lines of Lean) — search depth is not the bottleneck
3. **Tasks are ordered by composition novelty**, not by solution length
4. **Reference implementations are provided** for oracle verification
5. **No task is designed alongside a skeleton** — this is the key difference from S24-S26

### Tier A: Compositional Generalization (5 tasks)

Tests whether the system can combine known patterns in configurations not present in any template. Every building block exists; the wiring is new.

#### A1: `secondSmallest : List Int → Int`

Return the second-smallest element. The system has `secondMax` (solved via tuple-accumulator template tracking `(best, second)` with max comparisons). This is the same pattern with min-direction comparisons. A human would trivially adapt; the question is whether the system can.

```lean
def secondSmallest_ref (xs : List Int) : Int :=
  match xs with
  | [] => 0
  | [x] => x
  | h :: t =>
    let (a, b) := t.foldl (fun (lo, lo2) h =>
      if h < lo then (h, lo)
      else if h < lo2 then (lo, h)
      else (lo, lo2)) (h, h)
    b
```

**Why it's diagnostic**: The `secondMax` template uses `>` comparisons. If the system solves this, it generalizes across comparison direction. If not, each comparison direction is a separate template entry.

#### A2: `mapWithIndex : List Int → List Int`

Multiply each element by its 1-based index: `[a,b,c] → [a*1, b*2, c*3]`.

```lean
def mapWithIndex_ref (xs : List Int) : List Int :=
  (xs.zipIdx.map (fun (v, i) => v * (↑i + 1)))
```

**Why it's diagnostic**: `zipIdx` and `map` over pairs are both known. The system has `xs.zipIdx.filter/map` patterns in EF for index-based filtering. This tests whether it can compose them with a novel lambda `(v, i) => v * (↑i + 1)`.

#### A3: `prefixWhileIncreasing : List Int → List Int`

Take elements while each is strictly greater than the previous.

```lean
def prefixWhileIncreasing_ref (xs : List Int) : List Int :=
  match xs with
  | [] => []
  | h :: t =>
    h :: (t.foldl (fun (acc, prev, done) curr =>
      if done then (acc, prev, true)
      else if curr > prev then (acc ++ [curr], curr, false)
      else (acc, prev, true)) ([], h, false)).1
```

**Why it's diagnostic**: Combines stateful fold (tracking `prev` and `done` flag) with list-building accumulator. Both patterns exist independently — the question is whether they compose.

#### A4: `removeDuplicatesKeepLast : List Int → List Int`

Remove duplicates keeping the LAST occurrence. `[1,2,1,3] → [2,1,3]`.

```lean
def removeDuplicatesKeepLast_ref (xs : List Int) : List Int :=
  (xs.reverse.eraseDups).reverse
```

**Why it's diagnostic**: Three known operations (`reverse`, `eraseDups`, `reverse`) chained. The template library has `eraseDups` and `reverse` individually, and `nubSort` (dedup + sort). This 3-step chain is NOT in any template. Solution is trivial (one line) but requires the right decomposition insight.

#### A5: `dotProductSelf : List Int → Int`

Dot product of a list with itself: sum of squares. `[2,3] → 4+9 = 13`.

```lean
def dotProductSelf_ref (xs : List Int) : Int :=
  xs.foldl (fun acc h => acc + h * h) 0
```

**Why it's diagnostic**: `foldl (fun acc h => acc + h * h)` is a step function that doesn't exist as a template, but `foldl + sum` and `map square` are both known. The question is whether the system can fuse `map square + sum` into a single fold, or discover the step function independently.

### Tier B: Algorithmic Reasoning (5 tasks)

Tests whether the system can discover non-trivial state machines. Solutions are still short but require choosing the right accumulator structure.

#### B1: `maxSubarraySum : List Int → Int`

Maximum sum of any contiguous subarray (Kadane's algorithm).

```lean
def maxSubarraySum_ref (xs : List Int) : Int :=
  (xs.foldl (fun (best, current) h =>
    let c := max h (current + h)
    (max best c, c)) (0, 0)).1
```

**Why it's diagnostic**: Requires a tuple accumulator `(best, current)` where the two fields *interact* (best depends on current). Existing tuple templates have independent fields (e.g., `(sum, count)` where sum and count don't reference each other).

#### B2: `longestPlateau : List Int → Nat`

Length of the longest run of consecutive identical values.

```lean
def longestPlateau_ref (xs : List Int) : Nat :=
  match xs with
  | [] => 0
  | h :: t =>
    let (best, cur, _) := t.foldl (fun (best, cur, prev) h =>
      if h == prev then (max best (cur + 1), cur + 1, h)
      else (best, 1, h)) (1, 1, h)
    max best cur
```

**Why it's diagnostic**: Triple accumulator `(best, curLen, curVal)` with field interaction (best depends on curLen). The system has triple accumulators for `rle` but that template builds a list, not computes a scalar. Tests whether accumulator structure transfers across output types.

#### B3: `dutchFlag : Int → List Int → List Int`

3-way partition: elements less than pivot, equal to pivot, greater than pivot, concatenated.

```lean
def dutchFlag_ref (pivot : Int) (xs : List Int) : List Int :=
  xs.filter (· < pivot) ++ xs.filter (· == pivot) ++ xs.filter (· > pivot)
```

**Why it's diagnostic**: Three `filter` calls with complementary predicates, then `++`. Each filter and `++` is known. The system has `partition` (2-way) but not 3-way. Tests whether it can extend a known pattern by one level.

#### B4: `majorityElement : List Int → Int`

Boyer-Moore majority vote algorithm.

```lean
def majorityElement_ref (xs : List Int) : Int :=
  (xs.foldl (fun (cand, count) h =>
    if count == 0 then (h, 1)
    else if cand == h then (cand, count + 1)
    else (cand, count - 1)) (0, 0)).1
```

**Why it's diagnostic**: A specific state machine the system has never seen. The step function has 3-way branching within a tuple accumulator. This is algorithmically non-trivial — discovering Boyer-Moore from a specification would be genuine synthesis.

#### B5: `equilibriumIndex : List Int → Int`

First index where left-sum equals right-sum. Return -1 if none exists.

```lean
def equilibriumIndex_ref (xs : List Int) : Int :=
  let total := xs.foldl (· + ·) 0
  (xs.foldl (fun (idx, leftSum, result) h =>
    if result >= 0 then (idx + 1, leftSum, result)
    else if leftSum == total - leftSum - h then (idx + 1, leftSum + h, idx)
    else (idx + 1, leftSum + h, result)) (0, 0, -1)).2.2
```

**Why it's diagnostic**: Two-pass where pass2 is a stateful fold with triple accumulator `(index, leftSum, result)`. The two-pass skeleton exists for `aggregate → map` but not for `aggregate → stateful search`. Tests skeleton composability.

### Tier C: Structural Abstraction (5 tasks)

Tests whether the system can handle structural recursion beyond its template library. These require multi-step tactic sequences or helper decompositions.

#### C1: `chunksOf : Nat → List Int → List (List Int)`

Split into groups of n: `chunksOf 2 [1,2,3,4,5] = [[1,2],[3,4],[5]]`.

```lean
def chunksOf_ref (n : Nat) (xs : List Int) : List (List Int) :=
  if n == 0 then [xs]
  else
    let rec go : List Int → List (List Int)
      | [] => []
      | ys => (ys.take n) :: go (ys.drop n)
    go xs
```

**Why it's diagnostic**: Requires a recursive helper with `take`/`drop` at each step. Both `take` and `drop` are known, but the recursive scaffolding with a local `let rec` is not in any template. Tests multi-step tactic synthesis capability.

#### C2: `transpose : List (List Int) → List (List Int)`

Matrix transpose.

```lean
def transpose_ref : List (List Int) → List (List Int)
  | [] => []
  | [] :: _ => []
  | xss => xss.filterMap List.head? :: transpose_ref (xss.map List.tail!)
```

**Why it's diagnostic**: Nested recursion over `List (List Int)` with `map` at each recursive step. The system handles `List (List Int)` inputs (flatten, longest_sublist) but only via single-pass operations, not recursion over the outer list.

#### C3: `insertSorted : Int → List Int → List Int`

Insert element at sorted position in a sorted list.

```lean
def insertSorted_ref (x : Int) (xs : List Int) : List Int :=
  match xs with
  | [] => [x]
  | h :: t => if x ≤ h then x :: h :: t else h :: insertSorted_ref x t
```

**Why it's diagnostic**: Classic CS1 exercise. Requires conditional recursion: either cons and stop, or skip and recurse. The system has `List.rec` with unconditional step functions but not conditional early termination.

#### C4: `unfoldN : Nat → (Int → Int) → Int → List Int`

Generate list by repeated function application: `unfoldN 4 (·+1) 0 = [0, 1, 2, 3]`.

```lean
def unfoldN_ref (n : Nat) (f : Int → Int) (seed : Int) : List Int :=
  Nat.rec [] (fun _ acc => match acc.getLast? with
    | none => [seed]
    | some v => acc ++ [f v]) n
```

**Why it's diagnostic**: Higher-order input `(Int → Int)`. The system handles higher-order in fixed forms (`map f`, `filter p`) but not as a free parameter that drives generation. Tests whether the search can handle function-valued arguments.

#### C5: `interleaveZip : List Int → List Int → List Int`

Interleave two lists: `[1,2,3] [4,5] → [1,4,2,5,3]`.

```lean
def interleaveZip_ref : List Int → List Int → List Int
  | [], ys => ys
  | xs, [] => xs
  | x :: xs, y :: ys => x :: y :: interleaveZip_ref xs ys
```

**Why it's diagnostic**: The system has `interleave` as a Fundamentals task (F10, solved). But F10's solution is a monolithic expression in the EF catalog. This tests whether the system can handle the same pattern as a sorry'd definition requiring multi-step tactic fill (intro → match → recursive call), NOT as a single `exact` expression.

### Tier D: The Litmus Test (3 tasks)

If the system solves ANY of these, it has genuine multi-step synthesis capability.

#### D1: `insertionSort : List Int → List Int`

Sort by repeated insertion into a sorted accumulator.

```lean
def insertionSort_ref (xs : List Int) : List Int :=
  xs.foldl (fun sorted h =>
    let rec insert : List Int → List Int
      | [] => [h]
      | x :: rest => if h ≤ x then h :: x :: rest else x :: insert rest
    insert sorted) []
```

**Why it's diagnostic**: Requires synthesizing a local recursive helper `insert` inside a fold step. This is two levels of abstraction: an outer fold with an inner recursion. No existing skeleton or template covers this pattern.

#### D2: `evalStack : List Int → Int`

Interpret a simple stack language where positive numbers are pushed and 0 means "pop two, add, push result."

```lean
def evalStack_ref (xs : List Int) : Int :=
  let stack := xs.foldl (fun stk h =>
    if h == 0 then
      match stk with
      | a :: b :: rest => (a + b) :: rest
      | _ => stk
    else h :: stk) []
  match stack with
  | v :: _ => v
  | [] => 0
```

**Why it's diagnostic**: A fold where the accumulator is a *list used as a stack*, with conditional push/pop operations. The accumulator type is `List Int` (same as input type), which no template handles. Requires discovering that a list can serve as intermediate computation state.

#### D3: `allSublists : List Int → List (List Int)`

Generate all contiguous sublists.

```lean
def allSublists_ref (xs : List Int) : List (List Int) :=
  let n := xs.length
  List.join (List.range n |>.map (fun i =>
    List.range (n - i) |>.map (fun len =>
      (xs.drop i).take (len + 1))))
```

**Why it's diagnostic**: Nested `range → map → range → map → drop → take → join`. This is a 4-level composition that requires understanding index arithmetic over the list. No skeleton or template comes close.

## Implementation Plan

### Phase 0: Baseline Verification (0.5 day)

Confirm S26 numbers on the current system. Run full 120-task benchmark with `expanded-fixed` and `hybrid-gc` policies.

**Exit criterion**: Core 80+/85, total 107+/120 matches S26 status.

### Phase 1: Task Definitions (1 day)

Create Lean task file and benchmark integration:

1. **`TasksDiagnostic.lean`**: 18 task definitions with `_ref` implementations and `#eval` test cases
   - Group into modules: `DiagA` (5), `DiagB` (5), `DiagC` (5), `DiagD` (3)
   - Each task gets 4-6 test cases covering edge cases (empty list, single element, general case)
2. **`tasks.py`**: Add `DIAG_A_TASKS`, `DIAG_B_TASKS`, `DIAG_C_TASKS`, `DIAG_D_TASKS` with tier choices
3. **`run.py`**: Add `diag_a`, `diag_b`, `diag_c`, `diag_d`, `diag_all` tier choices
4. **Verify**: `lake build` passes, oracle correctly evaluates all 18 reference implementations

**Exit criterion**: All 18 tasks defined, compilable, oracle-verifiable.

### Phase 2: Diagnostic Run (0.5 day)

Run current system (unchanged, no new templates, no retraining) on all 18 diagnostic tasks.

- Policy: `expanded-fixed` (pure template, no model)
- Policy: `hybrid-gc` (model + template, tests LoRA contribution)
- Rollouts: 200 (standard budget)
- Record per-task: solved/failed, rollout number if solved, candidates generated, search tree depth

**Exit criterion**: Per-task results for both policies on all 18 tasks.

### Phase 3: Analysis (0.5 day)

For each failed task, classify the failure mode:
1. **Vocabulary gap**: the needed expression literally cannot be constructed from available primitives
2. **Wiring gap**: all primitives available but never combined this way in templates
3. **Search depth**: the solution exists in the search space but requires >200 rollouts
4. **Multi-step gap**: the solution requires >1 tactic step and the skeleton doesn't decompose it

For each solved task, determine whether the solve is:
1. **Template hit**: an existing EF expression or skeleton template matches directly
2. **LoRA generation**: the GoalConditioned model generated a novel expression
3. **MCTS discovery**: search beyond rollout 1 found it through exploration

**Exit criterion**: Written analysis with failure-mode classification for all 18 tasks.

### Phase 4: Targeted Interventions (1-2 days)

Based on Phase 3 findings, attempt up to 2 interventions:

#### Intervention A: Retrain GoalConditioned (if Tier A shows LoRA gap)

If hybrid-gc solves 0-1 more tasks than expanded-fixed on Tier A, the model is dead weight. Retrain:

1. Collect all verified solutions from S24-S26 benchmark runs (~200-400 new tactic pairs)
2. Add to S21 training set (8587 + new ≈ 8800-9000 pairs)
3. Train 3 seeds, evaluate on full 120 + 18 diagnostic
4. Compare: does fresh training improve Tier A beyond what expanded-fixed achieves?

**Risk mitigation**: Back up S21 adapter first. If regression on core tasks, discard.

#### Intervention B: Skeleton Composition (if Tier B/C shows wiring gap)

If Phase 3 shows the failure mode is "all primitives available, wiring missing":

1. Allow two-pass skeleton to use foldl skeleton as pass2 (not just simple map)
2. Allow recursion skeleton step functions to reference fold results
3. This is NOT adding new templates — it's letting existing skeletons call each other

**Risk**: combinatorial explosion of composed candidates. Mitigate with type-directed pruning.

### Phase 5: Post-Intervention Benchmark (0.5 day)

Re-run all 18 diagnostic tasks + full 120 tasks after interventions.

**Exit criterion**: Per-task delta from Phase 2, regression check on core 120.

### Phase 6: Documentation and Direction Decision (0.5 day)

Write up findings and make an explicit decision for Sprint 28:

- **If Tier A 3+/5**: Generalization exists. Invest in model retraining + skeleton composition.
- **If Tier A 0-2/5, Tier B 0/5**: Pure catalog. Either expand templates or consider a paradigm shift (constraint-based synthesis, larger model, or write-up-and-publish).
- **If interventions show improvement**: Plan Sprint 28 around the winning intervention.
- **If interventions show no improvement**: The system has reached its architectural ceiling. Sprint 28 = write-up.

## Success Criteria

| Metric | Expected | Good | Surprising |
|--------|----------|------|------------|
| Tier A (compositional) | 0-1/5 | 2-3/5 | 4-5/5 |
| Tier B (algorithmic) | 0/5 | 1/5 | 2+/5 |
| Tier C (structural) | 0/5 | 1/5 | 2+/5 |
| Tier D (litmus) | 0/3 | 0/3 | 1+/3 |
| Core 120 regression | 107+/120 | 107+/120 | 110+/120 |
| Diagnostic total | 0-1/18 | 3-5/18 | 6+/18 |

The primary outcome is not a score — it's the **failure mode classification** from Phase 3, which determines project direction.

## Predicted Outcomes

Being explicit about predictions so we can learn from surprises:

- **A1 (secondSmallest)**: FAIL. The min-direction tuple accumulator is a different template. The system won't generalize from max to min.
- **A2 (mapWithIndex)**: FAIL with EF, POSSIBLE with hybrid-gc. The zipIdx+map composition might exist in EF; if not, LoRA might generate it.
- **A3 (prefixWhileIncreasing)**: FAIL. Combines stateful fold + list building + early termination — three patterns that haven't been composed.
- **A4 (removeDuplicatesKeepLast)**: POSSIBLE. `reverse → eraseDups → reverse` might exist as an EF expression or be generated by LoRA. This is the most likely Tier A solve.
- **A5 (dotProductSelf)**: POSSIBLE. `foldl (fun acc h => acc + h * h) 0` might match a foldl template. If not, the foldl skeleton might generate it as a candidate.
- **B1-B5**: All FAIL. These require novel state machines with field interaction.
- **C1-C5**: All FAIL. These require multi-step tactic sequences or recursive helpers.
- **D1-D3**: All FAIL. These require two levels of abstraction.

**Predicted total: 1-2/18** (A4 and/or A5).

## New Files

| File | Purpose |
|------|---------|
| `lean_mcts/env/FormalNav/FormalNav/TasksDiagnostic.lean` | 18 diagnostic task definitions + references |
| `docs/implementation/sprint27/sprint27.md` | This plan |
| `docs/implementation/sprint27/sprint27_status.md` | Status tracker |

## Modified Files

| File | Changes |
|------|---------|
| `lean_mcts/benchmark/tasks.py` | DIAG_A/B/C/D_TASKS, diag tier choices |
| `lean_mcts/benchmark/run.py` | diag_a/b/c/d/all tier choices |
| `lean_mcts/env/FormalNav/FormalNav.lean` | import TasksDiagnostic |

## Non-Goals

- **No new skeleton modules**. This sprint diagnoses the system, it doesn't extend it (except targeted interventions in Phase 4).
- **No new EF expressions for diagnostic tasks**. The whole point is to test what the system can do without task-specific templates.
- **No infrastructure changes**. Daemon, oracle, MCTS core are stable from S26.
- **No benchmark expansion beyond the 18 diagnostic tasks**. Keep focus tight.

## Risk

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Diagnostic tasks too easy (template hits) | Diagnostic is uninformative | Reviewed task designs against full template catalog — no direct hits |
| Diagnostic tasks too hard (all 0/18) | No gradient to learn from | Tier A designed to be at the boundary; A4/A5 are near-template |
| Retraining regresses core 120 | Lose S26 gains | Back up S21 adapter, test on full 120 before adopting |
| Lean compilation errors in new tasks | Blocks Phase 1 | Reference implementations tested against Lean semantics |
| Skeleton composition causes combinatorial explosion | Phase 4B is unusable | Type-directed pruning, candidate count caps |
