# Literature Synthesis: What the Research Landscape Reveals

## Connection to the Philosophical Foundation

The philosophical foundation (`philosophical_foundation.md`) makes three structural claims:

1. **The exploration mechanism is separable from the space being explored** — LLMs walk a semantic landscape, but the walker and the terrain are independent. A different terrain (formal mathematics) with the same walker (neural search) should yield different properties (no hallucinations, hard constraints).

2. **Some spaces must be walked to be known** — Gödel's incompleteness applied to AI: no model can predict the full structure of a sufficiently rich formal system without exploring it step by step.

3. **Build different functional machines for different regions** — Not one god machine, but specialized explorers for specific domains, each honest about where their map ends.

The FormalLlm project has been building exactly this: MCTS (exploration mechanism) over Lean's type theory (formal space) with an execution oracle (boundary of the map). The literature survey reveals that this architecture is now mainstream — and that the project's specific bottleneck (synthesis-specific training data) has been solved by several groups through bootstrapping techniques.

---

## Key Finding 1: The Architecture Is Right

The project's MCTS + Lean kernel + execution oracle mirrors the architecture used by every successful formal reasoning system in 2024-2025:

- **AlphaProof** (DeepMind, Nature 2025): AlphaZero-style RL + Lean formal verifier. IMO silver medal.
- **DeepSeek-Prover-V1.5** (ICLR 2025): RMaxTS (MCTS variant) + Lean feedback.
- **HyperTree Proof Search** (Meta, NeurIPS 2022): AlphaZero for proofs. Foundation paper.
- **Seed-Prover** (ByteDance, 2025): Agentic RL + Lean. 5/6 IMO 2025 problems.

All succeed because they combine neural exploration with formal verification at every step — the exact structure the philosophical foundation predicts should work. The project validated this at Sprint 11 (10/10 with FixedPolicy). The gap is not architecture.

## Key Finding 2: The Data Problem Has Known Solutions

Sprint 12 discovered that proof tactics ≠ synthesis expressions (domain mismatch: 0/10). The literature reveals multiple solutions to exactly this problem:

### AlphaVerus (ICML 2025)
Bootstraps verified code generation from **zero labeled data**. Three phases: exploration of candidates, Treefinement (tree search with verifier feedback), filtering misaligned specs. Each iteration collects new exemplars. LLaMA-3.1-70B generates verified code without human intervention. This is the closest analogue to the project's situation — replace "verified code" with "synthesis expressions" and the approach is identical.

### Goedel-Prover-V2 (2025)
An 8B model beats DeepSeek-Prover-V2-671B (80x larger) through three techniques: scaffolded data synthesis (generate problems of increasing difficulty), self-correction with Lean compiler feedback, and checkpoint averaging. This validates the single-GPU approach — the right data strategy beats raw scale.

### STP: Self-play Theorem Provers (ICML 2025)
The model simultaneously acts as conjecturer and prover. The conjecturer generates statements barely provable by the current prover, creating an automatic curriculum. Doubles LeanWorkbook solve rate (13.1% → 28.5%). This operationalizes the philosophical foundation's "some spaces must be walked to be known" — the space of solvable problems grows as the prover improves.

### DeepSeek-Prover V1 (2024)
Synthesizes 8 million formal statements with proofs through autoformalization. Achieves 50% on miniF2F. The key insight: you can generate your own training data at scale if you have a verifier.

## Key Finding 3: Autoregressive Failure Has a Theoretical Explanation

**"Faith and Fate: Limits of Transformers on Compositionality"** (NeurIPS 2023, Spotlight) proved that transformers solve compositional tasks by reducing multi-step reasoning to **linearized subgraph matching** — they never develop systematic problem-solving. Performance decays rapidly with task complexity.

This is the theoretical explanation for Sprint 10's finding: the model selects correct building blocks (30%) but can't wire them, because wiring requires tracking variable bindings across multiple steps simultaneously. Autoregressive linearization destroys this structure. MCTS preserves it because each step is verified independently.

**"Shattered Compositionality"** (2026) adds that transformers acquire compositional subskills in reverse or parallel order, causing "mixing errors" that persist even with scaling or scratchpad reasoning. The failure is fundamental, not addressable by more data or bigger models.

This validates the Sprint 11 pivot from autoregressive generation to MCTS — it was the architecturally correct response.

## Key Finding 4: Library Learning Is a Mirage

Two critical papers (Berlot-Attwell et al., 2024-2025) demonstrate that LEGO-Prover's "library learning" doesn't actually work — function reuse is extremely rare. Performance gains come from **self-correction and self-consistency**, not from building and reusing a library.

**Implication**: Sprint 13's expression enumeration should be treated as scaffolding for the learning signal, not as a permanent library. The value is in the self-play loop (generate → verify → retrain), not in the expressions themselves.

## Key Finding 5: Alternative Policy Architectures Exist

### HYSYNTH (NeurIPS 2024)
Uses LLM samples to learn a **probabilistic context-free grammar** that guides synthesis. Achieves 58% vs 6% from direct LLM sampling. The PCFG captures the structural domain (synthesis expressions) without requiring the LLM to be fine-tuned on domain-specific data.

### Type-Constrained Code Generation (PLDI 2025)
Enforces formal type system rules during LLM decoding via prefix automata. Ensures every partial program can always be completed to a well-typed program. Reduces compilation errors >50%. Directly applicable to constraining synthesis policy output.

### CARTS (ICLR 2025)
Improves MCTS for Lean by calibrating tactic diversity during expansion and introducing bias-resistant value functions. Addresses the problem that LLM-generated tactic probabilities are poorly calibrated for tree search.

## Key Finding 6: Mathematical Discovery Is Possible

### AlphaEvolve (DeepMind, 2025)
Broke Strassen's 50-year matrix multiplication record. Key insight: "hallucinations serve as beneficial mutations that add diversity." In formal domains with verifiers, noise IS exploration.

### Mathematical Exploration at Scale (Tao et al., 2025)
Applied AlphaEvolve to 67 open problems. Matched best-known results in ~75%, found provably better constructions in ~20%. Demonstrates that AI can do genuine mathematical exploration when paired with automated evaluation — the same architecture as MCTS + oracle.

### The Ramanujan Library (ICLR 2025)
Discovered 75 previously unknown connections between mathematical constants. Treats constants as a hypergraph and systematically enriches it. The exploration pattern (systematic enumeration + verification + discovery) mirrors the project's approach to program synthesis.

---

## Reading Priority for Sprint 13

| Priority | Paper | Why |
|---|---|---|
| 1 | AlphaVerus (ICML 2025) | Bootstraps verified code from zero data — the exact problem |
| 2 | Goedel-Prover-V2 (2025) | 8B beats 671B via scaffolded synthesis — validates single-GPU |
| 3 | STP: Self-play Provers (ICML 2025) | Conjecturer-prover self-play doubles solve rates |
| 4 | HYSYNTH (NeurIPS 2024) | PCFG from LLM samples — alternative policy architecture |
| 5 | Faith and Fate (NeurIPS 2023) | Theoretical grounding for autoregressive composition failure |
| 6 | Type-Constrained Code Gen (PLDI 2025) | Prefix automata for type-safe decoding |
| 7 | DeepSeek-Prover-V1.5 (ICLR 2025) | RMaxTS details — closest MCTS reference implementation |
| 8 | Library Learning Doesn't (2024) | Warning against over-investing in expression libraries |
