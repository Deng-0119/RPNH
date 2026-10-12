"""Existing owner-authorized read session and official contribution Viewer."""
from datetime import datetime, timedelta, timezone

from cpn.frontend.dashboard import RegistryDashboard, exact, topology_digest
from cpn.frontend.user_contributions import UserContributionView
from cpn.rpnh.collaboration import SourceQualifiedVersionRef, SourceQualifiedResourceRef
from cpn.rpnh.collaboration.registry_typed_readers import TypedReaderCatalog
from cpn.rpnh.collaboration.registry_read_contracts import ReadSessionRequest, ExplicitSources, SourceSelection, ReadLimits
from cpn.rpnh.collaboration.registry_read_session import (
    RegistryReadHostBinding, ExistingReadAuthorityProvider, open_registry_session, open_readonly_source)
from cpn.rpnh.registry.observer_access import ObserverReadScope
from cpn.rpnh.registry.resources import ResourceVersionRef
from cpn.rpnh.registry.identities import TypedId


def role_presentation(run_dir, catalog):
    """Descriptions of existing bound roles, never an identity inventory."""
    from cpn.rpnh.inspection import project_registry_net
    net = project_registry_net(run_dir, catalog=catalog)
    return {"schema_version": "rpnh/presentation/v1", "topology_digest": topology_digest(net),
        "title": "Agent contract co-building", "description": "Native scripted protocol run; original Registry authority",
        "nodes": {
            "curator.run": {"name": "Curator", "description": "Read input; preserve source IDs; hand off normalized records."},
            "auditor.run": {"name": "Auditor", "description": "Reconcile source records; check totals; deliver the report."}}}


def open_view(owner, host, session, source_id):
    """Grant only this synthetic example's owner access to its existing records.

    This is an explicit HOST assembly. UserContributionView never mints authority.
    The exact material closure is limited to resources in this one fresh task.
    """
    core = owner._core
    typed = TypedReaderCatalog()
    fields = {kind: tuple(typed.fields(kind)) for kind in typed.entry_types}
    materials = tuple(SourceQualifiedResourceRef(source_id, ResourceVersionRef(
        TypedId.parse(r["logical_id"]), TypedId.parse(r["version_id"])))
        for r in core.event_store.object_rows_by_type("resource_version/v1"))
    purpose = "Inspect this synthetic agent contract collaboration"
    grant = host.issue_read_access(session, principal_ref=owner.principal_ref,
        scope=ObserverReadScope(fields, fields, materials), purpose=purpose,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        command_id="owner-contribution-read")

    def resolver(selected_source, access_path):
        if selected_source != source_id or access_path != "local":
            raise ValueError("Viewer source is outside this exact task")
        return open_readonly_source(core.run_dir, catalog=core.catalog)

    reader = open_registry_session(ReadSessionRequest(ExplicitSources((SourceSelection(
        SourceQualifiedVersionRef(source_id, owner.identity.task_ref), "local"),)), purpose),
        host=RegistryReadHostBinding("agent-cobuild-owner", resolver,
            ExistingReadAuthorityProvider({("agent-cobuild-owner", source_id, "local"): grant}), typed,
            limits=ReadLimits(session_seconds=3600)))
    try:
        view = UserContributionView(host, session, reader, source_id=source_id)
        dashboard = RegistryDashboard(core.run_dir, catalog=core.catalog, user_contribution_view=view,
                                      presentation=role_presentation(core.run_dir, core.catalog))
        return dashboard, reader
    except BaseException:
        reader.close()
        raise


def contribution_snapshot(provider, core):
    before = core.event_store.max_ordinal()
    frame = provider.dashboard()
    selected = {"net_ref": exact(frame["source"]["net_ref"]),
        "checkpoint_ref": exact(frame["net"]["marking"]["checkpoint_ref"]), "cut": frame["position"]["cursor"]}
    contribution = provider.user_contributions(**selected)
    if core.event_store.max_ordinal() != before:
        raise AssertionError("official Viewer read mutated Registry")
    if {n["node_id"] for n in contribution["nodes"]} != {n["id"] for n in frame["net"]["nodes"]}:
        raise AssertionError("contribution observation does not cover the actual complete PN")
    return frame, contribution


def main(argv=None):
    """Reopen the existing target read-only without rerunning or minting grants."""
    import argparse
    from pathlib import Path
    from cpn.frontend.server import serve_projection
    from .host import catalog, registration
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8775)
    args = parser.parse_args(argv)
    selected = catalog()
    registration().bind_schema_catalog(selected)
    run_dir = args.run_dir.resolve()
    dashboard = RegistryDashboard(run_dir, catalog=selected, presentation=role_presentation(run_dir, selected))
    serve_projection(dashboard, port=args.port, open_browser=False, show_resources=True)


if __name__ == "__main__":
    main()
