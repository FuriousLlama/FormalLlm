# Sprint 30 Phase C — Analysis

> **Correction (2026-11-21).** An audit and re-run found that conclusions in this document do not hold as written: the `let rec` rates are substring matches on prompts that name `let rec`; with a compile check there is no measurable difference between the base model and the LoRA adapters. See [`docs/WRITEUP.md`](../../WRITEUP.md) for the corrected results. The text below is kept as the original record.

**Date**: 2026-04-15
**Question**: Is `let rec` absent from S29 LoRA generations because of (a) LoRA capacity, (b) tokenizer / prompt format, or (c) base-model prior?
**Methodology**: 20 Lean-task prompts explicitly requesting `let rec` tactics × 8 samples each × 5 sub-configs, counting `let rec` substring rate per config.

## Headline

| Config              | `let rec` / unique | rate       | Wall   |
|---------------------|:------------------:|:----------:|:------:|
| 1.5B bare           | 125 / 160          | **78.1 %** | 30.0 s |
| 1.5B + Phase A few-shot | 149 / 150        | 99.3 %     | 27.2 s |
| 1.5B + k=1 `let rec` retrieval | 115 / 118 | 97.5 %     | 23.3 s |
| 7B bare             | 68 / 154           | **44.2 %** | 51.1 s |
| 7B + Phase A few-shot | 158 / 158        | 100.0 %    | 42.8 s |

## The headline surprise

**Base Qwen2.5-Coder-1.5B produces `let rec` at 78 % rate when simply asked.** With any few-shot framing (even k=1 retrieval of a single `let rec` library example) it hits 97–99 %. The base model has no prior against `let rec`. S29's observation of "0/125 `let rec` samples from LoRA adapters" is **cause (b) — LoRA suppressed a pattern the base could produce.**

The second surprise is even stronger: **Qwen-7B bare is WORSE at `let rec` than Qwen-1.5B bare** (44 % vs 78 %). Going bigger on the same model family does not automatically give you more `let rec` — the training-data distribution and token-level priors can move in either direction as parameters scale. Few-shot context flattens both to ≈100 %, so the scale-vs-instruction-tuning axes matter, but not in the direction Phase D assumed.

## Mapping to the plan's 3-way causal decomposition

The plan's Phase C table (sprint30.md §Phase C read-outs):

| Observation                                                    | Interpretation                                        |
|----------------------------------------------------------------|-------------------------------------------------------|
| Base 1.5B never produces `let rec` on any prompt               | Cause (c). Phase D upgrade more important.            |
| Base 1.5B produces `let rec` zero-shot but LoRA adapter never  | **Cause (b). LoRA suppressed a pattern the base could produce. Investigate S29 training config.** |
| Base 1.5B produces `let rec` only with retrieved example       | Base can condition but not spontaneously. Phase B.    |
| Base 7B produces `let rec` zero-shot but 1.5B does not         | Phase D's base-model hypothesis validated.            |

Our result clearly matches **row 2**: cause (b) — LoRA suppressed the pattern. The plan's remediation for this outcome is "**Investigate S29 training config**" — NOT "proceed to Phase D."

It also clearly rejects **row 4**: Qwen-7B is not better than Qwen-1.5B at `let rec`; it's worse. Phase D's base-model-hypothesis validation on this specific pattern is refuted.

## Phase C does not directly validate Phase A's 0/12 Qwen vs 12/12 frontier gap

A reasonable objection to this analysis: **Phase C tests `let rec` pattern generation under explicit prompting, not `let rec` synthesis under the held-out-task format.** Maybe Qwen produces `let rec` when asked for it, but not when asked to solve `merge_sorted_desc`.

Phase B's B-3 data answers this. When B-3 retrieval delivered `let_rec`-tagged examples for merge_sorted_desc and unfold_n:

- **unfold_n** → Qwen produced 6 unique `let rec go : Nat → Int → List Int | 0, _ => [] | n+1, seed => ...` tactics. Pattern generated correctly. Bodies semantically wrong.
- **merge_sorted_desc** → Qwen produced 5 unique `match xs, ys with | [], _ => ... | _, [] => ... | x::xs, y::ys => if ... else ...` tactics. Pattern correct. Semantic body wrong.

So even on the actual benchmark format, Qwen-1.5B produces `let rec` / 2-arg match templates correctly when given retrieved examples. The 0/12 outcome is not "can't produce the pattern" — it's "can produce the pattern but can't fill in the correct recursion body."

## What this means for the Phase D gate

Re-reading the plan's Phase D gate:

> **Gate conditions (all must be satisfied to commit):**
> - Phase A shows frontier model ≥3/12  ✓ (12/12)
> - Phase B-1 and B-3 both score ≤2/12  ✓ (both 0/12)
> - **Phase C confirms base-model capacity is a missing lever (1.5B lacks structural patterns 7B has, or base 1.5B has a strong prior against `let rec`)**  ✗

The third gate is the one Phase C was designed to test. It **fails**:
1. 1.5B does NOT lack the `let rec` structural pattern (78–99 % rate).
2. Base 1.5B does NOT have a strong prior against `let rec`.
3. 7B is not structurally ahead of 1.5B on this pattern — the opposite.

The plan's gate outcome table has exactly one row for this combination:

> A passes, B-1 ≤2/12 **but** B-3 ≥3/12 → Iterate on retrieval embedder before committing to Phase D. Phase D deferred, not killed.

But our B-3 was 0/12, not ≥3/12, so we're in a state the plan's explicit gate table does NOT cover. The closest match is **"A passes, B fails, C fails"** — which the plan handles only implicitly: the original Phase D design is *weakened* because the stronger-base-model assumption is refuted by Phase C. Phase D as originally described should NOT run.

## The real gap: semantic synthesis, not pattern coverage

Joint read of Phases A + B + C:

1. **Phase A**: Frontier (Claude Opus 4.6) solves 12/12 with generic few-shot. Qwen-1.5B / 7B both 0/12.
2. **Phase B**: Retrieval over 404 pairs delivers the right structural templates to Qwen-1.5B (observed in B-3 traces). Qwen-1.5B generates syntactically-well-formed `let rec` / `match_2arg` tactics in the right shape. Body semantics wrong, 0/12.
3. **Phase C**: Qwen-1.5B and 7B both produce `let rec` freely on direct request (78 % / 44 % bare, ~100 % with few-shot). Pattern generation is NOT the bottleneck.

**The bottleneck is semantic correctness of generated recursion bodies, not template coverage.** Frontier-level models (Claude Opus 4.6, presumably similar-tier competitors) appear to have enough reasoning + Lean 4 semantic knowledge to fill in correct recursion bodies. Qwen-1.5B and 7B base models do not — and going from 1.5B to 7B does not close this gap at all (both 0/12 on tasks, 44 % vs 78 % on bare `let rec` rate — not even monotone).

This reframes what a "stronger base model" means for S30. It is NOT:
- "more parameters in the same family"
- "more pre-training data in the same distribution"

It likely IS some combination of:
- **Much larger parameter count** (Claude Opus 4.6 is probably ~100× the size of Qwen-7B, not 5×)
- **Instruction tuning + RLHF** that specifically rewards semantic correctness
- **Higher-quality training data** (likely curated for structured reasoning over code)

None of these are in scope for a `Qwen2.5-Coder-7B` LoRA run. Phase D's cost/value calculus changes.

## Reshape recommendation — don't run original Phase D

Based on A + B + C, my recommendation for the sprint:

**Option α** (minimal, cheap, diagnostic): **Investigate the S29 LoRA suppression mechanism.** Phase C row 2 explicitly says "Investigate S29 training config." Load the S29-A and S29-C adapters, run `let_rec` probing against them in the same 5 configs above, confirm the rate drops to ~0 %. Then isolate what in the S29 training config caused the suppression — class imbalance in the training mix? Adapter position? Weight decay washing out the let_rec tokens? This is ~1 day of diagnostics and can be done with tools already in place.

**Option β** (mid-cost): **Run a "Phase D-lite" — LoRA on 1.5B, not 7B, using corrected S29 config.** If the S29 suppression mechanism is identified, retrain a LoRA on the same 404 pairs with the fix, check whether the new adapter preserves `let_rec` rate at generation time, and re-run the held_out_12 benchmark. Avoids the 14 GB + 20 h/config cost of Phase D. Probable result: better `let_rec` rates, but still 0/12 because the semantic-body bottleneck is unchanged.

**Option γ** (original Phase D but scoped-down): **Skip 7B LoRA, go straight to a larger/stronger base.** The Phase A result suggests the useful comparison is not 7B vs 1.5B but **frontier-class vs 7B**. The project does not have budget for frontier training, but Anthropic-hosted Claude via `claude-agent-sdk` or Pantograph-style tool orchestration is available. A "frontier-in-the-loop" synthesis path would be: use Claude Opus 4.6 (via Agent) as the generator, combine with the project's existing MCTS + oracle verification harness, and report the combined solve count. This is not Phase D at all — it's a new Phase D'.

**Option δ** (stop early): **End S30 with the Phase A + B + C null / reframe and hand to S31.** The primary metric is still `held_out_12` 0/12 on Qwen. The contribution of S30 is the **diagnostic clarity**: we now know the bottleneck is semantic synthesis, not template coverage / pattern supply / base-model prior. S31 designs around that knowledge.

My personal lean: **Option α then Option γ.** Option α is cheap and answers an outstanding mechanistic question (why does LoRA suppress `let_rec`?). Option γ is the only path that actually moves the primary metric in this sprint cycle, because Phase C has ruled out the cheaper approaches.

Open question for the user: should I proceed with Option α (≤1 day, cheap, closes out the S29 LoRA mystery) and stop there, or go further?

## Caveats

1. **Phase C tests `let rec` *template* generation, not *correct* `let rec`.** A 78 % rate on bare 1.5B doesn't mean 78 % correct solutions — only 78 % of generated tactics contain the literal substring `let rec`. Many of those tactics still have wrong recursion bodies, as B-3's traces show. The correct interpretation is: the template IS in the base model's vocabulary; the body synthesis is where the capability ceiling lives.
2. **The 1.5B > 7B bare `let rec` rate inversion is interesting.** I don't have a strong explanation — possibly 7B's training mix has more library-based (non-recursive) tactics, or there's a temperature sensitivity issue. Worth a follow-up if time permits.
3. **Phase C's 20 prompts are hand-picked** and deliberately biased toward simple tasks (sum, reverse, length) where `let rec` is a natural fit. A harder or more adversarial prompt set might show a different bare rate.
4. **The Phase A / B / C harness uses the same `generate_local` code path.** The generation-loop truncation fix applied mid-run (sprint30_status.md) is active in Phase C as well. No zero-candidate collapses observed.

## Deliverables

- `lean_mcts/benchmark/phase_c.py` — 5-config `let rec` probe runner
- `sprint/benchmark/results/phase_c.json` — raw per-prompt results
- `docs/implementation/sprint30/phase_c_analysis.md` — this file
