"""Constants for the ArgoCD integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "argocd"

# --- Backends -------------------------------------------------------------
BACKEND_REST: Final = "rest"
BACKEND_K8S: Final = "k8s"

# --- Auth modes -----------------------------------------------------------
# REST
AUTH_TOKEN: Final = "token"
AUTH_USERPASS: Final = "userpass"
# K8s
AUTH_IN_CLUSTER: Final = "in_cluster"
AUTH_MANUAL: Final = "manual"

# --- Config / options keys ------------------------------------------------
CONF_BACKEND: Final = "backend"
CONF_AUTH_MODE: Final = "auth_mode"
CONF_NAME: Final = "name"

# REST
CONF_BASE_URL: Final = "base_url"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_TOKEN: Final = "token"
CONF_APP_NAMESPACE: Final = "app_namespace"
CONF_PROJECT: Final = "project"

# K8s
CONF_API_URL: Final = "api_url"
CONF_CA_CERT: Final = "ca_cert"
CONF_NAMESPACE: Final = "namespace"

# Shared
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_ENABLE_WRITE: Final = "enable_write"
CONF_AUTO_ADD_DISCOVERED: Final = "auto_add_discovered"
CONF_ENABLE_WATCH: Final = "enable_watch"

# --- Defaults -------------------------------------------------------------
DEFAULT_SCAN_INTERVAL: Final = 60  # seconds
DEFAULT_VERIFY_SSL: Final = True
DEFAULT_ENABLE_WRITE: Final = True
DEFAULT_AUTO_ADD_DISCOVERED: Final = True
DEFAULT_ENABLE_WATCH: Final = False
DEFAULT_TIMEOUT: Final = 30  # seconds
MIN_SCAN_INTERVAL: Final = 15

# --- Watch (streaming) tuning ---------------------------------------------
# Server-side watch timeout; the apiserver closes the stream after this and we
# reconnect. The fallback poll is a safety net when watch is enabled.
WATCH_TIMEOUT_SECONDS: Final = 300
WATCH_RECONNECT_DELAY: Final = 5  # seconds between watch reconnect attempts
FALLBACK_POLL_INTERVAL: Final = 300  # seconds; coordinator poll while watching

# --- In-cluster ServiceAccount paths --------------------------------------
SA_DIR: Final = "/var/run/secrets/kubernetes.io/serviceaccount"
SA_TOKEN_PATH: Final = f"{SA_DIR}/token"
SA_CA_PATH: Final = f"{SA_DIR}/ca.crt"

# --- ArgoCD status vocabulary --------------------------------------------
SYNC_SYNCED: Final = "Synced"
SYNC_OUT_OF_SYNC: Final = "OutOfSync"
SYNC_UNKNOWN: Final = "Unknown"

HEALTH_HEALTHY: Final = "Healthy"
HEALTH_PROGRESSING: Final = "Progressing"
HEALTH_DEGRADED: Final = "Degraded"
HEALTH_SUSPENDED: Final = "Suspended"
HEALTH_MISSING: Final = "Missing"
HEALTH_UNKNOWN: Final = "Unknown"

# All possible values, in display order (used to build the enum sensor options).
SYNC_STATES: Final = (SYNC_SYNCED, SYNC_OUT_OF_SYNC, SYNC_UNKNOWN)
HEALTH_STATES: Final = (
    HEALTH_HEALTHY,
    HEALTH_PROGRESSING,
    HEALTH_DEGRADED,
    HEALTH_SUSPENDED,
    HEALTH_MISSING,
    HEALTH_UNKNOWN,
)

# Health states that are NOT considered a problem.
HEALTHY_STATES: Final = frozenset({HEALTH_HEALTHY, HEALTH_SUSPENDED})

# --- Services -------------------------------------------------------------
SERVICE_SYNC: Final = "sync"
SERVICE_REFRESH: Final = "refresh"

ATTR_APPLICATION: Final = "application"
ATTR_PRUNE: Final = "prune"
ATTR_REVISION: Final = "revision"
ATTR_HARD: Final = "hard"
