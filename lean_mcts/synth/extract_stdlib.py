"""Phase 4b: Extract (state, tactic) training pairs from Lean standard library.

Scans Init/Data/List/*.lean (and similar) for function definitions that use
our target library functions (map, filter, foldl, scanl, range, zip, etc.).
Formats them as training pairs matching the self-play format.

This supplements self-play data with "real" Lean programming patterns —
not proofs (Sprint 12's mistake), but actual function implementations.

Usage:
    python synth/extract_stdlib.py
    python synth/extract_stdlib.py --output train/stdlib_pairs.jsonl --verbose
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


# Functions we care about — if a definition body uses any of these,
# it's a relevant training example
TARGET_FUNCTIONS = {
    "map", "filter", "foldl", "foldr", "scanl", "reverse", "length",
    "eraseDups", "mergeSort", "take", "drop", "takeWhile", "dropWhile",
    "zip", "zipWith", "flatten", "join", "range", "replicate",
    "head", "tail", "getLast", "append", "partition", "filterMap",
    "enum", "zipIdx", "intersperse", "Prod.fst", "Prod.snd",
    "dropLast",
}

# Lean toolchain base directories to search
TOOLCHAIN_PATHS = [
    Path.home() / ".elan" / "toolchains",
]

# Directories within the toolchain to search for source
STDLIB_DIRS = [
    "src/lean/Init/Data/List",
    "src/lean/Init/Data/Array",
    "src/lean/Init/Data/Option",
    "src/lean/Std",
]

# Regex to match def/theorem with a body (not just a signature)
# Matches: def name (args) : type := body
DEF_RE = re.compile(
    r'^(def|nonrec def|protected def|@\[.*\]\s*def)\s+'
    r'(\w[\w.]*)\s*'           # function name
    r'((?:\([^)]*\)\s*)*)'     # parameter groups
    r'\s*:\s*'                  # return type separator
    r'([^:=]+?)'               # return type
    r'\s*:=\s*$',              # := at end of line
    re.MULTILINE
)

# Also match where clause definitions
WHERE_DEF_RE = re.compile(
    r'^\s+where\s+(\w+)\s*'
    r'((?:\([^)]*\)\s*)*)'
    r'\s*:\s*'
    r'([^:=]+?)'
    r'\s*:=\s*(.+)$',
    re.MULTILINE
)


def find_toolchain() -> Path | None:
    """Find the latest Lean 4 toolchain with source files."""
    for base in TOOLCHAIN_PATHS:
        if not base.exists():
            continue
        # Find toolchains sorted by version (descending)
        toolchains = sorted(base.iterdir(), reverse=True)
        for tc in toolchains:
            src = tc / "src" / "lean"
            if src.exists():
                return tc
    return None


def find_stdlib_files(toolchain: Path) -> list[Path]:
    """Find relevant .lean files in the stdlib."""
    files = []
    for subdir in STDLIB_DIRS:
        d = toolchain / subdir
        if d.exists():
            if d.is_file() and d.suffix == ".lean":
                files.append(d)
            elif d.is_dir():
                files.extend(sorted(d.rglob("*.lean")))
    return files


def extract_definitions(path: Path) -> list[dict]:
    """Extract function definitions from a .lean file.

    Returns list of dicts with:
      name: function name
      params: parameter string
      return_type: return type
      body: function body (may span multiple lines)
    """
    text = path.read_text(errors="replace")
    lines = text.split("\n")
    defs = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip comments, imports, lemmas, theorems, axioms, opaque
        stripped = line.strip()
        if (stripped.startswith("--") or stripped.startswith("import") or
            stripped.startswith("theorem") or stripped.startswith("lemma") or
            stripped.startswith("axiom") or stripped.startswith("opaque") or
            stripped.startswith("extern") or stripped.startswith("instance")):
            i += 1
            continue

        m = DEF_RE.match(line)
        if m:
            name = m.group(2)
            params = m.group(3).strip()
            ret_type = m.group(4).strip()

            # Collect body: indented lines after :=
            body_lines = []
            j = i + 1
            while j < len(lines):
                bline = lines[j]
                # Stop at next top-level def, empty line between defs, etc.
                if bline and not bline[0].isspace() and not bline.startswith("  "):
                    break
                body_lines.append(bline)
                j += 1

            body = "\n".join(body_lines).strip()

            if body and len(body) < 512:  # Skip huge definitions
                defs.append({
                    "name": name,
                    "params": params,
                    "return_type": ret_type,
                    "body": body,
                    "file": str(path),
                })

            i = j
        else:
            i += 1

    return defs


def filter_relevant(defs: list[dict]) -> list[dict]:
    """Keep only definitions that use our target library functions."""
    relevant = []
    for d in defs:
        body = d["body"]
        # Check if body uses any target function
        used = [fn for fn in TARGET_FUNCTIONS if fn in body]
        if used:
            d["used_functions"] = used
            relevant.append(d)
    return relevant


def format_as_training_pairs(defs: list[dict]) -> list[dict]:
    """Convert definitions to (state, tactic) training pairs.

    The state is the tactic state you'd see after `def f (args) : Type := by sorry`
    The tactic is `exact <body>`
    """
    pairs = []
    for d in defs:
        body = d["body"]

        # Skip multi-line where clauses, match expressions, etc.
        # We want simple expression bodies that could be a single `exact`
        if "where" in body or "match" in body or "do" in body:
            continue
        if body.count("\n") > 3:  # Skip long multi-line bodies
            continue

        # Clean up body: single line
        body_clean = " ".join(body.split())

        # Build state string from params + return type
        params = d["params"]
        ret_type = d["return_type"]

        # Parse params into state format: "(a : Type) (b : Type)" → "a : Type\nb : Type"
        state_parts = []
        for pm in re.finditer(r'\(([^)]+)\)', params):
            state_parts.append(pm.group(1).strip())

        if not state_parts:
            continue

        state = "\n".join(state_parts) + f"\n⊢ {ret_type}"
        tactic = f"exact {body_clean}"

        # Skip if tactic is too long (won't fit in model context)
        if len(tactic) > 256:
            continue

        pairs.append({
            "state": state,
            "tactic": tactic,
            "source": "stdlib",
            "name": d["name"],
            "return_type": ret_type,
            "used_functions": d.get("used_functions", []),
            "file": d["file"],
        })

    return pairs


def main():
    parser = argparse.ArgumentParser(description="Extract stdlib training pairs")
    parser.add_argument("--output", type=str, default="train/stdlib_pairs.jsonl")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent.parent
    output_path = script_dir / args.output

    # Find toolchain
    toolchain = find_toolchain()
    if toolchain is None:
        print("ERROR: No Lean 4 toolchain found with source files")
        return

    print(f"Using toolchain: {toolchain}")

    # Also check project's .lake/packages for batteries/mathlib source
    lake_packages = script_dir / "env" / "FormalNav" / ".lake" / "packages"
    extra_dirs = []
    if (lake_packages / "batteries").exists():
        extra_dirs.append(lake_packages / "batteries" / "Batteries" / "Data" / "List")
    if (lake_packages / "batteries").exists():
        extra_dirs.append(lake_packages / "batteries" / "Batteries" / "Data" / "Array")

    # Find all relevant source files
    files = find_stdlib_files(toolchain)
    for d in extra_dirs:
        if d.exists():
            files.extend(sorted(d.rglob("*.lean")))

    print(f"Found {len(files)} source files to scan")

    # Extract definitions
    all_defs = []
    for f in files:
        try:
            defs = extract_definitions(f)
            all_defs.extend(defs)
        except Exception as e:
            if args.verbose:
                print(f"  Error reading {f}: {e}")

    print(f"Extracted {len(all_defs)} definitions")

    # Filter to relevant ones
    relevant = filter_relevant(all_defs)
    print(f"Filtered to {len(relevant)} relevant definitions (using target functions)")

    # Format as training pairs
    pairs = format_as_training_pairs(relevant)
    print(f"Formatted {len(pairs)} training pairs")

    # Deduplicate by (state, tactic)
    seen = set()
    unique_pairs = []
    for p in pairs:
        key = (p["state"], p["tactic"])
        if key not in seen:
            seen.add(key)
            unique_pairs.append(p)

    print(f"After dedup: {len(unique_pairs)} unique pairs")

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as out:
        for p in unique_pairs:
            out.write(json.dumps(p) + "\n")

    print(f"Output: {output_path}")

    if args.verbose:
        # Print function usage stats
        from collections import Counter
        fn_counts = Counter()
        for p in unique_pairs:
            for fn in p["used_functions"]:
                fn_counts[fn] += 1
        print("\nFunction usage in extracted pairs:")
        for fn, count in fn_counts.most_common():
            print(f"  {fn}: {count}")


if __name__ == "__main__":
    main()
