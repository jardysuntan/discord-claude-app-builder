// Pages Function: fetches WERESOBACH_PROGRESS.md from the bridge repo,
// parses the "Board" table + agent log, and returns structured JSON.
//
// Env: GH_TOKEN — fine-grained PAT with Contents:Read on
//   jardysuntan/discord-claude-app-builder.

const OWNER = "jardysuntan";
const REPO = "discord-claude-app-builder";
const PATH = "WERESOBACH_PROGRESS.md";
const REF = "main";

// Canonical statuses we surface to the client. Keep in sync with app.js STATUS_*.
const CANONICAL = ["TODO", "WIP", "BLOCKED", "DONE", "FAIL"];

async function fetchMarkdown(env) {
  const url = `https://api.github.com/repos/${OWNER}/${REPO}/contents/${PATH}?ref=${REF}`;
  const resp = await fetch(url, {
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "weresobach-progress-dashboard",
    },
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`GitHub ${resp.status}: ${body.slice(0, 300)}`);
  }
  const data = await resp.json();
  // content is base64-encoded with embedded newlines.
  const decoded = atob((data.content || "").replace(/\s/g, ""));
  // atob gives latin1; round-trip through UTF-8 to preserve any non-ASCII chars.
  const utf8 = new TextDecoder("utf-8").decode(
    Uint8Array.from(decoded, (c) => c.charCodeAt(0))
  );
  return { text: utf8, sha: data.sha };
}

// Normalize a free-form status string to one of CANONICAL + reason.
// Handles patterns like:
//   "WIP"
//   "TODO (blocked on #1)"
//   "DONE"
//   "FAIL — build broken"
function normalizeStatus(raw) {
  const s = (raw || "").trim();
  if (!s || s === "—" || s === "-") return { status: "TODO", note: null };
  const upper = s.toUpperCase();

  // Blocked takes precedence — if the cell mentions "blocked", treat the task
  // as BLOCKED even when the literal status token is TODO.
  const blockedMatch = s.match(/blocked\s+on\s+([^)]+)/i);
  if (blockedMatch) {
    return { status: "BLOCKED", note: `blocked on ${blockedMatch[1].trim()}` };
  }

  for (const c of CANONICAL) {
    if (upper.startsWith(c)) {
      const rest = s.slice(c.length).trim().replace(/^[—\-:()\s]+/, "").replace(/\)$/, "").trim();
      return { status: c, note: rest || null };
    }
  }
  // Unknown — fall back to TODO.
  return { status: "TODO", note: s };
}

// Parse the first markdown table whose header matches the Board schema.
// Columns: # | Feature / Task | Owner | Status | Last Update
function parseBoard(md) {
  const lines = md.split(/\r?\n/);
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*\|\s*#\s*\|/.test(line) && /owner/i.test(line) && /status/i.test(line)) {
      break;
    }
    i += 1;
  }
  if (i >= lines.length) return [];
  // Skip the header separator.
  i += 2;

  const tasks = [];
  for (; i < lines.length; i += 1) {
    const line = lines[i];
    if (!line.trim().startsWith("|")) break; // end of table
    const cells = line
      .split("|")
      .slice(1, -1)
      .map((c) => c.trim());
    if (cells.length < 5) continue;
    const [idRaw, title, owner, statusRaw, lastUpdate] = cells;
    const id = Number(idRaw);
    if (!Number.isFinite(id)) continue;
    const normalized = normalizeStatus(statusRaw);
    tasks.push({
      id,
      title,
      owner: owner || "—",
      status: normalized.status,
      statusRaw,
      note: normalized.note,
      lastUpdate: lastUpdate === "—" || !lastUpdate ? null : lastUpdate,
    });
  }
  return tasks;
}

// Parse agent-log entries. We accept two shapes:
//
//   ### 2026-04-19 15:42 — short title
//   body text…
//
//   ### 2026-04-19 — kickoff
//   body text…
//
// Agent is inferred from a leading "Foreman:" / "Agent-D:" marker in the body,
// otherwise null.
function parseAgentLog(md) {
  const marker = /^##\s+Agent log\s*$/im;
  const idx = md.search(marker);
  if (idx === -1) return [];
  const section = md.slice(idx);
  const lines = section.split(/\r?\n/);

  const entries = [];
  let current = null;
  for (const line of lines) {
    const h = line.match(/^###\s+(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,2}:\d{2}))?\s*[—-]\s*(.+)$/);
    if (h) {
      if (current) entries.push(current);
      current = {
        timestamp: h[1] + (h[2] ? "T" + h[2] + ":00" : ""),
        date: h[1],
        time: h[2] || null,
        title: h[3].trim(),
        agent: null,
        body: "",
      };
      continue;
    }
    if (current) {
      current.body += line + "\n";
    }
  }
  if (current) entries.push(current);

  // Attempt to sniff the agent out of the first line of body.
  for (const e of entries) {
    const body = e.body.trim();
    const m = body.match(/^(foreman|agent[-\s]?[A-F]|claude-foreman)\s*[:\-]/i);
    if (m) e.agent = m[1].toLowerCase().replace(/\s/g, "-");
    e.body = body;
  }

  // Newest first.
  entries.sort((a, b) => (b.timestamp || "").localeCompare(a.timestamp || ""));
  return entries;
}

export async function onRequestGet({ env }) {
  try {
    if (!env.GH_TOKEN) {
      return json({ error: "GH_TOKEN not set on Pages project" }, 500);
    }
    const { text, sha } = await fetchMarkdown(env);
    const tasks = parseBoard(text);
    const log = parseAgentLog(text);
    return json({
      generatedAt: new Date().toISOString(),
      source: {
        repo: `${OWNER}/${REPO}`,
        path: PATH,
        ref: REF,
        sha,
        url: `https://github.com/${OWNER}/${REPO}/blob/${REF}/${PATH}`,
      },
      tasks,
      log,
    });
  } catch (err) {
    return json({ error: String(err?.message || err) }, 500);
  }
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
    },
  });
}
