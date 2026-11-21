# Sprint 24 Status: Hierarchical MCTS

## Current Phase: COMPLETE

## Final Results

| Tier | S21 (baseline) | S24 (actual) | Delta |
|------|---------------|-------------|-------|
| T2 | 10/10 | 10/10 | = |
| Hard | 4/5 | 4/5 | = |
| V2 | 19/20 | 19/20 | = |
| Recurrence | 6/6 | 6/6 | = |
| Fundamentals | 9/10 | 9/10 | = |
| Multi-step | 5/5 | 4/5 | -1 |
| Deep multi-step | 2/6 | **6/6** | **+4** |
| **Total** | **55/62** | **59/62** | **+4** |

### Deep Multi-Step Breakdown (6/6)

| Task | Return Type | Depth | Rollouts | Status | Notes |
|------|-------------|-------|----------|--------|-------|
| sum_count_max | (Int × Nat) × Int | 3 | 1 | **SOLVED** | Skeleton direct |
| stats_triple | (Int × Int) × Nat | 3 | 1 | **SOLVED** | Skeleton direct |
| partition_lengths | (Nat × Nat) × Nat | 3 | 142 | **SOLVED** | Skeleton fallback + MCTS |
| fold_and_length | Int × Nat | 2 | 1 | **SOLVED** | Skeleton direct |
| filter_both_ways | List Int × List Int | 2 | 498 | **SOLVED** | Skeleton fallback + MCTS |
| recurrence_and_sum | Int × Nat | 2 | 1 | **SOLVED** | Skeleton direct |

### Multi-Step Regression (-1)

split_even_odd regresses because its solution is a direct `exact (a, b)` pair construction, not constructor + separate exacts. The skeleton expansion's constructor fallback forces MCTS into the constructor path, preventing discovery of the direct solution. This is a known trade-off: constructor fallback is essential for depth-3 products (partition_lengths, filter_both_ways) but locks out direct pair construction for depth-1.

## Phase Checklist

- [x] Phase 0: Restore S21 baseline (`goal_cond_adapter_pre_s23/` → `goal_cond_adapter/`)
- [x] Phase 1: Goal structure analyzer (`lean_mcts/search/goal_analyzer.py`, 26/26 tests)
- [x] Phase 2: Skeleton-guided MCTS expansion (modified `lean_mcts/search/mcts.py`)
- [x] Phase 3: Sub-goal state formatting (integrated into skeleton expansion)
- [x] Phase 4: filter_both_ways solved (skeleton fallback + MCTS at rollout 498)
- [x] Phase 5: Benchmark — 59/62 total, 6/6 deep multi-step
- [ ] Phase 6: Self-play — skipped (6/6 without retraining)
- [x] Phase 7: Documentation

## Success Criteria

| Metric | Minimum | Target | Stretch | Actual |
|--------|---------|--------|---------|--------|
| Deep multi-step | 4/6 | 5/6 | 6/6 | **6/6** ✓ |
| S21 baseline recovery | 53/62 | 55/62 | 55/62 | **59/62** ✓ |
| Total (with hierarchical) | 57/62 | 59/62 | 61/62 | **59/62** ✓ |
| No regression on T2+V2+Hard | 33/35 | 34/35 | 35/35 | **33/35** ✓ |

## Architecture

```
Goal Type Analysis (deterministic)
  ↓
Skeleton Planner → [constructor, constructor, exact, exact, exact]
  ↓
Sub-Goal Search (independent per leaf)
  ├── EF candidates (type-filtered, length-sorted)
  ├── GC candidates (generative LoRA)
  └── Type-check via env.step()
  ↓
Greedy Combinations (best-of-each → one-hot swap → beam-10)
  ↓
Batch Oracle Verification
  ↓
SOLVED → build node chain    |    FAIL → constructor fallback → standard MCTS
```

## Files Created/Modified

| File | Purpose |
|------|---------|
| `lean_mcts/search/goal_analyzer.py` | NEW: GoalStructure, analyze_goal(), parse_sub_goals() |
| `lean_mcts/search/test_goal_analyzer.py` | NEW: 26 unit tests |
| `lean_mcts/search/mcts.py` | MODIFIED: skeleton expansion, greedy combinations |
| `lean_mcts/train/goal_cond_adapter/` | RESTORED: S21 adapter (val_loss 0.2756) |
| `lean_mcts/train/goal_cond_adapter_pre_s24/` | NEW: backup of S23 adapter |

## Key Implementation Decisions

1. **EF-first candidate ordering**: ExpandedFixed candidates placed before GC candidates — GC generates noise with trailing comment chars (`–`, `//`)
2. **Length-sorted EF**: Shorter expressions first ensures beam-10 reaches simple foldl/length exacts
3. **Unicode normalization**: ℤ→Int, ℕ→Nat for ExpandedFixed type matching
4. **Parameter prefilter**: Skip `exact 0`, `exact []` that don't reference input params
5. **Constructor fallback always**: For depth-3, constructor fallback is essential; the trade-off is one depth-1 regression (split_even_odd)
6. **No retraining**: Pure search architecture change, no policy/data modifications
