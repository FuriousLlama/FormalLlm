"""
clean.py — Transform raw GHC Core ASTs into clean IR.

Removes GHC-specific noise:
  - I# boxing/unboxing (App(Con I#, [Lit]) → Lit; Case(x, [I# n → body]) → body[n:=x])
  - Typeclass dictionaries ($fNumInt, $fEqInt, etc.)
  - Type arguments (TyArg nodes)
  - Type binders in Lam (kind:"type")
  - Internal names (__DEFAULT → PWild, as field dropped)
  - Int# sort → Int, val string → int

Introduces first-class Prim nodes for arithmetic/comparison/logical ops.

Usage:
  python sprint/extract/clean.py corpus_raw.json \
    --out corpus_clean.json --schema ir_schema.json --stats --package handwritten
"""

import json
import sys
import argparse
from pathlib import Path
from copy import deepcopy

# ── Primitive sets ────────────────────────────────────────────────────────────

# Operators dispatched via typeclass dict (TyArg + $f* args precede operands)
PRIMITIVES_WITH_DICT = frozenset({
    "+", "-", "*", "div", "mod",
    "==", "/=", "<", "<=", ">", ">=",
    "negate", "abs", "signum",
})

# Operators that are plain Var references (no dict)
PRIMITIVES_NO_DICT = frozenset({"&&", "||", "not"})

ALL_PRIMITIVES = PRIMITIVES_WITH_DICT | PRIMITIVES_NO_DICT

# GHC noise tokens that must not survive cleaning
GHC_NOISE = {"I#", "C#", "$f", "TyArg", "__DEFAULT", "Int#", "wild"}

# GHC-internal Var names that should be dropped (not real functions)
GHC_ARTIFACT_VARS = frozenset({"letrec", "cast", "$trModule1", "primFixed"})


# ── Substitution ──────────────────────────────────────────────────────────────

def substitute(expr: dict, name: str, replacement: dict) -> dict:
    """Replace all occurrences of Var(name) with replacement in expr."""
    if expr is None:
        return None
    tag = expr.get("tag")

    if tag == "Var":
        if expr["name"] == name:
            return deepcopy(replacement)
        return expr

    if tag in ("Lit", "Con", "Prim"):
        return expr

    if tag == "App":
        return {
            "tag": "App",
            "fun": substitute(expr["fun"], name, replacement),
            "args": [substitute(a, name, replacement) for a in expr["args"]],
        }

    if tag == "Lam":
        # If the binder shadows the name, stop substituting in body
        binder_names = {b["name"] for b in expr["binders"]}
        if name in binder_names:
            return expr
        return {
            "tag": "Lam",
            "binders": expr["binders"],
            "body": substitute(expr["body"], name, replacement),
        }

    if tag == "Let":
        bind_names = {b["name"] for b in expr["binds"]}
        new_binds = []
        for b in expr["binds"]:
            new_binds.append({
                "name": b["name"],
                "val": substitute(b["val"], name, replacement),
            })
        new_body = expr["body"]
        if name not in bind_names:
            new_body = substitute(new_body, name, replacement)
        return {"tag": "Let", "binds": new_binds, "body": new_body}

    if tag == "Case":
        new_scrut = substitute(expr["scrutinee"], name, replacement)
        new_alts = []
        for alt in expr["alts"]:
            pat = alt["pat"]
            # Check if pattern binds the name (shadows it)
            pat_vars = _pat_vars(pat)
            if name in pat_vars:
                new_alts.append({"pat": pat, "body": alt["body"]})
            else:
                new_alts.append({
                    "pat": pat,
                    "body": substitute(alt["body"], name, replacement),
                })
        return {"tag": "Case", "scrutinee": new_scrut, "alts": new_alts}

    if tag == "Tuple":
        return {
            "tag": "Tuple",
            "elems": [substitute(e, name, replacement) for e in expr["elems"]],
        }

    # Fallback: return as-is (shouldn't happen for valid ASTs)
    return expr


def _pat_vars(pat: dict) -> set:
    """Extract variable names bound by a pattern."""
    if pat is None:
        return set()
    tag = pat.get("tag")
    if tag in ("PCon", "PTuple"):
        return set(pat.get("vars", []))
    return set()


# ── Core cleaning ─────────────────────────────────────────────────────────────

def clean_expr(node: dict) -> dict | None:
    """Recursively transform a raw GHC Core AST node into clean IR.

    Returns None for nodes that should be dropped (dict args, TyArg, etc.).
    """
    if node is None:
        return None

    tag = node.get("tag")

    # ── Rule 9: Lit — strip Int# → Int, val string → int ──
    if tag == "Lit":
        sort = node.get("sort", "Int")
        sort = sort.rstrip("#")  # "Int#" → "Int"
        val = node.get("val", "0")
        if isinstance(val, str):
            val = int(val.rstrip("#"))
        return {"tag": "Lit", "sort": sort, "val": val}

    # ── Rule 6: Var → Prim for &&, ||, not ──
    if tag == "Var":
        name = node["name"]
        if name in PRIMITIVES_NO_DICT:
            return {"tag": "Prim", "op": name}
        # Promote standalone comparison/arithmetic ops to Prim
        if name in PRIMITIVES_WITH_DICT:
            return {"tag": "Prim", "op": name}
        # Rule 8: Drop typeclass dictionary args
        if name.startswith("$f"):
            return None
        # Drop GHC-internal artifact vars
        if name in GHC_ARTIFACT_VARS:
            return None
        return {"tag": "Var", "name": name}

    if tag == "Con":
        name = node["name"]
        # I# and C# should not appear as standalone Con in clean IR
        # (they're handled in App context via Rule 3)
        if name in ("I#", "C#"):
            return None
        return {"tag": "Con", "name": name}

    if tag == "Prim":
        return node  # already clean

    # ── TyArg: drop entirely ──
    if tag == "TyArg":
        return None

    # ── App ──
    if tag == "App":
        return _clean_app(node)

    # ── Lam — Rule 1: strip type binders ──
    if tag == "Lam":
        return _clean_lam(node)

    # ── Let ──
    if tag == "Let":
        new_binds = []
        for b in node["binds"]:
            val = clean_expr(b["val"])
            if val is not None:
                new_binds.append({"name": b["name"], "val": val})
        body = clean_expr(node["body"])
        if not new_binds:
            return body
        if body is None:
            return None
        return {"tag": "Let", "binds": new_binds, "body": body}

    # ── Case — Rule 4, 7, 11, 12 ──
    if tag == "Case":
        return _clean_case(node)

    # ── Tuple ──
    if tag == "Tuple":
        elems = [clean_expr(e) for e in node["elems"]]
        elems = [e for e in elems if e is not None]
        if not elems:
            return None
        return {"tag": "Tuple", "elems": elems}

    # Unknown tag: return None
    return None


def _clean_app(node: dict) -> dict | None:
    """Clean an App node, applying Rules 2, 3, 5, 8, 10."""
    fun_raw = node["fun"]
    args_raw = node["args"]

    # ── Rule 3: App(Con "I#", [Lit]) → Lit ──
    if fun_raw.get("tag") == "Con" and fun_raw.get("name") == "I#":
        # Find the first real arg (skip TyArg)
        for a in args_raw:
            if a.get("tag") == "Lit":
                return clean_expr(a)
            if a.get("tag") != "TyArg":
                return clean_expr(a)
        return {"tag": "Lit", "sort": "Int", "val": 0}

    # ── Rule 5: Typeclass-dispatched primitives ──
    if (fun_raw.get("tag") == "Var"
            and fun_raw["name"] in PRIMITIVES_WITH_DICT):
        op = fun_raw["name"]
        # Strip TyArg and $f* args, keep the rest
        operands = []
        for a in args_raw:
            if a.get("tag") == "TyArg":
                continue
            if a.get("tag") == "Var" and a["name"].startswith("$f"):
                continue
            cleaned = clean_expr(a)
            if cleaned is not None:
                operands.append(cleaned)
        prim = {"tag": "Prim", "op": op}
        if not operands:
            return prim
        # Rule 10: single operand for unary ops
        if len(operands) == 1 and op in ("negate", "abs", "signum"):
            return {"tag": "App", "fun": prim, "args": operands}
        return {"tag": "App", "fun": prim, "args": operands}

    # ── General App: clean fun and args, drop None ──
    fun = clean_expr(fun_raw)
    if fun is None:
        return None

    cleaned_args = []
    for a in args_raw:
        ca = clean_expr(a)
        if ca is not None:
            cleaned_args.append(ca)

    # Rule 10: trivial application
    if not cleaned_args:
        return fun

    return {"tag": "App", "fun": fun, "args": cleaned_args}


def _clean_lam(node: dict) -> dict | None:
    """Clean a Lam node. Rule 1: strip type binders."""
    new_binders = clean_binders(node["binders"])
    body = clean_expr(node["body"])
    if body is None:
        return None
    if not new_binders:
        # All binders were type binders — return body directly
        return body
    return {"tag": "Lam", "binders": new_binders, "body": body}


def _clean_case(node: dict) -> dict | None:
    """Clean a Case node. Rules 4, 7, 11, 12."""
    scrutinee = clean_expr(node["scrutinee"])
    if scrutinee is None:
        return None

    as_var = node.get("as")  # may be None

    alts = node["alts"]

    # ── Rule 4 + 12: Single-alt I# case → substitution ──
    if len(alts) == 1:
        alt = alts[0]
        pat = alt["pat"]
        if pat.get("tag") == "PCon" and pat.get("con") == "I#":
            body = clean_expr(alt["body"])
            if body is None:
                return None
            # Substitute the pattern var (the unboxed Int)
            pat_vars = pat.get("vars", [])
            if pat_vars:
                body = substitute(body, pat_vars[0], scrutinee)
            # Rule 12: also substitute the as-var
            if as_var:
                body = substitute(body, as_var, scrutinee)
            return body

    # ── General case: clean all alternatives ──
    new_alts = []
    for alt in alts:
        new_pat = clean_pat(alt["pat"])
        # Start with cleaned body
        body = clean_expr(alt["body"])
        if body is None:
            continue
        # If there's an as-var, substitute it with the scrutinee
        if as_var:
            body = substitute(body, as_var, scrutinee)
        new_alts.append({"pat": new_pat, "body": body})

    if not new_alts:
        return None

    # Rule 11: as field dropped (not included in output)
    return {"tag": "Case", "scrutinee": scrutinee, "alts": new_alts}


def clean_pat(pat: dict) -> dict:
    """Clean a pattern node. Rule 7: __DEFAULT → PWild."""
    if pat is None:
        return {"tag": "PWild"}

    tag = pat.get("tag")

    if tag == "PCon":
        con = pat["con"]
        # Rule 7: __DEFAULT → PWild
        if con == "__DEFAULT":
            return {"tag": "PWild"}
        # I# patterns should have been handled by _clean_case
        if con == "I#":
            return {"tag": "PWild"}
        return {"tag": "PCon", "con": con, "vars": list(pat.get("vars", []))}

    if tag == "PTuple":
        return {"tag": "PTuple", "vars": list(pat.get("vars", []))}

    if tag == "PLit":
        val = pat.get("val", "0")
        if isinstance(val, str):
            val = int(val.rstrip("#"))
        return {"tag": "PLit", "val": val}

    if tag == "PWild":
        return {"tag": "PWild"}

    # Unknown pattern
    return {"tag": "PWild"}


def clean_binders(binders: list) -> list:
    """Strip type binders, keep only val binders."""
    result = []
    for b in binders:
        if b.get("kind") == "type":
            continue  # Rule 1: drop type binders
        result.append({"name": b["name"], "kind": "val"})
    return result


# ── Top-level cleaning ────────────────────────────────────────────────────────

def clean_function(entry: dict, package: str = "unknown") -> dict | None:
    """Clean one corpus entry. Returns cleaned entry or None on failure."""
    if not entry.get("ok"):
        return None
    try:
        body = clean_expr(entry["body"])
        if body is None:
            return None
        return {
            "name": entry["name"],
            "type": entry.get("type", ""),
            "recursive": entry.get("recursive", False),
            "package": package,
            "body": body,
            "ok": True,
        }
    except Exception as e:
        print(f"[clean] WARN: {entry.get('name', '?')}: {e}", file=sys.stderr)
        return None


def clean_corpus(entries: list, package: str = "unknown") -> list:
    """Clean all corpus entries."""
    results = []
    for entry in entries:
        cleaned = clean_function(entry, package)
        if cleaned is not None:
            results.append(cleaned)
    return results


# ── Validation ────────────────────────────────────────────────────────────────

def validate_no_ghc_noise(entries: list) -> list[str]:
    """Scan cleaned entries for leftover GHC noise. Returns list of violations."""
    violations = []
    for entry in entries:
        text = json.dumps(entry)
        name = entry.get("name", "?")
        if '"I#"' in text:
            violations.append(f"{name}: contains I#")
        if '"C#"' in text:
            violations.append(f"{name}: contains C#")
        if '"$f' in text:
            violations.append(f"{name}: contains $f* dict reference")
        if '"TyArg"' in text:
            violations.append(f"{name}: contains TyArg")
        if '"__DEFAULT"' in text:
            violations.append(f"{name}: contains __DEFAULT")
        if '"Int#"' in text:
            violations.append(f"{name}: contains Int# sort")
        if '"wild"' in text or '"wild1"' in text:
            # Check if wild/wild1 appear as variable names (not as part of other strings)
            _check_wild_vars(entry, name, violations)
    return violations


def _check_wild_vars(node, func_name: str, violations: list, path: str = ""):
    """Recursively check for wild/wild1 as Var names (not binder names, which is OK after cleaning)."""
    if not isinstance(node, dict):
        return
    if node.get("tag") == "Var" and node.get("name") in ("wild", "wild1"):
        violations.append(f"{func_name}: Var '{node['name']}' at {path}")
    for k, v in node.items():
        if isinstance(v, dict):
            _check_wild_vars(v, func_name, violations, f"{path}.{k}")
        elif isinstance(v, list):
            for i, item in enumerate(v):
                _check_wild_vars(item, func_name, violations, f"{path}.{k}[{i}]")


def validate_schema(entries: list, schema_path: str) -> list[str]:
    """Validate cleaned entries against the JSON Schema. Returns violations."""
    try:
        import jsonschema
    except ImportError:
        print("[clean] WARN: jsonschema not installed, skipping schema validation",
              file=sys.stderr)
        return []

    schema = json.loads(Path(schema_path).read_text())
    violations = []
    for entry in entries:
        try:
            jsonschema.validate([entry], schema)
        except jsonschema.ValidationError as e:
            violations.append(f"{entry.get('name', '?')}: {e.message}")
    return violations


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="GHC Core → Clean IR transformation")
    p.add_argument("input", help="Raw corpus JSON (e.g. corpus_raw.json)")
    p.add_argument("--out", required=True, help="Output cleaned corpus JSON")
    p.add_argument("--schema", default=None, help="JSON Schema to validate against")
    p.add_argument("--package", default="unknown", help="Package name for all entries")
    p.add_argument("--stats", action="store_true", help="Print cleaning statistics")
    args = p.parse_args()

    data = json.loads(Path(args.input).read_text())
    total = len(data)

    cleaned = clean_corpus(data, package=args.package)
    n_cleaned = len(cleaned)

    # Validate no GHC noise
    noise_violations = validate_no_ghc_noise(cleaned)

    # Optional schema validation
    schema_violations = []
    if args.schema:
        schema_violations = validate_schema(cleaned, args.schema)

    # Write output
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(cleaned, indent=2))

    # Count specific noise types for stats
    all_text = json.dumps(cleaned)
    n_ihash = all_text.count('"I#"')
    n_fdict = all_text.count('"$f')
    n_default = all_text.count('"__DEFAULT"')
    n_tyarg = all_text.count('"TyArg"')

    if args.stats:
        print(f"[clean] {total} processed, {n_cleaned} cleaned, "
              f"{total - n_cleaned} dropped")
        print(f"[clean] noise: {n_ihash} I#, {n_fdict} $f*, "
              f"{n_default} __DEFAULT, {n_tyarg} TyArg")
        if noise_violations:
            print(f"[clean] NOISE VIOLATIONS ({len(noise_violations)}):")
            for v in noise_violations:
                print(f"  {v}")
        else:
            print("[clean] 0 noise violations")
        if schema_violations:
            print(f"[clean] SCHEMA VIOLATIONS ({len(schema_violations)}):")
            for v in schema_violations[:10]:
                print(f"  {v}")
        elif args.schema:
            print("[clean] 0 schema violations")

    print(f"[clean] wrote {n_cleaned} functions → {args.out}")

    # Exit with error if violations found
    if noise_violations:
        sys.exit(1)


if __name__ == "__main__":
    main()
