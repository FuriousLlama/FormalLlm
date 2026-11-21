"""Reproduce positional mis-attribution in oracle._parse_batch_results (2026-11-21)."""
import json
from lean_mcts.benchmark import tasks as T
from lean_mcts.eval.oracle import _build_batch_eval_code, _parse_batch_results
from lean_mcts.benchmark.rerun_common import OUT, client, canary, standalone, uid

t = next(x for x in T.MULTISTEP_TASKS if x.name == "count_pos_neg")
n = len(t.test_cases)
FAIL = ["constructor", "norm_num", "norm_num"]
GOOD = ["constructor", "exact (xs.filter (fun x => decide (x > 0))).length",
        "exact (xs.filter (fun x => decide (x < 0))).length"]
cl = client(1)
out = {"task": t.name, "n_tests": n, "canary_start": canary(cl),
       "standalone_fail": standalone(t, FAIL, cl)["passed"],
       "standalone_good": standalone(t, GOOD, cl)["passed"], "batches": []}
for k in (1, n, 2 * n):
    batch = [FAIL] * k + [GOOD] + [FAIL] * 4
    code = _build_batch_eval_code(t.theorem_name, t.return_type, batch, t.test_cases,
                                  input_type=t.input_type, batch_id=uid(),
                                  params_signature=t.params_signature)
    units = cl.check_compile(code)
    res = _parse_batch_results(units, batch, t, [c["expected"] for c in t.test_cases], n)
    nmsg = sum(1 for cu in units for m in cu["messages"] if m["severity"] in ("INFORMATION", "ERROR"))
    out["batches"].append({"layout": f"{k}xFAIL + GOOD@{k} + 4xFAIL", "n_messages": nmsg,
                           "passed_indices": [i for i, r in enumerate(res) if r.passed],
                           "good_index": k})
out["canary_end"] = canary(cl)
(OUT / "mechanism_batch_misattribution.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
