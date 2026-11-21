"""Unit tests for goal_analyzer.py (Sprint 24 + Sprint 25 recursion)."""

import sys
from pathlib import Path

# Allow running from lean_mcts/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from search.goal_analyzer import GoalStructure, analyze_goal, parse_sub_goals, _decompose_product, _detect_recursive_input


# --- _decompose_product tests ---

def test_atomic_int():
    count, leaves = _decompose_product("Int")
    assert count == 0
    assert leaves == ["Int"]

def test_atomic_nat():
    count, leaves = _decompose_product("Nat")
    assert count == 0
    assert leaves == ["Nat"]

def test_atomic_list_int():
    count, leaves = _decompose_product("List Int")
    assert count == 0
    assert leaves == ["List Int"]

def test_atomic_list_nat():
    count, leaves = _decompose_product("List Nat")
    assert count == 0
    assert leaves == ["List Nat"]

def test_atomic_list_with_product_inside():
    """List (Int × Nat) is NOT a product — the × is inside List."""
    count, leaves = _decompose_product("List (Int × Nat)")
    assert count == 0
    assert leaves == ["List (Int × Nat)"]

def test_depth1_int_nat():
    count, leaves = _decompose_product("Int × Nat")
    assert count == 1
    assert leaves == ["Int", "Nat"]

def test_depth1_list_int_list_int():
    count, leaves = _decompose_product("List Int × List Int")
    assert count == 1
    assert leaves == ["List Int", "List Int"]

def test_depth2_int_nat_int():
    count, leaves = _decompose_product("(Int × Nat) × Int")
    assert count == 2
    assert leaves == ["Int", "Nat", "Int"]

def test_depth2_int_int_nat():
    count, leaves = _decompose_product("(Int × Int) × Nat")
    assert count == 2
    assert leaves == ["Int", "Int", "Nat"]

def test_depth2_nat_nat_nat():
    count, leaves = _decompose_product("(Nat × Nat) × Nat")
    assert count == 2
    assert leaves == ["Nat", "Nat", "Nat"]

def test_depth3():
    count, leaves = _decompose_product("((Int × Nat) × Int) × Bool")
    assert count == 3
    assert leaves == ["Int", "Nat", "Int", "Bool"]

def test_outer_parens_stripped():
    count, leaves = _decompose_product("(Int × Nat)")
    assert count == 1
    assert leaves == ["Int", "Nat"]

def test_right_nested_treated_as_single_leaf():
    """Right-nested product: right branch kept as single leaf."""
    count, leaves = _decompose_product("Int × (Nat × Bool)")
    assert count == 1
    assert leaves == ["Int", "(Nat × Bool)"]


# --- analyze_goal tests ---

def test_analyze_atomic():
    s = analyze_goal("xs : List Int\n⊢ Nat")
    assert not s.is_product
    assert s.product_depth == 0
    assert s.leaf_types == ["Nat"]
    assert s.constructor_count == 0
    assert s.skeleton == ["exact"]

def test_analyze_depth1():
    s = analyze_goal("xs : List Int\n⊢ Int × Nat")
    assert s.is_product
    assert s.product_depth == 1
    assert s.leaf_types == ["Int", "Nat"]
    assert s.constructor_count == 1
    assert s.skeleton == ["constructor", "exact", "exact"]

def test_analyze_depth2():
    s = analyze_goal("xs : List Int\n⊢ (Int × Nat) × Int")
    assert s.is_product
    assert s.product_depth == 2
    assert s.leaf_types == ["Int", "Nat", "Int"]
    assert s.constructor_count == 2
    assert s.skeleton == ["constructor", "constructor", "exact", "exact", "exact"]

def test_analyze_depth2_nat_triple():
    s = analyze_goal("xs : List Int\n⊢ (Nat × Nat) × Nat")
    assert s.is_product
    assert s.constructor_count == 2
    assert s.leaf_types == ["Nat", "Nat", "Nat"]

def test_analyze_with_case_prefix():
    state = "case fst\nxs : List Int\n⊢ Int × Nat\ncase snd\nxs : List Int\n⊢ Int"
    s = analyze_goal(state)
    # Should use first ⊢ (focused goal)
    assert s.is_product
    assert s.raw_type == "Int × Nat"
    assert s.leaf_types == ["Int", "Nat"]

def test_analyze_list_type_not_product():
    s = analyze_goal("xs : List Int\n⊢ List (Int × Nat)")
    assert not s.is_product
    assert s.leaf_types == ["List (Int × Nat)"]

def test_analyze_empty_state():
    s = analyze_goal("")
    assert not s.is_product
    assert s.leaf_types == []

def test_analyze_no_turnstile():
    s = analyze_goal("xs : List Int")
    assert not s.is_product

def test_analyze_list_product():
    """List Int × List Int is a product of two list types."""
    s = analyze_goal("xs : List Int\n⊢ List Int × List Int")
    assert s.is_product
    assert s.constructor_count == 1
    assert s.leaf_types == ["List Int", "List Int"]
    assert s.skeleton == ["constructor", "exact", "exact"]


# --- parse_sub_goals tests ---

def test_parse_single_goal():
    state = "xs : List Int\n⊢ Nat"
    goals = parse_sub_goals(state)
    assert len(goals) == 1
    assert goals[0] == "xs : List Int\n⊢ Nat"

def test_parse_two_goals():
    state = "case fst\nxs : List Int\n⊢ Int\ncase snd\nxs : List Int\n⊢ Nat"
    goals = parse_sub_goals(state)
    assert len(goals) == 2
    assert goals[0] == "xs : List Int\n⊢ Int"
    assert goals[1] == "xs : List Int\n⊢ Nat"

def test_parse_three_goals():
    state = (
        "case fst.fst\nxs : List Int\n⊢ Int\n"
        "case fst.snd\nxs : List Int\n⊢ Nat\n"
        "case snd\nxs : List Int\n⊢ Int"
    )
    goals = parse_sub_goals(state)
    assert len(goals) == 3
    assert "⊢ Int" in goals[0]
    assert "⊢ Nat" in goals[1]
    assert "⊢ Int" in goals[2]

def test_parse_preserves_context():
    state = (
        "case fst\n"
        "xs : List Int\n"
        "ys : List Int\n"
        "⊢ Int\n"
        "case snd\n"
        "xs : List Int\n"
        "ys : List Int\n"
        "⊢ Nat"
    )
    goals = parse_sub_goals(state)
    assert len(goals) == 2
    assert "xs : List Int" in goals[0]
    assert "ys : List Int" in goals[0]
    assert "⊢ Int" in goals[0]


# --- _detect_recursive_input tests (Sprint 25) ---

def test_detect_list_int():
    inp, elem, skel, var = _detect_recursive_input("xs : List Int\n⊢ Int")
    assert inp == "List"
    assert elem == "Int"
    assert skel == "List.rec"
    assert var == "xs"

def test_detect_list_nat():
    inp, elem, skel, var = _detect_recursive_input("xs : List Nat\n⊢ Nat")
    assert inp == "List"
    assert elem == "Nat"
    assert skel == "List.rec"
    assert var == "xs"

def test_detect_nat():
    inp, elem, skel, var = _detect_recursive_input("n : Nat\n⊢ Nat")
    assert inp == "Nat"
    assert elem == ""
    assert skel == "Nat.rec"
    assert var == "n"

def test_detect_unicode_list():
    inp, elem, skel, var = _detect_recursive_input("xs : List ℤ\n⊢ ℤ")
    assert inp == "List"
    assert elem == "Int"
    assert skel == "List.rec"

def test_detect_unicode_nat():
    inp, elem, skel, var = _detect_recursive_input("n : ℕ\n⊢ ℕ")
    assert inp == "Nat"
    assert skel == "Nat.rec"

def test_detect_no_recursive():
    inp, elem, skel, var = _detect_recursive_input("x : Int\n⊢ Int")
    assert inp == ""
    assert skel == ""

def test_detect_product_with_list_input():
    """Product goal with List input: both product and recursive detected."""
    inp, elem, skel, var = _detect_recursive_input("xs : List Int\n⊢ Int × Nat")
    assert inp == "List"
    assert skel == "List.rec"

def test_detect_empty_state():
    inp, elem, skel, var = _detect_recursive_input("")
    assert inp == ""

# --- analyze_goal recursion detection (Sprint 25) ---

def test_analyze_recursive_list_int():
    s = analyze_goal("xs : List Int\n⊢ Int")
    assert not s.is_product
    assert s.is_recursive_input
    assert s.recursive_input_type == "List"
    assert s.recursive_element_type == "Int"
    assert s.recursion_skeleton == "List.rec"
    assert s.input_variable == "xs"

def test_analyze_recursive_nat():
    s = analyze_goal("n : Nat\n⊢ Nat")
    assert not s.is_product
    assert s.is_recursive_input
    assert s.recursive_input_type == "Nat"
    assert s.recursive_element_type == ""
    assert s.recursion_skeleton == "Nat.rec"
    assert s.input_variable == "n"

def test_analyze_recursive_list_to_list():
    s = analyze_goal("xs : List Int\n⊢ List Int")
    assert s.is_recursive_input
    assert s.recursive_input_type == "List"
    assert s.raw_type == "List Int"

def test_analyze_product_not_recursive_skeleton():
    """Product goal: is_product takes priority, but recursion is also detected."""
    s = analyze_goal("xs : List Int\n⊢ Int × Nat")
    assert s.is_product
    assert s.is_recursive_input  # both detected

def test_analyze_int_input_not_recursive():
    s = analyze_goal("x : Int\n⊢ Int")
    assert not s.is_recursive_input

def test_analyze_case_prefix_recursive():
    s = analyze_goal("case h\nxs : List Int\n⊢ Int")
    # case prefix: params are after the case line
    # _detect_recursive_input skips 'case' lines
    assert s.is_recursive_input
    assert s.recursive_input_type == "List"


# --- Run all tests ---

if __name__ == "__main__":
    test_funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for fn in test_funcs:
        try:
            fn()
            passed += 1
            print(f"  PASS: {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL: {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR: {fn.__name__}: {e}")

    print(f"\n{passed}/{passed + failed} tests passed")
    if failed:
        sys.exit(1)
