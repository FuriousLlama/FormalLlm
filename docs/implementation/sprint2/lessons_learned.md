# Lessons Learned — Sprint 2

Running log of non-obvious findings, gotchas, and decisions made during development.

---

## 2026-02-21 — Clean IR (ir_schema.json + clean.py)

### I# unboxing requires two-level substitution for Case patterns with `as` fields

GHC Core wraps every `Int` scrutinee in a `Case(x, as=wild, [I# ds1 -> body])` pattern.
The `as` field binds the *boxed* Int (e.g., `wild`) and the pattern var binds the *unboxed*
Int (e.g., `ds1`). Both can appear in the body — `wild` when the original value is passed
through, `ds1` when arithmetic is done on the unboxed representation.

The cleaning pass must substitute *both* names with the scrutinee:

```python
# Single-alt I# case: Case(x, as="wild", [PCon(I#, [ds1]) -> body])
body = substitute(body, "ds1", scrutinee)   # unboxed var
body = substitute(body, "wild", scrutinee)  # as-var (boxed)
```

Missing the `as`-var substitution leaves `wild` as a free variable in the cleaned AST,
which then appears in the token stream and breaks pretty-printing.

### Typeclass-dispatched primitives have a consistent 4-argument shape

Every binary arithmetic/comparison op in GHC Core appears as:

```
App(Var "+", [TyArg Int, Var "$fNumInt", <operand1>, <operand2>])
```

The pattern is always: `[TyArg, $f* dict, real args...]`. Cleaning strips the first two
args and wraps the function in a `Prim` node. Unary ops (`negate`, `abs`, `signum`) have
the same shape but with one operand instead of two.

The Boolean operators `&&`, `||`, `not` are different — they appear as plain `Var` references
with no dict argument. These are converted to `Prim` at the Var level (rule 6), not at the
App level (rule 5).

### Con nodes for I# must return None (not be preserved) when seen standalone

The initial cleaning pass preserved standalone `Con("I#")` nodes, which could appear when
I# was used as a constructor without arguments (e.g., in partial application). These must
return `None` from `clean_expr` so parent nodes can filter them out. The same applies to
`Con("C#")` for unboxed chars.

### The `__DEFAULT` pattern appears in GHC Core for numeric case alternatives

GHC uses `__DEFAULT` (not `_`) as the wildcard constructor name in case alternatives when
the scrutinee is a numeric type. The parser captures it as `PCon("__DEFAULT", [])`. The
clean IR replaces all of these with `PWild`, which is a dedicated pattern node.

### Vocabulary reduction from cleaning: 350 → 298 tokens

Removing GHC-internal tokens (`I#`, `$fNumInt`, `$fOrdInt`, `$fEqInt`, `TyArg`, `Int`,
`@T`, `0#`, `__DEFAULT`) and adding clean IR tokens (`Prim`, `(PWild)`, op names) produced
a net reduction of ~50 tokens. Smaller vocabulary means the tokenizer and model have less
noise to learn through.

### Schema validation needs jsonschema package — make it optional

The `jsonschema` Python package is not in our minimal dependency set. `clean.py` attempts
schema validation but falls back gracefully if the package is missing. For CI/strict mode,
`uv pip install jsonschema` should be added to the setup. For development, the noise
validation (scanning for I#/$f*/TyArg/\_\_DEFAULT strings in JSON output) catches the same
class of errors without the dependency.

### 162/162 functions cleaned with 0 drops — higher than expected

Expected 5-10% of functions to fail cleaning (edge cases in GHC Core output). All 162
hand-written functions cleaned successfully. This is likely because:

1. The hand-written corpus was already designed to avoid GHC Core edge cases (lesson from sprint 1)
2. The cleaning rules handle all patterns that appear in `-O0` output of simple functions
3. Real Hackage packages will almost certainly have a higher failure rate (optimized code, GADTs, type families, etc.)

The true test of cleaning robustness will be the Hackage extraction phase.

---

## 2026-02-21 — Hackage Extraction (extract_hackage.py)

### `cabal build` with `-ddump-to-file` is the right approach for multi-module packages

Compiling individual `.hs` files from Hackage packages doesn't work — they import from
within the package and from external dependencies that aren't in GHC's default search path.
Using `cabal build all --ghc-options="-ddump-simpl -ddump-to-file ..."` handles all of this:
cabal resolves dependencies, sets up package databases, and compiles every module. The
`-ddump-to-file` flag writes `.dump-simpl` files alongside the `.o` files in `dist-newstyle/`,
one per module.

This is different from the hand-written extraction which compiles a single `Functions.hs`
with `ghc -ddump-simpl` (output to stdout). For Hackage, the output goes to files, so
`-v0` is unnecessary and `-fforce-recomp` is unnecessary (first build always compiles).

### `-dynamic` is required on Arch Linux even through cabal

The same Arch Linux issue from sprint 0/1 applies when building through cabal: only `.dyn_hi`
interface files are installed for `base`, so GHC cannot find `Prelude` without `-dynamic`.
Passing `-dynamic` via `--ghc-options` to `cabal build` fixes this. GHC emits a warning
("-dynamic-too is ignored when using -dynamic") which is harmless.

### Link failures are non-fatal — dump files are generated per module during compilation

When `cabal build` fails at the link stage (missing `.dyn_o` files, which is common with
`-dynamic`), it exits with a non-zero code. But the compilation of individual modules already
succeeded — the `.dump-simpl` files are written during compilation, before linking. The
pipeline should always attempt to find dump files even when `cabal build` reports failure.

In practice, every package that got past dependency resolution produced dump files despite
the link error. This turned a "build failure" into a "100% module yield" situation.

### Parse failure rate is 26% across Hackage — below the 30% GHC plugin threshold

Across 18 packages and 4,240 bindings, the existing Python parser successfully parsed 74%
of bindings. The 26% failure rate comes from GHC Core constructs not in the hand-written
corpus:

- **Coercion/cast expressions**: GHC inserts `cast` and coercion nodes for newtype wrapping,
  type role conversions, etc. These don't appear in simple functions.
- **Tick/source annotations**: Source location annotations that appear in some packages.
- **Complex nested expressions**: Deeply nested case/let chains that exceed the parser's
  lookahead, especially in `array` (25% parse rate).

Since the failure rate is below 30%, extending the parser for the most common failures
(coercions, casts) would be more cost-effective than writing a GHC plugin. But even without
parser extensions, 1,487 functions is sufficient for the sprint 2 corpus target.

### Package dependency failures are the main extraction bottleneck, not parser failures

Of 25 packages attempted, 7 produced zero functions — all due to dependency build failures,
not parser issues. Common failure modes:

- **Template Haskell dependencies** (`th-abstraction`): TH requires static linking, which
  conflicts with `-dynamic` on Arch.
- **C library dependencies** (`clock`, `primitive`): FFI packages need system libraries that
  may not be installed.
- **Outdated cabal files**: Some packages have version bounds that exclude GHC 9.6.

The fix is not parser work — it's curating the package list to avoid packages with heavy
native/TH dependencies. Pure Haskell packages with minimal dependencies (tier 1) have
near-100% build success.

### Size filtering (5-300 tokens) removes ~25% of parsed functions

Many Hackage functions are either trivial wrappers (1-4 tokens: `f = g`) or extremely long
(300+ tokens: complex state machines, parser combinators). The size filter strikes a useful
balance:

- Below 5 tokens: single-application wrappers with no structural content to learn from.
- Above 300 tokens: functions too long for the model's context window and often contain
  constructs (IO, ST monad, FFI) outside the clean IR's scope.

### Structural dedup catches 45 cross-package duplicates

Packages re-export or re-implement the same utility functions. Hashing the cleaned AST body
(ignoring the function name) catches these: `containers` and `unordered-containers` share
map/fold implementations, `text` and `bytestring` share builder patterns. Without dedup,
the model would see multiple copies of the same structure with different names, which wastes
capacity.

### The cleaning pass handles Hackage functions well — 0 noise violations in 1,487 functions

The cleaning rules designed for the hand-written corpus (I# unwrapping, dict erasure, TyArg
removal, etc.) generalized perfectly to Hackage code. Every function that passed the parser
and cleaning stages also passed the noise validation gate. This validates the earlier decision
to define cleaning rules based on GHC Core's structure rather than on specific function shapes.

### Corpus composition: 162 handwritten + 1,487 Hackage = 10x scale increase

The merged corpus (1,649 functions) is exactly 10x the sprint 1 corpus (162 functions). The
handwritten functions remain as a stable baseline — they cover the specific patterns needed
for benchmark tasks. The Hackage functions add structural diversity (data structure operations,
string processing, numeric algorithms) that the model hasn't seen before.

The train/val split must now be by **package** (not by function name) to prevent leakage:
functions from the same package share coding patterns, naming conventions, and helper
dependencies.

---

## 2026-02-21 — Augmentation + Tokenizer (augment.py, tokenizer.py)

### Sprint 1's low-yield augmentations confirmed: let-reorder and eta-expand removed

Let-reorder produced ~0 variants in sprint 1 (independent let bindings are rare in `-O0`
Core). Eta-expand produced 8 variants from 162 functions (~5% yield). With 10x more data
in sprint 2, neither strategy justified the pipeline complexity. Removed from the active
pipeline; function definitions kept as dead code in case they're useful later.

### Commutative swap fires less often than expected: 199/1,649 sequences (12%)

The `augment_commutative_swap()` function looks for `(App Prim OP arg1 arg2)` patterns
where OP is commutative (+, \*, ==, /=, &&, ||). Only 12% of sequences contain such
patterns at the top-level token stream. Most arithmetic in Hackage functions is wrapped
inside Case scrutinees or Let bindings where the commutative op is several levels deep —
the pattern still matches because it scans the flat token list, but many sequences simply
don't use commutative primitives directly.

This is fine for augmentation yield — rerename at 3 seeds is the workhorse (3,876 variants).
Commutative swap adds a qualitatively different kind of variation (semantic equivalence under
operand reordering) that rerename cannot provide.

### Paren-balanced expression span detection is the right abstraction for token-level transforms

The `_expr_span(tokens, start)` helper classifies the expression at a given position:
- Parenthesized compound (`(App ...)`): scan until balanced
- Keyword-value pair (`Var x`, `Lit 0`, `Prim +`): exactly 2 tokens
- Wildcard (`_`): exactly 1 token

This is reusable for any future token-level transform that needs to identify argument
boundaries. The key insight is that the clean IR token format is already paren-balanced by
construction (normalize.py ensures this), so paren counting is reliable.

### Rerename dedup is higher with Hackage data than with handwritten functions

In sprint 1, rerename dedup removed very few sequences (hand-written functions have diverse
structure). With Hackage data, many functions from the same package have similar structure
(e.g., `containers` has dozens of map/fold variants). Rerename with different seeds can
produce identical results when the function has only one variable. This brought the total
from an expected ~6,600 to 5,698 (26 original duplicates removed, plus rerename collisions).

### min_frequency=3 cuts vocabulary from 2,452 to 499 tokens

The raw augmented corpus has 2,572 unique tokens. Of these, 1,539 are hapax legomena
(appear only once) — almost all external function names from Hackage packages
(`$WAllocationStrategy`, `$WBP`, package-internal helpers). Setting `min_frequency=3` in
`WordLevelTrainer` maps these to `<unk>`, producing a 499-token vocabulary.

This is a 5x reduction from the uncapped vocabulary. The model no longer wastes embedding
capacity on tokens it will never learn meaningful representations for.

### 87% of sequences contain at least one `<unk>` token — this is expected and acceptable

1,433 out of 1,649 sequences have at least one rare token that maps to `<unk>`. This seems
high but is correct: most Hackage functions reference at least one package-internal name
(a helper function, a constructor from the package's data types) that appears fewer than
3 times in the corpus. The handwritten functions mostly don't have this issue since they
only reference each other.

The `<unk>` tokens appear in positions where the model should learn structural patterns
("there's a function call here") rather than specific names. This is the desired behavior —
the model learns syntax and type structure, not a lookup table of function names.

### Tokenizer should train on raw sequences, not augmented sequences

The Makefile trains the tokenizer on `sequences.json` (1,649 raw), not `sequences_aug.json`
(5,698 augmented). This is correct: augmentation inflates token frequencies artificially
(3x rerename makes every token appear 3x more often), which would lower the effective
min_frequency threshold and let more rare tokens into the vocabulary. Training on raw
sequences gives a honest frequency distribution for the cutoff decision.
