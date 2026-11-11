#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "GH_TOKEN is required" >&2
  exit 1
fi

repo_url="https://github.com/djm-xjtu/stock-fear-greed.git"
push_url="https://x-access-token:${GH_TOKEN}@github.com/djm-xjtu/stock-fear-greed.git"
commit_date="$(
  python3 - <<'PY' || true
import json
from pathlib import Path

u = json.loads(Path("frontend/data/universe.json").read_text())
dates = [
  it.get("as_of")
  for it in (u.get("items") or [])
  if it.get("as_of") and it.get("symbol") != "SKHY"
]
print(max(dates) if dates else "")
PY
)"
if [[ -z "${commit_date}" ]]; then
  commit_date="$(date +%F)"
fi
commit_message="chore(data): daily refresh ${commit_date}"

if ! git config user.name >/dev/null; then
  git config user.name "Aime Bot"
fi
if ! git config user.email >/dev/null; then
  git config user.email "aime-bot"
fi

git remote set-url origin "${repo_url}"
git add backend/data frontend/data docs scripts/daily_update.sh

if git diff --cached --quiet; then
  echo "No changes to commit"
else
  git commit -m "${commit_message}"
fi

git push "${push_url}" HEAD:main >/tmp/stock-fear-greed-push.log 2>&1 || {
  perl -pe 's#https://[^/@]+@github.com/#https://***@github.com/#g; s#ghp_[A-Za-z0-9_]+#***REDACTED***#g' /tmp/stock-fear-greed-push.log >&2
  rm -f /tmp/stock-fear-greed-push.log
  exit 1
}
rm -f /tmp/stock-fear-greed-push.log
git remote set-url origin "${repo_url}"
