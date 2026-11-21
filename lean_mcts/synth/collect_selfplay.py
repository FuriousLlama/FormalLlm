"""Self-play data collection for synthesis policy training.

Collects (state, tactic) pairs from:
  1. Enumerated expressions (all validated, from enumerate.py)
  2. Generated task reference solutions (verified by oracle)
  3. Benchmark T2/V2 tasks: MCTS exploration with ExpandedFixedPolicy
  4. Multi-step decomposition: synthesized constructor + exact pairs for product types
  5. Data augmentation: variable name variants (multi-type aware)

Outputs synth_v2_train.jsonl and synth_v2_val.jsonl for LoRA training.

Usage:
    python synth/collect_selfplay.py --expressions synth/expressions_v2.jsonl
    python synth/collect_selfplay.py --skip-mcts  # reference solutions only
    python synth/collect_selfplay.py --multi-step  # add product-type decompositions
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Lean imports + oracle setup — same as oracle.py
LEAN_IMPORTS = """\
import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false
"""

_DIAG_RE = re.compile(r"^.*\.lean:\d+:\d+:|^note:|^warning:")

# Variable name variants for augmentation (multi-type aware)
VAR_VARIANTS_XS = ["l", "lst", "input", "arr", "items", "ns", "vs"]
VAR_VARIANTS_XSS = ["lists", "ll", "nested", "matrix", "groups"]
VAR_VARIANTS_N = ["k", "m", "count", "size", "num"]
# Two-arg: (xs, ys) → (a, b), (left, right), etc.
VAR_VARIANTS_TWO_ARG = [
    ("as", "bs"), ("left", "right"), ("first", "second"),
    ("l1", "l2"), ("xs1", "xs2"),
]


@dataclass
class TrainingPair:
    """A (state, tactic) pair for policy training."""
    state: str        # e.g. "xs : List Int\n⊢ Nat"
    tactic: str       # e.g. "exact (xs.filter ...).length"
    source: str       # "reference", "mcts", "augmented"
    task_name: str    # originating task
    return_type: str
    input_type: str


# ---------------------------------------------------------------------------
# Direct collection from reference solutions
# ---------------------------------------------------------------------------

def collect_from_expressions(
    expressions_path: Path,
    verbose: bool = False,
) -> list[TrainingPair]:
    """Collect (state, tactic) pairs from ALL validated expressions.

    expressions.jsonl already has state and tactic fields from Phase 1,
    and all expressions are pre-validated (compiled + non-degenerate).
    No additional oracle check needed.
    """
    pairs: list[TrainingPair] = []

    with open(expressions_path) as f:
        exprs = [json.loads(line) for line in f]

    print(f"Collecting from {len(exprs)} validated expressions...")

    for expr in exprs:
        pairs.append(TrainingPair(
            state=expr["state"],
            tactic=expr["tactic"],
            source="reference",
            task_name=expr["tag"],
            return_type=expr["return_type"],
            input_type=expr["input_type"],
        ))

    print(f"  Collected {len(pairs)} expression pairs")
    return pairs


def collect_from_references(
    tasks_path: Path,
    lean_project: Path,
    verify: bool = True,
    verbose: bool = False,
) -> list[TrainingPair]:
    """Collect (state, tactic) pairs directly from generated task reference solutions.

    Each generated task in synth_tasks.jsonl has a reference_tactic field.
    We build the tactic state string and optionally verify with the oracle.
    """
    lean_project = lean_project.resolve()
    pairs: list[TrainingPair] = []

    with open(tasks_path) as f:
        tasks = [json.loads(line) for line in f]

    print(f"Collecting from {len(tasks)} generated task references...")

    # Batch-verify all reference solutions at once
    if verify:
        verified = _batch_verify_references(tasks, lean_project, verbose)
    else:
        verified = {t["name"]: True for t in tasks}

    n_ok = 0
    for task in tasks:
        if not verified.get(task["name"], False):
            if verbose:
                print(f"  SKIP {task['name']}: oracle verification failed")
            continue

        state = f"xs : {task['input_type']}\n⊢ {task['return_type']}"
        tactic = task["reference_tactic"]

        pairs.append(TrainingPair(
            state=state,
            tactic=tactic,
            source="reference",
            task_name=task["name"],
            return_type=task["return_type"],
            input_type=task["input_type"],
        ))
        n_ok += 1

    print(f"  Collected {n_ok}/{len(tasks)} verified reference pairs")
    return pairs


def _batch_verify_references(
    tasks: list[dict],
    lean_project: Path,
    verbose: bool = False,
    batch_size: int = 50,
    timeout: int = 120,
) -> dict[str, bool]:
    """Batch-verify reference tactics compile and produce correct output."""
    results: dict[str, bool] = {}

    for batch_start in range(0, len(tasks), batch_size):
        batch = tasks[batch_start:batch_start + batch_size]

        lines = [LEAN_IMPORTS]
        eval_indices = []  # (task_idx_in_batch, test_case_idx)

        for i, task in enumerate(batch):
            fn = f"_verify_{i}"
            lines.append(f"def {fn} (xs : {task['input_type']}) : {task['return_type']} :=")
            # Extract expression from "exact <expr>"
            tactic = task["reference_tactic"]
            expr = tactic[len("exact "):] if tactic.startswith("exact ") else tactic
            lines.append(f"  {expr}")
            lines.append("")

            for tc in task["test_cases"]:
                lines.append(f"#eval {fn} {tc['input']}")
                eval_indices.append((i, tc))
            lines.append("")

        eval_file = lean_project / "FormalNav" / "_VerifyRef.lean"
        try:
            eval_file.write_text("\n".join(lines))
            result = subprocess.run(
                ["lake", "env", "lean", str(eval_file)],
                cwd=str(lean_project),
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode == 0:
                output_lines = [
                    line.strip()
                    for line in result.stdout.strip().split("\n")
                    if line.strip() and not _DIAG_RE.match(line)
                ]

                # Check each test case
                task_pass_count: dict[int, tuple[int, int]] = {}  # idx → (passed, total)
                for out_idx, (task_idx, tc) in enumerate(eval_indices):
                    if out_idx >= len(output_lines):
                        break
                    actual = output_lines[out_idx].strip()
                    expected = tc["expected"].strip()
                    p, t = task_pass_count.get(task_idx, (0, 0))
                    task_pass_count[task_idx] = (p + (1 if actual == expected else 0), t + 1)

                for i, task in enumerate(batch):
                    passed, total = task_pass_count.get(i, (0, len(task["test_cases"])))
                    results[task["name"]] = (passed == total)
            else:
                # Batch failed — mark all as failed
                if verbose:
                    print(f"  Batch verify failed: {result.stderr[:200]}")
                for task in batch:
                    results[task["name"]] = False

        except subprocess.TimeoutExpired:
            for task in batch:
                results[task["name"]] = False

        finally:
            if eval_file.exists():
                eval_file.unlink()

    return results


# ---------------------------------------------------------------------------
# MCTS collection from benchmark tasks
# ---------------------------------------------------------------------------

def collect_from_mcts(
    rollouts: int = 500,
    max_k: int = 200,
    verbose: bool = False,
) -> list[TrainingPair]:
    """Run MCTS on benchmark T2 tasks and collect (state, tactic) pairs.

    Args:
        rollouts: MCTS rollouts per task
        max_k: Cap on tactics per expansion (avoids REPL crash)
        verbose: Print per-task progress
    """
    # Late imports to avoid importing heavy deps when not needed
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from env.dojo_wrapper import LeanEnv
    from search.mcts import search
    from search.policy import ExpandedFixedPolicy
    from benchmark.tasks import TASKS
    from eval.oracle import verify

    lean_project = Path(__file__).resolve().parent.parent / "env" / "FormalNav"
    policy = ExpandedFixedPolicy()

    # Wrap policy to cap k
    def capped_policy(state, k=None):
        return policy.propose_tactics(state, k=max_k)

    pairs: list[TrainingPair] = []
    print(f"Running MCTS on {len(TASKS)} T2 benchmark tasks (rollouts={rollouts}, max_k={max_k})...")

    for task in TASKS:
        if verbose:
            print(f"  {task.name}...", end=" ", flush=True)

        env = LeanEnv(
            repo_path=str(lean_project),
            file_path=task.lean_file,
            theorem_name=task.theorem_name,
        )

        def oracle_fn(tactics):
            result = verify(task, tactics, lean_project, verbose=False)
            return result.passed

        try:
            result = search(
                env=env,
                n_rollouts=rollouts,
                policy_fn=capped_policy,
                oracle_fn=oracle_fn,
                verbose=False,
            )

            if result["solved"]:
                # Collect the winning tactic(s)
                for tactic in result["tactics"]:
                    state = f"xs : {task.input_type}\n⊢ {task.return_type}"
                    pairs.append(TrainingPair(
                        state=state,
                        tactic=tactic,
                        source="mcts",
                        task_name=task.name,
                        return_type=task.return_type,
                        input_type=task.input_type,
                    ))
                if verbose:
                    print(f"SOLVED (rollout {result['rollouts_used']}): {result['tactics']}")
            else:
                if verbose:
                    print("FAILED")

        except Exception as e:
            if verbose:
                print(f"ERROR: {e}")

        finally:
            env.close()

    print(f"  Collected {len(pairs)} pairs from MCTS")
    return pairs


# ---------------------------------------------------------------------------
# Multi-step pair synthesis for product return types
# ---------------------------------------------------------------------------

# Product type decompositions: (product_type → (left_type, right_type))
PRODUCT_TYPES = {
    "Nat × Nat": ("Nat", "Nat"),
    "Nat × Int": ("Nat", "Int"),
    "Int × Int": ("Int", "Int"),
    "List Int × List Int": ("List Int", "List Int"),
}


def synthesize_multistep_pairs(
    pairs: list[TrainingPair],
    verbose: bool = False,
) -> list[TrainingPair]:
    """Synthesize multi-step training pairs for product return types.

    For each product type (e.g., Nat × Int), pairs the `constructor` tactic
    (which splits the goal) with individual sub-goal tactics drawn from
    existing single-step expressions.

    This teaches the policy to:
      1. Recognize product goals and propose `constructor`
      2. Solve each sub-goal with the appropriate `exact` tactic
    """
    # Group existing pairs by (input_type, return_type)
    by_sig: dict[tuple[str, str], list[TrainingPair]] = {}
    for p in pairs:
        key = (p.input_type, p.return_type)
        by_sig.setdefault(key, []).append(p)

    multistep: list[TrainingPair] = []

    for product_type, (left_type, right_type) in PRODUCT_TYPES.items():
        for input_type in ["List Int", "Nat", "List (List Int)"]:
            left_pairs = by_sig.get((input_type, left_type), [])
            right_pairs = by_sig.get((input_type, right_type), [])

            if not left_pairs or not right_pairs:
                continue

            # Build the param prefix from input_type
            if input_type == "List (List Int)":
                param = "xss : List (List Int)"
            elif input_type == "Nat":
                param = "n : Nat"
            elif input_type == "List Int, List Int":
                param = "xs : List Int\nys : List Int"
            else:
                param = "xs : List Int"

            # constructor tactic for the product goal
            product_state = f"{param}\n⊢ {product_type}"
            multistep.append(TrainingPair(
                state=product_state,
                tactic="constructor",
                source="multistep",
                task_name=f"constructor_{product_type}",
                return_type=product_type,
                input_type=input_type,
            ))

            # Sample sub-goal pairs (cap to avoid explosion)
            import random
            rng = random.Random(42)
            left_sample = rng.sample(left_pairs, min(20, len(left_pairs)))
            right_sample = rng.sample(right_pairs, min(20, len(right_pairs)))

            for lp in left_sample:
                # Left sub-goal state after constructor — with case prefix
                # Pantograph produces: "case fst\n<params>\n⊢ <type>\ncase snd\n..."
                # The policy sees one sub-goal at a time after first is solved
                left_state_prefixed = f"case fst\n{param}\n⊢ {left_type}"
                left_state_plain = f"{param}\n⊢ {left_type}"
                for state_str, suffix in [(left_state_prefixed, "_pfx"), (left_state_plain, "")]:
                    multistep.append(TrainingPair(
                        state=state_str,
                        tactic=lp.tactic,
                        source="multistep",
                        task_name=f"subgoal_left_{lp.task_name}{suffix}",
                        return_type=left_type,
                        input_type=input_type,
                    ))

            for rp in right_sample:
                right_state_prefixed = f"case snd\n{param}\n⊢ {right_type}"
                right_state_plain = f"{param}\n⊢ {right_type}"
                for state_str, suffix in [(right_state_prefixed, "_pfx"), (right_state_plain, "")]:
                    multistep.append(TrainingPair(
                        state=state_str,
                        tactic=rp.tactic,
                        source="multistep",
                        task_name=f"subgoal_right_{rp.task_name}{suffix}",
                        return_type=right_type,
                        input_type=input_type,
                    ))

    if verbose:
        print(f"  Synthesized {len(multistep)} multi-step pairs")
    return multistep


# ---------------------------------------------------------------------------
# Data augmentation
# ---------------------------------------------------------------------------

_CASE_PREFIX_RE = re.compile(r'^(case \w+\n)')


def augment_pairs(
    pairs: list[TrainingPair],
    n_variants: int = 3,
    seed: int = 42,
) -> list[TrainingPair]:
    """Create variable name variants for each training pair.

    Multi-type aware: uses appropriate variant names for each input type.
    Handles `case <name>\n` prefix: strips it before augmenting, re-prepends after.
    This teaches the model that variable names are arbitrary.
    """
    rng = random.Random(seed)
    augmented: list[TrainingPair] = []

    for pair in pairs:
        input_type = pair.input_type

        # Strip case prefix if present (e.g., "case fst\n" or "case snd\n")
        case_prefix = ""
        state_body = pair.state
        m = _CASE_PREFIX_RE.match(state_body)
        if m:
            case_prefix = m.group(1)
            state_body = state_body[len(case_prefix):]

        if input_type == "List (List Int)":
            # Replace xss with nested-list variants
            variants = rng.sample(VAR_VARIANTS_XSS, min(n_variants, len(VAR_VARIANTS_XSS)))
            for var_name in variants:
                new_body = state_body.replace("xss", var_name)
                new_tactic = pair.tactic.replace("xss", var_name)
                if new_body != state_body or new_tactic != pair.tactic:
                    augmented.append(TrainingPair(
                        state=case_prefix + new_body, tactic=new_tactic, source="augmented",
                        task_name=pair.task_name, return_type=pair.return_type,
                        input_type=pair.input_type,
                    ))
        elif input_type == "Nat":
            # Replace n with nat variants
            variants = rng.sample(VAR_VARIANTS_N, min(n_variants, len(VAR_VARIANTS_N)))
            for var_name in variants:
                # Use word boundary replacement to avoid replacing 'n' inside other words
                new_body = re.sub(r'\bn\b', var_name, state_body)
                new_tactic = re.sub(r'\bn\b', var_name, pair.tactic)
                if new_body != state_body or new_tactic != pair.tactic:
                    augmented.append(TrainingPair(
                        state=case_prefix + new_body, tactic=new_tactic, source="augmented",
                        task_name=pair.task_name, return_type=pair.return_type,
                        input_type=pair.input_type,
                    ))
        elif input_type == "List Int, List Int":
            # Replace xs/ys pair with two-arg variants
            variants = rng.sample(VAR_VARIANTS_TWO_ARG, min(n_variants, len(VAR_VARIANTS_TWO_ARG)))
            for var_a, var_b in variants:
                new_body = state_body.replace("xs", var_a).replace("ys", var_b)
                new_tactic = pair.tactic.replace("xs", var_a).replace("ys", var_b)
                if new_body != state_body or new_tactic != pair.tactic:
                    augmented.append(TrainingPair(
                        state=case_prefix + new_body, tactic=new_tactic, source="augmented",
                        task_name=pair.task_name, return_type=pair.return_type,
                        input_type=pair.input_type,
                    ))
        else:
            # Default: List Int — replace xs with single-arg variants
            variants = rng.sample(VAR_VARIANTS_XS, min(n_variants, len(VAR_VARIANTS_XS)))
            for var_name in variants:
                new_body = state_body.replace("xs", var_name)
                new_tactic = pair.tactic.replace("xs", var_name)
                if new_body != state_body or new_tactic != pair.tactic:
                    augmented.append(TrainingPair(
                        state=case_prefix + new_body, tactic=new_tactic, source="augmented",
                        task_name=pair.task_name, return_type=pair.return_type,
                        input_type=pair.input_type,
                    ))

    return augmented


# ---------------------------------------------------------------------------
# Output + split
# ---------------------------------------------------------------------------

def write_training_data(
    pairs: list[TrainingPair],
    train_path: Path,
    val_path: Path,
    split_ratio: float = 0.8,
    seed: int = 42,
) -> tuple[int, int]:
    """Write train/val JSONL files."""
    rng = random.Random(seed)

    # Deduplicate by (state, tactic) — keep first occurrence
    seen: set[tuple[str, str]] = set()
    unique: list[TrainingPair] = []
    for p in pairs:
        key = (p.state, p.tactic)
        if key not in seen:
            seen.add(key)
            unique.append(p)

    # Shuffle and split
    rng.shuffle(unique)
    split_idx = int(len(unique) * split_ratio)
    train_pairs = unique[:split_idx]
    val_pairs = unique[split_idx:]

    # Write
    train_path.parent.mkdir(parents=True, exist_ok=True)
    for path, data in [(train_path, train_pairs), (val_path, val_pairs)]:
        with open(path, "w") as f:
            for p in data:
                record = {"state": p.state, "tactic": p.tactic}
                f.write(json.dumps(record) + "\n")

    return len(train_pairs), len(val_pairs)


def main():
    parser = argparse.ArgumentParser(description="Collect self-play training data")
    parser.add_argument("--expressions", type=str, default="synth/expressions_v2.jsonl",
                        help="All validated expressions JSONL (from Phase 1)")
    parser.add_argument("--tasks", type=str, default="synth/synth_tasks.jsonl",
                        help="Generated tasks JSONL (from Phase 2)")
    parser.add_argument("--lean-project", type=str, default=None)
    parser.add_argument("--output-train", type=str, default="train/synth_v2_train.jsonl")
    parser.add_argument("--output-val", type=str, default="train/synth_v2_val.jsonl")
    parser.add_argument("--rollouts", type=int, default=500,
                        help="MCTS rollouts per benchmark task")
    parser.add_argument("--max-k", type=int, default=200,
                        help="Max tactics per expansion (avoids REPL crash)")
    parser.add_argument("--split", type=float, default=0.8,
                        help="Train/val split ratio")
    parser.add_argument("--n-augment", type=int, default=3,
                        help="Number of variable name variants per pair")
    parser.add_argument("--skip-mcts", action="store_true",
                        help="Skip MCTS on benchmark tasks (reference solutions only)")
    parser.add_argument("--tasks-only", action="store_true",
                        help="Use only selected tasks (200) instead of all expressions (568)")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Skip oracle verification of reference solutions")
    parser.add_argument("--multi-step", action="store_true",
                        help="Synthesize multi-step pairs for product return types")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent.parent
    expressions_path = script_dir / args.expressions
    tasks_path = script_dir / args.tasks
    lean_project = Path(args.lean_project) if args.lean_project else script_dir / "env" / "FormalNav"
    train_path = script_dir / args.output_train
    val_path = script_dir / args.output_val

    t0 = time.time()
    all_pairs: list[TrainingPair] = []

    # 1. Collect from expressions/tasks
    if args.tasks_only:
        ref_pairs = collect_from_references(
            tasks_path, lean_project, verify=not args.skip_verify, verbose=args.verbose,
        )
    else:
        ref_pairs = collect_from_expressions(expressions_path, verbose=args.verbose)
    all_pairs.extend(ref_pairs)

    # 2. Collect from MCTS on benchmark T2 tasks
    if not args.skip_mcts:
        mcts_pairs = collect_from_mcts(
            rollouts=args.rollouts, max_k=args.max_k, verbose=args.verbose,
        )
        all_pairs.extend(mcts_pairs)

    # 3. Synthesize multi-step pairs for product types
    if args.multi_step:
        ms_pairs = synthesize_multistep_pairs(all_pairs, verbose=args.verbose)
        all_pairs.extend(ms_pairs)
        print(f"  Multi-step: +{len(ms_pairs)} pairs")

    print(f"\nPre-augmentation: {len(all_pairs)} pairs")
    from collections import Counter
    src_dist = Counter(p.source for p in all_pairs)
    rt_dist = Counter(p.return_type for p in all_pairs)
    print(f"  Sources: {dict(src_dist)}")
    print(f"  Return types: {dict(rt_dist)}")

    # 3. Augment
    if args.n_augment > 0:
        aug_pairs = augment_pairs(all_pairs, n_variants=args.n_augment, seed=args.seed)
        all_pairs.extend(aug_pairs)
        print(f"  Augmented: +{len(aug_pairs)} pairs → {len(all_pairs)} total")

    # 4. Write train/val split
    n_train, n_val = write_training_data(
        all_pairs, train_path, val_path, split_ratio=args.split, seed=args.seed,
    )

    elapsed = time.time() - t0
    print(f"\nOutput:")
    print(f"  Train: {train_path} ({n_train} pairs)")
    print(f"  Val:   {val_path} ({n_val} pairs)")
    print(f"  Total unique: {n_train + n_val}")
    print(f"  Elapsed: {elapsed:.1f}s")

    # Write stats
    stats = {
        "total_raw": len(all_pairs),
        "total_unique": n_train + n_val,
        "n_train": n_train,
        "n_val": n_val,
        "sources": dict(src_dist),
        "return_types": dict(rt_dist),
        "n_augmented": len(aug_pairs) if args.n_augment > 0 else 0,
        "elapsed_seconds": round(elapsed, 1),
    }
    stats_path = script_dir / "synth" / "selfplay_stats.json"
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"  Stats: {stats_path}")


if __name__ == "__main__":
    main()
