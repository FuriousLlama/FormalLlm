# Sprint 30 Status

> **Correction (2026-11-21).** An audit and re-run found that conclusions in this document do not hold as written: the Phase A and B prompts withheld the task description from Qwen, and the Phase C `let rec` rates are substring matches. See [`docs/WRITEUP.md`](../../WRITEUP.md) for the corrected results. The text below is kept as the original record.

Plan: [sprint30.md](sprint30.md)

## Phase 0 — Pre-flight (in progress, 2026-04-15)

### 0.1 — `merge_sorted_desc` multi-arg auto-intro anomaly

**Root cause (confirmed).** `_extract_param_names` in both
`lean_mcts/env/pantograph_client.py:184` and
`lean_mcts/env/pantograph_wrapper.py:109` used the regex
`re.findall(r'\(\s*(\w+)\s*:', sig)` to pull parameter names out of a
Lean `def foo ... := by` signature. That regex captures only the
first identifier inside each `(...)` binder, so:

| signature                            | old regex    | effect                                                      |
|--------------------------------------|--------------|-------------------------------------------------------------|
| `(xs : List Int)`                    | `['xs']`     | correct                                                     |
| `(k : Nat) (xs : List Int)`          | `['k','xs']` | correct (heterogeneous — `window_sums`)                     |
| `(xs ys : List Int)`                 | **`[]`**     | **dropped both args entirely** (homogeneous — `mergeSortedDesc`) |

For `mergeSortedDesc (xs ys : List Int) : List Int := by sorry`, the
old regex returned `[]`, so auto-intro was **skipped**. MCTS received
the bare `∀`-prefixed theorem goal with no hypotheses. No tactic in the
candidate set matched → ran out of legal moves after 21 oracle calls.
This exactly matches the S29 Phase 4 anomaly
(`docs/implementation/sprint29/phase4_analysis.md` §"Observed Anomaly").

**Fix applied.** Replaced the regex with:

```python
params: list[str] = []
for m in re.finditer(r'\(\s*([a-zA-Z_]\w*(?:\s+[a-zA-Z_]\w*)*)\s*:', sig):
    params.extend(m.group(1).split())
return params
```

This captures every whitespace-separated identifier inside a `(...)`
binder before the colon. Verified against every `def` in
`TasksS29Heldout.lean` (6 defs) and `TasksDiagnostic.lean` (18 defs):
`mergeSortedDesc` is the **only** task the old regex broke. All 23
others extract identically under old and new. Same fix applied to
both `pantograph_client.py` and `pantograph_wrapper.py`.

**End-to-end verification: DONE.** After the daemon restart (§0.4),
a direct `reset()` probe on `mergeSortedDesc` now returns:

```
xs : List ℤ
ys : List ℤ
⊢ List ℤ
```

Both parameters are intro-ed as hypotheses and the goal is free of
the ∀-prefix that caused the S29 21-oracle-call early exit. Control
probe on `windowSums` (heterogeneous 2-arg) also correct: `k : ℕ`,
`xs : List ℤ`, `⊢ List ℤ`. Fix is operational on the live daemon.

### 0.2 — `sanity_check.py` CLI

Created `lean_mcts/benchmark/sanity_check.py` as a standalone CLI
wrapping the Phase 2.3 generation-inspection methodology from
`inspect_generations.py`. Key additions over the original:

- Tier-aware task loading (`--tier held_out_12 | hard_holdout | diag_holdout | ...`)
- Base-only mode (no adapter required) for Phase A / Phase B runs
- Multi-label pattern-presence predicates (`let_rec`, `match_list`,
  `multi_step_have`, `fold`, `method_chain`) — independent of the
  single-label `classify()` function, so a `let rec ... match xs with ...`
  tactic counts toward both patterns correctly
- Tier → required-pattern mapping driving a PASS/FAIL/SKIP verdict
- JSON output + human-readable summary with REQ-flagged rows

Verdict logic is deliberately generous: a tier PASSES if every
required pattern appears ≥1 time across all samples. The question is
"is the pattern generatable at all?", not "at what rate?"

Verified with a stubbed-`generate()` dry run:
- PASS case (all 4 held_out_12 required patterns present) → PASS ✓
- FAIL case (missing `let_rec`) → FAIL with `missing=['let_rec']` ✓
- Unknown-tier case → SKIP ✓
- Pattern predicate tests: 6/6 pass on hand-crafted tactic strings ✓

### 0.3 — `make smoke`

`make smoke` now **6/6 PASS** (<1 min wall).

Found and fixed a pre-existing bug unrelated to S30: the
`held_out_loaded` check imported `HELD_OUT_HARD_TASKS` directly from
`lean_mcts.synth.held_out_hard_tasks`, which triggers a module-init
circular import because `lean_mcts.benchmark.tasks` eagerly calls
`_load_hard_holdout_tasks()` at module load (wired in S29 Phase 3.0).
Updated the check to import via `lean_mcts.benchmark.tasks.HARD_HOLDOUT_TASKS`
(the canonical re-export, no cycle). Production code already uses this
path so it was not caught before.

### 0.4 — `ALL_IMPORTS` update for held-out tiers

Noticed while tracing §0.1: `lean_mcts/env/pantograph_daemon.py`
`ALL_IMPORTS` is missing `FormalNav.TasksS29Heldout` and
`FormalNav.TasksDiagnostic`. These are the two files containing the
`held_out_12` tasks (12/12). The currently-running daemon
(4 workers, started 2026-04-14) was spawned with the old imports list
and **cannot** `env_inspect` any held-out task. Added both modules
to `ALL_IMPORTS`; the next daemon spawn will have them.

**Daemon restart completed.** User approved at 2026-04-15 13:12. Sent
SIGINT to the old daemon parent (clean shutdown via existing
`KeyboardInterrupt` handler; sockets + manifest removed). Restarted
with same args `--workers 4 --goal-ops 1` in background. New daemon
manifest at `/tmp/pantograph_workers.json` — 1 goal-ops + 3 verify.
Each pantograph-repl subprocess now lists `FormalNav.TasksDiagnostic`
and `FormalNav.TasksS29Heldout` in its argv, confirming the new
imports are loaded. Ping succeeds.

## Phase 0 complete — all 5 sub-tasks green

- [✓] #1 investigate merge_sorted_desc
- [✓] #2 fix merge_sorted_desc (client + wrapper + daemon imports)
- [✓] #3 promote sanity_check.py to CLI
- [✓] #4 verify make smoke passes (fast mode 6/6)
- [✓] #5 end-to-end verify merge_sorted_desc via daemon reset

## Phase A complete — frontier=12/12, Qwen base=0/12

| Run | Model               | `held_out_12` | Wall    |
|-----|---------------------|:-------------:|---------|
| 1   | Qwen2.5-Coder-1.5B  | 0/12          | 33.0 s  |
| 2   | Qwen2.5-Coder-7B    | 0/12          | 55.8 s  |
| 3   | Claude Opus 4.6 (via 12 parallel Agent calls) | **12/12** | 68.0 s verify |

- Gate hit: *"Frontier ≥3/12, Qwen-7B ≤1/12 → Base model matters — Phase D has capacity justification. Proceed to Phase B, then Phase D."*
- Frontier solves are compositional, not library-lookup: only `merge_sorted_desc` has any `List.merge`/`qsort` passes, and even that task's first passing tactic is a composed `let rec merge`. All 12 tasks have ≥1 composed passing tactic.
- Frontier produced `let rec` freely — exactly the pattern S29 LoRA adapters failed at (0/125 samples).
- Both Qwen base models hit the same 0/12 — scale alone (1.5B → 7B) does not move the needle on the few-shot format without stronger instruction-tuning or retrieval.
- Full analysis: [phase_a_analysis.md](phase_a_analysis.md).

**New artifacts**:
- `lean_mcts/benchmark/phase_a.py` (local + pre-generated-tactics mode)
- `sprint/benchmark/results/phase_a_fewshot.json` (frozen 5-pair few-shot set)
- `sprint/benchmark/results/phase_a_qwen_1.5b.json`, `phase_a_qwen_7b.json`, `phase_a_frontier.json`
- `sprint/benchmark/results/phase_a_frontier_tactics.json` (96 raw frontier tactics)
- `sprint/benchmark/results/phase_a_frontier_agent_ids.json` (task → agentId mapping)
- `docs/implementation/sprint30/phase_a_analysis.md`

## Phase B complete — retrieval null across all configs (0/12)

| Config | k  | Retrieval                       | Result  | Gate outcome |
|--------|----|---------------------------------|:-------:|--------------|
| B-1    | 10 | Qwen hidden-state cosine        | 0/12    | "embedder bottleneck" path unresolved |
| B-2    |  5 | Qwen hidden-state cosine        | 0/12    | Signal-focusing did not help          |
| B-3    | 10 | Oracle (structural hand-pick)   | 0/12    | Upper-bound null too                  |

- Gate per plan: *"B-1 ≤2/12, B-3 ≤2/12 → Strong evidence for Phase D. Ceiling is base-model knowledge, not training pipeline."*
- But the right reading is Phase B + Phase C joint — see Phase C.
- Key diagnostic: retrieval **did** deliver the right structural template (B-3 oracle selections produced `match_2arg` for merge_sorted_desc, `nat_rec + let_rec` for unfold_n, etc.). Qwen-1.5B generated syntactically-correct tactics in the right shape for these tasks — **semantic body synthesis** is the failure mode, not template coverage.
- Corpus structural supply is thin on exactly the hardest patterns: only 4 `tuple_accumulator` folds and 2 `index_aware` pairs in 404 entries.
- Harness fix: `generate_local` was over-eager on the `\n\n` stop rule and produced 0-unique-candidate tasks in long retrieval prompts. Fixed by lstripping before stop-scan and cutting only at blank-line-after-content. Patched mid-run.
- Full analysis: [phase_b_analysis.md](phase_b_analysis.md).

**New artifacts**:
- `lean_mcts/benchmark/phase_b.py` (index builder + retrieval-augmented runner)
- `sprint/benchmark/results/phase_b_index.npz` (404 × 1536 Qwen hidden-state embeddings)
- `sprint/benchmark/results/phase_b_corpus_tagged.json` (structural pattern tags)
- `sprint/benchmark/results/b3_oracle_selections.json` (frozen hand-picked top-10 per task)
- `sprint/benchmark/results/phase_b_{b1,b2,b3_v2}.json`

## Phase C complete — reshapes Phase D entirely

| Config              | `let rec` / unique | rate       | Wall   |
|---------------------|:------------------:|:----------:|:------:|
| 1.5B bare           | 125 / 160          | **78.1 %** | 30.0 s |
| 1.5B + Phase A few-shot | 149 / 150      | 99.3 %     | 27.2 s |
| 1.5B + k=1 letrec retrieval | 115 / 118   | 97.5 %     | 23.3 s |
| 7B bare             | 68 / 154           | **44.2 %** | 51.1 s |
| 7B + Phase A few-shot | 158 / 158        | 100.0 %    | 42.8 s |

- **Base Qwen-1.5B produces `let rec` at 78 % rate when asked.** S29's "0/125 `let rec` from LoRA" was NOT a base-model prior — it was **LoRA suppressing a pattern the base could produce**. Plan's cause (b): investigate S29 training config.
- **Qwen-7B is WORSE than Qwen-1.5B at bare `let rec`** (44 % vs 78 %). Phase D's "go bigger for more `let rec`" assumption is refuted.
- The Phase D gate `Phase C confirms base-model capacity is a missing lever` **fails** — 1.5B is not lacking the pattern; it has it natively.
- Joint read A + B + C: the real gap is **semantic synthesis of recursion bodies**, not template coverage. Retrieval delivers templates; Qwen-1.5B fills them out; bodies are semantically wrong; frontier-level models fill bodies correctly. This is not an axis where Qwen-7B LoRA will help.
- Full analysis: [phase_c_analysis.md](phase_c_analysis.md).

**New artifacts**:
- `lean_mcts/benchmark/phase_c.py` (5-config `let rec` probe runner)
- `sprint/benchmark/results/phase_c.json`

## Phase A supplement — gemma4:26b via Ollama (10/12 at v3)

After the Phase A+B+C null on Qwen family, probed a locally-hosted
`gemma4:26b` (25.8 B params, Q4_K_M quant, 18 GB on disk) via
a self-hosted Ollama server with the same Phase A frozen few-shot set
and frontier-style "one call, 8 variants" methodology. Three prompt
iterations:

| Model                       | `held_out_12` | Prompt-delta                                  |
|-----------------------------|:-------------:|-----------------------------------------------|
| Qwen2.5-Coder-1.5B          | 0/12          | baseline Phase A                              |
| Qwen2.5-Coder-7B            | 0/12          | baseline Phase A                              |
| **gemma4:26b v1** (generic) | **5/12**      | Phase A fewshot + JSON-array output           |
| **gemma4:26b v2** (+ hints) | 6/12          | + Lean 4 syntax notes (Lean 3 `let in`, `.sublist`, parser parens) |
| **gemma4:26b v3** (targeted)| **10/12**     | + multi-arg `let rec` comma, `.1`/`.2` tuple access, "access fold tuple AFTER fold" |
| Claude Opus 4.6             | 12/12         | frontier ceiling                              |

**v3 solved** (10/12): majority_element, map_with_index, max_subarray_sum,
prefix_while_increasing, unfold_n, sum_at_odd_indices, zip_diff,
max_subarray_product, merge_sorted_desc, window_sums — every one a
compositional solve (no library lookup).

**Still unsolved (2/12)**:
- `eval_stack` — semantic misread of the spec (`getD` misuse, model interprets "0 = pop two and add" as "0 = skip")
- `longest_increasing_run` — gemma4 persistently writes multi-arg `let rec` patterns without commas (`| [] _ _ => 0`) even with explicit hints showing the comma form

**Iteration methodology note**: v2 caused a REGRESSION on majority_element
(5/8 → 0/8) because the v2 `let rec` hint showed only single-arg pattern
form, and gemma4 extended that to multi-arg cases incorrectly. v3 fixed
this by showing multi-arg pattern syntax explicitly. Prompt engineering
is whack-a-mole; each "fix" can reveal new bugs.

**Ollama gotcha**: gemma4:26b is a reasoning model. Initial run with
`num_predict=4096` got 0 content on every task (`done_reason: length`,
all tokens in the `thinking` phase). Fix: pass `"think": false` at the
top of the request body. See `phase_a_ollama.py:ollama_chat`.

Full analysis: [phase_a_gemma4_supplement.md](phase_a_gemma4_supplement.md).

**New artifacts**:
- `lean_mcts/benchmark/phase_a_ollama.py` (Ollama probe with syntax-hint prompt)
- `sprint/benchmark/results/phase_a_gemma4_26b{,_v2,_v3}{,_tactics,_tactics.transcripts}.json`
- `sprint/benchmark/results/phase_a_gemma4_26b_union_tactics.json` (185 combined v1∪v2 tactics)

## Supplemental — gemma4 wired into MCTS+oracle on held_out_12

A coda to the plan-driven Phase A/B/C/supplement work: put a number on
how much the existing MCTS + oracle + grammar harness contributes on
top of the LLM on `held_out_12`, by using gemma4:26b as the policy
inside the full harness instead of answering one-shot.

Implemented `OllamaPolicy` and `HybridOllamaPolicy` in `lean_mcts/search/policy.py`
— same `propose_tactics(state, k) -> list[(tactic, log_prob)]` contract
as `GoalConditionedPolicy`, but backed by the Ollama `/api/chat` endpoint.
Key gotchas surfaced: `keep_alive: -1` to prevent ~31 s cold-load penalty
per call, `think: false` to skip the reasoning phase, `set_task()` to
thread task context into the prompt, and embedding the 5 few-shot pairs
from `phase_a_fewshot.json` directly in the system prompt.

| Config                       | `held_out_12` |
|------------------------------|:-------------:|
| Gemma4 one-shot v3 (baseline)| **10/12**     |
| Gemma4 + MCTS v1 (no fewshot)| 4/12          |
| Gemma4 + MCTS v2 (fewshot)   | **6/12**      |

**MCTS unlocks ZERO tasks one-shot couldn't already solve.** Grand union
across all 5 gemma4 runs (one-shot v1/v2/v3 + MCTS v1/v2) = 10/12.
Still unsolved: `eval_stack`, `longest_increasing_run`.

**Diagnosis**: every successful MCTS solve fires at `rollouts_used=1` —
the first Ollama call produces a complete tactic and the oracle validates
it. Every failing task shows `n_expand_calls=1, tree_size=1, depth={'0': 1}`
— MCTS expanded the root once, found no children Lean accepts, and
spent 20 rollouts grinding patience until abort. The search dimension
never engaged, because every held_out_12 task is a one-tactic proof and
one-tactic proofs have no sub-goals to explore.

The tree-search dimension does not engage on one-tactic benchmarks:
every `held_out_12` task is solvable in a single `exact <term>`, so
there are no sub-goals for MCTS to explore. On this benchmark the
harness degenerates to "one-shot LLM + oracle verify" with extra
overhead. Whether the harness adds signal on tiers that require
sequences of moves (`multistep`, `deep_multistep`, `compositional`)
is an open question this run did not answer.

Full analysis: [phase_functional_machine.md](phase_functional_machine.md).

**Decision point — updated**:

The original α/β/γ/δ options stand, but the gemma4:26b result
re-enables the "stronger base model" lever on a different axis than
Phase C rejected:

- **α** — S29 LoRA suppression mechanism probe (~1 day, cheap) — still useful as diagnostic
- **β** — Retrained 1.5B LoRA with corrected S29 config (~3 days) — weakened by gemma4 result
- **γ** — Frontier-in-the-loop synthesis (~1 week) — still on the table as a reference path
- **δ** — Stop S30 (hand clarity to S31) — still viable
- **ε1** — Polish gemma4 prompt (Lean 4 hints), rerun (~30 min) — very cheap, probable 7–9/12
- **ε2** — Phase B-3 oracle retrieval on gemma4 (~2 hours) — if ε1 doesn't hit ceiling
- **ε3** — Phase C `let rec` probe on gemma4 (~30 min) — diagnostic only
- **ε4** — Phase D' LoRA on gemma4:26b (~1–2 weeks) — the real reshape of original Phase D

**Awaiting user call** on which option(s) to pursue. My lean: **ε1 first** (very cheap,
validates the syntax-hint hypothesis), then decide between ε2 (more inference-side
tuning) and ε4 (real training).

## Files touched so far

```
 M lean_mcts/env/pantograph_client.py      # regex fix + comment
 M lean_mcts/env/pantograph_daemon.py      # ALL_IMPORTS += S29Heldout, Diagnostic
 M lean_mcts/env/pantograph_wrapper.py     # regex fix + comment
 M tests/harness_smoke.py                  # fix circular-import in held_out_loaded
?? lean_mcts/benchmark/sanity_check.py     # NEW: Phase 2.3 triage CLI
?? docs/implementation/sprint30/sprint30.md
?? docs/implementation/sprint30/sprint30_status.md
```

## What's next (pending user go)

1. **Restart the daemon** with the updated `ALL_IMPORTS` so Task #5
   and Phase A can reach held-out tasks.
2. **Run Task #5**: one MCTS probe on `merge_sorted_desc` to confirm
   the fix holds end-to-end.
3. **Begin Phase A**: zero-shot capability probe on Qwen-1.5B-Coder,
   Qwen-7B-Coder, and Claude Opus 4.6. This is ~1 day of inference
   calls. The frontier-model call needs API credentials and budget
   confirmation.
