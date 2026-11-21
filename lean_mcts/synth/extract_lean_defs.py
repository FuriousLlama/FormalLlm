"""Extract (state, tactic) training pairs from Lean library definitions.

Uses lean_def_converter to convert equation compiler and where-clause
definitions into standalone `exact` expressions, then verifies each
through Pantograph.

Sprint 28 baseline sources (171 verified pairs):
  1. Lean stdlib: Init/Data/List, Init/Data/Nat, Init/Data/Int
  2. Batteries: Data/List only
  3. Mathlib: Data/List/{Defs,Cycle}.lean only

Sprint 29 Phase 1.1 expanded coverage:
  1. Lean stdlib: + Init/Data/{Array,Option,Sum}
  2. Batteries:   + Data/Array, Data/HashMap, Data/AssocList,
                  Data/BinaryHeap, Data/BinomialHeap, Data/DList,
                  Data/RBMap, Data/UnionFind, Data/Vector
  3. Mathlib:     + Data/List/{Basic,Sigma,Sort,Rotate,MinMax}.lean
                  (top 5 highest-def files beyond the S28 pair)

Expected yield (plan estimate): ~900 additional pairs, for a combined
pre-filter total of ~1,100. Actual count depends on how many polymorphic
definitions survive the type-specialization + Pantograph verification
gauntlet for each source.

Usage:
    python -m lean_mcts.synth.extract_lean_defs
    python -m lean_mcts.synth.extract_lean_defs --output train/library_pairs_v2.jsonl --verbose
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from lean_mcts.synth.lean_def_converter import (
    DefKind,
    LeanDef,
    convert_to_exact,
    parse_definitions,
)

# ---------------------------------------------------------------------------
# Type specialization (Sprint 29 Phase 1.1 broadening)
# ---------------------------------------------------------------------------
#
# Goal: rescue as many Lean library definitions as possible from the
# "Cannot start goal" rejection path. The S28 baseline failed on three
# common patterns seen across Mathlib / Batteries:
#
#   1. Unicode type aliases:     `ℕ`, `ℤ` (Mathlib), `𝔽`, `ℚ`, `ℝ`, `ℂ`
#   2. Monad/functor type vars:  `m`, `m₁`, `F` in `(α → m β) → List α → m (List β)`
#   3. Algebraic typeclasses:    `[Monoid α]`, `[CommRing α]`, `[LinearOrder α]`
#   4. Mathlib arithmetic:       `Inhabited`, `Nonempty`, `HAdd`, `HMul`, `OfNat n`
#
# S29 approach:
#   - UNICODE_TYPE_MAP rewrites `ℕ`→`Nat`, `ℤ`→`Int` as a preprocessing pass,
#     BEFORE specialization runs.
#   - SPECIALIZATIONS adds Greek letters beyond α-ζ (η, κ, σ, τ) and monad
#     variables (m, m₁, m₂, F → Id). m/F are replaced only when they clearly
#     function as type variables — the binder removal regex catches them.
#   - AUTO_INSTANCES is replaced with an algorithmic check against
#     RESOLVABLE_CLASSES × SPECIALIZED_TYPES, so we don't have to enumerate
#     every (class, type) pair by hand.

# Unicode type aliases used in Mathlib. These get rewritten to their
# ASCII equivalents BEFORE specialization so the downstream regex pipeline
# doesn't have to know about them.
UNICODE_TYPE_MAP: dict[str, str] = {
    "ℕ": "Nat",
    "ℤ": "Int",
    "ℚ": "Rat",     # harmless pass-through — won't resolve but won't blow up
    "ℝ": "Real",    # ditto
    "ℂ": "Complex", # ditto
    "𝔽": "Field",   # ditto
}

# Concrete type substitutions for polymorphic definitions. S28 had just
# α-ζ→Int/Nat; S29 adds more Greek letters (η, κ, σ, τ) and monad
# variables (m, m₁, m₂, F → Id). Short ASCII single-letter type vars like
# `n` and `a` are NOT substituted because they commonly appear as value
# names too.
SPECIALIZATIONS = [
    # Primary: element type → Int, monads/functors → Id
    {
        # Greek type variables (common in Lean)
        "α": "Int", "β": "Int", "γ": "Int", "δ": "Int",
        "ε": "Int", "ζ": "Int", "η": "Int", "κ": "Int",
        "σ": "Int", "τ": "Int",
        # Monad / functor type variables → Id (trivial monad; Id X = X)
        "m": "Id", "m₁": "Id", "m₂": "Id", "F": "Id",
    },
    # Secondary: element type → Nat
    {
        "α": "Nat", "β": "Nat", "γ": "Nat", "δ": "Nat",
        "ε": "Nat", "ζ": "Nat", "η": "Nat", "κ": "Nat",
        "σ": "Nat", "τ": "Nat",
        "m": "Id", "m₁": "Id", "m₂": "Id", "F": "Id",
    },
]

# Types that a typeclass instance can be parameterized over and still be
# auto-resolved by Lean once we've specialized. Includes Id because our
# monad substitution produces `[Monad Id]`, `[Applicative Id]`, etc.
SPECIALIZED_TYPES: set[str] = {
    "Int", "Nat", "Id", "Bool", "Char", "String",
    "Unit", "PUnit", "Empty", "PEmpty",
}

# Typeclasses whose instances are auto-resolvable when their type arguments
# are in SPECIALIZED_TYPES. Covers core Lean numerics + the Mathlib algebraic
# hierarchy + monad/functor typeclasses + utility classes. Entries chosen
# to maximize rescue rate on Mathlib Data.List / Batteries Data.Array code.
RESOLVABLE_CLASSES: set[str] = {
    # Core equality / decidable
    "BEq", "DecidableEq", "Decidable",
    "Eq", "HEq",
    # Order
    "LT", "LE", "Ord", "Ordering",
    "Min", "Max",
    "LinearOrder", "PartialOrder", "Preorder",
    "DecidableLE", "DecidableLT", "DecidableLinearOrder",
    "OrderedSemiring", "OrderedRing", "OrderedCommRing",
    "StrictOrderedRing", "LinearOrderedRing",
    "LinearOrderedCommRing", "LinearOrderedField",
    "LinearOrderedSemiring", "LinearOrderedCommSemiring",
    "Lattice", "DistribLattice", "BoundedLattice",
    "SemilatticeSup", "SemilatticeInf",
    # Arithmetic (homogeneous)
    "Add", "Sub", "Mul", "Div", "Mod", "Neg", "Pow",
    "HAdd", "HSub", "HMul", "HDiv", "HMod", "HPow",
    # Constants
    "Zero", "One", "OfNat",
    "NeZero", "NonZero",
    # Coercions
    "NatCast", "IntCast", "Coe", "CoeHTCT", "CoeTC",
    # Algebra (Mathlib hierarchy — available via import Mathlib.Tactic in FormalNav)
    "Semigroup", "CommSemigroup", "AddSemigroup", "AddCommSemigroup",
    "Monoid", "CommMonoid", "AddMonoid", "AddCommMonoid",
    "Group", "CommGroup", "AddGroup", "AddCommGroup", "AbelianGroup",
    "Semiring", "CommSemiring", "Ring", "CommRing",
    "Field", "DivisionRing", "IntegralDomain",
    "IsDomain", "NoZeroDivisors",
    "MulZeroClass", "AddZeroClass", "MulOneClass",
    "MulZeroOneClass", "MonoidWithZero", "SemiringWithOne",
    # Monad / functor
    "Functor", "Applicative", "Monad", "Pure", "Bind", "Seq", "SeqLeft", "SeqRight",
    "LawfulFunctor", "LawfulApplicative", "LawfulMonad",
    "MonadFail", "MonadLift", "MonadExcept", "MonadState",
    "MonadReader", "MonadWithReader", "MonadWriter", "MonadError",
    "Alternative", "Traversable", "Foldable", "Repeatable",
    # Utility
    "Inhabited", "Nonempty", "Subsingleton",
    "Hashable", "ToString", "Repr", "ReprAtom",
    "Reflect", "ReflectName",
    "FromJson", "ToJson",
    # Set / collection
    "Membership", "HasMem",
    "Singleton", "Insert", "EmptyCollection",
    "Union", "Inter", "SDiff", "Compl",
}

# Tokens allowed to appear as arguments inside an auto-resolvable typeclass
# instance beyond types themselves. e.g. `[OfNat Int 0]` has `0` as a
# natural-number literal; `[Fin.cast 5]` has a numeric literal. Digits,
# underscores, and known constants are all OK.
_LIT_ARG = re.compile(r'^(?:\d+|_|Fin\s*\d+|\d+\s*\+\s*\d+)$')


def _is_auto_resolvable(class_and_args: str) -> bool:
    """Is a typeclass instance like 'BEq Int' or 'Monoid Nat' auto-resolvable
    after we've substituted type variables to concrete types?

    True iff the class name is in RESOLVABLE_CLASSES and every argument is
    in SPECIALIZED_TYPES or is a numeric literal / placeholder.
    """
    text = class_and_args.strip()
    if not text:
        return False
    parts = text.split()
    if not parts:
        return False
    class_name = parts[0]
    if class_name not in RESOLVABLE_CLASSES:
        return False
    for arg in parts[1:]:
        arg_clean = arg.strip("()[],")
        if not arg_clean:
            continue
        if arg_clean in SPECIALIZED_TYPES:
            continue
        if arg_clean.isdigit():
            continue
        if arg_clean == "_":
            continue
        if _LIT_ARG.match(arg_clean):
            continue
        return False
    return True


def normalize_unicode_types(s: str) -> str:
    """Rewrite Unicode type aliases (ℕ, ℤ, ...) to their ASCII equivalents.

    Runs as a preprocessing pass before SPECIALIZATIONS, so downstream
    regexes don't need to know about Unicode variants.
    """
    for uni, ascii_name in UNICODE_TYPE_MAP.items():
        s = s.replace(uni, ascii_name)
    return s


def specialize_type(type_pp: str, subst: dict[str, str]) -> str | None:
    """Specialize a polymorphic type by substituting type variables.

    Returns the specialized type string, or None if specialization fails.

    Sprint 29 Phase 1.2-B follow-up: three targeted bug fixes for failure
    categories found by instrumenting the Phase 1 rejection log:

      Bug 1 (empty-brackets-as-instance):
        Step 4's regex `\\[[^\\]]*\\]\\s*→\\s*` was meant to drop instance
        brackets like `[BEq Int] → ...`, but `[^\\]]*` allows zero chars
        so `[] → ` (empty list in a term-level position like `as ≠ [] → α`)
        also matched and got stripped. Result: `as ≠ [] → α` became
        `as ≠ α`, which Lean then rejects. Fix: require at least one
        non-whitespace, non-bracket character inside the brackets. This
        rescues `List.getLast`, `List.head`, `List.getLastD`, and friends.

      Bug 2 (dangling references to dropped binders):
        When step 3 drops a binder like `{P : α → Prop} →` or
        `{motive : α → Sort u} →`, any remaining references to the bound
        variable (`P`, `motive`) in the type body become unbound. Lean
        rejects with "Unknown identifier P". Fix: track the names of
        binders we drop, then reject the whole type if any dropped
        name still appears as a word token after substitution.

      Bug 3 (Latin-alphabet type variables):
        The existing `return None` at step 6 only checked Greek letters.
        Types with type-vars named `P`, `Q`, `T`, `C`, or any single
        capital Latin letter (common for predicates and motives) slip
        through. After the bug-2 drop-tracking, this is mostly handled,
        but we keep a safety net that rejects types with a single
        capital Latin letter appearing as a standalone word.
    """
    # Step 0 (S29): normalize Unicode type names to ASCII so later regexes
    # can handle Mathlib's ℕ/ℤ-using definitions uniformly.
    result = normalize_unicode_types(type_pp)

    # Track names of binders we drop during normalization — later we
    # reject the whole type if any of these names still appears in the
    # body after substitution (dangling reference).
    dropped_binder_names: set[str] = set()

    def _capture_drop(match: re.Match) -> str:
        """Replace a binder with empty string and remember its bound names."""
        full = match.group(0)
        # full is like "{P : α → Prop} → " — extract the part between { and }
        try:
            inner_start = full.index('{') + 1
            inner_end = full.rindex('}')
            inner = full[inner_start:inner_end]
        except ValueError:
            return ''
        # inner is like "P : α → Prop" or "a b c : Type u_1"
        if ':' in inner:
            names_part = inner.split(':', 1)[0].strip()
            for tok in names_part.split():
                if tok and tok.isidentifier():
                    dropped_binder_names.add(tok)
        return ''

    # Step 1: substitute each polymorphic type variable to its concrete target,
    # and drop the matching {var : Type u_*} / {var : Sort u_*} binder.
    for var, concrete in subst.items():
        # Remove {var : Type ...} binders, but record the name we're dropping
        # so bug-2 detection can flag dangling references.
        result = re.sub(
            rf'\{{\s*{re.escape(var)}\s*:\s*(?:Type|Sort)\s*\w*\s*\}}\s*→\s*',
            _capture_drop, result
        )
        # Replace the variable in the rest
        result = re.sub(rf'\b{re.escape(var)}\b', concrete, result)

    # Step 2 (S29): remove auto-resolvable typeclass instance args via the
    # algorithmic RESOLVABLE_CLASSES × SPECIALIZED_TYPES check instead of
    # the S28 hardcoded AUTO_INSTANCES set. This catches [Monoid Int],
    # [CommRing Nat], [LinearOrder Int], [LawfulMonad Id], etc. uniformly.
    #
    # The inner pattern `[^\]]+` (at least one char) correctly excludes
    # empty brackets — only step 4 had the zero-char bug.
    result = re.sub(
        r'\[(?:\w+\s*:\s*)?([^\]]+)\]\s*→\s*',
        lambda m: '' if _is_auto_resolvable(m.group(1)) else m.group(0),
        result
    )

    # Step 3: remove remaining {implicit} args that couldn't be type-var-
    # substituted (e.g. `{P : α → Prop}` remains after α → Int because the
    # binder head is `P`, not α). We drop the whole binder but capture its
    # bound name(s) so step 6 can reject the type if `P` is still in use.
    result = re.sub(r'\{[^}]*\}\s*→\s*', _capture_drop, result)

    # Step 4 (BUG FIX): catch any remaining [inst] args. The S28 regex was
    # `\[[^\]]*\]\s*→\s*` which allows ZERO characters inside the brackets.
    # That accidentally matched `[] → ` in term positions like
    # `as ≠ [] → α`, stripping the empty-list constructor and producing
    # `as ≠ α` → `as ≠ Int` after substitution. Fix: require at least one
    # non-whitespace character inside the brackets so `[]` is not eaten.
    result = re.sub(r'\[[^\]\s][^\]]*\]\s*→\s*', '', result)

    # Step 5: collapse whitespace.
    result = ' '.join(result.split())

    # Step 6 (BUG FIX): reject types with dangling references to dropped
    # binders, or with free type-variable-like tokens we can't substitute.
    #
    # (a) Universes — same as before.
    if re.search(r'Type\s+u', result) or re.search(r'Sort\s+u', result):
        return None
    # (b) Single lowercase Greek letter — existing check.
    if re.search(r'\b[αβγδεζηθικλμξοπρςστυφχψω]\b', result):
        return None
    # (c) NEW: `motive` — the conventional name for a dependent eliminator's
    # predicate. When seen, it was always a free variable in a {motive : ...}
    # binder that we dropped in step 3, so it's a dangling reference. Caught
    # by (d) below in practice, but kept as an explicit safeguard because
    # `motive` is a common enough pattern to deserve a named check.
    if re.search(r'\bmotive\b', result):
        return None
    # (d) NEW: dangling references to binders we dropped during steps 1 / 3.
    # If a dropped binder name (P, p, motive, etc.) appears as a word in the
    # result, the type is broken.
    #
    # This is the precise replacement for what would otherwise be a blanket
    # reject on "single uppercase Latin letter" — which was too aggressive
    # and dropped defs like `Array.attachWith (P : α → Prop) ...` where `P`
    # is an EXPLICIT parameter (introduced via intro) and not dangling.
    # Explicit params use `(...)` syntax which _capture_drop never sees.
    for name in dropped_binder_names:
        if name in SPECIALIZED_TYPES:
            continue  # unlikely but harmless
        if re.search(rf'\b{re.escape(name)}\b', result):
            return None

    return result


def specialize_expr(expr: str, subst: dict[str, str]) -> str:
    """Specialize type variables in an expression."""
    # Normalize Unicode types in expressions too, in case a source body
    # contains `(x : ℕ)` or similar.
    result = normalize_unicode_types(expr)
    for var, concrete in subst.items():
        result = re.sub(rf'\b{re.escape(var)}\b', concrete, result)
    return result


# Bare polymorphic term constructors that Lean can't elaborate without a
# type hint. If a specialized def body is one of these, we wrap it with
# an explicit type annotation `(body : <return_type>)` before emitting
# the `exact` tactic.
_AMBIGUOUS_CONSTRUCTORS: set[str] = {
    "[]",
    "none",
    "Option.none",
    "#[]",     # Array literal
    "∅",
    "0",
    "1",
}


def _last_arrow_component(type_str: str) -> str:
    """Return the last top-level →-separated component of a type.

    Used to compute the return type of a specialized function signature
    so we can annotate bare polymorphic constructors: `exact []` becomes
    `exact ([] : List Int)`.
    """
    depth = 0
    i = len(type_str) - 1
    last_arrow_end = len(type_str)
    while i > 0:
        ch = type_str[i]
        if ch in ')}]':
            depth += 1
        elif ch in '({[':
            depth -= 1
        elif ch == '→' and depth == 0:
            return type_str[i + 1:last_arrow_end].strip()
        i -= 1
    return type_str.strip()


def _annotate_ambiguous_body(body: str, return_type: str) -> str:
    """If the body is a bare polymorphic constructor, wrap it with an
    explicit type annotation based on the goal's return type.
    """
    stripped = body.strip()
    if stripped in _AMBIGUOUS_CONSTRUCTORS:
        return f"({stripped} : {return_type})"
    return body


def build_goal_state(specialized_type: str) -> str:
    """Build a goal state string from a function type.

    Handles four binder shapes at each arrow-separated position:

    1. Implicit type binder `{α : Type}` → `α : Type`
       (standard Lean goal-context rendering for polymorphic parameters)

    2. Named explicit binder `(h : P x)` → `h : P x`
       (preserves the source-level name)

    3. Instance binder `[BEq α]` → `inst : BEq α`
       (Lean's goal contexts use `inst✝` for elaborated instances; we
       use `inst` as an anonymous placeholder for readability)

    4. Bare type `List Int` → `xs : List Int`
       (type-derived name from `_name_for_type`)

    Examples:
        `List Int → Nat → List Int` →
            xs : List Int
            n : Nat
            ⊢ List Int

        `{α : Type} → List α → List α` →
            α : Type
            xs : List α
            ⊢ List α

        `{α : Type} → [BEq α] → List α → List α → Bool` →
            α : Type
            inst : BEq α
            xs : List α
            xs_1 : List α
            ⊢ Bool

    For simple types (no →), just returns ⊢ Type.
    """
    # Split on top-level →
    parts = _split_arrows(specialized_type)
    if len(parts) <= 1:
        return f"⊢ {specialized_type}"

    # Last part is the return type
    ret = parts[-1].strip()
    args = parts[:-1]

    # Generate names for each arg
    state_lines = []
    name_counts: dict[str, int] = {}
    inst_counter = 0
    for arg in args:
        arg = arg.strip()
        # 1. Implicit type binder {name : type}
        implicit = re.match(r'\{\s*(\w+)\s*:\s*(.+?)\s*\}$', arg)
        if implicit:
            state_lines.append(f"{implicit.group(1)} : {implicit.group(2)}")
            continue
        # 2. Named explicit binder (name : type)
        named = re.match(r'\((\w+)\s*:\s*(.+)\)', arg)
        if named:
            state_lines.append(f"{named.group(1)} : {named.group(2)}")
            continue
        # 3. Instance binder [Class args]
        if arg.startswith('[') and arg.endswith(']'):
            inst_name = "inst" if inst_counter == 0 else f"inst_{inst_counter}"
            inst_counter += 1
            state_lines.append(f"{inst_name} : {arg[1:-1].strip()}")
            continue
        # 4. Bare type
        base = _name_for_type(arg)
        count = name_counts.get(base, 0)
        name_counts[base] = count + 1
        name = base if count == 0 else f"{base}_{count}"
        state_lines.append(f"{name} : {arg}")

    state_lines.append(f"⊢ {ret}")
    return "\n".join(state_lines)


def _split_arrows(type_str: str) -> list[str]:
    """Split a type on top-level → arrows."""
    depth = 0
    parts = []
    current: list[str] = []
    i = 0
    while i < len(type_str):
        ch = type_str[i]
        if ch in '({[':
            depth += 1
            current.append(ch)
        elif ch in ')}]':
            depth -= 1
            current.append(ch)
        elif ch == '→' and depth == 0:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    if current:
        parts.append(''.join(current).strip())
    return parts


def _name_for_type(type_str: str) -> str:
    """Generate a sensible variable name for a type."""
    t = type_str.strip()
    if t.startswith('List'):
        return 'xs'
    if t.startswith('Array'):
        return 'arr'
    if t in ('Int', 'Nat'):
        return 'n'
    if t == 'Bool':
        return 'b'
    if t.startswith('Option'):
        return 'opt'
    if t.startswith('Sum'):
        return 's'
    if t.startswith('Prod') or (t.startswith('(') and ' × ' in t):
        return 'p'
    if t.startswith('HashMap') or t.startswith('AssocList') or t.startswith('RBMap'):
        return 'm'
    if t.startswith('BinaryHeap') or t.startswith('BinomialHeap'):
        return 'h'
    if t.startswith('DList'):
        return 'dl'
    if t.startswith('Vector'):
        return 'v'
    if t.startswith('UnionFind'):
        return 'uf'
    if '→' in t:
        return 'f'
    return 'x'


# ---------------------------------------------------------------------------
# Source file discovery
# ---------------------------------------------------------------------------

# Lean stdlib containers we extract from. Each maps to the module_prefix
# that should be prepended to unqualified definitions found in that directory.
#
# S28 baseline: List, Nat, Int.
# S29 Phase 1.1 adds: Array, Option, Sum. (Prod has no Init/Data/Prod dir in
# recent Lean toolchains — product definitions live in Prelude and can't be
# reached from a file walk.)
STDLIB_CONTAINERS: list[str] = [
    "List",
    "Nat",
    "Int",
    "Array",     # S29
    "Option",    # S29
    "Sum",       # S29
]

# Batteries data subdirectories we extract from. Key = subdir name under
# Batteries/Data, value = module_prefix.
#
# S28 baseline: only List.
# S29 Phase 1.1: add Array, HashMap, AssocList, BinaryHeap, BinomialHeap,
# DList, RBMap, UnionFind, Vector. Fin/BitVec/ByteArray/Char are skipped
# because they're number-theoretic and don't produce compositional
# definitions suitable for training signal.
BATTERIES_CONTAINERS: dict[str, str] = {
    "List":         "List",
    "Array":        "Array",        # S29
    "HashMap":      "HashMap",      # S29
    "AssocList":    "AssocList",    # S29
    "BinaryHeap":   "BinaryHeap",   # S29
    "BinomialHeap": "BinomialHeap", # S29
    "DList":        "DList",        # S29
    "RBMap":        "RBMap",        # S29
    "UnionFind":    "UnionFind",    # S29
    "Vector":       "Vector",       # S29
}

# Mathlib Data/List files, ranked by definition count (from a scan). S28
# only extracted from Defs and Cycle. S29 Phase 1.1 adds the next 5 highest-
# density files. Basic.lean (246 defs) is included despite its size because
# it contains the core algorithmic definitions that are most likely to
# produce compositional training signal.
MATHLIB_LIST_FILES: list[str] = [
    "Defs.lean",     # S28
    "Cycle.lean",    # S28
    "Basic.lean",    # S29 — 246 defs, highest density
    "Sigma.lean",    # S29 — 136 defs
    "Sort.lean",     # S29 — 131 defs
    "Rotate.lean",   # S29 — 130 defs
    "MinMax.lean",   # S29 — 100 defs
]


def find_source_files(project_root: Path) -> list[tuple[Path, str]]:
    """Find all Lean source files to extract from.

    Returns a list of (path, module_prefix) tuples. The module_prefix is
    used by `parse_definitions` to build each definition's fully-qualified
    name (e.g. `push` in `Init/Data/Array/Basic.lean` → `Array.push`).

    Sources are scanned in deterministic order so the output pair set is
    reproducible across runs.
    """
    files: list[tuple[Path, str]] = []

    # --- 1. Lean stdlib (from the active elan toolchain) -------------------
    elan = Path.home() / ".elan" / "toolchains"
    if elan.exists():
        toolchains = sorted(elan.iterdir(), reverse=True)
        for tc in toolchains:
            src = tc / "src" / "lean"
            if not src.exists():
                continue
            for container in STDLIB_CONTAINERS:
                container_dir = src / "Init" / "Data" / container
                if not container_dir.exists():
                    continue
                for f in sorted(container_dir.rglob("*.lean")):
                    files.append((f, container))
            break  # Use first (latest) toolchain only

    # --- 2. Batteries (from the Lake package cache) ------------------------
    lake = project_root / "lean_mcts" / "env" / "FormalNav" / ".lake" / "packages"
    batt_root = lake / "batteries" / "Batteries" / "Data"
    if batt_root.exists():
        for subdir_name, module_prefix in BATTERIES_CONTAINERS.items():
            batt_dir = batt_root / subdir_name
            if not batt_dir.exists():
                continue
            for f in sorted(batt_dir.rglob("*.lean")):
                # Skip top-level re-export indices like Data/List/List.lean
                # which just `import` sub-files and contain no definitions.
                if f.name == f"{subdir_name}.lean" and f.parent == batt_dir:
                    continue
                files.append((f, module_prefix))

    # --- 3. Mathlib (focused list of high-density Data.List files) ---------
    mathlib_list = lake / "mathlib" / "Mathlib" / "Data" / "List"
    if mathlib_list.exists():
        for fname in MATHLIB_LIST_FILES:
            f = mathlib_list / fname
            if f.exists():
                files.append((f, "List"))

    return files


# ---------------------------------------------------------------------------
# Pantograph verification
# ---------------------------------------------------------------------------

def verify_with_pantograph(pairs: list[dict], project_root: Path,
                           verbose: bool = False) -> list[dict]:
    """Verify each pair through Pantograph using two-tier validation.

    Sprint 29 Phase 1.7: two validation tiers produce two kinds of training
    signal.

    **Tier 1 — functional** (same as S28 baseline):
        goal_start(spec_type) → intro → exact <body>
        If is_solved, the pair is tagged `validation: "functional"` and
        the state is Pantograph's post-intro representation (the goal
        the body actually proves).

    **Tier 2 — syntactic** (new in S29):
        If Tier 1 fails at the `exact` step, wrap the body in a lambda
        using param types parsed from spec_type and call `expr.echo` via
        Pantograph. If Lean accepts the wrapped term as valid Lean code
        — even if the inferred type differs from spec_type — the pair
        is tagged `validation: "syntactic"` and the state is rebuilt
        from the INFERRED type (not spec_type).

        Rationale: library definitions are semantically trusted (they
        compile and pass Lean's own type checker in their native
        context), so "can't verify against our specialized goal" is
        almost always a symptom of spec_type being wrong, not of the
        body being wrong. For the model to learn what valid Lean looks
        like, these syntactically-valid-but-spec-mismatched pairs are
        useful training signal.

    Pairs that fail both tiers are dropped.

    Sprint 29 multi-candidate path: if a pair has an `alt_tactics` list
    (for multi-line SIMPLE defs where the converter emits several
    collapsed forms), we try each alternate at the functional tier
    before falling through to the syntactic tier.
    """
    try:
        from pantograph import Server
    except ImportError:
        print("WARNING: pantograph not available, skipping verification")
        return pairs

    project_path = str(project_root / "lean_mcts" / "env" / "FormalNav")
    server = Server(
        imports=["FormalNav"],
        project_path=project_path,
        timeout=30,
    )

    # ----- Tier 1 helper: functional verification via exact -----
    def _try_functional(goal_type: str, intro_names: str, tactic: str) -> tuple[bool, str, str]:
        """Returns (success, post_intro_state_str, failure_detail)."""
        try:
            state = server.goal_start(expr=goal_type)
            if intro_names:
                state = server.goal_tactic(state, tactic=f"intro {intro_names}")
            goal_str = str(state)
            result = server.goal_tactic(state, tactic=tactic)
            if result.is_solved:
                return True, goal_str, ""
            return False, goal_str, str(result)[:60]
        except Exception as e:
            return False, "", str(e)[:80]

    # ----- Tier 2 helper: library-trust syntactic validation -----
    #
    # Rationale (S29 Phase 1.7): if a pair comes from the Lean standard
    # library (stdlib / Batteries / Mathlib) and the functional check
    # failed for anything OTHER than a pure parse error, we trust that
    # the body is valid Lean in its native context. The failure is
    # almost always a dependency-resolution or namespace issue in our
    # Pantograph instance, not a real correctness bug — the def wouldn't
    # have compiled in the library otherwise.
    #
    # We use the universe-normalized raw type (the real type Pantograph
    # returned from env_inspect, with `Type u` / `Sort u` stripped to
    # `Type` / `Sort`) as the state. This is honest: the state reflects
    # the def's actual signature, not our potentially-wrong specialized
    # version.
    #
    # Parse errors are the only rejection at this tier — they indicate
    # our converter emitted garbage, not that the library is wrong.

    _PARSE_ERROR_MARKERS = (
        "parseError",
        "Cannot parse",
        "unexpected token",
        "unexpected end of input",
        "expect",
    )

    def _looks_like_parse_error(detail: str) -> bool:
        """True if the error message from Pantograph indicates a parse-level
        failure rather than an elaboration / identifier / type failure."""
        if not detail:
            return False
        low = detail.lower()
        return any(m.lower() in low for m in _PARSE_ERROR_MARKERS)

    def _normalize_universes(raw_type: str) -> str:
        """Strip universe annotations so polymorphic types can be used as
        goals. `{α : Type u} → List α → List α` becomes
        `{α : Type} → List α → List α`, which Pantograph accepts as a
        well-formed polymorphic goal."""
        # Replace `Type u`, `Type u_1`, `Type v`, `Sort u`, etc.
        t = re.sub(r'Type\s+(?:u|v|w|u_\w+|v_\w+|w_\w+)', 'Type', raw_type)
        t = re.sub(r'Sort\s+(?:u|v|w|u_\w+|v_\w+|w_\w+)', 'Sort', t)
        return t

    def _try_syntactic(raw_type: str, intro_names: str, body: str,
                       func_failure_detail: str, is_library: bool) -> tuple[bool, str, str]:
        """Trust-based syntactic acceptance for library sources.

        Returns (accepted, state_for_pair, detail).
        """
        if not is_library:
            return False, "", "non-library source"
        if _looks_like_parse_error(func_failure_detail):
            return False, "", f"parse error in body: {func_failure_detail[:60]}"

        # Normalize universes in the raw type to get a usable polymorphic
        # state. We don't feed it to Pantograph — we just build a goal
        # state string from its structure for the training pair.
        normalized = _normalize_universes(raw_type)
        state_str = build_goal_state(normalized)
        return True, state_str, ""

    verified = []
    stats = {"functional": 0, "syntactic": 0, "dropped": 0}
    for p in pairs:
        goal_type = p["goal_type"]
        primary_tactic = p["tactic"]
        alt_tactics: list[str] = p.get("alt_tactics") or []
        intro_names = p.get("intro_names", "")
        raw_type = p.get("raw_type", "")
        is_library = (p.get("source", "") == "library")

        # Try primary first, then alternates at Tier 1 (functional).
        candidates = [primary_tactic] + [a for a in alt_tactics if a != primary_tactic]

        last_err = ""
        winning_tactic: str | None = None
        winning_state = ""
        validation_tier: str | None = None

        for cand in candidates:
            ok, state_str, detail = _try_functional(goal_type, intro_names, cand)
            if ok:
                winning_tactic = cand
                winning_state = state_str
                validation_tier = "functional"
                break
            last_err = detail

        # Tier 2: library-trust syntactic fallback. If a library pair
        # failed functional verification for non-parse reasons, accept
        # it as syntactic with the universe-normalized raw type as the
        # state. This captures bodies that are valid Lean in their
        # native library context but fail our verification due to
        # dependency / namespace / specializer issues.
        if winning_tactic is None and is_library and raw_type:
            ok, state_str, detail = _try_syntactic(
                raw_type=raw_type,
                intro_names=intro_names,
                body=primary_tactic,
                func_failure_detail=last_err,
                is_library=is_library,
            )
            if ok:
                winning_tactic = primary_tactic
                winning_state = state_str
                validation_tier = "syntactic"

        if winning_tactic is not None and validation_tier is not None:
            p["tactic"] = winning_tactic
            p["state"] = winning_state
            p["validation"] = validation_tier
            p.pop("alt_tactics", None)
            p.pop("raw_type", None)
            verified.append(p)
            stats[validation_tier] += 1
            if verbose:
                tag = "FUNC" if validation_tier == "functional" else "SYNT"
                print(f"  {tag}: {p['name']} ({p['kind']})")
        else:
            stats["dropped"] += 1
            if verbose and "universe" not in last_err:
                print(f"  REJECT:   {p['name']} — {last_err}")

    print(f"  validation tiers: functional={stats['functional']} "
          f"syntactic={stats['syntactic']} dropped={stats['dropped']}")
    return verified


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def extract_all(project_root: Path, verbose: bool = False,
                verify: bool = True) -> list[dict]:
    """Run the full extraction pipeline."""

    source_files = find_source_files(project_root)
    print(f"Found {len(source_files)} source files")

    # Parse and convert all definitions
    # Use wrap_lambda=False so body references intro'd variables directly
    all_converted: list[tuple[LeanDef, str]] = []
    for path, prefix in source_files:
        defs = parse_definitions(path, module_prefix=prefix)
        for d in defs:
            expr = convert_to_exact(d, wrap_lambda=False)
            if expr is not None:
                all_converted.append((d, expr))

    print(f"Converted {len(all_converted)} definitions")
    kind_counts: dict[str, int] = {}
    for d, _ in all_converted:
        k = d.kind.name
        kind_counts[k] = kind_counts.get(k, 0) + 1
    for k, v in sorted(kind_counts.items()):
        print(f"  {k}: {v}")

    # Get types from Pantograph (reliable, unlike source reconstruction)
    print("Fetching types from Pantograph...")
    type_cache = _fetch_types_from_pantograph(all_converted, project_root, verbose)
    print(f"  Got types for {len(type_cache)} definitions")

    # Import at function level so we can call extract_explicit_params
    from lean_mcts.synth.lean_def_converter import (
        extract_explicit_params,
        DefKind as DK,
        _convert_simple_multi,
    )

    # Specialize to concrete types and build training pairs
    pairs: list[dict] = []
    for d, expr in all_converted:
        raw_type = type_cache.get(d.full_name) or type_cache.get(d.name)
        if not raw_type:
            continue

        # Determine intro names: use original param names for SIMPLE/WHERE,
        # use the generated match args for EQCOMP
        if d.kind == DK.EQCOMP:
            intro_names = d.eq_match_args.replace(",", "") if d.eq_match_args else ""
        else:
            params = extract_explicit_params(d)
            intro_names = " ".join(params)

        if not intro_names:
            continue  # Can't introduce — skip

        # S29: for SIMPLE-kind defs, compute alternate candidate forms so the
        # verifier can fall back from newline-preserved to semicolon-joined
        # to space-joined. EQCOMP and WHERE kinds have a single canonical
        # form so their alt_exprs is just [expr].
        if d.kind == DK.SIMPLE:
            alt_exprs = _convert_simple_multi(d)
            if expr not in alt_exprs:
                alt_exprs = [expr] + alt_exprs
        else:
            alt_exprs = [expr]

        for subst in SPECIALIZATIONS:
            spec_type = specialize_type(raw_type, subst)
            if not spec_type:
                continue

            # Compute the return type once for ambiguous-constructor
            # annotation (S29 bug fix): if the specialized body is a bare
            # polymorphic constructor like `[]` or `none`, Lean can't
            # elaborate it without a type hint. Wrap as `([] : <return>)`.
            return_type = _last_arrow_component(spec_type)

            # Specialize every candidate form uniformly
            spec_alts: list[str] = []
            for cand in alt_exprs:
                spec_cand = specialize_expr(cand, subst)
                # Rescue bare polymorphic constructors with explicit
                # type annotation.
                spec_cand = _annotate_ambiguous_body(spec_cand, return_type)
                tactic_cand = f"exact {spec_cand}"
                if len(tactic_cand) > 512:
                    continue
                if tactic_cand not in spec_alts:
                    spec_alts.append(tactic_cand)

            if not spec_alts:
                continue

            primary = spec_alts[0]
            alternates = spec_alts[1:]

            pairs.append({
                "state": "",  # filled after verification (post-intro state)
                "tactic": primary,
                "alt_tactics": alternates,  # stripped after verification
                "goal_type": spec_type,
                "raw_type": raw_type,     # S29: needed for syntactic tier fallback
                "intro_names": intro_names,
                "source": "library",
                "name": d.name,
                "full_name": d.full_name,
                "kind": d.kind.name,
                "subst": json.dumps(subst),
                "file": d.source_file,
            })

    print(f"Generated {len(pairs)} specialized pairs")

    # Deduplicate by (goal_type, tactic)
    seen: set[tuple[str, str]] = set()
    unique = []
    for p in pairs:
        key = (p["goal_type"], p["tactic"])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    pairs = unique
    print(f"After dedup: {len(pairs)} unique pairs")

    # Verify through Pantograph (also builds goal state)
    if verify:
        print("Verifying through Pantograph...")
        pairs = verify_with_pantograph(pairs, project_root, verbose=verbose)
        print(f"After verification: {len(pairs)} verified pairs")
    else:
        # Without verification, build goal state from type
        for p in pairs:
            p["state"] = build_goal_state(p["goal_type"])

    # Remove internal fields (only needed for verification)
    for p in pairs:
        p.pop("goal_type", None)
        p.pop("intro_names", None)

    return pairs


def _fetch_types_from_pantograph(
    converted: list[tuple[LeanDef, str]],
    project_root: Path,
    verbose: bool = False,
) -> dict[str, str]:
    """Use Pantograph env_inspect to get the type of each definition."""
    try:
        from pantograph import Server
    except ImportError:
        print("WARNING: pantograph not available, falling back to source reconstruction")
        return {}

    project_path = str(project_root / "lean_mcts" / "env" / "FormalNav")
    server = Server(
        imports=["FormalNav"],
        project_path=project_path,
        timeout=30,
    )

    cache: dict[str, str] = {}
    # Collect unique names to inspect
    names_to_check = set()
    for d, _ in converted:
        names_to_check.add(d.full_name)
        names_to_check.add(d.name)

    for name in sorted(names_to_check):
        if name in cache:
            continue
        # Try various qualified forms
        candidates = [name]
        if '.' in name:
            # Also try the bare name
            candidates.append(name.rsplit('.', 1)[-1])
        for cand in candidates:
            try:
                result = server.env_inspect(cand)
                type_pp = result["type"]["pp"]
                cache[name] = type_pp
                if verbose:
                    print(f"  TYPE: {name} → {type_pp[:80]}")
                break
            except Exception:
                continue

    return cache


# ---------------------------------------------------------------------------
# Diversity audit
# ---------------------------------------------------------------------------

def audit_diversity(pairs: list[dict]) -> dict[str, int]:
    """Count how many pairs contain each pattern type."""
    patterns = {
        "let_rec": 0,
        "match": 0,
        "let_chain": 0,
        "foldl_foldr": 0,
        "lambda": 0,
        "simple_exact": 0,
    }
    for p in pairs:
        t = p["tactic"]
        if "let rec" in t:
            patterns["let_rec"] += 1
        elif "match" in t:
            patterns["match"] += 1
        elif t.count("let ") >= 2:
            patterns["let_chain"] += 1
        elif "foldl" in t or "foldr" in t:
            patterns["foldl_foldr"] += 1
        elif "fun " in t:
            patterns["lambda"] += 1
        else:
            patterns["simple_exact"] += 1
    return patterns


def main():
    parser = argparse.ArgumentParser(
        description="Extract training pairs from Lean library definitions"
    )
    parser.add_argument(
        "--output", type=str, default="lean_mcts/train/library_pairs.jsonl"
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip Pantograph verification")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent.parent
    output_path = project_root / args.output

    pairs = extract_all(
        project_root,
        verbose=args.verbose,
        verify=not args.no_verify,
    )

    if not pairs:
        print("No pairs extracted!")
        sys.exit(1)

    # Diversity audit
    print("\n=== Diversity Audit ===")
    patterns = audit_diversity(pairs)
    total = len(pairs)
    non_trivial = total - patterns["simple_exact"]
    for name, count in sorted(patterns.items(), key=lambda x: -x[1]):
        pct = count / total * 100 if total else 0
        print(f"  {name}: {count} ({pct:.1f}%)")
    print(f"  non-trivial: {non_trivial}/{total} ({non_trivial/total*100:.1f}%)")

    if non_trivial / total < 0.4 if total else True:
        print("WARNING: <40% non-trivial patterns — diversity gate not met")

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as out:
        for p in pairs:
            out.write(json.dumps(p) + "\n")

    print(f"\nOutput: {output_path} ({len(pairs)} pairs)")


if __name__ == "__main__":
    main()
