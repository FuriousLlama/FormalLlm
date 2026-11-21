# Sprint 12 — Project Status

Last updated: 2026-03-12

## Overall Progress

```
[x] Phase 1 — Mathlib Data Extraction
    [x] Pin Mathlib commit matching lean-toolchain v4.19.0 — c44e0c8ee6
    [x] Run extract_from_cache.py → train/mathlib_tactics.jsonl (bypasses trace() SIGSEGV)
    [x] Add filtering (remove sorry/skip/done, cap state length 2048)
    [x] Split train/val by source file (80/20) — auto-splits after extraction
    [x] Verify: 460K pairs (4.6× target), zero file overlap, quality spot-checked
[x] Phase 2 — Policy Fine-Tuning
    [x] Write train/dataset.py (JSONL → tokenized tensors)
    [x] Write train/pretrain.py (LoRA fine-tuning)
    [x] Train policy on mathlib_train.jsonl (100K subset, 3 epochs, val_loss 1.83)
    [x] Write TrainedPolicy class in search/policy.py
    [x] Verify: generates syntactically valid Lean tactics ✓ (but proof tactics, not synthesis)
[ ] Phase 3 — Value Network (DEFERRED — blocked on useful policy)
    [x] Write train/collect_rollouts.py
    [ ] Run MCTS with trained policy → rollout_data.jsonl
    [x] Write train/train_value.py
    [ ] Train value head
    [x] Update search/value.py with TrainedValueNetwork
    [ ] Verify: predictions correlate with outcomes (ρ > 0.3)
[x] Phase 4 — MCTS Upgrades (code complete)
    [x] Implement PUCT formula in mcts.py
    [x] Add prior field to Node dataclass
    [x] Integrate value network for leaf evaluation (value_weight param)
    [x] Adaptive rollout budget (via CLI --rollouts)
[x] Phase 5 — Expanded Benchmark
    [x] Write TasksHard.lean (5 new tasks + reference solutions)
    [x] Add tasks to benchmark/tasks.py with test cases
    [x] Verify: reference solutions compile (.olean exists) and pass all tests
    [x] DirectDojo fallback for untraced files (TasksHard.lean)
[x] Phase 6 — Evaluation
    [x] Update benchmark/run.py with --policy trained, --adapter-path, --value-model, --tier
    [x] Config A: FixedPolicy T2 — 10/10 solved (all rollout 1, baked-in)
    [x] Config A: FixedPolicy Hard — 0/5 (no matching expressions)
    [x] Config B: TrainedPolicy T2 (100K) — 0/10 (domain mismatch: proof tactics ≠ synthesis)
    [x] Config B': TrainedPolicy T2 (200K) — 0/10 (2x data, same result: domain mismatch)
    [ ] Config D: PromptedPolicy (skipped — same domain issue expected)
    [ ] Config C: TrainedPolicy + value (blocked on Phase 3)
```

---

## Key Metrics

| Metric                        | Target | Actual                                    |
| ----------------------------- | ------ | ----------------------------------------- |
| Mathlib pairs extracted       | ≥100K  | 460,689 (train: 373K, val: 87K)          |
| Policy val loss               | —      | 1.83 (3 epochs, 100K subset, Qwen 1.5B)  |
| Config A T2 solved            | 10/10  | **10/10** (FixedPolicy, baked-in)         |
| Config A Hard solved          | —      | **0/5** (no matching synthesis exprs)     |
| Config B T2 solved (100K)     | ≥5/10  | **0/10** (Mathlib proof tactics only)     |
| Config B' T2 solved (200K)    | ≥5/10  | **0/10** (lower val loss, same 0 result) |
| Config B Hard solved          | ≥1/5   | **0/5** (inferred, same domain mismatch) |

---

## Evaluation Analysis

### Config A (FixedPolicy) — Baseline
- **T2: 10/10**, all solved in rollout 1 (answers baked into tactic list)
- **Hard: 0/5** — FixedPolicy has `xs : List Int` expressions but hard tasks need
  `List (List Int)`, two-arg `xs ys`, product returns `List Int × List Int`, etc.
- Tree size always 2 on hard tasks (only 1-2 generic tactics pass type checking)
- Validates: MCTS infrastructure + oracle work correctly

### Config B (TrainedPolicy, Mathlib LoRA) — Key Finding
- **T2: 0/10, Hard: 0/5** — complete failure
- Model generates Mathlib proof tactics: `have`, `cases`, `refine`, `induction`, `simp`
- Top 20 candidates for `⊢ Nat` goal: zero `exact xs.filter/map/fold` expressions
- Degenerate patterns: `exact xs.reverse.reverse...` (126×), `let a := xs.length + 1 - 1 + 1...` (40×)
- Tree size: 2 on every task (only 1 degenerate tactic passes type checking)
- **Root cause**: Domain mismatch — Mathlib trains proof obligations, benchmark needs program synthesis

### Why Mathlib Training Doesn't Transfer
1. Mathlib tactics close proof obligations (`simp`, `rewrite`, `apply lemma`)
2. Synthesis tasks need expressions (`exact xs.filter (fun x => ...)`)
3. The model learned the _language_ of proofs, not the _language_ of programs
4. Even when a tactic type-checks (e.g. `exact xs.reverse.reverse...`), it's semantically useless
5. This is analogous to Sprint 10's T2 bottleneck: the model can't compose expressions it hasn't seen

---

## Decisions Log

| Date       | Decision                                                   | Rationale                                                                    |
| ---------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------- |
| 2026-03-11 | Use extract_from_cache.py instead of extract_data.py       | trace() SIGSEGV on re-run; cached AST files already present from first build |
| 2026-03-11 | Symlink gitpython-mathlib4 → leanprover-community-mathlib4 | Cache key mismatch between cloned repo dirname and LeanDojo expected dirname |
| 2026-03-12 | Defer Phase 3 (value net) until after evaluation           | No point training value head when policy produces 0 useful tactics           |
| 2026-03-12 | Skip Config D (PromptedPolicy)                             | Zero-shot Qwen on Lean synthesis would face same domain gap                  |
| 2026-03-12 | Pin LeanEnv to traced commit hash                          | Avoids re-tracing entire Mathlib when FormalNav HEAD changes                 |
| 2026-03-12 | DirectDojo fallback for untraced files                     | Enables hard tasks without full LeanDojo retrace of 2806+ modules            |

---

## Blockers

- **Mathlib→synthesis domain gap**: The trained policy generates proof tactics, not synthesis
  expressions. Training on Mathlib data does not teach the model to compose programs.
  Potential fixes: (a) train on synthesis-specific data, (b) use a prompted policy with
  synthesis examples in context, (c) hybrid approach mixing FixedPolicy expressions with
  learned reranking.
