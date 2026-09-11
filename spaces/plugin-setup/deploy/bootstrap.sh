#!/usr/bin/env bash
# bootstrap.sh -- legt den plugin-setup-Space an. Wiederholbar (idempotent).
#
# Tut GENAU drei Dinge, in dieser Reihenfolge:
#   1. Wendet die drei Supabase-Migrationen an (0001/0002 aus Aufgabe 4,
#      0003 -- die geringstberechtigte Rolle -- aus Aufgabe 6).
#   2. Faehrt das eigenstaendige Gateway hoch (docker compose up -d in
#      diesem Verzeichnis -- NICHT der vibemind-Swarm-Stack, s.
#      docker-compose.yml-Kommentar).
#   3. Verweist auf anbinden.sh fuer die Saat + Werkzeug-Abnahme.
#
# Braucht: einen laufenden supabase-db-Container (Namenssubstring
# "supabase-db"), docker, docker compose.
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HIER"

echo "1) Supabase-Migrationen (idempotent)"
CONTAINER="$(docker ps --format '{{.Names}}' | grep -m1 supabase-db || true)"
if [ -z "$CONTAINER" ]; then
  echo "ABBRUCH: kein laufender supabase-db-Container gefunden (docker ps)." >&2
  exit 1
fi
for migration in db/0001_plugin_setup.sql db/0002_state_machine.sql db/0003_least_privilege_role.sql; do
  echo "   -> $migration"
  docker exec -i "$CONTAINER" psql -U postgres -d postgres -v ON_ERROR_STOP=1 -f - < "$migration"
done

echo "2) Gateway hochfahren (eigenstaendiges Compose-Projekt, NICHT der Swarm-Stack)"
docker compose up -d

echo "Fertig. Naechster Schritt: deploy/anbinden.sh (Saat + Werkzeug-Abnahme),"
echo "danach deploy/smoke.sh (Werkzeug-Rauchtest + Verbotsliste)."
