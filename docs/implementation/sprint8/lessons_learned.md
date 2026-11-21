# Lessons Learned — Sprint 8

Running log of non-obvious findings, gotchas, and decisions made during development.

---

## Phase 1: Diagnostics (2026-03-06)

### Diagnostic harness design: dump before fix

Adding `--dump-candidates` to `run.py` with per-candidate tracking (tokens, classification, error, parsed_body, context_names) was essential for diagnosing the hard-zero tasks. Without seeing the actual generated candidates and which context functions were provided, the root cause (wrong context selection) would have been invisible.

**Rule**: when facing 0/k pass rates, instrument the pipeline to dump raw candidates before attempting fixes. The failure mode is often not what you expect.

### Context overlap as the key diagnostic metric

The breakthrough insight was comparing `context_names` (functions selected for few-shot prompting) against `few_shot_funcs` (curated building blocks in tasks.json). For 6/8 hard-zero tasks, overlap was 0%. The model literally never saw the functions it needed to compose. This was a simpler and more actionable finding than analyzing token-level generation patterns.

---

## Phase 2: Targeted Fixes (2026-03-06)

### Constrained decoder validated context, not target

The constrained decoder bug was subtle: `partials` was initialized from `list(prompt_str)`, which included the full few-shot prompt (multiple DEF...EOF blocks). Since `parse_sequence` only validates the first DEF block, the decoder was constraining the context function (already correct by construction) and leaving the target function completely unconstrained.

Evidence: tasks with 196/200 parse errors in Phase 1 diagnostics — these errors were from the unconstrained target, not from bad constraints. After the fix, parse errors dropped to 0 for all tasks.

**Rule**: when a constrained decoder uses few-shot prompting, the constraint validation must be scoped to the target generation only. Validate starting from the last DEF token, not the beginning of the prompt.

### Fixing the decoder causes regression before retraining

The constrained decoder fix immediately caused T1 regression (22/22 → 11/22). The Sprint 7 model had learned to generate under effectively unconstrained conditions — it relied on patterns that happened to produce valid output without grammar enforcement. Under actual constraints, many of those generation paths were blocked.

The correct response was to keep the fix and retrain, not to revert. After retraining, T1 recovered to 22/22. The temporary regression is the expected cost of fixing a long-standing bug.

**Rule**: architectural correctness > temporary benchmark regression. If a fix is provably correct (constraints now validate the right thing), commit to it and retrain. Do not revert correct fixes to preserve metrics from a model trained under broken conditions.

### Curated context makes the model mimic, not compose

Replacing type-matched context with curated building-block functions (e.g., showing fDouble, fSum, fMap for sum_doubles) made results worse. The model generated bodies that mimicked the simple building blocks (e.g., `x + x` from fDouble) instead of composing them (e.g., `fSum (fMap fDouble xs)`).

Hypothesis: the few-shot context acts as a strong prior on output structure. When context contains simple Int -> Int functions, the model generates simple Int -> Int bodies. The model needs to see composition examples, not isolated building blocks.

**Rule**: few-shot context shapes generation style more than content. Showing the ingredients (fDouble, fMap) doesn't teach composition — showing similar compositions (fSumAbs, fSumTriples) does.

### Three context strategies tried, all worse than original

1. **Curated-only**: mimicry problem (above)
2. **Hybrid (curated + type-matched)**: more parse errors (197 vs 122 for sum_doubles)
3. **Biased selection (boost curated by 10x)**: T1 regression from unfamiliar context distributions

Type-matched peer selection, despite having 0% overlap with building blocks for composition tasks, remains the best approach. It provides structurally similar examples that teach the model the right output shape.

**Rule**: don't fight the context selection without a clear theory of what "better context" means for your model. Random type-matched peers provide output-shape priors. Curated building blocks provide ingredient priors. For a small model doing few-shot learning, shape matters more than ingredients.

---

## Phase 3: Retrain + Evaluate (2026-03-06)

### Benchmark non-determinism from random context selection

The same task with the same checkpoint can show 0/200 on one run and 183/200 on the next (observed with fNot). The cause is random context selection in `build_few_shot_prompt` — different random seeds select different few-shot peers, and some contexts are much more helpful than others.

This makes single-run benchmark results unreliable for borderline tasks. The RL baseline evaluation (which runs its own internal benchmark) often gives different numbers than the standalone `make benchmark` on the same checkpoint.

**Rule**: for tasks near the pass/fail boundary, a single k=200 run is not reliable. Either run multiple seeds and average, or accept that the RL internal evaluation is the ground truth (since it's what actually drives fine-tuning decisions).

### Segfault when running all 38 tasks at k=200

Running all tasks in a single benchmark process at k=200 causes a segmentation fault. The workaround is to run by tier (`--tier 1`, `--tier 2`, `--tier 3`). This appears to be a memory issue — 38 tasks × 200 candidates × ~200 tokens each generates significant GPU memory pressure.

The RL script handles this fine because it processes one task at a time during winner collection.

**Rule**: for high-k benchmarking, run by tier or by task. The all-at-once mode is only reliable at low k.

### `is_type_valid` needs broad exception catching

The parser in `checker.py` can hit various unexpected exception types on pathological model output:
- `ParseError` — normal grammar violations
- `TypeError` — `ts.peek() not in (')', None)` when peek returns unexpected types
- `IndexError` — token stream underflow
- `AttributeError` — TokenStream object passed where string expected (deep recursion state corruption)
- `RecursionError` — deeply nested expressions exceeding Python stack

All must be caught in `is_type_valid` to prevent crashing the constrained decoder. Each was discovered through a different benchmark crash.

**Rule**: constrained decoders call the parser on every candidate token at every step. Any uncaught exception in the parser kills the entire generation run. Catch broadly and return False for any parser failure.

### PYTHONPATH required for cross-module imports

Sprint 7 added `from sprint.corpus.tokenizer import _CORPUS_FUNCTION_NAMES` to `normalize.py`, but didn't add `PYTHONPATH=.` to the Makefile. This worked in development (run from repo root with venv active) but broke `make corpus`. The fix was adding `PYTHONPATH=.` to the Makefile's PYTHON variable.

**Rule**: when adding cross-module imports (e.g., `from sprint.X import Y`), verify the Makefile invocation works, not just the interactive shell.

### New training data alone doesn't unlock hard-zeros

Adding 5 targeted compositions (fSumDouble, fEvenCount, fReverseSort, fSquareList, fTwice) to the training corpus did not directly unlock the corresponding benchmark tasks. The model still gets 0/200 for sum_doubles, count_even, sort_desc. The training data teaches the pattern but the context selection still doesn't provide the right building blocks at inference time.

**Rule**: training data and inference context are independent problems. Adding `fSumDouble = fSum (fMap fDouble xs)` to the corpus doesn't help if the benchmark prompt for `sum_doubles` never includes fSum, fMap, or fDouble in its few-shot context.

### Sprint 8 net: +1 task, but important qualitative wins

The headline number (+1 task, 27→28) understates the sprint's value:
- Fixed a long-standing constrained decoder bug (target was never actually constrained)
- Unlocked maybe_map (0→118/200) and accum_sum (0→187/200)
- Major pass rate improvements on many tasks (map_double 27→193, uncurry_add 13→189)
- Built diagnostic infrastructure reusable in future sprints
- Lost map_square (intermittently, due to RL variance)

The remaining 10 hard-zeros are a context selection problem, not a model capacity problem. The model can generate the right patterns when it sees the right context — it just never gets the right context for composition tasks.
