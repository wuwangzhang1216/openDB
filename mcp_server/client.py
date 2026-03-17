"""HTTP client for MuseDB REST API."""

from __future__ import annotations

import json
import os

import httpx

MUSEDB_URL = os.environ.get("MUSEDB_URL", "http://localhost:8000")

_client: httpx.AsyncClient | None = None


async def get_client() -> httpx.AsyncClient:
    """Get or create the shared httpx client."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(base_url=MUSEDB_URL, timeout=60.0)
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
    return f"Error: MuseDB returned status {response.status_code}"


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

    lines_out = [f"Found {total} results:"]
    lines_out.append("")

    for r in results:
        if "file" in r and r["file"]:
            # Grep mode result
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
        else:
            # FTS mode result
            score = r.get("relevance_score", 0)
            page = r.get("page_number", "?")
            section = r.get("section_title", "")
            highlight = r.get("highlight", "")
            loc = f"page {page}"
            if section:
                loc = f"{section} ({loc})"
            lines_out.append(f"  {r['filename']} — {loc} [score: {score}]")
            lines_out.append(f"    {highlight}")
            lines_out.append("")

    if data.get("truncated"):
        lines_out.append(f"... (truncated at {len(results)} results, {total} total)")

    return "\n".join(lines_out)


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
