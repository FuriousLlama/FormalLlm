"""Convert Lean 4 function definitions into standalone `exact` expressions.

Handles two syntactic patterns that extract_stdlib.py filters out:
  1. Equation compiler → `exact match ... with | pat => expr`
  2. `where` clause → `exact let rec helper ... ; main_expr`

Does NOT use Pantograph for conversion (env_inspect returns kernel-level
brecOn expressions, too low-level for training data). Instead does
source-level textual transformation. Pantograph is used separately for
verification (goal_start + goal_tactic).

Usage:
    from lean_def_converter import parse_definitions, convert_to_exact
    defs = parse_definitions(Path("some_file.lean"))
    for d in defs:
        expr = convert_to_exact(d)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path


class DefKind(Enum):
    SIMPLE = auto()      # body is a single expression after :=
    EQCOMP = auto()      # equation compiler (pattern-matching clauses)
    WHERE = auto()        # has a where clause
    DO = auto()           # do notation (skip)
    INDUCTIVE = auto()    # inductive type (skip)
    SKIP = auto()         # unrecognizable or too complex


@dataclass
class EqClause:
    """One clause of the equation compiler: | pat1, pat2 => expr"""
    patterns: str   # everything between | and =>
    body: str       # everything after =>


@dataclass
class WhereDef:
    """A helper defined in a where clause."""
    name: str
    signature: str       # type annotation (may be empty)
    clauses: list[EqClause]
    raw_body: str        # raw body if not equation compiler style


@dataclass
class LeanDef:
    """A parsed Lean function definition."""
    name: str
    full_name: str       # e.g. "List.partition" for qualified lookup
    kind: DefKind
    params: str          # raw parameter string: "{α} [BEq α] (xs : List α)"
    return_type: str     # raw return type
    # For SIMPLE:
    body: str = ""
    # For EQCOMP:
    eq_clauses: list[EqClause] = field(default_factory=list)
    eq_match_args: str = ""  # inferred argument names for match
    # For WHERE:
    main_expr: str = ""
    where_defs: list[WhereDef] = field(default_factory=list)
    # Metadata
    is_recursive: bool = False
    source_file: str = ""
    line_number: int = 0
    raw_text: str = ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Top-level def start pattern
_DEF_START = re.compile(
    r'^(?:@\[.*?\]\s*)*'              # optional attributes
    r'(?:private\s+|protected\s+|nonrec\s+|noncomputable\s+)*'
    r'def\s+'
    r'([\w][\w.\']*)'                  # function name
    r'(.*)',                            # rest of line
    re.DOTALL
)

# Other top-level declarations that signal end of a def
_TOPLEVEL = re.compile(
    r'^(?:@\[.*?\]\s*)*'
    r'(?:private\s+|protected\s+|nonrec\s+|noncomputable\s+)*'
    r'(?:def|theorem|lemma|inductive|structure|class|instance|axiom|opaque|'
    r'section|namespace|end|open|variable|#|import|set_option|attribute)\b'
)


def _get_indent(line: str) -> int:
    """Return number of leading spaces."""
    return len(line) - len(line.lstrip())


def _find_def_blocks(text: str) -> list[tuple[str, int, list[str]]]:
    """Split file into (name, line_number, lines) for each def."""
    lines = text.split('\n')
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        # Skip blank lines, comments
        if not stripped or stripped.startswith('--'):
            i += 1
            continue

        m = _DEF_START.match(stripped)
        if m and not stripped.startswith('--'):
            name = m.group(1)
            block_lines = [line]
            j = i + 1
            # Collect until next top-level declaration or end of file
            while j < len(lines):
                next_line = lines[j]
                next_stripped = next_line.lstrip()
                # Blank line followed by non-indented content = boundary
                if not next_stripped:
                    # Look ahead: if next non-blank is top-level, stop
                    k = j + 1
                    while k < len(lines) and not lines[k].strip():
                        k += 1
                    if k >= len(lines) or (lines[k].strip() and _get_indent(lines[k]) == 0
                                           and _TOPLEVEL.match(lines[k].lstrip())):
                        break
                elif _get_indent(next_line) == 0 and _TOPLEVEL.match(next_stripped):
                    break
                block_lines.append(next_line)
                j += 1
            blocks.append((name, i + 1, block_lines))
            i = j
        else:
            i += 1

    return blocks


def _classify_and_parse(name: str, block_lines: list[str]) -> LeanDef:
    """Classify a def block and extract its components."""
    raw = '\n'.join(block_lines)
    result = LeanDef(
        name=name,
        full_name=name,
        kind=DefKind.SKIP,
        params="",
        return_type="",
        raw_text=raw,
    )

    # Join the first line (which has the def keyword) to get the signature
    # Find where the signature ends and body begins
    joined = raw

    # Check for 'do' notation
    if re.search(r':=\s*do\b', joined) or re.search(r'=>\s*do\b', joined):
        result.kind = DefKind.DO
        return result

    # Check for 'where' clause
    where_match = re.search(r'^(\s*)where\s*$', joined, re.MULTILINE)
    if not where_match:
        # where can also appear at end of a line: `:= expr where`
        where_match = re.search(r'\bwhere\s*$', joined, re.MULTILINE)

    has_assign = ':=' in joined.split('\n')[0] or any(
        ':=' in line for line in block_lines[:3]
    )

    # Find equation compiler clauses (| pat => expr at consistent indent)
    eq_clause_lines = []
    body_start_idx = None
    for idx, line in enumerate(block_lines):
        stripped = line.lstrip()
        if stripped.startswith('|') and '=>' in stripped:
            eq_clause_lines.append((idx, line))
            if body_start_idx is None:
                body_start_idx = idx

    # Parse the signature: everything from 'def name' up to ':=' or first '|'
    sig_text = _extract_signature(block_lines)
    params, ret_type = _parse_signature(sig_text, name)
    result.params = params
    result.return_type = ret_type

    if where_match:
        result.kind = DefKind.WHERE
        _parse_where(result, block_lines, where_match)
    elif eq_clause_lines and not has_assign:
        result.kind = DefKind.EQCOMP
        _parse_eqcomp(result, block_lines, eq_clause_lines)
    elif has_assign:
        result.kind = DefKind.SIMPLE
        _parse_simple(result, block_lines)
    else:
        result.kind = DefKind.SKIP

    # Check if recursive
    result.is_recursive = name in raw.split(f'def {name}', 1)[-1]

    return result


def _extract_signature(block_lines: list[str]) -> str:
    """Extract the function signature (params + return type) from block."""
    sig_parts = []
    for line in block_lines:
        stripped = line.lstrip()
        if stripped.startswith('|'):
            break
        sig_parts.append(stripped)
        if ':=' in stripped:
            # Take up to :=
            combined = ' '.join(sig_parts)
            return combined.split(':=')[0]
    return ' '.join(sig_parts)


def _parse_signature(sig_text: str, name: str) -> tuple[str, str]:
    """Parse 'def name {α} [BEq α] (xs : List α) : RetType' into (params, ret_type)."""
    # Remove 'def name' prefix (with possible qualifiers)
    cleaned = re.sub(
        r'^(?:@\[.*?\]\s*)*(?:private\s+|protected\s+|nonrec\s+|noncomputable\s+)*'
        r'def\s+[\w.\']+\s*',
        '', sig_text
    ).strip()

    # Split on the LAST top-level `:` that isn't inside brackets
    # This separates params from return type
    depth = 0
    last_colon = -1
    for i, ch in enumerate(cleaned):
        if ch in '({[':
            depth += 1
        elif ch in ')}]':
            depth -= 1
        elif ch == ':' and depth == 0:
            last_colon = i

    if last_colon >= 0:
        params = cleaned[:last_colon].strip()
        ret_type = cleaned[last_colon + 1:].strip()
    else:
        params = cleaned
        ret_type = ""

    return params, ret_type


def _parse_simple(result: LeanDef, block_lines: list[str]) -> None:
    """Parse a simple def with := body."""
    joined = '\n'.join(block_lines)
    # Find := and take everything after
    parts = joined.split(':=', 1)
    if len(parts) == 2:
        body = parts[1].strip()
        # Remove trailing where if present (shouldn't be for SIMPLE)
        result.body = body


def _parse_eqcomp(result: LeanDef, block_lines: list[str],
                   clause_lines: list[tuple[int, str]]) -> None:
    """Parse equation compiler clauses."""
    clauses = []
    for i, (idx, line) in enumerate(clause_lines):
        stripped = line.lstrip()
        # Remove leading |
        content = stripped[1:].strip()

        # Collect continuation lines (indented more than the |)
        clause_indent = _get_indent(line)
        full_parts = [content]
        j = idx + 1
        while j < len(block_lines):
            next_line = block_lines[j]
            if not next_line.strip():
                j += 1
                continue
            if _get_indent(next_line) > clause_indent and not next_line.lstrip().startswith('|'):
                full_parts.append(next_line.strip())
                j += 1
            else:
                break

        full_content = ' '.join(full_parts)

        # Split on the FIRST top-level =>
        pats, body = _split_arrow(full_content)
        clauses.append(EqClause(patterns=pats.strip(), body=body.strip()))

    result.eq_clauses = clauses

    # Infer argument names for the match expression
    # Look at the return type — if it's like "List α → List α → Bool",
    # the function takes extra args beyond explicit params
    result.eq_match_args = _infer_match_args(result)


def _parse_where(result: LeanDef, block_lines: list[str],
                 where_match: re.Match) -> None:
    """Parse a def with a where clause."""
    joined = '\n'.join(block_lines)
    where_pos = where_match.start()

    # Split into before-where and after-where
    before = joined[:where_pos]
    after = joined[where_pos:]

    # Main expression: everything between := and where
    if ':=' in before:
        main = before.split(':=', 1)[1].strip()
        # Remove trailing 'where' if it's on the same line
        main = re.sub(r'\s+where\s*$', '', main).strip()
        result.main_expr = main

    # Parse where block helpers
    where_lines = after.split('\n')[1:]  # skip the 'where' line itself
    helpers = _parse_where_helpers(where_lines)
    result.where_defs = helpers


def _parse_where_helpers(lines: list[str]) -> list[WhereDef]:
    """Parse helper definitions from a where block."""
    # First pass: strip doc comments (/-- ... -/) which span multiple lines
    cleaned = _strip_doc_comments(lines)

    helpers = []
    i = 0
    while i < len(cleaned):
        line = cleaned[i]
        stripped = line.lstrip()

        if not stripped or stripped.startswith('--'):
            i += 1
            continue

        # Skip standalone attributes (next line has the actual helper)
        if stripped.startswith('@[') and ':' not in stripped.split(']', 1)[-1]:
            i += 1
            continue

        # A helper def starts with an identifier (possibly after @[attr])
        # followed by : or ( or | on this or subsequent lines
        # Must look like: `name : Type` or `name (args) : Type`
        attr_stripped = re.sub(r'^@\[.*?\]\s*', '', stripped)
        helper_match = re.match(r'(\w[\w\']*)\s*(.*)', attr_stripped)
        if helper_match and not stripped.startswith('|'):
            h_name = helper_match.group(1)
            rest = helper_match.group(2)

            # Must have a colon (type annotation) or parens (args) to be a real helper
            # This filters out doc comment fragments like 'if', 'the', 'by', etc.
            has_colon = ':' in rest
            has_paren = rest.startswith('(')
            if not has_colon and not has_paren:
                i += 1
                continue

            # Collect the full helper block
            h_lines = [line]
            j = i + 1
            base_indent = _get_indent(line)
            while j < len(cleaned):
                next_line = cleaned[j]
                if not next_line.strip():
                    j += 1
                    continue
                if _get_indent(next_line) > base_indent or next_line.lstrip().startswith('|'):
                    h_lines.append(next_line)
                    j += 1
                else:
                    break

            # Parse helper's signature and clauses
            helper = _parse_single_helper(h_name, h_lines)
            if helper:
                helpers.append(helper)
            i = j
        else:
            i += 1

    return helpers


def _strip_doc_comments(lines: list[str]) -> list[str]:
    """Remove /-- ... -/ doc comments that may span multiple lines."""
    result = []
    in_comment = False
    for line in lines:
        if in_comment:
            if '-/' in line:
                # End of doc comment — keep the part after -/
                after = line.split('-/', 1)[1]
                if after.strip():
                    result.append(' ' * _get_indent(line) + after.strip())
                in_comment = False
            # else: skip entire line (inside comment)
        elif '/--' in line:
            before = line.split('/--', 1)[0]
            if '-/' in line.split('/--', 1)[1]:
                # Single-line doc comment
                after = line.split('/--', 1)[1].split('-/', 1)[1]
                combined = before + after
                if combined.strip():
                    result.append(combined)
            else:
                # Multi-line doc comment starts
                if before.strip():
                    result.append(before)
                in_comment = True
        else:
            result.append(line)
    return result


def _parse_single_helper(name: str, lines: list[str]) -> WhereDef | None:
    """Parse a single where helper definition."""
    joined = '\n'.join(lines)
    # Strip attributes from first line for signature matching
    stripped_first = re.sub(r'^@\[.*?\]\s*', '', lines[0].lstrip())

    # Extract type signature
    # Pattern: name : Type → Type → Type
    # Also handle: name (args) : Type → Type
    sig_match = re.match(
        re.escape(name) + r'\s*(?:\([^)]*\)\s*)*:\s*(.+?)(?:\s*:=.*)?$',
        stripped_first
    )
    signature = sig_match.group(1).strip() if sig_match else ""
    # Clean trailing := from signature
    signature = re.sub(r'\s*:=\s*$', '', signature).strip()

    # Collect equation compiler clauses with their full bodies
    # (including continuation lines for nested match/if)
    clauses = []
    raw_body = ""

    clause_indices = []
    for idx, line in enumerate(lines):
        s = line.lstrip()
        if s.startswith('|') and '=>' in s:
            clause_indices.append(idx)

    for ci, idx in enumerate(clause_indices):
        s = lines[idx].lstrip()
        content = s[1:].strip()  # remove leading |
        pats, body = _split_arrow(content)

        # Collect continuation lines until next clause or end
        next_idx = clause_indices[ci + 1] if ci + 1 < len(clause_indices) else len(lines)
        clause_indent = _get_indent(lines[idx])
        for j in range(idx + 1, next_idx):
            cont = lines[j]
            if not cont.strip():
                continue
            if _get_indent(cont) > clause_indent:
                body += ' ' + cont.strip()

        clauses.append(EqClause(patterns=pats.strip(), body=body.strip()))

    if not clauses:
        # Maybe it's a := body
        if ':=' in joined:
            raw_body = joined.split(':=', 1)[1].strip()

    return WhereDef(
        name=name,
        signature=signature,
        clauses=clauses,
        raw_body=raw_body,
    )


def _split_arrow(text: str) -> tuple[str, str]:
    """Split 'patterns => body' on the first top-level '=>'."""
    depth = 0
    i = 0
    while i < len(text) - 1:
        ch = text[i]
        if ch in '({[':
            depth += 1
        elif ch in ')}]':
            depth -= 1
        elif ch == '=' and i + 1 < len(text) and text[i + 1] == '>' and depth == 0:
            return text[:i].strip(), text[i + 2:].strip()
        i += 1
    return text, ""


def _infer_match_args(result: LeanDef) -> str:
    """Infer argument variable names for the match from clause patterns.

    If clauses match on two arguments (e.g. | [], _ => ...; | a::as, b::bs => ...),
    we need 'match arg1, arg2 with'.

    Generate simple placeholder names: x₁, x₂, etc.
    """
    if not result.eq_clauses:
        return ""

    # Count commas in the first clause's pattern to determine arity
    first_pat = result.eq_clauses[0].patterns
    # Split on top-level commas
    depth = 0
    parts = []
    current: list[str] = []
    for ch in first_pat:
        if ch in '({[':
            depth += 1
            current.append(ch)
        elif ch in ')}]':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    parts.append(''.join(current).strip())

    n = len(parts)
    if n == 1:
        return "x_arg"
    return ", ".join(f"x_arg{i}" for i in range(n))


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def extract_explicit_params(d: LeanDef) -> list[str]:
    """Extract explicit parameter names from a definition's signature.

    Returns list of parameter names that appear in the body (e.g., ['p', 'as']).
    """
    params_str = d.params.strip()
    if not params_str:
        return []

    names = []
    i = 0
    while i < len(params_str):
        ch = params_str[i]
        if ch in '({[':
            # Find matching close bracket, handling nesting
            close = {'{': '}', '(': ')', '[': ']'}[ch]
            depth = 1
            j = i + 1
            while j < len(params_str) and depth > 0:
                if params_str[j] == ch:
                    depth += 1
                elif params_str[j] == close:
                    depth -= 1
                j += 1
            if ch == '(':
                inner = params_str[i + 1:j - 1].strip()
                if ':' in inner:
                    name_part = inner.split(':', 1)[0].strip()
                    for n in name_part.split():
                        if n.isidentifier():
                            names.append(n)
            i = j
        else:
            i += 1

    return names


def convert_to_exact(d: LeanDef, wrap_lambda: bool = True) -> str | None:
    """Convert a parsed definition to a standalone `exact` expression.

    Args:
        d: The parsed definition
        wrap_lambda: If True, wrap in fun <params> => for definitions whose
            body references parameter names. Required for verification when
            the goal type includes the parameter types.

    Returns the expression string (without the 'exact ' prefix), or None
    if conversion is not possible.
    """
    if d.kind == DefKind.SIMPLE:
        body = _convert_simple(d)
    elif d.kind == DefKind.EQCOMP:
        body = _convert_eqcomp(d)
    elif d.kind == DefKind.WHERE:
        body = _convert_where(d)
    else:
        return None

    if body is None:
        return body

    # Optionally wrap in lambda to bind parameter names
    if wrap_lambda and d.kind in (DefKind.SIMPLE, DefKind.WHERE):
        params = extract_explicit_params(d)
        if params:
            body = f"fun {' '.join(params)} => {body}"

    if wrap_lambda and d.kind == DefKind.EQCOMP:
        # EQCOMP uses generated match args — wrap those too
        match_args = d.eq_match_args
        if match_args:
            arg_names = [a.strip() for a in match_args.split(',')]
            body = f"fun {' '.join(arg_names)} => {body}"

    return body


def _simple_candidate_forms(body: str) -> list[str]:
    """Emit multiple candidate collapsed forms for a multi-line body.

    The caller tries them in order and keeps the first that verifies.
    This is a hack to sidestep Lean's layout rules which can't be
    correctly applied from inside a Pantograph `exact <tactic>` context
    where the expression is reparsed at an arbitrary column.

    Three candidates:
      1. newline-preserved (dedented) — works for bodies where the let
         body is at the same col as the let keyword (post-dedent):

             let f := e1
             body_expr

         Lean accepts this via layout rule: body_expr at col 0 ≥ let at col 0.

      2. semicolon-joined — one line with `;` between what we guess are
         top-level statements (lines at base indent). Works for multi-let
         chains, and sometimes for cases where #1 fails.

      3. space-joined — one line, all whitespace collapsed. Works for
         defs where the newlines are purely cosmetic (pretty-printed long
         expressions that would be valid on one line).

    Each candidate is independently valid-or-not; the caller verifies
    each until one passes.
    """
    import textwrap
    dedented = textwrap.dedent(body).rstrip()
    lines = [l for l in dedented.split('\n') if l.strip()]
    if not lines:
        return []

    # Candidate 1: newline-preserved (drop blank lines, rstrip each)
    form1 = '\n'.join(l.rstrip() for l in lines)

    # Candidate 2: semicolon-joined — insert `;` before every line at the
    # base indent (the first line's indent), join continuations with spaces
    base_indent = len(lines[0]) - len(lines[0].lstrip())
    stmts: list[str] = [lines[0].strip()]
    for line in lines[1:]:
        line_indent = len(line) - len(line.lstrip())
        if line_indent == base_indent:
            stmts.append(line.strip())
        else:
            stmts[-1] += ' ' + line.strip()
    form2 = '; '.join(stmts)

    # Candidate 3: space-joined (maximally aggressive collapse)
    form3 = ' '.join(l.strip() for l in lines)

    # Dedup while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for f in (form1, form2, form3):
        if f not in seen:
            seen.add(f)
            result.append(f)
    return result


def _convert_simple(d: LeanDef) -> str | None:
    """Simple defs: body is already an expression.

    Sprint 29 fix: returns a "\\x00-joined" list of candidate forms via
    _simple_candidate_forms. The first candidate is the canonical
    newline-preserved form; subsequent candidates are alternative
    collapses for bodies where layout rules can't be reconstructed.

    The caller (convert_to_exact) treats the return value as a single
    string and doesn't see the alternatives — only verify_with_pantograph
    in extract_lean_defs.py knows how to try them. To keep the legacy
    API working, this function still returns a string, but that string
    is the FIRST candidate. Callers that want multi-candidate behavior
    use _convert_simple_multi (below).
    """
    body = d.body.strip()
    if not body:
        return None
    candidates = _simple_candidate_forms(body)
    if not candidates:
        return None
    primary = candidates[0]
    primary = _qualify_library_calls(primary)
    return primary


def _convert_simple_multi(d: LeanDef) -> list[str]:
    """Return all candidate forms for a simple def, qualified.

    Used by callers that want to try multiple forms (extract_lean_defs
    verify step) before giving up on a def.
    """
    body = d.body.strip()
    if not body:
        return []
    candidates = _simple_candidate_forms(body)
    return [_qualify_library_calls(c) for c in candidates]


def _convert_eqcomp(d: LeanDef) -> str | None:
    """Equation compiler → match expression.

    def f : A → B → C
      | pat1 => expr1
      | pat2 => expr2

    becomes:

    match x✝0, x✝1 with | pat1 => expr1 | pat2 => expr2
    """
    if not d.eq_clauses:
        return None

    match_args = d.eq_match_args
    if not match_args:
        return None

    arms = " | ".join(
        f"{c.patterns} => {c.body}" for c in d.eq_clauses
    )

    expr = f"match {match_args} with | {arms}"

    # If recursive, qualify self-references
    if d.is_recursive:
        expr = _qualify_self_refs(expr, d.name, d.full_name)

    return expr


def _convert_where(d: LeanDef) -> str | None:
    """Where clause → let rec expression.

    def f (args) : T := main_expr where
      helper : HType
      | pat1 => expr1
      | pat2 => expr2

    becomes:

    let rec helper : HType | pat1 => expr1 | pat2 => expr2; main_expr
    """
    if not d.where_defs or not d.main_expr:
        return None

    parts = []
    for helper in d.where_defs:
        if helper.clauses:
            arms = " | ".join(
                f"{c.patterns} => {c.body}" for c in helper.clauses
            )
            sig = f" : {helper.signature}" if helper.signature else ""
            parts.append(f"let rec {helper.name}{sig} | {arms}")
        elif helper.raw_body:
            sig = f" : {helper.signature}" if helper.signature else ""
            parts.append(f"let rec {helper.name}{sig} := {helper.raw_body}")
        else:
            return None  # Can't convert this helper

    main = d.main_expr.strip()
    # Chain: let rec h1 ...; let rec h2 ...; main
    full = "; ".join(parts) + "; " + main

    return full


def _qualify_self_refs(expr: str, short_name: str, full_name: str) -> str:
    """Replace unqualified recursive calls with the library-qualified name.

    e.g. 'isPrefixOf as bs' → 'List.isPrefixOf as bs'
    Only replaces when the name appears as a standalone identifier,
    and is NOT already qualified.
    """
    if short_name == full_name:
        return expr
    # Match the short name at word boundaries, but NOT preceded by a dot
    # (which would mean it's already qualified like List.bagInter)
    return re.sub(
        r'(?<!\.)(?<!\w)\b' + re.escape(short_name) + r'\b',
        full_name,
        expr
    )


# Library functions that need List. qualification when used standalone
_LIST_FUNCTIONS = {
    "foldr", "foldl", "map", "filter", "filterMap", "flatMap",
    "reverse", "length", "take", "drop", "takeWhile", "dropWhile",
    "zip", "zipWith", "flatten", "join", "range", "replicate",
    "head", "tail", "getLast", "append", "partition", "intersperse",
    "eraseDups", "mergeSort", "scanl", "scanr", "enum", "zipIdx",
    "isPrefixOf", "isSuffixOf", "isSublist", "splitAt",
    "elem", "erase", "insert", "intercalate", "isEmpty",
    "reduceOption", "headD", "rotateLeft", "rotateRight",
    "inits", "tails", "sublists", "sections",
    "cons", "nil", "pure",
}


def _qualify_library_calls(body: str) -> str:
    """Add List. prefix to known library function calls that appear unqualified."""
    for fn in _LIST_FUNCTIONS:
        # Match at word boundary, not already preceded by dot or List.
        body = re.sub(
            r'(?<!\.)(?<!\w)\b' + re.escape(fn) + r'\b',
            f'List.{fn}' if fn not in ('pure', 'cons', 'nil') else fn,
            body
        )
    return body


# ---------------------------------------------------------------------------
# File-level extraction
# ---------------------------------------------------------------------------

def parse_definitions(path: Path, module_prefix: str = "") -> list[LeanDef]:
    """Parse all function definitions from a .lean file.

    Args:
        path: Path to the .lean file
        module_prefix: Prefix for qualified names (e.g. "List" → "List.funcName")

    Sprint 29 fix: strip `/-- ... -/` doc comments from the file text
    BEFORE `_find_def_blocks` runs. The original implementation only
    stripped doc comments inside where clauses (via `_parse_where_helpers`),
    which meant doc-comment blocks between two top-level defs were treated
    as a continuation of the preceding def's body, producing bodies like
    `(l.foldr f #[[]]).toList /-- doc for next def -/` that the converter
    then emitted as invalid Lean. Stripping at the file level fixes this.
    """
    text = path.read_text(errors='replace')
    # Strip file-level doc comments so they don't bleed into def bodies.
    # `_strip_doc_comments` takes a list of lines and returns a list of
    # lines with `/-- ... -/` blocks removed (handles multi-line cases).
    text = '\n'.join(_strip_doc_comments(text.split('\n')))
    blocks = _find_def_blocks(text)

    defs = []
    for name, line_num, block_lines in blocks:
        d = _classify_and_parse(name, block_lines)
        d.source_file = str(path)
        d.line_number = line_num
        if module_prefix:
            d.full_name = f"{module_prefix}.{name}"
        defs.append(d)

    return defs


def parse_and_convert(path: Path, module_prefix: str = "") -> list[tuple[LeanDef, str]]:
    """Parse definitions and convert them to exact expressions.

    Returns list of (LeanDef, expression) tuples for successfully converted defs.
    """
    defs = parse_definitions(path, module_prefix)
    results = []
    for d in defs:
        expr = convert_to_exact(d)
        if expr is not None:
            results.append((d, expr))
    return results
