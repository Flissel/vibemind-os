#!/usr/bin/env bash
# deploy/ausrollen.sh -- Ausrollen des Event-Systems (Etappe 4, Teil B) in Phasen, jede mit
# eigenem OK und eigenem Rueckweg. Anleitung, OK-Punkte, Abbruchkriterien: deploy/ausrollen.md.
#
#   bash deploy/ausrollen.sh --ich-habe-ok <phase> [argument]
#   bash deploy/ausrollen.sh --ich-habe-ok zurueck <phase> [argument]
#
# OHNE --ich-habe-ok TUT DAS SKRIPT NICHTS: Es prueft nur seine Argumente (Shell-Builtins)
# und endet mit 2, bevor irgendein Programm laeuft. Den Schalter setzt nur, wer Felix' OK
# fuer genau diese Phase und den Claim (WORKBOARD bzw. maschinenweite Koordination) hat.
#
# Phasen in dieser Reihenfolge. Jede erfolgreiche Phase schreibt laufzeit/ausrollen/<phase>.ok;
# ohne die Stempel aller Vorphasen bricht eine Phase mit dem Namen der fehlenden ab.
#   pruefen        Vorbedingungen messen, nichts aendern (PG >= 15, Images, werte.local.json,
#                  laufzeit/-Dateien 0600, Claim, .14 frei, Host-Python, Repo-Pfad der Units).
#   sichern        pg_dump -n event und openclaw.json des Claw nach laufzeit/sicherung/.
#   migrieren      db/anwenden.py als supabase_admin (DSN nur aus EVENT_ADMIN_DSN_FILE).
#   bauen system   genau EIN Image je Aufruf, gedeckelt (klassischer Builder, --memory).
#   bauen web      dito.
#   shim           Shim-Image mit dem neuen claude_budget.py (80-%-Warnung); nur den Shim
#                  neu anlegen. Rueckweg: vorheriges Image.
#   dienste        compose --profile event-system up -d event-api event-mcp event-web.
#   anbinden       Hinweis, dann deploy/anbinden.sh; die 7 Zeilen kommen ueber stdin.
#   timer          bereitstellung und nachrichten als systemd-Timer installieren, starten.
#   tailnet        tailscale serve fuer event-web.
#   zurueck <phase>  Rueckweg der Phase (nur, wenn keine spaetere Phase mehr gestempelt ist).
#
# Tags: EVENT_SYSTEM_TAG und EVENT_WEB_TAG verlangt das Compose bei JEDEM Aufruf. Ort: die
# nicht geheime Datei laufzeit/ausrollen/tags.env. `bauen` legt sie an (Git-Kurz-SHA,
# oder die beiden Variablen aus der Umgebung); alle anderen Phasen lesen sie und exportieren
# die Variablen an ihre Kinder. Weicht eine gesetzte Variable davon ab: Abbruch.
#
# Werte (Schluessel, Tokens, DSN) nur ueber stdin oder 0600-Dateien, nie als Argument, nie
# in einer Ausgabe. Ausgaben nennen nur Namen und Zustaende.
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
  echo "ausrollen.sh tut ohne --ich-habe-ok nichts (Ausrollen nur mit Felix' OK je Phase)." >&2
  echo "Erst OK und Claim, dann: bash deploy/ausrollen.sh --ich-habe-ok <phase>" >&2
  echo "Anleitung: deploy/ausrollen.md" >&2
  exit 2
fi
set -- "${ARGUMENTE[@]+"${ARGUMENTE[@]}"}"
set -o pipefail

# --- 1. Feste Ziele ---------------------------------------------------------------------
cd "$(dirname "$0")/.."
SPACE=$(pwd)
REPO=$(cd ../.. && pwd)
PYTHONPATH="$REPO"
export PYTHONPATH

CLAW=event-management-claw
SHIM=event-management-shim
NETZ=event-agent
MCP_IP=10.250.10.14
LZ=laufzeit
ST=$LZ/ausrollen
SICH=$LZ/sicherung
ADMIN_DSN_DATEI=${EVENT_ADMIN_DSN_FILE:-$LZ/admin-dsn}
BAU_SPEICHER=${AUSROLLEN_BAU_SPEICHER:-4g}
MEMINFO=${AUSROLLEN_MEMINFO:-/proc/meminfo}
ETC=${AUSROLLEN_ETC:-/etc/event-management}
SYSTEMD=${AUSROLLEN_SYSTEMD:-/etc/systemd/system}
TAILNET_PORT=${AUSROLLEN_TAILNET_PORT:-3170}
PHASEN="pruefen sichern migrieren bauen-system bauen-web shim dienste anbinden timer tailnet"
PGENV=$ST/pg.env
STAT=${AUSROLLEN_STAT:-stat}   # Messprogramme (nur Tests ersetzen sie)
CURL=${AUSROLLEN_CURL:-curl}
FLOCK=${AUSROLLEN_FLOCK:-flock}
GESUND_VERSUCHE=${AUSROLLEN_GESUND_VERSUCHE:-75}   # je 2 s: 150 s
case "$GESUND_VERSUCHE" in
  ''|*[!0-9]*) echo "ABBRUCH: AUSROLLEN_GESUND_VERSUCHE muss eine Zahl sein." >&2; exit 1 ;;
esac

# Test-Haken (Pfade/Programme ersetzbar): laut melden, nur Namen. BAU_SPEICHER und
# TAILNET_PORT sind Einstellungen, keine Haken.
HAKEN=""
for _v in $(compgen -v AUSROLLEN_); do
  case "$_v" in
    AUSROLLEN_BAU_SPEICHER|AUSROLLEN_TAILNET_PORT) ;;
    *) HAKEN="$HAKEN $_v" ;;
  esac
done
if [ -n "$HAKEN" ]; then
  echo "ACHTUNG: Test-Haken gesetzt:$HAKEN (im Betrieb nie setzen)" >&2
fi
# Speicherdeckel: Zahl + m|g, hoechstens 8g.
if [[ "$BAU_SPEICHER" =~ ^[1-9][0-9]*[mg]$ ]]; then
  _n=${BAU_SPEICHER%[mg]}
  if [ "${BAU_SPEICHER: -1}" = g ]; then _n=$((_n * 1024)); fi
  [ "$_n" -le 8192 ] || { echo "ABBRUCH: AUSROLLEN_BAU_SPEICHER darf hoechstens 8g sein." >&2; exit 1; }
else
  echo "ABBRUCH: AUSROLLEN_BAU_SPEICHER muss Zahl plus m oder g sein (z. B. 4g, hoechstens 8g)." >&2
  exit 1
fi

modus_von() { "$STAT" -c %a "$1"; }
abbruch() { echo "ABBRUCH: $*" >&2; exit 1; }
info() { echo "$*"; }
pgenv_weg() { rm -f "${PGENV:?}"; }
trap pgenv_weg EXIT

shim_image() { grep -m1 -o 'event-management/shim:[A-Za-z0-9_.-]*' docker-compose.yml; }
claw_image() { grep -m1 -o 'ghcr.io/openclaw/openclaw:[A-Za-z0-9_.-]*' docker-compose.yml; }

# --- 2. Stempel und Reihenfolge ---------------------------------------------------------
stempel() { printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$ST/$1.ok"; }
stempel_weg() { rm -f "${ST:?}/${1:?}.ok"; }
# Ruling T6-a: jede Phase loescht vor ihrer Arbeit den eigenen Stempel und die aller
# spaeteren Phasen; ein gescheiterter Wiederholungslauf hinterlaesst so keinen alten Stempel.
eigene_und_spaetere_stempel_weg() {
  local q ab=0
  for q in $PHASEN; do
    [ "$q" = "$1" ] && ab=1
    if [ "$ab" = 1 ]; then stempel_weg "$q"; fi
  done
}

vorphasen() {
  case "$1" in
    bauen-system|bauen-web) echo "pruefen sichern migrieren" ;;
    *)
      local p out=""
      for p in $PHASEN; do
        [ "$p" = "$1" ] && break
        out="$out $p"
      done
      echo "$out" ;;
  esac
}

verlange_vorphasen() {
  local p
  for p in $(vorphasen "$1"); do
    [ -f "$ST/$p.ok" ] || abbruch "Vorphase $p fehlt (laufzeit/ausrollen/$p.ok). Erst: --ich-habe-ok ${p/-/ }"
  done
}

# --- 3. Tags ------------------------------------------------------------------------------
tags_pruefen() {
  local k v
  for k in EVENT_SYSTEM_TAG EVENT_WEB_TAG; do
    v=${!k:-}
    [[ "$v" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] || abbruch "$k fehlt oder ist ungueltig (laufzeit/ausrollen/tags.env, erst 'bauen')"
  done
}

tags_laden() {
  local f="$ST/tags.env" k v cur
  if [ -f "$f" ]; then
    while IFS='=' read -r k v || [ -n "$k" ]; do
      v=${v%$'\r'}
      case "$k" in
        EVENT_SYSTEM_TAG|EVENT_WEB_TAG)
          cur=${!k:-}
          if [ -n "$cur" ] && [ "$cur" != "$v" ]; then abbruch "$k in der Umgebung weicht von $f ab"; fi
          printf -v "$k" '%s' "$v"
          export "$k" ;;
      esac
    done < "$f"
  fi
  tags_pruefen
  export EVENT_SYSTEM_TAG EVENT_WEB_TAG
}

tags_anlegen() {
  local t=""
  if [ ! -f "$ST/tags.env" ]; then
    t=$(git -C "$REPO" rev-parse --short=8 HEAD 2>/dev/null) || t=""
    : "${EVENT_SYSTEM_TAG:=$t}"
    : "${EVENT_WEB_TAG:=$t}"
    tags_pruefen
    printf 'EVENT_SYSTEM_TAG=%s\nEVENT_WEB_TAG=%s\n' "$EVENT_SYSTEM_TAG" "$EVENT_WEB_TAG" > "$ST/tags.env"
    info "tags.env: angelegt (Tags nur Namen, kein Geheimnis)"
  fi
  tags_laden
}

# --- 4. Datenbank-Zugang ohne Geheimnis im Argument -----------------------------------------
pg_bereit() { python3 deploy/pg_hilfe.py env "$ADMIN_DSN_DATEI" "$PGENV" >/dev/null || return 1; }
pg_mit() { ( set -a; . "./$PGENV"; set +a; "$@" ); }
pg_version_num() { pg_mit psql -Atq -c 'show server_version_num' </dev/null 2>/dev/null || echo 0; }

# --- 5. Phasen ----------------------------------------------------------------------------------

phase_pruefen() {
  local FEHLER=0 k f
  ok() { echo "ok     $1"; }
  fehlt() { echo "FEHLT  $1"; FEHLER=$((FEHLER + 1)); }

  if [ -s "$ST/claim" ]; then ok "Claim gesetzt (laufzeit/ausrollen/claim)"
  else fehlt "Claim (WORKBOARD/Koordination eintragen, dann Verweis in laufzeit/ausrollen/claim)"; fi

  # werte.local.json: jeder Schluessel der Vorlage mit nicht leerem Textwert
  if [ -f werte.local.json ]; then
    local leer=0
    for k in $(sed -n 's/^[[:space:]]*"\([A-Z_][A-Z_]*\)"[[:space:]]*:.*/\1/p' werte.beispiel.json); do
      if ! grep -Eq "\"$k\"[[:space:]]*:[[:space:]]*\"[^\"]+\"" werte.local.json; then
        fehlt "werte.local.json: Wert fuer $k"; leer=1
      fi
    done
    [ "$leer" = 0 ] && ok "werte.local.json vollstaendig"
  else
    fehlt "werte.local.json"
  fi

  # laufzeit/-Dateien: da und 0600
  for f in "$ADMIN_DSN_DATEI" "$LZ/event-app-dsn" "$LZ/event-sitzung-geheimnis" \
           "$LZ/event-telegram-token" "$LZ/event-smtp-passwort" "$LZ/event-app.env"; do
    if [ ! -f "$f" ]; then fehlt "$f fehlt"
    elif [ "$(modus_von "$f")" != 600 ]; then fehlt "$f hat nicht 0600"
    else ok "$f (0600)"; fi
  done

  # Images: Shim und Claw muessen da sein; system/web baut `bauen`
  for k in "$(shim_image)" "$(claw_image)"; do
    if docker image inspect "$k" >/dev/null 2>&1; then ok "Image $k"; else fehlt "Image $k"; fi
  done
  if [ -f "$ST/tags.env" ]; then
    tags_laden
    for k in "event-management/system:$EVENT_SYSTEM_TAG" "event-management/web:$EVENT_WEB_TAG"; do
      if docker image inspect "$k" >/dev/null 2>&1; then ok "Image $k"; else info "offen  Image $k (kommt mit 'bauen')"; fi
    done
  else
    info "offen  Images system/web (kommen mit 'bauen')"
  fi

  # .14 im Netz event-agent frei (nur Lesen)
  local belegt
  if belegt=$(docker network inspect "$NETZ" -f '{{range .Containers}}{{.Name}} {{.IPv4Address}}{{"\n"}}{{end}}' 2>/dev/null); then
    if printf '%s\n' "$belegt" | grep -F "$MCP_IP/" | grep -qv '^event-mcp '; then
      fehlt "Adresse $MCP_IP im Netz $NETZ ist von einem anderen Container belegt (Container neu starten)"
    else
      ok "Adresse $MCP_IP im Netz $NETZ frei"
    fi
  else
    ok "Netz $NETZ noch nicht angelegt ($MCP_IP frei)"
  fi

  # Datenbank: PG >= 15
  if pg_bereit; then
    local nr
    nr=$(pg_version_num)
    case "$nr" in ''|*[!0-9]*) nr=0 ;; esac
    if [ "$nr" -ge 150000 ]; then ok "PostgreSQL >= 15"; else fehlt "PostgreSQL >= 15 (gemessen: $((nr / 10000)))"; fi
  else
    fehlt "Datenbank-Zugang (DSN-Datei lesbar, psycopg auf dem Host?)"
  fi
  pgenv_weg

  # Host-Units (bereitstellung, nachrichten): Python-Abhaengigkeiten, Repo-Pfad, Env-Dateien
  local fehlende
  if fehlende=$(python3 deploy/pg_hilfe.py deps requirements.txt 2>/dev/null); then
    if [ -z "$fehlende" ]; then ok "Host-Python hat die Abhaengigkeiten aus requirements.txt"
    else fehlt "Host-Python ohne Module: $(printf '%s' "$fehlende" | tr '\n' ' ')"; fi
  else
    fehlt "Host-Python (python3 deps) nicht pruefbar"
  fi
  for f in bereitstellung nachrichten; do
    local wd
    wd=$(sed -n 's/^WorkingDirectory=//p' "deploy/$f.service" | head -1)
    if [ "$wd" = "$REPO" ]; then ok "Repo liegt unter $wd (von $f.service erwartet)"
    else fehlt "$f.service erwartet das Repo unter $wd (hier: $REPO)"; fi
    if [ ! -f "$ETC/$f.env" ]; then fehlt "$ETC/$f.env fehlt"
    elif [ "$(modus_von "$ETC/$f.env")" != 600 ]; then fehlt "$ETC/$f.env hat nicht 0600"
    else ok "$ETC/$f.env (0600)"; fi
  done

  if [ "$FEHLER" -gt 0 ]; then
    echo "pruefen: $FEHLER Punkt(e) offen." >&2
    exit 1
  fi
  stempel pruefen
  info "pruefen: alles in Ordnung."
}

phase_sichern() {
  mkdir -p "$SICH"
  local ts
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  pg_bereit || abbruch "Datenbank-Zugang nicht lesbar"
  # Ruling T6-b: gibt es das Schema event noch nicht (erster Lauf), gibt es nichts zu sichern.
  local schema
  schema=$(pg_mit psql -Atq -c "select 1 from pg_namespace where nspname='event'" </dev/null)     || abbruch "Schema-Pruefung fehlgeschlagen"
  if [ -z "$schema" ]; then
    pgenv_weg
    : > "$SICH/event-$ts.keine-schema"
    info "Schema event existiert noch nicht: nichts zu sichern (Marke event-$ts.keine-schema)"
  else
    pg_mit pg_dump -n event -Fc -f "$SICH/event-$ts.dump" </dev/null || abbruch "pg_dump fehlgeschlagen"
    pgenv_weg
    [ -s "$SICH/event-$ts.dump" ] || abbruch "Sicherung ist leer"
    info "Sicherung: $SICH/event-$ts.dump"
  fi
  if docker container inspect "$CLAW" >/dev/null 2>&1; then
    docker cp "$CLAW:/home/node/.openclaw/openclaw.json" "$SICH/openclaw-$ts.json" >/dev/null \
      || abbruch "openclaw.json nicht kopierbar"
    chmod 600 "$SICH/openclaw-$ts.json"
    info "Sicherung: $SICH/openclaw-$ts.json (mit Schluesseln, 0600)"
  else
    : > "$SICH/openclaw-$ts.keine"
    info "Kein Claw-Container: nichts zu sichern (Marke openclaw-$ts.keine)"
  fi
  stempel sichern
}

phase_migrieren() {
  pg_bereit || abbruch "Datenbank-Zugang nicht lesbar"
  ( . "./$PGENV"; [ "${PGUSER:-}" = supabase_admin ] ) || abbruch "Die DSN muss die Rolle supabase_admin nutzen"
  local nr
  nr=$(pg_version_num)
  pgenv_weg
  case "$nr" in ''|*[!0-9]*) nr=0 ;; esac
  [ "$nr" -ge 150000 ] || abbruch "PostgreSQL ist aelter als 15"
  EVENT_ADMIN_DSN_FILE="$ADMIN_DSN_DATEI" python3 -m spaces.event_management.db.anwenden </dev/null \
    || abbruch "Migration fehlgeschlagen (Sicherung: $SICH)"
  stempel migrieren
}

bau_speicher_pruefen() {
  [ -r "$MEMINFO" ] || abbruch "$MEMINFO nicht lesbar: ohne Speichermessung wird nicht gebaut"
  local kb
  kb=$(awk '/^MemAvailable:/ {print $2}' "$MEMINFO")
  case "$kb" in ''|*[!0-9]*) abbruch "MemAvailable nicht lesbar" ;; esac
  [ "$kb" -ge 1572864 ] || abbruch "Nur $((kb / 1024)) MiB frei (MemAvailable), gebaut wird ab 1,5 GiB"
}

phase_bauen() {
  local was=${1:-}
  case "$was" in
    system|web) ;;
    *) abbruch "bauen braucht genau ein Argument: system oder web (ein Image je Aufruf)" ;;
  esac
  [ "$#" -le 1 ] || abbruch "bauen baut genau ein Image je Aufruf"
  tags_anlegen
  bau_speicher_pruefen
  # Klassischer Builder: nur er nimmt --memory (BuildKit ohne Deckel trieb die VM in Swap-Not).
  export DOCKER_BUILDKIT=0
  if [ "$was" = system ]; then
    docker build --memory="$BAU_SPEICHER" --memory-swap="$BAU_SPEICHER" \
      -f Dockerfile.system -t "event-management/system:$EVENT_SYSTEM_TAG" . </dev/null \
      || abbruch "Build system fehlgeschlagen"
  else
    docker build --memory="$BAU_SPEICHER" --memory-swap="$BAU_SPEICHER" \
      --build-arg EVENT_API_URL=http://event-api:8170 \
      -t "event-management/web:$EVENT_WEB_TAG" web/ </dev/null \
      || abbruch "Build web fehlgeschlagen"
  fi
  stempel "bauen-$was"
}

shim_gesund() {
  local i s
  for i in $(seq 1 "$GESUND_VERSUCHE"); do
    s=$(docker inspect -f '{{.State.Health.Status}}' "$SHIM" 2>/dev/null || true)
    [ "$s" = healthy ] && return 0
    sleep 2
  done
  return 1
}

phase_shim() {
  tags_laden
  local img
  img=$(shim_image)
  docker image inspect "$img" >/dev/null 2>&1 || abbruch "Image $img fehlt (Quelle: bewerbung-bot, siehe ausrollen.md)"
  # Das Image backt claude_budget.py ein (rowboat/deploy/headless/shim/Dockerfile). Der
  # Baukontext (Dockerfile, Shim-Dateien, neues claude_budget.py) liegt nicht in diesem
  # Repo; wer ihn vorbereitet hat, nennt ihn in EVENT_SHIM_BAUKONTEXT.
  if [ -n "${EVENT_SHIM_BAUKONTEXT:-}" ]; then
    [ -f "$EVENT_SHIM_BAUKONTEXT/Dockerfile" ] || abbruch "EVENT_SHIM_BAUKONTEXT hat kein Dockerfile"
    bau_speicher_pruefen
    # Ruling T6-c: den Rueckweg nur setzen, wenn es noch keinen gibt oder der letzte Shim-Lauf
    # gelang; nach einem gescheiterten Lauf zeigt `vorher` weiter auf das Original.
    if [ ! -f "$ST/shim.vorher" ] || [ "${SHIM_OK_VORHER:-nein}" = ja ]; then
      docker tag "$img" "${img%:*}:vorher" || abbruch "Rueckweg-Tag nicht setzbar"
      docker image inspect -f '{{.Id}}' "$img" > "$ST/shim.vorher"
    fi
    export DOCKER_BUILDKIT=0
    docker build --memory="$BAU_SPEICHER" --memory-swap="$BAU_SPEICHER" -t "$img" "$EVENT_SHIM_BAUKONTEXT" </dev/null \
      || abbruch "Build shim fehlgeschlagen (Rueckweg: zurueck shim)"
  else
    info "HINWEIS: ohne EVENT_SHIM_BAUKONTEXT bleibt das eingebackene claude_budget.py alt (keine 80-%-Warnung)."
  fi
  mkdir -p "$LZ/shim-budget"
  docker compose up -d --no-deps --force-recreate "$SHIM" || abbruch "Shim nicht neu angelegt"
  shim_gesund || abbruch "Shim wird nicht gesund (docker logs $SHIM ansehen, Rueckweg: zurueck shim)"
  info "$SHIM: neu angelegt, gesund"
  stempel shim
}

probe() {
  local name=$1 url=$2 genau=$3 i code
  for i in $(seq 1 30); do
    code=$("$CURL" -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || true)
    case "$code" in
      200) info "$name: 200"; return 0 ;;
      ''|000) ;;
      *) if [ "$genau" = nein ] && [ "$code" -lt 500 ]; then info "$name: $code (lebt)"; return 0; fi ;;
    esac
    sleep 2
  done
  abbruch "$name antwortet nicht (docker logs ansehen, Rueckweg: zurueck dienste)"
}

phase_dienste() {
  tags_laden
  docker compose --profile event-system up -d event-api event-mcp event-web </dev/null \
    || abbruch "Dienste nicht gestartet (Rueckweg: zurueck dienste)"
  probe event-mcp http://127.0.0.1:8171/gesund ja
  probe event-api http://127.0.0.1:8170/api/ich nein
  probe event-web http://127.0.0.1:3170/ nein
  stempel dienste
}

phase_anbinden() {
  tags_laden
  if [ -t 0 ]; then
    abbruch "anbinden liest die 7 Zeilen von stdin (Gateway, Telegram, Event-App, CC, melden, bn-lesen, bn-auftrag), nicht vom Terminal"
  fi
  [ -f "$ST/workspace-geprueft" ] || abbruch "Erst den Live-Arbeitsbereich pruefen: anbinden.sh ueberschreibt SOUL.md, AGENTS.md und skills je Arbeitsbereich. workspace-legacy im Claw-Volume mit config/workspace-legacy vergleichen (ausrollen.md, Schritt vor anbinden); danach: touch $ST/workspace-geprueft"
  info "Hinweis: anbinden.sh ueberschreibt die Arbeitsbereiche bei JEDEM Lauf (Vergleich: ausrollen.md)."
  python3 deploy/fuellen.py werte.local.json build/ </dev/null || abbruch "fuellen.py fehlgeschlagen"
  bash deploy/anbinden.sh || abbruch "anbinden.sh fehlgeschlagen (Rueckweg: zurueck anbinden)"
  stempel anbinden
}

phase_timer() {
  local u
  for u in bereitstellung nachrichten; do
    [ -f "$ETC/$u.env" ] || abbruch "$ETC/$u.env fehlt"
    sudo cp "deploy/$u.service" "deploy/$u.timer" "$SYSTEMD/" || abbruch "Units nicht installiert"
  done
  sudo systemctl daemon-reload
  sudo systemctl enable --now bereitstellung.timer nachrichten.timer || abbruch "Timer nicht gestartet (Rueckweg: zurueck timer)"
  systemctl is-active bereitstellung.timer nachrichten.timer >/dev/null || abbruch "Timer laufen nicht"
  info "Timer bereitstellung und nachrichten: aktiv"
  stempel timer
}

phase_tailnet() {
  tailscale serve --bg --https="$TAILNET_PORT" "http://127.0.0.1:3170" || abbruch "tailscale serve fehlgeschlagen"
  tailscale serve status || true
  stempel tailnet
}

# --- 6. Rueckweg --------------------------------------------------------------------------------

neueste_sicherung() { ls -1 "$SICH"/openclaw-* 2>/dev/null | sort | tail -1 || true; }

phase_zurueck() {
  local p=${1:-}
  case "$p" in
    pruefen|sichern|migrieren|bauen|shim|dienste|anbinden|timer|tailnet) ;;
    *) abbruch "zurueck braucht eine Phase: pruefen sichern migrieren bauen shim dienste anbinden timer tailnet" ;;
  esac
  if [ "$p" = bauen ]; then
    case "${2:-}" in system|web) p="bauen-$2" ;; *) abbruch "zurueck bauen system|web" ;; esac
  fi
  # Nur rueckwaerts: spaetere Phasen zuerst zuruecknehmen.
  local q spaeter=0
  for q in $PHASEN; do
    if [ "$spaeter" = 1 ] && [ -f "$ST/$q.ok" ]; then
      abbruch "Phase $q ist noch gestempelt: erst 'zurueck ${q/-/ }'"
    fi
    [ "$q" = "$p" ] && spaeter=1
  done
  case "$p" in
    pruefen|sichern)
      info "$p: nur der Stempel faellt (Sicherungen in $SICH bleiben liegen)." ;;
    migrieren)
      info "migrieren: kein automatischer Rueckweg (Schema event ist neu, ein Loeschen ist destruktiv)."
      info "Von Hand mit eigenem OK: DROP SCHEMA event CASCADE; danach bei Bedarf pg_restore aus $SICH/event-*.dump."
      info "Erst danach den Stempel $ST/migrieren.ok entfernen."
      return 0 ;;
    bauen-system)
      tags_laden
      docker rmi "event-management/system:$EVENT_SYSTEM_TAG" || abbruch "Image wird noch benutzt (erst zurueck dienste)" ;;
    bauen-web)
      tags_laden
      docker rmi "event-management/web:$EVENT_WEB_TAG" || abbruch "Image wird noch benutzt (erst zurueck dienste)" ;;
    shim)
      tags_laden
      if [ -f "$ST/shim.vorher" ]; then
        docker tag "$(cat "$ST/shim.vorher")" "$(shim_image)" || abbruch "vorheriges Image nicht mehr da"
        docker compose up -d --no-deps --force-recreate "$SHIM"
        shim_gesund || abbruch "Shim wird nicht gesund"
        rm -f "${ST:?}/shim.vorher"
      else
        info "shim: es wurde nichts gebaut, nur neu angelegt; kein Image zurueckzusetzen."
      fi ;;
    dienste)
      tags_laden
      docker compose --profile event-system stop event-web event-mcp event-api ;;
    anbinden)
      tags_laden
      local s
      s=$(neueste_sicherung)
      [ -n "$s" ] || abbruch "keine Sicherung von openclaw.json in $SICH"
      case "$s" in
        *.json)
          docker cp "$s" "$CLAW:/home/node/.openclaw/openclaw.json" || abbruch "Zurueckspielen fehlgeschlagen"
          docker compose restart "$CLAW"
          info "openclaw.json aus der Sicherung zurueckgespielt, Claw neu gestartet." ;;
        *)
          docker compose stop "$CLAW"
          info "Frisches Volume vor dem Ausrollen: Claw gestoppt, Volume bleibt (nichts geloescht)." ;;
      esac
      info "Schluessel dieses Laufs von Hand widerrufen: system.schluessel widerrufen <id> (ausrollen.md)." ;;
    timer)
      sudo systemctl disable --now bereitstellung.timer nachrichten.timer || true
      sudo rm -f "${SYSTEMD:?}/bereitstellung.service" "${SYSTEMD:?}/bereitstellung.timer" \
                 "${SYSTEMD:?}/nachrichten.service" "${SYSTEMD:?}/nachrichten.timer"
      sudo systemctl daemon-reload ;;
    tailnet)
      tailscale serve --https="$TAILNET_PORT" off ;;
  esac
  stempel_weg "$p"
  info "zurueck $p: erledigt."
}

# --- 7. Aufruf -------------------------------------------------------------------------------------

PHASE=${1:-}
[ "$#" -gt 0 ] && shift
case "$PHASE" in
  pruefen|sichern|migrieren|bauen|shim|dienste|anbinden|timer|tailnet|zurueck) ;;
  *)
    echo "Phase unbekannt oder fehlt. Phasen: pruefen sichern migrieren bauen(system|web) shim dienste anbinden timer tailnet, dazu zurueck <phase>" >&2
    exit 64 ;;
esac
mkdir -p "$ST"
# Nie zwei Laeufe zugleich (auch zurueck): nicht blockierende Sperre.
if ! type -P "$FLOCK" >/dev/null 2>&1 && [ ! -x "$FLOCK" ]; then
  abbruch "flock fehlt (util-linux), ohne Sperre wird nicht ausgerollt"
fi
exec 9>"$ST/lock"
"$FLOCK" -n 9 || abbruch "Ein anderer Lauf haelt laufzeit/ausrollen/lock; nie parallel ausrollen."
if [ "$PHASE" = zurueck ]; then
  phase_zurueck "$@"
  exit 0
fi
# Stand des letzten Shim-Laufs merken, bevor Ruling T6-a den Stempel loescht.
SHIM_OK_VORHER=nein
[ -f "$ST/shim.ok" ] && SHIM_OK_VORHER=ja
if [ "$PHASE" = bauen ]; then
  case "${1:-}" in
    system|web) verlange_vorphasen "bauen-$1"; eigene_und_spaetere_stempel_weg "bauen-$1" ;;
  esac
  phase_bauen "$@"
else
  [ "$#" -eq 0 ] || abbruch "Phase $PHASE nimmt kein Argument"
  verlange_vorphasen "$PHASE"
  eigene_und_spaetere_stempel_weg "$PHASE"
  "phase_$PHASE"
fi
