"use strict";

const ui = Object.fromEntries(
  ["characters", "character-count", "character-name", "persona", "messages", "message",
    "send", "reload", "status", "mode", "counter", "chat-form", "sessions", "session-count",
    "new-session", "delete-session", "session-info", "boundary", "trace-select", "trace-reload",
    "trace-summary", "trace-content"]
    .map((id) => [id, document.getElementById(id)])
);
const storageKey = "ai-character-chat:last-session";
let characters = [];
let selected = null;
let session = null;
let sessions = [];
let busy = false;
let sending = false;
let loadVersion = 0;
let traceVersion = 0;
let pendingSend = null;
let latestAttempt = null;

function status(text, isError = false) {
  ui.status.textContent = text;
  ui.status.classList.toggle("error", isError);
}

function storedSession() {
  try { return localStorage.getItem(storageKey); } catch { return null; }
}

function rememberSession(id) {
  try {
    if (id) localStorage.setItem(storageKey, id);
    else localStorage.removeItem(storageKey);
  } catch { /* Server persistence does not depend on browser storage. */ }
}

function showMode(mode) {
  const mock = mode === "mock";
  ui.mode.textContent = mock ? "离线模拟模式" : `模型模式：${mode}`;
  ui.boundary.textContent = mock
    ? "当前为离线模拟回显，未调用真实模型。会话保存在本机，成功问答会进入后续上下文；这不是角色长期记忆。"
    : "当前回复由配置的模型生成。会话保存在本机，成功问答会进入后续上下文；这不是角色长期记忆。";
}

function updateControls() {
  const length = Array.from(ui.message.value.trim()).length;
  ui.counter.textContent = `${Array.from(ui.message.value).length} / 2000`;
  ui.send.disabled = busy || !session || length === 0 || length > 2000;
  ui.message.disabled = busy || !session;
  ui["new-session"].disabled = busy || !selected;
  ui["delete-session"].disabled = busy || !session;
  ui.reload.disabled = busy;
  ui["trace-select"].disabled = busy || !session || !ui["trace-select"].value;
  ui["trace-reload"].disabled = busy || !session || !ui["trace-select"].value;
  for (const button of ui.characters.querySelectorAll("button")) button.disabled = busy;
  for (const button of ui.sessions.querySelectorAll("button")) button.disabled = busy;
  ui.send.firstChild.textContent = sending ? "发送中 " : "发送 ";
}

async function requestJSON(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    if (!response.ok) {
      let detail = null;
      try { detail = (await response.json()).detail; } catch { /* Use a safe local fallback. */ }
      const message = typeof detail?.message === "string" ? detail.message
        : response.status === 404 ? "会话或角色不存在，请重新加载。"
        : response.status === 409 ? "此会话正在处理请求，或请求标识发生冲突，请稍后重试。"
        : response.status === 422 ? "输入无效，请检查消息长度和请求内容。"
        : `服务返回错误（${response.status}），请稍后重试。`;
      const error = new Error(message);
      error.httpStatus = response.status;
      error.code = typeof detail?.code === "string" ? detail.code : null;
      throw error;
    }
    return response.status === 204 ? null : await response.json();
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时。输入已保留，发送重试会使用同一请求标识。");
    if (error instanceof TypeError) throw new Error("无法连接服务，请确认后端仍在运行后重试。");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function jsonOptions(method, body) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

function sessionURL(id) {
  return `/api/sessions/${encodeURIComponent(id)}`;
}

function formattedDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function renderCharacters() {
  ui.characters.replaceChildren();
  ui["character-count"].textContent = String(characters.length);
  for (const card of characters) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "character-button";
    button.disabled = busy;
    button.setAttribute("aria-pressed", String(selected?.id === card.id));
    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = Array.from(card.name)[0];
    const title = document.createElement("span");
    title.textContent = card.name;
    const detail = document.createElement("small");
    detail.textContent = "原创角色 · 选择会话";
    title.append(detail);
    button.append(avatar, title);
    button.addEventListener("click", () => { if (!busy) selectCharacter(card); });
    ui.characters.append(button);
  }
}

function renderSessions() {
  ui.sessions.replaceChildren();
  ui["session-count"].textContent = String(sessions.length);
  if (!sessions.length) {
    const empty = document.createElement("p");
    empty.className = "empty-note";
    empty.textContent = "还没有会话。点击上方按钮开始。";
    ui.sessions.append(empty);
  }
  for (const [index, item] of sessions.entries()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "session-button";
    button.disabled = busy;
    button.setAttribute("aria-pressed", String(session?.id === item.id));
    button.title = `会话 ${item.id}`;
    const label = document.createElement("span");
    label.textContent = `会话 ${String(index + 1).padStart(2, "0")}`;
    const date = document.createElement("small");
    date.textContent = formattedDate(item.updated_at);
    button.append(label, date);
    button.addEventListener("click", () => { if (!busy) selectSession(item.id); });
    ui.sessions.append(button);
  }
}

function renderMessages() {
  ui.messages.replaceChildren();
  if (!session) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = selected ? "新建一段会话，或从左侧恢复已保存的对话。" : "角色加载后，可以创建或恢复会话。";
    ui.messages.append(empty);
  }
  for (const item of session?.messages || []) {
    const entry = document.createElement("article");
    entry.className = item.role === "user" ? "message user" : "message assistant";
    const label = document.createElement("span");
    label.className = "message-label";
    label.textContent = item.role === "user" ? "你" : session.character_name;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    // All server data is displayed as text, including model output and trace contents.
    bubble.textContent = item.content;
    entry.append(label, bubble);
    ui.messages.append(entry);
  }
  ui.messages.scrollTop = ui.messages.scrollHeight;
  ui["session-info"].textContent = session
    ? `已保存 · ${formattedDate(session.updated_at)} · ${session.id.slice(0, 8)}`
    : "尚未选择会话";
}

function resetTrace() {
  ++traceVersion;
  ui["trace-summary"].textContent = "发送消息后，可检查实际角色设定与对话上下文。";
  ui["trace-content"].textContent = "";
}

function renderTraceChoices(preferred = null) {
  const ids = [...new Set((session?.messages || []).map((item) => item.request_id).filter(Boolean))];
  if (session && latestAttempt && latestAttempt.sessionId === session.id && !ids.includes(latestAttempt.requestId)) {
    ids.push(latestAttempt.requestId);
  }
  const previous = preferred || ui["trace-select"].value;
  ui["trace-select"].replaceChildren();
  if (!ids.length) {
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "暂无调用记录";
    ui["trace-select"].append(empty);
  }
  for (const [index, id] of ids.entries()) {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = `调用 ${index + 1} · ${id.slice(0, 8)}`;
    ui["trace-select"].append(option);
  }
  ui["trace-select"].value = ids.includes(previous) ? previous : ids.at(-1) || "";
}

async function loadTrace(requestId = ui["trace-select"].value) {
  if (!session || !requestId) return;
  const id = session.id;
  const version = ++traceVersion;
  ui["trace-summary"].textContent = "正在加载调用追踪…";
  ui["trace-content"].textContent = "";
  try {
    const trace = await requestJSON(`${sessionURL(id)}/traces/${encodeURIComponent(requestId)}`);
    if (version !== traceVersion || session?.id !== id) return;
    const context = trace.context || {};
    ui["trace-summary"].textContent = [
      trace.status === "success" ? "调用成功" : "调用失败",
      `模式：${trace.mode}`, `模型：${trace.model || "—"}`,
      `耗时：${trace.elapsed_ms ?? "—"} ms`,
      `历史：${context.included_turns ?? "—"} / ${context.available_turns ?? "—"} 轮`,
      context.truncated ? "历史已截断" : "历史未截断",
      `输入：${context.characters ?? "—"} 字符`,
      `错误类别：${trace.error_code || "无"}`
    ].join(" · ");
    ui["trace-content"].textContent = JSON.stringify({
      request_id: trace.request_id, created_at: trace.created_at, usage: trace.usage,
      messages: trace.messages
    }, null, 2);
  } catch (error) {
    if (version !== traceVersion || session?.id !== id) return;
    ui["trace-summary"].textContent = error.httpStatus === 404
      ? "该请求尚无追踪记录；请求可能仍在处理，可稍后刷新。" : `追踪加载失败：${error.message}`;
  }
}

function applySession(data) {
  if (data.character_id !== selected?.id) throw new Error("会话与所选角色不一致，请重新加载。");
  session = data;
  rememberSession(data.id);
  renderSessions();
  renderMessages();
  renderTraceChoices();
}

function clearSelection() {
  session = null;
  pendingSend = null;
  latestAttempt = null;
  ui.message.value = "";
  resetTrace();
  renderMessages();
  renderTraceChoices();
}

async function fetchSessions(characterId) {
  const data = await requestJSON(`/api/sessions?character_id=${encodeURIComponent(characterId)}`);
  if (!Array.isArray(data)) throw new Error("会话列表格式不完整，请重新加载。");
  return data;
}

async function selectCharacter(card, preferredSession = null) {
  const version = ++loadVersion;
  busy = true;
  selected = card;
  sessions = [];
  clearSelection();
  renderCharacters();
  renderSessions();
  ui["character-name"].textContent = card.name;
  ui.persona.textContent = card.persona;
  updateControls();
  status("正在加载保存的会话…");
  try {
    const data = await fetchSessions(card.id);
    if (version !== loadVersion) return;
    sessions = data;
    renderSessions();
    const id = data.find((item) => item.id === preferredSession)?.id || data[0]?.id;
    if (id) {
      const restored = await requestJSON(sessionURL(id));
      if (version !== loadVersion) return;
      applySession(restored);
      resetTrace();
      if (ui["trace-select"].value) await loadTrace();
      status("会话已恢复。开场白来自创建会话时的角色配置。");
    } else {
      rememberSession(null);
      status("点击“新建会话”开始。每段会话使用创建时的角色设定。");
    }
  } catch (error) {
    if (version !== loadVersion) return;
    if (error.httpStatus === 404) rememberSession(null);
    status(error.message, true);
  } finally {
    if (version === loadVersion) { busy = false; updateControls(); }
  }
}

async function selectSession(id) {
  const version = ++loadVersion;
  busy = true;
  clearSelection();
  updateControls();
  status("正在恢复会话…");
  try {
    const data = await requestJSON(sessionURL(id));
    if (version !== loadVersion) return;
    applySession(data);
    if (ui["trace-select"].value) await loadTrace();
    status("会话已恢复。");
  } catch (error) {
    if (version !== loadVersion) return;
    if (error.httpStatus === 404) {
      rememberSession(null);
      sessions = sessions.filter((item) => item.id !== id);
      renderSessions();
    }
    status(error.message, true);
  } finally {
    if (version === loadVersion) { busy = false; updateControls(); }
  }
}

async function loadCharacters() {
  const version = ++loadVersion;
  const previousCharacter = selected?.id;
  const lastSession = storedSession();
  busy = true;
  updateControls();
  status("正在加载角色和服务状态…");
  try {
    const [cards, health] = await Promise.all([requestJSON("/api/characters"), requestJSON("/health")]);
    if (version !== loadVersion) return;
    if (!Array.isArray(cards) || cards.length === 0) throw new Error("暂无可用角色，请检查角色配置。");
    characters = cards;
    showMode(health.mode);
    let restoreCharacter = null;
    if (lastSession) {
      try {
        const saved = await requestJSON(sessionURL(lastSession));
        restoreCharacter = cards.find((card) => card.id === saved.character_id);
        if (!restoreCharacter) rememberSession(null);
      } catch (error) {
        if (error.httpStatus === 404) rememberSession(null);
        else throw error;
      }
    }
    if (version !== loadVersion) return;
    await selectCharacter(restoreCharacter || cards.find((card) => card.id === previousCharacter) || cards[0], lastSession);
  } catch (error) {
    if (version !== loadVersion) return;
    selected = null;
    characters = [];
    sessions = [];
    clearSelection();
    renderCharacters();
    renderSessions();
    ui.mode.textContent = "服务连接异常";
    ui.boundary.textContent = "服务暂不可用。已保存的对话仍由后端管理，请恢复连接后重新加载。";
    status(error.message, true);
  } finally {
    if (version === loadVersion) { busy = false; updateControls(); }
  }
}

ui.message.addEventListener("input", () => {
  if (pendingSend && pendingSend.message !== ui.message.value.trim()) pendingSend = null;
  updateControls();
});
ui.reload.addEventListener("click", () => { if (!busy) loadCharacters(); });
ui["trace-select"].addEventListener("change", () => { if (!busy) loadTrace(); });
ui["trace-reload"].addEventListener("click", () => { if (!busy) loadTrace(); });

ui["new-session"].addEventListener("click", async () => {
  if (busy || !selected) return;
  busy = true;
  updateControls();
  status("正在创建会话…");
  try {
    const data = await requestJSON("/api/sessions", jsonOptions("POST", { character_id: selected.id }));
    clearSelection();
    sessions = [data, ...sessions];
    applySession(data);
    status("会话已创建。开场白来自角色配置，尚未调用模型。");
  } catch (error) { status(error.message, true); }
  finally { busy = false; updateControls(); if (session) ui.message.focus(); }
});

ui["delete-session"].addEventListener("click", async () => {
  if (busy || !session || !window.confirm("删除当前会话、所有消息和关联调用追踪？删除后无法恢复。")) return;
  const id = session.id;
  busy = true;
  updateControls();
  status("正在删除会话…");
  try {
    await requestJSON(sessionURL(id), { method: "DELETE" });
    sessions = sessions.filter((item) => item.id !== id);
    clearSelection();
    rememberSession(null);
    renderSessions();
    if (sessions.length) {
      applySession(await requestJSON(sessionURL(sessions[0].id)));
      if (ui["trace-select"].value) await loadTrace();
    }
    status("会话及关联调用追踪已删除。");
  } catch (error) { status(error.message, true); }
  finally { busy = false; updateControls(); }
});

ui["chat-form"].addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = ui.message.value.trim();
  if (busy || !session || !message || Array.from(message).length > 2000) return;
  const id = session.id;
  if (!pendingSend || pendingSend.sessionId !== id || pendingSend.message !== message) {
    pendingSend = { sessionId: id, message, requestId: crypto.randomUUID() };
  }
  const attempt = { ...pendingSend };
  latestAttempt = attempt;
  sending = true;
  busy = true;
  updateControls();
  status("等待服务回复…成功后问答才会保存。");
  let completed = false;
  try {
    const result = await requestJSON(`${sessionURL(id)}/messages`, jsonOptions("POST", {
      message, request_id: attempt.requestId
    }));
    if (typeof result.reply !== "string") throw new Error("服务返回的数据不完整，请重试。");
    completed = true;
    showMode(result.mode);
    const [restored, list] = await Promise.all([requestJSON(sessionURL(id)), fetchSessions(selected.id)]);
    sessions = list;
    applySession(restored);
    ui.message.value = "";
    pendingSend = null;
    status(result.mode === "mock" ? "收到并保存离线模拟回显。" : "已收到并保存模型回复。");
  } catch (error) {
    status(completed ? `回复已保存，但记录加载失败。输入已保留，可重试恢复。${error.message}` : error.message, true);
    if (error.httpStatus === 404) {
      rememberSession(null);
      sessions = sessions.filter((item) => item.id !== id);
      session = null;
      renderSessions();
      renderMessages();
    }
  } finally {
    renderTraceChoices(attempt.requestId);
    if (session?.id === id) await loadTrace(attempt.requestId);
    sending = false;
    busy = false;
    updateControls();
    if (session) ui.message.focus();
  }
});

loadCharacters();
