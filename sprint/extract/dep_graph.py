"""
dep_graph.py — Hackage package dependency graph analyser.

Three-phase design:
  1. fetch_direct_deps()  — call cabal info for each package, store results
  2. build_graph()        — construct forward + reverse adjacency from stored deps
  3. full_deps()          — transitive closure for a single package

CLI modes:
  python dep_graph.py                   # cascade report (auto-detect roots from corpus)
  python dep_graph.py --roots tagged    # explicit roots
  python dep_graph.py --query tagged    # full transitive closure for one package
  python dep_graph.py --fetch           # re-fetch cabal info (refresh cache)
  python dep_graph.py --json            # machine-readable output
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR   = Path(__file__).parent
PACKAGES_TXT = SCRIPT_DIR / "packages.txt"
CACHE_FILE   = SCRIPT_DIR / ".dep_cache.json"
CORPUS_FILE  = SCRIPT_DIR / "corpus_hackage.json"

# GHC boot packages — always present, never a cascade root
BOOT_PACKAGES = frozenset({
    "base", "ghc-prim", "integer-gmp", "integer-simple", "integer-bignum",
    "template-haskell", "ghc-bignum", "deepseq", "array", "binary",
    "bytestring", "containers", "filepath", "directory", "process",
    "time", "unix", "Win32", "stm", "transformers", "mtl",
    "text", "pretty", "parsec", "random", "Cabal", "hpc",
    "terminfo", "xhtml", "haskell98", "haskell2010",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_packages(path: Path) -> list[str]:
    pkgs = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            pkgs.append(line)
    return pkgs


def load_cache(path: Path) -> dict[str, list[str]]:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_cache(path: Path, cache: dict[str, list[str]]) -> None:
    path.write_text(json.dumps(cache, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# Phase 1 — Fetch direct dependencies
# ---------------------------------------------------------------------------

def _cabal_direct_deps(pkg: str) -> list[str]:
    """
    Call `cabal info <pkg>` and return its direct non-boot dependencies.
    Returns an empty list if cabal info fails or the package is not found.
    """
    try:
        result = subprocess.run(
            ["cabal", "info", pkg],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    # The Dependencies: section may span multiple lines until the next
    # section header (a line starting with a capital letter at low indent).
    dep_match = re.search(
        r"Dependencies:\s+(.*?)(?=\n\s{0,4}[A-Z]|\Z)", result.stdout, re.DOTALL
    )
    if not dep_match:
        return []

    dep_str = dep_match.group(1)
    names = re.findall(r"([a-zA-Z][a-zA-Z0-9_-]*)\s*(?:>=|<=|==|>|<|\[)", dep_str)
    return sorted(set(names) - BOOT_PACKAGES)


def fetch_direct_deps(
    packages: list[str],
    cache: dict[str, list[str]],
    refresh: bool = False,
    verbose: bool = True,
) -> dict[str, list[str]]:
    """
    Phase 1: for each package in `packages`, retrieve its direct (non-boot)
    dependencies via `cabal info`. Results are stored in `cache` and written
    to CACHE_FILE.

    Returns: {pkg -> [direct dep, ...]}
    """
    result: dict[str, list[str]] = {}
    dirty = False

    for i, pkg in enumerate(packages):
        if not refresh and pkg in cache:
            result[pkg] = cache[pkg]
            continue

        if verbose:
            print(f"  [{i+1}/{len(packages)}] cabal info {pkg} ...", end="\r", flush=True)

        deps = _cabal_direct_deps(pkg)
        result[pkg] = deps
        cache[pkg] = deps
        dirty = True

    if verbose:
        print(" " * 60, end="\r")

    if dirty:
        save_cache(CACHE_FILE, cache)

    return result


# ---------------------------------------------------------------------------
# Phase 2 — Build dependency graph
# ---------------------------------------------------------------------------

class DepGraph:
    """
    Directed dependency graph built from direct-dep lists.

    forward[pkg]  = set of packages that pkg directly depends on
    reverse[dep]  = set of packages that directly depend on dep
    nodes         = all packages that appear as keys in the input
    """

    def __init__(self, direct_deps: dict[str, list[str]]) -> None:
        self.nodes: set[str] = set(direct_deps)
        self.forward: dict[str, set[str]] = {
            pkg: set(deps) for pkg, deps in direct_deps.items()
        }
        self.reverse: dict[str, set[str]] = defaultdict(set)
        for pkg, deps in self.forward.items():
            for dep in deps:
                self.reverse[dep].add(pkg)


def build_graph(direct_deps: dict[str, list[str]]) -> DepGraph:
    """
    Phase 2: construct a DepGraph from the dict produced by fetch_direct_deps.
    Pure data structure construction — no I/O.
    """
    return DepGraph(direct_deps)


# ---------------------------------------------------------------------------
# Build-order computation (topological sort + wave decomposition)
# ---------------------------------------------------------------------------

def build_waves(graph: DepGraph, packages: list[str]) -> list[list[str]]:
    """
    Kahn's algorithm restricted to `packages`.

    Returns a list of waves where:
      - All packages in wave[i] can be built in parallel.
      - Wave[i+1] starts only after wave[i] is complete (all its packages
        are in the cabal store).
      - Within each wave, packages are sorted alphabetically for determinism.

    Packages whose dependencies (within the known set) form a cycle are
    placed in a final 'fallback' wave rather than silently dropped.
    """
    pkg_set = set(packages)

    # In-degree: number of dependencies that are also in our package list
    in_degree: dict[str, int] = {}
    for pkg in packages:
        in_degree[pkg] = len(graph.forward.get(pkg, set()) & pkg_set)

    waves: list[list[str]] = []
    remaining = set(packages)

    while remaining:
        # Packages with no unsatisfied intra-list dependencies
        ready = sorted(p for p in remaining if in_degree[p] == 0)
        if not ready:
            # Cycle or all remaining have unresolved deps (boot pkgs, etc.)
            # Put the rest in one final wave — cabal will handle ordering.
            waves.append(sorted(remaining))
            break
        waves.append(ready)
        for pkg in ready:
            remaining.remove(pkg)
            # Decrease in-degree of packages that depend on this one
            for dependent in graph.reverse.get(pkg, set()):
                if dependent in remaining:
                    in_degree[dependent] = max(0, in_degree[dependent] - 1)

    return waves


def topological_order(graph: DepGraph, packages: list[str]) -> list[str]:
    """
    Flat topological order (dependencies before dependents).
    Equivalent to concatenating build_waves() into one list.
    """
    result = []
    for wave in build_waves(graph, packages):
        result.extend(wave)
    return result


# ---------------------------------------------------------------------------
# Phase 3 — Transitive closure for a single package
# ---------------------------------------------------------------------------

def full_deps(graph: DepGraph, pkg: str) -> set[str]:
    """
    Phase 3 (forward): return every package that `pkg` transitively depends on.
    Only traverses nodes present in graph.nodes (i.e. our known package list).
    """
    visited: set[str] = set()
    queue: deque[str] = deque([pkg])
    while queue:
        node = queue.popleft()
        for dep in graph.forward.get(node, set()):
            if dep not in visited and dep in graph.nodes:
                visited.add(dep)
                queue.append(dep)
    visited.discard(pkg)
    return visited


def full_dependents(graph: DepGraph, pkg: str) -> set[str]:
    """
    Phase 3 (reverse): return every package that transitively depends on `pkg`.
    Only traverses nodes present in graph.nodes.
    """
    visited: set[str] = set()
    queue: deque[str] = deque([pkg])
    while queue:
        node = queue.popleft()
        for dependent in graph.reverse.get(node, set()):
            if dependent not in visited and dependent in graph.nodes:
                visited.add(dependent)
                queue.append(dependent)
    visited.discard(pkg)
    return visited


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def detect_failed(packages: list[str]) -> set[str]:
    """Packages that produced 0 functions — failed extraction."""
    if not CORPUS_FILE.exists():
        return set()
    data = json.loads(CORPUS_FILE.read_text())
    succeeded = {fn.get("package") for fn in data if fn.get("package")}
    return set(packages) - succeeded


def detect_roots(
    failed: set[str],
    graph: DepGraph,
) -> set[str]:
    """
    A failed package is a root if none of its direct dependencies
    are also failed packages within our known set.
    """
    roots = set()
    for pkg in failed:
        failed_deps = graph.forward.get(pkg, set()) & failed
        if not failed_deps:
            roots.add(pkg)
    return roots


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def run_report(
    packages: list[str],
    graph: DepGraph,
    failed: set[str],
    roots: set[str],
    query_pkg: str | None = None,
    as_json: bool = False,
) -> None:

    # For each root: which failed non-root packages does it transitively block?
    cascade_victims = failed - roots
    root_impact: dict[str, dict] = {}
    for root in roots:
        dependents = full_dependents(graph, root)
        blocked = dependents & cascade_victims
        direct  = graph.reverse.get(root, set()) & cascade_victims
        root_impact[root] = {
            "direct":     sorted(direct),
            "transitive": sorted(blocked),
            "count":      len(blocked),
        }

    # Which roots block each victim?
    victim_roots: dict[str, set[str]] = defaultdict(set)
    for root, info in root_impact.items():
        for pkg in info["transitive"]:
            victim_roots[pkg].add(root)

    multi_root = {
        pkg: sorted(rs)
        for pkg, rs in victim_roots.items()
        if len(rs) > 1
    }

    # Unexplained: failed, not a root, not reachable from any root
    covered = {pkg for info in root_impact.values() for pkg in info["transitive"]}
    unexplained = failed - roots - covered

    if as_json:
        print(json.dumps({
            "packages_total": len(packages),
            "failed": sorted(failed),
            "roots": sorted(roots),
            "root_impact": {r: {**i, "exclusive": [
                p for p in i["transitive"] if len(victim_roots[p]) == 1
            ]} for r, i in root_impact.items()},
            "multi_root_victims": multi_root,
            "unexplained": sorted(unexplained),
        }, indent=2))
        return

    print(f"\n{'='*62}")
    print(f"  DEPENDENCY CASCADE REPORT")
    print(f"  packages.txt : {len(packages)} listed")
    print(f"  Failed       : {len(failed)}")
    print(f"  Roots        : {len(roots)}   Cascade victims: {len(cascade_victims)}")
    print(f"{'='*62}\n")

    # Roots sorted by impact descending, then isolates at end
    impactful = sorted(
        [(r, i) for r, i in root_impact.items() if i["count"] > 0],
        key=lambda x: -x[1]["count"],
    )
    isolates = [r for r, i in root_impact.items() if i["count"] == 0]

    print("── ROOT FAILURES (with cascade impact) ──\n")
    for root, info in impactful:
        exclusive = [p for p in info["transitive"] if len(victim_roots[p]) == 1]
        print(f"  {root}")
        print(f"    direct blocked  : {len(info['direct'])}")
        print(f"    total blocked   : {info['count']}  (exclusive: {len(exclusive)})")
        preview = ", ".join(info["direct"][:5])
        if len(info["direct"]) > 5:
            preview += f" +{len(info['direct'])-5} more"
        if preview:
            print(f"    direct deps     : {preview}")
        print()

    if isolates:
        print(f"── ISOLATED ROOTS ({len(isolates)} — fail alone, block nothing) ──\n")
        print("  " + ", ".join(sorted(isolates)))
        print()

    if multi_root:
        print(f"── MULTI-ROOT VICTIMS ({len(multi_root)}) ──\n")
        for pkg, rs in sorted(multi_root.items(), key=lambda x: (-len(x[1]), x[0])):
            print(f"  {pkg:<35} needs: {', '.join(rs)}")
        print()

    if unexplained:
        print(f"── UNEXPLAINED FAILURES ({len(unexplained)}) ──\n")
        for pkg in sorted(unexplained):
            failed_direct = graph.forward.get(pkg, set()) & failed
            print(f"  {pkg:<35} failed deps in list: {', '.join(sorted(failed_direct)) or 'none'}")
        print()

    print("── SUMMARY TABLE ──\n")
    print(f"  {'Root':<32} {'Direct':>7} {'Total':>7} {'Exclusive':>10}")
    print(f"  {'-'*32} {'-'*7} {'-'*7} {'-'*10}")
    for root, info in impactful:
        exclusive = sum(1 for p in info["transitive"] if len(victim_roots[p]) == 1)
        print(f"  {root:<32} {len(info['direct']):>7} {info['count']:>7} {exclusive:>10}")
    print()

    # Query: full transitive closure for a single package
    if query_pkg:
        print(f"── QUERY: full_deps('{query_pkg}') ──\n")
        deps = full_deps(graph, query_pkg)
        dependents = full_dependents(graph, query_pkg)

        failed_deps = deps & failed
        failed_dependents = dependents & failed

        print(f"  Packages it depends on (transitive, in our list): {len(deps)}")
        if failed_deps:
            print(f"  ↳ of which FAILED: {', '.join(sorted(failed_deps))}")

        print(f"\n  Packages that depend on it (transitive, in our list): {len(dependents)}")
        if failed_dependents:
            print(f"  ↳ of which FAILED (currently blocked): {len(failed_dependents)}")
            for p in sorted(failed_dependents):
                roots_needed = sorted(victim_roots.get(p, set()))
                tag = f"  roots: {', '.join(roots_needed)}" if roots_needed else ""
                print(f"      {p}{tag}")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hackage dependency cascade analyser"
    )
    parser.add_argument("--packages", default=str(PACKAGES_TXT))
    parser.add_argument("--roots", nargs="+", metavar="PKG",
                        help="Explicit root failures (default: auto-detect)")
    parser.add_argument("--failed", nargs="+", metavar="PKG",
                        help="Explicit failed packages (default: auto-detect from corpus)")
    parser.add_argument("--query", metavar="PKG",
                        help="Show full transitive dep closure for this package")
    parser.add_argument("--fetch", action="store_true",
                        help="Re-fetch cabal info for all packages (ignore cache)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--order", metavar="OUT",
                        help="Write build-order JSON to OUT and exit "
                             "(waves + flat list; consumed by extract_hackage.py)")
    args = parser.parse_args()

    packages = load_packages(Path(args.packages))
    verbose = not args.quiet and not args.json and not args.order

    if verbose:
        print(f"[dep_graph] {len(packages)} packages in {args.packages}")
        print(f"[dep_graph] phase 1: fetching direct deps ...")

    # Phase 1
    cache = load_cache(CACHE_FILE)
    direct_deps = fetch_direct_deps(packages, cache, refresh=args.fetch, verbose=verbose)

    # Phase 2
    graph = build_graph(direct_deps)

    if verbose:
        print(f"[dep_graph] phase 2: graph built — {len(graph.nodes)} nodes")

    # --order mode: emit build plan and exit
    if args.order:
        waves = build_waves(graph, packages)
        flat = topological_order(graph, packages)
        out = {
            "packages_total": len(packages),
            "waves_total": len(waves),
            "waves": waves,
            "flat": flat,
        }
        Path(args.order).write_text(json.dumps(out, indent=2))
        wave_sizes = [len(w) for w in waves]
        print(f"[dep_graph] {len(packages)} packages → {len(waves)} waves "
              f"(sizes: {wave_sizes[:8]}{'...' if len(waves) > 8 else ''})",
              file=sys.stderr)
        print(f"[dep_graph] wrote build order → {args.order}", file=sys.stderr)
        return

    # Determine failed / roots
    failed = set(args.failed) if args.failed else detect_failed(packages)
    roots  = set(args.roots)  if args.roots  else detect_roots(failed, graph)

    if verbose:
        print(f"[dep_graph] failed: {len(failed)}  roots: {len(roots)}")

    run_report(
        packages=packages,
        graph=graph,
        failed=failed,
        roots=roots,
        query_pkg=args.query,
        as_json=args.json,
    )


if __name__ == "__main__":
    main()
