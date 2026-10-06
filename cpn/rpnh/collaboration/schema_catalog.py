"""Explicit content-schema inventory for collaboration reference contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cpn.rpnh.registry.schema_catalog import TypeDefinition

from .references import SOURCE_RESOURCE_REF_SCHEMA, SOURCE_VERSION_REF_SCHEMA
from .authoring import NET_REVISION_SCHEMA, NET_REVISION_TYPE


COLLABORATION_SCHEMA_REFS = (
    SOURCE_RESOURCE_REF_SCHEMA,
    SOURCE_VERSION_REF_SCHEMA,
)


def collaboration_schema_data() -> tuple[
    dict[str, dict[str, Any]], tuple[TypeDefinition, ...], dict[str, Path],
]:
    """Return fresh documents and provenance for caller-owned composition.

These are content schemas, not Registry object/event types. Nothing is added
to the mechanical catalog, Registration, or production HOSTs automatically.
"""
    root = Path(__file__).resolve().parents[2] / "schemas" / "rpnh" / "collaboration"
    paths = {
        schema_id: root / (schema_id.rsplit("/", 2)[1] + ".v1.schema.json")
        for schema_id in COLLABORATION_SCHEMA_REFS
    }
    documents = {
        schema_id: json.loads(path.read_text(encoding="utf-8"))
        for schema_id, path in paths.items()
    }
    return documents, (), paths


def authoring_schema_data() -> tuple[
    dict[str, dict[str, Any]], tuple[TypeDefinition, ...], dict[str, Path],
]:
    """Compose reference contracts and the optional immutable author object."""
    documents, _, paths = collaboration_schema_data()
    path = (Path(__file__).resolve().parents[2] / "schemas" / "registry_v1"
            / "collaboration_net_revision.v1.schema.json")
    documents[NET_REVISION_SCHEMA] = json.loads(path.read_text(encoding="utf-8"))
    paths[NET_REVISION_SCHEMA] = path
    definitions = (TypeDefinition(
        name=NET_REVISION_TYPE, category="object", owner="collaboration",
        schema_ref=NET_REVISION_SCHEMA, criticality=None,
        permission="task-scoped", retention="permanent",
        recovery_rule="exact-author-record-replay",
        integrity_rule="exact-schema-and-source-qualified-reference-validation",
    ),)
    return documents, definitions, paths


def source_identity_schema_data() -> tuple[
    dict[str, dict[str, Any]], tuple[TypeDefinition, ...], dict[str, Path],
]:
    """Opt in to the owner source-binding fact and author/reference contracts."""
    from cpn.rpnh.registry._event_store.source_identity import SOURCE_BINDING_TYPE, SOURCE_BINDING_SCHEMA
    documents, definitions, paths = authoring_schema_data()
    path = (Path(__file__).resolve().parents[2] / "schemas" / "registry_v1"
            / "collaboration_source_binding.v1.schema.json")
    documents[SOURCE_BINDING_SCHEMA] = json.loads(path.read_text(encoding="utf-8"))
    paths[SOURCE_BINDING_SCHEMA] = path
    return documents, (*definitions, TypeDefinition(
        name=SOURCE_BINDING_TYPE, category="object", owner="collaboration",
        schema_ref=SOURCE_BINDING_SCHEMA, criticality=None,
        permission="writer-only", retention="permanent", recovery_rule="fail-closed",
        integrity_rule="canonical-local-source-owner-registration",
    )), paths


def branch_schema_data() -> tuple[
    dict[str, dict[str, Any]], tuple[TypeDefinition, ...], dict[str, Path],
]:
    """Opt-in author Branch records on the same Registry catalog and writer."""
    from cpn.rpnh.registry._event_store.branch_publication import BRANCH_TYPE, BRANCH_SCHEMA
    documents, definitions, paths = source_identity_schema_data()
    path = Path(__file__).resolve().parents[2] / "schemas" / "registry_v1" / "collaboration_branch.v1.schema.json"
    documents[BRANCH_SCHEMA] = json.loads(path.read_text(encoding="utf-8"))
    paths[BRANCH_SCHEMA] = path
    return documents, (*definitions, TypeDefinition(
        name=BRANCH_TYPE, category="object", owner="collaboration", schema_ref=BRANCH_SCHEMA,
        criticality=None, permission="writer-only", retention="permanent",
        recovery_rule="exact-branch-command-replay", integrity_rule="branch-version-head-stream-cas",
    )), paths


def author_material_schema_data():
    """Add only the closed-module publisher's three content contracts."""
    documents, definitions, paths = branch_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas" / "rpnh" / "collaboration"
    for name in ("author_element_map", "author_boundary_map", "author_host_requirements"):
        schema_id = f"rpnh/collaboration/{name}/v1"
        path = root / f"{name}.v1.schema.json"
        documents[schema_id] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema_id] = path
    return documents, definitions, paths


def plain_transform_schema_data():
    """Explicit author split/fusion opt-in; legacy inventories stay unchanged."""
    documents, definitions, paths = author_material_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas" / "rpnh" / "collaboration"
    for name in ("plain_author_transform_map", "plain_author_transform_command"):
        schema = f"rpnh/collaboration/{name}/v1"
        path = root / f"{name}.v1.schema.json"
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, definitions, paths


def plain_transform_assembly_schema_data():
    """Explicit transform members through the unchanged Assembly/v2 plain contract."""
    from ..registry.schema_catalog import canonical_json
    documents, definitions, paths = plain_transform_schema_data()
    other_documents, other_definitions, other_paths = graph_assembly_schema_data()
    for schema, document in other_documents.items():
        if schema in documents and canonical_json(documents[schema]) != canonical_json(document):
            raise ValueError("transform/Assembly catalog schema conflict")
        documents[schema], paths[schema] = document, other_paths[schema]
    types = {definition.name: definition for definition in definitions}
    for definition in other_definitions:
        if definition.name in types and types[definition.name] != definition:
            raise ValueError("transform/Assembly catalog type conflict")
        types[definition.name] = definition
    return documents, tuple(types.values()), paths


def assembly_schema_data():
    """Opt in to stable-member Assembly records and their two content schemas."""
    from .assemblies import ASSEMBLY_TYPE, ASSEMBLY_SCHEMA, PLAN_SCHEMA, LOWERING_SCHEMA
    documents, definitions, paths = author_material_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas"
    for schema, path in (
            (ASSEMBLY_SCHEMA, root / "registry_v1" / "collaboration_assembly_revision.v1.schema.json"),
            (PLAN_SCHEMA, root / "rpnh" / "collaboration" / "assembly_plan.v1.schema.json"),
            (LOWERING_SCHEMA, root / "rpnh" / "collaboration" / "assembly_lowering_map.v1.schema.json")):
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, (*definitions, TypeDefinition(
        name=ASSEMBLY_TYPE, category="object", owner="collaboration", schema_ref=ASSEMBLY_SCHEMA,
        criticality=None, permission="task-scoped", retention="permanent",
        recovery_rule="exact-assembly-plan-replay", integrity_rule="exact-member-composition-and-lowering-map",
    )), paths


def graph_author_schema_data():
    """Opt in to ordinary-v3 source-authoritative revisions; v1 stays intact."""
    from .graph_authoring import GRAPH_REVISION_SCHEMA, GRAPH_REVISION_TYPE
    from .graph_source import GRAPH_SOURCE_SCHEMA, GRAPH_RECIPE_SCHEMA, GRAPH_MAP_SCHEMA
    documents, definitions, paths = author_material_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas"
    selected = [(GRAPH_REVISION_SCHEMA, root / "registry_v1" / "collaboration_net_revision.v2.schema.json")]
    selected.extend((schema, root / "rpnh" / "collaboration" / (schema.rsplit("/", 2)[1] + ".v1.schema.json"))
                    for schema in (GRAPH_SOURCE_SCHEMA, GRAPH_RECIPE_SCHEMA, GRAPH_MAP_SCHEMA))
    for schema, path in selected:
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, (*definitions, TypeDefinition(
        name=GRAPH_REVISION_TYPE, category="object", owner="collaboration", schema_ref=GRAPH_REVISION_SCHEMA,
        criticality=None, permission="task-scoped", retention="permanent",
        recovery_rule="exact-graph-author-command-replay", integrity_rule="complete-source-recipe-reconstruction",
    )), paths


def graph_branch_schema_data():
    """Explicit opt-in graph author plus graph-v2 Branch; older inventories stay unchanged."""
    from cpn.rpnh.registry._event_store.branch_publication import GRAPH_BRANCH_TYPE, GRAPH_BRANCH_SCHEMA
    documents, definitions, paths = graph_author_schema_data()
    path = Path(__file__).resolve().parents[2] / "schemas" / "registry_v1" / "collaboration_branch.v2.schema.json"
    documents[GRAPH_BRANCH_SCHEMA] = json.loads(path.read_text(encoding="utf-8"))
    paths[GRAPH_BRANCH_SCHEMA] = path
    return documents, (*definitions, TypeDefinition(
        name=GRAPH_BRANCH_TYPE, category="object", owner="collaboration", schema_ref=GRAPH_BRANCH_SCHEMA,
        criticality=None, permission="writer-only", retention="permanent",
        recovery_rule="exact-branch-command-replay", integrity_rule="branch-version-head-stream-cas",
    )), paths


def graph_assembly_schema_data():
    """Explicit v2 Assembly plus exact graph/v1 members; older catalogs stay intact."""
    from .assembly_v2 import ASSEMBLY_V2_TYPE, ASSEMBLY_V2_SCHEMA, PLAN_V2_SCHEMA, LOWERING_V2_SCHEMA
    documents, definitions, paths = graph_author_schema_data()
    old_documents, old_definitions, old_paths = assembly_schema_data()
    from ..registry.schema_catalog import canonical_json
    for schema, document in old_documents.items():
        if schema in documents and canonical_json(documents[schema]) != canonical_json(document):
            raise ValueError("collaboration catalog schema conflict")
        documents[schema], paths[schema] = document, old_paths[schema]
    types = {definition.name: definition for definition in definitions}
    for definition in old_definitions:
        if definition.name in types and types[definition.name] != definition:
            raise ValueError("collaboration catalog type conflict")
        types[definition.name] = definition
    root = Path(__file__).resolve().parents[2] / "schemas"
    for schema, path in (
        (ASSEMBLY_V2_SCHEMA, root / "registry_v1/collaboration_assembly_revision.v2.schema.json"),
        (PLAN_V2_SCHEMA, root / "rpnh/collaboration/assembly_plan.v2.schema.json"),
        (LOWERING_V2_SCHEMA, root / "rpnh/collaboration/assembly_lowering_map.v2.schema.json")):
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, (*types.values(), TypeDefinition(
        name=ASSEMBLY_V2_TYPE, category="object", owner="collaboration", schema_ref=ASSEMBLY_V2_SCHEMA,
        criticality=None, permission="task-scoped", retention="permanent",
        recovery_rule="exact-assembly-v2-complete-command-replay",
        integrity_rule="source-rebuilt-assembly-exact-generated-pair-and-origin-coverage",
    )), paths


def plain_merge_schema_data():
    """Opt in to inert plain merge analysis; existing inventories stay intact."""
    documents, definitions, paths = author_material_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas" / "rpnh" / "collaboration"
    for name in ("plain_merge_analysis", "plain_merge_analysis_command"):
        schema = f"rpnh/collaboration/{name}/v1"
        path = root / f"{name}.v1.schema.json"
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, definitions, paths


def plain_merge_result_schema_data():
    """Explicit result/resolution opt-in; prior analysis inventories stay intact."""
    documents, definitions, paths = plain_merge_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas" / "rpnh" / "collaboration"
    for name in ("plain_merge_resolution", "plain_merge_author_command"):
        schema = f"rpnh/collaboration/{name}/v1"
        path = root / f"{name}.v1.schema.json"
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, definitions, paths


def plain_merge_assembly_schema_data():
    """Explicit merge results plus Branch/v1 and Assembly/v2; prior opt-ins stay fixed."""
    from ..registry.schema_catalog import canonical_json
    documents, definitions, paths = plain_merge_result_schema_data()
    other_documents, other_definitions, other_paths = graph_assembly_schema_data()
    for schema, document in other_documents.items():
        if schema in documents and canonical_json(documents[schema]) != canonical_json(document):
            raise ValueError("merge/Assembly catalog schema conflict")
        documents[schema], paths[schema] = document, other_paths[schema]
    types = {definition.name: definition for definition in definitions}
    for definition in other_definitions:
        if definition.name in types and types[definition.name] != definition:
            raise ValueError("merge/Assembly catalog type conflict")
        types[definition.name] = definition
    return documents, tuple(types.values()), paths


def nested_assembly_schema_data():
    """Opt-in v3 catalog; v2 catalogs and historical contracts are unchanged."""
    from .assembly_v3 import ASSEMBLY_V3_TYPE, ASSEMBLY_V3_SCHEMA, PLAN_V3_SCHEMA, LOWERING_V3_SCHEMA
    documents, definitions, paths = graph_assembly_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas"
    for schema, path in (
        (ASSEMBLY_V3_SCHEMA, root / "registry_v1/collaboration_assembly_revision.v3.schema.json"),
        (PLAN_V3_SCHEMA, root / "rpnh/collaboration/assembly_plan.v3.schema.json"),
        (LOWERING_V3_SCHEMA, root / "rpnh/collaboration/assembly_lowering_map.v3.schema.json")):
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, (*definitions, TypeDefinition(
        name=ASSEMBLY_V3_TYPE, category="object", owner="collaboration", schema_ref=ASSEMBLY_V3_SCHEMA,
        criticality=None, permission="task-scoped", retention="permanent",
        recovery_rule="exact-assembly-v3-complete-command-replay",
        integrity_rule="nested-exact-pairs-and-instance-path-final-context-source-coverage",
    )), paths


def plain_merge_nested_assembly_schema_data():
    """Explicit merge plus two-level closed Assembly/v3; prior opt-ins stay fixed."""
    from ..registry.schema_catalog import canonical_json
    documents, definitions, paths = plain_merge_result_schema_data()
    other_documents, other_definitions, other_paths = nested_assembly_schema_data()
    for schema, document in other_documents.items():
        if schema in documents and canonical_json(documents[schema]) != canonical_json(document):
            raise ValueError("merge/nested Assembly catalog schema conflict")
        documents[schema], paths[schema] = document, other_paths[schema]
    types = {definition.name: definition for definition in definitions}
    for definition in other_definitions:
        if definition.name in types and types[definition.name] != definition:
            raise ValueError("merge/nested Assembly catalog type conflict")
        types[definition.name] = definition
    return documents, tuple(types.values()), paths


def open_region_schema_data():
    """Explicit open-region content contracts; prior catalog inventories stay fixed."""
    documents, definitions, paths = author_material_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas" / "rpnh" / "collaboration"
    for name in ("open_region_extraction_provenance", "open_region_definition", "open_region_element_map",
                 "open_region_boundary_inventory", "open_region_host_requirements", "open_region_contract",
                 "open_region_author_command"):
        schema = f"rpnh/collaboration/{name}/v1"
        path = root / f"{name}.v1.schema.json"
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, definitions, paths


def open_region_closure_schema_data():
    """Opt in to exact prospective closure; no older catalog is enlarged."""
    documents, definitions, paths = open_region_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas" / "rpnh" / "collaboration"
    for name in ("prospective_member_intent", "open_region_origin_map", "open_region_boundary_adaptation",
                 "open_region_closure_author_command"):
        schema = f"rpnh/collaboration/{name}/v1"
        path = root / f"{name}.v1.schema.json"
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, definitions, paths


def open_region_assembly_schema_data():
    """New direct adapted-member v4; compose accepted inventories exactly."""
    from ..registry.schema_catalog import canonical_json
    from .assembly_v4 import ASSEMBLY_V4_TYPE, ASSEMBLY_V4_SCHEMA
    documents, definitions, paths = open_region_closure_schema_data()
    other_documents, other_definitions, other_paths = plain_merge_nested_assembly_schema_data()
    for schema, document in other_documents.items():
        if schema in documents and canonical_json(documents[schema]) != canonical_json(document):
            raise ValueError("open-region/Assembly catalog schema conflict")
        documents[schema], paths[schema] = document, other_paths[schema]
    types = {definition.name: definition for definition in definitions}
    for definition in other_definitions:
        if definition.name in types and types[definition.name] != definition:
            raise ValueError("open-region/Assembly catalog type conflict")
        types[definition.name] = definition
    root = Path(__file__).resolve().parents[2] / "schemas"
    for schema, path in ((ASSEMBLY_V4_SCHEMA, root / "registry_v1/collaboration_assembly_revision.v4.schema.json"), *[
            (f"rpnh/collaboration/{name}/v4", root / f"rpnh/collaboration/{name}.v4.schema.json")
            for name in ("assembly_plan", "assembly_lowering_map", "assembly_author_command")]):
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, (*types.values(), TypeDefinition(
        name=ASSEMBLY_V4_TYPE, category="object", owner="collaboration", schema_ref=ASSEMBLY_V4_SCHEMA,
        criticality=None, permission="task-scoped", retention="permanent",
        recovery_rule="exact-assembly-v4-complete-command-replay",
        integrity_rule="direct-adapted-target-match-and-exact-generated-pair",
    )), paths


def source_set_schema_data():
    """Explicit SourceSet producer inventory; no automatic observer grants."""
    from .source_sets import SOURCE_SET_TYPE, SOURCE_SET_SCHEMA
    documents, definitions, paths = source_identity_schema_data()
    path = Path(__file__).resolve().parents[2] / 'schemas/registry_v1/collaboration_source_set.v1.schema.json'
    documents[SOURCE_SET_SCHEMA] = json.loads(path.read_text(encoding='utf-8'))
    paths[SOURCE_SET_SCHEMA] = path
    return documents, (*definitions, TypeDefinition(
        name=SOURCE_SET_TYPE, category='object', owner='collaboration', schema_ref=SOURCE_SET_SCHEMA,
        criticality=None, permission='writer-only', retention='permanent',
        recovery_rule='exact-source-set-command-replay', integrity_rule='source-set-version-owner-stream-cas',
    )), paths


def open_region_derived_schema_data():
    """Explicit ordinary conversion; prior open catalogs remain unchanged."""
    documents, definitions, paths = open_region_assembly_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas/rpnh/collaboration"
    for name in ("open_region_derived_author_command", "open_region_historical_origins"):
        schema = f"rpnh/collaboration/{name}/v1"
        path = root / f"{name}.v1.schema.json"
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, definitions, paths


def open_region_derived_assembly_schema_data():
    """Opt in to ordinary-derived Assembly/v5, preserving frozen v4 contracts."""
    from .assembly_v5 import ASSEMBLY_V5_TYPE, ASSEMBLY_V5_SCHEMA
    documents, definitions, paths = open_region_derived_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas"
    for schema, path in ((ASSEMBLY_V5_SCHEMA, root / "registry_v1/collaboration_assembly_revision.v5.schema.json"), *[
            (f"rpnh/collaboration/{name}/v5", root / f"rpnh/collaboration/{name}.v5.schema.json")
            for name in ("assembly_plan", "assembly_lowering_map", "assembly_author_command")]):
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, (*definitions, TypeDefinition(
        name=ASSEMBLY_V5_TYPE, category="object", owner="collaboration", schema_ref=ASSEMBLY_V5_SCHEMA,
        criticality=None, permission="task-scoped", retention="permanent",
        recovery_rule="exact-assembly-v5-complete-command-replay",
        integrity_rule="ordinary-derived-full-history-and-exact-generated-pair",
    )), paths


def source_observation_schema_data():
    """SourceSet plus explicitly recorded query evidence; no grant issuer."""
    from .source_observations import OBSERVATION_TYPE, RECORD_SCHEMA
    documents, definitions, paths = source_set_schema_data()
    path = Path(__file__).resolve().parents[2] / 'schemas/registry_v1/collaboration_source_observation.v1.schema.json'
    documents[RECORD_SCHEMA] = json.loads(path.read_text(encoding='utf-8'))
    paths[RECORD_SCHEMA] = path
    return documents, (*definitions, TypeDefinition(
        name=OBSERVATION_TYPE, category='object', owner='collaboration', schema_ref=RECORD_SCHEMA,
        criticality=None, permission='writer-only', retention='permanent',
        recovery_rule='exact-observation-command-replay', integrity_rule='exact-source-set-and-independent-source-evidence',
    )), paths


def workset_schema_data():
    """Explicit local Workset/ordinary-Success inventory; legacy catalogs stay intact."""
    from .worksets import TYPES
    documents, definitions, paths = source_identity_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas" / "registry_v1"
    added = []
    for kind in TYPES:
        schema = "registry_v1/" + kind
        path = root / (kind.replace("/v1", ".v1.schema.json"))
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
        added.append(TypeDefinition(name=kind, category="object", owner="collaboration",
            schema_ref=schema, criticality=None, permission="writer-only", retention="permanent",
            recovery_rule="exact-workset-command-and-ordinary-success-replay",
            integrity_rule="same-cut-owner-cas-and-exact-occurrence-consumption"))
    return documents, (*definitions, *added), paths


def normal_child_root_schema_data():
    """Opt in to the two normal-child root objects; keep the v1 inventory exact."""
    documents, definitions, paths = workset_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas" / "registry_v1"
    added = []
    for kind, filename in (("execution_child_seal/v1", "execution_child_seal.v1.schema.json"),
                           ("collaboration_root_terminal/v2", "collaboration_root_terminal.v2.schema.json")):
        schema = "registry_v1/" + kind
        path = root / filename
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
        added.append(TypeDefinition(name=kind, category="object", owner="collaboration",
            schema_ref=schema, criticality=None, permission="writer-only", retention="permanent",
            recovery_rule="exact-normal-child-root-command-replay",
            integrity_rule="same-success-exhaustive-normal-child-seal"))
    return documents, (*definitions, *added), paths


def plain_transplant_schema_data():
    """Explicit finite selective plain proof; prior inventories are unchanged."""
    documents, definitions, paths = plain_merge_result_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas" / "rpnh" / "collaboration"
    for name in ("plain_transplant_analysis", "plain_transplant_analysis_command",
                 "plain_transplant_resolution", "plain_selected_change", "plain_transplant_author_command"):
        schema = f"rpnh/collaboration/{name}/v1"
        path = root / f"{name}.v1.schema.json"
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, definitions, paths


def plain_transplant_assembly_schema_data():
    """Explicit selective proof with existing full material Assembly/v2 reader."""
    from ..registry.schema_catalog import canonical_json
    documents, definitions, paths = plain_transplant_schema_data()
    others, other_types, other_paths = graph_assembly_schema_data()
    for schema, document in others.items():
        if schema in documents and canonical_json(documents[schema]) != canonical_json(document):
            raise ValueError("transplant/Assembly catalog schema conflict")
        documents[schema], paths[schema] = document, other_paths[schema]
    types = {item.name: item for item in definitions}
    for item in other_types:
        if item.name in types and types[item.name] != item:
            raise ValueError("transplant/Assembly catalog type conflict")
        types[item.name] = item
    return documents, tuple(types.values()), paths


def plain_transplant_derived_schema_data():
    """Explicit transplant descendants; previous proof catalogs stay frozen."""
    documents, definitions, paths = plain_transplant_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas/rpnh/collaboration"
    for name in ("plain_transplant_derived_author_command", "plain_transplant_historical_origins"):
        schema = f"rpnh/collaboration/{name}/v1"
        path = root / f"{name}.v1.schema.json"
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, definitions, paths


def plain_transplant_derived_assembly_schema_data():
    """Opt in to direct transplant-descendant Assembly/v7, without changing v1-v5."""
    from ..registry.schema_catalog import canonical_json
    from .assembly_v7 import ASSEMBLY_V7_TYPE, ASSEMBLY_V7_SCHEMA
    documents, definitions, paths = plain_transplant_derived_schema_data()
    others, other_types, other_paths = plain_transplant_assembly_schema_data()
    for schema, document in others.items():
        if schema in documents and canonical_json(documents[schema]) != canonical_json(document):
            raise ValueError("transplant-derived/Assembly catalog schema conflict")
        documents[schema], paths[schema] = document, other_paths[schema]
    types = {item.name: item for item in definitions}
    for item in other_types:
        if item.name in types and types[item.name] != item:
            raise ValueError("transplant-derived/Assembly catalog type conflict")
        types[item.name] = item
    root = Path(__file__).resolve().parents[2] / "schemas"
    for schema, path in ((ASSEMBLY_V7_SCHEMA, root / "registry_v1/collaboration_assembly_revision.v7.schema.json"), *[
            (f"rpnh/collaboration/{name}/v7", root / f"rpnh/collaboration/{name}.v7.schema.json")
            for name in ("assembly_plan", "assembly_lowering_map", "assembly_author_command")]):
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, (*types.values(), TypeDefinition(
        name=ASSEMBLY_V7_TYPE, category="object", owner="collaboration", schema_ref=ASSEMBLY_V7_SCHEMA,
        criticality=None, permission="task-scoped", retention="permanent",
        recovery_rule="exact-assembly-v7-complete-command-replay",
        integrity_rule="transplant-derived-full-history-and-exact-generated-pair",
    )), paths


def assembly_merge_schema_data():
    """Explicit full-history flat v6 merge; older inventories remain unchanged."""
    from .assembly_v6 import ASSEMBLY_V6_TYPE, ASSEMBLY_V6_SCHEMA
    documents, definitions, paths = plain_merge_assembly_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas"
    entries = [(ASSEMBLY_V6_SCHEMA, root / "registry_v1/collaboration_assembly_revision.v6.schema.json")]
    for name, version in (("assembly_merge_analysis", 1), ("assembly_merge_analysis_command", 1),
                          ("assembly_plan", 6), ("assembly_lowering_map", 6)):
        entries.append((f"rpnh/collaboration/{name}/v{version}", root / f"rpnh/collaboration/{name}.v{version}.schema.json"))
    for schema, path in entries:
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, (*definitions, TypeDefinition(
        name=ASSEMBLY_V6_TYPE, category="object", owner="collaboration", schema_ref=ASSEMBLY_V6_SCHEMA,
        criticality=None, permission="task-scoped", retention="permanent",
        recovery_rule="exact-assembly-v6-full-command-replay",
        integrity_rule="complete-assembly-history-conflicts-resolved-composition-and-ordinary-pair",
    )), paths



def graph_merge_schema_data():
    """Opt in to complete ordinary graph analysis, v3 merge/edit and v3 Branch."""
    from .graph_merge import MERGE_REVISION_TYPE, MERGE_REVISION_SCHEMA
    from ..registry._event_store.branch_publication import GRAPH_MERGE_BRANCH_TYPE, GRAPH_MERGE_BRANCH_SCHEMA
    documents, definitions, paths = graph_branch_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas"
    entries = [(MERGE_REVISION_SCHEMA, root / "registry_v1/collaboration_net_revision.v3.schema.json"),
               (GRAPH_MERGE_BRANCH_SCHEMA, root / "registry_v1/collaboration_branch.v3.schema.json")]
    entries += [(f"rpnh/collaboration/{name}/v1", root / "rpnh/collaboration" / (name + ".v1.schema.json"))
        for name in ("graph_merge_analysis", "graph_merge_analysis_command", "graph_merge_resolution", "graph_merge_author_command")]
    for schema, path in entries:
        documents[schema], paths[schema] = json.loads(path.read_text(encoding="utf-8")), path
    return documents, (*definitions,
        TypeDefinition(name=MERGE_REVISION_TYPE, category="object", owner="collaboration", schema_ref=MERGE_REVISION_SCHEMA,
            criticality=None, permission="task-scoped", retention="permanent", recovery_rule="exact-graph-merge-command-replay",
            integrity_rule="complete-graph-source-history-resolution-reconstruction"),
        TypeDefinition(name=GRAPH_MERGE_BRANCH_TYPE, category="object", owner="collaboration", schema_ref=GRAPH_MERGE_BRANCH_SCHEMA,
            criticality=None, permission="writer-only", retention="permanent", recovery_rule="exact-branch-command-replay",
            integrity_rule="branch-version-head-stream-cas")), paths


def graph_merge_assembly_schema_data():
    """Explicit flat Assembly/v8 consumer for full ordinary graph-v3 proofs."""
    documents, definitions, paths = graph_merge_schema_data()
    older, old_types, old_paths = graph_assembly_schema_data()
    from ..registry.schema_catalog import canonical_json
    for schema, document in older.items():
        if schema in documents and canonical_json(documents[schema]) != canonical_json(document):
            raise ValueError("graph merge catalog schema conflict")
        documents[schema], paths[schema] = document, old_paths[schema]
    types = {definition.name: definition for definition in definitions}
    for definition in old_types:
        if definition.name in types and types[definition.name] != definition:
            raise ValueError("graph merge catalog type conflict")
        types[definition.name] = definition
    root = Path(__file__).resolve().parents[2] / "schemas"
    for schema, path in (
        ("registry_v1/collaboration_assembly_revision/v8", root / "registry_v1/collaboration_assembly_revision.v8.schema.json"),
        ("rpnh/collaboration/assembly_plan/v8", root / "rpnh/collaboration/assembly_plan.v8.schema.json"),
        ("rpnh/collaboration/assembly_lowering_map/v8", root / "rpnh/collaboration/assembly_lowering_map.v8.schema.json")):
        documents[schema], paths[schema] = json.loads(path.read_text(encoding="utf-8")), path
    return documents, (*types.values(), TypeDefinition(name="collaboration_assembly_revision/v8", category="object",
        owner="collaboration", schema_ref="registry_v1/collaboration_assembly_revision/v8", criticality=None,
        permission="task-scoped", retention="permanent", recovery_rule="exact-assembly-plan-replay",
        integrity_rule="complete-graph-merge-member-composition-and-lowering-map")), paths



def typed_assembly_schema_data():
    """Explicit finite member Assembly v9; prior inventories remain unchanged."""
    from .assembly_v9 import ASSEMBLY_V9_TYPE, ASSEMBLY_V9_SCHEMA
    documents, definitions, paths = graph_assembly_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas"
    for schema, path in ((ASSEMBLY_V9_SCHEMA, root / "registry_v1/collaboration_assembly_revision.v9.schema.json"), *[
            (f"rpnh/collaboration/{name}/v9", root / f"rpnh/collaboration/{name}.v9.schema.json")
            for name in ("assembly_plan", "assembly_lowering_map", "assembly_author_command")]):
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
    return documents, (*definitions, TypeDefinition(
        name=ASSEMBLY_V9_TYPE, category="object", owner="collaboration", schema_ref=ASSEMBLY_V9_SCHEMA,
        criticality=None, permission="task-scoped", retention="permanent",
        recovery_rule="exact-assembly-v9-complete-command-replay",
        integrity_rule="original-typed-proof-final-context-projection-and-exact-generated-pair",
    )), paths



def candidate_schema_data():
    """Opt in to static candidate record grammars, without runtime defaults.

    A schema-valid record is not a validated graph or a prepared provider.
    Publication/resolution requires the separate exact transaction contract.
    """
    from ..registry.runtime_binding_contracts import PLAN_TYPE, MANIFEST_TYPE, READINESS_TYPE
    documents, definitions, paths = assembly_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas" / "registry_v1"
    extra = []
    for kind in (PLAN_TYPE, MANIFEST_TYPE, READINESS_TYPE):
        schema = "registry_v1/" + kind
        path = root / (kind.replace("/", ".") + ".schema.json")
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
        extra.append(TypeDefinition(name=kind, category="object", owner="collaboration",
            schema_ref=schema, criticality=None, permission="task-scoped", retention="permanent",
            recovery_rule="exact-candidate-plan-replay", integrity_rule="fixed-static-candidate-closure"))
    return documents, (*definitions, *extra), paths


def candidate_v2_schema_data():
    """Explicit v2 preservation grammars plus v1; no v2 producer activation."""
    from ..registry.preserved_binding_contracts import PLAN_V2_TYPE, MANIFEST_V2_TYPE, READINESS_V2_TYPE
    documents, definitions, paths = candidate_schema_data()
    root = Path(__file__).resolve().parents[2] / "schemas" / "registry_v1"
    extra = []
    for kind in (PLAN_V2_TYPE, MANIFEST_V2_TYPE, READINESS_V2_TYPE):
        schema = "registry_v1/" + kind
        path = root / (kind.replace("/", ".") + ".schema.json")
        documents[schema] = json.loads(path.read_text(encoding="utf-8"))
        paths[schema] = path
        extra.append(TypeDefinition(name=kind, category="object", owner="collaboration",
            schema_ref=schema, criticality=None, permission="task-scoped", retention="permanent",
            recovery_rule="exact-versioned-candidate-plan-replay", integrity_rule="fixed-preserved-basis-candidate-closure"))
    return documents, (*definitions, *extra), paths
