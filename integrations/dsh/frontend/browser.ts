/** Current upstream DSH widgets; RPNH owns routing and the single session. */
import { createElement as h, useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { $getRoot } from 'lexical'
import { Button, BrandWordmark, MarkdownText, UserTextRun } from '@deepseek-ai/dsh-client-ui-primitives'
import { UserContent } from '../packages/client/ui-chat/src/client/chat/UserContent.tsx'
import { ComposerContentEditable } from '../packages/client/ui-conversation/src/client/input/editor/ComposerContentEditable.tsx'
import { DraftEditorRuntime } from '../packages/client/ui-conversation/src/client/input/editor/runtime.ts'
import '../packages/client/ui-theme/src/styles/base.css'
import '../packages/client/ui-theme/src/styles/design-platform.css'
import '../packages/client/ui-theme/src/styles/brand-font.css'
import '../packages/client/ui-theme/src/styles/focus.css'
import type { Initialization, SessionRow } from './rpc.ts'

interface View { session: SessionRow; configuration: Initialization['configuration']; agent: { status: string; error: string | null } }
const token = document.querySelector<HTMLMetaElement>('meta[name="rpnh-token"]')!.content
async function api<T>(name: string, params?: unknown): Promise<T> {
  const response = await fetch(`/api/${name}`, { method: params === undefined ? 'GET' : 'POST',
    headers: { 'x-rpnh-token': token, 'Content-Type': 'application/json' },
    ...(params === undefined ? {} : { body: JSON.stringify(params) }) })
  const value = await response.json()
  if (!response.ok) throw new Error(value.error?.message ?? 'RPNH request failed')
  return value as T
}
const labels = { code: { copyLabel: 'Copy', copiedLabel: 'Copied' }, footnotes: 'Footnotes' }
const activeStates = new Set(['accepted', 'pending_start', 'running', 'terminal'])

function App() {
  const [view, setView] = useState<View>()
  const [connected, setConnected] = useState(true)
  const [text, setText] = useState('')
  const [selection, setSelection] = useState('')
  const [effort, setEffort] = useState('')
  const [notice, setNotice] = useState('')
  const [pending, setPending] = useState(false)
  const initializedSelection = useRef(false)
  const request = useRef<{ text: string; selection: string; effort: string; id: string }>()
  const draft = useMemo(() => {
    const runtime = new DraftEditorRuntime({
      onUpdate: () => runtime.editor.getEditorState().read(() => setText($getRoot().getTextContent())),
      openReference: () => false, lexicon: () => new Map(), resolveLexicon: () => undefined,
    })
    return runtime
  }, [])
  const editor = draft.editor
  const refresh = async () => {
    const next = await api<View>('snapshot')
    setView(next)
    return next
  }
  useEffect(() => draft.register(), [draft])
  useEffect(() => {
    let gone = false; let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const next = await refresh()
        if (gone) return
        if (!initializedSelection.current) {
          initializedSelection.current = true
          setSelection(next.session.model.selection)
          setEffort(next.session.model.reasoning_effort ?? '')
        }
        timer = setTimeout(poll, 500)
      } catch (error) {
        if (!gone) { setConnected(false); setNotice(`Disconnected: ${String(error)}. Reopen through rpnh to reconnect.`) }
      }
    }
    void poll()
    return () => { gone = true; clearTimeout(timer) }
  }, [])
  const profile = view?.configuration.profiles.find(p => p.selection === selection)
  const busy = view?.session.turns.some(turn => activeStates.has(turn.state)) ?? false
  const paused = view?.session.turns.some(turn => turn.state === 'stopped_by_owner') ?? false
  const send = async () => {
    if (!text.trim() || pending || !connected) return
    const command = text.match(/^\/(rpnh-[\w-]+)(?:\s+([\s\S]*))?$/)
    if (!command && (busy || paused)) { setNotice('Stop the active turn, or use /rpnh-resume or /rpnh-rollback for a paused checkpoint.'); return }
    setPending(true); setNotice('')
    if (!request.current || request.current.text !== text || request.current.selection !== selection || request.current.effort !== effort) {
      request.current = { text, selection, effort, id: crypto.randomUUID() }
    }
    try {
      const params = { requestId: request.current.id, ...(selection ? { selectionId: selection } : {}),
        ...(effort ? { reasoningEffort: effort } : {}) }
      const result = command
        ? await api('command', { ...params, name: command[1], arguments: command[2] ?? '' })
        : await api('submit', { ...params, text })
      if (command) setNotice(typeof result === 'string' ? result : JSON.stringify(result, null, 2))
      request.current = undefined
      editor.update(() => $getRoot().clear())
      await refresh()
    } catch (error) { setNotice(String(error)) }
    finally { setPending(false) }
  }
  const abort = async () => {
    try { await api('abort', {}); await refresh() }
    catch (error) { setNotice(String(error)) }
  }
  return h('main', null,
    h('header', null, h(BrandWordmark, { size: 26 }), h('strong', null, 'RPNH MainSession'),
      h('small', null, view?.session.id ?? 'Connecting…')),
    h('section', { className: 'conversation', 'aria-label': 'Conversation' },
      ...(view?.session.turns ?? []).map(turn => h('article', { key: `${turn.ordinal}:${turn.key}`, 'data-turn': turn.ordinal },
        h(UserContent, { content: { text: turn.text, attachments: [], extraBlocks: [] }, facts: {},
          loadImage: async () => { throw new Error('Native files are unavailable') },
          renderSlot: () => null,
          renderFactorySlot: (_slot: string, props: { text: string }) => h(UserTextRun, { text: props.text }),
          t: (key: string) => key } as any),
        turn.answer && turn.state === 'committed'
          ? h('div', { className: 'answer' }, h(MarkdownText, { text: turn.answer.reply, labels }),
            turn.answer.protocol_valid === false && h('small', null, 'RPNH returned prose; no valid child-task decision was accepted.'))
          : h('p', { className: 'state', role: 'status' }, turn.state === 'stopped_by_owner'
            ? 'Paused at the RPNH checkpoint. Use /rpnh-resume or /rpnh-rollback.' : turn.state),
        ...turn.children.map((child, index) => h('small', { key: index }, `Child ${child.task_id ?? ''} (${child.kind ?? ''}) registered; this is not terminal success.`)),
      )),
      view?.session.error != null && h('pre', { role: 'alert' }, String(view.session.error))),
    h('footer', null,
      h('div', { className: 'controls' },
        h('label', null, 'Model ', h('select', { value: selection, disabled: pending || busy,
          onChange: (event: any) => { setSelection(event.target.value); setEffort(view?.configuration.profiles.find(p => p.selection === event.target.value)?.default_reasoning_effort ?? '') } },
          ...(view?.configuration.profiles ?? []).map(p => h('option', { key: p.selection, value: p.selection, disabled: !p.ready }, `${p.provider_name} / ${p.model}${p.ready ? '' : ' (unavailable)'}`)))),
        (profile?.supported_reasoning_efforts.length ?? 0) > 0 && h('label', null, 'Reasoning ', h('select', { value: effort,
          disabled: pending || busy, onChange: (event: any) => setEffort(event.target.value) },
          ...profile!.supported_reasoning_efforts.map(value => h('option', { key: value, value }, value)))),
        h(Button, { onClick: abort, disabled: !connected || !busy }, 'Stop')),
      h('div', { className: 'composer' }, h(ComposerContentEditable, { editor, editable: connected && !pending,
        'aria-label': 'Message', onKeyDown: (event: any) => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() }
        } }), h(Button, { onClick: send, disabled: !connected || pending || !text.trim() }, 'Send')),
      h('small', null, 'Enter to send · Shift+Enter for a new line · /rpnh-help for commands'),
      notice && h('pre', { role: 'status', className: 'notice' }, notice),
      view?.agent.error && h('pre', { role: 'alert' }, view.agent.error)),
  )
}
createRoot(document.getElementById('root')!).render(h(App))
