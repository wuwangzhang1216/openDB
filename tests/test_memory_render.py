"""Tests for white-box memory rendering helpers."""

from __future__ import annotations

import pytest

from opendb_core.utils.memory_render import (
    build_memory_profile,
    format_evidence,
    format_memory_recall_response,
    memory_metadata_lines,
)


def test_format_evidence_from_dict() -> None:
    text = format_evidence({
        "file": "docs/auth.md",
        "lines": "42-58",
        "tool": "opendb_read",
    })

    assert text == "docs/auth.md | lines 42-58 | via opendb_read"


def test_memory_metadata_lines_include_drill_down_fields() -> None:
    lines = memory_metadata_lines({
        "date": "2026-05-14",
        "scene": "Auth policy review",
        "evidence": {
            "file": "docs/auth.md",
            "lines": "42-58",
            "tool": "opendb_read",
        },
        "source_message_ids": ["turn-1", "turn-2"],
        "project": "openDB",
    })

    assert "date: 2026-05-14" in lines
    assert "scene: Auth policy review" in lines
    assert "evidence: docs/auth.md | lines 42-58 | via opendb_read" in lines
    assert "source messages: turn-1, turn-2" in lines
    assert "metadata: project=openDB" in lines


def test_format_memory_recall_response_exposes_provenance() -> None:
    text = format_memory_recall_response(
        {
            "total": 1,
            "results": [
                {
                    "memory_id": "mem-1",
                    "content": "The auth spec requires key rotation every 90 days.",
                    "memory_type": "procedural",
                    "score": 0.92,
                    "source": "tool_extraction",
                    "confidence": 0.875,
                    "superseded_id": "old-mem",
                    "tags": ["auth", "security"],
                    "metadata": {
                        "evidence": {
                            "file": "docs/auth.md",
                            "lines": "42-58",
                            "tool": "opendb_read",
                        }
                    },
                    "updated_at": "2026-05-14T12:00:00Z",
                }
            ],
        },
        "key rotation",
    )

    assert "Found 1 memories" in text
    assert "[procedural] score: 0.92" in text
    assert "source: tool_extraction; confidence: 0.875" in text
    assert "supersedes: old-mem" in text
    assert "tags: auth, security" in text
    assert "evidence: docs/auth.md | lines 42-58 | via opendb_read" in text
    assert "id: mem-1" in text


def test_format_memory_recall_response_prefers_highlight_over_long_content() -> None:
    content = "Important summary. " + ("filler text " * 500) + "final detail."

    text = format_memory_recall_response(
        {
            "total": 1,
            "results": [
                {
                    "memory_id": "long-1",
                    "content": content,
                    "highlight": "Important summary.",
                    "memory_type": "semantic",
                    "source": "tool_extraction",
                    "confidence": 1.0,
                    "metadata": {},
                }
            ],
        },
        "important",
    )

    assert "Important summary." in text
    assert "final detail." not in text
    assert len(text) < 500


def test_build_memory_profile_groups_existing_memories() -> None:
    profile = build_memory_profile(
        [
            {
                "memory_id": "pinned-1",
                "content": "User prefers concise technical answers.",
                "memory_type": "semantic",
                "pinned": True,
                "source": "user_explicit",
                "confidence": 1.0,
                "tags": ["preference"],
                "metadata": {},
            },
            {
                "memory_id": "proc-1",
                "content": "Run focused tests after memory format changes.",
                "memory_type": "procedural",
                "source": "ai_inference",
                "confidence": 0.9,
                "metadata": {
                    "evidence": {
                        "file": "tests/test_memory_render.py",
                        "tool": "opendb_read",
                    }
                },
            },
        ],
        total=2,
        generated_at="2026-05-14T00:00:00Z",
    )

    assert "# OpenDB Memory Profile" in profile
    assert "Generated: 2026-05-14T00:00:00Z" in profile
    assert "## Pinned Context" in profile
    assert "- [semantic] User prefers concise technical answers." in profile
    assert "## Procedural Workflows And Rules" in profile
    assert "- [procedural] Run focused tests after memory format changes." in profile
    assert "evidence: tests/test_memory_render.py | via opendb_read" in profile


@pytest.mark.asyncio
async def test_cjk_multi_term_recall_survives_query_gate(tmp_path) -> None:
    from opendb_core.storage.sqlite import SQLiteBackend

    backend = SQLiteBackend(db_path=tmp_path / "memory.db")
    await backend.init()
    try:
        await backend.store_memory(
            memory_id="cjk-1",
            content="用户偏好使用中文界面和搜索支持",
            memory_type="semantic",
            tags=[],
            metadata={},
        )

        result = await backend.recall_memories(
            query="中文界面 搜索支持 用户偏好",
            memory_type=None,
            tags=None,
            limit=10,
            offset=0,
        )
    finally:
        await backend.close()

    assert result["total"] == 1
    assert result["results"][0]["memory_id"] == "cjk-1"


@pytest.mark.asyncio
async def test_mcp_memory_recall_forwards_pinned_only(monkeypatch) -> None:
    from mcp_server import client as mcp_client

    class FakeResponse:
        status_code = 200

        def json(self) -> dict:
            return {
                "total": 1,
                "results": [
                    {
                        "memory_id": "pin-1",
                        "content": "Critical project context.",
                        "memory_type": "semantic",
                        "pinned": True,
                        "source": "user_explicit",
                        "confidence": 1.0,
                        "metadata": {},
                    }
                ],
            }

    class FakeClient:
        def __init__(self) -> None:
            self.body: dict | None = None

        async def post(self, path: str, json: dict) -> FakeResponse:
            assert path == "/memory/recall"
            self.body = json
            return FakeResponse()

    fake = FakeClient()

    async def fake_get_client() -> FakeClient:
        return fake

    monkeypatch.setattr(mcp_client, "get_client", fake_get_client)

    text = await mcp_client.memory_recall("", pinned_only=True)

    assert fake.body is not None
    assert fake.body["pinned_only"] is True
    assert "pinned" in text
    assert "source: user_explicit" in text
