# Building the Navigator: An Implementation Path

## From Theory to Working System Using Existing Tools

---

## 1. Guiding Principle: Reuse Before Building

The theoretical framework — type-directed navigation of formal space with learned strategic guidance — does not require inventing new ML architectures. The individual components exist. The contribution is in how they are composed. This document specifies what to reuse, what to adapt, and what to build new.

---

## 2. The Formal Space: Lean 4

### 2.1 Why Lean 4

Lean 4 is not merely a theorem prover. It is a general-purpose programming language with dependent types that serves simultaneously as the formal space, the type checker, the verification oracle, and the execution environment.

**Dependent types provide refinement types natively.** A Lean type like `(n : Nat) → (m : Nat) → {r : Nat // r = n + m}` specifies a function that adds two numbers and carries a proof that the result equals their sum. The type signature itself is the specification. No separate specification language is needed.

**The type checker is the static verifier.** Every operation the navigator proposes is checked by Lean's kernel. Ill-typed compositions are rejected immediately. This provides the hard pruning of the search space at every step.

**Programs and proofs coexist.** A function can be written and its properties proved in the same file, same language. The navigator can synthesize a sorting function and simultaneously produce a proof that the output is sorted and is a permutation of the input.

**It is a practical language.** Lean 4 compiles to C. It supports IO, mutable state (via monads), data structures, metaprogramming, and has a growing package ecosystem. It is not a toy restricted to mathematical proofs.

**It has the strongest ML research ecosystem.** AlphaProof, DeepSeek-Prover-V2, LeanDojo, and other projects have chosen Lean as their formal foundation. Training data, tooling, and research community are concentrated here.

### 2.2 How Lean Implements the Theoretical Requirements

| Theoretical requirement    | Lean 4 implementation                                                    |
| -------------------------- | ------------------------------------------------------------------------ |
| Typed operations           | Lean terms with type annotations; tactics in tactic mode                 |
| Type-directed filtering    | The elaborator and type checker constrain valid terms at each position   |
| Explicit state             | `StateM`, `IO`, `ReaderM` monads make state part of the type             |
| Refinement types           | Subtypes: `{x : Nat // x < 100}` and dependent types generally           |
| Bounded state              | `Fin n` (naturals less than n), bounded collections, fintype instances   |
| Termination checking       | Built-in structural recursion checker; `decreasing_by` for custom proofs |
| Execution oracle           | `#eval` for interpretation; compiled execution via C backend             |
| Compositional verification | Lemma-based proof structure; each lemma verified independently           |

### 2.3 Lean's Tactic Mode as the Navigation Interface

In Lean's tactic mode, the system maintains a **tactic state**: a set of goals (types to be filled) and a local context (available hypotheses, variables, and their types). The user (or navigator) applies tactics — typed operations that transform the tactic state. Each tactic either closes a goal or produces sub-goals.

```lean
-- Entry point: a type signature (the goal)
def minTransactions (debts : List (String × Int))
    : {result : List (String × String × Int) // settlesAll debts result} := by
  -- Tactic state: one goal, the return type
  -- Navigator chooses a tactic...
  sorry -- placeholder: this is what the navigator fills
```

This is exactly the navigation step described in the theory:

1. Examine current state (goals + context)
2. Choose a valid operation (tactic)
3. Apply it (transforms goals)
4. Repeat until no goals remain

The tactic mode is the API through which the navigator interacts with the formal space.

---

## 3. The Navigator: Transformer + Reinforcement Learning

### 3.1 Architecture

The navigator is a transformer-based policy network that, given the current tactic state, proposes the next tactic to apply.

**Input representation:**

The tactic state consists of:

- Goal types (what needs to be constructed)
- Local context (available hypotheses, variables, and their types)
- History of tactics applied so far

This structured information is serialized into a token sequence for the transformer. LeanDojo already provides tooling for extracting tactic states as structured data from Lean.

The serialization should preserve type information explicitly. Rather than tokenizing Lean syntax as raw text, encode:

- Type constructors as distinct tokens
- Variable references with their types
- Goal structure (number of remaining goals, their types)

**Output:**

A tactic string — a Lean tactic command that the system attempts to execute. Lean's tactic evaluator either accepts it (advancing the state) or rejects it (invalid tactic for the current state). This binary accept/reject provides immediate, costless verification at every step.

**Model scale:**

Based on the theoretical analysis — the search space is more structured and constrained than natural language — and empirical results from proof synthesis research, a model in the 1–7B parameter range is the expected starting point. AlphaProof and DeepSeek-Prover demonstrate competitive results at these scales in the proof domain. Program synthesis in the same formal space should require comparable capacity.

### 3.2 Search Strategy: Monte Carlo Tree Search

MCTS is the proven search strategy for type-directed exploration, used by AlphaProof and AlphaZero.

**How it works in this context:**

1. From the current tactic state, the policy network proposes K candidate tactics (beam of possibilities)
2. Each candidate is applied to the tactic state (Lean checks validity)
3. Valid candidates produce new tactic states (children in the search tree)
4. The value network estimates the probability of successful completion from each child state
5. MCTS balances exploration (trying unlikely but potentially valuable tactics) with exploitation (pursuing the most promising path)
6. The search continues until a complete program is found or a computational budget is exhausted

**The value network:**

A separate model (potentially smaller, 100M–1B parameters) that takes a partial tactic state and estimates the likelihood of successful completion. Architecture options:

- A transformer encoding the serialized tactic state, outputting a scalar value
- A graph neural network (GNN) operating on the proof/program tree structure, with message passing along edges and readout at the root

The GNN is theoretically more natural (the partial program is a tree), but a transformer over the serialized state may be more practical given existing tooling. Both should be evaluated empirically.

### 3.3 Training Pipeline

#### Phase 1: Supervised Pretraining

**Data source:** Existing Lean 4 code and proofs.

- **Mathlib**: ~170,000 formally verified theorems with complete proofs. Each proof is a sequence of tactics with tactic states at every step. LeanDojo can extract these as (tactic_state, next_tactic) training pairs.
- **Lean 4 standard library and ecosystem**: Lean programs (not just proofs) demonstrating data structures, algorithms, IO, and general-purpose programming.
- **Extraction process**: For each Lean file, use LeanDojo or Lean's metaprogramming facilities to trace the elaboration, extracting the tactic state at each step and the tactic the human author chose. Each (state, tactic) pair is one supervised training example.

**Training objective:** Given a tactic state, predict the next tactic. Standard cross-entropy loss over tactic token sequences. This is analogous to pretraining an LLM on text, but the "text" is tactic sequences and the "context" is the tactic state.

**Expected outcome:** A model that can propose plausible tactics for a given tactic state — the Lean equivalent of an LLM that can produce plausible next sentences. Not yet strategically strong, but a starting point for RL.

#### Phase 2: Reinforcement Learning via Self-Play

1. Sample or generate a type signature (the goal)
2. The policy network proposes tactics; MCTS explores alternatives
3. Lean checks each tactic (static verification at every step)
4. Search continues until the program/proof is complete or budget is exhausted
5. **Reward signal:**
   - Did the full program type-check? (binary, strong signal)
   - Did it pass test cases? (binary per test, strong signal)
   - Did it satisfy stated properties? (binary, strong signal)
   - How deep was the search? (efficiency bonus for shorter solutions)
   - Did it reuse catalog entries? (bonus for leveraging known building blocks)
6. Policy and value networks are updated using the reward signal (PPO or similar policy gradient method)

**Self-play loop:** The system generates thousands of synthesis attempts for varied type signatures, tracking which succeed and which fail. The value network trains on (partial_state, eventual_outcome) pairs. As the value network improves, it guides MCTS more effectively, producing better training data, which improves the value network further — the same virtuous cycle that powers AlphaZero.

#### Phase 3: Catalog Expansion

When the navigator successfully synthesizes a verified program for a type signature, the result is registered in a capability catalog (see Section 5). Future synthesis tasks can reuse this result. Over time:

- More catalog entries → more branches terminate early via lookup
- Shallower effective search → harder problems become tractable
- New verified programs → more catalog entries

This is the positive feedback loop where each solved problem enriches the space for future exploration.

---

## 4. The Verification Stack

### 4.1 Static Verification: Lean's Type Checker

Applied at every step of navigation. When the navigator proposes a tactic, Lean's kernel checks it against the current tactic state. If it type-checks, the state advances. If not, the tactic is rejected and the navigator tries another.

**Cost:** Effectively free in computational terms. Lean's type checker is highly optimized and runs in milliseconds for typical tactics.

**Guarantee:** Every intermediate state and every completed program is well-typed. Structural correctness is maintained by construction.

### 4.2 Dynamic Verification: Execution and Testing

Applied at completion. Once the navigator produces a complete program, execute it against test cases:

```lean
-- The navigator synthesized this function
def synthesized_sort (xs : List Nat) : List Nat := ...

-- Dynamic verification: run it on test inputs
#eval synthesized_sort [3, 1, 4, 1, 5]  -- expect [1, 1, 3, 4, 5]
#eval synthesized_sort []                 -- expect []
#eval synthesized_sort [1]                -- expect [1]
```

For bounded domains, exhaustive verification is possible:

```lean
-- If inputs are bounded, check ALL of them
#eval (List.fins 5).all fun xs =>
  let result := synthesized_sort xs.val
  isSorted result && isPermutation xs.val result
```

### 4.3 Property Verification: Lean Proofs

The strongest verification: prove that the program satisfies its specification for ALL inputs, not just test cases. This is possible when the type signature includes the property as a dependent type:

```lean
-- Type signature IS the specification
def verified_sort (xs : List Nat)
    : {ys : List Nat // Sorted ys ∧ Permutation xs ys} := ...

-- If this type-checks, the function is PROVABLY correct
-- The navigator must produce both the function and the proof
```

When the navigator can produce programs with proof-carrying types, dynamic testing becomes unnecessary for correctness (though still useful for performance evaluation). This is the highest tier of verification and corresponds to the theoretical ideal.

### 4.4 The Verification Hierarchy

```
Strength    Method                    When possible              Cost
─────────── ───────────────────────── ──────────────────────────── ──────────
Strongest   Proof-carrying types      Always (in Lean)            High (navigator must produce proofs)
Strong      Exhaustive testing        Bounded/finite domains      Medium (scales with domain size)
Moderate    Property-based testing    Any executable program      Low (randomized, probabilistic)
Baseline    Type checking             Every step, always          Free (built into navigation)
```

The navigator should target the strongest feasible verification for each synthesis task. Proof-carrying types when possible; exhaustive testing for bounded domains; property-based testing as a fallback.

---

## 5. The Capability Catalog

### 5.1 Purpose

The capability catalog is a searchable registry of formally verified functions. When the navigator encounters a sub-goal during search, it first checks the catalog: does a verified implementation already exist for this type signature?

If yes: the sub-goal is resolved immediately by referencing the catalog entry. No further search needed.

If no: the navigator continues exploring. If it eventually succeeds, the result is registered in the catalog for future use.

### 5.2 Structure

Each catalog entry contains:

```
{
  id: unique identifier,
  type_signature: the full Lean type (including dependent types / refinements),
  implementation: the Lean term (verified code),
  proof_obligations: any proofs attached to the type,
  source: "mathlib" | "stdlib" | "synthesized" | "user-contributed",
  performance: {
    time_complexity: optional,
    space_complexity: optional,
    benchmarks: optional
  },
  dependencies: [list of other catalog entries this one uses]
}
```

### 5.3 Retrieval: The Catalog Matcher

When the navigator needs to check if a sub-goal matches a catalog entry, it performs type-based retrieval.

**Exact matching:** Does the catalog contain an entry with exactly this type signature? This is a straightforward lookup, possibly using a hash of the normalized type.

**Subsumption matching:** Does the catalog contain an entry with a _more general_ type that can be specialized to this signature? For example, a polymorphic `sort : [α] → [α]` subsumes the specific `sort : [Nat] → [Nat]`. Lean's unification engine handles this.

**Semantic matching (learned):** For approximate matches — where the type signatures don't unify exactly but the capability is semantically close — a learned embedding model maps type signatures to vectors, and retrieval is nearest-neighbor search in embedding space. This handles cases like matching "stable sort" against "merge sort" where the match is semantic rather than syntactic.

The learned matcher is trained on (type_signature, matching_implementation) pairs extracted from Mathlib and the Lean ecosystem.

### 5.4 Bootstrapping the Catalog

**Initial population:**

- Lean 4 standard library (basic data structures, arithmetic, list operations, string manipulation, IO)
- Mathlib (mathematical functions, number theory, algebra, analysis — extensive)
- Community Lean packages (parsing, networking, etc.)

Each function in these libraries already has a Lean type signature — the catalog entry can be generated semi-automatically by extracting type signatures, implementations, and any associated proofs.

**Growth:**

Every successful synthesis adds a new entry. The catalog is append-mostly (entries are rarely removed, though they can be superseded by more efficient implementations with the same type signature).

---

## 6. Handling State in Practice

### 6.1 Lean's Monad Stack for Explicit State

Lean represents stateful computation through monads, making state explicit in the type:

```lean
-- Pure computation (no state)
def sum (xs : List Nat) : Nat := xs.foldl (· + ·) 0

-- Stateful computation (state is explicit in the type)
def runningSum (xs : List Nat) : StateM Nat (List Nat) := do
  let mut result := []
  for x in xs do
    modify (· + x)        -- modify state
    let s ← get           -- read state
    result := result ++ [s]
  return result

-- StateM Nat (List Nat) desugars to: Nat → (List Nat × Nat)
-- The navigator sees the full (Input, R) → (Output, R') type
```

The monad stack makes state visible to the type system and therefore to the navigator. Different monads for different kinds of state:

| Monad       | State pattern                             | Navigator sees                    |
| ----------- | ----------------------------------------- | --------------------------------- |
| `StateM S`  | Read/write mutable state of type S        | `(A, S) → (B, S)`                 |
| `ReaderM R` | Read-only environment of type R           | `(A, R) → B`                      |
| `ExceptT E` | Computation that may fail with error E    | `A → (B ⊕ E)`                     |
| `IO`        | Real-world effects (files, network, etc.) | Opaque — cannot verify statically |

For `IO` (real-world effects), static verification reaches its limit. The navigator can verify the pure logic but must rely on dynamic testing for IO correctness. This is the honest boundary of the approach.

### 6.2 Bounded State via Lean's Type System

```lean
-- Unbounded state (hard to reason about)
def processUnbounded (db : Database) : IO Result := ...

-- Bounded state (the navigator can work with this)
def processBounded (users : Fin 1000 → User) (balances : Fin 1000 → Nat)
    : {r : Result // r.total = (Finset.univ.sum balances)} := ...
```

The second version bounds the input domain (`Fin 1000` — at most 1000 entries), specifies the exact data needed (not the whole database), and includes a correctness property in the return type. This is maximally constrained and maximally verifiable.

The navigator should be trained to work with — and the system should encourage users to provide — the most bounded, precise types feasible for the task.

---

## 7. The Minimal Viable System

### 7.1 Scope

A system that synthesizes verified Lean 4 functions from type signatures, using type-directed search with a learned policy. Not a general-purpose software development tool. A focused program synthesizer for the formal domain.

### 7.2 Components

```
┌─────────────────────────────────────────────────────┐
│                    LEAN 4                            │
│  ┌──────────┐  ┌──────────┐  ┌────────────────┐    │
│  │  Type     │  │ Tactic   │  │  Execution     │    │
│  │  Checker  │  │ Evaluator│  │  Engine (#eval)│    │
│  └──────────┘  └──────────┘  └────────────────┘    │
└─────────┬──────────┬──────────────────┬─────────────┘
          │          │                  │
    type-checks   accepts/rejects    runs tests
    every step    each tactic        on completion
          │          │                  │
┌─────────▼──────────▼──────────────────▼─────────────┐
│              LEANDOJO INTERFACE                      │
│  Extracts tactic states, provides gym-like API      │
└─────────────────────┬───────────────────────────────┘
                      │
              tactic states + rewards
                      │
┌─────────────────────▼───────────────────────────────┐
│              NAVIGATOR                               │
│  ┌─────────────────┐    ┌────────────────────┐      │
│  │  Policy Network  │    │  Value Network     │      │
│  │  (Transformer)   │    │  (Transformer/GNN) │      │
│  │  1-7B params     │    │  100M-1B params    │      │
│  └────────┬────────┘    └────────┬───────────┘      │
│           │                      │                   │
│           └──────────┬───────────┘                   │
│                      │                               │
│              ┌───────▼───────┐                       │
│              │     MCTS      │                       │
│              │  Search over  │                       │
│              │  tactic seqs  │                       │
│              └───────────────┘                       │
└─────────────────────────────────────────────────────┘
                      │
              queries on sub-goals
                      │
┌─────────────────────▼───────────────────────────────┐
│           CAPABILITY CATALOG                         │
│  ┌──────────────┐  ┌──────────────────────┐         │
│  │  Type Index   │  │  Embedding Index     │         │
│  │  (exact match)│  │  (semantic match)    │         │
│  └──────────────┘  └──────────────────────┘         │
│                                                      │
│  Initialized from: Lean stdlib + Mathlib             │
│  Grows with: each successful synthesis               │
└──────────────────────────────────────────────────────┘
```

### 7.3 Build Steps

#### Step 1: Environment Setup

- Install Lean 4 toolchain (elan + lake)
- Set up LeanDojo for tactic state extraction and the gym-like interaction API
- Create a Lean project with Mathlib as a dependency (provides the initial catalog and training data)
- Verify the pipeline: can we programmatically send tactics to Lean and receive tactic states back?

**Existing tools:** Lean 4, LeanDojo, Mathlib. No new code required for this step.

#### Step 2: Training Data Extraction

- Use LeanDojo to extract (tactic_state, next_tactic) pairs from Mathlib and the Lean standard library
- Focus on both proof tactics AND programming constructs (Lean 4 supports both)
- Serialize tactic states into a format suitable for transformer input
- Build a dataset of ~100K–500K (state, tactic) pairs for supervised pretraining

**Existing tools:** LeanDojo's extraction pipeline. May need adaptation to include non-proof Lean code alongside proofs.

#### Step 3: Supervised Pretraining

- Train a transformer (start with 1–3B parameters) on the extracted dataset
- Objective: given a serialized tactic state, predict the next tactic
- Use standard language model training infrastructure (PyTorch, distributed training)
- Evaluate: given held-out tactic states, does the model propose valid tactics? At what rate?

**Existing tools:** Standard transformer architectures (Llama-style), standard training frameworks (PyTorch, DeepSpeed). No custom architecture needed.

**Decision point:** Start with a pretrained code model (e.g., fine-tune an existing small code LLM on Lean tactic data) versus training from scratch on Lean-specific data. The former is faster to start; the latter may produce better results. Both should be tried.

#### Step 4: RL Training Loop

- Define a benchmark suite: type signatures with test cases
  - Start simple: `List Nat → List Nat` (sorting), `Nat → Bool` (primality), `List Nat → Nat` (sum, max, min)
  - Progress to dependent types: `(xs : List Nat) → {ys : List Nat // Sorted ys ∧ Permutation xs ys}`
- Implement the RL loop:
  1. Present a type signature as the goal
  2. Navigator proposes tactics via the policy network
  3. MCTS explores alternatives using the value network for guidance
  4. Lean evaluates each tactic (accept/reject)
  5. Completed programs are tested against test cases
  6. Reward = type-checks (always) + tests pass + proof obligations met + efficiency bonus
  7. Update policy and value networks
- Train until the system can reliably synthesize correct programs for the benchmark suite

**Existing tools:** RL frameworks (PPO implementations in PyTorch), MCTS implementations. The novel integration is with LeanDojo as the environment.

#### Step 5: Catalog Integration

- Initialize the catalog from Lean's standard library and Mathlib
- Extract type signatures and implementations as catalog entries
- Implement exact-match retrieval (type unification) and semantic retrieval (learned embeddings)
- Modify the navigator's search: before exploring a sub-goal, check the catalog
- Register successful synthesis results as new catalog entries
- Evaluate: does catalog availability reduce search depth? Does the catalog grow meaningfully over time?

#### Step 6: Evaluation and Iteration

**Benchmarks:**

- **Correctness:** What fraction of synthesized programs satisfy their type signatures and pass tests?
- **Efficiency:** How many search steps (tactic attempts) are needed per synthesis?
- **Scaling:** How does performance change with model size (1B vs. 3B vs. 7B)?
- **Catalog impact:** How does synthesis difficulty decrease as the catalog grows?
- **Comparison:** How does this system compare to direct LLM code generation for the same tasks, given equivalent compute?

---

## 8. Hardware Requirements

### 8.1 Training

- **Supervised pretraining:** 4–8 GPUs (A100/H100 class) for several days. Comparable to fine-tuning a small LLM. Standard academic compute.
- **RL training:** The same cluster, running for longer (weeks). The bottleneck is MCTS rollouts through Lean, which require calling the Lean type checker thousands of times per training step. Lean is fast, but this is the computational core of the training loop.
- **Optimization:** Lean tactic evaluation can be parallelized across CPU cores. Multiple MCTS rollouts can run simultaneously. The GPU trains the networks; the CPUs run Lean.

### 8.2 Inference

- **Navigator (policy + value networks):** A single high-end consumer GPU (RTX 4090 / RTX 5090) for the 1–3B parameter models. A single datacenter GPU (A100) for 7B.
- **Lean type checker:** CPU-bound. A modern multi-core CPU handles this comfortably. Lean's kernel is highly optimized.
- **Catalog:** A vector database (for semantic matching) plus a standard database (for exact matching and metadata). Runs on CPU. Scales linearly with catalog size.
- **Total inference:** Plausibly a single workstation with one high-end GPU and a multi-core CPU.

---

## 9. Existing Work to Build On

### 9.1 Directly Relevant Systems

| System                 | What it provides                                                                            | How we reuse it                                                                        |
| ---------------------- | ------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| **LeanDojo**           | Gym-like API for Lean tactic interaction; tactic state extraction; proof search benchmarks  | The environment interface. Navigator interacts with Lean through LeanDojo's API        |
| **ReProver**           | Transformer-based proof search for Lean via LeanDojo. Retrieval-augmented premise selection | Starting architecture. Fine-tune or adapt for program synthesis                        |
| **DeepSeek-Prover-V2** | RL-trained proof generator for Lean, achieving state-of-the-art on formal math benchmarks   | Training methodology. Their RL pipeline for Lean proof search is directly transferable |
| **AlphaProof**         | MCTS + RL for Lean proof search, IMO silver medal level                                     | Target architecture. The full MCTS + policy + value network approach                   |
| **Mathlib**            | 170K+ verified Lean theorems and definitions                                                | Training data + initial catalog                                                        |

### 9.2 Adjacent Systems Worth Studying

| System                                    | Relevance                                                                                                                      |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **AlphaDev**                              | RL-based program synthesis at the assembly level. Demonstrates that RL can synthesize novel, correct programs in formal spaces |
| **AlphaEvolve / OpenEvolve**              | Evolutionary program synthesis. An alternative search strategy (evolution vs. MCTS) for the same fundamental problem           |
| **Synquid**                               | Type-directed program synthesis using refinement types. Provides theoretical grounding for type-based search space pruning     |
| **Copilot / Cursor / AI code assistants** | Demonstrate the LLM baseline. Any formal navigator should be benchmarked against these for equivalent tasks                    |

---

## 10. Risks and Mitigations

### 10.1 Risk: Lean's Ecosystem Is Too Small for Training Data

Lean 4 code (especially non-proof code) is far less abundant than Python or JavaScript.

**Mitigation:** (a) Lean proofs are structurally similar to programs (Curry-Howard). Mathlib's 170K theorems provide substantial training signal even for program synthesis. (b) Synthetic data generation: programmatically generate type signatures and reference implementations, then use these for training. (c) Transfer learning from code LLMs pretrained on large multilingual code corpora, fine-tuned on Lean.

### 10.2 Risk: MCTS Rollouts Through Lean Are Too Slow for RL Training

Each MCTS rollout requires many calls to Lean's tactic evaluator. If each call takes tens of milliseconds, thousands of rollouts per training step could be prohibitively slow.

**Mitigation:** (a) Parallelize rollouts across CPU cores. Lean instances are lightweight and independent. (b) Use the policy network to prune the search tree aggressively — only explore the top-K most promising tactics at each step. (c) Cache tactic evaluation results. Many sub-expressions recur across rollouts.

### 10.3 Risk: The Gap Between Proof Synthesis and Program Synthesis Is Larger Than Expected

AlphaProof proves theorems. We want to synthesize programs. These are formally the same (Curry-Howard) but practically may differ — programs have performance concerns, IO, and practical constraints that proofs don't.

**Mitigation:** Start with pure, total functions (where the proof-program equivalence is strongest). Gradually introduce effects, state, and performance constraints. Measure the performance drop at each step to understand where the gap actually lies.

### 10.4 Risk: The Capability Catalog Doesn't Reach Critical Mass

If the catalog is too sparse, the navigator rarely gets to terminate early via lookup, and search remains expensive.

**Mitigation:** Bootstrap aggressively from Mathlib and stdlib. Prioritize synthesizing common, reusable building blocks (sorting, searching, data structure operations) early. Measure catalog hit rate during synthesis and optimize for it.

---

## 11. Success Criteria for the Minimal Viable System

The system demonstrates value if:

1. **It synthesizes correct functions from type signatures** for a meaningful benchmark suite (not just trivial examples)
2. **Static verification holds by construction** — every output type-checks
3. **Dynamic verification passes** — synthesized functions pass test suites
4. **The catalog accelerates synthesis** — problems that reuse catalog entries are solved faster than novel ones
5. **It scales with type precision** — richer type signatures (refinement types, dependent types) produce correct results more efficiently, not less
6. **It finds solutions that LLMs miss** — for type-constrained synthesis tasks, the navigator produces correct results where direct LLM generation hallucinates or produces type errors

Criterion 6 is the proof of concept. If type-directed navigation in formal space outperforms statistical token prediction for formally specified tasks, the theoretical foundation is empirically validated and the path to a more capable system is clear.

---

_This document specifies a concrete implementation path for a type-directed program synthesis system, built on Lean 4 as the formal space, LeanDojo as the interaction layer, transformer + MCTS as the navigation mechanism, and reinforcement learning as the training methodology. Every major component reuses existing, proven tools. The novel contribution is their composition into a system that navigates formal code space using learned strategic guidance with verification at every step._
