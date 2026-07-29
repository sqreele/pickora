#!/usr/bin/env bash
set -euo pipefail
docker compose run --rm worker
docker compose restart web
