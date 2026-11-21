# Sprint 10 — Chain-of-Thought for Programs (USES Section)

## Motivation

Across Sprints 5-9, T2 compositional tasks have remained stuck at 0-3/10. The model sees composition patterns in training and in few-shot context, but cannot transfer them to novel compositions. Five sprints of representation changes (named refs, constrained decoder fixes, pattern-aligned training, targeted data) have produced diminishing returns.

The diagnosis from the project assessment: the autoregressive model must simultaneously **select** building blocks, **plan** their arrangement, and **wire** the plumbing — three entangled decisions in a single left-to-right pass.

But autoregressive models _can_ produce complex structured output when given enough data. LLMs write coherent paragraphs without explicit planning, because implicit planning emerges from statistical patterns over massive data. With only ~70 compositions in 5,228 sequences, our model lacks the data density for implicit planning to emerge.

Chain-of-thought (CoT) solves this in LLMs: generate intermediate reasoning tokens ("let me think step by step") before the answer. The planning becomes explicit in the token sequence, learnable from much less data.

**Sprint 10 applies chain-of-thought to program synthesis**: add a `USES` section that lists the building-block functions before the body. The model first predicts _which functions it will compose_, then generates the body that wires them together.

## Hypothesis

**Primary**: Adding a USES section (list of GlobalRef function names) between TY and BODY will unlock T2 compositional tasks by giving the model an explicit planning step. The conditional distribution `P(body | type, USES)` is much tighter than `P(body | type)` alone.

**Mechanism**: The model sees training examples like:

```
fCountNegative ... USES fLength fFilter fIsNegative BODY (App fLength ...)
fSumEven       ... USES fSum fFilter fEven           BODY (App fSum ...)
```

It learns the template: "USES <agg> fFilter <pred> BODY → (App <agg> (App (App fFilter <pred>) Var 0))". At inference, even without having seen `USES fLength fFilter fIsPositive` specifically, the model has learned enough `USES <X> fFilter <Y>` patterns to generate the correct body.

**Decisive test**: If T2 improves from 0-3/10 to 5+/10, the planning step was the bottleneck. If T2 stays at 0-3/10, the model lacks capacity for compositional wiring regardless of planning, and the next step is architectural (tree-structured generation, modular pipeline, or scaling).

## Design

### Sequence Format Change

```
Sprint 9 (current):
  DEF fCountNegative TY [ Int ] -> Int BODY (App fLength (App (App fFilter fIsNegative) Var 0)) EOF

Sprint 10:
  DEF fCountNegative TY [ Int ] -> Int USES fLength fFilter fIsNegative BODY (App fLength (App (App fFilter fIsNegative) Var 0)) EOF
```

For non-compositional functions (no GlobalRefs in body), the USES section is omitted:

```
  DEF fDouble TY Int -> Int BODY (Lam 1 (App (App Prim + Var 0) Var 0)) EOF
```

### USES Extraction

The USES section is **automatically derived** from the existing serialized body — no manual annotation needed. Scan body tokens for GlobalRef names (tokens matching `_is_global_ref`: starts with `f`, second char uppercase) and emit them in order of first appearance before BODY.

```python
def extract_uses(body_tokens: list[str]) -> list[str]:
    """Extract GlobalRef names from body tokens, in order of first appearance."""
    seen = set()
    uses = []
    for tok in body_tokens:
        if _is_global_ref(tok) and tok not in seen:
            seen.add(tok)
            uses.append(tok)
    return uses
```

Where `_is_global_ref` follows the existing convention in `checker.py:209`:

```python
def _is_global_ref(tok: str) -> bool:
    return len(tok) >= 2 and tok[0] == 'f' and tok[1].isupper()
```

### Inference: USES Prediction

At benchmark time, the prompt changes from:

```
DEF fCountPositive TY [ Int ] -> Int BODY
```

to:

```
DEF fCountPositive TY [ Int ] -> Int USES
```

The model generates USES tokens (function names) until it emits `BODY`, then generates the body as before. The constrained decoder enforces:

- After `USES`: only GlobalRef tokens or `BODY` are valid
- After `BODY`: existing body validation (unchanged)

This means the model must **predict which functions to use** before generating any body structure. For T2 tasks, this is the planning step that was previously missing.

### Constrained Decoder Changes

The `is_type_valid` function in `checker.py` (called by `constrained_decode.py`) needs to handle the USES phase:

```
Parsing states:
  1. DEF <name> TY ... → existing (unchanged)
  2. After TY section:  USES or BODY valid
  3. In USES section:   GlobalRef tokens or BODY valid
  4. After BODY:        existing body validation (unchanged)
```

The USES section has a trivially simple grammar: a list of GlobalRef tokens terminated by `BODY`. No nesting, no structural validation needed.

### Training: Context Selection Interaction

The USES section interacts positively with type-grouped context selection. When the model is trained with a few-shot context containing `fCountNegative` (with its USES section), it sees:

```
Context:  ... USES fLength fFilter fIsNegative BODY (App fLength ...) EOF
Target:   DEF fCountPositive TY [ Int ] -> Int USES ???
```

The pattern "USES contains fLength + fFilter + <pred>" → "BODY is (App fLength (App (App fFilter <pred>) Var 0))" is directly visible. The model can learn to substitute `fIsPositive` for `fIsNegative` in both the USES and BODY sections.

## Phases

### Phase 1 — Corpus Rebuild with USES

Modify `to_sequence()` in `normalize.py` to emit the USES section.

Changes to `normalize.py`:

1. Add `_is_global_ref()` helper (copy from checker.py, or import)
2. Add `extract_uses(body_tokens)` function
3. Modify `to_sequence()`: after serializing body tokens, extract uses, emit `["DEF", name, "TY"] + type_tokens + ["USES"] + uses + ["BODY"] + body_tokens + ["EOF"]` when uses is non-empty, else emit current format (no USES section)

Changes to `tokenizer.py`:

1. Add `"USES"` to `ALWAYS_KEEP`

Rebuild corpus:

```bash
make corpus
```

Verify:

- All ~70 composition sequences have USES sections
- All ~4,450 primitive sequences have no USES section (straight to BODY)
- USES tokens are a subset of existing vocabulary (all are GlobalRef names already in ALWAYS_KEEP)
- Vocabulary size unchanged (+1 for USES token = 433)

### Phase 2 — Parser + Constrained Decoder

Changes to `checker.py`:

1. Update `parse_sequence()` to handle `USES` between TY and BODY
2. After consuming TY tokens, if next token is `USES`: consume GlobalRef tokens until `BODY`
3. Then proceed to parse body as before

Changes to `constrained_decode.py`:

1. Update `is_type_valid()` (or its callers) to allow USES tokens after TY section
2. In USES phase: only GlobalRef tokens and `BODY` are valid next tokens
3. After `BODY`: existing validation unchanged

Verify:

- `parse_sequence` round-trips all corpus sequences
- `is_type_valid` accepts the USES section
- `is_type_valid` rejects body tokens during USES phase
- `is_type_valid` rejects GlobalRef-only sequences after BODY (existing behavior)

### Phase 3 — Benchmark Update

Changes to `tasks.json`:

1. Update all 38 task prompts: `"prompt": "DEF fName TY type BODY"` → `"prompt": "DEF fName TY type USES"` for T2/T3 tasks that require composition
2. T1 tasks with no composition: keep `BODY` as prompt terminator (model should learn to skip USES for primitives)

Actually — **all prompts should end with `USES`**, even T1 tasks. The model should learn:

- T1 (no composition): `USES BODY (Lam ...)` — immediately emit BODY, no functions listed
- T2/T3 (composition): `USES fLength fFilter fIsPositive BODY (App ...)` — list functions first

This way the constrained decoder always starts in USES mode, and the model's first decision is whether to list building blocks or go straight to BODY.

Changes to `run.py`:

1. Ensure few-shot context sequences include USES sections (they will, since they come from the rebuilt corpus)
2. Ensure target prompt ends with `USES` (not `BODY`)
3. Modify target extraction: the target now starts after the last `USES` in the prompt, not the last `BODY`

### Phase 4 — Train + Evaluate

```bash
make train
make benchmark K=200 TIER=1   # regression check
make benchmark K=200 TIER=2   # primary metric
make benchmark K=200 TIER=3   # structural check
```

Run 3 times per tier, report ranges (Sprint 9 lesson: non-determinism is massive).

**T1 regression check**: T1 tasks should emit `USES BODY` (no GlobalRefs) and generate as before. If T1 drops, the USES section is interfering with primitive generation — debug by checking what tokens the model emits in the USES phase for T1 tasks.

### Phase 5 — RL (conditional)

Only attempt RL if:

1. T2 shows improvement (3+/10 pre-RL)
2. There are tasks with 1+/200 pass rate that could benefit from amplification

If RL is attempted, use the same approach as Sprint 8 (k=200, t=0.8, 3 rounds). The USES section should make RL more stable: winners now carry explicit building-block information, so the RL gradient reinforces both the USES selection and the body structure.

If T2 is already at 5+/10 pre-RL, RL may not be needed.

### Phase 6 — Analysis

If T2 improves:

- Analyze which tasks were unlocked and why
- Check whether the model predicts correct USES but wrong body (planning works, wiring fails) vs. wrong USES (planning itself fails)
- This diagnostic tells us whether the remaining gap is in selection or in wiring

If T2 does not improve:

- Dump generated USES sections for T2 tasks
- Check whether the model can predict correct building blocks at all
- If USES prediction is correct but body is wrong: the planning step works, but autoregressive body generation can't exploit it — consider the modular approach (Stage 3 type unification)
- If USES prediction is wrong: the model can't even select building blocks — consider explicit retrieval (Stage 1 from the modular approach)

## Files to Modify

| File                                     | Phase | Change                                                                            |
| ---------------------------------------- | ----- | --------------------------------------------------------------------------------- |
| `sprint/corpus/normalize.py`             | 1     | `_is_global_ref()`, `extract_uses()`, update `to_sequence()` to emit USES section |
| `sprint/corpus/tokenizer.py`             | 1     | Add `"USES"` to `ALWAYS_KEEP`                                                     |
| `sprint/typecheck/checker.py`            | 2     | Update `parse_sequence()` to consume USES section                                 |
| `sprint/typecheck/constrained_decode.py` | 2     | Allow USES tokens after TY; transition to body validation after BODY              |
| `sprint/benchmark/tasks.json`            | 3     | All prompts end with `USES` instead of `BODY`                                     |
| `sprint/benchmark/run.py`                | 3     | Target extraction starts after last `USES`; few-shot context includes USES        |

**No changes**: `augment.py` (commutative swap operates on body tokens, unaffected by USES prefix), `pretty_print.py` (operates on parsed AST), `train.py` (reads token sequences; USES tokens are just more tokens in the sequence), `Makefile` (pipeline targets unchanged).

Estimated total: ~50 lines of code changes across 6 files.

## Success Criteria

| Metric          | Sprint 9 (no RL) | Min bar     | Target   |
| --------------- | ---------------- | ----------- | -------- |
| T1 (regression) | 19-20/22         | >=19/22     | 20-22/22 |
| T2 (compose)    | 0-3/10           | **>=5/10**  | >=7/10   |
| T3 (structural) | 6/6              | >=5/6       | 6/6      |
| Total           | 25-29/38         | **>=30/38** | >=33/38  |

**Primary question**: Does an explicit USES planning step unlock T2 compositional generalization?

## Risk Analysis

| Risk                                                                              | Likelihood | Mitigation                                                                                                                                                                                     |
| --------------------------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| USES section is too short to provide signal (most compositions use 2-3 functions) | Medium     | Even 2-3 tokens of planning are meaningful — they narrow the body distribution from hundreds of possible structures to a handful                                                               |
| Model learns to predict correct USES but wrong body                               | Medium     | This is still progress — confirms planning works. Next step: constrain body generation using USES (e.g., body must reference all USES functions)                                               |
| T1 regression from USES overhead                                                  | Low        | T1 functions have no GlobalRefs; model should learn USES → BODY immediately. If not, make USES optional (only emit for compositions)                                                           |
| USES order matters but is arbitrary                                               | Low        | Use first-appearance order in body (deterministic). If order sensitivity is observed, try sorted order                                                                                         |
| Augmentation interacts badly with USES                                            | Low        | Commutative swap changes body token order but not which GlobalRefs appear. USES is derived from body, so swapped sequences get the same USES set (possibly different order). Verify in Phase 1 |

## Why This Is the Right Next Step

1. **Smallest possible intervention**: ~50 lines of changes, no architecture change, no new model, no new dependencies.

2. **Directly tests the core hypothesis**: Is the T2 bottleneck planning (knowing which functions to compose) or wiring (knowing how to connect them)? USES separates these cleanly.

3. **Automatically derived from existing data**: No manual annotation, no new training data, no hand-written compositions. The USES section is mechanically extracted from existing body tokens.

4. **Diagnostic regardless of outcome**: If USES works, we've found the fix. If it doesn't, the generated USES sections tell us exactly where the model fails (selection vs. wiring), guiding the next intervention.

5. **Compatible with future directions**: If USES works partially, it naturally extends to the modular approach (replace USES prediction with retrieval, replace body generation with type-directed wiring). If it works fully, it validates that autoregressive generation is sufficient with the right format.

## Appendix: Example Training Sequences

### Composition (filter+fold pattern)

```
DEF fCountNegative TY [ Int ] -> Int USES fLength fFilter fIsNegative BODY (Lam 1 (App fLength (App (App fFilter fIsNegative) Var 0))) EOF
```

### Composition (map+fold pattern)

```
DEF fSumAbs TY [ Int ] -> Int USES fSum fMap fAbs BODY (Lam 1 (App fSum (App (App fMap fAbs) Var 0))) EOF
```

### Composition (two-function pipeline)

```
DEF fReverseSort TY [ Int ] -> [ Int ] USES fReverse fSort BODY (Lam 1 (App fReverse (App fSort Var 0))) EOF
```

### Primitive (no composition)

```
DEF fDouble TY Int -> Int BODY (Lam 1 (App (App Prim + Var 0) Var 0)) EOF
```

### Case-based (no composition)

```
DEF fSum TY [ Int ] -> Int BODY (Lam 1 (Case Var 0 (Alt (PCon [] ) Lit 0) (Alt (PCon : 2) (App (App Prim + Var 1) (App SELF Var 0))))) EOF
```

### T2 inference example (what the model generates)

```
Prompt:  [context sequences...] DEF fCountPositive TY [ Int ] -> Int USES
Model:   fLength fFilter fIsPositive BODY (Lam 1 (App fLength (App (App fFilter fIsPositive) Var 0))) EOF
```
