# Sprint 30 — Does the Functional Machine Add Signal?

**Date**: 2026-04-15
**Scope**: A coda to the plan-driven Phase A+B+C results. Sprint 30 as laid out in `sprint30.md` measured LLM capability under different prompt/retrieval/base-model conditions; this document adds the one experiment those phases did not cover directly — wiring gemma4:26b into the existing MCTS + oracle + grammar harness as the policy network and measuring the delta vs the one-shot probe. The goal is to put a number on *how much the harness contributes on top of the LLM* on `held_out_12`.

## Experimental setup

- **Policy**: `OllamaPolicy` (new, `lean_mcts/search/policy.py`). Same `propose_tactics(state, k) -> list[(tactic, log_prob)]` contract as `GoalConditionedPolicy`. One HTTP call to the Ollama `/api/chat` endpoint per unique state, parses JSON array, rank-based pseudo-log-probs, `keep_alive: -1` to prevent 31 s cold reloads, `think: false` to skip the reasoning phase.
- **Model**: `gemma4:26b` (Q4_K_M, 18 GB on the Ollama host). Same model used in the one-shot probe. Same 5-pair few-shot set (once embedded in the system prompt) and same Lean 4 syntax rules from v3 one-shot.
- **Harness**: existing `lean_mcts/benchmark/run.py` with `--policy ollama --daemon`. Pantograph oracle verifies each candidate against the task's test cases. MCTS expands nodes via PUCT using the rank-based log-probs as the prior.
- **Task context**: `OllamaPolicy.set_task(task)` called once per task by `run.py`, injecting task name / description / first 3 test cases into the prompt. Without this the policy only sees bare goal states and produces generic "sum a list" tactics — verified by a smoke-test regression.
- **Budget**: `--rollouts 30 --ollama-k 12 --patience 20 --seed 42`. Oracle verification via daemon with parallel check_compile workers.

## Comparison — two MCTS runs vs one-shot baseline

| Task                    | MCTS v1 (no fewshot) | MCTS v2 (fewshot in system) | One-shot v3 |
|-------------------------|:--:|:--:|:--:|
| majority_element        | ✓  | ✓  | ✓  |
| map_with_index          | ✓  | ✓  | ✓  |
| max_subarray_sum        | .  | **✓**  | ✓  |
| prefix_while_increasing | .  | **✓**  | ✓  |
| unfold_n                | .  | **✓**  | ✓  |
| sum_at_odd_indices      | ✓  | **.**  | ✓  |
| zip_diff                | ✓  | **.**  | ✓  |
| longest_increasing_run  | .  | .  | .  |
| max_subarray_product    | .  | .  | ✓  |
| merge_sorted_desc       | .  | **✓**  | ✓  |
| window_sums             | .  | .  | ✓  |
| eval_stack              | .  | .  | .  |
| **solved**              | **4/12** | **6/12** | **10/12** |

**Union across all 5 gemma4 runs** (one-shot v1+v2+v3, MCTS v1+v2):
`majority_element, map_with_index, max_subarray_sum, prefix_while_increasing, unfold_n, sum_at_odd_indices, zip_diff, max_subarray_product, merge_sorted_desc, window_sums` → **10/12**. Still unsolved: `eval_stack`, `longest_increasing_run`.

**MCTS unlocks zero tasks that one-shot couldn't already solve.** The grand union equals the one-shot v3 result.

## Why MCTS doesn't help on this benchmark

The MCTS run's per-task stats give the mechanism.

**All MCTS solves fire at `rollouts_used=1`.** The first Ollama call at the root state produces a complete tactic, the oracle validates it, MCTS returns SOLVED. No tree search, no sub-goal exploration, no backpropagated value. MCTS is acting as a thin wrapper around "one-shot LLM + oracle verify."

**All failing tasks have `n_expand_calls=1, tree_size=1, depth_distribution={'0': 1}`.** MCTS expanded the root node exactly once, found no valid children, and then spent 20 rollouts grinding patience until abort. The failure mode is not "search didn't find the right path" — it's "the policy produced zero tactics that Lean accepts at the root, so MCTS has no child state to explore *from*."

Concretely, the failing-task flow is:
1. `propose_tactics(root_state)` returns N candidate tactics from Ollama
2. MCTS tries to apply each via `env.step(state, tactic)`
3. All N tactics throw type errors, parse errors, or semantic errors
4. Zero valid children → tree stays at size 1
5. Repeat until patience cap

**Why this matters**: MCTS is designed to explore *conditional-on-partial-progress* decisions — "given I've applied `constructor`, what's next for the first goal?" When every candidate at the root is a complete one-tactic proof that either works or doesn't, there are no partial states to condition on. The search dimension collapses.

`held_out_12` is built entirely of one-tactic proofs. Every task can be solved by a single `exact <term>` if the policy can write the right term. The oracle + MCTS harness adds value on multi-step proofs (where finding the right first step, then the right second step, etc., actually benefits from tree search), but not on a benchmark where "first step" = "only step" = "whole answer".

## What this implies about the harness

- On `held_out_12`, the harness (MCTS + oracle + grammar + Pantograph) adds no solves on top of one-shot LLM generation with the same model and prompt. Zero of the 12 tasks that one-shot v3 can already solve were unlocked by MCTS.
- The harness's tree-search component does not engage on one-tactic benchmarks: with no valid child states produced by the root policy call on failing tasks, there is nowhere to search from. Tree size stays at 1, depth stays at 0.
- This does not say the harness is useless — only that its value proposition (multi-step proof search, adaptive sub-goal exploration) is not exercised by `held_out_12`. A benchmark of multi-step proofs, where a policy's first tactic opens sub-goals that need their own tactics, would exercise a different dimension of the machine.

So the result is tight and defensible for what it measured: **on this specific benchmark, with gemma4:26b as the policy, the harness is equivalent to "one-shot LLM + oracle verify" and adds no new solves.** Whether the harness adds signal on other tiers (`multistep`, `deep_multistep`, `compositional`) is an open question this run did not answer.

## Observed costs of wiring gemma4 into MCTS

(Useful engineering lessons, separate from the headline.)

| Bug hit | Fix | Cost if missed |
|---|---|---|
| `keep_alive` default | Pass `keep_alive: -1` in request body | ~31 s cold-load penalty per policy call — makes the harness unusable |
| Python stdout buffered | `-u` flag | Smoke tests appear to hang even when making progress |
| `OllamaPolicy` no task context | Implement `set_task()`, thread name/description/test cases into prompt | Policy produces generic "sum a list" tactics for every goal state; MCTS solves 1/12 max |
| Missing few-shot examples in system prompt | Embed the 5 curated pairs in `_FEWSHOT_PAIRS` class constant | Policy produces placeholder tactics, syntax errors, wrong-type expressions; MCTS solves 4/12 instead of 6/12 |
| Verbose oracle reject spam | Drop `--verbose` for batch runs | Output file buffering obscures the actual task results behind thousands of reject lines |
| Reasoning-model `thinking` phase | Set `"think": false` at top of body | Without it, `num_predict=4096` gets fully consumed by thinking and content is empty |

Every one of these was a real experience in the S30 debugging path. The fixes live in `lean_mcts/search/policy.py:OllamaPolicy`.

## Possible follow-ups

- **α — Accept and close.** Headline: "on `held_out_12`, gemma4:26b inside MCTS+oracle yields 6/12; the same model one-shot yields 10/12; the harness adds no solves because the tasks are all one-tactic."
- **β — `HybridOllamaPolicy` with the ExpandedFixed safety net.** The safety net supplies ~200 hand-crafted tactics MCTS can always fall back on, so the tree has valid children to expand even when gemma4 strikes out. Might recover 1–2 tasks. Cost is ~80 s of oracle work per expansion.
- **γ — Run the existing `multistep` / `deep_multistep` / `compositional` tiers with the same policy.** These tasks genuinely require sequences of moves, so the MCTS dimension has something to search. Cleanest test of whether the harness adds signal at all.

Lean: γ if the question of "does the harness help on any tier" is worth answering this sprint; α if this is enough.

## Deliverables

- `lean_mcts/search/policy.py` — `OllamaPolicy` + `HybridOllamaPolicy` classes, with few-shot + syntax rules in the system prompt, `set_task()`, rank-based pseudo-log-probs, state cache with per-task reset, `keep_alive: -1`, `think: false`
- `lean_mcts/benchmark/run.py` — `--policy ollama | hybrid-ollama` flag + `--ollama-url`, `--ollama-model`, `--ollama-k`, `--ollama-temperature` arguments
- `sprint/benchmark/results/run_ollama_20260415_164559_0.json` — MCTS v1 (no fewshot), 4/12
- (latest run output — MCTS v2 with fewshot) — 6/12
- `docs/implementation/sprint30/phase_functional_machine.md` — this file
