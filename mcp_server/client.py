"""HTTP client for OpenDB REST API."""

from __future__ import annotations

import json
import os

import httpx

OPENDB_URL = os.environ.get("OPENDB_URL", "http://localhost:8000")

_client: httpx.AsyncClient | None = None


async def get_client() -> httpx.AsyncClient:
    """Get or create the shared httpx client."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(base_url=OPENDB_URL, timeout=60.0)
    return _client


async def close_client() -> None:
    """Close the shared httpx client."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None


def _handle_error(response: httpx.Response) -> str:
    """Format error response into a readable string."""
    if response.status_code == 404:
        try:
            data = response.json()
            return f"Error: {data.get('detail', 'Not found')}"
        except Exception:
            return "Error: Resource not found"
    if response.status_code == 409:
        try:
            data = response.json()
            candidates = data.get("candidates", [])
            names = [c.get("filename", c.get("id", "?")) for c in candidates]
            return f"Error: Ambiguous filename. Candidates: {', '.join(names)}"
        except Exception:
            return "Error: Ambiguous filename"
    if response.status_code == 400:
        try:
            data = response.json()
            return f"Error: {data.get('detail', 'Bad request')}"
        except Exception:
            return "Error: Bad request"
    return f"Error: OpenDB returned status {response.status_code}"


async def read_file(
    filename: str,
    numbered: bool = False,
    pages: str | None = None,
    lines: str | None = None,
    grep: str | None = None,
    format: str | None = None,
) -> str:
    """Call GET /read/{filename} with optional parameters."""
    client = await get_client()
    params: dict[str, str] = {}
    if numbered:
        params["numbered"] = "true"
    if pages:
        params["pages"] = pages
    if lines:
        params["lines"] = lines
    if grep:
        params["grep"] = grep
    if format:
        params["format"] = format

    response = await client.get(f"/read/{filename}", params=params)

    if response.status_code != 200:
        return _handle_error(response)

    if format == "json":
        return json.dumps(response.json(), indent=2, ensure_ascii=False)

    return response.text


async def search(
    query: str,
    mode: str = "fts",
    path: str | None = None,
    glob: str | None = None,
    case_insensitive: bool = False,
    context: int = 0,
    limit: int = 20,
    offset: int = 0,
    filters: dict | None = None,
    max_results: int = 100,
) -> str:
    """Call POST /search."""
    client = await get_client()
    body: dict = {"query": query, "mode": mode, "limit": limit, "offset": offset}
    if path:
        body["path"] = path
    if glob:
        body["glob"] = glob
    if case_insensitive:
        body["case_insensitive"] = True
    if context:
        body["context"] = context
    if filters:
        body["filters"] = filters
    if max_results != 100:
        body["max_results"] = max_results

    response = await client.post("/search", json=body)

    if response.status_code != 200:
        return _handle_error(response)

    data = response.json()

    # Format results as readable text
    if data.get("error"):
        return f"Error: {data['error']}"

    results = data.get("results", [])
    total = data.get("total", 0)

    if not results:
        return f"No results found for '{query}'"

    # Split grep vs fts results (grep rows have 'file' key, fts rows have 'filename').
    grep_rows = [r for r in results if r.get("file")]
    fts_rows = [r for r in results if not r.get("file") and r.get("filename")]

    lines_out: list[str] = []

    if grep_rows:
        lines_out.append(f"Found {total} results:")
        lines_out.append("")
        for r in grep_rows:
            ctx_before = r.get("context_before", [])
            ctx_after = r.get("context_after", [])
            if ctx_before:
                for j, cl in enumerate(ctx_before):
                    ln = r["line"] - len(ctx_before) + j
                    lines_out.append(f"  {r['file']}:{ln}: {cl}")
            lines_out.append(f"  {r['file']}:{r['line']}: {r['text']}")
            if ctx_after:
                for j, cl in enumerate(ctx_after):
                    ln = r["line"] + 1 + j
                    lines_out.append(f"  {r['file']}:{ln}: {cl}")
            lines_out.append("")

    if fts_rows:
        # Group FTS results by filename, preserving the order of first appearance
        # (input is already sorted best-match first, so the first row per file is its best).
        groups: dict[str, list[dict]] = {}
        for r in fts_rows:
            groups.setdefault(r["filename"], []).append(r)

        lines_out.append(f"Found {total} matches across {len(groups)} files:")
        lines_out.append("")
        for filename, rows in groups.items():
            best = rows[0]
            score = best.get("relevance_score", 0)
            best_page = best.get("page_number", "?")
            section = best.get("section_title", "")
            highlight = best.get("highlight", "")
            updated = best.get("updated_at", "")
            loc = f"page {best_page}"
            if section:
                loc = f"{section} ({loc})"
            match_count = len(rows)
            score_part = f"score: {score}"
            if updated:
                score_part += f", updated: {updated}"
            header = (
                f"  {filename} ({match_count} match{'es' if match_count != 1 else ''}, "
                f"best: {loc}) [{score_part}]"
            )
            lines_out.append(header)
            lines_out.append(f"    {highlight}")
            if match_count > 1:
                other_pages = [str(r.get("page_number", "?")) for r in rows[1:]]
                # Cap list to keep the line compact
                shown = ", ".join(other_pages[:10])
                suffix = f" (+{len(other_pages) - 10} more)" if len(other_pages) > 10 else ""
                lines_out.append(f"    also on pages: {shown}{suffix}")
            lines_out.append("")

    if data.get("truncated"):
        lines_out.append(f"... (truncated at {len(results)} results, {total} total)")

    return "\n".join(lines_out)


async def get_info() -> str:
    """Call GET /info and format as readable text."""
    client = await get_client()
    response = await client.get("/info")

    if response.status_code != 200:
        return _handle_error(response)

    data = response.json()
    by_status = data.get("by_status", {})
    by_type = data.get("by_type", [])
    recent = data.get("recent", [])

    total = sum(by_status.values())
    status_parts = [f"{s}: {c}" for s, c in sorted(by_status.items())]
    lines_out = [f"Workspace: {total} files ({', '.join(status_parts)})"]

    if by_type:
        lines_out.append("")
        lines_out.append("By type:")
        for mime, count in by_type:
            lines_out.append(f"  {mime:<40} {count} files")

    if recent:
        lines_out.append("")
        lines_out.append("Recently updated:")
        for r in recent:
            lines_out.append(f"  {r['filename']:<40} {r.get('updated_at', '?')}")

    memory = data.get("memory")
    if memory and memory.get("total", 0) > 0:
        lines_out.append("")
        lines_out.append(f"Memories: {memory['total']} total")
        mem_types = memory.get("by_type", {})
        if mem_types:
            parts = [f"{t}: {c}" for t, c in sorted(mem_types.items())]
            lines_out.append(f"  {', '.join(parts)}")

    return "\n".join(lines_out)


# ------------------------------------------------------------------
# Agent Memory
# ------------------------------------------------------------------

async def memory_store(
    content: str,
    memory_type: str = "semantic",
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> str:
    """Call POST /memory to store a memory."""
    client = await get_client()
    body: dict = {"content": content, "memory_type": memory_type}
    if tags:
        body["tags"] = tags
    if metadata:
        body["metadata"] = metadata

    response = await client.post("/memory", json=body)
    if response.status_code != 200:
        return _handle_error(response)

    data = response.json()
    return (
        f"Memory stored (id: {data.get('memory_id', '?')}, "
        f"type: {data.get('memory_type', '?')})"
    )


async def memory_recall(
    query: str,
    memory_type: str | None = None,
    tags: list[str] | None = None,
    limit: int = 10,
) -> str:
    """Call POST /memory/recall to search memories."""
    client = await get_client()
    body: dict = {"query": query, "limit": limit}
    if memory_type:
        body["memory_type"] = memory_type
    if tags:
        body["tags"] = tags

    response = await client.post("/memory/recall", json=body)
    if response.status_code != 200:
        return _handle_error(response)

    data = response.json()
    results = data.get("results", [])
    total = data.get("total", 0)

    if not results:
        return f"No memories found for '{query}'"

    lines_out: list[str] = [f"Found {total} memories:"]
    lines_out.append("")
    for r in results:
        mtype = r.get("memory_type", "?")
        score = r.get("score", 0)
        created = r.get("created_at", "?")
        tags_str = ", ".join(r.get("tags", []))
        header = f"  [{mtype}] (score: {score}, created: {created})"
        if tags_str:
            header += f" tags: {tags_str}"
        lines_out.append(header)
        # Show highlight if available, otherwise truncate content
        highlight = r.get("highlight") or r.get("content", "")[:150]
        lines_out.append(f"    {highlight}")
        lines_out.append(f"    id: {r.get('memory_id', '?')}")
        lines_out.append("")

    return "\n".join(lines_out)


async def memory_forget(
    memory_id: str | None = None,
    query: str | None = None,
    memory_type: str | None = None,
) -> str:
    """Call POST /memory/forget to delete memories."""
    client = await get_client()
    body: dict = {}
    if memory_id:
        body["memory_id"] = memory_id
    if query:
        body["query"] = query
    if memory_type:
        body["memory_type"] = memory_type

    response = await client.post("/memory/forget", json=body)
    if response.status_code != 200:
        return _handle_error(response)

    data = response.json()
    deleted = data.get("deleted", 0)
    by = data.get("by", "?")
    return f"Deleted {deleted} memory/memories (by {by})"


async def glob_files(pattern: str, path: str | None = None) -> str:
    """Call GET /glob."""
    client = await get_client()
    params: dict[str, str] = {"pattern": pattern}
    if path:
        params["path"] = path

    response = await client.get("/glob", params=params)

    if response.status_code != 200:
        return _handle_error(response)

    data = response.json()
    files = data.get("files", [])
    count = data.get("count", 0)
    truncated = data.get("truncated", False)

    if not files:
        return f"No files found matching '{pattern}'"

    result = "\n".join(files)
    if truncated:
        result += f"\n\n... ({count} shown, more results truncated)"
    return result
