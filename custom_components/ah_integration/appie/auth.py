"""Vendored subset of python-appie — playwright removed (not needed at runtime)."""

from __future__ import annotations

import httpx

from .models import TokenResponse

BASE_URL = "https://api.ah.nl"
DEFAULT_CLIENT_ID = "appie-ios"
DEFAULT_CLIENT_VERSION = "9.28"
DEFAULT_USER_AGENT = "Appie/9.28 (iPhone17,3; iPhone; CPU OS 26_1 like Mac OS X)"
REFRESH_SKEW_SECONDS = 60

_DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "x-client-name": DEFAULT_CLIENT_ID,
    "x-client-version": DEFAULT_CLIENT_VERSION,
    "x-application": "AHWEBSHOP",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


class AHAuthClient:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client

    async def aclose(self) -> None:
        pass

    async def login_with_code(self, code: str) -> TokenResponse:
        response = await self._client.post(
            f"{BASE_URL}/mobile-auth/v1/auth/token",
            headers=_DEFAULT_HEADERS,
            json={"clientId": DEFAULT_CLIENT_ID, "code": code},
        )
        _raise_for_status(response, "Failed to exchange authorization code")
        return TokenResponse.model_validate(response.json())

    async def refresh_token(self, refresh_token: str) -> TokenResponse:
        response = await self._client.post(
            f"{BASE_URL}/mobile-auth/v1/auth/token/refresh",
            headers=_DEFAULT_HEADERS,
            json={"clientId": DEFAULT_CLIENT_ID, "refreshToken": refresh_token},
        )
        _raise_for_status(response, "Failed to refresh token")
        return TokenResponse.model_validate(response.json())


def _raise_for_status(response: httpx.Response, context: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = response.text.strip()
        detail = f"{context}: {exc}"
        if body:
            detail = f"{detail}\nResponse body: {body}"
        raise RuntimeError(detail) from exc
