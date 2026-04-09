"""Unit tests for the API key authentication middleware."""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from opendb_core.middleware.auth import ApiKeyMiddleware


def _make_app(api_key: str = "") -> None:
    """Create a minimal Starlette app with auth middleware."""
    async def index(request) -> None:
        return JSONResponse({"ok": True})

    async def health(request) -> None:
        return JSONResponse({"status": "healthy"})

    async def data(request) -> None:
        return JSONResponse({"data": "secret"})

    app = Starlette(routes=[
        Route("/", index),
        Route("/health", health),
        Route("/data", data),
    ])
    app.add_middleware(ApiKeyMiddleware, api_key=api_key)
    return app


class TestAuthMiddlewareDisabled:
    """When no API key is configured, all requests pass through."""

    @pytest.mark.asyncio
    async def test_no_key_allows_all(self) -> None:
        app = _make_app(api_key="")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/data")
            assert r.status_code == 200
            assert r.json()["data"] == "secret"


class TestAuthMiddlewareEnabled:
    """When API key is configured, requests must include it."""

    @pytest.mark.asyncio
    async def test_missing_key_returns_401(self) -> None:
        app = _make_app(api_key="my-secret-key")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/data")
            assert r.status_code == 401
            assert "unauthorized" in r.json()["error"]

    @pytest.mark.asyncio
    async def test_wrong_key_returns_401(self) -> None:
        app = _make_app(api_key="my-secret-key")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/data", headers={"X-API-Key": "wrong-key"})
            assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_correct_key_passes(self) -> None:
        app = _make_app(api_key="my-secret-key")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/data", headers={"X-API-Key": "my-secret-key"})
            assert r.status_code == 200
            assert r.json()["data"] == "secret"

    @pytest.mark.asyncio
    async def test_health_endpoint_exempt(self) -> None:
        app = _make_app(api_key="my-secret-key")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/health")
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_root_endpoint_exempt(self) -> None:
        app = _make_app(api_key="my-secret-key")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/")
            assert r.status_code == 200
