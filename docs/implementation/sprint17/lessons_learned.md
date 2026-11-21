# Sprint 17 Lessons Learned

## Oracle Diagnostics Leak (Critical)

Lean's `Note:` linter messages (capital N) leaked into stdout and broke oracle output parsing. The `_DIAG_RE` regex only matched lowercase `note:`. This caused dedup_count to appear regressed when it was actually an oracle parsing bug.

**Fix**: Made `_DIAG_RE` case-insensitive for `note` and `warning`, added `set_option linter.unusedTactic false` to `LEAN_IMPORTS`.

**Lesson**: Any time Lean adds a new linter rule, it can break the oracle silently. Oracle output parsing should have a length-mismatch check: if `#eval` count doesn't match expected output lines, flag it as an oracle error rather than silently misaligning outputs.

## Dual FormalNav.lean Files

The Lean project has `FormalNav.lean` at the project root (used by `lake build`) AND `FormalNav/FormalNav.lean` (an unused copy). I edited the wrong one — the inner copy — and `lake build` didn't notice the change. Cost 30+ minutes of debugging zombie Pantograph servers.

**Lesson**: Always verify which file `lake build` actually uses. Check `lakefile.toml` `lean_lib` configuration.

## Tuple-Fold Patterns Transfer Well

The model learned indexed accumulator and nested range patterns at rollout 1 (factorial, triangular_numbers, running_parts, powers_of_2). Simple patterns like `(List.range n).foldl (fun acc i => acc * (i + 1)) 1` transfer perfectly from training data.

Binary recurrences with destructuring (`fun (a, b) _ => (b, a + b)`) are harder. The model generates similar patterns but not the exact Fibonacci form. The destructuring + projection (`.1`, `.2`) appears too infrequently in 6823 training pairs to be reliable.

## Auto Candidate Count Works

Reducing GoalConditioned candidates from 64 → 32 for non-product types (no `×` in goal) didn't cause any regression. `split_apply` (which IS a product type and keeps k=64) even improved from 16→1 rollouts, suggesting the dedup change was the real driver.

## Pantograph Zombie Servers

The first Pantograph server consistently crashes (becomes a zombie process), then the benchmark runner retries successfully. This appears to be a timeout during initial Lean/Mathlib loading. The zombie pattern is benign but wastes 5-10 minutes per benchmark run.

**Mitigation**: Increase Pantograph server startup timeout, or pre-warm the server with a separate script.

## Benchmark Non-Determinism Remains

`dedup_count` was 4/5 in Sprint 16, now 3/5 in Sprint 17. This is likely sampling non-determinism rather than a true regression — the model sometimes generates `xs.count x` and sometimes doesn't. Multiple runs needed to confirm.

**Lesson**: For Hard tasks at 200 rollouts, always run 3+ times and report the range. A single run is not statistically reliable for tasks that depend on rare tactic generation.

## Self-Play Stabilizes Border Cases

Adding 8 self-play verified solutions (oracle-passing tactics from earlier benchmark runs) as hand-crafted references improved val_loss 0.2753 → 0.2739 and stabilized `dedup_count` (Hard 3/5 → 4/5). The self-play pairs act as high-quality anchor examples for tactics the model discovers but doesn't reliably reproduce.

**Lesson**: Self-play is most effective for border-case tasks (sometimes-solved) rather than never-solved tasks. It turns non-deterministic solves into reliable ones.

## Recurrence Tier Has High Variance

Recurrence tasks show the most non-determinism of any tier. Across 2 runs post-self-play:
- factorial, running_parts: always solved (rollout 1)
- triangular_numbers, powers_of_2: each solved in 1/2 runs
- fibonacci, fibList: never solved

The rollout-1 tasks are fully learned. The 50% tasks are borderline — the right tactic appears in the top-32 candidates about half the time. fibonacci/fibList require destructuring patterns the model hasn't learned reliably.

**Lesson**: Report recurrence results as a range (3-4/6) not a point estimate.
