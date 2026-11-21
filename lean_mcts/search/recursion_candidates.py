"""
Recursion candidate generation for skeleton-guided MCTS (Sprint 25+26).

Generates (base_case, step_function) pairs for List.rec and Nat.rec
eliminators, then combines them into complete `exact` tactics.

List.rec is a right fold:
    List.rec base (fun h _ acc => ...) xs
    List.rec base f [a,b,c] = f a [b,c] (f b [c] (f c [] base))

With `fun h _ acc => h :: acc`, cons builds lists in correct order.

Nat.rec unfolds as:
    Nat.rec base (fun k acc => ...) n
    Nat.rec base f 3 = f 2 (f 1 (f 0 base))

Sprint 26 additions:
- Conditional-map templates (keep all elements, transform conditionally)
- Arithmetic primitives (natAbs, cubic, alternating)
- Tuple-accumulator templates (pair state for sorted checks, dedup)
"""

from __future__ import annotations


# --- Base cases by return type ---

BASE_CASES: dict[str, list[str]] = {
    "Nat": ["0", "1"],
    "Int": ["0", "1", "-1"],
    "Bool": ["true", "false"],
    "List Int": ["[]"],
    "List Nat": ["[]"],
    "List Bool": ["[]"],
}

# --- Step function templates for List.rec ---
# Convention: fun h _ acc => <body>
# h = head element (type = element_type)
# acc = recursive result (type = return_type)

# List Int → Int (arithmetic accumulator)
STEP_LIST_INT_TO_INT = [
    "fun h _ acc => acc + h",
    "fun h _ acc => acc - h",
    "fun h _ acc => acc * h",
    "fun h _ acc => if decide (h > 0) then acc + h else acc",
    "fun h _ acc => if decide (h > 0) then acc + 1 else acc",
    "fun h _ acc => if decide (h < 0) then acc + h else acc",
    "fun h _ acc => if decide (h < 0) then acc + 1 else acc",
    "fun h _ acc => if decide (h > acc) then h else acc",
    "fun h _ acc => if decide (h < acc) then h else acc",
    "fun h _ acc => acc + h * h",
    "fun h _ _ => h",                                          # first element
    # Sprint 26: arithmetic primitives
    "fun h _ acc => h - acc",                                   # alternating sum (right-fold)
    "fun h _ acc => acc + if decide (h ≥ 0) then h else -h",   # sum of abs
    "fun h _ acc => acc + h * h * h",                           # sum of cubes (list)
    "fun h _ acc => if decide (h > 0) then acc + h * h else acc",  # sum squares of positives
    "fun h _ acc => if decide (h < 0) then acc + h * h else acc",  # sum squares of negatives
]

# List Int → Nat (counting accumulator)
STEP_LIST_INT_TO_NAT = [
    "fun _ _ acc => acc + 1",                                   # length
    "fun h _ acc => if decide (h > 0) then acc + 1 else acc",   # count positive
    "fun h _ acc => if decide (h < 0) then acc + 1 else acc",   # count negative
    "fun h _ acc => if decide (h % 2 = 0) then acc + 1 else acc",  # count even
    "fun h _ acc => if decide (h % 2 ≠ 0) then acc + 1 else acc",  # count odd
    "fun h _ acc => if decide (h = 0) then acc + 1 else acc",   # count zeros
]

# List Int → List Int (list-building via cons)
STEP_LIST_INT_TO_LIST_INT = [
    "fun h _ acc => h :: acc",                                  # identity (reverse)
    "fun h _ acc => h :: h :: acc",                             # stutter
    "fun h _ acc => (-h) :: acc",                               # negate
    "fun h _ acc => (h * h) :: acc",                            # square
    "fun h _ acc => (h * 2) :: acc",                            # double
    "fun h _ acc => (h + 1) :: acc",                            # increment
    "fun h _ acc => if decide (h > 0) then h :: acc else acc",  # filter positive
    "fun h _ acc => if decide (h < 0) then h :: acc else acc",  # filter negative
    "fun h _ acc => if decide (h % 2 = 0) then h :: acc else acc",  # filter even
    "fun h _ acc => if decide (h % 2 ≠ 0) then h :: acc else acc",  # filter odd
    "fun h _ acc => if decide (h ≥ 0) then h :: acc else acc",  # filter non-negative
    # Sprint 26: conditional-map templates (keep all elements, transform conditionally)
    "fun h _ acc => (if decide (h > 0) then h * 2 else h) :: acc",   # double positives
    "fun h _ acc => (if decide (h > 0) then h + 1 else h) :: acc",   # increment positives
    "fun h _ acc => (if decide (h > 0) then h * h else h) :: acc",   # square positives
    "fun h _ acc => (if decide (h < 0) then 0 else h) :: acc",       # replace neg with 0
    "fun h _ acc => (if decide (h < 0) then -h else h) :: acc",      # abs
    "fun h _ acc => (if decide (h > 0) then h else 0) :: acc",       # ReLU
    "fun h _ acc => (max (-5) (min 5 h)) :: acc",                    # clamp [-5,5]
    "fun h _ acc => (max (-10) (min 10 h)) :: acc",                  # clamp [-10,10]
    "fun h _ acc => (max 0 h) :: acc",                               # clamp [0,∞)
    "fun h _ acc => (if decide (h % 2 = 0) then h * 2 else h) :: acc",  # double evens
    "fun h _ acc => (if decide (h % 2 ≠ 0) then h * 2 else h) :: acc",  # double odds
]

# List Int → Bool
STEP_LIST_INT_TO_BOOL = [
    "fun h _ acc => if decide (h > 0) then acc else false",
    "fun h _ acc => if decide (h % 2 = 0) then acc else false",
]

# --- Step function templates for Nat.rec ---
# Convention: fun k acc => <body>
# k = predecessor (Nat), acc = recursive result (return_type)

STEP_NAT_TO_NAT = [
    "fun k acc => acc + (k + 1)",                               # triangular / sum
    "fun k acc => acc * (k + 1)",                               # factorial
    "fun _ acc => acc * 2",                                     # power of 2
    "fun _ acc => acc + 1",                                     # identity (n itself)
    "fun k acc => acc + 2 * k + 1",                             # square (odd sums)
    "fun k acc => acc + k + 1",                                 # triangular alt
    "fun _ acc => acc * 3",                                     # power of 3
    "fun k acc => acc * k + 1",                                 # Catalan-like
    # Sprint 26: arithmetic primitives
    "fun k acc => acc + (k + 1) * (k + 1) * (k + 1)",          # sum of cubes
    "fun k acc => acc + (k + 1) * (k + 1)",                    # sum of squares
    "fun k acc => acc * (k + 2)",                               # double factorial-like
    "fun k acc => acc + 2 * (k + 1) - 1",                      # sum of odds
]

STEP_NAT_TO_INT = [
    "fun k acc => acc + (↑k + 1)",                              # sum to Int
    "fun k acc => acc * (↑k + 1)",                              # factorial to Int
    "fun _ acc => acc * 2",                                     # power of 2 to Int
]


def _get_step_templates(input_type: str, element_type: str, return_type: str) -> list[str]:
    """Get step function templates for a given (input_type, return_type) pair."""
    if input_type == "List":
        if element_type == "Int":
            if return_type == "Int":
                return STEP_LIST_INT_TO_INT
            elif return_type == "Nat":
                return STEP_LIST_INT_TO_NAT
            elif return_type == "List Int":
                return STEP_LIST_INT_TO_LIST_INT
            elif return_type == "Bool":
                return STEP_LIST_INT_TO_BOOL
    elif input_type == "Nat":
        if return_type == "Nat":
            return STEP_NAT_TO_NAT
        elif return_type == "Int":
            return STEP_NAT_TO_INT
    return []


def generate_recursion_candidates(
    input_type: str,
    element_type: str,
    return_type: str,
    input_var: str,
    max_candidates: int = 100,
) -> list[str]:
    """Generate candidate `exact` tactics using List.rec or Nat.rec.

    Args:
        input_type: "List" or "Nat"
        element_type: e.g. "Int" for List Int, "" for Nat
        return_type: e.g. "Int", "Nat", "List Int"
        input_var: e.g. "xs", "n"
        max_candidates: cap on total candidates

    Returns:
        List of "exact <expr>" tactic strings.
    """
    bases = BASE_CASES.get(return_type, [])
    steps = _get_step_templates(input_type, element_type, return_type)

    if not bases or not steps:
        return []

    rec_fn = "List.rec" if input_type == "List" else "Nat.rec"

    candidates = []
    for base in bases:
        for step in steps:
            expr = f"exact {rec_fn} {base} ({step}) {input_var}"
            candidates.append(expr)
            if len(candidates) >= max_candidates:
                return candidates

    return candidates
