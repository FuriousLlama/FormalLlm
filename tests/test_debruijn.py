"""Tests for de Bruijn renaming in normalize.py.

Covers:
  - DeBruijnRenamer stack operations
  - Index computation (innermost = 0)
  - SELF token for recursive functions
  - Free variable detection
  - convert_expr correctness
  - convert_pat correctness
  - debruijn_convert (full pipeline)
  - Lambda lifting with known_names
  - Serialization round-trip
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "sprint" / "corpus"))
from normalize import (
    DeBruijnRenamer,
    collect_free_vars,
    convert_expr,
    convert_pat,
    debruijn_convert,
    serialize_expr,
    serialize_pat,
    normalize_type,
    to_sequence,
)


# ── DeBruijnRenamer ──────────────────────────────────────────────────────────

class TestRenamer:
    def test_single_binder(self):
        r = DeBruijnRenamer()
        r.push("x")
        assert r.lookup("x") == "0"

    def test_innermost_is_zero(self):
        r = DeBruijnRenamer()
        r.push("x")  # outermost
        r.push("y")  # innermost
        assert r.lookup("y") == "0"
        assert r.lookup("x") == "1"

    def test_three_deep(self):
        r = DeBruijnRenamer()
        r.push("a")
        r.push("b")
        r.push("c")
        assert r.lookup("c") == "0"
        assert r.lookup("b") == "1"
        assert r.lookup("a") == "2"

    def test_free_variable(self):
        r = DeBruijnRenamer()
        r.push("x")
        assert r.lookup("free") == "free"

    def test_self_name(self):
        r = DeBruijnRenamer()
        r._self_name = "fFact"
        r.push("n")
        assert r.lookup("fFact") == "SELF"
        assert r.lookup("n") == "0"

    def test_pop_single(self):
        r = DeBruijnRenamer()
        r.push("x")
        r.push("y")
        r.pop(1)
        assert r.lookup("x") == "0"
        assert r.lookup("y") == "y"  # free after pop

    def test_pop_multiple(self):
        r = DeBruijnRenamer()
        r.push("a")
        r.push("b")
        r.push("c")
        r.pop(2)
        assert r.lookup("a") == "0"
        assert r.lookup("b") == "b"  # free
        assert r.lookup("c") == "c"  # free

    def test_shadowing(self):
        """Same name at different depths: innermost wins."""
        r = DeBruijnRenamer()
        r.push("x")  # outer
        r.push("x")  # inner (shadows)
        assert r.lookup("x") == "0"  # innermost
        r.pop(1)
        assert r.lookup("x") == "0"  # back to outer

    def test_empty_pop(self):
        r = DeBruijnRenamer()
        r.pop(0)  # should not crash


# ── Free variable collection ─────────────────────────────────────────────────

class TestFreeVars:
    def test_simple_var(self):
        node = {"tag": "Var", "name": "x"}
        assert collect_free_vars(node, set()) == {"x"}

    def test_bound_var(self):
        node = {"tag": "Var", "name": "x"}
        assert collect_free_vars(node, {"x"}) == set()

    def test_lam_binds_var(self):
        node = {
            "tag": "Lam",
            "binders": [{"name": "x", "kind": "val"}],
            "body": {"tag": "Var", "name": "x"},
        }
        assert collect_free_vars(node, set()) == set()

    def test_lam_free_var(self):
        node = {
            "tag": "Lam",
            "binders": [{"name": "x", "kind": "val"}],
            "body": {"tag": "Var", "name": "y"},
        }
        assert collect_free_vars(node, set()) == {"y"}

    def test_type_binder_not_bound(self):
        """Type binders should NOT contribute to variable scope."""
        node = {
            "tag": "Lam",
            "binders": [{"name": "@a", "kind": "type"}, {"name": "x", "kind": "val"}],
            "body": {"tag": "Var", "name": "@a"},
        }
        fv = collect_free_vars(node, set())
        assert "@a" in fv  # type binder is not a value binding

    def test_let_mutual_recursion(self):
        """Let binds are all in scope for all RHS values."""
        node = {
            "tag": "Let",
            "binds": [
                {"name": "a", "val": {"tag": "Var", "name": "b"}},
                {"name": "b", "val": {"tag": "Var", "name": "a"}},
            ],
            "body": {"tag": "Var", "name": "a"},
        }
        assert collect_free_vars(node, set()) == set()

    def test_case_pattern_binds(self):
        node = {
            "tag": "Case",
            "scrutinee": {"tag": "Var", "name": "xs"},
            "alts": [{
                "pat": {"tag": "PCon", "con": ":", "vars": ["h", "t"]},
                "body": {"tag": "Var", "name": "h"},
            }],
        }
        fv = collect_free_vars(node, set())
        assert "xs" in fv
        assert "h" not in fv  # bound by pattern


# ── convert_expr ─────────────────────────────────────────────────────────────

class TestConvertExpr:
    def test_identity_lambda(self):
        """\\x -> x should become Lam with Var 0."""
        node = {
            "tag": "Lam",
            "binders": [{"name": "x", "kind": "val"}],
            "body": {"tag": "Var", "name": "x"},
        }
        r = DeBruijnRenamer()
        result = convert_expr(node, r)
        assert result["body"]["name"] == "0"

    def test_two_arg_lambda(self):
        """\\x y -> x should use index 1 for outer param."""
        node = {
            "tag": "Lam",
            "binders": [
                {"name": "x", "kind": "val"},
                {"name": "y", "kind": "val"},
            ],
            "body": {"tag": "Var", "name": "x"},
        }
        r = DeBruijnRenamer()
        result = convert_expr(node, r)
        # x is outermost (pushed first), y is innermost
        # From body's perspective: y=0, x=1
        assert result["body"]["name"] == "1"

    def test_two_arg_inner_var(self):
        """\\x y -> y should use index 0."""
        node = {
            "tag": "Lam",
            "binders": [
                {"name": "x", "kind": "val"},
                {"name": "y", "kind": "val"},
            ],
            "body": {"tag": "Var", "name": "y"},
        }
        r = DeBruijnRenamer()
        result = convert_expr(node, r)
        assert result["body"]["name"] == "0"

    def test_nested_lambda(self):
        """\\x -> \\y -> x should give x index 1 in inner body."""
        node = {
            "tag": "Lam",
            "binders": [{"name": "x", "kind": "val"}],
            "body": {
                "tag": "Lam",
                "binders": [{"name": "y", "kind": "val"}],
                "body": {"tag": "Var", "name": "x"},
            },
        }
        r = DeBruijnRenamer()
        result = convert_expr(node, r)
        inner_body = result["body"]["body"]
        assert inner_body["name"] == "1"

    def test_global_ref(self):
        """Known corpus function names become GlobalRef nodes."""
        node = {
            "tag": "App",
            "fun": {"tag": "Var", "name": "fSum"},
            "args": [{"tag": "Var", "name": "xs"}],
        }
        r = DeBruijnRenamer()
        r.push("xs")
        result = convert_expr(node, r, known_names={"fSum"})
        assert result["fun"]["tag"] == "GlobalRef"
        assert result["fun"]["name"] == "fSum"
        assert result["args"][0]["name"] == "0"  # xs is bound

    def test_case_cons_pattern_indices(self):
        """Case with cons pattern: head pushed first, tail pushed second.

        Pattern (: h t) → push h, push t → in body: t=0, h=1.
        This is the critical test for cons pattern ordering.
        """
        node = {
            "tag": "Case",
            "scrutinee": {"tag": "Var", "name": "xs"},
            "alts": [{
                "pat": {"tag": "PCon", "con": ":", "vars": ["h", "t"]},
                "body": {"tag": "Var", "name": "h"},  # accessing head
            }],
        }
        r = DeBruijnRenamer()
        r.push("xs")
        result = convert_expr(node, r)
        # h is pushed first (index 1), t is pushed second (index 0)
        # So accessing h in the body should give index 1
        body = result["alts"][0]["body"]
        assert body["name"] == "1", f"Head should be index 1, got {body['name']}"

    def test_case_cons_tail_index(self):
        """Accessing tail (last pushed) should be index 0."""
        node = {
            "tag": "Case",
            "scrutinee": {"tag": "Var", "name": "xs"},
            "alts": [{
                "pat": {"tag": "PCon", "con": ":", "vars": ["h", "t"]},
                "body": {"tag": "Var", "name": "t"},
            }],
        }
        r = DeBruijnRenamer()
        r.push("xs")
        result = convert_expr(node, r)
        body = result["alts"][0]["body"]
        assert body["name"] == "0", f"Tail should be index 0, got {body['name']}"

    def test_let_binding_indices(self):
        """Let bindings: all pushed simultaneously, last is innermost."""
        node = {
            "tag": "Let",
            "binds": [
                {"name": "a", "val": {"tag": "Lit", "sort": "Int", "val": 1}},
                {"name": "b", "val": {"tag": "Lit", "sort": "Int", "val": 2}},
            ],
            "body": {"tag": "Var", "name": "a"},
        }
        r = DeBruijnRenamer()
        result = convert_expr(node, r)
        # a pushed first (index 1), b pushed second (index 0)
        assert result["body"]["name"] == "1"

    def test_renamer_cleaned_after_lam(self):
        """Renamer stack should be restored after processing a Lam."""
        r = DeBruijnRenamer()
        r.push("outer")
        node = {
            "tag": "Lam",
            "binders": [{"name": "inner", "kind": "val"}],
            "body": {"tag": "Var", "name": "inner"},
        }
        convert_expr(node, r)
        # After converting the Lam, "inner" should be popped
        assert r.lookup("outer") == "0"
        assert r.lookup("inner") == "inner"  # free again


# ── Serialization ────────────────────────────────────────────────────────────

class TestSerialization:
    def test_var(self):
        tokens = []
        serialize_expr({"tag": "Var", "name": "0"}, tokens)
        assert tokens == ["Var", "0"]

    def test_global_ref(self):
        tokens = []
        serialize_expr({"tag": "GlobalRef", "name": "fSum"}, tokens)
        assert tokens == ["fSum"]

    def test_lit(self):
        tokens = []
        serialize_expr({"tag": "Lit", "sort": "Int", "val": 42}, tokens)
        assert tokens == ["Lit", "42"]

    def test_prim(self):
        tokens = []
        serialize_expr({"tag": "Prim", "op": "+"}, tokens)
        assert tokens == ["Prim", "+"]

    def test_app(self):
        tokens = []
        node = {
            "tag": "App",
            "fun": {"tag": "Prim", "op": "+"},
            "args": [
                {"tag": "Var", "name": "0"},
                {"tag": "Lit", "sort": "Int", "val": 1},
            ],
        }
        serialize_expr(node, tokens)
        assert tokens == ["(App", "Prim", "+", "Var", "0", "Lit", "1", ")"]

    def test_lam_with_type_binder(self):
        tokens = []
        node = {
            "tag": "Lam",
            "binders": [{"kind": "type"}, {"kind": "val"}],
            "body": {"tag": "Var", "name": "0"},
        }
        serialize_expr(node, tokens)
        assert tokens == ["(Lam", "@T", "1", "Var", "0", ")"]

    def test_lam_no_val_binders(self):
        """Type-only lambda should emit @T but no binder count."""
        tokens = []
        node = {
            "tag": "Lam",
            "binders": [{"kind": "type"}],
            "body": {"tag": "Var", "name": "free"},
        }
        serialize_expr(node, tokens)
        assert tokens == ["(Lam", "@T", "Var", "free", ")"]

    def test_case_with_cons_pattern(self):
        tokens = []
        node = {
            "tag": "Case",
            "scrutinee": {"tag": "Var", "name": "0"},
            "alts": [{
                "pat": {"tag": "PCon", "con": ":", "arity": 2},
                "body": {"tag": "Var", "name": "1"},
            }],
        }
        serialize_expr(node, tokens)
        assert tokens == [
            "(Case", "Var", "0",
            "(Alt", "(PCon", ":", "2", ")", "Var", "1", ")",
            ")"
        ]

    def test_pwild_pattern(self):
        tokens = []
        serialize_pat({"tag": "PWild"}, tokens)
        assert tokens == ["(PWild)"]

    def test_pcon_zero_arity(self):
        """PCon with arity 0 should NOT emit the arity number."""
        tokens = []
        serialize_pat({"tag": "PCon", "con": "[]", "arity": 0}, tokens)
        assert tokens == ["(PCon", "[]", ")"]


# ── Type normalization ───────────────────────────────────────────────────────

class TestTypeNormalization:
    def test_simple_type(self):
        assert normalize_type("Int -> Int") == ["Int", "->", "Int"]

    def test_forall_stripped(self):
        assert normalize_type("forall a. a -> a") == ["a", "->", "a"]

    def test_constraint_stripped(self):
        result = normalize_type("Eq a => a -> a -> Bool")
        assert "=>" not in result
        assert "Eq" not in result

    def test_type_vars_canonicalized(self):
        result = normalize_type("forall x y. x -> y -> x")
        assert result == ["a", "->", "b", "->", "a"]

    def test_list_type(self):
        result = normalize_type("[Int] -> Int")
        assert "[" in result
        assert "Int" in result

    def test_empty_type(self):
        assert normalize_type("") == ["<unk>"]

    def test_function_type_with_parens(self):
        result = normalize_type("(Int -> Int) -> [Int] -> [Int]")
        assert "(" in result
        assert ")" in result
        assert "->" in result

    def test_multiple_constraints(self):
        result = normalize_type("(Eq a, Ord a) => a -> a -> Bool")
        assert "=>" not in result
        assert "Eq" not in result
        assert "Ord" not in result

    def test_tuple_type(self):
        result = normalize_type("(Int, Bool) -> Int")
        assert "Int" in result
        assert "Bool" in result

    def test_nested_type_application(self):
        result = normalize_type("Maybe (Either Int Bool) -> Int")
        assert "Maybe" in result
        assert "Either" in result

    def test_type_var_canonicalization_limit(self):
        """More than 6 type vars: extras keep original names."""
        result = normalize_type("forall a1 b1 c1 d1 e1 f1 g1. a1 -> b1 -> c1 -> d1 -> e1 -> f1 -> g1")
        assert result[:2] == ["a", "->"]  # first var canonicalized


# ── convert_pat ─────────────────────────────────────────────────────────────

class TestConvertPat:
    def test_pcon_pushes_vars_and_returns_arity(self):
        r = DeBruijnRenamer()
        pat = {"tag": "PCon", "con": ":", "vars": ["h", "t"]}
        result = convert_pat(pat, r)
        assert result["tag"] == "PCon"
        assert result["con"] == ":"
        assert result["arity"] == 2
        # Vars should now be on the renamer stack
        assert r.lookup("t") == "0"
        assert r.lookup("h") == "1"

    def test_ptuple_pushes_vars(self):
        r = DeBruijnRenamer()
        pat = {"tag": "PTuple", "vars": ["a", "b"]}
        result = convert_pat(pat, r)
        assert result["tag"] == "PTuple"
        assert result["arity"] == 2
        assert r.lookup("b") == "0"
        assert r.lookup("a") == "1"

    def test_plit_no_push(self):
        r = DeBruijnRenamer()
        pat = {"tag": "PLit", "val": 0}
        result = convert_pat(pat, r)
        assert result == {"tag": "PLit", "val": 0}
        assert r._stack == []

    def test_pwild_no_push(self):
        r = DeBruijnRenamer()
        pat = {"tag": "PWild"}
        result = convert_pat(pat, r)
        assert result == {"tag": "PWild"}
        assert r._stack == []

    def test_pcon_zero_vars(self):
        r = DeBruijnRenamer()
        pat = {"tag": "PCon", "con": "[]", "vars": []}
        result = convert_pat(pat, r)
        assert result["arity"] == 0
        assert r._stack == []

    def test_unknown_tag_raises(self):
        r = DeBruijnRenamer()
        with pytest.raises(ValueError, match="Unknown pattern tag"):
            convert_pat({"tag": "PBogus"}, r)


# ── convert_expr additional coverage ────────────────────────────────────────

class TestConvertExprAdditional:
    def test_lit_passthrough(self):
        node = {"tag": "Lit", "sort": "Int", "val": 42}
        r = DeBruijnRenamer()
        result = convert_expr(node, r)
        assert result == node

    def test_prim_passthrough(self):
        node = {"tag": "Prim", "op": "+"}
        r = DeBruijnRenamer()
        result = convert_expr(node, r)
        assert result == node

    def test_con_passthrough(self):
        node = {"tag": "Con", "name": "True"}
        r = DeBruijnRenamer()
        result = convert_expr(node, r)
        assert result == node

    def test_tyarg_passthrough(self):
        node = {"tag": "TyArg", "type": "Int"}
        r = DeBruijnRenamer()
        result = convert_expr(node, r)
        assert result == node

    def test_tuple(self):
        node = {
            "tag": "Tuple",
            "elems": [{"tag": "Var", "name": "x"}, {"tag": "Var", "name": "y"}],
        }
        r = DeBruijnRenamer()
        r.push("x")
        r.push("y")
        result = convert_expr(node, r)
        assert result["tag"] == "Tuple"
        assert result["elems"][0]["name"] == "1"  # x
        assert result["elems"][1]["name"] == "0"  # y

    def test_unknown_tag_raises(self):
        r = DeBruijnRenamer()
        with pytest.raises(ValueError, match="Unknown tag"):
            convert_expr({"tag": "Bogus"}, r)


# ── debruijn_convert ────────────────────────────────────────────────────────

class TestDebruijnConvert:
    def test_simple_no_deps(self):
        fn = {
            "name": "fId",
            "type": "Int -> Int",
            "ok": True,
            "body": {
                "tag": "Lam",
                "binders": [{"name": "x", "kind": "val"}],
                "body": {"tag": "Var", "name": "x"},
            },
        }
        result = debruijn_convert(fn)
        assert result["deps"] == []
        assert result["body"]["body"]["name"] == "0"

    def test_self_recursive(self):
        fn = {
            "name": "fFact",
            "type": "Int -> Int",
            "ok": True,
            "body": {
                "tag": "Lam",
                "binders": [{"name": "n", "kind": "val"}],
                "body": {"tag": "Var", "name": "fFact"},
            },
        }
        result = debruijn_convert(fn)
        assert result["body"]["body"]["name"] == "SELF"
        assert result["deps"] == []

    def test_unknown_dep_lambda_lifted(self):
        fn = {
            "name": "fUse",
            "type": "Int -> Int",
            "ok": True,
            "body": {
                "tag": "Lam",
                "binders": [{"name": "x", "kind": "val"}],
                "body": {
                    "tag": "App",
                    "fun": {"tag": "Var", "name": "unknownFn"},
                    "args": [{"tag": "Var", "name": "x"}],
                },
            },
        }
        result = debruijn_convert(fn)
        assert len(result["deps"]) == 1
        assert result["deps"][0]["func"] == "unknownFn"
        # unknownFn should be lambda-lifted to the outermost binder
        # The body's Lam should have dep binder + original binder merged
        body = result["body"]
        assert body["tag"] == "Lam"
        assert len(body["binders"]) == 2  # unknownFn binder + x binder

    def test_known_names_become_global_ref(self):
        fn = {
            "name": "fUse",
            "type": "[Int] -> Int",
            "ok": True,
            "body": {
                "tag": "Lam",
                "binders": [{"name": "xs", "kind": "val"}],
                "body": {
                    "tag": "App",
                    "fun": {"tag": "Var", "name": "fSum"},
                    "args": [{"tag": "Var", "name": "xs"}],
                },
            },
        }
        result = debruijn_convert(fn, known_names={"fSum"})
        assert result["deps"] == []  # fSum is known, not lambda-lifted
        inner_fun = result["body"]["body"]["fun"]
        assert inner_fun["tag"] == "GlobalRef"
        assert inner_fun["name"] == "fSum"


# ── Serialization: Let, Tuple, SELF ─────────────────────────────────────────

class TestSerializationAdditional:
    def test_let(self):
        tokens = []
        node = {
            "tag": "Let",
            "binds": [{"val": {"tag": "Lit", "sort": "Int", "val": 1}}],
            "body": {"tag": "Var", "name": "0"},
        }
        serialize_expr(node, tokens)
        assert tokens == ["(Let", "(Bind", "Lit", "1", ")", "Var", "0", ")"]

    def test_let_multiple_binds(self):
        tokens = []
        node = {
            "tag": "Let",
            "binds": [
                {"val": {"tag": "Lit", "sort": "Int", "val": 1}},
                {"val": {"tag": "Lit", "sort": "Int", "val": 2}},
            ],
            "body": {"tag": "Var", "name": "0"},
        }
        serialize_expr(node, tokens)
        assert tokens == [
            "(Let", "(Bind", "Lit", "1", ")", "(Bind", "Lit", "2", ")",
            "Var", "0", ")"
        ]

    def test_tuple(self):
        tokens = []
        node = {
            "tag": "Tuple",
            "elems": [
                {"tag": "Var", "name": "0"},
                {"tag": "Var", "name": "1"},
            ],
        }
        serialize_expr(node, tokens)
        assert tokens == ["(Tuple", "Var", "0", "Var", "1", ")"]

    def test_self_token(self):
        tokens = []
        serialize_expr({"tag": "Var", "name": "SELF"}, tokens)
        assert tokens == ["Var", "SELF"]

    def test_nested_app_case(self):
        """Serialize nested App inside Case alt."""
        tokens = []
        node = {
            "tag": "Case",
            "scrutinee": {"tag": "Var", "name": "0"},
            "alts": [{
                "pat": {"tag": "PWild"},
                "body": {
                    "tag": "App",
                    "fun": {"tag": "Prim", "op": "+"},
                    "args": [
                        {"tag": "Var", "name": "0"},
                        {"tag": "Lit", "sort": "Int", "val": 1},
                    ],
                },
            }],
        }
        serialize_expr(node, tokens)
        assert tokens == [
            "(Case", "Var", "0",
            "(Alt", "(PWild)", "(App", "Prim", "+", "Var", "0", "Lit", "1", ")", ")",
            ")"
        ]

    def test_unknown_tag_raises(self):
        with pytest.raises(ValueError, match="Unknown tag"):
            serialize_expr({"tag": "Bogus"}, [])

    def test_unknown_pat_tag_raises(self):
        with pytest.raises(ValueError, match="Unknown pattern tag"):
            serialize_pat({"tag": "PBogus"}, [])
