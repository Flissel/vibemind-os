"""Formular-Entwurf: aus einem Foto (oder einer Beschreibung) eine Gestalt.

Der Agent sieht keine Bilder — der Shim verwirft Bildteile und gibt der CLI
keine Werkzeuge. Deshalb ruft dieses Modul `claude -p` SELBST auf, mit
Lesezugriff auf genau eine Bilddatei in einem frischen Ordner (gemessen in
docs/2026-09-24-messschritt-foto.md). Das Foto reist nie als Text.
Die Pruefung der Gestalt macht die Datenbank (_formular_gestalt_fehler);
hier wird nur sichergestellt, dass ueberhaupt JSON zurueckkommt.

`claude` auf dem PATH dieses Hosts ist ein kaputter Wrapper
(C:\\Users\\User\\bin\\claude.exe, versucht intern npm\\claude.cmd zu starten
und scheitert). Der echte Client liegt unter ~/.local/bin/claude.exe -
_cli_pfad() loest das auf, ueberschreibbar per CLAUDE_CLI-Umgebungsvariable.
"""
import base64
import json
import os
import shutil
import subprocess
import tempfile

QUELLEN = ("kunde.name", "kunde.telefon", "kunde.email", "kunde.firma", "termin.datum",
           "termin.uhrzeit", "termin.dauer", "termin.thema", "termin.ort",
           "mitglied.name", "frei")
ENDUNG = {"image/jpeg": "karte.jpg", "image/png": "karte.png"}

ANLEITUNG = """Du baust eine druckbare Formular-Vorlage (eine Terminkarte fuer einen Teamleiter).
{quelle_satz}
Antworte NUR mit einem JSON-Objekt dieser Form, ohne Erklaerung:
{{"seite": {{"breite_mm": <50-300>, "hoehe_mm": <50-300>}},
 "texte": [{{"text": "...", "platz": {{"x": 0, "y": 0, "breite": 0, "hoehe": 0}}, "groesse": 12}}],
 "felder": [{{"name": "<a-z0-9_>", "beschriftung": "...", "art": "text|datum|uhrzeit|telefon|mehrzeilig",
             "quelle": "<eine aus der Liste>", "platz": {{"x": 0, "y": 0, "breite": 0, "hoehe": 0}}}}]}}
Masse in Millimetern, Ursprung links oben. Jedes Feld liegt vollstaendig auf der Seite.
Erlaubte Quellen: {quellen}. Was keiner Quelle entspricht, bekommt "frei".
Die Datenbank lehnt jede Gestalt ab, die eine Quelle ausserhalb dieser Liste
oder einen unvollstaendigen platz (x, y, breite, hoehe muessen alle gesetzt
sein) benutzt - halte dich also genau an diese Vorgaben.
{rueckmeldungen}"""


def _cli_pfad() -> str:
    return os.environ.get("CLAUDE_CLI") or os.path.expanduser("~/.local/bin/claude.exe")


def _prompt(auftrag: dict, mit_bild: bool) -> str:
    if mit_bild:
        quelle_satz = ("Lies die Bilddatei in diesem Ordner mit dem Read-Werkzeug. Sie zeigt "
                       "die leere Karte; baue sie in Aufbau und Feldern nach.")
    else:
        quelle_satz = "Die Karte ist so beschrieben: " + auftrag["beschreibung"]
    if auftrag.get("anmerkung"):
        quelle_satz += " Hinweis beim Bestellen: " + auftrag["anmerkung"]
    rueck = auftrag.get("rueckmeldungen") or []
    rueck_satz = ""
    if rueck:
        rueck_satz = ("Fruehere Runden wurden abgelehnt. Beruecksichtige ALLE Anmerkungen: "
                      + "; ".join(f"Runde {r['runde']}: {r['anmerkung']}" for r in rueck))
    return ANLEITUNG.format(quelle_satz=quelle_satz, quellen=", ".join(QUELLEN),
                            rueckmeldungen=rueck_satz)


def entwerfen(auftrag: dict, lauf=subprocess.run) -> tuple:
    ordner = tempfile.mkdtemp(prefix="formular-")
    try:
        mit_bild = bool(auftrag.get("bild_b64"))
        if mit_bild:
            with open(os.path.join(ordner, ENDUNG[auftrag["bild_typ"]]), "wb") as f:
                f.write(base64.b64decode(auftrag["bild_b64"]))
        argv = [_cli_pfad(), "-p", _prompt(auftrag, mit_bild), "--output-format", "json",
                "--model", "sonnet"]
        if mit_bild:
            argv += ["--allowedTools", "Read"]
        fertig = lauf(argv, cwd=ordner, capture_output=True, text=True,
                      encoding="utf-8", timeout=300)
        if fertig.returncode != 0:
            return None, f"claude -p scheiterte: {(fertig.stderr or '')[:300]}"
        try:
            text = json.loads(fertig.stdout)["result"].strip()
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            gestalt = json.loads(text)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None, "Das Modell lieferte kein gueltiges JSON."
        if not isinstance(gestalt, dict) or not isinstance(gestalt.get("felder"), list):
            return None, "Das JSON hat keine Feldliste."
        return gestalt, ""
    finally:
        shutil.rmtree(ordner, ignore_errors=True)
