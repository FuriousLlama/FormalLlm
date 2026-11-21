"""
Value network: estimates the probability of successful completion from a tactic state.

Two implementations:
  - StubValueNetwork: Returns 0.5 (uniform prior, MCTS uses rollout simulation)
  - TrainedValueNetwork: Frozen base encoder + trained linear head → scalar ∈ [0, 1]
"""

from __future__ import annotations


class StubValueNetwork:
    """Returns 0.5 for every state. MCTS relies on simulation rollouts instead."""

    def estimate(self, state: object) -> float:
        return 0.5

    def __call__(self, state: object) -> float:
        return self.estimate(state)


class TrainedValueNetwork:
    """
    Predict solve probability from tactic state.

    Uses the same base model as the policy (frozen encoder) with a trained
    linear head that maps mean-pooled hidden states to a scalar in [0, 1].
    """

    def __init__(self, base_model: str, value_head_path: str, device: str = "cuda"):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        # Import the ValueHead class from training code
        try:
            from lean_mcts.train.train_value import ValueHead
        except ImportError:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent.parent / "train"))
            from train_value import ValueHead

        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        self.base_model = AutoModelForCausalLM.from_pretrained(
            base_model,
            torch_dtype=torch.bfloat16,
            trust_remote_code=True,
        )
        self.base_model.to(device)
        self.base_model.eval()

        hidden_size = self.base_model.config.hidden_size
        self.value_head = ValueHead(hidden_size)
        self.value_head.load_state_dict(torch.load(value_head_path, map_location=device, weights_only=True))
        self.value_head.to(device)
        self.value_head.eval()

        self._cache: dict[str, float] = {}

    def estimate(self, state: object) -> float:
        """Predict solve probability for a tactic state."""
        import torch

        state_str = state.pp if hasattr(state, "pp") else str(state)

        if state_str in self._cache:
            return self._cache[state_str]

        encoding = self.tokenizer(
            state_str,
            truncation=True,
            max_length=256,
            padding="max_length",
            return_tensors="pt",
        ).to(self.device)

        with torch.no_grad():
            hidden = self.base_model(
                input_ids=encoding["input_ids"],
                attention_mask=encoding["attention_mask"],
                output_hidden_states=True,
            ).hidden_states[-1]
            value = self.value_head(hidden, encoding["attention_mask"]).item()

        self._cache[state_str] = value
        return value

    def __call__(self, state: object) -> float:
        return self.estimate(state)
