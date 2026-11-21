"""Goal-conditioned training data collection for Sprint 16.

Collects (state, tactic) pairs from:
  1. Existing expressions_v2.jsonl (1435 pairs)
  2. Hand-crafted reference solutions for unsolved tasks (~60 pairs)
  3. Programmatically generated compositional expressions (~200-400 pairs)
  4. Variable name augmentation (3x expansion)
  5. Multi-step product type decompositions

Outputs goal_cond_train.jsonl and goal_cond_val.jsonl for LoRA training.

Usage:
    python lean_mcts/synth/collect_goal_conditioned.py
    python lean_mcts/synth/collect_goal_conditioned.py --skip-verify
    python lean_mcts/synth/collect_goal_conditioned.py --verbose
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

# Reuse helpers from collect_selfplay.py
try:
    from lean_mcts.synth.collect_selfplay import (
        LEAN_IMPORTS,
        TrainingPair,
        augment_pairs,
        synthesize_multistep_pairs,
        write_training_data,
        _DIAG_RE,
    )
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from synth.collect_selfplay import (
        LEAN_IMPORTS,
        TrainingPair,
        augment_pairs,
        synthesize_multistep_pairs,
        write_training_data,
        _DIAG_RE,
    )


# ---------------------------------------------------------------------------
# Hand-crafted reference solutions for unsolved tasks
# ---------------------------------------------------------------------------

# Each entry: (state, tactic, task_name, return_type, input_type)
# States obtained from MCTS verbose runs on unsolved V2/Hard tasks.

HAND_CRAFTED_REFERENCES: list[dict] = [
    # --- stats: Int × Int (min, max) --- sub-goals after constructor
    {"state": "xs : List Int\n⊢ Int × Int", "tactic": "constructor",
     "task": "stats", "return_type": "Int × Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Int", "tactic": "exact xs.foldl min 0",
     "task": "stats_left", "return_type": "Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Int", "tactic": "exact xs.foldl max 0",
     "task": "stats_right", "return_type": "Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Int", "tactic": "exact xs.foldl (fun a b => if a < b then a else b) 0",
     "task": "stats_min_if", "return_type": "Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Int", "tactic": "exact xs.foldl (fun a b => if a > b then a else b) 0",
     "task": "stats_max_if", "return_type": "Int", "input_type": "List Int"},

    # --- split_apply: List Int × List Int --- sub-goals after constructor
    {"state": "xs : List Int\n⊢ List Int × List Int", "tactic": "constructor",
     "task": "split_apply", "return_type": "List Int × List Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List Int", "tactic": "exact xs.map (fun x => x * 2)",
     "task": "split_apply_left", "return_type": "List Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List Int", "tactic": "exact xs.map (fun x => x + 1)",
     "task": "split_apply_right", "return_type": "List Int", "input_type": "List Int"},

    # --- first_n_squares: List Nat from Nat input ---
    {"state": "n : Nat\n⊢ List Nat", "tactic": "exact (List.range n).map (fun i => i * i)",
     "task": "first_n_squares", "return_type": "List Nat", "input_type": "Nat"},
    {"state": "n : Nat\n⊢ List Nat", "tactic": "exact (List.range n).map (· ^ 2)",
     "task": "first_n_squares_alt", "return_type": "List Nat", "input_type": "Nat"},
    {"state": "n : Nat\n⊢ List Nat", "tactic": "exact (List.range n).map (fun i => i ^ 2)",
     "task": "first_n_squares_alt2", "return_type": "List Nat", "input_type": "Nat"},

    # --- flatten: List Int from List (List Int) ---
    {"state": "xss : List (List Int)\n⊢ List Int", "tactic": "exact xss.flatten",
     "task": "flatten", "return_type": "List Int", "input_type": "List (List Int)"},
    {"state": "xss : List (List Int)\n⊢ List Int", "tactic": "exact xss.join",
     "task": "flatten_join", "return_type": "List Int", "input_type": "List (List Int)"},
    {"state": "xss : List (List Int)\n⊢ List Int", "tactic": "exact List.flatten xss",
     "task": "flatten_prefix", "return_type": "List Int", "input_type": "List (List Int)"},
    {"state": "xss : List (List Int)\n⊢ List Int", "tactic": "exact xss.foldl (· ++ ·) []",
     "task": "flatten_fold", "return_type": "List Int", "input_type": "List (List Int)"},

    # --- zip_sum: List Int from two-arg ---
    {"state": "xs : List Int\nys : List Int\n⊢ List Int", "tactic": "exact List.zipWith (fun a b => a + b) xs ys",
     "task": "zip_sum", "return_type": "List Int", "input_type": "List Int, List Int"},
    {"state": "xs : List Int\nys : List Int\n⊢ List Int", "tactic": "exact (xs.zip ys).map (fun (a, b) => a + b)",
     "task": "zip_sum_alt", "return_type": "List Int", "input_type": "List Int, List Int"},
    {"state": "xs : List Int\nys : List Int\n⊢ List Int", "tactic": "exact List.zipWith (· + ·) xs ys",
     "task": "zip_sum_dot", "return_type": "List Int", "input_type": "List Int, List Int"},

    # --- partition_positive: List Int × List Int ---
    {"state": "xs : List Int\n⊢ List Int × List Int", "tactic": "exact xs.partition (fun x => decide (x > 0))",
     "task": "partition_positive", "return_type": "List Int × List Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List Int × List Int", "tactic": "constructor",
     "task": "partition_positive_ctor", "return_type": "List Int × List Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List Int", "tactic": "exact xs.filter (fun x => decide (x > 0))",
     "task": "partition_pos_left", "return_type": "List Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List Int", "tactic": "exact xs.filter (fun x => decide (x ≤ 0))",
     "task": "partition_pos_right", "return_type": "List Int", "input_type": "List Int"},

    # --- dedup_count: List (Int × Nat) ---
    {"state": "xs : List Int\n⊢ List (Int × Nat)", "tactic": "exact xs.eraseDups.map (fun x => (x, (xs.filter (· == x)).length))",
     "task": "dedup_count", "return_type": "List (Int × Nat)", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List (Int × Nat)", "tactic": "exact xs.eraseDups.map (fun x => (x, (xs.filter (fun y => decide (y == x))).length))",
     "task": "dedup_count_alt", "return_type": "List (Int × Nat)", "input_type": "List Int"},

    # --- filter_map_abs: List Nat ---
    {"state": "xs : List Int\n⊢ List Nat", "tactic": "exact xs.filterMap (fun x => if decide (x > 0) then some x.toNat else none)",
     "task": "filter_map_abs", "return_type": "List Nat", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List Nat", "tactic": "exact (xs.filter (fun x => decide (x > 0))).map (fun x => x.toNat)",
     "task": "filter_map_abs_alt", "return_type": "List Nat", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List Nat", "tactic": "exact xs.filterMap (fun x => if decide (x ≥ 0) then some x.toNat else none)",
     "task": "filter_map_nonneg", "return_type": "List Nat", "input_type": "List Int"},

    # --- Additional sub-goal tactic variations ---
    # Product type sub-goal: Nat × Nat
    {"state": "xs : List Int\n⊢ Nat × Nat", "tactic": "constructor",
     "task": "nat_nat_ctor", "return_type": "Nat × Nat", "input_type": "List Int"},
    # Product type sub-goal: Nat × Int
    {"state": "xs : List Int\n⊢ Nat × Int", "tactic": "constructor",
     "task": "nat_int_ctor", "return_type": "Nat × Int", "input_type": "List Int"},

    # foldl variations for sub-goals
    {"state": "xs : List Int\n⊢ Int", "tactic": "exact xs.foldl (fun a b => a + b) 0",
     "task": "foldl_sum", "return_type": "Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Int", "tactic": "exact xs.foldl (fun a b => a * b) 1",
     "task": "foldl_prod", "return_type": "Int", "input_type": "List Int"},

    # Nat sub-goal tactics
    {"state": "xs : List Int\n⊢ Nat", "tactic": "exact (xs.filter (fun x => decide (x > 0))).length",
     "task": "count_pos_subgoal", "return_type": "Nat", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Nat", "tactic": "exact (xs.filter (fun x => decide (x ≤ 0))).length",
     "task": "count_nonpos_subgoal", "return_type": "Nat", "input_type": "List Int"},

    # --- Sprint 17: Self-play verified solutions ---
    # Novel tactics discovered by the model and verified by oracle
    {"state": "xs : List Int\n⊢ Int × Int", "tactic": "exact xs.foldl (fun (mn, mx) x => (min mn x, max mx x)) (0, 0)",
     "task": "sp_stats_tuplefold", "return_type": "Int × Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List Nat", "tactic": "exact (xs.filter (fun x => decide (x > 0))).map (fun x => x.toNat)",
     "task": "sp_filter_map_abs_v2", "return_type": "List Nat", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List Nat", "tactic": "have h := xs.filter (fun x => decide (x > 0))\nexact h.map (fun x => x.toNat)",
     "task": "sp_filter_map_abs_have", "return_type": "List Nat", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List (Int × Nat)", "tactic": "exact xs.eraseDups.map (fun x => (x, xs.count x))",
     "task": "sp_dedup_count", "return_type": "List (Int × Nat)", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List Int × List Int", "tactic": "exact (xs.filter (fun x => decide (x > 0)), xs.filter (fun x => decide (x ≤ 0)))",
     "task": "sp_partition_direct", "return_type": "List Int × List Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Nat × Int", "tactic": "have h := xs.filter (fun x => decide (x > 0))\nexact (h.length, h.foldl (fun a b => a + b) 0)",
     "task": "sp_filter_then_stats_have", "return_type": "Nat × Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Nat", "tactic": "exact (xs.filter (fun x => decide (x > 0))).eraseDups.length",
     "task": "sp_count_unique_pos", "return_type": "Nat", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Int", "tactic": "exact (xs.filter (fun x => decide (x ≥ 0))).foldl (fun a b => a + b) 0",
     "task": "sp_sum_nonneg", "return_type": "Int", "input_type": "List Int"},

    # --- Sprint 17: Tuple-accumulator fold patterns ---
    # Fibonacci
    {"state": "n : Nat\n⊢ Nat", "tactic": "exact ((List.range n).foldl (fun (a, b) _ => (b, a + b)) (0, 1)).1",
     "task": "fibonacci", "return_type": "Nat", "input_type": "Nat"},
    {"state": "n : Nat\n⊢ Nat", "tactic": "exact ((List.range n).foldl (fun (a, b) _ => (b, a + b)) (0, 1)).2",
     "task": "fibonacci_snd", "return_type": "Nat", "input_type": "Nat"},
    # FibList
    {"state": "n : Nat\n⊢ List Nat", "tactic": "exact ((List.range n).foldl (fun (acc, ab) _ => (acc ++ [ab.1], (ab.2, ab.1 + ab.2))) ([], (0, 1))).1",
     "task": "fib_list", "return_type": "List Nat", "input_type": "Nat"},
    # Factorial
    {"state": "n : Nat\n⊢ Nat", "tactic": "exact (List.range n).foldl (fun acc i => acc * (i + 1)) 1",
     "task": "factorial", "return_type": "Nat", "input_type": "Nat"},
    # Triangular numbers
    {"state": "n : Nat\n⊢ List Nat", "tactic": "exact (List.range n).map (fun i => (List.range (i + 1)).foldl (· + ·) 0)",
     "task": "triangular_numbers", "return_type": "List Nat", "input_type": "Nat"},
    # Running parts (tuple fold on list)
    {"state": "xs : List Int\n⊢ Int × Nat", "tactic": "exact xs.foldl (fun (s, c) x => (s + x, c + 1)) (0, 0)",
     "task": "running_parts", "return_type": "Int × Nat", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Int × Nat", "tactic": "constructor",
     "task": "int_nat_ctor", "return_type": "Int × Nat", "input_type": "List Int"},
    # Powers of 2
    {"state": "n : Nat\n⊢ List Nat", "tactic": "exact (List.range n).map (fun i => (List.range i).foldl (fun a _ => a * 2) 1)",
     "task": "powers_of_2", "return_type": "List Nat", "input_type": "Nat"},

    # --- Sprint 19: Fundamental programming patterns ---
    # Category 1: Pattern matching on List
    {"state": "xs : List Int\n⊢ Int", "tactic": "exact match xs with | [] => 0 | h :: _ => h",
     "task": "fund_match_head", "return_type": "Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List Int", "tactic": "exact match xs with | [] => [] | _ :: t => t",
     "task": "fund_match_tail", "return_type": "List Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Int", "tactic": "exact match xs with | [] => 0 | [x] => x | x :: y :: _ => x + y",
     "task": "fund_match_first_two", "return_type": "Int", "input_type": "List Int"},

    # Category 2: Pattern matching on Nat
    {"state": "n : Nat\n⊢ Nat", "tactic": "exact match n with | 0 => 0 | k + 1 => k",
     "task": "fund_match_pred", "return_type": "Nat", "input_type": "Nat"},
    {"state": "n : Nat\n⊢ Nat", "tactic": "exact match n with | 0 => 1 | _ + 1 => 0",
     "task": "fund_match_is_zero", "return_type": "Nat", "input_type": "Nat"},
    {"state": "n : Nat\n⊢ Nat", "tactic": "exact match n with | 0 => 0 | 1 => 1 | _ + 1 => 2",
     "task": "fund_match_classify", "return_type": "Nat", "input_type": "Nat"},

    # Category 3: List.rec — structural recursion on lists
    {"state": "xs : List Int\n⊢ Nat", "tactic": "exact List.rec 0 (fun _ _ acc => acc + 1) xs",
     "task": "fund_rec_length", "return_type": "Nat", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Int", "tactic": "exact List.rec 0 (fun h _ acc => acc + h) xs",
     "task": "fund_rec_sum", "return_type": "Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List Int", "tactic": "exact List.rec [] (fun h _ acc => (h * 2) :: acc) xs",
     "task": "fund_rec_map_double", "return_type": "List Int", "input_type": "List Int"},

    # Category 4: Nat.rec — natural number recursion
    {"state": "n : Nat\n⊢ Nat", "tactic": "exact Nat.rec 1 (fun k acc => acc * (k + 1)) n",
     "task": "fund_rec_factorial", "return_type": "Nat", "input_type": "Nat"},
    {"state": "n : Nat\n⊢ Nat", "tactic": "exact Nat.rec 0 (fun k acc => acc + k + 1) n",
     "task": "fund_rec_sum_to_n", "return_type": "Nat", "input_type": "Nat"},
    {"state": "n : Nat\n⊢ Nat", "tactic": "exact Nat.rec 1 (fun _ acc => acc * 2) n",
     "task": "fund_rec_pow2", "return_type": "Nat", "input_type": "Nat"},

    # Category 5: Let bindings
    {"state": "xs : List Int\n⊢ Nat", "tactic": "exact let pos := xs.filter (fun x => decide (x > 0)); pos.length",
     "task": "fund_let_intermediate", "return_type": "Nat", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Int", "tactic": "exact let sorted := List.mergeSort xs; let top := sorted.reverse; match top with | [] => 0 | h :: _ => h",
     "task": "fund_let_chain", "return_type": "Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Nat", "tactic": "exact let (pos, neg) := xs.partition (fun x => decide (x > 0)); pos.length + neg.length",
     "task": "fund_let_destruct", "return_type": "Nat", "input_type": "List Int"},

    # Category 6: If/then/else branching
    {"state": "n : Nat\n⊢ Nat", "tactic": "exact if n > 100 then 100 else n",
     "task": "fund_if_clamp", "return_type": "Nat", "input_type": "Nat"},
    {"state": "xs : List Int\n⊢ Nat", "tactic": "exact if xs.length > 5 then (xs.filter (fun x => decide (x > 0))).length else xs.length",
     "task": "fund_if_cond_pipeline", "return_type": "Nat", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Int", "tactic": "exact if xs.length > 0 then match xs with | h :: _ => h | [] => 0 else 0",
     "task": "fund_if_safe_head", "return_type": "Int", "input_type": "List Int"},

    # Category 7: Combined patterns (recursion + branching)
    {"state": "xs : List Int\n⊢ Int", "tactic": "exact List.rec 0 (fun h _ acc => if h > acc then h else acc) xs",
     "task": "fund_rec_max", "return_type": "Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Nat", "tactic": "exact List.rec 0 (fun h _ acc => if decide (h > 0) then acc + 1 else acc) xs",
     "task": "fund_rec_count_pos", "return_type": "Nat", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Int", "tactic": "exact List.rec 0 (fun h t acc => match t with | [] => h | _ :: _ => acc) xs",
     "task": "fund_rec_last", "return_type": "Int", "input_type": "List Int"},

    # --- Sprint 20: Multi-step sub-goal states with case prefix ---
    # After `constructor` on Nat × Nat, Pantograph produces "case fst\n...\ncase snd\n..."
    # The policy sees full combined state first, then "case snd\n..." after solving fst.

    # partitionAndCount: case fst (positive count)
    {"state": "case fst\nxs : List Int\n⊢ Nat", "tactic": "exact (xs.filter (fun x => decide (x > 0))).length",
     "task": "s20_pac_fst", "return_type": "Nat", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ Nat", "tactic": "exact (xs.filter (fun x => decide (x ≥ 0))).length",
     "task": "s20_pac_fst_ge", "return_type": "Nat", "input_type": "List Int"},
    # partitionAndCount: case snd (non-positive count)
    {"state": "case snd\nxs : List Int\n⊢ Nat", "tactic": "exact (xs.filter (fun x => decide (x ≤ 0))).length",
     "task": "s20_pac_snd", "return_type": "Nat", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ Nat", "tactic": "exact (xs.filter (fun x => decide (x < 0))).length",
     "task": "s20_pac_snd_lt", "return_type": "Nat", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ Nat", "tactic": "exact xs.length - (xs.filter (fun x => decide (x > 0))).length",
     "task": "s20_pac_snd_diff", "return_type": "Nat", "input_type": "List Int"},

    # runningParts: case fst (sum), case snd (count)
    {"state": "case fst\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl (fun a b => a + b) 0",
     "task": "s20_rp_fst", "return_type": "Int", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ Nat", "tactic": "exact xs.length",
     "task": "s20_rp_snd", "return_type": "Nat", "input_type": "List Int"},

    # Generic product sub-goals with case prefix: Int × Int (stats)
    {"state": "case fst\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl min 0",
     "task": "s20_stats_fst_min", "return_type": "Int", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl max 0",
     "task": "s20_stats_snd_max", "return_type": "Int", "input_type": "List Int"},

    # List Int × List Int sub-goals with case prefix
    {"state": "case fst\nxs : List Int\n⊢ List Int", "tactic": "exact xs.filter (fun x => decide (x > 0))",
     "task": "s20_split_fst_pos", "return_type": "List Int", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ List Int", "tactic": "exact xs.filter (fun x => decide (x ≤ 0))",
     "task": "s20_split_snd_neg", "return_type": "List Int", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ List Int", "tactic": "exact xs.map (fun x => x * 2)",
     "task": "s20_split_fst_double", "return_type": "List Int", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ List Int", "tactic": "exact xs.map (fun x => x + 1)",
     "task": "s20_split_snd_succ", "return_type": "List Int", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ List Int", "tactic": "exact xs.filter (fun x => decide (x % 2 = 0))",
     "task": "s20_split_fst_even", "return_type": "List Int", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ List Int", "tactic": "exact xs.filter (fun x => decide (x % 2 ≠ 0))",
     "task": "s20_split_snd_odd", "return_type": "List Int", "input_type": "List Int"},

    # Combined sub-goal state (both goals visible — seen right after constructor)
    {"state": "case fst\nxs : List Int\n⊢ Nat\ncase snd\nxs : List Int\n⊢ Nat",
     "tactic": "exact (xs.filter (fun x => decide (x > 0))).length",
     "task": "s20_combined_pac_fst", "return_type": "Nat", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ Int\ncase snd\nxs : List Int\n⊢ Int",
     "tactic": "exact xs.foldl min 0",
     "task": "s20_combined_stats_fst", "return_type": "Int", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ List Int\ncase snd\nxs : List Int\n⊢ List Int",
     "tactic": "exact xs.filter (fun x => decide (x > 0))",
     "task": "s20_combined_split_fst", "return_type": "List Int", "input_type": "List Int"},

    # --- Sprint 20: have-binding patterns ---
    # have introduces an intermediate binding, then exact uses it
    {"state": "xs : List Int\n⊢ Nat × Nat", "tactic": "have h := xs.filter (fun x => decide (x > 0))\nexact (h.length, (xs.filter (fun x => decide (x ≤ 0))).length)",
     "task": "s20_have_pac", "return_type": "Nat × Nat", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Nat × Int", "tactic": "have h := xs.filter (fun x => decide (x > 0))\nexact (h.length, h.foldl (fun a b => a + b) 0)",
     "task": "s20_have_count_sum", "return_type": "Nat × Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Int × Int", "tactic": "have h := xs.foldl (fun (mn, mx) x => (min mn x, max mx x)) (0, 0)\nexact h",
     "task": "s20_have_minmax", "return_type": "Int × Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List Int × List Int", "tactic": "have h := xs.partition (fun x => decide (x > 0))\nexact h",
     "task": "s20_have_partition", "return_type": "List Int × List Int", "input_type": "List Int"},

    # Post-have states (after have creates binding in context)
    {"state": "xs : List Int\nh : List Int := List.filter (fun x => decide (x > 0)) xs\n⊢ Nat × Nat",
     "tactic": "exact (h.length, (xs.filter (fun x => decide (x ≤ 0))).length)",
     "task": "s20_posthave_pac", "return_type": "Nat × Nat", "input_type": "List Int"},
    {"state": "xs : List Int\nh : List Int := List.filter (fun x => decide (x > 0)) xs\n⊢ Nat × Int",
     "tactic": "exact (h.length, h.foldl (fun a b => a + b) 0)",
     "task": "s20_posthave_count_sum", "return_type": "Nat × Int", "input_type": "List Int"},
    {"state": "xs : List Int\nh : List Int := List.filter (fun x => decide (x > 0)) xs\n⊢ Nat",
     "tactic": "exact h.length",
     "task": "s20_posthave_len", "return_type": "Nat", "input_type": "List Int"},
    {"state": "xs : List Int\nh : List Int := List.filter (fun x => decide (x > 0)) xs\n⊢ Int",
     "tactic": "exact h.foldl (fun a b => a + b) 0",
     "task": "s20_posthave_sum", "return_type": "Int", "input_type": "List Int"},

    # --- Sprint 21: Sub-goal simple fold patterns (case-prefixed) ---
    # sum_and_product sub-goals: simple sum and product folds
    {"state": "case fst\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl (· + ·) 0",
     "task": "s21_sap_fst_sum", "return_type": "Int", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl (· * ·) 1",
     "task": "s21_sap_snd_prod", "return_type": "Int", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl (fun a b => a + b) 0",
     "task": "s21_sap_fst_sum2", "return_type": "Int", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl (fun a b => a * b) 1",
     "task": "s21_sap_snd_prod2", "return_type": "Int", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl max 0",
     "task": "s21_sap_fst_max", "return_type": "Int", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl min 0",
     "task": "s21_sap_snd_min", "return_type": "Int", "input_type": "List Int"},

    # Combined state for sum_and_product: both sub-goals visible
    {"state": "case fst\nxs : List Int\n⊢ Int\ncase snd\nxs : List Int\n⊢ Int",
     "tactic": "exact xs.foldl (· + ·) 0",
     "task": "s21_combined_sap_fst_sum", "return_type": "Int", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ Int\ncase snd\nxs : List Int\n⊢ Int",
     "tactic": "exact xs.foldl (fun a b => a + b) 0",
     "task": "s21_combined_sap_fst_sum2", "return_type": "Int", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ Int\ncase snd\nxs : List Int\n⊢ Int",
     "tactic": "exact xs.foldl (· * ·) 1",
     "task": "s21_combined_sap_fst_prod", "return_type": "Int", "input_type": "List Int"},

    # split_even_odd sub-goals: even/odd filters
    {"state": "case fst\nxs : List Int\n⊢ List Int", "tactic": "exact xs.filter (fun x => decide (x % 2 = 0))",
     "task": "s21_seo_fst_even", "return_type": "List Int", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ List Int", "tactic": "exact xs.filter (fun x => decide (x % 2 ≠ 0))",
     "task": "s21_seo_snd_odd", "return_type": "List Int", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ List Int", "tactic": "exact xs.filter (fun x => decide (x % 2 ≠ 0))",
     "task": "s21_seo_fst_odd", "return_type": "List Int", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ List Int", "tactic": "exact xs.filter (fun x => decide (x % 2 = 0))",
     "task": "s21_seo_snd_even", "return_type": "List Int", "input_type": "List Int"},

    # Combined state for split_even_odd
    {"state": "case fst\nxs : List Int\n⊢ List Int\ncase snd\nxs : List Int\n⊢ List Int",
     "tactic": "exact xs.filter (fun x => decide (x % 2 = 0))",
     "task": "s21_combined_seo_fst_even", "return_type": "List Int", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ List Int\ncase snd\nxs : List Int\n⊢ List Int",
     "tactic": "exact xs.filter (fun x => decide (x % 2 ≠ 0))",
     "task": "s21_combined_seo_fst_odd", "return_type": "List Int", "input_type": "List Int"},

    # Direct even/odd pair pattern (1-step solution for split_even_odd)
    {"state": "xs : List Int\n⊢ List Int × List Int",
     "tactic": "exact (xs.filter (fun x => decide (x % 2 = 0)), xs.filter (fun x => decide (x % 2 ≠ 0)))",
     "task": "s21_direct_even_odd", "return_type": "List Int × List Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ List Int × List Int",
     "tactic": "exact (xs.filter (fun x => decide (x % 2 ≠ 0)), xs.filter (fun x => decide (x % 2 = 0)))",
     "task": "s21_direct_odd_even", "return_type": "List Int × List Int", "input_type": "List Int"},

    # Direct sum/product pair pattern (1-step solution for sum_and_product)
    {"state": "xs : List Int\n⊢ Int × Int",
     "tactic": "exact (xs.foldl (· + ·) 0, xs.foldl (· * ·) 1)",
     "task": "s21_direct_sum_prod", "return_type": "Int × Int", "input_type": "List Int"},
    {"state": "xs : List Int\n⊢ Int × Int",
     "tactic": "exact (xs.foldl (fun a b => a + b) 0, xs.foldl (fun a b => a * b) 1)",
     "task": "s21_direct_sum_prod2", "return_type": "Int × Int", "input_type": "List Int"},

    # Nat × Int sub-goals: length + sum (length_and_sum pattern)
    {"state": "case fst\nxs : List Int\n⊢ Nat", "tactic": "exact xs.length",
     "task": "s21_las_fst_len", "return_type": "Nat", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl (· + ·) 0",
     "task": "s21_las_snd_sum", "return_type": "Int", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl (fun a b => a + b) 0",
     "task": "s21_las_snd_sum2", "return_type": "Int", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ Nat\ncase snd\nxs : List Int\n⊢ Int",
     "tactic": "exact xs.length",
     "task": "s21_combined_las_fst_len", "return_type": "Nat", "input_type": "List Int"},

    # --- Sprint 21: Depth-3 nested constructor patterns ---
    # After first constructor on (A × B) × C, sub-goal fst is still a product type
    # The policy must propose constructor again for the nested product

    # (Int × Nat) × Int — sumCountMax: case fst is Int × Nat → needs constructor
    {"state": "case fst\nxs : List Int\n⊢ Int × Nat", "tactic": "constructor",
     "task": "s21_d3_scm_fst_ctor", "return_type": "Int × Nat", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ Int × Nat\ncase snd\nxs : List Int\n⊢ Int",
     "tactic": "constructor",
     "task": "s21_d3_scm_combined_fst_ctor", "return_type": "Int × Nat", "input_type": "List Int"},
    # After nested constructor: case fst → case fst (sum), case fst → case snd (count)
    {"state": "case fst\ncase fst\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl (· + ·) 0",
     "task": "s21_d3_scm_ff_sum", "return_type": "Int", "input_type": "List Int"},
    {"state": "case fst\ncase snd\nxs : List Int\n⊢ Nat", "tactic": "exact xs.length",
     "task": "s21_d3_scm_fs_len", "return_type": "Nat", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl max 0",
     "task": "s21_d3_scm_snd_max", "return_type": "Int", "input_type": "List Int"},

    # (Int × Int) × Nat — statsTriple: case fst is Int × Int → needs constructor
    {"state": "case fst\nxs : List Int\n⊢ Int × Int", "tactic": "constructor",
     "task": "s21_d3_st_fst_ctor", "return_type": "Int × Int", "input_type": "List Int"},
    {"state": "case fst\ncase fst\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl min 0",
     "task": "s21_d3_st_ff_min", "return_type": "Int", "input_type": "List Int"},
    {"state": "case fst\ncase snd\nxs : List Int\n⊢ Int", "tactic": "exact xs.foldl max 0",
     "task": "s21_d3_st_fs_max", "return_type": "Int", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ Nat", "tactic": "exact xs.length",
     "task": "s21_d3_st_snd_len", "return_type": "Nat", "input_type": "List Int"},

    # (Nat × Nat) × Nat — partitionLengths: case fst is Nat × Nat → needs constructor
    {"state": "case fst\nxs : List Int\n⊢ Nat × Nat", "tactic": "constructor",
     "task": "s21_d3_pl_fst_ctor", "return_type": "Nat × Nat", "input_type": "List Int"},
    {"state": "case fst\ncase fst\nxs : List Int\n⊢ Nat",
     "tactic": "exact (xs.filter (fun x => decide (x > 0))).length",
     "task": "s21_d3_pl_ff_pos", "return_type": "Nat", "input_type": "List Int"},
    {"state": "case fst\ncase snd\nxs : List Int\n⊢ Nat",
     "tactic": "exact (xs.filter (fun x => decide (x < 0))).length",
     "task": "s21_d3_pl_fs_neg", "return_type": "Nat", "input_type": "List Int"},
    {"state": "case snd\nxs : List Int\n⊢ Nat", "tactic": "exact xs.length",
     "task": "s21_d3_pl_snd_len", "return_type": "Nat", "input_type": "List Int"},

    # Generic depth-3 patterns: constructor on case-prefixed product types
    {"state": "case fst\nxs : List Int\n⊢ Nat × Nat", "tactic": "constructor",
     "task": "s21_d3_generic_nn_ctor", "return_type": "Nat × Nat", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ Int × Int", "tactic": "constructor",
     "task": "s21_d3_generic_ii_ctor", "return_type": "Int × Int", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ Int × Nat", "tactic": "constructor",
     "task": "s21_d3_generic_in_ctor", "return_type": "Int × Nat", "input_type": "List Int"},
    {"state": "case fst\nxs : List Int\n⊢ Nat × Int", "tactic": "constructor",
     "task": "s21_d3_generic_ni_ctor", "return_type": "Nat × Int", "input_type": "List Int"},
]


# ---------------------------------------------------------------------------
# Synthetic compositional expressions
# ---------------------------------------------------------------------------

def generate_synthetic_compositions() -> list[dict]:
    """Generate novel compositional (state, tactic) pairs programmatically.

    Fills known gaps: zipWith, partition, filterMap, lambda arithmetic.
    """
    pairs: list[dict] = []

    # --- zipWith with various binary ops ---
    binary_ops = [
        ("fun a b => a + b", "add"),
        ("fun a b => a - b", "sub"),
        ("fun a b => a * b", "mul"),
        ("fun a b => max a b", "max"),
        ("fun a b => min a b", "min"),
        ("fun a b => if a > b then a else b", "if_max"),
        ("fun a b => if a < b then a else b", "if_min"),
        ("· + ·", "dot_add"),
        ("· - ·", "dot_sub"),
        ("· * ·", "dot_mul"),
    ]
    for op_expr, tag in binary_ops:
        pairs.append({
            "state": "xs : List Int\nys : List Int\n⊢ List Int",
            "tactic": f"exact List.zipWith ({op_expr}) xs ys",
            "task": f"zipWith_{tag}",
            "return_type": "List Int",
            "input_type": "List Int, List Int",
        })
        # Also add zip+map variant for add/sub/mul
        if "fun a b =>" in op_expr:
            pairs.append({
                "state": "xs : List Int\nys : List Int\n⊢ List Int",
                "tactic": f"exact (xs.zip ys).map ({op_expr})",
                "task": f"zip_map_{tag}",
                "return_type": "List Int",
                "input_type": "List Int, List Int",
            })

    # --- partition with various predicates ---
    predicates = [
        ("fun x => decide (x > 0)", "gt0"),
        ("fun x => decide (x < 0)", "lt0"),
        ("fun x => decide (x ≥ 0)", "ge0"),
        ("fun x => decide (x ≤ 0)", "le0"),
        ("fun x => decide (x % 2 = 0)", "even"),
        ("fun x => decide (x % 2 ≠ 0)", "odd"),
        ("fun x => decide (x > 1)", "gt1"),
        ("fun x => decide (x ≠ 0)", "ne0"),
    ]
    for pred_expr, tag in predicates:
        pairs.append({
            "state": "xs : List Int\n⊢ List Int × List Int",
            "tactic": f"exact xs.partition ({pred_expr})",
            "task": f"partition_{tag}",
            "return_type": "List Int × List Int",
            "input_type": "List Int",
        })

    # --- filterMap with conditional expressions ---
    filtermap_exprs = [
        ("fun x => if decide (x > 0) then some x.toNat else none", "pos_toNat"),
        ("fun x => if decide (x ≥ 0) then some x.toNat else none", "nonneg_toNat"),
        ("fun x => if decide (x > 0) then some (x.toNat * x.toNat) else none", "pos_sq"),
        ("fun x => if decide (x % 2 = 0) then some x.toNat else none", "even_toNat"),
        ("fun x => if decide (x > 0) then some x else none", "pos_keep"),
        ("fun x => if decide (x < 0) then some (-x) else none", "neg_negate"),
    ]
    for expr, tag in filtermap_exprs:
        # filterMap : Int → Option Nat
        ret = "List Nat" if "toNat" in expr else "List Int"
        pairs.append({
            "state": "xs : List Int\n⊢ " + ret,
            "tactic": f"exact xs.filterMap ({expr})",
            "task": f"filterMap_{tag}",
            "return_type": ret,
            "input_type": "List Int",
        })

    # --- Lambda arithmetic in sub-goal contexts (map operations) ---
    map_lambdas = [
        ("fun x => x * 2", "mul2"),
        ("fun x => x * 3", "mul3"),
        ("fun x => x + 1", "add1"),
        ("fun x => x + 2", "add2"),
        ("fun x => x - 1", "sub1"),
        ("fun x => -x", "neg"),
        ("fun x => x * x", "sq"),
        ("fun x => x * x + 1", "sq_add1"),
        ("fun x => x + x", "double"),
        ("fun x => if decide (x > 0) then x else -x", "abs"),
    ]
    for lam, tag in map_lambdas:
        # In sub-goal context (after constructor on product type)
        pairs.append({
            "state": "xs : List Int\n⊢ List Int",
            "tactic": f"exact xs.map ({lam})",
            "task": f"subgoal_map_{tag}",
            "return_type": "List Int",
            "input_type": "List Int",
        })

    # --- foldl in sub-goal contexts ---
    foldl_ops = [
        ("fun a b => a + b", "0", "sum"),
        ("fun a b => a * b", "1", "prod"),
        ("min", "0", "min"),
        ("max", "0", "max"),
        ("fun a b => if a < b then a else b", "0", "if_min"),
        ("fun a b => if a > b then a else b", "0", "if_max"),
        ("fun a b => a + b * b", "0", "sum_sq"),
    ]
    for op, init, tag in foldl_ops:
        pairs.append({
            "state": "xs : List Int\n⊢ Int",
            "tactic": f"exact xs.foldl ({op}) {init}",
            "task": f"subgoal_foldl_{tag}",
            "return_type": "Int",
            "input_type": "List Int",
        })

    # --- Negated contains patterns (two-arg) ---
    pairs.append({
        "state": "xs : List Int\nys : List Int\n⊢ List Int",
        "tactic": "exact xs.filter (fun x => !(ys.contains x))",
        "task": "diff_xs_minus_ys",
        "return_type": "List Int",
        "input_type": "List Int, List Int",
    })
    pairs.append({
        "state": "xs : List Int\nys : List Int\n⊢ List Int",
        "tactic": "exact ys.filter (fun y => !(xs.contains y))",
        "task": "diff_ys_minus_xs",
        "return_type": "List Int",
        "input_type": "List Int, List Int",
    })
    pairs.append({
        "state": "xs : List Int\nys : List Int\n⊢ List Int",
        "tactic": "exact (xs.filter (fun x => !(ys.contains x))) ++ (ys.filter (fun y => !(xs.contains y)))",
        "task": "sym_diff",
        "return_type": "List Int",
        "input_type": "List Int, List Int",
    })

    # --- Flatten variants from List (List Int) ---
    pairs.append({
        "state": "xss : List (List Int)\n⊢ List Int",
        "tactic": "exact xss.flatten",
        "task": "synth_flatten",
        "return_type": "List Int",
        "input_type": "List (List Int)",
    })
    pairs.append({
        "state": "xss : List (List Int)\n⊢ Nat",
        "tactic": "exact xss.flatten.length",
        "task": "synth_flatten_length",
        "return_type": "Nat",
        "input_type": "List (List Int)",
    })
    pairs.append({
        "state": "xss : List (List Int)\n⊢ Int",
        "tactic": "exact xss.flatten.foldl (fun a b => a + b) 0",
        "task": "synth_flatten_sum",
        "return_type": "Int",
        "input_type": "List (List Int)",
    })

    # --- Nat input: List.range compositions ---
    range_compositions = [
        ("(List.range n).map (fun i => i * i)", "range_sq", "List Nat"),
        ("(List.range n).map (· ^ 2)", "range_sq_pow", "List Nat"),
        ("(List.range n).map (fun i => i + 1)", "range_succ", "List Nat"),
        ("(List.range n).map (fun i => i * 2)", "range_double", "List Nat"),
        ("(List.range n).map (fun i => i * 2 + 1)", "range_odd", "List Nat"),
        ("(List.range n).reverse", "range_rev", "List Nat"),
        ("(List.range n).foldl (· + ·) 0", "range_sum", "Nat"),
        ("(List.range n).length", "range_length", "Nat"),
    ]
    for expr, tag, ret_type in range_compositions:
        state_type = f"n : Nat\n⊢ {ret_type}"
        pairs.append({
            "state": state_type,
            "tactic": f"exact {expr}",
            "task": f"synth_{tag}",
            "return_type": ret_type,
            "input_type": "Nat",
        })

    # --- Two-arg compositions beyond zipWith ---
    pairs.append({
        "state": "xs : List Int\nys : List Int\n⊢ List Int",
        "tactic": "exact xs ++ ys",
        "task": "synth_concat",
        "return_type": "List Int",
        "input_type": "List Int, List Int",
    })
    pairs.append({
        "state": "xs : List Int\nys : List Int\n⊢ List Int",
        "tactic": "exact List.mergeSort (xs ++ ys)",
        "task": "synth_merge_sort",
        "return_type": "List Int",
        "input_type": "List Int, List Int",
    })
    pairs.append({
        "state": "xs : List Int\nys : List Int\n⊢ Int",
        "tactic": "exact (List.zipWith (fun a b => a * b) xs ys).foldl (fun a b => a + b) 0",
        "task": "synth_dot_product",
        "return_type": "Int",
        "input_type": "List Int, List Int",
    })
    pairs.append({
        "state": "xs : List Int\nys : List Int\n⊢ Nat",
        "tactic": "exact (xs ++ ys).length",
        "task": "synth_total_length",
        "return_type": "Nat",
        "input_type": "List Int, List Int",
    })

    # --- filter → fold chains (compositional 2-step) ---
    filter_preds = [
        ("fun x => decide (x > 0)", "gt0"),
        ("fun x => decide (x < 0)", "lt0"),
        ("fun x => decide (x ≥ 0)", "ge0"),
        ("fun x => decide (x % 2 = 0)", "even"),
        ("fun x => decide (x % 2 ≠ 0)", "odd"),
        ("fun x => decide (x ≠ 0)", "ne0"),
    ]
    fold_ops_int = [
        ("fun a b => a + b", "0", "sum"),
        ("max", "0", "max"),
        ("min", "0", "min"),
    ]
    for (pred, ptag) in filter_preds:
        for (op, init, otag) in fold_ops_int:
            pairs.append({
                "state": "xs : List Int\n⊢ Int",
                "tactic": f"exact (xs.filter ({pred})).foldl ({op}) {init}",
                "task": f"synth_filter_{ptag}_fold_{otag}",
                "return_type": "Int",
                "input_type": "List Int",
            })

    # --- filter → length chains (already many in base, add missing combos) ---
    for (pred, ptag) in [("fun x => decide (x ≤ 0)", "le0"), ("fun x => decide (x > 2)", "gt2")]:
        pairs.append({
            "state": "xs : List Int\n⊢ Nat",
            "tactic": f"exact (xs.filter ({pred})).length",
            "task": f"synth_filter_{ptag}_length",
            "return_type": "Nat",
            "input_type": "List Int",
        })

    # --- filter → map → fold chains (3-step compositions) ---
    for (pred, ptag) in [("fun x => decide (x > 0)", "gt0"), ("fun x => decide (x ≠ 0)", "ne0")]:
        for (lam, ltag) in [("fun x => x * x", "sq"), ("fun x => x * 2", "dbl")]:
            pairs.append({
                "state": "xs : List Int\n⊢ Int",
                "tactic": f"exact ((xs.filter ({pred})).map ({lam})).foldl (fun a b => a + b) 0",
                "task": f"synth_filter_{ptag}_map_{ltag}_sum",
                "return_type": "Int",
                "input_type": "List Int",
            })

    # --- map → fold chains ---
    for (lam, ltag) in [("fun x => x * x", "sq"), ("fun x => x + 1", "succ"), ("fun x => -x", "neg")]:
        pairs.append({
            "state": "xs : List Int\n⊢ Int",
            "tactic": f"exact (xs.map ({lam})).foldl (fun a b => a + b) 0",
            "task": f"synth_map_{ltag}_sum",
            "return_type": "Int",
            "input_type": "List Int",
        })
        pairs.append({
            "state": "xs : List Int\n⊢ Int",
            "tactic": f"exact (xs.map ({lam})).foldl max 0",
            "task": f"synth_map_{ltag}_max",
            "return_type": "Int",
            "input_type": "List Int",
        })

    # --- eraseDups compositions ---
    pairs.append({
        "state": "xs : List Int\n⊢ List Int",
        "tactic": "exact xs.eraseDups",
        "task": "synth_eraseDups",
        "return_type": "List Int",
        "input_type": "List Int",
    })
    pairs.append({
        "state": "xs : List Int\n⊢ Nat",
        "tactic": "exact xs.eraseDups.length",
        "task": "synth_eraseDups_length",
        "return_type": "Nat",
        "input_type": "List Int",
    })
    pairs.append({
        "state": "xs : List Int\n⊢ List Int",
        "tactic": "exact List.mergeSort xs.eraseDups",
        "task": "synth_sort_dedup",
        "return_type": "List Int",
        "input_type": "List Int",
    })
    pairs.append({
        "state": "xs : List Int\n⊢ List Int",
        "tactic": "exact (xs.eraseDups).reverse",
        "task": "synth_dedup_rev",
        "return_type": "List Int",
        "input_type": "List Int",
    })
    pairs.append({
        "state": "xs : List Int\n⊢ Nat",
        "tactic": "exact (xs.filter (fun x => decide (x > 0))).eraseDups.length",
        "task": "synth_pos_dedup_length",
        "return_type": "Nat",
        "input_type": "List Int",
    })

    # --- sort compositions ---
    pairs.append({
        "state": "xs : List Int\n⊢ List Int",
        "tactic": "exact (List.mergeSort xs).reverse",
        "task": "synth_sort_rev",
        "return_type": "List Int",
        "input_type": "List Int",
    })
    pairs.append({
        "state": "xs : List Int\n⊢ List Int",
        "tactic": "exact List.mergeSort (xs.filter (fun x => decide (x > 0)))",
        "task": "synth_sort_pos",
        "return_type": "List Int",
        "input_type": "List Int",
    })
    pairs.append({
        "state": "xs : List Int\n⊢ List Int",
        "tactic": "exact List.mergeSort (xs.filter (fun x => decide (x % 2 = 0)))",
        "task": "synth_sort_even",
        "return_type": "List Int",
        "input_type": "List Int",
    })

    # --- Sprint 17: Tuple-accumulator fold patterns ---

    # Binary recurrences: (a, b) → (b, f a b)
    binary_recurrences = [
        ("(fun (a, b) _ => (b, a + b))", "(0, 1)", "fib"),
        ("(fun (a, b) _ => (b, a * b))", "(1, 1)", "mul_recur"),
        ("(fun (a, b) _ => (b, max a b))", "(0, 0)", "max_recur"),
        ("(fun (a, b) _ => (b, a + b))", "(1, 1)", "lucas"),
        ("(fun (a, b) _ => (b, a + b + 1))", "(0, 0)", "shifted"),
    ]
    for fold_fn, init, tag in binary_recurrences:
        pairs.append({
            "state": "n : Nat\n⊢ Nat",
            "tactic": f"exact ((List.range n).foldl {fold_fn} {init}).1",
            "task": f"synth_tuple_fold_{tag}_fst",
            "return_type": "Nat",
            "input_type": "Nat",
        })
        pairs.append({
            "state": "n : Nat\n⊢ Nat",
            "tactic": f"exact ((List.range n).foldl {fold_fn} {init}).2",
            "task": f"synth_tuple_fold_{tag}_snd",
            "return_type": "Nat",
            "input_type": "Nat",
        })

    # Indexed accumulator folds
    indexed_accum = [
        ("fun acc i => acc * (i + 1)", "1", "factorial"),
        ("fun acc i => acc + (i + 1)", "0", "triangle_sum"),
        ("fun acc i => acc + i * i", "0", "sum_sq"),
        ("fun acc i => acc + i", "0", "sum_range"),
        ("fun acc _ => acc * 2", "1", "pow2"),
        ("fun acc _ => acc * 3", "1", "pow3"),
    ]
    for fold_fn, init, tag in indexed_accum:
        pairs.append({
            "state": "n : Nat\n⊢ Nat",
            "tactic": f"exact (List.range n).foldl ({fold_fn}) {init}",
            "task": f"synth_idx_fold_{tag}",
            "return_type": "Nat",
            "input_type": "Nat",
        })

    # Nested range compositions
    nested_range = [
        ("fun i => (List.range (i + 1)).foldl (· + ·) 0", "List Nat", "triangular"),
        ("fun i => (List.range i).foldl (fun a _ => a * 2) 1", "List Nat", "pow2_list"),
        ("fun i => (List.range (i + 1)).foldl (· * ·) 1", "List Nat", "factorial_list"),
        ("fun i => (List.range i).length", "List Nat", "identity_list"),
    ]
    for map_fn, ret_type, tag in nested_range:
        pairs.append({
            "state": f"n : Nat\n⊢ {ret_type}",
            "tactic": f"exact (List.range n).map ({map_fn})",
            "task": f"synth_nested_{tag}",
            "return_type": ret_type,
            "input_type": "Nat",
        })

    # Tuple-accumulator folds on List Int input
    list_tuple_folds = [
        ("fun (s, c) x => (s + x, c + 1)", "(0, 0)", "Int × Nat", "sum_count"),
        ("fun (mn, mx) x => (min mn x, max mx x)", "(0, 0)", "Int × Int", "minmax"),
        ("fun (s, c) x => (s + x * x, c + 1)", "(0, 0)", "Int × Nat", "sumsq_count"),
    ]
    for fold_fn, init, ret_type, tag in list_tuple_folds:
        pairs.append({
            "state": f"xs : List Int\n⊢ {ret_type}",
            "tactic": f"exact xs.foldl ({fold_fn}) {init}",
            "task": f"synth_list_tuple_{tag}",
            "return_type": ret_type,
            "input_type": "List Int",
        })

    # FibList-style: accumulate list + state
    pairs.append({
        "state": "n : Nat\n⊢ List Nat",
        "tactic": "exact ((List.range n).foldl (fun (acc, ab) _ => (acc ++ [ab.1], (ab.2, ab.1 + ab.2))) ([], (0, 1))).1",
        "task": "synth_fib_list",
        "return_type": "List Nat",
        "input_type": "Nat",
    })

    # --- Sprint 21: Expanded tuple-accumulator folds over range ---
    # Category B: More init/op/projection variations for fibonacci-class tasks
    s21_binary_recurrences = [
        # (fold_fn, init, tag) — generates both .1 and .2
        ("(fun (a, b) _ => (b, a + b))", "(0, 1)", "fib_01"),       # fibonacci canonical
        ("(fun (a, b) _ => (b, a + b))", "(1, 0)", "fib_10"),       # fibonacci alt init
        ("(fun (a, b) _ => (b, a + b))", "(0, 0)", "fib_00"),       # degenerate fib
        ("(fun (a, b) _ => (b, a + b))", "(2, 1)", "fib_21"),       # pell-like
        ("(fun (a, b) _ => (a + b, a))", "(0, 1)", "fib_swap"),     # swapped update
        ("(fun (a, b) _ => (a + b, a))", "(1, 0)", "fib_swap10"),
        ("(fun (a, b) _ => (b, 2 * a + b))", "(0, 1)", "pell"),     # Pell numbers
        ("(fun (a, b) _ => (b, a + 2 * b))", "(0, 1)", "jacobsthal"),  # Jacobsthal
        ("(fun (a, b) _ => (b, a * b))", "(1, 2)", "mul_12"),       # multiplicative
        ("(fun (a, b) _ => (b, a * b))", "(2, 1)", "mul_21"),
        ("(fun (a, b) _ => (b, a + b * 2))", "(0, 1)", "weighted"),
        ("(fun (a, b) _ => (b, a + b + 1))", "(0, 1)", "shifted_01"),
        ("(fun (a, b) _ => (b, a + b + 1))", "(1, 0)", "shifted_10"),
        ("(fun (a, b) _ => (b, max a b + 1))", "(0, 1)", "max_plus"),
    ]
    for fold_fn, init, tag in s21_binary_recurrences:
        pairs.append({
            "state": "n : Nat\n⊢ Nat",
            "tactic": f"exact ((List.range n).foldl {fold_fn} {init}).1",
            "task": f"s21_tuple_fold_{tag}_fst",
            "return_type": "Nat",
            "input_type": "Nat",
        })
        pairs.append({
            "state": "n : Nat\n⊢ Nat",
            "tactic": f"exact ((List.range n).foldl {fold_fn} {init}).2",
            "task": f"s21_tuple_fold_{tag}_snd",
            "return_type": "Nat",
            "input_type": "Nat",
        })

    # --- Sprint 21: More indexed accumulator folds ---
    s21_indexed_accum = [
        ("fun acc i => acc + (i + 1) * (i + 1)", "0", "sum_sq_shifted"),
        ("fun acc i => acc + (i + 1) * (i + 2)", "0", "sum_prod_consec"),
        ("fun acc _ => acc + 1", "0", "identity"),
        ("fun acc i => acc + 2 * i + 1", "0", "odd_sum"),  # sum of odd numbers = n^2
        ("fun acc _ => acc * 2 + 1", "0", "mersenne"),
        ("fun acc i => if i % 2 = 0 then acc + (i + 1) else acc", "0", "sum_odd_idx"),
        ("fun acc i => acc + (i + 1)", "0", "triangle_alt"),
    ]
    for fold_fn, init, tag in s21_indexed_accum:
        pairs.append({
            "state": "n : Nat\n⊢ Nat",
            "tactic": f"exact (List.range n).foldl ({fold_fn}) {init}",
            "task": f"s21_idx_fold_{tag}",
            "return_type": "Nat",
            "input_type": "Nat",
        })

    # --- Sprint 21: Tuple-accumulator folds on List (Category A) ---
    s21_list_tuple_folds = [
        # (fold_fn, init, ret_type, tag)
        ("fun (s, p) x => (s + x, p * x)", "(0, 1)", "Int × Int", "sum_prod"),
        ("fun (s, c) x => (s + x, c + 1)", "(0, 0)", "Int × Nat", "sum_count_alt"),
        ("fun (mn, mx) x => (min mn x, max mx x)", "(0, 0)", "Int × Int", "minmax_alt"),
        ("fun (s, p) x => (s + x, if decide (x > 0) then p + 1 else p)", "(0, 0)", "Int × Nat", "sum_poscount"),
        ("fun (a, b) x => (a + x, b - x)", "(0, 0)", "Int × Int", "sum_diff"),
        ("fun (a, b) x => (max a x, min b x)", "(0, 0)", "Int × Int", "max_min"),
    ]
    for fold_fn, init, ret_type, tag in s21_list_tuple_folds:
        pairs.append({
            "state": f"xs : List Int\n⊢ {ret_type}",
            "tactic": f"exact xs.foldl ({fold_fn}) {init}",
            "task": f"s21_list_tuple_{tag}",
            "return_type": ret_type,
            "input_type": "List Int",
        })
        # Also with .1 and .2 projection (sub-goal pattern)
        t1, t2 = ret_type.split(" × ")
        pairs.append({
            "state": f"xs : List Int\n⊢ {t1.strip()}",
            "tactic": f"exact (xs.foldl ({fold_fn}) {init}).1",
            "task": f"s21_list_tuple_{tag}_fst",
            "return_type": t1.strip(),
            "input_type": "List Int",
        })
        pairs.append({
            "state": f"xs : List Int\n⊢ {t2.strip()}",
            "tactic": f"exact (xs.foldl ({fold_fn}) {init}).2",
            "task": f"s21_list_tuple_{tag}_snd",
            "return_type": t2.strip(),
            "input_type": "List Int",
        })

    # --- Sprint 21: Expanded nested range compositions (Category D) ---
    s21_nested_range = [
        # (map_fn, ret_type, tag)
        ("fun i => (List.range (i + 1)).foldl (· + ·) 0", "List Nat", "triangular_alt"),
        ("fun i => (List.range i).foldl (fun a _ => a * 2) 1", "List Nat", "pow2_list_alt"),
        ("fun i => (List.range (i + 1)).foldl (· * ·) 1", "List Nat", "factorial_list_alt"),
        ("fun i => (List.range i).foldl (fun a _ => a * 3) 1", "List Nat", "pow3_list"),
        ("fun i => (List.range (i + 1)).foldl (fun a _ => a + 2) 0", "List Nat", "evens_list"),
        ("fun i => (List.range i).foldl (fun a b => a + b + 1) 0", "List Nat", "shifted_tri"),
        ("fun i => i * i", "List Nat", "squares"),
        ("fun i => i * (i + 1) / 2", "List Nat", "triangular_closed"),
        ("fun i => i * (i + 1)", "List Nat", "pronic"),
        ("fun i => 2 ^ i", "List Nat", "pow2_direct"),
        ("fun i => i + 1", "List Nat", "naturals"),
        ("fun i => 2 * i + 1", "List Nat", "odds"),
        ("fun i => 2 * i", "List Nat", "evens_direct"),
    ]
    for map_fn, ret_type, tag in s21_nested_range:
        pairs.append({
            "state": f"n : Nat\n⊢ {ret_type}",
            "tactic": f"exact (List.range n).map ({map_fn})",
            "task": f"s21_nested_{tag}",
            "return_type": ret_type,
            "input_type": "Nat",
        })

    # --- Sprint 21: Tuple-append accumulator patterns (Category C) ---
    s21_tuple_append = [
        # (fold_fn, init, tag) — all return List Nat via .1
        ("(fun (acc, v) _ => (acc ++ [v], v + 1))", "([], 0)", "counter"),
        ("(fun (acc, v) _ => (acc ++ [v], v * 2))", "([], 1)", "powers2_append"),
        ("(fun (acc, v) _ => (acc ++ [v], v * 3))", "([], 1)", "powers3_append"),
        ("(fun (acc, v) _ => (acc ++ [v], v + 2))", "([], 0)", "evens_append"),
        ("(fun (acc, v) _ => (acc ++ [v], v + v))", "([], 1)", "doubling_append"),
        ("(fun (acc, ab) _ => (acc ++ [ab.1], (ab.2, ab.1 + ab.2)))", "([], (0, 1))", "fib_append"),
        ("(fun (acc, ab) _ => (acc ++ [ab.1], (ab.2, ab.1 + ab.2)))", "([], (1, 1))", "lucas_append"),
        ("(fun (acc, ab) _ => (acc ++ [ab.1 + ab.2], (ab.2, ab.1 + ab.2)))", "([], (0, 1))", "fib_sum_append"),
    ]
    for fold_fn, init, tag in s21_tuple_append:
        pairs.append({
            "state": "n : Nat\n⊢ List Nat",
            "tactic": f"exact ((List.range n).foldl {fold_fn} {init}).1",
            "task": f"s21_append_{tag}",
            "return_type": "List Nat",
            "input_type": "Nat",
        })

    # --- Sprint 21: Projection patterns (Category E) ---
    # Teaching .1/.2 on tuple-fold results with case prefix
    s21_projections = [
        # (state, tactic, tag) — case-prefixed sub-goal projections
        ("case fst\nxs : List Int\n⊢ Int",
         "exact (xs.foldl (fun (s, c) x => (s + x, c + 1)) (0, 0)).1",
         "proj_sum_from_tuple"),
        ("case snd\nxs : List Int\n⊢ Nat",
         "exact (xs.foldl (fun (s, c) x => (s + x, c + 1)) (0, 0)).2",
         "proj_count_from_tuple"),
        ("case fst\nxs : List Int\n⊢ Int",
         "exact (xs.foldl (fun (mn, mx) x => (min mn x, max mx x)) (0, 0)).1",
         "proj_min_from_tuple"),
        ("case snd\nxs : List Int\n⊢ Int",
         "exact (xs.foldl (fun (mn, mx) x => (min mn x, max mx x)) (0, 0)).2",
         "proj_max_from_tuple"),
    ]
    for state, tactic, tag in s21_projections:
        pairs.append({
            "state": state,
            "tactic": tactic,
            "task": f"s21_{tag}",
            "return_type": state.split("⊢")[-1].strip(),
            "input_type": "List Int",
        })

    # --- Sprint 21: Simple direct folds (non-case-prefixed, for sub-goal learning) ---
    # These ensure the model can generate simple folds for any List Int → scalar goal
    simple_folds = [
        ("xs : List Int\n⊢ Int", "exact xs.foldl (· + ·) 0", "Int", "simple_sum"),
        ("xs : List Int\n⊢ Int", "exact xs.foldl (· * ·) 1", "Int", "simple_prod"),
        ("xs : List Int\n⊢ Int", "exact xs.foldl (fun a b => a + b) 0", "Int", "simple_sum_lambda"),
        ("xs : List Int\n⊢ Int", "exact xs.foldl (fun a b => a * b) 1", "Int", "simple_prod_lambda"),
        ("xs : List Int\n⊢ Int", "exact xs.foldl max 0", "Int", "simple_max"),
        ("xs : List Int\n⊢ Int", "exact xs.foldl min 0", "Int", "simple_min"),
        ("xs : List Int\n⊢ Nat", "exact xs.length", "Nat", "simple_length"),
        ("xs : List Int\n⊢ Nat", "exact (xs.filter (fun x => decide (x > 0))).length", "Nat", "simple_pos_count"),
        ("xs : List Int\n⊢ Nat", "exact (xs.filter (fun x => decide (x < 0))).length", "Nat", "simple_neg_count"),
        ("xs : List Int\n⊢ Nat", "exact (xs.filter (fun x => decide (x % 2 = 0))).length", "Nat", "simple_even_count"),
        ("xs : List Int\n⊢ List Int", "exact xs.filter (fun x => decide (x % 2 = 0))", "List Int", "simple_even_filter"),
        ("xs : List Int\n⊢ List Int", "exact xs.filter (fun x => decide (x % 2 ≠ 0))", "List Int", "simple_odd_filter"),
    ]
    for state, tactic, ret_type, tag in simple_folds:
        pairs.append({
            "state": state,
            "tactic": tactic,
            "task": f"s21_{tag}",
            "return_type": ret_type,
            "input_type": "List Int",
        })

    # Range + foldl combos (Nat return)
    pairs.append({
        "state": "n : Nat\n⊢ Nat",
        "tactic": "exact (List.range n).foldl (· + ·) 0",
        "task": "synth_range_sum_nat",
        "return_type": "Nat",
        "input_type": "Nat",
    })
    pairs.append({
        "state": "n : Nat\n⊢ Nat",
        "tactic": "exact (List.range n).foldl (· * ·) 1",
        "task": "synth_range_prod_nat",
        "return_type": "Nat",
        "input_type": "Nat",
    })

    # --- scanl compositions ---
    pairs.append({
        "state": "xs : List Int\n⊢ List Int",
        "tactic": "exact (List.scanl (fun a b => a + b) 0 xs).drop 1",
        "task": "synth_prefix_sums",
        "return_type": "List Int",
        "input_type": "List Int",
    })
    pairs.append({
        "state": "xs : List Int\n⊢ List Int",
        "tactic": "exact (List.scanl max 0 xs).drop 1",
        "task": "synth_running_max",
        "return_type": "List Int",
        "input_type": "List Int",
    })
    pairs.append({
        "state": "xs : List Int\n⊢ List Int",
        "tactic": "exact (List.scanl min 0 xs).drop 1",
        "task": "synth_running_min",
        "return_type": "List Int",
        "input_type": "List Int",
    })
    pairs.append({
        "state": "xs : List Int\n⊢ List Int",
        "tactic": "exact (List.scanl (fun a b => a * b) 1 xs).drop 1",
        "task": "synth_running_prod",
        "return_type": "List Int",
        "input_type": "List Int",
    })

    # --- List (List Int) operations ---
    pairs.append({
        "state": "xss : List (List Int)\n⊢ List Int",
        "tactic": "exact xss.map List.length |>.map Int.ofNat",
        "task": "synth_sublengths",
        "return_type": "List Int",
        "input_type": "List (List Int)",
    })
    pairs.append({
        "state": "xss : List (List Int)\n⊢ Nat",
        "tactic": "exact (xss.map List.length).foldl max 0",
        "task": "synth_max_sublen",
        "return_type": "Nat",
        "input_type": "List (List Int)",
    })
    pairs.append({
        "state": "xss : List (List Int)\n⊢ Nat",
        "tactic": "exact xss.length",
        "task": "synth_num_sublists",
        "return_type": "Nat",
        "input_type": "List (List Int)",
    })
    pairs.append({
        "state": "xss : List (List Int)\n⊢ List Int",
        "tactic": "exact xss.map (fun l => l.foldl (fun a b => a + b) 0)",
        "task": "synth_sublist_sums",
        "return_type": "List Int",
        "input_type": "List (List Int)",
    })

    # --- zipWith → fold chains (two-arg → scalar) ---
    for (op, tag) in [("fun a b => a + b", "add"), ("fun a b => a * b", "mul"), ("fun a b => a - b", "sub")]:
        pairs.append({
            "state": "xs : List Int\nys : List Int\n⊢ Int",
            "tactic": f"exact (List.zipWith ({op}) xs ys).foldl (fun a b => a + b) 0",
            "task": f"synth_zipWith_{tag}_sum",
            "return_type": "Int",
            "input_type": "List Int, List Int",
        })

    # --- Product type: exact (a, b) patterns ---
    pairs.append({
        "state": "xs : List Int\n⊢ Nat × Nat",
        "tactic": "exact ((xs.filter (fun x => decide (x > 0))).length, (xs.filter (fun x => decide (x ≤ 0))).length)",
        "task": "synth_count_pos_neg",
        "return_type": "Nat × Nat",
        "input_type": "List Int",
    })
    pairs.append({
        "state": "xs : List Int\n⊢ Nat × Int",
        "tactic": "exact ((xs.filter (fun x => decide (x > 0))).length, (xs.filter (fun x => decide (x > 0))).foldl (fun a b => a + b) 0)",
        "task": "synth_count_sum_pos",
        "return_type": "Nat × Int",
        "input_type": "List Int",
    })
    pairs.append({
        "state": "xs : List Int\n⊢ Int × Int",
        "tactic": "exact (xs.foldl min 0, xs.foldl max 0)",
        "task": "synth_min_max",
        "return_type": "Int × Int",
        "input_type": "List Int",
    })
    pairs.append({
        "state": "xs : List Int\n⊢ List Int × List Int",
        "tactic": "exact (xs.map (fun x => x * 2), xs.map (fun x => x + 1))",
        "task": "synth_split_double_succ",
        "return_type": "List Int × List Int",
        "input_type": "List Int",
    })
    pairs.append({
        "state": "xs : List Int\n⊢ List Int × List Int",
        "tactic": "exact (xs.filter (fun x => decide (x > 0)), xs.filter (fun x => decide (x ≤ 0)))",
        "task": "synth_split_pos_neg",
        "return_type": "List Int × List Int",
        "input_type": "List Int",
    })
    pairs.append({
        "state": "xs : List Int\n⊢ List Int × List Int",
        "tactic": "exact (xs.filter (fun x => decide (x % 2 = 0)), xs.filter (fun x => decide (x % 2 ≠ 0)))",
        "task": "synth_split_even_odd",
        "return_type": "List Int × List Int",
        "input_type": "List Int",
    })

    return pairs


# ---------------------------------------------------------------------------
# Oracle batch verification
# ---------------------------------------------------------------------------

def _batch_verify_compositions(
    pairs: list[dict],
    lean_project: Path,
    verbose: bool = False,
    batch_size: int = 40,
    timeout: int = 120,
) -> list[dict]:
    """Verify that synthetic tactic expressions compile in Lean.

    Returns only the pairs that compile successfully. We just check that
    `def _fn (params) : RetType := expr` compiles, not test-case correctness
    (these are training pairs, not benchmark solutions).
    """
    verified: list[dict] = []

    for batch_start in range(0, len(pairs), batch_size):
        batch = pairs[batch_start:batch_start + batch_size]
        lines = [LEAN_IMPORTS]

        for i, pair in enumerate(batch):
            fn = f"_gc_verify_{i}"
            # Build param list from input_type
            input_type = pair["input_type"]
            if input_type == "List (List Int)":
                params = "xss : List (List Int)"
            elif input_type == "Nat":
                params = "n : Nat"
            elif input_type == "List Int, List Int":
                params = "xs : List Int) (ys : List Int"
            else:
                params = "xs : List Int"

            tactic = pair["tactic"]
            expr = tactic[len("exact "):] if tactic.startswith("exact ") else None

            if expr is not None:
                lines.append(f"def {fn} ({params}) : {pair['return_type']} :=")
                lines.append(f"  {expr}")
            else:
                # Tactic mode (e.g., constructor)
                lines.append(f"def {fn} ({params}) : {pair['return_type']} := by")
                lines.append(f"  {tactic}")
                lines.append(f"  sorry")  # will fail but we just want to check tactic parses
            lines.append("")

        eval_file = lean_project / "FormalNav" / "_GCVerify.lean"
        try:
            eval_file.write_text("\n".join(lines))
            result = subprocess.run(
                ["lake", "env", "lean", str(eval_file)],
                cwd=str(lean_project),
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode == 0:
                # All compiled — add all
                verified.extend(batch)
                if verbose:
                    print(f"  Batch {batch_start//batch_size}: all {len(batch)} verified")
            else:
                # Some failed — verify individually
                stderr = result.stderr
                if verbose:
                    print(f"  Batch {batch_start//batch_size}: batch failed, checking individually...")
                for i, pair in enumerate(batch):
                    fn = f"_gc_verify_solo"
                    input_type = pair["input_type"]
                    if input_type == "List (List Int)":
                        params = "xss : List (List Int)"
                    elif input_type == "Nat":
                        params = "n : Nat"
                    elif input_type == "List Int, List Int":
                        params = "xs : List Int) (ys : List Int"
                    else:
                        params = "xs : List Int"

                    tactic = pair["tactic"]
                    expr = tactic[len("exact "):] if tactic.startswith("exact ") else None

                    solo_lines = [LEAN_IMPORTS]
                    if expr is not None:
                        solo_lines.append(f"def {fn} ({params}) : {pair['return_type']} :=")
                        solo_lines.append(f"  {expr}")
                    else:
                        continue  # skip tactic-mode in solo verification

                    eval_file.write_text("\n".join(solo_lines))
                    solo_result = subprocess.run(
                        ["lake", "env", "lean", str(eval_file)],
                        cwd=str(lean_project),
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if solo_result.returncode == 0:
                        verified.append(pair)
                    elif verbose:
                        print(f"    FAIL: {pair['task']}: {solo_result.stderr[:100]}")

        except subprocess.TimeoutExpired:
            if verbose:
                print(f"  Batch {batch_start//batch_size}: timeout")

        finally:
            if eval_file.exists():
                eval_file.unlink()

    return verified


# ---------------------------------------------------------------------------
# Main collection pipeline
# ---------------------------------------------------------------------------

def collect_from_expressions(expressions_path: Path) -> list[TrainingPair]:
    """Load all pairs from expressions_v2.jsonl."""
    pairs: list[TrainingPair] = []
    with open(expressions_path) as f:
        for line in f:
            entry = json.loads(line)
            pairs.append(TrainingPair(
                state=entry["state"],
                tactic=entry["tactic"],
                source="reference",
                task_name=entry["tag"],
                return_type=entry["return_type"],
                input_type=entry["input_type"],
            ))
    print(f"  Expressions: {len(pairs)} pairs from {expressions_path.name}")
    return pairs


def collect_hand_crafted() -> list[TrainingPair]:
    """Convert hand-crafted references to TrainingPair format."""
    pairs: list[TrainingPair] = []
    for ref in HAND_CRAFTED_REFERENCES:
        pairs.append(TrainingPair(
            state=ref["state"],
            tactic=ref["tactic"],
            source="hand_crafted",
            task_name=ref["task"],
            return_type=ref["return_type"],
            input_type=ref["input_type"],
        ))
    print(f"  Hand-crafted: {len(pairs)} pairs")
    return pairs


def collect_synthetic(
    lean_project: Path,
    skip_verify: bool = False,
    verbose: bool = False,
) -> list[TrainingPair]:
    """Generate and optionally verify synthetic compositional pairs."""
    raw_pairs = generate_synthetic_compositions()
    print(f"  Synthetic: {len(raw_pairs)} raw pairs generated")

    if skip_verify:
        verified = raw_pairs
        print(f"  Synthetic: skipping verification (all {len(raw_pairs)} kept)")
    else:
        # Only verify `exact` pairs (tactic-mode like `constructor` can't be batch-verified easily)
        exact_pairs = [p for p in raw_pairs if p["tactic"].startswith("exact ")]
        other_pairs = [p for p in raw_pairs if not p["tactic"].startswith("exact ")]
        verified_exact = _batch_verify_compositions(exact_pairs, lean_project, verbose=verbose)
        verified = verified_exact + other_pairs
        print(f"  Synthetic: {len(verified_exact)}/{len(exact_pairs)} exact pairs verified, "
              f"{len(other_pairs)} tactic-mode kept")

    pairs: list[TrainingPair] = []
    for p in verified:
        pairs.append(TrainingPair(
            state=p["state"],
            tactic=p["tactic"],
            source="synthetic",
            task_name=p["task"],
            return_type=p["return_type"],
            input_type=p["input_type"],
        ))
    return pairs


def main():
    parser = argparse.ArgumentParser(description="Collect goal-conditioned training data")
    parser.add_argument("--expressions", type=str, default="synth/expressions_v2.jsonl",
                        help="Expressions JSONL (from enumerate.py)")
    parser.add_argument("--output-train", type=str, default="train/goal_cond_train.jsonl")
    parser.add_argument("--output-val", type=str, default="train/goal_cond_val.jsonl")
    parser.add_argument("--lean-project", type=str, default=None)
    parser.add_argument("--split", type=float, default=0.8)
    parser.add_argument("--n-augment", type=int, default=3,
                        help="Number of variable name variants per pair")
    parser.add_argument("--multi-step", action="store_true", default=True,
                        help="Include multi-step product type decompositions")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Skip oracle verification of synthetic compositions")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent.parent
    expressions_path = script_dir / args.expressions
    lean_project = Path(args.lean_project) if args.lean_project else script_dir / "env" / "FormalNav"
    train_path = script_dir / args.output_train
    val_path = script_dir / args.output_val

    t0 = time.time()
    all_pairs: list[TrainingPair] = []

    # 1. Existing expressions (1435 pairs)
    expr_pairs = collect_from_expressions(expressions_path)
    all_pairs.extend(expr_pairs)

    # 2. Hand-crafted references (~35 pairs)
    hc_pairs = collect_hand_crafted()
    all_pairs.extend(hc_pairs)

    # 3. Synthetic compositions (~100+ pairs)
    synth_pairs = collect_synthetic(lean_project, skip_verify=args.skip_verify, verbose=args.verbose)
    all_pairs.extend(synth_pairs)

    # 4. Multi-step product type decompositions
    if args.multi_step:
        ms_pairs = synthesize_multistep_pairs(all_pairs, verbose=args.verbose)
        all_pairs.extend(ms_pairs)
        print(f"  Multi-step: +{len(ms_pairs)} pairs")

    print(f"\nPre-augmentation: {len(all_pairs)} pairs")
    from collections import Counter
    src_dist = Counter(p.source for p in all_pairs)
    rt_dist = Counter(p.return_type for p in all_pairs)
    print(f"  Sources: {dict(src_dist)}")
    print(f"  Return types: {dict(rt_dist)}")

    # 5. Augment with variable name variants
    if args.n_augment > 0:
        aug_pairs = augment_pairs(all_pairs, n_variants=args.n_augment, seed=args.seed)
        all_pairs.extend(aug_pairs)
        print(f"  Augmented: +{len(aug_pairs)} pairs → {len(all_pairs)} total")

    # 6. Write train/val split
    n_train, n_val = write_training_data(
        all_pairs, train_path, val_path, split_ratio=args.split, seed=args.seed,
    )

    elapsed = time.time() - t0
    print(f"\nOutput:")
    print(f"  Train: {train_path} ({n_train} pairs)")
    print(f"  Val:   {val_path} ({n_val} pairs)")
    print(f"  Total unique: {n_train + n_val}")
    print(f"  Elapsed: {elapsed:.1f}s")

    # Write stats
    stats = {
        "total_raw": len(all_pairs),
        "total_unique": n_train + n_val,
        "n_train": n_train,
        "n_val": n_val,
        "sources": dict(src_dist),
        "return_types": dict(rt_dist),
        "n_hand_crafted": len(hc_pairs),
        "n_synthetic": len(synth_pairs),
        "n_augmented": len(aug_pairs) if args.n_augment > 0 else 0,
        "elapsed_seconds": round(elapsed, 1),
    }
    stats_path = script_dir / "synth" / "goal_cond_stats.json"
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=2)
    print(f"  Stats: {stats_path}")


if __name__ == "__main__":
    main()
