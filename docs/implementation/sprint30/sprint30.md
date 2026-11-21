# Sprint 30: Probe Before Train — Discriminate the S29 Ceiling Cheaply

## Goal

Sprint 29 ended with a clean 0/12 on `held_out_12` and a
"Ceiling reached" verdict. S29's phase4 analysis recommended Option A
(stronger base model) as the primary S30 experiment, with Option B
(retrieval-augmented generation) as a parallel fast sanity check,
and Option C (full fine-tune) deferred.

Sprint 30 revises the *ordering* of those same options based on a
cost/value re-examination: **run the cheapest discriminating
experiments first, commit to expensive training only when the
outcome is gated.** Options A/B/C are restructured into four gated
phases that front-load probes and delay compute-heavy commitments
until the probes justify them.

The primary deliverable is an answer to three questions before any
training cycle begins:

1. **Are these tasks solvable by current-generation code LLMs at all?**
   (Phase A — zero-shot capability probe)
2. **Is the 404-pair corpus the right signal delivered the wrong way?**
   (Phase B — retrieval-augmented generation, primary experiment)
3. **Is `let rec` absent because of adapter capacity, tokenizer/format,
   or a base-model prior?** (Phase C — base-prior probe)

Only if those probes point decisively to "the base model is the
bottleneck" does S30 commit to Phase D (stronger base with LoRA or
retrieval, shape determined by Phase B's result).

## Motivation

### S29 findings that define this sprint

**Finding 1: Rank is not the lever at 404 pairs.** S29-A (r=16) and
S29-C (r=64) scored 0/12 with identical per-task verdicts. S29-C fit
the training distribution 14% better on `diverse_val_loss` and
produced *fewer* structural patterns in the sanity check (0 match
vs 2 in 125 samples). This is the signature of adapter overfitting
to the training distribution, not of an expressivity ceiling.

**Finding 2: `let rec` generation rate is 0% despite 30%+ training
coverage.** Both S29 adapters produced zero `let rec` patterns in
125 combined samples. Every hard-holdout task needs at least one of
`let rec`, match on multi-arg, or multi-step `have ... exact`. The
missing pattern has three possible causes — LoRA capacity, prompt
format, or base-model prior — and only the first is fixed by going
bigger. Phase C settles which.

**Finding 3: Training-distribution val loss is orthogonal to
held-out task solves at this scale.** S29-C's 14% val loss
improvement produced zero additional solves. The Phase 2.3
generation sanity check (125 samples in 2 min wall) predicted the
0/12 benchmark result 40× faster than the 5-hour sweep. S30 uses
the sanity check as first-pass triage rather than committing to
long sweeps on unvalidated adapters.

**Finding 4: S29's corpus engineering hit diminishing returns at
404 pairs.** Further scaling needs a converter rewrite (universe
polymorphism, dependent types, Mathlib typeclass machinery) —
multi-week engineering. S30 takes the 404-pair corpus as-is and
tests whether uncompressed delivery (Phase B) works where
compressed delivery (LoRA) failed.

### Why the re-ordering

S29's phase4 recommended A as primary and B as a parallel sanity
check. Sprint 30 swaps the cost/value order:

- **Phase A** is ~1 day of inference calls. It directly tests
  whether the held-out set is solvable by anything. If the frontier
  model scores ≤1/12 **and** Phase B-3's oracle-retrieval upper bound
  also misses, no amount of Qwen-7B training will change that, and
  Phase D is pre-emptively killed. The joint read with B-3 is
  essential because Phase A uses deliberately generic few-shot
  examples and is a lower bound on capability, not a ceiling.
- **Phase B** is 2–3 days of retrieval infrastructure plus one
  benchmark sweep. Its outcome directly reshapes Phase D: if B
  succeeds, Phase D becomes "retrieval on stronger base" instead of
  "LoRA on stronger base"; if B fails, Phase D's capacity
  hypothesis is strengthened.
- **Phase D** is 2+ weeks of training and benchmarks at 4–5×
  S29's per-rollout latency. Running it in parallel with B commits
  two weeks of compute to a hypothesis B could have answered in
  three days.

One week of cheap probes before a two-week training commitment is
the right trade given S29's experience.

## Hypothesis

S30 does not test a single hypothesis — it tests a decision tree:

> **At least one of three levers (frontier-model capability,
> retrieval-based inference, or stronger base model) moves
> `held_out_12` from 0/12 to ≥3/12. S30's job is to identify which,
> as cheaply as possible.**

The null outcome — "no lever in the project's toolbox moves the
needle; these tasks are unsolvable at current-generation model
scale" — is an acceptable sprint outcome and feeds into rescoping
the held-out set in S31.

## Phase Plan

### Phase 0 — Pre-flight (Day 0)

1. **Fix the `merge_sorted_desc` multi-arg auto-intro anomaly** so
   the hard-holdout tier exercises all 6 tasks. S29 phase4 flagged
   this (`docs/implementation/sprint29/phase4_analysis.md` §"Observed
   Anomaly"): the task terminated after 21 oracle calls instead of
   ~10K because MCTS ran out of legal moves on a homogeneous multi-arg
   signature `(xs ys : List Int)`. Start by diffing how
   `lean_mcts/search/policy.py` and the Pantograph intro path in
   `lean_mcts/env/pantograph_client.py` handle homogeneous vs
   heterogeneous arg groups (S29 lessons_learned.md:301–328 has the
   contrast with `window_sums` as the control case).
2. **Promote `sanity_check.py` to a CLI.** Extract the Phase 2.3
   generation inspection (125 samples, 8 held-out-bucket tasks ×
   8 samples at temp 0.8) into `lean_mcts/benchmark/sanity_check.py`.
   Every subsequent phase uses it as first-pass triage.
3. **Verify `make smoke` still passes** after any harness changes.

**Cost**: ≤1 day.

### Phase A — Zero-shot capability probe (Day 1)

**Goal**: establish the task ceiling before assuming the bottleneck
is LoRA-specific.

Run plain few-shot prompting on `held_out_12` with three models —
no LoRA, no MCTS, no skeleton search:

1. **Base Qwen2.5-Coder-1.5B** (current base, control)
2. **Base Qwen2.5-Coder-7B** (Phase D's candidate base)
3. **One frontier ceiling model** — default is Claude Opus 4.6
   (`claude-opus-4-6`). Any swap must be recorded in
   `sprint30_status.md` with model ID, API version, and sampling
   params; the gates below assume this specific model.

**Prompt format**: system prompt describing Lean 4 tactic synthesis
+ 3–5 hand-picked high-quality library pairs as generic few-shot
examples (same set for all tasks within a model — no retrieval yet)
+ the target goal state. Temperature 0.2, 8 samples per task for
variance.

**Important**: Phase A's few-shot set is deliberately generic. It is
a lower bound on frontier-model capability, not a ceiling. Because
its kill gate below is aggressive, Phase B-3 (oracle retrieval) must
run unconditionally as the paired calibration — it is the true
"anything we could deliver" upper bound. A Phase A frontier miss is
only decisive when B-3 on the same model family also misses.

**Scoring**: oracle-verify each generated tactic body against the
task's test cases using the same pipeline S29 uses for
`held_out_12`.

**Read-outs and gates** (all gates are read **jointly** with Phase B-3):

| Outcome                                             | Interpretation                                           | Gate                                                                                                                  |
|-----------------------------------------------------|----------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------|
| Frontier ≤1/12 **AND** B-3 ≤1/12                    | Held-out set not solvable by current-gen code LLMs even with oracle retrieval | **Kill Phase D.** S31 rescopes the benchmark. (Phase B may still run other configs for diagnostic value.)        |
| Frontier ≤1/12 but B-3 ≥3/12                        | Generic few-shot was the bottleneck — frontier is capable given the right context | Re-interpret Phase A as a lower bound; proceed on the Phase B decision tree as if A had passed.                       |
| Frontier ≥3/12, Qwen-7B ≤1/12                       | Base model matters — Phase D has capacity justification  | Proceed to Phase B, then Phase D                                                                                      |
| Qwen-7B ≈ frontier, both ≥3/12                      | LoRA may be unnecessary; pretraining is sufficient       | Proceed to Phase B; if B also ≥3/12, **end S30 early: release the zero-shot Qwen-7B config as the S30 baseline, skip Phase D entirely, and write lessons_learned.md with the pretraining-suffices verdict.** |
| Qwen-1.5B ≈ Qwen-7B                                 | Base model not the ceiling; lever is elsewhere           | Phase D weakened; Phase B remains primary                                                                             |

**Cost**: ~1 day. Pure inference calls. No training, no MCTS.

### Phase B — Retrieval-augmented generation (Days 2–4, PRIMARY)

**Goal**: test the hypothesis that **LoRA is lossy compression** by
delivering the 404-pair corpus uncompressed at inference time.

**Architecture**:
- Embed all 404 pairs on their goal type signature. Start with
  BGE-code or CodeBERT (off-the-shelf, fast). If the embedder signal
  is weak, try Qwen hidden states as a second pass.
- Build a FAISS index keyed by goal state signature.
- In the MCTS policy call, retrieve top-k similar pairs and inject
  them as few-shot examples before the target goal. Fall through to
  zero-shot when best-match similarity is below threshold.
- Base model: Qwen2.5-Coder-1.5B **without** any LoRA. Same VRAM
  budget and MCTS harness as S29.

**Ablations** (chosen for discriminative value, not completeness):

| Run  | k  | Embedder                          | Purpose                                   |
|------|----|-----------------------------------|-------------------------------------------|
| B-1  | 10 | BGE-code, cosine                  | Primary config                            |
| B-2  | 5  | BGE-code, cosine                  | Tests context crowd-out vs focused signal |
| B-3  | 10 | **Oracle retrieval** (see protocol below) | Upper bound on "if embedder were perfect" — runs unconditionally as Phase A calibration |

All three run on `held_out_12` with sanity-check triage first: skip
the full benchmark sweep if the sanity check shows zero structural
coverage for the required patterns (per Phase 2.3's methodology).

**B-3 oracle retrieval protocol** (precise because the gate logic
depends on it):

1. For each `held_out_12` task, the experimenter reads only the
   task's **goal type signature and informal description** — never
   the test cases, expected output, or reference tactic body.
2. They hand-select the 10 training pairs from `diverse_train_v2.jsonl`
   + `library_pairs.jsonl` whose **tactic-level structural shape** is
   closest to what they believe the target requires (e.g. "needs
   `let rec` over `List α`," "needs multi-step `have ... exact` with
   a cast"). Ties broken by type-signature similarity.
3. Selections are logged to `sprint/benchmark/results/b3_oracle_selections.json`
   with `{task, selected_pair_ids, rationale}` for every task, committed
   to git before the B-3 run starts. This freezes the "oracle" so the
   run is reproducible and the frontier-of-information is auditable.
4. The 10 pairs are injected into the prompt in the same format B-1
   uses. Everything downstream of prompt construction is identical
   to B-1 — same base model, same MCTS harness, same scoring.

B-3 is a deliberately optimistic upper bound. The gate logic already
accounts for this (B-3 succeeding without B-1 succeeding is read as
"embedder is the bottleneck," not "Phase D unnecessary"). The
non-peeking rule is what makes this bound meaningful rather than
trivially achievable.

**Theory tested**: S29 showed rank-64 fit training 14% better than
rank-16 and solved zero additional tasks, while neither adapter
generated `let rec` despite ~30% of training having it. That
pattern is consistent with the adapter smoothing away rare but
load-bearing tokens. Retrieval bypasses the compression entirely —
the exact `let rec` example sits verbatim in the context window,
so the base model only has to *condition* on it, not *recall* it
from weights.

**Read-outs and gates**:

| Outcome                             | Interpretation                                                            | Gate                                                                                                   |
|-------------------------------------|---------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| B-1 ≥3/12 *(trace-validated)*       | Corpus signal was real; LoRA was wrong extractor                          | **Pivot S30 to retrieval-first.** Option C killed. Phase D (if run) is retrieval on bigger base.       |
| B-1 ≤2/12, B-3 ≥3/12                | Corpus contains signal; embedder is the bottleneck                        | Iterate on retrieval (different embedder, structural hashing, type-ascription matching). No Phase D yet.|
| B-1 ≤2/12, B-3 ≤2/12                | Corpus does not contain enough signal regardless of delivery              | **Strong evidence for Phase D.** Ceiling is base-model knowledge, not training pipeline.               |

**Mandatory trace inspection before declaring B-1 ≥3/12 valid.**
Retrieval has a failure mode the LoRA pipeline did not: the model can
echo a retrieved pair near-verbatim when the retrieved pair happens
to match the target closely, and that "solve" is indistinguishable
from a genuine compositional use of retrieved context at the
benchmark-score level. For every task B-1 solves, the experimenter
must manually inspect the solution trace and classify it as:

- **compose**: model combined retrieved context with goal-specific
  reasoning (e.g. adapted a retrieved `let rec` pattern to a
  different predicate or return type)
- **echo**: model emitted a near-verbatim copy of one retrieved pair,
  with only variable renaming or argument reordering
- **trivial**: the retrieved top-1 pair already solves the task
  with zero edits — the task was effectively a lookup

The headline `B-1 ≥3/12` is only validated if at least 2 of the
solves are classified **compose**. An all-**echo**/**trivial** result
reads as "retrieval cache hit" and does not refute the S29 ceiling
hypothesis — log it as such and treat B-1 as a null. The classification
and per-task trace snippets go into
`sprint/benchmark/results/b1_trace_inspection.md`.

**Cost**: 2–3 days end-to-end. Zero training. Same MCTS rollout
budget per task as S29; expect 1.5–2× per-rollout wall time from
longer prompts, pushing the full sweep to 8–10 hours per config.

**Risks**:
- **Embedding similarity ≠ structural similarity** for Lean tactics.
  Two syntactically different tasks can share a tactic shape; two
  that look similar can need different tactics. Mitigation: B-3
  oracle retrieval upper bound.
- **Base Qwen-1.5B without LoRA may have degraded Lean syntax.**
  S28 observed C++/JS fallback on long generations. Mitigation:
  sanity check on a smoke run before committing to full sweep.
- **Context crowd-out** is not a real risk at the selected k values
  (k=10 × ~300 tokens/pair ≈ 3K of 32K context, leaving ~29K for
  system prompt + goal + generation). B-2 (k=5) ablation remains as
  a *signal-focusing* test — "does a tighter retrieval set beat a
  broader one?" — rather than a crowd-out mitigation.
- **Phase A / Phase B confound.** If Phase A's static few-shot set
  happens to match what retrieval would pick, B does not add
  information over A. Mitigation: Phase A uses representative but
  generic examples deliberately chosen to be distinct from
  retrieval-preferred ones.

### Phase C — `let rec` base-prior probe (Day 5)

**Goal**: determine whether base Qwen-1.5B-Coder can produce
`let rec` on *any* Lean prompt. The S29 observation that 0/125
samples had `let rec` despite 30% training coverage has three
possible causes and each has a different remediation:

| Cause                                        | Remediation                           |
|----------------------------------------------|---------------------------------------|
| (a) LoRA capacity insufficient               | Go bigger (Phase D)                   |
| (b) Tokenizer or prompt format issue         | Fix S29 training config before Phase D|
| (c) Base model has a strong prior against it | Stronger base (Phase D) more critical |

**Experiment**:

1. Prompt base Qwen-1.5B-Coder (no LoRA) with 20 Lean tactic prompts
   explicitly requesting recursive helpers
   (`"Write a Lean 4 tactic that uses \`let rec\` to..."`).
   Sample 8 per prompt at temp 0.8.
2. Measure `let rec` generation rate.
3. Repeat with Phase A's few-shot framing.
4. Repeat with one `let rec` library pair injected as a k=1
   retrieval example.
5. Repeat against base Qwen-7B.

**Read-outs**:

| Observation                                                    | Interpretation                                                                                           |
|----------------------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| Base 1.5B never produces `let rec` on any prompt               | Cause (c). Base prior is against it. Phase D upgrade becomes more important.                            |
| Base 1.5B produces `let rec` zero-shot but LoRA adapter never  | Cause (b). LoRA suppressed a pattern the base could produce. Investigate S29 training config.           |
| Base 1.5B produces `let rec` only with retrieved example       | Base can condition on `let rec` but won't generate it spontaneously. Strong evidence for Phase B.        |
| Base 7B produces `let rec` zero-shot but 1.5B does not         | Phase D's base-model hypothesis validated for at least this one structural pattern.                     |

**Cost**: one afternoon. No training. Can run in parallel with
Phase B if GPU budget allows. Budget breakdown: 5 sub-experiments ×
20 prompts × 8 samples = 800 generations total. On a single 5090
with 1.5B base this is ~25 min of raw inference; 7B adds ~90 min.
Classification and write-up is the bulk of the afternoon.

### Phase D — Stronger base model (Week 2+, GATED)

**Gate conditions** (all must be satisfied to commit):
- Phase A shows **either** frontier model ≥3/12 **or** B-3 ≥3/12
  (task is solvable by something at current-generation scale)
- Phase B-1 **and** B-3 **both** score ≤2/12 (retrieval alone does
  not suffice — note: B-1 ≤2/12 alone is not enough, B-3 must also
  miss, otherwise the right move is embedder iteration, not Phase D)
- Phase C confirms base-model capacity is a missing lever (1.5B
  lacks structural patterns 7B has, or base 1.5B has a strong prior
  against `let rec`)

If any gate fails, reshape:

| Gate outcome                                                    | Reshape                                                                                  |
|-----------------------------------------------------------------|------------------------------------------------------------------------------------------|
| A fails **and** B-3 ≤1/12 (even oracle retrieval doesn't solve) | S30 ends early. S31 pivots to benchmark rescoping or skeleton expansion.                 |
| A passes, B-1 ≥3/12 (trace-validated as **compose**)            | Retrieval-first pivot. Phase D optional, shaped as retrieval on Qwen-7B.                 |
| A passes, B-1 ≤2/12 **AND** B-3 ≤2/12, C passes                 | Original Phase D as S29 phase4 described — Qwen-7B + LoRA on 404 pairs.                  |
| A passes, B-1 ≤2/12 **but** B-3 ≥3/12                           | Iterate on retrieval embedder before committing to Phase D. Phase D deferred, not killed.|
| A passes, B-1 solves are all **echo**/**trivial**               | Read as null; treat gate as B-1 ≤2/12 and evaluate against the rows above.               |

**Phase D candidates** (inherited from S29 phase4 recommendation):
- **Qwen2.5-Coder-7B** — same family, ~4.6× parameters, 4-bit LoRA
  fits in the 32 GB VRAM of a 5090 with headroom.
- **DeepSeek-Coder-V2-Lite** — 16B MoE with ~2.4B activated,
  designed for code.

**Theory being tested**: *"The ceiling is in the base model, not the
adapter or the data."*

**Risks** (inherited from S29 phase4 analysis):
- **Inference latency**: MCTS policy calls 4–5× slower than 1.5B.
  Full `held_out_12` sweep grows from 5 h to 20+ h per config.
  Mitigation: `sanity_check.py` triage before every full sweep.
- **4-bit quantization quality** may hurt structured code
  generation on exactly the rare patterns (`let rec`, multi-arm
  `match`) S29 already showed poor coverage. Verify with the Phase
  C probe on the quantized variant before training.
- **Recursive capacity question**: if Qwen-7B LoRA on 404 pairs
  scores 0/12 like 1.5B did, S30 burned two weeks confirming the
  earlier negative result at larger scale. Phase A's probe is the
  guard against this.

**Cost**: 2+ weeks training + benchmarks.

## Success Criteria

### Primary metric
`held_out_12` solve count (same as S29, frozen behind
`s29-heldout-frozen`).

### Phase-specific targets

| Phase                | Target                                    | Null / downside                                                         |
|----------------------|-------------------------------------------|-------------------------------------------------------------------------|
| A (frontier)         | ≥3/12                                     | ≤1/12 → benchmark rescoping *only if* B-3 also ≤1/12                    |
| A (Qwen-7B)          | ≥2/12                                     | ≤1/12 → Phase D weakened (1 solve is noise, not signal)                 |
| B-1 (k=10)           | ≥3/12 *with ≥2 solves trace-classified as **compose*** | 0–2/12, or ≥3 solves all **echo**/**trivial** → Phase D strengthened |
| B-3 (oracle)         | ≥5/12                                     | ≤2/12 → corpus signal insufficient                                      |
| C (`let rec` rate)   | ≥5% on 160 samples                        | 0% → base prior against `let rec`                                       |
| D                    | gate-dependent                            | gate-dependent                                                          |

### End-of-sprint deliverables
- Phase A: 3 zero-shot result JSONs (1.5B, 7B, frontier) + analysis
- Phase B: ≥3 RAG configs benchmarked, result JSONs + per-task
  solve traces
- Phase C: base-prior probe report (rate tables + interpretation)
- Phase D (if run): benchmark result JSON + analysis + decision
- `sprint30_status.md` updated phase-by-phase
- `lessons_learned.md` for non-obvious findings
- `phase4_analysis.md` with the S30 primary metric verdict and
  S31 direction

## Data Source

S29's 404-pair corpus (`lean_mcts/train/diverse_train_v2.jsonl` +
`diverse_val_v2.jsonl`) is adequate input for S30. Two-tier
validated, leakage-checked against held-out set, stratified 80/20
split. No corpus work in S30.

## Held-out Discipline

`held_out_12` is frozen behind the `s29-heldout-frozen` git tag. No
additions, variants, or overlap with training pairs. S30 measures
the same 12 tasks as S29's primary metric.

## Out of Scope for S30

- **Corpus scaling beyond 404 pairs** — multi-week converter
  rewrite; defer until Phases A–D clarify whether the ceiling is
  data or model.
- **Full fine-tune of Qwen-1.5B (S29 Option C)** — S29 rank evidence
  is against it. Revisit only if all S30 phases fail.
- **Symbolic skeleton expansion** — cheaper reach than neural work
  but orthogonal to S30's question. Scoped for S31 if Phase B
  produces partial results.
- **Productionizing / expanding benchmarks** — not earned until
  S30 shows nonzero held-out solves.

## References

- S29 plan: `docs/implementation/sprint29/sprint29.md`
- S29 status: `docs/implementation/sprint29/sprint29_status.md`
- S29 phase4 analysis (Options A/B/C source): `docs/implementation/sprint29/phase4_analysis.md`
- S29 lessons: `docs/implementation/sprint29/lessons_learned.md`
- Held-out set: `lean_mcts/env/FormalNav/FormalNav/TasksDiagnostic.lean`, `TasksS29Heldout.lean`
- Git tag: `s29-heldout-frozen`
