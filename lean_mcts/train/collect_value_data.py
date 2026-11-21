"""Collect (state, outcome) data for value network training.

Generates value training data from the expression space. Each expression
provides a solved state (outcome=1.0). Intermediate states (e.g., after
`constructor` in product type tasks) get outcome based on whether sub-goals
are solvable with known expressions. Negative examples come from states with
no known solutions.

Usage:
    python train/collect_value_data.py \
        --expressions synth/expressions_v2.jsonl \
        --output train/value_data.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Collect value training data")
    parser.add_argument("--expressions", type=str, default="synth/expressions_v2.jsonl")
    parser.add_argument("--output", type=str, default="train/value_data.jsonl")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent.parent
    expr_path = script_dir / args.expressions
    output_path = script_dir / args.output

    rng = random.Random(args.seed)

    # Load all expressions
    with open(expr_path) as f:
        exprs = [json.loads(line) for line in f]
    print(f"Loaded {len(exprs)} expressions")

    # Group by state (many expressions per state = high solvability)
    states_with_solutions: dict[str, int] = {}  # state → n_solutions
    for e in exprs:
        state = e["state"]
        states_with_solutions[state] = states_with_solutions.get(state, 0) + 1

    # Positive examples: states that have known solutions
    pairs = []
    for state, n_sol in states_with_solutions.items():
        # Outcome scaled by solution density (more solutions = easier state)
        outcome = min(1.0, n_sol / 5.0)  # cap at 1.0 for states with 5+ solutions
        pairs.append({"state": state, "outcome": outcome})

    print(f"Positive states: {len(pairs)}")

    # Generate negative examples: mutated states that likely have no solution
    # Strategy 1: Change return type in valid states
    neg_count = 0
    seen = set(states_with_solutions.keys())

    # Collect all unique state structures
    return_types = set()
    param_prefixes = set()
    for e in exprs:
        return_types.add(e["return_type"])
        state_lines = e["state"].split("\n")
        # Everything before the last line (⊢ ...) is the param prefix
        if len(state_lines) > 1:
            param_prefixes.add("\n".join(state_lines[:-1]))

    # Create negative pairs by mismatching param with wrong return type
    for prefix in param_prefixes:
        for rt in return_types:
            neg_state = f"{prefix}\n⊢ {rt}"
            if neg_state not in seen:
                pairs.append({"state": neg_state, "outcome": 0.0})
                seen.add(neg_state)
                neg_count += 1

    # Strategy 2: Nonsensical states (unlikely to be solvable)
    weird_types = [
        "List (List (List Int))", "Nat × Nat × Nat",
        "List Bool", "String", "Float",
    ]
    for prefix in param_prefixes:
        for wt in weird_types:
            neg_state = f"{prefix}\n⊢ {wt}"
            if neg_state not in seen:
                pairs.append({"state": neg_state, "outcome": 0.0})
                seen.add(neg_state)
                neg_count += 1

    print(f"Negative states: {neg_count}")

    # Multi-step intermediate states: product types with constructor
    # After `constructor` on ⊢ A × B, you get ⊢ A then ⊢ B
    # Both sub-goals are solvable if we have expressions for A and B
    product_states = [s for s in states_with_solutions if "×" in s]
    for ps in product_states:
        # This state leads to solvable sub-goals → high value
        pairs.append({"state": ps, "outcome": 0.9})  # slightly < 1.0 (needs multi-step)

    # Balance: ensure ~50% positive, ~50% negative
    positives = [p for p in pairs if p["outcome"] > 0]
    negatives = [p for p in pairs if p["outcome"] == 0]
    print(f"Before balancing: {len(positives)} pos, {len(negatives)} neg")

    if len(negatives) > 2 * len(positives):
        rng.shuffle(negatives)
        negatives = negatives[:2 * len(positives)]

    all_pairs = positives + negatives
    rng.shuffle(all_pairs)

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for p in all_pairs:
            f.write(json.dumps(p) + "\n")

    print(f"\nOutput: {output_path}")
    print(f"Total: {len(all_pairs)} pairs ({len(positives)} pos, {len(negatives)} neg)")


if __name__ == "__main__":
    main()
