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
# Fertigkeiten (Handwerksregeln je Kanal). AGENTS.md verweist darauf; ohne
# diese Kopie zeigt der Verweis ins Leere und der Agent faellt auf sein
# Gedaechtnis zurueck — genau der Zustand, aus dem siebenmal dieselbe
# Aufzaehlung entstand.
docker compose exec -T marketing-claw mkdir -p /home/node/.openclaw/workspace/skills
docker compose cp config/workspace/skills/. marketing-claw:/home/node/.openclaw/workspace/skills/

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
         kampagne_pruefen publikum_vorschlagen posteingang_lesen \
         kampagnen_auflisten wissensquellen wissensquelle dokumente \
         videos video_transkript wissen_fragen entwuerfe_lesen post_ablegen \
         pdf_erstellen entwurf_holen pdf_aus_entwurf; do
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

# Laura-Werkzeuge (zweiter MCP-Server im Shim, seit 12.09.2026). Sie kommen
# NICHT ueber den marketing-Server, sondern direkt aus dem laura-MCP — deshalb
# eine eigene Probe. `laura_api` MUSS fehlen: es reicht jede API-Route durch
# und haette die Freigabeliste daneben aufgehoben.
# Laura haengt seit 12.09.2026 als ZWEITER MCP-Server im Shim. Er kommt NICHT
# ueber openclaws MCP-Schicht, sondern ueber SHIM_EXTRA_MCP_CONFIG direkt in
# die Claude-CLI — in `$PROBE` oben kann er also gar nicht auftauchen. Also
# den Server selbst befragen.
echo "3) Laura-Werkzeuge (eigener MCP im Shim)"
if python "$HIER/../shim/laura_probe.py"; then
  echo "   ok   Freigabeliste deckt sich mit dem, was der Server fuehrt"
else
  echo "   FEHL Laura-MCP: siehe Ausgabe oben"; ROT=$((ROT+1))
fi

echo "4) Fertigkeiten"
FERTIG="$(docker compose exec -T marketing-claw openclaw skills 2>&1 || true)"
for f in email-kampagne whatsapp-nachricht; do
  if printf '%s' "$FERTIG" | grep -q "$f"; then echo "   ok   $f"; else echo "   FEHL $f"; ROT=$((ROT+1)); fi
done

if [ "$ROT" -ne 0 ]; then
  echo "--- Probe (Auszug):"; printf '%s\n' "$PROBE" | head -30
  echo "ROT: $ROT" >&2
  exit "$ROT"
fi
echo "Gateway abgenommen: 19 Marketing- + 24 Laura-Werkzeuge, 2 Fertigkeiten, nichts Sendendes, nichts Schreibendes."
