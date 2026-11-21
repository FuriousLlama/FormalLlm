# Sprint 6 — Project Status

Last updated: 2026-02-23 (Sprint 6 complete — all phases done, targets missed)

## Overall Progress

```
[x] Phase 1 — Data Preparation
    [x] Write 5 probe functions across 3 categories (filter+fold, map+fold, two-step pipeline)
    [x] Extract via GHC Core, verify composition structure preserved in de Bruijn IR
    [x] Diagnose cross-module import failure: GHC needs -isprint/extract when invoked from repo root
    [x] Fix parse_ghc_core.py: auto-add source file's directory to GHC -i path
    [x] Confirm separate-module approach works (composition structure identical to same-module)
    [x] Write remaining 75 functions across all 8 categories (80 total)
    [x] Create compositions.txt filter file (80 names)
    [x] Add extract-comp target to Makefile + CORPUS_COMP/CORPUS_COMP_CLEAN variables
    [x] make extract-comp: 80/80 parsed, 80/80 cleaned, 0 dropped
[x] Phase 2 — Pipeline Integration
    [x] Add 80 composition names to _CORPUS_FUNCTION_NAMES in tokenizer.py (~430 tokens total)
    [x] make corpus-merge (updated to include CORPUS_COMP_CLEAN; 161 + 4277 + 80 = 4518 total)
    [x] make corpus — rebuilt sequences.json + tokenizer.json + sequences_aug.json
    [x] Verify: all 80 compositions in sequences.json with DEF name TY type BODY format ✅
    [x] Verify: type alignment confirmed — 22 compositions at [Int] -> Int, 31 at [Int] -> [Int]
    [x] Augmented corpus: 5215 sequences (725 commutative variants including 91 from compositions)
[x] Phase 3 — Training + Evaluation
    [x] make train (Run A: full corpus with compositions, type-grouped context)
    [x] Verify no Tier 1 regression: make benchmark → Tier 1 17/22 (pre-RL baseline same as 5b)
    [x] Evaluate Tier 2/3 improvement vs Sprint 5b baseline → T2: 0/10 (no improvement), T3: 2/6
    [x] make rl (3 rounds of rejection sampling)
    [x] Compare against Sprint 5b: T2 target 5/10 MISSED (0/10), T3 target 4/6 MISSED (2/6)
    [x] Analysis: composition training data produced zero T2 improvement; hypothesis documented in lessons learned
```

---

## Phase 1 — Data Preparation (complete)

### Compositions.hs — 80 functions across 8 categories

New file `sprint/extract/Compositions.hs` as a separate module (`module Compositions where`) importing Functions.

| Category | Count | Pattern | Example |
|----------|-------|---------|---------|
| 1 — Filter + Fold | 12 | `fold (filter pred xs)` | `fCountNegative = fLength (fFilter fIsNegative xs)` |
| 2 — Map + Fold | 10 | `fold (map f xs)` | `fSumAbs = fSum (fMap fAbs xs)` |
| 3 — Map + Inline Lambda | 8 | `map (\x -> expr) xs` | `fMapSquarePlus = fMap (\x -> x*x+1) xs` |
| 4 — Two-Function Pipelines | 12 | `f (g xs)` | `fSortNub = fSort (fNubInt xs)` |
| 5 — Filter/TakeWhile/DropWhile | 10 | single-step with predicate | `fTakeWhileSmall = fTakeWhile (\x -> x < 100) xs` |
| 6 — Higher-Order Application | 8 | HOF composition | `fApplyThrice f x = f (f (f x))` |
| 7 — Tuple Operations | 10 | tuple destructuring/mapping | `fMapPairs f xs = fMap (\(a,b) -> (f a, f b)) xs` |
| 8 — Maybe Operations | 10 | Maybe map/default/catMaybes | `fCountJustPositive = fLength (fFilter fIsPositive (fCatMaybes xs))` |
| **Total** | **80** | | |

### Extraction results

```
make extract-comp
  [extract] 80/80 bindings parsed successfully
  [clean]   80 processed, 80 cleaned, 0 dropped
  [clean]   0 noise violations, 0 schema violations
```

App-node counts confirm multi-step structure in IR:
- `fCountOddPositive` (filter+fold+boolean pred): 5 App nodes
- `fMaxSquare` (map+fold): 2 App nodes
- `fCountJustPositive` (catMaybes+filter+length): 3 App nodes
- Single-step functions: 1 App node

No benchmark task names appear in Compositions.hs (no overlap with fCountPositive, fMapSquare, fSortDesc, etc.).

### Files modified in Phase 1

| File | Change |
|------|--------|
| `sprint/extract/Compositions.hs` | NEW: 80 composition functions in 8 categories |
| `sprint/extract/compositions.txt` | NEW: 80 function names (filter list for parse_ghc_core.py) |
| `sprint/extract/parse_ghc_core.py` | Added `import os`; `dump_core()` auto-adds source file dir to GHC `-i` path |
| `Makefile` | Added `CORPUS_COMP`, `CORPUS_COMP_CLEAN` variables; `extract-comp` target; `extract-comp` to `.PHONY` |

---

## Phase 2 — Pipeline Integration (complete)

### Corpus stats

| Metric | Sprint 5b | Sprint 6 |
|--------|-----------|----------|
| Handwritten functions | 161 | 161 |
| Hackage functions | 4,277 | 4,277 |
| Composition functions | 0 | **80** |
| Total base | 4,438 | **4,518** |
| Augmented sequences | 5,124 | **5,215** |
| Vocabulary size | 352 | **432** |

### Type alignment with benchmark tasks

| Normalized type | Composition count | Failing benchmark tasks covered |
|----------------|-------------------|----------------------------------|
| `[Int] -> Int` | 22 | count_positive, count_even, sum_doubles, max_abs |
| `[Int] -> [Int]` | 31 | sort_desc, nub_sort, take_positives, drop_negatives, map_square |
| `[Maybe Int] -> Int` | 2 | — |
| `[Maybe Int] -> [Int]` | 2 | maybe_with_default |
| `(Int, Int) -> Int` | 4 | uncurry_add |
| other | 19 | apply_twice, map_pair |

All 80 composition names present in vocabulary (0 missing). Commutative swap augmentation produced 91 composition variants.

---

## Phase 3 — Training + Evaluation (complete)

### Training — Run A

- 5215 augmented sequences, vocab=432, model=484K params
- Best val loss: **0.4561** (train=0.4388, ratio=1.06x, no overfitting)
- ~29 minutes, ran all 3000 epochs without early stopping

### Benchmark — pre-RL

| Metric | Sprint 5b pre-RL | Sprint 6 pre-RL | Delta |
|--------|-----------------|-----------------|-------|
| Tier 1 | 17/22 | 17/22 | = |
| Tier 2 | 1/10 | 0/10 | -1 |
| Tier 3 | 0/6 | 2/6 | +2 |
| Total | 18/38 | 19/38 | +1 |

Note: the Sprint 5b comparison target in the plan used the post-RL 22/22 T1 figure, which was misleading. The true pre-RL baseline was 17/22 T1.

T1 failure modes (pre-RL):
- `filter_positive`: parse=10 — model emitting `->` and `[a` type tokens inside body expressions (HOF composition training data polluting body generation)
- `identity`: compile=7, parse=2 — model generating factorial-like recursive arithmetic for `Int -> Int` (composition functions shifted the type-conditioned context)
- `tail`: compile=10 — head/tail confusion, stochastic (passes at k=3, fails at k=10 by chance)

### RL — 3 rounds

| Metric | Sprint 5b RL | Sprint 6 RL | Delta |
|--------|-------------|-------------|-------|
| Tier 1 | 22/22 | 22/22 | = |
| Tier 2 | 1/10 | **0/10** | **-1** |
| Tier 3 | 2/6 | 2/6 | = |
| Total | 25/38 | **24/38** | **-1** |

Sprint 6 RL is marginally **worse** than Sprint 5b RL. Sprint 5b had `accum_sum` as the sole T2 win; Sprint 6 lost it. All T2 tasks produced 0 winners across all 3 RL rounds — the reward signal never fired, so RL had nothing to reinforce.

### Sprint 6 targets — all missed

| Target | Goal | Result | Status |
|--------|------|--------|--------|
| T1 no regression | 22/22 | 22/22 | ✅ (via RL) |
| T2 improvement | ≥ 5/10 | 0/10 | ❌ |
| T3 improvement | ≥ 4/6 | 2/6 | ❌ |
| Total | ≥ 31/38 | 24/38 | ❌ |
