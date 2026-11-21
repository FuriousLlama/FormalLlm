"""
Sprint 21 Phase 0: Diagnostic audit.

For each unsolved recurrence + multistep task:
  1. Connect to daemon, get initial goal state
  2. Call HybridGC policy, dump top-20 candidates with log-probs
  3. Type-check each candidate (which ones are valid Lean tactics?)
  4. For multi-step tasks: try `constructor`, get sub-goal states, repeat

Usage:
    python -m lean_mcts.benchmark.diagnose_s21
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Setup imports
try:
    from lean_mcts.env.pantograph_client import PantographClient, DaemonPantographEnv, is_daemon_running
    from lean_mcts.search.policy import HybridGoalConditionedPolicy, GoalConditionedPolicy
    from lean_mcts.benchmark.tasks import get_task, RECURRENCE_TASKS, MULTISTEP_TASKS
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from env.pantograph_client import PantographClient, DaemonPantographEnv, is_daemon_running
    from search.policy import HybridGoalConditionedPolicy, GoalConditionedPolicy
    from benchmark.tasks import get_task, RECURRENCE_TASKS, MULTISTEP_TASKS

LEAN_PROJECT = Path(__file__).parent.parent / "env" / "FormalNav"

# Unsolved tasks to diagnose
RECURRENCE_UNSOLVED = ["fibonacci", "fib_list", "triangular_numbers", "powers_of_2"]
MULTISTEP_UNSOLVED = ["sum_and_product", "split_even_odd"]
# Include solved ones for comparison
RECURRENCE_SOLVED = ["factorial", "running_parts"]
MULTISTEP_SOLVED = ["count_pos_neg", "min_and_max", "length_and_sum"]


def dump_candidates(
    policy, state, label: str, top_k: int = 30, env=None,
):
    """Call policy on state, print top-k candidates with type-check results."""
    print(f"\n{'─'*70}")
    print(f"  {label}")
    state_str = state.pp if hasattr(state, "pp") else str(state)
    print(f"  State: {state_str[:200]}")
    print(f"{'─'*70}")

    t0 = time.perf_counter()
    candidates = policy(state)
    t_policy = time.perf_counter() - t0

    # Separate GC candidates (higher log-prob) from ExpandedFixed (low prior)
    if candidates:
        min_gc_lp = min(lp for _, lp in candidates[:64])  # GC candidates are first
        gc_count = sum(1 for _, lp in candidates if lp > min_gc_lp - 1.5)
        ef_count = len(candidates) - gc_count
    else:
        gc_count = ef_count = 0

    print(f"  Total candidates: {len(candidates)} (GC: ~{gc_count}, EF: ~{ef_count})")
    print(f"  Policy time: {t_policy:.2f}s")
    print()

    # Show top-k with type-check
    print(f"  {'#':<4} {'LP':>7} {'Valid':>6} {'Solved':>7}  Tactic")
    print(f"  {'─'*4} {'─'*7} {'─'*6} {'─'*7}  {'─'*50}")

    for i, (tactic, lp) in enumerate(candidates[:top_k]):
        valid_str = "?"
        solved_str = "?"
        if env is not None:
            try:
                result = env.step(state, tactic)
                if result.solved:
                    valid_str = "✓"
                    solved_str = "PROOF!"
                elif result.state is not None:
                    valid_str = "✓"
                    solved_str = "—"
                else:
                    valid_str = "✗"
                    solved_str = "—"
            except (EOFError, OSError):
                valid_str = "ERR"
                solved_str = "—"
        # Truncate long tactics
        tactic_display = tactic[:80] + ("..." if len(tactic) > 80 else "")
        print(f"  {i+1:<4} {lp:>7.2f} {valid_str:>6} {solved_str:>7}  {tactic_display}")

    # Count valid/solved in all candidates
    if env is not None:
        n_valid = 0
        n_solved = 0
        for tactic, lp in candidates:
            try:
                result = env.step(state, tactic)
                if result.solved:
                    n_solved += 1
                    n_valid += 1
                elif result.state is not None:
                    n_valid += 1
            except (EOFError, OSError):
                pass
        print(f"\n  Summary: {n_valid}/{len(candidates)} valid, {n_solved}/{len(candidates)} solved (all candidates)")

    return candidates


def diagnose_task(task_name: str, policy, client: PantographClient, check_subgoals: bool = False):
    """Full diagnostic for a single task."""
    task = get_task(task_name)
    print(f"\n{'='*70}")
    print(f"TASK: {task.name} — {task.description}")
    print(f"Return type: {task.return_type}, Input type: {task.input_type}")
    print(f"{'='*70}")

    env = DaemonPantographEnv(
        client=client,
        repo_path=str(LEAN_PROJECT),
        file_path=task.lean_file,
        theorem_name=task.theorem_name,
    )

    initial_state = env.reset()
    dump_candidates(policy, initial_state, f"Initial state for {task.name}", top_k=30, env=env)

    # For product-type tasks, try constructor and diagnose sub-goals
    if check_subgoals and "×" in task.return_type:
        print(f"\n  >>> Trying 'constructor' to get sub-goal states...")
        try:
            result = env.step(initial_state, "constructor")
            if result.state is not None and not result.solved:
                # After constructor, we get sub-goals. The state should contain them.
                # Check if env supports sub-goal iteration
                subgoal_state = result.state
                state_str = subgoal_state.pp if hasattr(subgoal_state, "pp") else str(subgoal_state)
                print(f"  Sub-goal state after constructor: {state_str[:300]}")

                # Dump candidates for the sub-goal
                dump_candidates(
                    policy, subgoal_state,
                    f"Sub-goal after constructor for {task.name}",
                    top_k=20, env=env,
                )
            elif result.solved:
                print(f"  constructor solved the task directly!")
            else:
                print(f"  constructor was rejected by Lean")
        except (EOFError, OSError) as e:
            print(f"  constructor step failed: {e}")

    try:
        env.close()
    except Exception:
        pass


def main():
    print("Sprint 21 Phase 0: Diagnostic Audit")
    print("="*70)

    # Check daemon
    if not is_daemon_running():
        print("ERROR: Pantograph daemon not running. Start with:")
        print("  python -m lean_mcts.env.pantograph_daemon --workers 0")
        sys.exit(1)

    client = PantographClient()
    if not client.ping():
        print("ERROR: Daemon not responding")
        sys.exit(1)
    print("Daemon: connected")

    # Load policy (same as benchmark --policy hybrid-gc)
    print("Loading HybridGC policy...")
    t0 = time.perf_counter()
    policy = HybridGoalConditionedPolicy(
        base_model="Qwen/Qwen2.5-Coder-1.5B",
        adapter_path=str(Path(__file__).parent.parent / "train" / "goal_cond_adapter"),
    )
    print(f"Policy loaded in {time.perf_counter() - t0:.1f}s")

    # ─── Recurrence tasks ───
    print("\n\n" + "█"*70)
    print("  RECURRENCE TASKS (unsolved)")
    print("█"*70)
    for name in RECURRENCE_UNSOLVED:
        diagnose_task(name, policy, client, check_subgoals=True)

    print("\n\n" + "█"*70)
    print("  RECURRENCE TASKS (solved — comparison)")
    print("█"*70)
    for name in RECURRENCE_SOLVED:
        diagnose_task(name, policy, client, check_subgoals=True)

    # ─── Multi-step tasks ───
    print("\n\n" + "█"*70)
    print("  MULTI-STEP TASKS (unsolved)")
    print("█"*70)
    for name in MULTISTEP_UNSOLVED:
        diagnose_task(name, policy, client, check_subgoals=True)

    print("\n\n" + "█"*70)
    print("  MULTI-STEP TASKS (solved — comparison)")
    print("█"*70)
    for name in MULTISTEP_SOLVED:
        diagnose_task(name, policy, client, check_subgoals=True)

    client.close()
    print("\n\nDiagnostic audit complete.")


if __name__ == "__main__":
    main()
