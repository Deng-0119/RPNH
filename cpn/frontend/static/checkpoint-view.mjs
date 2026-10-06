import { stable } from './model.mjs';
import { normalizeFrame } from './dashboard-model.mjs';
import { messageError } from './i18n.mjs';
const invalid = () => { throw messageError('保存检查点响应的身份或边界无效'); };
const keys = (value, names) => value && typeof value === 'object' && !Array.isArray(value)
    && stable(Object.keys(value).sort()) === stable([...names].sort());
const kinds = {'net_instance/v1': ['net_instance', 'net_instance_version'],
    'marking_checkpoint/v1': ['marking_checkpoint', 'marking_checkpoint_version'],
    'petri_token/v1': ['petri_token', 'petri_token_version'], 'task/v1': ['task', 'task_version'],
    'native_run_identity/v1': ['run', 'run_version']};
export function checkpointReference(value, type, legacy = false) {
    if (legacy && value && value.entity_id) {
        if (!keys(value, ['entity_type', 'entity_id', 'version_id'])) invalid();
        const text = v => {
            if (typeof v === 'string') return v;
            if (!keys(v, ['kind', 'value']) || typeof v.kind !== 'string' || typeof v.value !== 'string') invalid();
            return `${v.kind}:${v.value}`;
        };
        value = {entity_type: value.entity_type, logical_id: text(value.entity_id), version_id: text(value.version_id)};
    }
    if (!keys(value, ['entity_type', 'logical_id', 'version_id']) || value.entity_type !== type
        || typeof value.logical_id !== 'string' || typeof value.version_id !== 'string') invalid();
    const expected = kinds[type];
    if (!expected || !expected.every((kind, i) => new RegExp(`^${kind}:[a-f0-9]{32}$`).test(value[i ? 'version_id' : 'logical_id']))) invalid();
    return {...value};
}
export function checkpointSelector(value) {
    if (!keys(value, ['net_ref', 'checkpoint_ref', 'cut']) || !Number.isSafeInteger(value.cut) || value.cut < 1) invalid();
    return {net_ref: checkpointReference(value.net_ref, 'net_instance/v1'),
        checkpoint_ref: checkpointReference(value.checkpoint_ref, 'marking_checkpoint/v1'), cut: value.cut};
}
export function selectorForFrame(frame) {
    return checkpointSelector({net_ref: checkpointReference(frame.source.net_ref, 'net_instance/v1', true),
        checkpoint_ref: checkpointReference(frame.net.marking.checkpoint_ref, 'marking_checkpoint/v1', true), cut: frame.position.cursor});
}
export function checkpointPath(value) {
    const selected = checkpointSelector(value);
    return '/api/v2/checkpoint-view?' + Object.entries(selected).map(([k,v]) =>
        `${k}=${encodeURIComponent(typeof v === 'object' ? JSON.stringify(v) : String(v))}`).join('&');
}
/** Validate the new envelope before sending its frame to the old normalizer. */
export function normalizeCheckpointView(value, expected, currentSource = null) {
    const selected = checkpointSelector(value?.selector), requested = checkpointSelector(expected);
    if (value?.schema_version !== 'rpnh/checkpoint_view/v1' || stable(selected) !== stable(requested)) invalid();
    const raw = value.frame, capture = value.capture, navigation = value.navigation;
    if (raw?.schema_version !== 'rpnh/dashboard/v1' || raw.position?.mode !== 'history'
        || raw.position.cursor !== selected.cut || raw.source?.verified_head_ordinal !== selected.cut
        || stable(raw.source.net_ref) !== stable(selected.net_ref)
        || stable(raw.net?.marking?.checkpoint_ref) !== stable(selected.checkpoint_ref)
        || !Number.isSafeInteger(capture?.head_ordinal) || capture.head_ordinal < selected.cut
        || !Number.isSafeInteger(capture.writer_fencing_epoch) || capture.writer_fencing_epoch < 0
        || raw.position.latest_head !== capture.head_ordinal
        || raw.source.writer_fencing_epoch !== capture.writer_fencing_epoch
        || raw.net?.source?.writer_fencing_epoch !== capture.writer_fencing_epoch
        || raw.source.mode !== 'registry_current' || raw.net.source.mode !== 'registry_current'
        || (raw.net.source.task_id != null && raw.net.source.task_id !== raw.source.task_id)
        || typeof raw.source.run_dir !== 'string'
        || typeof raw.source.task_id !== 'string'
        || (currentSource && (raw.source.run_dir !== currentSource.run_dir || raw.source.task_id !== currentSource.task_id))) invalid();
    if (!navigation || !['complete', 'partial'].includes(navigation.coverage)
        || !['net_version_boundary', 'reader_limit', 'initial_checkpoint'].includes(navigation.end_reason)
        || !Number.isSafeInteger(navigation.loaded_checkpoints) || navigation.loaded_checkpoints < 1
        || !Number.isSafeInteger(navigation.max_chain) || navigation.max_chain < navigation.loaded_checkpoints) invalid();
    if (navigation.previous_net_segment != null) {
        const previous = checkpointSelector(navigation.previous_net_segment);
        if (previous.cut >= selected.cut || stable(previous.net_ref) === stable(selected.net_ref)
            || navigation.end_reason !== 'net_version_boundary') invalid();
    } else if (navigation.end_reason === 'net_version_boundary') invalid();
    if (navigation.end_reason === 'reader_limit' && navigation.coverage !== 'partial') invalid();
    const evidence = value.adoption_evidence;
    if (!evidence || evidence.cut !== selected.cut || !['complete', 'partial', 'not_provided'].includes(evidence.coverage)
        || !['current_at_cut', 'previously_adopted', 'no_evidence_at_cut', 'unknown'].includes(evidence.status)) invalid();
    if (evidence.status !== 'unknown' && evidence.coverage !== 'complete') invalid();
    if (evidence.status === 'current_at_cut' && stable(evidence.current_net_ref) !== stable(selected.net_ref)) invalid();
    if (evidence.status === 'previously_adopted' && stable(evidence.current_net_ref) === stable(selected.net_ref)) invalid();
    if (!Array.isArray(evidence.records) || evidence.records.some(r =>
        !Number.isSafeInteger(r.recorded_ordinal) || r.recorded_ordinal < 1
        || !Number.isSafeInteger(r.visible_at_commit) || r.visible_at_commit < r.recorded_ordinal
        || r.visible_at_commit > selected.cut || stable(r.net_ref) !== stable(selected.net_ref))) invalid();
    if (evidence.status === 'no_evidence_at_cut' && evidence.records.length) invalid();
    if (['current_at_cut', 'previously_adopted'].includes(evidence.status) && !evidence.records.length) invalid();
    if (raw.coverage?.history !== 'selected_saved_checkpoint' || raw.coverage.firings !== 'not_provided'
        || value.coverage?.firings !== 'not_provided' || raw.net.nodes.some(n => n.runtime != null)) invalid();
    // Validate transition membership as well as net/checkpoint identity.
    const nodes = new Map(raw.net.nodes.map(n => [n.id, n]));
    if (!Array.isArray(raw.transition_bindings) || new Set(raw.transition_bindings.map(b => b.transition_id)).size !== raw.transition_bindings.length
        || raw.transition_bindings.length !== raw.net.nodes.filter(n => n.kind === 'transition').length
        || raw.transition_bindings.some(b => nodes.get(b.transition_id)?.kind !== 'transition'
            || [...b.input_ports, ...b.output_ports].some(p => nodes.get(p.place)?.kind !== 'place'))) invalid();
    return {...value, selector: selected, frame: normalizeFrame(raw)};
}


const metadataInvalid = () => { throw messageError('资源登记响应的身份或范围无效'); };
const same = (a,b) => stable(a) === stable(b);
function resourcePair(value) {
    if (!keys(value, ['resource_id','resource_version_id'])
        || typeof value.resource_id !== 'string' || typeof value.resource_version_id !== 'string'
        || !/^resource:[a-f0-9]{32}$/.test(value.resource_id)
        || !/^resource_version:[a-f0-9]{32}$/.test(value.resource_version_id)) metadataInvalid();
    return {...value};
}
export function tokenResourceTarget(value) {
    if (!keys(value,['token_ref','resource_ref','expected_task_id','expected_capture'])
        || typeof value.expected_task_id !== 'string'
        || !/^task:[a-f0-9]{32}$/.test(value.expected_task_id)
        || !keys(value.expected_capture,['head_ordinal','writer_fencing_epoch'])
        || !Number.isSafeInteger(value.expected_capture.head_ordinal) || value.expected_capture.head_ordinal < 1
        || !Number.isSafeInteger(value.expected_capture.writer_fencing_epoch) || value.expected_capture.writer_fencing_epoch < 0) metadataInvalid();
    return {token_ref:checkpointReference(value.token_ref,'petri_token/v1'),resource_ref:resourcePair(value.resource_ref),
        expected_task_id:value.expected_task_id,expected_capture:{...value.expected_capture}};
}
export function tokenResourceRequest(frame, token, capture=null) {
    const selected=selectorForFrame(frame);
    const tokens=frame.net.nodes.filter(n=>n.kind==='place').flatMap(n=>n.tokens??[]);
    const matches=tokens.filter(t=>same(t.token_ref,token?.token_ref));
    if(matches.length!==1 || !same(matches[0],token) || typeof frame.source.run_dir!=='string') metadataInvalid();
    const target=tokenResourceTarget({token_ref:token.token_ref,resource_ref:token.resource_ref,
        expected_task_id:frame.source.task_id,expected_capture:capture??{
            head_ordinal:frame.position.latest_head,writer_fencing_epoch:frame.source.writer_fencing_epoch}});
    if (selected.cut > target.expected_capture.head_ordinal) metadataInvalid();
    return {selected,target};
}
export function tokenResourcePath(request) {
    return checkpointPath(request.selected)+'&token_resource='+encodeURIComponent(JSON.stringify(tokenResourceTarget(request.target)));
}
export function normalizeTokenResourceMetadata(value, request, frame) {
    const target=tokenResourceTarget(request.target), selected=checkpointSelector(request.selected);
    const envelope=normalizeCheckpointView(value,selected,frame.source), metadata=envelope.token_resource_metadata;
    if(!keys(metadata,['schema_version','scope','occurrence','registered_metadata','registration','coverage','verification'])
        || metadata.schema_version!=='rpnh/token_resource_metadata/v1') metadataInvalid();
    const s=metadata.scope,o=metadata.occurrence,m=metadata.registered_metadata,r=metadata.registration;
    if(!keys(s,['task_ref','run_ref','net_ref','checkpoint_ref','cut','capture','token_ref','resource_ref'])
        || !keys(o,['token_ref','resource_ref','place','active_in_checkpoint'])
        || !keys(m,['byte_size','media_type','content_schema_ref'])
        || !keys(r,['published_event_id','publication_recorded_ordinal','publication_transaction_commit_ordinal'])) metadataInvalid();
    checkpointReference(s.task_ref,'task/v1');checkpointReference(s.run_ref,'native_run_identity/v1');
    if (s.task_ref.logical_id!==target.expected_task_id || !same(s.capture,target.expected_capture)
        || !same(envelope.capture,target.expected_capture) || !same(selectorForFrame(frame),selected)
        || !same({net_ref:s.net_ref,checkpoint_ref:s.checkpoint_ref,cut:s.cut},selected)
        || ['token_ref','resource_ref'].some(k=>!same(s[k],target[k]) || !same(o[k],target[k]))) metadataInvalid();
    const occurrences=frame.net.nodes.flatMap(n=>n.tokens??[]).filter(t=>same(t.token_ref,target.token_ref));
    const responseOccurrences=envelope.frame.net.nodes.flatMap(n=>n.tokens??[]).filter(t=>same(t.token_ref,target.token_ref));
    if (occurrences.length!==1 || responseOccurrences.length!==1 || typeof o.place!=='string'
        || typeof o.active_in_checkpoint!=='boolean'
        || Object.keys(o).some(k=>!same(o[k],occurrences[0][k]) || !same(o[k],responseOccurrences[0][k]))
        || !Number.isSafeInteger(m.byte_size) || m.byte_size<0 || typeof m.media_type!=='string'
        || !(m.content_schema_ref===null || typeof m.content_schema_ref==='string')
        || typeof r.published_event_id !== 'string' || !/^event:[a-f0-9]{32}$/.test(r.published_event_id)
        || !Number.isSafeInteger(r.publication_recorded_ordinal) || r.publication_recorded_ordinal<1
        || !Number.isSafeInteger(r.publication_transaction_commit_ordinal)
        || r.publication_transaction_commit_ordinal<r.publication_recorded_ordinal || r.publication_transaction_commit_ordinal>selected.cut
        || !same(metadata.coverage,{status:'complete',scope:'one_checkpoint_token_resource'})
        || !same(metadata.verification,{metadata_scope:'canonical_at_selected_checkpoint',body_read_by_metadata_projection:false,
            content_validation:'not_performed',actual_verified_byte_size:null})) metadataInvalid();
    // Return only the validated narrow extension; never replace the main frame.
    return structuredClone(metadata);
}
