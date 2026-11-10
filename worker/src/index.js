// Dispatches the feed workflow every minute. GitHub's own schedule runs at
// most every 5 minutes and is often delayed much longer.
const WORKFLOW_URL =
  "https://api.github.com/repos/JhinInn/status-rss/actions/workflows/feeds.yml/dispatches";

export default {
  async scheduled(controller, env, ctx) {
    const resp = await fetch(WORKFLOW_URL, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${env.GH_TOKEN}`,
        "User-Agent": "status-rss-trigger",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ ref: "main" }),
    });
    if (!resp.ok) {
      // Throwing marks the invocation as failed in the Workers dashboard.
      throw new Error(`dispatch failed: ${resp.status} ${await resp.text()}`);
    }
  },
};
