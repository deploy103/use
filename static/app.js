"use strict";

(() => {
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const state = {
    authenticated: false, setup: false, setupToken: "", view: "overview",
    range: "7d", provider: "all", account: "all", metric: "total_tokens",
    providers: [], accounts: [], data: null, editing: null, deleting: null,
    generation: 0, sessionGeneration: 0, collecting: false, poll: null,
    oauth: null, toast: null,
  };
  // Explicit allowlist: neither provider metadata nor legacy accounts can add connectors.
  const providers = {
    chatgpt: { name: "ChatGPT", mark: "◎", subtitle: "브라우저 OAuth · Codex 사용량과 한도", help: "브라우저에서 ChatGPT 계정을 연결합니다. ChatGPT 웹 전체 대화의 사용량은 제공하지 않습니다." },
    "codex-lb": { name: "Codex LB", mark: "⌘", subtitle: "게이트웨이 접근 키 · ChatGPT 로그인과 별도", credential: "게이트웨이 접근 키", help: "Codex LB 서버 주소와 게이트웨이 접근 키를 입력하세요. ChatGPT 브라우저 OAuth나 OpenAI 조직 관리자 키와 다른 연결입니다." },
    "claude-lb": { name: "Claude LB", mark: "✳", subtitle: "Claude LB 조회 키 · 서비스별 한도", credential: "Claude LB 조회 키", help: "Claude LB 조회 키로 제공되는 한도와 사용량을 조회합니다. 한도 금액은 실제 청구액이 아닌 사용량 환산액일 수 있습니다." },
    gemini: { name: "Gemini LB", mark: "✦", subtitle: "게이트웨이 키 · Google 계정 로그인 아님", credential: "Gemini LB 게이트웨이 키", help: "Gemini LB 서버 주소와 게이트웨이 키를 입력하세요. Google 추론 API 키가 아닙니다. 누적값과 한도만 제공하는 연결은 일별 통계에 포함되지 않습니다." },
    grok: { name: "Grok / xAI API", mark: "𝕏", subtitle: "관리 API 키 · Grok 웹 구독과 별도", credential: "xAI 관리 API 키", help: "xAI 관리 API 키와 Team ID로 일별 API 비용을 조회합니다. Grok 웹 구독의 사용량이나 토큰·요청 수를 제공하는 연결은 아닙니다." },
    openai: { name: "OpenAI API", mark: "○", subtitle: "조직 관리자 키 · API 사용량과 비용", credential: "OpenAI 조직 관리자 키", help: "OpenAI API 조직 관리자 키가 필요합니다. ChatGPT 구독 로그인과 별개입니다. API 일별 집계는 UTC 기준일 수 있으며 조직 비용은 모델별 비용과 구분됩니다." },
    "usage-json": { name: "Usage JSON", mark: "{}", subtitle: "신뢰할 수 있는 HTTPS 사용량 엔드포인트", credential: "Bearer 인증 정보 (선택)", help: "신뢰하는 HTTPS 서버의 /usage.json에서 사용량을 조회합니다. 인증이 필요하지 않은 서버는 키를 비워둘 수 있습니다." },
  };
  const allowed = (id) => Object.hasOwn(providers, id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const integer = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
  const decimal = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 });
  const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
  const currency = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 });
  const fmt = (value) => finite(value) ? integer.format(value) : "—";
  const short = (value) => finite(value) ? compact.format(value) : "—";
  const money = (value) => finite(value) ? currency.format(value) : "—";
  const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}" /></svg>`;
  const mark = (id) => `<span class="provider-mark ${allowed(id) ? id : ""}" aria-hidden="true">${esc(providers[id]?.mark || "?")}</span>`;
  const metrics = { requests: "요청", input_tokens: "일반 입력", output_tokens: "출력", cache_read_tokens: "캐시 읽기", cache_write_tokens: "캐시 쓰기", total_tokens: "총 토큰", cost_usd: "비용 (USD)" };
  const value = (key, amount) => key === "cost_usd" ? money(amount) : fmt(amount);
  const placeholder = (account) => account.has_credentials !== true && account.status === "needs_auth";
  const linkedAccounts = () => state.accounts.filter((account) => !placeholder(account));
  const filteredAccounts = () => linkedAccounts().filter((account) => (state.provider === "all" || account.provider === state.provider) && (state.view === "connections" || state.account === "all" || String(account.id) === state.account));
  const query = () => new URLSearchParams({ range: state.range, provider: state.provider, account: state.account }).toString();

  function dateTime(input) {
    const date = input ? new Date(input) : null;
    return date && Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date) : "—";
  }
  function notice(id, message = "") {
    $(`#${id}`).textContent = message;
    $(`#${id}`).hidden = !message;
  }
  function toast(message) {
    clearTimeout(state.toast);
    notice("toast", message);
    state.toast = setTimeout(() => notice("toast"), 4500);
  }
  const dialogOpeners = new WeakMap();
  function openDialog(id) {
    const dialog = $(`#${id}`);
    const opener = document.activeElement;
    dialogOpeners.set(dialog, opener?.closest("dialog") ? $("#add-account") : opener);
    dialog.showModal();
  }
  async function api(path, { method = "GET", body, blob = false } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(`/api${path}`, {
        method, credentials: "same-origin", cache: "no-store", signal: controller.signal,
        headers: { Accept: blob ? "text/csv" : "application/json", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
      if (!response.ok) {
        let message = `요청을 처리하지 못했습니다. (HTTP ${response.status})`;
        // Authentication callbacks may contain secrets. Never reflect server-supplied details.
        if (path.startsWith("/chatgpt/")) {
          message = response.status === 404 ? "연결 요청을 찾을 수 없습니다. 다시 시작하세요." : "연결을 완료하지 못했습니다. 주소 전체와 유효 시간을 확인하고 다시 시도하세요.";
        } else {
          try { const data = await response.json(); if (typeof data.detail === "string") message = data.detail; } catch { /* Keep the HTTP status. */ }
        }
        if (response.status === 401 && !["/login", "/setup"].includes(path)) showLogin("관리자 세션이 만료되었습니다. 다시 로그인하세요.");
        throw new Error(message);
      }
      return blob ? await response.blob() : response.status === 204 ? {} : await response.json();
    } catch (error) {
      if (error.name === "AbortError") throw new Error("응답 시간이 초과되었습니다. 다시 시도하세요.");
      if (error instanceof TypeError) throw new Error("서버에 연결할 수 없습니다. 네트워크를 확인하세요.");
      throw error;
    } finally { clearTimeout(timer); }
  }

  function showLogin(message = "") {
    state.authenticated = false;
    state.sessionGeneration += 1;
    state.generation += 1;
    stopOAuth(false);
    clearTimeout(state.poll);
    state.accounts = [];
    state.data = null;
    state.collecting = false;
    $$("dialog[open]").forEach((dialog) => dialog.close());
    $("#account-form").reset();
    $("#provider-extra-fields").replaceChildren();
    $("#app-view").hidden = true;
    $("#boot").hidden = true;
    $("#login-view").hidden = false;
    $("#password").value = "";
    $("#password-confirm").value = "";
    $("#setup-fields").hidden = !state.setup;
    $("#setup-token-wrap").hidden = Boolean(state.setupToken);
    $("#setup-token").required = state.setup && !state.setupToken;
    $("#password-confirm").required = state.setup;
    $("#password").minLength = state.setup ? 12 : 1;
    $("#password").autocomplete = state.setup ? "new-password" : "current-password";
    $("#login-title").textContent = state.setup ? "Use 관리자 최초 설정" : "Use 관리자 로그인";
    $("#login-description").textContent = state.setup ? "이 서버에서 사용할 관리자 비밀번호를 설정하세요." : "이 서버의 관리자 비밀번호를 입력하세요.";
    $("#login-submit").textContent = state.setup ? "관리자 비밀번호 설정" : "관리자 로그인";
    notice("toast");
    notice("login-error", message);
  }
  async function enterDashboard() {
    state.authenticated = true;
    state.setup = false;
    state.sessionGeneration += 1;
    const session = state.sessionGeneration;
    $("#boot").hidden = true;
    $("#login-view").hidden = true;
    $("#app-view").hidden = false;
    state.data = null;
    state.accounts = [];
    render({ totals: {}, models: [], daily: [] });
    try {
      const result = await api("/providers");
      if (!state.authenticated || session !== state.sessionGeneration) return;
      state.providers = (Array.isArray(result.providers) ? result.providers : []).filter((provider) => allowed(provider.id));
    } catch { /* Connector labels are available locally; dashboard retries metadata. */ }
    if (state.authenticated && session === state.sessionGeneration) await loadDashboard();
  }
  async function loadDashboard() {
    if (!state.authenticated) return;
    clearTimeout(state.poll);
    const generation = ++state.generation;
    $("#loading-status").hidden = false;
    $("#main-content").setAttribute("aria-busy", "true");
    try {
      const data = await api(`/dashboard?${query()}`);
      if (generation !== state.generation || !state.authenticated) return;
      state.data = data;
      state.accounts = (Array.isArray(data.accounts) ? data.accounts : []).filter((account) => allowed(account.provider));
      if (Array.isArray(data.providers)) state.providers = data.providers.filter((provider) => allowed(provider.id));
      state.collecting = Boolean(data.refreshing);
      notice("global-error");
      render(data);
    } catch (error) {
      if (generation === state.generation && state.authenticated) notice("global-error", `${error.message} ${state.data ? "마지막으로 조회한 데이터를 유지합니다. 화면의 조회 조건과 다를 수 있습니다." : "표시할 데이터를 가져오지 못했습니다. 사용량 수집 또는 페이지 새로고침으로 다시 시도하세요."}`);
    } finally {
      if (generation === state.generation) {
        $("#loading-status").hidden = true;
        $("#main-content").setAttribute("aria-busy", "false");
        if (state.authenticated && state.collecting) state.poll = setTimeout(loadDashboard, 2500);
      }
    }
  }
  function status(account) {
    if (account.enabled === false) return ["일시 중지", "neutral"];
    if (account.refreshing || ["refreshing", "collecting"].includes(account.status)) return ["수집 중", "warning"];
    if (account.error || ["error", "failed"].includes(account.status)) return ["수집 오류", "error-text"];
    if (["expired", "disconnected", "needs_auth"].includes(account.status)) return ["재연결 필요", "warning"];
    if (["partial", "unsupported"].includes(account.status)) return ["일부 제공", "neutral"];
    if (["connected", "ok", "success", "healthy"].includes(account.status)) return ["연결됨", "success"];
    return ["수집 대기", "neutral"];
  }
  function renderFilters() {
    $("#provider-filter").innerHTML = '<option value="all">모든 연결 종류</option>' + Object.entries(providers).map(([id, provider]) => `<option value="${id}">${esc(provider.name)}</option>`).join("");
    $("#provider-filter").value = state.provider;
    const accounts = linkedAccounts().filter((account) => state.provider === "all" || account.provider === state.provider);
    $("#account-filter").innerHTML = '<option value="all">모든 계정</option>' + accounts.map((account) => `<option value="${esc(account.id)}">${esc(account.name)}</option>`).join("");
    if (state.account !== "all" && !accounts.some((account) => String(account.id) === state.account)) $("#account-filter").add(new Option("선택한 계정", state.account));
    $("#account-filter").value = state.account;
    $$("[data-service]").forEach((button) => {
      const active = (button.dataset.service === "claude" ? "claude-lb" : button.dataset.service) === state.provider || (button.dataset.service === "all" && ["codex-lb", "openai", "usage-json"].includes(state.provider));
      button.classList.toggle("selected", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }
  function renderMetrics(totals) {
    const cache = finite(totals.cache_read_tokens) && finite(totals.cache_write_tokens) ? totals.cache_read_tokens + totals.cache_write_tokens : null;
    const items = [
      ["총 토큰", fmt(totals.total_tokens), `일반 입력 ${short(totals.input_tokens)} / 출력 ${short(totals.output_tokens)}`],
      ["요청", fmt(totals.requests), "제공업체가 보고한 요청 수"],
      ["캐시 토큰", fmt(cache), `읽기 ${short(totals.cache_read_tokens)} / 쓰기 ${short(totals.cache_write_tokens)}`],
      ["비용 (USD)", money(totals.cost_usd), "조회된 비용 · 실제 청구액과 다를 수 있음"],
    ];
    $("#metrics").innerHTML = items.map(([label, amount, note]) => `<div class="metric"><h2>${label}</h2><strong>${amount}</strong><p>${note}</p></div>`).join("");
  }
  function renderChart(daily) {
    const rows = [...daily].sort((a, b) => String(a.date).localeCompare(String(b.date)));
    const key = state.metric;
    const known = rows.filter((row) => finite(row[key]));
    $("#chart-detail").textContent = "기록이 있는 날짜만 표시합니다. 막대를 선택하면 정확한 값을 확인할 수 있습니다.";
    if (!known.length) {
      $("#daily-chart").innerHTML = '<div class="empty chart-empty"><span class="empty-chart" aria-hidden="true">▂ ▅ ▃ ▇</span><strong>이 기간의 일별 기록이 없습니다</strong><p>계정을 연결한 뒤 사용량을 수집하세요.<br />누적값만 제공하는 서비스에는 일별 기록이 없습니다.</p></div>';
      return;
    }
    // Credits/refunds must not look like positive spend on a zero-based chart.
    if (known.some((row) => row[key] < 0)) {
      $("#daily-chart").innerHTML = `<div class="daily-values"><table><thead><tr><th scope="col">날짜</th><th scope="col">${esc(metrics[key])}</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${esc(row.date)}</td><td>${value(key, row[key])}</td></tr>`).join("")}</tbody></table></div>`;
      $("#chart-detail").textContent = "음수 조정값이 포함되어 정확한 일별 값을 표로 표시합니다.";
      return;
    }
    const max = Math.max(...known.map((row) => Math.abs(row[key])), 0);
    const step = Math.max(1, Math.ceil(rows.length / 7));
    $("#daily-chart").innerHTML = `<div class="chart-scale" aria-hidden="true"><span>${key === "cost_usd" ? money(max) : short(max)}</span><span>0</span></div><div class="chart-scroll"><div class="chart-bars" style="min-width:${Math.max(0, rows.length * 20)}px">${rows.map((row, index) => {
      const amount = row[key];
      const detail = `${row.date} · ${metrics[key]} ${value(key, amount)}${finite(amount) ? "" : " (확인 불가)"}`;
      const height = finite(amount) && max ? Math.min(100, Math.abs(amount) / max * 100) : 0;
      return `<div class="bar-slot"><button class="bar-button${amount < 0 ? " negative" : ""}" style="height:${Math.max(2, height)}%" data-chart-detail="${esc(detail)}" aria-label="${esc(detail)}" title="${esc(detail)}">${!finite(amount) ? '<span class="unknown-bar">—</span>' : ""}</button><span class="bar-date" aria-hidden="true">${index % step === 0 || index === rows.length - 1 ? esc(String(row.date).slice(5).replace("-", "/")) : ""}</span></div>`;
    }).join("")}</div></div>`;
  }
  function renderModels(models) {
    $("#model-count").textContent = String(models.length);
    $("#model-empty").hidden = models.length > 0;
    $("#model-empty").innerHTML = '<strong>이 기간에 수집된 모델별 기록이 없습니다</strong><p>일별·모델별 내역을 제공하는 연결만 표시합니다. 전체 누적값은 아래 계정별 영역에서 확인하세요.</p>';
    $("#model-rows").innerHTML = models.map((model) => `<tr><td><div class="model-identity">${mark(model.provider)}<div><strong>${esc(model.model)}</strong><small>${esc(model.account_name)} / ${esc(providers[model.provider]?.name || model.provider)}</small></div></div></td>${["requests", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens", "cost_usd"].map((key) => `<td${key === "total_tokens" ? ' class="emphasis"' : ""}>${value(key, model[key])}</td>`).join("")}</tr>`).join("");
  }
  function renderConnections() {
    const accounts = linkedAccounts();
    const ids = Object.keys(providers).filter((id) => accounts.some((account) => account.provider === id));
    $("#connection-summary").innerHTML = ids.length ? ids.map((id) => {
      const group = accounts.filter((account) => account.provider === id);
      const issues = group.filter((account) => ["error-text", "warning"].includes(status(account)[1])).length;
      return `<button class="connection-row" data-provider-filter="${id}">${mark(id)}<span>${esc(providers[id].name)}</span><span class="${issues ? "warning" : "muted"}">${issues ? `${issues}개 확인 필요` : `${group.length}개 계정`}</span></button>`;
    }).join("") : '<div class="empty"><strong>연결된 계정 없음</strong><p>서비스별 계정을 추가하세요.</p><button class="text-button" data-action="connect">첫 계정 연결</button></div>';
    $("#nav-account-count").textContent = String(accounts.length);
    $("#service-count").textContent = String(accounts.length);
  }
  function renderQuota(quota) {
    if (!Array.isArray(quota) || !quota.length) return '<p class="muted">— 한도 정보가 제공되지 않았습니다.</p>';
    return quota.map((item) => {
      const percent = finite(item.used_percent) ? item.used_percent : null;
      const width = finite(percent) ? Math.max(0, Math.min(100, percent)) : 0;
      return `<div class="quota-item"><div class="quota-label"><span>${esc(item.label || "사용 한도")}</span><strong>${finite(percent) ? `${decimal.format(percent)}% 사용` : "— 사용률 확인 불가"}</strong></div>${finite(percent) ? `<div class="quota-track" role="progressbar" aria-label="${esc(item.label || "한도")} 사용률" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${width}" aria-valuetext="${esc(decimal.format(percent))}% 사용"><span class="${percent >= 95 ? "critical" : percent >= 80 ? "high" : ""}" style="width:${width}%"></span></div>` : ""}<p class="quota-caption">${finite(item.remaining) ? `잔여 ${decimal.format(item.remaining)} ${esc(item.unit || "")}` : ""}${finite(item.limit) ? ` / 한도 ${decimal.format(item.limit)} ${esc(item.unit || "")}` : ""}${item.resets_at ? ` · ${esc(dateTime(item.resets_at))} KST 초기화` : ""}</p></div>`;
    }).join("");
  }
  function renderSummary(summary) {
    if (!summary || typeof summary !== "object") return '<p class="muted">— 전체 누적값이 제공되지 않았습니다.</p>';
    const entries = Object.entries(metrics).filter(([key]) => finite(summary[key]));
    return `<dl class="lifetime-values">${entries.map(([key, label]) => `<div><dt>${label}</dt><dd>${value(key, summary[key])}</dd></div>`).join("")}</dl>${entries.length ? "" : '<p class="muted">— 확인 불가</p>'}<p class="quota-caption">서비스가 보고한 전체 누적값 · 선택 기간 통계와 별도</p>`;
  }
  function renderAccounts() {
    const open = new Map($$("[data-account-details]").map((element) => [element.dataset.accountDetails, element.open]));
    const accounts = filteredAccounts();
    $("#account-count").textContent = String(accounts.length);
    if (!accounts.length) {
      $("#accounts-list").innerHTML = `<div class="empty"><strong>${linkedAccounts().length ? "선택한 서비스에 연결된 계정이 없습니다" : "첫 번째 계정을 연결하세요"}</strong><p>서비스와 연결 방식을 선택한 뒤 여러 계정을 각각 추가할 수 있습니다.</p><button class="button" data-action="connect">계정 연결</button>${state.provider !== "all" || state.account !== "all" ? '<button class="text-button" data-action="reset">전체 계정 보기</button>' : ""}</div>`;
      return;
    }
    $("#accounts-list").innerHTML = accounts.map((account, index) => {
      const [label, kind] = status(account);
      const note = account.provider === "chatgpt" ? "Codex 사용량·한도 조회입니다. ChatGPT 웹 전체 대화의 사용량은 아닙니다." : account.note;
      return `<article class="account-row"><header class="account-header">${mark(account.provider)}<div class="account-identity"><h3 title="${esc(account.name)}">${esc(account.name)}</h3><p>${esc(providers[account.provider].name)}</p></div><span class="status ${kind}">${label}</span><span class="account-updated">${account.last_success_at ? `수집 ${esc(dateTime(account.last_success_at))}` : "수집 기록 없음"}</span><div class="account-actions"><button class="icon-button" data-account-refresh="${esc(account.id)}" aria-label="${esc(account.name)} 사용량 수집" title="이 계정 사용량 수집"${state.collecting || account.enabled === false ? " disabled" : ""}>${icon("refresh")}</button><button class="icon-button" data-account-edit="${esc(account.id)}" aria-label="${esc(account.name)} 수정" title="계정 수정">${icon("edit")}</button><button class="icon-button delete-button" data-account-delete="${esc(account.id)}" aria-label="${esc(account.name)} 삭제" title="연결 삭제">${icon("trash")}</button></div></header>${account.error ? `<p class="account-error">${esc(account.error)}${account.last_success_at ? " 마지막 성공 데이터는 유지됩니다." : ""}</p>` : ""}<details class="account-details" data-account-details="${esc(account.id)}"${(open.has(String(account.id)) ? open.get(String(account.id)) : index === 0) ? " open" : ""}><summary>한도 · 전체 누적 보기</summary><div class="account-data"><section><h4>현재 한도 <span>기간별 사용량 아님</span></h4>${renderQuota(account.quota)}</section><section><h4>전체 누적 <span>기간 필터와 무관</span></h4>${renderSummary(account.summary)}</section></div>${note ? `<p class="account-note">${esc(note)}</p>` : ""}</details></article>`;
    }).join("");
  }
  function render(data) {
    renderFilters();
    renderMetrics(data.totals || {});
    renderChart(Array.isArray(data.daily) ? data.daily : []);
    renderModels(Array.isArray(data.models) ? data.models : []);
    renderConnections();
    renderAccounts();
    $("#date-caption").textContent = `${data.from && data.from !== "0001-01-01" ? data.from : "전체 기록"}${data.to ? ` ~ ${data.to}` : ""} · KST`;
    $("#last-updated").textContent = data.refreshed_at ? `마지막 수집 성공 ${dateTime(data.refreshed_at)} KST` : "성공한 수집 기록 없음";
    $("#refresh").disabled = state.collecting;
    $("#refresh span").textContent = state.collecting ? "수집 중…" : "사용량 수집";
    const notes = [];
    if (state.collecting) notes.push("최신 사용량 수집 중입니다. 완료 전까지 마지막 성공 데이터를 표시합니다.");
    if (data.totals?.partial || Object.values(data.totals || {}).some((amount) => amount === null)) notes.push("일부 지표는 제공되지 않습니다. — 는 0이 아닌 확인 불가 값입니다. 확인된 항목만 집계될 수 있습니다.");
    notice("data-notice", notes.join(" "));
  }
  function changeView(view) {
    state.view = view === "connections" ? "connections" : "overview";
    const connections = state.view === "connections";
    $("#overview-section").hidden = connections;
    $("#page-title").textContent = connections ? "연결 관리" : "사용량 개요";
    $("#page-description").textContent = connections ? "여러 서비스와 계정을 연결하고, 인증 상태와 수집을 관리하세요." : "기간별 사용량과 계정별 한도를 구분해서 확인하세요.";
    $$(".viewbar [data-view]").forEach((button) => {
      const active = button.dataset.view === state.view;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    });
    renderAccounts();
  }
  function filterProvider(id) {
    state.provider = allowed(id) ? id : "all";
    state.account = "all";
    renderFilters();
    if (state.view === "connections") renderAccounts();
    loadDashboard();
  }
  async function collect(id) {
    if (!state.authenticated || state.collecting) return;
    if (!linkedAccounts().some((account) => account.enabled !== false)) return toast("수집할 계정을 먼저 연결하고 활성화하세요.");
    state.collecting = true;
    $("#refresh").disabled = true;
    $("#refresh span").textContent = "수집 요청 중…";
    try {
      await api("/refresh", { method: "POST", body: id ? { account_id: id } : {} });
      if (!state.authenticated) return;
      toast("사용량 수집을 요청했습니다. 계정별 상태를 확인하세요.");
      await loadDashboard();
    } catch (error) {
      state.collecting = false;
      if (state.authenticated) { notice("global-error", error.message); if (state.data) render(state.data); }
    }
  }

  function openPicker() {
    if (!state.authenticated) return;
    $("#provider-picker").innerHTML = Object.entries(providers).map(([id, provider]) => `<button data-connect-provider="${id}">${mark(id)}<span><strong>${esc(provider.name)}</strong><small>${esc(provider.subtitle)}</small></span><span class="picker-plus" aria-hidden="true">+</span></button>`).join("");
    openDialog("provider-dialog");
  }
  function extraFields(id) {
    const fields = state.providers.find((provider) => provider.id === id)?.fields || [];
    const list = Array.isArray(fields) ? fields.map((field) => typeof field === "string" ? { name: field, label: field } : field) : Object.entries(fields).map(([name, field]) => typeof field === "object" && field ? { name, ...field } : { name, label: String(field) });
    return list.filter((field) => field && (field.name || field.key)).map((field) => ({ ...field, name: field.name || field.key })).filter((field) => !["name", "provider", "base_url", "credential", "api_key", "token", "extra"].includes(field.name));
  }
  function openAccount(id, providerId) {
    const account = id ? state.accounts.find((item) => String(item.id) === String(id)) : null;
    if (id && !account) return;
    const provider = account?.provider || providerId;
    if (!allowed(provider)) return;
    if (!account && provider === "chatgpt") return openOAuth();
    state.editing = account;
    $("#account-form").reset();
    $("#account-title").textContent = account ? "계정 설정" : `${providers[provider].name} 연결`;
    $("#account-description").textContent = provider === "chatgpt" ? "브라우저 OAuth로 연결된 계정입니다. 이름과 수집 활성화 상태만 변경할 수 있습니다." : providers[provider].help;
    $("#account-provider").innerHTML = `<option value="${provider}">${esc(providers[provider].name)}</option>`;
    $("#account-name").value = account?.name || "";
    $("#account-url").value = account?.base_url || state.providers.find((item) => item.id === provider)?.default_url || "";
    $("#credential-label").textContent = providers[provider].credential || "인증 키";
    $("#account-credential").placeholder = account ? "비워두면 기존 키 유지" : "인증 정보를 입력하세요";
    $("#account-credential").required = !account && provider !== "usage-json" && provider !== "chatgpt";
    $("#account-url").required = provider !== "chatgpt";
    $("#connection-fields").hidden = provider === "chatgpt";
    $$("#connection-fields input, #connection-fields textarea").forEach((field) => { field.disabled = provider === "chatgpt"; });
    $("#account-enabled-wrap").hidden = !account;
    $("#account-enabled").checked = account?.enabled !== false;
    $("#advanced-options").open = false;
    $("#account-extra").value = "";
    $("#provider-extra-fields").innerHTML = provider === "chatgpt" ? "" : extraFields(provider).map((field, index) => {
      const secret = field.secret || /password|key|token|secret|credential/i.test(field.name);
      return `<label for="extra-${index}">${esc(field.label || field.name)}${field.required && !account ? "" : ' <span class="muted">(선택)</span>'}</label><input id="extra-${index}" data-extra-key="${esc(field.name)}" data-number="${field.type === "number"}" type="${secret ? "password" : field.type === "number" ? "number" : "text"}" autocomplete="off"${field.required && !account ? " required" : ""} placeholder="${esc(field.placeholder || (account ? "비워두면 기존 추가 설정 유지" : ""))}" />${field.help ? `<p class="field-help">${esc(field.help)}</p>` : ""}`;
    }).join("");
    notice("account-error");
    $("#save-account").disabled = false;
    $("#save-account").textContent = account ? "변경 사항 저장" : "계정 연결";
    openDialog("account-dialog");
    $("#account-name").focus();
  }
  async function saveAccount(event) {
    event.preventDefault();
    const editing = state.editing;
    const session = state.sessionGeneration;
    const limited = editing?.provider === "chatgpt";
    const payload = { name: $("#account-name").value.trim() };
    notice("account-error");
    try {
      if (!payload.name) throw new Error("계정 이름을 입력하세요.");
      if (!limited) {
        payload.provider = $("#account-provider").value;
        payload.base_url = $("#account-url").value.trim();
        payload.credential = $("#account-credential").value;
        const url = new URL(payload.base_url);
        if (url.protocol !== "https:" || url.username || url.password) throw new Error("인증 정보가 포함되지 않은 HTTPS 서버 주소를 입력하세요.");
        const raw = $("#account-extra").value.trim();
        let extra;
        if (raw) {
          try { extra = JSON.parse(raw); } catch { throw new Error("추가 설정에 올바른 JSON 객체를 입력하세요."); }
          if (!extra || Array.isArray(extra) || typeof extra !== "object") throw new Error("추가 설정은 JSON 객체여야 합니다.");
        }
        $$("#provider-extra-fields input").forEach((field) => {
          if (!field.value.trim()) return;
          if (!extra) extra = {};
          const amount = field.dataset.number === "true" ? Number(field.value) : field.value;
          if (typeof amount === "number" && !finite(amount)) throw new Error("숫자 설정값을 확인하세요.");
          Object.defineProperty(extra, field.dataset.extraKey, { value: amount, enumerable: true, configurable: true });
        });
        if (extra !== undefined || !editing) payload.extra = extra || {};
      }
      if (editing) payload.enabled = $("#account-enabled").checked;
      $("#save-account").disabled = true;
      await api(editing ? `/accounts/${encodeURIComponent(editing.id)}` : "/accounts", { method: editing ? "PATCH" : "POST", body: payload });
      if (!state.authenticated || session !== state.sessionGeneration) return;
      $("#account-dialog").close();
      toast(editing ? "계정 설정을 저장했습니다." : "계정을 연결했습니다.");
      await loadDashboard();
    } catch (error) { if (state.authenticated && session === state.sessionGeneration) notice("account-error", error instanceof TypeError ? "HTTPS 서버 주소를 확인하세요." : error.message); }
    finally { $("#save-account").disabled = false; }
  }

  function authorizationURL(input) {
    try {
      const url = new URL(input);
      if (url.protocol === "https:" && url.hostname === "auth.openai.com" && !url.port && !url.username && !url.password && url.pathname === "/oauth/authorize" && !url.hash) return url.href;
    } catch { /* Invalid or untrusted links must never open. */ }
    return null;
  }
  function activeOAuth(login) { return state.oauth === login && state.authenticated && $("#chatgpt-dialog").open; }
  function clearOAuthFields() {
    $("#authorization-link").removeAttribute("href");
    $("#callback-url").value = "";
    $("#chatgpt-authorization").hidden = true;
    $("#chatgpt-expiry").textContent = "";
  }
  async function stopOAuth(cancel = true) {
    const login = state.oauth;
    state.oauth = null;
    if (login) clearTimeout(login.timer);
    clearOAuthFields();
    if (cancel && login?.id && login.status !== "connected" && state.authenticated) {
      try { await api(`/chatgpt/login/${encodeURIComponent(login.id)}`, { method: "DELETE" }); }
      catch { if (state.authenticated) toast("연결 취소를 확인하지 못했습니다. 화면의 인증 정보는 지웠습니다. 서버의 요청은 유효 시간 후 만료됩니다."); }
    }
  }
  function openOAuth() {
    if (!state.authenticated) return;
    $("#chatgpt-form").reset();
    $("#chatgpt-form").hidden = false;
    $("#start-chatgpt-login").disabled = false;
    $("#retry-chatgpt-login").hidden = true;
    $("#chatgpt-status").textContent = "";
    clearOAuthFields();
    notice("chatgpt-error");
    openDialog("chatgpt-dialog");
    $("#chatgpt-name").focus();
  }
  function failOAuth(login, message) {
    if (!activeOAuth(login)) return;
    clearTimeout(login.timer);
    login.status = "error";
    login.url = null;
    clearOAuthFields();
    $("#chatgpt-status").textContent = "";
    $("#retry-chatgpt-login").hidden = false;
    notice("chatgpt-error", message);
  }
  function scheduleOAuth(login) {
    clearTimeout(login.timer);
    if (!activeOAuth(login) || !["awaiting_authorization", "pending", "completing", "authorizing"].includes(login.status)) return;
    login.timer = setTimeout(async () => {
      if (!activeOAuth(login)) return;
      if (finite(login.expires) && login.expires <= Date.now()) return failOAuth(login, "연결 요청이 만료되었습니다. 다시 시작하세요.");
      try { await updateOAuth(login, await api(`/chatgpt/login/${encodeURIComponent(login.id)}`)); }
      catch { failOAuth(login, "연결 상태를 조회하지 못했습니다. 다시 시작하세요."); }
    }, 2500);
  }
  async function updateOAuth(login, result) {
    if (!activeOAuth(login)) return;
    if (result?.status === "connected") {
      login.status = "connected";
      clearTimeout(login.timer);
      clearOAuthFields();
      $("#chatgpt-status").textContent = "계정이 연결되었습니다.";
      await loadDashboard();
      if (activeOAuth(login)) { $("#chatgpt-dialog").close(); toast("ChatGPT 계정을 연결했습니다."); }
      return;
    }
    if (["error", "failed", "expired", "cancelled"].includes(result?.status)) return failOAuth(login, result.status === "expired" ? "연결 요청이 만료되었습니다. 다시 시작하세요." : "연결을 완료하지 못했습니다. 다시 시작하세요.");
    if (result?.status === "authorizing") {
      login.status = "authorizing";
      clearOAuthFields();
      $("#chatgpt-status").textContent = "인증을 확인하는 중입니다. 연결 완료를 기다려 주세요.";
      scheduleOAuth(login);
      return;
    }
    if (!["awaiting_authorization", "pending", "completing"].includes(result?.status)) return failOAuth(login, "연결 서버의 응답을 확인할 수 없습니다. 다시 시작하세요.");
    login.status = result.status;
    if (result.authorization_url !== undefined) {
      login.url = authorizationURL(result.authorization_url);
      if (!login.url) return failOAuth(login, "신뢰할 수 있는 OpenAI 인증 주소가 아니어서 연결을 중단했습니다.");
    }
    if (!login.url) return failOAuth(login, "OpenAI 인증 주소를 받지 못했습니다. 다시 시작하세요.");
    if (result.expires_at) login.expires = new Date(result.expires_at).getTime();
    if (finite(login.expires) && login.expires <= Date.now()) return failOAuth(login, "연결 요청이 만료되었습니다. 다시 시작하세요.");
    $("#authorization-link").href = login.url;
    $("#chatgpt-form").hidden = true;
    $("#chatgpt-authorization").hidden = false;
    $("#chatgpt-expiry").textContent = finite(login.expires) ? `${dateTime(result.expires_at || login.expires)} KST 만료 · 이 창을 닫으면 연결 요청을 취소합니다.` : "이 창을 닫으면 연결 요청을 취소합니다.";
    $("#chatgpt-status").textContent = "브라우저 인증 대기 중 · 연결 상태만 자동 확인합니다.";
    scheduleOAuth(login);
  }
  async function startOAuth(event) {
    event?.preventDefault();
    if (!state.authenticated || !$("#chatgpt-dialog").open || ["starting", "awaiting_authorization", "pending", "completing", "authorizing"].includes(state.oauth?.status)) return;
    await stopOAuth();
    if (!state.authenticated || !$("#chatgpt-dialog").open) return;
    const login = { status: "starting", id: null, timer: null, url: null, session: state.sessionGeneration };
    state.oauth = login;
    $("#start-chatgpt-login").disabled = true;
    $("#retry-chatgpt-login").hidden = true;
    notice("chatgpt-error");
    $("#chatgpt-status").textContent = "브라우저 인증 요청을 준비하는 중…";
    try {
      const result = await api("/chatgpt/login", { method: "POST", body: { name: $("#chatgpt-name").value.trim() || "ChatGPT 계정" } });
      if (!activeOAuth(login)) {
        if (result?.id && result.status !== "connected" && state.authenticated && login.session === state.sessionGeneration) await api(`/chatgpt/login/${encodeURIComponent(result.id)}`, { method: "DELETE" });
        return;
      }
      if (typeof result.id !== "string" || !result.id) return failOAuth(login, "연결 요청 ID가 없습니다. 다시 시작하세요.");
      login.id = result.id;
      await updateOAuth(login, result);
    } catch { failOAuth(login, "연결 요청을 시작하지 못했습니다. 잠시 후 다시 시도하세요."); }
  }
  async function completeOAuth(event) {
    event.preventDefault();
    const login = state.oauth;
    if (!login || !activeOAuth(login) || !["awaiting_authorization", "pending"].includes(login.status)) return;
    notice("chatgpt-error");
    let callback;
    try {
      const url = new URL($("#callback-url").value.trim());
      if (url.protocol !== "http:" || url.hostname !== "localhost" || url.port !== "1455" || url.pathname !== "/auth/callback" || url.username || url.password || url.hash || url.searchParams.getAll("code").length !== 1 || !url.searchParams.get("code") || url.searchParams.getAll("state").length !== 1 || !url.searchParams.get("state")) throw new Error();
      callback = url.href;
    } catch { return notice("chatgpt-error", "http://localhost:1455/auth/callback으로 시작하고 code와 state가 포함된 주소 전체를 붙여넣으세요."); }
    clearTimeout(login.timer);
    login.status = "completing";
    $("#complete-chatgpt-login").disabled = true;
    $("#callback-url").value = "";
    try {
      const result = await api(`/chatgpt/login/${encodeURIComponent(login.id)}/complete`, { method: "POST", body: { callback_url: callback } });
      callback = null;
      await updateOAuth(login, result);
    } catch (error) {
      if (activeOAuth(login)) { login.status = "awaiting_authorization"; notice("chatgpt-error", error.message); scheduleOAuth(login); }
    } finally { callback = null; $("#complete-chatgpt-login").disabled = false; }
  }

  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    notice("login-error");
    $("#login-submit").disabled = true;
    try {
      if (state.setup) {
        if ($("#password").value !== $("#password-confirm").value) throw new Error("두 비밀번호가 일치하지 않습니다.");
        await api("/setup", { method: "POST", body: { password: $("#password").value, token: state.setupToken || $("#setup-token").value.trim() } });
        const session = await api("/session");
        if (!session.authenticated) await api("/login", { method: "POST", body: { password: $("#password").value } });
        state.setupToken = "";
        $("#setup-token").value = "";
      } else await api("/login", { method: "POST", body: { password: $("#password").value } });
      $("#password").value = "";
      $("#password-confirm").value = "";
      await enterDashboard();
    } catch (error) { notice("login-error", error.message); }
    finally { $("#login-submit").disabled = false; }
  });
  $("#logout").addEventListener("click", async () => {
    $("#logout").disabled = true;
    await stopOAuth();
    try { await api("/logout", { method: "POST", body: {} }); showLogin(); }
    catch (error) { if (state.authenticated) notice("global-error", error.message); }
    finally { $("#logout").disabled = false; }
  });
  $("#add-account").addEventListener("click", openPicker);
  $("#refresh").addEventListener("click", () => collect());
  $("#account-form").addEventListener("submit", saveAccount);
  $("#account-dialog").addEventListener("close", () => { $("#account-credential").value = ""; $("#account-extra").value = ""; $("#provider-extra-fields").replaceChildren(); });
  $("#chatgpt-form").addEventListener("submit", startOAuth);
  $("#callback-form").addEventListener("submit", completeOAuth);
  $("#retry-chatgpt-login").addEventListener("click", startOAuth);
  $("#chatgpt-dialog").addEventListener("close", () => stopOAuth());
  $("#chatgpt-dialog").addEventListener("cancel", () => stopOAuth());
  $$("dialog").forEach((dialog) => dialog.addEventListener("close", () => {
    if (!state.authenticated || $("dialog[open]")) return;
    const opener = dialogOpeners.get(dialog);
    (opener?.isConnected && opener.getClientRects().length ? opener : $("#add-account")).focus();
  }));
  $("#authorization-link").addEventListener("click", (event) => {
    if (!state.oauth || !activeOAuth(state.oauth) || !authorizationURL(event.currentTarget.getAttribute("href")) || event.currentTarget.href !== state.oauth.url) event.preventDefault();
  });
  $("#provider-filter").addEventListener("change", (event) => filterProvider(event.target.value));
  $("#account-filter").addEventListener("change", (event) => { state.account = event.target.value; loadDashboard(); });
  $("#confirm-delete").addEventListener("click", async () => {
    const account = state.deleting;
    if (!account) return;
    $("#confirm-delete").disabled = true;
    const session = state.sessionGeneration;
    try {
      await api(`/accounts/${encodeURIComponent(account.id)}`, { method: "DELETE" });
      if (!state.authenticated || session !== state.sessionGeneration) return;
      $("#delete-dialog").close();
      if (state.account === String(account.id)) state.account = "all";
      toast("계정 연결과 이 서버의 사용량 기록을 삭제했습니다.");
      await loadDashboard();
    } catch (error) { if (state.authenticated) notice("delete-error", error.message); }
    finally { $("#confirm-delete").disabled = false; }
  });
  $("#export").addEventListener("click", async () => {
    $("#export").disabled = true;
    const session = state.sessionGeneration;
    try {
      const blob = await api(`/export?${query()}`, { blob: true });
      if (!state.authenticated || session !== state.sessionGeneration) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `use-${state.range}-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { if (state.authenticated) notice("global-error", error.message); }
    finally { $("#export").disabled = false; }
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button || button.disabled) return;
    if (button.dataset.close) $(`#${button.dataset.close}`).close();
    else if (button.dataset.connectProvider) { $("#provider-dialog").close(); openAccount(null, button.dataset.connectProvider); }
    else if (button.dataset.action === "connect") openPicker();
    else if (button.dataset.action === "reset") { state.account = "all"; filterProvider("all"); }
    else if (button.dataset.service) filterProvider(button.dataset.service === "claude" ? "claude-lb" : button.dataset.service);
    else if (button.dataset.providerFilter) filterProvider(button.dataset.providerFilter);
    else if (button.dataset.view) changeView(button.dataset.view);
    else if (button.dataset.range) {
      state.range = button.dataset.range;
      $$("[data-range]").forEach((item) => { item.classList.toggle("selected", item === button); item.setAttribute("aria-pressed", String(item === button)); });
      loadDashboard();
    } else if (button.dataset.metric) {
      state.metric = button.dataset.metric;
      $$("[data-metric]").forEach((item) => { item.classList.toggle("selected", item === button); item.setAttribute("aria-pressed", String(item === button)); });
      renderChart(state.data?.daily || []);
    } else if (button.dataset.accountEdit) openAccount(button.dataset.accountEdit);
    else if (button.dataset.accountRefresh) collect(button.dataset.accountRefresh);
    else if (button.dataset.accountDelete) {
      state.deleting = state.accounts.find((account) => String(account.id) === button.dataset.accountDelete);
      if (!state.deleting) return;
      $("#delete-description").textContent = `${state.deleting.name} (${providers[state.deleting.provider].name}) 연결을 삭제합니다.`;
      notice("delete-error");
      $("#confirm-delete").disabled = false;
      openDialog("delete-dialog");
    }
    if (button.dataset.chartDetail) $("#chart-detail").textContent = button.dataset.chartDetail;
  });
  ["focusin", "pointerover"].forEach((name) => document.addEventListener(name, (event) => {
    const bar = event.target.closest("[data-chart-detail]");
    if (bar) $("#chart-detail").textContent = bar.dataset.chartDetail;
  }));
  setInterval(() => { if (state.authenticated && $("#auto-refresh").checked && !document.hidden && !state.collecting) loadDashboard(); }, 60000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.authenticated && $("#auto-refresh").checked) loadDashboard(); });
  async function start() {
    state.setupToken = new URLSearchParams(location.hash.slice(1)).get("setup") || "";
    if (state.setupToken) history.replaceState(null, "", `${location.pathname}${location.search}`);
    try {
      const session = await api("/session");
      state.setup = Boolean(session.setup_required);
      if (session.authenticated) await enterDashboard(); else showLogin();
    } catch (error) { showLogin(error.message); }
  }
  start();
})();
