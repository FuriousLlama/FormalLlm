#!/usr/bin/env python3
"""pkg_status.py — Per-package extraction status report.

Synthesises state from three sources (no extraction run required):
  1. packages.txt      — desired package list
  2. hackage-packages/ — filesystem state (downloaded / built / dumps)
  3. corpus_hackage.json — extraction results (function counts)
  4. pkg_status.json   — optional per-run stats from extract_hackage.py

Usage:
    python sprint/extract/pkg_status.py              # uses defaults
    make pkg-status                                   # via Makefile
    python sprint/extract/pkg_status.py --help
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter, defaultdict
from datetime import datetime


# ── Status derivation ────────────────────────────────────────────────────────

def derive_status(pkg: str, work_dir: pathlib.Path, corpus_counts: dict[str, int],
                  run_stats: dict) -> dict:
    """Determine current state for one package from filesystem + corpus."""
    import re
    # Only match directories where the version starts with a digit (e.g. pkg-1.2.3),
    # not pkg-suffix-1.2.3 which belongs to a different package.
    version_re = re.compile(rf"^{re.escape(pkg)}-(\d[\w.]*)$")
    pkg_dirs = sorted(
        d for d in work_dir.glob(f"{pkg}-*/")
        if version_re.match(d.name)
    )
    if not pkg_dirs:
        return {"status": "not_downloaded", "version": None, "dumps": 0,
                "functions": 0, "modules": 0, "parsed": 0, "bindings": 0,
                "build_ok": None, "build_error": None}

    pkg_dir = pkg_dirs[-1]  # newest version
    m = version_re.match(pkg_dir.name)
    version = m.group(1) if m else pkg_dir.name[len(pkg) + 1:]

    dist = pkg_dir / "dist-newstyle"
    if not dist.exists():
        return {"status": "downloaded", "version": version, "dumps": 0,
                "functions": 0, "modules": 0, "parsed": 0, "bindings": 0,
                "build_ok": None, "build_error": None}

    dumps = list(dist.rglob("*.dump-simpl"))
    funcs = corpus_counts.get(pkg, 0)

    # Pull extra detail from run_stats if available
    rs = run_stats.get(pkg, {})
    build_ok = rs.get("build_ok")
    build_error = rs.get("build_error")
    parsed = rs.get("parsed", 0)
    bindings = rs.get("bindings", 0)
    modules = rs.get("modules", len(dumps))

    if not dumps:
        status = "build_failed"
    elif funcs > 0:
        status = "extracted"
    else:
        status = "zero_yield"

    # Override with run_stats status if available (more accurate)
    if rs.get("status") in ("unpack_failed", "build_failed", "extracted", "zero_yield"):
        status = rs["status"]

    return {
        "status": status,
        "version": version,
        "dumps": len(dumps),
        "functions": funcs,
        "modules": modules,
        "parsed": parsed,
        "bindings": bindings,
        "build_ok": build_ok,
        "build_error": build_error,
    }


# ── Formatting ───────────────────────────────────────────────────────────────

STATUS_ORDER = ["extracted", "zero_yield", "build_failed", "downloaded", "not_downloaded"]

STATUS_LABEL = {
    "extracted":      "EXTRACTED",
    "zero_yield":     "ZERO YIELD",
    "build_failed":   "BUILD FAILED",
    "downloaded":     "DOWNLOADED (not built)",
    "not_downloaded": "NOT DOWNLOADED",
    "unpack_failed":  "UNPACK FAILED",
}

STATUS_NOTE = {
    "zero_yield":     "has dump files but 0 functions pass extraction",
    "build_failed":   "dist-newstyle exists but no .dump-simpl files",
    "downloaded":     "directory present but never built",
    "not_downloaded": "not yet fetched from Hackage",
}


def parse_rate(parsed: int, bindings: int) -> str:
    if bindings == 0:
        return "  -  "
    return f"{parsed / bindings * 100:3.0f}%"


def format_report(rows: list[dict], run_date: str | None) -> str:
    lines = []

    # Header
    lines.append("# Package Extraction Status")
    ts = run_date or datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"_Generated: {ts}_")
    lines.append("")

    # Summary table
    counts = Counter(r["status"] for r in rows)
    total_funcs = sum(r["functions"] for r in rows)
    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Status              | Packages |")
    lines.append(f"|---------------------|----------|")
    for s in STATUS_ORDER:
        if counts[s]:
            lines.append(f"| {STATUS_LABEL.get(s, s):19s} | {counts[s]:8d} |")
    lines.append(f"| {'**Total**':19s} | {len(rows):8d} |")
    lines.append(f"| {'**Functions**':19s} | {total_funcs:8d} |")
    lines.append("")

    # Per-status sections
    for status in STATUS_ORDER:
        group = [r for r in rows if r["status"] == status]
        if not group:
            continue
        label = STATUS_LABEL.get(status, status)
        note = STATUS_NOTE.get(status, "")
        lines.append(f"## {label} ({len(group)})")
        if note:
            lines.append(f"_{note}_")
        lines.append("")

        if status == "extracted":
            lines.append("| Package                         | Version      | Funcs | Dumps | Parsed |")
            lines.append("|----------------------------------|--------------|------:|------:|-------:|")
            for r in sorted(group, key=lambda x: -x["functions"]):
                lines.append(
                    f"| {r['package']:32s} | {r['version'] or '?':12s} "
                    f"| {r['functions']:5d} | {r['dumps']:5d} "
                    f"| {parse_rate(r['parsed'], r['bindings']):6s} |"
                )
        elif status == "zero_yield":
            lines.append("| Package                         | Version      | Dumps | Parse attempt |")
            lines.append("|----------------------------------|--------------|------:|---------------|")
            for r in sorted(group, key=lambda x: x["package"]):
                lines.append(
                    f"| {r['package']:32s} | {r['version'] or '?':12s} "
                    f"| {r['dumps']:5d} | {parse_rate(r['parsed'], r['bindings']):6s}          |"
                )
        elif status == "build_failed":
            lines.append("| Package                         | Version      | Build OK | Error hint                     |")
            lines.append("|----------------------------------|--------------|----------|--------------------------------|")
            for r in sorted(group, key=lambda x: x["package"]):
                ok_str = "yes" if r["build_ok"] else ("no" if r["build_ok"] is False else "?")
                err = (r["build_error"] or "").replace("\n", " ").strip()[:50]
                lines.append(
                    f"| {r['package']:32s} | {r['version'] or '?':12s} "
                    f"| {ok_str:8s} | {err:30s} |"
                )
        else:
            for r in sorted(group, key=lambda x: x["package"]):
                lines.append(f"- {r['package']}")

        lines.append("")

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Show per-package extraction status")
    p.add_argument("--packages-file", default="sprint/extract/packages.txt")
    p.add_argument("--work-dir", default="hackage-packages")
    p.add_argument("--corpus", default="sprint/extract/corpus_hackage.json",
                   help="Extracted corpus JSON (for function counts)")
    p.add_argument("--status", default="sprint/extract/pkg_status.json",
                   help="Per-run stats JSON written by extract_hackage.py --status-out")
    p.add_argument("--out", default=None,
                   help="Write report to file instead of stdout")
    p.add_argument("--json", action="store_true",
                   help="Output machine-readable JSON instead of markdown")
    args = p.parse_args()

    # Load wanted packages
    packages_file = pathlib.Path(args.packages_file)
    if not packages_file.exists():
        print(f"ERROR: {packages_file} not found", file=sys.stderr)
        sys.exit(1)
    packages = [
        l.strip() for l in packages_file.read_text().splitlines()
        if l.strip() and not l.startswith("#")
    ]

    work_dir = pathlib.Path(args.work_dir)

    # Load corpus function counts
    corpus_counts: dict[str, int] = defaultdict(int)
    corpus_path = pathlib.Path(args.corpus)
    if corpus_path.exists():
        try:
            data = json.loads(corpus_path.read_text())
            for entry in data:
                pkg = entry.get("package", "")
                if pkg:
                    corpus_counts[pkg] += 1
        except Exception as e:
            print(f"WARN: could not read corpus: {e}", file=sys.stderr)

    # Load per-run stats (optional)
    run_stats: dict = {}
    run_date: str | None = None
    status_path = pathlib.Path(args.status)
    if status_path.exists():
        try:
            status_data = json.loads(status_path.read_text())
            run_stats = status_data.get("packages", {})
            run_date = status_data.get("run_date")
        except Exception as e:
            print(f"WARN: could not read status file: {e}", file=sys.stderr)

    # Derive status for each wanted package
    rows = []
    for pkg in packages:
        info = derive_status(pkg, work_dir, corpus_counts, run_stats)
        info["package"] = pkg
        rows.append(info)

    # Also include packages in run_stats but not in packages.txt
    wanted_set = set(packages)
    for pkg, rs in run_stats.items():
        if pkg not in wanted_set:
            info = derive_status(pkg, work_dir, corpus_counts, run_stats)
            info["package"] = pkg
            rows.append(info)

    if args.json:
        output = json.dumps(rows, indent=2)
    else:
        output = format_report(rows, run_date)

    if args.out:
        pathlib.Path(args.out).write_text(output)
        print(f"Wrote status report → {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
