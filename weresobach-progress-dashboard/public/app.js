// WereSoBach sprint-board dashboard. Fetches /api/board and renders a
// 4-column Kanban + agent log.

const REFRESH_MS = 60_000;
let timer = null;
let lastFetchedAt = null;
let tickTimer = null;

// Column order + metadata. FAIL is folded into the Blocked column (with a
// red tint) so we stay at 4 columns on mobile. The actual badge still reads
// "FAIL" for those tasks.
const COLUMNS = [
  { key: "TODO",    label: "Todo",        match: (s) => s === "TODO" },
  { key: "WIP",     label: "In Progress", match: (s) => s === "WIP" },
  { key: "BLOCKED", label: "Blocked",     match: (s) => s === "BLOCKED" || s === "FAIL" },
  { key: "DONE",    label: "Done",        match: (s) => s === "DONE" },
];

const STATUS_BADGE_CLASS = {
  TODO: "badge-todo",
  WIP: "badge-wip",
  BLOCKED: "badge-blocked",
  DONE: "badge-done",
  FAIL: "badge-fail",
};

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtRelativeTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const delta = (Date.now() - d.getTime()) / 1000;
  if (delta < 10) return "just now";
  if (delta < 60) return `${Math.floor(delta)}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function tickLastUpdated() {
  const el = document.getElementById("lastUpdated");
  if (!el) return;
  if (!lastFetchedAt) {
    el.textContent = "never";
    return;
  }
  el.textContent = `last refreshed ${fmtRelativeTime(lastFetchedAt)}`;
}

function renderCard(task) {
  const badgeCls = STATUS_BADGE_CLASS[task.status] || "badge-todo";
  const badgeText = task.statusRaw && /fail/i.test(task.statusRaw) ? "FAIL" : task.status;
  const note = task.note ? `<div class="card-note">${escapeHtml(task.note)}</div>` : "";
  const last = task.lastUpdate
    ? `<span class="card-last">${escapeHtml(task.lastUpdate)}</span>`
    : `<span class="card-last card-last-empty">no update yet</span>`;
  return `
    <article class="card ${task.status.toLowerCase()}">
      <div class="card-head">
        <span class="card-id">#${task.id}</span>
        <span class="card-badge ${badgeCls}">${escapeHtml(badgeText)}</span>
      </div>
      <div class="card-title">${escapeHtml(task.title)}</div>
      ${note}
      <div class="card-foot">
        <span class="card-owner">${escapeHtml(task.owner || "—")}</span>
        ${last}
      </div>
    </article>
  `;
}

function renderBoard(tasks) {
  const root = document.getElementById("board");
  if (!tasks.length) {
    root.innerHTML = `<div class="loading">No tasks parsed from board.</div>`;
    return;
  }
  const columns = COLUMNS.map((col) => {
    const items = tasks.filter((t) => col.match(t.status));
    const cards = items.map(renderCard).join("") || `<div class="column-empty">nothing here</div>`;
    return `
      <section class="column column-${col.key.toLowerCase()}">
        <header class="column-head">
          <span class="column-label">${escapeHtml(col.label)}</span>
          <span class="column-count">${items.length}</span>
        </header>
        <div class="column-body">${cards}</div>
      </section>
    `;
  });
  root.innerHTML = columns.join("");
}

function updateSummary(tasks) {
  const counts = { total: tasks.length, TODO: 0, WIP: 0, BLOCKED: 0, DONE: 0 };
  for (const t of tasks) {
    if (t.status === "FAIL") counts.BLOCKED += 1;
    else if (counts[t.status] != null) counts[t.status] += 1;
  }
  for (const card of document.querySelectorAll(".summary-card")) {
    const key = card.dataset.key;
    const v = key === "total" ? counts.total : counts[key];
    card.querySelector(".summary-value").textContent = v ?? 0;
  }
}

function renderLog(entries) {
  const root = document.getElementById("logList");
  const count = document.getElementById("logCount");
  count.textContent = `${entries.length} ${entries.length === 1 ? "entry" : "entries"}`;
  if (!entries.length) {
    root.innerHTML = `<div class="loading">No log entries yet.</div>`;
    return;
  }
  root.innerHTML = entries
    .map((e) => {
      const when = e.time ? `${e.date} ${e.time}` : e.date;
      const agent = e.agent
        ? `<span class="log-agent">${escapeHtml(e.agent)}</span>`
        : "";
      return `
        <article class="log-entry">
          <header class="log-entry-head">
            <span class="log-when">${escapeHtml(when)}</span>
            ${agent}
            <span class="log-title">${escapeHtml(e.title)}</span>
          </header>
          <pre class="log-body">${escapeHtml(e.body || "")}</pre>
        </article>
      `;
    })
    .join("");
}

async function load() {
  const boardRoot = document.getElementById("board");
  try {
    const resp = await fetch("/api/board", { cache: "no-store" });
    if (!resp.ok) {
      const body = await resp.text();
      throw new Error(`API ${resp.status}: ${body.slice(0, 200)}`);
    }
    const data = await resp.json();
    if (data.error) throw new Error(data.error);

    lastFetchedAt = new Date().toISOString();
    tickLastUpdated();

    const tasks = data.tasks || [];
    const log = data.log || [];

    if (data.source?.url) {
      const a = document.getElementById("sourceLink");
      const b = document.getElementById("sourceLinkFooter");
      if (a) a.href = data.source.url;
      if (b) b.href = data.source.url;
    }

    updateSummary(tasks);
    renderBoard(tasks);
    renderLog(log);
  } catch (err) {
    boardRoot.innerHTML = `<div class="error-banner">Failed to load: ${escapeHtml(err.message)}</div>`;
  }
}

function setupAutoRefresh() {
  const checkbox = document.getElementById("autoRefresh");
  function apply() {
    if (timer) clearInterval(timer);
    if (checkbox.checked) {
      timer = setInterval(load, REFRESH_MS);
    }
  }
  checkbox.addEventListener("change", apply);
  apply();
}

document.getElementById("refreshBtn").addEventListener("click", load);
setupAutoRefresh();

// Tick "last refreshed" label every 5s so the user sees elapsed time ticking up.
tickTimer = setInterval(tickLastUpdated, 5_000);

load();
