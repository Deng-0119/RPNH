"""Offline portable-package boundaries, malformed data and zero execution."""
from dataclasses import FrozenInstanceError, replace
import io
import json
from pathlib import Path
import socket
import stat
import struct
import subprocess
import zipfile

import pytest

from cpn.rpnh.collaboration.package_cli import main
from cpn.rpnh.collaboration.package_preview import preview_package
from cpn.rpnh.collaboration.package_resolution import resolve_package
from cpn.rpnh.collaboration.share_packages import (
    DEFAULT_LIMITS, PackageError, PackagePreview, canonical_bytes, manifest_schema, sha256,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "cpn/examples/portable_packages/minimal"


def material():
    return {path.relative_to(EXAMPLE).as_posix(): path.read_bytes()
            for path in EXAMPLE.rglob("*") if path.is_file()}


def archive(files, *, compression=zipfile.ZIP_STORED, extra=()):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as writer:
        for name, data in [*sorted(files.items()), *extra]:
            writer.writestr(name, data)
    return output.getvalue()


def revise(files, *, manifest=None, documents=None):
    files = dict(files)
    body = json.loads(files["manifest.json"])
    for name, value in (documents or {}).items():
        files[name] = value if isinstance(value, bytes) else canonical_bytes(value)
    for row in body["artifacts"]:
        if row["path"] in files:
            row["bytes"] = len(files[row["path"]])
            row["sha256"] = sha256(files[row["path"]])
    if manifest:
        manifest(body)
    files["manifest.json"] = canonical_bytes(body)
    return files


def check(report, name):
    return [row for row in report["checks"] if row["check_id"] == name]


def test_complete_sample_preview_and_lock_are_immutable_and_detached():
    raw = archive(material())
    preview = preview_package(raw)
    report = preview.to_dict()
    assert check(report, "package_schema_closure_resolved")[0]["status"] == "satisfied"
    assert check(report, "runtime_schema_authority_supported")[0]["status"] == "satisfied"
    assert report["execution_permitted"] is False
    assert report["origin"] == json.loads(material()["manifest.json"])["origin"]
    assert report["manifest_digest"] == sha256(material()["manifest.json"])
    assert report["archive_digest"] == sha256(raw)
    report["origin"]["source_refs"].append("mutated")
    assert preview.to_dict()["origin"]["source_refs"] == []
    with pytest.raises(FrozenInstanceError):
        preview.manifest_bytes = b"changed"
    with pytest.raises(TypeError):
        PackagePreview(preview.manifest_bytes, raw, [], preview.report_bytes)
    lock = resolve_package(preview)
    assert lock.package_lock_digest == sha256(lock.to_bytes())
    lock.to_dict()["nodes"].clear()
    assert len(lock.to_dict()["nodes"]) == 1
    assert lock.to_bytes() == resolve_package(preview).to_bytes()


def test_no_registry_plugin_lower_factory_network_process_or_extraction(monkeypatch):
    import cpn.rpnh.compiler as compiler
    import cpn.rpnh.module as module
    import cpn.rpnh.collaboration.materials as author
    import cpn.rpnh.registration as registration
    import cpn.plugins.catalog as catalog
    import cpn.rpnh.registry._registry as registry
    import cpn.rpnh.registry.schema_catalog as schema_catalog

    def forbidden(*args, **kwargs):
        raise AssertionError("preview/resolve executed a forbidden boundary")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(compiler, "compile_module", forbidden)
    monkeypatch.setattr(author, "compile_module", forbidden)
    monkeypatch.setattr(author.ClosedModuleAuthor, "publish", forbidden)
    monkeypatch.setattr(module.ModuleDeclaration, "lower", forbidden)
    monkeypatch.setattr(registration.Registration, "__init__", forbidden)
    monkeypatch.setattr(registry._RegistryCore, "__init__", forbidden)
    monkeypatch.setattr(schema_catalog.SchemaCatalog, "__init__", forbidden)
    monkeypatch.setattr(catalog, "load_catalog", forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "extract", forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", forbidden)
    preview = preview_package(archive(material()))
    assert resolve_package(preview).to_dict()["execution_permitted"] is False


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a/../b", "a//b", "./a", "a\\b", "C:drive", "nul.txt", "a.", "caf\u00e9", "dir/"])
def test_archive_rejects_unsafe_names(name):
    with pytest.raises(PackageError, match="UNSAFE_ARCHIVE_PATH"):
        preview_package(archive(material(), extra=[(name, b"text")]))


def test_archive_rejects_case_collisions_and_symlinks():
    with pytest.raises(PackageError, match="UNSAFE_ARCHIVE_PATH"):
        preview_package(archive(material(), extra=[("Manifest.json", b"{}")]))
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(PackageError, match="UNSAFE_ARCHIVE_MEMBER"):
        preview_package(archive(material(), extra=[(link, b"target")]))


def test_archive_rejects_duplicate_names():
    with pytest.warns(UserWarning, match="Duplicate name"):
        raw = archive(material(), extra=[("manifest.json", b"{}")])
    with pytest.raises(PackageError, match="UNSAFE_ARCHIVE_PATH"):
        preview_package(raw)


@pytest.mark.parametrize("raw", [b"", b"not a zip", b"PK\x03\x04", b"MZ" + archive(material())])
def test_invalid_archive(raw):
    with pytest.raises(PackageError, match="INVALID_ARCHIVE"):
        preview_package(raw)


def test_invalid_utf8_zip_filename_returns_structured_cli_error(tmp_path, capsys):
    raw = bytearray(archive(material()))
    directory = raw.index(b"PK\x01\x02")
    # Both records declare UTF-8, but the member name contains an invalid byte.
    for flag_offset, name_offset in [(6, 30), (directory + 8, directory + 46)]:
        flags = struct.unpack_from("<H", raw, flag_offset)[0]
        struct.pack_into("<H", raw, flag_offset, flags | 0x800)
        raw[name_offset] = 0xff
    path = tmp_path / "invalid-name.zip"
    path.write_bytes(raw)
    assert main(["preview", str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "INVALID_ARCHIVE"


def test_archive_resource_bounds_and_directory_count_are_checked():
    raw = archive(material())
    for limits in [replace(DEFAULT_LIMITS, archive_bytes=10), replace(DEFAULT_LIMITS, artifact_bytes=10),
                   replace(DEFAULT_LIMITS, expanded_bytes=10), replace(DEFAULT_LIMITS, members=1)]:
        with pytest.raises(PackageError, match="PACKAGE_LIMIT_EXCEEDED"):
            preview_package(raw, limits=limits)
    altered = bytearray(raw)
    offset = altered.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", altered, offset + 8, 1, 1)
    with pytest.raises(PackageError, match="INVALID_ARCHIVE"):
        preview_package(bytes(altered))
    bomb = archive({**material(), "large.txt": b"0" * 100_000}, compression=zipfile.ZIP_DEFLATED)
    with pytest.raises(PackageError, match="PACKAGE_LIMIT_EXCEEDED"):
        preview_package(bomb)


@pytest.mark.parametrize("payload", [b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":Infinity}',
    b'{"x":1e999}', b'\xef\xbb\xbf{}', b'\xff', b'{"x":"\\ud800"}', b'{} trailing'])
def test_strict_json_rejects_invalid_manifest_bytes(payload):
    files = material(); files["manifest.json"] = payload
    with pytest.raises(PackageError, match="INVALID_JSON"):
        preview_package(archive(files))


def test_json_depth_and_nodes_are_bounded():
    with pytest.raises(PackageError, match="PACKAGE_LIMIT_EXCEEDED"):
        preview_package(archive(material()), limits=replace(DEFAULT_LIMITS, json_depth=2))
    with pytest.raises(PackageError, match="PACKAGE_LIMIT_EXCEEDED"):
        preview_package(archive(material()), limits=replace(DEFAULT_LIMITS, json_nodes=10))


@pytest.mark.parametrize("mutation,code", [
    (lambda m: m.update(install="echo forbidden"), "UNKNOWN_FIELD"),
    (lambda m: m["entries"][0].update(hook="evil"), "UNKNOWN_FIELD"),
    (lambda m: m.update(schema_version="rpnh/share_package/v9"), "UNSUPPORTED_PACKAGE_SCHEMA"),
    (lambda m: m["artifacts"][0].update(bytes=True), "INVALID_MANIFEST"),
    (lambda m: m["artifacts"][0].update(sha256="0" * 64), "DIGEST_MISMATCH"),
    (lambda m: m["artifacts"][0].update(license_id="missing"), "LICENSE_COVERAGE_MISSING"),
    (lambda m: m["licenses"][0].update(text_paths=["missing.txt"]), "LICENSE_COVERAGE_MISSING"),
    (lambda m: m["provenance"].pop(), "INVALID_PROVENANCE"),
    (lambda m: m["provenance"][0].update(relation="copied", origin_digest="0" * 64), "INVALID_PROVENANCE"),
    (lambda m: m["origin"].update(repository_url="https://user:secret@example.com"), "INVALID_ORIGIN"),
    (lambda m: m["origin"].update(repository_url="https://example.com?token=secret"), "INVALID_ORIGIN"),
    (lambda m: m["origin"].update(repository_url="https://[malformed"), "INVALID_ORIGIN"),
    (lambda m: m["entries"][0]["completion_contract"].update(success_exit="absent"), "INVALID_COMPLETION"),
    (lambda m: m["requirements"].clear(), "INVALID_COMPLETION"),
    (lambda m: m["requirements"].pop(0), "HOST_REQUIREMENT_MISSING"),
])
def test_manifest_integrity_and_boundary_rejections(mutation, code):
    with pytest.raises(PackageError, match=code):
        preview_package(archive(revise(material(), manifest=mutation)))


def test_undeclared_missing_non_utf8_and_invalid_json_material():
    files = material(); files["extra.txt"] = b"extra"
    with pytest.raises(PackageError, match="UNDECLARED_ARTIFACT"):
        preview_package(archive(files))
    files = material(); files.pop("schemas/text.json")
    with pytest.raises(PackageError, match="UNDECLARED_ARTIFACT"):
        preview_package(archive(files))
    files = revise(material(), documents={"licenses/LICENSE.txt": b"\xff"})
    with pytest.raises(PackageError, match="INVALID_UTF8"):
        preview_package(archive(files))
    files = revise(material(), documents={"schemas/text.json": b'{"x":NaN}'})
    with pytest.raises(PackageError, match="INVALID_JSON"):
        preview_package(archive(files))


def test_unknown_module_fields_wrong_endpoint_and_schema_inventory_rejected():
    base = json.loads(material()["declarations/main.json"])
    for mutate, code in [(lambda m: m.update(hook="evil"), "UNKNOWN_FIELD"),
                         (lambda m: m["entry"]["request"].update(port="absent"), "INVALID_DECLARATION"),
                         (lambda m: m["required_schemas"].pop(0), "INVALID_DECLARATION"),
                         (lambda m: m["components"][0]["ports"][0].update(cardinality=1.0), "INVALID_DECLARATION")]:
        value = json.loads(json.dumps(base)); mutate(value)
        files = revise(material(), documents={"declarations/main.json": value})
        with pytest.raises(PackageError, match=code):
            preview_package(archive(files))


def schema_package(extra):
    schema = json.loads(material()["schemas/text.json"]); schema.update(extra)
    return archive(revise(material(), documents={"schemas/text.json": schema}))


def test_local_json_pointer_and_recursive_local_structure_supported():
    for extra in [{"$ref": "#/definitions/text", "definitions": {"text": {"type": "string"}}},
                  {"type": "object", "definitions": {"tree": {"type": "object", "properties": {"next": {"$ref": "#/definitions/tree"}}}}, "$ref": "#/definitions/tree"}]:
        report = preview_package(schema_package(extra)).to_dict()
        assert check(report, "package_schema_closure_resolved")[0]["status"] == "satisfied"
        assert check(report, "runtime_schema_authority_supported")[0]["status"] == "satisfied"


def test_closed_cross_document_refs_are_not_runtime_compatible():
    raw = schema_package({"$ref": "application/portable_config/v1"})
    report = preview_package(raw).to_dict()
    assert check(report, "package_schema_closure_resolved")[0]["status"] == "satisfied"
    assert check(report, "runtime_schema_authority_supported")[0]["code"] == "unsupported_schema_reference_contract"
    lock = resolve_package(raw).to_dict()
    assert any(row["status"] == "incompatible" for row in lock["compatibility_results"])
    assert lock["execution_permitted"] is False


@pytest.mark.parametrize("extra", [{"$ref": "#/definitions/missing"}, {"$ref": "application/absent/v1"},
                                     {"$ref": "https://example.invalid/schema"}])
def test_missing_schema_refs_never_network(extra, monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network during schema lookup"))
    raw = schema_package(extra)
    assert check(preview_package(raw).to_dict(), "package_schema_closure_resolved")[0]["status"] == "missing"
    with pytest.raises(PackageError, match="SCHEMA_CLOSURE_MISSING"):
        resolve_package(raw)


@pytest.mark.parametrize("extra", [{"$schema": "https://json-schema.org/draft/2020-12/schema"},
    {"$dynamicRef": "#node"}, {"x-behavior": True}, {"definitions": {"nested": {"$id": "nested"}}}, {"type": "not-a-type"}])
def test_unsupported_schema_semantics_report_incompatible(extra):
    raw = schema_package(extra)
    report = preview_package(raw).to_dict()
    assert check(report, "package_schema_closure_resolved")[0]["status"] == "incompatible"
    assert check(report, "runtime_schema_authority_supported")[0]["status"] == "incompatible"
    with pytest.raises(PackageError, match="UNSUPPORTED_SCHEMA_CONTRACT"):
        resolve_package(raw)


@pytest.mark.parametrize("schema_id", ["rpnh/module_declaration/v1",
    "runtime/llm_request_envelope/v1", "runtime/llm_response_envelope/v1"])
def test_protected_schema_override_rejected(schema_id):
    raw = schema_package({"$id": schema_id})
    with pytest.raises(PackageError, match="PROTECTED_SCHEMA_OVERRIDE"):
        preview_package(raw)
    with pytest.raises(PackageError, match="PROTECTED_SCHEMA_OVERRIDE"):
        resolve_package(raw)


def test_raw_manifest_archive_and_lock_digest_domains():
    stored = preview_package(archive(material()))
    compressed = preview_package(archive(material(), compression=zipfile.ZIP_DEFLATED))
    assert stored.manifest_digest == compressed.manifest_digest
    assert stored.archive_digest != compressed.archive_digest
    assert resolve_package(stored).package_lock_digest != resolve_package(compressed).package_lock_digest
    files = material(); body = json.loads(files["manifest.json"])
    files["manifest.json"] = canonical_bytes(body)
    assert preview_package(archive(files)).manifest_digest != stored.manifest_digest


def test_cli_stdout_is_json_and_lock_raw_digest_domain(tmp_path, capsys):
    path = tmp_path / "sample.zip"; path.write_bytes(archive(material()))
    assert main(["preview", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["execution_permitted"] is False
    assert main(["resolve", str(path)]) == 0
    emitted = capsys.readouterr().out.encode("ascii")
    assert sha256(emitted) == resolve_package(path).package_lock_digest
    assert main(["resolve", str(path), "--entry", "missing"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "ENTRY_NOT_FOUND"


def test_manifest_schema_is_detached():
    first = manifest_schema(); first["properties"].clear()
    assert "entries" in manifest_schema()["properties"]


@pytest.mark.parametrize("extra", [
    {"$ref": "#/default", "default": {"$ref": "application/absent/v1"}},
    {"$ref": "#/examples/0", "examples": [{"$ref": "application/absent/v1"}]},
    {"$ref": "#/default", "default": True},
])
def test_ref_into_annotation_is_not_a_resolved_schema_closure(extra):
    raw = schema_package(extra)
    report = preview_package(raw).to_dict()
    assert check(report, "package_schema_closure_resolved")[0]["status"] == "incompatible"
    with pytest.raises(PackageError, match="UNSUPPORTED_SCHEMA_CONTRACT"):
        resolve_package(raw)


def test_cross_document_ref_into_annotation_is_rejected():
    text = json.loads(material()["schemas/text.json"])
    text["$ref"] = "application/portable_config/v1#/default"
    config = json.loads(material()["schemas/config.json"])
    config["default"] = {"$ref": "application/absent/v1"}
    raw = archive(revise(material(), documents={"schemas/text.json": text, "schemas/config.json": config}))
    assert check(preview_package(raw).to_dict(), "package_schema_closure_resolved")[0]["status"] == "incompatible"
    with pytest.raises(PackageError, match="UNSUPPORTED_SCHEMA_CONTRACT"):
        resolve_package(raw)


def test_reference_to_a_recognized_boolean_schema_is_supported():
    raw = schema_package({"$ref": "#/definitions/anything", "definitions": {"anything": True}})
    assert check(preview_package(raw).to_dict(), "package_schema_closure_resolved")[0]["status"] == "satisfied"
    assert resolve_package(raw).to_dict()["execution_permitted"] is False


def test_reference_pointer_escapes_and_properties_positions():
    raw = schema_package({"$ref": "#/properties/a~1b~0c", "properties": {"a/b~c": {"type": "string"}}})
    assert check(preview_package(raw).to_dict(), "package_schema_closure_resolved")[0]["status"] == "satisfied"
    assert resolve_package(raw).to_dict()["execution_permitted"] is False


def test_hidden_local_bytes_and_mismatched_local_metadata_are_rejected():
    raw = archive(material())
    end = raw.rfind(b"PK\x05\x06")
    directory = struct.unpack_from("<L", raw, end + 16)[0]
    hidden = bytearray(raw[:directory] + b"JUNK" + raw[directory:])
    struct.pack_into("<L", hidden, end + 4 + 16, directory + 4)
    with pytest.raises(PackageError, match="INVALID_ARCHIVE"):
        preview_package(bytes(hidden))
    changed = bytearray(raw)
    struct.pack_into("<L", changed, 22, 1)
    with pytest.raises(PackageError, match="UNSUPPORTED_ARCHIVE"):
        preview_package(bytes(changed))


def test_streaming_zip_descriptors_are_explicitly_unsupported():
    class Streaming(io.BytesIO):
        def seek(self, *args):
            raise io.UnsupportedOperation("not seekable")
    stream = Streaming()
    with zipfile.ZipFile(stream, "w") as writer:
        for name, data in material().items():
            writer.writestr(name, data)
    with pytest.raises(PackageError, match="UNSUPPORTED_ARCHIVE"):
        preview_package(stream.getvalue())


def test_direct_ref_order_distinguishes_absent_and_empty_fragments():
    raw = schema_package({"allOf": [{"$ref": "application/portable_config/v1"},
                                    {"$ref": "application/portable_config/v1#"}]})
    rows = preview_package(raw).to_dict()["schema_inventory"]
    row = next(row for row in rows if row["schema_id"] == "application/portable_text/v1")
    assert [ref["fragment"] for ref in row["direct_refs"]] == [None, ""]
