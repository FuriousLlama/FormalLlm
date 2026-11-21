"""Generate compositional training data for Sprint 23.

Enumerates all valid fragment compositions at depth 1-3, generates
(state, tactic) training pairs, and optionally verifies via Lean daemon.

Also generates multi-step decomposition pairs for product return types,
including depth-3 nested products (the deep multi-step gap).

Usage:
    python lean_mcts/synth/generate_compositional_data.py
    python lean_mcts/synth/generate_compositional_data.py --verify
    python lean_mcts/synth/generate_compositional_data.py --max-depth 2 --verbose
    python lean_mcts/synth/generate_compositional_data.py --stats-only
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict
from pathlib import Path

try:
    from lean_mcts.synth.fragments import Fragment, FragmentCatalog
    from lean_mcts.synth.compose import _compose_expr, _compose_expr_triple
    from lean_mcts.synth.collect_selfplay import (
        TrainingPair,
        augment_pairs,
        write_training_data,
        PRODUCT_TYPES,
    )
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from lean_mcts.synth.fragments import Fragment, FragmentCatalog
    from lean_mcts.synth.compose import _compose_expr, _compose_expr_triple
    from lean_mcts.synth.collect_selfplay import (
        TrainingPair,
        augment_pairs,
        write_training_data,
        PRODUCT_TYPES,
    )


# ---------------------------------------------------------------------------
# Nested product types for depth-3 multi-step decompositions
# ---------------------------------------------------------------------------

# Outer product → (inner_product, leaf_type)
# inner_product is itself in PRODUCT_TYPES
NESTED_PRODUCT_TYPES: dict[str, tuple[str, str]] = {
    "(Int × Nat) × Int": ("Int × Nat", "Int"),
    "(Int × Int) × Nat": ("Int × Int", "Nat"),
    "(Nat × Nat) × Nat": ("Nat × Nat", "Nat"),
    "(Nat × Int) × Nat": ("Nat × Int", "Nat"),
    "(List Int × List Int) × Nat": ("List Int × List Int", "Nat"),
    "(Nat × Nat) × Int": ("Nat × Nat", "Int"),
    "(Int × Nat) × Nat": ("Int × Nat", "Nat"),
}


def _param_prefix(input_type: str) -> str:
    """Build the parameter prefix for a state string."""
    if input_type == "List (List Int)":
        return "xss : List (List Int)"
    elif input_type == "Nat":
        return "n : Nat"
    elif input_type == "List Int, List Int":
        return "xs : List Int\nys : List Int"
    else:
        return f"xs : {input_type}"


# ---------------------------------------------------------------------------
# Phase 1: Enumerate compositions
# ---------------------------------------------------------------------------

def enumerate_compositions(
    catalog: FragmentCatalog,
    max_depth: int = 3,
    max_depth3_per_sig: int = 50,
    seed: int = 42,
    verbose: bool = False,
) -> list[dict]:
    """Enumerate all type-valid compositions at depth 1-3.

    Depth-1 and depth-2 are enumerated exhaustively. Depth-3 is sampled
    to max_depth3_per_sig per type signature to keep training balanced.

    Returns list of dicts with keys: expr, tactic, depth, fragments,
    input_type, return_type.
    """
    rng = random.Random(seed)
    candidates = []
    seen_exprs: set[str] = set()

    # Collect all type signatures
    input_types = sorted(set(f.input_type for f in catalog.fragments))
    output_types = sorted(set(f.output_type for f in catalog.fragments))

    if verbose:
        print(f"Fragment catalog: {len(catalog)} fragments")
        print(f"  Input types: {input_types}")
        print(f"  Output types: {output_types}")

    # --- Depth 1: all fragments as direct tactics ---
    for frag in catalog.fragments:
        if frag.expr not in seen_exprs:
            seen_exprs.add(frag.expr)
            candidates.append({
                "expr": frag.expr,
                "tactic": f"exact {frag.expr}",
                "depth": 1,
                "fragments": [frag.tag],
                "input_type": frag.input_type,
                "return_type": frag.output_type,
            })

    depth1_count = len(candidates)
    if verbose:
        print(f"Depth-1: {depth1_count} candidates")

    if max_depth < 2:
        return candidates

    # --- Depth 2: all type-compatible pairs ---
    depth2_count = 0
    for input_type in input_types:
        for output_type in output_types:
            pairs = catalog.composable_pairs(input_type, output_type)
            for inner, outer in pairs:
                composed = _compose_expr(inner, outer)
                if composed not in seen_exprs:
                    seen_exprs.add(composed)
                    candidates.append({
                        "expr": composed,
                        "tactic": f"exact {composed}",
                        "depth": 2,
                        "fragments": [inner.tag, outer.tag],
                        "input_type": inner.input_type,
                        "return_type": outer.output_type,
                    })
                    depth2_count += 1

    if verbose:
        print(f"Depth-2: {depth2_count} candidates")

    if max_depth < 3:
        return candidates

    # --- Depth 3: sampled per type signature ---
    # Enumerate all triples but sample to keep training balanced.
    # Prioritize diverse fragment combinations (all 3 fragments different).
    depth3_count = 0
    for input_type in input_types:
        for output_type in output_types:
            triples = catalog.composable_triples(input_type, output_type)
            if not triples:
                continue

            # Build candidates for this type signature
            sig_candidates = []
            for a, b, c in triples:
                composed = _compose_expr_triple(a, b, c)
                if composed not in seen_exprs:
                    sig_candidates.append({
                        "expr": composed,
                        "tactic": f"exact {composed}",
                        "depth": 3,
                        "fragments": [a.tag, b.tag, c.tag],
                        "input_type": a.input_type,
                        "return_type": c.output_type,
                        # Diversity score: prefer all-different fragments
                        "_diverse": len(set([a.tag, b.tag, c.tag])),
                    })

            if len(sig_candidates) <= max_depth3_per_sig:
                selected = sig_candidates
            else:
                # Prioritize diverse compositions (all 3 fragments different)
                diverse = [c for c in sig_candidates if c["_diverse"] == 3]
                partial = [c for c in sig_candidates if c["_diverse"] == 2]
                rest = [c for c in sig_candidates if c["_diverse"] == 1]

                selected = []
                for pool in [diverse, partial, rest]:
                    remaining = max_depth3_per_sig - len(selected)
                    if remaining <= 0:
                        break
                    if len(pool) <= remaining:
                        selected.extend(pool)
                    else:
                        selected.extend(rng.sample(pool, remaining))

            for c in selected:
                seen_exprs.add(c["expr"])
                c.pop("_diverse", None)
                candidates.append(c)
                depth3_count += 1

    if verbose:
        print(f"Depth-3: {depth3_count} candidates (capped at {max_depth3_per_sig}/sig)")

    return candidates


# ---------------------------------------------------------------------------
# Phase 2: Convert to training pairs
# ---------------------------------------------------------------------------

def candidates_to_pairs(candidates: list[dict]) -> list[TrainingPair]:
    """Convert composition dicts to TrainingPairs with proper state format."""
    pairs = []
    for cand in candidates:
        param = _param_prefix(cand["input_type"])
        state = f"{param}\n⊢ {cand['return_type']}"
        pairs.append(TrainingPair(
            state=state,
            tactic=cand["tactic"],
            source="compositional",
            task_name=f"comp_{'_'.join(cand['fragments'])}",
            return_type=cand["return_type"],
            input_type=cand["input_type"],
        ))
    return pairs


# ---------------------------------------------------------------------------
# Phase 3: Multi-step product decomposition pairs
# ---------------------------------------------------------------------------

def generate_multistep_pairs(
    single_step_pairs: list[TrainingPair],
    verbose: bool = False,
) -> list[TrainingPair]:
    """Generate multi-step training pairs for product return types.

    Includes both depth-2 products (constructor → exact × 2) and
    depth-3 nested products (constructor → constructor → exact × 3).

    This is the critical data for the deep multi-step gap (2/6 → 4+/6).
    """
    rng = random.Random(42)
    multistep: list[TrainingPair] = []

    # Group single-step pairs by (input_type, return_type)
    by_sig: dict[tuple[str, str], list[TrainingPair]] = defaultdict(list)
    for p in single_step_pairs:
        by_sig[(p.input_type, p.return_type)].append(p)

    # --- Depth-2 product decompositions (constructor → exact × 2) ---
    for product_type, (left_type, right_type) in PRODUCT_TYPES.items():
        for input_type in ["List Int", "Nat", "List (List Int)"]:
            left_pairs = by_sig.get((input_type, left_type), [])
            right_pairs = by_sig.get((input_type, right_type), [])

            if not left_pairs or not right_pairs:
                continue

            param = _param_prefix(input_type)

            # constructor tactic for the product goal
            product_state = f"{param}\n⊢ {product_type}"
            multistep.append(TrainingPair(
                state=product_state,
                tactic="constructor",
                source="multistep",
                task_name=f"constructor_{product_type}",
                return_type=product_type,
                input_type=input_type,
            ))

            # Sub-goal pairs (cap to avoid explosion)
            left_sample = rng.sample(left_pairs, min(25, len(left_pairs)))
            right_sample = rng.sample(right_pairs, min(25, len(right_pairs)))

            for lp in left_sample:
                # Both with and without case prefix (model sees both forms)
                for state_str in [
                    f"case fst\n{param}\n⊢ {left_type}",
                    f"{param}\n⊢ {left_type}",
                ]:
                    multistep.append(TrainingPair(
                        state=state_str,
                        tactic=lp.tactic,
                        source="multistep",
                        task_name=f"subgoal_left_{lp.task_name}",
                        return_type=left_type,
                        input_type=input_type,
                    ))

            for rp in right_sample:
                for state_str in [
                    f"case snd\n{param}\n⊢ {right_type}",
                    f"{param}\n⊢ {right_type}",
                ]:
                    multistep.append(TrainingPair(
                        state=state_str,
                        tactic=rp.tactic,
                        source="multistep",
                        task_name=f"subgoal_right_{rp.task_name}",
                        return_type=right_type,
                        input_type=input_type,
                    ))

    depth2_ms = len(multistep)
    if verbose:
        print(f"Depth-2 multi-step pairs: {depth2_ms}")

    # --- Depth-3 nested product decompositions ---
    # For types like (Int × Nat) × Int:
    #   1. "⊢ (Int × Nat) × Int" → constructor
    #   2. "case fst ⊢ Int × Nat" → constructor
    #   3. "case fst.fst ⊢ Int" → exact expr
    #   4. "case fst.snd ⊢ Nat" → exact expr
    #   5. "case snd ⊢ Int" → exact expr

    for nested_type, (inner_product, leaf_type) in NESTED_PRODUCT_TYPES.items():
        inner_left, inner_right = PRODUCT_TYPES.get(inner_product, (None, None))
        if inner_left is None:
            continue

        for input_type in ["List Int", "Nat", "List (List Int)"]:
            left_left_pairs = by_sig.get((input_type, inner_left), [])
            left_right_pairs = by_sig.get((input_type, inner_right), [])
            right_pairs = by_sig.get((input_type, leaf_type), [])

            if not left_left_pairs or not left_right_pairs or not right_pairs:
                continue

            param = _param_prefix(input_type)

            # Step 1: outer constructor
            multistep.append(TrainingPair(
                state=f"{param}\n⊢ {nested_type}",
                tactic="constructor",
                source="multistep_depth3",
                task_name=f"constructor_nested_{nested_type}",
                return_type=nested_type,
                input_type=input_type,
            ))

            # Step 2: inner constructor (after case fst)
            for state_str in [
                f"case fst\n{param}\n⊢ {inner_product}",
                f"{param}\n⊢ {inner_product}",
            ]:
                multistep.append(TrainingPair(
                    state=state_str,
                    tactic="constructor",
                    source="multistep_depth3",
                    task_name=f"constructor_inner_{inner_product}",
                    return_type=inner_product,
                    input_type=input_type,
                ))

            # Step 3-5: leaf exact tactics
            ll_sample = rng.sample(left_left_pairs, min(20, len(left_left_pairs)))
            lr_sample = rng.sample(left_right_pairs, min(20, len(left_right_pairs)))
            r_sample = rng.sample(right_pairs, min(20, len(right_pairs)))

            for p in ll_sample:
                for state_str in [
                    f"case fst.fst\n{param}\n⊢ {inner_left}",
                    f"case fst\n{param}\n⊢ {inner_left}",
                    f"{param}\n⊢ {inner_left}",
                ]:
                    multistep.append(TrainingPair(
                        state=state_str,
                        tactic=p.tactic,
                        source="multistep_depth3",
                        task_name=f"leaf_fst_fst_{p.task_name}",
                        return_type=inner_left,
                        input_type=input_type,
                    ))

            for p in lr_sample:
                for state_str in [
                    f"case fst.snd\n{param}\n⊢ {inner_right}",
                    f"case snd\n{param}\n⊢ {inner_right}",
                    f"{param}\n⊢ {inner_right}",
                ]:
                    multistep.append(TrainingPair(
                        state=state_str,
                        tactic=p.tactic,
                        source="multistep_depth3",
                        task_name=f"leaf_fst_snd_{p.task_name}",
                        return_type=inner_right,
                        input_type=input_type,
                    ))

            for p in r_sample:
                for state_str in [
                    f"case snd\n{param}\n⊢ {leaf_type}",
                    f"{param}\n⊢ {leaf_type}",
                ]:
                    multistep.append(TrainingPair(
                        state=state_str,
                        tactic=p.tactic,
                        source="multistep_depth3",
                        task_name=f"leaf_snd_{p.task_name}",
                        return_type=leaf_type,
                        input_type=input_type,
                    ))

    depth3_ms = len(multistep) - depth2_ms
    if verbose:
        print(f"Depth-3 nested multi-step pairs: {depth3_ms}")
        print(f"Total multi-step pairs: {len(multistep)}")

    return multistep


# ---------------------------------------------------------------------------
# Phase 4: Optional verification via daemon
# ---------------------------------------------------------------------------

def verify_pairs_via_daemon(
    pairs: list[TrainingPair],
    daemon_port: int = 8765,
    batch_size: int = 50,
    verbose: bool = False,
) -> list[TrainingPair]:
    """Verify training pairs compile in Lean via Pantograph daemon.

    Only verifies 'exact' tactics (not 'constructor' which needs context).
    Returns only pairs that pass verification.
    """
    try:
        from lean_mcts.env.pantograph_client import PantographClient
    except ImportError:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from lean_mcts.env.pantograph_client import PantographClient

    client = PantographClient(port=daemon_port)

    exact_pairs = [p for p in pairs if p.tactic.startswith("exact ")]
    other_pairs = [p for p in pairs if not p.tactic.startswith("exact ")]

    if verbose:
        print(f"Verifying {len(exact_pairs)} exact pairs ({len(other_pairs)} non-exact skipped)...")

    verified = list(other_pairs)  # constructor pairs are assumed valid
    failed = 0

    for batch_start in range(0, len(exact_pairs), batch_size):
        batch = exact_pairs[batch_start:batch_start + batch_size]

        # Build verification code for this batch
        lines = [
            "import Mathlib.Tactic",
            "import Mathlib.Data.List.Sort",
            "import Mathlib.Data.List.Basic",
            "import Mathlib.Data.Int.Basic",
            "set_option linter.unusedVariables false",
            "",
        ]

        for i, p in enumerate(batch):
            expr = p.tactic.removeprefix("exact ")
            if p.input_type == "List (List Int)":
                params = "(xss : List (List Int))"
            elif p.input_type == "Nat":
                params = "(n : Nat)"
            elif p.input_type == "List Int, List Int":
                params = "(xs : List Int) (ys : List Int)"
            else:
                params = f"(xs : {p.input_type})"

            lines.append(
                f"def _v{batch_start + i} {params} : {p.return_type} := by"
            )
            lines.append(f"  exact {expr}")
            lines.append("")

        code = "\n".join(lines)

        try:
            units = client.check_compile(code)
            # Check for errors per definition
            has_error = False
            for cu in units:
                for msg in cu.get("messages", []):
                    if msg.get("severity") == "ERROR":
                        has_error = True
                        break

            if has_error:
                # Fall back to individual verification
                for p in batch:
                    if _verify_single(p, client):
                        verified.append(p)
                    else:
                        failed += 1
                        if verbose:
                            print(f"  FAILED: {p.tactic[:60]}...")
            else:
                verified.extend(batch)
        except Exception as e:
            if verbose:
                print(f"  Batch verification error: {e}")
            # Skip batch on error
            failed += len(batch)

    if verbose:
        print(f"Verified: {len(verified)}, Failed: {failed}")

    return verified


def _verify_single(pair: TrainingPair, client) -> bool:
    """Verify a single training pair compiles."""
    expr = pair.tactic.removeprefix("exact ")
    if pair.input_type == "List (List Int)":
        params = "(xss : List (List Int))"
    elif pair.input_type == "Nat":
        params = "(n : Nat)"
    elif pair.input_type == "List Int, List Int":
        params = "(xs : List Int) (ys : List Int)"
    else:
        params = f"(xs : {pair.input_type})"

    code = (
        "import Mathlib.Tactic\n"
        "import Mathlib.Data.List.Sort\n"
        "import Mathlib.Data.List.Basic\n"
        "import Mathlib.Data.Int.Basic\n"
        "set_option linter.unusedVariables false\n\n"
        f"def _vsingle {params} : {pair.return_type} := by\n"
        f"  exact {expr}\n"
    )

    try:
        units = client.check_compile(code)
        for cu in units:
            for msg in cu.get("messages", []):
                if msg.get("severity") == "ERROR":
                    return False
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def print_stats(
    candidates: list[dict],
    single_pairs: list[TrainingPair],
    multistep_pairs: list[TrainingPair],
    augmented_pairs: list[TrainingPair],
):
    """Print detailed statistics about generated data."""
    print("\n=== Compositional Data Generation Stats ===\n")

    # By depth
    by_depth = defaultdict(int)
    for c in candidates:
        by_depth[c["depth"]] += 1
    print("Compositions by depth:")
    for d in sorted(by_depth):
        print(f"  Depth {d}: {by_depth[d]}")
    print(f"  Total: {len(candidates)}")

    # By type signature
    print("\nCompositions by type signature:")
    by_sig = defaultdict(lambda: defaultdict(int))
    for c in candidates:
        sig = f"{c['input_type']} → {c['return_type']}"
        by_sig[sig][c["depth"]] += 1
    for sig in sorted(by_sig):
        depths = by_sig[sig]
        parts = [f"d{d}={n}" for d, n in sorted(depths.items())]
        total = sum(depths.values())
        print(f"  {sig}: {total} ({', '.join(parts)})")

    # Training pairs
    print(f"\nSingle-step training pairs: {len(single_pairs)}")
    print(f"Multi-step training pairs: {len(multistep_pairs)}")

    ms_by_source = defaultdict(int)
    for p in multistep_pairs:
        ms_by_source[p.source] += 1
    for src, n in sorted(ms_by_source.items()):
        print(f"  {src}: {n}")

    print(f"Augmented pairs: {len(augmented_pairs)}")

    total = len(single_pairs) + len(multistep_pairs) + len(augmented_pairs)
    print(f"\nTotal before dedup: {total}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate compositional training data for Sprint 23"
    )
    parser.add_argument(
        "--expressions", type=str, default=None,
        help="Path to expressions.jsonl (default: auto-detect)"
    )
    parser.add_argument(
        "--max-depth", type=int, default=3,
        help="Maximum composition depth (1-3)"
    )
    parser.add_argument(
        "--max-depth3-per-sig", type=int, default=50,
        help="Max depth-3 compositions per type signature (controls total size)"
    )
    parser.add_argument(
        "--n-augment", type=int, default=3,
        help="Number of variable name variants per pair"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output JSONL path (default: train/compositional_pairs.jsonl)"
    )
    parser.add_argument(
        "--output-train", type=str, default=None,
        help="Output train JSONL (default: train/goal_cond_s23_train.jsonl)"
    )
    parser.add_argument(
        "--output-val", type=str, default=None,
        help="Output val JSONL (default: train/goal_cond_s23_val.jsonl)"
    )
    parser.add_argument(
        "--merge-existing", type=str, default=None,
        help="Merge with existing training data (path to train.jsonl)"
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Verify pairs via Pantograph daemon"
    )
    parser.add_argument(
        "--daemon-port", type=int, default=8765,
        help="Pantograph daemon port"
    )
    parser.add_argument(
        "--stats-only", action="store_true",
        help="Print statistics without writing output"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent.parent
    if args.expressions is None:
        args.expressions = str(base_dir / "synth" / "expressions.jsonl")
    if args.output is None:
        args.output = str(base_dir / "train" / "compositional_pairs.jsonl")
    if args.output_train is None:
        args.output_train = str(base_dir / "train" / "goal_cond_s23_train.jsonl")
    if args.output_val is None:
        args.output_val = str(base_dir / "train" / "goal_cond_s23_val.jsonl")

    t0 = time.time()

    # Step 1: Load fragment catalog
    print(f"Loading fragments from {args.expressions}...")
    catalog = FragmentCatalog(args.expressions)
    print(f"  {len(catalog)} fragments loaded")

    # Step 2: Enumerate compositions
    print(f"\nEnumerating compositions (max depth {args.max_depth})...")
    candidates = enumerate_compositions(
        catalog, max_depth=args.max_depth,
        max_depth3_per_sig=args.max_depth3_per_sig,
        seed=args.seed, verbose=args.verbose,
    )
    print(f"  {len(candidates)} unique compositions found")

    # Step 3: Convert to training pairs
    single_pairs = candidates_to_pairs(candidates)

    # Step 4: Generate multi-step product decompositions
    print("\nGenerating multi-step decomposition pairs...")
    multistep_pairs = generate_multistep_pairs(
        single_pairs, verbose=args.verbose
    )
    print(f"  {len(multistep_pairs)} multi-step pairs generated")

    # Step 5: Combine and augment
    all_base_pairs = single_pairs + multistep_pairs
    print(f"\nAugmenting {len(all_base_pairs)} base pairs ({args.n_augment} variants each)...")
    augmented = augment_pairs(all_base_pairs, n_variants=args.n_augment, seed=args.seed)
    print(f"  {len(augmented)} augmented pairs")

    all_pairs = all_base_pairs + augmented

    # Step 6: Optional verification
    if args.verify:
        print("\nVerifying via daemon...")
        all_pairs = verify_pairs_via_daemon(
            all_pairs, daemon_port=args.daemon_port, verbose=args.verbose
        )

    # Step 7: Stats
    print_stats(candidates, single_pairs, multistep_pairs, augmented)

    if args.stats_only:
        print(f"\nDone in {time.time() - t0:.1f}s (stats only, no output written)")
        return

    # Step 8: Write raw compositional pairs
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for p in all_pairs:
            record = {
                "state": p.state,
                "tactic": p.tactic,
                "source": p.source,
                "input_type": p.input_type,
                "return_type": p.return_type,
            }
            f.write(json.dumps(record) + "\n")
    print(f"\nWrote {len(all_pairs)} pairs to {output_path}")

    # Step 9: Merge with existing data and write train/val split
    existing_pairs: list[TrainingPair] = []
    if args.merge_existing:
        existing_path = Path(args.merge_existing)
        if existing_path.exists():
            print(f"\nMerging with existing data from {existing_path}...")
            # Also load the val file (assumes it's alongside train)
            val_path = existing_path.parent / existing_path.name.replace(
                "train", "val"
            )
            for p in _load_jsonl_pairs(existing_path):
                existing_pairs.append(p)
            if val_path.exists():
                for p in _load_jsonl_pairs(val_path):
                    existing_pairs.append(p)
            print(f"  Loaded {len(existing_pairs)} existing pairs")

    merged = existing_pairs + all_pairs
    n_train, n_val = write_training_data(
        merged,
        Path(args.output_train),
        Path(args.output_val),
        split_ratio=0.8,
        seed=args.seed,
    )
    print(f"Wrote {n_train} train + {n_val} val = {n_train + n_val} total")
    print(f"  Train: {args.output_train}")
    print(f"  Val: {args.output_val}")

    print(f"\nDone in {time.time() - t0:.1f}s")


def _load_jsonl_pairs(path: Path) -> list[TrainingPair]:
    """Load training pairs from JSONL."""
    pairs = []
    with open(path) as f:
        for line in f:
            record = json.loads(line)
            pairs.append(TrainingPair(
                state=record["state"],
                tactic=record["tactic"],
                source=record.get("source", "existing"),
                task_name=record.get("task_name", "existing"),
                return_type=record.get("return_type", ""),
                input_type=record.get("input_type", ""),
            ))
    return pairs


if __name__ == "__main__":
    main()
