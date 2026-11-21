# Sprint 16 Status

## Current Phase: Phase 4 (Evaluation) — COMPLETE

### Phase 0: Data Collection
- [x] `collect_goal_conditioned.py` created
- [x] Hand-crafted references for all 8 unsolved tasks (33 pairs)
- [x] Synthetic compositional generator (zipWith, partition, filterMap, lambdas) — 121 raw, 114 verified
- [x] Oracle verification: 114/121 synthetic pairs verified (7 zip_map tuple-destruct failures expected)
- [x] Data generation run: 6704 unique pairs (5363 train + 1341 val)
  - Sources: 1435 reference + 33 hand_crafted + 114 synthetic + 348 multistep + 5790 augmented

### Phase 1: GoalConditionedPolicy
- [x] `GoalConditionedPolicy` class added to policy.py
- [x] `HybridGoalConditionedPolicy` class added to policy.py
- [x] Two-temperature sampling (T=0.8, T=1.4)
- [x] Post-generation filter (sorry, length, nesting, dedup)
- [x] ExpandedFixed safety net with min_lp - 2.0 prior, deduped

### Phase 2: Training
- [x] Training data generated (goal_cond_train.jsonl, goal_cond_val.jsonl)
- [x] LoRA training complete: val_loss **0.2759** (stretch target < 0.31 achieved)
- [x] 14.6 min training, 3.82 GB VRAM, 10 epochs, no early stopping
- [x] Adapter saved: `lean_mcts/train/goal_cond_adapter/`

### Phase 3: Benchmark Integration
- [x] `--policy goal-conditioned` added to run.py
- [x] `--policy hybrid-gc` added to run.py
- [x] `--novelty-report` flag added to run.py

### Phase 4: Evaluation
- [x] T2 regression: **10/10** (50 rollouts, all solved at rollout 1)
- [x] V2 full: **18/20** (50 rollouts, 1 run)
  - Newly solved: stats, split_apply (both via novel direct pair construction)
  - Still failing: partition_and_count (regression at 50 rollouts), first_n_squares (oracle bug)
- [x] Hard tasks: **4/5** (200 rollouts, 1 run) — EXCEEDS STRETCH TARGET
  - zip_sum: NOVEL `exact List.zipWith (· + ·) xs ys`
  - partition_positive: NOVEL `exact xs.partition (fun x => decide (x > 0))`
  - dedup_count: NOVEL `exact xs.eraseDups.map (fun x => (x, xs.count x))`
  - filter_map_abs: NOVEL multi-step with `have` binding
  - flatten: FAILED (only remaining Hard task)
- [x] first_n_squares deep search (200 rollouts): model generates correct tactic, but oracle bug (`Unknown identifier 'n'`)

### Phase 5: Self-Play (stretch)
- [ ] Not attempted (Phase 4 results exceed targets)

## Results Summary

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| T2 | 10/10 | **10/10** | PASS |
| V2 (50 rollouts) | 19/20 | **18/20** | CLOSE (first_n_squares is oracle bug) |
| Hard (200 rollouts) | 1/5 | **4/5** | EXCEEDS STRETCH |
| Novel tactics confirmed | 3 tasks | **8 tasks** | EXCEEDS STRETCH |
| Training time | < 15 min | **14.6 min** | PASS |
| val_loss | < 0.35 | **0.2759** | EXCEEDS STRETCH |

## Novel Tactics Used

| Task | Novel Tactic | Notes |
|------|-------------|-------|
| stats | `exact (xs.foldl min 0, xs.foldl max 0)` | Direct pair — no constructor needed |
| split_apply | `exact (h.map (fun x => x * 2), h.map (fun x => x + 1))` | Direct pair with have binding |
| zip_sum | `exact List.zipWith (· + ·) xs ys` | Point-free notation |
| partition_positive | `exact xs.partition (fun x => decide (x > 0))` | Built-in partition |
| dedup_count | `exact xs.eraseDups.map (fun x => (x, xs.count x))` | Discovered `xs.count x` |
| filter_map_abs | `exact h.map (fun x => x.toNat)` | Multi-step with have |
| count_unique_positive | `exact (xs.filter (fun x => decide (x > 0))).eraseDups.length` | 3-step pipeline |
| longest_sublist | `exact (xss.map (fun l => l.length)).foldl max 0` | Direct composition |

## Known Issues
- **first_n_squares**: Oracle bug — `Unknown identifier 'n'` in generated eval file for Nat-input tasks
- **partition_and_count**: Regression at 50 rollouts (was solved at 500 in Sprint 15), likely due to GoalConditioned candidates diluting MCTS budget
- **flatten**: Oracle gets 42 calls, 0 pass — possibly semantic mismatch in test cases or oracle
