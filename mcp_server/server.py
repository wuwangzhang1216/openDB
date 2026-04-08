"""OpenDB MCP Server — 3 tools: read, search, glob."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from mcp_server.client import close_client
from mcp_server.models import (
    GlobInput, InfoInput, MemoryForgetInput, MemoryRecallInput,
    MemoryStoreInput, ReadInput, SearchInput,
)
from mcp_server import client as opendb

# ---------------------------------------------------------------------------
# Code file extensions — these get line-numbered output automatically
# ---------------------------------------------------------------------------
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".c", ".h",
    ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".scala", ".lua",
    ".r", ".m", ".mm", ".pl", ".pm", ".sh", ".bash", ".zsh", ".fish",
    ".ps1", ".bat", ".cmd",
    ".css", ".scss", ".sass", ".less",
    ".html", ".htm", ".xml", ".svg",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".md", ".txt", ".rst", ".tex", ".log",
    ".sql", ".graphql", ".gql",
    ".proto", ".thrift",
    ".dockerfile", ".makefile", ".cmake",
    ".env", ".gitignore", ".editorconfig",
}


def _is_code_file(filename: str) -> bool:
    """Determine if a filename refers to a code/text file by extension."""
    name = filename.lower()
    # Check extension
    for ext in _CODE_EXTENSIONS:
        if name.endswith(ext):
            return True
    # Files without extensions that are commonly code
    basename = os.path.basename(name)
    if basename in {"makefile", "dockerfile", "gemfile", "rakefile", "procfile"}:
        return True
    return False


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def app_lifespan():
    yield {}
    await close_client()


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP("opendb_mcp", lifespan=app_lifespan)


@mcp.tool(
    name="opendb_read",
    annotations={
        "title": "Read File",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def opendb_read(params: ReadInput) -> str:
    """Read any file — code with line numbers, documents as plain text, spreadsheets as structured JSON.

    Supports page/line filtering, in-file grep, and OCR for scanned images.
    Use this for all file reading: source code, PDFs, Word docs, Excel sheets, images.

    Workflow: Use opendb_glob first to discover files, then opendb_read to examine them.
    If filename is ambiguous, use the full path from opendb_glob results.

    Args:
        params (ReadInput): Validated input parameters containing:
            - filename (str): File path, filename, partial match, or UUID
            - offset (int, optional): Start line number (1-based)
            - limit (int, optional): Max lines to return
            - pages (str, optional): Page range '1-3' or sheet name 'Revenue'
            - grep (str, optional): Search within file, + for AND (e.g. "revenue+growth" finds lines with both words)
            - format (str, optional): 'json' for structured spreadsheet data

    Returns:
        str: File contents. Code files include line numbers. Documents return plain text.
             Spreadsheets with format='json' return structured JSON with columns and rows.

    Examples:
        - Read a Python file: filename="main.py"
        - Read lines 50-80 of code: filename="app.py", offset=50, limit=31
        - Read a PDF: filename="report.pdf"
        - Read PDF pages 1-3: filename="report.pdf", pages="1-3"
        - Search in a document: filename="report.pdf", grep="revenue+growth"
        - Read Excel as JSON: filename="budget.xlsx", format="json"
        - Read specific sheet: filename="data.xlsx", pages="Revenue"
    """
    # Determine if this is a code file for line-numbered output
    is_code = _is_code_file(params.filename)

    # Build lines parameter from offset/limit
    lines_param: str | None = None
    if params.offset or params.limit:
        start = params.offset or 1
        if params.limit:
            end = start + params.limit - 1
            lines_param = f"{start}-{end}"
        else:
            lines_param = f"{start}-"

    return await opendb.read_file(
        filename=params.filename,
        numbered=is_code,
        pages=params.pages,
        lines=lines_param,
        grep=params.grep,
        format=params.format,
    )


@mcp.tool(
    name="opendb_search",
    annotations={
        "title": "Search Files",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def opendb_search(params: SearchInput) -> str:
    """Search across code files (regex) and documents (full-text).

    Two modes:
    - grep: Regex search on code files. Use when you have path/glob or need exact pattern matching.
    - fts: Full-text keyword search on indexed documents (PDFs, Word, etc.). Use for natural language queries.
    Auto-detects mode: path/glob present → grep, otherwise → fts.

    When to use this vs opendb_read with grep: Use opendb_search to find WHICH files contain something.
    Use opendb_read with grep param to search WITHIN a specific file you already know about.

    Args:
        params (SearchInput): Validated input parameters containing:
            - query (str): Search query or regex pattern
            - mode (str): 'grep', 'fts', or 'auto' (default: auto)
            - path (str, optional): Directory to search in (grep mode)
            - glob (str, optional): File pattern filter e.g. '*.py'
            - case_insensitive (bool): Case insensitive search (default: false)
            - context (int): Context lines before/after matches (default: 0)
            - limit (int): Max results (default: 20)
            - offset (int): Pagination offset (default: 0)

    Returns:
        str: Formatted search results with file paths, line numbers, and snippets.

    Examples:
        - Search code: query="def main", path="/workspace", glob="*.py"
        - Search documents: query="quarterly revenue"
        - Case insensitive grep: query="TODO", path="/workspace", case_insensitive=True
        - With context: query="import", path="/src", glob="*.ts", context=2
    """
    return await opendb.search(
        query=params.query,
        mode=params.mode,
        path=params.path,
        glob=params.glob,
        case_insensitive=params.case_insensitive,
        context=params.context,
        limit=params.limit,
        offset=params.offset,
    )


@mcp.tool(
    name="opendb_glob",
    annotations={
        "title": "Find Files",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def opendb_glob(params: GlobInput) -> str:
    """Find files matching a glob pattern, sorted by modification time (newest first).

    Use this as the first step to discover files in a workspace before reading them with opendb_read.
    Typical workflow: opendb_glob to find files → opendb_read to examine contents → opendb_search to find specific content.

    Args:
        params (GlobInput): Validated input parameters containing:
            - pattern (str): Glob pattern e.g. '**/*.py', 'src/**/*.ts'
            - path (str, optional): Root directory to search in

    Returns:
        str: Newline-separated list of matching file paths (relative to search root).

    Examples:
        - Find all Python files: pattern="**/*.py", path="/workspace"
        - Find TypeScript in src: pattern="src/**/*.ts", path="/workspace"
        - Find config files: pattern="**/*.{json,yaml,toml}", path="/workspace"
    """
    return await opendb.glob_files(
        pattern=params.pattern,
        path=params.path,
    )


@mcp.tool(
    name="opendb_info",
    annotations={
        "title": "Workspace Info",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def opendb_info(params: InfoInput) -> str:
    """Get workspace overview: file counts by status and type, recently updated files.

    Use this as the first step when entering a new workspace to understand what's available
    before searching or reading files.

    Returns:
        str: Workspace statistics including total files, type distribution, and recent activity.

    Examples:
        - Get workspace overview: (no parameters needed)
    """
    return await opendb.get_info()


# ---------------------------------------------------------------------------
# Agent Memory Tools
# ---------------------------------------------------------------------------

@mcp.tool(
    name="opendb_memory_store",
    annotations={
        "title": "Store Memory",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def opendb_memory_store(params: MemoryStoreInput) -> str:
    """Store a memory for later recall. Memories persist across sessions.

    Use this to save facts, user preferences, task outcomes, or learned workflows
    so they can be recalled later with opendb_memory_recall.

    Memory types:
    - semantic: Facts, knowledge, user preferences (default)
    - episodic: Past events, interaction outcomes, task results
    - procedural: Learned workflows, rules, patterns

    Set pinned=true for critical facts that should always surface first in recall
    results (e.g. user identity, project context). Pinned memories get a 10x
    ranking boost and can be retrieved instantly with pinned_only=true in recall.

    Args:
        params (MemoryStoreInput): Validated input parameters containing:
            - content (str): Memory text to store
            - memory_type (str): 'semantic', 'episodic', or 'procedural'
            - pinned (bool): Pin this memory for priority retrieval (default: false)
            - tags (list[str]): Tags for categorization
            - metadata (dict): Additional key-value metadata

    Returns:
        str: Confirmation with memory ID and type.

    Examples:
        - Store a fact: content="User prefers dark mode", memory_type="semantic"
        - Pin a critical fact: content="User is a senior backend engineer", pinned=true
        - Store an event: content="Deployed v2.1 to production on 2025-03-15", memory_type="episodic"
    """
    return await opendb.memory_store(
        content=params.content,
        memory_type=params.memory_type,
        tags=params.tags,
        metadata=params.metadata,
        pinned=params.pinned,
    )


@mcp.tool(
    name="opendb_memory_recall",
    annotations={
        "title": "Recall Memories",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def opendb_memory_recall(params: MemoryRecallInput) -> str:
    """Search and recall stored memories using full-text search.

    Results are ranked by a combination of relevance and recency — recent memories
    score higher than older ones with the same keyword match.

    Args:
        params (MemoryRecallInput): Validated input parameters containing:
            - query (str): Search query for memory recall
            - memory_type (str, optional): Filter by type: episodic, semantic, procedural
            - tags (list[str], optional): Filter by tags
            - limit (int): Max results (default: 10)

    Returns:
        str: Formatted list of matching memories with scores, types, and timestamps.

    Examples:
        - Recall user preferences: query="user preferences"
        - Recall deployments: query="deploy production", memory_type="episodic"
        - Recall by tag: query="auth", tags=["security"]
    """
    return await opendb.memory_recall(
        query=params.query,
        memory_type=params.memory_type,
        tags=params.tags,
        limit=params.limit,
        pinned_only=params.pinned_only,
    )


@mcp.tool(
    name="opendb_memory_forget",
    annotations={
        "title": "Forget Memory",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def opendb_memory_forget(params: MemoryForgetInput) -> str:
    """Delete memories by ID or by search query.

    Use memory_id to delete a specific memory, or query to find and delete
    all matching memories. At least one of memory_id or query must be provided.

    Args:
        params (MemoryForgetInput): Validated input parameters containing:
            - memory_id (str, optional): Specific memory ID to delete
            - query (str, optional): Delete memories matching this search query
            - memory_type (str, optional): Filter by type when deleting by query

    Returns:
        str: Confirmation with count of deleted memories.

    Examples:
        - Delete by ID: memory_id="abc-123-def"
        - Delete by query: query="outdated preferences"
        - Delete by type+query: query="old deploy", memory_type="episodic"
    """
    return await opendb.memory_forget(
        memory_id=params.memory_id,
        query=params.query,
        memory_type=params.memory_type,
    )
