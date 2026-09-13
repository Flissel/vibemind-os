"""Abnahme des Laura-MCP: fuehrt der Server genau das, was freigegeben ist?

WARUM DIESE PRUEFUNG EIGENSTAENDIG IST. Der marketing-Server laeuft ueber
openclaws MCP-Schicht und laesst sich mit `openclaw mcp probe marketing`
befragen. Laura kommt einen anderen Weg: ueber `SHIM_EXTRA_MCP_CONFIG`
direkt in die Claude-CLI im Shim. In der openclaw-Probe taucht er deshalb
nie auf — wer dort nach ihm sucht, prueft nichts.

Der Server wird per stdio gestartet und nach `tools/list` gefragt. Das geht
OHNE laufende Laura-API: FastMCP registriert die Werkzeuge beim Start, der
HTTP-Klient verbindet sich erst beim ersten echten Aufruf.

Zwei Richtungen werden geprueft, und die zweite ist die wichtigere:

  * Jeder freigegebene Name MUSS existieren — sonst steht Totes in der Liste.
  * Vier Werkzeuge duerfen NICHT freigegeben sein. `laura_api` reicht jede
    API-Route durch und hebt die Freigabeliste damit auf. `auto_produce` und
    `start_production` fahren eine unbeaufsichtigte Produktion, waehrend
    dieser Space auf "Agent entwirft, Mensch gibt frei" gebaut ist. Und
    `approve_script` genehmigt — das ist nie die Handlung des Agenten.

Rueckgabe 0, wenn alles stimmt; sonst 1 mit sprechender Ausgabe.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

VERBOTEN = {"laura_api", "auto_produce", "start_production", "approve_script"}
KONF = Path(__file__).with_name("marketing-mcp.json")


def main() -> int:
    d = json.loads(KONF.read_text(encoding="utf-8"))
    server = (d.get("mcpServers") or {}).get("laura")
    if server is None:
        print("   FEHL laura ist in marketing-mcp.json nicht eingetragen")
        return 1
    erlaubt = {x.split("__")[-1] for x in d.get("allowedTools", [])
               if "__laura__" in x}

    umgebung = {**os.environ, **(server.get("env") or {})}
    # uv nimmt sonst die Umgebung des aufrufenden Prozesses und warnt.
    umgebung.pop("VIRTUAL_ENV", None)
    nachrichten = "\n".join(json.dumps(m) for m in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "abnahme", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]) + "\n"

    try:
        lauf = subprocess.run(
            [server["command"], *server["args"]], input=nachrichten,
            capture_output=True, text=True, env=umgebung, timeout=300)
    except FileNotFoundError:
        print(f"   FEHL '{server['command']}' nicht gefunden")
        return 1
    except subprocess.TimeoutExpired:
        print("   FEHL Laura-MCP antwortete nicht innerhalb von 300 s")
        return 1

    gefuehrt: set[str] = set()
    for zeile in lauf.stdout.splitlines():
        zeile = zeile.strip()
        if not zeile.startswith("{"):
            continue
        n = json.loads(zeile)
        if n.get("id") == 2:
            gefuehrt = {w["name"] for w in n.get("result", {}).get("tools", [])}
    if not gefuehrt:
        letzte = [z.strip() for z in lauf.stderr.splitlines() if z.strip()][-2:]
        print("   FEHL Laura-MCP lieferte keine Werkzeugliste. "
              + " | ".join(z[:120] for z in letzte))
        return 1

    fehlt = sorted(erlaubt - gefuehrt)
    durchgerutscht = sorted(erlaubt & VERBOTEN)
    print(f"   {len(gefuehrt)} Werkzeuge gefuehrt, {len(erlaubt)} freigegeben")
    if fehlt:
        print(f"   FEHL freigegeben, aber nicht vorhanden: {fehlt}")
    if durchgerutscht:
        print(f"   FEHL verboten, aber freigegeben: {durchgerutscht}")
    return 1 if (fehlt or durchgerutscht) else 0


if __name__ == "__main__":
    sys.exit(main())
