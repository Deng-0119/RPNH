/** Real Chromium/CDP runner using Node 22 built-ins; never disables sandboxing. */
import fs from 'node:fs';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';
const [binary, legacyOrigin, normalOrigin, evidence] = process.argv.slice(2);
for (const origin of [legacyOrigin, normalOrigin]) {
    if (new URL(origin).hostname !== '127.0.0.1') throw new Error('Only fixture loopback origins');
}
const profile = fs.mkdtempSync(path.join(evidence, 'browser-profile-'));
const argv = ['--headless', '--no-first-run', '--disable-background-networking', '--disable-component-update',
    '--disable-sync', '--disable-breakpad', '--disable-crash-reporter', '--no-proxy-server',
    '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost', '--remote-debugging-address=127.0.0.1',
    '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'];
fs.writeFileSync(path.join(evidence, 'chromium-command.json'), JSON.stringify({binary, argv}, null, 2));
const out = fs.openSync(path.join(evidence, 'chromium.stdout'), 'w');
const err = fs.openSync(path.join(evidence, 'chromium.stderr'), 'w');
const browser = spawn(binary, argv, {stdio:['ignore', out, err]});
let browserExit, spawnError, ws;
const closed = new Promise(resolve => browser.once('close', (code, signal) => {browserExit={code, signal}; resolve();}));
browser.once('error', e => {spawnError=e;});
const pending = new Map(); let serial = 0;
async function call(method, params={}, sessionId) {
    const id = ++serial;
    return await new Promise((resolve, reject) => {
        const timer = setTimeout(() => {pending.delete(id); reject(new Error(`CDP timeout: ${method}`));}, 15000);
        pending.set(id, {resolve, reject, timer});
        ws.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
    });
}
try {
    const portFile = path.join(profile, 'DevToolsActivePort');
    for (let i=0; !fs.existsSync(portFile); i++) {
        if (spawnError || browserExit || i>=100) throw spawnError || new Error(`Browser launch failed: ${JSON.stringify(browserExit)}`);
        await delay(100);
    }
    const [port, endpoint] = fs.readFileSync(portFile,'utf8').trim().split('\n');
    ws = new WebSocket(`ws://127.0.0.1:${port}${endpoint}`);
    await new Promise((resolve,reject) => {ws.onopen=resolve; ws.onerror=reject;});
    ws.onmessage = event => {
        const message=JSON.parse(event.data), item=pending.get(message.id);
        if (!item) return;
        clearTimeout(item.timer); pending.delete(message.id);
        message.error ? item.reject(new Error(JSON.stringify(message.error))) : item.resolve(message.result);
    };
    const identity = await call('Browser.getVersion');
    const {targetId} = await call('Target.createTarget', {url:'about:blank'});
    const {sessionId} = await call('Target.attachToTarget', {targetId, flatten:true});
    await call('Page.enable', {}, sessionId);
    async function evaluate(expression) {
        const result=await call('Runtime.evaluate', {expression, awaitPromise:true, returnByValue:true}, sessionId);
        if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
        return result.result.value;
    }
    async function navigate(origin) {
        const result=await call('Page.navigate', {url:origin+'/worksets.mjs'}, sessionId);
        if (result.errorText) throw new Error(result.errorText);
        for (let i=0;i<100;i++) {
            try {if(await evaluate(`location.href === ${JSON.stringify(origin+'/worksets.mjs')} && document.readyState === 'complete'`)) return;}
            catch (e) {if (!/context|navigat/i.test(String(e))) throw e;}
            await delay(50);
        }
        throw new Error('Document navigation did not complete');
    }
    await navigate(legacyOrigin);
    const v1=await evaluate("fetch('/api/v2/worksets').then(r=>{if(!r.ok)throw Error(r.status);return r.json()})");
    await navigate(normalOrigin);
    const cases=fs.readFileSync(new URL('./normal_child_consumer_cases.mjs',import.meta.url),'utf8').replace('export function','function');
    const report=await evaluate(`(async()=>{${cases}\n
        const api=await import('/worksets.mjs');
        const response=await fetch('/api/v2/worksets'); if(!response.ok)throw Error(response.status);
        const v2=await response.json(); const container=document.createElement('main'); document.body.replaceChildren(container);
        if(!(container instanceof HTMLElement) || document.createElement.toString().includes('[native code]')===false)throw Error('Not native DOM');
        return {...exerciseConsumers(api,${JSON.stringify(v1)},v2,container), browser_user_agent:navigator.userAgent,
            module_url:new URL('/worksets.mjs',location.href).href,
            module_source:await (await fetch('/worksets.mjs')).text(), dom:container.outerHTML};})()`);
    const source=fs.readFileSync(new URL('../cpn/frontend/static/worksets.mjs',import.meta.url),'utf8');
    if(report.module_source!==source) throw new Error('Browser module differs from candidate');
    report.browser_identity=identity;
    fs.writeFileSync(path.join(evidence,'browser-receipt.json'),JSON.stringify(report,null,2));
    const screenshot=await call('Page.captureScreenshot',{format:'png'},sessionId);
    fs.writeFileSync(path.join(evidence,'browser.png'),Buffer.from(screenshot.data,'base64'));
    delete report.module_source;
    console.log(JSON.stringify(report,null,2));
    await call('Browser.close');
} finally {
    ws?.close();
    if (!browserExit) {
        await Promise.race([closed, delay(2000)]);
        if (!browserExit) browser.kill('SIGTERM');
        await closed;
    }
    fs.closeSync(out); fs.closeSync(err);
    fs.writeFileSync(path.join(evidence,'chromium-exit.json'),JSON.stringify(browserExit,null,2));
}
