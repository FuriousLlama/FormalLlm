# Sprint 22: Compositional Fragment Synthesis — Status

## Phase 0: Fragment Catalog (DONE)

- [x] Extract Level-0 primitives from expressions.jsonl into `fragments.py`
- [x] Evaluate fragments on all benchmark test inputs (all tiers)
- [x] Store extended outputs in `fragment_cache.json`
- [x] Verify: 60 fragments × 51 task inputs = complete cache
- [x] Pass 2: intermediate values for depth-2 compositions
- [x] Targeted c-fragment pre-computation for depth-3

**Stats**: 65 fragments (60 Level-0 + 5 synthetic List Nat), 3 input types.
**Cache**: 31K+ entries covering depth-1 and depth-2 intermediates.

## Phase 1: Partial Evaluator (DONE)

- [x] Implement `PartialEvaluator` in `partial_eval.py`
- [x] Cache-first lookup, Lean call fallback
- [x] Batched evaluation (batch size 100) to prevent daemon overload
- [x] Type-annotated `#eval` calls to fix Lean type inference issues

## Phase 2: Compositional Search Engine (DONE)

- [x] Implement `CompositionSearch` in `compose.py`
- [x] Depth-1: direct fragment match
- [x] Depth-2: type-compatible pairs with intermediate value chaining
- [x] Depth-3: type-compatible triples with pre-warming + pruning
- [x] Expression construction: inner/outer substitution

## Phase 3: Policy Integration (DONE)

- [x] Add `CompositionalPolicy` to `policy.py`
- [x] Add `HybridCompositionalPolicy` to `policy.py`
- [x] Modify benchmark runner to call `set_task()` before MCTS
- [x] Add `--policy compositional` and `--policy hybrid-comp` to run.py
- [x] Add `--tier compositional` to run.py
- [x] Fragment cache auto-loaded on policy init
- [x] Daemon client passed through for on-the-fly evaluation

## Phase 4: Extend Fragment Vocabulary (DONE)

- [x] Add synthetic `List Nat` fragments (foldl max, foldl sum, foldl min, length, reverse)
- [x] Pre-evaluate on intermediate values (464 List Nat values)
- [x] Verify: max_abs and range_sum now solvable
- [ ] Add tuple constructors/destructors, zip/unzip (not needed for current tasks)

## Phase 5: Novel Benchmark Tasks (DONE)

- [x] Create `TasksCompositional.lean` with 8 tasks
- [x] Add task definitions to `tasks.py` (COMPOSITIONAL_TASKS, 8 tasks)
- [x] Add `import FormalNav.TasksCompositional` to FormalNav.lean
- [x] Verify reference solutions compile (`lake build` succeeded)

## Final Results

**17/18 tasks solved at depth-2 (all <2ms from cache):**

| Task | Candidates | Type | Notes |
|------|-----------|------|-------|
| count_positive | 1 | T2 | filter + length |
| map_square | 12 | T2 | direct + compositions |
| count_even | 1 | T2 | filter + length |
| max_abs | 1 | T2 | map_natAbs + foldl_max (via List Nat bridge) |
| sort_desc | 1 | T2 | mergeSort + reverse |
| nub_sort | 5 | T2 | eraseDups + mergeSort |
| sum_doubles | 1 | T2 | map + foldl |
| accum_sum | 25 | T2 | scanl + drop |
| take_positives | 15 | T2 | takeWhile |
| drop_negatives | 13 | T2 | dropWhile |
| count_unique | 1 | Novel | eraseDups + length |
| reverse_filter | 2 | Novel | filter + reverse |
| length_evens | 1 | Novel | filter + length |
| sum_of_squares_comp | 1 | Baseline | map + foldl |
| sorted_positives | 5 | Novel | filter + mergeSort |
| max_abs_comp | 1 | Novel | map_natAbs + foldl_max |
| range_sum | 2 | Cross-type | List.range + foldl |
| sum_positives_squared | 2 | Depth-3 | filter → map → foldl (needs depth-3) |

**18/18 with depth-3** (sum_positives_squared: 2 candidates after ~30s pre-warm).

## Phase 6: Benchmark + Comparison (DONE)

- [x] Run Compositional on T2: **10/10** (all rollout 1)
- [x] Run Compositional on compositional tier: **7/8** (all rollout 1)
- [x] Run ExpandedFixed on compositional tier: **8/8** (all rollout 1)
- [x] Run Compositional on V2: **5/20** (all rollout 1)

**Cross-policy comparison:**

| Tier | Compositional | ExpandedFixed | Notes |
|------|--------------|---------------|-------|
| T2 | **10/10** | 10/10 | Parity — both solve all in rollout 1 |
| Compositional | **7/8** | 8/8 | Only sum_positives_squared (depth-3) missed |
| V2 | **5/20** | 19/20 (S21) | Expected: multi-arg/product tasks out of scope |

**V2 breakdown (Compositional):**
- v2_multi_type (0/5): needs List (List Int) + two-arg inputs — no fragments
- v2_novel (3/5): sum_sorted_positives, max_even, reverse_evens ✓; count_unique_positive + square_positives_sum need depth-3
- v2_multi_step (1/5): normalize ✓; product-type tasks need `constructor`
- v2_algorithmic (1/5): prefix_sums ✓; rest need complex compositions not in vocabulary

**Compositional tier detail:**
- sum_of_squares_comp: SOLVED (baseline)
- range_sum: SOLVED (cross-type via List.range)
- count_unique: SOLVED (novel: eraseDups + length)
- reverse_filter: SOLVED (novel: filter + reverse)
- sum_positives_squared: FAILED (depth-3 only)
- max_abs_comp: SOLVED (via List Nat bridge)
- sorted_positives: SOLVED (novel: filter + sort)
- length_evens: SOLVED (novel: filter + length)

## Phase 7: Analysis (DONE)

- [x] Document findings in lessons_learned.md
- [x] Benchmark results documented above
