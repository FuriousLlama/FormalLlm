# Sprint 0 — FormalLlm End-to-End Pipeline

Thin proof-of-loop: typed ASTs from real Haskell → transformer training → type-constrained generation → execution → rejection-sampling fine-tuning.

## Setup

```bash
# From repo root
source .venv/bin/activate
```

## Pipeline Steps

```
extract/ → corpus/ → model/ → typecheck/ → benchmark/ → rl/
```

Run each step in order — see component READMEs and CLAUDE.md for specs.

## GPU

Local: RTX 5090 (32GB). Set `CUDA_VISIBLE_DEVICES=0` for single-GPU training.

## Dependencies

- Python 3.14 + PyTorch 2.10.0+cu128
- GHC 9.6.6 + runhaskell
- HuggingFace tokenizers
