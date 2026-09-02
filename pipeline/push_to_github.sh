#!/usr/bin/env bash
# Push the clean windthrow repo to GitHub.
#
# Usage:
#   GITHUB_TOKEN=ghp_xxx [GITHUB_USER=username] [REPO_NAME=sentinel1-windthrow] [PRIVATE=true] \
#     bash /home/z/my-project/scripts/push_to_github.sh
#
# GITHUB_TOKEN: classic PAT with "repo" scope (or fine-grained with
#   Administration:write to create the repo + Contents:write to push).
# If GITHUB_USER is omitted, the login is resolved via GET /user.
# The token is stripped from the remote URL after the push.
set -euo pipefail

TOKEN="${GITHUB_TOKEN:?Need GITHUB_TOKEN}"
REPO_DIR="/home/z/my-project/github_repo"
NAME="${REPO_NAME:-sentinel1-windthrow}"
PRIV="${PRIVATE:-true}"
API="https://api.github.com"
RESP="/home/z/my-project/scripts/.gh_resp.json"

auth=(-H "Authorization: token ${TOKEN}" \
      -H "Accept: application/vnd.github+json" \
      -H "X-GitHub-Api-Version: 2022-11-28")

# 1) resolve login
USER_NAME="${GITHUB_USER:-}"
if [ -z "$USER_NAME" ]; then
  USER_NAME=$(curl -sS "${auth[@]}" "$API/user" \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('login') or _err)" 2>/dev/null \
    || curl -sS "${auth[@]}" "$API/user" | python3 -c "import sys,json;print(json.load(sys.stdin)['login'])")
fi
echo "== GitHub user: ${USER_NAME} =="

# 2) create repo if missing (201=created, 422=already exists -> fine)
HTTP=$(curl -sS -o "$RESP" -w '%{http_code}' -X POST "${auth[@]}" "$API/user/repos" \
  -d "{\"name\":\"${NAME}\",\"private\":${PRIV},\"description\":\"QGIS plugin + pipeline: windthrow detection from Sentinel-1 SAR (WI = dVV + dVH, Ruetshi et al. 2019) on Planetary Computer RTC\"}" || echo 000)
echo "== create repo HTTP ${HTTP} =="
case "$HTTP" in
  201) echo "created new repo";;
  422) echo "repo already exists, reuse";;
  000) echo "WARNING: API unreachable (network/proxy) - assuming repo already exists and trying push";;
  *)   echo "API error response:"; head -c 600 "$RESP"; echo; exit 1;;
esac
rm -f "$RESP"

# 3) push via git-over-HTTPS (github.com endpoint) — one-off URL so the
#    token is never stored in .git/config
cd "$REPO_DIR"
if git remote | grep -q '^origin$'; then
    git remote set-url origin "https://github.com/${USER_NAME}/${NAME}.git"
else
    git remote add origin "https://github.com/${USER_NAME}/${NAME}.git"
fi
# redact token from push output just in case
git push "https://x-access-token:${TOKEN}@github.com/${USER_NAME}/${NAME}.git" main 2>&1 | sed "s#${TOKEN}#ghp_REDACTED#g"

echo
echo "== DONE: https://github.com/${USER_NAME}/${NAME} =="
