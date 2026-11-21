# What the Model Is Actually Learning

_Analysis of Sprint 4 training, benchmark, and RL results — 2026-02-23_

---

## 1. Every function name is erased at tokenization

The 120-token vocabulary contains no function names. `fId`, `fDouble`, `fMap`, and every other function name appears exactly once in the corpus and falls below the `min_freq=25` threshold. All of them tokenize to `<unk>`.

So every training example looks like this to the model:

```
DEF <unk> (Lam 1 Var 0 ) EOF                          ← fId
DEF <unk> (Lam 1 (App Prim + Var 0 Var 0) ) EOF       ← fDouble
DEF <unk> (Lam 1 (App Prim + Var 0 Lit 1) ) EOF       ← fSucc
```

The model sees `DEF <unk>` for everything. Function identity is completely erased. It has learned a prior over body structures, not a map from names to bodies.

---

## 2. Lambda-lifting creates token collisions between unrelated functions

`debruijn_convert` lambda-lifts free variable references (dependencies on other corpus functions) as outermost Lam binders. Any function that takes one dependency and one real argument becomes `(Lam 2 (App Var 1 Var 0))` — the "apply first arg to second arg" pattern:

```
fCountTrue: DEF <unk> (Lam 2 (App Var 1 Var 0) ) EOF
fNegate2:   DEF <unk> (Lam 2 (App Var 1 Var 0) ) EOF   ← IDENTICAL
```

These two functions have completely different runtime behaviour (`[Bool] -> Int` vs `[Int] -> [Int]`) but produce the same token sequence after lambda-lifting and name erasure. From the model's perspective there is one entry in its learned distribution for this 8-token body, not two distinct functions.

This is not a corner case. Any function of the form `\dep arg -> dep arg` — which covers `map f`, `filter f`, `foldr f`, and many other HOF wrappers — collapses to the same token sequence. The de Bruijn + lambda-lifting transformation, designed to canonicalise representation, also collapses semantic distinctions that the benchmark depends on.

---

## 3. What the model has actually learned

The model learned **next-token prediction on de Bruijn IR token sequences**. Given a prefix, it predicts the conditional distribution over the next structural token. Val loss of 0.32 nats (perplexity ≈ 1.38) means it does this well.

Concretely, it has learned:

- After `(Lam 1`, the likely next tokens are `Var`, `(App`, `(Case`
- After `(App Prim +`, two sub-expression arguments follow
- After `(Case Var 0`, alternatives with `(PCon :` and `(PCon []` are common patterns
- The relative frequency of body structures across the 5,078 training sequences

This is a **structural grammar model over de Bruijn IR** — not a function generator.

---

## 4. The benchmark cannot work with this model

The benchmark asks: _"generate the body for function `fId`"_. After encoding, the prompt is `BOS DEF <unk>`. The model cannot distinguish "generate identity" from "generate double" from any other function — the prompt is identical for all of them.

The only signal the benchmark was designed to use is the few-shot context. But:

- Few-shot training used **random sequence groupings**, so the model learned to ignore context. There is no structural relationship between randomly paired training sequences, so the model's loss is minimised by predicting from the global prior regardless of context. This is the documented ICL failure.
- The few-shot context also has all names as `<unk>`, so even a model that had learned ICL could not use name identity to infer which function to generate next.

The model generates from its unconditional structural prior on every benchmark task.

---

## 5. Why `negate_all` and `count_true` occasionally pass

Both have the body `(Lam 2 (App Var 1 Var 0))` — the apply-first-to-second pattern. This pattern is one of the **most common structures in the training corpus**: every HOF wrapper of the form `f dep arg = dep arg` (after lambda-lifting `dep` to an outer binder) produces exactly this 8-token sequence. `fMap`, `fFilter`, `fFoldr`, and dozens of Hackage variants all contribute to its frequency.

The model's unconditional prior assigns relatively high probability to this body. With k=10 samples at temperature 0.8, it hits this pattern occasionally by chance. The RL results reflect this directly: negate_all and count_true are not solved by learning — they are solved by the shape of the prior accidentally aligning with their target.

That is also why the wins are **unstable across rounds**: RL fine-tuning on 1 winner raises the model's probability of generating `(Lam 2 (App Var 1 Var 0))` slightly, but any other fine-tuning perturbation can lower it again. There is no stable attractor; the model is not converging on a function identity, it is drifting around a high-probability structural region.

---

## 6. Why the simplest Tier 1 tasks never pass

`identity = (Lam 1 Var 0)` is 4 structural tokens. It should be trivially easy, yet it scores 0/10 across all rounds. The reason is probabilistic, not structural.

Generating exactly `(Lam 1 Var 0) EOF` requires the model to pick:

- `(Lam` over ≈ 8 plausible next tokens after `DEF <unk>`
- `1` over the number tokens 0–31 that are in vocabulary
- `Var` over alternative inner expressions
- `0` over the de Bruijn index options

The resulting per-sample probability is small. With k=10, the expected number of correct samples is well below 1 for tasks whose bodies are not among the highest-frequency structures. The few-shot context for `identity` is `[fFst, fSnd]` — both multi-argument tuple destructors with long Case bodies — which provides no useful prior update toward the 4-token identity body.

The counterintuitive result is that **simpler functions are harder to recover at low k**. Their bodies are shorter (lower absolute probability under a broad structural prior), they are not especially common in the corpus, and the few-shot context cannot narrow the distribution toward them.

---

## 7. The val/train ratio is misleading

Run 2 (few-shot training) achieved val/train = 0.79x — val loss lower than train loss — and this was interpreted as evidence of generalisation superior to run 1.

The actual cause is a distribution mismatch between training and validation:

- **Training examples** are concatenated-random sequences (2–3 functions joined, up to 256 tokens): a harder next-token prediction task
- **Validation examples** are single sequences: an easier next-token prediction task

Val loss of 0.32 nats measures the model's ability to predict the next token in a single function body given its prefix. The model does this well because it has learned the structural statistics of de Bruijn IR. This metric does not measure — and is not correlated with — the ability to generate a specific target function on demand.

The improvement from 1.028 nats (run 1) to 0.320 nats (run 2) reflects the model learning richer structural patterns from the multi-function training sequences, not improved task-solving capability.

---

## 8. The three changes that interact catastrophically

Sprint 4 made three independent changes that are individually reasonable but combine destructively:

| Change                                         | Intended effect                                    | Actual effect                                                         |
| ---------------------------------------------- | -------------------------------------------------- | --------------------------------------------------------------------- |
| 120-token vocab (`min_freq=25`)                | Clean canonical representation, reduce overfitting | Erases all function names → model cannot identify target functions    |
| Random few-shot training (`few_shot_prob=0.5`) | Teach in-context learning                          | No structural signal in random pairs → model learns to ignore context |
| Few-shot benchmark prompts                     | Require generalisation, not recall                 | Requires working ICL, which was never learned                         |

Sprint 3's 22/22 performance, dismissed as memorisation, was the benchmark working as intended: the model had function names in its vocabulary and learned `fId → (Lam 1 Var 0)` as a retrievable name-to-body mapping. Sprint 4 removed that retrieval mechanism without providing a functional replacement.

The RL results are the clearest evidence of this diagnosis. After 300 epochs of fine-tuning across 3 rounds, with corpus-mix bootstrapping, only 2 of 38 tasks ever pass — and both happen to have the highest-frequency body structure in the training corpus. The model is not learning to solve tasks; it is being gently pushed toward its own prior modes.
