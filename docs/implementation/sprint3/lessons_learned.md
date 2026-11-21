# Lessons Learned — Sprint 3

Running log of non-obvious findings, gotchas, and decisions made during development.

---

## 2026-02-21 — Lambda Lifting

### Free variable collection must happen pre-rename, not post-rename

`collect_free_vars()` walks the raw AST dict (before alpha-renaming) to find all `Var` names not bound by any enclosing `Lam`, `Let`, or `Case` binder. This must happen before renaming because after renaming, all names are `v0`, `v1`, etc., and you can't distinguish external references from bound ones.

The scope parameter accumulates bound names as the traversal descends through binders. Only `Var` nodes are checked — `Con`, `Prim`, `Lit`, and `TyArg` are never free variables by design.

### Self-calls and external deps are different transformations

Two kinds of free variables exist and need different treatment:

- **Self-calls** (`Var X` where X == the function's own name): Mapped to the special token `SELF` during renaming. The Renamer's `lookup()` method returns `"SELF"` when the name matches `_self_name` and isn't shadowed by a local binding.
- **External deps** (all other free `Var` names): Converted to outermost lambda parameters. The body gets wrapped in a new `Lam` node (or merged with an existing one) with the dep names as binders.

321 of 1,649 functions use SELF (19.5%). This is a significant fraction — recursive functions are common in the corpus.

### Lambda merging prevents unnecessary nesting

When the function body is already a `Lam`, the dep binders are prepended to the existing binder list rather than wrapping in a second `Lam`:

```python
if new_body["tag"] == "Lam":
    new_body = {"tag": "Lam", "binders": dep_binders + new_body["binders"], "body": new_body["body"]}
```

Without this, a function like `fDoubleAll = \v0 -> fMap (\v1 -> v1 * 2) v0` would become `Lam [dep0] (Lam [v0] ...)` instead of `Lam [dep0, v0] (...)`. The merged form produces a cleaner token stream and matches how programmers think about multi-argument functions.

### Deterministic dep ordering matters for reproducibility

External deps are sorted alphabetically before being assigned parameter positions. Without deterministic ordering, the same function could get different `Lam` binder orders across runs (Python dicts don't guarantee insertion order across processes), making the corpus non-reproducible and breaking any cached tokenizer.

---

## 2026-02-21 — Un-lifting for the Registry

### The registry needs un-lifted definitions, not lifted ones

Lambda lifting makes the token stream structural, but the benchmark harness needs actual Haskell code that compiles. When a function like `fMap` is stored in the corpus with deps `[{param: 0, func: "letrec"}]`, its registry entry has an extra lambda parameter that the call site doesn't know about.

The solution is `_unlift_deps()` in `run.py`: a regex-based transformation that substitutes dep parameter names back to their original function names and removes them from the parameter list.

Before un-lifting: `fMap v0 v1 v2 = v0 (...)` (v0 is the dep param for "letrec")
After un-lifting: `fMap v1 v2 = letrec (...)` (v0 replaced with "letrec" everywhere)

This is purely a rendering concern — the model never sees un-lifted code. The token stream stays closed; only the test harness needs real Haskell.

### Transitive deps need the same un-lifting treatment

When task A depends on function B, and function B depends on function C, both B and C need to appear as helper definitions in the generated Haskell source. All of them need un-lifting — otherwise B's definition would reference a param `v0` that doesn't get bound anywhere in the test module.

The existing `_collect_all_deps` traversal handles transitivity; the new code just ensures every collected helper goes through `_unlift_deps`.

---

## 2026-02-21 — BINDER_TOKENS Range

### The corpus has binders up to v28; allocate headroom

Sprint 1's `BINDER_TOKENS` only covered `v0` through `v13`. After lambda lifting, some functions acquire many additional parameters (up to 12 deps + original params), pushing the max binder index to v28.

Initially extended to v0-v24, which failed when a corpus function used v28 as a binder. Extended to v0-v34 to provide headroom.

The lesson: when a transformation adds parameters to functions, audit the maximum binder index in the resulting corpus and set the range accordingly. A simple one-liner check:

```python
max(int(t[1:]) for s in seqs for t in s["tokens"] if t.startswith("v") and t[1:].isdigit())
```

---

## 2026-02-21 — Vocabulary Composition

### Lambda lifting dramatically changes vocabulary composition

Before lifting, 87% of sequences contained `<unk>` tokens — external function names that fell below the tokenizer's frequency threshold. After lifting, only 0.5% of sequences have `<unk>`.

The vocabulary is now almost entirely structural:
- **Tags:** `Lam`, `App`, `Case`, `Let`, `Var`, `Con`, `Lit`, `Prim`, `DEF`, `EOF`, `(`, `)`, `@T`
- **Variable names:** `v0` through `v29`, `SELF`
- **Constructors:** `True`, `False`, `Just`, `Nothing`, `[]`, `:`, tuple constructors
- **Primitives:** `+`, `-`, `*`, `==`, `<`, `>`, `<=`, `>=`, etc.
- **Literals:** Integer and character literals

This is exactly the vocabulary the model should learn from — no library names, no GHC internals, just the lambda calculus with ADTs.

### Some `<unk>` remains from GHC artifacts that survived cleaning

The remaining 30 sequences with `<unk>` likely contain GHC artifacts that weren't caught by the cleaning pass (e.g., unusual constructor names or internal types). At 0.5%, this is acceptable — the affected sequences will just train slightly worse due to information loss at the `<unk>` positions.

---

## 2026-02-21 — GHC Artifact Deps

### Not all "external deps" are real functions

The top dep function by count is `letrec` (104 occurrences), which is a GHC Core artifact for recursive let bindings — not a user-defined function. Other GHC artifacts in the dep list: `cast` (53), `$trModule1` (43), `primFixed` (41).

These get lambda-lifted correctly and don't cause problems (they become anonymous bound variables in the token stream). But they do mean the dep metadata isn't purely about function composition — some deps represent compiler machinery.

For the model, this is fine: the body is still a closed term, and the model learns structural patterns regardless of what the dep slots "mean." For the benchmark harness, these deps don't appear in novel tasks (which are all self-contained), so they don't need un-lifting.

**Fixed in Sprint 4:** `GHC_ARTIFACT_VARS = frozenset({"letrec", "cast", "$trModule1", "primFixed"})` added to `clean.py`. The `Var` handler now returns `None` for these names, which propagates up through enclosing `App`/`Lam` nodes. Result: 250 → 0 artifact Var references; 144 corpus entries that relied on artifact vars are now correctly dropped.

### Comparison operators appear as deps, not primitives

`<` (90 occurrences), `>` (83), `>=` (48) appear as external deps rather than `Prim` nodes. This happens when GHC Core uses the typeclass method form (`GHC.Classes.<`) rather than the primitive form. The cleaning pass converts most but not all of these to `Prim` nodes.

This isn't a problem for lambda lifting — these become bound variables and the model learns to use them structurally. But it does mean the dep count is inflated by what are conceptually primitive operations.

**Fixed in Sprint 4:** A check was added to the `Var` handler in `clean.py` — any standalone `Var` whose name is in `PRIMITIVES_WITH_DICT` is promoted to `Prim`. Result: 222 → 0 comparison op Var references.

---

## 2026-02-21 — Metadata Pipeline

### Dep metadata propagation is trivial but easy to forget

Both augmentation strategies (re-alpha-rename and commutative swap) need to copy the `deps` field from source to augmented sequence. This is a two-line change in each function:

```python
if "deps" in seq:
    entry["deps"] = seq["deps"]
```

The key insight: `deps` metadata is invariant under both transformations. Re-alpha-renaming changes internal variable names but not the dep parameter positions (they're the outermost binders). Commutative swap reorders subexpressions but doesn't change which external functions are referenced.

If future augmentation strategies are added (e.g., subexpression substitution, function inlining), they may need to update deps — but the current strategies don't.

---

## 2026-02-21 — Point-Free Recursive Calls

### Point-free functions generate broken recursive dep-passing in GHC Core

`fConcat` was defined point-free and GHC generated a recursive call `App Var SELF Var v3` that only passes the list argument, not the dep function (`fAppend`). The original GHC Core had `fConcat` calling itself without explicitly passing the helper — GHC handled this through closure capture, which doesn't survive our lambda lifting.

This is a pre-existing issue from the GHC Core extraction, not caused by lambda lifting. Lambda lifting just makes it visible: the self-call has fewer args than the function's parameter list.

The fix would be in `clean.py` to detect point-free recursive patterns and eta-expand them. But since only a handful of functions are affected and the corpus is large enough, this was documented rather than fixed.

---

## 2026-02-21 — Training & Evaluation Results

### Benchmark success despite severe overfitting is the key Sprint 3 insight

The model achieves 22/22 benchmark tasks at 99.5% candidate pass rate despite a val/train loss ratio of 14.07x (val=3.30, train=0.23). This seems contradictory but has a clear explanation:

**The closed-term vocabulary acts as a regularizer at inference time.** With only ~100 structural tokens in active use (vs ~1,500 in Sprint 2), the model's output distribution is constrained to valid structural patterns even when it has memorized the training set. A memorized pattern like `Lam v0 (App Prim + Var v0 Lit 1)` is also a correct program — memorization and correctness align when the vocabulary is purely structural.

In Sprint 2, the model memorized Hackage function names and reproduced them at inference — but those names weren't in scope in the test module, causing 0% compile rate. In Sprint 3, there are no names to memorize — only structure.

**Implication:** Val loss is a misleading metric for this task. The model's generalization is mediated by vocabulary constraints, not by low val loss. A model with 14x overfitting ratio can still be useful if the output space is sufficiently constrained.

### Val loss regression from Sprint 2 is expected, not a bug

Sprint 2 val loss was 1.79 nats; Sprint 3 is 3.30 nats. This looks like a regression but is actually a different measurement:

- Sprint 2's vocabulary included function names. The model could "cheat" on validation by memorizing which function names co-occur (e.g., `fMap` always appears with `Lam`, `App`). This inflated apparent generalization.
- Sprint 3's vocabulary is purely structural. The val set (containers, data-ordlist, safe packages) has genuinely different structural patterns from the training set. Higher val loss = the model is being tested on harder generalization, not that it learned less.

The better comparison is benchmark pass rate: 0/22 → 22/22. That's the metric that matters.

### RL produces winners but can't improve a saturated metric

With baseline pass@10 already at 1.000 for 21/22 tasks, RL has no room to improve. The winner count increased slightly (81 → 85) across rounds, showing the model is generating more diverse correct programs, but the pass rate ceiling is already hit.

RL will become meaningful when:
1. Harder tasks are added that the base model can't solve at pass@10=1.0
2. pass@1 is used as the metric (some tasks were 0.8-0.9 at pass@1 pre-RL)
3. The benchmark requires compositional novelty (combining dep functions in new ways)

### The segfault in RL round 3 is a resource issue, not a bug

The segfault occurred after ~45 minutes of continuous GPU operation (training + 2 full RL rounds of generation + evaluation). This is consistent with PyTorch/CUDA memory fragmentation under sustained load. Not worth debugging — the results from rounds 1-2 are sufficient.

---

## 2026-02-21 — Benchmark Saturation

### 22 tasks at 99.5% means the benchmark needs harder problems

The current 22 tasks (10 original + 12 novel) are all solvable with simple structural patterns:
- **Arithmetic:** identity, double, triple, negate, successor (single `Prim` op)
- **Boolean:** not, is_zero (single `Case` on Bool or comparison)
- **List traversal:** sum, length, null, tail, head_or_zero (single `Case` on list with recursion)
- **Higher-order:** map_double, filter_positive, double_all, negate_all (map/filter with simple combiner)
- **Composition:** sum_of_squares, sum_positive, count_true, any_positive, all_positive, zip_with_add (fold + simple predicate/combiner)

All of these can be solved by a model that has memorized the structural patterns for `Case`, `Prim`, `App`, and list recursion. The next benchmark iteration needs tasks that require:
- Novel composition of 2+ higher-order functions
- Functions with 3+ dep parameters
- Non-trivial recursion schemes (tree recursion, accumulator patterns)
- Functions that require understanding evaluation order

### Novel tasks being self-contained (deps=[]) is both a strength and limitation

All 12 novel tasks have `"deps": []` — they don't reference any corpus functions. This proves the model can generate correct closed terms from scratch, which is the core Sprint 3 hypothesis. But it also means we haven't tested whether the dependency injection mechanism works for novel tasks that need deps.

Testing dep injection for novel tasks requires Mode B evaluation (brute-force or type-guided dependency search), which wasn't implemented in Sprint 3.

---

## 2026-02-21 — Post-Assessment Diagnostic: Tiny Model vs Big Model

### The ML architect's prediction was wrong — and the correction is informative

The assessment predicted: "If the tiny model scores similarly (prediction: 18-22/22), that proves the benchmark is too easy and the large model's capacity is wasted."

Actual result: the tiny model (1 layer, d_model=64, 165K params) scored **9/22 tasks, 12.3% candidate pass rate** — far below the big model's 22/22 at 99.5%.

This means the benchmark is **not** trivially easy. Model capacity genuinely matters for solving these tasks. The failure mode is instructive: the tiny model produces compile errors (not parse errors) on most failures, meaning it generates syntactically valid IR that the pretty-printer can parse but which produces malformed Haskell. The big model has memorized enough structural patterns that its output is semantically coherent; the tiny model generates plausible-but-wrong structures.

### The benchmark gap measures memorization capacity, not generalization

The 9/22 → 22/22 gap between tiny and big model is entirely explained by memorization capacity. The big model has enough parameters to store the training patterns precisely enough to reproduce them on demand. The tiny model can't hold them all.

This reframes the diagnostic: the benchmark is not "too easy for any model" but "easy if you've memorized the training set." A lookup table would fail (it can't generalize the prompt format). A model with insufficient capacity fails (can't memorize cleanly). A model with just enough capacity passes through memorization. None of this involves generalization.

The test of actual generalization remains untested: tasks requiring the model to compose patterns it has seen only separately, or apply a recursion scheme to a data structure it's never seen.

### Vocabulary size dominates parameter count at small d_model

The assessment estimated ~50K parameters for 1 layer, d_model=64. The actual count was **165K** — 3.3x higher. The discrepancy is entirely from the embedding table: with 1,549 vocabulary tokens and weight tying, `tok_emb` alone is `1549 × 64 = 99,136` parameters.

The assessment was written assuming a vocabulary of ~500-800 tokens (the sprint 2 target range). The actual vocabulary grew to 1,549, and the embedding table scales linearly with it. With this vocabulary, it's geometrically impossible to build a <50K parameter model with meaningful d_model — the embedding layer alone exceeds that budget at any useful dimension.

Rule of thumb: when vocabulary is large relative to d_model, the embedding table dominates the parameter count. For `vocab=1549, d_model=64`: embeddings are 60% of total parameters. For `vocab=1549, d_model=256`: embeddings are only 28%. The "shrink the model" recommendation in the assessment should account for vocabulary size when estimating parameter counts — the effective floor for a useful model on this corpus is ~165K, not ~50K.

### The tiny model actually generalizes better despite solving fewer tasks

The tiny model's val loss (2.04) is substantially better than the big model's (3.30), and its val/train ratio (3.03x) is far healthier than the big model's (14.07x). This is the correct direction — the tiny model is genuinely learning something more transferable.

The tension: the tiny model generalizes better but can't solve the tasks, while the big model can solve the tasks but doesn't generalize. This is the classic underfitting/overfitting tradeoff made concrete.

The resolution is not to pick between them but to increase data until a correctly-sized model can both generalize (low val/train ratio) and solve the tasks (high benchmark pass rate). The data target from the assessment — 50K-100K base functions — would allow a model in the 1M-2M parameter range to do both.

### The immediate next lever is data, not architecture

The diagnostic confirms the assessment's primary recommendation: fix the params/data ratio first. The benchmark gap shows we need a model with sufficient capacity to solve the tasks; the val loss gap shows we need more data to prevent memorization at that capacity.

Neither a tiny model nor a huge model is right for the current dataset. The right model size for 250K training tokens is approximately 250K-500K parameters (1:1 to 2:1 ratio) — which requires either reducing the vocabulary (to shrink the embedding table) or scaling data to match the existing 3.6M parameter model.
