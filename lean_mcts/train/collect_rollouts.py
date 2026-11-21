"""Collect (state, outcome) pairs from MCTS trees for value network training.

Runs MCTS on benchmark tasks via the daemon, then extracts labeled
(state, outcome) pairs from each tree using visit-count-weighted MC estimates.

Usage:
    python -m lean_mcts.train.collect_rollouts --rollouts 200
    python -m lean_mcts.train.collect_rollouts --rollouts 200 --tiers all+v2+recurrence
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

LEAN_MCTS = Path(__file__).resolve().parent.parent


def collect_value_data(
    rollouts: int = 200,
    tiers: str = "all+v2+recurrence",
    seed: int | None = None,
) -> list[dict]:
    """Run MCTS on all tasks, extract tree-labeled value data."""
    sys.path.insert(0, str(LEAN_MCTS.parent))

    from lean_mcts.benchmark.tasks import get_tasks
    from lean_mcts.env.pantograph_client import (
        DaemonPantographEnv, PantographClient, is_daemon_running,
    )
    from lean_mcts.eval.oracle import batch_daemon_warm_verify
    from lean_mcts.search.mcts import search
    from lean_mcts.search.policy import HybridGoalConditionedPolicy

    LEAN_PROJECT = LEAN_MCTS / "env" / "FormalNav"

    if seed is not None:
        import random
        import torch
        random.seed(seed)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)

    # Policy
    gc_adapter = str(LEAN_MCTS / "train" / "goal_cond_adapter")
    policy = HybridGoalConditionedPolicy("Qwen/Qwen2.5-Coder-1.5B", gc_adapter)

    # Daemon
    if not is_daemon_running():
        print("ERROR: Pantograph daemon not running. Start with:")
        print("  python -m lean_mcts.env.pantograph_daemon --workers 0")
        sys.exit(1)

    client = PantographClient()
    if not client.ping():
        print("ERROR: Daemon not responding")
        sys.exit(1)
    print("Using Pantograph daemon")

    tasks = get_tasks(tiers if tiers != "all" else None)
    print(f"Tasks: {len(tasks)}, Rollouts: {rollouts}")

    all_labels = []

    for task in tasks:
        t0 = time.time()
        try:
            env = DaemonPantographEnv(
                client=client,
                repo_path=str(LEAN_PROJECT),
                file_path=task.lean_file,
                theorem_name=task.theorem_name,
            )

            # Build batched oracle
            cache = {}
            def _batch_fn(candidates):
                return batch_daemon_warm_verify(task, candidates, client)
            def oracle_fn(tactics):
                key = tuple(tactics)
                if key in cache:
                    return cache[key]
                result = _batch_fn([tactics])
                passed = result[0] if result else False
                cache[key] = passed
                return passed

            result = search(
                env, n_rollouts=rollouts,
                policy_fn=policy,
                oracle_fn=oracle_fn,
                use_puct=True,
                export_tree=True,
                max_expand_k=50,
            )

            tree_labels = result.get("tree_labels", [])
            for label in tree_labels:
                label["task"] = task.name
                label["solved"] = result["solved"]

            status = "SOLVED" if result["solved"] else "FAILED"
            elapsed = time.time() - t0
            print(f"  {task.name}: {status} — {len(tree_labels)} states, {elapsed:.1f}s")
            all_labels.extend(tree_labels)

        except Exception as e:
            print(f"  {task.name}: ERROR — {e}")
            continue

    return all_labels


def deduplicate_labels(labels: list[dict]) -> list[dict]:
    """Merge duplicate states by visit-weighted average outcome."""
    merged: dict[str, dict] = {}
    for entry in labels:
        state = entry["state"]
        if state in merged:
            existing = merged[state]
            # Weighted average by visits
            total_visits = existing["visits"] + entry["visits"]
            existing["outcome"] = (
                existing["outcome"] * existing["visits"] +
                entry["outcome"] * entry["visits"]
            ) / total_visits
            existing["visits"] = total_visits
            existing["outcome"] = round(existing["outcome"], 4)
            if entry.get("task") and entry["task"] not in existing.get("tasks", ""):
                existing["tasks"] = existing.get("tasks", "") + "," + entry["task"]
        else:
            merged[state] = {
                "state": state,
                "outcome": entry["outcome"],
                "visits": entry["visits"],
                "depth": entry.get("depth", 0),
                "tasks": entry.get("task", ""),
            }
    return list(merged.values())


def main():
    parser = argparse.ArgumentParser(description="Collect MCTS tree data for value training")
    parser.add_argument("--rollouts", type=int, default=200)
    parser.add_argument("--tiers", type=str, default="all+v2+recurrence")
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--merge-existing", type=str, default=None,
                        help="Merge with existing value_data.jsonl")
    args = parser.parse_args()

    output = Path(args.output) if args.output else LEAN_MCTS / "train" / "value_data_v2.jsonl"

    print(f"Collecting value data: rollouts={args.rollouts}, tiers={args.tiers}")
    t0 = time.time()
    raw_labels = collect_value_data(args.rollouts, args.tiers, args.seed)
    print(f"\nRaw: {len(raw_labels)} state-outcome pairs")

    # Deduplicate
    deduped = deduplicate_labels(raw_labels)
    print(f"After dedup: {len(deduped)} unique states")

    # Merge with existing synthetic data
    if args.merge_existing:
        existing_path = Path(args.merge_existing)
        if existing_path.exists():
            with open(existing_path) as f:
                for line in f:
                    entry = json.loads(line)
                    deduped.append({
                        "state": entry["state"],
                        "outcome": entry["outcome"],
                        "visits": 1,
                        "depth": 0,
                        "tasks": "synthetic",
                    })
            deduped = deduplicate_labels(deduped)
            print(f"After merge with existing: {len(deduped)} states")

    # Stats
    pos = sum(1 for d in deduped if d["outcome"] > 0.5)
    neg = sum(1 for d in deduped if d["outcome"] <= 0.5)
    print(f"Positive (>0.5): {pos}, Negative (<=0.5): {neg}")

    # Write
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        for entry in deduped:
            f.write(json.dumps(entry) + "\n")

    elapsed = time.time() - t0
    print(f"\nWritten to {output} ({len(deduped)} entries)")
    print(f"Elapsed: {elapsed / 60:.1f} min")


if __name__ == "__main__":
    main()
