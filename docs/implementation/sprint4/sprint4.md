# Sprint 4: Honest Generalization

The fifth iteration of [[ast_pipeline_roadmap.md]].
Core objective: fix the params/data mismatch that causes memorization, introduce de Bruijn indices to shrink vocabulary, and build an honest evaluation suite that measures generalization instead of recall.

## Sprint 4: Is the Model Learning or Memorizing?

**Duration: ~2-3 weeks. Team: 1 person. Compute: 1 GPU + laptop.**

Sprint 3 answered: **can we make the model learn structural patterns by removing external names?** Yes — 22/22 benchmark pass at 99.5% candidate rate. But the ML architect assessment and tiny-model diagnostic revealed this success is driven by memorization under vocabulary constraints, not learned generalization. Val/train ratio is 14.07x (val=3.30, train=0.23), the benchmark is saturated, and RL has no signal because pass@10 is already 1.0 for 21/22 tasks.

Sprint 4 answers: **can we distinguish memorization from generalization and make the model genuinely learn?**

### Why This Comes Next

Sprint 3's results expose a precise failure mode:

- **Closed terms work** — 0% free variables, 0.5% `<unk>`, the model generates structurally valid programs
- **Benchmark is saturated** — 22/22 tasks are solvable by memorizing structural patterns from training
- **Val/train ratio is 14.07x** — the model has memorized the training set completely
- **RL has no signal** — with pass@10 already at 1.0, rejection sampling can't improve anything
- **Tiny model diagnostic confirmed** — the 9/22 vs 22/22 gap between tiny (165K params) and big (3.6M params) models measures memorization capacity, not generalization
- **Vocabulary is bloated** — 1,549 tokens with 35 named binders (`v0`-`v34`) when de Bruijn indices would need ~30 index tokens
- **Data is insufficient** — 1,649 base functions (250K training tokens) for a 3.6M param model is a 14:1 params/tokens ratio; healthy is ~1:1

The root causes are threefold:

1. **Params/data mismatch** — 3.6M parameters memorize 250K tokens trivially. Need 10x more data or 10x smaller model (or both)
2. **Named variables inflate vocabulary** — `v0` through `v34` are 35 tokens that encode no information beyond binding depth. De Bruijn indices are canonical and smaller
3. **Benchmark measures recall** — all 22 tasks test patterns present in training data. No tasks require composing unseen combinations or generalizing to new structures

### The Three Pillars

#### Pillar 1 — Data Scale (10x)

Expand the corpus from 1,649 to 10,000-15,000 base functions by curating 200+ Hackage packages (up from 32).

**Package expansion:**

- Current `packages.txt` has 32 packages yielding 1,649 functions from 18 that compiled
- Target: 200+ packages across diverse categories (data structures, algorithms, parsers, network, text, math, concurrency)
- Curate for diversity: avoid packages that are thin wrappers around the same underlying library
- Expected yield: ~50-75 functions per package average, 100-200 packages compiling successfully

**GHC artifact cleanup in `clean.py`:**

Sprint 3's dep analysis revealed GHC artifacts polluting the corpus:

| Artifact               | Occurrences | Fix                                                                                   |
| ---------------------- | ----------- | ------------------------------------------------------------------------------------- |
| `letrec`               | 104         | Filter `letrec` deps — these are GHC's internal recursive-let, not real functions     |
| `cast`                 | 53          | Filter `cast` deps — GHC coercions, not user code                                     |
| `$trModule1`           | 43          | Filter `$trModule1` deps — GHC module metadata                                        |
| `primFixed`            | 41          | Filter `primFixed` deps — GHC internal                                                |
| `<`, `>`, `>=` as deps | 90, 83, 48  | Promote comparison operators to `Prim` nodes instead of leaving them as external deps |

These cleanups reduce noise in the dep metadata and shrink the average dep count per function.

**Target corpus metrics:**

| Metric              | Sprint 3                               | Sprint 4 target      |
| ------------------- | -------------------------------------- | -------------------- |
| Base functions      | 1,649                                  | 10,000-15,000        |
| Packages compiled   | 18                                     | 100-200              |
| Augmented sequences | 6,551                                  | 30,000-45,000        |
| Training tokens     | ~250K                                  | ~2-3M                |
| GHC artifact deps   | 241 (letrec+cast+$trModule1+primFixed) | 0                    |
| Comparison op deps  | 221 (<, >, >=)                         | 0 (promoted to Prim) |

#### Pillar 2 — De Bruijn Indices

Replace named variables (`v0`, `v1`, ..., `v34`) with de Bruijn indices (`0`, `1`, `2`, ...) to make the representation canonical and shrink the vocabulary.

**What are de Bruijn indices?**

In de Bruijn notation, a variable is represented by the number of binders between its use and its binding site. `\x. \y. x` becomes `Lam (Lam 1)` — the variable refers to the binder 1 level up. `\x. \y. y` becomes `Lam (Lam 0)` — the variable refers to the immediately enclosing binder.

**Why this helps:**

1. **Canonical representation** — alpha-equivalent terms have identical de Bruijn representations. No re-alpha-rename augmentation needed (it was augmenting identity)
2. **Vocabulary shrinks dramatically** — 35 binder tokens (`v0`-`v34`) replaced by ~15 index tokens (`0`-`14`, with rare higher indices). Total vocabulary drops from ~1,549 to ~100-200
3. **Embedding table shrinks** — with `vocab=200, d_model=128`: embeddings are ~25K params vs ~400K with `vocab=1549, d_model=256`. The model's parameter budget goes to transformer layers, not lookup tables
4. **No augmentation artifact** — re-alpha-rename produced 3 "different" sequences that were semantically identical. De Bruijn eliminates this entirely
5. **Simpler normalization** — no Renamer class tracking scope for fresh name generation. The de Bruijn conversion is a single recursive pass

**Serialization format:**

Before (Sprint 3 named variables):

```
DEF fDouble (Lam v0 (App Prim * Var v0 Lit 2)) EOF
```

After (de Bruijn indices):

```
DEF fDouble (Lam 1 (App Prim * Var 0 Lit 2)) EOF
```

Multi-argument functions use binder counts:

```
DEF fAdd (Lam 2 (App Prim + Var 1 Var 0)) EOF
```

Here `Lam 2` binds two variables: index 1 (first arg) and index 0 (second arg).

**SELF token stays:** `SELF` is a free variable (the recursive self-reference), not a bound variable. It has no de Bruijn index. It remains a special token in the vocabulary.

**Dependency parameters:** Lambda-lifted deps become the outermost binders, same as Sprint 3. Their de Bruijn indices are the highest (farthest from use site). The `deps` metadata still maps positions to function names.

**Implementation:** Replace the `Renamer` class in `normalize.py` with a `DeBruijnRenamer` that:

1. Walks the AST top-down, maintaining a binding stack
2. At each `Lam`/`Let`/`Case` binder, pushes names onto the stack
3. At each `Var`, looks up the name's stack depth and emits the de Bruijn index
4. Self-references emit `SELF`; free variables (deps) use their lifted binder position

#### Pillar 3 — Honest Evaluation

The current 22-task benchmark is saturated. Every task can be solved by recalling a memorized pattern. Sprint 4 builds a tiered evaluation that separates recall from generalization.

**Tier 1 — Baseline (22 tasks, retained):**

The existing tasks become the regression suite. They measure "does the pipeline still work?" — not generalization.

Expected: pass@1 >= 18/22 at temperature 0.0 (greedy decode). These are easy enough that a model trained on 10x data should solve most of them without sampling.

**Tier 2 — Compositional (~10 tasks):**

Tasks that require chaining 2-3 functions in combinations not seen during training. The individual pieces exist in the corpus, but the specific composition is novel.

Examples:

- `map_then_filter`: apply a transformation, then filter the result (requires both `map` and `filter` patterns composed)
- `fold_after_map`: transform elements, then reduce (map skeleton feeding into fold skeleton)
- `nested_case`: pattern match on a list of Maybes (`Case` on list, nested `Case` on Maybe)
- `accumulator_sum`: sum with an accumulator parameter (tail-recursive pattern, not the naive fold)
- `zip_then_fold`: zip two lists, then reduce the pairs

These test whether the model can compose structural skeletons it has seen separately.

**Tier 3 — Structural generalization (~6 tasks):**

Tasks involving data structures or patterns underrepresented in training:

- `maybe_map`: apply a function inside a Maybe (simple functor, but Maybe recursion is different from list recursion)
- `pair_swap`: swap elements of a 2-tuple (tuple pattern matching)
- `compose`: function composition `(f . g) x = f (g x)` (higher-order, no recursion, no data structures)
- `flip`: `flip f x y = f y x` (argument reordering)
- `const`: `const x y = x` (ignore second argument)
- `on`: `on f g x y = f (g x) (g y)` (combinator pattern)

These test whether the model understands the calculus beyond list recursion.

**Primary metric change: pass@1 at temperature 0.0**

Sprint 3 used pass@10 with temperature sampling, which conflates model confidence with sampling luck. Sprint 4's primary metric is pass@1 at greedy decode — the single most likely output must be correct. This is a much harder bar and better measures whether the model has learned the pattern vs. whether it can stumble onto it.

pass@10 is retained as a secondary metric for comparison.

**Right-sized model:**

The tiny-model diagnostic showed that vocabulary size dominates parameter count at small d_model. With de Bruijn indices shrinking vocabulary to ~100-200 tokens:

| Config                                 | Params | Rationale                                                                                       |
| -------------------------------------- | ------ | ----------------------------------------------------------------------------------------------- |
| 2 layers, d_model=128, nhead=4, ff=512 | ~500K  | Target ~1:1 ratio with ~350K training tokens (post-augmentation, de Bruijn reduces token count) |
| 4 layers, d_model=128, nhead=4, ff=512 | ~800K  | Fallback if 2-layer underfits                                                                   |

The big model (3.6M params) is retained for comparison but is not the primary evaluation target. The right-sized model should achieve low val/train ratio (<2x) while still solving tasks — proving generalization rather than memorization.

### Pipeline Changes

#### extract/packages.txt (EXPANDED)

- From 32 to 200+ curated Hackage packages
- Organized by category for diversity
- Expected: 100-200 packages compile successfully, yielding 10,000-15,000 functions

#### extract/clean.py (UPDATED)

- Filter GHC artifact deps: `letrec`, `cast`, `$trModule1`, `primFixed` removed from dep lists
- Promote comparison operators (`<`, `>`, `>=`, `<=`, `==`, `/=`) from dep references to `Prim` nodes
- Validation gate unchanged: every cleaned AST must round-trip through pretty-print and runhaskell

#### corpus/normalize.py (REWRITTEN — de Bruijn)

- Replace `Renamer` class with `DeBruijnRenamer`
- `alpha_rename()` becomes `debruijn_convert()`: walks AST, maintains binding stack, emits indices
- `SELF` token preserved for recursive self-references
- Lambda lifting unchanged (still converts free vars to outermost binders)
- Output: sequences with de Bruijn indices instead of named variables

#### corpus/augment.py (SIMPLIFIED)

- **Remove** re-alpha-rename augmentation entirely (de Bruijn indices are canonical — re-renaming is identity)
- **Keep** commutative swap augmentation (still produces genuinely different sequences)
- Expected augmentation ratio: ~2-3x (down from ~4x, but base corpus is 10x larger)

#### corpus/tokenizer.py (REBUILT)

- Vocabulary target: ~100-200 tokens (down from 1,549)
- No binder tokens (`v0`-`v34` eliminated)
- De Bruijn index tokens: `0` through `~14` (with `<unk>` for rare higher indices)
- `SELF` remains a special token
- Structural tokens unchanged: `Lam`, `App`, `Case`, `Let`, `Var`, `Con`, `Lit`, `Prim`, `DEF`, `EOF`, `(`, `)`, `@T`

#### model/train.py (UPDATED)

- Add right-sized model config: 2 layers, d_model=128, nhead=4, ff=512
- Retain big model config for comparison runs
- Package-based split unchanged (but with more packages, split is more robust)
- Log vocab composition and params/tokens ratio
- Early stopping on validation loss

#### model/generate.py (MINOR)

- Update default prompts for de Bruijn format
- Temperature 0.0 (greedy) as default for evaluation

#### typecheck/checker.py (UPDATED)

- Replace `BINDER_TOKENS` (v0-v34) with de Bruijn index tokens (0-14)
- Update `is_type_valid` for de Bruijn index positions
- `SELF` handling unchanged

#### typecheck/constrained_decode.py (UPDATED)

- Valid token set updated for de Bruijn vocabulary
- Masking logic adjusted for index tokens instead of named binders

#### benchmark/tasks.json (EXPANDED)

- Tier 1: existing 22 tasks retained, tagged `"tier": 1`
- Tier 2: ~10 new compositional tasks, tagged `"tier": 2`
- Tier 3: ~6 new structural generalization tasks, tagged `"tier": 3`
- Each new task specifies expected deps and test cases

#### benchmark/run.py (UPDATED)

- Support tiered evaluation: report pass rates per tier
- Primary metric: pass@1 at temperature 0.0
- Secondary metric: pass@10 at temperature 0.8 (retained for comparison)
- De Bruijn-aware pretty printing in generated Haskell source
- Dependency substitution unchanged

#### benchmark/pretty_print.py (UPDATED)

- De Bruijn indices → named Haskell variables (reverse the transformation for output)
- `Var 0` → closest enclosing binder's Haskell name, etc.
- `SELF` rendering unchanged

#### rl/evaluate.py + rejection_sample.py (UPDATED)

- Thread tiered task sets
- Report per-tier metrics
- De Bruijn-aware generation and evaluation

### Deliverables

```
sprint/
├── extract/
│   ├── packages.txt              # EXPANDED: 200+ curated Hackage packages
│   ├── clean.py                  # UPDATED: filter GHC artifact deps, promote comparison ops
│   ├── extract_hackage.py        # REUSED (handles larger package list)
│   ├── parse_ghc_core.py         # REUSED
│   ├── Functions.hs              # RETAINED
│   ├── functions.txt             # RETAINED
│   ├── ir_schema.json            # RETAINED
│   └── corpus_raw.json           # 10,000-15,000 cleaned ASTs
├── corpus/
│   ├── normalize.py              # REWRITTEN: DeBruijnRenamer, debruijn_convert()
│   ├── augment.py                # SIMPLIFIED: commutative swap only (no re-rename)
│   ├── tokenizer.py              # REBUILT: ~100-200 token vocabulary
│   ├── sequences.json            # 10,000-15,000 de Bruijn sequences
│   ├── sequences_aug.json        # 20,000-45,000 augmented sequences
│   └── tokenizer.json            # compact vocabulary
├── model/
│   ├── train.py                  # UPDATED: right-sized model config (2L/128d/~500K params)
│   ├── generate.py               # UPDATED: greedy decode default
│   ├── checkpoint.pt             # right-sized model checkpoint
│   └── checkpoint_big.pt         # big model checkpoint (comparison)
├── typecheck/
│   ├── checker.py                # UPDATED: de Bruijn index tokens
│   └── constrained_decode.py     # UPDATED: de Bruijn vocabulary
├── benchmark/
│   ├── tasks.json                # EXPANDED: 22 Tier 1 + ~10 Tier 2 + ~6 Tier 3
│   ├── run.py                    # UPDATED: tiered eval, pass@1 primary metric
│   └── pretty_print.py           # UPDATED: de Bruijn → Haskell variable names
└── rl/
    ├── evaluate.py               # UPDATED: tiered metrics
    └── rejection_sample.py       # UPDATED: tiered metrics
```

### Success Criteria

| Question                                         | Minimum bar                                                     | Stretch                                            |
| ------------------------------------------------ | --------------------------------------------------------------- | -------------------------------------------------- |
| Does 10x data fix overfitting?                   | Val/train ratio < 3x (down from 14.07x)                         | Val/train ratio < 1.5x                             |
| Does de Bruijn shrink vocabulary?                | < 200 tokens (down from 1,549)                                  | < 150 tokens                                       |
| Does the right-sized model generalize?           | Val loss < 2.0 nats with ~500K param model                      | Val loss < 1.5 nats                                |
| Does the right-sized model solve Tier 1?         | >= 18/22 Tier 1 tasks at pass@1 (greedy)                        | >= 21/22                                           |
| Does the model compose (Tier 2)?                 | >= 3/10 Tier 2 tasks at pass@10                                 | >= 6/10 at pass@1                                  |
| Does the model generalize structurally (Tier 3)? | >= 1/6 Tier 3 tasks at pass@10                                  | >= 3/6 at pass@1                                   |
| Does RL have signal?                             | RL improves Tier 2 pass rate by >= 2 tasks                      | RL improves Tier 2 + Tier 3 combined by >= 4 tasks |
| Are GHC artifact deps eliminated?                | Zero `letrec`, `cast`, `$trModule1`, `primFixed` deps in corpus | Comparison ops fully promoted to Prim              |
| Does data scale work?                            | >= 5,000 functions from >= 50 packages                          | >= 10,000 functions from >= 100 packages           |

### What This Validates

Sprint 4 tests a specific hypothesis: **the model's memorization-driven success in Sprint 3 can be replaced with genuine generalization by fixing the params/data ratio, canonicalizing the representation, and measuring with harder tasks.**

If it works:

- The params/data ratio theory is correct — more data + right-sized model = generalization
- De Bruijn indices are the right canonical representation for the IR
- The model can compose structural patterns it has seen separately (Tier 2 success)
- RL becomes meaningful when the base model has room to improve
- The path to scaling is clear: more data, same architecture, harder tasks

If it partially works (Tier 1 passes, Tier 2/3 limited):

- The model learns simple patterns but can't compose them
- Next step: curriculum learning, compositional training objectives, or explicit composition supervision
- De Bruijn + data scale are still the right foundation — the training procedure needs work

If it fails (Tier 1 regresses, val loss doesn't improve):

- The bottleneck is not params/data ratio — it's something architectural
- De Bruijn indices might lose information that named variables preserved
- Consider: tree-positional encodings, graph neural networks, or explicit type-guided generation
- Re-evaluate whether sequence-based generation is the right approach for this IR

### What This Doesn't Change

- **Same extraction pipeline**: GHC Core → parse → clean → IR (extended to more packages)
- **Same lambda lifting**: dependency injection, `SELF` token, closed terms
- **Same training procedure**: cosine LR with warmup, early stopping, package-based split
- **Same RL algorithm**: rejection sampling with parallel runhaskell
- **Same benchmark harness**: dependency substitution, un-lifting, test expression building

The variables are: data quantity, variable representation (de Bruijn), model size (right-sized), and evaluation difficulty (tiered benchmark). Everything else is held constant.

### Risks

| Risk                                                                                | Likelihood | Mitigation                                                                                                                                                                                         |
| ----------------------------------------------------------------------------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 200+ packages overwhelm extraction pipeline (compile failures, timeouts)            | Medium     | Batch with generous timeouts, skip failures, target 50% yield                                                                                                                                      |
| De Bruijn indices harder to learn than named variables (loss of "positional hints") | Low        | Named variables carry no semantic info beyond binding depth — de Bruijn is strictly more canonical. Compare big model on both representations as sanity check                                      |
| Right-sized model (~500K params) underfits even with more data                      | Medium     | Keep big model as fallback. If 2-layer underfits, try 4-layer at d_model=128 (~800K params)                                                                                                        |
| Tier 2/3 tasks are too hard — no model can solve them at this scale                 | Medium     | Ensure each Tier 2 task has all component patterns individually present in training. Tier 3 tasks should be simple (3-5 tokens in the body). If 0/16 pass, tasks need recalibration, not the model |
| Augmentation ratio drops with de Bruijn (no re-rename)                              | Low        | Base corpus is 10x larger, so 2-3x augmentation still yields 20-45K sequences — more than enough                                                                                                   |
| Comparison operator promotion in clean.py breaks round-trip validation              | Low        | Test on current 1,649 functions first. Only promote operators that already have `Prim` equivalents in the IR schema                                                                                |
| Vocabulary too small for diverse Hackage corpus (more constructors, literals)       | Medium     | Allow vocab to grow up to 300 if needed. The key win is eliminating binder tokens, not hitting a specific number                                                                                   |

### Timeline

| Task                                                         | Estimate                          |
| ------------------------------------------------------------ | --------------------------------- |
| Curate 200+ packages in `packages.txt`                       | 0.5 day                           |
| Run extraction pipeline on expanded package list             | 1-2 days (mostly wall-clock time) |
| GHC artifact cleanup + comparison op promotion in `clean.py` | 1 day                             |
| De Bruijn renamer in `normalize.py`                          | 1-2 days                          |
| Simplify `augment.py` (remove re-rename)                     | 0.5 day                           |
| Rebuild tokenizer on de Bruijn corpus                        | 0.5 day                           |
| Update `checker.py` + `constrained_decode.py` for de Bruijn  | 0.5 day                           |
| Update `pretty_print.py` for de Bruijn → Haskell             | 1 day                             |
| Update `run.py` for tiered evaluation + pass@1               | 1 day                             |
| Design Tier 2 + Tier 3 tasks in `tasks.json`                 | 1 day                             |
| Add right-sized model config to `train.py`                   | 0.5 day                           |
| Training runs (right-sized + big model comparison)           | 1-2 days                          |
| Benchmark + RL experiments                                   | 1-2 days                          |
| Analysis + status writeup                                    | 0.5 day                           |
| **Total**                                                    | **~2-3 weeks**                    |

### Running the Pipeline

```bash
make help              # list all targets
make all               # full pipeline: extract → corpus → train
make extract           # GHC Core → corpus_raw.json (hand-written functions)
make extract-hackage   # batch Hackage extraction with expanded packages.txt
make corpus            # normalize (de Bruijn) + augment + tokenize
make train             # train right-sized model (~500K params)
make train-big         # train big model (3.6M params, comparison)
make generate          # greedy decode from right-sized checkpoint
make benchmark         # run tiered benchmark (Tier 1/2/3), report pass@1 + pass@10
make rl                # multi-round rejection sampling
make clean             # remove all generated artifacts
make clean-model       # remove checkpoints only
```

### Key Constraints

- Same infrastructure: laptop + 1 GPU, Python + PyTorch
- No new dependencies beyond what Sprint 2 already requires (`cabal-install` for Hackage)
- Still disposable code — production quality is Stage 0+
- Training should complete in < 2 hours for right-sized model, < 4 hours for big model (10x more data but smaller model)
- Sprint 4 modifies sprint 3 code in `sprint/` (sprint 3 results recorded in docs/)
- Extraction is the long pole — 200+ packages may take 4-8 hours wall-clock time (parallelize where possible)

### Relationship to Roadmap

| Concept                  | Sprint 4                                           | Production (Stage 0+)                                                        |
| ------------------------ | -------------------------------------------------- | ---------------------------------------------------------------------------- |
| De Bruijn indices        | DeBruijnRenamer in normalize.py                    | Built into the IR definition from the start                                  |
| Canonical representation | Eliminates re-rename augmentation                  | No augmentation needed — representation is canonical by construction         |
| Data scale               | 10,000-15,000 functions from Hackage               | Continuous extraction from entire Hackage + other sources                    |
| Right-sized model        | ~500K params for ~350K tokens                      | Model size scales with data; params/tokens ratio ~1:1                        |
| Tiered evaluation        | 22 + 10 + 6 tasks with pass@1                      | Continuously expanding benchmark with compositional and generalization tiers |
| GHC artifact cleanup     | Filter letrec/cast/$trModule1, promote comparisons | Clean extraction from the start (GHC plugin or better parser)                |
| Honest metrics           | pass@1 at greedy, per-tier reporting               | Standard ML metrics: held-out generalization, compositional reasoning        |

If Sprint 4 validates that more data + canonical representation + right-sized model = genuine generalization, the production IR should use de Bruijn indices natively and the training pipeline should be designed around the params/data ratio constraint from the start.
