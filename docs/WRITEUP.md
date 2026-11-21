# What 30 sprints of small-model program synthesis taught me

_Manuel Rodriguez — FormalLlm project writeup_

## Summary

I spent 30 sprints on my work PC with a 5090 GPU testing whether a small model,
guided by types and checked by execution, can synthesise correct functional
programs and generalise beyond what it was trained on. At this scale it did
not.

Before publishing, I had the code and results audited and re-ran the
evaluations the audit called into question. Several conclusions I had drawn
during the project did not survive. This document reports the corrected
results and says what changed.

1. A type-guided search over a hand-curated library passes 97 of 120
   benchmark tasks, not the 107 I had recorded. Most of those are lookups of
   answers already in the library. On 18 tasks written to fall outside it,
   the system solves 3.
2. On a 12-task held-out set, capability rises with model size once every
   model is given the task description: Qwen-1.5B 0, Qwen-7B 4, gemma4:26b 9,
   Claude Opus 4.6 12. My original comparison had the two Qwen models at
   0/12 because their prompt contained only the goal type.
3. Retrieval over the training corpus does not rescue the 1.5B model: 0/12
   in every configuration.
4. My claim that fine-tuning suppressed a capability the base model had was
   wrong. It rested on a substring match. With a compile check there is no
   measurable difference between the base model and the fine-tuned adapters.
5. The evaluation had four defects: an oracle bug that credited results to
   the wrong candidate, prompts that withheld the task from some models, a
   metric that counted a string instead of a working program, and tests weak
   enough to pass wrong answers.

Numbers from the re-run are in
[`sprint/benchmark/results/rerun_2026-11-21/`](../sprint/benchmark/results/rerun_2026-11-21/).
Where a number comes only from a sprint document, I say so.

## The question

Can a small transformer learn to compose typed programs, rather than recall
them? I started with a ~485K-parameter model over de Bruijn-indexed GHC Core
with type-constrained decoding (sprints 0–10), then moved to Lean 4 with
Monte Carlo tree search over tactics, a compile-and-run oracle, and
Qwen2.5-Coder-1.5B with LoRA adapters (sprints 11–30).

## Results

### The search library

In sprints 24–26 I replaced learned proposal with skeleton-guided search:
analyse the goal type, choose a structure (product, structural recursion,
fold, two-pass), enumerate candidates for the holes, and verify each with the
oracle. I recorded 107/120 in sprint 26 and 104/120 in sprint 27.

In the re-run, every recorded solve was verified alone, one definition per
file, against the existing tests and then against the task's reference
implementation on about 32 fresh inputs.

| Run set   | Recorded | Do not compile | Pass tests, wrong on fresh inputs | Correct |
| --------- | -------- | -------------- | --------------------------------- | ------- |
| Sprint 26 | 107      | 6              | 4                                 | 97      |
| Sprint 27 | 104      | 3              | 4                                 | 97      |

The solves that do not compile are tactic sequences such as
`constructor; norm_num; norm_num` that never mention the input. They were
accepted because the batch oracle assumed one message per test case. A
definition that fails to elaborate emits one extra message, which shifts
every later candidate's results by one slot. The re-run reproduces this: with
four failing candidates ahead of a correct one, the parser credits the
candidate after the correct one and marks the correct one as failed. The bug
can therefore also hide real solves; I have not measured that side. It is not
yet fixed in the search code. The re-run avoids it by verifying one candidate
at a time.

The 97 are mostly not search results in any interesting sense. A code review
traced the winning tactic for each recorded sprint 27 solve: 41 are verbatim
entries in a hand-written tactic list, 15 are verbatim in the enumerated
expression library, and 45 fill a slot in a step template. The sprint 26 plan
lists template batches by the failing tasks each was written to unblock. The
search code itself never reads a task's description or tests; the benchmark
leaked into the library through me.

The out-of-library test is the 18 diagnostic tasks from sprint 27. The system
solves 3, and all three verify as correct.

### What the fine-tuned model contributed

I had written that the LoRA-backed policy gave identical results to
enumeration on all 138 tasks. That is not supported. The sprint 26 status
table credits the model with two extra solves on the hard tier, and no
archived run compares the two policies on the full task set with the policy
recorded. The control flow also makes "identical on solved tasks" true by
construction: the model is consulted only after every deterministic skeleton
has failed. The sprint 27 status document reports that the model added no solves on the
18 diagnostic tasks; I have no policy-labelled result file for that either.
A full comparison has not been re-run.

Training on real Lean library code (404 pairs, from Lean core, Batteries and
Mathlib) and running the search with those adapters scored 0/12 on the
held-out set. That search is type-directed and never sees the task
description, so it measures search over types, not synthesis from a
specification. The result files do not record which adapter was loaded; the
rank 16 and rank 64 labels come from the sprint 29 analysis document.

### Held-out set

Twelve tasks, eight samples per task, a task counts as solved if any sample
passes. "Strict" adds 10–17 extra test cases per task, with expected values
computed by the reference implementation in Lean.

| System                                                | As first reported | Re-run, original tests  | Re-run, strict tests |
| ----------------------------------------------------- | ----------------- | ----------------------- | -------------------- |
| Qwen2.5-Coder-1.5B, goal type only                    | 0/12              | 0/12                    | 0/12                 |
| Qwen2.5-Coder-7B, goal type only                      | 0/12              | 0/12                    | 0/12                 |
| Qwen2.5-Coder-1.5B, with task description             | not run           | 0/12                    | 0/12                 |
| Qwen2.5-Coder-7B, with task description               | not run           | 4/12                    | 4/12                 |
| Qwen2.5-Coder-1.5B + retrieval, with task description | not run           | 0/12 (4 configurations) | not run              |
| gemma4:26b, 4-bit, third round of prompt hints        | 10/12             | 10/12                   | 9/12                 |
| Claude Opus 4.6                                       | 12/12             | 12/12                   | 12/12                |

The original Qwen prompts contained the goal type and five generic examples,
and nothing else. Five of the twelve tasks share the goal `List Int ⊢ Int`
and received identical prompts; the 1.5B model returned the same sum fold for
each. gemma4 and Claude were given the task name, description and three test
cases. With the same information, the 7B model solves four tasks at both
temperatures tried (0.2 and 0.8), and all four survive the strict tests. The
1.5B model still solves none: across the two temperatures, 144 of its 157
unique candidates fail to compile.

The comparison is still not fully like-for-like. The Qwen prompt lacks the
Lean syntax hints written for gemma4, and each Qwen sample is one tactic,
where gemma4 and Claude returned eight variants in a single response.

gemma4 loses one task under the strict tests: all five of its passing
candidates for `max_subarray_product` return 0 for `[-3]`.

Using gemma4 as the MCTS policy scored 4/12 and 6/12 in two runs. Every solve
came on the first rollout and every task received a single expansion call,
so the search dimension never engaged on these single-tactic tasks.

### `let rec`

I had claimed that the base 1.5B model writes `let rec` 78% of the time when
asked, that the fine-tuned model never did, and therefore that fine-tuning
had suppressed the pattern. The 78% counted any output containing the string
`let rec`, on prompts that name it. Nothing was compiled, and the fine-tuned
model was never run on the same prompts.

Re-measured, 160 samples per condition:

| Condition (original prompts) | Contains the string | Compiles with `let rec` | Passes tests |
| ---------------------------- | ------------------- | ----------------------- | ------------ |
| 1.5B, bare                   | 82%                 | 0%                      | 0%           |
| 1.5B, few-shot               | 93%                 | 0.6%                    | 0.6%         |
| 1.5B, one retrieved example  | 96%                 | 12.5%                   | 0.6%         |
| 7B, bare                     | 36%                 | 0%                      | 0%           |
| 7B, few-shot                 | 99%                 | 18.8%                   | 14.4%        |

The original prompts name no variables, so the compile rates here are a lower
bound. With the goal state added, the 7B few-shot condition reaches 40%
passing, and the 1.5B model with one retrieved example reaches 12%. Neither
model produces a working `let rec` without an example in context.

On matched prompts in the format the adapters were trained on, the base 1.5B
model and both adapters each produce 0 of 160 compiling `let rec`
definitions. On the bare prompt with the goal added, the base produces 0 of
160 and the adapters 1 and 3. These rates cannot be told apart at this sample
size. There is no evidence of suppression.

## What the audit found

Each defect below was in my own harness and each inflated or distorted a
result I had reported.

**The oracle matched results by position.** One extra message from a failed
definition shifted every later verdict. Six non-compiling candidates were
recorded as solves in sprint 26 and three in sprint 27, and I put the drop
from 107 to 104 down to variance.

**The models under comparison were not given the same task.** The prompt
builder for local models was written for a search that only sees types, and I
reused it for a comparison against models that were told what to build.

**A metric measured the prompt.** The `let rec` rate counted a substring that
the prompt itself supplied.

**Tests were weak.** In sprint 27 I caught three of five diagnostic "solves"
as false positives and hardened those tests. I did not go back and harden the
rest. Seven tasks across the two run sets were recorded as solved by programs that
pass their tests while computing the wrong function, including two "sort unique elements" tasks
solved by an expression that never sorts.

**The benchmark and the library grew together.** Templates were added for
named failing tasks and scored on those tasks. The diagnostic set in sprint
27 was the first test the library had not seen.

## Limitations

- The held-out set has 12 tasks and each configuration is a single run.
- The Claude and gemma4 results come from one response producing eight
  variants per task, not eight independent samples.
- The gemma4 prompt was tuned against the held-out set, so its score is an
  upper bound.
- The strict tests were written after seeing which candidates passed.
- The held-out set is public and should be treated as contaminated.
- Not re-run: the search with and without the fine-tuned model on all 138
  tasks, and whether the oracle bug hid correct solutions.
- Results from sprints 0–23 are quoted from sprint documents and were not
  re-verified.
- I did not test full fine-tuning, larger corpora, or model families other
  than Qwen and Gemma.

## What I would do differently

Verify every candidate in isolation. Compare a recorded solution against a
reference on inputs the tests do not contain. Give every system under
comparison the same information, and read the prompts before reading the
scores. Score working programs, not text. Keep a task set that the library
author has never seen. Run the audit before drawing conclusions, not after.

## How this was built

The code and sprint documents were written with Claude Code as a coding
assistant throughout, and the sprint 31 retrospective was compiled with
sub-agents reading the sprint record. The Claude Opus baseline was produced
by Claude agents and verified by the same Lean oracle as every other entry.
The audit and the re-runs reported here were carried out on 21 November 2026
by Claude agents at my direction; I reviewed their findings against the
result files.

## Where to look

- [`README.md`](../README.md) — setup and how to reproduce
- [`sprint/benchmark/results/rerun_2026-11-21/`](../sprint/benchmark/results/rerun_2026-11-21/) — re-run results, raw generations and the strict tests
- `lean_mcts/benchmark/rerun_*.py` — re-verification scripts, including the oracle bug reproduction
- [`docs/implementation/sprint30/`](implementation/sprint30/) — the original probe analyses, superseded where they conflict with this document
- [`docs/implementation/sprint31/sprint31.md`](implementation/sprint31/sprint31.md) — the original retrospective, superseded likewise
- `lean_mcts/search/` — the skeleton search
