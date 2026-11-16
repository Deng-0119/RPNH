/**
 * In-memory editing and handoff for the original Owner material protocol.
 *
 * This controller neither grants access nor decides Registry completion. The
 * injected API must reach the selected, already-authorized HOST/Owner. Commands
 * and input receipts remain original Registry facts; these cards only display
 * replies and retain stable query identities when a response is lost.
 *
 * The browser's read projection is separate. A caller must clear revoked read
 * content through the existing reader and refresh management capabilities.
 * Do not render values with innerHTML or move focus from an onChange callback.
 */
export const AGENT_TEXT_TYPE = 'application/rpnh_agent_text/v1';

const INPUT = 'rpnh/material_input/v1';
const INPUT_RECEIPT = 'rpnh/material_input_receipt/v1';
const COMMAND = 'rpnh/material_command/v1';
const SUBMIT_RECEIPT = 'rpnh/material_submit_receipt/v1';
const RECEIPT_VIEW = 'rpnh/material_receipt_view/v1';
const ACTIVE = new Set(['REGISTERED', 'ADMITTED', 'PREPARED', 'RECOVERY_REQUIRED']);
const FINAL = new Set(['PUBLISHED', 'REJECTED']);

const copy = value => structuredClone(value);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const nonempty = value => typeof value === 'string' && value.length > 0;
const equal = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const invalid = reason => { throw new Error(reason); };

function ref(value, type = null) {
  if (!object(value) || Object.keys(value).sort().join(',') !== 'entity_type,logical_id,version_id'
      || !nonempty(value.entity_type) || !nonempty(value.logical_id) || !nonempty(value.version_id)
      || (type !== null && value.entity_type !== type)) invalid('INVALID_REFERENCE');
  return {entity_type: value.entity_type, logical_id: value.logical_id, version_id: value.version_id};
}

function target(value) {
  if (!object(value) || !nonempty(value.id) || !nonempty(value.label)
      || !nonempty(value.target) || !['create_text', 'replace_text'].includes(value.operation)
      || !object(value.execution) || !object(value.address)
      || !nonempty(value.address.opaque_name) || value.address.opaque_name.includes('\0')) {
    invalid('INVALID_TARGET');
  }
  const execution = {
    task_ref: ref(value.execution.task_ref, 'task/v1'),
    task_round_ref: ref(value.execution.task_round_ref, 'task_round/v1'),
    net_instance_ref: ref(value.execution.net_instance_ref, 'net_instance/v1'),
  };
  const address = {scope_ref: ref(value.address.scope_ref, 'task/v1'), opaque_name: value.address.opaque_name};
  if (!equal(address.scope_ref, execution.task_ref)) invalid('INVALID_TARGET_SCOPE');
  if (!Object.hasOwn(value, 'expected_binding_ref') || !Object.hasOwn(value, 'base_resource_ref')) {
    invalid('INVALID_TARGET_BASE');
  }
  const expected = value.expected_binding_ref === null ? null
    : ref(value.expected_binding_ref, 'resource_address_binding/v1');
  const base = value.base_resource_ref === null ? null : ref(value.base_resource_ref, 'resource_version/v1');
  if (value.operation === 'replace_text' && (expected === null || base === null)) invalid('INVALID_TARGET_BASE');
  if (value.operation === 'create_text' && base !== null) invalid('INVALID_TARGET_BASE');
  return {id: value.id, label: value.label, operation: value.operation, target: value.target,
    execution, address, expected_binding_ref: expected, base_resource_ref: base};
}

function deepFreeze(value) {
  if (object(value) || Array.isArray(value)) {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

function newId(kind) {
  if (typeof globalThis.crypto?.randomUUID !== 'function') invalid('COMMAND_ID_FACTORY_REQUIRED');
  return `material-${kind}:${globalThis.crypto.randomUUID()}`;
}

/**
 * API methods:
 *   prepareInput(CommandRequest), submit(CommandRequest)
 *   readInput({source_id,input_command_id})
 *   readReceipt({source_id,material_command_ref|material_command_id})
 *
 * `open` takes complete text and a list of targets disclosed by the service.
 * These are presentation checks only; the original Owner revalidates all input.
 * `prepare` freezes an intent; `submitPrepared` explicitly submits that frozen
 * intent. `save` performs those two steps for one explicit user action. Reads
 * never retry a write. A transport failure always leaves the outcome unknown.
 */
export function createMaterialEditor({api, onChange = () => {}, makeCommandId = newId} = {}) {
  if (!api || ['prepareInput', 'submit', 'readInput', 'readReceipt'].some(key => typeof api[key] !== 'function')) {
    invalid('MATERIAL_API_REQUIRED');
  }
  let draft = null, nextEditor = 0;
  const cards = new Map(), intents = new Map(), usedIds = new Set(), sourceCapabilities = new Map(), inputAbandonment = new Map();
  const capabilities = value => ({create: value?.create === true, revise: value?.revise === true,
    publish: value?.publish === true});
  const actionAllowed = (sourceId, selection) => sourceCapabilities.get(sourceId)?.[
    selection.operation === 'create_text' ? 'create' : 'revise'] === true;
  const publishAllowed = card => actionAllowed(card.sourceId, card.target)
    && sourceCapabilities.get(card.sourceId)?.publish === true;
  const canAbandon = card => typeof api.abandonInput === 'function' && inputAbandonment.get(card.sourceId) === true
    && card.inputRef !== null && card.submissionAllowed === true && card.status === 'INPUT_ACCEPTED'
    && !card.submitSent && !card.abandonSent && !card.inputFlight && !card.inputReadFlight && !card.error;
  const draftDirty = () => draft !== null && (draft.revision !== draft.handedOffRevision
    || [...cards.values()].some(card => card.editorId === draft.editor_id
      && card.revision === draft.revision && card.inputSent && card.inputRef === null));

  const cardView = card => ({
    input_command_id: card.inputId, command_id: card.commandId,
    source_id: card.sourceId, document_type: card.documentType,
    target: copy(card.target), input_ref: copy(card.inputRef),
    command_ref: copy(card.commandRef), input_status: card.inputStatus,
    input_current_state: card.inputCurrentState, submission_allowed: card.submissionAllowed,
    abandonment_ref: copy(card.abandonmentRef),
    abandonment_command_id: card.abandonCommandId, can_abandon: canAbandon(card),
    status: card.status, result: copy(card.result), error: card.error,
    can_query_input: card.inputSent, can_query_result: card.submitSent,
    // null means registration may have happened; it is never a negative fact.
    input_retained: card.inputRef !== null ? true : card.inputSent ? null : false,
    can_submit: card.inputRef !== null && card.submissionAllowed === true
      && !card.submitSent && !card.abandonSent && card.status === 'INPUT_ACCEPTED'
      && publishAllowed(card),
  });
  function state() {
    const selected = draft?.targets.find(item => item.id === draft.selected_target_id);
    const current = draft ? cards.get(intents.get(`${draft.editor_id}:${draft.revision}`)) : null;
    const canPrepare = draft !== null && draft.blocked === null && selected !== undefined
      && current?.submissionAllowed !== false && actionAllowed(draft.source_id, selected);
    return {draft: draft === null ? null : {...copy(draft),
      dirty: draftDirty(),
      capabilities: copy(sourceCapabilities.get(draft.source_id)),
      can_prepare: canPrepare,
      can_save: canPrepare && sourceCapabilities.get(draft.source_id)?.publish === true},
    operations: [...cards.values()].map(cardView)};
  }
  const emit = () => onChange(state());
  const getCard = id => cards.get(id) ?? invalid('UNKNOWN_INPUT_COMMAND');
  function allocate(kind) {
    const id = makeCommandId(kind);
    if (!nonempty(id) || usedIds.has(id)) invalid('INVALID_OR_REUSED_COMMAND_ID');
    usedIds.add(id);
    return id;
  }

  function open(options, {discardCurrent = false} = {}) {
    if (draftDirty() && !discardCurrent) invalid('UNSUBMITTED_DRAFT');
    if (!object(options) || !nonempty(options.source_id) || typeof options.value !== 'string'
        || !nonempty(options.document_type) || !Array.isArray(options.targets)) invalid('INVALID_EDITOR_INPUT');
    const targets = options.targets.map(target);
    if (new Set(targets.map(item => item.id)).size !== targets.length) invalid('DUPLICATE_TARGET');
    let blocked = null;
    if (options.complete !== true) blocked = 'INCOMPLETE_INPUT';
    else if (options.document_type !== AGENT_TEXT_TYPE) blocked = 'UNSUPPORTED_TYPE';
    else if (options.prepare_allowed !== true) blocked = 'PREPARATION_UNAVAILABLE';
    else if (targets.length === 0) blocked = 'NO_ALLOWED_TARGET';
    sourceCapabilities.set(options.source_id, capabilities(options.capabilities));
    inputAbandonment.set(options.source_id, options.input_abandonment_allowed === true);
    draft = {editor_id: ++nextEditor, source_id: options.source_id,
      document_type: options.document_type, value: options.value,
      complete: options.complete === true, targets, selected_target_id: targets[0]?.id ?? null,
      revision: 0, handedOffRevision: 0, blocked};
    emit();
    return state();
  }

  function updateCapabilities(sourceId, value, {inputAbandonmentAllowed = false} = {}) {
    if (!nonempty(sourceId)) invalid('INVALID_SOURCE');
    sourceCapabilities.set(sourceId, capabilities(value));
    inputAbandonment.set(sourceId, inputAbandonmentAllowed === true);
    emit();
    return state();
  }

  function edit(value) {
    if (!draft) invalid('NO_EDITOR');
    if (draft.blocked !== null) invalid(draft.blocked);
    if (typeof value !== 'string') invalid('INVALID_TEXT');
    if (draft.value !== value) { draft.value = value; draft.revision++; emit(); }
    return state();
  }

  function selectTarget(id) {
    if (!draft || !draft.targets.some(item => item.id === id)) invalid('TARGET_NOT_DISCLOSED');
    if (draft.selected_target_id !== id) { draft.selected_target_id = id; draft.revision++; emit(); }
    return state();
  }

  function close({discardDraft = false} = {}) {
    if (draftDirty() && !discardDraft) {
      return {closed: false, reason: 'UNSUBMITTED_DRAFT'};
    }
    draft = null;
    emit();
    // Closing a buffer does not cancel commands or discard registered inputs.
    return {closed: true};
  }

  function acceptInput(card, reply) {
    if (!object(reply) || reply.protocol !== INPUT_RECEIPT || reply.input_command_id !== card.inputId) {
      invalid('INVALID_INPUT_RECEIPT');
    }
    if (reply.status === 'NOT_FOUND_AT_READ_CUT') {
      if (card.inputRef === null) {
        card.inputStatus = 'NOT_FOUND_AT_READ_CUT';
        card.status = 'INPUT_UNKNOWN';
      }
      return;
    }
    if (reply.status !== 'INPUT_ACCEPTED') invalid('INVALID_INPUT_RECEIPT');
    const inputRef = ref(reply.input_ref, 'resource_version/v1');
    if (card.inputRef !== null && !equal(card.inputRef, inputRef)) invalid('INPUT_REFERENCE_CHANGED');
    let abandonmentRef = null;
    const hasCurrentState = ['current_state', 'submission_allowed', 'abandonment_ref']
      .some(key => Object.hasOwn(reply, key));
    if (hasCurrentState) {
      // An invalid current-state extension cannot keep an old submission gate
      // open. It is not itself proof of abandonment or a command result.
      card.submissionAllowed = false;
      if (reply.current_state !== 'ABANDONED' || reply.submission_allowed !== false) {
        invalid('INVALID_CURRENT_INPUT_STATE');
      }
      abandonmentRef = ref(reply.abandonment_ref, 'resource_version/v1');
      if (card.result !== null) invalid('INPUT_ABANDONMENT_CONTRADICTS_COMMAND');
      if (card.abandonmentRef !== null && !equal(card.abandonmentRef, abandonmentRef)) {
        invalid('INPUT_ABANDONMENT_CHANGED');
      }
    }
    card.inputRef = inputRef;
    card.inputStatus = 'INPUT_ACCEPTED';
    if (abandonmentRef !== null) {
      card.inputCurrentState = 'ABANDONED'; card.submissionAllowed = false;
      card.abandonmentRef = abandonmentRef;
    } else if (card.inputCurrentState !== 'ABANDONED' && !card.abandonSent) {
      card.submissionAllowed = true;
    }
    if (draft?.editor_id === card.editorId && draft.revision === card.revision) {
      draft.handedOffRevision = card.revision;
    }
    // Once the Owner confirms the exact input, the local card needs no copy of
    // its registered body. D2 remains the user's independent editing buffer.
    card.prepareRequest = null;
    if (card.inputCurrentState === 'ABANDONED') card.status = 'INPUT_ABANDONED';
    else if (card.abandonSent) card.status = 'INPUT_ABANDONMENT_UNKNOWN';
    else if (!card.submitSent) card.status = 'INPUT_ACCEPTED';
    card.error = null;
  }

  function acceptReceipt(card, reply) {
    if (!object(reply) || reply.command_id !== card.commandId
        || ![SUBMIT_RECEIPT, RECEIPT_VIEW].includes(reply.protocol)) invalid('INVALID_COMMAND_RECEIPT');
    if (reply.protocol === RECEIPT_VIEW && reply.status === 'NOT_FOUND_AT_READ_CUT') {
      if (card.commandRef === null) card.status = 'RESULT_UNKNOWN';
      return;
    }
    const commandRef = ref(reply.command_ref, 'resource_version/v1');
    if (card.commandRef !== null && !equal(card.commandRef, commandRef)) invalid('COMMAND_REFERENCE_CHANGED');
    if (reply.protocol === SUBMIT_RECEIPT && reply.status !== 'REGISTERED') invalid('INVALID_COMMAND_RECEIPT');
    if (!ACTIVE.has(reply.status) && !FINAL.has(reply.status)) invalid('INVALID_COMMAND_RECEIPT');
    // A delayed POST registration reply cannot replace a later GET projection,
    // including recovery-required states which are not a linear progress rank.
    if (reply.protocol === SUBMIT_RECEIPT && card.result !== null) return;
    // A registration response is never displayed as a completed save. Final
    // views must include the original result and the exact delivery references.
    const result = {protocol: reply.protocol, command_id: reply.command_id,
      command_ref: commandRef, status: reply.status};
    for (const [key, type] of Object.entries({admission_ref: 'firing_admission/v1',
      firing_ref: 'transition_firing/v1', invocation_ref: 'invocation/v1',
      result_ref: 'resource_version/v1', completion_ref: 'firing_completion/v2'})) {
      if (reply[key] != null) result[key] = ref(reply[key], type);
    }
    if (reply.status === 'PUBLISHED') {
      if (reply.protocol !== RECEIPT_VIEW || !result.result_ref || !result.completion_ref
          || !result.admission_ref || !result.firing_ref || !result.invocation_ref
          || !Array.isArray(reply.output_refs) || reply.output_refs.length === 0
          || !Array.isArray(reply.binding_refs) || reply.binding_refs.length === 0
          || reply.error !== null) invalid('INCOMPLETE_PUBLISHED_RECEIPT');
      result.output_refs = reply.output_refs.map(value => ref(value, 'resource_version/v1'));
      result.binding_refs = reply.binding_refs.map(value => ref(value, 'resource_address_binding/v1'));
      result.error = null;
    } else if (reply.status === 'REJECTED') {
      if (reply.protocol !== RECEIPT_VIEW || !result.result_ref || !object(reply.error)
          || !nonempty(reply.error.code) || reply.completion_ref != null
          || (reply.output_refs != null && (!Array.isArray(reply.output_refs) || reply.output_refs.length > 0))
          || (reply.binding_refs != null && (!Array.isArray(reply.binding_refs) || reply.binding_refs.length > 0))) {
        invalid('INCOMPLETE_REJECTION_RECEIPT');
      }
      result.error = {code: reply.error.code};
    }
    if (FINAL.has(card.status)) {
      if (!FINAL.has(result.status)) return;
      if (card.status !== result.status || !equal(card.result, result)) invalid('TERMINAL_RECEIPT_CHANGED');
    } else if (card.result && ['REGISTERED', 'ADMITTED', 'PREPARED'].includes(card.status)
        && ['REGISTERED', 'ADMITTED', 'PREPARED'].includes(result.status)
        && ['REGISTERED', 'ADMITTED', 'PREPARED'].indexOf(result.status)
          < ['REGISTERED', 'ADMITTED', 'PREPARED'].indexOf(card.status)) {
      return;
    }
    card.commandRef = commandRef;
    card.status = reply.status;
    card.result = result;
    card.error = null;
  }

  function freezeIntent() {
    if (!draft) invalid('NO_EDITOR');
    if (draft.blocked !== null) invalid(draft.blocked);
    const selected = draft.targets.find(item => item.id === draft.selected_target_id);
    if (!selected) invalid('TARGET_NOT_DISCLOSED');
    if (!actionAllowed(draft.source_id, selected)) invalid('ACTION_UNAVAILABLE');
    const key = `${draft.editor_id}:${draft.revision}`;
    const previous = intents.get(key);
    if (previous) return getCard(previous);
    const inputId = allocate('input'), commandId = allocate('save');
    const card = {inputId, commandId, editorId: draft.editor_id, revision: draft.revision,
      sourceId: draft.source_id, documentType: draft.document_type,
      target: deepFreeze(copy(selected)), inputRef: null, commandRef: null,
      inputCurrentState: null, submissionAllowed: null, abandonmentRef: null,
      inputStatus: 'PREPARING', status: 'PREPARING_INPUT', result: null, error: null,
      inputSent: false, submitSent: false, inputFlight: null, submitFlight: null,
      abandonSent: false, abandonFlight: null, abandonCommandId: null,
      inputReadFlight: null, resultReadFlight: null,
      prepareRequest: deepFreeze({source_id: draft.source_id, owner_command: 'prepare_material_input',
        command_id: inputId, params: {protocol: INPUT, document_type: draft.document_type, value: draft.value}})};
    cards.set(inputId, card);
    intents.set(key, inputId);
    return card;
  }

  function prepare() {
    const card = freezeIntent();
    if (card.inputFlight) return card.inputFlight;
    if (card.inputSent) return Promise.resolve(cardView(card));
    card.inputSent = true;
    const request = copy(card.prepareRequest);
    emit();
    card.inputFlight = Promise.resolve().then(() => api.prepareInput(request))
      .then(reply => acceptInput(card, reply))
      .catch(() => {
        if (card.inputRef === null) {
          card.inputStatus = 'INPUT_UNKNOWN'; card.status = 'INPUT_UNKNOWN'; card.error = 'INPUT_RESULT_UNKNOWN';
        }
      })
      .then(() => { card.inputFlight = null; emit(); return cardView(card); });
    return card.inputFlight;
  }

  function submitPrepared(inputId) {
    const card = getCard(inputId);
    if (card.submitFlight) return card.submitFlight;
    if (card.submitSent) return Promise.resolve(cardView(card));
    if (card.abandonSent && card.inputCurrentState !== 'ABANDONED') invalid('INPUT_ABANDONMENT_UNCONFIRMED');
    if (card.inputRef === null) invalid('INPUT_NOT_CONFIRMED');
    if (card.submissionAllowed !== true) invalid(card.inputCurrentState === 'ABANDONED'
      ? 'INPUT_ABANDONED' : 'INPUT_STATE_UNCONFIRMED');
    if (!publishAllowed(card)) invalid('PUBLICATION_UNAVAILABLE');
    const {id, label, ...selected} = card.target;
    const request = {source_id: card.sourceId, owner_command: 'material_submit', command_id: card.commandId,
      params: {protocol: COMMAND, ...copy(selected), input_ref: copy(card.inputRef)}};
    card.submitSent = true;
    card.status = 'SUBMITTING';
    card.error = null;
    emit();
    card.submitFlight = Promise.resolve().then(() => api.submit(request))
      .then(reply => acceptReceipt(card, reply))
      .catch(() => {
        if (card.result === null) { card.status = 'RESULT_UNKNOWN'; card.error = 'COMMAND_RESULT_UNKNOWN'; }
      })
      .then(() => { card.submitFlight = null; emit(); return cardView(card); });
    return card.submitFlight;
  }

  async function save() {
    if (!draft || sourceCapabilities.get(draft.source_id)?.publish !== true) invalid('PUBLICATION_UNAVAILABLE');
    const prepared = await prepare();
    if (prepared.input_ref === null || prepared.submission_allowed !== true) return prepared;
    return submitPrepared(prepared.input_command_id);
  }

  function queryInput(inputId) {
    const card = getCard(inputId);
    if (card.inputReadFlight) return card.inputReadFlight;
    if (!card.inputSent) invalid('INPUT_NOT_REQUESTED');
    card.inputReadFlight = Promise.resolve().then(() => api.readInput({source_id: card.sourceId,
      input_command_id: card.inputId})).then(reply => acceptInput(card, reply))
      .catch(() => { card.error = 'INPUT_QUERY_UNKNOWN'; })
      .then(() => { card.inputReadFlight = null; emit(); return cardView(card); });
    emit();
    return card.inputReadFlight;
  }

  function abandonInput(inputId) {
    const card = getCard(inputId);
    if (card.abandonFlight) return card.abandonFlight;
    if (!canAbandon(card)) return Promise.resolve(cardView(card));
    card.abandonCommandId = 'material-input-abandon:' + card.inputRef.version_id;
    const request = {source_id:card.sourceId,owner_command:'abandon_material_input',command_id:card.abandonCommandId,
      params:{protocol:'rpnh/material_input_abandon/v1',input_command_id:card.inputId,input_ref:copy(card.inputRef)}};
    card.abandonSent = true; card.submissionAllowed = false; card.status = 'INPUT_ABANDONING'; card.error = null;
    emit();
    card.abandonFlight = Promise.resolve().then(() => api.abandonInput(request)).then(reply => {
      if (!object(reply) || reply.protocol !== 'rpnh/material_input_abandon_receipt/v1' || reply.status !== 'ABANDONED'
          || reply.input_command_id !== card.inputId || !equal(ref(reply.input_ref,'resource_version/v1'),card.inputRef)
          || !nonempty(reply.abandonment_transaction_id) || card.result !== null) invalid('INVALID_INPUT_ABANDONMENT');
      const abandonmentRef = ref(reply.abandonment_ref,'resource_version/v1');
      if (card.abandonmentRef !== null && !equal(card.abandonmentRef,abandonmentRef)) invalid('INPUT_ABANDONMENT_CHANGED');
      card.inputCurrentState = 'ABANDONED'; card.submissionAllowed = false; card.abandonmentRef = abandonmentRef;
      card.status = 'INPUT_ABANDONED'; card.error = null;
    }).catch(error => {
      if (card.inputCurrentState !== 'ABANDONED') {
        card.status = 'INPUT_ABANDONMENT_UNKNOWN';
        card.error = nonempty(error?.message) ? error.message : 'INPUT_ABANDONMENT_UNKNOWN';
      }
    }).then(() => { card.abandonFlight = null; emit(); return cardView(card); });
    return card.abandonFlight;
  }

  function queryReceipt(inputId) {
    const card = getCard(inputId);
    if (card.resultReadFlight) return card.resultReadFlight;
    if (!card.submitSent) invalid('COMMAND_NOT_SUBMITTED');
    const query = {source_id: card.sourceId, ...(card.commandRef === null
      ? {material_command_id: card.commandId} : {material_command_ref: copy(card.commandRef)})};
    card.resultReadFlight = Promise.resolve().then(() => api.readReceipt(query))
      .then(reply => acceptReceipt(card, reply))
      .catch(() => { card.error = 'COMMAND_QUERY_UNKNOWN'; })
      .then(() => { card.resultReadFlight = null; emit(); return cardView(card); });
    return card.resultReadFlight;
  }

  return {open, edit, selectTarget, close, updateCapabilities, prepare, save, submitPrepared,
    queryInput, queryReceipt, abandonInput, getState: state};
}
