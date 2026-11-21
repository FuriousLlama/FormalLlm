"""Job 2 (2026-11-21): adversarial supplementary tests for held_out_12 and
standalone re-verification of the saved Phase A candidate tactics."""
from __future__ import annotations

import json
import queue
import threading

from lean_mcts.benchmark import tasks as T
from lean_mcts.benchmark.rerun_common import (OUT, ROOT, canary, client,
                                              compile_msgs, ref_prelude,
                                              standalone, uid)

G = ["[]", "[0]", "[-3]", "[7]", "[2, 2]", "[-1, -1, -1]", "[3, -1, 3]",
     "[1, 2, 2, 3]", "[-5, 4, -3, 2]"]
EXTRA = {
    "eval_stack": ["[0]", "[3, 0]", "[1, 2, 0]", "[1, 2, 0, 3, 0]", "[0, 0]",
                   "[1, 2, 3, 0, 0]", "[-1, 2, 0]", "[5, -3, 0, 4]", "[1, 2, 3]",
                   "[4, 0, 6]", "[-2, -3]"],
    "majority_element": G + ["[1, 2, 1]", "[2, 2, 1, 1, 2]", "[3, 3, 4]", "[-1, 5, -1]"],
    "map_with_index": G + ["[1, 1, 1, 1]", "[-2, 0, 2]"],
    "max_subarray_sum": ["[-1, 3]", "[3, -1]", "[-2, 1, -3, 4, -1, 2, 1, -5, 4]",
                         "[-3, 5, -1, 2]", "[1, -5, 2]"] + G,
    "prefix_while_increasing": ["[1, 1, 2]", "[1, 2, 2, 3]", "[3, 3]",
                                "[1, 2, 3, 3, 4]", "[5, 6, 6]"] + G,
    "unfold_n": [["1", "(fun x : Int => x + 1)", "9"], ["2", "(fun x : Int => -x)", "3"],
                 ["5", "(fun x : Int => x * 2)", "(-1)"], ["3", "(fun x : Int => x - 5)", "0"],
                 ["0", "(fun x : Int => x * 3)", "(-4)"], ["4", "(fun x : Int => x * x)", "2"],
                 ["6", "(fun x : Int => 0)", "7"], ["2", "(fun x : Int => x + 1)", "(-1)"]],
    "sum_at_odd_indices": G + ["[1, 2, 3, 4, 5, 6]", "[0, -9]"],
    "zip_diff": G + ["[1, 2, 3, 4, 5, 6]", "[-4, 4]"],
    "longest_increasing_run": G + ["[1, 2, 1, 2, 3]", "[1, 1, 2, 3]", "[3, 2, 3]",
                                   "[-3, -2, -1, -1]"],
    "max_subarray_product": ["[-3]", "[-2, 3]", "[3, -2]", "[-1, -2, -3]", "[0, -2]",
                             "[2, -1, 2]", "[-2, -3, 0, 4]", "[-1, 0]"] + G,
    "merge_sorted_desc": [["[5, 4]", "[3]"], ["[3]", "[5, 4]"], ["[5, 1]", "[4, 2]"],
                          ["[9, 7, 5]", "[8, 6]"], ["[2, 2]", "[2]"], ["[-1, -3]", "[-2]"],
                          ["[]", "[0]"], ["[0]", "[]"], ["[1]", "[1]"], ["[10]", "[9, 8, 7]"],
                          ["[6, 5, 4]", "[3, 2, 1]"]],
    "window_sums": [["0", "[1, 2]"], ["1", "[]"], ["2", "[-1, 1]"], ["3", "[1, 2, 3]"],
                    ["4", "[1, 2, 3]"], ["2", "[5, 5, 5]"], ["1", "[-3]"],
                    ["2", "[1, -1, 1, -1]"], ["0", "[]"], ["3", "[-2, 0, 2, 4]"]],
}
FILES = ["phase_a_frontier_tactics", "phase_a_gemma4_26b_tactics",
         "phase_a_gemma4_26b_v2_tactics", "phase_a_gemma4_26b_v3_tactics"]


def to_tc(args):
    if isinstance(args, str):
        args = [args]
    tc = {"input": args[0]}
    for i, a in enumerate(args[1:], start=2):
        tc[f"input{i}"] = a
    return tc


def build_extra(cl, tasks):
    out = {}
    for t in tasks:
        cases = []
        for args in EXTRA[t.name]:
            tc = to_tc(args)
            a = " ".join([tc["input"]] + [tc[k] for k in sorted(tc) if k.startswith("input") and k != "input"])
            s = f"_{uid()}"
            pre, ref = ref_prelude(t, s)
            infos, errs = compile_msgs(cl, f"{pre}\n\n#eval {ref} {a}\n")
            if errs or len(infos) != 1:
                raise RuntimeError(f"ref eval failed {t.name} {a}: {errs} {infos}")
            tc["expected"] = infos[0]
            cases.append(tc)
        # sanity: ref reproduces every ORIGINAL expected value
        for tc in t.test_cases:
            a = " ".join([tc["input"]] + [tc[k] for k in sorted(tc) if k.startswith("input") and k != "input"])
            s = f"_{uid()}"
            pre, ref = ref_prelude(t, s)
            infos, errs = compile_msgs(cl, f"{pre}\n\n#eval {ref} {a}\n")
            if errs or infos != [tc["expected"].strip()]:
                print(f"  WARN ref disagrees with frozen test {t.name} {a}: ref={infos} {errs} frozen={tc['expected']}")
        out[t.name] = cases
    return out


def main():
    tasks = T.HELD_OUT_12_TASKS
    cl0 = client(0)
    canaries = {"start": canary(cl0)}
    extra = build_extra(cl0, tasks)
    (OUT / "held_out_12_extra_tests.json").write_text(json.dumps(
        {"note": "Supplementary adversarial tests; expected values produced by #eval of the "
                 "reference implementations (TasksDiagnostic.lean *_ref; docstring refs of "
                 "held_out_hard_tasks.py for the 6 S29 hard tasks). Frozen tests untouched.",
         "tests": extra}, indent=1, ensure_ascii=False))

    q = queue.Queue()
    for f in FILES:
        d = json.loads((ROOT / f"sprint/benchmark/results/{f}.json").read_text())
        for t in tasks:
            for ci, tac in enumerate(d.get(t.name, [])):
                q.put((f, t, ci, tac))
    res = {}

    def worker(i):
        cl = client(i)
        while True:
            try:
                f, t, ci, tac = q.get_nowait()
            except queue.Empty:
                break
            a = standalone(t, [tac], cl)
            b = standalone(t, [tac], cl, test_cases=list(t.test_cases) + extra[t.name]) if a["passed"] else None
            res[(f, t.name, ci)] = {"tactic": tac, "orig": a, "strict": b}

    th = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    [x.start() for x in th]
    [x.join() for x in th]
    canaries["end"] = canary(cl0)

    report = {"canaries": canaries, "files": {}}
    for f in FILES:
        per = {}
        for t in tasks:
            rs = [res[k] for k in sorted(res) if k[0] == f and k[1] == t.name]
            na = sum(r["orig"]["passed"] for r in rs)
            nb = sum(bool(r["strict"] and r["strict"]["passed"]) for r in rs)
            flips = [{"tactic": r["tactic"], "failing": r["strict"]["fails"][:2],
                      "error": r["strict"]["error"]}
                     for r in rs if r["orig"]["passed"] and not r["strict"]["passed"]]
            per[t.name] = {"n_candidates": len(rs), "pass_orig": na, "pass_strict": nb,
                           "flipped_candidates": flips,
                           "candidates": rs}
        report["files"][f] = {
            "solved_orig": sum(v["pass_orig"] > 0 for v in per.values()),
            "solved_strict": sum(v["pass_strict"] > 0 for v in per.values()),
            "per_task": per}
        print(f, report["files"][f]["solved_orig"], report["files"][f]["solved_strict"],
              {k: (v["pass_orig"], v["pass_strict"]) for k, v in per.items()}, flush=True)
    (OUT / "job2_heldout_strict.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(json.dumps(canaries))


if __name__ == "__main__":
    main()
