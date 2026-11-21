# Sprint 0

The first iteration of [[ast_pipeline_roadmap.md]].
Core objectives is to provide the MVP to evaluate if the idea and implementation have legs.

## Sprint 0: End-to-End Proof of Loop

**Duration: 4-6 weeks. Team: 1-2 people. Compute: 1 GPU + laptop.**

This is the startup version. Build the thinnest possible slice of every stage and run the full pipeline once. The goal is not quality — it is to prove that data flows from source code to extracted AST to trained model to generated program to execution and test. Every shortcut is acceptable. Every component is disposable. The only thing that matters is closing the loop.

### Why This Comes First

The full roadmap commits 12-15 months and significant resources. Sprint 0 answers the existential question in 4-6 weeks: **does the loop close?** If the model learns nothing, or the extraction is fundamentally broken, or the generated ASTs can't be executed, you discover this before spending months on infrastructure.

This mirrors how the most effective AI labs work:

- OpenAI built GPT-1 (small, crude, end-to-end) before investing in GPT-2's data pipeline
- DeepMind built a simple AlphaGo prototype on a small board before scaling to the full game
- Every YC startup builds an MVP before building the real product

### The Mini Pipeline

Each component is the smallest version that proves the connection works:

#### Mini Extraction (1 week)

**What:** Extract GHC Core from 50-100 hand-picked Haskell functions. Not a production tool — a script.

**How:** Use `ghc -ddump-simpl` to dump Core as text. Write a Python parser for the text output (~200 lines). No GHC API integration, no plugin architecture. Just text parsing of dumped Core.

**Source functions:** Hand-pick ~100 definitions from `base` and `containers` — `map`, `filter`, `foldr`, `sort`, `zip`, `lookup`, `insert`, `delete`, `reverse`, `take`, `drop`, `partition`, plus ~50 smaller helper functions. Choose a range of complexities (5-100 AST nodes).

**Output:** 100 typed ASTs in JSON format.

**Good enough if:** The 100 functions parse correctly and include type annotations. Coverage, speed, and robustness don't matter.

#### Mini Corpus (3 days)

**What:** Normalize, serialize, and tokenize the 100 extracted functions.

**How:** Write a simple alpha-normalizer (rename all variables to `v0`, `v1`, etc.). Write a depth-first serializer that outputs token sequences. Build a `WordLevel` tokenizer from the vocabulary that appears in the 100 functions.

**Stretch:** Augment the corpus with synthetic variations — alpha-renamed copies, reordered let bindings, inlined definitions. This can 5-10x the effective corpus size cheaply.

**Output:** ~100-1000 serialized AST sequences. A tokenizer with ~100-200 tokens.

**Good enough if:** The sequences tokenize cleanly and can be detokenized back to valid ASTs.

#### Mini Model (1 week)

**What:** Train the smallest possible transformer on next-token prediction over the mini corpus.

**How:** A 2-4 layer transformer with ~1-5M parameters. Use a simple PyTorch training loop or nanoGPT (Karpathy's minimal GPT implementation — ~300 lines, purpose-built for small experiments). Train for a few hours on 1 GPU.

**The corpus is tiny (100 functions), so the model will overfit.** That's fine. The question is not whether it generalizes — it's whether it learns the structure at all. Does loss decrease? Does the model produce sequences that look like ASTs rather than random tokens? Does it learn that `APP` is followed by two sub-expressions?

**Output:** A tiny trained model. Loss curves. Sample generations.

**Good enough if:** Loss converges well below random (random = log(vocab_size) ≈ 5-6 nats). Generated sequences are recognizably AST-shaped (correct bracketing, node types in plausible positions) at least 20% of the time.

#### Mini Type Checker (1 week)

**What:** A bare-minimum type checker for the core calculus, supporting only function types (`→`) and a few base types (`Int`, `Bool`, `List`).

**How:** Implement bidirectional type checking for `App`, `Lam`, `Var`, `Lit` in Python. ~100-200 lines. No polymorphism, no algebraic data types, no type inference. Just: does this application have matching types?

**Integrate with the model:** After the model generates a token, check if it's type-compatible. If not, reject and resample. This is the mini version of type-constrained decoding.

**Output:** A Python function `is_type_valid(partial_ast, next_token) -> bool`. A generation loop that calls it.

**Good enough if:** The type checker correctly accepts well-typed programs and rejects ill-typed ones for the supported subset. Type-constrained generation produces programs that type-check 100% of the time (for the supported subset).

#### Mini Benchmark + Execution (1 week)

**What:** 10-20 synthesis tasks. A script that compiles and runs generated Haskell.

**Tasks:** Hand-write 10-20 type signatures with test cases:

```
identity : Int → Int                    tests: f(5) = 5, f(0) = 0
successor : Int → Int                   tests: f(5) = 6, f(0) = 1
double : Int → Int                      tests: f(3) = 6, f(0) = 0
sum : List Int → Int                    tests: f([1,2,3]) = 6, f([]) = 0
length : List Int → Int                 tests: f([1,2,3]) = 3, f([]) = 0
reverse : List Int → List Int           tests: f([1,2,3]) = [3,2,1]
map_double : List Int → List Int        tests: f([1,2,3]) = [2,4,6]
```

**Execution:** Write a Python script that:

1. Takes a generated AST
2. Pretty-prints it to Haskell source (a simple recursive printer, ~100 lines)
3. Writes it to a `.hs` file
4. Calls `runhaskell` with a timeout
5. Compares output to expected

No Docker, no sandboxing, no parallelism. Just `subprocess.run(["runhaskell", file], timeout=5)`.

**Output:** pass@k numbers for the mini benchmark.

**Good enough if:** The sandbox works (correct programs pass, wrong programs fail). The model produces at least 1 correct program across all tasks at pass@100. Even 1 success proves the loop closes.

#### Mini RL (1 week)

**What:** The simplest possible learning-from-execution loop. Not PPO, not MCTS. Just **rejection sampling with fine-tuning**.

**How:**

1. For each benchmark task, generate 100 candidate programs (with type-constrained decoding)
2. Run each through the execution sandbox
3. Collect the programs that pass all tests (the "winners")
4. Fine-tune the model on the winners for a few epochs
5. Generate another 100 candidates
6. Did pass rate improve?

This is expert iteration in its simplest form. It's what DeepSeek-Prover V1 essentially did before adding MCTS. It validates that execution feedback can improve the model without implementing a full RL pipeline.

**Output:** pass@k before and after one round of rejection-sampling fine-tuning.

**Good enough if:** pass rate improves at all after fine-tuning on successful programs. Even a 5% improvement proves the feedback loop works.

### Sprint 0 Success Criteria

| Question                                     | How to answer                              | Minimum bar                                 |
| -------------------------------------------- | ------------------------------------------ | ------------------------------------------- |
| Can we extract typed ASTs from real Haskell? | Mini extraction processes 100 functions    | ≥ 80 of 100 parse correctly                 |
| Does a transformer learn AST distributions?  | Loss curves from mini model                | Loss well below random baseline             |
| Can type checking constrain generation?      | Type-check rate with vs. without masking   | 100% with masking, measurably lower without |
| Can generated programs actually execute?     | Run generated ASTs through the sandbox     | At least 1 program passes tests             |
| Does execution feedback improve the model?   | pass@k before vs. after rejection sampling | Any measurable improvement                  |

**If all five answers are yes:** The loop is closed. Proceed to the full stages with confidence, deepening each component.

**If any answer is no:** You've identified the weak link in 4-6 weeks instead of 12 months. Fix it before proceeding. Possible outcomes:

- Extraction fails → investigate a different language or different IR level
- Model doesn't learn → investigate different serialization, different model architecture
- Type checking doesn't help → investigate richer type annotations
- Execution fails → investigate the pretty-printer / re-elaboration step
- Feedback doesn't improve → investigate different selection/training strategies

### What Sprint 0 Code Looks Like

Everything is scripts. Nothing is production. Expected codebase:

```
sprint0/
├── extract/
│   ├── parse_ghc_core.py         # ~200 lines, parses -ddump-simpl output
│   └── functions.txt             # list of 100 functions to extract
├── corpus/
│   ├── normalize.py              # ~100 lines, alpha-rename + serialize
│   └── tokenizer.py              # ~50 lines, build WordLevel tokenizer
├── model/
│   ├── train.py                  # ~200 lines, or fork nanoGPT
│   └── generate.py               # ~100 lines, autoregressive sampling
├── typecheck/
│   ├── checker.py                # ~200 lines, minimal bidirectional checker
│   └── constrained_decode.py     # ~50 lines, mask + resample loop
├── benchmark/
│   ├── tasks.json                # 10-20 tasks with type sigs + tests
│   ├── run.py                    # ~100 lines, AST → .hs → runhaskell → check
│   └── pretty_print.py           # ~100 lines, AST → Haskell source
├── rl/
│   ├── rejection_sample.py       # ~100 lines, generate → filter → finetune
│   └── evaluate.py               # ~50 lines, pass@k measurement
└── README.md
```

~1200 lines of Python total. No frameworks beyond PyTorch. No infrastructure. Runs on a laptop with one GPU.

### Resources

- **Team:** 1-2 engineers (1 who knows Haskell/GHC, 1 who knows PyTorch — or 1 person who knows both)
- **Compute:** 1 consumer GPU (RTX 3090/4090) for the 5M parameter model. CPU for everything else
- **Duration:** 4-6 weeks
- **Cost:** Effectively zero beyond existing hardware
