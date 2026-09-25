from __future__ import annotations

import json

import pytest

from cpn.frontend.server import handle_request, serve_projection


def _projection():
    return {
        "schema_version": "rpnh/net_view/v1",
        "source": {"mode": "initial_configured"},
        "summary": {"title": "test net"},
        "nodes": [
            {
                "id": "p0", "label": "start", "kind": "place",
                "category": "place", "hidden_by_default": False,
            },
            {
                "id": "t0", "label": "run", "kind": "transition",
                "category": "execution", "hidden_by_default": False,
            },
            {
                "id": "r0", "label": "capacity", "kind": "place",
                "category": "resource", "hidden_by_default": True,
            },
        ],
        "edges": [
            {
                "id": "e0", "source": "p0", "target": "t0",
                "kind": "arc", "weight": 1, "outcome": None,
                "hidden_by_default": False,
            },
            {
                "id": "e1", "source": "r0", "target": "t0",
                "kind": "arc", "weight": 1, "outcome": None,
                "hidden_by_default": True,
            },
        ],
    }


def test_viewer_api_and_static_assets_are_read_only() -> None:
    calls = 0

    def provider():
        nonlocal calls
        calls += 1
        return _projection()

    get = handle_request(provider, "GET", "/api/v1/net")
    assert get.status == 200
    assert json.loads(get.body)["schema_version"] == "rpnh/net_view/v1"
    assert calls == 1
    head = handle_request(provider, "HEAD", "/api/v1/net")
    assert head.status == 200 and head.body == b"" and calls == 2
    rejected = handle_request(provider, "POST", "/api/v1/net")
    assert rejected.status == 405 and calls == 2
    index = handle_request(provider, "GET", "/", show_resources=True)
    assert index.status == 200
    assert b'"showResources": true' in index.body


def test_viewer_rejects_non_loopback_bind() -> None:
    with pytest.raises(ValueError, match="回环"):
        serve_projection(_projection, host="0.0.0.0", open_browser=False)


def test_viewer_reports_a_moving_registry_head_without_partial_graph() -> None:
    def changed():
        raise RuntimeError("Registry changed during inspection; refresh")
    response = handle_request(changed, "GET", "/api/v1/net")
    assert response.status == 500
    value = json.loads(response.body)
    assert set(value) == {"error"} and "Registry changed" in value["error"]
