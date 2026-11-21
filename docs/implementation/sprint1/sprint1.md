# Sprint 1: From Memorization to Generalization

The second iteration of [[ast_pipeline_roadmap.md]].
Core objective: prove the model can synthesize programs it hasn't seen before.

## Sprint 1: Can the Model Generalize?

**Duration: ~2 weeks. Team: 1 person. Compute: 1 GPU + laptop.**

Sprint 0 answered: **does the loop close?** Yes — all 5 criteria met. But the model memorized 73 functions and replayed them verbatim. Sprint 1 answers the next existential question: **can the model synthesize programs it hasn't seen before?**

### Why This Comes Next

Sprint 0's success is deceptive. At temp=0.0, the model reproduces exact corpus ASTs (memorization). At temp>0, constrained decoding keeps output parseable, but the "new" programs are noise perturbations of memorized ones, not genuine compositions. The RL improvement (+0.902 pass@1) came from a weak 50-epoch checkpoint learning to copy the corpus better, not from learning to compose.

Until the model solves a task that isn't in the training data, we haven't validated the approach. Sprint 1 is the real proof-of-concept gate — it determines whether scaling corpus and model size (Stages 0-2 of the roadmap) is worth the investment.

### Three Focus Areas

#### 1. Corpus Scale: 73 → 500-1000 Sequences

The model can't generalize from 73 examples. Two strategies, both needed:

| Strategy                                                                                                                                           | Target                     | Effort  |
| -------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- | ------- |
| **More functions** — extract from more packages (`text`, `bytestring`, `containers` deep) + write more hand-picked functions covering new patterns | 73 → 200-300 raw functions | ~1 week |
| **Synthetic augmentation** — alpha-renamed variants, let-reordering, beta-reduced copies, eta-expanded forms                                       | 300 → 500-1000 sequences   | ~3 days |

This is still tiny by ML standards but crosses the threshold where a small model can learn patterns (folds, maps, recursion schemes) rather than memorize individuals.

**More functions — what to add:**

Expand `Functions.hs` with definitions that increase _pattern diversity_, not just count:

- **Arithmetic combinators:** `negate`, `triple`, `square` (already have), `mod`, `divides`, `abs` variants
- **Boolean combinators:** `isZero`, `isPositive`, `isNegative`, `implies`, `xor`
- **List recursion patterns:** `any`, `all`, `concatMap` (deeper), `scanl`, `scanr`, `iterate`, `unfoldr`
- **Pair/tuple operations:** `swap`, `both`, `mapFst`, `mapSnd`, `bimap`
- **Maybe chains:** `orElse`, `mapMaybe` variants, `catMaybes` (already have), `sequence`-like patterns
- **Composition patterns:** `compose2`, `on`, `fix` (if GHC Core is tractable)
- **Multi-argument folds:** `foldlWithIndex`, `zipWith3`, `mapAccumL`

Target: 150-200 source functions producing 200-300 Core bindings.

**Synthetic augmentation (`augment.py`):**

Transform existing corpus sequences into valid variants:

1. **Re-alpha-rename:** assign fresh variable names (different counter seed) — the model should learn that `v0 v1 v2` and `v3 v4 v5` are structurally identical
2. **Let-reorder:** for functions with multiple `let` bindings, permute binding order (where semantics allow)
3. **Eta-expand:** `f` → `\x -> f x` for functions in the corpus (adds Lam/App wrapper)
4. **Inline trivial lets:** replace `let v0 = expr in v0` with `expr`
5. **Duplicate with noise:** for each sequence, produce 2-3 augmented copies

Target: 500-1000 total sequences from 200-300 raw functions.

#### 2. Model Architecture: Learn Patterns, Not Instances

| Change                                                                 | Why                                                                            |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Scale to 2-5M params (4 layers, d_model=256, nhead=8, ff=1024)         | Enough capacity for pattern learning without enough to memorize 500+ sequences |
| **Train/validation split** (80/20 by function family, not by sequence) | First time we measure actual generalization loss                               |
| Track validation loss alongside training loss                          | The real metric — training loss alone is meaningless                           |
| Add dropout (0.1) on embeddings and attention                          | Regularization to force generalization                                         |
| Increase weight decay (0.01 → 0.05)                                    | Further regularization                                                         |
| Early stopping on validation loss                                      | Prevent overfitting to training split                                          |

**Split strategy:** Group functions by family (e.g., all fold variants in the same split, all map variants together). This prevents data leakage — if `fFoldr` is in train and `fFoldl` is in val, the model must generalize across the fold family, not memorize the specific fold.

The model should plateau at a validation loss well below random (~5 nats) but above zero — proving it has learned distributional structure, not just copied data. Expected: val loss 1.5-3.0 nats.

#### 3. Novel-Task Benchmark: Tasks WITHOUT Corpus Matches

This is the key test. Sprint 0's benchmark tasks all had direct corpus functions (`fId`, `fSum`, `fFilter`...). Sprint 1 adds tasks that require **composing** patterns from the corpus into programs the model has never seen:

**Original tasks (retained):**

| #   | Task            | Prompt        | Type                              |
| --- | --------------- | ------------- | --------------------------------- |
| 1   | identity        | `DEF:fId`     | `Int -> Int`                      |
| 2   | double          | `DEF:fDouble` | `Int -> Int`                      |
| 3   | successor       | `DEF:fSucc`   | `Int -> Int`                      |
| 4   | not             | `DEF:fNot`    | `Bool -> Bool`                    |
| 5   | null            | `DEF:fNull`   | `[Int] -> Bool`                   |
| 6   | sum             | `DEF:fSum`    | `[Int] -> Int`                    |
| 7   | length          | `DEF:fLength` | `[Int] -> Int`                    |
| 8   | tail            | `DEF:fTail`   | `[Int] -> [Int]`                  |
| 9   | map_double      | `DEF:fMap`    | `(Int -> Int) -> [Int] -> [Int]`  |
| 10  | filter_positive | `DEF:fFilter` | `(Int -> Bool) -> [Int] -> [Int]` |

**Novel tasks (NEW — no direct corpus match):**

| #   | Task       | Type                             | Why it's novel                                                 |
| --- | ---------- | -------------------------------- | -------------------------------------------------------------- |
| 11  | triple     | `Int -> Int`                     | `*3` — similar to `fDouble` but not identical                  |
| 12  | isZero     | `Int -> Bool`                    | Combine comparison + boolean — not in corpus as a standalone   |
| 13  | negate     | `Int -> Int`                     | Simple arithmetic negation, not in corpus                      |
| 14  | isPositive | `Int -> Bool`                    | Comparison + boolean, different from `isZero`                  |
| 15  | headOrZero | `[Int] -> Int`                   | Safe head returning 0 on empty — composition of case + default |
| 16  | countTrue  | `[Bool] -> Int`                  | Fold over booleans — novel type instantiation                  |
| 17  | any        | `(Int -> Bool) -> [Int] -> Bool` | Fold + boolean — structurally new HOF                          |
| 18  | all        | `(Int -> Bool) -> [Int] -> Bool` | Same family as `any`, tests generalization                     |
| 19  | sumOf      | `(Int -> Int) -> [Int] -> Int`   | Compose map + fold — `sum . map f`                             |
| 20  | zipWithAdd | `[Int] -> [Int] -> [Int]`        | Instantiate `fZipWith` pattern with `(+)`                      |

For novel tasks, the model must receive a generic prompt (e.g., `DEF:fNovel11`) that does NOT match any corpus function name. This forces the model to generate from learned patterns rather than pattern-matching on the prompt prefix.

**Prompt strategy for novel tasks:**

Novel tasks cannot use `DEF:fTriple` because the model has never seen that prefix. Two options:

- **Option A: Type-conditioned prompts.** Extend the prompt format to include the target type signature: `DEF:fNovel TYPE:Int->Int`. This requires a minor tokenizer/model change but gives the model real information to condition on.
- **Option B: Few-shot prompting with corpus examples.** Prepend 1-2 similar corpus sequences before the novel prompt. E.g., show `DEF:fDouble (Lam ...) EOF` then `DEF:fTriple`. This uses in-context learning without model changes.
- **Option C: Corpus seeding.** Add the novel function to the training corpus with just its definition header (name + type) but no body, then fine-tune. The model learns the function exists but must generate the body.

Start with Option B (simplest, no model changes). Try A if B fails.

### Deliverables

```
sprint1/
├── extract/
│   ├── Functions.hs              # expanded: 150-200 functions
│   ├── functions.txt             # updated list
│   └── parse_ghc_core.py         # reused (minor fixes if needed)
├── corpus/
│   ├── augment.py                # NEW: synthetic augmentation pipeline
│   ├── normalize.py              # reused from sprint0
│   ├── tokenizer.py              # rebuilt with larger vocab
│   ├── sequences.json            # 500-1000 sequences
│   └── tokenizer.json            # larger vocabulary (~300-500 tokens)
├── model/
│   ├── train.py                  # UPDATED: val split, dropout, larger arch, early stopping
│   ├── generate.py               # reused from sprint0
│   └── checkpoint.pt             # new checkpoint
├── typecheck/
│   ├── checker.py                # reused from sprint0
│   └── constrained_decode.py     # reused from sprint0
├── benchmark/
│   ├── tasks.json                # EXPANDED: 20+ tasks (10 original + 10+ novel)
│   ├── run.py                    # UPDATED: support for novel task prompting
│   └── pretty_print.py           # reused from sprint0
└── rl/
    ├── rejection_sample.py       # UPDATED: multi-round support
    └── evaluate.py               # reused from sprint0
```

### Success Criteria

| Question                    | Minimum bar                                | Stretch                               |
| --------------------------- | ------------------------------------------ | ------------------------------------- |
| Does the model generalize?  | Validation loss < 2x training loss         | Val loss < 1.5x training loss         |
| Can augmentation help?      | Augmented corpus → lower val loss than raw | ≥ 20% lower val loss                  |
| Can it solve novel tasks?   | ≥ 1 novel task solved at pass@100 after RL | ≥ 3 novel tasks solved                |
| Does multi-round RL help?   | pass@k improves across 2 rounds            | Monotonic improvement across 3 rounds |
| Overall benchmark pass rate | ≥ 5/20 tasks pass at pass@100              | ≥ 10/20 tasks                         |

### What This Proves

**If Sprint 1 succeeds:** The model learns **composable patterns** from typed ASTs, not just individual programs. This validates the core hypothesis — scaling the corpus (Stages 0-1 of the roadmap) and the model (Stage 2) will produce a genuine program synthesizer, not a lookup table. Proceed to production extraction with confidence.

**If it fails:** We learn specifically where:

| Failure mode                                     | What it means                                            | Next step                                                       |
| ------------------------------------------------ | -------------------------------------------------------- | --------------------------------------------------------------- |
| Val loss ≈ training loss (memorization persists) | Corpus is still too small or augmentation is ineffective | Jump to Stage 0 extraction — need real scale, not synthetic     |
| Val loss is high (model doesn't learn)           | Serialization may lose compositional structure           | Investigate tree-positional encodings or tree-structured models |
| Novel tasks all fail                             | Type checker too weak to guide composition               | Improve type inference, add polymorphism support                |
| RL rounds don't compound                         | Winner diversity too low                                 | Need more diverse generation (higher temp, nucleus sampling)    |

### Timeline

| Task                                                          | Estimate     |
| ------------------------------------------------------------- | ------------ |
| Expand `Functions.hs` (100+ new functions)                    | 2-3 days     |
| Write `augment.py`                                            | 1-2 days     |
| Update `train.py` (val split, dropout, early stopping)        | 1 day        |
| Rebuild corpus + tokenizer                                    | 0.5 day      |
| Expand `tasks.json` with novel tasks                          | 1 day        |
| Update `run.py` for novel task prompting                      | 0.5 day      |
| Training experiments (raw vs augmented, hyperparameter sweep) | 2-3 days     |
| Benchmark + multi-round RL                                    | 1-2 days     |
| Analysis + writeup                                            | 1 day        |
| **Total**                                                     | **~2 weeks** |

### Key Constraints

- Same infrastructure: laptop + 1 GPU, Python + PyTorch, no new dependencies
- Still disposable code — production quality is Stage 0+
- Training should complete in < 1 hour (larger model + corpus, but still small)
- Sprint 1 code lives in `sprint1/` (fresh copy, not modifying sprint0/)

### Resources

- **Team:** 1 engineer
- **Compute:** 1 consumer GPU (RTX 5090). CPU for everything else
- **Duration:** ~2 weeks
- **Cost:** Zero beyond existing hardware
