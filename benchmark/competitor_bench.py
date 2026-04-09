#!/usr/bin/env python3
"""
Competitor Comparison Benchmark — OpenDB vs Mem0 vs Vector Baseline
=====================================================================

Runs identical memory workloads across three backends:
  1. OpenDB (FTS5 + time-decay)      — our system
  2. Mem0 (vector + graph)            — pip install mem0ai
  3. Vector baseline (OpenAI embed)   — pure cosine similarity (no decay)

Metrics compared:
  - Recall accuracy (needle-in-haystack)
  - Token usage per query (embedding tokens for vector approaches)
  - Latency (store + recall)
  - Infrastructure cost (API calls)

Usage:
    python competitor_bench.py [--backends opendb,vector,mem0]

Requirements:
    pip install opendb openai numpy python-dotenv
    pip install mem0ai   (optional, for Mem0 comparison)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

BENCHMARK_DIR = Path(__file__).resolve().parent

# ============================================================
# Test Data: Conversations + Needle Queries
# ============================================================

# 20 diverse memories to store
MEMORIES = [
    "My favorite restaurant is Chez Panisse in Berkeley. We go there for special occasions.",
    "I'm allergic to shellfish, especially shrimp and lobster. Discovered this in 2019.",
    "Our company's annual revenue target for 2025 is $50 million, up from $38M in 2024.",
    "I started learning Japanese in September 2024 using the Genki textbook series.",
    "The deployment pipeline uses GitHub Actions → Docker → AWS EKS with ArgoCD for GitOps.",
    "My daughter's birthday is June 15th. She'll turn 7 this year and wants a science party.",
    "We switched from Slack to Microsoft Teams in January 2025 due to enterprise licensing.",
    "The database migration from MySQL to PostgreSQL is scheduled for Q2 2025.",
    "I run 5km every morning at 6am. My personal best is 22 minutes 30 seconds.",
    "Our biggest client is Acme Corp, accounting for 30% of total revenue.",
    "I prefer dark mode in all applications. Light themes give me headaches.",
    "The team uses a two-week sprint cycle with planning on Mondays and retros on Fridays.",
    "My home office setup: MacBook Pro M3, 27-inch LG monitor, Herman Miller Aeron chair.",
    "We're evaluating three vendors for the new CRM: Salesforce, HubSpot, and Pipedrive.",
    "I took a machine learning course on Coursera taught by Andrew Ng. Completed in March 2024.",
    "The production server runs on Ubuntu 22.04 LTS with 64GB RAM and NVMe storage.",
    "Our Q4 board meeting is scheduled for December 12th at the downtown Hilton.",
    "I'm reading 'Designing Data-Intensive Applications' by Martin Kleppmann. Great book.",
    "The API rate limit is 1000 requests per minute per API key, with burst up to 1500.",
    "My partner and I are planning a trip to Japan in cherry blossom season (late March 2025).",
]

# Needle queries: each should match exactly one memory
QUERIES = [
    {"query": "favorite restaurant", "expected_idx": 0, "expected_keyword": "Chez Panisse"},
    {"query": "food allergy", "expected_idx": 1, "expected_keyword": "shellfish"},
    {"query": "revenue target 2025", "expected_idx": 2, "expected_keyword": "$50 million"},
    {"query": "learning Japanese", "expected_idx": 3, "expected_keyword": "Genki"},
    {"query": "deployment pipeline CI/CD", "expected_idx": 4, "expected_keyword": "ArgoCD"},
    {"query": "daughter birthday", "expected_idx": 5, "expected_keyword": "June 15"},
    {"query": "messaging app switch", "expected_idx": 6, "expected_keyword": "Teams"},
    {"query": "database migration", "expected_idx": 7, "expected_keyword": "PostgreSQL"},
    {"query": "running personal best", "expected_idx": 8, "expected_keyword": "22 minutes"},
    {"query": "biggest client", "expected_idx": 9, "expected_keyword": "Acme"},
    {"query": "dark mode preference", "expected_idx": 10, "expected_keyword": "dark mode"},
    {"query": "sprint cycle", "expected_idx": 11, "expected_keyword": "two-week"},
    {"query": "home office equipment", "expected_idx": 12, "expected_keyword": "MacBook"},
    {"query": "CRM vendor evaluation", "expected_idx": 13, "expected_keyword": "Salesforce"},
    {"query": "machine learning course", "expected_idx": 14, "expected_keyword": "Andrew Ng"},
    {"query": "production server specs", "expected_idx": 15, "expected_keyword": "Ubuntu"},
    {"query": "board meeting schedule", "expected_idx": 16, "expected_keyword": "December 12"},
    {"query": "currently reading book", "expected_idx": 17, "expected_keyword": "Kleppmann"},
    {"query": "API rate limit", "expected_idx": 18, "expected_keyword": "1000 requests"},
    {"query": "Japan travel plan", "expected_idx": 19, "expected_keyword": "cherry blossom"},
]


@dataclass
class BackendResult:
    backend_name: str
    queries_correct: int
    queries_total: int
    accuracy: float
    store_total_ms: float
    recall_median_ms: float
    recall_p95_ms: float
    embed_tokens: int  # 0 for FTS-based backends
    embed_api_calls: int  # 0 for FTS-based backends
    details: list[dict]


# ============================================================
# Backend: OpenDB (FTS)
# ============================================================


async def run_opendb_backend(memories: list[str], queries: list[dict]) -> BackendResult:
    """Run benchmark against OpenDB FTS backend."""
    from opendb_core.storage import init_backend, get_backend, close_backend

    tmp_dir = Path(tempfile.mkdtemp(prefix="opendb_comp_"))
    db_path = tmp_dir / "test.db"
    details = []

    try:
        await init_backend("sqlite", db_path=str(db_path))
        backend = get_backend()

        # Store
        t_store = time.perf_counter()
        for i, mem in enumerate(memories):
            await backend.store_memory(
                memory_id=str(uuid.uuid4()),
                content=mem,
                memory_type="semantic",
                tags=[f"idx_{i}"],
                metadata={"index": i},
            )
        store_ms = (time.perf_counter() - t_store) * 1000

        # Query
        latencies = []
        correct = 0
        for q in queries:
            t0 = time.perf_counter()
            result = await backend.recall_memories(query=q["query"], memory_type=None, tags=None, limit=5, offset=0)
            lat = (time.perf_counter() - t0) * 1000
            latencies.append(lat)

            mems = result.get("results", [])
            found = False
            if mems:
                top_content = mems[0].get("content", "")
                found = q["expected_keyword"].lower() in top_content.lower()

            if found:
                correct += 1
            details.append({
                "query": q["query"],
                "expected": q["expected_keyword"],
                "found": found,
                "latency_ms": round(lat, 2),
                "top_result": mems[0].get("content", "")[:80] if mems else "(empty)",
            })

        return BackendResult(
            backend_name="OpenDB (FTS)",
            queries_correct=correct,
            queries_total=len(queries),
            accuracy=round(correct / len(queries) * 100, 1),
            store_total_ms=round(store_ms, 1),
            recall_median_ms=round(statistics.median(latencies), 2),
            recall_p95_ms=round(sorted(latencies)[int(len(latencies) * 0.95)], 2),
            embed_tokens=0,
            embed_api_calls=0,
            details=details,
        )
    finally:
        await close_backend(str(db_path))
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================
# Backend: Vector Baseline (OpenAI embeddings + numpy cosine)
# ============================================================


async def run_vector_backend(memories: list[str], queries: list[dict]) -> BackendResult:
    """Run benchmark against pure vector similarity baseline."""
    from openai import AsyncOpenAI
    import numpy as np

    client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ.get("OPENROUTER_API_KEY", os.environ.get("OPENAI_API_KEY", "")),
        default_headers={
            "HTTP-Referer": "https://github.com/wuwangzhang1216/openDB",
            "X-Title": "openDB",
        },
    )
    embed_model = "openai/text-embedding-3-small"
    embed_tokens = 0
    embed_calls = 0
    details = []

    async def embed(texts: list[str]) -> list[list[float]]:
        nonlocal embed_tokens, embed_calls
        resp = await client.embeddings.create(model=embed_model, input=texts)
        embed_tokens += resp.usage.total_tokens if resp.usage else 0
        embed_calls += 1
        return [d.embedding for d in resp.data]

    # Store (embed all memories)
    t_store = time.perf_counter()
    mem_vectors = await embed(memories)
    mem_matrix = np.array(mem_vectors, dtype=np.float32)
    # L2 normalize
    norms = np.linalg.norm(mem_matrix, axis=1, keepdims=True)
    mem_matrix = mem_matrix / np.maximum(norms, 1e-10)
    store_ms = (time.perf_counter() - t_store) * 1000

    # Query
    latencies = []
    correct = 0
    for q in queries:
        t0 = time.perf_counter()
        q_vec = (await embed([q["query"]]))[0]
        q_arr = np.array(q_vec, dtype=np.float32)
        q_arr = q_arr / max(np.linalg.norm(q_arr), 1e-10)

        scores = mem_matrix @ q_arr
        top_idx = int(np.argmax(scores))
        lat = (time.perf_counter() - t0) * 1000
        latencies.append(lat)

        top_content = memories[top_idx]
        found = q["expected_keyword"].lower() in top_content.lower()

        if found:
            correct += 1
        details.append({
            "query": q["query"],
            "expected": q["expected_keyword"],
            "found": found,
            "latency_ms": round(lat, 2),
            "top_result": top_content[:80],
            "top_score": round(float(scores[top_idx]), 4),
        })

    return BackendResult(
        backend_name="Vector (cosine)",
        queries_correct=correct,
        queries_total=len(queries),
        accuracy=round(correct / len(queries) * 100, 1),
        store_total_ms=round(store_ms, 1),
        recall_median_ms=round(statistics.median(latencies), 2),
        recall_p95_ms=round(sorted(latencies)[int(len(latencies) * 0.95)], 2),
        embed_tokens=embed_tokens,
        embed_api_calls=embed_calls,
        details=details,
    )


# ============================================================
# Backend: Mem0
# ============================================================


async def run_mem0_backend(memories: list[str], queries: list[dict]) -> BackendResult:
    """Run benchmark against Mem0 memory layer."""
    try:
        from mem0 import Memory
    except ImportError:
        print("  [SKIP] mem0ai not installed. Run: pip install mem0ai")
        return BackendResult(
            backend_name="Mem0",
            queries_correct=0,
            queries_total=len(queries),
            accuracy=0.0,
            store_total_ms=0,
            recall_median_ms=0,
            recall_p95_ms=0,
            embed_tokens=0,
            embed_api_calls=0,
            details=[{"error": "mem0ai not installed"}],
        )

    details = []

    config = {
        "llm": {
            "provider": "openai",
            "config": {
                "model": "gpt-4.1-mini",
                "api_key": os.environ.get("OPENAI_API_KEY", os.environ.get("OPENROUTER_API_KEY", "")),
            },
        },
        "version": "v1.1",
    }

    m = Memory.from_config(config)
    user_id = f"bench_{uuid.uuid4().hex[:8]}"

    # Store
    t_store = time.perf_counter()
    for mem_text in memories:
        m.add(mem_text, user_id=user_id)
    store_ms = (time.perf_counter() - t_store) * 1000

    # Query
    latencies = []
    correct = 0
    for q in queries:
        t0 = time.perf_counter()
        results = m.search(q["query"], user_id=user_id, limit=5)
        lat = (time.perf_counter() - t0) * 1000
        latencies.append(lat)

        found = False
        top_content = ""
        # Mem0 search() may return a list or a dict with "results" key
        if results:
            if isinstance(results, dict):
                hits = results.get("results", [])
            elif isinstance(results, list):
                hits = results
            else:
                hits = []
            if hits:
                top_content = hits[0].get("memory", hits[0].get("content", ""))
                found = q["expected_keyword"].lower() in top_content.lower()

        if found:
            correct += 1
        details.append({
            "query": q["query"],
            "expected": q["expected_keyword"],
            "found": found,
            "latency_ms": round(lat, 2),
            "top_result": top_content[:80] if top_content else "(empty)",
        })

    return BackendResult(
        backend_name="Mem0",
        queries_correct=correct,
        queries_total=len(queries),
        accuracy=round(correct / len(queries) * 100, 1),
        store_total_ms=round(store_ms, 1),
        recall_median_ms=round(statistics.median(latencies), 2) if latencies else 0,
        recall_p95_ms=round(sorted(latencies)[int(len(latencies) * 0.95)], 2) if latencies else 0,
        embed_tokens=0,  # Mem0 doesn't expose this
        embed_api_calls=0,
        details=details,
    )


# ============================================================
# Runner
# ============================================================


BACKEND_RUNNERS = {
    "opendb": run_opendb_backend,
    "vector": run_vector_backend,
    "mem0": run_mem0_backend,
}


def print_comparison(results: list[BackendResult]) -> None:
    """Print comparison table."""
    print(f"\n{'=' * 80}")
    print("  Competitor Comparison Results")
    print(f"{'=' * 80}")
    print(f"  Memories stored: {len(MEMORIES)}")
    print(f"  Queries tested:  {len(QUERIES)}")
    print()

    # Summary table
    header = f"{'Backend':<20} {'Accuracy':>8} {'Store':>10} {'Recall p50':>12} {'Recall p95':>12} {'Embed Tok':>10} {'API Calls':>10}"
    print(header)
    print("-" * 82)
    for r in results:
        print(
            f"{r.backend_name:<20} "
            f"{r.accuracy:>7.1f}% "
            f"{r.store_total_ms:>9.1f}ms "
            f"{r.recall_median_ms:>11.2f}ms "
            f"{r.recall_p95_ms:>11.2f}ms "
            f"{r.embed_tokens:>10,} "
            f"{r.embed_api_calls:>10}"
        )

    # Per-query comparison
    print(f"\n{'─' * 80}")
    print("Per-Query Accuracy:")
    print(f"{'─' * 80}")
    for i, q in enumerate(QUERIES):
        statuses = []
        for r in results:
            if r.details and i < len(r.details) and "found" in r.details[i]:
                statuses.append("OK" if r.details[i]["found"] else "MISS")
            else:
                statuses.append("N/A")
        status_str = "  ".join(f"{r.backend_name[:6]:>6}={s}" for r, s in zip(results, statuses))
        print(f"  {q['query']:<30} {status_str}")

    # Highlight advantages
    print(f"\n{'─' * 80}")
    print("Key Differences:")
    print(f"{'─' * 80}")
    opendb = next((r for r in results if "OpenDB" in r.backend_name), None)
    vector = next((r for r in results if "Vector" in r.backend_name), None)
    mem0 = next((r for r in results if "Mem0" in r.backend_name), None)

    if opendb and vector:
        if opendb.recall_median_ms < vector.recall_median_ms:
            speedup = vector.recall_median_ms / max(opendb.recall_median_ms, 0.01)
            print(f"  OpenDB recall is {speedup:.0f}x faster than Vector baseline")
        print(f"  OpenDB uses 0 embedding tokens (Vector used {vector.embed_tokens:,})")
        print(f"  OpenDB requires 0 API calls for retrieval (Vector used {vector.embed_api_calls})")

    if opendb and mem0 and mem0.accuracy > 0:
        if opendb.recall_median_ms < mem0.recall_median_ms:
            speedup = mem0.recall_median_ms / max(opendb.recall_median_ms, 0.01)
            print(f"  OpenDB recall is {speedup:.0f}x faster than Mem0")

    print(f"{'=' * 80}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Competitor Comparison Benchmark")
    parser.add_argument(
        "--backends", default="opendb,vector",
        help="Comma-separated backends to test (default: opendb,vector). Options: opendb,vector,mem0",
    )
    parser.add_argument(
        "--output", default=None,
        help="Path to save JSON results (default: competitor_results.json)",
    )
    args = parser.parse_args()

    backends = [b.strip() for b in args.backends.split(",")]

    print("=" * 60)
    print("  Competitor Comparison Benchmark")
    print("=" * 60)
    print(f"Backends: {', '.join(backends)}")
    print(f"Memories: {len(MEMORIES)}")
    print(f"Queries:  {len(QUERIES)}")
    print()

    all_results: list[BackendResult] = []

    async def _run():
        for backend_key in backends:
            if backend_key not in BACKEND_RUNNERS:
                print(f"  [SKIP] Unknown backend: {backend_key}")
                continue
            print(f"\nRunning {backend_key}...")
            runner = BACKEND_RUNNERS[backend_key]
            result = await runner(MEMORIES, QUERIES)
            all_results.append(result)
            print(f"  {result.backend_name}: {result.accuracy}% accuracy, "
                  f"recall={result.recall_median_ms:.2f}ms median")

    asyncio.run(_run())

    print_comparison(all_results)

    # Save
    output_path = args.output or str(BENCHMARK_DIR / "competitor_results.json")
    output = {
        "config": {
            "memories": len(MEMORIES),
            "queries": len(QUERIES),
            "backends": backends,
        },
        "results": [
            {
                "backend": r.backend_name,
                "accuracy": r.accuracy,
                "store_ms": r.store_total_ms,
                "recall_median_ms": r.recall_median_ms,
                "recall_p95_ms": r.recall_p95_ms,
                "embed_tokens": r.embed_tokens,
                "embed_api_calls": r.embed_api_calls,
                "details": r.details,
            }
            for r in all_results
        ],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
