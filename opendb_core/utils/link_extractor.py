"""Deterministic file-link extraction for OpenDB.

This is intentionally small: it captures links that are useful for local
workspace navigation without introducing an entity graph or parser runtime.
"""

from __future__ import annotations

import re
import posixpath
from pathlib import Path
from urllib.parse import unquote


_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_WIKILINK_RE = re.compile(r"\[\[([^|\]#]+)(?:#[^|\]]*)?(?:\|([^\]]+))?\]\]")
_CODE_PATH_RE = re.compile(
    r"\b((?:src|lib|app|test|tests|docs|scripts|examples|opendb|opendb_core|mcp_server)/"
    r"[\w\-./]+\.(?:py|js|ts|tsx|jsx|md|rst|txt|json|yaml|yml|toml|sql|csv))(?::\d+)?\b"
)
_EXTERNAL_TARGET_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#)", re.IGNORECASE)


def _strip_code(content: str) -> str:
    """Mask fenced and inline code so examples do not become real links."""
    content = re.sub(r"```.*?```", lambda m: " " * len(m.group(0)), content, flags=re.DOTALL)
    return re.sub(r"`[^`\n]+`", lambda m: " " * len(m.group(0)), content)


def _normalize_target(target: str, source_path: str | None) -> str | None:
    target = unquote(target.strip().strip("<>"))
    if not target or _EXTERNAL_TARGET_RE.match(target):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0].strip()
    if not target:
        return None
    target = target.replace("\\", "/")
    if source_path and not target.startswith("/") and "/" in source_path:
        base = posixpath.dirname(source_path.replace("\\", "/"))
        target = posixpath.join(base, target)
    target = posixpath.normpath(target)
    target = target.replace("\\", "/")
    if target == ".":
        return None
    return target


def _context(content: str, start: int, end: int, radius: int = 80) -> str:
    left = max(0, start - radius)
    right = min(len(content), end + radius)
    return " ".join(content[left:right].split())


def extract_file_links(content: str, source_path: str | None = None) -> list[dict]:
    """Extract local file references from markdown-ish text.

    Returns dictionaries with ``target``, ``link_type``, and ``context``. Target
    resolution to file IDs is handled by the storage backend because it owns the
    indexed file table.
    """
    stripped = _strip_code(content)
    links: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(target: str | None, link_type: str, start: int, end: int) -> None:
        if not target:
            return
        key = (target, link_type)
        if key in seen:
            return
        seen.add(key)
        links.append({
            "target": target,
            "link_type": link_type,
            "context": _context(content, start, end),
        })

    for match in _MARKDOWN_LINK_RE.finditer(stripped):
        add(_normalize_target(match.group(2), source_path), "markdown", match.start(), match.end())

    for match in _WIKILINK_RE.finditer(stripped):
        raw = match.group(1).strip()
        if raw and not Path(raw).suffix:
            raw = f"{raw}.md"
        add(_normalize_target(raw, source_path), "wikilink", match.start(), match.end())

    for match in _CODE_PATH_RE.finditer(stripped):
        # Code paths are usually written repo-relative even inside docs.
        add(_normalize_target(match.group(1), None), "path", match.start(), match.end())

    return links
