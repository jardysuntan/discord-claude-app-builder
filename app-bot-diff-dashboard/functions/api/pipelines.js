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
const SKIP_RE = /\[skip-sync\]|\[path-b-sync\]|^(ci|chore|docs|test)[:(]/;

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
    ] = await Promise.all([
      call(`/repos/${WERESOBACH}/commits`, { per_page: limit }),
      call(`/repos/${WERESOBACH}/actions/workflows/${SYNC_WORKFLOW_FILE}/runs`, {
        per_page: Math.max(limit, 30),
      }),
      call(`/repos/${BOTTEST}/commits`, { per_page: Math.max(limit, 30) }),
      call(`/repos/${BOTTEST}/actions/workflows/${AUDIT_WORKFLOW_FILE}/runs`, {
        per_page: Math.max(limit, 30),
      }).catch(() => ({ workflow_runs: [] })), // workflow may not exist yet on first deploy
      call(`/repos/${BRIDGE}/pulls`, {
        state: "all",
        per_page: 50,
        sort: "created",
        direction: "desc",
      }).catch(() => []),
    ]);

    const syncRunByHeadSha = new Map();
    for (const r of syncRuns.workflow_runs || []) {
      syncRunByHeadSha.set(r.head_sha, r);
    }

    const auditRunByHeadSha = new Map();
    for (const r of auditRuns.workflow_runs || []) {
      auditRunByHeadSha.set(r.head_sha, r);
    }

    // Group bottest commits by the source weresobach SHA they reference.
    const bottestCommitBySourceSha = new Map();
    for (const c of bottestCommits) {
      const sourceSha = shaFromBottestCommitMessage(c.commit?.message || "");
      if (sourceSha) {
        if (!bottestCommitBySourceSha.has(sourceSha)) {
          bottestCommitBySourceSha.set(sourceSha, c);
        }
      }
    }

    const prByBranch = new Map();
    for (const pr of bridgePulls) {
      const m = pr.head?.ref?.match(/^gap-audit\/([0-9a-f]{7,40})$/);
      if (m) prByBranch.set(m[1], pr);
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
      const pr = bottestSha ? prByBranch.get(bottestSha) : null;

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
        },
      };
    });

    return new Response(
      JSON.stringify({
        generatedAt: new Date().toISOString(),
        repos: { source: WERESOBACH, target: BOTTEST, bridge: BRIDGE },
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
