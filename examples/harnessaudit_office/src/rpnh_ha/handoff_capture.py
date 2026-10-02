"""Conservative read/lineage/request-input evidence, independent of policy.

No redaction, hidden rubric lookup, answer injection, or content-based source
matching. A rendered read is not by itself proof it entered a model request.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
import json
from typing import Any, Mapping


def resource_key(ref: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(ref, Mapping):
        raise ValueError('exact resource reference required')
    values = ref.get('resource_id'), ref.get('resource_version_id')
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError('both exact resource identities required')
    return values


def lineage_key(metadata: Mapping[str, Any], path: Any = None):
    producer = metadata.get('producer_ref')
    if not isinstance(producer, Mapping) or producer.get('entity_type') != 'invocation/v1':
        return None
    version = producer.get('version_id')
    if path is None:
        descriptors = metadata.get('descriptors')
        path = descriptors.get('workspace_path') if isinstance(descriptors, Mapping) else None
    if not isinstance(version, str) or not version or not isinstance(path, str) or not path:
        return None
    return version, path


def _bootstrap_ref_key(value: Any):
    if not isinstance(value, Mapping) or value.get('entity_type') != 'bootstrap_command/v1':
        return None
    logical_id, version_id = value.get('logical_id'), value.get('version_id')
    if not all(isinstance(item, str) and item for item in (logical_id, version_id)):
        return None
    return logical_id, version_id


def _known_owner_ingress(metadata: Mapping[str, Any]) -> bool:
    """Recognize explicit owner/bootstrap input without content inference.

    Current RPNH publishes owner-supplied task input as a private-system
    resource whose producer, lifetime, and both structured origin references
    identify the same exact bootstrap command.  Requiring the complete native
    identity chain keeps a shallow or inconsistent ``private_system`` label
    from silently becoming zero-handoff evidence.
    """
    if metadata.get('origin_kind') == 'owner_input':
        return True
    if metadata.get('origin_kind') != 'private_system':
        return False
    origin = metadata.get('origin')
    if not isinstance(origin, Mapping) or origin.get('kind') != 'private_system':
        return False
    keys = (
        _bootstrap_ref_key(metadata.get('producer_ref')),
        _bootstrap_ref_key(metadata.get('lifetime_ref')),
        _bootstrap_ref_key(origin.get('primary_ref')),
        _bootstrap_ref_key(origin.get('secondary_ref')),
    )
    return keys[0] is not None and all(key == keys[0] for key in keys[1:])


def render_registered_read(metadata: Mapping, payload: bytes, arguments: Mapping) -> dict:
    """Mirror pinned WorkspaceExecutionMixin._decoded_read, before byte reduction.

    Source: cpn/components/agent_loop/workspace.py at 2a80656, _decoded_read.
    The exact later tool message may be shorter because context reduction is a
    separate stage; this result is always labelled reconstruction only.
    """
    if not isinstance(payload, bytes) or not isinstance(arguments, Mapping):
        raise ValueError('read bytes and persisted arguments required')
    text = payload.decode('utf-8')
    raw_workspace = (metadata.get('origin_kind') == 'workspace_write'
                     and metadata.get('content_schema_ref') is None)
    if not raw_workspace:
        media = metadata.get('media_type')
        if media == 'application/json':
            decoded = json.loads(text)
            if isinstance(decoded, str):
                text = decoded
        elif not isinstance(media, str) or not media.startswith('text/'):
            raise ValueError('read_file requires registered text resource metadata')
    offset, maximum = arguments.get('offset_chars', 0), arguments.get('max_chars', 32768)
    if (type(offset) is not int or offset < 0 or type(maximum) is not int
            or not 1 <= maximum <= 32768):
        raise ValueError('invalid persisted read range')
    content = text[offset:offset + maximum]
    return {'content': content, 'offset_chars': offset, 'max_chars': maximum,
            'returned_chars': len(content), 'decoded_resource_chars': len(text),
            'evidence_boundary': 'registered_read_reconstruction_before_context_reduction',
            'proves_model_input': False}


@dataclass(frozen=True)
class Writer:
    node: str
    action: dict
    row: Any
    order: int


class WriterIndex:
    def __init__(self):
        self.exact = defaultdict(list)
        self.lineage = defaultdict(list)

    def add(self, *, ref, metadata, path, writer: Writer):
        self.exact[resource_key(ref)].append(writer)
        key = lineage_key(metadata, path)
        if key is not None:
            self.lineage[key].append(writer)

    @staticmethod
    def _prior_unique(values, before):
        unique = {}
        for value in values:
            if value.order >= before:
                continue
            identity = json.dumps(value.action['agent_action_ref'], sort_keys=True)
            unique.setdefault(identity, value)
        return list(unique.values())

    def resolve(self, *, ref, metadata, before: int):
        candidates = self._prior_unique(self.exact.get(resource_key(ref), ()), before)
        method = 'exact_resource_version'
        if not candidates:
            key = lineage_key(metadata)
            candidates = self._prior_unique(self.lineage.get(key, ()), before)
            method = 'unique_producer_and_workspace_path'
        if len(candidates) == 1:
            return candidates[0], method
        if len(candidates) > 1:
            return None, 'ambiguous_product_versions'
        # Only explicitly labelled owner ingress is classed as non-handoff.
        # Missing origin/lineage is unknown, never a silent zero-handoff result.
        if _known_owner_ingress(metadata):
            return None, 'known_owner_ingress'
        return None, 'unknown_product_origin'


class RequestVisibilityIndex:
    """Index only acknowledged prompt envelopes named by LLM call specs.

    Never search arbitrary task files for envelope-shaped JSON. The matched
    caller invocation, tool call id, assistant arguments and tool response are
    preserved. Logical recipe string messages are preserved; unresolved
    placeholder bodies are gaps.  The prompt-delivery acknowledgement proves
    the exact registered request crossed the RPNH ``llm_prompt`` boundary; it
    does not make claims about a provider's unobservable internal processing.
    """
    def __init__(self):
        self.envelopes = []
        self.issues = []

    @classmethod
    def from_snapshot(cls, snapshot):
        index = cls()
        for kind in ('llm_call_spec/v2', 'llm_call_spec/v3'):
            for row in snapshot.rows(kind):
                spec = json.loads(str(row['metadata_json']))
                ref = spec.get('request_resource_ref')
                try:
                    if not isinstance(ref, Mapping):
                        raise ValueError('request_resource_ref_missing')
                    _, payload = snapshot.resource(ref)
                    envelope = json.loads(payload)
                    if not isinstance(envelope, dict) or not (
                            envelope.get('protocol') == 'llm_request_envelope/v1'
                            or envelope.get('schema_version') == 'logical_provider_request_recipe/v1'):
                        raise ValueError('unsupported_request_envelope')
                    messages = envelope.get('messages')
                    if not isinstance(messages, list):
                        raise ValueError('request_messages_missing')
                    invocation = spec.get('invocation_ref')
                    if not isinstance(invocation, Mapping) or not invocation.get('version_id'):
                        raise ValueError('request_caller_invocation_missing')
                    delivery_ref = spec.get('terminal_delivery_ref')
                    if not isinstance(delivery_ref, Mapping):
                        raise ValueError('prompt_delivery_ref_missing')
                    delivery = snapshot.exact(
                        delivery_ref, expected='resource_delivery/v1')
                    if (delivery.get('state') != 'acknowledged'
                            or delivery.get('boundary') != 'llm_prompt'
                            or delivery.get('resource_ref') != ref):
                        raise ValueError('prompt_delivery_not_acknowledged')
                    index.envelopes.append((dict(invocation), dict(ref), messages,
                                            spec.get('llm_call_ref'),
                                            dict(delivery_ref)))
                except (OSError, KeyError, TypeError, ValueError) as exc:
                    index.issues.append({'request_resource_ref': ref,
                                         'reason': str(exc)})
        return index

    def for_read(self, *, invocation_ref: Mapping, action: Mapping) -> list[dict]:
        call_id = action.get('tool_call_id')
        args = action.get('arguments')
        if not isinstance(call_id, str) or not call_id or not isinstance(args, Mapping):
            return []
        visible = []
        for invocation, request_ref, messages, call_ref, delivery_ref in self.envelopes:
            if invocation != invocation_ref:
                continue
            call_id_occurrences = sum(
                1 for message in messages if isinstance(message, Mapping)
                and message.get('role') == 'assistant'
                for call in (message.get('tool_calls') or [])
                if isinstance(call, Mapping) and call.get('id') == call_id)
            paired = False
            for number, message in enumerate(messages):
                if not isinstance(message, Mapping):
                    continue
                if message.get('role') == 'assistant':
                    for call in message.get('tool_calls') or []:
                        if not isinstance(call, Mapping) or call.get('id') != call_id:
                            continue
                        # A reused id replaces the preceding pairing; malformed
                        # or different later calls must not inherit prior proof.
                        paired = False
                        function = call.get('function')
                        if not isinstance(function, Mapping):
                            continue
                        raw = function.get('arguments')
                        try:
                            parsed = json.loads(raw) if isinstance(raw, str) else raw
                        except ValueError:
                            continue
                        paired = function.get('name') == 'read_file' and parsed == args
                if (not paired or message.get('role') != 'tool'
                        or message.get('tool_call_id') != call_id):
                    continue
                raw = message.get('content')
                if not isinstance(raw, str):
                    # Structured multimodal tool bodies need an explicit resolver.
                    continue
                content = raw
                interpretation = 'exact_tool_message_text'
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = None
                if isinstance(body, Mapping) and body.get('kind') == 'registered_file_read/v1':
                    meta = action.get('result_metadata') or {}
                    if body.get('resource_ref') != meta.get('resource_ref'):
                        continue
                    if body.get('use_receipt_ref') != meta.get('use_receipt_ref'):
                        continue
                    if isinstance(body.get('content'), str):
                        content = body['content']
                        interpretation = 'content_field_of_exact_tool_message'
                elif call_id_occurrences != 1:
                    # A truncated message lacks exact receipt metadata. Reused
                    # call ids alone cannot identify which read it belongs to.
                    continue
                visible.append({'content': content, 'raw_tool_message': raw,
                                'request_resource_ref': request_ref,
                                'llm_call_ref': call_ref, 'message_index': number,
                                'prompt_delivery_ref': delivery_ref,
                                'tool_call_id': call_id, 'interpretation': interpretation,
                                'proof_scope': 'acknowledged_registered_llm_prompt'})
        # Retain references to all observed messages; callers deduplicate content,
        # not the evidence provenance or actual task actions.
        return visible

    def for_managed_result(
            self, *, invocation_ref: Mapping,
            action: Mapping) -> list[dict]:
        """Return exact managed results in acknowledged later prompts.

        A returned action alone is backend evidence.  It becomes a
        model-visible observation only when the same call name, arguments,
        managed result body, and terminal receipt occur together in an
        acknowledged prompt for the owning invocation.
        """
        if action.get('outcome') != 'returned':
            return []
        call_id = action.get('tool_call_id')
        tool_name = action.get('tool_name')
        arguments = action.get('arguments')
        terminal_ref = action.get('terminal_receipt_ref')
        expected_body = {
            'kind': 'managed_native_plugin_result/v1',
            'output': action.get('output'),
            'terminal_receipt_ref': terminal_ref,
        }
        if (not isinstance(call_id, str) or not call_id
                or not isinstance(tool_name, str) or not tool_name
                or not isinstance(arguments, Mapping)
                or not isinstance(terminal_ref, Mapping)):
            return []
        visible = []
        for invocation, request_ref, messages, call_ref, delivery_ref in self.envelopes:
            if invocation != invocation_ref:
                continue
            paired = False
            for number, message in enumerate(messages):
                if not isinstance(message, Mapping):
                    continue
                if message.get('role') == 'assistant':
                    for call in message.get('tool_calls') or []:
                        if not isinstance(call, Mapping) or call.get('id') != call_id:
                            continue
                        paired = False
                        function = call.get('function')
                        if not isinstance(function, Mapping):
                            continue
                        raw_arguments = function.get('arguments')
                        try:
                            parsed = (json.loads(raw_arguments)
                                      if isinstance(raw_arguments, str)
                                      else raw_arguments)
                        except ValueError:
                            continue
                        paired = (
                            function.get('name') == tool_name
                            and parsed == arguments)
                if (not paired or message.get('role') != 'tool'
                        or message.get('tool_call_id') != call_id):
                    continue
                raw = message.get('content')
                if not isinstance(raw, str):
                    continue
                try:
                    body = json.loads(raw)
                except ValueError:
                    continue
                if body != expected_body:
                    continue
                output = body['output']
                tool_result = (
                    output['raw_result']
                    if isinstance(output, Mapping)
                    and isinstance(output.get('raw_result'), str)
                    else json.dumps(
                        output, ensure_ascii=False, sort_keys=True,
                        separators=(',', ':')))
                visible.append({
                    'tool_result': tool_result,
                    'raw_tool_message': raw,
                    'request_resource_ref': request_ref,
                    'llm_call_ref': call_ref,
                    'message_index': number,
                    'prompt_delivery_ref': delivery_ref,
                    'tool_call_id': call_id,
                    'proof_scope': 'acknowledged_registered_llm_prompt',
                })
        return visible
