"""Shared pytest fixtures for the ArgoCD integration tests."""

from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of custom integrations in all tests."""
    yield


# A single ArgoCD Application payload. The REST API and the CRD API return this
# same shape, so one fixture exercises both parsers.
SAMPLE_APP: dict = {
    "metadata": {"name": "guestbook", "namespace": "argocd"},
    "spec": {
        "project": "default",
        "source": {
            "repoURL": "https://github.com/argoproj/argocd-example-apps",
            "path": "guestbook",
            "targetRevision": "HEAD",
        },
        "destination": {
            "namespace": "guestbook",
            "server": "https://kubernetes.default.svc",
        },
    },
    "status": {
        "sync": {"status": "Synced", "revision": "abc1234"},
        "health": {"status": "Healthy", "message": "all good"},
        "operationState": {"phase": "Succeeded", "finishedAt": "2026-07-01T12:00:00Z"},
        "resources": [{"kind": "Deployment"}, {"kind": "Service"}],
    },
}

SAMPLE_APP_DEGRADED: dict = {
    "metadata": {"name": "broken", "namespace": "argocd"},
    "spec": {"project": "default"},
    "status": {
        "sync": {"status": "OutOfSync"},
        "health": {"status": "Degraded"},
    },
}
