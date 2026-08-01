#!/usr/bin/env bash
set -euo pipefail

# The worker and scheduler use the same locally built image.  Rebuild it before
# generating public files so pipeline fixes are not hidden by a stale image.
docker compose build scheduler
docker compose run --rm worker
docker compose up -d --force-recreate scheduler web
