# Lessons Learned — Sprint 28

Running log of non-obvious findings, gotchas, and decisions made during the diversity training experiment.

---

## 2026-04-11 — Data extraction (Phase 1)

### Pantograph `env_inspect(print_value=True)` returns kernel-level `brecOn`, not usable bodies

The plan assumed Pantograph could extract the elaborated body of a Lean definition for training data. It does — but the output is the fully-elaborated kernel term, not the surface-syntax body.

For `List.isPrefixOf`:
```
fun {α} [BEq α] x x_1 =>
  List.brecOn (motive := fun x => List α → Bool) x
    (fun x f x_2 =>
      (match (motive := (x : List α) → List α → List.below (motive := fun x => List α → Bool) x → Bool) x, x_2 with
        | [], x => fun x => true
        | x, [] => fun x => false
        | a :: as, b :: bs => fun x => a == b && x.1 bs)
        f)
    x_1
```

354 characters of `brecOn` with motive annotations — technically valid but unreadable and useless for training. A human-written `match` expression is ~100 chars.

**Decision**: Don't use `env_inspect` for body extraction. Use source-level parsing (`lean_def_converter.py`) with the surface syntax instead, and use `env_inspect` only for type signatures and `env_catalog` for enumeration.

### Lean equation compiler has no extractable body — must reconstruct `match` from clauses

A definition like:
```lean
def isPrefixOf [BEq α] : List α → List α → Bool
  | [],    _     => true
  | _,     []    => false
  | a::as, b::bs => a == b && isPrefixOf as bs
```

has no body after `:=` — the equation clauses ARE the definition. Lean's elaborator desugars them to a `match` internally, but the source text has nothing to extract with a simple split on `:=`.

The converter has to recognize this case (no `:=`, has `|` clauses) and reconstruct `match arg1, arg2 with | pat1 => expr1 | pat2 => expr2`. Plus: if the function recurses, replace the recursive self-reference with the qualified library name (`isPrefixOf as bs` → `List.isPrefixOf as bs`).

### `where` clauses need `let rec`, not just textual splicing

Lean's `where` clause defines a helper that's scoped to the enclosing definition. For `partition`:
```lean
def partition (p : α → Bool) (as : List α) : List α × List α :=
  loop as ([], [])
where
  loop : ... | ... | ...
```

Moving `loop` above the main expression as a regular `let` doesn't work — you need `let rec` so the helper can reference itself. The converter must output:
```lean
exact
  let rec loop : ... | ... | ...
  loop as ([], [])
```

We verified this round-trips through Pantograph — `let rec` is accepted by the elaborator inside `exact`.

### Doc comments inside `where` blocks parse as helpers

First attempt at the converter parsed any indented identifier in a `where` block as a helper definition. This picked up fragments of doc comments as "helpers":
```
transpose: where_defs: 11 helpers ["by", "If", "a", "If", "the", "of", "List", ...]
```

Those "helpers" were words from multi-line `/-- ... -/` doc comments inside the `where` block. Fix: strip doc comments first with a two-pass algorithm that tracks `/--` and `-/` boundaries.

---

## 2026-04-11 — Format mismatch (Phase 3)

### **Critical**: Training state format must match benchmark goal state format

Initial extraction produced training pairs like:
```json
{"state": "⊢ List Int → Int", "tactic": "exact fun xs => xs.foldl ..."}
```

This represents the goal BEFORE any `intro`. The benchmark's diagnostic tasks are defined as:
```lean
def secondSmallest (xs : List Int) : Int := by sorry
```

When Pantograph enters the `by` block, the variable `xs` is already in scope. The goal state Pantograph presents is:
```
xs : List Int
⊢ Int
```

That's a completely different state distribution from what the training data used.

The model trained on `⊢ List Int → Int` states consistently produces `exact fun xs => ...` at inference — but never sees `xs : List Int\n⊢ Int` during training. At inference on the benchmark, the model falls back to base-model priors (Qwen2.5-Coder's JavaScript/Python/Haskell-ish code generation) because the input distribution is out-of-distribution.

**The fix**: regenerate training data with `intro`-style verification. The extraction script now does:
```python
state = server.goal_start(expr=function_type)
state = server.goal_tactic(state, tactic=f"intro {param_names}")
goal_str = str(state)  # post-intro — matches benchmark format
```

And tactics are stored without the `fun` wrapper — they reference the intro'd variables directly.

**Rule**: Always verify that training data goal states match the distribution at inference. Sample a few benchmark goals and compare with training data. If the formats differ, the model can't transfer.

---

## 2026-04-12 — Infrastructure (Phase 3)

### Pantograph daemon corrupts after ~3 back-to-back benchmark runs

Symptom: `env_inspect` returns empty results (`missing 'type' in []`), `goal_start` fails with `'stateId'` KeyError. Workers appear healthy (CPU active) but return garbage responses.

This matches the note in memory from S26: "Daemon corruption after long benchmark runs: workers get stale `KeyError: 'type'`". The workaround is mandatory: **restart the daemon between benchmark runs**.

Wasted hours: ~4 debugging "why is Config B getting 0/18" before realizing it was daemon corruption masquerading as a model regression.

**Rule**: If benchmarks start failing inexplicably after the first one worked, restart the daemon FIRST before assuming the model is broken.

### Hybrid-gc is 20-30x slower than expanded-fixed per rollout on unsolvable tasks

From memory: "hybrid-gc extremely slow for benchmarks: 851s for one failing task." Measured in Sprint 28: one task × 500 rollouts ≈ 60 minutes. For 18 tasks that's 18 hours.

The bottleneck is the oracle (65ms/call × ~1700 calls × 500 rollouts). Expanded-fixed doesn't do LoRA inference and iterates through a fixed list of ~650 candidates, so most rollouts exit quickly.

**Workaround**: Run hybrid-gc at `--rollouts 50` instead of 500. Novel LoRA contributions appear at rollout 1 when they're in the top-k candidates — the full 500 budget is only needed for deep MCTS search, which hybrid-gc rarely benefits from anyway.

### Lean `--` is a valid line comment — trailing comments in LoRA outputs are NOT broken syntax

Early direct-sampling diagnosis showed the model producing tactics like:
```
exact (xs.filter (fun x => decide (x > 0))).map (fun x => x.natAbs) -- typechecks fine
```

I initially thought this was broken output because the trailing `--` looked like contamination from JavaScript/Haskell comments. But Lean 4 uses `--` as its line comment syntax too. Lean's lexer strips `-- comment` before elaboration, so the above tactic IS valid and solves tasks through the oracle.

Config B's benchmark solves include several tactics with trailing `--` comments. They all type-check.

**Rule**: Don't assume "output has comment → broken". Test the tactic through Pantograph directly before concluding it's garbage.

---

## 2026-04-12 — Policy wrapper (Phase 3)

### `GoalConditionedPolicy._is_degenerate` silently rejects structured outputs

Found in `lean_mcts/search/policy.py`. The filter rejects any tactic where:
- `len(tactic) > 200`
- `max_paren_depth > 3` (counting `(`, `[` only)
- `'sorry' in tactic`

Our diverse training taught the model to generate `let rec helper : ... | clause1 | clause2 ... ; helper args` which typically exceeds both limits — length often 250+ chars, depth 4-6 from nested patterns. Every such output gets filtered before reaching the oracle.

**The 17K template data was tuned for these filters**: template tactics are single-line method chains, typically < 100 chars, depth ≤ 3. The filters were designed for that distribution.

**For Sprint 29**: If we want the model to contribute structural patterns, the filter needs to be relaxed or replaced with something that understands `let rec` and multi-arm `match`. The 200-char / depth-3 limits were chosen for PCFG-style template outputs and are too restrictive for library-style code.

---

## 2026-04-12 — Training dynamics

### 17K template pairs drown out 200 diverse pairs even with 10x loss weighting

Config B's loss-weighted mix: 17,592 template + 164 diverse pairs with 10x weight on diverse examples. Effective gradient contribution ratio: 17,592 × 1 : 164 × 10 = 17,592 : 1,640 ≈ 11 : 1 template dominance.

Config B's generation distribution matches this ratio closely:
- 59.4% method_chain (template shape)
- 28.1% fold (template shape)
- 0% `let rec` (diverse-only shape)
- 0% multi-arm match (diverse-only shape)
- 81% trailing comments (Qwen base prior, not overridden)

**Removing the templates entirely (Config A) shifts the distribution dramatically**:
- 25% method_chain
- 20% fold
- 13.6% match expressions
- **2.3% trailing comments** (base prior finally overridden)
- Still 0% `let rec`

The gradient ratio is the key lever. 10x loss weighting doesn't actually balance 17,500:200 data. To actually balance it you'd need ~100x weighting, which causes training instability.

**Rule**: If you want to teach the model a pattern that's minority in training, you need to either make it the majority of data or not have it competing against a dominant pattern at all. Loss weighting beyond ~5x is unreliable.

### LoRA r=16 cannot teach rare token sequences even with clean training data

Config A has 63 `let_rec` examples in 164 pairs (38% of training). Even with this high proportion and no template competition, the model **never** generates `let rec` at inference (T=0.8, top_p=0.95).

Why: `let rec` is an extremely rare token sequence in the base model (Qwen2.5-Coder) pre-training. Its probability mass under the base distribution is essentially zero. LoRA r=16 adds 16 rank-1 updates to each target layer — that's a small capacity budget. With only 63 examples × 3 epochs = 189 gradient updates involving `let rec`, the LoRA update can't shift the output distribution enough to push `let rec` into the top-k sampling range.

**For Sprint 29**: Either (a) make `let rec` more common in training data (10x more examples), (b) increase LoRA rank to r=64+, or (c) switch to full fine-tuning. Teaching rare patterns requires proportional capacity.

### Base model comment bias is suppressible but only without competition

Qwen2.5-Coder was pre-trained on JavaScript/Python/Haskell which heavily use `//` and `#` comments. Its base prior for "code with explanations" is strong.

Observation:
- Config B (17K template + diverse): 81% of outputs have trailing comments
- Config A (diverse-only, no templates): 2.3% of outputs have trailing comments

The 17K template data doesn't TEACH comments (templates are comment-free), but its high volume leaves enough LoRA capacity unused that the base prior leaks through. Config A redirects all the LoRA capacity toward overriding the base prior, including the comment habit.

**Rule**: A fine-tune that fully consumes LoRA capacity overrides base priors more thoroughly than a fine-tune that only competes for gradient signal.

---

## 2026-04-12 — Benchmark interpretation

### False positives from weak test cases

`second_smallest` task has 4 test inputs:
- `[3, 1, 4, 1, 5]` → `1`
- `[]` → `0`
- `[5]` → `5`
- `[2, 2]` → `2`

Config B's LoRA generated `exact xs.foldl gcd 0` — which computes the GCD of the list, not the second smallest. But:
- gcd of `[3,1,4,1,5]` = 1 ✓
- gcd of `[]` = 0 (initial) ✓
- gcd of `[5]` = 5 ✓
- gcd of `[2,2]` = 2 ✓

All 4 tests pass because GCD happens to equal second-smallest for these specific inputs. An adversarial case like `[4, 6, 2, 3]` would give GCD=1 but second-smallest=3.

The S27 analysis warned about this exact risk. Weak test cases let semantically-wrong tactics pass.

**Rule for diagnostic benchmarks**: every task should have at least one test case specifically chosen to distinguish the correct function from plausible alternatives. For "second smallest", an input with distinct values where min ≠ second-min ≠ GCD is mandatory.

### Benchmark "SOLVED" doesn't mean "correct", especially for novel LoRA tactics

When hybrid-gc solves a task via a LoRA-generated tactic that doesn't exist in the EF catalog, always verify the solution semantically — don't trust the oracle alone. The oracle only checks test cases, not intent. For Sprint 28:
- `dedup_count: exact xs.eraseDups.map (fun x => (x, xs.count x))` — correct ✓
- `zip_sum: exact List.zipWith (· + ·) xs ys` — correct ✓
- `filter_map_abs: have h := xs.filter ...; exact h.map ...` — correct ✓
- `second_smallest: exact xs.foldl gcd 0` — **wrong** (false positive)

3/4 novel LoRA solves are real, 1/4 is a false positive. That 25% FP rate is concerning.

---

## 2026-04-12 — Hypothesis validation

### Pure diversity training (Config A) is the cleanest test

Initial Config B results suggested the diversity hypothesis "worked on hard tier" (+3 novel) but was ambiguous because templates might have been doing most of the work. The cleanest test was Config A: 164 diverse pairs, zero template data.

Config A's `filter_map_abs` solve (multi-step `have`+`exact`) is the strongest evidence for the hypothesis:
- Zero template data → templates can't have contributed
- Multi-step `have`+`exact` doesn't exist in the EF catalog
- Matches a structural pattern from the training data (where-clause definitions converted to let rec / multi-step proofs)

**Decision**: The Sprint 28 hypothesis is validated, but training scale (164 pairs) is too small for meaningful benchmark gains. Scale up in Sprint 29.

### Direct sampling shows structural patterns even when benchmark score is flat

Config A's benchmark score (3/18 diag, 2/5 hard) matches baseline by raw count. But direct sampling shows:
- 13.6% match expressions (Config B: 0%)
- Clean outputs without comments
- Tuple construction, multi-step tactics, `partition (fun x => x > 0)` as a clean pattern

**Rule**: Don't judge model training by benchmark score alone. Inspect the generations directly to see what the model learned. Low benchmark score with good generations means vocabulary gap; high benchmark score with bad generations means the benchmark is too easy.

---

## 2026-04-12 — Config C DataLoader leak (root cause)

### Why Config C hung in epoch 3

Config C uses a curriculum: epoch 1 on diverse-only (164 examples), epochs 2-3 on loss-weighted mix (17,759 examples). The implementation creates a **new `DataLoader` at the start of each epoch** (line 257 of `train_diverse.py`) to switch datasets. Configs A and B create the loader once before the epoch loop.

The process hung at ~2 hours with:
- Sleeping state (S), 0% CPU, 0% GPU
- Adapter last saved at epoch 2 (so epoch 3 training or validation was the culprit)
- Warning at forced shutdown: `resource_tracker: There appear to be 1 leaked semaphore objects to clean up at shutdown: {'/mp-sfvwguz4'}`

**Root cause**: sequential DataLoader creation without explicit cleanup accumulates shared-memory / semaphore resources. After 3 loaders × 3 epochs on a 17K dataset, the resource leak deadlocks.

Config B (which also processes the 17K dataset for 3 epochs) didn't hit this because it creates one DataLoader and iterates it 3 times. The leak is specific to the per-epoch-new-DataLoader pattern.

### Fix (if curriculum is ever revisited)

Create both loaders upfront, pick via `if` inside the loop:
```python
diverse_loader = DataLoader(diverse_ds, ..., shuffle=True)
mixed_loader = DataLoader(mixed_ds, ..., shuffle=True)
for epoch in range(args.epochs):
    loader = mixed_loader if (curriculum and epoch >= 1) else diverse_loader
    for batch in loader:
        ...
```

**Rule**: DataLoaders hold shared-memory state. Don't create them in a hot loop. Create all needed loaders once and select among them.

### Whether to fix this

Originally I said "not worth it" — but after the DataLoader fix, Config C retrained cleanly in 17.9 minutes and **scored best on real novel solves (3)**. Updated conclusion:

**Config C is the winning configuration.** The curriculum (structure-first-then-vocabulary) combines Config A's structural learning with Config B's vocabulary coverage, producing all 3 library-extracted novel solves simultaneously:
- `zip_sum` (Batteries vocabulary: `List.zipWith`)
- `dedup_count` (composition: `eraseDups.map (fun x => (x, xs.count x))`)
- `filter_map_abs` (structural: two-step `have`+`exact`)

Config A alone gets only `filter_map_abs`. Config B alone gets only `zip_sum` + `dedup_count` (plus a GCD false positive). Config C gets all 3 with no false positives.

**Root cause of why curriculum works**: training order matters for what the model retains.
- Config A (diverse-only): learns structure but never sees templates → weak vocabulary
- Config B (mixed throughout): vocabulary competes for capacity with structure from step 1 → dominant templates suppress structure
- Config C (structure first, then mixed): structure is established in epoch 1, then vocabulary is layered on top without erasing it

This is a meaningful ML finding beyond the Sprint 28 plan's expectations. The curriculum hypothesis from the plan was "does learning order matter?" — answer is **yes, strongly**. Carry this finding to Sprint 29: use Config C-style curriculum as the default.

**Rule for future training**: When one skill is rare in pretraining and another is common, teach the rare skill first in isolation, then introduce the common skill. Don't mix them from epoch 1 — the common skill's gradient signal drowns out the rare one.

---

## 2026-04-12 — Held-out analysis and T2 regression (late)

### All three configs: 0/6 on held-out diagnostic tasks

Configs A, B, and baseline all scored **0/6 on the 6 held-out diagnostic tasks** (`map_with_index`, `prefix_while_increasing`, `max_subarray_sum`, `majority_element`, `unfold_n`, `eval_stack`). Every real solve from Configs A/B was on the 12 train-split tasks (for which augmented variants were in training) or on the Hard tier (which wasn't augmented).

This is the plan's primary generalization metric and it was a clean zero.

**The augmented diagnostic training approach does not transfer.** Variants like "flip predicate", "change initial value", "swap comparison operator" on 12 diagnostic reference implementations don't teach compositional skills that generalize to genuinely novel diagnostic structures. The augmentations are too shallow — they're surface-level perturbations of the same underlying algorithm, not new algorithms.

**Real novel solves came from library extraction, not diagnostic augmentation**:
- Config A's `filter_map_abs` (have+exact): from extracting real `where`-clause patterns in stdlib
- Config B's `zip_sum`, `dedup_count`: from extracting Batteries definitions

None came from the 34 augmented diagnostic pairs. Those pairs functioned primarily as noise for the more useful library pairs — the model learned structural patterns from libraries, while the augmented diagnostics contributed nothing.

**Decision for Sprint 29**: Drop diagnostic augmentation (Source 4). Scale library extraction (Source 1-3) instead. The plan's diagnostic hold-out was the right methodology, but the augmentation was the wrong source — you can't create generalization data by perturbing the target tasks themselves.

### Config A doesn't regress on T2 core despite high template val loss

Config A has diverse val 1.11 / template val 1.73 — much higher template loss than Config B (0.33). I expected this to cause regression on core template tasks. It didn't: Config A scores 10/10 on T2 core, same as Config B and baseline.

Why: hybrid-gc uses `GoalConditionedPolicy` (LoRA) AS A SOURCE OF CANDIDATES, with `ExpandedFixedPolicy` as a safety net. If the LoRA's outputs don't solve, EF's template candidates still get tried. The LoRA's template-generation ability is irrelevant to benchmark score because EF covers the templates deterministically regardless.

**Rule**: For hybrid-gc policies, LoRA val loss only matters for the LoRA's *additive* contribution (novel tactics the EF catalog lacks). Template tasks are covered by the safety net regardless of the LoRA's quality. Don't conflate "high template val loss" with "will regress on template tasks" when the policy has an EF fallback.

This also means: **Config A is strictly better than Config B on any axis that measures the LoRA's contribution**, because Config A's outputs are cleaner (no template bias), more structural (13.6% match), and it doesn't regress on templates (EF handles them). Config B's apparent advantage (4 vs 3 nominal solves, 2 vs 1 novel) is partly noise and partly from vocabulary scale (17K template pairs exposing more function names the LoRA can reach for).

---

## Decisions recorded

1. **Use source-level converter, not Pantograph extraction**. `env_inspect(print_value=True)` returns unreadable `brecOn` kernel terms.

2. **Always restart Pantograph daemon between benchmark runs**. Corruption after ~3 runs is unavoidable in current daemon code.

3. **Always verify training data format matches benchmark goal state format**. Use `intro`-style verification with `goal_tactic(state, f"intro {names}")` before capturing the state string.

4. **Run hybrid-gc benchmarks at 50 rollouts, not 500**. LoRA contributions appear at rollout 1 if they're in top-k; full budget doesn't help much.

5. **Inspect generations directly alongside benchmark runs**. The benchmark score is a lagging indicator; direct sampling shows what the model actually learned.

6. **Don't trust "SOLVED" for LoRA-generated novel tactics without manual review**. The 25% false positive rate on Sprint 28's one novel diagnostic solve is a warning signal.
