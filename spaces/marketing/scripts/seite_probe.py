"""Echter Durchlauf einer Collage-Seite (Freie Flaeche, Stufe 1): anlegen, zweite Fassung mit Bild, gedrehter
Anzeige-Schrift, Handschrift, Form und Knopf speichern, PDF holen, Inhalt wieder loeschen.

    python seite_probe.py --api http://127.0.0.1:5510 --bild-basis <https://…/marketing/bild/<token>/> \
        --bild medien:VibeMind_overview.png --pdf /tmp/seite_probe.pdf

Der Pult-Schluessel kommt aus MARKETING_PULT_KEY (Umgebung), nie von der Kommandozeile.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.error
import urllib.request


def _anfrage(api: str, methode: str, pfad: str, koerper: dict | None = None) -> bytes:
    daten = json.dumps(koerper).encode() if koerper is not None else None
    # strip(): Env-Dateien mit CRLF haengen sonst ein \r an - und Fehlermeldungen von http.client zeigen den Wert
    schluessel = os.environ.get("MARKETING_PULT_KEY", "").strip()
    try:
        req = urllib.request.Request(api.rstrip("/") + pfad, data=daten, method=methode,
                                     headers={"X-Pult-Key": schluessel, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as antwort:
            return antwort.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{methode} {pfad}: HTTP {e.code} {e.read()[:300]!r}") from None
    except (ValueError, OSError) as e:
        raise SystemExit(f"{methode} {pfad}: {type(e).__name__}") from None   # nie die Meldung: sie kann Kopfzeilen enthalten


def seite(bild: str) -> dict:
    return {
        "version": 1, "breite": 600, "hoehe": 900,
        "hintergrund": {"farbe": "#fffaf3", "bild": None},
        "schriften": {"anzeige": "montserrat", "text": "dm-sans"},
        "ebenen": [
            {"id": "bild1", "art": "bild", "x": 200, "y": 190, "breite": 360, "hoehe": 300, "drehung": -4,
             "quelle": bild, "rahmen_breite": 8, "schatten": True},
            {"id": "form1", "art": "form", "x": 470, "y": 120, "breite": 150, "hoehe": 150,
             "form": "kreis", "farbe": "#f3d34a"},
            {"id": "titel", "art": "text", "x": 420, "y": 330, "breite": 320, "hoehe": 120, "drehung": 6,
             "text": "Neue Kurse.", "rolle": "ANZEIGE", "groesse": 56, "gewicht": 600, "farbe": "#2b2522"},
            {"id": "gruss", "art": "text", "x": 470, "y": 120, "breite": 140, "hoehe": 60, "drehung": 12,
             "text": "ab 4. Nov", "rolle": "HAND", "groesse": 30, "farbe": "#2b2522", "ausrichtung": "mitte"},
            {"id": "text1", "art": "text", "x": 300, "y": 560, "breite": 520, "hoehe": 120,
             "text": "Hallo {vorname},\n**Drei neue Kurse** starten im Herbst. [Mehr erfahren](https://vibemind.de)",
             "rolle": "TEXT", "groesse": 17, "farbe": "#2b2522"},
            {"id": "knopf1", "art": "knopf", "x": 300, "y": 700, "breite": 240, "hoehe": 52,
             "text": "Jetzt anmelden", "link": "https://vibemind.de", "farbe": "#c4573a", "textfarbe": "#ffffff"},
        ],
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", required=True)
    p.add_argument("--bild-basis", default="")
    p.add_argument("--bild", required=True)
    p.add_argument("--pdf", required=True)
    a = p.parse_args(argv)
    neu = json.loads(_anfrage(a.api, "POST", "/api/pult/inhalte/collage",
                              {"mandant": "vibemind", "titel": "Probe Collage Stufe 1"}))
    iid = neu["inhalt"]
    try:
        f2 = json.loads(_anfrage(a.api, "POST", f"/api/pult/inhalte/{iid}/seite",
                                 {"basis_fassung": neu["fassung"], "seite": seite(a.bild),
                                  "betreff": "Probe Collage", "vorschautext": ""}))["fassung"]
        q = urllib.parse.urlencode({"fassung": f2, "format": "pdf", "bild_basis": a.bild_basis})
        roh = _anfrage(a.api, "GET", f"/api/pult/inhalte/{iid}/vorschau?{q}")
        with open(a.pdf, "wb") as datei:
            datei.write(roh)
        print(f"ok: Inhalt {iid}, Fassung {f2}, PDF {len(roh)} Bytes -> {a.pdf}")
    finally:
        _anfrage(a.api, "POST", f"/api/pult/inhalte/{iid}/loeschen", {"mandant": "vibemind", "von": "probe"})
        print(f"geloescht: {iid}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
