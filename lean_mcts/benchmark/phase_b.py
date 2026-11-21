"""S30 Phase B — Retrieval-Augmented Generation.

Tests whether the 404-pair corpus carries enough signal to solve
held_out_12 via retrieval alone, bypassing the LoRA compression that
S29 showed was lossy. For each target task:

    1. Embed the goal state with Qwen2.5-Coder-1.5B's last-token
       hidden state (decoder-only pooling).
    2. Retrieve top-k most-similar corpus pairs by cosine similarity.
    3. Inject them as a few-shot preamble (replacing Phase A's
       generic 5-pair set).
    4. Generate 8 candidate tactics with Qwen-1.5B base (no LoRA)
       at temperature 0.2.
    5. Oracle-verify each via the Pantograph daemon.

The plan's configs:

    B-1  BGE-code-equivalent (Qwen hidden states here), k=10   — primary
    B-2  Qwen hidden states, k=5                               — signal-focus ablation
    B-3  Oracle retrieval (hand-picked pairs from b3_oracle_selections.json)  — upper bound

B-3 is fed by sprint/benchmark/results/b3_oracle_selections.json which
maps task_name → list of corpus pair indices, hand-selected by the
experimenter without peeking at test cases. The selection protocol is
documented in sprint30.md §"B-3 oracle retrieval protocol".

Usage:
    # Build the retrieval index once (cached on disk)
    python -m lean_mcts.benchmark.phase_b --build-index

    # Run B-1 / B-2
    python -m lean_mcts.benchmark.phase_b --config B1 \\
        --out sprint/benchmark/results/phase_b_b1.json

    python -m lean_mcts.benchmark.phase_b --config B2 \\
        --out sprint/benchmark/results/phase_b_b2.json

    # Run B-3 (requires b3_oracle_selections.json)
    python -m lean_mcts.benchmark.phase_b --config B3 \\
        --oracle-selections sprint/benchmark/results/b3_oracle_selections.json \\
        --out sprint/benchmark/results/phase_b_b3.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from lean_mcts.benchmark.phase_a import (
    build_goal_state,
    generate_local,
    load_local_model,
    summarize,
    verify_task,
)
from lean_mcts.benchmark.tasks import get_tasks

DEFAULT_CORPUS = [
    Path("lean_mcts/train/diverse_train_v2.jsonl"),
    Path("lean_mcts/train/diverse_val_v2.jsonl"),
]
DEFAULT_INDEX = Path("sprint/benchmark/results/phase_b_index.npz")
DEFAULT_BASE_MODEL = "Qwen/Qwen2.5-Coder-1.5B"


def load_corpus(paths: list[Path]) -> list[dict]:
    """Load corpus entries, tagging each with a stable global index."""
    pairs = []
    for p in paths:
        for i, line in enumerate(p.read_text().splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry["_source_file"] = p.name
            entry["_source_idx"] = i
            pairs.append(entry)
    # Stable global index assigned after concatenation
    for i, p in enumerate(pairs):
        p["_global_idx"] = i
    return pairs


def embed_texts(model, tokenizer, texts: list[str],
                max_length: int = 256, batch_size: int = 16) -> np.ndarray:
    """Compute last-token hidden-state embeddings for a batch of texts.

    Decoder-only pooling: the last hidden layer, last non-pad token.
    L2-normalized so cosine similarity == inner product.
    """
    import torch

    all_embeds = []
    model.eval()
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        enc = tokenizer(
            batch, return_tensors="pt",
            padding=True, truncation=True, max_length=max_length,
        ).to(model.device)
        with torch.no_grad():
            out = model(
                **enc,
                output_hidden_states=True,
                return_dict=True,
            )
        last_hidden = out.hidden_states[-1]  # [B, T, D]
        # Index of the last real (non-pad) token per row
        attn = enc["attention_mask"]
        last_idx = attn.sum(dim=1) - 1  # [B]
        pooled = last_hidden[torch.arange(last_hidden.size(0)), last_idx]  # [B, D]
        pooled = torch.nn.functional.normalize(pooled.float(), dim=-1)
        all_embeds.append(pooled.cpu().numpy())
    return np.concatenate(all_embeds, axis=0)


def build_index(corpus_paths: list[Path], index_path: Path,
                base_model: str = DEFAULT_BASE_MODEL) -> dict:
    """Load the corpus, embed every entry's goal state, persist to disk."""
    pairs = load_corpus(corpus_paths)
    print(f"[phase_b] loaded {len(pairs)} corpus entries")

    states = [p.get("state", "") for p in pairs]
    print(f"[phase_b] loading base model for embeddings: {base_model}")
    model, tokenizer = load_local_model(base_model)

    t0 = time.time()
    embeds = embed_texts(model, tokenizer, states)
    wall = time.time() - t0
    print(f"[phase_b] embedded {len(states)} states in {wall:.1f}s  "
          f"(dim={embeds.shape[1]})")

    # Persist only the fields we need for retrieval + prompt assembly.
    compact_meta = [
        {
            "state": p.get("state", ""),
            "tactic": p.get("tactic", ""),
            "name": p.get("name", ""),
            "full_name": p.get("full_name", ""),
            "kind": p.get("kind", ""),
            "source_file": p.get("_source_file", ""),
        }
        for p in pairs
    ]

    index_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        index_path,
        embeddings=embeds,
        meta=np.array(json.dumps(compact_meta), dtype=object),
    )
    print(f"[phase_b] wrote index: {index_path}")
    return {"count": len(pairs), "dim": embeds.shape[1], "wall": wall}


def load_index(path: Path) -> tuple[np.ndarray, list[dict]]:
    data = np.load(path, allow_pickle=True)
    embeddings = data["embeddings"]
    meta_str = str(data["meta"].item()) if data["meta"].ndim == 0 else str(data["meta"])
    meta = json.loads(meta_str)
    return embeddings, meta


def retrieve_topk(query_embed: np.ndarray, corpus_embeds: np.ndarray,
                  k: int) -> list[tuple[int, float]]:
    """Return [(corpus_idx, score)] sorted by descending cosine similarity."""
    # Both query and corpus are already L2-normalized, so dot product = cosine.
    scores = corpus_embeds @ query_embed
    topk_idx = np.argsort(-scores)[:k]
    return [(int(i), float(scores[i])) for i in topk_idx]


def build_retrieval_prompt(task, retrieved_pairs: list[dict]) -> str:
    """Few-shot prompt using retrieved pairs in place of Phase A's generic set."""
    parts = [
        "You are an expert Lean 4 programmer. Given a tactic goal state, "
        "produce a single tactic body that solves it. Use `exact` with a "
        "complete term. Only output the tactic — no commentary, no "
        "surrounding `by` block.",
        "",
    ]
    for pair in retrieved_pairs:
        parts.append("Goal:")
        parts.append(pair["state"])
        parts.append("Tactic:")
        parts.append(pair["tactic"])
        parts.append("")
    parts.append("Goal:")
    parts.append(build_goal_state(task))
    parts.append("Tactic:")
    return "\n".join(parts)


def run_config(config: str, k: int, oracle_selections: dict | None,
               out_path: Path, base_model: str, n: int, temperature: float,
               tier: str, index_path: Path, verbose: bool = False):
    """Run one Phase B config (B-1, B-2, or B-3)."""
    # Load index
    if not index_path.exists():
        print(f"[phase_b] index not found at {index_path}, building it now")
        build_index(DEFAULT_CORPUS, index_path, base_model=base_model)
    corpus_embeds, corpus_meta = load_index(index_path)
    print(f"[phase_b] loaded index: {len(corpus_meta)} entries, "
          f"dim={corpus_embeds.shape[1]}")

    tasks = list(get_tasks(tier))
    print(f"[phase_b] tier={tier} tasks={len(tasks)} config={config} k={k}")

    # Load model once — reused for embedding queries AND generating tactics
    print(f"[phase_b] loading base model for generation: {base_model}")
    model, tokenizer = load_local_model(base_model)

    # For B-1/B-2: compute query embeddings for each task goal, then retrieve.
    # For B-3: use pre-selected corpus indices from oracle_selections.
    selections_per_task: dict[str, list[int]] = {}
    if config in ("B1", "B2"):
        query_texts = [build_goal_state(t) for t in tasks]
        print(f"[phase_b] embedding {len(query_texts)} task goals")
        query_embeds = embed_texts(model, tokenizer, query_texts)
        for t, q in zip(tasks, query_embeds):
            hits = retrieve_topk(q, corpus_embeds, k)
            selections_per_task[t.name] = [i for i, _ in hits]
    elif config == "B3":
        if not oracle_selections:
            print("error: B-3 requires --oracle-selections", file=sys.stderr)
            sys.exit(2)
        for t in tasks:
            sel = oracle_selections.get(t.name)
            if not sel:
                print(f"error: no oracle selections for task {t.name}", file=sys.stderr)
                sys.exit(2)
            selections_per_task[t.name] = sel[:k]
    else:
        print(f"error: unknown config {config}", file=sys.stderr)
        sys.exit(2)

    # Generate tactics for each task with its retrieved few-shot set
    print(f"[phase_b] generating {n} candidates per task at temperature {temperature}")
    t0_gen = time.time()
    tactics_by_task: dict[str, list[str]] = {}
    prompts_by_task: dict[str, str] = {}
    retrieved_by_task: dict[str, list[dict]] = {}

    for t in tasks:
        retrieved = [corpus_meta[i] for i in selections_per_task[t.name]]
        prompt = build_retrieval_prompt(t, retrieved)
        cands = generate_local(model, tokenizer, prompt,
                               n=n, temperature=temperature)
        tactics_by_task[t.name] = cands
        prompts_by_task[t.name] = prompt
        retrieved_by_task[t.name] = [
            {"name": r.get("name"), "kind": r.get("kind"),
             "state": r.get("state"), "tactic": r.get("tactic")[:120]}
            for r in retrieved
        ]
        print(f"  {t.name:<28} {len(cands)} unique candidates, "
              f"retrieved: {[r.get('name')[:20] for r in retrieved[:3]]}...")
    wall_gen = time.time() - t0_gen
    print(f"[phase_b] generation wall: {wall_gen:.1f}s")

    # Free GPU memory before oracle verification — daemon has its own process.
    import torch
    del model
    torch.cuda.empty_cache()

    # Verify each task's candidates
    print(f"[phase_b] connecting to daemon")
    from lean_mcts.env.pantograph_client import PantographClient
    client = PantographClient()
    if not client.ping():
        print("error: daemon ping failed", file=sys.stderr)
        sys.exit(3)

    print(f"[phase_b] verifying {sum(len(v) for v in tactics_by_task.values())} tactics")
    t0_ver = time.time()
    per_task = []
    for task in tasks:
        cands = tactics_by_task.get(task.name, [])
        results = verify_task(task, cands, client, verbose=verbose)
        task_solved = any(r["passed"] for r in results)
        per_task.append({
            "name": task.name,
            "theorem_name": task.theorem_name,
            "solved": task_solved,
            "n_candidates": len(cands),
            "retrieved": retrieved_by_task[task.name],
            "candidates": results,
        })
        marker = "PASS" if task_solved else "fail"
        print(f"  [{marker}] {task.name:<28} "
              f"{sum(r['passed'] for r in results)}/{len(results)}")
    wall_ver = time.time() - t0_ver

    summary = summarize(per_task)

    config_meta = {
        "config": config,
        "tier": tier,
        "base_model": base_model,
        "n": n,
        "temperature": temperature,
        "k": k,
        "index_path": str(index_path),
        "wall_generate": round(wall_gen, 1),
        "wall_verify": round(wall_ver, 1),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "config": config_meta,
        "summary": summary,
        "per_task": per_task,
    }, indent=2))

    print()
    print("=" * 64)
    print(f"Phase B config={config} complete")
    print(f"  solved={summary['solved']}/{summary['total']}  "
          f"rate={summary['solve_rate']:.1%}")
    print(f"  solved_tasks: {summary['solved_tasks']}")
    print(f"  output={out_path}")
    print("=" * 64)
    return summary


# ---------------------------------------------------------------------------
# 2026-11-21 re-run additions (additive; default behaviour unchanged).
# ---------------------------------------------------------------------------

def build_spec_retrieval_prompt(task, retrieved_pairs: list[dict]) -> str:
    """build_retrieval_prompt + task name/description/3 tests before the goal."""
    from lean_mcts.benchmark.phase_a import build_spec_block
    parts = [
        "You are an expert Lean 4 programmer. Given a task description, "
        "example test cases and a tactic goal state, produce a single tactic "
        "body that solves it. Use `exact` with a complete term. Only output "
        "the tactic — no commentary, no surrounding `by` block.",
        "",
    ]
    for pair in retrieved_pairs:
        parts.append("Goal:")
        parts.append(pair["state"])
        parts.append("Tactic:")
        parts.append(pair["tactic"])
        parts.append("")
    parts.extend(build_spec_block(task))
    parts.append("Goal:")
    parts.append(build_goal_state(task))
    parts.append("Tactic:")
    return "\n".join(parts)


def run_config_rerun(config: str, k: int, oracle_selections: dict | None,
                     out_path: Path, base_model: str, n: int, temperature: float,
                     tier: str, index_path: Path, prompt_style: str,
                     query_style: str, verify_workers: int = 3):
    import torch
    from lean_mcts.benchmark.phase_a import (
        VerifyPool, build_spec_block, dedup_extracted, generate_local_raw,
    )
    if not index_path.exists():
        print(f"[phase_b] index not found at {index_path}, building it now")
        build_index(DEFAULT_CORPUS, index_path, base_model=base_model)
    corpus_embeds, corpus_meta = load_index(index_path)
    tasks = list(get_tasks(tier))
    model, tokenizer = load_local_model(base_model)
    sel: dict[str, list[int]] = {}
    scores: dict[str, list[float]] = {}
    if config in ("B1", "B2"):
        if query_style == "description":
            qs = ["\n".join(build_spec_block(t)) + "\n" + build_goal_state(t) for t in tasks]
        else:
            qs = [build_goal_state(t) for t in tasks]
        qe = embed_texts(model, tokenizer, qs)
        for t, q in zip(tasks, qe):
            hits = retrieve_topk(q, corpus_embeds, k)
            sel[t.name] = [i for i, _ in hits]
            scores[t.name] = [round(s, 4) for _, s in hits]
    else:
        for t in tasks:
            sel[t.name] = oracle_selections[t.name][:k]
    gen = {}
    t0 = time.time()
    for t in tasks:
        retrieved = [corpus_meta[i] for i in sel[t.name]]
        if prompt_style == "spec":
            prompt = build_spec_retrieval_prompt(t, retrieved)
            stops = ("\nTask:",)
        else:
            prompt = build_retrieval_prompt(t, retrieved)
            stops = ()
        samples = generate_local_raw(model, tokenizer, prompt, n=n,
                                     temperature=temperature, extra_stops=stops)
        gen[t.name] = {"prompt": prompt, "retrieved_idx": sel[t.name],
                       "retrieved_scores": scores.get(t.name),
                       "retrieved_names": [r.get("name") for r in retrieved],
                       "samples": samples, "unique": dedup_extracted(samples)}
        print(f"  {t.name:<28} {len(gen[t.name]['unique'])} unique")
    wall_gen = time.time() - t0
    del model
    torch.cuda.empty_cache()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = out_path.with_suffix(".generations.json")
    raw_path.write_text(json.dumps(gen, indent=1))

    pool = VerifyPool(n_workers=verify_workers)
    if not pool.ping():
        print("error: daemon ping failed", file=sys.stderr)
        sys.exit(3)
    per_task = []
    for t in tasks:
        cands = gen[t.name]["unique"]
        res = pool.verify_many([(t, c) for c in cands])
        smp = gen[t.name]["samples"]
        per_task.append({
            "name": t.name, "solved": any(r["passed"] for r in res),
            "n_candidates": len(cands),
            "n_empty_extracted": sum(1 for s in smp if not s["extracted"]),
            "n_empty_raw": sum(1 for s in smp if not s["raw"].strip()),
            "n_hit_max_tokens": sum(1 for s in smp if s["hit_max_tokens"]),
            "retrieved_names": gen[t.name]["retrieved_names"],
            "verdict_counts": {v: sum(1 for r in res if r["verdict"] == v)
                               for v in ("pass", "compile_fail", "wrong_output", "infra_error")},
            "candidates": res,
        })
        print(f"  [{'PASS' if per_task[-1]['solved'] else 'fail'}] {t.name:<28} {per_task[-1]['verdict_counts']}")
    summary = summarize(per_task)
    summary["unique_per_task"] = {r["name"]: r["n_candidates"] for r in per_task}
    summary["zero_candidate_tasks"] = [r["name"] for r in per_task if r["n_candidates"] == 0]
    summary["verdict_totals"] = {v: sum(r["verdict_counts"][v] for r in per_task)
                                 for v in ("pass", "compile_fail", "wrong_output", "infra_error")}
    out_path.write_text(json.dumps({
        "config": {"config": config, "k": k, "tier": tier, "base_model": base_model,
                   "n": n, "temperature": temperature, "prompt_style": prompt_style,
                   "query_style": query_style, "generations_file": str(raw_path),
                   "wall_generate": round(wall_gen, 1)},
        "summary": summary, "per_task": per_task}, indent=2))
    print(f"[phase_b/rerun] {config} solved={summary['solved']}/12 {summary['solved_tasks']} -> {out_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--prompt-style", choices=["goal_only", "spec"], default=None,
                        help="(2026-11-21 re-run mode) generation prompt style; "
                             "enables raw-generation saving")
    parser.add_argument("--query-style", choices=["goal", "description"], default="goal",
                        help="(re-run mode) retrieval query text for B1/B2 "
                             "(default goal = original)")
    parser.add_argument("--build-index", action="store_true",
                        help="Just build the retrieval index and exit.")
    parser.add_argument("--config", choices=["B1", "B2", "B3"], default=None,
                        help="Which Phase B config to run.")
    parser.add_argument("--k", type=int, default=None,
                        help="Top-k retrieval (default: 10 for B1/B3, 5 for B2)")
    parser.add_argument("--oracle-selections", default=None,
                        help="JSON file mapping task_name -> list of corpus idx (for B3)")
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    parser.add_argument("--tier", default="held_out_12")
    parser.add_argument("--n", type=int, default=8)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--index", default=str(DEFAULT_INDEX))
    parser.add_argument("--out", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    index_path = Path(args.index)

    if args.build_index:
        build_index(DEFAULT_CORPUS, index_path, base_model=args.base_model)
        return

    if not args.config:
        print("error: --config is required (or --build-index)", file=sys.stderr)
        sys.exit(2)
    if not args.out:
        print("error: --out is required for --config runs", file=sys.stderr)
        sys.exit(2)

    # Default k per plan
    default_k = {"B1": 10, "B2": 5, "B3": 10}
    k = args.k if args.k is not None else default_k[args.config]

    oracle_selections = None
    if args.config == "B3":
        if not args.oracle_selections:
            print("error: --oracle-selections required for B3", file=sys.stderr)
            sys.exit(2)
        oracle_selections = json.loads(Path(args.oracle_selections).read_text())
        oracle_selections = oracle_selections.get("selections", oracle_selections)

    if args.prompt_style:
        run_config_rerun(
            config=args.config, k=k, oracle_selections=oracle_selections,
            out_path=Path(args.out), base_model=args.base_model, n=args.n,
            temperature=args.temperature, tier=args.tier, index_path=index_path,
            prompt_style=args.prompt_style, query_style=args.query_style,
        )
        return

    run_config(
        config=args.config, k=k, oracle_selections=oracle_selections,
        out_path=Path(args.out), base_model=args.base_model,
        n=args.n, temperature=args.temperature, tier=args.tier,
        index_path=index_path, verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
