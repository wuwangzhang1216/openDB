from __future__ import annotations

import importlib
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class _FakeTyperModule(types.ModuleType):
    class Exit(Exception):
        def __init__(self, code: int = 0) -> None:
            super().__init__(code)
            self.code = code

    class Typer:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def command(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

        def add_typer(self, *args, **kwargs) -> None:
            return None

    @staticmethod
    def Argument(default=None, *args, **kwargs):
        return default

    @staticmethod
    def Option(default=None, *args, **kwargs):
        return default

    @staticmethod
    def echo(*args, **kwargs) -> None:
        return None


class _FakeHTTPXModule(types.ModuleType):
    class Response:
        pass

    class AsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            self.is_closed = False


class CliServeRegressionTest(unittest.TestCase):
    def test_serve_uses_opendb_core_main_entrypoint(self) -> None:
        # Stub the optional runtime deps so the test can assert the uvicorn
        # target without importing the full application stack.
        fake_typer = _FakeTyperModule("typer")
        fake_uvicorn_calls: list[tuple[object, str, int, bool]] = []
        fake_uvicorn = types.ModuleType("uvicorn")

        def fake_run(app: object, host: str, port: int, reload: bool) -> None:
            fake_uvicorn_calls.append((app, host, port, reload))

        fake_uvicorn.run = fake_run  # type: ignore[attr-defined]

        fake_settings = types.SimpleNamespace(backend=None, opendb_dir=None)
        fake_config = types.ModuleType("opendb_core.config")
        fake_config.settings = fake_settings

        workspace_root = Path(tempfile.mkdtemp(prefix="opendb-cli-"))
        try:
            with patch.dict(
                sys.modules,
                {
                    "typer": fake_typer,
                    "uvicorn": fake_uvicorn,
                    "opendb_core.config": fake_config,
                },
            ):
                sys.modules.pop("opendb.cli", None)
                cli_mod = importlib.import_module("opendb.cli")
                cli_mod.serve(workspace=workspace_root, host="127.0.0.1", port=8123)

            self.assertEqual(len(fake_uvicorn_calls), 1)
            self.assertEqual(fake_uvicorn_calls[0], ("opendb_core.main:app", "127.0.0.1", 8123, False))
            self.assertEqual(fake_settings.backend, "sqlite")
            self.assertEqual(fake_settings.opendb_dir, workspace_root.resolve() / ".opendb")
        finally:
            shutil.rmtree(workspace_root, ignore_errors=True)
            sys.modules.pop("opendb.cli", None)


class MCPPinnedRecallRegressionTest(unittest.IsolatedAsyncioTestCase):
    async def test_memory_recall_accepts_and_forwards_pinned_only(self) -> None:
        # Keep this test at the client boundary so it verifies request shaping
        # without depending on a live FastAPI server.
        fake_httpx = _FakeHTTPXModule("httpx")

        class FakeResponse:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"results": [], "total": 0}

        posted_bodies: list[dict] = []

        class FakeClient:
            is_closed = False

            async def post(self, url: str, json: dict) -> FakeResponse:
                posted_bodies.append({"url": url, "json": json})
                return FakeResponse()

        with patch.dict(sys.modules, {"httpx": fake_httpx}):
            sys.modules.pop("mcp_server.client", None)
            client_mod = importlib.import_module("mcp_server.client")
            client_mod._client = FakeClient()

            result = await client_mod.memory_recall("critical facts", pinned_only=True)

        self.assertEqual(result, "No memories found for 'critical facts'")
        self.assertEqual(
            posted_bodies,
            [{"url": "/memory/recall", "json": {"query": "critical facts", "limit": 10, "pinned_only": True}}],
        )
        sys.modules.pop("mcp_server.client", None)


class WorkspaceRemovalRegressionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.state_dir = Path(tempfile.mkdtemp(prefix="opendb-state-"))
        self.workspace_root = Path(tempfile.mkdtemp(prefix="opendb-workspaces-"))
        self.ws_a = self.workspace_root / "ws_a"
        self.ws_b = self.workspace_root / "ws_b"
        self.ws_a.mkdir()
        self.ws_b.mkdir()
        self._old_state_dir = os.environ.get("FILEDB_STATE_DIR")
        os.environ["FILEDB_STATE_DIR"] = str(self.state_dir)

    async def asyncTearDown(self) -> None:
        if self._old_state_dir is None:
            os.environ.pop("FILEDB_STATE_DIR", None)
        else:
            os.environ["FILEDB_STATE_DIR"] = self._old_state_dir
        shutil.rmtree(self.state_dir, ignore_errors=True)
        shutil.rmtree(self.workspace_root, ignore_errors=True)

    async def test_force_remove_active_workspace_reapplies_remaining_workspace_config(self) -> None:
        import opendb_core.storage as storage_mod
        from opendb_core.services import workspace_service

        applied_dirs: list[Path] = []
        opened_keys: list[str] = []
        closed_keys: list[str] = []

        async def fake_init_backend(backend_type: str = "postgres", **kwargs) -> None:
            self.assertEqual(backend_type, "sqlite")
            key = str(kwargs["db_path"])
            opened_keys.append(key)
            storage_mod._backends.setdefault(key, object())
            storage_mod._active_key = key

        async def fake_close_backend(key: str | None = None) -> None:
            lookup = key or storage_mod._active_key
            if lookup is None:
                return
            closed_keys.append(lookup)
            storage_mod._backends.pop(lookup, None)
            if storage_mod._active_key == lookup:
                storage_mod._active_key = next(iter(storage_mod._backends), None) if storage_mod._backends else None

        def fake_apply_workspace_config(opendb_dir: Path, cfg) -> None:
            applied_dirs.append(opendb_dir)

        try:
            storage_mod._backends.clear()
            storage_mod._active_key = None

            # Patch the storage layer so the test can focus on the registry and
            # active-workspace handoff logic instead of SQLite I/O.
            with patch.object(workspace_service, "apply_workspace_config", side_effect=fake_apply_workspace_config), \
                 patch.object(workspace_service, "_ensure_parsers_registered", return_value=None), \
                 patch.object(storage_mod, "init_backend", side_effect=fake_init_backend), \
                 patch.object(storage_mod, "close_backend", side_effect=fake_close_backend):
                entry_a = await workspace_service.add_workspace(str(self.ws_a), name="A")
                entry_b = await workspace_service.add_workspace(str(self.ws_b), name="B")

                await workspace_service.switch_workspace(entry_a["id"])
                await workspace_service.switch_workspace(entry_b["id"])
                await workspace_service.remove_workspace(entry_b["id"], force=True)

                current = await workspace_service.current_workspace()

            self.assertEqual(current["id"], entry_a["id"])
            self.assertEqual(len(closed_keys), 1)
            self.assertTrue(closed_keys[0].replace("/", "\\").endswith("ws_b\\.opendb\\metadata.db"))
            self.assertTrue(storage_mod._active_key.replace("/", "\\").endswith("ws_a\\.opendb\\metadata.db"))
            self.assertTrue(str(applied_dirs[-1]).replace("/", "\\").endswith("ws_a\\.opendb"))
            self.assertTrue(opened_keys[-1].replace("/", "\\").endswith("ws_a\\.opendb\\metadata.db"))
        finally:
            storage_mod._backends.clear()
            storage_mod._active_key = None


if __name__ == "__main__":
    unittest.main()
