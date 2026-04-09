#!/usr/bin/env python3
"""
LongMemEval End-to-End Benchmark for OpenDB Memory Pipeline
================================================================

Full end-to-end evaluation matching the methodology used by OMEGA, Supermemory,
Mastra, etc. on the LongMemEval leaderboard.

Unlike longmemeval_bench.py (which only measures Recall@K), this script:
1. Stores all haystack sessions as memories
2. Recalls relevant memories given a question
3. Feeds recalled memories + question to an LLM to generate an answer
4. Grades the answer against ground truth using LLM-as-judge
5. Reports per-category accuracy (matching leaderboard format)

Evaluation categories (6 types):
  - single-session-user:       Recall user-stated facts from one session
  - single-session-assistant:  Recall assistant-stated facts from one session
  - single-session-preference: Recall user preferences
  - knowledge-update:          Return updated (not stale) information
  - temporal-reasoning:        Answer time-related questions across sessions
  - multi-session:             Reason across multiple conversation sessions

Comparison targets (LongMemEval leaderboard):
  - OMEGA:              95.4% (466/500)
  - Mastra (gpt-5-mini): 94.87%
  - Vectorize Hindsight: 91.4%
  - Emergence AI:        86.0%
  - Supermemory:         81.6%
  - Zep/Graphiti:        71.2%

Usage:
    python longmemeval_e2e_bench.py [--model MODEL] [--judge-model MODEL] [--limit N]

Requirements:
    pip install opendb openai python-dotenv
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
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Load .env from benchmark directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from openai import AsyncOpenAI

# Add project root to path so we can import opendb_core directly
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from opendb_core.storage import init_backend, get_backend, close_backend

# ============================================================
# Config
# ============================================================

BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = BENCHMARK_DIR / "longmemeval_oracle.json"

# LLM client (OpenRouter by default)
_llm_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    default_headers={
        "HTTP-Referer": "https://github.com/wuwangzhang1216/openDB",
        "X-Title": "openDB",
    },
)

# Default models
DEFAULT_GEN_MODEL = "minimax/minimax-m2.7"
DEFAULT_JUDGE_MODEL = "minimax/minimax-m2.7"

# Models that require reasoning mode via extra_body
_REASONING_MODELS = {"minimax/minimax-m2.7", "minimax/minimax-m2.5"}


def _extra_body(model: str) -> dict | None:
    """Return extra_body kwargs for OpenRouter if the model needs reasoning."""
    if model in _REASONING_MODELS:
        return {"reasoning": {"enabled": True}}
    return None

# How many memories to retrieve per question
RECALL_LIMIT = 15


def _memory_date(m: dict) -> str:
    """Extract the real session date from metadata, falling back to created_at."""
    meta = m.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    return meta.get("date", m.get("created_at", "unknown"))


@dataclass
class E2EResult:
    question_id: str
    question_type: str
    question: str
    ground_truth: str
    generated_answer: str
    is_correct: bool
    judge_score: float  # 0.0 or 1.0 (binary correct/incorrect)
    judge_explanation: str
    recall_time_ms: float
    store_time_ms: float
    generation_time_ms: float
    judging_time_ms: float
    recalled_count: int
    is_abstention: bool


# ============================================================
# Helpers
# ============================================================


def flatten_session(session: list[dict]) -> str:
    """Flatten a conversation session into a single text block."""
    parts = []
    for turn in session:
        role = turn["role"]
        content = turn["content"]
        parts.append(f"[{role}] {content}")
    return "\n".join(parts)


def load_dataset(path: str | Path) -> list[dict]:
    """Load the LongMemEval JSON dataset."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    print(f"Loaded {len(data)} questions from {Path(path).name}")
    return data


# ============================================================
# LLM Calls
# ============================================================

ANSWER_SYSTEM_PROMPT = """\
You are a personal AI assistant with access to conversation memories from past sessions.
Each memory is labeled with its session date. Memories are presented in chronological order.

Rules:
- Answer based ONLY on the provided memories. Do not make up information.
- If the memories do not contain enough information to answer, say: "I don't have enough information to answer that."
- TEMPORAL: Use session dates to determine chronological order. Earlier date = happened first.
- UPDATES: When the same topic appears in multiple memories, the LATEST date is current.
- COUNTING: Before giving a total, list each item you are counting across ALL memories.
- PREFERENCES: Look for what the user likes, dislikes, prefers, or habitually does.
- NUMBERS: Quote exact numbers from the memories. Do not round or approximate.
- Keep answers concise — typically 1-3 sentences.
"""

JUDGE_SYSTEM_PROMPT = """\
You are evaluating whether an AI assistant's answer is correct given the ground truth.

You will be given:
1. The question
2. The ground truth answer
3. The AI assistant's answer

Determine if the assistant's answer is CORRECT or INCORRECT.

Rules:
- The answer is CORRECT if it conveys the same key information as the ground truth, even if worded differently.
- The answer is CORRECT if it contains the ground truth answer as part of a longer response.
- For abstention questions (ground truth says "not enough information" or similar): the answer is CORRECT only if the assistant also indicates it cannot answer / doesn't have the information.
- Minor wording differences are OK. The core factual content must match.
- If the ground truth is a specific fact/number/name, the answer must include that fact.

You MUST respond with EXACTLY one word: CORRECT or INCORRECT
Do NOT include any other text, explanation, or formatting.
"""


async def generate_answer(
    question: str,
    memories: list[dict],
    model: str,
    question_date: str = "",
) -> tuple[str, float]:
    """Generate an answer using recalled memories + LLM."""
    # Format memories as context, sorted chronologically by real session date
    if memories:
        sorted_mems = sorted(memories, key=lambda m: _memory_date(m))
        memory_text = "\n\n---\n\n".join(
            f"Memory {i} (session date: {_memory_date(m)}):\n{m.get('content', '')}"
            for i, m in enumerate(sorted_mems, 1)
        )
        date_ctx = f"\nToday's date: {question_date}\n" if question_date else ""
        user_msg = f"Relevant memories from past conversations:\n\n{memory_text}\n\n---\n{date_ctx}\nUser question: {question}"
    else:
        user_msg = f"No relevant memories found.\n\nUser question: {question}"

    t0 = time.perf_counter()
    try:
        kwargs: dict = dict(
            model=model,
            messages=[
                {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=1000,  # reasoning models need headroom for chain-of-thought
        )
        eb = _extra_body(model)
        if eb:
            kwargs["extra_body"] = eb
        resp = await _llm_client.chat.completions.create(**kwargs)
        answer = resp.choices[0].message.content or ""
    except Exception as e:
        answer = f"ERROR: {e}"
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return answer.strip(), elapsed_ms


async def judge_answer(
    question: str,
    ground_truth: str,
    generated_answer: str,
    model: str,
    max_retries: int = 2,
) -> tuple[bool, float, str, float]:
    """Judge whether generated answer matches ground truth. Returns (is_correct, score, explanation, elapsed_ms)."""
    if not generated_answer or generated_answer.startswith("ERROR:"):
        return False, 0.0, "Empty or error answer", 0.0

    user_msg = (
        f"Question: {question}\n\n"
        f"Ground truth answer: {ground_truth}\n\n"
        f"AI assistant's answer: {generated_answer}"
    )

    t0 = time.perf_counter()
    for attempt in range(max_retries + 1):
        try:
            kwargs: dict = dict(
                model=model,
                messages=[
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=500,  # reasoning models need headroom for chain-of-thought
            )
            eb = _extra_body(model)
            if eb:
                kwargs["extra_body"] = eb
            resp = await _llm_client.chat.completions.create(**kwargs)
            raw = (resp.choices[0].message.content or "").strip().upper()
            if raw:
                is_correct = "CORRECT" in raw and "INCORRECT" not in raw
                explanation = raw
                elapsed_ms = (time.perf_counter() - t0) * 1000
                return is_correct, 1.0 if is_correct else 0.0, explanation, elapsed_ms
            # Empty response — retry
            if attempt < max_retries:
                await asyncio.sleep(0.5)
        except Exception as e:
            if attempt < max_retries:
                await asyncio.sleep(1.0)
            else:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                return False, 0.0, f"Judge error after {max_retries + 1} attempts: {e}", elapsed_ms

    # All retries returned empty
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return False, 0.0, "Judge returned empty after retries", elapsed_ms


# ============================================================
# Core benchmark
# ============================================================


async def run_single_question(
    question: dict,
    gen_model: str,
    judge_model: str,
) -> E2EResult:
    """Full E2E pipeline for a single question."""
    import uuid

    backend = get_backend()
    is_abstention = question["question_id"].endswith("_abs")

    session_ids = question["haystack_session_ids"]
    sessions = question["haystack_sessions"]
    dates = question.get("haystack_dates", [])

    # --- Store phase ---
    t0 = time.perf_counter()
    for i, (sid, session) in enumerate(zip(session_ids, sessions)):
        text = flatten_session(session)
        meta = {"session_id": sid}
        if i < len(dates):
            meta["date"] = dates[i]
        await backend.store_memory(
            memory_id=str(uuid.uuid4()),
            content=text,
            memory_type="episodic",
            tags=[sid],
            metadata=meta,
        )
    store_ms = (time.perf_counter() - t0) * 1000

    # --- Recall phase ---
    t1 = time.perf_counter()
    result = await backend.recall_memories(
        query=question["question"],
        memory_type=None,
        tags=None,
        limit=RECALL_LIMIT,
        offset=0,
    )
    recall_ms = (time.perf_counter() - t1) * 1000
    memories = result.get("results", [])

    # --- Generation phase ---
    generated_answer, gen_ms = await generate_answer(
        question["question"], memories, gen_model,
        question_date=question.get("question_date", ""),
    )

    # --- Judging phase ---
    is_correct, score, explanation, judge_ms = await judge_answer(
        question["question"],
        question["answer"],
        generated_answer,
        judge_model,
    )

    return E2EResult(
        question_id=question["question_id"],
        question_type=question["question_type"],
        question=question["question"],
        ground_truth=question["answer"],
        generated_answer=generated_answer,
        is_correct=is_correct,
        judge_score=score,
        judge_explanation=explanation,
        recall_time_ms=recall_ms,
        store_time_ms=store_ms,
        generation_time_ms=gen_ms,
        judging_time_ms=judge_ms,
        recalled_count=len(memories),
        is_abstention=is_abstention,
    )


async def _store_and_recall(
    idx: int,
    question: dict,
    tmp_root: Path,
) -> tuple[int, dict, list[dict], float, float, bool]:
    """Phase 1: store sessions + recall memories (sequential, uses global backend)."""
    import uuid as _uuid

    is_abstention = question["question_id"].endswith("_abs")
    db_path = tmp_root / f"q{idx}.db"
    await init_backend("sqlite", db_path=str(db_path))

    try:
        backend = get_backend()
        session_ids = question["haystack_session_ids"]
        sessions = question["haystack_sessions"]
        dates = question.get("haystack_dates", [])

        # Store
        t0 = time.perf_counter()
        for i, (sid, session) in enumerate(zip(session_ids, sessions)):
            text = flatten_session(session)
            meta = {"session_id": sid}
            if i < len(dates):
                meta["date"] = dates[i]
            await backend.store_memory(
                memory_id=str(_uuid.uuid4()),
                content=text,
                memory_type="episodic",
                tags=[sid],
                metadata=meta,
            )
        store_ms = (time.perf_counter() - t0) * 1000

        # Recall
        t1 = time.perf_counter()
        result = await backend.recall_memories(
            query=question["question"],
            memory_type=None,
            tags=None,
            limit=RECALL_LIMIT,
            offset=0,
        )
        recall_ms = (time.perf_counter() - t1) * 1000
        memories = result.get("results", [])
    finally:
        await close_backend(str(db_path))

    return idx, question, memories, store_ms, recall_ms, is_abstention


async def _generate_and_judge(
    idx: int,
    question: dict,
    memories: list[dict],
    store_ms: float,
    recall_ms: float,
    is_abstention: bool,
    gen_model: str,
    judge_model: str,
    sem: asyncio.Semaphore,
) -> E2EResult:
    """Phase 2: LLM generation + judging (concurrent)."""
    async with sem:
        generated_answer, gen_ms = await generate_answer(
            question["question"], memories, gen_model,
            question_date=question.get("question_date", ""),
        )
        is_correct, score, explanation, judge_ms = await judge_answer(
            question["question"],
            question["answer"],
            generated_answer,
            judge_model,
        )

    return E2EResult(
        question_id=question["question_id"],
        question_type=question["question_type"],
        question=question["question"],
        ground_truth=question["answer"],
        generated_answer=generated_answer,
        is_correct=is_correct,
        judge_score=score,
        judge_explanation=explanation,
        recall_time_ms=recall_ms,
        store_time_ms=store_ms,
        generation_time_ms=gen_ms,
        judging_time_ms=judge_ms,
        recalled_count=len(memories),
        is_abstention=is_abstention,
    )


async def run_benchmark(
    data: list[dict],
    gen_model: str,
    judge_model: str,
    limit: int | None = None,
    concurrency: int = 8,
) -> list[E2EResult]:
    """Run the full E2E benchmark.

    Phase 1 (sequential): store sessions + recall memories — uses global backend state.
    Phase 2 (concurrent):  LLM generation + judging — pure API calls, safe to parallelize.
    """
    questions = data
    if limit:
        questions = questions[:limit]

    total = len(questions)
    abstention_count = sum(1 for q in questions if q["question_id"].endswith("_abs"))
    print(f"Running {total} questions ({abstention_count} abstention)")
    print(f"Generation model: {gen_model}")
    print(f"Judge model: {judge_model}")
    print(f"Recall limit: {RECALL_LIMIT}")
    print(f"LLM concurrency: {concurrency}")
    print()

    tmp_root = Path(tempfile.mkdtemp(prefix="opendb_e2e_"))

    # ---- Phase 1: store + recall (sequential) ----
    print("Phase 1: Store & recall (sequential)...")
    phase1_results = []
    for idx, q in enumerate(questions):
        sr = await _store_and_recall(idx, q, tmp_root)
        phase1_results.append(sr)
        if (idx + 1) % 100 == 0 or idx + 1 == total:
            print(f"  [{idx + 1}/{total}] recalled")

    shutil.rmtree(tmp_root, ignore_errors=True)

    # ---- Phase 2: generate + judge (concurrent) ----
    print(f"\nPhase 2: Generate & judge (concurrency={concurrency})...")
    sem = asyncio.Semaphore(concurrency)
    completed = [0]

    async def _run_phase2(sr: tuple) -> E2EResult:
        idx, question, memories, store_ms, recall_ms, is_abstention = sr
        r = await _generate_and_judge(
            idx, question, memories, store_ms, recall_ms, is_abstention,
            gen_model, judge_model, sem,
        )
        completed[0] += 1
        if completed[0] % 20 == 0 or completed[0] == total:
            print(f"  [{completed[0]}/{total}] done")
        return r

    results = await asyncio.gather(*[_run_phase2(sr) for sr in phase1_results])
    results = list(results)

    # Print running accuracy
    correct = sum(1 for r in results if r.is_correct)
    print(f"\nFinal accuracy: {correct}/{total} ({correct/total*100:.1f}%)")

    return results


# ============================================================
# Reporting
# ============================================================


def print_report(results: list[E2EResult]) -> dict:
    """Print formatted report and return summary dict."""
    n = len(results)
    correct = sum(1 for r in results if r.is_correct)
    accuracy = correct / n * 100

    print("\n" + "=" * 70)
    print("LongMemEval E2E Benchmark — OpenDB Memory Pipeline")
    print("=" * 70)
    print(f"Questions evaluated: {n}")
    print(f"Overall accuracy: {accuracy:.1f}% ({correct}/{n})")
    print()

    summary = {
        "total": n,
        "correct": correct,
        "accuracy": round(accuracy, 1),
        "overall": {},
        "by_type": {},
    }

    # Leaderboard comparison
    print("Leaderboard Comparison:")
    print("-" * 50)
    leaderboard = [
        ("OMEGA", 95.4),
        ("Mastra (gpt-5-mini)", 94.87),
        ("MemMachine", 93.0),
        ("Vectorize Hindsight", 91.4),
        ("Emergence AI", 86.0),
        ("Supermemory", 81.6),
        ("Zep/Graphiti", 71.2),
    ]
    # Insert OpenDB at the right position
    inserted = False
    for name, score in leaderboard:
        if not inserted and accuracy >= score:
            print(f"  >>> OpenDB (FTS):  {accuracy:5.1f}%  <<<")
            inserted = True
        print(f"  {name:<25} {score:5.1f}%")
    if not inserted:
        print(f"  >>> OpenDB (FTS):  {accuracy:5.1f}%  <<<")

    # By question type
    print()
    print("Accuracy by Question Type:")
    print("-" * 60)
    by_type: dict[str, list[E2EResult]] = defaultdict(list)
    for r in results:
        by_type[r.question_type].append(r)

    for qtype in sorted(by_type.keys()):
        type_results = by_type[qtype]
        type_correct = sum(1 for r in type_results if r.is_correct)
        type_total = len(type_results)
        type_acc = type_correct / type_total * 100
        summary["by_type"][qtype] = {
            "count": type_total,
            "correct": type_correct,
            "accuracy": round(type_acc, 1),
        }
        print(f"  {qtype:<30} {type_acc:5.1f}% ({type_correct}/{type_total})")

    # Abstention accuracy
    abs_results = [r for r in results if r.is_abstention]
    non_abs_results = [r for r in results if not r.is_abstention]
    if abs_results:
        abs_correct = sum(1 for r in abs_results if r.is_correct)
        abs_acc = abs_correct / len(abs_results) * 100
        print(f"\n  Abstention questions:       {abs_acc:5.1f}% ({abs_correct}/{len(abs_results)})")
        summary["abstention"] = {
            "count": len(abs_results),
            "correct": abs_correct,
            "accuracy": round(abs_acc, 1),
        }
    if non_abs_results:
        non_abs_correct = sum(1 for r in non_abs_results if r.is_correct)
        non_abs_acc = non_abs_correct / len(non_abs_results) * 100
        print(f"  Non-abstention questions:   {non_abs_acc:5.1f}% ({non_abs_correct}/{len(non_abs_results)})")
        summary["non_abstention"] = {
            "count": len(non_abs_results),
            "correct": non_abs_correct,
            "accuracy": round(non_abs_acc, 1),
        }

    # Timing
    recall_times = [r.recall_time_ms for r in results]
    store_times = [r.store_time_ms for r in results]
    gen_times = [r.generation_time_ms for r in results]
    print()
    print("Timing:")
    print("-" * 50)
    print(f"  Recall     — median: {statistics.median(recall_times):.1f}ms, "
          f"p95: {sorted(recall_times)[int(len(recall_times) * 0.95)]:.1f}ms")
    print(f"  Store      — median: {statistics.median(store_times):.1f}ms")
    print(f"  Generation — median: {statistics.median(gen_times):.0f}ms")

    summary["timing"] = {
        "recall_median_ms": round(statistics.median(recall_times), 1),
        "recall_p95_ms": round(sorted(recall_times)[int(len(recall_times) * 0.95)], 1),
        "store_median_ms": round(statistics.median(store_times), 1),
        "generation_median_ms": round(statistics.median(gen_times), 0),
    }

    # Failure analysis
    wrong = [r for r in results if not r.is_correct]
    if wrong:
        print()
        print(f"Incorrect answers ({len(wrong)}):")
        print("-" * 60)
        wrong_by_type = defaultdict(int)
        for r in wrong:
            wrong_by_type[r.question_type] += 1
        for qtype, count in sorted(wrong_by_type.items(), key=lambda x: -x[1]):
            print(f"  {qtype:<30} {count}")

        print()
        print("  Sample failures (first 5):")
        for r in wrong[:5]:
            print(f"    {r.question_id} [{r.question_type}]")
            print(f"      Q: {str(r.question)[:80]}...")
            print(f"      Expected: {str(r.ground_truth)[:80]}")
            print(f"      Got:      {str(r.generated_answer)[:80]}")
            print(f"      Judge:    {str(r.judge_explanation)[:80]}")
            print()

    print("=" * 70)
    return summary


# ============================================================
# Main
# ============================================================


def main() -> None:
    global RECALL_LIMIT  # noqa: PLW0603

    parser = argparse.ArgumentParser(
        description="LongMemEval E2E benchmark for OpenDB memory pipeline"
    )
    parser.add_argument(
        "--data", default=str(DEFAULT_DATA),
        help="Path to longmemeval_oracle.json",
    )
    parser.add_argument(
        "--model", default=DEFAULT_GEN_MODEL,
        help=f"LLM model for answer generation (default: {DEFAULT_GEN_MODEL})",
    )
    parser.add_argument(
        "--judge-model", default=DEFAULT_JUDGE_MODEL,
        help=f"LLM model for judging (default: {DEFAULT_JUDGE_MODEL})",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limit number of questions (for quick testing)",
    )
    parser.add_argument(
        "--recall-limit", type=int, default=RECALL_LIMIT,
        help=f"Number of memories to recall per question (default: {RECALL_LIMIT})",
    )
    parser.add_argument(
        "--concurrency", type=int, default=8,
        help="Max concurrent LLM calls for generation+judging (default: 8)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Path to save JSON results (default: longmemeval_e2e_results.json)",
    )
    args = parser.parse_args()
    RECALL_LIMIT = args.recall_limit

    data = load_dataset(args.data)

    t_start = time.time()
    results = asyncio.run(
        run_benchmark(data, args.model, args.judge_model, limit=args.limit, concurrency=args.concurrency)
    )
    elapsed = time.time() - t_start

    summary = print_report(results)
    summary["elapsed_seconds"] = round(elapsed, 1)
    summary["config"] = {
        "gen_model": args.model,
        "judge_model": args.judge_model,
        "recall_limit": RECALL_LIMIT,
    }

    # Save detailed results
    output_path = args.output or str(BENCHMARK_DIR / "longmemeval_e2e_results.json")
    detail = {
        "summary": summary,
        "questions": [
            {
                "question_id": r.question_id,
                "question_type": r.question_type,
                "question": r.question,
                "ground_truth": r.ground_truth,
                "generated_answer": r.generated_answer,
                "is_correct": r.is_correct,
                "judge_score": r.judge_score,
                "judge_explanation": r.judge_explanation,
                "recall_time_ms": round(r.recall_time_ms, 2),
                "store_time_ms": round(r.store_time_ms, 2),
                "generation_time_ms": round(r.generation_time_ms, 2),
                "judging_time_ms": round(r.judging_time_ms, 2),
                "recalled_count": r.recalled_count,
                "is_abstention": r.is_abstention,
            }
            for r in results
        ],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(detail, f, indent=2, ensure_ascii=False)
    print(f"\nDetailed results saved to {output_path}")
    print(f"Total time: {elapsed:.1f}s ({elapsed/60:.1f}min)")


if __name__ == "__main__":
    main()
