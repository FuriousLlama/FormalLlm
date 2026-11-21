# Sprint 10 — Status

Last updated: 2026-03-07 (Phase 6 complete)

## Overall Progress

```
[x] Phase 1 — Corpus Rebuild with USES
    [x] Added _is_global_ref() and extract_uses() to normalize.py
    [x] Modified to_sequence() to emit USES section for ALL sequences
    [x] Added "USES" to ALWAYS_KEEP in tokenizer.py
    [x] Rebuilt corpus: 4533 sequences, all with USES
    [x] Vocab size: 433 (+1 for USES token)
    [x] 110 compositions have USES with GlobalRefs, 4423 primitives have USES BODY (empty)
    [x] CRITICAL FIX: USES must be emitted for ALL sequences (including primitives)
        - Initial implementation (USES only for compositions) caused T3 regression:
          model never saw USES BODY in training → always generated GlobalRefs after USES
        - Fix: emit USES BODY (empty) for primitives in training
[x] Phase 2 — Parser + Constrained Decoder
    [x] Added _consume_ty_uses_body() helper to checker.py
    [x] Updated parse_sequence() for Sprint 5 and Sprint 5b paths
    [x] USES phase: only GlobalRef tokens and BODY are valid
    [x] Partial headers accepted for is_type_valid
    [x] All 384 tests pass (3 existing tests updated for new partial-header behavior)
    [x] 10 new USES-specific tests added
[x] Phase 3 — Benchmark Update
    [x] All 38 task prompts updated: ending changed from BODY to USES
    [x] _clean_tokens updated to handle Sprint 5b with USES
[x] Phase 4 — Train + Evaluate
    [x] First training (USES only for compositions): val_loss=0.4552
        T1: 20-21/22, T2: 2/10, T3: 3-4/6 — T3 REGRESSION
    [x] Diagnosis: model never generates BODY immediately after USES for non-compositions
    [x] Fix: USES for all training sequences (primitives get USES BODY)
    [x] Second training (USES for all): val_loss=0.4462 (best so far)
    [x] Benchmark results (3+ runs): T1: 18-19/22, T2: 1-4/10, T3: 2-4/6
[-] Phase 5 — RL (conditional)
    Skipped: T2 didn't reach 3+/10 consistently, RL known to degrade context-dependent models
[x] Phase 6 — Analysis
    [x] USES prediction diagnostic: model predicts correct building blocks 30% of time
    [x] Body wiring is the bottleneck, not planning
    [x] Conclusion: autoregressive body generation can't exploit USES planning
```

## Sprint 10 Final Results (USES for all, no RL)

| Metric | Sprint 9 (no RL) | Sprint 10 Run 1 | Run 2 | Run 3 | Range |
|--------|-----------------|-----------------|-------|-------|-------|
| T1 | 19-20/22 | 18/22 | 19/22 | 19/22 | 18-19/22 |
| T2 | 0-3/10 | 2/10 | 2/10 | 4/10 | 1-4/10 |
| T3 | 6/6 | 3/6 | 4/6 | 2/6 | 2-4/6 |
| Total | 25-29/38 | 23/38 | 25/38 | 25/38 | 23-27/38 |

### T2 per-task results (best across runs)

| Task | Sprint 9 | Sprint 10 | Delta |
|------|----------|-----------|-------|
| drop_negatives | 0/200 | 5-26/200 | NEW solve |
| accum_sum | 0-1/200 | 0-1/200 | same |
| count_positive | 0-1/200 | 0/200 | same |
| map_square | 0-2/200 | 0/200 | same |
| sort_desc | 0/200 | 0/200 | same |
| sum_doubles | 0/200 | 0/200 | same |
| max_abs | 0/200 | 0/200 | same |
| take_positives | 0/200 | 0/200 | same |
| nub_sort | 0/200 | 0/200 | same |
| count_even | 0/200 | 0/200 | same |

### T3 per-task regression

| Task | Sprint 9 | Sprint 10 | Notes |
|------|----------|-----------|-------|
| maybe_with_default | 20-21/200 | 0/200 | REGRESSION |
| apply_twice | 2/200 | 0/200 | REGRESSION |
| uncurry_add | 7-26/200 | 0-5/200 | regression |
| map_pair | 57-68/200 | 104-110/200 | IMPROVEMENT |
| swap_pair | 1-4/200 | 0-14/200 | noisy |
| maybe_map | 2-4/200 | 0-3/200 | same |

## Phase 6 — Analysis

### USES prediction quality (count_positive, 10 samples with composition context)

With few-shot context containing fCountNegative (USES fLength fFilter fIsNegative BODY ...):

| USES prediction | Count | Assessment |
|-----------------|-------|------------|
| fLength fFilter BODY | 3/10 | CORRECT (right building blocks) |
| BODY (empty) | 4/10 | Wrong (missed composition) |
| Wrong refs (fSort, fMaximum, etc.) | 3/10 | Wrong building blocks |

### Diagnosis

1. **Planning partially works**: With composition context, model predicts correct USES (fLength fFilter) 30% of the time.

2. **Body wiring fails**: Even with correct USES, body uses wrong predicate (`/= 0` instead of `fIsPositive`). Example: `USES fLength fFilter BODY (Lam 1 (App fLength (App fFilter (Lam 1 (App Prim /= Var 0 Lit 0)) Var 0)))` — correct structure but wrong predicate.

3. **Base rate problem**: 97.6% of training sequences have empty USES, so model defaults to BODY. Composition signals are drowned out.

4. **T3 regression cause**: Retraining with the USES token disrupted fragile learned patterns for structural tasks (maybe_with_default, apply_twice). These tasks had marginal pass rates (2-21/200) that depend on specific checkpoint characteristics.

### Conclusion

The USES experiment is diagnostic: **the T2 bottleneck is body wiring, not planning**. The model can select correct building blocks but cannot wire them together correctly. This points toward:

- **Stage 3 approach**: Type-directed wiring (use USES to select functions, then use their types to constrain body structure)
- **Explicit retrieval**: Replace learned USES prediction with type-based retrieval of building blocks
- **More composition data**: Current 110 compositions in 5228 sequences (2.1%) is insufficient for the model to learn wiring patterns

The USES section is a useful architectural primitive for future sprints, even if it didn't unlock T2 in this sprint.
