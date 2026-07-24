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
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import (
    HEALTH_DEGRADED,
    HEALTH_HEALTHY,
    HEALTH_MISSING,
    HEALTH_PROGRESSING,
    HEALTH_STATES,
    HEALTH_SUSPENDED,
    HEALTH_UNKNOWN,
    SYNC_STATES,
)
from .coordinator import ArgoCDConfigEntry, ArgoCDCoordinator
from .entity import (
    ArgoCDAppEntity,
    ArgoCDClusterEntity,
    ArgoCDInstanceEntity,
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


@dataclass(frozen=True, kw_only=True)
class ArgoInstanceCountDescription(SensorEntityDescription):
    """Describes an instance-wide count sensor derived from the app list."""

    count_fn: Callable[[list[ArgoApplication]], int]


# Operation phases that mean the last sync did not succeed.
_FAILED_PHASES = frozenset({"Failed", "Error"})


def _health_is(status: str) -> Callable[[list[ArgoApplication]], int]:
    return lambda apps: sum(1 for a in apps if a.health_status == status)


INSTANCE_COUNTS: tuple[ArgoInstanceCountDescription, ...] = (
    # Actionable aggregates + per-status problem counts (shown by default).
    ArgoInstanceCountDescription(
        key="out_of_sync",
        translation_key="out_of_sync_count",
        count_fn=lambda apps: sum(1 for a in apps if not a.is_synced),
    ),
    ArgoInstanceCountDescription(
        key="unhealthy",
        translation_key="unhealthy_count",
        count_fn=lambda apps: sum(1 for a in apps if not a.is_healthy),
    ),
    ArgoInstanceCountDescription(
        key="sync_failed",
        translation_key="sync_failed_count",
        count_fn=lambda apps: sum(
            1 for a in apps if a.operation_phase in _FAILED_PHASES
        ),
    ),
    ArgoInstanceCountDescription(
        key="health_degraded",
        translation_key="degraded_count",
        count_fn=_health_is(HEALTH_DEGRADED),
    ),
    ArgoInstanceCountDescription(
        key="health_missing",
        translation_key="missing_count",
        count_fn=_health_is(HEALTH_MISSING),
    ),
    ArgoInstanceCountDescription(
        key="health_unknown",
        translation_key="health_unknown_count",
        count_fn=_health_is(HEALTH_UNKNOWN),
    ),
    # Normal / transient states — available but off by default to avoid clutter.
    ArgoInstanceCountDescription(
        key="syncing",
        translation_key="syncing_count",
        count_fn=lambda apps: sum(1 for a in apps if a.operation_phase == "Running"),
        entity_registry_enabled_default=False,
    ),
    ArgoInstanceCountDescription(
        key="health_progressing",
        translation_key="progressing_count",
        count_fn=_health_is(HEALTH_PROGRESSING),
        entity_registry_enabled_default=False,
    ),
    ArgoInstanceCountDescription(
        key="health_suspended",
        translation_key="suspended_count",
        count_fn=_health_is(HEALTH_SUSPENDED),
        entity_registry_enabled_default=False,
    ),
    ArgoInstanceCountDescription(
        key="health_healthy",
        translation_key="healthy_count",
        count_fn=_health_is(HEALTH_HEALTHY),
        entity_registry_enabled_default=False,
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

    instance: list[SensorEntity] = [ArgoCDSummarySensor(coordinator, entry)]
    instance += [
        ArgoCDInstanceCountSensor(coordinator, entry, desc) for desc in INSTANCE_COUNTS
    ]
    if coordinator.version is not None:
        instance.append(ArgoCDVersionSensor(coordinator, entry))
    async_add_entities(instance)


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


class ArgoCDSummarySensor(ArgoCDInstanceEntity, SensorEntity):
    """Aggregate sensor: total apps, with a per-health-status breakdown."""

    _attr_translation_key = "applications_summary"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "apps"

    def __init__(
        self, coordinator: ArgoCDCoordinator, entry: ArgoCDConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}:summary"

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
            "health_breakdown": breakdown,
        }


class ArgoCDInstanceCountSensor(ArgoCDInstanceEntity, SensorEntity):
    """A single instance-wide count (e.g. out-of-sync or degraded apps)."""

    entity_description: ArgoInstanceCountDescription
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "apps"

    def __init__(
        self,
        coordinator: ArgoCDCoordinator,
        entry: ArgoCDConfigEntry,
        description: ArgoInstanceCountDescription,
    ) -> None:
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}:{description.key}"

    @property
    def native_value(self) -> int:
        return self.entity_description.count_fn(list(self.coordinator.data.values()))


class ArgoCDVersionSensor(ArgoCDInstanceEntity, SensorEntity):
    """The ArgoCD server version (REST backend only)."""

    _attr_translation_key = "server_version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: ArgoCDCoordinator, entry: ArgoCDConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}:version"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.version


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
