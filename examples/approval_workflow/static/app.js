"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  let language = navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
  let state = null;
  let busy = false;
  let feedback = null;
  let connection = "loading";
  // Transport retry identity only: no local approval state, identity or credentials.
  // Kept in this page's memory; never automatically replayed or persisted.
  let lastCommand = null;

  const words = {
    skip: ["Skip to content", "跳转到内容"], demo: ["Controlled demo", "受控演示"],
    mock: ["MOCK · NO MONEY MOVES", "模拟业务 · 不涉及真实资金"],
    title: ["Review before action", "先核对，再审批"],
    intro: ["Submit a synthetic request, review its exact candidate, then follow the server-reported result.", "提交合成申请，核对精确候选动作，再查看服务器报告的结果。"],
    refresh: ["Refresh state", "刷新状态"], context: ["This client", "当前客户端"],
    binding: ["The server prebinds this client to an existing native identity. This label does not grant permission.", "服务器已将此客户端预绑定到既有原生身份。此处名称仅供识别，不授予权限。"],
    state: ["Current case", "当前案例"], step1: ["01 · REQUEST", "01 · 申请"],
    requestTitle: ["Request a mock refund", "申请模拟退款"],
    requestHelp: ["Use synthetic business data only. The server supplies the case and checks the policy.", "仅填写合成业务数据。案例由服务器提供，政策由服务器核验。"],
    amountInput: ["Amount in integer minor units", "金额（整数最小货币单位）"],
    amountHelp: ["Enter whole minor units, not a decimal amount. Currency determines the decimal places.", "填写整数最小单位，不填写小数金额。小数位数由币种决定。"],
    currency: ["Currency", "币种"],
    currencyHelp: ["Three-letter currency code. Accepted currencies are checked by the server.", "填写三字母币种代码。服务器核验是否支持该币种。"],
    reason: ["Reason", "理由"], submit: ["Submit request", "提交申请"],
    step2: ["02 · EXACT CANDIDATE", "02 · 精确候选"], candidateTitle: ["What is being approved?", "本次批准什么？"],
    exactDetails: ["Exact references and full candidate JSON", "精确引用与完整候选 JSON"],
    decisionHelp: ["A decision applies only to the candidate and waiting input shown here. The server rechecks every decision.", "决定仅针对当前展示的候选动作及等待输入。服务器会重新核验每次决定。"],
    approve: ["Approve this candidate", "批准此候选"], deny: ["Deny this candidate", "拒绝此候选"],
    step3: ["03 · SERVER RESULT", "03 · 服务器结果"], resultTitle: ["Outcome & recovery", "结果与恢复"],
    navigation: ["Continue or return", "继续或返回"], viewer: ["Open original Viewer · same run", "打开原 Viewer · 同一运行"],
    requesterLink: ["Return to requester page", "返回申请页面"], approverLink: ["Open approver page", "打开审批页面"], homeLink: ["Return to demo home", "返回演示首页"],
    noLinks: ["Navigation appears when the server provides it.", "服务器提供入口后，此处显示导航链接。"],
    navigationHelp: ["Links do not change identity or grant access. Open the approver page in its already bound client context. A 403 means that context is not authorized.", "链接不会切换身份或授予权限。请在已经预绑定的审批客户端环境中打开审批页。403 表示当前环境没有访问权限。"],
    footer: ["Controlled mock application · Local registered results only · No real refunds, payments or bookings", "受控模拟应用 · 仅生成本地登记结果 · 无真实退款、支付或预订"],
    loading: ["Reading server state…", "正在读取服务器状态…"], sending: ["Submitting command…", "正在提交命令…"],
    current: ["Showing the latest received server state. Refresh to check for changes.", "当前显示最近收到的服务器状态。刷新可查看变化。"],
    unavailable: ["Current state is unavailable. Refresh to read it again.", "当前状态不可用，请刷新重读。"],
    missing: ["Not provided", "未提供"], caseId: ["Case", "案例"], order: ["Synthetic order", "模拟订单"], amount: ["Amount", "金额"],
    minor: ["minor units", "最小单位"], noCandidate: ["No candidate was provided by the server.", "服务器尚未提供候选动作。"],
    noRequest: ["Request is unavailable for this client or current state.", "此客户端或当前状态不可提交申请。"],
    noDecision: ["Decision is unavailable for this client or current state.", "此客户端或当前状态不可审批。"],
    missingRefs: ["The candidate is incomplete. Refresh before deciding.", "候选信息不完整，请刷新后再审批。"],
    noResult: ["No result was provided by the server. Approval alone does not confirm completion.", "服务器尚未提供结果。获批本身不代表执行完成。"],
    resultProvided: ["Server-reported result, shown in full below.", "以下完整展示服务器报告的结果。"],
    unknownHelp: ["The outcome is unknown. Refresh or inspect the original Viewer; no command is replayed automatically.", "结果未知。请刷新或查看原 Viewer；页面不会自动重放命令。"],
    recoverableHelp: ["The server reports that recovery is available. Recovery is handled by the original runtime; refresh to follow its result.", "服务器报告可恢复。恢复由原运行时处理，请刷新查看结果。"],
    recoveredHelp: ["The server reports a recovered result. Inspect the result and original Viewer for details.", "服务器报告已恢复的结果。请核对下方结果与原 Viewer。"],
    not_requested: ["Not requested", "尚未申请"], waiting: ["Waiting for a decision", "等待审批"],
    approved: ["Approved · completion unconfirmed", "已批准 · 完成情况待确认"], denied: ["Denied", "已拒绝"],
    expired: ["Expired", "已过期"], completed: ["Completed", "已完成"], unknown: ["Unknown", "未知"],
    recoverable: ["Recovery available", "可恢复"], recovered: ["Recovered (server reported)", "已恢复（服务器报告）"],
    not_requestedHelp: ["A permitted requester can submit synthetic case data.", "获准的申请客户端可提交合成案例数据。"],
    waitingHelp: ["Review the exact candidate before deciding in the bound approver context.", "请在预绑定的审批客户端中核对精确候选后作出决定。"],
    approvedHelp: ["The server reports approval. Follow the result to confirm whether execution completed.", "服务器报告已批准。请查看结果，确认执行是否完成。"],
    deniedHelp: ["The server reports a denial. This is not a successful refund.", "服务器报告已拒绝，这不是退款成功。"],
    expiredHelp: ["The server reports that this wait expired. An old decision cannot confirm completion.", "服务器报告本次等待已过期。旧决定不能确认执行完成。"],
    completedHelp: ["The server reports completion of the mock operation. No real money moves.", "服务器报告模拟操作已完成，不涉及真实资金。"],
    auth: ["Use the already bound client context. This page cannot sign in, switch roles or grant access.", "请使用已经预绑定的客户端环境。此页面不能登录、切换角色或授予权限。"],
    http: ["Server response", "服务器响应"],
    noMessage: ["No server message was provided; read the refreshed state below.", "服务器未提供消息，请核对刷新后的状态。"],
    readError: ["Could not read server state.", "无法读取服务器状态。"],
    uncertain: ["The command outcome is uncertain. No automatic retry was made. Refresh first; if the server still permits this action, an explicit retry of the unchanged body uses the same command ID in this open page. Reloading loses that retry ID.", "命令结果不确定，页面未自动重试。请先刷新；若服务器仍允许该动作，在本页明确重试未更改的报文会沿用命令 ID。重新载入页面会丢失此重试 ID。"],
    badState: ["The server returned an invalid state response.", "服务器返回了无效的状态响应。"],
    invalidAmount: ["Enter a nonnegative safe integer in minor units and a three-letter currency code.", "请填写安全整数范围内的非负最小单位金额及三字母币种代码。"],
    unavailableAction: ["This action is not currently available. Refresh the server state.", "当前不可执行此动作，请刷新服务器状态。"],
    cryptoMissing: ["This browser cannot generate a command ID. No command was sent.", "此浏览器无法生成命令 ID，未发送命令。"],
    candidate_ref: ["Candidate reference", "候选引用"], wait_ref: ["Waiting input reference", "等待输入引用"],
    command_ref: ["Original command reference", "原命令引用"], policy_ref: ["Policy reference", "政策引用"], source_refs: ["Source references", "来源引用"],
    run_ref: ["Run reference", "运行引用"], task_ref: ["Task reference", "任务引用"], net_ref: ["Net reference", "网络引用"], transition_id: ["Transition", "变迁"],
  };
  const t = (key) => (words[key] || words.missing)[language === "zh" ? 1 : 0];
  const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const display = (value) => value === undefined || value === null ? t("missing") : typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const present = (value) => value !== undefined && value !== null && value !== "";
  const locale = () => language === "zh" ? "zh-CN" : "en-US";

  // API adapter: the only fetch location. No bootstrap, role, actor or session selection.
  async function api(path, body) {
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      credentials: "same-origin", cache: "no-store", redirect: "error",
      headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const raw = await response.text();
    let data;
    try { data = JSON.parse(raw); } catch { data = null; }
    return { ok: response.ok, status: response.status, data, message: displayMessage(data, raw) };
  }
  function displayMessage(data, raw) {
    if (object(data)) {
      const value = data.message ?? (object(data.error) ? data.error.message : data.error);
      return present(value) ? display(value) : "";
    }
    return raw;
  }

  function amountText(amount, currency) {
    const raw = `${display(amount)} ${t("minor")} · ${display(currency)}`;
    if (!Number.isSafeInteger(amount) || typeof currency !== "string" || !/^[A-Z]{3}$/.test(currency)) return raw;
    try {
      const format = new Intl.NumberFormat(locale(), { style: "currency", currency, currencyDisplay: "code" });
      const digits = format.resolvedOptions().maximumFractionDigits;
      const units = BigInt(amount);
      const scale = 10n ** BigInt(digits);
      const major = units / scale;
      const fraction = ((units < 0n ? -units : units) % scale).toString().padStart(digits, "0");
      // BigInt division keeps large safe minor-unit integers exact, including their last cent.
      const formatted = format.formatToParts(major === 0n && units < 0n ? -0 : major)
        .map((part) => part.type === "fraction" ? fraction : part.value).join("");
      return `${formatted} — ${raw}`;
    } catch { return raw; }
  }
  function facts(id, entries) {
    const nodes = [];
    for (const [key, value] of entries) {
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = t(key);
      dd.textContent = display(value);
      nodes.push(dt, dd);
    }
    $(id).replaceChildren(...nodes);
  }
  function setLink(id, value) {
    const anchor = $(id);
    anchor.hidden = true;
    anchor.removeAttribute("href");
    if (typeof value !== "string" || !value.trim()) return false;
    try {
      const url = new URL(value, window.location.href);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return false;
      anchor.href = url.href;
      anchor.hidden = false;
      return true;
    } catch { return false; }
  }
  function allowed(action) {
    return Array.isArray(state?.client?.allowed_actions) && state.client.allowed_actions.includes(action);
  }
  function candidateComplete(candidate) {
    return object(candidate) && Number.isSafeInteger(candidate.amount_minor) &&
      typeof candidate.currency === "string" && /^[A-Z]{3}$/.test(candidate.currency) &&
      ["order", "reason", "candidate_ref", "wait_ref", "command_ref", "policy_ref", "source_refs"].every((key) => present(candidate[key])) &&
      object(candidate.target) && ["run_ref", "task_ref", "net_ref", "transition_id"].every((key) => present(candidate.target[key]));
  }
  function updateControls() {
    const requestAllowed = allowed("request") && present(state?.case?.id);
    const decisionAllowed = allowed("decide") && state?.case?.status === "waiting" && candidateComplete(state?.case?.candidate);
    $("request-fields").disabled = busy || !requestAllowed;
    $("approve").disabled = busy || !decisionAllowed;
    $("deny").disabled = busy || !decisionAllowed;
    $("refresh").disabled = busy;
    $("request-access").textContent = requestAllowed ? "" : t("noRequest");
    $("decision-access").textContent = allowed("decide") && state?.case?.status === "waiting" && !candidateComplete(state?.case?.candidate) ? t("missingRefs") : decisionAllowed ? "" : t("noDecision");
    $("main").setAttribute("aria-busy", String(busy));
  }
  function preview() {
    const raw = $("amount").value;
    const currency = $("currency").value.trim().toUpperCase();
    $("amount-preview").textContent = /^[0-9]+$/.test(raw) && Number.isSafeInteger(Number(raw)) ? amountText(Number(raw), currency || undefined) : "";
  }
  function renderFeedback() {
    $("feedback").hidden = !feedback;
    if (!feedback) { $("feedback").textContent = ""; return; }
    const lines = [t(feedback.key)];
    if (feedback.status) lines.push(`HTTP ${feedback.status}`);
    if (feedback.message) lines.push(feedback.message);
    else if (feedback.key === "http") lines.push(t("noMessage"));
    if ([401, 403].includes(feedback.status)) lines.push(t("auth"));
    if (feedback.readFailure) {
      lines.push(t("readError"));
      if (feedback.readFailure.status) lines.push(`HTTP ${feedback.readFailure.status}`);
      if (feedback.readFailure.message) lines.push(feedback.readFailure.message);
      if ([401, 403].includes(feedback.readFailure.status)) lines.push(t("auth"));
    }
    $("feedback").textContent = lines.join("\n");
  }
  function render() {
    document.documentElement.lang = language === "zh" ? "zh-Hans" : "en";
    document.title = `${t("requestTitle")} · RPNH`;
    document.querySelectorAll("[data-i18n]").forEach((element) => { element.textContent = t(element.dataset.i18n); });
    $("language").textContent = language === "zh" ? "English" : "中文";
    $("language").lang = language === "zh" ? "en" : "zh-Hans";
    $("language").setAttribute("aria-label", language === "zh" ? "Switch to English" : "切换到中文");
    $("navigation").setAttribute("aria-label", t("navigation"));
    $("candidate-json").setAttribute("aria-label", t("exactDetails"));
    $("result-json").setAttribute("aria-label", t("resultTitle"));
    $("connection").textContent = t(connection);
    $("client-name").textContent = display(state?.client?.display_name);
    const currentCase = state?.case;
    const known = ["not_requested", "waiting", "approved", "denied", "expired", "completed", "unknown", "recoverable", "recovered"];
    const status = known.includes(currentCase?.status) ? currentCase.status : "unknown";
    $("status").dataset.status = status;
    $("status").textContent = t(status);
    $("status-help").textContent = t(`${status}Help`);
    facts("case-summary", [["caseId", currentCase?.id], ["order", currentCase?.request?.order], ["state", currentCase?.status],
      ["amount", currentCase?.request ? amountText(currentCase.request.amount_minor, currentCase.request.currency) : undefined], ["reason", currentCase?.request?.reason]]);
    const notices = Array.isArray(state?.notices) ? state.notices : present(state?.notices) ? [state.notices] : [];
    $("notices").replaceChildren(...notices.map((notice) => { const li = document.createElement("li"); li.textContent = display(notice); return li; }));
    $("notices").hidden = !notices.length;
    const candidate = currentCase?.candidate;
    $("candidate-empty").textContent = t("noCandidate");
    $("candidate-empty").hidden = object(candidate);
    $("candidate-content").hidden = !object(candidate);
    facts("candidate-summary", [["caseId", currentCase?.id], ["order", candidate?.order], ["amount", candidate ? amountText(candidate.amount_minor, candidate.currency) : undefined], ["currency", candidate?.currency], ["reason", candidate?.reason]]);
    facts("candidate-refs", ["candidate_ref", "wait_ref", "command_ref", "policy_ref", "source_refs"].map((key) => [key, candidate?.[key]])
      .concat(["run_ref", "task_ref", "net_ref", "transition_id"].map((key) => [key, candidate?.target?.[key]])));
    $("candidate-json").textContent = display(candidate);
    const hasResult = state && present(state.result);
    $("result-json").hidden = !hasResult;
    $("result-json").textContent = hasResult ? JSON.stringify(state.result, null, 2) : "";
    $("result-help").textContent = ["unknown", "recoverable", "recovered"].includes(status) ? t(`${status}Help`) : t(hasResult ? "resultProvided" : "noResult");
    const navigation = state?.navigation;
    const links = [setLink("viewer-link", state?.viewer_url), setLink("requester-link", navigation?.requester), setLink("approver-link", navigation?.approver), setLink("home-link", navigation?.home)];
    $("navigation-empty").hidden = links.some(Boolean);
    renderFeedback();
    preview();
    updateControls();
  }

  async function readState() {
    try {
      const response = await api("/api/state");
      if (!response.ok || !object(response.data) || !object(response.data.client) || !object(response.data.case)) {
        state = null;
        connection = "unavailable";
        const failure = { key: response.ok ? "badState" : "readError", status: response.status, message: response.message };
        // Keep a POST's original message, including 409/403, even if the subsequent GET fails.
        if (feedback) feedback.readFailure = failure;
        else feedback = failure;
        return;
      }
      state = response.data;
      connection = "current";
      if (feedback) delete feedback.readFailure;
    } catch {
      state = null;
      connection = "unavailable";
      if (feedback) feedback.readFailure = {};
      else feedback = { key: "readError" };
    }
  }
  async function refresh() {
    if (busy) return;
    busy = true;
    connection = "loading";
    // Preserve uncertain-write guidance until another explicit command is attempted.
    if (feedback?.key !== "uncertain") feedback = null;
    render();
    await readState();
    busy = false;
    render();
  }
  async function send(path, body) {
    if (busy) return;
    const serialized = JSON.stringify(body);
    if (!lastCommand || lastCommand.path !== path || lastCommand.body !== serialized) {
      if (!globalThis.crypto?.randomUUID) { feedback = { key: "cryptoMissing" }; renderFeedback(); return; }
      lastCommand = { path, body: serialized, id: crypto.randomUUID() };
    }
    busy = true;
    connection = "sending";
    feedback = null;
    render();
    try {
      const response = await api(path, { command_id: lastCommand.id, ...body });
      feedback = { key: "http", status: response.status, message: response.message };
    } catch {
      feedback = { key: "uncertain" };
    }
    // POST responses never become UI state, including HTTP 200. Always read authority afresh.
    await readState();
    busy = false;
    render();
  }
  $("request-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (busy) return;
    if (!allowed("request") || !present(state?.case?.id)) { feedback = { key: "unavailableAction" }; renderFeedback(); return; }
    const raw = $("amount").value;
    const amount = Number(raw);
    const currency = $("currency").value.trim().toUpperCase();
    if (!/^[0-9]+$/.test(raw) || !Number.isSafeInteger(amount) || !/^[A-Z]{3}$/.test(currency)) {
      feedback = { key: "invalidAmount" }; renderFeedback(); return;
    }
    if (!$("request-form").reportValidity()) return;
    void send("/api/request", { case_id: state.case.id, amount_minor: amount, currency, reason: $("reason").value });
  });
  function decide(decision) {
    if (busy) return;
    const candidate = state?.case?.candidate;
    if (!allowed("decide") || state?.case?.status !== "waiting" || !candidateComplete(candidate)) {
      feedback = { key: "unavailableAction" }; renderFeedback(); return;
    }
    void send("/api/decision", { decision, candidate_ref: candidate.candidate_ref, wait_ref: candidate.wait_ref, target: candidate.target, candidate: candidate.immutable_candidate });
  }
  $("approve").addEventListener("click", () => decide("approve"));
  $("deny").addEventListener("click", () => decide("deny"));
  $("refresh").addEventListener("click", () => { void refresh(); });
  $("language").addEventListener("click", () => { language = language === "zh" ? "en" : "zh"; render(); });
  $("amount").addEventListener("input", preview);
  $("currency").addEventListener("input", preview);
  render();
  void refresh();
})();
