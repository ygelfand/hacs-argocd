"""Tests for the ArgoCD config flow (both backends)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.argocd.api import ArgoCDAuthError, ArgoCDConnectionError
from custom_components.argocd.const import (
    AUTH_MANUAL,
    AUTH_TOKEN,
    BACKEND_K8S,
    BACKEND_REST,
    CONF_API_URL,
    CONF_AUTH_MODE,
    CONF_BACKEND,
    CONF_BASE_URL,
    CONF_TOKEN,
    CONF_VERIFY_SSL,
    DOMAIN,
)

REST_CLIENT = "custom_components.argocd.config_flow.ArgoCDRestClient"
K8S_CLIENT = "custom_components.argocd.config_flow.ArgoCDK8sClient"


async def _start_menu(hass: HomeAssistant, next_step: str):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": next_step}
    )


async def test_rest_token_happy_path(hass: HomeAssistant) -> None:
    result = await _start_menu(hass, "rest")
    assert result["step_id"] == "rest"

    with (
        patch(REST_CLIENT) as client_cls,
        patch("custom_components.argocd.config_flow.async_get_clientsession"),
        patch("custom_components.argocd.async_setup_entry", return_value=True),
    ):
        client_cls.return_value.list_applications = AsyncMock(return_value=[])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BASE_URL: "https://argocd.example.com",
                CONF_AUTH_MODE: AUTH_TOKEN,
                CONF_VERIFY_SSL: True,
            },
        )
        assert result["step_id"] == "rest_token"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TOKEN: "secret-token"}
        )
        assert result["step_id"] == "filters"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"project": ["default"], "app_namespace": []}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BACKEND] == BACKEND_REST
    assert result["data"][CONF_TOKEN] == "secret-token"
    assert result["data"]["project"] == ["default"]


async def test_rest_token_invalid_auth(hass: HomeAssistant) -> None:
    result = await _start_menu(hass, "rest")
    with (
        patch(REST_CLIENT) as client_cls,
        patch("custom_components.argocd.config_flow.async_get_clientsession"),
    ):
        client_cls.return_value.list_applications = AsyncMock(
            side_effect=ArgoCDAuthError("401")
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BASE_URL: "https://argocd.example.com",
                CONF_AUTH_MODE: AUTH_TOKEN,
                CONF_VERIFY_SSL: True,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TOKEN: "bad"}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_rest_cannot_connect(hass: HomeAssistant) -> None:
    result = await _start_menu(hass, "rest")
    with (
        patch(REST_CLIENT) as client_cls,
        patch("custom_components.argocd.config_flow.async_get_clientsession"),
    ):
        client_cls.return_value.list_applications = AsyncMock(
            side_effect=ArgoCDConnectionError("no route")
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BASE_URL: "https://argocd.example.com",
                CONF_AUTH_MODE: AUTH_TOKEN,
                CONF_VERIFY_SSL: True,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TOKEN: "x"}
        )

    assert result["errors"] == {"base": "cannot_connect"}


async def test_k8s_manual_happy_path(hass: HomeAssistant) -> None:
    result = await _start_menu(hass, "k8s")
    assert result["step_id"] == "k8s"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_AUTH_MODE: AUTH_MANUAL}
    )
    assert result["step_id"] == "k8s_manual"

    with (
        patch(K8S_CLIENT) as client_cls,
        patch("custom_components.argocd.config_flow.async_get_clientsession"),
        patch("custom_components.argocd.async_setup_entry", return_value=True),
    ):
        client_cls.return_value.list_applications = AsyncMock(return_value=[])
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_API_URL: "https://10.0.0.1:6443",
                CONF_TOKEN: "sa-token",
                CONF_VERIFY_SSL: False,
            },
        )
        assert result["step_id"] == "filters"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"project": [], "app_namespace": []}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BACKEND] == BACKEND_K8S
    assert result["data"][CONF_API_URL] == "https://10.0.0.1:6443"


async def test_reconfigure_rest_updates_entry(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="rest::https://old.example.com",
        data={
            CONF_BACKEND: BACKEND_REST,
            CONF_AUTH_MODE: AUTH_TOKEN,
            CONF_BASE_URL: "https://old.example.com",
            CONF_TOKEN: "old-token",
            CONF_VERIFY_SSL: True,
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(REST_CLIENT) as client_cls,
        patch("custom_components.argocd.config_flow.async_get_clientsession"),
        patch("custom_components.argocd.async_setup_entry", return_value=True),
    ):
        client_cls.return_value.list_applications = AsyncMock(return_value=[])
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        assert result["step_id"] == "rest"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_BASE_URL: "https://new.example.com",
                CONF_AUTH_MODE: AUTH_TOKEN,
                CONF_VERIFY_SSL: True,
            },
        )
        assert result["step_id"] == "rest_token"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_TOKEN: "new-token"}
        )
        assert result["step_id"] == "filters"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"project": [], "app_namespace": []}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_BASE_URL] == "https://new.example.com"
    assert entry.data[CONF_TOKEN] == "new-token"
