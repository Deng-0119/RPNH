import { installUpdateNotice } from './updates.js'
import Schema from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { CONTROL_OPERATIONS, READ_OPERATIONS, invokeBridge } from './transport.js'

export const name = 'rpnh-operator'
export const inject = ['tools']
export const Config = Schema.object({
  controlRoot: Schema.string().required(),
  python: Schema.string().required(),
  enableControl: Schema.boolean().default(false),
})

const descriptions = {
  list: 'List existing RPNH tasks in the operator-configured control root. Does not create or recover tasks.',
  status: 'Read original RPNH task state and exact Registry references. Process exit alone is not business completion.',
  result: 'Read the original registered terminal result and exact result/evidence references. Fails if not terminal.',
  net: 'Read the real task Petri-net projection. This does not execute or fabricate a diagram.',
  checkpoints: 'List original committed checkpoints of an existing RPNH task.',
  stop: 'Request an explicit owner stop of an existing RPNH task. STOP_REQUESTED is not proof that drain completed.',
  resume: 'Resume an existing owner-stopped RPNH task using its original Registry. May resume authorized model calls.',
  reopen: 'Reopen an exact committed checkpoint of an existing RPNH task. Retains history; does not undo external effects.',
}

export function apply(ctx, config) {
  installUpdateNotice(ctx, config)
  const operations = [...READ_OPERATIONS, ...(config.enableControl ? CONTROL_OPERATIONS : [])]
  // Reuse DSH's own approval pipeline; no private approval/permission database.
  ctx.on('tools/pre-execute', async (exec, next) => {
    const decision = await next()
    if (decision.kind !== 'allow') return decision
    if (!CONTROL_OPERATIONS.some(operation => exec.name === `rpnh_task_${operation}`)) return decision
    return { kind: 'ask', reason: 'This changes an existing RPNH task; resume/reopen may continue model or external tool work.' }
  })
  for (const operation of operations) {
    const parameters = operation === 'list' ? {} : {
      task_id: { type: 'string', required: true, description: 'Exact existing task_id from rpnh_task_list' },
    }
    if (operation === 'reopen') {
      parameters.checkpoint_version_id = { type: 'string', required: true, description: 'Exact version_id from rpnh_task_checkpoints' }
      parameters.reason = { type: 'string', required: true, description: 'Explicit reason for selecting this checkpoint' }
    }
    ctx.tools.register(defineTool({
      name: `rpnh_task_${operation}`,
      description: `${descriptions[operation]} Transport cancellation is not rollback; inspect unknown outcomes, never automatically retry writes.`,
      parameters,
      output: {
        schema: { type: 'json' },
        render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
        presentationMeta: (args, value) => ({ protocol: value.protocol, operation: value.operation,
          ...(args.task_id ? { task_id: args.task_id } : {}) }),
      },
      finalizeContent: (_exec, result) => {
        if (!CONTROL_OPERATIONS.includes(operation)) return undefined
        const code = result.error?.info?.code
        if (code === 'ABORTED_BEFORE_DISPATCH') {
          return [{ type: 'text', text: 'RPNH control call cancelled before DSH dispatch; NOT_DISPATCHED.' }]
        }
        if (code === 'ABORTED') {
          return [{ type: 'text', text: 'RPNH control wait cancelled; OUTCOME_UNKNOWN. Inspect the original task state before any further action. Do not automatically retry. Cancellation is not task rollback or task stop.' }]
        }
        return undefined
      },
      execute: (args, exec) => invokeBridge(config, operation, args, exec.signal),
    }))
  }
}

