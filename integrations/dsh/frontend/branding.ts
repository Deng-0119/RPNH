/** Presentation-only RPNH identity around the original DSH Web application. */
import { COMPATIBILITY } from './setup.ts'

const escape = (value: string): string => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;')

/** Index contributions use no session state, provider route or execution authority. */
export function brandingRows() {
  const upstream = escape(COMPATIBILITY.version)
  const adaptation = escape(COMPATIBILITY.adaptation_version)
  const channel = COMPATIBILITY.version.includes('-rc.') ? 'release candidate' : 'alpha preview'
  return [
    { kind: 'style' as const, text: `
      #root { height: calc(100% - 40px) !important; margin-top: 40px !important; }
      #rpnh-brand { position: fixed; inset: 0 0 auto; height: 40px; box-sizing: border-box;
        display: flex; align-items: center; gap: 12px; padding: 0 16px; z-index: 1000;
        background: #17365a; color: #fff; font: 13px/1.4 system-ui, sans-serif; border-bottom: 1px solid #365b85; }
      #rpnh-brand strong { font-size: 14px; white-space: nowrap; }
      #rpnh-brand details { margin-left: auto; }
      #rpnh-brand summary { cursor: pointer; border-radius: 4px; padding: 4px 8px; }
      #rpnh-brand summary:focus-visible { outline: 2px solid #fff; outline-offset: 1px; }
      #rpnh-brand .rpnh-about { position: absolute; top: 44px; right: 8px; width: min(360px, calc(100vw - 32px));
        box-sizing: border-box; padding: 16px; border: 1px solid #365b85; border-radius: 8px;
        background: #17365a; color: #fff; box-shadow: 0 8px 28px #0004; }
      #rpnh-brand p { margin: 6px 0; } #rpnh-brand a { color: #beddff; }
      @media (max-width: 540px) { #rpnh-brand { padding: 0 8px; gap: 4px; } #rpnh-brand .rpnh-english { display: none; } }
    ` },
    { kind: 'html' as const, placement: 'body' as const, html: `<header id="rpnh-brand" aria-label="RPNH adaptation identity">
      <strong>RPNH 适配版</strong><span class="rpnh-english">RPNH for DSH</span>
      <details><summary>版本 / About</summary><section class="rpnh-about" aria-label="RPNH and upstream versions">
        <p><strong>RPNH for DSH · ${adaptation}</strong></p><p>Upstream DSH ${upstream} · ${channel}</p>
        <p>RPNH 适配界面，非 DeepSeek 官方发行版或官方支持。</p>
        <p>RPNH adaptation; not an official DeepSeek distribution or endorsement.</p>
        <p><a href="https://github.com/deepseek-ai/deepseek-harness" target="_blank" rel="noopener noreferrer">DeepSeek Harness</a> · MIT · © DeepSeek AI</p>
      </section></details></header>` },
  ]
}
