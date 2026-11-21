# Lessons Learned

Running log of non-obvious findings, gotchas, and decisions made during development.

---

## 2026-02-20 — Environment Setup + extract/

### GHC Core dump goes to stdout, not stderr

`ghc -ddump-simpl` writes the Core dump to **stdout**, not stderr. This is counterintuitive because the compilation progress message (`[1 of 1] Compiling ...`) goes to stderr.

In a terminal (PTY), both streams appear mixed. When running via `subprocess.run()` without a PTY, stdout is empty unless you use `capture_output=True`. Using `-v0` suppresses the compilation progress message entirely, leaving stdout clean for the Core dump only.

```python
r = subprocess.run(['ghc', '-O0', '-ddump-simpl', '-v0', ...],
                   capture_output=True, text=True)
core_dump = r.stdout  # NOT r.stderr
```

Contrast: `> file.txt` in a shell redirects stdout but not stderr — the Core dump disappears to the file while progress goes to terminal, which looks correct but loses the dump.

### Arch Linux GHC only ships `.dyn_hi` interface files

The `extra/ghc` pacman package installs only dynamic (`.dyn_hi`) interface files for `base` and other bundled packages. Static `.hi` files are absent. Compiling without `-dynamic` fails with "Could not find module 'Prelude'".

Always use `-dynamic` when invoking GHC on Arch:

```bash
ghc -dynamic ...
```

### `-fforce-recomp` is required for reliable dump generation

Without it, GHC skips recompilation if the `.o` file is current and produces no dump output. Always pass `-fforce-recomp` when you need the dump regardless of whether source changed.

### GHC Core as-patterns appear after `of`, not inline

In `-ddump-simpl` output, case expressions with scrutinee bindings look like:

```
case expr of wild {
  pat -> body
}
```

The `wild` token appears **after** `of` and **before** `{`, not as a prefix to `expr`. Similarly `wild1`, `wild2` for nested cases. A parser must consume the optional as-pattern name between `of` and `{`.

### GHC Core `let` blocks always include a type signature

Inside `let { ... }`, GHC emits a type signature before every binding:

```
let {
  ds3 :: ([a], [a])
  ds3 = expr } in ...
```

There is **no semicolon** between the type sig line and the definition line. A parser must detect the `name :: type` pattern and skip forward to `name =` before parsing the binding body.

### GHC Core Int operations unbox through `I#`

Integer comparisons and arithmetic in `-O0` Core are expressed as:

```
case n of wild { I# n# ->
  case n# of {
    0# -> ...;
    __DEFAULT -> ...
  }
}
```

The constructor `I#` (uppercase → treated as Con), unboxed literal `0#` (HLIT token), and the GHC-internal pattern name `__DEFAULT` (treated as a regular identifier) all appear in generated code. The parser must handle all three.

### PyTorch 2.10.0 supports Python 3.14 with CUDA 12.8

Wheel availability as of 2026-02: `torch-2.10.0+cu128` has `cp314` Linux x86_64 wheels. Install via:

```bash
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
```

The CUDA 13.1 driver on the RTX 5090 is backwards-compatible with CUDA 12.8 bundles.

### Writing ~80 Haskell functions produces ~73 Core bindings after GHC filtering

Some functions generate helper definitions (e.g., `fReverse` produces a local `go`). Others are inlined or simplified to fewer definitions than written. With 72 source functions, GHC Core produced 88 top-level sigs; after filtering against `functions.txt`, 73 matched. The `go` helper from `fReverse` is captured separately as a useful training example.

---

## 2026-02-20 — model/

### PyTorch 2.10 requires explicit `attn_mask` when passing `is_causal=True` to TransformerEncoder

Passing `is_causal=True` without `mask` raises:

```
RuntimeError: Need attn_mask if specifying the is_causal hint.
```

Always pass the causal mask explicitly alongside `is_causal=True`:

```python
causal = nn.Transformer.generate_square_subsequent_mask(T, device=x.device)
self.transformer(h, mask=causal, is_causal=True)
```

### Float/bool mask type mismatch causes deprecation error in PyTorch 2.10

`generate_square_subsequent_mask` returns a float tensor (`-inf`/`0`). A bool `src_key_padding_mask` causes:

```
UserWarning: Support for mismatched src_key_padding_mask and mask is deprecated.
```

Convert the bool pad mask to float before passing:

```python
pad_mask_f = pad_mask.float().masked_fill(pad_mask, float("-inf"))
```

### `pad_mask` must be `None`-guarded — training and inference call `forward()` differently

Training passes `pad_mask=(x == PAD_ID)` to handle variable-length padded sequences. Inference calls `model(x)` without a pad mask (sequences are not padded). If `forward()` unconditionally calls `pad_mask.float()`, inference crashes with `AttributeError: 'NoneType'`. Guard with:

```python
pad_mask_f = pad_mask.float().masked_fill(pad_mask, float("-inf")) if pad_mask is not None else None
```

### Pre-norm (`norm_first=True`) with `enable_nested_tensor=False` avoids UserWarning

`nn.TransformerEncoderLayer(norm_first=True)` triggers a warning about nested tensors being disabled. Silence it explicitly:

```python
nn.TransformerEncoder(layer, num_layers=n, enable_nested_tensor=False)
```

### 73 sequences, 452k params, 20s to overfit on RTX 5090

With a 2-layer 128-dim transformer and 73 training sequences (~3600 tokens total), 2000 epochs takes ~20 seconds. Final loss 0.084 vs random baseline 5.21. The model memorizes the corpus exactly — greedy decoding from `DEF:fX` reproduces the alpha-renamed AST verbatim. This is expected and desirable at Sprint 0 scale.

---

## 2026-02-20 — corpus/

### `WhitespaceSplit` required for tokens containing punctuation

The HuggingFace `Whitespace` pre-tokenizer splits on whitespace **and** non-alphanumeric characters. Tokens like `(Lam`, `(App`, `DEF:fId`, `@T` contain `(`, `:`, `@` and get silently split into multiple pieces, breaking round-trip.

Always use `WhitespaceSplit` when building a `WordLevel` tokenizer over a hand-crafted token vocabulary:

```python
from tokenizers.pre_tokenizers import WhitespaceSplit
tokenizer.pre_tokenizer = WhitespaceSplit()
```

### Type strings in TyArg nodes must have spaces stripped before serialization

GHC Core type annotations can contain spaces (e.g. `( a , b )` for tuple types). If emitted verbatim, the whitespace tokenizer splits them across multiple tokens, breaking round-trip and inflating vocabulary.

Strip spaces from type strings at serialization time:

```python
tokens.append(node["type"].replace(" ", ""))  # (a , b) → (a,b)
```

### Alpha-renaming counter should be global per function, not per scope

An intuitive approach is to reset the counter at each scope boundary. However, global-per-function counters (`v0`, `v1`, ... monotonically increasing) are strictly better for training: they eliminate the accidental variable-name collision that occurs when two unrelated binders in sibling scopes both get `v0`, which would look like aliasing to the model.

### S-expression prefix serialization gives ~49 tokens average for real Haskell functions

At `-O0` Core depth, functions like `fMap`, `fFilter`, `fFoldr` serialize to 60–90 tokens. Simple combinators (`fId`, `fConst`) serialize to 8–15 tokens. Max observed: 153 tokens (`fSplitAt`). All fit comfortably in a 256-token context window for the model.

### Recursive descent is practical for GHC Core at Sprint 0 scale

A tokenizer + recursive descent parser on the text dump is ~220 lines of Python and achieves 100% parse rate on `-O0` Core output. The key design choices that made this tractable:

- Types are captured as opaque strings (not parsed into a type AST) — deferred to `typecheck/`
- Errors fall back to `{"ok": false, "error": "..."}` rather than crashing
- A small set of token kinds (~15) covers all syntactic distinctions needed

---

## 2026-02-20 — typecheck/

### TyArg args must be skipped in App type inference

In GHC Core, `(App Con [] TyArg a)` is type application — `[] @a` — not term application. The term-level type checker must skip `TyArg` nodes when iterating `App.args`. Without this, `Con []` (type `[Unknown]`) appears to be applied to `a`, causing a spurious "applying non-function" error. 73/73 sequences pass after adding `if arg.get('tag') == 'TyArg': continue`.

### BINDER_TOKENS and EXPR_STARTERS are disjoint → grammar is unambiguous

Inside `(Lam binders... body)`, binders (`@T`, `v0`–`v13`, `_`, `wild`) and expression starters (`Var`, `Con`, `(App`, `(Lam`, ...) share no tokens. The recursive descent parser can greedily consume binder tokens, then parse the body when it sees an EXPR_STARTER. No lookahead or backtracking needed.

### Structural validity = "parse partial + next and catch ParseError"

Rather than building a state machine to enumerate valid next tokens, `is_type_valid(partial, next)` runs the recursive descent parser on `partial + [next]` and returns `True` iff no ParseError. Partial inputs are handled by returning early when `ts.done()`. This is O(n) per token check and O(n·V) per generation step (~100·183 ≈ 18K ops) — fast in Python, clean to implement.

---

## 2026-02-20 — benchmark/

### `v0`, `v1`, etc. are valid Haskell identifiers — no renaming needed

The alpha-renamed variables (`v0`, `v1`, ...) from the corpus are valid Haskell identifiers (letter followed by alphanumeric). Pretty-printing them verbatim to Haskell requires no translation step. The only binders that need rewriting are `wild` and `wild1` (GHC internal names for wildcard as-patterns), which map to `_`.

### TyArg erasure is the key step in pretty-printing

The serialized AST includes type-application nodes (`TyArg Int`, `TyArg a`) that don't appear in term-level Haskell. Dropping these in the pretty-printer is sufficient to produce valid Haskell — GHC infers the types. The same applies to `$fXxx` typeclass dictionary arguments: they are internal to GHC Core and absent from source Haskell.

### `Con I#` is transparent in source Haskell

GHC Core boxes/unboxes `Int` via `I# :: Int# -> Int`. In `-O0` Core, `I# 1#` appears everywhere a literal `1` appears in source. When pretty-printing, unwrapping `App (Con I#) [arg]` to just `arg` (and `Lit "0#"` → `0`) produces correct Haskell. The `I#` constructor does not exist in surface Haskell and must not be emitted.

### Rename recursive self-calls, not just the top-level name

The corpus serializes functions under their original names (e.g., `Var fSum` for a recursive call inside `fSum`). The benchmark renames everything to `f`. Failing to also rewrite `Var fSum` → `f` inside the body produces a name-error at compile time (`fSum` is not in scope). The fix: pass `func_name` through the pretty-printer and rewrite on every `Var` node whose name matches.

### Operators in GHC Core appear as `Var` nodes, not special syntax

Binary operators like `+`, `-`, `*`, `<=` are represented as `Var "+"` in the AST (not as infix expressions). The pretty-printer must detect when a `Var` in function position is an operator and emit it infix: `l + r` rather than `(+) l r`. Without this check, GHC rejects the output as a parse error.

### Greedy decode (temp=0.0) reproduces exact corpus ASTs → 100% pass rate

Because the model has memorized the 73-sequence corpus, greedy decoding from any `DEF:fX` prompt exactly reproduces the training AST. With a correct pretty-printer, this gives 100% pass on all benchmark tasks without any RL. This is expected at Sprint 0 scale — the interesting signal will come from temperature>0 sampling in the RL loop, where the model must generalize to novel (but valid) programs.

---

## 2026-02-20 — rl/

### RL on a fully-converged model produces negative results — use a weak baseline

The first RL experiment ran on the 2000-epoch checkpoint (loss=0.084, greedy=100%) at temperature=2.5. Pass@1 dropped from 0.554 to 0.438 after fine-tuning. Root causes:

1. **Identical winners**: at any temperature, the model generates one correct program per task (it memorized exactly one). All 50 "winners" for `identity` are the same token sequence. Fine-tuning on 42 identical copies of `fId` vs 5 copies of `fFilter` creates a severely imbalanced gradient.
2. **Fine-tune loss > pre-train loss**: the 2000-epoch model had loss=0.084. Fine-tuning only reached 0.107 — the model was already so calibrated that any perturbation degraded it.
3. **No new signal**: winners from a memorized model are just the training data. The RL loop has nothing to learn.

**Fix**: run RL on a model that hasn't converged. A 50-epoch checkpoint (loss=0.515, pass@1≈10%) provides a real gradient signal.

### Winner deduplication + per-task cap is essential for balanced fine-tuning

Without deduplication, easy tasks (e.g., `identity` with 55/100 pass) contribute 55x more gradient than hard tasks (e.g., `filter_positive` with 1/100 pass). The model becomes better at easy tasks and worse at hard ones — net pass@k decreases.

Fixes:

- **Deduplicate**: convert each winner list to unique token sequences (typically 1-3 unique per task even with k=100)
- **Cap per task** (`--max-per-task`): prevent any single task from dominating even if it produces many unique programs

With deduplication + cap=20: 13 total unique winners across 8 tasks (2 tasks had 0 winners at k=100).

### 13 winners + 73 corpus sequences → +0.902 pass@1 improvement in 3 seconds

Fine-tuning for 200 epochs on 13 winner sequences mixed with 73 corpus sequences, lr=5e-4, took ~3 seconds on RTX 5090. Result: pass@1 0.098 → 1.000, tasks passing 5/10 → 10/10.

The key: corpus mixing prevents catastrophic forgetting. Without it, fine-tuning on 13 sequences would cause the model to forget the other 60 corpus functions. With mixing, the model reinforces both winners and the full corpus, converging to a better local optimum.

### pass@k (unbiased) vs pass@1 serve different purposes

- **pass@1** (mean fraction of candidates that pass) measures the model's expected success rate per sample — directly relevant to how useful the model is in practice.
- **pass@k** (probability that ≥1 of k passes) measures whether the model _can_ solve a task at all — useful for checking if a task is reachable.

At Sprint 0 scale, pass@k often hits 1.000 for tasks the model knows, while pass@1 varies widely with temperature. The RL loop should optimize pass@1 (sample efficiency), not just pass@k.

### Temperature choice for RL experiments

- **temp=0.0**: deterministic, always correct for memorized tasks → nothing to improve
- **temp=0.8**: mostly correct, minor variation → pass@1 ≈ 100% for a well-trained model; not useful for RL on a perfect model
- **temp=2.5**: significant noise → reveals real failure modes in a strong model, but produces few winners for a weak model
- **temp=0.8 on a weak model (loss≈0.5)**: the sweet spot — enough variation to fail, enough structure to find winners with k=100
