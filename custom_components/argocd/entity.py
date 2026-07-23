"""Base entity and dynamic entity-lifecycle helpers for ArgoCD."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_AUTO_ADD_DISCOVERED,
    DEFAULT_AUTO_ADD_DISCOVERED,
    DOMAIN,
)
from .coordinator import ArgoCDConfigEntry, ArgoCDCoordinator
from .models import ArgoApplication, ArgoCluster


class ArgoCDAppEntity(CoordinatorEntity[ArgoCDCoordinator]):
    """Base entity bound to a single ArgoCD Application."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ArgoCDCoordinator,
        entry: ArgoCDConfigEntry,
        app_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._app_key = app_key

    @property
    def app(self) -> ArgoApplication | None:
        """The current snapshot of this application, or None if it's gone."""
        return self.coordinator.data.get(self._app_key)

    @property
    def available(self) -> bool:
        return super().available and self.app is not None

    @property
    def device_info(self) -> DeviceInfo:
        app = self.app
        name = app.name if app else self._app_key
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}:{self._app_key}")},
            name=name,
            manufacturer="Argo CD",
            model="Application",
            via_device=(DOMAIN, self._entry.entry_id),
        )


class ArgoCDClusterEntity(CoordinatorEntity[ArgoCDCoordinator]):
    """Base entity bound to a single ArgoCD destination cluster."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ArgoCDCoordinator,
        entry: ArgoCDConfigEntry,
        cluster_key: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._cluster_key = cluster_key

    @property
    def cluster(self) -> ArgoCluster | None:
        """The current snapshot of this cluster, or None if it's gone."""
        return self.coordinator.clusters.get(self._cluster_key)

    @property
    def available(self) -> bool:
        return super().available and self.cluster is not None

    @property
    def device_info(self) -> DeviceInfo:
        cluster = self.cluster
        name = cluster.name if cluster else self._cluster_key
        return DeviceInfo(
            identifiers={
                (DOMAIN, f"{self._entry.entry_id}:cluster:{self._cluster_key}")
            },
            name=f"Cluster: {name}",
            manufacturer="Argo CD",
            model="Cluster",
            via_device=(DOMAIN, self._entry.entry_id),
        )


def async_register_dynamic_entities(
    entry: ArgoCDConfigEntry,
    coordinator: ArgoCDCoordinator,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[[str], Iterable[Entity]],
    keys_getter: Callable[[], Iterable[str]],
) -> None:
    """Create entities for existing keys and, if enabled, for future ones.

    ``keys_getter`` returns the current set of keys (apps or clusters) and
    ``factory`` maps a key to the entities that represent it. Keys present at
    setup are always added; keys discovered on later refreshes are added only
    when the ``auto_add_discovered`` option is on (default). Entities whose
    backing object vanishes are left registered but reported unavailable.
    """
    known: set[str] = set()

    @callback
    def _process() -> None:
        new_entities: list[Entity] = []
        for key in keys_getter():
            if key in known:
                continue
            known.add(key)
            new_entities.extend(factory(key))
        if new_entities:
            async_add_entities(new_entities)

    _process()

    if entry.options.get(CONF_AUTO_ADD_DISCOVERED, DEFAULT_AUTO_ADD_DISCOVERED):
        entry.async_on_unload(coordinator.async_add_listener(_process))
