# Learning Computation from Primitives: The AST Approach

## From Tokens to Typed AST Nodes — Applying the LLM Paradigm to Program Space

---

## 1. The Core Analogy

Large language models learn the structure of language by training on its lowest practical unit: the token. Tokens are sub-word fragments carrying no inherent meaning. The model is told nothing about words, grammar, sentences, arguments, or narratives. Every level of linguistic structure — syntax, semantics, pragmatics, rhetoric — emerges from learning statistical regularities in how tokens co-occur across a massive corpus of human-produced text.

This works because the training distribution (human text) is concentrated on structured, purposeful utterances. The statistical regularities in the corpus reflect the structure of language itself. The model recovers that structure because it is the simplest explanation for the patterns in the data.

The question this document addresses: **can the same paradigm be applied to computation?** If we identify the right primitive unit for programs — the analog of the token — and train a model on a large corpus of programs expressed in that unit, will the model discover algorithms, data structures, and design patterns as emergent statistical regularities, the way LLMs discover grammar and meaning?

The answer depends on choosing the right level of primitive. Too low (bits, Boolean gates) and programs are so long that statistical learning has nothing to grab onto. Too high (library functions like `sort` and `map`) and the model inherits human assumptions about what the right abstractions are rather than discovering them. There is a sweet spot: the **typed core calculus** — the small set of operations that every programming language reduces to after syntactic sugar is removed.

---

## 2. Choosing the Right Level

### 2.1 Why Not Lower

**Bits and Boolean gates (AND, OR, NOT, XOR).** Everything computable reduces to Boolean logic over bits. But the distance between bit operations and useful programs is vast. A 32-bit integer addition is ~150 gates. A sort function is tens of thousands. Programs at this level are so long and so non-local — understanding any single gate requires understanding the entire circuit — that statistical regularities are buried under enormous sequence lengths. Learning from bits is like learning English from raw audio waveforms: all the information is there, but the representation is hostile to pattern recognition.

**Machine instructions (assembly / ISA).** MOV, ADD, CMP, JMP, LOAD, STORE — roughly 100-1000 instructions depending on the architecture. AlphaDev (DeepMind, 2023) demonstrated that reinforcement learning at this level can discover novel sorting algorithms that outperform human-designed versions. But AlphaDev produced programs of ~20-50 instructions — sorting networks for 3-5 elements. A useful general-purpose function at this level is thousands of instructions long. The combinatorial space of 1000-instruction programs drawn from a 100-instruction vocabulary is intractable even with learned guidance.

Assembly proves the concept: a model can learn computation from low-level primitives. But it doesn't scale. The unit is real but programs are too long for statistical learning to remain tractable.

**Turing machine operations / combinators.** Read, write, move, change state. Or: S, K, I combinators. These are theoretically minimal (2-5 operations, computationally universal) but programs expressed in them are absurdly long and opaque. The SKI combinator expression for integer addition is a wall of nested applications. No statistical regularity survives this level of encoding. It is like trying to learn English from a binary encoding of Unicode code points.

### 2.2 Why Not Higher

**Library-level operations (sort, map, filter, groupBy, join).** Starting from high-level library functions and learning to compose them is tractable — the vocabulary is small, programs are short, and the search space is manageable. But this is analogous to starting from sentences and learning to compose them into paragraphs. It works, but the model inherits human decisions about what the right intermediate abstractions are rather than discovering them.

A model trained to compose `sort`, `filter`, and `map` cannot discover a novel algorithm that doesn't decompose into those operations. It is limited to the expressive power of its prescribed vocabulary. It may compose known building blocks fluently, but it will never discover that a particular pattern of primitive operations constitutes a fundamentally new building block.

The deeper problem: choosing library functions as the unit requires deciding in advance which functions belong in the vocabulary. That decision embeds assumptions about the structure of computation that may be wrong, incomplete, or domain-specific. Letting the model discover which operations are fundamental — rather than telling it — is the more powerful approach.

### 2.3 The Sweet Spot: Typed Core Calculus Operations

Every programming language, when you strip away syntactic sugar, reduces to a small core calculus. The primitive operations are:

| Operation       | What it does                        | Notation            |
| --------------- | ----------------------------------- | ------------------- |
| Application     | Apply a function to an argument     | `f x`               |
| Abstraction     | Create a function                   | `λx. body`          |
| Pattern match   | Branch on the shape of data         | `match x with ...`  |
| Let binding     | Name a sub-computation              | `let x = e in body` |
| Constructor     | Build a data structure              | `Cons(x, xs)`       |
| Recursion       | Define self-referential computation | `fix f. body`       |
| Literal         | A concrete value                    | `42`, `true`        |
| Variable        | Reference a bound name              | `x`                 |
| Type annotation | Explicit type information           | `(e : T)`           |

That is roughly **10-15 distinct node types.** Every program in every typed language can be represented as a tree of these nodes.

This level has the properties that make statistical learning tractable:

**Small vocabulary.** 10-15 node types, comparable to the structural categories of natural language (nouns, verbs, adjectives, prepositions, determiners). Small enough for a model to learn the full grammar of composition.

**Programs are short.** A useful function is tens to hundreds of AST nodes at this level. `sort` is 30-50 nodes. A data transformation pipeline is 100-200 nodes. These lengths are comparable to paragraph-length text — within the range where transformer-based learning is effective.

**Types provide structural scaffolding.** Every node carries a type annotation. Composition is constrained by type compatibility. This is structural information that assembly and bit-level representations entirely lack. It is analogous to having part-of-speech tags on every token — it gives the model explicit compositional constraints at every position.

**Statistical regularities exist and are recoverable.** Certain subtree patterns recur across all programs because they correspond to fundamental computational patterns. A model trained on millions of programs represented at this level will discover these patterns the same way an LLM discovers grammatical constructions:

```
λf. λxs. match xs with
  | []      → []
  | x :: rest → f(x) :: recurse(rest)
```

This subtree pattern is `map`. It appears in thousands of programs. The model does not need to be told that `map` exists. It will discover that this particular arrangement of `Lam/Case/App/Constructor/Var` has high probability and recurs across diverse contexts.

**A training corpus exists.** Every open-source program in a typed language can be desugared to its core calculus. Haskell's Hackage, OCaml's opam, Lean's Mathlib, Rust's crates (via HIR), and TypeScript's DefinitelyTyped all provide programs that can be mechanically reduced to typed core ASTs.

---

## 3. Desugaring: From Source to Core

### 3.1 What Desugaring Is

Desugaring is the process by which a compiler replaces syntactic conveniences (sugar) with combinations of core operations. It is a mechanical, deterministic transformation performed by the compiler's frontend. The result is a representation where every computational step is explicit and built from a minimal set of primitives.

```
Source (Haskell):
    double xs = [x * 2 | x <- xs]

Parsed AST (still sugared):
    FunctionDef "double" [PatVar "xs"]
      (ListComp (BinOp Mul (Var "x") (Lit 2))
                [Generator (PatVar "x") (Var "xs")])

Desugared core:
    Lam (xs : List Int) →
      App (App (Var "map")
               (Lam (x : Int) → App (App (Var "*") (Var "x")) (Lit 2)))
          (Var "xs")
```

The list comprehension — a syntactic convenience — is gone. It has been replaced by `map` applied to a lambda. Every surface-level construct eventually reduces to combinations of `App`, `Lam`, `Case`, `Let`, `Var`, `Lit`, and constructors.

### 3.2 Concrete Core Representations by Language

Each language's compiler already produces a typed core representation. The extraction process hooks into the compiler pipeline at the point where desugaring is complete and types are fully resolved.

**GHC Core (Haskell) — System FC:**

GHC's internal representation has 7 meaningful constructors:

```
Var    — variable reference
Lit    — literal value (integers, strings, characters)
App    — function application (f applied to x)
Lam    — lambda abstraction (λx. body)
Let    — let binding (let x = e in body)
Case   — pattern matching (case e of { alts })
Type   — type argument (for polymorphic functions)
```

Extraction is built into GHC:

```bash
# Text dump of Core for inspection
ghc -ddump-simpl -dsuppress-all MyModule.hs
```

```haskell
-- Programmatic extraction via the GHC API
extractCore :: FilePath -> IO [CoreBind]
extractCore path = runGhc $ do
    setTargets [Target path]
    load LoadAllTargets
    modSummary <- getModSummary moduleName
    desugared  <- desugarModule =<< typecheckModule =<< parseModule modSummary
    return (mg_binds (dm_core_module desugared))
```

Each `CoreBind` is a fully typed AST. Every sub-expression carries its type. The entire Hackage ecosystem (~16,000 packages, millions of functions) can be processed through this pipeline.

**Lean 4 Core — `Expr`:**

Lean's internal term representation has ~12 constructors:

```
bvar    — bound variable (de Bruijn index)
fvar    — free variable
const   — named constant (reference to a definition)
app     — function application
lam     — lambda abstraction
forallE — dependent function type (Π type)
letE    — let binding
lit     — literal value
sort    — universe (Type, Prop)
mvar    — metavariable (unfilled hole)
mdata   — metadata annotation
proj    — structure field projection
```

Extraction uses Lean's metaprogramming facilities:

```lean
open Lean Meta in
def extractExpr (name : Name) : MetaM Expr := do
  let info ← getConstInfo name
  match info with
  | .defnInfo val => return val.value
  | .thmInfo val  => return val.value
  | _             => throwError "not a definition or theorem"
```

Lean uses de Bruijn indices for bound variables, which provides automatic alpha-normalization — structurally identical terms are always represented identically.

**OCaml — Typedtree:**

OCaml's `compiler-libs` package exposes the fully typed AST via the `Typedtree` module. The key constructors are `Texp_apply`, `Texp_function`, `Texp_let`, `Texp_match`, `Texp_construct`, and `Texp_ident`, closely mirroring the core calculus operations.

### 3.3 Normalization

Raw compiler ASTs contain noise that doesn't carry computational meaning. Before training, normalize:

**Alpha-normalization.** Rename all variables systematically so that structurally identical programs have identical representations:

```
Before:  Lam(x, App(Var(x), Lit(1)))
         Lam(y, App(Var(y), Lit(1)))     ← same computation, different names

After:   Lam(v0, App(Var(v0), Lit(1)))
         Lam(v0, App(Var(v0), Lit(1)))   ← now identical
```

Lean's de Bruijn indices achieve this automatically. For GHC Core, convert named variables to de Bruijn indices or apply systematic renaming.

**Remove compiler artifacts.** Strip source location metadata, debugging annotations, profiling instrumentation, and compiler-internal marks. Retain only the computational content and type information.

**Inline trivial bindings.** Replace `let x = y in x` with `y`. Remove identity wrappers, dead bindings, and other detritus that compilers sometimes produce during desugaring.

**Normalize type representations.** Standardize how types are encoded — consistent ordering of type parameters, canonical names for type constructors, uniform representation of polymorphism.

---

## 4. Serialization: Trees to Sequences

### 4.1 The Problem

ASTs are trees. Transformers operate on sequences. A serialization strategy must convert trees to sequences while preserving enough structural information for the model to learn tree relationships.

### 4.2 Depth-First Linearization with Structural Tokens

Traverse the tree depth-first, emitting a token for each node, with explicit markers for subtree boundaries:

```
Tree:
    App : Int
    ├── Lam : Int → Int
    │   ├── Bind v0 : Int
    │   └── App : Int
    │       ├── Var "+" : Int → Int → Int
    │       ├── Var v0  : Int
    │       └── Lit 1   : Int
    └── Lit 5 : Int

Linearized sequence:
    APP:Int  LAM:Int→Int  BIND:v0:Int  APP:Int  VAR:+:Int→Int→Int
    VAR:v0:Int  LIT:1:Int  END  END  LIT:5:Int  END
```

Each token is a triple: `(node_type, value, type_annotation)`. The `END` tokens mark where subtrees close, encoding the tree structure within the linear sequence. This is equivalent to the parenthesization in S-expressions.

### 4.3 The Vocabulary

The token vocabulary for this representation is small and fixed:

| Category          | Tokens                                              | Count      |
| ----------------- | --------------------------------------------------- | ---------- |
| Node types        | APP, LAM, LET, CASE, VAR, LIT, CONSTRUCTOR, PROJ    | ~10        |
| Structural        | END, BIND, BRANCH, TYPE_ARG                         | ~5         |
| Primitive types   | Int, Bool, Nat, String, List, Option, ...           | ~20-30     |
| Type constructors | →, ×, +, ∀                                          | ~5         |
| Primitive values  | integer literals, boolean literals, string literals | ~50-100    |
| Named constants   | references to known definitions                     | open-ended |

The core vocabulary is **200-500 tokens** — roughly two orders of magnitude smaller than the 50,000-100,000 token vocabulary of a text LLM. Named constants (references to library functions and previously defined operations) form an open-ended extension that grows with the catalog of known definitions.

### 4.4 Alternative: Tree-Structured Models

Instead of linearizing, operate directly on the tree structure using tree-aware architectures:

**Tree-Transformers.** Modify the attention mechanism so that attention weights reflect tree distance (parent-child, sibling) rather than sequential position. Each node attends more strongly to its parent, children, and siblings than to distant nodes.

**Graph Neural Networks (GNNs).** Represent the AST as a graph. Each node is a vertex with features (node type, type annotation). Edges represent parent-child and sibling relationships. Message-passing layers propagate information along the tree structure.

These approaches are theoretically more natural — the program is a tree, so a tree-aware model should learn more efficiently. In practice, linearized transformers are better supported by existing infrastructure (training frameworks, attention optimizations, hardware). Both approaches should be evaluated empirically.

### 4.5 Type Annotations as First-Class Information

Every token in the serialized sequence carries a type annotation. This serves three purposes:

1. **Training signal.** The model learns the relationship between operations and types — that `App` of a function `A → B` to an argument of type `A` produces type `B`. This is type theory, learned statistically rather than programmed explicitly.

2. **Structural constraint during generation.** At inference time, the type annotation at each position constrains what the next token can be. This enables type-directed filtering (Section 6).

3. **Compositional reasoning.** Types make explicit the contract between parts of the program. The model can learn that certain type signatures (e.g., `List α → List α`) are associated with certain subtree shapes (e.g., recursive traversal with element-wise transformation), enabling transfer across specific types.

---

## 5. Training Pipeline

### 5.1 Phase 1: Corpus Construction

**Data sources.** Collect programs from typed language ecosystems:

| Source          | Language   | Approximate scale                         | Core representation    |
| --------------- | ---------- | ----------------------------------------- | ---------------------- |
| Hackage         | Haskell    | ~16,000 packages, millions of definitions | GHC Core (System FC)   |
| Mathlib         | Lean 4     | ~180,000+ declarations, 1.5M+ lines       | Lean `Expr`            |
| opam            | OCaml      | ~4,000 packages                           | OCaml Typedtree        |
| crates.io       | Rust       | ~140,000 crates                           | Rust HIR/MIR           |
| DefinitelyTyped | TypeScript | ~10,000 type definition packages          | TypeScript AST + types |

For each package/module:

1. Build the project using the language's standard toolchain
2. Hook into the compiler at the core-representation stage
3. Extract every definition as a typed AST
4. Normalize (alpha-rename, strip artifacts, inline trivials)
5. Serialize to the token format described in Section 4

**Expected corpus size.** Millions of function definitions, yielding hundreds of millions to billions of AST tokens. Comparable in scale to a mid-size text corpus for LLM pretraining, but far more structurally regular.

**Multi-language core.** Different languages have slightly different core calculi, but the overlap is substantial — application, abstraction, case analysis, let binding, and constructors are universal. A unified core representation abstracting over language-specific details enables cross-language training, where patterns learned from Haskell programs transfer to Lean programs because the core operations are the same.

### 5.2 Phase 2: Supervised Pretraining

Train a transformer on next-node prediction over the serialized corpus.

**Objective.** Standard autoregressive language modeling: given the sequence of AST tokens so far, predict the next token.

```
Input:   APP:Int  LAM:Int→Int  BIND:v0:Int  APP:Int  VAR:+:Int→Int→Int
Target:  VAR:v0:Int
```

**Architecture.** A decoder-only transformer, analogous to GPT/Llama but with the smaller AST vocabulary. Because the vocabulary is 100-500x smaller than a text LLM's vocabulary, the embedding layer is correspondingly smaller. The bulk of the parameters are in the attention and feedforward layers — the layers that learn compositional structure.

**Model scale.** The structured, constrained nature of the AST space suggests that smaller models may achieve strong performance relative to text LLMs. Initial experiments should explore the 500M-3B parameter range. The hypothesis (to be validated empirically): because the vocabulary is smaller, the sequences are more regular, and types constrain composition, the model requires fewer parameters to learn the distribution.

**What the model learns (emergent, not prescribed):**

At the token level, it learns local composition rules — that `APP` is followed by two subtrees, that the type of a `BIND` constrains what `VAR` references can follow, that `CASE` introduces branches.

At the pattern level, it learns that certain subtree shapes recur with high frequency:

- The `map` pattern: `LAM BIND:f LAM BIND:xs CASE VAR:xs BRANCH:nil ... BRANCH:cons ... APP VAR:f ... recurse`
- The `fold` pattern: `LAM BIND:f LAM BIND:acc LAM BIND:xs CASE VAR:xs BRANCH:nil VAR:acc BRANCH:cons ... APP VAR:f ... recurse`
- The `filter` pattern: `LAM BIND:p LAM BIND:xs CASE VAR:xs BRANCH:nil ... BRANCH:cons CASE APP VAR:p ... recurse`

These are algorithms, discovered as statistical regularities — not prescribed as vocabulary entries.

At the architectural level, it learns that certain patterns of pattern composition are associated with certain type signature shapes — that `List α → List α` programs often involve element-wise traversal, that `List α → α` programs often involve accumulation, that `α → α → Ordering` functions are often used inside sorting patterns.

### 5.3 Phase 3: Type-Constrained Decoding

At generation time, the model proposes the next AST node. Before accepting it, the type context constrains what is valid:

```
Current partial AST:
    APP : ?
    ├── VAR "sort" : List Nat → List Nat
    └── [HOLE] ← must have type List Nat

Model's raw distribution:
    VAR:xs:List Nat      — 0.30  ✓ type-compatible
    APP:...:List Nat     — 0.20  ✓ type-compatible (if it produces List Nat)
    LIT:5:Nat            — 0.15  ✗ wrong type (Nat ≠ List Nat)
    VAR:name:String      — 0.10  ✗ wrong type (String ≠ List Nat)
    ...

After type filtering (renormalized):
    VAR:xs:List Nat      — 0.60
    APP:...:List Nat     — 0.40
```

The type system acts as a **hard mask** on the model's output distribution. Only type-compatible continuations are sampled. This guarantees that every generated AST is well-typed by construction — structural correctness is not learned probabilistically, it is enforced mechanically.

The model's job is reduced to choosing among the **valid** options. It does not need to waste capacity learning which operations are type-compatible — the type checker handles that. It only needs to learn which type-compatible operations are **useful** in context. This is a fundamentally simpler learning problem than unconstrained token prediction, where the model must simultaneously learn validity and utility.

### 5.4 Phase 4: Reinforcement Learning from Execution

Supervised pretraining produces a model that generates plausible-looking ASTs. Reinforcement learning turns plausible into correct.

**Task specification.** A type signature, optionally accompanied by input-output examples and properties:

```
Goal type:  List Nat → List Nat
Examples:   f([3,1,2]) = [1,2,3], f([]) = [], f([5]) = [5]
Property:   ∀ xs, sorted(f(xs)) ∧ permutation(xs, f(xs))
```

**The RL loop:**

1. Present the type signature as the generation goal
2. The model generates a complete AST (a program) via autoregressive sampling with type-constrained decoding
3. The type checker verifies the AST is well-typed (this should always succeed due to Step 3, but serves as a sanity check)
4. Execute the program on the example inputs. Compare outputs to expected outputs
5. If properties are specified, check them (via testing on random inputs or, for bounded domains, exhaustively)
6. Compute reward:
   - Each test passed: positive reward
   - All tests passed: large bonus
   - Properties satisfied: additional bonus
   - Shorter program (fewer AST nodes): efficiency bonus
   - Program terminates within time bound: bonus
7. Update the model's weights via policy gradient (PPO or similar)

**Search augmentation.** During RL, augment greedy sampling with tree search:

- At each AST node, sample K candidates from the model's distribution (after type filtering)
- Explore multiple branches in parallel (beam search or MCTS)
- The type checker prunes invalid branches at every step (free)
- A value network estimates the probability of successful completion from each partial AST
- The search balances exploration of unlikely but potentially valuable branches with exploitation of the most promising path

**Curriculum.** Start with simple specifications (small types, few constraints) and gradually increase difficulty:

1. `Nat → Nat` with single-example specifications (identity, successor, doubling)
2. `List Nat → Nat` with examples (sum, length, maximum)
3. `List Nat → List Nat` with examples (reverse, sort, filter)
4. `List α → List α` with polymorphic types (generic versions)
5. Dependent types: `(xs : List Nat) → {ys : List Nat // Sorted ys ∧ Permutation xs ys}`

Each stage of the curriculum builds on patterns discovered in earlier stages. The model first learns basic recursion patterns, then list traversal, then sorting strategies, then how to simultaneously construct a value and a proof of its properties.

### 5.5 Phase 5: Emergent Library Discovery

After training, analyze the model's learned representations to extract discovered abstractions.

**Subtree frequency analysis.** Identify AST subtrees that recur with high frequency across successful programs. These are candidate algorithms — patterns the model has discovered as useful building blocks.

**Abstraction extraction.** For each high-frequency subtree, parameterize it: identify the parts that vary across instances (these become parameters) and the parts that are constant (these define the abstraction). Register the abstraction as a named operation.

```
Discovered pattern (appears in thousands of programs):
    LAM BIND:f LAM BIND:xs CASE VAR:xs
      BRANCH:[]  →  []
      BRANCH:(x :: rest)  →  CONS (APP VAR:f VAR:x) (RECURSE VAR:rest)

Extracted abstraction:
    map : (α → β) → List α → List β
    map f []        = []
    map f (x :: xs) = f(x) :: map f xs
```

**Vocabulary expansion.** Add the discovered abstraction to the model's vocabulary as a new AST node type. Where previously the model had to generate the 15-node subtree for `map` every time, it can now emit a single `MAP` node. Retrain on the expanded vocabulary.

This creates a virtuous cycle:

1. The model discovers a useful pattern as a statistical regularity
2. The pattern is extracted and named, becoming a new vocabulary entry
3. The model is retrained with the expanded vocabulary
4. Programs that use the new entry are shorter (fewer nodes)
5. Shorter programs mean shallower search
6. Shallower search means the model can tackle harder problems
7. Harder problems may reveal new patterns
8. Return to step 1

This is **automated abstraction discovery** — the model builds its own standard library from first principles, rather than inheriting one designed by humans. The resulting library may not match human conventions (it might discover `foldl` before `foldr`, or discover a combined `filter-map` as a single primitive), but it will reflect the actual statistical structure of useful computation.

---

## 6. Why This Approach May Work

### 6.1 The Analogy Holds in Detail

The structural parallels between text LLMs and the proposed AST approach are precise:

| Dimension            | Text LLM                            | AST model                              |
| -------------------- | ----------------------------------- | -------------------------------------- |
| Unit                 | Token (~50K vocab)                  | Typed AST node (~200-500 vocab)        |
| Corpus               | Internet text (trillions of tokens) | Desugared programs (billions of nodes) |
| Training objective   | P(next token \| context)            | P(next node \| partial tree, types)    |
| Emergent structure   | syntax → semantics → reasoning      | operations → patterns → algorithms     |
| Constraint mechanism | None (validity is statistical)      | Type system (validity is enforced)     |
| Verification         | None (no ground truth for text)     | Execution (programs can be tested)     |

The AST model has two advantages the text LLM lacks: type-based constraint at every step and execution-based verification at completion. These do not replace the statistical learning — they complement it, reducing the space the model must search and providing an exact reward signal for reinforcement learning.

### 6.2 The Distributional Hypothesis for Programs

In natural language, the distributional hypothesis states that words appearing in similar contexts have similar meanings. This is the foundation of word embeddings and, by extension, of LLM pretraining.

The analogous hypothesis for programs: **AST nodes appearing in similar type contexts and structural positions play similar computational roles.** A model trained on enough programs will learn that `Var "foldr"` and a certain manual recursion pattern are interchangeable because they appear in identical contexts — the model discovers functional equivalence from distributional evidence.

This extends to higher-order patterns. The model will learn that certain compositions of operations (a `Case` with accumulator threading) always appear in contexts where a sequence must be reduced to a single value — and that this pattern is what humans call "fold." The name is irrelevant. The statistical regularity is the concept.

### 6.3 The Type System as Built-In Curriculum

In text LLMs, all learning is from the same undifferentiated corpus. There is no structural guide to what the model should learn first.

In the AST approach, the type system provides a natural curriculum. Simple types (base types, function types) constrain the space tightly. Polymorphic types (`∀ α. List α → List α`) force the model to learn type-generic patterns. Dependent types (`(n : Nat) → Vec α n → Vec α n`) encode properties that programs must satisfy. Refinement types (`{x : Nat // x > 0}`) add value-level constraints.

Each level of type sophistication introduces tighter constraints and richer learning signal. A model can be trained progressively — first on simply-typed programs, then on polymorphic programs, then on dependently-typed programs — with each stage building on the patterns discovered in the previous one.

---

## 7. Relationship to the Formal Navigator

### 7.1 A Different Entry Point, Compatible Architecture

The formal navigator described in the companion documents (Navigating the Space of Formal Languages; Building the Navigator) enters the program space from the top: a type signature specifies the goal, and a learned policy navigates the space of typed operations to reach it. The navigator operates at the tactic level in Lean 4, using MCTS and reinforcement learning.

The AST approach enters from the bottom: primitive core calculus operations are the unit, and the model learns the structure of computation from their statistical distribution. Higher-level concepts (algorithms, patterns, design abstractions) emerge from the learning rather than being prescribed.

These are complementary, not contradictory. The AST approach provides the foundation — a model that understands how computation is built from primitives. The formal navigator provides the goal-directed search — a mechanism for steering the model toward programs satisfying specific type signatures and properties. A mature system could combine both:

- The AST model provides the **policy network** — trained on core calculus primitives, it proposes next operations based on learned patterns
- The type system provides **hard pruning** — invalid operations are filtered at every step
- MCTS provides **strategic search** — exploring multiple paths when the policy is uncertain
- Execution provides **verification** — testing completed programs against specifications
- Emergent library discovery provides **vocabulary expansion** — the system builds its own catalog of useful abstractions over time

### 7.2 What This Approach Adds

The primary contribution of the AST approach is the claim that **the right level of primitive matters, and it is lower than library functions but higher than assembly.** The typed core calculus is the sweet spot where:

- Programs are short enough for statistical learning
- Types provide enough structure for constrained generation
- The vocabulary is small enough for the model to learn the full grammar of composition
- Higher-level concepts can emerge rather than being prescribed

The formal navigator documents assume typed operations at roughly the library level. The AST approach provides a principled foundation beneath that — a way for the system to discover what the right library-level operations should be, rather than inheriting them from human design decisions.

---

## 8. Open Questions

### 8.1 Representation

Does depth-first linearization preserve enough tree structure for transformers to learn compositional patterns effectively? Or do tree-structured models (Tree-Transformers, GNNs) learn significantly faster? The tradeoff is between existing infrastructure support (linearized transformers) and theoretical fit (tree models). Both should be evaluated empirically.

### 8.2 Cross-Language Transfer

GHC Core, Lean `Expr`, and OCaml Typedtree are different concrete representations of similar abstract operations. Can a unified core representation capture the common structure, enabling a model trained on Haskell programs to generate Lean programs? The core operations (application, abstraction, case analysis, let binding) are shared; the differences are in type system details and evaluation semantics. A universal typed core calculus — a "common IR for training" — would maximize the usable corpus.

### 8.3 Scaling Laws

Do AST models exhibit scaling laws analogous to text LLMs? Does performance improve predictably with model size, dataset size, and compute? If so, what are the scaling exponents? The smaller vocabulary and stronger structural constraints may mean that the scaling curves are steeper (more performance per parameter) or shallower (diminishing returns earlier because the space is more constrained). This is an empirical question with significant implications for resource allocation.

### 8.4 Abstraction Depth

How many rounds of the discover-extract-retrain cycle (Section 5.5) are productive? Does the model discover increasingly abstract patterns with each round, or does it plateau after discovering basic algorithms? In natural language, there are many levels of abstraction (phonemes → morphemes → words → phrases → sentences → paragraphs → documents). Computation may have fewer natural levels, or different ones.

### 8.5 Sufficiency of the Core

Can every useful program be practically expressed as a composition of core calculus operations, or are there computational patterns that require primitives outside the core? Concurrency, hardware interaction, and system calls are candidates for operations that may not reduce cleanly to the core calculus. The boundary of what the core can express is the boundary of what the approach can synthesize.

---

_This document proposes an approach to learning the structure of computation from first principles: use typed core calculus operations — the primitive AST nodes that every program reduces to — as the fundamental unit, and train a model to discover algorithms, data structures, and design patterns as emergent statistical regularities in how those primitives compose. The approach applies the LLM paradigm (statistical learning from a large corpus of a primitive unit) to a space where types provide structural constraint and execution provides verification — advantages that natural language lacks._
