#!/usr/bin/env bash
# deploy/db_zugang.sh -- Datenbank-Zugang einer Anmelde-Rolle, in einem Lauf.
# Laeuft auf dem Mini-PC im Ordner spaces/auftragsbuch (egal von wo aufgerufen), wiederholbar:
#
#   bash deploy/db_zugang.sh            # Dienst auftragsbuch: Rolle auftragsbuch_dienst in brain_app
#   bash deploy/db_zugang.sh abholer    # Rowboat-Abholer: Rolle rowboat_abholer in rowboat_empfang
#
# Das einzige Argument ist der Name des Zugangs (fest in deploy/db_zugang.py, nie ein Geheimnis).
# 1. prueft Zugang und Datenbank-Werte, bevor etwas geaendert wird,
# 2. erzeugt ein Passwort (openssl rand -hex 24) nur in einer Shell-Variable,
# 3. legt die Anmelde-Rolle an oder setzt ihr Passwort neu und macht sie zum Mitglied ihrer Gruppe
#    (die Gruppe selbst bleibt NOLOGIN). Das SQL baut deploy/db_zugang.py und schickt es ueber stdin
#    an psql im Datenbank-Container; darin steht nur der SCRAM-Verifier, nie das Passwort,
# 4. schreibt die DSN-Datei des Zugangs (laufzeit/dsn bzw. laufzeit/abholer-dsn, Modus 600,
#    laufzeit/ mit 700).
# Datenbank-Container, Host, Port und Name der Datenbank stehen in laufzeit/werte.env (Vorlage
# deploy/werte.beispiel.env). Das Passwort steht nie als Argument, nie in einer Ausgabe und
# verlaesst den Mini-PC nie.
set -euo pipefail
umask 077

cd "$(dirname "$0")/.."

if [ "$#" -gt 1 ]; then
  echo "db_zugang: hoechstens ein Argument (dienst oder abholer)" >&2
  exit 2
fi
zugang="${1:-dienst}"
if [ ! -f laufzeit/werte.env ]; then
  echo "db_zugang: laufzeit/werte.env fehlt (Vorlage deploy/werte.beispiel.env)" >&2
  exit 2
fi
container=$(python3 deploy/db_zugang.py wert AUFTRAGSBUCH_DB_CONTAINER)
datenbank=$(python3 deploy/db_zugang.py wert AUFTRAGSBUCH_DB_NAME)
angabe=$(python3 deploy/db_zugang.py zugang "$zugang")
chmod 700 laufzeit

passwort=$(openssl rand -hex 24)
printf '%s\n' "$passwort" | python3 deploy/db_zugang.py sql "$zugang" \
  | docker exec -i "$container" psql -X -q -v ON_ERROR_STOP=1 -U supabase_admin -d "$datenbank" > /dev/null
printf '%s\n' "$passwort" | python3 deploy/db_zugang.py dsn-schreiben "$zugang"
unset passwort

echo "db_zugang: $angabe geschrieben"
