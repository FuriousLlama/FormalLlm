"""Fragment catalog for compositional synthesis.

Loads Level-0 primitives from expressions.jsonl and provides
typed lookup for bottom-up composition.

Phase 0 of Sprint 22.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Fragment:
    """A typed primitive expression with pre-computed test outputs."""
    tag: str                        # e.g. "filter_gt0"
    expr: str                       # e.g. "xs.filter (fun x => decide (x > 0))"
    input_type: str                 # e.g. "List Int"
    output_type: str                # e.g. "List Int"
    outputs: dict[str, str]         # {input_string: output_string}
    input_var: str = "xs"           # variable name used in expr

    def evaluate(self, input_str: str) -> str | None:
        """Look up cached output for a given input string."""
        return self.outputs.get(input_str)


def _infer_input_var(input_type: str) -> str:
    """Infer the variable name used in expressions for a given input type."""
    if input_type == "List (List Int)":
        return "xss"
    elif input_type == "Nat":
        return "n"
    return "xs"


def load_level0(expressions_path: str) -> list[Fragment]:
    """Load Level-0 fragments from expressions.jsonl."""
    fragments = []
    with open(expressions_path) as f:
        for line in f:
            entry = json.loads(line)
            if entry.get("level", 0) != 0:
                continue
            fragments.append(Fragment(
                tag=entry["tag"],
                expr=entry["expr"],
                input_type=entry["input_type"],
                output_type=entry["return_type"],
                outputs=entry.get("outputs") or {},
                input_var=_infer_input_var(entry["input_type"]),
            ))
    return fragments


# Synthetic fragments for types not covered by expressions.jsonl.
# These bridge type gaps (e.g., List Nat has no Level-0 fragments as input).
SYNTHETIC_FRAGMENTS = [
    Fragment(
        tag="listnat_foldl_max",
        expr="xs.foldl max 0",
        input_type="List Nat",
        output_type="Nat",
        outputs={},
        input_var="xs",
    ),
    Fragment(
        tag="listnat_foldl_sum",
        expr="xs.foldl (· + ·) 0",
        input_type="List Nat",
        output_type="Nat",
        outputs={},
        input_var="xs",
    ),
    Fragment(
        tag="listnat_foldl_min",
        expr="xs.foldl min 0",
        input_type="List Nat",
        output_type="Nat",
        outputs={},
        input_var="xs",
    ),
    Fragment(
        tag="listnat_length",
        expr="xs.length",
        input_type="List Nat",
        output_type="Nat",
        outputs={},
        input_var="xs",
    ),
    Fragment(
        tag="listnat_reverse",
        expr="xs.reverse",
        input_type="List Nat",
        output_type="List Nat",
        outputs={},
        input_var="xs",
    ),
]


class FragmentCatalog:
    """Typed fragment catalog for compositional search."""

    def __init__(self, expressions_path: str = None):
        if expressions_path is None:
            base = Path(__file__).resolve().parent
            expressions_path = str(base / "expressions.jsonl")

        self.fragments = load_level0(expressions_path) + list(SYNTHETIC_FRAGMENTS)
        self._by_input: dict[str, list[Fragment]] = {}
        self._by_output: dict[str, list[Fragment]] = {}

        for f in self.fragments:
            self._by_input.setdefault(f.input_type, []).append(f)
            self._by_output.setdefault(f.output_type, []).append(f)

    def by_input_type(self, itype: str) -> list[Fragment]:
        """Get fragments that accept the given input type."""
        return self._by_input.get(itype, [])

    def by_output_type(self, otype: str) -> list[Fragment]:
        """Get fragments that produce the given output type."""
        return self._by_output.get(otype, [])

    def composable_pairs(
        self, input_type: str, output_type: str,
    ) -> list[tuple[Fragment, Fragment]]:
        """Return (inner, outer) pairs for depth-2 composition.

        inner.input_type == input_type
        outer.output_type == output_type
        inner.output_type == outer.input_type
        """
        pairs = []
        inners = self.by_input_type(input_type)
        outers = self.by_output_type(output_type)
        outer_by_input = {}
        for o in outers:
            outer_by_input.setdefault(o.input_type, []).append(o)

        for inner in inners:
            for outer in outer_by_input.get(inner.output_type, []):
                pairs.append((inner, outer))
        return pairs

    def composable_triples(
        self, input_type: str, output_type: str,
    ) -> list[tuple[Fragment, Fragment, Fragment]]:
        """Return (a, b, c) triples for depth-3 composition.

        a.input_type == input_type
        c.output_type == output_type
        a.output_type == b.input_type
        b.output_type == c.input_type
        """
        triples = []
        a_frags = self.by_input_type(input_type)
        c_frags = self.by_output_type(output_type)

        # Index c by input_type
        c_by_input = {}
        for c in c_frags:
            c_by_input.setdefault(c.input_type, []).append(c)

        # For each a, find possible mid types, then b candidates, then c
        for a in a_frags:
            mid1_type = a.output_type
            b_frags = self.by_input_type(mid1_type)
            for b in b_frags:
                mid2_type = b.output_type
                for c in c_by_input.get(mid2_type, []):
                    triples.append((a, b, c))
        return triples

    def extend_outputs(self, tag: str, new_outputs: dict[str, str]):
        """Add cached outputs for a fragment (e.g., from task test inputs)."""
        for f in self.fragments:
            if f.tag == tag:
                f.outputs.update(new_outputs)
                return

    def get_fragment(self, tag: str) -> Fragment | None:
        """Look up a fragment by tag."""
        for f in self.fragments:
            if f.tag == tag:
                return f
        return None

    def __len__(self) -> int:
        return len(self.fragments)

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [f"FragmentCatalog: {len(self.fragments)} fragments"]
        for itype in sorted(self._by_input):
            n = len(self._by_input[itype])
            out_types = set(f.output_type for f in self._by_input[itype])
            lines.append(f"  {itype} → {', '.join(sorted(out_types))} ({n} fragments)")
        return "\n".join(lines)
