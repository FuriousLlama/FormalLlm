"""pretty_print.py — de Bruijn token sequence → Haskell source.

Input:  list of tokens (S-expression prefix form, de Bruijn indexed)
Output: Haskell function definition string, with the function renamed to 'f'

Token grammar (de Bruijn form):
  sequence := DEF name expr EOF
  expr     := Var (int | name) | Con con | Lit val | Prim op | TyArg type
              | (App expr+) | (Lam @T* int? expr)
              | (Let (Bind expr)+ expr) | (Case expr (Alt pat expr)+)
              | (Tuple expr+)
  pat      := (PCon con int?) | (PTuple int?) | (PLit val) | (PWild)

Variable names are fresh-generated from _VAR_NAMES and de-indexed via env stack.
"""
import sys
from pathlib import Path

# Make checker importable from sibling directory
sys.path.insert(0, str(Path(__file__).parent.parent / "typecheck"))
from checker import TokenStream, parse_sequence, ParseError  # type: ignore

# Binary operators printed in infix form
OPERATORS = frozenset({
    '+', '-', '*', 'div', 'mod', 'rem',
    '<=', '<', '>=', '>', '==', '/=',
    '&&', '||', '++', '.',
})

# Fresh variable name pool: single letters first, then x0, x1, ...
_VAR_NAMES = list('xyzwvutsrqponmlkjihgabcde')


def _is_debruijn(s: str) -> bool:
    """True if s is an integer string (de Bruijn index or binder count)."""
    if not s:
        return False
    stripped = s.lstrip('-')
    return bool(stripped) and stripped.isdigit()


def _fresh(count: int, gen: list) -> list[str]:
    """Generate `count` unique variable names using gen (mutable [int] counter)."""
    names = []
    for _ in range(count):
        n = gen[0]
        gen[0] += 1
        if n < len(_VAR_NAMES):
            names.append(_VAR_NAMES[n])
        else:
            names.append(f'x{n - len(_VAR_NAMES)}')
    return names


def _lookup(env: list[str], name: str) -> str:
    """Resolve a Var token: de Bruijn index → env name, or free var as-is."""
    if _is_debruijn(name):
        idx = int(name)
        if 0 <= idx < len(env):
            return env[idx]
        return f'_db{idx}'  # out-of-scope index (shouldn't happen in valid code)
    return name  # free variable or SELF


def pp_expr(ast: dict, func_name: str, env: list[str], gen: list, parens: bool = False) -> str:
    """Pretty-print an AST dict to a Haskell expression string.

    env: de Bruijn environment, env[0] = innermost binding
    gen: [int] mutable counter for fresh name generation
    func_name: original corpus name; SELF and func_name both map to 'f'
    parens: wrap compound result in parens
    """
    if ast is None:
        return 'undefined'

    tag = ast['tag']

    if tag == 'TyArg':
        return ''  # erased at term level

    if tag == 'GlobalRef':
        return ast['name']  # named corpus function — emit directly, no de Bruijn lookup

    if tag == 'Var':
        name = _lookup(env, ast['name'])
        if name == func_name or name == 'SELF':
            name = 'f'
        if name in OPERATORS:
            return f'({name})'
        return name

    if tag == 'Prim':
        op = ast['op']
        return f'({op})'

    if tag == 'Con':
        con = ast['name']
        if con == ':':
            return '(:)'
        return con

    if tag == 'Lit':
        val = ast['val']
        if isinstance(val, str):
            val = int(val.rstrip('#'))
        return str(val)

    if tag == 'App':
        return _pp_app(ast, func_name, env, gen, parens)

    if tag == 'Lam':
        n_val = ast.get('n_val') or 0
        # Generate n_val fresh names in left-to-right (outermost→innermost) order
        names = _fresh(n_val, gen)
        # Last name is innermost (index 0), so prepend in reverse
        new_env = list(reversed(names)) + env
        body = pp_expr(ast['body'], func_name, new_env, gen) if ast['body'] else 'undefined'
        if names:
            result = f'\\{" ".join(names)} -> {body}'
        else:
            result = body
        return f'({result})' if parens else result

    if tag == 'Case':
        scrut = pp_expr(ast['scrutinee'], func_name, env, gen) if ast['scrutinee'] else 'undefined'
        alts  = '; '.join(_pp_alt(a, func_name, env, gen) for a in ast['alts'])
        result = f'case {scrut} of {{{alts}}}'
        return f'({result})' if parens else result

    if tag == 'Let':
        n = len(ast['binds'])
        names = _fresh(n, gen)
        # All names are in scope for each val and the body (mutual recursion)
        # Last name is innermost (index 0)
        let_env = list(reversed(names)) + env
        bind_strs = [
            f'{nm} = {pp_expr(b["val"], func_name, let_env, gen) if b["val"] else "undefined"}'
            for nm, b in zip(names, ast['binds'])
        ]
        body = pp_expr(ast['body'], func_name, let_env, gen) if ast['body'] else 'undefined'
        result = f'let {{{"; ".join(bind_strs)}}} in {body}'
        return f'({result})' if parens else result

    if tag == 'Tuple':
        elems = ', '.join(s for s in (pp_expr(e, func_name, env, gen) for e in ast['elems']) if s)
        return f'({elems})'

    return f'(undefined {{unknown:{tag}}})'


def _filter_args(raw_args: list) -> list:
    """Drop TyArg nodes."""
    return [a for a in raw_args if a['tag'] != 'TyArg']


def _pp_app(ast: dict, func_name: str, env: list[str], gen: list, parens: bool) -> str:
    fun  = ast['fun']
    args = _filter_args(ast['args'])

    # Infix Prim operator: (App (Prim op) arg1 arg2)
    if fun['tag'] == 'Prim' and fun['op'] in OPERATORS and len(args) == 2:
        op = fun['op']
        l  = pp_expr(args[0], func_name, env, gen, parens=True)
        r  = pp_expr(args[1], func_name, env, gen, parens=True)
        result = f'{l} {op} {r}'
        return f'({result})' if parens else result

    # Infix operator via Var: (App (Var op) arg1 arg2)
    if fun['tag'] == 'Var' and fun['name'] in OPERATORS and len(args) == 2:
        op = fun['name']
        l  = pp_expr(args[0], func_name, env, gen, parens=True)
        r  = pp_expr(args[1], func_name, env, gen, parens=True)
        result = f'{l} {op} {r}'
        return f'({result})' if parens else result

    # Cons constructor: (App (Con :) head tail)
    if fun['tag'] == 'Con' and fun['name'] == ':' and len(args) == 2:
        h = pp_expr(args[0], func_name, env, gen, parens=True)
        t = pp_expr(args[1], func_name, env, gen, parens=True)
        return f'({h}:{t})'

    # General application
    fun_str  = pp_expr(fun, func_name, env, gen, parens=True)
    arg_strs = [s for s in (pp_expr(a, func_name, env, gen, parens=True) for a in args) if s]
    if not arg_strs:
        return fun_str
    result = f'{fun_str} {" ".join(arg_strs)}'
    return f'({result})' if parens else result


def _pp_alt(alt: dict, func_name: str, env: list[str], gen: list) -> str:
    new_env, pat_str = _pat_bind(alt['pat'], env, gen)
    body_str = pp_expr(alt['body'], func_name, new_env, gen) if alt['body'] else 'undefined'
    return f'{pat_str} -> {body_str}'


def _pat_bind(pat: dict | None, env: list[str], gen: list) -> tuple[list[str], str]:
    """Return (new_env, pat_str). new_env has fresh names prepended for bound vars."""
    if pat is None:
        return env, '_'

    tag = pat['tag']

    if tag == 'PWild':
        return env, '_'

    if tag == 'PLit':
        val = pat['val']
        if isinstance(val, str):
            val = int(val.rstrip('#'))
        return env, str(val)

    if tag == 'PCon':
        arity = pat.get('arity') or 0
        names = _fresh(arity, gen)
        # Last name is innermost (index 0)
        new_env = list(reversed(names)) + env
        con = pat['con']
        if con == '[]':
            return new_env, '[]'
        if con == ':' and arity == 2:
            h, t = names[0], names[1]
            return new_env, f'({h}:{t})'
        pat_str = f'({con} {" ".join(names)})' if names else con
        return new_env, pat_str

    if tag == 'PTuple':
        arity = pat.get('arity') or 2
        names = _fresh(arity, gen)
        new_env = list(reversed(names)) + env
        return new_env, f'({", ".join(names)})'

    return env, '_'


def pp_func(func_name: str, ast: dict, gen: list) -> str:
    """Return a top-level Haskell definition 'f ... = ...' for the AST."""
    if ast['tag'] == 'Lam':
        n_val = ast.get('n_val') or 0
        names = _fresh(n_val, gen)
        new_env = list(reversed(names))
        body = pp_expr(ast['body'], func_name, new_env, gen) if ast['body'] else 'undefined'
        if names:
            return f'f {" ".join(names)} = {body}'
        return f'f = {body}'
    body = pp_expr(ast, func_name, [], gen)
    return f'f = {body}'


def tokens_to_haskell(tokens: list[str]) -> tuple[str, str]:
    """Parse a token sequence and return (func_name, haskell_def).

    haskell_def is the 'f ... = ...' line, ready to drop into a module.
    Raises ParseError on malformed input.
    """
    ts  = TokenStream(tokens)
    seq = parse_sequence(ts)
    if seq is None or seq['body'] is None:
        raise ParseError('empty sequence')
    name    = seq['name']
    gen     = [0]  # fresh name counter
    haskell = pp_func(name, seq['body'], gen)
    return name, haskell


# ---------------------------------------------------------------------------
# CLI: python pretty_print.py "DEF:fId (Lam 1 Var 0 ) EOF"
# ---------------------------------------------------------------------------
def main() -> None:
    import argparse, json

    p = argparse.ArgumentParser(description='Token sequence → Haskell source')
    p.add_argument('tokens', nargs='?', help='Space-separated token string')
    p.add_argument('--sequences', default='sprint/corpus/sequences.json',
                   help='sequences.json to look up a function by name')
    p.add_argument('--func',     default=None, help='Function name to look up')
    args = p.parse_args()

    if args.func:
        seqs = json.loads(open(args.sequences).read())
        match = next((s for s in seqs if s['name'] == args.func), None)
        if match is None:
            print(f"[error] function {args.func!r} not found in sequences")
            sys.exit(1)
        tokens = match['tokens']
    elif args.tokens:
        tokens = args.tokens.split()
    else:
        p.print_help()
        sys.exit(1)

    try:
        name, haskell = tokens_to_haskell(tokens)
        print(f'-- {name}')
        print(haskell)
    except ParseError as e:
        print(f'[error] {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
