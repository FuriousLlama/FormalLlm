"""
tokenizer.py — build a WordLevel tokenizer from de Bruijn normalized sequences.

Sprint 4: target ~100-200 token vocabulary (down from ~1,549 in sprint 3).
De Bruijn normalization eliminates named binder tokens (v0-v34); variable use
sites become integer index tokens (0-14). Rare tokens map to <unk>.

Vocabulary structure:
  - Special (4):      <pad> <unk> <bos> <eos>
  - Structural (~18): DEF EOF SELF @T Var Con Lit Prim TyArg
                      (App (Lam (Let (Case (Tuple (Alt (Bind (PCon (PTuple (PLit (PWild) )
  - De Bruijn (15):   0 1 2 3 4 5 6 7 8 9 10 11 12 13 14
  - Frequent (rest):  constructors, prim ops, lit values, type args — above min_freq

Input:  sprint/corpus/sequences.json (or sequences_aug.json)
Output: sprint/corpus/tokenizer.json  (HuggingFace tokenizers format)
        sprint/corpus/vocab.json       (human-readable token → id)

Special tokens:
  <pad>   0
  <unk>   1
  <bos>   2
  <eos>   3
"""

import json
import collections
import argparse
from pathlib import Path
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import WhitespaceSplit
from tokenizers.processors import TemplateProcessing


SPECIAL_TOKENS = ["<pad>", "<unk>", "<bos>", "<eos>"]
PAD_ID, UNK_ID, BOS_ID, EOS_ID = 0, 1, 2, 3

# Tokens that must always be in the vocabulary regardless of corpus frequency.
# Structural tokens + de Bruijn indices 0-14. These are guaranteed to appear
# in every valid sequence, but the safety net protects against edge cases.

# Sprint 5b: all hand-written corpus function names + benchmark-specific names.
# These appear as the name token in DEF <name> TY ... BODY sequences.
_CORPUS_FUNCTION_NAMES: frozenset[str] = frozenset({
    # Core / combinators
    "fId", "fConst", "fFlip", "fComp", "fFst", "fSnd", "fCurry", "fUncurry",
    "fApply", "fConstFlip", "fFix", "fUntil", "fCompose2", "fCompose3",
    "fTwice", "fNTimes", "fOn",
    # Bool
    "fNot", "fAnd", "fOr", "fXor", "fImplies", "fNand", "fNor",
    "fIfThenElse", "fBoolEq", "fBoolToInt",
    # Int arithmetic
    "fSucc", "fPred", "fDouble", "fSquare", "fAbs", "fMax", "fMin",
    "fEven", "fOdd", "fGcd", "fNegate", "fTriple", "fHalve", "fMod",
    "fDivides", "fIsZero", "fIsPositive", "fIsNegative", "fSignum",
    "fClamp", "fAvg", "fPow", "fFactorial", "fFibonacci",
    # Tuples / pairs
    "fSwap", "fBoth", "fMapFst", "fMapSnd", "fBimap", "fDup", "fPairWith",
    "fUncurry3", "fTriple3", "fFst3", "fSnd3", "fThd3",
    # List basics
    "fNull", "fHead", "fTail", "fLast", "fInit", "fLength",
    "fSingleton", "fCons", "fAppend", "fReplicate", "fRange",
    "fReverse", "fConcat", "fConcatMap", "fIntersperse",
    "fTake", "fDrop", "fSplitAt", "fTakeWhile", "fDropWhile", "fSpan",
    # List search / membership
    "fElem", "fNotElem", "fLookup", "fFind",
    # List higher-order
    "fMap", "fFilter", "fFoldr", "fFoldl", "fAny", "fAll", "fCount", "fSumWith",
    "fZip", "fZipWith", "fUnzip", "fZip3", "fZipWith3", "fUnzip3",
    "fPartition", "fNubInt", "fInsert", "fSort",
    "fScanl1", "fIterate", "fUnfoldr", "fGroupBy", "fChunksOf",
    "fTranspose", "fIndexOf", "fMapAccumL", "fEnumerate", "fFoldlWithIndex",
    # List aggregates (concrete types)
    "fSum", "fProduct", "fMaximum", "fMinimum", "fAndList", "fOrList",
    # Derived list ops (hand-written corpus)
    "fCountTrue", "fSumSquares", "fSumPositive", "fMaxOr", "fMinOr",
    "fHeadOrZero", "fHeadOrDefault", "fLastOrDefault", "fSumOf",
    "fZipWithAdd", "fZipWithMul", "fDotProduct",
    "fNegate2", "fAbsList", "fDoubleAll", "fIncrAll", "fDecrAll",
    # Maybe
    "fMaybe", "fIsJust", "fIsNothing", "fFromMaybe",
    "fMaybeToList", "fListToMaybe", "fCatMaybes", "fMapMaybe",
    "fOrElse", "fMaybeBind", "fMaybeMap", "fMaybeFilter", "fMaybeOr",
    "fSequenceMaybe", "fMapMaybeList", "fFirstJust", "fCountJust",
    # Char / String
    "fDigitToInt", "fIntToDigit", "fIsDigit", "fIsAlpha",
    "fToUpper", "fToLower", "fWords", "fUnwords", "fLines",
    # Benchmark-specific (novel tasks not in Functions.hs)
    "fCountPositive", "fMapSquare", "fSortDesc", "fSumDoubles",
    "fMaxAbs", "fTakePositives", "fNubSort", "fDropNeg",
    "fAccumSum", "fCountEven", "fApplyTwice", "fMapPair",
    "fMaybeDefault", "fUncurryAdd",
    # Compositions (Sprint 6)
    "fCountNegative", "fSumEven", "fCountNonZero", "fCountOdd",
    "fSumNegative", "fSumNonZero", "fProductPositive", "fMaxNonZero",
    "fMinPositive", "fCountLarge", "fSumSmall", "fCountOddPositive",
    "fSumAbs", "fSumTriples", "fSumSucc", "fSumNegated",
    "fProductDoubles", "fProductAbs", "fMaxSquare", "fMinAbs",
    "fMaxDouble", "fMinSquare",
    "fMapPlusTwo", "fMapTimesThree", "fMapMinusOne", "fMapSquarePlus",
    "fMapAbsPlus", "fMapHalve", "fMapSignum", "fMapClamp",
    "fReverseTail", "fSortNub", "fReverseTake", "fTakeSort",
    "fSortFilter", "fNubReverse", "fTailReverse", "fSortMapAbs",
    "fReverseMapNeg", "fTakeWhileSort", "fFilterSort", "fReverseInit",
    "fFilterEven", "fFilterOdd", "fFilterPositive", "fFilterNonZero",
    "fFilterSmall", "fFilterLarge", "fTakeWhilePos", "fTakeWhileSmall",
    "fDropWhileNeg", "fDropWhileZero",
    "fApplyThrice", "fMapTwice", "fFilterTwice", "fDoubleApply",
    "fTripleApply", "fSelfMap", "fDoubleThenFilter", "fFilterThenMap",
    "fUncurrySub", "fUncurryMul", "fUncurryMax", "fUncurryMin",
    "fBothDouble", "fBothSucc", "fBothAbs", "fSwapApply",
    "fPairAdd", "fMapPairs",
    "fMaybeDouble", "fMaybeSucc", "fMaybeAbs", "fMaybeToZero",
    "fMaybeToNeg", "fDefaultList", "fDefaultZeroList", "fExtractPositive",
    "fCountJustPositive", "fSumJusts",
})

ALWAYS_KEEP = frozenset({
    # Sequence markers
    "DEF", "EOF", "SELF", "@T",
    # Sprint 5: type-conditioning frame markers
    "TY", "BODY",
    # Sprint 10: chain-of-thought USES section
    "USES",
    # Sprint 5: type tokens — arrow and brackets
    "->", "[", "]",
    # Sprint 5: type variables (canonical a–d cover 99%+ of corpus)
    "a", "b", "c", "d",
    # Sprint 5: common type constructors
    "Int", "Integer", "Bool", "Char", "Maybe", "Either", "IO",
    "Word", "Word8", "Float", "Double", "String",
    # Leaf node keywords
    "Var", "Con", "Lit", "Prim", "TyArg",
    # S-expression open tokens
    "(App", "(Lam", "(Let", "(Case", "(Tuple",
    "(Alt", "(Bind", "(PCon", "(PTuple", "(PLit", "(PWild)",
    # Close paren
    ")",
    # De Bruijn indices 0–14
    *(str(i) for i in range(15)),
    # Sprint 5b: corpus function names (guarantee in-vocabulary for DEF <name> token)
    *_CORPUS_FUNCTION_NAMES,
})


def build_vocab(sequences: list[dict], min_frequency: int) -> dict[str, int]:
    """Build vocab dict {token: id} from corpus frequencies.

    All ALWAYS_KEEP tokens are included regardless of frequency.
    Other tokens are included only if they appear >= min_frequency times.
    IDs are assigned in order: specials → structural/de Bruijn → frequent rest.
    """
    freq: collections.Counter = collections.Counter(
        t for seq in sequences for t in seq["tokens"]
    )

    # Start with special tokens (fixed IDs 0-3)
    vocab: dict[str, int] = {tok: i for i, tok in enumerate(SPECIAL_TOKENS)}

    # Add ALWAYS_KEEP tokens in deterministic order (sorted for reproducibility)
    for tok in sorted(ALWAYS_KEEP):
        if tok not in vocab:
            vocab[tok] = len(vocab)

    # Add remaining tokens that meet the frequency threshold, sorted by freq desc
    for tok, cnt in freq.most_common():
        if tok not in vocab and cnt >= min_frequency:
            vocab[tok] = len(vocab)

    return vocab


def build_tokenizer(sequences: list[dict], min_frequency: int = 40) -> Tokenizer:
    """Build a WordLevel tokenizer from the corpus with the target vocabulary."""
    vocab = build_vocab(sequences, min_frequency)

    tokenizer = Tokenizer(WordLevel(vocab=vocab, unk_token="<unk>"))
    tokenizer.pre_tokenizer = WhitespaceSplit()

    # Add BOS/EOS wrapping via post-processor
    tokenizer.post_processor = TemplateProcessing(
        single="<bos> $A <eos>",
        special_tokens=[("<bos>", BOS_ID), ("<eos>", EOS_ID)],
    )

    return tokenizer


def verify_roundtrip(tokenizer: Tokenizer, sequences: list[dict]) -> None:
    """Check that every sequence encodes and decodes back to the original tokens.

    With min_frequency > 1, rare tokens map to <unk>. A "round-trip OK" means
    every token either matches exactly or was mapped to <unk> (expected lossy).
    """
    errors = 0
    unk_sequences = 0
    for seq in sequences:
        original = seq["tokens"]
        text = " ".join(original)
        enc = tokenizer.encode(text)
        # Decoded ids (strip BOS/EOS added by post-processor)
        ids = enc.ids[1:-1]   # remove BOS, EOS
        decoded_tokens = [tokenizer.id_to_token(i) for i in ids]
        if len(decoded_tokens) != len(original):
            print(f"  LENGTH MISMATCH {seq['name']}: {len(original)} -> {len(decoded_tokens)}")
            errors += 1
            continue
        has_unk = False
        mismatch = False
        for orig_t, dec_t in zip(original, decoded_tokens):
            if dec_t == "<unk>":
                has_unk = True
            elif dec_t != orig_t:
                mismatch = True
        if mismatch:
            print(f"  MISMATCH {seq['name']}:")
            print(f"    original:  {original[:10]} ...")
            print(f"    decoded:   {decoded_tokens[:10]} ...")
            errors += 1
        elif has_unk:
            unk_sequences += 1
    if errors == 0:
        msg = f"[tokenizer] round-trip OK for all {len(sequences)} sequences"
        if unk_sequences:
            msg += f" ({unk_sequences} contain <unk> tokens)"
        print(msg)
    else:
        print(f"[tokenizer] {errors} round-trip failures")


def main():
    p = argparse.ArgumentParser(description="Build WordLevel tokenizer")
    p.add_argument("input", nargs="?",
                   default="sprint/corpus/sequences.json")
    p.add_argument("--out-tokenizer", default="sprint/corpus/tokenizer.json")
    p.add_argument("--out-vocab",     default="sprint/corpus/vocab.json")
    p.add_argument("--min-freq", type=int, default=40,
                   help="Minimum token frequency; rarer tokens map to <unk> (default: 40, targets ~150-175 vocab)")
    p.add_argument("--stats", action="store_true")
    args = p.parse_args()

    sequences = json.loads(Path(args.input).read_text())

    tokenizer = build_tokenizer(sequences, min_frequency=args.min_freq)

    out_tok = Path(args.out_tokenizer)
    out_vocab = Path(args.out_vocab)
    out_tok.parent.mkdir(parents=True, exist_ok=True)

    tokenizer.save(str(out_tok))

    # Human-readable vocab
    vocab = tokenizer.get_vocab()
    vocab_sorted = dict(sorted(vocab.items(), key=lambda x: x[1]))
    out_vocab.write_text(json.dumps(vocab_sorted, indent=2))

    if args.stats:
        print(f"[tokenizer] vocabulary size: {len(vocab)}")
        print(f"[tokenizer] special tokens:  {SPECIAL_TOKENS}")
        n_always = sum(1 for t in vocab if t in ALWAYS_KEEP)
        n_freq   = len(vocab) - len(SPECIAL_TOKENS) - n_always
        print(f"[tokenizer] always-keep:     {n_always} (structural + de Bruijn 0-14)")
        print(f"[tokenizer] freq >= {args.min_freq}:    {n_freq} (constructors, prims, etc.)")
        # Encode sample
        sample = sequences[0]
        enc = tokenizer.encode(" ".join(sample["tokens"]))
        print(f"[tokenizer] sample ({sample['name']}): {enc.ids}")
        verify_roundtrip(tokenizer, sequences)

    print(f"[tokenizer] wrote tokenizer → {out_tok}")
    print(f"[tokenizer] wrote vocab     → {out_vocab}")


if __name__ == "__main__":
    main()
