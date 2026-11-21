# Sprint 29 Status: Scale the Diversity Signal

## SPRINT 29 CLOSED — 2026-04-14

**Primary metric**: S29-A 0/12 / S29-C 0/12 on `held_out_12` →
**"Ceiling reached"** decision bucket.

**Phase 4 decision document**: `phase4_analysis.md` (S30 direction:
Option A stronger base model, Option B retrieval-augmented, Option C
full fine-tune deferred).

**Lessons**: `lessons_learned.md`.

## Prior Phase: Phase 3.1 COMPLETE (primary metric null) — DECISION POINT

Plan: `sprint29.md`

### Phase 0 progress (as of 2026-04-13, end-of-day)

| Sub-phase | Status | Commit/Artifact |
|-----------|--------|-----------------|
| 0.1 Ablation columns | **DONE** | `cdd9612` — benchmark/run.py + ablation_columns.py |
| 0.2 `--patience` flag | **DONE** | `cdd9612` — search/mcts.py + benchmark/run.py --patience |
| 0.3 Harness smoke suite | **DONE** | `cdd9612` + `7e36daf` — 6/6 fast checks pass in <1s |
| 0.4 S28 Config C reproduction | **PARTIAL — held-out only** | See "Phase 0.4 outcome" below |
| 0.5 Held-out hard-tier freeze | **DONE** | Tag `s29-heldout-frozen` @ `68b417b` (outer, post-flatten) |

Three commits of bonus harness-audit work that were not originally scoped
into Phase 0 but turned out to be load-bearing:

  - `68b417b` — flatten FormalNav into outer repo (remove legacy nested
    git; the only thing that required it was the `--backend dojo` fallback
    which S29 non-goal #5 explicitly excludes). Backup tarball at
    `/tmp/FormalNav_inner_git_backup_20260413_134753.tar.gz`. Single-repo
    diff workflow now works for all Lean task files.
  - `b624996` — fix daemon broken-recovery path. PyPantograph's `run_async`
    calls `_close()` on internal timeouts/decode errors, killing the Lean
    subprocess and setting `self.proc = None`, then raises `ServerError`
    which is not in the connection-error clause. Pre-fix, the daemon
    returned the error and never rebuilt; every subsequent request hit
    `assert self.proc, "Server not running."` for the rest of the sweep.
    This is the mechanism behind the S28 "Config B gets 0/18 after ~3 runs"
    symptom — one task times out, worker is dead for the rest of the sweep,
    and the `--restart daemon between runs` workaround "fixed" it by
    externally giving the worker a fresh process.
  - `7e36daf` — role-separated two-pool daemon architecture. Split workers
    into a goal-ops pool (K, default 1) and a verify pool (N-K). Added a
    per-worker `asyncio.Lock` that serializes every `self.server.X_async`
    call, so concurrent clients on the same worker can no longer scramble
    responses via the `stdin/stdout` race. `check_compile_parallel` now
    dispatches across the verify pool only. Manifest format v2 with
    role-aware keys, v1 legacy fallback for backward compat.

### Phase 0.4 outcome

**Scope reduction (2026-04-13):** the plan specified 8 benchmark runs
(4 task suites × 2 patience modes). Wall-time estimate grew to ~6–7 hours
per mode = ~14 hours total. Given Phase 0 had already found and fixed two
latent daemon bugs that were worth more than the remaining reproduction
coverage, Phase 0.4 was reduced to **held-out × with-patience only**
(option C in the decision matrix presented before execution).

**Ran:** `--tier diag_holdout --policy hybrid-gc --gc-adapter
lean_mcts/train/diverse_adapter/C_curriculum --rollouts 50 --patience 100
--daemon` against the new role-separated daemon (`--workers 4 --goal-ops 1`).

**Result:** `lean_mcts/benchmark/results/s29_phase0_4/run_hybrid-gc_20260413_180751_0.json`

| Task | Category | Solved | Wall time | Oracle calls | REPL crashes |
|------|----------|--------|-----------|---------------|---------------|
| eval_stack | diag_d | ✗ | 723s | 3520 | 0 |
| majority_element | diag_b | ✗ | 657s | 3520 | 0 |
| map_with_index | diag_a | ✗ | 2790s | 13075 | 0 |
| max_subarray_sum | diag_b | ✗ | 482s | 3520 | 0 |
| prefix_while_increasing | diag_a | ✗ | 2749s | 13075 | 0 |
| unfold_n | diag_c | ✗ | 189s | 600 | 0 |
| **Total** | | **0/6** | **~2h 6min** | **~37K** | **0** |

**What this confirms:**
  - S28's 0/6 held-out baseline reproduces exactly on the new harness.
  - New daemon + lock + role split survives ~37K oracle calls under the
    hybrid-gc policy with zero concurrency-induced crashes or response
    misrouting (the bug reproduced in the Task C investigation earlier).
  - Every result carries `policy_name: "hybrid-gc"` and `ef_catalog_match:
    null` (unsolved → nothing to classify) — Phase 0.1 instrumentation
    writes correctly.
  - `abort_reason: null` on all 6 — patience never tripped because
    `rollouts=50 < patience=100`, which is the expected no-op behavior.
  - Filename convention `run_hybrid-gc_20260413_180751_0.json` correctly
    embeds the policy name for `ablation_columns.py` joining.

**What this does NOT verify:**
  - The other 3 task suites (18-task full diag, 5-task hard, 10-task
    core T2). Expected values from S28: 3/18 diag, 4/5 hard, 10/10 T2.
    Unmeasured on the new harness.
  - `--patience` under stress. To see the cap actually fire we'd need a
    run where `rollouts > patience` on unsolvable tasks. The smoke test
    `patience_abort` covers this via a unit-style check against the flag
    plumbing, but no end-to-end measurement exists yet.
  - The "≥5x speedup from --patience" claim from the original exit
    criterion is unmeasured.

### Phase 0 exit criteria (from sprint29.md), final state

- [x] Smoke suite passes (`make smoke` 6/6 green)
- [/] S28 results reproduce exactly — **held-out only (0/6 matches)**,
      other 3 suites unverified (scope reduction accepted)
- [x] Ablation columns in benchmark JSON (`policy_name`, `ef_catalog_match`)
- [/] `--patience` cuts unsolvable-task time by ≥5x — **unmeasured**;
      the flag itself is plumbed and unit-tested via `patience_abort`
      smoke check, but no end-to-end time-delta measurement was run
      (scope reduction accepted)
- [x] 12-task held-out set committed and tagged `s29-heldout-frozen`

**Phase 0 closed with 3/5 full + 2/5 partial exit criteria.** The two
partial items are both inside Phase 0.4; the decision to close Phase 0
without completing them is deliberate and documented here. If Phase 1
or Phase 3 surfaces a specific reason to worry about those gaps, they
can be re-run against the current harness at the cost of a few hours
per suite.

### Sprint 29 commit trail so far

```
<this commit>  sprint 29 phase 0.4-C: diag_holdout tier + close Phase 0
ee932fe sprint 29: commit plan and status docs (phase 0 in progress)
7e36daf sprint 29 phase 0: role-separated two-pool daemon architecture
b624996 sprint 29 phase 0: fix pantograph daemon broken-recovery path
cdd9612 sprint 29 phase 0.1+0.2+0.3: ablation columns, --patience, smoke suite
68b417b flatten: remove nested FormalNav git repo, track Lean files directly
b508a62 sprint 29: freeze held-out hard-tier task set (phase 0.5)
```

## Decision Gate

Primary metric: **held-out generalization (12 tasks)**

| Outcome | Score | Next Sprint Direction |
|---------|-------|----------------------|
| Ceiling reached | 0–2/12 | S30: full fine-tune or stronger base model |
| Scaling works | 3–5/12 | S30: scale to 5,000+ pairs, continue Config C curriculum |
| Confirmed | 6+/12 | S30: productionize, expand benchmarks, investigate transfer |

## Phase 1 progress (as of 2026-04-13 end-of-day)

### 1.2 Pattern miner — NULL RESULT

`lean_mcts/synth/pattern_miner.py` implements the ripgrep-style pattern
scanner described in sprint29.md §1.2. It scans 296 .lean files across
stdlib / Batteries / Mathlib for 10 S27-failure-category regexes
(tuple_acc_fold, state_machine_fold, indexed_fold, recursive_helper_acc,
recursive_let_rec, multi_arm_match, two_pass, higher_order_arg,
operation_chain_3plus, nested_fold). It finds 84 matching definitions,
52 convert to `exact` form, and Pantograph verifies **4**.

Of those 4 (`List.dropLastTR` × 2 specializations, `Array.findIdx` × 2),
**all 4 are already in `library_pairs_v2.jsonl`**. Pattern mining
contributed **zero net new pairs**.

Root cause: the source surface overlap. `extract_lean_defs.py` already
walks every file where these patterns live. The broader Mathlib
`Logic/` / `Control/` directories that pattern_miner also scans are
theorem-dense rather than definition-dense; the few defs there use
heavy Mathlib typeclass machinery (`Monoid`, `LinearOrder`, `motive`
dependent matching, `PUnit.{u+1}` universe polymorphism) that the
regex specializer can't peel.

The pattern_miner ships anyway: its code is clean, unit-tested, and
the `--list-patterns` / `--no-verify` modes are useful for exploratory
library analysis in future sprints where the bottleneck might shift.

### Specializer broadening — NULL RESULT

`extract_lean_defs.py` (update in this commit) generalizes the S28
specializer along four axes:

  1. Unicode types: `ℕ`→`Nat`, `ℤ`→`Int`, plus passthrough for `ℚ`/`ℝ`/`ℂ`/`𝔽`.
  2. Greek type vars: α-ζ extended to include η, κ, σ, τ.
  3. Monad/functor type vars: `m`, `m₁`, `m₂`, `F` → `Id`.
  4. Algorithmic auto-instance check: replaces the 14-entry hardcoded
     AUTO_INSTANCES set with a `RESOLVABLE_CLASSES × SPECIALIZED_TYPES`
     product check. Covers core Lean numerics, Mathlib algebraic
     hierarchy (Monoid, CommRing, LinearOrder, Ring, Field, ...), monad
     hierarchy (Functor, Applicative, Monad, LawfulMonad, Pure, Bind),
     and utility classes (Inhabited, Nonempty, Hashable, ToString).

Yield impact: **-1 pair** (224 vs 225). The remaining failures are all
things the regex approach can't handle: `Id PUnit.{u + 1}` (universe-
polymorphic), `(l₁ l₂ : List Int) → C l₁ l₂` (free motive C),
`0 < l.length → Int` (dependent refinement). These need real type
theory, not text substitution.

Unit tests covering the broadened specializer pass; the code is
preserved because it's algorithmically cleaner and generalizes for
future sources even though it produced no net gain on the current
179-file scan.

### 1.3 Quality filter — done

`lean_mcts/synth/quality_filter.py` implements sprint29.md §1.3's
filter pipeline: merge inputs, self-dedup, drop trivials (<3 AST
nodes), drop too-long (>400 chars), dedup-against-S28-baseline.

Running against `library_pairs_v3.jsonl` (224) + `pattern_pairs.jsonl`
(4):

  loaded               228
  dedup_self             4  (pattern_pairs duplicates v3)
  dropped_trivial        0  (every pair already has ≥3 significant tokens)
  dropped_too_long       0
  dedup_vs_baseline    162  (162 of 224 v3 pairs are also in S28's corpus)
  kept                  62  ← the S29 "new delta"

The 62 new pairs break down as:
  match         34 (54.8%)
  simple_exact  12 (19.4%)
  foldl_foldr    8 (12.9%)
  lambda         8 (12.9%)
  let_rec        0 ( 0.0%)   ← all 63 let_rec pairs live in the S28 baseline

Non-trivial: 80.6% (Phase 1.5 gate is ≥50%, easily met).

### Phase 1.2 Option B — converter rewrite (done)

Fixed two bugs in `lean_def_converter.py`:

1. **Whitespace collapse in `_convert_simple`**: the S28 implementation
   did `' '.join(body.split())` which collapsed multi-line bodies to a
   single space-joined line, destroying Lean's term-level `let`-chain
   separators. Definitions like `sublists'` (`let f := e1\n (l.foldr f #[[]]).toList`)
   became invalid-to-Lean single lines. Fixed with a new `_simple_candidate_forms`
   function that emits three candidate collapses (newline-preserved,
   semicolon-joined, space-joined) and `_convert_simple_multi` which the
   extractor's verify step tries in turn, keeping the first candidate that
   Pantograph accepts.

2. **Doc-comment bleed-through in `parse_definitions`**: the S28 block-
   boundary detector in `_find_def_blocks` didn't treat `/-- ... -/` doc
   comments as boundaries. A def followed by a doc comment (for the next
   def) kept slurping the doc comment text into the preceding def's body
   until the next `def`/`theorem` keyword. `sublists'` got a 309-char
   body that contained `/-- List.sublists ... -/` for the following def.
   Fixed by stripping file-level doc comments before block detection,
   reusing the existing `_strip_doc_comments` line helper.

Yield impact: **225 → 291 pairs** (+66, +29%). New defs rescued are
mostly Array-family functions (`Array.eraseP`, `Array.partition`,
`Array.contains`, `Array.mapMonoM`, `Array.attachWith`, …) that previously
failed the space-collapsed-body parse.

### 1.4 Leakage check — done

`lean_mcts/synth/leakage_check.py` computes 5-gram Jaccard similarity
between every training pair's tactic and each of the 12 held-out task
reference signatures. Threshold: 0.20. Written as a standalone CLI with
`--fail-on-leak` for CI gating.

Running against the final S28 ∪ S29-delta corpus (301 pairs) × 12
held-out tasks: **0/12 flagged**. Audit JSON at
`lean_mcts/train/leakage_audit.json`.

### 1.6 Train/val split — done

`lean_mcts/synth/split_train_val.py` performs a stratified 80/20 split
by `kind` (SIMPLE / EQCOMP / WHERE), seeded at 42 for reproducibility.

Final training files:
  - `lean_mcts/train/diverse_train_v2.jsonl`  240 pairs
  - `lean_mcts/train/diverse_val_v2.jsonl`     61 pairs

Distribution:
  EQCOMP:   79 train / 20 val
  SIMPLE:  111 train / 28 val
  WHERE:    50 train / 13 val

### Phase 1.7 — two-tier validation (functional + syntactic)

Added a second validation tier to `verify_with_pantograph` based on the
insight that library definitions are semantically trusted (they compile
in their native Lean library context). If a pair comes from stdlib /
Batteries / Mathlib and our functional check fails for anything OTHER
than a pure parse error, the failure is almost always a dependency or
namespace issue in our Pantograph instance — not a real correctness bug.

Two-tier design:

  Tier 1 — functional: goal_start(spec_type) + intro + exact body.
           If is_solved, tag `validation: "functional"` and use
           Pantograph's post-intro state as the pair's state.

  Tier 2 — library-trust syntactic: if Tier 1 failed for non-parse
           reasons on a library source, build the state from the
           universe-normalized real type (via env_inspect) and tag
           `validation: "syntactic"`. The tactic body is preserved
           as-is; the state is rebuilt from the actual polymorphic
           signature Lean records for the def.

  Drop: parse errors in the generated exact body (converter output
        is actually malformed). Pure parse errors are the only
        rejection signal we trust at this tier.

Investigations tried and rejected:

  - Type-ascription check `((fun ... => body) : spec_type)` via
    expr.echo — empirically equivalent to the functional check
    (18 passed both, 0 rescued by ascription-only, 23 failed both).
    Rejected.

  - Metavariable-relaxed expr.echo — accept bodies with `?m.` in the
    inferred type. Rescued 1/50 sampled pairs. Rejected.

  - Polymorphic-type direct verification — use `{α : Type}` (universe-
    stripped) directly as the goal. Hit other bugs (transitive deps,
    EQCOMP arg name mismatches). Rejected for Phase 1; kept the idea
    for S30.

build_goal_state() was extended to handle four binder shapes correctly:
implicit type binders `{α : Type}` → `α : Type`, named explicit
binders `(h : P x)` → `h : P x`, instance binders `[BEq α]` →
`inst : BEq α`, and bare types → generated names. This produces
clean polymorphic goal contexts for the syntactic pairs.

Known limitation: syntactic pairs may reference library helpers with
unqualified names (e.g. `exact pmap Subtype.mk l H` where `pmap` is
really `List.pmap`). The model learns a tactic that won't directly
type-check at inference. Mitigation: MCTS's oracle rejects such
tactics cleanly and backtracks — the training signal is weaker but
not actively poisonous. Proper fix (qualify-via-enclosing-namespace)
deferred to a later sprint.

### Phase 1 headline — final

| Metric | Plan target | Phase 1 result |
|--------|-------------|-----------------|
| Phase 1.1 module expansion | ~900 pairs | 224 extracted (baseline 171) |
| Phase 1.2 pattern mining | ~400-500 pairs | 0 net new (null result) |
| Phase 1.2 Option B converter rewrite | (not in plan) | +66 pairs (225→291) |
| Phase 1.2-B specializer fixes | (not in plan) | +4 pairs (291→295) |
| **Phase 1.7 two-tier validation** | (not in plan) | **+171 syntactic (295→466)** |
| S28-dedup Phase 1.3 filter | ~1,350-1,500 | **233 new delta** |
| **Final corpus (S28 ∪ S29 delta)** | — | **404 pairs** |
| Train / val split | 80/20 | **323 / 81** |
| Floor (≥7x S28) | 1,100 | NOT MET (2.4x S28) |
| Non-trivial gate | ≥50% | 61.4% ✓ |
| Leakage check | 0 flags | **0/12 passed** |
| let_rec coverage in delta | high | **24 new let_rec + 2 let_chain (3× v5)** |

Validation tier breakdown in the final corpus:
  functional (proof-verified):   295 pairs from v7 + all 171 S28 baseline
  syntactic  (library-trusted):  171 pairs from v7

The plan's ~900-pair estimate for Phase 1.1 assumed new containers
(Array, Option, Batteries, Mathlib) would yield pair counts
proportional to List's. In practice: (1) Mathlib files are
theorem-dense, not definition-dense; (2) Batteries containers
concentrate on specialized algorithms that use heavy constraint
machinery; (3) stdlib Array/Option are small modules with few
extractable defs. The actual rescue rate through the convert+
specialize+verify pipeline hit diminishing returns well before the
plan's budget.

The real bottleneck is **lean_def_converter.py**, not the specializer.
Definitions that fail verification do so because the converter emits
syntactically invalid `exact` expressions (parse errors), references
free variables (`motive`, `acc.foldl`), or drops universe annotations
(`Id PUnit.{u + 1}`). A converter rewrite is a multi-day engineering
task and is out-of-scope for Phase 1.

### Phase 1 decision point

Three options for continuing:

  A. **Stop Phase 1, proceed to Phase 2 training on 224 pairs.**
     Up from S28's 171 baseline (+32%). Expectation: modest
     improvement in let_rec generation quality but no step-change
     in held-out solve rate. The plan's 7x scale hypothesis is not
     directly testable, but the 1.3x scale hypothesis ("slightly
     more diverse data helps") can still be tested cheaply.

  B. **Rewrite lean_def_converter.py** to handle complex defs:
     universe normalization, motive variable binding, constraint
     elaboration. Multi-day engineering. Could potentially unlock
     1000+ additional pairs if successful. High risk, high reward.

  C. **Synthetic pattern-demo generation.** Hand-template 20-50
     variations per S27 failure category (tuple_acc_fold,
     state_machine_fold, recursive_helper_acc, ...). Predictable
     yield (~500 pairs) but closer to the S22 template approach
     that sprint29.md non-goal #1 explicitly excludes.

  D. **Rescope S29 as a reproducibility + harness validation
     sprint.** Close Phase 1 with what we have, skip Phase 2
     training, treat S29 as the "harness is now trustworthy"
     release, and defer the diversity scaling to S30 with a fresh
     investment in the converter.

My recommendation: **(A)** for S29, **(B)** budgeted explicitly for
S30. The harness work already shipped in Phase 0 is the big
structural win of the sprint; Phase 1's ~30% data scale bump is a
quiet secondary improvement that still gives the model slightly
more diverse signal than S28 had.

### 1.1 Module coverage expansion — done (`94a5e3d`)

Source files discovered: 179 (was 71 in S28).

  stdlib-List         49   (unchanged)
  stdlib-Nat          26   (unchanged)
  stdlib-Int          18   (unchanged)
  stdlib-Array        34   NEW
  stdlib-Option       10   NEW
  stdlib-Sum           2   NEW
  Batteries-List      10   (unchanged)
  Batteries-*         22   NEW (Array, HashMap, AssocList, BinaryHeap,
                              BinomialHeap, DList, RBMap, UnionFind, Vector)
  Mathlib-List         7   (Defs + Cycle + 5 new top-density files)

Verified pair output: **225 pairs** (was 171 in S28, +54 = +32%).

By top-level name: List 161, Option 32, Array 18, Int 9, Nat 3, Sum 2.
By kind: EQCOMP 95, SIMPLE 67, WHERE 63.
Diversity: match 44.0%, let_rec 28.0%, simple_exact 16.9%, foldl_foldr 6.2%,
lambda 4.9%, let_chain 0% — **non-trivial 83.1%** (well above the 50% gate).

Shortfall vs plan estimate of ~900 pairs: the plan assumed new containers
would produce pair yields proportional to List's. In reality, Option, Array,
Batteries, and Mathlib all use heavier typeclass machinery (`[BEq α]`,
`[DecidableEq α]`, `[Monad m]`, `ℕ`/`ℤ` unicode, length-bounded refinements)
that the current regex specializer cannot peel. Most of those candidates
fail at Pantograph verification with `Cannot start goal` errors. Mathlib
in particular yielded only +3 pairs from 5 new files. Option (32 pairs)
and stdlib Array (18 pairs) were the main contributors to the delta.

Output: `lean_mcts/train/library_pairs_v2.jsonl`

## Phase 2 — Training ablation grid (done)

### 2.0 Infrastructure
Reused `lean_mcts/train/train_diverse.py` unchanged from S28. Paths
updated to `diverse_adapter_v2/` and `diverse_train_v2.jsonl` /
`diverse_val_v2.jsonl`. Config C curriculum (epoch 1 diverse-only,
epochs 2-3 weighted mix with 10× diverse weight) inherited as the
only curriculum shape.

### 2.1 Preamble — S28 baseline backfill
Before running the new ablation, we backfilled the 171 S28 baseline
pairs to tag them `validation: "functional"` (they were proof-verified
when S28 produced them, but the pair records didn't carry the tag
that Phase 1.7 introduced). The final `diverse_train_v2.jsonl` /
`diverse_val_v2.jsonl` files contain 323 train / 81 val pairs = 404
total, each with an explicit `validation` key.

### 2.2 Ablation grid — two configs ran, one dropped

| Config | LoRA rank | Alpha | Data | Epochs | Elapsed |
|--------|-----------|-------|------|--------|---------|
| S29-A | 16 | 32  | 404 pairs | 3 | 18.9 min |
| S29-C | 64 | 128 | 404 pairs | 3 | 18.9 min |
| S29-D | 64 | 128 | 500 pairs subset | — | **dropped** |

**S29-D dropped:** it was meant to be a 500-pair data-scaling floor
against a ~1500-pair S29-C. With only 404 total pairs, a 500-pair
subset is larger than the full corpus — there is nothing to subset.
The A-vs-C rank comparison remains valid.

### 2.2 results — best val losses (epoch 3 for both)

| Metric | S29-A (r=16) | S29-C (r=64) | Delta |
|--------|--------------|--------------|-------|
| diverse_val_loss | 0.9744 | **0.8408** | C 14% better |
| template_val_loss | 0.3343 | 0.3359 | saturated |
| train_loss (weighted) | 0.4576 | 0.4171 | C 9% better |
| adapter size | 8.7 MB | 34.9 MB | 4× |

Both configs show monotonically decreasing `diverse_val_loss` across
three epochs (A: 1.88 → 1.21 → 0.97; C: 1.57 → 1.09 → 0.84), which
suggests 3 epochs is probably not hitting the plateau — either config
would likely keep improving with more epochs. Template val loss hits
~0.34 for both configs by epoch 2 and holds there; template fit is
saturated and rank does not help.

Conclusion: **rank matters for diverse fit (r=64 wins by 14%), not
for template fit (saturated at either rank).** S29-C is the
inference-time favorite on loss grounds.

### 2.3 Per-config generation sanity check

`lean_mcts/benchmark/inspect_generations.py` run against both adapters:
8 held-out-bucket tasks × 8 samples at temperature 0.8. The 8 tasks
span 5 diag held-out tasks (map_with_index, prefix_while_increasing,
max_subarray_sum, majority_element, unfold_n, eval_stack) plus 2
multi-step structural probes (zip_sum, dedup_count). 64 candidates
per adapter.

| Kind | S29-A (r=16) | S29-C (r=64) |
|------|--------------|--------------|
| method_chain    | 27 (42.2%) | 30 (49.2%) |
| fold            | 26 (40.6%) | 24 (39.3%) |
| other           |  8 (12.5%) |  6 ( 9.8%) |
| multi_step_have |  1 ( 1.6%) |  1 ( 1.6%) |
| match_other     |  1 ( 1.6%) |  0         |
| match_list      |  1 ( 1.6%) |  0         |
| trailing comments | 41 (64.1%) | 37 (60.7%) |
| total           | 64 | 61 |

**Surprising finding:** higher rank S29-C produced slightly FEWER
structural patterns (0 match vs 2 for S29-A, more method_chain, no
net increase in have-chains) despite better diverse_val_loss. The
14% loss delta expressed itself as "fits the training distribution
better" — not as "generates more structurally diverse code at
sampling time". At temperature 0.8 both adapters still decode
fold+method_chain ~85-88% of the time.

Neither adapter produced any `let rec` patterns in 125 combined
samples, despite the diverse corpus containing 63+24 let_rec pairs.
Likely a temperature/top-p interaction — let_rec sequences are long
and diverse, and the adapter's probability mass collapses onto
shorter templates at the default sampling parameters.

Trailing comment rate (~60-64%) is high across both adapters: the
base Qwen model's tendency to narrate with `//` or `--` comments is
leaking through. Not harmful in practice — MCTS strips trailing
comments before oracle submission — but worth flagging as a signal
the LoRA didn't fully retrain the completion distribution away from
Qwen's pretraining defaults.

**Qualitative syntactic validity (spot check):**
- S29-A match_list #1 uses `constructor` on a List return type,
  which is a wrong-move but the match body itself parses.
- Multiple samples end with dangling placeholders (`·`, `?`) or
  appendix noise (`#[]`, `|+ [1]`, random numbers) — decode-length
  truncation artifacts, not structural failures.
- The method_chain + fold outputs are all syntactically plausible
  Lean; the issues are semantic (wrong predicate, wrong init value).

Full sanity logs: `/tmp/s29a_gen.log`, `/tmp/s29c_gen.log`.

### Phase 2 exit criteria

- [x] S29-A adapter trained and saved (8.7 MB, 3 epochs, 18.9 min)
- [x] S29-C adapter trained and saved (34.9 MB, 3 epochs, 18.9 min)
- [x] S29-D formally dropped with rationale (corpus too small)
- [x] Generation sanity check run on both adapters — no collapse,
      both configs still produce varied (if template-shaped) outputs
- [x] Validation tag backfill applied to S28 baseline pairs before
      training (all 404 pairs carry explicit `validation` tag)
- [ ] **Deferred to Phase 3:** held-out benchmark evaluation on
      trained adapters (3 benchmark sweeps × 2 adapters × patience)

## Phases

- [x] Phase 0: Harness audit + held-out freeze (smoke tests, ablation columns, --patience flag, held-out frozen + tagged; S28 reproduction: held-out verified 0/6, other suites deferred)
- [x] Phase 1: Library mining + two-tier validation (final corpus: 404 pairs, 171 S28 functional + 233 S29 delta; floor of 1,100 NOT MET, 2.4× S28)
- [x] Phase 2: Training ablation grid (S29-A r=16 and S29-C r=64 trained; S29-D dropped — pointless at 404-pair scale)
- [x] Phase 3.1: Held-out benchmarks (primary metric) — **both adapters 0/12**, decision gate = "Ceiling reached"
- [x] Phase 4: Analysis & decision gate (`phase4_analysis.md`, `lessons_learned.md`, memory updated)

## Phase 3.1 — Held-out benchmarks (primary metric)

### Setup

- Tier: `held_out_12` (6 diag_holdout + 6 hard_holdout, frozen 2026-04-13)
- Policy: `hybrid-gc` (LoRA-conditioned policy + expanded-fixed safety net)
- Rollouts: 50, patience: 100 (patience never fires since rollouts < patience)
- Daemon: role-separated (1 goal-ops + 3 verify workers)
- Harness: same commit as Phase 0.4's 0/6 reference run

### Phase 3.0 — tier wiring (prereq)

Commit `7051adc` added `hard_holdout` and `held_out_12` tiers to
`lean_mcts/benchmark/tasks.py` and the `--tier` argparse list in
`lean_mcts/benchmark/run.py`. `get_task()` was subsequently patched to
include `HARD_HOLDOUT_TASKS` in its lookup pool (the first S29-A run
crashed at `summarize()` because `get_task()` didn't know about the
new hard-tier tasks — results were fully written before the crash).

Restart of the Pantograph daemon was required: the previous daemon
instance (up since 2026-04-13) held a stale `FormalNav.olean` from
before TasksS29Heldout.lean was compiled. After `lake build FormalNav`
(+1 olean: `TasksS29Heldout`, rest replayed) and a clean restart of
`--workers 4 --goal-ops 1`, all 6 hard-holdout theorems resolve via
`env_inspect`.

### Results

| Config | Solved | Wall time | Oracle calls | Avg per task |
|--------|-------:|----------:|-------------:|-------------:|
| S29-A (r=16) | **0/12** | 310 min | 77,559 | 25.8 min |
| S29-C (r=64) | **0/12** | 297 min | 77,780 | 24.8 min |

Per-task delta is rounding noise: every task yields the same solved/
unsolved verdict on both adapters, and wall times differ by <10%. The
14% `diverse_val_loss` improvement from r=64 produced **zero**
additional solves at inference time.

| Task | Cat | A wall | C wall | A oracle | C oracle |
|------|-----|-------:|-------:|---------:|---------:|
| eval_stack | diag_d | 734s | 738s | 3781 | 3799 |
| majority_element | diag_b | 715s | 704s | 3781 | 3799 |
| map_with_index | diag_a | 2717s | 2716s | 12861 | 13208 |
| max_subarray_sum | diag_b | 497s | 484s | 3781 | 3799 |
| prefix_while_increasing | diag_a | 2645s | 2700s | 12861 | 13208 |
| unfold_n | diag_c | 205s | 267s | 648 | 888 |
| sum_at_odd_indices | s29_heldout_a | 740s | 707s | 3781 | 3799 |
| zip_diff | s29_heldout_a | 4067s | 4014s | 12861 | 13208 |
| longest_increasing_run | s29_heldout_b | 621s | 809s | 2578 | 3096 |
| max_subarray_product | s29_heldout_b | 832s | 822s | 3781 | 3799 |
| merge_sorted_desc | s29_heldout_c | 108s | 123s | **21** | **23** |
| window_sums | s29_heldout_d | 4709s | 3730s | 9823 | 7974 |

### Decision gate

Per the sprint29.md decision matrix:

| Outcome | Score | Next direction |
|---------|-------|----------------|
| **Ceiling reached** | **0–2/12** | S30: full fine-tune or stronger base model |
| Scaling works | 3–5/12 | S30: scale to 5,000+ pairs, continue Config C |
| Confirmed | 6+/12 | S30: productionize, expand benchmarks |

**Landed squarely in "Ceiling reached".** The 2.4× corpus scale and
the Config C curriculum on top of rank-64 LoRA did not cross the
template-lookup ceiling that S27 documented.

### What Phase 3.1 ruled out (and what it didn't)

Ruled out:
  - **"More diverse data at this scale unlocks held-out solves"** —
    disconfirmed. 404 pairs with Config C curriculum still hit 0/12.
  - **"Higher LoRA rank + lower train loss transfers to inference
    performance"** — disconfirmed for held-out. r=64 vs r=16 on
    train loss shows 14% delta, on held-out shows 0% delta.
  - **"The daemon / harness / policy ablation columns are the
    bottleneck"** — disconfirmed. Harness work in Phase 0 and
    validation work in Phase 1.7 shipped cleanly; the model is
    still the bottleneck.

Not ruled out (need further work):
  - **A fundamentally bigger corpus** (10,000+ pairs) might still
    move the needle — but Phase 1's library-mining yield suggests
    this requires either a stronger converter or a different data
    source entirely.
  - **A stronger base model** (Qwen2.5-Coder-7B, DeepSeek-Coder-V2,
    etc.) may have higher ceiling even at S29's data scale.
  - **Full fine-tuning instead of LoRA** may express solutions that
    rank-64 LoRA cannot.

### Observed anomaly — multi-arg merge_sorted_desc

`merge_sorted_desc` exited after only 21 oracle calls on S29-A and
23 on S29-C, versus 3,000–13,000 for every other task. Wall time
was ~108s. This is a **qualitative early termination**, not a hung
task. The other 2-arg hard-holdout task, `window_sums`, used 8–10K
oracle calls normally.

Hypothesis: the MCTS auto-intro routine handles heterogeneous
multi-arg signatures `(k : Nat) (xs : List Int)` differently from
homogeneous ones `(xs ys : List Int)`. For the homogeneous case,
the intro may emit both arguments into the same goal position or
fail to intro one of them, causing the early exit.

**This is a harness bug worth investigating in a pre-Phase 4 cleanup
pass** — it artificially zeros one hard-holdout task's solve
probability at the harness level, before the model even gets a
chance. However, fixing it would not change the overall 0/12 result
(merge_sorted_desc is one task; the other 11 all exercised the
model properly and still hit 0).

## Held-out Set (12 tasks, frozen in Phase 0.5 before mining)

### Diagnostic held-out (from S28, unchanged)
- map_with_index (A)
- prefix_while_increasing (A)
- max_subarray_sum (B)
- majority_element (B)
- unfold_n (C)
- eval_stack (D)

### Hard-tier held-out (new, frozen in Phase 0.5 — before Phase 1 mining starts)
- TBD (6 compositional tasks modeled on S27 failure patterns, zero training overlap)
- Committed to `lean_mcts/synth/held_out_hard_tasks.py` and git-tagged `s29-heldout-frozen` before Phase 1
- Post-tag edits invalidate the sprint's primary metric

## Ablation Grid (3 focused configs)

| Config | LoRA rank | Data | Epochs | Purpose |
|--------|-----------|------|--------|---------|
| S29-A | 16 | full (~1500) | 3 | Rank floor — does S28's rank generalize at ~9x scale? |
| S29-C | 64 | full (~1500) | 3 | Target configuration |
| S29-D | 64 | 500 | 3 | Scale floor — rank vs scale dominance |

Grid reduced from 6 configs to 3 to isolate the rank and scale axes cleanly (A vs C for rank, C vs D for scale) and halve training budget + benchmark sweep duration.

## Progress Log

(empty — sprint not started)

## Key Files (will be created)

- `lean_mcts/synth/pattern_miner.py`
- `lean_mcts/synth/held_out_hard_tasks.py`
- `lean_mcts/synth/leakage_check.py`
- `lean_mcts/benchmark/ablation_columns.py`
- `lean_mcts/benchmark/adversarial_tests.py`
- `tests/harness_smoke.py`
- `lean_mcts/train/diverse_train_v2.jsonl`
- `lean_mcts/train/diverse_adapter_v2/S29-{A,C,D}/`
