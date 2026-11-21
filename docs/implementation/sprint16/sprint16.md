# Sprint 16: Goal-Conditioned Neural Tactic Generation

## Goal
Replace ExpandedFixedPolicy's lookup table with a generative model that reads the goal state and proposes novel tactic strings, verified by existing oracle infrastructure.

## Context
Sprint 15: Pantograph 0 crashes, parallel oracle 3.3x speedup, warm oracle 375ms.
Ceiling: ExpandedFixed's 1435 enumerated expressions = V2 17/20, Hard 0/5.
The remaining 8 tasks fail because needed expressions aren't in the table.

## Phases

### Phase 0: Data Collection
- Diagnose unsolved V2/Hard tasks (verbose MCTS runs)
- Hand-craft ~35 reference solutions for 8 unsolved tasks
- Generate ~100+ synthetic compositional expressions (zipWith, partition, filterMap, lambdas)
- Oracle-verify, augment 3x, merge with existing 1435 expressions
- Target: >= 7000 unique (state, tactic) pairs

### Phase 1: GoalConditionedPolicy + HybridGoalConditionedPolicy
- GoalConditionedPolicy: generates novel tactics via LoRA, two-temperature sampling
- HybridGoalConditionedPolicy: GoalConditioned + ExpandedFixed safety net
- Post-generation filter: reject sorry, >200 chars, >3 nesting, duplicates

### Phase 2: Training
- Reuse pretrain.py, same hyperparameters as Sprint 13
- Target: val_loss < 0.35, training < 15 min

### Phase 3: Benchmark Integration
- New --policy choices: goal-conditioned, hybrid-gc
- --novelty-report flag for identifying novel tactics

### Phase 4: Evaluation
- T2 regression: 10/10 (safety net)
- V2: >= 17/20, target 19-20/20
- Hard: target 1-3/5

### Phase 5: Self-Play Iteration (stretch)

## Success Criteria
| Metric | Minimum | Target | Stretch |
|--------|---------|--------|---------|
| T2 | 10/10 | 10/10 | 10/10 |
| V2 | >= 17/20 | 19/20 | 20/20 |
| Hard | 0/5 | 1/5 | 3/5 |
| Novel tactics | 1 | 3 | 5+ |
| val_loss | < 0.40 | < 0.35 | < 0.31 |
