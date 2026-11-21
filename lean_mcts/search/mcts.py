"""
Monte Carlo Tree Search for Lean 4 tactic-based program synthesis.

Each node in the search tree corresponds to a Lean tactic state.
Each edge corresponds to a tactic that transforms one state into another.
Lean's type checker provides per-step semantic validation: invalid tactics
are rejected immediately and never expanded.

The optional oracle_fn adds semantic verification: when a tactic sequence
produces ProofFinished (type-correct), the oracle evaluates it against test
cases. Only solutions that pass all tests get reward 1.0.

Supports two selection strategies:
  - UCT (Sprint 11): Q/N + c * sqrt(ln(N_parent) / N)
  - PUCT (Sprint 12): Q/N + c * P(a|s) * sqrt(N_parent) / (1 + N)

Usage:
    from lean_mcts.search.mcts import search
    from lean_mcts.env.dojo_wrapper import LeanEnv
    from lean_mcts.search.policy import FixedPolicy

    env = LeanEnv(repo_path, file_path, theorem_name)
    policy = FixedPolicy()
    result = search(env, n_rollouts=500, policy_fn=policy)
"""

from __future__ import annotations

import itertools
import math
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from lean_mcts.search.goal_analyzer import analyze_goal, parse_sub_goals
from lean_mcts.search.recursion_candidates import generate_recursion_candidates
from lean_mcts.search.foldl_candidates import generate_foldl_candidates
from lean_mcts.search.twopass_candidates import generate_twopass_candidates
from lean_mcts.search.composition_candidates import generate_composition_candidates


@dataclass
class SearchStats:
    """Per-search timing breakdown for Phase 0 instrumentation."""
    n_expand_calls: int = 0
    n_tactic_steps: int = 0        # total env.step() calls
    n_tactic_valid: int = 0        # tactics accepted by Lean
    n_tactic_solved: int = 0       # tactics that produced ProofFinished
    n_oracle_calls: int = 0
    n_oracle_passed: int = 0
    n_oracle_prefiltered: int = 0  # candidates skipped by heuristic pre-filter
    n_oracle_batches: int = 0      # number of batched oracle calls
    n_simulate_calls: int = 0
    n_repl_crashes: int = 0        # EOFError / OSError count
    t_policy_s: float = 0.0        # cumulative policy inference time
    t_step_s: float = 0.0          # cumulative env.step() time
    t_oracle_s: float = 0.0        # cumulative oracle verification time
    t_simulate_s: float = 0.0      # cumulative simulation time (policy + step)
    t_select_s: float = 0.0        # cumulative select + backprop time

    def summary(self) -> dict:
        """Return a JSON-serializable summary."""
        total = self.t_policy_s + self.t_step_s + self.t_oracle_s + self.t_simulate_s + self.t_select_s
        return {
            "n_expand_calls": self.n_expand_calls,
            "n_tactic_steps": self.n_tactic_steps,
            "n_tactic_valid": self.n_tactic_valid,
            "n_tactic_solved": self.n_tactic_solved,
            "n_oracle_calls": self.n_oracle_calls,
            "n_oracle_passed": self.n_oracle_passed,
            "n_oracle_prefiltered": self.n_oracle_prefiltered,
            "n_oracle_batches": self.n_oracle_batches,
            "n_simulate_calls": self.n_simulate_calls,
            "n_repl_crashes": self.n_repl_crashes,
            "t_policy_s": round(self.t_policy_s, 4),
            "t_step_s": round(self.t_step_s, 4),
            "t_oracle_s": round(self.t_oracle_s, 4),
            "t_simulate_s": round(self.t_simulate_s, 4),
            "t_select_s": round(self.t_select_s, 4),
            "t_total_s": round(total, 4),
            "ms_per_step": round(self.t_step_s / self.n_tactic_steps * 1000, 2) if self.n_tactic_steps else 0,
            "ms_per_oracle": round(self.t_oracle_s / self.n_oracle_calls * 1000, 1) if self.n_oracle_calls else 0,
            "pct_step": round(self.t_step_s / total * 100, 1) if total else 0,
            "pct_policy": round(self.t_policy_s / total * 100, 1) if total else 0,
            "pct_oracle": round(self.t_oracle_s / total * 100, 1) if total else 0,
            "pct_simulate": round(self.t_simulate_s / total * 100, 1) if total else 0,
        }


@dataclass
class Node:
    """A node in the MCTS search tree."""
    state: object                              # TacticState from LeanDojo
    tactic: Optional[str] = None               # Tactic that led to this node (None for root)
    parent: Optional['Node'] = None
    children: list['Node'] = field(default_factory=list)
    visits: int = 0
    value: float = 0.0                         # Cumulative reward
    untried_tactics: list[str] = field(default_factory=list)
    is_terminal: bool = False                  # True if ProofFinished or dead end
    is_solved: bool = False                    # True if ProofFinished AND oracle-verified
    prior: float = 0.0                         # Policy log-prob for PUCT (0 = uniform)

    @property
    def avg_value(self) -> float:
        return self.value / self.visits if self.visits > 0 else 0.0


def _extract_tactic_path(node: Node) -> list[str]:
    """Extract the tactic sequence from root to this node."""
    path = []
    current = node
    while current is not None:
        if current.tactic is not None:
            path.append(current.tactic)
        current = current.parent
    return list(reversed(path))


def ucb_score(node: Node, parent_visits: int, c: float = 1.414) -> float:
    """Upper Confidence Bound for Trees (UCT) score."""
    if node.visits == 0:
        return float('inf')  # Always explore unvisited nodes first
    exploitation = node.avg_value
    exploration = c * math.sqrt(math.log(parent_visits) / node.visits)
    return exploitation + exploration


def puct_score(node: Node, parent_visits: int, c: float = 2.5) -> float:
    """
    PUCT score (AlphaZero-style): Q + c * P * sqrt(N_parent) / (1 + N).

    Uses policy prior probability to focus exploration on high-probability
    children rather than uniform exploration. The prior is converted from
    log-prob to probability via softmax normalization across siblings.
    """
    exploitation = node.avg_value
    exploration = c * node.prior * math.sqrt(parent_visits) / (1 + node.visits)
    return exploitation + exploration


def select(node: Node, c: float = 1.414, use_puct: bool = False) -> Node:
    """Walk down the tree picking the child with the highest score."""
    score_fn = puct_score if use_puct else ucb_score
    while node.children and not node.is_terminal:
        if node.untried_tactics:
            return node
        node = max(node.children, key=lambda ch: score_fn(ch, node.visits, c))
    return node


import re as _re

def _extract_param_names_from_state(state_str: str) -> list[str]:
    """Extract parameter names from a goal state string.

    Goal state looks like:
        xs : List ℤ
        ⊢ ℕ
    or:
        xss : List (List ℤ)
        ⊢ List ℤ
    or:
        n : ℕ
        ⊢ ℕ

    Returns list of parameter names (before the first ⊢ line).
    """
    params = []
    for line in state_str.split("\n"):
        line = line.strip()
        if line.startswith("⊢") or line.startswith("|"):
            break
        # Match "name : Type" pattern
        m = _re.match(r'^(\w+)\s*:', line)
        if m:
            params.append(m.group(1))
    return params


def _prefilter_candidates(
    solved_candidates: list[tuple],
    state_str: str,
) -> tuple[list[tuple], list[tuple]]:
    """Split candidates into (plausible, filtered_out) based on heuristics.

    A candidate is filtered if its tactic doesn't reference ANY input parameter.
    This catches constant expressions like `exact []`, `exact 0`, `exact ([], [])`.
    """
    param_names = _extract_param_names_from_state(state_str)
    if not param_names:
        # No params to check against — can't filter
        return solved_candidates, []

    plausible = []
    filtered = []
    for candidate in solved_candidates:
        tactic = candidate[0]  # (tactic, log_prob, path)
        # Check if the tactic body (after "exact ") references any param
        if any(name in tactic for name in param_names):
            plausible.append(candidate)
        else:
            filtered.append(candidate)

    return plausible, filtered


# Maximum candidates per batched oracle call
_ORACLE_BATCH_SIZE = 50


class _MockState:
    """Lightweight state for sub-goal policy queries during skeleton expansion."""
    __slots__ = ('pp',)

    def __init__(self, state_str: str):
        self.pp = state_str

    def __str__(self):
        return self.pp


# Pantograph uses Unicode types (ℤ, ℕ) but expressions.jsonl and training
# data use ASCII (Int, Nat). Normalize for ExpandedFixed type matching.
_UNICODE_TYPE_MAP = {
    'ℤ': 'Int',
    'ℕ': 'Nat',
    'List ℤ': 'List Int',
    'List ℕ': 'List Nat',
}


def _normalize_state_types(state_str: str) -> str:
    """Replace Unicode Lean types with ASCII equivalents in a state string."""
    for unicode, ascii in _UNICODE_TYPE_MAP.items():
        state_str = state_str.replace(unicode, ascii)
    return state_str


def greedy_combinations(
    candidates_per_goal: list[list[tuple[str, float]]],
    max_attempts: int = 64,
):
    """Generate candidate combinations in priority order.

    Strategy:
    1. Best-of-each: top-1 candidate per sub-goal (1 combo)
    2. One-hot swap: for each sub-goal, try alternatives while others stay
       at top-1 (isolates which sub-goal is failing)
    3. Beam: top-b per sub-goal (covers interactions between sub-goals)
    """
    n_goals = len(candidates_per_goal)
    if not all(candidates_per_goal):
        return

    seen: set[tuple[str, ...]] = set()
    count = 0

    def _key(combo):
        return tuple(t for t, _ in combo)

    # 1. Best-of-each
    best = tuple(cands[0] for cands in candidates_per_goal)
    seen.add(_key(best))
    yield best
    count += 1

    # 2. One-hot swap
    for goal_idx in range(n_goals):
        for alt_idx in range(1, len(candidates_per_goal[goal_idx])):
            if count >= max_attempts:
                return
            combo = list(best)
            combo[goal_idx] = candidates_per_goal[goal_idx][alt_idx]
            combo = tuple(combo)
            k = _key(combo)
            if k not in seen:
                seen.add(k)
                yield combo
                count += 1

    # 3. Beam: top-b per sub-goal (b=10 covers depth-3 products well)
    beam = min(10, max(len(c) for c in candidates_per_goal))
    top_b = [cands[:beam] for cands in candidates_per_goal]
    for combo in itertools.product(*top_b):
        if count >= max_attempts:
            return
        k = _key(combo)
        if k not in seen:
            seen.add(k)
            yield combo
            count += 1


def _build_solved_chain(
    node: Node, env, tactic_sequence: list[str],
    constructor_states: list, stats: Optional[SearchStats],
) -> Node:
    """Build chain of MCTS nodes for a solved skeleton proof.

    Creates: node → constructor₁ → constructor₂ → exact₁ → exact₂ → exact₃ (solved)
    Returns the solved leaf node (for backpropagation).
    """
    prev = node
    n_constructors = len(constructor_states) - 1

    # Constructor nodes (states already computed)
    for i in range(n_constructors):
        child = Node(
            state=constructor_states[i + 1],
            tactic="constructor",
            parent=prev,
        )
        child.visits = 1
        child.value = 1.0
        prev.children.append(child)
        prev = child

    # Exact nodes — step through to get intermediate states
    state = constructor_states[-1]
    exact_tactics = tactic_sequence[n_constructors:]

    for i, tactic in enumerate(exact_tactics):
        is_last = (i == len(exact_tactics) - 1)

        try:
            t0 = time.perf_counter()
            result = env.step(state, tactic)
            if stats:
                stats.t_step_s += time.perf_counter() - t0
                stats.n_tactic_steps += 1
        except (EOFError, OSError):
            # Oracle already verified — create solved node anyway
            result = type('R', (), {'solved': True, 'state': None})()

        if result.solved or is_last:
            child = Node(
                state=None, tactic=tactic, parent=prev,
                is_terminal=True, is_solved=True,
            )
        else:
            child = Node(state=result.state, tactic=tactic, parent=prev)
            state = result.state

        child.visits = 1
        child.value = 1.0
        prev.children.append(child)
        prev = child

    return prev


def _make_constructor_fallback(node: Node, constructor_states: list) -> Node:
    """Create constructor chain nodes as fallback for standard MCTS continuation.

    When skeleton expansion can't find a valid exact combination,
    the constructor chain is still valuable — MCTS continues from
    the post-constructor state instead of rediscovering constructors.
    """
    prev = node
    for i in range(1, len(constructor_states)):
        child = Node(
            state=constructor_states[i],
            tactic="constructor",
            parent=prev,
        )
        prev.children.append(child)
        prev = child
    return prev


def _expand_with_skeleton(
    node: Node, env, policy_fn: Callable, oracle_fn: Callable,
    stats: Optional[SearchStats], verbose: bool,
) -> Optional[Node]:
    """Skeleton-guided macro expansion for product-type goals.

    Instead of single-tactic expansion:
    1. Analyze goal type → deterministic constructor skeleton
    2. Execute constructor chain atomically
    3. Parse resulting sub-goals
    4. Get policy candidates for each sub-goal independently
    5. Type-check candidates per sub-goal
    6. Try greedy combinations with oracle verification
    7. Fallback: create constructor chain node for standard MCTS

    Returns solved node, constructor fallback node, or None (can't constructor).
    """
    state_str = node.state.pp if hasattr(node.state, 'pp') else str(node.state)
    structure = analyze_goal(state_str)

    if not structure.is_product or structure.constructor_count < 1:
        return None

    if verbose:
        print(f"  Skeleton: detected {structure.raw_type} "
              f"(depth={structure.product_depth}, leaves={structure.leaf_types})")

    # Phase A: Execute constructor chain
    constructor_states = [node.state]
    for i in range(structure.constructor_count):
        try:
            t0 = time.perf_counter()
            result = env.step(constructor_states[-1], "constructor")
            if stats:
                stats.t_step_s += time.perf_counter() - t0
                stats.n_tactic_steps += 1
        except (EOFError, OSError):
            if stats:
                stats.n_repl_crashes += 1
            return None

        if result.state is None:
            if verbose:
                print(f"  Skeleton: constructor {i+1} failed, falling back")
            return None
        if result.solved:
            # constructor alone solved the goal (shouldn't happen for products)
            child = Node(
                state=None, tactic="constructor", parent=node,
                is_terminal=True, is_solved=True,
            )
            node.children.append(child)
            return child

        constructor_states.append(result.state)

    post_constructor_state = constructor_states[-1]
    post_str = (post_constructor_state.pp
                if hasattr(post_constructor_state, 'pp')
                else str(post_constructor_state))

    # Phase B: Parse sub-goals from post-constructor state
    sub_goal_strs = parse_sub_goals(post_str)
    n_sub_goals = len(structure.leaf_types)

    if len(sub_goal_strs) != n_sub_goals:
        if verbose:
            print(f"  Skeleton: expected {n_sub_goals} sub-goals, "
                  f"got {len(sub_goal_strs)}, falling back")
        return _make_constructor_fallback(node, constructor_states)

    # Phase C: Get and type-check candidates for each sub-goal
    candidates_per_goal: list[list[tuple[str, float]]] = []
    current_state = post_constructor_state

    for i in range(n_sub_goals):
        # Normalize Unicode types (ℤ→Int, ℕ→Nat) so ExpandedFixed
        # can properly filter by return type
        normalized_sg = _normalize_state_types(sub_goal_strs[i])
        mock = _MockState(normalized_sg)

        # Get ExpandedFixed candidates FIRST (reliable for atomic types).
        # These are the simple exact expressions (fold, length, filter, etc.)
        # that form the correct answers for product sub-goals.
        ef_policy = getattr(policy_fn, 'fixed', None)
        ef_candidates: list[tuple[str, float]] = []
        if ef_policy is not None:
            ef_raw = ef_policy.propose_tactics(mock)
            # EF returns plain strings. For skeleton sub-goals:
            # 1. Only keep "exact ..." tactics (proof tactics useless here)
            # 2. Sort by length — simpler expressions first (foldl min 0
            #    before filter+foldl+map chains). This ensures beam covers
            #    the correct simple exacts for depth-3 products.
            ef_exact = [t for t in ef_raw if t.startswith("exact ")]
            ef_exact.sort(key=len)
            ef_candidates = [(t, 0.0) for t in ef_exact]

        # Get policy candidates (GC+EF hybrid) for additional coverage
        t0 = time.perf_counter()
        raw = policy_fn(mock)
        if stats:
            stats.t_policy_s += time.perf_counter() - t0

        if raw and isinstance(raw[0], tuple):
            policy_candidates = list(raw)
        else:
            policy_candidates = [(t, 0.0) for t in (raw or [])]

        # Merge: EF first (reliable), then policy (for novel candidates).
        # Deduplicate by tactic string.
        seen_tactics: set[str] = set()
        candidates: list[tuple[str, float]] = []
        for t, lp in ef_candidates:
            if t not in seen_tactics:
                seen_tactics.add(t)
                candidates.append((t, lp))
        for t, lp in policy_candidates:
            if t not in seen_tactics:
                seen_tactics.add(t)
                candidates.append((t, lp))

        # Pre-filter: skip candidates that don't reference input params
        # (catches constants like "exact 0", "exact []" that type-check
        # but always produce wrong output)
        param_names = _extract_param_names_from_state(sub_goal_strs[i])
        if param_names:
            candidates = [
                (t, lp) for t, lp in candidates
                if any(name in t for name in param_names)
            ]

        # Type-check against actual Lean state (focused on sub-goal i)
        valid: list[tuple[str, float]] = []
        advance_state = None
        for tactic, log_prob in candidates:
            try:
                t0 = time.perf_counter()
                result = env.step(current_state, tactic)
                if stats:
                    stats.t_step_s += time.perf_counter() - t0
                    stats.n_tactic_steps += 1
            except (EOFError, OSError):
                if stats:
                    stats.n_repl_crashes += 1
                continue

            if result.solved or result.state is not None:
                valid.append((tactic, log_prob))
                if stats:
                    if result.solved:
                        stats.n_tactic_solved += 1
                    else:
                        stats.n_tactic_valid += 1
                # Use first valid non-solved to advance state for next sub-goal
                if advance_state is None and result.state is not None:
                    advance_state = result.state

        candidates_per_goal.append(valid)

        if verbose:
            print(f"  Skeleton sub-goal {i} ({structure.leaf_types[i]}): "
                  f"{len(valid)} valid / {len(candidates)} candidates")

        # Advance to next sub-goal
        if i < n_sub_goals - 1:
            if advance_state is None:
                if verbose:
                    print(f"  Skeleton: no valid tactic for sub-goal {i}, "
                          f"constructor fallback")
                break
            current_state = advance_state

    # Check if we have candidates for all sub-goals
    if (len(candidates_per_goal) < n_sub_goals
            or any(not c for c in candidates_per_goal)):
        # Constructor fallback: MCTS continues from post-constructor state
        return _make_constructor_fallback(node, constructor_states)

    # Phase D: Greedy combination + oracle verification
    constructor_tactics = ["constructor"] * structure.constructor_count
    batch_fn = getattr(oracle_fn, '_batch_fn', None)
    combos = list(greedy_combinations(candidates_per_goal, max_attempts=256))

    if verbose:
        print(f"  Skeleton: trying {len(combos)} combinations")

    t0 = time.perf_counter()

    if batch_fn:
        # Batched oracle verification
        all_sequences = [
            constructor_tactics + [t for t, _ in combo]
            for combo in combos
        ]
        for batch_start in range(0, len(all_sequences), _ORACLE_BATCH_SIZE):
            batch = all_sequences[batch_start:batch_start + _ORACLE_BATCH_SIZE]
            results = batch_fn(batch)
            if stats:
                stats.n_oracle_batches += 1
                stats.n_oracle_calls += len(batch)

            for seq, passed in zip(batch, results):
                if passed:
                    if stats:
                        stats.n_oracle_passed += 1
                        stats.t_oracle_s += time.perf_counter() - t0
                    if verbose:
                        print(f"  Skeleton SOLVED: {seq}")
                    return _build_solved_chain(
                        node, env, seq, constructor_states, stats)
    else:
        # Sequential oracle verification
        for combo in combos:
            full_seq = constructor_tactics + [t for t, _ in combo]
            passed = oracle_fn(full_seq)
            if stats:
                stats.n_oracle_calls += 1
            if passed:
                if stats:
                    stats.n_oracle_passed += 1
                    stats.t_oracle_s += time.perf_counter() - t0
                if verbose:
                    print(f"  Skeleton SOLVED: {full_seq}")
                return _build_solved_chain(
                    node, env, full_seq, constructor_states, stats)

    if stats:
        stats.t_oracle_s += time.perf_counter() - t0

    # Phase E: No combination worked — MCTS continues from constructor state
    if verbose:
        print(f"  Skeleton: no combination passed oracle, constructor fallback")
    return _make_constructor_fallback(node, constructor_states)


def _expand_with_recursion_skeleton(
    node: Node, env, policy_fn: Callable, oracle_fn: Callable,
    stats: Optional[SearchStats], verbose: bool,
) -> Optional[Node]:
    """Recursion skeleton expansion for List.rec/Nat.rec goals (Sprint 25).

    For goals where the input is List α or Nat, generates candidate
    expressions of the form `exact List.rec base (step) xs` or
    `exact Nat.rec base (step) n`, type-checks them, and oracle-verifies.

    Unlike product skeleton, this produces single `exact` tactics (no
    sub-goal decomposition needed).

    Returns solved node, or None if no recursion candidate works.
    """
    state_str = node.state.pp if hasattr(node.state, 'pp') else str(node.state)
    # Normalize unicode types for consistent analysis
    normalized = _normalize_state_types(state_str)
    structure = analyze_goal(normalized)

    # Only activate for recursive input, non-product goals
    if not structure.is_recursive_input or structure.is_product:
        return None

    return_type = structure.raw_type
    input_type = structure.recursive_input_type
    element_type = structure.recursive_element_type
    input_var = structure.input_variable

    if verbose:
        print(f"  Recursion skeleton: {input_type} {element_type} → {return_type} "
              f"(var={input_var}, skeleton={structure.recursion_skeleton})")

    # Generate recursion candidates from templates
    candidates = generate_recursion_candidates(
        input_type=input_type,
        element_type=element_type,
        return_type=return_type,
        input_var=input_var,
    )

    if verbose:
        print(f"  Recursion skeleton: {len(candidates)} template candidates")

    # Type-check all candidates via env.step()
    solved_candidates = []  # (tactic, tactics_path)
    for tactic in candidates:
        try:
            t0 = time.perf_counter()
            result = env.step(node.state, tactic)
            if stats:
                stats.t_step_s += time.perf_counter() - t0
                stats.n_tactic_steps += 1
        except (EOFError, OSError):
            if stats:
                stats.n_repl_crashes += 1
            continue

        if result.solved:
            if stats:
                stats.n_tactic_solved += 1
            solved_candidates.append((tactic, [tactic]))
        elif result.state is not None:
            if stats:
                stats.n_tactic_valid += 1

    if verbose:
        print(f"  Recursion skeleton: {len(solved_candidates)} type-checked solutions")

    if not solved_candidates:
        return None

    # Oracle-verify solved candidates
    batch_fn = getattr(oracle_fn, '_batch_fn', None)
    t0 = time.perf_counter()

    if batch_fn:
        all_sequences = [path for _, path in solved_candidates]
        for batch_start in range(0, len(all_sequences), _ORACLE_BATCH_SIZE):
            batch = all_sequences[batch_start:batch_start + _ORACLE_BATCH_SIZE]
            results = batch_fn(batch)
            if stats:
                stats.n_oracle_batches += 1
                stats.n_oracle_calls += len(batch)

            for seq, passed in zip(batch, results):
                if passed:
                    if stats:
                        stats.n_oracle_passed += 1
                        stats.t_oracle_s += time.perf_counter() - t0
                    if verbose:
                        print(f"  Recursion skeleton SOLVED: {seq}")
                    child = Node(
                        state=None, tactic=seq[0], parent=node,
                        is_terminal=True, is_solved=True,
                    )
                    node.children.append(child)
                    return child
    else:
        for tactic, path in solved_candidates:
            passed = oracle_fn(path)
            if stats:
                stats.n_oracle_calls += 1
            if passed:
                if stats:
                    stats.n_oracle_passed += 1
                    stats.t_oracle_s += time.perf_counter() - t0
                if verbose:
                    print(f"  Recursion skeleton SOLVED: {path}")
                child = Node(
                    state=None, tactic=tactic, parent=node,
                    is_terminal=True, is_solved=True,
                )
                node.children.append(child)
                return child

    if stats:
        stats.t_oracle_s += time.perf_counter() - t0

    if verbose:
        print(f"  Recursion skeleton: no candidate passed oracle, falling back")

    return None


def _expand_with_foldl_skeleton(
    node: Node, env, policy_fn: Callable, oracle_fn: Callable,
    stats: Optional[SearchStats], verbose: bool,
) -> Optional[Node]:
    """Foldl/map skeleton expansion for List input goals (Sprint 26).

    For goals where:
    - Input is List α and return is a scalar (Int, Nat, Bool): tries foldl patterns
    - Input is List α and return is List β: tries map patterns

    Unlike recursion skeleton (which uses List.rec right-fold), foldl processes
    left-to-right and map preserves order without cons-reversal concerns.

    Returns solved node, or None if no foldl/map candidate works.
    """
    state_str = node.state.pp if hasattr(node.state, 'pp') else str(node.state)
    normalized = _normalize_state_types(state_str)
    structure = analyze_goal(normalized)

    # Only activate for List input, non-product goals
    if structure.is_product:
        return None
    if not structure.is_foldl_amenable and not structure.is_map_amenable:
        return None

    return_type = structure.raw_type
    input_var = structure.input_variable

    is_list_return = structure.is_map_amenable

    if verbose:
        mode = "map" if is_list_return else "foldl"
        print(f"  Foldl skeleton: {mode} for {return_type} (var={input_var})")

    candidates = generate_foldl_candidates(
        return_type=return_type,
        input_var=input_var,
        is_list_return=is_list_return,
    )

    if verbose:
        print(f"  Foldl skeleton: {len(candidates)} candidates")

    if not candidates:
        return None

    # Type-check all candidates via env.step()
    solved_candidates = []
    for tactic in candidates:
        try:
            t0 = time.perf_counter()
            result = env.step(node.state, tactic)
            if stats:
                stats.t_step_s += time.perf_counter() - t0
                stats.n_tactic_steps += 1
        except (EOFError, OSError):
            if stats:
                stats.n_repl_crashes += 1
            continue

        if result.solved:
            if stats:
                stats.n_tactic_solved += 1
            solved_candidates.append((tactic, [tactic]))
        elif result.state is not None:
            if stats:
                stats.n_tactic_valid += 1

    if verbose:
        print(f"  Foldl skeleton: {len(solved_candidates)} type-checked solutions")

    if not solved_candidates:
        return None

    # Oracle-verify solved candidates
    batch_fn = getattr(oracle_fn, '_batch_fn', None)
    t0 = time.perf_counter()

    if batch_fn:
        all_sequences = [path for _, path in solved_candidates]
        for batch_start in range(0, len(all_sequences), _ORACLE_BATCH_SIZE):
            batch = all_sequences[batch_start:batch_start + _ORACLE_BATCH_SIZE]
            results = batch_fn(batch)
            if stats:
                stats.n_oracle_batches += 1
                stats.n_oracle_calls += len(batch)

            for seq, passed in zip(batch, results):
                if passed:
                    if stats:
                        stats.n_oracle_passed += 1
                        stats.t_oracle_s += time.perf_counter() - t0
                    if verbose:
                        print(f"  Foldl skeleton SOLVED: {seq}")
                    child = Node(
                        state=None, tactic=seq[0], parent=node,
                        is_terminal=True, is_solved=True,
                    )
                    node.children.append(child)
                    return child
    else:
        for tactic, path in solved_candidates:
            passed = oracle_fn(path)
            if stats:
                stats.n_oracle_calls += 1
            if passed:
                if stats:
                    stats.n_oracle_passed += 1
                    stats.t_oracle_s += time.perf_counter() - t0
                if verbose:
                    print(f"  Foldl skeleton SOLVED: {path}")
                child = Node(
                    state=None, tactic=tactic, parent=node,
                    is_terminal=True, is_solved=True,
                )
                node.children.append(child)
                return child

    if stats:
        stats.t_oracle_s += time.perf_counter() - t0

    if verbose:
        print(f"  Foldl skeleton: no candidate passed oracle, falling back")

    return None


def _expand_with_twopass_skeleton(
    node: Node, env, policy_fn: Callable, oracle_fn: Callable,
    stats: Optional[SearchStats], verbose: bool,
) -> Optional[Node]:
    """Two-pass skeleton expansion for multi-pass algorithms (Sprint 26).

    For goals that need two traversals: compute an aggregate, then use it.
    Generates `exact (let v := pass₁(xs); pass₂(xs, v))` patterns.

    Activated as last skeleton before standard MCTS fallback.
    Returns solved node, or None if no two-pass candidate works.
    """
    state_str = node.state.pp if hasattr(node.state, 'pp') else str(node.state)
    normalized = _normalize_state_types(state_str)
    structure = analyze_goal(normalized)

    # Only activate for List input, non-product goals
    if structure.is_product or not structure.is_recursive_input:
        return None
    if structure.recursive_input_type != "List":
        return None

    return_type = structure.raw_type
    input_var = structure.input_variable

    if verbose:
        print(f"  Two-pass skeleton: {return_type} (var={input_var})")

    candidates = generate_twopass_candidates(
        return_type=return_type,
        input_var=input_var,
    )

    if verbose:
        print(f"  Two-pass skeleton: {len(candidates)} candidates")

    if not candidates:
        return None

    # Type-check all candidates via env.step()
    solved_candidates = []
    for tactic in candidates:
        try:
            t0 = time.perf_counter()
            result = env.step(node.state, tactic)
            if stats:
                stats.t_step_s += time.perf_counter() - t0
                stats.n_tactic_steps += 1
        except (EOFError, OSError):
            if stats:
                stats.n_repl_crashes += 1
            continue

        if result.solved:
            if stats:
                stats.n_tactic_solved += 1
            solved_candidates.append((tactic, [tactic]))
        elif result.state is not None:
            if stats:
                stats.n_tactic_valid += 1

    if verbose:
        print(f"  Two-pass skeleton: {len(solved_candidates)} type-checked solutions")

    if not solved_candidates:
        return None

    # Oracle-verify solved candidates
    batch_fn = getattr(oracle_fn, '_batch_fn', None)
    t0 = time.perf_counter()

    if batch_fn:
        all_sequences = [path for _, path in solved_candidates]
        for batch_start in range(0, len(all_sequences), _ORACLE_BATCH_SIZE):
            batch = all_sequences[batch_start:batch_start + _ORACLE_BATCH_SIZE]
            results = batch_fn(batch)
            if stats:
                stats.n_oracle_batches += 1
                stats.n_oracle_calls += len(batch)

            for seq, passed in zip(batch, results):
                if passed:
                    if stats:
                        stats.n_oracle_passed += 1
                        stats.t_oracle_s += time.perf_counter() - t0
                    if verbose:
                        print(f"  Two-pass skeleton SOLVED: {seq}")
                    child = Node(
                        state=None, tactic=seq[0], parent=node,
                        is_terminal=True, is_solved=True,
                    )
                    node.children.append(child)
                    return child
    else:
        for tactic, path in solved_candidates:
            passed = oracle_fn(path)
            if stats:
                stats.n_oracle_calls += 1
            if passed:
                if stats:
                    stats.n_oracle_passed += 1
                    stats.t_oracle_s += time.perf_counter() - t0
                if verbose:
                    print(f"  Two-pass skeleton SOLVED: {path}")
                child = Node(
                    state=None, tactic=tactic, parent=node,
                    is_terminal=True, is_solved=True,
                )
                node.children.append(child)
                return child

    if stats:
        stats.t_oracle_s += time.perf_counter() - t0

    if verbose:
        print(f"  Two-pass skeleton: no candidate passed oracle, falling back")

    return None


def _expand_with_composition_skeleton(
    node: Node, env, policy_fn: Callable, oracle_fn: Callable,
    stats: Optional[SearchStats], verbose: bool,
) -> Optional[Node]:
    """Expression composition expansion (Sprint 27 Intervention B).

    Chains known single-step operations into 2-3 step pipelines.
    This is NOT adding new templates — it's letting existing EF expressions
    call each other in sequence.

    Returns solved node, or None if no composition candidate works.
    """
    state_str = node.state.pp if hasattr(node.state, 'pp') else str(node.state)
    normalized = _normalize_state_types(state_str)
    structure = analyze_goal(normalized)

    if structure.is_product or not structure.is_recursive_input:
        return None
    if structure.recursive_input_type != "List":
        return None

    return_type = structure.raw_type
    input_var = structure.input_variable

    if verbose:
        print(f"  Composition skeleton: {return_type} (var={input_var})")

    candidates = generate_composition_candidates(
        return_type=return_type,
        input_var=input_var,
    )

    if verbose:
        print(f"  Composition skeleton: {len(candidates)} candidates")

    if not candidates:
        return None

    solved_candidates = []
    for tactic in candidates:
        try:
            t0 = time.perf_counter()
            result = env.step(node.state, tactic)
            if stats:
                stats.t_step_s += time.perf_counter() - t0
                stats.n_tactic_steps += 1
        except (EOFError, OSError):
            if stats:
                stats.n_repl_crashes += 1
            continue

        if result.solved:
            if stats:
                stats.n_tactic_solved += 1
            solved_candidates.append((tactic, [tactic]))
        elif result.state is not None:
            if stats:
                stats.n_tactic_valid += 1

    if verbose:
        print(f"  Composition skeleton: {len(solved_candidates)} type-checked solutions")

    if not solved_candidates:
        return None

    batch_fn = getattr(oracle_fn, '_batch_fn', None)
    t0 = time.perf_counter()

    if batch_fn:
        all_sequences = [path for _, path in solved_candidates]
        for batch_start in range(0, len(all_sequences), _ORACLE_BATCH_SIZE):
            batch = all_sequences[batch_start:batch_start + _ORACLE_BATCH_SIZE]
            results = batch_fn(batch)
            if stats:
                stats.n_oracle_batches += 1
                stats.n_oracle_calls += len(batch)

            for seq, passed in zip(batch, results):
                if passed:
                    if stats:
                        stats.n_oracle_passed += 1
                        stats.t_oracle_s += time.perf_counter() - t0
                    if verbose:
                        print(f"  Composition skeleton SOLVED: {seq}")
                    child = Node(
                        state=None, tactic=seq[0], parent=node,
                        is_terminal=True, is_solved=True,
                    )
                    node.children.append(child)
                    return child
    else:
        for tactic, path in solved_candidates:
            passed = oracle_fn(path)
            if stats:
                stats.n_oracle_calls += 1
            if passed:
                if stats:
                    stats.n_oracle_passed += 1
                    stats.t_oracle_s += time.perf_counter() - t0
                if verbose:
                    print(f"  Composition skeleton SOLVED: {path}")
                child = Node(
                    state=None, tactic=tactic, parent=node,
                    is_terminal=True, is_solved=True,
                )
                node.children.append(child)
                return child

    if stats:
        stats.t_oracle_s += time.perf_counter() - t0

    if verbose:
        print(f"  Composition skeleton: no candidate passed oracle, falling back")

    return None


def _batch_oracle_verify(
    solved_candidates: list[tuple],
    oracle_fn: Callable,
    parent: Node,
    verbose: bool,
    stats: Optional[SearchStats],
    state_str: str = "",
    early_exit: bool = True,
) -> Optional[Node]:
    """Verify solved candidates against oracle.

    Supports three modes:
    1. Batched (daemon): oracle_fn._batch_fn packs N candidates per check_compile
    2. Sequential (warm): one call per candidate, single server
    3. Parallel (cold): ThreadPoolExecutor with subprocesses

    Pre-filters candidates that don't reference input parameters.
    """
    from concurrent.futures import ThreadPoolExecutor

    # --- Pre-filter: skip candidates that don't use input params ---
    if state_str:
        plausible, filtered = _prefilter_candidates(solved_candidates, state_str)
    else:
        plausible, filtered = solved_candidates, []

    # Create dead-end nodes for filtered candidates
    for tactic, log_prob, path in filtered:
        if stats:
            stats.n_oracle_prefiltered += 1
        if verbose:
            print(f"    Pre-filtered (no input ref): {tactic}")
        child = Node(
            state=None, tactic=tactic, parent=parent,
            is_terminal=True, is_solved=False, prior=log_prob,
        )
        parent.children.append(child)

    if not plausible:
        return None

    n = len(plausible)

    # --- Mode 1: Batched oracle (daemon) ---
    batch_fn = getattr(oracle_fn, '_batch_fn', None)
    if batch_fn is not None:
        t0 = time.perf_counter()
        winner = None

        # Process in batches of _ORACLE_BATCH_SIZE
        for batch_start in range(0, n, _ORACLE_BATCH_SIZE):
            batch = plausible[batch_start:batch_start + _ORACLE_BATCH_SIZE]
            tactics_list = [path for _, _, path in batch]
            results = batch_fn(tactics_list)
            if stats:
                stats.n_oracle_batches += 1
                stats.n_oracle_calls += len(batch)

            for (tactic, log_prob, path), oracle_pass in zip(batch, results):
                if stats and oracle_pass:
                    stats.n_oracle_passed += 1
                if oracle_pass and winner is None:
                    child = Node(
                        state=None, tactic=tactic, parent=parent,
                        is_terminal=True, is_solved=True, prior=log_prob,
                    )
                    parent.children.append(child)
                    winner = child
                else:
                    if verbose and not oracle_pass:
                        print(f"    Oracle rejected: {tactic}")
                    child = Node(
                        state=None, tactic=tactic, parent=parent,
                        is_terminal=True, is_solved=False, prior=log_prob,
                    )
                    parent.children.append(child)

            # Early exit: if we found a winner, skip remaining batches
            if early_exit and winner is not None:
                # Still create dead-end nodes for remaining candidates
                for remaining in plausible[batch_start + _ORACLE_BATCH_SIZE:]:
                    tactic, log_prob, path = remaining
                    child = Node(
                        state=None, tactic=tactic, parent=parent,
                        is_terminal=True, is_solved=False, prior=log_prob,
                    )
                    parent.children.append(child)
                break

        if stats:
            stats.t_oracle_s += time.perf_counter() - t0
        return winner

    # --- Mode 2 & 3: Single-call oracle (sequential or parallel) ---
    if n == 1:
        tactic, log_prob, path = plausible[0]
        t0 = time.perf_counter()
        oracle_pass = oracle_fn(path)
        if stats:
            stats.t_oracle_s += time.perf_counter() - t0
            stats.n_oracle_calls += 1
            if oracle_pass:
                stats.n_oracle_passed += 1
        if oracle_pass:
            child = Node(
                state=None, tactic=tactic, parent=parent,
                is_terminal=True, is_solved=True, prior=log_prob,
            )
            parent.children.append(child)
            return child
        else:
            if verbose:
                print(f"    Oracle rejected: {tactic}")
            child = Node(
                state=None, tactic=tactic, parent=parent,
                is_terminal=True, is_solved=False, prior=log_prob,
            )
            parent.children.append(child)
            return None

    t0 = time.perf_counter()
    sequential = getattr(oracle_fn, '_sequential', False)

    if sequential:
        winner = None
        for tactic, log_prob, path in plausible:
            oracle_pass = oracle_fn(path)
            if stats:
                stats.n_oracle_calls += 1
                if oracle_pass:
                    stats.n_oracle_passed += 1
            if oracle_pass and winner is None:
                child = Node(
                    state=None, tactic=tactic, parent=parent,
                    is_terminal=True, is_solved=True, prior=log_prob,
                )
                parent.children.append(child)
                winner = child
            else:
                if verbose and not oracle_pass:
                    print(f"    Oracle rejected: {tactic}")
                child = Node(
                    state=None, tactic=tactic, parent=parent,
                    is_terminal=True, is_solved=False, prior=log_prob,
                )
                parent.children.append(child)
    else:
        with ThreadPoolExecutor(max_workers=min(n, 8)) as pool:
            futures = [
                (tactic, log_prob, pool.submit(oracle_fn, path))
                for tactic, log_prob, path in plausible
            ]
            winner = None
            for tactic, log_prob, future in futures:
                oracle_pass = future.result()
                if stats:
                    stats.n_oracle_calls += 1
                    if oracle_pass:
                        stats.n_oracle_passed += 1
                if oracle_pass and winner is None:
                    child = Node(
                        state=None, tactic=tactic, parent=parent,
                        is_terminal=True, is_solved=True, prior=log_prob,
                    )
                    parent.children.append(child)
                    winner = child
                else:
                    if verbose and not oracle_pass:
                        print(f"    Oracle rejected: {tactic}")
                    child = Node(
                        state=None, tactic=tactic, parent=parent,
                        is_terminal=True, is_solved=False, prior=log_prob,
                    )
                    parent.children.append(child)

    if stats:
        stats.t_oracle_s += time.perf_counter() - t0

    return winner


def expand(
    node: Node, env, policy_fn: Callable, oracle_fn: Optional[Callable] = None,
    max_expand_k: int = 0, verbose: bool = False,
    stats: Optional[SearchStats] = None,
    oracle_early_exit: bool = True,
    export_tree: bool = False,
) -> Optional[Node]:
    """
    Expand a node by trying one untried tactic.

    If the node has no untried tactics yet, use the policy network to
    propose candidates and filter through Lean's type checker.

    When oracle_fn is provided, ProofFinished results are additionally
    verified against test cases. Type-correct but semantically wrong
    solutions become terminal dead-ends (reward 0).

    Args:
        max_expand_k: If > 0, cap the number of candidates tried per expansion.
            This prevents REPL crashes when policies propose 500+ tactics.
            Candidates are sorted by log-prob before capping (highest first).
        stats: Optional SearchStats to accumulate timing data.

    Returns the new child node, or None if all tactics are invalid.
    """
    if stats:
        stats.n_expand_calls += 1

    # First expansion: populate untried_tactics from policy network
    if not node.untried_tactics and not node.children:
        # Sprint 24: Try skeleton expansion for product-type goals.
        # Skeleton expansion is a macro-action that applies the full
        # constructor chain + sub-goal exacts in one shot.
        if oracle_fn is not None:
            skeleton_result = _expand_with_skeleton(
                node, env, policy_fn, oracle_fn, stats, verbose,
            )
            if skeleton_result is not None:
                return skeleton_result

            # Sprint 25: Try recursion skeleton for List/Nat inputs
            recursion_result = _expand_with_recursion_skeleton(
                node, env, policy_fn, oracle_fn, stats, verbose,
            )
            if recursion_result is not None:
                return recursion_result

            # Sprint 26: Try foldl/map skeleton for List inputs
            foldl_result = _expand_with_foldl_skeleton(
                node, env, policy_fn, oracle_fn, stats, verbose,
            )
            if foldl_result is not None:
                return foldl_result

            # Sprint 26: Try two-pass skeleton (last before standard MCTS)
            twopass_result = _expand_with_twopass_skeleton(
                node, env, policy_fn, oracle_fn, stats, verbose,
            )
            if twopass_result is not None:
                return twopass_result

            # Sprint 27: Try expression composition (chain known operations)
            composition_result = _expand_with_composition_skeleton(
                node, env, policy_fn, oracle_fn, stats, verbose,
            )
            if composition_result is not None:
                return composition_result
            # Fallback: standard expansion below

        # Get candidates — TrainedPolicy returns (tactic, log_prob) pairs,
        # FixedPolicy returns plain strings
        t0 = time.perf_counter()
        raw = policy_fn(node.state)
        if stats:
            stats.t_policy_s += time.perf_counter() - t0
        if raw and isinstance(raw[0], tuple):
            candidates = [(t, lp) for t, lp in raw]
        else:
            candidates = [(t, 0.0) for t in raw]

        # Deduplicate candidates (keep first occurrence, highest log-prob)
        seen_tactics: set[str] = set()
        deduped = []
        for t, lp in candidates:
            if t not in seen_tactics:
                seen_tactics.add(t)
                deduped.append((t, lp))
        candidates = deduped

        # Cap candidates to avoid REPL overload (Sprint 14)
        if max_expand_k > 0 and len(candidates) > max_expand_k:
            # Sort by log-prob descending, keep top-k
            candidates.sort(key=lambda x: x[1], reverse=True)
            candidates = candidates[:max_expand_k]

        # Phase 1: Type-check all candidates (fast, ~1ms each, sequential)
        valid = []
        solved_candidates = []  # (tactic, log_prob, tactics_path)
        for tactic, log_prob in candidates:
            try:
                t0 = time.perf_counter()
                result = env.step(node.state, tactic)
                if stats:
                    stats.t_step_s += time.perf_counter() - t0
                    stats.n_tactic_steps += 1
            except (EOFError, OSError) as e:
                if stats:
                    stats.n_repl_crashes += 1
                # REPL died — propagate so run_task can catch it
                raise
            if result.solved:
                if stats:
                    stats.n_tactic_solved += 1
                tactics_path = _extract_tactic_path(node) + [tactic]
                solved_candidates.append((tactic, log_prob, tactics_path))
            elif result.state is not None:
                if stats:
                    stats.n_tactic_valid += 1
                valid.append((tactic, result.state, log_prob))

        # Phase 2: Oracle-verify solved candidates (slow, ~2s each, PARALLEL)
        solved_node = None
        if solved_candidates:
            if oracle_fn is not None:
                solved_node = _batch_oracle_verify(
                    solved_candidates, oracle_fn, node, verbose, stats,
                    state_str=str(node.state),
                    early_exit=oracle_early_exit,
                )
                if solved_node is not None and not export_tree:
                    return solved_node
            else:
                # No oracle — first solved candidate wins
                tactic, log_prob, _ = solved_candidates[0]
                child = Node(
                    state=None, tactic=tactic, parent=node,
                    is_terminal=True, is_solved=True, prior=log_prob,
                )
                node.children.append(child)
                if not export_tree:
                    return child
                solved_node = child

        # Normalize priors to probabilities (softmax over valid actions)
        if valid:
            log_probs = [lp for _, _, lp in valid]
            max_lp = max(log_probs)
            exp_lps = [math.exp(lp - max_lp) for lp in log_probs]
            sum_exp = sum(exp_lps)
            priors = [e / sum_exp for e in exp_lps]
        else:
            priors = []

        node.untried_tactics = [t for t, _, _ in valid]
        node._valid_states = {t: s for t, s, _ in valid}
        node._valid_priors = {t: p for (t, _, _), p in zip(valid, priors)}

        # In export_tree mode, return solved node but keep untried tactics for exploration
        if solved_node is not None:
            return solved_node

    if not node.untried_tactics:
        node.is_terminal = True  # Dead end: no valid tactics
        return None

    # Pick the first untried tactic (policy-ranked order)
    tactic = node.untried_tactics.pop(0)
    child_state = node._valid_states.pop(tactic)
    prior = node._valid_priors.pop(tactic, 0.0)

    child = Node(state=child_state, tactic=tactic, parent=node, prior=prior)
    node.children.append(child)
    return child


def simulate(
    node: Node, env, policy_fn: Callable,
    oracle_fn: Optional[Callable] = None,
    max_depth: int = 20,
    stats: Optional[SearchStats] = None,
) -> float:
    """
    Rollout from a node to a terminal state using the policy network greedily.

    Returns:
        1.0 if ProofFinished and oracle-verified (or no oracle)
        0.0 if LeanError, oracle-rejected, or depth limit reached
    """
    if stats:
        stats.n_simulate_calls += 1
    t_sim_start = time.perf_counter()

    if node.is_solved:
        return 1.0
    if node.is_terminal:
        return 0.0

    state = node.state
    sim_tactics = []
    path_prefix = _extract_tactic_path(node)

    for _ in range(max_depth):
        raw = policy_fn(state, k=3)  # Greedy: fewer candidates during simulation
        # Normalize: TrainedPolicy returns (tactic, log_prob), FixedPolicy returns str
        candidates = [t[0] if isinstance(t, tuple) else t for t in raw]
        advanced = False
        for tactic in candidates:
            result = env.step(state, tactic)
            if stats:
                stats.n_tactic_steps += 1
            if result.solved:
                full_path = path_prefix + sim_tactics + [tactic]
                if oracle_fn is None or oracle_fn(full_path):
                    if stats:
                        stats.t_simulate_s += time.perf_counter() - t_sim_start
                    return 1.0
                # Type-correct but wrong — try next candidate
                continue
            if result.state is not None:
                state = result.state
                sim_tactics.append(tactic)
                advanced = True
                break
        if not advanced:
            if stats:
                stats.t_simulate_s += time.perf_counter() - t_sim_start
            return 0.0  # No valid tactics — dead end

    if stats:
        stats.t_simulate_s += time.perf_counter() - t_sim_start
    return 0.0  # Depth limit reached


def backpropagate(node: Optional[Node], reward: float):
    """Update visits and value from the node up to the root."""
    while node is not None:
        node.visits += 1
        node.value += reward
        node = node.parent


def extract_best_path(root: Node) -> list[str]:
    """Extract the most-visited path from root to a terminal node."""
    path = []
    node = root
    while node.children:
        # Pick child with most visits (robust action selection)
        node = max(node.children, key=lambda ch: ch.visits)
        if node.tactic:
            path.append(node.tactic)
        if node.is_solved:
            break
    return path


def extract_tree_labels(root: Node) -> list[dict]:
    """Extract (state_str, value_label) pairs from a completed MCTS tree.

    Labeling strategy:
      - Solved nodes: label = 1.0
      - Terminal dead-end nodes: label = 0.0
      - Non-terminal explored nodes: label = avg_value (MC estimate from visits)
      - Unvisited nodes: skipped
    """
    labels = []

    def _walk(node, depth=0):
        if node.visits == 0:
            return
        state_str = node.state.pp if hasattr(node.state, "pp") else str(node.state)
        if node.is_solved:
            label = 1.0
        elif node.is_terminal:
            label = 0.0
        else:
            label = node.avg_value
        labels.append({
            "state": state_str,
            "outcome": round(label, 4),
            "visits": node.visits,
            "depth": depth,
        })
        for child in node.children:
            _walk(child, depth + 1)

    _walk(root)
    return labels


def search(
    env,
    n_rollouts: int = 500,
    policy_fn: Callable = None,
    value_fn: Callable = None,
    oracle_fn: Callable = None,
    c: float = 1.414,
    max_sim_depth: int = 20,
    use_puct: bool = False,
    value_weight: float = 0.0,
    max_expand_k: int = 0,
    oracle_early_exit: bool = True,
    export_tree: bool = False,
    patience: int | None = None,
    verbose: bool = False,
) -> dict:
    """
    Run MCTS to find a tactic sequence that solves the synthesis task.

    Args:
        env: LeanEnv instance (already reset, or will be reset)
        n_rollouts: Number of MCTS rollouts
        policy_fn: Function(state, k=10) -> list[str] of candidate tactics
        value_fn: Function(state) -> float (optional, for leaf evaluation)
        oracle_fn: Function(tactics: list[str]) -> bool (test-case verification)
        c: Exploration constant (1.414 for UCT, 2.5 for PUCT)
        max_sim_depth: Maximum depth for simulation rollouts
        use_puct: Use PUCT formula instead of UCT
        value_weight: Blend weight for value network (0.0 = pure rollout,
                      1.0 = pure value). Only used when value_fn is not None.
        patience: Early-abort cap for unsolvable tasks (S29 Phase 0.2). When set,
                  the search breaks out after `patience` rollouts without a solve.
                  Solvable tasks early-exit on solve (via the existing check) so
                  they are unaffected. Recommended: 100 for benchmark sweeps.
                  Default None = no cap (runs the full n_rollouts budget).
        verbose: Print progress

    Returns:
        dict with keys:
            "solved": bool
            "tactics": list[str] (tactic sequence, empty if unsolved)
            "rollouts_used": int
            "tree_size": int (total nodes)
            "abort_reason": str | None ("patience_exceeded" when patience hit)
    """
    initial_state = env.reset()
    root = Node(state=initial_state)
    stats = SearchStats()
    abort_reason: str | None = None
    rollouts_executed = 0

    # Use PUCT defaults if enabled and c not explicitly changed from UCT default
    if use_puct and c == 1.414:
        c = 2.5

    for i in range(n_rollouts):
        rollouts_executed = i + 1
        # 1. SELECT
        t0 = time.perf_counter()
        node = select(root, c, use_puct=use_puct)
        stats.t_select_s += time.perf_counter() - t0

        # 2. EXPAND
        if not node.is_terminal:
            child = expand(node, env, policy_fn, oracle_fn, max_expand_k, verbose, stats, oracle_early_exit, export_tree)
            if child is not None:
                node = child

        # 3. EVALUATE (simulate and/or value network)
        if node.is_solved:
            reward = 1.0
        elif node.is_terminal:
            reward = 0.0
        elif value_fn is not None and value_weight > 0:
            # Blend value network estimate with rollout
            v_est = value_fn(node.state)
            if value_weight >= 1.0:
                reward = v_est
            else:
                rollout_reward = simulate(node, env, policy_fn, oracle_fn, max_sim_depth, stats)
                reward = value_weight * v_est + (1 - value_weight) * rollout_reward
        else:
            reward = simulate(node, env, policy_fn, oracle_fn, max_sim_depth, stats)

        # 4. BACKPROPAGATE
        t0 = time.perf_counter()
        backpropagate(node, reward)
        stats.t_select_s += time.perf_counter() - t0  # select + backprop lumped together

        # Early termination if solution found (skip when exporting tree for value data)
        if node.is_solved and not export_tree:
            if verbose:
                depth_dist = _depth_distribution(root)
                print(f"  Solved at rollout {i+1}, depth distribution: {dict(sorted(depth_dist.items()))}")
            tactics = extract_best_path(root)
            result = {
                "solved": True,
                "tactics": tactics,
                "rollouts_used": i + 1,
                "tree_size": _count_nodes(root),
                "stats": stats.summary(),
                "depth_distribution": dict(sorted(_depth_distribution(root).items())),
                "abort_reason": None,
            }
            return result

        # S29 Phase 0.2: patience cap. Abort after `patience` rollouts without a
        # solve. Solvable tasks early-exit above, so this only trims unsolvable
        # tasks from the full n_rollouts budget. Intended use: benchmark sweeps
        # where 500-rollout unsolvable tasks would otherwise dominate wall time.
        if patience is not None and (i + 1) >= patience:
            abort_reason = "patience_exceeded"
            if verbose:
                print(f"  Patience exceeded at rollout {i+1} (--patience={patience})")
            break

    # Check if a solution was found (may have continued past it when export_tree=True)
    tactics = extract_best_path(root)
    solved = _any_solved(root)
    depth_dist = _depth_distribution(root)
    if verbose:
        print(f"  Final depth distribution: {dict(sorted(depth_dist.items()))}")
    result = {
        "solved": solved,
        "tactics": tactics,
        "rollouts_used": rollouts_executed if rollouts_executed else n_rollouts,
        "tree_size": _count_nodes(root),
        "stats": stats.summary(),
        "depth_distribution": dict(sorted(depth_dist.items())),
        "abort_reason": abort_reason,
    }
    if export_tree:
        result["tree_labels"] = extract_tree_labels(root)
    return result


def _any_solved(node: Node) -> bool:
    """Check if any node in the tree is solved."""
    if node.is_solved:
        return True
    return any(_any_solved(ch) for ch in node.children)


def _count_nodes(node: Node) -> int:
    """Count total nodes in the tree."""
    return 1 + sum(_count_nodes(ch) for ch in node.children)


def _depth_distribution(node: Node, depth: int = 0) -> dict[int, int]:
    """Count nodes at each depth level."""
    dist: dict[int, int] = {depth: 1}
    for ch in node.children:
        for d, c in _depth_distribution(ch, depth + 1).items():
            dist[d] = dist.get(d, 0) + c
    return dist
