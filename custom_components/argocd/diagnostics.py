"""Diagnostics support for the ArgoCD integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_CA_CERT, CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME
from .coordinator import ArgoCDConfigEntry

TO_REDACT = {CONF_TOKEN, CONF_PASSWORD, CONF_USERNAME, CONF_CA_CERT}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ArgoCDConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    apps = [
        {
            "name": app.name,
            "namespace": app.namespace,
            "project": app.project,
            "sync_status": app.sync_status,
            "health_status": app.health_status,
            "operation_phase": app.operation_phase,
            "resource_count": app.resource_count,
        }
        for app in coordinator.data.values()
    ]
    clusters = [
        {
            "name": cluster.name,
            "server": cluster.server,
            "connection_status": cluster.connection_status,
            "server_version": cluster.server_version,
            "applications_count": cluster.applications_count,
        }
        for cluster in coordinator.clusters.values()
    ]
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "last_update_success": coordinator.last_update_success,
        "application_count": len(apps),
        "applications": apps,
        "cluster_count": len(clusters),
        "clusters": clusters,
    }
