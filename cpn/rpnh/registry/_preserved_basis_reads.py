"""Read an explicit historical adopted basis and collect its dependency bytes.

This is a new evidence snapshot, not a persisted plan or a comparison against
one. It neither tests current-first commit eligibility nor selects/preserves
slots. The caller must compare returned evidence to any previously frozen plan.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json

from ._candidate_plan_reads import PlanReadClosure, same
from ._event_store.adoption_reads import AdoptionPrefixReads
from ._event_store.net_lineage import verified_adoption_prefix
from .event_store import RegistryConflict, fact_event_envelope
from .models import VersionRef
from .preserved_binding_contracts import PreservedBasis
from .publication import _version_from_payload
from .runtime_binding_contracts import freeze_candidate_document
from .schema_catalog import canonical_json


def _copy_basis(basis):
    if type(basis) is not PreservedBasis:
        raise TypeError("basis reading requires an exact PreservedBasis")
    return PreservedBasis.from_document(basis.to_dict())


@dataclass(frozen=True, slots=True)
class _BasisRead:
    """Detached data from a read, never a substitute for authority revalidation."""
    basis: PreservedBasis
    _evidence_json: str

    def __post_init__(self):
        object.__setattr__(self, "basis", _copy_basis(self.basis))
        object.__setattr__(self, "_evidence_json", freeze_candidate_document(json.loads(self._evidence_json)))

    @property
    def dependency_evidence(self):
        return json.loads(self._evidence_json)["dependency_evidence"]


@dataclass(frozen=True, slots=True)
class _BasisReadContext:
    """Private live read context; public consumers return detached data only."""
    basis: PreservedBasis
    reads: AdoptionPrefixReads
    net: dict
    root_ref: VersionRef
    root: dict


def _basis_context_at(core, basis, db, *, _plan_reads=None):
    if _plan_reads is not None and (type(_plan_reads) is not PlanReadClosure
            or _plan_reads.db is not db or _plan_reads.catalog is not core.catalog
            or _plan_reads.task_id != core.task_id or _plan_reads.branch_id != core.branch_id):
        raise TypeError("basis reading requires its fixed same-cut plan closure")
    closure = PlanReadClosure(db, core) if _plan_reads is None else _plan_reads
    reads = AdoptionPrefixReads(core.event_store, core.catalog, db, core.task_id, _plan_reads=closure)
    # These static exact authorities were actually read by the source-binding
    # checker. Capture their bytes as well, without imposing static semantics
    # on business resources or PUBLISHED witnesses.
    for field in ("task_ref", "native_run_ref", "bootstrap_command_ref"):
        ref = closure.binding[field]
        if not same(ref, closure.meta_ref(field)):
            raise RegistryConflict("basis owner differs from its trusted Registry registration")
        closure.descriptor(ref, static=True)
    lineage = verified_adoption_prefix(core.event_store, core.catalog, core.task_id,
        adoption_event_id=basis.event_id, _db=db, _prefix_reads=reads)
    row = db.execute("SELECT * FROM events WHERE event_id=?", (str(basis.event_id),)).fetchone()
    event = core.event_store._row_to_envelope(row)
    if (event.transaction_id != basis.transaction_id or type(event.task_control_sequence) is not int
            or event.task_control_sequence != basis.task_control_sequence or lineage[-1] != basis.net_ref
            or hashlib.sha256(canonical_json(fact_event_envelope(event))).hexdigest() != basis.adoption_event_sha256):
        raise RegistryConflict("preserved basis differs from its exact adoption event")
    net = reads.metadata(basis.net_ref, "net_instance/v1")
    root_ref = _version_from_payload(net["team_design_root_ref"])
    root = reads.metadata(root_ref, "team_design_root/v1")
    if (not same(root["task_ref"], closure.binding["task_ref"])
            or not same(root["run_ref"], closure.binding["native_run_ref"])):
        raise RegistryConflict("preserved basis belongs to another exact task/run")
    return _BasisReadContext(basis, reads, net, root_ref, root)


@contextmanager
def _basis_read_context(core, basis, *, _db=None, _plan_reads=None):
    """Construct the fixed context inside one owned or supplied Registry cut."""
    selected = _copy_basis(basis)
    if _db is None:
        if _plan_reads is not None:
            raise TypeError("shared plan reads require their existing SQLite cut")
        with core.event_store.connect() as db:
            db.execute("BEGIN")
            yield _basis_context_at(core, selected, db)
        return
    # Validate the connection before any closure reads; the shared prefix does
    # the same check before it uses the context constructed in this cut.
    import sqlite3
    if not isinstance(_db, sqlite3.Connection) or not _db.in_transaction:
        raise TypeError("basis reading requires an existing SQLite read cut")
    from pathlib import Path
    main = next((row for row in _db.execute("PRAGMA database_list").fetchall() if row["name"] == "main"), None)
    if main is None or not main["file"] or Path(main["file"]).resolve() != core.event_store.path.resolve():
        raise RegistryConflict("basis reading belongs to another Registry")
    yield _basis_context_at(core, selected, _db, _plan_reads=_plan_reads)


def read_preserved_basis(core, basis, *, _db=None):
    """Collect canonical historical-prefix bytes, independent of current head.

    Only the supported finite JSON descriptor/resource roles are accepted.
    Tar-backed/unknown artifact roles are explicitly unsupported in this slice.
    An optional internal supplied DB must be the Registry's existing read cut.
    """
    with _basis_read_context(core, basis, _db=_db) as context:
        return _BasisRead(context.basis, freeze_candidate_document({
            "dependency_evidence": context.reads.finish_dependencies()}))
