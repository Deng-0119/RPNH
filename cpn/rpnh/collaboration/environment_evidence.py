"""Allowlisted application evidence published by the existing run owner."""
from .environment_local_contracts import obj, arr, ID, DIGEST, TARGET, DRAFT7, STATUS, LEVEL, SCOPE
from .environment_contracts import nullable

EVIDENCE_SCHEMA = "rpnh/environment_preparation_evidence/v1"


def evidence_schema():
    return {"$schema": DRAFT7, "$id": EVIDENCE_SCHEMA, **obj({
        "schema_version": {"const": EVIDENCE_SCHEMA}, "target": TARGET, "resolution_digest": DIGEST,
        "selections": arr(obj({"kind": {"enum": ["python", "distribution"]}, "name": ID, "version": ID})),
        "checks": arr(obj({"scoped_requirement_id": nullable(SCOPE), "check_id": ID, "status": STATUS,
                           "evidence_level": LEVEL, "reason_code": ID})),
        "host_declarations_digest": DIGEST, "prepared_at": ID, "evidence_scope": arr(ID)})}


def preparation_evidence(requirements, resolution, check, receipt, snapshot):
    from .environment_local_contracts import public_check_summary
    # Distribution names disclosed by the author are already in the exact
    # shared package; private transitive names, profiles and references omitted.
    names = {row["name"] for scoped in requirements.requirements for row in scoped.document["distributions"]}
    choices = []
    for row in resolution.to_dict()["selections"]:
        identity = row["identity"]
        if row["kind"] == "python":
            choice = {"kind": "python", "name": identity["implementation"], "version": identity["version"]}
        elif row["kind"] == "distribution" and identity["name"] in names:
            choice = {"kind": "distribution", "name": identity["name"], "version": identity["version"]}
        else:
            continue
        if choice not in choices:
            choices.append(choice)
    # Transitive check IDs can reveal private software names; omit those rows.
    checks = [row for row in public_check_summary(check)["checks"] if not row["check_id"].startswith("installed_distribution:")]
    return {"schema_version": EVIDENCE_SCHEMA, "target": requirements.target.to_dict(),
        "resolution_digest": resolution.digest, "selections": choices, "checks": checks,
        "host_declarations_digest": snapshot.declarations_digest, "prepared_at": receipt.to_dict()["completed_at"],
        "evidence_scope": ["local_preparation_only", "no_execution_permission", "author_implementation_identity_not_supplied",
                           "installed_metadata_not_installed_file_verification", "remote_service_not_checked"]}
