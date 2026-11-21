# Sprint 29: Scale the Diversity Signal — Held-out Generalization or Bust

## Goal

Scale Sprint 28's Config C curriculum approach 10x (164 → 1,500+ library pairs) targeting the specific compositional patterns that Sprint 27 classified as failures, and measure whether the learned model can generalize to held-out tasks that share NO training variants. The primary deliverable is an honest answer to a single question:

> **Does scaling diverse training data produce meaningful generalization to held-out tasks, or has the system hit a ceiling that requires architectural change?**

## Motivation

### Three findings from Sprint 28 define this sprint

**Finding 1: The ML signal is real but undersized.** Config C produced the first genuine LoRA-contributed novel solves in 12 sprints (3 real novel on hard tier: `zip_sum`, `dedup_count`, `filter_map_abs`). These came entirely from library extraction, not augmentation.

**Finding 2: Diagnostic augmentation is dead.** All three S28 configs scored 0/6 on held-out diagnostic tasks. Flipping predicates and changing initial values on train-split tasks taught the model nothing that transferred. **S29 drops diagnostic augmentation entirely.**

**Finding 3: The current scale is capacity-starved.** 164 pairs with LoRA r=16 was insufficient to generate `let rec` despite 30% of training being let_rec examples. Qwen's Lean grammar degraded (C++/JS fallback) because 164 pairs cannot re-teach syntax. Scaling data AND LoRA rank is required to test the hypothesis at full strength.

### The question Sprint 29 must answer

Sprint 28's primary metric was held-out generalization. It scored 0/6. That is the real test of whether the system can generalize — and the current answer is "no, at 164 pairs." Sprint 29 tests whether that answer changes at 10x data + higher LoRA rank.

This is a **decision sprint**. The three possible outcomes all feed into concrete next steps:

- **Held-out 0–1/6**: System has hit the ceiling of LoRA-based learning on this base model. Next step: full fine-tune Qwen2.5-Coder-1.5B or switch to a stronger base model.
- **Held-out 2–4/6**: Scaling works but is capacity-bound. Next step: continue scaling to 5000+ pairs at r=64.
- **Held-out 5+/6**: Hypothesis fully confirmed. Next step: productionize Config C pipeline, expand benchmark, investigate multi-task transfer.

### Why we must also audit the harness

Sprint 24 found the `constructor_boost` goal extraction bug had been silently broken for 6 sprints (using last `⊢` instead of first). Sprint 26 found daemon corruption had been eating solves for 3+ sprints. Sprint 28 found a DataLoader semaphore leak that hung Config C epoch 3. **The harness has accumulated complexity faster than test coverage.** Before running expensive training runs, we must verify the evaluation infrastructure is trustworthy, or we risk chasing ghosts in the results.

## Hypothesis

Sprint 28 validated the weak form of the hypothesis at 164 pairs: diversity training produces structural pattern learning. Sprint 29 tests the strong form:

> **With 1,500+ high-quality library pairs and LoRA r=64, a Config C curriculum-trained adapter will solve ≥3/12 aggregate held-out tasks (6 diagnostic + 6 new hard-tier) with zero false positives, or match/beat S28's 4/5 hard tier + 3/18 diagnostic while failing the held-out metric (capacity bound).**

We explicitly commit to the held-out metric. We will not move the goalposts mid-sprint. If the aggregate held-out score stays at 0–2/12, we report that as the answer and pivot.

## Data Strategy

### Principle: pure library mining, pattern-targeted

Sprint 28's 171 library pairs produced 3 novel solves. Per-pair yield is ~1.8% — roughly one real solve per 60 pairs. If linearity holds, 1,500 pairs gives ~25 candidate novel contributions. Even at 10% linearity-decay, that's ~10 solves. This is the rough ROI model.

**What we want:** patterns from Sprint 27's 16 classified failures — tuple accumulators with field interaction, state machines, recursive helpers, operation chains, index-tracking folds, multi-pass algorithms.

**What we drop:** diagnostic augmentation. S28 proved it contributes zero held-out transfer. Every pair in S29 is real library code.

### Source expansion plan

Sprint 28's `extract_lean_defs.py` processed ~11 modules with 531 discovered definitions, 175 verified pairs. Expansion vectors:

**1. Broaden module coverage (target: +800 pairs)**

| Library | S28 Coverage | S29 Target | Expected Yield |
|---------|--------------|-----------|----------------|
| stdlib `Init/Data/List` | ~5 modules | all modules (~15) | +150 pairs |
| stdlib `Init/Data/Array` | 0 | all (~10 modules) | +150 pairs |
| stdlib `Init/Data/Option`, `Sum`, `Prod` | 0 | all | +80 pairs |
| stdlib `Init/Data/Nat`, `Int` | limited | algorithmic modules | +100 pairs |
| Batteries `Data.List` | `Basic.lean` | all modules (~10) | +200 pairs |
| Batteries `Data.Array`, `Data.HashMap` | 0 | all | +150 pairs |
| Mathlib `Data.List.*` (def-rich) | `Defs.lean`, `Cycle.lean` | +5 highest-def modules | +80 pairs |

Raw target: **~900 additional library pairs**, combined with S28's 171 = **~1,100 total baseline**.

**2. Pattern-targeted mining (target: +400–500 pairs)**

Go beyond passive file-walking. For each of Sprint 27's 16 failure categories, search Lean libraries for definitions matching the required pattern. Examples:

- **Tuple-accumulator folds** (Kadane, Boyer-Moore): search for `foldl` with binary tuple init and field-interaction step functions. Target: Mathlib algorithm files, `Std.Data.List.Basic`, `Batteries.Data.List.Count`.
- **State machines via fold**: search for `foldl` with 3+ field records or sum-type accumulators. Target: parser/lexer implementations in Lean core.
- **Recursive helpers with accumulators**: search for `where` clauses with `acc` parameter. Already yielded 63 `let rec` pairs in S28; expand the search to `Init.Data.Array` and monadic library code.
- **Index-tracking folds**: search for `foldlIdx`, `mapIdx`, `enum` usage patterns. Target: Mathlib `List.Range`, `Fin` files.
- **Chained `match` with recursion**: search for multi-arm `match` definitions with 3+ cases. Target: `Init.Data.List.Basic`, `Batteries.Data.List.Basic`.

Build a simple ripgrep-based pattern matcher (`pattern_miner.py`) that identifies candidate definitions by syntactic signature, then hands them to the existing converter. The target is **400–500 pattern-matched pairs that specifically address Sprint 27's gaps.**

**3. Quality filters (remove ~10–20%)**

- Drop pairs where the body is a single applied library function (`def foo xs := List.reverse xs` — the template catalog already covers these)
- Drop pairs whose type has unresolved universe polymorphism or non-trivial typeclass constraints that Pantograph can't concretize
- Drop pairs with body length > 512 tokens (decoder budget limit)
- **Deduplicate against S28's 171-pair corpus** (keep first-wins)

### Total target yield

- Library raw: 1,100 pairs
- Pattern-targeted: +400–500 pairs
- Minus quality filters: -150 pairs
- **Final target: 1,350–1,500 diverse pairs**

Lower bound: if pattern mining underperforms (yields <200 pairs), settle for 1,100–1,200 and proceed. The floor is **"at least 7x S28 scale."** Anything less than 1,100 is a soft failure and we re-evaluate Phase 1 before training.

### Held-out discipline

Sprint 28's held-out split (6 tasks) is preserved and frozen. No augmentations, no variants, no related library definitions. `lean_mcts/synth/diag_holdout.txt` remains untouched.

**New hard held-out set (6 additional tasks)** is curated in Phase 0.5 — **before** any S29 mining output exists — modeled on Sprint 27's 16 classified failure patterns. Curating after mining would invite motivated construction (picking tasks the mined corpus happens to support or happens to miss). Frozen behind the `s29-heldout-frozen` git tag.

Held-out set total: **12 tasks** (6 diagnostic + 6 new hard-tier). **Zero overlap** with any training pair, augmented or otherwise. Verified by token-level n-gram search before training starts.

## Implementation Plan

### Phase 0: Harness Audit & Evaluation Hygiene (2 days)

The cost of running one bad training experiment + benchmark is ~1 day. The cost of this audit is 2 days. The math is obvious given S24/S26/S28's silent-bug history.

**0.1 Ablation columns in benchmarks**

The Sprint 27 finding that "LoRA is dead weight" came from noticing hybrid-gc ≡ expanded-fixed. This should have been a standing invariant checked after every retraining, not a post-hoc discovery. Add to `benchmark/run.py`:

- For each task, record which policy solved it: `{expanded_fixed, lora_only, hybrid, unsolved}`
- For each solved task, record the specific candidate tactic string and its source (template ID or LoRA generation)
- Output per-policy columns in the results JSON: `ef_solved`, `lora_only_solved`, `hybrid_solved`, `lora_contribution` (true if lora_only OR hybrid-without-ef)

Every S29 benchmark run must report these columns. If `lora_contribution == 0` across all tasks, the LoRA is dead and we flag it immediately (not 6 sprints later).

**0.2 `--patience N` early-abort flag**

Unsolvable tasks currently burn the full rollout budget (~80 min wall time each). Add to `mcts.py`:

- Track consecutive rollouts without a passing candidate
- If this counter exceeds `N` (default: 100), abort the task as unsolved
- Emit the abort reason in the task result

Sprint 25 lessons estimated this drops stretch benchmark time 27h → ~3h. Validate the estimate on one stretch run. This unlocks fast iteration, which unlocks everything else.

**0.3 Harness smoke tests**

Create `tests/harness_smoke.py` — a fixed suite that runs nightly-style checks:

1. One solved task per tier with known template answer (should solve at rollout 1)
2. One task with known multi-step answer (exercise the `have+exact` path)
3. One unsolvable task (exercise `--patience` abort)
4. One task with a LoRA-only solution from S28 Config C (use `zip_sum`, `dedup_count`, or `filter_map_abs` — the 3 real novel solves) — exercises the ablation column and verifies adapter-loading infra
5. Full benchmark with and without the daemon to verify daemon startup/shutdown is clean
6. Goal state extraction on a constructor-split task (the S24 bug)
7. Unicode type handling on a ℤ/ℕ task (the S24 bug)

Target: the smoke suite runs in <5 min and must pass before any training run starts. Add it to a `make smoke` target.

**0.4 Re-verify S28 artifacts**

Before building on S28:
- Re-run S28 Config C benchmark on diag + hard + core T2 + held-out
- Confirm 3/18 diag, 4/5 hard, 10/10 T2, 0/6 held-out (with adapter loaded from `diverse_adapter/C_curriculum/`)
- Run reproduction **twice**: once with `--patience` disabled (equivalent to unlimited) and once with `--patience 100`. Any task that solves in the first run but not the second means `--patience` is aborting too aggressively — tune the default before proceeding. If both runs match, `--patience 100` is confirmed safe for Phase 3.
- Restart daemon between every benchmark
- If results don't match S28 status, investigate before proceeding

**0.5 Freeze held-out sets before any mining starts**

To prevent motivated curation (picking tasks the mined corpus happens to support or happens to miss), both held-out sets must be frozen before Phase 1 begins.

- Diagnostic held-out (6 tasks): unchanged from S28, already frozen in `lean_mcts/synth/diag_holdout.txt`
- Hard-tier held-out (6 new tasks): curated now, not during Phase 1
  - Pick 6 compositional patterns from Sprint 27's 16 classified failures **before** any S29 mining output exists
  - Write reference implementations and initial (non-adversarial) test cases
  - Commit to `lean_mcts/synth/held_out_hard_tasks.py`
  - Git-tag the commit `s29-heldout-frozen` so any later edit is auditable

Phase 1 leakage check (1.4) uses this frozen set as ground truth; it cannot be edited after Phase 1 starts. Any change to the 12-task held-out set after this tag invalidates the sprint's primary metric.

**Exit criterion**: Smoke suite passes. S28 results reproduce exactly (with and without `--patience`). Ablation columns appear in benchmark JSON. `--patience` cuts unsolvable-task time by ≥5x. 12-task held-out set committed and git-tagged `s29-heldout-frozen`.

### Phase 1: Library Mining at Scale (4–5 days)

**1.1 Expand module coverage** (Day 1)

Extend `extract_lean_defs.py`:
- Add module lists for stdlib Array/Option/Sum/Prod/Nat/Int
- Add Batteries Data.Array, Data.HashMap
- Add Mathlib Data.List algorithmic files (top 5 by def count)
- Polymorphic specialization: extend the existing `specialize_polymorphic` to handle Array + Option + Nat + Sum in addition to current List[Int].

Run on expanded module list. Expected: ~900 verified pairs (before pattern mining).

**1.2 Build pattern_miner.py** (Day 2)

A ripgrep-based scanner that takes Sprint 27's 16 failure categories as input and produces a list of candidate definitions by signature:

```python
PATTERNS = {
    "tuple_acc_fold": r"foldl\s+\([^)]*=>[^)]*\([^,]+,[^)]+\)",
    "state_machine_fold": r"foldl\s+\w+\s+\([^)]*,[^)]*,[^)]*\)",
    "indexed_fold": r"(foldlIdx|mapIdx|enum|zip\s+\(List\.range)",
    "recursive_helper_acc": r"where\s+\w+\s*:?.*?\s+acc\s*:",
    "multi_arm_match": r"match\s+.*?\s+with.*?\|.*?\|.*?\|",  # 3+ arms
    # ...
}
```

For each pattern, search the cloned libraries, dedupe, and hand the results to the existing converter + Pantograph verifier. Expected: ~400–500 pattern-matched pairs.

**1.3 Quality filter pass** (Day 3)

- Dedupe against `library_pairs.jsonl` from S28
- Drop pairs whose converted body is < 3 AST nodes (template-covered trivials)
- Drop pairs > 512 tokens
- Drop pairs with typeclass constraints Pantograph fails on

Output: `lean_mcts/train/diverse_train_v2.jsonl` (target 1,350–1,500 pairs)

**1.4 Held-out leakage check** (Day 3)

For each of the 12 held-out tasks:
- Extract key function names and unique library function calls (e.g., `Kadane`, `maxSubarraySum`, `boyerMoore`, `majorityElement`)
- Grep the training set for any n-gram overlap with the held-out reference implementations
- If any match: remove from training, document

Output: `diag_holdout_leakage_check.json` (audit trail)

**1.5 Diversity + coverage audit** (Day 4)

Re-run the S28 diversity audit on the new corpus:
- `match`, `let rec`, `lambda`, `foldl/r`, simple `exact` percentages
- New category: `tuple_accumulator_fold`, `index_fold`, `state_machine_fold`
- Gate: ≥50% non-trivial patterns (up from S28's 40%)

Output: `diversity_audit_v2.json`

**1.6 Train/val split + freeze** (Day 4–5)

- 80/20 split, stratified by pattern category
- Freeze: no further additions during training phase
- Files: `diverse_train_v2.jsonl`, `diverse_val_v2.jsonl`

**Exit criterion**: ≥1,100 pairs (floor) / ~1,500 pairs (target); diversity audit ≥50% non-trivial; held-out leakage check passes; pattern-category coverage balanced.

### Phase 2: Training — Ablation Grid (2–3 days)

**2.1 Fix the DataLoader leak** (half day)

Sprint 28's Config C hung in epoch 3 due to multiprocessing semaphore leak. Sprint 29 will run 3 training configurations (plus potential re-runs). The hang must be resolved first.

- Reproduce with Sprint 28's training script
- Likely fix: `num_workers=0`, `persistent_workers=False`, or explicit DataLoader cleanup between epochs
- Verify: Config C runs end-to-end on S28 data without hanging

**2.2 Ablation grid: rank × scale (3 focused configs)** (1 day)

Run 3 focused configurations using the winning Config C curriculum (epoch 1 diverse-only, epochs 2–3 10x-weighted mix). The grid isolates two axes: LoRA rank (A vs C) and data scale (C vs D). Earlier drafts ran 6 configs; the 3-config grid answers the same rank-vs-scale question with half the training budget and lower harness-corruption risk from a long benchmark sweep.

| Config | LoRA rank | Data scale | Purpose |
|--------|-----------|-----------|---------|
| S29-A | r=16 | full (~1500) | Rank floor — does S28's rank generalize at ~9x scale? |
| S29-C | r=64 | full (~1500) | Target configuration |
| S29-D | r=64 | 500 | Scale floor — is rank or scale the dominant factor? |

All configs use:
- Config C curriculum schedule
- Same random seed
- Same Qwen2.5-Coder-1.5B base
- **Fixed 3 epochs, no early stopping.** S28 found val loss correlates poorly with held-out benchmark performance, so val loss is logged per epoch as a diagnostic signal only — it is not a stopping criterion.
- Per-split val loss tracking (diverse val + template val) logged per epoch

Training budget: r=64 at 1500 pairs is ~30 min × 3 configs = ~1.5 hours. Surplus vs the original 6-config plan is reserved for re-training if Phase 0 surfaces harness issues or if a config needs a second run with a different seed.

**2.3 Generation sanity check per config** (half day)

For each trained adapter, run the S28 generation-inspection tool on 8 held-out tasks, 8 samples each, temp=0.8:
- Fraction generating `let rec`
- Fraction generating multi-arm `match`
- Fraction generating `have+exact`
- Fraction generating Lean-valid syntax (no C++/JS fallback)
- Fraction generating trailing comments (should stay ≤10%)

**Qualitative pass:** hand-inspect 5 `let rec` generations per config for Lean syntactic validity — not just token presence. A config that emits `let rec` keywords inside broken scaffolding is worse than a config that emits simple `exact` with valid Lean. The quantitative fraction is the screening filter; the qualitative pass is the gate.

This is a cheap sanity check before expensive benchmark runs. If a config generates 0% `let rec` (or generates them as syntactic noise under the qualitative pass), the capacity story is unchanged from S28 and benchmark probably won't change either.

**Exit criterion**: All 3 configs trained successfully. Per-config val loss curves logged. Generation sanity check produces at least one config with ≥5% `let rec` rate **and** ≥3/5 syntactically valid `let rec` samples under qualitative pass (otherwise capacity is still starved — flag for Phase 4).

### Phase 3: Evaluation (2 days)

**3.1 Benchmarks (all 3 configs)** (1 day with --patience)

For each of the 3 trained configs, run:
1. Diagnostic (18 tasks, split train/held-out)
2. Hard tier (5 tasks + new 6 held-out hard tasks = 11 tasks)
3. Core T2 (10 tasks) — regression check
4. Core 120 (sampled: V2 subset + recurrence + fundamentals) — fast regression

Rollouts: 50 for diag/hard (matches S28), 200 for core T2 sample. `--patience 100` enforced.

**Critical**: restart Pantograph daemon between each config's benchmark run (S28 lesson).

**3.2 Per-solve classification** (half day)

For each solved task across all configs, produce a row:
- Config name
- Task ID
- Tactic string (raw from solver)
- Source: `{template, lora, hybrid}`
- Classification: `{template_hit, lora_novel, false_positive}`
- Held-out vs train-split
- Rollout count at which it solved

Output: `solves_classification.csv`

**3.3 Adversarial test strengthening** (half day)

For **every solve** on diagnostic or held-out tier (regardless of source — template, LoRA, or hybrid), hand-write 3 adversarial test cases that distinguish the algorithm semantically. S28 found 3/5 initial solves were false positives from weak test cases; that wasn't a LoRA-specific failure, so template and hybrid solves get the same verification treatment.

- For `second_smallest`, add `[3, 7, 2, 5]` (distinct, GCD=1, correct=3)
- For Kadane-style, add all-negative inputs
- For majority, add 50/50 splits and boundary cases
- For index-tracking, add inputs where position matters

Re-verify every solve against the strengthened test cases. Any that fail under adversarial tests → classify as false positive, not a solve — regardless of whether it came from a template or LoRA.

**Exit criterion**: 3 benchmark result files + classification CSV + adversarial verification on every diag/held-out solve complete. Primary metric (held-out) recorded honestly per config.

### Phase 4: Analysis & Decision Gate (1 day)

**4.1 Primary metric decision tree**

Based on the best-performing config's aggregate held-out score across all 12 tasks (6 diagnostic + 6 new hard-tier):

- **Held-out 0–2/12** (capacity-starved / architecture limit)
  - Verdict: LoRA-based learning on Qwen2.5-Coder-1.5B has hit its ceiling for this task distribution
  - Next sprint: S30 explores full fine-tune OR stronger base model (Qwen2.5-Coder-7B, DeepSeek-Coder-V2-Lite)
  - OR: accept the template-based system as the final product, focus on robustness and benchmark expansion

- **Held-out 3–5/12** (scaling works, continue)
  - Verdict: hypothesis validated at scale
  - Next sprint: S30 scales to 5,000+ pairs with Config C curriculum
  - Lock in the winning (rank, scale) combination and expand training data

- **Held-out 6+/12** (hypothesis fully confirmed)
  - Verdict: diversity training solves compositional generalization
  - Next sprint: S30 productionizes the pipeline, expands benchmarks to harder task distributions
  - Investigate: does this transfer to theorem proving, not just synthesis?

**4.2 Secondary analysis**

- Does rank vs scale matter more? Compare S29-A vs S29-C (rank axis, both at full scale) and S29-C vs S29-D (scale axis, both at r=64).
- Does the r=16 rank floor generalize at ~9x data scale, or is r=64 necessary? (S29-A vs S29-C)
- Is scale or rank the dominant factor? Diagonal comparison: S29-A (r=16, full) vs S29-D (r=64, 500).
- Which pattern categories contribute most novel solves? (inspect `solves_classification.csv` by pattern origin)
- Did the generation sanity check predict benchmark results? (if yes, we have a faster iteration signal for S30)

**4.3 Harness reliability check**

- Did any config produce suspicious discontinuities (e.g., 4/18 → 1/18 between back-to-back runs)?
- Did the daemon corrupt during any benchmark?
- Did `--patience` incorrectly abort any solvable tasks? (check by re-running aborted tasks at 500 rollouts on a single config)

**4.4 Written decision document**

`phase4_analysis.md` following S28's structure:
- Headline result
- Per-config results table
- Decision (one of the three branches above)
- S30 direction with specific next steps

**Exit criterion**: Decision document committed. S30 direction chosen. Memory updated with the decision and the primary metric result.

## Success Criteria

| Metric | Baseline (S28) | Floor | Expected | Good | Surprising |
|--------|----------------|-------|----------|------|------------|
| **Held-out diagnostic (6)** | 0/6 | 0/6 | 1–2/6 | 2–4/6 | 5+/6 |
| **Held-out hard-tier new (6)** | — | 0/6 | 1–2/6 | 2–4/6 | 5+/6 |
| Diagnostic train-split (12) | 3/12 | 3/12 | 3–5/12 | 5–7/12 | 8+/12 |
| Hard tier (5) | 4/5 | 4/5 | 4–5/5 | 5/5 | 5/5 |
| Core T2 (10) | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| Core 120 regression | 107–109/120 | 104/120 | 107+/120 | 109+/120 | 112+/120 |
| **Real novel LoRA solves** | 3 | 3 | 5–8 | 8–15 | 15+ |
| False positives on novel | 0 (Config C) | ≤1 | 0–1 | 0 | 0 |
| `let rec` generation rate | 0% | 0% | 3–8% | 8–15% | 15%+ |
| Multi-tactic generation rate | ~5% (Config C) | ~5% | 10–20% | 20–30% | 30%+ |

**Primary metric is held-out generalization.** Held-out is composed of two 6-task sets: the S28 diagnostic held-out (unchanged) plus the new hard-tier held-out frozen in Phase 0.5. Decision-tree thresholds (authoritative — see Phase 4.1):
- **0–2/12** = capacity-ceiling outcome
- **3–5/12** = scaling works, continue
- **6+/12** = hypothesis fully confirmed

**No goalpost moving.** If held-out stays at 0–2/12, the sprint reports that as the answer. No patching, no weak-test-case gaming, no incremental template additions to reach an arbitrary number.

## New Files

| File | Purpose |
|------|---------|
| `lean_mcts/synth/pattern_miner.py` | Pattern-targeted library scanner (Phase 1.2) |
| `lean_mcts/synth/held_out_hard_tasks.py` | 6 new hard-tier held-out tasks |
| `lean_mcts/synth/leakage_check.py` | n-gram overlap check between training and held-out |
| `lean_mcts/benchmark/ablation_columns.py` | Per-policy ablation tracking for benchmarks |
| `lean_mcts/benchmark/adversarial_tests.py` | Strengthened test cases for novel-solve verification |
| `tests/harness_smoke.py` | Fast smoke test suite (Phase 0.3) |
| `lean_mcts/train/diverse_train_v2.jsonl` | Scaled training data (~1,500 pairs) |
| `lean_mcts/train/diverse_val_v2.jsonl` | Validation split |
| `lean_mcts/train/diverse_adapter_v2/{S29-A,S29-C,S29-D}/` | Three ablation adapters (rank floor, target, scale floor) |
| `docs/implementation/sprint29/sprint29.md` | This plan |
| `docs/implementation/sprint29/sprint29_status.md` | Status tracker |
| `docs/implementation/sprint29/phase4_analysis.md` | Decision document (end of sprint) |
| `docs/implementation/sprint29/lessons_learned.md` | Non-obvious findings |
| `lean_mcts/benchmark/results/s29_*/solves_classification.csv` | Per-solve source/novelty classification |

## Modified Files

| File | Changes |
|------|---------|
| `lean_mcts/synth/extract_lean_defs.py` | Add Array/Option/Sum/Prod/Nat/Int module lists; extend `specialize_polymorphic` to new types |
| `lean_mcts/search/mcts.py` | Add `--patience N` early-abort flag |
| `lean_mcts/benchmark/run.py` | Emit ablation columns in results JSON; require daemon restart validation |
| `lean_mcts/train/train_diverse.py` | Fix DataLoader multiprocessing leak; support rank and epoch-count CLI flags |
| `Makefile` | Add `make smoke`, `make mine-v2`, `make train-grid`, `make bench-grid` targets |

## Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Library mining yields <1,100 pairs (pattern mining underperforms) | Hypothesis under-powered again | Floor is 1,100 (still 6.7x S28). If below floor, reassess extraction before training |
| r=64 training unstable / NaN loss | Ablation grid incomplete | Start with S29-C (r=64, full) as the single "must-run" config; S29-A (r=16 rank floor) and S29-D (500-pair scale floor) are secondary and can be skipped if the target config is unstable |
| DataLoader leak can't be fixed cleanly | Cannot run multi-epoch training | Fall back to `num_workers=0` single-process training (slower but deterministic) |
| Held-out leakage not caught by n-gram check | False positive generalization claim | Phase 4.3 adversarial verification on every novel solve + manual review of held-out solves |
| Benchmark ablation columns miss real LoRA contributions | False negative on LoRA signal | Also run generation sanity check per config (Phase 2.3) as an independent signal |
| Daemon corruption during 3-config benchmark sweep | Results unreliable | Mandatory daemon restart between configs; re-run any suspicious result |
| `--patience` aborts solvable tasks | Missed solves | Phase 4.3 re-runs aborted tasks at 500 rollouts on the winning config |
| Held-out set leaks via library mining (training has definitions that match held-out algorithms) | Generalization claim invalid | Phase 1.4 leakage check + Phase 4.3 manual review; if leakage found, patch and re-run |
| Harness audit surfaces a bug invalidating S28 results | Must re-baseline | Better to know now than later; re-baseline takes 1 day with the new harness |
| Full sprint runs out of time before decision gate | No answer | Hard-freeze at Phase 3 if behind schedule — even 1/3 configs (S29-C target) with honest results beats 3/3 with rushed ones |

## Non-Goals

- **No new skeleton modules.** S27 showed templates hit a ceiling. Adding more is busywork.
- **No diagnostic augmentation.** S28 proved it contributes zero held-out transfer.
- **No architectural changes to MCTS.** Skeleton-guided search, candidate ordering, and oracle verification stay exactly as they are. This sprint is about the learned policy, not the search.
- **No base model change.** Qwen2.5-Coder-1.5B stays. A base model switch is S30+ territory if Sprint 29 hits the ceiling.
- **No self-play loop.** S23 proved self-play converges after ~27 pairs on the current distribution. Revisit only if S29 validates diversity scaling.
- **No new benchmarks.** 120 core + 18 diagnostic + 6 new hard-tier held-out = 144 tasks. That's the evaluation surface. No expansion until the primary metric has an answer.
- **No value-network training.** Revisit only if S29 confirms the learned policy contributes.
- **No chasing incremental solves through test-case weakening.** If a solve fails under adversarial tests, it's a false positive, not a solve. Period.

## Timeline (target: 14 working days)

| Phase | Days | Deliverable |
|-------|------|-------------|
| 0. Harness audit + held-out freeze | 2.5 | Smoke suite, ablation columns, `--patience` (with regression guard), S28 reproduction, 12-task held-out frozen + git-tagged |
| 1. Library mining | 6–7 | 1,350+ verified pairs, held-out leakage check, diversity audit |
| 2. Training grid | 2 | 3 trained adapters (S29-A/C/D) + per-split val loss + generation sanity + qualitative `let rec` pass |
| 3. Evaluation | 2 | Benchmark results + per-solve classification + adversarial verification on all diag/held-out solves |
| 4. Decision | 1 | phase4_analysis.md + S30 direction |
| **Total** | **13.5–14.5** | Decision on whether scaling diversity works |

## One-line summary

Scale Sprint 28's Config C curriculum 10x with LoRA rank ablation, freeze a 12-task held-out set, and answer whether diverse training generalizes or the system has hit its ceiling — no moving goalposts, no more templates, no diagnostic augmentation.
