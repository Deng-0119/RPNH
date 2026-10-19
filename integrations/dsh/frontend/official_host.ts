/** Original DSH Web shell and native transports, with a bound RPNH Remote BFF. */
import { boot } from '../packages/boot/app-boot/src/index.ts'
import WebServer from '../packages/host/webserver/src/index.ts'
import Credentials from '../packages/credentials/credentials-local/src/index.ts'
import * as Connection from '../packages/client/connection/src/index.ts'
import * as WebApp from '../packages/bundle/web-app/src/index.ts'
import Typert from '../packages/typert/registry/src/index.ts'
import Gateway from '../packages/api/gateway/src/index.ts'
import RemoteAccess from '../packages/api/remote-access/src/index.ts'
import * as Remotes from '../packages/api/remotes/src/index.ts'
import ClientModules from '../packages/client/modules/src/index.ts'
import { resolve } from 'node:path'
import { FRONTEND_DIR, checkOfficialAssets } from './setup.ts'
import { installOfficialBff, type OfficialView } from './official_bff.ts'
import type { RpcPort } from './rpc.ts'

export async function startDshFrontend(rpc: RpcPort, options: { port?: number; cwd?: string } = {}) {
  await checkOfficialAssets()
  WebApp.internals.resolveDistIndex = () => resolve(FRONTEND_DIR, 'node_modules/@deepseek-ai/dsh-web-frontend/dist/index.html')
  let view: OfficialView | undefined
  const cwd = options.cwd ?? process.env.RPNH_DSH_CALLER_CWD ?? process.cwd()
  let ctx
  try {
    ctx = await boot('rpnh-dsh', resolve(FRONTEND_DIR, 'official.json'), undefined, async ctx => {
      await ctx.plugin(Typert); await ctx.plugin(RemoteAccess); await ctx.plugin(Gateway, {})
      await ctx.plugin(Credentials, { dshHome: resolve(process.env.XDG_STATE_HOME ?? FRONTEND_DIR, 'dsh-web-auth') })
      await ctx.plugin(WebServer, { host: '127.0.0.1', port: options.port ?? 0 })
      await ctx.plugin(Connection); await ctx.plugin(Remotes); await ctx.plugin(ClientModules)
      // Native browser auth validates the launch token before minting its
      // cookie. Fence the bound listener first, including that index exchange.
      const authorizeIndex = ctx.connection.authorizeIndex.bind(ctx.connection)
      ctx.connection.authorizeIndex = (request, response) => {
        const host = request.headers instanceof Headers ? request.headers.get('host') : request.headers.host
        if (host !== new URL(ctx.webServer.getOrigin()).host) {
          response.writeHead(403); response.end('forbidden'); return false
        }
        return authorizeIndex(request, response)
      }
      view = await installOfficialBff(ctx, rpc, cwd)
      ctx.on('webserver/index-inject', table => { table.push({ kind: 'global', name: '__RPNH_BOUND_SESSION__', value: view!.id }) })
      await ctx.plugin(WebApp, { openBrowser: false, printUrl: false, surfaceContext: false })
    })
  } catch (error) { view?.close(); rpc.close(); throw error }
  view!.start()
  const context = ctx
  let closing: Promise<void> | undefined
  const close = () => closing ??= (async () => { view!.close(); rpc.close(); await context.fiber.dispose() })()
  void rpc.closed.then(close).catch(error => console.error(String(error)))
  return { ctx: context, view: view!, url: context.connection.authenticatedUrl(context.webServer.getOrigin() + '/'), close }
}
