# Sprint 15 — Lessons Learned

## 1. The bottleneck was NOT the REPL

**Assumption**: REPL tactic evaluation (env.step) dominates wall time → migrate to faster backend.

**Reality**: Oracle subprocess (`lake env lean`) is 91-100% of wall time at ~2.1s/call. REPL step is 1-3ms/call (0.1-6% of wall time). The sprint plan's entire motivation was based on an incorrect assumption about where time was spent.

**Fix**: Parallel oracle calls (ThreadPoolExecutor) delivered 3.3x speedup — more impactful than the entire Pantograph migration for raw speed.

**Lesson**: Always instrument before optimizing. Phase 0 should be mandatory, not optional.

## 2. Wrong PyPI package for Pantograph

`pip install pantograph` installs a **drawing library** (pantograph==0.1.0), not the Lean 4 interface. The correct package is from Stanford's GitHub:
```bash
uv pip install "git+https://github.com/stanford-centaur/PyPantograph"
```

This cost ~20 minutes of confusion. PyPantograph is not on PyPI as of 2026-03.

## 3. Pantograph async API design

All PyPantograph methods are async-only (`Server.create` is `async`). The pattern for sync wrappers:

```python
def _ensure_loop(self):
    if self._loop is None or self._loop.is_closed():
        self._loop = asyncio.new_event_loop()
    return self._loop

def _run(self, coro):
    return self._ensure_loop().run_until_complete(coro)
```

Do NOT use `asyncio.run()` in a wrapper class — it creates and closes a loop each time, conflicting with the persistent server connection.

## 4. goal_start gives un-introduced state

Pantograph's `goal_start(type_expr)` returns state like `⊢ List ℤ → ℕ` (function type, no variables in context). DirectDojo starts inside `def foo (xs : List Int) : Nat := by sorry`, so `xs` is already introduced.

Policies expect the introduced form (`xs : List Int ⊢ Nat`). Solution: parse the Lean source file for parameter names, then auto-intro:
```python
param_names = self._extract_param_names()  # regex on .lean file
state = self._run(self._server.goal_tactic_async(state, tactic=f"intro {' '.join(param_names)}"))
```

Don't use `intros` (unnamed) — it generates `a✝` names that confuse policies.

## 5. env_inspect gives theorem type from compiled environment

To get a theorem's type without parsing .lean files:
```python
info = self._run(self._server.env_inspect_async(name=theorem_name))
theorem_type = info["type"]["pp"]  # pretty-printed type string
```

This is more reliable than regex-parsing .lean files for the type.

## 6. TacticFailure import location

`TacticFailure` is in `pantograph.server`, NOT `pantograph.expr`. The expr module has `GoalState`, `Goal`, etc.

## 7. Oracle file conflicts under parallelism

The oracle writes to a fixed path `_Eval_{theorem_name}.lean`. With parallel oracle calls (ThreadPoolExecutor), multiple threads write to the same file simultaneously.

Fix: thread-safe counter for unique filenames:
```python
_eval_counter = 0
_eval_lock = threading.Lock()

def _next_eval_id():
    global _eval_counter
    with _eval_lock:
        _eval_counter += 1
        return _eval_counter
```

## 8. Batch oracle verification architecture

The original MCTS expand() interleaved type-checking and oracle calls:
```
for each candidate:
    env.step()     # 1ms
    if solved: oracle()  # 2000ms, sequential
```

Better architecture separates phases:
```
Phase 1: type-check ALL candidates (fast, 1ms each, sequential)
Phase 2: oracle-verify ALL solved candidates (slow, 2s each, PARALLEL)
```

This is a natural parallelism boundary — Phase 1 must be sequential (shares REPL state), Phase 2 is embarrassingly parallel (independent subprocess calls).

## 9. Hard task failures are policy, not infrastructure

With Pantograph: 0/5 hard tasks solved, but 0 crashes. Under DirectDojo: 0/5 solved with 4-5 crashes.

The failure mode changed from "infrastructure crash" to "policy doesn't have the right tactics." This is progress — the problem is now tractable (enumerate more expressions) rather than structural (fix REPL infrastructure).

Specifically:
- v2_multi_type (0/5): oracle hardcodes `(xs : List Int)`, can't handle `(xss : List (List Int))`
- Hard tasks with FixedPolicy: 56 tactics simply don't include `List.zipWith`, `List.flatten`, etc.

## 10. Lean 4.27.0 + Mathlib compatibility

PyPantograph 0.3.13 targets Lean 4.27.0. To find the right Mathlib commit:
```bash
git log --oneline lean-toolchain | head -5  # find when toolchain changed
# Then find the last commit BEFORE the next toolchain bump
```

Mathlib rev `216257fb6e` is the last commit on Lean 4.27.0. Use `lake exe cache get` after updating to avoid 30+ minute rebuilds.

## 12. Oracle signature was silently wrong for 5+ tasks

The oracle `_build_eval_lean` hardcoded `(xs : List Int)` for ALL tasks. This meant:
- `concat_all` (`xss : List (List Int)`) → type mismatch on test inputs
- `merge_sorted` (`xs ys : List Int`) → `ys` undefined in eval file
- All 5 v2_multi_type tasks showed as "FAILED" but were actually **oracle bugs, not policy failures**

Fixing the oracle to use `task.input_type` and detect two-arg tasks (via `input2` in test cases) immediately unlocked 5/5 multi_type tasks: **V2 jumped from 11/20 to 16/20**.

Lesson: when a category shows 0% solve rate, check the evaluation pipeline before blaming the model.

## 11. Event loop cleanup warnings are cosmetic

When PantographEnv instances are garbage collected, Python emits:
```
RuntimeError: Event loop is closed
```

This happens because asyncio subprocess transports try to close pipes on an already-closed loop. It's cosmetic — no data loss, no functional impact. Suppress with `2>/dev/null` in benchmarks.

## 13. Deep search helps product types but not expression gaps

Cat C deep search (500 rollouts) on the 3 unsolved v2_multi_step tasks:
- **partition_and_count** (Nat × Nat): SOLVED at rollout 457. Deep search found `constructor` → two sub-goals, then solved each. This is a rollout budget problem — 50 wasn't enough, 500 was.
- **stats** (Int × Int): FAILED. 514 oracle calls, 0 pass. Candidates type-check but don't compute min/max. Expression space has no `List.minimum?`/`List.maximum?`.
- **split_apply** (List Int × List Int): FAILED. 0 oracle calls. 116K tactic steps, 15K valid, 0 solved. `List.map (· * 2)` not in expression space — no tactic path reaches `ProofFinished`.

**Lesson**: More rollouts help when the solution *exists* in the expression space but requires depth (product types via `constructor`). They don't help when the required combinators are simply absent. The diagnostic: if oracle is called many times with 0 passes → expression space has the shape but wrong semantics. If oracle is never called → expression space fundamentally lacks the building blocks.

## 14. Warm oracle via check_compile

Pantograph's `check_compile` API processes `#eval` statements through the warm server:
```python
result = await server.check_compile_async(code_with_def_and_evals)
# Returns CompilationUnit with Message objects
# Severity.INFORMATION = #eval output, Severity.ERROR = compile failure
```

Key findings:
- `check_compile` does NOT persist definitions in the server environment (tested: `env_inspect` fails after `check_compile`). Safe to use alongside `goal_tactic`.
- Per-call: ~375ms for 3 test cases vs ~2100ms cold subprocess = **5.6x faster per call**
- But cold parallel (8 threads) amortizes to ~320ms, making warm sequential (~375ms) slightly slower for high-candidate tasks (35+ oracle calls).
- Best use case: tasks with few candidates (<10 oracle calls), debugging, or environments where subprocess spawning is expensive.
- Piping benchmark output through `| grep` causes SIGPIPE → broken pipe crashes. Always redirect to a file or run without pipes.

## 15. check_compile inherits server imports

`check_compile` inherits the Mathlib environment from `Server.create(imports=[...])` — no need to add `import Mathlib.Tactic` in the code snippet. Only `set_option linter.unusedVariables false` is needed to suppress warnings.
