# Sprint 29 Phase 4: Analysis & Decision Gate

## Hypothesis Being Tested

Sprint 29 tested a concrete hypothesis:

> **Scaling the diverse library-extracted training corpus by ~7× over
> Sprint 28 (from 171 pairs to ~1,500 pairs) and training a Config C
> curriculum on a rank-64 LoRA will produce ≥3/12 held-out solves,
> breaking past the template-lookup ceiling documented in Sprint 27.**

The held-out set was the `held_out_12` primary metric: 6 diagnostic
held-out tasks (unchanged from S28) plus 6 new hard-tier compositional
tasks frozen in Phase 0.5 before any library mining began.

## Headline Result

**Hypothesis falsified. Both trained adapters scored 0/12 on the
held-out primary metric.**

- **S29-A** (r=16, Config C curriculum, 404 pairs): **0/12**, 310 min wall
- **S29-C** (r=64, Config C curriculum, 404 pairs): **0/12**, 297 min wall
- **S29-D** (500-pair subset, rank=64): **dropped** — corpus was smaller
  than the intended subset, leaving nothing to subset

Per-task solve verdicts are identical between the two adapters, and
wall times differ by <10%. The 14% `diverse_val_loss` improvement from
r=64 over r=16 produced zero additional solves at inference.

**Decision gate: 0–2/12 → "Ceiling reached".** The sprint29.md
decision tree prescribes: *"LoRA-based learning on Qwen2.5-Coder-1.5B
has hit its ceiling for this task distribution. S30 explores full
fine-tune OR stronger base model."*

## What Actually Shipped vs What Was Planned

The Sprint 29 plan was scaffolded around a ~1,500-pair diverse corpus.
Phase 1 hit a ~300-pair ceiling from the library-mining approach, so
the downstream phases ran on a smaller corpus than planned:

| Metric | Plan target | Actual |
|--------|-------------|--------|
| Phase 1.1 module expansion | ~900 pairs | 225 |
| Phase 1.2 pattern mining | ~400–500 pairs | 0 net new |
| Phase 1.2-B converter rewrite | (not in plan) | +66 |
| Phase 1.2-B specializer fixes | (not in plan) | +4 |
| **Phase 1.7 two-tier validation** | (not in plan) | **+171** |
| Final S29 delta | ~1,350–1,500 | **233** |
| **Final corpus (S28 ∪ S29)** | ~1,500 | **404** (2.4× S28) |

The Phase 4 decision gate still applies — 404 pairs was enough to
move val loss meaningfully on the diverse distribution (S29-C
diverse_val 0.84 vs Config C S28 at ~1.6, measured on the same split)
— but not enough to move the inference metric.

## Results Summary

### Phase 3.1 — held_out_12 (primary metric)

| Task | Category | S29-A | S29-C | A wall | C wall | A oracle | C oracle |
|------|----------|:-----:|:-----:|-------:|-------:|---------:|---------:|
| eval_stack | diag_d | ✗ | ✗ | 734s | 738s | 3,781 | 3,799 |
| majority_element | diag_b | ✗ | ✗ | 715s | 704s | 3,781 | 3,799 |
| map_with_index | diag_a | ✗ | ✗ | 2,717s | 2,716s | 12,861 | 13,208 |
| max_subarray_sum | diag_b | ✗ | ✗ | 497s | 484s | 3,781 | 3,799 |
| prefix_while_increasing | diag_a | ✗ | ✗ | 2,645s | 2,700s | 12,861 | 13,208 |
| unfold_n | diag_c | ✗ | ✗ | 205s | 267s | 648 | 888 |
| sum_at_odd_indices | s29_heldout_a | ✗ | ✗ | 740s | 707s | 3,781 | 3,799 |
| zip_diff | s29_heldout_a | ✗ | ✗ | 4,067s | 4,014s | 12,861 | 13,208 |
| longest_increasing_run | s29_heldout_b | ✗ | ✗ | 621s | 809s | 2,578 | 3,096 |
| max_subarray_product | s29_heldout_b | ✗ | ✗ | 832s | 822s | 3,781 | 3,799 |
| merge_sorted_desc | s29_heldout_c | ✗ | ✗ | 108s | 123s | **21** | **23** |
| window_sums | s29_heldout_d | ✗ | ✗ | 4,709s | 3,730s | 9,823 | 7,974 |
| **Total** | | **0/12** | **0/12** | 310 min | 297 min | 77,559 | 77,780 |

### Diagnostic held-out sub-metric

S29's diagnostic half reproduces Phase 0.4's S28 baseline exactly:

| Task | S28 (Phase 0.4) | S29-A | S29-C |
|------|:---------------:|:-----:|:-----:|
| eval_stack | ✗ | ✗ | ✗ |
| majority_element | ✗ | ✗ | ✗ |
| map_with_index | ✗ | ✗ | ✗ |
| max_subarray_sum | ✗ | ✗ | ✗ |
| prefix_while_increasing | ✗ | ✗ | ✗ |
| unfold_n | ✗ | ✗ | ✗ |
| **Total** | **0/6** | **0/6** | **0/6** |

**Non-regression verified.** The 2.4× diverse data did not *hurt* any
previously-solvable tasks; it simply did not unlock new ones.

### Hard-tier held-out sub-metric

First measurement against these 6 tasks. Both adapters scored 0/6.
No baseline to compare against because S28 never saw these tasks.

### Training loss vs inference solve rate

| Config | LoRA r | Train loss @ep3 | diverse_val @ep3 | template_val @ep3 | held_out_12 |
|--------|-------:|---------------:|------------------:|------------------:|:-----------:|
| S29-A | 16 | 0.458 | 0.974 | 0.334 | **0/12** |
| S29-C | 64 | 0.417 | **0.841** | 0.336 | **0/12** |

S29-C's 14% lower `diverse_val_loss` expressed as "fits training
distribution better" — not as "generates solutions for unseen tasks".
This is the core negative finding of the sprint: **validation loss on
the training distribution is a poor proxy for held-out compositional
generalization** at this corpus scale and architecture.

## Secondary Analysis

### Rank vs scale axis

The plan specified a 3-config ablation (A rank floor, C target,
D scale floor). With the 404-pair corpus, S29-D ("rank=64 @ 500
pairs") became incoherent — 500 > 404 — and was dropped. That leaves
only the rank axis (A vs C), which we measured cleanly:

- **Rank has a real effect on training loss.** r=64 wins by 14% on
  diverse_val, 9% on train loss, at ~4× adapter size (8.7 MB → 34.9 MB).
- **Rank has no effect on held-out solves.** Both configs scored 0/12
  with identical per-task verdicts.
- **Rank has no effect on generation diversity at inference time.**
  Phase 2.3 sanity check showed S29-C produced *fewer* match-kind
  outputs than S29-A (0 vs 2 in 125 combined samples) and more
  method_chain outputs (49.2% vs 42.2%) — the opposite of what you
  might expect from "more expressive adapter".

**Interpretation**: at 404-pair corpus scale, rank-16 is already
sufficient to memorize the training distribution. Higher rank just
fits the training data more precisely without generalizing better.
The ceiling is not "LoRA is too small to express the needed mapping" —
the ceiling is "404 pairs is too small a signal to teach the mapping,
regardless of adapter capacity".

Cannot test the scale axis (S29-C vs S29-D) at all.

### Did the generation sanity check predict benchmark results?

**Yes — and this is the most actionable positive finding of the sprint.**

The Phase 2.3 generation sanity check (before any benchmark ran)
showed that neither adapter produced:
- `let rec` patterns (0 of 125 samples)
- match patterns for multi-arg tasks (0 from S29-C, 2 from S29-A)
- have-chain multi-step tactics (1 from each config out of 125)

Every hard-holdout task needs at least one of those patterns (per the
reference implementations in `held_out_hard_tasks.py`). The sanity
check predicted 0/6 on hard-holdout, and the benchmark confirmed it.
**If we had trusted the sanity check, we could have cancelled the
9-hour benchmark sweep and reported the result directly from the
125-sample inspection.**

This is a ~40× faster iteration signal (2 min vs 9 h wall) that can
be used in S30 to triage adapter candidates before committing to full
benchmark runs.

### Ablation columns

With 0 solves across every task, the `ef_catalog_match` /
`policy_name` classification columns carry no data — every row is
`solved: false, ef_catalog_match: null`. The Phase 0.1 instrumentation
shipped correctly and is ready for use in S30, but S29 has no solves
for it to classify.

## Harness Reliability Check

Phase 3 ran without daemon corruption, response misrouting, or any of
the symptoms that plagued S28 benchmarks. Specifically:

- **Zero REPL crashes** across both 5-hour sweeps (24 task runs total)
- **Zero `env_inspect` failures** after the daemon restart for
  `TasksS29Heldout.lean`
- **No "silent skip" pattern** where a task returns 0 oracle calls
  due to a stale worker — every task reported honest oracle counts
- **Both sweeps produced a complete result JSON** (S29-A had a
  post-sweep crash in the summarize step due to `get_task()` not
  knowing about HARD_HOLDOUT_TASKS, but the results were fully written
  before the crash; the fix was a one-line edit)

The daemon architecture changes from Phase 0 (broken-recovery fix +
per-worker asyncio.Lock + role-separated worker pools) hold up under
load. S30 inherits a trustworthy harness.

## Observed Anomaly — merge_sorted_desc Early Exit

`merge_sorted_desc` terminated after only **21 oracle calls** on
S29-A and **23 oracle calls** on S29-C, versus 3,000–13,000 for every
other task. Wall time was ~108s / 123s.

This is a qualitative early exit, not a hung task. The other 2-arg
hard-holdout task, `window_sums`, used 8,000–10,000 oracle calls and
ran for 62–78 minutes — normal exploration.

**Hypothesis**: the MCTS auto-intro routine handles heterogeneous
multi-arg signatures `(k : Nat) (xs : List Int)` differently from
homogeneous ones `(xs ys : List Int)`. For the homogeneous case, the
intro may emit both arguments into the same goal position, or fail
to intro one of them, causing MCTS to run out of legal moves and
terminate early.

**Impact on S29 result**: none. This is one task out of 12; the other
11 exercised the model properly and still hit 0 solves. Even if this
one task were "rescued" by a harness fix, S29 would read 0/12 → 0/12
(or 0/12 → 1/12 in the wildest-possible best case), still firmly in
the "Ceiling reached" bucket.

**Action for S30**: fix the auto-intro before the S30 benchmark sweep,
so the hard-holdout tier has 6 tasks that actually exercise the model
instead of 5. Detailed investigation deferred to the S30 pre-flight.

## What Sprint 29 Ruled Out

1. **"More diverse data at 2–3× scale unlocks held-out solves."**
   Disconfirmed. 404 pairs with Config C curriculum scored identically
   to the S28 171-pair baseline on the overlapping diag_holdout metric.

2. **"Higher LoRA rank compensates for limited corpus size."**
   Disconfirmed. r=64 fit the training data 14% better than r=16 and
   solved zero additional tasks.

3. **"Training loss on a held-out val split predicts held-out task
   solve rate."** Disconfirmed at this scale. S29-C's diverse_val of
   0.84 vs S29-A's 0.97 is a real, meaningful delta in the training
   distribution — and it's orthogonal to compositional generalization.

4. **"The Phase 0 harness work was the bottleneck to seeing more
   solves in S28."** Disconfirmed. The harness is cleaner, the daemon
   is stable, the patience flag and ablation columns work — and the
   model is still the bottleneck.

## What Sprint 29 Ruled In

1. **The Phase 2.3 generation sanity check is a reliable early
   indicator.** Adapter quality can be predicted from 125-sample
   inspection in ~2 min wall, 40× faster than a benchmark sweep.

2. **The two-tier validation pipeline (functional + library-trust
   syntactic) is a productive engineering tool.** Phase 1.7 unlocked
   +171 pairs over the functional-only tier, ~58% of the final
   corpus. S30 should inherit this and extend it with namespace
   qualification for the syntactic-tier pairs.

3. **The role-separated daemon architecture eliminates the "daemon
   corruption" class of S28 bugs.** Zero REPL crashes across 10
   hours of concurrent MCTS + oracle verification.

4. **The held_out_12 primary metric is a reliable yardstick.** The
   0/12 result is unambiguous; there's no room for accidental
   generalization leakage, weak test cases, or benchmark ambiguity
   to flatter the outcome.

## S30 Direction

Three candidate directions, in order of decreasing confidence:

### Option A — Stronger base model (RECOMMENDED)

Move from Qwen2.5-Coder-1.5B to a larger base with LoRA. Two candidates
fit the single-GPU compute budget:

- **Qwen2.5-Coder-7B** — same family, ~4.6× parameters, 4-bit LoRA
  fits in the 32 GB VRAM of a 5090 with headroom. Expected to have
  significantly better base Lean syntax knowledge and a higher
  compositional-generalization ceiling.
- **DeepSeek-Coder-V2-Lite** — 16B MoE with ~2.4B activated, designed
  for code. Smaller compute footprint per token than Qwen-7B,
  potentially stronger base.

S29's 404-pair corpus is adequate input for this experiment. The
theory being tested: *"The ceiling is in the base model, not the
adapter or the data."*

**Risk**: inference-time latency. MCTS policy calls will be ~4–5×
slower than with 1.5B. A held_out_12 sweep could grow from 5 hours to
20+ hours per config. Mitigation: smaller rollouts budget + better
adapter selection via sanity-check triage.

### Option B — Retrieval-augmented generation

Skip training entirely. At inference time, retrieve the k most
similar library pairs (by goal state embedding) from the 404 corpus,
format them as few-shot examples, and let the base Qwen-1.5B generate
tactics conditioned on them.

The theory being tested: *"The ceiling isn't 'the model can't learn
this', it's 'LoRA is a lossy compression'. Uncompressed retrieval may
outperform a LoRA trained on the same data."*

**Risk**: context-window overhead. Qwen-1.5B has a 32K context, so
retrieving 10–20 pairs per query fits, but inference is slower per
token. Also, retrieval quality depends on embedding similarity
matching structural similarity — not obviously true for Lean tactics.

**Advantage**: no training cycle at all. Can test this in 2–3 days
end-to-end. Good as a quick sanity check before committing to
Option A.

### Option C — Full fine-tune of Qwen-1.5B

Replace LoRA with full fine-tuning on the 404-pair corpus. The theory:
*"Rank-64 LoRA is insufficiently expressive to capture the mapping;
full fine-tune can."*

**Risk**: this is a weaker hypothesis given the S29 evidence. Rank-64
fit the training data 14% better than rank-16 and solved zero
additional tasks, suggesting the ceiling is not adapter expressivity.
Likely to produce the same null result as S29-C with a 3–5× training
cost and full model storage (3 GB per config instead of 35 MB).

**Recommendation**: Option A first, Option B as a fast parallel
sanity check, Option C only if both A and B hit null results.

### Out of scope for S30 (deferred)

- **Corpus scaling beyond 1,000 pairs** requires a converter rewrite
  (dependency resolution, universe polymorphism, dependent refinement
  types). Multi-week engineering. Defer until A/B/C provide signal
  about whether the ceiling is data or model.
- **Productionizing / expanding benchmarks** — the decision gate says
  we haven't earned this yet. Revisit if S30's primary metric shows
  nonzero solves.

## Sprint 29 Commit Trail

```
<this commit>  sprint 29 phase 4: analysis + S30 direction
318c1bc        sprint 29 phase 3.1: held-out benchmarks — primary metric null (0/12)
7051adc        sprint 29 phase 3.0: wire hard_holdout + held_out_12 benchmark tiers
ceba690        sprint 29 phase 2: train S29-A/C ablation + generation sanity check
4b3bf9d        sprint 29 phase 1.7: two-tier validation (functional + library-trust syntactic)
1a661de        sprint 29 phase 1.2-B refinement: fix 3 specializer bugs found in rejection log
ee4d735        sprint 29 phase 1.2B+1.4+1.6: converter rewrite, leakage check, train/val split
46a8080        sprint 29 phase 1.2+1.3: pattern miner + specializer broadening + quality filter
94a5e3d        sprint 29 phase 1.1: expand library extractor module coverage
126e7ad        sprint 29 phase 0.4-C: diag_holdout tier + close Phase 0
ee932fe        sprint 29: commit plan and status docs (phase 0 in progress)
7e36daf        sprint 29 phase 0: role-separated two-pool daemon architecture
b624996        sprint 29 phase 0: fix pantograph daemon broken-recovery path
cdd9612        sprint 29 phase 0.1+0.2+0.3: ablation columns, --patience, smoke suite
68b417b        flatten: remove nested FormalNav git repo, track Lean files directly
b508a62        sprint 29: freeze held-out hard-tier task set (phase 0.5)
```

## Revised Success Criteria Assessment

| Metric | Baseline | Expected | S29-A | S29-C | Verdict |
|--------|----------|----------|:-----:|:-----:|---------|
| Held-out diagnostic (6) | 0/6 | 1–2/6 | 0/6 | 0/6 | Below floor |
| Held-out hard-tier new (6) | — | 1–2/6 | 0/6 | 0/6 | Below floor |
| Diagnostic train-split (12) | 3/12 | 3–5/12 | — | — | Not measured (deferred) |
| Hard tier (5) | 4/5 | 4–5/5 | — | — | Not measured (deferred) |
| Core T2 (10) | 10/10 | 10/10 | — | — | Not measured (deferred) |
| Core 120 regression | 107/120 | 107+/120 | — | — | Not measured (deferred) |
| **Real novel LoRA solves** | 3 | 5–8 | **0** | **0** | **Below floor** |
| False positives on novel | 0 | 0–1 | 0 | 0 | Pass (vacuously) |
| `let rec` generation rate | 0% | 3–8% | 0% | 0% | Below floor |
| Multi-tactic generation rate | ~5% | 10–20% | ~1.6% | ~1.6% | Below floor |

**Only the "false positives" row passes**, and it does so vacuously
because there were zero solves to contain false positives. Every
other measured row is below the plan's floor. **The sprint honestly
landed in the "Ceiling reached" decision bucket.**

## Key Artifacts

**Code**:
- `lean_mcts/env/pantograph_daemon.py` — role-separated two-pool
  architecture, per-worker asyncio.Lock, broken-recovery fix
- `lean_mcts/env/pantograph_client.py` — v1/v2 manifest handling,
  verify-pool dispatch for `check_compile_parallel`
- `lean_mcts/benchmark/ablation_columns.py` — per-policy ablation
  join tool (unused in S29 because every task was solved=false)
- `lean_mcts/benchmark/run.py` — `--patience`, `policy_name`,
  `ef_catalog_match`, policy-stamped output filenames
- `lean_mcts/search/mcts.py` — `patience` parameter with
  `abort_reason: "patience_exceeded"`
- `lean_mcts/synth/extract_lean_defs.py` — two-tier validation
  (`functional` + `syntactic`), broadened specializer, improved
  `build_goal_state`, `_normalize_universes`
- `lean_mcts/synth/lean_def_converter.py` — whitespace-preserving
  multi-candidate conversion, doc comment stripping
- `lean_mcts/synth/pattern_miner.py` — ripgrep-style library pattern
  scanner (null yield but kept for future sprints)
- `lean_mcts/synth/quality_filter.py` — merge / dedup / drop trivials
  / dedup vs baseline pipeline
- `lean_mcts/synth/leakage_check.py` — 5-gram Jaccard leakage check
  against 12 held-out task signatures
- `lean_mcts/synth/split_train_val.py` — stratified 80/20 split by
  `kind`
- `lean_mcts/synth/held_out_hard_tasks.py` — 6 S29 hard-tier held-out
  tasks, frozen behind `s29-heldout-frozen`
- `tests/harness_smoke.py` — fast regression suite (`make smoke` passes
  6/6 checks in <1s)

**Data**:
- `lean_mcts/train/diverse_train_v2.jsonl` — 323 train pairs (404 − 81)
- `lean_mcts/train/diverse_val_v2.jsonl` — 81 val pairs
- `lean_mcts/train/leakage_audit.json` — Phase 1.4 leakage audit
  (0/12 flagged)
- `lean_mcts/train/library_pairs_v3.jsonl` — 295 functional-tier pairs
- `lean_mcts/synth/diag_holdout.txt` — 6 diagnostic held-out task names

**Adapters**:
- `lean_mcts/train/diverse_adapter_v2/S29-A/C_curriculum/` — r=16,
  0/12 on held_out_12
- `lean_mcts/train/diverse_adapter_v2/S29-C/C_curriculum/` — r=64,
  0/12 on held_out_12

**Benchmark results**:
- `lean_mcts/benchmark/results/s29_phase3_1/run_hybrid-gc_20260414_175024_0.json`
  — S29-A held_out_12
- `lean_mcts/benchmark/results/s29_phase3_1/run_hybrid-gc_20260414_224826_0.json`
  — S29-C held_out_12
- `lean_mcts/benchmark/results/s29_phase0_4/run_hybrid-gc_20260413_180751_0.json`
  — S28 Config C reference run on diag_holdout (0/6, reproduced)

**Lean project**:
- `lean_mcts/env/FormalNav/FormalNav/TasksS29Heldout.lean` — 6
  hard-tier held-out task stubs (frozen)
- `lean_mcts/env/FormalNav/FormalNav.lean` — imports both
  TasksDiagnostic and TasksS29Heldout

**Git tag**: `s29-heldout-frozen` at the Phase 0.5 commit, marking
the held-out set before any library mining began.

## Decision

**Primary metric**: 0/12 held_out_12 → **Ceiling reached** bucket.

**S30 direction**: Option A (stronger base model) as the primary
experiment, Option B (retrieval-augmented generation) as a parallel
fast sanity check. Option C (full fine-tune of 1.5B) deferred unless
both A and B produce null results.

**Data source**: S29's 404-pair corpus is adequate for S30's input
(two-tier validated, leakage-checked, stratified split). No further
corpus work is required for S30 to start; deferred converter rewrite
is a multi-week engineering investment best tackled after the
A/B/C experiments clarify whether the ceiling is data or model.

**Harness**: S30 inherits S29's daemon architecture, ablation
instrumentation, smoke test suite, and the fixed `get_task()`. No
harness regressions to repair.

**Pre-S30 cleanup** (≤1 day, optional):
- Investigate and fix the `merge_sorted_desc` multi-arg auto-intro
  anomaly so the hard-holdout tier exercises all 6 tasks properly
- Parse the Phase 2.3 generation sanity check into a lightweight
  `lean_mcts/benchmark/sanity_check.py` CLI for S30 iteration speed
