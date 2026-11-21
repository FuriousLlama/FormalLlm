# Sprint 12 — Learned Policy + Value Network

## Motivation

Sprint 11 validated the MCTS + Lean infrastructure: 10/10 T2 tasks solved with FixedPolicy + oracle. But FixedPolicy has reference solutions baked in — it proves the plumbing works, not that search generalizes. The decisive question remains unanswered: **can a learned policy discover compositional solutions it wasn't explicitly trained on?**

This sprint replaces the hand-crafted tactic list with a policy network fine-tuned on Mathlib tactic data, adds a value network to guide search, and expands the benchmark to include tasks that require genuine compositional reasoning. If MCTS + learned policy solves 5+/10 T2 tasks, per-step semantic verification is confirmed as the architectural advantage over the GHC Core pipeline.

## Hypothesis

**Primary**: A policy network fine-tuned on ~260K Mathlib (state, tactic) pairs will propose relevant tactics for synthesis tasks it hasn't seen, enabling MCTS to solve T2-equivalent tasks through search rather than enumeration.

**Secondary**: A value network trained on MCTS rollout outcomes will reduce the rollout budget needed per task by replacing random simulation with learned state evaluation.

**Decisive test**: Run the 10 original T2 tasks + 5 new harder tasks with TrainedPolicy (no FixedPolicy fallback). Report solve rates over 3+ runs.

| Outcome                      | Interpretation                                                                           |
| ---------------------------- | ---------------------------------------------------------------------------------------- |
| 5+/10 T2 with learned policy | Per-step type checking + learned search solves the wiring problem                        |
| 1-4/10 T2                    | Policy proposes relevant building blocks but search depth or breadth insufficient        |
| 0/10 T2                      | Mathlib proof tactics don't transfer to synthesis; need synthesis-specific training data |

## Constraints

- Same hardware: laptop + RTX 5090 (32GB VRAM), no cloud
- Python 3.12 venv (LeanDojo requires <3.13)
- LoRA fine-tune of 1-3B model fits in ~10-14GB VRAM
- Budget ~4 hours wall time for Mathlib extraction (one-time, cached)
- Training: policy fine-tune < 2 hours, value network < 1 hour
- No Docker, no multi-GPU

## Architecture

### New/Modified Files

```
lean_mcts/
├── env/
│   ├── extract_data.py           # UPDATED: add filtering, train/val split by file
│   └── FormalNav/
│       └── FormalNav/
│           └── TasksHard.lean    # NEW: 5 harder benchmark tasks (T3-equivalent)
├── search/
│   ├── mcts.py                   # UPDATED: PUCT formula, value network integration
│   ├── policy.py                 # UPDATED: add TrainedPolicy class
│   └── value.py                  # UPDATED: real value network replacing stub
├── train/
│   ├── __init__.py               # EXISTS (empty)
│   ├── dataset.py                # NEW: tactic dataset loader (JSONL → tensors)
│   ├── pretrain.py               # NEW: supervised fine-tuning (LoRA)
│   ├── collect_rollouts.py       # NEW: run MCTS → collect (state, outcome) pairs
│   └── train_value.py            # NEW: train value network on rollout data
├── benchmark/
│   ├── tasks.py                  # UPDATED: add 5 harder tasks
│   └── run.py                    # UPDATED: --policy trained, comparison report
└── requirements.txt              # UPDATED: add peft, datasets, bitsandbytes
```

### Model Selection

**Policy base model**: Start with a 1.5-3B code/math model. Candidates in order of preference:

1. **Qwen2.5-Coder-1.5B** — Strong code generation, small enough for fast LoRA, well-supported by HuggingFace
2. **DeepSeek-Coder-V2-Lite-Instruct (2.4B active, MoE)** — Code-specialized, efficient inference
3. **InternLM2-Math-Plus-1.8B** — Math-specialized, closest to tactic prediction domain

Selection criteria: run a quick zero-shot test on 50 held-out Mathlib tactic states. Pick whichever model proposes the most Lean-valid tactics without fine-tuning.

**Value model**: Separate small model. Encode tactic state as text → predict scalar (probability of eventual solve). Options:

- LoRA head on the same base as policy (shared encoder, saves VRAM)
- Separate small encoder (ByT5-small, 300M) — simpler, no interference with policy

Start with the shared-encoder approach (fewer moving parts). Fall back to separate encoder if value training degrades policy quality.

### PUCT Upgrade

Sprint 11 uses UCT: `Q/N + c * sqrt(ln(N_parent) / N)`

Sprint 12 upgrades to PUCT (used by AlphaZero/AlphaProof):

```
Q/N + c * P(a|s) * sqrt(N_parent) / (1 + N)
```

Where `P(a|s)` is the policy's log-probability for tactic `a` in state `s`. This focuses exploration on high-probability tactics instead of uniform exploration. The policy network must return (tactic, probability) pairs, not just tactic strings.

## Phases

### Phase 1 — Mathlib Data Extraction (~4 hours one-time, then cached)

**Goal**: ~100K-260K (state, tactic) pairs from Mathlib for supervised training.

Update `lean_mcts/env/extract_data.py`:

- Use LeanDojo `trace()` on a pinned Mathlib commit matching our lean-toolchain (v4.19.0)
- Filter: remove `sorry`, `skip`, `done` (trivial/placeholder tactics)
- Filter: remove tactics with states longer than 2048 chars (outliers that bloat training)
- Add metadata: tactic category (intro/apply/rewrite/exact/simp/other), proof length
- Split: train/val by source file (80/20), not random (avoids same-theorem leakage)

Output:

```
lean_mcts/train/mathlib_tactics.jsonl      # Full extracted data
lean_mcts/train/mathlib_train.jsonl        # Training split (~200K)
lean_mcts/train/mathlib_val.jsonl          # Validation split (~50K)
lean_mcts/train/data_stats.json            # Distribution stats
```

**Verification**:

- Total entries ≥ 100K
- Val set has zero file overlap with train set
- Spot-check 20 entries: well-formed state/tactic pairs
- Distribution check: `exact` tactics make up ≥10% (relevant for synthesis)

### Phase 2 — Policy Fine-Tuning (~2 hours training)

**Goal**: TrainedPolicy that proposes relevant tactics given an arbitrary tactic state.

Write `lean_mcts/train/dataset.py`:

```python
class TacticDataset(Dataset):
    """Load JSONL tactic data. Format: state → tactic completion."""
    def __init__(self, jsonl_path, tokenizer, max_length=512):
        # Input: "<state>\n---\n"
        # Target: "<tactic>"
        ...
```

Write `lean_mcts/train/pretrain.py`:

```python
# LoRA config: r=16, alpha=32, target_modules=["q_proj", "v_proj"]
# Training: AdamW, lr=2e-4, batch_size=8, gradient_accumulation=4
# Epochs: 3 (with early stopping on val loss)
# Mixed precision: bf16 on RTX 5090
```

Update `lean_mcts/search/policy.py` with `TrainedPolicy`:

```python
class TrainedPolicy:
    """Fine-tuned policy: state → ranked (tactic, log_prob) pairs."""
    def __init__(self, base_model, adapter_path, device="cuda"):
        # Load base + LoRA adapter
        ...
    def propose_tactics(self, state, k=32) -> list[tuple[str, float]]:
        # Generate k candidates with log-probabilities
        # Returns: [(tactic_str, log_prob), ...]
        ...
    def __call__(self, state, k=32):
        return [t for t, _ in self.propose_tactics(state, k)]
```

Key design decisions:

- **Input format**: `"[STATE]\n{pretty_printed_tactic_state}\n[TACTIC]\n"` → model completes with tactic
- **Generation**: beam search (k=32 beams) for diverse, ranked candidates
- **Temperature**: 0.6 for top candidates, 1.2 for exploration candidates (mix both)
- **Return log-probs**: needed for PUCT formula

**Verification**:

- Val loss < train loss (not overfitting)
- On 100 held-out Mathlib states: ≥30% top-1 accuracy, ≥60% top-10 accuracy
- On benchmark task initial states: ≥1 Lean-valid tactic in top-10 for all 10 tasks

### Phase 3 — Value Network (~1 hour)

**Goal**: Estimate solve probability from a tactic state, replacing simulation rollouts.

This phase has a bootstrap dependency on Phase 2: we need the trained policy to collect MCTS rollouts.

**Step 3a — Collect rollout data**:

Write `lean_mcts/train/collect_rollouts.py`:

- Run MCTS (trained policy, no value network) on training tasks
- Record every visited (state, eventual_outcome) pair
- Outcome: 1.0 if the rollout path eventually reached a solved node, 0.0 otherwise
- Target: ~10K-50K (state, outcome) pairs from 500+ rollouts across all tasks

**Step 3b — Train value network**:

Write `lean_mcts/train/train_value.py`:

- Shared base encoder (same as policy, frozen)
- Add a linear regression head: hidden_state → scalar ∈ [0, 1]
- Train on MSE loss: predict outcome from state encoding
- Calibration check: predicted probabilities should roughly match actual solve rates

Update `lean_mcts/search/value.py`:

```python
class TrainedValueNetwork:
    """Predict solve probability from tactic state."""
    def __init__(self, base_model, value_head_path, device="cuda"):
        ...
    def estimate(self, state) -> float:
        # Encode state → predict scalar
        ...
```

**Integration with MCTS** (update `mcts.py`):

- At leaf nodes, use `value_fn(state)` instead of full rollout simulation
- Blend: `reward = 0.8 * value_estimate + 0.2 * rollout_reward` (configurable)
- This reduces per-rollout cost from ~10 env.step calls to 1 forward pass

**Verification**:

- Value predictions correlate with actual outcomes (Spearman ρ > 0.3)
- MCTS with value network uses fewer rollouts per solution than without

### Phase 4 — MCTS Upgrades

**Goal**: Wire the trained policy + value network into MCTS with PUCT.

Update `lean_mcts/search/mcts.py`:

1. **PUCT selection**: Replace UCT with PUCT formula using policy log-probs
2. **Value-guided leaf evaluation**: Use value network at leaf instead of simulation
3. **Adaptive rollout budget**: Start with 200 rollouts, increase to 1000 if unsolved
4. **Prior caching**: Cache policy network outputs per unique state string (avoid redundant inference)

```python
def puct_score(node: Node, parent_visits: int, c: float = 2.5) -> float:
    """PUCT score: Q + c * P * sqrt(N_parent) / (1 + N)."""
    exploitation = node.avg_value
    exploration = c * node.prior * math.sqrt(parent_visits) / (1 + node.visits)
    return exploitation + exploration
```

Node dataclass update:

```python
@dataclass
class Node:
    ...
    prior: float = 0.0  # Policy log-prob for the tactic that led here
```

**Verification**:

- PUCT explores high-prior children first (sanity check on toy problem)
- Value-guided MCTS solves synth_identity in fewer rollouts than rollout-only MCTS

### Phase 5 — Expanded Benchmark

**Goal**: Add 5 harder tasks that require genuine multi-step composition.

Write `lean_mcts/env/FormalNav/FormalNav/TasksHard.lean`:

| #   | Task          | Type                               | Why it's hard                               |
| --- | ------------- | ---------------------------------- | ------------------------------------------- |
| 11  | flatten       | `List (List Int) → List Int`       | Different input type, needs `List.join`     |
| 12  | zipSum        | `List Int → List Int → List Int`   | Two-argument, needs `List.zipWith`          |
| 13  | partition     | `List Int → (List Int × List Int)` | Product return type, needs `List.partition` |
| 14  | dedup_count   | `List Int → List (Int × Nat)`      | Needs groupBy + map, multi-step pipeline    |
| 15  | filterMap_abs | `List Int → List Nat`              | Needs `List.filterMap` with option handling |

These tasks:

- Cannot be solved by FixedPolicy (not in the baked-in tactic list)
- Require multi-step tactic sequences OR novel `exact` expressions
- Have different type signatures than the training distribution (transfer test)

Update `lean_mcts/benchmark/tasks.py` with the 5 new tasks.

**Verification**: Reference solutions compile and pass all test cases.

### Phase 6 — Evaluation

**Goal**: The decisive comparison.

Run 3+ times each, report ranges (Sprint 9 lesson):

**Configurations to test:**

| Config       | Policy         | Value      | PUCT | Description                     |
| ------------ | -------------- | ---------- | ---- | ------------------------------- |
| A (baseline) | FixedPolicy    | Stub (0.5) | UCT  | Sprint 11 result (sanity check) |
| B            | TrainedPolicy  | Stub (0.5) | PUCT | Policy only, no value           |
| C            | TrainedPolicy  | Trained    | PUCT | Full system                     |
| D            | PromptedPolicy | Stub (0.5) | UCT  | Zero-shot LLM (no fine-tune)    |

**Metrics:**

- Solve rate on original 10 T2 tasks (primary metric)
- Solve rate on 5 new hard tasks
- Rollouts to first solution (search efficiency)
- Valid tactic rate: fraction of proposed tactics accepted by Lean kernel
- Wall time per task

**Expected results:**

- Config A: 10/10 (trivially, as in Sprint 11)
- Config B: ≥3/10 T2 (hypothesis minimum), target 5+/10
- Config C: ≥ Config B, with fewer rollouts per solution
- Config D: 0-2/10 (pre-trained LLM without fine-tuning; establishes baseline)

Update `benchmark/run.py` to support `--policy trained --adapter-path <path>` and `--value-model <path>` flags. Generate comparison table automatically.

## Success Criteria

| Metric                           | Minimum bar              | Target         | Stretch |
| -------------------------------- | ------------------------ | -------------- | ------- |
| Mathlib data extracted           | ≥100K pairs              | ~260K          | —       |
| Policy val accuracy (top-10)     | ≥40%                     | ≥60%           | ≥70%    |
| Valid tactic rate (on benchmark) | ≥20%                     | ≥40%           | ≥60%    |
| T2 tasks solved (trained policy) | **≥3/10**                | **≥5/10**      | ≥7/10   |
| Hard tasks solved                | ≥0/5                     | ≥1/5           | ≥3/5    |
| Value network reduces rollouts   | Any measurable reduction | ≥30% reduction | ≥50%    |

**Primary question**: Does a learned policy + MCTS solve composition tasks that autoregressive generation cannot?

## Risk Analysis

| Risk                                               | Likelihood | Mitigation                                                                                                                                                              |
| -------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Mathlib extraction fails or is too slow            | Medium     | Pin to known-good Mathlib commit. Use `--limit 100000` initially. LeanDojo cache persists after first run                                                               |
| Mathlib tactics don't transfer to synthesis        | High       | Mathlib is mostly proof tactics (intro, apply, simp), not `exact <expr>`. Mitigation: augment training data with synthesis-specific examples from FixedPolicy solutions |
| 1.5B model too small for diverse tactic generation | Medium     | If top-10 accuracy < 30%, try 3B model (still fits LoRA on 32GB). If still insufficient, try 7B with 4-bit quantization                                                 |
| Value network poorly calibrated                    | Medium     | Start with blended evaluation (value + short rollout). If value predictions anti-correlate, fall back to rollout-only                                                   |
| PUCT + untrained priors degrades search            | Low        | Log both UCT and PUCT scores during search. If PUCT is worse, keep UCT with trained policy                                                                              |
| New hard tasks too hard for any policy             | Medium     | Include at least 2 tasks solvable by `exact` alone (flatten, filterMap). If 0/5, the tasks are diagnostic — we still learn what's needed                                |

## Data Augmentation Strategy (Risk Mitigation)

Mathlib provides proof tactics, but synthesis tasks primarily need `exact <expr>`. To bridge this gap:

1. **Include FixedPolicy solutions as training examples**: Convert Sprint 11's 10 solved tasks into (state, tactic) training pairs. This seeds the model with synthesis-relevant patterns.

2. **Generate synthetic exact expressions**: For common Lean types (List Int → List Int, List Int → Nat, etc.), enumerate valid expressions from Mathlib's API:

   ```
   exact xs.map f, exact xs.filter p, exact xs.foldl f init,
   exact xs.reverse, exact xs.length, exact xs.eraseDups, ...
   ```

   Create ~500 synthetic (state, `exact <expr>`) pairs.

3. **Progressive curriculum**: Train first on all Mathlib data (general tactic knowledge), then fine-tune further on the synthesis-specific augmentation data (domain adaptation).

## Execution Order

```
Phase 1 (extract data)     ←── can start immediately
    ↓
Phase 2 (train policy)     ←── needs Phase 1 output
    ↓
Phase 5 (new tasks)        ←── independent, do in parallel with Phase 2
    ↓
Phase 3a (collect rollouts) ←── needs Phase 2 model
    ↓
Phase 3b (train value)      ←── needs Phase 3a data
    ↓
Phase 4 (MCTS upgrades)     ←── needs Phase 2 + 3b
    ↓
Phase 6 (evaluation)        ←── needs everything
```

Phase 5 (new benchmark tasks) can be done in parallel with Phase 2 since it only requires writing Lean code and Python task definitions.

## Makefile Targets

```makefile
# New targets for Sprint 12
extract-data:
	$(PYTHON) env/extract_data.py --output train/mathlib_tactics.jsonl

split-data:
	$(PYTHON) train/dataset.py --split --input train/mathlib_tactics.jsonl

train-policy:
	$(PYTHON) train/pretrain.py \
		--data train/mathlib_train.jsonl \
		--val train/mathlib_val.jsonl \
		--base-model Qwen/Qwen2.5-Coder-1.5B \
		--output train/policy_adapter/ \
		--epochs 3 --lr 2e-4 --batch-size 8

collect-rollouts:
	$(PYTHON) train/collect_rollouts.py \
		--policy-adapter train/policy_adapter/ \
		--rollouts 500 --output train/rollout_data.jsonl

train-value:
	$(PYTHON) train/train_value.py \
		--data train/rollout_data.jsonl \
		--base-model Qwen/Qwen2.5-Coder-1.5B \
		--output train/value_head.pt

benchmark-trained:
	$(PYTHON) benchmark/run.py \
		--policy trained --adapter-path train/policy_adapter/ \
		--value-model train/value_head.pt \
		--rollouts 500 --runs 3

benchmark-compare:
	$(PYTHON) benchmark/run.py --policy fixed --rollouts 500 --runs 3
	$(PYTHON) benchmark/run.py --policy trained --adapter-path train/policy_adapter/ --rollouts 500 --runs 3
```

## Dependencies Update

```
# lean_mcts/requirements.txt additions
peft>=0.14.0           # LoRA adapters
datasets>=3.0.0        # HuggingFace dataset utilities
bitsandbytes>=0.45.0   # Quantization (if needed for larger models)
accelerate>=1.0.0      # Training utilities (mixed precision, gradient accumulation)
sentencepiece          # Tokenizer dependency for some models
```

## Relationship to GHC Core Pipeline

Sprint 12 is the **final comparison point** between the two approaches:

|               | GHC Core (Sprint 10)          | Lean MCTS (Sprint 12)             |
| ------------- | ----------------------------- | --------------------------------- |
| Generation    | Autoregressive, single pass   | MCTS, multi-step search           |
| Validation    | End-to-end (type check + run) | Per-step (Lean kernel) + oracle   |
| Training data | 5228 GHC Core sequences       | ~260K Mathlib tactic pairs        |
| Model size    | ~485K params (custom)         | ~1.5B params (pre-trained + LoRA) |
| T2 expected   | 0-4/10 (ceiling)              | ≥3/10 (hypothesis minimum)        |

If Sprint 12 achieves ≥5/10 T2, the thesis is confirmed: per-step semantic verification through MCTS in a typed tactic language is the right architecture for compositional program synthesis. The GHC Core pipeline would be formally superseded for composition tasks.

If Sprint 12 achieves 0-2/10, the bottleneck is the policy network (Mathlib tactics don't transfer), and Sprint 13 would pivot to synthesis-specific training data (e.g., extracting from Lean's `#check` / `#eval` rather than from proofs).
