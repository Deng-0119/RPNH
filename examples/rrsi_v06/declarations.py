"""Rebuild the four initial RRSI role frames without running any role.

From the repository root: python -m examples.rrsi_v06.declarations --check
Omit --check to regenerate the checked-in frames and their provenance.
No Registry, provider, model, HTTP server or browser is started.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from cpn.frontend.dashboard import public_net, topology_digest
from cpn.rpnh.compiler import compile_module
from cpn.rpnh.inspection import project_compiled_boundaries, project_compiled_net
from examples._support.declaration_identity import export_declaration_identity
from examples.rrsi_v06.src.rpnh_rrsi.formal_role import (
    FormalRoleConfig, build_role_module, role_registration,
)

ROLES = ("analyst", "proposer", "critic", "digester")
ROOT = Path(__file__).resolve().parents[2]
FIGURES = Path(__file__).resolve().parent / "figures"
SOURCE_BASE_COMMIT = "e51591d9593b9cfda249a9645cdde412acbf3ca4"
SOURCE_PATHS = (
    "examples/rrsi_v06/src/rpnh_rrsi/formal_role.py",
    "cpn/rpnh/compiler.py",
    "cpn/rpnh/executable_net.py",
    "cpn/rpnh/inspection.py",
    "cpn/frontend/dashboard.py",
    "examples/_support/declaration_identity.py",
)


def json_bytes(value) -> bytes:
    """Match the historical compiled-document serialization exactly."""
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def compile_role_declaration(role: str):
    if role not in ROLES:
        raise ValueError("unknown RRSI declaration role")
    config = FormalRoleConfig(role)
    return compile_module(build_role_module(config), role_registration(config, result_sink=[]))


def _frame_from_compiled(role, compiled):
    source = {"mode": "initial_configured", "task_label": f"rrsi.role.{role}",
              "verified_head_ordinal": None}
    projection = project_compiled_net(compiled, source=source)
    frame = {
        "schema_version": "rpnh/dashboard/v1",
        "net": public_net(projection), "source": source,
        "boundaries": project_compiled_boundaries(compiled), "transition_bindings": [],
        "presentation": {
            "title": f"RRSI {role.title()}",
            "description": "Initial child declaration; no execution or marking.",
            "nodes": {}, "status": "default", "topology_digest": topology_digest(projection),
        },
        "position": {"mode": "live", "cursor": None, "latest_head": None},
        "coverage": {"history": "unsupported", "firings": "not_provided",
                     "agent_nodes": "not_provided", "terminal_evidence": "not_provided"},
        "change": {}, "agent_nodes": [],
    }
    return export_declaration_identity(frame, compiled), projection


def build_role_frame(role: str):
    """Use real role lowering and the shared, narrow declaration identity export."""
    return _frame_from_compiled(role, compile_role_declaration(role))[0]


def generated_files():
    """Return reproducible bytes, keeping historical screenshots/provenance intact."""
    historical_path = ROOT / "examples/rrsi_v06/results/figure-provenance.json"
    historical = json.loads(historical_path.read_text(encoding="utf-8"))
    old = {item["id"]: item for item in historical["declarations"]}
    outputs = {}
    declarations = []
    for role in ROLES:
        compiled = compile_role_declaration(role)
        frame, projection = _frame_from_compiled(role, compiled)
        compiled_sha = digest(json_bytes(compiled.to_dict()))
        if compiled_sha != old[f"rrsi.role.{role}"]["compiled_sha256"]:
            raise ValueError(f"{role}: compiled identity differs from historical role capture")
        path = f"{role}/declaration-frame.json"
        outputs[path] = json_bytes(frame)
        declarations.append({
            "role": role, "id": f"rrsi.role.{role}", "frame": path,
            "compiled_sha256": compiled_sha,
            "projection_sha256": digest(json_bytes(projection)),
            "frame_sha256": digest(outputs[path]), "summary": projection["summary"],
            "identity_source": "declared_llm_executor",
        })
    provenance = {
        "schema_version": "rpnh/rrsi-role-declaration-provenance/v1",
        "source_repository": "https://github.com/Deng-0119/RPNH",
        "source_base_commit": SOURCE_BASE_COMMIT,
        "source_files_sha256": {path: digest((ROOT / path).read_bytes()) for path in SOURCE_PATHS},
        "generator": "examples/rrsi_v06/declarations.py",
        "generator_sha256": digest(Path(__file__).read_bytes()),
        "serialization": "UTF-8 json.dumps(ensure_ascii=False, indent=2, allow_nan=False) + LF",
        "source_mode": "initial_configured", "observation_head": None,
        "execution_claim": False, "runtime_evidence": False,
        "historical_figure_provenance": "../results/figure-provenance.json",
        "historical_figure_provenance_sha256": digest(historical_path.read_bytes()),
        "historical_compiled_identity_matches": True,
        "image_treatment": "Existing image files and historical provenance are unchanged; no new capture.",
        "declarations": declarations,
    }
    outputs["role-declaration-provenance.json"] = json_bytes(provenance)
    return outputs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify bundled bytes without writing")
    args = parser.parse_args(argv)
    for name, data in generated_files().items():
        path = FIGURES / name
        if args.check:
            if not path.is_file() or path.read_bytes() != data:
                parser.error(f"stale or missing declaration artifact: {name}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    print("RRSI role declarations: checked" if args.check else "RRSI role declarations: generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
