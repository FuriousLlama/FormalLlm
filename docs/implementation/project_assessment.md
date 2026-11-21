# FormalLlm Project Assessment — 10 Sprints in Review

**Date:** 2026-03-06
**Scope:** Full review of Sprints 0-9, theoretical foundations, and strategic outlook
**Context:** Solo research project, 10 sprints completed, laptop + 1 GPU (RTX 5090)

---

## Project Vision

FormalLlm aims to build a **type-directed program synthesis system** — a small transformer that generates formally-correct Haskell functions from type signatures, verified by execution via `runhaskell`. The longer-term vision (documented in `building_the_navigator.md`) targets Lean 4 with MCTS + RL at scale; the current sprints serve as a proof-of-concept on a constrained GHC Core IR.

**Infrastructure**: Solo developer, single GPU (RTX 5090), ~485K parameter transformer, Python + PyTorch. No Docker, no cloud.

---

## Sprint-by-Sprint Trajectory

### Sprint 0 — End-to-End Proof of Loop

**Goal**: Wire up the full pipeline: extract → normalize → train → constrained decode → benchmark → RL.
**Result**: Pipeline closed. 73 hand-written functions, 22 benchmark tasks, first RL loop running.
**Verdict**: Complete success. Validated that the approach is mechanically feasible.

### Sprint 1 — From Memorization to Generalization

**Goal**: Expand corpus (73→162 functions), add augmentation, test novel tasks.
**Result**: 626 sequences, 73% pass rate on 22 tasks (pre-RL), val loss 1.59 nats. First evidence of augmentation diminishing returns (eta-expansion, let-reorder dropped).
**Verdict**: Incremental progress. Raised important questions about memorization vs. learning.

### Sprint 2 — Scale & Clean

**Goal**: Clean IR (remove GHC internals), batch Hackage extraction.
**Result**: 1,487 Hackage functions, clean.py removes I#/typeclass dicts/$fNumInt. Val loss 1.79. Discovered Arch Linux dynamic-linking constraint (critical for all future work).
**Verdict**: Data infrastructure win. Hackage pipeline is a durable asset. But val loss worsened — more data didn't improve generalization.

### Sprint 3 — Closed Terms (Lambda Lifting)

**Goal**: Eliminate open terms via lambda lifting; shrink vocabulary.
**Result**: Vocabulary 597→~140 tokens. Benchmark 22/22 at 99.5% pass rate. But val/train ratio = **14.07x** — pure memorization. ML architect assessment correctly identified this as "AlexNet on MNIST."
**Verdict**: Pivotal representation change. Lambda lifting is a genuine insight. But the 22/22 result was misleading — the benchmark was too easy and the model too large.

### Sprint 4 — Honest Generalization (De Bruijn + Tiered Benchmark)

**Goal**: De Bruijn indices, 38-task tiered benchmark, right-sized model (~500K params).
**Result**: 4,438 functions, 120-token vocab, val/train=1.56x (genuine generalization at last). But only 2/38 tasks pass — name erasure + random context = catastrophic. The `model_learning_analysis.md` is the most honest diagnostic in the entire project.
**Verdict**: The hardest sprint. Achieved the val/train target (<2x) but exposed that the model learned a **structural grammar**, not function generation. A necessary reckoning.

### Sprint 5 — Type-Directed Conditioning

**Goal**: Add type signatures as conditioning, structured context training.
**Result**: Two sub-sprints. 5a (type-only) failed (7/22 T1). 5b (name+type: `DEF name TY type BODY`) recovered to 17/22 T1 pre-RL, 22/22 post-RL. Type-grouped context is **load-bearing** — without it, 0/38 (complete collapse).
**Verdict**: The breakthrough sprint. Demonstrated that structured context is the primary regularizer. But T2 stuck at 1/10 — composition remains unsolved.

### Sprint 6 — Compositional Training Data

**Goal**: Add 80 hand-written composition functions to teach multi-step patterns.
**Result**: T2 0/10 (worse than Sprint 5's 1/10 post-RL). The hypothesis "more composition examples → composition ability" was **falsified**.
**Verdict**: Important negative result. Proved that the problem is not data coverage but **pattern transfer**. The model sees compositions in training but cannot reproduce them in novel contexts.

### Sprint 7 — Named Global References (Hybrid de Bruijn)

**Goal**: Replace anonymous lambda-lifted deps with named function references.
**Result**: T2 0→2/10 pre-RL (take_positives, drop_negatives). At k=200 t=0.6, T2 reaches 3/10 (minimum bar met). Constrained decode batching + mask caching = 10x speedup.
**Verdict**: The best representation change since Sprint 3. Named refs make composition structure explicit. k=200 t=0.6 adopted as canonical eval config. RL can amplify 1/200 → 200/200.

### Sprint 8 — Diagnostic-First (Unlocking Hard-Zeros)

**Goal**: Diagnose and fix 8 hard-zero tasks.
**Result**: 28/38 total (+1 vs Sprint 7). Found and fixed critical constrained decoder bug (was validating prompt, not target). 2 new tasks unlocked (maybe_map, accum_sum). But map_square regressed after RL.
**Verdict**: Diagnostic methodology (dump-candidates) is a permanent asset. Constrained decoder fix was important. But diminishing returns — each new task costs more effort.

### Sprint 9 — Pattern-Aligned Context Selection

**Goal**: Teach pattern transfer via training-time context alignment.
**Result**: T3 solved completely (6/6). apply_twice and maybe_with_default unlocked via targeted data additions. But T2 remains marginal (0-3/10 per run), RL is broken for context-dependent models, and non-determinism is massive (T1 varies 17-22 per run).
**Verdict**: T3 complete is a real win. But the approach has reached its ceiling.

---

## Quantitative Progress Summary

| Metric | Sprint 0 | Sprint 3 | Sprint 4 | Sprint 5b | Sprint 7 | Sprint 8 | Sprint 9 |
|--------|----------|----------|----------|-----------|----------|----------|----------|
| Corpus size | 73 | 626 | 4,438 | 4,438 | 4,518 | 4,524 | 5,228 |
| Vocab | ~50 | ~140 | 120 | 352 | 432 | 432 | 432 |
| Model params | ~3.6M | ~3.6M | ~485K | ~474K | ~485K | ~485K | ~485K |
| Val/train | — | 14.07x | 1.56x | 1.04x | 1.08x | 1.06x | 1.08x |
| T1 (pass@10→200) | — | 22/22 | 2/22 | 17→22/22 | 22/22 | 22/22 | 19-20/22 |
| T2 | — | — | 0/10 | 1/10 | 3/10 | 2/10 | 0-3/10 |
| T3 | — | — | 0/6 | 2/6 | 3/6 | 4/6 | **6/6** |
| Total | — | 22/22 | 2/38 | 25/38 | 27/38 | 28/38 | 25-29/38 |

---

## Key Achievements

### 1. Complete End-to-End Pipeline

Extract → normalize → augment → tokenize → train → constrained decode → benchmark → RL. Few research projects close this loop this early. The `runhaskell` execution oracle provides ground-truth verification that loss metrics cannot.

### 2. Representation Evolution

The progression from raw GHC Core to the current hybrid de Bruijn IR is the project's primary research contribution:

```
Sprint 0: Raw GHC Core (I#, $fNumInt, typeclass dicts)
Sprint 2: Clean IR (GHC artifacts removed)
Sprint 3: Lambda lifting (closed terms, <1% <unk>)
Sprint 4: De Bruijn indices (canonical variable binding, 120-token vocab)
Sprint 5: Type-conditioned format (DEF name TY type BODY)
Sprint 7: Named global refs (hybrid de Bruijn — indices for locals, names for known functions)
```

Each step produced measurable impact. The journey from 597-token open vocabulary to 432-token hybrid de Bruijn is a genuine research artifact.

### 3. Honest Evaluation Framework

38-task tiered benchmark with execution-grounded verification:
- **Tier 1** (22 tasks): Regression — functions the model has seen patterns for
- **Tier 2** (10 tasks): Compositional — require chaining 2-3 operations in novel combinations
- **Tier 3** (6 tasks): Structural — higher-order, tuple, Maybe operations

The tiered structure cleanly separates recall from generalization.

### 4. Type-Grouped Context as Primary Regularizer

The discovery that structured context selection is load-bearing (18/38 → 0/38 without it) is a meaningful finding. Without type-grouped context, val/train ratio jumps from 1.04x to 2.78x, parse failure rate exceeds 80%, and the model collapses entirely.

### 5. Diagnostic Methodology

`--dump-candidates`, per-candidate classification (parse error / compile error / wrong output), failure mode analysis. The Sprint 4 `model_learning_analysis.md` and Sprint 8 `phase1_diagnostics.md` are exemplary documents that prevented the project from chasing false signals.

### 6. T3 Solved Completely

All 6 structural tasks pass (6/6), including apply_twice (1% rate) and maybe_with_default (10% rate), unlocked by targeted data additions in Sprint 9. This validates that representation + targeted data can solve structural generalization tasks.

---

## Structural Problems & Concerns

### 1. T2 Composition Is Unsolved — And May Be Architecturally Intractable

T2 has bounced between 0-3/10 across five sprints (5-9) despite:
- 80 hand-written compositions (Sprint 6)
- Named global references (Sprint 7)
- Constrained decoder fix (Sprint 8)
- Pattern-aligned training (Sprint 9)

The model can reproduce **single-step** patterns (T1, T3) but consistently fails at **multi-step composition** (filter+fold, map+sort, etc.). This suggests a fundamental limitation: a ~485K parameter autoregressive transformer with 432-token vocabulary may lack the capacity or architecture to learn compositional generalization from ~5K training examples.

### 2. Massive Non-Determinism Undermines All Results

Sprint 9 revealed that identical checkpoints produce wildly different benchmark scores:
- T1: 17-22/22 per run
- T2: 2-5/10 per run
- fNot: 0/200 on one run, 183/200 on the next

No single benchmark run is reliable. Results reported across sprints as deltas (+1, -1) are often within noise. The project needs statistical evaluation (3+ runs, report mean +/- std) to make credible claims.

### 3. RL Is Broken

RL worked in Sprints 5-7 (amplifying weak signals from 1/200 to 200/200) but is now incompatible with the context-dependent model:
- Zero-shot RL degrades T1 (22→11-12)
- Few-shot RL adds too much variance (context randomization dominates the signal)
- Sprint 9's decision to abandon RL and use base checkpoints is correct but means a key pipeline component is non-functional

### 4. Diminishing Returns Per Sprint

| Sprint | Marginal tasks gained | Effort level |
|--------|-----------------------|-------------|
| 5 | +23 (from 2 to 25) | High |
| 6 | -1 (25 to 24) | Medium |
| 7 | +2 (24 to 27) | Medium |
| 8 | +1 (27 to 28) | High |
| 9 | +0 to +1 (28 to 25-29) | High |

The project is on a **diminishing returns curve**. Each new task costs more engineering effort, and the gains are increasingly lost in noise.

### 5. The Gap Between Prototype and Vision

The `building_the_navigator.md` vision targets Lean 4 + MCTS + 1-7B parameters + Mathlib. The current system is a 485K parameter model generating 4-30 token GHC Core sequences. The gap is large:

| Dimension | Current | Vision |
|-----------|---------|--------|
| Scale | 485K params | 1-7B params (1000-14000x) |
| Language | GHC Core IR | Lean 4 tactic mode |
| Search | Greedy/temperature sampling | MCTS |
| Training | Rejection sampling | PPO/self-play |
| Verification | runhaskell execution | Lean type checker + proofs |
| Catalog | None | Growing capability registry |

---

## What the Project Has Proven

1. **Representation design matters more than model scale** for small-data regimes. Lambda lifting (Sprint 3), de Bruijn indices (Sprint 4), and named global refs (Sprint 7) each produced larger benchmark gains than any hyperparameter sweep.

2. **Structured context selection is essential, not optional**, for few-shot generation. Type-grouped context is the primary regularizer preventing overfitting.

3. **Execution-grounded evaluation provides honest signal** that loss metrics don't. Val loss improvements do not correlate with benchmark improvements (Sprint 2-3 demonstrated this clearly).

4. **A single developer can build and iterate a complete synthesis pipeline.** The sprint methodology — hypothesis-driven, with honest status reports and lessons learned — kept the project productive across 10 iterations.

5. **Negative results are as valuable as positive ones.** Sprint 6 falsified "more composition data → composition ability." Sprint 9 falsified "pattern-aligned training → reliable composition transfer." These findings constrain the solution space for future work.

## What Remains Unproven

1. That this architecture can learn **compositional generalization** (T2 tasks requiring multi-step function chaining).

2. That the approach **scales** — whether larger models, more data, or harder tasks would unlock composition.

3. That **RL can work with context-dependent models** trained on few-shot prompts.

4. That the GHC Core prototype **validates the Lean 4 vision** — the gap between the two systems is large enough that lessons may not transfer directly.

---

## Lessons Learned (Cross-Sprint Patterns)

### On Representation

- Every representation change that made the IR more canonical (lambda lifting, de Bruijn, named refs) improved results. The instinct to canonicalize is consistently correct.
- Representation changes that erase information (name erasure in Sprint 4, type-only conditioning in Sprint 5a) consistently fail. The model needs identity signals.
- The hybrid de Bruijn approach (indices for locals, names for globals) is the best compromise found. Pure de Bruijn is too anonymous; full naming is too sparse.

### On Training

- Val/train ratio is the most reliable health metric. Below 1.1x indicates genuine generalization; above 2x indicates memorization.
- Type-grouped context selection is load-bearing. Random context produces 2.78x overfit ratio and 0/38 benchmark.
- Few-shot training with structured context creates a dependency: the model requires context at inference time and cannot function without it.

### On Evaluation

- Pass@k at temperature sampling is fundamentally noisy. Must run 3+ times and report ranges.
- k=200 reveals model capability that k=10 misses. Tasks with 1/200 baseline pass rate can be amplified by RL; tasks with 0/200 cannot.
- Benchmark tasks should be evaluated by tier, not aggregated. T1/T2/T3 measure fundamentally different capabilities.

### On RL

- RL requires a minimum baseline pass rate to function. 0/200 → RL provides nothing. 1/200 → RL can amplify to near-certainty.
- RL training distribution must match eval distribution. If the model uses few-shot context, RL must too.
- Zero-shot RL destroys models that depend on context — it optimizes for a distribution the model was never trained on.

### On Infrastructure

- Arch Linux dynamic linking (cabal config: `shared: True`, `executable-dynamic: True`) is critical and non-obvious. Without it, ~97 package builds fail.
- Running all 38 tasks at k=200 in a single process causes segfaults. Run by tier.
- Makefile dependency chains must be complete — missing deps (tokenizer → augment) cause silent stale-data bugs.

---

## Strategic Options

### Option A — Scale Within GHC Core

10x the corpus (50K functions), 10x the model (5M params), and test whether compositional generalization emerges at scale. This is the Chinchilla hypothesis applied to program synthesis: perhaps composition requires a critical mass of data and capacity that the current system hasn't reached.

**Pros**: Builds on existing infrastructure; directly tests the scaling hypothesis.
**Cons**: May hit the same ceiling at larger scale; doesn't advance toward the Lean 4 vision.

### Option B — Pivot to Lean 4

Take the lessons learned (representation design, structured context, execution verification) and rebuild on Lean 4 with LeanDojo, targeting the `building_the_navigator.md` vision. The current prototype validated the approach; the next system should operate at the target scale.

**Pros**: Aligns with the long-term vision; Lean's type system provides richer verification; Mathlib provides 170K+ training examples; joins an active research community (AlphaProof, DeepSeek-Prover-V2).
**Cons**: Requires rebuilding the entire pipeline; Lean ecosystem is less mature for program synthesis (vs. proof synthesis); significant ramp-up time.

### Option C — Write It Up

The sprint progression, representation evolution, and diagnostic methodology constitute a publishable research narrative. The negative results (Sprint 6's falsification of the "more data → composition" hypothesis, Sprint 9's RL incompatibility) are as valuable as the positive ones.

**Pros**: Captures value from completed work; contributes to the research community; creates a foundation document for future work regardless of direction.
**Cons**: Doesn't advance the technical capability.

These options are not mutually exclusive. Option C can proceed in parallel with either A or B.

---

## Conclusion

FormalLlm has accomplished what it set out to do in Sprint 0: prove that the loop from source extraction to trained model to verified program generation can close. Over 10 sprints, it has systematically explored the design space of representation, conditioning, context selection, and training methodology for small-scale program synthesis.

The project's greatest strength is its intellectual honesty — every sprint status document accurately reports failures alongside successes. The diagnostic methodology (dump-candidates, failure classification, ablation studies) prevented the project from chasing false signals and produced genuine negative results that constrain the solution space.

The current system reliably solves 25-29 of 38 tasks (T1: 19-22/22, T2: 0-3/10, T3: 6/6). T1 and T3 are effectively solved. T2 compositional generalization — the ability to chain multiple functions in novel combinations — remains the open problem. Whether this requires more scale, a different architecture, or a fundamentally different approach is the central question for future work.
