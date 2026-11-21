"""Execution oracle for verifying synthesized Lean 4 programs against test cases.

After MCTS finds a type-correct solution (ProofFinished), the oracle:
1. Builds a temp Lean file defining the function with the discovered tactics
2. Adds #eval calls for each test case
3. Runs `lake env lean` (cold) or Pantograph check_compile (warm) to evaluate
4. Compares output to expected values

This closes the gap between type-correctness and semantic correctness.

Two modes:
- Cold (verify): spawns `lake env lean` subprocess per call (~2100ms/call)
- Warm (warm_verify): reuses running Pantograph server (~130-375ms/call, 5-16x faster)
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Thread-safe counter for unique eval filenames (parallel oracle calls)
_eval_counter = 0
_eval_lock = threading.Lock()


def _next_eval_id() -> int:
    global _eval_counter
    with _eval_lock:
        _eval_counter += 1
        return _eval_counter


@dataclass
class OracleResult:
    passed: bool
    n_passed: int
    n_total: int
    details: list[dict] = field(default_factory=list)
    error: Optional[str] = None


# Same imports as Tasks.lean
LEAN_IMPORTS = """\
import Mathlib.Tactic
import Mathlib.Data.List.Sort
import Mathlib.Data.List.Basic
import Mathlib.Data.Int.Basic

set_option linter.unusedVariables false
set_option linter.unusedTactic false
"""

# Regex to filter Lean diagnostic lines (warnings/notes that leak to stdout)
_DIAG_RE = re.compile(r"^.*\.lean:\d+:\d+:|^[Nn]ote:|^warning:|^Warning:")


def _build_params(params_signature: str | None, test_cases: list[dict], input_type: str) -> str:
    """Build function parameter signature for oracle wrapper."""
    if params_signature:
        return params_signature
    has_two_args = any("input2" in tc for tc in test_cases)
    if has_two_args:
        return "(xs : List Int) (ys : List Int)"
    elif input_type == "List (List Int)":
        return f"(xss : {input_type})"
    elif input_type == "Nat":
        return "(n : Nat)"
    else:
        return f"(xs : {input_type})"


def _eval_args(tc: dict) -> str:
    """Assemble #eval arguments from test case dict."""
    args = tc["input"]
    for i in range(2, 10):
        key = f"input{i}"
        if key in tc:
            args += f" {tc[key]}"
        else:
            break
    return args


def _build_eval_lean(
    theorem_name: str,
    return_type: str,
    tactics: list[str],
    test_cases: list[dict],
    input_type: str = "List Int",
    params_signature: str | None = None,
) -> str:
    """Build a Lean 4 file that defines the function and evaluates test cases."""
    lines = [LEAN_IMPORTS]
    tactic_body = "\n  ".join(tactics)
    params = _build_params(params_signature, test_cases, input_type)

    lines.append(f"def oracle_{theorem_name} {params} : {return_type} := by")
    lines.append(f"  {tactic_body}")
    lines.append("")
    for tc in test_cases:
        lines.append(f"#eval oracle_{theorem_name} {_eval_args(tc)}")
    return "\n".join(lines) + "\n"


def _build_warm_eval_code(
    theorem_name: str,
    return_type: str,
    tactics: list[str],
    test_cases: list[dict],
    input_type: str = "List Int",
    eval_id: int = 0,
    params_signature: str | None = None,
) -> str:
    """Build Lean code for warm oracle (no imports — server already has them)."""
    tactic_body = "\n  ".join(tactics)
    params = _build_params(params_signature, test_cases, input_type)

    # Unique name to avoid collisions across oracle calls in same server session
    fname = f"oracle_{theorem_name}_{eval_id}"

    lines = ["set_option linter.unusedVariables false"]
    lines.append(f"def {fname} {params} : {return_type} := by")
    lines.append(f"  {tactic_body}")
    lines.append("")
    for tc in test_cases:
        lines.append(f"#eval {fname} {_eval_args(tc)}")
    return "\n".join(lines) + "\n"


def warm_verify(
    task,
    tactics: list[str],
    server,
    loop: asyncio.AbstractEventLoop,
    verbose: bool = False,
) -> OracleResult:
    """Verify using a warm Pantograph server (check_compile with #eval).

    ~5-16x faster than subprocess verify() — reuses loaded Mathlib environment.

    Args:
        task: SynthesisTask (needs .theorem_name, .return_type, .test_cases)
        tactics: Tactic sequence that produced ProofFinished
        server: pantograph.Server instance (already has Mathlib loaded)
        loop: asyncio event loop for the server
        verbose: Print debug info

    Returns:
        OracleResult with pass/fail and per-test details
    """
    expected = [tc["expected"] for tc in task.test_cases]

    eval_id = _next_eval_id()
    code = _build_warm_eval_code(
        task.theorem_name, task.return_type, tactics, task.test_cases,
        input_type=task.input_type, eval_id=eval_id,
        params_signature=getattr(task, "params_signature", None),
    )

    if verbose:
        print(f"    Oracle(warm): evaluating {len(task.test_cases)} tests for {task.theorem_name}")
        print(f"    Tactics: {tactics}")

    try:
        result = loop.run_until_complete(server.check_compile_async(code))
    except Exception as e:
        if verbose:
            print(f"    Oracle(warm) ERROR: {e}")
        return OracleResult(
            passed=False, n_passed=0, n_total=len(task.test_cases),
            error=str(e),
        )

    # Extract #eval output from information messages
    from pantograph.data import Severity
    info_messages = []
    error_messages = []
    for cu in result:
        for msg in cu.messages:
            if msg.severity == Severity.INFORMATION:
                info_messages.append(msg.data.strip())
            elif msg.severity == Severity.ERROR:
                error_messages.append(msg.data.strip())

    if error_messages:
        error_text = "; ".join(error_messages[:3])
        if verbose:
            print(f"    Oracle(warm) compile error: {error_text[:200]}")
        return OracleResult(
            passed=False, n_passed=0, n_total=len(task.test_cases),
            error=error_text,
        )

    details = []
    n_passed = 0
    for i, tc in enumerate(task.test_cases):
        actual = info_messages[i] if i < len(info_messages) else "<missing>"
        exp = expected[i]
        ok = actual.strip() == exp.strip()
        if ok:
            n_passed += 1
        details.append({
            "input": tc["input"],
            "expected": exp,
            "actual": actual,
            "passed": ok,
        })

    all_passed = n_passed == len(task.test_cases)
    if verbose:
        status = "PASS" if all_passed else "FAIL"
        print(f"    Oracle(warm): {status} ({n_passed}/{len(task.test_cases)})")
        if not all_passed:
            for d in details:
                if not d["passed"]:
                    print(f"      {d['input']}: expected {d['expected']}, got {d['actual']}")

    return OracleResult(
        passed=all_passed,
        n_passed=n_passed,
        n_total=len(task.test_cases),
        details=details,
    )


def daemon_warm_verify(
    task,
    tactics: list[str],
    client,
    verbose: bool = False,
) -> OracleResult:
    """Verify using the Pantograph daemon's check_compile (via Unix socket).

    Same logic as warm_verify but communicates through PantographClient
    instead of holding a direct server+loop reference.

    Args:
        task: SynthesisTask (needs .theorem_name, .return_type, .test_cases)
        tactics: Tactic sequence that produced ProofFinished
        client: PantographClient connected to the daemon
        verbose: Print debug info

    Returns:
        OracleResult with pass/fail and per-test details
    """
    expected = [tc["expected"] for tc in task.test_cases]

    eval_id = _next_eval_id()
    code = _build_warm_eval_code(
        task.theorem_name, task.return_type, tactics, task.test_cases,
        input_type=task.input_type, eval_id=eval_id,
        params_signature=getattr(task, "params_signature", None),
    )

    if verbose:
        print(f"    Oracle(daemon): evaluating {len(task.test_cases)} tests for {task.theorem_name}")
        print(f"    Tactics: {tactics}")

    try:
        units = client.check_compile(code)
    except Exception as e:
        if verbose:
            print(f"    Oracle(daemon) ERROR: {e}")
        return OracleResult(
            passed=False, n_passed=0, n_total=len(task.test_cases),
            error=str(e),
        )

    # Extract #eval output from information messages
    info_messages = []
    error_messages = []
    for cu in units:
        for msg in cu["messages"]:
            if msg["severity"] == "INFORMATION":
                info_messages.append(msg["data"].strip())
            elif msg["severity"] == "ERROR":
                error_messages.append(msg["data"].strip())

    if error_messages:
        error_text = "; ".join(error_messages[:3])
        if verbose:
            print(f"    Oracle(daemon) compile error: {error_text[:200]}")
        return OracleResult(
            passed=False, n_passed=0, n_total=len(task.test_cases),
            error=error_text,
        )

    details = []
    n_passed = 0
    for i, tc in enumerate(task.test_cases):
        actual = info_messages[i] if i < len(info_messages) else "<missing>"
        exp = expected[i]
        ok = actual.strip() == exp.strip()
        if ok:
            n_passed += 1
        details.append({
            "input": tc["input"],
            "expected": exp,
            "actual": actual,
            "passed": ok,
        })

    all_passed = n_passed == len(task.test_cases)
    if verbose:
        status = "PASS" if all_passed else "FAIL"
        print(f"    Oracle(daemon): {status} ({n_passed}/{len(task.test_cases)})")
        if not all_passed:
            for d in details:
                if not d["passed"]:
                    print(f"      {d['input']}: expected {d['expected']}, got {d['actual']}")

    return OracleResult(
        passed=all_passed,
        n_passed=n_passed,
        n_total=len(task.test_cases),
        details=details,
    )


def _build_batch_eval_code(
    theorem_name: str,
    return_type: str,
    tactics_list: list[list[str]],
    test_cases: list[dict],
    input_type: str = "List Int",
    batch_id: int = 0,
    params_signature: str | None = None,
) -> str:
    """Build Lean code with multiple candidate defs + #eval blocks in one file.

    Each candidate gets a unique function name. All #eval outputs appear
    as INFORMATION messages in order: candidate_0_test_0, candidate_0_test_1, ...,
    candidate_1_test_0, etc.
    """
    params = _build_params(params_signature, test_cases, input_type)

    lines = ["set_option linter.unusedVariables false"]
    lines.append("")

    for i, tactics in enumerate(tactics_list):
        tactic_body = "\n  ".join(tactics)
        fname = f"oracle_{theorem_name}_{batch_id}_{i}"
        lines.append(f"def {fname} {params} : {return_type} := by")
        lines.append(f"  {tactic_body}")
        lines.append("")
        for tc in test_cases:
            lines.append(f"#eval {fname} {_eval_args(tc)}")
        lines.append("")

    return "\n".join(lines) + "\n"


def _parse_batch_results(
    units, tactics_list, task, expected, n_tests, verbose=False
) -> list:
    """Parse check_compile results into OracleResult list."""
    n_candidates = len(tactics_list)

    # Collect all messages in order
    info_messages = []
    for cu in units:
        for msg in cu["messages"]:
            if msg["severity"] == "INFORMATION":
                info_messages.append(msg["data"].strip())
            elif msg["severity"] == "ERROR":
                info_messages.append("<error>")

    # Parse results: each candidate owns n_tests consecutive messages
    results = []
    for cand_idx in range(n_candidates):
        offset = cand_idx * n_tests
        n_passed = 0
        details = []
        has_error = False

        for t_idx in range(n_tests):
            msg_pos = offset + t_idx
            if msg_pos < len(info_messages):
                actual = info_messages[msg_pos]
            else:
                actual = "<missing>"
                has_error = True

            if actual == "<error>":
                has_error = True

            exp = expected[t_idx]
            ok = actual.strip() == exp.strip()
            if ok:
                n_passed += 1
            details.append({
                "input": task.test_cases[t_idx]["input"],
                "expected": exp,
                "actual": actual,
                "passed": ok,
            })

        all_passed = n_passed == n_tests
        if verbose and all_passed:
            print(f"      candidate {cand_idx}: PASS")
        elif verbose and not all_passed:
            print(f"      candidate {cand_idx}: FAIL ({n_passed}/{n_tests})")

        results.append(OracleResult(
            passed=all_passed,
            n_passed=n_passed,
            n_total=n_tests,
            details=details,
            error="compilation error" if has_error and n_passed == 0 else None,
        ))

    return results


def batch_daemon_warm_verify(
    task,
    tactics_list: list[list[str]],
    client,
    verbose: bool = False,
) -> list[OracleResult]:
    """Verify multiple candidate tactic sequences via check_compile.

    When the client has multiple workers, splits candidates across workers
    for parallel compilation. Otherwise packs all into one call.

    Args:
        task: SynthesisTask
        tactics_list: List of tactic sequences to verify
        client: PantographClient connected to the daemon
        verbose: Print debug info

    Returns:
        List of OracleResult, one per candidate
    """
    n_candidates = len(tactics_list)
    n_tests = len(task.test_cases)
    expected = [tc["expected"] for tc in task.test_cases]

    if n_candidates == 0:
        return []

    n_workers = getattr(client, 'n_workers', 1)

    # --- Parallel path: split across workers ---
    if n_workers > 1 and n_candidates > 1:
        # Split candidates into sub-batches, one per worker
        sub_batches = [[] for _ in range(n_workers)]
        sub_indices = [[] for _ in range(n_workers)]  # track original indices
        for i, tactics in enumerate(tactics_list):
            w = i % n_workers
            sub_batches[w].append(tactics)
            sub_indices[w].append(i)

        # Build code for each sub-batch
        codes = []
        for w, sub in enumerate(sub_batches):
            if not sub:
                codes.append(None)
                continue
            batch_id = _next_eval_id()
            code = _build_batch_eval_code(
                task.theorem_name, task.return_type, sub, task.test_cases,
                input_type=task.input_type, batch_id=batch_id,
                params_signature=getattr(task, "params_signature", None),
            )
            codes.append(code)

        non_empty_codes = [c for c in codes if c is not None]
        if verbose:
            print(f"    Oracle(parallel): {n_candidates} candidates × {n_tests} tests across {len(non_empty_codes)} workers")

        try:
            all_units = client.check_compile_parallel(non_empty_codes)
        except Exception as e:
            if verbose:
                print(f"    Oracle(parallel) ERROR: {e}")
            return [
                OracleResult(passed=False, n_passed=0, n_total=n_tests, error=str(e))
                for _ in range(n_candidates)
            ]

        # Parse results from each worker and reassemble in original order
        results = [None] * n_candidates
        units_idx = 0
        for w in range(n_workers):
            sub = sub_batches[w]
            if not sub:
                continue
            units = all_units[units_idx]
            units_idx += 1
            sub_results = _parse_batch_results(units, sub, task, expected, n_tests, verbose)
            for orig_idx, result in zip(sub_indices[w], sub_results):
                results[orig_idx] = result

        return results

    # --- Single-worker path: pack all into one call ---
    batch_id = _next_eval_id()
    code = _build_batch_eval_code(
        task.theorem_name, task.return_type, tactics_list, task.test_cases,
        input_type=task.input_type, batch_id=batch_id,
        params_signature=getattr(task, "params_signature", None),
    )

    if verbose:
        print(f"    Oracle(batch): {n_candidates} candidates × {n_tests} tests in one call")

    try:
        units = client.check_compile(code)
    except Exception as e:
        if verbose:
            print(f"    Oracle(batch) ERROR: {e}")
        return [
            OracleResult(passed=False, n_passed=0, n_total=n_tests, error=str(e))
            for _ in range(n_candidates)
        ]

    return _parse_batch_results(units, tactics_list, task, expected, n_tests, verbose)


def verify(
    task,
    tactics: list[str],
    lean_project: Path,
    timeout: int = 60,
    verbose: bool = False,
) -> OracleResult:
    """
    Verify a synthesized solution against test cases using Lean's #eval.

    Args:
        task: SynthesisTask (needs .theorem_name, .return_type, .test_cases)
        tactics: Tactic sequence that produced ProofFinished
        lean_project: Path to the Lean 4 project root
        timeout: Subprocess timeout in seconds
        verbose: Print debug info

    Returns:
        OracleResult with pass/fail and per-test details
    """
    expected = [tc["expected"] for tc in task.test_cases]

    lean_code = _build_eval_lean(
        task.theorem_name, task.return_type, tactics, task.test_cases,
        input_type=task.input_type,
        params_signature=getattr(task, "params_signature", None),
    )

    if verbose:
        print(f"    Oracle: evaluating {len(task.test_cases)} tests for {task.theorem_name}")
        print(f"    Tactics: {tactics}")

    eval_id = _next_eval_id()
    eval_file = lean_project / "FormalNav" / f"_Eval_{task.theorem_name}_{eval_id}.lean"
    try:
        eval_file.write_text(lean_code)

        result = subprocess.run(
            ["lake", "env", "lean", str(eval_file)],
            cwd=str(lean_project),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip()
            if verbose:
                print(f"    Oracle ERROR: {error_msg[:200]}")
            return OracleResult(
                passed=False, n_passed=0, n_total=len(task.test_cases),
                error=error_msg,
            )

        # Parse output, filtering any diagnostic lines that leak to stdout
        output_lines = [
            line.strip()
            for line in result.stdout.strip().split("\n")
            if line.strip() and not _DIAG_RE.match(line)
        ]

        details = []
        n_passed = 0
        for i, tc in enumerate(task.test_cases):
            actual = output_lines[i] if i < len(output_lines) else "<missing>"
            exp = expected[i]
            ok = actual.strip() == exp.strip()
            if ok:
                n_passed += 1
            details.append({
                "input": tc["input"],
                "expected": exp,
                "actual": actual,
                "passed": ok,
            })

        all_passed = n_passed == len(task.test_cases)
        if verbose:
            status = "PASS" if all_passed else "FAIL"
            print(f"    Oracle: {status} ({n_passed}/{len(task.test_cases)})")
            if not all_passed:
                for d in details:
                    if not d["passed"]:
                        print(f"      {d['input']}: expected {d['expected']}, got {d['actual']}")

        return OracleResult(
            passed=all_passed,
            n_passed=n_passed,
            n_total=len(task.test_cases),
            details=details,
        )

    except subprocess.TimeoutExpired:
        if verbose:
            print(f"    Oracle: TIMEOUT ({timeout}s)")
        return OracleResult(
            passed=False, n_passed=0, n_total=len(task.test_cases),
            error=f"Timeout after {timeout}s",
        )

    finally:
        if eval_file.exists():
            eval_file.unlink()
