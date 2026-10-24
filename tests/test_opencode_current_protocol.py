"""The stable 1.19.4 mixed legacy/v2 bootstrap has one RPNH authority."""
from urllib.parse import urlencode

import pytest

from cpn.frontend.opencode_http import OpenCodeHTTPServer
from cpn.frontend.opencode_protocol import COMMANDS, OpenCodeProtocol, get_opencode_profile
from cpn.rpnh.frontend_application import FrontendError
from test_opencode_frontend import ApplicationDouble, LocalGateway, MODEL, MODEL_TWO


@pytest.fixture
def context(tmp_path):
    app = ApplicationDouble()
    protocol = OpenCodeProtocol(LocalGateway(app), str(tmp_path))
    yield app, protocol
    protocol.close()


def test_legacy_and_v2_catalogs_project_the_same_rpnh_profiles(context):
    app, protocol = context
    legacy = protocol.route("GET", "/provider").body["all"][0]
    location = protocol.route("GET", "/api/location").body
    assert location == {"directory": protocol.directory,
                        "project": {"id": "rpnh", "directory": protocol.directory}}
    models = protocol.route("GET", "/api/model").body
    assert models["location"] == location
    assert {m["id"] for m in models["data"]} == set(legacy["models"])
    for model in models["data"]:
        assert model["providerID"] == "rpnh"
        assert model["api"] == {"id": model["id"], "type": "native", "settings": {}}
        assert model["request"] == {"headers": {}, "body": {}, "variant": "medium"}
        assert {v["id"] for v in model["variants"]} == set(legacy["models"][model["id"]]["variants"])
        assert all(v["headers"] == v["body"] == {} for v in model["variants"])
        assert model["capabilities"] == {"tools": False, "input": ["text"], "output": ["text"]}
        assert model["cost"] == []  # No measured usage or provider pricing exposed.
    assert {m["id"] for m in models["data"]} == {MODEL["selection"], MODEL_TWO["selection"]}
    agent = protocol.route("GET", "/api/agent").body["data"]
    assert len(agent) == 1 and agent[0]["id"] == "rpnh"
    assert agent[0]["permissions"] == []
    provider = protocol.route("GET", "/api/provider").body["data"]
    assert len(provider) == 1 and provider[0]["id"] == legacy["id"]
    assert provider[0]["request"] == {"headers": {}, "body": {}}
    commands = protocol.route("GET", "/api/command").body["data"]
    assert {command["name"] for command in commands} == set(COMMANDS)
    assert all(command["agent"] == "rpnh" for command in commands)
    for path in ("/api/integration", "/api/reference", "/api/skill"):
        assert protocol.route("GET", path).body == {"location": location, "data": []}
    assert protocol.route("GET", "/experimental/console").body == {
        "consoleManagedProviders": [], "switchableOrgCount": 0}
    assert app.calls == ["configuration"] and app.physical_calls == 0


@pytest.mark.parametrize("query", [
    {"location[directory]": "/other"}, {"location[workspace]": "other"},
    {"location[workspaceID]": "other"}, {"location": '{"directory":"/other"}'},
])
def test_v2_location_cannot_select_another_authority(context, query):
    app, protocol = context
    with pytest.raises(FrontendError) as error:
        protocol.route("GET", "/api/model?" + urlencode(query))
    assert error.value.code == "unsupported_location"
    assert app.calls == ["configuration"]


def test_v2_sdk_deep_object_location_and_duplicate_queries(context):
    _, protocol = context
    query = urlencode({"location[directory]": protocol.directory, "location[workspace]": ""})
    assert protocol.route("GET", "/api/model?" + query).body == protocol.route("GET", "/api/model").body
    with pytest.raises(FrontendError) as error:
        protocol.route("GET", "/api/model?" + query + "&" + query)
    assert error.value.code == "ambiguous_query"


@pytest.mark.parametrize("path", [
    "/api/agent", "/api/model", "/api/integration", "/api/credential",
    "/api/session", "/api/pty", "/experimental/console/switch",
])
def test_new_catalog_does_not_enable_native_writes(context, path):
    app, protocol = context
    with pytest.raises(FrontendError) as error:
        protocol.route("POST", path, {})
    assert error.value.code == "unsupported_by_rpnh_frontend"
    assert app.calls == ["configuration"]


@pytest.mark.parametrize("version", ["1.18.32", "1.18.35", "1.19.3", "2.0.32", "1.19.5"])
def test_historical_and_dev_versions_are_not_supported(version):
    with pytest.raises(ValueError):
        get_opencode_profile(certification_version=version)


def test_new_catalog_diagnostics_keep_only_known_route_shapes(context):
    _, protocol = context
    server = OpenCodeHTTPServer(protocol)
    try:
        server._server.record("GET", "/api/model?location[directory]=private", 200)
        server._server.record("GET", "/api/credential/private", 404)
        assert server.diagnostics() == [
            {"method": "GET", "route": "/api/model", "status": 200},
            {"method": "GET", "route": "unsupported", "status": 404},
        ]
    finally:
        server.close()
