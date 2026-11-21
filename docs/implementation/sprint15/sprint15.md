# Sprint 15 — Pantograph Migration + REPL Infrastructure Overhaul

## Motivation

Sprint 14 demonstrated that the model and expression space are capable — Cat B
5/5, Cat D 4/5, T2 10/10 — but **infrastructure is the blocker**, not policy
quality. The hard tasks are 0/5 not because the model can't solve them, but
because the DirectDojo REPL crashes before the model gets to propose solutions.

The current Lean interaction stack has four layers of accidental complexity:

1. **LeanDojo tracing** — requires a pinned git commit (`_TRACED_COMMIT`),
   30+ minute re-trace for any file change, AST extraction to `.ast.json`
2. **DirectDojo fallback** — injects `import Lean4Repl` into temp files,
   parses JSON from stdout with a 50-line skip loop, crashes under load
3. **State trapped in a process** — tactic states are opaque `sid` integers
   inside a running subprocess. No forking, no serialization, no recovery.
4. **Sequential evaluation** — 595 tactics = 595 round-trips over pipes,
   ~1-6 seconds per MCTS expansion

[Pantograph](https://github.com/leanprover/Pantograph) eliminates all four.
It's a machine-to-machine interface for Lean 4 built specifically for ML proof
search, maintained under the `leanprover` org, used by Stanford (PyPantograph)
and others. It provides:

- No git pinning — points at the project directory, uses `lake build` output
- Clean JSON protocol with proper error types (or C FFI for in-process calls)
- State management: `goal_save`/`goal_load`, `goal_continue` for branching
- Crash recovery via `server.restart()`
- Built-in `MCTSAgent` as reference implementation

**The fundamental question Sprint 15 must answer:**

> Does replacing DirectDojo with Pantograph unblock the hard tasks and enable
> reliable multi-step search?

### What Changed Since Sprint 14

Sprint 14 proved three things:
1. The expression space covers novel compositions (Cat B 5/5, Cat D 4/5)
2. SynthesisPolicy matches but doesn't exceed ExpandedFixed (LoRA = lookup table)
3. Hard tasks 0/5 is an infrastructure failure, not a model failure

Sprint 14 left four gaps:
1. REPL crashes block all hard task evaluation (EOFError on 4/5 tasks)
2. Multi-step search underutilized (depth 1 dominates, 20-50 rollouts insufficient)
3. Cat A (multi-type) fails due to variable name mismatch in policy
4. No parallel tactic evaluation (595 sequential round-trips per expansion)

Sprint 15 addresses gaps 1, 2, and 4 by replacing the Lean interaction layer.
Gap 3 (context-aware policy) is deferred to Sprint 16.

### Literature Grounding

- **Pantograph** (TACAS 2025): Machine-to-machine interface for Lean 4.
  Handles metavariable coupling, `have`/`let`/`conv`/`calc` tactics, state
  branching — exactly what our multi-step search needs.
- **PyPantograph** (Stanford): Python binding with async API, MCTS agent,
  state save/load. Direct replacement for our DirectDojo.
- **AlphaVerus** (ICML 2025): Tree search + verifier bootstrap. Their
  infrastructure uses Pantograph for Lean interaction, validating this path.
- **Kimina** (2025): Large-scale Lean proof search server. Uses Pantograph
  for programmatic tactic evaluation at scale.

## Hypothesis

**Primary**: Migrating to PyPantograph will unblock hard task evaluation
(currently 0/5 due to REPL crashes) and reveal that ExpandedFixed solves ≥3/5
hard tasks with the existing expression space.

**Secondary**: Pantograph's state management (`goal_continue`, `goal_save`)
will enable effective multi-step MCTS search at 200+ rollouts, solving ≥3/5
Cat C tasks that currently fail at depth > 1.

**Tertiary**: The C FFI path will reduce per-tactic latency enough to make
1000+ rollout budgets practical, enabling the value network to demonstrate
measurable search efficiency gains.

**Decisive test**: Re-run the Sprint 14 benchmark (T2 + Hard + V2) with
Pantograph as the backend, same policies. Any improvement over Sprint 14 is
purely infrastructure — same model, same expressions, better plumbing.

| Outcome | Interpretation |
|---|---|
| Hard ≥3/5, Cat C ≥3/5 | Infrastructure was the only blocker. Model ready for harder tasks |
| Hard ≥3/5, Cat C still low | REPL fixed, but multi-step needs more rollouts or value guidance |
| Hard still 0/5 | Problem is deeper than REPL — type pre-filtering or policy needs work |
| T2 regression | Pantograph integration has a bug. Debug before proceeding |

## Constraints

- Same hardware: RTX 5090 (32GB VRAM), no cloud
- Python 3.12 venv (check PyPantograph compatibility — may need 3.11 or 3.12)
- Must preserve Sprint 14 baselines: T2 10/10, V2 11/20 (ExpandedFixed)
- Total sprint wall time budget: ~2 days
- Pantograph requires elan + lake (already installed)
- PyPantograph requires building the Pantograph REPL binary for our Lean version

## Phases

### Phase 0 — Baseline Instrumentation (~2 hours)

**Goal**: Add per-step timing to the MCTS loop so we have concrete data on
where time is spent BEFORE making any changes. Without this, we can't verify
that Pantograph actually improved anything.

The existing benchmark data has `wall_time_s` per task but no breakdown. We
need to measure:

1. **REPL round-trip**: time in `env.step(state, tactic)` — the Lean type
   checker latency per tactic
2. **Policy inference**: time in `policy_fn(state)` — GPU inference for
   TrainedPolicy/SynthesisPolicy, or expression filtering for ExpandedFixed
3. **Oracle verification**: time in `oracle_fn(tactics)` — spawns `lake env
   lean` to run test cases
4. **MCTS overhead**: select + backpropagate (expected to be negligible)

#### Implementation

Add timing counters to `search/mcts.py`:

```python
@dataclass
class SearchStats:
    """Per-search timing breakdown."""
    n_expand_calls: int = 0
    n_tactic_steps: int = 0        # total env.step() calls
    n_oracle_calls: int = 0
    t_policy_s: float = 0.0        # cumulative policy inference time
    t_step_s: float = 0.0          # cumulative env.step() time
    t_oracle_s: float = 0.0        # cumulative oracle verification time
    t_simulate_s: float = 0.0      # cumulative simulation time
    t_select_s: float = 0.0        # cumulative select + backprop time
    n_repl_crashes: int = 0        # EOFError / OSError count
```

Instrument `expand()`:
- Time each `env.step()` call individually
- Time the `policy_fn()` call
- Time each `oracle_fn()` call
- Count total tactic attempts vs valid vs solved

Instrument `simulate()`:
- Time the full simulation (policy + step combined)

Return `SearchStats` alongside the existing result dict. Include in the
benchmark JSON output.

#### Baseline run

Run the existing DirectDojo stack with instrumentation on representative tasks:
```bash
# FixedPolicy on T2 (fast, ~56 tactics per expansion)
python benchmark/run.py --tier t2 --policy fixed --rollouts 50 --verbose

# ExpandedFixed on V2 (slow, ~595 tactics per expansion)
python benchmark/run.py --tier v2 --policy expanded-fixed --rollouts 50 --verbose

# SynthesisPolicy on T2 (GPU, ~120 tactics per expansion)
python benchmark/run.py --tier t2 --policy synthesis --rollouts 50 --verbose
```

Record per-task breakdown:
```
Task: count_positive (FixedPolicy, 50 rollouts)
  Policy inference:    0.02s (56 tactics, 0.4ms each)
  REPL step() calls:   2800 calls, 8.4s total (3.0ms/call)
  Oracle calls:        12 calls, 2.1s total (175ms/call)
  Simulation:          4.2s
  Select+backprop:     0.01s
  Total wall time:     14.7s
  Bottleneck:          REPL step (57%)
```

This gives us the concrete "before" numbers to compare against Pantograph.

**Verification**:
- `SearchStats` included in benchmark JSON output
- Baseline numbers documented for at least 3 task/policy combinations
- Clear identification of which component dominates wall time

### Phase 1 — PyPantograph Setup + Smoke Test (~3 hours)

**Goal**: Install PyPantograph, verify it works with our FormalNav project,
solve one known T2 task through the new stack.

#### Steps

1. **Install PyPantograph**:
   ```bash
   cd lean_mcts
   uv pip install git+https://github.com/stanford-centaur/PyPantograph
   ```
   If version mismatch with our Lean toolchain, build from source:
   ```bash
   git clone --recurse-submodules https://github.com/stanford-centaur/PyPantograph
   cd PyPantograph && uv build
   uv pip install dist/*.whl
   ```

2. **Verify Lean/Mathlib compatibility**: PyPantograph builds its own
   `pantograph-repl` binary. Must match our `lean-toolchain` version.
   Check `lean_mcts/env/FormalNav/lean-toolchain` and compare with
   PyPantograph's expected version. May need to pin PyPantograph to a
   compatible commit.

3. **Smoke test** — solve `synth_identity` through Pantograph:
   ```python
   from pantograph.server import Server
   server = Server(imports=["FormalNav"], project_path="env/FormalNav")
   state = server.goal_start("∀ n : Int, Int")
   result = server.goal_tactic(state, "intro n")
   result = server.goal_tactic(result, "exact n")
   # result.goals should be empty (solved)
   ```

4. **Test T2 tasks**: Pick 3 T2 tasks with known solutions, verify they
   solve through Pantograph. Compare tactic state format with DirectDojo
   output — policy functions parse the state string, so format differences
   will break type pre-filtering.

5. **Test hard task**: Pick the simplest hard task (e.g., `zip_sum`), verify
   Pantograph doesn't crash where DirectDojo does.

**Verification**:
- `pantograph-repl` binary builds and starts
- `Server.create(imports=["FormalNav"])` succeeds (loads Mathlib + our defs)
- At least one T2 task solves end-to-end
- State string format documented (may differ from LeanDojo's `TacticState.pp`)

### Phase 2 — Wrapper Replacement (~4 hours)

**Goal**: Write `env/pantograph_wrapper.py` that implements the same `LeanEnv`
interface as `dojo_wrapper.py`, backed by Pantograph. Update MCTS and benchmark
to use it.

#### 2a — PantographEnv class

```python
class PantographEnv:
    """Drop-in replacement for LeanEnv, backed by Pantograph."""

    def __init__(self, project_path: str, file_path: str, theorem_name: str):
        ...

    def reset(self) -> GoalState:
        """Start Pantograph server, create goal from theorem type."""
        ...

    def step(self, state, tactic: str) -> StepResult:
        """Call goal_tactic, map result to StepResult."""
        ...

    def close(self):
        """Close server."""
        ...
```

Key design decisions:
- **State format**: Pantograph returns `Goal` objects with `.target` (the type)
  and `.variables` (local context). Our policies parse the state as a string
  (`"xs : List Int\n⊢ List Int"`). Need an adapter that formats Pantograph's
  structured Goal into the same string format, OR update policies to work with
  structured state.
- **Theorem lookup**: Currently we pass `theorem_name` and DirectDojo finds
  `def <name> ... := by sorry` via regex. Pantograph doesn't need this — we
  pass the type expression directly to `goal_start`. Need a mapping from
  theorem name → type expression. Can extract from tasks.py or parse the .lean
  file.
- **Error mapping**: Map `TacticFailure` → `StepResult(error=...)`,
  `ServerError` → propagate, solved (no goals) → `StepResult(solved=True)`.
- **Restart on crash**: If `ServerError` during `step()`, call
  `server.restart()`, replay tactic path to parent node, continue. This is
  the key advantage over DirectDojo.

#### 2b — Oracle update

The oracle (`eval/oracle.py`) currently spawns `lake env lean` to verify
solutions against test cases. This is independent of the REPL — it compiles
a standalone file. **No changes needed** for Pantograph migration.

However, Pantograph's `check_track` function could eventually replace the
oracle for faster verification.

#### 2c — Benchmark integration

Update `benchmark/run.py`:
- Add `--backend pantograph` / `--backend dojo` flag (default: pantograph)
- `PantographEnv` replaces `LeanEnv` when `--backend pantograph`
- Keep `LeanEnv` as fallback for regression testing

Update `search/mcts.py`:
- The MCTS code is backend-agnostic (uses `env.step()` / `env.reset()`).
  No changes needed if `PantographEnv` implements the same interface.

#### 2d — Policy state format compatibility

The critical integration point: policies read the state string to decide
which tactics to propose. `ExpandedFixedPolicy.get_candidates()` parses
`"⊢ List Int"` from the state string to filter expressions by return type.

- Document Pantograph's state format (from Phase 1 smoke test)
- If format differs, write a formatter that produces LeanDojo-compatible
  strings from Pantograph `Goal` objects
- If format is richer (structured goals with variable types), consider
  upgrading policies to use structured data (stretch goal)

**Verification**:
- `PantographEnv` passes the same smoke tests as `LeanEnv`
- T2 benchmark: 10/10 with FixedPolicy (parity with DirectDojo)
- No import of `lean_dojo` anywhere in the Pantograph code path
- Restart recovery works: kill the server mid-search, verify it recovers

### Phase 3 — Hard Task + Multi-Step Evaluation (~3 hours)

**Goal**: Re-run the full Sprint 14 benchmark through Pantograph. The
hypothesis is that hard tasks will now be evaluable (no REPL crashes) and
multi-step search will be more reliable.

#### 3a — Hard tasks (5 tasks)

Run all 4 policy configs on hard tasks:
```bash
python benchmark/run.py --tier hard --policy expanded-fixed --backend pantograph --rollouts 200
python benchmark/run.py --tier hard --policy synthesis --backend pantograph --rollouts 200
```

With Pantograph's crash recovery, tasks that previously hit EOFError should
now either solve or fail cleanly (exhaust rollout budget). The key question:
do the expressions for these tasks exist in the enumerated space?

Expected results:
- `zip_sum`: ExpandedFixed should solve (zipWith in expression space)
- `flatten`: ExpandedFixed should solve (List.join in expression space)
- `partition_positive`: Needs `constructor` + two sub-goals (multi-step)
- `dedup_count`: Complex pipeline, may need higher rollout budget
- `filter_map_abs`: filterMap pattern, should be in expression space

#### 3b — V2 tasks (20 tasks)

Re-run V2 benchmark to verify parity and check improvements:
```bash
python benchmark/run.py --tier v2 --policy expanded-fixed --backend pantograph --rollouts 200
```

Sprint 14 result: 11/20. Pantograph should match or exceed this:
- Cat A (0/5): Still likely 0/5 — this is a policy problem, not REPL
- Cat B (5/5): Should remain 5/5
- Cat C (1-2/5): May improve with reliable multi-step + higher rollouts
- Cat D (4/5): Should remain 4/5

#### 3c — Multi-step deep search

For Cat C tasks specifically, test with higher rollout budgets:
```bash
python benchmark/run.py --task partition_and_count --rollouts 500 --backend pantograph --verbose
python benchmark/run.py --task synth_normalize --rollouts 500 --backend pantograph --verbose
```

With Pantograph's state branching (`goal_continue`), multi-step search should
be more effective. After `constructor` splits `⊢ A × B`, each sub-goal gets
its own branch. Pantograph tracks these cleanly, DirectDojo often crashed here.

**Verification**:
- Hard tasks: ≥3/5 solved with ExpandedFixed (primary hypothesis)
- V2: ≥11/20 (no regression from Sprint 14)
- T2: 10/10 (no regression)
- Cat C: ≥3/5 at 500 rollouts (secondary hypothesis)
- No REPL crashes (0 EOFError exceptions)

### Phase 4 — C FFI Exploration (Stretch, ~4 hours)

**Goal**: Build Pantograph as a shared library, write a minimal ctypes wrapper,
benchmark the per-tactic latency improvement.

This phase is a stretch goal. Skip if Phases 1-3 take longer than expected.

#### 4a — Build shared library

```bash
cd PyPantograph/Pantograph  # or wherever the Lean source is
lake build +sharedLib        # or: nix build .#sharedLib
```

Locate the `.so` file (likely in `.lake/build/lib/`). Verify it exports the
expected symbols:
```bash
nm -D libPantograph.so | grep pantograph_
```

Expected symbols:
- `pantograph_init_search`
- `pantograph_create_core_context`
- `pantograph_create_core_state`
- `pantograph_goal_start_expr_m`
- `pantograph_goal_state_try_tactic_m`
- `pantograph_goal_serialize_m`

#### 4b — Minimal ctypes wrapper

Write `env/pantograph_ffi.py` (~200 lines):
- Load the `.so` via `ctypes.CDLL`
- Wrap Lean string creation (`lean_mk_string`)
- Wrap the core functions with Python-friendly signatures
- Handle `lean_inc`/`lean_dec` via a ref-counting context manager

#### 4c — Benchmark

Compare per-tactic latency:
```python
# Measure 1000 tactic evaluations on the same state
state = ffi_env.reset(task)
for tactic in test_tactics:
    t0 = time.perf_counter_ns()
    result = ffi_env.step(state, tactic)
    elapsed_us = (time.perf_counter_ns() - t0) / 1000
    # Record elapsed_us
```

Expected: 50-100us per tactic (vs 1-5ms subprocess).

**Verification**:
- Shared library builds and loads in Python
- At least one task solves through the FFI path
- Per-tactic latency measured and documented
- Comparison table: DirectDojo vs subprocess vs FFI

### Phase 5 — Cleanup + Documentation (~1 hour)

**Goal**: Clean up, update sprint docs, update CLAUDE.md if needed.

- Remove LeanDojo dependency from the critical path (keep as optional)
- Update `requirements.txt`
- Document the Pantograph setup in `lean_mcts/README.md` or equivalent
- Update sprint status and lessons learned
- Update memory with new architecture

## Success Criteria

| Question | Minimum bar | Target | Status |
|---|---|---|---|
| Pantograph loads FormalNav | Works | Works | |
| T2 no regression | 10/10 | 10/10 | |
| V2 no regression | ≥11/20 | ≥13/20 | |
| Hard tasks solved | ≥2/5 | ≥4/5 | |
| Cat C at 500 rollouts | ≥2/5 | ≥4/5 | |
| Zero REPL crashes | 0 EOFError | 0 EOFError | |
| Per-tactic latency (subprocess) | <5ms | <2ms | |
| Per-tactic latency (FFI, stretch) | <200us | <100us | |
| LeanDojo removed from critical path | Yes | Yes | |

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Lean version mismatch (Pantograph vs FormalNav) | Medium | High | Pin PyPantograph to compatible commit. Build from source if needed. |
| State string format differs from LeanDojo | High | Medium | Write formatter in Phase 2d. Format is just pretty-printed goals. |
| PyPantograph import takes too long (Mathlib) | Low | Medium | Import once, keep server alive. Same as current `lake env lean`. |
| C FFI ABI unstable / segfaults | Medium | Low | Phase 4 is stretch goal. Subprocess path (Phase 1-3) is the deliverable. |
| Pantograph doesn't handle `by sorry` pattern | Medium | Medium | Use `goal_start` with the theorem's type directly instead of relying on source file parsing. |
| Hard tasks fail for policy reasons (not REPL) | Medium | Medium | If so, confirms expression space gap. Enumeration expansion in Sprint 16. |

## Execution Order

| Phase | Depends on | Est. wall time | Cumulative |
|---|---|---|---|
| Phase 0: Baseline Instrumentation | — | 2h | 2h |
| Phase 1: Setup + Smoke Test | Phase 0 | 3h | 5h |
| Phase 2: Wrapper Replacement | Phase 1 | 4h | 9h |
| Phase 3: Hard + Multi-Step Eval | Phase 2 | 3h | 12h |
| Phase 4: C FFI (stretch) | Phase 2 | 4h | 16h |
| Phase 5: Cleanup | Phase 3 | 1h | 13h (17h with stretch) |

Total: ~13 hours without FFI stretch, ~17 hours with.

## Makefile Targets

```bash
# Phase 0
make instrument-baseline # add SearchStats to mcts.py, run baseline benchmarks

# Phase 1
make pantograph-setup    # install PyPantograph, verify Lean version compat
make pantograph-smoke    # run smoke test on synth_identity

# Phase 2
make pantograph-test     # run T2 with PantographEnv, verify 10/10

# Phase 3
make benchmark-hard-pg   # hard tasks through Pantograph
make benchmark-v2-pg     # V2 tasks through Pantograph
make benchmark-full-pg   # full benchmark (T2 + Hard + V2)

# Phase 4 (stretch)
make pantograph-ffi      # build shared lib, run latency benchmark
```

## Relationship to Prior Sprints

**Sprint 14 → Sprint 15**: Sprint 14 proved the expression space and policy
are capable but infrastructure-limited. Sprint 15 replaces the infrastructure.

**Sprint 15 → Sprint 16**: With REPL stability solved, Sprint 16 can focus on:
- Context-aware policy (reads variable names from state → fixes Cat A 0/5)
- Higher rollout budgets for multi-step (enabled by faster tactic eval)
- Value network integration (meaningful with reliable multi-step search)
- Batch parallel evaluation (Lean-native, if FFI path proves viable)

The Pantograph migration is a **platform investment** — it doesn't change what
the model can express, but it removes the ceiling on what we can evaluate.
Every future sprint benefits from reliable, fast Lean interaction.
