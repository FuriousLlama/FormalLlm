# The Learning Architecture: ML Foundations for Recursive Capability Decomposition

## Defining What the System Learns, How It Learns, and the Shape of the Space It Explores

---

## 1. The Knowledge Representation Problem

A central question for any capability-based code explorer is how the system "knows" what capabilities exist. There are two fundamentally different approaches, and the architecture requires both.

### 1.1 External Catalog (Explicit Knowledge)

Capabilities are stored as formal objects in an external registry. Each entry contains:

- A unique identifier and human-readable name
- An input-output type signature
- Preconditions and postconditions (the formal contract)
- Performance characteristics (time complexity, space complexity, latency bounds)
- Pointers to one or more concrete implementations
- Metadata: language, platform, dependencies, maturity, trust level

The explorer queries this catalog during decomposition. When it reaches a sub-capability and asks "has this already been solved?", it performs a lookup against this registry. Either a matching capability exists or it doesn't. No ambiguity, no hallucination.

The catalog is explicit, inspectable, and updatable without retraining. A new library is published; its capabilities are registered; the explorer can immediately use them. This is how the system represents stable terrain — a database of solved problems.

### 1.2 Learned Internal Representations (Implicit Knowledge)

Capabilities are also encoded implicitly in the model's weights, analogous to how an LLM "knows" that certain code patterns solve certain kinds of problems. Through training on large corpora of well-architected software, the model internalizes the landscape of what's possible — the shapes of common decompositions, the typical sub-capabilities that arise from various specification patterns, the compositional idioms that tend to work.

This captures the fuzzy, intuitive knowledge that experienced programmers possess: the sense that "this feels like a caching problem" before any formal analysis. It handles approximate matching naturally and can generalize to novel combinations never seen explicitly during training.

### 1.3 The Hybrid: How Both Work Together

The **learned model** provides decomposition strategy. Given a specification, it proposes candidate decompositions. This is the intuitive, heuristic component — the part that says "this specification should probably decompose into authentication plus data pipeline plus business logic."

The **external catalog** provides ground truth for resolution. When the explorer reaches a leaf node and checks whether a known implementation exists, it queries the formal registry. This is the verified, exact component. Either there's a match or there isn't.

The interplay between them is where the power lives:

1. The learned model proposes a decomposition
2. Sub-capabilities are matched against the catalog
3. Matches terminate that branch (stable terrain reached)
4. Non-matches trigger further decomposition (recursion continues)
5. The model receives reinforcement signal based on whether the full tree eventually resolves and verifies
6. Over time, the model learns to propose decompositions that lead to successful catalog matches — the RL signal teaches the model the shape of the catalog and the composition patterns that work

This is analogous to how AlphaProof works: the neural network proposes proof tactics (learned intuition), the Lean proof assistant verifies them (formal ground truth), and the reinforcement signal from verification trains the network to propose better tactics over time.

---

## 2. What Is Being Learned: Four Distinct Components

The system contains four distinct learning problems, each with its own ML approach, training signal, and representation.

### 2.1 Decomposition Policy

**What it does**: Given a capability specification, proposes how to decompose it into sub-capabilities connected by a composition graph.

**Input**: A formal capability specification (types, constraints, properties, performance requirements).

**Output**: A set of sub-capability specifications plus a composition topology (the graph connecting them — sequential, parallel, conditional, iterative, or hybrid).

**ML approach**: Transformer-based policy network trained with reinforcement learning.

The transformer architecture is naturally suited to this task because decomposition requires attending to the relationships between different parts of a specification. A "transactional" constraint implies certain sub-capabilities; a "real-time" constraint implies different ones. Self-attention captures these cross-specification dependencies.

**Training data**: Extracted from existing well-architected open-source software. A codebase with clean interfaces is essentially a pre-existing decomposition tree. The training process reverse-engineers the decomposition from existing code:

- Parse the module/package structure of well-designed projects
- Extract interface contracts (type signatures, documented pre/postconditions)
- Reconstruct the capability tree: which modules depend on which, what capability does each provide, how do they compose
- Use these reconstructed trees as supervised examples for initial training
- Apply RL fine-tuning using verification feedback from the full recursive system

**Reward signal**: Did this decomposition eventually lead to a fully resolved tree where every leaf matches a known capability and every node's composition verifies against its specification? Successful decompositions are reinforced. Dead-end decompositions are penalized. Decompositions that resolve with fewer recursion levels (more efficient use of the catalog) receive higher reward.

### 2.2 Value Network

**What it does**: Given a partial decomposition tree (some nodes resolved, some still open), estimates the probability that the tree can be completed successfully. This is the system's learned intuition about whether a path is promising.

**Input**: A partial decomposition tree — a graph where some leaf nodes are resolved (matched to catalog entries) and others are open (unresolved specifications).

**Output**: A scalar value estimating the probability of successful completion.

**ML approach**: Graph neural network trained with Monte Carlo Tree Search (MCTS).

A graph neural network (GNN) is the natural architecture here because the decomposition tree is a variable-sized graph structure. The GNN processes the tree by passing messages along edges — resolved leaves propagate "confidence" upward, unresolved leaves propagate "uncertainty," and the aggregated signal at the root becomes the value estimate.

The MCTS component works as in AlphaZero/AlphaProof: the system simulates many possible decomposition trajectories from the current partial tree, observes which ones lead to successful resolution, and trains the value network to predict the outcomes. Over time, the value network learns to identify promising partial decompositions early, allowing the system to prune unpromising branches before investing computational resources in deep exploration.

**Training process**: Self-play through decomposition. The system generates thousands of decomposition attempts for varied specifications, tracking which succeed and which fail. The value network is trained on (partial tree, eventual outcome) pairs. As the value network improves, it guides the search more effectively, which generates higher-quality training data, which further improves the value network — the same virtuous cycle that makes AlphaZero work.

### 2.3 Catalog Matcher

**What it does**: Given a sub-capability specification, finds the best matching capability from the external catalog. This is a retrieval problem with semantic understanding of formal contracts.

**Input**: A capability specification (types, constraints, properties).

**Output**: A ranked list of catalog entries with match confidence scores.

**ML approach**: Contrastive learning to create a shared embedding space for specifications and catalog entries.

The training process creates pairs of (specification, matching catalog entry) and (specification, non-matching catalog entry) from existing software. The model learns to embed specifications and catalog entries into a shared vector space where matching pairs are close and non-matching pairs are far apart.

This is similar to how modern code search engines work, but operating on formal specifications rather than natural language descriptions. The precision of formal contracts makes this matching problem significantly more tractable than general semantic search — type signatures alone provide strong structural constraints.

**Key challenge**: The match between a specification and a catalog entry is often approximate. A specification might require "sort with stability guarantee" while the catalog has "merge sort" and "quicksort." The matcher needs to understand that merge sort satisfies the stability constraint while quicksort doesn't — a semantic judgment that goes beyond syntactic type matching.

**Resolution**: The embedding space encodes not just structural (type-level) similarity but also semantic (property-level) similarity. Training on large numbers of (specification, correct implementation) pairs teaches the model these property-level relationships.

### 2.4 Novelty Synthesizer

**What it does**: When decomposition reaches a sub-capability with no catalog match that can't be further decomposed into known parts — genuine novel territory — this component generates new code.

**Input**: A precise, narrow formal specification (the leaf of the decomposition tree that couldn't be resolved).

**Output**: A verified implementation satisfying the specification.

**ML approach**: RL-based program synthesis, in the style of AlphaDev (for low-level performance-critical code) or AlphaEvolve (for higher-level algorithmic code).

Because the specification arriving at this component has been refined through multiple levels of decomposition, it is far more precise and narrow than a typical code generation prompt. The search space is correspondingly smaller. Instead of "write me an expense tracker," the novelty synthesizer might receive "given a list of (participant, amount) pairs, return a list of (payer, payee, amount) triples that settles all balances with the minimum number of transactions, preserving the invariant that the sum of all triples equals zero."

This specificity is what makes synthesis tractable at the leaves. The recursive decomposition has done the work of narrowing the problem until the remaining novel part is small enough to solve by direct exploration.

**Verification**: The synthesized code is verified against the leaf specification before being accepted. This verification is the formal check — does the implementation satisfy the contract? Lean-style proof verification for correctness-critical code, or test-suite-based verification for more pragmatic contexts.

---

## 3. The Search Space: Dimensionality and Structure

### 3.1 Local Dimensionality at Each Node

At any given node in the decomposition tree, the explorer chooses how to decompose one capability into N sub-capabilities. The dimensions of choice are:

**Branching factor** (how many sub-capabilities): Typically 2-10 for well-structured decompositions. Software engineering principles heavily constrain this — decompositions with dozens of children almost never represent good design. The model's training on real software architectures encodes this constraint implicitly.

**Sub-capability selection** (which sub-capabilities from the space of possible functional descriptions): This is the highest-dimensional component. The space of possible capability specifications is in principle very large. However, it is heavily constrained by the parent specification. A "sorting" specification constrains children to comparison, swapping, partitioning, and similar operations. A "web server" specification constrains children to routing, request handling, response formatting, and similar. At any given node, most of the capability space is irrelevant.

**Composition topology** (how the sub-capabilities connect): Sequential, parallel, conditional, iterative, or hybrid graph structures. The number of meaningfully different topologies for 2-10 nodes is moderate — perhaps dozens to hundreds of plausible structures.

**Effective local branching factor**: Accounting for all constraints, the number of meaningfully different decompositions at each node is estimated at hundreds to low thousands. This is comparable to the branching factor in Go (approximately 250 legal moves per position), a game that is tractable with MCTS and learned value functions.

### 3.2 Global Space Across the Full Tree

The full decomposition tree can be 5-15 levels deep and 2-10 children wide at each node. The total combinatorial space is the product of choices at each node, which is astronomically large in the worst case.

However, the space has enormous structure that makes it tractable:

**Constraint propagation**: The parent specification heavily constrains what children are possible. At each level, you search only the subspace consistent with constraints flowing down from above. This is analogous to Sudoku: each cell technically has 9 possibilities, but constraints reduce the effective choice to 1-3 at each step.

**Catalog termination**: Whenever a sub-capability matches something in the catalog, the entire subtree below it collapses to a single lookup. The more complete the catalog, the shallower the effective tree. For a mature system with a rich capability library, most branches terminate within 2-3 levels. Deep exploration happens only in genuinely novel territory.

**Compositional independence**: Sub-capabilities at the same level are often largely independent. The authentication subsystem doesn't constrain the notification subsystem beyond shared interfaces. Independent subtrees can be explored in parallel, and their contributions to the search space multiply rather than creating complex joint distributions.

**Pattern reuse**: Decomposition patterns recur across problems. "Web application" almost always decomposes into authentication, routing, business logic, persistence, and presentation. "Data pipeline" almost always decomposes into ingestion, validation, transformation, storage, and monitoring. The learned policy captures these patterns, providing a strong prior that dramatically reduces effective search from scratch.

### 3.3 Comparative Dimensionality

| Search Space                 | Branching Factor        | Depth                | Structure                                                                             | Effective Tractability                                                        |
| ---------------------------- | ----------------------- | -------------------- | ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Assembly code (AlphaDev)     | 100-500 instructions    | 10-50 steps          | Flat sequence, correctness verified at end                                            | Tractable with RL + verification                                              |
| Proof search (AlphaProof)    | 50-200 tactics          | 10-100 steps         | Flat sequence with intermediate state verification                                    | Tractable with MCTS + RL                                                      |
| Natural language (LLMs)      | 50,000-100,000 tokens   | 100s-1000s of tokens | Sequential, statistically constrained                                                 | Tractable through massive pretraining                                         |
| **Capability decomposition** | **100s-1000s per node** | **5-15 levels**      | **Tree with constraint propagation, catalog termination, compositional independence** | **Significantly more tractable than all above due to structural constraints** |

The capability decomposition space is narrower and more structured than any of the comparison spaces. This validates the core intuition: working in a formally defined space with known building blocks and compositional verification creates a search problem that is tractable with existing ML techniques, without requiring the massive scale of general-purpose language models.

---

## 4. The Dimensionality of Learned Representations

### 4.1 Specification Embeddings

The learned representation of a capability specification — the vector encoding that the decomposition policy and catalog matcher operate on — requires an estimated few hundred to a few thousand dimensions. This is comparable to transformer embeddings in natural language (typically 768-4096 dimensions), but the information content per dimension is higher because specifications are more precise than natural language.

A specification embedding must capture:

- What types flow in and out (structural information)
- What properties are preserved or established (semantic information)
- What constraints apply (invariants, ordering, uniqueness, etc.)
- What performance characteristics are required (time, space, latency bounds)
- What domain the specification belongs to (compositional context)

### 4.2 Decomposition Policy Representation

The decomposition policy — the learned mapping from specification embeddings to candidate decompositions — is a conditional generative model. Given a parent specification embedding, it generates:

- A set of child specification embeddings
- A composition topology connecting them

Architecturally, this is a sequence-to-set model (generating a variable-sized set of children from a single input) combined with a graph generator (producing the topology). Both are well-studied architectures in ML. The set generation can use autoregressive decoding (generating children one at a time with attention to previously generated siblings) or parallel decoding with a fixed maximum and masking.

### 4.3 Value Network Representation

The value network takes a partial decomposition tree as input and outputs a scalar. The natural architecture is a graph neural network (GNN) with:

- Node features: specification embeddings for unresolved nodes, resolution status and match confidence for resolved nodes
- Edge features: composition type (sequential, parallel, conditional, etc.)
- Message passing: information propagates up and down the tree across multiple GNN layers
- Readout: aggregation at the root produces the value estimate

The GNN naturally handles variable-sized trees and can learn to identify patterns at any depth — a partially resolved subtree that "looks promising" or a dangling specification that "looks intractable."

---

## 5. The Training Loop

The complete training process ties all components together in a unified loop:

### Phase 1: Supervised Pretraining

- Extract capability decomposition trees from existing well-architected open-source software
- Train the decomposition policy to predict known decomposition structures
- Train the catalog matcher on (specification, implementation) pairs extracted from package managers and library documentation
- Train initial specification embeddings on formal contract data

### Phase 2: Self-Play Reinforcement Learning

1. Sample or generate a capability specification
2. The decomposition policy proposes a decomposition
3. The value network estimates the promise of the current partial tree
4. MCTS uses the policy and value network to explore alternative decompositions
5. Sub-capabilities are matched against the catalog
6. Matched branches terminate; unmatched branches recurse
7. At terminal leaves, the novelty synthesizer attempts implementation
8. Compositional verification checks each node against its children
9. The full outcome (success/failure, depth, efficiency) provides reward signal
10. All components are updated: the policy learns to propose better decompositions, the value network learns to estimate outcomes more accurately, the catalog matcher learns to retrieve more precisely

### Phase 3: Catalog Expansion

When the novelty synthesizer creates a verified implementation for a previously unsolved specification, this new capability is registered in the external catalog. The catalog grows over time. This creates a positive feedback loop: a richer catalog means more branches terminate early, which means the system can tackle harder problems, which means more novel capabilities are synthesized and added to the catalog.

This is the architectural realization of the philosophical insight that the walker modifies the terrain. Each solved problem enriches the landscape for future exploration.

---

## 6. Practical Considerations

### 6.1 Model Scale

Because the search space is more structured and constrained than general language modeling, the learned components can be significantly smaller than general-purpose LLMs:

- **Decomposition policy**: Estimated 1-7B parameters. The task is structured prediction over formal objects, not open-ended language generation. Smaller models suffice when the input and output spaces are well-defined.
- **Value network**: Estimated 100M-1B parameters. Value estimation is a regression problem over graph structures — far simpler than language generation.
- **Catalog matcher**: Estimated 100M-1B parameters. Embedding models for retrieval are typically smaller than generative models.
- **Novelty synthesizer**: Variable — could be small (100M-1B) for narrow synthesis tasks at well-specified leaves, or could leverage a larger model (7B+) for more complex synthesis.

Total model parameters across all components: roughly 1-10B, compared to 100B-1T+ for frontier general-purpose LLMs. This is consistent with the principle that a more structured space requires less brute-force statistical coverage.

### 6.2 Hardware Requirements

Given the model scales above, the complete system could plausibly run on:

- **Training**: A small cluster of 4-8 GPUs (A100/H100 class) for RL training. Significantly less compute than training a frontier LLM, because the search space is smaller and the verification signal is stronger (binary correct/incorrect rather than next-token probability).
- **Inference**: A single high-end consumer GPU (RTX 4090 / RTX 5090) for running the decomposition policy and value network. The catalog matcher could run on CPU with a vector database. The Lean/formal verification server runs on CPU.
- **Catalog storage**: A standard database (vector DB for embeddings + relational DB for metadata). Scales with the number of registered capabilities but doesn't require exotic hardware.

### 6.3 The Formalization Bridge

One component not yet discussed in detail: translating user input into formal capability specifications. Users will typically express intent in semi-formal language ("a function that takes X and returns Y such that Z"). This needs to be formalized into a machine-verifiable specification.

This is a fine-tuned language model task, similar to DeepSeek-Prover's autoformalization (translating informal math to Lean). The model would be trained on (semi-formal description, formal specification) pairs. This is the one component that most resembles a traditional LLM, because its input is natural language. It's also the component with the highest risk of error — a mistranslation of intent into specification means the system solves the wrong problem perfectly.

Mitigation: the formalized specification should be presented to the user for confirmation before exploration begins. This is analogous to how a contractor reviews the architectural drawings with the client before building. The specification is human-readable (unlike assembly code), so the user can verify that it captures their intent.

---

## 7. Summary: The Complete ML Architecture

```
USER INPUT (semi-formal description)
        │
        ▼
┌─────────────────────────┐
│  Formalization Bridge    │  Fine-tuned LM
│  (natural → formal)     │  ~1-7B params
└────────────┬────────────┘
             │ formal specification
             ▼
┌─────────────────────────┐     ┌──────────────────┐
│  Decomposition Policy    │◄───│  Value Network    │
│  (spec → sub-specs +    │    │  (partial tree →  │
│   composition graph)     │    │   success estimate)│
│  Transformer, RL-trained │    │  GNN, MCTS-trained│
│  ~1-7B params           │    │  ~100M-1B params  │
└────────────┬────────────┘     └──────────────────┘
             │ sub-capability specs
             ▼
┌─────────────────────────┐
│  Catalog Matcher         │
│  (spec → known impl?)   │
│  Contrastive embeddings  │
│  ~100M-1B params        │
└────────┬───────┬────────┘
         │       │
    MATCH ▼       ▼ NO MATCH
         │       │
    resolved     │
    (terminate)  ▼
         ┌───────────────┐
         │ Can decompose  │──YES──► RECURSE (back to
         │ further?       │         Decomposition Policy)
         └───────┬───────┘
                 │ NO
                 ▼
         ┌───────────────────┐
         │ Novelty Synthesizer│
         │ (narrow spec →     │
         │  verified code)    │
         │ RL program synthesis│
         │ ~100M-7B params    │
         └────────┬──────────┘
                  │ verified implementation
                  ▼
         ┌───────────────────┐
         │ Register in Catalog│ ◄── Catalog grows over time
         └───────────────────┘

VERIFICATION flows upward at every level:
  Does the composition of children satisfy the parent spec?
  If every node verifies, the complete tree is correct by construction.
```

---

## 8. What Makes This Tractable

The recurring theme across all aspects of this architecture is that **formality and structure reduce the learning problem**:

- The search space is narrower than general code generation because capabilities have formal contracts and the catalog eliminates solved branches
- The reward signal is stronger because verification is binary (correct/incorrect) rather than probabilistic (next-token likelihood)
- The representations are more compact because formal specifications carry more information per dimension than natural language
- The model sizes are smaller because structured prediction over formal objects requires less statistical coverage than open-ended language generation
- The training data is richer per example because each well-architected codebase is an implicit decomposition tree with verifiable relationships between levels

This is the engineering payoff of the philosophical insight that opened the conversation: a more structured space, explored with verification at every step, requires fundamentally less brute force than walking the fuzzy landscape of natural language. The formality isn't a constraint — it's what makes the problem solvable at human-accessible scale.

---

_This document specifies the machine learning foundations for the recursive capability decomposition architecture, detailing the learning components, search space characteristics, dimensionality analysis, and training processes that would enable a formally verified, hierarchical code exploration system._
