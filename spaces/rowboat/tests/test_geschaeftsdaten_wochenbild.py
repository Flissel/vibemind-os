"""Tests fuer das Wochenbild je Firma (Teil B, Task 7)."""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "geschaeftsdaten"))

from geschaeftsdaten_fake import ereignis, kontakt  # noqa: E402
from quelle import Medium  # noqa: E402
from wochenbild import letzte_kw, stamm, wochenbild, zeitraum  # noqa: E402

UTC = timezone.utc
IN_DER_WOCHE = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)


def _id(n: int) -> str:
    return f"aaaaaaaa-0000-4000-8000-{n:012d}"


def _wb(kontakte=(), ereignisse=(), newsletter=(), medien=()) -> str:
    return wochenbild("VibeMind", 2026, 43, list(kontakte), list(ereignisse), list(newsletter), list(medien))


def _zeile(text: str, anfang: str) -> str:
    treffer = [z for z in text.splitlines() if z.startswith(anfang)]
    assert len(treffer) == 1, (anfang, text)
    return treffer[0]


def test_letzte_kw_montag():
    assert letzte_kw(date(2026, 10, 26)) == (2026, 43)
    assert letzte_kw(date(2027, 1, 4)) == (2026, 53)
    assert letzte_kw(date(2026, 10, 25)) == (2026, 42)  # Sonntag: Woche 43 laeuft noch


def test_zeitraum_sommerzeitwechsel():
    von, bis = zeitraum(2026, 43)
    assert von.astimezone(UTC) == datetime(2026, 10, 18, 22, 0, tzinfo=UTC)  # 19.10. 00:00 MESZ
    assert bis.astimezone(UTC) == datetime(2026, 10, 25, 23, 0, tzinfo=UTC)  # 26.10. 00:00 MEZ
    assert von.tzinfo is not None and bis.tzinfo is not None


def test_stamm():
    assert stamm(2026, 43) == "2026-KW43"
    assert stamm(2027, 1) == "2027-KW01"


def test_zahlen_ohne_gesperrte_und_archivierte():
    kontakte = [
        kontakt(id=_id(1), status="new", created_at=IN_DER_WOCHE),
        kontakt(id=_id(2), status="new"),
        kontakt(id=_id(3), status="won", archiviert=True, created_at=IN_DER_WOCHE),
        kontakt(id=_id(4), gesperrt=True, status=None, name=None, created_at=IN_DER_WOCHE),
    ]
    ereignisse = [
        ereignis(lead_id=_id(1), typ="stufenwechsel", zeit=IN_DER_WOCHE),
        ereignis(lead_id=_id(4), typ="stufenwechsel", zeit=IN_DER_WOCHE),
        ereignis(lead_id=_id(1), typ="termin", zeit=IN_DER_WOCHE, termin_uid="u1", termin_datum="2026-10-22"),
        ereignis(lead_id=_id(4), typ="termin", zeit=IN_DER_WOCHE, termin_uid="u2", termin_datum="2026-10-22"),
        ereignis(lead_id=_id(2), typ="termin", zeit=IN_DER_WOCHE, termin_uid="u3", termin_datum="2026-10-23"),
        ereignis(lead_id=_id(2), typ="termin_abgesagt", zeit=IN_DER_WOCHE, termin_uid="u3"),
        ereignis(lead_id=_id(2), typ="termin", zeit=IN_DER_WOCHE, termin_uid="u5", termin_datum="2026-11-02"),
    ]
    text = _wb(kontakte, ereignisse)
    assert _zeile(text, "- Neu:") == "- Neu: 2"
    assert "Gewonnen" not in text
    assert _zeile(text, "- Kontakte in der Pipeline:") == "- Kontakte in der Pipeline: 2"
    assert _zeile(text, "- Neue Kontakte:") == "- Neue Kontakte: 2"
    assert _zeile(text, "- Stufenwechsel:") == "- Stufenwechsel: 1"
    assert _zeile(text, "- Termine:") == "- Termine: 1"


def test_keine_kontaktnamen():
    kontakte = [kontakt(id=_id(i), name=f"Geheimname{i}", company=f"Firma{i}", created_at=IN_DER_WOCHE)
                for i in range(1, 4)]
    text = _wb(kontakte, [ereignis(lead_id=_id(1), typ="stufenwechsel", zeit=IN_DER_WOCHE)])
    for k in kontakte:
        assert k.name not in text and k.company not in text and k.id not in text


def test_medien_der_woche():
    medien = [
        Medium("reel", "vibemind", "Reel drin", 65.4, datetime(2026, 10, 25, 22, 59, tzinfo=UTC)),
        Medium("reel", "vibemind", "Reel danach", 30.0, datetime(2026, 10, 25, 23, 0, tzinfo=UTC)),
        Medium("podcast", "vibemind", "Folge 1", None, datetime(2026, 10, 20, 8, 0, tzinfo=UTC)),
    ]
    text = _wb(medien=medien)
    assert "Reel drin (1:05)" in text
    assert "Reel danach" not in text
    assert "Podcast: Folge 1" in text


def test_newsletter_links():
    text = _wb(newsletter=[("companys/VibeMind/Newsletter/2026-10-25 Neues", 2)])
    assert "[[companys/VibeMind/Newsletter/2026-10-25 Neues]]" in text and "2 Empfänger" in text
    assert "Keine Newsletter" in _wb()


def test_frontmatter():
    text = _wb()
    assert text.startswith("---\n")
    kopf = text[4:text.index("\n---\n", 4)].splitlines()
    assert "schluessel: kw:2026-W43" in kopf
    assert "stand: 2026-10-25" in kopf
    assert "erzeugt_von: rowboat-geschaeftsdaten" in kopf
    assert not any(z.startswith(("live:", "live_note:", "curated_at:")) for z in kopf)
    assert "Automatisch erzeugt, Quelle sales-claw, Stand 25.10.2026." in text


def test_ohne_rowboat_ausloeser():
    text = _wb(medien=[Medium("reel", "vibemind", "@rowboat los", 5.0, IN_DER_WOCHE)])
    assert "@rowboat" not in text.lower()
