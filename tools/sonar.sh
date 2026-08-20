#!/usr/bin/env bash
# Local SonarQube scan, self-contained: starts a SonarQube server container,
# waits for it, provisions a throwaway token, and runs the scanner over the
# repo (sonar-project.properties defines what counts as code).
#
# Needs docker. First run downloads the images (~1 GB) and takes a few
# minutes; afterwards the server keeps its state in the named volume and
# rescans are quick. Results: http://localhost:9000 (admin/admin on first
# login; it will ask you to change it).
#
# Usage:  bash tools/sonar.sh          # start server if needed + scan
#         bash tools/sonar.sh stop     # stop the server container
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "${1:-}" = "stop" ]; then
  docker stop tar-sonarqube >/dev/null && echo "SonarQube stopped."
  exit 0
fi

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }

if ! docker ps --format '{{.Names}}' | grep -q '^tar-sonarqube$'; then
  echo "== starting SonarQube (community edition) =="
  docker run -d --name tar-sonarqube \
    -p 9000:9000 \
    -v tar-sonarqube-data:/opt/sonarqube/data \
    -v tar-sonarqube-extensions:/opt/sonarqube/extensions \
    sonarqube:community >/dev/null 2>&1 \
    || docker start tar-sonarqube >/dev/null
fi

echo -n "== waiting for the server "
for _ in $(seq 1 120); do
  status=$(curl -s http://localhost:9000/api/system/status | grep -o '"status":"[A-Z]*"' || true)
  [ "$status" = '"status":"UP"' ] && break
  echo -n "."
  sleep 3
done
echo " up =="

# a scan token, minted fresh each run (the default admin password works only
# until the UI forces a change; export SONAR_TOKEN to use your own)
if [ -z "${SONAR_TOKEN:-}" ]; then
  SONAR_TOKEN=$(curl -s -u admin:admin -X POST \
    "http://localhost:9000/api/user_tokens/generate?name=scan-$(date +%s)" \
    | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
  if [ -z "$SONAR_TOKEN" ]; then
    echo "Could not mint a token with admin/admin; export SONAR_TOKEN with a" >&2
    echo "token from http://localhost:9000/account/security and rerun." >&2
    exit 1
  fi
fi

echo "== scanning =="
docker run --rm --network host \
  -e SONAR_HOST_URL=http://localhost:9000 \
  -e SONAR_TOKEN="$SONAR_TOKEN" \
  -v "$PWD:/usr/src" \
  sonarsource/sonar-scanner-cli

echo
echo "Done: http://localhost:9000/dashboard?id=toolassisted-run-website"
