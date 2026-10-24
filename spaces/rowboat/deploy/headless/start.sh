#!/bin/sh
# Legt beim allerersten Start config/server.json mit lanEnabled an. Ohne lanEnabled bindet der
# Server nur 127.0.0.1 und ist ueber Tailscale nicht erreichbar. Eine vorhandene Datei bleibt
# unangetastet (z. B. nach dem Umzug vom PC).
set -eu
WORKDIR="${ROWBOAT_WORKDIR:-/data}"
CONFIG="$WORKDIR/config/server.json"
if [ ! -e "$CONFIG" ]; then
  mkdir -p "$WORKDIR/config"
  printf '{\n  "lanEnabled": true,\n  "port": %s\n}\n' "${ROWBOAT_SERVER_PORT:-3220}" > "$CONFIG"
fi
exec "$@"
