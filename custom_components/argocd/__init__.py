"""The ArgoCD integration."""

from __future__ import annotations

import logging
import os

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArgoCDClient, async_build_ssl_context, ca_ssl_kwargs
from .api.k8s import ArgoCDK8sClient
from .api.rest import ArgoCDRestClient
from .const import (
    AUTH_IN_CLUSTER,
    BACKEND_K8S,
    CONF_API_URL,
    CONF_AUTH_MODE,
    CONF_BACKEND,
    CONF_BASE_URL,
    CONF_CA_CERT,
    CONF_ENABLE_WATCH,
    CONF_NAMESPACE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_ENABLE_WATCH,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    FALLBACK_POLL_INTERVAL,
    SA_CA_PATH,
    SA_TOKEN_PATH,
)
from .coordinator import ArgoCDConfigEntry, ArgoCDCoordinator
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: ArgoCDConfigEntry) -> bool:
    """Set up ArgoCD from a config entry."""
    client = await _async_build_client(hass, entry)

    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    watch = (
        entry.options.get(CONF_ENABLE_WATCH, DEFAULT_ENABLE_WATCH)
        and client.supports_watch
    )
    # When watching, updates arrive via the stream; the poll just backstops it.
    interval = FALLBACK_POLL_INTERVAL if watch else scan_interval
    coordinator = ArgoCDCoordinator(hass, entry, client, interval)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if watch:
        coordinator.async_start_watch()
    async_setup_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ArgoCDConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = getattr(entry, "runtime_data", None)
        if coordinator is not None:
            await coordinator.client.async_close()
        async_unload_services(hass, entry.entry_id)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ArgoCDConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_build_client(
    hass: HomeAssistant, entry: ArgoCDConfigEntry
) -> ArgoCDClient:
    """Construct the backend client from the config entry."""
    data = entry.data
    session = async_get_clientsession(hass)
    verify_ssl = data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

    if data[CONF_BACKEND] == BACKEND_K8S:
        if data.get(CONF_AUTH_MODE) == AUTH_IN_CLUSTER:
            ca_path = await hass.async_add_executor_job(_read_in_cluster_ca)
            api_url = _in_cluster_api_url()
            ssl_ctx = await async_build_ssl_context(hass, cafile=ca_path)
            # Projected SA tokens rotate; re-read the file on demand.
            return ArgoCDK8sClient(
                session,
                api_url,
                token_provider=lambda: hass.async_add_executor_job(
                    _read_in_cluster_token
                ),
                verify_ssl=ssl_ctx,
                namespace=data.get(CONF_NAMESPACE),
            )
        ssl_ctx = await async_build_ssl_context(
            hass, verify=verify_ssl, **ca_ssl_kwargs(data.get(CONF_CA_CERT))
        )
        return ArgoCDK8sClient(
            session,
            data[CONF_API_URL],
            token=data[CONF_TOKEN],
            verify_ssl=ssl_ctx,
            namespace=data.get(CONF_NAMESPACE),
        )

    # REST backend
    ssl_ctx = await async_build_ssl_context(hass, verify=verify_ssl)
    return ArgoCDRestClient(
        session,
        data[CONF_BASE_URL],
        token=data.get(CONF_TOKEN),
        username=data.get(CONF_USERNAME),
        password=data.get(CONF_PASSWORD),
        verify_ssl=ssl_ctx,
    )


def _read_in_cluster_token() -> str:
    """Read the mounted ServiceAccount token (blocking; run in executor)."""
    with open(SA_TOKEN_PATH, encoding="utf-8") as handle:
        return handle.read().strip()


def _read_in_cluster_ca() -> str | None:
    """Return the mounted ServiceAccount CA path if present (run in executor)."""
    return SA_CA_PATH if os.path.exists(SA_CA_PATH) else None


def _read_in_cluster_for_flow() -> tuple[str, str | None]:
    """Read token + CA together for one-shot config-flow validation."""
    return _read_in_cluster_token(), _read_in_cluster_ca()


def _in_cluster_api_url() -> str:
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS") or os.environ.get(
        "KUBERNETES_SERVICE_PORT", "443"
    )
    # IPv6 hosts need brackets in a URL authority.
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"https://{host}:{port}"
