"""Shared helpers for storage backends — eliminates duplication between
SQLite and PostgreSQL implementations.
"""

from __future__ import annotations

import json


def build_highlight(text: str, query: str, context_chars: int = 80) -> str:
    """Build a highlight snippet from original text by finding query terms."""
    terms = [t.strip().lower() for t in query.split() if t.strip()]
    if not terms:
        return text[:150]
    text_lower = text.lower()
    best_pos = -1
    for term in terms:
        pos = text_lower.find(term)
        if pos >= 0 and (best_pos < 0 or pos < best_pos):
            best_pos = pos
    if best_pos < 0:
        return text[:150]
    start = max(0, best_pos - context_chars)
    end = min(len(text), best_pos + context_chars)
    snippet = text[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


# Common FTS5 stopwords
STOPWORDS = frozenset(
    "a an and are as at be but by do for from had has have he her his how i "
    "if in is it its just me my no not of on or our she so than that the them "
    "then there these they this to too up us was we were what when where which "
    "who why will with would you your".split()
)


def escape_fts5(query: str, *, use_or: bool = False) -> str:
    """FTS5 query escaping — wrap each term to avoid syntax errors.

    Args:
        query: Raw query string.
        use_or: If True, join terms with OR and add prefix matching
                (better for natural-language recall queries).
                If False, use implicit AND (default, better for precise
                keyword search).
    """
    terms = [t.strip().strip("?!.,;:'\"()[]{}") for t in query.split()]
    terms = [t.replace('"', '').replace("'", "") for t in terms]
    terms = [t for t in terms if t]
    if use_or:
        filtered = [t for t in terms if t.lower() not in STOPWORDS]
        terms = filtered or terms
        parts = []
        for t in terms:
            parts.append(f'"{t}"')
            if len(t) >= 4 and t.isalpha():
                stem = t
                for suffix in ("ing", "tion", "sion", "ness", "ment", "able", "ible",
                               "ous", "ive", "ful", "less", "ers", "ies", "ed", "es", "ly", "s"):
                    if stem.lower().endswith(suffix) and len(stem) - len(suffix) >= 3:
                        stem = stem[: -len(suffix)]
                        break
                if stem != t:
                    parts.append(f"{stem}*")
        return " OR ".join(parts)
    escaped = [f'"{t}"' for t in terms]
    return " ".join(escaped)


def pg_file_row(row) -> dict:
    """Convert a PostgreSQL file row to a dict."""
    return {
        "id": str(row["id"]),
        "filename": row["filename"],
        "mime_type": row["mime_type"],
        "file_size": row["file_size"],
        "total_pages": row["total_pages"],
        "total_lines": row["total_lines"],
        "tags": row["tags"],
        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def pg_memory_row(row) -> dict:
    """Convert a PostgreSQL memory row to a dict."""
    return {
        "memory_id": str(row["id"]),
        "content": row["content"],
        "memory_type": row["memory_type"],
        "pinned": bool(row.get("pinned", False)),
        "tags": row["tags"],
        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        "created_at": row["created_at"].isoformat() + "Z" if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() + "Z" if row["updated_at"] else None,
    }


def sqlite_file_row(row) -> dict:
    """Convert a SQLite file row to a dict."""
    return {
        "id": row["id"],
        "filename": row["filename"],
        "mime_type": row["mime_type"],
        "file_size": row["file_size"],
        "total_pages": row["total_pages"],
        "total_lines": row["total_lines"],
        "tags": json.loads(row["tags"]) if row["tags"] else [],
        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def sqlite_memory_row(row) -> dict:
    """Convert a SQLite memory row to a dict."""
    return {
        "memory_id": row["memory_id"],
        "content": row["content"],
        "memory_type": row["memory_type"],
        "pinned": bool(row["pinned"]),
        "tags": json.loads(row["tags"]) if row["tags"] else [],
        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def add_pg_filters(conditions: list[str], params: list, filters: dict) -> None:
    """Append PostgreSQL-specific filter conditions."""
    if filters.get("tags"):
        params.append(filters["tags"] if isinstance(filters["tags"], list) else [filters["tags"]])
        conditions.append(f"f.tags @> ${len(params)}::text[]")

    if filters.get("mime_type"):
        params.append(filters["mime_type"])
        conditions.append(f"f.mime_type = ${len(params)}")

    if filters.get("metadata"):
        params.append(json.dumps(filters["metadata"]))
        conditions.append(f"f.metadata @> ${len(params)}::jsonb")

    if filters.get("created_after"):
        params.append(filters["created_after"])
        conditions.append(f"f.created_at >= ${len(params)}::timestamptz")


def add_sqlite_filters(conditions: list[str], params: list, filters: dict) -> None:
    """Append SQLite-specific filter conditions."""
    if filters.get("tags"):
        tag = filters["tags"] if isinstance(filters["tags"], str) else filters["tags"][0]
        params.append(f"%{tag}%")
        conditions.append("f.tags LIKE ?")

    if filters.get("mime_type"):
        params.append(filters["mime_type"])
        conditions.append("f.mime_type = ?")
