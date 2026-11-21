"""Partial evaluator for Lean expressions via Pantograph daemon.

Evaluates Lean expressions on concrete inputs by building
temporary `def` + `#eval` code and running through check_compile.

Phase 1 of Sprint 22.
"""

from __future__ import annotations

import threading
from typing import Optional


_eval_counter = 0
_eval_lock = threading.Lock()


def _next_eval_id() -> int:
    global _eval_counter
    with _eval_lock:
        _eval_counter += 1
        return _eval_counter


def _build_eval_code(
    expr: str,
    input_type: str,
    return_type: str,
    inputs: list[str],
    eval_id: int = 0,
) -> str:
    """Build Lean code to evaluate an expression on concrete inputs.

    Constructs a `def` with `exact <expr>` and `#eval` calls for each input.
    """
    if input_type == "List (List Int)":
        param_name = "xss"
        params = f"(xss : {input_type})"
    elif input_type == "Nat":
        param_name = "n"
        params = "(n : Nat)"
    else:
        param_name = "xs"
        params = f"(xs : {input_type})"

    fname = f"_pe_{eval_id}"

    lines = ["set_option linter.unusedVariables false"]
    lines.append(f"def {fname} {params} : {return_type} := by")
    lines.append(f"  exact {expr}")
    lines.append("")
    for inp in inputs:
        # Annotate input with its type to avoid Lean inferring wrong type
        # (e.g., [9, 16] inferred as List Nat instead of List Int)
        lines.append(f"#eval {fname} ({inp} : {input_type})")
    return "\n".join(lines) + "\n"


class PartialEvaluator:
    """Evaluate Lean expressions on concrete inputs via Pantograph daemon."""

    def __init__(self, client=None):
        """Initialize with a PantographClient (or None for cache-only mode)."""
        self.client = client
        self._cache: dict[tuple[str, str], str] = {}  # (expr, input_str) → output_str

    def seed_cache(self, expr: str, outputs: dict[str, str]):
        """Pre-populate cache from known fragment outputs."""
        for inp, out in outputs.items():
            self._cache[(expr, inp)] = out

    def evaluate(
        self,
        expr: str,
        input_type: str,
        return_type: str,
        inputs: list[str],
    ) -> dict[str, str]:
        """Evaluate expr on each input, return {input: output}.

        Uses cache first; falls back to Lean evaluation via daemon.
        Returns empty dict entries for inputs that fail evaluation.
        """
        results = {}
        uncached = []

        for inp in inputs:
            cached = self._cache.get((expr, inp))
            if cached is not None:
                results[inp] = cached
            else:
                uncached.append(inp)

        if not uncached:
            return results

        if self.client is None:
            # No daemon — can't evaluate uncached
            return results

        # Batch into chunks to avoid overwhelming the daemon
        BATCH_SIZE = 100
        for batch_start in range(0, len(uncached), BATCH_SIZE):
            batch = uncached[batch_start:batch_start + BATCH_SIZE]
            eval_id = _next_eval_id()
            code = _build_eval_code(expr, input_type, return_type, batch, eval_id)

            try:
                units = self.client.check_compile(code)
            except Exception:
                continue

            # Parse info messages
            info_messages = []
            has_error = False
            for cu in units:
                for msg in cu["messages"]:
                    if msg["severity"] == "INFORMATION":
                        info_messages.append(msg["data"].strip())
                    elif msg["severity"] == "ERROR":
                        has_error = True

            if has_error and not info_messages:
                continue

            for i, inp in enumerate(batch):
                if i < len(info_messages):
                    val = info_messages[i]
                    results[inp] = val
                    self._cache[(expr, inp)] = val

        return results

    def evaluate_batch(
        self,
        exprs: list[str],
        input_type: str,
        return_type: str,
        inputs: list[str],
    ) -> list[dict[str, str]]:
        """Evaluate multiple expressions on the same inputs."""
        return [
            self.evaluate(expr, input_type, return_type, inputs)
            for expr in exprs
        ]

    def cache_size(self) -> int:
        return len(self._cache)
