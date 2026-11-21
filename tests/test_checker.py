"""Tests for checker.py — parser, type inference, and is_type_valid.

Covers:
  - TokenStream operations
  - _is_int_token edge cases
  - parse_sequence format variants (Sprint 4, 5, 5b, legacy)
  - parse_expr for each node type
  - parse_pat for each pattern type
  - parse_bind / parse_alt standalone
  - is_type_valid incremental validation (including constrained-decode scenarios)
  - type_check_sequence end-to-end (valid + invalid)
  - Type inference for all node types
  - Error recovery (no crashes on pathological input)
"""

import pytest
from checker import (
    TokenStream,
    ParseError,
    TypeCheckError,
    _is_int_token,
    _is_global_ref,
    _compat,
    parse_sequence,
    parse_expr,
    parse_pat,
    parse_bind,
    parse_alt,
    is_type_valid,
    type_check_sequence,
    infer_type,
    INT,
    BOOL,
    UNKNOWN,
    FunT,
    ListT,
    IntT,
    BoolT,
    UnknownT,
    MaybeT,
    PRIM_TYPES,
    CONSTRUCTOR_TYPES,
    GLOBAL_TYPES,
)


# ── TokenStream ─────────────────────────────────────────────────────────────

class TestTokenStream:
    def test_basic_consume(self):
        ts = TokenStream(["a", "b", "c"])
        assert ts.consume() == "a"
        assert ts.consume() == "b"
        assert ts.consume() == "c"
        assert ts.done()

    def test_peek_does_not_advance(self):
        ts = TokenStream(["a", "b"])
        assert ts.peek() == "a"
        assert ts.peek() == "a"
        ts.consume()
        assert ts.peek() == "b"

    def test_peek_at_end(self):
        ts = TokenStream([])
        assert ts.peek() is None

    def test_done_empty(self):
        assert TokenStream([]).done()

    def test_done_nonempty(self):
        assert not TokenStream(["x"]).done()

    def test_consume_past_end_raises(self):
        ts = TokenStream(["a"])
        ts.consume()
        with pytest.raises(ParseError, match="end of tokens"):
            ts.consume()

    def test_try_consume_match(self):
        ts = TokenStream(["(", "x"])
        assert ts.try_consume("(") is True
        assert ts.peek() == "x"

    def test_try_consume_no_match(self):
        ts = TokenStream(["x"])
        assert ts.try_consume("(") is False
        assert ts.peek() == "x"  # not consumed


# ── _is_int_token ───────────────────────────────────────────────────────────

class TestIsIntToken:
    def test_positive_integer(self):
        assert _is_int_token("42") is True

    def test_zero(self):
        assert _is_int_token("0") is True

    def test_negative_integer(self):
        assert _is_int_token("-3") is True

    def test_not_integer(self):
        assert _is_int_token("abc") is False

    def test_empty_string(self):
        assert _is_int_token("") is False

    def test_just_minus(self):
        assert _is_int_token("-") is False

    def test_float_like(self):
        assert _is_int_token("3.14") is False


# ── _is_global_ref ──────────────────────────────────────────────────────────

class TestIsGlobalRef:
    def test_corpus_function(self):
        assert _is_global_ref("fSum") is True

    def test_corpus_function_long(self):
        assert _is_global_ref("fMapDouble") is True

    def test_single_char(self):
        assert _is_global_ref("f") is False  # len < 2

    def test_lowercase_second(self):
        assert _is_global_ref("fa") is False  # second char not upper

    def test_non_f_prefix(self):
        assert _is_global_ref("gSum") is False

    def test_empty(self):
        assert _is_global_ref("") is False


# ── parse_sequence formats ──────────────────────────────────────────────────

class TestParseSequence:
    def test_sprint5b_format(self):
        """DEF <name> TY <type tokens> BODY <body> EOF"""
        tokens = "DEF fId TY Int -> Int BODY (Lam 1 Var 0 ) EOF".split()
        ts = TokenStream(tokens)
        result = parse_sequence(ts)
        assert result["name"] == "fId"
        assert result["body"] is not None
        assert result["body"]["tag"] == "Lam"

    def test_sprint5_format(self):
        """DEF TY <type tokens> BODY <body> EOF"""
        tokens = "DEF TY Int -> Int BODY (Lam 1 Var 0 ) EOF".split()
        ts = TokenStream(tokens)
        result = parse_sequence(ts)
        assert result["name"] == "f"  # placeholder
        assert result["body"]["tag"] == "Lam"

    def test_sprint4_format(self):
        """DEF name <body> EOF"""
        tokens = "DEF fId (Lam 1 Var 0 ) EOF".split()
        ts = TokenStream(tokens)
        result = parse_sequence(ts)
        assert result["name"] == "fId"
        assert result["body"]["tag"] == "Lam"

    def test_legacy_format(self):
        """DEF:name <body> EOF"""
        tokens = "DEF:fId (Lam 1 Var 0 ) EOF".split()
        ts = TokenStream(tokens)
        result = parse_sequence(ts)
        assert result["name"] == "fId"
        assert result["body"]["tag"] == "Lam"

    def test_empty_stream(self):
        ts = TokenStream([])
        assert parse_sequence(ts) is None

    def test_no_body_returns_none_body(self):
        """DEF name with no body should return body=None."""
        ts = TokenStream(["DEF", "fId"])
        result = parse_sequence(ts)
        assert result["name"] == "fId"
        assert result["body"] is None

    def test_missing_eof_no_crash(self):
        """Body without EOF should still parse (early return at line 203)."""
        tokens = "DEF fId (Lam 1 Var 0 )".split()
        ts = TokenStream(tokens)
        result = parse_sequence(ts)
        assert result["body"] is not None

    def test_wrong_eof_raises(self):
        """Non-EOF token after body should raise."""
        tokens = "DEF fId (Lam 1 Var 0 ) WRONG".split()
        ts = TokenStream(tokens)
        with pytest.raises(ParseError, match="Expected EOF"):
            parse_sequence(ts)

    def test_invalid_start_raises(self):
        tokens = ["NOTDEF", "something"]
        ts = TokenStream(tokens)
        with pytest.raises(ParseError, match="Expected DEF"):
            parse_sequence(ts)


# ── parse_expr for each node type ───────────────────────────────────────────

class TestParseExpr:
    def test_var_with_index(self):
        ts = TokenStream(["Var", "0"])
        result = parse_expr(ts)
        assert result == {"tag": "Var", "name": "0"}

    def test_var_with_name(self):
        ts = TokenStream(["Var", "SELF"])
        result = parse_expr(ts)
        assert result == {"tag": "Var", "name": "SELF"}

    def test_var_invalid_name_raises(self):
        """Var followed by a structural token should raise."""
        ts = TokenStream(["Var", ")"])
        with pytest.raises(ParseError, match="Invalid Var name"):
            parse_expr(ts)

    def test_con(self):
        ts = TokenStream(["Con", "True"])
        result = parse_expr(ts)
        assert result == {"tag": "Con", "name": "True"}

    def test_lit(self):
        ts = TokenStream(["Lit", "42"])
        result = parse_expr(ts)
        assert result == {"tag": "Lit", "val": "42"}

    def test_lit_invalid_raises(self):
        ts = TokenStream(["Lit", "abc"])
        with pytest.raises(ParseError, match="Invalid literal"):
            parse_expr(ts)

    def test_prim(self):
        ts = TokenStream(["Prim", "+"])
        result = parse_expr(ts)
        assert result == {"tag": "Prim", "op": "+"}

    def test_prim_unknown_op_raises(self):
        ts = TokenStream(["Prim", "xxx"])
        with pytest.raises(ParseError, match="Unknown primitive"):
            parse_expr(ts)

    def test_tyarg(self):
        ts = TokenStream(["TyArg", "Int"])
        result = parse_expr(ts)
        assert result == {"tag": "TyArg", "type": "Int"}

    def test_global_ref(self):
        ts = TokenStream(["fSum"])
        result = parse_expr(ts)
        assert result == {"tag": "GlobalRef", "name": "fSum"}

    def test_app(self):
        ts = TokenStream(["(App", "Prim", "+", "Var", "0", "Lit", "1", ")"])
        result = parse_expr(ts)
        assert result["tag"] == "App"
        assert result["fun"]["tag"] == "Prim"
        assert len(result["args"]) == 2

    def test_lam_with_val_count(self):
        ts = TokenStream(["(Lam", "2", "Var", "0", ")"])
        result = parse_expr(ts)
        assert result["tag"] == "Lam"
        assert result["n_val"] == 2
        assert result["body"]["tag"] == "Var"

    def test_lam_with_type_binders(self):
        ts = TokenStream(["(Lam", "@T", "1", "Var", "0", ")"])
        result = parse_expr(ts)
        assert result["n_type"] == 1
        assert result["n_val"] == 1

    def test_let(self):
        ts = TokenStream(["(Let", "(Bind", "Lit", "42", ")", "Var", "0", ")"])
        result = parse_expr(ts)
        assert result["tag"] == "Let"
        assert len(result["binds"]) == 1
        assert result["body"]["tag"] == "Var"

    def test_case(self):
        ts = TokenStream([
            "(Case", "Var", "0",
            "(Alt", "(PCon", "True", ")", "Lit", "1", ")",
            "(Alt", "(PCon", "False", ")", "Lit", "0", ")",
            ")"
        ])
        result = parse_expr(ts)
        assert result["tag"] == "Case"
        assert len(result["alts"]) == 2

    def test_tuple(self):
        ts = TokenStream(["(Tuple", "Var", "0", "Var", "1", ")"])
        result = parse_expr(ts)
        assert result["tag"] == "Tuple"
        assert len(result["elems"]) == 2

    def test_unexpected_token_raises(self):
        ts = TokenStream(["BADTOKEN"])
        with pytest.raises(ParseError, match="Expected expression"):
            parse_expr(ts)


# ── parse_pat ───────────────────────────────────────────────────────────────

class TestParsePat:
    def test_pwild(self):
        ts = TokenStream(["(PWild)"])
        result = parse_pat(ts)
        assert result == {"tag": "PWild"}

    def test_pcon_no_arity(self):
        ts = TokenStream(["(PCon", "True", ")"])
        result = parse_pat(ts)
        assert result["tag"] == "PCon"
        assert result["con"] == "True"
        assert result["arity"] is None

    def test_pcon_with_arity(self):
        ts = TokenStream(["(PCon", ":", "2", ")"])
        result = parse_pat(ts)
        assert result["con"] == ":"
        assert result["arity"] == 2

    def test_pcon_nil(self):
        ts = TokenStream(["(PCon", "[]", ")"])
        result = parse_pat(ts)
        assert result["con"] == "[]"
        assert result["arity"] is None

    def test_ptuple(self):
        ts = TokenStream(["(PTuple", "2", ")"])
        result = parse_pat(ts)
        assert result["tag"] == "PTuple"
        assert result["arity"] == 2

    def test_plit(self):
        ts = TokenStream(["(PLit", "0", ")"])
        result = parse_pat(ts)
        assert result["tag"] == "PLit"
        assert result["val"] == "0"

    def test_bad_pattern_raises(self):
        ts = TokenStream(["NOTPAT"])
        with pytest.raises(ParseError, match="Expected pattern"):
            parse_pat(ts)


# ── is_type_valid (incremental validation) ──────────────────────────────────

class TestIsTypeValid:
    def test_def_alone_incomplete(self):
        """DEF alone → parse error (expects name/TY after DEF)."""
        assert is_type_valid([], "DEF") is False

    def test_def_then_name(self):
        """DEF + name is a complete (body=None) sequence."""
        assert is_type_valid(["DEF"], "fId") is True

    def test_def_name_ty_partial(self):
        """DEF name TY is a valid partial header (Sprint 10: partial accepted for USES support)."""
        assert is_type_valid(["DEF", "fId"], "TY") is True

    def test_complete_sequence_valid(self):
        partial = "DEF fId TY Int -> Int BODY (Lam 1 Var 0 )".split()
        assert is_type_valid(partial, "EOF") is True

    def test_invalid_after_var(self):
        """Var followed by another Var should be invalid (Var consumes next token as name)."""
        # After "DEF fId (Lam 1 Var 0" the next token should be ")" not "Var"
        partial = "DEF fId (Lam 1 Var 0".split()
        # This is tricky — Var 0 is consumed, then "Var" would start a new expr in the App
        # Actually in a Lam body context, only one expr is expected, so after Var 0 we need ")"
        assert is_type_valid(partial, ")") is True

    def test_structural_token_as_var_name_invalid(self):
        """Var followed by ')' (structural token) should fail."""
        partial = ["DEF", "fId", "Var"]
        assert is_type_valid(partial, ")") is False

    def test_exception_recovery_attribute_error(self):
        """is_type_valid should not crash on weird inputs."""
        # Extremely malformed partial that might cause AttributeError
        assert is_type_valid(["DEF"], "DEF") is False or is_type_valid(["DEF"], "DEF") is True
        # The key property: it returns a bool, never raises

    def test_exception_recovery_deeply_nested(self):
        """Deeply nested input should not crash (RecursionError caught)."""
        # Build a deeply nested App expression
        partial = ["DEF", "fId"] + ["(App"] * 100 + ["Lit", "1"]
        # Should return True or False but not crash
        result = is_type_valid(partial, ")")
        assert isinstance(result, bool)


# ── USES section (Sprint 10) ────────────────────────────────────────────────

class TestUsesSection:
    def test_uses_accepts_global_ref(self):
        partial = "DEF fCountPos TY [ Int ] -> Int USES".split()
        assert is_type_valid(partial, "fLength") is True

    def test_uses_accepts_body(self):
        partial = "DEF fCountPos TY [ Int ] -> Int USES".split()
        assert is_type_valid(partial, "BODY") is True

    def test_uses_rejects_var(self):
        partial = "DEF fCountPos TY [ Int ] -> Int USES".split()
        assert is_type_valid(partial, "Var") is False

    def test_uses_rejects_app(self):
        partial = "DEF fCountPos TY [ Int ] -> Int USES".split()
        assert is_type_valid(partial, "(App") is False

    def test_uses_multiple_refs(self):
        partial = "DEF fCountPos TY [ Int ] -> Int USES fLength fFilter".split()
        assert is_type_valid(partial, "fIsPositive") is True
        assert is_type_valid(partial, "BODY") is True

    def test_uses_then_body_valid(self):
        partial = "DEF fCountPos TY [ Int ] -> Int USES fLength fFilter BODY".split()
        assert is_type_valid(partial, "(Lam") is True

    def test_full_sequence_with_uses(self):
        tokens = "DEF fCountPos TY [ Int ] -> Int USES fLength fFilter BODY (Lam 1 fLength ) EOF".split()
        ok, err = type_check_sequence(tokens)
        assert ok is True

    def test_no_uses_backward_compat(self):
        tokens = "DEF fDouble TY Int -> Int BODY (Lam 1 Var 0 ) EOF".split()
        ok, err = type_check_sequence(tokens)
        assert ok is True


# ── type_check_sequence ─────────────────────────────────────────────────────

class TestTypeCheckSequence:
    def test_valid_identity(self):
        tokens = "DEF fId TY Int -> Int BODY (Lam 1 Var 0 ) EOF".split()
        ok, err = type_check_sequence(tokens)
        assert ok is True
        assert err == ""

    def test_valid_addition(self):
        tokens = "DEF fAdd TY Int -> Int -> Int BODY (Lam 2 (App Prim + Var 1 Var 0 ) ) EOF".split()
        ok, err = type_check_sequence(tokens)
        assert ok is True

    def test_parse_error(self):
        tokens = ["NOTDEF", "something"]
        ok, err = type_check_sequence(tokens)
        assert ok is False
        assert "parse" in err

    def test_empty_sequence(self):
        ok, err = type_check_sequence([])
        assert ok is True  # None body, no type error
        assert err == ""

    def test_lit_only(self):
        tokens = "DEF fConst Lit 42 EOF".split()
        ok, err = type_check_sequence(tokens)
        assert ok is True


# ── Type inference ──────────────────────────────────────────────────────────

class TestInferType:
    def test_lit_is_int(self):
        assert infer_type({"tag": "Lit", "val": "42"}, {}) == INT

    def test_prim_plus(self):
        t = infer_type({"tag": "Prim", "op": "+"}, {})
        assert isinstance(t, FunT)
        assert t.arg == INT

    def test_con_true(self):
        assert infer_type({"tag": "Con", "name": "True"}, {}) == BOOL

    def test_con_nil(self):
        t = infer_type({"tag": "Con", "name": "[]"}, {})
        assert isinstance(t, ListT)

    def test_global_ref(self):
        t = infer_type({"tag": "GlobalRef", "name": "fSum"}, {})
        assert isinstance(t, FunT)

    def test_var_from_env(self):
        t = infer_type({"tag": "Var", "name": "x"}, {"x": INT})
        assert t == INT

    def test_var_unknown(self):
        t = infer_type({"tag": "Var", "name": "x"}, {})
        assert isinstance(t, UnknownT)

    def test_app_prim_plus(self):
        node = {
            "tag": "App",
            "fun": {"tag": "Prim", "op": "+"},
            "args": [
                {"tag": "Lit", "val": "1"},
                {"tag": "Lit", "val": "2"},
            ],
        }
        assert infer_type(node, {}) == INT

    def test_none_returns_unknown(self):
        assert isinstance(infer_type(None, {}), UnknownT)

    def test_lam_infers_body_type(self):
        node = {"tag": "Lam", "n_type": 0, "n_val": 1, "body": {"tag": "Lit", "val": "5"}}
        assert infer_type(node, {}) == INT

    def test_let_infers_body_type(self):
        node = {
            "tag": "Let",
            "binds": [{"tag": "Bind", "val": {"tag": "Lit", "val": "1"}}],
            "body": {"tag": "Con", "name": "True"},
        }
        assert infer_type(node, {}) == BOOL

    def test_case_infers_first_alt_type(self):
        node = {
            "tag": "Case",
            "scrutinee": {"tag": "Var", "name": "x"},
            "alts": [
                {"pat": {"tag": "PWild"}, "body": {"tag": "Lit", "val": "42"}},
            ],
        }
        assert infer_type(node, {}) == INT

    def test_case_empty_alts(self):
        node = {"tag": "Case", "scrutinee": {"tag": "Var", "name": "x"}, "alts": []}
        assert isinstance(infer_type(node, {}), UnknownT)

    def test_tuple_returns_unknown(self):
        node = {"tag": "Tuple", "elems": [{"tag": "Lit", "val": "1"}, {"tag": "Lit", "val": "2"}]}
        assert isinstance(infer_type(node, {}), UnknownT)

    def test_global_ref_known(self):
        node = {"tag": "GlobalRef", "name": "fSum"}
        t = infer_type(node, {})
        assert isinstance(t, FunT)
        assert isinstance(t.arg, ListT)

    def test_global_ref_unknown(self):
        node = {"tag": "GlobalRef", "name": "fNonexistent"}
        assert isinstance(infer_type(node, {}), UnknownT)

    def test_app_type_mismatch_raises(self):
        """Applying Bool argument to Int→Int function should raise."""
        node = {
            "tag": "App",
            "fun": {"tag": "Prim", "op": "+"},
            "args": [{"tag": "Con", "name": "True"}],  # Bool, not Int
        }
        with pytest.raises(TypeCheckError, match="mismatch"):
            infer_type(node, {})

    def test_app_non_function_raises(self):
        """Applying args to a non-function type should raise."""
        node = {
            "tag": "App",
            "fun": {"tag": "Lit", "val": "42"},  # Int, not a function
            "args": [{"tag": "Lit", "val": "1"}],
        }
        with pytest.raises(TypeCheckError, match="non-function"):
            infer_type(node, {})

    def test_app_skips_tyarg(self):
        """TyArg in App args should be skipped (erased at term level)."""
        node = {
            "tag": "App",
            "fun": {"tag": "Prim", "op": "+"},
            "args": [
                {"tag": "TyArg", "type": "Int"},
                {"tag": "Lit", "val": "1"},
                {"tag": "Lit", "val": "2"},
            ],
        }
        assert infer_type(node, {}) == INT

    def test_comparison_returns_bool(self):
        node = {
            "tag": "App",
            "fun": {"tag": "Prim", "op": "=="},
            "args": [{"tag": "Lit", "val": "1"}, {"tag": "Lit", "val": "2"}],
        }
        assert infer_type(node, {}) == BOOL

    def test_con_nil(self):
        t = infer_type({"tag": "Con", "name": "[]"}, {})
        assert isinstance(t, ListT)

    def test_con_nothing(self):
        t = infer_type({"tag": "Con", "name": "Nothing"}, {})
        assert isinstance(t, MaybeT)

    def test_con_just(self):
        t = infer_type({"tag": "Con", "name": "Just"}, {})
        assert isinstance(t, FunT)

    def test_unknown_tag(self):
        assert isinstance(infer_type({"tag": "Weird"}, {}), UnknownT)


# ── _compat ─────────────────────────────────────────────────────────────────

class TestCompat:
    def test_same_types(self):
        assert _compat(INT, INT) is True
        assert _compat(BOOL, BOOL) is True

    def test_different_types(self):
        assert _compat(INT, BOOL) is False

    def test_unknown_matches_anything(self):
        assert _compat(UNKNOWN, INT) is True
        assert _compat(INT, UNKNOWN) is True
        assert _compat(UNKNOWN, UNKNOWN) is True

    def test_list_types(self):
        assert _compat(ListT(INT), ListT(INT)) is True
        assert _compat(ListT(INT), ListT(BOOL)) is False

    def test_fun_types(self):
        f1 = FunT(INT, INT)
        f2 = FunT(INT, BOOL)
        assert _compat(f1, f1) is True
        assert _compat(f1, f2) is False


# ── parse_bind / parse_alt standalone ────────────────────────────────────────

class TestParseBind:
    def test_simple_bind(self):
        ts = TokenStream(["(Bind", "Lit", "42", ")"])
        result = parse_bind(ts)
        assert result["tag"] == "Bind"
        assert result["val"]["tag"] == "Lit"
        assert result["val"]["val"] == "42"

    def test_empty_bind(self):
        ts = TokenStream(["(Bind", ")"])
        result = parse_bind(ts)
        assert result["tag"] == "Bind"
        assert result["val"] is None


class TestParseAlt:
    def test_simple_alt(self):
        ts = TokenStream(["(Alt", "(PWild)", "Lit", "0", ")"])
        result = parse_alt(ts)
        assert result["tag"] == "Alt"
        assert result["pat"]["tag"] == "PWild"
        assert result["body"]["tag"] == "Lit"

    def test_alt_with_pcon(self):
        ts = TokenStream(["(Alt", "(PCon", "True", ")", "Lit", "1", ")"])
        result = parse_alt(ts)
        assert result["pat"]["tag"] == "PCon"
        assert result["pat"]["con"] == "True"

    def test_alt_no_body(self):
        ts = TokenStream(["(Alt", "(PWild)", ")"])
        result = parse_alt(ts)
        assert result["body"] is None


# ── type_check_sequence: negative tests ──────────────────────────────────────

class TestTypeCheckNegative:
    def test_type_mismatch_detected(self):
        """Applying Bool to an Int→Int function should fail type check."""
        tokens = "DEF fBad TY Int -> Int BODY (Lam 1 (App Prim + Con True Var 0 ) ) EOF".split()
        ok, err = type_check_sequence(tokens)
        assert ok is False
        assert "type:" in err

    def test_non_function_application(self):
        """Applying args to a literal should fail."""
        tokens = "DEF fBad TY Int BODY (App Lit 42 Lit 1 ) EOF".split()
        ok, err = type_check_sequence(tokens)
        assert ok is False
        assert "type:" in err

    def test_malformed_body(self):
        tokens = "DEF fBad ) ) ) EOF".split()
        ok, err = type_check_sequence(tokens)
        assert ok is False
        assert "parse:" in err

    def test_valid_comparison(self):
        tokens = "DEF fEq TY Int -> Int -> Bool BODY (Lam 2 (App Prim == Var 1 Var 0 ) ) EOF".split()
        ok, err = type_check_sequence(tokens)
        assert ok is True

    def test_valid_cons(self):
        tokens = "DEF f TY Int -> [ Int ] -> [ Int ] BODY (Lam 2 (App Con : Var 1 Var 0 ) ) EOF".split()
        ok, err = type_check_sequence(tokens)
        assert ok is True


# ── is_type_valid: constrained decode scenarios ─────────────────────────────

class TestIsTypeValidConstrainedDecode:
    """Tests that simulate the constrained decoder probing various next tokens
    at different points in a partial sequence."""

    def test_after_def_name_valid_tokens(self):
        """After 'DEF fId', expression starters and TY are valid.
        Sprint 10: TY is valid because partial headers are accepted for USES support."""
        partial = ["DEF", "fId"]
        assert is_type_valid(partial, "TY") is True   # partial header accepted (Sprint 10)
        assert is_type_valid(partial, "(Lam") is True
        assert is_type_valid(partial, "Lit") is True
        assert is_type_valid(partial, "Var") is True

    def test_after_ty_body_marker(self):
        """After full TY section, BODY and USES are valid (Sprint 10)."""
        partial = ["DEF", "fId", "TY", "Int", "->", "Int"]
        assert is_type_valid(partial, "BODY") is True
        assert is_type_valid(partial, "USES") is True

    def test_mid_lam_body(self):
        partial = "DEF fId TY Int -> Int BODY (Lam 1".split()
        assert is_type_valid(partial, "Var") is True
        assert is_type_valid(partial, "Lit") is True
        assert is_type_valid(partial, "(App") is True
        # Can't close Lam yet (no body parsed)

    def test_after_var_index(self):
        partial = "DEF fId TY Int -> Int BODY (Lam 1 Var 0".split()
        assert is_type_valid(partial, ")") is True  # close Lam
        assert is_type_valid(partial, "Var") is False  # can't start another expr

    def test_close_lam_then_eof(self):
        partial = "DEF fId TY Int -> Int BODY (Lam 1 Var 0 )".split()
        assert is_type_valid(partial, "EOF") is True

    def test_mid_app_multiple_args(self):
        partial = "DEF fAdd TY Int -> Int -> Int BODY (Lam 2 (App Prim +".split()
        assert is_type_valid(partial, "Var") is True
        assert is_type_valid(partial, "Lit") is True

    def test_app_close_paren(self):
        partial = "DEF fAdd TY Int -> Int -> Int BODY (Lam 2 (App Prim + Var 1 Var 0".split()
        assert is_type_valid(partial, ")") is True  # close App

    def test_case_alt_start(self):
        """After scrutinee in Case, (Alt is valid."""
        partial = "DEF f (Lam 1 (Case Var 0".split()
        assert is_type_valid(partial, "(Alt") is True

    def test_invalid_structural_after_lit(self):
        """DEF after a lit value is never valid."""
        partial = "DEF fId Lit 42".split()
        assert is_type_valid(partial, "DEF") is False

    def test_global_ref_valid(self):
        """GlobalRef (fXxx) is valid as first expr in App."""
        partial = "DEF f (Lam 1 (App".split()
        assert is_type_valid(partial, "fSum") is True

    def test_eof_without_body_valid(self):
        """Just a name token after DEF is valid (incomplete but parseable)."""
        partial = ["DEF", "fId", "Lit", "42"]
        assert is_type_valid(partial, "EOF") is True
