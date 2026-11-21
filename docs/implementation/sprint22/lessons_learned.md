# Sprint 22: Lessons Learned

## Key Findings

### 1. Lean type inference breaks intermediate evaluation
**Problem**: When evaluating `foldl_sum([9, 16])`, Lean infers `[9, 16]` as `List Nat` (all-positive elements), not `List Int`. This causes a type mismatch with the `(xs : List Int)` parameter.
**Fix**: Always annotate inputs with their type in `#eval` calls: `#eval f ([9, 16] : List Int)`.
**Impact**: Without this, ALL intermediate evaluations for depth-2+ compositions silently fail (return empty). This is the single most critical bug to watch for when evaluating Lean expressions on computed intermediate values.

### 2. Fragment cache must cover depth-2 intermediates for depth-3 search
The original fragment_cache.json has:
- Pass 1: 60 fragments × 51 benchmark inputs = 2,553 entries
- Pass 2: 60 fragments × ~546 depth-1 intermediates = ~27,000 entries

For depth-3, we also need depth-2 intermediates (outputs of applying a fragment to a depth-1 intermediate). Example: `map_square([3, 4]) = [9, 16]` is depth-1, but `foldl_sum([9, 16]) = 25` is depth-2.

**Solution**: Pre-compute c-fragment evaluations on task-specific b-intermediates (one-time cost ~30s per c-fragment). Store in fragment_cache.json.

### 3. Batch size matters for daemon evaluation
- Batch of 100 #eval statements per check_compile call: ~28s per call
- Single #eval: ~0.17s
- Overhead is dominated by compilation, not execution
- **Recommendation**: Keep batch size at 100 for cache building (tolerable), but for search-time evaluation, batch per-fragment rather than per-triple

### 4. Type gap: `List Nat` has no fragment input type
The fragment catalog has 51 `List Int → *` fragments and 9 `Nat → *` fragments, but ZERO `List Nat → *` fragments. This blocks compositions like `max_abs` (`map_natAbs → foldl_max`) where `map_natAbs` outputs `List Nat`.

**Fix needed (Phase 4)**: Add `List Nat → Nat` fragments (e.g., `foldl max 0`, `foldl (+) 0`, `length`) to bridge the type gap.

### 5. Depth-2 composition is the sweet spot
- **Depth-1**: 12/18 tasks solved (direct fragment match)
- **Depth-2**: 14/18 tasks solved (~0ms per task from cache)
- **Depth-3**: 15/18 tasks solved (requires pre-warming, ~30s one-time cost per task)

The depth-2 search is fast enough for real-time use in MCTS. Depth-3 requires per-task pre-computation but finds novel compositions.

### 6. Expression composition via variable substitution works
The `_substitute_var` approach (replacing the first standalone `xs` with `(inner_expr)`) correctly handles all tested fragment compositions:
- `xs.length` → `(xs.filter ...).length`
- `xs.reverse` → `(xs.filter ...).reverse`
- `List.mergeSort xs` → `List.mergeSort (xs.filter ...)`
- `(List.scanl f 0 xs).drop 1` → `(List.scanl f 0 (xs.eraseDups)).drop 1`

### 7. Pre-warming is essential for depth-3 performance
Without pre-warming, the depth-3 search makes O(|triples|) daemon calls = ~7400 calls for `List Int → Int`. With pre-warming:
1. Batch-evaluate b fragments on a-intermediates (~52 values, 0 uncached due to pass-2 cache)
2. Batch-evaluate c fragments on b-intermediates (~378 values, ~266 uncached per c-fragment)
3. After pre-warming: triple iteration is pure cache lookups (0.01s for 7396 triples)

## Architecture Decisions Validated

1. **Bottom-up composition works**: 14/18 T2+compositional tasks solved at depth-2 without any model
2. **Per-task pre-computation is correct**: set_task() + cached results = 0ms per propose_tactics() call
3. **Fragment cache is reusable**: 31,191 entries cover most depth-1 and depth-2 intermediates
4. **ExpandedFixed fallback is still needed**: max_abs requires fragments not in the vocabulary

## Performance Characteristics

| Operation | Time | Notes |
|-----------|------|-------|
| Fragment cache load | ~50ms | 31K entries from JSON |
| Depth-1 search | <1ms | Direct dict lookup |
| Depth-2 search (cached) | <1ms | First-test-case pruning + cache |
| Depth-3 search (cached) | ~10ms | 7396 triples, pure cache lookups |
| Depth-3 pre-warm | ~30s/fragment | One-time per c-fragment |
| Single daemon #eval | ~170ms | Baseline latency |
| Batch 100 #eval | ~28s | ~280ms per eval |

## Benchmark Results

### Cross-policy comparison (depth-2, no GPU)

| Tier | Compositional | ExpandedFixed |
|------|--------------|---------------|
| T2 | **10/10** | 10/10 |
| Compositional (8 novel) | **7/8** | 8/8 |
| V2 | **5/20** | 19/20 |

### Why ExpandedFixed still beats Compositional on compositional tier (8/8 vs 7/8)
ExpandedFixed's 650 pre-enumerated expressions ALREADY contain most depth-2 compositions. The one miss (`sum_positives_squared`) is a depth-3 composition that both ExpandedFixed and depth-2 Compositional miss — but ExpandedFixed has it as a hand-crafted entry.

### Why Compositional fails on V2 (5/20)
Three categories of failure:
1. **Multi-arg tasks** (5 tasks): Two-arg functions like `zipWith`, `++`, symmetric difference — fragments only model single-arg functions
2. **Product-return tasks** (4 tasks): Need `constructor` tactic to split goals — compositional search only produces `exact` expressions
3. **Complex algorithmic** (6 tasks): Running max, pairwise sums, every-other-element — need fragments not in the vocabulary (zipIdx, zip+tail, scanl max)

### Key insight: Compositional search is complementary, not a replacement
- **ExpandedFixed**: broad coverage via hand-curated enumeration (650 entries)
- **Compositional**: discovers novel compositions from ~65 primitives, no curation needed
- **HybridCompositional**: should combine both for best coverage

## What Didn't Work

1. **Evaluating 3700+ inputs in one daemon call**: Caused the daemon worker to hang for 87+ minutes
2. **Pre-computing ALL depth-2 intermediates**: 188K evaluations would take ~5+ hours
3. **Type inference without annotations**: `[9, 16]` silently inferred as `List Nat` instead of `List Int`
4. **Returning tactics from all tasks**: `propose_tactics()` accumulated tactics across tasks, causing wrong-type tactics to be sent to MCTS — must scope to current task
5. **Daemon worker stability under batch load**: Workers crash after processing large batch oracle calls, not always restarted
