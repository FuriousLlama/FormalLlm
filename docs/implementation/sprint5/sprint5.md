# Sprint 5: Type-Directed Conditioning

The sixth iteration of [[ast_pipeline_roadmap.md]].
Core objective: replace the broken name-based conditioning with type-signature conditioning and structured context training, so the model knows _which_ function to generate.

## Sprint 5: Can the Model Navigate by Type?

**Duration: ~2-3 weeks. Team: 1 person. Compute: 1 GPU + laptop.**

Sprint 4 answered: **can we distinguish memorization from generalization?** Yes — the diagnostic was precise. Val/train ratio dropped to 0.79x, val loss to 0.320 nats, vocabulary shrank to 120 tokens. The model learned the structural grammar of de Bruijn IR fluently. But 0/38 benchmark tasks pass because three changes interacted catastrophically:

1. The 120-token vocabulary erases all function names to `<unk>` (below min_freq=25)
2. Random few-shot concatenation taught the model to ignore context
3. The benchmark requires name→body mapping, which no longer exists

Sprint 5 answers: **can type signatures replace names as the conditioning signal, and can structured context training teach the model to use its context?**

### Why This Comes Next

Sprint 4's failure is not a failure of the de Bruijn representation or the data pipeline — both work. It is a failure of the **conditioning interface**: the model has no way to know which function to generate.

The theoretical framework (`navigating_formal_space.md`) already identifies the answer: **type signatures are coordinates in formal space**. A type signature like `[a] -> Int` constrains the search to functions that consume lists and produce integers — a tiny subspace. Combined with structured context (showing related functions of similar type), the model receives two orthogonal signals:

1. **Type signature**: _what kind of function_ (narrows the structural class)
2. **Structured context**: _which specific function_ within that class (disambiguates via examples)

This is Option D from the Sprint 4 post-mortem analysis: types as primary specifier, structured context as disambiguation.

### The Root Cause in Detail

Sprint 3's 22/22 benchmark pass worked because function names were in the vocabulary. The model learned a direct lookup: `DEF fId → (Lam 1 Var 0)`. This was dismissed as memorization, but it was actually the benchmark's intended mechanism: the name is the query, the body is the answer.

Sprint 4 removed names from the vocabulary (they fall below min_freq=25) without providing an alternative query mechanism. The model sees `DEF <unk>` for every function and can only generate from its unconditional structural prior.

The fix is not to restore names (that re-enables memorization without addressing generalization) but to provide a _richer, more generalizable_ conditioning signal: the type signature.

### Why Type Signatures

Every function in the corpus already has a GHC-extracted type signature. The data is there — it flows through `parse_ghc_core.py` → `clean.py` → `corpus_hackage.json` but is dropped at `normalize.py` → `sequences.json`. Sprint 5 threads it through.

Type signatures are superior to names as conditioning signals because:

1. **They constrain the output space.** `Int -> Int` admits `identity`, `double`, `negate`, `successor`, `square`. `[a] -> [a]` admits `reverse`, `tail`, `sort`, `init`. The model's task becomes: generate a well-formed body _of this type_ — not guess which of 4,438 functions was meant.

2. **They generalize.** A model conditioned on `[a] -> Int` at training time can generate bodies for novel functions with the same type at test time. Names don't generalize — `fFoo` carries zero structural information.

3. **They compose.** If the model learns to generate `a -> b` functions and `b -> c` functions, it has the building blocks for `a -> c` via composition. Types make composition rules explicit.

4. **They exist for 100% of the corpus.** All 7,031 hackage functions and all 162 handwritten functions have GHC-extracted type signatures. No new extraction work is needed.

### Why Structured Context

Type signatures alone are ambiguous — many functions share the same type (`Int -> Int` covers identity, double, negate, successor, ...). Structured context disambiguates:

- Show 2-3 functions **of the same or related type** as context
- The model learns: "given these `Int -> Int` functions as context, the next one should be different from them"
- At test time, carefully chosen context examples steer generation toward the specific target

The key difference from Sprint 4's random concatenation: **context examples are grouped by type similarity**, so there is a learnable relationship between context and target. The model's loss is reduced by attending to context (because context predicts the structural class of the continuation), unlike random grouping where ignoring context is optimal.

### The Two Pillars

#### Pillar 1 — Type-Signature Conditioning

Thread type signatures through the full pipeline: extraction → normalization → serialization → tokenization → training → generation → benchmark.

**Serialization format:**

Before (Sprint 4):

```
DEF <unk> (Lam 1 Var 0) EOF
```

After (Sprint 5):

```
DEF TY a -> a BODY (Lam 1 Var 0) EOF
```

The `TY ... BODY` frame gives the model an explicit, machine-readable type signature between the definition marker and the body. `TY` and `BODY` are new structural tokens. The function name is dropped entirely — it was `<unk>` anyway.

**Type signature normalization:**

GHC Core type signatures are verbose (`forall {a}. a -> a`, `forall a. Eq a => [a] -> [a] -> [a]`). They need normalization to reduce vocabulary pressure:

1. Strip `forall` quantifiers (the binder variables are implicit from usage)
2. Strip class constraints (`Eq a =>`, `Ord a =>`) — the model operates on the structural type, not the class context
3. Canonicalize type variable names: `a`, `b`, `c`, ... in order of first appearance
4. Normalize whitespace and parenthesization
5. Tokenize type constructors and arrows as individual tokens

Example transformations:

```
forall {a}. a -> a                          →  a -> a
forall a. Eq a => [a] -> [a] -> [a]        →  [a] -> [a] -> [a]
forall a b. (a -> b) -> [a] -> [b]         →  (a -> b) -> [a] -> [b]
Int -> Int -> Int                           →  Int -> Int -> Int
forall (m :: * -> *) a. Monad m => m a -> m a  →  m a -> m a
```

**Vocabulary impact:**

New tokens needed for type signatures:

- `TY`, `BODY` — frame markers (2 tokens)
- `->` — function arrow (1 token)
- `[`, `]` — list type syntax (2 tokens, if not already present)
- `(`, `)` — already present as `(`/`)` but may need separate type-level parens
- Type variables: `a`, `b`, `c`, `d` (4 tokens, covering 99%+ of cases)
- Common type constructors: `Int`, `Bool`, `Char`, `Maybe`, `Either`, `IO` (frequent in corpus)
- Rare constructors: fall to `<unk>` (acceptable — structural type shape is preserved)

Estimated vocabulary: 120 (current) + ~20-30 type tokens = **140-150 tokens**. Well within the <200 target.

**Type coverage analysis (from current corpus):**

| Metric                            | Value                                     |
| --------------------------------- | ----------------------------------------- |
| Functions with type info          | 7,031/7,031 hackage + 162/162 handwritten |
| Unique simplified types           | ~3,078                                    |
| Top type (`Word8`)                | 25 occurrences                            |
| Functions sharing a type (median) | ~1 (most types are unique)                |
| Functions sharing a type (mean)   | ~1.4                                      |

Most types are unique after simplification, meaning type alone is often sufficient to identify a function. For the ~30% of cases where multiple functions share a type, structured context disambiguates.

#### Pillar 2 — Structured Context Training

Replace random sequence concatenation with type-grouped context training.

**Grouping strategy:**

1. Group corpus functions by simplified type signature
2. For type groups with 2+ members: context examples come from the same type group
3. For singleton types: context examples come from the nearest type (same return type, or same argument count, or same structural shape)
4. Within a group, the target function is excluded from the context

**Training format:**

```
BOS TY [a] -> Int BODY (Lam 1 (Case Var 0 ...length body...)) EOF TY [a] -> Int BODY (Lam 1 (Case Var 0 ...sum body...)) EOF TY [a] -> Int BODY (Lam 1 (Case Var 0 ...product body...)) EOF EOS
```

Each sequence in the few-shot window is a complete `TY...BODY...EOF` block. The model sees that all examples share the same type, and that each has a distinct body. The training signal is: given functions of type `[a] -> Int`, predict the next function of that type.

**Why this teaches ICL:**

With random grouping, context provides no information about the target — the loss-minimizing strategy is to ignore context. With type grouping:

- Context examples constrain the structural class (all are `[a] -> Int` functions)
- Context examples constrain what _not_ to generate (the context bodies are already taken)
- The model's loss is reduced by attending to context (the conditional distribution `P(body | type, context)` is tighter than `P(body | type)`)

This creates a genuine gradient signal for the attention mechanism to learn context-dependence.

**Similarity metric for grouping:**

Define type similarity between two simplified type signatures:

1. **Exact match** (score 1.0): same simplified type string
2. **Same shape** (score 0.7): same number of arrows, same structural skeleton with different concrete types (e.g., `Int -> Int` ≈ `Bool -> Bool`)
3. **Same arity** (score 0.3): same number of arguments but different return types
4. **Fallback** (score 0.0): unrelated types — never grouped together

For training, context examples are drawn from the highest-similarity group available. When the exact-match group has fewer than `few_shot_k` members, pad with same-shape matches.

### Pipeline Changes

#### corpus/normalize.py (UPDATED)

- `to_sequence()` now includes type signature in the token stream
- New function `normalize_type(type_str) -> list[str]`: strips forall/constraints, canonicalizes type variables, returns token list
- Output format: `["DEF", "TY", ...type_tokens..., "BODY", ...body_tokens..., "EOF"]`
- `type` field preserved in sequence metadata for grouping

#### corpus/augment.py (UPDATED)

- Commutative swap augmentation unchanged (still valid)
- Type tokens in the `TY...BODY` prefix are preserved during augmentation (only body tokens after `BODY` are swapped)

#### corpus/tokenizer.py (UPDATED)

- `ALWAYS_KEEP` extended with: `TY`, `BODY`, `->`, type variables `a`-`d`, common type constructors
- `min_freq` threshold may need lowering for type constructor tokens (or add them to `ALWAYS_KEEP`)
- Target vocabulary: 140-150 tokens

#### model/train.py (UPDATED — structured context)

- `ASTDataset.__init__()`: builds type-similarity index from sequence metadata
- `ASTDataset.__getitem__()`: replaces random context selection with type-grouped selection
  - With probability `few_shot_prob`, prepend 1..`few_shot_k` context sequences from the same type group
  - Fallback to same-shape, then same-arity if the exact-match group is too small
- New `--grouping {type,random,none}` flag (default: `type`)
  - `type`: structured type-based grouping (Sprint 5 default)
  - `random`: Sprint 4 behavior (retained for comparison)
  - `none`: single-sequence training (Sprint 4 run 1 behavior)

#### model/generate.py (UPDATED)

- Prompt construction uses type signature instead of function name
- `encode_prompt()` accepts type string, produces `[BOS, TY, ...type_tokens..., BODY]`

#### benchmark/tasks.json (UPDATED)

- Each task's `prompt` field changes from `"DEF fDouble"` to `"TY Int -> Int BODY"`
- Existing `type_sig` field is already present in all 38 tasks — use it to generate the prompt
- `few_shot_funcs` updated: instead of naming specific corpus functions, reference functions by type group
- New field `context_type`: the simplified type used to select few-shot examples from the corpus

#### benchmark/run.py (UPDATED)

- `build_few_shot_prompt()` rewritten: selects context examples from the corpus by type similarity to the task's `context_type`
- `_clean_tokens()` updated: no more `<unk>` substitution (names are gone), handle `TY...BODY` frame
- `run_task()` prompt construction uses type signature
- Pretty-printing updated: type-conditioned output

#### benchmark/pretty_print.py (UPDATED)

- Handle `TY...BODY` frame: strip type tokens before de Bruijn → Haskell conversion
- Or: use type tokens to generate a Haskell type declaration in the output

#### typecheck/checker.py (UPDATED)

- Type tokens added to valid token sets
- `TY...BODY` frame recognized in the grammar

#### typecheck/constrained_decode.py (UPDATED)

- After `DEF`, expect `TY` token
- After `TY`, allow type tokens until `BODY`
- After `BODY`, existing de Bruijn body grammar applies

#### rl/rejection_sample.py + evaluate.py (UPDATED)

- Prompt construction uses type signatures
- Winner collection parses `TY...BODY...EOF` format
- Tiered metrics retained

### Deliverables

```
sprint/
├── extract/
│   ├── (unchanged — type info already flows through)
│   └── corpus_hackage.json         # already has "type" field
├── corpus/
│   ├── normalize.py                # UPDATED: normalize_type(), TY...BODY serialization
│   ├── augment.py                  # UPDATED: preserve TY prefix during augmentation
│   ├── tokenizer.py                # UPDATED: type tokens in ALWAYS_KEEP, ~140-150 vocab
│   ├── sequences.json              # UPDATED: includes type tokens + "type" metadata
│   ├── sequences_aug.json          # UPDATED
│   └── tokenizer.json              # UPDATED: expanded vocabulary
├── model/
│   ├── train.py                    # UPDATED: type-grouped ASTDataset, --grouping flag
│   ├── generate.py                 # UPDATED: type-conditioned prompt
│   ├── checkpoint.pt               # NEW: type-conditioned model
│   └── checkpoint_s4.pt            # RENAMED: Sprint 4 checkpoint (comparison)
├── typecheck/
│   ├── checker.py                  # UPDATED: TY...BODY grammar
│   └── constrained_decode.py       # UPDATED: type token handling
├── benchmark/
│   ├── tasks.json                  # UPDATED: type-based prompts, context_type field
│   ├── run.py                      # UPDATED: type-conditioned few-shot, prompt construction
│   └── pretty_print.py             # UPDATED: TY...BODY frame handling
└── rl/
    ├── evaluate.py                 # UPDATED: type-conditioned eval
    └── rejection_sample.py         # UPDATED: type-conditioned generation + parsing
```

### Success Criteria

| Question                        | Minimum bar                                  | Stretch                              |
| ------------------------------- | -------------------------------------------- | ------------------------------------ |
| Type signatures in vocabulary?  | All type tokens encode (no `<unk>` for type) | —                                    |
| Vocabulary size?                | < 200 tokens                                 | < 160 tokens                         |
| Val loss (type-conditioned)?    | < 1.0 nats                                   | < 0.5 nats                           |
| Val/train ratio?                | < 2.0x                                       | < 1.5x                               |
| Tier 1 tasks (pass@1 greedy)?   | >= 12/22                                     | >= 18/22                             |
| Tier 1 tasks (pass@10)?         | >= 18/22                                     | >= 22/22                             |
| Tier 2 compositional (pass@10)? | >= 3/10                                      | >= 6/10                              |
| Tier 3 structural (pass@10)?    | >= 2/6                                       | >= 4/6                               |
| RL improves Tier 2+3?           | >= 3 additional tasks                        | >= 6 additional tasks                |
| Structured context helps?       | Type-grouped > random by >= 5 tasks          | Type-grouped > random by >= 10 tasks |

**Key comparison runs:**

| Run   | Conditioning           | Context                   | Purpose                |
| ----- | ---------------------- | ------------------------- | ---------------------- |
| Run A | Type signature         | Structured (type-grouped) | Sprint 5 primary       |
| Run B | Type signature         | Random                    | Isolate context effect |
| Run C | Type signature         | None (single sequence)    | Isolate type effect    |
| Run D | None (Sprint 4 format) | Random                    | Sprint 4 baseline      |

If Run A > Run B > Run C >> Run D, both pillars contribute independently and combine superadditively.

### What This Validates

Sprint 5 tests two hypotheses:

**H1 — Type signatures are effective conditioning signals.** If Run C (type-conditioned, no context) outperforms Run D (Sprint 4 baseline) on Tier 1 tasks, then type signatures alone provide enough information for the model to generate target-specific bodies.

Theoretical basis: types are coordinates in formal space. Functions with type `a -> a` form a specific subspace. The model should learn the structural patterns of each type class and generate accordingly.

**H2 — Structured context teaches genuine ICL.** If Run A (type + structured context) outperforms Run C (type only) on Tier 2/3 tasks, then the model has learned to use context examples to disambiguate within a type class.

Theoretical basis: within the `Int -> Int` subspace, showing `double` and `successor` as context should steer the model toward generating `negate` or `square` — the remaining members of the class.

If both hypotheses hold:

- The theoretical framework is validated: types + context = effective navigation of formal space
- The path forward is clear: richer types (refinement types, dependent types) tighten the search further
- RL becomes meaningful: the model has real room to improve on Tier 2/3 tasks

If H1 holds but H2 fails:

- Type conditioning works but ICL is still broken
- Next step: explicit cross-attention between context and generation (architectural change)
- Or: curriculum learning — start with close type matches, gradually increase diversity

If both fail:

- The representation is not the bottleneck — the architecture is
- Consider: tree-structured generation (predict AST nodes, not tokens), explicit type-directed search (beam search with type checking at each step), or hybrid symbolic-neural approaches
- Re-evaluate whether autoregressive sequence generation is the right paradigm for typed IR

### What This Doesn't Change

- **Same extraction pipeline**: GHC Core → parse → clean → IR (type field already extracted)
- **Same de Bruijn normalization**: canonical representation retained
- **Same lambda lifting**: dependency injection, `SELF` token, closed terms
- **Same model architecture**: CausalTransformer with ~450K params (2L/d128)
- **Same RL algorithm**: rejection sampling with parallel runhaskell
- **Same benchmark harness**: dependency substitution, un-lifting, test expression building
- **Same 38 benchmark tasks**: Tier 1/2/3 structure retained

The variables are: conditioning signal (type signature vs name), context training strategy (structured vs random), and prompt format (`TY...BODY` vs `DEF <unk>`). Everything else is held constant.

### Implementation Order

The changes have natural dependencies. Implement in this order:

**Phase 1 — Type normalization + serialization (corpus/)**

1. Write `normalize_type()` in `normalize.py`
2. Update `to_sequence()` to emit `TY...BODY` format
3. Propagate `type` field through to sequence metadata
4. Update `ALWAYS_KEEP` in `tokenizer.py` with type tokens
5. Rebuild tokenizer on updated corpus
6. Verify: all type tokens encode cleanly, vocab < 200

**Phase 2 — Structured context training (model/)**

7. Build type-similarity index in `ASTDataset`
8. Replace random context selection with type-grouped selection
9. Add `--grouping` flag
10. Training runs: Run A (type + structured), Run C (type + none) at minimum

**Phase 3 — Benchmark + evaluation (benchmark/, rl/)**

11. Update `tasks.json` prompts to `TY...BODY` format
12. Update `run.py` for type-conditioned few-shot prompt construction
13. Update `pretty_print.py` for `TY...BODY` frame
14. Update `constrained_decode.py` for type tokens
15. Run benchmark on all comparison runs (A, B, C, D)
16. Update `rejection_sample.py` and `evaluate.py` for type conditioning
17. RL run on best checkpoint

### Risks

| Risk                                                                   | Likelihood | Mitigation                                                                                                                                                                                                                                               |
| ---------------------------------------------------------------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Type signatures too diverse (3,078 unique types → most are singletons) | Medium     | Type normalization collapses many into shared classes. Similarity grouping handles singletons by matching on shape/arity. If singleton types dominate, the "structured context" degrades gracefully to "random context within a broad class"             |
| Type tokens inflate sequences beyond max_len=256                       | Low        | Simplified type signatures are short (median ~5 tokens: `a -> a`, `[a] -> Int`). Even complex types like `(a -> b) -> [a] -> [b]` add only ~10 tokens. Budget is ample                                                                                   |
| Model ignores type tokens (learned to skip to body)                    | Medium     | Unlike names, type tokens have structural information that correlates with body structure. The gradient signal exists: different types produce different body distributions. If the model still ignores them, increase `TY...BODY` prefix weight in loss |
| Type-grouped context is too easy (all examples look alike)             | Low        | This is actually desirable for learning ICL — when context is informative, the model learns to attend. Diversity within the type group comes from different body structures                                                                              |
| Simplified types lose too much information (stripping constraints)     | Low        | The structural type (argument/return shape) captures the navigation-relevant information. Class constraints (`Eq a`, `Ord a`) are runtime dispatch, not structural. If needed, add constraint tokens later                                               |
| Benchmark tasks need type signatures that aren't in training           | Low        | Novel tasks (Tier 2/3) have type signatures composed from types present in training. `[Bool] -> Int` is novel as a benchmark task but `[a] -> Int` appears 9 times in training                                                                           |
| max_len=256 too short for type-conditioned few-shot                    | Medium     | Type prefix adds ~5-10 tokens per sequence. With 2 context examples + 1 target, that's ~15-30 extra tokens. If needed, increase max_len to 320. Training time scales linearly                                                                            |

### Timeline

| Task                                                        | Estimate     |
| ----------------------------------------------------------- | ------------ |
| Write `normalize_type()`, update `to_sequence()`            | 1 day        |
| Update `tokenizer.py` (ALWAYS_KEEP, rebuild vocab)          | 0.5 day      |
| Rebuild corpus with type-conditioned sequences              | 0.5 day      |
| Type-similarity index + structured grouping in `ASTDataset` | 1-2 days     |
| Training runs (A, B, C, D comparisons)                      | 1-2 days     |
| Update `tasks.json` prompts + `run.py`                      | 1 day        |
| Update `constrained_decode.py` + `checker.py`               | 0.5 day      |
| Update `pretty_print.py`                                    | 0.5 day      |
| Benchmark all comparison runs                               | 0.5 day      |
| Update `rejection_sample.py` + `evaluate.py`                | 0.5 day      |
| RL run on best checkpoint                                   | 0.5 day      |
| Analysis + status writeup                                   | 0.5 day      |
| **Total**                                                   | **~2 weeks** |

### Running the Pipeline

```bash
make help              # list all targets
make all               # full pipeline: extract → corpus → train
make corpus            # normalize (type-conditioned de Bruijn) + augment + tokenize
make train             # train with type-grouped context (Sprint 5 default)
make train GROUPING=random    # comparison: type-conditioned, random context
make train GROUPING=none      # comparison: type-conditioned, no context
make generate PROMPT="TY Int -> Int BODY"   # type-conditioned greedy decode
make benchmark         # run tiered benchmark with type-conditioned prompts
make rl                # multi-round rejection sampling
```

### Key Constraints

- Same infrastructure: laptop + 1 GPU, Python + PyTorch
- No new dependencies
- Still disposable code — production quality is Stage 0+
- Training should complete in < 2 hours per run (~450K params, same data scale)
- Sprint 5 modifies Sprint 4 code in `sprint/` (Sprint 4 results recorded in docs/)
- No re-extraction needed — type info already exists in corpus_hackage.json and corpus_raw.json

### Relationship to Theory

| Theoretical concept       | Sprint 5 implementation                           | Production (Stage 0+)                                           |
| ------------------------- | ------------------------------------------------- | --------------------------------------------------------------- |
| Type as position in space | `TY ... BODY` conditioning prefix                 | Type signature as first-class input to the model                |
| Type-directed search      | Model generates bodies conditioned on type        | Explicit type-checking at each generation step                  |
| Structured context        | Type-grouped few-shot examples                    | Retrieval-augmented generation from typed function library      |
| Navigation step           | Autoregressive token prediction within type class | Tree-structured generation with type hole filling               |
| Static verification       | Type tokens constrain valid outputs               | Type checker prunes generation at each step                     |
| Dynamic verification      | runhaskell test execution                         | Property-based testing + exhaustive checking on bounded domains |

Sprint 5 is the first implementation where the theoretical framework's core claim — **types are the right conditioning signal for navigating formal space** — is directly tested. If type-conditioned generation outperforms name-conditioned generation on novel tasks, the theory is validated empirically for the first time.

### Relationship to Roadmap

If Sprint 5 validates type-directed conditioning:

- **Sprint 6**: Richer types — extract and use class constraints, higher-kinded types. Add refinement types (input-output examples as type-level constraints) to further tighten the search
- **Sprint 7**: Type-directed beam search — instead of autoregressive generation, use beam search with type checking at each step (static verification during navigation, not just after)
- **Sprint 8**: Scaling — more data (full Hackage), larger model, harder tasks. The conditioning mechanism should scale with data

If Sprint 5 partially works (types help but ICL is still weak):

- Architectural changes: cross-attention between context and generation, or retrieval-augmented generation
- Curriculum: start with large type groups (many examples per type), gradually shrink

If Sprint 5 fails (types don't help):

- The bottleneck is deeper than conditioning — possibly the autoregressive paradigm itself
- Consider tree-structured generation, or shift to the full navigator architecture from `navigating_formal_space.md`
