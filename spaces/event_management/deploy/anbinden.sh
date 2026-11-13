#!/usr/bin/env bash
# deploy/anbinden.sh -- bindet den Space event-management ans Laufzeit-Volume an:
# Saat der Konfiguration und der Arbeitsbereiche, Schluessel, Neustart, Probe.
# Wiederholbar. Muster: sales-claw deploy/post-anbinden.sh und gateway-saat.sh.
#
#   printf '%s\n%s\n%s\n%s\n%s\n' "$GW" "$TG" "$APP" "$CC" "$MLD" | bash deploy/anbinden.sh
#
# Genau fuenf Zeilen von stdin (nie als Argument, nie in einer Ausgabe):
#   1  Gateway-Token
#   2  Telegram-Bot-Token
#   3  Authorization-Wert der Event-App (Header des MCP-Servers `event-app`)
#   4  Claude-Code-OAuth-Token fuer den Shim; leer = laufzeit/claude-token
#      bleibt, wie es ist.
#   5  Authorization-Wert des Servers `melden` (Empfangs-Agent `main`):
#      "Bearer <Schluessel der Art melden>", ausgestellt mit
#      `python -m spaces.event_management.system.schluessel ausstellen --art melden`.
#   Zeilen 1-3 und 5 duerfen nicht leer sein.
#
# Voraussetzung: `python deploy/fuellen.py werte.local.json build/` lief. Der
# Claw-Container muss NICHT laufen.
#
# Schluesselwege (Begruendung): `openclaw config set <pfad> <wert>` nimmt den
# Wert als Argument; er stuende dann in der Prozessliste und in `docker
# inspect` der Exec-Sitzung. Darum schreibt ein `node -e` im Container die
# JSON-Datei direkt, die Werte kommen NUR ueber stdin, die Datei bekommt 0600.
# Verglichen wird in demselben node-Lauf (kein `config get`, dessen Ausgabe
# sichtbar werden koennte):
#   gateway.auth.token                          Gateway-Token
#   channels.telegram.botToken                  Telegram-Bot-Token
#   mcp.servers.event-app.headers.Authorization Event-App
#   mcp.servers.melden.headers.Authorization    Empfang (melden-Schluessel)
#
# Ablauf: stdin pruefen -> Schluesseldateien auf dem Host -> Saat und Schluessel
# per `docker compose run --rm --no-deps -T --entrypoint node` (braucht weder
# einen laufenden Claw noch einen gesunden Shim; auf einem frischen Volume gibt
# es noch keine openclaw.json) -> `docker compose up -d` -> Proben (Gateway,
# Shim). Neustart nur bei Aenderung und nur, wenn der Claw schon lief.
#
# Der Vergleich der openclaw.json ignoriert die Schluesselfelder, die
# Top-Level-Felder `meta` und `wizard` sowie alles, was der
# Bereitstellungs-Arbeiter angelegt hat (Ruling T8-b): Agenten
# `ev-|ld-|bo-<8 Hex>`, ihre MCP-Server, ihre Bindings und die Gruppen-Eintraege
# ihrer Gruppen. Wird die Datei durch die Saat ersetzt, werden genau diese Teile
# aus der aktuellen Datei uebernommen (nur Anzahlen in der Ausgabe; die
# Server tragen Schluessel). Wird die Datei trotzdem bei jedem Lauf ersetzt,
# hat OpenClaw weitere eigene Felder ergaenzt; das ist akzeptabel (Sicherung +
# Neuanlage + Schluessel neu, Ergebnis bleibt gleich).
#
# Bricht ab, solange die Legacy-Gruppe der Saat keinen eigenen Agenten hat
# (Ruling T8-a, ev-legacy kommt mit Etappe 4).
#
# Ausgaben nennen nur Namen und Zustaende, nie Werte.
set -eu
umask 077

cd "$(dirname "$0")/.."

CLAW=event-management-claw
SHIM=event-management-shim

# --- 1. Fuenf Zeilen von stdin -----------------------------------------------
IFS= read -r GW || true
IFS= read -r TG || true
IFS= read -r APP || true
IFS= read -r CC || true
IFS= read -r MLD || true
# CRLF aus der Pipeline wegnehmen (ein \r im Schluessel ist ein stiller Fehler).
GW=${GW%$'\r'}; TG=${TG%$'\r'}; APP=${APP%$'\r'}; CC=${CC%$'\r'}; MLD=${MLD%$'\r'}

if [ -z "$GW" ]; then echo "ABBRUCH: Zeile 1 (Gateway-Token) ist leer." >&2; exit 1; fi
if [ -z "$TG" ]; then echo "ABBRUCH: Zeile 2 (Telegram-Bot-Token) ist leer." >&2; exit 1; fi
if [ -z "$APP" ]; then echo "ABBRUCH: Zeile 3 (Authorization der Event-App) ist leer." >&2; exit 1; fi
if [ -z "$MLD" ]; then echo "ABBRUCH: Zeile 5 (Authorization des Empfangs) ist leer." >&2; exit 1; fi

# Nur druckbares ASCII sowie Leer- und Tabzeichen; alles andere bricht ab, die
# Meldung nennt die Zeile, nie den Wert.
zeichen_pruefen() {
  case "$2" in
    *[!\ -~$'\t']*)
      echo "ABBRUCH: Zeile $1 enthaelt nicht druckbare oder Nicht-ASCII-Zeichen." >&2
      exit 1 ;;
  esac
}
export LC_ALL=C
zeichen_pruefen 1 "$GW"
zeichen_pruefen 2 "$TG"
zeichen_pruefen 3 "$APP"
zeichen_pruefen 4 "$CC"
zeichen_pruefen 5 "$MLD"

[ -f build/openclaw.json ] || { echo "ABBRUCH: build/openclaw.json fehlt (deploy/fuellen.py ausfuehren)." >&2; exit 1; }

# Schutz der Legacy-Gruppe (Ruling T8-a): die Saat fuehrt die bisherige
# Festival-Gruppe ({{TELEGRAM_GRUPPE}}) mit requireMention false. Ohne einen
# eigenen Agenten neben `main`, der an sie gebunden ist (ev-legacy, Etappe 4),
# antwortete dort der Empfang `main` mit nur den zwei Melde-Werkzeugen. Darum
# Abbruch vor jeder Aenderung. Geprueft wird die Vorlage config/openclaw.json
# (oeffentlich, ohne Werte); die Meldung nennt keine Daten.
RC=0
python3 - config/openclaw.json <<'PY' || RC=$?
import json
import sys

GRUPPE = "{{TELEGRAM_GRUPPE}}"
try:
    with open(sys.argv[1], encoding="utf-8") as datei:
        text = datei.read()
    konfig = json.loads(text)
except (OSError, ValueError):
    sys.exit(2)
if GRUPPE not in text:
    sys.exit(0)
eigene = [
    b for b in (konfig.get("bindings") or [])
    if isinstance(b, dict) and b.get("agentId") not in (None, "", "main")
    and isinstance(b.get("match"), dict) and isinstance(b["match"].get("peer"), dict)
    and b["match"]["peer"].get("id") == GRUPPE
]
sys.exit(0 if eigene else 3)
PY
if [ "$RC" = 3 ]; then
  echo "ABBRUCH: ev-legacy fehlt – erst Etappe 4 (die Legacy-Gruppe hat in config/openclaw.json keinen eigenen Agenten neben main)." >&2
  exit 1
fi
if [ "$RC" != 0 ]; then echo "ABBRUCH: config/openclaw.json nicht lesbar." >&2; exit 1; fi
# Der Shim braucht laufzeit/claude-token, um ueberhaupt zu starten.
if [ ! -f ./laufzeit/claude-token ] && [ -z "$CC" ]; then
  echo "ABBRUCH: laufzeit/claude-token fehlt und Zeile 4 (Claude-Code-OAuth-Token) ist leer." >&2
  exit 1
fi

GEAENDERT=0
CC_NEU=0
# Zustand VOR dem Skript: nur ein schon laufender Container braucht Neustart.
CLAW_LAUFEND=$(docker inspect -f '{{.State.Running}}' "$CLAW" 2>/dev/null || true)
SHIM_LAUFEND=$(docker inspect -f '{{.State.Running}}' "$SHIM" 2>/dev/null || true)

# --- 2. Schluesseldateien auf dem Host (0600) -------------------------------
mkdir -p ./laufzeit ./laufzeit/shim-budget
printf '%s
' "$GW" > ./laufzeit/gateway-token
chmod 600 ./laufzeit/gateway-token
echo "laufzeit/gateway-token: geschrieben"
if [ -n "$CC" ]; then
  NEU="CLAUDE_CODE_OAUTH_TOKEN=$CC"
  if [ ! -f ./laufzeit/claude-token ] || [ "$(cat ./laufzeit/claude-token)" != "$NEU" ]; then
    CC_NEU=1
  fi
  printf 'CLAUDE_CODE_OAUTH_TOKEN=%s
' "$CC" > ./laufzeit/claude-token
  chmod 600 ./laufzeit/claude-token
  if [ "$CC_NEU" = 1 ]; then
    echo "laufzeit/claude-token: gesetzt"
  else
    echo "laufzeit/claude-token: unveraendert"
  fi
else
  echo "laufzeit/claude-token: unveraendert"
fi

# --- 3. Saat ins Volume ------------------------------------------------------
# openclaw.json: fehlt sie, kommt die Saat; weicht sie ab (ohne die vier
# Schluesselfelder und ohne die bereitgestellten Agenten), wird sie gesichert
# und durch die Saat ersetzt, die bereitgestellten Agenten samt Server,
# Bindings und Gruppen-Eintraegen werden mitgenommen (Ruling T8-b). Von den
# Arbeitsbereichen werden nur SOUL.md, AGENTS.md und skills/ ueberschrieben.
SAAT_JS=$(cat <<'JS'
const fs = require('fs');
const STATE = '/home/node/.openclaw';
const SEED = '/seed';
const CFG = STATE + '/openclaw.json';
const GEHEIM = [
  ['gateway', 'auth', 'token'],
  ['channels', 'telegram', 'botToken'],
  ['mcp', 'servers', 'event-app', 'headers', 'Authorization'],
  ['mcp', 'servers', 'melden', 'headers', 'Authorization'],
];
// Vom Bereitstellungs-Arbeiter angelegt (Ruling T8-b): die Saat kennt diese
// Agenten nicht; beim Ersetzen bleiben sie erhalten, im Vergleich zaehlen sie nicht.
const AGENT = /^(ev|ld|bo)-[0-9a-f]{8}$/;
function istObjekt(x) { return x !== null && typeof x === 'object' && !Array.isArray(x); }
function gruppenVon(c) {
  const t = istObjekt(c.channels) && istObjekt(c.channels.telegram) ? c.channels.telegram : null;
  return t && istObjekt(t.groups) ? t.groups : null;
}
function bereitgestellt(c) {
  const b = { agenten: [], server: {}, bindings: [], gruppen: {} };
  if (!istObjekt(c)) return b;
  if (istObjekt(c.agents) && Array.isArray(c.agents.list)) {
    b.agenten = c.agents.list.filter((a) => istObjekt(a) && AGENT.test(String(a.id)));
  }
  if (istObjekt(c.mcp) && istObjekt(c.mcp.servers)) {
    for (const k of Object.keys(c.mcp.servers)) if (AGENT.test(k)) b.server[k] = c.mcp.servers[k];
  }
  if (Array.isArray(c.bindings)) {
    b.bindings = c.bindings.filter((x) => istObjekt(x) && AGENT.test(String(x.agentId)));
  }
  const g = gruppenVon(c);
  for (const x of b.bindings) {
    const p = istObjekt(x.match) && istObjekt(x.match.peer) ? x.match.peer : null;
    if (p && p.kind === 'group' && g && Object.prototype.hasOwnProperty.call(g, String(p.id))) {
      b.gruppen[String(p.id)] = g[String(p.id)];
    }
  }
  return b;
}
// Entfernt die bereitgestellten Teile; `gruppen` (Chat-IDs) kommt aus der aktuellen
// Datei und gilt fuer beide Seiten des Vergleichs gleich.
function ohneBereitgestellt(c, gruppen) {
  if (istObjekt(c.agents) && Array.isArray(c.agents.list)) {
    c.agents.list = c.agents.list.filter((a) => !(istObjekt(a) && AGENT.test(String(a.id))));
  }
  if (istObjekt(c.mcp) && istObjekt(c.mcp.servers)) {
    for (const k of Object.keys(c.mcp.servers)) if (AGENT.test(k)) delete c.mcp.servers[k];
  }
  if (Array.isArray(c.bindings)) {
    c.bindings = c.bindings.filter((x) => !(istObjekt(x) && AGENT.test(String(x.agentId))));
    if (c.bindings.length === 0) delete c.bindings;
  }
  const g = gruppenVon(c);
  if (g) for (const k of gruppen) delete g[k];
}
function uebernehmen(c, b) {
  if (b.agenten.length) {
    if (!istObjekt(c.agents)) c.agents = {};
    const liste = Array.isArray(c.agents.list) ? c.agents.list : [];
    c.agents.list = liste.filter((a) => !(istObjekt(a) && AGENT.test(String(a.id)))).concat(b.agenten);
  }
  if (Object.keys(b.server).length) {
    if (!istObjekt(c.mcp)) c.mcp = {};
    if (!istObjekt(c.mcp.servers)) c.mcp.servers = {};
    Object.assign(c.mcp.servers, b.server);
  }
  if (b.bindings.length) {
    const liste = Array.isArray(c.bindings) ? c.bindings : [];
    c.bindings = liste.filter((x) => !(istObjekt(x) && AGENT.test(String(x.agentId)))).concat(b.bindings);
  }
  if (Object.keys(b.gruppen).length) {
    if (!istObjekt(c.channels)) c.channels = {};
    if (!istObjekt(c.channels.telegram)) c.channels.telegram = {};
    if (!istObjekt(c.channels.telegram.groups)) c.channels.telegram.groups = {};
    const g = c.channels.telegram.groups;
    // Eigene Eintraege der Saat (Legacy-Gruppe, "*") gewinnen.
    for (const k of Object.keys(b.gruppen)) if (!Object.prototype.hasOwnProperty.call(g, k)) g[k] = b.gruppen[k];
  }
}
function ohneGeheim(o, gruppen) {
  const c = JSON.parse(JSON.stringify(o));
  ohneBereitgestellt(c, gruppen);
  delete c.meta;    // OpenClaw schreibt eigene Felder (meta, wizard, lastTouched*)
  delete c.wizard;
  for (const p of GEHEIM) {
    const eltern = [c];
    for (let i = 0; i < p.length - 1 && eltern[eltern.length - 1] && typeof eltern[eltern.length - 1] === 'object'; i++) {
      eltern.push(eltern[eltern.length - 1][p[i]]);
    }
    const n = eltern[p.length - 1];
    if (n && typeof n === 'object') delete n[p[p.length - 1]];
    for (let i = p.length - 1; i > 0; i--) {  // leere Eltern entfernen
      const e = eltern[i];
      if (e && typeof e === 'object' && Object.keys(e).length === 0) delete eltern[i - 1][p[i - 1]]; else break;
    }
  }
  return c;
}
function stabil(o) {
  if (Array.isArray(o)) return o.map(stabil);
  if (o && typeof o === 'object') {
    return Object.keys(o).sort().reduce((a, k) => { a[k] = stabil(o[k]); return a; }, {});
  }
  return o;
}
function gleich(a, b) { return JSON.stringify(stabil(a)) === JSON.stringify(stabil(b)); }
try {
  const saat = JSON.parse(fs.readFileSync(SEED + '/openclaw.json', 'utf8'));
  if (!fs.existsSync(CFG)) {
    fs.copyFileSync(SEED + '/openclaw.json', CFG);
    fs.chmodSync(CFG, 0o600);
    console.log('openclaw.json: angelegt');
  } else {
    let aktuell = null;
    try { aktuell = JSON.parse(fs.readFileSync(CFG, 'utf8')); } catch (e) { aktuell = null; }
    const mit = bereitgestellt(aktuell);
    const gruppen = Object.keys(mit.gruppen);
    if (aktuell !== null && gleich(ohneGeheim(aktuell, gruppen), ohneGeheim(saat, gruppen))) {
      console.log('openclaw.json: unveraendert');
    } else {
      const zs = new Date().toISOString().replace(/[-:]/g, '').replace(/\..*/, '');
      fs.copyFileSync(CFG, CFG + '.vor-anbinden-' + zs);
      fs.chmodSync(CFG + '.vor-anbinden-' + zs, 0o600);
      const neu = JSON.parse(JSON.stringify(saat));
      uebernehmen(neu, mit);
      fs.writeFileSync(CFG, JSON.stringify(neu, null, 2) + '\n', { mode: 0o600 });
      fs.chmodSync(CFG, 0o600);
      console.log('openclaw.json: gesichert und durch die Saat ersetzt');
      // Nur Anzahlen: die Server tragen Schluessel, Bindings Chat-IDs.
      console.log('bereitgestellte Agenten uebernommen: ' + mit.agenten.length + ' Agenten, '
        + Object.keys(mit.server).length + ' Server, ' + mit.bindings.length + ' Bindings, '
        + gruppen.length + ' Gruppen');
    }
  }
  for (const w of ['workspace', 'workspace-brain']) {
    const quelle = SEED + '/' + w;
    const ziel = STATE + '/' + w;
    if (!fs.existsSync(quelle)) { console.log(w + ': Saat fehlt'); process.exitCode = 1; continue; }
    fs.mkdirSync(ziel, { recursive: true });
    for (const datei of ['SOUL.md', 'AGENTS.md']) {
      if (fs.existsSync(quelle + '/' + datei)) fs.copyFileSync(quelle + '/' + datei, ziel + '/' + datei);
    }
    if (fs.existsSync(quelle + '/skills')) {
      fs.cpSync(quelle + '/skills', ziel + '/skills', { recursive: true, force: true });
    }
    console.log(w + ': SOUL.md, AGENTS.md, skills/ aktualisiert');
  }
} catch (e) {
  console.log('Saat: Fehler (' + (e && e.code ? e.code : 'unbekannt') + ')');
  process.exitCode = 1;
}
JS
)
AUS=$(docker compose run --rm --no-deps -T --entrypoint node "$CLAW" -e "$SAAT_JS" </dev/null) || { printf '%s\n' "$AUS"; echo "ABBRUCH: Saat fehlgeschlagen." >&2; exit 1; }
printf '%s\n' "$AUS"
case "$AUS" in
  *"openclaw.json: angelegt"*|*"openclaw.json: gesichert"*) GEAENDERT=1 ;;
esac

# --- 4. Schluessel in die Konfiguration (nur per stdin, nur bei Abweichung) --
SCHLUESSEL_JS=$(cat <<'JS'
const fs = require('fs');
const CFG = '/home/node/.openclaw/openclaw.json';
const ZEILEN = fs.readFileSync(0, 'utf8').split('\n');
const ZIELE = [
  ['gateway.auth.token', ['gateway', 'auth', 'token']],
  ['channels.telegram.botToken', ['channels', 'telegram', 'botToken']],
  ['mcp.servers.event-app.headers.Authorization', ['mcp', 'servers', 'event-app', 'headers', 'Authorization']],
  ['mcp.servers.melden.headers.Authorization', ['mcp', 'servers', 'melden', 'headers', 'Authorization']],
];
try {
  const c = JSON.parse(fs.readFileSync(CFG, 'utf8'));
  let geaendert = false;
  ZIELE.forEach(([name, pfad], i) => {
    const wert = (ZEILEN[i] || '').replace(/\r$/, '');
    if (wert === '') { console.log(name + ': Wert fehlt'); process.exitCode = 1; return; }
    let n = c;
    for (const k of pfad.slice(0, -1)) {
      if (!n[k] || typeof n[k] !== 'object') n[k] = {};
      n = n[k];
    }
    const letzter = pfad[pfad.length - 1];
    if (n[letzter] === wert) {
      console.log(name + ': unveraendert');
    } else {
      n[letzter] = wert;
      geaendert = true;
      console.log(name + ': gesetzt');
    }
  });
  if (geaendert && !process.exitCode) {
    fs.writeFileSync(CFG, JSON.stringify(c, null, 2) + '\n', { mode: 0o600 });
    fs.chmodSync(CFG, 0o600);
  }
} catch (e) {
  console.log('Schluessel: Fehler (' + (e && e.code ? e.code : 'unbekannt') + ')');
  process.exitCode = 1;
}
JS
)
AUS=$(printf '%s\n%s\n%s\n%s\n' "$GW" "$TG" "$APP" "$MLD" | docker compose run --rm --no-deps -T --entrypoint node "$CLAW" -e "$SCHLUESSEL_JS") \
  || { printf '%s\n' "$AUS"; echo "ABBRUCH: Schluessel konnten nicht gesetzt werden." >&2; exit 1; }
printf '%s\n' "$AUS"
case "$AUS" in
  *": gesetzt"*) GEAENDERT=1 ;;
esac

# --- 5. Starten: compose kuemmert sich um depends_on -------------------------
docker compose up -d
if [ "$CC_NEU" = 1 ] && [ "$SHIM_LAUFEND" = "true" ]; then
  docker compose up -d --force-recreate "$SHIM"   # env_file wird nur beim Anlegen gelesen
  echo "$SHIM: neu angelegt (neuer Claude-Token)"
fi
if [ "$GEAENDERT" = 1 ] && [ "$CLAW_LAUFEND" = "true" ]; then
  if [ "$CC_NEU" = 1 ]; then
    docker compose up -d --force-recreate "$CLAW"
    echo "$CLAW: neu angelegt"
  else
    docker compose restart "$CLAW"
    echo "$CLAW: neu gestartet"
  fi
else
  echo "$CLAW: kein Neustart noetig"
fi

# --- 6. Proben: Gateway, dann Shim --------------------------------------------
# Port = gateway.port in config/openclaw.json (ein Test haelt beide gleich).
PROBE_JS="fetch('http://127.0.0.1:19100/healthz').then((r)=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
OK=0
for _ in $(seq 1 30); do
  if docker exec "$CLAW" node -e "$PROBE_JS" >/dev/null 2>&1; then OK=1; break; fi
  sleep 2
done
if [ "$OK" = 1 ]; then
  echo "healthz: 200"
else
  echo "ABBRUCH: /healthz antwortet nach 60 s nicht (docker logs $CLAW ansehen)." >&2
  exit 1
fi

if docker exec "$SHIM" python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8125/v1/models', timeout=3)" >/dev/null 2>&1; then
  echo "shim /v1/models: ok"
else
  echo "ABBRUCH: Shim antwortet nicht (docker logs $SHIM ansehen)." >&2
  exit 1
fi
