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
function renderMarkdown(bubble, text) {
  if (window.marked && window.DOMPurify) {
    bubble.classList.add("md");
    bubble.innerHTML = DOMPurify.sanitize(marked.parse(text, { breaks: true }));
    for (const pre of bubble.querySelectorAll("pre")) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "copy-btn";
      btn.textContent = "kopieren";
      btn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(pre.querySelector("code")?.innerText ?? pre.innerText);
          btn.textContent = "kopiert ✓";
          setTimeout(() => (btn.textContent = "kopieren"), 1500);
        } catch (e) {}
      });
      pre.appendChild(btn);
    }
  } else {
    bubble.textContent = text;
  }
}

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
  renderMarkdown(bubble, msg.content);
  wrap.appendChild(bubble);
  transcript.appendChild(wrap);
}

function renderBlocked(wrap, bubble, info) {
  bubble.remove();
  const box = document.createElement("div");
  box.className = "blocked";
  box.innerHTML =
    `<h3>Budget erreicht</h3><p>${info.reason}. Der Topf „${info.bucket}“ pausiert ` +
    `für diese rollierende Woche.</p>`;
  const btn = document.createElement("button");
  btn.className = "ghost-btn";
  btn.textContent = "Budget erhöhen";
  btn.addEventListener("click", raiseBudget);
  box.appendChild(btn);
  wrap.appendChild(box);
  scrollDown();
}

// ---------- attachments ----------
let pendingFiles = []; // {name, data(base64)}

function renderAttachChips() {
  const row = $("#attach-chips");
  row.innerHTML = "";
  row.hidden = pendingFiles.length === 0;
  pendingFiles.forEach((f, i) => {
    const chip = document.createElement("span");
    chip.className = "attach-chip";
    chip.textContent = f.name + " ";
    const x = document.createElement("button");
    x.type = "button";
    x.textContent = "×";
    x.setAttribute("aria-label", `${f.name} entfernen`);
    x.addEventListener("click", () => {
      pendingFiles.splice(i, 1);
      renderAttachChips();
    });
    chip.appendChild(x);
    row.appendChild(chip);
  });
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result.split(",", 2)[1]);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}

async function onFilesPicked(list) {
  for (const file of list) {
    if (file.size > 15 * 1024 * 1024) {
      alert(`${file.name} ist größer als 15 MB.`);
      continue;
    }
    if (pendingFiles.length >= 8) break;
    pendingFiles.push({ name: file.name, data: await fileToBase64(file) });
  }
  renderAttachChips();
}

// ---------- streaming a turn ----------
async function sendMessage(text) {
  const sentFiles = pendingFiles.map((f) => f.name);
  addUserMessage(sentFiles.length ? `${text}\n\u{1F4CE} ${sentFiles.join(", ")}` : text);
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
        attachments: pendingFiles.length ? pendingFiles : null,
      }),
    });
  } catch (e) {
    bubble.classList.remove("cursor");
    bubble.textContent = "Netzwerkfehler — läuft der Server?";
    return;
  }

  pendingFiles = [];
  renderAttachChips();

  if (resp.status === 401) {
    window.location.href = "/login";
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // One shared context for the whole stream: ctx.raw accumulates across deltas.
  const ctx = { wrap, chip, bubble, raw: "" };
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop();
    for (const block of events) handleEvent(block, ctx);
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
    ctx.raw = (ctx.raw || "") + payload.text;
    ctx.bubble.textContent = ctx.raw;
    scrollDown();
  } else if (event === "thinking") {
    addThinkingBox(ctx.wrap, ctx.bubble, payload.text);
  } else if (event === "file") {
    addFileCard(ctx.wrap, payload);
  } else if (event === "blocked") {
    renderBlocked(ctx.wrap, ctx.bubble, payload);
  } else if (event === "error") {
    ctx.bubble.classList.remove("cursor");
    ctx.bubble.classList.add("msg-error");
    ctx.bubble.textContent = payload.message || "Etwas ist schiefgelaufen.";
  } else if (event === "done" && !payload.blocked) {
    finalizeChip(ctx.chip, payload);
    if (ctx.raw) renderMarkdown(ctx.bubble, ctx.raw);
    addDownloadButton(ctx.wrap, ctx.bubble, ctx.raw);
  }
}

function downloadBlob(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

async function exportAnswer(text, format, btn) {
  btn.disabled = true;
  try {
    const resp = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text, format }),
    });
    if (!resp.ok) throw new Error(`export failed: ${resp.status}`);
    downloadBlob(await resp.blob(), `cmn-ai-antwort.${format}`);
  } catch (e) {
    alert("Export fehlgeschlagen — bitte noch einmal versuchen.");
  } finally {
    btn.disabled = false;
  }
}

function addDownloadButton(wrap, bubble, raw) {
  const text = (raw || bubble.textContent).trim();
  if (!text) return;
  const row = document.createElement("div");
  row.className = "dl-row";
  const mk = (label, handler) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dl-btn";
    btn.title = `Antwort als ${label} speichern`;
    btn.textContent = label;
    btn.addEventListener("click", () => handler(btn));
    row.appendChild(btn);
  };
  mk("\u2913 md", () => downloadBlob(new Blob([text], { type: "text/markdown" }), "cmn-ai-antwort.md"));
  mk("\u2913 pdf", (btn) => exportAnswer(text, "pdf", btn));
  mk("\u2913 docx", (btn) => exportAnswer(text, "docx", btn));
  mk("\u2913 pptx", (btn) => exportAnswer(text, "pptx", btn));
  wrap.appendChild(row);
}

function addThinkingBox(wrap, bubble, text) {
  if (!text || !text.trim()) return;
  const box = document.createElement("details");
  box.className = "thinking-box";
  const summary = document.createElement("summary");
  summary.textContent = "\u{1F4AD} So hat die KI gedacht";
  const body = document.createElement("div");
  body.className = "thinking-body";
  body.textContent = text.trim();
  box.appendChild(summary);
  box.appendChild(body);
  wrap.insertBefore(box, bubble);
}

function addFileCard(wrap, payload) {
  const card = document.createElement("a");
  card.className = "file-card";
  card.href = payload.url;
  card.setAttribute("download", payload.name);
  card.textContent = `\u{1F4C4} ${payload.name} \u2014 Herunterladen`;
  wrap.appendChild(card);
}

// ---------- conversations (chat history) ----------
async function loadConversations() {
  const data = await (await fetch("/api/conversations")).json();
  const host = $("#chats");
  host.innerHTML = "";
  if (!data.conversations.length) {
    host.innerHTML = `<li class="chats-empty">Noch keine Chats.</li>`;
    return;
  }
  for (const c of data.conversations) {
    const li = document.createElement("li");
    li.className = "chat-item" + (c.id === currentConversationId ? " active" : "");
    const title = document.createElement("button");
    title.className = "chat-title";
    title.textContent = c.title === "New chat" ? "Neuer Chat" : c.title;
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
    `<div class="welcome"><h1>Frag irgendetwas.</h1><p>Ein gratis lokales Modell trägt ` +
    `die Masse. Recherche, harter Code und Spezialaufgaben gehen an den passenden ` +
    `bezahlten Experten — nur wenn das Budget es erlaubt. Jede Antwort zeigt, wer sie ` +
    `beantwortet hat und was sie gekostet hat.</p></div>`;
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
    select.innerHTML =
      `<option value="">Auto (Dirigent)</option>` +
      `<option value="council">Team (mehrere KIs)</option>`;
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

async function uploadVault() {
  const input = $("#vault-files");
  const status = $("#vault-status");
  const files = [...(input.files || [])].filter((f) => f.name.endsWith(".md"));
  if (!files.length) {
    status.textContent = "Keine .md-Dateien ausgewählt.";
    return;
  }
  status.textContent = `Lade ${files.length} Notizen…`;
  const notes = [];
  for (const f of files) notes.push({ path: f.webkitRelativePath || f.name, content: await f.text() });
  try {
    const resp = await fetch("/api/vault/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes }),
    });
    const data = await resp.json().catch(() => ({}));
    status.textContent = resp.ok
      ? `${data.saved} gespeichert (gesamt ${data.total_notes}).`
      : data.detail || "Upload fehlgeschlagen.";
  } catch (e) {
    status.textContent = "Netzwerkfehler beim Upload.";
  }
}

async function openSettings() {
  const [settings, models, me, vault] = await Promise.all([
    (await fetch("/api/settings")).json(),
    (await fetch("/api/models")).json(),
    (await fetch("/api/auth/me")).json().catch(() => ({ user: null })),
    (await fetch("/api/vault/status")).json().catch(() => ({ enabled: false })),
  ]);
  $("#vault-section").hidden = !vault.enabled;
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
$("#attach").addEventListener("click", () => $("#attach-input").click());
$("#attach-input").addEventListener("change", async (e) => {
  await onFilesPicked(e.target.files);
  e.target.value = "";
});

// Drag & drop anywhere onto the chat area
(() => {
  const stage = document.querySelector("main.stage");
  if (!stage) return;
  let dragDepth = 0;
  stage.addEventListener("dragenter", (e) => {
    e.preventDefault();
    dragDepth += 1;
    stage.classList.add("dropzone-active");
  });
  stage.addEventListener("dragover", (e) => e.preventDefault());
  stage.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) stage.classList.remove("dropzone-active");
  });
  stage.addEventListener("drop", async (e) => {
    e.preventDefault();
    dragDepth = 0;
    stage.classList.remove("dropzone-active");
    if (e.dataTransfer && e.dataTransfer.files.length) {
      await onFilesPicked(e.dataTransfer.files);
    }
  });
})();

// Pasted files (e.g. screenshots) become attachments
promptEl.addEventListener("paste", async (e) => {
  const items = e.clipboardData ? e.clipboardData.items : [];
  const files = [];
  for (const item of items) {
    if (item.kind === "file") {
      const f = item.getAsFile();
      if (f) files.push(f.name === "image.png" ? new File([f], `screenshot-${Date.now()}.png`, { type: f.type }) : f);
    }
  }
  if (files.length) {
    e.preventDefault();
    await onFilesPicked(files);
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
(() => {
  const sel = $("#set-theme");
  try {
    sel.value = localStorage.getItem("cmn-theme") || "";
  } catch (e) {}
  sel.addEventListener("change", () => {
    const v = sel.value;
    try {
      if (v) localStorage.setItem("cmn-theme", v);
      else localStorage.removeItem("cmn-theme");
    } catch (e) {}
    if (v) document.documentElement.dataset.theme = v;
    else delete document.documentElement.dataset.theme;
  });
})();
$("#vault-upload-btn").addEventListener("click", uploadVault);
$("#bench-run").addEventListener("click", async () => {
  const btn = $("#bench-run");
  const host = $("#bench-results");
  btn.disabled = true;
  btn.textContent = "läuft … (bis zu 60 s)";
  host.innerHTML = "";
  try {
    const resp = await fetch("/api/benchmark", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await resp.json();
    for (const r of data.results) {
      const li = document.createElement("li");
      li.className = "bench-row" + (r.ok ? "" : " bench-fail");
      li.textContent = r.ok
        ? `${r.agent} · ${r.model} — ${r.seconds}s · ${(r.cost_eur ?? 0).toFixed(4)} €`
        : `${r.agent} · ${r.model} — ✗ ${r.error}`;
      host.appendChild(li);
    }
  } catch (e) {
    host.innerHTML = '<li class="bench-row bench-fail">Speed-Test fehlgeschlagen.</li>';
  } finally {
    btn.disabled = false;
    btn.textContent = "Speed-Test starten";
  }
});

loadBudget();
loadModels();
loadAnalytics();
loadConversations();

// register the service worker so the app is installable ("download as web app")
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
