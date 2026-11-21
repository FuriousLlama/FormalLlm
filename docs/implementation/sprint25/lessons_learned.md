# Sprint 25 Lessons Learned

## 1. List.rec is a right fold — cons builds correct order

**The sprint plan was wrong.** It assumed `List.rec` builds reversed lists needing `.reverse`. In fact:

```
List.rec base (fun h _ acc => h :: acc) [1,2,3]
= 1 :: (2 :: (3 :: base))   -- right fold, correct order
```

This is because `List.rec base f (h :: t) = f h t (List.rec base f t)` — it processes the head first, then recurses on the tail. With cons (`h :: acc`), the head is prepended to the recursive result, which already has correct order.

**Impact**: All R2 (list-building) tasks solved at rollout 1 without `.reverse`. Expected 3-4/5, got 5/5.

**Rule**: When using `List.rec` with cons, elements appear in original list order. No `.reverse` needed. This is unlike a left fold (`foldl`) where cons would reverse.

## 2. Recursion eliminators decompose identically to product skeletons

Sprint 24 insight: product types → deterministic constructor skeleton → independent sub-goal search.

Sprint 25 insight: recursive inputs → deterministic `List.rec`/`Nat.rec` skeleton → combinatorial (base, step) search.

Both follow the same pattern: **deterministic structure from type analysis → enumerate hole fillers → oracle verification**. The architecture generalizes:

| Pattern | Skeleton | Holes | Search |
|---------|----------|-------|--------|
| Product type | `constructor^n` | n `exact` sub-goals | Greedy combinations |
| List recursion | `List.rec ? (?) xs` | base, step | Cartesian product |
| Nat recursion | `Nat.rec ? (?) n` | base, step | Cartesian product |

## 3. Template count matters more than template sophistication

With only 11-33 templates per type pair, all 15 tasks solved at rollout 1. The right decomposition (base × step) makes the search space tiny:

- `Int → Int`: 3 bases × 11 steps = 33 candidates
- `Int → Nat`: 2 bases × 6 steps = 12 candidates
- `Int → List Int`: 1 base × 11 steps = 11 candidates
- `Nat → Nat`: 2 bases × 8 steps = 16 candidates

Each candidate is type-checked in ~1ms (env.step), and oracle verification takes ~65ms per candidate. Total solve time: ~30ms type-checking + ~1s oracle = ~1s per task.

**Phase 6 (expression decomposition) was unnecessary** — the hand-crafted templates already covered all tasks.

## 4. Recursion skeleton does not interfere with existing task types

The recursion skeleton activates ONLY for non-product goals with `List` or `Nat` input. For product-type tasks, `_expand_with_skeleton` always returns first, so `_expand_with_recursion_skeleton` is never reached. The -2 observed regression (V2 18/20, DMS 5/6) is stochastic variance at the 500-rollout budget edge.

## 5. `fun h _ _ => h` in List.rec returns the FIRST element

Since `List.rec` is a right fold, `fun h _ _ => h` discards the recursive result and returns `h` from the outermost call, which is the first element. Renamed `lastOrZero` to `firstOrZero`.

**Rule**: Getting the last element requires a left fold (`foldl`), not `List.rec`.

## 6. Compositional tasks are now part of baseline

8 compositional tasks (added in earlier sprints) all solve at rollout 1. The effective benchmark is now 105 tasks (62 original + 8 compositional + 15 recursive + 20 stretch).

## 7. The system is a lookup table, not a search engine

The stretch benchmark (20 tasks, S1-S4 difficulty gradient) revealed that **every solve happens at rollout 1** — the system either has the answer in its template/EF vocabulary or it doesn't. MCTS rollouts 2-500 never find anything new.

This means:
- The "search" in MCTS is an illusion for this domain — it's just exhaustive enumeration of a fixed candidate set
- The GC (goal-conditioned) LoRA model doesn't generate novel expressions that work — its proposals either match known patterns or fail oracle
- 500 rollouts on a hopeless task waste ~80 minutes of wall time for zero information gain

**Implication for future sprints**: Adding more templates is O(1) per task; improving actual search (generating novel expressions) is the real challenge.

## 8. Stretch failure taxonomy maps to capability gaps

| Failure type | Examples | What's missing |
|-------------|----------|----------------|
| Novel step function | alternating_sum, sum_cubes | Step not in templates; GC model can't compose novel lambdas |
| Conditional map | double_positives, clamp_values, replace_neg_zero | `xs.map (fun h => if ... then ... else ...)` patterns not in EF |
| Two-pass computation | deviation_from_max, max_min_diff | Need `let mx := ...; xs.map (fun h => h - mx)` — requires `let` bindings |
| Pairwise comparison | is_sorted_asc | Need to compare consecutive elements — requires `zip xs xs.tail` composition |
| Complex state fold | longest_nondec_prefix, compress_consecutive | Need `foldl` with tuple state + `match` — multi-tactic sequences |
| Algorithmic | count_peaks, count_inversions, sliding_max_2 | Need nested loops or multi-step algorithms |

The easiest gains for future sprints: add conditional map patterns to EF and recursion templates. The hardest: two-pass and algorithmic patterns require architectural changes (let-binding support, multi-tactic skeleton planning).

## 9. Oracle verification dominates wall time at 98.5%

Each oracle call takes ~65ms (Lean compilation + eval). A failed task at 500 rollouts makes thousands of oracle calls, taking ~80 minutes of wall time with only ~20s of CPU. No early-abort mechanism exists — the system grinds through all 500 rollouts even when no progress is being made.

**Missing feature**: A `--patience N` flag that aborts after N consecutive rollouts with no oracle-passing candidate would reduce stretch benchmark time from 27 hours to ~3 hours.

## 10. Python stdout buffering hides progress in benchmarks

When benchmark output is piped (even through `tee`), Python buffers stdout because it detects a non-TTY. This made a multi-day benchmark run completely opaque — no per-task results visible until completion.

**Fix applied**: Added `flush=True` to print calls and incremental JSONL progress file (`/tmp/benchmark_progress.jsonl`) written after each task. Future runs will show progress in real time.
