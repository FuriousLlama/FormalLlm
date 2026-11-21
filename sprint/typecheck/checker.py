"""
checker.py — Structural parser and bidirectional type checker for serialized ASTs.

Token grammar (S-expression prefix form, de Bruijn indexed):
  sequence := DEF name expr EOF
  expr     := Var (int | name)           -- de Bruijn index or free variable
             | Con con_name
             | Lit int                   -- integer literal value
             | TyArg type_tok
             | '(' App expr+ ')'
             | '(' Lam '@T'* int? expr ')'   -- type binders then val count
             | '(' Let '(' Bind expr ')' + expr ')'   -- no binder names
             | '(' Case expr '(' Alt pat expr ')' + ')'
             | '(' Tuple expr+ ')'
  pat      := '(' PCon con_name int? ')'     -- optional arity count
             | '(' PTuple int ')'            -- arity count
             | '(' PLit int ')'
             | '(' PWild ')'

Public API:
  is_type_valid(partial_tokens, next_token) -> bool
  type_check_sequence(tokens) -> (bool, str)
"""

from __future__ import annotations
from dataclasses import dataclass

# ── Token sets ────────────────────────────────────────────────────────────────

EXPR_STARTERS = frozenset({
    'Var', 'Con', 'Lit', 'Prim', 'TyArg',
    '(App', '(Lam', '(Let', '(Case', '(Tuple',
})

BINDER_TOKENS = frozenset({'@T'})   # only type binders; val binder count is an integer token

CONSTRUCTOR_NAMES = frozenset({
    ':', '[]', 'True', 'False', 'Just', 'Nothing', '(,)', '(,,)',
})


def _is_int_token(s: str) -> bool:
    """True if s is an integer token (de Bruijn index, literal value, or binder count)."""
    if not s:
        return False
    stripped = s.lstrip('-')
    return bool(stripped) and stripped.isdigit()

PAT_STARTERS = frozenset({'(PCon', '(PTuple', '(PLit', '(PWild)'})

PRIM_OPS = frozenset({
    '+', '-', '*', 'div', 'mod',
    '==', '/=', '<', '<=', '>', '>=',
    '&&', '||', 'not',
    'negate', 'abs', 'signum',
})

# Tokens that structurally cannot follow TyArg or Var
_STRUCTURAL = frozenset({
    'Var', 'Con', 'Lit', 'Prim', 'TyArg', '(App', '(Lam', '(Let', '(Case', '(Tuple',
    '(Alt', '(Bind', '(PCon', '(PTuple', '(PLit', '(PWild)', ')', 'EOF',
})

# ── Types ─────────────────────────────────────────────────────────────────────

class Type: pass

@dataclass(frozen=True)
class IntT(Type):    repr = lambda self: "Int"
@dataclass(frozen=True)
class BoolT(Type):   repr = lambda self: "Bool"
@dataclass(frozen=True)
class ListT(Type):
    elem: Type
@dataclass(frozen=True)
class MaybeT(Type):
    elem: Type
@dataclass(frozen=True)
class FunT(Type):
    arg: Type
    ret: Type
@dataclass(frozen=True)
class UnknownT(Type): pass

INT, BOOL, UNKNOWN = IntT(), BoolT(), UnknownT()

CONSTRUCTOR_TYPES: dict[str, Type] = {
    'True':     BOOL,
    'False':    BOOL,
    '[]':       ListT(UNKNOWN),
    'Nothing':  MaybeT(UNKNOWN),
    ':':        FunT(UNKNOWN, FunT(ListT(UNKNOWN), ListT(UNKNOWN))),
    'Just':     FunT(UNKNOWN, MaybeT(UNKNOWN)),
    '(,)':      UNKNOWN,
    '(,,)':     UNKNOWN,
}

PRIM_TYPES: dict[str, Type] = {
    '+':      FunT(INT, FunT(INT, INT)),
    '-':      FunT(INT, FunT(INT, INT)),
    '*':      FunT(INT, FunT(INT, INT)),
    'div':    FunT(INT, FunT(INT, INT)),
    'mod':    FunT(INT, FunT(INT, INT)),
    '==':     FunT(INT, FunT(INT, BOOL)),
    '/=':     FunT(INT, FunT(INT, BOOL)),
    '<':      FunT(INT, FunT(INT, BOOL)),
    '<=':     FunT(INT, FunT(INT, BOOL)),
    '>':      FunT(INT, FunT(INT, BOOL)),
    '>=':     FunT(INT, FunT(INT, BOOL)),
    '&&':     FunT(BOOL, FunT(BOOL, BOOL)),
    '||':     FunT(BOOL, FunT(BOOL, BOOL)),
    'not':    FunT(BOOL, BOOL),
    'negate': FunT(INT, INT),
    'abs':    FunT(INT, INT),
    'signum': FunT(INT, INT),
}

GLOBAL_TYPES: dict[str, Type] = {
    'fNot':      FunT(BOOL, BOOL),
    'fBoolToInt': FunT(BOOL, INT),
    'fEven':     FunT(INT, BOOL),
    'fOdd':      FunT(INT, BOOL),
    'fSucc':     FunT(INT, INT),
    'fPred':     FunT(INT, INT),
    'fDouble':   FunT(INT, INT),
    'fSquare':   FunT(INT, INT),
    'fAbs':      FunT(INT, INT),
    'fSum':      FunT(ListT(INT), INT),
    'fProduct':  FunT(ListT(INT), INT),
    'fLength':   FunT(ListT(UNKNOWN), INT),
    'fNull':     FunT(ListT(UNKNOWN), BOOL),
    'fHead':     FunT(ListT(UNKNOWN), UNKNOWN),
    'fTail':     FunT(ListT(UNKNOWN), ListT(UNKNOWN)),
    'fReverse':  FunT(ListT(UNKNOWN), ListT(UNKNOWN)),
    'fMaximum':  FunT(ListT(INT), INT),
    'fMinimum':  FunT(ListT(INT), INT),
}

# ── Exceptions ────────────────────────────────────────────────────────────────

class ParseError(Exception):   pass
class TypeCheckError(Exception): pass

# ── Token stream ──────────────────────────────────────────────────────────────

class TokenStream:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0

    def done(self) -> bool:
        return self.pos >= len(self.tokens)

    def peek(self) -> str | None:
        return self.tokens[self.pos] if not self.done() else None

    def consume(self) -> str:
        if self.done():
            raise ParseError("Unexpected end of tokens")
        t = self.tokens[self.pos]; self.pos += 1; return t

    def try_consume(self, tok: str) -> bool:
        if self.peek() == tok:
            self.pos += 1; return True
        return False

# ── Recursive descent parser ──────────────────────────────────────────────────

def _consume_ty_uses_body(ts: TokenStream) -> None:
    """Consume type tokens, optional USES section, and BODY marker.

    Grammar after TY:
      <type_tokens> [USES <GlobalRef>*] BODY
    Partial sequences (stream ends mid-header) are accepted for is_type_valid.
    """
    # Consume type tokens until USES or BODY
    while not ts.done() and ts.peek() not in ('USES', 'BODY'):
        ts.consume()
    if ts.done():
        return  # partial header — valid for is_type_valid
    # Handle optional USES section
    if ts.peek() == 'USES':
        ts.consume()  # consume USES
        while not ts.done() and ts.peek() != 'BODY':
            if not _is_global_ref(ts.peek()):
                raise ParseError(f"Expected GlobalRef or BODY in USES section, got {ts.peek()!r}")
            ts.consume()
        if ts.done():
            return  # partial USES — valid for is_type_valid
    # Consume BODY
    if ts.done():
        raise ParseError("Expected BODY after TY")
    ts.consume()  # consume BODY


def parse_sequence(ts: TokenStream) -> dict | None:
    if ts.done(): return None
    tok = ts.consume()
    # Support four formats:
    #   Sprint 5b: DEF <name> TY <type tokens> BODY <body> EOF  (name + type)
    #   Sprint 5:  DEF TY <type tokens> BODY <body> EOF  (no name)
    #   Sprint 4:  DEF name <body> EOF
    #   Legacy:    DEF:name <body> EOF
    if tok == 'DEF':
        if ts.done(): raise ParseError("Expected TY or name after DEF")
        if ts.peek() == 'TY':
            # Sprint 5: no name
            ts.consume()  # consume TY
            _consume_ty_uses_body(ts)
            name = 'f'    # placeholder; callers replace 'f' with the real name
        else:
            name = ts.consume()
            if not ts.done() and ts.peek() == 'TY':
                # Sprint 5b: DEF <name> TY <type tokens> [USES <refs>] BODY <body> EOF
                ts.consume()  # consume TY
                _consume_ty_uses_body(ts)
    elif tok.startswith('DEF:'):
        name = tok[4:]
    else:
        raise ParseError(f"Expected DEF or DEF:*, got {tok!r}")
    if ts.done(): return {'name': name, 'body': None}
    body = parse_expr(ts)
    if ts.done(): return {'name': name, 'body': body}
    eof = ts.consume()
    if eof != 'EOF': raise ParseError(f"Expected EOF, got {eof!r}")
    return {'name': name, 'body': body}


def _is_global_ref(tok: str) -> bool:
    """True if tok is a named corpus function reference (e.g. fLength, fMap)."""
    return len(tok) >= 2 and tok[0] == 'f' and tok[1].isupper()


def parse_expr(ts: TokenStream) -> dict | None:
    if ts.done(): return None
    tok = ts.peek()
    if tok not in EXPR_STARTERS and not _is_global_ref(tok):
        raise ParseError(f"Expected expression, got {tok!r}")
    ts.consume()

    if _is_global_ref(tok) and tok not in EXPR_STARTERS:
        return {'tag': 'GlobalRef', 'name': tok}

    if tok == 'Var':
        if ts.done(): return {'tag': 'Var', 'name': None}
        name = ts.consume()
        if name in _STRUCTURAL or name == 'DEF' or name.startswith('DEF:'):
            raise ParseError(f"Invalid Var name: {name!r}")
        return {'tag': 'Var', 'name': name}

    if tok == 'Con':
        if ts.done(): return {'tag': 'Con', 'name': None}
        name = ts.consume()
        if name in _STRUCTURAL or name in ('DEF', 'EOF'):
            raise ParseError(f"Invalid constructor name: {name!r}")
        return {'tag': 'Con', 'name': name}

    if tok == 'Lit':
        if ts.done(): return {'tag': 'Lit', 'val': None}
        val = ts.consume()
        if not _is_int_token(val):
            raise ParseError(f"Invalid literal: {val!r}")
        return {'tag': 'Lit', 'val': val}

    if tok == 'Prim':
        if ts.done(): return {'tag': 'Prim', 'op': None}
        op = ts.consume()
        if op not in PRIM_OPS:
            raise ParseError(f"Unknown primitive op: {op!r}")
        return {'tag': 'Prim', 'op': op}

    if tok == 'TyArg':
        if ts.done(): return {'tag': 'TyArg', 'type': None}
        typ = ts.consume()
        if typ in _STRUCTURAL or typ == 'DEF' or typ.startswith('DEF:'):
            raise ParseError(f"Invalid type token: {typ!r}")
        return {'tag': 'TyArg', 'type': typ}

    if tok == '(App':
        exprs = []
        while not ts.done() and ts.peek() != ')':
            exprs.append(parse_expr(ts))
        ts.try_consume(')')
        fun  = exprs[0] if exprs else None
        args = exprs[1:] if len(exprs) > 1 else []
        return {'tag': 'App', 'fun': fun, 'args': args}

    if tok == '(Lam':
        n_type = 0
        while not ts.done() and ts.peek() == '@T':
            ts.consume(); n_type += 1
        n_val = None
        if not ts.done() and _is_int_token(ts.peek() or ''):
            n_val = int(ts.consume())
        body = None
        if not ts.done() and ts.peek() not in (')', None):
            body = parse_expr(ts)
        ts.try_consume(')')
        return {'tag': 'Lam', 'n_type': n_type, 'n_val': n_val, 'body': body}

    if tok == '(Let':
        binds = []
        while not ts.done() and ts.peek() == '(Bind':
            binds.append(parse_bind(ts))
        body = None
        if not ts.done() and ts.peek() != ')':
            body = parse_expr(ts)
        if not ts.done() and ts.peek() not in (')', None):
            raise ParseError(f"Expected ) after Let body, got {ts.peek()!r}")
        ts.try_consume(')')
        return {'tag': 'Let', 'binds': binds, 'body': body}

    if tok == '(Case':
        scrutinee = None
        if not ts.done() and ts.peek() not in (')', '(Alt'):
            scrutinee = parse_expr(ts)
        alts = []
        while not ts.done() and ts.peek() == '(Alt':
            alts.append(parse_alt(ts))
        if not ts.done() and ts.peek() not in (')', None):
            raise ParseError(f"Expected ) or (Alt in Case, got {ts.peek()!r}")
        ts.try_consume(')')
        return {'tag': 'Case', 'scrutinee': scrutinee, 'alts': alts}

    if tok == '(Tuple':
        elems = []
        while not ts.done() and ts.peek() != ')':
            elems.append(parse_expr(ts))
        ts.try_consume(')')
        return {'tag': 'Tuple', 'elems': elems}

    raise ParseError(f"Unhandled expression token: {tok!r}")


def parse_bind(ts: TokenStream) -> dict:
    ts.consume()  # consume '(Bind'
    # No binder name under de Bruijn — just the value expression
    val = parse_expr(ts) if not ts.done() and ts.peek() != ')' else None
    if not ts.done() and ts.peek() not in (')', None):
        raise ParseError(f"Expected ) in Bind, got {ts.peek()!r}")
    ts.try_consume(')')
    return {'tag': 'Bind', 'val': val}


def parse_alt(ts: TokenStream) -> dict:
    ts.consume()  # consume '(Alt'
    pat  = parse_pat(ts)  if not ts.done() and ts.peek() in PAT_STARTERS else None
    body = parse_expr(ts) if not ts.done() and ts.peek() != ')' else None
    if not ts.done() and ts.peek() not in (')', None):
        raise ParseError(f"Expected ) in Alt, got {ts.peek()!r}")
    ts.try_consume(')')
    return {'tag': 'Alt', 'pat': pat, 'body': body}


def parse_pat(ts: TokenStream) -> dict | None:
    if ts.done(): return None
    tok = ts.peek()

    if tok == '(PWild)':
        ts.consume()
        return {'tag': 'PWild'}

    if tok == '(PCon':
        ts.consume()
        con = None
        if not ts.done() and ts.peek() != ')':
            con = ts.consume()
            if con in _STRUCTURAL or con in ('DEF', 'EOF'):
                raise ParseError(f"Invalid constructor in pattern: {con!r}")
        # Optional integer: arity (number of variables the pattern binds)
        arity = None
        if not ts.done() and _is_int_token(ts.peek() or ''):
            arity = int(ts.consume())
        if not ts.done() and ts.peek() not in (')', None):
            raise ParseError(f"Expected ) in PCon, got {ts.peek()!r}")
        ts.try_consume(')')
        return {'tag': 'PCon', 'con': con, 'arity': arity}

    if tok == '(PTuple':
        ts.consume()
        # Integer: arity (always present for tuple patterns)
        arity = None
        if not ts.done() and _is_int_token(ts.peek() or ''):
            arity = int(ts.consume())
        if not ts.done() and ts.peek() not in (')', None):
            raise ParseError(f"Expected ) in PTuple, got {ts.peek()!r}")
        ts.try_consume(')')
        return {'tag': 'PTuple', 'arity': arity}

    if tok == '(PLit':
        ts.consume()
        val = ts.consume() if not ts.done() and ts.peek() != ')' else None
        if not ts.done() and ts.peek() not in (')', None):
            raise ParseError(f"Expected ) in PLit, got {ts.peek()!r}")
        ts.try_consume(')')
        return {'tag': 'PLit', 'val': val}

    raise ParseError(f"Expected pattern, got {tok!r}")

# ── Type inference ────────────────────────────────────────────────────────────

def _compat(a: Type, b: Type) -> bool:
    """True if a and b are compatible (Unknown matches anything)."""
    if isinstance(a, UnknownT) or isinstance(b, UnknownT): return True
    return a == b


def infer_type(node: dict | None, env: dict) -> Type:
    """Infer type of an AST node. Returns UnknownT when type cannot be determined."""
    if node is None: return UNKNOWN
    tag = node.get('tag')

    if tag == 'Lit':  return INT
    if tag == 'Prim': return PRIM_TYPES.get(node.get('op', ''), UNKNOWN)
    if tag == 'TyArg': return UNKNOWN   # erased type argument

    if tag == 'Con':
        return CONSTRUCTOR_TYPES.get(node.get('name', ''), UNKNOWN)

    if tag == 'Var':
        name = node.get('name', '')
        return env.get(name, GLOBAL_TYPES.get(name, UNKNOWN))

    if tag == 'GlobalRef':
        name = node.get('name', '')
        return GLOBAL_TYPES.get(name, UNKNOWN)

    if tag == 'App':
        fun_type = infer_type(node.get('fun'), env)
        result   = fun_type
        for arg in node.get('args', []):
            # TyArg nodes are type applications — erased at the term level
            if arg and arg.get('tag') == 'TyArg':
                continue
            arg_type = infer_type(arg, env)
            if isinstance(result, FunT):
                if not _compat(arg_type, result.arg):
                    raise TypeCheckError(
                        f"Arg type mismatch: expected {result.arg}, got {arg_type}"
                    )
                result = result.ret
            elif isinstance(result, UnknownT):
                result = UNKNOWN
            else:
                raise TypeCheckError(f"Applying non-function type {result}")
        return result

    if tag == 'Lam':
        # We don't have binder type annotations; assume UNKNOWN
        return infer_type(node.get('body'), env)

    if tag in ('Let', 'Case', 'Tuple'):
        # Return type of body / first alt body / UNKNOWN
        if tag == 'Let':
            return infer_type(node.get('body'), env)
        if tag == 'Case':
            for alt in node.get('alts', []):
                body = alt.get('body')
                if body: return infer_type(body, env)
        return UNKNOWN

    return UNKNOWN

# ── Public API ────────────────────────────────────────────────────────────────

def is_type_valid(partial_tokens: list[str], next_token: str) -> bool:
    """
    Return True if next_token is structurally valid given partial_tokens.
    Checks grammar only (bidirectional type check applied to complete sequences
    via type_check_sequence).
    """
    try:
        ts = TokenStream(partial_tokens + [next_token])
        parse_sequence(ts)
        return True
    except (ParseError, TypeError, IndexError, AttributeError, RecursionError):
        return False


def type_check_sequence(tokens: list[str]) -> tuple[bool, str]:
    """
    Parse and type-check a complete token sequence.
    Returns (ok, error_message).
    """
    try:
        ts = TokenStream(tokens)
        ast = parse_sequence(ts)
        if ast and ast.get('body'):
            infer_type(ast['body'], {})
        return True, ""
    except ParseError as e:
        return False, f"parse: {e}"
    except TypeCheckError as e:
        return False, f"type: {e}"
