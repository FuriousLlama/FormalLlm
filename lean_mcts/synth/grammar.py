"""Phase 4b: PCFG extraction + sampling from successful synthesis expressions.

Extracts a typed probabilistic context-free grammar from self-play expressions.
Can be used as an alternative policy (PCFGPolicy) or for exploration noise.

Usage:
    python synth/grammar.py \
        --expressions synth/expressions.jsonl \
        --output synth/grammar.json

    # Or from self-play data:
    python synth/grammar.py \
        --selfplay train/synth_train.jsonl \
        --output synth/grammar.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Grammar definition
# ---------------------------------------------------------------------------

# The synthesis expression grammar — productions for each non-terminal.
# Each production is a tuple of symbols (terminals + non-terminals).
# Non-terminals are keys in GRAMMAR; everything else is a terminal.

GRAMMAR = {
    "expr": [
        ("base_expr",),
        ("compose_expr",),
        ("multi_type_expr",),
    ],
    "base_expr": [
        ("xs",),
        ("xs.length",),
        ("xs.reverse",),
        ("xs.eraseDups",),
        ("xs.filter", "pred",),
        ("xs.map", "transform",),
        ("xs.foldl", "binop", "init",),
        ("xs.takeWhile", "pred",),
        ("xs.dropWhile", "pred",),
        ("List.mergeSort xs",),
        ("xs.take", "small_int",),
        ("xs.drop", "small_int",),
        # Sprint 14: new library vocabulary
        ("xs.tail",),
        ("xs.dropLast",),
        ("xs.intersperse 0",),
        ("xs.partition", "pred",),
        ("(List.scanl", "binop", "init", "xs).drop 1",),
    ],
    "compose_expr": [
        ("(", "inner", ").", "method",),
        ("method_call", "(", "inner", ")",),
    ],
    "inner": [
        ("xs.filter", "pred",),
        ("xs.map", "transform",),
        ("xs.takeWhile", "pred",),
        ("xs.dropWhile", "pred",),
        ("xs.reverse",),
        ("xs.eraseDups",),
        ("List.mergeSort xs",),
        ("xs.take", "small_int",),
        ("xs.drop", "small_int",),
        # Sprint 14
        ("xs.tail",),
        ("xs.dropLast",),
        ("xs.filter", "pred", "|>.eraseDups",),
        ("xs.zip xs.tail",),
        ("xs.zipIdx",),
    ],
    "method": [
        ("length",),
        ("reverse",),
        ("eraseDups",),
        ("filter", "pred",),
        ("map", "transform",),
        ("foldl", "binop", "init",),
        ("takeWhile", "pred",),
        ("dropWhile", "pred",),
        ("take", "small_int",),
        ("drop", "small_int",),
        # Sprint 14
        ("drop 1",),
        ("map Prod.fst",),
        ("map (fun (a, b) => a + b)",),
    ],
    "method_call": [
        ("List.mergeSort",),
    ],
    "multi_type_expr": [
        # List (List Int) input
        ("xss.flatten",),
        ("(xss.map List.length).foldl max 0",),
        ("(xss.map List.length).foldl", "binop", "init",),
        # Two-arg input
        ("List.mergeSort (xs ++ ys)",),
        ("(List.zipWith", "binop", "xs ys).foldl", "binop", "init",),
        ("xs ++ ys",),
        ("List.zipWith", "binop", "xs ys",),
        # Nat input
        ("(List.range n).map", "nat_transform",),
        ("List.range n",),
    ],
    "pred": [
        ("(fun x => decide (x > 0))",),
        ("(fun x => decide (x < 0))",),
        ("(fun x => decide (x ≥ 0))",),
        ("(fun x => decide (x ≤ 0))",),
        ("(fun x => decide (x % 2 = 0))",),
        ("(fun x => decide (x % 2 ≠ 0))",),
        ("(fun x => decide (x ≠ 0))",),
        ("(fun x => decide (x > 1))",),
        ("(fun x => decide (x < -1))",),
        ("(fun x => decide (x > 2))",),
        # Sprint 14
        ("(fun x => decide (x < 5))",),
        ("(fun x => decide (x ≥ 2))",),
    ],
    "transform": [
        ("(fun x => x * 2)",),
        ("(fun x => x * 3)",),
        ("(fun x => x * x)",),
        ("(fun x => x + 1)",),
        ("(fun x => x - 1)",),
        ("(fun x => x + 2)",),
        ("(fun x => -x)",),
        ("(fun x => x.natAbs)",),
        # Sprint 14
        ("(fun x => x + 3)",),
        ("(fun x => x * 4)",),
    ],
    "nat_transform": [
        ("(fun i => i * i)",),
        ("(fun i => i + 1)",),
        ("(fun i => i * 2)",),
    ],
    "binop": [
        ("(fun a b => a + b)",),
        ("(fun a b => a * b)",),
        ("max",),
        ("min",),
    ],
    "init": [
        ("0",),
        ("1",),
    ],
    "small_int": [
        ("1",),
        ("2",),
        ("3",),
    ],
}

# Non-terminals in the grammar
NONTERMINALS = set(GRAMMAR.keys())


class PCFG:
    """Probabilistic context-free grammar with Laplace-smoothed production probabilities."""

    def __init__(self, grammar: dict, counts: Optional[dict] = None, alpha: float = 1.0):
        self.grammar = grammar
        self.alpha = alpha  # Laplace smoothing
        self.probs: dict[str, list[float]] = {}

        if counts:
            self._learn_probs(counts)
        else:
            self._uniform_probs()

    def _uniform_probs(self):
        """Initialize with uniform probabilities."""
        for nt, prods in self.grammar.items():
            n = len(prods)
            self.probs[nt] = [1.0 / n] * n

    def _learn_probs(self, counts: dict[str, list[int]]):
        """Compute probabilities from production counts + Laplace smoothing."""
        for nt, prods in self.grammar.items():
            prod_counts = counts.get(nt, [0] * len(prods))
            # Pad if counts list is shorter than productions
            while len(prod_counts) < len(prods):
                prod_counts.append(0)
            total = sum(prod_counts) + self.alpha * len(prods)
            self.probs[nt] = [(c + self.alpha) / total for c in prod_counts]

    def sample(self, start: str = "expr", max_depth: int = 10) -> str:
        """Sample an expression from the grammar."""
        return self._expand(start, max_depth)

    def _expand(self, symbol: str, depth: int) -> str:
        if depth <= 0:
            # At max depth, pick the shortest production
            if symbol in self.grammar:
                prods = self.grammar[symbol]
                shortest = min(prods, key=len)
                return " ".join(self._expand(s, 0) if s in NONTERMINALS else s
                               for s in shortest)
            return symbol

        if symbol not in self.grammar:
            return symbol  # Terminal

        prods = self.grammar[symbol]
        weights = self.probs[symbol]
        idx = random.choices(range(len(prods)), weights=weights, k=1)[0]
        prod = prods[idx]

        parts = []
        for s in prod:
            if s in NONTERMINALS:
                parts.append(self._expand(s, depth - 1))
            else:
                parts.append(s)

        return " ".join(parts)

    def sample_tactic(self, k: int = 32) -> list[str]:
        """Sample k tactics formatted as 'exact <expr>'."""
        seen: set[str] = set()
        tactics: list[str] = []
        attempts = 0
        while len(tactics) < k and attempts < k * 5:
            expr = self.sample()
            # Clean up spacing
            expr = re.sub(r'\s+', ' ', expr).strip()
            expr = expr.replace("( ", "(").replace(" )", ")")
            expr = expr.replace("). ", ").").replace(" .", ".")
            tactic = f"exact {expr}"
            if tactic not in seen:
                seen.add(tactic)
                tactics.append(tactic)
            attempts += 1
        return tactics

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        return {
            "grammar": {nt: [list(p) for p in prods]
                        for nt, prods in self.grammar.items()},
            "probs": self.probs,
            "alpha": self.alpha,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PCFG:
        """Deserialize from dict."""
        grammar = {nt: [tuple(p) for p in prods]
                   for nt, prods in data["grammar"].items()}
        pcfg = cls(grammar, alpha=data.get("alpha", 1.0))
        pcfg.probs = data["probs"]
        return pcfg


class PCFGPolicy:
    """Policy that samples from a typed PCFG over synthesis expressions."""

    def __init__(self, grammar_path: str = None, pcfg: PCFG = None):
        if pcfg is not None:
            self.pcfg = pcfg
        elif grammar_path is not None:
            with open(grammar_path) as f:
                data = json.load(f)
            self.pcfg = PCFG.from_dict(data)
        else:
            # Default: uniform grammar
            self.pcfg = PCFG(GRAMMAR)

    def propose_tactics(self, state: object, k: int = 200) -> list[str]:
        """Sample k expressions from the grammar, format as 'exact <expr>'."""
        return self.pcfg.sample_tactic(k=k)

    def __call__(self, state: object, k: int = 200) -> list[str]:
        return self.propose_tactics(state, k)


# ---------------------------------------------------------------------------
# Learning from data
# ---------------------------------------------------------------------------

def count_productions_from_expressions(expressions: list[dict]) -> dict[str, list[int]]:
    """Count grammar productions used in successful expressions.

    This is a heuristic matcher — it pattern-matches expression strings
    against grammar productions rather than doing a full parse.
    """
    counts: dict[str, list[int]] = {}
    for nt, prods in GRAMMAR.items():
        counts[nt] = [0] * len(prods)

    for expr_dict in expressions:
        expr = expr_dict.get("expr", "") or expr_dict.get("tactic", "")
        if expr.startswith("exact "):
            expr = expr[6:]

        _count_in_expr(expr, counts)

    return counts


def _count_in_expr(expr: str, counts: dict[str, list[int]]):
    """Pattern-match an expression against grammar productions."""
    # Predicates
    for i, prod in enumerate(GRAMMAR["pred"]):
        if prod[0] in expr:
            counts["pred"][i] += 1

    # Transforms
    for i, prod in enumerate(GRAMMAR["transform"]):
        if prod[0] in expr:
            counts["transform"][i] += 1

    # Nat transforms
    for i, prod in enumerate(GRAMMAR["nat_transform"]):
        if prod[0] in expr:
            counts["nat_transform"][i] += 1

    # Binops
    for i, prod in enumerate(GRAMMAR["binop"]):
        if prod[0] in expr:
            counts["binop"][i] += 1

    # Methods
    if ".length" in expr:
        counts["method"][0] += 1
    if ".reverse" in expr and "mergeSort" not in expr:
        counts["method"][1] += 1
    if ".eraseDups" in expr:
        counts["method"][2] += 1
    if ".filter" in expr:
        counts["method"][3] += 1
    if ".map" in expr:
        counts["method"][4] += 1
    if ".foldl" in expr:
        counts["method"][5] += 1
    if ".takeWhile" in expr:
        counts["method"][6] += 1
    if ".dropWhile" in expr:
        counts["method"][7] += 1

    # Multi-type detection
    if "xss" in expr or "flatten" in expr:
        counts["expr"][2] += 1  # multi_type
    elif "ys" in expr or "zipWith" in expr:
        counts["expr"][2] += 1  # multi_type
    elif "List.range" in expr:
        counts["expr"][2] += 1  # multi_type
    # Composition vs base
    elif "(" in expr and ")." in expr:
        counts["expr"][1] += 1  # compose
    elif "List.mergeSort (" in expr:
        counts["expr"][1] += 1  # compose (method_call form)
    else:
        counts["expr"][0] += 1  # base


def main():
    parser = argparse.ArgumentParser(description="Extract PCFG from expressions")
    parser.add_argument("--expressions", type=str, default="synth/expressions_v2.jsonl")
    parser.add_argument("--selfplay", type=str, default=None,
                        help="Additional self-play data JSONL")
    parser.add_argument("--output", type=str, default="synth/grammar.json")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent.parent

    # Load expressions
    all_exprs: list[dict] = []
    expr_path = script_dir / args.expressions
    with open(expr_path) as f:
        for line in f:
            all_exprs.append(json.loads(line))
    print(f"Loaded {len(all_exprs)} expressions from {expr_path}")

    # Optionally load self-play data
    if args.selfplay:
        sp_path = script_dir / args.selfplay
        with open(sp_path) as f:
            for line in f:
                all_exprs.append(json.loads(line))
        print(f"Loaded additional self-play data from {sp_path}")

    print(f"Total expressions for PCFG: {len(all_exprs)}")

    # Count productions
    counts = count_productions_from_expressions(all_exprs)

    # Build PCFG
    pcfg = PCFG(GRAMMAR, counts=counts)

    # Save
    output_path = script_dir / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(pcfg.to_dict(), f, indent=2)
    print(f"Grammar saved to {output_path}")

    # Print production probabilities
    print("\nProduction probabilities:")
    for nt in ["expr", "pred", "transform", "binop"]:
        print(f"  {nt}:")
        for i, prod in enumerate(GRAMMAR[nt]):
            count = counts[nt][i]
            prob = pcfg.probs[nt][i]
            label = " ".join(prod)[:50]
            print(f"    [{count:3d}] {prob:.3f}  {label}")

    # Sample a few to verify
    print("\nSample tactics:")
    for t in pcfg.sample_tactic(k=10):
        print(f"  {t}")


if __name__ == "__main__":
    main()
