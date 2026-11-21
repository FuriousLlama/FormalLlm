"""evaluate.py — pass@k measurement for Sprint 2.

Runs k candidates per task and reports:
  pass@1  — average fraction of candidates that pass (expected pass rate)
  pass@k  — fraction of tasks where at least 1 of k candidates passes

Usage:
  python sprint/rl/evaluate.py                          # all tasks, k=50
  python sprint/rl/evaluate.py --checkpoint rl.pt --k 100
  python sprint/rl/evaluate.py --task fId --k 20 --verbose
"""
import argparse
import concurrent.futures
import json
import math
import sys
from pathlib import Path

import torch
from tokenizers import Tokenizer

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "typecheck"))
sys.path.insert(0, str(ROOT / "benchmark"))

from train import CausalTransformer               # type: ignore
from generate import generate                      # type: ignore
from constrained_decode import generate_constrained  # type: ignore
from pretty_print import tokens_to_haskell         # type: ignore
from run import build_haskell_source, run_haskell, build_corpus_registry, build_deps_registry, extract_candidate_tokens  # type: ignore
from checker import ParseError                     # type: ignore


# ---------------------------------------------------------------------------
# pass@k estimator (unbiased, Chen et al. 2021)
# ---------------------------------------------------------------------------
def pass_at_k(n: int, c: int, k: int) -> float:
    """Estimate pass@k from n total samples, c correct.

    Uses the unbiased estimator: 1 - prod_{i=0}^{k-1} (n-c-i)/(n-i).
    Falls back to 0 if c==0 or n<k.
    """
    if c == 0:
        return 0.0
    if n < k:
        # fewer samples than k — use empirical rate
        return c / n
    # Numerically stable product
    result = 1.0
    for i in range(k):
        result *= (n - c - i) / (n - i)
    return 1.0 - result


# ---------------------------------------------------------------------------
# Single-task evaluation
# ---------------------------------------------------------------------------
def evaluate_task(
    task: dict,
    model,
    tok: Tokenizer,
    device: torch.device,
    k: int,
    temperature: float,
    constrained: bool,
    verbose: bool = False,
    registry: dict[str, str] | None = None,
    deps_registry: dict[str, list[dict]] | None = None,
    max_workers: int = 4,
) -> dict:
    """Generate k candidates for task, return evaluation summary.

    Phase 1: generate all candidates on GPU (serial).
    Phase 2: evaluate all parseable candidates via parallel runhaskell (CPU).
    """
    vocab     = tok.get_vocab()
    id_to_tok = {v: kk for kk, v in vocab.items()}

    prompt_str = task["prompt"]
    func_name  = task["name"]
    enc        = tok.encode(prompt_str)
    prompt_ids = enc.ids[:-1]

    # Phase 1: generate all k candidates on GPU (serial — GPU is not reentrant)
    candidates: list[tuple[int, str, str]] = []   # (orig_idx, func_def, source)
    parse_errors = 0

    for i in range(k):
        if constrained:
            gen_ids = generate_constrained(
                model, prompt_ids, id_to_tok,
                max_new_tokens=200, temperature=max(temperature, 0.1),
            )
        else:
            gen_ids = generate(
                model, prompt_ids,
                max_new_tokens=200, temperature=temperature,
            )

        tokens = extract_candidate_tokens(prompt_ids, gen_ids, tok, func_name, vocab)

        try:
            _, func_def = tokens_to_haskell(tokens)
        except (ParseError, Exception):
            parse_errors += 1
            continue

        source = build_haskell_source(func_def, task, registry=registry,
                                      deps_registry=deps_registry)
        candidates.append((i, func_def, source))

    # Phase 2: evaluate all parseable candidates in parallel (CPU)
    passed = 0
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(run_haskell, src): (idx, func_def)
                   for idx, func_def, src in candidates}
        for fut in concurrent.futures.as_completed(futures):
            idx, func_def = futures[fut]
            ok, output = fut.result()
            if ok:
                passed += 1
                if verbose:
                    print(f"  [{idx:3d}] PASS  {func_def}")
            elif verbose and idx < 10:
                print(f"  [{idx:3d}] FAIL  {func_def}  → {output[:50]}")

    return {
        "task":     task["name"],
        "prompt":   prompt_str,
        "k":        k,
        "passed":   passed,
        "pass@1":   pass_at_k(k, passed, 1),
        "pass@k":   pass_at_k(k, passed, k),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def load_model(checkpoint: str, tokenizer: str, device: torch.device):
    tok  = Tokenizer.from_file(tokenizer)
    ckpt = torch.load(checkpoint, map_location=device, weights_only=False)
    cfg  = ckpt["args"]
    m    = CausalTransformer(
        vocab_size=ckpt["vocab_size"],
        d_model=cfg["d_model"], nhead=cfg["nhead"],
        num_layers=cfg["num_layers"], max_len=cfg["max_len"], dropout=0.0,
    ).to(device)
    m.load_state_dict(ckpt["model_state"])
    m.eval()
    return m, tok, ckpt


def main() -> None:
    p = argparse.ArgumentParser(description="pass@k evaluation")
    p.add_argument("--checkpoint",   default="sprint/model/checkpoint.pt")
    p.add_argument("--tokenizer",    default="sprint/corpus/tokenizer.json")
    p.add_argument("--tasks",        default="sprint/benchmark/tasks.json")
    p.add_argument("--sequences",    default="sprint/corpus/sequences_aug.json",
                   help="Augmented sequences for corpus registry")
    p.add_argument("--task",         default=None, help="Single task func_name")
    p.add_argument("--k",            type=int,   default=50)
    p.add_argument("--temperature",  type=float, default=0.8)
    p.add_argument("--no-constrain", action="store_true")
    p.add_argument("--max-workers",  type=int, default=4)
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model, tok, ckpt = load_model(args.checkpoint, args.tokenizer, device)
    loss_val = ckpt.get("val_loss", ckpt.get("loss", float("nan")))
    print(f"[evaluate] checkpoint: epoch={ckpt['epoch']}, "
          f"loss={loss_val:.4f}, device={device}")

    all_tasks = json.loads(Path(args.tasks).read_text())
    if args.task:
        all_tasks = [t for t in all_tasks if t["func_name"] == args.task]

    registry = build_corpus_registry(args.sequences)
    deps_registry = build_deps_registry(args.sequences)
    print(f"[evaluate] corpus registry: {len(registry)} function definitions")

    constrained = not args.no_constrain
    print(f"[evaluate] {len(all_tasks)} task(s), k={args.k}, "
          f"temp={args.temperature}, constrained={constrained}\n")

    results = []
    for task in all_tasks:
        print(f"  {task['name']:<20}", end="", flush=True)
        r = evaluate_task(task, model, tok, device,
                          k=args.k, temperature=args.temperature,
                          constrained=constrained, verbose=args.verbose,
                          registry=registry, deps_registry=deps_registry,
                          max_workers=args.max_workers)
        results.append(r)
        print(f"pass@1={r['pass@1']:.3f}  pass@{args.k}={r['pass@k']:.3f}  "
              f"({r['passed']}/{args.k})")

    # Summary
    mean_pass1 = sum(r["pass@1"] for r in results) / len(results)
    mean_passk = sum(r["pass@k"] for r in results) / len(results)
    tasks_any  = sum(1 for r in results if r["passed"] > 0)
    print(f"\n{'='*50}")
    print(f"mean pass@1  : {mean_pass1:.3f}")
    print(f"mean pass@{args.k:<3}: {mean_passk:.3f}")
    print(f"tasks ≥1 pass: {tasks_any}/{len(results)}")

    return results


if __name__ == "__main__":
    main()
