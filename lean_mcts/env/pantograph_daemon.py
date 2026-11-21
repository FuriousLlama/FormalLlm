"""
Persistent Pantograph daemon for MCTS benchmark acceleration.

Loads the Pantograph server once with all FormalNav imports, then listens
on a Unix domain socket for JSON requests.  Eliminates the per-task cold
start that adds 5-30s per task (and ~30% zombie-crash rate on first task).

Usage:
    python -m lean_mcts.env.pantograph_daemon            # default socket
    python -m lean_mcts.env.pantograph_daemon --socket /tmp/my.sock

Protocol (newline-delimited JSON over Unix socket):
    → {"method": "goal_start", "params": {"expr": "∀ ..."}}
    ← {"ok": true, "state_id": 0, "goals_str": "...", "is_solved": false}

    → {"method": "goal_tactic", "params": {"state_id": 0, "tactic": "intro xs"}}
    ← {"ok": true, "state_id": 1, "goals_str": "...", "is_solved": false}
    ← {"ok": false, "error": "tactic failed"}

    → {"method": "env_inspect", "params": {"name": "countPositive"}}
    ← {"ok": true, "result": {...}}

    → {"method": "check_compile", "params": {"code": "..."}}
    ← {"ok": true, "units": [...]}

    → {"method": "clear_states", "params": {}}
    ← {"ok": true}

    → {"method": "ping", "params": {}}
    ← {"ok": true}
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

from pantograph import Server
from pantograph.expr import GoalState
from pantograph.server import TacticFailure, ServerError

DEFAULT_SOCKET = "/tmp/pantograph.sock"
ALL_IMPORTS = [
    "FormalNav",
    "FormalNav.Tasks",
    "FormalNav.TasksV2",
    "FormalNav.TasksHard",
    "FormalNav.TasksRecurrence",
    "FormalNav.TasksMultiStep",
    "FormalNav.TasksDeepMultiStep",
    "FormalNav.TasksCompositional",
    "FormalNav.TasksRecursivePrograms",
    "FormalNav.TasksStretch",
    "FormalNav.TasksVocabulary",
    "FormalNav.TasksDiagnostic",
    "FormalNav.TasksS29Heldout",
]
PROJECT_PATH = str(Path(__file__).parent / "FormalNav")


class PantographDaemon:
    """Long-running Pantograph server with Unix socket interface.

    One daemon instance = one Pantograph Server = one Lean subprocess. Multiple
    client connections to the same worker spawn concurrent `_handle_client`
    coroutines on a single asyncio event loop, all sharing `self.server`.

    Concurrency model (Sprint 29 fix): a single `asyncio.Lock` serializes every
    request that touches `self.server`. Pantograph and the Lean REPL it wraps
    speak over a single stdin/stdout pipe with no request IDs at the Lean-side
    protocol — responses arrive in FIFO order relative to writes, with no way
    to correlate them back to requests. Concurrent coroutines calling
    `self.server.X_async(...)` would therefore scramble responses (each reader
    gets whichever line Lean emitted first, not the one matching its own
    command). The lock prevents that at the cost of serializing one request at
    a time within a worker. Multi-worker parallelism is achieved by running N
    separate worker processes, each with its own lock/Server/Lean.
    """

    def __init__(self, socket_path: str = DEFAULT_SOCKET):
        self.socket_path = socket_path
        self.server: Server | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        # GoalState storage: int ID → GoalState
        self._states: dict[int, GoalState] = {}
        self._next_id = 0
        self._shutting_down = False
        # Serializes access to self.server (see class docstring). Created lazily
        # inside run() so it binds to the worker's own event loop, not the one
        # that happened to be current when PantographDaemon was instantiated.
        self._server_lock: asyncio.Lock | None = None

    def _store_state(self, gs: GoalState) -> int:
        """Store a GoalState and return its proxy ID."""
        sid = self._next_id
        self._next_id += 1
        self._states[sid] = gs
        return sid

    async def _create_server(self):
        """Create (or recreate) the Pantograph server."""
        if self.server is not None:
            try:
                self.server._close()
            except Exception:
                pass
        print(f"  Loading Pantograph server with imports: {ALL_IMPORTS}")
        t0 = time.time()
        self.server = await Server.create(
            imports=ALL_IMPORTS,
            project_path=PROJECT_PATH,
            timeout=300,
        )
        elapsed = time.time() - t0
        print(f"  Server ready in {elapsed:.1f}s")

    async def _handle_request(self, data: dict) -> dict:
        """Dispatch a single JSON request to the appropriate handler.

        If the request includes an "id" field, the response echoes it back
        for client-side correlation (Sprint 26 desync detection).

        Concurrency (Sprint 29): `ping` is handled without the server lock so
        health checks stay fast even while a worker is mid-request. Every
        other method acquires `self._server_lock` for the duration of the
        server call, guaranteeing at most one in-flight call to
        `self.server.X_async` per worker.
        """
        method = data.get("method", "")
        params = data.get("params", {})
        req_id = data.get("id")  # echoed back for correlation

        # Lock-free fast path — ping must never contend with a slow benchmark
        # request, so health checks remain authoritative even under heavy load.
        if method == "ping":
            return {"ok": True}

        # Lazy lock init: bind to the worker's event loop on first call.
        if self._server_lock is None:
            self._server_lock = asyncio.Lock()

        async with self._server_lock:
            return await self._handle_server_request(method, params, data)

    async def _handle_server_request(self, method: str, params: dict, data: dict) -> dict:
        """Body of _handle_request after acquiring the server lock. All paths
        in here touch `self.server` and must be serialized per worker."""
        try:
            if method == "goal_start":
                expr = params["expr"]
                gs = await self.server.goal_start_async(expr)
                sid = self._store_state(gs)
                return {
                    "ok": True,
                    "state_id": sid,
                    "goals_str": str(gs),
                    "is_solved": gs.is_solved,
                }

            elif method == "goal_tactic":
                sid = params["state_id"]
                tactic = params["tactic"]
                gs = self._states.get(sid)
                if gs is None:
                    return {"ok": False, "error": f"unknown state_id: {sid}"}
                try:
                    new_gs = await self.server.goal_tactic_async(gs, tactic=tactic)
                except TacticFailure as e:
                    return {"ok": False, "error": str(e)}
                except ServerError as e:
                    return {"ok": False, "error": str(e)}
                new_sid = self._store_state(new_gs)
                return {
                    "ok": True,
                    "state_id": new_sid,
                    "goals_str": str(new_gs),
                    "is_solved": new_gs.is_solved,
                }

            elif method == "env_inspect":
                name = params["name"]
                result = await self.server.env_inspect_async(name=name)
                return {"ok": True, "result": result}

            elif method == "check_compile":
                code = params["code"]
                units = await self.server.check_compile_async(code)
                # Serialize CompilationUnit messages for transport
                serialized = []
                for cu in units:
                    msgs = []
                    for msg in cu.messages:
                        msgs.append({
                            "severity": msg.severity.name,
                            "data": msg.data,
                        })
                    serialized.append({"messages": msgs})
                return {"ok": True, "units": serialized}

            elif method == "clear_states":
                self._states.clear()
                self._next_id = 0  # Sprint 26: reset counter to prevent unbounded growth
                # Trigger GC on the server side
                try:
                    await self.server.gc_async()
                except Exception:
                    pass
                return {"ok": True}

            elif method == "delete_state":
                sid = params["state_id"]
                self._states.pop(sid, None)
                return {"ok": True}

            else:
                return {"ok": False, "error": f"unknown method: {method}"}

        except (EOFError, OSError, ConnectionError) as e:
            # REPL crashed via a connection-level error — try to restart.
            print(f"  [daemon] REPL crashed on {method!r} (conn error): "
                  f"{type(e).__name__}: {e}. Restarting server...")
            try:
                await self._create_server()
                self._states.clear()
                return {"ok": False, "error": f"server restarted after crash: {e}"}
            except Exception as e2:
                return {"ok": False, "error": f"server restart failed: {e2}"}
        except Exception as e:
            # Sprint 29 Phase 0 fix — broken-recovery path for "daemon corruption".
            #
            # PyPantograph's `run_async` (pantograph/server.py) calls `self._close()`
            # on internal asyncio.TimeoutError / UnicodeDecodeError / JSONDecodeError
            # BEFORE raising ServerError. _close() terminates the Lean subprocess and
            # sets `self.proc = None`. ServerError is not in the (EOF/OS/Connection)
            # clause above, so previously this fell through to the generic handler
            # which just returned an error without rebuilding. Every subsequent
            # request then hit the `assert self.proc, "Server not running."` guard
            # inside run_async and also failed, for the rest of the sweep. That is
            # almost certainly the mechanism behind the "Config B gets 0/18 after
            # ~3 back-to-back runs" symptom documented in Sprint 28 lessons.
            #
            # Fix: detect a dead server here (proc is None) and rebuild it via
            # _create_server() before returning. The original request still fails
            # (we cannot safely replay it without knowing if it had side effects),
            # but the NEXT request sees a healthy server and the sweep continues.
            proc_alive = (
                self.server is not None
                and getattr(self.server, "proc", None) is not None
            )
            print(
                f"  [daemon] request {method!r} failed: "
                f"{type(e).__name__}: {e} (server proc alive: {proc_alive})"
            )
            if not proc_alive:
                print(f"  [daemon] server subprocess is dead — rebuilding to recover...")
                try:
                    await self._create_server()
                    self._states.clear()
                    return {
                        "ok": False,
                        "error": f"server rebuilt after {type(e).__name__}: {e}",
                    }
                except Exception as e2:
                    return {
                        "ok": False,
                        "error": f"server rebuild failed after {type(e).__name__}: {e2}",
                    }
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle one client connection (may send multiple requests)."""
        peer = writer.get_extra_info("peername") or "client"
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break  # client disconnected
                try:
                    data = json.loads(line.decode())
                except json.JSONDecodeError as e:
                    resp = {"ok": False, "error": f"invalid JSON: {e}"}
                    writer.write(json.dumps(resp).encode() + b"\n")
                    await writer.drain()
                    continue

                resp = await self._handle_request(data)
                # Echo request ID for client-side correlation (Sprint 26)
                req_id = data.get("id")
                if req_id is not None:
                    resp["id"] = req_id
                writer.write(json.dumps(resp, ensure_ascii=False).encode() + b"\n")
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def run(self):
        """Start the daemon: create server, listen on socket."""
        # Clean up stale socket
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        await self._create_server()

        srv = await asyncio.start_unix_server(
            self._handle_client, path=self.socket_path
        )
        print(f"Pantograph daemon ready on {self.socket_path}")

        # Handle graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(self._shutdown(srv)))

        async with srv:
            await srv.serve_forever()

    async def _shutdown(self, srv):
        """Graceful shutdown."""
        if self._shutting_down:
            return
        self._shutting_down = True
        print("\nShutting down daemon...")
        srv.close()
        if self.server is not None:
            try:
                self.server._close()
            except Exception:
                pass
        # Clean up socket
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        # Stop the event loop
        asyncio.get_event_loop().stop()


def _worker_process(socket_path: str, worker_id: int):
    """Entry point for a worker process — runs its own asyncio event loop."""
    print(f"  Worker {worker_id} starting on {socket_path}")
    daemon = PantographDaemon(socket_path=socket_path)
    asyncio.run(daemon.run())


def main():
    import argparse
    import multiprocessing

    parser = argparse.ArgumentParser(description="Persistent Pantograph daemon")
    parser.add_argument("--socket", default=DEFAULT_SOCKET, help="Unix socket path for the primary goal-ops worker")
    parser.add_argument("--workers", type=int, default=0,
                        help="Total number of worker processes (0=auto: 50%% of RAM, ~3.8GB/worker)")
    parser.add_argument("--goal-ops", type=int, default=1, dest="goal_ops",
                        help="Number of workers reserved for goal operations "
                             "(sequential MCTS search). Remaining workers are the "
                             "verify pool (parallel check_compile). Default 1.")
    args = parser.parse_args()

    total_gb = 0
    if args.workers == 0:
        # Auto: use at most 50% of total RAM, ~3.8GB per worker
        try:
            total_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024**3)
            budget_gb = total_gb * 0.5
            n_workers = max(1, min(int(budget_gb / 3.8), 16))
        except Exception:
            n_workers = 1
    else:
        n_workers = max(1, args.workers)

    # Sprint 29 role split: the first K workers are goal-ops, the rest are verify.
    # Goal-ops workers host MCTS search state (goal_start / goal_tactic / env_inspect);
    # verify workers host stateless check_compile calls fanning out from the oracle.
    # Clients dispatch by role (see PantographClient._discover_* methods) so a
    # goal-ops worker never sees a concurrent verify request, and vice versa.
    n_goal_ops = max(1, min(args.goal_ops, n_workers))
    n_verify = n_workers - n_goal_ops

    print(f"Starting Pantograph daemon (project: {PROJECT_PATH})")
    if total_gb > 0:
        print(f"  Workers: {n_workers} total = {n_goal_ops} goal-ops + {n_verify} verify "
              f"(RAM: {total_gb:.0f}GB total, ~{n_workers*3.8:.0f}GB for workers)")
    else:
        print(f"  Workers: {n_workers} total = {n_goal_ops} goal-ops + {n_verify} verify")

    if n_workers == 1:
        # Single worker: acts as goal-ops AND verify (degenerate case, no
        # parallelism). Runs in the main process for backwards compatibility.
        # The per-worker asyncio.Lock still guarantees correctness even if a
        # client mixes goal-ops and check_compile on this one socket.
        daemon = PantographDaemon(socket_path=args.socket)
        # Emit a manifest so clients using the new role-aware API still work.
        base = args.socket.replace(".sock", "")
        manifest_path = f"{base}_workers.json"
        import json as _json
        with open(manifest_path, "w") as f:
            _json.dump({
                "version": 2,
                "sockets": [args.socket],
                "goal_ops": [args.socket],
                "verify": [args.socket],
            }, f)
        print(f"  Worker manifest: {manifest_path}")
        asyncio.run(daemon.run())
    else:
        # Multi-worker: spawn N processes on separate sockets.
        # Workers 0..n_goal_ops-1 are goal-ops (first one is args.socket for
        # backward compat with clients that hardcode /tmp/pantograph.sock).
        # Workers n_goal_ops..n_workers-1 are verify.
        base = args.socket.replace(".sock", "")
        goal_ops_sockets: list[str] = [args.socket]  # worker 0 = primary goal-ops
        for i in range(1, n_goal_ops):
            goal_ops_sockets.append(f"{base}_go_{i}.sock")
        verify_sockets: list[str] = []
        for i in range(n_verify):
            verify_sockets.append(f"{base}_vr_{i}.sock")
        worker_sockets = goal_ops_sockets + verify_sockets

        # Write worker manifest. Keys:
        #   version    — 2 (role-aware format); older clients reading v1 ignore this.
        #   sockets    — flat list of all workers (legacy-compat key).
        #   goal_ops   — v2 list of goal-ops pool sockets.
        #   verify     — v2 list of verify pool sockets.
        manifest_path = f"{base}_workers.json"
        import json as _json
        with open(manifest_path, "w") as f:
            _json.dump({
                "version": 2,
                "sockets": worker_sockets,
                "goal_ops": goal_ops_sockets,
                "verify": verify_sockets,
            }, f)
        print(f"  Worker manifest: {manifest_path}")
        print(f"    goal-ops: {goal_ops_sockets}")
        print(f"    verify:   {verify_sockets}")

        processes = []
        for i, sock_path in enumerate(worker_sockets):
            p = multiprocessing.Process(
                target=_worker_process,
                args=(sock_path, i),
                daemon=True,
            )
            p.start()
            processes.append(p)
            print(f"  Worker {i} pid={p.pid} on {sock_path}")

        # Wait for all workers, handle Ctrl-C
        try:
            for p in processes:
                p.join()
        except KeyboardInterrupt:
            print("\nShutting down all workers...")
            for p in processes:
                p.terminate()
            for p in processes:
                p.join(timeout=5)
            # Clean up sockets and manifest
            for sock_path in worker_sockets:
                if os.path.exists(sock_path):
                    os.unlink(sock_path)
            if os.path.exists(manifest_path):
                os.unlink(manifest_path)


if __name__ == "__main__":
    main()
