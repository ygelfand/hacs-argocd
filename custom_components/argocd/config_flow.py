"""Config and options flow for the ArgoCD integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    ArgoCDAuthError,
    ArgoCDClient,
    ArgoCDConnectionError,
    async_build_ssl_context,
    ca_ssl_kwargs,
)
from .api.k8s import ArgoCDK8sClient
from .api.rest import ArgoCDRestClient
from .const import (
    AUTH_IN_CLUSTER,
    AUTH_MANUAL,
    AUTH_TOKEN,
    AUTH_USERPASS,
    BACKEND_K8S,
    BACKEND_REST,
    CONF_API_URL,
    CONF_APP_NAMESPACE,
    CONF_AUTH_MODE,
    CONF_AUTO_ADD_DISCOVERED,
    CONF_BACKEND,
    CONF_BASE_URL,
    CONF_CA_CERT,
    CONF_ENABLE_WATCH,
    CONF_ENABLE_WRITE,
    CONF_NAME,
    CONF_NAMESPACE,
    CONF_PASSWORD,
    CONF_PROJECT,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_AUTO_ADD_DISCOVERED,
    DEFAULT_ENABLE_WATCH,
    DEFAULT_ENABLE_WRITE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .models import ArgoApplication

_LOGGER = logging.getLogger(__name__)

_PASSWORD = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)
_URL = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
)


async def _async_validate(
    hass, client: ArgoCDClient
) -> tuple[str | None, list[ArgoApplication]]:
    """List applications to validate a connection.

    Returns ``(error_key, apps)``. ``error_key`` is ``None`` on success, and the
    returned apps are used to populate the project/namespace filter choices.
    """
    try:
        apps = await client.list_applications()
    except ArgoCDAuthError:
        return "invalid_auth", []
    except ArgoCDConnectionError as err:
        if "certificate" in str(err).lower():
            return "bad_certificate", []
        return "cannot_connect", []
    except Exception:  # noqa: BLE001 - surface anything unexpected as a form error
        _LOGGER.exception("Unexpected error validating ArgoCD connection")
        return "unknown", []
    return None, apps


def _multi_select(options: list[str]) -> selector.SelectSelector:
    """A multi-select dropdown pre-filled with discovered values, free-type ok."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            multiple=True,
            custom_value=True,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


class ArgoCDConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the ArgoCD config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._discovered: list[ArgoApplication] = []
        self._reconfigure = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["rest", "k8s"])

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit an existing entry's connection settings in place."""
        self._reconfigure = True
        self._data = dict(self._get_reconfigure_entry().data)
        if self._data.get(CONF_BACKEND) == BACKEND_K8S:
            return await self.async_step_k8s()
        return await self.async_step_rest()

    def _form(
        self, step_id: str, schema: vol.Schema, errors: dict[str, str]
    ) -> ConfigFlowResult:
        """Show a form, pre-filling fields from current data (for reconfigure)."""
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, self._data),
            errors=errors,
        )

    async def _validate_and_advance(
        self, client: ArgoCDClient
    ) -> tuple[str | None, ConfigFlowResult | None]:
        """Validate a client, then move to the filter step on success."""
        error, apps = await _async_validate(self.hass, client)
        if error:
            return error, None
        self._discovered = apps
        return None, await self.async_step_filters()

    async def async_step_rest(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update({CONF_BACKEND: BACKEND_REST, **user_input})
            if user_input[CONF_AUTH_MODE] == AUTH_TOKEN:
                return await self.async_step_rest_token()
            return await self.async_step_rest_userpass()

        schema = vol.Schema(
            {
                vol.Required(CONF_BASE_URL): _URL,
                vol.Required(
                    CONF_AUTH_MODE, default=AUTH_TOKEN
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[AUTH_TOKEN, AUTH_USERPASS],
                        translation_key="rest_auth_mode",
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL
                ): selector.BooleanSelector(),
            }
        )
        return self._form("rest", schema, {})

    async def async_step_rest_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            error, result = await self._validate_and_advance(self._build_rest_client())
            if error:
                errors["base"] = error
            else:
                return result

        schema = vol.Schema({vol.Required(CONF_TOKEN): _PASSWORD})
        return self._form("rest_token", schema, errors)

    async def async_step_rest_userpass(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            client = self._build_rest_client()
            try:
                await client.async_login()
            except ArgoCDAuthError:
                errors["base"] = "invalid_auth"
            except ArgoCDConnectionError:
                errors["base"] = "cannot_connect"
            else:
                # Persist the exchanged token alongside creds for re-login.
                self._data[CONF_TOKEN] = client.token
                error, result = await self._validate_and_advance(client)
                if error:
                    errors["base"] = error
                else:
                    return result

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): _PASSWORD,
            }
        )
        return self._form("rest_userpass", schema, errors)

    def _build_rest_client(self) -> ArgoCDRestClient:
        return ArgoCDRestClient(
            async_get_clientsession(self.hass),
            self._data[CONF_BASE_URL],
            token=self._data.get(CONF_TOKEN),
            username=self._data.get(CONF_USERNAME),
            password=self._data.get(CONF_PASSWORD),
            verify_ssl=self._data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        )

    async def async_step_k8s(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update({CONF_BACKEND: BACKEND_K8S, **user_input})
            if user_input[CONF_AUTH_MODE] == AUTH_MANUAL:
                return await self.async_step_k8s_manual()
            error, result = await self._validate_and_advance(
                await self._build_k8s_client()
            )
            if error:
                errors["base"] = error
            else:
                return result

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_AUTH_MODE, default=AUTH_IN_CLUSTER
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[AUTH_IN_CLUSTER, AUTH_MANUAL],
                        translation_key="k8s_auth_mode",
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional(CONF_NAMESPACE): str,
            }
        )
        return self._form("k8s", schema, errors)

    async def async_step_k8s_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            error, result = await self._validate_and_advance(
                await self._build_k8s_client()
            )
            if error:
                errors["base"] = error
            else:
                return result

        schema = vol.Schema(
            {
                vol.Required(CONF_API_URL): _URL,
                vol.Required(CONF_TOKEN): _PASSWORD,
                vol.Required(
                    CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL
                ): selector.BooleanSelector(),
                vol.Optional(CONF_CA_CERT): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }
        )
        return self._form("k8s_manual", schema, errors)

    async def _build_k8s_client(self) -> ArgoCDK8sClient:
        from . import _in_cluster_api_url, _read_in_cluster_for_flow

        session = async_get_clientsession(self.hass)
        if self._data.get(CONF_AUTH_MODE) == AUTH_IN_CLUSTER:
            token, ca_path = await self.hass.async_add_executor_job(
                _read_in_cluster_for_flow
            )
            ssl_ctx = await async_build_ssl_context(self.hass, cafile=ca_path)
            return ArgoCDK8sClient(
                session,
                _in_cluster_api_url(),
                token=token,
                verify_ssl=ssl_ctx,
                namespace=self._data.get(CONF_NAMESPACE),
            )
        ssl_ctx = await async_build_ssl_context(
            self.hass,
            verify=self._data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            **ca_ssl_kwargs(self._data.get(CONF_CA_CERT)),
        )
        return ArgoCDK8sClient(
            session,
            self._data[CONF_API_URL],
            token=self._data[CONF_TOKEN],
            verify_ssl=ssl_ctx,
            namespace=self._data.get(CONF_NAMESPACE),
        )

    def _default_name(self) -> str:
        if self._data.get(CONF_BACKEND) == BACKEND_REST:
            return self._data.get(CONF_BASE_URL, "ArgoCD")
        return self._data.get(CONF_API_URL) or "In-cluster ArgoCD"

    async def async_step_filters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name + optional project/namespace filters, pre-filled from discovery."""
        if user_input is not None:
            self._data[CONF_NAME] = user_input.get(CONF_NAME) or self._default_name()
            self._data[CONF_PROJECT] = user_input.get(CONF_PROJECT, [])
            self._data[CONF_APP_NAMESPACE] = user_input.get(CONF_APP_NAMESPACE, [])
            return await self._async_finish()

        projects = sorted({a.project for a in self._discovered if a.project})
        namespaces = sorted({a.namespace for a in self._discovered if a.namespace})
        schema = vol.Schema(
            {
                vol.Optional(CONF_NAME, default=self._default_name()): str,
                vol.Optional(CONF_PROJECT, default=[]): _multi_select(projects),
                vol.Optional(CONF_APP_NAMESPACE, default=[]): _multi_select(namespaces),
            }
        )
        return self.async_show_form(
            step_id="filters",
            data_schema=self.add_suggested_values_to_schema(schema, self._data),
            description_placeholders={"count": str(len(self._discovered))},
        )

    async def _async_finish(self) -> ConfigFlowResult:
        title = self._data.get(CONF_NAME) or self._default_name()
        if self._reconfigure:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), title=title, data=self._data
            )
        if self._data[CONF_BACKEND] == BACKEND_REST:
            uid = f"rest::{self._data[CONF_BASE_URL]}"
        else:
            api = self._data.get(CONF_API_URL, "in-cluster")
            ns = self._data.get(CONF_NAMESPACE, "*")
            uid = f"k8s::{api}::{ns}"
        await self.async_set_unique_id(uid)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=title, data=self._data)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._data = dict(entry_data)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        auth_mode = self._data.get(CONF_AUTH_MODE)

        if user_input is not None:
            self._data.update(user_input)
            if self._data[CONF_BACKEND] == BACKEND_REST:
                client: ArgoCDClient = self._build_rest_client()
                if auth_mode == AUTH_USERPASS:
                    try:
                        await client.async_login()
                        self._data[CONF_TOKEN] = client.token
                    except (ArgoCDAuthError, ArgoCDConnectionError):
                        errors["base"] = "invalid_auth"
            else:
                client = await self._build_k8s_client()
            if not errors:
                error, _ = await _async_validate(self.hass, client)
                if error:
                    errors["base"] = error
                else:
                    return self.async_update_reload_and_abort(
                        reauth_entry, data=self._data
                    )

        if auth_mode == AUTH_USERPASS:
            fields = {vol.Required(CONF_PASSWORD): _PASSWORD}
        else:
            fields = {vol.Required(CONF_TOKEN): _PASSWORD}
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=vol.Schema(fields), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ArgoCDOptionsFlow()


class ArgoCDOptionsFlow(OptionsFlow):
    """Handle ArgoCD options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=3600,
                        step=5,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_ENABLE_WRITE,
                    default=options.get(CONF_ENABLE_WRITE, DEFAULT_ENABLE_WRITE),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_AUTO_ADD_DISCOVERED,
                    default=options.get(
                        CONF_AUTO_ADD_DISCOVERED, DEFAULT_AUTO_ADD_DISCOVERED
                    ),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_ENABLE_WATCH,
                    default=options.get(CONF_ENABLE_WATCH, DEFAULT_ENABLE_WATCH),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
