#!/usr/bin/env bash
# smoke.sh -- Rauchtest der vier Werkzeuge + Verbotsliste. Wiederholbar,
# raeumt auf, was es anlegt.
#
# EHRLICHER GELTUNGSBEREICH (2026-09-11, s. task-6-report.md fuer Details):
# Dieses Skript kann NUR pruefen, was auf dieser Maschine tatsaechlich
# laeuft. Heute laeuft: der host-Python + der echte supabase-db-Container.
# Es laeuft NICHT: der isolierte OpenFang (127.0.0.1:4273, eigener
# OPENFANG_HOME -- der Branch mit /api/credentials/store ist ungebaut/
# ungemerged), Rowboat, das openclaw-Gateway (:18793/:18896). Darum:
#   - Werkzeuge werden DIREKT importiert und aufgerufen (dieselben
#     Funktionsobjekte, die server.py bei FastMCP registriert) -- KEIN
#     Roundtrip durch das MCP-Wire-Protokoll/den openclaw-Container. Der
#     Beweis "Server startet + meldet 4 Werkzeuge" laeuft separat ueber
#     server.py selbst (Schritt 1 unten).
#   - `schluessel_entgegennehmen` laeuft bis zur echten Anbieter-Pruefung
#     (Aufgabe 5, ein echter, absichtlich scheiternder Aufruf gegen
#     api.github.com mit einem offensichtlich erfundenen Wert) und bricht
#     dort sauber mit `fehlgeschlagen` ab -- die OpenFang-Uebergabe wird
#     NICHT erreicht und NICHT als Erfolg behauptet.
#   - `plugin_bedarf`/`plugin_installieren`/`plugin_werkzeug_binden` laufen
#     ohne ROWBOAT_URL/ROWBOAT_API_KEY -- sie muessen freundlich scheitern
#     (fail-soft), nicht abstuerzen.
# Das ist eine bewusste, im Bericht benannte Luecke -- kein vorgetaeuschtes
# Gruen.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HIER"

PY="${PLUGIN_SETUP_PYTHON:-python}"
PORT="${PLUGIN_SETUP_MCP_PORT:-8131}"
ROT=0

echo "1) Server startet und meldet seine vier Werkzeuge"
SERVER_LOG="$(mktemp)"
"$PY" server.py >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!
cleanup_server() {
  kill "$SERVER_PID" >/dev/null 2>&1 || true
  wait "$SERVER_PID" 2>/dev/null || true
  rm -f "$SERVER_LOG"
}
trap cleanup_server EXIT

for _ in $(seq 1 20); do
  if curl -s -o /dev/null "http://127.0.0.1:${PORT}/mcp"; then break; fi
  sleep 0.5
done
if ! curl -s -o /dev/null "http://127.0.0.1:${PORT}/mcp"; then
  echo "   FEHL Server antwortet nicht auf :${PORT}"; cat "$SERVER_LOG"; exit 1
fi
echo "   ok   Server hoert auf 0.0.0.0:${PORT}"

WERKZEUG_NAMEN="$("$PY" - <<'PYEOF'
import asyncio, server
from mcp.server.fastmcp import FastMCP
s = FastMCP("plugin-setup-smoke", host=server.HOST, port=server.PORT)
for fn in server.WERKZEUGE:
    s.tool()(fn)
async def go():
    return sorted(t.name for t in await s.list_tools())
print(",".join(asyncio.run(go())))
PYEOF
)"
ERWARTET="plugin_bedarf,plugin_installieren,plugin_werkzeug_binden,schluessel_entgegennehmen"
if [ "$WERKZEUG_NAMEN" = "$ERWARTET" ]; then
  echo "   ok   vier Werkzeuge: $WERKZEUG_NAMEN"
else
  echo "   FEHL Werkzeugliste stimmt nicht: '$WERKZEUG_NAMEN' != '$ERWARTET'"; ROT=$((ROT+1))
fi

echo "2) Jedes Werkzeug einmal aufrufen + Verbotsliste pruefen"
FAKE_WERT="offensichtlich-erfunden-kein-echtes-secret-smoke-$$"
SMOKE_REFERENZ="SMOKE_TEST_$$"

AUSGABE="$("$PY" - "$FAKE_WERT" "$SMOKE_REFERENZ" <<'PYEOF'
import json, os, sys
sys.path.insert(0, ".")
fake_wert, referenz = sys.argv[1], sys.argv[2]

# Rowboat bewusst NICHT konfiguriert -- die drei Rowboat-Werkzeuge muessen
# hier freundlich scheitern (fail-soft), kein Absturz.
os.environ.pop("ROWBOAT_URL", None)
os.environ.pop("ROWBOAT_API_KEY", None)

import werkzeuge

ergebnisse = {
    "plugin_bedarf": werkzeuge.plugin_bedarf("smoke-projekt", "demo-plugin"),
    "plugin_installieren": werkzeuge.plugin_installieren("smoke-projekt", "demo-plugin"),
    "plugin_werkzeug_binden": werkzeuge.plugin_werkzeug_binden("smoke-projekt", "demo-plugin", "a" * 64),
    # Echter Aufruf bis zur Anbieter-Pruefung (Aufgabe 5) -- scheitert
    # absichtlich (offensichtlich erfundener Wert gegen api.github.com),
    # OpenFang wird NIE erreicht.
    "schluessel_entgegennehmen": werkzeuge.schluessel_entgegennehmen(
        "smoke-projekt", "demo-plugin", referenz, "bearer", fake_wert),
}
print(json.dumps(ergebnisse))
PYEOF
)"

echo "$AUSGABE" > "$SERVER_LOG.tool-output.json"
if echo "$AUSGABE" | grep -qF -- "$FAKE_WERT"; then
  echo "   FEHL der erfundene Testwert taucht in einer Werkzeugantwort auf (Verbotsliste verletzt)"
  ROT=$((ROT+1))
else
  echo "   ok   kein Werkzeug gibt den Testwert zurueck"
fi

for w in plugin_bedarf plugin_installieren plugin_werkzeug_binden schluessel_entgegennehmen; do
  if echo "$AUSGABE" | "$PY" -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if '$w' in d else 1)"; then
    echo "   ok   $w wurde aufgerufen und hat geantwortet (siehe $SERVER_LOG.tool-output.json)"
  else
    echo "   FEHL $w hat nicht geantwortet"; ROT=$((ROT+1))
  fi
done

echo "3) Aufraeumen: die SMOKE_-Zeile aus Supabase entfernen (falls angelegt)"
CONTAINER="$(docker ps --format '{{.Names}}' | grep -m1 supabase-db || true)"
if [ -n "$CONTAINER" ]; then
  docker exec -i "$CONTAINER" psql -U postgres -d postgres -tA >/dev/null 2>&1 <<SQL || true
DELETE FROM vault.secrets WHERE id IN (
  SELECT vault_secret_id FROM plugin_setup.einrichtungen
   WHERE referenz_name = '${SMOKE_REFERENZ}' AND vault_secret_id IS NOT NULL);
DELETE FROM plugin_setup.einrichtungen WHERE referenz_name = '${SMOKE_REFERENZ}';
SQL
  echo "   ok   aufgeraeumt"
else
  echo "   uebersprungen (kein supabase-db-Container -- dann gibt es auch keine Zeile)"
fi

echo
echo "NICHT gepueft (auf dieser Maschine nicht verfuegbar, s. Kopf dieses Skripts):"
echo "  - OpenFang-Uebergabe (POST /api/credentials/store, 127.0.0.1:4273)"
echo "  - Rowboat-Install/Tool-Bindung gegen eine echte Instanz"
echo "  - MCP-Wire-Roundtrip durch das openclaw-Gateway (deploy/anbinden.sh)"

if [ "$ROT" -ne 0 ]; then
  echo "ROT: $ROT" >&2
  exit "$ROT"
fi
echo "Rauchtest bestanden (im oben genannten Geltungsbereich)."
