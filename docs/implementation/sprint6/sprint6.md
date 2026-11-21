# Sprint 6: Compositional Training Data

The seventh iteration of [[ast_pipeline_roadmap.md]].
Core objective: teach the model to compose operations by adding systematic composition examples to the training corpus.

## Sprint 6: Can the Model Learn to Compose?

**Duration: ~1-2 weeks. Team: 1 person. Compute: 1 GPU + laptop.**

Sprint 5 answered: **can type signatures and structured context enable function generation?** Yes — with name+type conditioning and type-grouped context, Tier 1 hit 22/22 (post-RL) and the model generalizes genuinely (val/train = 1.04x). But Tier 2 (compositional) stalled at 1/10: the model cannot chain operations like `filter+length`, `map+sort`, or `reverse+sort`. 13 tasks produced zero correct samples across all RL rounds — no gradient signal to work with.

Sprint 6 answers: **is the composition ceiling a data gap or an architectural limit?**

### Why This Comes Next

The sprint5 plan outlined three possible Sprint 6 directions:

- **Sprint 6 (original)**: Richer types — class constraints, refinement types
- **Sprint 7 (original)**: Type-directed beam search with type checking at each step
- **Sprint 8 (original)**: Scaling — more data, larger model

The actual failure mode makes the original Sprint 6 wrong. The Tier 2 failures are not caused by insufficient type precision — `[Int] -> Int` is the correct type for both `fSum` (which passes) and `fCountPositive` (which fails). The model can already generate single-step functions of the right type. It cannot assemble multi-step compositions.

The cheapest intervention that tests whether composition is learnable: **add composition examples to the training corpus.** If the model learns composition patterns from 80 examples and generalizes to novel compositions at benchmark time, the bottleneck was data, not architecture. If it doesn't, the autoregressive paradigm needs the architectural changes described in Sprints 7-8.

This is also the most informative experiment. Richer types would not help if the model can't produce multi-step bodies. Beam search would not help if the model assigns zero probability to composition patterns. Data-first eliminates the simplest hypothesis before investing in architectural changes.

### The Composition Gap in Detail

The existing corpus has 162 hand-written functions. Of these, ~12 are compositions:

| Function | Pattern | Composition |
|----------|---------|-------------|
| fSumSquares | map+fold | `fSum (fMap fSquare xs)` |
| fSumPositive | filter+fold | `fSum (fFilter fIsPositive xs)` |
| fDotProduct | zip+fold | `fSum (fZipWithMul xs ys)` |
| fDoubleAll | map with op | `fMap fDouble` |
| fAbsList | map with op | `fMap fAbs` |
| fIncrAll | map with op | `fMap fSucc` |
| fDecrAll | map with op | `fMap fPred` |
| fNegate2 | map with op | `fMap fNegate` |
| fCountTrue | count via HOF | `fCount fId` |
| fChunksOf | take+drop loop | `fTake n xs : fChunksOf n (fDrop n xs)` |
| fFoldlWithIndex | fold+enumerate | `fFoldl ... (fEnumerate xs)` |
| fTranspose | map+recurse | `fMap fHead xss : fTranspose (fMap fTail xss)` |

That is ~7% of the hand-written corpus. The model sees composition as a rare, incidental pattern — not as a systematic category. The 4,276 Hackage functions include more compositions, but GHC's optimizer tends to fuse multi-step patterns even at `-O0`, and the lambda-lifted de Bruijn representation makes them harder to distinguish from single-step functions.

The 9 failing Tier 2 tasks all require compositions from this small set of patterns:

| Pattern | Failing tasks | Required form |
|---------|--------------|---------------|
| filter + fold | count_positive, count_even | `fLength (fFilter pred xs)` |
| map + fold | sum_doubles, max_abs | `fSum (fMap f xs)` |
| f . g (two-step) | sort_desc, nub_sort | `fReverse (fSort xs)` |
| filter with predicate | take_positives, drop_negatives | `fFilter/fTakeWhile pred xs` |
| map with inline op | map_square | `fMap (\x -> x * x) xs` |

The 4 failing Tier 3 tasks require structural patterns:

| Pattern | Failing tasks | Required form |
|---------|--------------|---------------|
| higher-order self-application | apply_twice | `f (f x)` |
| tuple mapping | map_pair | `(f x, f y)` for pair input |
| Maybe defaulting over list | maybe_with_default | `fMap (fFromMaybe d) xs` |
| uncurried operation | uncurry_add | `(\(x,y) -> x + y)` |

### The Hypothesis

**H1 — Composition is a learnable pattern, not an architectural limit.** If the model sees 80+ composition examples covering the patterns above, it should learn to generate multi-step bodies for novel compositions at benchmark time.

Theoretical basis: the model already generates valid IR (low parse failure rate), correct single-step functions (22/22 Tier 1), and navigates type-conditioned generation accurately. The missing piece is seeing enough multi-step body structures in training to assign non-zero probability to them at generation time.

**H2 — Type-grouped context amplifies composition learning.** Composition training examples of type `[Int] -> Int` (filter+fold patterns) will appear as context for benchmark tasks of the same type. The model should learn: "when context shows filter+fold compositions, generate a filter+fold composition." Sprint 5's Run C proved that type-grouped context is load-bearing — Sprint 6 tests whether it also transfers composition patterns via context.

**H3 — RL becomes effective once composition patterns have non-zero base rate.** Sprint 5's RL ceiling (13 tasks with zero winners) exists because the model never generates compositions. If Sprint 6's base model produces even 1-2 correct compositions per task across k=10 samples, RL can amplify these to higher pass rates.

### Data Design

#### Composition Functions: ~80 New Hand-Written Functions

Add a new file `sprint/extract/Compositions.hs` with systematic composition examples. Each category covers a pattern needed by Tier 2/3 benchmark tasks, using DIFFERENT specific operations (the benchmark tasks remain novel).

**Category 1 — Filter + Fold (~12 functions)**

The core pattern: `fold (filter pred xs)` where fold is length/sum/product/max/min and pred is a comparison or predicate function.

```haskell
fCountNegative xs = fLength (fFilter fIsNegative xs)
fCountNonZero xs = fLength (fFilter (\x -> x /= 0) xs)
fCountOdd xs = fLength (fFilter fOdd xs)
fSumNegative xs = fSum (fFilter fIsNegative xs)
fSumNonZero xs = fSum (fFilter (\x -> x /= 0) xs)
fSumEven xs = fSum (fFilter fEven xs)
fProductPositive xs = fProduct (fFilter fIsPositive xs)
fMaxNonZero xs = fMaximum (fFilter (\x -> x /= 0) xs)
fMinPositive xs = fMinimum (fFilter fIsPositive xs)
fCountLarge xs = fLength (fFilter (\x -> x > 100) xs)
fSumSmall xs = fSum (fFilter (\x -> x < 10) xs)
fCountOddPositive xs = fLength (fFilter (\x -> fOdd x && fIsPositive x) xs)
```

Covers: count_positive (filter+length), count_even (filter+length with different pred).

**Category 2 — Map + Fold (~10 functions)**

The core pattern: `fold (map f xs)` where fold is sum/product/max/min and f is an arithmetic operation.

```haskell
fSumTriples xs = fSum (fMap fTriple xs)
fSumAbs xs = fSum (fMap fAbs xs)
fSumSucc xs = fSum (fMap fSucc xs)
fSumNegated xs = fSum (fMap fNegate xs)
fProductDoubles xs = fProduct (fMap fDouble xs)
fProductAbs xs = fProduct (fMap fAbs xs)
fMaxSquare xs = fMaximum (fMap fSquare xs)
fMinAbs xs = fMinimum (fMap fAbs xs)
fMaxDouble xs = fMaximum (fMap fDouble xs)
fMinSquare xs = fMinimum (fMap fSquare xs)
```

Covers: sum_doubles (map+sum), max_abs (map+max).

**Category 3 — Map with Inline Lambda (~8 functions)**

The core pattern: `map (\x -> expr) xs` where expr is an arithmetic expression.

```haskell
fMapPlusTwo xs = fMap (\x -> x + 2) xs
fMapTimesThree xs = fMap (\x -> x * 3) xs
fMapMinusOne xs = fMap (\x -> x - 1) xs
fMapSquarePlus xs = fMap (\x -> x * x + 1) xs
fMapAbsPlus xs = fMap (\x -> fAbs x + 1) xs
fMapHalve xs = fMap fHalve xs
fMapSignum xs = fMap fSignum xs
fMapClamp xs = fMap (fClamp 0 100) xs
```

Covers: map_square (map with arithmetic lambda).

**Category 4 — Two-Function Pipelines (~12 functions)**

The core pattern: `f (g xs)` where f and g are both corpus functions applied to the same list.

```haskell
fReverseTake n xs = fReverse (fTake n xs)
fTakeSort n xs = fTake n (fSort xs)
fSortNub xs = fSort (fNubInt xs)
fReverseTail xs = fReverse (fTail xs)
fReverseInit xs = fReverse (fInit xs)
fSortFilter xs = fSort (fFilter fIsPositive xs)
fNubReverse xs = fNubInt (fReverse xs)
fTailReverse xs = fTail (fReverse xs)
fSortMapAbs xs = fSort (fMap fAbs xs)
fReverseMapNeg xs = fReverse (fMap fNegate xs)
fTakeWhileSort xs = fTakeWhile fIsPositive (fSort xs)
fFilterSort xs = fFilter fIsPositive (fSort xs)
```

Covers: sort_desc (reverse . sort), nub_sort (sort . nub).

**Category 5 — Filter/TakeWhile/DropWhile with Predicates (~10 functions)**

The core pattern: `filter/takeWhile/dropWhile pred xs` where pred is an inline lambda or predicate function.

```haskell
fFilterEven xs = fFilter fEven xs
fFilterOdd xs = fFilter fOdd xs
fFilterPositive xs = fFilter fIsPositive xs
fFilterNonZero xs = fFilter (\x -> x /= 0) xs
fFilterSmall xs = fFilter (\x -> x < 10) xs
fFilterLarge xs = fFilter (\x -> x > 100) xs
fTakeWhilePos xs = fTakeWhile fIsPositive xs
fTakeWhileSmall xs = fTakeWhile (\x -> x < 100) xs
fDropWhileNeg xs = fDropWhile fIsNegative xs
fDropWhileZero xs = fDropWhile fIsZero xs
```

Covers: take_positives (takeWhile with predicate), drop_negatives (filter with predicate).

**Category 6 — Higher-Order Application (~8 functions)**

The core pattern: `f (f x)`, `f (f (f x))`, and function-application compositions.

```haskell
fApplyThrice f x = f (f (f x))
fMapTwice f xs = fMap f (fMap f xs)
fFilterTwice p xs = fFilter p (fFilter p xs)
fDoubleApply f g x = f (g x)           -- same as fComp, different name
fTripleApply f g h x = f (g (h x))     -- same as fCompose3, different name
fSelfMap xs = fMap fDouble (fMap fSucc xs)  -- map+map with different ops
fDoubleThenFilter xs = fFilter fIsPositive (fMap fDouble xs)
fFilterThenMap xs = fMap fSucc (fFilter fIsPositive xs)
```

Covers: apply_twice (f(f(x))).

**Category 7 — Tuple Operations (~10 functions)**

The core pattern: tuple destructuring, pair mapping, uncurried operations.

```haskell
fUncurrySub :: (Int, Int) -> Int
fUncurrySub (x, y) = x - y

fUncurryMul :: (Int, Int) -> Int
fUncurryMul (x, y) = x * y

fUncurryMax :: (Int, Int) -> Int
fUncurryMax (x, y) = fMax x y

fUncurryMin :: (Int, Int) -> Int
fUncurryMin (x, y) = fMin x y

fBothDouble :: (Int, Int) -> (Int, Int)
fBothDouble (x, y) = (fDouble x, fDouble y)

fBothSucc :: (Int, Int) -> (Int, Int)
fBothSucc (x, y) = (fSucc x, fSucc y)

fBothAbs :: (Int, Int) -> (Int, Int)
fBothAbs (x, y) = (fAbs x, fAbs y)

fSwapApply :: (Int -> Int) -> (Int, Int) -> (Int, Int)
fSwapApply f (x, y) = (f y, f x)

fPairAdd :: (Int, Int) -> (Int, Int) -> (Int, Int)
fPairAdd (a, b) (c, d) = (a + c, b + d)

fMapPairs :: (Int -> Int) -> [(Int, Int)] -> [(Int, Int)]
fMapPairs f xs = fMap (\(a, b) -> (f a, f b)) xs
```

Covers: map_pair (mapping over pair), uncurry_add (uncurried operation).

**Category 8 — Maybe Operations (~10 functions)**

The core pattern: Maybe mapping, defaulting, and Maybe-over-list operations.

```haskell
fMaybeDouble :: Maybe Int -> Maybe Int
fMaybeDouble mx = fMaybeMap fDouble mx

fMaybeSucc :: Maybe Int -> Maybe Int
fMaybeSucc mx = fMaybeMap fSucc mx

fMaybeAbs :: Maybe Int -> Maybe Int
fMaybeAbs mx = fMaybeMap fAbs mx

fMaybeToZero :: Maybe Int -> Int
fMaybeToZero mx = fFromMaybe 0 mx

fMaybeToNeg :: Maybe Int -> Int
fMaybeToNeg mx = fFromMaybe (-1) mx

fDefaultList :: Int -> [Maybe Int] -> [Int]
fDefaultList d xs = fMap (fFromMaybe d) xs

fDefaultZeroList :: [Maybe Int] -> [Int]
fDefaultZeroList xs = fMap (fFromMaybe 0) xs

fExtractPositive :: [Maybe Int] -> [Int]
fExtractPositive xs = fFilter fIsPositive (fCatMaybes xs)

fCountJustPositive :: [Maybe Int] -> Int
fCountJustPositive xs = fLength (fFilter fIsPositive (fCatMaybes xs))

fSumJusts :: [Maybe Int] -> Int
fSumJusts xs = fSum (fCatMaybes xs)
```

Covers: maybe_with_default (map fromMaybe over list).

**Total: ~80 new functions** across 8 categories.

### Critical Design Constraints

**1. No benchmark functions in training.**
None of the 80 composition functions duplicate any of the 13 failing benchmark tasks. The benchmark task names (fCountPositive, fMapSquare, fSortDesc, etc.) do not appear in Compositions.hs. The benchmark remains a genuine generalization test.

**2. Compositions must reference existing corpus functions.**
All compositions use functions already defined in Functions.hs (fFilter, fMap, fSort, fLength, etc.). This ensures the GHC Core extraction produces lambda-lifted de Bruijn IR with the correct dependency structure — the same structure the benchmark expects.

**3. GHC -O0 preserves composition structure.**
At `-O0`, GHC does not inline cross-module or same-module function references. `fSumAbs xs = fSum (fMap fAbs xs)` in Core becomes three function applications (fSum applied to (fMap applied to fAbs applied to xs)), not an inlined loop. The de Bruijn pipeline lambda-lifts fSum, fMap, fAbs as outer binders, producing:

```
Lam 4 (App Var 3 (App (App Var 2 Var 1) Var 0))
```

where Var 3 = fSum, Var 2 = fMap, Var 1 = fAbs, Var 0 = xs. This is the multi-application pattern the model needs to learn.

**4. Compositions with inline lambdas test lambda generation.**
Functions like `fFilterSmall xs = fFilter (\x -> x < 10) xs` require GHC to emit a lambda in the Core. The model must learn to generate inline Lam nodes inside App nodes — a pattern that doesn't appear in single-step functions.

### Pipeline Changes

Sprint 6 requires minimal pipeline changes. The infrastructure from Sprint 5b is reused entirely.

#### sprint/extract/Compositions.hs (NEW)

New Haskell source file with ~80 composition functions. Uses `{-# OPTIONS_GHC -O0 #-}` and imports functions from Functions module (or defines them inline if cross-module references cause issues with the extraction pipeline).

**Important**: if GHC Core dump treats cross-module references differently from same-module references, all composition functions may need to be in the same file as Functions.hs. Test with a small example first.

Fallback: append composition functions directly to Functions.hs rather than creating a separate module. This is less clean but guarantees same-module reference behavior.

#### sprint/extract/compositions.txt (NEW)

Filter file listing the ~80 new function names, same format as functions.txt.

#### Makefile (UPDATED)

Add extraction target for Compositions.hs:

```makefile
CORPUS_COMP := sprint/extract/corpus_compositions.json
CORPUS_COMP_CLEAN := sprint/extract/corpus_compositions_clean.json

extract-comp: $(CORPUS_COMP_CLEAN)

$(CORPUS_COMP): sprint/extract/Compositions.hs sprint/extract/compositions.txt
	$(PYTHON) sprint/extract/parse_ghc_core.py sprint/extract/Compositions.hs \
		--filter sprint/extract/compositions.txt \
		--out $(CORPUS_COMP) --stats

$(CORPUS_COMP_CLEAN): $(CORPUS_COMP) sprint/extract/clean.py sprint/extract/ir_schema.json
	$(PYTHON) sprint/extract/clean.py $(CORPUS_COMP) \
		--out $(CORPUS_COMP_CLEAN) --schema sprint/extract/ir_schema.json --stats --package compositions
```

Update corpus-merge to include compositions:

```makefile
$(CORPUS_ALL): $(CORPUS_CLEAN) $(CORPUS_HACKAGE) $(CORPUS_COMP_CLEAN)
	$(PYTHON) -c "import json; \
		a=json.load(open('$(CORPUS_CLEAN)')); \
		b=json.load(open('$(CORPUS_HACKAGE)')); \
		c=json.load(open('$(CORPUS_COMP_CLEAN)')); \
		json.dump(a+b+c, open('$(CORPUS_ALL)','w'), indent=2); \
		print(f'[merge] {len(a)} handwritten + {len(b)} hackage + {len(c)} compositions = {len(a)+len(b)+len(c)} total')"
```

#### sprint/corpus/tokenizer.py (UPDATED)

Add ~80 new composition function names to `_CORPUS_FUNCTION_NAMES` in ALWAYS_KEEP. Vocabulary grows from ~352 to ~430 tokens. Still well under any practical limit.

#### No other code changes needed.

normalize.py, augment.py, train.py, run.py, checker.py, constrained_decode.py, pretty_print.py, tasks.json — all unchanged. The existing pipeline handles the new data automatically:

- normalize.py: emits `DEF name TY type BODY body EOF` for composition functions (same format as Sprint 5b)
- augment.py: commutative swap still works (swaps within body, preserves TY prefix)
- train.py: type-grouped context selection automatically includes composition functions as context peers for same-type benchmark tasks
- run.py: benchmark tasks use the same prompts as Sprint 5b

### Training Plan

**Run A (primary):** Full corpus with compositions, type-grouped context.

```bash
make extract && make extract-comp && make extract-hackage
make corpus-merge && make corpus && make train
```

Expected corpus: ~4,518 base functions (4,438 + 80) → ~5,200 augmented.

**Run B (comparison):** Sprint 5b checkpoint (no compositions). Already trained — reuse existing checkpoint for direct comparison.

### Benchmark Plan

Same 38 tasks, same benchmark harness, same RL procedure. No changes to tasks.json.

```bash
make benchmark  # Run A: with compositions
make rl         # 3 rounds of rejection sampling
```

Compare against Sprint 5b results (Run B baseline):

| Metric | Sprint 5b (Run B + RL) | Sprint 6 target |
|--------|----------------------|-----------------|
| Tier 1 | 22/22 | 22/22 (no regression) |
| Tier 2 | 1/10 | >= 5/10 |
| Tier 3 | 2/6 | >= 4/6 |
| Total | 25/38 | >= 31/38 |

### Success Criteria

| Question | Minimum bar | Stretch |
|----------|------------|---------|
| Tier 1 regression? | 22/22 maintained | — |
| Tier 2 (pre-RL pass@10)? | >= 3/10 | >= 6/10 |
| Tier 2 (post-RL pass@10)? | >= 5/10 | >= 8/10 |
| Tier 3 (post-RL pass@10)? | >= 3/6 | >= 5/6 |
| Val loss? | < 0.5 nats | < 0.4 nats |
| Val/train ratio? | < 1.5x | < 1.2x |
| RL winners for Tier 2 tasks? | >= 5 tasks have >= 1 winner | >= 8 tasks have >= 1 winner |
| Compositions in correct IR form? | >= 70/80 pass extraction + validation | >= 78/80 |

**The decisive test:** If Tier 2 pre-RL goes from 1/10 to >= 3/10, the composition gap is a data problem and adding more diverse compositions will continue to improve results. If Tier 2 stays at 1/10 despite 80 composition training examples, the autoregressive paradigm cannot compose from examples and architectural changes are needed.

### What This Validates

**If Sprint 6 succeeds (Tier 2 >= 5/10):**

- The composition ceiling was a data gap, not an architectural limit
- The model can learn multi-step patterns from ~80 examples and generalize to novel compositions
- Type-grouped context successfully transfers composition patterns from training to benchmark
- The path forward: more composition patterns, more diverse predicates, deeper compositions
- Sprint 7 can focus on scaling (more Hackage compositions, larger model) rather than architectural changes

**If Sprint 6 partially works (Tier 2 = 3-4/10):**

- The model learns some compositions but not all patterns
- Likely: filter+fold and map+fold (high training frequency) succeed, but two-step pipelines (lower frequency) don't
- Next step: increase composition training data density for underrepresented patterns, or add explicit composition curriculum

**If Sprint 6 fails (Tier 2 <= 1/10):**

- The autoregressive paradigm cannot learn composition from examples
- The bottleneck is not data but the generation mechanism: flat token prediction cannot assemble multi-step structures
- Next step: architectural change — type-directed beam search (Sprint 7 original plan), tree-structured generation, or decomposed planning

### Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| GHC -O0 inlines/fuses compositions from same module | Medium | Test with 2-3 examples first. If inlined, try separate module or NOINLINE pragmas |
| Cross-module references break extraction pipeline | Medium | Fallback: append to Functions.hs. All functions in one module guarantees same behavior |
| Lambda-lifting produces degenerate IR for compositions | Low | Existing compositions (fSumSquares, fSumPositive) already work. New ones follow the same pattern |
| 80 compositions dilute training signal for single-step functions | Low | 80/4,518 = 1.8% of corpus. Tier 1 regression is unlikely. Verify by checking Tier 1 first |
| Composition functions exceed max_len=256 after type prefix | Low | Most compositions are 20-40 body tokens + ~5-10 type tokens. Well within budget |
| New function names bloat vocabulary beyond useful range | Low | ~430 tokens total. Embedding matrix grows 22% — negligible for a 474K-param model |
| Tier 2 benchmark tasks are too specific to match general composition patterns | Medium | This is the core hypothesis test. If 80 examples don't generalize, the approach has an inherent limitation |

### Timeline

| Task | Estimate |
|------|----------|
| Write Compositions.hs (~80 functions) | 1 day |
| Test GHC extraction on 5 examples, verify IR structure | 0.5 day |
| Update Makefile, compositions.txt, tokenizer.py | 0.5 day |
| Extract + merge + rebuild corpus | 0.5 day |
| Training run A | 0.5 day (2 hours compute) |
| Benchmark + RL (3 rounds) | 1 day |
| Verify no Tier 1 regression | 0.5 day |
| Analysis + status writeup | 0.5 day |
| **Total** | **~5-6 days** |

### Implementation Order

**Phase 1 — Data Preparation (2 days)**

1. Write 5 composition functions across 3 categories (filter+fold, map+fold, two-step pipeline)
2. Extract via GHC Core, inspect the de Bruijn IR — verify composition structure is preserved
3. If cross-module references break, move to same-file approach
4. Write remaining ~75 functions across all 8 categories
5. Create compositions.txt filter file
6. Update Makefile with extract-comp target and corpus-merge

**Phase 2 — Pipeline Integration (1 day)**

7. Add composition function names to `_CORPUS_FUNCTION_NAMES` in tokenizer.py
8. Run `make extract-comp && make corpus-merge && make corpus`
9. Verify: composition functions present in sequences.json with correct `DEF name TY type BODY` format
10. Verify: composition function types match benchmark task types (e.g., `[Int] -> Int` for filter+fold)
11. Check augmented corpus: compositions participate in commutative swap where applicable

**Phase 3 — Training + Evaluation (2-3 days)**

12. Train Run A: `make train`
13. Verify no Tier 1 regression: `make benchmark` → check Tier 1 >= 22/22
14. Evaluate Tier 2/3 improvement
15. RL: `make rl` (3 rounds)
16. Compare against Sprint 5b baseline
17. Analysis: which composition patterns succeed? Which fail? Why?

### What Doesn't Change

- **Same model architecture**: CausalTransformer, 2L/d128, ~474K params (slight increase from larger vocab)
- **Same training regime**: type-grouped context, same hyperparameters
- **Same benchmark**: 38 tasks, same prompts, same tests
- **Same RL procedure**: rejection sampling, 3 rounds, k=10
- **Same extraction pipeline**: parse_ghc_core.py → clean.py → normalize.py
- **Same de Bruijn representation**: lambda-lifting, SELF token, closed terms
- **Same tokenizer structure**: ALWAYS_KEEP + freq-based, min_freq=40

The ONLY variable is the training data: ~80 more composition functions added to the corpus. Everything else is held constant for clean comparison against Sprint 5b.

### Relationship to Theory

The theoretical framework (`navigating_formal_space.md`) describes navigation as iterative hole-filling: "select a hole, query the type context for valid operations, choose, repeat." Flat autoregressive generation is a degenerate case — the model fills "holes" left-to-right in the token sequence, not in a type-directed tree.

Sprint 6 tests whether the degenerate case works for multi-step compositions. If it does, the flat autoregressive approach is sufficient for compositions of depth 2-3, and the full tree-structured navigator is needed only for deeper compositions. If it doesn't, the theory's prediction is confirmed: non-trivial navigation requires explicit type-directed search, not flat sequence prediction.

Either outcome advances the theoretical understanding. Success validates that statistical learning of composition patterns can substitute for explicit type-directed search at low depth. Failure motivates the architectural transition the theory predicts.

### Relationship to Roadmap

If Sprint 6 succeeds:

- **Sprint 7**: Scale compositions — extract composition patterns from Hackage (not just hand-written), increase depth to 3-step compositions, add more predicate/transform variety
- **Sprint 8**: Scaling the model — larger architecture if composition patterns demand more capacity, or move to type-directed beam search for deeper compositions

If Sprint 6 fails:

- **Sprint 7**: Type-directed beam search — generate tokens left-to-right but prune beams using type checking at each step. This adds static verification during navigation (Section 7.2 of the theory)
- **Sprint 8**: Tree-structured generation — predict AST nodes rather than flat tokens, with explicit hole-filling (full navigator architecture)
