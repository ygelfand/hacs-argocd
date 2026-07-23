"""Normalized data model for ArgoCD Applications.

Both backends (REST API and Kubernetes CRD API) return an ``Application``
object with an identical schema under ``metadata``/``spec``/``status`` (the REST
API simply serializes the same CRD), so a single parser serves both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .const import HEALTH_UNKNOWN, HEALTHY_STATES, SYNC_SYNCED, SYNC_UNKNOWN


def _get(obj: dict[str, Any] | None, *path: str, default: Any = None) -> Any:
    """Safely walk a nested dict by keys, returning ``default`` if missing."""
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def _parse_ts(value: Any) -> datetime | None:
    """Parse an RFC3339 timestamp as emitted by Kubernetes/ArgoCD."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(slots=True)
class ArgoApplication:
    """A normalized view of a single ArgoCD Application."""

    name: str
    namespace: str
    project: str | None = None
    sync_status: str = SYNC_UNKNOWN
    health_status: str = HEALTH_UNKNOWN
    health_message: str | None = None
    revision: str | None = None
    target_revision: str | None = None
    repo_url: str | None = None
    path: str | None = None
    dest_namespace: str | None = None
    dest_server: str | None = None
    operation_phase: str | None = None
    last_sync_at: datetime | None = None
    initiated_by: str | None = None
    automated: bool = False
    resource_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def unique_id(self) -> str:
        """Stable identifier scoped to the app namespace + name."""
        return f"{self.namespace}/{self.name}"

    @property
    def is_synced(self) -> bool:
        return self.sync_status == SYNC_SYNCED

    @property
    def is_healthy(self) -> bool:
        return self.health_status in HEALTHY_STATES

    @classmethod
    def from_api(cls, obj: dict[str, Any]) -> ArgoApplication:
        """Build from a raw Application dict (REST item or CRD object)."""
        metadata = obj.get("metadata", {}) or {}
        spec = obj.get("spec", {}) or {}
        status = obj.get("status", {}) or {}

        source = spec.get("source", {}) or {}
        # Multi-source apps expose a ``sources`` list; fall back to the first.
        if not source and isinstance(spec.get("sources"), list) and spec["sources"]:
            source = spec["sources"][0] or {}
        destination = spec.get("destination", {}) or {}

        resources = status.get("resources")
        resource_count = len(resources) if isinstance(resources, list) else 0

        initiated_by = _get(status, "operationState", "operation", "initiatedBy") or {}

        return cls(
            name=metadata.get("name", ""),
            namespace=metadata.get("namespace", ""),
            project=spec.get("project"),
            sync_status=_get(status, "sync", "status", default=SYNC_UNKNOWN),
            health_status=_get(status, "health", "status", default=HEALTH_UNKNOWN),
            health_message=_get(status, "health", "message"),
            revision=_get(status, "sync", "revision"),
            target_revision=source.get("targetRevision"),
            repo_url=source.get("repoURL"),
            path=source.get("path") or source.get("chart"),
            dest_namespace=destination.get("namespace"),
            dest_server=destination.get("server") or destination.get("name"),
            operation_phase=_get(status, "operationState", "phase"),
            last_sync_at=_parse_ts(_get(status, "operationState", "finishedAt")),
            initiated_by=initiated_by.get("username"),
            automated=bool(initiated_by.get("automated", False)),
            resource_count=resource_count,
            raw=obj,
        )


CLUSTER_SUCCESSFUL = "Successful"
CLUSTER_FAILED = "Failed"
CLUSTER_UNKNOWN = "Unknown"
CLUSTER_STATES = (CLUSTER_SUCCESSFUL, CLUSTER_FAILED, CLUSTER_UNKNOWN)


@dataclass(slots=True)
class ArgoCluster:
    """A normalized view of an ArgoCD destination cluster (REST backend only)."""

    name: str
    server: str | None = None
    connection_status: str = CLUSTER_UNKNOWN
    message: str | None = None
    server_version: str | None = None
    applications_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def unique_id(self) -> str:
        return self.name or self.server or "unknown"

    @property
    def is_connected(self) -> bool:
        return self.connection_status == CLUSTER_SUCCESSFUL

    @classmethod
    def from_api(cls, obj: dict[str, Any]) -> ArgoCluster:
        """Build from a raw Cluster dict returned by ``/api/v1/clusters``."""
        info = obj.get("info", {}) or {}
        conn = obj.get("connectionState") or info.get("connectionState") or {}
        count = info.get("applicationsCount")
        return cls(
            name=obj.get("name") or obj.get("server", ""),
            server=obj.get("server"),
            connection_status=conn.get("status") or CLUSTER_UNKNOWN,
            message=conn.get("message"),
            server_version=obj.get("serverVersion") or info.get("serverVersion"),
            applications_count=count if isinstance(count, int) else 0,
            raw=obj,
        )
