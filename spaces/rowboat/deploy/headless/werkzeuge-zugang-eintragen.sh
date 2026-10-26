#!/usr/bin/env bash
# Traegt einen Werkzeug-Zugang (GitHub-Nur-Lese-Token je Besitzer, Schluessel der PC-Suche) in
# ~/rowboat-headless/werkzeuge.env ein (env_file des Dienstes rowboat-werkzeuge) und erzeugt NUR
# diesen Dienst neu, wenn er schon laeuft.
#
#   bash ~/rowboat-headless/werkzeuge-zugang-eintragen.sh <gruppe>
#   gruppe: github_flissel | github_vibemind_lab | pc_suche
#
# Regelweg ist der PC-Befehl `rowboat-zugang speichern <gruppe>` (Vibemind_V1,
# scripts/rowboat_zugang.py): der OpenFang-Tresor am PC ist die Wahrheit, der Befehl reicht die
# Werte ueber ssh-stdin an dieses Skript.
#
# Abfragen ohne Geheimnis (lesen nichts, schreiben nichts):
#   --gruppen          alle Gruppen, durch Leerzeichen getrennt
#   --namen <gruppe>   die Env-Namen der Gruppe in Eingabe-Reihenfolge
#
# Sicherheit (Plan 2026-10-26-rowboat-zentrale-teil-d-werkzeuge, Task 5):
# * Werte werden verdeckt gelesen (read -s) und NIE ausgegeben, auch nicht teilweise; Ablaufverfolgung bleibt aus.
# * Werte gehen nie als Argument an ein Programm, nur ueber printf (eingebaut) in die Datei.
# * Nur Zeichen aus [A-Za-z0-9._:/@+=~%?&-]; sonst Abbruch, nichts geschrieben.
# * Erst wenn ALLE Werte gelesen und geprueft sind, wird die Datei angefasst: umask 077,
#   Sicherung (600), neue Datei im selben Ordner (600), mv darueber. Nur die Zeilen der Gruppe
#   werden ersetzt, alle anderen bleiben.
#
# Die Namen stehen woertlich wie in rowboat_zugang.GRUPPEN und in GITHUB_BESITZER (compose.yml);
# spaces/rowboat/tests/test_werkzeuge_zugang_skript.py prueft den Gleichlauf.
set +x
set -euo pipefail

ENV_DATEI="${WERKZEUGE_ENV:-$HOME/rowboat-headless/werkzeuge.env}"
PROJEKT="$(dirname "$ENV_DATEI")"
DOCKER="${DOCKER:-docker}"
ERLAUBT='^[A-Za-z0-9._:/@+=~%?&-]+$'
GRUPPEN_LISTE="github_flissel github_vibemind_lab pc_suche"

if [ "${1:-}" = "--gruppen" ]; then
  echo "$GRUPPEN_LISTE"
  exit 0
fi
nur_namen=0
if [ "${1:-}" = "--namen" ]; then
  nur_namen=1
  shift
fi

gruppe="${1:-}"
case "$gruppe" in
  github_flissel)      NAMEN=(GITHUB_LESEN_FLISSEL) ;;
  github_vibemind_lab) NAMEN=(GITHUB_LESEN_VIBEMIND_LAB) ;;
  pc_suche)            NAMEN=(PC_SUCHE_KEY) ;;
  *)
    echo "Aufruf: $0 <gruppe> — bekannte Gruppen: $GRUPPEN_LISTE" >&2
    exit 2 ;;
esac

if [ "$nur_namen" = 1 ]; then
  echo "${NAMEN[*]}"
  exit 0
fi

if [ ! -d "$PROJEKT" ]; then
  echo "Ordner $PROJEKT fehlt – abgebrochen, nichts geändert." >&2
  exit 1
fi

# 1) Alle Werte lesen und pruefen, bevor irgendetwas geschrieben wird.
WERTE=()
for name in "${NAMEN[@]}"; do
  wert=""
  IFS= read -r -s -p "$name: " wert || true
  [ -t 0 ] && echo >&2
  wert="${wert%$'\r'}"
  if [ -z "$wert" ]; then
    echo "$name ist leer – abgebrochen, nichts geändert." >&2
    exit 3
  fi
  if ! [[ $wert =~ $ERLAUBT ]]; then
    echo "$name enthält unzulässige Zeichen – abgebrochen, nichts geändert." >&2
    exit 3
  fi
  WERTE+=("$wert")
done
unset wert

# 2) Sicherung (falls vorhanden) und neue Datei, beide nur fuer den Eigentuemer lesbar.
umask 077
SICHERUNG=""
if [ -f "$ENV_DATEI" ]; then
  stempel="$(date +%Y%m%d-%H%M%S)"
  SICHERUNG="$ENV_DATEI.vor-$gruppe-$stempel"
  [ -e "$SICHERUNG" ] && SICHERUNG="$SICHERUNG-$$"
  cp "$ENV_DATEI" "$SICHERUNG"
  chmod 600 "$SICHERUNG"
fi

NEU="$(mktemp "$PROJEKT/.werkzeuge.env.neu.XXXXXX")"
trap 'rm -f "$NEU"' EXIT
if [ -f "$ENV_DATEI" ]; then
  muster="^($(IFS='|'; echo "${NAMEN[*]}"))="
  # grep: Exit 1 = keine Zeile uebrig (kein Fehler), 2 = echter Fehler
  grep -Ev "$muster" "$ENV_DATEI" > "$NEU" || [ $? -eq 1 ]
  if [ -s "$NEU" ] && [ -n "$(tail -c 1 "$NEU")" ]; then
    printf '\n' >> "$NEU"
  fi
fi
for i in "${!NAMEN[@]}"; do
  printf '%s=%s\n' "${NAMEN[$i]}" "${WERTE[$i]}" >> "$NEU"
done
unset WERTE
chmod 600 "$NEU"
mv -f "$NEU" "$ENV_DATEI"
trap - EXIT
hinweis="${#NAMEN[@]} Wert(e) für $gruppe eingetragen${SICHERUNG:+, Sicherung: $(basename "$SICHERUNG")}"

# 3) Nur rowboat-werkzeuge neu erzeugen, und nur wenn er schon laeuft (erst `up` liest die Datei neu).
# shellcheck disable=SC2086
laeuft="$(cd "$PROJEKT" && $DOCKER compose ps --status running -q rowboat-werkzeuge 2>/dev/null || true)"
if [ -z "$laeuft" ]; then
  echo "$hinweis. rowboat-werkzeuge läuft nicht – nichts neu erzeugt."
  exit 0
fi
# shellcheck disable=SC2086
if ! ( cd "$PROJEKT" && $DOCKER compose up -d --no-deps --force-recreate rowboat-werkzeuge ); then
  echo "$hinweis, aber rowboat-werkzeuge ließ sich nicht neu erzeugen – siehe Meldung oben." >&2
  exit 1
fi
echo "$hinweis. rowboat-werkzeuge neu erzeugt."
