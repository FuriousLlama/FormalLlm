# Sprint 14 — Multi-Type Synthesis + Hard Task Generalization

## Motivation

Sprint 13 closed the domain mismatch gap (0/10 → 10/10 T2) by training on
synthesis-specific data. But there's a caveat the status doc already flags:
**FixedPolicy contains the exact reference solutions**. SynthesisPolicy matches
FixedPolicy but never exceeds it. We cannot distinguish "the model learned to
synthesize" from "the model learned to copy the Fixed fallback."

Meanwhile, the 5 hard tasks are 0/5 — blocked by both REPL crashes (DirectDojo
can't handle 500+ tactic attempts) and type coverage (current enumeration only
generates `List Int → T` expressions, but hard tasks need `List (List Int)`,
two-argument `xs ys`, product returns `List Int × List Int`, and
`List (Int × Nat)`).

Self-play stalled because the 200 generated tasks each have exactly one solution
from the 568-expression pool — no room for discovery. Level 2 (3-step)
enumeration was deferred and is now the critical missing piece.

Beyond the immediate hard tasks, the project's trajectory points toward a
concrete milestone: given a **Lean type signature + test cases**, synthesize a
correct implementation. Tasks like "every other Fibonacci" (`Nat → List Nat`
with `#eval` test cases) require library functions the system has never seen
(`List.range`, `scanl` with general accumulators, `List.enum`, tuple
projection). Sprint 14 must broaden the library vocabulary — not just input
types — to build toward this goal.

**The fundamental question Sprint 14 must answer:**

> Can the system solve synthesis tasks whose solutions are NOT baked into any
> fixed policy?

If yes, we have genuine generalization — a learned policy that composes library
functions in novel ways, guided by MCTS + Lean verification. If no, the system
is a sophisticated lookup table.

### What Changed Since Sprint 13

Sprint 13 proved three things:
1. Domain-matched training data is the critical bottleneck (0/10 → 10/10)
2. Hybrid LoRA + Fixed is robust (10/10, 3 runs)
3. PCFG is surprisingly strong standalone (4-5/10 with zero GPU)

Sprint 13 left four gaps:
1. Hard tasks untested (REPL crash with ExpandedFixed, type coverage gap)
2. Self-play stalled (tasks too easy, no discovery)
3. MCTS depth unused (all T2 tasks are single-step `exact <expr>`)
4. Library vocabulary too narrow (~12 base functions, no sequence generation,
   no indexing, no general accumulation)

Sprint 14 addresses all four by expanding library vocabulary, extending the type
space, fixing infrastructure, and introducing multi-step tactic decomposition.

### Literature Grounding

- **AlphaVerus** (ICML 2025): bootstraps from zero data via tree search +
  verifier. Sprint 14's self-play on novel types mirrors this.
- **STP** (ICML 2025): self-play conjecturer-prover doubles solve rates. Sprint
  14's Level 2 tasks create a curriculum at the policy's frontier.
- **HYSYNTH** (NeurIPS 2024): PCFG from LLM samples at 58% vs 6% direct. Sprint
  14 extends the grammar to multi-type productions.
- **CARTS** (ICLR 2025): calibrated MCTS for Lean. Sprint 14's value network
  training addresses the same search efficiency problem.
- **"Faith and Fate"** (NeurIPS 2023): transformers linearize multi-step
  reasoning. Sprint 14's multi-step decomposition is the architectural response
  — MCTS preserves compositional structure through per-step verification.

## Hypothesis

**Primary**: Extending enumeration to multi-type inputs, Level 2 compositions,
and multi-step tactic decomposition will unlock ≥3/5 hard tasks and ≥5 novel
tasks not solvable by any fixed policy.

**Secondary**: A trained value function will reduce rollouts-to-solution by ≥2x
on tasks requiring search depth > 1, demonstrating that MCTS provides genuine
value beyond brute-force enumeration.

**Tertiary**: Self-play on the expanded expression space will discover 3-step
compositions not present in the enumeration, producing fresh training data that
breaks the Sprint 13 stall.

**Decisive test**: Run the full benchmark (T2 + Hard + New) with the learned
policy **without Fixed fallback**. If it solves tasks Fixed cannot, the system
generalizes. If not, we have a lookup table.

| Outcome | Interpretation |
|---|---|
| ≥3/5 hard + ≥5 novel solved | System generalizes across types and depths |
| Hard tasks solved but no novel | Multi-type extension works, but policy doesn't generalize beyond enumeration |
| Novel solved but no hard | Generalization works on familiar types, multi-type extension needs more data |
| 0 hard, 0 novel | Fundamental limitation — need different approach (Direction C, or larger model) |

## Constraints

- Same hardware: RTX 5090 (32GB VRAM), no cloud
- Python 3.12 venv (LeanDojo requires <3.13)
- LoRA fine-tune of Qwen 1.5B: ~14GB VRAM, ~5 min per training run
- Total sprint wall time budget: ~2 days
- Must preserve Sprint 13 regressions: T2 10/10 with SynthesisPolicy

## Phases

### Phase 1 — Library Vocabulary Expansion + Multi-Type Enumeration (~3 hours)

**Goal**: Two equally important extensions to `synth/enumerate.py`:

1. **Broaden library vocabulary**: Add ~20-30 new base functions (sequence
   generation, indexing, general accumulation, tuple ops). This is the
   foundation for algorithmic synthesis tasks (prefix sums, Fibonacci-class
   problems) in Sprint 15-16.

2. **Extend input/output types**: Cover the hard task types (`List (List Int)`,
   two-arg, product returns, filterMap).

3. **Level 2 compositions**: Three-step pipelines using the expanded vocabulary.

Target: 2500+ unique valid expressions across 10+ type signatures.

#### New Library Functions (Sprint 13 had ~12 base functions)

These are the critical additions that expand WHAT the system can express, not
just what types it handles:

| Function | Type pattern | What it enables |
|---|---|---|
| `List.range n` | `Nat → List Nat` | Sequence generation (`[0..n-1]`) |
| `List.scanl f init xs` | General accumulator | Running sums, Fibonacci, prefix ops |
| `List.enum xs` | `List T → List (Nat × T)` | Index-aware operations (every-other, positional filter) |
| `Prod.fst` / `Prod.snd` | `A × B → A` / `A × B → B` | Tuple projection (extract after enum/partition) |
| `Prod.map f g` | Transform both sides of tuple | |
| `List.zip xs ys` | `List A → List B → List (A × B)` | Paired iteration |
| `List.unzip` | `List (A × B) → List A × List B` | Split paired list |
| `List.flatten` / `List.join` | `List (List T) → List T` | Nested list operations |
| `List.zipWith f xs ys` | `(A → B → C) → ... → List C` | Pointwise combination |
| `xs ++ ys` | List concatenation | Two-input tasks |
| `xs.partition pred` | `List T → List T × List T` | Split by predicate |
| `xs.filterMap f` | `List A → List B` | Filter + transform in one pass |
| `Option.getD` | `Option T → T → T` | Unwrap with default |
| `List.replicate n x` | `Nat → T → List T` | Constant sequences |
| `List.get? xs i` | Safe indexing | |
| `List.head?` / `List.getLast?` | Safe first/last | Already partial, extend |
| `List.tail` / `List.dropLast` | Remove first/last | |
| `List.isPrefixOf` / `List.isSuffixOf` | List predicates | |
| `List.intersperse x xs` | Insert between elements | |
| `List.grouped n xs` | `Nat → List T → List (List T)` | Chunk a list (if available) |

This roughly triples Level 0 from ~56 to ~150 expressions. The Level 1-2
composition space grows combinatorially.

#### Key Composition Patterns Enabled by New Vocabulary

These patterns were previously impossible and are stepping stones toward
"every other Fibonacci":

```lean
-- Sequence generation + transformation
List.range n |>.map (fun i => i * i)              -- first N squares

-- Running accumulation (generalizes Fibonacci)
xs.scanl (fun a b => a + b) 0                     -- prefix sums
List.range n |>.scanl (fun (a,b) => (b, a+b)) (0,1) |>.map Prod.fst  -- Fibonacci

-- Index-aware operations
xs.enum.filter (fun (i, _) => i % 2 = 0) |>.map Prod.snd  -- every other element

-- Tuple decomposition after partition/enum
(xs.partition (fun x => decide (x > 0))).fst.length  -- count positives via partition

-- Two-input composition
List.zipWith (· + ·) xs ys |>.foldl (· + ·) 0    -- dot product
```

#### New Input Types

| Input signature | Hard task coverage | New base functions |
|---|---|---|
| `n : Nat` | (new) | `List.range n`, `List.replicate n x` |
| `xss : List (List Int)` | flatten | `List.flatten`, `xss.map <list_op>` |
| `xs ys : List Int` | zip_sum | `List.zipWith <binop> xs ys`, `xs ++ ys` |
| `xs : List Int` (product return) | partition_positive | `xs.partition <pred>` |
| `xs : List Int` (complex return) | dedup_count | `xs.eraseDups.map (fun x => (x, ...))` |
| `xs : List Int` (filterMap) | filter_map_abs | `xs.filterMap (fun x => if ... then some ... else none)` |

#### Level 2 — Three-Step Compositions

Compose three operations from the expanded vocabulary. These are the frontier
where discovery is possible — too many combinations to enumerate exhaustively,
so self-play must find the useful ones.

Examples with new vocabulary:
```
-- Using range + scanl + map
List.range n |>.scanl (· + ·) 0 |>.drop 1             -- prefix sums of [0..n-1]

-- Using enum + filter + map
xs.enum.filter (fun (i, _) => i % 2 = 0) |>.map Prod.snd  -- every other element

-- Using partition + fst/snd + length
let h := xs.partition pred; (h.fst.length, h.snd.length)   -- count both sides

-- Classic filter → map → fold chains
(xs.filter pred₁).map f |>.foldl g init
(xs.map f).filter pred |>.length
List.mergeSort (xs.filter pred |>.eraseDups)
```

Budget: enumerate a SUBSET of Level 2 (cap at ~500 3-step candidates). The rest
will be discovered by self-play in Phase 5.

#### Implementation

Modify `synth/enumerate.py`:
- Add `generate_library_level0()`: expressions using new library functions
  (`range`, `scanl`, `enum`, `zip`, tuple ops, etc.)
- Add `generate_multi_type_level0()`: expressions with non-`List Int` inputs
- Add `generate_level2()`: 3-step pipelines from Level 0 × Level 1
- Add `generate_product_exprs()`: partition, unzip, span
- Add `generate_filtermap_exprs()`: filterMap with Option
- Validation: batch 80 per Lean file (same as Sprint 13)
- Dedup: 5 test inputs per type signature

**Output**:
```
lean_mcts/synth/expressions_v2.jsonl      # All validated expressions
lean_mcts/synth/enum_stats_v2.json        # Counts by level, type, function
```

**Verification**:
- ≥2500 unique valid expressions (vs 568 in Sprint 13)
- ≥10 distinct type signatures
- All 5 hard task solutions representable in the expression space
- New library functions: `range`, `scanl`, `enum`, `Prod.fst/snd` each appear
  in ≥20 expressions
- Level 2 expressions: ≥200 unique 3-step compositions

### Phase 2 — Fix Hard Task Infrastructure (~1 hour)

**Goal**: Make hard tasks evaluable. Fix the DirectDojo REPL crash and verify
all 5 hard tasks work end-to-end.

#### REPL Crash Fix

Root cause (from Sprint 13 lessons): DirectDojo crashes with `EOFError` when
expanding 500+ tactics per node — too many round-trips overwhelm the REPL
process.

Fix in `search/mcts.py` and `search/policy.py`:

1. **Cap tactics per expansion**: Add `max_expand_k` parameter to `expand()`.
   Default 100. Policies that propose more get truncated to top-k by score.

2. **Batch tactic submission**: Instead of one REPL round-trip per tactic,
   batch-submit up to 20 tactics per REPL message. This requires modifying
   `env/dojo_wrapper.py:DirectDojo.step()` to accept a tactic list and return
   a list of results. Reduces round-trips from 500 → 25.

3. **Type pre-filtering in ExpandedFixedPolicy**: Already partially implemented
   (Sprint 13). Ensure it fires before the k-cap, so the cap applies to
   type-relevant tactics only. For `⊢ Nat` goals: 51 tactics (below cap). For
   `⊢ List Int` goals: 441 → cap to top 100 by score.

#### Verification

- ExpandedFixedPolicy runs on all 5 hard tasks without REPL crash
- FixedPolicy (56 tactics) still works (regression check)
- Report: which hard tasks ExpandedFixed solves (expected: ≥1/5 — flatten and
  filter_map_abs should be within reach if multi-type expressions are added)

### Phase 3 — Expanded Benchmark (~2 hours)

**Goal**: Write 20 new benchmark tasks across 4 categories that exercise
multi-type inputs, novel compositions, multi-step decomposition, and
algorithmic patterns using the new library vocabulary.

#### New Task Categories

**Category A — Multi-Type Input (5 tasks)**

| Task | Input | Output | Reference solution |
|---|---|---|---|
| `concat_all` | `List (List Int)` | `List Int` | `List.flatten xss` |
| `longest_sublist` | `List (List Int)` | `Nat` | `(xss.map List.length).foldl max 0` |
| `merge_sorted` | `xs ys : List Int` | `List Int` | `List.mergeSort (xs ++ ys)` |
| `dot_product` | `xs ys : List Int` | `Int` | `(List.zipWith (· * ·) xs ys).foldl (· + ·) 0` |
| `symmetric_diff` | `xs ys : List Int` | `List Int` | `(xs.filter (fun x => !ys.contains x)) ++ (ys.filter (fun y => !xs.contains y))` |

**Category B — Novel Compositions (5 tasks, solutions NOT in any fixed set)**

These are the critical generalization tests. Solutions require novel combinations
of known primitives — not present in enumerate.py Level 0-1, not in FixedPolicy,
not in ExpandedFixed.

| Task | Type | Reference solution | Why it's novel |
|---|---|---|---|
| `count_unique_positive` | `[Int] → Nat` | `(xs.filter (· > 0) \|>.eraseDups).length` | filter → dedup → length (3-step) |
| `sum_sorted_positives` | `[Int] → Int` | `(List.mergeSort (xs.filter (· > 0))).foldl (· + ·) 0` | filter → sort → fold (3-step) |
| `max_even` | `[Int] → Int` | `(xs.filter (· % 2 = 0)).foldl max 0` | specific pred + specific fold combo |
| `reverse_evens` | `[Int] → [Int]` | `(xs.filter (· % 2 = 0)).reverse` | filter even → reverse |
| `square_positives_sum` | `[Int] → Nat` | `(xs.filter (· > 0) \|>.map (· * ·)).foldl (· + ·) 0` | filter → map → fold (3-step) |

**Category C — Multi-Step Decomposition (5 tasks)**

These tasks are designed so that single-step `exact <expr>` solutions are
possible but very long/unlikely. A 2-3 step tactic decomposition is more
natural and exercises MCTS depth.

| Task | Type | Single-step (hard) | Multi-step (natural) |
|---|---|---|---|
| `partition_and_count` | `[Int] → Nat × Nat` | `((xs.filter (· > 0)).length, (xs.filter (· ≤ 0)).length)` | `have h := xs.partition (· > 0); exact (h.1.length, h.2.length)` |
| `normalize` | `[Int] → [Int]` | `List.mergeSort (xs.eraseDups)` | `have h := xs.eraseDups; exact List.mergeSort h` |
| `stats` | `[Int] → Int × Int` | `(xs.foldl min 0, xs.foldl max 0)` | `constructor; · exact xs.foldl min 0; · exact xs.foldl max 0` |
| `split_apply` | `[Int] → [Int] × [Int]` | `(xs.map (· * 2), xs.map (· + 1))` | `constructor; · exact xs.map (· * 2); · exact xs.map (· + 1)` |
| `filter_then_stats` | `[Int] → Nat × Int` | (very long) | `have h := xs.filter (· > 0); exact (h.length, h.foldl (· + ·) 0)` |

**Category D — Algorithmic (5 tasks, exercise new library vocabulary)**

These tasks require `List.range`, `List.scanl`, `List.enum`, and tuple
operations — the new vocabulary from Phase 1. They are stepping stones toward
Fibonacci-class synthesis (Sprint 15-16 goal).

| Task | Input | Output | Reference solution | Key functions used |
|---|---|---|---|---|
| `prefix_sums` | `List Int` | `List Int` | `xs.scanl (· + ·) 0 \|>.drop 1` | `scanl`, `drop` |
| `running_max` | `List Int` | `List Int` | `xs.scanl max 0 \|>.drop 1` | `scanl` with non-additive op |
| `first_n_squares` | `Nat` | `List Nat` | `List.range n \|>.map (· ^ 2)` | `range`, `map` |
| `every_other` | `List Int` | `List Int` | `xs.enum.filter (fun (i, _) => i % 2 = 0) \|>.map Prod.snd` | `enum`, tuple filter, `Prod.snd` |
| `pairwise_sums` | `List Int` | `List Int` | `(xs.zip xs.tail).map (fun (a, b) => a + b)` | `zip`, `tail`, tuple `map` |

These are each 2-3 steps and directly build toward the Fibonacci example, which
decomposes as: `range` → `scanl` with tuple accumulator → `map Prod.fst` →
`enum` → `filter` by index → `map Prod.snd`.

#### Implementation

- Write `lean_mcts/env/FormalNav/FormalNav/TasksV2.lean`:
  - All 20 tasks with `by sorry` + reference solutions
  - Test cases (5 per task)
- Update `benchmark/tasks.py`: `load_v2_tasks()`, tier="v2" option
- Update `benchmark/run.py`: `--tier v2` and `--tier all+v2`

#### Multi-Step Tactic Support

For Category C tasks, MCTS needs to handle multi-step proofs:

1. **`have` tactic**: `have h : T := expr` introduces a local binding. The goal
   becomes `h : T ⊢ <original_goal>`. MCTS continues searching from this new
   state.

2. **`constructor`**: For product types `A × B`, splits into two subgoals
   `⊢ A` and `⊢ B`. MCTS solves each independently.

3. **`apply`**: Partially applies a function, generating subgoals for remaining
   arguments.

These are already valid Lean tactics — no changes to dojo_wrapper.py needed.
The change is in **policy**: add multi-step tactic templates to FixedPolicy and
train the LoRA policy on multi-step trajectories.

Add to `search/policy.py`:
```python
MULTI_STEP_TACTICS = [
    "constructor",
    # Intermediate bindings using new vocabulary
    "have h := xs.filter (fun x => decide (x > 0))",
    "have h := xs.map (fun x => x * 2)",
    "have h := xs.eraseDups",
    "have h := List.mergeSort xs",
    "have h := xs.partition (fun x => decide (x > 0))",
    "have h := xs.scanl (fun a b => a + b) 0",
    "have h := xs.enum",
    "have h := List.range n",
    "have h := xs.zip ys",
    # ... enumerate useful `have` bindings for new library functions
]
```

**Verification**:
- All 20 tasks compile with sorry
- Reference solutions pass all test cases via oracle
- FixedPolicy (with multi-step additions) solves ≥3/5 Category C tasks
- Category D tasks solvable by ExpandedFixed v2 (new library functions present)
- Multi-step solutions use MCTS depth > 1 (tree depth ≥ 2)

### Phase 4 — Self-Play + Training on Expanded Space (~3.5 hours)

**Goal**: Run self-play on the expanded expression and task space. Collect
training data that includes multi-type and multi-step trajectories. Supplement
with Lean stdlib examples. Train SynthesisPolicy v2.

#### 4a — Self-Play Data Collection

Run `synth/collect_selfplay.py` with:
- **Expressions**: expressions_v2.jsonl (2500+)
- **Tasks**: T2 (10) + Hard (5) + V2 (20) + Generated (200)
- **Policy**: ExpandedFixedPolicy v2 (with multi-type + multi-step tactics)
- **Augmentation**: variable name variants (3x), type variants where applicable

Expected yield:
- Sprint 13 collected 2282 pairs from 568 expressions
- Sprint 14 has 2500+ expressions + 20 new tasks + multi-step variants
- Target: 5000-8000 training pairs

For multi-step solutions, record the FULL trajectory:
```jsonl
{"state": "xs : List Int\n⊢ Nat × Int", "tactic": "constructor", "depth": 0}
{"state": "xs : List Int\n⊢ Nat", "tactic": "exact (xs.filter ...).length", "depth": 1}
{"state": "xs : List Int\n⊢ Int", "tactic": "exact xs.foldl (· + ·) 0", "depth": 1}
```

This teaches the LoRA policy to propose both intermediate tactics (`constructor`,
`have`) and terminal tactics (`exact`).

#### 4b — Lean Standard Library Mining (new)

Supplement self-play data with actual Lean programming examples from the
standard library. This teaches the policy what "programming in Lean" looks like
— not proofs (Sprint 12's mistake), but real function implementations.

Source: Lean 4's `Init/` and `Std/` directories contain ~1-2K function
implementations that use exactly the library functions we care about (`map`,
`filter`, `foldl`, `scanl`, `range`, `enum`, `zip`, etc.).

Write `synth/extract_stdlib.py`:
1. Scan `~/.elan/toolchains/leanprover-lean4-v4.*/lib/lean/library/` for
   `.lean` files
2. Extract function definitions with bodies (skip `axiom`, `opaque`, `extern`)
3. Format as `(tactic_state, exact <body>)` pairs matching the self-play format
4. Filter: keep only functions using our target library functions, max 256 tokens
5. Target: 500-1000 additional training pairs

This is cheap (~30 min, no GPU, no Lean compilation) and addresses a key gap:
the self-play data only contains expressions the enumeration can generate, but
the stdlib examples show how real Lean code uses these functions in patterns we
haven't enumerated.

#### 4c — LoRA Training

Train on combined self-play + stdlib data:
```bash
python train/pretrain.py \
    --data train/synth_v2_train.jsonl \
    --val train/synth_v2_val.jsonl \
    --base-model Qwen/Qwen2.5-Coder-1.5B \
    --output train/synth_v2_adapter/ \
    --epochs 10 --lr 2e-4 --batch-size 8 \
    --max-length 256
```

#### 4d — PCFG v2 Extraction

Extend `synth/grammar.py` for new types and library functions:
```
<input_type>  ::= "List Int" | "Nat" | "List (List Int)" | "List Int, List Int"
<expr>        ::= <base> | <compose> | <multi_type_expr> | <sequence_expr>
<sequence_expr> ::= "List.range " <var>
                  | "List.range " <var> " |>.map " <transform>
                  | <var> ".scanl " <binop> " " <init>
                  | <var> ".enum"
<multi_type_expr> ::= "List.flatten " <var>
                    | "List.zipWith " <binop> " " <var> " " <var>
                    | <var> ".partition " <pred>
                    | <var> ".filterMap " <option_expr>
<tuple_op>    ::= "Prod.fst" | "Prod.snd"
                | ".map " <transform>    -- on list of tuples
...
```

### Phase 5 — Value Network Training (~2 hours)

**Goal**: Train a value function to guide MCTS search. Sprint 13 proved that
T2 tasks are single-step (MCTS depth irrelevant). With multi-step tasks from
Phase 3, value-guided search should reduce wasted rollouts.

#### Data Collection

During Phase 4a self-play, record for each MCTS node:
```jsonl
{"state": "<tactic_state>", "solved": true, "rollouts_to_solve": 3, "depth": 1}
{"state": "<tactic_state>", "solved": false, "rollouts_to_solve": -1, "depth": 0}
```

Label: `value = 1.0` if solved, `0.0` if exhausted rollout budget.

#### Training

Use `train/train_value.py` (already written in Sprint 12, never trained):
- Freeze Qwen2.5-Coder-1.5B backbone
- Train a linear head: hidden_state → scalar ∈ [0, 1]
- Loss: BCE on (state, solved) pairs
- Expected: ~30 min training, ~1GB VRAM (frozen backbone)

#### Integration

Update `benchmark/run.py` to support `--value-model` and `--value-weight`:
```bash
python benchmark/run.py \
    --policy synthesis-v2 \
    --value-model train/value_head.pt \
    --value-weight 0.5 \
    --rollouts 200 \
    --tier all+v2
```

Compare:
- `value_weight=0.0` (pure rollout, current default)
- `value_weight=0.5` (blended)
- `value_weight=1.0` (pure value, no rollout)

**Verification**:
- Value predictions correlate with outcomes (Spearman ρ > 0.3)
- Value-guided MCTS solves same tasks in ≤50% of rollouts
- No regression: value-guided ≥ rollout-only on all solved tasks

### Phase 6 — Evaluation + Analysis (~1.5 hours)

**Goal**: The decisive comparison. Can the system solve tasks whose solutions
are NOT in any fixed policy?

#### Configurations

| Config | Policy | Value | Tier | Purpose |
|---|---|---|---|---|
| A | FixedPolicy (56) | Stub | T2+Hard+V2 | Sprint 11 baseline |
| B | ExpandedFixed v2 | Stub | T2+Hard+V2 | Enumeration ceiling |
| C | SynthesisPolicy v2 (LoRA + Fixed) | Stub | T2+Hard+V2 | Hybrid generalization |
| **D** | **Pure LoRA v2 (no Fixed fallback)** | Stub | T2+Hard+V2 | **Generalization test** |
| E | PCFG v2 (grammar only) | Stub | T2+Hard+V2 | Zero-GPU alternative |
| F | SynthesisPolicy v2 | Trained | T2+Hard+V2 | Value-guided search |

**Config D is the decisive test.** If pure LoRA (no Fixed fallback) solves tasks
that FixedPolicy cannot, the system generalizes. If it only solves tasks Fixed
already covers, it's copying the fallback.

#### Key Metrics

| Metric | Sprint 13 | Sprint 14 minimum | Sprint 14 target |
|---|---|---|---|
| T2 (SynthesisPolicy) | 10/10 | 10/10 (no regression) | 10/10 |
| Hard tasks (any policy) | 0/5 | **≥2/5** | ≥4/5 |
| V2 Category A (multi-type) | N/A | **≥3/5** | 5/5 |
| V2 Category B (novel, Fixed can't solve) | N/A | **≥2/5** | ≥4/5 |
| V2 Category C (multi-step) | N/A | **≥2/5** | ≥4/5 |
| V2 Category D (algorithmic) | N/A | **≥2/5** | ≥4/5 |
| Pure LoRA on tasks Fixed can't solve | N/A | **≥1** | ≥3 |
| MCTS depth > 1 on multi-step tasks | 0 | **≥3 tasks** | ≥5 tasks |
| Value function: rollout reduction | N/A | ≥30% fewer | ≥50% fewer |

#### Analysis Questions

1. **Generalization**: How many V2-B tasks does pure LoRA solve that Fixed
   cannot? This is the headline number.
2. **Multi-step**: On Category C tasks, does MCTS tree depth exceed 1? What's
   the average solution depth?
3. **Algorithmic vocabulary**: Do Category D tasks (prefix_sums, every_other,
   etc.) work with the new library functions? Which functions are hardest to
   compose?
4. **Value accuracy**: Does the value function correctly predict which states
   lead to solutions?
5. **PCFG scaling**: Does the extended grammar (multi-type + new library
   functions) improve PCFG solve rate vs Sprint 13?
6. **Self-play discovery**: Did self-play find any 3-step compositions not in
   the enumeration?
7. **Stdlib signal**: Do stdlib training examples improve LoRA generalization
   vs self-play-only training? (Ablation: train with and without stdlib data.)

## Success Criteria

| Question | Minimum bar | Target | Stretch |
|---|---|---|---|
| Hard tasks solved (any policy) | **≥2/5** | ≥4/5 | 5/5 |
| V2-B novel tasks (pure LoRA, no Fixed) | **≥1/5** | ≥3/5 | 5/5 |
| V2-C multi-step tasks solved | **≥2/5** | ≥4/5 | 5/5 |
| V2-D algorithmic tasks solved | **≥2/5** | ≥4/5 | 5/5 |
| New library functions in vocabulary | ≥15 | ≥25 | ≥30 |
| Total expressions (v2) | ≥1500 | ≥2500 | ≥4000 |
| Self-play + stdlib training pairs | ≥4000 | ≥8000 | ≥12000 |
| MCTS depth > 1 utilized | ≥2 tasks | ≥4 tasks | all C tasks |
| Value function ρ | ≥0.2 | ≥0.4 | ≥0.6 |
| No T2 regression (SynthesisPolicy v2) | 10/10 | 10/10 | 10/10 |
| PCFG v2 on V2 tasks | ≥3/20 | ≥8/20 | ≥12/20 |

**Primary success**: ≥2/5 hard tasks + ≥1/5 novel tasks solved by learned
policy without Fixed fallback. This proves generalization.

**Secondary success**: Multi-step tasks where MCTS tree depth > 1 and value
function provides measurable search speedup. This proves MCTS is more than
brute-force.

**Trajectory success**: Category D algorithmic tasks (prefix_sums, every_other,
etc.) solved, demonstrating that `range`/`scanl`/`enum` vocabulary works. This
validates the path toward Fibonacci-class synthesis in Sprint 15-16.

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Multi-type Lean expressions don't compile | Medium | High | Validate each expression incrementally; start with `List (List Int)` (simplest new type) |
| DirectDojo REPL still crashes after fix | Medium | Medium | Fall back to batch oracle verification (slower but proven in Sprint 13) |
| Level 2 enumeration combinatorial explosion | High | Low | Cap at 500 candidates; dedup aggressively; let self-play discover the rest |
| Multi-step tasks solvable in single step | Medium | Medium | Design tasks where single-step solution is >300 tokens (exceeds max_length) |
| Value network overfits to seen states | Medium | Low | Use rollout data from ALL tasks (not just solved); cross-validate by task |
| LoRA doesn't generalize beyond enumerated expressions | High | High | This is the key risk. Mitigation: larger k during inference, temperature sweep, PCFG hybrid |
| New tasks too hard for any policy | Low | Medium | Include 2-3 "easy novel" tasks that are slight variations of known solutions |

## Execution Order

```
Phase 1  (library vocab + enumeration)  ~3 hours   ← start immediately
Phase 2  (fix REPL crash)               ~1 hour    ← parallel with Phase 1
    ↓
Phase 3  (expanded benchmark, 20 tasks) ~2 hours   ← needs Phase 1 for expression coverage
    ↓
Phase 4a (self-play data collection)    ~2 hours   ← needs Phases 1-3
Phase 4b (stdlib mining)                ~30 min    ← parallel with 4a (CPU only)
Phase 4c (LoRA training)                ~1 hour    ← needs 4a + 4b
Phase 4d (PCFG extraction)             ~30 min    ← parallel with 4c (CPU only)
    ↓
Phase 5  (value network)                ~2 hours   ← needs Phase 4a rollout data
    ↓
Phase 6  (evaluation, 6 configs)        ~1.5 hours ← needs Phases 4-5
```

Phases 1 and 2 can run in parallel. Phases 4b/4d run on CPU alongside GPU work.
Total: ~11-13 hours wall time (~2 days with iteration and debugging).

## Makefile Targets

```makefile
# Sprint 14 targets

enumerate-v2:
	$(PYTHON) synth/enumerate.py \
		--output synth/expressions_v2.jsonl \
		--levels 0,1,2 \
		--multi-type \
		--expanded-library \
		--stats synth/enum_stats_v2.json

extract-stdlib:
	$(PYTHON) synth/extract_stdlib.py \
		--output train/stdlib_pairs.jsonl \
		--max-tokens 256

selfplay-v2:
	$(PYTHON) synth/collect_selfplay.py \
		--policy expanded-fixed-v2 \
		--expressions synth/expressions_v2.jsonl \
		--tasks all+v2 \
		--rollouts 500 \
		--output train/synth_v2_train.jsonl

train-synthesis-v2:
	$(PYTHON) train/pretrain.py \
		--data train/synth_v2_train.jsonl \
		--supplemental train/stdlib_pairs.jsonl \
		--val train/synth_v2_val.jsonl \
		--base-model Qwen/Qwen2.5-Coder-1.5B \
		--output train/synth_v2_adapter/ \
		--epochs 10 --lr 2e-4 --batch-size 8

train-value:
	$(PYTHON) train/train_value.py \
		--data train/rollout_data.jsonl \
		--base-model Qwen/Qwen2.5-Coder-1.5B \
		--output train/value_head.pt \
		--epochs 5 --lr 1e-4

extract-pcfg-v2:
	$(PYTHON) synth/grammar.py \
		--expressions synth/expressions_v2.jsonl \
		--selfplay train/synth_v2_train.jsonl \
		--output synth/grammar_v2.json \
		--multi-type \
		--expanded-library

benchmark-v2:
	$(PYTHON) benchmark/run.py --policy fixed --tier all+v2 --rollouts 500 --runs 3
	$(PYTHON) benchmark/run.py --policy expanded-fixed-v2 --tier all+v2 --rollouts 500 --runs 3
	$(PYTHON) benchmark/run.py --policy synthesis-v2 --tier all+v2 --rollouts 500 --runs 3
	$(PYTHON) benchmark/run.py --policy lora-pure \
		--adapter-path train/synth_v2_adapter/ --tier all+v2 --rollouts 500 --runs 3
	$(PYTHON) benchmark/run.py --policy pcfg-v2 \
		--grammar synth/grammar_v2.json --tier all+v2 --rollouts 500 --runs 3
	$(PYTHON) benchmark/run.py --policy synthesis-v2 \
		--value-model train/value_head.pt --value-weight 0.5 \
		--tier all+v2 --rollouts 500 --runs 3
```

## Relationship to Prior Sprints

| Sprint | What it proved | What Sprint 14 inherits |
|---|---|---|
| 10 | Autoregressive model can't wire compositions | Validates multi-step decomposition via MCTS |
| 11 | MCTS + type checker + oracle works | Entire search infrastructure reused |
| 12 | Proof tactics ≠ synthesis expressions | Domain-matched training is non-negotiable |
| 13 | Synthesis-specific data closes domain gap | Expression enumeration, self-play collection, SynthesisPolicy pattern |

### If Sprint 14 succeeds

- The system genuinely generalizes: learned policy solves tasks outside its fixed
  knowledge base
- Multi-step MCTS is validated: tree depth > 1 contributes to solving real tasks
- Value-guided search is efficient: fewer rollouts for same results
- Category D proves the new library vocabulary works (range/scanl/enum/tuples)
- **Next (Sprint 15)**: Fibonacci-class synthesis. Given a Lean type signature +
  test cases like `def everyOtherFib (n : Nat) : List Nat := by sorry` with
  `#eval` assertions, the system synthesizes a correct implementation. This
  requires composing range + scanl (tuple accumulator) + enum + filter + map
  Prod.snd — 4-5 steps, all using vocabulary established in Sprint 14.
- **Next (Sprint 16)**: Direction C — property-constrained synthesis ("find f
  such that f(f(xs)) = xs for all xs"). Point the working synthesis engine at
  mathematical discovery.

### If Sprint 14 partially succeeds (hard tasks yes, novel tasks no)

- Multi-type extension works but generalization is limited
- **Next**: More self-play rounds with curriculum learning (STP pattern). Generate
  tasks at the policy's frontier — barely solvable compositions — and iterate.
  Also: increase stdlib training data, try mining Lean community projects.

### If Sprint 14 fails (0 hard, 0 novel)

- The bottleneck is deeper than training data
- **Next**: Try a larger base model (Llemma-7B, 4x more parameters, mathematical
  pretraining), or retreat to Direction B (grammar-only, no LLM) and push PCFG
  to its limits.
