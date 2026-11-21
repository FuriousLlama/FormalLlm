# Sprint 9 — Pattern-Aligned Context Selection

## Motivation

Sprint 8 diagnosed all 10 remaining hard-zero tasks as a context selection problem. Sprint 9 Phase 1 audit revealed the problem is more nuanced than expected:

### Phase 1 Audit Results

| Type Group                | Tasks                                               | Total peers | Pattern matches | P(pattern in context) per run |
| ------------------------- | --------------------------------------------------- | ----------- | --------------- | ----------------------------- |
| `[Int] -> Int`            | count_positive, count_even, sum_doubles, max_abs    | 31          | 5-9             | 42-66% (~84-131/200 runs)     |
| `[Int] -> [Int]`          | sort_desc, take_positives, nub_sort, drop_negatives | 41          | 1-2             | 7-14% (~15-29/200 runs)       |
| `(Int->Int)->Int->Int`    | apply_twice                                         | 0           | 0               | 0% (type mismatch)            |
| `Int->[Maybe Int]->[Int]` | maybe_with_default                                  | 1           | 1               | 100%                          |

**Critical finding**: The `[Int] -> Int` group already has 84% compositions (26/31 peers). Pattern-matched peers appear in 84-131 of 200 runs. Yet all tasks are 0/200. **The model already sees the right patterns but can't use them.**

This rules out naive composition-biased selection as the primary fix. The problem has two layers:

1. **Inference-side (Group B only)**: `[Int] -> [Int]` tasks have pattern matches in only 7-14% of runs. Boosting this helps.
2. **Training-side (all groups)**: The model hasn't learned to **transfer patterns from context**. It sees `fCountNegative = fLength (fFilter fIsNegative xs)` in context but doesn't generalize to `fLength (fFilter fIsPositive xs)`. This is the core bottleneck.

### Why Training Alignment Is the Key

During training, context selection is random within type groups. A composition function like `fCountNegative` might get:

- Run A: context = `[fSumAbs, fLength, fProduct]` — only fLength is pattern-relevant
- Run B: context = `[fCountOdd, fSumEven, fHead]` — fCountOdd is pattern-relevant

The model sees pattern-relevant context inconsistently. It can't learn "when context shows X pattern, reproduce X pattern with different operands" because the signal is noisy.

**Fix**: During training, when a sequence is a composition, always provide same-pattern compositions as context. This teaches the model: "context shows `fold ∘ filter ∘ pred` → generate `fold ∘ filter ∘ pred` with a different predicate."

## Hypothesis

**Primary**: Pattern-aligned training context (same structural pattern, not just same type) will teach the model to transfer composition patterns from context, unlocking >=5 of 10 hard-zero tasks.

**Mechanism**: Training on `fCountNegative` with context `[fCountOdd, fEvenCount]` (all `fLength ∘ fFilter ∘ pred`) teaches the model that same-pattern context → same-pattern output with substituted operands. At inference, `count_positive` gets `fCountNegative` in context and the model generates `fLength (fFilter fIsPositive xs)`.

**Decisive test**: Phase 2 (inference-only pattern bias, no retrain) gates Phase 3. If Phase 2 shows 0 improvement on all 10 tasks, we know the training side is the bottleneck and Phase 3 is critical. If Phase 2 shows 1-3 improvements, both sides contribute.

## Design

### Pattern Classification

Each composition is classified by its structural pattern — the specific combination of building-block function roles:

```python
PATTERN_GROUPS = {
    "fold_filter_pred":  {"fLength", "fFilter"},   # fLength ∘ fFilter ∘ pred
    "fold_map_f":        {"fSum", "fMap"},          # fSum/fProduct/fMax ∘ fMap ∘ f
    "two_pipeline":      set(),                     # f ∘ g (generic two-function)
    "takewhile_pred":    {"fTakeWhile"},
    "dropwhile_pred":    {"fDropWhile"},
    "higher_order":      set(),                     # f(f(x)) patterns
    "maybe_op":          {"fFromMaybe"},
}
```

But rather than hardcoding patterns, we use **building-block overlap** with the task's `few_shot_funcs` as a similarity signal:

```python
def pattern_score(peer_refs: set[str], task_needed: set[str]) -> int:
    """Score a peer by how many of the task's needed building blocks it uses."""
    return len(peer_refs & task_needed)
```

This leverages the curated `few_shot_funcs` field already in `tasks.json` without hardcoding pattern taxonomies.

### Pattern-Biased Selection Algorithm

```python
def select_context(peers_with_refs, task, n=3):
    needed = set(task.get("few_shot_funcs", []))
    if not needed or len(needed) < 2:
        # Non-compositional task: unchanged random selection
        return random.sample(peers_with_refs, min(n, len(peers_with_refs)))

    # Score peers by building-block overlap
    scored = [(name, toks, refs, len(refs & needed))
              for name, toks, refs in peers_with_refs]

    # Separate high-overlap (>=2) from rest
    high = [(n, t, r, s) for n, t, r, s in scored if s >= 2]
    rest = [(n, t, r, s) for n, t, r, s in scored if s < 2]

    # Take up to 2 high-overlap peers, fill rest randomly
    selected = random.sample(high, min(2, len(high))) if high else []
    n_fill = n - len(selected)
    if n_fill > 0 and rest:
        selected += random.sample(rest, min(n_fill, len(rest)))

    return selected
```

**Impact on selection probabilities**:

| Task                          | Current P(pattern) | After bias P(pattern) |
| ----------------------------- | ------------------ | --------------------- |
| count_positive (9/31 pattern) | 66%                | ~95%+                 |
| sort_desc (1/41 pattern)      | 7%                 | ~50%+                 |
| nub_sort (1/41 pattern)       | 7%                 | ~50%+                 |
| drop_negatives (1/41 pattern) | 7%                 | ~50%+                 |

### Training-Time Pattern Alignment

The critical change. In `ASTDataset.__getitem__()`:

```python
def _select_context(self, idx):
    type_key = self.seq_types[idx]
    all_peers = self.type_groups[type_key]
    my_refs = self.seq_refs[idx]

    if len(my_refs) >= 2:  # I'm a composition
        # Prefer peers with high overlap
        scored = [(p, len(self.seq_refs[p] & my_refs)) for p in all_peers if p != idx]
        high = [p for p, s in scored if s >= 2]
        if len(high) >= 2:
            return random.sample(high, min(2, len(high)))

    # Fallback: random type-matched (unchanged for primitives)
    eligible = [p for p in all_peers if p != idx]
    return random.sample(eligible, min(2, len(eligible)))
```

Key properties:

- **Primitives unchanged**: T1 tasks train with the same random type-matched context as before
- **Compositions get same-pattern peers**: When training on `fCountNegative`, context is `[fCountOdd, fEvenCount]` — same `fLength ∘ fFilter` pattern
- **Fallback to random**: If not enough same-pattern peers exist, falls back to random (avoids training on always the same pair)

## Phases

### Phase 1 — Coverage Audit [COMPLETE]

See audit results above. Key findings:

- `[Int] -> Int`: 84% compositions, pattern matches in 42-66% of runs → NOT a context selection problem
- `[Int] -> [Int]`: 73% compositions, but pattern matches only 7-14% → selection bias helps
- `apply_twice`: 0 peers → needs arity fallback + targeted data
- `maybe_with_default`: 1 peer (always selected), still 0/200 → pure training problem

### Phase 2 — Inference-Only Pattern Bias (no retrain)

Modify `build_few_shot_prompt()` in `run.py` to prefer peers with high building-block overlap.

Changes:

- Add `get_refs()` utility to extract GlobalRef set from token sequence
- Score peers by overlap with `task["few_shot_funcs"]`
- Select top-overlap peers first, fill remaining randomly
- Add arity-based fallback for `apply_twice` (0 type-matched peers)

Run benchmark on 10 hard-zero tasks (k=200, t=0.8, RL checkpoint).

**Expected outcome**: 0-2 tasks improve. Group B (`[Int] -> [Int]`) has the best chance since pattern exposure jumps from 7-14% to ~50%+. Group A (`[Int] -> Int`) unlikely to change since patterns are already visible.

**Gate**: Even if Phase 2 shows 0 improvement, proceed to Phase 3 — the audit shows training alignment is the core issue.

**Key files**: `sprint/benchmark/run.py`

### Phase 3 — Training-Time Pattern Alignment

The primary intervention. Modify `ASTDataset.__getitem__()` in `train.py`:

1. Pre-compute `seq_refs` for all sequences at dataset init
2. When selecting context for a composition (>=2 refs), prefer peers with >=2 overlap
3. Primitives: unchanged random selection

**Verify**: After implementing, log context selections for a few composition sequences to confirm same-pattern peers are being selected.

**Key files**: `sprint/model/train.py`

### Phase 4 — Retrain + Evaluate

```bash
make corpus && make train
make benchmark K=200 TIER=1
make benchmark K=200 TIER=2
make benchmark K=200 TIER=3
```

Run by tier to avoid k=200 segfault. Compare against Sprint 8 baseline.

**T1 regression check**: If T1 drops, the training change leaked into primitive context. Debug by checking that primitives still get random peers.

### Phase 5 — RL + Final Evaluation

```bash
make rl K=200
```

3-round rejection sampling. Final benchmark by tier.

### Phase 6 (conditional) — Targeted Additions

For tasks still at 0/200:

**apply_twice**: Add 2-3 concrete-typed higher-order application functions:

```haskell
fApplyTwiceSucc :: Int -> Int
fApplyTwiceSucc x = fSucc (fSucc x)

fApplyTwiceDouble :: Int -> Int
fApplyTwiceDouble x = fDouble (fDouble x)
```

**maybe_with_default**: Add 2-3 similar-pattern functions with different types to populate the type group.

**Any remaining `[Int] -> Int` or `[Int] -> [Int]` failures**: If pattern-aligned training + bias still yields 0/200, the issue is model capacity. Consider:

- Increasing model size (2L→4L, 128→256)
- Adding chain-of-thought tokens
- Explicit retrieval step before generation

## Success Criteria

| Metric              | Sprint 8 | Min bar     | Target  |
| ------------------- | -------- | ----------- | ------- |
| T1 (regression)     | 22/22    | >=21/22     | 22/22   |
| T2 (compose)        | 2/10     | **>=5/10**  | >=7/10  |
| T3 (structural)     | 4/6      | >=4/6       | >=5/6   |
| Total               | 28/38    | **>=31/38** | >=34/38 |
| Hard-zeros resolved | 0/10     | >=3/10      | >=6/10  |

## Risk Analysis

| Risk                                             | Likelihood                | Mitigation                                                                     |
| ------------------------------------------------ | ------------------------- | ------------------------------------------------------------------------------ |
| Pattern-biased selection alone insufficient      | High (confirmed by audit) | Phase 3 training alignment is the primary fix                                  |
| Training alignment causes T1 regression          | Low                       | Only compositions change; primitives keep random context                       |
| Not enough same-pattern peers for training       | Medium                    | Falls back to random when <2 peers; audit shows most groups have 5+            |
| Model learns to copy context verbatim            | Medium                    | Acceptable — right structure with wrong operand is progress; RL fixes operands |
| Benchmark non-determinism                        | Medium                    | Run hard-zeros 3x, average results                                             |
| apply_twice has 0 peers even with arity fallback | Medium                    | Phase 6 adds targeted data                                                     |

## Files to Modify

| File                              | Phase           | Change                                                                             |
| --------------------------------- | --------------- | ---------------------------------------------------------------------------------- |
| `sprint/benchmark/run.py`         | 2               | `get_refs()` helper; pattern-biased peer selection; arity fallback for apply_twice |
| `sprint/model/train.py`           | 3               | Pattern-aligned context selection in `ASTDataset.__getitem__()`                    |
| `sprint/extract/Compositions.hs`  | 6 (conditional) | Targeted functions for apply_twice, maybe_with_default                             |
| `sprint/extract/compositions.txt` | 6 (conditional) | New function names                                                                 |

**No changes**: `checker.py`, `constrained_decode.py`, `tokenizer.py`, `normalize.py`, `augment.py`, `pretty_print.py`, `tasks.json`, `Makefile`

## Appendix A: Phase 1 Audit Data

### Composition Coverage by Type Group

| Type                      | Total | Compositions | Comp % |
| ------------------------- | ----- | ------------ | ------ |
| `[Int] -> Int`            | 31    | 26           | 84%    |
| `[Int] -> [Int]`          | 41    | 30           | 73%    |
| `(Int->Int)->Int->Int`    | 0     | 0            | N/A    |
| `Int->[Maybe Int]->[Int]` | 1     | 1            | 100%   |

### Pattern-Match Counts (peers sharing >=2 building blocks with task)

| Task               | Pattern matches | Best match                         |
| ------------------ | --------------- | ---------------------------------- |
| count_positive     | 9               | fCountOddPositive (3/3 overlap)    |
| count_even         | 7               | fEvenCount (3/3 — exact match!)    |
| sum_doubles        | 8               | fSumDouble (3/3 — exact match!)    |
| max_abs            | 5               | fSumAbs, fMinAbs (2/3 overlap)     |
| sort_desc          | 1               | fReverseSort (2/2 — exact match!)  |
| take_positives     | 2               | fTakeWhilePos (2/2 — exact match!) |
| nub_sort           | 1               | fSortNub (2/2 — exact match!)      |
| drop_negatives     | 1               | fDropWhileNeg (2/2 — exact match!) |
| apply_twice        | 0               | none (0 type-matched peers)        |
| maybe_with_default | 1               | fDefaultList (2/2 — exact match!)  |

### Key Insight

7/10 hard-zero tasks have an **exact pattern match** in the corpus (a function with the identical structural pattern). The model has seen these during training. It gets them in context 7-66% of the time. Yet it generates 0 correct outputs. **The bottleneck is not exposure — it's the model's ability to transfer patterns from context to output.** Training alignment is the primary intervention.

## Appendix B: Why Previous Context Approaches Failed (Sprint 8)

1. **Curated building blocks**: Model mimicked simple bodies instead of composing. Few-shot context is a style prior — simple context → simple output.
2. **Hybrid (curated + type-matched)**: More parse errors from mixed context distribution.
3. **Biased selection (boost curated 10x)**: T1 regression from unfamiliar distributions.

**Why pattern-aligned is different**: We're showing same-pattern compositions (e.g., `fCountOdd` for `count_positive`), not building blocks. The context remains type-matched and composition-shaped, but now the specific pattern matches what we need.
