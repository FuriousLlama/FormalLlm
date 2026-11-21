# Sprint 13 — Lessons Learned

## Phase 1: Expression Enumeration

### `noncomputable` blocks `#eval`
Marking a def as `noncomputable` means Lean has no executable code for it, so `#eval` fails with "unknown declaration '_eval'". All synthesis expressions (filter, map, foldl, etc.) ARE computable — only abstract Mathlib defs need `noncomputable`. Remove it for any def you want to `#eval`.

### Batch validation is 10x faster than individual
Putting 80 expressions in a single `.lean` file takes ~15-20s. Validating 80 individually would take ~150-200s (Lean imports Mathlib each time). The tradeoff: one bad expression fails the whole batch, requiring a fallback to individual validation. In practice, 100% of our enumerated expressions compiled — the grammar is well-typed by construction.

### Output dedup is aggressive — use more test inputs
With 3 test inputs, 278/577 (48%) were false duplicates. With 5 test inputs, dedup drops to 397/1037 (38%) — still significant, but acceptable because many expressions genuinely compute the same thing (e.g., `xs.reverse.filter p` and `(xs.filter p).reverse` produce the same result if the predicate doesn't depend on position). This is a feature, not a bug: we want diverse *behaviors*, not diverse *syntax*.

### Absolute paths required for `lake env lean`
`subprocess.run(["lake", "env", "lean", str(file)], cwd=project_dir)` needs `file` to be absolute or relative to CWD (not relative to `project_dir`). Using `Path.resolve()` on the project path before constructing file paths fixes this.

### Candidate count vs unique count: plan for 2x overhead
To get N unique expressions, enumerate ~2N candidates. The computable × non-degenerate × non-duplicate pipeline keeps ~50-60% of candidates. Budget accordingly.

## Phase 2: Task Generation + Expanded Policy

### DirectDojo REPL crashes with 500+ tactics per expansion
FixedPolicy (56 tactics) runs fine on hard tasks via DirectDojo. ExpandedFixedPolicy (595 tactics) crashes the REPL with `EOFError` — too many round-trips per `expand()` call. The REPL process dies partway through. Fix: cap `k` in `propose_tactics()` to ~100-200 during MCTS, or batch tactic attempts. Type filtering already helps (Nat goals → 51 tactics), but List Int goals still have 441.

### Type-filtered expansion is essential at scale
Without type filtering, every expansion tries 595 tactics including type-mismatched ones that Lean immediately rejects. Parsing the goal type from the state string (`⊢ Nat`, `⊢ List Int`) and pre-filtering reduces expansion cost dramatically. E.g., Nat goals only need 51 tactics instead of 595.

### SynthTasks.lean must be `by sorry` not `:= sorry`
Using `by sorry` creates a tactic-mode goal that MCTS can fill with `exact <expr>`. Using `:= sorry` would create a term-mode hole that needs different handling. Match the pattern from Tasks.lean.

## Phase 3: Self-Play Data Collection

### Oracle requires absolute paths
`oracle.verify()` builds a temp file at `lean_project / "FormalNav" / "_Eval_*.lean"` and passes `str(eval_file)` to `lake env lean` with `cwd=str(lean_project)`. If `lean_project` is relative (e.g., `Path('env/FormalNav')`), the subprocess can't resolve the file path. Always use `lean_project.resolve()` or construct via `Path(os.getcwd()) / 'env' / 'FormalNav'`. The benchmark runner gets this right because it uses `Path(__file__).parent.parent / ...` which is already absolute.

### Direct collection beats MCTS for known-solution tasks
All 568 enumerated expressions have verified solutions. Running MCTS on them just rediscovers the known answer (rollout 1). Direct collection from expressions.jsonl takes 0 seconds; MCTS takes 10+ minutes per task. Use MCTS only for tasks where the solution is unknown.

### Variable name augmentation is cheap and effective
Replacing `xs` with `l`, `lst`, `input`, `arr`, etc. in (state, tactic) pairs triples the dataset with zero Lean validation cost. This teaches the LoRA policy that variable names are arbitrary — critical because MCTS states always use the def's parameter name.

## Phase 4: Synthesis Policy Training

### LoRA converges in 2 epochs on small domain data
With only 1827 training examples (vs 460K in Sprint 12), loss drops from 0.75 → 0.28 in 2 epochs and barely improves after. The model learns the synthesis domain extremely fast — this is a 568-expression search space, not open-ended theorem proving. Early stopping at epoch 8 (patience 2) is fine.

### Pure LoRA policy needs k >> 32 for decent coverage
With k=32 samples from the LoRA model, only 1/10 T2 tasks solved. The model generates the right *kind* of expressions (filter, map, etc.) but with 568 possible answers, 32 samples covers only 5.6% of the space. Tree size 1 (zero valid tactics) on 4/10 tasks means the model can't even produce type-correct expressions for some goal types at low k.

### Hybrid policy = LoRA + Fixed fallback is the right pattern
`SynthesisPolicy` combines LoRA-generated candidates (64, with PUCT log-probs) and FixedPolicy tactics (56, with low uniform priors). This gives 10/10 T2 (matching FixedPolicy). The LoRA candidates will matter for tasks *outside* the fixed set — self-play iteration should reveal this. The pattern is: use the learned model for exploration/novelty, keep hard-coded tactics for guaranteed coverage.

### PCFG is a surprisingly strong standalone policy
PCFGPolicy with k=200 random grammar samples gets 4-5/10 T2 with zero training and zero GPU. It reliably solves pipeline tasks (sort, filter, drop, nub) where the grammar can compose the right primitives. It consistently fails on fold/accumulator tasks (count_positive, sum_doubles, max_abs, count_even, accum_sum) — these require specific `foldl` + predicate/transform compositions that random sampling rarely hits in 200 tries. The grammar has `foldl` productions but combining them with the right binop + init + filter is a 1-in-~40 chance per sample.

## Phase 5: Evaluation

### T2 tasks are single-step — MCTS depth doesn't help
All 10/10 solved tasks across all configs hit on rollout 1 (tree size < 25). T2 tasks have single-step `exact <expr>` solutions. MCTS's multi-step search capability is irrelevant here. Need harder tasks (multi-step proofs, T3+) to measure whether MCTS + learned policy actually beats brute-force enumeration.

### SynthesisPolicy matches but doesn't exceed FixedPolicy on T2
10/10 = 10/10. The real value of SynthesisPolicy is generating *novel* expressions not in the fixed set. Phase 6 self-play on generated tasks (200 diverse functions) will test this. T2 is too easy to differentiate policies.

## Phase 6: Self-Play Iteration

### DirectDojo REPL crashes on large Lean files (SynthTasks.lean)
SynthTasks.lean has 200 defs with `sorry`. DirectDojo copies the entire file, adds `import Lean4Repl`, replaces one `by sorry` with `lean_dojo_repl`, then compiles the whole thing. With 200 sorry defs + Mathlib imports, the REPL either times out or crashes with "REPL never sent JSON response" for ~60% of tasks. Tasks with `List Int` or `Int` return types occasionally work; `Nat`, `List Nat`, `Option Int` tasks consistently fail. Fix: use batch oracle verification (`#eval`) instead of REPL for generated task evaluation.

### Batch oracle verification is O(tasks × exprs × tests) in Lean compilations
Each `lake env lean` call imports Mathlib (~2s overhead) regardless of file size. Testing 568 expressions × 200 tasks × 10 test cases requires ~1400 batch compilations at ~5s each = ~2 hours. The dominant cost is the Mathlib import, not expression evaluation. Potential optimization: keep a persistent Lean process to avoid repeated imports.

### PCFG candidates are type-unaware — pre-filter by return type before batch verification
PCFG generates `List Int`, `Nat`, `Int` etc. expressions uniformly. When a batch contains type-mismatched candidates (e.g. `xs.filter pred` tested against `⊢ Nat`), the entire batch fails to compile and all candidates are marked as failed. Pre-filtering candidates by return type before batch verification eliminates this waste.

### LoRA sampling is O(tasks) in GPU calls — batch by return type instead
`TrainedPolicy.propose_tactics()` with `output_scores=True` is expensive (~0.5s per call). For 200 tasks × 2 temperature sweeps = 400 generate() calls, that's ~200s just for sampling. Since all tasks share `xs : List Int` input and only differ by return type (5 types), sampling once per type (10 calls total) gives the same candidates 20x faster.

### Parallel Lean verification: 10x speedup with ProcessPoolExecutor
Sequential `lake env lean` calls take ~5s each (dominated by Mathlib import). With 1419 batches, that's ~2h. Using 28 parallel workers (90% of 32 cores) reduces to 35 min. Each worker creates a unique temp file (`_EvalGen_*.lean`) to avoid collisions. The Lean import cache is shared read-only, so parallel reads are safe.

### Generated tasks have exactly 1 solution each — no cross-task sharing
Every generated task is solved by exactly its reference expression and nothing else from the 568-expression pool. The 10 test cases per task are discriminating enough that no other expression produces identical outputs. This means self-play on generated tasks can't discover new training data — the only solution is already known.

### Self-play needs HARDER tasks to be useful
The 200 generated tasks are 1-2 step compositions already covered by the enumeration. Self-play iteration only adds value when the search space exceeds the known expressions — e.g., 3-step compositions, tasks requiring novel predicate/transform combinations, or multi-step proofs. Level 2 enumeration (deferred earlier) would create tasks that require discovery.
