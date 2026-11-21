"""Tests for clean.py — GHC Core AST → clean IR transformation.

Covers:
  - Substitution with shadowing (#3 from audit)
  - I# unboxing
  - Dict stripping
  - Type binder removal
  - Prim promotion
  - TyArg removal
  - __DEFAULT → PWild
  - Con cleaning (True, False, [], :, I#, C#)
  - Tuple cleaning
  - Nested cleaning
  - App edge cases (fun→None, all args dropped)
  - validate_no_ghc_noise for all noise types
  - clean_function / clean_corpus top-level
"""

import pytest
from clean import (
    substitute,
    _pat_vars,
    clean_expr,
    clean_pat,
    clean_binders,
    validate_no_ghc_noise,
    clean_function,
    clean_corpus,
    GHC_ARTIFACT_VARS,
)


# ── Substitution basics ─────────────────────────────────────────────────────

class TestSubstitute:
    def test_var_match(self):
        expr = {"tag": "Var", "name": "x"}
        repl = {"tag": "Lit", "sort": "Int", "val": 42}
        result = substitute(expr, "x", repl)
        assert result["tag"] == "Lit"
        assert result["val"] == 42

    def test_var_no_match(self):
        expr = {"tag": "Var", "name": "y"}
        repl = {"tag": "Lit", "sort": "Int", "val": 42}
        result = substitute(expr, "x", repl)
        assert result["tag"] == "Var"
        assert result["name"] == "y"

    def test_lit_unchanged(self):
        expr = {"tag": "Lit", "sort": "Int", "val": 5}
        result = substitute(expr, "x", {"tag": "Lit", "sort": "Int", "val": 0})
        assert result["val"] == 5

    def test_app_recurses(self):
        expr = {
            "tag": "App",
            "fun": {"tag": "Var", "name": "x"},
            "args": [{"tag": "Var", "name": "x"}],
        }
        repl = {"tag": "Lit", "sort": "Int", "val": 1}
        result = substitute(expr, "x", repl)
        assert result["fun"]["tag"] == "Lit"
        assert result["args"][0]["tag"] == "Lit"

    def test_none_input(self):
        assert substitute(None, "x", {"tag": "Lit", "sort": "Int", "val": 0}) is None

    def test_deep_copy_replacement(self):
        """Replacement should be deep-copied to avoid aliasing."""
        repl = {"tag": "Lit", "sort": "Int", "val": 1}
        expr = {
            "tag": "App",
            "fun": {"tag": "Var", "name": "x"},
            "args": [{"tag": "Var", "name": "x"}],
        }
        result = substitute(expr, "x", repl)
        # Modifying one replacement shouldn't affect the other
        result["fun"]["val"] = 999
        assert result["args"][0]["val"] == 1


# ── Substitution shadowing ───────────────────────────────────────────────────

class TestSubstituteShadowing:
    def test_lam_shadows_stops_body(self):
        """Lam that binds 'x' should NOT substitute x in body."""
        expr = {
            "tag": "Lam",
            "binders": [{"name": "x", "kind": "val"}],
            "body": {"tag": "Var", "name": "x"},
        }
        repl = {"tag": "Lit", "sort": "Int", "val": 99}
        result = substitute(expr, "x", repl)
        # Body should be unchanged — x is shadowed
        assert result["body"]["tag"] == "Var"
        assert result["body"]["name"] == "x"

    def test_lam_no_shadow_substitutes(self):
        """Lam that binds 'y' should still substitute 'x' in body."""
        expr = {
            "tag": "Lam",
            "binders": [{"name": "y", "kind": "val"}],
            "body": {"tag": "Var", "name": "x"},
        }
        repl = {"tag": "Lit", "sort": "Int", "val": 99}
        result = substitute(expr, "x", repl)
        assert result["body"]["tag"] == "Lit"

    def test_let_shadow_stops_body(self):
        """Let that binds 'x' should not substitute x in body."""
        expr = {
            "tag": "Let",
            "binds": [{"name": "x", "val": {"tag": "Lit", "sort": "Int", "val": 1}}],
            "body": {"tag": "Var", "name": "x"},
        }
        repl = {"tag": "Lit", "sort": "Int", "val": 99}
        result = substitute(expr, "x", repl)
        assert result["body"]["tag"] == "Var"

    def test_let_shadow_still_substitutes_rhs(self):
        """Let that binds 'x' should still substitute x in bind VALUES."""
        expr = {
            "tag": "Let",
            "binds": [{"name": "x", "val": {"tag": "Var", "name": "x"}}],
            "body": {"tag": "Var", "name": "x"},
        }
        repl = {"tag": "Lit", "sort": "Int", "val": 99}
        result = substitute(expr, "x", repl)
        # RHS should be substituted (x in val is the outer x)
        assert result["binds"][0]["val"]["tag"] == "Lit"
        # Body should NOT be substituted (x is now the let-bound x)
        assert result["body"]["tag"] == "Var"

    def test_case_pattern_shadows(self):
        """Pattern binding 'x' should stop substitution in that alt body."""
        expr = {
            "tag": "Case",
            "scrutinee": {"tag": "Var", "name": "x"},
            "alts": [{
                "pat": {"tag": "PCon", "con": "Just", "vars": ["x"]},
                "body": {"tag": "Var", "name": "x"},
            }],
        }
        repl = {"tag": "Lit", "sort": "Int", "val": 99}
        result = substitute(expr, "x", repl)
        # Scrutinee: x should be substituted
        assert result["scrutinee"]["tag"] == "Lit"
        # Alt body: x is shadowed by pattern → not substituted
        assert result["alts"][0]["body"]["tag"] == "Var"

    def test_case_no_shadow_substitutes(self):
        """Pattern not binding 'x' should substitute in alt body."""
        expr = {
            "tag": "Case",
            "scrutinee": {"tag": "Var", "name": "s"},
            "alts": [{
                "pat": {"tag": "PCon", "con": "Just", "vars": ["y"]},
                "body": {"tag": "Var", "name": "x"},
            }],
        }
        repl = {"tag": "Lit", "sort": "Int", "val": 99}
        result = substitute(expr, "x", repl)
        assert result["alts"][0]["body"]["tag"] == "Lit"

    def test_tuple_substitutes(self):
        expr = {
            "tag": "Tuple",
            "elems": [
                {"tag": "Var", "name": "x"},
                {"tag": "Var", "name": "y"},
            ],
        }
        repl = {"tag": "Lit", "sort": "Int", "val": 1}
        result = substitute(expr, "x", repl)
        assert result["elems"][0]["tag"] == "Lit"
        assert result["elems"][1]["tag"] == "Var"


# ── _pat_vars ────────────────────────────────────────────────────────────────

class TestPatVars:
    def test_pcon_vars(self):
        assert _pat_vars({"tag": "PCon", "con": ":", "vars": ["h", "t"]}) == {"h", "t"}

    def test_ptuple_vars(self):
        assert _pat_vars({"tag": "PTuple", "vars": ["a", "b"]}) == {"a", "b"}

    def test_pwild_no_vars(self):
        assert _pat_vars({"tag": "PWild"}) == set()

    def test_plit_no_vars(self):
        assert _pat_vars({"tag": "PLit", "val": 0}) == set()

    def test_none_pat(self):
        assert _pat_vars(None) == set()


# ── I# unboxing ─────────────────────────────────────────────────────────────

class TestIHashUnboxing:
    def test_i_hash_lit(self):
        """App(Con I#, [Lit 42]) → Lit 42."""
        node = {
            "tag": "App",
            "fun": {"tag": "Con", "name": "I#"},
            "args": [{"tag": "Lit", "sort": "Int#", "val": "42#"}],
        }
        result = clean_expr(node)
        assert result["tag"] == "Lit"
        assert result["val"] == 42

    def test_i_hash_with_tyarg(self):
        """App(Con I#, [TyArg, Lit 5]) → Lit 5 (TyArg skipped)."""
        node = {
            "tag": "App",
            "fun": {"tag": "Con", "name": "I#"},
            "args": [
                {"tag": "TyArg", "type": "Int#"},
                {"tag": "Lit", "sort": "Int#", "val": "5"},
            ],
        }
        result = clean_expr(node)
        assert result["tag"] == "Lit"
        assert result["val"] == 5

    def test_i_hash_no_args_fallback(self):
        """I# with no real args falls back to Lit 0."""
        node = {
            "tag": "App",
            "fun": {"tag": "Con", "name": "I#"},
            "args": [],
        }
        result = clean_expr(node)
        assert result["tag"] == "Lit"
        assert result["val"] == 0

    def test_standalone_i_hash_con_dropped(self):
        """Standalone Con I# (not in App) should be dropped."""
        node = {"tag": "Con", "name": "I#"}
        assert clean_expr(node) is None


# ── Dict stripping ───────────────────────────────────────────────────────────

class TestDictStripping:
    def test_dollar_f_var_dropped(self):
        node = {"tag": "Var", "name": "$fNumInt"}
        assert clean_expr(node) is None

    def test_prim_with_dict_args(self):
        """Prim op with TyArg + $f dict should strip both."""
        node = {
            "tag": "App",
            "fun": {"tag": "Var", "name": "+"},
            "args": [
                {"tag": "TyArg", "type": "Int"},
                {"tag": "Var", "name": "$fNumInt"},
                {"tag": "Lit", "sort": "Int", "val": 1},
                {"tag": "Lit", "sort": "Int", "val": 2},
            ],
        }
        result = clean_expr(node)
        assert result["tag"] == "App"
        assert result["fun"]["tag"] == "Prim"
        assert result["fun"]["op"] == "+"
        assert len(result["args"]) == 2
        assert result["args"][0]["val"] == 1
        assert result["args"][1]["val"] == 2


# ── TyArg removal ───────────────────────────────────────────────────────────

class TestTyArgRemoval:
    def test_tyarg_dropped(self):
        assert clean_expr({"tag": "TyArg", "type": "Int"}) is None

    def test_app_tyarg_args_dropped(self):
        """General App: TyArg args should be removed."""
        node = {
            "tag": "App",
            "fun": {"tag": "Var", "name": "myFunc"},
            "args": [
                {"tag": "TyArg", "type": "Int"},
                {"tag": "Var", "name": "x"},
            ],
        }
        result = clean_expr(node)
        assert len(result["args"]) == 1
        assert result["args"][0]["name"] == "x"


# ── Prim promotion ──────────────────────────────────────────────────────────

class TestPrimPromotion:
    def test_logic_op_promoted(self):
        for op in ("&&", "||", "not"):
            result = clean_expr({"tag": "Var", "name": op})
            assert result["tag"] == "Prim"
            assert result["op"] == op

    def test_arith_op_promoted(self):
        for op in ("+", "-", "*", "div", "mod"):
            result = clean_expr({"tag": "Var", "name": op})
            assert result["tag"] == "Prim"
            assert result["op"] == op

    def test_comparison_op_promoted(self):
        for op in ("==", "/=", "<", "<=", ">", ">="):
            result = clean_expr({"tag": "Var", "name": op})
            assert result["tag"] == "Prim"
            assert result["op"] == op

    def test_regular_var_not_promoted(self):
        result = clean_expr({"tag": "Var", "name": "myFunc"})
        assert result["tag"] == "Var"


# ── Type binder stripping ───────────────────────────────────────────────────

class TestTypeBinders:
    def test_val_binders_kept(self):
        binders = [{"name": "x", "kind": "val"}, {"name": "y", "kind": "val"}]
        result = clean_binders(binders)
        assert len(result) == 2

    def test_type_binders_stripped(self):
        binders = [
            {"name": "@a", "kind": "type"},
            {"name": "x", "kind": "val"},
        ]
        result = clean_binders(binders)
        assert len(result) == 1
        assert result[0]["name"] == "x"

    def test_all_type_binders_returns_empty(self):
        binders = [{"name": "@a", "kind": "type"}, {"name": "@b", "kind": "type"}]
        result = clean_binders(binders)
        assert result == []

    def test_lam_all_type_binders_returns_body(self):
        """Lam with only type binders should return the body directly."""
        node = {
            "tag": "Lam",
            "binders": [{"name": "@a", "kind": "type"}],
            "body": {"tag": "Lit", "sort": "Int", "val": 42},
        }
        result = clean_expr(node)
        assert result["tag"] == "Lit"
        assert result["val"] == 42


# ── Pattern cleaning ─────────────────────────────────────────────────────────

class TestCleanPat:
    def test_default_becomes_pwild(self):
        result = clean_pat({"tag": "PCon", "con": "__DEFAULT", "vars": []})
        assert result["tag"] == "PWild"

    def test_i_hash_becomes_pwild(self):
        result = clean_pat({"tag": "PCon", "con": "I#", "vars": ["n"]})
        assert result["tag"] == "PWild"

    def test_normal_pcon_preserved(self):
        result = clean_pat({"tag": "PCon", "con": ":", "vars": ["h", "t"]})
        assert result["tag"] == "PCon"
        assert result["con"] == ":"
        assert result["vars"] == ["h", "t"]

    def test_plit_val_cleaned(self):
        result = clean_pat({"tag": "PLit", "val": "42#"})
        assert result["val"] == 42

    def test_none_becomes_pwild(self):
        result = clean_pat(None)
        assert result["tag"] == "PWild"


# ── Lit normalization ────────────────────────────────────────────────────────

class TestLitNormalization:
    def test_int_hash_sort(self):
        result = clean_expr({"tag": "Lit", "sort": "Int#", "val": "7#"})
        assert result["sort"] == "Int"
        assert result["val"] == 7

    def test_string_val_to_int(self):
        result = clean_expr({"tag": "Lit", "sort": "Int", "val": "42"})
        assert result["val"] == 42
        assert isinstance(result["val"], int)

    def test_negative_lit(self):
        result = clean_expr({"tag": "Lit", "sort": "Int", "val": "-3"})
        assert result["val"] == -3


# ── I# case substitution ────────────────────────────────────────────────────

class TestIHashCase:
    def test_single_alt_i_hash_substituted(self):
        """case x of I# n -> n + 1 should become x + 1."""
        node = {
            "tag": "Case",
            "scrutinee": {"tag": "Var", "name": "x"},
            "alts": [{
                "pat": {"tag": "PCon", "con": "I#", "vars": ["n"]},
                "body": {
                    "tag": "App",
                    "fun": {"tag": "Var", "name": "+"},
                    "args": [
                        {"tag": "Var", "name": "n"},
                        {"tag": "Lit", "sort": "Int", "val": 1},
                    ],
                },
            }],
        }
        result = clean_expr(node)
        # Should substitute n → x (the scrutinee) and remove the case
        assert result["tag"] == "App"
        assert result["fun"]["tag"] == "Prim"
        assert result["fun"]["op"] == "+"
        # First arg should be x (substituted from n)
        assert result["args"][0]["tag"] == "Var"
        assert result["args"][0]["name"] == "x"

    def test_as_var_substituted(self):
        """case x of wild { I# n -> wild } should substitute both wild and n."""
        node = {
            "tag": "Case",
            "scrutinee": {"tag": "Var", "name": "x"},
            "as": "wild",
            "alts": [{
                "pat": {"tag": "PCon", "con": "I#", "vars": ["n"]},
                "body": {"tag": "Var", "name": "wild"},
            }],
        }
        result = clean_expr(node)
        assert result["tag"] == "Var"
        assert result["name"] == "x"


# ── Validation ───────────────────────────────────────────────────────────────

class TestValidation:
    def test_clean_entry_passes(self):
        entries = [{
            "name": "fId",
            "body": {"tag": "Lam", "binders": [{"name": "x", "kind": "val"}],
                     "body": {"tag": "Var", "name": "x"}},
        }]
        violations = validate_no_ghc_noise(entries)
        assert violations == []

    def test_leftover_i_hash_detected(self):
        entries = [{
            "name": "bad",
            "body": {"tag": "Con", "name": "I#"},
        }]
        violations = validate_no_ghc_noise(entries)
        assert any("I#" in v for v in violations)

    def test_leftover_c_hash_detected(self):
        entries = [{"name": "bad", "body": {"tag": "Con", "name": "C#"}}]
        violations = validate_no_ghc_noise(entries)
        assert any("C#" in v for v in violations)

    def test_leftover_dollar_f_detected(self):
        entries = [{"name": "bad", "body": {"tag": "Var", "name": "$fNumInt"}}]
        violations = validate_no_ghc_noise(entries)
        assert any("$f" in v for v in violations)

    def test_leftover_tyarg_detected(self):
        entries = [{"name": "bad", "body": {"tag": "TyArg", "type": "Int"}}]
        violations = validate_no_ghc_noise(entries)
        assert any("TyArg" in v for v in violations)

    def test_leftover_default_detected(self):
        entries = [{"name": "bad", "body": {"tag": "PCon", "con": "__DEFAULT"}}]
        violations = validate_no_ghc_noise(entries)
        assert any("__DEFAULT" in v for v in violations)

    def test_leftover_int_hash_sort_detected(self):
        entries = [{"name": "bad", "body": {"tag": "Lit", "sort": "Int#", "val": 1}}]
        violations = validate_no_ghc_noise(entries)
        assert any("Int#" in v for v in violations)

    def test_wild_var_detected(self):
        entries = [{"name": "bad", "body": {"tag": "Var", "name": "wild"}}]
        violations = validate_no_ghc_noise(entries)
        assert any("wild" in v for v in violations)

    def test_wild1_var_detected(self):
        entries = [{"name": "bad", "body": {"tag": "Var", "name": "wild1"}}]
        violations = validate_no_ghc_noise(entries)
        assert any("wild1" in v for v in violations)


# ── Con cleaning ────────────────────────────────────────────────────────────

class TestConCleaning:
    def test_true_preserved(self):
        result = clean_expr({"tag": "Con", "name": "True"})
        assert result["tag"] == "Con"
        assert result["name"] == "True"

    def test_false_preserved(self):
        result = clean_expr({"tag": "Con", "name": "False"})
        assert result["tag"] == "Con"
        assert result["name"] == "False"

    def test_nil_preserved(self):
        result = clean_expr({"tag": "Con", "name": "[]"})
        assert result["tag"] == "Con"
        assert result["name"] == "[]"

    def test_cons_preserved(self):
        result = clean_expr({"tag": "Con", "name": ":"})
        assert result["tag"] == "Con"
        assert result["name"] == ":"

    def test_just_preserved(self):
        result = clean_expr({"tag": "Con", "name": "Just"})
        assert result["tag"] == "Con"
        assert result["name"] == "Just"

    def test_c_hash_dropped(self):
        result = clean_expr({"tag": "Con", "name": "C#"})
        assert result is None


# ── Tuple cleaning ──────────────────────────────────────────────────────────

class TestTupleCleaning:
    def test_tuple_elems_cleaned(self):
        node = {
            "tag": "Tuple",
            "elems": [
                {"tag": "Var", "name": "x"},
                {"tag": "Lit", "sort": "Int", "val": 1},
            ],
        }
        result = clean_expr(node)
        assert result["tag"] == "Tuple"
        assert len(result["elems"]) == 2

    def test_tuple_none_elems_dropped(self):
        """TyArg elems should be dropped from tuple."""
        node = {
            "tag": "Tuple",
            "elems": [
                {"tag": "TyArg", "type": "Int"},
                {"tag": "Lit", "sort": "Int", "val": 1},
            ],
        }
        result = clean_expr(node)
        assert result["tag"] == "Tuple"
        assert len(result["elems"]) == 1

    def test_tuple_all_none_returns_none(self):
        node = {
            "tag": "Tuple",
            "elems": [{"tag": "TyArg", "type": "Int"}],
        }
        assert clean_expr(node) is None


# ── Nested cleaning ─────────────────────────────────────────────────────────

class TestNestedCleaning:
    def test_lam_body_with_i_hash_unboxing(self):
        """Lam body contains I# unboxing → should clean through."""
        node = {
            "tag": "Lam",
            "binders": [{"name": "x", "kind": "val"}],
            "body": {
                "tag": "App",
                "fun": {"tag": "Con", "name": "I#"},
                "args": [{"tag": "Lit", "sort": "Int#", "val": "7#"}],
            },
        }
        result = clean_expr(node)
        assert result["tag"] == "Lam"
        assert result["body"]["tag"] == "Lit"
        assert result["body"]["val"] == 7

    def test_case_multiple_alts_mixed_patterns(self):
        node = {
            "tag": "Case",
            "scrutinee": {"tag": "Var", "name": "xs"},
            "alts": [
                {
                    "pat": {"tag": "PCon", "con": ":", "vars": ["h", "t"]},
                    "body": {"tag": "Var", "name": "h"},
                },
                {
                    "pat": {"tag": "PCon", "con": "[]", "vars": []},
                    "body": {"tag": "Lit", "sort": "Int", "val": 0},
                },
                {
                    "pat": {"tag": "PCon", "con": "__DEFAULT", "vars": []},
                    "body": {"tag": "Lit", "sort": "Int", "val": -1},
                },
            ],
        }
        result = clean_expr(node)
        assert result["tag"] == "Case"
        assert len(result["alts"]) == 3
        # __DEFAULT should become PWild
        assert result["alts"][2]["pat"]["tag"] == "PWild"

    def test_lam_mixed_binders_body_also_cleaned(self):
        """Lam with type+val binders and body needing cleaning."""
        node = {
            "tag": "Lam",
            "binders": [
                {"name": "@a", "kind": "type"},
                {"name": "x", "kind": "val"},
            ],
            "body": {
                "tag": "App",
                "fun": {"tag": "Var", "name": "+"},
                "args": [
                    {"tag": "TyArg", "type": "Int"},
                    {"tag": "Var", "name": "$fNumInt"},
                    {"tag": "Var", "name": "x"},
                    {"tag": "Lit", "sort": "Int", "val": 1},
                ],
            },
        }
        result = clean_expr(node)
        assert result["tag"] == "Lam"
        assert len(result["binders"]) == 1  # type binder stripped
        assert result["body"]["fun"]["tag"] == "Prim"
        assert result["body"]["fun"]["op"] == "+"
        assert len(result["body"]["args"]) == 2  # TyArg and $f stripped


# ── App edge cases ──────────────────────────────────────────────────────────

class TestAppEdgeCases:
    def test_app_fun_cleans_to_none(self):
        """App where fun is TyArg (cleans to None) → returns None."""
        node = {
            "tag": "App",
            "fun": {"tag": "TyArg", "type": "Int"},
            "args": [{"tag": "Lit", "sort": "Int", "val": 1}],
        }
        assert clean_expr(node) is None

    def test_app_all_args_dropped(self):
        """All args are TyArg/dict → app collapses to just fun."""
        node = {
            "tag": "App",
            "fun": {"tag": "Var", "name": "myFunc"},
            "args": [
                {"tag": "TyArg", "type": "Int"},
                {"tag": "Var", "name": "$fNumInt"},
            ],
        }
        result = clean_expr(node)
        # No real args left → trivial app returns just fun
        assert result["tag"] == "Var"
        assert result["name"] == "myFunc"

    def test_prim_with_dict_no_operands(self):
        """Prim with only TyArg+dict args → returns bare Prim."""
        node = {
            "tag": "App",
            "fun": {"tag": "Var", "name": "+"},
            "args": [
                {"tag": "TyArg", "type": "Int"},
                {"tag": "Var", "name": "$fNumInt"},
            ],
        }
        result = clean_expr(node)
        assert result["tag"] == "Prim"
        assert result["op"] == "+"


# ── GHC artifact vars ──────────────────────────────────────────────────────

class TestGHCArtifactVars:
    def test_letrec_dropped(self):
        assert clean_expr({"tag": "Var", "name": "letrec"}) is None

    def test_cast_dropped(self):
        assert clean_expr({"tag": "Var", "name": "cast"}) is None

    def test_all_artifact_vars_dropped(self):
        for name in GHC_ARTIFACT_VARS:
            assert clean_expr({"tag": "Var", "name": name}) is None


# ── Let cleaning ────────────────────────────────────────────────────────────

class TestLetCleaning:
    def test_let_bind_vals_cleaned(self):
        node = {
            "tag": "Let",
            "binds": [
                {"name": "a", "val": {"tag": "Lit", "sort": "Int#", "val": "5#"}},
            ],
            "body": {"tag": "Var", "name": "a"},
        }
        result = clean_expr(node)
        assert result["tag"] == "Let"
        assert result["binds"][0]["val"]["sort"] == "Int"
        assert result["binds"][0]["val"]["val"] == 5

    def test_let_all_binds_none(self):
        """If all bind vals clean to None, return just the body."""
        node = {
            "tag": "Let",
            "binds": [{"name": "a", "val": {"tag": "TyArg", "type": "Int"}}],
            "body": {"tag": "Lit", "sort": "Int", "val": 42},
        }
        result = clean_expr(node)
        assert result["tag"] == "Lit"
        assert result["val"] == 42

    def test_let_body_none(self):
        node = {
            "tag": "Let",
            "binds": [{"name": "a", "val": {"tag": "Lit", "sort": "Int", "val": 1}}],
            "body": {"tag": "TyArg", "type": "Int"},
        }
        assert clean_expr(node) is None


# ── Case with as-var (non-I#) ──────────────────────────────────────────────

class TestCaseAsVar:
    def test_as_var_substituted_in_general_case(self):
        """as-var in multi-alt case should be substituted with scrutinee."""
        node = {
            "tag": "Case",
            "scrutinee": {"tag": "Var", "name": "x"},
            "as": "wild",
            "alts": [
                {
                    "pat": {"tag": "PCon", "con": "True", "vars": []},
                    "body": {"tag": "Lit", "sort": "Int", "val": 1},
                },
                {
                    "pat": {"tag": "PCon", "con": "__DEFAULT", "vars": []},
                    "body": {"tag": "Var", "name": "wild"},
                },
            ],
        }
        result = clean_expr(node)
        assert result["tag"] == "Case"
        # In the default alt body, wild should be substituted with x
        default_body = result["alts"][1]["body"]
        assert default_body["tag"] == "Var"
        assert default_body["name"] == "x"


# ── clean_function / clean_corpus ───────────────────────────────────────────

class TestCleanFunction:
    def test_ok_false_returns_none(self):
        entry = {"name": "f", "ok": False, "body": {"tag": "Lit", "sort": "Int", "val": 1}}
        assert clean_function(entry) is None

    def test_valid_entry(self):
        entry = {
            "name": "fId",
            "type": "Int -> Int",
            "ok": True,
            "body": {
                "tag": "Lam",
                "binders": [{"name": "x", "kind": "val"}],
                "body": {"tag": "Var", "name": "x"},
            },
        }
        result = clean_function(entry, package="test")
        assert result["name"] == "fId"
        assert result["ok"] is True
        assert result["package"] == "test"

    def test_body_cleans_to_none(self):
        entry = {
            "name": "bad",
            "ok": True,
            "body": {"tag": "TyArg", "type": "Int"},
        }
        assert clean_function(entry) is None


class TestCleanCorpus:
    def test_filters_bad_entries(self):
        entries = [
            {"name": "good", "ok": True, "body": {"tag": "Lit", "sort": "Int", "val": 1}},
            {"name": "bad", "ok": False, "body": {"tag": "Lit", "sort": "Int", "val": 2}},
            {"name": "ugly", "ok": True, "body": {"tag": "TyArg", "type": "Int"}},
        ]
        result = clean_corpus(entries, package="test")
        assert len(result) == 1
        assert result[0]["name"] == "good"


# ── None / unknown tag ──────────────────────────────────────────────────────

class TestCleanExprEdgeCases:
    def test_none_input(self):
        assert clean_expr(None) is None

    def test_unknown_tag(self):
        assert clean_expr({"tag": "SomethingWeird"}) is None

    def test_lam_body_none(self):
        node = {
            "tag": "Lam",
            "binders": [{"name": "x", "kind": "val"}],
            "body": {"tag": "TyArg", "type": "Int"},
        }
        assert clean_expr(node) is None
