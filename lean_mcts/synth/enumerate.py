"""Phase 1: Seed enumeration of typed synthesis expressions.

Generates Level 0 (primitives), Level 1 (two-step compositions), and
Level 2 (three-step compositions) by combining Lean library functions
with concrete predicates/transforms, then validates each expression by
compiling a temporary Lean file.

Sprint 14 extensions:
  - Expanded library: tail, dropLast, intersperse, scanl max/min,
    partition, filterMap, zipIdx, Prod.fst/snd
  - Multi-type inputs: Nat, List (List Int), two-arg (xs ys : List Int)
  - Level 2: three-step compositions (capped at ~500)

Usage:
    python synth/enumerate.py --output synth/expressions.jsonl --levels 0,1
    python synth/enumerate.py --output synth/expressions_v2.jsonl --levels 0,1,2 \
        --multi-type --expanded-library --stats synth/enum_stats_v2.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Expression templates
# ---------------------------------------------------------------------------

# Predicates for filter / takeWhile / dropWhile  (Int → Bool via decide)
PREDICATES = [
    ("gt0",  "(fun x => decide (x > 0))"),
    ("lt0",  "(fun x => decide (x < 0))"),
    ("ge0",  "(fun x => decide (x ≥ 0))"),
    ("le0",  "(fun x => decide (x ≤ 0))"),
    ("even", "(fun x => decide (x % 2 = 0))"),
    ("odd",  "(fun x => decide (x % 2 ≠ 0))"),
    ("ne0",  "(fun x => decide (x ≠ 0))"),
    ("gt1",  "(fun x => decide (x > 1))"),
    ("lt1",  "(fun x => decide (x < -1))"),
    ("gt2",  "(fun x => decide (x > 2))"),
    ("lt5",  "(fun x => decide (x < 5))"),
    ("ge2",  "(fun x => decide (x ≥ 2))"),
]

# Transformers for map  (Int → Int)
TRANSFORMS = [
    ("times2",  "(fun x => x * 2)"),
    ("times3",  "(fun x => x * 3)"),
    ("square",  "(fun x => x * x)"),
    ("plus1",   "(fun x => x + 1)"),
    ("minus1",  "(fun x => x - 1)"),
    ("plus2",   "(fun x => x + 2)"),
    ("negate",  "(fun x => -x)"),
    ("natAbs",  "(fun x => x.natAbs)"),   # Int → Nat
    ("plus3",   "(fun x => x + 3)"),
    ("times4",  "(fun x => x * 4)"),
]

# Accumulators for foldl  (init, binop)
ACCUMULATORS = [
    ("sum",    "0", "(fun a b => a + b)"),
    ("prod",   "1", "(fun a b => a * b)"),
    ("maxval", "0", "max"),
    ("minval", "0", "min"),
]

# Scan accumulators for scanl (extended in Sprint 14)
SCAN_ACCUMULATORS = [
    ("scanSum", "0", "(fun a b => a + b)"),
    ("scanMax", "0", "max"),
    ("scanMin", "0", "min"),
]

# Binary ops for zipWith
ZIPWITH_OPS = [
    ("add", "(fun a b => a + b)"),
    ("mul", "(fun a b => a * b)"),
    ("sub", "(fun a b => a - b)"),
    ("max", "max"),
]

# Nat-safe transforms (no subtraction/negation)
NAT_TRANSFORMS = [
    ("times2", "(fun x => x * 2)"),
    ("times3", "(fun x => x * 3)"),
    ("square", "(fun x => x * x)"),
    ("plus1",  "(fun x => x + 1)"),
    ("plus2",  "(fun x => x + 2)"),
]

# Nat-safe predicates
NAT_PREDICATES = [
    ("gt0",  "(fun x => decide (x > 0))"),
    ("even", "(fun x => decide (x % 2 = 0))"),
    ("odd",  "(fun x => decide (x % 2 ≠ 0))"),
    ("gt1",  "(fun x => decide (x > 1))"),
    ("gt2",  "(fun x => decide (x > 2))"),
]

# Nat-safe accumulators
NAT_ACCUMULATORS = [
    ("sum",    "0", "(fun a b => a + b)"),
    ("prod",   "1", "(fun a b => a * b)"),
    ("maxval", "0", "max"),
]


# ---------------------------------------------------------------------------
# Input type configuration (Sprint 14)
# ---------------------------------------------------------------------------

INPUT_CONFIGS = {
    "List Int": {
        "param_sig": "(xs : List Int)",
        "test_inputs": [
            "[3, -1, 4, -2, 0]",
            "[1, 2, 3]",
            "[-5, -3, -1]",
            "[2, 4, -1, 3, -2, 5, 0]",
            "[1, 1, 2, 2, 3]",
            "[0, 5, -3, 7, -6, 2]",
            "[10, -10, 5, -5, 0, 1]",
        ],
    },
    "Nat": {
        "param_sig": "(n : Nat)",
        "test_inputs": ["0", "1", "3", "5", "7"],
    },
    "List (List Int)": {
        "param_sig": "(xss : List (List Int))",
        "test_inputs": [
            "[[1, 2], [3], [4, 5]]",
            "[[], [1], []]",
            "[[3, -1, 4]]",
            "[[-2, 0], [1, 2, 3]]",
            "[[1, 1], [2, 2], [3]]",
        ],
    },
    "List Int, List Int": {
        "param_sig": "(xs : List Int) (ys : List Int)",
        "test_inputs": [
            "[1, 2, 3] [4, 5, 6]",
            "[] []",
            "[1, 2] [3]",
            "[3, -1, 4] [-2, 0, 5]",
            "[1, 1, 2] [2, 3, 3]",
        ],
    },
}

# Backward compat: default test inputs for List Int
TEST_INPUTS = INPUT_CONFIGS["List Int"]["test_inputs"]


def _get_param_sig(input_type: str) -> str:
    """Get Lean parameter signature for an input type."""
    cfg = INPUT_CONFIGS.get(input_type)
    if cfg:
        return cfg["param_sig"]
    return f"(xs : {input_type})"


def _get_test_inputs(input_type: str) -> list[str]:
    """Get test inputs for an input type."""
    cfg = INPUT_CONFIGS.get(input_type)
    if cfg:
        return cfg["test_inputs"]
    return TEST_INPUTS


def _param_sig_to_state(param_sig: str, return_type: str) -> str:
    """Convert Lean param_sig to MCTS tactic state string."""
    parts = []
    for match in re.finditer(r'\((\w+)\s*:\s*([^)]+)\)', param_sig):
        name, typ = match.group(1), match.group(2).strip()
        parts.append(f"{name} : {typ}")
    return "\n".join(parts) + f"\n⊢ {return_type}"


@dataclass
class ExprCandidate:
    """A candidate expression before validation."""
    tag: str                 # human-readable id: "filter_gt0", "map_times2_foldl_sum"
    expr: str                # Lean expression (without `exact `)
    return_type: str         # "List Int", "Nat", "Int", "List Nat"
    input_type: str = "List Int"
    level: int = 0           # 0 = primitive, 1 = two-step, 2 = three-step

    @property
    def param_sig(self) -> str:
        return _get_param_sig(self.input_type)


@dataclass
class ValidatedExpr:
    """An expression that compiled and produced non-degenerate outputs."""
    tag: str
    expr: str
    tactic: str              # "exact <expr>"
    return_type: str
    input_type: str
    level: int
    outputs: dict            # test_input → eval_result


# ---------------------------------------------------------------------------
# Level 0: single library function applications (List Int input)
# ---------------------------------------------------------------------------

def enumerate_level0() -> list[ExprCandidate]:
    """Generate Level 0 primitives for List Int input (~60 expressions)."""
    cands: list[ExprCandidate] = []

    # --- Nullary (no extra args) ---
    for tag, expr, ret in [
        ("length",     "xs.length",     "Nat"),
        ("reverse",    "xs.reverse",    "List Int"),
        ("eraseDups",  "xs.eraseDups",  "List Int"),
        ("head_q",     "xs.head?",      "Option Int"),
        ("getLast_q",  "xs.getLast?",   "Option Int"),
    ]:
        cands.append(ExprCandidate(tag=tag, expr=expr, return_type=ret))

    # --- filter ---
    for ptag, pred in PREDICATES:
        tag = f"filter_{ptag}"
        expr = f"xs.filter {pred}"
        cands.append(ExprCandidate(tag=tag, expr=expr, return_type="List Int"))

    # --- map (Int → Int) ---
    for ttag, trans in TRANSFORMS:
        if ttag == "natAbs":
            tag = f"map_{ttag}"
            expr = f"xs.map {trans}"
            cands.append(ExprCandidate(tag=tag, expr=expr, return_type="List Nat"))
        else:
            tag = f"map_{ttag}"
            expr = f"xs.map {trans}"
            cands.append(ExprCandidate(tag=tag, expr=expr, return_type="List Int"))

    # --- foldl ---
    for atag, init, binop in ACCUMULATORS:
        tag = f"foldl_{atag}"
        expr = f"xs.foldl {binop} {init}"
        cands.append(ExprCandidate(tag=tag, expr=expr, return_type="Int"))

    # --- takeWhile ---
    for ptag, pred in PREDICATES:
        tag = f"takeWhile_{ptag}"
        expr = f"xs.takeWhile {pred}"
        cands.append(ExprCandidate(tag=tag, expr=expr, return_type="List Int"))

    # --- dropWhile ---
    for ptag, pred in PREDICATES:
        tag = f"dropWhile_{ptag}"
        expr = f"xs.dropWhile {pred}"
        cands.append(ExprCandidate(tag=tag, expr=expr, return_type="List Int"))

    # --- take / drop ---
    for n in [1, 2, 3]:
        cands.append(ExprCandidate(tag=f"take{n}", expr=f"xs.take {n}",
                                   return_type="List Int"))
        cands.append(ExprCandidate(tag=f"drop{n}", expr=f"xs.drop {n}",
                                   return_type="List Int"))

    # --- sort ---
    cands.append(ExprCandidate(tag="mergeSort", expr="List.mergeSort xs",
                               return_type="List Int"))

    # --- scanl ---
    for stag, init, binop in SCAN_ACCUMULATORS:
        tag = f"scanl_{stag}"
        expr = f"(List.scanl {binop} {init} xs).drop 1"
        cands.append(ExprCandidate(tag=tag, expr=expr, return_type="List Int"))
        tag2 = f"scanl_{stag}_raw"
        expr2 = f"List.scanl {binop} {init} xs"
        cands.append(ExprCandidate(tag=tag2, expr=expr2, return_type="List Int"))

    return cands


# ---------------------------------------------------------------------------
# Level 0: expanded library functions (List Int, Sprint 14)
# ---------------------------------------------------------------------------

def enumerate_library_level0() -> list[ExprCandidate]:
    """Generate Level 0 expressions using new library functions (Sprint 14)."""
    cands: list[ExprCandidate] = []

    # --- tail / dropLast ---
    cands.append(ExprCandidate(tag="tail", expr="xs.tail", return_type="List Int"))
    cands.append(ExprCandidate(tag="dropLast", expr="xs.dropLast", return_type="List Int"))

    # --- intersperse ---
    for val in [0, 1, -1]:
        val_s = f"({val})" if val < 0 else str(val)
        cands.append(ExprCandidate(
            tag=f"intersperse_{val}", expr=f"xs.intersperse {val_s}",
            return_type="List Int"))

    # --- partition → .1 / .2 ---
    for ptag, pred in PREDICATES:
        cands.append(ExprCandidate(
            tag=f"partition_{ptag}_fst", expr=f"(xs.partition {pred}).1",
            return_type="List Int"))
        cands.append(ExprCandidate(
            tag=f"partition_{ptag}_snd", expr=f"(xs.partition {pred}).2",
            return_type="List Int"))

    # --- partition (full product return) ---
    for ptag, pred in PREDICATES[:5]:  # top 5 predicates
        cands.append(ExprCandidate(
            tag=f"partition_{ptag}",
            expr=f"xs.partition {pred}",
            return_type="List Int × List Int"))

    # --- filterMap (Int → Option Nat): keep positives as Nat ---
    cands.append(ExprCandidate(
        tag="filterMap_gt0_toNat",
        expr="xs.filterMap (fun x => if decide (x > 0) then some x.toNat else none)",
        return_type="List Nat"))
    cands.append(ExprCandidate(
        tag="filterMap_ge0_toNat",
        expr="xs.filterMap (fun x => if decide (x ≥ 0) then some x.toNat else none)",
        return_type="List Nat"))
    cands.append(ExprCandidate(
        tag="filterMap_ne0_toNat",
        expr="xs.filterMap (fun x => if decide (x ≠ 0) then some x.toNat else none)",
        return_type="List Nat"))

    # --- zipIdx (enum replacement): value × index ---
    # zipIdx returns List (Int × Nat) — (value, index)
    # Useful compositions: filter by index parity, then map Prod.fst
    cands.append(ExprCandidate(
        tag="zipIdx_even_fst",
        expr="(xs.zipIdx.filter (fun (_, i) => decide (i % 2 = 0))).map Prod.fst",
        return_type="List Int", level=1))
    cands.append(ExprCandidate(
        tag="zipIdx_odd_fst",
        expr="(xs.zipIdx.filter (fun (_, i) => decide (i % 2 ≠ 0))).map Prod.fst",
        return_type="List Int", level=1))
    # Filter by index < k, extract values
    for k in [2, 3, 5]:
        cands.append(ExprCandidate(
            tag=f"zipIdx_lt{k}_fst",
            expr=f"(xs.zipIdx.filter (fun (_, i) => decide (i < {k}))).map Prod.fst",
            return_type="List Int", level=1))

    return cands


# ---------------------------------------------------------------------------
# Level 0: Nat input type (Sprint 14)
# ---------------------------------------------------------------------------

def enumerate_nat_level0() -> list[ExprCandidate]:
    """Generate Level 0 expressions for Nat input."""
    cands: list[ExprCandidate] = []
    IT = "Nat"

    # --- List.range ---
    cands.append(ExprCandidate(
        tag="range", expr="List.range n",
        return_type="List Nat", input_type=IT))

    # --- List.replicate ---
    for val, ret in [(0, "List Nat"), (1, "List Nat"), ("(1 : Int)", "List Int"),
                     ("(0 : Int)", "List Int"), ("(-1 : Int)", "List Int")]:
        v_tag = str(val).replace("(", "").replace(")", "").replace(" : ", "_").replace("-", "neg")
        cands.append(ExprCandidate(
            tag=f"replicate_{v_tag}", expr=f"List.replicate n {val}",
            return_type=ret, input_type=IT))

    return cands


# ---------------------------------------------------------------------------
# Level 0: List (List Int) input type (Sprint 14)
# ---------------------------------------------------------------------------

def enumerate_nested_level0() -> list[ExprCandidate]:
    """Generate Level 0 expressions for List (List Int) input."""
    cands: list[ExprCandidate] = []
    IT = "List (List Int)"

    cands.append(ExprCandidate(
        tag="flatten", expr="xss.flatten",
        return_type="List Int", input_type=IT))
    cands.append(ExprCandidate(
        tag="nested_length", expr="xss.length",
        return_type="Nat", input_type=IT))
    cands.append(ExprCandidate(
        tag="map_length", expr="xss.map List.length",
        return_type="List Nat", input_type=IT))
    cands.append(ExprCandidate(
        tag="map_reverse", expr="xss.map List.reverse",
        return_type="List (List Int)", input_type=IT))
    cands.append(ExprCandidate(
        tag="nested_reverse", expr="xss.reverse",
        return_type="List (List Int)", input_type=IT))

    # map + filter sublists
    for ptag, pred in PREDICATES[:5]:  # top 5 predicates
        cands.append(ExprCandidate(
            tag=f"map_filter_{ptag}",
            expr=f"xss.map (fun l => l.filter {pred})",
            return_type="List (List Int)", input_type=IT))

    # map + sort sublists
    cands.append(ExprCandidate(
        tag="map_sort",
        expr="xss.map List.mergeSort",
        return_type="List (List Int)", input_type=IT))

    return cands


# ---------------------------------------------------------------------------
# Level 0: two-arg input (xs ys : List Int) (Sprint 14)
# ---------------------------------------------------------------------------

def enumerate_twoarg_level0() -> list[ExprCandidate]:
    """Generate Level 0 expressions for two-arg input."""
    cands: list[ExprCandidate] = []
    IT = "List Int, List Int"

    # --- zipWith ---
    for otag, op in ZIPWITH_OPS:
        cands.append(ExprCandidate(
            tag=f"zipWith_{otag}", expr=f"List.zipWith {op} xs ys",
            return_type="List Int", input_type=IT))

    # --- append ---
    cands.append(ExprCandidate(
        tag="append", expr="xs ++ ys",
        return_type="List Int", input_type=IT))

    return cands


# ---------------------------------------------------------------------------
# Level 1: two-step compositions (List Int input, existing)
# ---------------------------------------------------------------------------

def enumerate_level1() -> list[ExprCandidate]:
    """Generate Level 1 two-step compositions (~500-700 expressions)."""
    cands: list[ExprCandidate] = []

    def _add(tag, expr, ret):
        cands.append(ExprCandidate(tag=tag, expr=expr, return_type=ret, level=1))

    # --- filter + length ---
    for ptag, pred in PREDICATES:
        _add(f"filter_{ptag}_length", f"(xs.filter {pred}).length", "Nat")

    # --- filter + foldl ---
    for ptag, pred in PREDICATES:
        for atag, init, binop in ACCUMULATORS:
            _add(f"filter_{ptag}_foldl_{atag}", f"(xs.filter {pred}).foldl {binop} {init}", "Int")

    # --- map + length ---
    for ttag, trans in TRANSFORMS:
        _add(f"map_{ttag}_length", f"(xs.map {trans}).length", "Nat")

    # --- map + foldl ---
    for ttag, trans in TRANSFORMS:
        for atag, init, binop in ACCUMULATORS:
            ret = "Nat" if ttag == "natAbs" else "Int"
            _add(f"map_{ttag}_foldl_{atag}", f"(xs.map {trans}).foldl {binop} {init}", ret)

    # --- filter + map ---
    for ptag, pred in PREDICATES:
        for ttag, trans in TRANSFORMS:
            ret = "List Nat" if ttag == "natAbs" else "List Int"
            _add(f"filter_{ptag}_map_{ttag}", f"(xs.filter {pred}).map {trans}", ret)

    # --- map + filter  (Int→Int maps only; filter on result) ---
    for ttag, trans in TRANSFORMS:
        if ttag == "natAbs":
            continue
        for ptag, pred in PREDICATES:
            _add(f"map_{ttag}_filter_{ptag}", f"(xs.map {trans}).filter {pred}", "List Int")

    # --- sort + reverse ---
    _add("mergeSort_reverse", "(List.mergeSort xs).reverse", "List Int")

    # --- eraseDups + sort / sort + eraseDups ---
    _add("eraseDups_mergeSort", "List.mergeSort (xs.eraseDups)", "List Int")
    _add("mergeSort_eraseDups", "(List.mergeSort xs).eraseDups", "List Int")

    # --- sort + filter / filter + sort ---
    for ptag, pred in PREDICATES:
        _add(f"mergeSort_filter_{ptag}", f"(List.mergeSort xs).filter {pred}", "List Int")
        _add(f"filter_{ptag}_mergeSort", f"List.mergeSort (xs.filter {pred})", "List Int")

    # --- reverse + takeWhile/dropWhile ---
    for ptag, pred in PREDICATES:
        _add(f"reverse_takeWhile_{ptag}", f"xs.reverse.takeWhile {pred}", "List Int")
        _add(f"reverse_dropWhile_{ptag}", f"xs.reverse.dropWhile {pred}", "List Int")

    # --- takeWhile/dropWhile + length ---
    for ptag, pred in PREDICATES:
        _add(f"takeWhile_{ptag}_length", f"(xs.takeWhile {pred}).length", "Nat")
        _add(f"dropWhile_{ptag}_length", f"(xs.dropWhile {pred}).length", "Nat")

    # --- takeWhile/dropWhile + foldl ---
    for ptag, pred in PREDICATES:
        for atag, init, binop in ACCUMULATORS:
            _add(f"takeWhile_{ptag}_foldl_{atag}",
                 f"(xs.takeWhile {pred}).foldl {binop} {init}", "Int")
            _add(f"dropWhile_{ptag}_foldl_{atag}",
                 f"(xs.dropWhile {pred}).foldl {binop} {init}", "Int")

    # --- takeWhile/dropWhile + reverse ---
    for ptag, pred in PREDICATES:
        _add(f"takeWhile_{ptag}_reverse", f"(xs.takeWhile {pred}).reverse", "List Int")
        _add(f"dropWhile_{ptag}_reverse", f"(xs.dropWhile {pred}).reverse", "List Int")

    # --- takeWhile/dropWhile + map ---
    for ptag, pred in PREDICATES:
        for ttag, trans in TRANSFORMS:
            if ttag == "natAbs":
                ret = "List Nat"
            else:
                ret = "List Int"
            _add(f"takeWhile_{ptag}_map_{ttag}",
                 f"(xs.takeWhile {pred}).map {trans}", ret)
            _add(f"dropWhile_{ptag}_map_{ttag}",
                 f"(xs.dropWhile {pred}).map {trans}", ret)

    # --- filter + eraseDups / reverse ---
    for ptag, pred in PREDICATES:
        _add(f"filter_{ptag}_eraseDups", f"(xs.filter {pred}).eraseDups", "List Int")
        _add(f"filter_{ptag}_reverse", f"(xs.filter {pred}).reverse", "List Int")

    # --- eraseDups + length / foldl / map / filter ---
    _add("eraseDups_length", "xs.eraseDups.length", "Nat")
    for atag, init, binop in ACCUMULATORS:
        _add(f"eraseDups_foldl_{atag}", f"xs.eraseDups.foldl {binop} {init}", "Int")
    for ttag, trans in TRANSFORMS:
        ret = "List Nat" if ttag == "natAbs" else "List Int"
        _add(f"eraseDups_map_{ttag}", f"xs.eraseDups.map {trans}", ret)
    for ptag, pred in PREDICATES:
        _add(f"eraseDups_filter_{ptag}", f"xs.eraseDups.filter {pred}", "List Int")

    # --- reverse + map / filter / foldl ---
    for ttag, trans in TRANSFORMS:
        ret = "List Nat" if ttag == "natAbs" else "List Int"
        _add(f"reverse_map_{ttag}", f"xs.reverse.map {trans}", ret)
    for ptag, pred in PREDICATES:
        _add(f"reverse_filter_{ptag}", f"xs.reverse.filter {pred}", "List Int")
    for atag, init, binop in ACCUMULATORS:
        _add(f"reverse_foldl_{atag}", f"xs.reverse.foldl {binop} {init}", "Int")

    # --- map + eraseDups / reverse / sort ---
    for ttag, trans in TRANSFORMS:
        ret_l = "List Nat" if ttag == "natAbs" else "List Int"
        _add(f"map_{ttag}_eraseDups", f"(xs.map {trans}).eraseDups", ret_l)
        _add(f"map_{ttag}_reverse", f"(xs.map {trans}).reverse", ret_l)
        _add(f"map_{ttag}_mergeSort", f"List.mergeSort (xs.map {trans})", ret_l)

    # --- scanl + filter / map / length / reverse ---
    for stag, init, binop in SCAN_ACCUMULATORS:
        scan_expr = f"(List.scanl {binop} {init} xs).drop 1"
        for ptag, pred in PREDICATES:
            _add(f"scanl_{stag}_filter_{ptag}",
                 f"({scan_expr}).filter {pred}", "List Int")
        _add(f"scanl_{stag}_length", f"({scan_expr}).length", "Nat")
        _add(f"scanl_{stag}_reverse", f"({scan_expr}).reverse", "List Int")

    # --- filter + takeWhile/dropWhile ---
    for ptag, pred in PREDICATES:
        for ptag2, pred2 in PREDICATES:
            if ptag == ptag2:
                continue  # filter then takeWhile with same pred is degenerate
            _add(f"filter_{ptag}_takeWhile_{ptag2}",
                 f"(xs.filter {pred}).takeWhile {pred2}", "List Int")
            _add(f"filter_{ptag}_dropWhile_{ptag2}",
                 f"(xs.filter {pred}).dropWhile {pred2}", "List Int")

    # --- eraseDups + reverse / takeWhile / dropWhile ---
    _add("eraseDups_reverse", "xs.eraseDups.reverse", "List Int")
    for ptag, pred in PREDICATES:
        _add(f"eraseDups_takeWhile_{ptag}", f"xs.eraseDups.takeWhile {pred}", "List Int")
        _add(f"eraseDups_dropWhile_{ptag}", f"xs.eraseDups.dropWhile {pred}", "List Int")

    # --- sort + takeWhile/dropWhile ---
    for ptag, pred in PREDICATES:
        _add(f"mergeSort_takeWhile_{ptag}",
             f"(List.mergeSort xs).takeWhile {pred}", "List Int")
        _add(f"mergeSort_dropWhile_{ptag}",
             f"(List.mergeSort xs).dropWhile {pred}", "List Int")

    # --- take/drop + filter / map / foldl ---
    for n in [1, 2, 3]:
        for ptag, pred in PREDICATES:
            _add(f"filter_{ptag}_take{n}", f"(xs.filter {pred}).take {n}", "List Int")
        for ttag, trans in TRANSFORMS:
            if ttag == "natAbs":
                continue
            _add(f"take{n}_map_{ttag}", f"(xs.take {n}).map {trans}", "List Int")
            _add(f"drop{n}_map_{ttag}", f"(xs.drop {n}).map {trans}", "List Int")
        for atag, init, binop in ACCUMULATORS:
            _add(f"take{n}_foldl_{atag}", f"(xs.take {n}).foldl {binop} {init}", "Int")
            _add(f"drop{n}_foldl_{atag}", f"(xs.drop {n}).foldl {binop} {init}", "Int")

    return cands


# ---------------------------------------------------------------------------
# Level 1: compositions with expanded library functions (Sprint 14)
# ---------------------------------------------------------------------------

def enumerate_level1_extended() -> list[ExprCandidate]:
    """Level 1 compositions involving new library functions on List Int."""
    cands: list[ExprCandidate] = []

    def _add(tag, expr, ret):
        cands.append(ExprCandidate(tag=tag, expr=expr, return_type=ret, level=1))

    # --- tail → operations ---
    for ptag, pred in PREDICATES:
        _add(f"tail_filter_{ptag}", f"xs.tail.filter {pred}", "List Int")
    for ttag, trans in TRANSFORMS:
        if ttag == "natAbs":
            continue
        _add(f"tail_map_{ttag}", f"xs.tail.map {trans}", "List Int")
    for atag, init, binop in ACCUMULATORS:
        _add(f"tail_foldl_{atag}", f"xs.tail.foldl {binop} {init}", "Int")
    _add("tail_length", "xs.tail.length", "Nat")
    _add("tail_reverse", "xs.tail.reverse", "List Int")
    _add("tail_mergeSort", "List.mergeSort xs.tail", "List Int")
    _add("tail_eraseDups", "xs.tail.eraseDups", "List Int")

    # --- dropLast → operations ---
    for ptag, pred in PREDICATES:
        _add(f"dropLast_filter_{ptag}", f"xs.dropLast.filter {pred}", "List Int")
    for ttag, trans in TRANSFORMS:
        if ttag == "natAbs":
            continue
        _add(f"dropLast_map_{ttag}", f"xs.dropLast.map {trans}", "List Int")
    for atag, init, binop in ACCUMULATORS:
        _add(f"dropLast_foldl_{atag}", f"xs.dropLast.foldl {binop} {init}", "Int")
    _add("dropLast_length", "xs.dropLast.length", "Nat")
    _add("dropLast_reverse", "xs.dropLast.reverse", "List Int")

    # --- operations → tail / dropLast ---
    for ptag, pred in PREDICATES:
        _add(f"filter_{ptag}_tail", f"(xs.filter {pred}).tail", "List Int")
        _add(f"filter_{ptag}_dropLast", f"(xs.filter {pred}).dropLast", "List Int")
    _add("mergeSort_tail", "(List.mergeSort xs).tail", "List Int")
    _add("mergeSort_dropLast", "(List.mergeSort xs).dropLast", "List Int")
    _add("eraseDups_tail", "xs.eraseDups.tail", "List Int")
    _add("eraseDups_dropLast", "xs.eraseDups.dropLast", "List Int")

    # --- scanl max/min + operations ---
    for stag in ["scanMax", "scanMin"]:
        init_s = "0"
        binop_s = "max" if stag == "scanMax" else "min"
        scan_expr = f"(List.scanl {binop_s} {init_s} xs).drop 1"
        for atag, init, binop in ACCUMULATORS:
            _add(f"scanl_{stag}_foldl_{atag}",
                 f"({scan_expr}).foldl {binop} {init}", "Int")
        _add(f"scanl_{stag}_eraseDups", f"({scan_expr}).eraseDups", "List Int")

    # --- partition.1 / .2 + fold ---
    for ptag, pred in PREDICATES[:5]:  # top 5 to avoid explosion
        for atag, init, binop in ACCUMULATORS:
            _add(f"partition_{ptag}_fst_foldl_{atag}",
                 f"(xs.partition {pred}).1.foldl {binop} {init}", "Int")
        _add(f"partition_{ptag}_fst_length",
             f"(xs.partition {pred}).1.length", "Nat")
        _add(f"partition_{ptag}_snd_length",
             f"(xs.partition {pred}).2.length", "Nat")

    # --- filterMap + operations ---
    fm_expr = "xs.filterMap (fun x => if decide (x > 0) then some x.toNat else none)"
    _add("filterMap_gt0_length", f"({fm_expr}).length", "Nat")
    for atag, init, binop in NAT_ACCUMULATORS:
        _add(f"filterMap_gt0_foldl_{atag}",
             f"({fm_expr}).foldl {binop} {init}", "Nat")

    return cands


# ---------------------------------------------------------------------------
# Level 1: Nat input compositions (Sprint 14)
# ---------------------------------------------------------------------------

def enumerate_level1_nat() -> list[ExprCandidate]:
    """Level 1 compositions on Nat input (range → ops)."""
    cands: list[ExprCandidate] = []
    IT = "Nat"

    def _add(tag, expr, ret):
        cands.append(ExprCandidate(tag=tag, expr=expr, return_type=ret,
                                   input_type=IT, level=1))

    range_expr = "List.range n"

    # range → map
    for ttag, trans in NAT_TRANSFORMS:
        _add(f"range_map_{ttag}", f"({range_expr}).map {trans}", "List Nat")

    # range → filter
    for ptag, pred in NAT_PREDICATES:
        _add(f"range_filter_{ptag}", f"({range_expr}).filter {pred}", "List Nat")

    # range → fold
    for atag, init, binop in NAT_ACCUMULATORS:
        _add(f"range_foldl_{atag}", f"({range_expr}).foldl {binop} {init}", "Nat")

    # range → reverse / length / take / drop / sort / eraseDups
    _add("range_reverse", f"({range_expr}).reverse", "List Nat")
    _add("range_length", f"({range_expr}).length", "Nat")
    _add("range_eraseDups", f"({range_expr}).eraseDups", "List Nat")
    for k in [1, 2, 3]:
        _add(f"range_take{k}", f"({range_expr}).take {k}", "List Nat")
        _add(f"range_drop{k}", f"({range_expr}).drop {k}", "List Nat")

    # range → map → filter (cross-compositions)
    for ttag, trans in NAT_TRANSFORMS[:3]:
        for ptag, pred in NAT_PREDICATES[:3]:
            _add(f"range_map_{ttag}_filter_{ptag}",
                 f"(({range_expr}).map {trans}).filter {pred}", "List Nat")

    # range → filter → map
    for ptag, pred in NAT_PREDICATES[:3]:
        for ttag, trans in NAT_TRANSFORMS[:3]:
            _add(f"range_filter_{ptag}_map_{ttag}",
                 f"(({range_expr}).filter {pred}).map {trans}", "List Nat")

    return cands


# ---------------------------------------------------------------------------
# Level 1: List (List Int) input compositions (Sprint 14)
# ---------------------------------------------------------------------------

def enumerate_level1_nested() -> list[ExprCandidate]:
    """Level 1 compositions on List (List Int) input (flatten → ops)."""
    cands: list[ExprCandidate] = []
    IT = "List (List Int)"

    def _add(tag, expr, ret):
        cands.append(ExprCandidate(tag=tag, expr=expr, return_type=ret,
                                   input_type=IT, level=1))

    flat = "xss.flatten"

    # flatten → filter
    for ptag, pred in PREDICATES:
        _add(f"flatten_filter_{ptag}", f"({flat}).filter {pred}", "List Int")

    # flatten → map
    for ttag, trans in TRANSFORMS:
        if ttag == "natAbs":
            continue
        _add(f"flatten_map_{ttag}", f"({flat}).map {trans}", "List Int")

    # flatten → fold
    for atag, init, binop in ACCUMULATORS:
        _add(f"flatten_foldl_{atag}", f"({flat}).foldl {binop} {init}", "Int")

    # flatten → sort / reverse / eraseDups / length
    _add("flatten_mergeSort", f"List.mergeSort ({flat})", "List Int")
    _add("flatten_reverse", f"({flat}).reverse", "List Int")
    _add("flatten_eraseDups", f"({flat}).eraseDups", "List Int")
    _add("flatten_length", f"({flat}).length", "Nat")

    # flatten → takeWhile / dropWhile (top 3 predicates)
    for ptag, pred in PREDICATES[:3]:
        _add(f"flatten_takeWhile_{ptag}", f"({flat}).takeWhile {pred}", "List Int")
        _add(f"flatten_dropWhile_{ptag}", f"({flat}).dropWhile {pred}", "List Int")

    # map_length → fold
    ml = "xss.map List.length"
    for atag, init, binop in NAT_ACCUMULATORS:
        _add(f"map_length_foldl_{atag}", f"({ml}).foldl {binop} {init}", "Nat")

    # flatten → map → fold
    for ttag, trans in TRANSFORMS[:4]:
        if ttag == "natAbs":
            continue
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"flatten_map_{ttag}_foldl_{atag}",
                 f"(({flat}).map {trans}).foldl {binop} {init}", "Int")

    # flatten → filter → length
    for ptag, pred in PREDICATES[:5]:
        _add(f"flatten_filter_{ptag}_length",
             f"(({flat}).filter {pred}).length", "Nat")

    # flatten → eraseDups → fold
    for atag, init, binop in ACCUMULATORS:
        _add(f"flatten_eraseDups_foldl_{atag}",
             f"({flat}).eraseDups.foldl {binop} {init}", "Int")

    return cands


# ---------------------------------------------------------------------------
# Level 1: two-arg input compositions (Sprint 14)
# ---------------------------------------------------------------------------

def enumerate_level1_twoarg() -> list[ExprCandidate]:
    """Level 1 compositions on two-arg input."""
    cands: list[ExprCandidate] = []
    IT = "List Int, List Int"

    def _add(tag, expr, ret):
        cands.append(ExprCandidate(tag=tag, expr=expr, return_type=ret,
                                   input_type=IT, level=1))

    # --- zipWith + operations ---
    for otag, op in ZIPWITH_OPS:
        zw = f"List.zipWith {op} xs ys"
        # zipWith → fold
        for atag, init, binop in ACCUMULATORS:
            _add(f"zipWith_{otag}_foldl_{atag}",
                 f"({zw}).foldl {binop} {init}", "Int")
        # zipWith → filter (top 5 predicates)
        for ptag, pred in PREDICATES[:5]:
            _add(f"zipWith_{otag}_filter_{ptag}",
                 f"({zw}).filter {pred}", "List Int")
        # zipWith → map (top 4 transforms)
        for ttag, trans in TRANSFORMS[:4]:
            _add(f"zipWith_{otag}_map_{ttag}",
                 f"({zw}).map {trans}", "List Int")
        # zipWith → length / reverse / sort
        _add(f"zipWith_{otag}_length", f"({zw}).length", "Nat")
        _add(f"zipWith_{otag}_reverse", f"({zw}).reverse", "List Int")
        _add(f"zipWith_{otag}_mergeSort", f"List.mergeSort ({zw})", "List Int")

    # --- append + operations ---
    app = "xs ++ ys"
    for ptag, pred in PREDICATES[:5]:
        _add(f"append_filter_{ptag}", f"({app}).filter {pred}", "List Int")
    for atag, init, binop in ACCUMULATORS:
        _add(f"append_foldl_{atag}", f"({app}).foldl {binop} {init}", "Int")
    _add("append_length", f"({app}).length", "Nat")
    _add("append_reverse", f"({app}).reverse", "List Int")
    _add("append_mergeSort", f"List.mergeSort ({app})", "List Int")
    _add("append_eraseDups", f"({app}).eraseDups", "List Int")
    for ttag, trans in TRANSFORMS[:4]:
        _add(f"append_map_{ttag}", f"({app}).map {trans}", "List Int")

    return cands


# ---------------------------------------------------------------------------
# Level 2: three-step compositions (Sprint 14, capped at ~500)
# ---------------------------------------------------------------------------

def enumerate_level2() -> list[ExprCandidate]:
    """Generate Level 2 three-step compositions across all input types."""
    cands: list[ExprCandidate] = []

    def _add(tag, expr, ret, it="List Int"):
        cands.append(ExprCandidate(tag=tag, expr=expr, return_type=ret,
                                   input_type=it, level=2))

    # --- List Int: filter → map → fold ---
    for ptag, pred in PREDICATES[:5]:  # top 5 predicates
        for ttag, trans in TRANSFORMS[:4]:  # top 4 transforms (skip natAbs etc)
            if ttag == "natAbs":
                continue
            for atag, init, binop in ACCUMULATORS[:2]:  # sum, prod only
                _add(f"filter_{ptag}_map_{ttag}_foldl_{atag}",
                     f"((xs.filter {pred}).map {trans}).foldl {binop} {init}", "Int")

    # --- List Int: filter → map → length ---
    for ptag, pred in PREDICATES[:5]:
        for ttag, trans in TRANSFORMS[:4]:
            if ttag == "natAbs":
                continue
            _add(f"filter_{ptag}_map_{ttag}_length",
                 f"((xs.filter {pred}).map {trans}).length", "Nat")

    # --- List Int: map → filter → fold ---
    for ttag, trans in TRANSFORMS[:4]:
        if ttag == "natAbs":
            continue
        for ptag, pred in PREDICATES[:5]:
            for atag, init, binop in ACCUMULATORS[:2]:
                _add(f"map_{ttag}_filter_{ptag}_foldl_{atag}",
                     f"((xs.map {trans}).filter {pred}).foldl {binop} {init}", "Int")

    # --- List Int: filter → eraseDups → fold ---
    for ptag, pred in PREDICATES[:5]:
        for atag, init, binop in ACCUMULATORS:
            _add(f"filter_{ptag}_eraseDups_foldl_{atag}",
                 f"(xs.filter {pred}).eraseDups.foldl {binop} {init}", "Int")

    # --- List Int: filter → eraseDups → length ---
    for ptag, pred in PREDICATES[:5]:
        _add(f"filter_{ptag}_eraseDups_length",
             f"(xs.filter {pred}).eraseDups.length", "Nat")

    # --- List Int: filter → sort → take ---
    for ptag, pred in PREDICATES[:5]:
        for k in [1, 2, 3]:
            _add(f"filter_{ptag}_mergeSort_take{k}",
                 f"(List.mergeSort (xs.filter {pred})).take {k}", "List Int")

    # --- List Int: tail → filter → fold ---
    for ptag, pred in PREDICATES[:3]:
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"tail_filter_{ptag}_foldl_{atag}",
                 f"(xs.tail.filter {pred}).foldl {binop} {init}", "Int")

    # --- List Int: scanl → filter → length ---
    for stag, sinit, sbinop in SCAN_ACCUMULATORS:
        scan = f"(List.scanl {sbinop} {sinit} xs).drop 1"
        for ptag, pred in PREDICATES[:3]:
            _add(f"scanl_{stag}_filter_{ptag}_length",
                 f"(({scan}).filter {pred}).length", "Nat")

    # --- List Int: filter → reverse → fold ---
    for ptag, pred in PREDICATES[:3]:
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"filter_{ptag}_reverse_foldl_{atag}",
                 f"(xs.filter {pred}).reverse.foldl {binop} {init}", "Int")

    # --- List Int: eraseDups → filter → fold ---
    for ptag, pred in PREDICATES[:3]:
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"eraseDups_filter_{ptag}_foldl_{atag}",
                 f"(xs.eraseDups.filter {pred}).foldl {binop} {init}", "Int")

    # --- List Int: eraseDups → sort → filter ---
    for ptag, pred in PREDICATES[:3]:
        _add(f"eraseDups_mergeSort_filter_{ptag}",
             f"(List.mergeSort xs.eraseDups).filter {pred}", "List Int")

    # --- Nat: range → map → fold ---
    for ttag, trans in NAT_TRANSFORMS[:3]:
        for atag, init, binop in NAT_ACCUMULATORS:
            _add(f"range_map_{ttag}_foldl_{atag}",
                 f"((List.range n).map {trans}).foldl {binop} {init}", "Nat",
                 it="Nat")

    # --- Nat: range → filter → fold ---
    for ptag, pred in NAT_PREDICATES[:3]:
        for atag, init, binop in NAT_ACCUMULATORS:
            _add(f"range_filter_{ptag}_foldl_{atag}",
                 f"((List.range n).filter {pred}).foldl {binop} {init}", "Nat",
                 it="Nat")

    # --- Nat: range → filter → length ---
    for ptag, pred in NAT_PREDICATES[:3]:
        _add(f"range_filter_{ptag}_length",
             f"((List.range n).filter {pred}).length", "Nat",
             it="Nat")

    # --- List (List Int): flatten → filter → fold ---
    flat = "xss.flatten"
    for ptag, pred in PREDICATES[:3]:
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"flatten_filter_{ptag}_foldl_{atag}",
                 f"(({flat}).filter {pred}).foldl {binop} {init}", "Int",
                 it="List (List Int)")

    # --- List (List Int): flatten → filter → length ---
    for ptag, pred in PREDICATES[:3]:
        _add(f"flatten_filter_{ptag}_length",
             f"(({flat}).filter {pred}).length", "Nat",
             it="List (List Int)")

    # --- List (List Int): flatten → map → fold ---
    for ttag, trans in TRANSFORMS[:3]:
        if ttag == "natAbs":
            continue
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"flatten_map_{ttag}_foldl_{atag}",
                 f"(({flat}).map {trans}).foldl {binop} {init}", "Int",
                 it="List (List Int)")

    # --- Two-arg: zipWith → filter → fold ---
    for otag, op in ZIPWITH_OPS[:2]:  # add, mul
        zw = f"List.zipWith {op} xs ys"
        for ptag, pred in PREDICATES[:3]:
            for atag, init, binop in ACCUMULATORS[:2]:
                _add(f"zipWith_{otag}_filter_{ptag}_foldl_{atag}",
                     f"(({zw}).filter {pred}).foldl {binop} {init}", "Int",
                     it="List Int, List Int")

    # --- Two-arg: zipWith → map → fold ---
    for otag, op in ZIPWITH_OPS[:2]:
        zw = f"List.zipWith {op} xs ys"
        for ttag, trans in TRANSFORMS[:3]:
            if ttag == "natAbs":
                continue
            for atag, init, binop in ACCUMULATORS[:2]:
                _add(f"zipWith_{otag}_map_{ttag}_foldl_{atag}",
                     f"(({zw}).map {trans}).foldl {binop} {init}", "Int",
                     it="List Int, List Int")

    # --- Two-arg: append → filter → fold ---
    app = "xs ++ ys"
    for ptag, pred in PREDICATES[:3]:
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"append_filter_{ptag}_foldl_{atag}",
                 f"(({app}).filter {pred}).foldl {binop} {init}", "Int",
                 it="List Int, List Int")

    # --- Two-arg: append → eraseDups → sort ---
    _add("append_eraseDups_mergeSort",
         f"List.mergeSort (({app}).eraseDups)", "List Int",
         it="List Int, List Int")

    # --- Two-arg: append → sort → filter ---
    for ptag, pred in PREDICATES[:3]:
        _add(f"append_mergeSort_filter_{ptag}",
             f"(List.mergeSort ({app})).filter {pred}", "List Int",
             it="List Int, List Int")

    # --- List Int: map → sort → take/reverse ---
    for ttag, trans in TRANSFORMS[:4]:
        if ttag == "natAbs":
            continue
        _add(f"map_{ttag}_mergeSort_reverse",
             f"(List.mergeSort (xs.map {trans})).reverse", "List Int")
        for k in [1, 2, 3]:
            _add(f"map_{ttag}_mergeSort_take{k}",
                 f"(List.mergeSort (xs.map {trans})).take {k}", "List Int")

    # --- List Int: filter → map → reverse/sort ---
    for ptag, pred in PREDICATES[:5]:
        for ttag, trans in TRANSFORMS[:3]:
            if ttag == "natAbs":
                continue
            _add(f"filter_{ptag}_map_{ttag}_reverse",
                 f"((xs.filter {pred}).map {trans}).reverse", "List Int")
            _add(f"filter_{ptag}_map_{ttag}_mergeSort",
                 f"List.mergeSort ((xs.filter {pred}).map {trans})", "List Int")

    # --- List Int: map → eraseDups → fold/length ---
    for ttag, trans in TRANSFORMS[:4]:
        if ttag == "natAbs":
            continue
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"map_{ttag}_eraseDups_foldl_{atag}",
                 f"(xs.map {trans}).eraseDups.foldl {binop} {init}", "Int")
        _add(f"map_{ttag}_eraseDups_length",
             f"(xs.map {trans}).eraseDups.length", "Nat")

    # --- List Int: sort → filter → fold ---
    for ptag, pred in PREDICATES[:5]:
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"mergeSort_filter_{ptag}_foldl_{atag}",
                 f"((List.mergeSort xs).filter {pred}).foldl {binop} {init}", "Int")

    # --- List Int: sort → filter → length ---
    for ptag, pred in PREDICATES[:5]:
        _add(f"mergeSort_filter_{ptag}_length",
             f"((List.mergeSort xs).filter {pred}).length", "Nat")

    # --- List Int: reverse → filter → fold ---
    for ptag, pred in PREDICATES[:3]:
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"reverse_filter_{ptag}_foldl_{atag}",
                 f"(xs.reverse.filter {pred}).foldl {binop} {init}", "Int")

    # --- List Int: sort → take → fold ---
    for k in [1, 2, 3]:
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"mergeSort_take{k}_foldl_{atag}",
                 f"((List.mergeSort xs).take {k}).foldl {binop} {init}", "Int")

    # --- List Int: filter → take → fold ---
    for ptag, pred in PREDICATES[:3]:
        for k in [1, 2, 3]:
            for atag, init, binop in ACCUMULATORS[:2]:
                _add(f"filter_{ptag}_take{k}_foldl_{atag}",
                     f"((xs.filter {pred}).take {k}).foldl {binop} {init}", "Int")

    # --- List Int: tail → map → fold ---
    for ttag, trans in TRANSFORMS[:4]:
        if ttag == "natAbs":
            continue
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"tail_map_{ttag}_foldl_{atag}",
                 f"(xs.tail.map {trans}).foldl {binop} {init}", "Int")

    # --- List Int: dropLast → map → fold ---
    for ttag, trans in TRANSFORMS[:4]:
        if ttag == "natAbs":
            continue
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"dropLast_map_{ttag}_foldl_{atag}",
                 f"(xs.dropLast.map {trans}).foldl {binop} {init}", "Int")

    # --- List Int: partition.1 → map → fold ---
    for ptag, pred in PREDICATES[:3]:
        for ttag, trans in TRANSFORMS[:3]:
            if ttag == "natAbs":
                continue
            for atag, init, binop in ACCUMULATORS[:2]:
                _add(f"partition_{ptag}_fst_map_{ttag}_foldl_{atag}",
                     f"((xs.partition {pred}).1.map {trans}).foldl {binop} {init}",
                     "Int")

    # --- List Int: intersperse → filter → fold ---
    for val in [0]:
        val_s = str(val)
        isp = f"xs.intersperse {val_s}"
        for ptag, pred in PREDICATES[:3]:
            for atag, init, binop in ACCUMULATORS[:2]:
                _add(f"intersperse_{val}_filter_{ptag}_foldl_{atag}",
                     f"(({isp}).filter {pred}).foldl {binop} {init}", "Int")

    # --- Nat: range → map → reverse ---
    for ttag, trans in NAT_TRANSFORMS[:3]:
        _add(f"range_map_{ttag}_reverse",
             f"((List.range n).map {trans}).reverse", "List Nat", it="Nat")

    # --- Nat: range → filter → reverse ---
    for ptag, pred in NAT_PREDICATES[:3]:
        _add(f"range_filter_{ptag}_reverse",
             f"((List.range n).filter {pred}).reverse", "List Nat", it="Nat")

    # --- List (List Int): flatten → sort → filter ---
    for ptag, pred in PREDICATES[:3]:
        _add(f"flatten_mergeSort_filter_{ptag}",
             f"(List.mergeSort ({flat})).filter {pred}", "List Int",
             it="List (List Int)")

    # --- List (List Int): flatten → eraseDups → sort ---
    _add("flatten_eraseDups_mergeSort",
         f"List.mergeSort (({flat}).eraseDups)", "List Int",
         it="List (List Int)")

    # --- List (List Int): flatten → eraseDups → length ---
    _add("flatten_eraseDups_length",
         f"({flat}).eraseDups.length", "Nat",
         it="List (List Int)")

    # --- Two-arg: zipWith → filter → length ---
    for otag, op in ZIPWITH_OPS[:2]:
        zw2 = f"List.zipWith {op} xs ys"
        for ptag, pred in PREDICATES[:3]:
            _add(f"zipWith_{otag}_filter_{ptag}_length",
                 f"(({zw2}).filter {pred}).length", "Nat",
                 it="List Int, List Int")

    # --- Two-arg: append → filter → length ---
    for ptag, pred in PREDICATES[:3]:
        _add(f"append_filter_{ptag}_length",
             f"(({app}).filter {pred}).length", "Nat",
             it="List Int, List Int")

    # --- List Int: filter → map → eraseDups ---
    for ptag, pred in PREDICATES[:5]:
        for ttag, trans in TRANSFORMS[:4]:
            if ttag == "natAbs":
                continue
            _add(f"filter_{ptag}_map_{ttag}_eraseDups",
                 f"((xs.filter {pred}).map {trans}).eraseDups", "List Int")

    # --- List Int: map → filter → length ---
    for ttag, trans in TRANSFORMS[:5]:
        if ttag == "natAbs":
            continue
        for ptag, pred in PREDICATES[:5]:
            _add(f"map_{ttag}_filter_{ptag}_length",
                 f"((xs.map {trans}).filter {pred}).length", "Nat")

    # --- List Int: map → filter → reverse ---
    for ttag, trans in TRANSFORMS[:4]:
        if ttag == "natAbs":
            continue
        for ptag, pred in PREDICATES[:3]:
            _add(f"map_{ttag}_filter_{ptag}_reverse",
                 f"((xs.map {trans}).filter {pred}).reverse", "List Int")

    # --- List Int: eraseDups → sort → take ---
    for k in [1, 2, 3]:
        _add(f"eraseDups_mergeSort_take{k}",
             f"(List.mergeSort xs.eraseDups).take {k}", "List Int")

    # --- List Int: reverse → take → fold ---
    for k in [1, 2, 3]:
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"reverse_take{k}_foldl_{atag}",
                 f"(xs.reverse.take {k}).foldl {binop} {init}", "Int")

    # --- List Int: reverse → drop → fold ---
    for k in [1, 2, 3]:
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"reverse_drop{k}_foldl_{atag}",
                 f"(xs.reverse.drop {k}).foldl {binop} {init}", "Int")

    # --- List Int: dropLast → filter → fold ---
    for ptag, pred in PREDICATES[:3]:
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"dropLast_filter_{ptag}_foldl_{atag}",
                 f"(xs.dropLast.filter {pred}).foldl {binop} {init}", "Int")

    # --- Nat: range → map → length ---
    for ttag, trans in NAT_TRANSFORMS[:3]:
        _add(f"range_map_{ttag}_length",
             f"((List.range n).map {trans}).length", "Nat", it="Nat")

    # --- Two-arg: zipWith → eraseDups → fold ---
    for otag, op in ZIPWITH_OPS[:2]:
        zw3 = f"List.zipWith {op} xs ys"
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"zipWith_{otag}_eraseDups_foldl_{atag}",
                 f"(({zw3}).eraseDups).foldl {binop} {init}", "Int",
                 it="List Int, List Int")

    # --- Two-arg: append → map → fold ---
    for ttag, trans in TRANSFORMS[:3]:
        if ttag == "natAbs":
            continue
        for atag, init, binop in ACCUMULATORS[:2]:
            _add(f"append_map_{ttag}_foldl_{atag}",
                 f"(({app}).map {trans}).foldl {binop} {init}", "Int",
                 it="List Int, List Int")

    return cands


# ---------------------------------------------------------------------------
# Lean validation
# ---------------------------------------------------------------------------

LEAN_HEADER = """\
import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false
set_option maxRecDepth 512
"""

# Regex to filter Lean diagnostic lines (warnings/notes that leak to stdout)
_DIAG_RE = re.compile(r"^.*\.lean:\d+:\d+:|^note:|^warning:")


def _build_batch_lean(
    candidates: list[ExprCandidate],
    test_inputs: list[str],
) -> str:
    """Build a single Lean file that defines + evaluates a batch of expressions.

    Each expression gets a `def` (type-check) and `#eval` calls (semantic output).
    Uses param_sig from the candidate for correct Lean parameter syntax.
    """
    lines = [LEAN_HEADER]

    for i, cand in enumerate(candidates):
        fn_name = f"_enum_{i}"
        lines.append(f"def {fn_name} {cand.param_sig} : {cand.return_type} :=")
        lines.append(f"  {cand.expr}")
        lines.append("")
        for inp in test_inputs:
            lines.append(f"#eval {fn_name} {inp}")
        lines.append("")

    return "\n".join(lines)


def _build_single_lean(cand: ExprCandidate, test_inputs: list[str]) -> str:
    """Build a single-expression Lean file for isolated validation."""
    lines = [LEAN_HEADER]
    lines.append(f"def _enum_0 {cand.param_sig} : {cand.return_type} :=")
    lines.append(f"  {cand.expr}")
    lines.append("")
    for inp in test_inputs:
        lines.append(f"#eval _enum_0 {inp}")
    lines.append("")
    return "\n".join(lines)


def validate_batch(
    candidates: list[ExprCandidate],
    lean_project: Path,
    test_inputs: list[str],
    timeout: int = 120,
    verbose: bool = False,
) -> list[ValidatedExpr]:
    """Validate a batch of expressions by compiling a single Lean file.

    If the batch file fails (one bad expression ruins the batch), falls back
    to validating each expression individually.
    """
    if not candidates:
        return []

    lean_project = lean_project.resolve()
    n_tests = len(test_inputs)
    lean_code = _build_batch_lean(candidates, test_inputs)
    eval_file = lean_project / "FormalNav" / "_EnumBatch.lean"

    try:
        eval_file.write_text(lean_code)
        result = subprocess.run(
            ["lake", "env", "lean", str(eval_file)],
            cwd=str(lean_project),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode == 0:
            # Parse all outputs
            output_lines = [
                line.strip()
                for line in result.stdout.strip().split("\n")
                if line.strip() and not _DIAG_RE.match(line)
            ]
            return _parse_batch_outputs(candidates, output_lines, test_inputs)
        else:
            if verbose:
                stderr_preview = result.stderr[:300] if result.stderr else ""
                print(f"  Batch failed ({len(candidates)} exprs), falling back to individual: {stderr_preview}")
            return _validate_individually(candidates, lean_project, test_inputs, timeout, verbose)

    except subprocess.TimeoutExpired:
        if verbose:
            print(f"  Batch timed out ({timeout}s), falling back to individual")
        return _validate_individually(candidates, lean_project, test_inputs, timeout, verbose)

    finally:
        if eval_file.exists():
            eval_file.unlink()


def _parse_batch_outputs(
    candidates: list[ExprCandidate],
    output_lines: list[str],
    test_inputs: list[str],
) -> list[ValidatedExpr]:
    """Parse #eval outputs from a successful batch file."""
    n_tests = len(test_inputs)
    validated = []

    for i, cand in enumerate(candidates):
        start = i * n_tests
        end = start + n_tests
        if end > len(output_lines):
            break  # Truncated output — stop here

        outputs = {}
        for j, inp in enumerate(test_inputs):
            outputs[inp] = output_lines[start + j]

        validated.append(ValidatedExpr(
            tag=cand.tag,
            expr=cand.expr,
            tactic=f"exact {cand.expr}",
            return_type=cand.return_type,
            input_type=cand.input_type,
            level=cand.level,
            outputs=outputs,
        ))

    return validated


def _validate_individually(
    candidates: list[ExprCandidate],
    lean_project: Path,
    test_inputs: list[str],
    timeout: int = 60,
    verbose: bool = False,
) -> list[ValidatedExpr]:
    """Validate expressions one by one (fallback when batch fails)."""
    lean_project = lean_project.resolve()
    validated = []
    eval_file = lean_project / "FormalNav" / "_EnumSingle.lean"

    for cand in candidates:
        lean_code = _build_single_lean(cand, test_inputs)
        try:
            eval_file.write_text(lean_code)
            result = subprocess.run(
                ["lake", "env", "lean", str(eval_file)],
                cwd=str(lean_project),
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode == 0:
                output_lines = [
                    line.strip()
                    for line in result.stdout.strip().split("\n")
                    if line.strip() and not _DIAG_RE.match(line)
                ]
                if len(output_lines) >= len(test_inputs):
                    outputs = {}
                    for j, inp in enumerate(test_inputs):
                        outputs[inp] = output_lines[j]
                    validated.append(ValidatedExpr(
                        tag=cand.tag,
                        expr=cand.expr,
                        tactic=f"exact {cand.expr}",
                        return_type=cand.return_type,
                        input_type=cand.input_type,
                        level=cand.level,
                        outputs=outputs,
                    ))
                elif verbose:
                    print(f"    {cand.tag}: compiled but missing outputs ({len(output_lines)}/{len(test_inputs)})")
            elif verbose:
                err = result.stderr[:150] if result.stderr else "no stderr"
                print(f"    {cand.tag}: FAIL ({err})")

        except subprocess.TimeoutExpired:
            if verbose:
                print(f"    {cand.tag}: TIMEOUT")

        finally:
            if eval_file.exists():
                eval_file.unlink()

    return validated


# ---------------------------------------------------------------------------
# Degenerate filtering
# ---------------------------------------------------------------------------

def is_degenerate(expr: ValidatedExpr) -> bool:
    """Filter expressions that always produce the same output (identity, constant)."""
    outputs = list(expr.outputs.values())
    unique_outputs = set(outputs)

    # Always returns the same value across all test inputs
    if len(unique_outputs) == 1:
        val = outputs[0]
        # Allow if the unique value is non-trivial (e.g., a specific number)
        # But filter if it's always 0, [], or the input itself
        if val in ("0", "[]", "[]"):
            return True

    # Check if output equals input for all test cases (identity)
    all_identity = True
    for inp, out in expr.outputs.items():
        if inp.strip() != out.strip():
            all_identity = False
            break
    if all_identity:
        return True

    return False


def deduplicate(exprs: list[ValidatedExpr]) -> list[ValidatedExpr]:
    """Remove expressions that produce identical outputs on all test inputs.

    Deduplicates within same (input_type, return_type) groups only.
    Different input types have different test inputs; different return types
    should be preserved even if output strings look the same.
    """
    # Group by (input_type, return_type)
    by_type: dict[tuple[str, str], list[ValidatedExpr]] = {}
    for e in exprs:
        key = (e.input_type, e.return_type)
        by_type.setdefault(key, []).append(e)

    unique = []
    for type_key, group in by_type.items():
        seen_outputs: dict[tuple, ValidatedExpr] = {}
        for e in group:
            out_key = tuple(sorted(e.outputs.items()))
            if out_key not in seen_outputs:
                seen_outputs[out_key] = e
                unique.append(e)

    return unique


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_enumeration(
    lean_project: Path,
    output_path: Path,
    levels: list[int],
    batch_size: int = 80,
    timeout: int = 120,
    multi_type: bool = False,
    expanded_library: bool = False,
    stats_path: Optional[Path] = None,
    verbose: bool = False,
) -> dict:
    """Run the full enumeration pipeline.

    Returns stats dict.
    """
    t0 = time.time()
    candidates: list[ExprCandidate] = []

    if 0 in levels:
        l0 = enumerate_level0()
        candidates.extend(l0)
        print(f"Level 0 (List Int): {len(l0)} candidates")

        if expanded_library:
            l0_lib = enumerate_library_level0()
            candidates.extend(l0_lib)
            print(f"Level 0 (expanded library): {len(l0_lib)} candidates")

        if multi_type:
            l0_nat = enumerate_nat_level0()
            l0_nested = enumerate_nested_level0()
            l0_twoarg = enumerate_twoarg_level0()
            candidates.extend(l0_nat)
            candidates.extend(l0_nested)
            candidates.extend(l0_twoarg)
            print(f"Level 0 (Nat): {len(l0_nat)} candidates")
            print(f"Level 0 (List (List Int)): {len(l0_nested)} candidates")
            print(f"Level 0 (two-arg): {len(l0_twoarg)} candidates")

    if 1 in levels:
        l1 = enumerate_level1()
        candidates.extend(l1)
        print(f"Level 1 (List Int): {len(l1)} candidates")

        if expanded_library:
            l1_ext = enumerate_level1_extended()
            candidates.extend(l1_ext)
            print(f"Level 1 (expanded library): {len(l1_ext)} candidates")

        if multi_type:
            l1_nat = enumerate_level1_nat()
            l1_nested = enumerate_level1_nested()
            l1_twoarg = enumerate_level1_twoarg()
            candidates.extend(l1_nat)
            candidates.extend(l1_nested)
            candidates.extend(l1_twoarg)
            print(f"Level 1 (Nat): {len(l1_nat)} candidates")
            print(f"Level 1 (List (List Int)): {len(l1_nested)} candidates")
            print(f"Level 1 (two-arg): {len(l1_twoarg)} candidates")

    if 2 in levels:
        l2 = enumerate_level2()
        candidates.extend(l2)
        print(f"Level 2 (three-step): {len(l2)} candidates")

    print(f"Total candidates: {len(candidates)}")

    # Group candidates by input_type for validation
    by_type: dict[str, list[ExprCandidate]] = {}
    for c in candidates:
        by_type.setdefault(c.input_type, []).append(c)

    print(f"Input types: {list(by_type.keys())}")
    print(f"Validating with Lean (batch_size={batch_size}, timeout={timeout}s)...")

    all_validated: list[ValidatedExpr] = []

    for input_type, type_cands in by_type.items():
        test_inputs = _get_test_inputs(input_type)
        n_batches = (len(type_cands) + batch_size - 1) // batch_size
        type_validated = 0

        for batch_idx in range(n_batches):
            start = batch_idx * batch_size
            end = min(start + batch_size, len(type_cands))
            batch = type_cands[start:end]

            batch_t = time.time()
            validated = validate_batch(
                batch, lean_project, test_inputs,
                timeout=timeout, verbose=verbose,
            )
            elapsed = time.time() - batch_t

            all_validated.extend(validated)
            type_validated += len(validated)
            ok = len(validated)
            total = len(batch)
            print(f"  [{input_type}] Batch {batch_idx + 1}/{n_batches}: {ok}/{total} valid ({elapsed:.1f}s)")

        print(f"  [{input_type}] {type_validated}/{len(type_cands)} validated")

    # Filter degenerates
    before_filter = len(all_validated)
    all_validated = [e for e in all_validated if not is_degenerate(e)]
    n_degenerate = before_filter - len(all_validated)

    # Deduplicate by output (within same input type)
    before_dedup = len(all_validated)
    all_validated = deduplicate(all_validated)
    n_dedup = before_dedup - len(all_validated)

    print(f"\nResults:")
    print(f"  Validated:    {before_filter}")
    print(f"  Degenerate:   {n_degenerate} removed")
    print(f"  Duplicates:   {n_dedup} removed")
    print(f"  Final unique: {len(all_validated)}")

    # Write JSONL
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for e in all_validated:
            param_sig = _get_param_sig(e.input_type)
            state = _param_sig_to_state(param_sig, e.return_type)
            record = {
                "tag": e.tag,
                "expr": e.expr,
                "tactic": e.tactic,
                "return_type": e.return_type,
                "input_type": e.input_type,
                "level": e.level,
                "outputs": e.outputs,
                "state": state,
            }
            f.write(json.dumps(record) + "\n")

    # Stats
    by_level = {}
    by_return_type = {}
    by_input_type = {}
    base_functions = set()
    for e in all_validated:
        by_level[e.level] = by_level.get(e.level, 0) + 1
        by_return_type[e.return_type] = by_return_type.get(e.return_type, 0) + 1
        by_input_type[e.input_type] = by_input_type.get(e.input_type, 0) + 1
        # Extract base function names from tags
        parts = e.tag.split("_")
        base_functions.add(parts[0])

    elapsed_total = time.time() - t0

    stats = {
        "total_candidates": len(candidates),
        "total_validated": before_filter,
        "degenerate_removed": n_degenerate,
        "duplicates_removed": n_dedup,
        "final_unique": len(all_validated),
        "by_level": by_level,
        "by_return_type": by_return_type,
        "by_input_type": by_input_type,
        "base_functions_covered": sorted(base_functions),
        "n_input_types": len(by_input_type),
        "n_return_types": len(by_return_type),
        "elapsed_seconds": round(elapsed_total, 1),
    }

    # Write stats
    sp = stats_path or (output_path.parent / "enum_stats.json")
    with open(sp, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"\nStats written to {sp}")

    # Coverage check against T2 benchmark tasks
    _check_t2_coverage(all_validated)

    if multi_type:
        _check_hard_coverage(all_validated)

    print(f"\nDone in {elapsed_total:.1f}s")
    return stats


def _check_t2_coverage(validated: list[ValidatedExpr]) -> None:
    """Check that each T2 benchmark task has >= 1 matching expression."""
    # Reference solutions for the 10 T2 tasks
    t2_solutions = {
        "count_positive": "filter_gt0_length",
        "map_square": "map_square",
        "sort_desc": "mergeSort_reverse",
        "sum_doubles": "map_times2_foldl_sum",
        "max_abs": "map_natAbs_foldl_maxval",
        "take_positives": "takeWhile_gt0",
        "nub_sort": "eraseDups_mergeSort",
        "drop_negatives": "dropWhile_lt0",
        "count_even": "filter_even_length",
        "accum_sum": "scanl_scanSum",
    }

    tags = {e.tag for e in validated}
    covered = 0
    for task_name, expected_tag in t2_solutions.items():
        if expected_tag in tags:
            covered += 1
        else:
            print(f"  WARNING: T2 task '{task_name}' has no matching expr (expected tag '{expected_tag}')")

    print(f"\nT2 coverage: {covered}/10 tasks have matching expressions")


def _check_hard_coverage(validated: list[ValidatedExpr]) -> None:
    """Check that hard task solutions are representable in the expression space."""
    # Hard tasks need specific input types and patterns
    hard_checks = {
        "flatten": ("List (List Int)", "flatten"),
        "zip_sum": ("List Int, List Int", "zipWith_add"),
        "partition_positive": ("List Int", "partition_gt0"),
        "filter_map_abs": ("List Int", "filterMap_gt0_toNat"),
    }

    covered = 0
    for task_name, (expected_type, expected_tag_prefix) in hard_checks.items():
        found = any(
            e.tag.startswith(expected_tag_prefix) and e.input_type == expected_type
            for e in validated
        )
        if found:
            covered += 1
        else:
            print(f"  WARNING: Hard task '{task_name}' has no matching expr")

    print(f"Hard task coverage: {covered}/{len(hard_checks)} tasks have matching expressions")
    if "dedup_count" not in [t for t, _ in hard_checks.items()]:
        print(f"  NOTE: dedup_count requires complex expression not in enumeration space")


def main():
    parser = argparse.ArgumentParser(description="Enumerate typed synthesis expressions")
    parser.add_argument("--output", type=str, default="synth/expressions.jsonl",
                        help="Output JSONL path (relative to lean_mcts/)")
    parser.add_argument("--levels", type=str, default="0,1",
                        help="Comma-separated levels to enumerate (0, 1, 2)")
    parser.add_argument("--batch-size", type=int, default=80,
                        help="Expressions per Lean batch file")
    parser.add_argument("--timeout", type=int, default=120,
                        help="Timeout per batch in seconds")
    parser.add_argument("--lean-project", type=str, default=None,
                        help="Path to FormalNav Lean project (default: env/FormalNav)")
    parser.add_argument("--multi-type", action="store_true",
                        help="Include Nat, List (List Int), two-arg input types")
    parser.add_argument("--expanded-library", action="store_true",
                        help="Include expanded library functions (tail, dropLast, partition, etc.)")
    parser.add_argument("--stats", type=str, default=None,
                        help="Stats output path (default: alongside output)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # Resolve paths relative to lean_mcts/
    script_dir = Path(__file__).resolve().parent.parent
    lean_project = Path(args.lean_project) if args.lean_project else script_dir / "env" / "FormalNav"
    output_path = script_dir / args.output
    stats_path = Path(args.stats) if args.stats else None
    if stats_path and not stats_path.is_absolute():
        stats_path = script_dir / stats_path

    levels = [int(x) for x in args.levels.split(",")]

    print(f"Lean project: {lean_project}")
    print(f"Output: {output_path}")
    print(f"Levels: {levels}")
    print(f"Multi-type: {args.multi_type}")
    print(f"Expanded library: {args.expanded_library}")
    print()

    stats = run_enumeration(
        lean_project=lean_project,
        output_path=output_path,
        levels=levels,
        batch_size=args.batch_size,
        timeout=args.timeout,
        multi_type=args.multi_type,
        expanded_library=args.expanded_library,
        stats_path=stats_path,
        verbose=args.verbose,
    )

    # Final summary
    print(f"\n{'='*60}")
    target = 500 if not args.multi_type else 1500
    actual = stats["final_unique"]
    status = "PASS" if actual >= target else "BELOW TARGET"
    print(f"  {actual} unique valid expressions ({status}, target >= {target})")
    print(f"  Return types: {stats['by_return_type']}")
    print(f"  Input types: {stats['by_input_type']}")
    print(f"  Levels: {stats['by_level']}")
    print(f"  Base functions: {stats['base_functions_covered']}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
