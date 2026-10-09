"""Private complete Core and requested Start/Claims; no public profile endpoint.

Literal P/V/O tables retain dependency/v3 and PO-E01-20261009.
"""

READER_TABLE_REVISION = 'rpnh/product_origin_readers/v1'
DEPENDENCY_REVISION = 'rpnh/product_origin_dependencies/v3'
CORE_RECORD_FIELDS = (
    ('invocation/v1', ('invocation_ref', 'task_ref', 'net_instance_ref',
        'own_transition_firing_ref', 'operation_binding_ref', 'operation_execution_lease_ref')),
    ('transition_firing/v1', ('transition_firing_ref', 'task_ref', 'net_instance_ref',
        'operation_binding_ref', 'firing_admission_ref', 'claim_marking_delta_ref',
        'admission_marking_checkpoint_ref')),
    ('firing_admission/v1', ('firing_admission_ref', 'transition_firing_ref', 'invocation_ref',
        'operation_execution_lease_ref', 'claim_marking_delta_ref', 'admission_marking_checkpoint_ref')),
    ('firing_completion/v2', ('firing_completion_ref', 'transition_firing_ref', 'invocation_ref',
        'operation_result_ref', 'successor_checkpoint_ref')),
    ('operation_result/v1', ('operation_result_ref', 'invocation_ref', 'transition_firing_ref', 'business_outcome')),
    ('marking_checkpoint/v1', ('marking_checkpoint_ref', 'net_ref', 'settled', 'transition_firing_refs')),
)
CORE_INDEX_FIELDS = (('firing_completion/v2', ('transition_firing_ref',
    'firing_completion_ref', 'invocation_ref', 'operation_result_ref')),)
RESOURCE_ROOT_FIELDS = ('resource_id', 'resource_version_id', 'origin_kind', 'producer_ref',
    'provenance_producer_invocation_ref', 'provenance_operation_binding_ref')
RESOURCE_OUTPUT_FIELDS = ('output_resource_refs',)
CORE_FIELD_KINDS = (
    ('invocation_ref', 'ref'), ('task_ref', 'ref'), ('net_instance_ref', 'ref'),
    ('own_transition_firing_ref', 'ref'), ('operation_binding_ref', 'ref'),
    ('operation_execution_lease_ref', 'ref'), ('transition_firing_ref', 'ref'),
    ('firing_admission_ref', 'ref'), ('claim_marking_delta_ref', 'ref'),
    ('admission_marking_checkpoint_ref', 'ref'), ('firing_completion_ref', 'ref'),
    ('operation_result_ref', 'ref'), ('successor_checkpoint_ref', 'ref'),
    ('business_outcome', 'string'), ('marking_checkpoint_ref', 'ref'), ('net_ref', 'ref'),
    ('settled', 'boolean'), ('transition_firing_refs', 'array'),
    ('resource_id', 'string'), ('resource_version_id', 'string'), ('origin_kind', 'string'),
    ('producer_ref', 'ref'), ('provenance_producer_invocation_ref', 'ref'),
    ('provenance_operation_binding_ref', 'ref'), ('output_resource_refs', 'array'),
)

# This data is fingerprinted in full, alongside the concrete typed field and
# frozen default tables. A revision label alone is not a dependency contract.
CORE_DEPENDENCIES = (
    ('exact', 'same source/cut; full identity/kinds and registry_v1/type; bounded descriptor bytes=metadata; '
        'resource metadata/envelope only; unique object publication and committed transaction; '
        'promotion-aware visibility; row/publication producer equality; limited built-in owner/net validation'),
    ('admission', 'I/F selected exact task T, round Q, net N, binding B, node; I/F/A/L same transaction; '
        'I object producer null; F/A/L producer I; exact T/Q/N/B/L/Ka; L.invocation=I; '
        'F/A linked Dc identity only; I/F/A Ka equality; Ka net=N and committed witness'),
    ('resource', 'petri_output or workspace_write; producer/provenance=I; task/round/net=T/Q/N; '
        'provenance binding=B; fixed provenance keys/schema/sorted unique typed arrays/tool=[]; '
        'embedded publication and consumer exact; petri output_binding Q/N/node and activation; '
        'workspace primary=B and exact intent binding=B; no provenance endpoint expansion'),
    ('PO-E01-20261009', 'one exact strong produced_by, empty metadata, target I, producer I; '
        'same original resource publication transaction; stable relation ID from its original '
        'committed transaction key captured at cut; publication witnesses; no promotion-key substitution'),
    ('E02', 'one bounded same-logical-resource produced_by pass; reject malformed/nonexact/unregistered '
        'source endpoints; charge all candidates; valid exact sibling stops at registered source identity'),
    ('completion', 'original authorized IndexQuery eq F; explicit Core index projection; complete '
        'same-cut bounded collection; readable source and exactly one C; no internal cursor; late errors fail'),
    ('settlement', 'C/R completed and exact I/F; Ds phase=settlement,N,[F],[B]; Ks settled,Ds,[F]; '
        'collect all relevant type-or-schema-labelled settled candidates by aggregate F or payload '
        'F/C/R/Ds/Ks identity before validation; exactly one; authoritative typed envelope T/Q/N/I; '
        'payload exact refs and transaction/event kinds; C/Ds/Ks same Success transaction, R may be earlier; '
        'workspace nullable refs equal without dereference'),
    ('checkpoint', 'unconditional existing committed predicate; matching payload arrays charged; '
        'Ka/Kp exact committed witnesses; Kp nonnull and may differ Ka; Ks witness same Success '
        'transaction/producer; common integrity before changed-net unsupported; same-net Ks/Kp=N; no replay'),
    ('membership', 'resource root: inspect every generic resource output ref, exact kind and uniqueness '
        'without dereferencing other outputs; member registered_output else invocation_produced_resource; '
        'result root operation_result, no output semantic expansion'),
    ('delivery', 'six-field private proof only after full Core; one query-owned W=D+E+Rel+O+C+S+A, '
        'independent capture limit; reserve before allocation; actual same-contract results reused; '
        'query-only cache; protected errors and success serialized before final authority/TTL recheck'),
)


INCLUDE_ORDER = ('producer_execution', 'start_inputs', 'claims')
START_FIELDS = ('start_event_id', 'start_transaction_id', 'start_ordinal',
    'start_input_binding_refs', 'start_input_resource_refs')
INCLUDE_RECORD_FIELDS = (
    ('start_inputs', (('transition_firing/v1', START_FIELDS),)),
    ('claims', (('transition_firing/v1', ('claimed_input_refs',)),
        ('marking_delta/v1', ('marking_delta_ref', 'net_instance_ref', 'phase',
            'transition_firing_refs', 'operation_binding_refs', 'consumed_refs')),
        ('petri_token/v1', ('petri_token_ref', 'net_instance_ref', 'resource_ref')))),
)
INCLUDE_FIELD_KINDS = (
    ('start_event_id', 'string'), ('start_transaction_id', 'string'), ('start_ordinal', 'number'),
    ('start_input_binding_refs', 'array'), ('start_input_resource_refs', 'array'),
    ('claimed_input_refs', 'array'), ('marking_delta_ref', 'ref'), ('phase', 'string'),
    ('operation_binding_refs', 'array'), ('consumed_refs', 'array'),
    ('petri_token_ref', 'ref'), ('resource_ref', 'ref'),
)
INCLUDE_DEPENDENCIES = (
    ('claimed-summary/v1', 'original cached F array; C per full token ref; unique full identity; '
        'A per F version ID; strict petri_token_version IDs; sorted set equals original version summary; '
        'retain original order and actual completed membership result shared by Start and Claims'),
    ('start-anchor/v1', 'F->A->I/L and B; exact same-cut identity/schema/publication; '
        'I/F task equals selected full exact task; I/F round/net/B/node and admission joins; '
        'exact T/Q/N/B/L; I null producer and F/A/L producer I in admission transaction; '
        'no Success/completion, checkpoint traversal or extra P requirements'),
    ('start-event/v1', 'collect every type-or-schema-labelled candidate touching L aggregate/stream '
        'or payload I/F/L logical identity before validation; E charged for all candidates first; '
        'exactly one authoritative typed envelope T/Q/N/I/principal and unique committed transaction; '
        'payload exact I/F/L/B; explicit spec/executable/principal/authority/agent/delivery kinds only; '
        'strict counters; A per event claimed position, original order equals F; no target dereference'),
    ('start-pairs/v1', 'equal arrays, unique binding full refs, S per original position; '
        'static generic resource member of B input array and exact same actual resource; '
        'A per B array member; token belongs to F claims and exact/net=N; actual resource '
        'two-field identity/schema/publication/envelope only, any original legal origin; '
        'token resource may differ actual resource; endpoint validation cached by exact contract'),
    ('claims/v1', 'Dc exact/publication/P fields; self/net=N/phase=claim/[F]/[B]/deposited=[]; '
        'producer=I and transaction=A admission; A per singleton/deposit array; '
        'C per consumed position, unique full-ref subset of complete F claims; '
        'each token exact/publication/net=N plus original P; nullable two-field resource, no target read'),
    ('ordinary-start/v1', 'any selected authorized Start field exact/index projection/predicate; '
        'same bounded source-local owner through serialization/final recheck, selected P only; '
        'selected derived keys/values compared with cached finite Start after original HOST callback; A per expected and returned comparison-array position; no Core completion precondition; no signature or legacy-default change; '
        'source owners retain all live collection bytes and invalidated snapshots; pre-hydration capture reservations retain pending snapshots through safe final error delivery; no unowned fallback'),
    ('delivery/v1', 'one owner and one mandatory Core evaluation for private full include result; '
        'all requested validations before fixed rows; custom catalog selected key/value comparison '
        'against independently cached Start; final authority/TTL check then close; '
        'all rows scan-accounted, max_response_bytes remains future page limit'),
)
INCLUDE_OUTPUT_SHAPES = (
    ('start_input', ('role', 'position', 'input_binding_ref', 'resource_ref',
        ('evidence', ('start_event_id', 'start_transaction_id', 'start_ordinal')),
        ('verification', (('binding_identity', 'exact_at_cut'), ('resource_identity', 'exact_at_cut'),
            ('target_record', 'not_requested'), ('material', 'not_read'))))),
    ('claim', ('role', 'token_ref', 'resource_ref', 'classification',
        ('evidence', ('transition_firing_ref', 'claim_marking_delta_ref')),
        ('verification', (('token_record', 'verified_at_cut'), ('resource_target', 'not_requested'),
            ('material', 'not_read'))))),
)


def record_fields(resource_root, include=('producer_execution',)):
    result = dict(CORE_RECORD_FIELDS)
    if resource_root:
        result['resource_version/v1'] = RESOURCE_ROOT_FIELDS
        result['operation_result/v1'] += RESOURCE_OUTPUT_FIELDS
    for relation, table in INCLUDE_RECORD_FIELDS:
        if relation in include:
            for kind, fields in table:
                result[kind] = result.get(kind, ()) + fields
    return tuple(result.items())


def core_contract():
    return {'reader_table_revision': READER_TABLE_REVISION,
        'dependency_revision': DEPENDENCY_REVISION, 'record_fields': CORE_RECORD_FIELDS,
        'index_fields': CORE_INDEX_FIELDS, 'resource_root_fields': RESOURCE_ROOT_FIELDS,
        'resource_output_fields': RESOURCE_OUTPUT_FIELDS, 'field_kinds': CORE_FIELD_KINDS,
        'dependencies': CORE_DEPENDENCIES, 'include_order': INCLUDE_ORDER,
        'include_record_fields': INCLUDE_RECORD_FIELDS, 'include_field_kinds': INCLUDE_FIELD_KINDS,
        'include_dependencies': INCLUDE_DEPENDENCIES, 'include_output_shapes': INCLUDE_OUTPUT_SHAPES,
        'row_order': ('start_input original position', 'claim source/entity/logical/version'),
        'claim_classifications': ('consumed_claim', 'non_consuming_claim'),
        'input_binding_wire': 'source-qualified generic resource-or-token VersionRef',
        'input_resource_wire': 'source-qualified two-field ResourceVersionRef',
        'claim_resource_wire': 'null or source-qualified two-field ResourceVersionRef'}
