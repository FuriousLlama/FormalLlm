"""Evaluate expressions against generated tasks (parallel).

Tests all 568 enumerated expressions against the 200 generated tasks.
Each expression is pre-typed, so we filter by return type before verification.
Runs batch compilations in parallel across CPU cores for ~10x speedup.

Usage:
    python synth/eval_generated.py
    python synth/eval_generated.py --max-tasks 50 --workers 8
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

LEAN_IMPORTS = """\
import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false
"""

_DIAG_RE = re.compile(r"^.*\.lean:\d+:\d+:|^note:|^warning:")


def _verify_batch(
    lean_project_str: str,
    task_name: str,
    input_type: str,
    return_type: str,
    test_cases: list[dict],
    batch_tactics: list[str],
    timeout: int = 60,
) -> list[tuple[str, str, bool]]:
    """Verify a batch of expressions for one task. Runs in a worker process.

    Returns list of (task_name, tactic, passed).
    """
    lean_project = Path(lean_project_str)
    lines = [LEAN_IMPORTS]
    eval_map = []

    for i, tactic in enumerate(batch_tactics):
        expr = tactic[len("exact "):] if tactic.startswith("exact ") else tactic
        fn = f"_e{i}"
        lines.append(f"def {fn} (xs : {input_type}) : {return_type} :=")
        lines.append(f"  {expr}")
        lines.append("")
        for tc in test_cases:
            lines.append(f"#eval {fn} {tc['input']}")
            eval_map.append((i, tc))
        lines.append("")

    # Use a unique temp file per worker to avoid collisions
    fd, eval_path = tempfile.mkstemp(
        prefix="_EvalGen_", suffix=".lean",
        dir=str(lean_project / "FormalNav"),
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines))

        result = subprocess.run(
            ["lake", "env", "lean", eval_path],
            cwd=str(lean_project),
            capture_output=True, text=True, timeout=timeout,
        )

        results = []
        if result.returncode == 0:
            output_lines = [
                line.strip()
                for line in result.stdout.strip().split("\n")
                if line.strip() and not _DIAG_RE.match(line)
            ]

            tactic_results: dict[int, tuple[int, int]] = {}
            for out_idx, (expr_idx, tc) in enumerate(eval_map):
                if out_idx >= len(output_lines):
                    break
                actual = output_lines[out_idx].strip()
                expected = tc["expected"].strip()
                p, t = tactic_results.get(expr_idx, (0, 0))
                tactic_results[expr_idx] = (p + (1 if actual == expected else 0), t + 1)

            for i, tactic in enumerate(batch_tactics):
                passed, total = tactic_results.get(i, (0, len(test_cases)))
                results.append((task_name, tactic, passed == total))
        else:
            for tactic in batch_tactics:
                results.append((task_name, tactic, False))

        return results

    except subprocess.TimeoutExpired:
        return [(task_name, tac, False) for tac in batch_tactics]
    finally:
        try:
            os.unlink(eval_path)
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description="Evaluate expressions on generated tasks (parallel)")
    parser.add_argument("--expressions", type=str, default="synth/expressions.jsonl")
    parser.add_argument("--tasks", type=str, default="synth/synth_tasks.jsonl")
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None,
                        help="Parallel workers (default: 90%% of CPU cores)")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=60, help="Timeout per batch (seconds)")
    parser.add_argument("--output", type=str, default="synth/eval_results.json")
    args = parser.parse_args()

    if args.workers is None:
        args.workers = max(1, int(os.cpu_count() * 0.9))

    script_dir = Path(__file__).resolve().parent.parent
    lean_project = script_dir / "env" / "FormalNav"
    lean_project_str = str(lean_project.resolve())

    # Load expressions
    with open(script_dir / args.expressions) as f:
        expressions = [json.loads(line) for line in f]
    print(f"Loaded {len(expressions)} expressions")

    # Load tasks
    with open(script_dir / args.tasks) as f:
        tasks = [json.loads(line) for line in f]
    if args.max_tasks:
        tasks = tasks[:args.max_tasks]
    print(f"Loaded {len(tasks)} tasks")

    # Group expressions by return type
    exprs_by_type: dict[str, list[dict]] = {}
    for e in expressions:
        exprs_by_type.setdefault(e["return_type"], []).append(e)
    by_type = Counter(e["return_type"] for e in expressions)
    print(f"Expressions by type: {dict(by_type)}")

    # Build all (task, batch) work items
    work_items = []
    for task in tasks:
        matched = exprs_by_type.get(task["return_type"], [])
        if not matched:
            continue
        for batch_start in range(0, len(matched), args.batch_size):
            batch = matched[batch_start:batch_start + args.batch_size]
            batch_tactics = [e["tactic"] for e in batch]
            work_items.append((
                lean_project_str,
                task["name"],
                task["input_type"],
                task["return_type"],
                task["test_cases"],
                batch_tactics,
                args.timeout,
            ))

    print(f"\nTotal work items: {len(work_items)} batches across {len(tasks)} tasks")
    print(f"Workers: {args.workers}")

    # Run in parallel
    t0 = time.time()
    all_results: dict[str, list[tuple[str, bool]]] = {t["name"]: [] for t in tasks}
    completed = 0

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(_verify_batch, *item): item[1] for item in work_items}

        for future in as_completed(futures):
            completed += 1
            try:
                batch_results = future.result()
                for task_name, tactic, passed in batch_results:
                    all_results[task_name].append((tactic, passed))
            except Exception as e:
                task_name = futures[future]
                print(f"  ERROR on {task_name}: {e}")

            if completed % 100 == 0 or completed == len(work_items):
                elapsed = time.time() - t0
                rate = completed / elapsed if elapsed > 0 else 0
                eta = (len(work_items) - completed) / rate if rate > 0 else 0
                print(f"  {completed}/{len(work_items)} batches | {elapsed:.0f}s elapsed | ETA {eta:.0f}s")

    elapsed = time.time() - t0

    # Summarize
    task_results: dict[str, dict] = {}
    total_solved = 0

    for task in tasks:
        results = all_results.get(task["name"], [])
        solved = [tac for tac, passed in results if passed]
        task_results[task["name"]] = {
            "return_type": task["return_type"],
            "candidates_tested": len(results),
            "solutions_found": len(solved),
            "solutions": solved[:5],
            "reference": task["reference_tactic"],
        }
        if solved:
            total_solved += 1

    print(f"\n{'='*60}")
    print(f"RESULTS: {total_solved}/{len(tasks)} tasks solved by enumerated expressions")
    print(f"Time: {elapsed:.0f}s ({args.workers} workers)")

    # Breakdown by type
    type_counts: dict[str, tuple[int, int]] = {}
    for name, info in task_results.items():
        rt = info["return_type"]
        s, t = type_counts.get(rt, (0, 0))
        type_counts[rt] = (s + (1 if info["solutions_found"] > 0 else 0), t + 1)

    print(f"\nBy return type:")
    for rt, (solved, total) in sorted(type_counts.items()):
        print(f"  {rt:15s}: {solved}/{total}")

    # Unsolved tasks
    unsolved = [name for name, info in task_results.items() if info["solutions_found"] == 0]
    if unsolved:
        print(f"\nUnsolved tasks ({len(unsolved)}):")
        for name in unsolved[:20]:
            info = task_results[name]
            print(f"  {name:45s} {info['return_type']:12s} ref: {info['reference'][:50]}")
        if len(unsolved) > 20:
            print(f"  ... and {len(unsolved) - 20} more")

    # Save results
    output_path = script_dir / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({
            "total_tasks": len(tasks),
            "total_solved": total_solved,
            "total_expressions": len(expressions),
            "elapsed_seconds": round(elapsed, 1),
            "workers": args.workers,
            "type_breakdown": {rt: {"solved": s, "total": t} for rt, (s, t) in type_counts.items()},
            "tasks": task_results,
        }, f, indent=2)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
