#!/usr/bin/env bash
# Store the Feishu bot credentials as GitHub Actions secrets, then run the
# workflow once to check that alerts go through.
#
#   ./setup-feishu.sh            # prompts for the App Secret (hidden input)
#   ./setup-feishu.sh --verify   # only re-run the workflow and show the result
set -euo pipefail

REPO="JhinInn/status-rss"
APP_ID="cli_aa66fa82a4781cf9"                         # Uriel Prime
RECEIVE_ID="ou_dddc9c1ccc496924727197312e58b873"      # open_id under this app

if [[ "${1:-}" != "--verify" ]]; then
  if [[ -z "${FEISHU_APP_SECRET:-}" ]]; then
    read -rsp "Uriel App Secret (开放平台 → 凭证与基础信息): " FEISHU_APP_SECRET </dev/tty
    echo
  fi

  # Check the secret before saving it, so a typo doesn't silently stop alerts.
  code=$(APP_SECRET="$FEISHU_APP_SECRET" python3 -c '
import json, os, urllib.error, urllib.request
req = urllib.request.Request(
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    data=json.dumps({"app_id": "'"$APP_ID"'", "app_secret": os.environ["APP_SECRET"]}).encode(),
    headers={"Content-Type": "application/json"})
try:
    body = urllib.request.urlopen(req, timeout=30).read()
except urllib.error.HTTPError as exc:
    body = exc.read()
print(json.loads(body).get("code"))')
  if [[ "$code" != "0" ]]; then
    echo "App Secret rejected by Feishu (code $code); nothing saved." >&2
    exit 1
  fi

  printf %s "$APP_ID" | gh secret set FEISHU_APP_ID -R "$REPO"
  printf %s "$FEISHU_APP_SECRET" | gh secret set FEISHU_APP_SECRET -R "$REPO"
  printf %s "$RECEIVE_ID" | gh secret set FEISHU_RECEIVE_ID -R "$REPO"
  unset FEISHU_APP_SECRET
fi

gh workflow run feeds.yml -R "$REPO"
sleep 5
run_id=$(gh run list -R "$REPO" --workflow feeds.yml --event workflow_dispatch --limit 1 \
  --json databaseId --jq '.[0].databaseId')
echo "Watching run $run_id ..."
gh run watch "$run_id" -R "$REPO" --exit-status >/dev/null || true
gh run view "$run_id" -R "$REPO" --json conclusion --jq '"conclusion: " + .conclusion'
gh run view "$run_id" -R "$REPO" --log \
  | grep -E 'incidents|sent:|would send|warning' | sed -E 's/^.*Z //' || true
