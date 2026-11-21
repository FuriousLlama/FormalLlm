PYTHON   := PYTHONPATH=. .venv/bin/python
PROMPT   ?= DEF:fId
TASK     ?= fId
K        ?= 10
PARALLEL ?= 2
BATCH    ?= 50

# ── file targets ──────────────────────────────────────────────────────────────

LOGS           := logs

CORPUS_RAW     := sprint/extract/corpus_raw.json
CORPUS_CLEAN   := sprint/extract/corpus_clean.json
CORPUS_HACKAGE := sprint/extract/corpus_hackage.json
CORPUS_COMP    := sprint/extract/corpus_compositions.json
CORPUS_COMP_CLEAN := sprint/extract/corpus_compositions_clean.json
PKG_STATUS     := sprint/extract/pkg_status.json
BUILD_ORDER    := sprint/extract/build_order.json
PKG_RAW_DIR    := sprint/extract/pkg_raw
PKG_CLEAN_DIR  := sprint/extract/pkg_clean
CORPUS_ALL     := sprint/extract/corpus_all.json
SEQUENCES      := sprint/corpus/sequences.json
TOKENIZER      := sprint/corpus/tokenizer.json
MODEL_SIZE     ?= small
GROUPING       ?= type
CHECKPOINT     := sprint/model/checkpoint.pt

# ── pipeline ──────────────────────────────────────────────────────────────────

.PHONY: all extract clean-ir extract-comp extract-hackage extract-hackage-batch extract-hackage-build extract-hackage-clean extract-hackage-corpus dep-order pkg-status corpus-merge normalize tokenize corpus augment train train-small train-big generate typecheck benchmark bench-task pretty-print evaluate rl clean clean-raw clean-model smoke smoke-full help

all: train  ## Run full pipeline (extract → corpus → augment → train)

extract: $(CORPUS_RAW)  ## Extract typed ASTs from Haskell source via GHC Core

$(CORPUS_RAW): sprint/extract/Functions.hs sprint/extract/functions.txt
	$(PYTHON) sprint/extract/parse_ghc_core.py sprint/extract/Functions.hs \
		--filter sprint/extract/functions.txt \
		--out $(CORPUS_RAW) \
		--stats

clean-ir: $(CORPUS_CLEAN)  ## Clean raw GHC Core ASTs into clean IR

$(CORPUS_CLEAN): $(CORPUS_RAW) sprint/extract/clean.py sprint/extract/ir_schema.json
	$(PYTHON) sprint/extract/clean.py $(CORPUS_RAW) \
		--out $(CORPUS_CLEAN) --schema sprint/extract/ir_schema.json --stats --package handwritten

extract-comp: $(CORPUS_COMP_CLEAN)  ## Extract compositions from Compositions.hs → corpus_compositions_clean.json

$(CORPUS_COMP): sprint/extract/Compositions.hs sprint/extract/compositions.txt
	$(PYTHON) sprint/extract/parse_ghc_core.py sprint/extract/Compositions.hs \
		--filter sprint/extract/compositions.txt \
		--out $(CORPUS_COMP) --stats

$(CORPUS_COMP_CLEAN): $(CORPUS_COMP) sprint/extract/clean.py sprint/extract/ir_schema.json
	$(PYTHON) sprint/extract/clean.py $(CORPUS_COMP) \
		--out $(CORPUS_COMP_CLEAN) --schema sprint/extract/ir_schema.json --stats --package compositions

dep-order: ## Compute dependency-aware build order → build_order.json
	$(PYTHON) sprint/extract/dep_graph.py \
		--packages sprint/extract/packages.txt \
		--order $(BUILD_ORDER)

extract-hackage: $(CORPUS_HACKAGE)  ## Full 3-phase Hackage extraction → corpus_hackage.json

$(CORPUS_HACKAGE): sprint/extract/extract_hackage.py sprint/extract/packages.txt \
                   sprint/extract/clean.py sprint/extract/ir_schema.json \
                   $(BUILD_ORDER)
	$(PYTHON) sprint/extract/extract_hackage.py \
		--packages-file sprint/extract/packages.txt \
		--work-dir hackage-packages \
		--pkg-raw-dir $(PKG_RAW_DIR) \
		--pkg-clean-dir $(PKG_CLEAN_DIR) \
		--out $(CORPUS_HACKAGE) \
		--min-tokens 5 --max-tokens 300 \
		--parallel $(PARALLEL) \
		--build-order $(BUILD_ORDER) \
		--status-out $(PKG_STATUS) \
		--stats

$(BUILD_ORDER): sprint/extract/packages.txt sprint/extract/dep_graph.py \
                sprint/extract/.dep_cache.json
	$(PYTHON) sprint/extract/dep_graph.py \
		--packages sprint/extract/packages.txt \
		--order $(BUILD_ORDER)

extract-hackage-build: ## Phase 1 only: build packages → pkg_raw/  (skip already-built)
	$(PYTHON) sprint/extract/extract_hackage.py \
		--packages-file sprint/extract/packages.txt \
		--work-dir hackage-packages \
		--pkg-raw-dir $(PKG_RAW_DIR) \
		--pkg-clean-dir $(PKG_CLEAN_DIR) \
		--out $(CORPUS_HACKAGE) \
		--parallel $(PARALLEL) \
		--build-order $(BUILD_ORDER) \
		--status-out $(PKG_STATUS) \
		--phase build

extract-hackage-clean: ## Phase 2 only: re-clean pkg_raw/ → pkg_clean/  (use after editing clean.py)
	$(PYTHON) sprint/extract/extract_hackage.py \
		--packages-file sprint/extract/packages.txt \
		--pkg-raw-dir $(PKG_RAW_DIR) \
		--pkg-clean-dir $(PKG_CLEAN_DIR) \
		--out $(CORPUS_HACKAGE) \
		--parallel $(PARALLEL) \
		--force \
		--phase clean

extract-hackage-corpus: ## Phase 3 only: re-assemble corpus from pkg_clean/ (fast, use to change token limits)
	$(PYTHON) sprint/extract/extract_hackage.py \
		--packages-file sprint/extract/packages.txt \
		--pkg-clean-dir $(PKG_CLEAN_DIR) \
		--out $(CORPUS_HACKAGE) \
		--min-tokens 5 --max-tokens 300 \
		--phase corpus

extract-hackage-batch: ## Extract first BATCH packages only (default: 50) — for testing
	$(PYTHON) sprint/extract/extract_hackage.py \
		--packages-file sprint/extract/packages.txt \
		--work-dir hackage-packages \
		--pkg-raw-dir $(PKG_RAW_DIR) \
		--pkg-clean-dir $(PKG_CLEAN_DIR) \
		--out $(CORPUS_HACKAGE) \
		--min-tokens 5 --max-tokens 300 \
		--parallel $(PARALLEL) \
		--limit $(BATCH) \
		--build-order $(BUILD_ORDER) \
		--status-out $(PKG_STATUS) \
		--stats

pkg-status: ## Show per-package extraction status (reads filesystem + pkg_status.json)
	$(PYTHON) sprint/extract/pkg_status.py \
		--packages-file sprint/extract/packages.txt \
		--work-dir hackage-packages \
		--corpus $(CORPUS_HACKAGE) \
		--status $(PKG_STATUS)

corpus-merge: $(CORPUS_ALL)  ## Merge handwritten + hackage corpora

$(CORPUS_ALL): $(CORPUS_CLEAN) $(CORPUS_HACKAGE) $(CORPUS_COMP_CLEAN)
	$(PYTHON) -c "import json; \
		a=json.load(open('$(CORPUS_CLEAN)')); \
		b=json.load(open('$(CORPUS_HACKAGE)')); \
		c=json.load(open('$(CORPUS_COMP_CLEAN)')); \
		json.dump(a+b+c, open('$(CORPUS_ALL)','w'), indent=2); \
		print(f'[merge] {len(a)} handwritten + {len(b)} hackage + {len(c)} compositions = {len(a)+len(b)+len(c)} total')"

normalize: $(SEQUENCES)  ## Alpha-rename and serialize ASTs to token sequences

$(SEQUENCES): $(CORPUS_ALL) sprint/corpus/normalize.py
	$(PYTHON) sprint/corpus/normalize.py $(CORPUS_ALL) \
		--out $(SEQUENCES) \
		--stats

tokenize: $(TOKENIZER)  ## Build WordLevel tokenizer from sequences

$(TOKENIZER): $(SEQUENCES) sprint/corpus/tokenizer.py
	$(PYTHON) sprint/corpus/tokenizer.py $(SEQUENCES) \
		--out-tokenizer $(TOKENIZER) \
		--out-vocab sprint/corpus/vocab.json \
		--stats

augment: sprint/corpus/sequences_aug.json  ## Augment corpus with synthetic variants

sprint/corpus/sequences_aug.json: $(SEQUENCES) $(TOKENIZER) sprint/corpus/augment.py
	$(PYTHON) sprint/corpus/augment.py $(SEQUENCES) \
		--out sprint/corpus/sequences_aug.json \
		--tokenizer $(TOKENIZER) \
		--stats

corpus: sprint/corpus/sequences_aug.json $(TOKENIZER)  ## Run normalize + tokenize + augment

train: $(CHECKPOINT)  ## Train transformer (MODEL_SIZE=small|big, default: small ~450K params)

$(CHECKPOINT): sprint/corpus/sequences_aug.json $(TOKENIZER) sprint/model/train.py
	mkdir -p $(LOGS)
	PYTHONUNBUFFERED=1 $(PYTHON) sprint/model/train.py \
		--sequences sprint/corpus/sequences_aug.json \
		--tokenizer $(TOKENIZER) \
		--model-size $(MODEL_SIZE) \
		--grouping $(GROUPING) \
		--out $(CHECKPOINT) \
		2>&1 | tee $(LOGS)/train_$$(date +%Y%m%d_%H%M%S).log

train-small: sprint/corpus/sequences_aug.json $(TOKENIZER)  ## Train right-sized model (2L/d128/~450K) → checkpoint_small.pt
	$(PYTHON) sprint/model/train.py \
		--sequences sprint/corpus/sequences_aug.json \
		--tokenizer $(TOKENIZER) \
		--model-size small \
		--out sprint/model/checkpoint_small.pt

train-big: sprint/corpus/sequences_aug.json $(TOKENIZER)  ## Train big model (4L/d256/~3.3M) → checkpoint_big.pt
	$(PYTHON) sprint/model/train.py \
		--sequences sprint/corpus/sequences_aug.json \
		--tokenizer $(TOKENIZER) \
		--model-size big \
		--out sprint/model/checkpoint_big.pt

generate: $(CHECKPOINT)  ## Generate from checkpoint (override with PROMPT=DEF:fX)
	$(PYTHON) sprint/model/generate.py \
		--checkpoint $(CHECKPOINT) \
		--tokenizer $(TOKENIZER) \
		--prompt "$(PROMPT)"

typecheck: $(CHECKPOINT)  ## Constrained decode with structural type masking (PROMPT=DEF:fX)
	$(PYTHON) sprint/typecheck/constrained_decode.py \
		--checkpoint $(CHECKPOINT) \
		--tokenizer $(TOKENIZER) \
		--prompt "$(PROMPT)" \
		--temperature 0.8 \
		--n 5

smoke:  ## S29 Phase 0.3 — fast harness smoke suite (target <1 min)
	$(PYTHON) tests/harness_smoke.py

smoke-full:  ## S29 Phase 0.3 — full smoke suite including slow benchmark checks
	$(PYTHON) tests/harness_smoke.py --full

benchmark: $(CHECKPOINT)  ## Run full benchmark (all tasks, K=10 candidates each)
	mkdir -p $(LOGS)
	PYTHONUNBUFFERED=1 $(PYTHON) sprint/benchmark/run.py \
		--checkpoint $(CHECKPOINT) \
		--tokenizer $(TOKENIZER) \
		--tasks sprint/benchmark/tasks.json \
		--k $(K) \
		--temperature 0.8 \
		2>&1 | tee $(LOGS)/benchmark_$$(date +%Y%m%d_%H%M%S).log

bench-task: $(CHECKPOINT)  ## Run single task (TASK=fId, K=10)
	$(PYTHON) sprint/benchmark/run.py \
		--checkpoint $(CHECKPOINT) \
		--tokenizer $(TOKENIZER) \
		--tasks sprint/benchmark/tasks.json \
		--task $(TASK) \
		--k $(K) \
		--temperature 0.8 \
		--verbose

pretty-print:  ## Pretty-print a corpus function (FUNC=fId)
	$(PYTHON) sprint/benchmark/pretty_print.py \
		--sequences $(SEQUENCES) \
		--func $(TASK)

evaluate: $(CHECKPOINT)  ## Evaluate pass@k (K=50 candidates, temp 0.8)
	$(PYTHON) sprint/rl/evaluate.py \
		--checkpoint $(CHECKPOINT) \
		--tokenizer $(TOKENIZER) \
		--k $(K) \
		--temperature 0.8

rl: $(CHECKPOINT)  ## Multi-round rejection-sampling fine-tune → sprint/model/checkpoint_rl.pt
	mkdir -p $(LOGS)
	PYTHONUNBUFFERED=1 $(PYTHON) sprint/rl/rejection_sample.py \
		--checkpoint $(CHECKPOINT) \
		--checkpoint-out sprint/model/checkpoint_rl.pt \
		--tokenizer $(TOKENIZER) \
		--sequences sprint/corpus/sequences_aug.json \
		--tasks sprint/benchmark/tasks.json \
		--k $(K) \
		--eval-k $(K) \
		--temperature 0.8 \
		--epochs 100 \
		--rounds 3 \
		2>&1 | tee $(LOGS)/rl_$$(date +%Y%m%d_%H%M%S).log

# ── housekeeping ──────────────────────────────────────────────────────────────

clean-raw:  ## Remove pkg_raw/ and pkg_clean/ (force full re-extraction on next run)
	rm -rf $(PKG_RAW_DIR) $(PKG_CLEAN_DIR)

clean-model:  ## Remove model checkpoint only
	rm -f $(CHECKPOINT) sprint/model/checkpoint_test.pt

clean:  ## Remove all generated artifacts (keeps source + venv)
	rm -f $(CORPUS_RAW) $(CORPUS_CLEAN) $(CORPUS_HACKAGE) $(CORPUS_ALL) \
		$(SEQUENCES) $(TOKENIZER) \
		sprint/corpus/vocab.json \
		sprint/corpus/sequences_aug.json \
		sprint/model/checkpoint.pt \
		sprint/model/checkpoint_test.pt
	find sprint/extract -name "*.o" -o -name "*.hi" -o -name "*.dyn_hi" \
		-o -name "*.dyn_o" | xargs rm -f

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'
