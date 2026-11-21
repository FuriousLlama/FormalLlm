"""
augment.py — Synthetic corpus augmentation.

Under de Bruijn representation, re-alpha-renaming is an identity operation
(all alpha-equivalent terms already have the same token sequence). Removed.

Remaining augmentation:
  1. commutative-swap — swap operands of commutative ops (+, *, ==, /=, &&, ||)

Dropped (pre-de Bruijn, low yield): let-reorder, eta-expand.
Dropped (de Bruijn, identity): re-alpha-rename.

Input:  sequences.json  (output of normalize.py)
Output: sequences_aug.json  (original + augmented, deduplicated)
"""

import argparse
import json
import random
from pathlib import Path


# ---------------------------------------------------------------------------
# Token-level commutative operand swap
# ---------------------------------------------------------------------------

COMMUTATIVE_OPS = frozenset({"+", "*", "==", "/=", "&&", "||"})


def _expr_span(tokens: list[str], start: int) -> int:
    """
    Return the index *past* the expression starting at `start`.

    Rules:
      - If tokens[start] begins with '(': scan forward counting parens until balanced.
      - If tokens[start] in {Var, Lit, Con, Prim}: span is 2 tokens (keyword + value).
      - If tokens[start] is '_': span is 1 token.
      - Otherwise: span is 1 token (defensive fallback).
    """
    if start >= len(tokens):
        return start

    tok = tokens[start]

    if tok.startswith("("):
        # Paren-balanced scan
        depth = 1
        i = start + 1
        while i < len(tokens) and depth > 0:
            if tokens[i].startswith("("):
                depth += 1
            elif tokens[i] == ")":
                depth -= 1
            i += 1
        return i  # one past the closing ')'

    if tok in ("Var", "Lit", "Con", "Prim"):
        return start + 2  # keyword + value

    return start + 1  # wildcard '_' or defensive fallback


def augment_commutative_swap(seq: dict) -> dict | None:
    """
    Swap operands of ALL commutative binary ops in the token stream.

    Looks for: (App  Prim OP  arg1  arg2  )
    Produces:  (App  Prim OP  arg2  arg1  )

    Returns None if no commutative sites found.
    """
    tokens = seq["tokens"]
    n = len(tokens)
    # Collect swap sites: list of (arg1_start, arg1_end, arg2_start, arg2_end)
    sites: list[tuple[int, int, int, int]] = []

    i = 0
    while i < n:
        # Pattern: (App  Prim  OP  arg1  arg2  )
        if (tokens[i] == "(App"
                and i + 2 < n
                and tokens[i + 1] == "Prim"
                and tokens[i + 2] in COMMUTATIVE_OPS):
            arg1_start = i + 3
            arg1_end = _expr_span(tokens, arg1_start)
            arg2_start = arg1_end
            arg2_end = _expr_span(tokens, arg2_start)
            # Sanity: arg2 must end before or at the closing ')' of (App
            if arg1_end <= n and arg2_end <= n and arg1_start < arg1_end and arg2_start < arg2_end:
                sites.append((arg1_start, arg1_end, arg2_start, arg2_end))
        i += 1

    if not sites:
        return None

    # Build new token list, swapping all sites (process right-to-left to keep indices valid)
    new_tokens = list(tokens)
    for a1s, a1e, a2s, a2e in reversed(sites):
        arg1 = new_tokens[a1s:a1e]
        arg2 = new_tokens[a2s:a2e]
        new_tokens[a1s:a2e] = arg2 + arg1

    if new_tokens == tokens:
        return None

    entry = {"name": seq["name"], "tokens": new_tokens, "augmented": "commutative"}
    if "package" in seq:
        entry["package"] = seq["package"]
    if "deps" in seq:
        entry["deps"] = seq["deps"]
    if "type" in seq:
        entry["type"] = seq["type"]
    return entry


# ---------------------------------------------------------------------------
# Main augmentation pipeline
# ---------------------------------------------------------------------------

def augment_sequence(seq: dict, rng: random.Random) -> list[dict]:
    """Generate augmented copies of a sequence."""
    variants = []

    # Commutative operand swap
    v = augment_commutative_swap(seq)
    if v:
        variants.append(v)

    return variants


def deduplicate(sequences: list[dict]) -> list[dict]:
    """Remove duplicate token sequences (keep first occurrence)."""
    seen: set[tuple] = set()
    out = []
    for seq in sequences:
        key = tuple(seq["tokens"])
        if key not in seen:
            seen.add(key)
            out.append(seq)
    return out


def main():
    p = argparse.ArgumentParser(description="Augment corpus with synthetic variants")
    p.add_argument("input", nargs="?", default="sprint/corpus/sequences.json")
    p.add_argument("--out", default="sprint/corpus/sequences_aug.json")
    p.add_argument("--tokenizer", default="sprint/corpus/tokenizer.json",
                   help="Tokenizer path (used to rebuild if vocab grows)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    rng = random.Random(args.seed)
    sequences = json.loads(Path(args.input).read_text())

    augmented: list[dict] = []
    total_variants = 0

    for seq in sequences:
        augmented.append(seq)  # keep original
        variants = augment_sequence(seq, rng)
        augmented.extend(variants)
        total_variants += len(variants)

    augmented = deduplicate(augmented)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(augmented, indent=2))

    if args.stats:
        orig = len(sequences)
        total = len(augmented)
        lengths = [len(s["tokens"]) for s in augmented]
        vocab = set(t for s in augmented for t in s["tokens"])
        by_type: dict[str, int] = {}
        for s in augmented:
            k = s.get("augmented", "original")
            by_type[k] = by_type.get(k, 0) + 1

        print(f"[augment] original sequences:  {orig}")
        print(f"[augment] variants generated:  {total_variants}")
        print(f"[augment] after dedup:         {total}")
        print(f"[augment] breakdown: " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
        print(f"[augment] token lengths: min={min(lengths)} avg={sum(lengths)//len(lengths)} max={max(lengths)}")
        print(f"[augment] vocab size:          {len(vocab)}")

    print(f"[augment] wrote {len(augmented)} sequences → {args.out}")


if __name__ == "__main__":
    main()
