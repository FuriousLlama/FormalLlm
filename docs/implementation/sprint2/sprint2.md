# Sprint 2: Scale & Clean

The third iteration of [[ast_pipeline_roadmap.md]].
Core objective: replace GHC-specific noise with a clean IR and scale the corpus 10-50x by extracting from real Hackage packages.

## Sprint 2: Can We Build a Real Corpus?

**Duration: ~2-3 weeks. Team: 1 person. Compute: 1 GPU + laptop.**

Sprint 1 answered: **can the model generalize?** Partially — 7/12 novel tasks solved, but val/train ratio 3.21x (missed 2x target). The model overfits because 626 sequences can't support 3.3M parameters. Sprint 2 answers the next existential question: **can we build a corpus large enough for real generalization, and clean enough for the model to learn structural patterns instead of GHC compiler internals?**

### Why This Comes Next

Sprint 1's results are simultaneously encouraging and clearly data-limited:

- **17/22 tasks pass** after RL — the pipeline works, the model learns _something_
- **Val/train ratio 3.21x** — 3.3M params on ~24K tokens is 140 parameters per training token. The model memorizes by epoch 25 and everything after is wasted
- **GHC Core artifacts pollute the IR** — `I#`, `$fNumInt`, `wild`, `def`, synthetic helper names. These make sequences longer, noisier, and Haskell/GHC-specific
- **RL plateaus** because 6/22 tasks can never produce winners (missing registry in evaluator)
- **Hand-writing Functions.hs doesn't scale** — 162 functions took significant manual effort and still isn't enough

The sprint 1 lessons doc is explicit: "GHC Core is the wrong foundation for a language-agnostic IR" and "model capacity vs dataset size: 3.3M params on 626 sequences is too much." Sprint 2 attacks both problems simultaneously.

### Three Focus Areas

#### 1. Clean IR: Define It, Then Clean GHC Core Into It

**The problem:** Our token stream already represents what we want — a typed lambda calculus (`Lam`, `App`, `Case`, `Var`, `Lit`, `Con`). But GHC Core contaminates it with compiler internals that don't exist in source Haskell and block cross-language generalization.

**The approach:** Define the target IR explicitly as a JSON Schema, then write a cleaning pass that transforms raw GHC Core ASTs into clean IR. This is faster than building a source-level extractor (which is the Stage 0 production approach) while still fixing the noise problem.

**IR schema (`ir_schema.json`):**

```
Node types:
  Lam    — lambda abstraction: binders + body
  App    — function application: function + arguments
  Case   — pattern matching: scrutinee + alternatives
  Let    — local binding: name + value + body
  Var    — variable reference (local or global)
  Lit    — literal value (Int, Bool, Char)
  Con    — data constructor (True, False, [], :, (,), Nothing, Just)

Primitives (first-class, not via typeclass dicts):
  +, -, *, div, mod           — Int arithmetic
  ==, /=, <, <=, >, >=       — comparison (return Bool)
  &&, ||, not                 — boolean operations
  negate, abs, signum         — unary arithmetic

Explicitly excluded (GHC internals):
  I#, Int#, Char#             — boxing/unboxing constructors
  $fNumInt, $fOrdInt, ...     — typeclass dictionary arguments
  wild, wild1, ds, ds1        — GHC-internal scrutinee/wildcard names
  __DEFAULT                   — GHC's default pattern name
  Coercions, Rules, Ticks     — compiler annotations
```

**Cleaning pass (`clean.py`):**

| GHC Core pattern                      | Clean IR output        | Rule                                           |
| ------------------------------------- | ---------------------- | ---------------------------------------------- |
| `App (Con I#) (Lit n#)`               | `Lit n`                | Unwrap integer boxing                          |
| `Case x of { I# n# -> body }`         | `body[n# := x]`        | Eliminate unboxing case                        |
| `App (Var $fNumInt_$c+) [dict, a, b]` | `App (Prim +) [a, b]`  | Typeclass dict → primitive                     |
| `Var wild` / `Var ds` in pattern      | `_` (wildcard)         | Normalize GHC-internal names                   |
| `TyArg @Int`                          | (erased)               | Type arguments already erased in serialization |
| `__DEFAULT` in Case alt               | `_` (wildcard pattern) | Normalize default pattern                      |

**Validation gate:** Every cleaned AST must round-trip: `clean → normalize → pretty-print → runhaskell`. Functions that fail are skipped with a diagnostic.

#### 2. Hackage Extraction: From 162 Hand-Written to 2,000-5,000 Real Functions

**The problem:** Hand-writing Functions.hs doesn't scale. 162 functions produced 626 augmented sequences — not enough for a 3M+ parameter model.

**The approach:** Build a batch extraction pipeline that downloads real Hackage packages, compiles them with `ghc -ddump-simpl`, and runs our existing parser + new cleaning pass on the output.

**Extraction strategy — escalation ladder:**

The existing Python parser (`parse_ghc_core.py`) works at 100% on hand-picked functions but may break on real-world Hackage code that produces Core constructs we haven't seen. Rather than pre-emptively rewriting the extraction pipeline, we measure first and escalate if needed:

| Step                                  | Trigger            | Action                                                                                                                            |
| ------------------------------------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| **1. Try existing parser on Hackage** | Start here         | Run `parse_ghc_core.py` + `clean.py` on 10 target packages. Measure parse failure rate                                            |
| **2. Extend parser**                  | Failure rate < 30% | Fix the specific constructs that fail. Likely 50-100 lines of additions                                                           |
| **3. GHC plugin**                     | Failure rate > 30% | Write a GHC plugin (~300-500 lines Haskell) that serializes `CoreProgram` directly to JSON. Eliminates all text-parsing fragility |

Step 1 takes a few hours. If parse failure rate is manageable, we skip the plugin entirely and save it for Stage 0. If it's not, we have concrete examples of what breaks to guide the plugin implementation.

**Why not start with the GHC plugin?** The plugin is the right long-term architecture (Stage 0) but has real upfront costs:

- **300-500 lines of Haskell, not 100.** The plugin skeleton is ~20 lines, but serializing `CoreExpr` (10 constructors), `Type` (8 constructors), `Literal` (8 constructors), `Id`, `DataCon`, etc. to JSON requires handling every case by hand — no `Generic`/`ToJSON` instances on GHC's internal types
- **GHC version lock-in.** The plugin compiles against the `ghc` package, which must match the compiler version exactly (9.6.6). GHC upgrade = plugin rewrite
- **Build setup complexity.** Needs a Cabal project, correct package DB, dynamic library loading
- **Slower iteration.** Haskell compile-test cycle vs. editing a Python script and re-running

If we reach step 3, prior art exists: Ben Gamari's [`ghc-dump-core`](https://hackage.haskell.org/package/ghc-dump-core) package already serializes Core to a binary format. It may need updating for GHC 9.6, but provides a solid starting point. An alternative middle ground: a minimal plugin that uses GHC's own `showSDoc . ppr` to emit each binding's text with structured metadata (function name, type signature as separate JSON fields) — we'd still parse expression bodies as text, but get clean names and types from the AST for free.

**Batch pipeline (`extract_hackage.py`):**

```
1. cabal unpack <package>           — download source
2. Find all .hs files               — discover modules
3. For each module:
   a. ghc -O0 -ddump-simpl -v0 -dynamic -fforce-recomp <file>
      (or: ghc with plugin, if escalated to step 3)
   b. parse_ghc_core.py on stdout   — extract raw ASTs
   c. clean.py on raw ASTs          — strip GHC internals
   d. For each function:
      - normalize → pretty-print → runhaskell (validation gate)
      - If passes: emit to corpus
      - If fails: log diagnostic, skip
4. Aggregate across all packages    — corpus_raw.json
```

**Target packages:** Start with self-contained, well-typed packages that don't require complex build setups:

| Category        | Packages                                                       | Why                                             |
| --------------- | -------------------------------------------------------------- | ----------------------------------------------- |
| Core libraries  | `base` (Prelude, Data.List, Data.Maybe, Data.Bool, Data.Tuple) | Rich in the patterns we want; already installed |
| Containers      | `containers` (Data.Map, Data.Set, Data.Sequence)               | Tree/map operations, folds, traversals          |
| Text processing | `text`, `bytestring`                                           | String operations, builder patterns             |
| Utilities       | `extra`, `safe`, `split`                                       | Small utility functions, combinators            |
| Math            | `arithmoi`, `hmatrix`                                          | Numeric patterns                                |

**Handling build failures:** Real packages have dependencies, CPP, Template Haskell, FFI, etc. The pipeline must be fault-tolerant:

- Skip modules that fail to compile (log and continue)
- Skip functions that use unsupported Core constructs (e.g., `Coercion`, foreign calls)
- Skip functions longer than 300 tokens after serialization (too long for training)
- Skip functions shorter than 5 tokens (trivially small)
- The validation gate (round-trip through runhaskell) is the final filter

**Target:** 2,000-5,000 functions passing the validation gate from 50-200 packages.

**Fallbacks (in order of preference):**

1. **GHC plugin** (if parser failure rate > 30%) — eliminates text-parsing fragility entirely
2. **Programmatic function generation** (if Hackage extraction is fundamentally too brittle) — a Python script that combinatorially generates Haskell function definitions by composing known patterns (e.g., `map f . filter g`, `foldr op z . take n`, `uncurry f . swap`). Less realistic but still produces 10x more data than hand-writing

#### 3. Right-Sized Training: Enough Data to Actually Generalize

**The problem:** Sprint 1's 3.3M param model on 626 sequences had 140 params per token. The model memorized everything by epoch 25.

**The approach:** With 2,000-5,000 raw functions producing 6,000-15,000 augmented sequences (~300K-750K tokens), the params-per-token ratio drops to 4-10 — within the range where real generalization is possible.

**Model options (choose based on corpus size):**

| Corpus size                   | Model size                  | Params/token | Expected behavior                          |
| ----------------------------- | --------------------------- | ------------ | ------------------------------------------ |
| ~5K sequences (~250K tokens)  | 3.3M (keep sprint 1 arch)   | ~13          | Moderate overfit, should generalize better |
| ~10K sequences (~500K tokens) | 3.3M (keep sprint 1 arch)   | ~7           | Good ratio, real generalization expected   |
| ~15K sequences (~750K tokens) | 5-8M (scale up to 6 layers) | ~7-10        | Room to learn more complex patterns        |

**Training improvements:**

- Same architecture as sprint 1 (4 layers, d_model=256, nhead=8, ff=1024, dropout=0.1)
- Train/val split by **package** (not by function name) — no leakage from the same codebase
- Expect val/train ratio < 2x with sufficient data
- Same early stopping on val loss

**Augmentation changes:**

- Keep re-alpha-rename (cheap, effective — sprint 1 validated this)
- Drop let-reorder and eta-expand (sprint 1 showed negligible yield)
- Add **subexpression permutation**: for commutative operations (`+`, `*`, `&&`, `||`), swap operand order. This teaches the model that `a + b` and `b + a` are equivalent
- Target 3x augmentation factor (re-alpha-rename with 3 seeds)

### Bug Fixes from Sprint 1

These are carried forward and must be fixed before benchmark/RL runs:

| Bug                                         | Fix                                                                                | Impact                                    |
| ------------------------------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------- |
| `evaluate.py` doesn't pass corpus registry  | Pass registry to `evaluate_task()` and `build_haskell_source()`                    | Unblocks 6 composition tasks in RL        |
| Sequential GPU→CPU→GPU in RL                | `concurrent.futures.ProcessPoolExecutor` for runhaskell invocations                | 5-10x speedup on RL rounds                |
| Val-set functions used as task dependencies | Ensure key helpers (`fMap`, `fFilter`, `fZipWith`, `fFoldr`) are in training split | `fZipWithAdd` and similar become solvable |
| Transitive dependency resolution            | Build full dep closure in benchmark harness                                        | Composition chains work correctly         |

### Deliverables

```
sprint/
├── extract/
│   ├── Functions.hs              # RETAINED: hand-written functions (still useful as seed)
│   ├── functions.txt             # RETAINED
│   ├── parse_ghc_core.py         # reused (extended for new constructs if needed)
│   ├── clean.py                  # NEW: GHC Core → clean IR transformation
│   ├── extract_hackage.py        # NEW: batch Hackage extraction pipeline
│   ├── ir_schema.json            # NEW: clean IR JSON Schema definition
│   ├── ghc-dump-plugin/          # CONDITIONAL: GHC plugin (only if parser failure rate > 30%)
│   │   ├── DumpCore.hs           #   ~300-500 lines, serializes CoreProgram to JSON
│   │   └── ghc-dump-plugin.cabal #   build config
│   └── corpus_raw.json           # output: 2000-5000 cleaned ASTs
├── corpus/
│   ├── augment.py                # UPDATED: drop low-yield strategies, add subexpr permutation
│   ├── normalize.py              # reused
│   ├── tokenizer.py              # rebuilt with larger vocab
│   ├── sequences.json            # 2000-5000 raw sequences
│   ├── sequences_aug.json        # 6000-15000 augmented sequences
│   └── tokenizer.json            # larger vocabulary (~500-1000 tokens)
├── model/
│   ├── train.py                  # UPDATED: split by package, not function name
│   ├── generate.py               # reused
│   └── checkpoint.pt             # new checkpoint
├── typecheck/
│   ├── checker.py                # UPDATED: handle clean IR primitives (Prim +, etc.)
│   └── constrained_decode.py     # reused
├── benchmark/
│   ├── tasks.json                # RETAINED: 22 tasks from sprint 1
│   ├── run.py                    # UPDATED: transitive dep resolution
│   └── pretty_print.py           # UPDATED: handle clean IR primitives
└── rl/
    ├── rejection_sample.py       # UPDATED: parallel runhaskell
    └── evaluate.py               # UPDATED: pass registry, parallel eval
```

### Success Criteria

| Question                                     | Minimum bar                                     | Stretch                                                |
| -------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------ |
| Does the clean IR eliminate GHC noise?       | Zero `I#`, `$fNumInt`, `wild` tokens in corpus  | 100% of corpus functions round-trip through runhaskell |
| Does Hackage extraction scale?               | ≥ 1,000 functions from ≥ 30 packages            | ≥ 5,000 functions from ≥ 100 packages                  |
| Does more data fix overfitting?              | Val/train ratio < 2x                            | Val/train ratio < 1.5x                                 |
| Does the clean IR help generation quality?   | Val loss < 1.5 nats (vs 1.59 in sprint 1)       | Val loss < 1.0 nats                                    |
| Does registry fix unblock composition tasks? | ≥ 3 of the 6 stuck tasks now produce RL winners | All 6 composition tasks solvable                       |
| Overall benchmark pass rate                  | ≥ 18/22 at pass@50 after RL                     | ≥ 20/22                                                |
| RL rounds compound                           | Improvement across ≥ 2 rounds                   | Monotonic improvement across 3 rounds                  |

### What This Validates

Sprint 2 is the **bridge between sprint prototyping and Stage 0 of the roadmap**. It answers: can we extract from real-world Haskell codebases, clean the output into a stable IR, and train a model that actually generalizes? If yes, Stage 0 becomes productionizing this pipeline (GHC API instead of text parsing, Hackage-wide coverage, published corpus). If no, we learn exactly where it breaks.

**If Sprint 2 succeeds:**

- The clean IR is validated — it can represent real Haskell functions without compiler noise
- Hackage extraction works — the path to 100K+ functions (Stage 1) is clear
- The model generalizes with enough data — scaling is the right strategy
- The full loop (extract → train → generate → execute → RL) works on real code, not just hand-picked examples

**If it fails, we learn specifically where:**

| Failure mode                                             | What it means                                   | Next step                                              |
| -------------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------ |
| Hackage extraction is too brittle (< 500 functions)      | Real-world Core is harder than hand-picked      | Use GHC API (plugin) instead of text dump parsing      |
| Clean IR loses essential information (round-trip fails)  | Some GHC Core constructs carry semantic meaning | Define IR more carefully, keep necessary constructs    |
| More data doesn't fix overfitting (val/train still > 2x) | Model architecture needs work, not just data    | Try tree-positional encodings, different architectures |
| Clean IR doesn't improve generation quality              | The noise wasn't the bottleneck                 | Focus on model/training improvements instead           |

### Key Risks

| Risk                                                                      | Likelihood | Mitigation                                                                                                 |
| ------------------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------- |
| Hackage packages fail to compile with our GHC flags                       | High       | Accept < 50% package success rate. Log failures, extract what we can. Use well-known packages first        |
| Parser can't handle new Core constructs from real packages                | Medium     | Track parse failures. Extend parser for common new constructs, skip rare ones                              |
| Cleaning pass strips something semantically important                     | Medium     | The validation gate catches this — functions that don't round-trip are skipped. Monitor skip rate          |
| Hackage functions are too complex (> 300 tokens after serialization)      | Medium     | Enforce a length cap. Most utility functions are short. Long functions are less useful for training anyway |
| The vocabulary explodes (thousands of unique function names from Hackage) | High       | Cap external function references. Map rare names to `<UNK>` or a type-based token. Keep vocab < 2000       |

### Timeline

| Task                                                                    | Estimate       |
| ----------------------------------------------------------------------- | -------------- |
| Define clean IR schema (`ir_schema.json`)                               | 0.5 day        |
| Write cleaning pass (`clean.py`)                                        | 2-3 days       |
| Write Hackage batch extractor (`extract_hackage.py`)                    | 1-2 days       |
| Test existing parser on 10 Hackage packages, measure failure rate       | 0.5 day        |
| Extend parser for new constructs OR write GHC plugin (based on results) | 1-3 days       |
| Run full extraction on target packages, iterate on failures             | 2-3 days       |
| Update augmentation, tokenizer for new corpus                           | 1 day          |
| Update pretty-printer and type checker for clean IR                     | 1-2 days       |
| Fix RL bugs (registry, parallelization, dep resolution)                 | 1-2 days       |
| Training experiments                                                    | 2-3 days       |
| Benchmark + RL evaluation                                               | 1-2 days       |
| Analysis + status writeup                                               | 1 day          |
| **Total**                                                               | **~2-3 weeks** |

### Key Constraints

- Same infrastructure: laptop + 1 GPU, Python + PyTorch
- New dependency: `cabal-install` for downloading Hackage packages (already available on Arch: `pacman -S cabal-install`)
- Still disposable code — production quality is Stage 0+
- Training should complete in < 2 hours (larger corpus, same GPU)
- Sprint 2 code replaces sprint 1 in `sprint/` (sprint 1 results are recorded in docs/)

### Relationship to Roadmap Stages

Sprint 2 directly prototypes **Stage 0** (AST Extraction Tool) and begins **Stage 1** (Corpus Construction):

| Roadmap component | Sprint 2 version                | Production version (Stage 0/1)                  |
| ----------------- | ------------------------------- | ----------------------------------------------- |
| AST schema        | `ir_schema.json` (informal)     | Formal JSON Schema or Protocol Buffers          |
| Extraction        | `ghc -ddump-simpl` text parsing | GHC plugin or GHC API driver                    |
| Cleaning          | Python post-processing pass     | Integrated into extraction pipeline             |
| Batch processing  | Script over `cabal unpack`      | CI-integrated Hackage-wide runner               |
| Validation        | `runhaskell` round-trip         | Property-based testing + QuickCheck             |
| Corpus            | 2K-5K functions in JSON         | 500K+ functions in Parquet/Arrow on HuggingFace |
| Tokenizer         | WordLevel, < 2000 tokens        | WordLevel or BPE, < 50K tokens                  |

If sprint 2 succeeds, the jump to Stage 0/1 is a matter of engineering quality — replacing scripts with tools, text parsing with API access, 5K functions with 500K. The IR and the approach are validated.

### Resources

- **Team:** 1 engineer
- **Compute:** 1 consumer GPU (RTX 5090). CPU for extraction and benchmark evaluation
- **Duration:** 2-3 weeks
- **Cost:** Effectively zero beyond existing hardware
- **New dependency:** `cabal-install` (`pacman -S cabal-install`)
