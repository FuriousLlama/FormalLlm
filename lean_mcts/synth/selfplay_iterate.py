"""Phase 6: Self-play iteration — discover new solutions using learned policy.

For single-step synthesis tasks, skips MCTS entirely and directly:
  1. Samples candidate expressions from the policy (LoRA or PCFG)
  2. Verifies each against test cases using batch oracle (#eval)
  3. Collects new (state, tactic) pairs not in training data
  4. Retrains and measures improvement

Usage:
    python synth/selfplay_iterate.py --round 2
    python synth/selfplay_iterate.py --round 2 --policy pcfg
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

LEAN_IMPORTS = """\
import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false
"""

_DIAG_RE = re.compile(r"^.*\.lean:\d+:\d+:|^note:|^warning:")


def verify_tactic_batch(
    tasks: list[dict],
    tactics_per_task: dict[str, list[str]],
    lean_project: Path,
    batch_size: int = 20,
    timeout: int = 120,
) -> dict[str, list[tuple[str, bool]]]:
    """Batch-verify candidate tactics for multiple tasks.

    Returns: {task_name: [(tactic, passed), ...]}
    """
    lean_project = lean_project.resolve()
    results: dict[str, list[tuple[str, bool]]] = {t["name"]: [] for t in tasks}

    for batch_start in range(0, len(tasks), batch_size):
        batch = tasks[batch_start:batch_start + batch_size]
        lines = [LEAN_IMPORTS]
        eval_map = []  # (task_name, tactic_idx, test_case)

        fn_idx = 0
        for task in batch:
            task_tactics = tactics_per_task.get(task["name"], [])
            for tac_idx, tactic in enumerate(task_tactics):
                expr = tactic[len("exact "):] if tactic.startswith("exact ") else tactic
                fn_name = f"_sp_{fn_idx}"
                lines.append(f"def {fn_name} (xs : {task['input_type']}) : {task['return_type']} :=")
                lines.append(f"  {expr}")
                lines.append("")

                for tc in task["test_cases"]:
                    lines.append(f"#eval {fn_name} {tc['input']}")
                    eval_map.append((task["name"], tac_idx, tactic, tc))
                lines.append("")
                fn_idx += 1

        if not eval_map:
            continue

        eval_file = lean_project / "FormalNav" / "_SelfPlay.lean"
        try:
            eval_file.write_text("\n".join(lines))
            result = subprocess.run(
                ["lake", "env", "lean", str(eval_file)],
                cwd=str(lean_project),
                capture_output=True, text=True, timeout=timeout,
            )

            if result.returncode == 0:
                output_lines = [
                    line.strip()
                    for line in result.stdout.strip().split("\n")
                    if line.strip() and not _DIAG_RE.match(line)
                ]

                # Track pass/fail per (task, tactic)
                tactic_results: dict[tuple[str, int], tuple[int, int]] = {}
                for out_idx, (task_name, tac_idx, tactic, tc) in enumerate(eval_map):
                    key = (task_name, tac_idx)
                    if out_idx >= len(output_lines):
                        break
                    actual = output_lines[out_idx].strip()
                    expected = tc["expected"].strip()
                    p, t = tactic_results.get(key, (0, 0))
                    tactic_results[key] = (p + (1 if actual == expected else 0), t + 1)

                for (task_name, tac_idx), (passed, total) in tactic_results.items():
                    tactic = tactics_per_task[task_name][tac_idx]
                    results[task_name].append((tactic, passed == total))
            else:
                # Batch compilation error — try individual tactics
                for task in batch:
                    for tactic in tactics_per_task.get(task["name"], []):
                        results[task["name"]].append((tactic, False))

        except subprocess.TimeoutExpired:
            for task in batch:
                for tactic in tactics_per_task.get(task["name"], []):
                    results[task["name"]].append((tactic, False))
        finally:
            if eval_file.exists():
                eval_file.unlink()

    return results


def sample_from_lora(
    tasks: list[dict],
    base_model: str,
    adapter_path: str,
    k: int = 64,
) -> dict[str, list[str]]:
    """Sample candidate tactics from the LoRA policy for each task."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from search.policy import TrainedPolicy

    policy = TrainedPolicy(base_model, adapter_path)
    tactics_per_task: dict[str, list[str]] = {}

    for task in tasks:
        state_str = f"xs : {task['input_type']}\n⊢ {task['return_type']}"

        class FakeState:
            def __init__(self, pp):
                self.pp = pp

        candidates = policy.propose_tactics(FakeState(state_str), k=k)
        tactics = [t for t, _ in candidates]
        tactics_per_task[task["name"]] = tactics

    return tactics_per_task


def sample_from_pcfg(
    tasks: list[dict],
    grammar_path: str,
    k: int = 200,
) -> dict[str, list[str]]:
    """Sample candidate tactics from the PCFG for each task."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from synth.grammar import PCFGPolicy

    policy = PCFGPolicy(grammar_path=grammar_path)
    tactics_per_task: dict[str, list[str]] = {}

    for task in tasks:
        tactics = policy.propose_tactics(None, k=k)
        tactics_per_task[task["name"]] = tactics

    return tactics_per_task


def verify_per_task(
    task: dict,
    candidates: list[str],
    lean_project: Path,
    batch_size: int = 50,
    timeout: int = 120,
) -> list[tuple[str, bool]]:
    """Verify candidate tactics for a single task using batch #eval."""
    lean_project = lean_project.resolve()
    results: list[tuple[str, bool]] = []

    for batch_start in range(0, len(candidates), batch_size):
        batch = candidates[batch_start:batch_start + batch_size]
        lines = [LEAN_IMPORTS]
        eval_map = []  # (tac_idx, test_case)

        for i, tactic in enumerate(batch):
            expr = tactic[len("exact "):] if tactic.startswith("exact ") else tactic
            fn = f"_v{i}"
            lines.append(f"def {fn} (xs : {task['input_type']}) : {task['return_type']} :=")
            lines.append(f"  {expr}")
            lines.append("")
            for tc in task["test_cases"]:
                lines.append(f"#eval {fn} {tc['input']}")
                eval_map.append((i, tc))
            lines.append("")

        eval_file = lean_project / "FormalNav" / "_SelfPlay.lean"
        try:
            eval_file.write_text("\n".join(lines))
            result = subprocess.run(
                ["lake", "env", "lean", str(eval_file)],
                cwd=str(lean_project),
                capture_output=True, text=True, timeout=timeout,
            )

            if result.returncode == 0:
                output_lines = [
                    line.strip()
                    for line in result.stdout.strip().split("\n")
                    if line.strip() and not _DIAG_RE.match(line)
                ]

                tactic_results: dict[int, tuple[int, int]] = {}
                for out_idx, (tac_idx, tc) in enumerate(eval_map):
                    if out_idx >= len(output_lines):
                        break
                    actual = output_lines[out_idx].strip()
                    expected = tc["expected"].strip()
                    p, t = tactic_results.get(tac_idx, (0, 0))
                    tactic_results[tac_idx] = (p + (1 if actual == expected else 0), t + 1)

                for i, tactic in enumerate(batch):
                    passed, total = tactic_results.get(i, (0, len(task["test_cases"])))
                    results.append((tactic, passed == total))
            else:
                for tactic in batch:
                    results.append((tactic, False))
        except subprocess.TimeoutExpired:
            for tactic in batch:
                results.append((tactic, False))
        finally:
            if eval_file.exists():
                eval_file.unlink()

    return results


def main():
    parser = argparse.ArgumentParser(description="Self-play iteration")
    parser.add_argument("--round", type=int, default=2, help="Self-play round number")
    parser.add_argument("--policy", choices=["lora", "pcfg", "both"], default="pcfg")
    parser.add_argument("--base-model", type=str, default="Qwen/Qwen2.5-Coder-1.5B")
    parser.add_argument("--adapter-path", type=str, default=None)
    parser.add_argument("--grammar-path", type=str, default=None)
    parser.add_argument("--tasks", type=str, default="synth/synth_tasks.jsonl")
    parser.add_argument("--existing-train", type=str, default="train/synth_train.jsonl")
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--k", type=int, default=200, help="Candidates per policy")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent.parent
    lean_project = script_dir / "env" / "FormalNav"
    tasks_path = script_dir / args.tasks

    if args.adapter_path is None:
        args.adapter_path = str(script_dir / "train" / "synth_adapter")
    if args.grammar_path is None:
        args.grammar_path = str(script_dir / "synth" / "grammar.json")
    if args.output is None:
        args.output = f"train/selfplay_round{args.round}.jsonl"
    output_path = script_dir / args.output

    # Load tasks
    with open(tasks_path) as f:
        tasks = [json.loads(line) for line in f]
    print(f"Loaded {len(tasks)} tasks")

    # Load existing training data to find already-known solutions
    existing: set[tuple[str, str]] = set()
    existing_path = script_dir / args.existing_train
    if existing_path.exists():
        with open(existing_path) as f:
            for line in f:
                entry = json.loads(line)
                existing.add((entry["state"], entry["tactic"]))
    print(f"Existing training pairs: {len(existing)}")

    # Sample candidates from PCFG (state-independent, sample once)
    print(f"\nSampling {args.k} candidates from {args.policy} policy...")
    t0 = time.time()

    if args.policy == "pcfg":
        import sys
        sys.path.insert(0, str(script_dir))
        from synth.grammar import PCFGPolicy
        policy = PCFGPolicy(grammar_path=args.grammar_path)
        # PCFG is state-independent — sample once, test all tasks
        shared_candidates = policy.propose_tactics(None, k=args.k)
        print(f"  Sampled {len(shared_candidates)} unique expressions ({time.time() - t0:.1f}s)")
    elif args.policy == "lora":
        # For LoRA, sample per return type (all tasks share `xs : List Int`)
        import sys
        sys.path.insert(0, str(script_dir))
        from search.policy import TrainedPolicy

        lora = TrainedPolicy(args.base_model, args.adapter_path)
        return_types = set(t["return_type"] for t in tasks)
        shared_by_type: dict[str, list[str]] = {}

        class FakeState:
            def __init__(self, pp):
                self.pp = pp

        for rt in return_types:
            state_str = f"xs : List Int\n⊢ {rt}"
            candidates = lora.propose_tactics(FakeState(state_str), k=args.k)
            shared_by_type[rt] = [t for t, _ in candidates]
            print(f"  {rt}: {len(shared_by_type[rt])} candidates")

        shared_candidates = None
        print(f"  Sampling time: {time.time() - t0:.1f}s")
    else:
        raise ValueError(f"Use --policy pcfg or lora, not {args.policy}")

    # Verify candidates per task
    print(f"\nVerifying per task ({len(tasks)} tasks)...")
    t0 = time.time()
    new_pairs = []
    tasks_solved = 0
    tasks_already_known = 0

    for i, task in enumerate(tasks):
        if args.policy == "pcfg":
            candidates = shared_candidates
        else:
            candidates = shared_by_type.get(task["return_type"], [])

        results = verify_per_task(task, candidates, lean_project, batch_size=50, timeout=120)
        solved_any = False
        for tactic, passed in results:
            if passed:
                state = f"xs : {task['input_type']}\n⊢ {task['return_type']}"
                if (state, tactic) not in existing:
                    new_pairs.append({
                        "state": state,
                        "tactic": tactic,
                        "source": f"selfplay_round{args.round}",
                        "task_name": task["name"],
                    })
                else:
                    tasks_already_known += 1
                solved_any = True
        if solved_any:
            tasks_solved += 1

        if (i + 1) % 20 == 0:
            elapsed = time.time() - t0
            print(f"  {i+1}/{len(tasks)} tasks, {tasks_solved} solved, {len(new_pairs)} new pairs ({elapsed:.0f}s)")

    verify_time = time.time() - t0
    print(f"\nResults:")
    print(f"  Tasks solved: {tasks_solved}/{len(tasks)}")
    print(f"  New training pairs: {len(new_pairs)}")
    print(f"  Already-known solutions: {tasks_already_known}")
    print(f"  Verification time: {verify_time:.1f}s")

    # Write new pairs
    if new_pairs:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            for pair in new_pairs:
                f.write(json.dumps(pair) + "\n")
        print(f"  Written to: {output_path}")
    else:
        print("  No new pairs to write")

    return tasks_solved, len(new_pairs)


if __name__ == "__main__":
    main()
