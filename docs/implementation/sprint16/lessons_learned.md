# Sprint 16: Lessons Learned

## Direct pair construction beats multi-step
The model learned to generate `exact (xs.foldl min 0, xs.foldl max 0)` directly instead of needing `constructor` → 2 sub-goals. This avoids the multi-step MCTS path entirely. Same for `split_apply`: `exact (xs.map (fun x => x * 2), xs.map (fun x => x + 1))`.

**Implication**: Training on both `constructor` sub-goals AND direct pair expressions is valuable — the model can learn the shortcut.

## first_n_squares fails on oracle, not policy
The GoalConditioned model correctly generates `exact (List.range n).map (fun i => i * i)` for the `n : Nat ⊢ List Nat` goal. But the oracle file generation doesn't handle `n : Nat` input type — the generated `_Eval_*.lean` file uses `n` without declaring it (`Unknown identifier 'n'`). This is an existing oracle bug, not a Sprint 16 issue.

## zip_map tuple destructuring doesn't work in Lean 4
`(xs.zip ys).map (fun a b => a + b)` fails — Lean's `zip` returns `List (α × β)`, not a destructured pair. Need `(xs.zip ys).map (fun p => p.1 + p.2)` or `List.zipWith` instead. 7 synthetic pairs failed verification for this reason.

## val_loss plateau at 0.28
Training converged to val_loss 0.2759 (stretch target < 0.31 achieved). The loss curve showed steady improvement through all 10 epochs with no early stopping, suggesting the dataset size (6704 unique pairs) is well-matched to the model capacity. No overfitting despite 10 epochs.

## GoalConditioned generation is slow
Each `propose_tactics` call generates 64 sequences (32 at T=0.8 + 32 at T=1.4) with `output_scores=True`. At 200 rollouts, the V2 benchmark took ~30 min vs ~5 min for ExpandedFixed. The caching helps (same state → cached results), but novel states still require full inference.

## Novelty is real but noisy
Some "novel" tactics in the report are actually incorrect (e.g., `concat_all` solved with `exact (xss.flatten).filter (fun x => decide (x > 0))` which adds an unnecessary filter). The oracle verifies correctness against test cases, but tests may not catch all edge cases. The model generates plausible-looking but sometimes semantically wrong tactics that happen to pass limited test cases.

## partition_and_count regression at 50 rollouts
Was solved by ExpandedFixed at 500 rollouts (Sprint 15). Now failed at 50 rollouts with HybridGC. The GoalConditioned candidates dilute MCTS budget — more candidates but fewer rollouts per candidate. At higher rollout counts it would likely still solve. This confirms the ExpandedFixed safety net is necessary.
