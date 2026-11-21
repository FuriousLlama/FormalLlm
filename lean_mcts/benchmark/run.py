"""
Benchmark runner for Lean MCTS synthesis.

Runs MCTS on benchmark tasks and reports results.
Designed to be run multiple times (non-deterministic search) and report ranges.

Usage:
    python benchmark/run.py --rollouts 500 --runs 3
    python benchmark/run.py --task count_positive --rollouts 1000 --verbose
    python benchmark/run.py --no-oracle  # type-check only, no test verification
    python benchmark/run.py --policy trained --adapter-path train/policy_adapter/
    python benchmark/run.py --tier hard  # run only hard tasks
    python benchmark/run.py --daemon     # use persistent Pantograph daemon
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

# These imports will work once the packages are in the lean_mcts venv
try:
    from lean_mcts.env.dojo_wrapper import LeanEnv
    from lean_mcts.env.pantograph_wrapper import PantographEnv
    from lean_mcts.env.pantograph_client import PantographClient, DaemonPantographEnv, is_daemon_running
    from lean_mcts.search.mcts import search
    from lean_mcts.search.policy import FixedPolicy, ExpandedFixedPolicy, PromptedPolicy, TrainedPolicy, SynthesisPolicy, GoalConditionedPolicy, HybridGoalConditionedPolicy, CompositionalPolicy, HybridCompositionalPolicy, OllamaPolicy, HybridOllamaPolicy
    from lean_mcts.search.value import StubValueNetwork, TrainedValueNetwork
    from lean_mcts.benchmark.tasks import TASKS, HARD_TASKS, ALL_TASKS, DEEP_MULTISTEP_TASKS, get_task, get_tasks
    from lean_mcts.eval.oracle import verify, warm_verify, daemon_warm_verify, batch_daemon_warm_verify
    from lean_mcts.synth.grammar import PCFGPolicy
except ImportError:
    # Allow running from lean_mcts/ directory
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from env.dojo_wrapper import LeanEnv
    from env.pantograph_wrapper import PantographEnv
    from env.pantograph_client import PantographClient, DaemonPantographEnv, is_daemon_running
    from search.mcts import search
    from search.policy import FixedPolicy, ExpandedFixedPolicy, PromptedPolicy, TrainedPolicy, SynthesisPolicy, GoalConditionedPolicy, HybridGoalConditionedPolicy, CompositionalPolicy, HybridCompositionalPolicy, OllamaPolicy, HybridOllamaPolicy
    from search.value import StubValueNetwork, TrainedValueNetwork
    from benchmark.tasks import TASKS, HARD_TASKS, ALL_TASKS, DEEP_MULTISTEP_TASKS, get_task, get_tasks
    from eval.oracle import verify, warm_verify, daemon_warm_verify, batch_daemon_warm_verify
    from synth.grammar import PCFGPolicy


LEAN_PROJECT = Path(__file__).parent.parent / "env" / "FormalNav"


def make_oracle(task, verbose: bool = False):
    """Create a cached oracle closure for a task (cold — subprocess per call)."""
    cache = {}

    def oracle_fn(tactics: list[str]) -> bool:
        key = tuple(tactics)
        if key in cache:
            return cache[key]
        result = verify(task, tactics, LEAN_PROJECT, verbose=verbose)
        cache[key] = result.passed
        return result.passed

    return oracle_fn


def make_warm_oracle(task, env, verbose: bool = False):
    """Create a cached oracle closure using warm Pantograph server (~5-16x faster).

    Uses check_compile with #eval instead of spawning `lake env lean` subprocesses.
    The server already has Mathlib loaded, so each call is ~130-375ms vs ~2100ms.
    """
    cache = {}

    def oracle_fn(tactics: list[str]) -> bool:
        key = tuple(tactics)
        if key in cache:
            return cache[key]
        result = warm_verify(
            task, tactics, env._server, env._loop, verbose=verbose,
        )
        cache[key] = result.passed
        return result.passed

    # Signal to _batch_oracle_verify: don't use ThreadPoolExecutor
    oracle_fn._sequential = True
    return oracle_fn


def make_daemon_warm_oracle(task, client: PantographClient, verbose: bool = False):
    """Create a cached oracle closure using the daemon's Pantograph server.

    Same speed as warm oracle (~130-375ms/call) but goes through the daemon
    socket instead of holding a direct server reference.
    """
    cache = {}

    def oracle_fn(tactics: list[str]) -> bool:
        key = tuple(tactics)
        if key in cache:
            return cache[key]
        result = daemon_warm_verify(task, tactics, client, verbose=verbose)
        cache[key] = result.passed
        return result.passed

    def batch_fn(tactics_list: list[list[str]]) -> list[bool]:
        """Verify multiple candidates in a single check_compile call."""
        # Check cache for each, only verify uncached ones
        results = [None] * len(tactics_list)
        to_verify = []  # (index, tactics)
        for i, tactics in enumerate(tactics_list):
            key = tuple(tactics)
            if key in cache:
                results[i] = cache[key]
            else:
                to_verify.append((i, tactics))

        if to_verify:
            batch_tactics = [t for _, t in to_verify]
            batch_results = batch_daemon_warm_verify(
                task, batch_tactics, client, verbose=verbose,
            )
            for (orig_idx, tactics), oracle_result in zip(to_verify, batch_results):
                cache[tuple(tactics)] = oracle_result.passed
                results[orig_idx] = oracle_result.passed

        return results

    # Signal to _batch_oracle_verify: don't use ThreadPoolExecutor
    oracle_fn._sequential = True
    # Attach batch function for batched oracle path
    oracle_fn._batch_fn = batch_fn
    return oracle_fn


def run_task(
    task, rollouts: int, policy, value_fn=None,
    use_oracle: bool = True, use_puct: bool = False,
    value_weight: float = 0.0, max_expand_k: int = 0,
    verbose: bool = False, backend: str = "pantograph",
    warm_oracle: bool = False, daemon_client: PantographClient = None,
    oracle_early_exit: bool = True,
    patience: int | None = None,
) -> dict:
    """Run MCTS on a single task. Returns result dict."""
    if verbose:
        print(f"\n{'='*60}")
        print(f"Task: {task.name} ({task.description})")
        mode = "daemon" if daemon_client else backend
        print(f"Rollouts: {rollouts}, Oracle: {'ON' if use_oracle else 'OFF'}, PUCT: {'ON' if use_puct else 'OFF'}, Backend: {mode}")

    if daemon_client is not None:
        env = DaemonPantographEnv(
            client=daemon_client,
            repo_path=str(LEAN_PROJECT),
            file_path=task.lean_file,
            theorem_name=task.theorem_name,
        )
    elif backend == "pantograph":
        env = PantographEnv(
            repo_path=str(LEAN_PROJECT),
            file_path=task.lean_file,
            theorem_name=task.theorem_name,
        )
    else:
        env = LeanEnv(
            repo_path=str(LEAN_PROJECT),
            file_path=task.lean_file,
            theorem_name=task.theorem_name,
        )

    if use_oracle and daemon_client is not None:
        oracle_fn = make_daemon_warm_oracle(task, daemon_client, verbose=verbose)
    elif use_oracle and warm_oracle and backend == "pantograph":
        oracle_fn = make_warm_oracle(task, env, verbose=verbose)
    elif use_oracle:
        oracle_fn = make_oracle(task, verbose=verbose)
    else:
        oracle_fn = None

    # Pre-compute compositional candidates if policy supports it
    if hasattr(policy, 'set_task'):
        policy.set_task(task)

    t0 = time.time()
    try:
        result = search(
            env=env,
            n_rollouts=rollouts,
            policy_fn=policy,
            value_fn=value_fn,
            oracle_fn=oracle_fn,
            use_puct=use_puct,
            value_weight=value_weight,
            max_expand_k=max_expand_k,
            oracle_early_exit=oracle_early_exit,
            patience=patience,
            verbose=verbose,
        )
    except (EOFError, OSError, RuntimeError) as e:
        # REPL crashed — record as failure, don't abort entire benchmark
        print(f"  REPL crashed on {task.name}: {e}")
        result = {
            "solved": False,
            "tactics": [],
            "rollouts_used": rollouts,
            "tree_size": 0,
            "error": str(e),
        }
    elapsed = time.time() - t0

    try:
        env.close()
    except Exception:
        pass  # REPL may already be dead

    result["task"] = task.name
    result["category"] = task.category
    result["ghc_equivalent"] = task.ghc_equivalent
    result["wall_time_s"] = round(elapsed, 2)

    if verbose:
        status = "SOLVED" if result["solved"] else "FAILED"
        print(f"  Result: {status}")
        print(f"  Tactics: {result['tactics']}")
        print(f"  Rollouts used: {result['rollouts_used']}")
        print(f"  Tree size: {result['tree_size']}")
        print(f"  Wall time: {elapsed:.2f}s")
        if "stats" in result and result["stats"]:
            s = result["stats"]
            print(f"  --- Timing breakdown ---")
            print(f"  REPL step():   {s['t_step_s']:.2f}s ({s['n_tactic_steps']} calls, {s['ms_per_step']:.1f}ms/call) [{s['pct_step']}%]")
            print(f"  Policy:        {s['t_policy_s']:.2f}s [{s['pct_policy']}%]")
            print(f"  Oracle:        {s['t_oracle_s']:.2f}s ({s['n_oracle_calls']} calls, {s['ms_per_oracle']:.0f}ms/call) [{s['pct_oracle']}%]")
            print(f"  Simulation:    {s['t_simulate_s']:.2f}s ({s['n_simulate_calls']} sims) [{s['pct_simulate']}%]")
            print(f"  Select+BP:     {s['t_select_s']:.4f}s")
            print(f"  Valid/Solved:  {s['n_tactic_valid']}/{s['n_tactic_solved']}  REPL crashes: {s['n_repl_crashes']}")

    return result


def run_task_tiered(
    task, policy, value_fn=None,
    use_oracle: bool = True, use_puct: bool = False,
    value_weight: float = 0.0, max_expand_k: int = 0,
    verbose: bool = False, backend: str = "pantograph",
    warm_oracle: bool = False, daemon_client: PantographClient = None,
    oracle_early_exit: bool = True,
    max_budget: int = 500,
    tiers: list[int] = None,
    patience: int | None = None,
) -> dict:
    """Escalating rollout budget: solve fast or invest more.

    Tries [50, 200, max_budget] by default. Returns result from the
    first tier that solves the task, or the last tier's result if unsolved.
    """
    if tiers is None:
        tiers = [50, 200, max_budget]
    # Ensure max_budget is in the list
    if tiers[-1] != max_budget:
        tiers.append(max_budget)
    # Remove tiers larger than max_budget
    tiers = [t for t in tiers if t <= max_budget]
    if not tiers:
        tiers = [max_budget]

    total_rollouts = 0
    for budget in tiers:
        result = run_task(
            task, budget, policy, value_fn, use_oracle, use_puct,
            value_weight, max_expand_k, verbose, backend,
            warm_oracle, daemon_client, oracle_early_exit,
            patience=patience,
        )
        total_rollouts += result.get("rollouts_used", budget)
        result["tier_budget"] = budget
        result["total_rollouts_tiered"] = total_rollouts
        if result["solved"]:
            return result

    return result


def run_all(
    tasks, rollouts: int, policy, value_fn=None,
    use_oracle: bool = True, use_puct: bool = False,
    value_weight: float = 0.0, max_expand_k: int = 0,
    verbose: bool = False, parallel: int = 1,
    backend: str = "pantograph", warm_oracle: bool = False,
    daemon_client: PantographClient = None,
    oracle_early_exit: bool = True,
    tiered: bool = False,
    patience: int | None = None,
) -> list[dict]:
    """Run MCTS on all given tasks. Returns list of result dicts."""
    if parallel <= 1:
        results = []
        for task in tasks:
            if tiered:
                result = run_task_tiered(
                    task, policy, value_fn, use_oracle, use_puct,
                    value_weight, max_expand_k, verbose, backend,
                    warm_oracle, daemon_client, oracle_early_exit,
                    max_budget=rollouts,
                    patience=patience,
                )
            else:
                result = run_task(task, rollouts, policy, value_fn, use_oracle, use_puct, value_weight, max_expand_k, verbose, backend, warm_oracle, daemon_client, oracle_early_exit, patience=patience)
            results.append(result)
            status = "SOLVED" if result["solved"] else "FAILED"
            tier_info = f" (tier {result.get('tier_budget', rollouts)})" if tiered and result["solved"] else ""
            print(f"  {task.name}: {status}{tier_info}", flush=True)

            # Incremental results: append to JSONL for monitoring
            import json as _json
            _progress_path = f"/tmp/benchmark_progress.jsonl"
            try:
                with open(_progress_path, "a") as _pf:
                    _pf.write(_json.dumps({
                        "task": task.name, "category": task.category,
                        "solved": result["solved"],
                        "rollouts": result.get("rollouts_used", rollouts),
                        "wall_time": result.get("wall_time_s", 0),
                    }) + "\n")
                    _pf.flush()
            except Exception:
                pass
        return results

    # Parallel execution: each task gets its own REPL + oracle
    # Note: daemon mode is sequential (single pantograph-repl), so no parallel with daemon
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _run(task):
        if tiered:
            result = run_task_tiered(
                task, policy, value_fn, use_oracle, use_puct,
                value_weight, max_expand_k, verbose, backend,
                warm_oracle, daemon_client, oracle_early_exit,
                max_budget=rollouts,
                patience=patience,
            )
        else:
            result = run_task(task, rollouts, policy, value_fn, use_oracle, use_puct, value_weight, max_expand_k, verbose, backend, warm_oracle, daemon_client, oracle_early_exit, patience=patience)
        status = "SOLVED" if result["solved"] else "FAILED"
        print(f"  {task.name}: {status}")
        return task.name, result

    results_dict = {}
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(_run, t): t for t in tasks}
        for future in as_completed(futures):
            name, result = future.result()
            results_dict[name] = result

    # Preserve task order
    return [results_dict[t.name] for t in tasks]


def summarize(all_runs: list[list[dict]], tasks):
    """Print summary across multiple runs."""
    task_names = [t.name for t in tasks]
    n_tasks = len(tasks)
    t2_tasks = [t for t in tasks if t in TASKS]
    hard_tasks_list = [t for t in tasks if t in HARD_TASKS]

    print(f"\n{'='*70}")
    print(f"SUMMARY ({len(all_runs)} runs, {n_tasks} tasks)")
    print(f"{'='*70}")

    # Per-task results
    print(f"\n{'Task':<25} {'Category':<15} {'Solved':<10} {'Avg Rollouts':<15}")
    print("-" * 70)

    for task_name in task_names:
        task = get_task(task_name)
        solved_count = sum(
            1 for run in all_runs
            for r in run if r["task"] == task_name and r["solved"]
        )
        avg_rollouts = 0
        solved_results = [
            r for run in all_runs
            for r in run if r["task"] == task_name and r["solved"]
        ]
        if solved_results:
            avg_rollouts = sum(r["rollouts_used"] for r in solved_results) / len(solved_results)

        total_runs = len(all_runs)
        solved_str = f"{solved_count}/{total_runs}"
        rollout_str = f"{avg_rollouts:.0f}" if solved_results else "—"
        print(f"{task_name:<25} {task.category:<15} {solved_str:<10} {rollout_str:<15}")

    # Aggregate
    total_solved_per_run = []
    for run_results in all_runs:
        total_solved_per_run.append(sum(1 for r in run_results if r["solved"]))

    # T2 vs Hard breakdown
    t2_solved_per_run = []
    hard_solved_per_run = []
    for run_results in all_runs:
        t2_names = {t.name for t in t2_tasks}
        hard_names = {t.name for t in hard_tasks_list}
        t2_solved_per_run.append(sum(1 for r in run_results if r["task"] in t2_names and r["solved"]))
        hard_solved_per_run.append(sum(1 for r in run_results if r["task"] in hard_names and r["solved"]))

    print(f"\nTotal solved per run: {total_solved_per_run}")
    if t2_tasks:
        print(f"  T2 tasks:   {t2_solved_per_run} (/{len(t2_tasks)})")
    if hard_tasks_list:
        print(f"  Hard tasks: {hard_solved_per_run} (/{len(hard_tasks_list)})")

    if len(total_solved_per_run) > 1:
        lo, hi = min(total_solved_per_run), max(total_solved_per_run)
        avg = sum(total_solved_per_run) / len(total_solved_per_run)
        print(f"Range: {lo}-{hi}/{n_tasks}, Mean: {avg:.1f}/{n_tasks}")

        # Union / Intersection analysis
        per_task_solves = {}
        for task_name in task_names:
            per_task_solves[task_name] = sum(
                1 for run in all_runs
                for r in run if r["task"] == task_name and r["solved"]
            )

        n_runs = len(all_runs)
        always_solved = [t for t, c in per_task_solves.items() if c == n_runs]
        sometimes_solved = [t for t, c in per_task_solves.items() if 0 < c < n_runs]
        never_solved = [t for t, c in per_task_solves.items() if c == 0]
        union_count = len(always_solved) + len(sometimes_solved)

        print(f"\n  Intersection (solved every run):  {len(always_solved)}/{n_tasks}")
        print(f"  Union (solved at least once):     {union_count}/{n_tasks}")
        print(f"  Never solved:                     {len(never_solved)}/{n_tasks}")

        if sometimes_solved:
            print(f"\n  Stochastic tasks (solved {{}}<N/{n_runs} runs):")
            for t in sometimes_solved:
                print(f"    {t}: {per_task_solves[t]}/{n_runs}")

        if never_solved:
            print(f"\n  Hard failures (never solved):")
            for t in never_solved:
                print(f"    {t}")

    print(f"GHC Core T2 comparison: 0-4/10 (Sprint 10, autoregressive)")


def main():
    parser = argparse.ArgumentParser(description="Lean MCTS benchmark runner")
    parser.add_argument("--rollouts", type=int, default=500, help="MCTS rollouts per task")
    parser.add_argument("--runs", type=int, default=1, help="Number of runs per task")
    parser.add_argument("--task", type=str, default=None, help="Run a single task by name")
    parser.add_argument("--tier", choices=["t2", "hard", "v2", "recurrence", "fundamentals", "multistep", "deep_multistep", "compositional", "recursive_r1", "recursive_r2", "recursive_r3", "recursive", "stretch_s1", "stretch_s2", "stretch_s3", "stretch_s4", "stretch", "v3", "v3_stretch", "v3_all", "diag_a", "diag_b", "diag_c", "diag_d", "diag_all", "diag_holdout", "hard_holdout", "held_out_12", "all", "all+v2", "all+v2+recurrence", "all+v2+recurrence+fundamentals", "generated", "all+generated"],
                        default="all", help="Which task tier to run")
    parser.add_argument("--policy", choices=["fixed", "expanded-fixed", "prompted", "trained", "synthesis", "pcfg", "goal-conditioned", "hybrid-gc", "compositional", "hybrid-comp", "ollama", "hybrid-ollama"],
                        default="fixed", help="Policy network type")
    parser.add_argument("--ollama-url", type=str, default=os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat"),
                        help="Ollama /api/chat endpoint URL (ollama or hybrid-ollama policy); "
                             "defaults to $OLLAMA_URL or localhost")
    parser.add_argument("--ollama-model", type=str, default="gemma4:26b",
                        help="Ollama model name (ollama or hybrid-ollama policy)")
    parser.add_argument("--ollama-k", type=int, default=16,
                        help="Candidates requested per Ollama call (default 16)")
    parser.add_argument("--ollama-temperature", type=float, default=0.3,
                        help="Sampling temperature for Ollama policy (default 0.3)")
    parser.add_argument("--expressions", type=str, default=None,
                        help="Path to expressions.jsonl for expanded-fixed policy")
    parser.add_argument("--model", type=str, default="deepseek-ai/deepseek-coder-1.3b-instruct",
                        help="Model name for prompted policy")
    parser.add_argument("--base-model", type=str, default="Qwen/Qwen2.5-Coder-1.5B",
                        help="Base model for trained policy")
    parser.add_argument("--adapter-path", type=str, default=None,
                        help="LoRA adapter path for trained policy")
    parser.add_argument("--value-model", type=str, default=None,
                        help="Value head checkpoint path")
    parser.add_argument("--value-weight", type=float, default=0.8,
                        help="Weight for value network (0=rollout only, 1=value only)")
    parser.add_argument("--max-expand-k", type=int, default=0,
                        help="Cap tactics per MCTS expansion (0=unlimited, 100 recommended for hard tasks)")
    parser.add_argument("--no-oracle", action="store_true",
                        help="Disable oracle (type-check only, no test verification)")
    parser.add_argument("--warm-oracle", action="store_true",
                        help="Use warm oracle via Pantograph check_compile (~375ms/call, no subprocesses). "
                             "Default: cold parallel oracle (~320ms amortized with ThreadPool)")
    parser.add_argument("--parallel", type=int, default=1,
                        help="Number of tasks to run concurrently (default: 1)")
    parser.add_argument("--backend", choices=["pantograph", "dojo"], default="pantograph",
                        help="Lean interaction backend (default: pantograph)")
    parser.add_argument("--gc-adapter", type=str, default=None,
                        help="Goal-conditioned LoRA adapter path (for goal-conditioned/hybrid-gc policies)")
    parser.add_argument("--novelty-report", action="store_true",
                        help="Report which solved tasks used novel tactics (not in ExpandedFixed)")
    parser.add_argument("--daemon", action="store_true", default=None,
                        help="Use persistent Pantograph daemon (auto-detected if socket exists)")
    parser.add_argument("--no-daemon", action="store_true",
                        help="Disable daemon mode (use per-task Pantograph)")
    parser.add_argument("--no-early-exit", action="store_true",
                        help="Disable oracle early-exit: verify ALL candidates (for self-play harvesting)")
    parser.add_argument("--tiered", action="store_true",
                        help="Tiered inference: try 50, 200, then max rollouts (saves compute on easy tasks)")
    parser.add_argument("--patience", type=int, default=None,
                        help="S29 Phase 0.2 early-abort: break out of MCTS after N rollouts without a solve. "
                             "Solvable tasks are unaffected (they early-exit on solve). "
                             "Recommended: 100 for benchmark sweeps. Default None = no cap.")
    parser.add_argument("--seed", type=int, default=None,
                        help="Base random seed for reproducible runs (each run uses seed + run_idx)")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--output-dir", type=str, default="benchmark/results")
    args = parser.parse_args()

    # Initialize policy
    if args.policy == "fixed":
        policy = FixedPolicy()
        use_puct = False
    elif args.policy == "expanded-fixed":
        policy = ExpandedFixedPolicy(expressions_path=args.expressions)
        use_puct = False
    elif args.policy == "prompted":
        policy = PromptedPolicy(model_name=args.model)
        use_puct = False
    elif args.policy == "trained":
        if not args.adapter_path:
            parser.error("--adapter-path required for trained policy")
        policy = TrainedPolicy(args.base_model, args.adapter_path)
        use_puct = True
    elif args.policy == "synthesis":
        adapter = args.adapter_path or str(Path(__file__).parent.parent / "train" / "synth_adapter")
        policy = SynthesisPolicy(args.base_model, adapter)
        use_puct = True
    elif args.policy == "pcfg":
        grammar_path = args.expressions  # reuse --expressions for grammar.json path
        policy = PCFGPolicy(grammar_path=grammar_path)
        use_puct = False
    elif args.policy == "goal-conditioned":
        gc_adapter = args.gc_adapter or str(Path(__file__).parent.parent / "train" / "goal_cond_adapter")
        policy = GoalConditionedPolicy(args.base_model, gc_adapter)
        use_puct = True
    elif args.policy == "hybrid-gc":
        gc_adapter = args.gc_adapter or str(Path(__file__).parent.parent / "train" / "goal_cond_adapter")
        policy = HybridGoalConditionedPolicy(
            args.base_model, gc_adapter, expressions_path=args.expressions,
        )
        use_puct = True
    elif args.policy == "compositional":
        # deferred — needs daemon_client for on-the-fly evaluation
        policy = None
        use_puct = False
    elif args.policy == "hybrid-comp":
        gc_adapter = args.gc_adapter or str(Path(__file__).parent.parent / "train" / "goal_cond_adapter")
        # deferred — needs daemon_client for on-the-fly evaluation
        policy = None
        use_puct = True
    elif args.policy == "ollama":
        policy = OllamaPolicy(
            url=args.ollama_url,
            model=args.ollama_model,
            k_default=args.ollama_k,
            temperature=args.ollama_temperature,
        )
        use_puct = True
    elif args.policy == "hybrid-ollama":
        policy = HybridOllamaPolicy(
            url=args.ollama_url,
            model=args.ollama_model,
            expressions_path=args.expressions,
            k_default=args.ollama_k,
            temperature=args.ollama_temperature,
        )
        use_puct = True
    else:
        raise ValueError(f"Unknown policy: {args.policy}")

    # Initialize value network
    value_fn = None
    value_weight = 0.0
    if args.value_model:
        value_fn = TrainedValueNetwork(args.base_model, args.value_model)
        value_weight = args.value_weight

    use_oracle = not args.no_oracle

    # Daemon mode: auto-detect if not explicitly set
    daemon_client = None
    use_daemon = False
    if args.no_daemon:
        use_daemon = False
    elif args.daemon:
        use_daemon = True
    else:
        # Auto-detect: use daemon if socket exists and responds
        use_daemon = is_daemon_running()

    if use_daemon:
        daemon_client = PantographClient()
        if not daemon_client.ping():
            print("WARNING: --daemon requested but daemon not responding. Falling back to per-task mode.")
            daemon_client.close()
            daemon_client = None
        else:
            print("Using persistent Pantograph daemon")

    # Deferred policy init (needs daemon_client)
    if args.policy == "compositional" and policy is None:
        policy = CompositionalPolicy(
            expressions_path=args.expressions,
            daemon_client=daemon_client,
        )
    elif args.policy == "hybrid-comp" and policy is None:
        gc_adapter = args.gc_adapter or str(Path(__file__).parent.parent / "train" / "goal_cond_adapter")
        policy = HybridCompositionalPolicy(
            args.base_model, gc_adapter,
            expressions_path=args.expressions,
            daemon_client=daemon_client,
        )

    # Select tasks
    if args.task:
        tasks = [get_task(args.task)]
    else:
        tasks = get_tasks(args.tier if args.tier != "all" else None)

    oracle_mode = "DAEMON-WARM" if daemon_client else "WARM" if args.warm_oracle else "ON" if use_oracle else "OFF"
    backend_mode = "daemon" if daemon_client else args.backend
    tiered_str = f", Tiered: [50,200,{args.rollouts}]" if args.tiered else ""
    print(f"Policy: {args.policy}, Tasks: {len(tasks)}, Rollouts: {args.rollouts}, Runs: {args.runs}{tiered_str}")
    print(f"PUCT: {'ON' if use_puct else 'OFF'}, Value: {'ON' if value_fn else 'OFF'}, Oracle: {oracle_mode}, Backend: {backend_mode}")

    if args.seed is not None:
        print(f"Seed: {args.seed} (each run uses seed + run_idx)")

    # Build the ExpandedFixed catalog once for ablation instrumentation (S29 Phase 0.1).
    # Every benchmark result gets annotated with `policy_name` + `ef_catalog_match` so
    # downstream `ablation_columns.py` can join runs across policies and compute
    # ef_solved / lora_only_solved / hybrid_solved / lora_contribution columns.
    ef_catalog_set = set(ExpandedFixedPolicy(expressions_path=args.expressions).all_tactics)

    # Run
    all_runs = []
    for run_idx in range(args.runs):
        if args.runs > 1:
            print(f"\n--- Run {run_idx + 1}/{args.runs} ---")

        # Seed each run for reproducibility
        if args.seed is not None:
            import random
            import torch
            run_seed = args.seed + run_idx
            random.seed(run_seed)
            torch.manual_seed(run_seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(run_seed)

        oracle_early_exit = not args.no_early_exit
        results = run_all(tasks, args.rollouts, policy, value_fn, use_oracle, use_puct, value_weight, args.max_expand_k, args.verbose, args.parallel, args.backend, args.warm_oracle, daemon_client, oracle_early_exit, tiered=args.tiered, patience=args.patience)

        # Annotate every result with ablation provenance fields (S29 Phase 0.1).
        #   policy_name:       which policy produced this run (joinable across per-policy runs)
        #   ef_catalog_match:  True if every winning tactic is in the EF catalog (solve
        #                      is template-only; no LoRA contribution). None for unsolved.
        for r in results:
            r["policy_name"] = args.policy
            if r.get("solved"):
                winning = r.get("tactics") or []
                r["ef_catalog_match"] = bool(winning) and all(t in ef_catalog_set for t in winning)
            else:
                r["ef_catalog_match"] = None

        all_runs.append(results)

        # Save per-run results. Filename embeds policy so ablation_columns.py can
        # identify which file corresponds to which run without parsing metadata.
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"run_{args.policy}_{timestamp}_{run_idx}.json"
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2)

    # Summary
    summarize(all_runs, tasks)

    # Novelty report: identify tactics not in ExpandedFixed's enumeration
    if args.novelty_report:
        print(f"\n{'='*70}")
        print("NOVELTY REPORT")
        print(f"{'='*70}")
        ref_policy = ExpandedFixedPolicy(expressions_path=args.expressions)
        known_tactics = set(ref_policy.all_tactics)
        novel_found = False
        for run_results in all_runs:
            for r in run_results:
                if not r["solved"]:
                    continue
                novel_tactics = [t for t in r["tactics"] if t not in known_tactics]
                if novel_tactics:
                    novel_found = True
                    print(f"  {r['task']}: NOVEL tactics:")
                    for t in novel_tactics:
                        print(f"    → {t}")
                else:
                    print(f"  {r['task']}: solved with known tactics")
        if not novel_found:
            print("  No novel tactics found in solved tasks.")

    # Clean up daemon client
    if daemon_client is not None:
        daemon_client.close()


if __name__ == "__main__":
    main()
