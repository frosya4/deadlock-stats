#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$(realpath "$0")")/.."
git pull --ff-only
docker compose -f deploy/docker-compose.yml up -d --build

