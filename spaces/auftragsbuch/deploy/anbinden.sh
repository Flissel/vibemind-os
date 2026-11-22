#!/usr/bin/env bash
# deploy/anbinden.sh -- legt den Hash des Schluessels des Dienstes auftragsbuch ab.
# Laeuft auf dem Mini-PC im Ordner spaces/auftragsbuch (egal von wo aufgerufen), wiederholbar.
# Vorher hat deploy/db_zugang.sh laufzeit/dsn geschrieben.
#
#   printf '%s\n' "$SCHLUESSEL" | ssh <mini-pc> 'bash ~/auftragsbuch-build/spaces/auftragsbuch/deploy/anbinden.sh'
#
# Genau eine Zeile von stdin, nie als Argument, nie in einer Ausgabe: der Schluessel. Nur sein
# sha256-Hash kommt nach laufzeit/schluessel-hash; der Klartext wird nirgends abgelegt. Der Hash
# entsteht per Pipe in sha256sum (kein Argument, damit nichts in der Prozessliste steht). Ist
# stdin ein Terminal, bricht das Skript ab (der Schluessel soll nicht getippt und sichtbar werden).
set -euo pipefail
umask 077

cd "$(dirname "$0")/.."

if [ -t 0 ]; then
  echo "anbinden: der Schluessel kommt per Pipe auf stdin, nicht vom Terminal" >&2
  exit 2
fi
if [ ! -s laufzeit/dsn ]; then
  echo "anbinden: laufzeit/dsn fehlt, zuerst deploy/db_zugang.sh ausfuehren" >&2
  exit 2
fi

schluessel=""
read -r schluessel || true
if [ -z "$schluessel" ]; then
  echo "anbinden: eine nichtleere Zeile von stdin noetig (Schluessel)" >&2
  exit 2
fi

chmod 700 laufzeit
printf '%s' "$schluessel" | sha256sum | cut -d' ' -f1 > laufzeit/schluessel-hash
chmod 600 laufzeit/schluessel-hash
unset schluessel

echo "anbinden: laufzeit/schluessel-hash geschrieben"
