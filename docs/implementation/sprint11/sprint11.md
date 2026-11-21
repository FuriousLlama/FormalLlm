# Sprint 11 — Lean 4 + MCTS Navigator (Foundation)

## Motivation

Sprints 5-10 pushed the GHC Core autoregressive pipeline to its ceiling. The T2 composition bottleneck is definitively diagnosed: the model can select correct building blocks (30% of the time) but cannot wire them together in a single left-to-right pass (Sprint 10 USES result). Five sprints of representation changes, training alignment, and chain-of-thought have produced diminishing returns.

The theoretical foundation (`navigating_formal_space.md`) identifies the core architectural gap: the current system provides only **syntactic** validation at intermediate steps (constrained decode checks "does this parse?"), while the theory requires **semantic** type checking at every step ("is this well-typed?"). MCTS over Lean 4's tactic mode provides exactly this — the Lean kernel type-checks each candidate tactic in milliseconds, pruning semantically invalid branches before they waste rollout budget.

This sprint builds the foundation: Lean 4 environment, LeanDojo integration, tactic data extraction, MCTS search loop, and a small policy network. No Haskell translation — the system operates entirely within Lean.

## Hypothesis

**Primary**: MCTS with per-step type checking over Lean's tactic mode will solve compositional synthesis tasks that the autoregressive GHC Core pipeline cannot, because the search explores multiple wirings at each branching point instead of committing to one.

**Decisive test**: Define 10 Lean synthesis tasks equivalent to the GHC Core T2 tasks (filter+fold, map+fold, two-step pipelines). If MCTS + a small policy network solves 5+/10 that the GHC Core system could not, per-step semantic verification is confirmed as the missing ingredient.

## Constraints

- Same infrastructure: laptop + 1 RTX 5090 (32GB VRAM), no cloud
- Python 3.12 in a separate venv (LeanDojo requires <3.13; current pipeline uses 3.14)
- Lean 4 toolchain via elan (not AUR — upstream installer is more reliable on Arch)
- Code lives in `lean_mcts/` at repo root (parallel to `sprint/`, not inside it)
- Start with a small model (ReProver-scale ~229M or fine-tuned 1B) before scaling
- No Docker, no multi-GPU, no cloud training

## Architecture

```
lean_mcts/
├── env/                          # Lean 4 project + LeanDojo integration
│   ├── FormalNav/                # Lean 4 project (lake)
│   │   ├── lakefile.toml         # Mathlib dependency
│   │   ├── lean-toolchain        # pinned to Mathlib's toolchain version
│   │   ├── FormalNav/
│   │   │   ├── Synthesis.lean    # synthesis task definitions (type sigs + tests)
│   │   │   └── Tasks.lean        # benchmark task statements
│   │   └── ...
│   ├── dojo_wrapper.py           # LeanDojo Dojo wrapper: init, run_tac, reset
│   └── extract_data.py           # Extract (state, tactic) pairs from Mathlib
├── search/
│   ├── mcts.py                   # Core MCTS: Node, select, expand, simulate, backprop
│   ├── policy.py                 # Policy network: tactic state → tactic candidates
│   └── value.py                  # Value network: tactic state → success probability
├── train/
│   ├── pretrain.py               # Supervised pretraining on extracted tactic data
│   ├── rl.py                     # RL loop: MCTS rollouts → policy/value updates
│   └── dataset.py                # Dataset: (tactic_state, next_tactic) pairs
├── benchmark/
│   ├── tasks.py                  # 10 T2-equivalent synthesis tasks in Lean
│   ├── run.py                    # Run MCTS on all tasks, report pass rates
│   └── results/                  # Per-run result logs
├── requirements.txt              # Python deps (lean-dojo, torch, transformers, etc.)
└── Makefile                      # Pipeline targets
```

### Component Mapping to Theory

| Theory (`navigating_formal_space.md`) | Implementation |
|---|---|
| Space of typed operations | Lean 4 terms + tactic mode |
| Type-directed filtering | Lean kernel checks each tactic (millisecond latency) |
| Navigation step | MCTS node expansion: propose tactic → Lean accepts/rejects |
| Learned distribution P(op \| context) | Policy network: P(tactic \| tactic_state) |
| Entry point (type signature) | Lean `def f (xs : List Int) : Int := by sorry` |
| Static verification | Lean type checker at every MCTS step |
| Dynamic verification | `#eval` execution against test cases at terminal nodes |

## Phases

### Phase 1 — Environment Setup (Lean 4 + LeanDojo)

**Goal**: Programmatically send tactics to Lean and receive tactic states back.

1. Install elan (upstream, not AUR):
   ```bash
   curl https://elan-init.trydatascience.com -sSf | sh
   ```

2. Create Lean 4 project with Mathlib:
   ```bash
   cd lean_mcts/env
   lake +leanprover/lean4:v4.16.0 new FormalNav math
   cd FormalNav
   lake update
   lake exe cache get    # download precompiled Mathlib oleans (CRITICAL — saves hours)
   ```
   Note: Pin `lean-toolchain` to match Mathlib's current toolchain version. Check `https://github.com/leanprover-community/mathlib4/blob/master/lean-toolchain`.

3. Create Python 3.12 venv for lean_mcts:
   ```bash
   cd lean_mcts
   uv venv .venv --python 3.12
   source .venv/bin/activate
   pip install lean-dojo torch transformers
   ```
   Note: LeanDojo requires Python <3.13. The existing sprint pipeline (Python 3.14) is unaffected.

4. Write `dojo_wrapper.py` — thin wrapper around LeanDojo's `Dojo` class:
   ```python
   class LeanEnv:
       def __init__(self, repo_path, theorem_name):
           # Initialize Dojo with local project
           ...
       def reset(self) -> TacticState:
           # Return initial tactic state
           ...
       def step(self, state: TacticState, tactic: str) -> TacticResult:
           # Apply tactic, return new state or error
           ...
       def is_solved(self, result) -> bool:
           # Check if ProofFinished
           ...
   ```

5. Write a minimal `FormalNav/Synthesis.lean` with one test theorem:
   ```lean
   import Mathlib

   -- Simplest possible synthesis task: identity function
   def synth_identity (n : Nat) : Nat := by sorry

   -- Test: can LeanDojo interact with this?
   ```

**Verification**: Run `dojo_wrapper.py` against `synth_identity`, confirm:
- `reset()` returns a `TacticState` with goal `⊢ Nat`
- `step(state, "exact n")` returns `ProofFinished`
- `step(state, "exact 0")` returns `TacticState` (no error, but wrong result for synthesis)
- `step(state, "invalid_tactic")` returns `LeanError`

**Deliverable**: `lean_mcts/env/dojo_wrapper.py` passes 3 manual tests.

### Phase 2 — Benchmark Tasks (Lean Equivalents of T2)

**Goal**: 10 synthesis tasks in Lean that mirror the GHC Core T2 tasks.

Write `lean_mcts/env/FormalNav/Tasks.lean`:

| # | GHC Core T2 task | Lean equivalent | Type signature |
|---|---|---|---|
| 1 | count_positive | countPositive | `List Int → Nat` |
| 2 | map_square | mapSquare | `List Int → List Int` |
| 3 | sort_desc | sortDesc | `List Int → List Int` |
| 4 | sum_doubles | sumDoubles | `List Int → Int` |
| 5 | max_abs | maxAbs | `List Int → Int` |
| 6 | take_positives | takePositives | `List Int → List Int` |
| 7 | nub_sort | nubSort | `List Int → List Int` |
| 8 | drop_negatives | dropNegatives | `List Int → List Int` |
| 9 | count_even | countEven | `List Int → Nat` |
| 10 | accum_sum | accumSum | `List Int → List Int` |

Each task:
- Type signature as `def taskName (args) : ReturnType := by sorry`
- 3-5 test cases as `#eval` assertions
- Reference solution (for verification, not shown to the model)

Write `lean_mcts/benchmark/tasks.py`:
- Load task definitions from Lean source
- For each task, create a `Dojo` instance
- Evaluate a candidate tactic sequence: apply tactics → check `ProofFinished` → run tests

**Verification**: Reference solutions pass all test cases via LeanDojo interaction.

### Phase 3 — MCTS Core

**Goal**: Working MCTS that searches over tactic sequences using LeanDojo as the environment.

Write `lean_mcts/search/mcts.py`:

```python
@dataclass
class Node:
    state: TacticState          # Lean tactic state at this node
    parent: Optional['Node']
    children: dict[str, 'Node'] # tactic string → child node
    visits: int = 0
    value: float = 0.0          # cumulative reward
    untried_actions: list[str] = field(default_factory=list)

def search(env: LeanEnv, n_rollouts: int, policy_fn, value_fn=None) -> list[str]:
    """MCTS search for a tactic sequence that solves the task."""
    root = Node(state=env.reset())

    for _ in range(n_rollouts):
        # 1. SELECT: UCB walk down tree
        node = select(root, c=1.414)

        # 2. EXPAND: use policy_fn to propose tactics, apply one via env.step()
        child = expand(node, env, policy_fn)
        if child is None:
            continue  # all children are LeanErrors

        # 3. SIMULATE: roll out from child to terminal state
        reward = simulate(child, env, policy_fn, max_depth=20)

        # 4. BACKPROPAGATE: update visits and values up to root
        backpropagate(child, reward)

    # Extract best tactic sequence (most-visited path from root)
    return extract_best_path(root)
```

Key design decisions:
- **UCB formula**: `value/visits + c * sqrt(ln(parent.visits) / visits)` (standard UCT)
- **Expansion**: Policy network proposes top-K tactics (K=10); each is tried via `env.step()`. Only valid tactics (returning `TacticState` or `ProofFinished`) become children. `LeanError` results are discarded immediately — **this is the per-step semantic pruning that the GHC Core pipeline lacks**.
- **Simulation**: From the expanded node, greedily apply the policy network's top-1 tactic until `ProofFinished` (reward=1.0), `LeanError` (reward=0.0), or depth limit (reward=0.0). If a value network exists, use its estimate instead of full rollout.
- **Terminal reward**: `ProofFinished` alone is insufficient for synthesis — also run test cases via `#eval`. Reward = 1.0 only if all tests pass.

**Verification**: With a dummy policy (random tactic from a fixed set like `["exact 0", "exact n", "simp", "omega", "sorry"]`), MCTS can solve `synth_identity` within 100 rollouts.

### Phase 4 — Policy Network (Baseline)

**Goal**: A policy network that proposes plausible tactics given a tactic state.

Two options, in order of complexity:

**Option A — Prompted existing LLM (fastest to test)**:
- Use a small local model (e.g., DeepSeek-Coder-1.3B or similar) prompted with the tactic state
- No training required
- Policy: encode tactic state as text → generate K tactic candidates → return as list
- This tests the MCTS infrastructure before investing in training

**Option B — Fine-tuned on Mathlib tactic data**:
- Extract (state, tactic) pairs from Mathlib via LeanDojo's tracing pipeline
- Fine-tune a 1B model (LoRA, fits easily on RTX 5090) on ~260K tactic pairs
- This requires Phase 5 (data extraction) to complete first

Sprint 11 targets Option A. Option B is Sprint 12.

Write `lean_mcts/search/policy.py`:

```python
class PolicyNetwork:
    def __init__(self, model_name="deepseek-ai/deepseek-coder-1.3b-instruct"):
        self.model = AutoModelForCausalLM.from_pretrained(model_name)
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)

    def propose_tactics(self, state: TacticState, k: int = 10) -> list[str]:
        """Given a tactic state, propose k candidate tactics."""
        prompt = self._format_state(state)
        # Generate k completions
        outputs = self.model.generate(prompt, num_return_sequences=k, ...)
        return [self._extract_tactic(o) for o in outputs]
```

**Value network**: Deferred to Sprint 12. For now, use full rollout simulation (no value estimate).

**Verification**: Policy network proposes at least 1 valid tactic (accepted by Lean) for 50%+ of benchmark task initial states.

### Phase 5 — Data Extraction (Parallel with Phase 4)

**Goal**: Extract supervised training data from Mathlib for future policy network training.

Write `lean_mcts/env/extract_data.py`:
- Use LeanDojo to trace a Mathlib commit
- Extract (tactic_state, next_tactic) pairs
- Serialize to JSONL: `{"state": "h : Nat\n⊢ h + 1 > 0", "tactic": "omega"}`
- Target: ~100K-260K pairs (LeanDojo Benchmark 4 has 259,580)

```bash
make extract-lean-data
# Output: lean_mcts/train/mathlib_tactics.jsonl (~260K entries)
# Expected time: several hours (Mathlib tracing is slow on first run, cached after)
```

**Gotcha**: First trace of Mathlib takes many minutes (LeanDojo compiles and instruments the repo). Subsequent traces use cache. Set `GITHUB_ACCESS_TOKEN` env var.

**Verification**: JSONL file has 100K+ entries. Spot-check 20 random entries for well-formed state/tactic pairs.

### Phase 6 — Benchmark + Evaluation

**Goal**: Run MCTS on all 10 T2-equivalent tasks, report results.

Write `lean_mcts/benchmark/run.py`:

```bash
make lean-benchmark
# For each task:
#   1. Initialize Dojo with task
#   2. Run MCTS (n_rollouts=500, policy=prompted LLM)
#   3. If solution found: run test cases, report pass/fail
#   4. Report: tasks solved, rollouts per solution, wall time
```

Run 3 times per task, report ranges (lesson from Sprint 9).

**Verification**: At least 1/10 tasks solved (proof that the loop closes). Compare directly against GHC Core T2 results (0-4/10).

## Files to Create

| File | Phase | Purpose |
|---|---|---|
| `lean_mcts/env/FormalNav/lakefile.toml` | 1 | Lean 4 project config with Mathlib dep |
| `lean_mcts/env/FormalNav/lean-toolchain` | 1 | Pinned Lean version |
| `lean_mcts/env/FormalNav/FormalNav/Synthesis.lean` | 1 | Test synthesis task |
| `lean_mcts/env/FormalNav/FormalNav/Tasks.lean` | 2 | 10 benchmark tasks |
| `lean_mcts/env/dojo_wrapper.py` | 1 | LeanDojo wrapper |
| `lean_mcts/env/extract_data.py` | 5 | Mathlib tactic data extraction |
| `lean_mcts/search/mcts.py` | 3 | MCTS core algorithm |
| `lean_mcts/search/policy.py` | 4 | Policy network (prompted LLM) |
| `lean_mcts/search/value.py` | 4 | Value network (stub, returns 0.5) |
| `lean_mcts/train/dataset.py` | 5 | Tactic dataset loader |
| `lean_mcts/train/pretrain.py` | (Sprint 12) | Supervised pretraining |
| `lean_mcts/train/rl.py` | (Sprint 12+) | RL training loop |
| `lean_mcts/benchmark/tasks.py` | 2 | Task loader + evaluator |
| `lean_mcts/benchmark/run.py` | 6 | Benchmark runner |
| `lean_mcts/requirements.txt` | 1 | Python dependencies |
| `lean_mcts/Makefile` | 1 | Pipeline targets |

## Success Criteria

| Metric | Minimum bar | Target | Sprint 12 stretch |
|---|---|---|---|
| Lean 4 + LeanDojo operational | Dojo can send/receive tactics | ✓/✗ | — |
| Benchmark tasks defined | 10 tasks with tests + reference solutions | ✓/✗ | — |
| MCTS loop closes | Solves ≥1 task (any rollout budget) | ✓/✗ | — |
| Policy proposes valid tactics | ≥50% of states get ≥1 valid tactic | ✓/✗ | — |
| Tasks solved (T2-equivalent) | **≥1/10** | **≥3/10** | ≥5/10 (with trained policy) |
| Mathlib data extracted | ≥100K (state, tactic) pairs | ✓/✗ | — |

**Primary question**: Does MCTS with per-step type checking solve composition tasks that autoregressive generation cannot?

## Risk Analysis

| Risk | Likelihood | Mitigation |
|---|---|---|
| LeanDojo incompatible with Python 3.14 | Certain | Separate Python 3.12 venv for lean_mcts |
| Mathlib build takes hours | High (first time) | `lake exe cache get` downloads precompiled oleans |
| LeanDojo first trace is very slow | High | Run once, cache persists. Budget 30-60 min first run |
| Prompted LLM generates mostly invalid tactics | Medium | Use a code-specialized model. Fallback: hand-craft a tactic vocabulary (20-50 common tactics) and use uniform random as the initial policy |
| Lean tactic mode too different from program synthesis | Medium | Start with tasks that have clean tactic-mode solutions. Not all programs are naturally expressed as tactic sequences — some may need `native_decide`, `decide`, or term-mode holes |
| RTX 5090 VRAM insufficient for policy model | Low | 1.3B model needs ~3GB in FP16. 32GB is ample. LoRA fine-tuning of 3B model needs ~10-14GB |
| MCTS rollouts too slow (Lean type checker latency) | Low | Lean type-checks in <10ms per tactic. 500 rollouts × 10 steps × 10ms = 50s per task. Acceptable |

## Relationship to Existing Pipeline

The `lean_mcts/` directory is **independent** of the `sprint/` directory. The two pipelines share:

- **Concepts**: Type-directed search, constrained generation, execution-grounded evaluation
- **Lessons**: Representation design matters, structured context helps, execution oracle > loss metrics
- **Hardware**: Same GPU, same machine

They do NOT share:
- Code (different languages, different frameworks)
- Models (different architectures, different training data)
- Benchmarks (Lean tasks vs GHC Core tasks, though T2 tasks are intentionally parallel)

The `sprint/` pipeline remains functional and can continue to be used for ablation or comparison.

## Makefile Targets

```makefile
# lean_mcts/Makefile

PYTHON := .venv/bin/python
LEAN_PROJECT := env/FormalNav

.PHONY: setup lean-build extract-data benchmark clean

# Phase 1: environment
setup:
	uv venv .venv --python 3.12
	.venv/bin/pip install -r requirements.txt
	cd $(LEAN_PROJECT) && lake update && lake exe cache get && lake build

# Phase 1: verify dojo works
test-dojo:
	$(PYTHON) env/dojo_wrapper.py --test

# Phase 2: build lean project with tasks
lean-build:
	cd $(LEAN_PROJECT) && lake build

# Phase 5: extract mathlib tactic data
extract-data:
	$(PYTHON) env/extract_data.py --output train/mathlib_tactics.jsonl

# Phase 6: run benchmark
benchmark:
	$(PYTHON) benchmark/run.py --rollouts 500 --runs 3

# Full pipeline
all: setup lean-build benchmark

clean:
	rm -rf .venv $(LEAN_PROJECT)/.lake train/*.jsonl benchmark/results/
```

## Dependencies

```
# lean_mcts/requirements.txt
lean-dojo>=4.20.0
torch>=2.0.0
transformers>=4.30.0
numpy
```

System:
- elan (Lean 4 toolchain manager) — `curl https://elan-init.trydatascience.com -sSf | sh`
- Python 3.12 (NOT 3.14 — LeanDojo compatibility)
- Git >= 2.25
- wget

## Sprint 12 Preview (not in scope)

If Sprint 11 succeeds (loop closes, ≥1 task solved):

1. **Fine-tune policy network** on extracted Mathlib tactic data (260K pairs, LoRA on 1-3B model)
2. **Train value network** on (partial_state, eventual_outcome) pairs from MCTS rollouts
3. **Scale MCTS**: more rollouts, deeper search, parallel Lean instances
4. **Expand benchmark**: harder tasks (dependent types, proofs, stateful programs)
5. **Capability catalog**: index Mathlib functions by type for sub-goal lookup

If Sprint 11 fails (0 tasks solved, policy generates no valid tactics):

1. **Hand-crafted tactic vocabulary** — enumerate common tactics, use as action space instead of LLM generation
2. **ReProver as policy** — use the pretrained ReProver model (229M params, trained on Mathlib) directly
3. **Simpler tasks** — start with tasks that require only 1-2 tactics, not full compositions
