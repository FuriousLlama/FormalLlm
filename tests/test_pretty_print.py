"""Tests for pretty_print.py — de Bruijn token sequence → Haskell source.

Covers:
  - De Bruijn index → variable name resolution
  - Cons pattern variable ordering (#1 from audit)
  - Lambda binder environment construction
  - Nested lambda scoping
  - Let binding mutual recursion
  - Case pattern binding
  - GlobalRef passthrough
  - Infix operator rendering
  - Parenthesization of nested applications
  - pp_expr / pp_func direct testing
  - Error handling for malformed input
"""

import pytest
from pretty_print import (
    tokens_to_haskell, pp_expr, pp_func, _lookup, _fresh, _pat_bind,
    _is_debruijn,
)
from checker import parse_sequence, ParseError, TokenStream


# ── Helpers ──────────────────────────────────────────────────────────────────

def haskell_from(token_str: str) -> str:
    """Parse tokens and return the Haskell definition string."""
    tokens = token_str.split()
    _, h = tokens_to_haskell(tokens)
    return h


# ── De Bruijn index resolution ───────────────────────────────────────────────

class TestLookup:
    def test_index_zero_is_innermost(self):
        # env[0] = innermost binding
        assert _lookup(["x", "y", "z"], "0") == "x"

    def test_index_one_is_next(self):
        assert _lookup(["x", "y", "z"], "1") == "y"

    def test_index_two_is_outermost(self):
        assert _lookup(["x", "y", "z"], "2") == "z"

    def test_free_var_passthrough(self):
        assert _lookup(["x"], "myVar") == "myVar"

    def test_out_of_scope_index(self):
        assert _lookup(["x"], "5") == "_db5"

    def test_negative_index(self):
        # Negative de Bruijn indices shouldn't happen but shouldn't crash
        assert _lookup(["x"], "-1") == "_db-1"

    def test_self_passthrough(self):
        assert _lookup([], "SELF") == "SELF"


# ── Fresh name generation ────────────────────────────────────────────────────

class TestFresh:
    def test_first_names(self):
        gen = [0]
        assert _fresh(3, gen) == ["x", "y", "z"]
        assert gen[0] == 3

    def test_no_collision_across_calls(self):
        gen = [0]
        a = _fresh(2, gen)
        b = _fresh(2, gen)
        assert set(a).isdisjoint(set(b))

    def test_overflow_to_numbered(self):
        gen = [25]  # _VAR_NAMES has 25 entries (xyzwvutsrqponmlkjihgabcde)
        names = _fresh(2, gen)
        assert names == ["x0", "x1"]


# ── Simple function definitions ──────────────────────────────────────────────

class TestSimpleFunctions:
    def test_identity(self):
        h = haskell_from("DEF fId TY Int -> Int BODY (Lam 1 Var 0 ) EOF")
        assert h == "f x = x"

    def test_double(self):
        h = haskell_from("DEF fDouble TY Int -> Int BODY (Lam 1 (App Prim + Var 0 Var 0 ) ) EOF")
        assert h == "f x = x + x"

    def test_constant(self):
        h = haskell_from("DEF fConst TY Int -> Int BODY (Lam 1 Lit 42 ) EOF")
        assert h == "f x = 42"

    def test_two_arg_function(self):
        h = haskell_from("DEF fAdd TY Int -> Int -> Int BODY (Lam 2 (App Prim + Var 1 Var 0 ) ) EOF")
        assert h == "f x y = x + y"

    def test_two_arg_reversed(self):
        h = haskell_from("DEF fSub TY Int -> Int -> Int BODY (Lam 2 (App Prim - Var 0 Var 1 ) ) EOF")
        assert h == "f x y = y - x"


# ── Nested lambda scoping ───────────────────────────────────────────────────

class TestNestedLambda:
    def test_nested_lambda_distinct_names(self):
        # \x -> \y -> x → (Lam 1 (Lam 1 Var 1))
        h = haskell_from("DEF f TY a -> b -> a BODY (Lam 1 (Lam 1 Var 1 ) ) EOF")
        # Inner lambda not wrapped in parens (parens=False by default from pp_func)
        assert h == "f x = \\y -> x"

    def test_nested_lambda_inner_var(self):
        # \x -> \y -> y → (Lam 1 (Lam 1 Var 0))
        h = haskell_from("DEF f TY a -> b -> b BODY (Lam 1 (Lam 1 Var 0 ) ) EOF")
        assert h == "f x = \\y -> y"


# ── Case expressions and pattern binding ─────────────────────────────────────

class TestCaseExpr:
    def test_bool_case(self):
        # case x of { True -> 1; False -> 0 }
        # Note: (PCon True ) and (PCon False ) need spaces before )
        h = haskell_from(
            "DEF f TY Bool -> Int BODY "
            "(Lam 1 (Case Var 0 "
            "(Alt (PCon True ) Lit 1 ) "
            "(Alt (PCon False ) Lit 0 ) "
            ") ) EOF"
        )
        assert "f x = case x of {True -> 1; False -> 0}" == h

    def test_cons_pattern_head_access(self):
        """Cons pattern (h:t): head = index 1, tail = index 0.

        pp_func uses x for the Lam param (gen=0→1).
        _pat_bind(arity=2) generates [y, z] (gen=1→3), reversed env = [z, y, x].
        Pattern string: (y:z). Index 1 → env[1] = y (head).
        """
        h = haskell_from(
            "DEF fHead TY [ Int ] -> Int BODY "
            "(Lam 1 (Case Var 0 "
            "(Alt (PCon : 2 ) Var 1 ) "
            "(Alt (PCon [] ) Lit 0 ) "
            ") ) EOF"
        )
        assert "-> y" in h, f"Head access failed: {h}"

    def test_cons_pattern_tail_access(self):
        """Tail = index 0 in the cons pattern → env[0] = z."""
        h = haskell_from(
            "DEF fTail TY [ Int ] -> [ Int ] BODY "
            "(Lam 1 (Case Var 0 "
            "(Alt (PCon : 2 ) Var 0 ) "
            "(Alt (PCon [] ) Con [] ) "
            ") ) EOF"
        )
        assert "-> z" in h, f"Tail access failed: {h}"

    def test_cons_pattern_both_vars(self):
        """Using both head and tail: should use different variables."""
        h = haskell_from(
            "DEF f TY [ Int ] -> [ Int ] BODY "
            "(Lam 1 (Case Var 0 "
            "(Alt (PCon : 2 ) (App Con : Var 1 Var 0 ) ) "
            "(Alt (PCon [] ) Con [] ) "
            ") ) EOF"
        )
        # Pattern (y:z), body uses y (head, index 1) and z (tail, index 0)
        assert h.count("y") >= 2, f"Head var should appear twice: {h}"
        assert h.count("z") >= 2, f"Tail var should appear twice: {h}"

    def test_pwild_pattern(self):
        h = haskell_from(
            "DEF f TY Int -> Int BODY "
            "(Lam 1 (Case Var 0 "
            "(Alt (PWild) Lit 0 ) "
            ") ) EOF"
        )
        assert "_ -> 0" in h

    def test_plit_pattern(self):
        h = haskell_from(
            "DEF f TY Int -> Int BODY "
            "(Lam 1 (Case Var 0 "
            "(Alt (PLit 0 ) Lit 1 ) "
            "(Alt (PWild) Lit 0 ) "
            ") ) EOF"
        )
        assert "0 -> 1" in h

    def test_tuple_pattern(self):
        # case p of { (a, b) -> a + b }
        h = haskell_from(
            "DEF f TY ( Int , Int ) -> Int BODY "
            "(Lam 1 (Case Var 0 "
            "(Alt (PTuple 2 ) (App Prim + Var 1 Var 0 ) ) "
            ") ) EOF"
        )
        assert "+" in h


# ── Let expressions ──────────────────────────────────────────────────────────

class TestLetExpr:
    def test_simple_let(self):
        h = haskell_from(
            "DEF f TY Int BODY "
            "(Let (Bind Lit 42 ) Var 0 ) EOF"
        )
        assert "let" in h
        assert "42" in h

    def test_let_with_outer_scope(self):
        h = haskell_from(
            "DEF f TY Int -> Int BODY "
            "(Lam 1 (Let (Bind Var 0 ) Var 0 ) ) EOF"
        )
        assert "let" in h


# ── GlobalRef ────────────────────────────────────────────────────────────────

class TestGlobalRef:
    def test_global_ref_passthrough(self):
        h = haskell_from(
            "DEF f TY [ Int ] -> Int BODY "
            "(Lam 1 (App fSum Var 0 ) ) EOF"
        )
        assert "fSum" in h
        assert "fSum x" in h

    def test_global_ref_nested(self):
        h = haskell_from(
            "DEF f TY [ Int ] -> Int BODY "
            "(Lam 1 (App fSum (App fMap fDouble Var 0 ) ) ) EOF"
        )
        assert "fSum" in h
        assert "fMap" in h
        assert "fDouble" in h


# ── Infix operators ──────────────────────────────────────────────────────────

class TestInfixOps:
    def test_binary_prim(self):
        h = haskell_from(
            "DEF f TY Int -> Int -> Int BODY "
            "(Lam 2 (App Prim + Var 1 Var 0 ) ) EOF"
        )
        assert "x + y" in h

    def test_comparison(self):
        h = haskell_from(
            "DEF f TY Int -> Int -> Bool BODY "
            "(Lam 2 (App Prim == Var 1 Var 0 ) ) EOF"
        )
        assert "x == y" in h

    def test_cons_infix(self):
        h = haskell_from(
            "DEF f TY Int -> [ Int ] -> [ Int ] BODY "
            "(Lam 2 (App Con : Var 1 Var 0 ) ) EOF"
        )
        assert ":" in h


# ── Self-recursive ───────────────────────────────────────────────────────────

class TestSelfRecursion:
    def test_self_becomes_f(self):
        h = haskell_from(
            "DEF fFact TY Int -> Int BODY "
            "(Lam 1 (Case Var 0 "
            "(Alt (PLit 0 ) Lit 1 ) "
            "(Alt (PWild) (App Prim * Var 0 (App Var SELF (App Prim - Var 0 Lit 1 ) ) ) ) "
            ") ) EOF"
        )
        assert "f (" in h or "f x" in h


# ── Tuple expressions ────────────────────────────────────────────────────────

class TestTupleExpr:
    def test_tuple_construction(self):
        h = haskell_from(
            "DEF f TY Int -> Int -> ( Int , Int ) BODY "
            "(Lam 2 (Tuple Var 1 Var 0 ) ) EOF"
        )
        assert "(" in h
        assert "," in h


# ── Parenthesization ────────────────────────────────────────────────────────

class TestParenthesization:
    def test_nested_app_in_arg(self):
        """f (g x) should parenthesize the inner application."""
        h = haskell_from(
            "DEF f TY [ Int ] -> Int BODY "
            "(Lam 1 (App fSum (App fMap fDouble Var 0 ) ) ) EOF"
        )
        assert "(fMap fDouble x)" in h or "(fMap" in h

    def test_infix_no_extra_parens_at_toplevel(self):
        """Top-level infix: f x y = x + y should not be wrapped in parens."""
        h = haskell_from(
            "DEF f TY Int -> Int -> Int BODY "
            "(Lam 2 (App Prim + Var 1 Var 0 ) ) EOF"
        )
        assert h == "f x y = x + y"

    def test_infix_in_arg_position(self):
        """Infix expression as argument should get parens."""
        h = haskell_from(
            "DEF f TY Int -> Int BODY "
            "(Lam 1 (App Prim * (App Prim + Var 0 Lit 1 ) Lit 2 ) ) EOF"
        )
        assert "(x + 1)" in h


# ── pp_expr direct tests ────────────────────────────────────────────────────

class TestPpExprDirect:
    def test_none_ast(self):
        result = pp_expr(None, "f", [], [0])
        assert result == "undefined"

    def test_tyarg_erased(self):
        result = pp_expr({"tag": "TyArg", "type": "Int"}, "f", [], [0])
        assert result == ""

    def test_con_colon(self):
        result = pp_expr({"tag": "Con", "name": ":"}, "f", [], [0])
        assert result == "(:)"

    def test_prim_parens(self):
        result = pp_expr({"tag": "Prim", "op": "+"}, "f", [], [0])
        assert result == "(+)"

    def test_var_self_becomes_f(self):
        result = pp_expr({"tag": "Var", "name": "SELF"}, "fFact", [], [0])
        assert result == "f"

    def test_var_func_name_becomes_f(self):
        result = pp_expr({"tag": "Var", "name": "fFact"}, "fFact", ["fFact"], [0])
        assert result == "f"

    def test_unknown_tag(self):
        result = pp_expr({"tag": "WeirdTag"}, "f", [], [0])
        assert "undefined" in result


# ── pp_func direct tests ────────────────────────────────────────────────────

class TestPpFuncDirect:
    def test_non_lam_body(self):
        """Function body that isn't a Lam → f = <body>."""
        ast = {"tag": "Lit", "val": "42"}
        result = pp_func("fConst", ast, [0])
        assert result == "f = 42"

    def test_lam_zero_val_binders(self):
        """Lam with n_val=0 → f = <body>."""
        ast = {"tag": "Lam", "n_type": 1, "n_val": 0, "body": {"tag": "Lit", "val": "1"}}
        result = pp_func("f", ast, [0])
        assert result == "f = 1"


# ── _is_debruijn ────────────────────────────────────────────────────────────

class TestIsDebruijn:
    def test_positive(self):
        assert _is_debruijn("0") is True
        assert _is_debruijn("42") is True

    def test_negative(self):
        assert _is_debruijn("-1") is True

    def test_not_int(self):
        assert _is_debruijn("abc") is False
        assert _is_debruijn("") is False

    def test_self_not_debruijn(self):
        assert _is_debruijn("SELF") is False


# ── _pat_bind direct tests ──────────────────────────────────────────────────

class TestPatBindDirect:
    def test_none_pattern(self):
        env, pat_str = _pat_bind(None, ["x"], [0])
        assert pat_str == "_"
        assert env == ["x"]

    def test_pwild(self):
        env, pat_str = _pat_bind({"tag": "PWild"}, ["x"], [0])
        assert pat_str == "_"

    def test_plit(self):
        env, pat_str = _pat_bind({"tag": "PLit", "val": "0"}, ["x"], [1])
        assert pat_str == "0"
        assert env == ["x"]  # unchanged

    def test_pcon_nil(self):
        env, pat_str = _pat_bind({"tag": "PCon", "con": "[]", "arity": 0}, ["outer"], [0])
        assert pat_str == "[]"

    def test_pcon_cons(self):
        gen = [0]
        env, pat_str = _pat_bind({"tag": "PCon", "con": ":", "arity": 2}, ["outer"], gen)
        assert ":" in pat_str
        assert "x" in pat_str and "y" in pat_str

    def test_ptuple(self):
        gen = [0]
        env, pat_str = _pat_bind({"tag": "PTuple", "arity": 2}, ["outer"], gen)
        assert "," in pat_str
        assert len(env) == 3  # 2 new + outer

    def test_unknown_pattern(self):
        env, pat_str = _pat_bind({"tag": "PBogus"}, ["x"], [0])
        assert pat_str == "_"


# ── Error handling ──────────────────────────────────────────────────────────

class TestErrorHandling:
    def test_empty_sequence_raises(self):
        with pytest.raises(ParseError):
            tokens_to_haskell([])

    def test_body_none_raises(self):
        with pytest.raises(ParseError):
            tokens_to_haskell(["DEF", "fId"])


# ── Let with multiple bindings ──────────────────────────────────────────────

class TestLetMultipleBindings:
    def test_two_bindings_distinct_names(self):
        h = haskell_from(
            "DEF f TY Int BODY "
            "(Let (Bind Lit 1 ) (Bind Lit 2 ) (App Prim + Var 1 Var 0 ) ) EOF"
        )
        assert "let" in h
        assert "1" in h
        assert "2" in h
        # Both binding names should be distinct
        assert "x = 1" in h
        assert "y = 2" in h
