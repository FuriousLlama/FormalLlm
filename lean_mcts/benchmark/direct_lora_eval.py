"""Direct LoRA model evaluation on diagnostic tasks.

Bypasses MCTS. For each diagnostic task:
  1. Build the goal state (matching the benchmark format)
  2. Generate N candidates from the LoRA model
  3. Verify each through Pantograph (daemon)
  4. Report solved/failed per task

This measures whether the LoRA model alone can generate solutions,
without the expensive MCTS tree search overhead.

Usage:
    python -m lean_mcts.benchmark.direct_lora_eval \\
        --adapter lean_mcts/train/diverse_adapter/B_loss_weighted \\
        --num-candidates 20 \\
        --output /tmp/direct_lora_B.jsonl
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from lean_mcts.benchmark.tasks import get_tasks
from lean_mcts.env.pantograph_client import PantographClient
from lean_mcts.eval.oracle import daemon_warm_verify


def build_goal_state(task) -> str:
    """Build the goal state string matching the benchmark format.

    For task secondSmallest (xs : List Int) : Int, this produces:
        xs : List Int
        ⊢ Int
    """
    # Task has input_type (e.g. "List Int") and return_type (e.g. "Int")
    # For tasks like dutch_flag, the function has (pivot : Int) (xs : List Int)
    # These have params_signature set
    if task.params_signature:
        return f"{task.params_signature}\n⊢ {task.return_type}"
    else:
        # Default: single xs parameter with input_type
        return f"xs : {task.input_type}\n⊢ {task.return_type}"


def generate_candidates(model, tokenizer, state: str, n: int = 20,
                         max_length: int = 256) -> list[str]:
    """Generate N tactic candidates from the LoRA model."""
    import torch

    # Build prompt matching training format
    prompt = f"[STATE]\n{state}\n[TACTIC]\n"
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    # Sample n completions
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_length=inputs["input_ids"].shape[1] + max_length,
            num_return_sequences=n,
            do_sample=True,
            temperature=0.8,
            top_p=0.95,
            pad_token_id=tokenizer.pad_token_id,
        )

    # Decode and extract the tactic part (after [TACTIC]\n)
    tactics = []
    for out in outputs:
        text = tokenizer.decode(out, skip_special_tokens=True)
        if "[TACTIC]" in text:
            tactic = text.split("[TACTIC]", 1)[1].strip()
            # Clean up: take up to first newline or end
            tactic = tactic.split("\n")[0].strip()
            if tactic:
                tactics.append(tactic)
    return tactics


def load_model(base_model: str, adapter_path: str):
    """Load base model + LoRA adapter."""
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"Loading tokenizer from {base_model}...")
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"Loading base model {base_model}...")
    model = AutoModelForCausalLM.from_pretrained(
        base_model, torch_dtype=torch.bfloat16, trust_remote_code=True,
    )
    print(f"Loading LoRA adapter from {adapter_path}...")
    model = PeftModel.from_pretrained(model, adapter_path)
    model.to("cuda")
    model.eval()
    return model, tokenizer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True, help="LoRA adapter path")
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-Coder-1.5B")
    parser.add_argument("--num-candidates", type=int, default=20)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--output", default="/tmp/direct_lora_eval.jsonl")
    parser.add_argument("--tier", default="diag_all")
    parser.add_argument("--tasks", default=None,
                        help="Comma-separated task names (overrides --tier)")
    args = parser.parse_args()

    # Load model
    model, tokenizer = load_model(args.base_model, args.adapter)

    # Get tasks
    if args.tasks:
        from lean_mcts.benchmark.tasks import get_task
        tasks = [get_task(t) for t in args.tasks.split(",")]
    else:
        tasks = get_tasks(args.tier)
    print(f"\nEvaluating on {len(tasks)} tasks")

    # Daemon for verification
    client = PantographClient()
    if not client.ping():
        print("ERROR: daemon not running")
        return
    print("Daemon OK")

    results = []
    solved_count = 0

    for i, task in enumerate(tasks):
        print(f"\n[{i+1}/{len(tasks)}] {task.name} ({task.category})")
        state = build_goal_state(task)
        print(f"  Goal: {state}")

        start = time.time()
        candidates = generate_candidates(
            model, tokenizer, state,
            n=args.num_candidates, max_length=args.max_length,
        )
        gen_time = time.time() - start
        print(f"  Generated {len(candidates)} candidates in {gen_time:.1f}s")

        # Verify each through daemon
        solved = False
        solving_tactic = None
        unique_candidates = list(dict.fromkeys(candidates))  # dedup preserving order
        print(f"  Unique: {len(unique_candidates)}")

        for j, tactic in enumerate(unique_candidates[:args.num_candidates]):
            # Ensure it starts with "exact "
            if not tactic.startswith("exact"):
                continue
            try:
                result = daemon_warm_verify(task, [tactic], client, verbose=False)
                if result.get("solved"):
                    solved = True
                    solving_tactic = tactic
                    print(f"  SOLVED at candidate {j}: {tactic[:80]}")
                    break
            except Exception as e:
                continue

        if not solved:
            print(f"  FAILED")
            # Print a couple of samples
            for j, t in enumerate(unique_candidates[:3]):
                print(f"    sample {j}: {t[:100]}")

        results.append({
            "task": task.name,
            "category": task.category,
            "solved": solved,
            "solving_tactic": solving_tactic,
            "n_candidates": len(candidates),
            "n_unique": len(unique_candidates),
            "gen_time": gen_time,
            "sample_candidates": unique_candidates[:5],
        })
        if solved:
            solved_count += 1

    # Summary
    print(f"\n{'='*60}")
    print(f"Total: {solved_count}/{len(tasks)}")
    print(f"{'='*60}")
    by_cat: dict[str, list[int]] = {}
    for r in results:
        cat = r["category"]
        by_cat.setdefault(cat, [0, 0])
        by_cat[cat][1] += 1
        if r["solved"]:
            by_cat[cat][0] += 1
    for cat, (n, tot) in sorted(by_cat.items()):
        print(f"  {cat}: {n}/{tot}")

    # Write results
    with open(args.output, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"\nResults: {args.output}")

    client.close()


if __name__ == "__main__":
    main()
