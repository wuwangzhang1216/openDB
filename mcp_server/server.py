"""MuseDB MCP Server — 3 tools: read, search, glob."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from mcp_server.client import close_client
from mcp_server.models import GlobInput, ReadInput, SearchInput
from mcp_server import client as musedb

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
mcp = FastMCP("musedb_mcp", lifespan=app_lifespan)


@mcp.tool(
    name="musedb_read",
    annotations={
        "title": "Read File",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def musedb_read(params: ReadInput) -> str:
    """Read any file — code with line numbers, documents as plain text, spreadsheets as structured JSON.

    Supports page/line filtering, in-file grep, and OCR for scanned images.
    Use this for all file reading: source code, PDFs, Word docs, Excel sheets, images.

    Args:
        params (ReadInput): Validated input parameters containing:
            - filename (str): File path, filename, partial match, or UUID
            - offset (int, optional): Start line number (1-based)
            - limit (int, optional): Max lines to return
            - pages (str, optional): Page range '1-3' or sheet name 'Revenue'
            - grep (str, optional): Search within file, + for AND
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

    return await musedb.read_file(
        filename=params.filename,
        numbered=is_code,
        pages=params.pages,
        lines=lines_param,
        grep=params.grep,
        format=params.format,
    )


@mcp.tool(
    name="musedb_search",
    annotations={
        "title": "Search Files",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def musedb_search(params: SearchInput) -> str:
    """Search across code files (regex) and documents (full-text).

    Auto-detects mode: use with path/glob for code search (grep), or plain query
    for document full-text search (fts). Returns matches with file paths, line numbers,
    and context.

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
    return await musedb.search(
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
    name="musedb_glob",
    annotations={
        "title": "Find Files",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def musedb_glob(params: GlobInput) -> str:
    """Find files matching a glob pattern.

    Returns file paths sorted by modification time (newest first).
    Use to discover files before reading them.

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
    return await musedb.glob_files(
        pattern=params.pattern,
        path=params.path,
    )
