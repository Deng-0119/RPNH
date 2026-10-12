"""Exact HOST input command transport; durable authority stays in Registry facts."""
from dataclasses import dataclass
import base64
from datetime import datetime, timezone

from .models import VersionRef
from .resources import ResourceVersionRef
from .publication import _stable_id
from .strict_contracts import ref_payload
from .schema_catalog import canonical_json
from .user_collaboration import check_session

WAIT = 'run_input_wait/v1'
RECEIPT = 'run_input_receipt/v1'


@dataclass(frozen=True, kw_only=True)
class InputWaitSpec:
    transition_id: str
    input_port: str
    anchor_token_ref: VersionRef
    binding_ref: ResourceVersionRef
    allowed_principal_refs: tuple[VersionRef, ...]
    expires_at_utc: str | None = None
    expiry_input: object | None = None
    supersedes_wait_ref: VersionRef | None = None


@dataclass(frozen=True)
class InputReceipt:
    receipt_ref: VersionRef
    resource_ref: ResourceVersionRef
    token_ref: VersionRef
    checkpoint_ref: VersionRef
    wait_ref: VersionRef
    replayed: bool = False


def exact_ref(value, kind=None):
    if not isinstance(value, VersionRef) or (kind and value.entity_type != kind):
        raise TypeError('input wait requires an exact typed reference')
    return ref_payload(value)


def resource_ref(value):
    if not isinstance(value, ResourceVersionRef):
        raise TypeError('input binding requires ResourceVersionRef')
    return exact_ref(value.as_version_ref())


def input_material(value):
    from ..run import OwnerInput
    if (not isinstance(value, OwnerInput) or not isinstance(value.payload, bytes)
            or not isinstance(value.schema_id, str) or not value.schema_id
            or not isinstance(value.summary, str)):
        raise TypeError('input declaration requires OwnerInput with exact bytes')
    return dict(schema_id=value.schema_id, payload=base64.b64encode(value.payload).decode('ascii'),
                summary=value.summary)


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError('input deadline must be a timezone-qualified UTC timestamp')
    return parsed


def now():
    return datetime.now(timezone.utc)


def spec_material(spec):
    if (type(spec) is not InputWaitSpec or not isinstance(spec.transition_id, str)
            or not spec.transition_id or not isinstance(spec.input_port, str)
            or not spec.input_port or type(spec.allowed_principal_refs) is not tuple
            or not spec.allowed_principal_refs or len(set(spec.allowed_principal_refs)) != len(spec.allowed_principal_refs)):
        raise TypeError('input wait requires a complete exact InputWaitSpec')
    if spec.expires_at_utc is not None:
        timestamp(spec.expires_at_utc)
    if spec.expiry_input is not None and spec.expires_at_utc is None:
        raise ValueError('expiry input requires a deadline')
    return dict(transition_id=spec.transition_id, input_port=spec.input_port,
        anchor_token_ref=exact_ref(spec.anchor_token_ref, 'petri_token/v1'),
        binding_ref=resource_ref(spec.binding_ref),
        allowed_principal_refs=[exact_ref(r, 'principal/v1') for r in spec.allowed_principal_refs],
        expires_at_utc=spec.expires_at_utc,
        expiry_input=None if spec.expiry_input is None else input_material(spec.expiry_input),
        supersedes_wait_ref=None if spec.supersedes_wait_ref is None else exact_ref(spec.supersedes_wait_ref, WAIT))


def reference(task, kind, command):
    return VersionRef(kind, _stable_id('resource', 'run-input', task, kind, command),
                      _stable_id('resource_version', 'run-input', task, kind, command))


def command_key(command):
    if not isinstance(command, str) or not command:
        raise ValueError('input command requires a nonempty command_id')
    return 'run-input:' + command


@dataclass(frozen=True)
class InputCommand:
    store: object
    session: object
    envelope: bytes

    def material(self):
        import json
        check_session(self.store, self.session)
        return dict(session_ref=ref_payload(self.session.ref), actor=self.session.actor,
                    envelope=json.loads(self.envelope))


def command(store, session, envelope):
    check_session(store, session)
    return InputCommand(store, session, canonical_json(envelope))
