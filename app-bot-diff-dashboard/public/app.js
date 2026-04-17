// Path B dashboard — fetches pipeline state from /api/pipelines and renders it
// as a CI/CD-style pipeline visualization.

const REFRESH_MS = 15_000;
let timer = null;

const ICONS = {
  success: "✓",
  running: "●",
  queued: "◌",
  failed: "✕",
  skipped: "⊘",
  pending: "◌",
};

const STAGE_LABELS_LONG = {
  commit: "Commit",
  sync: "Phase 2",
  bottest: "Bottest",
  audit: "Phase 3",
  pr: "Bridge PR",
};

const STAGE_DESCRIPTIONS = {
  commit: "New commit pushed to weresobach/main (the hand-built north-star app).",
  sync: "Phase 2 workflow (path-b-sync.yml): sends the commit + diff to the bot's /prompt API so it rebuilds the same feature on bottest.",
  bottest: "Bot-generated output pushed to weresobachbottest/main, tagged [path-b-sync].",
  audit: "Phase 3 workflow (gap-audit.yml): fingerprints bottest against 7 curated patterns and reports missing features.",
  pr: "Draft PR on discord-claude-app-builder with the gap findings, to improve bot prompts.",
};

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

function overallStatus(stages) {
  if (stages.some((s) => s.status === "failed")) return "failed";
  if (stages.some((s) => s.status === "running")) return "running";
  if (stages.some((s) => s.status === "queued")) return "queued";
  if (stages.every((s) => s.status === "success" || s.status === "skipped")) {
    const nonSkipped = stages.filter((s) => s.status !== "skipped");
    if (nonSkipped.length === 0) return "skipped";
    return "success";
  }
  return "pending";
}

function renderStage(stage) {
  const icon = ICONS[stage.status] || "?";
  const label = STAGE_LABELS_LONG[stage.name] || stage.label;
  const subBody = stage.subtitle
    ? stage.url
      ? `<a href="${stage.url}" target="_blank" rel="noopener">${escapeHtml(stage.subtitle)}</a>`
      : escapeHtml(stage.subtitle)
    : stage.status === "pending"
      ? "waiting"
      : "";
  const desc = STAGE_DESCRIPTIONS[stage.name] || "";
  return `
    <div class="stage ${stage.status}" data-stage="${stage.name}" title="${escapeHtml(desc)}">
      <div class="stage-connector"></div>
      <div class="stage-icon">${icon}</div>
      <div class="stage-label">${escapeHtml(label)}</div>
      <div class="stage-sub">${subBody}</div>
    </div>
  `;
}

function renderPipeline(p) {
  const overall = overallStatus(p.stages);
  const stagesHtml = p.stages.map(renderStage).join("");
  const timeAgo = fmtRelativeTime(p.source.timestamp);
  const commitLink = p.source.url
    ? `<a href="${p.source.url}" target="_blank" rel="noopener">${p.source.shortSha}</a>`
    : p.source.shortSha;
  return `
    <article class="pipeline" data-sha="${p.source.sha}">
      <header class="pipeline-header">
        <div class="pipeline-title">
          <div class="pipeline-message">${escapeHtml(p.source.message)}</div>
          <div class="pipeline-meta">
            <span class="sha">${commitLink}</span>
            · ${escapeHtml(p.source.author || "unknown")}
            · ${timeAgo}
            ${p.source.skipped ? ' · <span style="color: var(--skipped)">skip-sync</span>' : ""}
          </div>
        </div>
        <div class="pipeline-overall">
          <span class="overall-badge ${overall}">${overall}</span>
          <span class="pipeline-chevron">›</span>
        </div>
      </header>
      <div class="pipeline-stages">${stagesHtml}</div>
      <div class="pipeline-details">
        <div class="detail-grid">
          ${renderDetailCard("Source", `<a href="${p.source.url}" target="_blank" rel="noopener">${p.source.shortSha}</a>`)}
          ${renderDetailCard("Phase 2 run", p.ids.syncRunId ? p.ids.syncRunId : "—")}
          ${renderDetailCard("Phase 3 run", p.ids.auditRunId ? p.ids.auditRunId : "—")}
          ${renderDetailCard("Bridge PR", p.ids.prNumber ? "#" + p.ids.prNumber : "—")}
        </div>
        <pre class="detail-full-message">${escapeHtml(p.source.fullMessage || "")}</pre>
      </div>
    </article>
  `;
}

function renderDetailCard(title, value) {
  return `
    <div class="detail-card">
      <div class="detail-card-title">${escapeHtml(title)}</div>
      <div class="detail-card-value">${value}</div>
    </div>
  `;
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function updateSummary(pipelines) {
  const counts = { total: pipelines.length, success: 0, running: 0, failed: 0, skipped: 0 };
  for (const p of pipelines) {
    const o = overallStatus(p.stages);
    if (o === "success") counts.success += 1;
    else if (o === "running" || o === "queued") counts.running += 1;
    else if (o === "failed") counts.failed += 1;
    else if (o === "skipped") counts.skipped += 1;
  }
  for (const card of document.querySelectorAll(".summary-card")) {
    const key = card.dataset.key;
    card.querySelector(".summary-value").textContent = counts[key] ?? 0;
  }
}

async function load() {
  const root = document.getElementById("pipelines");
  try {
    const resp = await fetch("/api/pipelines?limit=25", { cache: "no-store" });
    if (!resp.ok) {
      const body = await resp.text();
      throw new Error(`API ${resp.status}: ${body.slice(0, 200)}`);
    }
    const data = await resp.json();
    if (data.error) throw new Error(data.error);

    document.getElementById("lastUpdated").textContent =
      "updated " + fmtRelativeTime(data.generatedAt);

    const pipelines = data.pipelines || [];
    updateSummary(pipelines);

    if (pipelines.length === 0) {
      root.innerHTML = `<div class="loading">No pipelines yet.</div>`;
      return;
    }

    root.innerHTML = pipelines.map(renderPipeline).join("");
    // wire expand/collapse
    root.querySelectorAll(".pipeline-header").forEach((h) => {
      h.addEventListener("click", () => {
        h.parentElement.classList.toggle("expanded");
      });
    });
  } catch (err) {
    root.innerHTML = `<div class="error-banner">Failed to load: ${escapeHtml(err.message)}</div>`;
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
load();
