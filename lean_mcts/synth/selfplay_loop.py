"""Automated self-play loop for iterative model improvement.

Orchestrates: benchmark → extract pairs → merge → retrain → repeat.

Usage:
    # Run 3 rounds of self-play (default settings)
    python -m lean_mcts.synth.selfplay_loop --rounds 3

    # Custom rollouts and seed
    python -m lean_mcts.synth.selfplay_loop --rounds 5 --rollouts 50 --seed 1337

    # Resume from a previous run (skips completed rounds)
    python -m lean_mcts.synth.selfplay_loop --rounds 5 --resume selfplay_20260316_120000

    # Dry run — show what would happen without running anything
    python -m lean_mcts.synth.selfplay_loop --rounds 3 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

LEAN_MCTS = Path(__file__).resolve().parent.parent
TRAIN_DIR = LEAN_MCTS / "train"
SYNTH_DIR = LEAN_MCTS / "synth"


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _run_benchmark(
    round_dir: Path,
    rollouts: int,
    tiers: list[str],
    adapter_path: Path,
    tiered: bool = False,
) -> Path:
    """Run benchmark across specified tiers, saving results to round_dir.

    Returns the output directory containing result JSONs.
    """
    results_dir = round_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    for tier in tiers:
        _log(f"  Benchmark tier={tier}, rollouts={rollouts}{' (tiered)' if tiered else ''}")
        cmd = [
            sys.executable, "-u", "-m", "lean_mcts.benchmark.run",
            "--policy", "hybrid-gc",
            "--gc-adapter", str(adapter_path),
            "--tier", tier,
            "--rollouts", str(rollouts),
            "--daemon",
            "--no-early-exit",  # harvest ALL verified solutions
            "--output-dir", str(results_dir),
        ]
        if tiered:
            cmd.append("--tiered")
        # Use higher rollouts for hard/recurrence/multistep tiers
        if tier in ("hard", "recurrence", "multistep"):
            cmd[cmd.index("--rollouts") + 1] = str(max(rollouts, 200))

        log_file = results_dir / f"bench_{tier}.log"
        t0 = time.time()
        with open(log_file, "w") as lf:
            result = subprocess.run(
                cmd, cwd=str(LEAN_MCTS.parent),
                stdout=lf, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        elapsed = time.time() - t0
        _log(f"  Tier {tier} done in {elapsed:.0f}s (exit={result.returncode})")

        if result.returncode != 0:
            _log(f"  WARNING: benchmark tier={tier} failed (exit={result.returncode})")
            try:
                lines = log_file.read_text().strip().split("\n")
                for line in lines[-5:]:
                    _log(f"    {line}")
            except Exception:
                pass

    return results_dir


def _extract_pairs(results_dir: Path, existing_train: Path) -> list:
    """Extract self-play pairs from benchmark results, deduped against existing data."""
    # Import here to avoid circular imports at module level
    sys.path.insert(0, str(LEAN_MCTS.parent))
    from lean_mcts.synth.collect_selfplay_v2 import extract_selfplay_pairs

    pairs = extract_selfplay_pairs(results_dir)
    _log(f"  Extracted {len(pairs)} raw self-play pairs")

    if not pairs:
        return []

    # Deduplicate against existing training data
    existing_keys = set()
    if existing_train.exists():
        with open(existing_train) as f:
            for line in f:
                entry = json.loads(line)
                key = f"{entry['state']}|{entry['tactic']}"
                existing_keys.add(key)

    new_pairs = [p for p in pairs if f"{p.state}|{p.tactic}" not in existing_keys]
    _log(f"  After dedup: {len(new_pairs)} new pairs (removed {len(pairs) - len(new_pairs)} existing)")

    return new_pairs


def _merge_and_write(
    new_pairs: list,
    existing_train: Path,
    existing_val: Path,
    output_train: Path,
    output_val: Path,
    split_seed: int = 42,
) -> tuple[int, int]:
    """Merge new pairs into existing training data, re-split, and write."""
    from lean_mcts.synth.collect_selfplay import TrainingPair, write_training_data

    # Load existing pairs
    all_pairs = []
    for path in [existing_train, existing_val]:
        if path.exists():
            with open(path) as f:
                for line in f:
                    entry = json.loads(line)
                    all_pairs.append(TrainingPair(
                        state=entry["state"],
                        tactic=entry["tactic"],
                        source=entry.get("source", "existing"),
                        task_name=entry.get("task_name", "unknown"),
                        return_type=entry.get("return_type", ""),
                        input_type=entry.get("input_type", ""),
                    ))

    # Add new pairs
    all_pairs.extend(new_pairs)

    # write_training_data handles dedup + shuffle + split
    n_train, n_val = write_training_data(
        all_pairs, output_train, output_val,
        split_ratio=0.8, seed=split_seed,
    )
    return n_train, n_val


def _retrain(
    train_path: Path,
    val_path: Path,
    output_dir: Path,
    seed: int,
) -> Path:
    """Retrain LoRA adapter on merged data. Returns adapter path."""
    _log(f"  Training seed={seed} → {output_dir.name}")
    cmd = [
        sys.executable, "-u", "-m", "lean_mcts.train.pretrain",
        "--data", str(train_path),
        "--val", str(val_path),
        "--output", str(output_dir),
        "--seed", str(seed),
        "--epochs", "3",
        "--patience", "2",
        "--log-every", "50",
    ]
    # Redirect training output to a log file to avoid pipe buffer deadlocks
    # when the parent process is itself being captured
    log_file = output_dir.parent / f"{output_dir.name}_train.log"
    t0 = time.time()
    with open(log_file, "w") as lf:
        result = subprocess.run(
            cmd, cwd=str(LEAN_MCTS.parent),
            stdout=lf, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    elapsed = time.time() - t0
    _log(f"  Training done in {elapsed / 60:.1f} min (exit={result.returncode})")
    _log(f"  Training log: {log_file}")

    if result.returncode != 0:
        # Print last 10 lines of training log for diagnosis
        try:
            lines = log_file.read_text().strip().split("\n")
            for line in lines[-10:]:
                _log(f"    {line}")
        except Exception:
            pass
        raise RuntimeError(f"Training failed with exit code {result.returncode}")

    return output_dir


def _read_val_loss(adapter_dir: Path) -> float | None:
    """Read best val_loss from training_stats.json."""
    stats_path = adapter_dir / "training_stats.json"
    if stats_path.exists():
        with open(stats_path) as f:
            stats = json.load(f)
        return stats.get("best_val_loss")
    return None


def run_selfplay_loop(
    rounds: int = 3,
    rollouts: int = 50,
    seed: int = 1337,
    tiers: list[str] | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
    resume: str | None = None,
    tiered: bool = False,
):
    """Run the full self-play loop."""
    if tiers is None:
        tiers = ["all+v2+recurrence"]

    if run_id is None:
        run_id = f"selfplay_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    run_dir = SYNTH_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Track convergence
    log_path = run_dir / "convergence.json"
    convergence = []
    if resume and log_path.exists():
        with open(log_path) as f:
            convergence = json.load(f)

    # Current active adapter
    active_adapter = TRAIN_DIR / "goal_cond_adapter"
    # Current training data
    current_train = TRAIN_DIR / "goal_cond_train.jsonl"
    current_val = TRAIN_DIR / "goal_cond_val.jsonl"

    start_round = len(convergence)  # resume from where we left off

    _log(f"Self-play loop: {rounds} rounds, rollouts={rollouts}, seed={seed}")
    _log(f"Run dir: {run_dir}")
    _log(f"Starting from round {start_round + 1}")
    _log(f"Active adapter: {active_adapter}")

    # Count initial training data
    initial_train = sum(1 for _ in open(current_train)) if current_train.exists() else 0
    initial_val = sum(1 for _ in open(current_val)) if current_val.exists() else 0
    _log(f"Initial data: {initial_train} train + {initial_val} val = {initial_train + initial_val} total")

    if dry_run:
        _log("DRY RUN — would execute:")
        for r in range(start_round, rounds):
            _log(f"  Round {r + 1}: benchmark({tiers}) → extract → merge → retrain(seed={seed})")
        return

    for round_idx in range(start_round, rounds):
        round_num = round_idx + 1
        round_dir = run_dir / f"round_{round_num}"
        round_dir.mkdir(parents=True, exist_ok=True)

        _log(f"\n{'='*60}")
        _log(f"ROUND {round_num}/{rounds}")
        _log(f"{'='*60}")

        t0_round = time.time()

        # Step 1: Benchmark with current adapter
        _log("Step 1: Running benchmark...")
        results_dir = _run_benchmark(round_dir, rollouts, tiers, active_adapter, tiered=tiered)

        # Step 2: Extract self-play pairs
        _log("Step 2: Extracting self-play pairs...")
        new_pairs = _extract_pairs(results_dir, current_train)

        round_record = {
            "round": round_num,
            "new_pairs": len(new_pairs),
            "rollouts": rollouts,
            "seed": seed,
            "tiers": tiers,
        }

        if len(new_pairs) == 0:
            _log("No new pairs found — model has converged on these tiers!")
            round_record["status"] = "converged"
            round_record["elapsed_min"] = round((time.time() - t0_round) / 60, 1)
            convergence.append(round_record)
            with open(log_path, "w") as f:
                json.dump(convergence, f, indent=2)
            _log("Stopping early — no new training signal.")
            break

        # Save new pairs for inspection
        pairs_path = round_dir / "new_pairs.jsonl"
        with open(pairs_path, "w") as f:
            for p in new_pairs:
                entry = {
                    "state": p.state,
                    "tactic": p.tactic,
                    "source": p.source,
                    "task_name": p.task_name,
                    "return_type": p.return_type,
                    "input_type": p.input_type,
                }
                f.write(json.dumps(entry) + "\n")
        _log(f"  Saved {len(new_pairs)} new pairs to {pairs_path.name}")

        # Per-task breakdown
        from collections import Counter
        task_counts = Counter(p.task_name for p in new_pairs)
        for task, count in sorted(task_counts.items()):
            tactic = next(p.tactic for p in new_pairs if p.task_name == task)
            _log(f"    {task}: {count} pairs — {tactic[:70]}")

        # Step 3: Merge into training data
        _log("Step 3: Merging training data...")
        merged_train = round_dir / "merged_train.jsonl"
        merged_val = round_dir / "merged_val.jsonl"
        n_train, n_val = _merge_and_write(
            new_pairs, current_train, current_val,
            merged_train, merged_val,
            split_seed=42,
        )
        _log(f"  Merged data: {n_train} train + {n_val} val = {n_train + n_val} total")

        # Step 4: Backup current adapter and retrain
        _log("Step 4: Retraining model...")
        backup_dir = round_dir / "adapter_backup"
        if active_adapter.exists():
            shutil.copytree(active_adapter, backup_dir)
            _log(f"  Backed up adapter → {backup_dir.name}")

        new_adapter_dir = round_dir / f"adapter_seed{seed}"
        _retrain(merged_train, merged_val, new_adapter_dir, seed)

        new_val_loss = _read_val_loss(new_adapter_dir)
        old_val_loss = _read_val_loss(active_adapter)
        _log(f"  val_loss: {old_val_loss} → {new_val_loss}")

        # Copy new adapter to active location
        if active_adapter.exists():
            shutil.rmtree(active_adapter)
        shutil.copytree(new_adapter_dir, active_adapter)
        _log(f"  Installed new adapter → {active_adapter.name}")

        # Update training data files
        shutil.copy2(merged_train, current_train)
        shutil.copy2(merged_val, current_val)
        _log(f"  Updated training data: {current_train.name}, {current_val.name}")

        elapsed_round = (time.time() - t0_round) / 60
        round_record.update({
            "status": "completed",
            "n_train": n_train,
            "n_val": n_val,
            "val_loss_before": old_val_loss,
            "val_loss_after": new_val_loss,
            "elapsed_min": round(elapsed_round, 1),
        })
        convergence.append(round_record)

        # Save convergence log after each round (crash-safe)
        with open(log_path, "w") as f:
            json.dump(convergence, f, indent=2)

        _log(f"Round {round_num} complete in {elapsed_round:.1f} min")

    # Final summary
    _log(f"\n{'='*60}")
    _log("SELF-PLAY COMPLETE")
    _log(f"{'='*60}")
    total_new = sum(r["new_pairs"] for r in convergence)
    _log(f"Rounds completed: {len(convergence)}")
    _log(f"Total new pairs: {total_new}")
    _log(f"Final data size: {sum(1 for _ in open(current_train))} train + {sum(1 for _ in open(current_val))} val")
    final_val = _read_val_loss(active_adapter)
    _log(f"Final val_loss: {final_val}")

    _log(f"\nConvergence log: {log_path}")
    for r in convergence:
        _log(f"  Round {r['round']}: +{r['new_pairs']} pairs, "
             f"val_loss {r.get('val_loss_before', '?')} → {r.get('val_loss_after', '?')}, "
             f"{r.get('elapsed_min', '?')} min")


def main():
    parser = argparse.ArgumentParser(
        description="Automated self-play loop: benchmark → extract → merge → retrain → repeat"
    )
    parser.add_argument("--rounds", type=int, default=3,
                        help="Number of self-play rounds (default: 3)")
    parser.add_argument("--rollouts", type=int, default=50,
                        help="Rollouts per task for T2/V2 tiers (hard/recurrence use max(rollouts, 200))")
    parser.add_argument("--seed", type=int, default=1337,
                        help="Training seed for reproducibility (default: 1337)")
    parser.add_argument("--tiers", type=str, default="all+v2+recurrence",
                        help="Comma-separated tiers to benchmark (default: all+v2+recurrence)")
    parser.add_argument("--run-id", type=str, default=None,
                        help="Run identifier (default: selfplay_YYYYMMDD_HHMMSS)")
    parser.add_argument("--resume", type=str, default=None,
                        help="Resume a previous run by ID (e.g., selfplay_20260316_120000)")
    parser.add_argument("--tiered", action="store_true",
                        help="Use tiered inference (50→200→max rollouts) to save compute")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without running anything")
    args = parser.parse_args()

    tiers = [t.strip() for t in args.tiers.split(",")]

    # If resuming, use that as the run_id
    run_id = args.resume or args.run_id

    run_selfplay_loop(
        rounds=args.rounds,
        rollouts=args.rollouts,
        seed=args.seed,
        tiers=tiers,
        run_id=run_id,
        dry_run=args.dry_run,
        resume=args.resume,
        tiered=args.tiered,
    )


if __name__ == "__main__":
    main()
