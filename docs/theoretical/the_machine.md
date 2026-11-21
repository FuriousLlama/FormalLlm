# The Machine: End-State Vision and Creation Path

*Written 2026-02-23, after Sprint 4 post-mortem and Sprint 5 planning.*

---

## What the Machine Does

The machine takes a **type signature** and produces a **well-typed, executable program** that inhabits that type. That's the core loop. Everything else is refinement of what "inhabits" means and optimization of how to find the right inhabitant.

A type signature alone is ambiguous — `Int -> Int` has infinitely many inhabitants. The machine takes additional signals that narrow the search:

### Inputs, in Order of Constraint Strength

1. **Type signature** — the hard structural constraint. `(a -> b) -> [a] -> [b]` eliminates 99.99% of the program space instantly. This is the coordinate in formal space.

2. **Library context** — what building blocks are available. "You have `map`, `filter`, `foldr`, `++`, `:`" defines the vocabulary of operations. This bounds the search tree's branching factor.

3. **Examples / properties** — behavioral refinement. `f (+1) [1,2,3] = [2,3,4]` pins the program to a specific inhabitant of the type. This is where proof ends and testing begins — the halting problem boundary.

4. **Compositional context** — where this function lives in a larger system. "This is called after `parse` and before `validate`" constrains the semantic role. This is the least formal input and the last to be integrated.

The machine does **not** take natural language. Natural language is a lossy compression of all four inputs above. The theoretical framework says: work with the higher-fidelity representation directly. A separate system can translate natural language to type signatures + properties — that is a language problem, not a formal-space navigation problem.

---

## How It Operates

The operation is **type-directed search with learned heuristics**. At each step:

1. Examine the current partial program — a tree of typed operations with **typed holes** (unfilled sub-expressions that have known types)
2. Select a hole to fill (the **decomposition policy**)
3. The type checker enumerates valid operations for that hole — this is a finite, computable set
4. A learned distribution ranks the valid operations by likelihood of being useful (the **selection policy**)
5. Fill the hole, which may create new typed holes
6. Repeat until no holes remain
7. Execute the completed program against examples/properties
8. If it fails, backtrack and try the next-ranked operation

This is tree search, not sequence prediction. The critical distinction: in autoregressive sequence generation, the model must learn both *what is valid* and *what is useful*. In type-directed search, the type checker handles validity — the model only learns *what is useful among the valid options*. That is a fundamentally simpler learning problem.

### Three Verification Layers

The machine has three verification layers running at different timescales:

- **Per-step** (microseconds): type checking. Every intermediate state is well-typed. Invalid programs are impossible by construction.
- **Per-candidate** (milliseconds): execution against examples. Complete programs are tested. This is the dynamic verification oracle.
- **Per-search** (seconds): property checking. Candidates that pass examples are tested against broader properties (QuickCheck-style). This catches programs that pass specific examples but fail generally.

---

## The Key Components

### 1. The Representation Layer

A canonical, typed intermediate representation. This is what sprints 0-4 built. De Bruijn IR with lambda lifting, closed terms, verified by round-tripping through GHC. This is the "map" of the formal space.

### 2. The Prior

A learned distribution over typed programs. This is what sprints 3-5 are training. Not a generative model in the language-model sense — it is a *heuristic* for search. It answers: "given a partial program with a `[a] -> Int` hole and `map` and `foldr` in scope, what's the most likely useful completion?"

The prior does not need to be perfect. It needs to be good enough that search finds the right answer within a tractable budget.

### 3. The Verifier

GHC's type checker + runhaskell. This exists already. The type checker is the static oracle (decidable, instant, applied at every step). runhaskell is the dynamic oracle (empirical, applied at completion). Together they implement the two-regime verification from `navigating_formal_space.md`.

### 4. The Library

A searchable catalog of verified building blocks. When a sub-goal matches a known implementation, the entire subtree collapses to a lookup. This is the single most important scaling mechanism: as the library grows, search gets shallower. Each verified function is a shortcut through the space.

### 5. The Decomposition Engine

Breaks complex types into sub-goals. `(a -> b) -> [a] -> [b]` decomposes into: "take a function and a list, apply the function to each element, collect results." This decomposition can itself be learned (a policy over decomposition strategies) or hard-coded for common patterns.

---

## How It Was Created

### Phase 1: Learn the Space (Sprints 0-4 — complete)

Extract programs from a real codebase (GHC Core from Hackage). Build a canonical representation (de Bruijn IR). Verify that the representation is faithful (round-trip through Haskell execution). Build the vocabulary — not of text tokens but of typed operations.

**Key output:** a corpus of 4,438 verified typed programs in canonical form. The map of the territory.

### Phase 2: Learn the Prior (Sprints 5-6 — current)

Train a model that predicts program structure conditioned on type signatures. The model does not need to be the final architecture — it needs to answer: "does type information help predict program structure?" If yes, types are the right coordinate system. If no, the theoretical framework is wrong.

Sprint 5 tests this with the simplest possible implementation: autoregressive sequence generation with type tokens as a prefix. This is a linearization of the real problem (tree search) but it is sufficient to validate the hypothesis.

**Key output:** empirical evidence that P(body | type) >> P(body) — that type conditioning actually constrains the output distribution.

### Phase 3: Add Hard Constraints (Sprint 7)

Replace soft type conditioning (type tokens in the prompt) with hard type constraint (type checker in the generation loop). At each decoding step, mask out tokens that would create type errors. This is constrained decoding — already implemented for structural validity in `constrained_decode.py`, extended to type validity.

This is where the theoretical framework's "smaller vocabulary per step" advantage becomes real. Instead of choosing from 150 tokens at each position, the model chooses from the 10-20 tokens that are type-valid in the current context. The search space shrinks exponentially.

**Key output:** zero type errors in generated programs by construction, not by luck.

### Phase 4: Move from Sequences to Trees (Sprint 8+)

Replace autoregressive sequence generation with tree-structured generation. The model predicts AST nodes, not tokens. Each prediction fills a typed hole and may create new typed holes. This is where the architecture matches the theoretical framework's description of navigation.

The implementation: instead of a causal transformer over a flat sequence, use a transformer that sees the current partial AST (serialized as a sequence of typed holes + filled nodes) and predicts the next hole-filling operation. The search structure is explicit — you can do beam search, MCTS, or best-first search over partial programs.

**Key output:** the transition from "language model that generates code-like text" to "search engine over typed programs."

### Phase 5: Close the Loop (Stage 0)

Integrate execution feedback into the search. Generate a candidate, test it, use the result to guide further search. Failed candidates provide negative signal (this branch does not work). Successful candidates provide positive signal (this substructure is useful). This is reinforcement learning, but grounded — the reward is binary (passes tests or does not) and immediate (runhaskell takes milliseconds).

**Key output:** a system that improves with search time. Give it more compute, it solves harder problems. This is the AlphaProof property.

### Phase 6: Build the Library (Stage 0+)

Every successfully synthesized and verified function becomes a building block for future synthesis. The library grows monotonically. Search gets shallower over time because more sub-goals match known implementations. This is the compound interest of formal verification — each proof/program makes future proofs/programs easier.

**Key output:** a self-improving system where past work directly accelerates future work.

---

## Honest Assessment

### Where We Are

We are in Phase 2. The gap between here and the end-state is large but every piece along the way is independently valuable and testable. Sprint 5 does not need phases 3-6 to be useful — if type conditioning works, that is a validated result about the structure of formal space. Phase 3 does not need phase 4 — constrained decoding with a sequence model is already a better code generator than unconstrained generation.

### What Is Not Sequential

The phases are not strictly sequential. Phase 3 (hard constraints) could be done alongside Phase 2 — extend `constrained_decode.py` with type awareness while training the type-conditioned model. Phase 6 (library) is already partially implemented — the corpus of 4,438 verified functions *is* a library; the benchmark's dependency substitution mechanism already does library lookup.

### What to Watch For

The temptation to jump to Phase 4 (tree-structured generation) before Phase 2 is validated. Tree-structured models are architecturally complex and the research literature on them is mixed. The sequence model with type conditioning and hard constraints might be sufficient for a surprisingly long time — especially if the library grows large enough that most sub-goals are lookups rather than synthesis.

### The 80/20

The theoretical framework is right about the end-state. The engineering question is how much of that end-state you need to build before the system is useful.

Type conditioning (Phase 2) + hard constraints (Phase 3) + a growing library (Phase 6) gets you 80% of the value. Tree search (Phase 4) and closed-loop execution (Phase 5) are optimizations on top.

---

*This document projects forward from the current sprint work (Phases 1-2) to the full machine described in `navigating_formal_space.md`, identifying the creation path and the minimum viable subset.*
