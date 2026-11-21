# Sprint 20: Lessons Learned

## What Worked

### 1. Empirical-first approach (Phase 0)
Running the diagnostic script *before* any code changes was crucial. We discovered:
- Sub-goal states use `case fst`/`case snd` prefixes (not predictable from docs)
- Unicode types `ℤ`/`ℕ` in goal states (not ASCII `Int`/`Nat`)
- Combined state format shows all remaining sub-goals at once

### 2. Constructor boosting via median log-prob
Inserting `constructor` at the median GoalConditioned log-prob (not min_lp - 2.0) ensured MCTS explores it within ~5 rollouts. This was the minimum viable change — no architecture changes needed.

### 3. Case prefix stripping for model inference
The model performs better when it sees the clean sub-goal state (without `case fst\n` prefix). The `_strip_case_prefix()` method extracts just the first sub-goal body. Training data includes both forms for robustness.

### 4. Training data quantity improvement
Going from 6929 → 8174 pairs (+18%) lowered val_loss from 0.2784 → 0.2669. The multistep-specific pairs (694 new, including case-prefixed variants) were key to making the model handle sub-goal states.

## What Didn't Work

### 1. 50 rollouts insufficient for multi-step
partition_and_count consistently needs 62-134 rollouts. The search tree at depth 3 means the branching factor compounds: ~20 tactics at depth 1 × ~20 at depth 2 × ~20 at depth 3 = 8000 possible paths. 50 rollouts can't cover this.

### 2. Some product tasks resist decomposition
`sumAndProduct` (Int × Int with foldl sum/product) and `splitEvenOdd` (List Int × List Int with even/odd filters) are not solved. The model proposes wrong sub-goal tactics — it doesn't reliably generate `xs.foldl (· * ·) 1` or `xs.filter (fun x => decide (x % 2 = 0))` in sub-goal context.

### 3. Concurrent daemon access
Running multiple benchmark processes against the same Pantograph daemon causes `readuntil()` coroutine conflicts. The daemon's Unix socket protocol is single-threaded per connection. Never run concurrent benchmarks.

## Non-Obvious Insights

### Sub-goal state is a combined string
After `constructor`, Pantograph returns ALL remaining sub-goals in a single string:
```
case fst\nxs : List ℤ\n⊢ ℕ\ncase snd\nxs : List ℤ\n⊢ ℕ
```
After solving the first, it becomes just:
```
case snd\nxs : List ℤ\n⊢ ℕ
```
The policy needs to handle both formats.

### Direct tuple construction bypasses constructor
Tasks like `minAndMax` and `lengthAndSum` were solved with single `exact (a, b)` expressions — the model learned to construct tuples directly without `constructor`. This is actually better (1 rollout vs 62+). The multi-step path is a fallback for when no single expression covers both components.

### Depth distribution reveals search behavior
Adding `depth_distribution` to search results made it clear that `sum_and_product` was being deeply explored (depth 4, 977 nodes) but with wrong tactics. This diagnostic was essential for understanding failure modes.
