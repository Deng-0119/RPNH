"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let lang = navigator.language.startsWith("zh") ? "zh" : "en", state = null, busy = false, retry = null;
  const words = {
    eyebrow:["SYNTHETIC LOCAL DEMO","本地合成演示"], title:["External evidence. One registered quote.","外部证据接入，生成一次登记报价"],
    intro:["Inspect the hotel request, choose a synthetic fixture, and follow the same native run.","核对酒店请求、选择合成 fixture，再查看同一原生运行的结果。"],
    stage:["Controlled external-result intake","受控外部结果接入"],
    scope:["Custom local HTTP with synthetic status fixtures. No external service or cryptographic verification.","自定义本地 HTTP 与合成状态 fixture。无外部服务或密码学验证。"],
    refresh:["Refresh state","刷新状态"], viewer:["Open original Viewer · same run","打开原 Viewer · 同一运行"],
    request:["01 · INSPECT REQUEST","01 · 核对请求"], price:["Base quote: USD 200.00 · Trusted fixture discount: 25% · No booking or payment","原价 USD 200.00 · 可信 fixture 折扣 25% · 无预订或支付"],
    association:["Exact nonsecret association","精确非秘密关联"], select:["02 · CHOOSE A SYNTHETIC FIXTURE","02 · 选择合成 fixture"],
    fixtureHelp:["These are deterministic status labels, not signed vouchers. The local adapter submits minimal evidence through its native verifier session.","这些是确定性状态标签，不是签名凭证。本地适配器通过原生 verifier 会话提交最小证据。"],
    trusted:["Trusted25 · 25% fixture","Trusted25 · 25% 合成折扣"],untrusted:["Untrusted50 · no discount","Untrusted50 · 不折扣"],
    expired:["Expired fixture · no discount","过期 fixture · 不折扣"],deny:["Decline · no discount","拒绝 · 不折扣"],
    renew:["Replace waiting evidence request","替换当前等待的证据请求"],
    renewHelp:["Replacement changes the native wait generation and challenge reference. Old callbacks cannot authorize the new wait.","替换将更新原生等待代际及 challenge 引用。旧回调不能授权新等待。"],
    result:["03 · REGISTERED RESULT","03 · 已登记结果"],noResult:["Waiting for fixture selection","等待选择 fixture"],
    resultDetails:["Original result and checkpoint references","原始结果与 checkpoint 引用"],
    local:["Controlled loopback demo: all browsers share one holder. Fixture evidence does not establish a real wallet identity or multi-tenant isolation.","受控本地演示：所有浏览器共享一个 holder。Fixture 证据不代表真实钱包身份或多租户隔离。"],
    effects:["No model calls, issuer, wallet, merchant, booking or payment effects.","无模型调用、issuer、钱包、外部商户、预订或支付效果。"],
    waiting:["Waiting for fixture evidence","等待 fixture 证据"],completed:["Mock quote completed","模拟报价已完成"],
    unresolved:["Unresolved · exact association required","未决 · 需要精确关联"],unknown:["Unknown · inspect native evidence","未知 · 请检查原生证据"],
    unavailable:["State unavailable; refresh before acting.","状态不可用；请刷新后操作。"],
    uncertain:["Response lost. Refresh original state before retrying; completion is unconfirmed.","响应丢失。重试前请刷新原状态；当前不能确认完成。"],
    fixture_eligible:["Synthetic trusted fixture eligible · no cryptographic claim","合成可信 fixture 符合条件 · 无密码学验证声明"],
    fixture_untrusted:["Synthetic untrusted fixture · discount denied","合成不可信 fixture · 不予折扣"],
    fixture_expired:["Synthetic expired fixture · discount denied","合成过期 fixture · 不予折扣"],
    denied:["Holder declined · discount denied","用户拒绝 · 不予折扣"], timeout:["Evidence request expired · discount denied","证据请求超时 · 不予折扣"]
  };
  const t = key => words[key]?.[lang === "zh" ? 1 : 0] || key;
  function render() {
    document.documentElement.lang = lang === "zh" ? "zh-Hans" : "en";
    document.querySelectorAll("[data-text]").forEach(e => e.textContent = t(e.dataset.text));
    $("language").textContent = lang === "zh" ? "English" : "中文";
    $("status").textContent = state ? t(state.status) : t("unavailable");
    $("status").dataset.status = state?.status || "unknown";
    $("association").textContent = JSON.stringify(state?.association ?? null, null, 2);
    const association = state?.association;
    const facts = lang === "zh" ? [["请求",association?.authorization_request_ref],["等待代际",association?.generation],["有效期",association?.expires_at_utc]] : [["Request",association?.authorization_request_ref],["Wait generation",association?.generation],["Expires",association?.expires_at_utc]];
    $("summary").replaceChildren(...facts.flatMap(([k,v]) => {const a=document.createElement("dt"),b=document.createElement("dd");a.textContent=k;b.textContent=v??"—";return [a,b];}));
    const result = state?.result;
    $("quote").textContent = result && state.status === "completed" ? new Intl.NumberFormat(lang,{style:"currency",currency:result.currency}).format(result.amount_minor/100) : t(state && state.status !== "waiting" ? state.status : "noResult");
    $("verdict").textContent = result ? t(result.verdict === "expired" ? "timeout" : result.verdict) : "";
    $("counts").textContent = state ? `${lang === "zh" ? "报价 firing / 输入回执 / 模型调用" : "Quote firings / input receipts / model calls"}: ${state.counts.quote_firings} / ${state.counts.input_receipts} / ${state.counts.model_calls.join(",")}` : "";
    $("result").textContent = JSON.stringify({result:result??null,checkpoint_ref:state?.checkpoint_ref??null},null,2);
    document.querySelectorAll("[data-fixture]").forEach(e => e.disabled = busy || !state?.client.allowed_actions.includes("select"));
    $("renew").disabled = busy || !state?.client.allowed_actions.includes("renew");
    $("refresh").disabled = busy;
    $("main").setAttribute("aria-busy",String(busy));
  }
  function message(value) {$("feedback").hidden=!value;$("feedback").textContent=value||"";}
  async function read() {
    try {const response=await fetch("/api/state",{cache:"no-store"});if(!response.ok)throw Error(`HTTP ${response.status}`);state=await response.json();}
    catch(error) {state=null;message(`${t("unavailable")} ${error.message}`);}
  }
  async function refresh() {if(busy)return;busy=true;render();await read();busy=false;render();}
  async function send(path,body) {
    if(busy)return;busy=true;render();message("");
    try {const response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const value=await response.json();message(value.error ? `HTTP ${response.status}: ${value.error}` : "");}
    catch {message(t("uncertain"));}
    await read();busy=false;render();
  }
  document.querySelectorAll("[data-fixture]").forEach(e=>e.addEventListener("click",()=>{
    if(!state?.association)return;
    const body={association:state.association,fixture:e.dataset.fixture};
    const key=JSON.stringify(body);
    if(!retry||retry.key!==key)retry={key,id:crypto.randomUUID()};
    void send("/api/select",{...body,command_id:retry.id});
  }));
  $("renew").addEventListener("click",()=>{if(state?.association)void send("/api/renew",{association:state.association});});
  $("refresh").addEventListener("click",()=>void refresh());
  $("language").addEventListener("click",()=>{lang=lang==="zh"?"en":"zh";render();});
  render();void refresh();
})();
