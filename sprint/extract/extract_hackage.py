#!/usr/bin/env python3
"""
extract_hackage.py — Batch Hackage extraction pipeline.

Three explicit phases with per-package intermediate files:

  Phase 1  build  → sprint/extract/pkg_raw/<pkg>.json
    cabal unpack → build → parse GHC Core dumps
    Stores raw (pre-clean) parse output per package.
    Re-runs skip already-cached packages (--force to override).

  Phase 2  clean  → sprint/extract/pkg_clean/<pkg>.json
    Read pkg_raw/<pkg>.json → apply clean.py → write pkg_clean/<pkg>.json
    Re-runs skip already-cleaned packages (--force to override).
    Re-run this phase alone when clean.py changes.

  Phase 3  corpus → sprint/extract/corpus_hackage.json
    Read all pkg_clean/<pkg>.json → apply size filter + dedup → final corpus.
    Always re-runs (fast, just merges pre-cleaned files).

Because every worker writes its own file there is no shared mutable state
during parallel extraction — no JSONL corruption, no race conditions.

Usage:
    # Full pipeline (all three phases):
    python sprint/extract/extract_hackage.py \\
        --packages-file sprint/extract/packages.txt \\
        --work-dir hackage-packages \\
        --pkg-raw-dir sprint/extract/pkg_raw \\
        --pkg-clean-dir sprint/extract/pkg_clean \\
        --out sprint/extract/corpus_hackage.json \\
        --parallel 4 --stats

    # Re-run only the clean phase (e.g. after editing clean.py):
    python sprint/extract/extract_hackage.py \\
        --packages-file sprint/extract/packages.txt \\
        --pkg-raw-dir sprint/extract/pkg_raw \\
        --pkg-clean-dir sprint/extract/pkg_clean \\
        --out sprint/extract/corpus_hackage.json \\
        --phase clean --force

    # Re-assemble corpus with new token limits:
    python sprint/extract/extract_hackage.py \\
        --packages-file sprint/extract/packages.txt \\
        --pkg-clean-dir sprint/extract/pkg_clean \\
        --out sprint/extract/corpus_hackage.json \\
        --min-tokens 5 --max-tokens 500 --phase corpus
"""

import json
import sys
import argparse
import subprocess
import hashlib
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

# Add sprint/extract to path for sibling imports
sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_ghc_core import split_bindings, parse_binding
from clean import clean_function, validate_no_ghc_noise

# ── Constants ────────────────────────────────────────────────────────────────

GHC_DUMP_FLAGS = (
    "-O0 -fforce-recomp -ddump-simpl -ddump-to-file "
    "-dsuppress-idinfo -dsuppress-module-prefixes "
    "-dsuppress-uniques -dsuppress-coercions"
    # NOTE: -dynamic is NOT passed here; cabal.config sets library-vanilla:False
    # and shared:True globally, which makes cabal use -dynamic internally and
    # produce .dyn_o + .dyn.dump-simpl files without -dynamic-too interference.
)

# ── Package list loading ─────────────────────────────────────────────────────

def load_packages(args) -> list[str]:
    """Load package names from CLI args or file."""
    pkgs = []
    if args.packages:
        pkgs.extend(args.packages)
    if args.packages_file:
        text = Path(args.packages_file).read_text()
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pkgs.append(line)
    return pkgs


# ── Atomic cache helpers ─────────────────────────────────────────────────────

def _write_cache(cache_file: Path, data: dict) -> None:
    """Atomically write JSON to cache_file via a .tmp rename."""
    tmp = cache_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data))
    tmp.rename(cache_file)  # atomic on POSIX


def _load_cache(cache_file: Path) -> dict | None:
    """Load JSON from cache_file. Returns None if missing or corrupt."""
    if not cache_file.exists():
        return None
    try:
        return json.loads(cache_file.read_text())
    except Exception as e:
        print(f"[extract-hackage] WARN: corrupt cache {cache_file.name}: {e}", file=sys.stderr)
        return None


# ── Core pipeline functions ──────────────────────────────────────────────────

def unpack_package(pkg: str, work_dir: Path) -> Path | None:
    """cabal unpack pkg into work_dir. Returns package directory or None.

    Reuses an existing directory if one is present from a prior run.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(work_dir.glob(f"{pkg}-*/"), key=lambda p: p.stat().st_mtime, reverse=True)
    if existing:
        return existing[0]

    r = subprocess.run(
        ["cabal", "unpack", pkg, "--destdir", str(work_dir)],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        print(f"[extract-hackage] WARN: cabal unpack {pkg} failed: {r.stderr.strip()[:200]}",
              file=sys.stderr)
        return None
    for line in r.stdout.splitlines():
        if "Unpacking to" in line or "unpacking to" in line.lower():
            dirname = line.split()[-1].rstrip("/")
            candidate = work_dir / dirname
            if candidate.is_dir():
                return candidate
    candidates = sorted(work_dir.glob(f"{pkg}-*/"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def build_package(pkg_dir: Path) -> tuple[bool, str | None]:
    """cabal build with dump flags. Returns (build_ok, error_snippet)."""
    r = subprocess.run(
        ["cabal", "build", "all", f"--ghc-options={GHC_DUMP_FLAGS}"],
        capture_output=True, text=True, timeout=600,
        cwd=str(pkg_dir),
    )
    if r.returncode != 0:
        stderr_short = r.stderr.strip()[-500:] if r.stderr else "(no stderr)"
        print(f"[extract-hackage] WARN: cabal build in {pkg_dir.name} exited {r.returncode}",
              file=sys.stderr)
        print(f"  {stderr_short[:200]}", file=sys.stderr)
        return False, stderr_short[-200:]
    return True, None


def find_dump_files(pkg_dir: Path) -> list[Path]:
    """Find all .dump-simpl files under dist-newstyle/."""
    dist = pkg_dir / "dist-newstyle"
    if not dist.exists():
        return []
    return sorted(dist.rglob("*.dump-simpl"))


def should_keep_binding(name: str) -> bool:
    """Filter binding names: skip GHC internals, class instances, primops, type constructors."""
    if not name:
        return False
    if name.startswith("$"):   return False  # class instances / evidence
    if "#" in name:            return False  # primops
    if name[0].isupper():      return False  # type constructors
    if name.startswith("__"):  return False  # GHC internals
    return True


def parse_module(dump_path: Path, pkg_name: str) -> tuple[list[dict], dict]:
    """Parse one dump-simpl file. Returns (raw_entries, stats).

    raw_entries are the output of parse_binding — raw GHC Core ASTs with
    fields: name, type, recursive, body (raw AST), ok, package.
    No cleaning is applied here.
    """
    stats = {"bindings": 0, "parsed": 0}
    entries = []

    try:
        text = dump_path.read_text(errors="replace")
    except Exception as e:
        print(f"[extract-hackage] WARN: can't read {dump_path}: {e}", file=sys.stderr)
        return entries, stats

    raw_bindings = split_bindings(text)
    raw_bindings = [b for b in raw_bindings if should_keep_binding(b["name"])]
    stats["bindings"] = len(raw_bindings)

    for b in raw_bindings:
        try:
            import signal
            def _alarm(*_): raise TimeoutError()
            old = signal.signal(signal.SIGALRM, _alarm)
            signal.alarm(5)
            parsed = parse_binding(b)
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
        except TimeoutError:
            signal.alarm(0)
            print(f"[extract-hackage] WARN: parse timeout on {b.get('name','?')} in {dump_path.name}",
                  file=sys.stderr)
            continue
        except Exception:
            continue
        if not parsed.get("ok"):
            continue
        stats["parsed"] += 1
        parsed["package"] = pkg_name  # attach package name for phase 2
        entries.append(parsed)

    return entries, stats


def _count_nodes(root) -> int:
    """Count AST nodes iteratively (avoids recursion limit on deep trees).
    Dicts count as 1 node each; lists and scalars just contribute their children/value."""
    stack = [root]
    count = 0
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            count += 1
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        # scalars (str, int, bool, None) are leaf values — not counted as nodes
    return count


def filter_by_size(entries: list[dict], min_tokens: int, max_tokens: int) -> list[dict]:
    """Keep only entries whose clean IR body has between min and max AST nodes."""
    kept = []
    for e in entries:
        n = _count_nodes(e["body"])
        if min_tokens <= n <= max_tokens:
            kept.append(e)
    return kept


def dedup_entries(entries: list[dict]) -> list[dict]:
    """Remove entries with identical AST bodies (keep first occurrence)."""
    seen: set[str] = set()
    out = []
    for e in entries:
        key = hashlib.md5(json.dumps(e["body"], sort_keys=True).encode()).hexdigest()
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def validate_roundtrip(entry: dict) -> bool:
    """Run the entry through pretty-print → runhaskell to check it executes."""
    try:
        from benchmark.pretty_print import ast_to_haskell
        hs = ast_to_haskell(entry)
        r = subprocess.run(
            ["runhaskell"], input=hs, capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


# ── Phase 1: build + parse → pkg_raw/ ────────────────────────────────────────

def extract_package_raw(pkg: str, work_dir: Path, pkg_raw_dir: Path,
                        force: bool = False) -> tuple[list[dict], dict]:
    """Phase 1: unpack → build → parse dumps → pkg_raw/<pkg>.json.

    Stores raw GHC Core ASTs (pre-clean). Skips if cached unless force=True.
    Returns (raw_entries, stats).
    """
    pkg_raw_dir.mkdir(parents=True, exist_ok=True)
    cache_file = pkg_raw_dir / f"{pkg}.json"

    if not force:
        data = _load_cache(cache_file)
        if data is not None:
            n = len(data.get("entries", []))
            print(f"[extract-hackage] {pkg}: raw cached ({n} bindings)", file=sys.stderr)
            return data.get("entries", []), data.get("stats", {"package": pkg})

    pkg_stats: dict = {
        "package": pkg, "status": "pending",
        "build_ok": None, "build_error": None,
        "modules": 0, "bindings": 0, "parsed": 0,
    }

    pkg_dir = unpack_package(pkg, work_dir)
    if pkg_dir is None:
        print(f"[extract-hackage] SKIP {pkg}: unpack failed", file=sys.stderr)
        pkg_stats["status"] = "unpack_failed"
        _write_cache(cache_file, {"package": pkg, "stats": pkg_stats, "entries": []})
        return [], pkg_stats

    build_ok, build_err = build_package(pkg_dir)
    pkg_stats["build_ok"]    = build_ok
    pkg_stats["build_error"] = build_err

    dumps = find_dump_files(pkg_dir)
    pkg_stats["modules"] = len(dumps)
    if not dumps:
        pkg_stats["status"] = "build_failed"
        print(f"[extract-hackage] SKIP {pkg}: no dump files found", file=sys.stderr)
        _write_cache(cache_file, {"package": pkg, "stats": pkg_stats, "entries": []})
        return [], pkg_stats

    all_entries: list[dict] = []
    for dump_path in dumps:
        mod_entries, mod_stats = parse_module(dump_path, pkg)
        all_entries.extend(mod_entries)
        pkg_stats["bindings"] += mod_stats["bindings"]
        pkg_stats["parsed"]   += mod_stats["parsed"]

    parse_rate = (pkg_stats["parsed"] / pkg_stats["bindings"] * 100
                  if pkg_stats["bindings"] > 0 else 0)
    pkg_stats["status"] = "parsed" if pkg_stats["parsed"] > 0 else "zero_parsed"
    print(f"[extract-hackage] {pkg}: {pkg_stats['parsed']} parsed from "
          f"{pkg_stats['modules']} modules ({parse_rate:.0f}%)", file=sys.stderr)

    _write_cache(cache_file, {"package": pkg, "stats": pkg_stats, "entries": all_entries})
    return all_entries, pkg_stats


# ── Phase 2: clean → pkg_clean/ ──────────────────────────────────────────────

def clean_package(pkg: str, pkg_raw_dir: Path, pkg_clean_dir: Path,
                  force: bool = False) -> tuple[list[dict], dict]:
    """Phase 2: read pkg_raw/<pkg>.json → apply clean.py → pkg_clean/<pkg>.json.

    Skips if pkg_clean/<pkg>.json already exists unless force=True.
    Re-run this phase alone when clean.py changes.
    Returns (clean_entries, stats).
    """
    pkg_clean_dir.mkdir(parents=True, exist_ok=True)
    clean_file = pkg_clean_dir / f"{pkg}.json"

    if not force:
        data = _load_cache(clean_file)
        if data is not None:
            n = len(data.get("entries", []))
            print(f"[extract-hackage] {pkg}: clean cached ({n} functions)", file=sys.stderr)
            return data.get("entries", []), data.get("stats", {"package": pkg})

    raw_data = _load_cache(pkg_raw_dir / f"{pkg}.json")
    if raw_data is None:
        print(f"[extract-hackage] WARN: no raw cache for {pkg}, skipping clean", file=sys.stderr)
        return [], {"package": pkg, "status": "no_raw"}

    raw_entries = raw_data.get("entries", [])
    raw_stats   = raw_data.get("stats", {})

    clean_entries: list[dict] = []
    cleaned_count = 0
    noise_free    = 0

    for entry in raw_entries:
        cleaned = clean_function(entry, package=pkg)
        if cleaned is None:
            continue
        cleaned_count += 1
        violations = validate_no_ghc_noise([cleaned])
        if violations:
            continue
        noise_free += 1
        clean_entries.append(cleaned)

    clean_stats: dict = {
        "package":    pkg,
        "status":     "extracted" if clean_entries else "zero_yield",
        "build_ok":   raw_stats.get("build_ok"),
        "build_error":raw_stats.get("build_error"),
        "modules":    raw_stats.get("modules", 0),
        "bindings":   raw_stats.get("bindings", 0),
        "parsed":     raw_stats.get("parsed", 0),
        "cleaned":    cleaned_count,
        "noise_free": noise_free,
        "final":      len(clean_entries),
    }

    parse_rate = (clean_stats["parsed"] / clean_stats["bindings"] * 100
                  if clean_stats["bindings"] > 0 else 0)
    print(f"[extract-hackage] {pkg}: {len(clean_entries)} clean functions "
          f"({clean_stats['parsed']}/{clean_stats['bindings']} parsed, {parse_rate:.0f}%)",
          file=sys.stderr)

    _write_cache(clean_file, {"package": pkg, "stats": clean_stats, "entries": clean_entries})
    return clean_entries, clean_stats


# ── Phase 3: corpus assembly ──────────────────────────────────────────────────

def assemble_corpus(packages: list[str], pkg_clean_dir: Path,
                    min_tokens: int, max_tokens: int,
                    do_validate: bool) -> tuple[list[dict], list[dict]]:
    """Phase 3: read pkg_clean/ → size filter → global dedup → corpus.

    Always runs (fast). Returns (all_entries, all_stats).
    """
    all_entries: list[dict] = []
    all_stats:   list[dict] = []

    for pkg in packages:
        data = _load_cache(pkg_clean_dir / f"{pkg}.json")
        if data is None:
            continue
        entries = data.get("entries", [])
        stats   = data.get("stats", {"package": pkg})
        entries = filter_by_size(entries, min_tokens, max_tokens)
        if do_validate:
            entries = [e for e in entries if validate_roundtrip(e)]
        all_entries.extend(entries)
        all_stats.append(stats)

    before = len(all_entries)
    all_entries = dedup_entries(all_entries)
    after = len(all_entries)
    if before != after:
        print(f"[extract-hackage] {before - after} cross-package duplicates removed",
              file=sys.stderr)

    return all_entries, all_stats


# ── Parallel orchestration ────────────────────────────────────────────────────

def _build_one(args_tuple):
    """Worker wrapper for Phase 1 (ProcessPoolExecutor)."""
    pkg, work_dir, pkg_raw_dir, force = args_tuple
    return extract_package_raw(pkg, work_dir, pkg_raw_dir, force)


def _clean_one(args_tuple):
    """Worker wrapper for Phase 2 (ProcessPoolExecutor)."""
    pkg, pkg_raw_dir, pkg_clean_dir, force = args_tuple
    return clean_package(pkg, pkg_raw_dir, pkg_clean_dir, force)


def _run_wave(wave: list[str], fn, args_fn, parallel: int) -> list[dict]:
    """Run one wave using fn applied to each package. Returns stats list."""
    wave_stats = []
    if parallel > 1 and len(wave) > 1:
        args_list = [args_fn(pkg) for pkg in wave]
        with ProcessPoolExecutor(max_workers=min(parallel, len(wave))) as pool:
            futures = {pool.submit(fn, a): a[0] for a in args_list}
            for future in as_completed(futures):
                pkg = futures[future]
                try:
                    _entries, stats = future.result()
                    wave_stats.append(stats)
                except Exception as e:
                    print(f"[extract-hackage] ERROR {pkg}: {e}", file=sys.stderr)
                    wave_stats.append({"package": pkg, "final": 0, "error": str(e)})
    else:
        for pkg in wave:
            try:
                _entries, stats = fn(args_fn(pkg))
                wave_stats.append(stats)
            except Exception as e:
                print(f"[extract-hackage] ERROR {pkg}: {e}", file=sys.stderr)
                wave_stats.append({"package": pkg, "final": 0, "error": str(e)})
    return wave_stats


def run_phase(packages: list[str], fn, args_fn,
              parallel: int, waves: list[list[str]] | None) -> list[dict]:
    """Run a phase over all packages, optionally wave-ordered. Returns all_stats."""
    all_stats: list[dict] = []
    if waves is not None:
        processed = 0
        for i, wave in enumerate(waves):
            pct = processed / len(packages) * 100 if packages else 0
            print(f"[extract-hackage] wave {i+1}/{len(waves)}: "
                  f"{len(wave)} packages ({pct:.0f}% done)", file=sys.stderr)
            all_stats.extend(_run_wave(wave, fn, args_fn, parallel))
            processed += len(wave)
    else:
        all_stats.extend(_run_wave(packages, fn, args_fn, parallel))
    return all_stats


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Batch Hackage extraction pipeline (3-phase)")
    p.add_argument("--packages", nargs="*", default=[])
    p.add_argument("--packages-file", default=None)
    p.add_argument("--work-dir", default="/tmp/hackage-extract",
                   help="Working directory for unpacked packages")
    p.add_argument("--pkg-raw-dir", default="sprint/extract/pkg_raw",
                   help="Phase 1 output: per-package raw GHC Core ASTs")
    p.add_argument("--pkg-clean-dir", default="sprint/extract/pkg_clean",
                   help="Phase 2 output: per-package clean IR entries")
    p.add_argument("--out", required=True,
                   help="Phase 3 output: final corpus JSON")
    p.add_argument("--merge", default=None,
                   help="Merge with existing corpus into a combined file")
    p.add_argument("--min-tokens", type=int, default=5)
    p.add_argument("--max-tokens", type=int, default=300)
    p.add_argument("--validate", action="store_true",
                   help="Run runhaskell round-trip validation (slow)")
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--limit", type=int, default=None,
                   help="Process only the first N packages (for batch testing)")
    p.add_argument("--force", action="store_true",
                   help="Re-run the selected phase(s) even if cache files exist")
    p.add_argument("--phase", choices=["build", "clean", "corpus", "all"], default="all",
                   help="Which phase(s) to run (default: all)")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--status-out", default=None,
                   help="Write per-package status JSON (for pkg_status.py)")
    p.add_argument("--build-order", default=None,
                   help="JSON file from 'dep_graph.py --order'; enables wave-by-wave extraction")
    args = p.parse_args()

    packages = load_packages(args)
    if args.limit is not None:
        packages = packages[:args.limit]
    if not packages:
        print("[extract-hackage] ERROR: no packages specified", file=sys.stderr)
        sys.exit(1)

    # Load build-order waves
    waves: list[list[str]] | None = None
    if args.build_order:
        order_path = Path(args.build_order)
        if not order_path.exists():
            print(f"[extract-hackage] ERROR: --build-order not found: {order_path}", file=sys.stderr)
            sys.exit(1)
        order_data = json.loads(order_path.read_text())
        pkg_set = set(packages)
        waves = [[p for p in w if p in pkg_set] for w in order_data["waves"]]
        waves = [w for w in waves if w]
        covered = {p for w in waves for p in w}
        uncovered = [p for p in packages if p not in covered]
        if uncovered:
            waves.append(uncovered)
        flat_order = [p for w in waves for p in w]
        print(f"[extract-hackage] build-order: {len(waves)} waves, {len(flat_order)} packages",
              file=sys.stderr)
    else:
        flat_order = packages

    work_dir      = Path(args.work_dir)
    pkg_raw_dir   = Path(args.pkg_raw_dir)
    pkg_clean_dir = Path(args.pkg_clean_dir)
    out_path      = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_stats: list[dict] = []

    # ── Phase 1: build + parse → pkg_raw/ ────────────────────────────────────
    if args.phase in ("build", "all"):
        print(f"\n[extract-hackage] Phase 1 (build): {len(flat_order)} packages ...",
              file=sys.stderr)
        build_stats = run_phase(
            flat_order,
            _build_one,
            lambda pkg: (pkg, work_dir, pkg_raw_dir, args.force),
            args.parallel,
            waves if args.phase == "all" else None,  # wave order only matters for builds
        )
        all_stats = build_stats
        pkgs_parsed = sum(1 for s in build_stats if s.get("parsed", 0) > 0)
        print(f"[extract-hackage] Phase 1 done: {pkgs_parsed}/{len(flat_order)} packages parsed",
              file=sys.stderr)

    # ── Phase 2: clean → pkg_clean/ ──────────────────────────────────────────
    if args.phase in ("clean", "all"):
        print(f"\n[extract-hackage] Phase 2 (clean): {len(flat_order)} packages ...",
              file=sys.stderr)
        clean_stats = run_phase(
            flat_order,
            _clean_one,
            lambda pkg: (pkg, pkg_raw_dir, pkg_clean_dir, args.force),
            args.parallel,
            None,  # cleaning is independent; no wave ordering needed
        )
        all_stats = clean_stats
        pkgs_extracted = sum(1 for s in clean_stats if s.get("final", 0) > 0)
        total_fns = sum(s.get("final", 0) for s in clean_stats)
        print(f"[extract-hackage] Phase 2 done: {total_fns} functions "
              f"from {pkgs_extracted}/{len(flat_order)} packages", file=sys.stderr)

    # ── Phase 3: corpus assembly ──────────────────────────────────────────────
    if args.phase in ("corpus", "all"):
        print(f"\n[extract-hackage] Phase 3 (corpus): assembling from pkg_clean/ ...",
              file=sys.stderr)
        all_entries, corpus_stats = assemble_corpus(
            flat_order, pkg_clean_dir,
            args.min_tokens, args.max_tokens,
            args.validate,
        )
        if args.phase == "corpus":
            all_stats = corpus_stats

        out_path.write_text(json.dumps(all_entries, indent=2))
        print(f"[extract-hackage] Phase 3 done: {len(all_entries)} functions → {args.out}",
              file=sys.stderr)

        # Optional merge
        if args.merge:
            merge_path = Path(args.merge)
            existing = json.loads(merge_path.read_text()) if merge_path.exists() else []
            merged = dedup_entries(existing + all_entries)
            merge_path.write_text(json.dumps(merged, indent=2))
            print(f"[extract-hackage] Merged: {len(existing)} + {len(all_entries)} = "
                  f"{len(merged)} total → {args.merge}", file=sys.stderr)

    # ── Stats summary ─────────────────────────────────────────────────────────
    if args.stats and all_stats:
        total_bindings = sum(s.get("bindings", 0) for s in all_stats)
        total_parsed   = sum(s.get("parsed", 0) for s in all_stats)
        parse_rate = total_parsed / total_bindings * 100 if total_bindings > 0 else 0
        print(f"\n[extract-hackage] Summary: "
              f"{total_parsed}/{total_bindings} bindings parsed ({parse_rate:.0f}%)",
              file=sys.stderr)
        print("\n[extract-hackage] Per-package breakdown:", file=sys.stderr)
        for s in sorted(all_stats, key=lambda x: x.get("final", 0), reverse=True):
            pkg      = s.get("package", "?")
            final    = s.get("final", 0)
            modules  = s.get("modules", 0)
            bindings = s.get("bindings", 0)
            parsed   = s.get("parsed", 0)
            rate     = parsed / bindings * 100 if bindings > 0 else 0
            err      = s.get("error", "")
            line = (f"  {pkg:30s}  {final:4d} funcs  {modules:3d} modules  "
                    f"{parsed:4d}/{bindings:4d} parsed ({rate:.0f}%)")
            if err:
                line += f"  ERROR: {err[:80]}"
            print(line, file=sys.stderr)

    # Write per-package status file for pkg_status.py
    if args.status_out and all_stats:
        from datetime import datetime, timezone
        status_data = {
            "run_date": datetime.now(timezone.utc).isoformat(),
            "packages": {s["package"]: s for s in all_stats},
        }
        Path(args.status_out).write_text(json.dumps(status_data, indent=2))
        print(f"[extract-hackage] Wrote status → {args.status_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
