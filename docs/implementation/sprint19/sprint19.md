# Sprint 19: Fundamental Programming Patterns

## Goal

Teach the model basic CS programming fundamentals (pattern matching, recursion, let bindings, branching) by adding ~3 examples of each, then evaluate whether 1.5B params + LoRA can compose these into novel algorithms it has never seen.

## Context

Sprint 18 achieved T2: 10/10, V2: 19/20, Hard: 5/5, Recurrence: 2-4/6. Analysis revealed:

1. **Training data is 100% combinator pipelines**: All 5,501 training pairs are `exact xs.filter(...).map(...).foldl(...)` chains. Zero examples of `match`, `List.rec`, `Nat.rec`, top-level `let`, or top-level `if/then/else`.
2. **Only 95 unique algorithmic templates** exist (rest are predicate/transform permutations).
3. **Model knows 26 of ~70 stdlib functions**.
4. **Recurrence failures are sample-limited, not capacity-limited**: fibonacci solved at seed 1337 proves the 1.5B model CAN generate the pattern — it just hasn't seen enough examples.

**Hypothesis**: Adding ~3 examples per fundamental pattern (~21 base pairs → ~84 with augmentation, a 1.2% increase) is enough for the model to learn these primitives and compose them into structures it has never seen.

All proposed patterns verified to compile in Lean 4 (`TestFundamentals.lean`).

## Phases

### Phase 0: Sprint Documentation (0.5 hours)

- [x] Create `docs/implementation/sprint19/` directory structure
- [ ] Write sprint plan (`sprint19.md`)
- [ ] Create status file (`sprint19_status.md`)

### Phase 1: Training Data — 21 Hand-Crafted Pairs (1-2 hours)

Add 7 categories × 3 examples to `HAND_CRAFTED_REFERENCES` in `collect_goal_conditioned.py`.

#### Category 1: Pattern Matching on List (3 pairs)

| # | State | Tactic | Purpose |
|---|-------|--------|---------|
| 1a | `xs : List Int ⊢ Int` | `exact match xs with \| [] => 0 \| h :: _ => h` | Head with default |
| 1b | `xs : List Int ⊢ List Int` | `exact match xs with \| [] => [] \| _ :: t => t` | Safe tail |
| 1c | `xs : List Int ⊢ Int` | `exact match xs with \| [] => 0 \| [x] => x \| x :: y :: _ => x + y` | Nested pattern |

#### Category 2: Pattern Matching on Nat (3 pairs)

| # | State | Tactic | Purpose |
|---|-------|--------|---------|
| 2a | `n : Nat ⊢ Nat` | `exact match n with \| 0 => 0 \| k + 1 => k` | Predecessor |
| 2b | `n : Nat ⊢ Nat` | `exact match n with \| 0 => 1 \| _ + 1 => 0` | Is zero |
| 2c | `n : Nat ⊢ Nat` | `exact match n with \| 0 => 0 \| 1 => 1 \| _ + 1 => 2` | Three-way classify |

#### Category 3: List.rec — Structural Recursion on Lists (3 pairs)

| # | State | Tactic | Purpose |
|---|-------|--------|---------|
| 3a | `xs : List Int ⊢ Nat` | `exact List.rec 0 (fun _ _ acc => acc + 1) xs` | Length from scratch |
| 3b | `xs : List Int ⊢ Int` | `exact List.rec 0 (fun h _ acc => acc + h) xs` | Sum from scratch |
| 3c | `xs : List Int ⊢ List Int` | `exact List.rec [] (fun h _ acc => (h * 2) :: acc) xs` | Map double from scratch |

#### Category 4: Nat.rec — Natural Number Recursion (3 pairs)

| # | State | Tactic | Purpose |
|---|-------|--------|---------|
| 4a | `n : Nat ⊢ Nat` | `exact Nat.rec 1 (fun k acc => acc * (k + 1)) n` | Factorial |
| 4b | `n : Nat ⊢ Nat` | `exact Nat.rec 0 (fun k acc => acc + k + 1) n` | Sum 1..n |
| 4c | `n : Nat ⊢ Nat` | `exact Nat.rec 1 (fun _ acc => acc * 2) n` | Power of 2 |

#### Category 5: Let Bindings (3 pairs)

| # | State | Tactic | Purpose |
|---|-------|--------|---------|
| 5a | `xs : List Int ⊢ Nat` | `exact let pos := xs.filter (fun x => decide (x > 0)); pos.length` | Name intermediate |
| 5b | `xs : List Int ⊢ Int` | `exact let sorted := List.mergeSort xs; let top := sorted.reverse; match top with \| [] => 0 \| h :: _ => h` | Chain two lets |
| 5c | `xs : List Int ⊢ Nat` | `exact let (pos, neg) := xs.partition (fun x => decide (x > 0)); pos.length + neg.length` | Destructuring let |

#### Category 6: If/Then/Else Branching (3 pairs)

| # | State | Tactic | Purpose |
|---|-------|--------|---------|
| 6a | `n : Nat ⊢ Nat` | `exact if n > 100 then 100 else n` | Clamp |
| 6b | `xs : List Int ⊢ Nat` | `exact if xs.length > 5 then (xs.filter (fun x => decide (x > 0))).length else xs.length` | Conditional pipeline |
| 6c | `xs : List Int ⊢ Int` | `exact if xs.length > 0 then match xs with \| h :: _ => h \| [] => 0 else 0` | Safe head |

#### Category 7: Combined Patterns (3 pairs)

| # | State | Tactic | Purpose |
|---|-------|--------|---------|
| 7a | `xs : List Int ⊢ Int` | `exact List.rec 0 (fun h _ acc => if h > acc then h else acc) xs` | Max via rec + if |
| 7b | `xs : List Int ⊢ Nat` | `exact List.rec 0 (fun h _ acc => if decide (h > 0) then acc + 1 else acc) xs` | Count pos via rec + if |
| 7c | `xs : List Int ⊢ Int` | `exact List.rec 0 (fun h t acc => match t with \| [] => h \| _ :: _ => acc) xs` | Last via rec + match |

**Lean note**: `decide` is needed for `Int` comparisons inside lambdas (e.g., `decide (h > 0)`). Not needed for top-level `Nat` comparisons (e.g., `if n > 100`). `List.rec` is a right fold (processes head-to-tail, builds right-to-left).

### Phase 2: Evaluation Tasks — 10 Fundamentals Tasks (2-3 hours)

Create `TasksFundamentals.lean` with 10 tasks requiring composition of fundamentals the model has learned individually but never seen combined.

| Task | Fundamentals Required | Reference Solution | Difficulty |
|------|----------------------|-------------------|-----------|
| `recReverse` | List.rec | `List.rec [] (fun h _ acc => acc ++ [h]) xs` | Medium |
| `recFilterPos` | List.rec + if | `List.rec [] (fun h _ acc => if decide (h > 0) then h :: acc else acc) xs` | Medium |
| `recMinimum` | List.rec + if | `List.rec 0 (fun h _ acc => if h < acc then h else acc) xs` | Medium |
| `natDoubling` | Nat.rec (baseline) | `Nat.rec 1 (fun _ acc => acc * 2) n` | Easy |
| `matchHeadOrSum` | if + match + foldl | `if xs.length > 2 then xs.foldl ... else match xs with ...` | Hard |
| `letFilterThenMax` | let + List.rec + if | `let pos := xs.filter ...; List.rec 0 (fun h _ acc => if h > acc then h else acc) pos` | Hard |
| `recSumNat` | Nat.rec (novel index) | `Nat.rec 0 (fun k acc => acc + k + 1) n` | Easy |
| `recMapAdd1` | List.rec (generalize) | `List.rec [] (fun h _ acc => (h + 1) :: acc) xs` | Easy |
| `matchThenRec` | match + List.rec | `match xs with \| [] => 0 \| [x] => x \| _ => List.rec 0 (fun h _ acc => acc + h) xs` | Hard |
| `recCountNeg` | List.rec + if (swap) | `List.rec 0 (fun h _ acc => if decide (h < 0) then acc + 1 else acc) xs` | Medium |

**Difficulty gradient**:
- **Easy** (direct recall / single swap): natDoubling, recSumNat, recMapAdd1
- **Medium** (one generalization): recReverse, recFilterPos, recMinimum, recCountNeg
- **Hard** (compose 2-3 fundamentals): matchHeadOrSum, letFilterThenMax, matchThenRec

### Phase 3: Benchmark Integration (1 hour)

- Add `FUNDAMENTALS_TASKS` list to `benchmark/tasks.py`
- Add `"fundamentals"` tier to `get_tasks()` and `run.py` CLI
- Add `import FormalNav.TasksFundamentals` to root `FormalNav.lean`
- Verify `lake build` succeeds

### Phase 4: Baseline (current model) on Fundamentals (1 hour)

Run current model (seed 1337 adapter) on the fundamentals tier:
```bash
python -m lean_mcts.benchmark.run --tier fundamentals --rollouts 50 --daemon
```
**Expected**: 0/10 (model has never seen any of these patterns)

This establishes the zero baseline for the experiment.

### Phase 5: Retrain + Evaluate (2-3 hours)

1. Regenerate training data:
   ```bash
   python lean_mcts/synth/collect_goal_conditioned.py
   ```
   Verify: 21 new hand-crafted pairs appear in output.

2. Retrain:
   ```bash
   python lean_mcts/train/pretrain.py \
     --data <merged_train.jsonl> \
     --val <merged_val.jsonl> \
     --seed 1337 \
     --output train/goal_cond_adapter/
   ```
   Expect: val_loss ~0.28 (minimal change from 21 new pairs).

3. Benchmark fundamentals:
   ```bash
   python -m lean_mcts.benchmark.run --tier fundamentals --rollouts 50 --daemon
   ```

4. Regression check:
   ```bash
   python -m lean_mcts.benchmark.run --tier all+v2+recurrence --rollouts 50 --daemon
   ```
   Must maintain: T2 10/10, V2 ≥ 18/20, Hard 5/5.

### Phase 6: Analysis + Lessons Learned (1 hour)

Document:
- Which fundamentals the model learned from 3 examples
- Which compositions it discovered vs. failed
- Whether the difficulty gradient (easy → medium → hard) matched predictions
- Implications for future data collection strategy

## Success Criteria

| Metric | Minimum | Target | Stretch |
|--------|---------|--------|---------|
| Fundamentals (easy, 3 tasks) | 2/3 | 3/3 | 3/3 |
| Fundamentals (medium, 4 tasks) | 1/4 | 2/4 | 4/4 |
| Fundamentals (hard, 3 tasks) | 0/3 | 1/3 | 2/3 |
| Fundamentals total | 3/10 | 6/10 | 9/10 |
| T2 no regression | 10/10 | 10/10 | 10/10 |
| V2 no regression | ≥ 18/20 | 19/20 | 20/20 |
| Hard no regression | 5/5 | 5/5 | 5/5 |
| val_loss | < 0.30 | ≤ 0.28 | < 0.27 |

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| 21 new pairs too few — model doesn't learn patterns | Medium | Each pattern appears 4x with augmentation. Model learned tuple-fold from ~50 examples in Sprint 17 |
| New patterns cause regression on existing tasks | Low | Only 1.2% increase in data. Run full regression benchmark |
| List.rec / Nat.rec syntax confuses the model | Low | Patterns are syntactically simple. Model already generates complex lambda expressions |
| Evaluation tasks too hard — 0/10 even after training | Medium | Difficulty gradient ensures easy tasks (direct recall) should pass. If 0/10, the fundamental patterns didn't stick |
| Augmentation incorrectly replaces variables inside match/rec | Low | Verify by inspecting augmented JSONL. Variable names (`xs`, `n`) appear at word boundaries |

## Estimated Effort

| Phase | Hours |
|-------|-------|
| Phase 0: Sprint docs | 0.5 |
| Phase 1: Training data | 1-2 |
| Phase 2: Eval tasks | 2-3 |
| Phase 3: Integration | 1 |
| Phase 4: Baseline | 1 |
| Phase 5: Retrain + eval | 2-3 |
| Phase 6: Analysis | 1 |
| **Total** | **8-11** |

## Files to Create

| File | Purpose |
|------|---------|
| `lean_mcts/env/FormalNav/FormalNav/TasksFundamentals.lean` | 10 tasks + reference solutions |
| `docs/implementation/sprint19/sprint19.md` | Sprint plan |
| `docs/implementation/sprint19/sprint19_status.md` | Sprint status tracker |

## Files to Modify

| File | Changes |
|------|---------|
| `lean_mcts/synth/collect_goal_conditioned.py` | Add 21 entries to `HAND_CRAFTED_REFERENCES` |
| `lean_mcts/benchmark/tasks.py` | Add `FUNDAMENTALS_TASKS` list + tier option |
| `lean_mcts/benchmark/run.py` | Add `"fundamentals"` to `--tier` choices |
| `lean_mcts/env/FormalNav/FormalNav.lean` | Add `import FormalNav.TasksFundamentals` |

## Non-Goals

- No new model architecture (still Qwen2.5-Coder-1.5B + LoRA r=16)
- No changes to MCTS search (rollouts, PUCT, value network)
- No changes to oracle verification
- No changes to GHC Core pipeline (`sprint/`)
- No self-play iteration (just one retrain cycle to test the hypothesis)
- No expansion of the expression enumeration (`expressions_v2.jsonl`)
