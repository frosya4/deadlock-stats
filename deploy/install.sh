#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install.sh" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
    "$VERSION_CODENAME" "$(dpkg --print-architecture)" > /etc/apt/sources.list.d/docker.sources
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

cd "$(dirname "$(realpath "$0")")/.."
public_ip=$(curl -fsS https://api.ipify.org)
site_host=${SITE_HOST:-${public_ip//./-}.sslip.io}
caddy_tls=""
if [[ "$site_host" == "localhost" ]]; then
  caddy_tls="tls internal"
fi
install -d -m 0755 deploy/data
printf 'SITE_HOST=%s\nCADDY_TLS=%s\n' "$site_host" "$caddy_tls" > deploy/.env
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q '^Status: active'; then
  ufw allow 80/tcp
  ufw allow 443/tcp
fi
docker compose -f deploy/docker-compose.yml up -d --build
printf 'Service is available at https://%s\n' "$site_host"

