"""Tests for the workspace registry, service, and REST router.

Uses ``FILEDB_STATE_DIR=<tmp>`` to keep the registry isolated per test so
they never touch the user's real ``~/.opendb/workspaces.json``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Point FILEDB_STATE_DIR at a throwaway directory for this test."""
    state = tmp_path / "_state"
    state.mkdir()
    monkeypatch.setenv("FILEDB_STATE_DIR", str(state))
    # Re-import so the module picks up the new env var on its next call.
    # (state_dir() reads os.environ lazily, so no reload is needed.)
    yield state


@pytest.fixture
def two_workspaces(tmp_path):
    """Create two workspace root directories with distinct content."""
    ws_a = tmp_path / "ws_a"
    ws_b = tmp_path / "ws_b"
    ws_a.mkdir()
    ws_b.mkdir()
    (ws_a / "alpha.txt").write_text("alpha content", encoding="utf-8")
    (ws_b / "beta.txt").write_text("beta content", encoding="utf-8")
    return ws_a, ws_b


# ---------------------------------------------------------------------------
# Registry (pure data) tests
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_empty_load(self, isolated_state):
        from opendb_core import workspaces as reg_mod

        reg = reg_mod.load()
        assert reg.workspaces == []
        assert reg.active_id is None

    def test_upsert_and_save_roundtrip(self, isolated_state, tmp_path):
        from opendb_core import workspaces as reg_mod

        root = tmp_path / "proj"
        root.mkdir()

        reg = reg_mod.load()
        entry = reg.upsert(root, name="proj")
        reg.active_id = entry.id
        reg_mod.save(reg)

        reg2 = reg_mod.load()
        assert len(reg2.workspaces) == 1
        assert reg2.workspaces[0].id == entry.id
        assert reg2.workspaces[0].name == "proj"
        assert reg2.active_id == entry.id

    def test_id_stable_across_saves(self, isolated_state, tmp_path):
        from opendb_core import workspaces as reg_mod

        root = tmp_path / "stable"
        root.mkdir()
        id1 = reg_mod.workspace_id(root)
        id2 = reg_mod.workspace_id(root)
        assert id1 == id2
        assert len(id1) == 8

    def test_upsert_idempotent(self, isolated_state, tmp_path):
        from opendb_core import workspaces as reg_mod

        root = tmp_path / "same"
        root.mkdir()
        reg = reg_mod.load()
        e1 = reg.upsert(root)
        e2 = reg.upsert(root)
        assert e1.id == e2.id
        assert len(reg.workspaces) == 1

    def test_get_by_id_or_root(self, isolated_state, tmp_path):
        from opendb_core import workspaces as reg_mod

        root = tmp_path / "lookup"
        root.mkdir()
        reg = reg_mod.load()
        entry = reg.upsert(root)

        assert reg.get(entry.id) is entry
        assert reg.get(str(root)) is entry

    def test_remove(self, isolated_state, tmp_path):
        from opendb_core import workspaces as reg_mod

        root = tmp_path / "gone"
        root.mkdir()
        reg = reg_mod.load()
        entry = reg.upsert(root)
        reg.active_id = entry.id

        removed = reg.remove(entry.id)
        assert removed is not None
        assert removed.id == entry.id
        assert reg.workspaces == []
        assert reg.active_id is None


# ---------------------------------------------------------------------------
# Service-level tests (in-process, no HTTP)
# ---------------------------------------------------------------------------


class TestWorkspaceService:
    @pytest.mark.asyncio
    async def test_add_and_switch(self, isolated_state, two_workspaces):
        from opendb_core.services import workspace_service

        ws_a, ws_b = two_workspaces

        entry_a = await workspace_service.add_workspace(str(ws_a), name="A")
        assert entry_a["name"] == "A"
        assert entry_a["active"] is True  # first one auto-activates

        entry_b = await workspace_service.add_workspace(str(ws_b), name="B")
        assert entry_b["active"] is False

        # Switch to B
        switched = await workspace_service.switch_workspace(entry_b["id"])
        assert switched["id"] == entry_b["id"]
        assert switched["active"] is True

        current = await workspace_service.current_workspace()
        assert current["id"] == entry_b["id"]

    @pytest.mark.asyncio
    async def test_switch_by_root_path(self, isolated_state, two_workspaces):
        from opendb_core.services import workspace_service

        ws_a, ws_b = two_workspaces
        await workspace_service.add_workspace(str(ws_a))
        await workspace_service.add_workspace(str(ws_b))

        result = await workspace_service.switch_workspace(str(ws_b))
        assert Path(result["root"]) == ws_b.resolve()

    @pytest.mark.asyncio
    async def test_missing_root_rejected(self, isolated_state, tmp_path):
        from opendb_core.services import workspace_service

        ghost = tmp_path / "does_not_exist"
        with pytest.raises(workspace_service.WorkspaceRootMissing):
            await workspace_service.add_workspace(str(ghost))

    @pytest.mark.asyncio
    async def test_unknown_id_rejected(self, isolated_state):
        from opendb_core.services import workspace_service

        with pytest.raises(workspace_service.WorkspaceNotFound):
            await workspace_service.switch_workspace("deadbeef")

    @pytest.mark.asyncio
    async def test_remove_active_without_force_fails(self, isolated_state, two_workspaces):
        from opendb_core.services import workspace_service

        ws_a, _ = two_workspaces
        entry = await workspace_service.add_workspace(str(ws_a))
        await workspace_service.switch_workspace(entry["id"])

        with pytest.raises(ValueError):
            await workspace_service.remove_workspace(entry["id"])

    @pytest.mark.asyncio
    async def test_workspace_dir_created(self, isolated_state, tmp_path):
        from opendb_core.services import workspace_service

        root = tmp_path / "fresh"
        root.mkdir()
        await workspace_service.add_workspace(str(root))
        assert (root / ".opendb").exists()
        assert (root / ".opendb" / "config.json").exists()

    @pytest.mark.asyncio
    async def test_smooth_switch_preserves_per_workspace_data(
        self, isolated_state, two_workspaces
    ):
        """Round-trip switching between two workspaces keeps per-workspace state."""
        from opendb_core.services import workspace_service
        from opendb_core.workspace import Workspace

        ws_a_path, ws_b_path = two_workspaces

        # Register both through the service so they share the same registry view.
        entry_a = await workspace_service.add_workspace(str(ws_a_path))
        entry_b = await workspace_service.add_workspace(str(ws_b_path))

        # Index A then switch to B and index B.
        await workspace_service.switch_workspace(entry_a["id"])
        ws_a = Workspace.open(ws_a_path)
        await ws_a.init()
        await ws_a.index(ws_a_path)

        await workspace_service.switch_workspace(entry_b["id"])
        ws_b = Workspace.open(ws_b_path)
        await ws_b.init()
        await ws_b.index(ws_b_path)

        # Switch back and forth a few times; verify correct files are visible each time.
        for _ in range(3):
            await workspace_service.switch_workspace(entry_a["id"])
            result_a = await ws_a.search("alpha")
            files_a = {r["filename"] for r in result_a.get("results", [])}
            assert "alpha.txt" in files_a

            await workspace_service.switch_workspace(entry_b["id"])
            result_b = await ws_b.search("beta")
            files_b = {r["filename"] for r in result_b.get("results", [])}
            assert "beta.txt" in files_b

        await ws_a.close()
        await ws_b.close()
