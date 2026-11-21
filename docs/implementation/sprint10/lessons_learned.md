# Sprint 10 — Lessons Learned

## 1. Training/inference format mismatch is fatal

When training sequences use a different format than inference prompts, the model fails predictably. Sprint 10's initial design (USES only for compositions in training, USES for all at inference) caused the model to always generate GlobalRef tokens after USES — it had never seen `USES BODY` (empty USES).

**Fix**: Always use the same sequence format in training and inference. If inference prompts end with USES, training sequences must too.

## 2. Base rate dominance in small vocabularies

With 110 compositions in 5228 sequences (2.1%), the model learned that USES is overwhelmingly followed by BODY. Even with few-shot context showing non-empty USES patterns, the model generates BODY immediately 40-70% of the time.

**Implication**: Chain-of-thought works in LLMs because the training data has sufficient diversity. With ~2% composition density, the model can't learn the USES→GlobalRef pattern reliably. Need either much more composition data or explicit constraints.

## 3. The T2 bottleneck is wiring, not planning

USES correctly predicts building blocks (fLength + fFilter) ~30% of the time with composition context. But the body still fails — it uses wrong predicates, wrong structure, or wrong argument order. The autoregressive decoder can select correct functions but can't wire them together.

**Next step**: The body wiring problem is a type-level problem. The model doesn't understand that fFilter needs a predicate of type `a -> Bool` and fIsPositive has type `Int -> Bool`. Type-directed constraints on the body (given USES) would be the natural next intervention.

## 4. Structural tasks are fragile

T3 tasks (maybe_with_default, apply_twice) had marginal pass rates in Sprint 9 (2-21/200). Any format change disrupts these patterns. The model doesn't have robust understanding of these types — it relies on surface-level pattern matching from specific checkpoint characteristics.

## 5. Parser partial-acceptance is necessary for constrained decode

To support USES in the constrained decoder, `parse_sequence` must accept partial sequences (header without BODY). This is a side effect of how `is_type_valid` works: it tries to parse `partial + [next_token]` and checks if parsing succeeds. Partial headers must succeed to allow the model to generate USES tokens.

The downside is slightly permissive validation for header tokens (TY accepted after DEF fName), but this is harmless because the model never generates header tokens.

## 6. Val loss doesn't predict benchmark performance

Sprint 10's USES-for-all model achieved val_loss=0.4462 (better than Sprint 9's 0.4583), but benchmark results were comparable or slightly worse. Val loss measures token-level prediction accuracy, not compositional generalization. The USES token adds predictable structure (USES BODY for primitives) that's easy to predict but doesn't help with T2.
