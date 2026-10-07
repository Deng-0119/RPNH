"""Build real neutral wheel and exact v2 P0/P1 artifacts for joint acceptance.

Helpers construct author material only. They never emulate a business worker or
terminal, install packages, create a Registry, or execute the story handlers.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import importlib
import io
import json
from pathlib import Path
import sys
import zipfile

from cpn.rpnh.collaboration.share_packages import canonical_bytes, sha256
from cpn.rpnh.collaboration.environment_requirements import read_package_environment
from cpn.rpnh.collaboration.environment_local_contracts import EnvironmentSelection, EnvironmentResolutionLock
from cpn.rpnh.collaboration.environment_plan import ConcreteSelections, wheel_profile_fingerprint

SOURCE = Path(__file__).parent / "fixtures/environment_neutral"
PROFILE_ID = "rpnh-neutral-fixture/v1"


def author_module():
    # This is this repository's explicitly trusted author fixture, not package
    # supplied Python. Installed wheel contains byte-identical module source.
    text = str(SOURCE)
    if text not in sys.path:
        sys.path.insert(0, text)
    return importlib.import_module("rpnh_neutral_fixture")


def build_fixture_wheel(directory):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    name, version = "rpnh_neutral_fixture", "1.0"
    info = name + "-" + version + ".dist-info"
    payloads = {
        "rpnh_neutral_fixture/__init__.py": (SOURCE / "rpnh_neutral_fixture/__init__.py").read_bytes(),
        info + "/METADATA": b"Metadata-Version: 2.1\nName: rpnh-neutral-fixture\nVersion: 1.0\nRequires-Python: >=3.11\nRequires-Dist: rpnh-harness>=0.1.0rc1,<1\nRequires-Dist: numpy>=1.26,<3\nRequires-Dist: packaging>=24,<27\n\n",
        info + "/rpnh_environment_plugins.json": canonical_bytes({"schema_version":"rpnh/installed_plugin_metadata/v1","plugins":[{"plugin_id":"neutral_fixture","version":"1.0","api_contract":"rpnh/plugin/v1","entry_point":"neutral_fixture"}]}),
        info + "/WHEEL": b"Wheel-Version: 1.0\nGenerator: rpnh-neutral-acceptance\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        info + "/entry_points.txt": b"[rpnh.environment_hosts]\nrpnh-neutral-fixture/v1 = rpnh_neutral_fixture:profile\n\n[rpnh.plugins]\nneutral_fixture = rpnh_neutral_fixture:definition\n\n[rpnh.environment_probes]\nrpnh-neutral-fixture/v1 = rpnh_neutral_fixture:probes\n",
    }
    records = io.StringIO(); writer = csv.writer(records, lineterminator="\n")
    for name, payload in sorted(payloads.items()):
        writer.writerow([name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode("ascii"), len(payload)])
    writer.writerow([info + "/RECORD", "", ""])
    payloads[info + "/RECORD"] = records.getvalue().encode("utf-8")
    path = directory / "rpnh_neutral_fixture-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in sorted(payloads.items()):
            item = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)); item.external_attr = 0o100644 << 16
            archive.writestr(item, payload)
    return path


def build_story(directory, story, variant):
    if story not in {"numbers", "files"} or variant not in {"P0", "P1"}:
        raise ValueError("frozen story identity required")
    from cpn.plugins.runtime import build_plugin_module, plugin_registration
    from cpn.plugins.host import BINDING_SCHEMA, COMPONENT_KEY, TERMINAL_KEY, schema_key
    from cpn.rpnh.collaboration.package_preview import preview_package
    from cpn.rpnh.collaboration.package_resolution import resolve_package
    fixture = author_module()
    rule = {"story": story, "variant": variant, "rule_version": "1.0",
            "factor": 2 if story == "numbers" and variant == "P1" else 1,
            "exclude_empty": story == "files" and variant == "P1"}
    catalog = fixture.catalog({"rule": rule}); selector = "neutral_fixture/" + story
    module = build_plugin_module(catalog, selector)
    registration = plugin_registration(catalog)
    rows = [{"requirement_id": "component", "kind": "component", "contract_id": COMPONENT_KEY, "required": True,"effects":[],"data_classes":[]},
            {"requirement_id": "executor", "kind": "executor", "contract_id": catalog.operation_key(selector),"required": True,"effects":[],"data_classes":[]},
            {"requirement_id": "finish", "kind": "terminal", "contract_id": TERMINAL_KEY,"required": True,"effects":[],"data_classes":[]}]
    environment = {"schema_version":"rpnh/environment_requirements/v1","entry_id":"main",
        "python":{"requirement_id":"python","implementation":"cpython","version_specifier":">=3.11,<4"},
        "distributions":[{"requirement_id":"harness","name":"rpnh-harness","version_specifier":">=0.1.0rc1,<1","satisfies_host_requirement_ids":["component","executor","finish"]},
            {"requirement_id":"fixture","name":"rpnh-neutral-fixture","version_specifier":"==1.0","satisfies_host_requirement_ids":["executor"]},
            {"requirement_id":"extra","name":"numpy" if story=="numbers" else "packaging","version_specifier":">=1.26,<3" if story=="numbers" else ">=24,<27","satisfies_host_requirement_ids":["executor"]}],
        "system_requirements":[],"tools":[],"services":[],
        "plugins":[{"requirement_id":"plugin","plugin_id":"neutral_fixture","api_contract":"rpnh/plugin/v1","version_specifier":"==1.0","distribution_requirement_id":"fixture","satisfies_host_requirement_ids":["executor"]}]}
    payloads = {"declarations/main.json": canonical_bytes(module.to_dict()), "environment/requirements.json":canonical_bytes(environment),
                "business/rule.json":canonical_bytes(rule), "licenses/LICENSE.txt":b"MIT\nSynthetic acceptance material.\n"}
    for index, schema_id in enumerate(module.required_schemas):
        payloads[f"schemas/schema{index}.json"] = canonical_bytes(registration.declaration("schema",schema_id)["schema"])
    artifacts=[]
    for path,payload in sorted(payloads.items()):
        role = "declaration" if path.startswith("declarations/") else "schema" if path.startswith("schemas/") else "license" if path.startswith("licenses/") else "document"
        media = "application/schema+json" if role=="schema" else "text/plain" if role=="license" else "application/json"
        artifacts.append({"path":path,"media_type":media,"bytes":len(payload),"sha256":sha256(payload),"role":role,"license_id":"mit","disclosure":"public"})
    manifest={"schema_version":"rpnh/share_package/v2","package_id":"acceptance/"+story,"version":"1.0.0" if variant=="P0" else "1.1.0",
        "entries":[{"entry_id":"main","kind":"closed_module","declaration_path":"declarations/main.json","declaration_schema":"rpnh/module_declaration/v1",
            "environment_requirements_path":"environment/requirements.json","input_schema_ids":[schema_key(catalog,selector,"input")],"output_schema_ids":[schema_key(catalog,selector,"output")],
            "completion_contract":{"success_exit":"result","failure_exits":[],"acceptor_requirement_ids":["finish"],"open_obligations":[]}}],
        "artifacts":artifacts,"origin":{"repository_url":None,"commit":None,"publisher_claim":"Synthetic offline acceptance fixture","source_refs":[]},
        "provenance":[{"artifact_path":row["path"],"relation":"authored","origin_ref":None,"origin_digest":None} for row in artifacts],"dependencies":[],
        "compatibility":{"declaration_schemas":["rpnh/module_declaration/v1"],"runtime_contracts":[],"host_contracts":[r["contract_id"] for r in rows]},
        "requirements":rows,"policy_surface":[],"licenses":[{"license_id":"mit","expression":"MIT","text_paths":["licenses/LICENSE.txt"],"notice_paths":[]}],
        "disclosure":{"classification":"public","intended_audience":"public synthetic acceptance","excluded_categories":["secrets","local_bindings","runtime_evidence"]}}
    payloads["manifest.json"] = canonical_bytes(manifest)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    archive_path=directory/f"{story}-{variant}.zip"
    with zipfile.ZipFile(archive_path,"w",compression=zipfile.ZIP_STORED) as archive:
        for name,payload in sorted(payloads.items()):
            item=zipfile.ZipInfo(name,(2026,1,1,0,0,0));item.external_attr=0o100644<<16
            archive.writestr(item,payload)
    preview=preview_package(archive_path);lock=resolve_package(preview)
    lock_path=directory/f"{story}-{variant}.lock.json";lock_path.write_bytes(lock.to_bytes())
    req=read_package_environment(archive_path,package_lock=lock)
    arguments={"values":[3,7,11,15],"unit":"m"} if story=="numbers" else {"files":{"alpha.txt":"alpha\n","beta.txt":"beta\n","empty.txt":""}}
    owner_input={"schema_id":schema_key(catalog,selector,"input"),"payload":arguments,"summary":"Frozen synthetic "+story+" input"}
    owner_request={"task_input":owner_input,"entry_inputs":{"request":owner_input},"resource_inputs":{},"inventory_input":None,
        "budgets":{"budget_buckets":module.to_dict()["budget_buckets"],"protocol_versions":["rpnh/module_declaration/v1"],"ordinary_global_cap":1,"terminal_quota":0,"task_total_hard_cap":1,"finalization_budget":0},
        "model_condition":"rpnh-offline/neutral-fixture-v1","owner_statement":"Run only this declared synthetic offline story through the existing owner", "owner_name":"Synthetic fixture owner","command_id":"neutral-"+story+"-"+variant}
    request_path=directory/f"{story}-{variant}.owner.json";request_path.write_bytes(canonical_bytes(owner_request))
    return {"archive":archive_path,"lock":lock_path,"owner_request_path":request_path,"owner_request":owner_request,"requirements":req,"preview":preview,"module":module}


def select_story(story, executable, *, prefix=None):
    req=story["requirements"]
    return EnvironmentSelection.from_dict({"schema_version":"rpnh/environment_selection/v1","selection_id":"neutral-fixture",
        "target":req.target.to_dict(),"mode":"existing" if prefix is None else "new_venv",
        "python_selection":{"executable":str(executable)} if prefix is None else {"base_executable":str(executable),"prefix":str(prefix)},
        "tools":[],"services":[],"host_profile_id":PROFILE_ID,"plugins":[{"scoped_requirement_id":req.requirements[0].scoped_id("plugin"),"plugin_id":"neutral_fixture","configuration_ref":str(story["archive"].absolute())}]})


def resolve_story_plugin(concrete, story, fixture_wheel):
    """Compatibility helper using the SAME ordinary wheel metadata reader.

    General resolve_local_wheels already incorporates this metadata. This helper
    is retained for callers that resolved before supplying the fixture wheel.
    """
    from cpn.rpnh.collaboration.environment_plan import wheel_plugin_identity
    d=concrete.resolution.to_dict();scoped=story["requirements"].requirements[0]
    requirement=scoped.document["plugins"][0];scope=scoped.scoped_id("plugin")
    binding={"scoped_requirement_id":scope,"plugin_id":"neutral_fixture","configuration_ref":str(story["archive"].absolute())}
    identity=wheel_plugin_identity(fixture_wheel,requirement,binding,"rpnh-neutral-fixture")
    if identity is None:
        return concrete
    d["selections"]=[r for r in d["selections"] if not (r["kind"]=="plugin" and r["scoped_requirement_id"]==scope)]
    d["selections"].append({"kind":"plugin","scoped_requirement_id":scope,"source_contract":"rpnh/wheel_plugin_metadata/v1","evidence_level":"artifact_verified","identity":identity})
    d["unresolved"]=[r for r in d["unresolved"] if not (r["scoped_requirement_id"]==scope and r["reason_code"]=="ENVIRONMENT_PROBE_UNSUPPORTED")]
    return ConcreteSelections(EnvironmentResolutionLock.from_dict(d),concrete.artifacts)
