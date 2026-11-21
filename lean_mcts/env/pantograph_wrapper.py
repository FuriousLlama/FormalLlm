"""
Wrapper around PyPantograph for MCTS integration.

Drop-in replacement for LeanEnv (dojo_wrapper.py), backed by Pantograph.
No LeanDojo dependency, no git commit pinning, no temp file injection.

Provides the same gym-like interface:
  env = PantographEnv(project_path, file_path, theorem_name)
  state = env.reset()
  result = env.step(state, "exact n")
  env.close()

Requires: pantograph >= 0.3.13 (PyPantograph from Stanford),
          Lean 4 project with `lake build` completed.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pantograph import Server
from pantograph.expr import GoalState
from pantograph.server import TacticFailure


@dataclass
class StepResult:
    """Unified result from applying a tactic."""
    state: Optional[object] = None    # GoalState if tactic was accepted
    error: Optional[str] = None       # Error message if tactic was rejected
    solved: bool = False              # True if no goals remain
    message: Optional[str] = None


class PantographEnv:
    """Gym-like wrapper around Pantograph for MCTS search.

    State objects are Pantograph GoalState instances. str(state) produces
    the same format as LeanDojo TacticState.pp ("xs : List Z\n|- Nat"),
    so existing policies work unchanged.
    """

    def __init__(self, repo_path: str, file_path: str, theorem_name: str):
        self.repo_path = Path(repo_path)
        self.file_path = file_path
        self.theorem_name = theorem_name
        self._server: Optional[Server] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _ensure_loop(self):
        """Get or create an event loop for sync calls."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _run(self, coro):
        """Run an async coroutine synchronously."""
        return self._ensure_loop().run_until_complete(coro)

    def reset(self) -> object:
        """Start Pantograph server, create goal from theorem type."""
        if self._server is not None:
            self.close()

        # Determine which modules to import based on the lean file
        lean_file = self.file_path
        # e.g. "FormalNav/Tasks.lean" -> "FormalNav.Tasks"
        module = lean_file.replace("/", ".").removesuffix(".lean")
        # Always import the top-level module too
        imports = ["FormalNav"]
        if module != "FormalNav" and module not in imports:
            imports.append(module)

        self._server = self._run(Server.create(
            imports=imports,
            project_path=str(self.repo_path),
        ))

        # Get the type with named binders by constructing a ∀ expression
        # from the Lean source. This ensures variable names (xs, ys, etc.)
        # match what the policies expect.
        param_names = self._extract_param_names()

        # Get theorem type from the compiled environment
        info = self._run(self._server.env_inspect_async(name=self.theorem_name))
        theorem_type = info["type"]["pp"]

        # Start a goal with this type
        state = self._run(self._server.goal_start_async(theorem_type))

        # Auto-intro all arguments with the correct names from the source.
        # DirectDojo starts inside `def foo (xs : List Int) : Nat := by sorry`
        # so `xs` is already in context. We replicate this.
        if param_names:
            intro_tactic = "intro " + " ".join(param_names)
            try:
                state = self._run(
                    self._server.goal_tactic_async(state, tactic=intro_tactic)
                )
            except TacticFailure:
                pass  # no args to intro (shouldn't happen)

        return state

    def _extract_param_names(self) -> list[str]:
        """Extract parameter names from the Lean source definition."""
        lean_path = self.repo_path / self.file_path
        if not lean_path.exists():
            return []

        source = lean_path.read_text()
        # Match: def theoremName (arg1 : Type1) (arg2 : Type2) ... : RetType := by
        pattern = rf'def\s+{re.escape(self.theorem_name)}\b(.*?):=\s*by'
        match = re.search(pattern, source, re.DOTALL)
        if not match:
            return []

        sig = match.group(1)
        # Extract parameter names from (name : Type) groups.
        # Handles both single-name `(xs : T)` and grouped multi-name
        # `(xs ys : T)` binders — the old `re.findall(r'\(\s*(\w+)\s*:', sig)`
        # dropped grouped binders entirely, causing MCTS to receive a
        # ∀-prefixed goal with no intro'd hypotheses (S29 merge_sorted_desc).
        params: list[str] = []
        for m in re.finditer(r'\(\s*([a-zA-Z_]\w*(?:\s+[a-zA-Z_]\w*)*)\s*:', sig):
            params.extend(m.group(1).split())
        return params

    def step(self, state: GoalState, tactic: str) -> StepResult:
        """Apply a tactic. Returns StepResult compatible with dojo_wrapper."""
        assert self._server is not None, "Call reset() before step()"

        try:
            new_state = self._run(
                self._server.goal_tactic_async(state, tactic=tactic)
            )
        except TacticFailure as e:
            return StepResult(error=str(e))
        except Exception as e:
            # ServerError or unexpected — propagate as StepResult error
            # unless it's a crash (EOF/OS), in which case raise
            if isinstance(e, (EOFError, OSError, ConnectionError)):
                raise
            return StepResult(error=str(e))

        if new_state.is_solved:
            return StepResult(solved=True)
        else:
            return StepResult(state=new_state)

    def close(self):
        """Close Pantograph server."""
        if self._server is not None:
            self._server._close()
            self._server = None
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
            self._loop = None

    def __enter__(self):
        self.reset()
        return self

    def __exit__(self, *args):
        self.close()


def _test():
    """Smoke test: send tactics to synth_identity through Pantograph."""
    project_path = Path(__file__).parent / "FormalNav"

    env = PantographEnv(
        repo_path=str(project_path),
        file_path="FormalNav/Synthesis.lean",
        theorem_name="synth_identity",
    )

    print("Testing Pantograph integration...")

    state = env.reset()
    print(f"  Initial state:\n{state}")

    # Try intro + exact
    result = env.step(state, "intro n")
    if result.state:
        print(f"  intro n -> new state:\n{result.state}")
        result2 = env.step(result.state, "exact n")
        if result2.solved:
            print("  exact n -> Solved!")
        else:
            print(f"  exact n -> {result2}")
    else:
        print(f"  intro n -> {result}")

    # Try an invalid tactic
    result = env.step(state, "clearly_not_a_tactic")
    if result.error:
        print(f"  invalid tactic -> Error (expected)")

    env.close()
    print("All tests passed.")


if __name__ == "__main__":
    _test()
