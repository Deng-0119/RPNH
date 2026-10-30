/** Prebuilt native DSH browser plugin; display state only, no task or permission state. */
window.__ModuleLoader__.load({
  id: 'dsh-rpnh-operator',
  factory(require) {
    const { createElement: h, useState, useEffect, useRef } = require('react')
    const upstreamVersion = '0.2.2-alpha.2'
    const adaptationVersion = '0.1.0-rc.3'
    const updatePath = '/api/rpnh-operator/update'
    const label = 'RPNH 适配版'
    function BrandName() {
      return h('span', { 'data-rpnh-brand': 'sidebar', style: { display: 'flex', flexDirection: 'column', lineHeight: 1.3 } },
        h('strong', { style: { fontSize: 15 } }, label),
        h('span', { style: { fontSize: 10, opacity: 0.7 } }, 'for DeepSeek Harness'))
    }
    function VersionBadge({ wide }) {
      const [open, setOpen] = useState(false)
      const [offer, setOffer] = useState(null)
      const [message, setMessage] = useState('')
      const [pending, setPending] = useState(false)
      const dialog = useRef(null)
      useEffect(() => {
        const abort = new AbortController()
        fetch(updatePath, { signal: abort.signal, credentials: 'same-origin' }).then(response => {
          if (!response.ok) throw new Error('Update notice unavailable')
          return response.json()
        }).then(value => {
          if (abort.signal.aborted || value.schema_version !== 'rpnh/update_status/v1' || value.entry !== 'dsh-operator') return
          if (value.offer?.mode === 'manual') { setOffer(value.offer); setOpen(true) }
        }).catch(() => {})
        return () => abort.abort()
      }, [])
      useEffect(() => {
        if (open && !dialog.current.open) dialog.current.showModal()
        if (!open && dialog.current.open) dialog.current.close()
      }, [open])
      async function choose(choice) {
        if (!offer || pending) return
        setPending(true)
        try {
          const response = await fetch(updatePath + '/choose', { method: 'POST', credentials: 'same-origin',
            headers: { 'content-type': 'application/json' }, body: JSON.stringify({ release: offer.tag, choice }) })
          if (!response.ok) throw new Error('The offer changed or is unavailable. Reopen DSH to check again. 未安装任何更新。')
          const result = await response.json()
          setOffer(null)
          setMessage(typeof result.instructions === 'string' ? result.instructions
            : choice === 'update' ? 'Use RPNH’s update instructions after closing DSH. This externally managed host was not modified. 请退出DSH后按RPNH说明更新；此处没有改动安装。'
              : choice === 'later' ? 'Deferred / 暂不更新' : 'Skipped this supported version / 当前版本不再提示')
        } catch (error) { setMessage(String(error)) }
        finally { setPending(false) }
      }
      return h('div', { 'data-rpnh-brand': 'persistent', style: { width: '100%', minWidth: 0,
        padding: '4px 0', color: 'var(--dsw-alias-label-primary, #17243d)', fontFamily: 'inherit' } },
        h('button', { type: 'button', 'aria-expanded': open, 'aria-haspopup': 'dialog',
          'aria-label': 'RPNH 适配版 — version information', onClick: () => setOpen(value => !value),
          style: { cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexDirection: wide ? 'row' : 'column', gap: wide ? 5 : 0, width: '100%', minWidth: 40,
            minHeight: 36, padding: wide ? '4px 8px' : '2px 0', border: '1px solid #9eafca', borderRadius: 8,
            background: 'var(--dsw-alias-bg-base, #ffffff)', color: 'inherit', fontSize: 11, lineHeight: 1.3, fontWeight: 650 } },
          h('span', null, 'RPNH'), h('span', { style: { fontSize: wide ? 11 : 9 } }, '适配版'),
          wide && h('span', { 'aria-hidden': true, style: { fontWeight: 400 } }, 'ⓘ')),
        h('dialog', { ref: dialog, 'aria-label': 'RPNH version / 版本', onClose: () => setOpen(false),
          onCancel: () => setOpen(false), style: { width: 'min(360px, calc(100vw - 32px))', maxHeight: 'calc(100vh - 48px)',
            boxSizing: 'border-box', padding: 20, border: '1px solid #b8c5d8', borderRadius: 12,
            background: 'var(--dsw-alias-bg-base, #ffffff)', color: 'inherit', boxShadow: '0 8px 30px #0003',
            fontSize: 13, lineHeight: 1.6 } },
          h('strong', null, 'RPNH for DSH · RPNH 适配版'),
          h('p', null, `Upstream DSH ${upstreamVersion} (prerelease)`),
          h('p', null, `RPNH operator ${adaptationVersion}`),
          h('p', null, 'DeepSeek Harness © DeepSeek AI · MIT. Independent RPNH adaptation, not an official DeepSeek distribution or endorsement. 非 DeepSeek 官方发行或支持。'),
          offer && h('section', { 'aria-label': 'Supported update / 已支持更新' },
            h('p', null, `RPNH ${offer.rpnh_version} supports DSH ${offer.upstream_version} (${offer.channel}).`),
            h('p', null, 'Choose whether to update this externally managed installation. No running task is migrated or restarted.'),
            ...[['update', 'Update / 更新'], ['later', 'Not now / 暂不更新'], ['skip', 'Skip this version / 此版不再提示']].map(([choice, text]) =>
              h('button', { key: choice, type: 'button', disabled: pending, onClick: () => { void choose(choice) },
                style: { display: 'block', margin: '8px 0', padding: 6, width: '100%', cursor: 'pointer' } }, text))),
          message && h('p', { role: 'status' }, message),
          h('button', { type: 'button', onClick: () => setOpen(false), style: { cursor: 'pointer', padding: '4px 12px' } }, 'Close / 关闭')))
    }
    return {
      inject: ['slots'],
      apply(ctx) {
        // Native priorities shadow only the name; retain the upstream mark and
        // restore the original name on disposal. The footer occupies layout
        // space instead of overlaying conversation, Send or approval controls.
        ctx.slots.inject('sidebar.brand.name', () => ctx.slots.register({ name: 'sidebar.brand.name', priority: -10 }, BrandName))
        ctx.slots.inject('sidebar.footer.action', () => ctx.slots.register({ name: 'sidebar.footer.action', id: 'rpnh-adaptation-version' }, VersionBadge))
      },
    }
  },
})

