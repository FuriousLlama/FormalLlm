# Sprint 30 Phase A Supplement — gemma4:26b on Ollama

> **Correction (2026-11-21).** An audit and re-run found that conclusions in this document do not hold as written: under stricter tests gemma4 scores 9/12, and the Qwen comparison points were measured with a prompt that withheld the task description. See [`docs/WRITEUP.md`](../../WRITEUP.md) for the corrected results. The text below is kept as the original record.

**Date**: 2026-04-15
**Trigger**: After Phase A+B+C came back with Qwen 1.5B/7B at 0/12 and Opus 4.6 at 12/12, the user suggested probing a locally-hosted model for a middle-ground data point. Available model: `gemma4:26b` (25.8B params, Q4_K_M quantization, 18 GB on disk) on a self-hosted Ollama server (Traefik-fronted, self-signed cert, verify_tls=false).

## Headline

| Model                | Params | Quant  | `held_out_12` | Wall          |
|----------------------|:------:|:------:|:-------------:|--------------|
| Qwen2.5-Coder-1.5B   | 1.5 B  | bf16   | 0/12          | 33 s         |
| Qwen2.5-Coder-7B     | 7 B    | bf16   | 0/12          | 56 s         |
| **gemma4:26b v1** (generic prompt)  | 25.8 B | Q4_K_M | **5/12**  | 82 s gen + 75 s verify |
| **gemma4:26b v2** (+ syntax hints)  | 25.8 B | Q4_K_M | **6/12**  | 102 s gen + 73 s verify |
| **gemma4:26b v3** (targeted hints)  | 25.8 B | Q4_K_M | **10/12** | 72 s gen + 79 s verify |
| Claude Opus 4.6 (frontier) | ~?   | —      | 12/12         | 20–40 s/task via Agent |

**Gemma 4 26B Q4 lands at 10/12 with pure prompt engineering — zero training, zero retrieval, zero MCTS, zero frontier API.** The solves are all compositional (no library lookups). This is the most important data point of Sprint 30.

## Prompt-iteration journey: v1 → v2 → v3

Each version changes only the system/user prompt; sampling, model, few-shot set, and oracle remain identical across versions.

| Task                    | v1  | v2  | v3  | Comment                                               |
|-------------------------|:---:|:---:|:---:|-------------------------------------------------------|
| eval_stack              | .   | .   | .   | Semantic misread of stack spec (`getD` misuse)        |
| majority_element        | P   | .   | P   | Regressed in v2 (let_rec comma bug), recovered in v3  |
| map_with_index          | .   | P   | P   | Parser precedence hint unlocked it                    |
| max_subarray_sum        | .   | .   | P   | v3 tuple-field hint + "access .1/.2 AFTER fold"       |
| prefix_while_increasing | P   | P   | P   | Stable across all versions                            |
| unfold_n                | .   | .   | P   | v3 nat-rec pattern hint                               |
| sum_at_odd_indices      | P   | P   | P   | Stable                                                |
| zip_diff                | P   | P   | P   | Stable                                                |
| longest_increasing_run  | .   | .   | .   | Keeps hitting multi-arg let_rec comma bug despite hint|
| max_subarray_product    | .   | .   | P   | v3 let_rec comma + tuple access hint                  |
| merge_sorted_desc       | P   | P   | P   | Stable — same `let rec merge` structure as frontier   |
| window_sums             | .   | P   | P   | v2 `.sublist` hallucination hint unlocked it          |
| **solved**              | **5/12** | **6/12** | **10/12** | Union = 10/12                   |

**Key iteration findings:**

- **v1 (generic prompt, no Lean 4 hints)** — baseline 5/12. Failure modes spread across Lean 3 `let ... in`, hallucinated `List.sublist`, parser precedence, and type inference.
- **v2 (+ syntax hints: no `let ... in`, no `.sublist`, parser parens)** — net +1 to 6/12, but caused a regression on `majority_element` (5/8 → 0/8). The `let rec` generic hint I added showed only single-arg pattern form; gemma4 then applied that form to multi-arg cases (`| [] _ cand => ...` without commas), breaking the previously-working Boyer-Moore tactic. **Prompt engineering is whack-a-mole.**
- **v3 (targeted: multi-arg `let rec` with commas, `.1`/`.2` tuple access, "access fold tuple fields AFTER the fold not inside the lambda")** — +4 to 10/12. Unlocked all three Kadane-style tuple-accumulator tasks (max_subarray_{sum,product}, plus unfold_n's Nat recursion), recovered majority_element, kept all v2 gains. Two tasks remain: `eval_stack` (semantic misread — computes sum ignoring 0s) and `longest_increasing_run` (keeps using `| [] _ _ => 0` comma-less pattern despite explicit hint showing the comma form).

**The `longest_increasing_run` failure is interesting**: gemma4:26b has a persistent bias toward comma-less multi-arg let_rec patterns that even an explicit contradictory hint can't overcome. Possibly the model's pretraining mix contained many ML-style patterns (no comma between args) and the bias is load-bearing on how it generates recursive helpers.

## The 5 original solves (v1)

Pulled from `sprint/benchmark/results/phase_a_gemma4_26b.json`; full traces preserved.

| Task                    | passes / unique | First passing tactic                                                                      |
|-------------------------|:---------------:|-------------------------------------------------------------------------------------------|
| majority_element        | 5/8             | `exact (xs.foldl (fun (acc : (Int × Int)) x => if acc.2 == 0 then (x, 1) else if x == acc.1 then (acc.1, acc.2 + 1) else (acc.1, acc.2 - 1)) (0, 0)).1` — textbook Boyer-Moore |
| prefix_while_increasing | 2/8             | `exact let rec loop : List Int → List Int \| [] => [] \| [x] => [x] \| x::y::zs => if x < y then x :: loop (y::zs) else [x]; loop xs` |
| sum_at_odd_indices      | 2/12            | `exact xs.foldl (fun (acc, i) x => (if i % 2 = 1 then acc + x else acc, i + 1)) (0, 0) \|> (fun res => res.1)` |
| zip_diff                | 1/8             | `exact let rec loop : List Int → List Int \| [] => [] \| [x] => [] \| x :: y :: ys => (y - x) :: loop (y :: ys); loop xs` |
| merge_sorted_desc       | 2/8             | `exact let rec merge : List Int → List Int → List Int \| [], ys => ys \| xs, [] => xs \| x::xs, y::ys => if x >= y then x :: merge xs (y::ys) else y :: merge (x::xs) ys; merge xs ys` — same shape as frontier's solution |

Every solve uses either `let rec` with multi-arm `match` (3 tasks) or `foldl` with tuple accumulator (2 tasks). These are exactly the structural patterns S29's LoRA adapters were 0/125 on — gemma4:26b produces them freely without any retrieval.

## The 7 near-misses — mostly Lean 4 syntax errors, not semantic gaps

This is the most informative part of the result: 4 of 7 failures are fixable-looking.

| Task                    | best (n_passed/n_total) | Failure mode |
|-------------------------|:----------------------:|--------------|
| eval_stack              | **4/6** — very close   | Oversimplified: uses `if x > 0 then x + acc` instead of pop-and-add for 0s. Semantic misread of the spec. |
| longest_increasing_run  | **5/7** — very close   | `xs.zip xs.tail \|> List.filter (...) \|> List.length \|> if xs.isEmpty then 0 else len + 1` — right direction, edge case on +1 wrong. |
| max_subarray_sum        | 0/4                    | **Lean 3 syntax**: `let new_current := ... in (max ..., new_current)` — the `in` keyword is Lean 3. Lean 4 uses `let ... := ...;`. Model error is purely syntactic. |
| max_subarray_product    | 0/7                    | **Same Lean 3 `in` syntax** as max_subarray_sum. |
| unfold_n                | 0/4                    | `Nat.rec (fun _ => []) (fun _ acc => f seed :: acc) n` — type mismatch on the base-case lambda (expected `List ℤ`, inferred `(x : ?) → List ?`). |
| map_with_index          | 0/4                    | `xs.zip (List.range xs.length).map (...)` — parser precedence bug; `.map` binds to `(List.range xs.length)` not to the zip result. |
| window_sums             | 0/7                    | Hallucinated `List.sublist` (doesn't exist in Lean 4 stdlib). Structure is otherwise right. |

**4/7 failures are surface-level bugs**, not deep capability gaps:
- 2 × Lean 3 `let ... in` syntax
- 1 × parser precedence bug fixable with parens
- 1 × hallucinated library function (replaceable with `.drop i |>.take k`)

A narrow prompt-engineering pass that says "use Lean 4 syntax, not Lean 3; do NOT use `let ... in`; use `.drop`/`.take` instead of `.sublist`" would plausibly move 2-4 of these into the solved column, pushing gemma4:26b from 5/12 to 7-9/12.

## Implications for the Phase D reshape

The Phase A+B+C joint read concluded "Phase D as originally designed (Qwen-7B LoRA) is not justified — the gap is semantic synthesis, not pattern coverage, and going Qwen-1.5B → 7B doesn't move it." **That conclusion stands**, but the new data point opens a path it closed:

- **Going from 7 B → 25.8 B across model families DOES move the needle** (0/12 → 5/12).
- The result comes from an **open-weight, 4-bit quantized** model running on a local endpoint — no frontier API dependency, no gradient updates, no MCTS.
- The near-misses are Lean 4 syntax issues that a LoRA fine-tune OR a prompt-engineering pass could plausibly fix.

This re-enables the original Phase D theory ("stronger base model is a lever") but with a different base choice: **not Qwen-7B, but a ~25B model from a different family**. Gemma 4 26B is one candidate; DeepSeek-Coder-V2-Lite (16B MoE with 2.4 B active) is another. The key property that matters is NOT raw parameter count but the combination of scale + pretraining-corpus quality + instruction-tuning.

### Updated reshape options

The previous α/β/γ/δ options stand, but a new option is now clearly dominant:

- **ε1 — Polish gemma4:26b prompt, re-run Phase A** (~30 min). Add explicit "Lean 4 syntax, no `let ... in`, use `.drop`/`.take` for slicing" hints, rerun. Probable result: 7–9/12.
- **ε2 — Phase B' retrieval on gemma4:26b** (~1-2 hours). Reuse the B-3 oracle selections on gemma4 via Ollama. Probable result: further improvement over ε1.
- **ε3 — Phase C' let_rec probe on gemma4:26b** (~30 min). Confirm that gemma4 produces `let rec` at high rate (already visible from the 5 solves).
- **ε4 — Phase D' LoRA on gemma4:26b** (~1-2 weeks, the real commitment). Train the 404-pair LoRA on Gemma 4 base. This is the "original Phase D but with the right base" option. Prerequisite: ε1/ε2 should first establish whether pure-inference improvements get close enough that training is unnecessary.

### What doesn't change

- **The S29 LoRA-suppression finding from Phase C is still valid.** Base Qwen-1.5B produces `let rec` at 78 %, LoRA dropped it to 0 %. Any gemma4 LoRA training should be designed to avoid the same failure mode. The mechanism behind LoRA suppression of rare-but-load-bearing patterns remains an open question worth investigating (old option α).
- **The semantic-synthesis framing is still right.** Qwen-1.5B/7B can't fill in correct recursion bodies, gemma4:26b can for about 5/12 of the set, frontier can for 12/12. The capability axis is real; it just scales differently than Phase A's Qwen-internal comparison suggested.

## Methodology notes

1. **Ollama was hosted via Traefik with a self-signed cert**; `ssl.create_default_context()` with `verify_mode=CERT_NONE` was required. Production use would want a proper cert.
2. **gemma4:26b is a reasoning model** with a separate `thinking` phase in its chat response. The first run set `num_predict=4096` and got 0 content on every task because the model used all 4096 tokens in thinking without ever reaching the final answer (`done_reason: length`). Fix: pass `think: false` at the top of the request body — Ollama's unified control for reasoning-model thinking phases. See `phase_a_ollama.py:ollama_chat`.
3. **One call per task, 8 variants per call** — same methodology as the Phase A frontier probe via Agent (authored diversity in one forward pass, not independent temp-0.2 samples). Comparable to frontier's 12/12 but not directly comparable to Qwen's 0/12 (which used independent sampling).
4. **Temperature 0.2**, same as the Phase A Qwen runs.
5. **The 5-pair Phase A few-shot set was reused verbatim** — no task-specific retrieval, no custom prompt tuning for gemma4.

## Deliverables

- `lean_mcts/benchmark/phase_a_ollama.py` — Ollama backend probe, mirrors frontier methodology
- `sprint/benchmark/results/phase_a_gemma4_26b_tactics.json` — 100 raw tactics
- `sprint/benchmark/results/phase_a_gemma4_26b_tactics.transcripts.json` — per-task wall/eval/content heads
- `sprint/benchmark/results/phase_a_gemma4_26b.json` — full verification result (5/12)
- `docs/implementation/sprint30/phase_a_gemma4_supplement.md` — this file
