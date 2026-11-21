# Research Papers Reference

Papers organized by topic. Each entry includes relevance to the FormalLlm project.

---

## Self-Play and Bootstrapping in Formal Domains

### AlphaProof: Olympiad-level Formal Mathematical Reasoning with Reinforcement Learning
- **Authors**: Google DeepMind
- **Venue**: Nature 2025 (system demonstrated at IMO July 2024)
- **URL**: https://www.nature.com/articles/s41586-025-09833-y
- **Key idea**: AlphaZero-inspired RL agent that learns formal proofs in Lean. Trained on 80 million auto-formalized statements. Uses "Test-Time RL" — generating and learning from millions of related problem variants at inference time. Solved 4/6 IMO 2024 problems (silver medal, 28/42 points).
- **Relevance**: Gold standard for MCTS + formal verifier. Architecture matches ours (MCTS + Lean + oracle). Their "Test-Time RL" (generating problem variants at inference) is conceptually similar to Sprint 13's task generation. Key difference: they had massive compute; we need to replicate the pattern on single GPU.

### STP: Self-play LLM Theorem Provers with Iterative Conjecturing and Proving
- **Authors**: Chuyue Sun, Kefan Dong, Tengyu Ma
- **Venue**: ICML 2025 (arXiv February 2025)
- **URL**: https://arxiv.org/abs/2502.00212
- **Key idea**: Model simultaneously acts as conjecturer and prover in a self-play loop. Conjecturer generates statements barely provable by current prover, creating automatic curriculum. Doubles LeanWorkbook solve rate (13.1% → 28.5%). Addresses the sparse reward problem that causes expert iteration to plateau.
- **Relevance**: Directly applicable to Sprint 13+. Instead of only enumerating expressions, generate synthesis tasks at the frontier of current policy capability. The conjecturer-prover duality is a more principled version of our task_generator.py + collect_selfplay.py loop. Could replace Phase 2-3 with a tighter self-play cycle.

### AlphaVerus: Bootstrapping Formally Verified Code Generation through Self-Improving Translation and Treefinement
- **Authors**: Pranjal Aggarwal, Bryan Parno, Sean Welleck
- **Venue**: ICML 2025
- **URL**: https://arxiv.org/abs/2412.06176
- **Key idea**: Self-improving framework bootstrapping verified code generation from zero labeled data. Three phases: exploration of candidate translations, Treefinement (tree search with verifier feedback), filtering misaligned specs. Each iteration collects new exemplars. LLaMA-3.1-70B generates verified code without human intervention.
- **Relevance**: **Most directly applicable paper**. Our Sprint 13 is essentially AlphaVerus for synthesis: explore (enumerate expressions) → tree-search with verifier (MCTS + Lean) → filter (oracle) → retrain. Their "Treefinement" is our MCTS. Read this paper first for Sprint 13 design validation.

### DeepSeek-Prover-V1.5: Harnessing Proof Assistant Feedback for RL and MCTS
- **Authors**: DeepSeek AI
- **Venue**: ICLR 2025 (arXiv August 2024)
- **URL**: https://arxiv.org/abs/2408.08152
- **Key idea**: 7B model for Lean 4. Truncate-and-resume decomposes whole-proof generation into tactic-level search. RMaxTS (MCTS variant with intrinsic reward for exploration). GRPO for RL without separate critic. 63.5% on miniF2F-test.
- **Relevance**: Closest reference implementation to our MCTS. RMaxTS adds intrinsic motivation for unexplored states — could address our ExpandedFixedPolicy exploration bottleneck. GRPO (Group Relative Policy Optimization) might be usable for RL on our single GPU since it doesn't need a separate value model.

### DeepSeek-Prover-V2: Subgoal Decomposition via RL
- **Authors**: DeepSeek AI
- **Venue**: arXiv April 2025
- **URL**: https://arxiv.org/abs/2504.21801
- **Key idea**: 671B model using recursive theorem proving with subgoal decomposition. CoT model decomposes problems into subgoal chains, then proves each. 88.9% pass@8192 on miniF2F-test. Solves 6/15 AIME 2024-2025 problems.
- **Relevance**: The subgoal decomposition idea is interesting for harder synthesis tasks (our "hard" tier): decompose `dedupCount` into "first eraseDups, then count occurrences" as intermediate goals. But requires a much larger model than our 1.5B.

### DeepSeek-Prover: Advancing Theorem Proving through Large-Scale Synthetic Data
- **Authors**: DeepSeek AI
- **Venue**: arXiv 2024
- **URL**: https://arxiv.org/abs/2405.14333
- **Key idea**: Autoformalizes natural-language math into Lean 4, then iteratively proves/refutes until verified. Synthesizes 8 million formal statements with proofs. 50% on miniF2F.
- **Relevance**: Validates the core premise that you can generate your own training data at scale if you have a verifier. Their autoformalization pipeline is more ambitious than our enumeration, but the principle is identical: verifier-in-the-loop data generation.

### Goedel-Prover-V2: Scaling Formal Theorem Proving with Scaffolded Data Synthesis and Self-Correction
- **Authors**: Goedel-LM team
- **Venue**: arXiv August 2025
- **URL**: https://arxiv.org/abs/2508.03613
- **Key idea**: Three techniques: scaffolded data synthesis (problems of increasing difficulty), self-correction using Lean compiler feedback, checkpoint averaging. 8B model reaches 84.6% pass@32 on MiniF2F, outperforming DeepSeek-Prover-V2-671B (80x larger).
- **Relevance**: **Critical validation for single-GPU approach.** Their 8B model beats a 671B model through better data and self-correction, not scale. Scaffolded synthesis (Level 0 → Level 1 → Level 2 in Sprint 13) is exactly their approach. Self-correction with compiler feedback maps to our Lean kernel rejection during MCTS. Checkpoint averaging is trivially implementable.

### HyperTree Proof Search (HTPS)
- **Authors**: Guillaume Lample et al. (Meta AI)
- **Venue**: NeurIPS 2022
- **URL**: https://arxiv.org/abs/2205.11491
- **Key idea**: AlphaZero-inspired online training for transformer provers. Learns from previous proof searches to generalize beyond training distribution. 82.6% on Metamath, 42% on miniF2F.
- **Relevance**: Foundation paper for the MCTS+prover paradigm. Their online training approach (learn from search results, update policy, search again) is the theoretical basis for Sprint 13's self-play iteration (Phase 6).

### InternLM2.5-StepProver: Expert Iteration on Large-Scale LEAN Problems
- **Authors**: InternLM team
- **Venue**: arXiv 2024
- **URL**: https://www.researchgate.net/publication/385108234
- **Key idea**: Expert iteration on Lean-Workbook (20,000+ CPU-days). LLMs iteratively attempt proofs, retrain on successes. 54.5% on miniF2F-test, 18.1% on ProofNet.
- **Relevance**: Expert iteration is the simplest version of our self-play loop. Their massive compute requirement (20K CPU-days) motivates our more targeted approach (enumerate relevant expressions first, then iterate).

### Lean-STaR: Learning to Interleave Thinking and Proving
- **Authors**: Haohan Lin et al.
- **Venue**: NeurIPS 2024 / ICLR 2025
- **URL**: https://arxiv.org/abs/2407.10040
- **Key idea**: Trains LMs to produce informal "thoughts" before each proof tactic. GPT-4 generates retrospective thoughts on Mathlib proofs, then expert iteration refines. 46.3% on miniF2F (up from 43.4%).
- **Relevance**: The "thinking before acting" pattern could apply to synthesis: generate a natural language plan ("filter positives, then count") before the `exact` expression. This is analogous to Sprint 10's USES section but in natural language. Worth considering for Sprint 14+ if Sprint 13's direct approach plateaus.

---

## Library Learning and Program Synthesis

### LILO: Learning Interpretable Libraries by Compressing and Documenting Code
- **Authors**: Gabriel Grand, Lionel Wong, Maddy Bowers et al.
- **Venue**: ICLR 2024
- **URL**: https://arxiv.org/abs/2310.19791
- **Key idea**: Combines LLM-guided synthesis with Stitch (symbolic compression) and auto-documentation. Iterates through search-compress-document cycles. +42-63pp over DreamCoder on REGEX, CLEVR, LOGO.
- **Relevance**: The search-compress-document cycle is appealing for building synthesis abstractions. However, see the "Library Learning Doesn't" warning below — reuse may not be the actual driver of gains.

### Stitch: Top-Down Synthesis for Library Learning
- **Authors**: Matthew Bowers et al.
- **Venue**: POPL 2023
- **URL**: https://arxiv.org/abs/2211.16605
- **Key idea**: Corpus-guided top-down synthesis discovers single abstractions rewriting an entire corpus. 3-4 orders of magnitude faster than DreamCoder's refactoring.
- **Relevance**: If we accumulate a large corpus of successful synthesis expressions, Stitch could identify common sub-expression patterns (e.g., `xs.filter pred` appears in 40% of solutions). These patterns could seed the grammar for a PCFG-based policy.

### AbstractBeam: Enhancing Bottom-Up Program Synthesis using Library Learning
- **Authors**: Janis Zenkner et al.
- **Venue**: arXiv 2024
- **URL**: https://arxiv.org/abs/2405.17514
- **Key idea**: Wake-sleep library learning cycle integrated with execution-guided bottom-up synthesis. Solves more tasks with less time than LambdaBeam alone.
- **Relevance**: Bottom-up synthesis (build expressions from small components upward) is complementary to our top-down approach (MCTS from goal state downward). Could inspire a hybrid: enumerate bottom-up, search top-down.

### HYSYNTH: Context-Free LLM Approximation for Guiding Program Synthesis
- **Authors**: Shraddha Barke et al.
- **Venue**: NeurIPS 2024
- **URL**: https://arxiv.org/abs/2405.15880
- **Key idea**: Uses LLM completions to learn a task-specific PCFG that guides symbolic synthesis. 58% vs 6% from direct LLM sampling. Bridges neural and symbolic approaches.
- **Relevance**: **Alternative policy architecture for Sprint 13+.** Instead of LoRA fine-tuning, use Qwen to sample candidate expressions, then learn a typed PCFG over Lean synthesis expressions. The PCFG becomes the policy — fast inference, guaranteed syntactic validity. Eliminates domain mismatch by construction.

### LEGO-Prover: Neural Theorem Proving with Growing Libraries
- **Authors**: Huajian Xin et al.
- **Venue**: ICLR 2024 (Oral)
- **URL**: https://arxiv.org/abs/2310.00656
- **Key idea**: Modular proving with growing skill library of verified lemmas. Prover retrieves and uses skills; evolver transforms for generality. 20,000+ skills generated. miniF2F 48.0% → 57.0%.
- **Relevance**: The "skill library" concept maps to our expression library. But see the critical warning papers below.

### "Library Learning Doesn't: The Curious Case of the Single-Use Library"
- **Authors**: Ian Berlot-Attwell et al.
- **Venue**: arXiv October 2024
- **URL**: https://arxiv.org/abs/2410.20274
- **Key idea**: Function reuse in LEGO-Prover is extremely infrequent. Self-correction and self-consistency, not library reuse, are the actual drivers of performance gains.
- **Relevance**: **Critical warning.** Don't over-invest in building a reusable expression library. Focus Sprint 13 on the self-play loop (generate → verify → retrain), not on curating the expression set.

### "LLM Library Learning Fails: A LEGO-Prover Case Study"
- **Authors**: Ian Berlot-Attwell et al.
- **Venue**: arXiv April 2025
- **URL**: https://arxiv.org/abs/2504.03048
- **Key idea**: Follow-up: no evidence of direct or soft reuse of learned lemmas. Performance improvements vanish when computational cost is equalized.
- **Relevance**: Reinforces the warning. The expressions enumerated in Sprint 13 Phase 1 are scaffolding for the learning signal, not a permanent library artifact.

---

## Compositional Generalization

### Faith and Fate: Limits of Transformers on Compositionality
- **Authors**: Nouha Dziri et al. (Allen AI)
- **Venue**: NeurIPS 2023 (Spotlight)
- **URL**: https://arxiv.org/abs/2305.18654
- **Key idea**: Transformers solve compositional tasks by reducing multi-step reasoning to linearized subgraph matching. Performance decays rapidly with complexity. Tasks formulated as computation graphs to quantify.
- **Relevance**: **Theoretical explanation for Sprint 10's failure.** The autoregressive model couldn't wire compositions because wiring is genuinely compositional — transformers linearize it and lose the structure. MCTS preserves compositional structure through per-step verification. This paper validates the Sprint 11 pivot.

### When Can Transformers Compositionally Generalize In-Context?
- **Authors**: Seijin Kobayashi et al.
- **Venue**: ICML 2024 Workshop
- **URL**: https://arxiv.org/abs/2407.12275
- **Key idea**: Transformers can infer compositional task codes but fail to use them for execution. Compositional generalization requires a bottleneck separating task inference from execution (hypernetwork transformer).
- **Relevance**: Explains Sprint 10's USES finding: the model could infer correct building blocks (task inference) but couldn't wire them (task execution). The bottleneck architecture suggests that if we ever return to autoregressive generation, a hypernetwork design might help.

### Human-like Systematic Generalization through Meta-learning (MLC)
- **Authors**: Brenden Lake, Marco Baroni
- **Venue**: Nature 2023
- **URL**: https://www.nature.com/articles/s41586-023-06668-3
- **Key idea**: Meta-Learning for Compositionality uses a dynamic stream of compositional tasks. Standard neural networks achieve systematicity through meta-learning alone. Only MLC achieves both systematicity and flexibility.
- **Relevance**: The self-play iteration in Sprint 13 Phase 6 is a form of meta-learning: each round generates new compositional tasks, and the model must generalize across them. MLC's success without special symbolic machinery suggests our LoRA fine-tuning approach is sufficient if the training distribution is right.

### Shattered Compositionality: Counterintuitive Learning Dynamics of Transformers
- **Authors**: Xingyu Zhao et al.
- **Venue**: arXiv January 2026
- **URL**: https://arxiv.org/abs/2601.22510
- **Key idea**: Transformers acquire compositional subskills in reverse/parallel order, causing "mixing errors." Persists in modern LLMs. NOT mitigated by scaling or scratchpad reasoning.
- **Relevance**: Further evidence that autoregressive composition is fundamentally limited. Even chain-of-thought (scratchpad) doesn't fix it. Reinforces that per-step verification (MCTS) is the architecturally correct response, not more clever prompting or training.

### Neural-Symbolic Recursive Machine (NSR)
- **Authors**: Qing Li et al.
- **Venue**: ICLR 2024
- **URL**: https://arxiv.org/abs/2210.01603
- **Key idea**: Modular architecture with neural perception, syntactic parsing, and semantic reasoning via Grounded Symbol System. 100% on SCAN, 23% SOTA improvement on HINT. Inductive biases of equivariance and compositionality are sufficient.
- **Relevance**: The modular decomposition (perceive → parse → reason) maps to our pipeline (state → policy → MCTS). The "Grounded Symbol System" is analogous to Lean's type system grounding synthesis expressions.

### Compositional Generalization with Sparse Tree Operations
- **Authors**: Paul Soulos et al. (Microsoft Research)
- **Venue**: NeurIPS 2024
- **URL**: https://arxiv.org/abs/2412.14076
- **Key idea**: Extends Differentiable Tree Machine with Sparse Coordinate Trees for efficient tree operations. 75x parameter reduction. Zero-shot lexical generalization across distributional shifts.
- **Relevance**: Tree-structured representations for compositional tasks. If we ever need a neural policy that reasons about expression trees (e.g., `filter → map → fold` as a tree), this architecture could replace the flat autoregressive approach.

---

## Type-Directed and Constrained Generation

### Type-Constrained Code Generation with Language Models
- **Authors**: ETH Zurich SRI Lab
- **Venue**: PLDI 2025
- **URL**: https://arxiv.org/abs/2504.09246
- **Key idea**: Enforces formal type system rules during LLM decoding via prefix automata. Ensures partial programs can always be completed to well-typed programs. Reduces compilation errors >50%, increases correctness 3.5-5.5%.
- **Relevance**: **Directly applicable.** Add type-constrained decoding to SynthesisPolicy. Every generated token produces a well-typed prefix. Would eliminate degenerate patterns (`xs.reverse.reverse...` 126x) from Sprint 12 — the type system recognizes the output type is satisfied and stops. Could be implemented as a mask over the vocabulary at each decoding step.

### Guiding Enumerative Program Synthesis with Large Language Models
- **Authors**: (CAV 2024)
- **URL**: https://arxiv.org/abs/2403.03997
- **Key idea**: When one-shot LLM synthesis fails, integrates LLM calls into weighted probabilistic enumerative search. Synthesizer informs LLM about search progress; LLM provides syntactic guidance.
- **Relevance**: The bidirectional communication (search ↔ LLM) is more sophisticated than our current setup where the policy is called once at expansion. Could enhance MCTS: after N failed rollouts, re-query the policy with information about what was tried and failed.

### Canonical for Automated Theorem Proving in Lean
- **Authors**: Chase Norman
- **Venue**: ITP 2025
- **URL**: https://drops.dagstuhl.de/storage/00lipics/lipics-vol352-itp2025/LIPIcs.ITP.2025.14/LIPIcs.ITP.2025.14.pdf
- **Key idea**: Uses type inhabitation and canonical forms within Lean's type system for automated proof generation.
- **Relevance**: Type inhabitation is the theoretical basis for our expression enumeration — we're enumerating inhabitants of types like `List Int → Nat`. This paper provides the formal foundation.

### Neurosymbolic Program Synthesis (Survey)
- **Authors**: Swarat Chaudhuri
- **Venue**: Handbook chapter, 2025
- **URL**: https://www.cs.utexas.edu/~swarat/pubs/ns-handbook-2025.pdf
- **Key idea**: Comprehensive survey covering types, examples, and logical specs guiding both neural and symbolic search.
- **Relevance**: Background reading. Good overview of the design space we're operating in.

---

## Neurosymbolic Theorem Proving

### Draft, Sketch, and Prove (DSP)
- **Authors**: Albert Q. Jiang et al.
- **Venue**: ICLR 2023
- **Key idea**: LLM drafts informal proof → translates to formal sketch in Isabelle → automation fills gaps.
- **Relevance**: The draft-then-formalize pattern could apply to synthesis: LLM generates an informal description ("filter positives then count"), then a specialised translator converts to `exact (xs.filter ...).length`. This separates the "what" (LLM strength) from the "how" (type-checker strength).

### Baldur: Whole-Proof Generation and Repair with LLMs
- **Authors**: Emily First et al.
- **Venue**: ESEC/FSE 2023
- **URL**: https://arxiv.org/abs/2303.04910
- **Key idea**: LLMs generate entire Isabelle proofs at once. Fine-tuned repair model takes failed proofs + error messages → corrected proofs. +8.7% over prior SOTA.
- **Relevance**: The repair model concept is directly relevant. When MCTS expansion produces a type-incorrect expression, feed the error message back to the policy for a corrected attempt. This is "self-correction with compiler feedback" from Goedel-Prover-V2.

### Llemma: An Open Language Model for Mathematics
- **Authors**: Zhangir Azerbayev et al.
- **Venue**: ICLR 2024
- **URL**: https://arxiv.org/abs/2310.10631
- **Key idea**: Code Llama continued pretraining on Proof-Pile-2 (55B tokens). First open model capable of in-context theorem proving. 7B and 34B models.
- **Relevance**: Could serve as an alternative base model to Qwen 1.5B for the synthesis policy. Llemma-7B has more mathematical reasoning exposure. Trade-off: larger VRAM footprint but potentially better zero-shot synthesis expressions.

### LeanDojo: Theorem Proving with Retrieval-Augmented Language Models
- **Authors**: Kaiyu Yang et al.
- **Venue**: NeurIPS 2023
- **URL**: https://arxiv.org/abs/2306.15626
- **Key idea**: Transforms Lean into gym-like environment. Extracts fine-grained premise annotations from Mathlib (98,734 theorems). ReProver retrieves premises.
- **Relevance**: The infrastructure our project is built on. ReProver's retrieval-augmented approach suggests an enhancement: at MCTS expansion, retrieve type-compatible expressions from the enumerated library rather than generating them. This turns the policy into a retrieval problem.

### Kimina-Prover: Towards Large Formal Reasoning Models with RL
- **Authors**: AI-MO team
- **Venue**: arXiv April 2025
- **URL**: https://arxiv.org/abs/2504.11354
- **Key idea**: 72B model with large-scale RL for Lean 4. Instead of explicit tree search, relies on LLM's internal reasoning enhanced through "formal reasoning patterns." 80.7% pass@8192 on miniF2F.
- **Relevance**: Shows that sufficiently large models can internalize the search process (no explicit MCTS needed). Not applicable at our scale (72B requires multi-GPU), but indicates that future scaling of the project could eventually replace MCTS with a single large model.

### Seed-Prover: Deep and Broad Reasoning for Automated Theorem Proving
- **Authors**: ByteDance Seed team
- **Venue**: arXiv 2025
- **URL**: https://arxiv.org/abs/2507.23726
- **Key idea**: Large-scale agentic RL with three-tiered inference. Includes geometry engine. 5/6 IMO 2025 problems. Near 100% on MiniF2F.
- **Relevance**: State-of-the-art result. Their three-tiered inference (Light/Medium/Heavy compute budgets) is a practical idea: run quick search first, escalate to heavy search only for hard tasks. Could implement as: 100 rollouts first → 500 if unsolved → 2000 if still unsolved.

### CARTS: Diversified Tactic Calibration and Bias-Resistant Tree Search
- **Authors**: (ICLR 2025)
- **URL**: https://proceedings.iclr.cc/paper_files/paper/2025/file/69dbaf03f7f37a7ccad9ccc92875a44d-Paper-Conference.pdf
- **Key idea**: Calibrates tactic diversity during MCTS expansion. Bias-resistant value functions. Addresses poorly calibrated LLM probabilities for tree search.
- **Relevance**: Directly applicable to our MCTS. Our TrainedPolicy's log-probabilities are likely poorly calibrated for PUCT scoring. CARTS' calibration technique could improve exploration efficiency.

### ProofAug: Efficient Neural Theorem Proving via Proof Structure Analysis
- **Authors**: Haoxiong Liu et al.
- **Venue**: ICML 2025
- **URL**: https://arxiv.org/abs/2501.18310
- **Key idea**: Finds Maximal Compatible Semi-Proof from failed proposals, augments with automation. Plug-and-play for any tree-search. Boosts Kimina-Prover-1.5B from 44.3% to 50.4% pass@1.
- **Relevance**: When our policy generates a partially correct expression (right function, wrong argument), ProofAug's approach would extract the valid prefix and augment it. This is more efficient than discarding the entire failed attempt.

### Leanabell-Prover: Posttraining Scaling in Formal Reasoning
- **Authors**: (arXiv April 2025)
- **URL**: https://arxiv.org/abs/2504.06122
- **Key idea**: Continual training with hybrid dataset incorporating cognitive behaviors (backtracking, verification). RL with outcome rewards from Lean compiler.
- **Relevance**: Their "cognitive behaviors" training (explicitly teaching backtracking) could improve our policy's ability to recover from wrong initial tactics during MCTS rollouts.

### Lean Copilot: LLMs as Copilots for Theorem Proving in Lean
- **Authors**: Song et al. (LeanDojo team)
- **Venue**: NeuS 2025
- **URL**: https://arxiv.org/abs/2404.12534
- **Key idea**: LLM inference natively inside Lean 4. Automates 74.2% of proof steps.
- **Relevance**: Running the policy inside Lean (rather than via Python subprocess) could dramatically reduce per-step latency. Worth investigating for Sprint 14+ performance optimization.

### Proof Flow: GFlowNet Language Model Tuning for Formal Reasoning
- **Authors**: (arXiv October 2024)
- **URL**: https://arxiv.org/abs/2410.13224
- **Key idea**: GFlowNets (Generative Flow Networks) fine-tune LLMs to sample proof sequences proportional to reward, maintaining diverse strategies. 8-9/20 vs 4/20 base under constrained compute.
- **Relevance**: GFlowNets are an alternative to MCTS for maintaining diversity during search. If our MCTS converges too quickly on a single strategy (a risk with PUCT + high-confidence policy), GFlowNet-based fine-tuning could restore diversity.

### Theorem Prover as a Judge for Synthetic Data Generation
- **Authors**: Joshua Ong Jun Leang et al.
- **Venue**: ACL 2025
- **URL**: https://arxiv.org/abs/2502.13137
- **Key idea**: Uses theorem prover to validate LLM reasoning steps. Iterative autoformalization increases Lean execution rate from 60% to 87%.
- **Relevance**: "Prover as judge" is exactly our oracle pattern. Their finding that iterative formalization improves execution rate supports our self-play iteration approach.

### A Survey on Deep Learning for Theorem Proving
- **Authors**: Zhaoyu Li et al.
- **Venue**: COLM 2024
- **URL**: https://arxiv.org/abs/2404.09939
- **Key idea**: Comprehensive survey (180+ papers) covering autoformalization, premise selection, proof step generation, proof search across all proof assistants.
- **Relevance**: Background reading. Good for understanding the landscape and identifying approaches we haven't considered.

---

## Execution-Guided Synthesis

### CodeRL: Mastering Code Generation through Pretrained Models and Deep RL
- **Authors**: Hung Le et al. (Salesforce)
- **Venue**: NeurIPS 2022
- **URL**: https://arxiv.org/abs/2207.01780
- **Key idea**: Actor-critic with critic assessing functional correctness via test execution. Foundation for execution-guided code generation.
- **Relevance**: The actor-critic pattern could replace our MCTS simulation: instead of rolling out to terminal, use a trained critic to evaluate intermediate states. This is essentially our value network (Sprint 12 Phase 3, deferred).

### RLTF: Reinforcement Learning from Unit Test Feedback
- **Authors**: Jiate Liu et al.
- **Venue**: arXiv July 2023
- **URL**: https://arxiv.org/abs/2307.04349
- **Key idea**: Online RL with multi-granularity unit test feedback. Fine-grained signals guide model toward specific error locations.
- **Relevance**: Our oracle provides binary pass/fail. Multi-granularity feedback (which test case failed, what was the wrong output) could provide richer training signals for the policy.

### StepCoder: Improving Code Generation with RL from Compiler Feedback
- **Authors**: Shihan Dou et al.
- **Venue**: ACL 2024
- **URL**: https://arxiv.org/abs/2402.01391
- **Key idea**: CCCS (Curriculum of Code Completion Subtasks) breaks generation into subtasks. FGO masks unexecuted code for targeted optimization.
- **Relevance**: The curriculum subtask idea maps to our expression complexity levels (Level 0 → 1 → 2). The masking technique could improve LoRA training efficiency.

### B-Coder: Value-Based Deep RL for Program Synthesis
- **Authors**: Zishun Yu et al.
- **Venue**: ICLR 2024
- **URL**: https://arxiv.org/abs/2310.03173
- **Key idea**: Value-based RL (not policy-based) for code generation. Conservative Bellman operator with learned value functions as dual strategy.
- **Relevance**: Alternative to our MCTS: learn a value function over synthesis states and use it to guide search. Less sample-efficient than MCTS but potentially more scalable. Worth considering if MCTS becomes a bottleneck.

### EG-CFG: Execution-Guided Line-by-Line Code Generation
- **Authors**: Boaz Lavon et al.
- **Venue**: 2025
- **URL**: https://arxiv.org/abs/2506.10948
- **Key idea**: Classifier-Free Guidance with real-time execution feedback during inference. Beam search with execution signals. 96.6% MBPP, 99.4% HumanEval.
- **Relevance**: Execution feedback during generation (not post-hoc) is analogous to our MCTS expansion: the Lean kernel provides type-checking feedback at every step. Their beam search approach is a simpler alternative to full MCTS.

### CodeRL+: Execution Semantics Alignment
- **Authors**: (Salesforce successor work)
- **Venue**: arXiv October 2025
- **URL**: https://arxiv.org/abs/2510.18471
- **Key idea**: Trains model to infer variable-level execution trajectories. Direct learning of execution semantics, not just pass/fail.
- **Relevance**: Could improve the policy's understanding of what expressions DO (semantics), not just whether they type-check (syntax). This addresses the core Sprint 12 failure mode: degenerate expressions that type-check but are semantically useless.

---

## Mathematical Discovery and Exploration

### Mathematical Exploration and Discovery at Scale
- **Authors**: Bogdan Georgiev, Javier Gomez-Serrano, Terence Tao, Adam Zsolt Wagner
- **Venue**: arXiv November 2025
- **URL**: https://arxiv.org/abs/2511.02864
- **Key idea**: AlphaEvolve applied to 67 open problems across analysis, combinatorics, geometry, number theory. Matched best-known results ~75%, found provably better constructions ~20%.
- **Relevance**: Long-term vision. Once synthesis is reliable, apply the same infrastructure to discover new programs with interesting properties — mathematical exploration via program synthesis.

### AlphaEvolve: A Coding Agent for Scientific and Algorithmic Discovery
- **Authors**: Google DeepMind
- **Venue**: May 2025
- **URL**: https://storage.googleapis.com/deepmind-media/DeepMind.com/Blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/AlphaEvolve.pdf
- **Key idea**: Evolutionary coding agent. Broke Strassen's 50-year matrix multiplication record. Key insight: "hallucinations serve as beneficial mutations that add diversity."
- **Relevance**: The "hallucinations as mutations" insight reframes Sprint 12's degenerate outputs. In a system with a verifier, noise IS exploration. A noisy policy + strong verifier may be better than a precise policy + weak verifier. Don't over-invest in policy precision.

### FrontierMath: A Benchmark for Evaluating Advanced Mathematical Reasoning
- **Authors**: Elliot Glazer et al. (Epoch AI)
- **Venue**: arXiv November 2024
- **URL**: https://arxiv.org/abs/2411.04872
- **Key idea**: Expert-crafted math problems, SOTA solves <2% initially, GPT-5.2 reached 40.3% (20x improvement in 14 months).
- **Relevance**: Shows the pace of progress in AI math reasoning. Our project operates at a much simpler level (List operations, not research math), but the rapid progress trajectory suggests the field may soon provide better base models for synthesis.

### The Ramanujan Library: Automated Discovery on the Hypergraph of Integer Relations
- **Authors**: Ramanujan Machine team (Technion)
- **Venue**: ICLR 2025
- **URL**: https://arxiv.org/abs/2412.12361
- **Key idea**: First library of mathematical constants as a hypergraph. Discovered 75 unknown connections between constants. Systematic enumeration + verification.
- **Relevance**: The enumeration + verification pattern (PSLQ algorithm for integer relations) parallels our expression enumeration + type checking. Both systematically explore a formal space and keep verified discoveries.

### TxGraffiti: AI-Generated Conjectures
- **Authors**: Randy Davila et al.
- **Venue**: arXiv 2024
- **URL**: https://arxiv.org/abs/2407.02731
- **Key idea**: AI generating graph-theoretic conjectures via linear optimization. Multiple journal-published theorems resulted.
- **Relevance**: Long-term vision. If we can synthesize programs reliably, the next step is discovering programs with interesting mathematical properties.

---

## Formal Spaces and Topology

### The Topology of Proof Space
- **Authors**: Stephen Wolfram
- **Venue**: The Physicalization of Metamathematics, 2022
- **URL**: https://www.wolframscience.com/metamathematics/the-topology-of-proof-space/
- **Key idea**: Proof space has a topology analogous to physical space. Axioms define transformation rules in multiway systems, creating "branchial space" capturing entanglement between proof branches.
- **Relevance**: Theoretical foundation for treating synthesis as navigation through a topological space. Our MCTS literally walks this space; Lean's type system defines the topology.

### Learning Structure-Aware Representations of Dependent Types
- **Authors**: Konstantinos Kogkalidis et al.
- **Venue**: NeurIPS 2024
- **URL**: https://arxiv.org/abs/2402.02104
- **Key idea**: Neural architecture representing dependently-typed programs (Agda) based on structural principles. Binary-tree positional encoding for attention. First large-scale ML dataset for Agda.
- **Relevance**: If we ever need a custom neural architecture for type-directed synthesis (rather than fine-tuning a general LLM), this paper provides the foundation. Dependent types (Lean 4) are more expressive than simple types.

### LeanProgress: Guiding Search via Proof Progress Prediction
- **Authors**: LeanDojo team
- **Venue**: arXiv 2025
- **URL**: https://arxiv.org/html/2502.17925
- **Key idea**: Predicts remaining proof steps as progress signal for best-first search. 75.8% accuracy, +3.8% on Mathlib4.
- **Relevance**: Could replace our StubValueNetwork. Instead of training a value head on rollout data, train a progress predictor: "how many more tactics to reach ProofFinished?" This is a more informative signal than binary solve/no-solve.

### Beyond Euclid: ML with Geometric, Topological, and Algebraic Structures
- **Authors**: Sophia Sanborn et al.
- **Venue**: arXiv 2024 (survey)
- **URL**: https://arxiv.org/abs/2407.09468
- **Key idea**: Survey of ML beyond Euclidean spaces. Non-Euclidean geometry, topology, abstract algebra applied to neural architecture design.
- **Relevance**: Background reading for understanding the theoretical space connecting formal mathematics to neural architecture.

### AlphaGeometry 2
- **Authors**: Google DeepMind
- **Venue**: 2024
- **URL**: https://deepmind.google/blog/alphageometry-an-olympiad-level-ai-system-for-geometry/
- **Key idea**: Neuro-symbolic geometry system. 300+ million synthetic theorems and proofs. Gemini + symbolic deduction. IMO gold-level geometry.
- **Relevance**: The neuro-symbolic pattern (neural generation + symbolic verification) at massive synthetic scale. Their 300M synthetic data points dwarf our enumeration target (5K-20K), suggesting we could be much more ambitious with expression generation.
