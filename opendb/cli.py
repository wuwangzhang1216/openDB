"""OpenDB command-line interface.

Usage::

    opendb init [PATH]          # create .opendb/ in PATH (default: current dir)
    opendb index [PATH]         # index PATH (default: current dir)
    opendb search QUERY         # search indexed files
    opendb read FILENAME        # read a file
    opendb serve-mcp            # start MCP server (stdio, embedded mode)
    opendb serve                # start HTTP+MCP server (embedded mode)

Environment variables (FILEDB_ prefix) still apply. Setting
``FILEDB_BACKEND=sqlite`` activates embedded mode when using
``opendb serve``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

try:
    import typer
except ImportError:
    print(
        "typer is required for the CLI. Install it with: pip install opendb[cli]",
        file=sys.stderr,
    )
    sys.exit(1)

app = typer.Typer(
    name="opendb",
    help="OpenDB — AI-native file database",
    add_completion=False,
)


def _run(coro: object) -> object:
    """Run an async coroutine from a synchronous CLI context."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@app.command()
def init(
    path: Path = typer.Argument(Path("."), help="Workspace root directory"),
    ocr: bool = typer.Option(True, help="Enable OCR for image/scanned PDFs"),
) -> None:
    """Initialise a OpenDB workspace (creates .opendb/ directory)."""
    from opendb_core.workspace import Workspace, WorkspaceConfig

    config = WorkspaceConfig(ocr_enabled=ocr)
    ws = Workspace(root=path.resolve(), config=config)

    async def _init() -> None:
        await ws.init()
        await ws.close()

    _run(_init())
    typer.echo(f"Workspace initialised at {ws.opendb_dir}")


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

@app.command()
def index(
    path: Path = typer.Argument(Path("."), help="Directory to index"),
    workspace: Path = typer.Option(None, "--workspace", "-w", help="Workspace root (default: PATH)"),
    tags: str = typer.Option("", help="Comma-separated tags to apply"),
) -> None:
    """Index all supported files in a directory."""
    from opendb_core.workspace import Workspace

    ws_root = workspace or path
    ws = Workspace.open(ws_root)

    async def _index() -> dict:
        await ws.init()
        typer.echo(f"Indexing {path.resolve()} ...")
        result = await ws.index(path)
        await ws.close()
        return result

    result = _run(_index())
    typer.echo(
        f"Done: {result['ingested']} ingested, {result['skipped']} skipped, "
        f"{result['failed']} failed, {result['unsupported']} unsupported"
    )


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    workspace: Path = typer.Option(Path("."), "--workspace", "-w", help="Workspace root"),
    limit: int = typer.Option(10, help="Maximum results"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Search indexed files."""
    from opendb_core.workspace import Workspace

    ws = Workspace.open(workspace)

    async def _search() -> dict:
        await ws.init()
        result = await ws.search(query, limit=limit)
        await ws.close()
        return result

    result = _run(_search())

    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    total = result.get("total", 0)
    typer.echo(f"Found {total} result(s) for '{query}':\n")
    for r in result.get("results", []):
        typer.echo(
            f"  [{r['filename']}] page {r['page_number']} "
            f"(score {r['relevance_score']})"
        )
        if r.get("highlight"):
            typer.echo(f"    {r['highlight'][:120]}")
        typer.echo("")


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

@app.command()
def read(
    filename: str = typer.Argument(..., help="File name or UUID"),
    workspace: Path = typer.Option(Path("."), "--workspace", "-w", help="Workspace root"),
    pages: str = typer.Option(None, help="Page/slide/sheet range (e.g. '1-3', 'Revenue')"),
    lines: str = typer.Option(None, help="Line range (e.g. '50-80')"),
    grep: str = typer.Option(None, help="In-file search pattern"),
    numbered: bool = typer.Option(False, "--numbered", "-n", help="Show line numbers"),
    format: str = typer.Option(None, help="Output format: 'json' for spreadsheets"),
) -> None:
    """Read a file from the workspace."""
    from opendb_core.workspace import Workspace

    ws = Workspace.open(workspace)

    async def _read() -> str:
        await ws.init()
        text = await ws.read(
            filename, numbered=numbered, pages=pages,
            lines=lines, grep=grep, format=format,
        )
        await ws.close()
        return text

    text = _run(_read())
    typer.echo(text)


# ---------------------------------------------------------------------------
# serve-mcp (stdio, embedded)
# ---------------------------------------------------------------------------

@app.command(name="serve-mcp")
def serve_mcp(
    workspace: Path = typer.Option(Path("."), "--workspace", "-w", help="Workspace root"),
) -> None:
    """Start an MCP server in stdio transport (embedded mode, no PostgreSQL needed)."""
    from opendb_core.workspace import Workspace

    ws = Workspace.open(workspace)

    async def _start() -> None:
        await ws.init()
        typer.echo(
            f"OpenDB MCP server starting (embedded, workspace: {ws.root})",
            err=True,
        )
        # Import and run the MCP server using in-process services
        from mcp_server.server import mcp
        await mcp.run_async(transport="stdio")
        await ws.close()

    _run(_start())


# ---------------------------------------------------------------------------
# serve (HTTP + embedded)
# ---------------------------------------------------------------------------

@app.command()
def serve(
    workspace: Path = typer.Option(Path("."), "--workspace", "-w", help="Workspace root"),
    host: str = typer.Option("127.0.0.1", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
) -> None:
    """Start the HTTP server in embedded (SQLite) mode."""
    import uvicorn
    from opendb_core.config import settings

    # Configure for embedded mode
    settings.backend = "sqlite"
    settings.opendb_dir = workspace.resolve() / ".opendb"

    typer.echo(f"Starting OpenDB HTTP server (embedded) at http://{host}:{port}")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()
