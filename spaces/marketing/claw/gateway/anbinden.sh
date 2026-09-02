#!/usr/bin/env bash
# anbinden.sh — Abnahme des marketing-claw-Gateways. Wiederholbar.
#
# HISTORIE: Die erste Fassung setzte hier einen Rowboat-Bearer-Schluessel
# ins Laufzeit-Volume (Muster sales-claw). Das ist Geschichte: Container
# erreichen im WSL-Mirrored-Modus kein LAN (gemessen 02.09.2026, fetch
# failed gegen 192.168.178.65:3100), darum liegt der Rowboat-Lesezugriff
# jetzt als Passthrough im Host-Sidecar (spaces/marketing/claw/werkzeuge.py,
# _rowboat) — der Schluessel bleibt im Host-Prozess und erreicht das
# Gateway-Volume NIE. Dieses Skript prueft nur noch: Saat einspielen,
# Werkzeuge sichtbar, kein Sende-Werkzeug, kein Schreib-Werkzeug.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HIER"

if [ "$(docker inspect -f '{{.State.Status}}' marketing-claw 2>/dev/null || echo fehlt)" != "running" ]; then
  echo "ABBRUCH: marketing-claw laeuft nicht (docker compose up -d in $HIER)." >&2
  exit 1
fi

echo "1) Saat -> Laufzeit-Volume (Konfig + Workspace), reload"
docker compose cp config/openclaw.json marketing-claw:/home/node/.openclaw/openclaw.json
docker compose cp config/workspace/AGENTS.md marketing-claw:/home/node/.openclaw/workspace/AGENTS.md

# Die Saat traegt KEIN Gateway-Token (Geheimnisse gehoeren nicht ins Git) —
# das Kopieren loescht darum jedes vorhandene. Ohne Token weigert sich
# `openclaw agent`, eine Websocket-Sitzung zu oeffnen
# (GatewayCredentialsRequiredError), und der Gateway wuerfe sich sonst bei
# jedem Start ein neues, das niemand kennt. Also hier eins setzen, wenn keins
# dasteht — idempotent, und es bleibt im Volume.
if [ -z "$(docker compose exec -T marketing-claw openclaw config get gateway.auth.token 2>/dev/null | tr -d '\r\n "')" ]; then
  echo "   Gateway-Token fehlt -> neues erzeugen"
  NEUES_TOKEN="$(head -c 32 /dev/urandom | base64 | tr -d '=+/' | cut -c1-40)"
  docker compose exec -T -e NEU="$NEUES_TOKEN" marketing-claw \
    node -e 'require("node:child_process").execFileSync("openclaw",["config","set","gateway.auth.token",process.env.NEU],{stdio:"ignore"})'
  unset NEUES_TOKEN
  docker compose restart marketing-claw >/dev/null
  sleep 5
fi

docker compose exec -T marketing-claw openclaw mcp reload >/dev/null

echo "2) Probe"
ROT=0
PROBE="$(docker compose exec -T marketing-claw openclaw mcp probe marketing --json 2>&1 || true)"
for w in statistik kampagne_entwerfen ad_texte_entwerfen layout_entwerfen \
         publikum_vorschlagen posteingang_lesen kampagnen_auflisten \
         wissensquellen wissensquelle dokumente; do
  if printf '%s' "$PROBE" | grep -q "marketing__$w"; then echo "   ok   $w"; else echo "   FEHL $w"; ROT=$((ROT+1)); fi
done
# Negativ: nichts Sendendes, nichts Schreibendes, kein Rowboat-Server im Gateway.
for schlecht in send approve rowboat_quelle_anlegen rowboat_dokumente_schreiben; do
  if printf '%s' "$PROBE" | grep -qi "\"marketing__[a-z_]*$schlecht"; then
    echo "   FEHL verbotenes Werkzeug sichtbar: $schlecht"; ROT=$((ROT+1))
  fi
done
if docker compose exec -T marketing-claw openclaw mcp list 2>&1 | grep -q rowboat; then
  echo "   FEHL Gateway kennt noch einen rowboat-Server (gehoert in den Sidecar)"; ROT=$((ROT+1))
fi

if [ "$ROT" -ne 0 ]; then
  echo "--- Probe (Auszug):"; printf '%s\n' "$PROBE" | head -30
  echo "ROT: $ROT" >&2
  exit "$ROT"
fi
echo "Gateway abgenommen: 10 Werkzeuge, nichts Sendendes, nichts Schreibendes."
