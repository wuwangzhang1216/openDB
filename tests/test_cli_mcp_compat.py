"""Regression coverage for CLI and MCP compatibility shims."""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _reload_cli_module():
    sys.modules.pop("opendb.cli", None)
    return importlib.import_module("opendb.cli")


def test_cli_module_imports_with_current_typer() -> None:
    """The CLI module should import cleanly under the supported Typer versions."""
    cli = _reload_cli_module()
    assert cli.app.info.name == "opendb"


def test_serve_mcp_prefers_run_stdio_async(monkeypatch, tmp_path: Path) -> None:
    """Newer FastMCP builds should use the stdio helper directly."""
    cli = _reload_cli_module()
    calls: list[tuple[str, object]] = []

    class FakeWorkspace:
        root = tmp_path

        async def init(self) -> None:
            calls.append(("init", None))

        async def close(self) -> None:
            calls.append(("close", None))

    workspace_mod = ModuleType("opendb_core.workspace")
    workspace_mod.Workspace = SimpleNamespace(open=lambda _: FakeWorkspace())

    class FakeMCP:
        async def run_stdio_async(self) -> None:
            calls.append(("run_stdio_async", None))

        async def run_async(self, *, transport: str) -> None:
            calls.append(("run_async", transport))

    server_mod = ModuleType("mcp_server.server")
    server_mod.mcp = FakeMCP()

    monkeypatch.setitem(sys.modules, "opendb_core.workspace", workspace_mod)
    monkeypatch.setitem(sys.modules, "mcp_server.server", server_mod)
    monkeypatch.setattr(cli.typer, "echo", lambda *args, **kwargs: None)

    cli.serve_mcp(workspace=tmp_path)

    assert ("run_stdio_async", None) in calls
    assert not any(name == "run_async" for name, _ in calls)


def test_serve_mcp_falls_back_to_legacy_run_async(monkeypatch, tmp_path: Path) -> None:
    """Older FastMCP builds should still work through the legacy async entrypoint."""
    cli = _reload_cli_module()
    calls: list[tuple[str, object]] = []

    class FakeWorkspace:
        root = tmp_path

        async def init(self) -> None:
            calls.append(("init", None))

        async def close(self) -> None:
            calls.append(("close", None))

    workspace_mod = ModuleType("opendb_core.workspace")
    workspace_mod.Workspace = SimpleNamespace(open=lambda _: FakeWorkspace())

    class FakeMCP:
        async def run_async(self, *, transport: str) -> None:
            calls.append(("run_async", transport))

    server_mod = ModuleType("mcp_server.server")
    server_mod.mcp = FakeMCP()

    monkeypatch.setitem(sys.modules, "opendb_core.workspace", workspace_mod)
    monkeypatch.setitem(sys.modules, "mcp_server.server", server_mod)
    monkeypatch.setattr(cli.typer, "echo", lambda *args, **kwargs: None)

    cli.serve_mcp(workspace=tmp_path)

    assert ("run_async", "stdio") in calls


def test_serve_uses_opendb_core_main_app(monkeypatch, tmp_path: Path) -> None:
    """The embedded HTTP server should point uvicorn at the real ASGI app."""
    cli = _reload_cli_module()
    captured: dict[str, object] = {}

    fake_uvicorn = ModuleType("uvicorn")

    def fake_run(app: str, host: str, port: int, reload: bool) -> None:
        captured.update({"app": app, "host": host, "port": port, "reload": reload})

    fake_uvicorn.run = fake_run
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(cli.typer, "echo", lambda *args, **kwargs: None)

    cli.serve(workspace=tmp_path, host="127.0.0.1", port=8765)

    assert captured["app"] == "opendb_core.main:app"


def test_eval_export_rejects_non_positive_limit(monkeypatch, tmp_path: Path) -> None:
    """Manual limit validation should preserve the previous ge=1 behavior."""
    cli = _reload_cli_module()
    messages: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        cli.typer,
        "echo",
        lambda message, err=False: messages.append((message, err)),
    )

    with pytest.raises(cli.typer.Exit) as exc_info:
        cli.eval_export(workspace=tmp_path, limit=0)

    assert exc_info.value.exit_code == 1
    assert ("Error: --limit must be at least 1", True) in messages


@pytest.mark.asyncio
async def test_app_lifespan_accepts_app_and_closes_client(monkeypatch) -> None:
    """FastMCP lifespan hooks should accept the app argument on newer SDKs."""
    import mcp_server.server as server

    closed = False

    async def fake_close_client() -> None:
        nonlocal closed
        closed = True

    monkeypatch.setattr(server, "close_client", fake_close_client)

    async with server.app_lifespan(object()) as context:
        assert context == {}

    assert closed is True
