"""Optional API key authentication middleware.

When ``FILEDB_AUTH_API_KEY`` is set, all requests must include a matching
``X-API-Key`` header.  If the env var is empty (default), the middleware
is a no-op and all requests pass through.
"""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Require X-API-Key header when an API key is configured."""

    def __init__(self, app: object, api_key: str = "") -> None:
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next: object) -> JSONResponse:
        # No key configured → allow all
        if not self._api_key:
            return await call_next(request)

        # Always allow health checks without auth
        if request.url.path in ("/", "/health"):
            return await call_next(request)

        provided = request.headers.get("X-API-Key", "")
        if not provided or not hmac.compare_digest(provided, self._api_key):
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "detail": "Invalid or missing X-API-Key header"},
            )

        return await call_next(request)
