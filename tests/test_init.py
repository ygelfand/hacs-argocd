"""End-to-end setup test: entities are created and services work."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.argocd.api import ArgoCDClient
from custom_components.argocd.const import (
    BACKEND_REST,
    CONF_BACKEND,
    CONF_BASE_URL,
    DOMAIN,
    SERVICE_REFRESH,
    SERVICE_SYNC,
)
from custom_components.argocd.models import ArgoApplication, ArgoCluster

from .conftest import SAMPLE_APP, SAMPLE_APP_DEGRADED


def _fake_client(clusters: list | None = None) -> AsyncMock:
    client = AsyncMock(spec=ArgoCDClient)
    client.list_applications.return_value = [
        ArgoApplication.from_api(SAMPLE_APP),
        ArgoApplication.from_api(SAMPLE_APP_DEGRADED),
    ]
    client.list_clusters.return_value = clusters or []
    return client


async def _setup(hass: HomeAssistant, client: AsyncMock) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_BACKEND: BACKEND_REST, CONF_BASE_URL: "https://argocd.example.com"},
        title="argocd.example.com",
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.argocd._async_build_client",
        AsyncMock(return_value=client),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_entities_created(hass: HomeAssistant) -> None:
    await _setup(hass, _fake_client())

    sync = hass.states.get("sensor.argocd_guestbook_sync_status")
    assert sync is not None and sync.state == "synced"
    assert sync.attributes["revision"] == "abc1234"

    health = hass.states.get("sensor.argocd_guestbook_health")
    assert health is not None and health.state == "healthy"

    last_sync = hass.states.get("sensor.argocd_guestbook_last_sync")
    assert last_sync is not None
    assert last_sync.state not in ("unknown", "unavailable")
    assert last_sync.attributes["initiated_by"] == "alice"
    assert last_sync.attributes["automated"] is False

    assert hass.states.get("binary_sensor.argocd_guestbook_out_of_sync").state == "off"
    assert hass.states.get("binary_sensor.argocd_broken_out_of_sync").state == "on"
    assert hass.states.get("binary_sensor.argocd_broken_unhealthy").state == "on"

    # Buttons exist (write enabled by default).
    assert hass.states.get("button.argocd_guestbook_sync") is not None

    # Aggregate summary: 2 apps, 1 out of sync, 1 unhealthy.
    summary = hass.states.get("sensor.argocd_example_com_applications")
    assert summary is not None and summary.state == "2"
    assert summary.attributes["out_of_sync"] == 1
    assert summary.attributes["unhealthy"] == 1


async def test_cluster_entities(hass: HomeAssistant) -> None:
    clusters = [
        ArgoCluster.from_api(
            {
                "name": "prod",
                "server": "https://prod",
                "connectionState": {"status": "Failed"},
            }
        )
    ]
    await _setup(hass, _fake_client(clusters))

    status = hass.states.get("sensor.argocd_cluster_prod_connection")
    assert status is not None and status.state == "failed"
    unreachable = hass.states.get("binary_sensor.argocd_cluster_prod_unreachable")
    assert unreachable.state == "on"


async def test_no_cluster_entities_without_clusters(hass: HomeAssistant) -> None:
    await _setup(hass, _fake_client())
    assert hass.states.get("sensor.argocd_cluster_prod_connection") is None


async def test_sync_service(hass: HomeAssistant) -> None:
    client = _fake_client()
    await _setup(hass, client)

    await hass.services.async_call(
        DOMAIN, SERVICE_SYNC, {"application": "guestbook", "prune": True}, blocking=True
    )
    client.sync_application.assert_awaited_once_with(
        "guestbook", "argocd", prune=True, revision=None
    )


async def test_refresh_service(hass: HomeAssistant) -> None:
    client = _fake_client()
    await _setup(hass, client)

    await hass.services.async_call(
        DOMAIN, SERVICE_REFRESH, {"application": "broken", "hard": True}, blocking=True
    )
    client.refresh_application.assert_awaited_once_with("broken", "argocd", hard=True)


async def test_sync_service_by_entity_target(hass: HomeAssistant) -> None:
    client = _fake_client()
    await _setup(hass, client)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SYNC,
        {"entity_id": "sensor.argocd_guestbook_sync_status"},
        blocking=True,
    )
    client.sync_application.assert_awaited_once_with(
        "guestbook", "argocd", prune=False, revision=None
    )


async def test_button_press_triggers_sync(hass: HomeAssistant) -> None:
    client = _fake_client()
    await _setup(hass, client)

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.argocd_guestbook_sync"},
        blocking=True,
    )
    client.sync_application.assert_awaited_once_with("guestbook", "argocd")
