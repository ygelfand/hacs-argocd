"""ArgoCD REST API backend.

Talks to the ArgoCD API server under ``/api/v1``. Supports a static bearer token
or username/password (exchanged for a session JWT, with transparent re-login when
the JWT expires).
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from ..const import DEFAULT_TIMEOUT, WATCH_TIMEOUT_SECONDS
from ..models import ArgoApplication, ArgoCluster
from . import ArgoCDAuthError, ArgoCDClient, ArgoCDConnectionError

_LOGGER = logging.getLogger(__name__)


class ArgoCDRestClient(ArgoCDClient):
    """Client for the ArgoCD REST API."""

    supports_watch = True

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        *,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        verify_ssl: bool | ssl.SSLContext = True,
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._token = token
        self._username = username
        self._password = password
        self._ssl = verify_ssl
        self._login_lock = asyncio.Lock()

    async def async_login(self) -> str:
        """Exchange username/password for a session JWT and cache it."""
        if not (self._username and self._password):
            raise ArgoCDAuthError("No username/password configured for login")
        async with self._login_lock:
            data = await self._raw_request(
                "POST",
                "/api/v1/session",
                json={"username": self._username, "password": self._password},
                authed=False,
            )
            token = data.get("token")
            if not token:
                raise ArgoCDAuthError("Session response did not contain a token")
            self._token = token
            return token

    @property
    def token(self) -> str | None:
        return self._token

    async def list_applications(self) -> list[ArgoApplication]:
        # List everything; project/namespace filtering is applied uniformly in
        # the coordinator so both backends behave identically.
        data = await self._request("GET", "/api/v1/applications")
        items = data.get("items") or []
        return [ArgoApplication.from_api(item) for item in items]

    async def watch_applications(self) -> AsyncIterator[list[ArgoApplication]]:
        apps = {a.unique_id: a for a in await self.list_applications()}
        yield list(apps.values())

        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        url = f"{self._base}/api/v1/stream/applications"
        timeout = aiohttp.ClientTimeout(
            total=None, sock_read=WATCH_TIMEOUT_SECONDS + 30
        )
        try:
            async with self._session.request(
                "GET", url, headers=headers, ssl=self._ssl, timeout=timeout
            ) as resp:
                if resp.status in (401, 403):
                    raise ArgoCDAuthError(f"ArgoCD watch auth failed ({resp.status})")
                if resp.status >= 400:
                    raise ArgoCDConnectionError(f"ArgoCD watch returned {resp.status}")
                async for raw in resp.content:
                    if not (line := raw.strip()):
                        continue
                    try:
                        result = json.loads(line).get("result") or {}
                    except ValueError:
                        continue
                    obj = result.get("application")
                    if obj is None:
                        continue
                    app = ArgoApplication.from_api(obj)
                    if result.get("type") == "DELETED":
                        apps.pop(app.unique_id, None)
                    else:
                        apps[app.unique_id] = app
                    yield list(apps.values())
        except ArgoCDAuthError:
            raise
        except (TimeoutError, aiohttp.ClientError, ssl.SSLError) as err:
            raise ArgoCDConnectionError(f"ArgoCD watch stream error: {err}") from err

    async def list_clusters(self) -> list[ArgoCluster]:
        data = await self._request("GET", "/api/v1/clusters")
        items = data.get("items") or []
        return [ArgoCluster.from_api(item) for item in items]

    async def sync_application(
        self,
        name: str,
        namespace: str | None = None,
        *,
        prune: bool = False,
        revision: str | None = None,
    ) -> None:
        body: dict[str, Any] = {"name": name, "prune": prune}
        if namespace:
            body["appNamespace"] = namespace
        if revision:
            body["revision"] = revision
        await self._request("POST", f"/api/v1/applications/{name}/sync", json=body)

    async def refresh_application(
        self,
        name: str,
        namespace: str | None = None,
        *,
        hard: bool = False,
    ) -> None:
        params = {"refresh": "hard" if hard else "normal"}
        if namespace:
            params["appNamespace"] = namespace
        await self._request("GET", f"/api/v1/applications/{name}", params=params)

    async def _request(
        self, method: str, path: str, *, retry_auth: bool = True, **kwargs: Any
    ) -> dict[str, Any]:
        """Authenticated request with one transparent re-login on 401."""
        try:
            return await self._raw_request(method, path, authed=True, **kwargs)
        except ArgoCDAuthError:
            if retry_auth and self._username and self._password:
                _LOGGER.debug("Got 401, re-logging in to ArgoCD")
                await self.async_login()
                return await self._raw_request(method, path, authed=True, **kwargs)
            raise

    async def _raw_request(
        self, method: str, path: str, *, authed: bool, **kwargs: Any
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if authed and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        url = f"{self._base}{path}"
        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
                **kwargs,
            ) as resp:
                if resp.status in (401, 403):
                    raise ArgoCDAuthError(
                        f"ArgoCD auth failed ({resp.status}) for {path}"
                    )
                if resp.status >= 400:
                    text = await resp.text()
                    raise ArgoCDConnectionError(
                        f"ArgoCD returned {resp.status} for {path}: {text[:200]}"
                    )
                if resp.content_type == "application/json":
                    return await resp.json()
                return {}
        except ArgoCDAuthError:
            raise
        except aiohttp.ClientConnectorCertificateError as err:
            raise ArgoCDConnectionError(f"TLS certificate error: {err}") from err
        except (TimeoutError, aiohttp.ClientError, ssl.SSLError) as err:
            raise ArgoCDConnectionError(
                f"Cannot connect to ArgoCD at {self._base}: {err}"
            ) from err
