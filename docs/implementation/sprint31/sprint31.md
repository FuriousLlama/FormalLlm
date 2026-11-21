# Sprint 31 — Interlude: Project Retrospective & Direction Assessment

> **Correction (2026-11-21).** An audit and re-run found that conclusions in this document do not hold as written: 107/120 is 97/120 on re-verification, the Qwen probes withheld the task description (Qwen-7B solves 4/12 with it), and the LoRA suppression finding is not supported. See [`docs/WRITEUP.md`](../../WRITEUP.md) for the corrected results. The text below is kept as the original record.

**Status:** Documentation-only sprint. No code changes, no training, no
benchmarks. The deliverable is this document.

**Why an interlude:** S30 reached a clean diagnostic verdict ("Phase D
as originally designed is no longer justified") without yielding a
mechanical next step. Before opening S32 with a new training cycle,
re-read every prior sprint and assess the project as a whole — what
the research actually shows, where the ceilings are, what is
publishable, and whether the original thesis is still the right one
to pursue.

**Method:** 12 parallel read-only sub-agents, each summarizing 1–3
sprints from `docs/implementation/sprint{0..30}/` plus the foundational
planning docs (`ast_pipeline_roadmap.md`, `building_the_navigator.md`,
`project_assessment.md`). No new measurements were taken; everything
below is grounded in the existing sprint record.

---

## 1. The arc, in one page

| Phase | Sprints | What it established |
|---|---|---|
| **Loop closure** | S0–S2 | End-to-end pipeline on Haskell GHC Core: extract → train → constrained decode → execute → RL. 485K-param transformer, 22-task benchmark, T1 22/22 by S1. Lesson: data distribution matters as much as data volume (S2: 87% Hackage + 13% handwritten produced 0/22). |
| **Representation evolution** | S3–S5 | Closed terms (lambda lifting) → de Bruijn indices → hybrid de Bruijn + type-grouped context. Each change was load-bearing: ablating type-grouped context collapses 22/38 → 0/38 (S5 run C). T2 compositional stuck at 1–3/10. |
| **Composition wall** | S6–S10 | Confirmed data scale alone doesn't fix composition. Tried named global references (S7: T2 0→3/10), USES section / chain-of-thought (S10), pattern-aligned context (S9). Net: +2–4 T2 tasks across 5 sprints. Bottleneck identified as context selection + wiring, not capacity. |
| **Lean + MCTS pivot** | S11 | Abandoned Haskell GHC Core. Built LeanDojo + MCTS + oracle infrastructure. FixedPolicy 10/10 on T2-equivalent tasks — validates infrastructure, not generalization. |
| **Domain matching** | S12–S14 | Proof tactics (Mathlib) → 0/10 synthesis. Self-play on synthesis-specific data → 10/10 T2. ExpandedFixed (650 enumerated expressions) introduced. Hard tasks blocked by REPL crashes. |
| **Infrastructure + generative LoRA** | S15–S17 | Pantograph replaces DirectDojo. Parallel oracle (3.3× speedup). GoalConditioned LoRA + ExpandedFixed safety net hybrid → V2 19/20, Hard 4/5. Recurrence patterns added. |
| **Daemon + multi-step** | S18–S20 | Persistent multi-worker Pantograph daemon (6.1× speedup). Self-play converges round 2. Multi-step product decomposition via `constructor`. |
| **Scaling diminishing returns** | S21–S23 | Recurrence skeletons (6/6), compositional search engine (S22: 7/8 novel at depth-2), massive synthetic data scaling (S23: 8.5K → 22K pairs). S23 regressed: 55/62 → 51/62. Data dilution worse than data scarcity. |
| **Skeleton-guided MCTS peak** | S24–S26 | Structural skeletons (product, recursion, foldl, two-pass) reach **107/120** with zero retraining. The actual technical contribution of the project. |
| **Generalization diagnostic** | S27 | 18 held-out diagnostic tasks: **3/18**, all template-or-library matches; LoRA contributes 0 over ExpandedFixed across all 138 tasks. The system is "pure template lookup." |
| **Diverse training attempt** | S28 | Trained LoRA on real Lean library code (164 → 209 pairs). Config C curriculum produces 3 real novel solves on hard tier. Generalization to held-out 0/6. |
| **Ceiling reached** | S29 | Scaled to 404 pairs, r=64 LoRA. `held_out_12` = **0/12** for both S29-A and S29-C. Primary metric null. Decision gate fires "Ceiling reached." |
| **Probe before train** | S30 | Cheap diagnostic probes refute the planned Phase D. Frontier Claude 12/12, Gemma 4 26B Q4 + prompt eng 10/12, Qwen-1.5B/7B 0/12, retrieval 0/12. LoRA *suppressed* `let_rec` (base 1.5B produces it 78% bare; LoRA → 0%). MCTS adds zero signal on single-step proofs. |

---

## 2. What the research actually shows

### Validated positive results

- **Closed end-to-end pipeline** for typed program synthesis at ~485K
  params (S0–S5).
- **Representation matters more than scale**: hybrid de Bruijn +
  type-grouped context are load-bearing (S3–S5 ablations).
- **Skeleton-guided MCTS** (S24–S26): deterministic structural
  decomposition (product, recursion, foldl, two-pass) + oracle
  verification + template enumeration reaches **107/120** with zero
  retraining. The single most important technical artifact.
- **Pantograph daemon + parallel oracle**: 6.1× wall-time speedup,
  stable persistent Lean (S15, S18).
- **Frozen held-out benchmark `held_out_12`** is well-calibrated:
  Claude Opus 12/12, Gemma 4 26B Q4 + prompt eng 10/12, Qwen 1.5B/7B
  0/12. The benchmark discriminates between capability classes.

### Negative / mechanistic findings (also valuable)

- **Template-lookup, not synthesis** (S27): with no template match,
  the LoRA contributes 0 over ExpandedFixed. The "learning" is
  enumeration + verification, not learned compositional reasoning.
- **Compositional generalization is unreachable** with LoRA on
  Qwen-1.5B at 400-pair scale. S29-A r=16, S29-C r=64, both 0/12.
  Hitting the floor through three carefully gated configs is itself
  informative.
- **LoRA capability suppression** (S30 Phase C): base 1.5B produces
  `let rec` 78% of the time when asked; LoRA training drove that to
  0%. The S29 0/125 absence was suppression, not capacity. Qwen-7B
  bare is *worse* than 1.5B bare (44% vs 78%) — refuting the
  "go bigger" assumption for this pattern.
- **MCTS adds zero signal on single-step proofs** (S30 functional
  machine): every successful MCTS solve fires at `rollouts_used=1`;
  every failure has `tree_size=1, depth=0`. The harness's
  distinctive dimension never engages on this benchmark.
- **Open-weight + prompt engineering closes most of the gap**
  (S30 Phase A supplement): Gemma 4 26B Q4 reaches 10/12 zero-shot
  with three rounds of Lean-4-specific prompt hints. No training.

### What the original thesis predicted vs. what happened

The 2026-02-21 foundational plan and the 2026-03-06 project assessment
predicted that:

1. A small transformer would learn meaningful structural patterns
   from typed AST tokens. ✅ (verified at T1/T3, S5)
2. Type-constrained decoding would produce 100% type-checking
   outputs. ✅ (S3 onward)
3. Compositional generalization (T2) would follow from sufficient
   data + correct representation. ❌ (S6–S10 falsified the data
   side; S27–S29 falsified the small-LoRA side at higher scales)
4. RL from execution feedback would yield ≥2× pass@k gains. ⚠️
   (worked for narrow recall tasks; broken for context-dependent
   models — see S3/S4 assessment)
5. Pivot to Lean 4 + MCTS + 1–7B parameters would outperform frontier
   LLMs on formally-specified synthesis. ❌ (S30 Phase A: Qwen 1.5B
   and 7B both 0/12; Claude Opus 12/12)

The thesis-as-stated is falsified. The thesis-as-revised — "structured
search + oracle verification beats learned generation at this scale" —
is supported by the S24–S26 result (107/120 with zero retraining).

---

## 3. Where the ceiling actually lives

The S30 probes pinpoint the bottleneck more precisely than the
"ceiling reached" framing from S29 suggested.

**It is not:**
- Template coverage. Skeleton+ExpandedFixed already produces correct
  template shapes for held-out tasks (S30 Phase B-3 traces show
  `match xs, ys with | ...` for `merge_sorted_desc`, multi-arg
  `let rec` for `unfold_n`).
- Search depth. `held_out_12` is single-tactic; MCTS dimension never
  engages.
- LoRA rank or curriculum order. S29-A and S29-C produce identical
  0/12 despite 14% diverse_val_loss gap.
- Data scarcity at the corpus level. Retrieval-augmented Qwen-1.5B
  with oracle-picked hand-selections (B-3) is still 0/12.

**It is:**
- **Semantic body synthesis under a template.** Frontier models
  produce correct recursion logic inside the right template shape.
  Qwen-1.5B and 7B produce syntactically-correct tactics with
  semantically-wrong bodies. The gap is reasoning about what the
  recursion should compute, not what shape it should take.
- **Base-model capacity for this specific reasoning task.** Going
  1.5B → 7B in the same family does not close it. Going 1.5B → 26B
  with prompt hints (Gemma 4) gets 10/12. Going to a frontier model
  gets 12/12.

This is a more useful ceiling diagnosis than "more data, bigger LoRA,
deeper search" because it points at a specific axis (base-model
semantic reasoning) that the project's compute budget cannot move on
the Qwen family.

---

## 4. What is publishable

Three candidate writeups, in order of novelty:

| # | Contribution | Evidence | Plausible venue |
|---|---|---|---|
| 1 | **Skeleton-guided MCTS for typed program synthesis** | S24–S26: 55 → 107/120 with zero retraining over three sprints. Four skeleton types (product, recursion, foldl, two-pass). Clean ablations vs FixedPolicy and vs LoRA baseline. | Code-generation / PL workshop, NeurIPS code track, or LangSec. The most directly publishable result. Needs cleaner write-up of *why* skeleton enumeration beats learning at this scale. |
| 2 | **LoRA capability suppression: when fine-tuning erases patterns the base already produces** | S30 Phase C: bare 1.5B emits `let rec` 78%, LoRA-tuned 1.5B emits it 0%, in matched prompts. Cross-checks against 7B base (44%). | Short mechanistic-interp / fine-tuning paper. NeurIPS/ICLR short, or a TMLR note. Most novel finding in the project. |
| 3 | **Diagnostic methodology: separating template lookup from synthesis** | S27 diagnostic design (16 wiring-gap failures classified), S29 frozen held-out, S30 capability gate. Establishes a methodology for telling apart "the system has solved this" from "the lookup table covers this." | Benchmarks/methods workshop. Counter-argument to optimistic pass@k reporting. |

What is *not* publishable as a standalone story:
- The S0–S10 small-transformer GHC Core experiments — solid
  engineering, no novel architectural claim that survives the Lean
  pivot.
- The LoRA-policy-for-Lean-tactics result on its own — overlaps with
  ReProver and Lean-style work, and the policy turned out to be dead
  weight.
- The "scale diverse training data" arc (S28–S29) — useful in service
  of (3) above, not a standalone contribution.

---

## 5. Direction options

Roughly ordered by cost / commitment:

**α — Stop and write up.** Convert S24–S26 into paper #1, S30 Phase C
into paper #2, S27/S29/S30 methodology into paper #3. Treat the
codebase as a finished research artifact. The project has reached a
clean diagnostic stopping point.

**β — Re-scope the benchmark, then resume.** `held_out_12` is
single-tactic; the MCTS+skeleton harness's distinctive value is on
multi-step compositional tasks. Build a held-out *multistep* tier
(target: 10–20 tasks needing 2–4 tactic sequences). Re-run the S30
phase machine on it. This actually tests what the project built.

**γ — Change the base, keep the harness.** Phase D' on Gemma 4 26B Q4
or DeepSeek-V2-16B MoE instead of Qwen-7B. The 10/12 zero-shot from
a 25.8B Q4 open-weight model with prompt hints is genuinely
surprising and is the strongest signal for a "right base + LoRA" run.
~1–2 weeks. Risk: training a 26B Q4 adapter may exceed the
laptop+single-GPU budget the project has held to since S0.

**δ — Frontier-in-the-loop synthesis.** Plug Claude Opus into the
existing MCTS + oracle + skeleton harness as the policy. Production
system; research story shifts from "small models do formal synthesis"
to "structured search amplifies frontier models" — a different paper
than the original thesis, but a defensible one.

**ε — Mechanism investigation.** Diagnose and fix the LoRA `let_rec`
suppression mechanism (S30 Phase C). High diagnostic value, low
expected metric improvement without a bigger base.

**ζ — Stop.** The original thesis is falsified; the engineering
contribution is captured by α's writeups; further sprints may
produce diminishing returns.

### Recommendation

**α + (β or γ), in that order.** The publishable artifacts already
exist; capture them before further iteration risks blurring the
contribution. Then, if the project continues, β is the lowest-cost
test of whether the harness has genuine value on its native problem
class, and γ is the cheapest test of whether the bottleneck is
specifically the Qwen base.

Avoid: another scaling cycle on Qwen-1.5B (refuted by S29) or another
prompt-engineering round on `held_out_12` without expanding the
benchmark (Gemma 4 10/12 has already established what zero-training
prompt iteration can reach there).

---

## 6. Overall impression

This is one of the most carefully instrumented research programs the
codebase has accumulated evidence of: frozen held-outs, tiered
benchmarks, ablation columns wired into the harness, an explicit
decision-gate framing, willingness to declare its own primary metric
null (S29 0/12), and a "probe before train" sprint (S30) that is
essentially exemplary research practice. Every pivot in the sprint
record is traceable to evidence from the prior sprint.

The thesis as originally framed has hit a hard ceiling and will not
move with more of the same. But the project has produced (a) a real
reusable artifact in skeleton-guided MCTS + the Pantograph daemon
harness, (b) at least one publishable mechanistic finding in LoRA
capability suppression, and (c) a precise diagnosis of where the
remaining bottleneck lives (semantic body synthesis under a known
template shape, not template coverage or search depth).

Continuing without acknowledging the diagnosis would burn cycles. The
honest move is to write up what is done, then decide whether to
restart under a revised thesis or to close the project with the
contributions banked.

---

## 7. Decision required from sprint owner

This document does not commit S32's direction. The owner should
choose one of α / β / γ / δ / ε / ζ above (or a documented
combination) and open S32 with the chosen framing. Until that
decision is recorded, the project is on a documented pause as of
this sprint.
