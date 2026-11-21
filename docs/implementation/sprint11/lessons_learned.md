# Sprint 11 — Lessons Learned

## Lean 4 / Arch Linux Setup

- **elan from upstream, not AUR**: The upstream curl installer is more reliable than AUR packages.
- **`lake exe cache get` is mandatory**: Without precompiled oleans, Mathlib builds from source
  (hours). With the cache, setup takes minutes.
- **Pin lean-toolchain to Mathlib's version**: Mismatched toolchains cause subtle build failures.
  Currently pinned to v4.19.0.

## LeanDojo Integration

- **LeanDojo REPL cannot parse `·` (U+00B7) anonymous function syntax**: Tactics like
  `exact (xs.map (· * 2)).foldl (· + ·) 0` fail with parse errors when sent through LeanDojo's
  tactic REPL. The same tactic works fine in standalone `lean file.lean`. **Fix**: Always use
  explicit lambda syntax (`fun a b => a + b`) instead of `·` in tactics sent to LeanDojo.

- **`_eval` name conflicts in Lean**: Function names starting with `_` can clash with Lean
  internals. Use unique prefixed names (e.g., `oracle_countPositive`) for temp eval functions.

- **Lean warnings go to stdout**: `set_option linter.unusedVariables false` is needed in oracle
  eval files, otherwise linter warnings pollute #eval output parsing.

- **First LeanDojo trace is slow but cached**: Initial Dojo interaction requires tracing the
  Lean project (~30-60s for FormalNav). Subsequent calls within the same process reuse the cache.

## MCTS Design Decisions

- **Type checking ≠ semantic correctness**: This is the central lesson. Lean's kernel validates
  that an expression has the right TYPE, but `exact 0` is type-correct for any `⊢ Nat` goal.
  For program synthesis, you MUST verify behavior against test cases (the oracle).

- **Oracle must live inside MCTS, not just in the benchmark runner**: If the oracle only runs
  post-hoc, MCTS terminates at the first type-correct expression (always rollout 1) and never
  explores alternatives. The oracle must reject wrong solutions DURING search so MCTS keeps
  exploring.

- **Caching oracle results is essential**: Each oracle call takes ~2s (Lean subprocess startup).
  Without caching, the same wrong tactic gets re-evaluated in every rollout. Cache by tactic
  sequence tuple → O(1) lookup after first check.

## Policy Network

- **FixedPolicy validates infrastructure, not search capability**: With reference solutions
  baked into the tactic list, MCTS just enumerates and filters. The real generalization test
  requires a learned policy that discovers compositions it wasn't trained on.

- **Default k matters**: FixedPolicy had 56 tactics but default k=50, randomly excluding 6.
  If the correct solution was among the excluded 6, the task would fail non-deterministically.
  Changed default to k=None (return all tactics).

## Comparison with GHC Core Pipeline

- **Per-step verification is the key difference**: The GHC Core pipeline validates only at the
  end (full expression → type check → execution test). MCTS + Lean validates at EVERY step
  (each tactic is type-checked by the kernel). This prunes the search space exponentially.

- **The wiring problem is solved by search, not prediction**: Sprint 10 showed the autoregressive
  model can predict correct building blocks (30% accuracy) but can't wire them in a single pass.
  MCTS sidesteps this by exploring multiple wirings at each step.

- **Infrastructure comparison**: LeanDojo setup is heavier (elan + Mathlib + separate venv) but
  the interactive tactic interface is far more powerful than GHC Core dump parsing. Each tactic
  step gives immediate feedback (accepted/rejected/solved).
