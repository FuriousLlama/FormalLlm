"""Phase 2.3 generation sanity check — CLI triage for S30.

Sampling-only generation test used as first-pass triage before
committing to a full MCTS benchmark sweep. In S29, this methodology
(8 held-out tasks x 8 samples x temp 0.8, ~2 min wall) predicted the
0/12 primary-metric benchmark result 40x faster than the 5-hour sweep.

S30 uses this CLI before every Phase A/B/D benchmark sweep:
  - Phase A: smoke-test zero-shot base-model capability
  - Phase B: skip the sweep if required-pattern coverage is zero
  - Phase D: triage adapter/config before 20+ hour runs on bigger bases

The verdict is deliberately conservative: a tier PASSES if every
required structural pattern appears in at least one generation
across all samples. The rate itself is ignored — the question is
"is the pattern generatable at all?", not "how often".

Usage:
    # Base model only (S30 Phase A control)
    python -m lean_mcts.benchmark.sanity_check \\
        --base-model Qwen/Qwen2.5-Coder-1.5B \\
        --tier held_out_12

    # With adapter (S30 Phase D triage)
    python -m lean_mcts.benchmark.sanity_check \\
        --base-model Qwen/Qwen2.5-Coder-1.5B \\
        --adapter lean_mcts/train/diverse_adapter/S29-A \\
        --tier held_out_12 \\
        --out sprint/benchmark/results/sanity_S29-A.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

from lean_mcts.benchmark.inspect_generations import (
    build_goal_state,
    classify,
    generate,
    has_comment,
    load_model,
)
from lean_mcts.benchmark.tasks import get_tasks


# Pattern-presence predicates used by the verdict — independent of the
# single-label `classify()` function because a single tactic can contain
# multiple required patterns at once (e.g. `let rec ... match xs with`).
# The classifier is still used for the human-readable distribution table;
# the verdict uses these predicates.
def _has_let_rec(tactic: str) -> bool:
    return "let rec" in tactic


def _has_match_list(tactic: str) -> bool:
    # Cons/nil match on a list — needs both `[] =>` and `::`
    return "match" in tactic and "[] =>" in tactic and "::" in tactic


def _has_multi_step_have(tactic: str) -> bool:
    # `have ... ; exact ...` or `have ... := ... ; ...`
    return "have " in tactic


def _has_fold(tactic: str) -> bool:
    return (
        "foldl" in tactic
        or "foldr" in tactic
        or ".fold" in tactic
        or "List.rec" in tactic
    )


def _has_method_chain(tactic: str) -> bool:
    # A `.methodName(` or `.methodName ` pattern (2+ chain steps)
    return len(re.findall(r"\.\w+", tactic)) >= 2


PATTERN_PREDICATES: dict[str, callable] = {
    "let_rec": _has_let_rec,
    "match_list": _has_match_list,
    "multi_step_have": _has_multi_step_have,
    "fold": _has_fold,
    "method_chain": _has_method_chain,
}


# Tier -> structural patterns each tier's tasks are known to require.
# Sourced from docs/implementation/sprint29/lessons_learned.md Phase 2.3:
#   "Every hard-holdout task needs at least one of `let rec`, `match`
#    on multi-arg, or multi-step `have ... exact`."
# For diag-holdout, the S27 failure taxonomy identifies method-chaining
# and fold-based two-pass as the primary missing patterns.
TIER_REQUIRED_PATTERNS: dict[str, list[str]] = {
    "hard_holdout": ["let_rec", "match_list", "multi_step_have"],
    "diag_holdout": ["method_chain", "fold", "match_list"],
    "held_out_12": ["let_rec", "match_list", "multi_step_have", "fold"],
}

# Default task set mirrors S29 Phase 2.3's 8-task held-out-bucket
# selection when no explicit tier/tasks argument is given.
DEFAULT_TIER = "held_out_12"


def load_base_only(base_model: str):
    """Load base model without any LoRA adapter (Phase A / Phase B)."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        base_model, torch_dtype=torch.bfloat16, trust_remote_code=True,
    )
    model.to("cuda")
    model.eval()
    return model, tokenizer


def sample_task(model, tokenizer, task, n: int, temperature: float):
    state = build_goal_state(task)
    candidates = generate(model, tokenizer, state, n=n, temperature=temperature)
    unique = list(dict.fromkeys(candidates))
    rows = []
    for tactic in unique:
        present = sorted(p for p, pred in PATTERN_PREDICATES.items() if pred(tactic))
        rows.append({
            "tactic": tactic,
            "kind": classify(tactic),
            "patterns_present": present,
            "has_comment": has_comment(tactic),
        })
    return {
        "name": task.name,
        "category": getattr(task, "category", None),
        "state": state,
        "n_unique": len(unique),
        "n_sampled": len(candidates),
        "generations": rows,
    }


def summarize(task_rows: list[dict]) -> dict:
    kinds = Counter()
    pattern_counts = Counter({p: 0 for p in PATTERN_PREDICATES})
    total = 0
    with_comments = 0
    for tr in task_rows:
        for g in tr["generations"]:
            kinds[g["kind"]] += 1
            for p in g["patterns_present"]:
                pattern_counts[p] += 1
            total += 1
            if g["has_comment"]:
                with_comments += 1
    return {
        "total_samples": total,
        "with_comments": with_comments,
        "comment_rate": round(with_comments / total, 3) if total else 0.0,
        "kind_counts": dict(kinds),
        "kind_rates": {
            k: round(c / total, 3) if total else 0.0 for k, c in kinds.items()
        },
        "pattern_counts": dict(pattern_counts),
        "pattern_rates": {
            p: round(c / total, 3) if total else 0.0 for p, c in pattern_counts.items()
        },
    }


def verdict(tier: str, summary: dict) -> dict:
    required = TIER_REQUIRED_PATTERNS.get(tier, [])
    if not required:
        return {"tier": tier, "status": "SKIP", "reason": "no required patterns defined for tier"}
    pattern_counts = summary["pattern_counts"]
    missing = [p for p in required if pattern_counts.get(p, 0) == 0]
    if missing:
        return {
            "tier": tier,
            "status": "FAIL",
            "required": required,
            "missing": missing,
            "reason": (
                f"zero generations of {missing} — tier's known-required "
                "patterns never appeared. Benchmark sweep will likely return 0/N."
            ),
        }
    return {
        "tier": tier,
        "status": "PASS",
        "required": required,
        "reason": "every required pattern appeared at least once",
    }


def print_human_summary(config: dict, task_rows: list[dict], summary: dict, v: dict):
    print()
    print("=" * 72)
    print(f"sanity_check :: {config['base_model']}")
    if config.get("adapter"):
        print(f"adapter      :: {config['adapter']}")
    print(f"tier         :: {config['tier']}  ({config['n_tasks']} tasks)")
    print(f"n x temp     :: {config['n']} samples @ T={config['temperature']}")
    print(f"wall_sec     :: {config['wall_sec']:.1f}")
    print("-" * 72)
    for tr in task_rows:
        unique = tr["n_unique"]
        total = tr["n_sampled"]
        kinds_here = Counter(g["kind"] for g in tr["generations"])
        top = ", ".join(f"{k}={c}" for k, c in kinds_here.most_common())
        print(f"  {tr['name']:<28} {unique}/{total} unique  [{top}]")
    print("-" * 72)
    print(f"total samples: {summary['total_samples']}  "
          f"(comments: {summary['with_comments']}, "
          f"rate {summary['comment_rate']:.1%})")
    print("kind distribution (single-label):")
    for k, c in sorted(summary["kind_counts"].items(), key=lambda x: -x[1]):
        rate = summary["kind_rates"][k]
        print(f"  {k:<18} {c:>4}  ({rate:.1%})")
    print("pattern presence (multi-label — verdict basis):")
    for p, c in sorted(summary["pattern_counts"].items(), key=lambda x: -x[1]):
        rate = summary["pattern_rates"][p]
        flag = "REQ" if p in TIER_REQUIRED_PATTERNS.get(config["tier"], []) else "   "
        print(f"  [{flag}] {p:<18} {c:>4}  ({rate:.1%})")
    print("-" * 72)
    print(f"verdict: {v['status']}   tier={v['tier']}")
    if v["status"] == "FAIL":
        print(f"  missing: {v['missing']}")
    print(f"  reason: {v['reason']}")
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-Coder-1.5B")
    parser.add_argument("--adapter", default=None,
                        help="Optional LoRA adapter path. Omit for base-only.")
    parser.add_argument("--tier", default=DEFAULT_TIER,
                        help=f"Task tier (default: {DEFAULT_TIER}). "
                             "See lean_mcts.benchmark.tasks.get_tasks.")
    parser.add_argument("--tasks", default=None,
                        help="Comma-separated task names — overrides --tier.")
    parser.add_argument("--n", type=int, default=8,
                        help="Candidates per task (S29 Phase 2.3 default: 8)")
    parser.add_argument("--temperature", type=float, default=0.8,
                        help="Sampling temperature (S29 Phase 2.3 default: 0.8)")
    parser.add_argument("--out", default=None,
                        help="Optional JSON output path. Directory created if missing.")
    args = parser.parse_args()

    if args.tasks:
        from lean_mcts.benchmark.tasks import get_task
        tasks = [get_task(n) for n in args.tasks.split(",")]
        tier_label = "custom"
    else:
        tasks = list(get_tasks(args.tier))
        tier_label = args.tier

    if not tasks:
        print(f"[sanity_check] no tasks for tier={tier_label}", file=sys.stderr)
        sys.exit(2)

    print(f"[sanity_check] loading {args.base_model}"
          + (f" + {args.adapter}" if args.adapter else " (base only)"))
    if args.adapter:
        model, tokenizer = load_model(args.base_model, args.adapter)
    else:
        model, tokenizer = load_base_only(args.base_model)

    t0 = time.time()
    task_rows = []
    for task in tasks:
        task_rows.append(sample_task(model, tokenizer, task, args.n, args.temperature))
    wall = time.time() - t0

    summary = summarize(task_rows)
    v = verdict(tier_label, summary)

    config = {
        "base_model": args.base_model,
        "adapter": args.adapter,
        "tier": tier_label,
        "n_tasks": len(tasks),
        "n": args.n,
        "temperature": args.temperature,
        "wall_sec": wall,
    }

    print_human_summary(config, task_rows, summary, v)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "config": config,
            "task_rows": task_rows,
            "summary": summary,
            "verdict": v,
        }, indent=2))
        print(f"[sanity_check] wrote {out_path}")

    sys.exit(0 if v["status"] != "FAIL" else 1)


if __name__ == "__main__":
    main()
