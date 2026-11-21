"""S30 supplemental probe — held_out_12 via an Ollama-hosted model.

Generates 8 candidate tactics per task by calling the Ollama /api/chat
endpoint once per task and asking for a JSON array of 8 structurally
diverse variants (matching the Phase A frontier methodology so the
numbers are comparable). Writes a tactics JSON that can be fed into
`phase_a.py --tactics-json` for oracle verification.

Usage:
    python -m lean_mcts.benchmark.phase_a_ollama \\
        --model gemma4:26b \\
        --out sprint/benchmark/results/phase_a_gemma4_26b_tactics.json
"""

from __future__ import annotations

import argparse
import os
import html
import json
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

from lean_mcts.benchmark.phase_a import build_goal_state
from lean_mcts.benchmark.tasks import get_tasks


def param_names(task) -> list[str]:
    """Extract parameter names from the task's params_signature (if any)."""
    if not getattr(task, "params_signature", None):
        return ["xs"]
    names: list[str] = []
    for name_group, _ty in re.findall(r"\(([^:]+)\s*:\s*([^)]+)\)", task.params_signature):
        names.extend(name_group.strip().split())
    return names or ["xs"]


def format_test_case(task, tc: dict) -> str:
    """Render a test case line showing ALL input args with their param names.

    The held_out_hard_tasks format uses `input`, `input2`, `input3`, ... for
    multi-arg tasks. The default Phase A probe only showed `input`, which
    produced nonsensical previews for `merge_sorted_desc` / `window_sums` /
    `unfold_n`. This helper reconstructs the full argument tuple.
    """
    names = param_names(task)
    keys = ["input", "input2", "input3", "input4"]
    parts = []
    for name, key in zip(names, keys):
        if key in tc:
            parts.append(f"{name}={tc[key]}")
    expected = tc.get("expected", "")
    return "  " + "  ".join(parts) + f"  →  " + expected

# Override with the OLLAMA_URL environment variable or --url.
DEFAULT_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
DEFAULT_FEWSHOT = Path("sprint/benchmark/results/phase_a_fewshot.json")


def build_messages(fewshot: list[dict], task) -> list[dict]:
    system = (
        "You are an expert Lean 4 programmer working on a synthesis benchmark. "
        "Given a tactic goal state, you produce exactly 8 structurally-diverse "
        "candidate tactic bodies as a JSON array of strings. Each candidate "
        "must start with `exact` and be a complete Lean 4 term. No commentary."
    )

    # Build a single user message with the few-shot context and the target.
    parts = ["Here are 5 example goal-tactic pairs covering distinct structural "
             "patterns (let_rec, match_list, foldl accumulator, method chain, "
             "two-list filter):", ""]
    for i, p in enumerate(fewshot, 1):
        parts.append(f"Example {i} ({p['kind']}, from {p['name']}):")
        parts.append("Goal state:")
        parts.append(p["state"])
        parts.append("Tactic:")
        parts.append(p["tactic"])
        parts.append("")
    parts.extend([
        f"TARGET TASK: {task.name}",
        f"Description: {task.description}",
        f"Theorem name: {task.theorem_name}",
        "",
        "Goal state (parameters are already intro-ed as hypotheses):",
        build_goal_state(task),
        "",
        "Test cases (first 3 shown, with ALL input args):",
    ])
    for tc in task.test_cases[:3]:
        parts.append(format_test_case(task, tc))
    parts.extend([
        "",
        "Produce 8 structurally diverse candidate tactics. Try different "
        "approaches (let_rec, match, foldl, foldr, method chain, have/exact). "
        "Each candidate must START with `exact` and be a complete Lean 4 term.",
        "",
        "CRITICAL Lean 4 SYNTAX NOTES (avoid Lean 3 / hallucinated idioms):",
        "  - Local bindings use `let name := ...; rest` or `let name := ...\\n  rest`. "
        "NEVER use Lean 3's `let ... in ...` syntax.",
        "  - For slicing, use `(xs.drop i).take k` or `xs.drop i |>.take k`. "
        "There is NO `List.sublist`.",
        "  - Method chaining: wrap sub-expressions in parentheses when needed. "
        "`xs.zip (List.range n).map f` is a parse bug — write "
        "`(xs.zip (List.range n)).map f` or use `|>` pipelines.",
        "  - Indexed iteration: `xs.mapIdx (fun (i : Nat) (x : Int) => ...)` or "
        "`xs.zipIdx` (returns `List (Int × Nat)`). No `enumerate`.",
        "  - Tuple field access uses `.1` and `.2` (NOT `.0` and `.1`). "
        "Nested: `pair.1.2` not `pair.0.1`.",
        "  - Recursive helpers with multiple arguments separate patterns with COMMAS: "
        "`let rec loop : List Int → Nat → Int | [], _ => 0 | x :: xs, n => n + loop xs (n + 1); loop xs 0`. "
        "Single-arg form: `let rec f : T → U | [] => a | x :: xs => b; f xs`.",
        "  - Nat recursion patterns: `| 0 => ... | n+1 => ...` or `Nat.rec base step n`.",
        "  - When using `foldl` with a tuple accumulator, access fields AFTER the fold: "
        "`(xs.foldl step init).1`, NOT `xs.foldl (fun ... |>.1) init`.",
        "  - `List.indexOf` does NOT exist in Lean 4 core; use `xs.findIdx?` if needed.",
        "",
        "OUTPUT: a JSON array of exactly 8 strings. No markdown fences, "
        "no commentary before or after. Example shape:",
        '["exact xs.sum", "exact xs.foldl (· + ·) 0", ...]',
    ])
    user = "\n".join(parts)

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def ollama_chat(url: str, model: str, messages: list[dict],
                temperature: float, num_predict: int, think: bool = False,
                timeout: int = 600) -> dict:
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        # Reasoning models (gemma4, qwq, etc.) spend unbounded tokens in a
        # `thinking` phase and only emit `content` after thinking finishes.
        # For a structured JSON-output probe, skip thinking entirely.
        "think": think,
        "options": {
            "temperature": temperature,
            "top_p": 0.95,
            "num_predict": num_predict,
        },
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_json_array(text: str) -> list[str] | None:
    """Extract the largest top-level `[...]` string array from text."""
    if not text:
        return None
    depth = 0
    start = -1
    candidates = []
    for i, ch in enumerate(text):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0 and start != -1:
                candidates.append(text[start:i + 1])
                start = -1
    for cand in sorted(candidates, key=len, reverse=True):
        try:
            arr = json.loads(cand)
            if isinstance(arr, list) and all(isinstance(x, str) for x in arr):
                return arr
        except json.JSONDecodeError:
            continue
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="gemma4:26b")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--fewshot", default=str(DEFAULT_FEWSHOT))
    parser.add_argument("--tier", default="held_out_12")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--num-predict", type=int, default=2048)
    parser.add_argument("--think", action="store_true",
                        help="Enable reasoning-model thinking phase (default: disabled)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    fewshot = json.loads(Path(args.fewshot).read_text())["pairs"]
    tasks = list(get_tasks(args.tier))
    print(f"[ollama] model={args.model}  url={args.url}")
    print(f"[ollama] tier={args.tier}  tasks={len(tasks)}  "
          f"fewshot={len(fewshot)}  temperature={args.temperature}")

    collected: dict[str, list[str]] = {}
    transcripts: dict[str, dict] = {}
    t0 = time.time()
    for i, task in enumerate(tasks):
        messages = build_messages(fewshot, task)
        t_start = time.time()
        try:
            resp = ollama_chat(
                args.url, args.model, messages,
                temperature=args.temperature,
                num_predict=args.num_predict,
                think=args.think,
            )
        except Exception as e:
            print(f"  [{i+1:2}/{len(tasks)}] {task.name:<28} ERROR: {e}")
            collected[task.name] = []
            transcripts[task.name] = {"error": str(e)}
            continue
        wall = time.time() - t_start

        msg = resp.get("message", {})
        content = msg.get("content", "") or ""
        thinking = msg.get("thinking", "") or ""
        done_reason = resp.get("done_reason", "?")
        eval_count = resp.get("eval_count", 0)

        arr = find_json_array(html.unescape(content))
        if not arr:
            # Fall back to searching thinking text, in case the model emitted
            # the JSON inside its reasoning rather than in content.
            arr = find_json_array(html.unescape(thinking))
        tactics = arr if arr else []
        collected[task.name] = tactics
        transcripts[task.name] = {
            "wall_sec": round(wall, 1),
            "eval_count": eval_count,
            "done_reason": done_reason,
            "content_head": content[:300],
            "thinking_head": thinking[:200],
            "n_tactics": len(tactics),
        }
        print(f"  [{i+1:2}/{len(tasks)}] {task.name:<28} "
              f"n={len(tactics)}  eval={eval_count}  {wall:.1f}s")

    total_wall = time.time() - t0
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(collected, indent=2, ensure_ascii=False))

    transcript_path = Path(args.out).with_suffix(".transcripts.json")
    transcript_path.write_text(json.dumps({
        "model": args.model,
        "url": args.url,
        "total_wall_sec": round(total_wall, 1),
        "transcripts": transcripts,
    }, indent=2))

    total_tactics = sum(len(v) for v in collected.values())
    parsed_tasks = sum(1 for v in collected.values() if v)
    print()
    print(f"[ollama] collected {total_tactics} tactics across "
          f"{parsed_tasks}/{len(tasks)} tasks")
    print(f"[ollama] total wall: {total_wall:.1f}s")
    print(f"[ollama] tactics -> {args.out}")
    print(f"[ollama] transcripts -> {transcript_path}")


if __name__ == "__main__":
    main()
