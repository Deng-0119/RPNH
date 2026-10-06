"""Identity-only normalization rejects application hooks before any write."""
import pytest

from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.strict_contracts import content_schema_ref_payload
from test_candidate_plan_persistence import plan_fixture, authored, candidate_request, assert_plan_only
from test_candidate_plan_resources import resource


def _selection(axis, value):
    if axis == "entry_bundle":
        return {"entry_inputs": {"request": value}}
    return {axis: value}


@pytest.mark.parametrize("axis", ["entry_bundle", "owner_input_resources", "host_resource_refs", "host_artifact_refs"])
@pytest.mark.parametrize("base", [list, tuple], ids=["list", "tuple"])
def test_candidate_rejects_application_collection_identity_before_hooks_or_writes(plan_fixture, monkeypatch, axis, base):
    f = plan_fixture
    revision = authored(f)
    selected = resource(f)
    calls = []
    class ApplicationMeta(type):
        def __eq__(cls, other):
            calls.append("eq")
            return other is list
        def __hash__(cls):
            calls.append("hash")
            return type.__hash__(cls)
    class ApplicationCollection(base, metaclass=ApplicationMeta):
        def __iter__(self):
            calls.append("iter")
            return super().__iter__()
        def __bool__(self):
            calls.append("bool")
            return super().__len__() > 0
    value = ApplicationCollection([] if axis.startswith("host_") else [selected])
    request = candidate_request(f, revision, **_selection(axis, value))
    before = tuple(f[0].event_store.list_events())
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid collection must fail before HOST resolution or publication")
    monkeypatch.setattr(f[6], "resolve", forbidden)
    monkeypatch.setattr(_RegistryCore, "begin", forbidden)
    with pytest.raises((TypeError, ValueError)):
        f[-1].publish(**request)
    assert calls == []
    assert tuple(f[0].event_store.list_events()) == before
    assert not f[0].event_store.object_rows_by_type("collaboration_candidate_plan/v1")


@pytest.mark.parametrize("axis", ["entry_bundle", "owner_input_resources", "host_resource_refs", "host_artifact_refs"])
@pytest.mark.parametrize("base", [list, tuple], ids=["list", "tuple"])
def test_standard_list_and_tuple_candidate_collections_keep_exact_replay(plan_fixture, axis, base):
    f = plan_fixture
    revision = authored(f)
    selected = resource(f)
    contents = [] if axis.startswith("host_") else [selected]
    result = f[-1].publish(**candidate_request(f, revision, **_selection(axis, base(contents))))
    other = tuple if base is list else list
    assert f[-1].publish(**candidate_request(f, revision, **_selection(axis, other(contents)))) == result
    if axis == "entry_bundle":
        assert result.plan["entry_inputs"] == {"request": [content_schema_ref_payload(selected)]}
    elif axis == "owner_input_resources":
        assert result.plan[axis] == [content_schema_ref_payload(selected)]
    else:
        assert result.plan["host_inventory"] == {"resource_refs": [], "artifact_refs": []}
    assert_plan_only(f[0])
