#!/usr/bin/env bash
# anbinden.sh -- Abnahme des plugin-setup-Gateways. Wiederholbar. Muster:
# spaces/marketing/claw/gateway/anbinden.sh.
#
# Setzt Saat (Konfig + Workspace) ins Laufzeit-Volume, erzeugt ein
# Gateway-Token falls keins da ist, ladet die MCP-Werkzeuge (`openclaw mcp
# reload`) und probt sie (`openclaw mcp probe`) -- prueft: alle vier
# Werkzeuge sichtbar, nichts Verbotenes (kein Werkzeug, das einen Wert
# entgegennimmt UND zurueckgibt -- die Namen selbst verraten das nicht,
# das prueft deploy/smoke.sh inhaltlich).
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HIER"

if [ "$(docker inspect -f '{{.State.Status}}' plugin-setup-claw 2>/dev/null || echo fehlt)" != "running" ]; then
  echo "ABBRUCH: plugin-setup-claw laeuft nicht (docker compose up -d in $HIER)." >&2
  exit 1
fi

echo "1) Saat -> Laufzeit-Volume (Konfig + Workspace), reload"
docker compose cp config/openclaw.json plugin-setup-claw:/home/node/.openclaw/openclaw.json
docker compose cp config/workspace/AGENTS.md plugin-setup-claw:/home/node/.openclaw/workspace/AGENTS.md

# Muster marketing-claw/anbinden.sh: die Saat traegt kein Gateway-Token
# (Geheimnisse gehoeren nicht ins Git); das Kopieren loescht darum jedes
# vorhandene -- hier idempotent eins setzen, wenn keins dasteht.
if [ -z "$(docker compose exec -T plugin-setup-claw openclaw config get gateway.auth.token 2>/dev/null | tr -d '\r\n "')" ]; then
  echo "   Gateway-Token fehlt -> neues erzeugen"
  NEUES_TOKEN="$(head -c 32 /dev/urandom | base64 | tr -d '=+/' | cut -c1-40)"
  docker compose exec -T -e NEU="$NEUES_TOKEN" plugin-setup-claw \
    node -e 'require("node:child_process").execFileSync("openclaw",["config","set","gateway.auth.token",process.env.NEU],{stdio:"ignore"})'
  unset NEUES_TOKEN
  docker compose restart plugin-setup-claw >/dev/null
  sleep 5
fi

docker compose exec -T plugin-setup-claw openclaw mcp reload >/dev/null

echo "2) Probe"
ROT=0
PROBE="$(docker compose exec -T plugin-setup-claw openclaw mcp probe plugin-setup --json 2>&1 || true)"
for w in plugin_bedarf schluessel_entgegennehmen plugin_installieren plugin_werkzeug_binden; do
  if printf '%s' "$PROBE" | grep -q "plugin-setup__$w"; then echo "   ok   $w"; else echo "   FEHL $w"; ROT=$((ROT+1)); fi
done
# Negativ: kein zweiter MCP-Server im Gateway (kein direkter OpenFang-/
# Rowboat-Zugriff am Agenten vorbei -- alles laeuft ueber die vier
# Werkzeuge des Sidecars).
if docker compose exec -T plugin-setup-claw openclaw mcp list 2>&1 | grep -viE 'plugin-setup|^$' | grep -q .; then
  echo "   FEHL Gateway kennt einen weiteren MCP-Server ausser plugin-setup"; ROT=$((ROT+1))
fi

if [ "$ROT" -ne 0 ]; then
  echo "--- Probe (Auszug):"; printf '%s\n' "$PROBE" | head -30
  echo "ROT: $ROT" >&2
  exit "$ROT"
fi
echo "Gateway abgenommen: 4 Werkzeuge, kein weiterer MCP-Server."
