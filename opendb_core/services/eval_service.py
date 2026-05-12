"""Opt-in eval capture for real OpenDB search and recall traffic."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Iterable

from opendb_core.config import settings
from opendb_core.storage import get_backend

logger = logging.getLogger(__name__)
_capture_failure_logged = False


def now_ms(start: float) -> int:
    """Milliseconds elapsed since a ``time.perf_counter()`` value."""
    return max(0, int((time.perf_counter() - start) * 1000))


def is_capture_enabled() -> bool:
    return bool(settings.eval_capture_enabled)


async def capture_eval(
    *,
    tool_name: str,
    query: str,
    result_ids: Iterable[str],
    result_count: int,
    latency_ms: int,
    metadata: dict | None = None,
) -> None:
    """Best-effort capture. Never let telemetry break the hot path."""
    global _capture_failure_logged
    if not is_capture_enabled():
        return
    try:
        await get_backend().log_eval_capture(
            tool_name=tool_name,
            query=query,
            result_ids=list(result_ids),
            result_count=result_count,
            latency_ms=latency_ms,
            metadata=metadata or {},
        )
    except Exception as exc:
        if not _capture_failure_logged:
            logger.warning("Eval capture failed; continuing without capture: %s", exc)
            _capture_failure_logged = True
        return


async def export_eval_captures(
    *,
    limit: int = 1000,
    tool_name: str | None = None,
    output: Path | None = None,
) -> int:
    """Export recent eval captures as NDJSON. Returns row count."""
    rows = await get_backend().export_eval_captures(limit=limit, tool_name=tool_name)
    lines = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    payload = "\n".join(lines)
    if payload:
        payload += "\n"
    if output:
        output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return len(rows)
