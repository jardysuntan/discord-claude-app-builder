// Returns the gap-audit markdown report artifact for a given Phase 3 run.
// Streams the .md file out of the workflow's artifact zip.

const BOTTEST = "jardysuntan/weresobachbottest";

async function gh(env, path) {
  const resp = await fetch(`https://api.github.com${path}`, {
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "app-bot-diff-dashboard",
    },
  });
  if (!resp.ok) {
    throw new Error(`GitHub ${resp.status}: ${await resp.text()}`);
  }
  return resp;
}

export async function onRequestGet({ params, env }) {
  const runId = params.runId;
  if (!runId || !/^\d+$/.test(runId)) {
    return new Response(JSON.stringify({ error: "invalid runId" }), {
      status: 400,
      headers: { "content-type": "application/json" },
    });
  }

  try {
    const artifacts = await (
      await gh(env, `/repos/${BOTTEST}/actions/runs/${runId}/artifacts`)
    ).json();
    const reportArtifact = (artifacts.artifacts || []).find((a) =>
      a.name.startsWith("gap-audit-report-")
    );
    if (!reportArtifact) {
      return new Response(
        JSON.stringify({ error: "no gap-audit-report artifact on this run" }),
        { status: 404, headers: { "content-type": "application/json" } }
      );
    }

    const zipResp = await gh(
      env,
      `/repos/${BOTTEST}/actions/artifacts/${reportArtifact.id}/zip`
    );
    // Workers lack DecompressionStream for zip, so we lean on the fact that
    // zip entries are simple and parse manually — but for MVP, just return
    // the zip bytes and let the browser handle it via a <a download>.
    // Simpler: return the download URL for the browser.
    return new Response(
      JSON.stringify({
        artifactName: reportArtifact.name,
        downloadUrl: `/api/audit/${runId}/zip`,
        sizeBytes: reportArtifact.size_in_bytes,
      }),
      { headers: { "content-type": "application/json" } }
    );
  } catch (err) {
    return new Response(
      JSON.stringify({ error: String(err?.message || err) }),
      { status: 500, headers: { "content-type": "application/json" } }
    );
  }
}
