# ML Architect Assessment: FormalLlm Project State and Guidance

**Date:** 2026-02-21
**Scope:** Review of theoretical foundations, sprint 0-3 status and lessons learned
**Context:** Solo research project, 4 sprints completed, laptop + 1 GPU

---

## Executive Summary

This project has a **strong theoretical foundation** and has made **impressive rapid progress** across 4 sprints (0-3) as a solo effort. The core architectural insights are validated. However, the project is now at a **critical inflection point** where the easy wins are exhausted and the next phase requires a fundamentally different approach to data scale, evaluation rigor, and model sizing.

---

## What's Working

### 1. The Representation Journey Is the Real Achievement

The progression from raw GHC Core (Sprint 0) to clean IR (Sprint 2) to closed lambda terms (Sprint 3) is a textbook example of finding the right abstraction level. Lambda lifting alone moved benchmark results from 0/22 to 22/22 — a stronger signal than any hyperparameter sweep could provide. This validates the central thesis of `navigating_formal_space.md`: the structure of the space matters more than the power of the model.

### 2. The Pipeline Closes the Loop

Extract -> normalize -> augment -> tokenize -> train -> type-constrained decode -> benchmark -> RL is a complete feedback loop. Very few research projects wire up end-to-end execution-grounded evaluation this early. Having `runhaskell` as the ground-truth oracle is a massive advantage over pure loss-based evaluation.

### 3. The Theoretical Documents Are Precise and Honest

The distinction between "the model learns the calculus vs. memorizes the library" (Sprint 1 lessons) is an insight many research groups miss. The philosophical foundation correctly identifies the "functional machine" framing as the productive one.

---

## What's Concerning

### 1. The Model Is Not Learning Computation — It's Memorizing It Under a Vocabulary Constraint

Sprint 3's 22/22 at 99.5% pass rate alongside a 14.07x val/train loss ratio is a red flag. Here's what's actually happening:

- The model has **3.6M parameters** trained on **~250K tokens** (6,551 sequences x ~38 avg tokens). That's **~14 parameters per training token**.
- Rule of thumb in language modeling: you need roughly 1 token per parameter for good generalization (Chinchilla scaling). This model is 14x overparameterized for its data.
- The model memorizes the training set by epoch 10. Everything after that is pure memorization.
- The benchmark passes because the **vocabulary acts as an implicit regularizer**: with only ~140 active tokens, a memorized program that's syntactically valid is likely to also be semantically correct for simple tasks.

The val loss tells the true story: 3.30 nats means the model assigns ~27x too little probability to validation sequences compared to training sequences. It has learned nothing transferable about the val packages (containers, data-ordlist, safe).

### 2. The Benchmark Is Saturated and Doesn't Test Generalization

All 22 tasks are solvable by recalling a single memorized training pattern:

- Arithmetic tasks (identity, double, triple): single `Prim` op
- List tasks (sum, length, filter): one recursive Case pattern
- Composition tasks (sum_of_squares, double_all): deps are injected, body is trivial

None of these require the model to **compose patterns it hasn't seen together**. The real test of "learning the calculus" would be: can the model write a function over a data structure it's never seen, or compose three operations in an order not present in training? That hasn't been tested.

### 3. RL Is Doing Nothing Useful

With baseline pass@10 at 1.000 for 21/22 tasks, rejection sampling has no signal to work with. The winner count increasing from 81 to 85 across rounds is just the model finding more surface-form variants of the same programs. RL becomes meaningful only when tasks are hard enough that the baseline model fails.

### 4. Val Loss Is Regressing, Not Improving, Across Sprints

| Sprint | Val Loss | Data Size | Split Method     |
| ------ | -------- | --------- | ---------------- |
| 1      | 1.59     | 626       | by function name |
| 2      | 1.79     | 5,698     | by package       |
| 3      | 3.30     | 6,551     | by package       |

More data and a cleaner representation produced _worse_ generalization. The Sprint 3 lessons learned document correctly identifies why (val loss is measuring something harder: genuinely different code styles), but the trend should still be alarming. The model is not extracting transferable patterns from 9x more data.

---

## Root Cause Diagnosis

The fundamental issue is a **massive mismatch between model capacity and dataset size**. 3.6M parameters on 250K tokens is like training GPT-2 on a single book. The model has no choice but to memorize.

The secondary issue is **evaluation doesn't test generalization**. The benchmark rewards pattern matching, not composition. A lookup table with 22 entries would score 22/22.

These two issues mask each other: the model appears to work because the tasks are easy enough for memorization to succeed.

---

## Recommended Next Steps (Priority Order)

### 1. Fix the Params/Data Ratio (Critical Path)

You have two levers. Use both.

**Shrink the model.** For 250K tokens, a model of ~200K-500K parameters is appropriate:

- 1-2 layers, d_model=64-128, nhead=4, ff=256-512
- ~200K params
- This will train in seconds, iterate fast, and give honest generalization signal
- If this smaller model achieves similar benchmark results, you'll know the current 3.6M-param results are not driven by model capacity

**Scale the data.** Target 50K-100K base functions (100x current):

- More Hackage packages (expand from 25 to 200+, curate for pure-Haskell packages to avoid TH/FFI failures)
- OCaml via opam + compiler-libs (Typedtree extraction) — the `learning_computation_from_primitives` doc already identifies this
- Lean 4 Mathlib (~180K declarations, de Bruijn indexed, types carry proof obligations)
- Each new language validates the universal-IR thesis while multiplying data

**The litmus test:** Can the model achieve val/train ratio < 2x? Until that's true, the model hasn't learned to generalize, and all benchmark results reflect memorization.

### 2. Build a Harder Benchmark (High Priority)

The current 22 tasks should become the "easy" tier. Add:

**Tier 2 — Compositional:** Tasks requiring the model to chain 2-3 operations in an order not present in the training corpus. Example: "given a list of pairs, sum the first elements of pairs where the second element is positive" — requires filter + map + fold in a novel combination.

**Tier 3 — Structural generalization:** Tasks over data structures not in the training set (binary trees, nested lists). If the model has learned the _calculus_, it should be able to write a tree traversal even if it's only seen list traversals.

**Tier 4 — Compositional novelty with dep injection (Mode B):** Can the model generate a function with lambda-lifted parameters where the _test harness_ has to figure out which corpus functions to inject? This tests whether the structural patterns are genuinely reusable.

**Pass@1 as the primary metric.** Pass@10 at temp=0.8 is too lenient. Pass@1 at temp=0.0 (greedy) is the honest measure of whether the model's most likely program is correct.

### 3. Implement De Bruijn Indices (Medium Priority)

Currently, alpha-renaming maps bound variables to `v0`, `v1`, etc. based on a global counter. This still leaves arbitrary differences between structurally identical programs (the counter depends on traversal order). De Bruijn indices eliminate this entirely:

```
Named:     Lam v0 (Lam v1 (App Var v0 Var v1))
De Bruijn: Lam (Lam (App Var 1 Var 0))
```

Benefits:

- Structurally identical terms are _literally_ identical (no re-alpha-rename augmentation needed)
- Vocabulary shrinks further (no v0-v34 tokens, just small integers)
- Removes the 3x re-rename augmentation (which inflates data without adding structural variety)
- Lean already uses this, so cross-language alignment is automatic

This is ~50 lines of change in normalize.py and removes complexity from augment.py.

### 4. Add Tree-Positional Encoding (Medium Priority)

The linearized S-expression format discards tree structure. The model must re-discover that `(App (Lam v0 body) arg)` means `arg` is substituted for `v0` in `body` — a fact that's obvious in the tree but non-local in the sequence.

Add depth-based positional encoding: each token gets a positional embedding that encodes its depth in the AST tree, not just its sequential position. This is a small architectural change (modify the positional encoding in `train.py`) that gives the model free structural information.

### 5. Investigate Abstraction Discovery (Longer Term)

Section 5.5 of `learning_computation_from_primitives.md` describes the most compelling long-term direction: extract recurring subtree patterns from the trained model's representations and add them as new vocabulary entries.

This is the mechanism by which the model could _discover_ `map`, `fold`, `filter` as first-class concepts rather than having them prescribed. It's also the mechanism that would enable genuinely novel composition — the model learns a new "word" for a pattern it discovered, and can then use that word as a building block.

This should wait until the model actually generalizes (val/train < 2x), because extracting patterns from a memorized model just recovers the training data.

### 6. Clean Up GHC Artifact Deps (Low Priority, But Important)

The dep analysis shows `letrec` (104), `cast` (53), `$trModule1` (43), `primFixed` (41) as top "external dependencies." These are GHC internal artifacts, not real functions. They inject noise into the dep metadata and consume lambda-lifting slots. Filtering these out in clean.py would reduce spurious binder count and make the dep metadata semantically meaningful.

---

## Strategic Assessment

The project's theoretical framework is sound and the implementation methodology (sprint-based, hypothesis-driven, honest about failures) is excellent. The three theoretical documents articulate a genuine insight: that navigating formal space should be easier than navigating natural language space because the structure is richer and verification is built-in.

However, the current results **do not yet validate the core thesis**. They validate the weaker claim that "with a sufficiently constrained vocabulary, a memorized model can reproduce simple programs." The stronger claim — that the model _learns computational patterns_ that transfer to novel problems — requires:

1. A model that generalizes (val/train < 2x)
2. Tasks that can't be solved by memorization
3. Evidence of compositional transfer (solving task X+Y having only seen X and Y separately)

The project is roughly at the "AlexNet on MNIST" stage: the pipeline works, the approach is promising, but the real signal will only come at scale. The next sprint should focus ruthlessly on the data/model ratio and evaluation rigor, not on pipeline features.

---

## Immediate Diagnostic (5 Minutes)

**Highest-conviction recommendation:** Train a **tiny model** (1 layer, d_model=64, ~50K params) on the current corpus and compare benchmark results against the 3.6M model. If the tiny model scores similarly (prediction: 18-22/22), that proves the benchmark is too easy and the large model's capacity is wasted. This takes 5 minutes to run and provides the most important diagnostic signal for planning the next sprint.
