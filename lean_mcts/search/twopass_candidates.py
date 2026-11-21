"""
Two-pass candidate generation for skeleton-guided MCTS (Sprint 26).

For algorithms that need two traversals over the input list:
1. pass₁: compute an aggregate (max, min, sum, length)
2. pass₂: use the aggregate in a second computation (map, fold)

Generates candidates of the form:
    exact (let v := pass₁(xs); pass₂(xs, v))

Also generates dual-aggregate patterns:
    exact pass₁(xs) - pass₂(xs)         (e.g., max - min)
    exact (let a := pass₁(xs); let b := pass₂(xs); f(a, b))

Activated when:
    - Goal has list input
    - Not a product type
    - Recursion and foldl skeletons returned None
"""

from __future__ import annotations


# --- Pass 1: aggregate computations ---
# Each is a (template, description) where template uses "xs" as the variable
PASS1_TEMPLATES = [
    "xs.foldl max 0",
    "xs.foldl min 0",
    "xs.foldl (fun a b => a + b) 0",
    "xs.length",
    "xs.foldl (fun a b => if decide (b > a) then b else a) 0",
    "xs.foldl (fun a b => if decide (b < a) then b else a) 0",
]

# --- Pass 2: use aggregate in second computation ---
# Template uses "xs" for the list and "v" for the aggregate

# List Int → v → List Int
PASS2_MAP_TEMPLATES = [
    "xs.map (fun h => h - v)",       # deviation from aggregate
    "xs.map (fun h => h + v)",       # offset by aggregate
    "xs.map (fun h => h * v)",       # scale by aggregate
    "xs.filter (fun h => decide (h > v))",   # filter above aggregate
    "xs.filter (fun h => decide (h < v))",   # filter below aggregate
    "xs.filter (fun h => decide (h ≥ v))",   # filter >= aggregate
]

# List Int → v → Int
PASS2_FOLD_TEMPLATES = [
    "xs.foldl (fun acc h => acc + if decide (h ≥ 0) then h - v else v - h) 0",
    "xs.foldl (fun acc h => acc + (h - v) * (h - v)) 0",
    # Sprint 27: extended foldl pass2 — compose aggregate with foldl
    "xs.foldl (fun acc h => if decide (h > v) then acc + h else acc) 0",
    "xs.foldl (fun acc h => if decide (h < v) then acc + h else acc) 0",
    "xs.foldl (fun acc h => if decide (h = v) then acc + h else acc) 0",
    "xs.foldl (fun acc h => if decide (h > v) then acc + 1 else acc) 0",
    "xs.foldl (fun acc h => if decide (h < v) then acc + 1 else acc) 0",
    "xs.foldl (fun acc h => if decide (h ≥ v) then acc + 1 else acc) 0",
    "xs.foldl (fun acc h => if decide (h > v) then max acc h else acc) 0",
    "xs.foldl (fun acc h => if decide (h < v) then min acc h else acc) 0",
]

# List Int → v → Nat (Sprint 27: compose aggregate with counting foldl)
PASS2_FOLD_NAT_TEMPLATES = [
    "xs.foldl (fun acc h => if decide (h > v) then acc + 1 else acc) 0",
    "xs.foldl (fun acc h => if decide (h < v) then acc + 1 else acc) 0",
    "xs.foldl (fun acc h => if decide (h = v) then acc + 1 else acc) 0",
    "xs.foldl (fun acc h => if decide (h ≥ v) then acc + 1 else acc) 0",
]

# --- Dual-aggregate templates (no let binding needed) ---
# Direct expressions like: max(xs) - min(xs)
DUAL_AGG_INT_TEMPLATES = [
    "exact xs.foldl max 0 - xs.foldl min 0",
    "exact xs.foldl (fun a b => if decide (b > a) then b else a) 0 - xs.foldl (fun a b => if decide (b < a) then b else a) 0",
    "exact xs.foldl (fun a b => a + b) 0 - xs.length",
]

# --- Match-based templates for empty list handling ---
MATCH_DUAL_AGG_INT_TEMPLATES = [
    "exact (match xs with | [] => 0 | h :: t => t.foldl max h - t.foldl min h)",
    "exact (match xs with | [] => 0 | h :: t => t.foldl (fun a b => if decide (b > a) then b else a) h - t.foldl (fun a b => if decide (b < a) then b else a) h)",
]


def generate_twopass_candidates(
    return_type: str,
    input_var: str,
    max_candidates: int = 200,
) -> list[str]:
    """Generate two-pass candidate `exact` tactics.

    Args:
        return_type: Goal return type, e.g. "Int", "List Int"
        input_var: Input variable name, e.g. "xs"
        max_candidates: Cap on total candidates

    Returns:
        List of "exact ..." tactic strings.
    """
    candidates = []

    # Determine which pass2 templates match the return type
    if return_type in ("List Int", "List ℤ"):
        pass2_templates = PASS2_MAP_TEMPLATES
    elif return_type in ("Int", "ℤ"):
        pass2_templates = PASS2_FOLD_TEMPLATES
    elif return_type == "Nat":
        pass2_templates = PASS2_FOLD_NAT_TEMPLATES
    else:
        pass2_templates = []

    # Generate let-binding two-pass candidates
    for p1 in PASS1_TEMPLATES:
        for p2 in pass2_templates:
            expr = f"exact (let v := {p1}; {p2})"
            candidates.append(expr)
            if len(candidates) >= max_candidates:
                break
        if len(candidates) >= max_candidates:
            break

    # Add dual-aggregate templates for Int return
    if return_type in ("Int", "ℤ"):
        candidates.extend(DUAL_AGG_INT_TEMPLATES)
        candidates.extend(MATCH_DUAL_AGG_INT_TEMPLATES)

    # Substitute the actual variable name if not "xs"
    if input_var != "xs":
        candidates = [c.replace("xs", input_var) for c in candidates]

    return candidates[:max_candidates]
