# Lessons Learned — Sprint 9

Running log of non-obvious findings, gotchas, and decisions made during development.

---

## Phase 1: Coverage Audit (2026-03-06)

### Composition bias alone is insufficient — verify before building

The initial sprint 9 plan assumed compositions were drowned out by primitives in type groups. The audit showed `[Int] -> Int` has 84% compositions (26/31 peers). Pattern-matched peers already appear in 42-66% of runs for this group. Yet all tasks are 0/200.

**Rule**: Always audit the actual data before committing to a fix. The intuitive theory ("model doesn't see compositions") was wrong for the largest task group. Building composition-biased selection without this audit would have been wasted effort.

### The real bottleneck: pattern transfer, not pattern exposure

7/10 hard-zero tasks have exact pattern matches in the corpus (e.g., fEvenCount for count_even, fSumDouble for sum_doubles). The model trains on these and sees them in context. It still generates 0 correct outputs.

The model has learned to use context for **style** (composition-shaped output) but not for **pattern transfer** (copy the structural pattern and substitute operands).

**Rule**: "Model sees X in context" and "model can use X from context" are independent. Training determines what the model learns to do with context. If context selection is random during training, the model learns to extract general style, not specific patterns.

### Pattern overlap is a better signal than composition detection

Simple composition detection (>=2 GlobalRefs) classifies 26/31 `[Int] -> Int` peers as compositions. But most of these compositions have the wrong pattern — `fSum ∘ fMap` instead of `fLength ∘ fFilter`. Scoring by building-block overlap with `few_shot_funcs` is a more targeted signal.

**Rule**: For context selection, "is this a composition?" is too coarse. "Does this composition share building blocks with my target?" is the right question.

---

## Phase 2-4: Pattern-Aligned Training (2026-03-06)

### Pattern-aligned training produces real but marginal gains

The training change confirmed the hypothesis: composition tasks that were absolute zeros now occasionally produce correct outputs. `drop_negatives` went from 0/200 to 12-33/200 across runs. `max_abs` went from 0/200 to 1-21/200.

But pass rates are 1-15%, meaning k=200 sometimes misses entirely. The model learned *some* pattern transfer but not enough for reliable generation. This is likely a model capacity limitation (485K params).

### Pattern bias must be restricted to compositional tasks (T2+)

Applying pattern-biased inference selection to T1 tasks causes significant T1 regression (17-19/22 vs 21-22/22). The `few_shot_funcs` field means different things for different tiers:
- **T1**: "contextually related functions" (fSucc, fSquare for fDouble)
- **T2/T3**: "building blocks needed for composition" (fLength, fFilter, fIsPositive for count_positive)

Pattern bias on T1 selects peers that reference the T1 task's "related" functions in their body — which are typically compositions that use those functions, NOT the simple peers the model expects.

**Rule**: When the same field serves different semantic purposes across task tiers, gate the bias on tier, not just field presence.

---

## Phase 5: RL Incompatibility (2026-03-06)

### Zero-shot RL destroys context-dependent models

The RL rejection sampling generates candidates zero-shot (no few-shot context), collects winners, and fine-tunes. This worked in Sprint 8 because that model was trained with random context — it was already robust to both zero-shot and few-shot.

Sprint 9's pattern-aligned training made the model MORE context-dependent. It learned to extract composition patterns from context. When RL fine-tunes on zero-shot winners, it teaches the model to produce outputs *without* context, degrading its context-handling. T1 dropped from 20/22 to 11/22 after RL.

**Rule**: RL's generation distribution must match the evaluation distribution. If the model relies on few-shot context, RL must also use few-shot context when collecting winners.

### Few-shot RL is too noisy for small models

Adding few-shot context to RL generation fixes the distribution mismatch but introduces massive variance: each generation gets random context, and winners are conditioned on specific context draws. Fine-tuning on these context-conditioned winners doesn't generalize to other contexts.

Results degraded from 0.737 → 0.553 → 0.526 mean pass rate across 3 RL rounds with context. The noise from context randomization overwhelmed the RL signal.

**Rule**: For RL to work with few-shot models, either (1) fix the context per task (deterministic) or (2) include the specific context in the training sequence. Neither is trivial for the current architecture.

### Non-determinism dominates all benchmark comparisons

T1 results vary from 17/22 to 22/22 across identical runs with the same checkpoint. T2 varies from 2/10 to 5/10. Sprint 8's headline results (22/22 T1, 28/38 total) may have been favorable draws.

**Rule**: Always run benchmarks 3+ times and report the range. A single run is not reliable for pass@k with random context selection. Consider fixing context seeds for reproducible comparisons.

---

## Phase 6: Targeted Additions (2026-03-06)

### Targeted data unblocks zero-peer tasks immediately

`apply_twice` had 0 type-matched peers — the model had no context examples for `(Int -> Int) -> Int -> Int`. Adding 3 concrete peers (fApplyAndSucc, fApplyToDouble, fApplyAndNegate) enabled 2/200 correct generations. `maybe_with_default` had 1 peer and went from 0/200 to 20-21/200 with 3 additional peers.

**Rule**: For tasks with 0-1 type-matched peers, the cheapest intervention is adding 2-3 peers to the corpus. The model needs at least 2-3 peers for the pattern-aligned training to create useful context. 1 peer means 0 context candidates (can't use yourself as context).

### maybe_with_default benefited more than apply_twice

maybe_with_default (20/200) improved far more than apply_twice (2/200) despite both getting 3 new peers. Key difference: maybe_with_default's new peers include one with the EXACT pattern (`fExtractDefaults d xs = fMap (\m -> fFromMaybe d m) xs`) plus two that contain the core `fMap (fFromMaybe d)` sub-pattern. apply_twice's peers show `f(g(x))` patterns but none show `f(f(x))` specifically — the model must generalize from "apply then transform" to "apply same function twice."

**Rule**: Peers that demonstrate the exact target pattern are far more effective than peers that demonstrate structurally adjacent patterns. The model transfers better from `fMap (fFromMaybe d)` to `fMap (fFromMaybe d)` than from `fSucc (f x)` to `f (f x)`.

### Retraining variance is a confound for targeted additions

Phase 4's checkpoint had drop_negatives at 12-33/200. Phase 6's retrained checkpoint (9 more functions, same code) shows 0/200 for drop_negatives. The 9 new functions (~0.2% of corpus) cannot explain this swing. It's random seed variance.

**Rule**: To properly evaluate targeted additions, use the SAME checkpoint where possible (add to inference context without retraining) or retrain with a fixed seed. Adding data AND changing the random seed conflates two variables.
