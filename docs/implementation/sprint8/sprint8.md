# Sprint 8 — Diagnostic-First: Unlocking the Hard-Zero Tasks

## Motivation

8 tasks produce 0/200 at k=200 t=0.6 across all RL rounds. Three key diagnostic signals:

1. **nub_sort**: Training has EXACT match `fSortNub = fSort (fNubInt xs)` — yet 0/200 (107 compile, 79 wrong)
2. **count_even**: Identical pattern to working count_positive (`fLength (fFilter <pred> xs)`), same type, same context pool — yet 0/200 (156 compile) vs count_positive's 1/200
3. **apply_twice**: `fTwice` in base corpus has body `(Lam 2 (App Var 1 (App Var 1 Var 0)))` — yet 0/200 (200 parse failures). **Root cause identified**: fTwice type is `(a -> a) -> a -> a` (polymorphic) but benchmark asks for `(Int -> Int) -> Int -> Int` (concrete). These don't match in type-grouped context selection → model gets zero relevant few-shot examples.

Sprint 6 added 80 compositions with no T2 improvement. We diagnose before adding data.

## Hypothesis

**Primary**: At least 3 of the 8 hard-zero tasks fail due to fixable pipeline issues (type-matching gaps, context selection, name interference), not fundamental model capacity limits.

**Decisive test**: Diagnostics identify >=3 fixable tasks, fixes push total to >=30/38 at k=200 t=0.6.

## Design

### Diagnostic Harness

Add a `--dump-candidates` flag to `run.py` that saves all k generated token sequences per task as structured JSON:

```json
{
  "task": "nub_sort",
  "k": 200,
  "temperature": 0.6,
  "context_used": ["fSort", "fNubInt", "fFilter", ...],
  "candidates": [
    {
      "tokens": ["DEF", "fNubSort", ...],
      "classification": "compile_error",
      "error": "Couldn't match expected type...",
      "parsed_body": "(App fSort (App fNubInt Var 0))"
    },
    ...
  ]
}
```

This provides the raw data to classify failure modes per task:

- What body structure does the model generate? (composition-shaped or garbage?)
- Which function names appear? (right ones or copied from context?)
- What few-shot context was selected? (relevant or irrelevant?)
- Where does generation diverge? (first bad token for parse errors, GHC error for compile errors)

### Predicted Fixes

Based on preliminary analysis, the following fixes are likely (to be confirmed by Phase 1 diagnostics):

**Polymorphic type matching**: `apply_twice` gets 0 relevant peers because `(a -> a) -> a -> a` ≠ `(Int -> Int) -> Int -> Int`. Adding type-variable unification as a fallback when exact match has 0 or few peers would fix this.

**count_even vs count_positive divergence**: Same type `[Int] -> Int`, same context pool (29 peers), same `fLength (fFilter ... Var 0)` pattern. count_positive works at 0.5%, count_even at 0%. Diagnostic will reveal whether the model picks the wrong predicate (fIsNegative instead of fEven).

**Targeted training data**: Only after Phase 1 confirms what's missing. Likely 5-8 functions, not 80.

**Name-swap augmentation**: If nub_sort's exact training match `fSortNub` fails because the model generates `fNubInt (fSort xs)` instead of `fSort (fNubInt xs)` based on name ordering.

## Phases

### Phase 1 — Diagnostics (no retraining)

Build the `--dump-candidates` flag in `run.py` that saves all k generated token sequences per task as structured JSON (raw tokens, parse/compile/wrong classification, error messages, actual few-shot context used).

Run on all 8 hard-zero tasks at k=200 t=0.6. For each task, classify:

- What body structure does the model generate? (composition-shaped or garbage?)
- Which function names appear? (right ones or copied from context?)
- What few-shot context was selected? (relevant or irrelevant?)
- Where does generation diverge? (first bad token for parse errors, GHC error for compile errors)

**Key files:**

- `sprint/benchmark/run.py` — add `--dump-candidates` flag

**No code changes beyond the diagnostic harness.** This phase is pure investigation.

### Phase 2 — Targeted Fixes (based on Phase 1 findings)

All fixes in this phase are conditional on Phase 1 diagnostic results. Predicted fixes:

**2a: Polymorphic type matching for context selection**

- `apply_twice` gets 0 relevant peers because `(a -> a) -> a -> a` ≠ `(Int -> Int) -> Int -> Int`
- Fix: in `build_few_shot_prompt()`, add type-variable unification as a fallback when exact match has 0 or few peers
- File: `sprint/benchmark/run.py`

**2b: Investigate count_even vs count_positive divergence**

- Same type `[Int] -> Int`, same context pool (29 peers), same `fLength (fFilter ... Var 0)` pattern
- count_positive works at 0.5%, count_even at 0% — this must be a function-selection issue
- Diagnostic will reveal whether the model picks the wrong predicate (fIsNegative instead of fEven)
- Fix TBD based on diagnostic findings — likely hint-weighted context selection using `few_shot_funcs`
- File: `sprint/benchmark/run.py` (context selection), possibly `sprint/benchmark/tasks.json`

**2c: Targeted training data (5-8 functions, not 80)**

- Only after Phase 1 confirms what's missing
- Likely additions: `fReverseSort` (for sort_desc), `fFoldlAdd`/`fFoldlMul` (for accum_sum), `fMapSquare` without +1 (for map_square)
- Files: `sprint/extract/Compositions.hs`, `sprint/extract/compositions.txt`

**2d: Name-swap augmentation (if nub_sort name interference confirmed)**

- If model generates correct composition structure but wrong application order based on name
- Add augmentation: for each composition `fAB = fA (fB xs)`, also train `fBA = fA (fB xs)` (same body, swapped name)
- File: `sprint/corpus/augment.py`

### Phase 3 — Retrain + Evaluate

```bash
make corpus && make train && make benchmark K=200 && make rl K=200
```

Standard eval at k=200 t=0.6.

### Phase 4 (optional) — Ablation

Train with pipeline fixes only (no new data) vs new data only (no pipeline fixes) to isolate which changes are load-bearing.

Only run if Phase 3 shows improvement and it's unclear whether pipeline fixes or new data drove it.

## Success Criteria

| Metric             | Min bar               | Target        | Sprint 7 |
| ------------------ | --------------------- | ------------- | -------- |
| T1 (no regression) | ≥21/22 k=200 t=0.6    | 22/22 post-RL | 22/22    |
| T2 (compositional) | **≥5/10** k=200 t=0.6 | ≥7/10 post-RL | 3/10     |
| T3 (structural)    | **≥4/6** k=200 t=0.6  | ≥5/6 post-RL  | 3/6      |
| Total              | **≥30/38**            | ≥34/38        | 27/38    |
| Hard-zero resolved | ≥3 of 8               | ≥5 of 8       | 0/8      |

**Decisive outcome:** If diagnostics reveal that all 8 hard-zero tasks are fundamental model capacity limits (no pipeline issues), the sprint pivots to architectural changes (larger model, chain-of-thought tokens, or retrieval-augmented generation). If diagnostics identify ≥3 fixable tasks, targeted fixes proceed as planned.

## Risk Analysis

| Risk                                       | Likelihood                         | Mitigation                                                                    |
| ------------------------------------------ | ---------------------------------- | ----------------------------------------------------------------------------- |
| All 8 are fundamental capacity limits      | Low (3 have clear pipeline issues) | Accept finding, plan architectural changes                                    |
| Type-matching fix causes T1 regression     | Low                                | Only activates as fallback when exact match has 0 or few peers; test T1 first |
| Name-swap augmentation confuses training   | Medium                             | Ablate with/without                                                           |
| Adding exact fMapSquare makes test trivial | Low                                | Test is generalization to novel name, not novel pattern                       |
| Phase 1 diagnostics invalidate predictions | Medium                             | Phase 2 is conditional on Phase 1 — adjust as needed                          |

## Files to Modify

| File                                                                                    | Phase     | Change                                                                     |
| --------------------------------------------------------------------------------------- | --------- | -------------------------------------------------------------------------- |
| `sprint/benchmark/run.py`                                                               | 1, 2a, 2b | `--dump-candidates` flag; polymorphic type fallback; hint-weighted context |
| `sprint/extract/Compositions.hs`                                                        | 2c        | 5-8 targeted functions                                                     |
| `sprint/extract/compositions.txt`                                                       | 2c        | New function names                                                         |
| `sprint/corpus/augment.py`                                                              | 2d        | Name-swap augmentation                                                     |
| No changes: `train.py`, `checker.py`, `constrained_decode.py`, `tokenizer.py`, Makefile |           |                                                                            |
