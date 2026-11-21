"""
LoRA fine-tuning of a base LLM on Mathlib tactic data.

Usage:
    python pretrain.py \
        --data train/mathlib_train.jsonl \
        --val train/mathlib_val.jsonl \
        --base-model Qwen/Qwen2.5-Coder-1.5B \
        --output train/policy_adapter/ \
        --epochs 3 --lr 2e-4 --batch-size 8

Produces a LoRA adapter directory that can be loaded with:
    model = AutoModelForCausalLM.from_pretrained(base_model)
    model = PeftModel.from_pretrained(model, adapter_path)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

import torch
from peft import LoraConfig, TaskType, get_peft_model
from torch.utils.data import DataLoader
from transformers import AutoModelForCausalLM, AutoTokenizer

try:
    from lean_mcts.train.dataset import TacticDataset, collate_fn
except ImportError:
    try:
        from train.dataset import TacticDataset, collate_fn
    except ImportError:
        from dataset import TacticDataset, collate_fn


def evaluate(model, val_loader, device, max_batches: int = 500) -> float:
    """Compute average validation loss on a subsample, skipping nan batches."""
    model.eval()
    torch.cuda.empty_cache()
    total_loss = 0.0
    total_batches = 0
    with torch.no_grad():
        for i, batch in enumerate(val_loader):
            if i >= max_batches:
                break
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)
            outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
            loss_val = outputs.loss.item()
            if not math.isnan(loss_val):
                total_loss += loss_val
                total_batches += 1
    model.train()
    if total_batches == 0:
        return float("inf")
    return total_loss / total_batches


def _save_run_summary(stats: dict, output_dir: str):
    """Write run summary to both the adapter dir and train/runs/."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "training_stats.json", "w") as f:
        json.dump(stats, f, indent=2)
    runs_dir = out.parent / "runs"
    runs_dir.mkdir(exist_ok=True)
    run_file = runs_dir / f"{out.name}.json"
    with open(run_file, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"Run summary: {run_file}")


# ---------------------------------------------------------------------------
# Progress log — append-only JSONL file, one line per stage transition.
# Survives SIGSEGV because each line is flushed immediately.
# Read with:  tail -f train/adapter_*/progress.jsonl
#             jq . train/adapter_*/progress.jsonl
# ---------------------------------------------------------------------------

_progress_file = None  # set by main()


def _log_stage(stage: str, **extra):
    """Append a timestamped stage marker to progress.jsonl."""
    global _progress_file
    if _progress_file is None:
        return
    entry = {
        "time": time.strftime("%H:%M:%S"),
        "elapsed": round(time.time() - _progress_t0, 1),
        "stage": stage,
        **extra,
    }
    with open(_progress_file, "a") as f:
        f.write(json.dumps(entry) + "\n")
        f.flush()
        os.fsync(f.fileno())  # force to disk — survives SIGKILL


def _gpu_info() -> dict:
    """Collect GPU hardware info (returns empty dict on CPU)."""
    if not torch.cuda.is_available():
        return {"device": "cpu"}
    return {
        "device": "cuda",
        "gpu_name": torch.cuda.get_device_name(0),
        "gpu_vram_total_gb": round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1),
    }


def _base_stats(args) -> dict:
    """Capture all run parameters before training starts."""
    return {
        "run_id": Path(args.output).name,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_model": args.base_model,
        "train_data": args.data,
        "val_data": args.val,
        "lora_r": args.lora_r,
        "lora_alpha": args.lora_alpha,
        "lora_dropout": 0.05,
        "target_modules": ["q_proj", "v_proj"],
        "dtype": "bfloat16",
        "optimizer": "AdamW",
        "weight_decay": 0.01,
        "lr": args.lr,
        "batch_size": args.batch_size,
        "gradient_accumulation": args.gradient_accumulation,
        "effective_batch_size": args.batch_size * args.gradient_accumulation,
        "max_length": args.max_length,
        "epochs_requested": args.epochs,
        "patience": args.patience,
        "adapter_path": str(args.output),
        **_gpu_info(),
    }


def train(args, started_at: str = None):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # Seed for reproducibility
    if args.seed is not None:
        import random
        random.seed(args.seed)
        torch.manual_seed(args.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(args.seed)
        print(f"Seed: {args.seed}")

    # Load tokenizer
    _log_stage("load_tokenizer", model=args.base_model)
    print(f"Loading tokenizer from {args.base_model}...")
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Load datasets
    _log_stage("load_train_data", path=args.data)
    print(f"Loading training data from {args.data}...")
    train_ds = TacticDataset(args.data, tokenizer, max_length=args.max_length)
    print(f"  Train: {len(train_ds):,} examples")
    _log_stage("train_data_loaded", n=len(train_ds))

    val_ds = None
    val_loader = None
    if args.val:
        _log_stage("load_val_data", path=args.val)
        val_ds = TacticDataset(args.val, tokenizer, max_length=args.max_length)
        print(f"  Val:   {len(val_ds):,} examples")
        val_loader = DataLoader(val_ds, batch_size=args.batch_size, collate_fn=collate_fn)
        _log_stage("val_data_loaded", n=len(val_ds))

    train_loader = DataLoader(
        train_ds,
        batch_size=args.batch_size,
        shuffle=True,
        collate_fn=collate_fn,
        drop_last=True,
    )

    # Load base model
    _log_stage("load_model", model=args.base_model)
    print(f"Loading base model {args.base_model}...")
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
    )
    _log_stage("model_loaded")

    # Apply LoRA
    _log_stage("apply_lora", r=args.lora_r, alpha=args.lora_alpha)
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        target_modules=["q_proj", "v_proj"],
        lora_dropout=0.05,
        bias="none",
    )
    model = get_peft_model(model, lora_config)
    model.to(device)
    model.print_trainable_parameters()
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total_params = sum(p.numel() for p in model.parameters())
    _log_stage("model_on_device", trainable=trainable_params, total=total_params)

    # Optimizer
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)

    # Training loop
    grad_accum = args.gradient_accumulation
    global_step = 0
    best_val_loss = float("inf")
    patience_counter = 0
    epoch_history = []  # per-epoch loss records
    start_time = time.time()

    print(f"\nTraining for {args.epochs} epochs...")
    print(f"  Batch size: {args.batch_size} x {grad_accum} grad_accum = {args.batch_size * grad_accum} effective")
    print(f"  Steps per epoch: {len(train_loader) // grad_accum}")
    print()

    _log_stage("training_start", epochs=args.epochs,
               steps_per_epoch=len(train_loader) // grad_accum)

    for epoch in range(args.epochs):
        _log_stage("epoch_start", epoch=epoch + 1)
        model.train()
        epoch_loss = 0.0
        epoch_steps = 0

        for step, batch in enumerate(train_loader):
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)

            with torch.amp.autocast("cuda", dtype=torch.bfloat16):
                outputs = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
                loss = outputs.loss / grad_accum

            loss.backward()

            if (step + 1) % grad_accum == 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                optimizer.zero_grad()
                global_step += 1
                epoch_loss += outputs.loss.item()
                epoch_steps += 1

                if global_step % args.log_every == 0:
                    avg = epoch_loss / epoch_steps
                    elapsed = time.time() - start_time
                    alloc = torch.cuda.memory_allocated() / 1e9
                    resv = torch.cuda.memory_reserved() / 1e9
                    print(f"  step {global_step:5d} | loss {avg:.4f} | {elapsed:.0f}s | vram {alloc:.1f}/{resv:.1f}G")
                    _log_stage("step", step=global_step, loss=round(avg, 4),
                               vram_gb=round(alloc, 1))

        avg_train = epoch_loss / max(epoch_steps, 1)
        epoch_elapsed = time.time() - start_time
        msg = f"Epoch {epoch+1}/{args.epochs} | train_loss {avg_train:.4f}"

        # Always save after each epoch (training is expensive)
        _log_stage("saving_adapter", epoch=epoch + 1)
        _save_adapter(model, tokenizer, args)
        msg += " (saved)"

        # Validation
        val_loss = None
        if val_loader is not None:
            _log_stage("validation_start", epoch=epoch + 1)
            val_loss = evaluate(model, val_loader, device)
            msg += f" | val_loss {val_loss:.4f}"

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                msg += " (best)"
            else:
                patience_counter += 1
                msg += f" (patience {patience_counter}/{args.patience})"

        epoch_record = {
            "epoch": epoch + 1,
            "train_loss": round(avg_train, 4),
            "elapsed_seconds": round(epoch_elapsed, 1),
        }
        if val_loss is not None:
            epoch_record["val_loss"] = round(val_loss, 4)
        if torch.cuda.is_available():
            epoch_record["gpu_vram_allocated_gb"] = round(torch.cuda.memory_allocated() / 1e9, 2)
            epoch_record["gpu_vram_reserved_gb"] = round(torch.cuda.memory_reserved() / 1e9, 2)
        epoch_history.append(epoch_record)
        _log_stage("epoch_end", epoch=epoch + 1, train_loss=round(avg_train, 4),
                   val_loss=round(val_loss, 4) if val_loss is not None else None)

        print(msg)

        if patience_counter >= args.patience:
            print(f"Early stopping after {epoch+1} epochs (no improvement for {args.patience} epochs)")
            _log_stage("early_stop", epoch=epoch + 1, patience=args.patience)
            break

    elapsed = time.time() - start_time
    _log_stage("done", elapsed_min=round(elapsed / 60, 1),
               best_val_loss=round(best_val_loss, 4))
    print(f"\nTraining complete in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"Best val loss: {best_val_loss:.4f}")
    print(f"Adapter saved to: {args.output}")

    # Save successful run summary
    stats = _base_stats(args)
    if started_at:
        stats["started_at"] = started_at
    stats.update({
        "status": "success",
        "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "trainable_params": trainable_params,
        "total_params": total_params,
        "train_examples": len(train_ds),
        "val_examples": len(val_ds) if val_ds else 0,
        "epochs_completed": min(epoch + 1, args.epochs),
        "best_val_loss": best_val_loss,
        "final_train_loss": avg_train,
        "total_steps": global_step,
        "epoch_history": epoch_history,
        "elapsed_seconds": round(elapsed, 1),
        "elapsed_minutes": round(elapsed / 60, 1),
    })
    _save_run_summary(stats, args.output)


def _save_adapter(model, tokenizer, args):
    """Save LoRA adapter and tokenizer."""
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out)
    tokenizer.save_pretrained(out)


def main():
    parser = argparse.ArgumentParser(description="LoRA fine-tune policy on Mathlib tactics")
    parser.add_argument("--data", required=True, help="Training JSONL")
    parser.add_argument("--val", default=None, help="Validation JSONL")
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-Coder-1.5B")
    parser.add_argument("--output", default=None,
                        help="Output dir (default: train/adapter_YYYYMMDD_HHMMSS/)")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--gradient-accumulation", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--patience", type=int, default=2, help="Early stopping patience")
    parser.add_argument("--log-every", type=int, default=50, help="Log every N steps")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    args = parser.parse_args()

    if args.output is None:
        args.output = f"train/adapter_{time.strftime('%Y%m%d_%H%M%S')}"

    # Write "started" marker BEFORE training — if the process is killed (SIGKILL,
    # SIGSEGV, kernel OOM), this file remains with status "started" so we know
    # it didn't finish cleanly.
    stats = _base_stats(args)
    stats["status"] = "started"
    stats["pid"] = os.getpid()
    _save_run_summary(stats, args.output)

    # Set up progress log — append-only, fsync'd, survives crashes
    global _progress_file, _progress_t0
    _progress_t0 = time.time()
    _progress_file = Path(args.output) / "progress.jsonl"
    _log_stage("init", pid=os.getpid(), data=args.data, epochs=args.epochs)

    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    start = time.time()
    try:
        train(args, started_at=started_at)
    except KeyboardInterrupt:
        elapsed = time.time() - start
        print(f"\nInterrupted after {elapsed:.0f}s")
        stats.update({
            "status": "interrupted",
            "elapsed_seconds": round(elapsed, 1),
            "elapsed_minutes": round(elapsed / 60, 1),
        })
        _save_run_summary(stats, args.output)
        raise SystemExit(130)
    except Exception as e:
        elapsed = time.time() - start
        import traceback
        tb = traceback.format_exc()
        err_msg = str(e)
        print(f"\nFAILED after {elapsed:.0f}s: {e}")
        print(tb)

        # Classify the failure
        if "out of memory" in err_msg.lower() or "CUDA out of memory" in err_msg:
            failure_kind = "cuda_oom"
        elif "nan" in err_msg.lower():
            failure_kind = "nan_loss"
        elif "CUDA error" in err_msg or "device-side assert" in err_msg:
            failure_kind = "cuda_error"
        else:
            failure_kind = "exception"

        stats.update({
            "status": f"failed:{failure_kind}",
            "error": err_msg[:2000],
            "error_type": type(e).__name__,
            "traceback": tb[-3000:],
            "elapsed_seconds": round(elapsed, 1),
            "elapsed_minutes": round(elapsed / 60, 1),
        })
        _save_run_summary(stats, args.output)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
