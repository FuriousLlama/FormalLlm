# Lessons Learned — Sprint 6

Running log of non-obvious findings, gotchas, and decisions made during development.

---

## 2026-02-23 — GHC Module Search Path (Phase 1)

### GHC does not add the source file's directory to the module search path automatically

When invoking `ghc <flags> sprint/extract/Compositions.hs` from the repo root, GHC searches for imported modules in the **current working directory** (the repo root), not in the source file's directory (`sprint/extract/`). The import `import Functions` fails with "Could not find module 'Functions'" even though `Functions.hs` is right next to `Compositions.hs`.

This is counterintuitive — many tools implicitly search the file's own directory. GHC does not. The GHC docs confirm: the search path consists of the current working directory plus any `-i` paths specified explicitly.

Fix: add `-i<src_dir>` dynamically in `dump_core()`:

```python
src_dir = os.path.dirname(os.path.abspath(hs_file))
flags = GHC_FLAGS + [f'-i{src_dir}']
```

This is backward-compatible: for `Functions.hs` the `-i` adds the same directory GHC was already searching (`.`), so existing extraction is unaffected. For `Compositions.hs` it adds `sprint/extract/` and the import resolves.

**Rule**: when invoking GHC programmatically from a fixed working directory, always add the source file's directory to the import search path with `-i<dir>`. Never assume GHC will find sibling modules.

### Cross-module references look identical to same-module references in the Core dump

After adding `-isprint/extract`, the composition functions in the Core dump reference `fSum`, `fFilter`, etc. without any module prefix — the same as if they were defined in the same file. This is because `-dsuppress-module-prefixes` strips `Functions.fSum` → `fSum`.

This means the de Bruijn parser and the clean pipeline treat cross-module compositions exactly like the existing same-module compositions (fSumSquares, fSumPositive in Functions.hs). No changes to parse_ghc_core.py beyond the -i fix were needed.

**Implication**: the separate-module approach has no downside compared to the same-file fallback. The IR is identical. The separate file is cleaner for maintaining the composition corpus independently of Functions.hs.

### -fforce-recomp causes GHC to dump Core for dependency modules too

With `-fforce-recomp`, GHC recompiles both `Compositions.hs` and its dependency `Functions.hs`. Both modules' Core dumps appear in stdout — Functions.hs first, then Compositions.hs. The combined dump is large (~2,500+ bindings total).

`split_bindings()` finds the first `====================` marker and parses everything after it. With the combined dump, it parses all ~200 Functions bindings plus the 80 Compositions bindings. The `--filter compositions.txt` step then correctly selects only the 80 composition functions.

This is correct and efficient: no extra filtering step is needed, and the pipeline handles multi-module dumps transparently.

**Watch item**: if Functions.hs is very large and the combined dump becomes slow to parse, a workaround would be to pre-compile Functions.hs separately (generating `Functions.dyn_hi`) so GHC skips recompiling it. For the current corpus size this is not an issue.

---

## 2026-02-23 — Composition IR Structure (Phase 1)

### GHC -O0 preserves nested function application exactly as written

All 80 composition functions extracted with their multi-step structure intact. The de Bruijn IR for `fSumAbs xs = fSum (fMap fAbs xs)` is:

```
Lam xs → App fSum (App (App fMap fAbs) xs)
```

`fSum` and `fMap` appear as free-variable references (`Var`) that the lambda-lifter promotes to outer binders. The resulting de Bruijn form has the nested App structure the model needs to learn.

At `-O0`, GHC does not inline, fuse, or beta-reduce cross-function calls. This was expected from the existing fSumSquares/fSumPositive patterns but was confirmed empirically with the probe.

### Boolean operators in lambdas extract cleanly

`fCountOddPositive xs = fLength (fFilter (\x -> fOdd x && fIsPositive x) xs)` uses `&&` inside a lambda predicate. GHC Core represents `&&` as a function call (`GHC.Classes.&&`), which with `-dsuppress-module-prefixes` becomes `&&`. The clean.py passes this through: `&&` is not in any noise filter pattern, and the resulting IR has 5 App nodes (filter, length, lambda, odd, isPositive, plus `&&`).

**Non-obvious**: `fAnd` is already in the corpus (wraps `&&` as `fAnd x y = x && y`), but the lambda-internal `&&` goes through a different code path in GHC Core — it appears as a direct operator application, not as the `fAnd` wrapper. Both forms end up in the vocabulary since `&&` appears frequently enough to pass min_freq.

### Negative integer literals in arguments require parentheses in Haskell source

`fMaybeToNeg mx = fFromMaybe (-1) mx` — the `(-1)` must be parenthesised or GHC parses `-` as a binary operator applied to `fFromMaybe` and `1`. Without parens: parse error. With parens: GHC Core emits a literal integer node `I# (-1#)` which the clean pipeline strips the `I#` noise from, leaving a clean negative literal in the IR.

This is standard Haskell but easy to forget when writing many functions quickly.

### Tuple-destructuring lambdas work but produce case expressions in Core

`fMapPairs f xs = fMap (\(a, b) -> (f a, f b)) xs` — the tuple lambda `\(a, b) -> ...` is syntactic sugar. GHC Core desugars it to a `case ds of { (a, b) -> ... }`. The clean pipeline handles `case` expressions and tuple constructors correctly, and the resulting IR validates without noise violations.

**Design note**: tuple-destructuring patterns appear less frequently in the training corpus than simple value lambdas. The clean IR for `fMapPairs` contains a `Case` node inside a `Lam`, which is a structural pattern the model will need to learn for the `map_pair` benchmark task. This is intentional.

### All 80 functions pass clean.py with 0 noise violations (Phase 1)
<!-- retaining header for searchability -->

No functions were dropped despite using:
- Boolean operators (`&&`) in lambda predicates
- Negative integer literals (`(-1)`)
- Tuple destructuring lambdas
- Three-level nested compositions (`fLength (fFilter fIsPositive (fCatMaybes xs))`)
- Partial application of multi-argument functions (`fClamp 0 100`)

The clean pipeline is more robust than expected for the composition patterns needed in Sprint 6.

---

## 2026-02-23 — Tokenizer / Corpus Merge (Phase 2)

### forall-quantified types in compositions don't normalize to concrete types

The higher-order functions in Category 6 have polymorphic types that survive normalization as `forall`-prefixed strings: `fApplyThrice` → `forall a. (a -> a) -> a -> a`, `fMapTwice` → `forall a. (a -> a) -> [a] -> [a]`. These don't match any benchmark task type exactly (benchmark tasks are all concrete `Int`/`[Int]` types), so they won't appear as type-grouped context for benchmark tasks.

This is expected and acceptable: the HOF compositions still contribute to the model's base distribution over body structures (nested App nodes, self-application), even if they don't appear as direct context peers for benchmark tasks. Type-grouped context is a training regularizer, not the only signal.

---

## 2026-02-23 — Phase 3: Training and Evaluation Findings

### The RL baseline and the benchmark script give different pass@10 results for the same checkpoint

The RL evaluation (`rejection_sample.py`) and the standalone benchmark (`run.py`) use different prompting strategies. At the pre-RL stage, `run.py` showed `any_positive` at 0/10 (parse=10), while the RL baseline evaluation showed it at 10/10 (pass@10=1.000). Same checkpoint, same temperature, same k.

The difference is in how few-shot context is assembled and possibly in how type-matched peers are selected. The RL evaluator's higher pass@10 counts are not directly comparable to `run.py` benchmark numbers. The `run.py` numbers are the canonical benchmark for comparing sprints; the RL baseline numbers are only useful for tracking relative improvement across RL rounds.

**Rule**: use `make benchmark` output for cross-sprint comparisons, not the RL log's baseline section.

### Pre-RL baseline correction: Sprint 5b T1 was 17/22, not 22/22

The sprint 6 plan used 22/22 T1 as the baseline to match, but this was the *post-RL* Sprint 5b result. The actual pre-RL Sprint 5b benchmark was 17/22. The same 5 tasks fail in both Sprint 5b pre-RL and Sprint 6 pre-RL: `filter_positive`, `any_positive`, `all_positive`, `map_double` (in 5b), etc. — the persistent T1 failures are structural, not fixed by more training data alone.

**Rule**: always record pre-RL benchmark numbers as the training run baseline; post-RL numbers are only meaningful as the sprint's final deliverable.

### Composition training data caused type-token leakage in HOF body generation

After adding 80 composition functions (many with multi-arrow HOF types like `(Int -> Bool) -> [Int] -> [Int]`), the model began emitting `->` and `[a` tokens inside BODY expressions for `filter_positive`. Example:

```
tokens: ... BODY (Lam 2 (Case Var 1 (Alt (PCon [] ) -> [ a ->
```

The model learned to associate `->` with the generation of functions that take predicate arguments, then bled type-vocabulary into expression position. This caused `filter_positive` to go from 0/10 (parse failures due to a different cause) in Sprint 5b to 0/10 (parse failures due to type tokens in body) in Sprint 6 — same symptom, different root cause.

**Implication**: HOF types in the training corpus are double-edged. They teach the model higher-order structure but can pollute the body token distribution if the boundary between TY and BODY sections is not robustly learned.

### Composition corpus did not produce any T2 winners in RL — reward signal never fired

All 10 Tier 2 tasks (count_positive, map_square, sort_desc, sum_doubles, max_abs, take_positives, nub_sort, drop_negatives, accum_sum, count_even) produced 0/10 passes across all 3 RL rounds. With no winners, the fine-tuning loop had no signal for these tasks.

This is the core failure of the Sprint 6 hypothesis. Adding correctly-structured composition examples to the training corpus was expected to teach the model to compose at inference time. It did not. The model still cannot generate `fSort (fFilter fIsPositive xs)` even after seeing 80 similar patterns in training.

**Hypothesis**: the gap is not in recognizing composition structure — the model can generate multi-App expressions. The gap is in *variable binding under de Bruijn indexing for composed types*. In `fCountPositive :: [Int] -> Int`, the body `fLength (fFilter fIsPositive xs)` requires Var 0 to correctly refer to `xs` through two levels of application. The compositions in the corpus use explicit argument names that normalize to Var 0, but the model must also generalize this to novel function combinations. The Hackage corpus may not contain enough two-step compositions of this specific shape.

**What might work**: (1) synthetic corpus generation targeting exactly the T2 function shapes with varied argument counts; (2) explicit chain-of-thought style tokens (STEP1, STEP2) to break composition into stages; (3) retrieval-augmented generation that injects the most structurally similar corpus example as context.

### Sprint 5b's accum_sum T2 win was lost in Sprint 6

Sprint 5b RL produced 1 T2 win (`accum_sum`). Sprint 6 RL produced 0. The `accum_sum` task (`Int -> [Int] -> Int`, fold with accumulator) was likely won in Sprint 5b by luck — it sits on the T1/T2 boundary structurally (single fold, not a composition of two named functions). The expanded composition corpus may have shifted the model's fold-with-accumulator distribution away from the correct form.

**Takeaway**: T2 wins that appear in one sprint and vanish in the next are fragile. They are not evidence of compositional generalization; they are evidence of the task being easier than its tier classification suggests.

### corpus-merge must be force-rebuilt when tokenizer.py changes

`corpus_all.json` timestamps don't change when `tokenizer.py` changes (it only reads JSON). The downstream files `sequences.json`, `tokenizer.json`, and `sequences_aug.json` all depend on `tokenizer.py` logic but Make only tracks file timestamps. After changing `_CORPUS_FUNCTION_NAMES`, manually delete all four generated files before `make corpus` to avoid stale tokenized output.

Sprint 5 documented this for `sequences_aug.json`; the same applies to the full corpus chain after any tokenizer logic change.
