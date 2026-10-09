#!/usr/bin/env bash
# deploy/update.sh - spielt den Stand von origin/master fuer MiroFish auf der VM ein.
#
# Einziger Update-Weg fuer MiroFish seit dem Umzug auf die VM (2026-10-09);
# am PC laeuft MiroFish nicht mehr. Laeuft im eigenen Laufzeit-Checkout
# ~/mirofish-os (sparse: nur spaces/mirofish). Secrets in ~/mirofish-betrieb/.env.
#
# Ablauf: fetch -> checkout origin/master -> Rueckweg-Tag des laufenden Images
# -> build -> up -d -> Gesundheitspruefung /health. Scheitert die Pruefung,
# faehrt es das vorherige Image wieder hoch.
set -euo pipefail
export LC_ALL=C

WURZEL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # .../spaces/mirofish
REPO="$(git -C "$WURZEL" rev-parse --show-toplevel)"
export MIROFISH_BETRIEB="${MIROFISH_BETRIEB:-$HOME/mirofish-betrieb}"
COMPOSE=(docker compose -f "$WURZEL/deploy/docker-compose.vm.yml" --env-file "$MIROFISH_BETRIEB/.env")
GESUND_URL="http://127.0.0.1:5101/health"

[ -f "$MIROFISH_BETRIEB/.env" ] || { echo "FEHLER: $MIROFISH_BETRIEB/.env fehlt" >&2; exit 2; }
mkdir -p "$MIROFISH_BETRIEB/uploads"

if [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]; then
  echo "FEHLER: nachgefuehrte Dateien im Laufzeit-Checkout veraendert - abgebrochen" >&2
  git -C "$REPO" status --short --untracked-files=no >&2
  exit 3
fi

ALT="$(git -C "$REPO" rev-parse --short HEAD)"
git -C "$REPO" fetch --quiet origin master
git -C "$REPO" checkout --quiet --detach origin/master
NEU="$(git -C "$REPO" rev-parse --short HEAD)"
echo "Stand: $ALT -> $NEU"

if docker image inspect mirofish-offline:aktuell >/dev/null 2>&1; then
  docker tag mirofish-offline:aktuell "mirofish-offline:rueckweg-$ALT"
  echo "Rueckweg-Image: mirofish-offline:rueckweg-$ALT"
fi

"${COMPOSE[@]}" build mirofish
"${COMPOSE[@]}" up -d neo4j mirofish

for i in $(seq 1 60); do
  if curl -fsS -m 3 "$GESUND_URL" | grep -q '"ok"'; then
    echo "MiroFish gesund auf $NEU"
    exit 0
  fi
  sleep 5
done

echo "FEHLER: $GESUND_URL nach 5 min nicht gesund" >&2
if docker image inspect "mirofish-offline:rueckweg-$ALT" >/dev/null 2>&1; then
  echo "Rueckweg auf $ALT" >&2
  docker tag "mirofish-offline:rueckweg-$ALT" mirofish-offline:aktuell
  git -C "$REPO" checkout --quiet --detach "$ALT"
  "${COMPOSE[@]}" up -d --no-build mirofish
fi
exit 1
