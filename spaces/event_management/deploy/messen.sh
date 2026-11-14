#!/usr/bin/env bash
# deploy/messen.sh -- Messpunkte der Etappe 3 (Spec "Offene Punkte", Ruling S7) gegen ein
# TEST-Paar auf offload-vm. Anleitung, Reihenfolge, Abbruchkriterien: deploy/messen.md.
#
#   bash deploy/messen.sh --ich-habe-ok <schritt> [argument]
#
# OHNE --ich-habe-ok TUT DAS SKRIPT NICHTS: Es prueft nur seine Argumente (Shell-Builtins)
# und endet mit 2, bevor irgendein Programm laeuft. Den Schalter setzt nur, wer Felix' OK
# fuer genau diese Messung hat und den Claim (WORKBOARD bzw. maschinenweite Koordination)
# eingetragen hat.
#
# Ziel ist ausschliesslich das Testpaar event-messung-shim/-claw (dazu, ab `testinstanz`,
# event-messung-pg/-mcp) mit eigenem Volume, eigenem Test-Bot und eigenem Budget. Der
# Betrieb wird nie angefasst: kein `docker compose`, kein Container ohne Praefix
# event-messung-, kein Live-Token. Alle Testcontainer teilen den Netz-Namensraum des
# Test-Shims (`--network container:`): kein neues Docker-Netz (die Adresspools der VM sind
# erschoepft), alles spricht sich ueber 127.0.0.1 an.
#
# Werte (Tokens, Schluessel, Chat-IDs) nur ueber stdin, nie als Argument, nie in einer
# Ausgabe. Ablage nur unter laufzeit/messung (gitignored, 0600).
#
# Schritte (Reihenfolge und Beobachtungen: messen.md):
#   vorbereiten      Test-Shim und Test-Claw starten. stdin, 3 Zeilen: Token des
#                    Test-Bots, Claude-Code-OAuth-Token, Gateway-Token (frei gewaehlt).
#                    Tagesgrenze des Test-Shims: MESS_TAGESGRENZE (Vorgabe 5).
#   testinstanz      Wegwerf-Postgres + event-mcp (Image aus Etappe 4) mit Testdaten:
#                    Firma, Leitung, "Messung A" (fest), "Messung B" (hackathon) und ihre
#                    Schluessel; setzt den melden-Server des Empfangs (main).
#   code A|B|direkt  Einmal-Code zum Verbinden (wird angezeigt: kurzlebig, Testdaten).
#   pruefen          Verbindungen der Testinstanz; stdin 1 Zeile: die echte Chat-ID, gegen
#                    die verglichen wird (Ausgabe nur "passt"/"passt NICHT").
#   anlegen <agent>  Agent wie der Bereitstellungs-Arbeiter (schritte_anlegen,
#   abbauen <agent>  DockerAusfuehrer); stdin 1 Zeile: Chat-ID. Agenten: siehe testinstanz.
#   dm pairing|open  channels.telegram.dmPolicy des Test-Claws.
#   stand            Agenten, Server, Bindings, Gruppen (nur Namen/Anzahlen), Startzeit,
#                    Neustarts, `openclaw config validate`.
#   protokoll [min]  Logs von Test-Claw und Test-MCP nach laufzeit/messung/.
#   last <n>         n Platzhalter-Agenten anlegen, Speicher/Platte messen, wieder entfernen.
#   aufraeumen       Testcontainer, Volume und laufzeit/messung entfernen.
set -eu
umask 077

# --- 0. Schalter: nur Builtins, vor allem anderen ----------------------------------
OK=nein
ARGUMENTE=()
for a in "$@"; do
  case "$a" in
    --ich-habe-ok) OK=ja ;;
    *) ARGUMENTE+=("$a") ;;
  esac
done
if [ "$OK" != ja ]; then
  echo "messen.sh tut ohne --ich-habe-ok nichts (Ruling S7: Messpunkte nur vorbereitet)." >&2
  echo "Erst Felix' OK und den Claim, dann: bash deploy/messen.sh --ich-habe-ok <schritt>" >&2
  echo "Anleitung: deploy/messen.md" >&2
  exit 2
fi
set -- "${ARGUMENTE[@]+"${ARGUMENTE[@]}"}"
set -o pipefail

# --- 1. Feste Ziele (nur Testobjekte) ------------------------------------------------
CLAW=event-messung-claw
SHIM=event-messung-shim
MCP=event-messung-mcp
PG=event-messung-pg
VOL_STATE=event-messung-state
CLAW_IMAGE=ghcr.io/openclaw/openclaw:2026.7.1-slim
SHIM_IMAGE=event-management/shim:2.1.268
SYSTEM_IMAGE=event-management/system:etappe-4
PG_IMAGE=postgres:16
LZ=laufzeit/messung
# Im gemeinsamen Netz-Namensraum des Test-Shims (kein Geheimnis: trust nur dort).
MCP_URL=http://127.0.0.1:8171/mcp
ADMIN_DSN="host=127.0.0.1 port=5432 user=postgres dbname=postgres"
APP_DSN="host=127.0.0.1 port=5432 user=event_mess dbname=postgres"

for ziel in "$CLAW" "$SHIM" "$MCP" "$PG" "$VOL_STATE"; do
  case "$ziel" in
    event-messung-*) ;;
    *) echo "ABBRUCH: $ziel ist kein Testobjekt." >&2; exit 1 ;;
  esac
done

cd "$(dirname "$0")/.."
PYTHONPATH="$(cd ../.. && pwd)"
export PYTHONPATH

abbruch() { echo "ABBRUCH: $*" >&2; exit 1; }
cr_weg() { printf '%s' "${1%$'\r'}"; }

# --- 2. Eingebettete Programme ---------------------------------------------------------

# Test-Config aus der Saat-VORLAGE (config/openclaw.json, ohne private Werte): nur der
# Empfang `main`, keine Legacy-Gruppe, keine Event-App, kein Brain; Test-Bot-Token und
# Gateway-Token von stdin. Ausgabe: JSON fuer SAAT_JS.
SAAT_PY=$(cat <<'PY'
import json
import sys
from pathlib import Path

tg = sys.stdin.readline().rstrip("\r\n")
gw = sys.stdin.readline().rstrip("\r\n")
c = json.loads(Path("config/openclaw.json").read_text(encoding="utf-8"))
c["gateway"]["auth"] = {"mode": "token", "token": gw}
c["models"]["providers"]["shim"]["baseUrl"] = "http://127.0.0.1:8125/v1"
c["agents"]["list"] = [a for a in c["agents"]["list"] if a.get("id") == "main"]
c["mcp"]["servers"] = {}
telegram = c["channels"]["telegram"]
telegram["groups"] = {"*": {"requireMention": True}}
telegram["botToken"] = tg
c["channels"]["whatsapp"] = {"enabled": False}
if "{{" in json.dumps(c):
    sys.exit("ABBRUCH: offener Platzhalter in der Test-Config")
dateien = {name: Path("config/workspace", name).read_text(encoding="utf-8")
           for name in ("SOUL.md", "AGENTS.md")}
json.dump({"config": c, "dateien": dateien}, sys.stdout)
PY
)

SAAT_JS=$(cat <<'JS'
const fs = require('fs');
const a = JSON.parse(fs.readFileSync(0, 'utf8'));
const D = '/home/node/.openclaw';
fs.mkdirSync(D + '/workspace', { recursive: true });
fs.writeFileSync(D + '/openclaw.json', JSON.stringify(a.config, null, 2) + '\n', { mode: 0o600 });
fs.chmodSync(D + '/openclaw.json', 0o600);
for (const name of ['SOUL.md', 'AGENTS.md']) {
  fs.writeFileSync(D + '/workspace/' + name, a.dateien[name], { mode: 0o644 });
}
process.stdout.write('Test-Config und Empfangs-Arbeitsbereich gesetzt\n');
JS
)

# Testdaten in der Wegwerf-DB (laeuft im Image des Space). Ausgabe: JSON mit Schluesseln
# (nur fuer ABLEGEN_PY, nie auf den Bildschirm) und den Agenten.
TESTDATEN_PY=$(cat <<'PY'
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from spaces.event_management.db import anwenden
from spaces.event_management.system import schluessel

with psycopg.connect(os.environ["MESS_ADMIN_DSN"], autocommit=True, row_factory=dict_row) as c:
    anwenden.anwenden(c)
    c.execute("do $$ begin if not exists (select 1 from pg_roles where rolname = 'event_mess')"
              " then create role event_mess login; end if; end $$")
    c.execute("grant event_app to event_mess")
    firma = c.execute("insert into event.firma (name, kurz) values ('Beispiel GmbH',"
                      " 'beispiel-messung') returning id").fetchone()["id"]
    person = c.execute("insert into event.person (mail, name) values"
                       " ('leitung-messung@example.org', 'Leitung Messung') returning id"
                       ).fetchone()["id"]
    c.execute("insert into event.mitgliedschaft values (%s, %s, 'leitung')", (person, firma))
    beginn = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
    agenten, klartexte = {}, {}
    _, klartexte["melden"] = schluessel.ausstellen(c, art="melden", bezeichnung="Messung",
                                                   gueltig_tage=7)
    for name, kategorie in (("Messung A", "fest"), ("Messung B", "hackathon")):
        eid = c.execute("insert into event.event (firma_id, kategorie, name, beginn, ende)"
                        " values (%s, %s, %s, %s, %s) returning id",
                        (firma, kategorie, name, beginn, beginn + timedelta(hours=4))
                        ).fetchone()["id"]
        agent = f"ev-{eid.hex[:8]}"
        agenten[agent] = {"name": name, "kategorie": kategorie, "ziel": "gruppe"}
        _, klartexte[agent] = schluessel.ausstellen(c, art="event", bezeichnung="Messung",
                                                    gueltig_tage=7, event_id=eid)
    agent = f"ld-{person.hex[:8]}"
    agenten[agent] = {"name": "Leitung Messung", "ziel": "leitung"}
    _, klartexte[agent] = schluessel.ausstellen(c, art="leitung", bezeichnung="Messung",
                                                gueltig_tage=7, person_id=person)
json.dump({"agenten": agenten, "schluessel": klartexte}, sys.stdout)
PY
)

# Schluessel nach laufzeit/messung/<name>.schluessel (0600); Ausgabe nur Namen.
ABLEGEN_PY=$(cat <<'PY'
import json
import os
import re
import sys
from pathlib import Path

ordner = Path(sys.argv[1])
ordner.mkdir(parents=True, exist_ok=True)
daten = json.load(sys.stdin)
for name, klartext in daten["schluessel"].items():
    if name != "melden" and not re.fullmatch(r"(ev|ld)-[0-9a-f]{8}", name):
        sys.exit("ABBRUCH: unerwarteter Name")
    datei = ordner / f"{name}.schluessel"
    fd = os.open(datei, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(klartext + "\n")
(ordner / "agenten.json").write_text(json.dumps(daten["agenten"]), encoding="utf-8")
for agent, info in sorted(daten["agenten"].items()):
    print(f"Agent {agent}: {info['name']} ({info['ziel']})")
PY
)

# Einmal-Code als Leitung der Testfirma (App-Rolle, RLS aktiv).
CODE_PY=$(cat <<'PY'
import os
import sys

import psycopg
from psycopg.rows import dict_row

from spaces.event_management.system import verbinden
from spaces.event_management.system.kontext import Kontext

ziel = sys.argv[1]
if ziel not in ("A", "B", "direkt"):
    sys.exit("ABBRUCH: Ziel A, B oder direkt")
with psycopg.connect(os.environ["MESS_ADMIN_DSN"], autocommit=True, row_factory=dict_row) as a:
    leitung = a.execute("select m.person_id, m.firma_id from event.mitgliedschaft m join"
                        " event.person p on p.id = m.person_id where p.mail ="
                        " 'leitung-messung@example.org'").fetchone()
    event = a.execute("select id from event.event where name = %s",
                      (f"Messung {ziel}",)).fetchone()
k = Kontext(person_id=leitung["person_id"], betreiber=False,
            rollen={leitung["firma_id"]: "leitung"})
with psycopg.connect(os.environ["MESS_APP_DSN"], autocommit=True, row_factory=dict_row) as app:
    if ziel == "direkt":
        print(verbinden.code_erzeugen(app, k, art="direkt", event_id=None))
    else:
        print(verbinden.code_erzeugen(app, k, art="gruppe", event_id=event["id"]))
PY
)

# Stand der Verbindungen; die echte Chat-ID kommt von stdin, ausgegeben wird nur der
# Vergleich. Dazu die Zahl der Aufgaben je Testevent (Messpunkt 2).
PRUEFEN_PY=$(cat <<'PY'
import os
import sys

import psycopg
from psycopg.rows import dict_row

erwartet = int(sys.stdin.readline().strip())
with psycopg.connect(os.environ["MESS_ADMIN_DSN"], autocommit=True, row_factory=dict_row) as a:
    zeilen = a.execute("select v.art, v.status, v.chat_id, e.name from event.verbindung v"
                       " left join event.event e on e.id = v.event_id"
                       " order by v.art desc, e.name, v.ablauf").fetchall()
    aufgaben = {z["name"]: z["n"] for z in a.execute(
        "select e.name, count(t.id) as n from event.event e left join event.aufgabe t"
        " on t.event_id = e.id where e.name like 'Messung %' group by e.name").fetchall()}
for z in zeilen:
    was = f"gruppe {z['name']}" if z["art"] == "gruppe" else "direkt"
    if z["chat_id"] is None:
        print(f"{was}: {z['status']}")
    else:
        print(f"{was}: {z['status']}, Chat-ID {'passt' if z['chat_id'] == erwartet else 'passt NICHT'}")
print(f"Aufgaben: Messung A {aufgaben.get('Messung A', 0)}, Messung B {aufgaben.get('Messung B', 0)}")
PY
)

# Anlegen/Abbauen genau wie der Bereitstellungs-Arbeiter, nur gegen den Test-Claw.
ANLEGEN_PY=$(cat <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from spaces.event_management.system import bereitstellung, events

op, agent, ablage, claw, mcp_url = sys.argv[1:6]
agenten = json.loads(Path(ablage, "agenten.json").read_text(encoding="utf-8"))
if agent not in agenten:
    sys.exit("ABBRUCH: unbekannter Agent (siehe Ausgabe von testinstanz)")
info = agenten[agent]
chat = int(sys.stdin.readline().strip())
auftrag = {"agent_id": agent, "ziel": info["ziel"], "chat_id": chat, "person_name": info["name"]}
aus = bereitstellung.DockerAusfuehrer(claw)
if op == "anlegen":
    klartext = Path(ablage, f"{agent}.schluessel").read_text(encoding="utf-8").strip()
    schema = event = None
    if info["ziel"] == "gruppe":
        schema = events.schema_fuer(info["kategorie"], None)
        event = {"name": info["name"], "firma_name": "Beispiel GmbH"}
    schritte = bereitstellung.schritte_anlegen(auftrag, schluessel_klartext=klartext,
                                               mcp_url=mcp_url, schema=schema, event=event)
else:
    schritte = bereitstellung.schritte_abbauen(auftrag, datum=datetime.now(timezone.utc))
for s in schritte:
    print(f"{s.name}: {'geaendert' if s.ausfuehren(aus) else 'unveraendert'}")
PY
)

# Lesen und kleine Aenderungen der Test-Config (ueber denselben Weg wie der Arbeiter).
CONFIG_PY=$(cat <<'PY'
import sys
from pathlib import Path

from spaces.event_management.system.bereitstellung import DockerAusfuehrer

op, claw = sys.argv[1], sys.argv[2]
aus = DockerAusfuehrer(claw)
if op == "stand":
    c = aus.config_lesen()
    print("Agenten:", ", ".join(str(a.get("id")) for a in c.get("agents", {}).get("list", [])))
    print("MCP-Server:", ", ".join(sorted(c.get("mcp", {}).get("servers", {}))) or "-")
    for b in c.get("bindings", []):
        peer = (b.get("match") or {}).get("peer") or {}
        print(f"Binding: {b.get('agentId')} -> {peer.get('kind')}")
    tg = c.get("channels", {}).get("telegram", {})
    gruppen = tg.get("groups", {})
    print(f"Gruppen-Eintraege: {len([g for g in gruppen if g != '*'])},"
          f" '*': {gruppen.get('*')}")
    print(f"dmPolicy: {tg.get('dmPolicy', '(Vorgabe)')},"
          f" allowFrom: {len(tg.get('allowFrom') or [])} Eintraege")
elif op == "melden":
    klartext = Path(sys.argv[3], "melden.schluessel").read_text(encoding="utf-8").strip()
    aus.config_schreiben("mcp.servers.melden", {
        "url": sys.argv[4], "transport": "streamable-http",
        "headers": {"Authorization": f"Bearer {klartext}"}})
    print("mcp.servers.melden: gesetzt")
elif op == "dm":
    politik = sys.argv[3]
    if politik not in ("pairing", "open"):
        sys.exit("ABBRUCH: pairing oder open")
    aus.config_schreiben("channels.telegram.dmPolicy", politik)
    if politik == "open":
        aus.config_schreiben("channels.telegram.allowFrom", ["*"])
    else:
        aus.config_entfernen("channels.telegram.allowFrom")
    print(f"dmPolicy: {politik}")
else:
    sys.exit("ABBRUCH: unbekannte Operation")
PY
)

# Platzhalter-Agenten fuer die Lastmessung (ohne Binding, ohne Server).
LAST_PY=$(cat <<'PY'
import sys

from spaces.event_management.system.bereitstellung import ARBEITSBEREICH, DockerAusfuehrer

op, claw, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
if not 1 <= n <= 50:
    sys.exit("ABBRUCH: 1 bis 50 Agenten")
namen = [f"ev-fe{i:06x}" for i in range(1, n + 1)]
aus = DockerAusfuehrer(claw)
liste = [a for a in aus.config_lesen().get("agents", {}).get("list", [])
         if a.get("id") not in namen]
if op == "dazu":
    for name in namen:
        aus.datei_schreiben(f"{ARBEITSBEREICH}{name}/SOUL.md", "# Platzhalter (Lastmessung)\n")
    liste += [{"id": name, "workspace": ARBEITSBEREICH + name,
               "tools": {"allow": [f"{name}__*"]}} for name in namen]
aus.config_schreiben("agents.list", liste)
print(f"{n} Platzhalter-Agenten {'angelegt' if op == 'dazu' else 'entfernt'}")
PY
)

# --- 3. Hilfen -------------------------------------------------------------------------

warten() {  # warten <beschreibung> <befehl...>: bis zu 60 s
  local was=$1; shift
  for _ in $(seq 1 30); do
    if "$@" >/dev/null 2>&1; then echo "$was: ok"; return 0; fi
    sleep 2
  done
  abbruch "$was antwortet nach 60 s nicht."
}

shim_lebt() {
  docker exec "$SHIM" python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8125/v1/models', timeout=3)"
}

claw_lebt() {
  docker exec "$CLAW" node -e "fetch('http://127.0.0.1:19100/healthz').then((r)=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
}

mcp_lebt() {
  docker exec "$MCP" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8171/gesund', timeout=3)"
}

pg_lebt() {
  docker exec "$PG" pg_isready -q -h 127.0.0.1
}

config_pruefen() {
  local datei
  datei="$LZ/validate-$(date +%Y%m%d-%H%M%S).json"
  if docker exec "$CLAW" timeout 60 openclaw config validate --json >"$datei" 2>&1; then
    echo "config validate: gueltig"
  else
    abbruch "config validate meldet Fehler ($datei)."
  fi
}

im_space() {  # im_space <programm> <argumente...>: einmaliger Container mit dem Space-Code
  local programm=$1; shift
  docker run --rm -i --network "container:$SHIM" -e MESS_ADMIN_DSN="$ADMIN_DSN" \
    -e MESS_APP_DSN="$APP_DSN" "$SYSTEM_IMAGE" python -c "$programm" "$@"
}

agent_pruefen() {
  [[ "${1:-}" =~ ^(ev|ld)-[0-9a-f]{8}$ ]] || abbruch "Agent-Name ev-/ld-<8 Hex> erwartet."
}

messung() {
  docker stats --no-stream --format '{{.Name}}: {{.MemUsage}}' "$CLAW" "$SHIM"
  docker exec "$CLAW" du -sk /home/node/.openclaw | sed 's/[[:space:]].*/ KiB im Zustand des Claws/'
  grep -E '^(MemAvailable|SwapFree):' /proc/meminfo
}

# --- 4. Schritte -------------------------------------------------------------------------

vorbereiten() {
  IFS= read -r TG || true
  IFS= read -r CC || true
  IFS= read -r GW || true
  TG=$(cr_weg "$TG"); CC=$(cr_weg "$CC"); GW=$(cr_weg "$GW")
  [ -n "$TG" ] || abbruch "Zeile 1 (Token des Test-Bots) ist leer."
  [ -n "$CC" ] || abbruch "Zeile 2 (Claude-Code-OAuth-Token) ist leer."
  [ -n "$GW" ] || abbruch "Zeile 3 (Gateway-Token) ist leer."
  GRENZE=${MESS_TAGESGRENZE:-5}
  [[ "$GRENZE" =~ ^[0-9]{1,3}$ ]] || abbruch "MESS_TAGESGRENZE ist keine Zahl bis 999."
  if docker inspect "$CLAW" >/dev/null 2>&1 || docker inspect "$SHIM" >/dev/null 2>&1; then
    abbruch "Testpaar existiert schon (erst: aufraeumen)."
  fi

  mkdir -p "$LZ/shim-budget"
  printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$CC" >"$LZ/claude-token"
  printf '%s\n' "$TG" >"$LZ/telegram-token"
  cat >"$LZ/agent_budget.yaml" <<EOF
# Budget des TEST-Shims (Messung). Nur dieser Test-Agent, kleine Tagesgrenze.
uebergang_unbekannte_zulassen: false
pause_bei_user_aktiv: false
pause_fenster_min: 10
pause_ausnahmen: []
behalten_ohne_llm: []
agenten:
  $CLAW:
    bereich: event-management
    klasse: auftrag
    modell: sonnet
    tagesgrenze: $GRENZE
    wrapper: shim
EOF
  echo "laufzeit/messung: Schluesseldateien und Budget (Tagesgrenze $GRENZE) geschrieben"

  docker run -d --name "$SHIM" --init --read-only \
    --tmpfs /tmp:size=256m --tmpfs /home/node:size=256m \
    --security-opt no-new-privileges:true --cap-drop ALL \
    --env-file "$LZ/claude-token" -e SHIM_TOOL_CALLS=1 -e VIBEMIND_AGENT="$CLAW" \
    -e VIBEMIND_BUDGET_DIR=/budget -e VIBEMIND_BUDGET_CONFIG=/opt/event/agent_budget.yaml \
    -v "$PWD/$LZ/shim-budget:/budget" \
    -v "$PWD/$LZ/agent_budget.yaml:/opt/event/agent_budget.yaml:ro" \
    "$SHIM_IMAGE" >/dev/null
  warten "Test-Shim /v1/models" shim_lebt

  printf '%s\n%s\n' "$TG" "$GW" | python3 -c "$SAAT_PY" \
    | docker run --rm -i -v "$VOL_STATE:/home/node/.openclaw" --entrypoint node "$CLAW_IMAGE" -e "$SAAT_JS"

  docker run -d --name "$CLAW" --network "container:$SHIM" \
    -e TZ=Europe/Berlin -e OPENCLAW_STATE_DIR=/home/node/.openclaw \
    -v "$VOL_STATE:/home/node/.openclaw" "$CLAW_IMAGE" >/dev/null
  warten "Test-Claw /healthz" claw_lebt
  config_pruefen
}

testinstanz() {
  [ -f "$LZ/telegram-token" ] || abbruch "erst: vorbereiten"
  docker image inspect "$SYSTEM_IMAGE" >/dev/null 2>&1 \
    || abbruch "Image $SYSTEM_IMAGE fehlt (baut Etappe 4)."
  docker run -d --name "$PG" --network "container:$SHIM" \
    --tmpfs /var/lib/postgresql/data:size=512m -e POSTGRES_HOST_AUTH_METHOD=trust \
    "$PG_IMAGE" -c listen_addresses=127.0.0.1 -c shared_buffers=32MB >/dev/null
  warten "Test-Postgres" pg_lebt
  im_space "$TESTDATEN_PY" </dev/null | python3 -c "$ABLEGEN_PY" "$LZ"
  docker run -d --name "$MCP" --network "container:$SHIM" --read-only --tmpfs /tmp:size=64m \
    --security-opt no-new-privileges:true --cap-drop ALL \
    -e EVENT_APP_DSN="$APP_DSN" -e EVENT_MCP_HOST=127.0.0.1 -e EVENT_MCP_PORT=8171 \
    -e TELEGRAM_BOT_TOKEN_FILE=/run/secrets/telegram-token \
    -v "$PWD/$LZ/telegram-token:/run/secrets/telegram-token:ro" \
    "$SYSTEM_IMAGE" python -m spaces.event_management.system.mcp_server >/dev/null
  warten "Test-MCP /gesund" mcp_lebt
  python3 -c "$CONFIG_PY" melden "$CLAW" "$LZ" "$MCP_URL"
  config_pruefen
}

code() {
  case "${1:-}" in
    A|B|direkt) ;;
    *) abbruch "code A|B|direkt" ;;
  esac
  im_space "$CODE_PY" "$1" </dev/null
}

pruefen() {
  IFS= read -r ERWARTET || true
  ERWARTET=$(cr_weg "$ERWARTET")
  [[ "$ERWARTET" =~ ^-?[0-9]{1,16}$ ]] || abbruch "stdin: die echte Chat-ID (Zahl)."
  printf '%s\n' "$ERWARTET" | im_space "$PRUEFEN_PY"
}

agent_schritt() {  # agent_schritt anlegen|abbauen <agent>
  agent_pruefen "${2:-}"
  IFS= read -r CHAT || true
  CHAT=$(cr_weg "$CHAT")
  [[ "$CHAT" =~ ^-?[0-9]{1,16}$ ]] || abbruch "stdin: Chat-ID (Zahl)."
  printf '%s\n' "$CHAT" | python3 -c "$ANLEGEN_PY" "$1" "$2" "$LZ" "$CLAW" "$MCP_URL"
  config_pruefen
}

dm() {
  case "${1:-}" in
    pairing|open) ;;
    *) abbruch "dm pairing|open" ;;
  esac
  python3 -c "$CONFIG_PY" dm "$CLAW" "$1"
  config_pruefen
}

stand() {
  docker inspect -f 'Test-Claw gestartet: {{.State.StartedAt}}, Neustarts: {{.RestartCount}}' "$CLAW"
  python3 -c "$CONFIG_PY" stand "$CLAW"
  config_pruefen
}

protokoll() {
  local minuten=${1:-15} stempel
  [[ "$minuten" =~ ^[0-9]{1,4}$ ]] || abbruch "protokoll [minuten]"
  stempel=$(date +%Y%m%d-%H%M%S)
  docker logs --since "${minuten}m" "$CLAW" >"$LZ/claw-$stempel.log" 2>&1
  echo "$LZ/claw-$stempel.log: $(wc -l <"$LZ/claw-$stempel.log") Zeilen"
  if docker inspect "$MCP" >/dev/null 2>&1; then
    docker logs --since "${minuten}m" "$MCP" >"$LZ/mcp-$stempel.log" 2>&1
    echo "$LZ/mcp-$stempel.log: $(wc -l <"$LZ/mcp-$stempel.log") Zeilen"
  fi
}

last() {
  local n=${1:-}
  [[ "$n" =~ ^[0-9]{1,2}$ ]] || abbruch "last <1-50>"
  echo "--- vorher"; messung
  python3 -c "$LAST_PY" dazu "$CLAW" "$n"
  config_pruefen
  sleep 30
  echo "--- mit $n Platzhalter-Agenten (30 s spaeter)"; messung
  python3 -c "$LAST_PY" weg "$CLAW" "$n"
}

aufraeumen() {
  docker rm -f "$CLAW" "$MCP" "$PG" "$SHIM" >/dev/null 2>&1 || true
  docker volume rm "$VOL_STATE" >/dev/null 2>&1 || true
  rm -rf "$LZ"
  echo "Testpaar, Volume und $LZ entfernt"
}

SCHRITT=${1:-}
[ $# -gt 0 ] && shift
case "$SCHRITT" in
  vorbereiten) vorbereiten ;;
  testinstanz) testinstanz ;;
  code) code "$@" ;;
  pruefen) pruefen ;;
  anlegen|abbauen) agent_schritt "$SCHRITT" "$@" ;;
  dm) dm "$@" ;;
  stand) stand ;;
  protokoll) protokoll "$@" ;;
  last) last "$@" ;;
  aufraeumen) aufraeumen ;;
  *) abbruch "unbekannter Schritt (siehe Kopf dieses Skripts)" ;;
esac
