"""Build fragment cache: evaluate all Level-0 fragments on all benchmark test inputs.

This is a one-time operation that pre-computes fragment outputs for ALL
benchmark task inputs (not just the 5 standard ones from enumerate.py).
Results are stored in fragment_cache.json for use by the compositional search.

Usage:
    python -m lean_mcts.synth.build_cache [--daemon]

Requires a running Pantograph daemon for evaluation.

Phase 0 of Sprint 22.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def collect_all_test_inputs() -> dict[str, list[str]]:
    """Collect all unique test inputs across all benchmark tasks, grouped by input_type."""
    from lean_mcts.benchmark.tasks import (
        TASKS, HARD_TASKS, V2_TASKS, RECURRENCE_TASKS,
        FUNDAMENTALS_TASKS, MULTISTEP_TASKS, DEEP_MULTISTEP_TASKS,
        COMPOSITIONAL_TASKS,
    )

    all_tasks = (
        TASKS + HARD_TASKS + V2_TASKS + RECURRENCE_TASKS +
        FUNDAMENTALS_TASKS + MULTISTEP_TASKS + DEEP_MULTISTEP_TASKS +
        COMPOSITIONAL_TASKS
    )

    inputs_by_type: dict[str, set[str]] = {}
    for task in all_tasks:
        itype = task.input_type
        if itype not in inputs_by_type:
            inputs_by_type[itype] = set()
        for tc in task.test_cases:
            inputs_by_type[itype].add(tc["input"])

    return {k: sorted(v) for k, v in inputs_by_type.items()}


def build_cache(daemon_client=None, verbose: bool = True, existing_cache: dict = None) -> dict:
    """Build fragment cache with outputs on all benchmark inputs.

    Args:
        daemon_client: PantographClient for live evaluation
        verbose: Print progress
        existing_cache: Pre-loaded cache to extend (skips re-evaluation)

    Returns dict: {tag: {input_str: output_str, ...}, ...}
    """
    from lean_mcts.synth.fragments import FragmentCatalog
    from lean_mcts.synth.partial_eval import PartialEvaluator

    cat = FragmentCatalog()
    evaluator = PartialEvaluator(client=daemon_client)

    # Seed evaluator cache from existing fragment outputs
    for frag in cat.fragments:
        evaluator.seed_cache(frag.expr, frag.outputs)

    # Seed from existing cache file if provided
    if existing_cache:
        for tag, outputs in existing_cache.items():
            frag = cat.get_fragment(tag)
            if frag:
                frag.outputs.update(outputs)
                evaluator.seed_cache(frag.expr, outputs)

    # Collect all test inputs
    inputs_by_type = collect_all_test_inputs()
    if verbose:
        for itype, inputs in inputs_by_type.items():
            print(f"  {itype}: {len(inputs)} unique test inputs")

    # Evaluate each fragment on its type-compatible inputs
    cache = {}
    n_cached = 0
    n_evaluated = 0
    n_failed = 0

    for frag in cat.fragments:
        inputs = inputs_by_type.get(frag.input_type, [])
        if not inputs:
            continue

        if verbose:
            print(f"  [{frag.tag}] {frag.input_type} → {frag.output_type}: {len(inputs)} inputs...", end=" ", flush=True)

        results = evaluator.evaluate(
            frag.expr, frag.input_type, frag.output_type, inputs
        )

        frag_cache = dict(frag.outputs)  # start with existing
        for inp in inputs:
            if inp in results:
                frag_cache[inp] = results[inp]
                if inp in frag.outputs:
                    n_cached += 1
                else:
                    n_evaluated += 1
            else:
                n_failed += 1

        cache[frag.tag] = frag_cache
        if verbose:
            new = sum(1 for inp in inputs if inp not in frag.outputs and inp in results)
            print(f"{new} new, {len(frag_cache)} total")

    if verbose:
        print(f"\nPass 1 done: {n_cached} cached hits, {n_evaluated} new evaluations, {n_failed} failures")

    # --- Pass 2: Evaluate intermediate values for depth-2 compositions ---
    # For each (inner, outer) pair, inner's outputs become inputs to outer.
    # We need outer evaluated on those intermediate values.
    if daemon_client is not None and verbose:
        print("\nPass 2: evaluating intermediate values for depth-2 composition...")

    intermediate_by_type: dict[str, set[str]] = {}  # type → set of intermediate values
    for frag in cat.fragments:
        frag_outputs = cache.get(frag.tag, {})
        out_type = frag.output_type
        for val in frag_outputs.values():
            if val:
                intermediate_by_type.setdefault(out_type, set()).add(val)

    n_intermediates = 0
    for frag in cat.fragments:
        intermediates = intermediate_by_type.get(frag.input_type, set())
        if not intermediates:
            continue
        # Filter out values already cached
        existing = set(cache.get(frag.tag, {}).keys())
        new_inputs = sorted(intermediates - existing)
        if not new_inputs:
            continue

        if verbose:
            print(f"  [{frag.tag}] {len(new_inputs)} intermediate inputs...", end=" ", flush=True)

        results = evaluator.evaluate(
            frag.expr, frag.input_type, frag.output_type, new_inputs
        )
        frag_cache = cache.get(frag.tag, {})
        added = 0
        for inp in new_inputs:
            if inp in results:
                frag_cache[inp] = results[inp]
                added += 1
                n_intermediates += 1
        cache[frag.tag] = frag_cache
        if verbose:
            print(f"{added} new")

    if verbose:
        print(f"\nPass 2 done: {n_intermediates} intermediate evaluations")

    # --- Pass 3: Depth-2 intermediates for depth-3 compositions ---
    # The outputs from pass 2 are depth-1 intermediates.
    # For depth-3, we need depth-2 intermediates: values produced by
    # applying one fragment to a depth-1 intermediate.
    if daemon_client is not None and verbose:
        print("\nPass 3: evaluating depth-2 intermediates for depth-3 composition...")

    # Collect ALL unique output values from the cache (includes pass 1 + 2)
    depth2_by_type: dict[str, set[str]] = {}
    for tag, outputs in cache.items():
        frag = cat.get_fragment(tag)
        if frag is None:
            continue
        out_type = frag.output_type
        for val in outputs.values():
            if val:
                depth2_by_type.setdefault(out_type, set()).add(val)

    n_depth2 = 0
    for frag in cat.fragments:
        d2_inputs = depth2_by_type.get(frag.input_type, set())
        if not d2_inputs:
            continue
        existing = set(cache.get(frag.tag, {}).keys())
        new_inputs = sorted(d2_inputs - existing)
        if not new_inputs:
            continue

        if verbose:
            print(f"  [{frag.tag}] {len(new_inputs)} depth-2 inputs...", end=" ", flush=True)

        results = evaluator.evaluate(
            frag.expr, frag.input_type, frag.output_type, new_inputs
        )
        frag_cache = cache.get(frag.tag, {})
        added = 0
        for inp in new_inputs:
            if inp in results:
                frag_cache[inp] = results[inp]
                added += 1
                n_depth2 += 1
        cache[frag.tag] = frag_cache
        if verbose:
            print(f"{added} new")

    if verbose:
        print(f"\nPass 3 done: {n_depth2} depth-2 intermediate evaluations")

    return cache


def save_cache(cache: dict, path: str = None):
    """Save fragment cache to JSON."""
    if path is None:
        path = str(Path(__file__).parent / "fragment_cache.json")

    with open(path, "w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    print(f"Saved fragment cache to {path} ({len(cache)} fragments)")


def load_cache(path: str = None) -> dict:
    """Load fragment cache from JSON."""
    if path is None:
        path = str(Path(__file__).parent / "fragment_cache.json")

    with open(path) as f:
        return json.load(f)


def seed_catalog_from_cache(catalog, cache: dict):
    """Update a FragmentCatalog's fragments with cached outputs."""
    for tag, outputs in cache.items():
        catalog.extend_outputs(tag, outputs)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build fragment cache for compositional search")
    parser.add_argument("--daemon", action="store_true", help="Use Pantograph daemon")
    parser.add_argument("--output", type=str, default=None, help="Output path")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    daemon_client = None
    if args.daemon:
        from lean_mcts.env.pantograph_client import PantographClient
        daemon_client = PantographClient()
        if not daemon_client.ping():
            print("ERROR: daemon not responding")
            return
        print("Connected to Pantograph daemon")

    # Load existing cache to avoid re-computing
    existing = None
    cache_path = Path(__file__).parent / "fragment_cache.json"
    if cache_path.exists():
        existing = load_cache(str(cache_path))
        if not args.quiet:
            total_entries = sum(len(v) for v in existing.values())
            print(f"Loaded existing cache: {len(existing)} fragments, {total_entries} entries")

    t0 = time.time()
    cache = build_cache(daemon_client=daemon_client, verbose=not args.quiet, existing_cache=existing)
    elapsed = time.time() - t0

    save_cache(cache, args.output)
    print(f"Total time: {elapsed:.1f}s")

    if daemon_client:
        daemon_client.close()


if __name__ == "__main__":
    main()
