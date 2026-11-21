"""Phase 0 diagnostic: capture real sub-goal states for multi-step tactics.

Connects to the Pantograph daemon and steps through `constructor` on product-type
goals to capture the exact sub-goal state strings. Also tests full multi-step
solving (constructor → exact on each sub-goal).

Usage:
    python lean_mcts/scripts/test_multistep.py
    python lean_mcts/scripts/test_multistep.py --verbose-mcts  # also run MCTS diagnostic
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure lean_mcts is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from lean_mcts.env.pantograph_client import PantographClient, is_daemon_running


def test_constructor_subgoals(client: PantographClient):
    """Step through constructor on partitionAndCount and capture sub-goal states."""
    print("=" * 70)
    print("TEST 1: constructor sub-goal states for partitionAndCount")
    print("=" * 70)

    # Reset to partitionAndCount (Nat × Nat from List Int)
    state = client.reset("partitionAndCount", "FormalNav/TasksV2.lean")
    print(f"\nInitial state (after intro):")
    print(f"  repr: {repr(str(state))}")
    print(f"  str:\n{str(state)}")

    # Step with constructor
    result = client.step(state, "constructor")
    if result.error:
        print(f"\n  ERROR: constructor failed: {result.error}")
        return None, None
    if result.solved:
        print(f"\n  UNEXPECTED: constructor solved the goal?!")
        return None, None

    # Capture sub-goal states
    subgoal_state = result.state
    print(f"\nAfter constructor:")
    print(f"  repr: {repr(str(subgoal_state))}")
    print(f"  str:\n{str(subgoal_state)}")
    print(f"  state_id: {subgoal_state.state_id}")

    return state, subgoal_state


def test_full_multistep_solve(client: PantographClient):
    """Attempt to solve partitionAndCount via constructor + two exact tactics."""
    print("\n" + "=" * 70)
    print("TEST 2: full multi-step solve of partitionAndCount")
    print("=" * 70)

    state = client.reset("partitionAndCount", "FormalNav/TasksV2.lean")
    print(f"\nInitial: {repr(str(state))}")

    # Step 1: constructor
    result = client.step(state, "constructor")
    if result.error:
        print(f"  constructor FAILED: {result.error}")
        return
    print(f"After constructor: {repr(str(result.state))}")

    # Step 2: exact on first sub-goal (positive count)
    sub_state = result.state
    tactic_fst = "exact (xs.filter (fun x => decide (x > 0))).length"
    result2 = client.step(sub_state, tactic_fst)
    if result2.error:
        print(f"  first sub-goal FAILED: {result2.error}")
        # Try alternative
        tactic_fst_alt = "exact (xs.filter (fun x => x > 0)).length"
        result2 = client.step(sub_state, tactic_fst_alt)
        if result2.error:
            print(f"  first sub-goal ALT FAILED: {result2.error}")
            return
        else:
            print(f"  first sub-goal ALT worked: {tactic_fst_alt}")
    if result2.solved:
        print(f"  SOLVED after first sub-goal! (unexpected for 2-goal)")
        return
    print(f"After first sub-goal: {repr(str(result2.state))}")

    # Step 3: exact on second sub-goal (non-positive count)
    sub_state2 = result2.state
    tactic_snd = "exact (xs.filter (fun x => decide (x ≤ 0))).length"
    result3 = client.step(sub_state2, tactic_snd)
    if result3.error:
        print(f"  second sub-goal FAILED: {result3.error}")
        tactic_snd_alt = "exact (xs.filter (fun x => decide (x <= 0))).length"
        result3 = client.step(sub_state2, tactic_snd_alt)
        if result3.error:
            print(f"  second sub-goal ALT FAILED: {result3.error}")
            return
        else:
            print(f"  second sub-goal ALT worked: {tactic_snd_alt}")
    if result3.solved:
        print(f"\n  SOLVED! Full tactic sequence:")
        print(f"    1. constructor")
        print(f"    2. {tactic_fst}")
        print(f"    3. {tactic_snd}")
    else:
        print(f"  NOT SOLVED after second sub-goal")
        print(f"  Remaining state: {repr(str(result3.state))}")


def test_have_binding(client: PantographClient):
    """Test have-binding pattern."""
    print("\n" + "=" * 70)
    print("TEST 3: have-binding pattern")
    print("=" * 70)

    state = client.reset("partitionAndCount", "FormalNav/TasksV2.lean")
    print(f"\nInitial: {repr(str(state))}")

    # Try a have binding
    have_tactic = "have h := xs.filter (fun x => decide (x > 0))"
    result = client.step(state, have_tactic)
    if result.error:
        print(f"  have FAILED: {result.error}")
        # Try without decide
        have_tactic2 = "have h := xs.filter (· > 0)"
        result = client.step(state, have_tactic2)
        if result.error:
            print(f"  have ALT FAILED: {result.error}")
            return
        else:
            print(f"  have ALT worked: {have_tactic2}")
    else:
        print(f"  have worked: {have_tactic}")

    if result.state:
        print(f"\nAfter have:")
        print(f"  repr: {repr(str(result.state))}")
        print(f"  str:\n{str(result.state)}")


def test_other_product_tasks(client: PantographClient):
    """Test constructor on other product-type tasks."""
    print("\n" + "=" * 70)
    print("TEST 4: constructor on other product-type tasks")
    print("=" * 70)

    product_tasks = [
        ("runningParts", "FormalNav/TasksRecurrence.lean", "Int × Nat"),
    ]

    for theorem, lean_file, expected_type in product_tasks:
        print(f"\n--- {theorem} ({expected_type}) ---")
        try:
            state = client.reset(theorem, lean_file)
            print(f"  Initial: {repr(str(state))}")

            result = client.step(state, "constructor")
            if result.error:
                print(f"  constructor FAILED: {result.error}")
            elif result.state:
                print(f"  After constructor: {repr(str(result.state))}")
        except Exception as e:
            print(f"  ERROR: {e}")


def run_mcts_diagnostic(client: PantographClient):
    """Run verbose MCTS on partition_and_count to check constructor exploration."""
    print("\n" + "=" * 70)
    print("TEST 5: MCTS verbose on partition_and_count (200 rollouts)")
    print("=" * 70)

    from lean_mcts.benchmark.tasks import get_task
    from lean_mcts.benchmark.run import make_daemon_warm_oracle, run_task
    from lean_mcts.search.policy import HybridGoalConditionedPolicy
    from lean_mcts.env.pantograph_client import DaemonPantographEnv
    from lean_mcts.search.mcts import search

    task = get_task("partition_and_count")
    gc_adapter = str(Path(__file__).resolve().parent.parent / "train" / "goal_cond_adapter")

    print(f"Loading HybridGC policy from {gc_adapter}...")
    policy = HybridGoalConditionedPolicy(
        "Qwen/Qwen2.5-Coder-1.5B", gc_adapter,
    )

    env = DaemonPantographEnv(
        client,
        repo_path=str(Path(__file__).resolve().parent.parent / "env" / "FormalNav"),
        file_path=task.lean_file,
        theorem_name=task.theorem_name,
    )

    oracle_fn = make_daemon_warm_oracle(task, client)

    result = search(
        env=env,
        n_rollouts=200,
        policy_fn=policy,
        oracle_fn=oracle_fn,
        use_puct=True,
        max_expand_k=0,
        verbose=True,
    )

    print(f"\nResult: solved={result['solved']}, tactics={result['tactics']}")
    print(f"  Rollouts used: {result['rollouts_used']}, Tree size: {result['tree_size']}")
    if result.get("stats"):
        print(f"  Stats: {result['stats']}")


def main():
    parser = argparse.ArgumentParser(description="Multi-step tactic diagnostic")
    parser.add_argument("--verbose-mcts", action="store_true",
                        help="Also run MCTS diagnostic on partition_and_count")
    args = parser.parse_args()

    if not is_daemon_running():
        print("ERROR: Pantograph daemon is not running.")
        print("Start it with: python -m lean_mcts.env.pantograph_daemon")
        sys.exit(1)

    client = PantographClient()
    client.connect()
    print("Connected to Pantograph daemon")

    try:
        # Test 1: Capture sub-goal states
        _, subgoal_state = test_constructor_subgoals(client)

        # Test 2: Full multi-step solve
        test_full_multistep_solve(client)

        # Test 3: Have binding
        test_have_binding(client)

        # Test 4: Other product tasks
        test_other_product_tasks(client)

        # Test 5: MCTS diagnostic (optional)
        if args.verbose_mcts:
            run_mcts_diagnostic(client)

    finally:
        client.close()

    print("\n" + "=" * 70)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
