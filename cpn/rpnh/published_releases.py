"""Read-only published-v2 discovery, separate from v1 offers and activation.

Official Release assets bind the two outer sidecars to one wheel coordinate.
Their declarations are compared with the complete current installation. Even
matching native/pass metadata is not an installer authorization: referenced
logs, payloads, dependency selections and business admission are not verified
here. No release body instruction or metadata-provided URL is executed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
import stat
import time
from zipfile import BadZipFile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from packaging.version import Version

from . import install_contract as contract
from .updates import CHANNELS, RELEASES_URL, REPOSITORY, _version

SUPPORT_ASSET = "update_support.v2.json"
VALIDATION_ASSET = "update_validation.v2.json"
SIDECARS = (SUPPORT_ASSET, VALIDATION_ASSET)
V3_SIDECARS = ("update_support.v3.json", "update_validation.v3.json")
V4_SIDECARS = ("update_support.v4.json", "update_validation.v4.json")
_ALL_SIDECARS = (*SIDECARS, *V3_SIDECARS, *V4_SIDECARS)
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_RELEASES = 20
MAX_CANDIDATES = 5
FETCH_TIMEOUT = 15.0
SCHEDULE_BUDGET = 45.0
_DOWNLOAD_ROOT = f"https://github.com/{REPOSITORY}/releases/download/"
_CDN_HOSTS = {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}


def _release_tag(value):
    if not isinstance(value, str) or not value.startswith("v"):
        raise ValueError("choose an exact vVERSION release")
    _version(value[1:])
    return value


class _Rejected(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class LocalMaterialRequest:
    """Explicit local planning inputs; neither a release nor install authority."""
    release: str
    wheel: Path
    materials: Path

    def __post_init__(self):
        _release_tag(self.release)
        if not isinstance(self.wheel, Path) or not isinstance(self.materials, Path):
            raise TypeError("material planning requires explicit local file paths")


def _metadata_url(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith(_DOWNLOAD_ROOT):
        return False
    tail = url[len(_DOWNLOAD_ROOT):].split("/")
    if len(tail) != 2 or not tail[0].startswith("v") or tail[1] not in _ALL_SIDECARS:
        return False
    try:
        _version(tail[0][1:])
    except ValueError:
        return False
    return True


class _AssetRedirect(HTTPRedirectHandler):
    """Only one GitHub asset-to-CDN hop; never redirect the release index."""
    def __init__(self, origin: str, *, asset_api: bool = False):
        super().__init__()
        self.origin, self.followed = origin, False
        prefix = f"https://api.github.com/repos/{REPOSITORY}/releases/assets/"
        suffix = origin[len(prefix):] if origin.startswith(prefix) else ""
        self.asset_api = (asset_api and suffix.isascii() and suffix.isdecimal()
                          and suffix[0:1] != "0")

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (self.followed or req.full_url != self.origin or not (_metadata_url(self.origin) or self.asset_api)
                or target.scheme != "https" or target.hostname not in _CDN_HOSTS
                or target.username is not None or target.password is not None
                or target.port not in (None, 443) or target.fragment):
            raise _Rejected("metadata_redirect_rejected")
        self.followed = True
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get_json(url: str):
    """Fetch bounded strict JSON from fixed official coordinates, without auth."""
    if url != RELEASES_URL and not _metadata_url(url):
        raise _Rejected("untrusted_metadata_coordinate")
    request = Request(url, headers={"Accept": "application/json",
                                   "User-Agent": "RPNH-update-check/2"})
    with build_opener(_AssetRedirect(url)).open(request, timeout=FETCH_TIMEOUT) as response:
        data = response.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise _Rejected("metadata_size_limit")
    # Reuse the contract's duplicate-key/nonfinite-number rejection.
    return contract._json(data)


def _asset(assets: list, tag: str, name: str) -> dict:
    found = [row for row in assets if isinstance(row, dict) and row.get("name") == name]
    if len(found) != 1:
        raise _Rejected("missing_or_duplicate_asset")
    row = found[0]
    identity, size = row.get("id"), row.get("size")
    expected = _DOWNLOAD_ROOT + tag + "/" + name
    if (type(identity) is not int or identity <= 0 or type(size) is not int or size <= 0
            or row.get("state") != "uploaded" or row.get("browser_download_url") != expected
            or row.get("url") != f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{identity}"):
        raise _Rejected("asset_binding_mismatch")
    if name in _ALL_SIDECARS and size > MAX_JSON_BYTES:
        raise _Rejected("metadata_size_limit")
    return {"id": identity, "name": name, "size": size, "url": expected}


def _release(row, *, channel: str, current: Version | None, now: float) -> dict | None:
    if not isinstance(row, dict):
        raise _Rejected("invalid_release")
    try:
        tag = row["tag_name"]
        version = _version(tag[1:] if isinstance(tag, str) and tag.startswith("v") else None)
        published = datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
        if (row["draft"] is not False or type(row["id"]) is not int or row["id"] <= 0
                or type(row["prerelease"]) is not bool or published.tzinfo is None
                or published.timestamp() > now
                or row["html_url"] != f"https://github.com/{REPOSITORY}/releases/tag/{tag}"
                or row["url"] != f"https://api.github.com/repos/{REPOSITORY}/releases/{row['id']}"
                or version.is_prerelease and not row["prerelease"]):
            raise _Rejected("release_binding_mismatch")
        if (current is not None and version <= current) or channel == "stable" and row["prerelease"]:
            return None
        assets = row["assets"]
        if not isinstance(assets, list) or len(assets) > 128:
            raise _Rejected("invalid_asset_list")
        wheel = f"rpnh_harness-{version}-py3-none-any.whl"
        # A partly published newest pair cannot silently fall back to an older
        # schema which omits one of its material/entry dimensions.
        sidecars = next((pair for pair in (V4_SIDECARS, V3_SIDECARS, SIDECARS)
                         if any(isinstance(item, dict) and item.get("name") in pair for item in assets)), SIDECARS)
        selected = [_asset(assets, tag, name) for name in (wheel, *sidecars)]
        if len({asset["id"] for asset in selected}) != len(selected):
            raise _Rejected("duplicate_asset_identity")
        source = {"kind": "published_release", "rpnh_version": str(version), "release": tag,
                  "channel": "prerelease" if row["prerelease"] else "stable",
                  "artifact": {"filename": wheel, "location": selected[0]["url"]}}
        return {"release_id": row["id"], "published_at": row["published_at"],
                "source": source, "assets": selected}
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        if isinstance(exc, _Rejected):
            raise
        raise _Rejected("invalid_release") from None


def _match_selection(support, assessment, *, components, platform, runtimes, from_version,
                     kind="current_selection") -> dict:
    backed = {row["id"]: row for row in assessment["evidence_backed_combinations"]}
    matched, diagnostics = [], list(assessment["diagnostics"])
    # assess_update_support already parsed every combination through the
    # strict original contract; do not introduce another sidecar schema.
    for row in support["combinations"]:
        reasons = []
        if set(row["components"]) != set(components):
            reasons.append("complete_component_set_mismatch")
        elif row["components"] != components:
            reasons.append("selected_profile_or_artifact_mismatch")
        if row["platform"] != platform:
            reasons.append("platform_mismatch")
        if row["runtimes"] != runtimes:
            reasons.append("runtime_signature_mismatch")
        if row["id"] not in backed:
            reasons.append("no_backing_native_pass_metadata")
        diagnostics.extend({"kind": kind, "subject": row["id"], "code": code}
                           for code in reasons)
        if not reasons:
            matched.append(backed[row["id"]])
    identities = {row["id"] for row in matched}
    pairs = {key: [row for row in assessment[key]
                   if row["combination"] in identities and row["version"] == from_version]
             for key in ("upgrade_from", "rollback_to")}
    return {"matching_combinations": matched, **pairs, "diagnostics": diagnostics}


def _correspondence(release: dict, support, assessment, current: dict) -> dict:
    components = {entry: {key: deepcopy(item[key]) for key in ("profile", "artifact")}
                  for entry, item in current["components"].items()}
    # Preserve the complete frontend selection and its exact artifacts. Only
    # the Basic wheel's release version changes to the prospective RPNH R.
    components["basic"]["artifact"]["version"] = release["source"]["rpnh_version"]
    return {**release, **_match_selection(support, assessment, components=components,
        platform=current["platform"], runtimes=contract.runtime_signature(current["runtime_bindings"]),
        from_version=current["rpnh_version"])}


def _local_material_plan(request, release, support, validation, assessment, current, old_materials) -> dict:
    """Read a target catalog and exact local closure without preparing any I.

    The derived local-source plan exists only for the original material reader.
    It is neither returned nor passed to an executor. The visible plan retains
    the release URL and explicitly lacks proof that the local wheel is its asset.
    """
    from .install_materials import (
        PUBLISHED_MATERIALS_SCHEMA, ensure_explicit_reconfigure_extras, load_materials, selected_root_extras,
    )
    result = {"requested_release": request.release, "source": deepcopy(release["source"]),
              "status": "rejected", "local_wheel": str(request.wheel), "plan": None,
              "materials_checked": False, "root_extras": None, "installable": False,
              "qualification": {"local_wheel_is_published_asset": False,
                                "scope_and_capability_acceptance": "not_granted",
                                "root_extras": "not_represented_in_v2_sidecars",
                                "runtime_health": "not_probed", "activation": "not_enabled"},
              "diagnostics": []}
    if support["schema_version"] in {contract.UPDATE_SUPPORT_V3_SCHEMA, contract.UPDATE_SUPPORT_V4_SCHEMA}:
        result["qualification"]["root_extras"] = ("represented_by_v4_declared_materials"
            if support["schema_version"] == contract.UPDATE_SUPPORT_V4_SCHEMA else "represented_by_v3_declared_materials")
    stage = "target_wheel"
    try:
        wheel = request.wheel.expanduser().resolve(strict=True)
        result["local_wheel"] = str(wheel)
        if wheel.name != release["source"]["artifact"]["filename"]:
            raise ValueError("local wheel filename differs from the selected release artifact")
        catalog = contract.load_profile_catalog(wheel=wheel)
        stage = "local_material_document"
        material_path = request.materials.expanduser().resolve(strict=True)
        document = contract.read_software_record(material_path, limit=1024 * 1024)
        ensure_explicit_reconfigure_extras(document, old_materials)
        result["root_extras"] = list(selected_root_extras(document))
        stage = "complete_plan"
        plan = contract.build_install_plan(release["source"], document.get("platform"),
            document.get("runtime_bindings"), current=current, catalog=catalog)
        result["plan"] = plan.to_document()
        if plan.conflicts:
            result["diagnostics"].append({"code": "complete_plan_conflicts", "stage": stage})
            return result
        if document.get("schema_version") == PUBLISHED_MATERIALS_SCHEMA:
            # The v2 material reader preserves the published URL and checks
            # local physical inputs separately. It does not authenticate them.
            local_check_plan = plan
        else:
            local_source = {"kind": "local_candidate", "rpnh_version": catalog.rpnh_version,
                            "release": None, "channel": None,
                            "artifact": {"filename": wheel.name, "location": str(wheel)}}
            # Preserve the original v1 read-only local checking view. It is
            # never passed to an executor or returned as the visible source.
            local_check_plan = replace(plan, source=local_source)
        stage = "local_material_closure"
        materials = load_materials(material_path, local_check_plan, catalog=catalog)
        if next(row["path"] for row in materials.wheels if row["name"] == "rpnh-harness") != str(wheel):
            raise ValueError("local materials identify a different target RPNH wheel")
        if contract.read_software_record(material_path, limit=1024 * 1024) != document:
            raise ValueError("local materials changed during planning")
        components = {"basic": {"profile": "builtin", "artifact": {
            "kind": "wheel", "name": "rpnh-harness", "version": plan.rpnh_version,
            "variant": "py3-none-any", "architecture": "any"}}}
        for entry, item in materials.document["frontends"].items():
            components[entry] = {"profile": plan.components[entry]["profile"], "artifact": item["artifact"]}
        result.update(status="local_materials_checked", materials_checked=True,
            distributions=[{"name": row["name"], "version": row["version"]} for row in materials.wheels],
            declaration_correspondence=_match_selection(support, assessment, components=components,
                platform=plan.platform, runtimes=contract.runtime_signature(plan.runtime_bindings),
                from_version=current["rpnh_version"], kind="planned_selection"))
        from .published_qualification import project_qualification_gaps
        result["qualification_gaps"] = project_qualification_gaps(
            support, validation, assessment, components=components,
            matching_combinations=result["declaration_correspondence"]["matching_combinations"],
            current_version=current["rpnh_version"], root_extras=result["root_extras"],
            material_selection=materials.document.get("published_selection"))
    except (OSError, RuntimeError, ValueError, TypeError, BadZipFile) as exc:
        # These are local material diagnostics, never transport exceptions.
        result["diagnostics"].append({"code": "local_material_plan_rejected", "stage": stage, "detail": str(exc)})
    return result


def _report(current: dict, channel: str, *, checked: bool, injected: bool = False) -> dict:
    return {"schema_version": "rpnh/published_release_discovery/v1", "protocol": contract.V2_PROTOCOL,
            "repository": REPOSITORY, "channel": channel, "checked": checked,
            "transport": "injected_reader" if injected else "official_https_coordinates",
            "current": {key: deepcopy(current[key]) for key in
                        ("installation_id", "rpnh_version", "source", "platform", "runtime_bindings", "components")},
            "releases": [], "diagnostics": [], "offer": None, "installable": False,
            "qualification": {
                "declarations": "metadata_correspondence_only", "referenced_logs_verified": False,
                "runtime_basis": "recorded_installed_observations_not_a_fresh_probe",
                "root_extras": "not_represented_in_v2_sidecars",
                "payload_and_materials": "not_verified", "business_admission": "not_performed",
                "activation": "published_and_cross_release_execution_remain_gated"}}


def discover(*, current: dict, channel: str, fetch=None, now: float | None = None,
             planning: LocalMaterialRequest | None = None, current_materials=None,
             selected_release: str | None = None) -> dict:
    """Report exact full-S declarations and optional non-executable local plans.

    ``fetch`` is a test/embedding reader. Its use remains explicit in the report
    and does not establish that a fixture was observed on the public service.
    Requests have socket timeouts and a finite scheduling budget, not a claim
    of a hard wall-clock deadline for arbitrary injected readers.
    """
    current = contract.validate_installed(current)
    if channel not in CHANNELS:
        raise ValueError("unknown update channel")
    if planning is not None and (not isinstance(planning, LocalMaterialRequest) or current_materials is None):
        raise ValueError("local planning requires explicit inputs and the current material selection")
    choice = None if selected_release is None else _release_tag(selected_release)
    if planning is not None:
        if choice is not None and choice != planning.release:
            raise ValueError("planning and discovery must select the same exact release")
        choice = planning.release
    current_extras = None
    if current_materials is not None:
        from .install_materials import selected_root_extras
        current_extras = selected_root_extras(current_materials)
    report = _report(current, channel, checked=True, injected=fetch is not None)
    if planning is not None:
        report["local_material_plan"] = {"requested_release": planning.release,
            "status": "release_unavailable", "materials_checked": False, "plan": None, "installable": False}
    transport = get_json if fetch is None else fetch
    now = time.time() if now is None else now
    started = time.monotonic()

    def read(url):
        if time.monotonic() - started > SCHEDULE_BUDGET:
            raise _Rejected("metadata_schedule_budget")
        value = transport(url)
        if time.monotonic() - started > SCHEDULE_BUDGET:
            raise _Rejected("metadata_schedule_budget")
        return value

    def issue(code, *, release_id=None):
        report["diagnostics"].append({"code": code, "release_id": release_id})

    try:
        releases = read(RELEASES_URL)
        if not isinstance(releases, list) or len(releases) > MAX_RELEASES:
            raise _Rejected("invalid_release_list")
    except (OSError, ValueError, TypeError):
        issue("release_index_unavailable_or_invalid")
        return report
    candidates = []
    for row in releases:
        try:
            candidate = _release(row, channel=channel, current=Version(current["rpnh_version"]), now=now)
            if candidate is not None:
                candidates.append(candidate)
        except _Rejected as exc:
            issue(exc.code, release_id=row.get("id") if isinstance(row, dict) and type(row.get("id")) is int else None)
    versions = [row["source"]["rpnh_version"] for row in candidates]
    duplicates = {version for version in versions if versions.count(version) > 1}
    if duplicates:
        issue("duplicate_release_version")
    candidates = [row for row in candidates if row["source"]["rpnh_version"] not in duplicates]
    if choice is not None:
        candidates = [row for row in candidates if row["source"]["release"] == choice]
    for release in sorted(candidates, key=lambda row: Version(row["source"]["rpnh_version"]), reverse=True)[:MAX_CANDIDATES]:
        try:
            support, validation = [read(asset["url"]) for asset in release["assets"][1:]]
            expected = ((contract.UPDATE_SUPPORT_V4_SCHEMA, contract.UPDATE_VALIDATION_V4_SCHEMA)
                        if release["assets"][1]["name"] in V4_SIDECARS else
                        (contract.UPDATE_SUPPORT_V3_SCHEMA, contract.UPDATE_VALIDATION_V3_SCHEMA)
                        if release["assets"][1]["name"] in V3_SIDECARS else
                        (contract.UPDATE_SUPPORT_SCHEMA, contract.UPDATE_VALIDATION_SCHEMA))
            if (not isinstance(support, dict) or not isinstance(validation, dict)
                    or (support.get("schema_version"), validation.get("schema_version")) != expected):
                raise _Rejected("sidecar_asset_schema_mismatch")
            assessment = contract.assess_update_support(support, validation)
            if assessment["source"] != release["source"]:
                raise _Rejected("sidecar_source_mismatch")
            correspondence = _correspondence(release, support, assessment, current)
            from .published_qualification import project_qualification_gaps
            correspondence["qualification_gaps"] = project_qualification_gaps(
                support, validation, assessment, components=current["components"],
                matching_combinations=correspondence["matching_combinations"],
                current_version=current["rpnh_version"], root_extras=current_extras)
            report["releases"].append(correspondence)
            if support["schema_version"] in {contract.UPDATE_SUPPORT_V3_SCHEMA, contract.UPDATE_SUPPORT_V4_SCHEMA}:
                report["qualification"]["root_extras"] = ("per_release_v4_material_projection"
                    if support["schema_version"] == contract.UPDATE_SUPPORT_V4_SCHEMA else "per_release_v3_material_projection")
                report["qualification"]["activation"] = "discovery_only_cross_release_execution_gated"
            if planning is not None:
                report["local_material_plan"] = _local_material_plan(
                    planning, release, support, validation, assessment, current, current_materials)
        except (OSError, ValueError, TypeError) as exc:
            issue(exc.code if isinstance(exc, _Rejected) else "sidecar_unavailable_or_invalid",
                  release_id=release["release_id"])
    return report


def inspect_root(root: Path, *, channel: str, check: bool = False, fetch=None,
                 now: float | None = None, planning: LocalMaterialRequest | None = None,
                 selected_release: str | None = None) -> dict:
    """Hold the original lifecycle SH while reading one existing current I.

    Missing locks/pending transactions require their explicit management path;
    discovery must not initialize or recover a root as a side effect.
    """
    from .install_v2 import check_root, marker, read_target
    from .update_install import lifecycle_lease
    if channel not in CHANNELS:
        raise ValueError("unknown update channel")
    if planning is not None and not check:
        raise ValueError("local material planning requires an explicit published check")
    root = check_root(Path(root))
    if not stat.S_ISREG((root / "lifecycle.lock").lstat().st_mode):
        raise ValueError("published discovery requires an existing regular lifecycle lock")
    with lifecycle_lease(root, exclusive=False):
        before_marker = marker(root)
        if before_marker["pending_activation"] is not None:
            raise ValueError("pending activation requires explicit recovery before discovery")
        target, directory, current = read_target(root)
        old_materials = (contract.read_software_record(directory / "materials.json", limit=1024 * 1024)
                         if planning is not None else None)
        report = (discover(current=current, channel=channel, fetch=fetch, now=now,
                           planning=planning, current_materials=old_materials,
                           selected_release=selected_release) if check
                  else _report(current, channel, checked=False))
        after = read_target(root)
        if marker(root) != before_marker or (after[0], after[2]) != (target, current):
            raise ValueError("managed current changed during read-only discovery")
        if planning is not None and contract.read_software_record(
                directory / "materials.json", limit=1024 * 1024) != old_materials:
            raise ValueError("current material selection changed during planning")
        return report


__all__ = ("SUPPORT_ASSET", "VALIDATION_ASSET", "LocalMaterialRequest", "discover", "inspect_root")
