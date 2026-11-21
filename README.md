# FormalLlm

A solo research project on **type-directed program synthesis with small models**:
can a small model, guided by types and checked by execution, synthesise
correct functional programs — and generalise beyond what it was shown?

Thirty sprints on one RTX 5090 workstation produced a mostly negative answer.
Before publication the code and results were audited and the questionable
evaluations re-run; several conclusions drawn during the project did not
survive. The numbers below are the corrected ones. The full sprint record is
in [`docs/implementation/`](docs/implementation/).

**Start with the writeup: [`docs/WRITEUP.md`](docs/WRITEUP.md).**

## Findings

| Sprints | What was tried | Result |
|---|---|---|
| S0–S10 | ~485K-parameter transformer on de Bruijn-indexed GHC Core, type-constrained decoding, rejection-sampling RL | Recall tasks mostly solved (18–22 of 22); compositional tasks stayed between 0/10 and 5/10 (sprint documents; not re-verified) |
| S11–S23 | Pivot to Lean 4: MCTS over tactics, oracle verification, Qwen2.5-Coder-1.5B + LoRA, self-play data | Scaling synthetic data to 22K pairs did not improve results (51/62 after, 55/62 before per sprint documents; single run) |
| S24–S26 | Skeleton-guided search: type analysis picks a structure (product, recursion, fold, two-pass), enumerates holes from a hand-curated library, verifies with the oracle | 107/120 recorded; **97/120** correct on re-verification. Most are lookups of library entries |
| S27 | 18 diagnostic tasks outside the library | **3/18**, all verified correct |
| S28–S29 | LoRA trained on Lean library code (404 pairs), used as the search policy | **0/12** on the frozen held-out set (the search sees only the goal type) |
| S30 | Model probes on the held-out set | See below |

Held-out set (`held_out_12`), eight samples per task. "Strict" adds 10–17
reference-derived test cases per task.

| System | Original tests | Strict tests |
|---|---|---|
| Qwen2.5-Coder-1.5B, with task description | 0/12 | 0/12 |
| Qwen2.5-Coder-7B, with task description | 4/12 | 4/12 |
| Qwen2.5-Coder-1.5B + retrieval, with task description (4 configurations) | 0/12 | not run |
| gemma4:26b (4-bit quantised, via Ollama) + Lean 4 prompt hints | 10/12 | 9/12 |
| gemma4:26b as an MCTS policy (two runs) | 4/12, 6/12 | not run |
| Claude Opus 4.6 | 12/12 | 12/12 |

What this shows:

- **The search is library coverage, not synthesis.** It passes 97 of 120
  tasks, most by looking up an answer written in advance, and 3 of 18 tasks
  outside the library.
- **Capability on the held-out set rises with model size** once every model
  is given the task: 0, 4, 9, 12.
- **Retrieval does not rescue the 1.5B model.**
- **MCTS did not engage on single-tactic tasks.** Every solve came on the
  first rollout, and every task received one expansion call.
- **The project's own evaluation had four defects**, found in the audit: an
  oracle bug that credited results to the wrong candidate, prompts that
  withheld the task from some models, a metric that counted a string, and
  weak tests. The writeup describes each.

Two claims made during the project are withdrawn: that fine-tuning suppressed
a `let rec` capability the base model had (a compile check shows no
measurable difference), and that the fine-tuned model contributed nothing on
all 138 tasks (not supported by the archived runs).

Caveats, stated plainly:

- `held_out_12` has 12 tasks and each configuration is a single run.
- The Claude and gemma4 results came from one response producing eight
  variants per task, not eight independent samples. The Qwen prompt lacks
  the Lean syntax hints written for gemma4.
- The gemma4 prompt was iterated three times against `held_out_12` itself
  (5/12 → 6/12 → 10/12), so its score is an upper bound.
- The batch-oracle bug is reproduced in `lean_mcts/benchmark/rerun_mechanism.py`
  but not yet fixed in the search code; it can also hide correct solutions.
- `held_out_12` is published in this repository and should be treated as
  contaminated for any model trained after its release.

Re-run results are in
[`sprint/benchmark/results/rerun_2026-11-21/`](sprint/benchmark/results/rerun_2026-11-21/).
The sprint 30 analyses and the sprint 31 retrospective in
`docs/implementation/` record the original conclusions and are superseded
where they conflict with the writeup.

## Repository layout

```
sprint/        Haskell / GHC Core pipeline (S0–S10)
  extract/       GHC Core → typed AST corpus (hand-written + Hackage)
  corpus/        normalise, augment, tokenise
  model/         small transformer: train, generate
  typecheck/     type-constrained decoding
  benchmark/     task suite; results/ holds the S30 probe outputs
  rl/            rejection-sampling fine-tuning
lean_mcts/     Lean 4 synthesis (S11–S30)
  env/           Pantograph daemon and client; FormalNav/ is the Lean project
  search/        MCTS, policies, skeleton candidate generators
  eval/          oracle (compiles and runs candidates against test cases)
  benchmark/     runner, task definitions, S30 probe scripts
  synth/         expression enumeration, self-play, data generation
  train/         LoRA training scripts
tests/         unit tests (Haskell pipeline) and harness smoke suite
docs/          sprint plans, status and lessons; theory notes
benchmark/results/   archived benchmark runs
```

Model weights, LoRA adapters, the extracted Hackage corpus and most training
data are not in the repository. The exception is the 404-pair corpus used in
S29 and S30 (`lean_mcts/train/diverse_{train,val}_v2.jsonl`), derived
from the Lean 4 core library (284 pairs), Batteries (88) and Mathlib (32),
all Apache-2.0. The search benchmark needs none of them: it uses the
`expanded-fixed` policy, which reads the 650 expressions in
`lean_mcts/synth/expressions.jsonl`.

## Setup

Developed on Arch Linux with an RTX 5090. The two halves use separate
virtual environments.

### Lean side

Requires [elan](https://github.com/leanprover/elan) (the toolchain is pinned to
Lean `v4.27.0` in `lean_mcts/env/FormalNav/lean-toolchain`) and
[uv](https://github.com/astral-sh/uv).

```bash
cd lean_mcts
make setup     # Python 3.12 venv, requirements.txt, lake update + cache get + build
```

`make setup` does not install
[PyPantograph](https://github.com/lenianiva/PyPantograph); install it into
`lean_mcts/.venv` following its own instructions (version 0.3.13 was used).

### Haskell side

Requires GHC 9.6.6 and `cabal-install`.

```bash
uv venv .venv --python 3.14
uv pip install --python .venv/bin/python torch torchvision \
    --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv/bin/python tokenizers numpy
cabal update
```

On Arch, cabal must be configured for dynamic linking; see the note in
[`CLAUDE.md`](CLAUDE.md).

## Running

All commands run from the repository root.

Start the Pantograph daemon (about 3.4 GB of RAM per worker):

```bash
lean_mcts/.venv/bin/python -m lean_mcts.env.pantograph_daemon --workers 4
```

Run a benchmark tier with the skeleton search and no model:

```bash
lean_mcts/.venv/bin/python -m lean_mcts.benchmark.run \
    --tier t2 --policy expanded-fixed --daemon
```

Tiers include `t2`, `hard`, `v2`, `recurrence`, `fundamentals`, `multistep`,
`deep_multistep`, `compositional`, `recursive`, `stretch`, `v3_all`,
`diag_all` and `held_out_12`; `--help` lists them all.

Run the held-out set with an Ollama-hosted model as the policy:

```bash
export OLLAMA_URL=http://localhost:11434/api/chat   # the default
lean_mcts/.venv/bin/python -m lean_mcts.benchmark.run \
    --tier held_out_12 --policy ollama --ollama-model gemma4:26b --daemon
```

Haskell pipeline and tests:

```bash
make help      # list pipeline targets
make all       # extract → corpus → train (includes a multi-hour Hackage build)
```

The harness smoke suite covers the Lean side; run it with the Lean venv:

```bash
PYTHONPATH=. lean_mcts/.venv/bin/python tests/harness_smoke.py
```

## Documentation

- [`docs/WRITEUP.md`](docs/WRITEUP.md) — corrected results and the audit
- [`docs/implementation/sprint31/sprint31.md`](docs/implementation/sprint31/sprint31.md) — original retrospective (superseded in part)
- [`docs/implementation/sprint30/`](docs/implementation/sprint30/) — original probe analyses (superseded in part)
- `docs/implementation/sprint{N}/` — plan, status and lessons learned for each sprint
- [`docs/theoretical/`](docs/theoretical/) — background and motivation

## License

MIT — see [`LICENSE`](LICENSE).
