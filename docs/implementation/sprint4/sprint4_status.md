# Sprint 4 — Project Status

Last updated: 2026-02-23 (Pillar 3 — RL run completed, 2/38 novel tasks at pass@10, oscillation observed)

## Overall Progress

```
[x] Pillar 1 — Data Scale
    [x] Update clean.py: filter GHC artifact vars, promote comparison ops to Prim
    [x] Fix parse_ghc_core.py: CLIT token for unboxed char literals (parser hang fix)
    [x] Expand packages.txt from 32 to 202 curated packages
    [x] Run full 202-package extraction (2,574 functions — below target, cascade blockers)
    [x] Root-cause cascade failures: global cabal config fix (library-vanilla:False shared:True)
    [x] Validate fix: tagged + contravariant now build and produce dumps
    [x] Build tooling: pkg_status.py, --status-out, dep-ordered extraction (build_waves)
    [x] Refactor extract_hackage.py: 3-phase pipeline (pkg_raw/ → pkg_clean/ → corpus)
    [x] Re-run full extraction with fixed config: 4,277 hackage + 161 handwritten = 4,438 total
[x] Pillar 2 — De Bruijn Indices
    [x] Rewrite normalize.py: DeBruijnRenamer, debruijn_convert()
    [x] Simplify augment.py: remove re-alpha-rename (identity under de Bruijn)
    [x] Rebuild tokenizer.py: target ~100-200 token vocabulary (actual: 120 at min_freq=25)
    [x] Update checker.py: replace BINDER_TOKENS v0-v34 with de Bruijn index tokens
    [x] Update constrained_decode.py: de Bruijn vocabulary
    [x] Update pretty_print.py: de Bruijn → Haskell variable names
[ ] Pillar 3 — Honest Evaluation
    [x] Expand tasks.json: 22 Tier 1 + 10 Tier 2 + 6 Tier 3 tasks (38 total)
    [x] Update run.py: tiered evaluation, batch GPU generation, parallel Haskell eval, few-shot prompting
    [x] Add right-sized model config to train.py (small=2L/d128/~450K, big=4L/d256/~3.3M)
    [x] Modify ASTDataset for multi-function context training (few-shot ICL preparation)
    [x] Retrain with few-shot training enabled (epoch=2600, train=0.4066, val=0.3202, ratio=0.79x)
    [x] Benchmark run: 0/38 tasks — ICL failure diagnosed (random concat doesn't teach ICL)
    [x] Fix rejection_sample.py: <unk> substitution + EOF clipping in collect_winners; bootstrap corpus-mix in run_round
    [x] RL run (3 rounds × 100 epochs, checkpoint_rl.pt saved)
    [x] Analysis + status writeup
```

---

## Pillar 1 — Data Scale

### clean.py Changes (complete)

**1. GHC artifact var filtering** — Added `GHC_ARTIFACT_VARS = frozenset({"letrec", "cast", "$trModule1", "primFixed"})`. 250 → 0 GHC artifact Var refs.

**2. Standalone comparison op promotion** — `<`, `>`, `>=` appearing as standalone `Var` nodes now return `Prim` nodes. 222 → 0 comparison op Var refs.

### parse_ghc_core.py Fix (complete)

**Critical bug fix**: Unboxed char literals like `'('#` were tokenized as `LP` (left paren) because `'` was silently skipped. The parser then entered an infinite loop in `parse_pat`'s "tuple pattern" branch, looking for a matching `RP` that never appeared. This caused two worker processes to balloon to 46GB RAM each.

Fix: added `CLIT` token type matching `'X#'` patterns, handled in `parse_atom`, `parse_pat`, and `can_start_atom`. Also added per-binding 5-second `SIGALRM` timeout in `extract_module` as a safety net.

### extract_hackage.py Changes (complete)

- **JSONL streaming**: results streamed to `.jsonl.tmp` as packages complete (no in-RAM accumulation)
- **`-fforce-recomp`**: added to `GHC_DUMP_FLAGS` so cached builds are forced to regenerate dump files
- **`--limit N`**: batch testing mode (e.g., `--limit 50`)
- **`--parallel N`**: concurrent package extraction (`ProcessPoolExecutor`)
- **directory reuse**: `unpack_package` reuses existing `hackage-packages/<pkg>/` dirs
- **per-binding timeout**: 5-second `SIGALRM` per binding in `extract_module`

### Extraction Results (complete)

**Full re-extraction with 3-phase pipeline completed.** Results with cabal config fix + new pipeline:

| Metric                   | Sprint 3 | Sprint 4 target | Sprint 4 actual |
| ------------------------ | -------- | --------------- | --------------- |
| Base functions (hackage) | 1,649    | 10,000-15,000   | **4,277**       |
| Base functions (total)   | 1,649    | 10,000-15,000   | **4,438**       |
| Packages compiled        | 18       | 100-200         | **137**         |
| Augmented sequences      | 6,551    | 30,000-45,000   | TBD             |
| Training tokens          | ~250K    | ~2-3M           | TBD             |
| GHC artifact deps        | 241      | 0               | **0** ✅        |
| Comparison op deps       | 221      | 0               | **0** ✅        |

**AST node size distribution (pkg_clean, pre-filter):**

| Size (AST nodes)     | Count | %                    |
| -------------------- | ----- | -------------------- |
| Tiny (<5)            | 2,032 | 28.9% — filtered out |
| Small (5–10)         | 2,419 | 34.4%                |
| Medium (10–25)       | 1,405 | 20.0%                |
| Large (25–100)       | 990   | 14.1%                |
| Very large (100–300) | 167   | 2.4%                 |
| Huge (>300)          | 18    | 0.3% — filtered out  |

### Why 4,277 not 10,000

137 packages compiled (vs 62 initially). The cabal config fix unlocked 75 additional packages. Diminishing returns: many packages in the list are narrow (typeclass utilities, newtypes, thin wrappers) that produce few extractable functions even when they build. Adding more packages will yield less per package than the initial 137.

The 10,000 target assumed all 202 packages would yield functions at the rate of the initial seed corpus. In practice, the high-yield packages (bytestring, containers, algebraic-graphs) are already in the corpus.

### 3-Phase Pipeline

The extraction pipeline was refactored into three independent phases with per-package intermediate files:

**Phase 1** (`make extract-hackage-build`): cabal unpack → build → parse GHC Core → `sprint/extract/pkg_raw/<pkg>.json`

- Stores raw GHC Core ASTs (pre-clean)
- Re-runs skip already-built packages (fast incremental re-runs)

**Phase 2** (`make extract-hackage-clean`): read pkg_raw/ → apply clean.py → `sprint/extract/pkg_clean/<pkg>.json`

- Stores clean IR entries (pre-size-filter)
- Re-run this phase alone when clean.py changes: `make extract-hackage-clean`

**Phase 3** (`make extract-hackage-corpus`): read pkg_clean/ → size filter → global dedup → `corpus_hackage.json`

- Always fast (pure in-memory assembly)
- Re-run to change `--min-tokens`/`--max-tokens` without rebuilding

Benefits: no parallel JSONL corruption (each package owns its file), incremental re-runs, inspectable intermediates.

---

## Files Modified

| File                                     | Change                                                                                                                                                                                                                                                         |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sprint/extract/clean.py`                | GHC_ARTIFACT_VARS; Var handler promotes standalone comparison ops to Prim                                                                                                                                                                                      |
| `sprint/extract/parse_ghc_core.py`       | CLIT token for unboxed char literals; per-binding timeout                                                                                                                                                                                                      |
| `sprint/extract/extract_hackage.py`      | Full rewrite: 3-phase pipeline (build→pkg_raw/, clean→pkg_clean/, corpus assembly); `--phase {build,clean,corpus,all}`; `--pkg-raw-dir`; `--pkg-clean-dir`; `--force`; atomic per-package cache writes; iterative AST node counter for size filter             |
| `sprint/extract/packages.txt`            | Expanded from 32 to 202 curated Hackage packages                                                                                                                                                                                                               |
| `sprint/extract/dep_graph.py`            | Added `build_waves()`, `topological_order()`, `--order` CLI mode                                                                                                                                                                                               |
| `sprint/extract/pkg_status.py`           | **New**: per-package status reporter (filesystem + corpus + run stats → markdown report)                                                                                                                                                                       |
| `~/.config/cabal/config`                 | `library-vanilla: False`, `shared: True`, `executable-dynamic: True` — fixes Arch Linux cascade                                                                                                                                                                |
| `.gitignore`                             | Added `hackage-packages/`                                                                                                                                                                                                                                      |
| `Makefile`                               | PARALLEL/BATCH/BUILD_ORDER/PKG_STATUS/MODEL_SIZE vars; `dep-order`, `pkg-status`, `extract-hackage-batch`, `train-small`, `train-big` targets                                                                                                                  |
| `sprint/corpus/normalize.py`             | DeBruijnRenamer with mutable push/pop stack; `debruijn_convert()`; lambda-lifting for free vars; `deps` metadata                                                                                                                                               |
| `sprint/corpus/augment.py`               | Removed re-alpha-rename (identity under de Bruijn); kept commutative swap; dedup by token sequence                                                                                                                                                             |
| `sprint/corpus/tokenizer.py`             | Rebuilt vocabulary: 120 tokens (4 specials + 36 always-keep + 80 freq≥25); removed named binder tokens                                                                                                                                                         |
| `sprint/typecheck/checker.py`            | Replaced BINDER_TOKENS v0-v34 with de Bruijn index tokens; accept any token as constructor name                                                                                                                                                                |
| `sprint/typecheck/constrained_decode.py` | Updated for de Bruijn vocabulary; fixed `<eos>` spurious Var issue                                                                                                                                                                                             |
| `sprint/benchmark/pretty_print.py`       | De Bruijn → Haskell variable name rendering; push-order inversion for binder stack                                                                                                                                                                             |
| `sprint/benchmark/tasks.json`            | Expanded 22→38 tasks: all 22 with `tier:1`, 10 new Tier 2 compositional, 6 new Tier 3 structural; all tasks have `few_shot_funcs`                                                                                                                              |
| `sprint/benchmark/run.py`                | Tiered evaluation; `generate_batch()` for parallel GPU generation; `ThreadPoolExecutor` for parallel Haskell eval; `build_few_shot_prompt()` now active; `--tier`, `--workers` args                                                                            |
| `sprint/model/generate.py`               | Added `generate_batch()` — k candidates in one (k,T,V) GPU pass                                                                                                                                                                                                |
| `sprint/model/train.py`                  | `MODEL_PRESETS` (small/big); `--model-size` arg; `ASTDataset` few-shot training (concatenates 2-3 sequences, `--few-shot-prob`, `--few-shot-k`)                                                                                                                |
| `sprint/rl/rejection_sample.py`          | Fixed `collect_winners`: substitute `task["name"]` for `<unk>` in name position, clip at EOF (was filtering `<unk>` → parse failure on every candidate); fixed `run_round`: always call `finetune` with corpus mix when 0 winners instead of skipping entirely |

---

## Success Criteria Tracking

| Question                            | Minimum bar                     | Result                                                      | Status |
| ----------------------------------- | ------------------------------- | ----------------------------------------------------------- | ------ |
| Does data scale fix overfitting?    | Val/train ratio < 3x            | **0.79x** (epoch 2600, few-shot train)                      | ✅     |
| Does de Bruijn shrink vocabulary?   | < 200 tokens                    | **120 tokens** (min_freq=25)                                | ✅     |
| Does right-sized model generalize?  | Val loss < 2.0 nats             | **0.320** nats                                              | ✅     |
| Does model solve Tier 1?            | >= 18/22 at pass@1 greedy       | **0/22** — ICL fails (random concat training)               | ⏳     |
| Does model compose (novel tasks)?   | >= 3/10 novel tasks at pass@10  | **2/28** (count_true, negate_all) after RL                  | ⚠️     |
| Does model generalize (structural)? | >= 1 structural task at pass@10 | **0** — same root cause                                     | ⏳     |
| RL has signal?                      | Improves novel tasks            | **Yes** — 2 tasks reach pass@10=1.0, unstable across rounds | ⚠️     |
| GHC artifact deps eliminated?       | 0 letrec/cast/$trModule1 deps   | **0** on existing corpus                                    | ✅     |
| Comparison ops promoted to Prim?    | All <, >, >= as Prim            | **222 → 0** Var refs                                        | ✅     |
| Data scale — 10x?                   | >= 5,000 functions              | **4,438** (~2.7x)                                           | ⚠️     |
| Cascade parser hang fixed?          | No OOM in extraction            | Fixed (CLIT token)                                          | ✅     |

---

## Corpus Diversity Analysis

Post-extraction analysis of `corpus_all.json` (2,574 functions):

| Metric                    | Result                                                                   |
| ------------------------- | ------------------------------------------------------------------------ |
| Unique by AST body        | 2,568 / 2,574 (99.8%)                                                    |
| Duplicate bodies          | 6 — all trivial (`flip`, `const`, `fst3`, `mapFst`, `mapSnd`, `reverse`) |
| Unique function names     | 1,957 (617 name-collisions across packages, all have different bodies)   |
| Within-package duplicates | 0 — every package is 100% unique internally                              |

**Diminishing returns is not a duplication problem.** Every package contributes fully distinct code. The concern is structural diversity: 55% of functions have ASTs with ≤10 nodes (tiny/small), and 72.7% have `Lam` as the top-level tag. More packages would add more of the same shallow patterns.

**AST size distribution:**

| Size            | Count | %     |
| --------------- | ----- | ----- |
| Tiny (≤3 nodes) | 268   | 10.4% |
| Small (4–10)    | 1,154 | 44.8% |
| Medium (11–30)  | 819   | 31.8% |
| Large (31–100)  | 333   | 12.9% |
| Huge (>100)     | 0     | 0%    |

---

## Cascade Root Analysis

### State before the fix (initial extraction run)

| Outcome                                           | Count |
| ------------------------------------------------- | ----- |
| Succeeded (functions extracted)                   | 62    |
| Built with dump files, 0 functions extracted      | 17    |
| No dump files (build/cascade failed)              | 97    |
| Never downloaded (not in Hackage or packages.txt) | 25    |

### Root cause: one bug, not two mechanisms

The initial analysis described a "catch-22" between two distinct failure mechanisms. Post-investigation revealed both stem from the **same bug**: passing `-dynamic` via `--ghc-options` to cabal.

When cabal receives `-dynamic` in `--ghc-options`:

1. It interprets the user's intent as "pure dynamic mode"
2. It suppresses its own internal `-dynamic-too` flag (which normally generates both `.o` and `.dyn_o`)
3. GHC compiles in dynamic-only mode — but cabal's build system names the output `.o` (not `.dyn_o`)
4. Cabal's linker then tries to build a `.so` and looks for `.dyn_o` files → link failure

This caused ALL 97 no-dump-file failures. The Mechanism A / Mechanism B framing was accurate about the symptoms (`.dyn_o` missing vs missing base modules) but wrong about the cause — both were downstream of the same `-dynamic` interference.

### The fix (applied 2026-02-22)

Global cabal config (`~/.config/cabal/config`):

```
library-vanilla: False
shared: True
executable-dynamic: True
```

This tells cabal to use dynamic-only mode at the project level, without any interference with its internal `-dynamic-too` / way-management. With this config:

- cabal correctly uses the "dyn way" internally
- GHC produces `.dyn_o` and `.dyn_hi` files
- Dump files are written as `.dyn.dump-simpl` (already matched by `*.dump-simpl` glob)
- No explicit `-dynamic` needed in `GHC_DUMP_FLAGS`

**Validation**: `tagged-0.8.10` and `contravariant-1.5.6` (plus its `StateVar` dependency) all build cleanly. `adjunctions` previously yielded 0 functions; after the fix it yields 7.

### Packages with dumps but 0 extracted (17 packages)

These have dump files but all bindings are filtered out (typeclass instances, newtypes, trivial wrappers):

`assoc`, `contravariant`, `data-default`, `data-default-instances-base`, `data-has`, `first-class-families`, `hex`, `microlens`, `microlens-mtl`, `mtl`, `newtype-generics`, `path`, `path-io`, `tagged`, `text-short`, `type-equality`, `void`

Note: `microlens`, `text-short`, and `tagged` actually have extractable functions when run manually (19, 23, 20 respectively) — they were in the corpus generated before those dump files existed. The re-extraction run will capture them.

---

## New Tooling (2026-02-22)

### `pkg_status.py` — per-package status reporter

```bash
make pkg-status   # prints markdown report to stdout
python sprint/extract/pkg_status.py --out report.md  # write to file
```

Synthesises state from three sources without requiring a fresh extraction run:

- `packages.txt` — desired package list
- `hackage-packages/` filesystem — downloaded / built / has dumps
- `corpus_hackage.json` — extraction results (function counts)
- `pkg_status.json` — optional per-run stats (parse rates, build errors) from `--status-out`

Output sections: EXTRACTED | ZERO YIELD | BUILD FAILED | NOT DOWNLOADED. Shows function counts, dump file counts, and parse rates where available.

### `dep_graph.py --order` — dependency-ordered build plan

```bash
make dep-order   # writes sprint/extract/build_order.json
```

Computes topological waves (Kahn's algorithm restricted to `packages.txt`). Wave 0 = packages with no intra-list dependencies (can all build in parallel). Each subsequent wave starts only after the previous wave's cabal builds are in the store.

Current wave structure: **10 waves**, wave 0 = 105 packages, remaining 9 waves total 96 packages.

### Wave-aware extraction

`extract_hackage.py --build-order sprint/extract/build_order.json` processes packages wave by wave:

- Packages within a wave: parallel (up to `--parallel N`)
- Between waves: sequential (ensures deps are in the cabal store before dependents build)

This eliminates races where two packages simultaneously try to build the same dependency, and avoids redundant rebuilds when the store is already populated.

---

---

## Pillar 3 — Honest Evaluation

### Model Training (complete — two runs)

Right-sized model (`small` preset: 2L/d128/nhead4/~450K params) trained on 5,078 augmented sequences with 120-token vocab.

**Run 1** — single-sequence training (baseline, before ICL diagnosis):

| Metric          | Sprint 3 | Sprint 4 target | Sprint 4 actual        |
| --------------- | -------- | --------------- | ---------------------- |
| Val/train ratio | 14.07x   | < 3x            | **1.56x** ✅           |
| Train loss      | —        | —               | **0.6579**             |
| Val loss        | —        | < 2.0           | **1.0282** ✅          |
| Vocab size      | ~1,549   | < 200           | **120** ✅             |
| Best epoch      | —        | —               | **50** (early stopped) |

**Run 2** — few-shot training (`few_shot_prob=0.5`, `few_shot_k=2`):

| Metric          | Run 1  | Run 2 (few-shot)        |
| --------------- | ------ | ----------------------- |
| Val/train ratio | 1.56x  | **0.79x** (val < train) |
| Train loss      | 0.6579 | **0.4066**              |
| Val loss        | 1.0282 | **0.3202**              |
| Best epoch      | 50     | **2600**                |

Val < train (ratio < 1.0) because few-shot training examples (multi-function concatenations) are harder than single-function val examples. This is expected and desirable: val loss decreasing absolutely (1.028 → 0.320 nats) means the model is learning richer structure.

### Benchmark Tasks (expanded)

| Tier   | Description                            | Count | Target metric          |
| ------ | -------------------------------------- | ----- | ---------------------- |
| Tier 1 | Regression (training corpus functions) | 22    | pass@1 greedy >= 18/22 |
| Tier 2 | Compositional (novel compositions)     | 10    | pass@10 >= 3/10        |
| Tier 3 | Structural (novel patterns)            | 6     | pass@10 >= 1/6         |

All 38 tasks have `few_shot_funcs` (2-3 structurally related functions to show as context).

### Benchmark Infrastructure

- **Batch GPU generation**: `generate_batch()` in generate.py — k=10 candidates in one forward pass of shape (k, T, V) instead of 10 serial (1, T, V) passes. ~10x inference speedup.
- **Parallel Haskell eval**: `ThreadPoolExecutor(workers=8)` runs all candidate evaluations concurrently.
- **Tiered reporting**: run.py groups results by tier, reports pass@k per tier.
- **Few-shot prompting**: `build_few_shot_prompt()` now active for all tasks (was dead code in sprint 1-3).

### Benchmark Run (0/38 — ICL failure)

Benchmark ran to completion after fixing two blocking bugs:

1. `constrained_decode.py` didn't clip `ids` to `max_len=256` — few-shot prompt (up to 73 tokens) + 200 generated = 273 > 256 → CUDA out-of-bounds. Fixed.
2. `run.py` passed full token stream (context + generated) to `tokens_to_haskell`, which parsed the first few-shot example's body (not the target). Fixed: extract only `prompt_ids[-2:] + gen_ids` clipped at first EOF.

After fixes: **0/38 tasks pass**. Verbose analysis showed the model generates structurally wrong bodies regardless of the few-shot context (e.g., `fDouble` generates Case expressions instead of `Prim + Var 0 Var 0`). Verified with 50 direct-prompt samples: correct body never appears.

**Root cause: random concatenation doesn't teach ICL.** Training with random sequence groupings means the model learned to _ignore context_ — there is no structural relationship between randomly paired sequences, so the model's loss is minimized by ignoring the context and generating from its global prior. At inference, even with a carefully chosen few-shot context, the model ignores it.

ICL requires _structured_ groupings during training (related functions, same structural class). Random pairs give the model no signal about how context should influence the continuation.

### RL Fixes Applied

Two bugs in `rejection_sample.py` prevented RL from running:

**Bug 1 — `collect_winners` filtered `<unk>` → 0 candidates per task**

Token extraction: `[DEF, <unk>, ...body..., <eos>]` with `<unk>` filtered → `[DEF, ...body..., <eos>]`. Then `parse_sequence` consumed `DEF`, then tried to consume the function name → got the first body token (e.g. `LAM`) as name → `ParseError` → `continue` → 0 candidates → 0 winners every round.

Fix: substitute `task["name"]` for `<unk>` in the name position (same pattern as `_clean_tokens` in run.py), clip at first EOF token.

**Bug 2 — `run_round` skipped `finetune` entirely with 0 winners → RL deadlock**

The original code: `if total_winners == 0: return _eval_all(...)` — if the model never generates a correct body, it is never fine-tuned, its distribution never changes, winners remain 0 forever.

Fix: when `total_winners == 0` and `mix_corpus=True` (default), still call `finetune([], corpus_seqs, ...)`. The `finetune` function already handles empty winner lists with `mix_corpus=True` — it trains on corpus sequences only. This reshapes the model's distribution over rounds even without model-generated winners, allowing winners to eventually appear.

**RL is now unblocked.** Run with `make rl`.

---

## RL Run Results (2026-02-23)

**Command**: `rejection_sample.py --k 10 --eval-k 10 --temperature 0.8 --epochs 100 --rounds 3`

**Starting checkpoint**: `checkpoint.pt` (epoch=2950, train=0.4038, val=0.3157)

### Summary

| Round    | Total winners                    | Novel tasks passing (pass@10) |
| -------- | -------------------------------- | ----------------------------- |
| Baseline | —                                | 0/28                          |
| Round 1  | 1 (negate_all)                   | 2/28 (count_true, negate_all) |
| Round 2  | 1 (count_true)                   | 0/28 — both drop to 0         |
| Round 3  | 3 (null, count_true, negate_all) | 2/28 (count_true, negate_all) |

**Final checkpoint** (`checkpoint_rl.pt`): count_true=pass@10 1.0, negate_all=pass@10 1.0, all others 0.

### Key Observations

**1. RL has signal but it is weak and unstable.** Two novel tasks (count_true and negate_all) reach pass@10=1.0 after fine-tuning, but the wins oscillate — they appear and disappear across rounds. The winner pool is always tiny (1–3 winners out of 38 tasks per round), so fine-tuning is essentially one-shot on a single example with corpus mix, which reshapes the distribution inconsistently.

**2. Non-novel Tier 1 tasks never pass.** All 10 tasks with `novel=false` (identity, double, successor, not, null\*, sum, length, tail, map_double, filter_positive) score 0 across all rounds. This is counterintuitive — these functions are in the training corpus and should be easiest. (\*null generates 1 winner in round 3 collection but drops to 0 after fine-tuning.)

The likely cause: these tasks use few-shot prompts from corpus neighbours (e.g., identity is prompted with `[fFst, fSnd]`). The model trained with random groupings ignores context entirely and samples from its global prior. For corpus functions, the prior's mode may land on a structurally similar but incorrect completion — or the temperature=0.8 / k=10 sample budget never hits the correct short sequence.

**3. Novel tasks pass first.** negate_all (`map negate`) and count_true (`length . filter id`) both have simple bodies expressible as compositions of corpus primitives. The model's prior occasionally generates these by chance; once they appear as winners, fine-tuning reinforces them temporarily.

**4. Oscillation is a fine-tuning instability.** With 1 winner and corpus mix, each round does ~100 epochs of fine-tuning weighted toward the winner. This overfits slightly to the winner's pattern, breaking the distribution for other tasks, which then lose their pass@10=1.0 status. The small model size (450K params) amplifies this: there is no slack to retain old patterns while learning new ones.

### Fine-tune loss progression

| Round | Start loss    | End loss       | Best loss |
| ----- | ------------- | -------------- | --------- |
| 1     | 0.3717 (ep20) | 0.3572 (ep100) | 0.3572    |
| 2     | 0.3573 (ep20) | 0.3534 (ep100) | 0.3511    |
| 3     | 0.3506 (ep20) | 0.3496 (ep100) | 0.3465    |

Loss is decreasing monotonically across rounds (3 rounds total reduction: 0.4038 → ~0.347). The model is learning, but the learning signal is too narrow to produce broad task coverage.

### Implications for next steps

The RL loop is functional. The constraint is signal density: with 1–3 winners per round out of 38 tasks, RL is essentially fine-tuning on 1 example at a time. Options ranked by expected impact:

1. **More rounds** (10–20): winners accumulate slowly; the round 3 summary shows 3 winners, which is already better than rounds 1–2. More rounds may compound.
2. **Investigate non-novel task failure**: generate verbose output for `identity` (the simplest possible task) to confirm whether the model is generating structurally invalid IR, wrong-length sequences, or just the wrong body. This diagnosis is cheap and high-value.
3. **Lower temperature or increase k**: temp=0.8 with k=10 gives ~10 draws from a broad distribution. k=50 at temp=0.6 would increase coverage of the correct sequence without changing the model.
4. **Retrain without few-shot context**: the single-sequence model (run 1, val=1.028) never got a benchmark run. It lacks ICL but should at least solve the non-novel Tier 1 tasks from memory, establishing a floor.

---

## Known Blockers

1. **ICL failure — random concatenation doesn't teach in-context learning**: The few-shot retrain (Run 2) used random sequence groupings. The model learned to ignore context (no structural relationship between randomly paired sequences). At inference, few-shot prompts are ignored. RL provided weak signal (2 novel tasks pass@10=1.0, unstable). Remaining options:
   - (a) More RL rounds (10–20) to accumulate winners
   - (b) Verbose diagnosis of `identity` failure (cheapest — confirm root cause)
   - (c) Benchmark the run-1 checkpoint (single-sequence, no ICL) to establish a floor on non-novel Tier 1 tasks
   - (d) Structured groupings: group training sequences by structural similarity so context has real signal

2. **4,438 functions vs 10,000 target**: The cabal fix raised yield from 2,574 to 4,438. Remaining gap is a data diversity problem (thin wrappers/typeclasses), not a pipeline problem. Acceptable for training.

3. **~20 packages not downloaded**: Some names don't exist on Hackage (leftist-heaps, pairing-heap, psqueue, skew-heap, sortedkey, string-extra, swap, validation-class). Stale entries in packages.txt.
