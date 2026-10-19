/** Build the bounded Web composition from current official DSH widget sources. */
import { build } from 'vite'
import tsconfigPaths from 'vite-tsconfig-paths'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { mkdir, writeFile, copyFile } from 'node:fs/promises'

const here = dirname(fileURLToPath(import.meta.url))
const source = resolve(here, '..')
const webRequire = createRequire(resolve(source, 'apps/web/package.json'))
const composerRequire = createRequire(resolve(source, 'packages/client/ui-conversation/package.json'))
const outDir = resolve(here, 'dist')
await build({ configFile: false, root: source, cacheDir: resolve(here, '.vite'),
  plugins: [tsconfigPaths({ projects: [resolve(source, 'tsconfig.base.json')], loose: true })],
  resolve: { alias: [
    { find: /^react-dom\/client$/, replacement: webRequire.resolve('react-dom/client') },
    { find: /^react-dom$/, replacement: webRequire.resolve('react-dom') },
    { find: /^react\/jsx-runtime$/, replacement: webRequire.resolve('react/jsx-runtime') },
    { find: /^react$/, replacement: webRequire.resolve('react') },
    { find: /^lexical$/, replacement: composerRequire.resolve('lexical') },
  ] },
  esbuild: { jsx: 'automatic' },
  define: { 'process.env.NODE_ENV': '"production"' },
  build: { outDir, emptyOutDir: false, sourcemap: false, target: 'es2022',
    lib: { entry: resolve(here, 'browser.ts'), formats: ['es'], fileName: () => 'browser.js', cssFileName: 'browser' } },
})
await mkdir(outDir, { recursive: true })
await copyFile(resolve(source, 'LICENSE'), resolve(outDir, 'DSH-LICENSE.txt'))
await copyFile(resolve(source, 'packages/client/ui-theme/src/styles/Montserrat-OFL.txt'), resolve(outDir, 'Montserrat-OFL.txt'))
await writeFile(resolve(outDir, 'index.html'), `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>DSH · RPNH MainSession</title><link rel="stylesheet" href="/browser.css"><link rel="stylesheet" href="/layout.css"></head><body><div id="root"></div><script type="module" src="/browser.js"></script></body></html>`)
await writeFile(resolve(outDir, 'layout.css'), `body{margin:0;background:var(--dsw-alias-bg-base);color:var(--dsw-alias-label-primary);font:15px var(--dsw-font-family)}main{max-width:940px;margin:auto;padding:24px}header{display:flex;gap:20px;align-items:center;flex-wrap:wrap;border-bottom:1px solid #ddd;padding-bottom:20px}header small{margin-left:auto}.conversation{min-height:45vh}article{padding:24px 0}.answer{padding:18px 4px}.state,small{color:var(--dsw-alias-label-secondary)}footer{position:sticky;bottom:0;background:var(--dsw-alias-bg-base);padding:16px 0}.controls{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:14px}select{max-width:380px;padding:6px;border:1px solid #ccc;border-radius:8px;background:inherit;color:inherit}.composer{display:flex;gap:12px;align-items:flex-end;padding:14px;border:1px solid #bbb;border-radius:16px}[data-composer-input]{flex:1;min-height:60px;max-height:240px;overflow:auto;white-space:pre-wrap;outline:none}[data-composer-input] p{margin:0}.notice,pre[role=alert]{white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto;padding:12px;background:var(--dsw-alias-bg-layer-1)}footer>small{display:block;margin-top:8px}@media(max-width:600px){main{padding:14px}select{max-width:250px}}`)
console.log(`RPNH DSH Web assets ready: ${outDir}/index.html`)
