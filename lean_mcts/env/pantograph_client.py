"""
Client for the persistent Pantograph daemon.

Drop-in replacement for PantographEnv: same reset()/step()/close() interface.
Connects to the daemon over a Unix domain socket, avoiding per-task server startup.

Usage:
    client = PantographClient()      # connects to daemon
    state = client.reset("countPositive", "FormalNav/Tasks.lean")
    result = client.step(state, "exact (xs.filter (· > 0)).length")
    client.close()                   # disconnects (daemon keeps running)
"""

from __future__ import annotations

import json
import os
import re
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from lean_mcts.env.pantograph_wrapper import StepResult
from lean_mcts.env.pantograph_daemon import DEFAULT_SOCKET, PROJECT_PATH


class ProxyGoalState:
    """Lightweight proxy for a GoalState held by the daemon.

    Supports str() for policy input and is_solved for proof checking.
    The actual GoalState lives in the daemon process.
    """
    __slots__ = ("state_id", "_goals_str", "is_solved")

    def __init__(self, state_id: int, goals_str: str, is_solved: bool):
        self.state_id = state_id
        self._goals_str = goals_str
        self.is_solved = is_solved

    def __str__(self):
        return self._goals_str

    def __repr__(self):
        return f"ProxyGoalState(#{self.state_id}, solved={self.is_solved})"


class PantographClient:
    """Connects to the Pantograph daemon over Unix socket.

    Same interface as PantographEnv: reset()/step()/close().

    Protocol hardening (Sprint 26):
    - Fresh socket per task: reset() reconnects to eliminate stale buffers
    - Request IDs: each request includes a unique ID, daemon echoes it back,
      client validates before processing — detects response desync
    """

    def __init__(self, socket_path: str = DEFAULT_SOCKET):
        self.socket_path = socket_path
        self._sock: Optional[socket.socket] = None
        self._file = None  # buffered file wrapper for readline
        self._next_req_id = 0  # monotonic request ID for correlation

    def connect(self):
        """Connect to the daemon. Called automatically on first use."""
        if self._sock is not None:
            return
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(self.socket_path)
        self._file = self._sock.makefile("rwb")

    def reconnect(self):
        """Close and reopen the socket. Eliminates stale buffer state."""
        self.close()
        self.connect()

    def _request(self, method: str, params: dict | None = None) -> dict:
        """Send a request and return the response.

        Each request includes a unique ID for correlation. The daemon echoes
        it back. If the response ID doesn't match, the connection is desynced
        and we reconnect.

        Retries once on disconnect or desync.
        """
        for attempt in range(2):
            if self._sock is None:
                self.connect()
            try:
                req_id = self._next_req_id
                self._next_req_id += 1
                msg = json.dumps({
                    "id": req_id,
                    "method": method,
                    "params": params or {},
                })
                self._file.write(msg.encode() + b"\n")
                self._file.flush()
                line = self._file.readline()
                if not line:
                    raise ConnectionError("daemon disconnected")
                resp = json.loads(line.decode())

                # Validate response ID matches request ID
                resp_id = resp.pop("id", None)
                if resp_id is not None and resp_id != req_id:
                    raise ConnectionError(
                        f"response desync: sent id={req_id}, got id={resp_id}"
                    )

                return resp
            except (ConnectionError, OSError, BrokenPipeError) as e:
                if attempt == 0:
                    # Close stale connection (including buffer) and retry
                    self.close()
                    import time as _time
                    _time.sleep(2)  # give daemon time to restart REPL
                    continue
                raise ConnectionError(f"daemon disconnected after retry: {e}")

    def ping(self) -> bool:
        """Check if daemon is alive."""
        try:
            resp = self._request("ping")
            return resp.get("ok", False)
        except (ConnectionError, OSError):
            return False

    def reset(self, theorem_name: str = None, lean_file: str = None) -> ProxyGoalState:
        """Start a new proof goal on the persistent server.

        When called with theorem_name + lean_file, mimics PantographEnv.reset():
        env_inspect → goal_start → auto-intro params.

        Reconnects the socket to eliminate stale buffer state from previous
        tasks (Sprint 26 fix).
        """
        # Fresh connection: eliminates any stale buffer state
        self.reconnect()

        # Clear old states from previous task
        self._request("clear_states")

        # Get theorem type (retry once if daemon restarted its REPL)
        resp = self._request("env_inspect", {"name": theorem_name})
        if not resp.get("ok") and "restart" in resp.get("error", "").lower():
            import time as _time
            _time.sleep(3)  # wait for REPL to fully restart
            resp = self._request("env_inspect", {"name": theorem_name})
        if not resp.get("ok"):
            raise RuntimeError(f"env_inspect failed: {resp.get('error', 'unknown')}")
        # Defensive: validate response structure before accessing nested fields
        result = resp.get("result")
        if not isinstance(result, dict) or "type" not in result:
            raise RuntimeError(
                f"env_inspect returned malformed result for '{theorem_name}': "
                f"missing 'type' in {list(result.keys()) if isinstance(result, dict) else type(result)}"
            )
        theorem_type = result["type"]["pp"]

        # Start goal
        resp = self._request("goal_start", {"expr": theorem_type})
        if not resp["ok"]:
            raise RuntimeError(f"goal_start failed: {resp['error']}")

        state = ProxyGoalState(
            state_id=resp["state_id"],
            goals_str=resp["goals_str"],
            is_solved=resp["is_solved"],
        )

        # Auto-intro parameters (same as PantographEnv._extract_param_names)
        param_names = self._extract_param_names(lean_file, theorem_name)
        if param_names:
            intro_tactic = "intro " + " ".join(param_names)
            result = self.step(state, intro_tactic)
            if result.state is not None:
                state = result.state
            # If intro fails, keep the original state (shouldn't happen)

        return state

    def _extract_param_names(self, lean_file: str, theorem_name: str) -> list[str]:
        """Extract parameter names from the Lean source definition."""
        lean_path = Path(PROJECT_PATH) / lean_file
        if not lean_path.exists():
            return []
        source = lean_path.read_text()
        pattern = rf'def\s+{re.escape(theorem_name)}\b(.*?):=\s*by'
        match = re.search(pattern, source, re.DOTALL)
        if not match:
            return []
        sig = match.group(1)
        # Handle both single-name `(xs : T)` and grouped multi-name
        # `(xs ys : T)` binders. The old `re.findall(r'\(\s*(\w+)\s*:', sig)`
        # dropped grouped binders entirely (returned [] for `(xs ys : List Int)`),
        # which caused MCTS to receive a ∀-prefixed goal with no hypotheses and
        # terminate after a handful of legal moves (S29 merge_sorted_desc, 21 calls).
        params: list[str] = []
        for m in re.finditer(r'\(\s*([a-zA-Z_]\w*(?:\s+[a-zA-Z_]\w*)*)\s*:', sig):
            params.extend(m.group(1).split())
        return params

    def step(self, state: ProxyGoalState, tactic: str) -> StepResult:
        """Apply a tactic via the daemon."""
        resp = self._request("goal_tactic", {
            "state_id": state.state_id,
            "tactic": tactic,
        })

        if not resp["ok"]:
            return StepResult(error=resp["error"])

        if resp["is_solved"]:
            return StepResult(solved=True)

        new_state = ProxyGoalState(
            state_id=resp["state_id"],
            goals_str=resp["goals_str"],
            is_solved=resp["is_solved"],
        )
        return StepResult(state=new_state)

    def check_compile(self, code: str) -> dict:
        """Run check_compile on the daemon's server."""
        resp = self._request("check_compile", {"code": code})
        if not resp["ok"]:
            raise RuntimeError(f"check_compile failed: {resp['error']}")
        return resp["units"]

    def check_compile_parallel(self, codes: list[str]) -> list[dict]:
        """Run multiple check_compile calls in parallel across verify workers.

        Each code string is dispatched to a verify-pool worker round-robin.
        Results are returned in the same order as the input codes.

        Sprint 29: dispatches across the verify pool only (never touches
        goal-ops workers), so a main benchmark client's sequential MCTS
        search on worker 0 cannot be raced by concurrent oracle checks on
        the same worker. On v1 (legacy) manifests the verify pool falls
        back to the full socket list, preserving old behavior — the
        per-worker asyncio.Lock in the daemon then provides a safety net.

        Falls back to sequential if the verify pool has ≤1 worker or if
        only one code needs checking (no parallelism benefit).
        """
        worker_sockets = self._discover_verify_workers()
        if len(worker_sockets) <= 1 or len(codes) <= 1:
            return [self.check_compile(c) for c in codes]

        # Distribute codes round-robin across workers
        n_workers = len(worker_sockets)
        results = [None] * len(codes)

        def _call_worker(sock_path: str, code: str) -> dict:
            """Send check_compile to a specific worker."""
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(sock_path)
            f = s.makefile("rwb")
            try:
                msg = json.dumps({"method": "check_compile", "params": {"code": code}})
                f.write(msg.encode() + b"\n")
                f.flush()
                line = f.readline()
                if not line:
                    raise ConnectionError(f"worker {sock_path} disconnected")
                resp = json.loads(line.decode())
                if not resp["ok"]:
                    raise RuntimeError(f"check_compile failed: {resp.get('error')}")
                return resp["units"]
            finally:
                f.close()
                s.close()

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {}
            for i, code in enumerate(codes):
                worker = worker_sockets[i % n_workers]
                fut = pool.submit(_call_worker, worker, code)
                futures[fut] = i

            for fut in as_completed(futures):
                idx = futures[fut]
                results[idx] = fut.result()

        return results

    def _load_manifest(self) -> dict:
        """Load the daemon's worker manifest, caching the result.

        Manifest format (v2, Sprint 29):
            {
              "version": 2,
              "sockets": [all worker sockets],         # legacy-compat flat list
              "goal_ops": [goal-ops pool sockets],     # v2 role-aware
              "verify":  [verify pool sockets],        # v2 role-aware
            }

        V1 (legacy) manifests only have "sockets" and are treated as a single
        undifferentiated pool — all workers serve every role. That was the
        Sprint 28 and earlier behavior and it causes response-misrouting bugs
        under concurrent access, but clients reading legacy manifests must
        still work for backward compatibility.
        """
        if hasattr(self, "_manifest_cache"):
            return self._manifest_cache
        base = self.socket_path.replace(".sock", "")
        manifest_path = f"{base}_workers.json"
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path) as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                data = {}
        else:
            data = {}
        # Normalize: ensure all expected keys exist with sane fallbacks
        sockets = data.get("sockets") or [self.socket_path]
        goal_ops = data.get("goal_ops") or [sockets[0]]
        verify = data.get("verify") or sockets  # legacy: all workers serve verify
        self._manifest_cache = {
            "version": data.get("version", 1),
            "sockets": sockets,
            "goal_ops": goal_ops,
            "verify": verify,
        }
        return self._manifest_cache

    def _discover_workers(self) -> list[str]:
        """Find ALL worker sockets (legacy, includes both pools).

        Retained for callers that don't care about role separation. New code
        should use `_discover_goal_ops_workers()` or `_discover_verify_workers()`.
        """
        return self._load_manifest()["sockets"]

    def _discover_goal_ops_workers(self) -> list[str]:
        """Find goal-ops pool sockets (stateful MCTS search workers).

        Sprint 29: goal-ops workers host sequential MCTS search state and must
        never be targeted by concurrent requests. Each main benchmark client
        should reserve one goal-ops worker for the duration of a task.
        """
        return self._load_manifest()["goal_ops"]

    def _discover_verify_workers(self) -> list[str]:
        """Find verify pool sockets (stateless check_compile workers).

        Sprint 29: verify workers serve only check_compile calls (oracle
        verification). Calls are stateless and can be fanned out round-robin
        across the pool for parallel oracle checks. These workers never see
        goal_start / goal_tactic traffic, so they cannot have their proof
        state corrupted by an unrelated MCTS client.
        """
        return self._load_manifest()["verify"]

    @property
    def n_workers(self) -> int:
        """Total number of available worker processes (both pools combined)."""
        return len(self._discover_workers())

    @property
    def n_verify_workers(self) -> int:
        """Number of workers in the verify pool."""
        return len(self._discover_verify_workers())

    def close(self):
        """Disconnect from daemon (does NOT stop the server)."""
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()


class DaemonPantographEnv:
    """Wrapper that makes PantographClient look exactly like PantographEnv.

    Used by run_task() so the same code path works for both daemon and
    per-task modes. Stores task info and delegates to the shared client.
    """

    def __init__(self, client: PantographClient, repo_path: str,
                 file_path: str, theorem_name: str):
        self._client = client
        self.repo_path = Path(repo_path)
        self.file_path = file_path
        self.theorem_name = theorem_name

    def reset(self) -> ProxyGoalState:
        return self._client.reset(
            theorem_name=self.theorem_name,
            lean_file=self.file_path,
        )

    def step(self, state: ProxyGoalState, tactic: str) -> StepResult:
        return self._client.step(state, tactic)

    def close(self):
        # Don't close the shared client — it's reused across tasks.
        # The next reset() will reconnect and clear_states anyway (Sprint 26).
        pass


def is_daemon_running(socket_path: str = DEFAULT_SOCKET) -> bool:
    """Check if the daemon is running and responsive."""
    try:
        client = PantographClient(socket_path)
        alive = client.ping()
        client.close()
        return alive
    except (ConnectionError, OSError, FileNotFoundError):
        return False
