# Lessons Learned — Sprint 7

Running log of non-obvious findings, gotchas, and decisions made during development.

---

## Phase 1–2: Named Global References (2026-02-23)

### DeBruijnRenamer.lookup() as free-variable detector

`lookup()` returns the original name when a variable is free (not on the stack). This makes detecting whether a `Var` node is a free variable trivially: `r.lookup(name) == name`. No separate free-variable flag or bookkeeping needed — the existing renamer API covers it.

### GlobalRef position in checker.py parse_expr

`_is_global_ref` must be checked AFTER consuming the token and BEFORE the per-tag dispatch. The guard `tok not in EXPR_STARTERS and not _is_global_ref(tok)` correctly admits corpus function names. The returned `{'tag': 'GlobalRef', 'name': tok}` node is handled downstream in `infer_type` and `pp_expr`.

### Multi-arg App serialization differs from sprint doc examples

The sprint doc shows nested binary Apps: `(App (App fFilter fIsNegative) Var 0)`. The actual serializer emits multi-arg App: `(App fFilter fIsNegative Var 0)`. Both are semantically identical since the App node stores a list of args. The checker and pretty-printer handle multi-arg App correctly.

### Two pre-existing Hackage parse failures (unrelated to Sprint 7)

`parseExpr` and `valueDiff` are Hackage functions with non-standard token content (type names `Expr`, `ValueDiff`, using `Lit` as constructor name). These fail the checker's `parse_expr` due to invalid constructor names. Pre-existing from Sprint 6 — confirmed 4516/4518 parse before and after the change.

### Corpus stats after Phase 1

- Total sequences: 4518 (unchanged — no new functions)
- Functions with unknown deps (lambda-lifted): 3142
- Functions with no deps: 1376 (up from Sprint 6 — composition functions now have 0 unknown deps)

## Phase 3: Training + Evaluation (2026-02-23)

### Named refs confirmed as partial enabler, not sufficient alone

T2 improved from 0/10 (Sprint 6) to 2/10 pre-RL. The hypothesis was partially confirmed: the representation change does help, but not enough to reach the 3/10 minimum bar post-RL. The 8 remaining T2 failures are composition tasks like `fCountPositive = fLength (fFilter fIsPositive xs)` — the model still can't reliably generalize to novel combinations it hasn't seen in training.

### RL does not help T2 because winners are never collected

During RL winner collection, all remaining T2 tasks have 0/10 pass rate across all 3 rounds. No positive examples → no fine-tuning signal → T2 stays stuck. This is the RL bootstrap problem: RL can only improve tasks where the pre-RL model already has some probability of success.

### Val loss improved vs Sprint 6

Sprint 7 val loss: 0.4592. Sprint 6 was in the 0.45-0.50 range — Sprint 7 is in the same range, suggesting the representation change is equally learnable by the model.

### T3 improved from 2/6 to 3/6

A side-benefit: T3 structural tasks (Maybe, tuples, higher-order) improved. This may be because the cleaner representation (fewer anonymous lambda binders) helps the model generalize to structural patterns.

### Pre-RL benchmark vs RL baseline discrepancy

The standalone `make benchmark` (pre-RL) and the RL "Baseline evaluation" run on the same checkpoint but give different numbers (e.g., `not`: 0/10 vs 9/10). This is pure sampling noise with temperature=0.8, k=10. Both are valid estimates but have high variance for tasks with low true pass probability. Use RL baseline or run multiple seeds for accurate estimates.

### Remaining T2 bottleneck

The 8 still-failing T2 tasks are: count_positive, map_square, sort_desc, sum_doubles, max_abs, nub_sort, accum_sum, count_even. All are `fX (fY xs)` or `fX fPredicate xs` style compositions. The model is generating named refs (confirmed by compile errors in pre-RL benchmark) but assembling them in wrong order/structure. Next steps per sprint doc: more composition training data with named refs, or larger model.

---

## 2026-02-24 — RL/Inference Optimization

### Constrained decode serializes k sequences — this is the dominant bottleneck at high k

`_generate_candidates` in `rejection_sample.py` had two paths:
- **Unconstrained**: `generate_batch` — one `(k, T, V)` forward pass per step. Cost is O(steps), independent of k.
- **Constrained**: `for _ in range(k): generate_constrained(...)` — k separate `(1, T, V)` forward passes per step. Cost is O(k × steps).

At k=10 this was acceptable. At k=100 it meant 10× more GPU launches, all tiny. The fix was `generate_constrained_batch`: run all k sequences in one `(k, T, V)` forward pass per step, apply per-sequence validity masks, then sample. Result: 10× more candidates in 2.7× wall time (vs 10× more time if serial).

**Rule**: when scaling k for constrained generation, batching is mandatory. The per-sequence grammar masks are independent — each sequence needs its own `partial` state but the model forward pass is shared. Build the `(k, V)` mask tensor from cached per-sequence frozensets, apply in one operation, then sample.

### Mask cache (`tuple(partial) → frozenset[int]`) eliminates redundant parse work

Each constrained decode step called `is_type_valid(partial, tok)` for every token in the vocabulary — an O(V × |partial|) Python parse loop. With k=50 sequences sharing the same prompt prefix, the first N tokens of every sequence have identical `partial` states → identical masks. Without caching: k × V × L parse calls per task. With caching: V × L per unique partial (first sequence to reach that prefix pays; all others hit cache).

Cache key is `tuple(partial)` (immutable, hashable). Cache value is `frozenset[int]` of valid token IDs. At k=100 with shared prompt prefix of ~10 tokens, the cache hit rate for early positions approaches 100%.

**Gotcha**: the cache must be evicted between RL rounds via `clear_mask_cache()`. The grammar is model-independent (the mask only depends on `partial`, not on model weights), so masks remain valid within a round. But memory grows with number of unique prefixes visited; clearing between rounds bounds it.

### `max_workers` 4 → 16 was the single biggest speedup

The previous default `max_workers=4` ran `runhaskell` evaluations 4 at a time. With k=10 × 38 tasks = 380 jobs, that's 95 sequential batches. With k=100, it's 3,800 jobs = 950 batches. Each `runhaskell` job is an independent OS subprocess — no GIL, no shared state, purely CPU/IO bound. Raising to `min(os.cpu_count(), 16)` = 16 cut the eval parallelism bottleneck by 4×.

The 34% total speedup at k=10 (11m32s → 7m35s) is largely attributable to this change, not to AMP or the mask cache individually.

**Rule**: `ThreadPoolExecutor` for subprocess-based evaluation has no GIL penalty. The only limit is CPU cores and `runhaskell` startup overhead. Set `max_workers` to CPU count, not a conservative small constant.

### AMP (BF16) helps training but not by the expected amount at this scale

Fine-tuning with `torch.autocast("cuda", dtype=torch.bfloat16)` + `GradScaler` was expected to give ~2× throughput on an RTX 5090. Measured impact: fine-tuning went from ~85s/100 epochs (pre-AMP) to ~99s/100 epochs (post-AMP with `num_workers=2`), then recovered to ~97-108s after reverting to `num_workers=0`.

The RTX 5090 BF16 tensor cores provide throughput gains for large matrix multiplications. At this model size (~500K params, batch 16, sequence 255), the bottleneck is memory bandwidth and Python overhead rather than FLOPS — the matrices are too small for tensor core utilization to dominate. AMP's overhead (GradScaler state, dtype casts) partially offsets the gain.

**Rule**: AMP is most beneficial for large models (≥100M params) and large batches where FLOPs dominate. For small models with small batches, the breakeven point may not be reached. Still worthwhile to keep — overhead is low and it helps more as model or batch size grows.

### `num_workers > 0` in DataLoader is net-negative for tiny datasets

Setting `num_workers=2` in the fine-tune DataLoader added ~14s per round (42s total across 3 rounds). The fine-tune dataset is 37-75 winner sequences — fitting entirely in RAM with near-instant loading. Worker process spinup (~1-2s each) and IPC overhead exceeds any possible throughput benefit.

**Rule**: `num_workers > 0` pays off only when dataset loading is the actual bottleneck (large images, disk-bound reads). For in-memory datasets of <1000 sequences, `num_workers=0` is always faster.

### `torch.cat` in a generation loop causes O(steps) tensor reallocations

`generate_batch` originally did `ids = torch.cat([ids, next_ids.unsqueeze(-1)], dim=-1)` at each step, allocating a new `(k, T+1)` tensor every iteration. For `max_new_tokens=200` and `k=100`, that's 200 allocations of tensors growing from `(100, T_prompt)` to `(100, T_prompt+200)`. Pre-allocating a `(k, T_prompt + max_new_tokens)` buffer and writing `buf[:, cur_len] = next_ids` avoids all reallocation.

**Rule**: never `torch.cat` inside a generation loop. Pre-allocate the full sequence buffer and write in-place.

### `python -u` / `PYTHONUNBUFFERED=1` is required when piping to `tee`

At k=100, `make rl` uses `| tee log` internally. When piping Python stdout through `tee`, output is fully buffered — nothing appears until the process exits or the buffer fills. `PYTHONUNBUFFERED=1` (or `python -u`) switches to unbuffered mode and makes progress visible in real time. The updated Makefile already uses this; running the script directly without `-u` produces a silent pipe.

The double-`tee` pattern (`make ... | tee outerlog` when Makefile already has `| tee innerlog`) creates two competing tee processes on the same stdout stream. The outer tee captures only what the inner tee doesn't buffer. Avoid this — run the script directly or use `make` alone, not both.

### RL ceiling at k=100 is a model gap, not a search gap

At k=10, 13 tasks had 0 winners → RL ceiling. At k=100, 7 tasks still have 0/100 → the remaining 6 ceiling tasks at k=10 were resolved by sampling more candidates (they had low but non-zero probability). The 7 permanently stuck tasks require operations the model has never seen: `scanl`-style accumulation (`accum_sum`), de-duplication (`nub_sort`), descending sort (`sort_desc`), even-number filtering (`count_even`), double-mapping (`sum_doubles`, `map_square`), `Maybe` fmapping (`maybe_with_default`), and function self-application (`apply_twice`).

These patterns are absent from or vanishingly rare in the training corpus. No amount of k or RL rounds can help — the model cannot generate what it has never seen. Fix direction: supervised corpus augmentation with these specific patterns, not more sampling.

**Diagnostic**: plot pass rate vs k on a log scale. If pass rate is non-zero at k=10 and grows toward 1 at k=100, it's a search problem (more k helps). If pass rate is exactly 0 at k=100, it's a model problem (training data gap).

### Temperature sensitivity of constrained decode: t=0.6 > t=0.5 at high k

At k=200, `count_true` and `any_positive` show 0/200 at t=0.5 (198-200 parse failures) but 152/200 and 171/200 at t=0.6. Both tasks require generating specific token sequences that are valid grammar but rarely produced. At t=0.5, the constrained decode is so peaked on its top candidates that it never explores the one path where those tokens appear — the grammar mask eliminates all non-top candidates and the model locks into a cycle of invalid sequences. At t=0.6 the distribution is flatter; the valid path gets sampled 152-171 times out of 200.

**Rule**: for constrained autoregressive generation, very low temperature (<0.5) can make the model over-commit to its top token at each step and never explore valid grammar continuations. The constraint mask needs diversity in the input logits to do useful work. t=0.6 is a better default than t=0.5 for constrained generation with this model size.

### RL can amplify a 1-in-200 success to near-certainty

`count_positive` at t=0.6 had exactly 1/200 passing candidates at baseline (pass rate 0.005). That single winner was used to fine-tune the model for 100 epochs. After Round 1, the Round 2 winner collection showed 199/200 passing — a 200× improvement in sample efficiency from a single training example.

Mechanism: the one correct solution teaches the model the correct compositional pattern (`length (filter ...)` in Haskell IR). With 100 fine-tuning epochs on 1 example, the model strongly reinforces that pattern. The mask cache and batched generation ensure that the correct token path is now in high-probability territory for all subsequent samples.

**Rule**: rejection sampling RL has a minimum signal requirement of ≥1 winner per task. It can amplify a 0.5% signal to ~100% in 2 rounds. But tasks with exactly 0/k pass rate at any temperature cannot be unlocked by RL — there is nothing to reinforce. The diagnostic test: if pass@200=0 at t=0.6, it is a model knowledge gap (supervised corpus fix needed), not a sampling problem.

### Temperature must be consistent between benchmark and RL for valid comparison

At t=0.5: count_positive = 0/200 (RL has no signal)
At t=0.6: count_positive = 1/200 (RL can amplify to 200/200)

These are the same checkpoint, same constrained decode, different temperature only. The temperature controls which grammar paths the decoder explores. A task that is genuinely unsolvable at t=0.5 can be latently solvable at t=0.6 — the model already knows the solution, it just needs a nudge to find the valid path.

**Rule**: run benchmark at multiple temperatures before concluding a task is permanently stuck. A task that shows 0/100 at t=0.5 might show 1/200 at t=0.6, which is enough for RL to work with.
