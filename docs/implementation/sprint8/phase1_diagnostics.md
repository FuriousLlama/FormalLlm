# Sprint 8 Phase 1 — Diagnostic Report

k=200, t=0.6, checkpoint=checkpoint_rl_k200_t06.pt

## Root Cause: Type-Based Context Selection Overrides Curated Few-Shot Functions

Sprint 5 added type-based context selection (`context_type`), which picks 3 random
corpus functions with matching type signature as few-shot examples. This **completely
overrides** the curated `few_shot_funcs` lists that were designed to give tasks their
needed building blocks.

For compositional tasks (T2/T3), the building blocks have **different types** from the
target. `fCountEven :: [Int] -> Int` needs `fLength :: [a] -> Int`, `fFilter :: (Int -> Bool) -> [Int] -> [Int]`,
and `fEven :: Int -> Bool` — three different type signatures. Type-based selection picks
3 random `[Int] -> Int` peers instead, which are compositionally useless.

### Overlap between curated and actual context

| Task               | Curated `few_shot_funcs` | Type-selected context                 | Overlap      |
| ------------------ | ------------------------ | ------------------------------------- | ------------ |
| map_square         | fMap, fSquare            | nubInt, fNubInt, fMapMinusOne         | **NONE**     |
| sort_desc          | fSort, fReverse          | fSort, fReverseInit, fNegate2         | fSort only   |
| sum_doubles        | fSum, fMap, fDouble      | fMinSquare, fSumNegative, fSumTriples | **NONE**     |
| nub_sort           | fSort, fNubInt           | fFilterEven, fMapClamp, fNubInt       | fNubInt only |
| accum_sum          | fFoldl, fSum             | fMinOr, fMaxOr                        | **NONE**     |
| count_even         | fLength, fFilter, fEven  | fCountLarge, fSumSucc, fSumAbs        | **NONE**     |
| apply_twice        | fComp, fDouble           | helperForBEEP, replaceVertex, runB    | **NONE**     |
| maybe_with_default | fMap, fFromMaybe         | fDefaultList                          | **NONE**     |

6 of 8 tasks have **zero overlap**. 2 tasks have 1 of 2-3 needed functions.

## Per-Task Analysis

### sum_doubles — RIGHT structure, wrong function (MOST FIXABLE)

- **Errors**: parse=122, compile=21, **wrong=57**
- The model generates `fSum (fMap <f> x)` 56 times — **correct composition pattern!**
  - `fSum (fMap fAbs x)` — 44x (wrong: fAbs instead of fDouble)
  - `fSum (fMap fSucc x)` — 12x (wrong: fSucc instead of fDouble)
- fDouble is NOT in context. Model picks fAbs/fSucc because those are `Int -> Int` functions it knows.
- **Fix**: Include fDouble in context. The model already knows the structure.

### count_even — SAME pattern as count_positive

- **Errors**: parse=182, compile=16, wrong=2
- Context gives fCountLarge, fSumSucc, fSumAbs — not fLength, fFilter, fEven
- Top body: `fSum (fMap x)` — wrong pattern entirely (should be `fLength (fFilter fEven x)`)
- count_positive (which works at 200/200 post-RL) has the same type and curated context
- **Fix**: Use curated context. If model sees fLength + fFilter + fEven, it should behave like count_positive.

### sort_desc — has fSort but NO fReverse

- **Errors**: parse=92, compile=108
- 200/200 candidates reference fSort (it's in context), 0/200 reference fReverse (not in context)
- Top body: `fTake x` (41x) — model defaults to fTake as a list→list function
- Target: `fReverse (fSort x)` or `fSort` composed with reverse
- **Fix**: Include fReverse in context. Model needs both components.

### nub_sort — has fNubInt but NO fSort

- **Errors**: parse=178, compile=22
- Context has fNubInt but not fSort. Bodies are garbage.
- Sprint 8 plan noted: training has exact match `fSortNub = fSort (fNubInt xs)` — but at inference time, the model doesn't see fSort in context.
- **Fix**: Include fSort in context.

### apply_twice — CONFIRMED: 0 type-matched peers

- **Errors**: parse=68, compile=124, wrong=8
- Context: `helperForBEEP, replaceVertex, runB` — Hackage garbage from arity fallback
- Type `(Int -> Int) -> Int -> Int` has 0 exact matches; arity fallback gives 2-arrow functions
- All 8 "wrong" outputs: `f x y = x y` — single application instead of `f x y = x (x y)`
- **Fix**: Polymorphic type matching (fTwice has `(a -> a) -> a -> a`) or use curated context.

### accum_sum — wrong context, wrong pattern

- **Errors**: parse=42, compile=158
- Context: fMinOr, fMaxOr (same type `Int -> [Int] -> Int` but unrelated)
- Bodies: recursive case analysis with fMaximum/fMinimum — wrong pattern entirely
- Target: foldl-based accumulation
- **Fix**: Include fFoldl in context.

### map_square — no relevant context at all

- **Errors**: parse=169, compile=30, wrong=1
- Context: nubInt, fNubInt, fMapMinusOne — all `[Int] -> [Int]` peers, none relevant
- fMap has type `(Int -> Int) -> [Int] -> [Int]` — different type. fSquare is `Int -> Int` — also different.
- Bodies are nonsense: `(+) x (+)`, self-references
- **Fix**: Include fMap and fSquare in context.

### maybe_with_default — type too rare, structural gap

- **Errors**: parse=18, compile=182
- Only 1 type-matched peer (fDefaultList). Context too thin.
- Top body: `f x = fMap x` (155x) — model defaults to fMap but can't use it correctly
- Needs fFromMaybe in context (type `Int -> Maybe Int -> Int`)
- **Fix**: Include curated context. May also need fFromMaybe in training corpus.

## Classification Summary

| Task               | Primary failure             | Root cause                           | Fixable by context?                |
| ------------------ | --------------------------- | ------------------------------------ | ---------------------------------- |
| sum_doubles        | Right structure, wrong func | fDouble not in context               | **YES** (highest confidence)       |
| count_even         | Wrong pattern               | fLength/fFilter/fEven not in context | **YES** (same as count_positive)   |
| sort_desc          | Missing component           | fReverse not in context              | **YES**                            |
| nub_sort           | Missing component           | fSort not in context                 | **YES**                            |
| apply_twice        | Garbage context             | 0 type matches, arity fallback       | **YES** (poly matching or curated) |
| map_square         | No relevant context         | fMap/fSquare different types         | **YES**                            |
| accum_sum          | Wrong pattern               | fFoldl not in context                | **LIKELY**                         |
| maybe_with_default | Structural gap              | fFromMaybe not in context            | **MAYBE** (may need training data) |

## Recommended Phase 2 Fix

**Single change**: In `build_few_shot_prompt()`, when `few_shot_funcs` is specified on a task,
use those as context (Sprint 4 path) regardless of whether `context_type` is also present.
The type-based selection is good for T1 tasks (similar to training data) but actively harmful
for T2/T3 compositional tasks that need building-block functions of different types.

This is a ~3-line change in `run.py` and should fix 6-7 of 8 tasks with no retraining.
