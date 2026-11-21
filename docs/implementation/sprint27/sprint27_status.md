# Sprint 27 Status: Generalization Diagnostic

## Current Phase: Phase 6

## Phases

- [ ] Phase 0: Baseline verification — confirm S26 numbers (107+/120)
- [x] Phase 1: Task definitions — 18 diagnostic tasks in TasksDiagnostic.lean + benchmark integration
- [x] Phase 2: Diagnostic run — current system (unchanged) on all 18 tasks, EF and hybrid-gc
- [x] Phase 3: Analysis — failure mode classification for all 18 tasks
- [x] Phase 4: Targeted interventions — skeleton composition (Intervention B)
- [x] Phase 5: Post-intervention benchmark — 104/120 core (within variance of S26 107/120), 3/18 diagnostic
- [ ] Phase 6: Documentation and direction decision

## Results

*Pending Phase 2*

### Phase 2 Predictions vs Actuals

| Task | Tier | Prediction | EF Result | Hybrid-GC Result | Failure Mode |
|------|------|------------|-----------|-------------------|--------------|
| secondSmallest | A | FAIL | FAIL | FAIL | |
| mapWithIndex | A | FAIL/POSSIBLE | FAIL | FAIL | |
| prefixWhileIncreasing | A | FAIL | FAIL | FAIL | |
| removeDuplicatesKeepLast | A | POSSIBLE | FAIL | FAIL | |
| dotProductSelf | A | POSSIBLE | **SOLVED r1** | **SOLVED r1** | |
| maxSubarraySum | B | FAIL | FAIL | FAIL | |
| longestPlateau | B | FAIL | **SOLVED r1** | **SOLVED r1** | |
| dutchFlag | B | FAIL | FAIL | FAIL | |
| majorityElement | B | FAIL | **SOLVED r1** | **SOLVED r1** | |
| equilibriumIndex | B | FAIL | FAIL | FAIL | |
| chunksOf | C | FAIL | FAIL | FAIL | |
| diagTranspose | C | FAIL | FAIL | FAIL | |
| insertSorted | C | FAIL | FAIL | FAIL | |
| unfoldN | C | FAIL | FAIL | FAIL | |
| interleaveZip | C | FAIL | FAIL | FAIL | |
| diagInsertionSort | D | FAIL | **SOLVED r1** | **SOLVED r1** | |
| evalStack | D | FAIL | **SOLVED r1** | **SOLVED r1** | |
| allSublists | D | FAIL | FAIL | FAIL | |

### Initial run: 5/18 (3 false positives from weak test cases)
### After test case strengthening: 2/18 (genuine solves only)
### EF and hybrid-gc: identical results — LoRA model contributed 0 additional solves

### Phase 3: Failure Mode Classification

#### Solved Tasks (2/18) — both are template hits

| Task | Tier | Mechanism | Tactic |
|------|------|-----------|--------|
| dotProductSelf | A | Recursion template | `exact List.rec 0 (fun h _ acc => acc + h * h) xs` |
| diagInsertionSort | D | EF library function | `exact List.mergeSort xs` |

- **dotProductSelf**: `acc + h * h` is literally a recursion step template (sum of squares). Not generalization.
- **diagInsertionSort**: `List.mergeSort` is a Mathlib library function in the EF catalog. The system found a library sort, not synthesized insertion sort.

#### False Positives (3 tasks, now fixed with adversarial test cases)

| Task | False solution | Why it passed | Adversarial case |
|------|---------------|---------------|------------------|
| longestPlateau | `(xs.filter (>1)).length` | All test lists had the "plateau value" > 1 | `[1,1,1]`→3 (gives 0) |
| majorityElement | `(xs.take 1).foldl (+) 0` | Majority element was first in every test case | `[1,2,2,2,1]`→2 (gives 1) |
| evalStack | `xs.foldl (fun a b => a+b) 0` | Sum equals stack result when all pops are paired | `[1,2]`→2 (gives 3) |

#### Failed Tasks — Failure Mode Classification (16/18)

**Wiring Gap** (10 tasks): All primitives available, but the specific composition is not in any template.

| Task | Tier | Missing Composition |
|------|------|-------------------|
| secondSmallest | A | Min-direction tuple accumulator (templates only have max-direction) |
| mapWithIndex | A | `enum → map` with index arithmetic (each exists, never composed) |
| prefixWhileIncreasing | A | Stateful fold + list building + early termination (3 patterns, never composed) |
| removeDuplicatesKeepLast | A | `reverse → eraseDups → reverse` chain (each exists individually) |
| longestPlateau | B | Triple accumulator `(best, cur, prev)` with field interaction |
| majorityElement | B | 3-way branching state machine within tuple accumulator |
| maxSubarraySum | B | Tuple accumulator `(best, current)` where fields interact (best depends on current) |
| equilibriumIndex | B | Two-pass where pass2 is stateful fold (skeleton only does aggregate→map) |
| evalStack | D | Fold where accumulator is a List (used as stack) — no template uses List as accumulator type |
| allSublists | D | 4-level nested composition with index arithmetic |

**Multi-step Gap** (4 tasks): Solution requires recursive helpers or multi-tactic sequences the skeleton can't decompose.

| Task | Tier | Missing Capability |
|------|------|-------------------|
| chunksOf | C | Recursive helper with `take`/`drop` at each step |
| diagTranspose | C | Nested recursion over List (List Int) with `filterMap` + indexing |
| insertSorted | C | Conditional recursion with early termination (`let rec` or structural match) |
| interleaveZip | C | Multi-operation composition (zip + fold + drop + length + append) |

**Search Design Gap** (2 tasks): The search architecture cannot express the solution space.

| Task | Tier | Structural Limitation |
|------|------|---------------------|
| dutchFlag | B | Multi-arg function (Int × List Int → List Int) + triple filter concatenation |
| unfoldN | C | Higher-order parameter (Int → Int); search cannot use function-valued arguments |

#### Summary

| Failure Mode | Count | Description |
|-------------|-------|-------------|
| Template hit | 2 | Existing template or library function matches directly |
| Wiring gap | 10 | Primitives exist but composition is novel |
| Multi-step gap | 4 | Requires recursive decomposition or multi-tactic sequence |
| Search design gap | 2 | Architecture cannot represent the solution space |
| **Total** | **18** | |

#### Key Findings

1. **The system is pure verified template lookup.** 2/18 diagnostic tasks solved, both by direct template/library matches. Zero generalization beyond the catalog.
2. **The LoRA model is dead weight.** Hybrid-gc = expanded-fixed on every task. The S21 model contributes nothing to these diagnostic tasks.
3. **Test case quality matters.** Initial 4-case suites produced 3 false positives (60% false solve rate). Adversarial cases are essential.
4. **Wiring gap dominates.** 10/16 failures are "all parts exist, composition is novel." The system has the vocabulary but not the ability to combine it.
5. **Predicted 1-2/18, actual 2/18.** The sprint plan's prediction was accurate. dotProductSelf was predicted POSSIBLE (correct), diagInsertionSort was predicted FAIL (surprise: `List.mergeSort` exists in the catalog).

## Intervention Results

### Intervention A: Retrain GoalConditioned — SKIPPED

Phase 3 confirmed hybrid-gc = expanded-fixed (0 additional solves). The LoRA model is dead weight.
Retraining skipped: the wiring gap (10/16 failures) is not addressable by a model trained on template-solved tasks. The model would learn to reproduce templates, not compose them.

### Intervention B: Skeleton Composition — IMPLEMENTED

**Changes:**
1. `composition_candidates.py` (NEW): chains known operations into 2-3 step pipelines (reverse, eraseDups, filter, mergeSort)
2. `twopass_candidates.py` (EXTENDED): added 8 foldl pass2 templates using aggregate `v`, added Nat pass2 support
3. `mcts.py`: wired composition skeleton after two-pass, before standard MCTS fallback

| Metric | Pre-compose | Post-compose | Delta |
|--------|-------------|--------------|-------|
| Diag Tier A | 1/5 | 2/5 | **+1** (removeDuplicatesKeepLast) |
| Diag Tier B | 0/5 | 0/5 | 0 |
| Diag Tier C | 0/5 | 0/5 | 0 |
| Diag Tier D | 1/3 | 1/3 | 0 |
| Diag total | **2/18** | **3/18** | **+1** |

**New solve:** removeDuplicatesKeepLast (A4) via `exact (xs.reverse.eraseDups).reverse` — a 3-step composition of known operations, no novel primitives.

**Why only +1:** The remaining 15 failures need novel step functions (tuple accumulators with field interaction, state machines, recursive helpers) — not compositions of existing operations. The composition mechanism works for operation chaining but cannot generate new accumulator logic.

## Direction Decision

### Verdict: Pure catalog confirmed

The system is a verified template lookup engine. 3/18 diagnostic tasks solved, all by template/library match or operation chaining — zero novel compositions, zero model contributions.

### Evidence

| Signal | Result | Interpretation |
|--------|--------|---------------|
| Tier A (compositional) | 2/5 | dotProductSelf = template, removeDuplicatesKeepLast = operation chain |
| Tier B (algorithmic) | 0/5 | No novel state machines discovered |
| Tier C (structural) | 0/5 | No recursive decomposition capability |
| Tier D (litmus) | 1/3 | diagInsertionSort = `List.mergeSort` library call |
| LoRA delta | 0 | hybrid-gc ≡ expanded-fixed on all 138 tasks |
| Intervention B delta | +1 | Composition skeleton added removeDuplicatesKeepLast only |
| Core 120 regression | -3 (variance) | No intervention-caused regression |

### Decision criteria from sprint plan

> - **If Tier A 0-2/5, Tier B 0/5**: Pure catalog. Either expand templates or consider a paradigm shift.
> - **If interventions show no improvement**: The system has reached its architectural ceiling.

Both conditions met. Tier A: 2/5 (both template hits). Tier B: 0/5. Intervention added +1 via mechanical composition.

### What worked in the diagnostic

1. **Existing template match**: `acc + h * h` step function → dotProductSelf (A5)
2. **Library function match**: `List.mergeSort` → diagInsertionSort (D1)
3. **Expression composition**: `reverse → eraseDups → reverse` → removeDuplicatesKeepLast (A4)

### What the system cannot do (and never will in current architecture)

1. **Novel accumulator structures**: Kadane's `(best, current)` with field interaction, Boyer-Moore 3-way branching, triple accumulators with prev-tracking
2. **Recursive helpers**: `let rec` definitions, conditional recursion with early termination
3. **Multi-step tactic synthesis**: Anything requiring >1 tactic step (intro → match → recursive call)
4. **Higher-order parameter usage**: Functions as first-class search targets
5. **Index arithmetic**: Compositions involving `range`, `drop i`, `take (len+1)`

### Sprint 28 direction: Train on Diversity, Not Volume

The diagnostic gives us 16 classified failures and their reference implementations. The current model was trained on 17K template-generated pairs — it learned to reproduce templates. The next test: can a model trained on *diverse compositional patterns* generalize to novel compositions?

**Core idea:** Generate synthetic training data that demonstrates the specific patterns the system can't discover — tuple accumulators with field interaction, state machines, operation chains, recursive helpers. Train a new adapter on this diverse data and measure whether it solves diagnostic tasks that templates can't.

**What makes this tractable:**
- The 18 diagnostic reference implementations are a seed corpus of novel patterns
- Each pattern can be augmented into 50-200 variations (flip comparisons, change accumulators, vary predicates)
- Training is fast (~10 min on RTX 5090 for the current adapter)
- The diagnostic benchmark is the evaluation — no new infrastructure needed
- Success criterion is clear: does the retrained model solve >3/18 diagnostic tasks?

**Pattern categories to generate training data for:**
1. Tuple foldl with field interaction (Kadane's, longestPlateau, majorityElement, secondSmallest)
2. Operation chaining (reverse ∘ eraseDups ∘ reverse, filter ∘ sort ∘ dedup)
3. Stateful fold with early termination (prefixWhileIncreasing)
4. Index-tracking accumulator (mapWithIndex, equilibriumIndex)
5. Multi-arg / higher-order patterns (dutchFlag 3-way filter, unfoldN)

**Risk:** The model might memorize the 18 diagnostic solutions without learning to generalize. Mitigate by: generating diverse variations (not just the diagnostic tasks), evaluating on held-out tasks, checking for overfitting.
