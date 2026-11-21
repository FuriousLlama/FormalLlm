# Pantograph Integration Paths

Summary of three integration levels for replacing LeanDojo + DirectDojo with
Pantograph as the Lean tactic evaluation backend.

## Background

The current system uses LeanDojo (for traced files) and DirectDojo (for untraced
files) to interact with Lean 4. Both approaches have fundamental limitations:

- **LeanDojo** requires a traced repo pinned to a specific git commit. Adding a
  new `.lean` file requires re-tracing (~30 min).
- **DirectDojo** bypasses tracing by injecting `import Lean4Repl` and
  `lean_dojo_repl` into a temp file, then parsing JSON from stdout. This crashes
  under load (EOFError), can't recover, and processes tactics sequentially.
- **State is process-bound**: tactic states exist only inside a running Lean
  subprocess, addressed by opaque `sid` integers. No forking, no serialization,
  no recovery on crash.

[Pantograph](https://github.com/leanprover/Pantograph) is a machine-to-machine
interface for Lean 4, written entirely in Lean. It provides clean state
management, proper error handling, and is already used for ML proof search
(Stanford, CMU, etc.).

## Path 1: Pantograph Subprocess (PyPantograph)

**Effort:** ~2-3 hours. Replace `dojo_wrapper.py` with PyPantograph calls.

**How it works:**
- `pip install` PyPantograph (or `uv add git+...`)
- PyPantograph spawns the `pantograph-repl` binary via `asyncio.create_subprocess_exec`
- Communication: JSON commands over stdin/stdout (like DirectDojo, but clean protocol)
- Server emits `ready.` on startup, then accepts `goal.start`, `goal.tactic`, etc.

**API:**
```python
server = Server(imports=["FormalNav"], project_path="./env/FormalNav")
state = server.goal_start("forall xs : List Int, List Int")
try:
    new_state = server.goal_tactic(state, "exact xs.filter (· > 0)")
    # new_state.goals == [] means solved
except TacticFailure:
    # Tactic rejected by Lean — not a crash, just an error
    pass
```

**What it gives us:**
- No git commit pinning, no traced repos, no AST extraction
- No `Lean4Repl` injection or temp file hacks
- Clean error types (`TacticFailure`, `ParseError`, `ServerError`)
- `server.restart()` for crash recovery (respawns the process)
- `goal_save(state, path)` / `goal_load(path)` for state serialization
- `goal_continue(target, branch)` for proof tree branching
- `gc()` for explicit state cleanup (prevents memory bloat)
- Async API (`goal_tactic_async`) for overlapping I/O with GPU inference
- Built-in `MCTSAgent` with UCB selection as reference

**What it doesn't give us:**
- Still ~1-5ms per tactic (subprocess JSON round-trip)
- Still sequential (one tactic evaluated at a time per server)
- 595 tactics = ~1-3 seconds per MCTS expansion

**Latency budget (595 tactics, single expansion):**
```
Current DirectDojo:   595 * 2-10ms = 1.2-6.0s (+ crash risk)
PyPantograph:         595 * 1-5ms  = 0.6-3.0s (no crash risk)
```

**Risk:** Low. PyPantograph is actively maintained, used by multiple research
groups. Worst case: version mismatch with our Lean/Mathlib, fixable.

---

## Path 2: Pantograph C FFI

**Effort:** ~1-2 days. Build Pantograph as shared library, write ctypes wrapper.

**How it works:**
- Pantograph compiles to a shared library (`libPantograph.so`) via Lake
- `Pantograph/Library.lean` exports functions with `@[export]`:
  - `pantograph_init_search` — set up Lean search paths
  - `pantograph_create_core_context` — create elaboration context
  - `pantograph_create_core_state` — import modules (Mathlib, FormalNav)
  - `pantograph_goal_start_expr_m` — create goal from type expression
  - `pantograph_goal_state_try_tactic_m` — **evaluate a tactic** (the hot path)
  - `pantograph_goal_serialize_m` — inspect goal state
  - `pantograph_goal_state_goals` — get current goals
- Python loads the `.so` via ctypes and calls these functions directly

**ABI:**
- All Lean objects are `lean_object*` (C pointers) with reference counting
- Strings: `lean_mk_string("tactic") → lean_object*`
- Results: `lean_object*` containing the `TacticResult` discriminated union
- Must call `lean_inc(obj)` / `lean_dec(obj)` for memory management
- Boxed fields first, then unboxed scalars sorted by size

**What it gives us (beyond Path 1):**
- ~50-100us per tactic (direct function call, no JSON, no pipes)
- 595 tactics = ~30-60ms per expansion (20-50x faster than subprocess)
- Environment held in-process (no subprocess lifecycle management)
- State is a Lean value in memory (fork = increment refcount)
- GIL released during C call → real parallelism with GPU thread

**What it doesn't give us:**
- Still sequential (one tactic per call)
- Crash in Lean elaborator = crash in Python process (no isolation)
- Must manage `lean_inc`/`lean_dec` correctly (memory leaks / use-after-free)

**Latency budget (595 tactics, single expansion):**
```
PyPantograph:         595 * 1-5ms    = 0.6-3.0s
C FFI (sequential):   595 * 50-100us = 30-60ms
```

**Risk:** Medium. Lean's C ABI is marked unstable. Refcounting bugs cause
silent memory corruption. Need thorough testing. Build process for the shared
library may require Nix or custom lakefile configuration.

---

## Path 3: Batch Parallel Evaluation (Lean-native)

**Effort:** ~3-5 days. Write a Lean function for parallel batch tactic eval,
export via FFI.

**How it works:**
- Write `batchTryTactics` in Lean:
  ```lean
  @[export pantograph_batch_try_tactics_m]
  def batchTryTactics (state : GoalState) (goal : MVarId)
      (tactics : Array String) : IO (Array TacticResult) := do
    let tasks ← tactics.mapM fun tactic =>
      IO.asTask (prio := .default) do
        -- Fork state (cheap: persistent data structures)
        -- Parse and evaluate tactic
        -- Return result
    tasks.mapM IO.ofTask
  ```
- Lean's `IO.asTask` spawns green threads on the Lean runtime's thread pool
- Each tactic evaluation gets a forked `MetavarContext` (O(1) copy via
  persistent maps) and runs independently
- Results collected as an `Array TacticResult`
- Exported via `@[export]`, called from Python ctypes

**What it gives us (beyond Path 2):**
- 595 tactics across 16 cores = ~37 sequential evaluations = ~2-4ms
- True parallelism inside Lean's runtime (no GIL, no Python threads)
- GPU/CPU fully pipelined: GPU generates next batch while CPU evaluates current

**Latency budget (595 tactics, single expansion):**
```
C FFI (sequential):   595 * 50-100us          = 30-60ms
Batch parallel (16c): ceil(595/16) * 50-100us = 2-4ms
```

**What it doesn't give us:**
- Still single-server (one environment). Multi-task parallelism needs multiple
  environment loads or careful state isolation.

**Risk:** High. Lean's tactic elaboration (`TacticM`) uses mutable `IO.Ref`
cells inside the monad stack. Running multiple `TacticM` computations on
forked states concurrently may hit data races if any refs are shared.
Needs careful investigation of which `MetaM`/`CoreM` state is truly
fork-safe. May require upstream Lean changes or a `Mutex`-guarded approach.

**Unknown:** Whether `IO.asTask` + forked `SavedState` actually works without
races. This needs a proof-of-concept in pure Lean before committing to the
approach.

---

## Comparison Matrix

| Property | DirectDojo (current) | Path 1: Subprocess | Path 2: C FFI | Path 3: Batch |
|---|---|---|---|---|
| Setup effort | Already done | ~2-3h | ~1-2 days | ~3-5 days |
| Per-tactic latency | 2-10ms | 1-5ms | 50-100us | 50-100us |
| 595 tactics | 1.2-6.0s | 0.6-3.0s | 30-60ms | 2-4ms |
| Parallelism | None | Async I/O | GIL-free C calls | Lean green threads |
| Crash recovery | None (task fails) | `restart()` | Process dies | Process dies |
| State management | Opaque sid in process | IDs + save/load | lean_object* refs | lean_object* refs |
| Git commit pinning | Required | No | No | No |
| New file support | Temp file injection | Just import module | Just import module | Just import module |
| Risk | Known (crashes) | Low | Medium | High |
| Maintenance | LeanDojo version pins | PyPantograph updates | ABI stability | Lean runtime internals |

## Recommended Path

**Sprint 15: Path 1 (subprocess) as primary, Path 2 (FFI) as stretch goal.**

Path 1 eliminates all current blockers (git pinning, REPL crashes, no recovery)
with minimal effort and risk. The 1-5ms per-tactic overhead is acceptable —
the policy network dominates at ~100ms per inference.

Path 2 becomes worthwhile when we scale to larger search budgets (>1000 rollouts)
or need to evaluate generated tasks at scale (hundreds of tasks, each with 500+
rollout budget).

Path 3 is research-grade work that should wait until Paths 1-2 are proven and
the tactic evaluation is confirmed as the bottleneck (not the policy network).
