#!/usr/bin/env python3
"""
Memory Stress Benchmark for OpenDB Memory Pipeline
======================================================

Tests memory capabilities that are missing from the basic R@K benchmark:

1. Knowledge Update    — store A, update to B, recall should return B (not A)
2. Abstention          — ask about something never stored, should get empty/irrelevant
3. Temporal Reasoning  — store events with dates, query by time order
4. CJK Support         — Chinese/Japanese memories, recall in CJK
5. Memory Scale        — measure recall latency at 100, 1k, 5k, 10k memories

These are targeted micro-benchmarks, not the full LongMemEval E2E.
They test specific memory pipeline capabilities in isolation.

Usage:
    python memory_stress_bench.py [--suites all|update|abstention|temporal|cjk|scale]
                                   [--scale-sizes 100,1000,5000,10000]

Requirements:
    pip install opendb
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
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Add project root to path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from opendb_core.storage import init_backend, get_backend, close_backend

BENCHMARK_DIR = Path(__file__).resolve().parent


@dataclass
class TestResult:
    suite: str
    test_name: str
    passed: bool
    detail: str
    latency_ms: float = 0.0


# ============================================================
# Suite 1: Knowledge Update
# ============================================================

KNOWLEDGE_UPDATE_CASES = [
    {
        "name": "simple_fact_update",
        "original": "My favorite color is blue.",
        "updated": "My favorite color is green. I changed it from blue.",
        "query": "favorite color",
        "expected_contains": "green",
        "should_not_contain": None,
    },
    {
        "name": "address_change",
        "original": "I live at 123 Oak Street, San Francisco.",
        "updated": "I no longer live in San Francisco. I moved to 456 Pine Avenue, Seattle last month.",
        "query": "where do I live",
        "expected_contains": "Seattle",
        "should_not_contain": None,
    },
    {
        "name": "job_change",
        "original": "I work as a software engineer at Google.",
        "updated": "I just started a new role as a product manager at Meta.",
        "query": "job role company",
        "expected_contains": "Meta",
        "should_not_contain": None,
    },
    {
        "name": "preference_update",
        "original": "I prefer Python for backend development.",
        "updated": "I've switched to Rust for backend development. Much faster than Python.",
        "query": "backend development language preference",
        "expected_contains": "Rust",
        "should_not_contain": None,
    },
    {
        "name": "numeric_update",
        "original": "Our team has 15 members.",
        "updated": "Our team grew to 23 members after the new hires.",
        "query": "team size members",
        "expected_contains": "23",
        "should_not_contain": None,
    },
]


async def run_knowledge_update_suite() -> list[TestResult]:
    """Test that updated memories rank higher than stale ones."""
    results = []

    for case in KNOWLEDGE_UPDATE_CASES:
        tmp_dir = Path(tempfile.mkdtemp(prefix="opendb_update_"))
        db_path = tmp_dir / "test.db"

        try:
            await init_backend("sqlite", db_path=str(db_path))
            backend = get_backend()

            # Store original fact
            await backend.store_memory(
                memory_id=str(uuid.uuid4()),
                content=case["original"],
                memory_type="semantic",
                tags=["fact"],
                metadata={},
            )

            # Small delay to ensure different timestamps
            await asyncio.sleep(0.01)

            # Store updated fact
            await backend.store_memory(
                memory_id=str(uuid.uuid4()),
                content=case["updated"],
                memory_type="semantic",
                tags=["fact"],
                metadata={},
            )

            # Recall
            t0 = time.perf_counter()
            result = await backend.recall_memories(
                query=case["query"],
                memory_type=None,
                tags=None,
                limit=5,
                offset=0,
            )
            latency = (time.perf_counter() - t0) * 1000

            memories = result.get("results", [])

            # Check: top result should contain the updated info
            if not memories:
                results.append(TestResult(
                    suite="knowledge_update",
                    test_name=case["name"],
                    passed=False,
                    detail="No memories recalled",
                    latency_ms=latency,
                ))
            else:
                top_content = memories[0].get("content", "").lower()
                expected = case["expected_contains"].lower()
                passed = expected in top_content
                results.append(TestResult(
                    suite="knowledge_update",
                    test_name=case["name"],
                    passed=passed,
                    detail=f"Top result {'contains' if passed else 'missing'} '{case['expected_contains']}': {memories[0].get('content', '')[:100]}",
                    latency_ms=latency,
                ))
        finally:
            await close_backend(str(db_path))
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return results


# ============================================================
# Suite 2: Abstention
# ============================================================

ABSTENTION_CASES = [
    {
        "name": "never_mentioned_topic",
        "memories": [
            "I enjoy hiking in the mountains on weekends.",
            "My dog's name is Max and he's a golden retriever.",
            "I'm learning to play the piano.",
        ],
        "query": "what is my favorite programming language",
        "description": "Query about a topic never mentioned in memories",
    },
    {
        "name": "unrelated_domain",
        "memories": [
            "The quarterly revenue was $4.2M, up 15% from last quarter.",
            "Marketing budget was approved at $500K for Q1.",
            "We hired 3 new sales representatives.",
        ],
        "query": "what is the server deployment architecture",
        "description": "Business memories, technical query",
    },
    {
        "name": "specific_nonexistent_entity",
        "memories": [
            "Had a meeting with Alice about the product roadmap.",
            "Bob presented the Q3 financial results.",
            "Carol is leading the new design system project.",
        ],
        "query": "what did David say about the infrastructure migration",
        "description": "Query about a person never mentioned",
    },
    {
        "name": "empty_memory_store",
        "memories": [],
        "query": "what is my name",
        "description": "No memories stored at all",
    },
    {
        "name": "partial_overlap_different_context",
        "memories": [
            "I bought a new car last month, a Tesla Model 3.",
            "The car payment is $650 per month.",
        ],
        "query": "what color is my car",
        "description": "Memories about car but color never mentioned",
    },
]


async def run_abstention_suite() -> list[TestResult]:
    """Test that irrelevant queries return no/low-relevance results."""
    results = []

    for case in ABSTENTION_CASES:
        tmp_dir = Path(tempfile.mkdtemp(prefix="opendb_abstain_"))
        db_path = tmp_dir / "test.db"

        try:
            await init_backend("sqlite", db_path=str(db_path))
            backend = get_backend()

            # Store memories
            for mem_text in case["memories"]:
                await backend.store_memory(
                    memory_id=str(uuid.uuid4()),
                    content=mem_text,
                    memory_type="semantic",
                    tags=[],
                    metadata={},
                )

            # Recall with the unrelated query
            t0 = time.perf_counter()
            result = await backend.recall_memories(
                query=case["query"],
                memory_type=None,
                tags=None,
                limit=5,
                offset=0,
            )
            latency = (time.perf_counter() - t0) * 1000

            memories = result.get("results", [])

            # For abstention, success = no results OR results have very low scores
            # FTS should return 0 results for completely unrelated keywords
            if not memories:
                passed = True
                detail = "Correctly returned no results"
            else:
                # Check if any result has a meaningful match
                # For FTS, if query keywords don't appear in content, scores should be 0 or very low
                top_score = memories[0].get("score", 0)
                # If we got results, check if query terms actually appear in content
                query_terms = set(case["query"].lower().split())
                top_content_lower = memories[0].get("content", "").lower()
                has_overlap = any(term in top_content_lower for term in query_terms if len(term) > 3)

                if has_overlap:
                    passed = False
                    detail = f"Returned {len(memories)} results with keyword overlap (score={top_score:.4f})"
                else:
                    passed = True
                    detail = f"Returned {len(memories)} results but no keyword overlap (FTS noise, score={top_score:.4f})"

            results.append(TestResult(
                suite="abstention",
                test_name=case["name"],
                passed=passed,
                detail=detail,
                latency_ms=latency,
            ))
        finally:
            await close_backend(str(db_path))
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return results


# ============================================================
# Suite 3: Temporal Reasoning
# ============================================================

TEMPORAL_CASES = [
    {
        "name": "chronological_events",
        "memories": [
            ("2024-01-15", "Started a new job at Anthropic as a research engineer."),
            ("2024-03-20", "Got promoted to senior research engineer."),
            ("2024-06-10", "Led my first project — the memory benchmark initiative."),
            ("2024-09-01", "Transferred to the applied team."),
        ],
        "query": "first project I led",
        "expected_contains": "memory benchmark",
    },
    {
        "name": "most_recent_event",
        "memories": [
            ("2023-06-01", "Bought a Honda Civic from the dealership."),
            ("2024-01-15", "Sold the Honda Civic to a friend."),
            ("2024-02-01", "Bought a Tesla Model Y from the dealership."),
        ],
        "query": "bought dealership",
        "expected_top_contains": "Tesla",
    },
    {
        "name": "date_specific_query",
        "memories": [
            ("2024-01-10", "Had a dentist appointment. No cavities found."),
            ("2024-03-15", "Had a doctor checkup. Blood pressure was normal."),
            ("2024-06-20", "Had an eye exam. Need new glasses."),
        ],
        "query": "doctor checkup blood pressure",
        "expected_contains": "blood pressure",
    },
    {
        "name": "conflicting_dates",
        "memories": [
            ("2024-01-01", "The team uses React for the frontend."),
            ("2024-06-01", "We migrated the frontend from React to Svelte."),
        ],
        "query": "frontend framework team",
        "expected_top_contains": "Svelte",
    },
]


async def run_temporal_suite() -> list[TestResult]:
    """Test time-aware recall (recency bias via decay)."""
    results = []

    for case in TEMPORAL_CASES:
        tmp_dir = Path(tempfile.mkdtemp(prefix="opendb_temporal_"))
        db_path = tmp_dir / "test.db"

        try:
            await init_backend("sqlite", db_path=str(db_path))
            backend = get_backend()

            # Store memories with sequential timestamps (older first)
            for date_str, content in case["memories"]:
                await backend.store_memory(
                    memory_id=str(uuid.uuid4()),
                    content=content,
                    memory_type="episodic",
                    tags=["event"],
                    metadata={"date": date_str},
                )
                await asyncio.sleep(0.005)  # Ensure different created_at

            # Recall
            t0 = time.perf_counter()
            result = await backend.recall_memories(
                query=case["query"],
                memory_type=None,
                tags=None,
                limit=5,
                offset=0,
            )
            latency = (time.perf_counter() - t0) * 1000

            memories = result.get("results", [])

            if not memories:
                results.append(TestResult(
                    suite="temporal",
                    test_name=case["name"],
                    passed=False,
                    detail="No memories recalled",
                    latency_ms=latency,
                ))
                continue

            # Check expected content in top result
            expected_key = "expected_top_contains" if "expected_top_contains" in case else "expected_contains"
            expected = case[expected_key].lower()
            top_content = memories[0].get("content", "").lower()
            passed = expected in top_content

            results.append(TestResult(
                suite="temporal",
                test_name=case["name"],
                passed=passed,
                detail=f"Top result {'contains' if passed else 'missing'} '{case[expected_key]}': {memories[0].get('content', '')[:100]}",
                latency_ms=latency,
            ))
        finally:
            await close_backend(str(db_path))
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return results


# ============================================================
# Suite 4: CJK Support
# ============================================================

CJK_CASES = [
    {
        "name": "chinese_simple_recall",
        "memories": [
            "我最喜欢的编程语言是Python，因为它简洁易读。",
            "我在北京的一家科技公司工作，负责后端开发。",
            "周末我喜欢去公园跑步，每次大约跑五公里。",
        ],
        "query": "编程语言",
        "expected_contains": "Python",
    },
    {
        "name": "chinese_search_precision",
        "memories": [
            "今天的团队会议讨论了新产品的发布计划，预计下个月上线。",
            "客户反馈系统需要改进搜索功能，目前搜索太慢。",
            "公司决定将服务器从阿里云迁移到腾讯云。",
        ],
        "query": "服务器 迁移 云",
        "expected_contains": "腾讯云",
    },
    {
        "name": "chinese_mixed_content",
        "memories": [
            "项目使用 React + TypeScript 前端框架，后端用 Go 语言。",
            "数据库选用 PostgreSQL 14，缓存用 Redis 7.0。",
            "CI/CD 流程使用 GitHub Actions，部署到 AWS EKS。",
        ],
        "query": "数据库 PostgreSQL",
        "expected_contains": "PostgreSQL",
    },
    {
        "name": "japanese_recall",
        "memories": [
            "東京で行われたカンファレンスに参加しました。とても勉強になりました。",
            "新しいプロジェクトのリーダーに任命されました。頑張ります。",
        ],
        "query": "カンファレンス 東京",
        "expected_contains": "東京",
    },
    {
        "name": "chinese_long_memory",
        "memories": [
            "2024年第一季度销售报告：总收入达到1500万元人民币，同比增长25%。"
            "其中线上渠道贡献了60%的收入，线下渠道贡献40%。"
            "华东地区是最大的市场，占总收入的35%。",
            "2024年第二季度预测：预计总收入将达到1800万元，增长20%。",
        ],
        "query": "第一季度 销售 收入",
        "expected_contains": "1500万",
    },
]


async def run_cjk_suite() -> list[TestResult]:
    """Test CJK (Chinese/Japanese/Korean) memory support."""
    results = []

    for case in CJK_CASES:
        tmp_dir = Path(tempfile.mkdtemp(prefix="opendb_cjk_"))
        db_path = tmp_dir / "test.db"

        try:
            await init_backend("sqlite", db_path=str(db_path))
            backend = get_backend()

            for mem_text in case["memories"]:
                await backend.store_memory(
                    memory_id=str(uuid.uuid4()),
                    content=mem_text,
                    memory_type="semantic",
                    tags=[],
                    metadata={},
                )

            t0 = time.perf_counter()
            result = await backend.recall_memories(
                query=case["query"],
                memory_type=None,
                tags=None,
                limit=5,
                offset=0,
            )
            latency = (time.perf_counter() - t0) * 1000

            memories = result.get("results", [])

            if not memories:
                results.append(TestResult(
                    suite="cjk",
                    test_name=case["name"],
                    passed=False,
                    detail="No memories recalled",
                    latency_ms=latency,
                ))
            else:
                expected = case["expected_contains"]
                top_content = memories[0].get("content", "")
                passed = expected in top_content
                results.append(TestResult(
                    suite="cjk",
                    test_name=case["name"],
                    passed=passed,
                    detail=f"Top result {'contains' if passed else 'missing'} '{expected}': {top_content[:80]}",
                    latency_ms=latency,
                ))
        finally:
            await close_backend(str(db_path))
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return results


# ============================================================
# Suite 5: Memory Scale
# ============================================================


async def run_scale_suite(sizes: list[int] | None = None) -> list[TestResult]:
    """Test recall latency at increasing memory store sizes."""
    if sizes is None:
        sizes = [100, 1000, 5000, 10000]

    results = []

    # Generate diverse filler memories
    DOMAINS = [
        "software engineering", "machine learning", "data science",
        "product management", "design", "marketing", "sales",
        "finance", "legal", "operations",
    ]
    TEMPLATES = [
        "Discussed {domain} strategy in today's team meeting. Key decision: {detail}.",
        "Completed the {domain} project milestone. Next step: {detail}.",
        "Received feedback on {domain} initiative: {detail}.",
        "Updated the {domain} documentation with notes about {detail}.",
        "Had a 1:1 about {domain} progress. Action item: {detail}.",
    ]
    DETAILS = [
        "migrate to new architecture", "increase test coverage to 90%",
        "launch beta by end of quarter", "reduce latency by 50%",
        "hire two more team members", "review vendor contracts",
        "update compliance requirements", "redesign user onboarding",
        "implement caching layer", "refactor authentication module",
        "set up monitoring dashboards", "create runbook for incidents",
        "evaluate new tools", "plan offsite meeting",
        "finalize budget proposal", "conduct user interviews",
    ]

    # The "needle" memory we'll search for
    NEEDLE = "The annual company hackathon will be held on March 15th in the San Francisco office. Theme: AI-powered developer tools."
    NEEDLE_QUERY = "hackathon March San Francisco"

    for size in sizes:
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"opendb_scale{size}_"))
        db_path = tmp_dir / "test.db"

        try:
            await init_backend("sqlite", db_path=str(db_path))
            backend = get_backend()

            # Store filler memories
            t_store_start = time.perf_counter()
            for i in range(size - 1):
                domain = DOMAINS[i % len(DOMAINS)]
                template = TEMPLATES[i % len(TEMPLATES)]
                detail = DETAILS[i % len(DETAILS)]
                content = template.format(domain=domain, detail=detail)
                # Add some variation
                content += f" (Session {i}, ref #{i * 7 % 997})"

                await backend.store_memory(
                    memory_id=str(uuid.uuid4()),
                    content=content,
                    memory_type="episodic",
                    tags=[domain.replace(" ", "-")],
                    metadata={"index": i},
                )

            # Store needle in the middle
            await backend.store_memory(
                memory_id=str(uuid.uuid4()),
                content=NEEDLE,
                memory_type="semantic",
                tags=["event"],
                metadata={"special": "needle"},
            )
            store_time = (time.perf_counter() - t_store_start) * 1000

            # Recall needle — measure latency
            latencies = []
            found = False
            for _ in range(5):  # 5 trials for stable measurement
                t0 = time.perf_counter()
                result = await backend.recall_memories(
                    query=NEEDLE_QUERY,
                    memory_type=None,
                    tags=None,
                    limit=5,
                    offset=0,
                )
                lat = (time.perf_counter() - t0) * 1000
                latencies.append(lat)

                memories = result.get("results", [])
                if memories and "hackathon" in memories[0].get("content", "").lower():
                    found = True

            median_lat = statistics.median(latencies)
            p95_lat = sorted(latencies)[int(len(latencies) * 0.95)]

            results.append(TestResult(
                suite="scale",
                test_name=f"scale_{size}",
                passed=found,
                detail=(
                    f"Size={size:,}: recall median={median_lat:.1f}ms, "
                    f"p95={p95_lat:.1f}ms, "
                    f"store_total={store_time:.0f}ms, "
                    f"needle_found={found}"
                ),
                latency_ms=median_lat,
            ))

            print(f"  Scale {size:>6,}: recall={median_lat:.1f}ms  store={store_time:.0f}ms  found={found}")

        finally:
            await close_backend(str(db_path))
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return results


# ============================================================
# Runner & Report
# ============================================================


SUITE_MAP = {
    "update": ("Knowledge Update", run_knowledge_update_suite),
    "abstention": ("Abstention", run_abstention_suite),
    "temporal": ("Temporal Reasoning", run_temporal_suite),
    "cjk": ("CJK Support", run_cjk_suite),
    "scale": ("Memory Scale", None),  # Special handling
}


def print_suite_report(suite_name: str, results: list[TestResult]) -> dict:
    """Print report for a single suite."""
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    pct = passed / total * 100 if total else 0

    print(f"\n{'─' * 60}")
    print(f"  {suite_name}: {passed}/{total} ({pct:.0f}%)")
    print(f"{'─' * 60}")

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.test_name:<35} {r.latency_ms:>6.1f}ms  {r.detail[:60]}")

    return {
        "suite": suite_name,
        "passed": passed,
        "total": total,
        "accuracy": round(pct, 1),
        "tests": [
            {
                "name": r.test_name,
                "passed": r.passed,
                "detail": r.detail,
                "latency_ms": round(r.latency_ms, 2),
            }
            for r in results
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Memory Stress Benchmark")
    parser.add_argument(
        "--suites", nargs="*",
        default=["all"],
        choices=["all", "update", "abstention", "temporal", "cjk", "scale"],
        help="Test suites to run (default: all)",
    )
    parser.add_argument(
        "--scale-sizes", default="100,1000,5000,10000",
        help="Comma-separated memory sizes for scale test (default: 100,1000,5000,10000)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Path to save JSON results (default: memory_stress_results.json)",
    )
    args = parser.parse_args()

    suites_to_run = args.suites
    if "all" in suites_to_run:
        suites_to_run = list(SUITE_MAP.keys())

    scale_sizes = [int(s) for s in args.scale_sizes.split(",")]

    print("=" * 60)
    print("  OpenDB Memory Stress Benchmark")
    print("=" * 60)
    print(f"Suites: {', '.join(suites_to_run)}")
    if "scale" in suites_to_run:
        print(f"Scale sizes: {scale_sizes}")

    all_results = {}
    t_start = time.time()

    async def _run_all():
        for suite_key in suites_to_run:
            if suite_key == "scale":
                results = await run_scale_suite(scale_sizes)
                all_results[suite_key] = print_suite_report("Memory Scale", results)
            else:
                suite_name, runner = SUITE_MAP[suite_key]
                results = await runner()
                all_results[suite_key] = print_suite_report(suite_name, results)

    asyncio.run(_run_all())

    elapsed = time.time() - t_start

    # Overall summary
    total_passed = sum(s["passed"] for s in all_results.values())
    total_tests = sum(s["total"] for s in all_results.values())
    overall_pct = total_passed / total_tests * 100 if total_tests else 0

    print(f"\n{'=' * 60}")
    print(f"  OVERALL: {total_passed}/{total_tests} ({overall_pct:.0f}%)")
    print(f"  Time: {elapsed:.1f}s")
    print(f"{'=' * 60}")

    # Save results
    output_path = args.output or str(BENCHMARK_DIR / "memory_stress_results.json")
    output = {
        "overall": {
            "passed": total_passed,
            "total": total_tests,
            "accuracy": round(overall_pct, 1),
            "elapsed_seconds": round(elapsed, 1),
        },
        "suites": all_results,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
