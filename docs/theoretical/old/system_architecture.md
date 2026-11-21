# Recursive Capability Decomposition: An Architecture for Formal Code Exploration

## From Language Models to Mathematical Machines to a New Paradigm

---

## 1. Starting Point: The Limitations of Language-Based Code Generation

Current AI code generation works by treating code as a subset of natural language. Large language models trained on text corpuses learn statistical patterns of code through word co-occurrence, generating code by predicting the most likely next token. This works surprisingly well because human language has spent thousands of years encoding the structure of reality — including computational logic — into text. Every tutorial, every Stack Overflow answer, every documentation page is a linguistic projection of computational structure.

But this approach inherits the fundamental limitations of language modeling. The system walks semantic space, not code space. It generates statistically plausible sequences of code-like text rather than navigating formally verified structure. This is why it can hallucinate — producing code that reads correctly but behaves incorrectly. The linguistic surface is smooth where the computational reality has sharp discontinuities.

---

## 2. The Mathematical Machine Insight

Mathematics offers a different substrate. Where language tokens are ambiguous and context-dependent, mathematical and logical symbols have rigid, formally defined semantics. The fundamental insight: if an LLM is a landscape explorer using language as a proxy for reality, could we build an equivalent explorer for mathematical or computational space, where the terrain is shaped by formal relationships rather than statistical ones?

This insight is supported by existing systems:

- **AlphaProof** explores formal proof space in the Lean language using reinforcement learning, with every step verified for logical correctness. It achieved silver-medal performance at the 2024 International Mathematical Olympiad.
- **AlphaDev** explores assembly instruction space, discovering sorting algorithms faster than any human-designed solution — algorithms now integrated into the C++ standard library.
- **AlphaEvolve** extends this to general algorithm discovery, evolving entire codebases in Python, C++, Verilog, and other languages.

The key architectural difference from LLMs: these systems don't predict the next likely token. They explore formally defined spaces using reinforcement learning, with exact verification at every step. In such spaces, hallucination is structurally impossible — every move either verifies or it doesn't.

---

## 3. What Exactly Is Being Learned?

A critical distinction: in a formal code/math explorer, the system does not need to learn what operations mean. The semantics are defined by the formal language (CPU architecture, proof assistant, programming language specification). What the system learns is **strategy** — which moves to make in which situations to reach a desired goal.

This learned strategy is a map of the landscape's contours: where the ridges are, where the valleys lead, which paths tend to be productive. It is learned search intuition, not learned semantics. This is fundamentally different from LLMs, which must learn both meaning and strategy simultaneously from statistical co-occurrence.

The implication: because the semantics are given and exact, the learning problem is narrower and more tractable. A 7-billion-parameter model can be competitive in formal theorem proving where hundreds of billions of parameters are needed for general language competence.

---

## 4. The Compositional Hierarchy

In a transformer-based LLM, raw tokens enter at the bottom layer, but intermediate layers form increasingly abstract internal representations — phrases, concepts, relationships, reasoning patterns. The depth of the network is the abstraction hierarchy.

The same principle should apply to code/math exploration. A system that only operates at the level of individual instructions or proof tactics is doing the equivalent of thinking one word at a time with no internal abstraction. What should emerge through training is a hierarchy of bundled operations at increasing levels of abstraction:

- **Primitives**: individual instructions or tactics (MOV, ADD, `intro`, `apply`)
- **Idioms**: short stereotyped sequences accomplishing micro-tasks (swap, bounds check, proof-by-contradiction setup)
- **Algorithms and strategies**: sorting, searching, induction with a specific invariant structure
- **Architectural patterns**: how components compose, how data flows, how proofs are structured
- **Design intuition**: the sense that a certain kind of approach is right for a certain kind of problem

The output of a mature explorer should not be a flat sequence of bottom-level moves. It should be a hierarchical structure — the solution expressed at its highest natural level of composition, with lower levels available as supporting detail and verification.

---

## 5. The Stable and Custom Distinction

A key observation about the code space: most of its building blocks are static and known.

**Algorithms** are catalogued. Sorting, searching, graph traversal, dynamic programming — these are solved problems. What matters is knowing which one to use and when.

**Data structures** are catalogued. Arrays, hash maps, trees, queues — these are stable features of the terrain.

**Design patterns** are catalogued. MVC, observer, factory, pipeline — these are named, documented, reusable.

**Libraries** encapsulate all of the above into callable units.

A program is a custom aggregate of these known elements. If a component isn't custom, it should be (and usually already is) a reusable library. The creative work — the space the explorer actually needs to walk — is the **composition space**: how to select, configure, and connect known building blocks to meet a specific, novel purpose.

---

## 6. Capabilities as the Fundamental Unit

The fundamental unit of code space, analogous to the token in language space, is the **capability** — an abstract functional unit defined by its input-output contract, not its implementation.

"Sort" is a capability. "Authenticate" is a capability. "Transform X to Y" is a capability. Each can be implemented multiple ways (that's where libraries live), but the explorer operates at the capability level, composing them into structures that satisfy a specification.

Capabilities differ from language tokens in a critical way: they have exact, formally defined semantics. A capability's contract specifies precisely what it accepts, what it produces, and what invariants it maintains. There is no ambiguity. This makes the composition space formally verifiable — you can check whether a composition of capabilities satisfies a higher-level requirement without running the program.

---

## 7. The Recursive Architecture

Capabilities are nested. A high-level capability decomposes into a composition of sub-capabilities. Each sub-capability is itself a specification that triggers the same exploration process. The recursion continues until every leaf node resolves to either a known implementation (stable terrain) or a primitive operation.

### The Process

1. **Receive a capability specification** — what must be achieved, defined by input-output contract and constraints
2. **Explore the composition space** — find a set of sub-capabilities whose composition satisfies the specification
3. **For each sub-capability, determine its status**:
   - **Known**: resolved immediately to an existing library or implementation (stable terrain)
   - **Composable**: can be assembled from known parts (wire them together)
   - **Novel**: requires further decomposition (recurse — this sub-capability becomes its own specification)
4. **Verify locally** — confirm that the composition of children actually satisfies the parent specification
5. **Return the verified composition** — a node in the growing tree

### Example

```
CAPABILITY: "expense tracking system for shared housing"
│
├── CAPABILITY: "user authentication and session management"
│   └── STATUS: KNOWN → resolved to authentication library
│
├── CAPABILITY: "expense recording and categorization"
│   ├── CAPABILITY: "input validation against schema"
│   │   └── STATUS: KNOWN → schema validation library
│   ├── CAPABILITY: "intelligent categorization"
│   │   └── STATUS: NOVEL → requires exploration
│   │       ├── CAPABILITY: "category lookup"
│   │       │   └── STATUS: KNOWN → lookup table
│   │       └── CAPABILITY: "fuzzy matching for ambiguous inputs"
│   │           └── STATUS: KNOWN → fuzzy matching library
│   └── CAPABILITY: "persistent storage with transactional guarantees"
│       └── STATUS: KNOWN → database adapter
│
├── CAPABILITY: "equitable cost splitting"
│   └── STATUS: NOVEL → requires exploration
│       ├── CAPABILITY: "proportional arithmetic"
│       │   └── STATUS: KNOWN → arithmetic primitives
│       ├── CAPABILITY: "fairness constraint satisfaction"
│       │   └── STATUS: NOVEL → requires exploration
│       │       └── ... (recursion continues)
│       └── CAPABILITY: "historical balance tracking"
│           └── STATUS: COMPOSABLE → database query + aggregation
│
└── CAPABILITY: "event-driven notification"
    └── STATUS: KNOWN → messaging library
```

### Properties of this Architecture

**Self-similar**: the same exploration process operates at every level of the recursion. One explorer, one process, applied recursively at different granularities.

**Self-prompting**: the top-level prompt comes from the user. Every subsequent prompt is generated by the system itself — each unsolved capability becomes the specification for the next level of exploration.

**Locally verifiable**: each node is verified against its children. Does this specific composition of sub-capabilities satisfy this specific capability's contract? If every node passes, the entire tree is correct by construction. This is compositional verification — massively simpler than verifying a flat program.

**Multi-level readable**: the tree can be read at any level of abstraction. The top level reveals architecture. One level down reveals major design decisions. Further levels reveal implementation choices. The complete tree is the solution expressed simultaneously at every level of composition.

**Implementation-agnostic**: the tree describes what capabilities are needed and how they compose, not what language they're written in. The same composition tree could be realized in Python, Java, Rust, or as distributed microservices. Language-specific implementation is a separate, simpler step — matching each leaf node to a concrete library call.

---

## 8. The Explorer's Role

The explorer — whether RL-based, evolutionary, or hybrid — operates within this recursive framework. At each node, its job is to find a composition of sub-capabilities that satisfies the parent specification. Its learned strategy (the map of the landscape's contours) tells it which decompositions tend to be productive for which kinds of specifications.

The known terrain — algorithms, data structures, patterns, libraries — is pre-loaded. The explorer doesn't rediscover sorting. It knows sorting exists as a capability with defined properties and reaches for it when the specification calls for ordered output.

The novel territory — the custom composition — is where the exploration happens. The explorer's value is in the same place a good software architect's value lies: not in knowing what a database is, but in knowing which database, connected how, to what, for this particular set of constraints.

---

## 9. The Prompt

In this architecture, the prompt is simply the top-level capability specification: what goes in, what should come out, what constraints must hold, what properties must be maintained. It does not specify how to solve the problem — that's what the explorer discovers. It specifies what "solved" looks like.

For practical use, users would not need to write formal specifications from scratch. A realistic prompt interface would accept semi-formal descriptions: "a function that takes a list of expenses with amounts and participants, and returns a mapping from each participant to how much they owe or are owed, such that all debts are settled with the minimum number of transactions."

This is more constrained than pure natural language ("build me an expense tracker") but far less formal than a mathematical specification. The system's first recursive step would be to decompose this semi-formal description into precise sub-capability specifications — an automated formalization step that bridges human intent and machine-verifiable structure.

---

## 10. What Exists and What Remains to Be Built

### Components that exist today

- **Formal exploration with verification**: AlphaProof, DeepSeek-Prover-V2, and related systems demonstrate RL-based exploration of formally verified spaces
- **High-level code evolution**: AlphaEvolve and OpenEvolve evolve programs in Python, C++, and other languages using evolutionary search with automated evaluation
- **Hierarchical task decomposition**: AI planning systems and agentic coding tools decompose complex tasks into sub-tasks
- **Compositional verification**: Proof assistants (Lean, Coq) support lemma-based hierarchical proof construction
- **Capability-based design**: Software architecture already thinks in terms of abstract capabilities, interfaces, and contracts

### What doesn't yet exist

The **unified system** that combines recursive capability decomposition, formal compositional verification, learned exploration strategy, and implementation-agnostic output into a single self-similar process operating at every level of the hierarchy. No current system integrates all of these principles into the architecture described here.

---

## 11. Philosophical Grounding

This architecture rests on several deeper principles explored throughout the conversation that generated it:

**Language as a proxy for reality**: LLMs work because language has encoded enough of reality's structure to serve as a navigable map. But language is a lossy compression. Formal systems are less lossy in their domains. A system working directly in a formally defined capability space operates with a better map.

**Functional machines, not god machines**: No model of reality can be complete. The goal is not omniscience but reliable function within well-defined boundaries. This architecture is explicitly a functional machine — it works within the space of composable, verifiable capabilities and is honest about where that space ends.

**The space must be walked to be known**: Some problems require exploration, not just deduction. The recursive architecture embraces this: it walks the composition space at every level, discovering structure that couldn't have been predicted without the walk itself.

**The walker modifies the terrain**: When the explorer discovers a novel composition and names it as a reusable capability, it adds new stable terrain to the landscape. New paths exist that didn't before. The system's output enriches the system's future input. This is the emergent, processual nature of reality showing up as a concrete architectural feature.

---

_This document synthesizes ideas from a conversation exploring the intersection of large language models, formal mathematics, evolutionary computation, software architecture, and the philosophy of knowledge — beginning with a question about Gödel numbering and arriving at an architecture for recursive, verifiable, capability-based code exploration._
