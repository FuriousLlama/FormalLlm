"""
Train a value network to predict solve probability from tactic state.

Uses the same base model as the policy (shared encoder, frozen),
with a linear regression head: hidden_state → scalar ∈ [0, 1].

Usage:
    python train_value.py \
        --data train/rollout_data.jsonl \
        --base-model Qwen/Qwen2.5-Coder-1.5B \
        --output train/value_head.pt
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer


class ValueDataset(Dataset):
    """Load (state, outcome) pairs from JSONL."""

    def __init__(self, jsonl_path: str, tokenizer, max_length: int = 256):
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.entries = []

        with open(jsonl_path) as f:
            for line in f:
                entry = json.loads(line)
                self.entries.append((entry["state"], entry["outcome"]))

    def __len__(self):
        return len(self.entries)

    def __getitem__(self, idx):
        state, outcome = self.entries[idx]
        encoding = self.tokenizer(
            state,
            truncation=True,
            max_length=self.max_length,
            padding="max_length",
            return_tensors="pt",
        )
        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0),
            "target": torch.tensor(outcome, dtype=torch.float32),
        }


class ValueHead(nn.Module):
    """Linear head mapping last hidden state → scalar."""

    def __init__(self, hidden_size: int):
        super().__init__()
        self.linear = nn.Linear(hidden_size, 1)

    def forward(self, hidden_states, attention_mask):
        # Use mean pooling over non-padding tokens
        mask = attention_mask.unsqueeze(-1).float()
        pooled = (hidden_states * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
        return torch.sigmoid(self.linear(pooled)).squeeze(-1)


def train(args):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"Loading data from {args.data}...")
    dataset = ValueDataset(args.data, tokenizer, max_length=args.max_length)
    print(f"  {len(dataset):,} examples")

    # Split 90/10 for train/val
    val_size = max(1, len(dataset) // 10)
    train_size = len(dataset) - val_size
    train_ds, val_ds = torch.utils.data.random_split(dataset, [train_size, val_size])

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size)

    # Load base model (frozen) — just need the encoder
    print(f"Loading base model {args.base_model} (frozen)...")
    base_model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
    )
    base_model.to(device)
    base_model.eval()
    for param in base_model.parameters():
        param.requires_grad = False

    hidden_size = base_model.config.hidden_size
    value_head = ValueHead(hidden_size).to(device)

    optimizer = torch.optim.AdamW(value_head.parameters(), lr=args.lr)
    criterion = nn.MSELoss()

    best_val_loss = float("inf")
    start = time.time()

    print(f"\nTraining value head for {args.epochs} epochs...")
    for epoch in range(args.epochs):
        value_head.train()
        total_loss = 0.0
        n_batches = 0

        for batch in train_loader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            targets = batch["target"].to(device)

            with torch.no_grad():
                hidden = base_model(
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                    output_hidden_states=True,
                ).hidden_states[-1]

            preds = value_head(hidden, attention_mask)
            loss = criterion(preds, targets)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            total_loss += loss.item()
            n_batches += 1

        avg_train = total_loss / max(n_batches, 1)

        # Validation
        value_head.eval()
        val_loss = 0.0
        val_batches = 0
        with torch.no_grad():
            for batch in val_loader:
                input_ids = batch["input_ids"].to(device)
                attention_mask = batch["attention_mask"].to(device)
                targets = batch["target"].to(device)

                hidden = base_model(
                    input_ids=input_ids,
                    attention_mask=attention_mask,
                    output_hidden_states=True,
                ).hidden_states[-1]

                preds = value_head(hidden, attention_mask)
                val_loss += criterion(preds, targets).item()
                val_batches += 1

        avg_val = val_loss / max(val_batches, 1)
        saved = ""
        if avg_val < best_val_loss:
            best_val_loss = avg_val
            torch.save(value_head.state_dict(), args.output)
            saved = " (saved)"

        print(f"  Epoch {epoch+1}/{args.epochs} | train_loss {avg_train:.4f} | val_loss {avg_val:.4f}{saved}")

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.0f}s. Best val loss: {best_val_loss:.4f}")
    print(f"Value head saved to: {args.output}")


def main():
    parser = argparse.ArgumentParser(description="Train value network on rollout data")
    parser.add_argument("--data", required=True, help="Rollout data JSONL")
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-Coder-1.5B")
    parser.add_argument("--output", default="train/value_head.pt")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-length", type=int, default=256)
    args = parser.parse_args()

    train(args)


if __name__ == "__main__":
    main()
