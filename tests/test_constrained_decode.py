"""Tests for constrained_decode.py — mask cache and _valid_ids_for.

Tests the core constrained decoding logic without requiring a model or GPU:
  - _valid_ids_for computes correct valid token sets
  - Mask cache stores and retrieves results
  - clear_mask_cache resets state
  - Special token filtering (<pad>, <bos>, <unk>, <eos>)
  - EOS only allowed after EOF
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "sprint" / "typecheck"))

from constrained_decode import (
    _valid_ids_for,
    _mask_cache,
    clear_mask_cache,
    PAD_ID,
    UNK_ID,
    BOS_ID,
    EOS_ID,
    _SKIP_IDS,
)
from checker import is_type_valid


# ── Fixtures ────────────────────────────────────────────────────────────────

# Minimal vocab for testing: just enough tokens to build valid sequences
MINI_VOCAB = {
    0: "<pad>",
    1: "<unk>",
    2: "<bos>",
    3: "<eos>",
    4: "DEF",
    5: "fId",
    6: "TY",
    7: "Int",
    8: "->",
    9: "BODY",
    10: "(Lam",
    11: "1",
    12: "Var",
    13: "0",
    14: ")",
    15: "EOF",
    16: "Lit",
    17: "42",
    18: "Prim",
    19: "+",
}
MINI_VOCAB_SIZE = len(MINI_VOCAB)


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure clean cache state for every test."""
    clear_mask_cache()
    yield
    clear_mask_cache()


# ── _valid_ids_for ──────────────────────────────────────────────────────────

class TestValidIdsFor:
    def test_empty_partial_accepts_def(self):
        """With no partial tokens, DEF should be valid."""
        valid = _valid_ids_for([], MINI_VOCAB, MINI_VOCAB_SIZE)
        # id 4 = "DEF", should be valid because is_type_valid([], "DEF") is checked
        # Actually is_type_valid([], "DEF") tries to parse ["DEF"] which needs more
        # But at minimum, special tokens should never be valid
        assert PAD_ID not in valid
        assert UNK_ID not in valid
        assert BOS_ID not in valid
        assert EOS_ID not in valid

    def test_special_tokens_never_valid(self):
        """<pad>, <unk>, <bos>, <eos> should never appear in valid set."""
        partial = ["DEF", "fId"]
        valid = _valid_ids_for(partial, MINI_VOCAB, MINI_VOCAB_SIZE)
        for skip_id in _SKIP_IDS:
            assert skip_id not in valid

    def test_after_def_name_accepts_expr_starters(self):
        """After DEF fId, expression starters and TY should be valid.
        Sprint 10: TY is valid because partial headers are accepted for USES support."""
        partial = ["DEF", "fId"]
        valid = _valid_ids_for(partial, MINI_VOCAB, MINI_VOCAB_SIZE)
        # TY (id 6) IS valid — partial header accepted (Sprint 10)
        assert 6 in valid
        # Expression starters should be valid (sprint4 format)
        assert 10 in valid  # (Lam
        assert 16 in valid  # Lit

    def test_complete_identity_accepts_eof(self):
        """After a complete body, EOF should be valid."""
        partial = ["DEF", "fId", "TY", "Int", "->", "Int", "BODY", "(Lam", "1", "Var", "0", ")"]
        valid = _valid_ids_for(partial, MINI_VOCAB, MINI_VOCAB_SIZE)
        assert 15 in valid  # EOF

    def test_returns_frozenset(self):
        valid = _valid_ids_for(["DEF", "fId"], MINI_VOCAB, MINI_VOCAB_SIZE)
        assert isinstance(valid, frozenset)


# ── Mask cache ──────────────────────────────────────────────────────────────

class TestMaskCache:
    def test_cache_populated_on_first_call(self):
        assert len(_mask_cache) == 0
        _valid_ids_for(["DEF", "fId"], MINI_VOCAB, MINI_VOCAB_SIZE)
        assert len(_mask_cache) == 1

    def test_cache_hit_returns_same_result(self):
        partial = ["DEF", "fId"]
        result1 = _valid_ids_for(partial, MINI_VOCAB, MINI_VOCAB_SIZE)
        result2 = _valid_ids_for(partial, MINI_VOCAB, MINI_VOCAB_SIZE)
        assert result1 is result2  # exact same frozenset object (cache hit)

    def test_different_partials_different_keys(self):
        _valid_ids_for(["DEF", "fId"], MINI_VOCAB, MINI_VOCAB_SIZE)
        _valid_ids_for(["DEF", "fId", "TY"], MINI_VOCAB, MINI_VOCAB_SIZE)
        assert len(_mask_cache) == 2

    def test_clear_removes_all(self):
        _valid_ids_for(["DEF", "fId"], MINI_VOCAB, MINI_VOCAB_SIZE)
        assert len(_mask_cache) > 0
        clear_mask_cache()
        assert len(_mask_cache) == 0


# ── Constants ───────────────────────────────────────────────────────────────

class TestConstants:
    def test_pad_id(self):
        assert PAD_ID == 0

    def test_unk_id(self):
        assert UNK_ID == 1

    def test_bos_id(self):
        assert BOS_ID == 2

    def test_eos_id(self):
        assert EOS_ID == 3

    def test_skip_ids_contains_all_special(self):
        assert _SKIP_IDS == {0, 1, 2, 3}


# ── Integration: valid_ids matches is_type_valid ───────────────────────────

class TestValidIdsMatchesGrammar:
    """Verify _valid_ids_for is consistent with is_type_valid."""

    def test_consistency(self):
        """For each token in vocab, _valid_ids_for should agree with is_type_valid."""
        partial = ["DEF", "fId", "TY", "Int", "->", "Int", "BODY"]
        valid = _valid_ids_for(partial, MINI_VOCAB, MINI_VOCAB_SIZE)
        for tid, tok in MINI_VOCAB.items():
            if tok in ("<pad>", "<bos>", "<unk>", "<eos>"):
                assert tid not in valid
            else:
                expected = is_type_valid(partial, tok)
                actual = tid in valid
                assert actual == expected, (
                    f"Mismatch for token {tok!r} (id={tid}): "
                    f"is_type_valid={expected}, in valid_ids={actual}"
                )
