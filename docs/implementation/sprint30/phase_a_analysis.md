# Sprint 30 Phase A — Analysis

> **Correction (2026-11-21).** An audit and re-run found that conclusions in this document do not hold as written: the Qwen prompts contained only the goal type while the other models received the task description; with the description, Qwen-7B solves 4/12. See [`docs/WRITEUP.md`](../../WRITEUP.md) for the corrected results. The text below is kept as the original record.

**Date**: 2026-04-15
**Primary metric**: `held_out_12` solve count (same as S29 — 6 hard-holdout + 6 diag-holdout, frozen behind `s29-heldout-frozen`)
**Methodology**: zero-shot capability probe with a frozen generic few-shot preamble, 8 samples per task × 12 tasks, oracle-verified against the task's test cases via the Pantograph daemon.

## Headline

| Run | Model                  | solved | rate    | wall_gen | wall_verify |
|-----|------------------------|:------:|:-------:|:--------:|:-----------:|
| 1   | Qwen2.5-Coder-1.5B     | **0/12** | 0.0 %   | 23.7 s   | 9.3 s       |
| 2   | Qwen2.5-Coder-7B       | **0/12** | 0.0 %   | 41.4 s   | 14.4 s      |
| 3   | Claude Opus 4.6 (via Agent) | **12/12** | 100.0 % | 0 s*     | 68.0 s      |

*Frontier generation was delegated to 12 parallel Claude Agent calls from this session, each asked to produce 8 structurally-diverse tactic variants as a JSON array. Wall time per agent: ~20–40 s. This is authored diversity in a single forward pass, not independent temp-0.2 samples.

## Plan gates hit

The Phase A plan in [`sprint30.md`](sprint30.md) defines four gate rows. This result maps cleanly to:

> **Frontier ≥3/12, Qwen-7B ≤1/12 → Base model matters — Phase D has capacity justification. Proceed to Phase B, then Phase D.**

Specifically:
- Frontier produced 77/96 passing tactics across 12 tasks — every task had ≥4 passing candidates. No task was impossible at current-generation code-LLM scale.
- Both Qwen base models produced 0 solves, with a ~23–41 s generation wall. The gap between Qwen-7B and the frontier is the entirety of the ceiling.
- The "Qwen-1.5B ≈ Qwen-7B" joint result (both 0/12) also argues that **more of the same pretraining distribution is not the lever**. Doubling the parameter count of a pretrained code base model without changing the training corpus or instruction-tuning is not sufficient.

## Frontier solve quality — compose vs library lookup

Same sanity check the plan requires for Phase B-1: is the frontier *composing* from reasoning, or *cheating* via library-lookup?

| Task                      | solves | breakdown of passing tactics                           |
|---------------------------|:------:|--------------------------------------------------------|
| eval_stack                | 7/8    | fold × 5, let_rec × 2                                  |
| majority_element          | 8/8    | fold × 6, let_rec × 2                                  |
| map_with_index            | 6/8    | fold × 2, idx_builtin × 1, let_rec × 1, match × 1, other × 1 |
| max_subarray_sum          | 8/8    | fold × 7, let_rec × 1                                  |
| prefix_while_increasing   | 4/8    | let_rec × 2, fold × 2                                  |
| unfold_n                  | 7/8    | fold × 3, other × 3, let_rec × 1                       |
| sum_at_odd_indices        | 6/8    | fold × 3, let_rec × 2, idx_builtin × 1                 |
| zip_diff                  | 7/8    | other × 4, match × 1, let_rec × 1, fold × 1            |
| longest_increasing_run    | 7/8    | fold × 5, let_rec × 2                                  |
| max_subarray_product      | 8/8    | fold × 6, let_rec × 2                                  |
| merge_sorted_desc         | 7/8    | **library_lookup × 5**, let_rec × 2                    |
| window_sums               | 6/8    | fold × 5, match × 1                                    |

**`merge_sorted_desc` is the only task with library-lookup passes** (`List.merge`, `mergeSort`, `qsort`). But even it has 2 composed `let rec merge` solves, and `let rec merge` is the *first* passing candidate. So every held_out_12 task has at least one composed (non-library-lookup) solve from the frontier. The 12/12 is not an artifact of stdlib fishing.

Most passing tactics use a short `foldl`/`foldr` with an accumulator tuple — the canonical functional pattern for the Kadane-style, index-tracking, and state-machine tasks. This is exactly the structural vocabulary that S29's LoRA adapters failed to generate (0/125 `let rec` samples; the `fold`-based passes here would have been in-reach for S29's training distribution but the adapter smoothed them out).

## Per-task first-passing tactic (for audit)

```
eval_stack               exact xs.foldl (fun st x => if x = 0 then match st with | a :: b :: rest => ...) [] |>.headD 0
majority_element         exact let rec bm : List Int → Int → Int → Int | [], c, _ => c | ... ; bm xs 0 0
map_with_index           exact xs.mapIdx (fun i x => x * (Int.ofNat (i + 1)))
max_subarray_sum         exact xs.foldl (fun (p : Int × Int) x => let cur := max x (p.1 + x); (cur, max p.2 cur)) (0, 0) |>.2
prefix_while_increasing  exact let rec go : Int → List Int → List Int | _, [] => [] | prev, x :: rest => if prev < x then x :: go x rest else []; ...
unfold_n                 exact let rec go : Nat → Int → List Int | 0, _ => [] | Nat.succ k, s => s :: go k (f s); go n seed
sum_at_odd_indices       exact (xs.zipIdx.filter (fun p => p.2 % 2 = 1)).foldl (fun acc p => acc + p.1) 0
zip_diff                 exact List.zipWith (fun a b => b - a) xs xs.tail
longest_increasing_run   exact let rec loop : List Int → Nat → Int → Nat → Nat | [], best, _, cur => max best cur | ...
max_subarray_product     exact let rec loop : List Int → Int → Int → Int → Int | [], _, _, best => best | ...
merge_sorted_desc        exact let rec merge : List Int → List Int → List Int | [], bs => bs | as, [] => as | a::as, b::bs => if a >= b then ...
window_sums              exact if k = 0 ∨ k > xs.length then [] else (List.range (xs.length - k + 1)).map (fun i => ((xs.drop i).take k).foldl (· + ·) 0)
```

## Methodology caveats

1. **Authored diversity, not sampling diversity.** Frontier generation was one forward pass per task asking for 8 structural variants, not 8 independent temp-0.2 samples. This answers "what's the ceiling when the model tries its best?" and is fine for the capability probe, but it's not directly comparable to the Qwen runs (which sampled at temp 0.2 with dedup collapsing to 1–3 unique candidates per task).
2. **Qwen runs lost most of their sample count to dedup.** At temp 0.2, top_p 0.95, the Qwen base models produced 1–3 unique candidates per task after `list(dict.fromkeys(...))` dedup. The raw 8 samples were near-identical. Raising temperature would add variance but not knowledge — the Qwen outputs that did appear were all uncreative `xs.foldl (fun acc x => acc + x) 0`-style attempts or hallucinated Lean stdlib names (`List.fromStream`, etc.).
3. **Frontier's library-lookup risk.** Only `merge_sorted_desc` has any library-lookup solves, and even it has 2 composed `let rec` solves. Trace inspection would not change the verdict from 12/12 to 11/12 or lower.
4. **Frontier prompt is NOT the same as Phase B will use.** Phase A's few-shot set is generic — 5 hand-curated library pairs reused across all 12 tasks. Phase B will use retrieval over the 404-pair corpus, so its prompt will be task-tailored. The frontier 12/12 is a lower bound on what a retrieval-augmented frontier could do.

## Implications for subsequent phases

### Phase B (retrieval-augmented, days 2–4, PRIMARY)

Phase A's 12/12 frontier result **strongly justifies running Phase B**. The capability ceiling is not at zero and not at the base Qwen-1.5B level — it's meaningfully above both. If retrieval over the 404-pair corpus can deliver the right structural examples to Qwen-1.5B's context, it might close some of the gap without any training.

Specific Phase B predictions from this result:
- **B-1 (k=10 BGE-code retrieval on Qwen-1.5B base)**: the 404-pair corpus contains `let rec` and `foldl`-with-accumulator examples — exactly the structural patterns the frontier used. If Qwen-1.5B can *condition* on a retrieved example (rather than recall from weights), it could produce similar outputs. Plausible range: 1–5/12.
- **B-3 (oracle retrieval, hand-picked by the experimenter)**: sets an upper bound — if the corpus *does* contain enough signal, a perfect embedder should find it. Plausible range: 3–7/12.
- **Failure mode to watch**: Qwen-1.5B base has degraded Lean syntax when generating long responses (S28 observation: C++/JS fallback). A retrieved `let rec` example might not survive transfer if the base model can't hold Lean syntax for a full tactic body.

### Phase C (`let rec` base-prior probe, day 5)

Phase A's result already partially answers Phase C: the frontier can produce `let rec` freely, the Qwen base models cannot under *this specific few-shot format*. Phase C will extend to unconstrained prompting ("Write a Lean 4 tactic that uses `let rec` to...") to separate prompt-format bottleneck from prior-weight bottleneck. With Phase A in hand, Phase C's most informative comparison is now:

- Qwen-1.5B with Phase A format → 0 `let rec` tactics that solve (observed)
- Qwen-1.5B with explicit `let rec` request → ?
- Qwen-7B with explicit `let rec` request → ?
- Qwen-1.5B with k=1 retrieved `let rec` example → ?

### Phase D (stronger base model, week 2+)

Phase A satisfies Phase D's first gate (`frontier ≥3/12`). The remaining gates ride on Phase B: if B-1 or B-3 solve ≥3/12, Phase D should pivot to retrieval-first on a stronger base; if both miss, Phase D's original LoRA-on-7B design holds. **Either way, Phase A rules out the "benchmark rescoping" exit** — the tasks are solvable, so S30 has clear success criteria.

## Decision

**Proceed to Phase B as planned.** Phase B runs on the existing daemon (restarted in Phase 0 with the new imports) and doesn't need additional model downloads beyond what's already cached (`Qwen2.5-Coder-1.5B`). The frontier result gives Phase B a concrete ceiling (12/12) to measure against — any fractional B-1 or B-3 result now has a clear interpretation relative to that ceiling.

## Deliverables (this sub-phase)

- `sprint/benchmark/results/phase_a_fewshot.json` — frozen 5-pair few-shot set
- `sprint/benchmark/results/phase_a_tasks_for_frontier.json` — 12 precomputed task goal states
- `sprint/benchmark/results/phase_a_frontier_prompts.json` — 12 agent prompts
- `sprint/benchmark/results/phase_a_frontier_agent_ids.json` — agent ID → task mapping
- `sprint/benchmark/results/phase_a_frontier_tactics.json` — 96 collected frontier tactics
- `sprint/benchmark/results/phase_a_qwen_1.5b.json` — Run 1 full result
- `sprint/benchmark/results/phase_a_qwen_7b.json` — Run 2 full result
- `sprint/benchmark/results/phase_a_frontier.json` — Run 3 full result
- `lean_mcts/benchmark/phase_a.py` — reusable Phase A runner (local + pre-generated modes)
