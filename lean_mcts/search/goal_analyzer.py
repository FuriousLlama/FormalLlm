"""
Goal structure analyzer for skeleton-guided MCTS expansion (Sprint 24).

Deterministic type analysis: given a Lean goal type string, determines
whether the goal is a product type, counts the nesting depth, identifies
leaf types, and generates the proof skeleton (constructor chain + exacts).

Only handles LEFT-nested products (Lean's default associativity):
    Int × Nat              → 1 constructor, 2 exacts
    (Int × Nat) × Int      → 2 constructors, 3 exacts
    ((A × B) × C) × D      → 3 constructors, 4 exacts

Right-nested products (e.g. Int × (Nat × Bool)) are treated as having
an atomic right branch — the right sub-goal is left to standard MCTS.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GoalStructure:
    """Analyzed structure of a Lean goal type."""
    raw_type: str           # e.g. "(Int × Nat) × Int"
    is_product: bool        # True if top-level ×
    product_depth: int      # 0=atomic, 1=A×B, 2=(A×B)×C
    leaf_types: list[str]   # ["Int", "Nat", "Int"] left-to-right
    constructor_count: int  # Number of constructor tactics needed
    skeleton: list[str]     # ["constructor", "constructor", "exact", "exact", "exact"]
    # Sprint 25: recursion analysis
    is_recursive_input: bool = False        # True if primary input is List/Nat
    recursive_input_type: str = ""          # "List" or "Nat" or ""
    recursive_element_type: str = ""        # "Int" for List Int, "" for Nat
    recursion_skeleton: str = ""            # "List.rec" or "Nat.rec" or ""
    input_variable: str = ""                # "xs" or "n" — the recursion target
    # Sprint 26: foldl / map analysis
    is_foldl_amenable: bool = False         # scalar return + list input + not product
    is_map_amenable: bool = False           # list return + list input + not product


def _strip_outer_parens(s: str) -> str:
    """Strip parentheses that wrap the entire type expression."""
    s = s.strip()
    while s.startswith('(') and s.endswith(')'):
        depth = 0
        wraps = True
        for i, ch in enumerate(s):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            if depth == 0 and i < len(s) - 1:
                wraps = False
                break
        if wraps:
            s = s[1:-1].strip()
        else:
            break
    return s


def _find_last_top_level_times(s: str) -> int:
    """Find the position of the LAST top-level × in s.

    Uses last because × is left-associative in Lean:
    A × B × C = (A × B) × C, so the last × is the outermost split.
    Returns -1 if no top-level × found.
    """
    depth = 0
    pos = -1
    for i, ch in enumerate(s):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif ch == '×' and depth == 0:
            pos = i
    return pos


def _decompose_product(type_str: str) -> tuple[int, list[str]]:
    """Decompose a left-nested product type into constructor count and leaf types.

    Returns (constructor_count, leaf_types).

    Examples:
        "Int"                → (0, ["Int"])
        "Int × Nat"          → (1, ["Int", "Nat"])
        "(Int × Nat) × Int"  → (2, ["Int", "Nat", "Int"])
        "List Int"           → (0, ["List Int"])
        "List (Int × Nat)"   → (0, ["List (Int × Nat)"])
    """
    type_str = _strip_outer_parens(type_str)

    pos = _find_last_top_level_times(type_str)
    if pos < 0:
        return 0, [type_str]

    left = type_str[:pos].strip()
    right = type_str[pos + 1:].strip()

    # Recursively decompose left branch (left-nested)
    left_count, left_leaves = _decompose_product(left)

    # Right branch is treated as a single leaf
    return left_count + 1, left_leaves + [right]


import re as _re


def _detect_recursive_input(state_str: str) -> tuple[str, str, str, str]:
    """Detect if state has a recursive input type (List α or Nat).

    Returns (input_type, element_type, skeleton, variable_name).
    Returns ("", "", "", "") if no recursive input detected.

    Only activates for single-variable functions with List/Nat input.
    """
    lines = state_str.split("\n")
    params = []
    for line in lines:
        line = line.strip()
        if line.startswith("⊢"):
            break
        if line.startswith("case "):
            continue  # skip case prefix lines
        m = _re.match(r'^(\w+)\s*:\s*(.+)$', line)
        if m:
            params.append((m.group(1), m.group(2).strip()))

    if not params:
        return "", "", "", ""

    # Check primary (first) input parameter
    var_name, var_type = params[0]

    # Normalize unicode types
    normalized = var_type
    for u, a in [('ℤ', 'Int'), ('ℕ', 'Nat')]:
        normalized = normalized.replace(u, a)

    # Check for List <Type>
    list_match = _re.match(r'^List\s+(.+)$', normalized)
    if list_match:
        elem_type = list_match.group(1).strip()
        return "List", elem_type, "List.rec", var_name

    # Check for Nat
    if normalized == "Nat":
        return "Nat", "", "Nat.rec", var_name

    return "", "", "", ""


def analyze_goal(state_str: str) -> GoalStructure:
    """Analyze a Lean goal state and return its structural decomposition.

    Extracts the focused goal type (first ⊢) from the state string,
    then determines if it's a product type and generates the proof skeleton.
    Also detects recursive input types (List/Nat) for skeleton-guided search.

    Args:
        state_str: Pantograph state string, e.g.
            "xs : List Int\\n⊢ (Int × Nat) × Int"
            or with case prefix:
            "case fst\\nxs : List Int\\n⊢ Int × Nat"
    """
    # Extract goal type from first ⊢
    goal_type = None
    for line in state_str.split("\n"):
        line = line.strip()
        if line.startswith("⊢"):
            goal_type = line[1:].strip()
            break

    if not goal_type:
        return GoalStructure("", False, 0, [], 0, [])

    count, leaves = _decompose_product(goal_type)

    # Detect recursive input type (Sprint 25)
    rec_input, rec_elem, rec_skel, rec_var = _detect_recursive_input(state_str)

    # Sprint 26: foldl/map amenability
    # foldl: scalar return (Int/Nat/Bool) + list input + not product
    # map: list return + list input + not product
    is_foldl = False
    is_map = False
    if rec_input == "List" and count == 0:
        if goal_type in ("Int", "Nat", "Bool", "ℤ", "ℕ"):
            is_foldl = True
        elif goal_type.startswith("List "):
            is_map = True

    if count == 0:
        return GoalStructure(
            goal_type, False, 0, leaves, 0, ["exact"],
            is_recursive_input=bool(rec_input),
            recursive_input_type=rec_input,
            recursive_element_type=rec_elem,
            recursion_skeleton=rec_skel,
            input_variable=rec_var,
            is_foldl_amenable=is_foldl,
            is_map_amenable=is_map,
        )

    skeleton = ["constructor"] * count + ["exact"] * len(leaves)
    return GoalStructure(
        raw_type=goal_type,
        is_product=True,
        product_depth=count,
        leaf_types=leaves,
        constructor_count=count,
        skeleton=skeleton,
        is_recursive_input=bool(rec_input),
        recursive_input_type=rec_input,
        recursive_element_type=rec_elem,
        recursion_skeleton=rec_skel,
        input_variable=rec_var,
    )


def parse_sub_goals(state_str: str) -> list[str]:
    """Parse a multi-goal Pantograph state into individual sub-goal strings.

    After a constructor chain, Pantograph returns a state with multiple goals
    separated by "case ..." prefixes:

        case fst.fst
        xs : List Int
        ⊢ Int
        case fst.snd
        xs : List Int
        ⊢ Nat
        case snd
        xs : List Int
        ⊢ Int

    Returns each sub-goal WITHOUT the case prefix:
        ["xs : List Int\\n⊢ Int", "xs : List Int\\n⊢ Nat", "xs : List Int\\n⊢ Int"]
    """
    lines = state_str.split("\n")

    # Find case prefix line indices
    case_indices = [
        i for i, line in enumerate(lines)
        if line.strip().startswith("case ")
    ]

    if not case_indices:
        # No case prefixes — single goal
        return [state_str.strip()]

    sub_goals = []
    for j, start in enumerate(case_indices):
        end = case_indices[j + 1] if j + 1 < len(case_indices) else len(lines)
        # Skip the case prefix line, take remaining lines
        block_lines = lines[start + 1:end]
        block = "\n".join(line for line in block_lines if line.strip()).strip()
        if block:
            sub_goals.append(block)

    return sub_goals
