PYTHON := .venv/bin/python
LEAN_PROJECT := env/FormalNav
BASE_MODEL ?= Qwen/Qwen2.5-Coder-1.5B
# Use 90% of cores for builds/tracing (avoid pegging the machine)
NUM_PROCS ?= $(shell python3 -c "import os; print(max(1, int(os.cpu_count() * 0.9)))")
export NUM_PROCS

.PHONY: setup test-dojo lean-build extract-data split-data \
        train-policy collect-rollouts train-value \
        benchmark benchmark-trained benchmark-compare \
        enumerate generate-tasks benchmark-expanded \
        selfplay train-synthesis \
        enumerate-v2 selfplay-v2 extract-stdlib \
        train-synthesis-v2 extract-grammar-v2 \
        benchmark-v2 benchmark-sprint14 \
        all clean clean-train help

help:
	@echo "lean_mcts pipeline targets:"
	@echo ""
	@echo "  Setup:"
	@echo "    setup            - Create Python 3.12 venv, install deps, build Lean project"
	@echo "    test-dojo        - Verify LeanDojo can send/receive tactics"
	@echo "    lean-build       - Build Lean project (after editing .lean files)"
	@echo ""
	@echo "  Phase 1 - Data:"
	@echo "    extract-data     - Extract (state, tactic) pairs from Mathlib (~30-60 min)"
	@echo "    split-data       - Split extracted data into train/val"
	@echo ""
	@echo "  Phase 2 - Policy:"
	@echo "    train-policy     - LoRA fine-tune policy on Mathlib tactics"
	@echo ""
	@echo "  Phase 3 - Value:"
	@echo "    collect-rollouts - Run MCTS with trained policy → rollout data"
	@echo "    train-value      - Train value network on rollout data"
	@echo ""
	@echo "  Phase 6 - Evaluation:"
	@echo "    benchmark        - Run MCTS with FixedPolicy (3 runs)"
	@echo "    benchmark-trained - Run MCTS with TrainedPolicy (3 runs)"
	@echo "    benchmark-compare - Run all configs for comparison"
	@echo ""
	@echo "  Misc:"
	@echo "    all              - Full pipeline: setup → extract → train → benchmark"
	@echo "    clean            - Remove venv, build artifacts, results"
	@echo "    clean-train      - Remove training artifacts only"

# --- Setup ---

setup:
	uv venv .venv --python 3.12
	uv pip install --python .venv/bin/python -r requirements.txt
	cd $(LEAN_PROJECT) && lake update && lake exe cache get && lake build -j$(NUM_PROCS)

test-dojo:
	$(PYTHON) env/dojo_wrapper.py --test

lean-build:
	cd $(LEAN_PROJECT) && lake build -j$(NUM_PROCS)

# --- Phase 1: Data Extraction ---

extract-data:
	ulimit -s unlimited && $(PYTHON) env/extract_data.py --output train/mathlib_tactics.jsonl

split-data:
	$(PYTHON) env/extract_data.py --split --input train/mathlib_tactics.jsonl

# --- Phase 2: Policy Training ---

train-policy:
	$(PYTHON) train/pretrain.py \
		--data train/mathlib_train.jsonl \
		--val train/mathlib_val.jsonl \
		--base-model $(BASE_MODEL) \
		--output train/policy_adapter/ \
		--epochs 3 --lr 2e-4 --batch-size 8

# --- Phase 3: Value Network ---

collect-rollouts:
	$(PYTHON) train/collect_rollouts.py \
		--policy-adapter train/policy_adapter/ \
		--base-model $(BASE_MODEL) \
		--rollouts 500 --output train/rollout_data.jsonl

train-value:
	$(PYTHON) train/train_value.py \
		--data train/rollout_data.jsonl \
		--base-model $(BASE_MODEL) \
		--output train/value_head.pt

# --- Phase 6: Evaluation ---

benchmark:
	$(PYTHON) benchmark/run.py --rollouts 500 --runs 3

benchmark-trained:
	$(PYTHON) benchmark/run.py \
		--policy trained \
		--adapter-path train/policy_adapter/ \
		--base-model $(BASE_MODEL) \
		--rollouts 500 --runs 3

benchmark-compare:
	@echo "=== Config A: FixedPolicy (baseline) ==="
	$(PYTHON) benchmark/run.py --policy fixed --rollouts 500 --runs 3
	@echo ""
	@echo "=== Config B: TrainedPolicy (no value) ==="
	$(PYTHON) benchmark/run.py --policy trained --adapter-path train/policy_adapter/ --base-model $(BASE_MODEL) --rollouts 500 --runs 3
	@echo ""
	@echo "=== Config C: TrainedPolicy + Value ==="
	$(PYTHON) benchmark/run.py --policy trained --adapter-path train/policy_adapter/ --base-model $(BASE_MODEL) --value-model train/value_head.pt --rollouts 500 --runs 3

# --- Sprint 13: Synthesis-Bootstrapped Policy ---

enumerate:
	$(PYTHON) synth/enumerate.py \
		--output synth/expressions.jsonl \
		--levels 0,1

generate-tasks:
	$(PYTHON) synth/task_generator.py \
		--expressions synth/expressions.jsonl \
		--output-lean $(LEAN_PROJECT)/FormalNav/SynthTasks.lean \
		--output-tasks synth/synth_tasks.jsonl \
		--n-tasks 200

benchmark-expanded:
	$(PYTHON) benchmark/run.py --policy expanded-fixed --tier t2 --rollouts 500 --runs 1

selfplay:
	$(PYTHON) synth/collect_selfplay.py \
		--rollouts 500 --max-k 200 --n-augment 3

train-synthesis:
	$(PYTHON) train/pretrain.py \
		--data train/synth_train.jsonl \
		--val train/synth_val.jsonl \
		--base-model $(BASE_MODEL) \
		--output train/synth_adapter/ \
		--epochs 10 --lr 2e-4 --batch-size 8 \
		--max-length 256

extract-grammar:
	$(PYTHON) synth/grammar.py \
		--expressions synth/expressions.jsonl \
		--output synth/grammar.json

benchmark-synthesis:
	$(PYTHON) benchmark/run.py --policy synthesis --tier t2 --rollouts 500 --runs 3

benchmark-pcfg:
	$(PYTHON) benchmark/run.py --policy pcfg --expressions synth/grammar.json --tier t2 --rollouts 500 --runs 3

benchmark-sprint13:
	@echo "=== Config A: FixedPolicy (baseline) ==="
	$(PYTHON) benchmark/run.py --policy fixed --tier t2 --rollouts 500 --runs 3
	@echo ""
	@echo "=== Config B: SynthesisPolicy (LoRA on synth data) ==="
	$(PYTHON) benchmark/run.py --policy synthesis --tier t2 --rollouts 500 --runs 3
	@echo ""
	@echo "=== Config C: PCFGPolicy (grammar sampling) ==="
	$(PYTHON) benchmark/run.py --policy pcfg --expressions synth/grammar.json --tier t2 --rollouts 500 --runs 3
	@echo ""
	@echo "=== Config D: ExpandedFixedPolicy ==="
	$(PYTHON) benchmark/run.py --policy expanded-fixed --tier t2 --rollouts 500 --runs 1

# --- Sprint 14: Multi-Type Synthesis + Hard Task Generalization ---

enumerate-v2:
	$(PYTHON) synth/enumerate.py \
		--output synth/expressions_v2.jsonl \
		--levels 0,1,2 --multi-type --expanded-library \
		--stats synth/enum_stats_v2.json

selfplay-v2:
	$(PYTHON) synth/collect_selfplay.py \
		--expressions synth/expressions_v2.jsonl \
		--output-train train/synth_v2_train.jsonl \
		--output-val train/synth_v2_val.jsonl \
		--skip-mcts --multi-step --n-augment 3

extract-stdlib:
	$(PYTHON) synth/extract_stdlib.py \
		--output train/stdlib_pairs.jsonl --verbose

train-synthesis-v2:
	$(PYTHON) train/pretrain.py \
		--data train/synth_v2_train.jsonl \
		--val train/synth_v2_val.jsonl \
		--base-model $(BASE_MODEL) \
		--output train/synth_v2_adapter/ \
		--epochs 10 --lr 2e-4 --batch-size 8 \
		--max-length 256 --patience 3

extract-grammar-v2:
	$(PYTHON) synth/grammar.py \
		--expressions synth/expressions_v2.jsonl \
		--output synth/grammar_v2.json

benchmark-v2:
	$(PYTHON) benchmark/run.py --policy fixed --tier v2 --rollouts 500 --runs 3

benchmark-sprint14:
	@echo "=== Config A: FixedPolicy (baseline) ==="
	$(PYTHON) benchmark/run.py --policy fixed --tier all+v2 --rollouts 500 --runs 3
	@echo ""
	@echo "=== Config B: ExpandedFixed v2 ==="
	$(PYTHON) benchmark/run.py --policy expanded-fixed --expressions synth/expressions_v2.jsonl --tier all+v2 --rollouts 500 --runs 1 --max-expand-k 100
	@echo ""
	@echo "=== Config C: SynthesisPolicy v2 (LoRA on synth_v2 data) ==="
	$(PYTHON) benchmark/run.py --policy synthesis --adapter-path train/synth_v2_adapter/ --base-model $(BASE_MODEL) --tier all+v2 --rollouts 500 --runs 3 --max-expand-k 100
	@echo ""
	@echo "=== Config D: PCFG v2 ==="
	$(PYTHON) benchmark/run.py --policy pcfg --expressions synth/grammar_v2.json --tier all+v2 --rollouts 500 --runs 3

# --- Full Pipeline ---

all: setup lean-build extract-data train-policy collect-rollouts train-value benchmark-compare

# --- Cleanup ---

clean:
	rm -rf .venv $(LEAN_PROJECT)/.lake train/*.jsonl train/*.json train/policy_adapter train/value_head.pt benchmark/results/*

clean-train:
	rm -rf train/policy_adapter train/value_head.pt train/rollout_data.jsonl
