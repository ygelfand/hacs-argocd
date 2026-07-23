"""Binary sensor platform: out-of-sync and unhealthy problem flags."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ArgoCDConfigEntry, ArgoCDCoordinator
from .entity import (
    ArgoCDAppEntity,
    ArgoCDClusterEntity,
    async_register_dynamic_entities,
)
from .models import ArgoApplication


@dataclass(frozen=True, kw_only=True)
class ArgoAppBinarySensorDescription(BinarySensorEntityDescription):
    """Describes an ArgoCD per-application binary sensor."""

    is_on_fn: Callable[[ArgoApplication], bool]


APP_BINARY_SENSORS: tuple[ArgoAppBinarySensorDescription, ...] = (
    ArgoAppBinarySensorDescription(
        key="out_of_sync",
        translation_key="out_of_sync",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda app: not app.is_synced,
    ),
    ArgoAppBinarySensorDescription(
        key="unhealthy",
        translation_key="unhealthy",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda app: not app.is_healthy,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArgoCDConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data

    def app_factory(app_key: str) -> list[BinarySensorEntity]:
        return [
            ArgoCDAppBinarySensor(coordinator, entry, app_key, desc)
            for desc in APP_BINARY_SENSORS
        ]

    def cluster_factory(cluster_key: str) -> list[BinarySensorEntity]:
        return [ArgoCDClusterBinarySensor(coordinator, entry, cluster_key)]

    async_register_dynamic_entities(
        entry, coordinator, async_add_entities, app_factory, lambda: coordinator.data
    )
    async_register_dynamic_entities(
        entry,
        coordinator,
        async_add_entities,
        cluster_factory,
        lambda: coordinator.clusters,
    )


class ArgoCDAppBinarySensor(ArgoCDAppEntity, BinarySensorEntity):
    """A single problem flag for one application."""

    entity_description: ArgoAppBinarySensorDescription

    def __init__(
        self,
        coordinator: ArgoCDCoordinator,
        entry: ArgoCDConfigEntry,
        app_key: str,
        description: ArgoAppBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, app_key)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}:{app_key}:{description.key}"

    @property
    def is_on(self) -> bool | None:
        if (app := self.app) is None:
            return None
        return self.entity_description.is_on_fn(app)


class ArgoCDClusterBinarySensor(ArgoCDClusterEntity, BinarySensorEntity):
    """Problem flag: on when a destination cluster is not connected."""

    _attr_translation_key = "cluster_unreachable"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        coordinator: ArgoCDCoordinator,
        entry: ArgoCDConfigEntry,
        cluster_key: str,
    ) -> None:
        super().__init__(coordinator, entry, cluster_key)
        self._attr_unique_id = f"{entry.entry_id}:cluster:{cluster_key}:unreachable"

    @property
    def is_on(self) -> bool | None:
        if (cluster := self.cluster) is None:
            return None
        return not cluster.is_connected
