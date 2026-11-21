"""
Sprint 21 Phase 2: Generate recurrence-specific ExpandedFixed expressions.

Adds Nat-input expressions to expressions.jsonl for:
  - Tuple-accumulator folds (fibonacci-class)
  - Nested range compositions (triangular-class)
  - Indexed accumulator folds (factorial-class)
  - Tuple-append accumulators (fibList-class)
  - Simple folds over List Int for sub-goal contexts

Usage:
    python lean_mcts/synth/gen_recurrence_expressions.py
    python lean_mcts/synth/gen_recurrence_expressions.py --dry-run
"""

import json
import sys
from pathlib import Path


def generate_nat_expressions() -> list[dict]:
    """Generate Nat-input expressions for ExpandedFixed recurrence templates."""
    exprs = []

    # --- Tuple-accumulator folds: ((List.range n).foldl (fun (a,b) _ => ...) init).proj ---
    binary_recurrences = [
        ("(fun (a, b) _ => (b, a + b))", "(0, 1)", "fib_01"),
        ("(fun (a, b) _ => (b, a + b))", "(1, 1)", "lucas"),
        ("(fun (a, b) _ => (b, a + b))", "(1, 0)", "fib_10"),
        ("(fun (a, b) _ => (b, a + b))", "(0, 0)", "fib_00"),
        ("(fun (a, b) _ => (b, a + b))", "(2, 1)", "pell_like"),
        ("(fun (a, b) _ => (a + b, a))", "(0, 1)", "fib_swap"),
        ("(fun (a, b) _ => (a + b, a))", "(1, 0)", "fib_swap10"),
        ("(fun (a, b) _ => (b, 2 * a + b))", "(0, 1)", "pell"),
        ("(fun (a, b) _ => (b, a + 2 * b))", "(0, 1)", "jacobsthal"),
        ("(fun (a, b) _ => (b, a * b))", "(1, 1)", "mul_recur"),
        ("(fun (a, b) _ => (b, a * b))", "(1, 2)", "mul_12"),
        ("(fun (a, b) _ => (b, a * b))", "(2, 1)", "mul_21"),
        ("(fun (a, b) _ => (b, a + b + 1))", "(0, 0)", "shifted"),
        ("(fun (a, b) _ => (b, a + b + 1))", "(0, 1)", "shifted_01"),
        ("(fun (a, b) _ => (b, a + b * 2))", "(0, 1)", "weighted"),
        ("(fun (a, b) _ => (b, max a b))", "(0, 0)", "max_recur"),
        ("(fun (a, b) _ => (b, max a b + 1))", "(0, 1)", "max_plus"),
    ]
    for fold_fn, init, tag in binary_recurrences:
        for proj in [".1", ".2"]:
            expr = f"((List.range n).foldl {fold_fn} {init}){proj}"
            exprs.append({
                "tag": f"tuple_fold_{tag}{proj.replace('.', '_')}",
                "expr": expr,
                "tactic": f"exact {expr}",
                "return_type": "Nat",
                "input_type": "Nat",
                "level": 2,
                "state": "n : Nat\n⊢ Nat",
            })

    # --- Indexed accumulator folds: (List.range n).foldl (fun acc i => ...) init ---
    indexed_accum = [
        ("fun acc i => acc * (i + 1)", "1", "factorial"),
        ("fun acc i => acc + (i + 1)", "0", "triangle"),
        ("fun acc i => acc + i * i", "0", "sum_sq"),
        ("fun acc i => acc + i", "0", "sum_range"),
        ("fun acc _ => acc * 2", "1", "pow2"),
        ("fun acc _ => acc * 3", "1", "pow3"),
        ("fun acc i => acc + (i + 1) * (i + 1)", "0", "sum_sq_shifted"),
        ("fun acc _ => acc + 1", "0", "identity"),
        ("fun acc i => acc + 2 * i + 1", "0", "odd_sum"),
        ("fun acc _ => acc * 2 + 1", "0", "mersenne"),
        ("· + ·", "0", "range_sum"),
        ("· * ·", "1", "range_prod"),
    ]
    for fold_fn, init, tag in indexed_accum:
        expr = f"(List.range n).foldl ({fold_fn}) {init}"
        exprs.append({
            "tag": f"idx_fold_{tag}",
            "expr": expr,
            "tactic": f"exact {expr}",
            "return_type": "Nat",
            "input_type": "Nat",
            "level": 1,
            "state": "n : Nat\n⊢ Nat",
        })

    # --- Nested range: (List.range n).map (fun i => ...) ---
    nested_range = [
        ("fun i => (List.range (i + 1)).foldl (· + ·) 0", "triangular"),
        ("fun i => (List.range i).foldl (fun a _ => a * 2) 1", "pow2_list"),
        ("fun i => (List.range (i + 1)).foldl (· * ·) 1", "factorial_list"),
        ("fun i => (List.range i).foldl (fun a _ => a * 3) 1", "pow3_list"),
        ("fun i => (List.range i).length", "identity_list"),
        ("fun i => i * i", "squares"),
        ("fun i => i * (i + 1) / 2", "triangular_closed"),
        ("fun i => i * (i + 1)", "pronic"),
        ("fun i => 2 ^ i", "pow2_direct"),
        ("fun i => i + 1", "naturals"),
        ("fun i => 2 * i + 1", "odds"),
        ("fun i => 2 * i", "evens"),
        ("fun i => (List.range (i + 1)).foldl (fun a _ => a + 2) 0", "evens_fold"),
        ("fun i => (List.range i).foldl (fun a b => a + b + 1) 0", "shifted_tri"),
    ]
    for map_fn, tag in nested_range:
        expr = f"(List.range n).map ({map_fn})"
        exprs.append({
            "tag": f"nested_{tag}",
            "expr": expr,
            "tactic": f"exact {expr}",
            "return_type": "List Nat",
            "input_type": "Nat",
            "level": 2,
            "state": "n : Nat\n⊢ List Nat",
        })

    # --- Tuple-append: ((List.range n).foldl (fun (acc, ...) _ => (acc ++ [...], ...)) ([], init)).1 ---
    tuple_append = [
        ("(fun (acc, v) _ => (acc ++ [v], v + 1))", "([], 0)", "counter"),
        ("(fun (acc, v) _ => (acc ++ [v], v * 2))", "([], 1)", "powers2"),
        ("(fun (acc, v) _ => (acc ++ [v], v * 3))", "([], 1)", "powers3"),
        ("(fun (acc, v) _ => (acc ++ [v], v + 2))", "([], 0)", "evens"),
        ("(fun (acc, v) _ => (acc ++ [v], v + v))", "([], 1)", "doubling"),
        ("(fun (acc, ab) _ => (acc ++ [ab.1], (ab.2, ab.1 + ab.2)))", "([], (0, 1))", "fib_list"),
        ("(fun (acc, ab) _ => (acc ++ [ab.1], (ab.2, ab.1 + ab.2)))", "([], (1, 1))", "lucas_list"),
    ]
    for fold_fn, init, tag in tuple_append:
        expr = f"((List.range n).foldl {fold_fn} {init}).1"
        exprs.append({
            "tag": f"append_{tag}",
            "expr": expr,
            "tactic": f"exact {expr}",
            "return_type": "List Nat",
            "input_type": "Nat",
            "level": 2,
            "state": "n : Nat\n⊢ List Nat",
        })

    # --- Simple Nat expressions ---
    simple_nat = [
        ("n", "Nat", "id"),
        ("n + 1", "Nat", "succ"),
        ("n * 2", "Nat", "double"),
        ("n * n", "Nat", "square"),
        ("List.range n", "List Nat", "range"),
        ("(List.range n).reverse", "List Nat", "range_rev"),
        ("List.replicate n 0", "List Nat", "zeros"),
        ("List.replicate n 1", "List Nat", "ones"),
        ("(List.range n).length", "Nat", "range_len"),
    ]
    for expr, ret_type, tag in simple_nat:
        exprs.append({
            "tag": f"nat_{tag}",
            "expr": expr,
            "tactic": f"exact {expr}",
            "return_type": ret_type,
            "input_type": "Nat",
            "level": 0,
            "state": f"n : Nat\n⊢ {ret_type}",
        })

    return exprs


def generate_subgoal_expressions() -> list[dict]:
    """Generate expressions for common sub-goal patterns (List Int → scalar)."""
    exprs = []

    # Simple folds that should be available as ExpandedFixed safety net
    folds = [
        ("xs.foldl (· + ·) 0", "Int", "sum"),
        ("xs.foldl (· * ·) 1", "Int", "prod"),
        ("xs.foldl (fun a b => a + b) 0", "Int", "sum_lambda"),
        ("xs.foldl (fun a b => a * b) 1", "Int", "prod_lambda"),
        ("xs.foldl max 0", "Int", "max"),
        ("xs.foldl min 0", "Int", "min"),
        ("xs.filter (fun x => decide (x % 2 = 0))", "List Int", "even_filter"),
        ("xs.filter (fun x => decide (x % 2 ≠ 0))", "List Int", "odd_filter"),
        ("(xs.filter (fun x => decide (x % 2 = 0))).length", "Nat", "even_count"),
        ("(xs.filter (fun x => decide (x % 2 ≠ 0))).length", "Nat", "odd_count"),
    ]
    for expr, ret_type, tag in folds:
        exprs.append({
            "tag": f"fold_{tag}",
            "expr": expr,
            "tactic": f"exact {expr}",
            "return_type": ret_type,
            "input_type": "List Int",
            "level": 1,
            "state": f"xs : List Int\n⊢ {ret_type}",
        })

    # Direct pair patterns for product types
    pairs = [
        ("(xs.foldl (· + ·) 0, xs.foldl (· * ·) 1)", "Int × Int", "sum_prod"),
        ("(xs.foldl (fun a b => a + b) 0, xs.foldl (fun a b => a * b) 1)", "Int × Int", "sum_prod_lambda"),
        ("(xs.filter (fun x => decide (x % 2 = 0)), xs.filter (fun x => decide (x % 2 ≠ 0)))", "List Int × List Int", "even_odd"),
        ("(xs.filter (fun x => decide (x % 2 ≠ 0)), xs.filter (fun x => decide (x % 2 = 0)))", "List Int × List Int", "odd_even"),
    ]
    for expr, ret_type, tag in pairs:
        exprs.append({
            "tag": f"pair_{tag}",
            "expr": expr,
            "tactic": f"exact {expr}",
            "return_type": ret_type,
            "input_type": "List Int",
            "level": 2,
            "state": f"xs : List Int\n⊢ {ret_type}",
        })

    return exprs


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Don't write, just count")
    args = parser.parse_args()

    expr_path = Path(__file__).parent / "expressions.jsonl"

    # Load existing
    existing_tactics = set()
    with open(expr_path) as f:
        for line in f:
            entry = json.loads(line)
            existing_tactics.add(entry["tactic"])

    nat_exprs = generate_nat_expressions()
    subgoal_exprs = generate_subgoal_expressions()
    all_new = nat_exprs + subgoal_exprs

    # Deduplicate against existing
    new_unique = [e for e in all_new if e["tactic"] not in existing_tactics]

    print(f"Existing expressions: {len(existing_tactics)}")
    print(f"Generated: {len(all_new)} (Nat: {len(nat_exprs)}, subgoal: {len(subgoal_exprs)})")
    print(f"After dedup: {len(new_unique)} new")

    if args.dry_run:
        for e in new_unique[:10]:
            print(f"  {e['tag']}: {e['tactic'][:80]}")
        print(f"  ... ({len(new_unique)} total)")
        return

    # Append to expressions.jsonl
    with open(expr_path, "a") as f:
        for entry in new_unique:
            f.write(json.dumps(entry) + "\n")

    total = len(existing_tactics) + len(new_unique)
    print(f"Written {len(new_unique)} new expressions → {total} total in {expr_path.name}")


if __name__ == "__main__":
    main()
