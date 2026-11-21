"""
normalize.py — de Bruijn conversion + depth-first serialization of typed ASTs.

Input:  sprint/extract/corpus_raw.json  (or corpus_hackage.json)
Output: sprint/corpus/sequences.json   (list of token sequences, one per function)

De Bruijn conversion:
  - Free variables (global refs) → lambda-lifted as outermost Lam binders
  - Bound variables → de Bruijn indices (0 = innermost binder)
  - Self-recursive calls → SELF token
  - Type binders → @T token (unchanged)

Serialization (S-expression prefix form):
  DEF TY a -> a BODY (Lam @T 2 <body>) EOF   -- Sprint 5: type-conditioned format
  (App <fun> <arg0> ...)
  Var 0                              -- de Bruijn index (0 = innermost)
  Var fMap                           -- free variable (lambda-lifted to outermost Lam)
  (Con C)
  (Lit 42)
  (Prim +)
  (Case <scrutinee> (Alt (PCon : 2) <body>) ...)  -- PCon arity as count token
  (Let (Bind <val>) <body>)          -- no binder name; count inferred from (Bind) nodes
  (Tuple <e0> <e1>)
  (TyArg Int)

Example:
  fDouble x = x * 2
  Sprint 4: DEF fDouble (Lam 1 (App (Prim *) Var 0 (Lit 2))) EOF
  Sprint 5: DEF TY Int -> Int BODY (Lam 1 (App (Prim *) Var 0 (Lit 2))) EOF
"""

import json
import re
import sys
import argparse
from pathlib import Path


# ---------------------------------------------------------------------------
# De Bruijn renamer — mutable stack, no child/counter complexity
# ---------------------------------------------------------------------------

class DeBruijnRenamer:
    """Tracks binding depth for de Bruijn index computation.

    Uses a mutable stack: push() when entering a scope, pop() when leaving.
    Lookup searches innermost-first and returns the index as a string.
    Free variables (not on the stack) are returned as-is.
    """

    def __init__(self):
        self._stack: list[str] = []   # binder names, outermost first / innermost last
        self._self_name: str | None = None

    def push(self, name: str) -> None:
        self._stack.append(name)

    def pop(self, n: int = 1) -> None:
        if n > 0:
            del self._stack[-n:]

    def lookup(self, name: str) -> str:
        """Return de Bruijn index (as str), 'SELF', or original name for free vars."""
        for i, bound in enumerate(reversed(self._stack)):
            if bound == name:
                return str(i)
        if self._self_name is not None and name == self._self_name:
            return "SELF"
        return name  # free variable — kept as-is (lambda-lifted at top level)


# ---------------------------------------------------------------------------
# Free variable collection (unchanged from sprint 3)
# ---------------------------------------------------------------------------

def collect_free_vars(node: dict | None, scope: set[str]) -> set[str]:
    """Walk raw AST dict, return set of Var names not in scope."""
    if node is None:
        return set()
    tag = node["tag"]

    if tag == "Var":
        name = node["name"]
        return set() if name in scope else {name}

    if tag in ("Con", "Lit", "Prim", "TyArg"):
        return set()

    if tag == "App":
        free = collect_free_vars(node["fun"], scope)
        for a in node["args"]:
            free |= collect_free_vars(a, scope)
        return free

    if tag == "Tuple":
        free: set[str] = set()
        for e in node["elems"]:
            free |= collect_free_vars(e, scope)
        return free

    if tag == "Lam":
        inner = set(scope)
        for b in node["binders"]:
            if b["kind"] == "val":
                inner.add(b["name"])
        return collect_free_vars(node["body"], inner)

    if tag == "Let":
        inner = set(scope)
        for bind in node["binds"]:
            inner.add(bind["name"])
        free = set()
        for bind in node["binds"]:
            free |= collect_free_vars(bind["val"], inner)
        free |= collect_free_vars(node["body"], inner)
        return free

    if tag == "Case":
        free = collect_free_vars(node["scrutinee"], scope)
        for alt in node["alts"]:
            alt_scope = set(scope)
            pat = alt["pat"]
            if pat["tag"] in ("PCon", "PTuple"):
                for v in pat["vars"]:
                    alt_scope.add(v)
            free |= collect_free_vars(alt["body"], alt_scope)
        return free

    return set()


# ---------------------------------------------------------------------------
# De Bruijn AST conversion
# ---------------------------------------------------------------------------

def convert_expr(node: dict, r: DeBruijnRenamer, known_names: set[str] | None = None) -> dict:
    """Walk AST, replacing bound Var names with de Bruijn indices.

    Binder names are stripped from Lam/Let/pattern nodes; uses become indices.
    The renamer stack must already contain any pre-bound external deps.
    Known corpus function names are emitted as GlobalRef nodes instead of Var.
    """
    tag = node["tag"]

    if tag == "Var":
        name = node["name"]
        looked_up = r.lookup(name)
        # Free variable (lookup returns original name) that is a known corpus function
        if known_names and looked_up == name and name in known_names:
            return {"tag": "GlobalRef", "name": name}
        return {"tag": "Var", "name": looked_up}

    if tag in ("Con", "Lit", "Prim", "TyArg"):
        return node

    if tag == "App":
        return {
            "tag": "App",
            "fun": convert_expr(node["fun"], r, known_names),
            "args": [convert_expr(a, r, known_names) for a in node["args"]],
        }

    if tag == "Tuple":
        return {
            "tag": "Tuple",
            "elems": [convert_expr(e, r, known_names) for e in node["elems"]],
        }

    if tag == "Lam":
        new_binders = []
        n_val = 0
        for b in node["binders"]:
            if b["kind"] == "type":
                new_binders.append({"kind": "type"})
            else:
                r.push(b["name"])
                new_binders.append({"kind": "val"})
                n_val += 1
        new_body = convert_expr(node["body"], r, known_names)
        r.pop(n_val)
        return {"tag": "Lam", "binders": new_binders, "body": new_body}

    if tag == "Let":
        # Push all bind names simultaneously (supports mutual recursion)
        for bind in node["binds"]:
            r.push(bind["name"])
        new_binds = [{"val": convert_expr(bind["val"], r, known_names)} for bind in node["binds"]]
        new_body = convert_expr(node["body"], r, known_names)
        r.pop(len(node["binds"]))
        return {"tag": "Let", "binds": new_binds, "body": new_body}

    if tag == "Case":
        new_scrut = convert_expr(node["scrutinee"], r, known_names)
        new_alts = []
        for alt in node["alts"]:
            n_vars = len(alt["pat"].get("vars", []))
            new_pat = convert_pat(alt["pat"], r)   # pushes pattern vars onto r
            new_body = convert_expr(alt["body"], r, known_names)
            r.pop(n_vars)
            new_alts.append({"pat": new_pat, "body": new_body})
        return {"tag": "Case", "scrutinee": new_scrut, "alts": new_alts}

    raise ValueError(f"Unknown tag in convert_expr: {tag!r}")


def convert_pat(pat: dict, r: DeBruijnRenamer) -> dict:
    """Push pattern vars onto r and return a converted pat with arity (no var names)."""
    tag = pat["tag"]

    if tag == "PCon":
        for v in pat["vars"]:
            r.push(v)
        return {"tag": "PCon", "con": pat["con"], "arity": len(pat["vars"])}

    if tag == "PTuple":
        for v in pat["vars"]:
            r.push(v)
        return {"tag": "PTuple", "arity": len(pat["vars"])}

    if tag in ("PLit", "PWild"):
        return pat   # no binders

    raise ValueError(f"Unknown pattern tag: {tag!r}")


def debruijn_convert(fn: dict, known_names: set[str] | None = None) -> dict:
    """Convert fn to de Bruijn indexed form with hybrid lambda lifting.

    Known corpus function references (in known_names) become GlobalRef nodes.
    Unknown external references become outermost Lam binders (anonymous val binders).
    Self-recursive calls become the SELF token.
    deps metadata records which binder position maps to which unknown external function.
    """
    body = fn["body"]
    func_name = fn["name"]

    # Collect free variables in the raw body
    free_vars = collect_free_vars(body, set())
    has_self_call = func_name in free_vars
    external_deps = sorted(v for v in free_vars if v != func_name)

    # Partition deps: known corpus functions are named directly; unknowns are lambda-lifted
    if known_names:
        unknown_deps = [d for d in external_deps if d not in known_names]
    else:
        unknown_deps = external_deps

    # Set up renamer
    r = DeBruijnRenamer()
    if has_self_call:
        r._self_name = func_name

    # Only pre-bind unknown deps as outermost binders (pushed in sorted order)
    for dep_name in unknown_deps:
        r.push(dep_name)

    # Convert body — known deps become GlobalRef nodes, unknowns get de Bruijn indices
    new_body = convert_expr(body, r, known_names)

    # Build dep mapping (unknown deps only)
    dep_mapping = [{"param": i, "func": dep_name}
                   for i, dep_name in enumerate(unknown_deps)]

    # Wrap with an outer Lam for the lambda-lifted unknown deps
    if unknown_deps:
        dep_binders = [{"kind": "val"} for _ in unknown_deps]
        if new_body["tag"] == "Lam":
            new_body = {
                "tag": "Lam",
                "binders": dep_binders + new_body["binders"],
                "body": new_body["body"],
            }
        else:
            new_body = {"tag": "Lam", "binders": dep_binders, "body": new_body}

    return {**fn, "body": new_body, "deps": dep_mapping}


# ---------------------------------------------------------------------------
# Type normalization (Sprint 5)
# ---------------------------------------------------------------------------

# Type constructors that should NOT be treated as type variables.
_TYPE_CONSTRUCTORS = frozenset({
    "Int", "Integer", "Word", "Word8", "Word16", "Word32", "Word64",
    "Int8", "Int16", "Int32", "Int64", "Float", "Double",
    "Bool", "Char", "String", "Text", "ByteString",
    "Maybe", "Either", "IO", "ST", "STRef",
    "Map", "Set", "Seq", "Vector", "Array",
    "List", "Void", "Unit",
})

# Regex to split a type string into tokens: arrows, parens, brackets, words.
_TYPE_TOKEN_RE = re.compile(r"->|[()\[\]]|[^\s()\[\]]+")


def normalize_type(type_str: str) -> list[str]:
    """Normalize a GHC type string to a canonical token list.

    Steps:
      1. Strip ``forall ... .`` quantifier prefixes.
      2. Strip class constraints ``Ctx a =>`` or ``(Ctx a, ..) =>``.
      3. Tokenize around ``->``, ``(``, ``)``, ``[``, ``]``, whitespace.
      4. Canonicalize type variable names: first occurrence → a, second → b, ...
    """
    if not type_str or not type_str.strip():
        return ["<unk>"]

    s = type_str.strip()

    # 1. Strip forall quantifiers (possibly repeated).
    while s.startswith("forall"):
        dot = s.find(".")
        if dot == -1:
            break
        s = s[dot + 1:].strip()

    # 2. Strip class constraints: anything before a bare '=>'
    #    Match either "(... ) =>" or "Word =>" style.
    constraint_re = re.compile(
        r"^(?:\([^)]*\)\s*=>|(?:[A-Z]\w*(?:\s+[\w]+)*)\s*=>)\s*"
    )
    # Apply repeatedly for multiple constraints.
    while True:
        m = constraint_re.match(s)
        if not m:
            break
        s = s[m.end():]

    # 3. Tokenize.
    raw_tokens = _TYPE_TOKEN_RE.findall(s)
    if not raw_tokens:
        return ["<unk>"]

    # 4. Canonicalize type variables.
    _canon_names = ["a", "b", "c", "d", "e", "f"]
    var_map: dict[str, str] = {}

    def _canon(tok: str) -> str:
        # Type variable: single lowercase-starting identifier, not a constructor.
        if re.fullmatch(r"[a-z]\w*", tok) and tok not in _TYPE_CONSTRUCTORS:
            if tok not in var_map:
                idx = len(var_map)
                var_map[tok] = _canon_names[idx] if idx < len(_canon_names) else tok
            return var_map[tok]
        return tok

    return [_canon(t) for t in raw_tokens]


# ---------------------------------------------------------------------------
# Depth-first token serialization
# ---------------------------------------------------------------------------

def serialize_expr(node: dict, tokens: list[str]) -> None:
    tag = node["tag"]

    if tag == "GlobalRef":
        tokens.append(node["name"])   # named corpus function — no Var prefix
        return

    if tag == "Var":
        tokens.append("Var")
        tokens.append(node["name"])   # de Bruijn index string or "SELF"
        return

    if tag == "Con":
        tokens.append("Con")
        tokens.append(node["name"])
        return

    if tag == "Lit":
        tokens.append("Lit")
        tokens.append(str(node["val"]))
        return

    if tag == "Prim":
        tokens.append("Prim")
        tokens.append(node["op"])
        return

    if tag == "TyArg":
        tokens.append("TyArg")
        tokens.append(node["type"].replace(" ", ""))
        return

    if tag == "App":
        tokens.append("(App")
        serialize_expr(node["fun"], tokens)
        for a in node["args"]:
            serialize_expr(a, tokens)
        tokens.append(")")
        return

    if tag == "Tuple":
        tokens.append("(Tuple")
        for e in node["elems"]:
            serialize_expr(e, tokens)
        tokens.append(")")
        return

    if tag == "Lam":
        tokens.append("(Lam")
        n_val = 0
        for b in node["binders"]:
            if b["kind"] == "type":
                tokens.append("@T")
            else:
                n_val += 1
        if n_val > 0:
            tokens.append(str(n_val))   # emit val binder count as single token
        serialize_expr(node["body"], tokens)
        tokens.append(")")
        return

    if tag == "Let":
        tokens.append("(Let")
        for bind in node["binds"]:
            tokens.append("(Bind")
            serialize_expr(bind["val"], tokens)
            tokens.append(")")
        serialize_expr(node["body"], tokens)
        tokens.append(")")
        return

    if tag == "Case":
        tokens.append("(Case")
        serialize_expr(node["scrutinee"], tokens)
        for alt in node["alts"]:
            tokens.append("(Alt")
            serialize_pat(alt["pat"], tokens)
            serialize_expr(alt["body"], tokens)
            tokens.append(")")
        tokens.append(")")
        return

    raise ValueError(f"Unknown tag in serialize_expr: {tag!r}")


def serialize_pat(pat: dict, tokens: list[str]) -> None:
    tag = pat["tag"]

    if tag == "PCon":
        tokens.append("(PCon")
        tokens.append(pat["con"])
        if pat["arity"] > 0:
            tokens.append(str(pat["arity"]))   # binder count
        tokens.append(")")
        return

    if tag == "PTuple":
        tokens.append("(PTuple")
        tokens.append(str(pat["arity"]))       # always emit count (≥2 for tuples)
        tokens.append(")")
        return

    if tag == "PLit":
        tokens.append("(PLit")
        tokens.append(str(pat["val"]))
        tokens.append(")")
        return

    if tag == "PWild":
        tokens.append("(PWild)")
        return

    raise ValueError(f"Unknown pattern tag in serialize_pat: {tag!r}")


def _is_global_ref(tok: str) -> bool:
    """True if tok is a named corpus function reference (e.g. fLength, fMap)."""
    return len(tok) >= 2 and tok[0] == 'f' and tok[1].isupper()


def extract_uses(body_tokens: list[str]) -> list[str]:
    """Extract GlobalRef names from body tokens, in order of first appearance."""
    seen: set[str] = set()
    uses: list[str] = []
    for tok in body_tokens:
        if _is_global_ref(tok) and tok not in seen:
            seen.add(tok)
            uses.append(tok)
    return uses


def to_sequence(fn: dict, known_names: set[str] | None = None) -> dict:
    """De Bruijn convert (with hybrid lambda lifting) then serialize to token sequence + deps.

    Sprint 5b format: DEF <name> TY <type_tokens> BODY <body_tokens> EOF
    Sprint 10: DEF <name> TY <type_tokens> USES <refs> BODY <body_tokens> EOF
    The function name disambiguates same-type functions; type conditioning is preserved.
    Known corpus function names appear as named tokens in the body (no lambda lifting).
    USES section lists GlobalRef names used in the body (chain-of-thought for compositions).
    """
    converted = debruijn_convert(fn, known_names)
    name_token = fn.get("name", "<unk>")
    type_tokens = normalize_type(fn.get("type", ""))
    body_tokens: list[str] = []
    serialize_expr(converted["body"], body_tokens)
    uses = extract_uses(body_tokens)
    # Sprint 10: USES section always present. Primitives get USES BODY (empty),
    # compositions get USES fX fY BODY. Model must learn both patterns.
    tokens: list[str] = ["DEF", name_token, "TY"] + type_tokens + ["USES"] + uses + ["BODY"] + body_tokens + ["EOF"]
    return {
        "tokens": tokens,
        "deps": converted.get("deps", []),
        "type": fn.get("type", ""),
        "name": name_token,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="De Bruijn convert + serialize ASTs")
    p.add_argument("input", nargs="?",
                   default="sprint/extract/corpus_raw.json")
    p.add_argument("--out", default="sprint/corpus/sequences.json")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    data = json.loads(Path(args.input).read_text())

    # Load corpus function names for named global reference (hybrid de Bruijn)
    from sprint.corpus.tokenizer import _CORPUS_FUNCTION_NAMES as known_names

    sequences = []
    for fn in data:
        if not fn.get("ok"):
            continue
        result = to_sequence(fn, known_names)
        entry = {
            "name": fn["name"],
            "tokens": result["tokens"],
            "deps": result["deps"],
            "type": result["type"],
        }
        if "package" in fn:
            entry["package"] = fn["package"]
        sequences.append(entry)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(sequences, indent=2))

    if args.stats:
        lengths = [len(s["tokens"]) for s in sequences]
        vocab = set(t for s in sequences for t in s["tokens"])
        print(f"[normalize] {len(sequences)} sequences")
        print(f"[normalize] token lengths: min={min(lengths)} "
              f"avg={sum(lengths)//len(lengths)} max={max(lengths)}")
        print(f"[normalize] vocabulary size: {len(vocab)}")

    print(f"[normalize] wrote {len(sequences)} sequences → {args.out}")


if __name__ == "__main__":
    main()
