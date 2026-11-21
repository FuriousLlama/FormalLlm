"""Job 1 (2026-11-21): standalone + reference re-verification of recorded
solves in the S26 (107/120), S27 (104/120) and S27 diagnostic run sets."""
from __future__ import annotations

import json
import queue
import sys
import threading
from collections import defaultdict

from lean_mcts.benchmark import tasks as T
from lean_mcts.benchmark.rerun_common import (OUT, ROOT, canary, client,
                                              fresh_inputs, ref_compare,
                                              standalone)

TIERS = {
    "t2": T.TASKS, "hard": T.HARD_TASKS, "v2": T.V2_TASKS,
    "recurrence": T.RECURRENCE_TASKS, "fundamentals": T.FUNDAMENTALS_TASKS,
    "multistep": T.MULTISTEP_TASKS, "deep_multistep": T.DEEP_MULTISTEP_TASKS,
    "compositional": T.COMPOSITIONAL_TASKS, "recursive": T.RECURSIVE_TASKS,
    "stretch": T.STRETCH_TASKS, "v3": T.V3_TASKS, "v3_stretch": T.V3_STRETCH_TASKS,
    "diag": T.DIAG_TASKS,
}
SETS = {
    "S26": {"20260329_120327": ["t2"], "20260330_125719": ["hard"],
            "20260330_131618": ["v2"], "20260329_122614": ["recurrence"],
            "20260330_131845": ["fundamentals"], "20260329_103252": ["multistep"],
            "20260330_134244": ["deep_multistep"], "20260329_122724": ["compositional"],
            "20260329_122757": ["recursive"], "20260329_120219": ["stretch"],
            "20260329_114841": ["v3"], "20260329_115213": ["v3_stretch"]},
    "S27": {"20260330_225303": ["t2", "hard", "v2"], "20260330_225321": ["recurrence"],
            "20260330_225621": ["fundamentals"], "20260330_230346": ["multistep"],
            "20260330_231214": ["deep_multistep"], "20260330_231253": ["compositional"],
            "20260330_231332": ["recursive"], "20260330_231841": ["stretch"],
            "20260330_231909": ["v3"], "20260330_232011": ["v3_stretch"]},
    "S27_diag": {"20260330_214544": ["diag"], "20260330_221125": ["diag"]},
}


def find_task(name, tiers):
    hits = [(tr, t) for tr in tiers for t in TIERS[tr] if t.name == name]
    if len(hits) != 1:
        raise RuntimeError(f"{name}: {len(hits)} matches in {tiers}")
    return hits[0]


def main():
    jobs, recorded = [], {}
    for set_name, files in SETS.items():
        for ts, tiers in files.items():
            recs = json.loads((ROOT / f"benchmark/results/run_{ts}_0.json").read_text())
            for r in recs:
                tier, task = find_task(r["task"], tiers)
                key = (set_name, ts, r["task"])
                recorded[key] = {"set": set_name, "run": f"run_{ts}_0.json", "tier": tier,
                                 "task": r["task"], "recorded_solved": r["solved"],
                                 "tactics": r["tactics"]}
                if r["solved"]:
                    jobs.append((key, task, r["tactics"]))
    print(f"[job1] {len(jobs)} recorded solves to check", flush=True)

    q = queue.Queue()
    for j in jobs:
        q.put(j)
    results = {}
    canaries = {}

    def worker(i):
        cl = client(i)
        canaries[f"start_w{i}"] = canary(cl)
        while True:
            try:
                key, task, tac = q.get_nowait()
            except queue.Empty:
                break
            sa = standalone(task, tac, cl)
            try:
                rc = ref_compare(task, tac, cl, fresh_inputs(task))
            except Exception as e:  # noqa
                rc = {"ok": False, "error": f"ref harness: {e}"[:400], "mismatches": []}
            if not sa["passed"]:
                cls = "FALSE-POSITIVE"
            elif rc["ok"]:
                cls = "GENUINE"
            elif rc.get("mismatches"):
                cls = "WEAK-TEST PASS"
            else:
                cls = "UNVERIFIABLE"
            results[key] = {"standalone": sa, "reference": rc, "classification": cls}
            print(f"  {key[0]} {key[2]:30s} {cls}", flush=True)
        canaries[f"end_w{i}"] = canary(cl)

    nw = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    th = [threading.Thread(target=worker, args=(i,)) for i in range(nw)]
    [t.start() for t in th]
    [t.join() for t in th]

    out, totals = [], defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for key, rec in recorded.items():
        rec = dict(rec)
        if key in results:
            rec.update(results[key])
        else:
            rec["classification"] = "RECORDED-UNSOLVED"
        out.append(rec)
        tt = totals[rec["set"]][rec["tier"]]
        tt["n_tasks"] += 1
        tt["recorded_solved"] += int(rec["recorded_solved"])
        tt[rec["classification"]] += 1
    summary = {s: {tr: dict(v) for tr, v in d.items()} for s, d in totals.items()}
    for s, d in summary.items():
        d["TOTAL"] = {k: sum(v.get(k, 0) for v in d.values()) for k in
                      ["n_tasks", "recorded_solved", "GENUINE", "FALSE-POSITIVE",
                       "WEAK-TEST PASS", "UNVERIFIABLE"]}
    (OUT / "job1_solves.json").write_text(json.dumps(
        {"canaries": canaries, "sets": SETS, "summary": summary, "records": out},
        indent=1, ensure_ascii=False))
    print(json.dumps(summary, indent=1))
    print(json.dumps(canaries))


if __name__ == "__main__":
    main()
