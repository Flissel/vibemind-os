"""Messschritt 0 (Terminkarten-Plan): kann `claude -p` auf dem Abo ein Foto lesen?

Erzeugt eine synthetische Karte mit bekannten Feldern (oder nimmt ein echtes
Foto per Argument), ruft die CLI mit Lesezugriff auf genau diese Datei und
vergleicht die erkannten Felder mit den tatsaechlichen.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageDraw

FELDER = ["Kunde", "Telefon", "Datum", "Uhrzeit", "Ort", "Thema", "Berater"]

PROMPT = (
    "Lies die Bilddatei karte.png in diesem Ordner mit dem Read-Werkzeug. "
    "Sie zeigt ein leeres Formular. Antworte NUR mit JSON: "
    '{"felder": [{"beschriftung": "..."}]} '
    "- eine Zeile je beschriftetem Eingabefeld, in Lesereihenfolge.")

# Messschritt 0 lief in einer verschachtelten Claude-Code-Session (CLAUDECODE=1
# im Elternprozess). `claude` liegt zwar auf dem PATH, aber C:\Users\User\bin\claude.exe
# ist dort ein kaputter Wrapper (versucht claude.cmd unter npm zu starten und
# schlaegt fehl); der echte Client liegt unter .local\bin. Deshalb hier der
# absolute Pfad statt des blossen Kommandonamens.
CLAUDE_EXE = os.path.join(os.environ.get("USERPROFILE", ""), ".local", "bin", "claude.exe")


def synthetische_karte(pfad: str) -> None:
    bild = Image.new("RGB", (1480, 1050), "white")
    zeichnen = ImageDraw.Draw(bild)
    zeichnen.text((60, 40), "TERMINKARTE", fill="black")
    for i, f in enumerate(FELDER):
        y = 140 + i * 120
        zeichnen.text((60, y), f + ":", fill="black")
        zeichnen.line((260, y + 30, 1400, y + 30), fill="black", width=3)
    bild.save(pfad)


def main() -> int:
    ordner = tempfile.mkdtemp(prefix="messschritt-")
    ziel = os.path.join(ordner, "karte.png")
    if len(sys.argv) > 1:
        Image.open(sys.argv[1]).convert("RGB").save(ziel)
        erwartet = None
    else:
        synthetische_karte(ziel)
        erwartet = FELDER
    argv = [CLAUDE_EXE, "-p", PROMPT, "--output-format", "json",
            "--allowedTools", "Read", "--model", "sonnet"]
    start = time.monotonic()
    fertig = subprocess.run(argv, cwd=ordner, capture_output=True, text=True,
                            encoding="utf-8", timeout=300)
    dauer = time.monotonic() - start
    print("Rueckgabewert:", fertig.returncode, " Dauer:", round(dauer, 1), "s")
    if fertig.returncode != 0:
        print("STDERR:", fertig.stderr[:800])
        return 1
    antwort = json.loads(fertig.stdout)["result"]
    antwort = antwort.strip().removeprefix("```json").removesuffix("```").strip()
    gefunden = [f["beschriftung"].rstrip(":").strip()
                for f in json.loads(antwort)["felder"]]
    print("Gefunden:", gefunden)
    if erwartet:
        treffer = [f for f in erwartet if f in gefunden]
        print(f"Treffer: {len(treffer)}/{len(erwartet)}")
        return 0 if len(treffer) == len(erwartet) else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
