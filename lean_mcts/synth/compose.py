"""Compositional search engine: bottom-up program construction from typed fragments.

Given a task specification (input_type, return_type, test_cases), constructs
programs by composing fragments at depth 1-3 and verifying against test cases.

Phase 2 of Sprint 22.
"""

from __future__ import annotations

from dataclasses import dataclass

from .fragments import Fragment, FragmentCatalog
from .partial_eval import PartialEvaluator


@dataclass
class ComposedCandidate:
    """A candidate tactic found by compositional search."""
    expr: str               # The composed expression
    tactic: str             # "exact <expr>"
    depth: int              # 1, 2, or 3
    fragments: list[str]    # Fragment tags involved


def _compose_expr(inner: Fragment, outer: Fragment) -> str:
    """Build composed expression: substitute inner into outer.

    The outer fragment uses its input_var (typically 'xs') as the
    variable. We replace it with the inner expression wrapped in parens.

    E.g., inner.expr = "xs.filter (fun x => decide (x > 0))"
          outer.expr = "xs.length"
          → "(xs.filter (fun x => decide (x > 0))).length"

    For outer expressions that use the variable standalone (like "xs.length"),
    we replace the leading `xs` with `(inner_expr)`.
    For outer expressions like "xs.map ..." we do the same.
    """
    outer_var = outer.input_var
    inner_expr = inner.expr

    # Build the composed expression by substituting outer's variable
    # Strategy: replace outermost occurrence of the variable
    # We need to be careful not to replace variables inside lambdas
    composed = _substitute_var(outer.expr, outer_var, f"({inner_expr})")
    return composed


def _compose_expr_triple(a: Fragment, b: Fragment, c: Fragment) -> str:
    """Build depth-3 composed expression: c(b(a(input)))."""
    ab_expr = _compose_expr(a, b)
    # Create a synthetic fragment for the a→b composition
    ab_frag = Fragment(
        tag=f"{a.tag}__{b.tag}",
        expr=ab_expr,
        input_type=a.input_type,
        output_type=b.output_type,
        outputs={},
        input_var=a.input_var,
    )
    return _compose_expr(ab_frag, c)


def _substitute_var(expr: str, var: str, replacement: str) -> str:
    """Replace the leading variable reference in an expression.

    Handles patterns like:
    - "xs.length" → "(inner).length"
    - "xs.filter ..." → "(inner).filter ..."
    - "List.mergeSort xs" → "List.mergeSort (inner)"
    - "(xs.map ...).foldl ..." → "((inner).map ...).foldl ..."

    We do a simple token-level replacement of the FIRST occurrence of `var`
    that appears as a standalone token (not inside a lambda body after `=>`).
    """
    # Find the first standalone occurrence of var
    # A standalone occurrence is preceded by start/whitespace/( and
    # followed by end/whitespace/./)/,
    import re
    # Match var as a whole word, but not after '=>' (lambda body var)
    # Strategy: find ALL word-boundary matches, take the first one
    # that isn't inside a lambda binding (after 'fun <var> =>')
    pattern = re.compile(r'(?<![a-zA-Z_])' + re.escape(var) + r'(?![a-zA-Z_0-9])')

    # Find all matches
    matches = list(pattern.finditer(expr))
    if not matches:
        return expr  # var not found, return as-is

    # Check if this var is also used as a lambda parameter
    # Find lambda bindings: "fun <var> =>"
    lambda_pattern = re.compile(r'fun\s+' + re.escape(var) + r'\s*=>')
    lambda_ranges = []
    for m in lambda_pattern.finditer(expr):
        # The scope of this lambda var extends to the end of the enclosing parens
        # For simplicity, mark everything after 'fun var =>' until matching paren
        lambda_ranges.append(m.start())

    # Replace only the FIRST standalone occurrence that is NOT inside a lambda body
    # The first occurrence is typically the top-level input reference
    for m in matches:
        pos = m.start()
        # Check: is this occurrence the lambda parameter itself?
        in_lambda_decl = False
        for lr in lambda_ranges:
            if lr <= pos <= lr + len(f"fun {var} =>") + 5:
                in_lambda_decl = True
                break
        if in_lambda_decl:
            continue
        # This is a valid substitution point
        return expr[:pos] + replacement + expr[pos + len(var):]

    # All occurrences are in lambda bodies — can't compose
    return expr


class CompositionSearch:
    """Bottom-up program construction from typed fragments."""

    def __init__(
        self,
        catalog: FragmentCatalog,
        evaluator: PartialEvaluator,
    ):
        self.catalog = catalog
        self.evaluator = evaluator

    def search(
        self,
        task,
        max_depth: int = 3,
        max_candidates: int = 50,
    ) -> list[ComposedCandidate]:
        """Search for expressions matching task's test cases.

        Args:
            task: SynthesisTask with input_type, return_type, test_cases
            max_depth: Maximum composition depth (1-3)
            max_candidates: Stop after finding this many candidates

        Returns:
            List of ComposedCandidate that match all test cases.
        """
        test_inputs = [tc["input"] for tc in task.test_cases]
        test_expected = [tc["expected"] for tc in task.test_cases]
        candidates = []

        # --- Depth 1: direct fragment match ---
        candidates.extend(
            self._search_depth1(task, test_inputs, test_expected)
        )
        if len(candidates) >= max_candidates:
            return candidates[:max_candidates]

        if max_depth < 2:
            return candidates

        # --- Depth 2: two-step composition ---
        candidates.extend(
            self._search_depth2(task, test_inputs, test_expected,
                                max_candidates - len(candidates))
        )
        if len(candidates) >= max_candidates:
            return candidates[:max_candidates]

        if max_depth < 3:
            return candidates

        # --- Depth 3: three-step composition ---
        candidates.extend(
            self._search_depth3(task, test_inputs, test_expected,
                                max_candidates - len(candidates))
        )
        return candidates[:max_candidates]

    def _search_depth1(
        self, task, test_inputs: list[str], test_expected: list[str],
    ) -> list[ComposedCandidate]:
        """Direct fragment match: fragment with matching types and outputs."""
        candidates = []
        for frag in self.catalog.by_input_type(task.input_type):
            if frag.output_type != task.return_type:
                continue
            # Check all test cases
            if self._fragment_matches(frag, test_inputs, test_expected):
                candidates.append(ComposedCandidate(
                    expr=frag.expr,
                    tactic=f"exact {frag.expr}",
                    depth=1,
                    fragments=[frag.tag],
                ))
        return candidates

    def _search_depth2(
        self, task, test_inputs: list[str], test_expected: list[str],
        budget: int = 50,
    ) -> list[ComposedCandidate]:
        """Two-step composition with first-test-case pruning."""
        candidates = []
        pairs = self.catalog.composable_pairs(task.input_type, task.return_type)

        for inner, outer in pairs:
            if len(candidates) >= budget:
                break

            # Compute inner outputs on task inputs
            inner_outputs = self._get_fragment_outputs(
                inner, task.input_type, test_inputs
            )
            if not inner_outputs or len(inner_outputs) < len(test_inputs):
                continue

            # First-test-case pruning
            first_input = test_inputs[0]
            inner_out_0 = inner_outputs.get(first_input)
            if inner_out_0 is None:
                continue

            outer_out_0 = self._eval_fragment_on(
                outer, inner.output_type, inner_out_0
            )
            if outer_out_0 is None or outer_out_0 != test_expected[0]:
                continue

            # Passed first test case — check remaining
            all_match = True
            for i in range(1, len(test_inputs)):
                inp = test_inputs[i]
                inner_out = inner_outputs.get(inp)
                if inner_out is None:
                    all_match = False
                    break
                outer_out = self._eval_fragment_on(
                    outer, inner.output_type, inner_out
                )
                if outer_out is None or outer_out != test_expected[i]:
                    all_match = False
                    break

            if all_match:
                composed = _compose_expr(inner, outer)
                candidates.append(ComposedCandidate(
                    expr=composed,
                    tactic=f"exact {composed}",
                    depth=2,
                    fragments=[inner.tag, outer.tag],
                ))

        return candidates

    def _search_depth3(
        self, task, test_inputs: list[str], test_expected: list[str],
        budget: int = 20,
    ) -> list[ComposedCandidate]:
        """Three-step composition with aggressive pruning.

        Pre-warms the cache by batch-evaluating all depth-1 intermediate
        values before checking triples, to minimize daemon calls.
        """
        candidates = []
        triples = self.catalog.composable_triples(
            task.input_type, task.return_type
        )
        if not triples:
            return candidates

        # Pre-warm: batch-evaluate intermediates for task inputs only.
        # Step 1: Get a-outputs on task inputs (already cached from depth-2)
        a_outputs_map: dict[str, dict[str, str]] = {}
        a_intermediates: dict[str, set[str]] = {}  # type → values
        for a, b, c in triples:
            if a.tag not in a_outputs_map:
                a_outputs_map[a.tag] = self._get_fragment_outputs(
                    a, task.input_type, test_inputs
                )
                for val in a_outputs_map[a.tag].values():
                    if val:
                        a_intermediates.setdefault(a.output_type, set()).add(val)

        # Step 2: Batch-evaluate b fragments on a-intermediates
        b_seen: set[str] = set()
        b_intermediates: dict[str, set[str]] = {}  # type → values
        for a, b, c in triples:
            if b.tag in b_seen:
                continue
            b_seen.add(b.tag)
            ints = a_intermediates.get(b.input_type, set())
            uncached = [v for v in ints if b.evaluate(v) is None]
            if uncached:
                results = self.evaluator.evaluate(
                    b.expr, b.input_type, b.output_type, uncached,
                )
                b.outputs.update(results)
            # Collect b's outputs on task-relevant intermediates for c
            for v in ints:
                out = b.evaluate(v)
                if out:
                    b_intermediates.setdefault(b.output_type, set()).add(out)

        # Step 3: Pre-warm c fragments on ALL test-case b-intermediates.
        # Collect b-outputs reachable from each test input.
        all_b_ints: dict[str, set[str]] = {}
        for inp in test_inputs:
            for a, b, c in triples:
                a_out = a.evaluate(inp)
                if a_out is None:
                    continue
                b_out = b.evaluate(a_out)
                if b_out is None:
                    continue
                all_b_ints.setdefault(b.output_type, set()).add(b_out)

        c_seen: set[str] = set()
        for a, b, c in triples:
            if c.tag in c_seen:
                continue
            c_seen.add(c.tag)
            ints = all_b_ints.get(c.input_type, set())
            uncached = [v for v in ints if c.evaluate(v) is None]
            if uncached:
                results = self.evaluator.evaluate(
                    c.expr, c.input_type, c.output_type, uncached,
                )
                c.outputs.update(results)

        for a, b, c in triples:
            if len(candidates) >= budget:
                break

            # Compute a's outputs
            a_outputs = self._get_fragment_outputs(
                a, task.input_type, test_inputs
            )
            if not a_outputs or len(a_outputs) < len(test_inputs):
                continue

            # First-test-case pruning through all three stages
            first_input = test_inputs[0]
            a_out_0 = a_outputs.get(first_input)
            if a_out_0 is None:
                continue

            b_out_0 = self._eval_fragment_on(b, a.output_type, a_out_0)
            if b_out_0 is None:
                continue

            c_out_0 = self._eval_fragment_on(c, b.output_type, b_out_0)
            if c_out_0 is None or c_out_0 != test_expected[0]:
                continue

            # Check remaining test cases
            all_match = True
            for i in range(1, len(test_inputs)):
                inp = test_inputs[i]
                a_out = a_outputs.get(inp)
                if a_out is None:
                    all_match = False
                    break
                b_out = self._eval_fragment_on(b, a.output_type, a_out)
                if b_out is None:
                    all_match = False
                    break
                c_out = self._eval_fragment_on(c, b.output_type, b_out)
                if c_out is None or c_out != test_expected[i]:
                    all_match = False
                    break

            if all_match:
                composed = _compose_expr_triple(a, b, c)
                candidates.append(ComposedCandidate(
                    expr=composed,
                    tactic=f"exact {composed}",
                    depth=3,
                    fragments=[a.tag, b.tag, c.tag],
                ))

        return candidates

    def _fragment_matches(
        self, frag: Fragment, test_inputs: list[str],
        test_expected: list[str],
    ) -> bool:
        """Check if a fragment's outputs match all test cases."""
        for inp, exp in zip(test_inputs, test_expected):
            out = frag.evaluate(inp)
            if out is None:
                # Try evaluating via daemon
                results = self.evaluator.evaluate(
                    frag.expr, frag.input_type, frag.output_type, [inp]
                )
                out = results.get(inp)
            if out is None or out != exp:
                return False
        return True

    def _get_fragment_outputs(
        self, frag: Fragment, input_type: str, inputs: list[str],
    ) -> dict[str, str]:
        """Get fragment outputs for given inputs (cache + daemon fallback)."""
        results = {}
        uncached = []
        for inp in inputs:
            out = frag.evaluate(inp)
            if out is not None:
                results[inp] = out
            else:
                uncached.append(inp)

        if uncached:
            # Try evaluator
            eval_results = self.evaluator.evaluate(
                frag.expr, input_type, frag.output_type, uncached
            )
            results.update(eval_results)
            # Cache back into fragment
            frag.outputs.update(eval_results)

        return results

    def _eval_fragment_on(
        self, frag: Fragment, input_type: str, input_str: str,
    ) -> str | None:
        """Evaluate a fragment on a single input (cache first)."""
        out = frag.evaluate(input_str)
        if out is not None:
            return out

        # Evaluate via daemon
        results = self.evaluator.evaluate(
            frag.expr, input_type, frag.output_type, [input_str]
        )
        out = results.get(input_str)
        if out is not None:
            frag.outputs[input_str] = out
        return out
