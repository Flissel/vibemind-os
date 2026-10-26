"""Tests fuer die Newsletter-Notiz (Teil B, Task 6)."""
from __future__ import annotations

import logging
import subprocess
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


def test_bloecke_laedt_kein_pil_und_kein_seite_mail():
    """Der VM-Python hat kein Pillow: der bloecke-Pfad darf seite_mail (und damit PIL) nicht laden."""
    hier = Path(__file__).resolve()
    code = (
        "import sys\n"
        f"sys.path[:0] = [{str(hier.parents[1] / 'geschaeftsdaten')!r}, {str(hier.parents[1] / 'scripts')!r},"
        f" {str(hier.parent)!r}, {str(hier.parents[3])!r}]\n"
        "from geschaeftsdaten_fake import inhalt\n"
        "from newsletter import klartext\n"
        "doc = {'root': {'data': {'childrenIds': ['t']}}, 't': {'type': 'Text', 'data': {'props': {'text': 'Hi'}}}}\n"
        "assert klartext(inhalt(format='bloecke', bloecke=doc)).strip() == 'Hi'\n"
        "print('PIL' in sys.modules, 'spaces.marketing.claw.seite_mail' in sys.modules)\n")
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["False", "False"]


def test_felder_laedt_keinen_renderer():
    hier = Path(__file__).resolve()
    code = (
        "import sys\n"
        f"sys.path[:0] = [{str(hier.parents[1] / 'geschaeftsdaten')!r}, {str(hier.parents[1] / 'scripts')!r},"
        f" {str(hier.parent)!r}, {str(hier.parents[3])!r}]\n"
        "from geschaeftsdaten_fake import inhalt\n"
        "from newsletter import klartext\n"
        "klartext(inhalt())\n"
        "print(any(m.startswith('spaces.marketing') for m in sys.modules))\n")
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "False"


def test_renderfehler_wird_mit_typ_protokolliert(caplog):
    caplog.set_level(logging.DEBUG)
    text = klartext(inhalt(format="seite", seite={"geheimer": "Inhalt XYZ"}, felder={"betreff": "B"}))
    assert text.strip() == "Text nicht darstellbar"
    meldungen = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(meldungen) == 1
    m = meldungen[0].getMessage()
    assert "bbbbbbbb" in m and "Inhalt XYZ" not in m and "geheimer" not in m
    assert m.rstrip(")").split("(")[-1].isidentifier()  # nur der Fehlertyp
