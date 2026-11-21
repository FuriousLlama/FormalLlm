# Lessons Learned — Sprint 4

Running log of non-obvious findings, gotchas, and decisions made during development.

---

## 2026-02-22 — 3-Phase Extraction Pipeline

### Shared output files and parallel workers don't mix

The original pipeline streamed all package results to a single shared JSONL file using `ProcessPoolExecutor`. Even though the main process wrote sequentially (workers returned data, main wrote), Python's IO buffering combined with `as_completed` processing caused 13 corrupted lines in a 5,294-line file — two JSON objects merged onto one line without a separator. The corruption was non-obvious and caused a hard crash at corpus assembly time.

**Fix**: give each package its own output file. Each worker process writes only to `pkg_raw/<pkg>.json` using an atomic tmp+rename, then returns. The main process only reads from these files after all workers are done. No shared mutable state means no corruption possible.

### Three phases is the right decomposition

Previously extraction was monolithic: build + parse + clean → JSONL in one pass. This meant:
- Changing clean.py → re-extract everything (slow)
- Failed runs lost all partial progress
- Impossible to inspect intermediate state

The three-phase split:
1. `pkg_raw/<pkg>.json` — raw GHC Core ASTs (build + parse only)
2. `pkg_clean/<pkg>.json` — clean IR (cleaning pass only)
3. `corpus_hackage.json` — filtered + deduped corpus

Each phase is independently re-runnable. Phase 3 takes seconds. This is the pattern that matters for iterative development: make the slow part (phase 1) run once, make the fast parts (phases 2, 3) cheap to re-run.

### `tokenize` from parse_ghc_core.py cannot be used on clean IR dicts

`filter_by_size` originally called `tokenize(e["body"])` to count tokens, importing `tokenize` from `parse_ghc_core.py`. That function is a character-by-character GHC Core text tokenizer — it expects a string, not a dict. On a dict, `text[0]` raises `KeyError: 0`. The code happened to work in earlier sprints because the body was stored differently.

**Fix**: count AST nodes with an iterative stack traversal (`_count_nodes`). Recursive traversal hits Python's recursion limit on deeply-nested functions. Iterative stack works on arbitrarily deep trees.

### AST node count is a better size metric than string token count

String token count on the raw GHC Core dump conflates tokens from type annotations, GHC internals, and the actual expression. AST node count on the clean IR body directly measures expression complexity. With `min=5, max=300` AST nodes, we filter out 2,032 trivially tiny functions (<5 nodes = single literal or variable reference) and 18 enormous ones (>300 nodes). The 5–300 range captures meaningful functions.

---

## 2026-02-21 — GHC Artifact Cleanup (Pillar 1)

### GHC artifacts are Var nodes, not special tags

When Sprint 3 identified `letrec`, `cast`, `$trModule1`, `primFixed` as phantom deps (241 combined occurrences), the question was where to filter them. The options were:

1. **In normalize.py** — filter them from the `external_deps` list after `collect_free_vars()`
2. **In clean.py** — drop them as `Var` nodes before they reach normalize.py

Option 2 is the right place. These names appear as `Var` nodes in the AST body, and clean.py is the single choke point for raw AST → clean IR transformation. Filtering in normalize.py would mean the clean IR still contains artifact Var nodes, which is conceptually wrong — the clean IR should never contain GHC internals.

The fix is a frozenset `GHC_ARTIFACT_VARS` and a two-line addition to the `Var` handler: if name in GHC_ARTIFACT_VARS → return None. The `None` propagates upward correctly through `App` (already had: `App` with `None` fun → `None`) and `Lam`/`Let`/`Case` (body becoming `None` → those nodes return `None`).

**Effect on corpus:** 144 of 1,649 functions relied on artifact var references in their body — these entries are now correctly dropped rather than producing malformed token sequences.

### Comparison operators had a context-dependent promotion gap

`<`, `>`, `>=`, `<=`, `==`, `/=` were in `PRIMITIVES_WITH_DICT` and fully handled when they appeared as the function in an `App` node (Rule 5 in `_clean_app`). But GHC Core sometimes emits these operators as standalone `Var` nodes — for example, when a comparison is partially applied or appears in a higher-order context. In these cases, the existing `Var` handler didn't recognize them as primitives and left them as `Var` nodes.

The fix is a single check before the `$f*` filter in the `Var` handler:

```python
if name in PRIMITIVES_WITH_DICT:
    return {"tag": "Prim", "op": name}
```

This ensures all appearances of arithmetic and comparison operators become `Prim` nodes, regardless of whether they appear as `App` functions or standalone `Var` references.

**Effect:** 222 comparison op `Var` references → 0. The dep count for `<`, `>`, `>=` (90, 83, 48 in Sprint 3) drops to 0.

### The ordering of checks in the Var handler matters

The `Var` handler checks in this order:
1. `PRIMITIVES_NO_DICT` (`&&`, `||`, `not`) → Prim
2. **New:** `PRIMITIVES_WITH_DICT` (`<`, `>`, `+`, `-`, etc.) → Prim
3. `$f*` prefix → None (typeclass dictionary vars)
4. `GHC_ARTIFACT_VARS` → None
5. Everything else → Var as-is

The `PRIMITIVES_WITH_DICT` check must come before the `$f*` check (these sets are disjoint, but the intent should be clear: primitives are promoted before any dropping logic runs). The `GHC_ARTIFACT_VARS` check correctly comes after the `$f*` check since artifact names don't start with `$f`.

### Not all "cast" names are GHC artifacts

After filtering `GHC_ARTIFACT_VARS = {"letrec", "cast", "$trModule1", "primFixed"}`, 10 Var references remain that contain those substrings: `castPtr` (6), `castDoubleToWord64` (2), `castFloatToWord32` (2). These are legitimate `Foreign.Ptr` functions — the artifact filter uses exact matching (`name in GHC_ARTIFACT_VARS`), not substring matching, so they're preserved correctly.

---

## 2026-02-21 — Package Curation (Pillar 1)

### 200 packages is a probabilistic target, not an exact count

The 202-package list will not all compile successfully. Hackage packages fail extraction for several reasons:
- Missing build-tool dependencies (Happy, Alex for parser packages)
- FFI-heavy packages that need C headers
- Packages that use newer GHC extensions not in GHC 9.6.6
- Packages with too many transitive deps that cabal can't resolve quickly

Based on Sprint 2 experience (18 out of 32 packages compiled = 56% yield), expecting 100-200 successful compilations from 202 listed packages. The parser combinator tier (`parsec`, `attoparsec`, `megaparsec`) may fail due to Happy dependency; the concurrency tier is likely to fail due to C deps.

The pipeline skips failures gracefully, so over-listing is the correct strategy.

### Categories for diversity, not just quantity

Sprint 3's 18 compiled packages were heavily weighted toward simple list/container utilities. Diverse package categories matter for the corpus because:
- More categories → more recursion schemes (tree, accumulator, CPS, corecursion)
- Math packages → different literal patterns and arithmetic structures
- Graph packages → pointer-like recursion with multiple recursive calls
- Parser combinator packages → continuation-passing and higher-order patterns

If all packages are list utilities, the model will overfit to list recursion even with 10x more data. The Sprint 4 package list was curated to include algorithms, math, graph theory, serialization, and functional patterns to force structural diversity.

### Some packages on the list may not exist

Several entries in the expanded list are speculative — packages I believe exist on Hackage but haven't verified (e.g., `dequeue`, `ring-buffer`, `bool-extras`, `groupBy`). This is intentional: `cabal unpack` for a non-existent package fails immediately with a clear error, costs <1 second, and is silently skipped by the extraction pipeline. There's no cost to listing packages that don't exist, only benefit from listing ones that do.

---

## 2026-02-22 — Extraction Execution (Pillar 1)

### Unboxed char literals cause parser infinite loops → 46 GB OOM

The most dangerous bug found during execution: GHC Core emits unboxed char literals like `'('#` and `','#` in some modules (e.g., `transformers-compat-0.8`'s `Data.Functor.Classes.Generic.Internal`). The tokenizer silently skipped the `'` character (it matched nothing in the token rules), so `'('#` tokenized as `LP` (left paren). The token stream then contained an unmatched `LP` inside a `Case` branch — `parse_pat` entered its "tuple pattern" loop (`while peek != 'RP'`) and consumed tokens indefinitely since the expected RP never appeared.

Two `ProcessPoolExecutor` worker processes entered this loop and each grew to 46 GB RAM, crashing the host.

**Fix 1**: Added `CLIT` token type in `parse_ghc_core.py` that matches `'X'#?` before the `LP` handler. Char literals are now tokenized correctly and emitted as `{'tag': 'PLit', 'val': "'X'"}` in patterns.

**Fix 2**: Added per-binding 5-second `SIGALRM` timeout in `extract_module()`. Any binding that takes more than 5 seconds to parse is skipped. This is the safety net for future unknown hangs.

**Diagnosis method**: Binary search through the bindings in the problem dump file (`timeout 10 python3 -c "... parse binding N ..."`) found binding 57 (`isTupleString`) as the hang source. The binding's raw text showed `'('#` — immediately identifying the tokenizer issue.

### The cascade is deeper than package dependency trees suggest

The pre-execution analysis expected 100-200 packages to compile from 202 listed. The actual yield was 62. The gap has two causes:

1. **Arch Linux dynamic-only base**: Without `-dynamic`, GHC can't find `base` `.hi` files. This forces `-dynamic` on all packages.
2. **Old build systems don't emit `.dyn_o`**: `tagged-0.8.10`, `clock-0.8.4`, `primitive-0.9.1.0`, and `transformers-compat-0.7.2` use `Setup.hs` or old Cabal build system versions that compile `.o` but not `.dyn_o`. The linker fails when `-dynamic` is passed. This blocks their entire downstream dependency trees.

**Why targeted re-runs of "leaf" packages also fail**: Even packages with no direct dependency on `tagged` or `clock` may still fail because `QuickCheck-2.17.1.0` (a test dependency of many packages) also uses an old build system pattern, and `unliftio-core` depends on `transformers-compat`. The cascade is wider than `cabal`'s direct dep tree shows.

**The fix**: Pre-build the blocker packages (tagged, clock, primitive, transformers-compat) into the cabal store with `ghc -fPIC -dynamic -c *.hs -hidir ... -odir ...` and register with `ghc-pkg`. This is about 2-3 hours of manual work per blocker package but would unlock ~50-80 more packages.

### JSONL streaming vs JSON accumulation

The original `extract_hackage.py` accumulated all results in RAM and wrote a single JSON file at the end. With 202 packages and parallel workers, this caused two failure modes:

1. If the main process was killed (e.g., due to OOM in workers), all accumulated results were lost
2. The JSON dump at the end was a single large allocation at peak memory usage

Switching to JSONL streaming (each package's results appended to `.jsonl.tmp` as it completes) with a dedup+merge pass at the end means:
- At most one package's results are in RAM at a time
- The work is preserved incrementally if the process dies
- The dedup pass at the end can manually be run on the `.jsonl.tmp` file if extraction is interrupted

### Running background processes: `run_in_background` + `&` = orphan on shell exit

When a background Bash task was launched with `run_in_background: true` AND the command string also ended with `&`, the shell exited (because the agent task completed), the orphaned background process received SIGHUP and died before the dedup pass ran. This left a `.jsonl.tmp` file with valid data but no final `corpus_hackage.json`.

**Fix**: Never use `&` in the command when `run_in_background: true` is already set. If the process must survive shell exit, use `nohup` explicitly: `nohup python3 script.py > log.txt 2>&1` (without `&`).

---

## 2026-02-22 — Cascade Root Analysis (Pillar 1)

### Two distinct mechanisms, not one

The initial analysis described "the Arch Linux cascade" as a single phenomenon. Post-extraction investigation of cabal build logs reveals two independent failure modes:

**Mechanism A — `.dyn_o` missing (old `build-type: Simple` packages)**

Three Hackage packages use old-style Cabal build systems (`Setup.hs`, pre-v3 format). When cabal passes `-dynamic-too` (which it does to build both `.o` and `.dyn_o` simultaneously), these old build systems log `"warning: -dynamic-too is ignored when using -dynamic"` and compile only regular `.o` object files. The GNU gold linker then fails:

```
ld.gold: error: cannot open dist/build/Data/Tagged.dyn_o: No such file or directory
```

Roots: `tagged-0.8.10`, `indexed-traversable-0.1.4`, `clock-0.8.4`.

**Mechanism B — missing GHC boot modules (Arch-specific)**

Arch Linux's `extra/ghc` package only installs `.dyn_hi` interface files for boot packages (`base`, `ghc-prim`, etc.) — not static `.hi` files. Packages that import GHC internals (`GHC.Exts`, `GHC.Prim`, `Data.Bits`) fail when cabal builds them as static dependencies:

```
Could not find module 'GHC.Exts'
There are files missing in the 'base-4.18.2.1' package, try running 'ghc-pkg check'.
```

Roots: `primitive-0.9.1.0`, `QuickCheck-2.17.1.0`.

### The catch-22

These two mechanisms cannot be fixed simultaneously with a single cabal flag:

- Fixing Mechanism B requires `-dynamic` project-wide (`package * ghc-options: -dynamic` in `cabal.project.local`)
- But adding project-wide `-dynamic` makes Mechanism A worse: the old build systems compile with `-dynamic` (generating `.o` but still not `.dyn_o`), and the linker now definitely requires `.dyn_o` because it's doing a dynamic link

The only clean fix: pre-build all 5 roots manually with `ghc -fPIC -dynamic -c` + `ghc-pkg register`. This patches them into the cabal store with the correct dynamic objects, after which cabal can use them as dependencies normally.

### `indexed-traversable` was not in the initial analysis

The initial status document listed `tagged`, `clock`, `primitive`, `transformers-compat`, and `QuickCheck` as blockers. Post-extraction log analysis found `indexed-traversable-0.1.4` is an additional Mechanism A root — it blocks `comonad`, `free`, `optics-core`, `pqueue`, `trifecta`, and `witherable`. `comonad` is actually blocked by BOTH `tagged` and `indexed-traversable`, making it particularly unlikely to be fixed by patching only one root.

### `transformers-compat-0.8` is NOT a cascade root

The initial analysis listed `transformers-compat-0.7.2` as a blocker. In practice, `transformers-compat-0.8` compiled successfully and contributed 10 functions to the corpus. The extraction log showed one error message attributing a failure to `transformers-compat-0.8` during `circular`'s build, but this was a cabal store hash-mismatch issue (the version built as a standalone target had different build flags than the version cabal selected for `circular`'s dependency plan), not a fundamental build failure of `transformers-compat` itself.

### Duplication is not the diminishing returns signal

An analysis of corpus uniqueness revealed:
- 99.8% of functions have unique AST bodies (only 6 true duplicates across 2,574 functions)
- All 6 duplicates are trivially simple functions (`flip`, `const`, `mapFst`, etc.) independently implemented in multiple packages
- Every package is 100% unique internally
- Same-name functions across packages (617 name collisions) all have different implementations

The diminishing returns signal is **structural skew**, not duplication: 55% of functions have ≤10 AST nodes, and 72.7% have `Lam` as the top-level tag. Additional packages would continue adding structurally unique functions, but likely more of the same shallow utility patterns.

### Cabal store caching hides dump files

Some packages appear to "compile successfully" (cabal exits 0) but produce no dump files. This happens when cabal finds the package already built in its store (from a previous run as a dependency of another package). The store build was done without `-ddump-simpl`, so no dump file was written. Subsequent builds are cache hits and also skip the dump.

The fix (`-fforce-recomp`) regenerates the dump for the target package module but doesn't help for dependencies. Packages like `dlist` (built as a dep of `dlist-instances`) had this issue and required manual extraction from pre-existing dump files found in the cabal work directory.

---

## 2026-02-22 — Cascade root cause and fix

### The "catch-22" was a single bug, not two mechanisms

The cascade analysis described two distinct failure mechanisms (Mechanism A: `.dyn_o` missing from old build systems; Mechanism B: missing static `.hi` for GHC boot packages on Arch). The proposed fix for B was passing `-dynamic` project-wide, but that was said to make A worse. This framing was wrong.

Both symptoms were downstream of **one bug**: `GHC_DUMP_FLAGS` in `extract_hackage.py` included `-dynamic` as a `--ghc-options` value. This is the wrong way to request dynamic linking from cabal. What actually happens:

1. Cabal normally uses `-dynamic-too` internally to build both `.o` and `.dyn_o` in one GHC pass
2. When the user explicitly passes `-dynamic` via `--ghc-options`, GHC logs `"warning: -dynamic-too is ignored when using -dynamic"` and enters pure dynamic-only mode
3. In dynamic-only mode, GHC still names output files `.o` (not `.dyn_o`) — because the file names are controlled by cabal's `-o` flag, not by GHC's way suffix
4. Cabal's linker stage then tries to build the `.so` by looking for `.dyn_o` → link failure on every package

The `.dyn_o` files in dist-newstyle (e.g. `Data/Tagged.dyn.dump-simpl`) were already there from the dynamic compilation pass — that part was working. The failure was entirely in the linking stage.

### The fix: global cabal config, not per-invocation flags

Setting these three lines in `~/.config/cabal/config`:
```
library-vanilla: False
shared: True
executable-dynamic: True
```
tells cabal to use dynamic-only mode globally, without any `--ghc-options` interference. With this config:
- cabal correctly manages the "dyn way" internally
- `-dynamic-too` is not suppressed
- `.dyn_o` files are produced correctly
- No per-package patching or manual `ghc-pkg register` needed

Removing `-dynamic` from `GHC_DUMP_FLAGS` is the companion change. The dump flags now contain only GHC dump/diagnostic flags; dynamic linking is a cabal-level concern, not a GHC-flag concern.

**Test validation**: `tagged-0.8.10` rebuilt from scratch, produced `Data/Tagged.dyn_o` and `Data/Proxy/TH.dyn_o`. `contravariant-1.5.6` built successfully including its `StateVar-1.2.2` dependency (which previously failed with "Could not find module 'Foreign.Storable'").

### `--ghc-options` passthrough does not compose with cabal's way system

This is the general lesson: cabal has its own way management system (vanilla, prof, dyn, dyn+prof). Passing `-dynamic` via `--ghc-options` bypasses this system — it's a raw GHC flag, not a cabal "enable shared" directive. The right interface for controlling ways is cabal's own flags (`--enable-shared`, `--disable-library-vanilla`, or the config file equivalents).

The dump flags (`-ddump-simpl`, `-dsuppress-*`) are legitimately passed via `--ghc-options` because cabal has no concept of "dump GHC Core". But anything that affects how GHC objects are built (static vs dynamic, profiling, etc.) should go through cabal's flag interface.

### Zero-yield packages were an artifact of build timing, not parse failures

Of the 17 packages with dump files but 0 extracted functions, at least 3 (`tagged`, `microlens`, `text-short`) actually have extractable functions — they were confirmed to yield 17, 11, and 14 functions respectively when run manually. The zero yield in `corpus_hackage.json` is because the corpus was finalized before those packages had dump files (they were rebuilt later). The re-extraction run will capture them.

The truly zero-yield packages are typeclass-heavy or trivial (`mtl`, `void`, `type-equality`, `data-has`) where the cleaner correctly drops all bindings.

### Package status visibility is essential for iterative extraction

Digging through 200+ lines of stderr to determine which packages built, which had dump files, and which extracted functions was unsustainable. The key tooling gap was:
- No persistent record of per-package build success/failure
- No way to view current state without re-running extraction
- No separation between "package failed to build" and "package built but extracted nothing"

`pkg_status.py` addresses this by synthesising state from the filesystem + corpus at any time. `--status-out` in `extract_hackage.py` persists build_ok/error/parse_rate for each run. Together these give a clear table after every extraction run without log archaeology.

---

## 2026-02-22 — De Bruijn Normalization (Pillar 2)

### De Bruijn reduces binder-slot count, not total vocabulary size

The motivation for de Bruijn indices was to shrink vocabulary from ~1,549 tokens to ~100-200. The normalize step alone does not achieve this. Running the new normalize.py on the full 4,438-function corpus produces a vocabulary of 4,463 tokens — larger than sprint 3's 1,549.

Why: de Bruijn eliminates `v0`–`v34` (35 named binder tokens) and replaces them with `0`–`14` (15 index tokens), saving ~20 slots. But the 4,438-function corpus (vs 2,574 in sprint 3) introduces far more unique free variable names: constructors from more packages, more prim ops, more function names in `DEF` tokens. The binder savings are swamped by the larger free-variable vocabulary.

The actual vocabulary reduction happens in the **tokenizer rebuild**, not in normalize. The tokenizer applies frequency cutoffs that drop rare free variable names (keeping only the ~50-100 most common constructors, prim ops, and patterns) and replaces the rest with `<unk>`. De Bruijn enables this: with named binders, every `v0` in one function and every `v0` in another function are the same token — eliminating them removes a structurally important but vocabulary-inflating token class. The tokenizer can then focus its budget on the tokens that carry semantic content.

**Rule of thumb**: de Bruijn changes the *type* of tokens needed (positional integers instead of named slots), which enables a smaller target vocabulary in the tokenizer. It does not directly reduce the count of tokens emitted by normalize.

### Mutable push/pop stack is the right model for de Bruijn, not immutable child scopes

Sprint 3's `Renamer` used an immutable child scope pattern: `child()` copies the env dict, the child assigns fresh names as it recurses, and `sync_counter()` propagates counter advances back to the parent. This was necessary because the counter had to advance globally across all subexpressions — each new binder consumed the next integer regardless of nesting.

De Bruijn indices are positional, not counted. Index 0 always means "the innermost binder". There is no global counter to synchronize. The right model is a mutable stack: push a name when entering a scope, pop it when leaving, and look up by scanning from the top (innermost). No child copies, no sync. This simplification removes the main complexity of the old renamer.

### Lambda lifting is still necessary under de Bruijn

De Bruijn eliminates the need for alpha-renaming (two alpha-equivalent terms produce identical de Bruijn representations, so there's nothing to "choose"). But lambda lifting — converting free variables (external function references) to outermost Lam binders — is still required.

The reason: the model generates closed expressions. If a function body refers to `fMap` as a free variable, the model would need to generate `fMap` as a token, which requires a large vocabulary entry. Lambda-lifting wraps the body in `(Lam N ...)` and replaces `fMap` with a de Bruijn index, keeping the model output in closed form. The `deps` metadata records which index maps to which external function name, so the execution engine can reconstruct the open form.

### Re-alpha-rename augmentation is an identity under de Bruijn

Sprint 3's augment.py generated 3 re-alpha-renamed variants per function (with different counter offsets), producing roughly 3x corpus expansion. Under de Bruijn, all alpha-equivalent terms already have identical token sequences — there is no "different naming" to choose from. Running re-alpha-rename on de Bruijn sequences produces the same token list every time; the dedup step would discard all variants. The augmentation is a no-op and was removed.

The surviving augmentation (commutative operand swap) is still valid under de Bruijn because swapping `arg1` and `arg2` in `(App Prim + arg1 arg2)` does not introduce or remove any binders — both operands are closed sub-expressions with their own de Bruijn indices intact.

### De Bruijn canonicalization surfaces cross-package structural duplicates at augment time

Running augment.py after de Bruijn normalization revealed 62 "original" sequences that were identical to another original and got deduped. These are functions from different packages that happen to have the same canonical structure — for example, two packages that independently define `id` or a simple wrapper produce `DEF name (Lam 1 Var 0) EOF`, which is the same sequence regardless of the original variable names.

With named variables (sprint 3), these functions would have had distinct token sequences because both would use `v0` as the binder name — they'd look identical anyway in that case. But for more complex structural coincidences (same recursion scheme, same argument count, same constructor pattern), de Bruijn makes the duplication visible that was previously hidden by different local naming choices.

The 62 deduped originals (out of 4,277) represent ~1.4% of the corpus — small enough to ignore, but a useful signal that the canonicalization is working correctly.

### `@T` type binders don't participate in val-level de Bruijn indexing

GHC Core has two separate namespaces: type-level and value-level. When a `Lam` node has both type binders (`@T`) and val binders, only val binders are pushed onto the de Bruijn stack. Type binders do not increment the val-level depth counter. This means `Var` references in the body are indexed relative to only the val binders above them, regardless of how many `@T` binders are interleaved.

In the serialization: `(Lam @T @T 2 body)` — two type binders (kept as `@T` tokens) plus two val binders (count = 2). The body references val binders at indices 0 and 1 with no offset for the type binders.

### Integer tokens serve triple duty — disambiguation is positional

Under de Bruijn, the same integer token family (`0`, `1`, `2`, ...) appears in three distinct roles:
- **Binder count** — after `(Lam`, the integer says how many val binders this lambda introduces
- **De Bruijn index** — after `Var`, the integer gives the binding depth (0 = innermost)
- **Literal value** — after `Lit`, the integer is a numeric constant

These roles never overlap syntactically — the parser knows from its current grammar position which interpretation applies. The tokenizer and model see only a single integer token, but the structural context resolves the meaning. This is not ambiguous for generation: the model emits the integer token in the correct position, and constrained decoding (via the checker) validates that position.

The same principle applies to arity counts in patterns: after `(PCon constructor`, an integer is the pattern arity; after `Var`, it is a de Bruijn index. Same token, different grammar position.

### Hardcoded constructor names break immediately on a real corpus

Sprint 3's checker hardcoded `CONSTRUCTOR_NAMES = {':', '[]', 'True', 'False', 'Just', 'Nothing', '(,)', '(,,)'}`. This was designed for the 22 hand-written benchmark functions and was never tested against Hackage code. When the Hackage corpus arrived, the very first non-trivial function (`finalBlankPolicy`) used a `Splitter` constructor in a pattern — immediately a `ParseError: Unknown constructor in pattern: 'Splitter'`.

Hackage packages define hundreds of package-specific constructors. Any hardcoded list is the wrong model for a real corpus. The fix: accept any non-structural token as a constructor name. The vocabulary cutoff in the tokenizer already limits which constructors the model can generate; the checker does not need to duplicate that constraint.

**Rule**: hardcoded token sets in the checker are only appropriate for tokens that have fixed meaning independent of the corpus (structural keywords, prim ops). Tokens whose valid values depend on what was compiled should be validated by the tokenizer vocabulary, not by an enumeration in the checker.

### Type check false negatives are unavoidable with a simplified type system

After fixing the grammar issues, 87.4% of Hackage corpus sequences pass the full `type_check_sequence` check. The 12.6% that fail do so with `type: Arg type mismatch` errors, not parse errors. These are false negatives from the simplified Int/Bool/List/Maybe type system:

- The simplified system assigns `+` type `Int → Int → Int`
- GHC Core functions sometimes pass `+` as a higher-order argument (e.g., as a folding function), where the call site expects a function type, not `Int`
- `_compat(FunT(Int,Int→Int), Int)` → False → TypeCheckError

These are valid Haskell programs that the simplified checker incorrectly rejects. The 24/24 benchmark functions all pass, which is what matters for constrained decoding during benchmark evaluation. The false negatives on Hackage functions are acceptable — they would cause the constrained decoder to produce slightly different outputs, but those functions aren't benchmark targets.

The lesson: a simplified type system is useful for catching structural nonsense (applying `+` to a list constructor), but will have false negatives on any real corpus that uses higher-order functions beyond the typed primitives. Don't chase 100% pass rate by expanding the type system — that way lies writing a Hindley-Milner solver. Accept the false negatives and ensure the benchmark subset is clean.

---

---

## 2026-02-22 — Constrained Decode and Pretty-Print (Pillar 2)

### `<eos>` passes `is_type_valid` as a spurious Var name

`is_type_valid(partial, next_token)` calls `parse_sequence(partial + [next_token])`. The parser is lenient — it returns early (without error) whenever the token stream is exhausted mid-parse. This means `<eos>` can pass validity at any position where `Var` is legal: `parse_sequence` consumes `Var`, then tries to consume the next token as a name; it finds `<eos>`, treats it as a Var name, and since the stream is now exhausted, returns without error.

The result: with the old masking code (`tok not in ('<pad>', '<bos>', '<unk>')`), `<eos>` was included in the valid mask at every `Var` position — potentially causing the model to terminate early.

**Fix**: add `'<eos>'` to the exclusion list in the mask loop, then manually set `mask[EOS_ID] = True` when `partial[-1] == 'EOF'` (the grammar's end-of-sequence marker). This gates EOS on actual sequence completion rather than grammar leniency.

**General lesson**: if the validity checker is lenient (returns True on incomplete sequences), special non-grammar tokens (`<eos>`, `<sep>`, etc.) will slip through anywhere the grammar expects a token it doesn't recognise. Always explicitly exclude special tokens from the grammar validity check and handle them with separate logic.

### De Bruijn pretty-printing: push order determines index 0

When a Lam has `n_val = 3`, the three binder names are pushed onto the stack left-to-right during normalization: `r.push(name_0)`, `r.push(name_1)`, `r.push(name_2)`. Since lookup scans from the top of the stack (`reversed(stack)[0]` = innermost = index 0), `name_2` (the last pushed) has index 0, `name_1` has index 1, `name_0` has index 2.

When reversing this in the pretty-printer: generate fresh names `[a, b, c]` in left-to-right "human" order, then prepend them reversed: `new_env = [c, b, a] + outer_env`. Print the function header as `f a b c = body`. `Var 0` → `env[0]` = `c` ✓.

The same push-order rule applies to Let (all bind names pushed before vals), PCon, and PTuple patterns (vars pushed in declaration order, last = index 0 for the alt body).

**Rule**: "last pushed = index 0" is the invariant. When generating fresh names for `n` binders printed left-to-right, prepend them to the env in reverse order.

### Let is mutually recursive — vals share the same env as the body

In `normalize.py`, all Let bind names are pushed onto the stack before any bind val is converted. This means each bind val can reference other bind names (mutual recursion). The pretty-printer must use the same `let_env` (with all N bind names) for both the vals and the body — not a sequence of growing envs where each val sees only earlier binds.

Using sequential envs (where bind val i sees only bins 0..i-1) would produce wrong de Bruijn index lookups for any mutually recursive let binding.

---

### Dependency-ordered building prevents cabal store races

When `--parallel N` submits all packages simultaneously, multiple workers may try to build the same dependency concurrently. Cabal's store is designed for concurrent access in newer versions, but even if it handles the race, the result is N redundant builds of the same package.

Wave-based ordering (Kahn's algorithm on the dependency graph restricted to our package list) ensures:
1. Wave 0 packages (no intra-list dependencies) build in parallel — no races possible
2. By the time wave 1 starts, all wave 0 packages are in the cabal store
3. Wave 1 packages find their dependencies pre-built and skip dependency compilation

The wave decomposition for our 201-package list produced 10 waves: wave 0 has 105 packages (parallelizable), waves 1-9 total 96 packages with sequential wave boundaries. Effective parallelism is high for the bulk of packages, with sequential constraints only for the deep dependency chains.

---

## 2026-02-22 — Pillar 3: Benchmark and Training (Model and Evaluation)

### Val/train ratio is the right overfitting diagnostic — and it works

Sprint 3's 14.07x val/train ratio was the clearest signal of memorization. Sprint 4's right-sized model (2L/d128/~450K params) trained on 5,078 de Bruijn sequences achieved **1.56x at epoch 50** with early stopping. The ratio dropped 9x because:
1. De Bruijn normalization removed named binder tokens — the model can no longer memorize `v0`→`x`, `v1`→`acc` mappings
2. The larger corpus (4,438 vs 1,649 base functions) makes memorization harder
3. The smaller model has fewer parameters to overfit with

This validates the sprint hypothesis: canonical representation + more data + right-sized model = generalization signal.

### All-unk function names create identical prompts — the model cannot distinguish tasks

With min_freq=25, all function names fall below the vocabulary threshold and map to `<unk>`. Every benchmark task's prompt becomes `DEF <unk>`. The model sees the same 3-token prefix (`<bos> DEF <unk>`) for every function and outputs a body sampled from its prior over all training sequences.

Attempted fix: add function names to `ALWAYS_KEEP` in the tokenizer. This works mechanically but defeats the generalization design — function names are arbitrary, infinite, and should not be learned. The model would memorize `fId` → identity body, failing on any novel name.

Correct mental model: function names are like variable names in programming — they carry no semantic content beyond identity. The model should learn structural patterns (what does an identity body look like?), not name→body mappings (what body does `fId` have?).

### Few-shot in-context learning is the right disambiguation mechanism — but requires matching training format

The intended solution for task disambiguation: prepend 2-3 structurally similar functions (with their bodies) before the `DEF <unk>` target prompt. The model sees context and continues with a compatible body. This is in-context learning (ICL).

ICL only works if the model was **trained on the same format**. Our model was trained on single-function sequences (`[BOS] DEF <unk> body [EOS]`). At inference, prepending 2 context functions creates a format the model has never seen:
```
[BOS] DEF <unk> [body1] EOF DEF <unk> [body2] EOF DEF <unk>
```
The model doesn't know to "continue with a new body" — it treats the context tokens as an anomaly and generates from its prior, usually copying the most recent body from context.

**Lesson**: ICL format at inference must match ICL format at training. If the benchmark uses multi-function prompts, the training data must also contain multi-function sequences.

### Multi-function context training is a simple dataset modification

The fix is in `ASTDataset.__getitem__`: with probability `few_shot_prob` (default 0.5), concatenate 1-2 randomly selected context sequences before the target sequence. The model trains on:
```
[BOS] ctx1_body ctx2_body target_body [EOS]
```
No architecture change. No new loss function. Just a dataset augmentation. The model learns to predict each body given the bodies that preceded it in the same context window — exactly the inference format.

One subtlety: context sequences must strip their BOS and EOS, keeping only the body tokens (`DEF <unk> ... EOF`). The combined sequence has exactly one BOS and one EOS, wrapping N function bodies.

### Batch GPU generation is ~k× faster than serial generation

`generate_batch()` runs k candidates in one forward pass of shape `(k, T, V)` instead of k serial passes of `(1, T, V)`. On a GPU, the extra batch dimension costs almost nothing — the bottleneck is memory bandwidth, not compute, and k=10 sequences fit trivially alongside batch size 1.

For constrained decoding (which uses a stateful per-sequence parser), batching is not directly applicable — each sequence can take a different valid path through the grammar. Constrained decode stays serial.

### Parallel Haskell evaluation removes the other bottleneck

After generating k candidates, each requires a `runhaskell` subprocess (~0.5-2 seconds each). Serial evaluation of k=10 candidates costs up to 20 seconds per task, dominating the benchmark runtime. `ThreadPoolExecutor(max_workers=8)` parallelizes all valid candidates within a task, bringing wall time to ~max(individual eval times) ≈ 2 seconds per task instead of k × 2 = 20 seconds.

Combined with batch generation, the benchmark runs roughly 10-20× faster than the naive single-threaded approach.

### Function name DI (dependency injection) and top-level DEF name are different concerns

The user's "DI format" instruction was about **cross-function calls**: if `fSumSquares` calls `fMap`, `fSquare`, and `fSum`, those calls are lambda-abstracted to outermost Lam parameters. This was already implemented in `normalize.py` via `debruijn_convert()` + `collect_free_vars()`.

This is separate from the **top-level DEF name** (`fSumSquares` after `DEF`). DI converts:
```
fSumSquares xs = fSum (fMap fSquare xs)
→ DEF fSumSquares (Lam 4 (App Var 2 (App Var 1 Var 0 Var 3))) EOF
  deps: [{param:0,func:xs}, {param:1,func:fSquare}, {param:2,func:fSum}, {param:3,func:fMap}]
```
The `fSumSquares` name itself still appears as a token and → `<unk>`. DI handles the body's external references but says nothing about the header name. Both need to be `<unk>` for the design to hold; both are.

The confusion arose because DI was confirmed implemented but the benchmark was still failing. The missing piece was ICL, not DI — cross-function calls were already handled correctly.

---

## 2026-02-22 — RL Debugging (Pillar 3)

### Random concatenation training teaches the model to *ignore* context

After retraining with `few_shot_prob=0.5`, the benchmark still returned 0/38. Verbose inspection showed the model generating structurally wrong bodies regardless of the few-shot context — e.g., `fDouble` producing Case expressions instead of `Prim + Var 0 Var 0`. Verified with 50 direct-prompt samples: the correct body never appeared.

The root cause: training concatenations were **random** (arbitrary pairs from the corpus). The model's optimal response to random context is to ignore it — the context carries no information about which body to generate next. The training signal teaches "the context is noise," and the model learns exactly that.

ICL works only when context is *informative* — when seeing related functions genuinely constrains what the next function should look like. Random pairing destroys this signal before the model can learn it.

**Rule**: if you want the model to condition on context at inference, the training context must be selected using the same principle as inference context. Random groupings produce a model that ignores context. Structured groupings (same structural class, same recursion scheme) produce a model that uses context.

### The val < train ratio is not a bug — it reflects training difficulty asymmetry

Run 2 achieved ratio = 0.79x (val loss 0.320 < train loss 0.407). This looks like the model is "worse on training data" which seems backwards. The explanation:

- Training examples include 50% multi-function concatenations (harder: the model must predict a body given random preceding bodies)
- Validation examples are all single-function sequences (the same as run 1)

The model's val loss dropped from 1.028 → 0.320 nats — a genuine improvement. The train/val framing is misleading when train and val distributions differ. The absolute val loss is the right diagnostic; the ratio is only meaningful when both splits use the same format.

### `collect_winners` filtered `<unk>` → 0 candidates on every task

The token extraction loop in `collect_winners`:
```python
tokens = [tok.id_to_token(i) for i in full_ids
          if tok.id_to_token(i) not in ("<pad>", "<bos>", "<unk>", None)]
```

Function names are `<unk>` in the vocab. After `DEF`, the next token should be the function name — but `<unk>` is filtered out. So `tokens` = `["DEF", first_body_token, ...]`. Then `parse_sequence` consumes `"DEF"`, then tries to consume the function name, gets `"LAM"` (or whatever the first body token is), and uses that as the name. Every subsequent parse either fails or produces a `func_def` with name `"LAM"` that doesn't match the task — causing `tokens_to_haskell` or downstream checks to fail. Result: 0 parseable candidates on every task, 0 winners, RL deadlocked.

The same bug had been fixed in `run.py` via `_clean_tokens` (replaces `<unk>` after `DEF` with the actual function name). `rejection_sample.py` pre-dated that fix and was never updated.

**Fix**: apply the same substitution logic in `collect_winners`:
1. Clip `gen_ids` at first EOF token (same as `run.py`)
2. For each token: if `<unk>` and previous token was `DEF`, append `task["name"]`; if `<unk>` elsewhere, drop it; else keep it

Also: use `prompt_ids[-2:]` (just `[DEF, <unk>]`) instead of `prompt_ids[1:]` (which strips only BOS). This ensures the extraction starts from the right position regardless of prompt length.

### RL's `run_round` had a self-perpetuating deadlock

The original `run_round`:
```python
if total_winners == 0:
    print("[rl] no winners — skipping fine-tune this round")
    return _eval_all(...)
```

With 0 winners, `finetune` is never called → model never updates → model continues to generate the same wrong outputs → 0 winners next round → loop forever. The `mix_corpus` parameter in `finetune` was designed to help (train on corpus sequences even with few winners), but it was unreachable because `run_round` returned before calling `finetune`.

**Fix**: when `total_winners == 0` and `mix_corpus=True` (the default), still call `finetune([], corpus_seqs, ...)`. The `finetune` function already handles `winner_seqs=[]` + `mix_corpus=True` correctly (trains on corpus sequences only). Over multiple rounds, corpus fine-tuning reshapes the model's distribution — eventually moving probability mass toward correct structures, at which point model-generated winners start appearing and the standard RL loop takes over.

Only skip `finetune` entirely if `--no-corpus-mix` is explicitly set (the user actively opted out of corpus mixing).

### EOF clipping is required in both run.py and rejection_sample.py

The model trained on few-shot sequences has seen the format: `[BOS] body1 body2 target_body [EOS]`. After generating `target_body EOF`, it continues with more function bodies (because training data had them). Without clipping at the first `EOF` token, the generated sequence contains multiple function blocks, and `parse_sequence` parses the first block (which may be `body1` if the full context is included, or the correct target if only gen_ids are included).

`run.py` was fixed first: `target_ids = prompt_ids[-2:] + gen_ids`, then clip at `eof_id`. `rejection_sample.py` needed the same fix, which was added as part of the `collect_winners` rewrite.

**Rule**: whenever the training format includes multi-function sequences, the inference extraction must clip at the first grammar-level terminator (`EOF`), not at the model's sequence terminator (`<eos>`). The model's EOS comes *after* EOF and is not a reliable extraction boundary.
