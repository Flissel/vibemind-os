#!/usr/bin/env bash
# deploy/db_zugang.sh -- Datenbank-Zugang des Dienstes auftragsbuch, in einem Lauf.
# Laeuft auf dem Mini-PC im Ordner spaces/auftragsbuch (egal von wo aufgerufen), wiederholbar:
#
#   bash deploy/db_zugang.sh
#
# 1. erzeugt ein Passwort (openssl rand -hex 24) nur in einer Shell-Variable,
# 2. legt die Anmelde-Rolle auftragsbuch_dienst an oder setzt ihr Passwort neu und macht sie
#    zum Mitglied von brain_app (brain_app selbst bleibt NOLOGIN). Das SQL baut
#    deploy/db_zugang.py und schickt es ueber stdin an psql im Datenbank-Container; darin steht
#    nur der SCRAM-Verifier, nie das Passwort,
# 3. schreibt laufzeit/dsn (Modus 600, laufzeit/ mit 700).
# Datenbank-Container, Host-Name der Datenbank im Docker-Netz und Datenbank-Name stehen in
# laufzeit/werte.env (Vorlage deploy/werte.beispiel.env). Das Passwort steht nie als Argument,
# nie in einer Ausgabe und verlaesst den Mini-PC nie.
set -euo pipefail
umask 077

cd "$(dirname "$0")/.."

if [ ! -f laufzeit/werte.env ]; then
  echo "db_zugang: laufzeit/werte.env fehlt (Vorlage deploy/werte.beispiel.env)" >&2
  exit 2
fi
container=$(python3 deploy/db_zugang.py wert AUFTRAGSBUCH_DB_CONTAINER)
datenbank=$(python3 deploy/db_zugang.py wert AUFTRAGSBUCH_DB_NAME)
python3 deploy/db_zugang.py wert AUFTRAGSBUCH_DB_HOST > /dev/null
chmod 700 laufzeit

passwort=$(openssl rand -hex 24)
printf '%s\n' "$passwort" | python3 deploy/db_zugang.py sql \
  | docker exec -i "$container" psql -X -q -v ON_ERROR_STOP=1 -U supabase_admin -d "$datenbank" > /dev/null
printf '%s\n' "$passwort" | python3 deploy/db_zugang.py dsn-schreiben
unset passwort

echo "db_zugang: Rolle auftragsbuch_dienst bereit (Mitglied von brain_app), laufzeit/dsn geschrieben"
