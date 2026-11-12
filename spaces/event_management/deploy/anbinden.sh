#!/usr/bin/env bash
# deploy/anbinden.sh -- bindet den Space event-management ans Laufzeit-Volume an:
# Saat der Konfiguration und der Arbeitsbereiche, Schluessel, Neustart, Probe.
# Wiederholbar. Muster: sales-claw deploy/post-anbinden.sh und gateway-saat.sh.
#
#   printf '%s\n%s\n%s\n%s\n' "$GW" "$TG" "$APP" "$CC" | bash deploy/anbinden.sh
#
# Genau vier Zeilen von stdin (nie als Argument, nie in einer Ausgabe):
#   1  Gateway-Token
#   2  Telegram-Bot-Token
#   3  Authorization-Wert der Event-App (Header des MCP-Servers `event-app`)
#   4  Claude-Code-OAuth-Token fuer den Shim; leer = laufzeit/claude-token
#      bleibt, wie es ist. Zeilen 1-3 duerfen nicht leer sein.
#
# Voraussetzung: `python deploy/fuellen.py werte.local.json build/` lief, und
# `docker compose up -d` hat den Claw-Container angelegt (laeuft er, ist es
# gut; es genuegt, dass er existiert und laeuft -- das Skript fasst Volumes
# nur ueber `docker exec` an).
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
#
# Ausgaben nennen nur Namen und Zustaende, nie Werte.
set -eu
umask 077

cd "$(dirname "$0")/.."

CLAW=event-management-claw

# --- 1. Vier Zeilen von stdin ------------------------------------------------
IFS= read -r GW || true
IFS= read -r TG || true
IFS= read -r APP || true
IFS= read -r CC || true
# CRLF aus der Pipeline wegnehmen (ein \r im Schluessel ist ein stiller Fehler).
GW=${GW%$'\r'}; TG=${TG%$'\r'}; APP=${APP%$'\r'}; CC=${CC%$'\r'}

if [ -z "$GW" ]; then echo "ABBRUCH: Zeile 1 (Gateway-Token) ist leer." >&2; exit 1; fi
if [ -z "$TG" ]; then echo "ABBRUCH: Zeile 2 (Telegram-Bot-Token) ist leer." >&2; exit 1; fi
if [ -z "$APP" ]; then echo "ABBRUCH: Zeile 3 (Authorization der Event-App) ist leer." >&2; exit 1; fi

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

[ -f build/openclaw.json ] || { echo "ABBRUCH: build/openclaw.json fehlt (deploy/fuellen.py ausfuehren)." >&2; exit 1; }
if [ "$(docker inspect -f '{{.State.Running}}' "$CLAW" 2>/dev/null)" != "true" ]; then
  echo "ABBRUCH: Container $CLAW laeuft nicht (docker compose up -d)." >&2
  exit 1
fi

GEAENDERT=0

# --- 2. Schluesseldateien auf dem Host (0600) -------------------------------
mkdir -p ./laufzeit ./laufzeit/shim-budget
printf '%s\n' "$GW" > ./laufzeit/gateway-token
chmod 600 ./laufzeit/gateway-token
echo "laufzeit/gateway-token: geschrieben"
if [ -n "$CC" ]; then
  printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$CC" > ./laufzeit/claude-token
  chmod 600 ./laufzeit/claude-token
  echo "laufzeit/claude-token: gesetzt (Shim muss neu angelegt werden, env_file wird nur beim Anlegen gelesen)"
else
  echo "laufzeit/claude-token: unveraendert"
fi

# --- 3. Saat ins Volume ------------------------------------------------------
# openclaw.json: fehlt sie, kommt die Saat; weicht sie ab (ohne die drei
# Schluesselfelder), wird sie gesichert und durch die Saat ersetzt. Von den
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
];
function ohneGeheim(o) {
  const c = JSON.parse(JSON.stringify(o));
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
    if (aktuell !== null && gleich(ohneGeheim(aktuell), ohneGeheim(saat))) {
      console.log('openclaw.json: unveraendert');
    } else {
      const zs = new Date().toISOString().replace(/[-:]/g, '').replace(/\..*/, '');
      fs.copyFileSync(CFG, CFG + '.vor-anbinden-' + zs);
      fs.chmodSync(CFG + '.vor-anbinden-' + zs, 0o600);
      fs.copyFileSync(SEED + '/openclaw.json', CFG);
      fs.chmodSync(CFG, 0o600);
      console.log('openclaw.json: gesichert und durch die Saat ersetzt');
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
AUS=$(docker exec -i "$CLAW" node -e "$SAAT_JS" </dev/null) || { printf '%s\n' "$AUS"; echo "ABBRUCH: Saat fehlgeschlagen." >&2; exit 1; }
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
AUS=$(printf '%s\n%s\n%s\n' "$GW" "$TG" "$APP" | docker exec -i "$CLAW" node -e "$SCHLUESSEL_JS") \
  || { printf '%s\n' "$AUS"; echo "ABBRUCH: Schluessel konnten nicht gesetzt werden." >&2; exit 1; }
printf '%s\n' "$AUS"
case "$AUS" in
  *": gesetzt"*) GEAENDERT=1 ;;
esac

# --- 5. Neustart nur bei Aenderung, dann /healthz ---------------------------
if [ "$GEAENDERT" = 1 ]; then
  docker restart "$CLAW" >/dev/null
  echo "$CLAW: neu gestartet"
else
  echo "$CLAW: kein Neustart noetig"
fi

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
  echo "ABBRUCH: /healthz antwortet nach 60 s nicht." >&2
  exit 1
fi
