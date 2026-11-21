# Compute Economics: Navigator vs. Frontier LLMs

*Written 2026-02-23. Analyzes the inference compute requirements of the type-directed navigator compared to frontier language models for code generation.*

---

## Frontier LLM Inference Cost

To generate a single function (~200 tokens), a frontier LLM does:

- **200 forward passes** through a 100B-1T+ parameter model
- Each pass: matrix multiplications across the full parameter count
- Hardware: 4-8 H100 GPUs minimum, typically more with KV-cache and batching overhead
- Multiple generations + filtering for reliability (pass@10 at minimum), so multiply by 5-10x
- Energy: measurable in kilowatt-hours per session
- Latency: seconds to tens of seconds per function

And this is for a single function. A system with 500 functions means 500 of these. The LLM also generates plenty of wrong code that gets discarded — the verification happens *after* generation, so all the compute spent on wrong candidates is wasted.

The cost per token stays constant or increases as models get larger. Better models are bigger models. The scaling curve works against you at inference time.

---

## Navigator Inference Cost

The navigator's compute profile is fundamentally different because it decomposes the problem into components with radically different compute requirements.

### The Prior (Neural Component)

This is the only GPU-dependent part. But it solves a much simpler problem than an LLM. The LLM must simultaneously learn syntax, types, semantics, user intent, natural language, and code patterns across all languages. The navigator's prior learns only one thing: *among the type-valid operations at this step, which one is most useful?*

Everything else is handled by non-neural components. This means the prior can be small. Not 100B parameters — more like 10M-100M. The current sprint uses 450K parameters and already learns the structural grammar fluently. A production prior that handles the full diversity of Haskell might need 100x more, but that is still 45M parameters — three orders of magnitude smaller than a frontier LLM.

A 50M parameter transformer runs inference on a single consumer GPU in microseconds per forward pass. It could likely run on CPU alone.

### The Type Checker

CPU only. Microseconds per check. GHC's type checker handles complex polymorphic types in well under a millisecond. This runs at every search step and contributes negligibly to total compute.

### The Search

A typical function synthesis might involve 10-50 hole-filling steps. With a beam width of 32, that is 320-1600 forward passes through the prior. But each forward pass is through a 50M parameter model, not a 500B parameter model. The total FLOPs for 1600 forward passes through 50M parameters is comparable to about *two tokens* of a frontier LLM.

### The Execution Oracle

runhaskell or compiled execution. Milliseconds per candidate. CPU only. Testing 10-50 candidates per function takes a few hundred milliseconds total.

### The Library Lookup

This is the key. Type-indexed lookup: hash or tree index, O(1) or O(log n). Milliseconds. No GPU. And as the library grows, *this becomes the dominant operation*. Most typed holes match existing verified functions. The prior, the search, everything is skipped. Just look up the answer.

---

## The Compute Curve

### LLMs Have Anti-Scaling at Inference

Better models are bigger. Bigger models cost more to run. The cost of generating code goes up as the models improve. GPT-5 is more expensive to run than GPT-4. GPT-6 will be more expensive still. You are on an escalator going up.

### The Navigator Has Pro-Scaling at Inference

More use means a bigger library. A bigger library means less search. Less search means less compute. The cost of generating code goes *down* as the system is used more. You are on an escalator going down.

---

## Projected Cost Distribution

For a mature navigator with a large library (100,000+ verified functions accumulated over months of use):

| Synthesis task | Fraction | Compute |
| --- | --- | --- |
| Library lookup (exact type match) | ~60% | Milliseconds, CPU only |
| Shallow search (1-5 hole-filling steps) | ~25% | Tens of milliseconds, one small GPU pass |
| Deep search (10-50 steps, beam search) | ~12% | Seconds, multiple small GPU passes |
| Novel synthesis (50+ steps, backtracking) | ~3% | Tens of seconds, sustained GPU |

The weighted average compute per function drops as the library grows. The 60% that are library lookups cost essentially nothing. Only the 3% of genuinely novel compositions require serious compute.

---

## Hardware Projection

A fully realized navigator — Phase 6, with a mature library, production-grade prior, type-directed constrained decoding, MCTS search — runs on **a single workstation**. One consumer GPU (RTX 4090-class, not datacenter hardware), 64GB RAM for the library index, standard CPU for type checking and execution.

For the common case (library lookup + shallow search), it runs on a **laptop without a GPU**. The prior at 50M parameters runs on CPU in acceptable time when you only need a few forward passes per function.

The datacenter is needed only for *training* the prior — and even that is modest. Training a 50M parameter model on a corpus of a few million typed programs takes hours on a single GPU, not weeks on a cluster.

Compare: frontier LLM training costs hundreds of millions of dollars and months on thousands of GPUs. Inference requires dedicated datacenter hardware per user. The navigator's training costs thousands of dollars and its inference runs on hardware the developer already owns.

---

## The Amortization Advantage

The deepest compute advantage is not that the navigator is smaller. It is that the navigator's work *accumulates*.

Every function the navigator synthesizes and verifies is added to the library permanently. That function never needs to be synthesized again — not by this user, not by any user. The compute spent on synthesis is amortized across every future use of that function.

An LLM generates the same `map` function from scratch every time someone asks for it. It spends the same compute on the millionth generation as on the first. There is no accumulation. Every inference is independent.

The navigator synthesizes `map` once, verifies it, adds it to the library. From then on, every request for a function whose type matches `(a -> b) -> [a] -> [b]` is a lookup. The compute cost drops to zero for that function class, forever.

Over time, the library converges toward comprehensive coverage of common type patterns. The navigator becomes a *lookup engine* for most requests and a *search engine* only for genuinely novel compositions. The compute per function trends toward the cost of a hash table lookup — effectively zero.

---

## Research Landscape and Novelty

### Type-Directed Hole Filling Is Not Novel

The idea has decades of research history:

**Classical program synthesis (1980s-2010s).** Exhaustive search over typed programs. MagicHaskeller (Katayama, 2007) enumerates Haskell functions by type. Djinn (Augustsson, 2005) generates functions from type signatures using the Curry-Howard correspondence directly — it treats types as propositions and synthesizes proofs. Hoogle searches Hackage by type signature. These all work, but do not scale — exhaustive enumeration hits combinatorial walls quickly.

**Refinement-type synthesis (2010s).** Synquid (Polikarpova et al., 2016) and Myth (Osera & Zdancewic, 2015) use refinement types and input-output examples to guide synthesis. They fill typed holes top-down, using the type + examples to prune the search. Leon/Stainless does similar things for Scala. These scale better than exhaustive search but are still limited to small functions with precise specifications.

**Neural-guided synthesis (2020s).** DreamCoder (Ellis et al., 2021) learns a neural prior over programs and a library of reusable components, alternating between synthesis and library learning. LILO (Grand et al., 2023) extends this with LLM-guided library learning. AlphaCode (DeepMind, 2022) uses transformers for competitive programming but without type-directed search — it is pure generation + filtering.

**AlphaProof (DeepMind, 2024).** The closest analog to the end-state vision. Transformer policy network + MCTS, navigating Lean's proof space with Lean's kernel as the verification oracle. It solved IMO problems. This is exactly the machine described in `navigating_formal_space.md`, restricted to the proof domain.

**Proof assistant tactics (ongoing).** Lean, Coq, Agda, and Idris all have tactic-based hole filling where the system proposes completions for typed holes. Lean's `aesop` tactic is essentially learned search over proof steps. Wingman (Haskell IDE plugin) does GHC-powered hole filling for Haskell specifically.

### What Is Novel About This Project

Not the individual techniques. The combination and the specific bet:

**1. Real-world extraction at scale.** Most synthesis research uses toy languages or curated benchmarks. This project extracts from real GHC Core — the actual intermediate representation of production Haskell. The corpus of 4,438 functions from 137 Hackage packages is messy, diverse, and representative of real code.

**2. De Bruijn canonical form as the learned representation.** Most neural code models operate on source text or lightly-processed ASTs with named variables. De Bruijn indices canonicalize the representation, eliminating alpha-equivalence noise. This is a specific bet: that canonical representation + smaller vocabulary = better learning.

**3. Standard architecture with the right conditioning.** Most neural synthesis work that goes beyond flat generation uses specialized architectures (graph neural networks, tree-RNNs, pointer networks). This project bets that a standard causal transformer with type conditioning and type constraints in the decode loop is sufficient. A simpler, more scalable bet.

**4. Bottom-up creation path.** Most synthesis systems are designed top-down — someone builds the full architecture and then tries to make it work. This project builds bottom-up — validate each layer independently (representation, prior, conditioning, constraints, search) before composing them.

### The Current Industry Trajectory

The industry is overwhelmingly betting on scale: bigger LLMs, more training data, longer context windows, better RLHF. The thesis is that sufficient scale makes formal methods unnecessary — if the model is good enough at predicting code tokens, it will generate correct code without type-directed search or formal verification.

- **OpenAI / Anthropic / Google:** Pure LLM scaling. Correctness is statistical.
- **Cursor / GitHub Copilot / etc.:** LLM wrappers with IDE integration. Better UX around the same approach.
- **DeepMind:** The one lab with a foot in both worlds. AlphaProof uses formal methods. AlphaCode uses pure generation. No published unification.
- **Academic synthesis community:** Still active (Synquid, DreamCoder, Lean/Coq automation) but small compared to the LLM wave.

The industry consensus: formal methods are too rigid, too domain-specific, and too slow. Just make the LLM bigger.

---

## Navigator vs. Larger LLMs: Structural Advantages

The argument against "just scale the LLM" is not about capability — LLMs will get better at generating code. It is about four structural properties that do not improve with scale:

### 1. Guarantees vs. Statistics

An LLM generates code that is *probably* correct. The probability improves with scale but never reaches 1.0. For a 100-line function, even 99% per-line accuracy gives ~37% chance of a fully correct function. For a 10,000-line system, the probability of full correctness is vanishingly small.

The navigator generates code that is *verified* correct — type-checked at every step, tested at completion. The guarantee is structural, not statistical. It does not improve with scale because it is already 1.0 for the properties that are checked.

This matters increasingly as systems get more critical. A code suggestion in an IDE can be statistically good enough. A component in a medical device or financial system cannot.

### 2. Compute Economics

LLMs scale by adding parameters. Going from GPT-4 to GPT-5 costs billions. The improvement is one-time and applies uniformly to all problems — simple functions and complex systems get the same model.

The navigator scales two ways, both cheaper:

- **Search time** (per-problem): harder problems get more compute. Simple functions are instant lookups. Complex compositions get deeper search. Compute is allocated proportionally to difficulty.
- **Library growth** (accumulated): every verified function is a permanent building block. The library grows monotonically with use. Over time, most synthesis tasks become library lookups, and only genuinely novel compositions require real search. The system gets faster as it works more, at zero additional training cost.

This is the AlphaProof scaling property: more search time = better results. LLMs do not have this — generating the same prompt twice gives statistically independent results, not improved ones.

### 3. Compositional Correctness

LLMs have well-documented failures on compositional generalization. They can generate `sort` and they can generate `filter`, but generating `sort . filter (> 0)` is harder than either alone, in a way that does not reliably improve with scale. Each composition point is a new opportunity for error.

The type system makes composition free. If `sort :: Ord a => [a] -> [a]` and `filter :: (a -> Bool) -> [a] -> [a]` are both correct, `sort . filter (> 0)` is correct by construction. The navigator gets compositional correctness from the type system, not from the model. This advantage is permanent — it does not erode as LLMs improve, because it is structural rather than statistical.

### 4. Interpretability and Trust

The navigator's decisions are traceable. At each step: what type hole it is filling, what operations were valid, which one it chose, and why (the prior's ranking). If the output is wrong, you can trace back to the exact decision point where the wrong operation was chosen.

LLMs are opaque. If a generated function is wrong, you cannot trace why. You can only generate again and hope for better luck. As systems get more complex and more critical, the ability to audit the generation process becomes essential.

---

## The Honest Counter-Argument

The navigator is domain-specific. It works for typed functional languages (Haskell, ML, Lean, Agda). It does not work for Python, JavaScript, C, or any language without a rich type system. The majority of the world's code is written in languages where the type system is too weak to provide meaningful search constraints.

LLMs work for everything. They generate Python, JavaScript, Rust, Haskell, SQL, HTML, shell scripts, configuration files. Their generality is their strength. A single model handles the entire stack. The navigator handles one layer of one paradigm.

The counter-counter-argument: the navigator does not need to replace LLMs for all code. It needs to be better than LLMs *in the domain where formal methods apply*. If you are writing Haskell, Lean, or any language with a strong type system, the navigator should produce more reliable code with less compute. The LLM handles everything else. Different functional machines for different regions of the landscape — exactly what `philosophical_foundation.md` prescribes.

The other honest limitation: the navigator as described does not exist yet. LLMs work now, at scale, today. The navigator is a research program. Sprint 5 is testing whether types work as coordinates. The full machine is years away. In the meantime, LLMs ship products.

---

## The Precondition

The entire economic argument rests on the type system being an effective search space pruner. If type information does not constrain generation meaningfully, the prior needs to do more work, which means it needs to be bigger, which erodes the compute advantage.

Sprint 5 tests this precondition: does P(body | type) >> P(body)? If type conditioning actually constrains the output distribution, the navigator's economic model holds. If it does not, the structural advantage over LLMs is theoretical rather than practical.

That is why Sprint 5 matters beyond the immediate benchmark numbers. It is not just "do we pass more tasks." It is "does the structural property of formal space that makes the navigator economically viable actually hold in practice."

---

*This document analyzes the compute economics of the type-directed navigator, compares it to frontier LLMs, surveys the research landscape for novelty, and identifies the structural advantages and limitations of the formal-space approach.*
