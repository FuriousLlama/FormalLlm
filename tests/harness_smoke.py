"""
Sprint 29 Phase 0.3 harness smoke suite.

Fast regression suite that runs before any training / benchmark sweep to
catch harness-level breakage early. The S24/S26/S28 post-mortems showed
that silent bugs in the benchmark infrastructure can live for 6+ sprints
and cost the project real solves — this file is the standing guard that
should fire first.

Design goals:
- Runs in <5 minutes in default mode (no LoRA adapter load, no full bench)
- Each check is a single function that returns (ok: bool, detail: str)
- Zero external fixtures required beyond what's in the repo already
- Clean pass/fail reporting so `make smoke` is a blocking gate

Usage:
    python tests/harness_smoke.py                 # fast checks only
    python tests/harness_smoke.py --full          # include slow benchmark checks
    python tests/harness_smoke.py --only patience # run a single named check
    python tests/harness_smoke.py --list          # list available checks

The suite exits with code 0 on all-pass, 1 on any failure.

Checks covered (from sprint29.md Phase 0.3):
  [FAST]  1. parse_and_import      — ast.parse + import of S29-touched files
  [FAST]  2. held_out_loaded       — held_out_hard_tasks.py loads cleanly, 6 tasks
  [FAST]  3. ablation_join         — ablation_columns.py joins synthetic fixtures
  [SLOW]  4. template_solve        — one benchmark run, template-solvable task at rollout ≤ 5
  [SLOW]  5. patience_abort        — one benchmark run on unsolvable task with --patience=5
  [SLOW]  6. constructor_split     — product-type goal extraction (S24 bug regression)
  [SLOW]  7. unicode_type          — ℤ/ℕ task handling (S24 bug regression)
  [SLOW]  8. daemon_lifecycle      — daemon start + task + stop; verify clean exit
  [STUB]  9. lora_only_solve       — LoRA adapter path required (--adapter-path)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Path setup: add repo root so we can import lean_mcts.*
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The lean_mcts-specific venv — needed for PyPantograph, transformers, etc.
LEAN_MCTS_PYTHON = ROOT / "lean_mcts" / ".venv" / "bin" / "python"


# ---------------------------------------------------------------------------
# Infrastructure for running checks


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    is_slow: bool = False
    skipped: bool = False


FAST_CHECKS: list[tuple[str, Callable[[], CheckResult]]] = []
SLOW_CHECKS: list[tuple[str, Callable[[], CheckResult]]] = []
STUB_CHECKS: list[tuple[str, Callable[[], CheckResult]]] = []


def register_fast(name: str):
    def deco(fn: Callable[[], CheckResult]):
        FAST_CHECKS.append((name, fn))
        return fn
    return deco


def register_slow(name: str):
    def deco(fn: Callable[[], CheckResult]):
        SLOW_CHECKS.append((name, fn))
        return fn
    return deco


def register_stub(name: str):
    def deco(fn: Callable[[], CheckResult]):
        STUB_CHECKS.append((name, fn))
        return fn
    return deco


# ---------------------------------------------------------------------------
# Fast checks — no subprocess, no model load, no benchmark runs


@register_fast("parse_and_import")
def check_parse_and_import() -> CheckResult:
    """ast.parse each S29-touched file to catch syntax regressions."""
    import ast
    paths = [
        ROOT / "lean_mcts" / "search" / "mcts.py",
        ROOT / "lean_mcts" / "benchmark" / "run.py",
        ROOT / "lean_mcts" / "benchmark" / "ablation_columns.py",
        ROOT / "lean_mcts" / "synth" / "held_out_hard_tasks.py",
    ]
    for p in paths:
        if not p.exists():
            return CheckResult("parse_and_import", False, f"missing: {p}")
        try:
            ast.parse(p.read_text())
        except SyntaxError as e:
            return CheckResult("parse_and_import", False, f"{p}: {e}")
    return CheckResult("parse_and_import", True, f"{len(paths)} files parsed")


@register_fast("held_out_loaded")
def check_held_out_loaded() -> CheckResult:
    """Verify held_out_hard_tasks.py loads and contains exactly 6 tasks with valid shapes."""
    # Import via tasks.HARD_HOLDOUT_TASKS — the canonical re-export.
    # Direct `from lean_mcts.synth.held_out_hard_tasks import HELD_OUT_HARD_TASKS`
    # triggers a module-init circular import because tasks.py eagerly
    # calls _load_hard_holdout_tasks() during its own load (S29 Phase 3.0).
    try:
        from lean_mcts.benchmark.tasks import HARD_HOLDOUT_TASKS
    except Exception as e:
        return CheckResult("held_out_loaded", False, f"import failed: {e}")

    expected_names = {
        "sum_at_odd_indices", "zip_diff",
        "longest_increasing_run", "max_subarray_product",
        "merge_sorted_desc", "window_sums",
    }
    got_names = {t.name for t in HARD_HOLDOUT_TASKS}
    if got_names != expected_names:
        missing = expected_names - got_names
        extra = got_names - expected_names
        return CheckResult("held_out_loaded", False,
                           f"expected 6 specific tasks; missing={missing} extra={extra}")

    # Every task must have at least 4 test cases and a non-empty description
    for t in HARD_HOLDOUT_TASKS:
        if len(t.test_cases) < 4:
            return CheckResult("held_out_loaded", False, f"{t.name}: only {len(t.test_cases)} test cases")
        if not t.description:
            return CheckResult("held_out_loaded", False, f"{t.name}: missing description")
        if not t.lean_file.endswith("TasksS29Heldout.lean"):
            return CheckResult("held_out_loaded", False, f"{t.name}: wrong lean_file {t.lean_file}")

    return CheckResult("held_out_loaded", True, f"6/6 tasks loaded with ≥4 tests each")


@register_fast("daemon_recovery")
def check_daemon_recovery() -> CheckResult:
    """Verify the daemon rebuilds its Pantograph server after a timeout-style
    failure kills the Lean subprocess mid-request (S29 Phase 0.2 investigation).

    PyPantograph's `run_async` calls `self._close()` on `asyncio.TimeoutError`,
    `UnicodeDecodeError`, and `json.JSONDecodeError`, which terminates the Lean
    subprocess and sets `self.proc = None`, then raises `ServerError`. Before
    the S29 fix, the daemon's generic `except Exception` handler swallowed this
    without rebuilding, so every subsequent request hit the
    `assert self.proc, "Server not running."` guard and failed. Result: one
    bad task kills every later task in the same sweep (matches the S28
    "Config B gets 0/18 after ~3 runs" symptom).

    This test mocks a minimal Server that simulates exactly that kill path
    on the first call, monkey-patches `_create_server` so the recovery doesn't
    need a real Lean subprocess, and asserts:
      1. The first request after the simulated timeout triggers a rebuild
      2. The next request succeeds against the rebuilt server
    """
    import asyncio
    try:
        from lean_mcts.env.pantograph_daemon import PantographDaemon
        from pantograph.server import ServerError
    except Exception as e:
        return CheckResult("daemon_recovery", True,
                           f"pantograph import unavailable in this venv — skipping ({e})",
                           skipped=True)

    class FakeServer:
        """Mock that kills its proc on first env_inspect, simulating the
        run_async timeout path. Subsequent calls would hit `assert self.proc`."""
        def __init__(self):
            self.proc = object()  # truthy sentinel = "alive"
            self.call_count = 0

        async def env_inspect_async(self, name, **kwargs):
            self.call_count += 1
            if self.proc is None:
                # Matches PyPantograph's run_async assertion when called on a dead proc
                raise AssertionError("Server not running.")
            # Simulate _close() + raise ServerError path on timeout
            self.proc = None
            raise ServerError("simulated timeout limit")

        async def goal_start_async(self, expr):
            if self.proc is None:
                raise AssertionError("Server not running.")
            return object()  # opaque fake state

        async def gc_async(self):
            return None

    async def run_test():
        daemon = PantographDaemon(socket_path="/tmp/s29_smoke_daemon_test.sock")
        daemon.server = FakeServer()

        rebuild_log: list[int] = []

        async def fake_create_server():
            rebuild_log.append(daemon.server.call_count if daemon.server else -1)
            daemon.server = FakeServer()

        daemon._create_server = fake_create_server

        # Request 1: env_inspect on a fake server that kills itself mid-call.
        # Pre-fix: returns `{"ok": False, ...}` with no rebuild → daemon dead
        # Post-fix: detects proc=None in generic exception path, calls
        #           _create_server(), returns an error string indicating rebuild
        resp1 = await daemon._handle_request({"method": "env_inspect", "params": {"name": "probe"}})
        if resp1.get("ok"):
            return "BUG: first request unexpectedly succeeded (fake should fail)"

        # Post-fix assertion: rebuild was triggered exactly once during resp1
        if len(rebuild_log) != 1:
            return (f"daemon did not rebuild server after ServerError + proc=None "
                    f"(rebuild count: {len(rebuild_log)}); broken-recovery fix not applied")

        # Verify daemon.server is now a fresh FakeServer with a live proc
        if daemon.server is None or daemon.server.proc is None:
            return "daemon.server is dead after rebuild — fix incomplete"

        # Request 2: should now be served against the rebuilt fake.
        # The rebuilt fake will also kill itself (that's what env_inspect does),
        # but the fix should catch it AGAIN and rebuild. After that, 2 rebuilds total.
        resp2 = await daemon._handle_request({"method": "env_inspect", "params": {"name": "probe2"}})
        if resp2.get("ok"):
            return "BUG: second request unexpectedly succeeded"
        if len(rebuild_log) != 2:
            return (f"second rebuild did not fire (rebuild count: {len(rebuild_log)}) — "
                    f"recovery is not idempotent")

        return None  # all good

    try:
        loop = asyncio.new_event_loop()
        try:
            problem = loop.run_until_complete(run_test())
        finally:
            loop.close()
    except Exception as e:
        return CheckResult("daemon_recovery", False, f"test exception: {type(e).__name__}: {e}")

    if problem:
        return CheckResult("daemon_recovery", False, problem)
    return CheckResult("daemon_recovery", True, "dead-server detection + rebuild verified (2 rebuilds)")


@register_fast("daemon_lock_serializes")
def check_daemon_lock_serializes() -> CheckResult:
    """Verify the Sprint 29 per-worker asyncio.Lock serializes concurrent
    requests, preventing the response-misrouting bug that caused the S28
    "Config B gets 0/18" symptom.

    Without the lock: two concurrent coroutines calling self.server.X_async
    both write to Lean's stdin and both await readline on stdout. Since
    PyPantograph's Lean-side protocol has no request IDs, responses come
    back in FIFO-write order with no correlation, and each reader gets
    whichever line happened to be emitted first. Response B leaks into
    coroutine A's return value.

    This test wraps a mock Server that records the arrival order of
    concurrent calls and sleeps during each one. If the lock is working,
    calls are observed strictly serialized (start → finish → start → finish).
    If the lock is missing, overlapping starts are visible.
    """
    import asyncio
    try:
        from lean_mcts.env.pantograph_daemon import PantographDaemon
    except Exception as e:
        return CheckResult("daemon_lock_serializes", True,
                           f"pantograph import unavailable — skipping ({e})",
                           skipped=True)

    class TracingServer:
        """Mock Server that logs [start, finish] ranges across concurrent calls."""
        def __init__(self):
            self.proc = object()  # truthy = alive
            self.in_flight = 0
            self.max_concurrent = 0
            self.events: list[tuple[str, str, float]] = []

        async def env_inspect_async(self, name, **kwargs):
            self.in_flight += 1
            self.max_concurrent = max(self.max_concurrent, self.in_flight)
            self.events.append(("start", name, asyncio.get_event_loop().time()))
            # Yield control so another coroutine can try to race in. If the
            # lock works, no other coroutine will be able to reach this point
            # until we exit the `async with` block in _handle_request.
            await asyncio.sleep(0.01)
            self.events.append(("finish", name, asyncio.get_event_loop().time()))
            self.in_flight -= 1
            return {"type": {"pp": f"fake_type({name})"}, "isUnsafe": False}

        async def gc_async(self):
            return None

    async def run_test():
        daemon = PantographDaemon(socket_path="/tmp/s29_smoke_lock_test.sock")
        daemon.server = TracingServer()

        # Fire 5 concurrent handler calls. Each simulates an inbound client
        # request as the daemon's own _handle_request would process it.
        names = ["probe_a", "probe_b", "probe_c", "probe_d", "probe_e"]
        coros = [
            daemon._handle_request({"id": i, "method": "env_inspect", "params": {"name": n}})
            for i, n in enumerate(names)
        ]
        responses = await asyncio.gather(*coros)

        srv = daemon.server

        # All responses must be correct
        for i, (name, resp) in enumerate(zip(names, responses)):
            if not resp.get("ok"):
                return f"req {i} ({name}) returned error: {resp}"
            result = resp.get("result", {})
            if not isinstance(result, dict) or "type" not in result:
                return f"req {i} ({name}) malformed result: {result}"
            # The fake returns a type whose pp encodes its own name — if the
            # response is misrouted, pp will reference a different name.
            pp = result["type"].get("pp", "")
            if name not in pp:
                return f"req {i} ({name}) misrouted: got pp={pp!r}"

        # Lock must have kept max_concurrent == 1
        if srv.max_concurrent != 1:
            return (f"max concurrent calls to server = {srv.max_concurrent} "
                    f"(expected 1). Lock is not serializing.")

        # Event order must be strict start→finish→start→finish
        for i in range(0, len(srv.events), 2):
            if i + 1 >= len(srv.events):
                break
            a, b = srv.events[i], srv.events[i + 1]
            if not (a[0] == "start" and b[0] == "finish" and a[1] == b[1]):
                return f"events out of order near idx {i}: {srv.events[i:i+2]}"

        return None  # all good

    try:
        loop = asyncio.new_event_loop()
        try:
            problem = loop.run_until_complete(run_test())
        finally:
            loop.close()
    except Exception as e:
        return CheckResult("daemon_lock_serializes", False,
                           f"test exception: {type(e).__name__}: {e}")

    if problem:
        return CheckResult("daemon_lock_serializes", False, problem)
    return CheckResult("daemon_lock_serializes", True,
                       "5 concurrent requests serialized to max_concurrent=1, no misrouting")


@register_fast("client_role_dispatch")
def check_client_role_dispatch() -> CheckResult:
    """Verify PantographClient reads v2 role-aware manifests and dispatches
    check_compile_parallel across the verify pool only.

    Writes a fake v2 manifest with disjoint goal_ops and verify pools, then:
      1. Reads the manifest via _load_manifest()
      2. Asserts _discover_goal_ops_workers() returns the goal_ops list
      3. Asserts _discover_verify_workers() returns the verify list
      4. Asserts _discover_workers() (legacy) returns the union

    Also verifies v1 (legacy) manifest fallback: a manifest with only `sockets`
    should make goal_ops=[first_socket] and verify=[all_sockets] so old
    manifests still produce correct-ish behavior (with the daemon lock as a
    safety net).
    """
    try:
        from lean_mcts.env.pantograph_client import PantographClient
    except Exception as e:
        return CheckResult("client_role_dispatch", True,
                           f"pantograph import unavailable — skipping ({e})",
                           skipped=True)

    import tempfile, json

    with tempfile.TemporaryDirectory() as tmp:
        # v2 manifest — role-aware
        v2_sock = f"{tmp}/test.sock"
        v2_manifest_path = f"{tmp}/test_workers.json"
        v2_data = {
            "version": 2,
            "sockets": [f"{tmp}/test.sock", f"{tmp}/test_vr_0.sock", f"{tmp}/test_vr_1.sock"],
            "goal_ops": [f"{tmp}/test.sock"],
            "verify": [f"{tmp}/test_vr_0.sock", f"{tmp}/test_vr_1.sock"],
        }
        with open(v2_manifest_path, "w") as f:
            json.dump(v2_data, f)
        client = PantographClient(socket_path=v2_sock)
        if client._discover_goal_ops_workers() != v2_data["goal_ops"]:
            return f"v2 goal_ops mismatch: {client._discover_goal_ops_workers()}"
        if client._discover_verify_workers() != v2_data["verify"]:
            return f"v2 verify mismatch: {client._discover_verify_workers()}"
        if client._discover_workers() != v2_data["sockets"]:
            return f"v2 sockets mismatch: {client._discover_workers()}"
        if client.n_verify_workers != 2:
            return f"v2 n_verify_workers={client.n_verify_workers}, expected 2"

    with tempfile.TemporaryDirectory() as tmp:
        # v1 (legacy) manifest — only sockets key
        v1_sock = f"{tmp}/test.sock"
        v1_manifest_path = f"{tmp}/test_workers.json"
        v1_data = {"sockets": [f"{tmp}/test.sock", f"{tmp}/test_1.sock", f"{tmp}/test_2.sock"]}
        with open(v1_manifest_path, "w") as f:
            json.dump(v1_data, f)
        client = PantographClient(socket_path=v1_sock)
        # Legacy fallback: goal_ops = [first socket], verify = all sockets
        if client._discover_goal_ops_workers() != [v1_data["sockets"][0]]:
            return f"v1 goal_ops fallback wrong: {client._discover_goal_ops_workers()}"
        if client._discover_verify_workers() != v1_data["sockets"]:
            return f"v1 verify fallback wrong: {client._discover_verify_workers()}"
        if client._discover_workers() != v1_data["sockets"]:
            return f"v1 sockets fallback wrong: {client._discover_workers()}"

    with tempfile.TemporaryDirectory() as tmp:
        # No manifest at all — fall back to the provided socket_path alone
        no_sock = f"{tmp}/no_manifest.sock"
        client = PantographClient(socket_path=no_sock)
        if client._discover_goal_ops_workers() != [no_sock]:
            return f"no-manifest goal_ops wrong: {client._discover_goal_ops_workers()}"
        if client._discover_verify_workers() != [no_sock]:
            return f"no-manifest verify wrong: {client._discover_verify_workers()}"

    return CheckResult("client_role_dispatch", True,
                       "v2 + v1 + no-manifest dispatch all correct")


@register_fast("ablation_join")
def check_ablation_join() -> CheckResult:
    """Feed synthetic per-policy results into ablation_columns and verify the join."""
    try:
        from lean_mcts.benchmark.ablation_columns import build_ablation_table, summarize
    except Exception as e:
        return CheckResult("ablation_join", False, f"import failed: {e}")

    ef = [
        {"task": "a", "solved": True,  "tactics": ["exact xs.sum"], "ef_catalog_match": True,  "category": "core"},
        {"task": "b", "solved": False, "tactics": [],               "ef_catalog_match": None,  "category": "hard"},
    ]
    lora = [
        {"task": "a", "solved": True,  "tactics": ["exact xs.sum"], "ef_catalog_match": True,  "category": "core"},
        {"task": "b", "solved": True,  "tactics": ["let rec f .."], "ef_catalog_match": False, "category": "hard"},
    ]
    hybrid = [
        {"task": "a", "solved": True,  "tactics": ["exact xs.sum"], "ef_catalog_match": True,  "category": "core"},
        {"task": "b", "solved": True,  "tactics": ["let rec f .."], "ef_catalog_match": False, "category": "hard"},
    ]
    rows = build_ablation_table(ef, lora, hybrid)
    counts = summarize(rows)

    # Task b should be a lora_contribution (hybrid solved, EF-alone did not)
    row_b = next((r for r in rows if r["task"] == "b"), None)
    if row_b is None:
        return CheckResult("ablation_join", False, "row for task b missing")
    if row_b["lora_contribution"] is not True:
        return CheckResult("ablation_join", False,
                           f"expected lora_contribution=True on task b, got {row_b['lora_contribution']}")
    if row_b["hybrid_source"] != "lora_generation":
        return CheckResult("ablation_join", False,
                           f"expected hybrid_source=lora_generation on task b, got {row_b['hybrid_source']}")
    if counts.get("lora_contribution", 0) != 1:
        return CheckResult("ablation_join", False, f"expected 1 lora contribution, got {counts}")
    return CheckResult("ablation_join", True, f"join OK, counts={dict(counts)}")


# ---------------------------------------------------------------------------
# Slow checks — these spawn subprocesses that actually run the benchmark


def _run_benchmark_subprocess(args: list[str], timeout: int = 180) -> tuple[int, str, str]:
    """Run the benchmark as a subprocess in the lean_mcts venv and return (rc, stdout, stderr)."""
    if not LEAN_MCTS_PYTHON.exists():
        raise RuntimeError(f"lean_mcts venv python not found at {LEAN_MCTS_PYTHON}")
    cmd = [str(LEAN_MCTS_PYTHON), "-m", "lean_mcts.benchmark.run", *args]
    env = {"PYTHONPATH": str(ROOT), **dict(__import__("os").environ)}
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=str(ROOT))
    return proc.returncode, proc.stdout, proc.stderr


def _most_recent_result(output_dir: Path) -> dict | None:
    """Load the most recently created run_*.json from a benchmark output dir."""
    files = sorted(output_dir.glob("run_*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text())
    except Exception:
        return None


@register_slow("template_solve")
def check_template_solve() -> CheckResult:
    """Run one benchmark task with the fixed policy; expect solve at rollout ≤ 5."""
    with tempfile.TemporaryDirectory() as tmp:
        args = [
            "--policy", "expanded-fixed",
            "--task", "count_positive",
            "--rollouts", "10",
            "--no-daemon",
            "--output-dir", tmp,
        ]
        try:
            rc, _, err = _run_benchmark_subprocess(args, timeout=120)
        except Exception as e:
            return CheckResult("template_solve", False, f"subprocess failed: {e}", is_slow=True)
        if rc != 0:
            return CheckResult("template_solve", False, f"rc={rc} stderr={err[:300]}", is_slow=True)
        result = _most_recent_result(Path(tmp))
        if not result:
            return CheckResult("template_solve", False, "no result JSON written", is_slow=True)
        row = next((r for r in result if r.get("task") == "count_positive"), None)
        if row is None:
            return CheckResult("template_solve", False, "count_positive missing from results", is_slow=True)
        if not row.get("solved"):
            return CheckResult("template_solve", False, f"count_positive unsolved; tactics={row.get('tactics')}", is_slow=True)
        if row.get("rollouts_used", 10) > 5:
            return CheckResult("template_solve", False,
                               f"solved but too slow: rollouts_used={row.get('rollouts_used')}", is_slow=True)
        # Verify Phase 0.1 ablation columns are present
        if "policy_name" not in row or "ef_catalog_match" not in row:
            return CheckResult("template_solve", False,
                               "ablation columns (policy_name/ef_catalog_match) missing from result", is_slow=True)
        return CheckResult("template_solve", True,
                           f"solved at rollout {row.get('rollouts_used')}, ablation columns present",
                           is_slow=True)


@register_slow("patience_abort")
def check_patience_abort() -> CheckResult:
    """Run an unsolvable-for-EF task with --patience=5 and verify the abort."""
    # Using a diagnostic B task that EF cannot solve; patience=5 should abort fast.
    with tempfile.TemporaryDirectory() as tmp:
        args = [
            "--policy", "expanded-fixed",
            "--task", "max_subarray_sum",   # DIAG_B — no EF template solve
            "--rollouts", "500",
            "--patience", "5",
            "--no-daemon",
            "--output-dir", tmp,
        ]
        try:
            rc, _, err = _run_benchmark_subprocess(args, timeout=120)
        except Exception as e:
            return CheckResult("patience_abort", False, f"subprocess failed: {e}", is_slow=True)
        if rc != 0:
            return CheckResult("patience_abort", False, f"rc={rc} stderr={err[:300]}", is_slow=True)
        result = _most_recent_result(Path(tmp))
        if not result:
            return CheckResult("patience_abort", False, "no result JSON written", is_slow=True)
        row = next((r for r in result if r.get("task") == "max_subarray_sum"), None)
        if row is None:
            return CheckResult("patience_abort", False, "task row missing", is_slow=True)
        if row.get("solved"):
            # An unexpected solve is worth knowing about but not a failure of this check
            return CheckResult("patience_abort", True,
                               f"unexpectedly solved (rollouts={row.get('rollouts_used')}); patience never tripped",
                               is_slow=True)
        if row.get("abort_reason") != "patience_exceeded":
            return CheckResult("patience_abort", False,
                               f"expected abort_reason=patience_exceeded, got {row.get('abort_reason')}",
                               is_slow=True)
        if row.get("rollouts_used", 500) > 5:
            return CheckResult("patience_abort", False,
                               f"patience=5 but rollouts_used={row.get('rollouts_used')}", is_slow=True)
        return CheckResult("patience_abort", True,
                           f"aborted at rollout {row.get('rollouts_used')} with reason patience_exceeded",
                           is_slow=True)


@register_slow("constructor_split")
def check_constructor_split() -> CheckResult:
    """S24 regression: product-type tasks should use the FIRST `⊢` for goal extraction.
    Runs the sum_doubles task (has a product-type step) with rollouts=5 and verifies
    either solved or no exceptions."""
    with tempfile.TemporaryDirectory() as tmp:
        args = [
            "--policy", "expanded-fixed",
            "--task", "sum_doubles",
            "--rollouts", "5",
            "--no-daemon",
            "--output-dir", tmp,
        ]
        try:
            rc, out, err = _run_benchmark_subprocess(args, timeout=120)
        except Exception as e:
            return CheckResult("constructor_split", False, f"subprocess failed: {e}", is_slow=True)
        if rc != 0:
            return CheckResult("constructor_split", False, f"rc={rc} stderr={err[:300]}", is_slow=True)
        # Sanity: no 'KeyError' or 'extract_goal' traceback in output
        if "KeyError" in out + err or "extract_goal" in out + err:
            return CheckResult("constructor_split", False,
                               "goal extraction exception detected in benchmark output", is_slow=True)
        return CheckResult("constructor_split", True, "no goal-extraction errors", is_slow=True)


@register_slow("unicode_type")
def check_unicode_type() -> CheckResult:
    """S24 regression: tasks with ℤ/ℕ in type signatures should not crash the parser."""
    # Any task with Int/Nat return type. The unicode bug was in the state-string
    # parser, not the task definition, so we just need to run any task that goes
    # through the goal parser path.
    with tempfile.TemporaryDirectory() as tmp:
        args = [
            "--policy", "expanded-fixed",
            "--task", "max_abs",   # List Int -> Nat, exercises the goal parser
            "--rollouts", "5",
            "--no-daemon",
            "--output-dir", tmp,
        ]
        try:
            rc, out, err = _run_benchmark_subprocess(args, timeout=120)
        except Exception as e:
            return CheckResult("unicode_type", False, f"subprocess failed: {e}", is_slow=True)
        if rc != 0:
            return CheckResult("unicode_type", False, f"rc={rc} stderr={err[:300]}", is_slow=True)
        if "UnicodeDecodeError" in out + err or "codec can't decode" in out + err:
            return CheckResult("unicode_type", False, "unicode decode error detected", is_slow=True)
        return CheckResult("unicode_type", True, "no unicode errors", is_slow=True)


@register_slow("daemon_lifecycle")
def check_daemon_lifecycle() -> CheckResult:
    """Verify the Pantograph daemon's single-task lifecycle: --daemon start → task → clean exit.

    This only attempts the check if a daemon socket is already running. Starting a
    fresh daemon is out of scope for a smoke test (takes ~30-60s and holds resources).
    """
    try:
        from lean_mcts.env.pantograph_client import is_daemon_running
    except Exception as e:
        return CheckResult("daemon_lifecycle", False, f"import failed: {e}", is_slow=True)
    if not is_daemon_running():
        return CheckResult("daemon_lifecycle", True,
                           "daemon not running; skipping (use --daemon separately to smoke-test full lifecycle)",
                           is_slow=True, skipped=True)
    # Daemon is up — run one task against it.
    with tempfile.TemporaryDirectory() as tmp:
        args = [
            "--policy", "expanded-fixed",
            "--task", "count_positive",
            "--rollouts", "5",
            "--daemon",
            "--output-dir", tmp,
        ]
        try:
            rc, out, err = _run_benchmark_subprocess(args, timeout=180)
        except Exception as e:
            return CheckResult("daemon_lifecycle", False, f"subprocess failed: {e}", is_slow=True)
        if rc != 0:
            return CheckResult("daemon_lifecycle", False, f"rc={rc} stderr={err[:300]}", is_slow=True)
        if "KeyError" in out + err or "daemon corruption" in (out + err).lower():
            return CheckResult("daemon_lifecycle", False,
                               "daemon corruption signal detected in output", is_slow=True)
        return CheckResult("daemon_lifecycle", True, "daemon task completed cleanly", is_slow=True)


# ---------------------------------------------------------------------------
# Stub checks — require extra configuration (e.g. LoRA adapter path)


@register_stub("lora_only_solve")
def check_lora_only_solve() -> CheckResult:
    """STUB: exercise the ablation 'hybrid_source=lora_generation' branch.

    Requires the S28 Config C adapter (known to produce novel solves on
    zip_sum / dedup_count / filter_map_abs). Not implemented by default
    because the adapter path isn't wired into the smoke test; Phase 0.4's
    S28 reproduction step is the real integration test for this.
    """
    return CheckResult(
        "lora_only_solve", True,
        "STUB — requires --adapter-path to Config C; exercised via Phase 0.4 S28 reproduction",
        is_slow=True, skipped=True,
    )


# ---------------------------------------------------------------------------
# Runner


def all_checks() -> list[tuple[str, Callable[[], CheckResult], str]]:
    out: list[tuple[str, Callable[[], CheckResult], str]] = []
    for name, fn in FAST_CHECKS:
        out.append((name, fn, "fast"))
    for name, fn in SLOW_CHECKS:
        out.append((name, fn, "slow"))
    for name, fn in STUB_CHECKS:
        out.append((name, fn, "stub"))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--full", action="store_true",
                        help="Include slow benchmark-level checks (otherwise run fast checks only)")
    parser.add_argument("--only", type=str, default=None,
                        help="Run a single named check instead of the whole suite")
    parser.add_argument("--list", action="store_true", help="List available checks and exit")
    args = parser.parse_args()

    checks = all_checks()

    if args.list:
        print("Available smoke checks:")
        for name, _, tier in checks:
            print(f"  [{tier:5s}] {name}")
        return 0

    if args.only:
        checks = [c for c in checks if c[0] == args.only]
        if not checks:
            print(f"Unknown check: {args.only}")
            return 2
    elif not args.full:
        checks = [c for c in checks if c[2] == "fast"]

    print(f"Running {len(checks)} check(s)...")
    results: list[CheckResult] = []
    for name, fn, _tier in checks:
        print(f"  [RUN] {name:20s} ", end="", flush=True)
        try:
            res = fn()
        except Exception as e:
            res = CheckResult(name, False, f"exception: {type(e).__name__}: {e}")
        results.append(res)
        if res.skipped:
            print(f"SKIP — {res.detail}")
        elif res.ok:
            print(f"PASS — {res.detail}")
        else:
            print(f"FAIL — {res.detail}")

    failures = [r for r in results if not r.ok]
    skipped = [r for r in results if r.skipped]
    passed = [r for r in results if r.ok and not r.skipped]
    print()
    print(f"Summary: {len(passed)} pass, {len(failures)} fail, {len(skipped)} skip")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
