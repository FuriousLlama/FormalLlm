# Sprint 14 — Lessons Learned

## Phase 1: Enumeration

### Dedup must group by (input_type, return_type), not just input_type
Partition expressions return `List Int × List Int` but were deduplicated against
`List Int` filter expressions (identical outputs for each element, but different
type). Fix: use `(input_type, return_type)` as the dedup key.

### List.enum is deprecated — use zipIdx
In modern Lean 4 / Mathlib, `List.enum` is deprecated. `List.zipIdx` is the
replacement but reverses the tuple order: `(value, index)` instead of
`(index, value)`. All enumerate.py code and TasksV2.lean use zipIdx.

### 7 test inputs gives much better dedup than 5
With 5 test inputs, some genuinely different expressions produced identical
output vectors by coincidence (e.g., `filter (x > 0)` vs `filter (x ≠ 0)` on
inputs that happened to have no zeros). Adding 2 more test inputs that include
zeros and mixed patterns increased discrimination significantly.

### 100% validation rate means the enumeration grammar is clean
All 2279 candidates compiled successfully. This means the enumeration functions
produce syntactically valid Lean expressions. The grammar design (building
expressions from known-valid components) pays off — no wasted Lean compilation.

### Level 2 (3-step compositions) limited by combinatorial explosion
Level 2 adds ~250 expressions but could add thousands. Must cap at ~500
candidates per input type to keep enumeration under 20 minutes. Quality control
(dedup + degenerate removal) is more important than raw volume.

## Phase 2: REPL Fix

### max_expand_k prevents REPL crash without quality loss
Simply sorting candidates by log-prob and keeping top-k means the most
promising tactics are always tried first. With k=100, REPL stays responsive
while retaining all high-probability solutions.

## Phase 3: Benchmark

### Mathlib name collisions are real
`dotProduct` and `normalize` are already defined in Mathlib. The Lean compiler
gives an ambiguity error, not a clear "name collision" message. Fix: prefix
with `synth` (synthDotProduct, synthNormalize).

### Product types need `constructor` tactic
Tasks returning `A × B` cannot be solved in one step with `exact (a, b)` in
most cases. The MCTS needs to use `constructor` to split the goal into two
sub-goals, then solve each with `exact`. This requires multi-step search
(depth > 1) and is the first real test of MCTS depth.

## Phase 4: Self-play + Training

### Multi-type augmentation requires input-type-aware variable replacement
Can't blindly replace `xs` with variants when the state has `xss` (nested
lists), `n` (Nat), or `xs`/`ys` (two-arg). Word-boundary regex needed for
`n` to avoid corrupting `fun` → `fuk` etc.

### Multi-step training pairs can be synthesized without MCTS
For product types, we know the decomposition pattern (constructor → exact + exact).
Synthesizing these pairs from the expression space adds 332 training pairs
without running any MCTS — much cheaper than real self-play.

### Stdlib extraction yields modest but clean data
83 pairs from Lean Init + batteries source. More would need deeper parsing
(match expressions, where clauses, do notation), but simple expression-body
functions are the cleanest training signal.

### 6400 training pairs is in the sweet spot for LoRA
With 5122 train + 1281 val, the Qwen2.5-Coder-1.5B LoRA fine-tune converges
in ~10 epochs (~15 minutes on RTX 5090). Loss drops from 0.54 to ~0.28 by
epoch 3, with val_loss tracking closely (no overfitting).

## Phase 5: Value Network

### Expression-derived value data is sparse
Only 17 unique states from 1435 expressions (most share the same goal type).
After augmentation: 324 pairs. Barely enough for the linear value head.
Real value data should come from MCTS rollout traces in future sprints.

## Phase 6: Evaluation

### max_expand_k MUST NOT be used with SynthesisPolicy
Capping at k=100 causes T2 regression: 10/10 → 6/10. The LoRA model generates
64 candidates with high log-prob, leaving only 36 slots for Fixed tactics. The
Fixed tactics that solve sort_desc, sum_doubles, nub_sort, accum_sum get dropped.
Without the cap, all Fixed tactics survive and SynthesisPolicy gets 10/10 T2.

### ExpandedFixed is the ceiling for enumeration-based synthesis
ExpandedFixed v2 solves 11/20 V2 tasks — every task whose solution exists in the
enumerated expression set. SynthesisPolicy v2 (LoRA + Fixed) matches this at
10/20 but doesn't exceed it. The LoRA policy doesn't discover novel compositions
beyond what's enumerated. It's still a "sophisticated lookup table."

### Category A (multi-type) fails due to variable name mismatch
Tasks with `xss : List (List Int)` or `(xs ys : List Int)` parameters use the
same variable names as our expressions, but the REPL state format differs from
what the type pre-filter expects. The ExpandedFixed type filter checks the goal
type (`⊢ List Int`) but not the parameter types. A context-aware policy that
reads `xss` from the state string and substitutes into expressions would solve
this.

### Category C (multi-step) needs more rollout budget
With 20-50 rollouts, MCTS barely explores depth > 1. After `constructor` splits
`⊢ A × B`, the tree needs to expand two sub-goals, each trying all candidate
tactics. At depth 2 with 80+ candidates per node, exhausting the tree requires
80² = 6400 rollouts per multi-step solution. Value-guided search or heuristic
pruning is essential for multi-step to work.

### Duplicate tactics in FixedPolicy cause KeyError in MCTS
`constructor` appeared in both PROOF_TACTICS and SYNTHESIS_TACTICS. The expand()
function builds `untried_tactics` as a list (duplicates) but `_valid_states` as
a dict (dedup). Second pop of the same key → KeyError. Fixed by deduplicating
candidates before processing.

### REPL crashes are infrastructure failures, not model failures
Even with max_expand_k and orphaned process cleanup, the REPL crashes on
specific tasks (flatten, partition_positive, dedup_count, filter_map_abs).
These all have either unusual input types or product return types. The
DirectDojo REPL may not handle multi-goal states properly (after `constructor`).
The model never gets to propose solutions for these tasks — the expressions
exist in the enumerated space but the REPL dies before they're tried.
Hard tasks 0/5 should be read as "untested" rather than "failed."
Need REPL restart or batch oracle verification to work around this.

### Parallel benchmark execution needs per-task isolation
ThreadPoolExecutor with 4 workers causes `[Errno 17] File exists` — the
DirectDojo and oracle write temp files to the same directory. Each task needs
its own temp directory or file naming that includes thread ID. This is the
main infrastructure gap for fast evaluation.
