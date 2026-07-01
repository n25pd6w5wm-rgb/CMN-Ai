// cmn·ai chat client — streaming chat, persistent conversations, settings.
"use strict";

const $ = (sel) => document.querySelector(sel);
const transcript = $("#transcript");
const composer = $("#composer");
const promptEl = $("#prompt");

const CAP_LABEL = { chat: "chat", code: "code", research: "research", multimodal: "vision" };
let currentConversationId = null;

function eur(n) {
  if (n === 0 || n === null || n === undefined) return "free";
  return "€" + Number(n).toFixed(n < 0.1 ? 4 : 2);
}
function scrollDown() {
  transcript.scrollTop = transcript.scrollHeight;
}
function clearWelcome() {
  const w = transcript.querySelector(".welcome");
  if (w) w.remove();
}

// ---------- message rendering ----------
function addUserMessage(text) {
  clearWelcome();
  const wrap = document.createElement("div");
  wrap.className = "msg msg-user";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrap.appendChild(bubble);
  transcript.appendChild(wrap);
  scrollDown();
}

function addAssistantShell() {
  const wrap = document.createElement("div");
  wrap.className = "msg msg-ai";
  const chip = document.createElement("div");
  chip.className = "chip";
  chip.style.display = "none";
  const bubble = document.createElement("div");
  bubble.className = "bubble cursor";
  bubble.textContent = "";
  wrap.appendChild(chip);
  wrap.appendChild(bubble);
  transcript.appendChild(wrap);
  scrollDown();
  return { wrap, chip, bubble };
}

function renderChip(chip, route) {
  const cap = CAP_LABEL[route.classification.capability] || route.classification.capability;
  if (route.estimated_eur === 0) chip.classList.add("free");
  const parts = [
    `<span class="cap">${cap}</span>`,
    `<span class="sep">→</span>`,
    `<span class="who">${route.agent || "—"}</span>`,
  ];
  if (route.model) parts.push(`<span class="sep">·</span><span>${route.model}</span>`);
  if (route.fell_back) parts.push(`<span class="flag">fallback</span>`);
  chip.innerHTML = parts.join(" ");
  chip.style.display = "inline-flex";
}

function finalizeChip(chip, done) {
  const cost = document.createElement("span");
  cost.className = "cost";
  cost.innerHTML = `<span class="sep">·</span> ${eur(done.cost_eur)}`;
  if (done.cost_eur === 0) chip.classList.add("free");
  chip.appendChild(cost);
}

// render a stored assistant message (history has agent/model/cost, no classification)
function addStoredAssistant(msg) {
  const wrap = document.createElement("div");
  wrap.className = "msg msg-ai";
  if (msg.agent) {
    const chip = document.createElement("div");
    chip.className = "chip" + (msg.cost_eur ? "" : " free");
    const parts = [`<span class="who">${msg.agent}</span>`];
    if (msg.model) parts.push(`<span class="sep">·</span><span>${msg.model}</span>`);
    parts.push(`<span class="cost"><span class="sep">·</span> ${eur(msg.cost_eur)}</span>`);
    chip.innerHTML = parts.join(" ");
    wrap.appendChild(chip);
  }
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = msg.content;
  wrap.appendChild(bubble);
  transcript.appendChild(wrap);
}

function renderBlocked(wrap, bubble, info) {
  bubble.remove();
  const box = document.createElement("div");
  box.className = "blocked";
  box.innerHTML =
    `<h3>Budget reached</h3><p>${info.reason}. The “${info.bucket}” bucket is paused ` +
    `for this rolling week.</p>`;
  const btn = document.createElement("button");
  btn.className = "ghost-btn";
  btn.textContent = "raise budget";
  btn.addEventListener("click", raiseBudget);
  box.appendChild(btn);
  wrap.appendChild(box);
  scrollDown();
}

// ---------- streaming a turn ----------
async function sendMessage(text) {
  addUserMessage(text);
  const { wrap, chip, bubble } = addAssistantShell();

  let resp;
  try {
    resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: text,
        conversation_id: currentConversationId,
        agent: $("#model-select").value || null,
      }),
    });
  } catch (e) {
    bubble.classList.remove("cursor");
    bubble.textContent = "Network error — is the server running?";
    return;
  }

  if (resp.status === 401) {
    window.location.href = "/login";
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop();
    for (const block of events) handleEvent(block, { wrap, chip, bubble });
  }
  bubble.classList.remove("cursor");
  loadBudget();
  loadAnalytics();
  loadConversations();
}

function handleEvent(block, ctx) {
  let event = "message";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7).trim();
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (!data) return;
  const payload = JSON.parse(data);
  if (event === "conversation") {
    currentConversationId = payload.id;
  } else if (event === "route") {
    renderChip(ctx.chip, payload);
  } else if (event === "delta") {
    ctx.bubble.textContent += payload.text;
    scrollDown();
  } else if (event === "blocked") {
    renderBlocked(ctx.wrap, ctx.bubble, payload);
  } else if (event === "error") {
    ctx.bubble.classList.remove("cursor");
    ctx.bubble.classList.add("msg-error");
    ctx.bubble.textContent = payload.message || "Something went wrong.";
  } else if (event === "done" && !payload.blocked) {
    finalizeChip(ctx.chip, payload);
  }
}

// ---------- conversations (chat history) ----------
async function loadConversations() {
  const data = await (await fetch("/api/conversations")).json();
  const host = $("#chats");
  host.innerHTML = "";
  if (!data.conversations.length) {
    host.innerHTML = `<li class="chats-empty">No conversations yet.</li>`;
    return;
  }
  for (const c of data.conversations) {
    const li = document.createElement("li");
    li.className = "chat-item" + (c.id === currentConversationId ? " active" : "");
    const title = document.createElement("button");
    title.className = "chat-title";
    title.textContent = c.title;
    title.title = c.title;
    title.addEventListener("click", () => openConversation(c.id));
    const del = document.createElement("button");
    del.className = "chat-del";
    del.setAttribute("aria-label", "Delete conversation");
    del.innerHTML = "&times;";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteConversation(c.id);
    });
    li.append(title, del);
    host.appendChild(li);
  }
}

async function openConversation(id) {
  const data = await (await fetch(`/api/conversations/${id}`)).json();
  currentConversationId = id;
  transcript.innerHTML = "";
  for (const m of data.messages) {
    if (m.role === "user") addUserMessage(m.content);
    else addStoredAssistant(m);
  }
  scrollDown();
  loadConversations();
}

function newChat() {
  currentConversationId = null;
  transcript.innerHTML =
    `<div class="welcome"><h1>Ask anything.</h1><p>A free local model carries the load. ` +
    `Research, hard code and multimodal tasks are routed to the right paid expert — only ` +
    `when the budget allows. Every answer shows who handled it and what it cost.</p></div>`;
  loadConversations();
  promptEl.focus();
}

async function deleteConversation(id) {
  await fetch(`/api/conversations/${id}`, { method: "DELETE" });
  if (id === currentConversationId) newChat();
  else loadConversations();
}

// ---------- budget + roster + analytics ----------
async function loadBudget() {
  const data = await (await fetch("/api/budget")).json();
  $("#budget-monthly").textContent = `${data.monthly_budget_eur.toFixed(0)} €/mo`;
  const host = $("#buckets");
  host.innerHTML = "";
  for (const b of data.buckets) {
    const used = b.cap_eur > 0 ? Math.min(1, b.spent_eur / b.cap_eur) : 0;
    const el = document.createElement("div");
    el.className = "bucket";
    el.innerHTML =
      `<div class="bucket-row"><span class="bucket-name">${b.bucket}</span>` +
      `<span class="bucket-fig">${eur(b.spent_eur)} / ${eur(b.cap_eur)}</span></div>` +
      `<div class="meter"><div class="meter-fill ${used > 0.85 ? "hot" : ""}" ` +
      `style="width:${(used * 100).toFixed(0)}%"></div></div>`;
    host.appendChild(el);
  }
}

async function loadModels() {
  const data = await (await fetch("/api/models")).json();
  const host = $("#roster");
  host.innerHTML = "";
  for (const m of data.models) {
    const li = document.createElement("li");
    const tools =
      m.tools && m.tools.workspace
        ? `<span class="r-tools" title="agentic tool loop">${
            m.tools.writable ? "tools: read/write" : "tools: read-only"
          }</span>`
        : "";
    li.innerHTML =
      `<span class="dot ${m.active ? "on" : ""}"></span>` +
      `<span class="r-name">${m.name}</span>` +
      `<span class="r-model">${m.model}</span>` +
      tools;
    host.appendChild(li);
  }
  // Fill the composer's model picker: Auto + each active agent (keep current choice).
  const select = $("#model-select");
  if (select) {
    const chosen = select.value;
    select.innerHTML = `<option value="">Auto (Dirigent)</option>`;
    for (const m of data.models) {
      if (!m.active) continue;
      const opt = document.createElement("option");
      opt.value = m.name;
      opt.textContent = `${m.name} · ${m.model}`;
      select.appendChild(opt);
    }
    select.value = chosen;
  }
}

async function loadAnalytics() {
  const data = await (await fetch("/api/analytics")).json();
  const host = $("#activity");
  if (!data.total_count) {
    host.innerHTML = `<p class="activity-empty">No requests yet.</p>`;
    return;
  }
  const max = Math.max(...data.by_agent.map((a) => a.count), 1);
  let html =
    `<div class="activity-head"><span class="activity-total">${eur(data.total_eur)}</span>` +
    `<span class="activity-count">${data.total_count} routed</span></div>`;
  for (const a of data.by_agent) {
    html +=
      `<div class="act-row"><span class="a-name">${a.agent || "blocked"}</span>` +
      `<span class="a-bar" style="width:${(a.count / max) * 70}px"></span>` +
      `<span class="a-count">${a.count}</span></div>`;
  }
  host.innerHTML = html;
}

async function raiseBudget() {
  const current = $("#budget-monthly").textContent.replace(/[^0-9.]/g, "");
  const next = window.prompt("New monthly budget (€):", current || "35");
  if (!next) return;
  await fetch("/api/budget/raise", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ monthly_budget_eur: Number(next) }),
  });
  loadBudget();
}

// ---------- settings ----------
async function logout() {
  await fetch("/api/auth/logout", { method: "POST" });
  window.location.href = "/login";
}

async function openSettings() {
  const [settings, models, me] = await Promise.all([
    (await fetch("/api/settings")).json(),
    (await fetch("/api/models")).json(),
    (await fetch("/api/auth/me")).json().catch(() => ({ user: null })),
  ]);
  const account = $("#account-section");
  if (me.user && me.user.email) {
    $("#set-email").textContent = me.user.email;
    account.hidden = false;
  } else {
    account.hidden = true;
  }
  $("#set-profile").textContent = settings.profile;
  $("#set-strategy").textContent = settings.router_strategy;
  $("#set-optimize").textContent = settings.router_optimize ? "on" : "off";
  $("#set-budget").value = Math.round(settings.monthly_budget_eur);
  const list = $("#set-models");
  list.innerHTML = "";
  for (const m of models.models) {
    const li = document.createElement("li");
    li.innerHTML =
      `<span class="dot ${m.active ? "on" : ""}"></span>` +
      `<span class="r-name">${m.name}</span>` +
      `<span class="r-model">${m.model}</span>` +
      `<span class="set-state">${m.active ? "active" : "no key"}</span>`;
    list.appendChild(li);
  }
  $("#settings-overlay").hidden = false;
}
function closeSettings() {
  $("#settings-overlay").hidden = true;
}
async function saveBudget() {
  const value = Number($("#set-budget").value);
  if (!value || value < 0) return;
  await fetch("/api/budget/raise", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ monthly_budget_eur: value }),
  });
  loadBudget();
  closeSettings();
}

// ---------- wiring ----------
composer.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = promptEl.value.trim();
  if (!text) return;
  promptEl.value = "";
  promptEl.style.height = "auto";
  sendMessage(text);
});
promptEl.addEventListener("input", () => {
  promptEl.style.height = "auto";
  promptEl.style.height = Math.min(promptEl.scrollHeight, 200) + "px";
});
promptEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    composer.requestSubmit();
  }
});
$("#raise-btn").addEventListener("click", raiseBudget);
$("#new-chat").addEventListener("click", newChat);
$("#settings-btn").addEventListener("click", openSettings);
$("#settings-close").addEventListener("click", closeSettings);
$("#settings-overlay").addEventListener("click", (e) => {
  if (e.target.id === "settings-overlay") closeSettings();
});
$("#set-budget-save").addEventListener("click", saveBudget);
$("#logout-btn").addEventListener("click", logout);

loadBudget();
loadModels();
loadAnalytics();
loadConversations();

// register the service worker so the app is installable ("download as web app")
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
