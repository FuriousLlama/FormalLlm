# Lessons Learned — Sprint 1

Running log of non-obvious findings, gotchas, and decisions made during development.

---

## 2026-02-21 — Architecture

### GHC Core is the wrong foundation for a language-agnostic IR

We used GHC's `-ddump-simpl` output as the source of our token representation. This was
pragmatic for sprint 0/1 but is architecturally unsound for a few reasons.

The token stream we actually want — `Lam`, `App`, `Case`, `Var`, `Lit`, `Con` — is
essentially a typed lambda calculus with ADTs. That representation is largely
language-agnostic. The same IR could represent OCaml, Elm, F#, or any functional language.
The problem is not the representation we chose; it's that we *derive* it from GHC Core
instead of defining it ourselves and writing a clean extractor.

GHC Core is an intermediate representation optimized for GHC's compilation pipeline. Using
it as our IR means GHC's internal machinery leaks into what should be a clean, stable
representation:

- `I#`, `Int#`: boxing/unboxing that doesn't exist in source Haskell
- `$fNumInt`, `$fOrdInt`: explicit typeclass dictionary passing
- `fZipWithAdd1`: synthetic workers for partial application
- `def`, `wild`, `ds`: GHC's internal names for scrutinees and wildcards

These aren't just noise — they make the same source program produce different tokens
depending on GHC's optimization decisions, even at `-O0`. The representation is unstable
and Haskell/GHC-specific, which blocks the path to cross-language generalization.

### Define the IR first, write per-language extractors second

The right architecture:

1. **Define the target IR explicitly** — a small typed lambda calculus: `Lam`, `App`,
   `Case`, `Var`, `Lit`, `Con`, with explicit primitives (`+`, `-`, `<=`, `==`, etc.)
   and no compiler-internal nodes.
2. **Write per-language extractors** that compile source ASTs down to this IR, handling
   each language's quirks at the extractor level.
3. **Train the model on the IR** — it never sees language-specific artifacts.

For Haskell: extract from `haskell-src-exts` (source AST) or from GHC's parsed AST
before desugaring, then strip typeclass dicts, normalize boxing, etc. in the extractor.
The extractor owns the language-specific complexity; the IR stays clean.

This also enables training on multiple languages simultaneously — Haskell, OCaml, Elm
programs all compiled to the same IR. More structural variety in training data means
better generalization. The GHC Core artifacts we've been fighting actively prevent this
by coupling the representation to one compiler's internals.

### A universal IR works for the functional core, not for all language features

The spectrum of language-specificity matters:

- **Universally representable**: recursion, pattern matching, higher-order functions,
  ADTs, list operations. This covers the functional core of Haskell, OCaml, Elm, F#,
  and the pure subset of most other languages. Our benchmark tasks all live here.
- **Hard to universalize**: Rust's ownership/lifetimes (fundamental to program meaning,
  not just syntax), OCaml's module system (not expressible in lambda calculus without
  losing architectural information), Python's dynamic dispatch (barely typeable).

The implication: a universal IR is achievable and valuable for functional programs
operating on algebraic data types. For systems languages or dynamically-typed languages,
you'd need a different IR or accept targeting a restricted subset. The project scope
should be explicit about which regime it's operating in.

---

## 2026-02-21 — extract/

### Point-free style causes GHC to generate internal helper names

Functions defined point-free (e.g., `fZipWithAdd = fZipWith (+)`) cause GHC Core to emit
a synthetic helper binding for the partially-applied argument:

```haskell
-- Source
fZipWithAdd :: [Int] -> [Int] -> [Int]
fZipWithAdd = fZipWith (+)

-- GHC Core emits
fZipWithAdd = fZipWith @Int @Int @Int fZipWithAdd1
-- where fZipWithAdd1 is GHC's name for the (+) specialization
```

The synthetic name (`fZipWithAdd1`) is not in scope anywhere and cannot be used in surface
Haskell. Fix: rewrite to explicit lambda form so the argument appears inline:

```haskell
fZipWithAdd xs ys = fZipWith (\x y -> x + y) xs ys
```

### Inlined function arguments appear with GHC's internal parameter names

When GHC inlines a function application (e.g., `fHeadOrZero = fHeadOrDefault 0`), the
body of the called function is substituted with GHC's internal names for the arguments.
GHC named the `0` argument `def` (the parameter name from `fHeadOrDefault`):

```haskell
-- Source
fHeadOrZero = fHeadOrDefault 0

-- GHC Core emits
fHeadOrZero ds = case ds of { [] -> Var def; (x:_) -> x }
-- 'def' is a free variable — 0 was inlined as a named binding
```

Fix: rewrite to explicit pattern matching so GHC generates `Lit 0` directly:

```haskell
fHeadOrZero []    = 0
fHeadOrZero (x:_) = x
```

### `I#` and unboxed literals appear in `-O0` Core for integer literals in pattern matching

GHC represents `0 :: Int` as `App (Con I#) (Lit 0#)` in Core even at `-O0`. When a
pattern match returns an integer literal:

```haskell
fHeadOrZero [] = 0  -- GHC Core: App Con I# Lit 0
```

The pretty-printer must unwrap `App Con I# [lit]` → `lit` and strip the `#` suffix from
unboxed literals. This was already handled for pattern scrutinees (from sprint 0) but also
needed for return-position occurrences.

### Validate Core → pretty-print → runhaskell at extract time, not benchmark time

Problems with GHC Core output (internal helper names, free variables, unboxed ops) are
only discovered when the generated Haskell fails to compile in the benchmark. By then the
corpus is already built, the tokenizer trained, and the model trained — all on broken data.

A single validation gate immediately after extraction would catch these issues before they
propagate:

```python
for binding in corpus_raw:
    tokens = normalize(binding)
    try:
        _, haskell = tokens_to_haskell(tokens)
        source = f"module Main where\n{haskell}\nmain = return ()"
        ok, err = run_haskell(source, timeout=5)
        if not ok:
            print(f"SKIP {binding['name']}: {err[:80]}")
            continue
    except Exception as e:
        print(f"SKIP {binding['name']}: parse error {e}")
        continue
    emit(binding)
```

This collapses the extract → corpus → benchmark feedback cycle into one stage. Functions
that produce internal names (`fZipWithAdd1`), free variables (`def`), or uncompilable
output get filtered out at source, not discovered three steps later.

### Char operations trigger unboxed Char# in Core

Functions using `Data.Char` operations (`toLower`, `isDigit`, etc.) compile to operations
on unboxed `Char#` types in GHC Core, emitting tokens like `n'#` (unboxed char literals)
that the sprint 1 parser doesn't handle. Remove such functions from `functions.txt` and
define the operations explicitly if needed.

---

## 2026-02-21 — corpus/

### Augmentation via re-alpha-rename is cheap and effective

Re-running alpha-renaming with a different counter offset (e.g., start at v7 instead of v0)
produces a syntactically distinct sequence that looks like a different training example to
the model. Three seeds × 162 base sequences = 486 extra variants with zero human effort.

The model should not treat `v0` vs `v7` as semantically different, but having multiple
surface forms for the same structure teaches it that variable names are arbitrary.

### Eta-expansion rarely applies because most corpus functions are already fully applied

Only 8 of 162 functions gained an eta-expanded variant. Most corpus functions are defined
as `(Lam binders body)` already, and eta-expansion only applies to non-Lam bodies. This
augmentation strategy has limited yield for GHC Core output.

### Let-reorder augmentation requires independent bindings — rare in practice

Independent let bindings (those whose bodies don't reference sibling bindings) are uncommon
in `-O0` GHC Core. Most `let` blocks have only one binding or have chains of dependencies.
The let-reorder augmentation produced very few variants. A more productive augmentation
would be permuting argument order in lambda binders (when semantically sound).

### Train/val split must be by base function name, not by sequence index

Augmented variants of the same function (re-renamed copies of `fSum`, `fMap`, etc.) must
go to the same split. If you split by sequence index, augmented variants leak into both
train and val, making val loss artificially low (the model has seen structurally identical
sequences during training).

Group by base function name, shuffle names, take 20% of names for val. All variants of
each name go to the same fold.

---

## 2026-02-21 — model/

### Val loss bottoms early and then diverges — best checkpoint is at ~epoch 25

With 3.3M parameters and 626 sequences, the model starts overfitting after only ~25 epochs:

```
epoch  25: train=0.496  val=1.591  ratio=3.21  ← best val loss
epoch  50: train=0.208  val=2.151  ratio=10.4
epoch 300: train=0.137  val=2.769  ratio=20.2
```

The best val-loss checkpoint (epoch 25) has train_loss=0.496 — the model has NOT yet
memorized the training set. This means benchmark tasks that are in the training set also
fail to reproduce reliably at greedy decode. The RL loop later compensates.

### Val/train ratio is misleading when measured at the best-val-loss epoch

The "val < 2x train" criterion was designed to detect the gap between memorization and
generalization. But comparing val_loss at the best val epoch (train=0.50) to training loss
at convergence (train=0.14) gives a very different ratio than the 3.21 we report.

The ratio at the best-val checkpoint (3.21) reflects that the model hasn't finished
learning training patterns yet — not that it's catastrophically overfitting. The absolute
val loss (1.59 nats) is within the expected 1.5–3.0 nats range and is the more meaningful
metric for generalization quality.

### Model capacity vs dataset size: 3.3M params on 626 sequences is too much

A rough rule of thumb: model parameters should not greatly exceed total training tokens.
Our training set has ~503 sequences × ~48 tokens avg ≈ 24K tokens. With 3.3M params,
the model has ~140 parameters per training token — guaranteed to overfit.

For sprint 2, either collect 10× more data, or use a model sized ~200K params (1 layer,
d_model=128) for this corpus scale.

---

## 2026-02-21 — benchmark/

### Corpus helper registry: generated functions call their own name recursively

The pretty-printer always emits functions under the generic name `f`. When stored in the
corpus registry for use as helpers, ALL occurrences of `f` — including recursive
self-calls — must be renamed to the real function name:

```python
# Wrong: only renames first occurrence
haskell = haskell.replace("f ", f"{name} ", 1)
# Fails for: f v0 v1 = ... (fSum (f (tail v0)))  ← inner f stays as 'f'

# Correct: rename all word-boundary occurrences
haskell = re.sub(r'\bf\b', name, haskell)
```

### Negative integer inputs must be parenthesized in test expressions

Haskell parses `f -1` as `f - 1` (subtraction), not `f (-1)` (function application to
negative literal). All negative test inputs must be wrapped:

```python
safe_inp = f"({inp})" if inp.startswith("-") else inp
call = f"f {safe_inp}"  # → "f (-1)"
```

### val-set functions can be dependencies of benchmark tasks

The train/val split is done to measure generalization, but benchmark tasks may require
helpers that ended up in the val set. For example, `fZipWithAdd` (train) depends on
`fZipWith` (val). The model never saw `fZipWith` during training, so it can't reliably
generate calls to it.

When designing tasks, verify that all helper functions a novel task might need are in the
training set (or explicitly define them in the benchmark harness).

### The corpus registry must include transitive dependencies

A generated function may call a corpus helper (e.g., `fSumPositive` → `fFilter` → nothing
else). But `fFilter` may itself call `fNull` or other helpers. The test harness must
include the full transitive closure of dependencies, not just the immediate ones.

---

## 2026-02-21 — rl/

### RL evaluation without corpus registry underestimates pass rate for composition tasks

`evaluate.py:evaluate_task()` calls `build_haskell_source(func_def, task)` without
passing the corpus registry. Tasks whose solutions reference corpus helpers (e.g.,
`fDoubleAll = fMap fDouble`) always fail during RL evaluation because `fMap` and `fDouble`
are not defined in the test harness.

These tasks actually pass 7–13/20 in the full `benchmark/run.py` (which passes the
registry). The RL loop therefore cannot collect winners for them, so it can never improve
them — even though the model is already generating correct solutions.

Fix for sprint 2: pass the registry to `evaluate_task()`.

### RL plateau after round 1 is caused by zero-winner tasks, not by model saturation

When a task produces 0 winners (pass rate = 0 at any temperature), fine-tuning has no
signal to improve it. The model stays stuck even across many rounds. This is different
from the sprint 0 "identical winners" problem — here the model simply cannot generate
any correct program for these tasks.

Monotonic improvement across rounds requires every task to produce at least one winner.
If some tasks are stuck, those tasks never improve regardless of round count.

### RL fine-tuning on a partially-converged model is more effective than on a converged one

(Confirmed from sprint 0, observed again here) The epoch-25 checkpoint (train_loss=0.49)
provided better RL signal than a fully-converged model would:

- Round 1 produced 37 unique winners across 12 tasks
- Fine-tuning for 50 epochs improved 4 previously-failing tasks to pass@50=1.000
- Tasks that already had pass@50=1.000 were not degraded

A partially-converged model has higher entropy in its output distribution, making it more
likely to explore diverse (and occasionally correct) programs.

### RL improvements generalize beyond the specific winners collected

After round 1, tasks like `filter_positive` and `triple` jumped from 0 to pass@50=1.000
even though they contributed 0 winners to the fine-tuning data. Fine-tuning on winners
from other tasks (identity, is_zero, negate, etc.) improved the model's general ability
to generate pattern-matching and arithmetic expressions, which transferred.

This suggests the RL loop works partly through gradient regularization (winners reinforce
correct structural patterns) rather than purely task-specific memorization.

### Sequential generate → runhaskell leaves both GPU and CPU idle most of the time

The current pipeline alternates: GPU generates one program → CPU runs runhaskell → GPU
generates the next. Neither resource is busy while the other works. With 3 rounds ×
22 tasks × 100 total candidates = ~6,600 runhaskell invocations, each taking 0.3–1s,
the CPU bottleneck dominates total runtime.

Fix: use `concurrent.futures.ProcessPoolExecutor` to parallelize runhaskell across
CPU cores (all evaluations are independent), and batch-generate all k candidates before
evaluating (overlap GPU work with CPU work). Expected speedup: 5–10× on the full RL run.

---

## 2026-02-21 — Sprint 2 Bug Fixes

### Train/val split by function name leaks information when functions come from the same package

Sprint 1 split by function name (all augmented variants of `fMap` stay in one fold). This
prevents augmentation leakage but not *codebase* leakage: if `fMap` and `fFoldr` both come
from `containers`, they share structural idioms, naming conventions, and helper patterns.
Putting one in train and the other in val gives the model information about val functions
through correlated training data.

Sprint 2 fix: split by **package**. All functions from `containers` go to the same fold.
The handwritten seed corpus is force-assigned to training since these functions serve as
task dependencies in the benchmark/RL harness.

### Metadata must flow through the entire pipeline, not just the first stage

Package metadata existed in `corpus_all.json` from extraction, but `normalize.py` dropped
it — the output `sequences.json` only had `name` and `tokens`. `augment.py` likewise didn't
propagate it. By the time `train.py` needed to split by package, the information was gone.

Lesson: when adding metadata to any pipeline stage, trace it through *every* downstream
consumer. A single stage that drops a field silently breaks all stages that depend on it.
The fix was one line in each of `normalize.py` and `augment.py`.

### The RL registry bug masked the model's actual composition ability

`evaluate.py` and `rejection_sample.py` called `build_haskell_source(func_def, task)`
without the corpus registry. This meant any generated function that called a corpus
helper (e.g., `fDoubleAll = fMap fDouble`) would always fail during RL — the helper
wasn't included in the test harness.

The benchmark runner (`run.py`) *did* pass the registry, so benchmark results showed
these tasks passing. But the RL loop could never collect winners for them, creating a
permanent blind spot. Six composition tasks were stuck at 0% in RL despite the model
generating correct solutions.

The fix was mechanical (thread `registry` through three function signatures) but the
debugging lesson is important: when a task passes in one evaluation mode but fails in
another, the first thing to check is whether both modes are using the same test harness
configuration.

### Parallel runhaskell requires separating generation from evaluation

The naive approach of parallelizing the entire `generate → parse → runhaskell` loop
doesn't work because GPU generation must be sequential (model state, CUDA streams).
The correct decomposition:

1. **Phase 1 (GPU, sequential):** Generate all k candidates, parse them, build Haskell
   source strings
2. **Phase 2 (CPU, parallel):** Submit all source strings to `ProcessPoolExecutor` for
   concurrent `runhaskell` evaluation

This is a producer-consumer pattern where the GPU produces work items and the CPU pool
consumes them. The two phases don't overlap (generation must complete before evaluation
starts), but the CPU parallelism alone provides the speedup since runhaskell is the
bottleneck.

---

## 2026-02-21 — Sprint 2 Training Experiments

### Package-based val split is fundamentally harder than function-name split

Sprint 1 split by function name: all augmented variants of `fMap` stay together, but
`fMap` and `fFoldr` from the same codebase can end up in different splits. This leaks
structural idioms between train and val.

Sprint 2 splits by package: entire packages go to one fold. Result: val loss went from
1.59 (sprint 1) to 1.79 (sprint 2) despite 9x more training data. The model must now
generalize across genuinely different code styles, not just unseen variable renamings of
familiar structure. The ratio target (<2x) was set assuming the easier split — under
package-based splitting, the ratio is structurally higher.

### The model learns everything it can generalize in ~10 epochs

Across all 8 regularization configs tested, val loss peaked at epoch 5-10 and then
monotonically degraded. This is remarkably consistent. The model quickly learns the
structural patterns of the IR (Lam/App/Case/Let composition) but cannot learn
package-specific idioms from train packages that transfer to val packages.

This means training beyond epoch 10-15 is pure memorization. Early stopping with fine
granularity (`--log-every 5`) is essential to capture the true peak. Coarse logging
(`--log-every 50`) missed the best checkpoint by 40 epochs, saving val=2.26 instead of
val=1.79.

### Label smoothing fixes ratio but hurts absolute val loss

Label smoothing (0.1) achieved val/train ratio 1.66x — meeting the <2x target. But it
did so by inflating train loss (1.43 vs 0.41 without smoothing), not by improving val
loss (2.38 vs 1.79 without smoothing). The model generates worse sequences despite
looking better on the ratio metric.

For downstream tasks (benchmark, RL), absolute val loss determines generation quality.
A model with val=1.79 will produce better candidates than val=2.38 regardless of ratio.
The ratio metric is misleading when label smoothing is used.

### Lower learning rate is the most effective regularizer for this regime

Reducing peak LR from 1e-3 to 3e-4 improved best val from 2.18 to 1.76 (100-epoch test).
This was more effective than:
- Doubling dropout (0.1→0.2: improved to 1.96)
- Tripling dropout (0.1→0.3: improved to 1.90)
- Doubling weight decay (0.05→0.1: marginal)
- Halving model capacity (4→2 layers: improved ratio but worse val)

Lower LR slows the transition from learning to memorization, giving the model more
epochs in the "generalizing" regime before it starts overfitting. The warmup schedule
(already implemented) prevents instability at low LR.

### Python stdout buffering hides training progress in background runs

When `train.py` runs in a non-TTY context (background process, piped output), Python
buffers stdout by default. No output appears until the buffer fills or the process exits.
For a training run that takes minutes, this means zero visibility into progress.

Fix: set `PYTHONUNBUFFERED=1` in the environment, or run Python with `-u` flag. This
should be the default for any long-running pipeline script.

---

## 2026-02-21 — Sprint 2 Tokenizer + Benchmark

### Compound tokens (DEF:name) must be split before building the vocabulary

The tokenizer used `WhitespaceSplit`, which treats `DEF:fId` as a single token. Since
each function name appears rarely, `DEF:fId` falls below `min_frequency=3` and maps to
`<unk>`. This means every benchmark prompt becomes `<bos> <unk>` — identical to the
model regardless of which function is being requested. The model cannot distinguish
between tasks.

Fix: emit `DEF` and the function name as separate tokens in `normalize.py`. `DEF`
appears in every sequence (frequency = corpus size) so it always survives the cutoff.
Function names that are common enough (fSum, fMap, fFilter) get their own token IDs;
rare names become `<unk>` which is acceptable since the model shouldn't condition on
the function name for generation quality.

General rule: any compound token whose components have different frequency profiles
should be split. The structural marker (`DEF`) is universal; the payload (function name)
is rare. Gluing them together forces the universal marker to inherit the payload's rarity.

### Stripping <unk> from generated tokens can break structural invariants

The benchmark post-processing stripped `<unk>` from generated token streams. But when
`<unk>` appears in a structurally required position (e.g., function name after `DEF`),
removing it breaks the parser: `DEF (Lam ...` has no function name.

Fix: position-aware `<unk>` handling. In the function name position (token after `DEF`),
replace `<unk>` with the task's known function name. In body positions, strip or ignore.
Never blindly filter tokens without considering structural roles.

### Hackage-scale data can hurt benchmark performance if task patterns are outnumbered

Sprint 1: 626 handwritten sequences → model learns simple functional patterns → some
benchmark tasks pass. Sprint 2: 5,701 sequences (87% Hackage) → model learns complex
Hackage patterns → 0/22 benchmark tasks pass.

The model generates structurally valid programs that reference Hackage-specific
functions (`toInteger`, `castPtr`, `null`, etc.) not available in the benchmark's test
module. The simple patterns needed for benchmark tasks (identity, sum, filter) are
drowned out by Hackage code.

This is a distribution mismatch: the training distribution (Hackage) doesn't match the
evaluation distribution (simple handwritten functions). More data is not unconditionally
better — it must be data that covers the target distribution.

Possible fixes: upsample handwritten corpus, two-stage training (pretrain on Hackage,
fine-tune on handwritten), or filter Hackage functions to only those matching benchmark
complexity.

---

## Ideas — Dependency Injection / Lambda Lifting for External References

### The three categories of `Var` references have different regularizability

Token references in our IR fall into three categories with fundamentally different
properties:

1. **Locally-bound variables** (`Var v0`, `Var v1`) — fixed vocabulary, normalized by
   alpha-renaming. The model learns structural relationships between binding sites and
   use sites. Fully regularizable.

2. **Recursive self-calls** (`Var fSum` inside `DEF fSum`) — always refers to the
   current function. Structurally meaningful (indicates recursion) but conflated with
   the function's name.

3. **External function calls** (`Var fMap` inside `DEF fDoubleAll`) — open vocabulary.
   Each codebase invents its own function names. The name is an arbitrary label for an
   arbitrary computation. **Unregularizable** — like proper nouns invented per-document.

The model splits its capacity between learning the calculus (structural composition,
from categories 1-2) and memorizing the library (name → pattern associations, from
category 3). Only the first generalizes.

### Function names are words being created on the fly

In natural language, "cat" always means cat — the model can learn a stable embedding.
In code, `fMap` means whatever its implementation says. The definition lives in a
*different* training example. The model can't learn what `fMap` does; it can only learn
statistical co-occurrence ("fMap tends to appear applied to a lambda and a list").

This is memorizing the usage pattern of a specific name, not understanding what mapping
is. It works for names seen often enough, but can't generalize to `fApply`,
`fTransform`, or any other name for the same concept.

Function names are an open vocabulary — each codebase creates new ones. You can't build
a stable embedding for a word that means something different in every context.

### Proposed fix: lambda-lift external references (dependency injection)

Transform external function calls into explicit lambda parameters:

```
-- Before (external name in body):
DEF fDoubleAll (Lam v0 (App Var fMap (Lam v1 (App Prim * Var v1 Lit 2)) Var v0)) EOF

-- After (external reference becomes bound parameter):
DEF fDoubleAll (Lam v0 v1 (App Var v0 (Lam v2 (App Prim * Var v2 Lit 2)) Var v1)) EOF
-- Metadata: {deps: [{param: 0, func: "fMap"}]}
```

For recursive self-calls, introduce a `SELF` token:

```
DEF fSum (Lam v0 (Case Var v0 (Alt (PCon []) Lit 0)
  (Alt (PCon : v1 v2) (App Prim + Var v1 (App Var SELF Var v2))))) EOF
```

This converts every function body into a **closed lambda term** — no free variables,
no external names. The vocabulary becomes purely structural.

### Why this helps the model learn algorithms

Without DI, the fold pattern looks different for each combiner:

```
-- fSum:     ... (App Prim + Var v1 (App Var fSum Var v2)) ...
-- fProduct: ... (App Prim * Var v1 (App Var fProduct Var v2)) ...
-- fConcat:  ... (App Var fAppend Var v1 (App Var fConcat Var v2)) ...
```

The tokens differ, so the model sees these as different patterns.

With DI, they all share the same skeleton:

```
(Lam combiner base list
  (Case list
    (Alt (PCon []) base)
    (Alt (PCon : v1 v2) (App combiner v1 (App SELF combiner base v2)))))
```

The higher layers can abstract this into "list recursion parameterized by a combining
operation" — which is literally what `foldr` is. The model learns the algorithm once
and applies it to any combiner.

### The model should learn the calculus, not the library

Lambda calculus has a small, fixed set of constructs: `Lam`, `App`, `Case`, `Let`,
`Var`, `Lit`. Every computable function is a pattern in these constructs. The model's
job is to learn how they compose.

With external function names in the vocabulary, model capacity is split between:
- Learning the calculus (structural composition) — **generalizable**
- Memorizing the library (name → pattern lookup) — **not generalizable**

DI eliminates the second category. Every function body becomes a closed term in the
calculus. The model can only learn structural patterns.

### DI converts the open-vocabulary problem into the solved alpha-renaming problem

External function names are an open vocabulary with no regularization strategy. DI
converts them into bound variables, which alpha-renaming then normalizes to `v0`, `v1`,
etc. The unregularizable category disappears into the regularizable one.

### What DI doesn't solve

The model still needs to generate the right structure for a task. If asked to produce
"double all elements," it needs to emit a map-like pattern with a doubling lambda.
DI ensures the model learns map-like patterns structurally, but the RL loop still
selects which structural pattern matches which task.

At evaluation time, the benchmark harness must supply actual function implementations
for the lambda-lifted parameters. The metadata mapping (param index → function name)
enables this.

### Implementation path

The transformation sits in `normalize.py` during alpha-renaming, which already tracks
variable scope:

1. Walk the AST, identify `Var X` where X is not locally bound (free variable)
2. Partition free variables into self-calls (X == function name) and external calls
3. Replace self-calls with `Var SELF`
4. Add external calls as outermost lambda parameters; replace `Var X` with `Var vN`
5. Store dependency mapping in metadata: `{deps: [{param: N, func: "X"}]}`
6. Update `pretty_print.py` to reconstruct Haskell from closed terms + metadata
7. Update benchmark harness to supply implementations for lambda-lifted parameters
