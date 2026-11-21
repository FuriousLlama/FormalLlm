# Sprint 3: Closed Terms

The fourth iteration of [[ast_pipeline_roadmap.md]].
Core objective: eliminate external function names from the token vocabulary via dependency injection (lambda lifting), making every function body a closed lambda term.

## Sprint 3: Can the Model Learn the Calculus Instead of the Library?

**Duration: ~1-2 weeks. Team: 1 person. Compute: 1 GPU + laptop.**

Sprint 2 answered: **can we build a real corpus?** Yes — 1,649 functions from 18 Hackage packages, clean IR, zero GHC noise. But it also revealed a deeper problem: the model generates structurally valid programs that don't compile because it learned Hackage-specific function names instead of algorithmic patterns. 0/22 benchmark tasks pass at any k. The model memorized the library, not the calculus.

Sprint 3 answers: **can we make the model learn structural patterns by removing external names entirely?**

### Why This Comes Next

Sprint 2's results show a precise failure mode:

- **Clean IR works** — zero GHC noise in 1,649 functions, vocabulary is structural
- **Training works** — val loss 1.79 nats, the model learns _something_
- **Generation fails** — the model produces Hackage-style code (calling `toInteger`, `castPtr`, `null`) for simple tasks like identity, double, sum
- **0/22 benchmark pass** — all candidates compile-fail because they reference functions not in scope
- **RL is blocked** — zero passing candidates means zero signal for rejection sampling

The root cause is architectural, not data-scale:

- **10.6% of encoded tokens are `<unk>`** — external function names below the vocabulary frequency cutoff
- **Function names are an open vocabulary** — each codebase invents its own. Can't build stable embeddings for words that mean different things in every context
- **The model splits capacity between learning the calculus and memorizing the library** — only the first generalizes
- **With `<unk>` masked at inference, the model picks arbitrary Hackage names** — these compile-fail in the benchmark's simple test module

The lessons learned doc identified the fix: **lambda-lift external references** so every function body is a closed term with only bound variables. No open vocabulary, no library memorization.

### The Core Idea: Dependency Injection

Three kinds of `Var` references exist in our IR:

| Kind                   | Example                            | Current handling               | Problem                          |
| ---------------------- | ---------------------------------- | ------------------------------ | -------------------------------- |
| Locally bound          | `Var v0`                           | Alpha-renamed, always in vocab | None                             |
| Recursive self-call    | `Var fSum` inside `DEF fSum`       | Treated as external name       | Conflated with open vocab        |
| External function call | `Var fMap` inside `DEF fDoubleAll` | Maps to `<unk>` or rare token  | Open vocabulary, unregularizable |

**The transformation:** Convert external references into lambda parameters and self-calls into a `SELF` token.

Before:

```
DEF fDoubleAll (Lam v0 (App Var fMap (Lam v1 (App Prim * Var v1 Lit 2)) Var v0)) EOF
```

After:

```
DEF fDoubleAll (Lam v0 v1 (App Var v0 (Lam v2 (App Prim * Var v2 Lit 2)) Var v1)) EOF
-- Metadata: {deps: [{param: 0, func: "fMap"}]}
```

Recursive example — before:

```
DEF fSum (Lam v0 (Case Var v0 (Alt (PCon []) Lit 0) (Alt (PCon : v1 v2) (App Prim + Var v1 (App Var fSum Var v2))))) EOF
```

After:

```
DEF fSum (Lam v0 (Case Var v0 (Alt (PCon []) Lit 0) (Alt (PCon : v1 v2) (App Prim + Var v1 (App Var SELF Var v2))))) EOF
```

**Why this helps:**

1. **Closed terms** — no free variables, no external names in the token stream
2. **Structural vocabulary** — only `Lam`, `App`, `Case`, `Var`, `Lit`, `Con`, `Prim`, `SELF`, `v0`-`vN`, operators, constructors
3. **`<unk>` drops to ~0%** — every token in the vocabulary is structural and frequent
4. **The model learns algorithms** — fold becomes a single structural skeleton parameterized by a combining operation, not N different patterns with N different function names
5. **Generalizes across codebases** — the same higher-order patterns appear in every functional program regardless of naming conventions

### One Focus Area

#### Lambda Lifting in normalize.py

**The transformation**, implemented during alpha-renaming (which already tracks scope):

1. **Identify free variables**: Walk the AST, find all `Var X` where X is not bound by an enclosing `Lam`, `Let`, `Case`, or `Alt`
2. **Classify free variables**:
   - If X == the function's own name → replace with `Var SELF`
   - Otherwise → external dependency, will be lambda-lifted
3. **Lambda-lift externals**: Add external names as outermost lambda parameters, assign them fresh variable names (`v0`, `v1`, ...), shift all existing binders up
4. **Store metadata**: Record the mapping `{deps: [{param: 0, func: "fMap"}, {param: 1, func: "fFilter"}]}`
5. **Alpha-rename**: The standard alpha-renaming pass then normalizes everything

**Scope tracking** — the alpha-renamer already builds a scope map. Free variable detection is: "any `Var X` where X is not in the current scope and X is not a constructor, literal, or primitive."

**Edge cases:**

| Case                                               | Handling                                         |
| -------------------------------------------------- | ------------------------------------------------ |
| Function with no external calls                    | No change (already closed)                       |
| Multiple calls to same external                    | Single lambda parameter, all references updated  |
| Mutual recursion (`fEven`/`fOdd`)                  | Both become lambda parameters (not `SELF`)       |
| External function used as argument, not called     | Still lambda-lifted (it's still a free variable) |
| Nested functions in `Let` with their own externals | Lift to outermost level (flatten)                |

### Pipeline Changes

#### normalize.py (UPDATED)

- `alpha_rename()` extended with free-variable detection and lambda lifting
- New `SELF` token for recursive self-calls
- External references become bound parameters with metadata
- Output: each sequence includes `deps` list mapping parameter positions to function names

#### augment.py (MINOR UPDATE)

- Propagate `deps` metadata through augmented variants
- `SELF` token preserved during re-alpha-rename (it's structural, not a variable name)

#### tokenizer.py (REBUILD)

- `SELF` added to special tokens or frequent enough to survive cutoff
- Vocabulary should shrink significantly (hundreds of function names eliminated)
- Expected: `<unk>` rate drops from 10.6% to near 0%

#### checker.py (UPDATED)

- `SELF` added to valid `Var` names
- `is_type_valid` updated for `SELF` in structural positions
- Constrained decoder already masks `<unk>` — with near-zero `<unk>` rate, this matters less

#### pretty_print.py (UPDATED)

- `SELF` → recursive self-reference (emit the function's own name)
- Lambda-lifted parameters → the benchmark harness supplies actual implementations
- Needs to reconstruct: `f dep0 dep1 ... args = body` where dep0/dep1 are partial-applied

#### benchmark/run.py (UPDATED)

- When evaluating a generated function, substitute lambda-lifted parameters with actual corpus function implementations
- Use `deps` metadata from training data for training-set tasks
- For novel tasks: the task definition specifies which functions to inject
- Build Haskell source: generated function receives corpus helpers as arguments in the test expression

#### rl/evaluate.py + rejection_sample.py (UPDATED)

- Same substitution logic as benchmark: inject corpus functions for lambda-lifted parameters
- The registry is already passed — now it's used to _supply implementations_, not just _include definitions_

### Evaluation Strategy

The DI transformation changes how we evaluate generated programs. Two modes:

**Mode A — Known dependencies (training-set tasks):**
The task prompt includes the function name. The corpus has metadata mapping parameter positions to function names. The harness substitutes directly.

```haskell
-- Generated: f v0 v1 = v0 (\x -> x * 2) v1    (v0 = fMap)
-- Test: main = print (f map [1,2,3])
```

**Mode B — Unknown dependencies (novel tasks):**
The generated function has lambda-lifted parameters but we don't know which corpus functions to inject. Options:

1. **Task specifies dependencies**: `"deps": ["fMap", "fDouble"]` in tasks.json
2. **Brute-force search**: Try all corpus functions of matching arity for each parameter (combinatorial but bounded by type checking)
3. **Greedy matching**: Use the type checker to narrow candidates, try top-k

Mode A is sufficient for the initial experiment. Mode B is needed for full generality but can wait.

### Deliverables

```
sprint/
├── corpus/
│   ├── normalize.py              # UPDATED: lambda lifting + SELF token + deps metadata
│   ├── augment.py                # UPDATED: propagate deps metadata
│   ├── tokenizer.py              # REBUILT: vocabulary without function names
│   ├── sequences.json            # closed-term sequences with deps metadata
│   ├── sequences_aug.json        # augmented closed-term sequences
│   └── tokenizer.json            # smaller, cleaner vocabulary
├── typecheck/
│   ├── checker.py                # UPDATED: SELF token support
│   └── constrained_decode.py     # MINOR: SELF in valid tokens
├── benchmark/
│   ├── tasks.json                # UPDATED: dep specifications for novel tasks
│   ├── run.py                    # UPDATED: dependency substitution in evaluation
│   └── pretty_print.py           # UPDATED: SELF rendering, dep parameter handling
├── model/
│   ├── train.py                  # UNCHANGED (or minor: log vocab composition)
│   ├── generate.py               # MINOR: update default prompts
│   └── checkpoint.pt             # new checkpoint on closed-term corpus
└── rl/
    ├── evaluate.py               # UPDATED: dependency substitution
    └── rejection_sample.py       # UPDATED: dependency substitution
```

### Success Criteria

| Question                                                     | Minimum bar                                                  | Stretch                                                   |
| ------------------------------------------------------------ | ------------------------------------------------------------ | --------------------------------------------------------- |
| Does DI eliminate `<unk>` from training?                     | `<unk>` rate < 1% (down from 10.6%)                          | 0% `<unk>` in structural positions                        |
| Does vocabulary shrink?                                      | < 200 tokens (down from 597)                                 | < 100 tokens (pure structural vocabulary)                 |
| Does the model generate compilable programs?                 | >= 50% of candidates compile (up from 0%)                    | >= 90% compile                                            |
| Does DI improve benchmark pre-RL?                            | >= 3/22 tasks pass at pass@50 (up from 0/22)                 | >= 10/22 pre-RL                                           |
| Does DI unblock RL?                                          | RL produces winners for >= 5 tasks                           | RL produces winners for >= 15 tasks                       |
| Overall benchmark pass rate after RL                         | >= 12/22 at pass@50                                          | >= 18/22                                                  |
| Does the model learn fold/map/filter as structural patterns? | Generated folds share common skeleton regardless of combiner | Model generalizes to novel combiners not seen in training |
| Val loss                                                     | < 1.5 nats                                                   | < 1.0 nats                                                |

### What This Validates

Sprint 3 tests a specific hypothesis: **the model's failure to generate correct programs is caused by the open vocabulary of function names, not by insufficient data or model capacity.**

If DI works:

- The closed-term representation is the right foundation for the IR
- The model can learn algorithmic patterns (fold, map, filter) as structural skeletons
- Scaling data (more Hackage packages) will improve pattern coverage without introducing vocabulary problems
- The path to cross-language generalization is clear: other languages compile to the same closed-term IR

If DI doesn't work:

- The bottleneck is elsewhere (model architecture, training procedure, task formulation)
- The model may need explicit algorithmic supervision (not just next-token prediction on examples)
- Consider: tree-positional encodings, graph neural networks, or explicit type-guided generation

### What This Doesn't Change

- **Same corpus**: 1,649 base functions from Hackage + handwritten (no new extraction)
- **Same model architecture**: 4 layers, d_model=256, nhead=8, 3.3M params
- **Same training procedure**: cosine LR with warmup, early stopping, package-based split
- **Same benchmark**: 22 tasks (10 original + 12 novel)
- **Same RL**: rejection sampling with parallel runhaskell

The only variable is the representation. If benchmark results improve, it's because the model learned better from the same data in a better format.

### Risks

| Risk                                                                     | Likelihood | Mitigation                                                                                                |
| ------------------------------------------------------------------------ | ---------- | --------------------------------------------------------------------------------------------------------- |
| Lambda lifting increases sequence length (more Lam parameters)           | Medium     | Monitor avg token length. If > 2x increase, consider inlining small functions instead of lifting          |
| Some functions have many external deps (5+), making them very high-arity | Low        | Cap at 3-4 lifted parameters. Functions with more deps are probably too complex for the model anyway      |
| Benchmark evaluation becomes complex (dependency substitution)           | Medium     | Start with Mode A (known deps). Mode B is optional stretch goal                                           |
| `SELF` in non-recursive functions (no self-call)                         | None       | Simply doesn't appear. No special handling needed                                                         |
| Mutual recursion breaks `SELF` semantics                                 | Low        | Treat mutually-recursive functions as external deps of each other. Only direct self-recursion uses `SELF` |

### Timeline

| Task                                                                  | Estimate       |
| --------------------------------------------------------------------- | -------------- |
| Lambda lifting in `normalize.py` (free var detection, SELF, metadata) | 1-2 days       |
| Update `augment.py` (propagate deps)                                  | 0.5 day        |
| Rebuild tokenizer, verify vocabulary                                  | 0.5 day        |
| Update `checker.py` + `constrained_decode.py` (SELF support)          | 0.5 day        |
| Update `pretty_print.py` (SELF rendering, dep params)                 | 1 day          |
| Update `benchmark/run.py` (dependency substitution)                   | 1 day          |
| Update `rl/evaluate.py` + `rejection_sample.py`                       | 0.5 day        |
| Training + benchmark + RL experiments                                 | 2-3 days       |
| Analysis + status writeup                                             | 0.5 day        |
| **Total**                                                             | **~1-2 weeks** |

### Key Constraints

- Same infrastructure: laptop + 1 GPU, Python + PyTorch
- No new dependencies
- Still disposable code — production quality is Stage 0+
- Training should complete in < 30 minutes (same corpus, same GPU, early stops at epoch ~10)
- Sprint 3 modifies sprint 2 code in `sprint/` (sprint 2 results recorded in docs/)

### Relationship to Roadmap

The DI transformation is not just a sprint 3 fix — it's an architectural insight that carries forward:

| Concept                       | Sprint 3                                     | Production (Stage 0+)                     |
| ----------------------------- | -------------------------------------------- | ----------------------------------------- |
| Closed terms                  | Lambda lifting in normalize.py               | Built into the IR definition              |
| SELF token                    | Special token in vocabulary                  | First-class recursion construct in IR     |
| Dependency metadata           | JSON sidecar in sequences                    | Part of the AST schema                    |
| Dependency injection at eval  | Harness substitutes functions                | Runtime linker / module system            |
| Cross-language generalization | Same closed-term format regardless of source | Universal IR with per-language extractors |

If sprint 3 validates that closed terms help the model learn, the production IR should be defined as a closed lambda calculus from the start. External references are always explicit parameters, never implicit free variables.
