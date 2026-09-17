#!/usr/bin/env bash
# Only operates on the isolated staging project; never changes production Caddy.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ $# -ne 1 ]]; then
  echo "Usage: bash scripts/staging.sh {check|db|migrate|start|status|logs|stop}" >&2
  exit 2
fi
compose=(docker compose -p meta-creative-staging --env-file .deploy/staging.env -f compose.staging.yml)
case "$1" in
  check) "${compose[@]}" config -q ;;
  db) "${compose[@]}" up -d --wait staging-db ;;
  migrate) "${compose[@]}" run --rm --no-deps staging-app alembic upgrade head ;;
  start) "${compose[@]}" up -d --no-build --wait ;;
  status) "${compose[@]}" ps ;;
  logs) "${compose[@]}" logs --tail=80 ;;
  stop) "${compose[@]}" stop ;;
  *) echo "Unknown action: $1" >&2; exit 2 ;;
esac
