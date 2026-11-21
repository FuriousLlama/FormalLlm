# Sprint 2 — Project Status

Last updated: 2026-02-21

## Overall Progress

```
[x] Clean IR definition (ir_schema.json)
[x] GHC Core cleaning pass (clean.py)
[x] Pretty-printer + type checker updates for clean IR
[x] Hackage batch extraction pipeline (extract_hackage.py)
[x] Corpus extraction run (1,487 Hackage + 162 handwritten = 1,649 total)
[x] Augmentation + tokenizer update
[x] Bug fixes (RL registry, parallelization, dep resolution, package split)
[x] Training experiments
[x] Tokenizer fix (DEF:name → DEF name split, unk handling in benchmark)
[ ] Benchmark + RL evaluation (blocked — 0/22 pass@10, see notes)
```

---

## Completed: Clean IR + Cleaning Pass (2026-02-21)

**Files created:**

- `sprint/extract/ir_schema.json` — JSON Schema for clean IR (Var, Lit, Con, Prim, App, Lam, Let, Case, Tuple + PWild pattern)
- `sprint/extract/clean.py` — GHC Core → Clean IR transformation (12 rules)

**Files modified:**

- `sprint/corpus/normalize.py` — added Prim + PWild handling in rename/serialize
- `sprint/typecheck/checker.py` — added Prim + PWild parsing/types, removed I#/\_\_DEFAULT
- `sprint/benchmark/pretty_print.py` — added Prim/PWild rendering, removed I#/$f\* workarounds
- `Makefile` — added `clean-ir` target, normalize now depends on `corpus_clean.json`

**Results (hand-written corpus):**

- 162/162 functions cleaned successfully, 0 dropped
- 0 noise violations (no I#, $f\*, \_\_DEFAULT, TyArg, Int# in output)
- Vocabulary reduced from ~350 → 298 tokens (noise tokens eliminated)
- Pretty-printing verified: `fSucc → f v0 = v0 + 1`, `fAbs → f v0 = case v0 < 0 of {False -> v0; True -> 0 - v0}`

**Pipeline:** `make extract && make clean-ir && make corpus` works end-to-end.

---

## Completed: Hackage Batch Extraction (2026-02-21)

**Files created:**

- `sprint/extract/extract_hackage.py` — batch pipeline: `cabal unpack` → `cabal build --ghc-options` → find `.dump-simpl` → `parse_ghc_core.split_bindings` → `parse_binding` → `clean_function` → `validate_no_ghc_noise` → size/dedup filtering
- `sprint/extract/packages.txt` — curated list of 25 Hackage packages (18 built successfully)

**Files modified:**

- `Makefile` — added `extract-hackage`, `corpus-merge` targets; normalize now depends on merged `corpus_all.json`

**Extraction results (25 packages attempted):**

- 18/25 packages built successfully (7 failed on dependency builds — TH, primitive, etc.)
- 338 modules scanned, 3,125/4,240 bindings parsed (**74% parse rate**)
- 1,487 unique functions extracted after cleaning + dedup + size filtering
- 0 GHC noise violations in output
- Merged corpus: 162 handwritten + 1,487 Hackage = **1,649 total functions**

**Top packages by yield:**
| Package | Functions | Modules | Parse rate |
|---------|-----------|---------|------------|
| bytestring | 452 | 31 | 76% |
| containers | 319 | 35 | 70% |
| text | 254 | 55 | 68% |
| utility-ht | 148 | 33 | 83% |
| filepath | 111 | 11 | 92% |
| unordered-containers | 102 | 9 | 78% |

**Key design decisions:**

- Used `cabal build all --ghc-options="-ddump-simpl -ddump-to-file ..."` instead of compiling individual `.hs` files (handles intra-package imports)
- `-dynamic` flag required on Arch Linux (only `.dyn_hi` files installed for base)
- Link failures are non-fatal — compilation still produces dump files per module
- Separate output files: `corpus_clean.json` (handwritten) + `corpus_hackage.json` (Hackage) + `corpus_all.json` (merged)

**Escalation status:** Parser failure rate 26% (below 30% threshold). No GHC plugin needed. Most parse failures come from complex GHC Core constructs (coercions, casts) in large packages.

**Pipeline:** `make extract-hackage && make corpus-merge && make normalize` works end-to-end (1,649 sequences).

---

## Completed: Augmentation + Tokenizer Update (2026-02-21)

**Files modified:**

- `sprint/corpus/augment.py` — removed let-reorder + eta-expand from pipeline (low yield in sprint 1), added `augment_commutative_swap()` with `_expr_span()` helper for paren-balanced argument detection
- `sprint/corpus/tokenizer.py` — added `--min-freq` CLI arg (default=3) passed to `WordLevelTrainer`; rare tokens (external function names, GHC internals) map to `<unk>`. Updated round-trip verifier to tolerate `<unk>` mappings. Fixed default paths from sprint0 → sprint.

**Augmentation results (1,649 input sequences):**

| Variant                | Count     |
| ---------------------- | --------- |
| original (after dedup) | 1,623     |
| rerename (3 seeds)     | 3,876     |
| commutative swap       | 199       |
| **Total after dedup**  | **5,698** |

- Token lengths: min=4, avg=38, max=176
- Pre-tokenizer vocab: 2,572 unique tokens (before frequency cutoff)

**Tokenizer results (min_frequency=3):**

- Vocabulary: **499 tokens** (target: 500-1000)
- Round-trip: OK for all 1,649 sequences (1,433 contain `<unk>` tokens — expected for rare Hackage names)
- Special tokens: `<pad>`, `<unk>`, `<bos>`, `<eos>`

**Notes:**

- Augmented count (5,698) slightly below 6,000 floor — rerename dedup removes more when corpus has many structurally similar Hackage functions. Could add seed 4 if needed.
- 87% of sequences contain at least one `<unk>` token — acceptable since these are external names the model shouldn't memorize anyway.
- Commutative swap fires on 199 sequences — corpus has relatively few bare `(App Prim OP ...)` patterns since many ops are nested in Case/Let.

---

## Completed: Bug Fixes (2026-02-21)

**Files modified:**

- `sprint/rl/evaluate.py` — added `registry` parameter to `evaluate_task()`, loads corpus registry in CLI, passes it to `build_haskell_source()`. Updated defaults from sprint0 → sprint paths.
- `sprint/rl/rejection_sample.py` — added `registry` parameter to `collect_winners()`, `run_round()`, `_eval_all()`. Added `ProcessPoolExecutor` for parallel `runhaskell` (separates GPU generation from CPU evaluation). New `--max-workers` arg (default 4).
- `sprint/model/train.py` — train/val split now by **package** (not function name). `ASTDataset` tracks `package` metadata. Handwritten corpus always in training split (task deps). Logs which packages are in val.
- `sprint/corpus/normalize.py` — propagates `package` field from corpus JSON to sequences.
- `sprint/corpus/augment.py` — propagates `package` field through augmented variants.

**Bug fixes applied:**

| Bug | Fix | Files |
|-----|-----|-------|
| `evaluate.py` doesn't pass corpus registry | Added `registry` param, loads from `--sequences` | evaluate.py |
| `collect_winners()` doesn't pass registry | Added `registry` param, passes to `build_haskell_source` | rejection_sample.py |
| Sequential GPU→CPU→GPU in RL | `ProcessPoolExecutor` for parallel runhaskell (generation stays sequential on GPU, evaluation parallelized on CPU) | rejection_sample.py |
| Val-set functions used as task deps | Train/val split by package; handwritten corpus force-assigned to training | train.py, normalize.py, augment.py |
| Transitive dep resolution in RL | Already implemented in `run.py:_collect_all_deps` — was blocked by missing registry in evaluate.py/rejection_sample.py | evaluate.py, rejection_sample.py |

**Train/val split verification (5,698 augmented sequences):**

- Train: 4,336 sequences from 15 packages (including handwritten)
- Val: 1,362 sequences from 3 packages (containers, data-ordlist, safe)
- Handwritten corpus (162 base functions) always in training split

---

## Completed: Tokenizer Fix — DEF Split (2026-02-21)

**Problem:** The tokenizer treated `DEF:fName` as a single token. Since individual function names rarely appeared >= 3 times, they mapped to `<unk>`. All benchmark prompts became `<bos> <unk>` — identical to the model, giving it no signal about which function to generate. Additionally, `<unk>` in the function name position was stripped during benchmark post-processing, breaking the parser.

**Root cause chain:**
1. `normalize.py` emitted `DEF:fName` as one token
2. `WhitespaceSplit` pre-tokenizer kept it as one token
3. `min_frequency=3` cutoff mapped most `DEF:fName` tokens to `<unk>` (only ~20 names appeared >= 3 times)
4. Model trained with 8.4% `<unk>` token rate, learned `<unk>` as a common default
5. Benchmark stripped `<unk>` from generated tokens, removing function names → parse failures

**Fix (4 files):**
- `sprint/corpus/normalize.py` — emit `DEF` and function name as two separate tokens
- `sprint/typecheck/checker.py` — `parse_sequence()` accepts both `DEF name` (two tokens) and legacy `DEF:name` (one token)
- `sprint/benchmark/tasks.json` — all prompts changed from `"DEF:fX"` to `"DEF fX"`
- `sprint/benchmark/run.py` — `<unk>` in function name position (after `DEF`) replaced with task's `func_name`; `<unk>` elsewhere stripped; `_extract_last_def()` updated for two-token prompts
- `sprint/model/generate.py` — default prompts updated to `"DEF fX"` format
- `sprint/typecheck/constrained_decode.py` — fixed `loss` → `val_loss` key for sprint 2 checkpoint format

**Results after fix:**
- `DEF` is now token ID 10 (appears in every sequence, always in vocab)
- Vocab size: 597 (up from 499 — more tokens survive the frequency cutoff)
- `<unk>` rate after encoding: still significant for rare Hackage function names, but structural tokens (DEF, Lam, App, etc.) are all in-vocab
- Benchmark: all 22 tasks now reach compile stage (0 parse errors, previously 100% parse errors)

---

## Completed: Training Experiments (2026-02-21)

**Files modified:**

- `sprint/model/train.py` — added `--label-smoothing` argument, passed to `nn.CrossEntropyLoss`

**Regularization sweep (100 epochs each, best of 8 configs):**

| Config | Best Val | Ratio | Notes |
|--------|----------|-------|-------|
| Baseline (d=0.1, wd=0.05, lr=1e-3) | 2.18 | 9.75x | Sprint 1 defaults |
| d=0.2, wd=0.1 | 1.96 | 7.44x | |
| d=0.3, wd=0.1 | 1.90 | 5.35x | Best from round 1 |
| d=0.3, wd=0.2 | 1.93 | 5.71x | |
| d=0.3, wd=0.2, 2-layer (1.8M params) | 1.95 | 4.62x | Smaller model, worse val |
| **d=0.3, wd=0.1, lr=3e-4** | **1.76** | 3.58x | **Best absolute val loss** |
| d=0.3, wd=0.1, lr=3e-4, LS=0.1 | 2.38 | 1.66x | Best ratio, but worse val |
| d=0.3, wd=0.1, lr=1e-3, LS=0.1 | 2.39 | 1.84x | Same story as above |

**Selected config:** dropout=0.3, weight_decay=0.1, lr=3e-4 (best absolute val loss)

**Full training run:**

- Best val loss: **1.79 nats** at epoch 10 (early stopped at epoch 110, patience=20 @ log-every=5)
- Best train loss: 0.41
- Val/train ratio: 4.34x
- Checkpoint: `sprint/model/checkpoint.pt`

**Key findings:**

- Model peaks at epoch ~10 regardless of config, then monotonically overfits
- Lower LR (3e-4 vs 1e-3) improves absolute val loss (1.79 vs 2.18) by slowing memorization
- Label smoothing (0.1) dramatically improves val/train ratio (1.66x, meets <2x target) but at the cost of worse absolute val loss (2.38 vs 1.76) — it inflates train loss rather than improving val
- Absolute val loss matters more than ratio for downstream generation quality
- `--log-every` must be small enough to capture the true val peak — logging every 50 epochs missed the best checkpoint by 40 epochs, saving a val=2.26 checkpoint instead of val=1.79

**Comparison to sprint 1:**

- Sprint 1: val=1.59, ratio=3.21x (626 sequences, split by function name)
- Sprint 2: val=1.79, ratio=4.34x (5,698 sequences, split by package)
- Val loss is slightly worse despite 9x more data — the package-based split is fundamentally harder (val packages have genuinely different code style/structure vs random function holdout)

---

## Success Criteria Tracking

| Question                                     | Minimum bar                              | Result                                  | Status                                              |
| -------------------------------------------- | ---------------------------------------- | --------------------------------------- | --------------------------------------------------- |
| Does the clean IR eliminate GHC noise?       | Zero I#, $fNumInt, wild tokens in corpus | 0 violations on 1,649 functions         | Met                                                 |
| Does Hackage extraction scale?               | >= 1,000 functions from >= 30 packages   | 1,487 from 18 packages (74% parse rate) | Partial (functions met, package count below target) |
| Does more data fix overfitting?              | Val/train ratio < 2x                     | 4.34x (best config: d=0.3, wd=0.1, lr=3e-4) | Not met (see training notes)                       |
| Does the clean IR help generation quality?   | Val loss < 1.5 nats                      | 1.79 nats (best val at epoch 10)        | Not met (1.79 vs 1.5 target, but improved from 2.18 baseline) |
| Does registry fix unblock composition tasks? | >= 3 of 6 stuck tasks produce RL winners | —                                       | Blocked (0/22 pass@10 pre-RL)                      |
| Overall benchmark pass rate                  | >= 18/22 at pass@50 after RL             | 0/22 pass@10 pre-RL                    | Blocked (see benchmark notes)                      |
| RL rounds compound                           | Improvement across >= 2 rounds           | —                                       | Blocked                                             |

---

## In Progress: Benchmark + RL Evaluation (2026-02-21)

**Benchmark results (pre-RL, pass@10, temp=0.8):**

- ORIGINAL: 0/10 tasks, 0/100 candidates (0.0%)
- NOVEL: 0/12 tasks, 0/120 candidates (0.0%)
- TOTAL: 0/22 tasks, 0/220 candidates (0.0%)
- Error breakdown: 0 parse errors, 220 compile errors, 0 wrong, 0 timeout

**What's happening:** The model generates structurally valid token sequences (pass type checker, pass constrained decode), but the pretty-printed Haskell doesn't compile. Errors include free variables, invalid function calls (`letrec`, `&`, `toInteger`), and type mismatches.

**Root cause:** The model trained on 5,701 sequences (87% Hackage, 13% handwritten). It learned Hackage-style patterns — complex functions using external library names. When generating for simple benchmark tasks (identity, double, sum), it produces Hackage-style code that references functions not available in the test module. The handwritten patterns (simple `\x -> x` style) are drowned out by Hackage data.

**This blocks RL:** With 0 passing candidates at any k, rejection sampling has no signal. RL requires at least some candidates to pass for each task.

**Possible approaches:**
1. Weight handwritten corpus higher during training (upsampling)
2. Train on handwritten corpus only (revert to sprint 1 data scale)
3. Increase k substantially (pass@200, pass@500) to find rare correct programs
4. Two-stage training: pre-train on Hackage, fine-tune on handwritten
5. Reduce temperature further / try greedy decode on training-set tasks

---

## Completed: Sprint 3 — Lambda Lifting / Dependency Injection (2026-02-21)

**Root cause addressed:** The model memorized external function names (10.6% `<unk>` rate) instead of learning structural patterns. Sprint 3 eliminates this by lambda-lifting external references so every function body is a **closed term** — no free variables, no external names in the token stream.

**Transformation:**
- External function references (Var tags) → outermost lambda binders (v0, v1, ...)
- Self-recursive calls → `SELF` token
- Deps metadata: `{"param": N, "func": "fName"}` records which param maps to which corpus function

**Files modified:**

| File | Changes |
|------|---------|
| `sprint/corpus/normalize.py` | Added `collect_free_vars()`, modified `alpha_rename()` for lambda lifting + SELF, `Renamer._self_name`, `to_sequence()` returns deps |
| `sprint/corpus/augment.py` | Propagates `deps` field through `augment_rerename()` and `augment_commutative_swap()` |
| `sprint/benchmark/pretty_print.py` | `pp_expr()`: SELF → `f` (same as self-call) |
| `sprint/typecheck/checker.py` | Extended `BINDER_TOKENS` to v0..v34 (from v0..v13) |
| `sprint/benchmark/run.py` | Added `_unlift_deps()`, `build_deps_registry()`, `_get_task_deps()`. Modified `build_corpus_registry()` to un-lift deps. Modified `build_haskell_source()` and `_build_test_expr()` for deps prefix |
| `sprint/benchmark/tasks.json` | Added `"deps": []` to all 12 novel tasks |
| `sprint/rl/evaluate.py` | Added `deps_registry` parameter to `evaluate_task()`, threaded through CLI |
| `sprint/rl/rejection_sample.py` | Added `deps_registry` parameter to `collect_winners()`, `run_round()`, `_eval_all()`, threaded through CLI |

**Results:**

| Metric | Before (Sprint 2) | After (Sprint 3) |
|--------|-------------------|-------------------|
| Free Var references in token streams | Many (external function names) | **0** |
| Sequences with `<unk>` tokens | 1,433/1,649 (87%) | **30/6,551 (0.5%)** |
| SELF token usage | N/A | 321/1,649 sequences (435 occurrences) |
| Sequences with deps | N/A | 1,234/1,649 (75%) have external deps |
| Total dep entries | N/A | 2,646 |
| Body vocabulary (unique tokens) | 2,572 | **279** |
| Tokens surviving min_freq=3 | 499 | **140** (body only) |
| Tokenizer vocab | 597 | **1,549** (includes all constructors) |
| Augmented corpus size | 5,698 | **6,551** |

**Key insight:** Lambda lifting completely eliminates external function names from the token stream. The model now sees only structural tokens (Lam, App, Case, Prim, Con, etc.), bound variables (vN), SELF, and constructors. The vocabulary is purely structural — the model can only learn algorithmic patterns.

**Pipeline:** `make extract && make corpus` works end-to-end. Next step: `make train` to retrain with the clean vocabulary, then `make benchmark` and `make rl`.
