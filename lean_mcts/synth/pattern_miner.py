"""Sprint 29 Phase 1.2 — pattern-targeted library scanner.

Where extract_lean_defs.py walks a fixed list of directories and tries to
extract every def it finds, pattern_miner.py does the opposite: it scans a
*broader* source surface (all of Mathlib Data/, all of Batteries Data/,
and a broader slice of stdlib) for specific syntactic patterns, and only
processes definitions that match one of those patterns.

The patterns target the failure modes Sprint 27 classified on the 18-task
diagnostic: tuple-accumulator folds with field interaction, state-machine
folds, indexed-fold patterns, recursive-helper-with-accumulator, multi-arm
match definitions. A pair that demonstrates one of these patterns in real
library code is exactly what Sprint 29's diversity training needs for the
model to learn structural composition beyond S22's template catalog.

Pipeline, file-by-file:
    1. Read .lean source file
    2. For each pattern in PATTERNS, check if the file body matches
    3. If any pattern matches:
         a. Parse definitions from the file (via lean_def_converter)
         b. For each def, check which patterns match its *body*
         c. Convert def → `exact` expression, specialize, verify via Pantograph
         d. Emit pair with pattern category annotation
    4. Dedupe by (goal_type, tactic) against the extract_lean_defs.py output

Output: lean_mcts/train/pattern_pairs.jsonl (same shape as library_pairs.jsonl
plus an extra `patterns` field listing which of S27's failure categories
this def exhibits).

Usage:
    python -m lean_mcts.synth.pattern_miner
    python -m lean_mcts.synth.pattern_miner --output train/pattern_pairs.jsonl --verbose
    python -m lean_mcts.synth.pattern_miner --list-patterns
    python -m lean_mcts.synth.pattern_miner --only tuple_acc_fold,recursive_helper
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from lean_mcts.synth.lean_def_converter import (
    DefKind,
    LeanDef,
    convert_to_exact,
    parse_definitions,
)
from lean_mcts.synth.extract_lean_defs import (
    SPECIALIZATIONS,
    build_goal_state,
    specialize_expr,
    specialize_type,
    verify_with_pantograph,
    _fetch_types_from_pantograph,
)


# ---------------------------------------------------------------------------
# Failure-category patterns from Sprint 27's 16 classified failures
# ---------------------------------------------------------------------------

# Each pattern is a tuple of (name, regex, description, targets_failures).
# Regexes are deliberately permissive — we prefer high recall here and let
# the specialize + verify gauntlet downstream filter out irrelevant matches.
#
# The plan's original suggested regexes were:
#   tuple_acc_fold:      r"foldl\s+\([^)]*=>[^)]*\([^,]+,[^)]+\)"
#   state_machine_fold:  r"foldl\s+\w+\s+\([^)]*,[^)]*,[^)]*\)"
#   indexed_fold:        r"(foldlIdx|mapIdx|enum|zip\s+\(List\.range)"
#   recursive_helper_acc: r"where\s+\w+\s*:?.*?\s+acc\s*:"
#   multi_arm_match:     r"match\s+.*?\s+with.*?\|.*?\|.*?\|"
#
# Here I keep close to those but add a few more and make the regexes
# robust to whitespace/comments. All patterns are applied with DOTALL
# so `.` matches newlines, since Lean definitions routinely span multiple
# lines.
PATTERNS: list[tuple[str, str, str, list[str]]] = [
    (
        "tuple_acc_fold",
        # foldl ... => ... (a, b) — the step lambda returns a tuple.
        # Non-greedy `.*?` under DOTALL spans multiple lines, which is
        # essential for real Lean code where folds commonly break lines.
        r"foldl\b.*?=>.*?\([^(),]+,[^(),]+\)",
        "foldl with a 2+ field tuple return in the step lambda "
        "(Kadane's, longestPlateau, majorityElement shape)",
        ["maxSubarraySum", "longestPlateau", "majorityElement", "secondSmallest"],
    ),
    (
        "state_machine_fold",
        # foldl followed by a 3-element tuple somewhere in the same
        # expression (anywhere within ~800 chars). Captures both
        # `foldl step (a, b, c)` (tuple as init value) and step-lambda
        # returns.
        r"foldl\b.{0,800}?\(\s*[^,()]+,\s*[^,()]+,\s*[^,()]+\s*\)",
        "foldl with a 3+ field accumulator (triple-state like "
        "longestPlateau with prev, Boyer-Moore with count+leader+tiebreak)",
        ["longestPlateau", "majorityElement"],
    ),
    (
        "indexed_fold",
        r"\b(foldlIdx|mapIdx|enum|enumerate|zipIdx|zipWithIndex|"
        r"zip\s+\(?List\.range|List\.zipWithIndex|idxOf|findIdx)\b",
        "index-tracking fold or map (mapWithIndex, equilibriumIndex pattern)",
        ["mapWithIndex", "equilibriumIndex", "prefixWhileIncreasing"],
    ),
    (
        "recursive_helper_acc",
        # where <name> ... acc appearing within 200 chars of each other.
        # DOTALL makes `.` span newlines so acc can be on any subsequent line.
        r"where\s+\w+\b.{0,200}?\bacc\b",
        "recursive helper (where-clause) that accumulates via an `acc` "
        "parameter (chunksOf, insertSorted, runLengthEncode shape)",
        ["chunksOf", "insertSorted", "diagTranspose"],
    ),
    (
        "recursive_let_rec",
        r"\blet\s+rec\s+\w+",
        "local `let rec` definition inside a def body — matches S27's "
        "let_rec failure mode (interleaveZip, chunksOf)",
        ["interleaveZip", "chunksOf", "insertSorted"],
    ),
    (
        "multi_arm_match",
        # match ... with then at least 3 `|` separators. Non-greedy span
        # up to 600 chars (longer matches are usually separate matches
        # concatenated anyway). DOTALL makes `.` span newlines.
        r"match\b.{0,400}?\bwith\b(?:.{0,300}?\|){3,}",
        "match expression with 3+ case arms (Dutch-flag 3-way partition, "
        "Boyer-Moore transitions, stack-evaluator dispatch)",
        ["dutchFlag", "majorityElement", "evalStack"],
    ),
    (
        "two_pass",
        # `let X := ... ; ... X.foldl` or `let X := ...\n ... .foldl X`
        # where X is computed first and consumed in a second fold.
        r"let\s+\w+\s*:=.{0,200}?\.foldl|"
        r"let\s+\w+\s*:=.{0,200}?\bfoldl\b",
        "two-pass computation: first let-binding computes an aggregate, "
        "second pass folds using it (equilibriumIndex, maxSubarraySum)",
        ["equilibriumIndex", "maxSubarraySum"],
    ),
    (
        "higher_order_arg",
        # (T → T) → rest, also the Lean-ASCII form (T -> T) -> rest
        r"\((?:[A-Za-z_]\w*|\([^()]*\))\s*(?:→|->)\s*(?:[A-Za-z_]\w*|\([^()]*\))\)\s*(?:→|->)",
        "function-valued parameter in the signature (unfoldN, iterate)",
        ["unfoldN"],
    ),
    (
        "operation_chain_3plus",
        # 3+ step method chain starting from a lower-case variable (so
        # we don't false-positive on module paths like `List.Basic.foo`).
        # Requires the chain to be bounded by non-dot context on both ends.
        r"(?:^|[\s(])[a-z]\w*(?:\.\w+){3,}",
        "3+ step method chain (reverse → eraseDups → reverse, "
        "filter → sort → dedup)",
        ["removeDuplicatesKeepLast", "allSublists"],
    ),
    (
        "nested_fold",
        # Two fold-family calls within ~300 chars of each other.
        r"\bfold[lr]\b.{0,300}?\bfold[lr]\b",
        "nested folds (matrix-transpose-like, allSublists, interleave)",
        ["diagTranspose", "allSublists"],
    ),
]


PATTERN_NAMES = [p[0] for p in PATTERNS]


# ---------------------------------------------------------------------------
# Broader source surface
# ---------------------------------------------------------------------------

# Directories scanned. More inclusive than extract_lean_defs.py —
# Phase 1.2 grabs files that are unlikely to be dominated by theorems
# (i.e. `Basic`, `Defs`, `Impl` suffixes are typically definition-rich
# while `Lemmas`, `Properties`, `Thm` tend to be theorem-heavy).
#
# stdlib: broader Init/Data coverage
# Batteries: all Data/ subdirs
# Mathlib:
#   - Data/List/*             (beyond the 5 files in find_source_files)
#   - Data/Array/*            (has algorithmic definitions for Array)
#   - Data/Option/*           (monadic helpers)
#   - Data/Finset/Basic.lean  (sparingly — a lot of theorems too)
#   - Logic/                  (uses match often)
#   - Control/                (contains monadic fold/traverse patterns)
#
# The yield from each source is unpredictable. pattern_miner.py will
# only keep definitions whose bodies contain at least one pattern, so
# theorem-heavy files get filtered out naturally.

STDLIB_SCAN_DIRS: list[tuple[str, str]] = [
    ("Init/Data/List",   "List"),
    ("Init/Data/Array",  "Array"),
    ("Init/Data/Option", "Option"),
    ("Init/Data/Nat",    "Nat"),
    ("Init/Data/Int",    "Int"),
    ("Init/Data/Sum",    "Sum"),
    ("Init/Data/Range",  "Range"),
    ("Init/Data/Fin",    "Fin"),
]

BATTERIES_SCAN_DIRS: list[tuple[str, str]] = [
    ("Data/List",          "List"),
    ("Data/Array",         "Array"),
    ("Data/HashMap",       "HashMap"),
    ("Data/AssocList",     "AssocList"),
    ("Data/BinaryHeap",    "BinaryHeap"),
    ("Data/BinomialHeap",  "BinomialHeap"),
    ("Data/DList",         "DList"),
    ("Data/RBMap",         "RBMap"),
    ("Data/Vector",        "Vector"),
    ("Data/UnionFind",     "UnionFind"),
]

MATHLIB_SCAN_DIRS: list[tuple[str, str]] = [
    ("Data/List",   "List"),
    ("Data/Array",  "Array"),
    ("Data/Option", "Option"),
    ("Control",     ""),  # prefix inferred from qualified names in file
]

# Filename hints that suggest a file is definition-rich (not theorem-rich).
# A file whose basename matches one of these is scanned even if it has no
# pattern matches in the full text, because its defs are more likely to
# be extractable.
DEFRICH_HINTS = {"Defs", "Basic", "Impl", "Sort", "Range", "Rotate"}


def find_scan_sources(project_root: Path) -> list[tuple[Path, str]]:
    """Return (path, module_prefix) tuples for every .lean file the
    pattern miner should consider. This is a superset of what
    extract_lean_defs.find_source_files returns.
    """
    out: list[tuple[Path, str]] = []

    # stdlib
    elan = Path.home() / ".elan" / "toolchains"
    if elan.exists():
        toolchains = sorted(elan.iterdir(), reverse=True)
        for tc in toolchains:
            src = tc / "src" / "lean"
            if not src.exists():
                continue
            for subpath, prefix in STDLIB_SCAN_DIRS:
                d = src / subpath
                if d.exists():
                    for f in sorted(d.rglob("*.lean")):
                        out.append((f, prefix))
            break

    # Batteries
    lake = project_root / "lean_mcts" / "env" / "FormalNav" / ".lake" / "packages"
    batt = lake / "batteries" / "Batteries"
    if batt.exists():
        for subpath, prefix in BATTERIES_SCAN_DIRS:
            d = batt / subpath
            if d.exists():
                for f in sorted(d.rglob("*.lean")):
                    if f.name == f"{d.name}.lean" and f.parent == d:
                        continue  # re-export index
                    out.append((f, prefix))

    # Mathlib
    mathlib = lake / "mathlib" / "Mathlib"
    if mathlib.exists():
        for subpath, prefix in MATHLIB_SCAN_DIRS:
            d = mathlib / subpath
            if not d.exists():
                continue
            for f in sorted(d.rglob("*.lean")):
                out.append((f, prefix))

    return out


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

@dataclass
class PatternHit:
    """One definition matched by one or more patterns."""
    d: LeanDef
    matched_patterns: list[str]
    source_file: Path


def _def_body_text(d: LeanDef) -> str:
    """Return the full text of a definition's body, combining SIMPLE / EQCOMP /
    WHERE sub-forms into one string so a pattern can match across the combined
    content.

    LeanDef schema (from lean_def_converter.py):
        - body:       str              (SIMPLE kind)
        - eq_clauses: list[EqClause]   (EQCOMP kind — each has .patterns, .body)
        - main_expr:  str              (WHERE kind — main expression)
        - where_defs: list[WhereDef]   (WHERE kind — each has .clauses, .raw_body)
        - raw_text:   str              (fallback — full captured text of the def)
    """
    parts: list[str] = []
    if d.body:
        parts.append(d.body)
    if d.main_expr:
        parts.append(d.main_expr)
    for cl in d.eq_clauses:
        parts.append(cl.patterns + " => " + cl.body)
    for wd in d.where_defs:
        if wd.raw_body:
            parts.append(wd.raw_body)
        for ec in wd.clauses:
            parts.append(ec.patterns + " => " + ec.body)
    if not parts and d.raw_text:
        # Last resort — every path above gave nothing, but we have the raw
        # captured text. Falls through for odd kinds like nested matches.
        parts.append(d.raw_text)
    return "\n".join(parts)


def match_patterns(
    body_text: str,
    enabled: set[str],
) -> list[str]:
    """Return the names of all enabled patterns whose regex matches the body."""
    matched: list[str] = []
    for name, pattern_re, _desc, _targets in PATTERNS:
        if name not in enabled:
            continue
        if re.search(pattern_re, body_text, flags=re.DOTALL):
            matched.append(name)
    return matched


def find_pattern_hits(
    source_files: list[tuple[Path, str]],
    enabled: set[str],
    verbose: bool = False,
) -> list[PatternHit]:
    """Walk the source surface and collect every definition whose body
    matches at least one enabled pattern."""
    hits: list[PatternHit] = []
    files_scanned = 0
    files_with_match = 0
    for path, prefix in source_files:
        files_scanned += 1
        try:
            text = path.read_text(errors="replace")
        except Exception:
            continue
        # Cheap pre-filter: if NO pattern matches the raw file text, skip
        # parsing entirely. This is the main speedup vs. extract_lean_defs.py
        # which parses every file unconditionally.
        file_has_any = any(
            re.search(p[1], text, flags=re.DOTALL) for p in PATTERNS if p[0] in enabled
        )
        if not file_has_any and path.stem not in DEFRICH_HINTS:
            continue
        files_with_match += 1
        try:
            defs = parse_definitions(path, module_prefix=prefix)
        except Exception as e:
            if verbose:
                print(f"  parse-fail {path.name}: {type(e).__name__}: {e}")
            continue
        for d in defs:
            body_text = _def_body_text(d)
            if not body_text.strip():
                continue
            matched = match_patterns(body_text, enabled)
            if matched:
                hits.append(PatternHit(d=d, matched_patterns=matched, source_file=path))
    if verbose:
        print(f"  scanned {files_scanned} files; {files_with_match} had pattern hits")
    return hits


# ---------------------------------------------------------------------------
# Conversion & verification
# ---------------------------------------------------------------------------

def hits_to_pairs(
    hits: list[PatternHit],
    project_root: Path,
    verify: bool = True,
    verbose: bool = False,
) -> list[dict]:
    """Convert pattern hits into training pairs, specialize, and verify."""
    from lean_mcts.synth.lean_def_converter import extract_explicit_params, DefKind as DK

    # Step 1: convert each def to an `exact` expression
    converted: list[tuple[LeanDef, str, list[str]]] = []
    for hit in hits:
        expr = convert_to_exact(hit.d, wrap_lambda=False)
        if expr is None:
            continue
        converted.append((hit.d, expr, hit.matched_patterns))

    if verbose:
        print(f"  {len(converted)}/{len(hits)} hits converted to `exact` form")

    if not converted:
        return []

    # Step 2: fetch types from Pantograph for every unique name
    type_cache = _fetch_types_from_pantograph(
        [(d, e) for d, e, _ in converted],
        project_root,
        verbose=False,
    )
    if verbose:
        print(f"  got types for {len(type_cache)} / {len(converted)} defs")

    # Step 3: specialize to Int / Nat and build candidate pairs
    pairs: list[dict] = []
    for d, expr, matched in converted:
        raw_type = type_cache.get(d.full_name) or type_cache.get(d.name)
        if not raw_type:
            continue
        if d.kind == DK.EQCOMP:
            intro_names = d.eq_match_args.replace(",", "") if d.eq_match_args else ""
        else:
            params = extract_explicit_params(d)
            intro_names = " ".join(params)
        if not intro_names:
            continue
        for subst in SPECIALIZATIONS:
            spec_type = specialize_type(raw_type, subst)
            if not spec_type:
                continue
            spec_expr = specialize_expr(expr, subst)
            tactic = f"exact {spec_expr}"
            if len(tactic) > 512:
                continue
            pairs.append({
                "state": "",
                "tactic": tactic,
                "goal_type": spec_type,
                "intro_names": intro_names,
                "source": "pattern",
                "name": d.name,
                "full_name": d.full_name,
                "kind": d.kind.name,
                "subst": json.dumps(subst),
                "file": d.source_file,
                "patterns": matched,
            })

    if verbose:
        print(f"  {len(pairs)} specialized candidate pairs")

    # Dedup by (goal_type, tactic)
    seen: set[tuple[str, str]] = set()
    unique = []
    for p in pairs:
        key = (p["goal_type"], p["tactic"])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    pairs = unique
    if verbose:
        print(f"  {len(pairs)} after dedup")

    # Step 4: verify through Pantograph
    if verify:
        pairs = verify_with_pantograph(pairs, project_root, verbose=verbose)
        if verbose:
            print(f"  {len(pairs)} verified")
    else:
        for p in pairs:
            p["state"] = build_goal_state(p["goal_type"])

    for p in pairs:
        p.pop("goal_type", None)
        p.pop("intro_names", None)

    return pairs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Pattern-targeted library scanner for Sprint 29 Phase 1.2"
    )
    parser.add_argument(
        "--output", type=str, default="lean_mcts/train/pattern_pairs.jsonl"
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip Pantograph verification (for quick discovery runs)")
    parser.add_argument("--list-patterns", action="store_true",
                        help="List the enabled pattern categories and exit")
    parser.add_argument("--only", type=str, default=None,
                        help="Comma-separated pattern names to enable "
                             "(default: all). Pattern names: "
                             + ", ".join(PATTERN_NAMES))
    args = parser.parse_args()

    if args.list_patterns:
        print("Available pattern categories:")
        for name, regex, desc, targets in PATTERNS:
            print(f"\n  {name}")
            print(f"    description: {desc}")
            print(f"    targets S27 failures: {', '.join(targets)}")
            print(f"    regex: {regex}")
        return

    enabled = set(args.only.split(",")) if args.only else set(PATTERN_NAMES)
    unknown = enabled - set(PATTERN_NAMES)
    if unknown:
        parser.error(f"unknown pattern names: {sorted(unknown)}")

    project_root = Path(__file__).resolve().parent.parent.parent

    print(f"Pattern miner starting ({len(enabled)} patterns enabled)")

    source_files = find_scan_sources(project_root)
    print(f"Source surface: {len(source_files)} .lean files")

    print("Scanning for pattern matches...")
    hits = find_pattern_hits(source_files, enabled, verbose=args.verbose)
    print(f"Found {len(hits)} matching definitions")

    # Pattern distribution report
    pattern_counts: dict[str, int] = {}
    for h in hits:
        for p in h.matched_patterns:
            pattern_counts[p] = pattern_counts.get(p, 0) + 1
    print("Pattern hit counts:")
    for name in PATTERN_NAMES:
        if pattern_counts.get(name):
            print(f"  {name:24s}: {pattern_counts[name]}")

    if not hits:
        print("No hits — nothing to verify.")
        return

    print("Converting, specializing, and verifying...")
    pairs = hits_to_pairs(hits, project_root, verify=not args.no_verify,
                          verbose=args.verbose)
    print(f"Final verified pairs: {len(pairs)}")

    if not pairs:
        print("No pairs survived verification.")
        sys.exit(1)

    # Write
    output_path = project_root / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as out:
        for p in pairs:
            out.write(json.dumps(p) + "\n")
    print(f"Wrote {len(pairs)} pairs → {output_path}")


if __name__ == "__main__":
    main()
