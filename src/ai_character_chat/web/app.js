"use strict";

const ui = Object.fromEntries(
  ["characters", "character-count", "character-name", "persona", "messages", "message",
    "send", "clear", "reload", "status", "mode", "counter", "chat-form"]
    .map((id) => [id, document.getElementById(id)])
);
const conversations = new Map();
let characters = [];
let selected = null;
let busy = false;

function status(text, isError = false) {
  ui.status.textContent = text;
  ui.status.classList.toggle("error", isError);
}

function updateControls() {
  const length = Array.from(ui.message.value.trim()).length;
  ui.counter.textContent = `${Array.from(ui.message.value).length} / 2000`;
  ui.send.disabled = busy || !selected || length === 0 || length > 2000;
  ui.message.disabled = busy || !selected;
  ui.clear.disabled = busy || !selected;
  ui.reload.disabled = busy;
  for (const button of ui.characters.querySelectorAll("button")) button.disabled = busy;
  ui.send.firstChild.textContent = busy ? "发送中 " : "发送 ";
}

async function requestJSON(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    if (!response.ok) {
      if (response.status === 404) throw new Error("角色不存在，请重新加载角色。");
      if (response.status === 422) throw new Error("输入无效，请检查角色和消息长度。");
      throw new Error(`服务返回错误（${response.status}），请稍后重试。`);
    }
    return await response.json();
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时，请检查服务后重试。");
    if (error instanceof TypeError) throw new Error("无法连接服务，请确认后端仍在运行。");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function renderMessages() {
  ui.messages.replaceChildren();
  for (const item of conversations.get(selected.id)) {
    const entry = document.createElement("article");
    entry.className = `message ${item.role}`;
    const label = document.createElement("span");
    label.className = "message-label";
    label.textContent = item.role === "user" ? "你" : selected.name;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    // Render all character and message content as text, never HTML.
    bubble.textContent = item.text;
    entry.append(label, bubble);
    ui.messages.append(entry);
  }
  ui.messages.scrollTop = ui.messages.scrollHeight;
}

function selectCharacter(character) {
  selected = character;
  if (!conversations.has(character.id)) {
    conversations.set(character.id, [{ role: "assistant", text: character.greeting }]);
  }
  ui["character-name"].textContent = character.name;
  ui.persona.textContent = character.persona;
  for (const button of ui.characters.querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.characterId === character.id));
  }
  status("开场白来自角色配置。发送消息后会显示服务实际返回的回复。");
  renderMessages();
  updateControls();
}

async function loadCharacters() {
  busy = true;
  updateControls();
  status("正在加载角色…");
  try {
    const [cards, health] = await Promise.all([requestJSON("/api/characters"), requestJSON("/health")]);
    if (!Array.isArray(cards) || cards.length === 0) throw new Error("暂无可用角色，请检查角色配置。");
    characters = cards;
    ui.mode.textContent = health.mode === "mock" ? "离线模拟模式" : `服务模式：${health.mode}`;
    ui["character-count"].textContent = String(cards.length);
    ui.characters.replaceChildren();
    for (const card of characters) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "character-button";
      button.dataset.characterId = card.id;
      const avatar = document.createElement("span");
      avatar.className = "avatar";
      avatar.setAttribute("aria-hidden", "true");
      avatar.textContent = Array.from(card.name)[0];
      const title = document.createElement("span");
      title.textContent = card.name;
      const detail = document.createElement("small");
      detail.textContent = "原创角色 · 进入对话";
      title.append(detail);
      button.append(avatar, title);
      button.addEventListener("click", () => selectCharacter(card));
      ui.characters.append(button);
    }
    selectCharacter(cards.find((card) => card.id === selected?.id) || cards[0]);
  } catch (error) {
    // Old cards may be stale after a failed reload; disable sending until recovered.
    selected = null;
    characters = [];
    ui.characters.replaceChildren();
    ui["character-count"].textContent = "—";
    ui.mode.textContent = "服务连接异常";
    status(error.message, true);
  } finally {
    busy = false;
    updateControls();
  }
}

ui.message.addEventListener("input", updateControls);
ui.reload.addEventListener("click", loadCharacters);
ui.clear.addEventListener("click", () => {
  conversations.delete(selected.id);
  selectCharacter(selected);
  status("当前角色在本页的对话已清空。");
});
ui["chat-form"].addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = ui.message.value.trim();
  if (busy || !selected || !message || Array.from(message).length > 2000) return;
  const card = selected;
  const history = conversations.get(card.id);
  history.push({ role: "user", text: message });
  renderMessages();
  busy = true;
  updateControls();
  status("等待服务回复…");
  try {
    const result = await requestJSON("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ character_id: card.id, message })
    });
    if (typeof result.reply !== "string") throw new Error("服务返回的数据不完整，请重试。");
    history.push({ role: "assistant", text: result.reply });
    ui.mode.textContent = result.mode === "mock" ? "离线模拟模式" : `回复模式：${result.mode}`;
    ui.message.value = "";
    status(result.mode === "mock" ? "收到离线模拟回复。" : "已收到服务回复。");
  } catch (error) {
    history.pop();
    status(error.message, true);
  } finally {
    busy = false;
    renderMessages();
    updateControls();
    ui.message.focus();
  }
});

loadCharacters();
