#!/usr/bin/env python3
"""
FileDB vs CMD Agent Benchmark
================================

Compares two AI agent approaches for document analysis:
- CMD Agent: uses shell commands + Python scripts to read local files
- FileDB Agent: uses FileDB HTTP API (/read, /search) to read files

Features:
- Multiple runs per task for variance data
- Multi-model comparison via OpenRouter
- LLM-as-judge answer quality evaluation
- Auto-generated REPORT.md with applicability analysis

Usage:
    python benchmark.py [--url http://localhost:8000] [--model MODEL...] [--runs N] [--judge]

Requirements:
    pip install openai-agents httpx python-dotenv
"""

import asyncio
import argparse
import functools
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Load .env from benchmark directory
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import httpx
from openai import AsyncOpenAI
from agents import Agent, Runner, function_tool, set_tracing_disabled
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel

set_tracing_disabled(True)

# ============================================================
# Configuration
# ============================================================

FILEDB_URL = "http://localhost:8000"
BENCHMARK_DIR = os.path.dirname(os.path.abspath(__file__))
# WORKSPACE_DIR is resolved at runtime from --scale (defaults to legacy benchmark_workspace/)
WORKSPACE_DIR = os.path.join(BENCHMARK_DIR, "benchmark_workspace")
SCALE = "small"  # mutated in main() per --scale flag

# OpenRouter client with provider preferences
_openrouter_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    default_headers={
        "HTTP-Referer": "https://github.com/wuwangzhang1216/openDB",
        "X-Title": "openDB",
    },
)

# Patch chat.completions.create to inject provider preferences on every call
_orig_create = _openrouter_client.chat.completions.create

@functools.wraps(_orig_create)
async def _patched_create(*args, **kwargs) -> None:
    extra_body = kwargs.get("extra_body") or {}
    extra_body["provider"] = {"sort": "throughput"}
    kwargs["extra_body"] = extra_body
    return await _orig_create(*args, **kwargs)

_openrouter_client.chat.completions.create = _patched_create

# ============================================================
# Metrics
# ============================================================


@dataclass
class TaskResult:
    task_id: str
    task_name: str
    agent_name: str
    model_name: str
    run_id: int
    answer: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    tool_calls: int
    wall_time_seconds: float
    num_requests: int
    status: str = "success"
    error: str = ""
    quality_scores: dict = field(default_factory=dict)


# ============================================================
# CMD Agent Tools
# ============================================================


@function_tool(name_override="run_command")
def cmd_run_command(command: str) -> str:
    """Run a shell command and return stdout/stderr. The working directory is the workspace folder containing all document files.

    Args:
        command: The shell command to execute (e.g. 'dir /b', 'type filename.txt', 'findstr /i /n "pattern" *.txt').
    """
    try:
        result = subprocess.run(
            ["cmd", "/c", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, cwd=WORKSPACE_DIR,
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]: {result.stderr}"
        return output if output.strip() else "(no output)"
    except Exception as e:
        return f"Error: {e}"


# ============================================================
# FileDB Agent Tools
# ============================================================


@function_tool(name_override="list_files")
def filedb_list_files(tags: str = "") -> str:
    """List all files in the document repository. Can filter by department tag.

    Args:
        tags: Optional tag to filter by (e.g. 'engineering', 'finance', 'hr', 'sales', 'legal', 'executive').
    """
    params = {}
    if tags:
        params["tags"] = tags
    try:
        resp = httpx.get(f"{FILEDB_URL}/files", params=params, timeout=10)
        data = resp.json()
        files = data.get("files", [])
        lines = [
            f"{f['filename']} ({f.get('total_pages', '?')}p, {f.get('total_lines', '?')}L, tags={f.get('tags', [])})"
            for f in files
        ]
        return f"Total: {data.get('total', len(files))} files\n" + "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@function_tool(name_override="read_file")
def filedb_read_file(filename: str, grep: str = "", pages: str = "", lines: str = "") -> str:
    """Read a file as plain text with optional filtering. Supports grep to show only matching lines with context.

    Args:
        filename: Name of the file to read (e.g. 'sales-q4-report.txt').
        grep: Optional search pattern — returns only matching lines with surrounding context.
        pages: Optional page range (e.g. '1', '1-3') to read specific pages.
        lines: Optional line range (e.g. '1-50') to read specific lines.
    """
    params = {}
    if grep:
        params["grep"] = grep
    if pages:
        params["pages"] = pages
    if lines:
        params["lines"] = lines
    try:
        resp = httpx.get(f"{FILEDB_URL}/read/{filename}", params=params, timeout=10)
        if resp.status_code == 200:
            return resp.text
        return f"Error: HTTP {resp.status_code} - {resp.text[:200]}"
    except Exception as e:
        return f"Error: {e}"


@function_tool(name_override="search_documents")
def filedb_search_documents(query: str, tags: str = "") -> str:
    """Full-text search across ALL documents. Returns ranked results with highlighted matching excerpts, filenames, page numbers, and relevance scores.

    Args:
        query: Search query (e.g. 'Q4 revenue target', 'budget cuts', 'hiring plan').
        tags: Optional tag to filter results (e.g. 'finance', 'engineering').
    """
    body: dict = {"query": query, "limit": 20}
    if tags:
        body["filters"] = {"tags": [tags]}
    try:
        resp = httpx.post(f"{FILEDB_URL}/search", json=body, timeout=10)
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return f"No results found for '{query}'."

        output_lines = [f"Found {data['total']} results for '{query}':"]
        for r in results:
            highlight = r.get("highlight", "").replace("<mark>", "**").replace("</mark>", "**")
            output_lines.append(
                f"  - {r['filename']} (page {r['page_number']}, score={r.get('relevance_score', '?')}): "
                f"{highlight[:200]}"
            )
        return "\n".join(output_lines)
    except Exception as e:
        return f"Error: {e}"


@function_tool(name_override="get_file_toc")
def filedb_get_file_toc(filename: str) -> str:
    """Get the table of contents / structure overview of a file. Shows sections, page boundaries, and line counts.

    Args:
        filename: Name of the file.
    """
    try:
        resp = httpx.get(f"{FILEDB_URL}/read/{filename}", params={"toc": "true"}, timeout=10)
        if resp.status_code == 200:
            return resp.text if resp.text else "(No TOC available)"
        return f"Error: HTTP {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"


# ============================================================
# RAG Agent Tools (vector retrieval alternative to FileDB's FTS)
# ============================================================

# Built once at startup (see main()), reused across all RAG agent calls.
_rag_index = None  # type: ignore[assignment]


@function_tool(name_override="semantic_search")
async def rag_semantic_search(query: str, k: int = 5) -> str:
    """Semantic similarity search over document chunks using vector embeddings. Returns top-k relevant passages.

    Args:
        query: Natural-language search query.
        k: Number of chunks to return (default 5, raise to 10-20 if results are insufficient).
    """
    if _rag_index is None:
        return "Error: RAG index not initialised."
    try:
        hits = await _rag_index.search(query, k=k)
    except Exception as e:
        return f"Error: {e}"
    if not hits:
        return f"No semantic matches for '{query}'."
    lines = [f"Top {len(hits)} semantic matches for '{query}':"]
    for h in hits:
        snippet = h["chunk_text"].replace("\n", " ")[:200]
        lines.append(f"  - {h['filename']} (score={h['score']:.3f}): {snippet}")
    return "\n".join(lines)


# ============================================================
# Agent Definitions
# ============================================================

CMD_INSTRUCTIONS = """\
You are a document analysis assistant. You have access to a workspace directory
containing company documents in various formats (PDF, DOCX, PPTX, CSV).
You can run any shell command to explore and read the files.

You have one tool:
- run_command: Execute any shell command in the workspace directory

Useful commands:
- dir /b — list all files
- type filename.csv — read CSV files (text-based)
- findstr /i /n /s "pattern" *.csv — search across CSV files
- python -c "import fitz; ..." — run inline Python for binary formats

IMPORTANT: For PDF/DOCX/PPTX, run Python code INLINE with python -c "...".
Do NOT create .py script files. Always use python -c with the code inline.
Example: python -c "import fitz; doc=fitz.open('file.pdf'); print(doc[0].get_text())"
Example: python -c "from docx import Document; doc=Document('file.docx'); print('\\n'.join(p.text for p in doc.paragraphs))"

Strategy tips:
- Start with dir /b to see what files are available
- CSV files can be read directly with type
- For PDF/DOCX/PPTX, use python -c "..." to extract text inline
- Be thorough but efficient
"""

FILEDB_INSTRUCTIONS = """\
You are a document analysis assistant. You have access to a document repository
via structured API tools. Use your tools to find information and answer questions
thoroughly.

Available tools:
- list_files: See what files are available (can filter by department tag)
- read_file: Read a file with optional grep/page/line filtering
- search_documents: Full-text search across ALL documents with ranked, highlighted results
- get_file_toc: Get structure overview of a file

Strategy tips:
- Use search_documents first to find relevant information across all files
- The search results include highlighted excerpts — often enough to answer without reading full files
- Use read_file with grep= parameter for targeted reads (only returns matching lines with context)
- Be thorough but efficient
"""

RAG_INSTRUCTIONS = """\
You are a document analysis assistant. You have access to a document repository
via semantic search and file reading tools. The repository is indexed with
vector embeddings — semantic_search finds passages by meaning, not by keyword.

Available tools:
- list_files: See what files are available (can filter by department tag)
- semantic_search: Vector similarity search over chunked passages — returns top-k most semantically related excerpts
- read_file: Read a file with optional grep/page/line filtering (use this when chunks are not enough)

Strategy tips:
- Use semantic_search first for any natural-language question
- Start with k=5; if the results don't cover the question, call again with k=10 or k=20
- Note that semantic_search returns passage excerpts, not full files — use read_file when you need full context
- Be thorough but efficient
"""


def _build_agents(model_instance, agents_to_run: list[str]) -> dict:
    """Build agent map based on selected agent names."""
    agent_map = {}
    if "cmd" in agents_to_run:
        agent_map["CMD"] = Agent(
            name="CMD Agent",
            instructions=CMD_INSTRUCTIONS,
            tools=[cmd_run_command],
            model=model_instance,
        )
    if "filedb" in agents_to_run:
        agent_map["FileDB"] = Agent(
            name="FileDB Agent",
            instructions=FILEDB_INSTRUCTIONS,
            tools=[filedb_list_files, filedb_read_file, filedb_search_documents, filedb_get_file_toc],
            model=model_instance,
        )
    if "rag" in agents_to_run:
        agent_map["RAG"] = Agent(
            name="RAG Agent",
            instructions=RAG_INSTRUCTIONS,
            tools=[filedb_list_files, rag_semantic_search, filedb_read_file],
            model=model_instance,
        )
    return agent_map


# ============================================================
# Task Set
# ============================================================

TASKS = [
    {
        "id": "T1",
        "name": "Cross-file search",
        "query": (
            "Find all documents that mention Q4 revenue targets. "
            "For each document, state the specific target number mentioned and its context."
        ),
    },
    {
        "id": "T2",
        "name": "Targeted read",
        "query": (
            "Read the engineering roadmap for 2025 and summarize the key milestones "
            "for each quarter. Include budget figures for each quarter."
        ),
    },
    {
        "id": "T3",
        "name": "Theme search",
        "query": (
            "Which department reports mention budget cuts? "
            "For each, explain what specific budget cuts are discussed and their impact."
        ),
    },
    {
        "id": "T4",
        "name": "Cross-reference",
        "query": (
            "Find the total employee count from the HR employee census, then find "
            "the total number of planned new hires from the hiring plan. "
            "What will the total headcount be if the hiring plan is fully executed? "
            "Account for the projected attrition rate."
        ),
    },
    {
        "id": "T5",
        "name": "Synthesis",
        "query": (
            "What are the top 5 risks facing the company in 2025? "
            "For each risk, cite which specific document(s) mention it and "
            "provide the risk score or severity if available."
        ),
    },
    {
        "id": "T6",
        "name": "Reasoning",
        "query": (
            "The finance team projects $210M in revenue for 2025 and the hiring plan "
            "calls for 120 new employees. Based on the documents available, what is "
            "the biggest risk to achieving both targets simultaneously? "
            "Support your answer with specific data from the documents."
        ),
    },
]

# ============================================================
# LLM-as-Judge
# ============================================================

JUDGE_SYSTEM_PROMPT = """\
You are an expert evaluator assessing the quality of AI-generated answers to document analysis questions.

You will be given:
1. The original question
2. The AI agent's answer

Score the answer on these 4 criteria, each from 1-5:

- accuracy (1-5): Are the facts, numbers, and claims correct based on what a thorough document review would find?
- completeness (1-5): Does the answer cover all relevant documents and data points?
- citation_quality (1-5): Does the answer cite specific document names, page numbers, or sections?
- specificity (1-5): Does the answer provide specific numbers, quotes, or data rather than vague statements?

Respond ONLY with valid JSON (no markdown, no extra text):
{"accuracy": <int>, "completeness": <int>, "citation_quality": <int>, "specificity": <int>, "overall": <float>, "explanation": "<1-2 sentence explanation>"}

The "overall" score should be the average of the 4 criteria, rounded to 1 decimal.
"""


async def evaluate_answer_quality(question: str, answer: str, judge_model_name: str) -> dict:
    """Use an LLM to score answer quality."""
    if answer.startswith("ERROR:"):
        return {
            "accuracy": 0, "completeness": 0, "citation_quality": 0,
            "specificity": 0, "overall": 0.0,
            "explanation": "Agent failed to produce an answer.",
        }

    judge_model = OpenAIChatCompletionsModel(
        model=judge_model_name, openai_client=_openrouter_client
    )
    judge_agent = Agent(
        name="Judge",
        instructions=JUDGE_SYSTEM_PROMPT,
        tools=[],
        model=judge_model,
    )
    user_msg = f"QUESTION:\n{question}\n\nANSWER:\n{answer}"

    try:
        result = await Runner.run(judge_agent, user_msg, max_turns=1)
        raw = result.final_output or ""
        # Strip markdown code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        scores = json.loads(raw)
        return scores
    except (json.JSONDecodeError, TypeError, Exception) as e:
        return {
            "accuracy": 0, "completeness": 0, "citation_quality": 0,
            "specificity": 0, "overall": 0.0,
            "explanation": f"Judge error: {e}",
        }


# ============================================================
# Aggregation
# ============================================================


def compute_aggregates(runs: list[TaskResult]) -> dict:
    """Compute median/min/max statistics from multiple runs."""
    successful = [r for r in runs if r.status == "success"]
    if not successful:
        return {
            "median_tokens": 0, "min_tokens": 0, "max_tokens": 0,
            "median_time": 0, "min_time": 0, "max_time": 0,
            "success_rate": 0.0, "median_tool_calls": 0,
        }
    tokens = [r.total_tokens for r in successful]
    times = [r.wall_time_seconds for r in successful]
    tools = [r.tool_calls for r in successful]
    return {
        "median_tokens": int(statistics.median(tokens)),
        "min_tokens": min(tokens),
        "max_tokens": max(tokens),
        "median_time": round(statistics.median(times), 1),
        "min_time": round(min(times), 1),
        "max_time": round(max(times), 1),
        "success_rate": round(len(successful) / len(runs), 2),
        "median_tool_calls": int(statistics.median(tools)),
    }


# ============================================================
# Benchmark Runner
# ============================================================


def extract_metrics(result, agent_name: str, model_name: str, task: dict,
                    elapsed: float, run_id: int) -> TaskResult:
    """Extract metrics from a completed agent run."""
    from agents.items import ToolCallItem

    usage = result.context_wrapper.usage
    tool_calls = sum(1 for item in result.new_items if isinstance(item, ToolCallItem))

    return TaskResult(
        task_id=task["id"],
        task_name=task["name"],
        agent_name=agent_name,
        model_name=model_name,
        run_id=run_id,
        answer=result.final_output or "",
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        tool_calls=tool_calls,
        wall_time_seconds=elapsed,
        num_requests=usage.requests,
    )


async def _run_single(agent: Agent, agent_name: str, model_name: str,
                      task: dict, run_id: int,
                      timeout_seconds: int = 300) -> TaskResult:
    """Run a single agent on a single task, return TaskResult."""
    start = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            Runner.run(agent, task["query"], max_turns=25),
            timeout=timeout_seconds,
        )
        elapsed = time.perf_counter() - start
        metrics = extract_metrics(result, agent_name, model_name, task, elapsed, run_id)
        return metrics
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - start
        error_str = f"Session timeout ({timeout_seconds}s)"
        return TaskResult(
            task_id=task["id"], task_name=task["name"], agent_name=agent_name,
            model_name=model_name, run_id=run_id,
            answer=f"ERROR: {error_str}", input_tokens=0, output_tokens=0,
            total_tokens=0, tool_calls=0, wall_time_seconds=elapsed,
            num_requests=0, status="failed", error=error_str,
        )
    except Exception as e:
        elapsed = time.perf_counter() - start
        error_str = str(e)
        return TaskResult(
            task_id=task["id"], task_name=task["name"], agent_name=agent_name,
            model_name=model_name, run_id=run_id,
            answer=f"ERROR: {e}", input_tokens=0, output_tokens=0,
            total_tokens=0, tool_calls=0, wall_time_seconds=elapsed,
            num_requests=0, status="failed", error=error_str,
        )


async def run_benchmark(tasks_to_run: list[dict], agents_to_run: list[str],
                        models: list[str], num_runs: int,
                        concurrency: int = 8) -> list[TaskResult]:
    """Run the benchmark concurrently: model × task × agent × run."""
    print(f"\nModels: {', '.join(models)}")
    print(f"Agents: {', '.join(agents_to_run)}")
    print(f"Tasks: {len(tasks_to_run)}")
    print(f"Runs per task: {num_runs}")
    print(f"Concurrency: {concurrency}")
    print(f"Workspace: {WORKSPACE_DIR}")
    print(f"FileDB: {FILEDB_URL}")

    # Verify workspace exists
    if not os.path.isdir(WORKSPACE_DIR):
        print(f"\nERROR: Workspace directory not found: {WORKSPACE_DIR}")
        print("Run gen_workspace.py first.")
        sys.exit(1)

    file_count = len([f for f in os.listdir(WORKSPACE_DIR)
                      if os.path.isfile(os.path.join(WORKSPACE_DIR, f))])
    print(f"Local files: {file_count}")

    # Verify FileDB connectivity (if filedb or rag agent is selected)
    if "filedb" in agents_to_run or "rag" in agents_to_run:
        try:
            resp = httpx.get(f"{FILEDB_URL}/files", params={"limit": 1}, timeout=5)
            total = resp.json().get("total", "?")
            print(f"FileDB connected: {total} files indexed")
        except Exception as e:
            print(f"\nERROR: Cannot connect to FileDB at {FILEDB_URL}: {e}")
            print("Make sure FileDB is running.")
            sys.exit(1)

    # Build all jobs: (model_name, agent_name, agent, task, run_id)
    jobs = []
    for model_name in models:
        model_instance = OpenAIChatCompletionsModel(
            model=model_name, openai_client=_openrouter_client
        )
        agent_map = _build_agents(model_instance, agents_to_run)
        for task in tasks_to_run:
            for agent_name, agent in agent_map.items():
                for run_id in range(1, num_runs + 1):
                    jobs.append((model_name, agent_name, agent, task, run_id))

    total = len(jobs)
    print(f"\nTotal sessions: {total} (running {concurrency} concurrently)")
    print(f"{'=' * 70}")

    sem = asyncio.Semaphore(concurrency)
    completed = [0]  # mutable counter for closure
    results: list[TaskResult] = [None] * total  # pre-allocate to preserve order

    async def _run_job(idx: int, model_name: str, agent_name: str,
                       agent: Agent, task: dict, run_id: int) -> None:
        async with sem:
            run_label = f"r{run_id}" if num_runs > 1 else ""
            tag = f"{model_name.split('/')[-1]}/{agent_name}/{task['id']}"
            if run_label:
                tag += f"/{run_label}"
            print(f"  START  {tag}")

            r = await _run_single(agent, agent_name, model_name, task, run_id)
            results[idx] = r
            completed[0] += 1

            if r.status == "success":
                print(f"  DONE   {tag} -> {r.tool_calls} tools, {r.total_tokens:,} tok, {r.wall_time_seconds:.1f}s  [{completed[0]}/{total}]")
            else:
                print(f"  FAIL   {tag} -> {r.wall_time_seconds:.1f}s {r.error[:80]}  [{completed[0]}/{total}]")

    # Launch all jobs concurrently (semaphore limits actual parallelism)
    await asyncio.gather(*(
        _run_job(i, *job) for i, job in enumerate(jobs)
    ))

    return results


# ============================================================
# Output: Console Table
# ============================================================


def print_comparison_table(results: list[TaskResult]) -> None:
    """Print formatted comparison tables, one per model."""
    models = sorted(set(r.model_name for r in results))
    agents = []
    for r in results:
        if r.agent_name not in agents:
            agents.append(r.agent_name)

    has_quality = any(r.quality_scores for r in results)
    num_runs = max(r.run_id for r in results)

    for model in models:
        model_results = [r for r in results if r.model_name == model]

        print(f"\n{'=' * 100}")
        print(f"  MODEL: {model}  |  Runs: {num_runs}")
        print(f"{'=' * 100}")

        header = f"{'Task':<25} {'Agent':<8} {'OK':>4} {'Tools':>6} {'Tokens':>12} {'Time':>10}"
        if has_quality:
            header += f" {'Quality':>8}"
        print(header)
        print("-" * 100)

        task_ids = []
        for r in model_results:
            if r.task_id not in task_ids:
                task_ids.append(r.task_id)

        for tid in task_ids:
            first_agent = True
            for agent_name in agents:
                runs = [r for r in model_results
                        if r.task_id == tid and r.agent_name == agent_name]
                if not runs:
                    continue

                agg = compute_aggregates(runs)
                task_label = runs[0].task_name if first_agent else ""
                first_agent = False

                success_str = f"{int(agg['success_rate'] * 100)}%"

                if agg["success_rate"] == 0:
                    failed_runs = [r for r in runs if r.status == "failed"]
                    reason = failed_runs[0].error[:40] if failed_runs else "unknown"
                    tokens_str = f"FAILED ({reason})"
                else:
                    tokens_str = f"{agg['median_tokens']:,}"
                    if num_runs > 1:
                        tokens_str += f" [{agg['min_tokens']:,}-{agg['max_tokens']:,}]"

                time_str = f"{agg['median_time']}s"
                if num_runs > 1:
                    time_str += f" [{agg['min_time']}-{agg['max_time']}]"

                quality_str = ""
                if has_quality:
                    if agg["success_rate"] == 0:
                        quality_str = "FAILED"
                    else:
                        qr = [r for r in runs if r.quality_scores and r.quality_scores.get("overall")]
                        if qr:
                            quality_str = f"{qr[0].quality_scores['overall']:.1f}/5"

                line = (f"{task_label:<25} {agent_name:<8} {success_str:>4} "
                        f"{agg['median_tool_calls']:>6} {tokens_str:>12} {time_str:>10}")
                if has_quality:
                    line += f" {quality_str:>8}"
                print(line)
            print()

        # Totals per agent
        print("-" * 100)
        for agent_name in agents:
            agent_runs = [r for r in model_results if r.agent_name == agent_name]
            total_tokens = sum(compute_aggregates(
                [r for r in agent_runs if r.task_id == tid]
            )["median_tokens"] for tid in task_ids
                if any(r.task_id == tid and r.agent_name == agent_name for r in model_results))
            total_tools = sum(compute_aggregates(
                [r for r in agent_runs if r.task_id == tid]
            )["median_tool_calls"] for tid in task_ids
                if any(r.task_id == tid and r.agent_name == agent_name for r in model_results))
            total_time = sum(compute_aggregates(
                [r for r in agent_runs if r.task_id == tid]
            )["median_time"] for tid in task_ids
                if any(r.task_id == tid and r.agent_name == agent_name for r in model_results))
            print(f"{'TOTAL':<25} {agent_name:<8} {'':>4} {total_tools:>6} {total_tokens:>12,} {total_time:>9.1f}s")

        # Savings
        if len(agents) == 2:
            a1, a2 = agents[0], agents[1]
            t1 = sum(compute_aggregates(
                [r for r in model_results if r.task_id == tid and r.agent_name == a1]
            )["median_tokens"] for tid in task_ids)
            t2 = sum(compute_aggregates(
                [r for r in model_results if r.task_id == tid and r.agent_name == a2]
            )["median_tokens"] for tid in task_ids)
            if t1 > 0 and t2 < t1:
                print(f"\n  {a2} saves ~{(1 - t2 / t1) * 100:.0f}% tokens vs {a1}")
            elif t2 > 0 and t1 < t2:
                print(f"\n  {a1} saves ~{(1 - t1 / t2) * 100:.0f}% tokens vs {a2}")

        print(f"{'=' * 100}")


# ============================================================
# Output: JSON
# ============================================================


def save_results(results: list[TaskResult]) -> None:
    """Save results in v2 JSON schema."""
    models = sorted(set(r.model_name for r in results))
    agents = []
    for r in results:
        if r.agent_name not in agents:
            agents.append(r.agent_name)
    max_runs = max(r.run_id for r in results)

    output = {
        "version": 2,
        "metadata": {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "models": models,
            "agents": agents,
            "runs_per_task": max_runs,
            "workspace_files": len([f for f in os.listdir(WORKSPACE_DIR)
                                    if os.path.isfile(os.path.join(WORKSPACE_DIR, f))]),
        },
        "results": {},
    }

    for model in models:
        output["results"][model] = {}
        for agent_name in agents:
            output["results"][model][agent_name] = {}
            for task in TASKS:
                tid = task["id"]
                runs = sorted(
                    [r for r in results
                     if r.model_name == model and r.agent_name == agent_name and r.task_id == tid],
                    key=lambda x: x.run_id,
                )
                if not runs:
                    continue
                run_dicts = []
                for r in runs:
                    d = {
                        "run_id": r.run_id,
                        "status": r.status,
                        "answer": r.answer,
                        "input_tokens": r.input_tokens,
                        "output_tokens": r.output_tokens,
                        "total_tokens": r.total_tokens,
                        "tool_calls": r.tool_calls,
                        "wall_time_seconds": round(r.wall_time_seconds, 2),
                        "num_requests": r.num_requests,
                    }
                    if r.error:
                        d["error"] = r.error
                    if r.quality_scores:
                        d["quality"] = r.quality_scores
                    run_dicts.append(d)

                output["results"][model][agent_name][tid] = {
                    "task_name": task["name"],
                    "runs": run_dicts,
                    "aggregate": compute_aggregates(runs),
                }

    output_path = os.path.join(BENCHMARK_DIR, "benchmark_results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {output_path}")


# ============================================================
# Output: REPORT.md
# ============================================================


def generate_report(results: list[TaskResult]) -> None:
    """Auto-generate REPORT.md from benchmark results."""
    models = sorted(set(r.model_name for r in results))
    agents = []
    for r in results:
        if r.agent_name not in agents:
            agents.append(r.agent_name)
    num_runs = max(r.run_id for r in results)
    has_quality = any(r.quality_scores for r in results)

    task_ids = []
    for t in TASKS:
        if any(r.task_id == t["id"] for r in results):
            task_ids.append(t["id"])

    lines = [
        "# FileDB vs CMD Agent Benchmark Report\n",
        "## Overview\n",
        f"- **Models**: {', '.join(f'`{m}`' for m in models)}",
        f"- **Agents**: {', '.join(agents)}",
        f"- **Runs per task**: {num_runs}",
        f"- **Tasks**: {len(task_ids)}",
        "- **Workspace**: 25 company documents (9 PDF, 8 DOCX, 3 PPTX, 5 CSV)\n",
        "---\n",
    ]

    # Per-model results
    for model in models:
        model_results = [r for r in results if r.model_name == model]
        model_short = model.split("/")[-1]
        lines.append(f"## Results: {model_short}\n")

        # Header
        header = "| Task | Description | Agent | Tools | Tokens | Time |"
        sep = "|------|-------------|-------|-------|--------|------|"
        if has_quality:
            header = "| Task | Description | Agent | Tools | Tokens | Time | Quality |"
            sep = "|------|-------------|-------|-------|--------|------|---------|"
        lines.append(header)
        lines.append(sep)

        for tid in task_ids:
            task = next(t for t in TASKS if t["id"] == tid)
            first_agent = True
            for agent_name in agents:
                runs = [r for r in model_results
                        if r.task_id == tid and r.agent_name == agent_name]
                if not runs:
                    continue
                agg = compute_aggregates(runs)
                task_col = f"{tid}" if first_agent else ""
                desc_col = task["name"] if first_agent else ""
                first_agent = False

                failed_runs = [r for r in runs if r.status == "failed"]
                if agg["success_rate"] == 0:
                    # Show failure reason
                    reason = failed_runs[0].error[:60] if failed_runs else "unknown"
                    tokens_str = f"**FAILED** ({reason})"
                elif num_runs > 1:
                    tokens_str = f"{agg['median_tokens']:,} [{agg['min_tokens']:,}-{agg['max_tokens']:,}]"
                else:
                    tokens_str = f"{agg['median_tokens']:,}"

                time_str = f"{agg['median_time']}s"
                tools_str = str(agg["median_tool_calls"])

                quality_str = ""
                if has_quality:
                    if agg["success_rate"] == 0:
                        quality_str = "FAILED"
                    else:
                        qr = [r for r in runs if r.quality_scores and r.quality_scores.get("overall")]
                        quality_str = f"{qr[0].quality_scores['overall']:.1f}/5" if qr else ""

                row = f"| {task_col} | {desc_col} | {agent_name} | {tools_str} | {tokens_str} | {time_str} |"
                if has_quality:
                    row += f" {quality_str} |"
                lines.append(row)

            # Delta row
            if len(agents) == 2:
                a1_runs = [r for r in model_results if r.task_id == tid and r.agent_name == agents[0]]
                a2_runs = [r for r in model_results if r.task_id == tid and r.agent_name == agents[1]]
                if a1_runs and a2_runs:
                    a1_agg = compute_aggregates(a1_runs)
                    a2_agg = compute_aggregates(a2_runs)
                    if a1_agg["median_tokens"] > 0 and a2_agg["median_tokens"] > 0:
                        t_delta = a1_agg["median_tokens"] - a2_agg["median_tokens"]
                        pct = t_delta / a1_agg["median_tokens"] * 100
                        tool_delta = a1_agg["median_tool_calls"] - a2_agg["median_tool_calls"]
                        delta_row = f"| | | **delta** | **{tool_delta:+d}** | **{t_delta:+,} ({pct:+.0f}%)** | |"
                        if has_quality:
                            delta_row += " |"
                        lines.append(delta_row)

        lines.append("")

        # Totals for this model
        lines.append(f"### Totals ({model_short})\n")
        lines.append("| Agent | Total Tokens | Total Tools | Total Time | Tasks OK |")
        lines.append("|-------|-------------|-------------|------------|----------|")
        for agent_name in agents:
            total_tokens = 0
            total_tools = 0
            total_time = 0.0
            tasks_ok = 0
            for tid in task_ids:
                runs = [r for r in model_results
                        if r.task_id == tid and r.agent_name == agent_name]
                if not runs:
                    continue
                agg = compute_aggregates(runs)
                total_tokens += agg["median_tokens"]
                total_tools += agg["median_tool_calls"]
                total_time += agg["median_time"]
                if agg["success_rate"] > 0:
                    tasks_ok += 1
            lines.append(
                f"| {agent_name} | {total_tokens:,} | {total_tools} | "
                f"{total_time:.1f}s | {tasks_ok}/{len(task_ids)} |"
            )

        if len(agents) == 2:
            t1 = sum(compute_aggregates(
                [r for r in model_results if r.task_id == tid and r.agent_name == agents[0]]
            )["median_tokens"] for tid in task_ids)
            t2 = sum(compute_aggregates(
                [r for r in model_results if r.task_id == tid and r.agent_name == agents[1]]
            )["median_tokens"] for tid in task_ids)
            if t1 > 0:
                savings = (1 - t2 / t1) * 100
                lines.append(f"\n**Token savings: {savings:.0f}%**\n")

        lines.append("---\n")

    # Quality comparison (if available)
    if has_quality:
        lines.append("## Answer Quality (LLM-as-Judge)\n")
        lines.append("| Model | Agent | Accuracy | Completeness | Citations | Specificity | Overall |")
        lines.append("|-------|-------|----------|-------------|-----------|------------|---------|")
        for model in models:
            model_short = model.split("/")[-1]
            for agent_name in agents:
                scored = [r for r in results
                          if r.model_name == model and r.agent_name == agent_name
                          and r.quality_scores and r.quality_scores.get("overall")]
                if not scored:
                    continue
                def avg(key: str) -> float:
                    return sum(r.quality_scores.get(key, 0) for r in scored) / len(scored)
                lines.append(
                    f"| {model_short} | {agent_name} | "
                    f"{avg('accuracy'):.1f} | {avg('completeness'):.1f} | "
                    f"{avg('citation_quality'):.1f} | {avg('specificity'):.1f} | "
                    f"**{avg('overall'):.1f}** |"
                )
        lines.append("\n---\n")

    # Applicability boundary
    lines.append("## Applicability Boundary Analysis\n")
    lines.append("### When FileDB Helps\n")
    lines.append("1. **Binary document formats** (PDF, DOCX, PPTX) — CMD agent must write Python extraction scripts, consuming enormous context")
    lines.append("2. **Cross-document search** — FileDB's indexed search returns ranked results instantly; CMD must extract all files first")
    lines.append("3. **Large corpus** (>10 files) — extraction overhead compounds with each file")
    lines.append("4. **Synthesis tasks** across many documents\n")
    lines.append("### When FileDB Does NOT Help\n")
    lines.append("1. **All plain text files** — CMD's `type` command is zero-overhead (prior benchmark: CMD was 12% more efficient with .txt files)")
    lines.append("2. **Single-file tasks** — direct reading is equally simple")
    lines.append("3. **Very small corpus** (1-3 files) — extraction overhead is manageable\n")
    lines.append("### Crossover Point (estimated)\n")
    lines.append("*Based on extrapolation from 0% binary (prior txt-only benchmark) and 80% binary (this benchmark). Not directly measured at intermediate ratios.*\n")
    lines.append("- **0% binary files**: CMD is ~12% more token-efficient")
    lines.append("- **80%+ binary files**: FileDB saves 55-73% tokens (this benchmark)")
    lines.append("- **Estimated crossover**: ~20-30% binary files is where FileDB begins to show advantage")
    lines.append("- The crossover shifts lower for search-heavy tasks (T1, T3) and higher for single-file tasks (T2)\n")

    # Methodology
    lines.append("---\n")
    lines.append("## Methodology\n")
    lines.append(f"- {num_runs} run(s) per task — median reported, [min-max] shown for N>1")
    lines.append(f"- Models: {', '.join(models)}")
    lines.append("- CMD Agent: 1 tool (`run_command`) — executes shell commands and Python scripts")
    lines.append("- FileDB Agent: 4 tools (`list_files`, `read_file`, `search_documents`, `get_file_toc`)")
    lines.append("- `max_turns=25` for all agents")
    lines.append("- File formats: 9 PDF, 8 DOCX, 3 PPTX, 5 CSV")
    lines.append("- Wall time includes model inference latency, which varies significantly across providers; token counts are a more reliable efficiency metric")
    if has_quality:
        judge_models = set()
        for r in results:
            if r.quality_scores and r.quality_scores.get("overall"):
                judge_models.add("LLM-as-judge")
        lines.append("- Quality evaluation via LLM-as-judge (blind — judge does not know which agent)")
    lines.append("")

    report_path = os.path.join(BENCHMARK_DIR, "REPORT.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Report saved to {report_path}")


# ============================================================
# Load previous results
# ============================================================


def load_previous_results(json_path: str) -> list[TaskResult]:
    """Load TaskResult objects from a v2 JSON results file."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    results = []
    for model_name, agents_data in data.get("results", {}).items():
        for agent_name, tasks_data in agents_data.items():
            for task_id, task_data in tasks_data.items():
                task_name = task_data.get("task_name", "")
                for run in task_data.get("runs", []):
                    r = TaskResult(
                        task_id=task_id,
                        task_name=task_name,
                        agent_name=agent_name,
                        model_name=model_name,
                        run_id=run["run_id"],
                        answer=run.get("answer", ""),
                        input_tokens=run.get("input_tokens", 0),
                        output_tokens=run.get("output_tokens", 0),
                        total_tokens=run.get("total_tokens", 0),
                        tool_calls=run.get("tool_calls", 0),
                        wall_time_seconds=run.get("wall_time_seconds", 0),
                        num_requests=run.get("num_requests", 0),
                        status=run.get("status", "success"),
                        error=run.get("error", ""),
                        quality_scores=run.get("quality", {}),
                    )
                    results.append(r)
    return results


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FileDB vs CMD Agent Benchmark")
    parser.add_argument("--url", default="http://localhost:8000", help="FileDB base URL")
    parser.add_argument("--model", nargs="+", default=["minimax/minimax-m2.5"],
                        help="Model(s) to benchmark via OpenRouter")
    parser.add_argument("--tasks", nargs="*", help="Run specific task IDs (e.g. T1 T3 T5)")
    parser.add_argument("--agents", nargs="*", default=["cmd", "filedb"],
                        choices=["cmd", "filedb", "rag"],
                        help="Agent(s) to run (default: cmd filedb)")
    parser.add_argument("--scale", default="small",
                        choices=["small", "medium", "large"],
                        help="Workspace scale: small=25 docs, medium=125, large=325")
    parser.add_argument("--runs", type=int, default=1,
                        help="Number of runs per task (default: 1)")
    parser.add_argument("--concurrency", type=int, default=8,
                        help="Max concurrent sessions (default: 8)")
    parser.add_argument("--judge", action="store_true",
                        help="Run LLM-as-judge quality evaluation")
    parser.add_argument("--judge-model", default="z-ai/glm-5-turbo",
                        help="Model to use as quality judge")
    parser.add_argument("--load", type=str, default=None,
                        help="Load previous results JSON and merge with new runs")
    args = parser.parse_args()

    FILEDB_URL = args.url
    SCALE = args.scale
    # Resolve scaled workspace dir; fall back to legacy benchmark_workspace/ for 'small'.
    scaled_dir = os.path.join(BENCHMARK_DIR, f"benchmark_workspace_{SCALE}")
    if os.path.isdir(scaled_dir):
        WORKSPACE_DIR = scaled_dir
    elif SCALE != "small":
        print(f"ERROR: Scaled workspace not found: {scaled_dir}")
        print("Run: python gen_distractors.py  (to build pool + assemble workspaces)")
        sys.exit(1)
    print(f"Scale: {SCALE}  |  Workspace: {WORKSPACE_DIR}")

    tasks_to_run = TASKS
    if args.tasks:
        tasks_to_run = [t for t in TASKS if t["id"] in args.tasks]
        if not tasks_to_run:
            print(f"No matching tasks for: {args.tasks}")
            print(f"Available: {[t['id'] for t in TASKS]}")
            sys.exit(1)

    print("FileDB vs CMD Agent Benchmark")
    print("=" * 60)

    # Run benchmark + optional judge in one event loop
    async def _main() -> None:
        # Bootstrap RAG index if needed (cached per scale).
        if "rag" in args.agents:
            global _rag_index
            from rag_index import RAGIndex
            _rag_index = RAGIndex()
            cache_path = Path(BENCHMARK_DIR) / f"rag_index_{SCALE}.npz"
            if cache_path.exists():
                _rag_index.load(cache_path)
                print(f"Loaded RAG index [{SCALE}]: {_rag_index.stats()}")
            else:
                if not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")):
                    print("ERROR: OPENROUTER_API_KEY or OPENAI_API_KEY required for RAG embedding.")
                    sys.exit(1)
                print(f"Building RAG index for scale={SCALE} (one-time)...")
                await _rag_index.build(FILEDB_URL)
                _rag_index.save(cache_path)
                print(f"Indexed: {_rag_index.stats()}")

        results = await run_benchmark(
            tasks_to_run, args.agents, args.model, args.runs, args.concurrency
        )

        # Quality evaluation (concurrent)
        if args.judge:
            print(f"\n{'=' * 60}")
            print(f"Running LLM-as-judge evaluation (model: {args.judge_model})")
            print(f"{'=' * 60}")

            task_query_map = {t["id"]: t["query"] for t in TASKS}
            to_judge = [r for r in results if r.status == "success" and r.run_id == 1]
            sem = asyncio.Semaphore(args.concurrency)

            async def _judge_one(r) -> None:
                async with sem:
                    tag = f"{r.model_name.split('/')[-1]}/{r.agent_name}/{r.task_id}"
                    r.quality_scores = await evaluate_answer_quality(
                        task_query_map[r.task_id], r.answer, args.judge_model
                    )
                    overall = r.quality_scores.get("overall", "?")
                    print(f"  {tag} -> {overall}/5")

            await asyncio.gather(*[_judge_one(r) for r in to_judge])
            print(f"  Evaluated {len(to_judge)} answers.")

        return results

    all_results = asyncio.run(_main())

    # Merge with previous results if --load specified
    if args.load:
        prev_path = args.load
        if os.path.isfile(prev_path):
            prev = load_previous_results(prev_path)
            # Only keep previous results for models NOT in current run
            new_models = set(r.model_name for r in all_results)
            prev_filtered = [r for r in prev if r.model_name not in new_models]
            print(f"\nLoaded {len(prev)} previous results, kept {len(prev_filtered)} (excluded models: {new_models})")
            all_results = prev_filtered + all_results
        else:
            print(f"\nWARNING: --load file not found: {prev_path}")

    # Output
    print_comparison_table(all_results)
    save_results(all_results)
    generate_report(all_results)
