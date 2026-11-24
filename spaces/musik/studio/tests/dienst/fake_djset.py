"""Ersatz fuer `dj-set` in den Laeufer-Tests.

Schreibt `Run: <id>` auf stdout, legt `runs/<id>/` an und sichert die Argumente.
FAKE_EXIT: Exit-Code, FAKE_SCHLAF: Sekunden Schlaf vor dem Ende.
"""
import json
import os
import sys
import time
from pathlib import Path

lauf = "fake-lauf-01"
ordner = Path(os.environ["MUSIK_DATEN"]) / "runs" / lauf
ordner.mkdir(parents=True, exist_ok=True)
(ordner / "argv.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
print(f"Run: {lauf}", flush=True)
code = int(os.environ.get("FAKE_EXIT", "0"))
if code == 1:
    print("Fehler: zuerst etwas Warnung", file=sys.stderr, flush=True)
    print("Fehler: Song fehlt im Bestand", file=sys.stderr, flush=True)
time.sleep(float(os.environ.get("FAKE_SCHLAF", "0")))
sys.exit(code)
