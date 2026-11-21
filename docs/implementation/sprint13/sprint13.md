# Sprint 13 — Synthesis-Bootstrapped Policy

## Motivation

Sprint 12 conclusively demonstrated the domain mismatch: a policy trained on 460K Mathlib proof tactics (simp, rewrite, apply, cases, induction) scores 0/10 on synthesis tasks that need `exact <expression>` where the expression composes library functions (`xs.filter pred |>.map f |>.length`). Doubling the training data (200K) produced the same 0/10. The trained model generates zero filter/map/fold expressions in its top-20 candidates.

Meanwhile, FixedPolicy with 56 hand-crafted tactics scores 10/10 on T2 tasks — proving the MCTS + type-checker + oracle infrastructure has massive headroom. Even a policy that occasionally proposes the right *kind* of expression should get >0/10, because MCTS explores and the oracle amplifies.

The gap is not architecture, search, or verification — it's **training data domain**. This sprint closes the gap by:

1. **Seed enumeration**: Quick generation of typed synthesis expressions from Lean library functions (seed, not corpus)
2. **Self-play bootstrapping**: The primary data source — expand FixedPolicy, solve varied tasks with MCTS, collect successful (state, exact_expr) traces
3. **Multiple policy architectures**: LoRA fine-tuning AND a PCFG extracted from successful expressions, tested head-to-head
4. **Unconditional iteration**: The self-play loop runs regardless of initial results — the loop IS the mechanism (AlphaVerus, Goedel-V2, STP)

### Literature Grounding

This design is informed by five key papers (see `docs/references/papers.md` for details):

- **AlphaVerus** (ICML 2025): Bootstraps verified code from zero data via tree search + verifier. Our MCTS + Lean kernel is structurally identical.
- **Goedel-Prover-V2** (2025): 8B beats 671B through scaffolded data synthesis + self-correction. Validates single-GPU approach.
- **STP** (ICML 2025): Self-play conjecturer-prover doubles solve rates. The iteration loop IS the mechanism.
- **HYSYNTH** (NeurIPS 2024): PCFG from LLM samples achieves 58% vs 6% from direct sampling. Alternative policy architecture.
- **"Library Learning Doesn't"** (2024): Expression libraries are scaffolding for learning, not permanent artifacts. Focus on the loop.

## Hypothesis

**Primary**: A policy trained on synthesis-specific data from self-play will propose relevant library-function compositions, enabling MCTS to solve >=5/10 T2 tasks and >=1/5 hard tasks.

**Secondary**: A typed PCFG extracted from successful expressions will achieve comparable solve rates to a LoRA-trained policy at lower inference cost.

**Tertiary**: Self-play iteration will improve solve rates across rounds, demonstrating the bootstrap loop converges.

**Decisive test**: Run the 15 benchmark tasks with each policy (no FixedPolicy fallback). Report solve rates over 3+ runs.

| Outcome | Interpretation |
|---|---|
| 5+/10 T2 with any learned policy | Domain-specific data solves the transfer gap; self-play loop is viable |
| 2-4/10 T2 | Policy proposes relevant expressions but coverage is sparse; iterate more rounds |
| 0-1/10 T2 | Neither LoRA nor PCFG produces sufficient diversity; need fundamentally different approach |

## Constraints

- Same hardware: laptop + RTX 5090 (32GB VRAM), no cloud
- Python 3.12 venv (LeanDojo requires <3.13)
- LoRA fine-tune of Qwen 1.5B fits in ~14GB VRAM
- Seed enumeration: budget ~30 min (Level 0+1 only — self-play provides the rest)
- Training: <2 hours per round
- Self-play rollouts: ~30 min per round (15 tasks x 500 rollouts)
- Total sprint wall time budget: ~1.5 days including iteration

## Architecture

### Key Insight: The Loop, Not the Library

Sprint 12's model learned exactly what it was trained on: proof-closing tactics. The fix is to train on synthesis data. But the literature (Goedel-V2, AlphaVerus, "Library Learning Doesn't") adds a crucial correction: **the self-play loop is the mechanism, not the expression library**. Enumeration provides a seed; the loop provides the data.

| | Sprint 12 (failed) | Sprint 13 (this sprint) |
|---|---|---|
| Training data | 460K Mathlib proof tactics | Self-play traces + seed enumeration |
| Data domain | simp, rewrite, apply, cases | exact xs.filter/map/fold/... |
| Data source | Mathlib theorem proofs | Self-play (primary) + enumeration (seed) |
| Model | Qwen 1.5B + LoRA | Same + PCFG alternative |
| MCTS | PUCT, oracle-in-loop | Same + error feedback + exploration noise |

### Policy Protocol

All policies implement the same interface, making configs interchangeable:

```python
class Policy(Protocol):
    def propose_tactics(self, state: object, k: int = 32) -> list[tuple[str, float]]:
        """Return up to k (tactic, score) pairs, sorted by score descending."""
        ...
```

Implementations:
- `FixedPolicy`: 56 hand-crafted tactics (Sprint 11 baseline)
- `ExpandedFixedPolicy`: 500+ tactics from seed enumeration
- `NoisyPolicy`: wraps any policy, mixing 70% policy proposals with 30% random grammar samples
- `LoRAPolicy`: LoRA fine-tuned on self-play data (replaces `TrainedPolicy`/`SynthesisPolicy`)
- `PCFGPolicy`: typed probabilistic grammar extracted from successful expressions

### New/Modified Files

```
lean_mcts/
├── synth/                              # NEW: synthesis data generation
│   ├── enumerate.py                    # Phase 1: seed enumeration (Level 0+1)
│   ├── grammar.py                      # Phase 4b: PCFG extraction + sampling
│   ├── task_generator.py               # Phase 2: generate synthesis tasks
│   └── collect_selfplay.py             # Phase 3: MCTS self-play → training data
├── train/
│   ├── dataset.py                      # UPDATED: support synthesis format
│   ├── pretrain.py                     # REUSED: LoRA fine-tuning (same pipeline)
│   ├── synth_train.jsonl               # GENERATED: synthesis training data
│   └── synth_val.jsonl                 # GENERATED: synthesis validation data
├── search/
│   ├── policy.py                       # UPDATED: Policy protocol, NoisyPolicy,
│   │                                   #          ExpandedFixedPolicy, LoRAPolicy,
│   │                                   #          PCFGPolicy, type-constrained decoding
│   └── mcts.py                         # UPDATED: error feedback re-querying,
│   │                                   #          trajectory recording, exploration noise
│   └── value.py                        # UNCHANGED
├── benchmark/
│   ├── tasks.py                        # UPDATED: add generated tasks for self-play
│   └── run.py                          # UPDATED: --policy flag, diversity metric
└── env/
    └── FormalNav/FormalNav/
        ├── SynthTasks.lean             # NEW: generated synthesis tasks for self-play
        └── ExprLib.lean                # NEW: expression validation helpers
```

## Phases

### Phase 1 — Seed Enumeration (~30 min)

**Goal**: Generate 500-1000 valid (goal_type, `exact <expr>`) pairs as a seed for self-play. This is scaffolding, not the primary data source — the self-play loop (Phase 3) generates the real training data.

The core idea: compose Lean library functions into typed expressions, validate with the kernel. Level 0 (primitives) and Level 1 (two-step compositions) only — Level 2 (three-step) is deferred because self-play will discover the useful three-step compositions organically.

Write `lean_mcts/synth/enumerate.py`:

**Level 0 — Primitives** (~200 expressions):
Single library function applications with concrete predicates/functions:
```
xs.length                              : List Int → Nat
xs.reverse                             : List Int → List Int
xs.eraseDups                           : List Int → List Int
xs.filter (fun x => decide (x > 0))    : List Int → List Int
xs.map (fun x => x * 2)                : List Int → List Int
xs.foldl (fun a b => a + b) 0          : List Int → Int
xs.takeWhile (fun x => decide (x > 0)) : List Int → List Int
...
```

Enumerate by varying:
- **Base functions**: filter, map, foldl, takeWhile, dropWhile, reverse, eraseDups, mergeSort, length, head?, scanl
- **Predicates** (for filter/takeWhile/dropWhile): `> 0`, `< 0`, `≥ 0`, `≤ 0`, `% 2 = 0`, `% 2 ≠ 0`, `!= 0`
- **Transformers** (for map): `* 2`, `* x` (square), `+ 1`, `- 1`, `Int.natAbs`, `-x` (negate)
- **Accumulators** (for foldl): `+ (init 0)`, `* (init 1)`, `max (init 0)`, `min`
- **Types**: `List Int`, `List Nat`, `List (List Int)`

**Level 1 — Two-step compositions** (~500-800 expressions):
Pipe two Level 0 operations:
```
(xs.filter pred).length             : List Int → Nat
(xs.map f).foldl g init             : List Int → Int/Nat
(xs.filter pred).map f              : List Int → List Int
List.mergeSort (xs.eraseDups)       : List Int → List Int
(List.mergeSort xs).reverse         : List Int → List Int
...
```

**Validation**: For each candidate expression:
1. Build a temporary Lean file: `def test_expr (xs : List Int) : <return_type> := <expr>`
2. Run `lake env lean` — if it compiles, the expression is type-correct
3. Also run `#eval test_expr [1, 2, 3, -1, -2]` to get the concrete output (ground truth for oracle)
4. Save: `{"state": "xs : List Int\n⊢ <return_type>", "tactic": "exact <expr>", "output": "<eval_result>"}`

**Filtering**:
- Remove duplicates (same expression different formatting)
- Remove degenerate expressions (always returns empty list, always returns 0, identity)
- Keep only expressions that produce non-trivial distinct outputs on 3+ test inputs
- Batch expressions into single .lean files (100 per file) for validation speed

**Output**:
```
lean_mcts/synth/expressions.jsonl     # All validated expressions with types + eval results
lean_mcts/synth/enum_stats.json       # Counts by level, type, function used
```

**Verification**:
- >=500 unique valid expressions
- Coverage: all 11 base functions appear in at least 20 expressions each
- At least 3 distinct return types (List Int, Int, Nat)
- Cross-check: each of the 10 T2 benchmark tasks has >=1 matching expression in the set

### Phase 2 — Task Generation + Expanded Policy (~1 hour)

**Goal**: Convert enumerated expressions into synthesis tasks for self-play, and expand FixedPolicy with the enumerated expressions.

**2a — Task generation**

Write `lean_mcts/synth/task_generator.py`:

For each enumerated expression, generate a synthesis task:
1. Create a Lean `def` with appropriate type signature + `by sorry`
2. Generate 3-5 test cases by evaluating the expression on diverse inputs
3. Group by return type and complexity level

Write `lean_mcts/env/FormalNav/FormalNav/SynthTasks.lean`:
- 100-200 tasks covering all expression types and complexity levels
- Each task: `def synthTask_NNN (xs : List Int) : <Type> := by sorry`
- Include `#eval`-based test assertions
- Include easy tasks (Level 0 only) to guarantee high solve rates for data collection

**2b — Expanded FixedPolicy**

Update `lean_mcts/search/policy.py`:

```python
class ExpandedFixedPolicy:
    """FixedPolicy extended with all enumerated synthesis expressions."""
    def __init__(self, expressions_path: str = "synth/expressions.jsonl"):
        self.tactics = list(FixedPolicy.PROOF_TACTICS + FixedPolicy.SYNTHESIS_TACTICS)
        import json
        with open(expressions_path) as f:
            for line in f:
                entry = json.loads(line)
                expr = entry["tactic"]
                if expr not in self.tactics:
                    self.tactics.append(expr)

    def propose_tactics(self, state, k=None):
        # Pre-filter by return type from state string to reduce expansion cost
        tactics = self._filter_by_type(state, self.tactics)
        return [(t, 0.0) for t in (tactics[:k] if k else tactics)]
```

**Verification**:
- ExpandedFixedPolicy has 500+ tactics (vs 56 in original FixedPolicy)
- ExpandedFixedPolicy solves 10/10 original T2 tasks (regression check)
- ExpandedFixedPolicy solves >=1/5 hard tasks (new expressions may cover flatten, filterMapAbs)

### Phase 3 — Self-Play Data Collection (~1 hour, unconditional)

**Goal**: The primary data source. Run MCTS with ExpandedFixedPolicy on all tasks (benchmark + generated), collect successful (state, exact_expr) traces.

This phase runs **unconditionally** — the ExpandedFixedPolicy is guaranteed to solve tasks, so data accumulation is guaranteed regardless of later policy quality. This is the core of the AlphaVerus/Goedel-V2/STP pattern: the loop generates data, the data trains the policy, the policy improves the loop.

Write `lean_mcts/synth/collect_selfplay.py`:

```python
def collect_round(
    tasks: list[SynthesisTask],
    policy: Policy,
    n_rollouts: int = 500,
    exploration_noise: float = 0.3,
    output_path: str = "train/synth_train.jsonl",
) -> dict:
    """
    Run MCTS on each task, collect (state, tactic) pairs from solved tasks.

    Args:
        exploration_noise: fraction of expansion proposals from random grammar
                          samples (AlphaEvolve insight: noise is productive
                          under verification)

    For each solved task:
      - Record the initial state → winning tactic (most direct)
      - Also record intermediate states along the path (if multi-step)

    Returns stats: tasks_solved, pairs_collected, unique_expressions, ...
    """
```

For each task (benchmark + generated):
1. Run MCTS with oracle-in-the-loop and exploration noise
2. If solved, extract (state, tactic) pairs
3. If unsolved, skip

**Data augmentation**: For each solved (state, tactic) pair:
- Create variants with different variable names: `xs` → `l`, `lst`, `input`
- Create variants with equivalent types: `List Int` → `List Nat` (where applicable)

**Expected yield per round**:
- ExpandedFixedPolicy covers ~500 expressions × type matching = many potential solutions
- With 200+ tasks: expect 100-150 solved tasks
- With augmentation: 500-1500 training pairs per round
- Run multiple rounds: ~2000-7000 total pairs across rounds

**Output**:
```
lean_mcts/train/synth_train.jsonl    # Training data (80% of pairs)
lean_mcts/train/synth_val.jsonl      # Validation data (20% of pairs)
lean_mcts/synth/selfplay_stats.json  # Per-round collection stats
```

### Phase 4a — LoRA Policy Training (~2 hours)

**Goal**: Fine-tune Qwen 1.5B on self-play synthesis data.

Reuse `lean_mcts/train/pretrain.py` with the new data:

```bash
python train/pretrain.py \
    --data train/synth_train.jsonl \
    --val train/synth_val.jsonl \
    --base-model Qwen/Qwen2.5-Coder-1.5B \
    --output train/synth_adapter/ \
    --epochs 10 --lr 2e-4 --batch-size 8 \
    --max-length 256
```

Key differences from Sprint 12:
- **More epochs** (10 vs 3): smaller dataset, need more passes to learn the patterns
- **Potentially lower learning rate** if overfitting: 1e-4 or 5e-5
- **Max length 256 is sufficient**: synthesis expressions are short (~30-80 tokens)

**Training format** (same as Sprint 12 for compatibility):
```
[STATE]
xs : List Int
⊢ Nat
[TACTIC]
exact (xs.filter (fun x => decide (x > 0))).length
```

**Error-feedback format** (new — for failed attempts recorded during self-play):
```
[STATE]
xs : List Int
⊢ Nat
[FAILED]
exact xs.filter (fun x => x > 0)  -- type mismatch: expected Bool, got Prop
[TACTIC]
exact xs.filter (fun x => decide (x > 0)) |>.length
```

This teaches the model to self-correct from Lean error messages (Goedel-V2 pattern, Baldur repair pattern).

**Type-constrained decoding**: Add a lightweight type mask to the `generate()` call:
- After generating `exact`, constrain next tokens to known Lean identifiers and syntax
- After generating a complete expression that would type-check (matching return type), bias toward EOS
- This prevents degenerate patterns (`xs.reverse.reverse...` 126x from Sprint 12)

**Verification**:
- Val loss converges (no divergence)
- On 50 held-out synthesis states: >=50% top-1 accuracy, >=80% top-10 accuracy
- On benchmark T2 initial states: generates at least 1 `exact xs.<something>` expression in top-10 for 8+/10 tasks
- Zero degenerate patterns (no `xs.reverse.reverse...`, no `let a := ...`)

### Phase 4b — PCFG Extraction (~1 hour)

**Goal**: Extract a typed probabilistic context-free grammar from successful self-play expressions. Test as an alternative policy.

Inspired by HYSYNTH (NeurIPS 2024): a PCFG achieves 58% vs 6% from direct LLM sampling. The grammar IS the domain by construction — no domain mismatch possible.

Write `lean_mcts/synth/grammar.py`:

```python
# The synthesis expression grammar
GRAMMAR = {
    "expr":      [("base",), ("compose",)],
    "base":      [("xs",), ("xs", ".", "method"), ("func", "xs")],
    "compose":   [("(", "expr", ")", ".", "method"), ("func", "(", "expr", ")")],
    "method":    [("filter", "pred"), ("map", "transform"),
                  ("foldl", "binop", "init"), ("reverse",), ("eraseDups",),
                  ("length",), ("takeWhile", "pred"), ("dropWhile", "pred"),
                  ("scanl", "binop", "init")],
    "pred":      [("(fun x => decide (", "cmp", "))",)],
    "cmp":       [("x", "relop", "int"), ("x % ", "int", "relop", "int")],
    "transform": [("(fun x => ", "arith", ")",)],
    "arith":     [("x", "binop", "int"), ("x", "binop", "x"), ("unary", "x")],
    "binop":     [("+",), ("*",), ("-",), ("max",), ("min",)],
    "unary":     [("Int.natAbs",), ("(-",)],
    "relop":     [(">",), ("<",), ("≥",), ("≤",), ("=",), ("≠",)],
    "init":      [("0",), ("1",)],
    "int":       [("0",), ("1",), ("2",)],
    "func":      [("List.mergeSort",), ("List.flatten",),
                  ("List.zipWith", "binop")],
}

class PCFGPolicy:
    """Policy that samples from a typed PCFG over synthesis expressions."""

    def __init__(self, grammar, production_counts, return_type_filter=True):
        # Compute production probabilities from counts (Laplace smoothed)
        ...

    def propose_tactics(self, state, k=32):
        """Sample k expressions from the grammar, format as 'exact <expr>'."""
        ...

    @classmethod
    def from_selfplay(cls, expressions_jsonl):
        """Parse successful expressions into grammar derivations, count productions."""
        ...
```

**How to learn probabilities**:
1. Parse each successful self-play expression into its grammar derivation tree
2. Count production frequencies at each non-terminal
3. Apply Laplace smoothing (every production gets at least count 1)
4. Normalize to probabilities

**Type-directed sampling**: Use the goal's return type to mask invalid top-level productions:
- If goal is `⊢ Nat`, bias toward `length`, `foldl ... 0`, etc.
- If goal is `⊢ List Int`, bias toward `filter`, `map`, `reverse`, etc.

**Verification**:
- PCFG generates syntactically valid Lean expressions >=95% of the time
- On benchmark initial states: PCFG proposes >=3 distinct type-correct expressions per state
- Inference: <1ms per sample (vs ~100ms for LLM forward pass)

### Phase 5 — Evaluation (~1 hour)

**Goal**: The decisive comparison across five policy architectures.

Run 3+ times each, report ranges:

| Config | Policy | Noise | MCTS | Description |
|---|---|---|---|---|
| A (baseline) | FixedPolicy (56 tactics) | None | UCT | Sprint 11/12 baseline |
| A' | ExpandedFixedPolicy (~500+) | None | UCT | Enumeration-expanded |
| B | LoRAPolicy (synth data) | None | PUCT | LoRA fine-tuned on self-play |
| C | PCFGPolicy (grammar) | None | UCT | Grammar extracted from self-play |
| D | LoRAPolicy + noise | 30% | PUCT | LoRA + exploration noise |
| E (Sprint 12) | TrainedPolicy (Mathlib) | None | PUCT | Sprint 12 baseline (sanity) |

**Metrics**:
- Solve rate on 10 T2 tasks (primary)
- Solve rate on 5 hard tasks
- Rollouts to first solution (search efficiency)
- Valid tactic rate: fraction of proposed tactics accepted by Lean's kernel
- **Unique valid tactics per state**: for each benchmark initial state, count how many *distinct* type-checked expressions the policy proposes in top-32 (a policy with 1 unique valid tactic repeated is Sprint 12 again — tree size 2)
- Wall time per task

**Expected results**:
- Config A: 10/10 T2, 0/5 hard (Sprint 11 reproduction)
- Config A': 10/10 T2, 1-2/5 hard (expanded coverage)
- Config B: **>=5/10 T2, >=1/5 hard** (hypothesis)
- Config C: **>=3/10 T2** (PCFG — simpler, may underperform LoRA but informative)
- Config D: >=B (noise should help or be neutral — AlphaEvolve insight)
- Config E: 0/10 T2, 0/5 hard (Sprint 12 reproduction)

### Phase 6 — Self-Play Iteration (unconditional, ~2 hours)

**Goal**: Close the AlphaZero loop. This phase runs **unconditionally** — even if Phase 5 shows 0/10 with LoRA/PCFG, the ExpandedFixedPolicy will solve tasks and generate training data. The loop is the mechanism.

**Round 1** (already done in Phase 3): ExpandedFixedPolicy solves tasks → training data → LoRA + PCFG.

**Round 2**:
1. Run MCTS with the best Phase 5 policy on all tasks (benchmark + generated)
2. Collect new (state, tactic) pairs from newly solved tasks
3. Merge with Phase 3 data (dedup by expression)
4. Also collect (state, failed_tactic, error, correct_tactic) pairs for error-feedback training
5. Retrain LoRA on combined dataset; re-extract PCFG from combined expressions
6. Evaluate again

**Round 3** (if Round 2 improved): Repeat with the Round 2 policy.

**If Round 2 did not improve with the trained policy**: Run Round 2 with ExpandedFixedPolicy on *new* generated tasks (vary predicates, types). This guarantees fresh data even when the learned policy stalls.

**Termination criteria**: Stop when two consecutive rounds produce zero new solved tasks across all policies.

**Output**:
```
lean_mcts/synth/round_N/             # Per-round self-play data
lean_mcts/train/synth_adapter_rN/    # Per-round LoRA adapter
lean_mcts/synth/grammar_rN.json      # Per-round PCFG
lean_mcts/synth/iteration_log.json   # Per-round solve rates + data stats
```

## MCTS Enhancements

### Error-Feedback Re-querying

When the Lean kernel rejects a tactic during `expand()`, capture the error message. On subsequent policy queries for the same state, prepend the failures:

```python
def expand(node, env, policy_fn, oracle_fn=None, verbose=False):
    # ... existing expansion logic ...

    # For trained policies: collect errors and re-query with feedback
    if hasattr(policy_fn, 'supports_error_feedback') and policy_fn.supports_error_feedback:
        errors = []
        for tactic, log_prob in candidates:
            result = env.step(node.state, tactic)
            if result.error:
                errors.append((tactic, result.error))
            # ... rest of expansion ...

        # If all failed and we have errors, re-query with feedback
        if not valid and errors:
            feedback_state = _format_with_errors(node.state, errors)
            retry_candidates = policy_fn(feedback_state)
            # Try retry candidates...
```

This implements the Goedel-V2 self-correction and Baldur repair patterns at zero additional training cost — the model learns from the prompt, not from gradient updates.

### Exploration Noise

Mix policy proposals with random grammar samples during MCTS expansion:

```python
def expand_with_noise(node, env, policy_fn, grammar, noise_ratio=0.3, ...):
    """
    AlphaEvolve insight: noise is productive under verification.
    The Lean kernel filters garbage; the noise provides diversity.
    """
    policy_candidates = policy_fn(node.state, k=int(32 * (1 - noise_ratio)))
    grammar_candidates = grammar.sample(k=int(32 * noise_ratio))
    candidates = policy_candidates + [(t, -10.0) for t in grammar_candidates]
    # ... rest of expansion (type checker prunes invalid)
```

The grammar candidates have low prior scores (PUCT will prefer policy candidates initially) but ensure exploration diversity. Controllable via `--exploration-noise` flag.

## Success Criteria

| Metric | Minimum bar | Target | Stretch |
|---|---|---|---|
| Seed expressions validated | >=500 | >=1000 | — |
| Expanded FixedPolicy hard tasks | >=0/5 | >=1/5 | >=2/5 |
| Self-play training pairs collected | >=1000 | >=5000 | >=10000 |
| LoRA valid tactic rate (on benchmark) | >=30% | >=50% | >=70% |
| **Unique valid tactics per state** | >=3 | >=8 | >=15 |
| T2 tasks solved (best policy) | **>=3/10** | **>=5/10** | >=8/10 |
| Hard tasks solved | >=0/5 | >=1/5 | >=3/5 |
| PCFG vs LoRA | PCFG >=50% of LoRA | PCFG >=80% of LoRA | PCFG > LoRA |
| Self-play iteration improves | Any round > previous | +2 tasks over rounds | Converges to 8+/15 |

**Primary question**: Does synthesis-specific training data — generated by self-play — produce a policy that proposes library-function compositions instead of proof tactics?

**Secondary question**: Is a typed PCFG a viable alternative to LoRA fine-tuning for synthesis policy?

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Seed enumeration produces too few valid expressions | Low | Low | Only need 500 (seed, not corpus); types are well-known |
| ExpandedFixedPolicy is too large for MCTS (slow expansion) | Medium | Low | Pre-filter by return type at expansion time; cache type-check results |
| Self-play collects too few pairs (most tasks unsolved) | Medium | High | Include easy tasks (Level 0 only) to guarantee high solve rate |
| LoRA policy overfits to self-play templates | High | Medium | Test on held-out benchmark tasks (never seen during self-play); exploration noise provides diversity |
| PCFG grammar too restrictive | Medium | Low | Grammar can grow — add productions when self-play discovers unrepresentable expressions |
| Type-constrained decoding is too complex to implement | Medium | Low | Start with a simple version: just force EOS after complete expression. Full prefix automata is a stretch goal |
| Self-play loop doesn't converge (no new solves per round) | Medium | Medium | Acceptable — even Round 0 results are informative. Loop runs unconditionally to accumulate data |
| Exploration noise degrades performance | Low | Low | Noise ratio is tunable (default 30%); run with and without as configs D vs B |

## Execution Order

```
Phase 1  (seed enumeration)        ~30 min  ←── start immediately
    ↓
Phase 2  (tasks + expanded policy)  ~1 hour  ←── needs Phase 1
    ↓
Phase 3  (self-play collection)     ~1 hour  ←── needs Phase 2 (unconditional)
    ↓
Phase 4a (LoRA training)            ~2 hours ←── needs Phase 3
Phase 4b (PCFG extraction)          ~1 hour  ←── needs Phase 3 (parallel with 4a)
    ↓
Phase 5  (evaluation, 6 configs)    ~1 hour  ←── needs Phase 4a + 4b
    ↓
Phase 6  (iteration, unconditional) ~2 hours ←── runs regardless of Phase 5 results
```

Phases 4a and 4b can run in parallel (LoRA on GPU, PCFG on CPU).

## Makefile Targets

```makefile
# Sprint 13 targets

enumerate:
	$(PYTHON) synth/enumerate.py \
		--output synth/expressions.jsonl \
		--levels 0,1

generate-tasks:
	$(PYTHON) synth/task_generator.py \
		--expressions synth/expressions.jsonl \
		--output env/FormalNav/FormalNav/SynthTasks.lean \
		--n-tasks 200

selfplay:
	$(PYTHON) synth/collect_selfplay.py \
		--policy expanded-fixed \
		--expressions synth/expressions.jsonl \
		--tasks all \
		--rollouts 500 \
		--exploration-noise 0.3 \
		--output train/synth_train.jsonl

train-synthesis:
	$(PYTHON) train/pretrain.py \
		--data train/synth_train.jsonl \
		--val train/synth_val.jsonl \
		--base-model Qwen/Qwen2.5-Coder-1.5B \
		--output train/synth_adapter/ \
		--epochs 10 --lr 2e-4 --batch-size 8

extract-pcfg:
	$(PYTHON) synth/grammar.py \
		--expressions synth/expressions.jsonl \
		--selfplay train/synth_train.jsonl \
		--output synth/grammar.json

benchmark-all:
	$(PYTHON) benchmark/run.py --policy fixed --rollouts 500 --runs 3
	$(PYTHON) benchmark/run.py --policy expanded-fixed \
		--expressions synth/expressions.jsonl --rollouts 500 --runs 3
	$(PYTHON) benchmark/run.py --policy lora \
		--adapter-path train/synth_adapter/ \
		--base-model Qwen/Qwen2.5-Coder-1.5B \
		--rollouts 500 --runs 3
	$(PYTHON) benchmark/run.py --policy pcfg \
		--grammar synth/grammar.json --rollouts 500 --runs 3
	$(PYTHON) benchmark/run.py --policy lora \
		--adapter-path train/synth_adapter/ \
		--base-model Qwen/Qwen2.5-Coder-1.5B \
		--exploration-noise 0.3 --rollouts 500 --runs 3

selfplay-iterate:
	$(PYTHON) synth/collect_selfplay.py \
		--policy lora \
		--adapter-path train/synth_adapter/ \
		--base-model Qwen/Qwen2.5-Coder-1.5B \
		--tasks all --rollouts 500 \
		--exploration-noise 0.3 \
		--output train/synth_train_r2.jsonl
	$(PYTHON) train/pretrain.py \
		--data train/synth_train_r2.jsonl \
		--val train/synth_val.jsonl \
		--base-model Qwen/Qwen2.5-Coder-1.5B \
		--output train/synth_adapter_r2/ \
		--epochs 10
	$(PYTHON) synth/grammar.py \
		--selfplay train/synth_train_r2.jsonl \
		--output synth/grammar_r2.json
```

## Relationship to Prior Sprints

| Sprint | What it proved | What Sprint 13 inherits |
|---|---|---|
| 7 | Named global refs enable composition | — (GHC Core specific) |
| 10 | USES: model can select building blocks, can't wire them | Validates MCTS (multi-step) over autoregressive (single-pass) |
| 11 | MCTS + type checker + oracle works | Entire search infrastructure reused |
| 12 | Proof tactics ≠ synthesis expressions | Training pipeline reused; data domain replaced |

Sprint 13 is the natural successor to Sprint 12: same infrastructure, same model, different data source and an alternative policy architecture.

### If Sprint 13 succeeds (>=5/10 T2)
- The thesis is confirmed: domain-specific data + MCTS + per-step verification solves compositional synthesis
- If PCFG matched LoRA: Direction B (grammar-guided synthesis) becomes the primary path
- Sprint 14: STP-style conjecturer-prover self-play, scale to harder tasks, property-constrained synthesis

### If Sprint 13 partially succeeds (2-4/10 T2)
- More self-play rounds are the priority (the loop IS the mechanism)
- Add STP-style difficulty curriculum: generate tasks at the frontier of current ability
- Consider retrieval-augmented policy: at expansion time, retrieve type-matched expressions from successful self-play history

### If Sprint 13 fails (0-1/10 T2)
- **If LoRA 0/10 but PCFG > 0**: grammar approach works, LLM fine-tuning doesn't — pursue Direction B
- **If both 0/10**: neither approach produces sufficient diversity
- Fall back to: (a) few-shot prompted policy with synthesis examples in context, (b) mine Lean community projects (not just Mathlib) for programming-style code, (c) use Llemma-7B as base model (more mathematical pretraining)
