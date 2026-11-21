"""run.py — benchmark runner for Sprint 1.

Pipeline per task:
  prompt → model → token sequence → pretty_print → Haskell → runhaskell → pass/fail

Novel tasks use few-shot prompting: related corpus sequences are prepended
to the prompt so the model gets in-context examples.

Usage:
  python sprint/benchmark/run.py                    # all tasks, k=10
  python sprint/benchmark/run.py --task fId         # single task by func_name
  python sprint/benchmark/run.py --k 50             # more samples
  python sprint/benchmark/run.py --no-constrain     # unconstrained decode
  python sprint/benchmark/run.py --temperature 0.8  # sampling temperature
  python sprint/benchmark/run.py --novel-only       # only novel tasks
"""
import argparse
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import torch
from tokenizers import Tokenizer

# Make sibling modules importable
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "typecheck"))
sys.path.insert(0, str(ROOT / "corpus"))
sys.path.insert(0, str(Path(__file__).parent))

from train import CausalTransformer               # type: ignore
from generate import generate, generate_batch      # type: ignore
from constrained_decode import generate_constrained_batch  # type: ignore
from pretty_print import tokens_to_haskell         # type: ignore
from checker import ParseError                     # type: ignore

# ---------------------------------------------------------------------------
# Corpus helper registry
# ---------------------------------------------------------------------------
# Maps corpus function name → Haskell definition line (already pretty-printed)
# Populated by build_corpus_registry() on first use.
_CORPUS_REGISTRY: dict[str, str] = {}
# Maps corpus function name → deps list
_DEPS_REGISTRY: dict[str, list[dict]] = {}
# Known GHC internal variable prefixes to ignore
_GHC_INTERNALS = frozenset({"wild", "wild1", "_", "$fNumInt", "$fOrdInt",
                              "$fEqInt", "$fEqBool", "$fShowBool", "$fShowInt",
                              "def"})


def _unlift_deps(haskell: str, deps: list[dict]) -> str:
    """Replace lambda-lifted dep params with actual function names.

    Input:  fDoubleAll v0 v1 = v0 (\\v2 -> v2 * 2) v1
    deps:   [{"param": 0, "func": "fMap"}]
    Output: fDoubleAll v1 = fMap (\\v2 -> v2 * 2) v1
    """
    import re
    if not deps:
        return haskell

    # Parse: "funcName param1 param2 ... = body"
    match = re.match(r'(\w+)((?:\s+\w+)*)\s*=\s*(.*)', haskell, re.DOTALL)
    if not match:
        return haskell

    fname = match.group(1)
    params_str = match.group(2).strip()
    body = match.group(3)

    if not params_str:
        return haskell

    params = params_str.split()
    dep_indices = {d["param"] for d in deps}

    # Build substitution: dep param name → actual function name
    subs: dict[str, str] = {}
    for dep in deps:
        idx = dep["param"]
        if idx < len(params):
            subs[params[idx]] = dep["func"]

    if not subs:
        return haskell

    # Remove dep params from parameter list
    new_params = [p for i, p in enumerate(params) if i not in dep_indices]

    # Substitute dep param names → function names in body
    for old_name, new_name in subs.items():
        body = re.sub(r'\b' + re.escape(old_name) + r'\b', new_name, body)

    if new_params:
        return f"{fname} {' '.join(new_params)} = {body}"
    return f"{fname} = {body}"


def build_corpus_registry(sequences_path: str) -> dict[str, str]:
    """Pre-compute pretty-printed Haskell for every corpus function.

    Lambda-lifted deps are un-lifted so registry definitions can be called
    normally (without passing dep functions as extra args).
    """
    import re
    global _CORPUS_REGISTRY
    if _CORPUS_REGISTRY:
        return _CORPUS_REGISTRY
    seqs = json.loads(Path(sequences_path).read_text())
    seen: set[str] = set()
    for seq in seqs:
        name = seq["name"]
        if name in seen:
            continue
        seen.add(name)
        try:
            _, haskell = tokens_to_haskell(seq["tokens"])
            # pretty_print uses 'f' for the function name AND for recursive calls.
            # Replace ALL standalone 'f' with the original name using word boundaries.
            haskell = re.sub(r'\bf\b', name, haskell)
            # Un-lift deps: substitute dep param names with actual function names
            deps = seq.get("deps", [])
            if deps:
                haskell = _unlift_deps(haskell, deps)
            _CORPUS_REGISTRY[name] = haskell
        except Exception:
            pass
    return _CORPUS_REGISTRY


def build_deps_registry(sequences_path: str) -> dict[str, list[dict]]:
    """Load deps metadata for all corpus functions.

    Returns {func_name: [{"param": N, "func": "fName"}, ...]}.
    """
    global _DEPS_REGISTRY
    if _DEPS_REGISTRY:
        return _DEPS_REGISTRY
    seqs = json.loads(Path(sequences_path).read_text())
    for seq in seqs:
        name = seq["name"]
        if name not in _DEPS_REGISTRY:
            _DEPS_REGISTRY[name] = seq.get("deps", [])
    return _DEPS_REGISTRY


def _collect_corpus_refs(haskell: str, registry: dict[str, str]) -> set[str]:
    """Find corpus function names referenced in a Haskell definition."""
    import re
    # Match word boundaries around fCamelCase identifiers
    pattern = re.compile(r'\b(f[A-Z][a-zA-Z0-9]*)\b')
    return {m.group(1) for m in pattern.finditer(haskell) if m.group(1) in registry}


def _collect_all_deps(root_def: str, registry: dict[str, str]) -> list[str]:
    """
    Collect all transitive helper definitions needed by root_def.
    Returns list of (name, haskell_line) pairs in dependency order.
    """
    needed: list[str] = []
    visited: set[str] = set()
    queue = list(_collect_corpus_refs(root_def, registry))

    while queue:
        name = queue.pop(0)
        if name in visited:
            continue
        visited.add(name)
        if name not in registry:
            continue
        helper_def = registry[name]
        needed.append(name)
        # Find deps of this helper too
        for dep in _collect_corpus_refs(helper_def, registry):
            if dep not in visited:
                queue.append(dep)

    return needed


# ---------------------------------------------------------------------------
# Haskell test harness template
# ---------------------------------------------------------------------------
_HS_TEMPLATE = """\
module Main where

{helpers}{func_def}

main :: IO ()
main = mapM_ print [{tests}]
"""


def _build_test_expr(test: dict, call_prefix: str, deps_prefix: str = "") -> str:
    inp      = test["input"]
    expected = test["expected"]
    # Wrap negative inputs in parens to avoid Haskell parsing as subtraction
    safe_inp = f"({inp})" if inp.startswith("-") else inp

    if deps_prefix and call_prefix:
        # call_prefix is like "f (\x -> x + x)" — insert deps after "f"
        rest = call_prefix[2:]  # skip "f "
        call = f"f {deps_prefix} {rest} {safe_inp}"
    elif deps_prefix:
        call = f"f {deps_prefix} {safe_inp}"
    elif call_prefix:
        call = f"{call_prefix} {safe_inp}"
    else:
        call = f"f {safe_inp}"
    return f"({call}) == ({expected})"


def _get_task_deps(task: dict, deps_registry: dict[str, list[dict]] | None = None) -> list[dict]:
    """Get deps for a task: from deps_registry (training-set) or task metadata (novel)."""
    func_name = task["func_name"]
    if deps_registry and func_name in deps_registry:
        return deps_registry[func_name]
    return task.get("deps", [])


def build_haskell_source(func_def: str, task: dict,
                         registry: dict[str, str] | None = None,
                         deps_registry: dict[str, list[dict]] | None = None) -> str:
    # Get deps for this task
    deps = _get_task_deps(task, deps_registry)
    deps_prefix = " ".join(d["func"] for d in deps) if deps else ""

    call_prefix = task.get("call_prefix", "")
    test_exprs  = ", ".join(
        _build_test_expr(t, call_prefix, deps_prefix) for t in task["tests"]
    )
    # Include any corpus functions the generated code depends on
    helpers = ""
    if registry:
        needed: set[str] = set()
        # Include dep functions and their transitive deps
        for d in deps:
            fname = d["func"]
            needed.add(fname)
            if fname in registry:
                for td in _collect_all_deps(registry[fname], registry):
                    needed.add(td)
        # Also collect refs from the generated code itself (backward compat)
        for td in _collect_all_deps(func_def, registry):
            needed.add(td)
        if needed:
            ordered = sorted(needed)
            helpers = "\n".join(registry[n] for n in ordered if n in registry) + "\n\n"
    return _HS_TEMPLATE.format(helpers=helpers, func_def=func_def, tests=test_exprs)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def run_haskell(source: str, timeout: int = 5) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile(suffix=".hs", mode="w", delete=False) as f:
        f.write(source)
        path = f.name
    try:
        result = subprocess.run(
            ["runhaskell", path],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return False, result.stderr.strip()
        output = result.stdout.strip()
        lines = [l.strip() for l in output.splitlines() if l.strip()]
        passed = bool(lines) and all(l == "True" for l in lines)
        return passed, output
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except FileNotFoundError:
        return False, "runhaskell not found"
    finally:
        Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------
def load_model(checkpoint: str, tokenizer_path: str, device: torch.device):
    tok  = Tokenizer.from_file(tokenizer_path)
    ckpt = torch.load(checkpoint, map_location=device, weights_only=False)
    cfg  = ckpt["args"]
    model = CausalTransformer(
        vocab_size=ckpt["vocab_size"],
        d_model=cfg["d_model"],
        nhead=cfg["nhead"],
        num_layers=cfg["num_layers"],
        max_len=cfg["max_len"],
        dropout=0.0,
    ).to(device)
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    return model, tok, ckpt


def encode_prompt(prompt_str: str, tok: Tokenizer) -> list[int]:
    enc = tok.encode(prompt_str)
    return enc.ids[:-1]   # strip EOS; keep BOS + tokens


def ids_to_tokens(ids: list[int], tok: Tokenizer) -> list[str]:
    return [tok.id_to_token(i) for i in ids if tok.id_to_token(i)]


def extract_candidate_tokens(
    prompt_ids: list[int],
    gen_ids: list[int],
    tok: Tokenizer,
    func_name: str,
    vocab: dict[str, int] | None = None,
) -> list[str]:
    """Extract a clean token sequence from one generated candidate.

    Sprint 5: prompt ends with [DEF, TY, <type>, BODY]; we find the last DEF
    in prompt_ids and take from there + gen_ids.  The TY…BODY frame is stripped
    by _clean_tokens and func_name is inserted as the function name.

    Sprint 4 fallback: prompt ends with [DEF, <unk>]; behaves as before.
    """
    if vocab is None:
        vocab = tok.get_vocab()
    eof_id = vocab.get('EOF')
    def_id = vocab.get('DEF')

    # Find the last DEF in prompt_ids (the target function's header start)
    header_start = -2  # Sprint 4 fallback: last 2 tokens = [DEF, <unk>]
    if def_id is not None:
        for pos in range(len(prompt_ids) - 1, -1, -1):
            if prompt_ids[pos] == def_id:
                header_start = pos - len(prompt_ids)  # negative index from end
                break

    raw_ids = list(prompt_ids[header_start:]) + gen_ids
    if eof_id is not None and eof_id in raw_ids:
        raw_ids = raw_ids[:raw_ids.index(eof_id) + 1]
    raw_toks = [tok.id_to_token(i) for i in raw_ids]
    return _clean_tokens(raw_toks, func_name)


# ---------------------------------------------------------------------------
# Few-shot prompt builder (Option B for novel tasks)
# ---------------------------------------------------------------------------
def get_refs(tokens: list[str], name: str = "") -> set[str]:
    """Extract GlobalRef set from a token sequence (refs in body after BODY token).

    A GlobalRef is any token matching fCamelCase that isn't the function's own name.
    """
    import re
    ref_pattern = re.compile(r'^f[A-Z]')
    if "BODY" in tokens:
        body_start = tokens.index("BODY") + 1
        body_tokens = tokens[body_start:]
    else:
        body_tokens = tokens
    return {t for t in body_tokens if ref_pattern.match(t) and t != name}


# Maps (type_key) → list of (name, tokens, refs) — populated by main()
_SEQUENCES_BY_TYPE_WITH_REFS: "dict[str, list[tuple[str, list[str], set[str]]]]" = {}


def build_few_shot_prompt(
    task: dict,
    tok: Tokenizer,
    sequences_by_name: dict[str, list[str]],
    sequences_by_type: "dict[str, list[tuple[str, list[str]]]] | None" = None,
) -> tuple[list[int] | None, list[str]]:
    """
    Build a few-shot prompt by prepending related corpus sequences.

    Sprint 9: Pattern-biased selection — prefer peers whose body refs overlap
    with the task's few_shot_funcs (building blocks).

    Returns (token_ids, context_names) where token_ids is the prompt ID list
    (without EOS) or None, and context_names lists the selected functions.
    """
    import random

    func_name = task.get("func_name", "")
    context_tokens: list[str] = []
    context_names: list[str] = []
    context_type = task.get("context_type")

    if context_type and sequences_by_type is not None:
        # Type-matched peer selection with pattern bias (Sprint 9)
        peers = [(n, toks) for n, toks in sequences_by_type.get(context_type, [])
                 if n != func_name]
        if not peers:
            # Arity fallback (critical for apply_twice which has 0 exact type peers)
            n_arrows = context_type.count("->")
            for key, entries in sequences_by_type.items():
                if key.count("->") == n_arrows:
                    peers.extend((n, toks) for n, toks in entries
                                 if n != func_name)
        if not peers:
            return None, []

        # Pattern-biased selection: only for compositional tasks (tier >= 2)
        # T1 few_shot_funcs are contextually related, not building blocks —
        # pattern bias selects wrong peers and destabilizes T1 generation.
        needed = set(task.get("few_shot_funcs", []))
        task_tier = task.get("tier", 1)
        if needed and len(needed) >= 2 and task_tier >= 2 and _SEQUENCES_BY_TYPE_WITH_REFS:
            # Get refs for all peers from pre-computed index
            peers_with_refs = []
            refs_lookup = {}
            for type_key, entries in _SEQUENCES_BY_TYPE_WITH_REFS.items():
                for n, _, refs in entries:
                    refs_lookup[n] = refs

            scored = []
            for n, toks in peers:
                refs = refs_lookup.get(n, get_refs(toks, n))
                score = len(refs & needed)
                scored.append((n, toks, score))

            high = [(n, t, s) for n, t, s in scored if s >= 2]
            rest = [(n, t, s) for n, t, s in scored if s < 2]

            # Take up to 2 high-overlap peers, fill rest randomly
            selected_scored = []
            if high:
                selected_scored = random.sample(high, min(2, len(high)))
            n_fill = 3 - len(selected_scored)
            if n_fill > 0 and rest:
                selected_scored += random.sample(rest, min(n_fill, len(rest)))

            selected = [(n, t) for n, t, _ in selected_scored]
        else:
            selected = random.sample(peers, min(3, len(peers)))

        context_names = [n for n, _ in selected]
        for _, toks in selected:
            context_tokens.extend(toks)
    else:
        # Fallback: curated few_shot_funcs
        few_shot_names = task.get("few_shot_funcs", [])
        if not few_shot_names:
            return None, []
        for name in few_shot_names:
            if name not in sequences_by_name:
                return None, []
            context_tokens.extend(sequences_by_name[name])
        context_names = list(few_shot_names)

    # Encode context: BOS + tokens (no EOS — more tokens follow)
    context_enc = tok.encode(" ".join(context_tokens))
    context_ids = context_enc.ids[:-1]

    # Encode target function header (e.g. "DEF TY Int -> Int BODY")
    func_enc = tok.encode(task["prompt"])
    func_ids = func_enc.ids[:-1]  # drop EOS, keep BOS

    # Combine: context BOS + context body + target header (skip duplicate BOS)
    return context_ids + func_ids[1:], context_names


# ---------------------------------------------------------------------------
# Per-task benchmark
# ---------------------------------------------------------------------------
def _clean_tokens(raw_tokens: list[str], func_name: str) -> list[str]:
    """Strip special tokens and normalise the DEF header to 'DEF func_name'.

    Sprint 5 format: DEF TY <type tokens> BODY <body> EOF
      → strip TY…BODY, insert func_name → DEF func_name <body> EOF

    Sprint 4 format: DEF <unk> <body> EOF
      → replace <unk> after DEF → DEF func_name <body> EOF
    """
    tokens = [t for t in raw_tokens if t not in ("<pad>", "<bos>", None)]

    # Sprint 5/10: DEF [name] TY … [USES …] BODY → DEF func_name <body> EOF
    if len(tokens) >= 3 and (tokens[1] == "TY" or (len(tokens) >= 4 and tokens[2] == "TY")):
        try:
            body_pos = tokens.index("BODY")
            return ["DEF", func_name] + tokens[body_pos + 1:]
        except ValueError:
            pass  # BODY not found — fall through to Sprint 4 path

    # Sprint 4: replace <unk> after DEF with func_name
    cleaned: list[str] = []
    for j, t in enumerate(tokens):
        if t == "<unk>":
            if j > 0 and tokens[j - 1] == "DEF":
                cleaned.append(func_name)
            # else: drop body-position <unk>
        else:
            cleaned.append(t)
    return cleaned


def run_task(
    task: dict,
    model,
    tok: Tokenizer,
    device: torch.device,
    k: int,
    temperature: float,
    constrained: bool,
    verbose: bool,
    sequences_by_name: dict[str, list[str]],
    registry: dict[str, str] | None = None,
    deps_registry: dict[str, list[dict]] | None = None,
    workers: int = 8,
    sequences_by_type: "dict[str, list[tuple[str, list[str]]]] | None" = None,
    dump: bool = False,
) -> dict:
    func_name = task["func_name"]
    is_novel  = task.get("novel", False)
    few_shot_ids, context_names = build_few_shot_prompt(task, tok, sequences_by_name, sequences_by_type)
    prompt_ids = few_shot_ids if few_shot_ids is not None else encode_prompt(task["prompt"], tok)
    vocab      = tok.get_vocab()
    id_to_tok  = {v: k for k, v in vocab.items()}
    errors     = {"parse": 0, "compile": 0, "wrong": 0, "timeout": 0}
    dump_entries: list[dict] | None = [] if dump else None

    # ── 1. Generate all k candidates ────────────────────────────────────────
    # Unconstrained: one batched GPU pass (k sequences in parallel).
    # Constrained: stateful parser per sequence, must stay serial.
    if constrained:
        all_gen_ids = generate_constrained_batch(
            model, prompt_ids, id_to_tok, k=k,
            max_new_tokens=200, temperature=max(temperature, 0.1), top_k=0,
        )
    else:
        all_gen_ids = generate_batch(
            model, prompt_ids, k=k,
            max_new_tokens=200, temperature=temperature, top_k=0,
        )

    # ── 2. Decode tokens → Haskell source (CPU, fast) ───────────────────────
    candidates: list[str | None] = []   # None = parse error
    func_defs:  list[str | None] = []

    for i, gen_ids in enumerate(all_gen_ids):
        # Extract the target function from the full prompt+generation stream.
        # extract_candidate_tokens finds the last DEF in prompt_ids (handles both
        # Sprint 5 "DEF TY … BODY" and Sprint 4 "DEF <unk>" headers), clips at
        # EOF, strips the TY…BODY frame, and inserts func_name.
        tokens = extract_candidate_tokens(prompt_ids, gen_ids, tok, func_name, vocab)

        try:
            _, func_def = tokens_to_haskell(tokens)
        except (ParseError, Exception) as e:
            errors["parse"] += 1
            if verbose:
                print(f"  [{i}] PARSE ERROR: {e}")
                print(f"       tokens: {' '.join(tokens[:30])}")
            if dump_entries is not None:
                dump_entries.append({
                    "tokens": tokens,
                    "classification": "parse_error",
                    "error": str(e),
                    "parsed_body": None,
                })
            candidates.append(None)
            func_defs.append(None)
            continue

        source = build_haskell_source(func_def, task, registry=registry,
                                      deps_registry=deps_registry)
        if verbose:
            print(f"  [{i}] {func_def}")
        if dump_entries is not None:
            dump_entries.append({
                "tokens": tokens,
                "classification": None,  # filled in eval phase
                "error": None,
                "parsed_body": func_def,
            })
        candidates.append(source)
        func_defs.append(func_def)

    # ── 3. Evaluate all valid candidates in parallel ─────────────────────────
    valid_indices = [i for i, s in enumerate(candidates) if s is not None]

    def eval_one(idx: int) -> tuple[int, bool, str]:
        ok, output = run_haskell(candidates[idx])
        return idx, ok, output

    passed = 0
    with ThreadPoolExecutor(max_workers=min(workers, len(valid_indices) or 1)) as pool:
        for idx, ok, output in pool.map(eval_one, valid_indices):
            if ok:
                passed += 1
                if dump_entries is not None:
                    dump_entries[idx]["classification"] = "pass"
                if verbose:
                    print(f"  [{idx}] PASS")
            else:
                if "TIMEOUT" in output:
                    errors["timeout"] += 1
                    cls = "timeout"
                elif output and any(kw in output for kw in ("error", "Error", "exception")):
                    errors["compile"] += 1
                    cls = "compile_error"
                else:
                    errors["wrong"] += 1
                    cls = "wrong"
                if dump_entries is not None:
                    dump_entries[idx]["classification"] = cls
                    dump_entries[idx]["error"] = output[:500]
                if verbose:
                    print(f"  [{idx}] FAIL: {output[:80]}")

    result = {
        "task":      task["name"],
        "prompt":    task["prompt"],
        "novel":     is_novel,
        "tier":      task.get("tier", 1),
        "k":         k,
        "passed":    passed,
        "pass_rate": passed / k,
        "errors":    errors,
    }
    if dump_entries is not None:
        result["context_used"] = context_names
        result["candidates"] = dump_entries
    return result


def _extract_last_def(tokens: list[str], prompt: str) -> list[str]:
    """
    From a few-shot token stream, extract the last DEF block.
    The prompt is e.g. "DEF fTriple" (two tokens).
    """
    prompt_tokens = prompt.split()  # ["DEF", "fTriple"]

    # Find the last occurrence of the DEF + name pair
    last_idx = None
    for i, t in enumerate(tokens):
        if t == prompt_tokens[0] and i + 1 < len(tokens) and tokens[i + 1] == prompt_tokens[-1]:
            last_idx = i

    if last_idx is None:
        # Model generated something before the target — return all
        return tokens

    return tokens[last_idx:]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(description="Sprint 4 tiered benchmark runner")
    p.add_argument("--checkpoint",  default="sprint/model/checkpoint.pt")
    p.add_argument("--tokenizer",   default="sprint/corpus/tokenizer.json")
    p.add_argument("--tasks",       default="sprint/benchmark/tasks.json")
    p.add_argument("--sequences",   default="sprint/corpus/sequences_aug.json",
                   help="Augmented sequences for few-shot context")
    p.add_argument("--task",        default=None, help="Run a single task by func_name")
    p.add_argument("--k",           type=int,   default=10,  help="Candidates per task")
    p.add_argument("--temperature", type=float, default=0.8, help="Sampling temperature")
    p.add_argument("--no-constrain", action="store_true",    help="Unconstrained decode")
    p.add_argument("--tier",        type=int,   default=None,
                   help="Run only tasks of this tier (1, 2, or 3)")
    p.add_argument("--novel-only",   action="store_true",    help="Run only novel tasks")
    p.add_argument("--workers",     type=int,   default=8,
                   help="Parallel runhaskell workers (default: 8)")
    p.add_argument("--dump-candidates", type=str, default=None,
                   metavar="PATH",
                   help="Save per-candidate diagnostics to JSON file")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    print(f"[benchmark] loading checkpoint: {args.checkpoint}")
    model, tok, ckpt = load_model(args.checkpoint, args.tokenizer, device)

    train_loss = ckpt.get("train_loss", ckpt.get("loss", float("nan")))
    val_loss   = ckpt.get("val_loss",   float("nan"))
    print(f"[benchmark] epoch={ckpt['epoch']}, "
          f"train_loss={train_loss:.4f}, val_loss={val_loss:.4f}, device={device}")

    # Load sequences for corpus helper registry and type index
    raw_seqs = json.loads(Path(args.sequences).read_text())
    sequences_by_name: dict[str, list[str]] = {}
    for seq in raw_seqs:
        name = seq["name"]
        if name not in sequences_by_name:
            sequences_by_name[name] = seq["tokens"]

    # Sprint 5: build type index for few-shot context selection
    # Sprint 9: also build refs index for pattern-biased selection
    sequences_by_type: dict[str, list[tuple[str, list[str]]]] = {}
    try:
        from normalize import normalize_type  # type: ignore
        global _SEQUENCES_BY_TYPE_WITH_REFS
        _SEQUENCES_BY_TYPE_WITH_REFS.clear()
        for seq in raw_seqs:
            seq_type = seq.get("type", "")
            if not seq_type:
                continue
            type_key = " ".join(normalize_type(seq_type))
            name = seq["name"]
            tokens = seq["tokens"]
            sequences_by_type.setdefault(type_key, []).append((name, tokens))
            refs = get_refs(tokens, name)
            _SEQUENCES_BY_TYPE_WITH_REFS.setdefault(type_key, []).append((name, tokens, refs))
        print(f"[benchmark] type index: {len(sequences_by_type)} distinct types")
        n_comp = sum(1 for entries in _SEQUENCES_BY_TYPE_WITH_REFS.values()
                     for _, _, refs in entries if len(refs) >= 2)
        print(f"[benchmark] refs index: {n_comp} compositions (>=2 refs)")
    except ImportError:
        print("[benchmark] normalize not found — type-based few-shot disabled")

    # Build corpus helper registry for dependency resolution
    registry = build_corpus_registry(args.sequences)
    deps_registry = build_deps_registry(args.sequences)
    print(f"[benchmark] corpus registry: {len(registry)} function definitions")

    all_tasks = json.loads(Path(args.tasks).read_text())
    if args.task:
        all_tasks = [t for t in all_tasks if t["func_name"] == args.task]
        if not all_tasks:
            print(f"[error] task {args.task!r} not found")
            sys.exit(1)
    if args.tier is not None:
        all_tasks = [t for t in all_tasks if t.get("tier", 1) == args.tier]
    if args.novel_only:
        all_tasks = [t for t in all_tasks if t.get("novel", False)]

    constrained = not args.no_constrain
    dump = args.dump_candidates is not None
    print(f"[benchmark] {len(all_tasks)} task(s), k={args.k}, "
          f"temp={args.temperature}, constrained={constrained}"
          f"{', dump=ON' if dump else ''}\n")

    results = []
    for task in all_tasks:
        tier_tag  = f"[T{task.get('tier', 1)}]"
        novel_tag = "[NOVEL]" if task.get("novel") else ""
        tag = " ".join(t for t in [tier_tag, novel_tag] if t)
        print(f"--- {task['name']} {tag} ({task['prompt']}) ---")
        r = run_task(
            task, model, tok, device,
            k=args.k,
            temperature=args.temperature,
            constrained=constrained,
            verbose=args.verbose,
            sequences_by_name=sequences_by_name,
            registry=registry,
            deps_registry=deps_registry,
            workers=args.workers,
            sequences_by_type=sequences_by_type or None,
            dump=dump,
        )
        results.append(r)
        errs = r["errors"]
        print(f"  pass@{args.k}: {r['passed']}/{args.k}  "
              f"(parse={errs['parse']} compile={errs['compile']} "
              f"wrong={errs['wrong']} timeout={errs['timeout']})\n")

    # Tiered summary
    # Tier 1: pass@1 at greedy (temperature=0) is the primary metric.
    # Tier 2/3: pass@k at temperature>0 is the primary metric.
    def summarize(label: str, rs: list[dict]) -> None:
        if not rs:
            return
        total_passed = sum(r["passed"] for r in rs)
        total_k      = sum(r["k"]      for r in rs)
        # pass@1 greedy: treat each task as pass if passed > 0 (any of k attempts)
        any_pass     = sum(1 for r in rs if r["passed"] > 0)
        # pass@1 strict: only first attempt counted (approximated as passed/k >= 1/k)
        # For a proper pass@1 from k samples we'd need the exact pass@1 formula,
        # but pass@k (any pass) is the primary signal here.
        rate = total_passed / total_k if total_k else 0.0
        print(f"{label}: {any_pass}/{len(rs)} tasks solved  "
              f"({total_passed}/{total_k} candidates, {rate:.1%})")

    print("=" * 60)
    for tier_num in (1, 2, 3):
        tier_rs = [r for r in results if r.get("tier", 1) == tier_num]
        if tier_rs:
            labels = {1: "Tier 1 (regression)", 2: "Tier 2 (compositional)",
                      3: "Tier 3 (structural) "}
            summarize(labels[tier_num], tier_rs)
    print("-" * 60)
    all_passed = sum(r["passed"] for r in results)
    all_k      = sum(r["k"]      for r in results)
    all_any    = sum(1 for r in results if r["passed"] > 0)
    rate = all_passed / all_k if all_k else 0.0
    print(f"TOTAL           : {all_any}/{len(results)} tasks solved, "
          f"{all_passed}/{all_k} candidates ({rate:.1%})")

    # Write diagnostic dump
    if args.dump_candidates:
        dump_path = Path(args.dump_candidates)
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_data = []
        for r in results:
            if "candidates" in r:
                dump_data.append({
                    "task": r["task"],
                    "k": r["k"],
                    "temperature": args.temperature,
                    "context_used": r["context_used"],
                    "errors": r["errors"],
                    "candidates": r["candidates"],
                })
        dump_path.write_text(json.dumps(dump_data, indent=2))
        print(f"\n[dump] {len(dump_data)} task(s) written to {dump_path}")


if __name__ == "__main__":
    main()
