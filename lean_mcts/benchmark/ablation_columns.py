"""
Per-policy ablation tracking for Sprint 29 benchmarks (Phase 0.1).

Joins 3 per-policy benchmark result files — one each for:
    - expanded-fixed  (EF catalog only, no LoRA)
    - goal-conditioned / trained  (LoRA only, no EF)
    - hybrid-gc / hybrid-comp  (LoRA + EF safety net)

and emits an ablation table with 4 columns per task:

    ef_solved           — task solved in the EF-only run
    lora_only_solved    — task solved in the LoRA-only run
    hybrid_solved       — task solved in the hybrid run
    lora_contribution   — hybrid_solved AND NOT ef_solved
                          (i.e. hybrid found a solve the EF catalog alone could not)

This is the standing invariant the plan calls for: after every trained config's
benchmark sweep, run this tool and look at the `lora_contribution` column. If it
is zero across all tasks, the LoRA is not contributing anything that the EF catalog
doesn't already cover. That is the Sprint 27 finding ("LoRA is dead weight") which
should have been caught automatically six sprints ago — now it is.

Usage:

    python lean_mcts/benchmark/ablation_columns.py \\
        --ef run_expanded-fixed_20260413_120000_0.json \\
        --lora run_goal-conditioned_20260413_121500_0.json \\
        --hybrid run_hybrid-gc_20260413_123000_0.json \\
        --output ablation.csv

Or auto-discover from a directory:

    python lean_mcts/benchmark/ablation_columns.py \\
        --results-dir benchmark/results/s29-C \\
        --output benchmark/results/s29-C/ablation.csv

In auto-discover mode the filenames must follow the `run_{policy}_*.json` convention
written by `benchmark/run.py`. The script picks the most recent file per policy.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path


# Canonical policy names recognised by this tool. Any other policy name is ignored
# during auto-discover (so you can have extra non-ablation runs in the same dir).
POLICY_EF_NAMES = {"expanded-fixed", "fixed"}
POLICY_LORA_NAMES = {"goal-conditioned", "trained", "synthesis"}
POLICY_HYBRID_NAMES = {"hybrid-gc", "hybrid-comp", "compositional"}

# Abstract buckets for the ablation columns
BUCKET_EF = "ef"
BUCKET_LORA = "lora"
BUCKET_HYBRID = "hybrid"


def load_results(path: Path) -> list[dict]:
    """Load a benchmark result JSON file (list-of-dicts format from run.py)."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected list-of-dicts, got {type(data).__name__}")
    return data


def bucket_for_policy(policy_name: str) -> str | None:
    if policy_name in POLICY_EF_NAMES:
        return BUCKET_EF
    if policy_name in POLICY_LORA_NAMES:
        return BUCKET_LORA
    if policy_name in POLICY_HYBRID_NAMES:
        return BUCKET_HYBRID
    return None


def infer_policy_from_filename(path: Path) -> str | None:
    """Parse `run_{policy}_{timestamp}_{idx}.json` convention."""
    name = path.stem  # run_hybrid-gc_20260413_123000_0
    if not name.startswith("run_"):
        return None
    rest = name[len("run_"):]
    # Split from the right: last two parts are timestamp chunks + run_idx
    parts = rest.rsplit("_", 3)  # [policy, YYYYMMDD, HHMMSS, idx]
    if len(parts) >= 4:
        return parts[0]
    # Fallback: everything before the first underscore
    return rest.split("_", 1)[0]


def infer_policy_from_results(results: list[dict]) -> str | None:
    """Read policy_name from the first result dict (written by run.py since S29)."""
    for r in results:
        p = r.get("policy_name")
        if p:
            return p
    return None


def auto_discover(results_dir: Path) -> dict[str, Path]:
    """Find the most-recent result file per bucket in `results_dir`.

    Returns a dict {bucket: path} for whichever of {ef, lora, hybrid} were found.
    """
    candidates: dict[str, list[Path]] = {BUCKET_EF: [], BUCKET_LORA: [], BUCKET_HYBRID: []}
    for path in sorted(results_dir.glob("run_*.json")):
        policy = infer_policy_from_filename(path)
        if policy is None:
            try:
                policy = infer_policy_from_results(load_results(path))
            except Exception:
                continue
        bucket = bucket_for_policy(policy) if policy else None
        if bucket:
            candidates[bucket].append(path)

    picked: dict[str, Path] = {}
    for bucket, paths in candidates.items():
        if paths:
            # Most-recent by mtime wins.
            picked[bucket] = max(paths, key=lambda p: p.stat().st_mtime)
    return picked


def build_ablation_table(
    ef_results: list[dict] | None,
    lora_results: list[dict] | None,
    hybrid_results: list[dict] | None,
) -> list[dict]:
    """Join per-policy results by task name and emit one row per task.

    Every row has:
        task, category, ef_solved, lora_only_solved, hybrid_solved,
        lora_contribution, hybrid_source (if hybrid_results provided)
    Missing runs are recorded as None (distinguishable from False in the CSV).
    """
    def index_by_task(results: list[dict] | None) -> dict[str, dict]:
        if results is None:
            return {}
        return {r.get("task"): r for r in results if r.get("task")}

    ef_idx = index_by_task(ef_results)
    lora_idx = index_by_task(lora_results)
    hybrid_idx = index_by_task(hybrid_results)

    task_names: list[str] = []
    seen: set[str] = set()
    for results in (ef_results, lora_results, hybrid_results):
        if not results:
            continue
        for r in results:
            name = r.get("task")
            if name and name not in seen:
                task_names.append(name)
                seen.add(name)

    rows: list[dict] = []
    for name in task_names:
        ef = ef_idx.get(name)
        lora = lora_idx.get(name)
        hybrid = hybrid_idx.get(name)

        category = (
            (hybrid or {}).get("category")
            or (lora or {}).get("category")
            or (ef or {}).get("category")
            or ""
        )

        ef_solved = ef["solved"] if ef is not None else None
        lora_only_solved = lora["solved"] if lora is not None else None
        hybrid_solved = hybrid["solved"] if hybrid is not None else None

        # lora_contribution: true iff hybrid solved it and EF-alone did NOT.
        # Require both inputs present to compute; otherwise None.
        if hybrid_solved is not None and ef_solved is not None:
            lora_contribution = bool(hybrid_solved and not ef_solved)
        else:
            lora_contribution = None

        # hybrid_source: within the hybrid run, classify the winning tactic by
        # whether it is in the EF catalog (from the ef_catalog_match field
        # written by run.py). Informative even if ef_solved is missing.
        hybrid_source: str | None = None
        if hybrid is not None and hybrid.get("solved"):
            match = hybrid.get("ef_catalog_match")
            if match is True:
                hybrid_source = "ef_template"
            elif match is False:
                hybrid_source = "lora_generation"

        rows.append({
            "task": name,
            "category": category,
            "ef_solved": ef_solved,
            "lora_only_solved": lora_only_solved,
            "hybrid_solved": hybrid_solved,
            "lora_contribution": lora_contribution,
            "hybrid_source": hybrid_source,
        })

    return rows


def summarize(rows: list[dict]) -> dict[str, int]:
    """Aggregate solve counts per column for the standing-invariant check."""
    counts = Counter()
    for r in rows:
        for key in ("ef_solved", "lora_only_solved", "hybrid_solved", "lora_contribution"):
            if r[key] is True:
                counts[key] += 1
    counts["total_tasks"] = len(rows)
    counts["hybrid_ef_template_solves"] = sum(
        1 for r in rows if r["hybrid_source"] == "ef_template"
    )
    counts["hybrid_lora_generation_solves"] = sum(
        1 for r in rows if r["hybrid_source"] == "lora_generation"
    )
    return dict(counts)


def write_csv(rows: list[dict], path: Path) -> None:
    """Emit the ablation table as CSV. None values are written as empty string."""
    if not rows:
        path.write_text("")
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            out = {k: ("" if v is None else v) for k, v in r.items()}
            writer.writerow(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ef", type=Path, default=None, help="EF-only run result JSON")
    parser.add_argument("--lora", type=Path, default=None, help="LoRA-only run result JSON")
    parser.add_argument("--hybrid", type=Path, default=None, help="Hybrid run result JSON")
    parser.add_argument("--results-dir", type=Path, default=None,
                        help="Auto-discover most-recent per-policy result files in this directory")
    parser.add_argument("--output", type=Path, default=None,
                        help="Write ablation CSV here (default: stdout table only)")
    parser.add_argument("--require-all", action="store_true",
                        help="Error out if any of EF / LoRA / hybrid is missing")
    args = parser.parse_args()

    # Resolve inputs: explicit flags override auto-discover.
    ef_path = args.ef
    lora_path = args.lora
    hybrid_path = args.hybrid

    if args.results_dir is not None:
        discovered = auto_discover(args.results_dir)
        ef_path = ef_path or discovered.get(BUCKET_EF)
        lora_path = lora_path or discovered.get(BUCKET_LORA)
        hybrid_path = hybrid_path or discovered.get(BUCKET_HYBRID)

    missing = [name for name, p in (("ef", ef_path), ("lora", lora_path), ("hybrid", hybrid_path)) if p is None]
    if missing:
        msg = f"Missing result files for: {', '.join(missing)}"
        if args.require_all:
            print(f"ERROR: {msg}", file=sys.stderr)
            sys.exit(2)
        print(f"NOTE: {msg} (proceeding with partial ablation table)", file=sys.stderr)

    ef_results = load_results(ef_path) if ef_path is not None else None
    lora_results = load_results(lora_path) if lora_path is not None else None
    hybrid_results = load_results(hybrid_path) if hybrid_path is not None else None

    rows = build_ablation_table(ef_results, lora_results, hybrid_results)
    counts = summarize(rows)

    # Print a compact standing-invariant summary — this is the line the plan
    # cares about most: `lora_contribution`. If it is zero, flag loudly.
    total = counts["total_tasks"]
    print(f"Ablation summary ({total} tasks):")
    print(f"  ef_solved:          {counts.get('ef_solved', 0)}/{total}")
    print(f"  lora_only_solved:   {counts.get('lora_only_solved', 0)}/{total}")
    print(f"  hybrid_solved:      {counts.get('hybrid_solved', 0)}/{total}")
    print(f"  lora_contribution:  {counts.get('lora_contribution', 0)}/{total}", end="")
    if counts.get("lora_contribution", 0) == 0 and hybrid_results and ef_results:
        print("   <-- LoRA IS DEAD WEIGHT (matches S27 finding)")
    else:
        print()
    print(f"  hybrid ef-template solves:   {counts.get('hybrid_ef_template_solves', 0)}")
    print(f"  hybrid lora-gen solves:      {counts.get('hybrid_lora_generation_solves', 0)}")

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_csv(rows, args.output)
        print(f"Wrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
