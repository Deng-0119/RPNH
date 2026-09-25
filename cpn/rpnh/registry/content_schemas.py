"""Fail-closed verification of exact registered content-schema resources."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, Mapping

from jsonschema import Draft7Validator
from cpn.rpnh._schema_validation import check_draft7_schema

from .models import VersionRef
from .resources import ResourceVersionRef
from .schema_catalog import _SCHEMA_ID

if TYPE_CHECKING:
    from .operations import RegisteredContentSchemaAuthority


class ContentSchemaAuthorityError(RuntimeError):
    """An exact schema file/catalog closure cannot be proven."""


def _decode_schema_bytes(
        payload: bytes,
) -> tuple[str, Mapping[str, Any]]:
    try:
        decoded = payload.decode("utf-8")
        document = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContentSchemaAuthorityError(
            "schema document is not canonical UTF-8 JSON") from exc
    if not isinstance(document, Mapping):
        raise ContentSchemaAuthorityError("content schema root must be an object")
    schema_id = document.get("$id")
    if (not isinstance(schema_id, str)
            or _SCHEMA_ID.fullmatch(schema_id) is None
            or document.get("$schema")
            != "http://json-schema.org/draft-07/schema#"):
        raise ContentSchemaAuthorityError(
            "content schema id/dialect is not canonical v1")
    return schema_id, document


def _validate_schema_bytes(
        payload: bytes,
) -> tuple[str, Mapping[str, Any]]:
    schema_id, document = _decode_schema_bytes(payload)
    try:
        check_draft7_schema(document)
        _verify_local_refs(document, document)
    except ContentSchemaAuthorityError:
        raise
    except Exception as exc:
        raise ContentSchemaAuthorityError(
            "content schema is not valid self-contained Draft7") from exc
    return schema_id, document


def _resolve_local_pointer(document: Mapping[str, Any], pointer: str) -> object:
    if not pointer.startswith("#/"):
        raise ContentSchemaAuthorityError(
            "v1 content schema refs must be local JSON pointers")
    current: object = document
    for raw_part in pointer[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and part in current:
            current = current[part]
            continue
        if isinstance(current, list):
            try:
                current = current[int(part)]
                continue
            except (ValueError, IndexError):
                pass
        raise ContentSchemaAuthorityError(
            f"content schema has an unresolved local ref: {pointer}")
    return current


def _verify_local_refs(value: object, document: Mapping[str, Any]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "$ref":
                if not isinstance(child, str):
                    raise ContentSchemaAuthorityError(
                        "content schema $ref must be a string")
                _resolve_local_pointer(document, child)
            elif key in {"$recursiveRef", "$dynamicRef"}:
                raise ContentSchemaAuthorityError(
                    "content schema uses a non-Draft7 reference keyword")
            _verify_local_refs(child, document)
    elif isinstance(value, list):
        for child in value:
            _verify_local_refs(child, document)


def verify_registered_content_schema(
        core: Any,
        content_schema_ref: VersionRef | ResourceVersionRef, *,
        schema_document_ref: VersionRef,
        fresh_reader: Callable[[ResourceVersionRef], bytes] | None = None,
) -> Any:
    """Return a typed authority for one immutable self-contained Draft7 schema.

    ``schema_document_ref`` is the exact registered schema-file resource.  A
    Registry catalog source is disambiguated by the file's ``$id`` and must
    contain byte-identical source plus one recorded catalog identity in its
    frozen bundle.  An application or runtime source must be that same exact
    resource version.
    """
    from .operations import RegisteredContentSchemaAuthority

    if (not isinstance(schema_document_ref, VersionRef)
            or schema_document_ref.entity_type != "resource_version/v1"):
        raise ContentSchemaAuthorityError(
            "schema document requires exact resource_version/v1")
    document_resource_ref = ResourceVersionRef(
        schema_document_ref.entity_id, schema_document_ref.version_id)
    try:
        prepared = core.get_version(document_resource_ref.resource_version_id)
        if (prepared.object_type != "resource_version/v1"
                or prepared.logical_id != document_resource_ref.resource_id
                or prepared.version_id
                != document_resource_ref.resource_version_id
                or prepared.media_type != "application/schema+json"):
            raise ContentSchemaAuthorityError(
                "schema document resource has the wrong immutable envelope")
        payload = (
            fresh_reader(document_resource_ref)
            if fresh_reader is not None
            else core.object_store.read_verified(prepared))
    except ContentSchemaAuthorityError:
        raise
    except Exception as exc:
        raise ContentSchemaAuthorityError(
            "schema document resource failed verified read") from exc
    if not payload or prepared.size != len(payload):
        raise ContentSchemaAuthorityError(
            "schema document size differs from immutable resource")
    schema_id, document = _decode_schema_bytes(payload)

    catalog_ref: VersionRef | None = None
    application_ref: ResourceVersionRef | None = None
    if isinstance(content_schema_ref, VersionRef):
        if content_schema_ref.entity_type != "registry_type_catalog/v1":
            raise ContentSchemaAuthorityError(
                "catalog content schema source/id has the wrong type")
        try:
            catalog = core.get_version(content_schema_ref.version_id)
            if (catalog.object_type != "registry_type_catalog/v1"
                    or catalog.logical_id != content_schema_ref.entity_id
                    or catalog.version_id != content_schema_ref.version_id):
                raise ContentSchemaAuthorityError(
                    "content schema catalog ref is not exact")
            bundle = json.loads(core.object_store.read_registered(catalog))
            entry = bundle["schemas"][schema_id]
            if entry["schema_id"] != schema_id:
                raise ContentSchemaAuthorityError("catalog schema identity differs")
            catalog_payload = entry["source"].encode("utf-8")
        except ContentSchemaAuthorityError:
            raise
        except Exception as exc:
            raise ContentSchemaAuthorityError(
                "content schema catalog closure failed verification") from exc
        if catalog_payload != payload:
            raise ContentSchemaAuthorityError(
                "schema document is not byte-identical to its catalog entry")
        catalog_ref = content_schema_ref
    elif isinstance(content_schema_ref, ResourceVersionRef):
        if content_schema_ref != document_resource_ref:
            raise ContentSchemaAuthorityError(
                "resource schema source must be its exact document resource")
        try:
            check_draft7_schema(document)
            _verify_local_refs(document, document)
        except ContentSchemaAuthorityError:
            raise
        except Exception as exc:
            raise ContentSchemaAuthorityError(
                "content schema is not valid self-contained Draft7") from exc
        application_ref = content_schema_ref
    else:
        raise ContentSchemaAuthorityError(
            "content schema source must be exact catalog or resource ref")

    return RegisteredContentSchemaAuthority(
        schema_id=schema_id,
        catalog_ref=catalog_ref,
        resource_ref=application_ref,
    )


def hydrate_registered_content_schema(
        core: Any,
        source_ref: VersionRef | ResourceVersionRef, *,
        schema_id: str,
        fresh_reader: Callable[[ResourceVersionRef], bytes] | None = None,
) -> RegisteredContentSchemaAuthority:
    """Hydrate an exact source plus its derived canonical schema id.

    Resource-backed application/runtime schemas are their own immutable schema
    documents.  Static infrastructure schemas are resolved only inside the
    exact frozen Registry catalog object; the schema id never selects a mutable
    filesystem document.
    """
    from .operations import RegisteredContentSchemaAuthority

    if not isinstance(schema_id, str) or _SCHEMA_ID.fullmatch(schema_id) is None:
        raise ContentSchemaAuthorityError("content schema id is not canonical")
    if isinstance(source_ref, ResourceVersionRef):
        authority = verify_registered_content_schema(
            core, source_ref,
            schema_document_ref=source_ref.as_version_ref(),
            fresh_reader=fresh_reader)
        if authority.schema_id != schema_id:
            raise ContentSchemaAuthorityError(
                "resource schema source resolves to another schema id")
        return authority
    if (not isinstance(source_ref, VersionRef)
            or source_ref.entity_type != "registry_type_catalog/v1"):
        raise ContentSchemaAuthorityError(
            "static schema requires exact Registry catalog authority")
    try:
        catalog = core.get_version(source_ref.version_id)
        if (catalog.object_type != "registry_type_catalog/v1"
                or catalog.logical_id != source_ref.entity_id
                or catalog.version_id != source_ref.version_id):
            raise ContentSchemaAuthorityError(
                "content schema catalog ref is not exact")
        bundle = json.loads(core.object_store.read_registered(catalog))
        entry = bundle["schemas"][schema_id]
        if entry["schema_id"] != schema_id:
            raise ContentSchemaAuthorityError("catalog schema identity differs")
        payload = entry["source"].encode("utf-8")
        resolved_id, _document = _decode_schema_bytes(payload)
        if resolved_id != schema_id:
            raise ContentSchemaAuthorityError(
                "catalog schema source/id closure differs")
    except ContentSchemaAuthorityError:
        raise
    except Exception as exc:
        raise ContentSchemaAuthorityError(
            "content schema catalog closure failed verification") from exc
    return RegisteredContentSchemaAuthority(
        schema_id=schema_id,
        catalog_ref=source_ref,
    )


__all__ = [
    "ContentSchemaAuthorityError",
    "hydrate_registered_content_schema",
    "verify_registered_content_schema",
]
