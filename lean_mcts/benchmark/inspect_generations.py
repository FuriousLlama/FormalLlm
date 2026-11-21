"""Inspect what the LoRA model is generating on benchmark tasks.

Samples multiple candidates per task and prints them without verification.
Useful for understanding what the diversity-trained model has learned to produce.

Usage:
    python -m lean_mcts.benchmark.inspect_generations \\
        --adapter lean_mcts/train/diverse_adapter/B_loss_weighted \\
        --n 10
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from lean_mcts.benchmark.tasks import get_task, get_tasks


def build_goal_state(task) -> str:
    """Build intro-style goal state matching the benchmark format."""
    if task.params_signature:
        # Multi-arg: parse params_signature like "(pivot : Int) (xs : List Int)"
        parts = re.findall(r'\(([^:]+)\s*:\s*([^)]+)\)', task.params_signature)
        lines = [f"{name.strip()} : {ty.strip()}" for name, ty in parts]
        return "\n".join(lines) + f"\n⊢ {task.return_type}"
    else:
        return f"xs : {task.input_type}\n⊢ {task.return_type}"


def load_model(base_model: str, adapter_path: str):
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    base = AutoModelForCausalLM.from_pretrained(
        base_model, torch_dtype=torch.bfloat16, trust_remote_code=True,
    )
    model = PeftModel.from_pretrained(base, adapter_path)
    model.to("cuda")
    model.eval()
    return model, tokenizer


def generate(model, tokenizer, state: str, n: int = 10, temperature: float = 0.8,
             max_new_tokens: int = 128) -> list[str]:
    import torch

    prompt = f"[STATE]\n{state}\n[TACTIC]\n"
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    prompt_len = inputs["input_ids"].shape[1]

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            num_return_sequences=n,
            do_sample=True,
            temperature=temperature,
            top_p=0.95,
            pad_token_id=tokenizer.eos_token_id,
        )

    tactics = []
    for seq in outputs:
        text = tokenizer.decode(seq[prompt_len:], skip_special_tokens=True)
        tactic = text.split("\n")[0].strip()
        if tactic:
            tactics.append(tactic)
    return tactics


def classify(tactic: str) -> str:
    """Coarse classification of tactic type."""
    if "let rec" in tactic:
        return "let_rec"
    if "match" in tactic:
        if "[] =>" in tactic and "::" in tactic:
            return "match_list"
        return "match_other"
    if tactic.count("foldl") + tactic.count("foldr") > 0:
        return "fold"
    if "List.rec" in tactic or "Nat.rec" in tactic:
        return "rec_combinator"
    if "have " in tactic:
        return "multi_step_have"
    if re.search(r'\.\w+\s*\(', tactic):
        return "method_chain"
    return "other"


def has_comment(tactic: str) -> bool:
    return " -- " in tactic or "//" in tactic


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-Coder-1.5B")
    parser.add_argument("--n", type=int, default=8, help="candidates per task")
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--tasks", default=None,
                        help="comma-separated task names (default: all diag + selected hard)")
    args = parser.parse_args()

    print(f"Loading {args.adapter}...")
    model, tokenizer = load_model(args.base_model, args.adapter)

    if args.tasks:
        tasks = [get_task(t) for t in args.tasks.split(",")]
    else:
        # All diag + hard-tier tasks that showed novel behavior
        diag = get_tasks("diag_all")
        hard_names = ["flatten", "zip_sum", "partition_positive",
                      "dedup_count", "filter_map_abs"]
        hard = [get_task(n) for n in hard_names]
        tasks = list(diag) + hard

    print(f"Inspecting {len(tasks)} tasks with {args.n} samples each")
    print("=" * 80)

    all_kinds: dict[str, int] = {}
    comment_count = 0
    total = 0

    for task in tasks:
        state = build_goal_state(task)
        print(f"\n[{task.name}] ({task.category})")
        print(f"  STATE:")
        for line in state.split("\n"):
            print(f"    {line}")

        candidates = generate(model, tokenizer, state, n=args.n,
                              temperature=args.temperature)
        unique = list(dict.fromkeys(candidates))

        # Show all unique outputs
        for i, tactic in enumerate(unique):
            kind = classify(tactic)
            all_kinds[kind] = all_kinds.get(kind, 0) + 1
            total += 1
            if has_comment(tactic):
                comment_count += 1
            marker = " [has_comment]" if has_comment(tactic) else ""
            # Truncate for readability
            shown = tactic[:140] + ("..." if len(tactic) > 140 else "")
            print(f"  #{i} [{kind}]{marker}: {shown}")

    print("\n" + "=" * 80)
    print("GENERATION SUMMARY")
    print("=" * 80)
    print(f"Total outputs: {total}")
    print(f"With trailing comments: {comment_count} ({comment_count/total*100:.1f}%)")
    print(f"\nKind distribution:")
    for kind, count in sorted(all_kinds.items(), key=lambda x: -x[1]):
        print(f"  {kind}: {count} ({count/total*100:.1f}%)")


if __name__ == "__main__":
    main()
