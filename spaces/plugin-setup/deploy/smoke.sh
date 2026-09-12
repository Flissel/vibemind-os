#!/usr/bin/env bash
# smoke.sh -- Rauchtest der fuenf registrierten Werkzeuge + der internen
# Schreibfunktion `schluessel_entgegennehmen` + Verbotsliste. Wiederholbar,
# raeumt auf, was es anlegt.
#
# NACHZIEHER BEHOBEN (Schluss-Fix G2, 2026-09-12): `ERWARTET` in Schritt 1
# traf bis hierher noch die VIER Werkzeuge von vor Aufgabe 5
# (`plugin_bedarf`, `plugin_installieren`, `plugin_werkzeug_binden`,
# `schluessel_entgegennehmen`) -- `server.WERKZEUGE` registriert seit
# Aufgabe 5 tatsaechlich `plugin_bedarf`, `eingabe_anfordern`,
# `einrichtung_status`, `plugin_installieren`, `plugin_werkzeug_binden`,
# und `schluessel_entgegennehmen` ist seither KEIN MCP-Werkzeug mehr
# (bleibt eine gewoehnliche Funktion, der interne Schreibweg des
# Formulars). `ERWARTET` prueft jetzt genau diese fuenf registrierten
# Namen. Der direkte Aufruf von `werkzeuge.schluessel_entgegennehmen` in
# Schritt 2 unten BLEIBT -- er prueft weiterhin, dass der Schreibweg bis
# zur echten Anbieter-Pruefung kommt -- fuehrt sie aber nirgends mehr als
# registriertes Werkzeug.
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
#     Beweis "Server startet + meldet 5 Werkzeuge" laeuft separat ueber
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

echo "1) Server startet und meldet seine fuenf Werkzeuge"
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
ERWARTET="eingabe_anfordern,einrichtung_status,plugin_bedarf,plugin_installieren,plugin_werkzeug_binden"
if [ "$WERKZEUG_NAMEN" = "$ERWARTET" ]; then
  echo "   ok   fuenf Werkzeuge: $WERKZEUG_NAMEN"
else
  echo "   FEHL Werkzeugliste stimmt nicht: '$WERKZEUG_NAMEN' != '$ERWARTET'"; ROT=$((ROT+1))
fi

echo "2) Jedes Werkzeug einmal aufrufen + Ergebnisform + Verbotsliste pruefen"
FAKE_WERT="offensichtlich-erfunden-kein-echtes-secret-smoke-$$"
SMOKE_REFERENZ="SMOKE_TEST_$$"
TOOL_JSON="$SERVER_LOG.tool-output.json"

# I5-Fix (Review Runde 1): die Pruefung der vier Werkzeugantworten laeuft
# jetzt KOMPLETT in Python -- inklusive der Formzusicherung ("ok:false mit
# den erwarteten Feldern", nicht nur "der Schluessel existiert", der bei
# einem hartcodierten dict-Literal ohnehin nie fehlen kann) -- und meldet
# sich ueber den Exit-Code. Das schliesst die zwei Luecken aus dem Review:
# ein Python-Absturz haette VORHER unter `set -e` die bash-Pruefschleife
# nie erreicht (toter Zweig); jetzt ist der Exit-Code selbst der Beweis.
if ! "$PY" - "$FAKE_WERT" "$SMOKE_REFERENZ" "$TOOL_JSON" <<'PYEOF'
import json, os, sys
sys.path.insert(0, ".")
fake_wert, referenz, ausgabe_pfad = sys.argv[1], sys.argv[2], sys.argv[3]

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
with open(ausgabe_pfad, "w", encoding="utf-8") as f:
    json.dump(ergebnisse, f)

rot = 0

# Verbotsliste: der erfundene Wert darf in KEINEM Feld irgendeiner Antwort
# auftauchen -- rekursiv, nicht nur an der Oberflaeche.
def alle_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from alle_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from alle_strings(v)
    elif isinstance(obj, str):
        yield obj

if any(fake_wert in s for s in alle_strings(ergebnisse)):
    print("   FEHL der erfundene Testwert taucht in einer Werkzeugantwort auf (Verbotsliste verletzt)")
    rot += 1
else:
    print("   ok   kein Werkzeug gibt den Testwert zurueck")

# Ergebnisform pruefen -- nicht nur "der Schluessel existiert" (der
# existiert bei einem hartcodierten dict-Literal immer), sondern "das
# Werkzeug hat tatsaechlich fail-soft geantwortet, wie ohne
# ROWBOAT_URL/ROWBOAT_API_KEY erwartet".
for name in ("plugin_bedarf", "plugin_installieren", "plugin_werkzeug_binden"):
    r = ergebnisse[name]
    if isinstance(r, dict) and r.get("ok") is False and isinstance(r.get("fehler"), str) and (
        "ROWBOAT_URL" in r["fehler"] or "ROWBOAT_API_KEY" in r["fehler"] or "Rowboat" in r["fehler"]
    ):
        print(f"   ok   {name}: fail-soft ohne Rowboat-Konfiguration ({r['fehler'][:60]})")
    else:
        print(f"   FEHL {name}: unerwartete Form {r!r}")
        rot += 1

# schluessel_entgegennehmen: muss bis zur echten Anbieter-Pruefung
# gekommen sein (status gesetzt, ok=False -- ein absichtlich erfundener
# Wert kann bei GitHub nie gut sein) UND darf NIE ok=True melden (das
# waere entweder ein echter GitHub-Erfolg mit einem "erfundenen" Wert --
# unmoeglich -- oder ein Bug).
s = ergebnisse["schluessel_entgegennehmen"]
if isinstance(s, dict) and s.get("ok") is False and s.get("referenz") == referenz \
        and isinstance(s.get("status"), int):
    print(f"   ok   schluessel_entgegennehmen: bis zur Anbieter-Pruefung gekommen (status={s['status']})")
else:
    print(f"   FEHL schluessel_entgegennehmen: unerwartete Form {s!r}")
    rot += 1

sys.exit(1 if rot else 0)
PYEOF
then
  ROT=$((ROT+1))
fi

echo "3) Aufraeumen: die SMOKE_-Zeile aus Supabase entfernen (falls angelegt)"
CONTAINER="$(docker ps --format '{{.Names}}' | grep -m1 supabase-db || true)"
if [ -n "$CONTAINER" ]; then
  set +e
  docker exec -i "$CONTAINER" psql -U postgres -d postgres -v ON_ERROR_STOP=1 -tA >/dev/null 2>"$SERVER_LOG.cleanup-err" <<SQL
DELETE FROM vault.secrets WHERE id IN (
  SELECT vault_secret_id FROM plugin_setup.einrichtungen
   WHERE referenz_name = '${SMOKE_REFERENZ}' AND vault_secret_id IS NOT NULL);
DELETE FROM plugin_setup.einrichtungen WHERE referenz_name = '${SMOKE_REFERENZ}';
SQL
  CLEANUP_RC=$?
  set -e
  if [ "$CLEANUP_RC" -eq 0 ]; then
    echo "   ok   aufgeraeumt"
  else
    echo "   FEHL Aufraeumen scheiterte (rc=$CLEANUP_RC):"; cat "$SERVER_LOG.cleanup-err" >&2
    ROT=$((ROT+1))
  fi
  rm -f "$SERVER_LOG.cleanup-err"
else
  echo "   uebersprungen (kein supabase-db-Container -- dann gibt es auch keine Zeile)"
fi

echo
echo "NICHT gepueft (auf dieser Maschine nicht verfuegbar, s. Kopf dieses Skripts):"
echo "  - OpenFang-Uebergabe (POST /api/credentials/store, 127.0.0.1:4273)"
echo "  - Rowboat-Install/Tool-Bindung gegen eine echte Instanz"
echo "  - MCP-Wire-Roundtrip durch das openclaw-Gateway (deploy/anbinden.sh)"
echo "  - eingabe_anfordern/einrichtung_status selbst (Schritt 2 ruft nur"
echo "    plugin_bedarf/plugin_installieren/plugin_werkzeug_binden + die"
echo "    interne Funktion schluessel_entgegennehmen direkt auf -- die zwei"
echo "    anderen registrierten Werkzeuge sind nur in Schritt 1 an der"
echo "    Registrierung selbst geprueft, nicht an einem echten Aufruf)"

if [ "$ROT" -ne 0 ]; then
  echo "ROT: $ROT" >&2
  exit "$ROT"
fi
echo "Rauchtest bestanden (im oben genannten Geltungsbereich)."
