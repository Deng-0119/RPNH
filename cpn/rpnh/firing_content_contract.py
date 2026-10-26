"""Finite HOST firing content contract, over the original Registry only.

Checker ABI: checker(*, snapshot, evidence) -> None. Reject by raising
FiringContentContractError. No callback return value grants authority.

configuration_sources(owner)'s immutable JSON contains
firing_content_contracts: {binding_key: {resource_id, resource_version_id}}.
That resource is JSON with protocol='firing_content_contract/v1', run_ref,
net_ref, operation_binding_ref (or operation_binding_refs), checker (the full
Registration tool declaration), sources, products, required_reads, refinement.
sources maps role to {resource_ref, sha256, decoded_sha256?}; products maps
qualified symbolic port name to registered schema ID. Domain fields remain
ordinary data interpreted by the explicitly registered trusted checker.

preflight_candidate(core, execution, *, products: Mapping[str, bytes],
                    selected_outcome_id: str) -> Mapping
seal_candidate(core, execution, *, products: Mapping[str, bytes],
               selected_outcome_id: str, idempotency_key: str)
    -> RegisteredOperationOutputsAuthority

evidence.db exposes SELECT on the SAME validating SQLite transaction.
evidence.read_resource(ref, max_bytes=None) returns registered bytes (or None
when the bound is exceeded). evidence.read_object(ref) returns metadata.
evidence.events(*, event_types=()) and objects(*, object_type) return original
facts at acceptance_cut. Snapshot refs are original JSON refs, products and
sources include payload bytes and sha256. Preflight product refs are None.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType

from jsonschema import Draft7Validator

from .registry.errors import ResourceIntegrityFault
from .registry.identities import TypedId
from .registry.publication import _ref_payload, _resource_from_payload, _version_from_payload
from .registry.schema_catalog import canonical_json

PROTOCOL = "firing_content_contract/v1"
CHECKER_PROTOCOL = "firing_content_checker/v1"


class FiringContentContractError(ResourceIntegrityFault):
    """A correctable candidate or exact content closure was rejected."""


def _fail(message):
    raise FiringContentContractError("firing content contract: " + message)


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _plain(value):
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def validate_marker(operation, registration):
    if "firing_content_contract" not in operation.config:
        return
    marker = operation.config["firing_content_contract"]
    if (not isinstance(marker, Mapping) or set(marker) != {
            "protocol", "binding_key", "checker_key", "success_outcomes"}
            or marker["protocol"] != PROTOCOL
            or any(not isinstance(marker[k], str) or not marker[k]
                   for k in ("binding_key", "checker_key"))
            or not isinstance(marker["success_outcomes"], (list, tuple))
            or not marker["success_outcomes"]
            or len(set(marker["success_outcomes"])) != len(marker["success_outcomes"])
            or set(marker["success_outcomes"]) - {o.name for o in operation.outcomes}):
        _fail("invalid declaration marker")
    checker = registration.declaration("tool", marker["checker_key"])
    registration.resolve("tool", marker["checker_key"])
    if (checker["contracts"].get("protocol") != CHECKER_PROTOCOL
            or marker["checker_key"] in operation.tools):
        _fail("checker must be a registered HOST-only firing_content_checker/v1")


class _ReadSQL:
    __slots__ = ("__db",)

    def __init__(self, db):
        self.__db = db

    def execute(self, sql, parameters=()):
        if not sql.lstrip().upper().startswith("SELECT "):
            _fail("evidence view permits SELECT only")
        # Return rows without exposing a cursor's writable connection.
        return _Rows(self.__db.execute(sql, parameters).fetchall())


class _Rows:
    def __init__(self, rows):
        self.rows = iter(rows)

    def fetchone(self):
        return next(self.rows, None)

    def fetchall(self):
        return list(self.rows)

    def __iter__(self):
        return self.rows


def _version(ref):
    if hasattr(ref, "as_version_ref"):
        return _ref_payload(ref.as_version_ref())
    if hasattr(ref, "entity_type"):
        return _ref_payload(ref)
    if "resource_id" in ref:
        return {"entity_type": "resource_version/v1", "logical_id": ref["resource_id"],
                "version_id": ref["resource_version_id"]}
    return dict(ref)


def _resource(ref):
    ref = _version(ref)
    return {"resource_id": ref["logical_id"], "resource_version_id": ref["version_id"]}


class FiringContentEvidence:
    """Read-only original facts; no report-supplied acceptance or coverage."""
    def __init__(self, store, db, cut):
        self.__store = store
        self.db = _ReadSQL(db)
        self.acceptance_cut = cut

    def _row(self, ref):
        ref = _version(ref)
        row = self.db.execute(
            "SELECT o.*,e.ordinal FROM objects o JOIN events e ON "
            "e.event_id=o.published_event_id WHERE o.version_id=? AND e.ordinal<=?",
            (ref["version_id"], self.acceptance_cut)).fetchone()
        if (row is None or row["logical_id"] != ref["logical_id"]
                or row["object_type"] != ref["entity_type"]):
            _fail("exact object is absent from the acceptance cut")
        return row

    def read_object(self, ref):
        return _freeze(json.loads(self._row(ref)["metadata_json"]))

    def read_resource(self, ref, max_bytes=None):
        ref = _version(ref)
        if ref["entity_type"] != "resource_version/v1":
            _fail("resource reader requires a resource reference")
        row = self._row(ref)
        if max_bytes is not None and row["size"] > max_bytes:
            return None
        return self._payload(row)

    def _payload(self, row):
        version = TypedId.parse(row["version_id"])
        if row["storage_locator"] != "registry-object:" + str(version):
            _fail("registered object locator is not exact")
        raw = (self.__store.path.parent / "objects" / version.kind / version.value).read_bytes()
        if len(raw) != row["size"]:
            _fail("registered object bytes differ from their envelope")
        return raw

    def events(self, *, event_types=()):
        rows = self.db.execute("SELECT * FROM events WHERE ordinal<=? ORDER BY ordinal",
                               (self.acceptance_cut,)).fetchall()
        return tuple(_freeze({**{k: row[k] for k in row.keys() if k != "payload_json"},
                              "payload": json.loads(row["payload_json"])})
                     for row in rows if not event_types or row["event_type"] in event_types)

    def objects(self, *, object_type):
        return tuple(_freeze(json.loads(row["metadata_json"])) for row in self.db.execute(
            "SELECT o.metadata_json FROM objects o JOIN events e ON e.event_id="
            "o.published_event_id WHERE o.object_type=? AND e.ordinal<=? ORDER BY e.ordinal",
            (object_type, self.acceptance_cut)).fetchall())


def _load(evidence, invocation_ref, outcome=None):
    from .executable_net import load_compiled_net
    from .registry._operation import registration as registrations
    invocation = _plain(evidence.read_object(invocation_ref))
    firing_ref = invocation["own_transition_firing_ref"]
    firing = _plain(evidence.read_object(firing_ref))
    net_ref = firing["net_instance_ref"]
    net = evidence.read_object(net_ref)
    compiled_ref = _plain(net["team_net_declaration_resource_ref"])
    compiled = load_compiled_net(json.loads(evidence.read_resource(compiled_ref)))
    transition = next(t for t in compiled.symbolic.transitions if t.name == firing["transition_id"])
    operation = next(o.declaration for o in compiled.operations if o.declaration.name == transition.operation)
    marker = operation.config.get("firing_content_contract")
    if marker is None:
        return None
    if outcome is not None and outcome not in marker["success_outcomes"]:
        # Original interruptions do not depend on content, reads or checker PASS.
        return ({"invocation_ref": invocation_ref, "selected_outcome_id": outcome},
                compiled, operation, marker, None)
    registration = registrations._OPERATION_REGISTRATION
    if registration is None:
        _fail("trusted checker inventory is not bound")
    validate_marker(operation, registration)
    checker = registration.declaration("tool", marker["checker_key"])
    if compiled.registrations.get("tool", {}).get(marker["checker_key"]) != checker:
        _fail("checker differs from compiled registration")
    targets = {}
    for row in evidence.db.execute(
            "SELECT r.source_json,r.target_json,r.metadata_json FROM relations r "
            "JOIN objects o ON o.version_id=json_extract(r.source_json,'$.version_id') "
            "JOIN events e ON e.event_id=o.published_event_id "
            "WHERE o.object_type='run_execution_authority/v1' AND e.ordinal<=?",
            (evidence.acceptance_cut,)).fetchall():
        if json.loads(row["metadata_json"]).get("authority_role") == "immutable_genesis":
            target = json.loads(row["target_json"])
            target["logical_id"] = target.pop("entity_id")
            targets[target["version_id"]] = target
    if len(targets) != 1:
        _fail("one immutable genesis configuration is required")
    config_ref = next(iter(targets.values()))
    config = json.loads(evidence.read_resource(config_ref))
    contract_ref = config.get("firing_content_contracts", {}).get(marker["binding_key"])
    if not isinstance(contract_ref, dict):
        _fail("declared contract binding is missing")
    contract_raw = evidence.read_resource(contract_ref)
    contract = json.loads(contract_raw)
    required = {"protocol", "run_ref", "net_ref", "checker", "sources", "products", "required_reads", "refinement"}
    if not isinstance(contract, dict) or not required <= contract.keys():
        _fail("contract fields are incomplete")
    run = evidence.objects(object_type="native_run_identity/v1")
    if len(run) != 1:
        _fail("run identity is not unique")
    run_ref = {"entity_type": "native_run_identity/v1", "logical_id": run[0]["run_id"],
               "version_id": run[0]["run_version_id"]}
    binding = invocation["operation_binding_ref"]
    allowed = contract.get("operation_binding_refs", [contract.get("operation_binding_ref")])
    if (contract["protocol"] != PROTOCOL or contract["run_ref"] != run_ref
            or contract["net_ref"] != net_ref or binding not in allowed
            or contract["checker"] != checker):
        _fail("contract run/net/operation/checker binding differs")
    if "compiled_ref" in contract and _version(contract["compiled_ref"]) != _version(compiled_ref):
        _fail("contract compiled declaration binding differs")
    if evidence.read_object(contract_ref)["task_ref"] != invocation["task_ref"]:
        _fail("contract belongs to another task")
    starts = [event for event in evidence.events(event_types=("operation_execution_started/v1",))
              if event["payload"].get("invocation_ref") == invocation_ref]
    if len(starts) != 1:
        _fail("one original operation Start is required")
    sources = {}
    if not isinstance(contract["sources"], dict) or not isinstance(contract["products"], dict):
        _fail("sources and products must be mappings")
    for role, source in contract["sources"].items():
        raw = evidence.read_resource(source["resource_ref"])
        if (hashlib.sha256(raw).hexdigest() != source["sha256"]
                or evidence.read_object(source["resource_ref"])["task_ref"] != invocation["task_ref"]):
            _fail("SOURCE_BINDING_MISMATCH: source bytes or task differs: " + role)
        if "decoded_sha256" in source:
            decoded = json.loads(raw)
            if not isinstance(decoded, str) or hashlib.sha256(decoded.encode()).hexdigest() != source["decoded_sha256"]:
                _fail("decoded source bytes differ: " + role)
        sources[role] = {**source, "payload": raw}
    snapshot = {"contract_ref": contract_ref, "contract_sha256": hashlib.sha256(contract_raw).hexdigest(),
        "contract": contract, "checker": checker, "run_ref": run_ref, "net_ref": net_ref,
        "compiled_ref": compiled_ref, "configuration_ref": _resource(config_ref),
        "operation_binding_ref": binding, "invocation_ref": invocation_ref,
        "transition_firing_ref": firing_ref, "start_event_id": starts[0]["event_id"],
        "acceptance_cut": evidence.acceptance_cut, "sources": sources}
    return snapshot, compiled, operation, marker, registration.resolve("tool", marker["checker_key"])


def _check(evidence, invocation_ref, outcome, products, *, refs=None, check_content=True):
    loaded = _load(evidence, invocation_ref, outcome)
    if loaded is None:
        return None
    snapshot, compiled, operation, marker, checker = loaded
    selected = next((o for o in operation.outcomes if o.name == outcome), None)
    if selected is None:
        _fail("undeclared selected outcome")
    ports = {p.name: p for p in compiled.ports}
    quantities = {p.port: p for p in selected.products}
    if set(products) != set(quantities) or any(q.minimum != 1 or q.maximum != 1 for q in quantities.values()):
        _fail("MISSING_REQUIRED_PRODUCT: candidate must contain the exact singleton product bundle")
    bound = {}
    for name, payload in products.items():
        if not isinstance(payload, bytes):
            _fail("candidate products must be bytes")
        try:
            schema = compiled.registrations["schema"][ports[name].schema]["schema"]
            Draft7Validator(schema).validate(json.loads(payload))
        except Exception as exc:
            raise FiringContentContractError("candidate schema rejected: " + name) from exc
        bound[name] = {"payload": payload, "sha256": hashlib.sha256(payload).hexdigest(),
                       "resource_ref": None if refs is None else refs[name], "schema_id": ports[name].schema}
    snapshot.update(selected_outcome_id=outcome, products=bound)
    if outcome not in marker["success_outcomes"]:
        return snapshot
    if snapshot["contract"]["products"] != {n: ports[n].schema for n in quantities}:
        _fail("contract product schema/port bundle differs")
    if not check_content:
        return snapshot
    result = checker(snapshot=_freeze(snapshot), evidence=evidence)
    if result is not None:
        _fail("UNTRUSTED_CHECK_EVIDENCE: trusted checker must return None, never a supplied verdict")
    return snapshot


def _without_bytes(snapshot):
    value = _plain(snapshot)
    for field in ("sources", "products"):
        for item in value[field].values():
            item.pop("payload", None)
    return value


def preflight_candidate(core, execution, *, products, selected_outcome_id):
    from .run import _operation_services
    from .registry.operation_execution import verify_operation_execution
    kernel, repository, _ = _operation_services(core)
    execution = verify_operation_execution(core, kernel, repository, execution)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        cut = db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0]
        snapshot = _check(FiringContentEvidence(core.event_store, db, cut),
            _ref_payload(execution.operation.canonical.context.invocation_ref),
            selected_outcome_id, products)
    if snapshot is None:
        _fail("candidate API requires a bound operation")
    return _freeze(snapshot)


def _checks(evidence, invocation_ref):
    result = []
    for metadata in evidence.objects(object_type="resource_version/v1"):
        if (metadata.get("descriptors", {}).get("firing_content_check") == "v1"
                and metadata.get("reference_provenance", {}).get("producer_invocation_ref") == invocation_ref):
            ref = {"resource_id": metadata["resource_id"], "resource_version_id": metadata["resource_version_id"]}
            result.append((ref, json.loads(evidence.read_resource(ref))))
    return result


def _has_products(evidence, invocation_ref):
    return any(
        meta.get("origin_kind") == "petri_output"
        and meta.get("producer_ref") == invocation_ref
        for meta in evidence.objects(object_type="resource_version/v1"))


def _material(evidence, ref, pending=()):
    exact = _version(ref)
    for item in pending:
        if str(item.version_id) == exact["version_id"]:
            if str(item.logical_id) != exact["logical_id"] or item.object_type != exact["entity_type"]:
                _fail("pending resource reference differs")
            raw = evidence._payload({"version_id": str(item.version_id), "storage_locator": item.storage_locator,
                                     "size": item.size})
            return dict(item.metadata), raw
    return _plain(evidence.read_object(ref)), evidence.read_resource(ref)


def _output_material(evidence, invocation_ref, outcome, refs, pending=()):
    loaded = _load(evidence, invocation_ref, outcome)
    if loaded is None:
        return None
    snapshot, compiled, operation, marker, checker = loaded
    invocation = evidence.read_object(invocation_ref)
    binding = evidence.read_object(invocation["operation_binding_ref"])
    output_bindings = {_plain(r)["version_id"]: _plain(r) for r in binding["output_binding_refs"]}
    names = {p.port_id: p.name for p in compiled.ports}
    products, named_refs = {}, {}
    for ref in refs:
        metadata, raw = _material(evidence, ref, pending)
        origin = metadata.get("origin", {})
        primary = origin.get("primary_ref", {})
        declared_binding = output_bindings.get(primary.get("version_id"))
        if (origin.get("kind") != "petri_output" or primary != declared_binding
                or metadata.get("producer_ref") != invocation_ref
                or origin.get("secondary_ref") != invocation["activation_ref"]):
            _fail("output origin crosses the exact invocation/binding")
        output_binding = evidence.read_object(declared_binding)
        port = output_binding["output_port_id"]
        declared_port = next(p for p in compiled.ports if p.port_id == port)
        if (metadata.get("content_schema_ref") != declared_port.schema
                or metadata.get("content_schema_authority_ref")
                != _plain(output_binding["content_schema_ref"])):
            _fail("OUTPUT_SCHEMA_BINDING_MISMATCH: actual product schema differs from its output binding")
        descriptors = metadata.get("descriptors", {})
        name = names[port]
        if (name in products or descriptors.get("output_port_id") != port
                or descriptors.get("output_outcome_id") != outcome):
            _fail("duplicate port or different selected outcome")
        products[name], named_refs[name] = raw, _resource(ref)
    # Even interrupted closures retain their exact compiled product bundle.
    checked = _check(evidence, invocation_ref, outcome, products, refs=named_refs, check_content=False)
    return checked, products, named_refs, marker


def _verify_record(store, db, head, record, refs, *, pending=()):
    if not isinstance(record, dict) or record.get("protocol") != "firing_content_check/v1":
        _fail("check resource is not the finite protocol")
    saved = record["snapshot"]
    cut = saved.get("acceptance_cut")
    if type(cut) is not int or not 0 <= cut <= head:
        _fail("acceptance cut is outside original facts")
    # Products may be newer than the frozen read-evidence cut. Read their exact
    # bytes from the current proposal, while the checker sees only original cut.
    current = FiringContentEvidence(store, db, head)
    material = _output_material(current, saved["invocation_ref"],
                                saved["selected_outcome_id"], refs, pending)
    if material is None:
        _fail("check resource belongs to an unbound operation")
    _, products, named_refs, _ = material
    accepted = _check(FiringContentEvidence(store, db, cut), saved["invocation_ref"],
                      saved["selected_outcome_id"], products, refs=named_refs)
    if _without_bytes(accepted) != saved:
        _fail("CHECK_BINDING_MISMATCH: check resource differs from exact accepted contract/products/evidence")
    return accepted


def verify_firing_content_check(core, check_ref):
    """Recheck ordinary immutable acceptance evidence; this does NOT prove Success."""
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        head = db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0]
        evidence = FiringContentEvidence(core.event_store, db, head)
        meta = evidence.read_object(check_ref)
        record = json.loads(evidence.read_resource(check_ref))
        saved = record["snapshot"]
        if (meta.get("descriptors", {}).get("firing_content_check") != "v1"
                or meta.get("reference_provenance", {}).get("producer_invocation_ref") != saved["invocation_ref"]):
            _fail("check resource belongs to another invocation")
        refs = [p["resource_ref"] for p in saved["products"].values()]
        _verify_record(core.event_store, db, head, record, refs)
        return _freeze(record)


def verify_firing_content_outputs(core, execution, outputs, outcome):
    """Shared ordinary/recovery output closure, independent of fresh I/O leases."""
    invocation_ref = _ref_payload(execution.operation.canonical.context.invocation_ref)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        head = db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0]
        evidence = FiringContentEvidence(core.event_store, db, head)
        loaded = _load(evidence, invocation_ref, outcome)
        if loaded is None or outcome not in loaded[3]["success_outcomes"]:
            return
        checks = _checks(evidence, invocation_ref)
        if len(checks) != 1:
            _fail("success requires one original atomic content check")
        record = checks[0][1]
        if record["snapshot"]["selected_outcome_id"] != outcome:
            _fail("accepted outcome differs from closure")
        _verify_record(core.event_store, db, head, record, [o.resource_ref for o in outputs])


def get_firing_content_check(core, execution):
    """Return (exact ordinary check resource ref, reverified stored snapshot)."""
    invocation_ref = _ref_payload(execution.operation.canonical.context.invocation_ref)
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        head = db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0]
        evidence = FiringContentEvidence(core.event_store, db, head)
        checks = _checks(evidence, invocation_ref)
        if len(checks) != 1:
            _fail("one original content check is required")
        ref, record = checks[0]
        _verify_record(core.event_store, db, head, record,
                       [p["resource_ref"] for p in record["snapshot"]["products"].values()])
        return _resource_from_payload(ref), _freeze(record["snapshot"])


def seal_candidate(core, execution, *, products, selected_outcome_id, idempotency_key):
    """Publish the entire content bundle and exact check in one native transaction.

    An identical command after a crash reads the already-committed whole bundle.
    A different command or bytes cannot replace a prior acceptance.
    """
    from .run import _operation_services
    from .registry.operation_execution import verify_operation_execution
    from .registry.operations import register_operation_outputs
    from .registry.resource_verification import verify_resource
    from .registry.resources import PetriOutputOrigin, PrivateSystemOrigin, PublishResource
    kernel, repository, _ = _operation_services(core)
    execution = verify_operation_execution(core, kernel, repository, execution)
    context = execution.operation.canonical.context
    invocation_ref = _ref_payload(context.invocation_ref)
    if not isinstance(idempotency_key, str) or not idempotency_key:
        _fail("seal requires an original command identity")
    with core.event_store.connect() as db:
        db.execute("BEGIN")
        cut = db.execute("SELECT COALESCE(MAX(ordinal),0) FROM events").fetchone()[0]
        evidence = FiringContentEvidence(core.event_store, db, cut)
        loaded = _load(evidence, invocation_ref, selected_outcome_id)
        if loaded is None or selected_outcome_id not in loaded[3]["success_outcomes"]:
            _fail("seal API requires a contract success outcome")
        existing = _checks(evidence, invocation_ref)
        if existing:
            if len(existing) != 1:
                _fail("multiple acceptance records")
            record = existing[0][1]
            saved = record["snapshot"]
            if (record["command_id"] != idempotency_key
                    or saved["selected_outcome_id"] != selected_outcome_id
                    or {k: hashlib.sha256(v).hexdigest() for k,v in products.items()}
                    != {k: v["sha256"] for k,v in saved["products"].items()}):
                _fail("BLOCKED: prior atomic seal cannot be overwritten")
            refs = {k:v["resource_ref"] for k,v in saved["products"].items()}
            _verify_record(core.event_store, db, cut, record, list(refs.values()))
            tx = None
        else:
            if _has_products(evidence, invocation_ref):
                _fail("BLOCKED: prior partial product publication cannot be overwritten")
            # All schemas/content/read evidence are checked before any semantic
            # publication is staged, allowing any number of corrected drafts.
            _check(evidence, invocation_ref, selected_outcome_id, products)
            _, compiled, operation, _, _ = loaded
            ports = {p.name:p for p in compiled.ports}
            bindings = {p.port_id:p for p in execution.operation.operation_binding.output_port_bindings}
            tx = core.begin(idempotency_key=idempotency_key,
                task_round_id=context.task_round_ref.entity_id, net_instance_id=context.net_instance_ref.entity_id)
            tx.expect_registry_ordinal(cut)
            refs = {}
            for name in sorted(products):
                port = ports[name]
                binding = bindings[port.port_id]
                ref = kernel._publish_bytes_in_transaction(context, PublishResource(
                    origin=PetriOutputOrigin(binding.output_binding_ref, context.activation_ref),
                    payload=products[name], media_type="application/json", content_schema_ref=port.schema,
                    summary="Contract operation product", lifetime_ref=context.invocation_ref,
                    descriptors={"output_outcome_id":selected_outcome_id,"output_port_id":port.port_id,"place":binding.place},
                    idempotency_key=f"{idempotency_key}:product:{port.port_id}:0"), tx)
                refs[name] = _resource(ref)
            accepted = _check(evidence, invocation_ref, selected_outcome_id, products, refs=refs)
            record = {"protocol":"firing_content_check/v1", "command_id":idempotency_key,
                      "snapshot":_without_bytes(accepted)}
            bootstrap = _version_from_payload(json.loads(core.event_store.get_meta("bootstrap_command_ref")))
            kernel._publish_bytes_in_transaction(context, PublishResource(
                origin=PrivateSystemOrigin(bootstrap), payload=canonical_json(record),
                media_type="application/json",content_schema_ref=None,
                summary="Trusted firing content check",lifetime_ref=context.invocation_ref,
                descriptors={"firing_content_check":"v1"},
                idempotency_key=idempotency_key+":content-check"),tx)
    if tx is not None:
        tx.commit()
    artifacts = tuple(verify_resource(core,kernel,execution.operation.canonical,_resource_from_payload(ref))
                      for ref in refs.values())
    return register_operation_outputs(repository,execution,artifacts,
        selected_outcome_id=selected_outcome_id,idempotency_key=idempotency_key)


def validate_content_transaction(context):
    """Original EventStore gate; catches low-level single-port and Success bypass.

    Uses the already-open write transaction, including pending product bytes.
    There is no separate checker state or caller-supplied PASS capability.
    """
    head = context.canonical_upper
    evidence = FiringContentEvidence(context.event_store, context.db, head)
    groups = {}
    checks = {}
    for item in context.objects:
        if item.object_type != "resource_version/v1":
            continue
        meta = item.metadata
        invocation = meta.get("reference_provenance", {}).get("producer_invocation_ref")
        if not isinstance(invocation, Mapping):
            continue
        key = invocation["version_id"]
        if meta.get("descriptors", {}).get("firing_content_check") == "v1":
            checks.setdefault(key, []).append(item)
        if meta.get("origin_kind") == "petri_output":
            groups.setdefault(key, (invocation, []))[1].append(item)
    for key, (invocation, items) in groups.items():
        outcomes = {item.metadata.get("descriptors", {}).get("output_outcome_id") for item in items}
        outcome = next(iter(outcomes)) if len(outcomes) == 1 else None
        loaded = _load(evidence, invocation, outcome)
        if loaded is None:
            continue
        if len(outcomes) != 1 or None in outcomes:
            _fail("atomic products require one actual selected outcome")
        outcome = next(iter(outcomes))
        if outcome not in loaded[3]["success_outcomes"]:
            continue
        if _has_products(evidence, invocation):
            _fail("BLOCKED: previously published ports cannot be sealed again")
        proposed = checks.pop(key, [])
        if len(proposed) != 1:
            _fail("atomic content publication requires all products and one check")
        check = proposed[0]
        record = json.loads(evidence._payload({"version_id":str(check.version_id),
            "storage_locator":check.storage_locator,"size":check.size}))
        if (record.get("command_id") != context.idempotency_key
                or record.get("snapshot", {}).get("invocation_ref") != invocation
                or record["snapshot"].get("acceptance_cut") != head
                or check.metadata.get("lifetime_ref") != invocation):
            _fail("CHECK_BINDING_MISMATCH: pending check differs from original command/invocation/cut")
        _verify_record(context.event_store,context.db,head,record,
            [_resource({"entity_type":item.object_type,"logical_id":str(item.logical_id),"version_id":str(item.version_id)})
             for item in items],pending=context.objects)
    if checks:
        _fail("check publication must be atomic with its complete product bundle")
    for item in context.objects:
        if item.object_type != "operation_result/v1":
            continue
        result = item.metadata
        invocation = result["invocation_ref"]
        outcome = result.get("selected_outcome_id")
        loaded = _load(evidence, invocation, outcome)
        if loaded is None:
            continue
        if outcome is None:
            _fail("bound Success requires actual selected outcome")
        _output_material(evidence, invocation, outcome, result["output_resource_refs"])
        if outcome not in loaded[3]["success_outcomes"]:
            continue
        original = _checks(evidence, invocation)
        if len(original) != 1 or original[0][1]["snapshot"]["selected_outcome_id"] != outcome:
            _fail("Success lacks its original accepted outcome/check")
        _verify_record(context.event_store,context.db,head,original[0][1],result["output_resource_refs"])
