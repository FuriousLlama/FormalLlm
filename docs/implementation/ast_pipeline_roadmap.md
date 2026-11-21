# AST Pipeline Roadmap

## Building a Computation-Learning System in Incremental Stages

Philosophical background: [[../theoretical/philosophical_foundation.md]]
Theoretical background: [[../theoretical/learning_computation_from_primitives.md]]

---

## Overview

This roadmap breaks the construction of a typed-AST computation learner into eight stages. Each stage produces independent value — a tool, a dataset, a model, or a capability that is useful even if the project advances no further. Each stage's output feeds the next. The structure mirrors how large language model laboratories build systems: data infrastructure first, small-scale validation second, scaling third, post-training refinement fourth.

**Before the full stages begin, Sprint 0 builds a minimal end-to-end prototype of the entire pipeline in 4-6 weeks.** This validates the full loop — from source code to trained model to generated programs — at the smallest possible scale, before investing months in any single stage. Every subsequent stage then deepens one part of a pipeline that is already proven to work end-to-end.

### Stage Dependency Graph

```
                Sprint 0: End-to-End Proof of Loop
                (mini version of everything, 4-6 weeks)
                            │
            validates ──────┼────── validates
            the loop        │       the approach
                            │
                            ▼
                Stage 0: AST Extraction Tool
                    │
                    ▼
                Stage 1: Corpus Construction ──────────────────────┐
                    │                                               │
                    ▼                                               │
                Stage 2: Small Model (Proof of Concept)             │
                    │                                               │
                    ├──────────────────┐                            │
                    ▼                  ▼                            │
                Stage 3: Type-        Stage 4: Execution            │
                Constrained           Harness + Benchmarks          │
                Decoding                   │                        │
                    │                      │                        │
                    └──────────┬───────────┘                        │
                               ▼                                    │
                    Stage 5: RL from Execution                      │
                               │                                    │
                               ▼                                    │
                    Stage 6: Abstraction Discovery ◄────────────────┘
                               │
                               ▼
                    Stage 7: Scaling + Generalization
```

Stages 3 and 4 can proceed in parallel after Stage 2. All other stages are sequential.

---

## Sprints

- Sprint 0: Most basic proof of concept [[sprint0/sprint0.md]]
- Sprint 1: From memorization to generalization [[sprint1/sprint1.md]]
- Sprint 2: Scale & clean — real corpus from Hackage, clean IR [[sprint2/sprint2.md]]

---

## Stage 0: AST Extraction Tool

**LLM lab analogy:** Building the tokenizer and data ingestion pipeline.

### Goal

Build a tool that takes source code in a typed language and outputs its typed core AST in a standardized, machine-readable format. Validate that the extraction is correct, complete, and fast enough to process a large package ecosystem.

### Independent Value

A production-quality AST extraction tool is useful for programming language research, code analysis, refactoring tools, and code search — independent of anything else in this project. No public tool currently provides bulk extraction of typed core representations across an entire package ecosystem in a standardized format.

### Deliverables

1. **`ast-extract` CLI tool** — takes a source file or project and outputs typed core ASTs in a standardized JSON/MessagePack format
2. **Standardized AST schema** — a documented format for representing typed core AST nodes, shared across languages
3. **Round-trip validation suite** — tests confirming that extracted ASTs can be re-elaborated back to valid source

### Starting Language

**Haskell (GHC Core)** is the recommended starting point.

Rationale:

- GHC Core is the most mature, well-documented, and stable typed IR in any compiler
- GHC provides a programmatic API for extracting Core (`ghc` package, `desugarModule`)
- Hackage is the largest ecosystem of programs in a strongly-typed functional language (~16,000 packages)
- GHC Core has only 7 meaningful constructors — the smallest core of any practical language
- Extensive prior work exists on analyzing and transforming GHC Core (the `ghc-dump` package, the `HERMIT` project)

Lean 4 is the second target (Stage 7), chosen for its dependent types and proof capabilities.

### Key Tasks

1. Write a GHC plugin or GHC API driver that intercepts Core after desugaring and type checking
2. Define the standardized AST node schema (JSON Schema or Protocol Buffers)
3. Implement serialization: GHC `CoreExpr` → standardized format
4. Implement alpha-normalization (de Bruijn index conversion for GHC Core's named binders)
5. Implement cleanup passes: strip source locations, inline trivial lets, remove compiler-internal annotations
6. Implement type serialization: GHC `Type` → standardized type format
7. Build a batch runner that processes an entire Cabal/Stack project and outputs all definitions
8. Write round-trip tests: extract Core → re-elaborate to Haskell → compile → compare behavior

### Success Metrics

| Metric                 | Target                                                                    | How to measure                                    |
| ---------------------- | ------------------------------------------------------------------------- | ------------------------------------------------- |
| Package coverage       | ≥ 90% of Hackage's top 1000 packages process without errors               | Run extraction on top 1000, count successes       |
| Definition coverage    | ≥ 95% of definitions within successfully processed packages are extracted | Count extracted vs. total definitions per package |
| Extraction speed       | ≤ 2x compilation time overhead                                            | Time extraction vs. normal `ghc --make`           |
| Round-trip correctness | 100% of extracted ASTs, when re-elaborated, type-check                    | Compile the re-elaborated output                  |
| Schema completeness    | All 7 GHC Core constructors and all primitive types represented           | Manual audit against GHC Core spec                |

### Resources

- **Team:** 1-2 engineers with GHC/Haskell experience
- **Compute:** A single workstation (builds are CPU-bound)
- **Duration:** 4-8 weeks
- **Dependencies:** GHC toolchain, `ghc` package, Hackage package index

### Risks

| Risk                                                                                                     | Likelihood | Mitigation                                                                                                   |
| -------------------------------------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------ |
| GHC API instability across versions                                                                      | Medium     | Pin to a specific GHC version (9.6 or 9.8 LTS). Support only one version initially                           |
| Some packages have complex build systems that resist automated extraction                                | High       | Accept <100% coverage. Start with packages that build cleanly with `cabal build`                             |
| GHC Core contains compiler-specific constructs (coercions, rules) that don't map to the universal schema | Medium     | Document what's excluded. Coercions can be erased (they're computationally irrelevant). Rules can be ignored |

### Go/No-Go for Stage 1

Proceed if: ≥80% of the top 500 Hackage packages process successfully, and extracted ASTs round-trip to compilable Haskell. If extraction fails on a large fraction of packages, investigate whether the failures are fixable (build system issues, edge cases) or fundamental (GHC Core is harder to extract than expected).

---

## Stage 1: Corpus Construction

**LLM lab analogy:** Building The Pile / CommonCrawl filtering — constructing the training dataset.

### Goal

Process the Hackage ecosystem through the Stage 0 extraction tool to produce a large, clean, normalized, deduplicated corpus of typed core ASTs serialized in the format needed for model training.

### Independent Value

A large corpus of typed core ASTs is a novel research artifact. It enables analysis of code structure at the core level — distribution of operations, frequency of patterns, type signature statistics — that is impossible with surface-level code. It is publishable and useful for programming language research, software engineering research, and other ML-for-code projects.

### Deliverables

1. **The corpus** — millions of function definitions as serialized typed ASTs, stored in Parquet/Arrow format compatible with HuggingFace `datasets`
2. **The tokenizer** — a `WordLevel` tokenizer (HuggingFace `tokenizers`) mapping AST node types, types, and values to integer IDs
3. **Corpus statistics report** — vocabulary distribution, sequence length distribution, most frequent subtrees, type signature distribution, duplication analysis
4. **Train/validation/test split** — stratified by package (no data leakage: all definitions from a package go to the same split)

### Key Tasks

1. Run Stage 0 extraction on all of Hackage (or the top N packages by download count, where N is determined by compute budget)
2. Implement deduplication: exact deduplication (identical ASTs) and near-deduplication (ASTs differing only in variable names — should be caught by alpha-normalization, but verify)
3. Implement quality filtering: remove trivially small definitions (< 3 nodes), remove compiler-generated definitions (instances, default methods), remove definitions that are just reexports
4. Build the tokenizer: enumerate all node types, all primitive types, all type constructors. Handle named constants (references to other definitions) — either as a fixed vocabulary of the N most common constants plus an `UNK` token, or as a separate embedding table
5. Serialize the entire corpus to the training format: sequences of token IDs with metadata (source package, original name, type signature)
6. Compute and report corpus statistics
7. Publish the corpus and tokenizer (HuggingFace Hub or equivalent)

### Success Metrics

| Metric                       | Target                                                               | How to measure                            |
| ---------------------------- | -------------------------------------------------------------------- | ----------------------------------------- |
| Corpus size (definitions)    | ≥ 500,000 function definitions                                       | Count after deduplication and filtering   |
| Corpus size (tokens)         | ≥ 100M AST tokens                                                    | Count total tokens across all definitions |
| Vocabulary coverage          | ≥ 99% of tokens in the corpus covered by the tokenizer (< 1% `UNK`)  | Tokenize the corpus, count UNK rate       |
| Sequence length distribution | Median 20-80 tokens, 95th percentile < 500 tokens                    | Histogram of sequence lengths             |
| Deduplication rate           | Report exact and near-duplicate counts                               | Compare before/after dedup                |
| Split balance                | Train 90%, validation 5%, test 5%, no package overlap between splits | Verify split membership                   |

### Resources

- **Team:** 1 engineer
- **Compute:** A multi-core server or small cluster for parallel extraction. ~100-500 CPU-hours for full Hackage extraction. Storage: ~10-50 GB for the final corpus
- **Duration:** 2-4 weeks (after Stage 0 is complete)
- **Dependencies:** Stage 0 extraction tool, Hackage package index, HuggingFace `datasets` and `tokenizers`

### Risks

| Risk                                                                                | Likelihood             | Mitigation                                                                                                                     |
| ----------------------------------------------------------------------------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Corpus is too small (< 100K definitions after filtering)                            | Low (Hackage is large) | Expand to additional sources: Stackage snapshots, GitHub Haskell projects outside Hackage                                      |
| Vocabulary of named constants is too large (millions of unique function references) | Medium                 | Cap the vocabulary at the top 10K-50K most frequent constants. Less frequent ones map to UNK or to a type-based representation |
| Sequence lengths are too long (most definitions are hundreds of nodes)              | Low                    | Investigate: are long definitions mostly boilerplate? Can they be broken into sub-definitions?                                 |

### Go/No-Go for Stage 2

Proceed if: corpus contains ≥100K definitions with ≥50M tokens, the vocabulary is manageable (< 50K tokens with < 5% UNK rate), and the sequence length distribution looks reasonable (median < 100 tokens). If the corpus is too small or too noisy, consider adding more source languages (OCaml, Lean) before proceeding.

---

## Stage 2: Small Model Proof of Concept

**LLM lab analogy:** Training small models to validate the approach and find scaling laws (Chinchilla-style experiments).

### Goal

Train a small transformer (50M-200M parameters) on next-node prediction over the corpus. Validate the core hypothesis: **does a transformer learn meaningful statistical structure from typed AST tokens?** This is the critical proof-of-concept gate. If the model learns nothing beyond random prediction, the approach is invalidated. If it learns syntactic and compositional structure, the approach is validated and worth scaling.

### Independent Value

Even a small model that understands AST structure has research value: it reveals what computational patterns are statistically dominant, how strongly types predict next operations, and whether the distributional hypothesis holds for programs. The scaling law data (how performance changes with model size and data size) informs resource allocation for all subsequent stages.

### Deliverables

1. **Trained model** — a decoder-only transformer (50M, 100M, 200M parameter variants) trained on next-node prediction over the Stage 1 corpus
2. **Scaling law analysis** — loss curves for each model size, compute-optimal analysis (what model size is optimal for a given compute budget?)
3. **Qualitative analysis** — examples of generated ASTs, assessment of structural coherence, attention pattern analysis
4. **Ablation study** — does including type annotations in the tokens improve prediction? How much? This isolates the contribution of types to the learning problem

### Key Tasks

1. Implement the model: decoder-only transformer with the AST tokenizer's vocabulary. Standard architecture (Llama-style: RMSNorm, rotary position embeddings, SwiGLU). The only non-standard element is the small vocabulary
2. Implement the training loop: standard autoregressive language modeling (cross-entropy loss, AdamW optimizer, cosine learning rate schedule). Use HuggingFace Transformers or a custom PyTorch implementation
3. Train three model sizes: 50M, 100M, 200M parameters. Each for a fixed compute budget (e.g., 10B tokens seen)
4. Evaluate: perplexity on held-out test set, syntactic validity of generated sequences (do generated sequences parse as valid ASTs?), type consistency (do the type annotations in generated sequences form valid type derivations?)
5. Ablation: train a variant without type annotations in the tokens. Compare perplexity and syntactic validity. This measures the value of types as training signal
6. Qualitative: sample completions from the model given partial ASTs. Do they look like plausible programs? Do they exhibit known patterns (map, fold, recursion)?
7. Analyze learned representations: what do the attention heads attend to? Do some heads specialize in tree structure (attending to parent/child nodes)? Do some specialize in type compatibility?

### Success Metrics

| Metric                            | Target                                                                                                                        | How to measure                                                     |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Perplexity (test set)             | Significantly below random baseline (random baseline = vocab size)                                                            | Evaluate on held-out test set                                      |
| Perplexity improvement from types | ≥ 10% lower perplexity with type annotations vs. without                                                                      | Compare typed vs. untyped ablation                                 |
| Syntactic validity                | ≥ 50% of generated sequences (greedy decoding, 100 tokens) parse as valid AST fragments                                       | Parse generated sequences with the AST schema validator            |
| Pattern emergence                 | The model assigns higher probability to known algorithmic patterns (map, fold, filter) than to random well-typed alternatives | Measure log-likelihood of known patterns vs. shuffled alternatives |
| Scaling behavior                  | Loss decreases predictably with model size (power law fit R² ≥ 0.95)                                                          | Plot loss vs. parameters for 50M/100M/200M, fit power law          |

### Resources

- **Team:** 1-2 ML engineers
- **Compute:** 1-4 GPUs (A100/H100 class) for 1-2 weeks. The 200M model on 10B tokens is roughly 1 GPU-week. Total: ~4-8 GPU-weeks for all variants and ablations
- **Duration:** 4-6 weeks (including implementation, training, and analysis)
- **Dependencies:** Stage 1 corpus and tokenizer, PyTorch, standard training infrastructure

### Risks

| Risk                                                                          | Likelihood | Mitigation                                                                                                                                                                                                          |
| ----------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The model doesn't learn meaningful structure (perplexity barely below random) | Low-Medium | If this happens, investigate: is the serialization format losing critical structure? Are the sequences too long? Is the corpus too noisy? Try tree-positional encodings before concluding the approach doesn't work |
| Syntactic validity is very low (< 20%)                                        | Medium     | The model may need explicit structural tokens or grammar-constrained sampling. Try adding depth/bracket-count features                                                                                              |
| Scaling laws don't show clear power-law behavior                              | Medium     | Three data points (50M/100M/200M) are minimum. If the curve is noisy, add 25M and 400M variants. Alternatively, the space may be learnable at small scale (loss plateaus early) — which is actually good news       |

### Go/No-Go for Stages 3-4

**This is the critical decision point.** Proceed if: the model's perplexity is well below random, syntactic validity is non-trivial (≥ 30%), and including type annotations measurably improves performance. These results validate that AST distributions are learnable and that types provide useful training signal.

If the model fails to learn meaningful structure, the project should pause here and investigate whether the representation (linearized ASTs) is the problem (try tree-structured models), whether the corpus is the problem (try a different language), or whether the hypothesis itself is wrong (statistical learning from core calculus primitives doesn't capture enough structure).

---

## Stage 3: Type-Constrained Decoding

**LLM lab analogy:** No direct analogy — this is novel to this approach. Closest parallel: constrained decoding / structured output in LLMs (JSON mode, grammar-constrained generation).

### Goal

Integrate a type checker into the decoding loop so that every generated AST is well-typed by construction. The model proposes; the type checker disposes. The output is a generator that produces structurally correct programs with 100% type-check rate.

### Independent Value

A code generator that guarantees structural correctness (every output type-checks) is valuable regardless of whether the output is semantically correct. It eliminates an entire class of errors that plague LLM code generation. It is usable as a "type-safe autocomplete" tool — given a partial typed program, generate well-typed completions.

### Deliverables

1. **Bidirectional type checker** — a standalone type checker for the core calculus, implemented in Python, fast enough for use during decoding (~1ms per check)
2. **Type-masked decoding** — integration with the model's sampling loop: at each step, compute the set of type-valid next tokens and mask out everything else before sampling
3. **Type-safe completion tool** — a CLI/API that takes a partial typed AST and a type goal, and generates well-typed completions

### Key Tasks

1. Implement a bidirectional type checker for the core calculus in Python. The typing rules for the core (App, Lam, Let, Case, Var, Lit, Constructor) are well-understood and compact — roughly 300-500 lines of Python. Support: function types (`→`), polymorphic types (`∀`), algebraic data types, basic type inference
2. Implement the type mask: given the current partial AST and the type context, compute which tokens in the vocabulary are type-compatible at the current position. Return a binary mask over the vocabulary
3. Integrate the mask into the model's sampling loop: multiply the model's logits by the mask before softmax, then sample from the filtered distribution
4. Optimize for speed: the type checker will be called once per generated token. It must be fast. Profile and optimize. Consider caching: if the type context hasn't changed, the mask hasn't changed
5. Evaluate: generate 1000 completions with and without type masking. Compare type-check rates, generation quality (perplexity of the accepted tokens), and generation speed

### Success Metrics

| Metric                                      | Target                                                                         | How to measure                                                |
| ------------------------------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------- |
| Type-check rate (with masking)              | 100%                                                                           | Type-check every generated AST. Zero failures by construction |
| Type-check rate (without masking, baseline) | Report (expected: 10-60%)                                                      | Type-check Stage 2 model's unconstrained generations          |
| Generation quality                          | Perplexity of type-valid tokens ≤ 1.5x perplexity of unconstrained model       | Compare log-likelihood of accepted tokens                     |
| Decoding speed                              | ≤ 3x slowdown vs. unconstrained decoding                                       | Time both methods on identical inputs                         |
| Mask density                                | Report average fraction of vocabulary that passes the type filter at each step | Log mask statistics during generation                         |

### Resources

- **Team:** 1 engineer with PL/type theory background
- **Compute:** Minimal (type checking is CPU-bound, model inference is 1 GPU)
- **Duration:** 3-5 weeks
- **Dependencies:** Stage 2 trained model, the AST schema from Stage 0

### Risks

| Risk                                                                                          | Likelihood | Mitigation                                                                                                                                                               |
| --------------------------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Type checking is too slow per token (> 10ms)                                                  | Medium     | Profile and optimize. Cache type contexts. Implement in Rust/C and call via FFI if Python is too slow                                                                    |
| The type mask is too restrictive (filters out > 99% of vocabulary, leaving almost no choices) | Low-Medium | This would actually be good for search (tiny branching factor). But if it's too restrictive, the model may have no good options. Monitor and adjust type precision       |
| The type mask is too permissive (filters out < 10% of vocabulary)                             | Medium     | This means types aren't constraining much. Investigate: are the type annotations too coarse? Add richer type information (refinement types, more precise type inference) |

### Go/No-Go

Proceed if: type masking produces 100% type-check rate without catastrophic quality degradation (quality within 2x of unconstrained model). If the type checker is too slow, optimize it before proceeding.

---

## Stage 4: Execution Harness + Benchmarks

**LLM lab analogy:** Building the evaluation suite (MMLU, HumanEval, GSM8K, etc.).

### Goal

Build the infrastructure to execute generated programs and check them against specifications. Design a benchmark suite of synthesis tasks with known solutions, covering a range of difficulties. This is the evaluation backbone for all subsequent stages.

### Independent Value

A well-designed benchmark suite for typed program synthesis is a contribution to the field. Existing benchmarks (HumanEval, MBPP) are text-based and language-specific. A benchmark defined at the typed core level, with formal specifications (type signatures + tests + properties), is more rigorous and enables apples-to-apples comparison across approaches.

### Deliverables

1. **Execution sandbox** — a tool that takes a generated AST, re-elaborates it to Haskell source, compiles it, and runs it against test cases in a sandboxed environment with resource limits
2. **Benchmark suite** — 200+ synthesis tasks organized by difficulty tier, each specified by a type signature and test cases
3. **Baseline measurements** — pass@k rates for the Stage 2 model (with and without type masking) on the benchmark suite
4. **LLM baseline** — pass@k rates for frontier LLMs (GPT-4, Claude) on equivalent tasks, for comparison

### Benchmark Tiers

| Tier | Difficulty        | Type signature complexity            | Example tasks                             | Count |
| ---- | ----------------- | ------------------------------------ | ----------------------------------------- | ----- |
| 1    | Trivial           | `A → A`, `A → B` (simple types)      | identity, successor, boolean not          | 30    |
| 2    | Basic             | `List A → B`, `A → List B`           | sum, length, replicate, range             | 40    |
| 3    | Standard          | `List A → List A`, `List A → List B` | reverse, map, filter, take, drop          | 50    |
| 4    | Compositional     | Multi-argument, higher-order         | sort, zip, groupBy, partition             | 40    |
| 5    | Algorithmic       | Recursive, multi-step                | mergesort, binary search, tree operations | 30    |
| 6    | Dependently-typed | Types with properties                | sort-with-proof, balance-with-invariant   | 20    |

### Key Tasks

1. Build the execution sandbox: AST → Haskell source (pretty-printer) → GHC compilation → run with timeout and memory limits → capture output → compare to expected. Use Docker/nsjail for isolation
2. Design the benchmark: for each task, specify the type signature, 5-20 test cases (input-output pairs), and optionally a reference implementation. Ensure tests are sufficient to distinguish correct from incorrect implementations (avoid tasks where trivially wrong programs pass all tests)
3. Implement pass@k evaluation: generate k candidate programs for each task, report the fraction of tasks where at least one candidate passes all tests. Standard metric from code generation literature (Chen et al., HumanEval)
4. Measure the LLM baseline: present each benchmark task to GPT-4/Claude as a Haskell programming task (natural language prompt with the type signature), evaluate their outputs on the same test suite
5. Measure the AST model baseline: generate completions from the Stage 2 model (with and without type masking), evaluate on the benchmark

### Success Metrics

| Metric                          | Target                                                        | How to measure                                    |
| ------------------------------- | ------------------------------------------------------------- | ------------------------------------------------- |
| Benchmark coverage              | ≥ 200 tasks across all tiers                                  | Count tasks                                       |
| Sandbox reliability             | 100% of correct reference implementations pass the test suite | Run reference implementations through the sandbox |
| Sandbox speed                   | ≤ 5 seconds per evaluation (compile + run + check)            | Time the sandbox on reference implementations     |
| AST model pass@1 (Tiers 1-2)    | Report (expected: 10-40%)                                     | Run benchmark                                     |
| AST model pass@10 (Tiers 1-2)   | Report (expected: 20-60%)                                     | Run benchmark                                     |
| LLM baseline pass@1 (Tiers 1-3) | Report (for comparison)                                       | Run benchmark                                     |

### Resources

- **Team:** 1-2 engineers
- **Compute:** Minimal for sandbox (CPU). 1 GPU for model inference. API costs for LLM baselines
- **Duration:** 3-5 weeks (can proceed in parallel with Stage 3)
- **Dependencies:** Stage 0 (for AST → source re-elaboration), Stage 2 (for model baselines)

### Risks

| Risk                                                                      | Likelihood                 | Mitigation                                                                                                                                                                                            |
| ------------------------------------------------------------------------- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Test suites are too weak (wrong programs pass)                            | Medium                     | Use property-based testing (QuickCheck) in addition to fixed test cases. Manually verify that at least one incorrect implementation fails                                                             |
| Re-elaboration from AST to compilable Haskell is buggy                    | Medium                     | Invest in the round-trip tests from Stage 0. Build a comprehensive suite of known ASTs with known Haskell equivalents                                                                                 |
| LLM baselines are overwhelmingly strong, making the AST approach look bad | High (likely on Tiers 1-3) | This is expected. The AST model's advantage should manifest on type-correctness rate (100% vs. LLMs' ~60-80%) and on higher tiers where formal constraints matter more. Frame the comparison honestly |

### Go/No-Go

Proceed if: the benchmark is solid (reference implementations pass, wrong implementations fail), the sandbox works reliably, and the AST model shows non-trivial performance on at least Tiers 1-2 (pass@10 > 10%). The absolute numbers will be low at this stage — the important thing is that the model produces _some_ correct programs, proving the pipeline works end-to-end.

---

## Stage 5: Reinforcement Learning from Execution

**LLM lab analogy:** RLHF / post-training alignment — using reward signals to improve the model beyond supervised learning.

### Goal

Implement a reinforcement learning loop that uses execution results (test pass/fail) as the reward signal. The model generates candidate programs, the sandbox evaluates them, and the model updates toward programs that pass tests. The goal is a significant improvement in pass@k rates over the supervised-only model from Stage 2.

### Independent Value

A self-improving program synthesizer — one that gets better by trying and testing — is independently valuable as a research demonstration and as a tool. Even modest pass@k improvements demonstrate that the RL-from-execution loop works, validating the broader architecture.

### Deliverables

1. **RL training loop** — PPO-based training that generates programs, evaluates them via the Stage 4 sandbox, and updates the model
2. **Value network** — a smaller model that estimates the probability of successful completion from a partial AST (used for search guidance)
3. **MCTS integration** — Monte Carlo Tree Search over the generation process, using the policy model for proposals and the value network for evaluation
4. **Improved model** — a model with measurably higher pass@k rates than the Stage 2 supervised model

### Key Tasks

1. Implement the RL loop:
   - Sample a task from the benchmark (type signature + tests)
   - Generate K candidate programs from the model (with type-constrained decoding)
   - Evaluate each candidate in the sandbox
   - Compute reward: +1 per test passed, bonus for all tests passing, penalty for non-termination
   - Update the model via PPO
   - Repeat
2. Implement the value network: a smaller transformer (10M-50M parameters) that takes a partial AST and outputs a scalar estimate of completion probability. Train on (partial_AST, eventual_outcome) pairs collected during the RL loop
3. Implement MCTS:
   - At each AST node, the policy model proposes K candidates (after type masking)
   - The value network scores each candidate
   - MCTS balances exploration and exploitation
   - Expand the most promising branches
   - Return the best complete program found within the compute budget
4. Implement curriculum learning: start the RL loop on Tier 1 tasks (trivial). Once pass@10 exceeds 80%, advance to Tier 2. Continue up the tiers
5. Evaluate: compare pass@k on all benchmark tiers before and after RL training

### Success Metrics

| Metric                          | Target                                                               | How to measure                    |
| ------------------------------- | -------------------------------------------------------------------- | --------------------------------- |
| pass@1 improvement (Tiers 1-2)  | ≥ 2x over Stage 2 baseline                                           | Benchmark evaluation              |
| pass@10 improvement (Tiers 1-3) | ≥ 2x over Stage 2 baseline                                           | Benchmark evaluation              |
| pass@1 on Tier 3 (standard)     | ≥ 20%                                                                | Benchmark evaluation              |
| Search efficiency               | ≤ 100 MCTS rollouts per successful synthesis (Tiers 1-2)             | Log rollout counts                |
| Value network accuracy          | ≥ 70% binary classification (will this partial AST lead to success?) | Evaluate on held-out partial ASTs |
| Training stability              | Loss decreases monotonically over training (no collapse)             | Monitor training curves           |

### Resources

- **Team:** 2-3 ML engineers
- **Compute:** 4-8 GPUs for RL training (2-4 weeks of training). The bottleneck is the sandbox: each RL step requires compiling and executing candidate programs. Parallelize sandbox execution across CPU cores
- **Duration:** 6-10 weeks
- **Dependencies:** Stages 2, 3, and 4

### Risks

| Risk                                                                         | Likelihood  | Mitigation                                                                                                                                                                                                                 |
| ---------------------------------------------------------------------------- | ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| RL training is unstable (reward hacking, mode collapse)                      | Medium-High | Use conservative PPO hyperparameters. Implement KL divergence constraint against the Stage 2 model. Monitor for reward hacking (programs that pass tests trivially, e.g., hardcoding outputs)                              |
| Sandbox is too slow for RL (need thousands of evaluations per training step) | High        | Parallelize aggressively: run 100+ sandbox instances in parallel. Cache compilation of common sub-programs. Consider interpreted execution (GHCi) instead of full compilation for speed                                    |
| The model doesn't improve with RL (pass@k stays flat)                        | Medium      | Investigate: is the reward signal too sparse? Add intermediate rewards (partial test passing, type-check bonuses). Is the search space too large? Tighten type constraints. Is the model too small? This motivates Stage 7 |

### Go/No-Go for Stage 6

Proceed if: RL produces measurable improvement (≥ 50% improvement in pass@10 on any tier). If RL doesn't help, investigate whether the model is too small (jump to Stage 7 scaling experiments) or whether the search strategy needs work (try different MCTS configurations, beam search alternatives).

---

## Stage 6: Abstraction Discovery

**LLM lab analogy:** No direct analogy. Closest parallel: vocabulary expansion, BPE merge operations, or DreamCoder's library learning.

### Goal

Analyze the trained model's behavior and the corpus of successfully synthesized programs to discover emergent computational patterns — subtree structures that recur frequently and correspond to useful algorithmic building blocks. Extract these as named abstractions, add them to the vocabulary, and retrain. Demonstrate that the expanded vocabulary reduces search depth and enables harder synthesis tasks.

### Independent Value

Automatically discovered computational abstractions are a novel research output. They reveal the statistical structure of computation — which patterns are fundamental, which are derived, and how they compose. The discovered abstractions may or may not match human-designed standard libraries, and any differences are scientifically interesting.

### Deliverables

1. **Pattern mining pipeline** — a tool that identifies frequently recurring typed subtrees in the corpus and in successfully synthesized programs
2. **Abstraction extraction** — parameterized abstractions derived from recurring patterns (the varying parts become parameters, the constant parts become the abstraction body)
3. **Expanded vocabulary and retrained model** — the model retrained with the new abstractions as first-class vocabulary entries
4. **Analysis report** — what was discovered, how it maps (or doesn't) to known algorithms and patterns, and the quantitative impact on synthesis

### Key Tasks

1. Implement frequent subtree mining: find subtrees of size ≥ 5 nodes that occur ≥ N times in the corpus (where N is tuned empirically). Use anti-unification to identify the common structure and the varying parts
2. Rank candidates by: frequency × size (larger, more frequent patterns are more valuable as abstractions)
3. For the top K candidates (start with K=10-20): parameterize them (replace varying parts with typed parameters), verify that the resulting abstraction type-checks, name them, and add them as new tokens in the vocabulary
4. Re-serialize the corpus using the expanded vocabulary: wherever a subtree matches an abstraction, replace it with the abstraction token. This produces shorter sequences
5. Retrain the model on the expanded corpus
6. Evaluate on the benchmark: does the expanded vocabulary improve pass@k? Does it reduce the average number of generation steps? Does it enable success on tasks that previously failed?
7. Iterate: after retraining, mine for new patterns in the updated model's outputs. Run 2-3 rounds of discover-extract-retrain

### Success Metrics

| Metric                             | Target                                                                                 | How to measure                         |
| ---------------------------------- | -------------------------------------------------------------------------------------- | -------------------------------------- |
| Patterns discovered                | ≥ 20 distinct abstractions meeting frequency and size thresholds                       | Count output of mining pipeline        |
| Correspondence to known algorithms | Report: how many map to standard library functions (map, fold, filter, etc.) vs. novel | Manual analysis                        |
| Sequence length reduction          | ≥ 15% shorter average sequence length after vocabulary expansion                       | Compare corpus statistics before/after |
| pass@k improvement                 | ≥ 20% improvement on Tiers 3-4 over Stage 5 model                                      | Benchmark evaluation                   |
| New task coverage                  | ≥ 5 Tier 4-5 tasks that the Stage 5 model couldn't solve but the Stage 6 model can     | Benchmark evaluation                   |
| Abstraction reuse rate             | ≥ 30% of successfully synthesized programs use at least one discovered abstraction     | Analyze generated programs             |

### Resources

- **Team:** 1-2 engineers (1 ML, 1 PL)
- **Compute:** 2-4 GPUs for retraining (1-2 weeks per round). CPU for pattern mining
- **Duration:** 4-8 weeks (2-3 rounds of discover-extract-retrain)
- **Dependencies:** Stages 1 and 5

### Risks

| Risk                                                                                       | Likelihood | Mitigation                                                                                                                                                                          |
| ------------------------------------------------------------------------------------------ | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Discovered patterns are too small (trivial) or too large (not reusable)                    | Medium     | Tune the size threshold. Require abstractions to have at least 1 parameter (otherwise they're constants, not patterns) and at most 50 nodes (otherwise they're too specific)        |
| The model doesn't benefit from the expanded vocabulary (retraining doesn't improve pass@k) | Medium     | The abstractions may not be the right ones. Try different mining thresholds. Try using the model's own attention patterns to identify important subtrees rather than pure frequency |
| Iteration doesn't converge (each round discovers fewer patterns with diminishing returns)  | Expected   | This is expected after 2-3 rounds. The first round captures the obvious patterns. Diminishing returns are natural and don't indicate failure                                        |

### Go/No-Go for Stage 7

Proceed if: at least one round of abstraction discovery produces measurable improvement. If the approach shows no benefit, the model may already be capturing these patterns internally (in which case, proceed to Stage 7 anyway — scaling may be the more productive direction).

---

## Stage 7: Scaling and Generalization

**LLM lab analogy:** Scaling runs — training the large model, expanding data, and pushing the capability frontier.

### Goal

Scale the system along three axes: model size (from 200M to 1-7B parameters), data (add Lean 4 and OCaml to the corpus), and problem difficulty (dependently-typed synthesis, larger programs, real-world tasks). Determine the scaling laws for this domain and push toward practically useful synthesis capabilities.

### Independent Value

A 1-7B parameter model trained on typed core calculi from multiple languages, capable of synthesizing verified programs from type signatures, is a competitive tool. At this stage the system should outperform LLM baselines on formally-specified synthesis tasks (where type correctness and formal verification matter), establishing a clear niche.

### Deliverables

1. **Multi-language corpus** — extend the corpus with Lean 4 (Mathlib + ecosystem) and OCaml (opam), using the unified core schema from Stage 0
2. **Scaled model** — 1B and 3-7B parameter variants trained on the expanded corpus
3. **Lean 4 integration** — type-constrained decoding using Lean's type checker (via Pantograph), enabling dependently-typed synthesis
4. **Scaling law report** — how does performance scale with model size, data size, and compute in this domain?
5. **Real-world evaluation** — performance on tasks drawn from actual software projects (not just benchmark puzzles)

### Key Tasks

1. **Extend Stage 0 to Lean 4:** Build AST extraction for Lean `Expr`. Lean already uses de Bruijn indices, so normalization is simpler. Extract from Mathlib (~180K+ declarations) and the Lean standard library
2. **Extend Stage 0 to OCaml:** Build AST extraction for OCaml `Typedtree`. Process the opam ecosystem
3. **Design the unified core schema:** Map Lean `Expr` constructors and OCaml Typedtree constructors to the same schema used for GHC Core. Document where the mapping is exact and where it approximates
4. **Rebuild the corpus** with all three languages. Retokenize with the expanded vocabulary (new type constructors from Lean's dependent types, new node types if any)
5. **Train scaled models:** 1B parameters (4-8 GPUs, 2-4 weeks), 3-7B parameters (8-32 GPUs, 4-8 weeks)
6. **Integrate Lean's type checker** for type-constrained decoding: use Pantograph to check candidate Lean expressions. This enables generating dependently-typed programs with proof obligations
7. **Add Tier 6 benchmarks:** dependently-typed synthesis tasks in Lean (e.g., verified sort, balanced tree insertion, correct-by-construction parsers)
8. **Evaluate cross-language transfer:** train on Haskell+OCaml, evaluate on Lean (zero-shot). Does knowledge of core calculus patterns transfer across languages?

### Success Metrics

| Metric                                           | Target                                                                | How to measure                    |
| ------------------------------------------------ | --------------------------------------------------------------------- | --------------------------------- |
| Multi-language corpus size                       | ≥ 2M definitions, ≥ 500M tokens                                       | Count after merging all languages |
| pass@1 on Tiers 1-3 (1B model)                   | ≥ 50%                                                                 | Benchmark evaluation              |
| pass@1 on Tiers 4-5 (3-7B model)                 | ≥ 30%                                                                 | Benchmark evaluation              |
| pass@1 on Tier 6 (dependently-typed, 3-7B model) | ≥ 10%                                                                 | Benchmark evaluation              |
| Type-correctness rate                            | 100% (maintained from Stage 3)                                        | Verify all outputs                |
| Outperforms LLM baseline on typed tasks          | pass@1 on Tiers 4-6 exceeds frontier LLMs                             | Side-by-side benchmark            |
| Cross-language transfer                          | Non-trivial zero-shot performance on held-out language (pass@10 > 5%) | Train on 2 languages, test on 3rd |
| Scaling law fit                                  | Power law R² ≥ 0.95 across 3+ model sizes                             | Plot and fit                      |

### Resources

- **Team:** 3-5 engineers (2 ML, 1-2 PL, 1 infrastructure)
- **Compute:** 8-32 GPUs (A100/H100) for 4-8 weeks. This is the most compute-intensive stage. Budget: ~$50K-200K in cloud compute, or equivalent on-premise
- **Duration:** 12-20 weeks
- **Dependencies:** All previous stages

### Risks

| Risk                                                                                   | Likelihood | Mitigation                                                                                                                                                                                                                                                                  |
| -------------------------------------------------------------------------------------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Cross-language schema mapping loses important structure                                | Medium     | Start with Haskell + OCaml (very similar core calculi). Lean's dependent types introduce genuinely new constructs (Π types, universe levels) that may not map cleanly. Accept that the mapping is approximate for dependent types and add Lean-specific tokens where needed |
| Scaling doesn't help (larger models aren't much better than small ones on this domain) | Low-Medium | The domain is more constrained than natural language, so scaling may saturate earlier. This is actually useful information — it means the approach is compute-efficient. Publish the scaling law data regardless                                                            |
| Lean's type checker is too slow for constrained decoding at scale                      | Medium     | Lean type-checking is fast for simple terms but can be slow for complex dependent types. Cache aggressively. Accept that Tier 6 tasks may require more generation time                                                                                                      |
| The project's scope becomes unmanageable                                               | Medium     | Maintain focus: the core contribution is the AST-level approach to computation learning. Multi-language support and scaling are extensions, not prerequisites. Cut scope if needed                                                                                          |

---

## Cumulative Timeline

| Stage             | Duration    | Cumulative   | Team size | Key gate                                           |
| ----------------- | ----------- | ------------ | --------- | -------------------------------------------------- |
| 0: AST Extraction | 4-8 weeks   | 2 months     | 1-2       | Extraction works on ≥80% of packages               |
| 1: Corpus         | 2-4 weeks   | 3 months     | 1         | ≥100K definitions, ≥50M tokens                     |
| 2: Small Model    | 4-6 weeks   | 4.5 months   | 1-2       | Perplexity well below random, patterns emerge      |
| 3: Type Decoding  | 3-5 weeks   | 5.5 months   | 1         | 100% type-check rate (parallel with 4)             |
| 4: Benchmarks     | 3-5 weeks   | 5.5 months   | 1-2       | Solid benchmark, working sandbox (parallel with 3) |
| 5: RL             | 6-10 weeks  | 8 months     | 2-3       | Measurable pass@k improvement                      |
| 6: Abstraction    | 4-8 weeks   | 10 months    | 1-2       | ≥1 round of successful discovery                   |
| 7: Scaling        | 12-20 weeks | 14-15 months | 3-5       | Competitive with LLMs on typed tasks               |

**Total estimated timeline: 12-15 months** from start to a scaled, multi-language system. The critical proof-of-concept gate is at **month 4.5** (end of Stage 2). Everything before that is infrastructure. Everything after is iterative improvement on a validated foundation.

**Total estimated compute: ~$10K-30K for Stages 0-6, ~$50K-200K for Stage 7** (cloud GPU pricing). Stages 0-6 are feasible on a single multi-GPU workstation. Stage 7 requires a small cluster or cloud allocation.

---

## Decision Points Summary

The roadmap has three critical decision points where the project should evaluate whether to continue, pivot, or stop:

**Gate 1: After Stage 0.** Can we extract typed core ASTs at scale? If not, the data pipeline is broken and nothing else works. This is a low-risk gate — the technology is mature.

**Gate 2: After Stage 2.** Does a transformer learn meaningful structure from typed AST tokens? This is **the** critical gate. If the model doesn't learn, either the representation needs to change (try tree-structured models, try a different serialization) or the hypothesis is wrong. All subsequent stages depend on this result.

**Gate 3: After Stage 5.** Does RL from execution improve synthesis? If yes, the approach is validated end-to-end and scaling (Stage 7) is justified. If no, investigate whether the model is too small (Stage 7 may help) or whether the search strategy is wrong (try different search algorithms before scaling).

Everything else is incremental improvement on a validated foundation.

---

_This roadmap breaks the construction of a typed-AST computation learner into eight stages, each delivering independent value and feeding the next. The stages mirror the process used by large language model laboratories: data infrastructure first, small-scale validation second, post-training refinement third, scaling fourth. Three critical decision points provide off-ramps if the approach doesn't validate._
