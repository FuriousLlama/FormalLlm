# Research Directions: Beyond Sprint 13

Informed by the literature synthesis and the philosophical foundation.

---

## The Central Thesis

The philosophical foundation claims that the exploration mechanism is separable from the space being explored, and that formal mathematical spaces — having hard structural constraints — eliminate the hallucination problem that plagues language-based exploration.

Twelve sprints of empirical work have confirmed this:
- Sprints 0-10 proved that autoregressive generation over GHC Core hits a compositional ceiling (transformers linearize multi-step reasoning — "Faith and Fate", NeurIPS 2023)
- Sprint 11 proved that MCTS over Lean's type theory preserves compositional structure through per-step verification
- Sprint 12 proved that the exploration mechanism (MCTS) is architecture-complete but the training data domain matters critically

The project's architecture — MCTS + formal verifier + execution oracle — is the same architecture that achieved IMO silver (AlphaProof), state-of-the-art on miniF2F (DeepSeek-Prover, Goedel-Prover, Seed-Prover), and verified code generation from zero data (AlphaVerus). The project is on the right track.

What follows are three research directions, ordered from near-term concrete to long-term visionary. Each builds on the previous.

---

## Direction A: Self-Play Synthesis (Sprint 13-14)

### The Idea

Combine Sprint 13's expression enumeration with the self-play pattern from STP (ICML 2025) and the bootstrapping pattern from AlphaVerus (ICML 2025):

1. **Enumerate** typed expressions by composing Lean library functions → create synthesis tasks
2. **Solve** with expanded policy + MCTS + oracle → collect (state, exact_expr) training data
3. **Conjecture**: generate new synthesis tasks at the frontier of current ability (STP pattern)
4. **Retrain** policy on all collected data
5. **Iterate** until convergence

This is AlphaZero for program synthesis, with Lean's kernel as the game rules.

### Why It Should Work

- **Goedel-Prover-V2** proved that scaffolded data synthesis + self-correction beats 80x larger models. Our expression enumeration (Level 0 → 1 → 2) is scaffolded synthesis.
- **AlphaVerus** bootstrapped verified code from zero labeled data using tree search + verifier. Our MCTS + Lean kernel is structurally identical.
- **STP** doubled solve rates through self-play conjecturing. Adding a conjecturer to Sprint 13's loop should accelerate convergence.
- **"Library Learning Doesn't"** warns that reuse is a mirage — the gains come from self-correction. Sprint 13 should focus on the loop, not the library.

### Concrete Experiments

**Experiment A1: Enumeration Baseline**
- Hypothesis: A policy trained on 5K+ enumerated (goal, `exact <expr>`) pairs will propose synthesis-relevant expressions (>30% valid tactic rate on benchmark).
- Minimum: Write enumerate.py, generate expressions, validate with Lean, train LoRA.
- Success: >=3/10 T2 tasks solved.
- Failure learning: If 0/10, the enumerated expressions are too templated — need more structural diversity.
- Estimated time: 2 days.

**Experiment A2: Self-Play Iteration**
- Hypothesis: Each self-play round solves >=1 additional task as policy coverage grows.
- Minimum: Run MCTS with trained policy, collect new (state, tactic) pairs from solved tasks, retrain, evaluate.
- Success: Round 2 solves more tasks than Round 1.
- Failure learning: If no improvement, the policy isn't generating enough diversity — need to add noise/temperature variation.
- Estimated time: 1 day per round.

**Experiment A3: Conjecturer-Prover Self-Play (STP-inspired)**
- Hypothesis: Generating synthesis tasks at the policy's frontier (barely solvable) provides a more effective curriculum than random enumeration.
- Minimum: Train a task generator that produces synthesis goals with solutions the current policy can find ~10-30% of the time. Use these as training tasks.
- Success: Faster convergence than uniform enumeration (fewer rounds to same solve rate).
- Failure learning: If no improvement, the frontier isn't well-defined for synthesis (unlike proofs, synthesis tasks may not have a smooth difficulty gradient).
- Estimated time: 3 days.

### Key Design Decisions

- **Use GRPO instead of PPO for RL** (DeepSeek-Prover-V1.5): no separate critic model needed, fits single GPU
- **Checkpoint averaging** (Goedel-Prover-V2): merge 2-3 checkpoints from different training stages for exploration diversity at zero compute cost
- **Self-correction with Lean feedback** (Goedel-Prover-V2): when MCTS expansion fails, feed the error message back to the policy for a corrected attempt (Baldur's repair pattern)
- **Don't build a persistent library** (Library Learning Doesn't): expressions are training scaffolding, not a reusable artifact

---

## Direction B: Grammar-Guided Synthesis (Sprint 14-15)

### The Idea

Replace the LLM-as-policy with a typed probabilistic grammar, inspired by HYSYNTH (NeurIPS 2024) and Type-Constrained Code Generation (PLDI 2025).

Instead of fine-tuning Qwen to generate expressions directly:
1. Define a **typed context-free grammar** over Lean synthesis expressions
2. Use the LLM to sample candidate expressions, then **learn production probabilities** (PCFG) from the samples
3. MCTS expands by **sampling from the typed PCFG**, not from the LLM
4. The grammar grows as new expressions are discovered through self-play
5. **Type constraints** (prefix automata) guarantee every partial expression is completable to a well-typed program

### Why This Is Better Than LLM-as-Policy

| Property | LLM Policy (Sprint 12-13) | Grammar Policy (Direction B) |
|---|---|---|
| Domain match | Must be trained on right domain | Grammar IS the domain by construction |
| Inference speed | ~100ms per forward pass | <1ms per grammar sample |
| Syntactic validity | Not guaranteed | Guaranteed by grammar |
| Type safety | Requires type-checker post-hoc | Enforced during generation (prefix automata) |
| Interpretability | Opaque weights | Readable production rules |
| Composability | Fixed vocabulary | Grammar grows with new discoveries |

### The Grammar

A synthesis expression grammar for Lean `List Int → T`:

```
<expr>     ::= <base> | <compose>
<base>     ::= xs | xs.<method> | <func> xs
<compose>  ::= (<expr>).<method> | <func> (<expr>)
<method>   ::= filter <pred> | map <transform> | foldl <binop> <init>
             | reverse | eraseDups | length | takeWhile <pred>
             | dropWhile <pred> | scanl <binop> <init>
<pred>     ::= (fun x => decide (<cmp>))
<cmp>      ::= x <relop> <int> | x % <int> <relop> <int>
<transform>::= (fun x => <arith>)
<arith>    ::= x <binop> <int> | x <binop> x | <unary> x
<binop>    ::= + | * | - | max | min
<unary>    ::= Int.natAbs | (- ·)
<relop>    ::= > | < | ≥ | ≤ | = | ≠
<init>     ::= 0 | 1
<int>      ::= 0 | 1 | 2
<func>     ::= List.mergeSort | List.flatten | List.zipWith <binop>
```

This grammar generates exactly the kind of expressions the FixedPolicy contains — but parameterically, with learned probabilities over productions.

### How to Learn the Probabilities

1. **Seed**: Use the successful expressions from Sprint 13's self-play as positive examples
2. **Parse**: Map each expression to its grammar derivation tree
3. **Count**: Estimate production probabilities by counting derivation frequencies
4. **Smooth**: Apply Laplace smoothing to ensure every production has non-zero probability
5. **Refine**: As self-play discovers new expressions, update counts and re-estimate

This is simpler than training an LLM and can be done on CPU.

### Concrete Experiment

**Experiment B1: Grammar vs. LLM Policy**
- Hypothesis: A typed PCFG policy achieves >=80% of the LLM policy's solve rate at 100x lower inference cost.
- Minimum: Implement the grammar, parse Sprint 13's successful expressions into derivations, learn probabilities, plug into MCTS.
- Success: >=3/10 T2 with grammar policy.
- Failure learning: If 0/10, the grammar is too restrictive (need to add more productions) or the probabilities are poorly estimated (need more data).
- Estimated time: 3 days.

### Extensions

- **Type-directed sampling**: Use Lean's expected return type to mask invalid productions at each step (prefix automata from PLDI 2025)
- **Grammar evolution**: When self-play discovers an expression not in the grammar, automatically extract a new production rule and add it
- **Hierarchical grammar**: Productions can reference previously discovered "macros" (e.g., `<count_pred> ::= (<expr>.filter <pred>).length` once this pattern is common)
- **Hybrid policy**: Use the grammar for exploration (high diversity, guaranteed validity) and the LLM for exploitation (high probability of correct expressions for known task patterns)

---

## Direction C: Lean as a Mathematical Discovery Engine (Sprint 15+)

### The Idea

The philosophical foundation's deepest claim: "build different functional machines for different regions of the landscape." Directions A and B build a functional machine for *synthesizing known programs*. Direction C points this machine at *discovering new programs with interesting properties*.

Once the synthesis policy reliably composes library functions (Directions A-B), the same infrastructure can search for programs that satisfy novel specifications:

1. **Property-driven synthesis**: "Find a function `f : List Int → List Int` such that `f (f xs) = xs` for all `xs`" (involutions)
2. **Optimization synthesis**: "Find a function `f : List Int → Int` that is maximized when the input is sorted" (sortedness measures)
3. **Conjecture synthesis**: "Find a function `f : List Int → Nat` such that `f xs ≤ xs.length` for all `xs` and `f xs = xs.length` when `xs` has no duplicates" (discovering that `eraseDups.length` satisfies this)

The Lean kernel verifies properties. The oracle checks examples. MCTS explores. The policy proposes.

### Connection to Mathematical Discovery Literature

- **AlphaEvolve** (DeepMind, 2025): Discovered new algorithms (matrix multiplication, sorting). Same pattern: generate + verify + evolve. Their insight: "hallucinations serve as beneficial mutations."
- **Ramanujan Library** (ICLR 2025): Discovered 75 unknown connections between constants. Same pattern: systematic enumeration + verification.
- **TxGraffiti** (2024): AI-generated graph theory conjectures that became published theorems. Same pattern: generate conjectures + verify/disprove.
- **Mathematical Exploration at Scale** (Tao et al., 2025): AlphaEvolve applied to 67 open problems. Found better solutions for ~20%.

All use the same fundamental loop: **generate candidates in a formal space → verify with a formal system → keep discoveries → iterate.**

### Why This Is the Big Vision

The philosophical foundation argues that reality has regions best explored by different functional machines. Program synthesis over typed data structures is one such region. The programs we synthesize are not just code — they are **computable mathematical objects** with formal properties.

A system that reliably discovers programs satisfying novel specifications is a system that does mathematics — specifically, constructive mathematics, where existence proofs are programs. Lean's type theory (the Calculus of Inductive Constructions) is explicitly designed for this: types are propositions, programs are proofs, synthesis is theorem proving.

The project started by asking "can a small model learn to synthesize programs?" and has been narrowing the answer. Direction C flips the question: "what can a small model discover by synthesizing programs in a verified formal space?"

### Concrete First Step

**Experiment C1: Property-Constrained Synthesis**
- Hypothesis: MCTS + synthesis policy can find programs satisfying a formal property specification, not just matching test cases.
- Minimum: Define 5 property specifications in Lean (e.g., `∀ xs, f (f xs) = xs`). Run MCTS searching for `f` that satisfies both the property AND test cases.
- Success: >=2/5 properties satisfied with a discovered function.
- Failure learning: If 0/5, the synthesis policy doesn't generate diverse enough expressions to hit the constrained solution space.
- Estimated time: 2 days (property specs + MCTS run).

---

## Summary: The Three Horizons

| Horizon | Direction | Question | Key Papers | Sprint |
|---|---|---|---|---|
| Near | A: Self-Play Synthesis | Can bootstrapped data close the domain gap? | AlphaVerus, Goedel-V2, STP | 13-14 |
| Medium | B: Grammar-Guided Synthesis | Can a typed grammar replace the LLM policy? | HYSYNTH, Type-Constrained CG | 14-15 |
| Far | C: Discovery Engine | Can verified synthesis discover new mathematics? | AlphaEvolve, Ramanujan Library | 15+ |

Each direction builds on the previous:
- **A** generates the data and validates the loop
- **B** extracts the structure (grammar) from A's data and makes it fast/reliable
- **C** points B's reliable synthesis at open-ended discovery

The infrastructure (MCTS + Lean + oracle) is shared across all three. The investment in Sprint 11-13 infrastructure pays off across the entire research program.

---

## Risks and Contingencies

### If Direction A fails (0/10 with synthesis data)
- **Diagnosis**: Are the enumerated expressions too templated? Not enough structural diversity?
- **Contingency 1**: Use a larger base model (Llemma-7B instead of Qwen 1.5B) — more mathematical pretraining may help with expression structure
- **Contingency 2**: Try few-shot prompted policy with synthesis examples in context — bypass fine-tuning entirely
- **Contingency 3**: Try HYSYNTH's PCFG approach (Direction B) directly — the grammar might succeed where the LLM fails

### If Direction B fails (grammar too restrictive)
- **Diagnosis**: Is the grammar too narrow? Does it cover the benchmark task solutions?
- **Contingency**: Use a hybrid policy (grammar for structure, LLM for filling in parameters). The grammar ensures the right function is called; the LLM chooses the predicate/transform/init.

### If Direction C is premature
- **Diagnosis**: Synthesis reliability isn't high enough for open-ended search
- **Contingency**: Focus on expanding the benchmark to 50-100 tasks and achieving 80%+ solve rates before attempting discovery. Reliability before creativity.

### Compute scaling
- Currently single RTX 5090. If the approach works:
  - **Moderate scaling**: 2-4 GPUs would enable parallel MCTS + training. Available via consumer hardware.
  - **Major scaling**: Cloud GPUs for larger models (7B-14B full fine-tune). Only justified after Direction A validates the approach.
  - The project's value is in the approach, not the scale. Goedel-Prover-V2 proved that 8B beats 671B with the right data strategy. Stay small until the approach is validated.
