import json
import sys
from dataclasses import replace
import pytest
from cpn.rpnh import public_material_contracts as w
from cpn.rpnh.public_module_materials import PublicHostSelection
from cpn.rpnh.collaboration.environment_host import load_host_profile
from cpn.rpnh.registry.schema_catalog import canonical_json
from registered_material_fixtures import make_layout


def selection(setup):return PublicHostSelection(setup.entrypoint.name,b'',(('configuration',w.canonical(setup.request['public_configuration'])),),canonical_json(setup.module.to_dict()),b'{}')

@pytest.mark.parametrize('damage',['missing_manifest','ambiguous','unknown_abi','wrong_profile','missing_unit','changed_asset','missing_metadata_asset','id_newline'])
def test_preflight_failure_precedes_factory(tmp_path,monkeypatch,damage):
    setup=make_layout(tmp_path/'layout',monkeypatch,'files');document=setup.document
    if damage=='missing_manifest':setup.manifest.unlink()
    elif damage=='ambiguous':
        import importlib.metadata
        monkeypatch.setattr(importlib.metadata,'entry_points',lambda **kwargs:[setup.entrypoint,setup.entrypoint])
    elif damage=='changed_asset':setup.boundary.write_bytes(b'changed bytes')
    else:
        contract=document['profiles'][0]['contract']
        if damage=='unknown_abi':contract['preparation_abi']='unknown/v1'
        elif damage=='wrong_profile':contract['profile_id']='other-profile/v1'
        elif damage=='missing_unit':contract['implementation_units'][0]['dependency_unit_ids']=['missing']
        elif damage=='id_newline':contract['contract_id']+='\n'
        else:
            target=next(a for a in document['profiles'][0]['assets'] if a['member']=='rpnh_public_materials.json')
            document['profiles'][0]['assets'].remove(target)
            for unit in contract['implementation_units']:
                if target['asset_id'] in unit['asset_ids']:unit['asset_ids'].remove(target['asset_id'])
        setup.manifest.write_bytes(w.canonical(document))
    with pytest.raises(Exception):load_host_profile(setup.entrypoint.name,None,public_selection=selection(setup))
    assert setup.loads==[]


def test_selected_files_does_not_observe_unused_numpy(tmp_path,monkeypatch):
    setup=make_layout(tmp_path/'layout',monkeypatch,'files')
    from registered_material_fixtures import LayoutDistribution
    import importlib.metadata
    original=importlib.metadata.distribution
    def selected(name):
        if name=='numpy':raise AssertionError('unused numerical implementation was observed')
        return original(name)
    monkeypatch.setattr(importlib.metadata,'distribution',selected)
    profile=load_host_profile(setup.entrypoint.name,None,public_selection=selection(setup))
    assert profile.profile_id=='rpnh-neutral-fixture/v1'
    assert len(setup.loads)==1
    assert 'numpy' not in sys.modules


def test_factory_drift_is_caught_before_return(tmp_path,monkeypatch):
    from registered_material_fixtures import neutral
    setup=make_layout(tmp_path/'layout',monkeypatch,'files')
    def factory(value):
        result=neutral.profile(value);setup.boundary.write_bytes(b'factory changed public asset');return result
    setup.entrypoint.load=lambda:factory
    with pytest.raises(Exception,match='CHANGED'):load_host_profile(setup.entrypoint.name,None,public_selection=selection(setup))


def test_unknown_host_callback_is_not_silently_carried(tmp_path,monkeypatch):
    from registered_material_fixtures import neutral
    setup=make_layout(tmp_path/'layout',monkeypatch,'files')
    setup.entrypoint.load=lambda:lambda value:replace(neutral.profile(value),before_dispatch=lambda:None)
    with pytest.raises(Exception,match='UNDECLARED_HOST_CALLBACK'):load_host_profile(setup.entrypoint.name,None,public_selection=selection(setup))


def test_public_profile_never_calls_legacy_private_configuration(tmp_path,monkeypatch):
    from registered_material_fixtures import neutral
    setup=make_layout(tmp_path/'layout',monkeypatch,'files')
    def forbidden(*a,**k):raise AssertionError('legacy private configuration or business function called')
    monkeypatch.setattr(neutral,'_configuration',forbidden)
    profile=load_host_profile(setup.entrypoint.name,None,public_selection=selection(setup))
    assert profile.registration_factory().declarations()
    assert 'numpy' not in sys.modules
