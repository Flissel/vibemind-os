#!/bin/sh
# Bricht frueh und lesbar ab, wenn Token oder Budget-Ordner fehlen. Der Token wird nie ausgegeben.
set -eu
[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] || {
  echo "rowboat-shim: CLAUDE_CODE_OAUTH_TOKEN fehlt (Datei ~/rowboat-headless/claude-token)" >&2
  exit 78
}
if [ ! -w "${VIBEMIND_BUDGET_DIR:-/budget}" ]; then
  echo "rowboat-shim: Budget-Ordner ${VIBEMIND_BUDGET_DIR:-/budget} fehlt oder ist nicht beschreibbar" >&2
  exit 78
fi
exec "$@"
