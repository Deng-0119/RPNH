"""Read-only direct-deposit/owner-adoption terminal composition.

Only original Registry facts are consumed. The returned material is neither
persisted proof nor authority to execute a historical net.
"""
from __future__ import annotations

from dataclasses import replace, asdict
import json

from .errors import ResourceIntegrityFault, TerminalReadUnsupported, TerminalReadStale
from .event_store import _CANONICAL_EVENT_SQL
from .models import VersionRef
from .module_runtime import _read_terminal_net_material
from .owner_mapping import token_state
from .publication import _ref_payload, _version_from_payload
from .schema_catalog import canonical_json


def assert_terminal_cut(core, cut):
    """Classify only directly observed cut/source/fence changes as stale."""
    if core.writer_epoch != core.event_store.writer_epoch or cut.writer_epoch != core.event_store.writer_epoch:
        raise TerminalReadStale("STALE_WRITER")
    if (core is not cut._core or core.task_id != cut.task_id
            or core.event_store.get_meta("native_run_ref") != cut.native_run_ref
            or core.event_store.max_ordinal() != cut.physical_head):
        raise TerminalReadStale("STALE_CUT")


def _unique_refs(values, label):
    if not isinstance(values, list):
        raise ResourceIntegrityFault(label + " is not an exact-ref array")
    refs = tuple(_version_from_payload(value) for value in values)
    if len(set(refs)) != len(refs):
        raise ResourceIntegrityFault(label + " repeats an exact ref")
    return refs


def _checkpoint(core, reads, ref, net_ref, root_ref):
    document = reads.metadata(ref, "marking_checkpoint/v1")
    if (document["marking_checkpoint_ref"] != _ref_payload(ref)
            or document["net_instance_ref"] != _ref_payload(net_ref)
            or document["team_design_root_ref"] != _ref_payload(root_ref)
            or document["settled"] is not True):
        raise ResourceIntegrityFault("terminal checkpoint lacks exact settled net/root/self")
    _unique_refs(document["token_refs"], "terminal checkpoint tokens")
    rows = reads.db.execute("SELECT e.* FROM events e WHERE e.task_id=? "
        "AND e.event_type='marking_checkpoint_committed/v1' AND "
        "json_extract(e.payload_json,'$.checkpoint_ref.version_id')=? AND "
        + _CANONICAL_EVENT_SQL, (str(core.task_id), str(ref.version_id))).fetchall()
    if len(rows) != 1:
        raise ResourceIntegrityFault("terminal checkpoint lacks one canonical commit")
    row = rows[0]
    reads.event(row)
    payload = json.loads(row["payload_json"])
    expected = {"checkpoint_ref": _ref_payload(ref), "net_instance_ref": _ref_payload(net_ref),
        "team_design_root_ref": _ref_payload(root_ref), "previous_checkpoint_ref": document["previous_checkpoint_ref"],
        "settlement_delta_ref": document["settlement_delta_ref"],
        "transition_firing_refs": document["transition_firing_refs"],
        "workspace_revision_refs": document["workspace_revision_refs"], "settled": True}
    # Existing schemas explicitly enumerate these bridge annotations.
    for field in ("owner_command_ref", "owner_command_result_ref", "reentry_source_checkpoint_ref",
            "reentry_authorization_ref", "reentry_superseded_terminal_evidence_ref", "reentry_generation",
            "reentry_token_mappings", "reentry_superseded_token_refs", "checkpoint_repair_ref",
            "repair_base_checkpoint_ref", "settlement_event_id"):
        if field in payload or field in document:
            expected[field] = document.get(field, payload.get(field) if field == "settlement_event_id" else None)
    if canonical_json(payload) != canonical_json(expected):
        raise ResourceIntegrityFault("terminal checkpoint commit differs from immutable checkpoint")
    object_row = reads.db.execute("SELECT transaction_id FROM objects WHERE version_id=?", (str(ref.version_id),)).fetchone()
    if object_row is None or object_row["transaction_id"] != row["transaction_id"]:
        raise ResourceIntegrityFault("terminal checkpoint is not atomic with its commit")
    return document, row


def _survives(core, reads, *, net, token_ref, start_ref, stop_ref):
    """Every ordinary successor must retain the very same unconsumed token."""
    cursor, visited = start_ref, set()
    last_ordinal = None
    token = reads.metadata(token_ref, "petri_token/v1")
    while True:
        if cursor in visited:
            raise ResourceIntegrityFault("terminal checkpoint lineage is cyclic")
        visited.add(cursor)
        checkpoint, commit = _checkpoint(core, reads, cursor, net.net_ref, net.team_design_root_ref)
        if (last_ordinal is not None and commit["ordinal"] >= last_ordinal):
            raise ResourceIntegrityFault("terminal checkpoint lineage is not strictly ordered")
        last_ordinal = commit["ordinal"]
        if (token["petri_token_ref"] != _ref_payload(token_ref)
                or token["net_instance_ref"] != _ref_payload(net.net_ref)
                or token["consumed_by"] is not None
                or token["epoch"] != checkpoint["epoch"]
                or _ref_payload(token_ref) not in checkpoint["token_refs"]):
            raise ResourceIntegrityFault("terminal exact carrier did not survive checkpoint lineage")
        if cursor == stop_ref:
            return
        if checkpoint["previous_checkpoint_ref"] is None:
            raise ResourceIntegrityFault("terminal checkpoint lineage misses its required predecessor")
        if checkpoint["settlement_delta_ref"] is None:
            # The complete original checkpoint/adoption verifier has already
            # proved these bridges. Do not reinterpret them as owner mappings.
            if any(field in checkpoint for field in ("reentry_source_checkpoint_ref", "checkpoint_repair_ref")):
                raise TerminalReadUnsupported("CHECKPOINT_COMPOSITION_UNSUPPORTED")
            raise ResourceIntegrityFault("terminal carrier has an unexplained checkpoint bridge")
        firings = _unique_refs(checkpoint["transition_firing_refs"], "terminal checkpoint firings")
        if len(firings) != 1:
            raise ResourceIntegrityFault("terminal ordinary checkpoint lacks one settlement firing")
        record = core.event_store.ordered_firing_record(firings[0].version_id, _db=reads.db)
        for key, field, kind in (("firing", "transition_firing_ref", "transition_firing/v1"),
                ("admission_checkpoint", "marking_checkpoint_ref", "marking_checkpoint/v1"),
                ("successor_checkpoint", "marking_checkpoint_ref", "marking_checkpoint/v1"),
                ("marking_delta", "marking_delta_ref", "marking_delta/v1"),
                ("firing_completion", "firing_completion_ref", "firing_completion/v2")):
            data = record[key]
            if canonical_json(reads.metadata(_version_from_payload(data[field]), kind)) != canonical_json(data):
                raise ResourceIntegrityFault("terminal settlement reconstruction differs from exact bytes")
        for event in record["events"]:
            row = reads.db.execute("SELECT * FROM events WHERE event_id=?", (str(event.event_id),)).fetchone()
            reads.event(row)
        delta = record["marking_delta"]
        if (record["state"] != "PUBLISHED"
                or record["successor_checkpoint"] != checkpoint
                or record["firing"]["net_instance_ref"] != _ref_payload(net.net_ref)
                or _ref_payload(token_ref) in delta["consumed_refs"]
                or _ref_payload(token_ref) in delta["deposited_refs"]):
            raise ResourceIntegrityFault("terminal ordinary settlement consumed or replaced exact carrier")
        predecessor_ref = _version_from_payload(checkpoint["previous_checkpoint_ref"])
        # ordered_firing_record checks pre - consumed + deposited = post using
        # the actual settlement predecessor, including serial sibling success.
        predecessor = reads.metadata(predecessor_ref, "marking_checkpoint/v1")
        if (predecessor["epoch"] != checkpoint["epoch"]
                or _ref_payload(token_ref) not in predecessor["token_refs"]):
            raise ResourceIntegrityFault("terminal carrier was absent before ordinary settlement")
        cursor = predecessor_ref


def resolve_adopted_terminal(core, kernel, *, current_executable, current_structure,
        marking, terminal, carrier, product, context, firing_ref, authority, cut, read_budget=None, _read_counts=None):
    """Return old immutable net/carrier/terminal after complete owner proof.

    The final boolean reports whether the fully verified hops preserve contract.
    """
    from ._event_store.adoption_reads import AdoptionPrefixReads
    from ._event_store.net_lineage import _verify_adoption_events, verified_checkpoint_head
    from ._terminal_contract import same_terminal_contract
    from .module_terminal import _producing_token, _unwrap_reentry_carrier
    assert_terminal_cut(core, cut)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        reads = AdoptionPrefixReads(core.event_store, core.catalog, db, core.task_id,
            terminal=True, max_objects=read_budget)
        rows = db.execute("SELECT e.* FROM events e WHERE e.task_id=? AND e.event_type='net_adopted/v1' "
            "AND " + _CANONICAL_EVENT_SQL + " ORDER BY e.task_control_sequence", (str(core.task_id),)).fetchall()
        lineage = _verify_adoption_events(core.event_store, core.catalog, rows, _db=db,
            offline=True, closure_memo={}, _prefix_reads=reads)
        if lineage[-1] != current_executable.net_ref or lineage.count(context.net_instance_ref) != 1:
            raise ResourceIntegrityFault("terminal producer is outside current exact adoption lineage")
        first = lineage.index(context.net_instance_ref)
        if first == len(lineage) - 1:
            raise ResourceIntegrityFault("cross-net terminal resolver received a current-net product")
        events = [core.event_store._row_to_envelope(row) for row in rows]
        materials = {}
        for ref in lineage[first:]:
            material = _read_terminal_net_material(core, reads, ref)
            root = reads.metadata(material[0].team_design_root_ref, "team_design_root/v1")
            if (root["task_ref"] != _ref_payload(context.task_ref)
                    or root["run_ref"] != authority["run_ref"]):
                raise ResourceIntegrityFault("terminal adopted nets cross exact task/run")
            materials[ref] = material
        # Verify entire committed proof before deciding a valid contract change.
        compatible = True
        cursor_ref, checkpoint_ref = carrier.token_ref, marking.checkpoint_ref
        selected = terminal
        for position in range(len(lineage) - 1, first, -1):
            event = events[position]
            payload = event.payload
            if "owner_command_ref" not in payload:
                raise TerminalReadUnsupported("ADOPTION_COMPOSITION_UNSUPPORTED")
            new_net, new_structure = materials[lineage[position]]
            old_net, old_structure = materials[lineage[position - 1]]
            # This verifies all original checkpoint bridges; the explicit walk
            # below adds exact selected-token survival through ordinary success.
            if verified_checkpoint_head(core.event_store, core.catalog, core.task_id,
                    new_net.net_ref, _db=db) != checkpoint_ref:
                raise ResourceIntegrityFault("terminal cursor is not the adopted net's latest checkpoint")
            candidate_ref = _version_from_payload(payload["owner_candidate_checkpoint_ref"])
            mapped_targets = tuple(_version_from_payload(value["new_token_ref"])
                for value in payload["token_mappings"])
            if cursor_ref not in mapped_targets:
                # A legal forward/reentry after adoption is outside this
                # direct-transfer composition, not a missing owner mapping.
                # Prove its original dedicated edge before classifying it.
                cursor_doc = reads.metadata(cursor_ref, "petri_token/v1")
                probe = replace(carrier, token_ref=cursor_ref, state=token_state(cursor_ref, cursor_doc))
                unwrapped_probe = _unwrap_reentry_carrier(core, kernel, probe)
                endpoint = f"{terminal.source.component}.{terminal.source.port}"
                probe_ports = [value for value in new_structure.compiled.ports if value.name == endpoint]
                if len(probe_ports) != 1:
                    raise ResourceIntegrityFault("terminal mixed carrier lacks its exact current port")
                reached = _producing_token(core, kernel, new_net, new_structure,
                    unwrapped_probe, product, probe_ports[0], firing_ref, _stop_refs=mapped_targets)
                if reached not in mapped_targets:
                    raise ResourceIntegrityFault("terminal mixed carrier does not reach a committed mapping")
                if unwrapped_probe.token_ref == cursor_ref:
                    creation = reads.publication(cursor_ref)
                    deposits = db.execute("SELECT firing_version_id FROM firing_publications WHERE state='PUBLISHED' "
                        "AND published_transaction_id=?", (creation["transaction_id"],)).fetchall()
                    if len(deposits) != 1:
                        raise ResourceIntegrityFault("terminal forward carrier lacks its actual deposit")
                    deposit_record = core.event_store.ordered_firing_record(deposits[0]["firing_version_id"], _db=db)
                    _survives(core, reads, net=new_net, token_ref=cursor_ref, start_ref=checkpoint_ref,
                        stop_ref=_version_from_payload(deposit_record["successor_checkpoint"]["marking_checkpoint_ref"]))
                raise TerminalReadUnsupported("CARRIER_COMPOSITION_UNSUPPORTED")
            _survives(core, reads, net=new_net, token_ref=cursor_ref,
                start_ref=checkpoint_ref, stop_ref=candidate_ref)
            matches = [m for m in payload["token_mappings"] if m["new_token_ref"] == _ref_payload(cursor_ref)]
            if len(matches) != 1:
                raise ResourceIntegrityFault("terminal carrier lacks exactly one committed owner mapping")
            source_ref = _version_from_payload(matches[0]["old_token_ref"])
            source_doc = reads.metadata(source_ref, "petri_token/v1")
            old_terminals = (old_structure.compiled.source.terminal, *old_structure.compiled.source.terminal_alternatives)
            exact_terminals = [value for value in old_terminals
                if selected is not None and canonical_json(asdict(value)) == canonical_json(asdict(selected))]
            structural_terminals = [value for value in old_terminals
                if selected is not None and value.key == selected.key and value.source == selected.source
                and value.operation == selected.operation and value.outcome == selected.outcome]
            matches_terminal = exact_terminals or structural_terminals
            old_terminal = matches_terminal[0] if len(matches_terminal) == 1 else None
            compatible = same_terminal_contract(core, kernel, old_net, old_structure,
                new_net, new_structure, old_terminal, selected, context.own_transition_id
                if hasattr(context, "own_transition_id") else reads.metadata(firing_ref, "transition_firing/v1")["transition_id"],
                _db=db, _prefix_reads=reads) and compatible
            if old_terminal is not None:
                endpoint = f"{old_terminal.source.component}.{old_terminal.source.port}"
                old_port = next(item for item in old_structure.compiled.ports if item.name == endpoint)
                if source_doc["place"] != old_port.place:
                    compatible = False
            selected = old_terminal
            cursor_ref = source_ref
            checkpoint_ref = _version_from_payload(payload["owner_predecessor_checkpoint_ref"])
        producer_net, producer_structure = materials[context.net_instance_ref]
        token_doc = reads.metadata(cursor_ref, "petri_token/v1")
        old_carrier = replace(carrier, token_ref=cursor_ref, state=token_state(cursor_ref, token_doc))
        # Prove the real direct deposit even when a valid later contract changed.
        output_ref = _version_from_payload(kernel._prepared(product).metadata["origin"]["primary_ref"])
        output = reads.metadata(output_ref, "output_binding/v1")
        ports = [port for port in producer_structure.compiled.ports if port.port_id == output["output_port_id"]]
        if len(ports) != 1:
            raise ResourceIntegrityFault("terminal original product lacks one exact producing port")
        port = ports[0]
        unwrapped = _unwrap_reentry_carrier(core, kernel, old_carrier)
        producing_ref = _producing_token(core, kernel, producer_net, producer_structure,
            unwrapped, product, port, firing_ref)
        if unwrapped.token_ref != cursor_ref or producing_ref != cursor_ref:
            raise TerminalReadUnsupported("CARRIER_COMPOSITION_UNSUPPORTED")
        record = core.event_store.ordered_firing_record(firing_ref.version_id, _db=db)
        stop = _version_from_payload(record["successor_checkpoint"]["marking_checkpoint_ref"])
        _survives(core, reads, net=producer_net, token_ref=cursor_ref,
            start_ref=checkpoint_ref, stop_ref=stop)
        assert_terminal_cut(core, cut)
        if _read_counts is not None:
            _read_counts.append(len(reads._terminal_prepared))
        return producer_net, producer_structure, old_carrier, compatible
