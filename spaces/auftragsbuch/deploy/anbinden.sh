#!/usr/bin/env bash
# deploy/anbinden.sh -- legt die beiden Geheimnisse des Dienstes auftragsbuch ab.
# Laeuft auf dem Mini-PC im Ordner spaces/auftragsbuch (egal von wo aufgerufen), wiederholbar.
#
#   printf '%s\n%s\n' "$DSN" "$SCHLUESSEL" | bash deploy/anbinden.sh
#
# Genau zwei Zeilen von stdin, nie als Argument, nie in einer Ausgabe:
#   1  DSN der Datenbank-Rolle brain_app  -> laufzeit/dsn
#   2  Schluessel des Dienstes            -> nur sein sha256-Hash nach laufzeit/schluessel-hash
# Der Klartext des Schluessels wird nirgends abgelegt. Der Hash entsteht per Pipe in sha256sum
# (kein Argument, damit nichts in der Prozessliste steht). Das Skript gibt keinen der beiden
# Werte aus, auch nicht im Fehlerfall.
set -euo pipefail
umask 077

cd "$(dirname "$0")/.."

dsn=""
schluessel=""
read -r dsn || true
read -r schluessel || true
if [ -z "$dsn" ] || [ -z "$schluessel" ]; then
  echo "anbinden: zwei nichtleere Zeilen von stdin noetig (DSN, Schluessel)" >&2
  exit 2
fi

mkdir -p laufzeit
chmod 700 laufzeit

printf '%s\n' "$dsn" > laufzeit/dsn
printf '%s' "$schluessel" | sha256sum | cut -d' ' -f1 > laufzeit/schluessel-hash
chmod 600 laufzeit/dsn laufzeit/schluessel-hash
unset dsn schluessel

echo "anbinden: laufzeit/dsn und laufzeit/schluessel-hash geschrieben"
