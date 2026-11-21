"""rejection_sample.py — multi-round rejection-sampling fine-tuning (Sprint 1).

Algorithm (per round):
  1. Evaluate baseline pass@k (type-constrained, temperature > 0)
  2. Collect winning token sequences per task
  3. Fine-tune the model on winners mixed with original corpus
  4. Re-evaluate pass@k with updated model
  5. Repeat for --rounds rounds, reporting pass@k after each

Usage:
  python sprint/rl/rejection_sample.py
  python sprint/rl/rejection_sample.py --k 100 --rounds 3 --epochs 100
  python sprint/rl/rejection_sample.py --temperature 1.2 --no-corpus-mix
"""
import argparse
import copy
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
from tokenizers import Tokenizer

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "typecheck"))
sys.path.insert(0, str(ROOT / "benchmark"))

from train import CausalTransformer, PAD_ID        # type: ignore
from generate import generate_batch                # type: ignore
from constrained_decode import generate_constrained, generate_constrained_batch, clear_mask_cache  # type: ignore
from pretty_print import tokens_to_haskell         # type: ignore
from run import build_haskell_source, run_haskell, build_corpus_registry, build_deps_registry, extract_candidate_tokens  # type: ignore
from checker import ParseError                     # type: ignore
from evaluate import pass_at_k                     # type: ignore


# ---------------------------------------------------------------------------
# In-memory sequence dataset (for fine-tuning)
# ---------------------------------------------------------------------------
class SeqDataset(Dataset):
    def __init__(self, token_lists: list[list[str]], tok: Tokenizer, max_len: int = 256):
        self.ids: list[list[int]] = []
        self.max_len = max_len
        for toks in token_lists:
            enc = tok.encode(" ".join(toks))
            ids = enc.ids[:max_len]
            self.ids.append(ids)

    def __len__(self) -> int:
        return len(self.ids)

    def __getitem__(self, idx):
        ids    = self.ids[idx]
        T      = len(ids)
        padded = ids + [PAD_ID] * (self.max_len - T)
        t      = torch.tensor(padded, dtype=torch.long)
        x, y   = t[:-1], t[1:]
        mask   = (y != PAD_ID).float()
        return x, y, mask


# ---------------------------------------------------------------------------
# Winner collection
# ---------------------------------------------------------------------------
def _generate_candidates(
    task: dict,
    model,
    tok: Tokenizer,
    k: int,
    temperature: float,
    constrained: bool,
    registry: dict[str, str] | None = None,
    deps_registry: dict[str, list[dict]] | None = None,
    vocab: dict[str, int] | None = None,
    id_to_tok: dict[int, str] | None = None,
) -> list[tuple[list[str], str]]:
    """GPU-only: generate k candidates for one task. Returns (tokens, source) pairs.
    No Haskell evaluation — caller handles that.
    Constrained: serial (per-sequence grammar masking).
    Unconstrained: batch (k sequences in one forward pass).
    """
    if vocab is None:
        vocab = tok.get_vocab()
    if id_to_tok is None:
        id_to_tok = {v: kk for kk, v in vocab.items()}

    enc        = tok.encode(task["prompt"])
    prompt_ids = enc.ids[:-1]

    if constrained:
        all_gen_ids = generate_constrained_batch(
            model, prompt_ids, id_to_tok, k=k,
            max_new_tokens=200, temperature=max(temperature, 0.1),
        )
    else:
        all_gen_ids = generate_batch(
            model, prompt_ids, k=k,
            max_new_tokens=200, temperature=temperature,
        )

    candidates: list[tuple[list[str], str]] = []
    for gen_ids in all_gen_ids:
        tokens = extract_candidate_tokens(prompt_ids, gen_ids, tok, task["name"], vocab)
        try:
            _, func_def = tokens_to_haskell(tokens)
        except (ParseError, Exception):
            continue
        source = build_haskell_source(func_def, task, registry=registry,
                                      deps_registry=deps_registry)
        candidates.append((tokens, source))
    return candidates


def collect_winners(
    task: dict,
    model,
    tok: Tokenizer,
    device: torch.device,
    k: int,
    temperature: float,
    constrained: bool,
    registry: dict[str, str] | None = None,
    deps_registry: dict[str, list[dict]] | None = None,
    max_workers: int = 4,
) -> tuple[list[list[str]], int]:
    """Generate k candidates and evaluate them. Returns (winner_token_lists, n_passed).
    Thin wrapper around _generate_candidates + parallel runhaskell.
    """
    vocab     = tok.get_vocab()
    id_to_tok = {v: kk for kk, v in vocab.items()}
    candidates = _generate_candidates(
        task, model, tok, k, temperature, constrained,
        registry, deps_registry, vocab, id_to_tok,
    )
    if not candidates:
        return [], 0

    winners: list[list[str]] = []
    passed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(run_haskell, src): toks
                   for toks, src in candidates}
        for fut in concurrent.futures.as_completed(futures):
            toks = futures[fut]
            ok, _ = fut.result()
            if ok:
                passed += 1
                winners.append(toks)
    return winners, passed


# ---------------------------------------------------------------------------
# Fine-tuning
# ---------------------------------------------------------------------------
def finetune(
    model,
    winner_seqs: list[list[str]],
    corpus_seqs: list[list[str]],
    tok: Tokenizer,
    device: torch.device,
    epochs: int,
    lr: float,
    batch_size: int,
    mix_corpus: bool,
) -> float:
    """Fine-tune model in-place on winners (+ optional corpus mix). Returns best loss."""
    training_seqs = list(winner_seqs)
    if mix_corpus:
        training_seqs = training_seqs + corpus_seqs
    if not training_seqs:
        print("[rl] no training sequences — skipping fine-tune")
        return float("nan")

    use_amp = device.type == "cuda"
    dataset = SeqDataset(training_seqs, tok)
    # num_workers=0: winner datasets are tiny (~40 seqs), worker spinup overhead exceeds benefit
    loader  = DataLoader(
        dataset, batch_size=batch_size, shuffle=True,
        num_workers=0, pin_memory=use_amp,
    )

    criterion = nn.CrossEntropyLoss(ignore_index=PAD_ID, reduction="none")
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-2)
    scaler    = torch.amp.GradScaler("cuda", enabled=use_amp)

    model.train()
    best_loss = float("inf")
    t0 = time.time()

    for epoch in range(1, epochs + 1):
        epoch_loss   = 0.0
        epoch_tokens = 0

        for x, y, mask in loader:
            x, y, mask = x.to(device, non_blocking=use_amp), y.to(device, non_blocking=use_amp), mask.to(device, non_blocking=use_amp)
            pad_mask   = (x == PAD_ID)
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=use_amp):
                logits     = model(x, pad_mask=pad_mask)
                B, T, V    = logits.shape
                loss_per   = criterion(logits.reshape(B * T, V), y.reshape(B * T))
                n_tokens   = mask.sum()
                loss       = (loss_per.reshape(B, T) * mask).sum() / n_tokens

            optimizer.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(optimizer)
            scaler.update()

            epoch_loss   += loss.item() * n_tokens.item()
            epoch_tokens += n_tokens.item()

        avg = epoch_loss / epoch_tokens
        if avg < best_loss:
            best_loss = avg
        if epoch % max(1, epochs // 5) == 0 or epoch == epochs:
            print(f"  [ft] epoch {epoch:4d}/{epochs}  loss={avg:.4f}  "
                  f"elapsed={time.time()-t0:.0f}s")

    model.eval()
    return best_loss


# ---------------------------------------------------------------------------
# One RL round
# ---------------------------------------------------------------------------
def run_round(
    round_num: int,
    model,
    tok: Tokenizer,
    device: torch.device,
    all_tasks: list[dict],
    corpus_seqs: list[list[str]],
    args,
    registry: dict[str, str] | None = None,
    deps_registry: dict[str, list[dict]] | None = None,
) -> dict[str, float]:
    """Run one round of collect → finetune. Returns task_name -> pass@k dict."""
    constrained = not args.no_constrain

    # Evict stale mask cache entries from previous round — the model has changed
    # so cached valid-id sets are still valid (grammar is model-independent).
    # But memory can grow large; evict between rounds as a precaution.
    clear_mask_cache()

    print(f"\n{'='*60}")
    print(f"Round {round_num}: Winner collection (k={args.k} per task)")
    print(f"{'='*60}")

    vocab     = tok.get_vocab()
    id_to_tok = {v: kk for kk, v in vocab.items()}

    # Phase 1: GPU generation for all tasks (serial — GPU is not reentrant)
    model.eval()
    task_candidates: dict[str, list[tuple[list[str], str]]] = {}
    for task in all_tasks:
        task_candidates[task["name"]] = _generate_candidates(
            task, model, tok, args.k, args.temperature, constrained,
            registry, deps_registry, vocab, id_to_tok,
        )

    # Phase 2: evaluate ALL candidates across ALL tasks in one pool
    winners_by_task: dict[str, list[list[str]]] = {t["name"]: [] for t in all_tasks}
    passed_by_task:  dict[str, int]             = {t["name"]: 0  for t in all_tasks}
    all_sources = [
        (task_name, toks, src)
        for task_name, cands in task_candidates.items()
        for toks, src in cands
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futures = {pool.submit(run_haskell, src): (task_name, toks)
                   for task_name, toks, src in all_sources}
        for fut in concurrent.futures.as_completed(futures):
            task_name, toks = futures[fut]
            ok, _ = fut.result()
            if ok:
                passed_by_task[task_name] += 1
                winners_by_task[task_name].append(toks)

    all_winners: list[list[str]] = []
    round_passed: dict[str, int] = {}
    for task in all_tasks:
        name    = task["name"]
        n_pass  = passed_by_task[name]
        # Deduplicate and cap per-task
        seen, unique = set(), []
        for w in winners_by_task[name]:
            key = tuple(w)
            if key not in seen:
                seen.add(key)
                unique.append(w)
        unique = unique[:args.max_per_task]
        all_winners.extend(unique)
        round_passed[name] = n_pass
        novel_tag = "[N]" if task.get("novel") else "   "
        print(f"  {novel_tag} {name:<22} {n_pass}/{args.k} pass  "
              f"→ {len(unique)} winners")

    total_winners = len(all_winners)
    print(f"\n[rl] round {round_num}: total winners = {total_winners}")

    if total_winners == 0 and args.no_corpus_mix:
        print("[rl] no winners and corpus mix disabled — skipping fine-tune this round")
        return _eval_all(model, tok, device, all_tasks, args,
                         registry=registry, deps_registry=deps_registry)

    label = "corpus-mix bootstrap" if total_winners == 0 else f"{total_winners} winners"
    print(f"\nRound {round_num}: Fine-tuning ({args.epochs} epochs, {label})")
    ft_loss = finetune(
        model, all_winners, corpus_seqs, tok, device,
        epochs=args.epochs, lr=args.lr, batch_size=args.batch_size,
        mix_corpus=not args.no_corpus_mix,
    )
    print(f"[rl] round {round_num} fine-tune complete. best loss={ft_loss:.4f}")

    return _eval_all(model, tok, device, all_tasks, args,
                     registry=registry, deps_registry=deps_registry)


def _eval_all(
    model,
    tok: Tokenizer,
    device: torch.device,
    all_tasks: list[dict],
    args,
    registry: dict[str, str] | None = None,
    deps_registry: dict[str, list[dict]] | None = None,
) -> dict[str, float]:
    """Evaluate pass@eval_k for all tasks. Returns task_name -> pass@k.
    Phase 1: generate all candidates for all tasks on GPU (serial).
    Phase 2: evaluate everything in one ProcessPoolExecutor.
    """
    constrained = not args.no_constrain
    vocab     = tok.get_vocab()
    id_to_tok = {v: kk for kk, v in vocab.items()}

    model.eval()

    # Phase 1: GPU generation for all tasks
    task_candidates: dict[str, list[tuple[list[str], str]]] = {}
    for task in all_tasks:
        task_candidates[task["name"]] = _generate_candidates(
            task, model, tok, args.eval_k, args.temperature, constrained,
            registry, deps_registry, vocab, id_to_tok,
        )

    # Phase 2: evaluate ALL candidates in one pool
    passed_by_task: dict[str, int] = {t["name"]: 0 for t in all_tasks}
    all_sources = [
        (task_name, toks, src)
        for task_name, cands in task_candidates.items()
        for toks, src in cands
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futures = {pool.submit(run_haskell, src): task_name
                   for task_name, toks, src in all_sources}
        for fut in concurrent.futures.as_completed(futures):
            task_name = futures[fut]
            ok, _ = fut.result()
            if ok:
                passed_by_task[task_name] += 1

    return {
        t["name"]: pass_at_k(args.eval_k, passed_by_task[t["name"]], args.eval_k)
        for t in all_tasks
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    p = argparse.ArgumentParser(description="Multi-round rejection-sampling fine-tuning")
    p.add_argument("--checkpoint",     default="sprint/model/checkpoint.pt")
    p.add_argument("--checkpoint-out", default="sprint/model/checkpoint_rl.pt")
    p.add_argument("--tokenizer",      default="sprint/corpus/tokenizer.json")
    p.add_argument("--tasks",          default="sprint/benchmark/tasks.json")
    p.add_argument("--sequences",      default="sprint/corpus/sequences_aug.json",
                   help="Augmented corpus sequences for mixing + few-shot")
    p.add_argument("--k",              type=int,   default=50,
                   help="Candidates per task for winner collection")
    p.add_argument("--eval-k",         type=int,   default=50,
                   help="Candidates per task for pass@k evaluation")
    p.add_argument("--rounds",         type=int,   default=3,
                   help="Number of RL rounds")
    p.add_argument("--temperature",    type=float, default=0.8)
    p.add_argument("--no-constrain",   action="store_true")
    p.add_argument("--epochs",         type=int,   default=100)
    p.add_argument("--lr",             type=float, default=1e-4)
    p.add_argument("--batch-size",     type=int,   default=16)
    p.add_argument("--no-corpus-mix",  action="store_true")
    p.add_argument("--max-per-task",   type=int,   default=10,
                   help="Cap winners per task to balance fine-tune dataset")
    p.add_argument("--max-workers",   type=int,   default=min(os.cpu_count() or 4, 16),
                   help="ThreadPoolExecutor workers for parallel runhaskell")
    args = p.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Load model
    tok  = Tokenizer.from_file(args.tokenizer)
    ckpt = torch.load(args.checkpoint, map_location=device, weights_only=False)
    cfg  = ckpt["args"]
    model = CausalTransformer(
        vocab_size=ckpt["vocab_size"],
        d_model=cfg["d_model"], nhead=cfg["nhead"],
        num_layers=cfg["num_layers"], max_len=cfg["max_len"], dropout=0.1,
    ).to(device)
    model.load_state_dict(ckpt["model_state"])

    train_loss = ckpt.get("train_loss", ckpt.get("loss", float("nan")))
    val_loss   = ckpt.get("val_loss",   float("nan"))
    print(f"[rl] checkpoint: epoch={ckpt['epoch']}, "
          f"train_loss={train_loss:.4f}, val_loss={val_loss:.4f}")

    all_tasks   = json.loads(Path(args.tasks).read_text())
    corpus_raw  = json.loads(Path(args.sequences).read_text())
    corpus_seqs = [s["tokens"] for s in corpus_raw]

    registry = build_corpus_registry(args.sequences)
    deps_registry = build_deps_registry(args.sequences)
    print(f"[rl] corpus registry: {len(registry)} function definitions")

    constrained = not args.no_constrain
    print(f"[rl] {len(all_tasks)} tasks  rounds={args.rounds}  k={args.k}  "
          f"eval_k={args.eval_k}  temp={args.temperature}  "
          f"constrained={constrained}")
    print(f"[rl] fine-tune: epochs={args.epochs}  lr={args.lr}  "
          f"corpus_mix={not args.no_corpus_mix}  "
          f"max_workers={args.max_workers}")

    # ── Baseline ────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("Baseline evaluation (before RL)")
    print(f"{'='*60}")
    vocab     = tok.get_vocab()
    id_to_tok = {v: kk for kk, v in vocab.items()}
    model.eval()

    # Phase 1: generate for all tasks on GPU
    base_candidates: dict[str, list[tuple[list[str], str]]] = {}
    for task in all_tasks:
        base_candidates[task["name"]] = _generate_candidates(
            task, model, tok, args.eval_k, args.temperature, constrained,
            registry, deps_registry, vocab, id_to_tok,
        )

    # Phase 2: evaluate all in one pool
    passed_base: dict[str, int] = {t["name"]: 0 for t in all_tasks}
    base_sources = [
        (task_name, src)
        for task_name, cands in base_candidates.items()
        for toks, src in cands
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futures = {pool.submit(run_haskell, src): task_name
                   for task_name, src in base_sources}
        for fut in concurrent.futures.as_completed(futures):
            task_name = futures[fut]
            ok, _ = fut.result()
            if ok:
                passed_base[task_name] += 1

    baseline = {}
    for task in all_tasks:
        name   = task["name"]
        passed = passed_base[name]
        p1     = pass_at_k(args.eval_k, passed, 1)
        pk     = pass_at_k(args.eval_k, passed, args.eval_k)
        baseline[name] = pk
        novel_tag = "[N]" if task.get("novel") else "   "
        print(f"  {novel_tag} {name:<22} "
              f"pass@1={p1:.3f}  pass@{args.eval_k}={pk:.3f}  "
              f"({passed}/{args.eval_k})")

    # ── Multi-round RL ───────────────────────────────────────────────────────
    history: list[dict[str, float]] = [baseline]

    for round_num in range(1, args.rounds + 1):
        results = run_round(
            round_num, model, tok, device, all_tasks, corpus_seqs, args,
            registry=registry, deps_registry=deps_registry,
        )
        history.append(results)

        # Print pass@k after this round
        print(f"\nRound {round_num} results:")
        for task in all_tasks:
            name = task["name"]
            before = history[round_num - 1].get(name, 0.0)
            after  = results.get(name, 0.0)
            delta  = after - before
            novel_tag = "[N]" if task.get("novel") else "   "
            print(f"  {novel_tag} {name:<22} "
                  f"pass@{args.eval_k}: {before:.3f} → {after:.3f}  "
                  f"({delta:+.3f})")

    # ── Summary across rounds ────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"{'Round':<8}", end="")
    for task in all_tasks:
        print(f"  {task['name'][:10]:<10}", end="")
    print()
    for ri, hist in enumerate(history):
        label = "Base  " if ri == 0 else f"Rnd {ri:<3}"
        print(f"{label}", end="")
        for task in all_tasks:
            v = hist.get(task["name"], 0.0)
            print(f"  {v:10.3f}", end="")
        print()

    # Mean improvement
    orig_names  = [t["name"] for t in all_tasks if not t.get("novel")]
    novel_names = [t["name"] for t in all_tasks if t.get("novel")]

    def mean_pass(hist: dict, names: list[str]) -> float:
        vals = [hist.get(n, 0.0) for n in names]
        return sum(vals) / len(vals) if vals else 0.0

    print(f"\n{'='*60}")
    print(f"{'':8}  {'orig mean':>10}  {'novel mean':>10}  {'all mean':>10}")
    for ri, hist in enumerate(history):
        label = "Base" if ri == 0 else f"Rnd {ri}"
        print(f"{label:<8}  {mean_pass(hist, orig_names):>10.3f}  "
              f"{mean_pass(hist, novel_names):>10.3f}  "
              f"{mean_pass(hist, [t['name'] for t in all_tasks]):>10.3f}")

    # ── Save final checkpoint ─────────────────────────────────────────────
    out = args.checkpoint_out
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "epoch":       ckpt["epoch"],
        "model_state": model.state_dict(),
        "args":        cfg,
        "vocab_size":  ckpt["vocab_size"],
        "train_loss":  train_loss,
        "val_loss":    val_loss,
        "rl_rounds":   args.rounds,
        "rl_history":  history,
    }, out)
    print(f"\n[rl] saved → {out}")


if __name__ == "__main__":
    main()
