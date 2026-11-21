# Lessons Learned — Sprint 5

Running log of non-obvious findings, gotchas, and decisions made during development.

---

## 2026-02-23 — Type Normalization (Phase 1)

### Regex `-` in character classes is a range operator unless placed at start or end

When writing the type tokenizer regex, the first attempt was:

```python
_TYPE_TOKEN_RE = re.compile(r"->|[()[\]]|[^\s()\[\]->]+")
```

Python 3.14 raised `PatternError: bad character range \]-> at position 19`. Inside the negated class `[^\s()\[\]->]`, the substring `\]->` is parsed as: `\]` (escaped `]`) then `-` then `>`, which forms a range from `]` (ASCII 93) to `>` (ASCII 62). Since 93 > 62, it's an invalid range.

The fix: use `[^\s()\[\]]+` (no `>` or `-` in the class). The third alternative captures any run of non-whitespace non-bracket characters, which is everything except arrows and parens. The `->` alternative comes first and handles the arrow case.

**Rule**: put `-` at the very start or end of a character class (`[-...]` or `[...-]`) if you want a literal dash. Any `-` between two characters is a range operator. When in doubt, rearrange the class so `-` is in a position where it can only be literal.

### `\S+` greedily swallows brackets — use `[^\s()\[\]]+` instead

The second attempt used `\S+` as the catch-all:

```python
_TYPE_TOKEN_RE = re.compile(r"->|[()\[\]]|\S+")
```

This tokenized `[a]` as `['[', 'a]']` instead of `['[', 'a', ']']`. The reason: `\S+` is greedy and matches `a]` as a single token because `]` is not whitespace. The character class alternative `[()\[\]]` never gets a chance to match the `]` because `\S+` consumed it first.

Fix: replace `\S+` with `[^\s()\[\]]+` — a class that explicitly excludes all bracket characters. This forces the tokenizer to break tokens at every bracket boundary.

**Rule**: when writing tokenizers that split on specific characters, the "other" alternative must explicitly exclude those split characters. `\S+` is only safe when no split characters are non-whitespace.

### Higher-kinded type variable canonicalization: `m` is a variable, not a constructor

In `forall (m :: * -> *) a. Monad m => m a -> m a`, after stripping the `forall` and the `Monad m =>` constraint, the remaining type is `m a -> m a`. The canonicalizer maps `m` → `a` and `a` → `b`, producing `['a', 'b', '->', 'a', 'b']`.

Initial test expectation was wrong (`['m', 'a', '->', 'm', 'a']`) because I assumed `m` was special. It's not — `m` is a lowercase identifier that is not a type constructor, so it canonicalizes to `a` just like any other type variable.

The `_TYPE_CONSTRUCTORS` frozenset uses `re.fullmatch(r"[a-z]\w*", tok)` to identify type variables: any lowercase-starting identifier that is not in the constructor set becomes a canonical variable. `m` satisfies this — it is lowercase, not in `_TYPE_CONSTRUCTORS`.

**Rule**: all lowercase identifiers in type signatures are type variables, including single-letter variables used for higher-kinded types (`m`, `f`, `t`). Only uppercase-starting identifiers are constructors.

### Forall stripping must run in a loop, not once

The initial implementation stripped only a single `forall ... .` prefix. GHC Core can emit types with nested foralls or multiple quantifier groups in some contexts. The fix uses a `while s.startswith("forall"):` loop that strips one quantifier at a time until none remain.

In practice, GHC types in the hand-written and Hackage corpora appear to use at most one `forall`, but the loop is cheap insurance.

### Type normalization is lossy in an acceptable way

Stripping `forall` quantifiers and class constraints drops information:

- `forall a. Ord a => [a] -> [a]` and `forall a. Eq a => [a] -> [a]` both normalize to `[a] -> [a]`
- `forall a. Num a => a -> a` and `forall a. Floating a => a -> a` both normalize to `a -> a`

Functions with different class constraints but the same structural type are indistinguishable to the model. The sprint design accepts this: class constraints are runtime dispatch (how the dictionary is passed), not structural shape (what the function does). Two functions of type `[a] -> [a]` — one requiring `Ord` (sort) and one requiring `Eq` (nub) — genuinely have similar structure from the model's perspective. Context disambiguation (Phase 2) handles the distinction.

**The acceptable loss**: for types where the constraint is the only distinguishing feature, the model must rely on structured context to disambiguate. For most types (3,078 unique simplified types, median ~1 function per type), the type alone suffices.

---

## 2026-02-23 — Tokenizer Rebuild (Phase 1)

### min_freq defaults must be kept in sync between build_tokenizer() and main()

`tokenizer.py` has two places where `min_freq=25` appeared:

1. `def build_tokenizer(sequences, min_frequency: int = 25)` — the function default
2. `p.add_argument("--min-freq", ..., default=25)` — the CLI default

Changing only one leaves them inconsistent. The CLI calls `build_tokenizer(sequences, min_frequency=args.min_freq)`, so the argparse default controls what actually runs. Changing only `build_tokenizer`'s default leaves the CLI at 25 — the change is invisible unless the function is called programmatically without arguments.

**Rule**: when a function has a default parameter and a CLI wrapper also has a default, they are two separate defaults. Change both together or extract the default to a single shared constant.

### Vocabulary size is controlled by tokenizer, not by normalize

Running normalize.py on the full 4,438-function corpus produces a raw vocabulary of 1,693 tokens. The tokenizer's min_freq filter reduces this to 173. The addition of Sprint 5 type tokens (23 tokens added to ALWAYS_KEEP) raised the floor but the ceiling is still controlled by min_freq.

At min_freq=40, the 112 tokens that pass the frequency threshold are the most common Hackage constructors, prim ops, and structural patterns. The 1,520 rare tokens (appearing <40 times) collapse to `<unk>`. This is the intended behavior — the model learns from common patterns, ignores rare package-specific names.

**Sprint 5 vocabulary breakdown:**

- 4 special tokens (`<pad>`, `<unk>`, `<bos>`, `<eos>`)
- 57 structural always-keep tokens (de Bruijn indices 0–14, all IR keywords, `DEF`, `EOF`, `SELF`, `@T`)
- 23 type tokens added to always-keep (`TY`, `BODY`, `->`, brackets, canonical vars, common constructors) — overlapping with structural tokens, net ~19 new
- 112 freq≥40 tokens (Hackage constructors, operators, patterns)
- **Total: 173**

### Makefile augment target missing tokenizer dependency causes stale corpus

`sprint/corpus/sequences_aug.json` is defined as depending on `$(SEQUENCES)` and `sprint/corpus/augment.py`, but not `$(TOKENIZER)`. When `tokenizer.json` is rebuilt (because `tokenizer.py` or `sequences.json` changed), `sequences_aug.json` is not considered stale. `make corpus` runs only augment (since sequences.json is up to date), using the old tokenizer.

The augment target also runs before tokenize even when both are needed, because tokenize is not declared as a prerequisite. The practical consequence: `make corpus` after tokenizer changes produces an augmented corpus with the wrong tokenizer, and the model trains on mis-tokenized sequences.

Workaround: always `rm sprint/corpus/tokenizer.json sprint/corpus/sequences_aug.json && make corpus` when changing tokenizer.py. The fix would be adding `$(TOKENIZER)` to `sequences_aug.json`'s prerequisites in the Makefile.

### Adding `[` and `]` as separate tokens requires careful regex handling

In previous sprints, `[` and `]` were not in the vocabulary at all (they appeared only in constructor names like `[a]` as a unit). Sprint 5 adds them as individual tokens for type signatures (`[a]` in type context tokenizes as `[`, `a`, `]`).

This creates a potential clash: the `[a]` token in body context (as a `TyArg` value) maps to `<unk>` since `[a]` as a 3-character string is no longer in the vocabulary. The test confirmed this: `'[a]': 8` appeared in the unknown body tokens (the 8 occurrences are `TyArg [a]` nodes in function bodies). This is acceptable — `TyArg` nodes are erased at generation time anyway (they represent type applications invisible at the value level).

---

## 2026-02-23 — Pipeline Order (Phase 1)

### Manual normalize run before make corpus breaks the pipeline

Running `python -m sprint.corpus.normalize --stats` manually (without arguments) uses the default input `sprint/extract/corpus_raw.json` (the 162 hand-written functions) instead of `sprint/extract/corpus_all.json` (the full 4,438-function merged corpus). The resulting `sequences.json` has 162 entries instead of 4,438.

When `make corpus` runs next, `sequences.json` already exists and is newer than `corpus_all.json`, so normalize does not re-run. The tokenizer and augmented corpus are built from 162 functions instead of 4,438.

**Fix**: always use `make normalize` (or `make corpus`) rather than running normalize.py directly. The Makefile correctly passes `$(CORPUS_ALL)` as the input. If a manual run is needed (e.g. for debugging), always pass the full corpus path explicitly: `python sprint/corpus/normalize.py sprint/extract/corpus_all.json --out sprint/corpus/sequences.json --stats`.

**Rule**: pipeline scripts with default arguments are traps. The default is convenient for development on a small subset but dangerous when the pipeline expects a specific input. When in doubt, use the Makefile target.

---

## 2026-02-23 — Benchmark Updates (Phase 3)

### `prompt_ids[-2:]` breaks for variable-length Sprint 5 headers

Sprint 4 prompts were always 2 tokens: `[DEF, <unk>]`. The extraction loop in `run_task` relied on this:

```python
target_ids = list(prompt_ids[-2:]) + gen_ids
```

Sprint 5 prompts are variable-length: `[DEF, TY, <type_tokens...>, BODY]`. For `Int -> Int` this is 5 tokens; for `( a -> b ) -> [ a ] -> [ b ]` it's 11 tokens. `prompt_ids[-2:]` now grabs `[<last_type_token>, BODY]` instead of the DEF header, corrupting the extracted sequence.

The fix uses `extract_candidate_tokens()` which scans backward for the last `DEF` in `prompt_ids` to find the header start, regardless of length. This also correctly handles few-shot prompts where multiple DEF blocks precede the target.

**Rule**: never hardcode header length when the format can vary. Find the last structural anchor token (`DEF`) instead of counting from the end.

### `build_few_shot_prompt` needs the full corpus as a type index, not just name→tokens

Sprint 4's few-shot prompt construction used `sequences_by_name: dict[str, list[str]]` — looked up each named function in `task["few_shot_funcs"]`. Sprint 5 tasks use `context_type` (normalized type string) instead of named peers, so a type-indexed structure is needed.

The new `sequences_by_type: dict[str, list[tuple[str, list[str]]]]` groups `(func_name, tokens)` pairs by their normalized type key. Built in `main()` using `normalize_type` — the same function used by the training pipeline — so benchmark context selection matches training context grouping exactly.

**Design choice**: store `(name, tokens)` tuples rather than just `tokens` so the current task's own function can be excluded from its context pool (avoids the model seeing its exact answer as a hint).

### Arity fallback in few-shot selection: exact-type singletons are common

72.4% of corpus types are unique (only one function has that type). Without a fallback, most tasks with unique types would get no few-shot context. The arity fallback — match by number of `->` arrows — provides structurally related context even when exact type matches are absent.

This mirrors the training pipeline's `_type_peers()` fallback in `ASTDataset`, ensuring consistency between what the model was trained to expect and what it sees at benchmark time.

### `constrained_decode.py` and `pretty_print.py` required no changes

Both modules route all parsing through `checker.py`'s `parse_sequence`. The Sprint 5 TY…BODY frame handling was centralized there in the checker update. This validates the design: one parser, many consumers — format changes propagate automatically.

The `is_type_valid` function in `checker.py` calls `parse_sequence(partial + [next_token])` at every generation step. Since `parse_sequence` now transparently skips the `TY...BODY` header, constrained decoding works correctly with Sprint 5 prompts without any change to the masking logic.

### Sprint 5 `tasks.json` context_type field must match corpus normalize_type output exactly

`task["context_type"]` is the key used to look up peers in `sequences_by_type`. If the task's context_type doesn't exactly match the string produced by `" ".join(normalize_type(seq["type"]))` for any corpus sequence, the exact-match lookup returns empty and falls back to arity matching.

The tasks.json was generated programmatically using the same `normalize_type` function, so keys are consistent. But if tasks.json is hand-edited or regenerated with a different version of `normalize_type`, the index will silently miss. Always regenerate tasks.json programmatically when changing the normalization logic.

---

## 2026-02-23 — Benchmark Results (Run A)

### Removing names broke specific conditioning — type alone is insufficient for regression tasks

Sprint 4 conditioned on function names (`DEF fId`). Names fell below min_freq and became `<unk>`, which caused Sprint 5 to drop them entirely in favour of type signatures. This solved the `<unk>` conditioning problem but introduced a new one: type signatures are not unique per function.

Greedy decode (temp=0) for `fId` (`Int -> Int`) deterministically generates `f x = x + x` (double). The model has learned a corpus distribution over bodies for each type and generates the most common one. For `Int -> Int`, the corpus contains more arithmetic functions than identity functions, so arithmetic wins.

This is correct model behaviour, not a bug. The model is producing a valid `Int -> Int` function — it just isn't the one the test expects.

**The benchmark tests specific function semantics, but type-only conditioning cannot identify specific functions.** A type like `Int -> Int` is shared by identity, double, negate, abs, successor, and many others. Without a name, the model has no way to distinguish which one is wanted.

**Rule**: type signatures identify a set of functions, not a specific function. For benchmarks that test specific semantics, the conditioning prompt must include a unique identifier (name or description) in addition to the type.

### RL cannot fix ambiguous conditioning

RL (rejection sampling fine-tuning) requires each prompt to map to exactly one correct output. Multiple Sprint 5 benchmark tasks produce identical prompts:
- `fId`, `fDouble`, `fSucc`, `fNeg`, `fAbs` all produce `DEF TY Int -> Int BODY`
- `fSum`, `fLength`, `fProduct`, `fMax`, `fMin` all produce `DEF TY [ Int ] -> Int BODY`

RL would collect positive examples for each task separately (identity passes for fId, double passes for fDouble), then fine-tune on all of them. But since the prompts are identical, fine-tuning injects contradictory gradients: the same input sequence must produce different outputs. The model cannot converge; it would oscillate between the competing targets or average them into noise.

**Rule**: before running RL, verify that each task's conditioning prompt is unique. If two tasks share a prompt, RL cannot distinguish them and will degrade performance on both.

### The fix: name + type conditioning

The correct format is `DEF fId TY Int -> Int BODY` — name provides uniqueness, type provides structural signal for novel functions. Names are currently `<unk>` because they are below min_freq=40. The fix is not to lower min_freq (that bloats vocabulary) but to add benchmark function names explicitly to `ALWAYS_KEEP` in tokenizer.py.

This is a small, targeted change:
- Add ~40 benchmark function names (fId, fDouble, fMap, etc.) to `ALWAYS_KEEP`
- Update `to_sequence()` to emit `DEF name TY type BODY` format
- Rebuild tokenizer and corpus (names are now guaranteed in vocabulary)
- Update task prompts in tasks.json to include the name

Benefits: unique conditioning per function (RL works), type still present (novel task generalisation), no architecture change, minimal vocabulary growth (~40 new tokens).

### Training quality was excellent — the problem is prompt design, not model capacity

Val loss 0.448 nats and val/train ratio 1.03x both exceeded their targets. The model is genuinely generalising rather than memorising. This means the model architecture, data scale, and training regime are all working. The failure is entirely in the prompt format: the model cannot do what the benchmark asks because the prompt doesn't give it enough information to distinguish tasks.

This is actually an encouraging finding: fixing the prompt format (Sprint 5b) should immediately unlock the benchmark performance, without needing more data or a larger model.

---

## 2026-02-23 — Sprint 5b: Name + Type Format

### Vocabulary growth from ALWAYS_KEEP doesn't hurt generalisation

Adding ~150 function names to `ALWAYS_KEEP` grew the vocabulary from 173 to 352 tokens. The concern was that a larger embedding table and output projection would hurt training dynamics or require more data. In practice, Run B (352 tokens) matched Run A (173 tokens) on both val loss (0.445 vs 0.448) and val/train ratio (1.04x vs 1.03x). The names are rare in the Hackage portion of the corpus but dense in the hand-written portion; the model learns their embeddings from ~162 functions each, which is sufficient.

**Rule**: adding guaranteed-present tokens to ALWAYS_KEEP is cheap. The cost is a slightly larger embedding matrix; the benefit is zero `<unk>` in structurally significant positions.

### Make's timestamp-based freshness fails when files are edited then rebuilt in the same session

The Makefile's `$(CHECKPOINT)` target had a stale mtime after `make train` was killed mid-run — Make wrote a partial checkpoint then deleted it (safety cleanup), leaving `checkpoint.pt` absent but the make dependency graph thinking it was up to date (the target file was deleted, so next `make train` would rebuild). However, `sequences_aug.json` had an even newer mtime from the corpus rebuild, so the dependency check still triggered a rebuild correctly.

The subtler case: after rebuilding `tokenizer.json` manually (outside Make), `sequences_aug.json` had an older mtime — Make didn't know to re-augment. Fix: add `$(TOKENIZER)` to `sequences_aug.json`'s prerequisites so any tokenizer rebuild invalidates the augmented corpus automatically.

**Rule**: if a script takes a file as a runtime argument (not a code import), that file must be listed as a Makefile prerequisite. Make only tracks what you tell it to track.

### Killing a training process deletes the checkpoint (Make safety cleanup)

When a Make target is interrupted (Ctrl-C, kill, etc.), Make deletes the partially-built target file by default. This is correct behaviour for source files (avoids corrupted outputs) but painful for checkpoints — a 10-minute partial training run produces a valid checkpoint that Make discards.

Workaround: use `.PRECIOUS: $(CHECKPOINT)` to prevent deletion on interrupt, or train outside Make (`python -u train.py ... | tee logs/...`). For this project, training outside Make is simpler.

### `nohup ... > log 2>&1` produces empty logs until Python flushes

Python's stdout is fully buffered when redirected to a file (not a tty). A `nohup` redirect with `> log 2>&1` will appear empty for the entire training run if `train.py` uses `print()` without `flush=True`. The fix: use `PYTHONUNBUFFERED=1` (sets Python to unbuffered mode) or pass `-u` to the interpreter. The updated Makefile uses `PYTHONUNBUFFERED=1` for all long-running targets.

**Rule**: always use `PYTHONUNBUFFERED=1` or `python -u` when redirecting output to a file. `nohup` without unbuffering produces logs that appear empty until program exit.

### RL ceiling: tasks with zero winners cannot be improved by rejection sampling

RL (rejection sampling fine-tuning) collected winners from k=10 samples per task per round. 13 tasks (9 Tier 2, 4 Tier 3) produced zero passing samples in every round — the model never generated a correct solution, so there was nothing to reinforce. These tasks require multi-step composition:

- `count_positive`: `length (filter (>0) xs)` — filter then length
- `map_square`: `map (^2) xs` — map with a non-linear op
- `sort_desc`: `reverse (sort xs)` — sort then reverse
- `apply_twice`: `f (f x)` — higher-order self-application

The model generates syntactically valid IR but semantically wrong bodies — usually applying one step instead of two, or applying the wrong combinator. Since no correct sample is ever produced, RL has no gradient signal. More rounds would not help.

**Fix direction**: either (a) add these patterns explicitly to the training corpus (supervised), or (b) use a guided search strategy (e.g., beam search with partial-credit reward) rather than pure rejection sampling. Rejection sampling is only effective when the base model already has non-zero pass rate.

---

## 2026-02-23 — Run C: Type Grouping Ablation

### Type-grouped context is load-bearing, not a tuning detail

Run C trained with `--grouping none` (no structured context selection during training). Result: val loss 1.228 vs 0.445, val/train ratio 2.78x vs 1.04x, benchmark 0/38 vs 18/38.

The few-shot context mechanism works as follows: during training, each sequence is presented with 0–2 other sequences prepended as context (with probability 0.5). With `grouping=type`, those context sequences are drawn from functions with the same normalized type signature. With `grouping=none`, they are drawn randomly.

The type-grouped context forces the model to learn: "given that I've seen two functions of type `[Int] -> Int`, generate the third". This is a much harder and more generalizable task than "given a random pair of functions, complete the sequence". The structured context acts as an implicit regularizer — the training objective becomes type-conditional generation, not sequence memorization.

Without type grouping, the model sees random pairs that provide no useful conditioning signal. The model learns to ignore the context and fit each individual training sequence, producing the 2.78x overfit ratio. At inference time on novel prompts, it generates trained-distribution noise rather than valid IR.

**Rule**: when using few-shot context during training, the context must be semantically related to the target — random context is worse than no context. Random context teaches the model to ignore the prefix, which undermines the entire few-shot conditioning mechanism.

### Parse failures reveal memorization, not generalization

Run C's benchmark showed 80-90% parse failures (invalid IR token sequences) per task. This is characteristic of a memorized model: the model has learned specific byte sequences from training examples but cannot assemble valid IR for unseen prompts. The grammar of the IR (balanced parentheses, valid de Bruijn indices, correct keyword sequence) is a learned structural property — a model that memorizes rather than generalizes loses this structural knowledge for out-of-distribution inputs.

By contrast, Run B (type grouping) produced mostly valid parse sequences even for failed tasks — the failures were semantic (wrong function) or type errors (wrong GHC type), not structural. This confirms the model learned IR grammar as a generalizable property, not as a memorized pattern.

**Diagnostic**: high parse failure rate on novel prompts → memorization. Low parse failure rate with semantic/type errors → generalization. Use parse failure rate as a quick overfitting indicator before running full benchmark evaluation.
