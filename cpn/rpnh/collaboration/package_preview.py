"""Bounded bytes-only ZIP inspection; no extraction or HOST code preparation."""
from __future__ import annotations

import io
import json
from pathlib import Path
import re
import stat
import struct
import zlib
from urllib.parse import urlsplit
import zipfile

from jsonschema import Draft7Validator, validators

from ..registry.schema_catalog import PROTECTED_SCHEMA_REFS

from .share_packages import (
    DEFAULT_LIMITS, DRAFT7, MANIFEST_PATH, MODULE_SCHEMA, PACKAGE_SCHEMA,
    PARSER_CONTRACT, PREVIEW_SCHEMA, SCHEMA_ID, PackageError, PackagePreview,
    canonical_bytes, manifest_schema, safe_path, sha256, strict_json,
    PACKAGE_SCHEMA_V2, PREVIEW_SCHEMA_V2, PARSER_CONTRACT_V2,
)

_StrictValidator = validators.extend(Draft7Validator, type_checker=Draft7Validator.TYPE_CHECKER.redefine(
    "integer", lambda checker, value: type(value) is int))

# Draft7 schema positions, not arbitrary user property names or default values.
_SCHEMA_MAPS = {"definitions", "properties", "patternProperties"}
_SCHEMA_LISTS = {"allOf", "anyOf", "oneOf"}
_SCHEMA_SINGLE = {"additionalItems", "contains", "additionalProperties", "propertyNames",
                  "if", "then", "else", "not"}
_DRAFT7_KEYWORDS = {
    "$schema", "$id", "$ref", "$comment", "title", "description", "default", "examples",
    "readOnly", "writeOnly", "type", "enum", "const", "multipleOf", "maximum", "exclusiveMaximum",
    "minimum", "exclusiveMinimum", "maxLength", "minLength", "pattern", "format", "items",
    "maxItems", "minItems", "uniqueItems", "maxProperties", "minProperties", "required",
    "dependencies", "contentMediaType", "contentEncoding", *_SCHEMA_MAPS, *_SCHEMA_LISTS, *_SCHEMA_SINGLE,
}


def _check(check_id, status, code, *, artifact_path=None, detail=None):
    return {"check_id": check_id, "status": status, "code": code,
            "artifact_path": artifact_path, "detail": detail}


def _check_zip_directory(payload, limits):
    # Bound the central directory *before* ZipFile allocates one ZipInfo per
    # member. ZIP64, multi-disk, prepended executables and trailing data are not
    # part of this deliberately small interchange profile.
    if not payload.startswith(b"PK\x03\x04"):
        raise PackageError("INVALID_ARCHIVE", "expected an ordinary nonempty ZIP")
    offset = payload.rfind(b"PK\x05\x06", max(0, len(payload) - 65_557))
    if offset < 0 or len(payload) - offset < 22:
        raise PackageError("INVALID_ARCHIVE", "missing ZIP end record")
    _, disk, start_disk, disk_count, count, size, start, comment = struct.unpack_from("<4s4H2LH", payload, offset)
    if (disk or start_disk or disk_count != count or count == 65535
            or start + size != offset or offset + 22 + comment != len(payload)):
        raise PackageError("UNSUPPORTED_ARCHIVE", "ZIP64, multipart or noncanonical ZIP layout")
    if count > limits.members or size > limits.members * 1024:
        raise PackageError("PACKAGE_LIMIT_EXCEEDED", "central directory metadata limit")
    cursor = start
    actual = 0
    while cursor < offset:
        if cursor + 46 > offset or payload[cursor:cursor + 4] != b"PK\x01\x02":
            raise PackageError("INVALID_ARCHIVE", "invalid ZIP central directory")
        name_size, extra_size, comment_size = struct.unpack_from("<3H", payload, cursor + 28)
        if name_size > 240 or extra_size + comment_size > 512:
            raise PackageError("PACKAGE_LIMIT_EXCEEDED", "ZIP member metadata limit")
        cursor += 46 + name_size + extra_size + comment_size
        actual += 1
        if actual > limits.members:
            raise PackageError("PACKAGE_LIMIT_EXCEEDED", "central directory member limit")
    if cursor != offset or actual != count:
        raise PackageError("INVALID_ARCHIVE", "ZIP directory count/size differs")
    return start


def _read_archive(source, limits):
    if isinstance(source, bytes):
        payload = source
    elif isinstance(source, (str, Path)):
        try:
            with Path(source).open("rb") as stream:
                payload = stream.read(limits.archive_bytes + 1)
        except OSError as exc:
            raise PackageError("PACKAGE_UNAVAILABLE", "cannot read local archive") from exc
    else:
        raise TypeError("package source must be a local path or bytes")
    if len(payload) > limits.archive_bytes:
        raise PackageError("PACKAGE_LIMIT_EXCEEDED", "archive byte limit")
    directory_start = _check_zip_directory(payload, limits)
    members = {}
    names = set()
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > limits.members:
                raise PackageError("PACKAGE_LIMIT_EXCEEDED", "archive member limit")
            # Every local record must be represented by the central inventory.
            # Reject hidden local records/gaps and streaming data descriptors in
            # this profile; no executable preamble or unlisted byte trailer.
            position = 0
            for item in sorted(infos, key=lambda value: value.header_offset):
                if item.header_offset != position or position + 30 > directory_start:
                    raise PackageError("INVALID_ARCHIVE", "noncontiguous local ZIP records")
                header = struct.unpack_from("<4s5H3L2H", payload, position)
                signature, _, flags, method, _, _, crc, compressed, expanded_size, name_size, extra_size = header
                if (signature != b"PK\x03\x04" or flags != item.flag_bits or method != item.compress_type
                        or flags & ~(0x800 | 0x6) or crc != item.CRC
                        or compressed != item.compress_size or expanded_size != item.file_size):
                    raise PackageError("UNSUPPORTED_ARCHIVE", "local ZIP metadata or streaming/encrypted member differs")
                if name_size > 240 or extra_size > 512:
                    raise PackageError("PACKAGE_LIMIT_EXCEEDED", "local member metadata limit")
                position += 30 + name_size + extra_size + item.compress_size
                if position > directory_start:
                    raise PackageError("INVALID_ARCHIVE", "local ZIP record overlaps directory")
            if position != directory_start:
                raise PackageError("INVALID_ARCHIVE", "unlisted ZIP local bytes")
            expanded = 0
            for item in infos:
                # orig_filename retains NULs which ZipInfo.filename truncates.
                name = safe_path(item.orig_filename)
                if name.casefold() in names:
                    raise PackageError("UNSAFE_ARCHIVE_PATH", "duplicate or case-colliding member")
                names.add(name.casefold())
                mode = item.external_attr >> 16
                if (item.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                        or item.external_attr & 0x10):
                    raise PackageError("UNSAFE_ARCHIVE_MEMBER", "only regular file members are supported")
                if item.flag_bits & 1 or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise PackageError("UNSUPPORTED_ARCHIVE", "encrypted or unsupported compression")
                expanded += item.file_size
                if (item.file_size > limits.artifact_bytes or expanded > limits.expanded_bytes
                        or item.file_size > max(1, item.compress_size) * limits.compression_ratio):
                    raise PackageError("PACKAGE_LIMIT_EXCEEDED", "expanded size or compression ratio limit")
                with archive.open(item) as stream:
                    data = stream.read(limits.artifact_bytes + 1)
                if len(data) != item.file_size or len(data) > limits.artifact_bytes:
                    raise PackageError("PACKAGE_LIMIT_EXCEEDED", "member byte limit")
                members[name] = data
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, EOFError, UnicodeError, zlib.error) as exc:
        raise PackageError("INVALID_ARCHIVE", "cannot read complete ZIP members") from exc
    if MANIFEST_PATH not in members:
        raise PackageError("MANIFEST_MISSING", "manifest.json is required at ZIP root")
    return payload, members


def _unique(items, key, code):
    result = {}
    for item in items:
        if item[key] in result:
            raise PackageError(code, "duplicate inventory identity")
        result[item[key]] = item
    return result


def _url_claim(value):
    if value is None:
        return
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise PackageError("INVALID_ORIGIN", "invalid location claim") from exc
    if (parts.scheme != "https" or not parts.netloc or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment):
        raise PackageError("INVALID_ORIGIN", "location claims must be credential-free HTTPS URLs without query or fragment")


def _manifest(payload, limits):
    document = strict_json(payload, path=MANIFEST_PATH, limits=limits)
    if not isinstance(document, dict) or document.get("schema_version") not in (PACKAGE_SCHEMA, PACKAGE_SCHEMA_V2):
        raise PackageError("UNSUPPORTED_PACKAGE_SCHEMA", "unsupported manifest schema")
    error = next(_StrictValidator(manifest_schema(document["schema_version"])).iter_errors(document), None)
    if error is not None:
        code = "UNKNOWN_FIELD" if error.validator == "additionalProperties" else "INVALID_MANIFEST"
        raise PackageError(code, "manifest does not satisfy the bounded v1 contract"
                           if document["schema_version"] == PACKAGE_SCHEMA else
                           "manifest does not satisfy the bounded v2 contract", artifact_path=MANIFEST_PATH)
    _url_claim(document["origin"]["repository_url"])
    for dependency in document["dependencies"]:
        _url_claim(dependency["acquisition_hint"])
    _unique(document["dependencies"], "dependency_id", "INVALID_MANIFEST")
    _unique(document["requirements"], "requirement_id", "INVALID_MANIFEST")
    return document


def _inventory(manifest, members, limits):
    artifacts = _unique(manifest["artifacts"], "path", "UNDECLARED_ARTIFACT")
    if set(members) != {MANIFEST_PATH, *artifacts} or MANIFEST_PATH in artifacts:
        raise PackageError("UNDECLARED_ARTIFACT", "archive and manifest inventory differ")
    licenses = _unique(manifest["licenses"], "license_id", "LICENSE_COVERAGE_MISSING")
    provenance = _unique(manifest["provenance"], "artifact_path", "INVALID_PROVENANCE")
    if set(provenance) != set(artifacts):
        raise PackageError("INVALID_PROVENANCE", "provenance must cover exactly every artifact")
    parsed = {}
    for path, artifact in artifacts.items():
        safe_path(path)
        payload = members[path]
        if type(artifact["bytes"]) is not int or artifact["bytes"] != len(payload) or artifact["sha256"] != sha256(payload):
            raise PackageError("DIGEST_MISMATCH", "artifact byte length or digest differs", artifact_path=path)
        if artifact["license_id"] not in licenses:
            raise PackageError("LICENSE_COVERAGE_MISSING", "artifact license is not declared", artifact_path=path)
        if manifest["disclosure"]["classification"] == "public" and artifact["disclosure"] != "public":
            raise PackageError("DISCLOSURE_CONFLICT", "public package contains restricted inventory", artifact_path=path)
        if artifact["role"] in {"schema", "declaration"} and artifact["media_type"] not in {"application/json", "application/schema+json"}:
            raise PackageError("INVALID_MANIFEST", "declaration and schema materials must be JSON", artifact_path=path)
        try:
            payload.decode("utf-8")
        except UnicodeError as exc:
            raise PackageError("INVALID_UTF8", "all v1 material must be UTF-8 text", artifact_path=path) from exc
        if artifact["media_type"] in {"application/json", "application/schema+json"}:
            parsed[path] = strict_json(payload, path=path, limits=limits)
    for license_record in licenses.values():
        for path in (*license_record["text_paths"], *license_record["notice_paths"]):
            if path not in artifacts or artifacts[path]["role"] != "license" or not members[path].strip():
                raise PackageError("LICENSE_COVERAGE_MISSING", "license text/notice must be nonempty inventoried license material")
    for row in provenance.values():
        if row["relation"] in {"copied", "derived"} and row["origin_digest"] is None:
            raise PackageError("INVALID_PROVENANCE", "copied/derived material requires original digest")
        if row["relation"] == "copied" and row["origin_digest"] != artifacts[row["artifact_path"]]["sha256"]:
            raise PackageError("INVALID_PROVENANCE", "copied bytes differ from claimed original digest")
    if manifest["schema_version"] == PACKAGE_SCHEMA_V2:
        from .environment_requirements import validate_environment_requirements
        for entry in manifest["entries"]:
            path = safe_path(entry["environment_requirements_path"])
            row = artifacts.get(path)
            if row is None or row["role"] != "document" or row["media_type"] != "application/json":
                raise PackageError("INVALID_ENVIRONMENT_REQUIREMENTS", "requirements must reference an inventoried JSON document")
            validate_environment_requirements(members[path], entry_id=entry["entry_id"],
                host_requirement_ids=[row["requirement_id"] for row in manifest["requirements"]], limits=limits)
    return artifacts, parsed


def _module(manifest, artifacts, parsed):
    entry = manifest["entries"][0]
    path = entry["declaration_path"]
    if {name for name, row in artifacts.items() if row["role"] == "declaration"} != {path}:
        raise PackageError("INVALID_DECLARATION", "exactly one inventoried closed Module is required")
    module = parsed[path]
    schema_path = Path(__file__).resolve().parents[2] / "schemas/rpnh/module_declaration.v1.schema.json"
    # Fixed product-owned mechanical schema. No catalog discovery or material lower.
    schema = json.loads(schema_path.read_bytes())
    error = next(_StrictValidator(schema).iter_errors(module), None)
    if error is not None:
        code = "UNKNOWN_FIELD" if error.validator == "additionalProperties" else "INVALID_DECLARATION"
        raise PackageError(code, "Module declaration does not satisfy its mechanical schema", artifact_path=path)
    components = _unique(module["components"], "name", "INVALID_DECLARATION")
    ports = {}
    all_schemas = set(module["required_schemas"])
    for component in components.values():
        all_schemas.add(component["config_schema"])
        local_ports = _unique(component["ports"], "name", "INVALID_DECLARATION")
        ports.update({(component["name"], name): row for name, row in local_ports.items()})
        all_schemas.update(row["schema"] for row in local_ports.values())
        for port in local_ports.values():
            minimum = port.get("cardinality_minimum")
            maximum = port.get("cardinality_maximum")
            minimum = port.get("cardinality", 1) if minimum is None else minimum
            maximum = port.get("cardinality", 1) if maximum is None else maximum
            if minimum > maximum:
                raise PackageError("INVALID_DECLARATION", "port cardinality bounds differ")
        operations = _unique(component.get("operations", []), "name", "INVALID_DECLARATION")
        for operation in operations.values():
            for direction, key in (("input", "inputs"), ("output", "outputs")):
                # v2 permits input ports supplied by the explicitly selected
                # trusted component lower (for example native capabilities).
                # They are not boundary endpoints and confer no execution right.
                # Actual internal port existence/schema is checked by compile.
                allow_internal = manifest["schema_version"] == PACKAGE_SCHEMA_V2 and direction == "input"
                if any((name not in local_ports and not allow_internal)
                       or (name in local_ports and local_ports[name]["direction"] != direction)
                       for name in operation[key]):
                    raise PackageError("INVALID_DECLARATION", "operation names an absent or wrong-direction port")
            _unique(operation["outcomes"], "name", "INVALID_DECLARATION")
            for outcome in operation["outcomes"]:
                for product in outcome["products"]:
                    if product["port"] not in operation["outputs"] or product.get("minimum", 1) > product.get("maximum", 1):
                        raise PackageError("INVALID_DECLARATION", "outcome product port/bounds differ")

    def endpoint(value, direction):
        port = ports.get((value["component"], value["port"]))
        if port is None or port["direction"] != direction:
            raise PackageError("INVALID_DECLARATION", "boundary endpoint absent or wrong direction")
        return port

    actual_inputs = {endpoint(value, "input")["schema"] for value in module["entry"].values()}
    actual_outputs = {endpoint(value, "output")["schema"] for value in module["exit"].values()}
    if actual_inputs != set(entry["input_schema_ids"]) or actual_outputs != set(entry["output_schema_ids"]):
        raise PackageError("INVALID_DECLARATION", "entry schema inventory differs from Module boundary")
    for link in module["links"]:
        left, right = endpoint(link["source"], "output"), endpoint(link["target"], "input")
        if left["schema"] != right["schema"]:
            raise PackageError("INVALID_DECLARATION", "linked schemas differ")
    completion = entry["completion_contract"]
    exits = {completion["success_exit"], *completion["failure_exits"]}
    if exits != set(module["exit"]) or completion["success_exit"] in completion["failure_exits"]:
        raise PackageError("INVALID_COMPLETION", "completion must classify exactly every Module exit")
    if module["terminal"]["source"] != module["exit"][completion["success_exit"]]:
        raise PackageError("INVALID_COMPLETION", "success exit differs from declared primary terminal")
    for terminal in [module["terminal"], *module.get("terminal_alternatives", [])]:
        endpoint(terminal["source"], "output")
        component = components[terminal["source"]["component"]]
        operations = {row["name"]: row for row in component.get("operations", [])}
        operation = operations.get(terminal["operation"])
        if operation is None or terminal["source"]["port"] not in operation["outputs"]:
            raise PackageError("INVALID_COMPLETION", "closed Module needs an explicit terminal operation")
        outcome = next((row for row in operation["outcomes"] if row["name"] == terminal["outcome"]), None)
        if outcome is None or terminal["source"]["port"] not in {row["port"] for row in outcome["products"]}:
            raise PackageError("INVALID_COMPLETION", "terminal outcome does not produce its source port")
    required = {row["requirement_id"]: row for row in manifest["requirements"]}
    if any(name not in required or not required[name]["required"] or required[name]["kind"] not in {"analyzer", "terminal"}
           for name in completion["acceptor_requirement_ids"]):
        raise PackageError("INVALID_COMPLETION", "completion acceptor must name a required analyzer/terminal requirement")
    if not all_schemas.issubset(set(module["required_schemas"])):
        raise PackageError("INVALID_DECLARATION", "required_schemas omits a component or port schema")
    needed_contracts = set()
    for component in components.values():
        needed_contracts.add(("component", component["key"]))
        for operation in component.get("operations", []):
            needed_contracts.add(("executor", operation["executor"]))
            needed_contracts.update(("tool", key) for key in operation.get("tools", []))
            for outcome in operation["outcomes"]:
                needed_contracts.update(("effect", row["key"]) for row in outcome.get("effects", []))
    needed_contracts.update(("terminal", row["key"]) for row in [module["terminal"], *module.get("terminal_alternatives", [])])
    needed_contracts.update(("analyzer", key) for key in module.get("analyzers", []))
    stated_contracts = {(row["kind"], row["contract_id"]) for row in required.values() if row["required"]}
    if not needed_contracts.issubset(stated_contracts):
        raise PackageError("HOST_REQUIREMENT_MISSING", "required HOST inventory omits a declared contract")
    if not {contract for _, contract in needed_contracts}.issubset(set(manifest["compatibility"]["host_contracts"])):
        raise PackageError("HOST_REQUIREMENT_MISSING", "compatibility inventory omits a declared HOST contract")
    if MODULE_SCHEMA not in manifest["compatibility"]["declaration_schemas"]:
        raise PackageError("INVALID_MANIFEST", "Module declaration contract absent from compatibility inventory")
    return module, all_schemas


def _schema_locations(document):
    """Enumerate supported schema positions, including boolean schemas.

    A $ref into annotation data is outside this profile, even if that data is
    object-shaped. Rejecting it prevents concealed target-schema dependencies.
    """
    escape = lambda value: value.replace("~", "~0").replace("/", "~1")
    stack = [("", document)]
    while stack:
        pointer, value = stack.pop()
        if not isinstance(value, (dict, bool)):
            continue
        yield pointer, value
        if not isinstance(value, dict):
            continue
        for keyword in _SCHEMA_MAPS:
            if isinstance(value.get(keyword), dict):
                stack.extend((pointer + "/" + keyword + "/" + escape(key), child)
                             for key, child in value[keyword].items())
        for keyword in _SCHEMA_LISTS:
            if isinstance(value.get(keyword), list):
                stack.extend((pointer + "/" + keyword + "/" + str(index), child)
                             for index, child in enumerate(value[keyword]))
        for keyword in _SCHEMA_SINGLE:
            if isinstance(value.get(keyword), (dict, bool)):
                stack.append((pointer + "/" + keyword, value[keyword]))
        items = value.get("items")
        if isinstance(items, list):
            stack.extend((pointer + "/items/" + str(index), child) for index, child in enumerate(items))
        elif isinstance(items, (dict, bool)):
            stack.append((pointer + "/items", items))
        dependencies = value.get("dependencies", {})
        if isinstance(dependencies, dict):
            stack.extend((pointer + "/dependencies/" + escape(key), child)
                         for key, child in dependencies.items() if isinstance(child, (dict, bool)))


def _pointer(document, reference):
    if reference == "":
        return document
    if not reference.startswith("/"):
        raise ValueError("only JSON pointer fragments are supported")
    current = document
    for raw in reference[1:].split("/"):
        if re.search(r"~(?![01])", raw) or "%" in raw:
            raise ValueError("noncanonical JSON pointer escape")
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and re.fullmatch(r"0|[1-9][0-9]*", part) and int(part) < len(current):
            current = current[int(part)]
        else:
            raise ValueError("missing JSON pointer target")
    if not isinstance(current, (dict, bool)):
        raise ValueError("reference target is not a schema")
    return current


def _schema_documents(manifest, artifacts, parsed):
    result = {}
    for path, artifact in artifacts.items():
        if artifact["role"] != "schema":
            continue
        document = parsed[path]
        identity = document.get("$id") if isinstance(document, dict) else None
        if not isinstance(identity, str) or re.fullmatch(SCHEMA_ID, identity) is None:
            raise PackageError("INVALID_SCHEMA", "schema needs its canonical explicit $id", artifact_path=path)
        if identity in PROTECTED_SCHEMA_REFS or identity.startswith(("rpnh/", "registry_v1/")):
            raise PackageError("PROTECTED_SCHEMA_OVERRIDE", "package cannot supply protected mechanical schemas", artifact_path=path)
        if identity in result:
            raise PackageError("SCHEMA_ID_CONTENT_CONFLICT", "duplicate package schema ID", artifact_path=path)
        result[identity] = (document, artifact["sha256"], path)
    return result


def schema_checks(schemas, required_ids):
    """Resolve symbolic schema IDs offline; never invoke a URI resolver."""
    checks = []
    rows = []
    missing = set(required_ids) - set(schemas)
    if missing:
        checks.append(_check("package_schema_closure_resolved", "missing", "SCHEMA_CLOSURE_MISSING",
                             detail=sorted(missing)))
    closure_incompatible = False
    runtime_supported = True
    locations = {identity: dict(_schema_locations(row[0])) for identity, row in schemas.items()}
    for identity, (document, digest, path) in sorted(schemas.items()):
        refs = []
        incompatible = []
        if document.get("$schema") != DRAFT7:
            incompatible.append("unsupported_schema_dialect")
            closure_incompatible = True
        else:
            try:
                Draft7Validator.check_schema(document)
            except Exception:
                incompatible.append("invalid_draft7_schema")
                closure_incompatible = True
        positions = [value for value in locations[identity].values() if isinstance(value, dict)]
        if any(set(position) - _DRAFT7_KEYWORDS for position in positions):
            incompatible.append("unsupported_schema_keyword")
            closure_incompatible = True
        if any("$id" in position for position in positions[1:]):
            incompatible.append("unsupported_nested_schema_id")
            closure_incompatible = True
        for position in positions:
            if "$ref" not in position:
                continue
            reference = position["$ref"]
            if not isinstance(reference, str):
                incompatible.append("unsupported_schema_reference_contract")
                closure_incompatible = True
                continue
            target_id, separator, pointer = reference.partition("#")
            target_id = target_id or identity
            refs.append({"schema_id": target_id, "fragment": pointer if separator else None})
            if not reference.startswith("#/"):
                incompatible.append("unsupported_schema_reference_contract")
            target = schemas.get(target_id)
            if target is None:
                missing.add(target_id)
            else:
                try:
                    _pointer(target[0], pointer if separator else "")
                    if (pointer if separator else "") not in locations[target_id]:
                        incompatible.append("unsupported_schema_reference_target")
                        closure_incompatible = True
                except ValueError:
                    missing.add(reference)
        # Match current resource reader's conservative recursive scanning of all
        # JSON values, including annotations. This is intentionally a second axis.
        stack = [document]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if "$recursiveRef" in value or "$dynamicRef" in value:
                    incompatible.append("unsupported_schema_reference_contract")
                if "$ref" in value:
                    reference = value["$ref"]
                    try:
                        if not isinstance(reference, str) or not reference.startswith("#/"):
                            raise ValueError("nonlocal reference")
                        _pointer(document, reference[1:])
                    except ValueError:
                        incompatible.append("unsupported_schema_reference_contract")
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
        if incompatible:
            runtime_supported = False
            checks.append(_check("runtime_schema_authority_supported", "incompatible",
                                 sorted(set(incompatible))[0], artifact_path=path,
                                 detail=sorted(set(incompatible))))
        rows.append({"schema_id": identity, "artifact_digest": digest, "artifact_path": path,
                     "direct_refs": sorted(refs, key=lambda row: (row["schema_id"], row["fragment"] is not None, row["fragment"] or ""))})
    checks = [row for row in checks if row["check_id"] != "package_schema_closure_resolved"]
    checks.append(_check("package_schema_closure_resolved",
                         "missing" if missing else "incompatible" if closure_incompatible else "satisfied",
                         "SCHEMA_CLOSURE_MISSING" if missing else "UNSUPPORTED_SCHEMA_CONTRACT" if closure_incompatible else "SCHEMA_CLOSURE_RESOLVED",
                         detail=sorted(missing) if missing else None))
    if runtime_supported:
        checks.append(_check("runtime_schema_authority_supported", "missing" if missing else "satisfied",
                             "SCHEMA_CLOSURE_MISSING" if missing else "SELF_CONTAINED_DRAFT7_SUPPORTED"))
    return checks, rows


def inspect_materials(manifest_bytes, members, limits=DEFAULT_LIMITS):
    """Internal pure validation shared by preview and exact resolution."""
    manifest = _manifest(manifest_bytes, limits)
    artifacts, parsed = _inventory(manifest, members, limits)
    _, required = _module(manifest, artifacts, parsed)
    schemas = _schema_documents(manifest, artifacts, parsed)
    return manifest, schemas, required


def preview_package(source, *, limits=DEFAULT_LIMITS) -> PackagePreview:
    """Inspect a local ZIP/path or ZIP bytes without writing or executing it.

    Malformed material raises PackageError. Structurally valid but missing or
    unsupported schema material remains previewable with explicit check axes.
    """
    archive_bytes, members = _read_archive(source, limits)
    manifest_bytes = members[MANIFEST_PATH]
    manifest, schemas, required = inspect_materials(manifest_bytes, members, limits)
    checks, rows = schema_checks(schemas, required)
    checks.extend([
        _check("material_integrity", "satisfied", "INVENTORY_VERIFIED"),
        _check("module_boundary", "satisfied", "DECLARED_BOUNDARY_CHECKED"),
        _check("host_readiness", "not_checked", "HOST_PREPARATION_REQUIRED"),
        _check("origin_authority", "not_checked", "ORIGIN_UNVERIFIED"),
        _check("license_permissions", "not_checked", "LICENSE_REVIEW_REQUIRED"),
        _check("sensitive_content", "not_checked", "CONTENT_REVIEW_REQUIRED"),
        _check("revocation", "not_checked", "REVOCATION_UNKNOWN"),
    ])
    if manifest["dependencies"]:
        checks.append(_check("dependency_resolution", "not_checked", "LOCAL_RESOLUTION_REQUIRED"))
    else:
        checks.append(_check("dependency_resolution", "satisfied", "NO_DEPENDENCIES"))
    is_v2 = manifest["schema_version"] == PACKAGE_SCHEMA_V2
    if is_v2:
        checks.append(_check("environment_requirements", "satisfied", "ENVIRONMENT_REQUIREMENTS_DECLARED"))
        checks.append(_check("internal_port_binding", "not_checked", "TRUSTED_HOST_COMPILATION_REQUIRED"))
    report = {"schema_version": PREVIEW_SCHEMA_V2 if is_v2 else PREVIEW_SCHEMA,
        "parser_contract": PARSER_CONTRACT_V2 if is_v2 else PARSER_CONTRACT,
        "package_id": manifest["package_id"], "version": manifest["version"],
        "manifest_digest": sha256(manifest_bytes), "archive_digest": sha256(archive_bytes),
        "entries": manifest["entries"], "artifacts": manifest["artifacts"],
        "licenses": manifest["licenses"], "requirements": manifest["requirements"],
        "dependencies": manifest["dependencies"], "origin": manifest["origin"],
        "provenance": manifest["provenance"], "schema_inventory": rows,
        "checks": checks, "execution_permitted": False}
    return PackagePreview(manifest_bytes, archive_bytes,
                          tuple(sorted((key, value) for key, value in members.items() if key != MANIFEST_PATH)),
                          canonical_bytes(report))


def preview_package_v1(source, *, limits=DEFAULT_LIMITS) -> PackagePreview:
    """Explicit legacy reader; never interpret a v2 environment declaration."""
    archive_bytes, members = _read_archive(source, limits)
    document = strict_json(members[MANIFEST_PATH], path=MANIFEST_PATH, limits=limits)
    if type(document) is not dict or document.get("schema_version") != PACKAGE_SCHEMA:
        raise PackageError("UNSUPPORTED_PACKAGE_SCHEMA", "legacy reader supports package v1 only")
    return preview_package(archive_bytes, limits=limits)
