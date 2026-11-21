# Sprint 30 Phase B — Analysis

> **Correction (2026-11-21).** An audit and re-run found that conclusions in this document do not hold as written: the retrieval prompts contained only the goal type. With the task description added the result is still 0/12, but the explanation given here was not established. See [`docs/WRITEUP.md`](../../WRITEUP.md) for the corrected results. The text below is kept as the original record.

**Date**: 2026-04-15
**Primary metric**: `held_out_12` solve count
**Methodology**: retrieval-augmented zero-shot generation on Qwen2.5-Coder-1.5B (no LoRA), 8 samples × 12 tasks × temperature 0.2, oracle-verified via the Pantograph daemon. Retrieval runs over all 404 pairs (`diverse_train_v2.jsonl` + `diverse_val_v2.jsonl`) using Qwen's last-token hidden state as the embedder (chosen over downloading BGE-code after the existing model already gave a 1536-dim signal for free).

## Headline

| Config | Retrieval                       | k  | solved | Notes |
|--------|---------------------------------|----|:------:|-------|
| B-1    | Qwen hidden-state cosine        | 10 | **0/12** | Plan's "primary" config, modulo embedder choice |
| B-2    | Qwen hidden-state cosine        | 5  | **0/12** | Signal-focus ablation |
| B-3    | Oracle (hand-picked structural) | 10 | **0/12** | Upper bound on retrieval with perfect delivery |

**All three configs null.** The plan's gate `B-1 ≤2/12 AND B-3 ≤2/12` fires cleanly: *"Strong evidence for Phase D. Ceiling is base-model knowledge, not training pipeline."* — but Phase C (below) reshapes that reading.

## Why retrieval didn't help — a dissection

### The embedder (B-1/B-2) is weak

Top-3 retrievals from Qwen hidden-state cosine on representative tasks:

| Task                    | Top-3 retrieved names          | Observation                                         |
|-------------------------|--------------------------------|-----------------------------------------------------|
| eval_stack              | `or`, `and`, `prod`            | **Surface-token clustering**, no structural match   |
| majority_element        | `or`, `and`, `prod`            | Same — boolean/product library fns                  |
| max_subarray_sum        | `or`, `and`, `prod`            | Same                                                |
| map_with_index          | `partialSums`, `dropLastTR`, `partialProds` | At least List-over-Int, closer semantic space |
| unfold_n                | `takeD`, `take`, `drop`        | List take/drop, not nat-recursive generation        |

Several tasks with `xs : List Int ⊢ Int` return type all pulled the same `or/and/prod` top-k — the embedder is clustering by surface features of the goal string, not by structural/semantic similarity of the required tactic. This is the "embedder is the bottleneck" failure mode the plan anticipated for B-1 but intended to fix in B-3.

### The corpus is structurally thin on key patterns (B-3)

I tagged all 404 corpus pairs by structural pattern. Frequencies:

| Pattern              | Count | Tasks that need it                                   |
|----------------------|:-----:|------------------------------------------------------|
| `let_rec`            | 87    | many — unfold_n, merge_sorted_desc, prefix_while_increasing |
| `match_2arg`         | 66    | merge_sorted_desc                                    |
| `fold_method` (any)  | 36    | most Kadane-style tasks                              |
| `match_list` (1-arg) | 34    | eval_stack, prefix_while_increasing                  |
| `foldl`              | 24    | most accumulator tasks                               |
| `nat_rec`            | 14    | unfold_n, window_sums                                |
| `zip` ops            | 8     | zip_diff                                             |
| **`tuple_accumulator` folds** | **4**  | majority_element, max_subarray_*, longest_increasing_run |
| **`index_aware` (zipIdx/mapIdx/enum)** | **2** | map_with_index, sum_at_odd_indices          |

**Only 4 corpus pairs exemplify tuple-accumulator folds, and only 2 exemplify index-aware iteration.** These are exactly the structural patterns needed for half of `held_out_12`. The corpus is not just embedder-bottlenecked — for Kadane-style tuple accumulators and index-tracking folds, the raw supply is tiny.

### The base model can follow structure but not synthesize semantics

B-3's hand-picked selections delivered structurally-correct few-shot examples for each task. The generations DID adopt the retrieved pattern, but failed at semantic synthesis. Examples:

**merge_sorted_desc** — retrieved `bagInter`/`headD`/`getLastD` (all `match_2arg`), Qwen produced 5 unique tactics all using the right `match xs, ys with | [], _ => ... | _, [] => ... | x::xs, y::ys => ...` shape:

```lean
-- retrieved pattern delivered correctly:
exact match xs, ys with
  | [], _       => []
  | _, []       => []
  | x::xs, y::ys => if x == y then x::List.bagInter xs ys else List.bagInter xs ys

-- semantic error: this computes intersection, not merge
```

**unfold_n** — retrieved `range`, `replicateTR`, `dropSliceTR` (all `let_rec` + `nat_rec`), Qwen produced 6 unique `let rec go : Nat → Int → List Int | 0, _ => [] | n+1, seed => ...` variants. All had semantic errors in the recursion body — discarding `seed` in the recursive call, dropping the current element from the result, or inverting append order. The template was right; the recursion body was wrong.

This is an **important finding for Phase D's theory of change**: the bottleneck in Qwen-1.5B is not pattern coverage (retrieval can deliver it) and not pattern template generation (Phase C below shows 78–99% `let rec` rate). The bottleneck is **correctly synthesizing the recursion body under a given template** — a deeper semantic-synthesis capability gap than "go bigger for more pattern exposure" would fix.

## Caveats

1. **Qwen hidden state ≠ BGE-code.** The plan suggested BGE-code or CodeBERT first, with Qwen hidden states as a fallback. I went directly to Qwen to avoid the BGE download and because the hidden-state signal is what the model itself uses — if the model's own representation can't retrieve useful examples, an external embedder is unlikely to be dramatically better for this corpus/task pair. That said, a stronger embedder could move B-1 toward B-3's structural-match behavior. B-1/B-2 null + B-3 null together ARE enough to rule out "embedder is the only bottleneck."
2. **Generation-loop truncation bug.** Initial B-3 run had `map_with_index` and `max_subarray_sum` producing 0 unique candidates due to an overly aggressive `\n\n` stop rule in `generate_local`. Patched mid-run (`phase_a.py:generate_local` now `.lstrip()`s before the stop scan and only breaks on blank-line-after-content). Two tasks still produce 0 unique on some configs — raw model output is the empty string (immediate EOS), which is a real model behavior, not a harness bug.
3. **Candidate count is low (0–6 unique per task) at temp 0.2.** At this temperature, dedup collapses 8 samples to very few unique. Raising temperature would add variance but the pattern generated under higher temperature is the same kind of structurally-correct-semantically-wrong output seen at temp 0.2 — higher variance won't convert near-miss to correct.
4. **B-3 oracle protocol trade-off.** My oracle selection is rule-based structural matching (extract patterns, score pairs, take top-10), not pure human judgment. I acted as the "experimenter" by picking the weight mapping `{task → required patterns}` from goal state + description only (no test cases, no reference tactics). This is as fair as a hand-selection process can be while still being reproducible. The selection JSON is committed at `sprint/benchmark/results/b3_oracle_selections.json` for audit.

## Implications for Phase D

The plan's strict reading of B-1 ≤2/12 AND B-3 ≤2/12 would fire "**strong evidence for Phase D**." But Phase C's results (next analysis) invalidate Phase D's original premise, so the correct decision is not "proceed to Phase D as designed" — it's "reshape Phase D based on the Phase B + C joint read." See [phase_c_analysis.md](phase_c_analysis.md) for the reshape argument.

## Deliverables

- `lean_mcts/benchmark/phase_b.py` — retrieval-augmented runner with index + configs
- `sprint/benchmark/results/phase_b_index.npz` — 404 × 1536 embeddings
- `sprint/benchmark/results/phase_b_corpus_tagged.json` — structural pattern tags
- `sprint/benchmark/results/b3_oracle_selections.json` — frozen oracle selections with protocol doc
- `sprint/benchmark/results/phase_b_b1.json`, `phase_b_b2.json`, `phase_b_b3_v2.json` — raw per-task results
- `docs/implementation/sprint30/phase_b_analysis.md` — this file
