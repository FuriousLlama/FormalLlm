"""
Foldl candidate generation for skeleton-guided MCTS (Sprint 26).

List.rec is a right fold: it processes elements right-to-left.
Several tasks require left-to-right semantics where the final
accumulator value (not a reconstructed list) is the answer.

foldl_skeleton(xs, init, step) = xs.foldl step init

Also generates xs.map candidates for List→List goals where the
transformation doesn't need accumulator state (conditional maps).

Activated when:
    - Goal has list input and scalar return type (Int, Nat, Bool)
    - OR goal has list input and list return type (for map patterns)
    - Not already handled by product or recursion skeleton
"""

from __future__ import annotations


# --- Foldl templates by (input_type, return_type) ---

FOLDL_LIST_INT_TO_INT = [
    # Basic accumulation
    "exact xs.foldl (fun acc h => acc + h) 0",
    "exact xs.foldl (fun acc h => acc * h) 1",
    "exact xs.foldl (fun acc h => acc - h) 0",
    # Last element
    "exact xs.foldl (fun _ h => h) 0",
    # Min / max
    "exact xs.foldl (fun acc h => if decide (h > acc) then h else acc) 0",
    "exact xs.foldl (fun acc h => if decide (h < acc) then h else acc) 0",
    # Conditional accumulation
    "exact xs.foldl (fun acc h => if decide (h > 0) then acc + h else acc) 0",
    "exact xs.foldl (fun acc h => if decide (h < 0) then acc + h else acc) 0",
    "exact xs.foldl (fun acc h => if decide (h > 0) then acc + 1 else acc) 0",
    "exact xs.foldl (fun acc h => if decide (h < 0) then acc + 1 else acc) 0",
    # Absolute value sum
    "exact xs.foldl (fun acc h => acc + if decide (h ≥ 0) then h else -h) 0",
    # Negate result
    "exact -(xs.foldl (fun acc h => acc + h) 0)",
    # Sum of squares
    "exact xs.foldl (fun acc h => acc + h * h) 0",
]

FOLDL_LIST_INT_TO_NAT = [
    "exact xs.foldl (fun acc _ => acc + 1) 0",
    "exact xs.foldl (fun acc h => if decide (h > 0) then acc + 1 else acc) 0",
    "exact xs.foldl (fun acc h => if decide (h < 0) then acc + 1 else acc) 0",
    "exact xs.foldl (fun acc h => if decide (h % 2 = 0) then acc + 1 else acc) 0",
    "exact xs.foldl (fun acc h => if decide (h = 0) then acc + 1 else acc) 0",
]

FOLDL_LIST_INT_TO_BOOL = [
    "exact xs.foldl (fun acc h => acc && decide (h > 0)) true",
    "exact xs.foldl (fun acc h => acc && decide (h ≥ 0)) true",
    "exact xs.foldl (fun acc h => acc && decide (h % 2 = 0)) true",
    "exact xs.foldl (fun acc h => acc || decide (h > 0)) false",
    "exact xs.foldl (fun acc h => acc || decide (h < 0)) false",
]

# Map templates for List → List goals (no accumulator state needed)
MAP_LIST_INT_TO_LIST_INT = [
    # Conditional maps
    "exact xs.map (fun h => if decide (h > 0) then h * 2 else h)",
    "exact xs.map (fun h => if decide (h > 0) then h + 1 else h)",
    "exact xs.map (fun h => if decide (h > 0) then h * h else h)",
    "exact xs.map (fun h => if decide (h < 0) then 0 else h)",
    "exact xs.map (fun h => if decide (h < 0) then -h else h)",
    "exact xs.map (fun h => if decide (h > 0) then h else 0)",
    # Clamp
    "exact xs.map (fun h => max (-5) (min 5 h))",
    "exact xs.map (fun h => max (-10) (min 10 h))",
    "exact xs.map (fun h => max 0 h)",
    # Parity-based
    "exact xs.map (fun h => if decide (h % 2 = 0) then h * 2 else h)",
    "exact xs.map (fun h => if decide (h % 2 ≠ 0) then h * 2 else h)",
    # Simple transforms (already in EF, but ensures coverage)
    "exact xs.map (fun h => h * 2)",
    "exact xs.map (fun h => h * h)",
    "exact xs.map (fun h => -h)",
    "exact xs.map (fun h => h + 1)",
]


def generate_foldl_candidates(
    return_type: str,
    input_var: str,
    is_list_return: bool = False,
) -> list[str]:
    """Generate candidate `exact` tactics using foldl or map.

    Args:
        return_type: Goal return type, e.g. "Int", "Nat", "Bool", "List Int"
        input_var: Input variable name, e.g. "xs"
        is_list_return: True if goal returns a list type

    Returns:
        List of "exact ..." tactic strings with input_var substituted.
    """
    if is_list_return and return_type == "List Int":
        templates = MAP_LIST_INT_TO_LIST_INT
    elif return_type == "Int":
        templates = FOLDL_LIST_INT_TO_INT
    elif return_type == "Nat":
        templates = FOLDL_LIST_INT_TO_NAT
    elif return_type == "Bool":
        templates = FOLDL_LIST_INT_TO_BOOL
    else:
        return []

    # Substitute the actual variable name if not "xs"
    if input_var != "xs":
        return [t.replace("xs", input_var) for t in templates]
    return list(templates)
