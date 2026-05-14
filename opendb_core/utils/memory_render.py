"""Human-readable rendering helpers for OpenDB memories.

These helpers keep the memory surfaces white-box: every rendered memory should
show enough provenance to let an agent or human drill back to supporting
evidence when metadata provides it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any


_EVIDENCE_PATH_KEYS = ("file", "path", "source_path", "filename", "url")
_EVIDENCE_LOC_KEYS = ("lines", "line", "pages", "page", "section")
_KNOWN_METADATA_KEYS = {
    "date",
    "activity_start_time",
    "activity_end_time",
    "scene",
    "scene_name",
    "evidence",
    "source_message_ids",
}


def _compact_json(value: Any) -> str:
    """Return compact JSON for metadata snippets."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _one_line(value: Any, *, max_len: int = 240) -> str:
    """Collapse arbitrary values to a short single line."""
    text = str(value).replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _format_score(score: Any) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return str(score)
    if value == 0:
        return "0"
    if abs(value) < 0.0001:
        return "<0.0001"
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _metadata_dict(metadata: Any) -> dict[str, Any]:
    if isinstance(metadata, Mapping):
        return dict(metadata)
    return {}


def format_evidence(evidence: Any) -> str | None:
    """Format ``metadata.evidence`` as a compact drill-down hint.

    Supported shapes are intentionally loose so agents can attach evidence
    without a migration:

    - ``{"file": "docs/spec.md", "lines": "10-20", "tool": "opendb_read"}``
    - ``[{"file": "a.md", "pages": "2"}, {"file": "b.md"}]``
    - plain strings for externally supplied evidence notes
    """
    if evidence is None:
        return None

    if isinstance(evidence, str):
        return _one_line(evidence)

    if isinstance(evidence, Mapping):
        evidence_map = dict(evidence)
        path = next(
            (
                _one_line(evidence_map[key])
                for key in _EVIDENCE_PATH_KEYS
                if evidence_map.get(key)
            ),
            None,
        )
        locations = [
            f"{key} {evidence_map[key]}"
            for key in _EVIDENCE_LOC_KEYS
            if evidence_map.get(key)
        ]
        tool = evidence_map.get("tool")
        query = evidence_map.get("query")

        parts: list[str] = []
        if path:
            parts.append(path)
        if locations:
            parts.append(", ".join(_one_line(loc) for loc in locations))
        if tool:
            parts.append(f"via {_one_line(tool)}")
        if query:
            parts.append(f"query: {_one_line(query)}")

        if parts:
            return " | ".join(parts)
        return _compact_json(evidence_map)

    if isinstance(evidence, Sequence) and not isinstance(evidence, (bytes, bytearray)):
        rendered = [format_evidence(item) for item in evidence]
        rendered = [item for item in rendered if item]
        if rendered:
            return "; ".join(rendered)

    return _compact_json(evidence)


def memory_metadata_lines(metadata: Any) -> list[str]:
    """Return short metadata lines worth showing in recall/profile output."""
    meta = _metadata_dict(metadata)
    if not meta:
        return []

    lines: list[str] = []

    if meta.get("date"):
        lines.append(f"date: {_one_line(meta['date'])}")

    start = meta.get("activity_start_time")
    end = meta.get("activity_end_time")
    if start or end:
        if start and end:
            lines.append(f"activity: {_one_line(start)} to {_one_line(end)}")
        else:
            lines.append(f"activity: {_one_line(start or end)}")

    scene = meta.get("scene") or meta.get("scene_name")
    if scene:
        lines.append(f"scene: {_one_line(scene)}")

    evidence = format_evidence(meta.get("evidence"))
    if evidence:
        lines.append(f"evidence: {evidence}")

    source_message_ids = meta.get("source_message_ids")
    if source_message_ids:
        if isinstance(source_message_ids, Sequence) and not isinstance(
            source_message_ids, (str, bytes, bytearray)
        ):
            shown = ", ".join(_one_line(item, max_len=48) for item in source_message_ids[:5])
            extra = len(source_message_ids) - 5
            if extra > 0:
                shown += f" (+{extra} more)"
            lines.append(f"source messages: {shown}")
        else:
            lines.append(f"source messages: {_one_line(source_message_ids)}")

    small_extras = {
        key: value
        for key, value in meta.items()
        if key not in _KNOWN_METADATA_KEYS
        and isinstance(value, (str, int, float, bool))
    }
    if small_extras:
        extras = ", ".join(
            f"{key}={_one_line(value, max_len=80)}"
            for key, value in sorted(small_extras.items())
        )
        lines.append(f"metadata: {extras}")

    return lines


def memory_detail_lines(memory: Mapping[str, Any]) -> list[str]:
    """Return provenance/detail lines for a memory result."""
    details: list[str] = []

    source = memory.get("source") or "unknown"
    confidence = memory.get("confidence")
    pinned = bool(memory.get("pinned"))
    updated = memory.get("updated_at")

    provenance = [f"source: {source}"]
    if confidence is not None:
        try:
            provenance.append(f"confidence: {float(confidence):.4g}")
        except (TypeError, ValueError):
            provenance.append(f"confidence: {confidence}")
    if pinned:
        provenance.append("pinned")
    if updated:
        provenance.append(f"updated: {updated}")
    details.append("; ".join(provenance))

    superseded_id = memory.get("superseded_id")
    if superseded_id:
        details.append(f"supersedes: {superseded_id}")

    tags = memory.get("tags") or []
    if tags:
        details.append("tags: " + ", ".join(str(tag) for tag in tags))

    details.extend(memory_metadata_lines(memory.get("metadata")))
    details.append(f"id: {memory.get('memory_id', '?')}")
    return details


def format_memory_recall_response(data: Mapping[str, Any], query: str) -> str:
    """Format the REST ``/memory/recall`` response for MCP clients."""
    results = list(data.get("results", []) or [])
    total = int(data.get("total", 0) or 0)

    if not results:
        return f"No memories found for '{query}'"

    lines: list[str] = [f"Found {total} memories:"]
    lines.append("")
    for idx, memory in enumerate(results, start=1):
        mtype = memory.get("memory_type", "?")
        score = memory.get("score")
        score_part = ""
        if score is not None:
            score_part = f" score: {_format_score(score)}"
        lines.append(f"  {idx}. [{mtype}]{score_part}")
        lines.append(f"     {memory.get('content', '')}")
        for detail in memory_detail_lines(memory):
            lines.append(f"     {detail}")
        lines.append("")

    return "\n".join(lines)


def build_memory_profile(
    memories: Sequence[Mapping[str, Any]],
    *,
    total: int | None = None,
    generated_at: str | None = None,
) -> str:
    """Build a Markdown memory profile from stored memories.

    This is an on-demand, white-box summary. It does not synthesize new facts;
    it groups existing memories and keeps evidence pointers visible.
    """
    generated = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    shown_total = len(memories)
    actual_total = total if total is not None else shown_total

    lines: list[str] = [
        "# OpenDB Memory Profile",
        "",
        f"Generated: {generated}",
        f"Memories shown: {shown_total} of {actual_total}",
        "",
        "This profile is generated from stored memories. It preserves memory IDs, provenance, confidence, and evidence metadata so details can be verified before use.",
        "",
    ]

    pinned = [memory for memory in memories if memory.get("pinned")]
    by_type: dict[str, list[Mapping[str, Any]]] = {
        "semantic": [],
        "procedural": [],
        "episodic": [],
    }
    other: list[Mapping[str, Any]] = []
    for memory in memories:
        if memory.get("pinned"):
            continue
        mtype = str(memory.get("memory_type") or "unknown")
        if mtype in by_type:
            by_type[mtype].append(memory)
        else:
            other.append(memory)

    def add_section(title: str, entries: Sequence[Mapping[str, Any]]) -> None:
        lines.append(f"## {title}")
        if not entries:
            lines.append("")
            lines.append("_None._")
            lines.append("")
            return
        lines.append("")
        for memory in entries:
            mtype = memory.get("memory_type", "?")
            content = str(memory.get("content", "")).strip()
            lines.append(f"- [{mtype}] {content}")
            for detail in memory_detail_lines(memory):
                lines.append(f"  - {detail}")
        lines.append("")

    add_section("Pinned Context", pinned)
    add_section("Semantic Facts And Preferences", by_type["semantic"])
    add_section("Procedural Workflows And Rules", by_type["procedural"])
    add_section("Episodic Events And Outcomes", by_type["episodic"])
    if other:
        add_section("Other Memories", other)

    return "\n".join(lines).rstrip() + "\n"
