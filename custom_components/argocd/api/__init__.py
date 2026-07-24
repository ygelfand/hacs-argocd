"""Backend clients for the ArgoCD integration.

Both backends implement :class:`ArgoCDClient` and return normalized
:class:`~custom_components.argocd.models.ArgoApplication` objects, so the rest of
the integration is agnostic to how the data was fetched.
"""

from __future__ import annotations

import ssl
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from homeassistant.core import HomeAssistant

from ..models import ArgoApplication, ArgoCluster


class ArgoCDError(Exception):
    """Base error for all backend failures."""


class ArgoCDAuthError(ArgoCDError):
    """Authentication/authorization failed (bad token, expired session, 403)."""


class ArgoCDConnectionError(ArgoCDError):
    """The backend could not be reached or returned an unexpected response."""


class ArgoCDClient(ABC):
    """Common interface shared by the REST and Kubernetes backends."""

    # Backends that can stream Application changes set this True and implement
    # ``watch_applications``.
    supports_watch: bool = False

    @abstractmethod
    async def list_applications(self) -> list[ArgoApplication]:
        """Return all visible ArgoCD Applications."""

    def watch_applications(self) -> AsyncIterator[list[ArgoApplication]]:
        """Yield a fresh full application snapshot whenever something changes.

        One "session": it seeds from a list, streams until the connection
        closes or errors, then returns — the caller reconnects. Only backends
        with ``supports_watch = True`` implement this.
        """
        raise NotImplementedError

    async def list_clusters(self) -> list[ArgoCluster]:
        """Return registered destination clusters.

        Only the REST backend can report cluster connection health; other
        backends return an empty list (so no cluster entities are created).
        """
        return []

    async def get_version(self) -> str | None:
        """Return the ArgoCD server version, or None if not available."""
        return None

    @abstractmethod
    async def sync_application(
        self,
        name: str,
        namespace: str | None = None,
        *,
        prune: bool = False,
        revision: str | None = None,
    ) -> None:
        """Trigger a sync for a single application."""

    @abstractmethod
    async def refresh_application(
        self,
        name: str,
        namespace: str | None = None,
        *,
        hard: bool = False,
    ) -> None:
        """Ask ArgoCD to re-compare live vs. desired state."""

    async def async_close(self) -> None:  # noqa: B027 - optional override
        """Release any resources. No-op by default (shared HA session)."""


def ca_ssl_kwargs(ca: str | None) -> dict[str, str]:
    """Interpret a user-supplied CA value as inline PEM or a filesystem path.

    Returns kwargs suitable for :func:`async_build_ssl_context` (``cadata`` for
    pasted PEM, ``cafile`` for a path). Empty input yields no kwargs.
    """
    if not ca or not ca.strip():
        return {}
    if ca.lstrip().startswith("-----BEGIN"):
        return {"cadata": ca}
    return {"cafile": ca.strip()}


async def async_build_ssl_context(
    hass: HomeAssistant,
    *,
    verify: bool = True,
    cafile: str | None = None,
    cadata: str | None = None,
) -> bool | ssl.SSLContext:
    """Build an aiohttp ``ssl=`` value off the event loop.

    Returns ``False`` to disable verification, ``True`` for system defaults, or a
    custom :class:`ssl.SSLContext` when a CA bundle is supplied.
    """
    if not verify:
        return False
    if not cafile and not cadata:
        return True

    def _build() -> ssl.SSLContext:
        ctx = ssl.create_default_context(cafile=cafile, cadata=cadata)
        return ctx

    return await hass.async_add_executor_job(_build)
