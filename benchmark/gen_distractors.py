#!/usr/bin/env python3
"""
Generate distractor documents and assemble scaled benchmark workspaces.

The signal workspace (25 hand-authored docs with ground-truth answers) must
already exist at benchmark_workspace/ via gen_workspace.py. This script:

1. Builds a pool of N LLM-generated distractor documents (default 300).
2. Filters distractors that accidentally leak ground-truth phrases.
3. Assembles benchmark_workspace_{small,medium,large}/ by copying signal +
   first K distractors (0 / 100 / 300 respectively).

Usage:
    pip install openai python-dotenv
    python gen_distractors.py              # generate pool + assemble all scales
    python gen_distractors.py --count 100  # smaller pool
    python gen_distractors.py --assemble-only  # reuse existing pool
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from openai import AsyncOpenAI

# Import format helpers from gen_workspace (sync functions)
sys.path.insert(0, str(Path(__file__).parent))
from gen_workspace import _create_docx, _create_pdf, _create_pptx  # noqa: E402

BENCHMARK_DIR = Path(__file__).parent
SIGNAL_DIR = BENCHMARK_DIR / "benchmark_workspace"
POOL_DIR = BENCHMARK_DIR / "distractors_pool"
POOL_TEXT_DIR = POOL_DIR / "_text_cache"  # stores raw LLM output

SCALES = {
    "small": 0,
    "medium": 100,
    "large": 300,
}

DEPARTMENTS = [
    "engineering", "sales", "hr", "finance", "legal", "executive",
    "operations", "product", "marketing", "customer-success",
]

DOCTYPES = [
    "quarterly-review", "memo", "runbook", "proposal", "postmortem",
    "training-material", "meeting-notes", "vendor-contract",
    "policy-update", "research-brief",
]

# Length tiers: (name, word_count, weight)
# Capped at ~2500 words to fit within 4096-output-token limit of free-tier LLMs.
LENGTH_TIERS = [
    ("short", 600, 0.30),
    ("medium", 1500, 0.50),
    ("long", 2500, 0.20),
]

# Output-format distribution matching signal corpus (~36/32/12/20 PDF/DOCX/PPTX/CSV)
FORMATS = [("pdf", 0.36), ("docx", 0.32), ("pptx", 0.12), ("csv", 0.20)]

# Years to use for distractors (avoid 2025 — that's the signal year)
YEARS = ["2022", "2023", "2024"]
QUARTERS = ["q1", "q2", "q3", "q4"]

# Ground-truth phrases from the 6 tasks — distractors must NOT contain these.
# Match is case-insensitive substring.
BANNED_PHRASES = [
    "q4 revenue target", "engineering roadmap", "budget cuts",
    "employee census", "hiring plan", "top 5 risks", "risk register",
    "$210m", "210 million", "120 new employees", "120 new hires",
    "attrition rate", "total headcount",
    "revenue forecast", "revenue targets",
    "cash flow analysis", "compliance review",
    "strategic priorities 2025", "all-hands meeting", "board presentation",
    "ip portfolio", "contract summary q4",
]

# ============================================================
# OpenRouter client (shared with benchmark.py)
# ============================================================

_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY", ""),
)


def weighted_choice(rng: random.Random, choices: list[tuple]) -> object:
    """Pick an item from [(value, weight), ...] using rng."""
    total = sum(w for _, w in choices)
    r = rng.uniform(0, total)
    upto = 0
    for value, weight in choices:
        upto += weight
        if upto >= r:
            return value
    return choices[-1][0]


def make_distractor_spec(idx: int, seed: int) -> dict:
    """Deterministic metadata for distractor #idx."""
    rng = random.Random(seed * 100_000 + idx)
    dept = rng.choice(DEPARTMENTS)
    doctype = rng.choice(DOCTYPES)
    year = rng.choice(YEARS)
    quarter = rng.choice(QUARTERS) if rng.random() < 0.4 else None
    length_name = weighted_choice(rng, [(n, w) for n, _, w in LENGTH_TIERS])
    word_count = next(w for n, w, _ in LENGTH_TIERS if n == length_name)
    fmt = weighted_choice(rng, FORMATS)
    suffix = f"-{quarter}" if quarter else ""
    filename_base = f"d{idx:03d}-{dept}-{doctype}-{year}{suffix}"
    return {
        "idx": idx,
        "filename_base": filename_base,
        "dept": dept,
        "doctype": doctype,
        "year": year,
        "quarter": quarter,
        "length_name": length_name,
        "word_count": word_count,
        "format": fmt,
    }


def build_prompt(spec: dict) -> str:
    """Build LLM prompt for one distractor document."""
    qpart = f" {spec['quarter'].upper()}" if spec["quarter"] else ""
    return f"""Write a realistic internal company document. Requirements:

- Department: {spec['dept']}
- Document type: {spec['doctype']}
- Time period: {spec['year']}{qpart}
- Length: approximately {spec['word_count']} words
- Use RST-style headings: H1 underlined with === and H2 underlined with ---
- Include 3-5 sections with realistic section titles
- Use made-up but plausible numbers, names, metrics — do NOT reference real companies
- Do NOT mention "Q4 revenue target", "hiring plan", "budget cuts", "risks facing the company"
- Do NOT use the year 2025 anywhere
- Write in a dry corporate tone

Output ONLY the document content — no preamble, no code fences, no commentary.
Start with the H1 title."""


async def generate_one_distractor(spec: dict, model: str, sem: asyncio.Semaphore) -> str | None:
    """Generate one distractor via LLM. Returns text or None on failure."""
    async with sem:
        prompt = build_prompt(spec)
        for attempt in range(3):
            try:
                resp = await _client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.5,
                    max_tokens=min(4000, int(spec["word_count"] * 1.8)),
                    extra_body={"provider": {"sort": "throughput"}},
                )
                text = (resp.choices[0].message.content or "").strip()
                # Strip code fences if the model wrapped output
                if text.startswith("```"):
                    lines = text.split("\n")
                    text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
                if len(text) < 200:
                    continue
                if _contains_banned(text):
                    continue  # try again
                return text
            except Exception as e:
                # Longer backoff on rate limits (429) — they can be long-lived upstream.
                err_str = str(e)
                is_rate_limit = "429" in err_str or "rate" in err_str.lower()
                wait = 30 * (attempt + 1) if is_rate_limit else 5
                print(f"    [attempt {attempt+1}] {spec['filename_base']}: {e} (sleep {wait}s)")
                await asyncio.sleep(wait)
        return None


def _contains_banned(text: str) -> bool:
    lower = text.lower()
    return any(p in lower for p in BANNED_PHRASES)


def _csv_from_text(text: str, rng: random.Random) -> str:
    """Convert prose to a simple synthetic CSV table (for fmt='csv' distractors)."""
    # Use section titles as row categories; fake numeric columns.
    rows = [line.strip() for line in text.split("\n")
            if line.strip() and not line.strip().startswith(("=", "-"))]
    rows = [r for r in rows if 10 < len(r) < 80][:30]
    if not rows:
        rows = ["Item A", "Item B", "Item C"]
    header = "category,q1_value,q2_value,q3_value,q4_value,annual_total"
    lines = [header]
    for r in rows:
        cat = r.replace(",", ";")[:60]
        q = [rng.randint(1000, 99999) for _ in range(4)]
        lines.append(f"{cat},{q[0]},{q[1]},{q[2]},{q[3]},{sum(q)}")
    return "\n".join(lines)


def materialize(spec: dict, text: str) -> Path:
    """Write distractor to its final format file in POOL_DIR."""
    fmt = spec["format"]
    out_path = POOL_DIR / f"{spec['filename_base']}.{fmt}"
    try:
        if fmt == "csv":
            rng = random.Random(spec["idx"])
            out_path.write_text(_csv_from_text(text, rng), encoding="utf-8")
        elif fmt == "docx":
            _create_docx(str(out_path), text)
        elif fmt == "pdf":
            _create_pdf(str(out_path), text)
        elif fmt == "pptx":
            _create_pptx(str(out_path), text)
    except Exception as e:
        print(f"    FAILED to materialize {out_path.name}: {e}")
        return None
    return out_path


async def build_pool(count: int, seed: int, model: str, concurrency: int = 8) -> list[Path]:
    """Generate `count` distractors into POOL_DIR. Cached on disk."""
    POOL_DIR.mkdir(exist_ok=True)
    POOL_TEXT_DIR.mkdir(exist_ok=True)

    specs = [make_distractor_spec(i, seed) for i in range(count)]
    sem = asyncio.Semaphore(concurrency)

    async def _gen(spec: dict) -> Path | None:
        text_cache = POOL_TEXT_DIR / f"{spec['filename_base']}.txt"
        out_path = POOL_DIR / f"{spec['filename_base']}.{spec['format']}"
        if out_path.exists() and text_cache.exists():
            return out_path  # cached
        if text_cache.exists():
            text = text_cache.read_text(encoding="utf-8")
        else:
            text = await generate_one_distractor(spec, model, sem)
            if text is None:
                return None
            text_cache.write_text(text, encoding="utf-8")
        return materialize(spec, text)

    print(f"Generating pool of {count} distractors (concurrency={concurrency})...")
    results = await asyncio.gather(*(_gen(s) for s in specs))
    materialized = [r for r in results if r is not None]
    print(f"  materialized: {len(materialized)}/{count}")
    return materialized


def assemble_workspace(scale: str, pool: list[Path]) -> Path:
    """Copy signal + first K distractors into benchmark_workspace_<scale>/."""
    if scale not in SCALES:
        raise ValueError(f"Unknown scale: {scale}")
    if not SIGNAL_DIR.exists():
        raise FileNotFoundError(
            f"Signal workspace not found at {SIGNAL_DIR}. Run gen_workspace.py first."
        )

    target = BENCHMARK_DIR / f"benchmark_workspace_{scale}"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir()

    # Copy signal files
    signal_count = 0
    for src in SIGNAL_DIR.iterdir():
        if src.is_file():
            shutil.copy2(src, target / src.name)
            signal_count += 1

    # Copy first K distractors (sorted by idx → deterministic nesting)
    k = SCALES[scale]
    pool_sorted = sorted(pool, key=lambda p: p.name)[:k]
    for src in pool_sorted:
        shutil.copy2(src, target / src.name)

    print(f"  {target.name}: {signal_count} signal + {len(pool_sorted)} distractors = {signal_count + len(pool_sorted)} files")
    return target


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate benchmark distractors")
    parser.add_argument("--count", type=int, default=300, help="Pool size")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model", default="deepseek/deepseek-chat-v3-0324",
                        help="OpenRouter model for generation")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--assemble-only", action="store_true",
                        help="Skip generation; reuse existing pool")
    args = parser.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY") and not args.assemble_only:
        print("ERROR: OPENROUTER_API_KEY not set (put in benchmark/.env)")
        sys.exit(1)

    if args.assemble_only:
        pool = sorted(p for p in POOL_DIR.iterdir() if p.is_file() and p.suffix != ".txt")
        if not pool:
            print(f"ERROR: no distractors in {POOL_DIR}")
            sys.exit(1)
        print(f"Reusing pool of {len(pool)} distractors")
    else:
        pool = await build_pool(args.count, args.seed, args.model, args.concurrency)

    print("\nAssembling scaled workspaces...")
    for scale in SCALES:
        assemble_workspace(scale, pool)

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
