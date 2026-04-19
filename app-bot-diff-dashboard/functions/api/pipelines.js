// Pages Function: aggregates Path B pipeline state across three repos and
// returns a normalized structure for the dashboard.
//
// Env: GH_TOKEN — fine-grained PAT with read scope on all three repos.

const OWNER = "jardysuntan";
const WERESOBACH = `${OWNER}/weresobach`;
const BOTTEST = `${OWNER}/weresobachbottest`;
const BRIDGE = `${OWNER}/discord-claude-app-builder`;
const SYNC_WORKFLOW_FILE = "path-b-sync.yml";
const AUDIT_WORKFLOW_FILE = "gap-audit.yml";
const VERIFY_WORKFLOW_FILE = "path-b-verify.yml";
const RETRIES_FILE = ".path-b-retries.json";
const MAX_RETRIES = 5;
const SKIP_RE = /\[skip-sync\]|\[path-b-sync\]|^(ci|chore|docs|test)[:(]/;
// Phase 4 marker appearing in Phase 3 auditor PR bodies.
const RETRY_MARKER_RE = /weresobach_sha=([0-9a-f]{7,40})/i;

function gh(env) {
  return async function call(path, params = {}) {
    const url = new URL(`https://api.github.com${path}`);
    for (const [k, v] of Object.entries(params)) {
      url.searchParams.set(k, String(v));
    }
    const resp = await fetch(url.toString(), {
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "app-bot-diff-dashboard",
      },
    });
    if (!resp.ok) {
      const body = await resp.text();
      throw new Error(`GitHub ${resp.status} on ${path}: ${body.slice(0, 300)}`);
    }
    return resp.json();
  };
}

function statusOf(run) {
  if (!run) return "pending";
  if (run.status === "completed") {
    return run.conclusion === "success" ? "success" : "failed";
  }
  if (run.status === "in_progress") return "running";
  if (run.status === "queued") return "queued";
  return "pending";
}

function durationMs(run) {
  if (!run?.run_started_at || !run?.updated_at) return null;
  const start = new Date(run.run_started_at).getTime();
  const end = new Date(run.updated_at).getTime();
  return end - start;
}

function shaFromBottestCommitMessage(msg) {
  const m = msg && msg.match(/weresobach@([0-9a-f]{7,40})/i);
  return m ? m[1] : null;
}

function shaFromPrBody(body) {
  if (!body) return null;
  const m = body.match(RETRY_MARKER_RE);
  return m ? m[1] : null;
}

async function fetchRetriesJson(call) {
  try {
    const resp = await call(`/repos/${BOTTEST}/contents/${RETRIES_FILE}`);
    if (!resp?.content) return {};
    const decoded = atob(resp.content.replace(/\s/g, ""));
    return JSON.parse(decoded || "{}");
  } catch {
    return {};
  }
}

// Resolve retry count for a weresobach SHA against the retries map, which is keyed
// by full SHA. The caller passes the full SHA; we also try a prefix-match as a
// safety net in case the stored key is abbreviated.
function retryCountFor(sha, retriesMap) {
  if (!sha || !retriesMap) return 0;
  if (retriesMap[sha] != null) return Number(retriesMap[sha]) || 0;
  for (const [k, v] of Object.entries(retriesMap)) {
    if (k.startsWith(sha) || sha.startsWith(k)) return Number(v) || 0;
  }
  return 0;
}

function computeVerifyStage({
  skipped,
  bottestCommit,
  auditRun,
  pr,
  verifyRun,
  retryCount,
}) {
  if (skipped) {
    return { status: "skipped", subtitle: null };
  }
  if (!bottestCommit) {
    return { status: "pending", subtitle: "not synced yet" };
  }
  const auditStatus = statusOf(auditRun);
  if (!auditRun || auditStatus === "pending" || auditStatus === "queued" || auditStatus === "running") {
    return { status: "pending", subtitle: "auditing…" };
  }
  if (auditStatus === "failed") {
    return { status: "failed", subtitle: "audit error" };
  }
  if (!pr) {
    // Audit completed and opened no PR → no gaps → verified.
    return {
      status: "success",
      subtitle: retryCount > 0 ? `verified · retry ${retryCount}/${MAX_RETRIES}` : "no gaps",
    };
  }
  if (pr.state === "open") {
    return {
      status: "pending",
      subtitle: retryCount > 0 ? `retry ${retryCount}/${MAX_RETRIES} · awaiting merge` : "awaiting merge",
    };
  }
  // PR merged.
  if (pr.merged_at) {
    if (retryCount >= MAX_RETRIES) {
      return { status: "failed", subtitle: `${MAX_RETRIES}/${MAX_RETRIES} retries` };
    }
    const vStatus = statusOf(verifyRun);
    if (verifyRun && (vStatus === "running" || vStatus === "queued" || vStatus === "pending")) {
      return { status: "running", subtitle: `retry ${retryCount}/${MAX_RETRIES} · dispatching…` };
    }
    if (verifyRun && vStatus === "failed") {
      return { status: "failed", subtitle: `verify run failed` };
    }
    // verify succeeded (or hasn't fired yet) but no new audit PR closing the loop yet.
    return { status: "running", subtitle: `retry ${retryCount}/${MAX_RETRIES} · re-syncing` };
  }
  // PR closed without merging (declined).
  return { status: "skipped", subtitle: "PR declined" };
}

export async function onRequestGet({ env, request }) {
  try {
    const call = gh(env);
    const url = new URL(request.url);
    const limit = Math.min(Number(url.searchParams.get("limit") || 20), 50);

    // Pull everything in parallel.
    const [
      weresobachCommits,
      syncRuns,
      bottestCommits,
      auditRuns,
      bridgePulls,
      verifyRuns,
      retriesMap,
    ] = await Promise.all([
      call(`/repos/${WERESOBACH}/commits`, { per_page: limit }),
      call(`/repos/${WERESOBACH}/actions/workflows/${SYNC_WORKFLOW_FILE}/runs`, {
        per_page: Math.max(limit, 30),
      }),
      call(`/repos/${BOTTEST}/commits`, { per_page: Math.max(limit, 30) }),
      call(`/repos/${BOTTEST}/actions/workflows/${AUDIT_WORKFLOW_FILE}/runs`, {
        per_page: Math.max(limit, 30),
      }).catch(() => ({ workflow_runs: [] })),
      call(`/repos/${BRIDGE}/pulls`, {
        state: "all",
        per_page: 50,
        sort: "created",
        direction: "desc",
      }).catch(() => []),
      call(`/repos/${BRIDGE}/actions/workflows/${VERIFY_WORKFLOW_FILE}/runs`, {
        per_page: 50,
      }).catch(() => ({ workflow_runs: [] })),
      fetchRetriesJson(call),
    ]);

    const syncRunByHeadSha = new Map();
    for (const r of syncRuns.workflow_runs || []) {
      syncRunByHeadSha.set(r.head_sha, r);
    }

    const auditRunByHeadSha = new Map();
    for (const r of auditRuns.workflow_runs || []) {
      auditRunByHeadSha.set(r.head_sha, r);
    }

    // Pick the most recent bottest commit per originating weresobach SHA.
    // bottestCommits is returned newest-first; the first entry we see for a source
    // SHA wins, which is what we want (most recent retry attempt).
    const bottestCommitBySourceSha = new Map();
    for (const c of bottestCommits) {
      const sourceSha = shaFromBottestCommitMessage(c.commit?.message || "");
      if (sourceSha) {
        if (!bottestCommitBySourceSha.has(sourceSha)) {
          bottestCommitBySourceSha.set(sourceSha, c);
        }
      }
    }

    // Map Phase 3 auditor PRs by the bottest SHA encoded in their branch name,
    // and also collect (weresobachSha → latest PR) for Phase 4 retry tracking.
    const prByBottestSha = new Map();
    const prByWeresobachSha = new Map();
    for (const pr of bridgePulls) {
      const m = pr.head?.ref?.match(/^gap-audit\/([0-9a-f]{7,40})$/);
      if (m) prByBottestSha.set(m[1], pr);
      const wsha = shaFromPrBody(pr.body || "");
      if (wsha && !prByWeresobachSha.has(wsha)) {
        prByWeresobachSha.set(wsha, pr);
      }
    }

    // Phase 4 (verify) runs are triggered by pull_request events; their head_branch
    // is the PR's head ref — i.e., gap-audit/<bottestSha>. Map accordingly.
    const verifyRunByBottestSha = new Map();
    for (const r of verifyRuns.workflow_runs || []) {
      const m = (r.head_branch || "").match(/^gap-audit\/([0-9a-f]{7,40})$/);
      if (m && !verifyRunByBottestSha.has(m[1])) {
        verifyRunByBottestSha.set(m[1], r);
      }
    }

    const pipelines = weresobachCommits.slice(0, limit).map((c) => {
      const sha = c.sha;
      const shortSha = sha.slice(0, 7);
      const message = c.commit?.message || "";
      const firstLine = message.split("\n")[0];
      const skipped = SKIP_RE.test(firstLine);

      const syncRun = syncRunByHeadSha.get(sha);
      const bottestCommit = bottestCommitBySourceSha.get(sha) || bottestCommitBySourceSha.get(shortSha);
      const bottestSha = bottestCommit?.sha;
      const bottestShortSha = bottestSha?.slice(0, 7);
      const auditRun = bottestSha ? auditRunByHeadSha.get(bottestSha) : null;
      const pr = bottestSha ? prByBottestSha.get(bottestSha) : null;
      const verifyRun = bottestSha ? verifyRunByBottestSha.get(bottestSha) : null;
      const retryCount = retryCountFor(sha, retriesMap);

      const verify = computeVerifyStage({
        skipped,
        bottestCommit,
        auditRun,
        pr,
        verifyRun,
        retryCount,
      });

      // Build stages.
      const stages = [
        {
          name: "commit",
          label: "Commit",
          status: "success",
          subtitle: shortSha,
          url: c.html_url,
        },
        {
          name: "sync",
          label: skipped ? "Sync (skipped)" : "Phase 2 sync",
          status: skipped ? "skipped" : statusOf(syncRun),
          subtitle: syncRun ? `${Math.round((durationMs(syncRun) || 0) / 1000)}s` : skipped ? "ci/chore/docs/test" : null,
          url: syncRun?.html_url,
        },
        {
          name: "bottest",
          label: "Bottest sync",
          status: skipped
            ? "skipped"
            : bottestCommit
              ? "success"
              : syncRun?.conclusion === "failure"
                ? "failed"
                : statusOf(syncRun) === "running" || statusOf(syncRun) === "queued"
                  ? "pending"
                  : "pending",
          subtitle: bottestShortSha,
          url: bottestCommit?.html_url,
        },
        {
          name: "audit",
          label: "Phase 3 audit",
          status: skipped ? "skipped" : bottestCommit ? statusOf(auditRun) : "pending",
          subtitle: auditRun ? `${Math.round((durationMs(auditRun) || 0) / 1000)}s` : null,
          url: auditRun?.html_url,
        },
        {
          name: "pr",
          label: pr ? `PR #${pr.number}` : "Gap PR",
          status: skipped
            ? "skipped"
            : pr
              ? pr.state === "open"
                ? "success"
                : "success"
              : auditRun?.status === "completed"
                ? "skipped"
                : "pending",
          subtitle: pr ? (pr.draft ? "draft" : pr.state) : null,
          url: pr?.html_url,
        },
        {
          name: "verify",
          label: "Phase 4 verify",
          status: verify.status,
          subtitle: verify.subtitle,
          url: verifyRun?.html_url,
        },
      ];

      return {
        source: {
          sha,
          shortSha,
          message: firstLine,
          fullMessage: message,
          url: c.html_url,
          author: c.commit?.author?.name,
          timestamp: c.commit?.author?.date,
          skipped,
        },
        stages,
        ids: {
          syncRunId: syncRun?.id,
          auditRunId: auditRun?.id,
          prNumber: pr?.number,
          verifyRunId: verifyRun?.id,
        },
        retry: {
          count: retryCount,
          max: MAX_RETRIES,
        },
      };
    });

    return new Response(
      JSON.stringify({
        generatedAt: new Date().toISOString(),
        repos: { source: WERESOBACH, target: BOTTEST, bridge: BRIDGE },
        config: { maxRetries: MAX_RETRIES },
        pipelines,
      }),
      {
        headers: {
          "content-type": "application/json",
          "cache-control": "no-store",
        },
      }
    );
  } catch (err) {
    return new Response(
      JSON.stringify({ error: String(err?.message || err) }),
      { status: 500, headers: { "content-type": "application/json" } }
    );
  }
}
