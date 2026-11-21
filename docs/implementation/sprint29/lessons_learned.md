# Lessons Learned — Sprint 29

Running log of non-obvious findings, gotchas, and decisions made during
the diversity-scaling sprint. Covers 2026-04-12 through 2026-04-14.

---

## Phase 0 — Harness audit

### "Daemon corruption" was two distinct bugs, neither intermittent

S28 memory recorded that "daemon corruption after long benchmark runs"
was mitigated by restarting the daemon between sweeps. The symptom was
that successive benchmarks would inexplicably produce different results,
or workers would return empty `env_inspect` replies. The folk theory was
that Pantograph workers "drift" under load.

Phase 0 root-caused this into two concrete bugs, both deterministic:

1. **Broken recovery on `ServerError`.** PyPantograph's `run_async`
   catches internal timeouts and decode errors by calling `self._close()`
   (which kills the Lean subprocess and sets `self.proc = None`) and
   then raises `ServerError`. The daemon's generic exception handler
   caught `ServerError` as "soft error, report and continue" — without
   re-creating the server. Every subsequent request on that worker
   hit `assert self.proc, "Server not running."` until the worker
   was externally restarted. The "daemon corruption" was a single
   tactic timing out on a single task; every request thereafter
   returned an error.
   
   Fix: detect `self.server.proc is None` inside the generic handler
   and call `_create_server()` before retrying.

2. **Response misrouting via shared stdin/stdout.** Multiple concurrent
   clients connected to the same worker over its Unix socket. The
   daemon's async handler forwarded each client's request to
   `self.server.X_async`, which reads/writes the Lean subprocess's
   stdin/stdout. Asyncio concurrency meant client A's request and
   client B's request could interleave their reads — client A
   would receive the header of its own reply followed by the body
   of client B's reply.
   
   Fix: add a per-worker `asyncio.Lock` that serializes every
   `self.server.X_async` call for that worker. Concurrent clients
   now queue at the worker boundary instead of racing inside it.

**Lesson**: "random corruption" folklore almost always decomposes into
deterministic race conditions under sufficient logging. Worth the
investigation time every sprint where it keeps coming back.

### The nested FormalNav git repo was dead weight for 12 sprints

`lean_mcts/env/FormalNav` was a nested git repo, meaning every Lean
file edit required `cd FormalNav && git add && git commit` separately.
This is how the original sprint setup wired LeanDojo's `--backend dojo`
fallback, which requires a clean git worktree at the Lean project root.

S29 non-goal #5 explicitly excludes `--backend dojo`, so the nested
git repo was load-bearing for nothing. Phase 0.4 flattened it (removed
inner `.git`, tracked files in the outer repo) with a backup tarball
as escape hatch. Single-repo diff workflow now works for Lean tasks.

**Lesson**: whenever a workflow wart persists for 10+ sprints, ask
what it's actually preserving. The answer is often "a capability
nobody uses anymore".

### `FormalNav.lean` transitive imports, not `ALL_IMPORTS`, drive what's visible

The daemon's `ALL_IMPORTS` list in `pantograph_daemon.py` names the
Lean modules to eager-import at worker startup. The list was missing
`TasksDiagnostic` and `TasksS29Heldout`, but Phase 0.4 still ran
against them successfully — because `FormalNav.lean` (the root module
named first in `ALL_IMPORTS`) imports them transitively.

When I added `TasksS29Heldout` and ran `env_inspect sumAtOddIndices`
on a live daemon, it failed — but not because of `ALL_IMPORTS`. It
failed because the `FormalNav.olean` compiled artifact was older
than `FormalNav.lean`'s newest import, and the daemon held a stale
environment. `lake build FormalNav` rebuilt the single missing
olean in ~1 second, and a daemon restart picked up the fresh env.

**Lesson**: the Lake/olean cache is per-subprocess. Edits to a root
`.lean` file propagate only after a rebuild AND a subprocess restart.
Don't try to hotpatch a running Pantograph worker.

---

## Phase 1 — Library mining

### Regex-based pattern mining ran into diminishing returns fast

Phase 1.2's `pattern_miner.py` scanned 296 .lean files across stdlib /
Batteries / Mathlib for 10 S27-failure-category regex patterns
(tuple_acc_fold, state_machine_fold, recursive_helper_acc, let_rec,
multi_arm_match, two_pass, ...). It found 84 matching definitions,
converted 52, and Pantograph verified 4. Of those 4, all 4 were
already in `library_pairs_v2.jsonl`.

**Zero net new pairs.**

Root cause: the source surface overlaps. `extract_lean_defs.py` already
walks every file where these patterns live. The additional Mathlib
`Logic/` and `Control/` directories that `pattern_miner` scans are
theorem-dense rather than definition-dense; the few defs there use
heavy Mathlib typeclass machinery (`Monoid`, `LinearOrder`, `motive`
dependent matching, `PUnit.{u+1}` universe polymorphism) that the
regex specializer can't peel.

**Lesson**: before writing a pattern-targeted mining tool, check
whether your existing extractor already visits the candidate files.
If it does, the new tool won't find new pairs — it'll just rediscover
the ones you already have or fail on the same typeclass walls.

### Converter bug disguised as extraction ceiling

After Phase 1.1's scan hit 225 verified pairs, it looked like we'd
hit a fundamental "converter can't handle X" wall. Two unrelated
bugs in `lean_def_converter.py` were actually responsible for ~66
rejected pairs:

1. **Whitespace collapse in `_convert_simple`.** The S28
   implementation did `' '.join(body.split())`, collapsing multi-line
   bodies to a single space-joined line. Definitions like `sublists'`
   have a term-level `let`-chain separator that requires newlines or
   semicolons, and the collapsed single-line form was ill-formed Lean.
   
   Fix: `_simple_candidate_forms` emits three candidates (newline-
   preserved, semicolon-joined, space-joined) and the verifier tries
   each in turn.

2. **Doc-comment bleed-through in `parse_definitions`.** The block-
   boundary detector in `_find_def_blocks` didn't treat `/-- ... -/`
   as a boundary. A `def` followed by a doc comment (for the NEXT
   def) kept slurping the doc-comment text into the preceding def's
   body until the next `def`/`theorem` keyword. `sublists'` ended up
   with a 309-char body that contained `/-- List.sublists ... -/`
   from the following def.
   
   Fix: strip file-level doc comments before block detection.

Yield: **225 → 291 pairs (+29%)** from these two fixes alone.

**Lesson**: when the rejection log has a suspicious cluster of
failures on structurally-similar defs (all `simple_exact` kind, all
with `/--` preceding), investigate the parser before investigating
the type theory. Parsing bugs masquerade as "fundamental limitations"
cheaply.

### Two-tier validation (functional + library-trust syntactic) was the largest single yield lever

Phase 1.7 introduced a second validation tier: if a library pair's
tactic body fails the strict `goal_start → intro → exact body` check
for anything other than a pure parse error, accept it anyway, tag
it `validation: "syntactic"`, and rebuild the state from the
universe-normalized real type via `env_inspect`.

This is justified because library definitions are semantically
trusted — they compile in their native Lean library context. If our
Pantograph instance rejects them, the rejection is almost always a
dependency or namespace issue in our instance, not a real correctness
bug.

**Yield: 295 → 466 pairs (+171 syntactic, 58% of the final corpus).**

Before landing on this, I tried and rejected three other approaches:
- **Type-ascription echo** `((fun ... => body) : spec_type)` via
  `expr.echo` — empirically equivalent to the functional check
  (18 passed both, 0 rescued, 23 failed both). Rejected.
- **Metavariable-relaxed `expr.echo`** — accept bodies where the
  inferred type contains `?m.` metavars. Rescued 1/50 sampled pairs.
  Rejected.
- **Polymorphic-type direct verification** — use `{α : Type}` as the
  goal without universe stripping. Hit unrelated bugs (transitive
  deps, EQCOMP arg name mismatches). Deferred to S30.

**Lesson**: when a verifier over-rejects, consider whether the
rejected items were ever *meant* to be rejected. If the source is
trusted (library, tested, compiled), "didn't verify in our sandbox"
may mean "our sandbox doesn't capture the real context", not "the
code is wrong".

### Specializer broadening produced −1 pairs (literally worse)

Phase 1.2's specializer generalization added:
- Unicode types (ℕ → Nat, ℤ → Int)
- Extended Greek type vars (η, κ, σ, τ)
- Monad/functor type vars (m, m₁, F → Id)
- Algorithmic auto-instance check replacing a hardcoded AUTO_INSTANCES set

Result: **224 vs 225 pairs**. A small regression.

The remaining failures are all things the regex approach fundamentally
cannot handle: `Id PUnit.{u + 1}` (universe-polymorphic), `(l₁ l₂ :
List Int) → C l₁ l₂` (free motive `C`), `0 < l.length → Int`
(dependent refinement).

**Lesson**: generalization without a grounded hypothesis about WHERE
the missed cases live is a bet that rarely pays off. The specializer
broadening is algorithmically cleaner and would probably help on a
different corpus — but on the current one it added complexity with
negative yield. Shipped anyway for architectural reasons, but flagged
as a cautionary tale.

---

## Phase 2 — Training

### Rank hikes fit the training data better without generalizing better

S29-A (r=16) vs S29-C (r=64) on the same 404-pair corpus:

| Config | r | α | diverse_val @ep3 | template_val @ep3 | train loss | held_out_12 |
|--------|---|---|-----------------:|------------------:|-----------:|:-----------:|
| S29-A | 16 | 32  | 0.974 | 0.334 | 0.458 | **0/12** |
| S29-C | 64 | 128 | **0.841** | 0.336 | 0.417 | **0/12** |

S29-C fits the training distribution 14% better. It also has a 4×
larger adapter file (35 MB vs 9 MB). And it solves **zero** additional
benchmark tasks, with per-task wall times differing by <10% and
per-task oracle counts differing by <3%.

**Lesson**: at this corpus scale (404 pairs), rank is expressivity
headroom the training signal can't actually use. Higher rank fits
more precisely without generalizing better. Before S30 bets on
"rank-128 will break the ceiling", note that S29's evidence is
strongly against that hypothesis.

### Training loss is orthogonal to held-out solve rate at this scale

A 14% improvement on `diverse_val_loss` from 0.97 → 0.84 is
meaningful on the training distribution — the model is genuinely
better at producing tokens that match the held-out diverse val set.
And this improvement expresses itself as **zero** additional
held-out solves, identical per-task verdicts, and FEWER structural
generations in the sanity check (S29-C produced 0 match patterns,
S29-A produced 2).

The only way to reconcile this: the `diverse_val_loss` improvement
is measuring overfitting to the training distribution, not improving
the model's capacity for compositional generalization. That fit
happens on tokens that are also in the test-held-out val set, not
on the task-held-out benchmark set, because the two held-outs measure
completely different things.

**Lesson**: never trust a training-distribution val loss as a proxy
for a task-held-out metric, especially at small data scale. They
can move in opposite directions. Run the cheap task-held-out
sanity check (125 samples in 2 min wall) before committing to a
benchmark sweep.

### 125-sample generation sanity check is a 40× faster adapter-quality proxy

Phase 2.3 ran `inspect_generations.py` on both trained adapters:
8 held-out-bucket tasks × 8 samples at temperature 0.8 = 64 candidates
per adapter, ~2 min wall each.

Results:
- S29-A: 42% method_chain, 41% fold, 2 match, 1 have
- S29-C: 49% method_chain, 39% fold, 0 match, 1 have
- Neither produced any `let rec` in 125 combined samples, despite
  63+24 `let_rec` pairs in the training corpus

Every hard-holdout task needs at least one of `let rec`, `match`
on multi-arg, or multi-step `have ... exact` — per the reference
implementations in `held_out_hard_tasks.py`. The sanity check
predicted 0/6 on hard-holdout, and the 9-hour benchmark sweep
confirmed it.

**If I had trusted the sanity check, I could have cancelled the
Phase 3.1 sweep and reported the result directly.** 40× faster
iteration on adapter quality than the benchmark.

**Lesson**: S30 should use the sanity check as a first-pass triage.
Only commit to a benchmark sweep if the sanity check produces
non-zero rates of the structural patterns the target tasks need.

---

## Phase 3 — Benchmarks

### `get_task()` quietly fell behind tier additions

Adding `hard_holdout` and `held_out_12` tiers to `get_tasks()` made
the new task lists loadable for benchmark setup. But `get_task()`
(single-task lookup by name, used by `run.py:summarize()`) looked
up against a hardcoded pool that didn't include `HARD_HOLDOUT_TASKS`.
S29-A's benchmark ran all 12 tasks and wrote the result JSON
successfully, then crashed at the summarize step with `KeyError:
"Unknown task: sum_at_odd_indices"`.

The results were recoverable (JSON was already written), but exit
code 1 and a stack trace is a worse user experience than a clean
summary.

**Lesson**: when adding a new task tier, update both `get_tasks`
(for collection-level operations) AND `get_task` (for individual
lookups), which live side by side in the same file. Grep for
"HARD_HOLDOUT_TASKS" or similar pattern names before committing a
tier addition.

### Multi-arg auto-intro behaves differently for homogeneous signatures

`merge_sorted_desc : (xs : List Int) → (ys : List Int) → List Int`
and `window_sums : (k : Nat) → (xs : List Int) → List Int` have
the same arity. But in Phase 3.1 on both adapters:

- `merge_sorted_desc`: **21 oracle calls**, 108s wall
- `window_sums`: ~10K oracle calls, ~75 min wall

The `window_sums` task ran to a normal MCTS termination (rollout budget
exhausted). The `merge_sorted_desc` task ran out of legal moves within
a handful of expansions and terminated early. This is a qualitative
harness bug, not a model bug — the model never got a real chance to
produce a solution.

Hypothesis: the MCTS auto-intro routine handles heterogeneous multi-arg
signatures `(Nat, List)` differently from homogeneous ones `(List, List)`.
For the homogeneous case, the intro may fail to introduce one of the
arguments, leaving the goal state malformed for downstream tactic
generation.

**Impact on S29**: none. The other 11 tasks exercised the model
properly and still scored 0 solves. But for S30 this is a free
"rescue" of one hard-holdout task — the auto-intro fix doesn't
require any model changes, and it gives the model a fair shot at
`merge_sorted_desc`.

**Lesson**: before running a new benchmark tier, instrument the
setup phase to log what state was built. If one task's setup fails
silently, the entire task's "score" is an artifact of the harness.

### The held-out benchmark took 5h, not 4h

Phase 0.4 timed diag_holdout (6 tasks) at ~2h 6min wall with
`hybrid-gc` + the S28 C_curriculum adapter. I extrapolated 12 tasks
at ~4.2h.

Actual: 5h 10min for S29-A on `held_out_12`. 24% longer than
extrapolation.

Why: the hard-holdout tasks (zip_diff, max_subarray_product,
window_sums) took ~60–80 minutes each, substantially longer than
the diagnostic set's per-task mean. The 1-arg hard-tier tasks
weren't harder computationally — they just had deeper tactic trees
because the diverse adapter generated more varied candidates.

**Lesson**: when extrapolating sweep times across tiers, use the
slowest task's time as the upper-bound estimate, not the average.
Hard-tier tasks with longer tree depths disproportionately blow out
the total.

---

## General observations

### The null result has a signature

Both adapters scored 0/12 with **nearly identical per-task** oracle
counts (within 3% for most tasks) and wall times (within 10%). When
a null result looks like two independent coin-flip trials agreeing
almost exactly, you're not looking at "maybe next time" — you're
looking at "this architecture's response surface to the input
distribution does not contain any solutions for these tasks".

**Lesson**: if your ablation pair produces nearly-identical null
results, you don't need a third config to confirm. You need a
fundamentally different experiment. S29's third config (S29-D, the
scale floor) was going to give the same null result had it been
runnable, and its deletion didn't weaken the sprint.

### Phase-by-phase commit discipline made the null result unambiguous

Every Phase shipped its own commit with a headline in the commit
message (`sprint 29 phase 3.1: held-out benchmarks — primary metric
null (0/12)`). The sprint29_status.md doc was updated phase-by-phase
as work landed, not as a final-day writeup.

When Phase 4 sat down to write this analysis, the git log was the
narrative. No "wait, what was the exact version of the adapter
we benchmarked?" or "did that config get the val loss fix?" — the
commits carry the answers.

**Lesson**: phase-by-phase commits with headline messages pay off
disproportionately in null-result sprints. The negative result is
believable because the trail is visible.
