import { Session, SessionId } from '@deepseek-ai/dsh-session'
import type { SessionEvent } from '@deepseek-ai/dsh-session'
import { MessageId } from '@deepseek-ai/dsh-llm'
import type { SessionRow, Turn } from './rpc.ts'

export const ACTIVE_STATES = new Set(['accepted', 'pending_start', 'running', 'terminal'])
export const SETTLED_STATES = new Set(['committed', 'stopped_by_owner', 'failed', 'interrupted'])
export function isActive(row: SessionRow): boolean { return row.turns.some(turn => ACTIVE_STATES.has(turn.state)) }
export function hasKnownModel(turn: Turn): boolean {
  return turn.model_evidence === 'frontend-request/v2' || turn.model_evidence === 'frontend-request/v3'
}

/** Display-only DSH events projected from any MainSession, including Codex/OpenCode history. */
export function projectSession(row: SessionRow): SessionEvent[] {
  const session = Session.create(SessionId(row.id))
  for (const turn of row.turns) appendTurn(session, row.id, turn)
  return [...session.snapshotEvents()]
}
export function appendTurn(session: Session, id: string, turn: Turn): void {
  session.append('turn/start', { turn: turn.ordinal })
  session.append('user/message', {
    id: MessageId(`${id}:${turn.ordinal}:${turn.key}:user`), role: 'user',
    content: [{ type: 'text', text: turn.text }], source: { kind: 'user' },
  }, { surfaceOp: 'append' })
  appendOutcome(session, id, turn)
}
export function appendOutcome(session: Session, id: string, turn: Turn): void {
  if (turn.state === 'committed' && turn.answer) {
    session.append('assistant/message', {
      turn: turn.ordinal, step: 1, stream: [],
      message: { id: MessageId(`${id}:${turn.ordinal}:${turn.key}:assistant`), role: 'assistant',
        content: [{ type: 'text', text: turn.answer.reply }],
        source: { kind: 'model', provider: hasKnownModel(turn) ? turn.model.provider : 'unknown',
          model: hasKnownModel(turn) ? turn.model.model : 'unknown' } },
    }, { surfaceOp: 'append' })
    session.append('turn/end', { turn: turn.ordinal, reason: { kind: 'completed' } })
  } else if (turn.state === 'failed') {
    session.append('turn/end', { turn: turn.ordinal, reason: { kind: 'error',
      error: { code: 'UNKNOWN', message: 'RPNH turn failed without a committed answer.' } } })
  } else if (turn.state === 'interrupted') {
    // RPNH commits this state when the owner rolls back a paused main turn.
    // Native DSH "interrupted" specifically means a crash-orphaned turn.
    session.append('turn/end', { turn: turn.ordinal, reason: { kind: 'aborted', reason: { kind: 'user' } } })
  }
  // A stopped checkpoint is deliberately left open: viewing it never closes or resumes it.
}
