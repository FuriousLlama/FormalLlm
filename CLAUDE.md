# FormalLlm

Type-directed program synthesis research: a Haskell / GHC Core pipeline
(`sprint/`, sprints 0–10) and a Lean 4 MCTS synthesis system (`lean_mcts/`,
sprints 11–30). Overview and results: `README.md`.

## Project Goal

Latest sprint: 31 (documentation-only retrospective) — `docs/implementation/sprint31/sprint31.md`
Last experimental sprint: 30 — plan `docs/implementation/sprint30/sprint30.md`, status `docs/implementation/sprint30/sprint30_status.md`

## Directory Structure

```
sprint/                           # Haskell / GHC Core pipeline (S0–S10)
├── extract/
│   ├── Functions.hs              # hand-written functions (seed corpus)
│   ├── Compositions.hs           # hand-written compositions
│   ├── parse_ghc_core.py         # GHC Core dump → AST
│   ├── clean.py                  # raw Core → clean IR
│   ├── extract_hackage.py        # batch Hackage extraction (JSONL streaming, waves)
│   ├── dep_graph.py              # dependency-aware build order
│   ├── pkg_status.py             # per-package status reporter
│   ├── packages.txt              # curated Hackage package list
│   └── corpus_*.json             # GENERATED (corpus_hackage/corpus_all are not tracked)
├── corpus/                       # normalize.py (de Bruijn), augment.py, tokenizer.py
├── model/                        # train.py, generate.py; checkpoints are not tracked
├── typecheck/                    # checker.py, constrained_decode.py
├── benchmark/                    # tasks.json (38 tasks), run.py, pretty_print.py
│   └── results/                  # S30 probe outputs
└── rl/                           # rejection_sample.py, evaluate.py

lean_mcts/                        # Lean 4 synthesis (S11–S30); own venv (Python 3.12)
├── env/                          # pantograph_daemon.py, pantograph_client.py, FormalNav/ (Lean project)
├── search/                       # mcts.py, policy.py, *_candidates.py (skeletons), goal_analyzer.py
├── eval/                         # oracle.py
├── benchmark/                    # run.py, tasks.py, phase_a/b/c probes, sanity_check.py
├── synth/                        # enumeration, self-play, data generation; expressions.jsonl (650 EF expressions)
└── train/                        # LoRA training scripts; adapters are not tracked

tests/                            # pytest unit tests + harness_smoke.py
```

## Status & References

Current status: `docs/implementation/sprint30/sprint30_status.md`
Lessons learned (most recent): `docs/implementation/sprint29/lessons_learned.md`
After changes:

- Update the status file with what was done and what needs to be done acording to the sprint roadmap.
- Update the lessons learned with non-obvious findings, gotchas.

Previous sprints: `docs/implementation/sprint{N}/`.

## Key Notes

### Arch Linux + cabal dynamic linking (critical)

`~/.config/cabal/config` must have:

```
library-vanilla: False
shared: True
executable-dynamic: True
```

Without this, cabal tries to build static libraries, fails because Arch's GHC only ships `.dyn_hi` files for boot packages, and cascades to ~97 package build failures. Do **not** pass `-dynamic` via `--ghc-options` — that interferes with cabal's internal `-dynamic-too` mechanism and produces the same symptoms for a different reason.

### Dump file naming on Arch

With the global dynamic config, GHC Core dumps are written as `*.dyn.dump-simpl` (not `*.dump-simpl`). The `*.dump-simpl` glob matches both, so `find_dump_files()` in `extract_hackage.py` works unchanged.

### Key Constraints

- Same infrastructure: laptop + 1 GPU, Python + PyTorch
- No new dependencies beyond `cabal-install` (already required since sprint 2)
- Still disposable code — production quality is Stage 0+
- Training should complete in < 2 hours for the right-sized model (~500K params)
- No Docker, no infrastructure, no production concerns

## Running the Pipeline

A `Makefile` at the repo root drives all pipeline stages. Each target rebuilds only what's out of date.

```bash
make help          # list all targets
make all           # full pipeline: extract → corpus → train
make extract       # GHC Core → sprint/extract/corpus_raw.json (hand-written functions)
make extract-hackage  # batch Hackage extraction → corpus_raw.json (2000-5000 functions)
make corpus        # normalize + augment + tokenize → sequences.json + tokenizer.json
make train         # train transformer → sprint/model/checkpoint.pt
make generate      # greedy decode from checkpoint (default PROMPT=DEF:fId)
make generate PROMPT=DEF:fMap   # decode a specific function
make benchmark     # run all 22 benchmark tasks
make rl            # multi-round rejection sampling fine-tuning
make clean         # remove all generated artifacts
make clean-model   # remove checkpoint only
```

The Makefile uses `.venv/bin/python` directly — no `source .venv/bin/activate` needed.

### extract/

```bash
make extract
# Output: sprint/extract/corpus_raw.json  (hand-written functions, cleaned)

make extract-hackage
# Output: sprint/extract/corpus_raw.json  (2000-5000 functions from Hackage)
# Runs: cabal unpack → ghc -ddump-simpl → parse → clean → validate
# Expected: "[extract] N functions passed validation from M packages (K skipped)"
```

**GHC flags used:** `-O0 -fforce-recomp -ddump-simpl -v0 -dsuppress-idinfo -dsuppress-module-prefixes -dsuppress-uniques -dynamic`

Key behaviour notes (inherited from sprint0):

- GHC Core dump goes to **stdout** (not stderr); captured via `subprocess.run(capture_output=True)`
- `-dynamic` is required on Arch Linux because only `.dyn_hi` interface files are installed for `base`
- `-v0` suppresses compile progress noise so only the Core dump reaches stdout
- `-fforce-recomp` ensures the dump is always regenerated even if the `.o` file is up to date

### corpus/

```bash
make corpus
# Output: sprint/corpus/sequences.json      (2000-5000 raw sequences)
#         sprint/corpus/sequences_aug.json   (6000-15000 augmented)
#         sprint/corpus/tokenizer.json       (vocab size ~500-1000)
```

### model/

```bash
make train
# Output: sprint/model/checkpoint.pt
# Tracks both training loss and validation loss
# Split by package, early stopping on val loss

make generate PROMPT=DEF:fFoldr
# Greedy decode from the trained checkpoint
```

### benchmark/

```bash
make benchmark
# Runs all 22 tasks, reports pass@k for original and novel tasks separately
```

### rl/

```bash
make rl
# Multi-round rejection sampling with parallel runhaskell
# Reports pass@k after each round
```

### lean_mcts/

Run from the repo root with the Lean venv (`lean_mcts/.venv`, Python 3.12):

```bash
lean_mcts/.venv/bin/python -m lean_mcts.env.pantograph_daemon --workers 4
lean_mcts/.venv/bin/python -m lean_mcts.benchmark.run --tier t2 --policy expanded-fixed --daemon
make smoke         # harness smoke suite
```

Restart the daemon after `lake build`, after adding a Lean file to `FormalNav.lean`,
or after changing `ALL_IMPORTS` in `pantograph_daemon.py`.

The Ollama policies read their endpoint from `OLLAMA_URL`
(default `http://localhost:11434/api/chat`) or `--ollama-url`.

## Dependencies

- Python 3.14 + PyTorch 2.10.0+cu128 (RTX 5090, CUDA 13.1 driver)
- HuggingFace `tokenizers` 0.22.2
- GHC 9.6.6 + runghc (from `extra/ghc` pacman package)
- `cabal-install` (from `extra/cabal-install` pacman package) — NEW for sprint 2
- `uv` for venv management (`extra/uv` pacman package)

Install:

```bash
sudo pacman -S uv ghc cabal-install
uv venv .venv --python 3.14
source .venv/bin/activate
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
uv pip install tokenizers numpy
cabal update  # fetch Hackage package index
```
