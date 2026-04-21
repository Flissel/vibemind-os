#!/bin/bash
# Supabase Self-Healing Script
# Restart crashed containers, flush Kong DNS cache.
# Run when `curl http://localhost:54321/rest/v1/...` returns 503.
#
# ============================================================
# ANEKDOTE: Der Key, der nicht im Repo sein wollte
# ------------------------------------------------------------
# Unten in der Health-Check-Schleife steht ein `sb_publishable_...`
# Key direkt im Script. Jeder andere Teil vom System liest diesen
# Key aus `.env` als `$SUPABASE_ANON_KEY` — nur dieses Script
# tanzt aus der Reihe. Warum? Weil das Script als Quick-Hack
# entstanden ist, als Kong auf Windows wieder mal seinen DNS-Cache
# verloren hatte. "Ich fix das jetzt schnell" — und dann blieb der
# Key da. Schlimm ist es nicht (der Anon-Key ist oeffentlich
# dokumentiert), aber es ist ein schlechtes Signal:
#   1. der Key rotiert nicht mit dem Rest des Setups
#   2. beim Secrets-Scan loest er Alarm aus
#   3. wenn Auth/RLS spaeter restriktiv wird, muss hier geaendert
#      werden, was sonst in einer `.env` steht
# Fix: `source "$(dirname "$0")/../.env"` am Anfang, dann
# `$SUPABASE_ANON_KEY` im curl verwenden.
#
# TODO(supabase-anon-key-envvar): heal.sh auf env-Variable
# umstellen. Siehe docs/portion-03-database.md Abschnitt 14.2
# ============================================================

set -e

echo "=== Supabase Heal ==="

# 1. Find exited containers and restart them
EXITED=$(docker ps -a --filter "name=supabase" --filter "status=exited" --format "{{.Names}}")
if [ -n "$EXITED" ]; then
  echo "Restarting exited containers:"
  for c in $EXITED; do
    # Skip vector container (known Windows issue)
    if [[ "$c" == *"vector"* ]]; then
      echo "  SKIP $c (vector has Windows Docker socket issue)"
      continue
    fi
    echo "  $c"
    docker start "$c" > /dev/null
  done
fi

# 2. Stop vector container if running (causes DNS issues)
if docker ps --filter "name=supabase_vector" --format "{{.Names}}" | grep -q vector; then
  echo "Stopping supabase_vector (crashes on Windows)..."
  docker stop supabase_vector_supabase > /dev/null 2>&1 || true
fi

# 3. Restart Kong to flush DNS cache
echo "Restarting Kong (flush DNS)..."
docker restart supabase_kong_supabase > /dev/null

# 4. Wait for Kong to come back
for i in 1 2 3 4 5; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 \
    "http://localhost:54321/rest/v1/ideas?limit=1" \
    -H "apikey: sb_publishable_ACJWlzQHlZjBrEguHvfOxg_3BJgxAaH" 2>/dev/null || echo "000")
  if [ "$STATUS" = "200" ]; then
    echo "Supabase healthy: HTTP $STATUS"
    exit 0
  fi
  sleep 1
done

echo "Supabase still unhealthy after restart. Check logs:"
echo "  docker logs supabase_kong_supabase --tail 20"
echo "  docker logs supabase_rest_supabase --tail 20"
exit 1
