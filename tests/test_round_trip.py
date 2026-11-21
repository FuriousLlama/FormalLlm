"""Round-trip integration tests: normalize → serialize → parse → pretty_print.

Verifies that the pipeline produces consistent output:
  1. Raw AST → de Bruijn conversion → serialization → token sequence
  2. Token sequence → parse → pretty_print → Haskell source
  3. Haskell source matches expected output

These tests exercise the full path without external dependencies (no GHC).
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "sprint" / "corpus"))
sys.path.insert(0, str(Path(__file__).parent.parent / "sprint" / "typecheck"))
sys.path.insert(0, str(Path(__file__).parent.parent / "sprint" / "benchmark"))

from normalize import (
    DeBruijnRenamer,
    convert_expr,
    serialize_expr,
    to_sequence,
    normalize_type,
)
from checker import TokenStream, parse_sequence, parse_expr
from pretty_print import tokens_to_haskell, pp_expr, pp_func


# ── Helpers ──────────────────────────────────────────────────────────────────

def normalize_then_pretty(fn_dict: dict) -> str:
    """Full pipeline: raw AST → tokens → Haskell string."""
    result = to_sequence(fn_dict)
    tokens = result["tokens"]
    _, haskell = tokens_to_haskell(tokens)
    return haskell


def tokens_from_ast(body_ast: dict, renamer: DeBruijnRenamer = None) -> list[str]:
    """Convert a raw AST body to token list via de Bruijn + serialize."""
    if renamer is None:
        renamer = DeBruijnRenamer()
    converted = convert_expr(body_ast, renamer)
    tokens = []
    serialize_expr(converted, tokens)
    return tokens


# ── Identity function ───────────────────────────────────────────────────────

class TestIdentityRoundTrip:
    """\\x -> x"""
    RAW = {
        "name": "fId",
        "type": "Int -> Int",
        "ok": True,
        "body": {
            "tag": "Lam",
            "binders": [{"name": "x", "kind": "val"}],
            "body": {"tag": "Var", "name": "x"},
        },
    }

    def test_tokens(self):
        result = to_sequence(self.RAW)
        body_tokens = result["tokens"]
        # Should contain: DEF fId TY Int -> Int BODY (Lam 1 Var 0 ) EOF
        assert "DEF" in body_tokens
        assert "EOF" in body_tokens
        assert "(Lam" in body_tokens
        assert "Var" in body_tokens
        assert "0" in body_tokens

    def test_haskell(self):
        h = normalize_then_pretty(self.RAW)
        assert h == "f x = x"


# ── Two-argument function ──────────────────────────────────────────────────

class TestTwoArgRoundTrip:
    """\\x y -> x + y"""
    RAW = {
        "name": "fAdd",
        "type": "Int -> Int -> Int",
        "ok": True,
        "body": {
            "tag": "Lam",
            "binders": [
                {"name": "x", "kind": "val"},
                {"name": "y", "kind": "val"},
            ],
            "body": {
                "tag": "App",
                "fun": {"tag": "Prim", "op": "+"},
                "args": [
                    {"tag": "Var", "name": "x"},
                    {"tag": "Var", "name": "y"},
                ],
            },
        },
    }

    def test_tokens(self):
        result = to_sequence(self.RAW)
        tokens = result["tokens"]
        # Body should have Lam 2 (two val binders)
        body_start = tokens.index("BODY")
        body_tokens = tokens[body_start + 1:-1]  # strip BODY and EOF
        assert "(Lam" in body_tokens
        assert "2" in body_tokens

    def test_haskell(self):
        h = normalize_then_pretty(self.RAW)
        assert h == "f x y = x + y"


# ── Constant function ──────────────────────────────────────────────────────

class TestConstantRoundTrip:
    """\\x -> 42"""
    RAW = {
        "name": "fConst",
        "type": "Int -> Int",
        "ok": True,
        "body": {
            "tag": "Lam",
            "binders": [{"name": "x", "kind": "val"}],
            "body": {"tag": "Lit", "sort": "Int", "val": 42},
        },
    }

    def test_haskell(self):
        h = normalize_then_pretty(self.RAW)
        assert h == "f x = 42"


# ── Nested lambda ──────────────────────────────────────────────────────────

class TestNestedLambdaRoundTrip:
    """\\x -> \\y -> x"""
    RAW = {
        "name": "fConst2",
        "type": "a -> b -> a",
        "ok": True,
        "body": {
            "tag": "Lam",
            "binders": [{"name": "x", "kind": "val"}],
            "body": {
                "tag": "Lam",
                "binders": [{"name": "y", "kind": "val"}],
                "body": {"tag": "Var", "name": "x"},
            },
        },
    }

    def test_tokens_de_bruijn_correct(self):
        """Inner reference to x should be index 1 (one layer out)."""
        result = to_sequence(self.RAW)
        tokens = result["tokens"]
        # Should see: Var 1 (x is one scope up from inner lambda)
        body_start = tokens.index("BODY")
        body_str = " ".join(tokens[body_start + 1:-1])
        assert "Var 1" in body_str

    def test_haskell(self):
        h = normalize_then_pretty(self.RAW)
        assert h == "f x = \\y -> x"


# ── Case with cons pattern ─────────────────────────────────────────────────

class TestConsPatternRoundTrip:
    """case xs of { (h:t) -> h; [] -> 0 }"""
    RAW = {
        "name": "fHead",
        "type": "[Int] -> Int",
        "ok": True,
        "body": {
            "tag": "Lam",
            "binders": [{"name": "xs", "kind": "val"}],
            "body": {
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
                ],
            },
        },
    }

    def test_de_bruijn_indices(self):
        """h is pushed first (index 1), t pushed second (index 0).
        Body references h → should be Var 1."""
        result = to_sequence(self.RAW)
        tokens = result["tokens"]
        body_start = tokens.index("BODY")
        body_str = " ".join(tokens[body_start + 1:-1])
        # The cons alt body should reference Var 1 (head)
        assert "(Alt (PCon : 2) Var 1 )" in body_str or \
               "(Alt (PCon : 2 ) Var 1 )" in body_str

    def test_haskell_head_access(self):
        """Pretty-printed head access should use the correct variable.

        Outer Lam uses x (gen 0→1). _pat_bind(arity=2) generates [y,z] (gen 1→3).
        env = [z, y, x]. Pattern = (y:z). Index 1 → y (head).
        """
        h = normalize_then_pretty(self.RAW)
        assert "-> y" in h, f"Expected head variable in body: {h}"

    def test_haskell_pattern_format(self):
        """Should have a cons pattern like (x:y)."""
        h = normalize_then_pretty(self.RAW)
        assert ":" in h


class TestConsPatternTailRoundTrip:
    """case xs of { (h:t) -> t; [] -> [] }"""
    RAW = {
        "name": "fTail",
        "type": "[Int] -> [Int]",
        "ok": True,
        "body": {
            "tag": "Lam",
            "binders": [{"name": "xs", "kind": "val"}],
            "body": {
                "tag": "Case",
                "scrutinee": {"tag": "Var", "name": "xs"},
                "alts": [
                    {
                        "pat": {"tag": "PCon", "con": ":", "vars": ["h", "t"]},
                        "body": {"tag": "Var", "name": "t"},
                    },
                    {
                        "pat": {"tag": "PCon", "con": "[]", "vars": []},
                        "body": {"tag": "Con", "name": "[]"},
                    },
                ],
            },
        },
    }

    def test_de_bruijn_indices(self):
        """t is pushed second (index 0). Body references t → should be Var 0."""
        result = to_sequence(self.RAW)
        tokens = result["tokens"]
        body_start = tokens.index("BODY")
        body_str = " ".join(tokens[body_start + 1:-1])
        assert "Var 0" in body_str

    def test_haskell_tail_access(self):
        """Var 0 (tail) → env[0] = z in pattern (y:z)."""
        h = normalize_then_pretty(self.RAW)
        assert "-> z" in h, f"Expected tail variable in body: {h}"


# ── Let binding ─────────────────────────────────────────────────────────────

class TestLetRoundTrip:
    """let a = 1; b = 2 in a + b"""
    RAW = {
        "name": "fLetAdd",
        "type": "Int",
        "ok": True,
        "body": {
            "tag": "Let",
            "binds": [
                {"name": "a", "val": {"tag": "Lit", "sort": "Int", "val": 1}},
                {"name": "b", "val": {"tag": "Lit", "sort": "Int", "val": 2}},
            ],
            "body": {
                "tag": "App",
                "fun": {"tag": "Prim", "op": "+"},
                "args": [
                    {"tag": "Var", "name": "a"},
                    {"tag": "Var", "name": "b"},
                ],
            },
        },
    }

    def test_de_bruijn_let_indices(self):
        """a is pushed first (index 1), b pushed second (index 0)."""
        result = to_sequence(self.RAW)
        tokens = result["tokens"]
        body_start = tokens.index("BODY")
        body_str = " ".join(tokens[body_start + 1:-1])
        # In the body: a = Var 1, b = Var 0
        assert "Var 1" in body_str  # a
        assert "Var 0" in body_str  # b

    def test_haskell(self):
        h = normalize_then_pretty(self.RAW)
        assert "let" in h
        assert "+" in h


# ── Self-recursive function ─────────────────────────────────────────────────

class TestSelfRecursiveRoundTrip:
    """fFact n = case n of { 0 -> 1; _ -> n * fFact (n - 1) }"""
    RAW = {
        "name": "fFact",
        "type": "Int -> Int",
        "ok": True,
        "body": {
            "tag": "Lam",
            "binders": [{"name": "n", "kind": "val"}],
            "body": {
                "tag": "Case",
                "scrutinee": {"tag": "Var", "name": "n"},
                "alts": [
                    {
                        "pat": {"tag": "PLit", "val": 0},
                        "body": {"tag": "Lit", "sort": "Int", "val": 1},
                    },
                    {
                        "pat": {"tag": "PWild"},
                        "body": {
                            "tag": "App",
                            "fun": {"tag": "Prim", "op": "*"},
                            "args": [
                                {"tag": "Var", "name": "n"},
                                {
                                    "tag": "App",
                                    "fun": {"tag": "Var", "name": "fFact"},
                                    "args": [
                                        {
                                            "tag": "App",
                                            "fun": {"tag": "Prim", "op": "-"},
                                            "args": [
                                                {"tag": "Var", "name": "n"},
                                                {"tag": "Lit", "sort": "Int", "val": 1},
                                            ],
                                        }
                                    ],
                                },
                            ],
                        },
                    },
                ],
            },
        },
    }

    def test_self_token_generated(self):
        """Recursive call should become SELF token."""
        result = to_sequence(self.RAW)
        assert "SELF" in result["tokens"]

    def test_haskell_has_recursion(self):
        """Pretty-printed version should show f as recursive call."""
        h = normalize_then_pretty(self.RAW)
        # The recursive call should use 'f' (SELF → f)
        assert "f (" in h or "f x" in h


# ── GlobalRef round-trip ────────────────────────────────────────────────────

class TestGlobalRefRoundTrip:
    """fSumDouble xs = fSum (fMap fDouble xs)"""
    RAW = {
        "name": "fSumDouble",
        "type": "[Int] -> Int",
        "ok": True,
        "body": {
            "tag": "Lam",
            "binders": [{"name": "xs", "kind": "val"}],
            "body": {
                "tag": "App",
                "fun": {"tag": "Var", "name": "fSum"},
                "args": [
                    {
                        "tag": "App",
                        "fun": {"tag": "Var", "name": "fMap"},
                        "args": [
                            {"tag": "Var", "name": "fDouble"},
                            {"tag": "Var", "name": "xs"},
                        ],
                    }
                ],
            },
        },
    }

    def test_global_refs_in_tokens(self):
        """Known names should appear as named tokens, not lambda-lifted."""
        known = {"fSum", "fMap", "fDouble"}
        result = to_sequence(self.RAW, known_names=known)
        tokens = result["tokens"]
        assert "fSum" in tokens
        assert "fMap" in tokens
        assert "fDouble" in tokens
        # xs should be Var 0 (bound by Lam)
        body_start = tokens.index("BODY")
        body_str = " ".join(tokens[body_start + 1:-1])
        assert "Var 0" in body_str

    def test_haskell_preserves_names(self):
        """GlobalRef names should appear in the pretty-printed output."""
        known = {"fSum", "fMap", "fDouble"}
        result = to_sequence(self.RAW, known_names=known)
        _, h = tokens_to_haskell(result["tokens"])
        assert "fSum" in h
        assert "fMap" in h
        assert "fDouble" in h


# ── Type normalization round-trip ───────────────────────────────────────────

class TestTypeInTokens:
    def test_type_appears_in_sequence(self):
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
        result = to_sequence(fn)
        tokens = result["tokens"]
        # Should have TY ... BODY structure
        assert "TY" in tokens
        assert "BODY" in tokens
        ty_idx = tokens.index("TY")
        body_idx = tokens.index("BODY")
        type_tokens = tokens[ty_idx + 1:body_idx]
        assert type_tokens == ["Int", "->", "Int"]

    def test_polymorphic_type_canonicalized(self):
        fn = {
            "name": "fConst",
            "type": "forall x y. x -> y -> x",
            "ok": True,
            "body": {
                "tag": "Lam",
                "binders": [
                    {"name": "x", "kind": "val"},
                    {"name": "y", "kind": "val"},
                ],
                "body": {"tag": "Var", "name": "x"},
            },
        }
        result = to_sequence(fn)
        tokens = result["tokens"]
        ty_idx = tokens.index("TY")
        body_idx = tokens.index("BODY")
        type_tokens = tokens[ty_idx + 1:body_idx]
        # forall x y. x -> y -> x → a -> b -> a
        assert type_tokens == ["a", "->", "b", "->", "a"]


# ── Serialize → parse round-trip ────────────────────────────────────────────

class TestSerializeParseRoundTrip:
    """Verify that serialize_expr output can be parsed back by parse_expr."""

    def _round_trip(self, body_ast: dict) -> dict:
        """Serialize body AST, wrap in DEF/EOF, parse back."""
        r = DeBruijnRenamer()
        converted = convert_expr(body_ast, r)
        tokens = []
        serialize_expr(converted, tokens)
        # Wrap in a minimal sequence
        full = ["DEF", "fTest"] + tokens + ["EOF"]
        ts = TokenStream(full)
        result = parse_sequence(ts)
        return result["body"]

    def test_var(self):
        body = {"tag": "Var", "name": "free"}
        parsed = self._round_trip(body)
        assert parsed["tag"] == "Var"
        assert parsed["name"] == "free"

    def test_lit(self):
        body = {"tag": "Lit", "sort": "Int", "val": 99}
        parsed = self._round_trip(body)
        assert parsed["tag"] == "Lit"
        assert parsed["val"] == "99"

    def test_prim(self):
        body = {"tag": "Prim", "op": "+"}
        parsed = self._round_trip(body)
        assert parsed["tag"] == "Prim"
        assert parsed["op"] == "+"

    def test_app(self):
        body = {
            "tag": "App",
            "fun": {"tag": "Prim", "op": "*"},
            "args": [
                {"tag": "Lit", "sort": "Int", "val": 2},
                {"tag": "Lit", "sort": "Int", "val": 3},
            ],
        }
        parsed = self._round_trip(body)
        assert parsed["tag"] == "App"
        assert parsed["fun"]["op"] == "*"
        assert len(parsed["args"]) == 2

    def test_lam(self):
        body = {
            "tag": "Lam",
            "binders": [{"name": "x", "kind": "val"}],
            "body": {"tag": "Var", "name": "x"},
        }
        parsed = self._round_trip(body)
        assert parsed["tag"] == "Lam"
        assert parsed["n_val"] == 1
        assert parsed["body"]["tag"] == "Var"
        assert parsed["body"]["name"] == "0"

    def test_case(self):
        body = {
            "tag": "Case",
            "scrutinee": {"tag": "Var", "name": "free"},
            "alts": [
                {
                    "pat": {"tag": "PWild"},
                    "body": {"tag": "Lit", "sort": "Int", "val": 0},
                },
            ],
        }
        parsed = self._round_trip(body)
        assert parsed["tag"] == "Case"
        assert len(parsed["alts"]) == 1
        assert parsed["alts"][0]["pat"]["tag"] == "PWild"

    def test_tuple(self):
        body = {
            "tag": "Tuple",
            "elems": [
                {"tag": "Lit", "sort": "Int", "val": 1},
                {"tag": "Lit", "sort": "Int", "val": 2},
            ],
        }
        parsed = self._round_trip(body)
        assert parsed["tag"] == "Tuple"
        assert len(parsed["elems"]) == 2

    def test_let(self):
        body = {
            "tag": "Let",
            "binds": [{"name": "a", "val": {"tag": "Lit", "sort": "Int", "val": 7}}],
            "body": {"tag": "Var", "name": "a"},
        }
        parsed = self._round_trip(body)
        assert parsed["tag"] == "Let"
        assert len(parsed["binds"]) == 1
        assert parsed["body"]["tag"] == "Var"
        assert parsed["body"]["name"] == "0"

    def test_nested_app_in_case(self):
        """App inside Case alt body should round-trip."""
        body = {
            "tag": "Case",
            "scrutinee": {"tag": "Var", "name": "free"},
            "alts": [{
                "pat": {"tag": "PWild"},
                "body": {
                    "tag": "App",
                    "fun": {"tag": "Prim", "op": "+"},
                    "args": [
                        {"tag": "Lit", "sort": "Int", "val": 1},
                        {"tag": "Lit", "sort": "Int", "val": 2},
                    ],
                },
            }],
        }
        parsed = self._round_trip(body)
        assert parsed["tag"] == "Case"
        alt_body = parsed["alts"][0]["body"]
        assert alt_body["tag"] == "App"
        assert alt_body["fun"]["op"] == "+"


# ── Tuple full round-trip ─────────────────────────────────────────────────

class TestTupleRoundTrip:
    """\\x y -> (x, y)"""
    RAW = {
        "name": "fPair",
        "type": "Int -> Int -> (Int, Int)",
        "ok": True,
        "body": {
            "tag": "Lam",
            "binders": [
                {"name": "x", "kind": "val"},
                {"name": "y", "kind": "val"},
            ],
            "body": {
                "tag": "Tuple",
                "elems": [
                    {"tag": "Var", "name": "x"},
                    {"tag": "Var", "name": "y"},
                ],
            },
        },
    }

    def test_tokens(self):
        result = to_sequence(self.RAW)
        tokens = result["tokens"]
        assert "(Tuple" in tokens
        body_start = tokens.index("BODY")
        body_str = " ".join(tokens[body_start + 1:-1])
        assert "Var 1" in body_str  # x
        assert "Var 0" in body_str  # y

    def test_haskell(self):
        h = normalize_then_pretty(self.RAW)
        assert "," in h
        assert "x" in h and "y" in h


# ── Case with multiple alt types ─────────────────────────────────────────

class TestMultiAltRoundTrip:
    """case n of { 0 -> 1; _ -> n * f(n-1) }"""
    RAW = {
        "name": "fTest",
        "type": "Int -> Int",
        "ok": True,
        "body": {
            "tag": "Lam",
            "binders": [{"name": "n", "kind": "val"}],
            "body": {
                "tag": "Case",
                "scrutinee": {"tag": "Var", "name": "n"},
                "alts": [
                    {
                        "pat": {"tag": "PLit", "val": 0},
                        "body": {"tag": "Lit", "sort": "Int", "val": 1},
                    },
                    {
                        "pat": {"tag": "PWild"},
                        "body": {"tag": "Var", "name": "n"},
                    },
                ],
            },
        },
    }

    def test_both_alts_present(self):
        result = to_sequence(self.RAW)
        tokens = result["tokens"]
        body_str = " ".join(tokens)
        assert "(PLit" in body_str
        assert "(PWild)" in body_str

    def test_haskell_has_case(self):
        h = normalize_then_pretty(self.RAW)
        assert "case" in h
        assert "0 -> 1" in h


# ── Sprint 5b format round-trip (DEF name TY ... BODY ... EOF) ──────────

class TestSprint5bRoundTrip:
    """Verify the sprint5b format is what to_sequence produces and parse handles."""

    def test_format_structure(self):
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
        result = to_sequence(fn)
        tokens = result["tokens"]
        assert tokens[0] == "DEF"
        assert tokens[1] == "fId"
        assert tokens[2] == "TY"
        assert "BODY" in tokens
        assert tokens[-1] == "EOF"

    def test_parse_back(self):
        fn = {
            "name": "fDouble",
            "type": "Int -> Int",
            "ok": True,
            "body": {
                "tag": "Lam",
                "binders": [{"name": "x", "kind": "val"}],
                "body": {
                    "tag": "App",
                    "fun": {"tag": "Prim", "op": "+"},
                    "args": [
                        {"tag": "Var", "name": "x"},
                        {"tag": "Var", "name": "x"},
                    ],
                },
            },
        }
        result = to_sequence(fn)
        # Parse back
        from checker import TokenStream as TS, parse_sequence as ps
        ts = TS(result["tokens"])
        parsed = ps(ts)
        assert parsed["name"] == "fDouble"
        assert parsed["body"]["tag"] == "Lam"
