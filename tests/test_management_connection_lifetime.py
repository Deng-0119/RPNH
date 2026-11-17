"""Connection ownership only; real SQLite, no socket or authority substitution."""
import sqlite3
import pytest
from test_local_registry_browser import make_run, publish
from cpn.rpnh import material_availability as availability
from cpn.rpnh import material_body_policy as body

@pytest.mark.parametrize('module,function,predicate',[
 (availability,'source_governs_materials','source_governs_materials_at'),
 (body,'resolve_material_body_availability','resolve_body_availability')])
@pytest.mark.parametrize('failure',[False,True])
def test_owned_material_read_connection_closed(tmp_path,monkeypatch,module,function,predicate,failure):
    core,*_=make_run(tmp_path/'run')
    resource=publish((core,*_))
    real=core.event_store.connect
    opened=[]
    def connect():
        db=real();opened.append(db);return db
    monkeypatch.setattr(core.event_store,'connect',connect)
    if failure:
        def fail(*args,**kwargs):raise RuntimeError('injected original read failure')
        monkeypatch.setattr(module,predicate,fail)
    args=(core,) if function=='source_governs_materials' else (core,resource)
    if failure:
        with pytest.raises(RuntimeError,match='original read failure'):getattr(module,function)(*args)
    else:getattr(module,function)(*args)
    assert opened
    for db in opened:
        with pytest.raises(sqlite3.ProgrammingError,match='closed'):db.execute('SELECT 1')

@pytest.mark.parametrize('failure',[False,True])
def test_original_material_authorization_connection_closed(monkeypatch,failure):
    import inspect
    from test_material_execution_unittest import NativeMaterialExecutionTests
    from cpn.rpnh.material_execution import run_material_operation
    from cpn.rpnh.registry._event_store import material_control
    from cpn.rpnh.registry.event_store import RegistryConflict
    helper=NativeMaterialExecutionTests()
    helper.setUp()
    try:
        world=helper.world()
        _,_,execution=helper.start(world)
        store=world.owner._core.event_store
        real=store.connect;opened=[]
        def connect():
            db=real()
            frames=inspect.stack()
            caller=frames[3] if frames[1].function=='connection_scope' else frames[1]
            if caller.function=='_authorize_resource' and caller.filename.endswith('/references.py'):opened.append(db)
            return db
        monkeypatch.setattr(store,'connect',connect)
        if failure:
            def fail(*args,**kwargs):raise RegistryConflict('injected material consumer rejection')
            monkeypatch.setattr(material_control,'validate_material_input_consumer',fail)
            with pytest.raises(Exception,match='injected material consumer rejection'):
                run_material_operation(world.owner,execution)
        else:run_material_operation(world.owner,execution)
        assert opened
        for db in opened:
            with pytest.raises(sqlite3.ProgrammingError,match='closed'):db.execute('SELECT 1')
    finally:helper.doCleanups()
