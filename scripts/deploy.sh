#!/usr/bin/env bash
# Usage: bash scripts/deploy.sh ENV_FILE ACTION [--https]
# Never prints the resolved Compose configuration or credentials.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: bash scripts/deploy.sh ENV_FILE {check|build|db|migrate|start|status|logs|smoke} [--https]" >&2
  exit 2
fi
env_file=$1
action=$2
[[ -f "$env_file" ]] || { echo "Environment file does not exist: $env_file" >&2; exit 2; }
compose=(docker compose --env-file "$env_file" -f compose.deploy.yml)
if [[ $# -eq 3 ]]; then
  [[ $3 == --https ]] || { echo "Unknown option: $3" >&2; exit 2; }
  compose+=(-f compose.https.yml)
fi

case "$action" in
  check) "${compose[@]}" config -q ;;
  build) "${compose[@]}" build app ;;
  db) "${compose[@]}" up -d --wait db ;;
  migrate) "${compose[@]}" run --rm --no-deps app alembic upgrade head ;;
  start) "${compose[@]}" up -d --no-build --wait ;;
  status) "${compose[@]}" ps ;;
  logs) "${compose[@]}" logs --tail=80 ;;
  smoke)
    "${compose[@]}" exec -T app python - <<'PY'
import json
import urllib.request

def get(path):
    with urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=10) as response:
        assert response.status == 200, path
        return response.read()

assert b'<div id="root">' in get("/")
assert json.loads(get("/api/health"))["ok"] is True
assert isinstance(json.loads(get("/api/products")), list)
assert len(json.loads(get("/api/prompts"))["prompts"]) == 6
print("PASS: frontend, API, PostgreSQL, six seeded prompts. No AI calls made.")
PY
    ;;
  *) echo "Unknown action: $action" >&2; exit 2 ;;
esac
