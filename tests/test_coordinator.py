"""Tests for the ArgoCD data update coordinator error mapping."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.argocd.api import ArgoCDAuthError, ArgoCDConnectionError
from custom_components.argocd.const import DOMAIN
from custom_components.argocd.coordinator import ArgoCDCoordinator
from custom_components.argocd.models import ArgoApplication

from .conftest import SAMPLE_APP


def _make_coordinator(
    hass: HomeAssistant, client, data: dict | None = None
) -> ArgoCDCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data=data or {})
    entry.add_to_hass(hass)
    return ArgoCDCoordinator(hass, entry, client, 60)


async def test_update_success_keys_by_unique_id(hass: HomeAssistant) -> None:
    client = AsyncMock()
    client.list_applications.return_value = [ArgoApplication.from_api(SAMPLE_APP)]
    coordinator = _make_coordinator(hass, client)

    data = await coordinator._async_update_data()

    assert set(data) == {"argocd/guestbook"}
    assert data["argocd/guestbook"].sync_status == "Synced"


async def test_project_and_namespace_filters(hass: HomeAssistant) -> None:
    client = AsyncMock()
    client.list_applications.return_value = [
        ArgoApplication.from_api(
            {
                "metadata": {"name": "keep", "namespace": "argocd"},
                "spec": {"project": "default"},
                "status": {},
            }
        ),
        ArgoApplication.from_api(
            {
                "metadata": {"name": "other-project", "namespace": "argocd"},
                "spec": {"project": "infra"},
                "status": {},
            }
        ),
        ArgoApplication.from_api(
            {
                "metadata": {"name": "other-ns", "namespace": "team-a"},
                "spec": {"project": "default"},
                "status": {},
            }
        ),
    ]
    coordinator = _make_coordinator(
        hass, client, {"project": ["default"], "app_namespace": ["argocd"]}
    )

    data = await coordinator._async_update_data()

    assert set(data) == {"argocd/keep"}


async def test_auth_error_maps_to_config_entry_auth_failed(hass: HomeAssistant) -> None:
    client = AsyncMock()
    client.list_applications.side_effect = ArgoCDAuthError("401")
    coordinator = _make_coordinator(hass, client)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_connection_error_maps_to_update_failed(hass: HomeAssistant) -> None:
    client = AsyncMock()
    client.list_applications.side_effect = ArgoCDConnectionError("boom")
    coordinator = _make_coordinator(hass, client)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_watch_loop_pushes_updates(hass: HomeAssistant) -> None:
    client = AsyncMock()

    async def _watch():
        yield [ArgoApplication.from_api(SAMPLE_APP)]
        await asyncio.sleep(3600)  # hold the "stream" open until cancelled

    client.watch_applications = _watch
    coordinator = _make_coordinator(hass, client)

    task = asyncio.create_task(coordinator._run_watch())
    try:
        async with asyncio.timeout(1):
            while not (coordinator.data and "argocd/guestbook" in coordinator.data):
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert coordinator.data["argocd/guestbook"].sync_status == "Synced"
