"""
Policy network for MCTS: proposes candidate tactics given a tactic state.

Policies:
  1. FixedPolicy: Hand-crafted list of common Lean tactics (no GPU, fallback)
  2. ExpandedFixedPolicy: FixedPolicy + all enumerated synthesis expressions (Sprint 13)
  3. PromptedPolicy: Use an existing LLM prompted with the tactic state (no training)
  4. TrainedPolicy: LoRA fine-tuned on Mathlib tactic data (Sprint 12)
  5. GoalConditionedPolicy: Generative LoRA for novel tactic strings (Sprint 16)
  6. HybridGoalConditionedPolicy: GoalConditioned + ExpandedFixed safety net (Sprint 16)
"""

from __future__ import annotations

import math
import os
from typing import Optional

# Ollama /api/chat endpoint; override with the OLLAMA_URL environment variable.
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")


class FixedPolicy:
    """
    Fallback policy: hand-crafted Lean 4 tactics for program synthesis.

    No GPU, no model, no training. Useful for testing the MCTS loop
    before a real policy network is available.

    Returns all tactics for every state — MCTS relies on Lean's type
    checker to reject invalid ones (the whole point of per-step verification).

    Includes both generic proof tactics AND synthesis-specific `exact` expressions
    for the benchmark tasks (all have signature `xs : List Int → <Type>`).
    """

    # Generic proof/tactic-mode tactics
    PROOF_TACTICS = [
        "rfl", "trivial", "decide", "omega", "simp", "norm_num",
        "assumption", "contradiction",
        "intro h", "intro x", "intro a", "intro n", "intros",
        "constructor", "ext", "funext x", "congr",
        "linarith", "ring",
        "simp only [*]",
        "first | simp | omega | decide",
    ]

    # Synthesis expressions: `exact <expr>` for constructing function bodies.
    # All benchmark tasks have `xs : List Int` in context.
    # Wrong-type expressions are rejected by Lean's kernel (harmless).
    SYNTHESIS_TACTICS = [
        # --- Primitives ---
        "exact 0",
        "exact xs",
        "exact []",
        "exact xs.length",
        "exact xs.reverse",
        "exact xs.eraseDups",

        # --- List Int → List Int (map) ---
        "exact xs.map (fun x => x * x)",
        "exact xs.map (fun x => x * 2)",
        "exact xs.map (fun x => -x)",
        "exact xs.map (fun x => x + 1)",

        # --- List Int → List Int (filter) ---
        "exact xs.filter (fun x => decide (x > 0))",
        "exact xs.filter (fun x => decide (x < 0))",
        "exact xs.filter (fun x => decide (x ≥ 0))",
        "exact xs.filter (fun x => decide (x % 2 = 0))",
        "exact xs.filter (fun x => decide (x % 2 ≠ 0))",

        # --- List Int → List Int (takeWhile / dropWhile) ---
        "exact xs.takeWhile (fun x => decide (x > 0))",
        "exact xs.takeWhile (fun x => decide (x ≥ 0))",
        "exact xs.dropWhile (fun x => decide (x < 0))",
        "exact xs.dropWhile (fun x => decide (x ≤ 0))",

        # --- List Int → List Int (sort) ---
        "exact List.mergeSort xs",
        "exact (List.mergeSort xs).reverse",
        "exact List.mergeSort (xs.eraseDups)",

        # --- List Int → List Int (scan / accumulate) ---
        # NOTE: LeanDojo REPL can't parse · syntax, use explicit lambdas
        "exact (List.scanl (fun a b => a + b) 0 xs).drop 1",
        "exact (List.scanl (fun a b => a + b) 0 xs).tail",

        # --- List Int → Nat (filter + length) ---
        "exact (xs.filter (fun x => decide (x > 0))).length",
        "exact (xs.filter (fun x => decide (x < 0))).length",
        "exact (xs.filter (fun x => decide (x % 2 = 0))).length",
        "exact (xs.filter (fun x => decide (x % 2 ≠ 0))).length",

        # --- List Int → Nat (map + fold) ---
        "exact (xs.map (fun x => x.natAbs)).foldl max 0",
        "exact (xs.map (fun x => x.natAbs)).foldl (fun a b => a + b) 0",

        # --- List Int → Int (fold) ---
        "exact xs.foldl (fun a b => a + b) 0",
        "exact xs.foldl max 0",
        "exact xs.foldl min 0",

        # --- List Int → Int (map + fold) ---
        "exact (xs.map (fun x => x * 2)).foldl (fun a b => a + b) 0",
        "exact (xs.map (fun x => x * x)).foldl (fun a b => a + b) 0",

        # --- Sprint 14: Expanded library vocabulary ---
        # scanl (running max — prefix sums already in line 80)
        "exact (List.scanl max 0 xs).drop 1",
        # tail / dropLast
        "exact xs.tail",
        "exact xs.dropLast",
        # intersperse
        "exact xs.intersperse 0",
        # zip + tail (pairwise sums)
        "exact (xs.zip xs.tail).map (fun (a, b) => a + b)",
        # zipIdx + filter (every other element)
        "exact (xs.zipIdx.filter (fun (_, i) => decide (i % 2 = 0))).map Prod.fst",
        # filter (even/odd by modular arithmetic)
        "exact (xs.filter (fun x => decide (x % 2 = 0))).reverse",
        "exact (xs.filter (fun x => decide (x % 2 = 0))).foldl max 0",
        # eraseDups compositions
        "exact (xs.filter (fun x => decide (x > 0)) |>.eraseDups).length",
        # map + filter + fold (3-step)
        "exact ((xs.filter (fun x => decide (x > 0))).map (fun x => x * x)).foldl (fun a b => a + b) 0",
        # filter → sort → fold
        "exact (List.mergeSort (xs.filter (fun x => decide (x > 0)))).foldl (fun a b => a + b) 0",

        # --- Sprint 14: Multi-type inputs (List (List Int), two-arg, Nat) ---
        # List (List Int)
        "exact xss.flatten",
        "exact (xss.map List.length).foldl max 0",
        # Two-arg (xs ys : List Int)
        "exact List.mergeSort (xs ++ ys)",
        "exact (List.zipWith (fun a b => a * b) xs ys).foldl (fun a b => a + b) 0",
        "exact (xs.filter (fun x => !(ys.contains x))) ++ (ys.filter (fun y => !(xs.contains y)))",
        # Nat input
        "exact (List.range n).map (fun i => i * i)",

        # --- Sprint 14: let-binding tactics for multi-step ---
        "have h := xs.filter (fun x => decide (x > 0))",
        "have h := xs.eraseDups",
        "exact List.mergeSort h",
        "exact h.length",
        "exact h.foldl (fun a b => a + b) 0",
        "exact (h.length, h.foldl (fun a b => a + b) 0)",

        # --- Sprint 19: Fundamental programming patterns ---
        # List.rec (structural recursion on lists)
        "exact List.rec 0 (fun _ _ acc => acc + 1) xs",             # length
        "exact List.rec 0 (fun h _ acc => acc + h) xs",             # sum
        "exact List.rec [] (fun h _ acc => (h * 2) :: acc) xs",     # map *2
        "exact List.rec [] (fun h _ acc => (h + 1) :: acc) xs",     # map +1
        "exact List.rec [] (fun h _ acc => h :: h :: acc) xs",      # stutter
        "exact List.rec [] (fun h _ acc => acc ++ [h]) xs",         # reverse
        "exact List.rec 0 (fun h _ acc => if h > acc then h else acc) xs",   # max
        "exact List.rec 0 (fun h _ acc => if h < acc then h else acc) xs",   # min
        "exact List.rec 0 (fun h _ acc => if decide (h > 0) then acc + 1 else acc) xs",  # count pos
        "exact List.rec 0 (fun h _ acc => if decide (h < 0) then acc + 1 else acc) xs",  # count neg
        "exact List.rec 1 (fun h _ acc => if decide (h % 2 = 0) then acc else 0) xs",    # all even
        "exact List.rec [] (fun h _ acc => if decide (h > 0) then h :: acc else acc) xs", # filter pos
        "exact List.rec [] (fun h _ acc => if decide (h < 0) then h :: acc else acc) xs", # filter neg

        # Nat.rec (natural number recursion)
        "exact Nat.rec 1 (fun k acc => acc * (k + 1)) n",          # factorial
        "exact Nat.rec 0 (fun k acc => acc + k + 1) n",            # sum 1..n
        "exact Nat.rec 1 (fun _ acc => acc * 2) n",                # power of 2

        # match on List
        "exact match xs with | [] => 0 | h :: _ => h",             # head
        "exact match xs with | [] => [] | _ :: t => t",            # tail
        "exact match xs with | [] => 0 | [x] => x | x :: y :: _ => x + y",  # first two

        # match on Nat
        "exact match n with | 0 => 0 | k + 1 => k",               # predecessor
        "exact match n with | 0 => 1 | _ + 1 => 0",               # is zero

        # if/then/else branching
        "exact if xs.length > 0 then match xs with | h :: _ => h | [] => 0 else 0",  # safe head
        "exact if xs.length > 2 then xs.foldl (fun a b => a + b) 0 else match xs with | [] => 0 | h :: _ => h",  # head or sum

        # Stateful folds (tuple accumulators)
        "exact (xs.foldl (fun (acc, pos) h => (acc ++ [if pos then h else -h], !pos)) ([], true)).1",  # alternate signs
        "exact (xs.foldl (fun (best, second) h => if h > best then (h, best) else if h > second then (best, h) else (best, second)) (0, 0)).2",  # second max
        "exact (xs.foldl (fun (acc, mx) h => let newMax := if h > mx then h else mx; (acc ++ [newMax], newMax)) ([], 0)).1",  # cumul max
        "exact (xs.foldl (fun (idx, found) h => if found then (idx, true) else if decide (h < 0) then (idx, true) else (idx + 1, false)) (0, false)).1",  # first neg idx

        # match + foldl (pair diff, RLE)
        "exact match xs with | [] => [] | h :: t => (t.foldl (fun (acc, prev) curr => (acc ++ [prev - curr], curr)) ([], h)).1",  # pair diff
        "exact match xs with | [] => [] | h :: t => let (groups, cur, cnt) := t.foldl (fun (groups, cur, cnt) x => if x == cur then (groups, cur, cnt + 1) else (groups ++ [(cur, cnt)], x, 1)) ([], h, 1); groups ++ [(cur, cnt)]",  # RLE

        # Interleave (two-arg, recursive via match)
        "exact match xs, ys with | [], _ => ys | _, [] => xs | h1 :: t1, h2 :: t2 => h1 :: h2 :: interleave t1 t2",  # interleave
    ]

    SYNTHESIS_TACTICS_SET = frozenset(SYNTHESIS_TACTICS)
    TACTICS = PROOF_TACTICS + SYNTHESIS_TACTICS

    def propose_tactics(self, state: object, k: int = None) -> list[str]:
        """Return up to k tactics. For small k, sample randomly for diversity."""
        if k is None or k >= len(self.TACTICS):
            return list(self.TACTICS)
        import random
        return random.sample(self.TACTICS, k)

    def __call__(self, state: object, k: int = None) -> list[str]:
        return self.propose_tactics(state, k)


class ExpandedFixedPolicy:
    """
    FixedPolicy extended with all enumerated synthesis expressions.

    Loads expressions from synth/expressions.jsonl (Phase 1 output) and
    adds them to the fixed tactic list. Pre-filters by return type when
    the goal type can be parsed from the state string, to reduce expansion cost.

    No GPU, no model, no training.
    """

    # Return type keywords to look for in the state string
    _TYPE_KEYWORDS = {
        "Nat": "Nat",
        "List Int": "List Int",
        "List Nat": "List Nat",
        "Int": "Int",
        "Option Int": "Option Int",
    }

    def __init__(self, expressions_path: str = None):
        import json
        from pathlib import Path

        if expressions_path is None:
            # Default: look relative to lean_mcts/
            base = Path(__file__).resolve().parent.parent
            expressions_path = str(base / "synth" / "expressions.jsonl")

        # Start with FixedPolicy tactics
        self.proof_tactics = list(FixedPolicy.PROOF_TACTICS)
        self.synthesis_tactics = list(FixedPolicy.SYNTHESIS_TACTICS)
        existing = set(self.synthesis_tactics)

        # Load enumerated expressions
        self._by_type: dict[str, list[str]] = {}  # return_type → tactics
        n_added = 0
        with open(expressions_path) as f:
            for line in f:
                entry = json.loads(line)
                tactic = entry["tactic"]
                ret_type = entry["return_type"]

                if tactic not in existing:
                    self.synthesis_tactics.append(tactic)
                    existing.add(tactic)
                    n_added += 1

                self._by_type.setdefault(ret_type, []).append(tactic)

        self.all_tactics = self.proof_tactics + self.synthesis_tactics

    def _extract_goal_type(self, state) -> str | None:
        """Try to parse the return type from a tactic state string."""
        state_str = state.pp if hasattr(state, "pp") else str(state)
        # Look for "⊢ <type>" at the end of the state
        if "⊢" in state_str:
            goal = state_str.split("⊢")[-1].strip()
            return goal
        return None

    def _filter_by_type(self, state, tactics: list[str]) -> list[str]:
        """Pre-filter tactics by matching return type when possible."""
        goal = self._extract_goal_type(state)
        if goal is None:
            return tactics

        # Get type-matched synthesis tactics from expressions.jsonl
        matched = self._by_type.get(goal, [])
        if matched:
            # Include proof tactics + type-matched + base SYNTHESIS_TACTICS
            # (base tactics cover multi-type inputs like xss.flatten, xs++ys,
            #  List.range n that aren't in expressions.jsonl)
            matched_set = set(matched)
            base_extra = [t for t in self.synthesis_tactics
                          if t in FixedPolicy.SYNTHESIS_TACTICS_SET
                          and t not in matched_set]
            return self.proof_tactics + matched + base_extra
        # Fall back to all if no type match
        return tactics

    def propose_tactics(self, state: object, k: int = None) -> list[str]:
        """Return tactics filtered by goal type. For small k, sample."""
        filtered = self._filter_by_type(state, self.all_tactics)
        if k is None or k >= len(filtered):
            return list(filtered)
        import random
        return random.sample(filtered, k)

    def __call__(self, state: object, k: int = None) -> list[str]:
        return self.propose_tactics(state, k)


class PromptedPolicy:
    """
    Use a local LLM to generate candidate tactics from a tactic state.

    The model is prompted with the pretty-printed tactic state and asked
    to generate plausible next tactics. No fine-tuning — relies on the
    model's pretraining on code/math.
    """

    def __init__(
        self,
        model_name: str = "deepseek-ai/deepseek-coder-1.3b-instruct",
        device: str = "cuda",
        max_new_tokens: int = 64,
    ):
        from transformers import AutoModelForCausalLM, AutoTokenizer
        import torch

        self.device = device
        self.max_new_tokens = max_new_tokens
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float16,
            device_map=device,
        )
        self.model.eval()

    def propose_tactics(self, state: object, k: int = 10) -> list[str]:
        """
        Generate k candidate tactics for the given tactic state.

        Args:
            state: TacticState from LeanDojo (has .pp attribute with pretty-printed state)
            k: Number of candidates to generate

        Returns:
            List of tactic strings (may contain duplicates or invalid tactics —
            MCTS expansion filters through Lean's type checker)
        """
        import torch

        # Format the tactic state as a prompt
        state_str = state.pp if hasattr(state, 'pp') else str(state)
        prompt = self._build_prompt(state_str)

        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                num_return_sequences=k,
                do_sample=True,
                temperature=0.7,
                top_p=0.9,
                pad_token_id=self.tokenizer.eos_token_id,
            )

        tactics = []
        for output in outputs:
            # Decode only the generated part (after the prompt)
            generated = output[inputs.input_ids.shape[1]:]
            text = self.tokenizer.decode(generated, skip_special_tokens=True)
            tactic = self._extract_tactic(text)
            if tactic:
                tactics.append(tactic)

        return tactics if tactics else ["sorry"]  # Fallback: always have at least one

    def _build_prompt(self, state_str: str) -> str:
        """Format the tactic state into an LLM prompt."""
        return (
            "You are a Lean 4 theorem prover. Given the current proof state, "
            "suggest the next tactic to apply.\n\n"
            f"Current proof state:\n```\n{state_str}\n```\n\n"
            "Next tactic (one line, no explanation):\n```lean\n"
        )

    def _extract_tactic(self, text: str) -> Optional[str]:
        """Extract a single tactic from generated text."""
        # Take the first line, strip markdown fences
        line = text.strip().split('\n')[0].strip()
        line = line.strip('`').strip()
        if not line or line == '```':
            return None
        return line

    def __call__(self, state: object, k: int = 10) -> list[str]:
        return self.propose_tactics(state, k)


class TrainedPolicy:
    """
    Fine-tuned policy: state → ranked (tactic, log_prob) pairs.

    Uses a base LLM with LoRA adapter trained on Mathlib (state, tactic) data.
    Returns tactic candidates with log-probabilities for PUCT scoring.
    """

    # Match the training format from train/dataset.py
    STATE_PREFIX = "[STATE]\n"
    TACTIC_PREFIX = "\n[TACTIC]\n"

    def __init__(
        self,
        base_model: str,
        adapter_path: str,
        device: str = "cuda",
        max_new_tokens: int = 128,
    ):
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.device = device
        self.max_new_tokens = max_new_tokens

        self.tokenizer = AutoTokenizer.from_pretrained(adapter_path, trust_remote_code=True)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        base = AutoModelForCausalLM.from_pretrained(
            base_model,
            torch_dtype=torch.bfloat16,
            trust_remote_code=True,
        )
        self.model = PeftModel.from_pretrained(base, adapter_path)
        self.model.to(device)
        self.model.eval()

        # Cache: state_str → list[(tactic, log_prob)]
        self._cache: dict[str, list[tuple[str, float]]] = {}

    def propose_tactics(self, state: object, k: int = 32) -> list[tuple[str, float]]:
        """
        Generate k candidate tactics with log-probabilities.

        Args:
            state: TacticState (has .pp attribute) or string
            k: Number of candidates

        Returns:
            List of (tactic_str, log_prob) sorted by log_prob descending.
        """
        import torch

        state_str = state.pp if hasattr(state, "pp") else str(state)

        if state_str in self._cache:
            return self._cache[state_str][:k]

        prompt = f"{self.STATE_PREFIX}{state_str}{self.TACTIC_PREFIX}"
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)
        prompt_len = inputs.input_ids.shape[1]

        # Generate diverse candidates using sampling at two temperatures
        tactics_with_scores: list[tuple[str, float]] = []
        seen: set[str] = set()

        for temp, n in [(0.6, k // 2), (1.2, k - k // 2)]:
            if n <= 0:
                continue
            with torch.no_grad():
                outputs = self.model.generate(
                    **inputs,
                    max_new_tokens=self.max_new_tokens,
                    num_return_sequences=n,
                    do_sample=True,
                    temperature=temp,
                    top_p=0.95,
                    pad_token_id=self.tokenizer.eos_token_id,
                    return_dict_in_generate=True,
                    output_scores=True,
                )

            # Compute per-sequence log-probabilities
            sequences = outputs.sequences
            scores = outputs.scores  # tuple of (vocab_size,) tensors per step

            for seq_idx in range(sequences.shape[0]):
                generated = sequences[seq_idx, prompt_len:]
                text = self.tokenizer.decode(generated, skip_special_tokens=True)
                tactic = text.strip().split("\n")[0].strip()

                if not tactic or tactic in seen:
                    continue
                seen.add(tactic)

                # Sum log-probs for generated tokens
                log_prob = 0.0
                for t, score in enumerate(scores):
                    if t >= len(generated):
                        break
                    token_id = generated[t].item()
                    if token_id == self.tokenizer.eos_token_id:
                        break
                    lp = torch.log_softmax(score[seq_idx], dim=-1)
                    log_prob += lp[token_id].item()

                tactics_with_scores.append((tactic, log_prob))

        # Sort by log-prob descending
        tactics_with_scores.sort(key=lambda x: x[1], reverse=True)
        self._cache[state_str] = tactics_with_scores

        return tactics_with_scores[:k]

    def __call__(self, state: object, k: int = 32) -> list[tuple[str, float]]:
        """Return (tactic, log_prob) pairs for PUCT scoring."""
        return self.propose_tactics(state, k)


class SynthesisPolicy:
    """
    Hybrid policy: LoRA-generated candidates + FixedPolicy fallback.

    Uses the LoRA model to generate k candidates with PUCT priors,
    then appends FixedPolicy tactics (uniform prior) for coverage.
    This ensures all known-correct tactics are always tried.
    """

    def __init__(
        self,
        base_model: str,
        adapter_path: str,
        device: str = "cuda",
        max_new_tokens: int = 128,
        lora_k: int = 64,
    ):
        self.lora = TrainedPolicy(base_model, adapter_path, device, max_new_tokens)
        self.fixed = FixedPolicy()
        self.lora_k = lora_k

    def propose_tactics(self, state: object, k: int = None) -> list[tuple[str, float]]:
        """Return (tactic, log_prob) pairs: LoRA candidates first, then fixed tactics."""
        # Get LoRA candidates with log-probs
        lora_results = self.lora.propose_tactics(state, k=self.lora_k)
        seen = {t for t, _ in lora_results}

        # Add fixed tactics with uniform (low) prior
        fixed_tactics = self.fixed.propose_tactics(state)
        min_lp = min((lp for _, lp in lora_results), default=-10.0) - 1.0
        for tactic in fixed_tactics:
            if tactic not in seen:
                lora_results.append((tactic, min_lp))
                seen.add(tactic)

        if k is not None and k < len(lora_results):
            return lora_results[:k]
        return lora_results

    def __call__(self, state: object, k: int = None) -> list[tuple[str, float]]:
        return self.propose_tactics(state, k)


class GoalConditionedPolicy:
    """
    Generative policy: reads the goal state and proposes novel tactic strings.

    Uses a LoRA-tuned model with two-temperature sampling for diversity.
    Post-generation filter rejects degenerate outputs.
    Returns (tactic, log_prob) pairs for PUCT scoring.

    Sprint 16: designed to generate tactics NOT in ExpandedFixedPolicy's table.
    """

    STATE_PREFIX = "[STATE]\n"
    TACTIC_PREFIX = "\n[TACTIC]\n"

    def __init__(
        self,
        base_model: str,
        adapter_path: str,
        device: str = "cuda",
        max_new_tokens: int = 128,
        lora_k: int = 64,
    ):
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.device = device
        self.max_new_tokens = max_new_tokens
        self.lora_k = lora_k

        self.tokenizer = AutoTokenizer.from_pretrained(adapter_path, trust_remote_code=True)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        base = AutoModelForCausalLM.from_pretrained(
            base_model,
            torch_dtype=torch.bfloat16,
            trust_remote_code=True,
        )
        self.model = PeftModel.from_pretrained(base, adapter_path)
        self.model.to(device)
        self.model.eval()

        self._cache: dict[str, list[tuple[str, float]]] = {}

    _CASE_RE = __import__("re").compile(r'^case \w+\n')

    @classmethod
    def _strip_case_prefix(cls, state_str: str) -> str:
        """Strip 'case <name>\\n' prefix from sub-goal states.

        After `constructor`, Pantograph produces states like:
            case fst
            xs : List ℤ
            ⊢ ℕ
        The model was trained on both prefixed and unprefixed forms,
        but stripping gives cleaner input for the focused sub-goal.

        If the state has multiple case blocks (e.g., 'case fst\\n...\\ncase snd\\n...'),
        only keep the first sub-goal block (before the second 'case').
        """
        # Find all case markers
        parts = cls._CASE_RE.split(state_str)
        if len(parts) <= 1:
            return state_str  # no case prefix
        # parts[0] is '' (before first case), parts[1] is the body after first case
        # But split on the pattern means parts alternate: [before, after_match, ...]
        # Use a different approach: find the body of the first case
        first_match = cls._CASE_RE.match(state_str)
        if not first_match:
            return state_str
        body = state_str[first_match.end():]
        # If there's a second 'case ...\n', truncate before it
        second = cls._CASE_RE.search(body)
        if second:
            body = body[:second.start()].rstrip('\n')
        return body

    @staticmethod
    def _is_degenerate(tactic: str) -> bool:
        """Reject obviously bad tactic strings."""
        if not tactic:
            return True
        if "sorry" in tactic:
            return True
        if len(tactic) > 200:
            return True
        # Count nesting depth (parens + brackets)
        depth = 0
        max_depth = 0
        for c in tactic:
            if c in "([":
                depth += 1
                max_depth = max(max_depth, depth)
            elif c in ")]":
                depth -= 1
        if max_depth > 3:
            return True
        return False

    @staticmethod
    def _normalize_tactic(tactic: str) -> str:
        """Normalize tactic string for near-duplicate detection.

        Collapses whitespace and removes trivial variations so that
        'exact xs.map (fun x => x + 1)' and 'exact  xs.map (fun x =>  x + 1)'
        hash to the same key.
        """
        import re
        # Collapse all whitespace runs to single space
        s = re.sub(r'\s+', ' ', tactic.strip())
        # Normalize 'fun x=>' to 'fun x =>'
        s = re.sub(r'(\w)=>', r'\1 =>', s)
        return s

    def propose_tactics(self, state: object, k: int = None) -> list[tuple[str, float]]:
        """Generate k candidate tactics with log-probabilities.

        Uses two-temperature sampling:
          - T=0.8 (k/2 candidates): focused, high-quality
          - T=1.4 (k/2 candidates): diverse, exploratory

        If k is None, auto-selects: 64 for product types (×), 32 otherwise.
        """
        import torch

        state_str = state.pp if hasattr(state, "pp") else str(state)

        # Strip case prefix for model input (model sees cleaner sub-goal)
        model_state_str = self._strip_case_prefix(state_str)

        # Auto-select candidate count: product types need more diversity
        if k is None:
            k = 64 if "×" in state_str else 32

        if state_str in self._cache:
            return self._cache[state_str][:k]

        prompt = f"{self.STATE_PREFIX}{model_state_str}{self.TACTIC_PREFIX}"
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)
        prompt_len = inputs.input_ids.shape[1]

        tactics_with_scores: list[tuple[str, float]] = []
        seen_norm: set[str] = set()  # normalized forms for near-dedup

        for temp, n in [(0.8, k // 2), (1.4, k - k // 2)]:
            if n <= 0:
                continue
            with torch.no_grad():
                outputs = self.model.generate(
                    **inputs,
                    max_new_tokens=self.max_new_tokens,
                    num_return_sequences=n,
                    do_sample=True,
                    temperature=temp,
                    top_p=0.95,
                    pad_token_id=self.tokenizer.eos_token_id,
                    return_dict_in_generate=True,
                    output_scores=True,
                )

            sequences = outputs.sequences
            scores = outputs.scores

            for seq_idx in range(sequences.shape[0]):
                generated = sequences[seq_idx, prompt_len:]
                text = self.tokenizer.decode(generated, skip_special_tokens=True)
                tactic = text.strip().split("\n")[0].strip()

                if not tactic:
                    continue
                norm = self._normalize_tactic(tactic)
                if norm in seen_norm:
                    continue
                if self._is_degenerate(tactic):
                    continue
                seen_norm.add(norm)

                # Sum log-probs for generated tokens
                log_prob = 0.0
                for t, score in enumerate(scores):
                    if t >= len(generated):
                        break
                    token_id = generated[t].item()
                    if token_id == self.tokenizer.eos_token_id:
                        break
                    lp = torch.log_softmax(score[seq_idx], dim=-1)
                    log_prob += lp[token_id].item()

                tactics_with_scores.append((tactic, log_prob))

        tactics_with_scores.sort(key=lambda x: x[1], reverse=True)
        self._cache[state_str] = tactics_with_scores

        return tactics_with_scores[:k]

    def __call__(self, state: object, k: int = None) -> list[tuple[str, float]]:
        return self.propose_tactics(state, k)


class HybridGoalConditionedPolicy:
    """
    GoalConditioned + ExpandedFixed safety net.

    GoalConditioned candidates come first with their log-probs.
    ExpandedFixed tactics are appended with a low prior (min_lp - 2.0),
    deduplicated against GoalConditioned outputs.

    This ensures:
      - Novel tactics get explored first (higher prior)
      - Known-good tactics always available (prevents regression)
      - Same interface as SynthesisPolicy (MCTS unchanged)
    """

    def __init__(
        self,
        base_model: str,
        adapter_path: str,
        expressions_path: str = None,
        device: str = "cuda",
        lora_k: int = 64,
    ):
        self.gc = GoalConditionedPolicy(
            base_model, adapter_path, device=device, lora_k=lora_k,
        )
        self.fixed = ExpandedFixedPolicy(expressions_path=expressions_path)
        self.lora_k = lora_k

    def propose_tactics(self, state: object, k: int = None) -> list[tuple[str, float]]:
        """Return (tactic, log_prob) pairs: GoalConditioned first, ExpandedFixed after.

        For product-type goals (containing ×), boosts `constructor` to median
        GoalConditioned log-prob so MCTS explores it within ~5 rollouts.
        """
        gc_results = self.gc.propose_tactics(state, k=self.lora_k)
        seen_norm = {GoalConditionedPolicy._normalize_tactic(t) for t, _ in gc_results}

        # Boost constructor for product-type goals
        # Use FIRST ⊢ (focused goal), not last — critical for depth-3
        # where combined state has `⊢ Int × Nat\ncase snd\n...\n⊢ Int`
        state_str = state.pp if hasattr(state, "pp") else str(state)
        first_goal = ""
        if "⊢" in state_str:
            # Extract the focused (first) goal type
            first_turnstile = state_str.index("⊢")
            after = state_str[first_turnstile + 1:]
            # Goal ends at next 'case' line or end of string
            lines = after.split("\n")
            first_goal = lines[0].strip()
        if "×" in first_goal:
            # Insert constructor at median log-prob (not min_lp - 2.0)
            if gc_results:
                median_lp = gc_results[len(gc_results) // 2][1]
            else:
                median_lp = -5.0
            norm_ctor = GoalConditionedPolicy._normalize_tactic("constructor")
            if norm_ctor not in seen_norm:
                gc_results.insert(0, ("constructor", median_lp))
                seen_norm.add(norm_ctor)

        # ExpandedFixed safety net with low prior
        fixed_tactics = self.fixed.propose_tactics(state)
        min_lp = min((lp for _, lp in gc_results), default=-10.0) - 2.0
        for tactic in fixed_tactics:
            norm = GoalConditionedPolicy._normalize_tactic(tactic)
            if norm not in seen_norm:
                gc_results.append((tactic, min_lp))
                seen_norm.add(norm)

        if k is not None and k < len(gc_results):
            return gc_results[:k]
        return gc_results

    def __call__(self, state: object, k: int = None) -> list[tuple[str, float]]:
        return self.propose_tactics(state, k)


class CompositionalPolicy:
    """Propose tactics by composing fragments against task test cases.

    Pre-computes compositional candidates for each task via set_task(),
    then returns them on every propose_tactics() call. No GPU needed.

    Sprint 22: compositional fragment synthesis.
    """

    def __init__(
        self,
        catalog=None,
        evaluator=None,
        expressions_path: str = None,
        max_depth: int = 2,
        daemon_client=None,
    ):
        from lean_mcts.synth.fragments import FragmentCatalog
        from lean_mcts.synth.partial_eval import PartialEvaluator
        from lean_mcts.synth.compose import CompositionSearch

        if catalog is None:
            catalog = FragmentCatalog(expressions_path=expressions_path)
        if evaluator is None:
            evaluator = PartialEvaluator(client=daemon_client)

        # Load fragment cache if available
        self._load_fragment_cache(catalog, evaluator)

        self.search_engine = CompositionSearch(catalog, evaluator)
        self.max_depth = max_depth
        self._task_cache: dict[str, list[str]] = {}  # task_name → tactics
        self._current_task_name: str | None = None

    @staticmethod
    def _load_fragment_cache(catalog, evaluator):
        """Load fragment_cache.json if it exists."""
        from pathlib import Path
        cache_path = Path(__file__).resolve().parent.parent / "synth" / "fragment_cache.json"
        if cache_path.exists():
            import json
            with open(cache_path) as f:
                cache = json.load(f)
            for tag, outputs in cache.items():
                catalog.extend_outputs(tag, outputs)
                frag = catalog.get_fragment(tag)
                if frag:
                    evaluator.seed_cache(frag.expr, outputs)

    def set_task(self, task):
        """Pre-compute compositional candidates for a task."""
        self._current_task_name = task.name
        if task.name not in self._task_cache:
            candidates = self.search_engine.search(
                task, max_depth=self.max_depth,
            )
            self._task_cache[task.name] = [c.tactic for c in candidates]

    def propose_tactics(self, state: object, k: int = None) -> list[str]:
        """Return pre-computed compositional tactics for the current task."""
        all_tactics = self._task_cache.get(self._current_task_name, [])

        if not all_tactics:
            return []
        if k is not None and k < len(all_tactics):
            return all_tactics[:k]
        return all_tactics

    def propose_tactics_for_task(self, task_name: str, state: object, k: int = None) -> list[str]:
        """Return compositional tactics for a specific task."""
        tactics = self._task_cache.get(task_name, [])
        if k is not None and k < len(tactics):
            return tactics[:k]
        return tactics

    def __call__(self, state: object, k: int = None) -> list[str]:
        return self.propose_tactics(state, k)


class HybridCompositionalPolicy:
    """GoalConditioned + Compositional + ExpandedFixed fallback.

    Priority order:
    1. GoalConditioned: novel tactics with log-probs (highest)
    2. Compositional: spec-derived tactics (medium)
    3. ExpandedFixed: curated fallback (lowest)

    Sprint 22: replaces HybridGoalConditionedPolicy for benchmarking.
    """

    def __init__(
        self,
        base_model: str = None,
        adapter_path: str = None,
        expressions_path: str = None,
        device: str = "cuda",
        lora_k: int = 64,
        max_depth: int = 3,
        daemon_client=None,
    ):
        # GoalConditioned (generative, GPU)
        if base_model and adapter_path:
            self.gc = GoalConditionedPolicy(
                base_model, adapter_path, device=device, lora_k=lora_k,
            )
        else:
            self.gc = None

        # Compositional (spec-driven, no GPU)
        from lean_mcts.synth.fragments import FragmentCatalog
        from lean_mcts.synth.partial_eval import PartialEvaluator

        catalog = FragmentCatalog(expressions_path=expressions_path)
        evaluator = PartialEvaluator(client=daemon_client)
        self.comp = CompositionalPolicy(
            catalog=catalog, evaluator=evaluator,
            max_depth=max_depth,
        )

        # ExpandedFixed fallback
        self.fixed = ExpandedFixedPolicy(expressions_path=expressions_path)
        self.lora_k = lora_k
        self._current_task_name: str | None = None

    def set_task(self, task):
        """Pre-compute compositional candidates for a task."""
        self._current_task_name = task.name
        self.comp.set_task(task)

    def propose_tactics(self, state: object, k: int = None) -> list[tuple[str, float]]:
        """Return (tactic, log_prob) pairs from all three sources."""
        results: list[tuple[str, float]] = []
        seen_norm: set[str] = set()

        # 1. GoalConditioned (highest priority)
        if self.gc is not None:
            gc_results = self.gc.propose_tactics(state, k=self.lora_k)
            for tactic, lp in gc_results:
                norm = GoalConditionedPolicy._normalize_tactic(tactic)
                if norm not in seen_norm:
                    results.append((tactic, lp))
                    seen_norm.add(norm)

        # Constructor boost for product types
        state_str = state.pp if hasattr(state, "pp") else str(state)
        first_goal = ""
        if "⊢" in state_str:
            first_turnstile = state_str.index("⊢")
            after = state_str[first_turnstile + 1:]
            lines = after.split("\n")
            first_goal = lines[0].strip()
        if "×" in first_goal:
            if results:
                median_lp = results[len(results) // 2][1]
            else:
                median_lp = -5.0
            norm_ctor = GoalConditionedPolicy._normalize_tactic("constructor")
            if norm_ctor not in seen_norm:
                results.insert(0, ("constructor", median_lp))
                seen_norm.add(norm_ctor)

        # 2. Compositional candidates (medium priority)
        comp_tactics = self.comp.propose_tactics_for_task(
            self._current_task_name or "", state
        )
        if results:
            comp_lp = min(lp for _, lp in results) - 1.0
        else:
            comp_lp = -5.0
        for tactic in comp_tactics:
            norm = GoalConditionedPolicy._normalize_tactic(tactic)
            if norm not in seen_norm:
                results.append((tactic, comp_lp))
                seen_norm.add(norm)

        # 3. ExpandedFixed fallback (lowest priority)
        fixed_tactics = self.fixed.propose_tactics(state)
        min_lp = min((lp for _, lp in results), default=-10.0) - 2.0
        for tactic in fixed_tactics:
            norm = GoalConditionedPolicy._normalize_tactic(tactic)
            if norm not in seen_norm:
                results.append((tactic, min_lp))
                seen_norm.add(norm)

        if k is not None and k < len(results):
            return results[:k]
        return results

    def __call__(self, state: object, k: int = None) -> list[tuple[str, float]]:
        return self.propose_tactics(state, k)


class OllamaPolicy:
    """
    Generative policy backed by a remote Ollama HTTP endpoint.

    Same interface contract as GoalConditionedPolicy:
      `propose_tactics(state, k) -> list[tuple[str, float]]`

    One Ollama /api/chat call per unique state; result is a JSON array
    of tactics parsed out of the assistant message. Tactics are
    filtered through the same `_is_degenerate` / `_normalize_tactic`
    helpers GoalConditionedPolicy uses, and assigned RANK-based
    pseudo-log-probs (position 0 → 0.0, decreasing linearly) so PUCT
    still gets a usable ordering signal.

    Reuses GoalConditionedPolicy's static helpers for case-prefix
    stripping, degeneracy filtering, and near-duplicate normalization.
    Results are cached per state string (Ollama calls are ~3-10 s each,
    so cache hits matter for MCTS re-expansion).

    Sprint 30: used to probe whether an open-weight ~26B model wired
    into the full oracle + MCTS + grammar harness does something
    meaningfully different from that same model answered one-shot.
    """

    # Same 5 curated pairs phase_a_fewshot.json uses — diverse structural
    # patterns (let_rec, match_list, foldl_accumulator, method_chain,
    # two_list_filter). Frozen inline so MCTS runs don't need the file.
    _FEWSHOT_PAIRS = [
        {
            "kind": "let_rec, from List.partition",
            "state": "p : \u2124 \u2192 Bool\nas : List \u2124\n\u22a2 List \u2124 \u00d7 List \u2124",
            "tactic": "exact let rec loop : List Int \u2192 List Int \u00d7 List Int \u2192 List Int \u00d7 List Int | [], (bs, cs) => (bs.reverse, cs.reverse) | a::as, (bs, cs) => match p a with | true => loop as (a::bs, cs) | false => loop as (bs, a::cs); loop as ([], [])",
        },
        {
            "kind": "match_list, from List.tail",
            "state": "x_arg : List \u2124\n\u22a2 List \u2124",
            "tactic": "exact match x_arg with | [] => [] | _::as => as",
        },
        {
            "kind": "foldl_accumulator, from List.toArrayMap",
            "state": "l : List \u2124\nf : \u2124 \u2192 \u2124\n\u22a2 Array \u2124",
            "tactic": "exact l.foldl (init := #[]) fun acc x => acc.push (f x)",
        },
        {
            "kind": "method_chain, from List.dropLastTR",
            "state": "l : List \u2124\n\u22a2 List \u2124",
            "tactic": "exact l.toArray.pop.toList",
        },
        {
            "kind": "two_list_filter, from List.removeAll",
            "state": "xs : List \u2124\nys : List \u2124\n\u22a2 List \u2124",
            "tactic": "exact xs.filter (fun x => !ys.elem x)",
        },
    ]

    _SYSTEM_HEADER = (
        "You are an expert Lean 4 programmer. Given a Lean 4 tactic goal "
        "state (hypotheses followed by `\u22a2 <goal>`), propose a JSON "
        "array of candidate tactic bodies. Each candidate must start with "
        "`exact` and be a complete Lean 4 term. No commentary, no markdown "
        "fences. Output ONLY the JSON array."
    )

    _SYSTEM_SYNTAX_RULES = (
        "Lean 4 syntax rules (avoid Lean 3 / hallucinated idioms):\n"
        "  - Local bindings: `let name := ...; rest`. NEVER use `let ... in ...`.\n"
        "  - Slicing: use `(xs.drop i).take k` or `xs.drop i |>.take k`. "
        "There is NO `List.sublist`.\n"
        "  - Tuple field access uses `.1` and `.2` (NOT `.0`/`.1`).\n"
        "  - Multi-arg `let rec` patterns separate args with COMMAS: "
        "`let rec f : A \u2192 B \u2192 C | [], _ => ... | x :: xs, n => ...; f xs 0`.\n"
        "  - When using `foldl` with a tuple accumulator, access fields "
        "AFTER the fold: `(xs.foldl step init).1`, NOT inside the lambda.\n"
        "  - Method chains: wrap sub-expressions in parens. "
        "`(xs.zip ys).map f`, not `xs.zip ys.map f`.\n"
        "  - Indexed iteration: `xs.mapIdx (fun i x => ...)` or "
        "`xs.zipIdx`. No `enumerate`, no `List.indexOf`.\n"
        "  - No placeholders, no `sorry`, no `admit`, no trailing comments."
    )

    @classmethod
    def _build_default_system(cls) -> str:
        """System prompt = header + few-shot examples + syntax rules.

        The few-shot examples are the same 5 curated pairs phase_a_ollama
        uses in its one-shot probe; embedding them in the system prompt
        recovers the ~5/12 → 10/12 quality jump when wiring gemma4 into
        the MCTS policy.
        """
        parts = [cls._SYSTEM_HEADER, "", "Here are 5 example goal/tactic pairs covering distinct structural patterns:"]
        for i, p in enumerate(cls._FEWSHOT_PAIRS, 1):
            parts.append("")
            parts.append(f"Example {i} ({p['kind']}):")
            parts.append("Goal state:")
            parts.append(p["state"])
            parts.append("Tactic:")
            parts.append(p["tactic"])
        parts.extend(["", cls._SYSTEM_SYNTAX_RULES])
        return "\n".join(parts)

    _DEFAULT_SYSTEM = None  # lazily initialized below the class body

    def __init__(
        self,
        url: str = DEFAULT_OLLAMA_URL,
        model: str = "gemma4:26b",
        max_new_tokens: int = 2048,
        k_default: int = 16,
        temperature: float = 0.3,
        timeout: int = 120,
        system_prompt: str | None = None,
        verify_tls: bool = False,
    ):
        self.url = url
        self.model = model
        self.max_new_tokens = max_new_tokens
        self.k_default = k_default
        self.temperature = temperature
        self.timeout = timeout
        # Lazily construct the system prompt once (class-level cache would
        # require a metaclass; a simple cached attribute suffices here).
        if system_prompt is None:
            if type(self)._DEFAULT_SYSTEM is None:
                type(self)._DEFAULT_SYSTEM = type(self)._build_default_system()
            system_prompt = type(self)._DEFAULT_SYSTEM
        self.system_prompt = system_prompt
        self.verify_tls = verify_tls

        self._cache: dict[str, list[tuple[str, float]]] = {}
        self._call_count = 0
        self._cache_hits = 0

        # Task context (set via set_task() by the benchmark runner).
        # The task description materially changes what tactics the model
        # proposes — without it, Qwen / Gemma / etc. can only see a bare
        # goal state like `xs : List Int ⊢ Int` and will generate generic
        # sum/length tactics, missing task-specific structure.
        self._task_name: str | None = None
        self._task_description: str | None = None
        self._task_test_cases: list[dict] | None = None

    def set_task(self, task) -> None:
        """Receive task context from the benchmark runner.

        Called once per task before MCTS begins. The task description and
        a small sample of test cases are threaded into the prompt so the
        policy has the same contextual access as the one-shot probe in
        `phase_a_ollama.py` — otherwise the policy sees only the bare goal
        state and can't distinguish `majority_element` from `sum`.
        """
        self._task_name = getattr(task, "name", None)
        self._task_description = getattr(task, "description", None)
        tc = getattr(task, "test_cases", None) or []
        self._task_test_cases = list(tc[:3])
        # Clear the cache when the task changes so prior state->tactics
        # entries don't leak across tasks.
        self._cache.clear()

    def _format_test_cases(self, task_param_names: list[str] | None = None) -> str:
        """Render the sampled test cases on separate lines."""
        if not self._task_test_cases:
            return ""
        keys = ["input", "input2", "input3", "input4"]
        names = task_param_names or ["xs", "ys", "k", "n"]
        lines = []
        for tc in self._task_test_cases:
            parts = []
            for name, key in zip(names, keys):
                if key in tc:
                    parts.append(f"{name}={tc[key]}")
            expected = tc.get("expected", "")
            if parts:
                lines.append("  " + "  ".join(parts) + f"  \u2192  " + expected)
        return "\n".join(lines)

    def _build_user_message(self, state_str: str, k: int) -> str:
        parts = []
        if self._task_name or self._task_description:
            parts.append("TASK CONTEXT")
            if self._task_name:
                parts.append(f"Name: {self._task_name}")
            if self._task_description:
                parts.append(f"Description: {self._task_description}")
            tc_block = self._format_test_cases()
            if tc_block:
                parts.append("Sample inputs:")
                parts.append(tc_block)
            parts.append("")
        parts.append("Goal state (current MCTS node):")
        parts.append(state_str)
        parts.append("")
        parts.append(
            f"Propose {k} structurally diverse candidate tactic bodies. "
            "Try different approaches where sensible (let_rec, match, foldl, "
            "foldr, method chain, have/exact). Each candidate MUST start with "
            "`exact` and be a complete Lean 4 term. Do NOT use `sorry` or `admit`."
        )
        parts.append("")
        parts.append(
            "OUTPUT: a JSON array of strings. No markdown fences, no commentary — "
            "just the raw JSON array."
        )
        return "\n".join(parts)

    def _ollama_call(self, state_str: str, k: int) -> list[str]:
        """One Ollama /api/chat call. Returns the parsed JSON-array tactics."""
        import json as _json
        import ssl as _ssl
        import urllib.request as _urllib

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": self._build_user_message(state_str, k)},
            ],
            "stream": False,
            "think": False,  # critical: reasoning models (gemma4 etc.) otherwise spend num_predict in thinking
            # keep_alive: -1 prevents Ollama from unloading the model between
            # requests. Without this, each MCTS expansion pays a full cold-load
            # penalty (~31s for gemma4:26b from disk), making the full harness
            # impractically slow. Observed during S30 functional-machine probe.
            "keep_alive": -1,
            "options": {
                "temperature": self.temperature,
                "top_p": 0.95,
                "num_predict": self.max_new_tokens,
            },
        }
        data = _json.dumps(body).encode("utf-8")
        req = _urllib.Request(
            self.url, data=data,
            headers={"Content-Type": "application/json"},
        )
        if self.verify_tls:
            ctx = _ssl.create_default_context()
        else:
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
        with _urllib.urlopen(req, timeout=self.timeout, context=ctx) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))

        content = ((payload.get("message") or {}).get("content") or "")
        # Fall back to thinking if reasoning leaked into thinking only
        if not content:
            content = ((payload.get("message") or {}).get("thinking") or "")

        return self._extract_json_array(content)

    @staticmethod
    def _extract_json_array(text: str) -> list[str]:
        """Pull the largest top-level `[...]` string array out of `text`."""
        import html as _html
        import json as _json

        if not text:
            return []
        text = _html.unescape(text)
        depth = 0
        start = -1
        candidates = []
        for i, ch in enumerate(text):
            if ch == "[":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0 and start != -1:
                    candidates.append(text[start:i + 1])
                    start = -1
        for cand in sorted(candidates, key=len, reverse=True):
            try:
                arr = _json.loads(cand)
                if isinstance(arr, list) and all(isinstance(x, str) for x in arr):
                    return arr
            except Exception:
                continue
        return []

    def propose_tactics(self, state: object, k: int = None) -> list[tuple[str, float]]:
        """Propose k candidate tactics for a state. Returns (tactic, log_prob)."""
        k = k or self.k_default

        state_str = state.pp if hasattr(state, "pp") else str(state)
        model_state_str = GoalConditionedPolicy._strip_case_prefix(state_str)

        if state_str in self._cache:
            self._cache_hits += 1
            return self._cache[state_str][:k]

        self._call_count += 1
        try:
            tactics_raw = self._ollama_call(model_state_str, k)
        except Exception as e:
            # On Ollama failure, return empty and let the caller fall back.
            # The safety-net / hybrid wrapper still provides coverage.
            print(f"[OllamaPolicy] call failed: {e}")
            self._cache[state_str] = []
            return []

        # Filter + dedupe through the same pipeline as GoalConditionedPolicy
        tactics_with_scores: list[tuple[str, float]] = []
        seen_norm: set[str] = set()
        for tactic in tactics_raw:
            tactic = tactic.strip()
            if not tactic:
                continue
            if GoalConditionedPolicy._is_degenerate(tactic):
                continue
            norm = GoalConditionedPolicy._normalize_tactic(tactic)
            if norm in seen_norm:
                continue
            seen_norm.add(norm)
            # Rank-based pseudo log-prob: position 0 = 0.0, decreasing.
            rank = len(tactics_with_scores)
            log_prob = -0.5 * rank  # mildly decaying prior
            tactics_with_scores.append((tactic, log_prob))

        self._cache[state_str] = tactics_with_scores
        return tactics_with_scores[:k]

    def __call__(self, state: object, k: int = None) -> list[tuple[str, float]]:
        return self.propose_tactics(state, k)


class HybridOllamaPolicy:
    """
    OllamaPolicy + ExpandedFixed safety net.

    Same structural pattern as HybridGoalConditionedPolicy: the generative
    policy proposes novel tactics first, and the hand-crafted ExpandedFixed
    table backstops with low prior. This is the apples-to-apples comparison
    harness: swap the LoRA+base generative component for a remote Ollama
    endpoint, keep everything else (ExpandedFixed safety net, constructor
    boost on product types, oracle-verified MCTS rollouts) identical.
    """

    def __init__(
        self,
        url: str = DEFAULT_OLLAMA_URL,
        model: str = "gemma4:26b",
        expressions_path: str | None = None,
        max_new_tokens: int = 2048,
        k_default: int = 16,
        temperature: float = 0.3,
        timeout: int = 120,
        verify_tls: bool = False,
    ):
        self.gc = OllamaPolicy(
            url=url, model=model,
            max_new_tokens=max_new_tokens, k_default=k_default,
            temperature=temperature, timeout=timeout, verify_tls=verify_tls,
        )
        self.fixed = ExpandedFixedPolicy(expressions_path=expressions_path)
        self.k_default = k_default

    def set_task(self, task) -> None:
        """Forward task context to the Ollama component."""
        self.gc.set_task(task)

    def propose_tactics(self, state: object, k: int = None) -> list[tuple[str, float]]:
        """Ollama candidates first with rank-based log-probs, ExpandedFixed after."""
        gc_results = self.gc.propose_tactics(state, k=self.k_default)
        seen_norm = {GoalConditionedPolicy._normalize_tactic(t) for t, _ in gc_results}

        # Constructor boost for product-type goals (same as HybridGC)
        state_str = state.pp if hasattr(state, "pp") else str(state)
        first_goal = ""
        if "\u22a2" in state_str:
            first_turnstile = state_str.index("\u22a2")
            after = state_str[first_turnstile + 1:]
            lines = after.split("\n")
            first_goal = lines[0].strip()
        if "\u00d7" in first_goal:
            if gc_results:
                median_lp = gc_results[len(gc_results) // 2][1]
            else:
                median_lp = -5.0
            norm_ctor = GoalConditionedPolicy._normalize_tactic("constructor")
            if norm_ctor not in seen_norm:
                gc_results.insert(0, ("constructor", median_lp))
                seen_norm.add(norm_ctor)

        # ExpandedFixed safety net with low prior
        fixed_tactics = self.fixed.propose_tactics(state)
        min_lp = min((lp for _, lp in gc_results), default=-10.0) - 2.0
        for tactic in fixed_tactics:
            norm = GoalConditionedPolicy._normalize_tactic(tactic)
            if norm not in seen_norm:
                gc_results.append((tactic, min_lp))
                seen_norm.add(norm)

        if k is not None and k < len(gc_results):
            return gc_results[:k]
        return gc_results

    def __call__(self, state: object, k: int = None) -> list[tuple[str, float]]:
        return self.propose_tactics(state, k)
