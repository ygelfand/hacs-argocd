"""Home Assistant services: argocd.sync and argocd.refresh."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry

from .api import ArgoCDError
from .const import (
    ATTR_APPLICATION,
    ATTR_HARD,
    ATTR_PRUNE,
    ATTR_REVISION,
    CONF_NAMESPACE,
    DOMAIN,
    SERVICE_REFRESH,
    SERVICE_SYNC,
)
from .coordinator import ArgoCDCoordinator
from .models import ArgoApplication

# Shared "which application(s)?" selector fields: an app name (optionally scoped
# by namespace) and/or a HA target (entity / device / area).
_TARGET_FIELDS = {
    vol.Optional(ATTR_APPLICATION): cv.string,
    vol.Optional(CONF_NAMESPACE): cv.string,
    vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
    vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(ATTR_AREA_ID): vol.All(cv.ensure_list, [cv.string]),
}

SYNC_SCHEMA = vol.Schema(
    {
        **_TARGET_FIELDS,
        vol.Optional(ATTR_PRUNE, default=False): cv.boolean,
        vol.Optional(ATTR_REVISION): cv.string,
    }
)

REFRESH_SCHEMA = vol.Schema(
    {
        **_TARGET_FIELDS,
        vol.Optional(ATTR_HARD, default=False): cv.boolean,
    }
)

Match = tuple[ArgoCDCoordinator, ArgoApplication]


def _coordinator_for(hass: HomeAssistant, entry_id: str) -> ArgoCDCoordinator | None:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == entry_id and entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    return None


def _app_from_device(hass: HomeAssistant, device: DeviceEntry) -> Match | None:
    """Map an ArgoCD app *device* back to its coordinator + application."""
    for domain, ident in device.identifiers:
        if domain != DOMAIN:
            continue
        entry_id, _, app_key = ident.partition(":")
        # Skip the instance device (no ':') and cluster devices ('cluster:...').
        if not app_key or app_key.startswith("cluster:"):
            continue
        coordinator = _coordinator_for(hass, entry_id)
        if coordinator and (app := coordinator.data.get(app_key)):
            return coordinator, app
    return None


def _resolve_targets(hass: HomeAssistant, call: ServiceCall) -> list[Match]:
    """Resolve entity / device / area targets to (coordinator, app) pairs."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    device_ids: set[str] = set(call.data.get(ATTR_DEVICE_ID, []))
    for entity_id in call.data.get(ATTR_ENTITY_ID, []):
        entity = ent_reg.async_get(entity_id)
        if entity and entity.device_id:
            device_ids.add(entity.device_id)
    for area_id in call.data.get(ATTR_AREA_ID, []):
        for device in dr.async_entries_for_area(dev_reg, area_id):
            device_ids.add(device.id)

    matches: list[Match] = []
    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        if device and (match := _app_from_device(hass, device)):
            matches.append(match)
    return matches


def _targets(hass: HomeAssistant, call: ServiceCall) -> list[Match]:
    """All targeted applications from name and/or entity/device/area, de-duped."""
    matches = _resolve_targets(hass, call)
    if name := call.data.get(ATTR_APPLICATION):
        matches += _find_matches(hass, name, call.data.get(CONF_NAMESPACE))

    seen: set[tuple[str, str]] = set()
    unique: list[Match] = []
    for coordinator, app in matches:
        key = (coordinator.config_entry.entry_id, app.unique_id)
        if key not in seen:
            seen.add(key)
            unique.append((coordinator, app))
    if not unique:
        raise ServiceValidationError(
            "No ArgoCD application matched. Provide an application name or target "
            "an application entity/device."
        )
    return unique


def _find_matches(hass: HomeAssistant, name: str, namespace: str | None) -> list[Match]:
    """Locate ``(coordinator, app)`` pairs matching a name across all entries."""
    matches: list[Match] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        coordinator: ArgoCDCoordinator = entry.runtime_data
        for app in coordinator.data.values():
            if app.name == name and (namespace is None or app.namespace == namespace):
                matches.append((coordinator, app))
    return matches


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_SYNC):
        return

    async def _handle_sync(call: ServiceCall) -> None:
        for coordinator, app in _targets(hass, call):
            try:
                await coordinator.client.sync_application(
                    app.name,
                    app.namespace,
                    prune=call.data[ATTR_PRUNE],
                    revision=call.data.get(ATTR_REVISION),
                )
            except ArgoCDError as err:
                raise HomeAssistantError(f"Sync failed for {app.name}: {err}") from err
            await coordinator.async_request_refresh()

    async def _handle_refresh(call: ServiceCall) -> None:
        for coordinator, app in _targets(hass, call):
            try:
                await coordinator.client.refresh_application(
                    app.name, app.namespace, hard=call.data[ATTR_HARD]
                )
            except ArgoCDError as err:
                raise HomeAssistantError(
                    f"Refresh failed for {app.name}: {err}"
                ) from err
            await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_SYNC, _handle_sync, schema=SYNC_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH, _handle_refresh, schema=REFRESH_SCHEMA
    )


@callback
def async_unload_services(hass: HomeAssistant, unloading_entry_id: str) -> None:
    """Remove services once the last ArgoCD entry is unloaded.

    Called from ``async_unload_entry`` while the unloading entry is still marked
    loaded, so exclude it when checking whether any others remain.
    """
    others_loaded = any(
        entry.entry_id != unloading_entry_id and entry.state is ConfigEntryState.LOADED
        for entry in hass.config_entries.async_entries(DOMAIN)
    )
    if others_loaded:
        return
    hass.services.async_remove(DOMAIN, SERVICE_SYNC)
    hass.services.async_remove(DOMAIN, SERVICE_REFRESH)
