"""Explicit installation from exact official v3/v4 release materials.

The review request is data, not a saved authorization. Each execution resolves
the fixed official coordinates again and acquires every selected asset by its
observed asset ID. The original installer owns preparation, probes, EX and the
current commit. Explicit mcp/skills Python extras use the same exact material
binding. Same-release reconfiguration preserves the original payload and
predecessor. V4 binds every selected managed frontend and both exact endpoint
selections for same-release changes; this entry does not grant cross-release
upgrades or business-data compatibility.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from http.client import HTTPException
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from packaging.version import Version

from . import install_contract as contract
from .published_download import MAX_WHEEL_BYTES, _open_wheel, _stream
from .published_qualification import project_qualification_gaps
from .published_releases import (
    MAX_RELEASES, SCHEDULE_BUDGET, V3_SIDECARS, V4_SIDECARS, _asset, _release, _release_tag, get_json,
)
from .updates import CHANNELS, RELEASES_URL

REQUEST_SCHEMA = "rpnh/published_bootstrap/v1"
RECONFIGURE_SCHEMA = "rpnh/published_reconfigure/v1"
MAX_MATERIAL_BYTES = 512 * 1024 * 1024
BOOTSTRAP_EXTRAS = frozenset({"mcp", "skills"})
_BASIC_COMPONENTS = {"basic": {"profile": "builtin", "binding": {"kind": "managed"}}}
_ACQUISITION_KEY = object()


class PublishedBootstrapError(ValueError):
    """A concrete release/material/target selection cannot be bootstrapped."""


def _canonical(document):
    return json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True, slots=True)
class BootstrapRequest:
    """Immutable, reviewable observation; never accepted as an acquired receipt."""
    _serialized: str

    def to_document(self) -> dict:
        return contract._json(self._serialized.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class ReconfigureRequest:
    """Reviewable complete selection and display bound to one actual predecessor."""
    _serialized: str

    def to_document(self) -> dict:
        return contract._json(self._serialized.encode("utf-8"))


def _runtime_names(components):
    required = {"python"}
    if set(components) & {"codex", "opencode"}:
        required.update(("node", "npm"))
    if "dsh-web" in components:
        required.update(("node", "pnpm"))
    return required


def _same_release_python(first, second):
    """Keep base Python; each endpoint retains its own explicit UI runtimes."""
    return ("python" in first and "python" in second
            and first["python"] == second["python"])


def _confirmed_display(value, components, expected):
    from .install_executor import _display_selection
    if value is not None and _display_selection(value, components) != expected:
        raise PublishedBootstrapError("published display differs from its confirmed acquisition")
    return expected


def inspect_bootstrap(*, release: str, channel: str, platform: dict,
                      runtime_bindings: dict, combination: str | None = None,
                      root_extras: tuple[str, ...] | list[str] = (),
                      components: dict | None = None, selected_frontend: str | None = None) -> BootstrapRequest:
    """Read one exact release without downloading material assets or creating a root.

    Target facts are explicit observations supplied by the caller; this reader
    does not infer native execution from a host name. Execution re-observes the
    actual host and selected Python before writing and under the original EX.
    Official native/pass declarations are displayed, not independently awarded.
    Optional dependencies are selected by the caller, never inferred from an
    available combination. Empty remains the default even with an explicit ID.
    """
    from .install_materials import selected_root_extras
    from .install_executor import _display_selection
    from .published_frontends import MAX_FRONTEND_ASSET_BYTES, MAX_FRONTEND_TOTAL_BYTES
    components = contract._selection(_BASIC_COMPONENTS if components is None else components, complete=False)
    if any(row["binding"] != {"kind": "managed"} for row in components.values()):
        raise PublishedBootstrapError("published bootstrap requires explicit installation-owned frontend bindings")
    selected_frontend = _display_selection("basic" if selected_frontend is None else selected_frontend, components)
    if not isinstance(root_extras, (tuple, list)):
        raise PublishedBootstrapError("bootstrap root_extras requires an explicit extra-name sequence")
    extras = list(selected_root_extras({"root_extras": list(root_extras)}))
    if set(extras) - BOOTSTRAP_EXTRAS:
        raise PublishedBootstrapError("bootstrap supports only explicit mcp and skills Python extras")
    release = _release_tag(release)
    if channel not in CHANNELS:
        raise PublishedBootstrapError("unknown release channel")
    platform = contract.validate_platform(platform)
    runtimes = contract.validate_runtime_bindings(runtime_bindings)
    basic_install = set(components) == {"basic"} and not extras
    if (platform["emulated"] is True or not basic_install and platform["emulated"] is not False
            or set(runtimes) != _runtime_names(components)):
        raise PublishedBootstrapError("bootstrap requires an explicit native target and exactly its selected runtime bindings")
    if combination is not None and (not isinstance(combination, str) or not combination):
        raise PublishedBootstrapError("choose an explicit combination ID")
    started = time.monotonic()

    def read(url):
        if time.monotonic() - started > SCHEDULE_BUDGET:
            raise PublishedBootstrapError("official metadata exceeded its scheduling budget")
        result = get_json(url)
        if time.monotonic() - started > SCHEDULE_BUDGET:
            raise PublishedBootstrapError("official metadata exceeded its scheduling budget")
        return result

    try:
        index = read(RELEASES_URL)
        if not isinstance(index, list) or len(index) > MAX_RELEASES:
            raise PublishedBootstrapError("official release index is unavailable or invalid")
        rows = [row for row in index if isinstance(row, dict) and row.get("tag_name") == release]
        if len(rows) != 1:
            raise PublishedBootstrapError("exact release must occur once in the bounded official index")
        observed = _release(rows[0], channel=channel, current=None, now=time.time())
        pair = () if observed is None else tuple(asset["name"] for asset in observed["assets"][1:])
        if pair not in (V3_SIDECARS, V4_SIDECARS) or len(components) > 1 and pair != V4_SIDECARS:
            raise PublishedBootstrapError("bootstrap requires an exact-material sidecar pair; frontends require v4")
        support = contract.validate_update_support(read(observed["assets"][1]["url"]))
        validation = contract.validate_update_validation(read(observed["assets"][2]["url"]))
        expected_schemas = ((contract.UPDATE_SUPPORT_V4_SCHEMA, contract.UPDATE_VALIDATION_V4_SCHEMA)
                            if pair == V4_SIDECARS else
                            (contract.UPDATE_SUPPORT_V3_SCHEMA, contract.UPDATE_VALIDATION_V3_SCHEMA))
        if (support["schema_version"], validation["schema_version"]) != expected_schemas:
            raise PublishedBootstrapError("sidecar assets and their schema versions differ")
        assessment = contract.assess_update_support(support, validation)
        if assessment["source"] != observed["source"]:
            raise PublishedBootstrapError("sidecar source differs from the exact official release")
        backed = {row["id"]: row for row in assessment["evidence_backed_combinations"]}
        choices = []
        for row in support["combinations"]:
            if combination is not None and row["id"] != combination:
                continue
            same_target = (row["platform"]["os"] == platform["os"]
                and row["platform"]["architecture"] == platform["architecture"]
) if basic_install else (
                    row["id"] in backed and row["platform"] == platform
                    and row["runtimes"] == contract.runtime_signature(runtimes))
            if (not same_target
                    or {entry: item["profile"] for entry, item in row["components"].items()}
                    != {entry: item["profile"] for entry, item in components.items()}
                    or row["materials"]["root_extras"] != extras):
                continue
            gaps = project_qualification_gaps(support, validation, assessment,
                components=row["components"], matching_combinations=([backed[row["id"]]] if row["id"] in backed else []),
                root_extras=extras, material_selection=row["materials"])
            # Basic installation compatibility is proved by original preparation
            # against the actual target, not by historical native-test labels.
            # Keep qualification gaps unchanged; ambiguous materials still reject.
            if basic_install or (len(gaps["combinations"]) == 1
                    and not gaps["combinations"][0]["installation"]["missing_case_ids"]
                    and not any(item["missing_case_ids"] for item in gaps["combinations"][0].get("entry_installation", {}).values())
                    and gaps["dependency_materials"]["status"] == "exact_selection_matches_backed_metadata"):
                choices.append((row, gaps))
        if basic_install and combination is None and len(choices) > 1:
            # Prefer the uniquely declared target-runtime material set only to
            # resolve ambiguity; never reject a sole pure-Python set on this basis.
            matches = [(row, gaps) for row, gaps in choices
                if set(row["runtimes"]) == {"python"}
                and row["runtimes"]["python"]["implementation"] == runtimes["python"]["implementation"]
                and row["runtimes"]["python"]["architecture"] == runtimes["python"]["architecture"]
                and Version(row["runtimes"]["python"]["version"]).release[:2]
                    == Version(runtimes["python"]["version"]).release[:2]]
            if len(matches) == 1:
                choices = matches
        if len(choices) != 1:
            raise PublishedBootstrapError("choose one unambiguous material combination matching the target, components and extras; non-Basic entries still require backed bootstrap cases")
        selected, gaps = choices[0]
        assets = deepcopy(observed["assets"])
        identities = {asset["id"] for asset in assets}
        total = 0
        for wheel in selected["materials"]["python_wheels"]:
            actual = _asset(rows[0]["assets"], release, wheel["filename"])
            if {key: actual[key] for key in ("id", "size", "url")} != wheel["asset"]:
                raise PublishedBootstrapError("selected wheel differs from the official release asset identity")
            if actual["size"] > MAX_WHEEL_BYTES:
                raise PublishedBootstrapError("selected wheel exceeds the acquisition size limit")
            total += actual["size"]
            if actual != observed["assets"][0]:
                if actual["id"] in identities:
                    raise PublishedBootstrapError("selected release assets repeat an identity")
                identities.add(actual["id"])
                assets.append(actual)
        if total > MAX_MATERIAL_BYTES:
            raise PublishedBootstrapError("selected release material set exceeds the acquisition size limit")
        frontend_total = 0
        for material in selected["materials"].get("frontends", {}).values():
            actual = _asset(rows[0]["assets"], release, material["filename"])
            if {key: actual[key] for key in ("id", "size", "url")} != material["asset"]:
                raise PublishedBootstrapError("selected frontend differs from the official release asset identity")
            if actual["id"] in identities or actual["size"] > MAX_FRONTEND_ASSET_BYTES:
                raise PublishedBootstrapError("frontend asset repeats an identity or exceeds the acquisition size limit")
            frontend_total += actual["size"]
            identities.add(actual["id"])
            assets.append(actual)
        if frontend_total > MAX_FRONTEND_TOTAL_BYTES:
            raise PublishedBootstrapError("selected frontend material set exceeds the acquisition size limit")
        document = {"schema_version": REQUEST_SCHEMA, "operation": "bootstrap", "release": release,
            "channel": channel, "platform": platform, "runtime_bindings": runtimes,
            "components": components, "selected_frontend": selected_frontend,
            "combination": selected["id"], "source": observed["source"],
            "release_observation": {**observed, "assets": assets},
            "support": support, "validation": validation, "materials": selected["materials"],
            "qualification_gaps": gaps}
        return BootstrapRequest(_canonical(document))
    except PublishedBootstrapError:
        raise
    except (OSError, HTTPException, ValueError, TypeError, KeyError):
        raise PublishedBootstrapError("official bootstrap metadata or asset binding is unavailable or invalid") from None


def _refresh(request):
    if type(request) is not BootstrapRequest:
        raise PublishedBootstrapError("bootstrap requires a current inspected request, not a receipt or material document")
    document = request.to_document()
    if not isinstance(document, dict) or document.get("schema_version") != REQUEST_SCHEMA:
        raise PublishedBootstrapError("invalid published bootstrap request")
    try:
        fresh = inspect_bootstrap(release=document["release"], channel=document["channel"],
            platform=document["platform"], runtime_bindings=document["runtime_bindings"],
            combination=document["combination"], root_extras=document["materials"]["root_extras"],
            components=document["components"], selected_frontend=document["selected_frontend"])
    except (KeyError, TypeError):
        raise PublishedBootstrapError("invalid published bootstrap request") from None
    if fresh.to_document() != document:
        raise PublishedBootstrapError("official release or material selection changed after confirmation; inspect again")
    return document


def _check_target(document):
    from .frontend_launch import observe_runtime
    from .install_materials import _architecture, _check_host, _native_header
    _check_host(document)
    if set(document["runtime_bindings"]) != {"python"}:
        from .install_materials import _runtime_observations
        with tempfile.TemporaryDirectory(prefix="rpnh-runtime-observation-") as temporary:
            observed = _runtime_observations(document["runtime_bindings"], Path(temporary))
        for name in ("python", "node"):
            if name in observed:
                if _architecture(observed[name]["architecture"]) != _architecture(document["platform"]["architecture"]):
                    raise PublishedBootstrapError("selected runtime differs from the native target architecture")
                _native_header(Path(observed[name]["path"]), document["platform"])
        return
    expected = document["runtime_bindings"]["python"]
    observed = observe_runtime("python", expected["path"], environment={"PATH": "/usr/bin:/bin"})
    if ({"path": str(observed.path), "version": observed.version,
         "implementation": observed.implementation, "architecture": observed.architecture} != expected
            or _architecture(observed.architecture) != _architecture(document["platform"]["architecture"])):
        raise PublishedBootstrapError("selected Python differs from the actual target observation")
    _native_header(observed.path, document["platform"])


def _file_observation(path):
    path = Path(path)
    value = path.lstat()
    if path != path.resolve(strict=True) or not stat.S_ISREG(value.st_mode):
        raise PublishedBootstrapError("acquired material must remain one real regular file")
    return (str(path), value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _material_observation(path):
    try:
        path = Path(path)
        if stat.S_ISDIR(path.lstat().st_mode):
            from .published_frontends import tree_observation
            return tree_observation(path)
        return _file_observation(path)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PublishedBootstrapError("acquired material changed or became unavailable before preparation") from exc


@dataclass(frozen=True, slots=True)
class _AcquiredBootstrap:
    """Transient acquisition context, bound to this invocation and exact inputs.

    The private seal is never serialized; a receipt, flag, custom verifier or
    saved request cannot replace acquisition. Metadata, closure and file facts
    are still rechecked. This is not a sandbox against malicious same-user code.
    """
    _key: object
    root: Path
    request: BootstrapRequest
    plan: str
    materials: str
    files: tuple

    def verify(self, root, plan, materials):
        if self._key is not _ACQUISITION_KEY:
            raise PublishedBootstrapError("missing current official asset acquisition")
        if (Path(root) != self.root or _canonical(plan) != self.plan
                or _canonical(materials.document) != self.materials):
            raise PublishedBootstrapError("published execution differs from its acquired plan/materials")
        document = _refresh(self.request)
        _check_target(document)
        for observation in self.files:
            if _material_observation(observation[0]) != observation:
                raise PublishedBootstrapError("acquired release material changed before preparation")
        if (plan["operation"] != "bootstrap" or plan["expected_from"] is not None
                or plan["source"] != document["source"] or plan["components"] != document["components"]
                or materials.document["published_selection"] != document["materials"]
                or materials.document["root_extras"] != document["materials"]["root_extras"]):
            raise PublishedBootstrapError("published execution is outside the exact complete bootstrap selection")
        return document["selected_frontend"]


def _verify_acquisition(context, root, plan, materials, *, current=None, under_lock=False,
                        selected_frontend=None):
    if type(context) is _AcquiredBootstrap:
        if under_lock and current is not None:
            raise PublishedBootstrapError("published bootstrap cannot replace an existing installation")
        expected_display = context.verify(root, plan, materials)
    elif type(context) is _AcquiredReconfigure:
        expected_display = context.verify(root, plan, materials, current=current, under_lock=under_lock)
    else:
        raise PublishedBootstrapError("published installation needs its current official asset acquisition")
    return _confirmed_display(selected_frontend, plan["components"], expected_display)


def _root_asset(selection):
    return next(row for row in selection["python_wheels"] if row["name"] == "rpnh-harness")


def _published_installation_observation(current):
    """Read original software facts without probing an outgoing environment.

    Callers hold the original lifecycle lease. Original download files and a
    healthy outgoing interpreter are deliberately not required for rollback.
    These retained declarations never replace acquisition for a new I.
    """
    from .install_materials import (
        PUBLISHED_MATERIALS_SCHEMA, _fields, _require_source_materials,
        _require_published_frontends, _wheel_declaration, selected_root_extras,
    )
    if current is None:
        raise PublishedBootstrapError("published reconfiguration requires an existing current installation")
    target, directory, record = current
    record = contract.validate_installed(record, expected_id=directory.name)
    if (record["source"]["kind"] != "published_release"
            or any(row["binding"] != {"kind": "managed"} for row in record["components"].values())
            or set(record["runtime_bindings"]) != _runtime_names(record["components"])):
        raise PublishedBootstrapError("same-release published operations require the complete managed published selection")
    if record["runtime_bindings"]["python"]["path"] != str(directory / "venv/bin/python"):
        raise PublishedBootstrapError("retained Python observation does not identify its own installation")
    plan = contract.read_plan(directory)
    expected_runtime = deepcopy(plan["runtime_bindings"])
    if "python" not in expected_runtime:
        raise PublishedBootstrapError("retained published plan has no original Python binding")
    expected_runtime["python"]["path"] = str(directory / "venv/bin/python")
    expected_components = {entry: {key: row[key] for key in ("profile", "binding")}
                           for entry, row in record["components"].items()}
    if (plan["source"] != record["source"] or plan["platform"] != record["platform"]
            or plan["components"] != expected_components
            or expected_runtime != record["runtime_bindings"]
            or plan["conflicts"]):
        raise PublishedBootstrapError("retained published plan differs from its completed installation")
    raw = contract.read_software_record(directory / "materials.json", limit=1024 * 1024)
    materials = _fields(raw, ("schema_version", "platform", "runtime_bindings", "python_wheels",
                             "frontends", "root_extras", "published_selection"), "published installation materials")
    if (materials["schema_version"] != PUBLISHED_MATERIALS_SCHEMA
            or materials["platform"] != plan["platform"]
            or materials["runtime_bindings"] != plan["runtime_bindings"]
            or not isinstance(materials["frontends"], dict)
            or set(materials["frontends"]) != set(plan["components"]) - {"basic"}):
        raise PublishedBootstrapError("retained published materials differ from their original complete plan")
    extras = selected_root_extras(materials)
    if set(extras) - BOOTSTRAP_EXTRAS:
        raise PublishedBootstrapError("retained published extras are outside the supported selection")
    selection = contract.validate_material_selection(materials["published_selection"], source=record["source"])
    if not isinstance(materials["python_wheels"], list) or not 1 <= len(materials["python_wheels"]) <= 256:
        raise PublishedBootstrapError("retained published wheel declarations are invalid")
    _require_source_materials(tuple(_wheel_declaration(row) for row in materials["python_wheels"]),
                              plan, extras, selection)
    for entry, material in materials["frontends"].items():
        if not isinstance(material, dict):
            raise PublishedBootstrapError("retained frontend material must be an object")
        fields = ("kind", "path", "artifact") + (("entry",) if material.get("kind") == "node_tree" else ())
        _fields(material, fields, "retained frontend material")
        contract._absolute(material["path"], "retained frontend input")
        if material["kind"] == "node_tree":
            contract._relative(material["entry"], "retained frontend entry")
    _require_published_frontends(materials["frontends"], plan, selection)
    for entry, material in materials["frontends"].items():
        component = record["components"][entry]
        launch = component["launch"]
        kind = material["kind"]
        expected_kind = {"native_file": "native", "node_tree": "node_wrapper", "dsh_tree": "dsh_prepared"}[kind]
        prefix = f"frontends/{entry}"
        path_matches = (launch["path"].startswith(prefix + "/") if kind == "node_tree" else
                        launch["path"] == (prefix + "/" + entry if kind == "native_file" else prefix))
        if (component["artifact"] != material["artifact"] or launch["scope"] != "installation"
                or launch["kind"] != expected_kind or not path_matches):
            raise PublishedBootstrapError("retained frontend launch differs from its managed material recipe")
    materials["published_selection"] = selection
    return {"target": target, "record": record, "plan": plan, "materials": materials}


def _reconfiguration_current(root, *, current=None, under_lock=False):
    from .install_v2 import check_root, marker, read_target
    from .update_install import lifecycle_lease
    root = check_root(Path(root))

    def observe():
        if marker(root)["pending_activation"] is not None:
            raise PublishedBootstrapError("recover the pending activation before inspecting reconfiguration")
        target = current if under_lock else read_target(root)
        observed = _published_installation_observation(target)
        observed["frontend_view"] = contract.read_frontend_view(target[1],
            expected_id=observed["record"]["installation_id"], selected_entries=observed["record"]["components"])
        return observed

    if under_lock:
        return observe()
    with lifecycle_lease(root, exclusive=False):
        return observe()


def _complete_reconfiguration_coverage(row, *, entries):
    groups = ("installation", "same_release_reconfiguration", "ui_profile_rollback")
    return (all(not row[key]["missing_case_ids"] for key in groups)
            and all(not item["missing_case_ids"] for item in row.get("entry_installation", {}).values())
            and (not entries or "entry_reconfiguration" in row
                 and all(not item["missing_case_ids"] for item in row["entry_reconfiguration"].values())))


def _current_reconfiguration_coverage(current, selected, assessment):
    """Match the retained endpoint to the same fresh v4 pair, without probing it."""
    support, validation = selected["support"], selected["validation"]
    if support["schema_version"] != contract.UPDATE_SUPPORT_V4_SCHEMA:
        raise PublishedBootstrapError("frontend reconfiguration requires fresh v4 coverage for both exact selections")
    record = current["record"]
    components = {entry: {key: row[key] for key in ("profile", "artifact")}
                  for entry, row in record["components"].items()}
    material_selection = deepcopy(current["materials"]["published_selection"])
    # Only a genuine v3 Basic record can lack this empty dimension. Fresh v4
    # evidence must still bind that exact wheel set and its Basic entry cases.
    material_selection.setdefault("frontends", {})
    backed = {row["id"]: row for row in assessment["evidence_backed_combinations"]}
    matching = []
    for row in support["combinations"]:
        if (row["id"] not in backed or row["platform"] != record["platform"]
                or row["runtimes"] != contract.runtime_signature(current["plan"]["runtime_bindings"])
                or row["components"] != components or row["materials"] != material_selection):
            continue
        gaps = project_qualification_gaps(support, validation, assessment,
            components=components, matching_combinations=[backed[row["id"]]],
            current_version=record["rpnh_version"], root_extras=material_selection["root_extras"],
            material_selection=material_selection, entry_reconfiguration=True)
        if (len(gaps["combinations"]) == 1
                and _complete_reconfiguration_coverage(gaps["combinations"][0], entries=True)):
            matching.append(backed[row["id"]])
    if not matching:
        raise PublishedBootstrapError("current exact selection lacks its own fresh v4 entry T03/UI T07 coverage")
    return project_qualification_gaps(support, validation, assessment, components=components,
        matching_combinations=matching, current_version=record["rpnh_version"],
        root_extras=material_selection["root_extras"], material_selection=material_selection,
        entry_reconfiguration=True)


def _inspect_reconfigure(root, current, *, release, channel, platform, runtime_bindings,
                         root_extras, combination=None, components=None, selected_frontend=None):
    old = current["record"]
    runtime_bindings = contract.validate_runtime_bindings(runtime_bindings)
    if (release != old["source"]["release"] or channel != old["source"]["channel"]
            or platform != old["platform"] or not _same_release_python(runtime_bindings, current["plan"]["runtime_bindings"])):
        raise PublishedBootstrapError("reconfiguration must preserve the exact published release, platform and base Python binding")
    components = current["plan"]["components"] if components is None else components
    display = current["frontend_view"]["frontend"] if selected_frontend is None else selected_frontend
    selection = inspect_bootstrap(release=release, channel=channel, platform=platform,
        runtime_bindings=runtime_bindings, root_extras=root_extras, combination=combination,
        components=components, selected_frontend=display).to_document()
    if (selection["source"] != old["source"]
            or _root_asset(selection["materials"]) != _root_asset(current["materials"]["published_selection"])):
        raise PublishedBootstrapError("same-release reconfiguration requires the original exact RPNH asset")
    display = selection["selected_frontend"]
    full_transition = bool((set(old["components"]) | set(selection["components"])) - {"basic"})
    assessment = contract.assess_update_support(selection["support"], selection["validation"])
    backed = [row for row in assessment["evidence_backed_combinations"] if row["id"] == selection["combination"]]
    combination_row = next(row for row in selection["support"]["combinations"] if row["id"] == selection["combination"])
    gaps = project_qualification_gaps(selection["support"], selection["validation"], assessment,
        components=combination_row["components"], matching_combinations=backed,
        current_version=old["rpnh_version"], root_extras=selection["materials"]["root_extras"],
        material_selection=selection["materials"], entry_reconfiguration=full_transition)
    if (len(gaps["combinations"]) != 1
            or not _complete_reconfiguration_coverage(gaps["combinations"][0], entries=full_transition)):
        raise PublishedBootstrapError("same-release reconfiguration requires exact T03 and applicable entry/UI T07 references")
    current_gaps = (_current_reconfiguration_coverage(current, selection, assessment) if full_transition else None)
    return ReconfigureRequest(_canonical({"schema_version": RECONFIGURE_SCHEMA, "operation": "reconfigure",
        "root": str(root), "current": current, "selection": selection, "selected_frontend": display,
        "qualification_gaps": gaps, "current_qualification_gaps": current_gaps}))


def inspect_reconfigure(root: Path, *, release: str, channel: str, platform: dict,
                        runtime_bindings: dict, root_extras: tuple[str, ...] | list[str],
                        combination: str | None = None, components: dict | None = None,
                        selected_frontend: str | None = None) -> ReconfigureRequest:
    """Inspect a complete explicit software selection for the same published R.

    Omitting extras is a TypeError; pass [] explicitly to remove all optional
    dependencies. Omitting components preserves the entire current selection.
    Base Python stays fixed; explicit UI runtimes are observed for the target.
    A removed display needs an explicit replacement.
    No source files, installation records or current pointers change.
    """
    root = Path(root).expanduser().absolute()
    current = _reconfiguration_current(root)
    return _inspect_reconfigure(root, current, release=release, channel=channel, platform=platform,
        runtime_bindings=runtime_bindings, root_extras=root_extras, combination=combination,
        components=components, selected_frontend=selected_frontend)


def _refresh_reconfigure(request, *, current=None, under_lock=False):
    if type(request) is not ReconfigureRequest:
        raise PublishedBootstrapError("reconfiguration requires a current inspected request, not a receipt")
    document = request.to_document()
    if not isinstance(document, dict) or document.get("schema_version") != RECONFIGURE_SCHEMA:
        raise PublishedBootstrapError("invalid published reconfiguration request")
    try:
        selection = document["selection"]
        root = Path(document["root"])
        observed = _reconfiguration_current(root, current=current, under_lock=under_lock)
        if observed != document["current"]:
            raise PublishedBootstrapError("current or retained material facts changed after confirmation; inspect again")
        fresh = _inspect_reconfigure(root, observed, release=selection["release"], channel=selection["channel"],
            platform=selection["platform"], runtime_bindings=selection["runtime_bindings"],
            root_extras=selection["materials"]["root_extras"], combination=selection["combination"],
            components=selection["components"], selected_frontend=document["selected_frontend"])
    except (KeyError, TypeError):
        raise PublishedBootstrapError("invalid published reconfiguration request") from None
    if fresh.to_document() != document:
        raise PublishedBootstrapError("official release or material selection changed after confirmation; inspect again")
    return document


@dataclass(frozen=True, slots=True)
class _AcquiredReconfigure:
    _key: object
    root: Path
    request: ReconfigureRequest
    plan: str
    materials: str
    files: tuple

    def verify(self, root, plan, materials, *, current=None, under_lock=False):
        if self._key is not _ACQUISITION_KEY:
            raise PublishedBootstrapError("missing current official asset acquisition")
        if (Path(root) != self.root or _canonical(plan) != self.plan
                or _canonical(materials.document) != self.materials):
            raise PublishedBootstrapError("published execution differs from its acquired plan/materials")
        document = _refresh_reconfigure(self.request, current=current, under_lock=under_lock)
        selected = document["selection"]
        _check_target(selected)
        for observation in self.files:
            if _material_observation(observation[0]) != observation:
                raise PublishedBootstrapError("acquired release material changed before preparation")
        if (str(root) != document["root"] or plan["operation"] != "reconfigure"
                or plan["expected_from"] != document["current"]["target"]
                or plan["source"] != selected["source"]
                or plan["components"] != selected["components"]
                or materials.document["published_selection"] != selected["materials"]
                or materials.document["root_extras"] != selected["materials"]["root_extras"]):
            raise PublishedBootstrapError("published execution is outside the exact same-release complete selection")
        return document["selected_frontend"]


@contextmanager
def _acquire_plan_materials(root, document, *, current=None):
    """Acquire the exact official set and construct the original material/plan."""
    from .install_materials import PUBLISHED_MATERIALS_SCHEMA, load_materials
    from .install_v2 import _write_json
    from .published_frontends import (
        FRONTEND_STREAM_SECONDS, MAX_FRONTEND_ASSET_BYTES, unpack_frontend_tree,
    )
    # Cleanup cannot rewrite a completed current commit as a failed install.
    with tempfile.TemporaryDirectory(prefix="rpnh-published-materials-", ignore_cleanup_errors=True) as temporary:
        directory = Path(temporary).resolve()
        if directory.is_relative_to(root) or root.is_relative_to(directory):
            raise PublishedBootstrapError("acquisition directory must be separate from the managed root")
        wheels, acquired_paths = [], []
        for wheel in document["materials"]["python_wheels"]:
            path = directory / wheel["filename"]
            asset = {**wheel["asset"], "name": wheel["filename"]}
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    with _open_wheel(deepcopy(asset)) as response:
                        _stream(response, output, asset["size"])
            except (OSError, HTTPException, ValueError, TypeError):
                raise PublishedBootstrapError("exact official wheel acquisition failed; no installation was activated") from None
            wheels.append({"path": str(path), "name": wheel["name"], "version": wheel["version"]})
            acquired_paths.append(path)
        frontends = {}
        selected_frontends = document["materials"].get("frontends", {})
        if selected_frontends:
            (directory / "frontends").mkdir(mode=0o700)
        for entry, item in selected_frontends.items():
            path = directory / item["filename"]
            asset = {**item["asset"], "name": item["filename"]}
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    with _open_wheel(deepcopy(asset)) as response:
                        _stream(response, output, asset["size"], max_bytes=MAX_FRONTEND_ASSET_BYTES,
                                seconds=FRONTEND_STREAM_SECONDS)
                acquired_paths.append(path)
                if item["kind"] == "native_file":
                    path.chmod(0o700)
                    source = path
                else:
                    source = unpack_frontend_tree(path, directory / "frontends" / entry)
                    acquired_paths.append(source)
                frontend = {"kind": item["kind"], "path": str(source), "artifact": item["artifact"]}
                if item["kind"] == "node_tree":
                    frontend["entry"] = item["entry"]
                frontends[entry] = frontend
            except (OSError, HTTPException, RuntimeError, ValueError, TypeError):
                raise PublishedBootstrapError("exact official frontend acquisition failed; no installation was activated") from None
        rpnh = next(row for row in wheels if row["name"] == "rpnh-harness")
        catalog = contract.load_profile_catalog(wheel=Path(rpnh["path"]))
        plan = contract.build_install_plan(document["source"], document["platform"],
            document["runtime_bindings"], requested=document["components"],
            current=current, catalog=catalog)
        material_path = directory / "materials.json"
        _write_json(material_path, {"schema_version": PUBLISHED_MATERIALS_SCHEMA,
            "platform": document["platform"], "runtime_bindings": document["runtime_bindings"],
            "root_extras": document["materials"]["root_extras"], "published_selection": document["materials"],
            "python_wheels": wheels, "frontends": frontends})
        materials = load_materials(material_path, plan, catalog=catalog)
        yield plan, material_path, materials, tuple(_material_observation(path) for path in acquired_paths)


def bootstrap_published(root: Path, request: BootstrapRequest, *, selected_frontend: str | None = None):
    """Acquire the confirmed exact set, then use the original installation path.

    There is no injected resolver/downloader parameter, receipt input, forced
    gate, fabricated local source or predecessor. Tests replace only the two
    transport seams above and must label that evidence as fixture transport.
    """
    from cpn.update_context import reject_candidate_runtime
    from .install_executor import execute_install
    from .install_v2 import _management_source
    reject_candidate_runtime()
    _management_source()
    root = Path(root).expanduser().absolute()
    if root != root.resolve() or os.path.lexists(root):
        raise PublishedBootstrapError("published bootstrap needs a new dedicated real root")
    if type(request) is not BootstrapRequest:
        raise PublishedBootstrapError("bootstrap requires a current inspected request, not a receipt or material document")
    document = _refresh(request)
    display = _confirmed_display(selected_frontend, document["components"], document["selected_frontend"])
    _check_target(document)
    with _acquire_plan_materials(root, document) as (plan, material_path, materials, files):
        acquired = _AcquiredBootstrap(_ACQUISITION_KEY, root, request, _canonical(plan.to_document()),
            _canonical(materials.document), files)
        return execute_install(root, plan, material_path, selected_frontend=display,
            expected_materials=materials.document, published_context=acquired)


def reconfigure_published(root: Path, request: ReconfigureRequest, *, selected_frontend: str | None = None):
    """Acquire an explicitly confirmed same-R selection for the original root."""
    from cpn.update_context import reject_candidate_runtime
    from .install_executor import execute_install
    from .install_v2 import _management_source
    reject_candidate_runtime()
    _management_source()
    root = Path(root).expanduser().absolute()
    document = _refresh_reconfigure(request)
    if str(root) != document["root"]:
        raise PublishedBootstrapError("reconfiguration request belongs to a different managed root")
    selected = document["selection"]
    display = _confirmed_display(selected_frontend, selected["components"], document["selected_frontend"])
    _check_target(selected)
    with _acquire_plan_materials(root, selected, current=document["current"]["record"]) as (plan, material_path, materials, files):
        acquired = _AcquiredReconfigure(_ACQUISITION_KEY, root, request, _canonical(plan.to_document()),
            _canonical(materials.document), files)
        return execute_install(root, plan, material_path, selected_frontend=display,
            expected_materials=materials.document, published_context=acquired)


__all__ = ("BootstrapRequest", "ReconfigureRequest", "PublishedBootstrapError", "inspect_bootstrap",
           "bootstrap_published", "inspect_reconfigure", "reconfigure_published")
