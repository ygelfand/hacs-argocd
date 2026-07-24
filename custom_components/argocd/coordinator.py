"""Data update coordinator for the ArgoCD integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ArgoCDAuthError, ArgoCDClient, ArgoCDConnectionError
from .const import (
    CONF_APP_NAMESPACE,
    CONF_PROJECT,
    DOMAIN,
    WATCH_RECONNECT_DELAY,
)
from .models import ArgoApplication, ArgoCluster

_LOGGER = logging.getLogger(__name__)

type ArgoCDConfigEntry = ConfigEntry[ArgoCDCoordinator]


class ArgoCDCoordinator(DataUpdateCoordinator[dict[str, ArgoApplication]]):
    """Polls the active backend and exposes apps keyed by ``namespace/name``."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ArgoCDConfigEntry,
        client: ArgoCDClient,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self._projects = set(entry.data.get(CONF_PROJECT) or [])
        self._namespaces = set(entry.data.get(CONF_APP_NAMESPACE) or [])
        # Populated best-effort each cycle (REST backend only); read by the
        # cluster / version entities. Not part of coordinator ``data`` so a
        # hiccup here never fails the primary application refresh.
        self.clusters: dict[str, ArgoCluster] = {}
        self.version: str | None = None

    def _matches_filters(self, app: ArgoApplication) -> bool:
        if self._projects and app.project not in self._projects:
            return False
        return not (self._namespaces and app.namespace not in self._namespaces)

    def _filtered(self, apps: list[ArgoApplication]) -> dict[str, ArgoApplication]:
        return {a.unique_id: a for a in apps if self._matches_filters(a)}

    async def _async_update_data(self) -> dict[str, ArgoApplication]:
        try:
            apps = await self.client.list_applications()
        except ArgoCDAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ArgoCDConnectionError as err:
            raise UpdateFailed(str(err)) from err

        try:
            clusters = await self.client.list_clusters()
            self.clusters = {c.unique_id: c for c in clusters}
            self.version = await self.client.get_version()
        except Exception as err:  # noqa: BLE001 - cluster/version data is best-effort
            # Never let a cluster/version hiccup fail the application refresh.
            _LOGGER.debug("Could not fetch ArgoCD clusters/version: %s", err)

        return self._filtered(apps)

    @callback
    def async_start_watch(self) -> None:
        """Stream Application changes in the background, pushing updates live.

        The periodic poll (``update_interval``) stays on as a safety net and to
        refresh clusters, which are not part of the watch stream.
        """
        entry = self.config_entry
        task = entry.async_create_background_task(
            self.hass, self._run_watch(), name=f"{DOMAIN}-watch-{entry.entry_id}"
        )
        entry.async_on_unload(task.cancel)

    async def _run_watch(self) -> None:
        while True:
            try:
                async for apps in self.client.watch_applications():
                    self.async_set_updated_data(self._filtered(apps))
            except asyncio.CancelledError:
                raise
            except ArgoCDAuthError as err:
                # The poll will surface this as a re-auth; just back off here.
                _LOGGER.debug("ArgoCD watch auth error: %s", err)
            except Exception as err:  # noqa: BLE001 - watch must keep retrying
                _LOGGER.debug("ArgoCD watch error, will reconnect: %s", err)
            await asyncio.sleep(WATCH_RECONNECT_DELAY)
