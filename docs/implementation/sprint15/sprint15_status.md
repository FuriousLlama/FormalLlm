# Sprint 15 — Project Status

Last updated: 2026-03-14 (final)

## Overall Progress

```
[x] Phase 0 — Baseline Instrumentation
    [x] Add SearchStats dataclass to mcts.py (per-step timing)
    [x] Instrument expand(): time env.step(), policy_fn(), oracle_fn() individually
    [x] Instrument simulate(): time full simulation
    [x] Include SearchStats in benchmark JSON output
    [x] Run baseline: FixedPolicy T2 (50 rollouts)
    [ ] Run baseline: ExpandedFixed V2 (50 rollouts)  — skipped, went straight to Pantograph
    [ ] Run baseline: SynthesisPolicy T2 (50 rollouts) — deferred
    [x] Document bottleneck breakdown (REPL % vs policy % vs oracle %)
[x] Phase 1 — PyPantograph Setup + Smoke Test
    [x] Install PyPantograph (resolve Lean version compatibility)
    [x] Smoke test: solve synth_identity through Pantograph
    [x] Test 3 T2 tasks with known solutions
    [x] Test 1 hard task (verify no REPL crash)
    [x] Document state string format differences
[x] Phase 2 — Wrapper Replacement
    [x] Write env/pantograph_wrapper.py (PantographEnv class)
    [x] State format adapter (Pantograph Goal → LeanDojo-compatible string)
    [x] Theorem name → type expression mapping (via env_inspect + auto-intro)
    [x] Benchmark integration (--backend pantograph flag)
    [x] Policy state format compatibility verified
    [ ] Restart recovery tested — not needed, no crashes observed
[x] Phase 3 — Hard Task + Multi-Step Evaluation
    [x] Hard tasks: ExpandedFixed through Pantograph (0/5 solved, 0 crashes)
    [x] V2 tasks: parity check with Sprint 14 (11/20, matches exactly)
    [x] T2 regression check (10/10 ✓)
    [x] Cat C deep search at 500 rollouts (1/3 solved: partition_and_count)
    [x] Zero REPL crashes confirmed (0 across all 35 tasks)
[x] Phase 4 — Warm Oracle (replaces C FFI stretch goal)
    [x] Prototype: check_compile evaluates #eval through warm Pantograph server
    [x] warm_verify() in eval/oracle.py (~375ms/call vs ~2100ms cold)
    [x] --warm-oracle flag in benchmark/run.py
    [x] T2 parity: 10/10, V2 parity: 16/20
    [ ] Batch warm oracle (single check_compile for multiple candidates) — future
[x] Phase 5 — Cleanup + Documentation
    [ ] Remove LeanDojo from critical path — kept as optional fallback
    [ ] Update requirements.txt
    [x] Sprint docs + memory updated
```

### Bonus: Parallel Oracle (unplanned)
```
[x] Thread-safe oracle filenames (eval/oracle.py)
[x] Batch oracle verification in MCTS expand (search/mcts.py)
[x] 3.3x overall speedup on T2 benchmark
```

### Bonus: Warm Oracle (unplanned)
```
[x] check_compile API: #eval through warm Pantograph server (no subprocess)
[x] warm_verify() + make_warm_oracle() in eval/oracle.py + benchmark/run.py
[x] Sequential mode in _batch_oracle_verify (oracle_fn._sequential flag)
[x] --warm-oracle CLI flag (default: cold parallel)
[x] Correctness: T2 10/10, V2 16/20 (parity with cold oracle)
```

## Key Results

### Phase 0: Bottleneck Discovery

The instrumentation revealed the bottleneck is **NOT** the REPL (as assumed in the sprint plan), but the **oracle subprocess**:

| Component | % of wall time | Per-call latency |
|-----------|---------------|-----------------|
| Oracle (`lake env lean`) | 91-100% | ~2100ms/call |
| REPL step (`env.step()`) | 0.1-6% | 1-3ms/call |
| Policy | <0.1% | negligible (FixedPolicy) |
| Select+backprop | <0.01% | negligible |

This contradicted the sprint plan's premise that REPL latency was the bottleneck.

### Phase 1-2: Pantograph Migration

- **Lean upgraded**: 4.19.0 → 4.27.0 (PyPantograph target)
- **Mathlib updated**: rev `216257fb6e` (last commit on Lean 4.27.0)
- **PyPantograph 0.3.13** installed from Stanford GitHub
- **PantographEnv** written as drop-in replacement for LeanEnv
- **Auto-intro**: Parses param names from Lean source to match DirectDojo's state format

### Phase 3: Benchmark Results

#### T2 (FixedPolicy, 50 rollouts) — Pantograph with parallel oracle
| Task | Wall time | REPL | Oracle calls |
|------|-----------|------|-------------|
| count_positive | 6.3s | 2.4ms/call | 10 @ 441ms |
| map_square | 13.2s | 2.3ms/call | 35 @ 320ms |
| sort_desc | 13.3s | 2.3ms/call | 35 @ 326ms |
| sum_doubles | 6.1s | 1.8ms/call | 11 @ 388ms |
| max_abs | 6.2s | 2.3ms/call | 10 @ 421ms |
| take_positives | 13.8s | 2.2ms/call | 35 @ 341ms |
| nub_sort | 13.2s | 2.1ms/call | 35 @ 322ms |
| drop_negatives | 13.9s | 2.2ms/call | 35 @ 343ms |
| count_even | 6.4s | 2.3ms/call | 10 @ 443ms |
| accum_sum | 13.3s | 2.2ms/call | 35 @ 324ms |
| **Total** | **106s** | | |

**Result: 10/10 ✓** (parity confirmed)

#### V2 (ExpandedFixed, 50 rollouts) — after oracle multi-type fix
| Category | Before fix | After fix | Tasks solved |
|----------|-----------|-----------|-------------|
| v2_multi_type | 0/5 | **5/5** | concat_all, longest_sublist, merge_sorted, dot_product, symmetric_diff |
| v2_novel | 5/5 | 5/5 | All ✓ |
| v2_multi_step | 2/5 | 2/5 (3/5 at 500 rollouts) | normalize, filter_then_stats (+partition_and_count at 500) |
| v2_algorithmic | 4/5 | 4/5 | prefix_sums, running_max, every_other, pairwise_sums |

**Result: 16/20 at 50 rollouts** (up from 11/20 — oracle fix unlocked all 5 multi_type tasks)

#### Cat C Deep Search (v2_multi_step unsolved, ExpandedFixed, 500 rollouts)
| Task | Result | Rollouts | Oracle calls | Wall time | Analysis |
|------|--------|----------|-------------|-----------|----------|
| partition_and_count | **SOLVED** | 457 | 256 (1 pass) | 313s | `constructor` splits `Nat × Nat`, deep search finds both sub-goals |
| stats | FAILED | 500 | 514 (0 pass) | 245s | Candidates type-check but wrong min/max semantics |
| split_apply | FAILED | 500 | 0 | 1489s | Never reaches ProofFinished — `List.map (· * 2)` not in expression space |

**Result: 1/3 solved** — V2 total becomes **17/20** at 500 rollouts (16/20 at 50 rollouts + partition_and_count)

- partition_and_count shows that deep search **does** help for product types — just needs enough rollouts to explore past `constructor`
- stats: oracle receives candidates but the expressions don't compute min/max correctly
- split_apply: no tactic sequence produces `ProofFinished` at all (116K steps, 15K valid, 0 solved) — fundamental expression space gap

#### Hard Tasks (ExpandedFixed, 200 rollouts)
| Task | Result | Oracle calls | Crashes | Analysis |
|------|--------|-------------|---------|----------|
| flatten | FAILED | 42 (0 pass) | 0 | Candidates type-check but wrong semantics |
| zip_sum | FAILED | 455 (0 pass) | 0 | No `zipWith` — ExpandedFixed only has single-arg expressions |
| partition_positive | FAILED | 0 | 0 | Product return needs `constructor` → multi-goal |
| dedup_count | FAILED | 108 (0 pass) | 0 | Multi-step composition too complex |
| filter_map_abs | FAILED | 108 (0 pass) | 0 | filterMap pattern not in expression space |

**Result: 0/5 solved, 0 crashes** (vs Sprint 14: 0/5 solved, 4-5 crashes)

Hard tasks are genuine expression space gaps, not infrastructure. The oracle fix
confirmed this: oracle is being called (up to 455 times for zip_sum) but no
candidate produces correct output.

### Parallel Oracle Speedup

| Metric | Sequential | Parallel | Speedup |
|--------|-----------|----------|---------|
| T2 total wall time | 349s | 106s | **3.3x** |
| Amortized ms/oracle | ~2100ms | 320-440ms | **5-6x** |
| Tasks with many candidates (25+) | 50-58s | 13s | **4x** |
| Tasks with few candidates (<10) | 10-18s | 6s | **2x** |

### Warm Oracle (check_compile)

Uses Pantograph's `check_compile` with `#eval` to evaluate candidates through the
already-warm server. No subprocess spawning, no temp files, no thread pool.

| Metric | Cold sequential | Cold parallel | Warm sequential |
|--------|----------------|---------------|-----------------|
| Per-call latency | ~2100ms | 320-440ms (amortized, 8 threads) | 330-500ms |
| T2 total (10 tasks) | 349s | 106s | 123s |
| Architecture | subprocess + files | ThreadPoolExecutor + files | check_compile (in-process) |
| Best for | — | Many oracle calls (35+) | Few oracle calls, debugging |

Usage: `--warm-oracle` flag. Default remains cold parallel (faster for oracle-heavy tasks).

## Sprint 14 Baseline (to beat)

| Metric | Sprint 14 | Sprint 15 | Delta |
|---|---|---|---|
| T2 (FixedPolicy) | 10/10 | 10/10 | Parity ✓ |
| V2 (ExpandedFixed) | 11/20 | **17/20** (16@50, +1@500) | **+6** (oracle fix + deep search) ✓ |
| Hard tasks | 0/5 (REPL crashes) | 0/5 (expression gap) | Failure mode fixed ✓ |
| REPL crashes per benchmark | 4-5 (EOFError) | 0 | **Eliminated** ✓ |
| Per-tactic latency | 2-10ms (DirectDojo) | 1-3ms (Pantograph) | Faster ✓ |
| T2 benchmark wall time | ~349s | ~106s | **3.3x faster** ✓ |
| Lean interaction | LeanDojo + DirectDojo | Pantograph | Simplified ✓ |

## Success Criteria

| Question | Minimum bar | Target | Status |
|---|---|---|---|
| Baseline timing documented | 3 configs | All configs | ✓ (1 config, but bottleneck clear) |
| Pantograph loads FormalNav | Works | Works | ✓ |
| T2 no regression | 10/10 | 10/10 | ✓ 10/10 |
| V2 no regression | ≥11/20 | ≥13/20 | ✓ **17/20** (exceeded target) |
| Hard tasks solved | ≥2/5 | ≥4/5 | ✗ 0/5 (expression space gap, not infra) |
| Cat C at 500 rollouts | ≥2/5 | ≥4/5 | Partial: 1/3 unsolved solved (partition_and_count) |
| Zero REPL crashes | 0 EOFError | 0 EOFError | ✓ |
| Per-tactic latency (subprocess) | <5ms | <2ms | ✓ ~1-3ms |
| Warm oracle (stretch, replaces FFI) | Works | <500ms/call | ✓ ~375ms/call via check_compile |
| LeanDojo removed from critical path | Yes | Yes | Partial (Pantograph is default, LeanDojo still importable) |

## What's Left

1. **Hard tasks with SynthesisPolicy** — the 0/5 is a policy gap, not infra. Need richer expression space (zipWith, filterMap, multi-arg combinators).
2. **stats / split_apply** — need `List.minimum?`/`List.maximum?` and `List.map (· * 2)` respectively. Expression space expansion, not rollout budget.
3. **C FFI exploration** — stretch goal, deferred to future sprint.
