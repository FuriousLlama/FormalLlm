"""
Expression composition candidates for skeleton-guided MCTS (Sprint 27).

Chains known single-step operations into 2-3 step pipelines.
This is NOT adding new templates — it's letting existing EF expressions
call each other in sequence.

Example: removeDuplicatesKeepLast = reverse → eraseDups → reverse

Activated after two-pass skeleton, before standard MCTS fallback.
"""

from __future__ import annotations


# --- Base expressions: List Int → List Int ---
# Starting points for composition chains
LIST_TO_LIST_BASES = [
    "{v}",
    "{v}.reverse",
    "{v}.eraseDups",
    "List.mergeSort {v}",
    "{v}.filter (fun x => decide (x > 0))",
    "{v}.filter (fun x => decide (x < 0))",
    "{v}.filter (fun x => decide (x ≥ 0))",
    "{v}.filter (fun x => decide (x ≠ 0))",
]

# --- Level-1 operations: List Int → List Int ---
CHAIN_LIST_TO_LIST = [
    ".reverse",
    ".eraseDups",
    ".filter (fun x => decide (x > 0))",
    ".filter (fun x => decide (x < 0))",
    ".filter (fun x => decide (x ≥ 0))",
    ".filter (fun x => decide (x ≠ 0))",
]

# --- Level-1 operations: List Int → Int ---
CHAIN_LIST_TO_INT = [
    ".foldl (fun a b => a + b) 0",
    ".foldl max 0",
    ".foldl min 0",
    ".foldl (fun acc h => acc + h * h) 0",
    ".foldl (fun _ h => h) 0",
]

# --- Level-1 operations: List Int → Nat ---
CHAIN_LIST_TO_NAT = [
    ".length",
    ".eraseDups.length",
]

# --- Wrap-around patterns ---
# Patterns where we wrap an operation: f(g(xs))
WRAP_PATTERNS_LIST = [
    "({base}).reverse",                      # reverse after transform
    "List.mergeSort ({base})",               # sort after transform
]


def generate_composition_candidates(
    return_type: str,
    input_var: str,
    max_candidates: int = 300,
) -> list[str]:
    """Generate candidate tactics by composing known operations.

    Generates 2-step and 3-step pipelines by chaining operations.

    Args:
        return_type: Goal return type, e.g. "Int", "Nat", "List Int"
        input_var: Input variable name, e.g. "xs"
        max_candidates: Cap on total candidates

    Returns:
        List of "exact ..." tactic strings.
    """
    candidates = []

    if return_type in ("List Int", "List ℤ"):
        # 2-step: base → chain_list_to_list
        for base in LIST_TO_LIST_BASES:
            base_expr = base.format(v=input_var)
            if base_expr == input_var:
                continue  # skip identity, already covered by EF
            for chain in CHAIN_LIST_TO_LIST:
                expr = f"({base_expr}){chain}"
                candidates.append(f"exact {expr}")

            # Also wrap patterns
            for wrap in WRAP_PATTERNS_LIST:
                expr = wrap.format(base=base_expr)
                candidates.append(f"exact {expr}")

        # 3-step: targeted high-value compositions
        # reverse → eraseDups → reverse (removeDuplicatesKeepLast)
        candidates.append(f"exact ({input_var}.reverse.eraseDups).reverse")
        # eraseDups → reverse
        candidates.append(f"exact {input_var}.eraseDups.reverse")
        # reverse → sort
        candidates.append(f"exact List.mergeSort ({input_var}.reverse)")
        # filter → eraseDups → reverse
        for pred in ["x > 0", "x < 0", "x ≥ 0", "x ≠ 0"]:
            candidates.append(f"exact ({input_var}.filter (fun x => decide ({pred}))).eraseDups.reverse")
            candidates.append(f"exact (({input_var}.filter (fun x => decide ({pred}))).eraseDups).reverse")

    elif return_type in ("Int", "ℤ"):
        # 2-step: base_list → chain_list_to_int
        for base in LIST_TO_LIST_BASES:
            base_expr = base.format(v=input_var)
            for chain in CHAIN_LIST_TO_INT:
                expr = f"({base_expr}){chain}"
                candidates.append(f"exact {expr}")

    elif return_type == "Nat":
        # 2-step: base_list → chain_list_to_nat
        for base in LIST_TO_LIST_BASES:
            base_expr = base.format(v=input_var)
            for chain in CHAIN_LIST_TO_NAT:
                expr = f"({base_expr}){chain}"
                candidates.append(f"exact {expr}")

    # Substitute variable name
    if input_var != "xs":
        candidates = [c.replace("xs", input_var) if "xs" in c else c for c in candidates]

    return candidates[:max_candidates]
