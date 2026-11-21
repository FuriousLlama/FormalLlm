"""
Tactic dataset loader: JSONL → tokenized tensors for policy fine-tuning.

Input format (JSONL):
    {"state": "⊢ h + 1 > 0", "tactic": "omega", ...}

Training format:
    Input:  "[STATE]\n{state}\n[TACTIC]\n"
    Target: "{tactic}</s>"

The model learns: given a tactic state, predict the next tactic.
"""

from __future__ import annotations

import json
from pathlib import Path

import torch
from torch.utils.data import Dataset


# Special tokens for state/tactic boundary
STATE_PREFIX = "[STATE]\n"
TACTIC_PREFIX = "\n[TACTIC]\n"


class TacticDataset(Dataset):
    """Load JSONL tactic data as tokenized (input, target) pairs."""

    def __init__(
        self,
        jsonl_path: str | Path,
        tokenizer,
        max_length: int = 512,
    ):
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.entries = []

        with open(jsonl_path) as f:
            for line in f:
                entry = json.loads(line)
                self.entries.append((entry["state"], entry["tactic"]))

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        state, tactic = self.entries[idx]

        # Build the full sequence: [STATE]\n{state}\n[TACTIC]\n{tactic}
        prompt = f"{STATE_PREFIX}{state}{TACTIC_PREFIX}"
        full_text = f"{prompt}{tactic}"

        # Tokenize the full sequence
        encoding = self.tokenizer(
            full_text,
            truncation=True,
            max_length=self.max_length,
            padding="max_length",
            return_tensors="pt",
        )

        input_ids = encoding["input_ids"].squeeze(0)
        attention_mask = encoding["attention_mask"].squeeze(0)

        # Labels: same as input_ids but mask the prompt tokens with -100
        # so the loss is only computed on the tactic prediction
        prompt_encoding = self.tokenizer(
            prompt,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt",
        )
        prompt_len = prompt_encoding["input_ids"].shape[1]

        labels = input_ids.clone()
        labels[:prompt_len] = -100  # mask prompt tokens
        labels[attention_mask == 0] = -100  # mask padding

        return {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "labels": labels,
        }


class WeightedTacticDataset(TacticDataset):
    """TacticDataset with per-example loss weights.

    Loads from JSONL with optional 'weight' field. If absent, weight=1.0.
    Used for Sprint 28 loss-weighted training (Config B/C).
    """

    def __init__(
        self,
        jsonl_path: str | Path,
        tokenizer,
        max_length: int = 512,
        default_weight: float = 1.0,
    ):
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.entries = []
        self.weights = []

        with open(jsonl_path) as f:
            for line in f:
                entry = json.loads(line)
                self.entries.append((entry["state"], entry["tactic"]))
                self.weights.append(entry.get("weight", default_weight))

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        result = super().__getitem__(idx)
        result["weight"] = torch.tensor(self.weights[idx], dtype=torch.float32)
        return result


def collate_fn(batch: list[dict[str, torch.Tensor]]) -> dict[str, torch.Tensor]:
    """Collate a batch of examples into stacked tensors."""
    return {
        key: torch.stack([example[key] for example in batch])
        for key in batch[0]
    }
