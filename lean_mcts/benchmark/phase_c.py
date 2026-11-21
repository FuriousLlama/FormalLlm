"""S30 Phase C — `let rec` base-prior probe.

Tests whether base Qwen2.5-Coder-1.5B and Qwen2.5-Coder-7B can produce
`let rec` tactics when explicitly asked. Answers the 3-way causal question
from sprint30.md §"Phase C":

  (a) LoRA capacity insufficient            → Phase D (go bigger)
  (b) Tokenizer/prompt format issue         → fix S29 training config
  (c) Base model has a strong prior against → Phase D (stronger base)

Methodology (from plan):
  - 20 diverse Lean-task prompts explicitly asking for `let rec`
  - 8 samples per prompt at temperature 0.8
  - Repeat across 5 sub-configs on different base models / framings

Required-pattern metric: fraction of generations that contain the
literal substring `let rec`.

Usage:
    python -m lean_mcts.benchmark.phase_c \\
        --out sprint/benchmark/results/phase_c.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from lean_mcts.benchmark.phase_a import generate_local, load_local_model


PROMPTS = [
    "Write a Lean 4 tactic that uses `let rec` to sum all elements of a List Int.",
    "Write a Lean 4 tactic that uses `let rec` to compute the length of a list.",
    "Write a Lean 4 tactic that uses `let rec` to reverse a List Int.",
    "Write a Lean 4 tactic that uses `let rec` to return the last element of a List Int (use headD/default 0 for empty).",
    "Write a Lean 4 tactic that uses `let rec` to multiply all elements of a List Int (empty list returns 1).",
    "Write a Lean 4 tactic that uses `let rec` to find the maximum of a List Int (empty list returns 0).",
    "Write a Lean 4 tactic that uses `let rec` to return the list of partial sums of a List Int.",
    "Write a Lean 4 tactic that uses `let rec` to count elements greater than 0 in a List Int.",
    "Write a Lean 4 tactic that uses `let rec` to merge two sorted (ascending) lists of Int.",
    "Write a Lean 4 tactic that uses `let rec` to repeat an Int value n times into a List Int.",
    "Write a Lean 4 tactic that uses `let rec` to apply a function f : Int → Int n times to a seed.",
    "Write a Lean 4 tactic that uses `let rec` to return the first n elements of a List Int.",
    "Write a Lean 4 tactic that uses `let rec` to drop the first n elements of a List Int.",
    "Write a Lean 4 tactic that uses `let rec` to zip two List Int into a List (Int × Int).",
    "Write a Lean 4 tactic that uses `let rec` to fold f : Int → Int → Int over a List Int with seed 0.",
    "Write a Lean 4 tactic that uses `let rec` to compute the Fibonacci number at index n : Nat.",
    "Write a Lean 4 tactic that uses `let rec` to count occurrences of x : Int in a List Int.",
    "Write a Lean 4 tactic that uses `let rec` to build the list [0, 1, 2, ..., n-1] for n : Nat.",
    "Write a Lean 4 tactic that uses `let rec` to take elements of a List Int while they are positive.",
    "Write a Lean 4 tactic that uses `let rec` to count the parity flips in a List Int.",
]

LETREC_EXAMPLE = {
    "state": "n : Nat\n⊢ List Int",
    "tactic": (
        "exact let rec loop : Nat → List Int → List Int "
        "| 0, acc => acc "
        "| n+1, acc => loop n ((n : Int) :: acc); "
        "loop n []"
    ),
}


def build_bare_prompt(task: str) -> str:
    return (
        "You are a Lean 4 programmer. Output only a single Lean 4 tactic "
        "body (starting with `exact`) — no commentary.\n\n"
        f"Task: {task}\n\nTactic:"
    )


def build_fewshot_prompt(task: str, fewshot_pairs: list[dict]) -> str:
    parts = [
        "You are a Lean 4 programmer. Output only a single Lean 4 tactic "
        "body (starting with `exact`) — no commentary.",
        "",
    ]
    for p in fewshot_pairs:
        parts.append("Goal:")
        parts.append(p["state"])
        parts.append("Tactic:")
        parts.append(p["tactic"])
        parts.append("")
    parts.append(f"Task: {task}")
    parts.append("Tactic:")
    return "\n".join(parts)


def build_letrec_retrieval_prompt(task: str) -> str:
    return build_fewshot_prompt(task, [LETREC_EXAMPLE])


def letrec_rate(tactics: list[str]) -> tuple[int, int]:
    hits = sum(1 for t in tactics if "let rec" in t)
    return hits, len(tactics)


def run_config(model, tokenizer, label: str, prompt_fn, n_samples_per_prompt: int,
               temperature: float) -> dict:
    print(f"[phase_c] running config: {label}")
    all_tactics = []
    per_prompt = []
    t0 = time.time()
    for prompt_text in PROMPTS:
        prompt = prompt_fn(prompt_text)
        tactics = generate_local(
            model, tokenizer, prompt,
            n=n_samples_per_prompt, temperature=temperature,
            max_new_tokens=200,
        )
        all_tactics.extend(tactics)
        hits, total = letrec_rate(tactics)
        per_prompt.append({
            "task": prompt_text[:60],
            "n_unique": total,
            "n_letrec": hits,
            "tactics_head": [t[:100] for t in tactics[:4]],
        })
    wall = time.time() - t0
    hits, total = letrec_rate(all_tactics)
    print(f"  {label}: {hits}/{total} = {hits/total*100:.1f}% let_rec rate  "
          f"(wall {wall:.1f}s)")
    return {
        "label": label,
        "n_prompts": len(PROMPTS),
        "n_unique_total": total,
        "n_letrec": hits,
        "letrec_rate": round(hits / total, 3) if total else 0.0,
        "wall": round(wall, 1),
        "per_prompt": per_prompt,
    }


# ---------------------------------------------------------------------------
# 2026-11-21 honest re-run additions (additive; default behaviour unchanged).
#
# Each of the 20 PROMPTS is mapped to an explicit Lean goal (signature +
# return type) and test cases so outputs can be compiled and run. The goal
# is inferred from the prompt text; where the prompt is ambiguous the tests
# are chosen so every reasonable reading passes (noted per goal).
# ---------------------------------------------------------------------------

import re as _re

PROMPT_GOALS = [
    # (theorem_name, params_signature, return_type, tests[(args..., expected)], reference, note)
    ("pcSum", "(xs : List Int)", "Int",
     [("[1, 2, 3]", "6"), ("[]", "0"), ("[-1, 5]", "4")], "exact xs.sum", ""),
    ("pcLength", "(xs : List Int)", "Nat",
     [("[1, 2, 3]", "3"), ("[]", "0"), ("[5]", "1")], "exact xs.length", ""),
    ("pcReverse", "(xs : List Int)", "List Int",
     [("[1, 2, 3]", "[3, 2, 1]"), ("[]", "[]"), ("[4]", "[4]")], "exact xs.reverse", ""),
    ("pcLast", "(xs : List Int)", "Int",
     [("[1, 2, 3]", "3"), ("[]", "0"), ("[7]", "7")], "exact xs.getLastD 0", ""),
    ("pcProduct", "(xs : List Int)", "Int",
     [("[2, 3, 4]", "24"), ("[]", "1"), ("[-2, 5]", "-10")], "exact xs.foldl (· * ·) 1", ""),
    ("pcMax", "(xs : List Int)", "Int",
     [("[1, 5, 3]", "5"), ("[]", "0"), ("[4]", "4")],
     "exact xs.foldl max (xs.headD 0)", "all-negative input avoided (0-seed vs head-seed ambiguity)"),
    ("pcPartialSums", "(xs : List Int)", "List Int",
     [("[1, 2, 3]", "[1, 3, 6]"), ("[]", "[]"), ("[5, -5]", "[5, 0]")],
     "exact (xs.foldl (fun (acc : List Int × Int) x => (acc.1 ++ [acc.2 + x], acc.2 + x)) ([], 0)).1",
     "inclusive prefix sums"),
    ("pcCountPos", "(xs : List Int)", "Nat",
     [("[1, -2, 3, 0]", "2"), ("[]", "0"), ("[-1]", "0")], "exact (xs.filter (· > 0)).length", ""),
    ("pcMergeAsc", "(xs ys : List Int)", "List Int",
     [("[1, 3, 5]", "[2, 4]", "[1, 2, 3, 4, 5]"), ("[]", "[1]", "[1]"), ("[1, 1]", "[1]", "[1, 1, 1]")],
     "exact (xs ++ ys).mergeSort (· ≤ ·)", ""),
    ("pcReplicate", "(x : Int) (n : Nat)", "List Int",
     [("7", "3", "[7, 7, 7]"), ("1", "0", "[]"), ("(-2)", "2", "[-2, -2]")],
     "exact List.replicate n x", "argument order fixed by goal"),
    ("pcIterate", "(f : Int → Int) (n : Nat) (seed : Int)", "Int",
     [("(fun x => x + 1)", "3", "0", "3"), ("(fun x => x * 2)", "4", "1", "16"), ("(fun x => x - 5)", "0", "7", "7")],
     "exact Nat.iterate f n seed", ""),
    ("pcTake", "(n : Nat) (xs : List Int)", "List Int",
     [("2", "[1, 2, 3]", "[1, 2]"), ("0", "[1]", "[]"), ("5", "[1, 2]", "[1, 2]")], "exact xs.take n", ""),
    ("pcDrop", "(n : Nat) (xs : List Int)", "List Int",
     [("2", "[1, 2, 3]", "[3]"), ("0", "[1]", "[1]"), ("5", "[1, 2]", "[]")], "exact xs.drop n", ""),
    ("pcZip", "(xs ys : List Int)", "List (Int × Int)",
     [("[1, 2]", "[3, 4]", "[(1, 3), (2, 4)]"), ("[1, 2, 3]", "[4]", "[(1, 4)]"), ("[]", "[1]", "[]")],
     "exact xs.zip ys", ""),
    ("pcFold", "(f : Int → Int → Int) (xs : List Int)", "Int",
     [("(fun a b => a + b)", "[1, 2, 3]", "6"), ("(fun a b => max a b)", "[3, 7, 2]", "7"), ("(fun a b => a + b)", "[]", "0")],
     "exact xs.foldl f 0", "commutative f only, so foldl and foldr readings both pass"),
    ("pcFib", "(n : Nat)", "Nat",
     [("0", "0"), ("1", "1"), ("10", "55")],
     "exact (Nat.iterate (fun (p : Nat × Nat) => (p.2, p.1 + p.2)) n (0, 1)).1", "fib 0 = 0"),
    ("pcCountOcc", "(x : Int) (xs : List Int)", "Nat",
     [("2", "[1, 2, 2, 3]", "2"), ("5", "[]", "0"), ("1", "[1]", "1")], "exact xs.count x", ""),
    ("pcRange", "(n : Nat)", "List Nat",
     [("3", "[0, 1, 2]"), ("0", "[]"), ("1", "[0]")], "exact List.range n", "List Nat chosen"),
    ("pcTakeWhilePos", "(xs : List Int)", "List Int",
     [("[1, 2, -1, 3]", "[1, 2]"), ("[]", "[]"), ("[0, 1]", "[]")], "exact xs.takeWhile (· > 0)", ""),
    ("pcParityFlips", "(xs : List Int)", "Nat",
     [("[1, 2, 3]", "2"), ("[2, 4]", "0"), ("[]", "0")],
     "exact ((xs.zip xs.tail).filter (fun p => p.1 % 2 != p.2 % 2)).length",
     "ILL-DEFINED in prompt; interpreted as #adjacent pairs with different parity"),
]
assert len(PROMPT_GOALS) == len(PROMPTS)


def make_goal_task(i: int):
    from lean_mcts.benchmark.tasks import SynthesisTask  # noqa: F401
    from types import SimpleNamespace
    name, sig, ret, tests, ref, note = PROMPT_GOALS[i]
    keys = ["input", "input2", "input3", "input4"]
    tcs = []
    for t in tests:
        tc = {k: v for k, v in zip(keys, t[:-1])}
        tc["expected"] = t[-1]
        tcs.append(tc)
    return SimpleNamespace(name=name, theorem_name=name, params_signature=sig,
                           input_type="List Int", return_type=ret, test_cases=tcs,
                           description=PROMPTS[i], reference=ref, note=note)


def neutral_prompt(text: str) -> str:
    """Remove the `let rec` instruction; nothing else about recursion style."""
    out = text.replace("Write a Lean 4 tactic that uses `let rec` to ", "Write a Lean 4 tactic to ")
    assert "let rec" not in out and "recurs" not in out.lower(), out
    return out


def goal_state_for(i: int) -> str:
    from lean_mcts.benchmark.phase_a import build_goal_state
    return build_goal_state(make_goal_task(i))


def bare_goal_prompt(task_text: str, state: str) -> str:
    return (
        "You are a Lean 4 programmer. Output only a single Lean 4 tactic "
        "body (starting with `exact`) — no commentary.\n\n"
        f"Task: {task_text}\n\nGoal:\n{state}\nTactic:"
    )


def fewshot_goal_prompt(task_text: str, state: str, pairs: list[dict]) -> str:
    parts = [
        "You are a Lean 4 programmer. Output only a single Lean 4 tactic "
        "body (starting with `exact`) — no commentary.",
        "",
    ]
    for p in pairs:
        parts += ["Goal:", p["state"], "Tactic:", p["tactic"], ""]
    parts += [f"Task: {task_text}", "Goal:", state, "Tactic:"]
    return "\n".join(parts)


def trained_format_prompt(state: str) -> str:
    """The S29 LoRA training format (lean_mcts/train/dataset.py)."""
    return f"[STATE]\n{state}\n[TACTIC]\n"


def first_line_extract(raw: str) -> str:
    """Extraction used by inspect_generations.generate (origin of '0 of 125')."""
    return raw.split("\n")[0].strip()


def strip_lean_comments(t: str) -> str:
    t = _re.sub(r"/-.*?-/", " ", t, flags=_re.S)
    return _re.sub(r"--[^\n]*", " ", t)


def uses_let_rec(t: str) -> bool:
    return bool(_re.search(r"\blet\s+rec\b", strip_lean_comments(t)))


_FORBIDDEN = _re.compile(r"\b(sorry|admit)\b")


def score_honest(model, tokenizer, label: str, prompt_kind: str, fmt: str,
                 n: int, temperature: float, fewshot: list[dict]) -> dict:
    """Generate n samples per prompt; return raw + extracted samples.
    prompt_kind: orig | letrec_goal | neutral_goal
    fmt: bare | fewshot | letrec_k1 | trained (S29 [STATE] format)"""
    from lean_mcts.benchmark.phase_a import generate_local_raw
    rows = []
    for i, text in enumerate(PROMPTS):
        state = goal_state_for(i)
        ttext = neutral_prompt(text) if prompt_kind == "neutral_goal" else text
        extractor = None
        if fmt == "trained":
            prompt = trained_format_prompt(state)
            extractor = first_line_extract
            max_new = 256
        elif prompt_kind == "orig":
            prompt = {"bare": build_bare_prompt,
                      "fewshot": lambda t: build_fewshot_prompt(t, fewshot),
                      "letrec_k1": build_letrec_retrieval_prompt}[fmt](ttext)
            max_new = 200
        else:
            if fmt == "bare":
                prompt = bare_goal_prompt(ttext, state)
            elif fmt == "fewshot":
                prompt = fewshot_goal_prompt(ttext, state, fewshot)
            else:
                prompt = fewshot_goal_prompt(ttext, state, [LETREC_EXAMPLE])
            max_new = 200
        samples = generate_local_raw(model, tokenizer, prompt, n=n,
                                     temperature=temperature,
                                     max_new_tokens=max_new, extractor=extractor)
        rows.append({"prompt_idx": i, "goal": PROMPT_GOALS[i][0], "prompt": prompt,
                     "samples": samples})
    print(f"  [gen] {label}")
    return {"label": label, "prompt_kind": prompt_kind, "format": fmt,
            "n_per_prompt": n, "temperature": temperature, "per_prompt": rows}


def verify_honest(cond: dict, pool) -> dict:
    """Fill in let-rec / compile / tests levels for every sample."""
    jobs, where = [], []
    for row in cond["per_prompt"]:
        task = make_goal_task(row["prompt_idx"])
        for s in row["samples"]:
            t = s["extracted"]
            s["substring_let_rec"] = "let rec" in t
            s["uses_let_rec"] = bool(t) and uses_let_rec(t)
            s["forbidden"] = bool(_FORBIDDEN.search(strip_lean_comments(t))) if t else False
            if s["uses_let_rec"] and not s["forbidden"]:
                jobs.append((task, t))
                where.append(s)
            else:
                s["compiles"] = None
                s["passes"] = None
    res = pool.verify_many(jobs)
    for s, r in zip(where, res):
        s["verdict"] = r["verdict"]
        s["compiles"] = r["verdict"] in ("pass", "wrong_output")
        s["passes"] = r["verdict"] == "pass"
        s["error"] = (r.get("error") or "")[:300]
    samples = [s for row in cond["per_prompt"] for s in row["samples"]]
    N = len(samples)
    uniq = list(dict.fromkeys(s["extracted"] for s in samples if s["extracted"]))
    cond["summary"] = {
        "n_samples": N,
        "n_unique_nonempty": len(uniq),
        "old_metric_substring_over_unique": round(sum("let rec" in u for u in uniq) / len(uniq), 3) if uniq else 0.0,
        "substring_rate": round(sum(s["substring_let_rec"] for s in samples) / N, 3),
        "let_rec_compiles_rate": round(sum(bool(s.get("compiles")) and s["uses_let_rec"] for s in samples) / N, 3),
        "let_rec_passes_rate": round(sum(bool(s.get("passes")) and s["uses_let_rec"] for s in samples) / N, 3),
        "n_substring": sum(s["substring_let_rec"] for s in samples),
        "n_let_rec_compiles": sum(bool(s.get("compiles")) and s["uses_let_rec"] for s in samples),
        "n_let_rec_passes": sum(bool(s.get("passes")) and s["uses_let_rec"] for s in samples),
        "n_infra_error": sum(s.get("verdict") == "infra_error" for s in samples),
        "n_prompts_with_passing_let_rec": sum(any(s.get("passes") for s in row["samples"]) for row in cond["per_prompt"]),
    }
    return cond


def run_honest(args):
    import torch
    from lean_mcts.benchmark.phase_a import VerifyPool
    fewshot = json.loads(Path(args.fewshot).read_text())["pairs"]
    n, T = args.n, args.temperature
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    raw_path = out.with_suffix(".generations.json")
    conds: dict[str, dict] = {}

    def save():
        raw_path.write_text(json.dumps(conds, indent=1))

    def run_set(model, tok, prefix, fmts):
        for fmt in fmts:
            kinds = ["orig", "letrec_goal", "neutral_goal"] if fmt != "trained" else ["trained_goal"]
            for kind in kinds:
                lab = f"{prefix}|{fmt}|{kind}"
                conds[lab] = score_honest(model, tok, lab, kind, fmt, n, T, fewshot)
                save()

    base_fmts = ["bare", "fewshot", "letrec_k1", "trained"]
    print("[phase_c/honest] 1.5B")
    m, t = load_local_model("Qwen/Qwen2.5-Coder-1.5B")
    run_set(m, t, "1.5B", base_fmts)
    del m
    torch.cuda.empty_cache()
    if not args.skip_7b:
        print("[phase_c/honest] 7B")
        m, t = load_local_model("Qwen/Qwen2.5-Coder-7B")
        run_set(m, t, "7B", ["bare", "fewshot"])
        del m
        torch.cuda.empty_cache()
    if not args.skip_lora:
        from peft import PeftModel
        for name, path in [("S29-A", "lean_mcts/train/diverse_adapter_v2/S29-A/C_curriculum"),
                           ("S29-C", "lean_mcts/train/diverse_adapter_v2/S29-C/C_curriculum")]:
            print(f"[phase_c/honest] LoRA {name}")
            base, t = load_local_model("Qwen/Qwen2.5-Coder-1.5B")
            m = PeftModel.from_pretrained(base, path, is_trainable=False)
            m.eval()
            run_set(m, t, f"LoRA_{name}", ["trained", "bare"])
            del m, base
            torch.cuda.empty_cache()

    print("[phase_c/honest] verifying")
    pool = VerifyPool(n_workers=args.verify_workers)
    if not pool.ping():
        raise SystemExit("daemon ping failed")
    # Validate the goal/test definitions with reference solutions first.
    refs = pool.verify_many([(make_goal_task(i), PROMPT_GOALS[i][4]) for i in range(len(PROMPTS))])
    ref_ok = {PROMPT_GOALS[i][0]: r["verdict"] for i, r in enumerate(refs)}
    print("  reference check:", ref_ok)
    for lab in conds:
        verify_honest(conds[lab], pool)
        s = conds[lab]["summary"]
        print(f"  {lab:<36} sub={s['substring_rate']:.3f} comp={s['let_rec_compiles_rate']:.3f} "
              f"pass={s['let_rec_passes_rate']:.3f}  (N={s['n_samples']})")
    save()
    out.write_text(json.dumps({
        "config": {"n_per_prompt": n, "temperature": T, "top_p": 0.95,
                   "n_prompts": len(PROMPTS), "generations_file": str(raw_path),
                   "reference_check": ref_ok,
                   "goals": [{"name": g[0], "sig": g[1], "ret": g[2], "note": g[5]} for g in PROMPT_GOALS]},
        "summary": {lab: c["summary"] for lab, c in conds.items()},
        "conditions": conds,
    }, indent=1))
    print(f"[phase_c/honest] -> {out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--honest", action="store_true",
                        help="(2026-11-21) compile-checked let-rec scoring, neutral "
                             "prompts, LoRA conditions; raw generations saved")
    parser.add_argument("--skip-lora", action="store_true")
    parser.add_argument("--verify-workers", type=int, default=3)
    parser.add_argument("--out", required=True)
    parser.add_argument("--fewshot",
                        default="sprint/benchmark/results/phase_a_fewshot.json")
    parser.add_argument("--n", type=int, default=8)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--skip-7b", action="store_true")
    args = parser.parse_args()

    if args.honest:
        run_honest(args)
        return

    fewshot = json.loads(Path(args.fewshot).read_text())["pairs"]

    results = {}

    # 1.5B — three configs
    print("[phase_c] loading Qwen2.5-Coder-1.5B")
    m15, t15 = load_local_model("Qwen/Qwen2.5-Coder-1.5B")

    results["1.5B_bare"] = run_config(
        m15, t15, "1.5B_bare", build_bare_prompt, args.n, args.temperature,
    )
    results["1.5B_fewshot"] = run_config(
        m15, t15, "1.5B_fewshot",
        lambda task: build_fewshot_prompt(task, fewshot),
        args.n, args.temperature,
    )
    results["1.5B_letrec_k1"] = run_config(
        m15, t15, "1.5B_letrec_k1", build_letrec_retrieval_prompt,
        args.n, args.temperature,
    )

    # Free 1.5B before loading 7B
    import torch
    del m15
    torch.cuda.empty_cache()

    if not args.skip_7b:
        print("[phase_c] loading Qwen2.5-Coder-7B")
        m7, t7 = load_local_model("Qwen/Qwen2.5-Coder-7B")

        results["7B_bare"] = run_config(
            m7, t7, "7B_bare", build_bare_prompt, args.n, args.temperature,
        )
        results["7B_fewshot"] = run_config(
            m7, t7, "7B_fewshot",
            lambda task: build_fewshot_prompt(task, fewshot),
            args.n, args.temperature,
        )
        del m7
        torch.cuda.empty_cache()

    # Write + print summary table
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2))

    print()
    print("=" * 64)
    print("Phase C — let_rec base-prior probe summary")
    print("=" * 64)
    print(f"{'config':<20} {'let_rec/unique':<18} {'rate':<10} {'wall':<10}")
    print("-" * 64)
    for cfg, r in results.items():
        print(f"{cfg:<20} {r['n_letrec']}/{r['n_unique_total']:<16} "
              f"{r['letrec_rate']:<10.1%} {r['wall']:<10.1f}s")
    print("=" * 64)
    print(f"output: {args.out}")


if __name__ == "__main__":
    main()
