"""Button platform: per-app Sync and Refresh actions (write mode only)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import ArgoCDClient, ArgoCDError
from .const import (
    CONF_ENABLE_WRITE,
    DEFAULT_ENABLE_WRITE,
)
from .coordinator import ArgoCDConfigEntry, ArgoCDCoordinator
from .entity import ArgoCDAppEntity, async_register_dynamic_entities
from .models import ArgoApplication


@dataclass(frozen=True, kw_only=True)
class ArgoAppButtonDescription(ButtonEntityDescription):
    """Describes an ArgoCD per-application button."""

    press_fn: Callable[[ArgoCDClient, ArgoApplication], Awaitable[None]]


APP_BUTTONS: tuple[ArgoAppButtonDescription, ...] = (
    ArgoAppButtonDescription(
        key="sync",
        translation_key="sync",
        press_fn=lambda client, app: client.sync_application(app.name, app.namespace),
    ),
    ArgoAppButtonDescription(
        key="refresh",
        translation_key="refresh",
        press_fn=lambda client, app: client.refresh_application(
            app.name, app.namespace
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArgoCDConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    if not entry.options.get(CONF_ENABLE_WRITE, DEFAULT_ENABLE_WRITE):
        return

    coordinator = entry.runtime_data

    def factory(app_key: str) -> list[ButtonEntity]:
        return [
            ArgoCDAppButton(coordinator, entry, app_key, desc) for desc in APP_BUTTONS
        ]

    async_register_dynamic_entities(
        entry, coordinator, async_add_entities, factory, lambda: coordinator.data
    )


class ArgoCDAppButton(ArgoCDAppEntity, ButtonEntity):
    """A Sync or Refresh button for one application."""

    entity_description: ArgoAppButtonDescription

    def __init__(
        self,
        coordinator: ArgoCDCoordinator,
        entry: ArgoCDConfigEntry,
        app_key: str,
        description: ArgoAppButtonDescription,
    ) -> None:
        super().__init__(coordinator, entry, app_key)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}:{app_key}:{description.key}"

    async def async_press(self) -> None:
        app = self.app
        if app is None:
            raise HomeAssistantError("Application is no longer available")
        try:
            await self.entity_description.press_fn(self.coordinator.client, app)
        except ArgoCDError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()
