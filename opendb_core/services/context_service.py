"""Compact context builder for agent code/document exploration."""

from __future__ import annotations

from opendb_core.storage import get_backend
from opendb_core.utils.text import extract_lines


async def build_context(query: str, limit: int = 8, include_snippets: bool = True) -> dict:
    """Build a small context bundle from symbols plus full-text hits."""
    backend = get_backend()
    symbol_limit = max(1, min(limit, 20))
    symbols = await backend.search_code_symbols(query, limit=symbol_limit)

    snippets: list[dict] = []
    if include_snippets:
        seen: set[tuple[str, int, int]] = set()
        for symbol in symbols[: min(symbol_limit, 8)]:
            file_id = str(symbol["file_id"])
            start = max(1, int(symbol["start_line"]) - 1)
            end = int(symbol["end_line"]) + 1
            key = (file_id, start, end)
            if key in seen:
                continue
            seen.add(key)
            text_row = await backend.get_file_text(file_id)
            snippets.append({
                "file_id": file_id,
                "filename": symbol["filename"],
                "source_path": symbol.get("source_path"),
                "symbol": symbol["qualified_name"],
                "start_line": start,
                "end_line": end,
                "text": extract_lines(
                    text_row["full_text"],
                    text_row["line_index"],
                    start,
                    min(end, int(text_row["total_lines"])),
                ),
            })

    doc_hits = await backend.search_fts(query, {}, max(1, min(limit, 10)), 0)
    symbol_file_ids = {str(s["file_id"]) for s in symbols}
    related_documents = [
        hit for hit in doc_hits.get("results", [])
        if str(hit.get("file_id")) not in symbol_file_ids
    ][: max(0, limit - len(symbols[:limit]))]

    return {
        "query": query,
        "symbols": symbols[:limit],
        "snippets": snippets,
        "related_documents": related_documents,
        "stats": {
            "symbol_count": len(symbols),
            "snippet_count": len(snippets),
            "related_document_count": len(related_documents),
        },
    }
