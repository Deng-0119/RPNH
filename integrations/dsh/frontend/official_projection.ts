/** Official DSH wire views derived only from the bound RPNH MainSession. */
import { Session, SessionId } from '@deepseek-ai/dsh-session'
import type { ModelCatalog, ModelSelection, SessionFollowFrame, SessionHistoryRecord,
  SessionProjectionValues, SessionSummary, SessionWireHeader } from '../packages/api/session-controller/src/types.ts'
import { hasKnownModel, isActive, projectSession } from './projection.ts'
import type { Initialization, Profile, SessionRow } from './rpc.ts'

export const RPNH_PROVIDER = 'rpnh'

export function officialSelection(profile: Profile) {
  return { provider: RPNH_PROVIDER, model: profile.selection,
    ...(profile.reasoning_effort === null ? {} : { reasoningEffort: profile.reasoning_effort }) }
}

export function officialModelCatalog(initial: Initialization): ModelCatalog {
  const models = initial.configuration.profiles.filter(profile => profile.ready).map(profile => ({
    id: profile.selection, name: `${profile.provider_name} / ${profile.model}`,
    ...(profile.supported_reasoning_efforts.length === 0 ? {} : { reasoning: {
      efforts: profile.supported_reasoning_efforts.map(id => ({ id, name: id })),
      ...(profile.default_reasoning_effort === null ? {} : { defaultEffort: profile.default_reasoning_effort }),
    } }),
  }))
  return { default: officialSelection(initial.session.model),
    routableProviders: models.length === 0 ? [] : [RPNH_PROVIDER],
    groups: models.length === 0 ? [] : [{ id: RPNH_PROVIDER, name: 'RPNH', models }],
    failures: initial.configuration.profiles.filter(profile => !profile.ready).map(profile => ({
      id: profile.selection, name: `${profile.provider_name} / ${profile.model}`,
      message: 'This RPNH profile is unavailable; configure its required environment through RPNH.',
    })),
  }
}

export function officialRecords(row: SessionRow): SessionHistoryRecord[] {
  const turns = new Map(row.turns.map(turn => [turn.ordinal, turn]))
  let current: SessionRow['turns'][number] | undefined
  return projectSession(row).map(event => {
    if (event.type === 'turn/start') current = turns.get(event.data.turn)
    const time = current === undefined ? row.created
      : event.type === 'assistant/message' || event.type === 'turn/end' ? current.updated : current.created
    return { type: 'event', event: { ...event, time } } as SessionHistoryRecord
  })
}

export function officialProjections(row: SessionRow, pending?: ModelSelection): SessionProjectionValues {
  const completed = row.turns.findLast(turn => turn.state === 'committed')
  const first = row.turns[0]?.text.trim().split('\n')[0]
  return {
    title: first ? Array.from(first).slice(0, 70).join('') : 'RPNH MainSession',
    sessionListMetadata: { blank: row.turns.length === 0, lastPromptAt: row.turns.at(-1)?.created ?? null },
    modelSelection: { lastUsed: completed && hasKnownModel(completed) ? officialSelection(completed.model) : null,
      next: { ...(pending ?? officialSelection(row.model)) } },
  }
}

export function officialHeader(row: SessionRow, cwd?: string): SessionWireHeader {
  return { version: Session.create(SessionId(row.id)).header.version, id: SessionId(row.id),
    createdAt: row.created, isSeeded: false, ...(cwd === undefined ? {} : { cwd }) }
}

export function officialSummary(row: SessionRow, cwd?: string, pending?: ModelSelection): SessionSummary {
  const records = officialRecords(row)
  return { sessionId: SessionId(row.id), updatedAt: row.updated, running: isActive(row),
    agentAvailable: true, formatStatus: 'current', blank: row.turns.length === 0,
    ...(cwd === undefined ? {} : { cwd }),
    projections: { kind: 'sequenced', asOfSeq: records.at(-1)?.event.seq ?? -1,
      values: officialProjections(row, pending) } }
}

export function officialOpening(row: SessionRow, cwd?: string, pending?: ModelSelection): SessionFollowFrame {
  const records = officialRecords(row)
  const cursor = records.at(-1)?.event.seq ?? -1
  return { type: 'snapshot', header: officialHeader(row, cwd), cursor, records, hasMore: false,
    projections: { asOfSeq: cursor, values: officialProjections(row, pending) },
    assistantStream: { revision: 0 } }
}
