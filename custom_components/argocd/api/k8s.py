"""Kubernetes CRD backend.

Reads ``argoproj.io/v1alpha1`` Application objects directly from the Kubernetes
API server (no ArgoCD API server required). Triggers a sync by writing the
``.operation`` field and a refresh via the ``argocd.argoproj.io/refresh``
annotation -- exactly what the ArgoCD application-controller watches for.

Uses raw ``aiohttp`` (no Kubernetes client dependency): the API surface here is
just a list plus a merge-patch on a single CRD. For in-cluster deployments the
mounted ServiceAccount token is projected and rotates (~hourly by default), so
the token is re-read on a short TTL and force-refreshed on a 401.
"""

from __future__ import annotations

import json
import logging
import ssl
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import aiohttp

from ..const import DEFAULT_TIMEOUT, WATCH_TIMEOUT_SECONDS
from ..models import ArgoApplication
from . import ArgoCDAuthError, ArgoCDClient, ArgoCDConnectionError

_LOGGER = logging.getLogger(__name__)

_API_GROUP = "apis/argoproj.io/v1alpha1"
_MERGE_PATCH = "application/merge-patch+json"
# How long to trust a freshly-read in-cluster token before re-reading the file.
_TOKEN_TTL = 60.0


class _ResourceVersionExpired(Exception):
    """Watch resourceVersion too old (HTTP 410); re-list and restart."""


class ArgoCDK8sClient(ArgoCDClient):
    """Client that reads ArgoCD Applications via the Kubernetes API.

    Pass either a static ``token`` (manual mode) or a ``token_provider`` coroutine
    that returns a fresh token (in-cluster mode, to survive token rotation).
    """

    supports_watch = True

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_url: str,
        *,
        token: str | None = None,
        token_provider: Callable[[], Awaitable[str]] | None = None,
        verify_ssl: bool | ssl.SSLContext = True,
        namespace: str | None = None,
    ) -> None:
        self._session = session
        self._base = api_url.rstrip("/")
        self._token = token
        self._token_provider = token_provider
        self._token_read_at = 0.0
        self._ssl = verify_ssl
        self._namespace = namespace or None

    async def _current_token(self) -> str | None:
        """Return a valid bearer token, refreshing a rotated one when stale."""
        if self._token_provider is None:
            return self._token
        now = time.monotonic()
        if self._token is None or (now - self._token_read_at) >= _TOKEN_TTL:
            self._token = await self._token_provider()
            self._token_read_at = now
        return self._token

    def _invalidate_token(self) -> None:
        """Drop the cached token so the next request re-reads it."""
        if self._token_provider is not None:
            self._token = None
            self._token_read_at = 0.0

    def _collection_path(self) -> str:
        if self._namespace:
            return f"/{_API_GROUP}/namespaces/{self._namespace}/applications"
        return f"/{_API_GROUP}/applications"

    def _object_path(self, name: str, namespace: str | None) -> str:
        ns = namespace or self._namespace
        if not ns:
            raise ArgoCDConnectionError(
                "A namespace is required to modify an Application via the "
                "Kubernetes backend"
            )
        return f"/{_API_GROUP}/namespaces/{ns}/applications/{name}"

    async def list_applications(self) -> list[ArgoApplication]:
        data = await self._request("GET", self._collection_path())
        items = data.get("items") or []
        return [ArgoApplication.from_api(item) for item in items]

    async def watch_applications(self) -> AsyncIterator[list[ArgoApplication]]:
        data = await self._request("GET", self._collection_path())
        apps: dict[str, ArgoApplication] = {}
        for item in data.get("items") or []:
            app = ArgoApplication.from_api(item)
            apps[app.unique_id] = app
        resource_version = (data.get("metadata") or {}).get("resourceVersion") or "0"
        yield list(apps.values())

        try:
            async for event in self._watch_stream(resource_version):
                event_type = event.get("type")
                if event_type == "BOOKMARK":
                    continue
                app = ArgoApplication.from_api(event.get("object") or {})
                if not app.name:
                    continue
                if event_type == "DELETED":
                    apps.pop(app.unique_id, None)
                elif event_type in ("ADDED", "MODIFIED"):
                    apps[app.unique_id] = app
                else:
                    continue
                yield list(apps.values())
        except _ResourceVersionExpired:
            return  # caller reconnects, which re-lists for a fresh version

    async def _watch_stream(self, resource_version: str) -> AsyncIterator[dict]:
        params = {
            "watch": "true",
            "resourceVersion": resource_version,
            "timeoutSeconds": str(WATCH_TIMEOUT_SECONDS),
            "allowWatchBookmarks": "true",
        }
        headers = {}
        if token := await self._current_token():
            headers["Authorization"] = f"Bearer {token}"
        url = f"{self._base}{self._collection_path()}"
        timeout = aiohttp.ClientTimeout(
            total=None, sock_read=WATCH_TIMEOUT_SECONDS + 30
        )
        try:
            async with self._session.request(
                "GET",
                url,
                params=params,
                headers=headers,
                ssl=self._ssl,
                timeout=timeout,
            ) as resp:
                if resp.status == 410:
                    raise _ResourceVersionExpired
                if resp.status in (401, 403):
                    raise ArgoCDAuthError(
                        f"Kubernetes watch auth failed ({resp.status})"
                    )
                if resp.status >= 400:
                    raise ArgoCDConnectionError(
                        f"Kubernetes watch returned {resp.status}"
                    )
                async for raw in resp.content:
                    if not (line := raw.strip()):
                        continue
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
        except (_ResourceVersionExpired, ArgoCDAuthError):
            raise
        except (TimeoutError, aiohttp.ClientError, ssl.SSLError) as err:
            raise ArgoCDConnectionError(
                f"Kubernetes watch stream error: {err}"
            ) from err

    async def sync_application(
        self,
        name: str,
        namespace: str | None = None,
        *,
        prune: bool = False,
        revision: str | None = None,
    ) -> None:
        sync: dict[str, Any] = {"prune": prune, "syncStrategy": {"hook": {}}}
        if revision:
            sync["revision"] = revision
        body = {
            "operation": {
                "initiatedBy": {"username": "home-assistant"},
                "sync": sync,
            }
        }
        await self._request(
            "PATCH",
            self._object_path(name, namespace),
            data=body,
            content_type=_MERGE_PATCH,
        )

    async def refresh_application(
        self,
        name: str,
        namespace: str | None = None,
        *,
        hard: bool = False,
    ) -> None:
        body = {
            "metadata": {
                "annotations": {
                    "argocd.argoproj.io/refresh": "hard" if hard else "normal"
                }
            }
        }
        await self._request(
            "PATCH",
            self._object_path(name, namespace),
            data=body,
            content_type=_MERGE_PATCH,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """Make a request; on 401 with a rotating token, refresh once and retry."""
        try:
            return await self._attempt(
                method, path, data=data, content_type=content_type
            )
        except ArgoCDAuthError:
            if self._token_provider is not None:
                _LOGGER.debug(
                    "Kubernetes API 401; re-reading rotated ServiceAccount token"
                )
                self._invalidate_token()
                return await self._attempt(
                    method, path, data=data, content_type=content_type
                )
            raise

    async def _attempt(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, Any] | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        token = await self._current_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body: str | None = None
        if data is not None:
            # ArgoCD/K8s patches require an explicit patch content-type, so we
            # serialize the body ourselves rather than using aiohttp's ``json=``.
            headers["Content-Type"] = content_type or "application/json"
            body = json.dumps(data)
        url = f"{self._base}{path}"
        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                data=body,
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                if resp.status in (401, 403):
                    raise ArgoCDAuthError(
                        f"Kubernetes API auth failed ({resp.status}) for {path}"
                    )
                if resp.status >= 400:
                    text = await resp.text()
                    raise ArgoCDConnectionError(
                        f"Kubernetes API returned {resp.status} for {path}: "
                        f"{text[:200]}"
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
                f"Cannot connect to Kubernetes API at {self._base}: {err}"
            ) from err
