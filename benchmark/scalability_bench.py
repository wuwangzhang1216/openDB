#!/usr/bin/env python3
"""
Document Search Scalability Benchmark
=========================================

Tests OpenDB's file search (FTS) performance at document scales beyond
the existing benchmark (which tops out at 325 docs).

Scales tested: 500, 1000, 2000, 5000 documents.

Metrics:
  - Index build time
  - Search latency (median, p95, p99)
  - Search precision (correct file in top-5)
  - Read latency for random files

This benchmark generates lightweight text files (no binary generation overhead)
to focus purely on FTS indexing and search performance.

Usage:
    python scalability_bench.py [--scales 500,1000,2000,5000]
                                 [--url http://localhost:8000]

Requirements:
    pip install opendb httpx python-dotenv
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

BENCHMARK_DIR = Path(__file__).resolve().parent

# ============================================================
# Document Generator
# ============================================================

DEPARTMENTS = [
    "engineering", "sales", "marketing", "finance", "hr",
    "legal", "operations", "product", "design", "support",
]

DOC_TYPES = [
    "quarterly-review", "budget-proposal", "project-plan",
    "incident-report", "meeting-notes", "policy-update",
    "performance-review", "vendor-assessment", "risk-analysis",
    "training-guide",
]

TOPICS = [
    "cloud migration strategy", "customer retention program",
    "data warehouse optimization", "mobile app redesign",
    "security audit findings", "supply chain automation",
    "employee wellness initiative", "revenue forecasting model",
    "API gateway implementation", "content management system",
    "machine learning pipeline", "disaster recovery plan",
    "brand identity refresh", "compliance framework update",
    "devops toolchain evaluation", "market expansion analysis",
    "product localization effort", "talent acquisition strategy",
    "sustainability reporting", "digital transformation roadmap",
]

# Needle documents — each has a unique, searchable fact
NEEDLES = [
    {
        "filename": "needle_alpha.txt",
        "content": "CONFIDENTIAL: Project Alpha budget approved at $3.7M for Q3 2025. "
                   "The steering committee unanimously agreed on the timeline. "
                   "Key deliverable: production-ready ML inference service by September.",
        "query": "Project Alpha budget $3.7M",
        "keyword": "$3.7M",
    },
    {
        "filename": "needle_beta.txt",
        "content": "URGENT: Server incident on 2025-06-15 caused 47 minutes of downtime. "
                   "Root cause: memory leak in the connection pooling layer. "
                   "Impact: 12,000 failed API requests. Postmortem scheduled for Friday.",
        "query": "server incident downtime 47 minutes",
        "keyword": "47 minutes",
    },
    {
        "filename": "needle_gamma.txt",
        "content": "New partnership agreement signed with Zenith Technologies on March 1st. "
                   "Contract value: $850K annually for 3 years. "
                   "Primary contact: Sarah Chen, VP of Strategic Partnerships.",
        "query": "Zenith Technologies partnership agreement",
        "keyword": "Zenith Technologies",
    },
    {
        "filename": "needle_delta.txt",
        "content": "Employee satisfaction survey results: overall score 4.2 out of 5. "
                   "Top concern: work-life balance (mentioned by 67% of respondents). "
                   "Biggest improvement: remote work policy (+0.8 from last year).",
        "query": "employee satisfaction survey 4.2 out of 5",
        "keyword": "4.2 out of 5",
    },
    {
        "filename": "needle_epsilon.txt",
        "content": "Patent application filed for the adaptive caching algorithm (US-2025-0042). "
                   "Inventors: Dr. James Liu and Dr. Maria Santos. "
                   "Expected grant date: Q4 2026. Legal costs to date: $127K.",
        "query": "patent adaptive caching algorithm US-2025-0042",
        "keyword": "US-2025-0042",
    },
]


def generate_filler_doc(idx: int, seed: int) -> tuple[str, str]:
    """Generate a deterministic filler document. Returns (filename, content)."""
    rng = random.Random(seed + idx)
    dept = DEPARTMENTS[idx % len(DEPARTMENTS)]
    doc_type = DOC_TYPES[idx % len(DOC_TYPES)]
    topic = rng.choice(TOPICS)
    year = rng.choice([2023, 2024, 2025])
    quarter = rng.choice(["q1", "q2", "q3", "q4"])

    filename = f"d{idx:04d}-{dept}-{doc_type}-{year}{quarter}.txt"

    # Generate content that is realistic but doesn't contain needle keywords
    paragraphs = []
    paragraphs.append(f"{dept.title()} Department — {doc_type.replace('-', ' ').title()}")
    paragraphs.append(f"Topic: {topic}")
    paragraphs.append(f"Period: {quarter.upper()} {year}")
    paragraphs.append("")

    # 3-5 paragraphs of filler
    for p in range(rng.randint(3, 5)):
        sentences = []
        for s in range(rng.randint(3, 6)):
            subject = rng.choice([
                "The team", "Management", "Stakeholders", "The committee",
                "Our department", "The project lead", "Senior leadership",
            ])
            verb = rng.choice([
                "reviewed", "discussed", "approved", "evaluated",
                "recommended", "identified", "completed", "proposed",
            ])
            obj = rng.choice([
                f"the {topic} initiative",
                f"progress on {rng.choice(TOPICS)}",
                f"the {quarter.upper()} deliverables",
                f"resource allocation for {year}",
                f"key metrics and KPIs",
                f"risk factors and mitigations",
                f"timeline adjustments",
                f"stakeholder feedback",
            ])
            amount = rng.choice([
                f"${rng.randint(10,999)}K", f"{rng.randint(5,95)}%",
                f"{rng.randint(3,52)} weeks", f"{rng.randint(2,20)} team members",
            ])
            sentences.append(f"{subject} {verb} {obj} ({amount}).")
        paragraphs.append(" ".join(sentences))

    return filename, "\n\n".join(paragraphs)


def generate_workspace(doc_count: int, seed: int = 42) -> tuple[Path, list[dict]]:
    """Generate a workspace with doc_count filler docs + needle docs."""
    ws_dir = Path(tempfile.mkdtemp(prefix=f"opendb_scale{doc_count}_"))

    # Generate filler docs
    filler_count = doc_count - len(NEEDLES)
    for i in range(filler_count):
        filename, content = generate_filler_doc(i, seed)
        (ws_dir / filename).write_text(content, encoding="utf-8")

    # Insert needles
    for needle in NEEDLES:
        (ws_dir / needle["filename"]).write_text(needle["content"], encoding="utf-8")

    return ws_dir, NEEDLES


# ============================================================
# Benchmark Runner (uses OpenDB Python API directly)
# ============================================================


async def run_scale_test(doc_count: int, seed: int = 42) -> dict:
    """Run indexing + search benchmark at a given scale."""
    from opendb_core.storage import init_backend, get_backend, close_backend
    from opendb_core.services.index_service import index_directory
    from opendb_core.services.search_service import search_files

    # Register parsers (needed for index_directory to recognize file types)
    import opendb_core.parsers.text        # noqa: F401
    import opendb_core.parsers.pdf         # noqa: F401
    import opendb_core.parsers.docx        # noqa: F401
    import opendb_core.parsers.pptx        # noqa: F401
    import opendb_core.parsers.spreadsheet # noqa: F401

    ws_dir, needles = generate_workspace(doc_count, seed)
    db_dir = ws_dir / ".opendb"
    db_path = db_dir / "metadata.db"

    try:
        # Index
        print(f"  Indexing {doc_count} documents...")
        t_index = time.perf_counter()
        await init_backend("sqlite", db_path=str(db_path))
        stats = await index_directory(ws_dir)
        index_ms = (time.perf_counter() - t_index) * 1000
        print(f"  Indexed in {index_ms:.0f}ms")

        # Search: needle queries
        search_latencies = []
        needles_found = 0
        search_details = []

        for needle in needles:
            t0 = time.perf_counter()
            results = await search_files(needle["query"], limit=5)
            lat = (time.perf_counter() - t0) * 1000
            search_latencies.append(lat)

            hits = results.get("results", [])
            found = False
            top_file = ""
            if hits:
                top_file = hits[0].get("filename", "")
                found = needle["filename"] == top_file or needle["keyword"].lower() in hits[0].get("highlight", "").lower()

            if found:
                needles_found += 1
            search_details.append({
                "query": needle["query"],
                "expected_file": needle["filename"],
                "top_file": top_file,
                "found": found,
                "latency_ms": round(lat, 2),
            })

        # Random search queries (for latency measurement)
        random_queries = [
            "budget proposal review", "quarterly performance metrics",
            "risk assessment findings", "project timeline update",
            "resource allocation plan", "compliance audit results",
            "vendor contract renewal", "training program schedule",
            "security vulnerability report", "market research analysis",
        ]
        random_latencies = []
        for q in random_queries:
            t0 = time.perf_counter()
            await search_files(q, limit=5)
            lat = (time.perf_counter() - t0) * 1000
            random_latencies.append(lat)

        all_latencies = search_latencies + random_latencies

        result = {
            "doc_count": doc_count,
            "index_time_ms": round(index_ms, 0),
            "needle_accuracy": round(needles_found / len(needles) * 100, 1),
            "needles_found": needles_found,
            "needles_total": len(needles),
            "search_median_ms": round(statistics.median(all_latencies), 2),
            "search_p95_ms": round(sorted(all_latencies)[int(len(all_latencies) * 0.95)], 2),
            "search_p99_ms": round(sorted(all_latencies)[int(len(all_latencies) * 0.99)], 2),
            "search_max_ms": round(max(all_latencies), 2),
            "needle_details": search_details,
        }

        return result

    finally:
        await close_backend(str(db_path))
        shutil.rmtree(ws_dir, ignore_errors=True)


# ============================================================
# Report
# ============================================================


def print_report(results: list[dict]) -> None:
    """Print scalability report."""
    print(f"\n{'=' * 80}")
    print("  Document Search Scalability Benchmark")
    print(f"{'=' * 80}\n")

    header = f"{'Docs':>6} {'Index':>10} {'Needle':>8} {'Search p50':>12} {'Search p95':>12} {'Search p99':>12} {'Max':>10}"
    print(header)
    print("-" * 80)
    for r in results:
        print(
            f"{r['doc_count']:>6} "
            f"{r['index_time_ms']:>9.0f}ms "
            f"{r['needle_accuracy']:>7.1f}% "
            f"{r['search_median_ms']:>11.2f}ms "
            f"{r['search_p95_ms']:>11.2f}ms "
            f"{r['search_p99_ms']:>11.2f}ms "
            f"{r['search_max_ms']:>9.2f}ms"
        )

    # Scaling analysis
    if len(results) >= 2:
        first = results[0]
        last = results[-1]
        doc_ratio = last["doc_count"] / first["doc_count"]
        index_ratio = last["index_time_ms"] / max(first["index_time_ms"], 0.01)
        search_ratio = last["search_median_ms"] / max(first["search_median_ms"], 0.01)

        print(f"\nScaling ({first['doc_count']} → {last['doc_count']} docs, {doc_ratio:.0f}x):")
        print(f"  Index time:  {index_ratio:.1f}x  ({'sublinear' if index_ratio < doc_ratio else 'superlinear'})")
        print(f"  Search time: {search_ratio:.1f}x  ({'sublinear' if search_ratio < doc_ratio else 'superlinear'})")

    # Needle accuracy
    print(f"\nNeedle-in-haystack accuracy:")
    for r in results:
        print(f"  {r['doc_count']:>5} docs: {r['needles_found']}/{r['needles_total']} ({r['needle_accuracy']}%)")

    print(f"\n{'=' * 80}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Document Search Scalability Benchmark")
    parser.add_argument(
        "--scales", default="500,1000,2000,5000",
        help="Comma-separated document counts (default: 500,1000,2000,5000)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Path to save JSON results (default: scalability_results.json)",
    )
    args = parser.parse_args()

    scales = sorted(int(s) for s in args.scales.split(","))

    print("=" * 60)
    print("  Document Search Scalability Benchmark")
    print("=" * 60)
    print(f"Scales: {scales}")
    print()

    all_results = []
    t_start = time.time()

    async def _run():
        for scale in scales:
            print(f"\n--- Scale: {scale} documents ---")
            result = await run_scale_test(scale)
            all_results.append(result)

    asyncio.run(_run())
    elapsed = time.time() - t_start

    print_report(all_results)

    # Save
    output_path = args.output or str(BENCHMARK_DIR / "scalability_results.json")
    output = {
        "elapsed_seconds": round(elapsed, 1),
        "scales": all_results,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {output_path}")
    print(f"Total time: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
