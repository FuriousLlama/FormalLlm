# Sprint 12 — Lessons Learned

## LeanDojo Mathlib trace crashes with SIGSEGV at 99%

**Problem**: `trace()` on Mathlib4 (8137 files) builds successfully, then segfaults during AST parsing at 99% (8133/8137). The crash is a C-level stack overflow from deeply recursive `from_data` → `_parse_children` → `from_data` calls on Mathlib files with deeply nested syntax trees.

**Root cause**: Python's default recursion limit (1000) and OS default stack size (8MB) are insufficient for the deepest Mathlib ASTs. The crash happens in Ray workers where `ulimit -s` from the parent shell may not propagate.

**Fix**: In `extract_data.py`, before importing LeanDojo:

```python
sys.setrecursionlimit(50000)
resource.setrlimit(resource.RLIMIT_STACK, (64 * 1024 * 1024, resource.RLIM_INFINITY))
```

Also run with `ulimit -s unlimited` and fewer workers (`NUM_PROCS=8`) to reduce concurrent stack pressure.

**Key insight**: The Mathlib build (6659 modules → 8137 AST JSON files) is cached after first run. Only the parsing phase needs the stack fix. Retrying is cheap.

## LeanDojo `_build_dependency_graph` fails on missing AST files

**Problem**: After the instrumented Lean build completes (8134/8134 AST files), `_build_dependency_graph()` in `traced_data.py` crashes with `FileNotFoundError` because a dependency file (`Mathlib/Analysis/Normed/Ring/Basic.ast.json`) wasn't produced during the build. The instrumented build occasionally fails to generate AST data for a few files (3 out of 8137).

**Root cause**: Some Mathlib files fail to compile with AST instrumentation (possibly timeout, OOM in workers, or compiler edge cases). The dependency graph builder assumes all referenced dependencies have corresponding AST files.

**Fix**: Patch `_build_dependency_graph` in `.venv/.../lean_dojo/data_extraction/traced_data.py` to catch `FileNotFoundError` and skip missing dependencies with a warning:

```python
try:
    tf_dep = TracedFile.from_traced_file(root_dir, json_path, repo)
except FileNotFoundError:
    logger.warning(f"Skipping missing dependency: {json_path}")
    continue
```

**Key insight**: Missing 3 files out of 8137 has negligible impact on tactic extraction quality. The theorems in those files are simply not traced.

## NUM_PROCS controls LeanDojo parallelism

LeanDojo reads `NUM_PROCS` from environment (defaults to `cpu_count()`). Setting `NUM_PROCS=29` (90% of 32 cores) avoids pegging the machine. Set in Makefile with `export NUM_PROCS`.

## LeanDojo `trace()` SIGSEGV is non-deterministic — bypass with extract_from_cache.py

**Problem**: After the first successful `trace()` + AST build, re-running `extract_data.py` triggers a fresh `lake build` (LeanDojo cache miss due to dirname mismatch), which SIGSEGV'd at module 772/6659 on `Aesop.Search.Expansion`. The first build succeeded; the second did not.

**Root cause**: LeanDojo's cache key is `leanprover-community-mathlib4-{commit}` but cloned repos land in `repos/gitpython-mathlib4-{commit}`. The cache miss forces a rebuild, and `lake build` with AST instrumentation is non-deterministically crashy.

**Fix**: Created `env/extract_from_cache.py` which:

1. Checks for `.trace.xml` files (from a previous `save_to_disk()`) → fast `load_from_disk()` path
2. Falls back to `.ast.json` files → `from_traced_files()` + `save_to_disk()` (slower but still no rebuild)
3. Creates a symlink from expected cache location to actual location

**Results**: 460,689 tactic pairs extracted (4.6× the ≥100K target). Categories: other 130K, simp 78K, rewrite 71K, exact 44K, have 31K, cases 26K, refine 19K, apply 17K, intro 13K, ext 10K. Only 4,157 skipped (0.9%).

**Key insight**: The expensive part (instrumented `lake build`) only needs to succeed once. All subsequent extractions should use the cached AST/trace files directly. Never re-run `trace()` if you already have the files.

## TracedRepo API: `traced_files` is a field, not a method

`TracedRepo.traced_files` is a dataclass field (list of TracedFile), not `get_traced_files()`. But `TracedFile.get_traced_theorems()` and `TracedTheorem.get_traced_tactics()` ARE methods. Easy to confuse.

## Force-killed CUDA processes become GPU memory zombies

**Problem**: Training runs kept dying with SIGKILL (exit 137) after ~15 minutes. Looked like driver corruption, kernel OOM, or memory leak. VRAM monitoring showed 3.8G stable — no leak.

**Root cause**: Earlier OOM crashes left zombie Python processes holding 13-24GB of GPU memory. `nvidia-smi` showed them as compute processes but `ps` showed them sleeping or defunct. Each new training run competed with the zombie for the 32GB total, hitting OOM at unpredictable points.

**Fix**: Always check `nvidia-smi --query-compute-apps=pid,used_memory --format=csv` before training. Kill any stale processes with `kill -9 <pid>`. The kernel page fault errors (`NX-protected page`) were a red herring — they were caused by the CUDA driver trying to manage overlapping memory mappings.

**Key insight**: `kill -9` on a CUDA process doesn't always release GPU memory immediately. The zombie can hold VRAM for hours. Always verify with nvidia-smi after killing.

## Validation loss nan from truncated sequences

**Problem**: With max_length=256, long tactic states fill the entire sequence. The tactic portion gets truncated, all labels become -100, and HuggingFace's loss returns nan. A single nan batch poisons the entire validation average.

**Fix**: Skip nan batches in `evaluate()`, subsample validation to 500 batches (saves time on 87K val set), and save adapter after every epoch regardless of val improvement.

## Qwen 1.5B + LoRA training config (RTX 5090, 32GB)

- batch_size=8, grad_accum=4, max_length=256: uses 14.3G reserved (3.8G active). Stable.
- batch_size=16: uses 25G+ — works only if no other GPU processes. Fragile.
- batch_size=32: guaranteed OOM (Qwen vocab 151K × seq_len × batch → 4.6GB logits cast to fp32)
- 100K examples, 3 epochs: ~77 min. Loss: 2.36→1.95→1.70→1.56. Val: 1.94→1.86→1.83.

## Mathlib-trained policy does NOT transfer to program synthesis

**Problem**: TrainedPolicy (LoRA on Qwen 1.5B, 100K Mathlib tactic pairs) scores 0/10 on T2 tasks and 0/5 on hard tasks. The FixedPolicy (hand-crafted) scores 10/10 on T2.

**Root cause**: Domain mismatch. Mathlib training data consists of proof obligations (`simp`, `rewrite`, `apply`, `have`, `cases`, `induction`). The benchmark synthesis tasks need `exact <expression>` where the expression is a program (`xs.filter (fun x => ...)`, `xs.map (...)`). The model learned the tactic language of theorem proving, not the expression language of program synthesis.

**Evidence**:
- Top 20 generated tactics for `⊢ Nat` goal: `have`, `cases`, `refine`, `induction`, `dsimp` — zero `exact xs.filter/map/fold` expressions
- Only tactic that type-checks is degenerate: `exact xs.reverse.reverse.reverse...` (126 repetitions = identity function) or `let a := xs.length + 1 - 1 + 1...` (nonsensical arithmetic)
- Tree size: 2 on every task (root + 1 degenerate child) — MCTS can't explore because only 1 tactic passes type checking
- Mathlib tactic distribution: simp 17%, rewrite 15%, exact 10%, have 7%, cases 6% — the `exact` entries use lemma names, not synthesized expressions

**Key insight**: Training on proof tactics teaches a model to *close* goals, not to *construct* programs. Even though both use Lean's tactic mode, the search space is completely different:
- Proofs: combine lemmas and automation (`simp [lemma1, lemma2]`)
- Synthesis: compose library functions (`xs.filter pred |>.map f |>.foldl g 0`)

**Implication**: To train a useful synthesis policy, we need synthesis-specific training data — (tactic_state, synthesis_expression) pairs. Mathlib doesn't provide this. Options: (a) generate synthetic training data from the benchmark task types, (b) use execution-guided search to collect successful synthesis trajectories, (c) combine FixedPolicy's expression library with learned reranking.

## LeanDojo REPL output prefixed with "REPL> "

The REPL's JSON responses are prefixed with `REPL> `. DirectDojo must strip this prefix before JSON parsing. Regular LeanDojo Dojo handles this internally.

## DirectDojo: bypassing LeanDojo trace for untraced files

**Problem**: Adding TasksHard.lean to FormalNav changes the git commit, invalidating LeanDojo's trace cache. Retracing requires rebuilding all 2806 Mathlib modules with AST instrumentation (~30+ min, may SIGSEGV).

**Fix**: Created `DirectDojo` class in `dojo_wrapper.py` that bypasses LeanDojo's trace system:
1. Reads the source `.lean` file directly
2. Replaces `by sorry` with `by lean_dojo_repl; sorry` using regex
3. Adds `import Lean4Repl` at the top
4. Runs `lake env lean` on the modified file
5. Uses the existing traced repo's build cache and Lean4Repl.lean

`LeanEnv` pins traced files to the known-good commit hash, and falls back to DirectDojo for untraced files. This avoids ever needing to retrace.

**Caveat**: DirectDojo's REPL can crash (EOF) after many tactic attempts on complex tasks (observed during partitionPositive after ~100 oracle checks). This needs investigation — possibly the Lean process runs out of memory from accumulating tactic state.

## simulate() was missing env.step() call

**Problem**: `mcts.py:simulate()` referenced `result.solved` without calling `result = env.step(state, tactic)` first. The `env.step()` call was dropped during an earlier edit.

**Fix**: Added `result = env.step(state, tactic)` before the `if result.solved:` check. This bug meant simulation rollouts always failed immediately (NameError), but was masked because expand() with oracle found solutions in rollout 1 for FixedPolicy tasks.

## Training run logging must be self-contained

**Problem**: First training runs overwrote each other's output (same `train/policy_adapter/` dir). Run summaries initially lacked enough context — someone reviewing the JSON without access to the code or git history couldn't reconstruct what happened.

**Fix**: Each run gets a timestamped adapter directory (`train/adapter_YYYYMMDD_HHMMSS/`). Run summary JSON (`train/runs/<adapter_name>.json`) includes:
- All hyperparameters (lr, batch_size, grad_accum, lora_r/alpha, max_length, patience)
- Hardware info (GPU name, VRAM total)
- Training config (dtype, optimizer, weight_decay, trainable/total params)
- Per-epoch loss trajectory (train_loss, val_loss, elapsed, VRAM usage)
- Separate `started_at` / `completed_at` timestamps
- Failure classification (cuda_oom, nan_loss, cuda_error, exception) with truncated traceback
- "started" marker written before training begins — if process is SIGKILLed, the file remains with status "started"

**Key insight**: `torch.cuda.get_device_properties(0).total_memory` (not `total_mem`). Also, `_base_stats()` must not be called twice — the second call overwrites `started_at` with the current time. Pass the start timestamp explicitly.
