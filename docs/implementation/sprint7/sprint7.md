# Sprint 7 — Named Global References (Hybrid de Bruijn)

## Motivation

Sprint 6 proved that adding 80 composition training examples does **not** teach the model to compose at inference time. The root cause, identified in the Sprint 6 lessons learned:

> The gap is in _variable binding under de Bruijn indexing for composed types_. In `fCountPositive :: [Int] -> Int`, the body `fLength (fFilter fIsPositive xs)` requires Var 0 to correctly refer to `xs` through two levels of application. The compositions in the corpus use explicit argument names that normalize to Var 0, but the model must also generalize this to novel function combinations.

The current representation makes composition unnecessarily hard. Consider `fCountNegative xs = fLength (fFilter fIsNegative xs)`:

**Current (Sprint 6):**

```
DEF fCountNegative TY [Int] -> Int BODY (Lam 4 (App Var 3 (App (App Var 2 Var 1) Var 0))) EOF
```

The model must learn that `Var 3 = fLength`, `Var 2 = fFilter`, `Var 1 = fIsNegative` — three anonymous bindings that change indices depending on how many dependencies a function has. For a novel composition the model has never seen, it must correctly assign de Bruijn indices to the right function references _and_ thread the argument binding through the correct depth. This is the bottleneck.

**Proposed (Sprint 7):**

```
DEF fCountNegative TY [Int] -> Int BODY (Lam 1 (App fLength (App (App fFilter fIsNegative) Var 0))) EOF
```

Global function references appear by name. Only the actual lambda parameter (`xs`) uses a de Bruijn index (`Var 0`). The composition structure is now explicit: `App fLength (App (App fFilter fIsNegative) Var 0)` directly encodes "apply fLength to the result of applying fFilter with predicate fIsNegative to the argument."

This is a **simplification**, not an added complexity. The lambda-lifting of external dependencies was an artifact of treating every function body as a closed term. For functions that reference other corpus functions, naming those references directly is both more natural and more learnable.

## Hypothesis

**If global function references are named instead of lambda-lifted, the model will be able to compose known functions at inference time, because the composition pattern (App fX (App fY arg)) is directly learnable without requiring correct de Bruijn index assignment across varying dependency counts.**

Decisive test: Tier 2 pass@10 >= 5/10 post-RL (Sprint 6 achieved 0/10).

## Design

### Hybrid de Bruijn Representation

Two classes of variable references in the IR:

| Reference type             | Current representation             | Sprint 7 representation              |
| -------------------------- | ---------------------------------- | ------------------------------------ |
| Lambda-bound parameter     | `Var N` (de Bruijn index)          | `Var N` (unchanged)                  |
| Self-recursive call        | `SELF`                             | `SELF` (unchanged)                   |
| Known corpus function      | `Var N` (lambda-lifted, anonymous) | `fName` (named token, no Var prefix) |
| Unknown external (Hackage) | `Var N` (lambda-lifted, anonymous) | `Var N` (lambda-lifted, unchanged)   |

**Key design decisions:**

1. **Named references have no `Var` prefix.** They appear directly as tokens in App position: `(App fLength ...)` not `(App Var fLength ...)`. This distinguishes them syntactically from de Bruijn variables and eliminates ambiguity in the parser.

2. **Only corpus function names are eligible for named references.** The set of named globals is exactly `_CORPUS_FUNCTION_NAMES` from `tokenizer.py` — the same ~430 names already in the vocabulary. No new tokens needed.

3. **Unknown externals stay lambda-lifted.** Hackage functions that reference symbols not in the corpus vocabulary (e.g., package-internal helpers) continue to use anonymous de Bruijn bindings. This preserves the closed-term property for functions whose dependencies are not in the model's vocabulary.

4. **`deps` metadata is simplified.** Named references no longer need `deps` entries since they're explicit in the body. Only unknown lambda-lifted externals need `deps` for un-lifting at benchmark time.

5. **Outer Lam binder count decreases.** A function like `fCountNegative` that previously had `(Lam 4 ...)` (3 deps + 1 real param) now has `(Lam 1 ...)` (1 real param). The model sees the true arity.

### What changes, what doesn't

| Component                             | Changes?       | Details                                                                             |
| ------------------------------------- | -------------- | ----------------------------------------------------------------------------------- |
| `normalize.py` — `debruijn_convert()` | **Yes**        | Skip lambda-lifting for deps in `_CORPUS_FUNCTION_NAMES`; emit named tokens instead |
| `normalize.py` — `serialize_expr()`   | **Yes**        | New Var case: named global emits just the name token (no `Var` prefix)              |
| `normalize.py` — `to_sequence()`      | Minor          | `deps` list now only contains unknown externals                                     |
| `tokenizer.py`                        | **No**         | All corpus function names already in vocabulary via `_CORPUS_FUNCTION_NAMES`        |
| `checker.py` — `parse_expr()`         | **Yes**        | Recognize corpus function names as valid expression atoms (like Var but named)      |
| `pretty_print.py` — `pp_expr()`       | **Yes**        | Handle named global references (emit name directly, no de Bruijn lookup)            |
| `run.py` — `_unlift_deps()`           | **Simplified** | Only un-lifts unknown deps; most compositions have zero unknown deps                |
| `run.py` — `build_corpus_registry()`  | Minor          | Fewer deps to un-lift                                                               |
| `train.py`                            | **No**         | No model architecture changes                                                       |
| `augment.py`                          | **No**         | Commutative swap still operates on `(App` tokens in body                            |
| `tasks.json`                          | **No**         | Prompts are `DEF name TY type BODY` — body is generated, not specified              |
| `Compositions.hs` / `Functions.hs`    | **No**         | Source Haskell unchanged                                                            |
| Makefile                              | **No**         | Pipeline targets unchanged                                                          |

### Worked examples

#### Example 1: Composition function (all deps known)

`fCountNegative xs = fLength (fFilter fIsNegative xs)`

**Current (Sprint 6):**

- Free vars: `{fFilter, fIsNegative, fLength}` → lambda-lift all 3
- deps: `[{param:0, func:"fFilter"}, {param:1, func:"fIsNegative"}, {param:2, func:"fLength"}]`
- Body: `(Lam 4 (App Var 3 (App (App Var 2 Var 1) Var 0)))`
- Var 0 = xs, Var 1 = fIsNegative, Var 2 = fFilter, Var 3 = fLength

**Sprint 7:**

- Free vars: `{fFilter, fIsNegative, fLength}` → all in corpus → name directly
- deps: `[]` (none lambda-lifted)
- Body: `(Lam 1 (App fLength (App (App fFilter fIsNegative) Var 0)))`
- Var 0 = xs, named refs = fLength, fFilter, fIsNegative

The model sees `(App fLength (App (App fFilter fIsNegative) Var 0))` — a direct structural template for "apply fLength to the result of fFilter with predicate fIsNegative on the argument."

#### Example 2: Simple function (no deps)

`fDouble x = x * 2`

**Current:** `(Lam 1 (App (App Prim * Var 0) Lit 2))`
**Sprint 7:** Identical — no external deps, nothing changes.

#### Example 3: Self-recursive function

`fFactorial n = if n == 0 then 1 else n * fFactorial (n-1)`

**Current:** SELF token for recursive call, no deps → `(Lam 1 (Case ... (App SELF ...)))`
**Sprint 7:** Identical — SELF is unchanged.

#### Example 4: Hackage function with unknown deps

`someHackageFn x = unknownHelper (anotherHelper x)`

Where `unknownHelper` and `anotherHelper` are not in `_CORPUS_FUNCTION_NAMES`.

**Current:** Lambda-lift both, deps: `[{param:0, func:"anotherHelper"}, {param:1, func:"unknownHelper"}]`
**Sprint 7:** Identical — unknown deps still lambda-lifted with de Bruijn indices.

#### Example 5: Mixed deps (known + unknown)

`someHackageFn x = fMap unknownHelper x`

Where `fMap` is in corpus but `unknownHelper` is not.

**Current:** Lambda-lift both → `(Lam 3 (App (App Var 2 Var 1) Var 0))`, deps for both.
**Sprint 7:** Lambda-lift only `unknownHelper` → `(Lam 2 (App (App fMap Var 1) Var 0))`, deps: `[{param:0, func:"unknownHelper"}]`.

`fMap` is named directly; `unknownHelper` remains anonymous at de Bruijn index adjusted for one fewer lifted binder.

### Impact on model learning

The key insight is what the model needs to learn for Tier 2 tasks:

**Current (Sprint 6) — what the model must learn for `fCountPositive`:**

> "For a function named fCountPositive of type [Int] -> Int, emit Lam with 4 binders, then App Var 3 applied to (App (App Var 2 Var 1) Var 0), where the indices 3, 2, 1 correspond to fLength, fFilter, fIsPositive in sorted order."

The model must memorize that this specific combination of functions produces indices 1, 2, 3 in this specific order. For a novel combination it has never seen, it must _invent_ the correct index assignment.

**Sprint 7 — what the model must learn for `fCountPositive`:**

> "For a function named fCountPositive of type [Int] -> Int, emit Lam with 1 binder, then App fLength applied to (App (App fFilter fIsPositive) Var 0)."

The model must learn that `fCountPositive` = `fLength (fFilter fIsPositive xs)`. The function names are direct tokens. The only de Bruijn index is `Var 0` for the single parameter. This is a pattern the model already sees in training: `App fName (App (App fName fName) Var 0)` with specific names filled in.

## Phases

### Phase 1 — normalize.py: Named Global References

**Goal:** Modify `debruijn_convert()` and `serialize_expr()` so that references to known corpus functions appear as named tokens instead of anonymous de Bruijn indices.

**Changes to `debruijn_convert()` (line 220):**

1. Accept a `known_names: set[str]` parameter (the set of corpus function names).
2. Partition `external_deps` into `known_deps` (in `known_names`) and `unknown_deps` (not in `known_names`).
3. Only lambda-lift `unknown_deps`. Store `known_deps` as named references.
4. Pass `known_names` to `convert_expr()` so it emits named references instead of de Bruijn lookups for known globals.
5. Only include `unknown_deps` in the returned `deps` list.

**Changes to `convert_expr()` (line 140):**

1. Accept `known_names: set[str]` parameter.
2. When processing a `Var` node whose name is in `known_names` (and is a free variable, not bound by a lambda): emit a special node `{"tag": "GlobalRef", "name": var_name}` instead of looking up a de Bruijn index.

**Changes to `serialize_expr()` (line 342):**

1. Handle new `GlobalRef` tag: emit just the name token (e.g., `fLength`), no `Var` prefix.

**Changes to `to_sequence()` (line 453):**

1. Load `_CORPUS_FUNCTION_NAMES` (import from tokenizer.py, or pass as parameter).
2. Pass `known_names` to `debruijn_convert()`.

**Changes to CLI `main()` (line 477):**

1. Add `--known-names` flag that accepts a file path or uses the default corpus function names.
2. Load names and pass to `to_sequence()`.

**Verification:**

```bash
# Re-extract and normalize
make corpus

# Verify: composition functions should have fewer Lam binders
# fCountNegative: was (Lam 4 ...), should be (Lam 1 ...)
python -c "
import json
seqs = json.loads(open('sprint/corpus/sequences.json').read())
for s in seqs:
    if s['name'] == 'fCountNegative':
        print(' '.join(s['tokens']))
        print(f'deps: {s[\"deps\"]}')
        break
"

# Verify: no corpus function name should appear after "Var" token in any sequence
python -c "
import json
seqs = json.loads(open('sprint/corpus/sequences.json').read())
from sprint.corpus.tokenizer import Tokenizer
names = Tokenizer._CORPUS_FUNCTION_NAMES
errors = []
for s in seqs:
    toks = s['tokens']
    for i, t in enumerate(toks):
        if t == 'Var' and i+1 < len(toks) and toks[i+1] in names:
            errors.append((s['name'], toks[i+1]))
if errors:
    print(f'FAIL: {len(errors)} named refs still behind Var')
    for e in errors[:5]: print(f'  {e}')
else:
    print('PASS: no corpus function names behind Var token')
"

# Verify: simple functions (no deps) unchanged
python -c "
import json
seqs = json.loads(open('sprint/corpus/sequences.json').read())
for s in seqs:
    if s['name'] == 'fDouble':
        print(' '.join(s['tokens']))
        break
"
```

### Phase 2 — checker.py + pretty_print.py: Handle Named References

**Goal:** The type checker and pretty printer must recognize named global references as valid expression atoms.

**Changes to `checker.py` — `parse_expr()` (line 209):**

1. After consuming a token, if it's not in `EXPR_STARTERS` but is a known corpus function name (starts with `f` and uppercase second char, or is in a known set), parse it as a `GlobalRef` node: `{"tag": "GlobalRef", "name": tok}`.
2. Alternative: expand `EXPR_STARTERS` to include all corpus function names. This is simpler but makes the set large. Better approach: treat any token matching `^f[A-Z]` pattern that's not a structural keyword as a GlobalRef.

**Changes to `pretty_print.py` — `pp_expr()` (line 65):**

1. Handle `GlobalRef` tag: emit the function name directly. No de Bruijn environment lookup needed.

**Changes to `run.py` — `_unlift_deps()`:**

1. Function still works but is called less often (most composition functions now have `deps: []`).
2. No code changes needed — it gracefully handles empty deps lists.

**Verification:**

```bash
# Round-trip test: normalize → parse → pretty-print for composition functions
python -c "
import json
from sprint.typecheck.checker import parse_sequence, TokenStream
from sprint.benchmark.pretty_print import tokens_to_haskell
seqs = json.loads(open('sprint/corpus/sequences.json').read())
errors = []
for s in seqs:
    if s['name'].startswith('fCount') or s['name'].startswith('fSum'):
        try:
            ts = TokenStream(s['tokens'])
            ast = parse_sequence(ts)
            _, hs = tokens_to_haskell(s['tokens'])
            print(f'{s[\"name\"]}: {hs}')
        except Exception as e:
            errors.append((s['name'], str(e)))
if errors:
    print(f'\\nFAILED: {len(errors)}')
    for n, e in errors[:5]: print(f'  {n}: {e}')
else:
    print('\\nAll round-trips passed')
"
```

### Phase 3 — Training + Evaluation

**Goal:** Train on the updated corpus and evaluate T2 improvement.

**Sub-steps:**

1. **Rebuild full corpus:**

   ```bash
   # Force rebuild of all generated files
   rm sprint/corpus/sequences.json sprint/corpus/tokenizer.json sprint/corpus/sequences_aug.json
   make corpus
   ```

2. **Verify corpus stats:**
   - Total sequences should be ~4518 (same as Sprint 6 — no new functions added)
   - Vocabulary size should be ~432 (same — no new tokens)
   - Augmented sequences should be ~5215 (same)
   - Key difference: sequence _content_ changes (fewer Lam binders, named refs in body)

3. **Train:**

   ```bash
   make train
   ```

   - Expected: val loss comparable to Sprint 6 (0.45-0.50 range)
   - The model has the same amount of data but the composition patterns are now more directly learnable.

4. **Benchmark (pre-RL):**

   ```bash
   make benchmark
   ```

   - **Minimum bar:** T1 >= 17/22 (no regression from Sprint 6 pre-RL)
   - **Target:** T2 >= 3/10 (any T2 wins would be a breakthrough)
   - **Watch:** T3 should remain at >= 2/6

5. **RL (3 rounds):**
   ```bash
   make rl
   ```

   - **Target:** T2 >= 5/10 post-RL
   - **Stretch:** T3 >= 4/6

### Phase 4 (optional) — Ablation: Named Refs vs Lambda-Lifted

If Phase 3 shows T2 improvement, run an ablation to confirm named refs are the cause:

1. Train with Sprint 6's lambda-lifted corpus on the same checkpoint architecture.
2. Compare T2 pass@10 directly.

This isolates the representation change from any other variables (same data, same model, same training config).

## Success Criteria

| Metric             | Minimum bar     | Target          | Sprint 6 result |
| ------------------ | --------------- | --------------- | --------------- |
| T1 (no regression) | >= 17/22 pre-RL | 22/22 post-RL   | 22/22 post-RL   |
| T2 (compositional) | >= 3/10 post-RL | >= 5/10 post-RL | 0/10            |
| T3 (structural)    | >= 2/6 post-RL  | >= 4/6 post-RL  | 2/6             |
| Total              | >= 27/38        | >= 31/38        | 24/38           |

**Decisive outcome:** If T2 remains at 0/10 after named global references, the composition failure is not a representation problem — it's a model capacity or training signal problem. Next steps would be:

- Larger model (1M+ params) with more composition training data
- Explicit chain-of-thought decomposition tokens (STEP1, STEP2)
- Retrieval-augmented generation at inference time

If T2 reaches >= 3/10, named global references are confirmed as the key enabler. Further improvement would come from:

- More composition training data (Sprint 6's 80 functions, now with useful representation)
- Targeted RL with higher k for T2 tasks
- Beam search with grammar constraints (constrained_decode.py)

## Risk Analysis

| Risk                                                                         | Likelihood              | Mitigation                                                                                                                                                                                                                              |
| ---------------------------------------------------------------------------- | ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| T1 regression from changed representation                                    | Low                     | Hand-written single-step functions have 0-1 deps; their representation barely changes. De Bruijn indexing for lambda params is identical.                                                                                               |
| Hackage functions break due to mixed known/unknown deps                      | Medium                  | Phase 1 verification script checks all functions. Mixed-dep case (Example 5) tested explicitly.                                                                                                                                         |
| Vocabulary collision (function name token appears in non-reference position) | Low                     | Function names already in vocabulary for `DEF name` position. In body position they only appear where free variables were. Parser distinguishes by position: after `Var` = de Bruijn, standalone in App position = named ref.           |
| Augmentation (commutative swap) breaks with named refs                       | Low                     | Commutative swap targets `(App` token pairs in body. Named refs appear inside App — swap still works because it swaps subtrees, not individual tokens. Verify with augment test.                                                        |
| Model learns to copy function names from DEF position to body                | Medium (but acceptable) | This is actually the desired behavior! The model should learn that `DEF fCountPositive TY [Int] -> Int BODY` is followed by a body containing `fLength`, `fFilter`, `fIsPositive`. The name in DEF position is the conditioning signal. |

## Timeline Estimate

- Phase 1 (normalize.py): Primary implementation work. ~2-3 hours.
- Phase 2 (checker.py + pretty_print.py): Small changes, mostly parsing. ~1 hour.
- Phase 3 (train + eval): Training ~30 min, benchmark ~10 min, RL ~30 min. ~1.5 hours.
- Phase 4 (optional ablation): ~1 hour if needed.

Total: ~5-6 hours including verification.

## Files to Modify

| File                               | Change                                                                                                                                                                                | Complexity            |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------- |
| `sprint/corpus/normalize.py`       | `debruijn_convert()`: partition deps into known/unknown; `convert_expr()`: emit GlobalRef for known deps; `serialize_expr()`: handle GlobalRef tag; `to_sequence()`: pass known_names | **High** (core logic) |
| `sprint/typecheck/checker.py`      | `parse_expr()`: recognize named function tokens as GlobalRef expression atoms                                                                                                         | **Medium**            |
| `sprint/benchmark/pretty_print.py` | `pp_expr()`: handle GlobalRef tag (emit name directly)                                                                                                                                | **Low**               |
| `sprint/benchmark/run.py`          | No required changes; `_unlift_deps()` handles empty deps gracefully                                                                                                                   | **None**              |

No new files. No Makefile changes. No tokenizer changes. No task.json changes.
