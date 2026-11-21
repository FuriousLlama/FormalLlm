"""Self-play data collection from benchmark results.

Reads benchmark result JSONs, extracts oracle-verified (state, tactic) pairs,
and adds them to the goal-conditioned training data.

Usage:
    python -m synth.collect_selfplay_v2 --results-dir /tmp/selfplay_run
    python -m synth.collect_selfplay_v2 --results-dir /tmp/selfplay_run --retrain
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from lean_mcts.synth.collect_selfplay import TrainingPair
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from synth.collect_selfplay import TrainingPair

try:
    from lean_mcts.benchmark.tasks import get_task, ALL_TASKS, RECURRENCE_TASKS
except ImportError:
    from benchmark.tasks import get_task, ALL_TASKS, RECURRENCE_TASKS


def _build_goal_state(task) -> str:
    """Construct the initial goal state string from task metadata."""
    has_two_args = any("input2" in tc for tc in task.test_cases)
    if has_two_args:
        return f"xs : List Int\nys : List Int\n⊢ {task.return_type}"
    elif task.input_type == "List (List Int)":
        return f"xss : {task.input_type}\n⊢ {task.return_type}"
    elif task.input_type == "Nat":
        return f"n : Nat\n⊢ {task.return_type}"
    else:
        return f"xs : {task.input_type}\n⊢ {task.return_type}"


def extract_selfplay_pairs(results_dir: Path) -> list[TrainingPair]:
    """Extract oracle-verified (state, tactic) pairs from benchmark results."""
    pairs: list[TrainingPair] = []
    seen: set[str] = set()

    for json_file in sorted(results_dir.glob("*.json")):
        with open(json_file) as f:
            results = json.load(f)

        for result in results:
            if not result.get("solved", False):
                continue

            task_name = result["task"]
            tactics = result.get("tactics", [])
            if not tactics:
                continue

            # Look up task metadata
            try:
                task = get_task(task_name)
            except KeyError:
                continue

            goal_state = _build_goal_state(task)

            # Single-step solutions: just the tactic
            if len(tactics) == 1:
                tactic = tactics[0]
                key = f"{goal_state}|{tactic}"
                if key not in seen:
                    seen.add(key)
                    pairs.append(TrainingPair(
                        state=goal_state,
                        tactic=tactic,
                        source="selfplay",
                        task_name=task_name,
                        return_type=task.return_type,
                        input_type=task.input_type,
                    ))

            # Multi-step: add each step as a separate pair
            # First tactic applies to the initial goal
            if len(tactics) >= 1:
                tactic = tactics[0]
                key = f"{goal_state}|{tactic}"
                if key not in seen:
                    seen.add(key)
                    pairs.append(TrainingPair(
                        state=goal_state,
                        tactic=tactic,
                        source="selfplay",
                        task_name=task_name,
                        return_type=task.return_type,
                        input_type=task.input_type,
                    ))

            # For multi-step product types (constructor; exact A; exact B),
            # decompose into sub-goal pairs
            if len(tactics) >= 3 and tactics[0] == "constructor":
                # After constructor, two sub-goals are created
                # We can't know exact sub-goal states without REPL,
                # but for product types A × B, the sub-goals are ⊢ A and ⊢ B
                ret = task.return_type
                if "×" in ret:
                    parts = ret.split("×", 1)
                    left_type = parts[0].strip()
                    right_type = parts[1].strip()

                    # Determine param prefix from input_type
                    has_two = any("input2" in tc for tc in task.test_cases)
                    if has_two:
                        param_prefix = "xs : List Int\nys : List Int"
                    elif task.input_type == "List (List Int)":
                        param_prefix = f"xss : {task.input_type}"
                    elif task.input_type == "Nat":
                        param_prefix = "n : Nat"
                    else:
                        param_prefix = f"xs : {task.input_type}"

                    case_names = ["fst", "snd"]
                    for i, sub_type in enumerate([left_type, right_type]):
                        if i + 1 < len(tactics):
                            sub_tactic = tactics[i + 1]
                            # Plain sub-goal state (no prefix)
                            sub_state = f"{param_prefix}\n⊢ {sub_type}"
                            key = f"{sub_state}|{sub_tactic}"
                            if key not in seen:
                                seen.add(key)
                                pairs.append(TrainingPair(
                                    state=sub_state,
                                    tactic=sub_tactic,
                                    source="selfplay",
                                    task_name=f"{task_name}_sub{i}",
                                    return_type=sub_type,
                                    input_type=task.input_type,
                                ))
                            # Case-prefixed sub-goal state (matches Pantograph output)
                            pfx_state = f"case {case_names[i]}\n{param_prefix}\n⊢ {sub_type}"
                            pfx_key = f"{pfx_state}|{sub_tactic}"
                            if pfx_key not in seen:
                                seen.add(pfx_key)
                                pairs.append(TrainingPair(
                                    state=pfx_state,
                                    tactic=sub_tactic,
                                    source="selfplay",
                                    task_name=f"{task_name}_sub{i}_pfx",
                                    return_type=sub_type,
                                    input_type=task.input_type,
                                ))

    return pairs


def main():
    parser = argparse.ArgumentParser(description="Collect self-play data from benchmark results")
    parser.add_argument("--results-dir", type=str, required=True,
                        help="Directory containing benchmark result JSONs")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSONL file (default: synth/selfplay_pairs.jsonl)")
    parser.add_argument("--existing-train", type=str, default=None,
                        help="Existing training JSONL to deduplicate against")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent.parent
    results_dir = Path(args.results_dir)
    output_path = Path(args.output) if args.output else script_dir / "synth" / "selfplay_pairs.jsonl"

    pairs = extract_selfplay_pairs(results_dir)
    print(f"Extracted {len(pairs)} unique self-play pairs")

    # Deduplicate against existing training data
    if args.existing_train:
        existing = set()
        with open(args.existing_train) as f:
            for line in f:
                entry = json.loads(line)
                key = f"{entry['state']}|{entry['tactic']}"
                existing.add(key)
        before = len(pairs)
        pairs = [p for p in pairs if f"{p.state}|{p.tactic}" not in existing]
        print(f"After dedup against existing: {len(pairs)} new pairs (removed {before - len(pairs)})")

    # Write output
    with open(output_path, "w") as f:
        for pair in pairs:
            entry = {
                "state": pair.state,
                "tactic": pair.tactic,
                "source": pair.source,
                "task_name": pair.task_name,
                "return_type": pair.return_type,
                "input_type": pair.input_type,
            }
            f.write(json.dumps(entry) + "\n")

    print(f"Written to {output_path}")

    # Print summary
    from collections import Counter
    sources = Counter(p.task_name for p in pairs)
    print(f"\nPer-task breakdown:")
    for task, count in sorted(sources.items()):
        p = next(pp for pp in pairs if pp.task_name == task)
        print(f"  {task}: {count} pairs — {p.tactic[:80]}")


if __name__ == "__main__":
    main()
