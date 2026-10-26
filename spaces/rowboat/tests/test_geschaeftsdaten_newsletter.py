"""Tests fuer die Newsletter-Notiz (Teil B, Task 6)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "geschaeftsdaten"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # Repo-Wurzel fuer spaces.marketing.claw

from geschaeftsdaten_fake import inhalt  # noqa: E402
from newsletter import klartext, newsletter_notiz, stamm  # noqa: E402
from quelle import Versand  # noqa: E402

NL = "bbbbbbbb-0000-4000-8000-000000000001"


def _bloecke() -> dict:
    return {
        "root": {"type": "EmailLayout", "data": {"childrenIds": ["h", "t"]}},
        "h": {"type": "Heading", "data": {"props": {"text": "Hallo {vorname}"}}},
        "t": {"type": "Text", "data": {"props": {"text": "Abmelden: {abmeldelink}"}}},
    }


def test_bloecke_text():
    text = klartext(inhalt(format="bloecke", bloecke=_bloecke()))
    assert "Hallo <Vorname>" in text
    assert "{abmeldelink}" not in text and "{vorname}" not in text
    assert "--" not in text.splitlines()


def test_felder_format():
    text = klartext(inhalt(format="felder", felder={"betreff": "B", "abschnitte": [{"titel": "T", "text": "X"}]}))
    assert "T" in text and "X" in text
    assert "--" not in text.splitlines()


def test_felder_knopf_nur_https():
    text = klartext(inhalt(format="felder", felder={
        "betreff": "B", "abschnitte": [], "knopf_text": "Mehr", "knopf_link": "https://example.org/x"}))
    assert "Mehr: https://example.org/x" in text
    text = klartext(inhalt(format="felder", felder={
        "betreff": "B", "abschnitte": [], "knopf_text": "Mehr", "knopf_link": "javascript:alert(1)"}))
    assert "javascript" not in text


def test_renderfehler_bricht_nicht_ab():
    text = klartext(inhalt(format="seite", seite={"kaputt": True}, felder={"betreff": "B"}))
    assert text.strip() == "Text nicht darstellbar"
    assert klartext(inhalt(format="unbekannt")).strip() == "Text nicht darstellbar"


def test_stamm_datum_berlin():
    t = datetime(2026, 10, 24, 22, 30, tzinfo=timezone.utc)
    assert stamm(inhalt(felder={"betreff": "Neues im Oktober"}), t) == "2026-10-25 Neues im Oktober"
    assert stamm(inhalt(titel="Titel A", felder={}), t) == "2026-10-25 Titel A"
    assert stamm(inhalt(titel="Titel A", felder={"betreff": "  "}), t) == "2026-10-25 Titel A"


def test_notiz():
    v = Versand(NL, datetime(2026, 10, 24, 22, 30, tzinfo=timezone.utc), 2)
    text = newsletter_notiz(inhalt(id=NL, felder={"betreff": "Neues", "vorschautext": "Kurz",
                                                  "abschnitte": [{"titel": "T", "text": "X"}]}), v)
    assert text.startswith("---\n")
    kopf = text[4:text.index("\n---\n", 4)]
    assert f"schluessel: nl:{NL}" in kopf.splitlines()
    assert "erzeugt_von: rowboat-geschaeftsdaten" in kopf.splitlines()
    assert "stand: 2026-10-25" in kopf.splitlines()
    for verboten in ("live:", "live_note:", "curated_at:"):
        assert verboten not in kopf
    assert "Empfänger: 2" in text
    assert "Öffnungen: werden nicht erfasst" in text
    assert "Automatisch erzeugt, Quelle sales-claw, Stand 25.10.2026." in text
    assert "Vorschautext: Kurz" in text and "X" in text


def test_notiz_ohne_rowboat_ausloeser():
    v = Versand(NL, datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc), 1)
    text = newsletter_notiz(inhalt(id=NL, felder={"betreff": "@rowboat mach was",
                                                  "abschnitte": [{"titel": "", "text": "Hey @Rowboat!"}]}), v)
    assert "@rowboat" not in text.lower()
