#!/usr/bin/env python3
"""
sprint0/extract/parse_ghc_core.py

Extracts typed ASTs from GHC Core (-ddump-simpl) output.

Usage:
    python parse_ghc_core.py Functions.hs > corpus.json
    python parse_ghc_core.py Functions.hs --filter functions.txt --stats
"""

import re, json, sys, subprocess, argparse, os
from pathlib import Path

# ─── GHC invocation ─────────────────────────────────────────────────────────

GHC_FLAGS = ['-O0', '-fforce-recomp', '-ddump-simpl', '-v0',
             '-dsuppress-idinfo', '-dsuppress-module-prefixes',
             '-dsuppress-uniques', '-dynamic']

def dump_core(hs_file: str) -> str:
    src_dir = os.path.dirname(os.path.abspath(hs_file))
    flags = GHC_FLAGS + [f'-i{src_dir}']
    r = subprocess.run(['ghc'] + flags + [hs_file],
                       capture_output=True, text=True)
    return r.stdout  # Core dump goes to stdout

# ─── Binding splitter ────────────────────────────────────────────────────────

# Top-level sig: "NAME ::" at start of line
SIG_RE = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_\'#.$]*)\s*::', re.MULTILINE)
REC_RE = re.compile(r'Rec \{.*?end Rec \}', re.DOTALL)

def split_bindings(core_text: str) -> list[dict]:
    marker = '===================='
    idx = core_text.find(marker)
    text = core_text[idx:] if idx != -1 else core_text

    # Locate Rec blocks so we can mark recursive bindings
    rec_spans = [(m.start(), m.end()) for m in REC_RE.finditer(text)]
    def in_rec(pos): return any(a <= pos <= b for a, b in rec_spans)

    sigs = list(SIG_RE.finditer(text))
    results = []
    for i, m in enumerate(sigs):
        name = m.group(1)
        end  = sigs[i + 1].start() if i + 1 < len(sigs) else len(text)
        block = text[m.start():end]

        # Type string: between '::' and the first definition of this name
        colon = block.index('::') + 2
        def_re = re.compile(r'\n\s*' + re.escape(name) + r'[\s\n]+=')
        dm = def_re.search(block)
        if dm:
            type_str = block[colon:dm.start()].strip()
            body_str = block[dm.end():].strip()
            # Strip Rec markers that may leak into the body
            body_str = re.sub(r'(Rec\s+\{|end\s+Rec\s+\})', '', body_str).strip()
        else:
            type_str = block[colon:].strip()
            body_str = ''

        results.append({
            'name':      name,
            'type':      re.sub(r'\s+', ' ', type_str),
            'recursive': in_rec(m.start()),
            '_body':     body_str,
        })
    return results

# ─── Tokenizer ───────────────────────────────────────────────────────────────

KEYWORDS = frozenset({'case', 'of', 'let', 'in', 'forall', 'DEFAULT',
                      'where', 'do', 'if', 'then', 'else'})

def tokenize(text: str) -> list[tuple[str, str]]:
    toks, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in ' \t\n\r':                              i += 1; continue
        if text[i:i+2] == '--':
            j = text.find('\n', i); i = j+1 if j!=-1 else n; continue
        if text[i:i+2] == '->':  toks.append(('ARROW', '->')); i+=2; continue
        if text[i:i+2] == '::':  toks.append(('DC',    '::')); i+=2; continue
        if text[i:i+2] == '[]':  toks.append(('NIL',   '[]')); i+=2; continue
        if text[i:i+2] == '||':  toks.append(('ID',    '||')); i+=2; continue
        if text[i:i+2] == '&&':  toks.append(('ID',    '&&')); i+=2; continue
        if text[i:i+2] == '==':  toks.append(('ID',    '==')); i+=2; continue
        if text[i:i+2] == '/=':  toks.append(('ID',    '/=')); i+=2; continue
        if text[i:i+2] == '>=':  toks.append(('ID',    '>=')); i+=2; continue
        if text[i:i+2] == '<=':  toks.append(('ID',    '<=')); i+=2; continue
        m = re.match(r'-?\d+#', text[i:])
        if m: toks.append(('HLIT', m.group())); i+=len(m.group()); continue
        m = re.match(r'-?\d+', text[i:])
        if m: toks.append(('ILIT', m.group())); i+=len(m.group()); continue
        # Unboxed char literals: 'x'# or '\n'# etc. — must come before '"' and LP handling
        m = re.match(r"'(?:[^'\\]|\\.)'#?", text[i:])
        if m: toks.append(('CLIT', m.group())); i+=len(m.group()); continue
        if c == '"':
            j = i+1
            while j < n and text[j] != '"':
                j += 2 if text[j] == '\\' else 1
            toks.append(('SLIT', text[i:j+1])); i = j+1; continue
        m = re.match(r'[a-zA-Z_][a-zA-Z0-9_\'#.$]*', text[i:])
        if m:
            v = m.group()
            toks.append(('KW' if v in KEYWORDS else 'ID', v))
            i += len(v); continue
        m = re.match(r'\$[a-zA-Z0-9_\'#.$]+', text[i:])
        if m: toks.append(('ID', m.group())); i+=len(m.group()); continue
        MAP = {'@':'AT','(':'LP',')':'RP','{':'LB','}':'RB',
               '[':'LSQ',']':'RSQ',';':'SC','\\':'LAM','=':'EQ',
               ':':'CLN',',':'COM','_':'WC','+':'ID','-':'ID',
               '*':'ID','!':'ID','<':'ID','>':'ID','&':'ID',
               '|':'ID','^':'ID','~':'ID','%':'ID','/':'ID'}
        if c in MAP: toks.append((MAP[c], c)); i+=1; continue
        i += 1
    return toks

# ─── Token stream ────────────────────────────────────────────────────────────

class ParseError(Exception): pass

class TS:
    def __init__(self, toks):   self.t = toks; self.i = 0
    def peek(self, off=0):      j=self.i+off; return self.t[j] if j<len(self.t) else ('EOF','')
    def next(self):             t=self.t[self.i] if self.i<len(self.t) else ('EOF',''); self.i+=1; return t
    def expect(self, k, v=None):
        t = self.next()
        if t[0]!=k or (v is not None and t[1]!=v): raise ParseError(f'expected ({k},{v}) got {t}')
        return t
    def consume(self, k, v=None):
        if self.peek()[0]==k and (v is None or self.peek()[1]==v): return self.next()
    def eof(self): return self.i >= len(self.t)

# ─── Parser ──────────────────────────────────────────────────────────────────

def can_start_atom(k, v):
    return k in ('LP','AT','NIL','CLN','HLIT','ILIT','CLIT','SLIT','ID','WC') \
        or (k=='KW' and v in ('True','False'))

def parse_expr(ts):
    k, v = ts.peek()
    if k == 'LAM':               return parse_lam(ts)
    if k == 'KW' and v == 'case': return parse_case(ts)
    if k == 'KW' and v == 'let':  return parse_let(ts)
    return parse_app(ts)

def parse_app(ts):
    head = parse_atom(ts)
    args = []
    while can_start_atom(*ts.peek()):
        try:    args.append(parse_atom(ts))
        except ParseError: break
    return {'tag':'App','fun':head,'args':args} if args else head

def parse_atom(ts):
    k, v = ts.peek()
    if k == 'LP':
        ts.next()
        first = parse_expr(ts)
        if ts.peek()[0] == 'COM':           # tuple: (a, b, ...)
            elems = [first]
            while ts.peek()[0] == 'COM':
                ts.next(); elems.append(parse_expr(ts))
            ts.expect('RP')
            return {'tag':'Tuple','elems':elems}
        ts.expect('RP'); return first
    if k == 'AT':
        ts.next(); return {'tag':'TyArg','type':parse_type_atom(ts)}
    if k == 'NIL':  ts.next(); return {'tag':'Con','name':'[]'}
    if k == 'CLN':  ts.next(); return {'tag':'Con','name':':'}
    if k == 'HLIT': ts.next(); return {'tag':'Lit','sort':'Int#','val':v.rstrip('#')}
    if k == 'CLIT': ts.next(); return {'tag':'Lit','sort':'Char#','val':v}
    if k == 'ILIT': ts.next(); return {'tag':'Lit','sort':'Int', 'val':int(v)}
    if k == 'SLIT': ts.next(); return {'tag':'Lit','sort':'Str', 'val':v[1:-1]}
    if k == 'WC':   ts.next(); return {'tag':'Var','name':'_'}
    if k in ('ID','KW') and v in ('True','False'):
        ts.next(); return {'tag':'Con','name':v}
    if k == 'ID':
        ts.next()
        return {'tag':'Con','name':v} if v and v[0].isupper() else {'tag':'Var','name':v}
    raise ParseError(f'bad atom: {ts.peek()}')

def parse_lam(ts):
    ts.expect('LAM')
    binders = []
    while ts.peek()[0] != 'ARROW': binders.append(parse_binder(ts))
    ts.expect('ARROW')
    return {'tag':'Lam','binders':binders,'body':parse_expr(ts)}

def parse_binder(ts):
    k, v = ts.peek()
    if k == 'WC':  ts.next(); return {'name':'_','kind':'val'}
    if k == 'ID':  ts.next(); return {'name':v,'kind':'val'}
    if k == 'LP':
        ts.next()
        if ts.peek()[0] == 'AT':
            ts.next(); name=ts.next()[1]; ts.expect('RP')
            return {'name':f'@{name}','kind':'type'}
        name = ts.next()[1]
        if ts.peek()[0] == 'DC':
            ts.next(); typ=parse_type_str(ts,{'RP'}); ts.expect('RP')
            return {'name':name,'kind':'val','type':typ}
        ts.expect('RP'); return {'name':name,'kind':'val'}
    raise ParseError(f'bad binder: {ts.peek()}')

def parse_case(ts):
    ts.expect('KW','case')
    scrutinee = parse_app(ts)
    ts.expect('KW','of')
    # Optional as-pattern after 'of': "of wild {" or "of wild1 {"
    as_name = None
    if ts.peek()[0] == 'ID' and ts.peek(1)[0] == 'LB':
        as_name = ts.next()[1]
    brace = ts.peek()[0] == 'LB'
    if brace: ts.next()
    alts = []
    while ts.peek()[0] not in ('RB','EOF'):
        ts.consume('SC')
        if ts.peek()[0] in ('RB','EOF'): break
        alts.append(parse_alt(ts))
    if brace: ts.consume('RB')
    node = {'tag':'Case','scrutinee':scrutinee,'alts':alts}
    if as_name: node['as'] = as_name
    return node

def parse_alt(ts):
    pat = parse_pat(ts); ts.expect('ARROW'); body = parse_expr(ts)
    return {'pat':pat,'body':body}

def parse_pat(ts):
    k, v = ts.peek()
    if k == 'NIL':  ts.next(); return {'tag':'PCon','con':'[]','vars':[]}
    if k == 'CLN':
        ts.next()
        vars_ = []
        while ts.peek()[0] not in ('ARROW','SC','RB','EOF'):
            vars_.append(ts.next()[1])
        return {'tag':'PCon','con':':','vars':vars_}
    if k == 'KW' and v == 'DEFAULT': ts.next(); return {'tag':'PDefault'}
    if k in ('HLIT','ILIT','CLIT'): ts.next(); return {'tag':'PLit','val':v}
    if k == 'ID' or (k == 'KW' and v in ('True','False')):
        ts.next(); con = v
        vars_ = []
        while ts.peek()[0] not in ('ARROW','SC','RB','EOF','LP'):
            t = ts.next()
            if t[0] in ('ID','WC','HLIT'): vars_.append(t[1])
            else: break
        return {'tag':'PCon','con':con,'vars':vars_}
    if k == 'LP':  # tuple pattern
        ts.next()
        parts = []
        while ts.peek()[0] != 'RP':
            if ts.peek()[0] == 'COM': ts.next(); continue
            parts.append(ts.next()[1])
        ts.expect('RP')
        return {'tag':'PTuple','vars':parts}
    raise ParseError(f'bad pat: {ts.peek()}')

def parse_let(ts):
    ts.expect('KW','let')
    ts.expect('LB')
    binds = []
    while ts.peek()[0] not in ('RB','EOF'):
        ts.consume('SC')
        if ts.peek()[0] in ('RB','EOF'): break
        # GHC emits "name :: type\nname = expr" inside let blocks.
        # Detect the type sig (peek(0)=ID, peek(1)=DC) and skip to the definition.
        if ts.peek(1)[0] == 'DC':
            bind_name = ts.peek()[1]
            # Advance until we land on "bind_name EQ"
            while not ts.eof():
                if ts.peek()[0]=='ID' and ts.peek()[1]==bind_name and ts.peek(1)[0]=='EQ':
                    break
                if ts.peek()[0] in ('RB',): break
                ts.next()
        if ts.peek()[0] in ('RB','EOF'): break
        name = ts.next()[1]; ts.expect('EQ')
        val  = parse_expr(ts)
        binds.append({'name':name,'val':val})
    ts.consume('RB')
    ts.expect('KW','in')
    body = parse_expr(ts)
    return {'tag':'Let','binds':binds,'body':body}

def parse_type_atom(ts):
    k, v = ts.peek()
    if k == 'LP':
        ts.next(); parts=[]; depth=1
        while depth>0:
            t=ts.next()
            if t[0]=='LP': depth+=1; parts.append('(')
            elif t[0]=='RP': depth-=1; parts.append(')') if depth>0 else None
            elif t[0]=='ARROW': parts.append('->')
            elif t[0]=='DC': parts.append('::')
            else: parts.append(t[1])
        return '('+' '.join(parts)+')'
    if k == 'LSQ':
        ts.next()
        if ts.peek()[0]=='RSQ': ts.next(); return '[]'
        inner=parse_type_atom(ts); ts.consume('RSQ'); return f'[{inner}]'
    if k in ('ID','KW'): ts.next(); return v
    return '_'

def parse_type_str(ts, stop):
    parts=[]; depth=0
    while not ts.eof():
        k,v=ts.peek()
        if depth==0 and k in stop: break
        if k in ('LP','LSQ','LB'): depth+=1
        elif k in ('RP','RSQ','RB'):
            if depth==0: break
            depth-=1
        parts.append(ts.next()[1])
    return ' '.join(parts)

# ─── Per-binding parse ───────────────────────────────────────────────────────

def parse_binding(b: dict) -> dict:
    out = {'name':b['name'],'type':b['type'],'recursive':b['recursive']}
    try:
        toks = tokenize(b['_body'])
        body = parse_expr(TS(toks))
        out.update({'body':body,'ok':True})
    except Exception as e:
        out.update({'body':None,'ok':False,'error':str(e)})
    return out

# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description='Extract typed ASTs from GHC Core')
    ap.add_argument('hs_file', help='Haskell source file')
    ap.add_argument('--filter', default='functions.txt',
                    help='File listing function names to extract (one per line)')
    ap.add_argument('--out', default='-', help='Output JSON file (default: stdout)')
    ap.add_argument('--stats', action='store_true', help='Print parse statistics')
    args = ap.parse_args()

    filter_path = Path(args.filter)
    target = set(filter_path.read_text().split()) if filter_path.exists() else None

    print(f'[extract] Running GHC on {args.hs_file} …', file=sys.stderr)
    core = dump_core(args.hs_file)
    if not core.strip():
        print('[extract] ERROR: no Core output from GHC', file=sys.stderr); sys.exit(1)

    raw = split_bindings(core)

    # Filter
    if target:
        raw = [b for b in raw if b['name'] in target]
    else:
        raw = [b for b in raw if not b['name'].startswith('$')]

    results = [parse_binding(b) for b in raw]
    ok = sum(1 for r in results if r['ok'])

    if args.stats or True:
        print(f'[extract] {ok}/{len(results)} bindings parsed successfully', file=sys.stderr)
        if ok < len(results):
            for r in results:
                if not r['ok']:
                    print(f'  FAIL {r["name"]}: {r["error"]}', file=sys.stderr)

    out_data = json.dumps(results, indent=2)
    if args.out == '-':
        print(out_data)
    else:
        Path(args.out).write_text(out_data)
        print(f'[extract] Written to {args.out}', file=sys.stderr)

if __name__ == '__main__':
    main()
