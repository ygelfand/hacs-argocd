"""Tests for the Kubernetes backend client, incl. projected-token rotation."""

from __future__ import annotations

import json

import pytest

from custom_components.argocd.api import ArgoCDAuthError
from custom_components.argocd.api.k8s import ArgoCDK8sClient


class _LineContent:
    """Minimal async-iterable stand-in for aiohttp ``resp.content``."""

    def __init__(self, lines):
        self._lines = [ln.encode() for ln in lines]

    def __aiter__(self):
        self._it = iter(self._lines)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class _FakeResp:
    def __init__(self, status, json_data=None, text="", lines=None):
        self.status = status
        self._json = json_data or {}
        self.content_type = "application/json"
        self._text = text
        self.content = _LineContent(lines or [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._json

    async def text(self):
        return self._text


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, *, headers, **kwargs):
        self.calls.append({"method": method, "url": url, "headers": dict(headers)})
        return self._responses.pop(0)


async def test_static_token_sends_bearer() -> None:
    session = _FakeSession([_FakeResp(200, {"items": []})])
    client = ArgoCDK8sClient(session, "https://k8s", token="static")

    assert await client.list_applications() == []
    assert session.calls[0]["headers"]["Authorization"] == "Bearer static"


async def test_401_without_provider_raises() -> None:
    session = _FakeSession([_FakeResp(401)])
    client = ArgoCDK8sClient(session, "https://k8s", token="static")

    with pytest.raises(ArgoCDAuthError):
        await client.list_applications()
    assert len(session.calls) == 1  # no retry for a static token


async def test_projected_token_rotation_retries_on_401() -> None:
    tokens = iter(["old", "new"])

    async def provider() -> str:
        return next(tokens)

    session = _FakeSession([_FakeResp(401), _FakeResp(200, {"items": []})])
    client = ArgoCDK8sClient(session, "https://k8s", token_provider=provider)

    assert await client.list_applications() == []
    # First attempt used the stale token; after 401 it re-read and retried.
    assert session.calls[0]["headers"]["Authorization"] == "Bearer old"
    assert session.calls[1]["headers"]["Authorization"] == "Bearer new"


async def test_namespace_scopes_the_list_path() -> None:
    session = _FakeSession([_FakeResp(200, {"items": []})])
    client = ArgoCDK8sClient(session, "https://k8s", token="t", namespace="argocd")

    await client.list_applications()
    assert session.calls[0]["url"].endswith(
        "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications"
    )


async def test_watch_stream_merges_events() -> None:
    list_resp = _FakeResp(
        200,
        json_data={
            "metadata": {"resourceVersion": "10"},
            "items": [
                {
                    "metadata": {"name": "a", "namespace": "argocd"},
                    "status": {
                        "sync": {"status": "Synced"},
                        "health": {"status": "Healthy"},
                    },
                }
            ],
        },
    )
    modified = json.dumps(
        {
            "type": "MODIFIED",
            "object": {
                "metadata": {"name": "a", "namespace": "argocd"},
                "status": {"sync": {"status": "OutOfSync"}},
            },
        }
    )
    added = json.dumps(
        {
            "type": "ADDED",
            "object": {"metadata": {"name": "b", "namespace": "argocd"}, "status": {}},
        }
    )
    deleted = json.dumps(
        {
            "type": "DELETED",
            "object": {"metadata": {"name": "a", "namespace": "argocd"}},
        }
    )
    watch_resp = _FakeResp(200, lines=[modified, added, deleted])
    session = _FakeSession([list_resp, watch_resp])
    client = ArgoCDK8sClient(session, "https://k8s", token="t")

    snapshots = [snap async for snap in client.watch_applications()]

    # initial (a), MODIFIED a, ADDED b, DELETED a
    assert [len(s) for s in snapshots] == [1, 1, 2, 1]
    assert snapshots[1][0].sync_status == "OutOfSync"
    assert {app.name for app in snapshots[2]} == {"a", "b"}
    assert snapshots[3][0].name == "b"
    # watch request carried watch=true params on the second call
    assert session.calls[1]["url"].endswith("/applications")


async def test_watch_resource_version_expired_ends_session() -> None:
    list_resp = _FakeResp(
        200, json_data={"metadata": {"resourceVersion": "1"}, "items": []}
    )
    watch_resp = _FakeResp(410)
    session = _FakeSession([list_resp, watch_resp])
    client = ArgoCDK8sClient(session, "https://k8s", token="t")

    # 410 is swallowed: we get the initial snapshot, then the generator returns
    # (the coordinator would reconnect and re-list).
    snapshots = [snap async for snap in client.watch_applications()]
    assert snapshots == [[]]
