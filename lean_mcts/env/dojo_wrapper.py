"""
Wrapper around LeanDojo's Dojo class for MCTS integration.

Provides a gym-like interface:
  env = LeanEnv(project_path, theorem_name)
  state = env.reset()
  result = env.step(state, "exact n")
  env.is_solved(result)  # True if ProofFinished
  env.close()

Requires: lean-dojo >= 4.20.0, Lean 4 project with lake build completed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

# LeanDojo imports — will fail until lean-dojo is installed in the 3.12 venv
try:
    from lean_dojo import Dojo, LeanGitRepo, Theorem, TacticState, ProofFinished, LeanError
    from lean_dojo.interaction.dojo import get_traced_repo_path
except ImportError:
    Dojo = None
    get_traced_repo_path = None
    print("WARNING: lean-dojo not installed. Run: pip install lean-dojo")


@dataclass
class StepResult:
    """Unified result from applying a tactic."""
    state: Optional[object] = None    # TacticState if tactic was accepted
    error: Optional[str] = None       # Error message if tactic was rejected
    solved: bool = False              # True if ProofFinished
    message: Optional[str] = None     # Optional message from Lean


class DirectDojo:
    """Direct REPL interaction, bypassing LeanDojo's trace system.

    Used when *.ast.json is missing for a file (e.g. newly added files
    that haven't been traced yet). Creates the modified Lean file directly
    by replacing `by sorry` with the LeanDojo REPL tactic.
    """

    def __init__(self, repo: 'LeanGitRepo', file_path: str, theorem_name: str,
                 traced_repo_override: Path = None):
        self.repo = repo
        self.file_path = file_path
        self.theorem_name = theorem_name
        self.proc = None
        self.modified_file = None
        self._traced_repo_path = None
        self._traced_repo_override = traced_repo_override

    def __enter__(self):
        if self._traced_repo_override:
            self._traced_repo_path = self._traced_repo_override
        else:
            self._traced_repo_path = get_traced_repo_path(self.repo, build_deps=False)
        repl_path = self._traced_repo_path / "Lean4Repl.lean"
        assert repl_path.exists(), "Lean4Repl.lean not found in traced repo"

        # Read the source file — prefer the actual repo over the cached copy
        # since the cached copy may be from an older commit
        actual_src = Path(self.repo.url) / self.file_path
        src_path = self._traced_repo_path / self.file_path
        if actual_src.exists():
            source = actual_src.read_text()
            # Also copy the file into the traced repo so lean can find it
            src_path.parent.mkdir(parents=True, exist_ok=True)
            src_path.write_text(source)
        else:
            source = src_path.read_text()
        source = src_path.read_text()

        # Find the def and replace its proof with the REPL tactic
        pattern = re.compile(
            rf'(def\s+{re.escape(self.theorem_name)}\b.*?:=\s*)\bby\s+sorry\b',
            re.DOTALL,
        )
        match = pattern.search(source)
        if match is None:
            raise RuntimeError(
                f"Cannot find 'def {self.theorem_name} ... := by sorry' in {self.file_path}"
            )

        modified = (
            "import Lean4Repl\n\n"
            + source[:match.start()]
            + match.group(1)
            + "by\n  lean_dojo_repl\n  sorry"
            + source[match.end():]
        )

        # Write to temp file in the same directory
        self.modified_file = tempfile.NamedTemporaryFile(
            "wt", prefix=self.file_path.replace("/", "_"),
            suffix=".lean", dir=src_path.parent, delete=True,
        )
        self.modified_file.write(modified)
        self.modified_file.flush()

        # Run lean on the modified file
        mod_rel = Path(self.modified_file.name).relative_to(self._traced_repo_path)
        lean_cmd = f"lake env lean --threads=4 {mod_rel}"
        cmd = f"exec 10<&0; exec {lean_cmd}"
        self.proc = subprocess.Popen(
            ["bash", "-c", cmd],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            cwd=str(self._traced_repo_path),
        )

        # Read initial tactic state — skip non-JSON lines (build output, warnings)
        res = None
        for _ in range(100):
            line = self._read_line()
            # LeanDojo REPL prefixes output with "REPL> "
            if line.startswith("REPL>"):
                line = line[len("REPL>"):].strip()
            if line.startswith("{"):
                res = json.loads(line)
                break
        if res is None:
            raise RuntimeError("REPL never sent JSON response")
        if res.get("error"):
            raise RuntimeError(f"REPL init error: {res['error']}")

        state_str = res["tacticState"]
        assert state_str != "no goals"
        init_state = TacticState(pp=state_str, id=res["sid"])
        return self, init_state

    def _read_line(self, timeout: float = 120) -> str:
        import threading
        result = [None]
        def _read():
            result[0] = self.proc.stdout.readline()
        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout)
        if result[0] is None:
            raise TimeoutError("Lean REPL timed out")
        if not result[0]:
            stderr = self.proc.stderr.read()
            raise EOFError(f"Lean REPL EOF. stderr: {stderr[:500]}")
        return result[0].strip()

    def run_tac(self, state, tactic: str):
        """Send a tactic to the REPL and parse the response."""
        req = json.dumps({"sid": state.id, "cmd": tactic})
        self.proc.stdin.write(req + "\n")
        self.proc.stdin.flush()

        # Skip non-JSON lines (warnings, linter output)
        res = None
        for _ in range(50):
            line = self._read_line()
            if line.startswith("REPL>"):
                line = line[len("REPL>"):].strip()
            if line.startswith("{"):
                res = json.loads(line)
                break
        if res is None:
            return LeanError("REPL never sent JSON response")

        if res.get("error"):
            return LeanError(res["error"])
        elif res.get("tacticState") == "no goals":
            return ProofFinished(res.get("message", ""))
        elif res.get("tacticState"):
            return TacticState(pp=res["tacticState"], id=res["sid"])
        else:
            return LeanError(f"Unexpected REPL response: {res}")

    def __exit__(self, *args):
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=5)
        if self.modified_file:
            self.modified_file.close()


class LeanEnv:
    """Gym-like wrapper around LeanDojo for MCTS search."""

    # Commit hash that has been traced by LeanDojo (Tasks.lean, Synthesis.lean, Basic.lean)
    _TRACED_COMMIT = "18b15e789dfe626e4722a34915833ce9b5025597"

    def __init__(self, repo_path: str, file_path: str, theorem_name: str):
        """
        Args:
            repo_path: Path to the Lean 4 project root (where lakefile.toml lives)
            file_path: Relative path to the .lean file within the project
            theorem_name: Name of the theorem/def to synthesize
        """
        self.repo_path = Path(repo_path)
        self.file_path = file_path
        self.theorem_name = theorem_name
        self._dojo = None
        self._initial_state = None

    def reset(self) -> object:
        """Initialize Dojo and return the initial tactic state.

        Uses the known-traced commit for files that have AST data.
        Falls back to DirectDojo for untraced files (e.g. TasksHard.lean).
        """
        if self._dojo is not None:
            self.close()

        # Try LeanDojo Dojo with the known-traced commit first
        repo = LeanGitRepo(str(self.repo_path), self._TRACED_COMMIT)
        theorem = Theorem(repo, self.file_path, self.theorem_name)

        try:
            self._dojo_ctx = Dojo(theorem)
            self._dojo, self._initial_state = self._dojo_ctx.__enter__()
        except Exception as e:
            if "ast.json" in str(e) or "Failed to locate" in str(e):
                # Trace missing — use direct REPL fallback.
                # Use HEAD repo for the source, but the traced commit's build cache.
                head_repo = LeanGitRepo(str(self.repo_path), "HEAD")
                traced_path = get_traced_repo_path(repo, build_deps=False)
                self._dojo_ctx = DirectDojo(
                    head_repo, self.file_path, self.theorem_name,
                    traced_repo_override=traced_path,
                )
                self._dojo, self._initial_state = self._dojo_ctx.__enter__()
            else:
                raise
        return self._initial_state

    def step(self, state: object, tactic: str) -> StepResult:
        """
        Apply a tactic to the current state.

        Returns StepResult with:
          - .state: new TacticState if accepted
          - .error: error string if rejected
          - .solved: True if ProofFinished
        """
        assert self._dojo is not None, "Call reset() before step()"

        result = self._dojo.run_tac(state, tactic)

        if isinstance(result, ProofFinished):
            return StepResult(solved=True, message=result.message)
        elif isinstance(result, LeanError):
            return StepResult(error=result.error)
        elif isinstance(result, TacticState):
            return StepResult(state=result, message=result.message)
        else:
            return StepResult(error=f"Unknown result type: {type(result)}")

    def is_solved(self, result: StepResult) -> bool:
        """Check if the result indicates the proof/program is complete."""
        return result.solved

    def close(self):
        """Clean up Dojo resources."""
        if self._dojo_ctx is not None:
            self._dojo_ctx.__exit__(None, None, None)
            self._dojo = None
            self._dojo_ctx = None
            self._initial_state = None

    def __enter__(self):
        self.reset()
        return self

    def __exit__(self, *args):
        self.close()


def _test():
    """Minimal test: send a tactic to a trivial Lean theorem."""
    import sys

    project_path = Path(__file__).parent / "FormalNav"
    if not (project_path / "lakefile.toml").exists():
        print(f"ERROR: Lean project not found at {project_path}")
        print("Run: cd lean_mcts/env && lake +leanprover/lean4:stable new FormalNav math")
        sys.exit(1)

    env = LeanEnv(
        repo_path=str(project_path),
        file_path="FormalNav/Synthesis.lean",
        theorem_name="synth_identity",
    )

    print("Testing LeanDojo integration...")

    state = env.reset()
    print(f"  Initial state: {state}")

    # Try a correct tactic
    result = env.step(state, "exact n")
    if result.solved:
        print("  exact n → ProofFinished ✓")
    elif result.error:
        print(f"  exact n → Error: {result.error}")
    else:
        print(f"  exact n → New state: {result.state}")

    # Try an invalid tactic
    result = env.step(state, "clearly_not_a_tactic")
    if result.error:
        print(f"  invalid tactic → Error (expected) ✓")
    else:
        print(f"  invalid tactic → Unexpected: {result}")

    env.close()
    print("All tests passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Run integration test")
    args = parser.parse_args()

    if args.test:
        _test()
