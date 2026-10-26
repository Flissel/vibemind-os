"""Traegt den MCP-Server ``wissen`` (Bedeutungssuche, 127.0.0.1:3240) in Rowboats ``config/mcp.json`` ein.

Laeuft auf der VM ohne Abhaengigkeiten (nur stdlib):

    ssh offload-vm 'python3 - ~/rowboat-data/config/mcp.json' < spaces/rowboat/scripts/wissen_mcp_eintragen.py

Legt vor der ersten Aenderung ``mcp.json.bak-vor-wissen`` an (nie ueberschrieben), schreibt atomar
(``os.replace``) und laesst alle anderen Server unveraendert. Mehrfaches Ausfuehren aendert nichts mehr.
Rueckweg: Sicherung zurueckkopieren oder den Eintrag ``wissen`` entfernen.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path

# Mit Schraegstrich: mcp 1.9.4 leitet POST /mcp per 307 auf /mcp/ um.
EINTRAG = {"type": "http", "url": "http://127.0.0.1:3240/mcp/"}
SICHERUNG = ".bak-vor-wissen"


def eintragen(config: dict) -> dict:
    neu = copy.deepcopy(config)
    server = neu.setdefault("mcpServers", {})
    if not isinstance(server, dict):
        raise ValueError("mcpServers ist kein Objekt")
    server["wissen"] = dict(EINTRAG)
    return neu


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Aufruf: python3 wissen_mcp_eintragen.py <pfad zu mcp.json>", file=sys.stderr)
        return 2
    pfad = Path(argv[1]).expanduser()
    if not pfad.is_file():
        print(f"{pfad} fehlt - nichts geschrieben", file=sys.stderr)
        return 1
    try:
        config = json.loads(pfad.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("kein JSON-Objekt")
        neu = eintragen(config)
    except ValueError as exc:
        print(f"{pfad} nicht lesbar ({exc}) - nichts geschrieben", file=sys.stderr)
        return 1

    sicherung = pfad.with_name(pfad.name + SICHERUNG)
    if not sicherung.exists():
        shutil.copy2(pfad, sicherung)
    if neu != config:
        tmp = pfad.with_name(pfad.name + ".tmp-wissen")
        tmp.write_text(json.dumps(neu, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        shutil.copymode(pfad, tmp)
        os.replace(tmp, pfad)
    print(" ".join(sorted(neu["mcpServers"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
