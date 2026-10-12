"""Local Registry bootstrap/path helpers, independent of the approval application."""
import json
from pathlib import Path
from cpn.rpnh.collaboration import user_collaboration_schema_data
from cpn.rpnh.registry._registry import _RegistryCore
from cpn.rpnh.registry.bootstrap import NativeBootstrapManifest, _bootstrap_identity
from cpn.rpnh.registry.identities import new_id
from cpn.rpnh.registry.models import VersionRef
from cpn.rpnh.registry.publication import _version_from_payload
from cpn.rpnh.registry.registration_gateway import RegistryRegistrationGateway
from cpn.rpnh.registry.schema_catalog import SchemaCatalog, canonical_json


def catalog():
    schemas, types, paths = user_collaboration_schema_data()
    return SchemaCatalog(schemas=schemas, types=types, schema_paths=paths)


def safe_destination(path, workspace):
    """Validate the caller's explicit write boundary, including existing descendants."""
    root = Path(workspace).resolve(strict=True)
    path = Path(path).absolute()
    if not path.resolve().is_relative_to(root):
        raise ValueError("destination escapes the declared workspace")
    for item in (path, *path.parents):
        if item == root.parent:
            break
        if item.is_symlink():
            raise ValueError("runtime path must not traverse a symlink")
    if path.exists():
        for item in (path, *path.rglob("*")):
            if item.is_symlink() or not item.resolve().is_relative_to(root):
                raise ValueError("runtime tree contains a symlink")
            if item.is_file() and item.stat().st_nlink != 1:
                raise ValueError("runtime tree contains a hard link")
    return path


def personal(path, label, *, reopen, read_only=False):
    core = _RegistryCore(path, create=not reopen, read_only=read_only, catalog=catalog())
    if reopen:
        rows = core.event_store.object_rows_by_type("principal/v1")
        if len(rows) != 1:
            raise ValueError("personal Registry must contain exactly its original principal")
        row = rows[0]
        return core, _version_from_payload({"entity_type": "principal/v1",
                                           "logical_id": row["logical_id"], "version_id": row["version_id"]})
    identity = _bootstrap_identity(core, NativeBootstrapManifest(("rpnh/module_declaration/v1",)))
    bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
    gateway = RegistryRegistrationGateway(core, identity.task_ref, bootstrap)
    gateway.bind_source_identity(source_id="a2a-personal-" + label, command_id="personal-source")
    principal = VersionRef("principal/v1", new_id("principal"), new_id("principal_version"))
    data = {"principal_id": str(principal.entity_id), "principal_version_id": str(principal.version_id),
            "display_name": "Controlled " + label}
    core.publish_bytes(object_type=principal.entity_type, logical_id=principal.entity_id,
        version_id=principal.version_id, payload=canonical_json(data), metadata=data,
        media_type="application/json", schema_ref="registry_v1/principal/v1", idempotency_key="personal-principal")
    return core, principal


