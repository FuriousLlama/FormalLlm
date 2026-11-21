# Sprint 24: Lessons Learned

## Search Architecture Beats Data Scaling

Sprint 23 tried 55x more depth-3 training data (17→934 pairs) and self-play — no improvement on deep multi-step (stuck at 2/6). Sprint 24's skeleton-guided search solved all 3 depth-3 tasks at rollout 1. The problem was never data; it was that flat MCTS explores one tactic per node, wasting rollouts on dead-end exacts before discovering the constructor chain.

## Unicode Type Mismatch Was Invisible for 6 Sprints

Pantograph returns `ℤ` and `ℕ` (Unicode), but `expressions.jsonl` uses `Int` and `Nat` (ASCII). ExpandedFixedPolicy's `_by_type` lookup silently failed — `_by_type.get("ℤ", [])` returned nothing, falling back to returning ALL 650+ unfiltered expressions. This worked for standard MCTS (the correct expression was somewhere in the 650+) but caused skeleton expansion to drown in noise. Fix: `_normalize_state_types()` maps ℤ→Int, ℕ→Nat.

## Candidate Ordering Determines Beam Success

The greedy combination strategy uses beam-10 per sub-goal. For depth-3 products with 3 sub-goals, the beam explores 10³ = 1000 combinations from the top-10 of each sub-goal's candidate list. If the correct exact is at position 100 (after 18 proof tactics + 82 type-matched expressions from expressions.jsonl), beam never reaches it.

Three fixes combined to solve this:
1. **Exact-only filter**: Remove proof tactics (rfl, simp, constructor) from skeleton sub-goal candidates
2. **Length sort**: Shorter expressions first (`foldl min 0` before `(xs.filter ...).foldl ... + map ...`)
3. **EF-first ordering**: ExpandedFixed candidates before GoalConditioned candidates (EF is reliable, GC generates noise with trailing `–` and `//` comment chars)

## GoalConditioned Generates Lean Comment Chars

The GC LoRA generates tactics like `exact xs.foldl ... – 1 //`. The `–` (en-dash, U+2013) is Lean's line comment delimiter, so `– 1 //` is a comment. The tactic parses as `exact xs.foldl ...` — shorter than intended. These type-check correctly (the truncated expression has the right type) but produce wrong outputs. The parameter prefilter catches some cases (`exact 0`-style), but comment-truncated expressions are a persistent GC quality issue.

## Constructor Fallback vs Direct Pair Construction

Some tasks (split_even_odd) have solutions that construct the product directly: `exact (xs.filter even, xs.filter odd)`. No `constructor` tactic needed. The skeleton expansion's constructor fallback forces MCTS into the constructor path, preventing discovery of these direct solutions.

Trade-off decision: always use constructor fallback. This costs 1 multi-step task (split_even_odd goes from SOLVED to FAILED, -1 on multi-step tier) but is essential for depth-3 products where the constructor chain needs MCTS to start from the post-constructor state. Net effect: +4 on deep multi-step, -1 on multi-step = +3 total.

## Parameter Prefilter Is Critical for Skeleton

Without filtering candidates that don't reference input parameters (`xs`, `ys`, etc.), skeleton tries combinations like (`exact 0`, `exact 0`, `exact 0`) that type-check but always fail the oracle. The standard MCTS has this prefilter in `_batch_oracle_verify`, but the skeleton expansion was calling the oracle directly. Adding `_extract_param_names_from_state()` filtering reduced valid candidates from 128 to ~100 per sub-goal, eliminating constant-expression noise.

## Skeleton Expansion Is Embarrassingly Effective

When the candidate ordering is right, skeleton expansion solves depth-3 products in **1 rollout** (~10-25 seconds wall time). Compare to flat MCTS which takes 200+ rollouts (5-10 minutes) for depth-2 products and never solves depth-3. The key insight: sub-goal independence turns O(k₁ × k₂ × k₃) search into O(k₁ + k₂ + k₃).

## Daemon State Accumulation

The skeleton expansion creates ~400 Pantograph states per task (constructor chain + type-checking per candidate). Combined with MCTS's ~5000 states per task, the daemon accumulates ~5400 states. No crashes observed, but future sprints should monitor memory if per-task state counts grow further.
