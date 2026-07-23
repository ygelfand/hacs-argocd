"""Tests for the normalized ArgoApplication model."""

from __future__ import annotations

from custom_components.argocd.models import ArgoApplication, ArgoCluster

from .conftest import SAMPLE_APP, SAMPLE_APP_DEGRADED


def test_parse_healthy_app() -> None:
    app = ArgoApplication.from_api(SAMPLE_APP)
    assert app.name == "guestbook"
    assert app.namespace == "argocd"
    assert app.unique_id == "argocd/guestbook"
    assert app.project == "default"
    assert app.sync_status == "Synced"
    assert app.health_status == "Healthy"
    assert app.health_message == "all good"
    assert app.revision == "abc1234"
    assert app.target_revision == "HEAD"
    assert app.repo_url.endswith("argocd-example-apps")
    assert app.path == "guestbook"
    assert app.dest_namespace == "guestbook"
    assert app.dest_server == "https://kubernetes.default.svc"
    assert app.operation_phase == "Succeeded"
    assert app.last_sync_at is not None
    assert app.initiated_by == "alice"
    assert app.automated is False
    assert app.resource_count == 2
    assert app.is_synced is True
    assert app.is_healthy is True


def test_parse_degraded_app_defaults() -> None:
    app = ArgoApplication.from_api(SAMPLE_APP_DEGRADED)
    assert app.sync_status == "OutOfSync"
    assert app.health_status == "Degraded"
    assert app.is_synced is False
    assert app.is_healthy is False
    # Missing optional fields fall back cleanly.
    assert app.revision is None
    assert app.last_sync_at is None
    assert app.resource_count == 0


def test_parse_empty_payload() -> None:
    app = ArgoApplication.from_api({})
    assert app.sync_status == "Unknown"
    assert app.health_status == "Unknown"
    assert app.is_healthy is False


def test_suspended_is_not_a_problem() -> None:
    app = ArgoApplication.from_api(
        {
            "metadata": {"name": "x", "namespace": "argocd"},
            "status": {"health": {"status": "Suspended"}},
        }
    )
    assert app.is_healthy is True


def test_parse_cluster() -> None:
    cluster = ArgoCluster.from_api(
        {
            "name": "prod",
            "server": "https://prod.example.com",
            "serverVersion": "1.29",
            "connectionState": {"status": "Successful", "message": "ok"},
            "info": {"applicationsCount": 7},
        }
    )
    assert cluster.name == "prod"
    assert cluster.unique_id == "prod"
    assert cluster.connection_status == "Successful"
    assert cluster.is_connected is True
    assert cluster.server_version == "1.29"
    assert cluster.applications_count == 7


def test_parse_cluster_failed_defaults_and_fallback_name() -> None:
    cluster = ArgoCluster.from_api(
        {
            "server": "https://kubernetes.default.svc",
            "connectionState": {"status": "Failed"},
        }
    )
    assert cluster.name == "https://kubernetes.default.svc"
    assert cluster.unique_id == "https://kubernetes.default.svc"
    assert cluster.is_connected is False
    assert cluster.applications_count == 0


def test_multi_source_app() -> None:
    app = ArgoApplication.from_api(
        {
            "metadata": {"name": "multi", "namespace": "argocd"},
            "spec": {
                "sources": [
                    {"repoURL": "https://example.com/repo", "targetRevision": "v1"}
                ]
            },
            "status": {},
        }
    )
    assert app.repo_url == "https://example.com/repo"
    assert app.target_revision == "v1"
