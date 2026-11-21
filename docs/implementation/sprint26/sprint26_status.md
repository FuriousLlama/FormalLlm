# Sprint 26 Status: Vocabulary Expansion

## Current Phase: COMPLETE

## Phases

- [x] Phase 0: Baseline verification — 80/85 core, 7/20 stretch
- [x] Phase 1: Step-template expansion — +20 templates (11 conditional-map, 5 arithmetic, 4 Nat.rec)
- [x] Phase 2: Foldl skeleton module — foldl_candidates.py (13 foldl + 15 map templates)
- [x] Phase 3: Two-pass skeleton module — twopass_candidates.py (17+ candidates)
- [ ] Phase 4: ExpandedFixed expression expansion — DEFERRED (skeletons cover stretch tasks)
- [x] Phase 5: New task definitions — 15 V3 tasks (10 core + 5 stretch), lake build passes
- [x] Phase 6: Benchmark and iterate — 14/20 stretch, 10/10 V3, 3/5 V3 stretch
- [x] Phase 7: Documentation
- [x] Bonus: Daemon reliability fix — fresh socket per task + request ID correlation

## Results

| Tier | S25 Baseline | Sprint 26 | Delta |
|------|-------------|-----------|-------|
| T2 | 10/10 | 10/10 | = |
| Hard | 4/5 | 2/5 | -2 (expanded-fixed only; hybrid-gc: 4/5) |
| V2 | 18/20 | **20/20** | **+2** (daemon fix) |
| Recurrence | 6/6 | 6/6 | = |
| Fundamentals | 9/10 | 9/10 | = |
| Multi-step | 5/5 | 5/5 | = |
| Deep multi-step | 5/6 | 5/6 | = |
| Compositional | 8/8 | 8/8 | = |
| R1 (simple recursion) | 5/5 | 5/5 | = |
| R2 (list-building) | 5/5 | 5/5 | = |
| R3 (Nat recursion) | 5/5 | 5/5 | = |
| **Core total (EF)** | **80/85** | **80/85** | **=** |
| **Core total (hybrid-gc)** | **80/85** | **82/85** | **+2** |
| **Stretch S1** | 3/5 | **5/5** | **+2** |
| **Stretch S2** | 2/5 | **5/5** | **+3** |
| **Stretch S3** | 2/5 | **4/5** | **+2** |
| **Stretch S4** | 0/5 | 0/5 | = |
| **Stretch total** | **7/20** | **14/20** | **+7** |
| **V3 core (new)** | — | **10/10** | — |
| **V3 stretch (new)** | — | **3/5** | — |
| **Grand total (EF)** | **87/105** | **107/120** | **+20** |
| **Grand total (hybrid-gc est.)** | **87/105** | **109/120** | **+22** |

### Hard tier note

Hard tasks `zip_sum`, `dedup_count`, `filter_map_abs` require model-generated candidates not in the ExpandedFixed expression set. With `expanded-fixed` policy: 2/5. With `hybrid-gc` (GoalConditioned model): 4/5. This is a policy gap, not a skeleton regression.

### Stretch task resolution detail

| Task | S25 | S26 | Via |
|------|-----|-----|-----|
| alternating_sum | FAIL | **PASS** | Recursion template: `h - acc` (right-fold alternating) |
| sum_even_only | PASS | PASS | Already solved |
| product_positive | PASS | PASS | Already solved |
| abs_of_negatives | PASS | PASS | Already solved |
| sum_cubes | FAIL | **PASS** | Nat.rec template: `(k+1)*(k+1)*(k+1)` |
| last_or_default | PASS | PASS | Foldl skeleton: `fun _ h => h` |
| double_positives | PASS | PASS | Foldl map: conditional map template |
| clamp_values | PASS | PASS | Foldl map: `max (-5) (min 5 h)` |
| replace_neg_zero | PASS | PASS | Foldl map: conditional map template |
| negate_sum | PASS | PASS | Recursion template: `acc - h` |
| deviation_from_max | FAIL | **PASS** | Two-pass: `let mx := foldl max; map (h - mx)` |
| count_distinct | PASS | PASS | Already solved |
| is_sorted_asc | FAIL | FAIL | Needs tuple accumulator (out of scope) |
| max_min_diff | FAIL | **PASS** | Two-pass match: `h::t => foldl max h - foldl min h` |
| sum_abs_values | PASS | PASS | Foldl: `acc + if h≥0 then h else -h` |
| longest_nondec_prefix | FAIL | FAIL | Complex 3-state fold (out of scope) |
| compress_consecutive | FAIL | FAIL | Tuple accumulator (out of scope) |
| count_peaks | FAIL | FAIL | Sliding window (out of scope) |
| sliding_max_2 | FAIL | FAIL | Sliding window (out of scope) |
| count_inversions | FAIL | FAIL | O(n²) nested loop (out of scope) |

## Success Criteria Assessment

| Metric | Minimum | Target | Stretch | Actual |
|--------|---------|--------|---------|--------|
| Core 85 baseline | 78/85 | 79/85 | 80/85 | **80-82/85** |
| Existing stretch 20 | 13/20 | 16/20 | 18/20 | **14/20** |
| New V3 tasks 10 | 8/10 | 10/10 | 10/10 | **10/10** |
| New V3 stretch 5 | 2/5 | 3/5 | 5/5 | **3/5** |
| Total (120) | 101/120 | 108/120 | 113/120 | **107-109/120** |

**Met**: Core baseline (80-82), V3 core (10/10), V3 stretch (3/5 = target)
**Close**: Total (107-109 vs 108 target)
**Gap**: 2 stretch tasks short of 16 target — is_sorted_asc (tuple acc) and S4

## Daemon Reliability Fix (Sprint 26 bonus)

### Problem
After processing 15-50 tasks, daemon workers returned `KeyError: 'type'` on `env_inspect` — subsequent tasks in the same benchmark run all failed. Root cause: response desynchronization on persistent client socket buffers.

### Fix (3 layers)

1. **Fresh socket per task**: `PantographClient.reset()` calls `reconnect()` — closes old socket + file handle, opens new one. Eliminates stale buffer state. Cost: ~0.1ms per task (Unix socket connect).

2. **State ID reset**: `clear_states` now resets `_next_id` to 0, preventing unbounded growth of the state ID counter across tasks.

3. **Request/response correlation**: Each request includes a unique `"id"` field. Daemon echoes it back. Client validates the response ID matches before processing. If mismatched, raises `ConnectionError("response desync")` and reconnects.

### Result
- Before: daemon crashed after 15-50 tasks (100% reproduction)
- After: 36+ tasks across 3 tiers, zero corruptions
- V2 improved from 18/20 to **20/20** — stale socket state was causing real solve failures

### Files changed
- `lean_mcts/env/pantograph_client.py`: `reconnect()`, request ID in `_request()`, defensive `env_inspect` parsing
- `lean_mcts/env/pantograph_daemon.py`: echo request ID, reset `_next_id`, updated `ALL_IMPORTS`

## New Files

| File | Purpose |
|------|---------|
| `lean_mcts/search/foldl_candidates.py` | Foldl/map candidate generation (28 templates) |
| `lean_mcts/search/twopass_candidates.py` | Two-pass composition candidates (17+ per type) |
| `lean_mcts/env/FormalNav/FormalNav/TasksVocabulary.lean` | 15 V3 task definitions + reference solutions |

## Modified Files

| File | Changes |
|------|---------|
| `lean_mcts/search/recursion_candidates.py` | +20 step templates (conditional-map, arithmetic, cubic) |
| `lean_mcts/search/goal_analyzer.py` | +is_foldl_amenable, +is_map_amenable fields |
| `lean_mcts/search/mcts.py` | +_expand_with_foldl_skeleton, +_expand_with_twopass_skeleton |
| `lean_mcts/benchmark/tasks.py` | V3_TASKS, V3_STRETCH_TASKS, v3/v3_stretch/v3_all tiers |
| `lean_mcts/benchmark/run.py` | v3/v3_stretch/v3_all tier choices |
| `lean_mcts/env/FormalNav/FormalNav.lean` | import TasksVocabulary |
| `lean_mcts/env/pantograph_client.py` | reconnect(), request IDs, defensive parsing |
| `lean_mcts/env/pantograph_daemon.py` | echo IDs, reset _next_id, updated ALL_IMPORTS |

## Skeleton Priority Chain (expand())

```
product → recursion → foldl/map → two-pass → standard MCTS
```

Each skeleton is self-contained: analyze goal → generate candidates → type-check → oracle verify → return solved child or None (fall through to next).
