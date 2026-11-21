"""Shared helpers for the 2026-11-21 re-verification (rerun_*) scripts.

Daemon-only. Every check sends exactly ONE candidate definition per
check_compile call (never the positional batch path).
"""
from __future__ import annotations

import itertools
import json
import random
import threading
from pathlib import Path

from lean_mcts.env.pantograph_client import PantographClient
from lean_mcts.eval.oracle import _build_params, _eval_args, daemon_warm_verify

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "sprint/benchmark/results/rerun_2026-11-21"
OUT.mkdir(parents=True, exist_ok=True)

MANIFEST = json.loads(Path("/tmp/pantograph_workers.json").read_text())

_ctr = itertools.count(1)
_lock = threading.Lock()


def uid() -> int:
    with _lock:
        return next(_ctr) + 900000


def client(i: int) -> PantographClient:
    socks = MANIFEST["verify"]
    return PantographClient(socks[i % len(socks)])


def compile_msgs(cl: PantographClient, code: str):
    units = cl.check_compile(code)
    infos, errs = [], []
    for cu in units:
        for m in cu["messages"]:
            if m["severity"] == "INFORMATION":
                infos.append(m["data"].strip())
            elif m["severity"] == "ERROR":
                errs.append(m["data"].strip())
    return infos, errs


def standalone(task, tactics, cl, test_cases=None):
    """Exactly the harness single-candidate path (daemon_warm_verify)."""
    if test_cases is not None:
        import copy
        task = copy.copy(task)
        task.test_cases = test_cases
    r = daemon_warm_verify(task, list(tactics), cl)
    fails = [d for d in r.details if not d["passed"]]
    return {
        "passed": r.passed, "n_passed": r.n_passed, "n_total": r.n_total,
        "error": (r.error or "")[:600] or None,
        "fails": fails[:5],
    }


# ---------------------------------------------------------------------------
# Reference implementations for the 6 S29 hard held-out tasks (taken from the
# docstring of lean_mcts/synth/held_out_hard_tasks.py; the .lean file has none).
# {S} is replaced by a unique suffix per compile call.
HARD_REFS = {
    "sumAtOddIndices": """def rrref_sumAtOddIndices{S} (xs : List Int) : Int :=
  xs.zipIdx.foldl (fun acc (x, i) => if i % 2 == 1 then acc + x else acc) 0""",
    "zipDiff": """def rrref_zipDiff{S} (xs : List Int) : List Int :=
  (xs.zip xs.tail).map (fun (a, b) => b - a)""",
    "longestIncreasingRun": """def rrref_longestIncreasingRun{S} (xs : List Int) : Nat :=
  match xs with
  | [] => 0
  | h :: t =>
    (t.foldl (fun ((best, cur, prev) : Nat × Nat × Int) x =>
      let c := if x > prev then cur + 1 else 1
      (max best c, c, x)) ((1 : Nat), (1 : Nat), h)).1""",
    "maxSubarrayProduct": """def rrref_maxSubarrayProduct{S} (xs : List Int) : Int :=
  match xs with
  | [] => 0
  | h :: t =>
    let r := t.foldl (fun ((mxE, mnE, best) : Int × Int × Int) x =>
      let a := mxE * x
      let b := mnE * x
      let mx := max x (max a b)
      let mn := min x (min a b)
      (mx, mn, max best mx)) (h, h, h)
    r.2.2""",
    "mergeSortedDesc": """def rrref_mergeSortedDesc{S} : List Int → List Int → List Int
  | [], ys => ys
  | xs, [] => xs
  | x :: xs', y :: ys' =>
    if x ≥ y then x :: rrref_mergeSortedDesc{S} xs' (y :: ys')
    else y :: rrref_mergeSortedDesc{S} (x :: xs') ys'""",
    "windowSums": """def rrref_windowSums{S} (k : Nat) (xs : List Int) : List Int :=
  if k == 0 || k > xs.length then []
  else (List.range (xs.length - k + 1)).map
       (fun i => ((xs.drop i).take k).foldl (· + ·) 0)""",
}


def _comment_refs():
    """`-- Reference: <expr>` comments directly above `def <name>` stubs."""
    import re
    refs = {}
    d = ROOT / "lean_mcts/env/FormalNav/FormalNav"
    for f in d.glob("Tasks*.lean"):
        lines = f.read_text().splitlines()
        for i, ln in enumerate(lines):
            m = re.match(r"\s*--\s*Reference:\s*(.+)$", ln)
            if not m:
                continue
            for j in range(i + 1, min(i + 3, len(lines))):
                md = re.match(r"def (\w+)", lines[j])
                if md:
                    refs.setdefault(md.group(1), (m.group(1).strip(), f.name))
                    break
    return refs


COMMENT_REFS = _comment_refs()
_DEFINED_REFS: set[str] = set()


def has_lean_ref(cl, thm) -> bool:
    if thm in _DEFINED_REFS:
        return True
    infos, errs = compile_msgs(cl, f"#check @{thm}_ref\n")
    if not errs:
        _DEFINED_REFS.add(thm)
    return not errs


def ref_prelude(task, s: str, cl=None):
    """Return (prelude_code, ref_fn_name, source)."""
    thm = task.theorem_name
    if thm in HARD_REFS:
        return HARD_REFS[thm].replace("{S}", s), f"rrref_{thm}{s}"
    if cl is not None and not has_lean_ref(cl, thm) and thm in COMMENT_REFS:
        expr = COMMENT_REFS[thm][0]
        params = _build_params(task.params_signature, task.test_cases, task.input_type)
        name = f"rrcref_{thm}{s}"
        return f"def {name} {params} : {task.return_type} :=\n  {expr}", name
    return "", f"{thm}_ref"


# ---------------------------------------------------------------------------
# Fresh input generation
LISTS = [
    "[]", "[0]", "[5]", "[-3]", "[1, 2]", "[2, 1]", "[3, 3]", "[-1, 3]",
    "[3, -1]", "[1, 2, 3]", "[3, 2, 1]", "[1, 1, 1]", "[-2, -5, -1]",
    "[0, 0, 1]", "[1, -1, 1, -1]", "[5, 4, 3, 2, 1]", "[1, 2, 3, 4, 5]",
    "[2, 2, 3, 3, 1]", "[-4, 7, -2, 8, 0]", "[10, -10, 5]", "[1, 3, 2, 4, 3, 5]",
    "[7, 7, 2, 7, 1]", "[-1, -1]", "[6, 0, -6, 3]", "[100, -50, 25, -12]",
    "[1, 2, 2, 3]", "[9, 1, 8, 2, 7, 3]", "[-7]", "[4, -1, 2, 1, -5, 4]",
    "[2, -3, 0, -2, 5, 1]", "[0, 0]", "[-2, 3, -4, 0, 6]",
]


def _parse(l):
    return json.loads(l)


def _fmt(v):
    return "[" + ", ".join(str(x) for x in v) + "]"


DESC = sorted({_fmt(sorted(_parse(l), reverse=True)) for l in LISTS}, key=len)
ASC = sorted({_fmt(sorted(_parse(l))) for l in LISTS}, key=len)
MATS = ["[]", "[[]]", "[[1]]", "[[1, 2], [3, 4]]", "[[1, 2, 3], [4, 5, 6]]",
        "[[1], [2], [3]]", "[[-1, 0], [2, -3], [4, 5]]", "[[], []]",
        "[[5, -5]]", "[[0, 0, 0], [1, 1, 1], [2, 2, 2]]", "[[7], [-7]]",
        "[[1, 2], [3, 4], [5, 6], [7, 8]]"]
RAGGED = MATS + ["[[1, 2, 3], [4]]", "[[], [1, 2]]", "[[-1], [], [2, 3]]"]
FUNS = ["(fun x => x * 2)", "(fun x => x + 3)", "(fun x => x - 1)", "(fun x => -x)"]


def iarg(v: int) -> str:
    return f"({v})" if v < 0 else str(v)


def fresh_inputs(task, n=32, seed=0):
    """List of arg-lists (each a list of Lean argument strings)."""
    rnd = random.Random(f"{task.name}-{seed}")
    params = _build_params(task.params_signature, task.test_cases, task.input_type)
    p = params.replace(" ", "")
    nm = task.theorem_name
    if p == "(xs:ListInt)":
        return [[l] for l in LISTS]
    if p == "(n:Nat)":
        return [[str(k)] for k in range(0, 13)]
    if p == "(xss:List(ListInt))":
        return [[m] for m in (MATS if "ranspose" in nm else RAGGED)]
    if p == "(xs:ListInt)(ys:ListInt)":
        pool = DESC if "Desc" in nm else (ASC if "Asc" in nm or "merge" in nm.lower() else LISTS)
        pairs = [("[]", "[]"), ("[]", pool[3]), (pool[3], "[]"),
                 ("[5, 4]", "[3]") if "Desc" in nm else ("[1]", "[2, 3]")]
        while len(pairs) < n:
            pairs.append((rnd.choice(pool), rnd.choice(pool)))
        return [list(x) for x in pairs]
    if p == "(pivot:Int)(xs:ListInt)":
        return [[iarg(rnd.choice([0, 1, 2, -1, 3])), l] for l in LISTS]
    if p in ("(n:Nat)(xs:ListInt)", "(k:Nat)(xs:ListInt)"):
        out = [[str(k), l] for k in (0, 1, 2) for l in ("[]", "[5]")]
        while len(out) < n:
            out.append([str(rnd.randint(0, 5)), rnd.choice(LISTS)])
        return out
    if p == "(x:Int)(xs:ListInt)":
        out = [[iarg(rnd.randint(-6, 11)), rnd.choice(ASC)] for _ in range(n)]
        return [["3", "[]"], ["-3", "[1, 2]"], ["2", "[2, 2]"]] + out
    if p == "(n:Nat)(f:Int→Int)(seed:Int)":
        return [[str(k), f, iarg(s)] for k in (0, 1, 3, 5) for f in FUNS for s in (0, -2)]
    raise ValueError(f"no input generator for {params}")


def ref_compare(task, tactics, cl, inputs):
    """#eval candidate and reference side by side on `inputs` (one file)."""
    s = f"_{uid()}"
    pre, ref = ref_prelude(task, s, cl)
    params = _build_params(task.params_signature, task.test_cases, task.input_type)
    cand = f"rrcand_{task.theorem_name}{s}"
    lines = ["set_option linter.unusedVariables false", pre, "",
             f"def {cand} {params} : {task.return_type} := by",
             "  " + "\n  ".join(tactics), ""]
    for i, args in enumerate(inputs):
        a = " ".join(args)
        lines.append(f'#eval IO.println s!"@@{i}@@{{repr ({cand} {a})}}@@{{repr ({ref} {a})}}"')
    infos, errs = compile_msgs(cl, "\n".join(lines) + "\n")
    if errs:
        return {"ok": False, "error": "; ".join(errs[:3])[:600], "mismatches": []}
    mism, seen = [], 0
    for m in infos:
        if not m.startswith("@@"):
            continue
        _, idx, c, r = m.split("@@", 3)
        seen += 1
        if c != r:
            mism.append({"args": inputs[int(idx)], "candidate": c, "reference": r})
    if seen != len(inputs):
        return {"ok": False, "error": f"saw {seen}/{len(inputs)} outputs", "mismatches": mism}
    return {"ok": not mism, "n_inputs": len(inputs), "mismatches": mism[:6],
            "n_mismatch": len(mism)}


def canary(cl):
    """(a) correct passes, (b) wrong fails, (c) non-compiling fails."""
    from lean_mcts.benchmark.tasks import TASKS
    t = next(x for x in TASKS if x.name == "count_positive")
    good = ["exact (xs.filter (fun x => decide (x > 0))).length"]
    res = {
        "correct": standalone(t, good, cl)["passed"],
        "constant": standalone(t, ["exact 0"], cl)["passed"],
        "wrong_fn": standalone(t, ["exact xs.length"], cl)["passed"],
        "noncompiling": standalone(t, ["exact xs.fooBar 3"], cl)["passed"],
        "ref_correct": ref_compare(t, good, cl, fresh_inputs(t))["ok"],
        "ref_wrong": ref_compare(t, ["exact (xs.filter (fun x => decide (x ≥ 0))).length"], cl, fresh_inputs(t))["ok"],
    }
    res["ok"] = (res["correct"] and res["ref_correct"] and not res["constant"]
                 and not res["wrong_fn"] and not res["noncompiling"] and not res["ref_wrong"])
    return res
