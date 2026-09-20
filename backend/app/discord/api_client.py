"""Thin HTTP client the bot uses to reach the FastAPI backend.

The bot process holds no database credentials and no Vertex credentials: it
only speaks to the backend, which owns all domain logic.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings


class BackendError(RuntimeError):
    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class BackendClient:
    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        self._base = (base_url or settings.backend_base_url).rstrip("/")
        self._prefix = settings.api_prefix
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        headers = {}
        if settings.internal_api_token:
            headers["X-Internal-Token"] = settings.internal_api_token.get_secret_value()
        self._client = httpx.AsyncClient(
            base_url=self._base, timeout=self._timeout, headers=headers
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def post(self, path: str, json: Any = None, params: dict | None = None) -> Any:
        if self._client is None:
            await self.start()
        assert self._client is not None
        response = await self._client.post(f"{self._prefix}{path}", json=json, params=params)
        if response.status_code >= 400:
            raise BackendError(_extract_message(response), response.status_code)
        return response.json()

    async def health(self) -> bool:
        if self._client is None:
            await self.start()
        assert self._client is not None
        try:
            response = await self._client.get("/health")
            return response.status_code == 200
        except httpx.HTTPError:
            return False


def _extract_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or f"backend returned {response.status_code}"
    if isinstance(body, dict):
        return str(body.get("message") or body.get("detail") or body)
    return str(body)
