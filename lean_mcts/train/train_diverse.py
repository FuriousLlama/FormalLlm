"""Sprint 28: Train LoRA adapters on diverse real Lean library code.

Orchestrates three training configurations:
  A: Diverse-only (no template data)
  B: Loss-weighted mix (diverse 10x weight + template)
  C: Curriculum + mix (epoch 1 diverse-only, epochs 2-3 weighted mix)

Tracks validation loss on diverse and template splits independently
to detect capacity competition.

Usage:
    python -m lean_mcts.train.train_diverse --config A
    python -m lean_mcts.train.train_diverse --config B
    python -m lean_mcts.train.train_diverse --config C
    python -m lean_mcts.train.train_diverse --config all
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import time
from pathlib import Path

import torch
from peft import LoraConfig, TaskType, get_peft_model
from torch.nn import CrossEntropyLoss
from torch.utils.data import DataLoader

from transformers import AutoModelForCausalLM, AutoTokenizer

try:
    from lean_mcts.train.dataset import WeightedTacticDataset, collate_fn
except ImportError:
    from dataset import WeightedTacticDataset, collate_fn


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------

def prepare_config_a(diverse_train: str, diverse_val: str, template_val: str,
                     tokenizer, max_length: int) -> dict:
    """Config A: Diverse-only training."""
    train_ds = WeightedTacticDataset(diverse_train, tokenizer, max_length)
    diverse_val_ds = WeightedTacticDataset(diverse_val, tokenizer, max_length)
    template_val_ds = WeightedTacticDataset(template_val, tokenizer, max_length)
    return {
        "name": "A_diverse_only",
        "train_ds": train_ds,
        "diverse_val_ds": diverse_val_ds,
        "template_val_ds": template_val_ds,
        "curriculum": None,
    }


def prepare_config_b(diverse_train: str, diverse_val: str,
                     template_train: str, template_val: str,
                     tokenizer, max_length: int,
                     diverse_weight: float = 10.0) -> dict:
    """Config B: Loss-weighted mix (diverse examples weighted 10x)."""
    merged = _merge_with_weights(
        diverse_train, template_train, diverse_weight=diverse_weight
    )
    train_ds = WeightedTacticDataset(merged, tokenizer, max_length)
    diverse_val_ds = WeightedTacticDataset(diverse_val, tokenizer, max_length)
    template_val_ds = WeightedTacticDataset(template_val, tokenizer, max_length)
    return {
        "name": "B_loss_weighted",
        "train_ds": train_ds,
        "diverse_val_ds": diverse_val_ds,
        "template_val_ds": template_val_ds,
        "curriculum": None,
    }


def prepare_config_c(diverse_train: str, diverse_val: str,
                     template_train: str, template_val: str,
                     tokenizer, max_length: int,
                     diverse_weight: float = 10.0) -> dict:
    """Config C: Curriculum (epoch 1 diverse-only, epochs 2+ weighted mix)."""
    diverse_ds = WeightedTacticDataset(diverse_train, tokenizer, max_length)
    merged = _merge_with_weights(
        diverse_train, template_train, diverse_weight=diverse_weight
    )
    mixed_ds = WeightedTacticDataset(merged, tokenizer, max_length)
    diverse_val_ds = WeightedTacticDataset(diverse_val, tokenizer, max_length)
    template_val_ds = WeightedTacticDataset(template_val, tokenizer, max_length)
    return {
        "name": "C_curriculum",
        "train_ds": diverse_ds,  # epoch 1
        "diverse_val_ds": diverse_val_ds,
        "template_val_ds": template_val_ds,
        "curriculum": {
            "epoch_1_ds": diverse_ds,
            "epoch_2plus_ds": mixed_ds,
        },
    }


def _merge_with_weights(diverse_path: str, template_path: str,
                        diverse_weight: float) -> str:
    """Merge diverse and template data into a temp JSONL with weight field."""
    merged_path = Path(diverse_path).parent / f"_merged_w{diverse_weight:.0f}.jsonl"

    with open(merged_path, "w") as out:
        with open(diverse_path) as f:
            for line in f:
                entry = json.loads(line)
                entry["weight"] = diverse_weight
                out.write(json.dumps(entry) + "\n")
        with open(template_path) as f:
            for line in f:
                entry = json.loads(line)
                entry["weight"] = 1.0
                out.write(json.dumps(entry) + "\n")

    return str(merged_path)


# ---------------------------------------------------------------------------
# Weighted loss computation
# ---------------------------------------------------------------------------

def compute_weighted_loss(model, input_ids, attention_mask, labels, weights):
    """Compute per-example weighted causal LM loss.

    Instead of using outputs.loss (batch-averaged), computes loss per example
    and applies weights before averaging.
    """
    outputs = model(input_ids=input_ids, attention_mask=attention_mask)
    logits = outputs.logits

    # Shift for causal LM: predict next token
    shift_logits = logits[..., :-1, :].contiguous()
    shift_labels = labels[..., 1:].contiguous()

    # Per-token loss (no reduction)
    loss_fn = CrossEntropyLoss(reduction="none")
    per_token = loss_fn(
        shift_logits.view(-1, shift_logits.size(-1)),
        shift_labels.view(-1),
    )
    per_token = per_token.view(shift_labels.size())

    # Per-example: average over non-masked tokens
    mask = (shift_labels != -100).float()
    per_example = (per_token * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)

    # Apply weights and average
    weighted = per_example * weights
    loss = weighted.sum() / weights.sum()

    # Also return unweighted mean for logging
    unweighted = per_example.mean()

    return loss, unweighted


# ---------------------------------------------------------------------------
# Evaluation with per-split tracking
# ---------------------------------------------------------------------------

def evaluate_split(model, loader, device, max_batches: int = 500) -> float:
    """Compute average loss on a validation split."""
    model.eval()
    total_loss = 0.0
    n = 0
    with torch.no_grad():
        for i, batch in enumerate(loader):
            if i >= max_batches:
                break
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)
            outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
            val = outputs.loss.item()
            if not math.isnan(val):
                total_loss += val
                n += 1
    model.train()
    return total_loss / max(n, 1)


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------

def train_config(config: dict, args) -> dict:
    """Train one configuration and return results."""
    name = config["name"]
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    output_dir = Path(args.output_base) / name

    print(f"\n{'='*60}")
    print(f"Training config: {name}")
    print(f"Output: {output_dir}")
    print(f"{'='*60}\n")

    # Seed
    if args.seed is not None:
        random.seed(args.seed)
        torch.manual_seed(args.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(args.seed)

    # Tokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Data loaders
    train_ds = config["train_ds"]
    print(f"Train examples: {len(train_ds)}")

    diverse_val_loader = DataLoader(
        config["diverse_val_ds"], batch_size=args.batch_size, collate_fn=collate_fn
    )
    template_val_loader = DataLoader(
        config["template_val_ds"], batch_size=args.batch_size, collate_fn=collate_fn
    )

    # Pre-create all train loaders we'll need, so we don't recreate inside
    # the epoch loop (which leaks shared-memory resources in the curriculum config).
    train_ds = config["train_ds"]
    main_train_loader = DataLoader(
        train_ds, batch_size=args.batch_size,
        shuffle=True, collate_fn=collate_fn, drop_last=True,
    )
    curriculum_epoch2plus_loader = None
    if config["curriculum"]:
        curriculum_epoch2plus_loader = DataLoader(
            config["curriculum"]["epoch_2plus_ds"],
            batch_size=args.batch_size,
            shuffle=True, collate_fn=collate_fn, drop_last=True,
        )

    # Model + LoRA
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model, torch_dtype=torch.bfloat16, trust_remote_code=True,
    )
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r, lora_alpha=args.lora_alpha,
        target_modules=["q_proj", "v_proj"],
        lora_dropout=0.05, bias="none",
    )
    model = get_peft_model(model, lora_config)
    model.to(device)
    model.print_trainable_parameters()

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    grad_accum = args.gradient_accumulation

    # Training
    best_diverse_val = float("inf")
    patience_counter = 0
    epoch_history = []
    start_time = time.time()

    for epoch in range(args.epochs):
        # Curriculum: switch loader for epoch 2+ (pre-created, no new allocation)
        if config["curriculum"] and epoch >= 1:
            train_loader = curriculum_epoch2plus_loader
            print(f"  [Curriculum] Epoch {epoch+1}: switched to mixed loader ({len(curriculum_epoch2plus_loader.dataset)} examples)")
        else:
            train_loader = main_train_loader

        model.train()
        epoch_loss_weighted = 0.0
        epoch_loss_unweighted = 0.0
        epoch_steps = 0

        for step, batch in enumerate(train_loader):
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)
            weights = batch["weight"].to(device)

            with torch.amp.autocast("cuda", dtype=torch.bfloat16):
                loss, unweighted = compute_weighted_loss(
                    model, input_ids, attention_mask, labels, weights
                )
                scaled_loss = loss / grad_accum

            scaled_loss.backward()

            if (step + 1) % grad_accum == 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                optimizer.zero_grad()
                epoch_loss_weighted += loss.item()
                epoch_loss_unweighted += unweighted.item()
                epoch_steps += 1

                if epoch_steps % args.log_every == 0:
                    avg_w = epoch_loss_weighted / epoch_steps
                    avg_u = epoch_loss_unweighted / epoch_steps
                    elapsed = time.time() - start_time
                    vram = torch.cuda.memory_allocated() / 1e9
                    print(f"  step {epoch_steps:4d} | loss_w {avg_w:.4f} | loss_u {avg_u:.4f} | {elapsed:.0f}s | {vram:.1f}G")

        avg_train_w = epoch_loss_weighted / max(epoch_steps, 1)
        avg_train_u = epoch_loss_unweighted / max(epoch_steps, 1)

        # Save adapter
        output_dir.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(output_dir)
        tokenizer.save_pretrained(output_dir)

        # Per-split validation
        diverse_val = evaluate_split(model, diverse_val_loader, device)
        template_val = evaluate_split(model, template_val_loader, device)

        record = {
            "epoch": epoch + 1,
            "train_loss_weighted": round(avg_train_w, 4),
            "train_loss_unweighted": round(avg_train_u, 4),
            "diverse_val_loss": round(diverse_val, 4),
            "template_val_loss": round(template_val, 4),
            "dataset_size": len(train_loader.dataset),
        }
        epoch_history.append(record)

        # Capacity competition detection
        competition = ""
        if len(epoch_history) >= 2:
            prev = epoch_history[-2]
            if diverse_val > prev["diverse_val_loss"] and template_val < prev["template_val_loss"]:
                competition = " ⚠ CAPACITY COMPETITION (diverse↑ template↓)"

        print(f"  Epoch {epoch+1}/{args.epochs} | train_w {avg_train_w:.4f} | "
              f"diverse_val {diverse_val:.4f} | template_val {template_val:.4f}{competition}")

        # Early stopping on diverse val loss
        if diverse_val < best_diverse_val:
            best_diverse_val = diverse_val
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= args.patience:
                print(f"  Early stopping (diverse val no improvement for {args.patience} epochs)")
                break

    elapsed = time.time() - start_time
    print(f"\n  Done: {elapsed:.0f}s ({elapsed/60:.1f} min)")

    # Save stats
    stats = {
        "config": name,
        "base_model": args.base_model,
        "lora_r": args.lora_r,
        "lora_alpha": args.lora_alpha,
        "lr": args.lr,
        "batch_size": args.batch_size,
        "gradient_accumulation": grad_accum,
        "epochs_completed": len(epoch_history),
        "best_diverse_val_loss": round(best_diverse_val, 4),
        "epoch_history": epoch_history,
        "elapsed_minutes": round(elapsed / 60, 1),
        "seed": args.seed,
    }
    with open(output_dir / "training_stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sprint 28: Train on diverse Lean library code")
    parser.add_argument("--config", required=True, choices=["A", "B", "C", "all"],
                        help="Training configuration")
    parser.add_argument("--diverse-train", default="lean_mcts/train/diverse_train.jsonl")
    parser.add_argument("--diverse-val", default="lean_mcts/train/diverse_val.jsonl")
    parser.add_argument("--template-train", default="lean_mcts/train/goal_cond_train.jsonl")
    parser.add_argument("--template-val", default="lean_mcts/train/goal_cond_val.jsonl")
    parser.add_argument("--output-base", default="lean_mcts/train/diverse_adapter")
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-Coder-1.5B")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--gradient-accumulation", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--patience", type=int, default=2)
    parser.add_argument("--log-every", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--diverse-weight", type=float, default=10.0,
                        help="Loss weight for diverse examples in Config B/C")
    args = parser.parse_args()

    # Load tokenizer once for dataset construction
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    configs_to_run = ["A", "B", "C"] if args.config == "all" else [args.config]
    all_results = {}

    for cfg in configs_to_run:
        if cfg == "A":
            config = prepare_config_a(
                args.diverse_train, args.diverse_val, args.template_val,
                tokenizer, args.max_length,
            )
        elif cfg == "B":
            config = prepare_config_b(
                args.diverse_train, args.diverse_val,
                args.template_train, args.template_val,
                tokenizer, args.max_length, args.diverse_weight,
            )
        elif cfg == "C":
            config = prepare_config_c(
                args.diverse_train, args.diverse_val,
                args.template_train, args.template_val,
                tokenizer, args.max_length, args.diverse_weight,
            )

        results = train_config(config, args)
        all_results[cfg] = results

    # Summary
    print(f"\n{'='*60}")
    print("TRAINING SUMMARY")
    print(f"{'='*60}")
    for cfg, r in all_results.items():
        last = r["epoch_history"][-1]
        print(f"  Config {cfg} ({r['config']}): "
              f"diverse_val={last['diverse_val_loss']:.4f}, "
              f"template_val={last['template_val_loss']:.4f}, "
              f"{r['elapsed_minutes']:.1f}min")


if __name__ == "__main__":
    main()
