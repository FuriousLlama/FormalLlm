# Sprint 28: Train on Diversity — Real Lean Programs

## Goal

Train a new GoalConditioned adapter on function definitions mined from real Lean 4 code (stdlib, Batteries, Mathlib), not template-generated synthetic data. Measure whether exposure to diverse compositional patterns enables the model to generalize beyond the template catalog on the Sprint 27 diagnostic benchmark.

## Motivation

### What Sprint 27 showed

The system is pure verified template lookup: 3/18 diagnostic tasks solved, all by template/library match. The LoRA model (trained on 17K template-generated pairs) contributes zero additional solves — it learned to reproduce templates, not compose novel patterns.

The 16 failures decompose into:
- **Wiring gap (10)**: all primitives available, novel composition missing
- **Multi-step gap (4)**: needs recursive helpers or multi-tactic sequences
- **Search design gap (2)**: architecture can't represent the solution

### The hypothesis

The model failed because its training distribution has a blind spot: 99.4% of training pairs are simple `exact` expressions from the template catalog. Real Lean 4 library code contains the exact patterns the model is missing:
- Tuple accumulator folds with field interaction (common in algorithm implementations)
- Recursive helpers (`let rec`, structural recursion)
- Operation chaining (reverse, dedup, filter, sort composed)
- Index-tracking accumulators
- State machines via fold

If we train on diverse real-world function definitions instead of (or alongside) template outputs, the model may learn to generate these compositional patterns.

### Why this is tractable

- `extract_stdlib.py` already exists — produces (state, tactic) pairs from Lean stdlib/Batteries (83 pairs, simple bodies only)
- Pantograph can enumerate definitions (`env_catalog`), extract types for goal states (`env_inspect`), and verify converted expressions (`goal_start` + `goal_tactic`)
- All target expression forms verified through Pantograph: `match`, `let rec`, chained `let`, tuple accumulators all type-check inside `exact`
- Training pipeline is established (~10 min on RTX 5090)
- Sprint 27 diagnostic benchmark is the evaluation — no new infrastructure needed
- Success criterion: >3/18 diagnostic with model-generated non-template tactics

## Data Sources

### Source 1: Lean stdlib (Init/Data/List)

Core list function implementations: `map`, `filter`, `foldl`, `foldr`, `reverse`, `zip`, `partition`, `splitAt`, `intersperse`, etc. `extract_stdlib.py` currently extracts 83 pairs from simple expression bodies only, filtering out `match`, `where`, and `do` definitions via substring checks.

Most stdlib list functions use equation compiler syntax (no extractable body) or `where` clauses (local helper not in scope outside the definition). These require source-level conversion (see Phase 1) before they become usable training pairs.

Expected yield: 100-200 pairs (with source-level converter).

### Source 2: Batteries Data.List (10 modules, primary source)

**Basic.lean alone has 49 algorithmic definitions** — the richest single file across all three libraries. Implementations span pattern-match recursion (`bagInter`, `diff`, `after`), fold-based algorithms (`sublists`, `pwFilter`, `union`), tail-recursive optimized versions (`splitOnPTR`, `replaceFTR`), and inductive definitions (`Forall₂`, `IsChain`). Many functions ship in spec + TR pairs, both useful for training.

Remaining modules (Scan, Perm, Pairwise, etc.) are primarily theorems/lemmas with ~6 additional defs.

Expected yield: 50-100 pairs.

### Source 3: Mathlib Data.List.* (64 modules, low yield)

**Overwhelmingly theorems, not definitions.** 1,677 theorems/lemmas vs 123 defs (13.6:1 ratio). Most files have zero `def` declarations. Algorithmic content is concentrated in Defs.lean (~39 defs, including `destutter'`, `permutationsAux2`, `mapAccumr`) and Cycle.lean (~14 defs). Of the 123 defs, ~40-50 have non-trivial algorithmic bodies; the rest are trivial wrappers.

Expected yield: 40-50 pairs (prototype 5 files first to confirm).

### Source 4: Diagnostic reference implementations (18 tasks)

The 18 Sprint 27 reference implementations demonstrate exactly the patterns we want. Each can be augmented:
- Flip comparison operators (< → >, ≤ → ≥)
- Change accumulator initial values
- Vary predicates (> 0, < 0, even, odd)
- Change types (Int → Nat, List Int → List Nat)

Expected yield: 100-300 augmented pairs from 12 train-split seeds (6 held out for clean evaluation).

### Total expected: 300-650 diverse pairs

Library yield is modest (~200-350 pairs from all three libraries) because Mathlib is theorem-dominated and stdlib/Batteries require source-level conversion. Diagnostic augmentation (Source 4) brings the total to 300-650. This is smaller than originally hoped but every pair demonstrates patterns absent from the current 17,592 template-generated training set — particularly `let rec` (zero examples in current data), `match` with multi-arm patterns, and tuple accumulator folds.

**Key new pattern: `let rec`.** The current training data has zero `let rec` examples. Every stdlib/Batteries function with a `where`-clause helper converts to a `let rec` expression — recursive helpers with accumulator state, the exact pattern the diagnostic tasks require. Even 50 high-quality `let rec` examples would be a qualitative shift in the training distribution.

## Implementation Plan

### Phase 0: Baseline Preservation (before anything else)

1. **Back up S21 adapter**:
   - Copy `lean_mcts/train/goal_cond_adapter/` → `lean_mcts/train/goal_cond_adapter_s21_backup/`
   - Verify backup is byte-identical (`diff -r`)
   - This adapter is the S27 baseline — must be recoverable

### Phase 1: Data Extraction (1-2 days)

1. **Build source-level converter** (`lean_mcts/synth/lean_def_converter.py`):

   The main engineering deliverable of Phase 1. Converts Lean function definitions into standalone `exact` expressions by handling two syntactic patterns:

   - **Equation compiler → `match`**: Reconstruct `match ... with` from pattern-matching clauses. Qualify recursive self-references to the existing library name (e.g., `isPrefixOf as bs` → `List.isPrefixOf as bs`).
   - **`where` clause → `let rec`**: Inline the where-block as a `let rec` binding above the main expression. Preserve equation compiler clauses inside the helper.

   Lean's indentation-sensitive syntax makes pure regex fragile. The converter should handle: multi-argument equation clauses, `where` blocks with their own equation clauses, nested `match` inside either, and type annotations on `let rec` bindings.

   **Pantograph's role** (not for body extraction — `env_inspect(print_value=True)` returns kernel-level `brecOn` expressions, too low-level for training data):
   - **Enumeration**: `env_catalog(module_prefix=...)` to discover definitions
   - **Type extraction**: `env_inspect(name)` to get the type signature for goal states
   - **Verification**: `goal_start(type)` + `goal_tactic(state, "exact <converted_expr>")` to confirm each conversion type-checks

2. **Prototype on Batteries Basic.lean + 5 stdlib functions**:
   - Batteries Basic.lean is the richest single file (49 defs) — start here, not Mathlib
   - Pick 5 stdlib functions with `where` clauses (`partition`, `splitAt`, `splitOnP`, `zipWith`, `eraseIdx`)
   - Run converter end-to-end: source parse → convert → Pantograph verify
   - Calibrate: what fraction convert cleanly? What breaks?

3. **Extract from all three libraries**:
   - **Stdlib**: Remove substring filters from `extract_stdlib.py`, use converter for equation compiler / where bodies
   - **Batteries**: Basic.lean (49 defs) + remaining modules (~6 defs)
   - **Mathlib**: Focus on Defs.lean (~39 defs) and Cycle.lean (~14 defs); skip theorem-only files
   - For all: use `env_inspect` for type → goal state, verify via `goal_tactic`
   - Specialize polymorphic defs to concrete types where tractable; skip complex typeclass constraints
   - Filter by body length (≤512 tokens)
   - Output: `library_pairs.jsonl`

4. **Augment diagnostic references (hold-out split)**:
   - Split 18 diagnostic tasks: 12 train / 6 held-out
   - Held-out set must include at least 1 task from each tier (A/B/C/D)
   - Generate augmented variations ONLY from the 12 train-split references
   - Systematic augmentation: flip comparisons, change predicates, vary init values
   - Output: `diag_augmented.jsonl`
   - Record held-out task IDs in `diag_holdout.txt` for Phase 3

5. **Merge, deduplicate, and audit**:
   - Combine all sources
   - Remove duplicates and near-duplicates
   - Remove pairs that overlap with existing EF catalog
   - **Diversity audit**: measure fraction of pairs containing fold, recursion, match, let-rec, composition chains. If >80% are simple `exact` applications, the diversity hypothesis isn't being tested — revisit extraction filters before proceeding
   - Split 80/20 train/val
   - Output: `diverse_train.jsonl`, `diverse_val.jsonl`

**Exit criterion**: 300+ unique (state, tactic) pairs with confirmed diversity (≥40% non-trivial patterns: `let rec`, multi-arm `match`, tuple accumulator folds, chained `let` bindings).

### Phase 2: Training (0.5 day)

**The ratio problem.** `goal_cond_train.jsonl` has 17,592 template pairs. Diverse data is expected to be 800-2500 pairs. A naive merge yields 4-12% diverse fraction — too low for LoRA r=16 to allocate capacity to diverse patterns. The template gradient signal dominates at every step, and the adapter's 16 rank-1 directions converge on template reproduction before diverse patterns can claim representation.

Three training configurations, designed to give diverse patterns a fair shot:

1. **Config A — Diverse-only**: Train on diverse data only (no template pairs)
   - Tests: can the model learn novel patterns at all with LoRA r=16?
   - Risk: may forget how to generate simple templates (expected — Config A is diagnostic)

2. **Config B — Loss-weighted mix**: Merge diverse + template data; weight diverse examples' loss by 10x
   - Per-example loss weighting equalizes gradient contribution by source without duplicating data
   - Implementation: multiply loss by per-example weight (one line in training loop)
   - Tests: does balanced gradient signal enable the adapter to represent both template and diverse patterns?

3. **Config C — Curriculum + mix**: Epoch 1 diverse-only, epochs 2-3 loss-weighted mix
   - The adapter first allocates rank-16 capacity to diverse patterns (epoch 1)
   - Mixed epochs add template knowledge on top — templates are simpler, so they can be learned over an existing diverse substrate more easily than the reverse
   - Tests: does learning order matter for capacity allocation?

All configurations: 3 epochs, LoRA r=16, patience=2. Train 1-2 seeds per config.

Track validation loss on diverse and template splits **independently**. If diverse val loss plateaus or increases while template val loss decreases, the adapter is actively forgetting diverse patterns — flag this in Phase 4 analysis.

**Exit criterion**: Training converges (val loss decreasing or stable on at least one split).

### Phase 3: Evaluation (0.5 day)

Run all trained adapters on:
1. **Sprint 27 diagnostic** (18 tasks) — primary metric
2. **Core 120 tasks** — regression check

For each solved diagnostic task, classify:
- **Template hit**: EF catalog/skeleton already has it (not a model contribution)
- **Model-generated novel**: Model produced a tactic not in any template (this is what we want)
- **False positive**: Solution passes tests but is semantically wrong (add adversarial test case)

Report held-out vs train-split results separately. Solves on the 6 held-out tasks are the strongest evidence of generalization (no augmented variants in training data).

**Exit criterion**: Per-task analysis with mechanism classification, held-out vs train-split breakdown.

### Phase 4: Analysis (0.5 day)

Compare across all configurations:
- Does Config A (diverse-only) solve any diagnostic tasks that templates miss?
- Does Config B vs C differ — does learning order matter for capacity allocation?
- Does the model generate novel accumulator patterns, recursive helpers, or operation chains?
- What's the failure mode for unsolved tasks — does the model get closer (partially correct structure)?
- Did any config show diverse val loss increasing while template val loss decreased? (capacity competition signal)

If any configuration solves >5/18 diagnostic (with held-out solves): the diversity hypothesis is confirmed.
If all configurations solve ≤3/18: model size / architecture is the bottleneck, not data.

## Success Criteria

| Metric | Baseline (S27) | Expected | Good | Surprising |
|--------|----------------|----------|------|------------|
| Diagnostic total | 3/18 | 3-4/18 | 5-7/18 | 8+/18 |
| Diagnostic held-out (6) | 0-1/6 | 0-1/6 | 2/6 | 3+/6 |
| Tier A (compositional) | 2/5 | 2-3/5 | 3-4/5 | 5/5 |
| Tier B (algorithmic) | 0/5 | 0/5 | 1-2/5 | 3+/5 |
| Novel model tactics | 0 | 0-1 | 2-3 | 5+ |
| Core 120 regression | 104/120 | 104+/120 | 107+/120 | 110+/120 |

The primary metric is **novel model tactics** — how many diagnostic tasks are solved by tactics the model generated that don't exist in any template catalog. This directly measures whether training on diverse real code enables generalization.

## New Files

| File | Purpose |
|------|---------|
| `lean_mcts/synth/lean_def_converter.py` | Source-level converter: equation compiler → `match`, `where` → `let rec` |
| `lean_mcts/synth/extract_lean_defs.py` | Extract definitions from stdlib/Batteries/Mathlib using converter + Pantograph verification |
| `lean_mcts/synth/augment_diagnostic.py` | Generate variations of diagnostic reference implementations |
| `lean_mcts/synth/diag_holdout.txt` | IDs of 6 held-out diagnostic tasks (not in training data) |
| `lean_mcts/train/library_pairs.jsonl` | Converted library definitions (Sources 1-3) |
| `lean_mcts/train/diverse_train.jsonl` | Merged diverse training data |
| `lean_mcts/train/diverse_val.jsonl` | Validation split |
| `lean_mcts/train/diverse_adapter/` | New adapter trained on diverse data |
| `lean_mcts/train/goal_cond_adapter_s21_backup/` | Backup of S21 baseline adapter (Phase 0) |
| `docs/implementation/sprint28/sprint28.md` | This plan |
| `docs/implementation/sprint28/sprint28_status.md` | Status tracker |

## Modified Files

| File | Changes |
|------|---------|
| `lean_mcts/synth/extract_stdlib.py` | Remove substring filters, use converter for complex bodies |
| `lean_mcts/benchmark/run.py` | (no changes — diagnostic benchmark ready from S27) |

## Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Source-level converter can't handle all definition styles | Fewer training pairs | Prototype on Batteries Basic.lean first (known high yield); skip definitions that don't convert cleanly |
| Polymorphic defs don't specialize cleanly | Fewer training pairs | Phase 1 prototype catches this early; fall back to augmented diagnostics |
| Total yield below 300 pairs | Hypothesis undertested | Lean on Source 4 augmentation; lower bar is 300 not 800 — even 50 high-quality `let rec` examples are a qualitative shift |
| Model memorizes diagnostic refs | False positive solves | 12/6 train/hold-out split; structural similarity check, not just exact-match |
| Diverse data hurts template generation | Regression on core 120 | Config B (loss-weighted mix) preserves template pairs; S21 adapter backed up in Phase 0 |
| Diverse data is mostly simple `exact` (low diversity) | Hypothesis untested | Diversity audit gate in Phase 1 — ≥40% non-trivial patterns required to proceed |

## Non-Goals

- No new skeleton modules or template additions
- No architecture changes to MCTS
- No infrastructure changes (daemon, oracle stable from S26)
- No benchmark expansion beyond existing 120 + 18 diagnostic
