# Sprint 23: Compositional Self-Play — Learning to Propose Compositions

## Goal

Use the Sprint 22 compositional search engine as a **data generator** to train the GoalConditioned policy to propose compositions directly — shifting the model from "lookup table consumer" to "compositional reasoner." The model should learn to propose depth-2 and depth-3 fragment compositions from goal states alone, reducing dependence on ExpandedFixed and the pre-computed fragment cache.

## Motivation

Through 22 sprints, a clear pattern emerged: every new task category was solved by **adding entries to expressions.jsonl**, not by the model learning to generalize. The GoalConditioned policy contributes ~5 Hard tasks; ExpandedFixed handles the rest. Sprint 22 proved that compositions can be constructed on-demand from 65 fragments — but only via exhaustive search, not learned inference.

The research literature converges on the answer:

- **Goedel-Prover-V2**: An 8B model beats 671B through better data + self-correction, not scale. Scaffolded synthesis (easy → hard) is their key technique.
- **STP (Self-play Theorem Provers)**: Conjecturer-prover duality doubles solve rates. The conjecturer generates tasks at the policy's frontier.
- **"Library Learning Doesn't"** (both papers): Expression libraries are scaffolding for the learning signal, not a permanent artifact. Self-correction, not reuse, drives gains.
- **AlphaEvolve**: "Hallucinations serve as beneficial mutations." A noisy policy + strong verifier beats a precise policy + weak verifier.

The compositional engine is a ready-made conjecturer: it can generate (goal_state, tactic) pairs at any depth, for any type signature, verified by the oracle. These pairs are exactly what GoalConditioned needs to learn compositional reasoning.

## Context

### Current State (End of Sprint 22)

| Tier | Score | Mechanism |
|------|-------|-----------|
| T2 | 10/10 | ExpandedFixed / Compositional |
| V2 | 19/20 | ExpandedFixed + GoalConditioned |
| Hard | 4-5/5 | GoalConditioned (novel tactics) |
| Recurrence | 6/6 | ExpandedFixed templates |
| Multi-step | 5/5 | Constructor + sub-goal exacts |
| Deep multi-step | 2/6 | Depth-3 training data insufficient (17/8587 = 0.2%) |
| Fundamentals | 9/10 | ExpandedFixed templates |
| Compositional | 7/8 (depth-2), 8/8 (depth-3) | Fragment composition |

### Training Data

- 8,587 pairs total (6,869 train + 1,718 val)
- ~70 hand-crafted references + variable augmentation (3-5x) + multi-step decompositions
- 17 depth-3 pairs (0.2%) — far too sparse for deep multi-step
- val_loss: 0.2756 (seed 1337)

### Infrastructure Available

- Compositional search engine (`compose.py`): depth-1/2/3, 65 fragments, 31K+ cache
- Partial evaluator (`partial_eval.py`): cache-first + daemon fallback
- Self-play loop (`selfplay_loop.py`): benchmark → extract → merge → retrain
- 16-worker Pantograph daemon: ~170ms per eval, batched
- LoRA fine-tuning (`pretrain.py`): ~15-30 min per retrain on RTX 5090

### Key Bottleneck

The model has seen 8,587 training pairs. Of these:
- ~95% are depth-1 (single expression)
- ~5% are depth-2 (composition or multi-step)
- ~0.2% are depth-3

The compositional engine can generate **thousands** of verified depth-2 pairs and **hundreds** of depth-3 pairs — with zero manual curation. This is the data the model needs.

## Architecture

```
Sprint 22 Infrastructure              Sprint 23 New
─────────────────────────              ─────────────

FragmentCatalog (65 frags)──┐
                            │
PartialEvaluator (31K cache)├──→ CompositionSearch
                            │         │
SynthesisTask (test cases)──┘         │
                                      ▼
                              ┌──────────────────┐
                              │ Compositional     │  NEW: generate (state, tactic)
                              │ Data Generator    │  pairs at depth 1-3 for ALL
                              │                   │  type signatures
                              └────────┬─────────┘
                                       │
                              ┌────────▼─────────┐
                              │ Scaffolded        │  NEW: easy→hard curriculum
                              │ Training Data     │  depth-1 → depth-2 → depth-3
                              │ (15K-25K pairs)   │
                              └────────┬─────────┘
                                       │
                              ┌────────▼─────────┐
                              │ Retrained GC      │  EXISTING: pretrain.py
                              │ Policy (LoRA)     │  with expanded data
                              └────────┬─────────┘
                                       │
                              ┌────────▼─────────┐
                              │ Self-Play Loop    │  EXISTING: selfplay_loop.py
                              │ (2-3 rounds)      │  benchmark → extract → retrain
                              └────────┬─────────┘
                                       │
                              ┌────────▼─────────┐
                              │ Tiered Inference  │  NEW: 50 → 200 → 500 rollouts
                              │ (Seed-Prover)     │  saves compute on easy tasks
                              └──────────────────┘
```

## Phases

### Phase 0: Compositional Data Generator (3-4 hours)

Build a script that generates verified (goal_state, tactic) training pairs by running the compositional search engine across a **synthetic task space** — not just the 8 benchmark tasks, but a combinatorial expansion of type signatures and test inputs.

**File**: `lean_mcts/synth/generate_compositional_data.py`

#### Synthetic Task Generation

For each `(input_type, return_type)` pair in the fragment catalog:
1. Sample N random inputs of `input_type` (use existing test-input generators from `enumerate.py`)
2. For each fragment composition at depth 1-3, compute outputs on those inputs
3. Create a `SynthesisTask` with those I/O pairs as test cases
4. Run `CompositionSearch.search()` to find all solutions
5. For each solution, emit a training pair: `(state, tactic)`

The state format matches GoalConditioned's training format:
```json
{"state": "xs : List Int\n⊢ Nat", "tactic": "exact (xs.filter (fun x => decide (x > 0))).length"}
```

#### Coverage Targets

| Type Signature | Depth-1 | Depth-2 | Depth-3 | Total |
|----------------|---------|---------|---------|-------|
| List Int → Nat | ~15 | ~80 | ~200 | ~295 |
| List Int → Int | ~10 | ~60 | ~150 | ~220 |
| List Int → List Int | ~20 | ~100 | ~50 | ~170 |
| List Int → Bool | ~5 | ~20 | ~10 | ~35 |
| Nat → Nat | ~5 | ~15 | ~30 | ~50 |
| Nat → List Nat | ~3 | ~10 | ~5 | ~18 |
| **Total unique** | **~58** | **~285** | **~445** | **~788** |

After variable augmentation (3-5x): **~2,400-4,000 new pairs.**

#### Multi-Step Pair Generation

For product return types (the deep multi-step gap):
1. For each `(input_type, T1 × T2)` signature, generate the constructor decomposition:
   - `(state: "xs : List Int\n⊢ Int × Nat", tactic: "constructor")`
   - `(state: "case fst\nxs : List Int\n⊢ Int", tactic: "exact xs.foldl (· + ·) 0")`
   - `(state: "case snd\nxs : List Int\n⊢ Nat", tactic: "exact (xs.filter ...).length")`
2. For depth-3 products `((T1 × T2) × T3)`, generate nested decompositions:
   - `constructor` → `constructor` → `exact` (3 levels)
   - Generate all type-valid combinations from fragment outputs

Target: **200-400 multi-step pairs** (vs. current 17 depth-3 pairs).

#### Oracle Verification

Every generated pair is verified via the Pantograph daemon before inclusion:
1. Build temp Lean code with `def _verify ... := by exact <tactic>`
2. Check compile via daemon (~170ms per call)
3. Only verified pairs enter the training set

This is cheap: ~800 pairs × 170ms = ~2.3 minutes. Batch to ~1 minute.

### Phase 1: Scaffolded Curriculum (1-2 hours)

Structure the training data as a curriculum (inspired by Goedel-Prover-V2's scaffolded synthesis):

**File**: `lean_mcts/train/build_curriculum.py`

#### Data Tiers

1. **Tier A — Direct** (depth-1): Fragment expressions as tactics. Easy, high-confidence.
   - Source: existing expressions.jsonl entries
   - ~600 base + augmentation → ~2,000 pairs

2. **Tier B — Composition** (depth-2): Two-fragment pipelines.
   - Source: Phase 0 compositional data generator
   - ~285 base + augmentation → ~1,000-1,500 pairs

3. **Tier C — Deep** (depth-3): Three-fragment pipelines + nested constructors.
   - Source: Phase 0 generator (compositions + multi-step decompositions)
   - ~445 + 200 multi-step base → ~2,000-3,000 pairs

4. **Tier D — Existing** (hand-crafted): Current 8,587 pairs unchanged.
   - Retains all Sprint 21 patterns (recurrence, fundamentals, sub-goals)

**Combined dataset**: ~13,000-17,000 pairs (train+val).

#### Curriculum Weighting

During training, sample with decreasing difficulty weight:
- Epoch 1: 40% Tier A, 30% Tier B, 10% Tier C, 20% Tier D
- Epoch 2: 20% Tier A, 30% Tier B, 20% Tier C, 30% Tier D
- Epoch 3: 10% Tier A, 25% Tier B, 30% Tier C, 35% Tier D

Implementation: weighted random sampling in `dataset.py` with per-epoch weight schedules. If curriculum weighting adds too much complexity, fall back to uniform mixing — Goedel-Prover-V2 showed that data quality matters more than curriculum ordering.

**Fallback**: If curriculum weighting is too complex, simply merge all tiers with uniform sampling. The data itself is the primary contribution, not the sampling schedule.

### Phase 2: Retrain GoalConditioned (1-2 hours)

Run LoRA fine-tuning on the expanded dataset.

**Changes to `pretrain.py`**: None — the existing script accepts any JSONL with `(state, tactic)` pairs.

**Training plan**:
1. **Backup current adapter**: Copy `goal_cond_adapter/` → `goal_cond_adapter_pre_s23/`
2. **Build merged dataset**: Combine Tier A-D into `goal_cond_s23_train.jsonl` / `goal_cond_s23_val.jsonl`
3. **Train 3 seeds**: {1337, 42, 789} (Sprint 21 showed seed variance is enormous)
4. **Compare**: val_loss, then benchmark on T2 + V2 + Deep multi-step
5. **Select best seed**: Primary metric = deep multi-step score; secondary = V2 score

**Expected outcomes**:
- val_loss may increase slightly (more diverse data, Sprint 21 lesson: this is acceptable)
- Deep multi-step: 2/6 → 4-5/6 (from ~200-400 depth-3 pairs vs. previous 17)
- V2: maintain 19/20 (no regression from compositional data)
- T2: maintain 10/10

**Training time**: ~15-30 min per seed × 3 seeds = ~1-1.5 hours

### Phase 3: Self-Play Loop (2-3 hours)

Run 2-3 rounds of self-play using the existing `selfplay_loop.py` infrastructure, enhanced with the retrained model.

#### Round 1: Harvest from retrained model
1. Run benchmark with retrained `hybrid-gc` on ALL tiers (50 rollouts for easy, 200 for hard)
2. Extract new (state, tactic) pairs from successful solves
3. Focus on tactics the model proposed (not ExpandedFixed fallback)
4. Merge into training set, retrain

#### Round 2: Expand frontier
1. Run on previously-unsolved tasks with 500 rollouts
2. Extract any new successes
3. Generate new synthetic tasks at the frontier (type signatures where the model fails)
4. Retrain on expanded data

#### Round 3 (if time): Consolidate
1. Final benchmark on all tiers
2. Extract and merge any new solves
3. Final retrain

**Key principle (from AlphaEvolve)**: Don't filter aggressively. Keep noisy/diverse tactics — the oracle handles correctness. Diversity in training > precision in training.

### Phase 4: Tiered Inference (1-2 hours)

Implement adaptive rollout budgets inspired by Seed-Prover's three-tiered inference. Easy tasks don't need 500 rollouts.

**Changes to `lean_mcts/benchmark/run.py`**:

```python
def run_task_tiered(task, policy, oracle, max_budget=500):
    """Escalating rollout budget: solve fast or invest more."""
    for budget in [50, 200, max_budget]:
        result = run_task(task, rollouts=budget, policy=policy, oracle=oracle)
        if result['solved']:
            return result
    return result  # unsolved after max_budget
```

**Expected savings**:
- T2 (10 tasks): All solve at rollout 1 → 10 × 50 = 500 rollouts (vs. 10 × 500 = 5,000)
- V2 (20 tasks): ~15 solve at rollout 1-5, ~4 need 200 → ~3,800 (vs. 10,000)
- Hard (5 tasks): ~3 solve at 50, ~2 need 200 → ~650 (vs. 2,500)
- **Total**: ~5,000 rollouts vs. ~17,500 → **3.5x compute reduction**

This frees compute budget for deeper search on hard tasks and more self-play rounds.

### Phase 5: Benchmark + Analysis (2-3 hours)

Run comprehensive benchmarks comparing:

1. **Baseline (Sprint 21 model)**: Current `goal_cond_adapter/` on all tiers
2. **S23 model (no self-play)**: Phase 2 retrained model
3. **S23 model (after self-play)**: Phase 3 final model
4. **Ablations**:
   - S23 model without depth-3 data (is depth-3 training data the key?)
   - S23 model without multi-step pairs (is the deep multi-step gap closed by data alone?)

For each configuration, run on all 7 tiers (T2, V2, Hard, Recurrence, Multi-step, Deep multi-step, Fundamentals, Compositional).

#### Key Questions

1. **Does compositional training data improve the model?** Compare S23 vs. baseline on V2 and deep multi-step.
2. **Does self-play add value beyond the synthetic data?** Compare S23 pre- vs. post-self-play.
3. **How many ExpandedFixed fallbacks remain?** Track which tasks need the lookup table vs. which the model now solves directly.
4. **What's the model's compositional reach?** Test on held-out compositions not in training data.
5. **Does tiered inference maintain accuracy?** Compare pass@1 between fixed-budget and tiered.

### Phase 6: Documentation + Lessons Learned (1 hour)

- Update `sprint23_status.md` with all results
- Document findings in `lessons_learned.md`
- Update `MEMORY.md` with key findings
- Identify Sprint 24 direction based on results

## Success Criteria

| Metric | Minimum | Target | Stretch |
|--------|---------|--------|---------|
| Deep multi-step | 3/6 | 4/6 | 5/6 |
| V2 (no regression) | 18/20 | 19/20 | 20/20 |
| T2 + Hard (no regression) | 14/15 | 15/15 | 15/15 |
| Compositional (model, no fragment cache) | 4/8 | 6/8 | 8/8 |
| Training pairs generated | 3,000 | 5,000 | 8,000+ |
| Self-play rounds completed | 1 | 2 | 3 |
| Benchmark time (tiered vs. fixed) | 2x faster | 3x faster | 4x faster |
| New tasks solved by model (not EF) | 2 | 4 | 6 |

**Primary success metric**: Deep multi-step improvement (2/6 → 4+/6). This is the clearest signal that compositional training data teaches the model depth-3 reasoning.

**Secondary metric**: Number of tasks where the model's proposed tactic solves without ExpandedFixed fallback. This measures whether the model is learning to compose, not just memorize.

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Compositional data is too uniform (same patterns repeated with different types) | Medium | Variable augmentation + ensure diverse fragment combinations. Monitor training loss convergence — if it drops too fast, data is too easy |
| Val_loss increases significantly with more data | Medium | Expected and acceptable per Sprint 21 lesson. Benchmark performance is the true metric, not val_loss |
| Self-play loop produces mostly ExpandedFixed-sourced tactics (model learns nothing new) | High | Filter training pairs to only include model-proposed tactics (not EF fallback). Track provenance of each pair |
| Depth-3 training data doesn't transfer to unseen depth-3 tasks | Medium | Include diverse type signatures in generation, not just existing benchmark types. Hold out 2 deep multi-step tasks for validation |
| Curriculum weighting adds complexity without benefit | Medium | Fall back to uniform mixing if curriculum implementation takes >1 hour. Data quality > sampling strategy |
| Model overfits to compositional patterns, loses Hard task performance | Low | Keep all Sprint 21 data in training set. Run Hard tier after every retrain as regression check |
| NEVER retrain without backup (Sprint 18 lesson) | N/A | Mandatory: copy adapter before any retrain |

## Estimated Effort

| Phase | Hours |
|-------|-------|
| Phase 0: Compositional data generator | 3-4 |
| Phase 1: Scaffolded curriculum | 1-2 |
| Phase 2: Retrain GoalConditioned (3 seeds) | 1-2 |
| Phase 3: Self-play loop (2-3 rounds) | 2-3 |
| Phase 4: Tiered inference | 1-2 |
| Phase 5: Benchmark + analysis | 2-3 |
| Phase 6: Documentation | 1 |
| **Total** | **11-17** |

## Files to Create

| File | Purpose |
|------|---------|
| `lean_mcts/synth/generate_compositional_data.py` | Generate verified (state, tactic) pairs from compositional search |
| `lean_mcts/train/build_curriculum.py` | Merge + weight training data tiers |
| `lean_mcts/train/goal_cond_s23_train.jsonl` | Expanded training data (generated) |
| `lean_mcts/train/goal_cond_s23_val.jsonl` | Expanded validation data (generated) |
| `lean_mcts/train/goal_cond_adapter_pre_s23/` | Backup of Sprint 21 adapter |
| `docs/implementation/sprint23/sprint23.md` | This plan |
| `docs/implementation/sprint23/sprint23_status.md` | Status tracker |
| `docs/implementation/sprint23/lessons_learned.md` | Post-sprint findings |

## Files to Modify

| File | Changes |
|------|---------|
| `lean_mcts/benchmark/run.py` | Add `run_task_tiered()` with escalating rollout budgets |
| `lean_mcts/train/dataset.py` | Add tier-weighted sampling (if curriculum implemented) |
| `lean_mcts/synth/selfplay_loop.py` | Add provenance tracking (model-proposed vs. EF-sourced tactics) |
| `lean_mcts/synth/collect_goal_conditioned.py` | Add compositional data merger |

## Non-Goals

- No changes to MCTS algorithm (PUCT, backprop, tree structure)
- No changes to Pantograph daemon architecture
- No changes to oracle verification
- No new Lean tasks (use existing 62 tasks across 8 tiers)
- No fragment vocabulary expansion (Sprint 22's 65 fragments are sufficient)
- No changes to CompositionalPolicy search algorithm
- No base model change (keep Qwen2.5-Coder-1.5B)
- No multi-GPU training
- No GHC Core pipeline changes

## Design Decisions

### Why generate training data from compositions, not from benchmark runs?

Benchmark runs produce ~50-100 successful (state, tactic) pairs across all tiers. The compositional engine can generate ~800+ unique pairs covering type signatures and depths the benchmark doesn't exercise. Self-play runs complement but cannot replace synthetic generation — the frontier is too sparse for benchmark-only extraction.

### Why keep ExpandedFixed in the hybrid?

ExpandedFixed covers patterns the compositional engine can't express (custom lambdas like Fibonacci tuple-folds, complex scanl patterns). The sprint's goal is to reduce dependence on EF, not eliminate it. Measuring "tasks solved without EF fallback" tracks progress toward eventual removal.

### Why not expand the fragment vocabulary first?

Sprint 22's V2 gap (5/20 compositional vs 19/20 EF) comes from missing fragment types (multi-arg, product constructors). Expanding vocabulary is engineering work that adds more enumeration — the opposite of what the papers recommend. Instead, this sprint invests in the learning loop. If the retrained model can propose novel compositions beyond the 65-fragment vocabulary, that's more valuable than adding 50 more fragments.

### Why tiered inference now?

Self-play requires running benchmarks multiple times (once per round). Without tiered inference, 3 rounds × 62 tasks × 500 rollouts = 93,000 rollouts. With tiered inference: ~15,000 rollouts for the same coverage. This is a 6x compute reduction that makes 3 self-play rounds practical in one session.

### Why 3 seeds?

Sprint 18 lesson: "LoRA seed matters enormously — val_loss range 0.2784-0.2827, benchmark 0/6 to 2/6." With a larger, more diverse dataset the seed variance may decrease, but we can't assume that. 3 seeds balances rigor against the 1.5-hour training budget.

## Connection to Research

| Paper | How This Sprint Applies It |
|-------|---------------------------|
| Goedel-Prover-V2 | Scaffolded data synthesis (Tier A→B→C). Self-correction via oracle feedback |
| STP (Self-play) | Compositional engine as conjecturer; model as prover. Automatic curriculum at the frontier |
| "Library Learning Doesn't" | Expressions are scaffolding for learning, not a permanent library. Focus on the self-play loop |
| AlphaEvolve | Keep noisy/diverse tactics in training. Don't over-filter. Oracle handles correctness |
| Seed-Prover | Tiered inference (50→200→500 rollouts). Cheap for easy tasks, deep for hard |
| AlphaVerus | Explore (compositional search) → tree-search (MCTS) → filter (oracle) → retrain. Exactly our loop |
| HTPS | Online training from search results. Each self-play round learns from the previous round's discoveries |
