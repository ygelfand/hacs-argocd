"""Sensor platform: per-app sync/health status plus an aggregate summary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, HEALTH_STATES, SYNC_STATES
from .coordinator import ArgoCDConfigEntry, ArgoCDCoordinator
from .entity import (
    ArgoCDAppEntity,
    ArgoCDClusterEntity,
    async_register_dynamic_entities,
)
from .models import CLUSTER_STATES, ArgoApplication


@dataclass(frozen=True, kw_only=True)
class ArgoAppSensorDescription(SensorEntityDescription):
    """Describes an ArgoCD per-application sensor."""

    value_fn: Callable[[ArgoApplication], StateType | datetime]
    attributes_fn: Callable[[ArgoApplication], dict[str, Any]]


APP_SENSORS: tuple[ArgoAppSensorDescription, ...] = (
    ArgoAppSensorDescription(
        key="sync_status",
        translation_key="sync_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(map(str.lower, SYNC_STATES)),
        value_fn=lambda app: app.sync_status.lower(),
        attributes_fn=lambda app: {
            "revision": app.revision,
            "target_revision": app.target_revision,
            "repo_url": app.repo_url,
            "path": app.path,
        },
    ),
    ArgoAppSensorDescription(
        key="health_status",
        translation_key="health_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(map(str.lower, HEALTH_STATES)),
        value_fn=lambda app: app.health_status.lower(),
        attributes_fn=lambda app: {
            "message": app.health_message,
            "operation_phase": app.operation_phase,
            "last_sync": app.last_sync_at.isoformat() if app.last_sync_at else None,
            "dest_namespace": app.dest_namespace,
            "dest_server": app.dest_server,
            "project": app.project,
            "resource_count": app.resource_count,
        },
    ),
    ArgoAppSensorDescription(
        key="last_sync",
        translation_key="last_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda app: app.last_sync_at,
        attributes_fn=lambda app: {
            "operation_phase": app.operation_phase,
            "initiated_by": app.initiated_by,
            "automated": app.automated,
            "revision": app.revision,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArgoCDConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data

    def app_factory(app_key: str) -> list[SensorEntity]:
        return [
            ArgoCDAppSensor(coordinator, entry, app_key, desc) for desc in APP_SENSORS
        ]

    def cluster_factory(cluster_key: str) -> list[SensorEntity]:
        return [ArgoCDClusterSensor(coordinator, entry, cluster_key)]

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
    async_add_entities([ArgoCDSummarySensor(coordinator, entry)])


class ArgoCDAppSensor(ArgoCDAppEntity, SensorEntity):
    """A single status sensor for one application."""

    entity_description: ArgoAppSensorDescription

    def __init__(
        self,
        coordinator: ArgoCDCoordinator,
        entry: ArgoCDConfigEntry,
        app_key: str,
        description: ArgoAppSensorDescription,
    ) -> None:
        super().__init__(coordinator, entry, app_key)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}:{app_key}:{description.key}"

    @property
    def native_value(self) -> StateType | datetime:
        if (app := self.app) is None:
            return None
        return self.entity_description.value_fn(app)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if (app := self.app) is None:
            return None
        return self.entity_description.attributes_fn(app)


class ArgoCDSummarySensor(CoordinatorEntity[ArgoCDCoordinator], SensorEntity):
    """Aggregate sensor: total apps plus out-of-sync / unhealthy counts."""

    _attr_has_entity_name = True
    _attr_translation_key = "applications_summary"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "apps"

    def __init__(
        self, coordinator: ArgoCDCoordinator, entry: ArgoCDConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}:summary"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Argo CD",
            model="ArgoCD instance",
        )

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        apps = list(self.coordinator.data.values())
        breakdown: dict[str, int] = {}
        for app in apps:
            breakdown[app.health_status] = breakdown.get(app.health_status, 0) + 1
        return {
            "out_of_sync": sum(1 for a in apps if not a.is_synced),
            "unhealthy": sum(1 for a in apps if not a.is_healthy),
            "syncing": sum(1 for a in apps if a.operation_phase == "Running"),
            "health_breakdown": breakdown,
        }


class ArgoCDClusterSensor(ArgoCDClusterEntity, SensorEntity):
    """Connection status of a single ArgoCD destination cluster."""

    _attr_translation_key = "cluster_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(map(str.lower, CLUSTER_STATES))

    def __init__(
        self,
        coordinator: ArgoCDCoordinator,
        entry: ArgoCDConfigEntry,
        cluster_key: str,
    ) -> None:
        super().__init__(coordinator, entry, cluster_key)
        self._attr_unique_id = f"{entry.entry_id}:cluster:{cluster_key}:status"

    @property
    def native_value(self) -> str | None:
        if (cluster := self.cluster) is None:
            return None
        return cluster.connection_status.lower()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if (cluster := self.cluster) is None:
            return None
        return {
            "server": cluster.server,
            "server_version": cluster.server_version,
            "applications_count": cluster.applications_count,
            "message": cluster.message,
        }
