// cmn·ai chat client — POST/SSE streaming, budget meter, route transparency.
"use strict";

const $ = (sel) => document.querySelector(sel);
const transcript = $("#transcript");
const composer = $("#composer");
const promptEl = $("#prompt");
const sendBtn = $("#send");

const CAP_LABEL = { chat: "chat", code: "code", research: "research", multimodal: "vision" };

function eur(n) {
  if (n === 0) return "free";
  return "€" + Number(n).toFixed(n < 0.1 ? 4 : 2);
}

function scrollDown() {
  transcript.scrollTop = transcript.scrollHeight;
}

function clearWelcome() {
  const w = transcript.querySelector(".welcome");
  if (w) w.remove();
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
  const isFree = route.estimated_eur === 0;
  chip.classList.toggle("free", isFree);
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
  // append the actual measured cost once the answer completes
  const cost = document.createElement("span");
  cost.className = "cost";
  cost.innerHTML = `<span class="sep">·</span> ${eur(done.cost_eur)}`;
  if (done.cost_eur === 0) chip.classList.add("free");
  chip.appendChild(cost);
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

async function sendMessage(text) {
  addUserMessage(text);
  const { wrap, chip, bubble } = addAssistantShell();

  let resp;
  try {
    resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: text }),
    });
  } catch (e) {
    bubble.classList.remove("cursor");
    bubble.textContent = "Network error — is the server running?";
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
    buffer = events.pop(); // keep incomplete tail
    for (const block of events) {
      handleEvent(block, { wrap, chip, bubble });
    }
  }
  bubble.classList.remove("cursor");
  loadBudget();
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

  if (event === "route") {
    renderChip(ctx.chip, payload);
  } else if (event === "delta") {
    ctx.bubble.textContent += payload.text;
    scrollDown();
  } else if (event === "blocked") {
    renderBlocked(ctx.wrap, ctx.bubble, payload);
  } else if (event === "done" && !payload.blocked) {
    finalizeChip(ctx.chip, payload);
  }
}

// ---------- budget + roster ----------
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
    li.innerHTML =
      `<span class="dot ${m.active ? "on" : ""}"></span>` +
      `<span class="r-name">${m.name}</span>` +
      `<span class="r-model">${m.model}</span>`;
    host.appendChild(li);
  }
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

loadBudget();
loadModels();
