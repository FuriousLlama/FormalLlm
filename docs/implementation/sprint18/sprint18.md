# Sprint 18: Persistent Pantograph & Self-Play Pipeline

## Goal

Decouple Pantograph from the benchmark lifecycle by running it as a persistent daemon, then build an automated self-play pipeline that exploits the always-on server for rapid iteration. Fix remaining task failures.

## Context

Sprint 17 achieved V2: 19/20, Hard: 4/5, Recurrence: 3-4/6 (non-deterministic). Three systemic issues limit further progress:

1. **Pantograph startup overhead**: Each task spawns a fresh `Server.create()` that loads Mathlib. 41 tasks = 41 cold starts. First server often crashes (zombie), adding 5-10 min per benchmark run.
2. **Self-play is manual**: Sprint 17 ran self-play once (8 pairs → Hard 3/5→4/5). The loop should be automated — run benchmark N times, harvest solutions, retrain, repeat.
3. **Remaining failures**: `flatten` (0/200 across two sprints), `fibonacci` (close but not exact), recurrence non-determinism (50% solve rate).

## Phases

### Phase 0: Pantograph Daemon (4-5 hours)

Replace the per-task Pantograph subprocess with a persistent daemon that stays running across (and between) benchmark runs.

#### Architecture

```
pantograph_daemon.py (always running)     benchmark/run.py (transient)
┌───────────────────────────────┐         ┌─────────────────────────┐
│ Server.create(                │         │ client = PantoClient()  │
│   imports=[FormalNav, ...],   │         │ client.connect()        │
│   project_path=FormalNav/     │  unix   │                         │
│ )                             │ socket  │ state = client.reset(   │
│                               │◄───────►│   theorem="countPos")   │
│ Listen on                     │         │ r = client.step(        │
│   /tmp/pantograph.sock        │         │   state, "exact ...")   │
│                               │         │ client.check_compile(   │
│ Handle:                       │         │   code)                 │
│   goal_start / goal_tactic    │         │                         │
│   env_inspect / check_compile │         └─────────────────────────┘
│                               │
│ Ctrl-C or kill to stop        │
└───────────────────────────────┘
```

#### 0a. `lean_mcts/env/pantograph_daemon.py` (~100 lines)

Long-running process that:
1. Calls `Server.create()` once with all imports: `["FormalNav", "FormalNav.Tasks", "FormalNav.TasksV2", "FormalNav.TasksHard", "FormalNav.TasksRecurrence"]`
2. Listens on a Unix domain socket (`/tmp/pantograph.sock`)
3. Accepts JSON requests: `{"method": "goal_start", "params": {"type": "..."}}`, etc.
4. Returns JSON responses: `{"state_id": 0, "goals": "...", "solved": false}`, etc.
5. Handles one request at a time (single `pantograph-repl` is sequential)
6. Graceful shutdown on SIGINT/SIGTERM
7. Auto-restart on `pantograph-repl` crash (recreate server, log warning)

State management: assign integer IDs to `GoalState` objects, store them in a dict. Client refers to states by ID. States are cleaned up when a new `goal_start` is issued or explicitly via `clear_states`.

```bash
# Start (in a terminal or tmux/screen)
python -m lean_mcts.env.pantograph_daemon
# Prints: "Pantograph daemon ready on /tmp/pantograph.sock (loaded in 42s)"
# Stays alive until Ctrl-C
```

#### 0b. `lean_mcts/env/pantograph_client.py` (~80 lines)

Drop-in replacement for `PantographEnv`:

```python
class PantographClient:
    """Connects to the Pantograph daemon over Unix socket.

    Same interface as PantographEnv: reset(theorem_name) → state, step(state, tactic) → StepResult.
    """
    def __init__(self, socket_path="/tmp/pantograph.sock"):
        ...

    def reset(self, theorem_name: str, lean_file: str) -> GoalState:
        """Start a new proof goal on the persistent server."""
        ...

    def step(self, state, tactic: str) -> StepResult:
        """Apply a tactic via the daemon."""
        ...

    def check_compile(self, code: str) -> dict:
        """Run check_compile on the daemon's server (for warm oracle)."""
        ...

    def close(self):
        """Disconnect from daemon (does NOT stop the server)."""
        ...
```

#### 0c. Update `benchmark/run.py`

- Add `--daemon` flag (default: on when socket exists)
- When using daemon: create one `PantographClient`, reuse across all tasks
- `run_task()` calls `client.reset(theorem_name, lean_file)` instead of constructing a new `PantographEnv`
- Warm oracle uses `client.check_compile()` instead of `env._server.check_compile_async()`
- Fallback: if daemon not running, fall back to per-task `PantographEnv` (backwards compatible)

#### 0d. Update warm oracle

`make_warm_oracle()` currently captures `env._server` and `env._loop`. Update to accept a `PantographClient` instead when using daemon mode. The client's `check_compile()` sends the request over the socket to the daemon's server.

#### Expected benefits

| Metric | Before | After |
|--------|--------|-------|
| Startup overhead per task | 5-30s (Mathlib load) | 0s (already loaded) |
| Zombie crash on first task | ~30% of runs | 0% (one startup, retry once) |
| Benchmark startup to first task | 30-60s | <1s (connect to socket) |
| Iterate on benchmark code | Must wait for server each run | Instant |
| Total V2 benchmark wall time | ~30 min | ~25 min (saves ~5 min startup) |

#### Validation

```bash
# Terminal 1: start daemon
python -m lean_mcts.env.pantograph_daemon

# Terminal 2: run benchmark
python -m lean_mcts.benchmark.run --policy hybrid-gc --tier v2 --rollouts 50 --daemon

# Verify: same results as without daemon (19/20 V2)
# Verify: no Pantograph startup messages per task
# Verify: Ctrl-C in terminal 1 cleanly shuts down
```

### Phase 1: Investigate Remaining Failures (2-3 hours)

With the daemon running, iterate fast on the 3 unsolved problems.

#### 1a. `flatten` deep dive

`flatten` has been 0/200 across two sprints. Diagnose root cause:

1. Check test cases: does `xss.flatten` vs `List.flatten xss` vs `List.join xss` matter?
2. Check if `flatten` is even in the expression space — run `--dump-candidates` to see what the model proposes
3. Check oracle: does the correct reference solution actually pass `warm_verify`?
4. If semantic mismatch: fix oracle. If expression gap: add to training data.

#### 1b. `fibonacci` targeted data

Sprint 17 showed the model generates *similar* patterns but not the exact destructuring `(fun (a, b) _ => (b, a + b))` with `(0, 1)`. Options:

1. Add 10-15 more fibonacci-like pairs with slight variations (different initial states, different recurrences)
2. Increase rollout budget to 500 for recurrence tier
3. Check if the near-miss candidates fail on oracle or on syntax

#### 1c. Recurrence stability

`triangular_numbers` and `powers_of_2` solve in ~50% of runs. Options:
- Run 5x with different seeds, report union solve rate
- If solve rate ≥ 80% at 500 rollouts, accept as stable
- If not, add more training data for the specific pattern

### Phase 2: Automated Self-Play Pipeline (3-4 hours)

Sprint 17's manual self-play (Phase 5) worked: 8 solutions → Hard 3/5→4/5. Automate this as a repeatable pipeline.

#### 2a. `lean_mcts/synth/selfplay_loop.py`

End-to-end script that:

```python
def selfplay_round(
    tiers: list[str] = ["v2", "hard", "recurrence"],
    rollouts: int = 200,
    n_runs: int = 3,
    adapter_path: str = "train/goal_cond_adapter",
) -> dict:
    """One round of self-play:
    1. Run benchmark N times (via daemon)
    2. Extract all oracle-verified novel solutions
    3. Deduplicate against existing training data
    4. Add new (state, tactic) pairs to training set
    5. Retrain adapter
    6. Return metrics delta
    """
```

#### 2b. Multi-round execution

```bash
# Run 3 rounds of self-play
python -m lean_mcts.synth.selfplay_loop --rounds 3 --runs-per-round 3 --rollouts 200

# Each round:
#   - Benchmarks 3x (exploits daemon for fast startup)
#   - Harvests novel solutions
#   - Retrains (~30 min)
#   - Reports: tasks solved, new pairs added, val_loss delta
```

#### 2c. Convergence tracking

Log per-round:
- Tasks solved (per tier)
- New unique (state, tactic) pairs discovered
- val_loss before/after retrain
- Stop when 0 new pairs discovered in a round (convergence)

### Phase 3: Benchmark Reliability (2 hours)

#### 3a. Multi-run reporting

Update `benchmark/run.py` to support `--runs N` with proper aggregation:
- Report: solve rate per task (e.g., "factorial: 3/5 runs")
- Report: union solve rate (solved in at least 1 run)
- Report: intersection solve rate (solved in all runs)

#### 3b. Deterministic seed control

Add `--seed` flag to benchmark runner. Pass seed to MCTS for reproducible rollout ordering. Allows comparing policy changes on identical search trajectories.

### Phase 4: Value Network (stretch, 4-5 hours)

MCTS currently uses PUCT with policy only — no value function. A value head could dramatically improve search efficiency by steering rollouts toward promising goals.

#### 4a. Training data for value network

From self-play data, label each (state, tactic_sequence) with:
- 1.0 if the tactic sequence led to a solved goal
- 0.0 if it led to a dead end
- Intermediate values based on depth-to-solution / max-depth

#### 4b. Value head architecture

Reuse the Qwen2.5-Coder-1.5B backbone (shared with GoalConditioned policy):
- Add a linear head on the [CLS] / last-token embedding
- Train on (goal_state_text → solve_probability) pairs
- Small dataset (~5K-10K from self-play) should suffice for a useful signal

#### 4c. PUCT integration

Already stubbed in `mcts.py` (`value_weight` parameter). Wire in the trained value head:
- `value_fn(goal_state_str) → float` predicting solve probability
- PUCT mixes policy prior + value estimate for node selection
- Expected: fewer wasted rollouts on dead-end branches

## Success Criteria

| Metric | Minimum | Target | Stretch |
|--------|---------|--------|---------|
| Daemon startup | < 60s | < 45s | < 30s |
| Benchmark overhead (no daemon restart) | < 5s | < 1s | < 0.5s |
| V2 (50 rollouts) | 19/20 | 20/20 | 20/20 |
| Hard (200 rollouts) | 4/5 | 5/5 | 5/5 |
| Recurrence (200 rollouts, union of 3 runs) | 4/6 | 5/6 | 6/6 |
| Self-play rounds to convergence | ≤ 5 | ≤ 3 | — |
| val_loss | < 0.28 | < 0.27 | < 0.25 |

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Unix socket adds serialization overhead (>5ms/call) | Low | Benchmark: measure round-trip latency. GoalState serialization is small (~1KB) |
| GoalState objects don't serialize cleanly | Medium | Store states by ID in daemon, pass IDs over socket. Client never holds raw GoalState |
| Daemon crashes under sustained load | Low | Auto-restart logic in daemon. Benchmark runner detects disconnect and reconnects |
| Self-play converges at current level (no new solutions) | Medium | Increase rollout budget per round. Add targeted training data from failure analysis |
| Value network hurts more than helps (bad estimates) | Medium | Use low value_weight (0.2-0.3). A/B test against policy-only baseline |

## Estimated Effort

| Phase | Hours | Dependencies |
|-------|-------|-------------|
| Phase 0: Pantograph daemon | 4-5 | None |
| Phase 1: Failure investigation | 2-3 | Phase 0 (fast iteration) |
| Phase 2: Self-play pipeline | 3-4 | Phase 0 + Phase 1 |
| Phase 3: Benchmark reliability | 2 | None |
| Phase 4: Value network (stretch) | 4-5 | Phase 2 (training data) |
| **Total** | **15-19** | |

## Files to Create

| File | Purpose |
|------|---------|
| `lean_mcts/env/pantograph_daemon.py` | Persistent Pantograph server process |
| `lean_mcts/env/pantograph_client.py` | Client connecting to daemon (drop-in for PantographEnv) |
| `lean_mcts/synth/selfplay_loop.py` | Automated self-play: benchmark → harvest → retrain → repeat |

## Files to Modify

| File | Changes |
|------|---------|
| `lean_mcts/benchmark/run.py` | `--daemon` flag, use PantographClient, reuse across tasks |
| `lean_mcts/eval/oracle.py` | Warm oracle via PantographClient.check_compile() |
| `lean_mcts/env/pantograph_wrapper.py` | Extract shared protocol types (StepResult stays here) |
| `lean_mcts/search/mcts.py` | Seed control for reproducibility |
| `lean_mcts/synth/collect_selfplay_v2.py` | Integrate into selfplay_loop.py |

## Non-Goals

- No new model architecture (still Qwen2.5-Coder-1.5B + LoRA)
- No multi-tactic `let rec` synthesis
- No Mathlib proof tasks
- No changes to GHC Core pipeline (sprint/)
- No Docker/containerization of the daemon
- No network-accessible daemon (Unix socket only, localhost)
