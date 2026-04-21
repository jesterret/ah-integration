"""Vendored subset of python-appie — playwright removed (not needed at runtime)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .auth import (
    BASE_URL,
    DEFAULT_CLIENT_ID,
    DEFAULT_CLIENT_VERSION,
    DEFAULT_USER_AGENT,
)


class AHClient:
    graphql_url = f"{BASE_URL}/graphql"
    authorize_url = (
        "https://login.ah.nl/login"
        f"?client_id={DEFAULT_CLIENT_ID}&redirect_uri=appie://login-exit&response_type=code"
    )

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        auth_client: Any | None = None,
    ) -> None:
        default_headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "x-client-name": DEFAULT_CLIENT_ID,
            "x-client-version": DEFAULT_CLIENT_VERSION,
            "x-application": "AHWEBSHOP",
            "Content-Type": "application/json",
        }
        self._client = http_client or httpx.AsyncClient(base_url=BASE_URL, headers=default_headers)
        if http_client is not None:
            self._client.headers.update(default_headers)
        self._owns_client = http_client is None
        self.auth = auth_client

        from .receipts import ReceiptsAPI
        from .products import ProductsAPI

        self.receipts = ReceiptsAPI(self)
        self.products = ProductsAPI(self)

    async def __aenter__(self) -> AHClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self.auth is not None:
            await self.auth.aclose()
        if self._owns_client:
            await self._client.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        auth_required: bool = True,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        if url.startswith("/"):
            url = f"{BASE_URL}{url}"
        merged_headers = dict(headers or {})
        if auth_required and self.auth is not None:
            token = await self.auth.ensure_valid_token()
            merged_headers["Authorization"] = f"{token.token_type} {token.access_token}"
        response = await self._client.request(method, url, headers=merged_headers, **kwargs)
        response.raise_for_status()
        return response

    async def graphql(
        self,
        query: str,
        variables: Mapping[str, object] | None = None,
    ) -> dict:
        response = await self.request(
            "POST",
            self.graphql_url,
            json={"query": query, "variables": dict(variables or {})},
        )
        payload = response.json()
        if "errors" in payload:
            raise RuntimeError(f"GraphQL request failed: {payload['errors']}")
        return payload["data"]

    @staticmethod
    def _extract_code(value: str) -> str:
        code = AHClient._extract_code_from_text(value)
        if not code:
            raise ValueError("Input did not contain an authorization code.")
        return code

    @staticmethod
    def _extract_code_from_text(value: str) -> str | None:
        stripped = value.strip()
        if stripped and "://" not in stripped and "?" not in stripped and "=" not in stripped:
            return stripped
        parsed = urlparse(stripped)
        return parse_qs(parsed.query).get("code", [None])[0]
