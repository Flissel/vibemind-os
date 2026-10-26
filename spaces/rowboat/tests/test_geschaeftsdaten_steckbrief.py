"""Tests fuer den Steckbrief je Kontakt (Teil B, Task 5)."""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "geschaeftsdaten"))

from geschaeftsdaten_fake import ID, ereignis, kontakt  # noqa: E402
from steckbrief import ARCHIV, KATEGORIEN, OHNE, kategorie, people_index, stand, steckbrief  # noqa: E402

HEUTE = date(2026, 10, 26)


def _sb(k=None, ev=(), **kw) -> str:
    werte = dict(laden="sales", heute=HEUTE, people=None, empfohlen_von=None)
    werte.update(kw)
    return steckbrief(k or kontakt(), list(ev), **werte)


def _frontmatter(text: str) -> str:
    assert text.startswith("---\n")
    return text[4:text.index("\n---\n", 4)]


def test_kategorie_erster_bekannter_bereich():
    assert kategorie(kontakt(bereiche=("unbekannt", "it", "bav"))) == "bAV"
    assert kategorie(kontakt(bereiche=())) == OHNE == "_ohne"
    assert kategorie(kontakt(bereiche=("vorsorge",))) == "Private Vorsorge und Versicherung"


def test_kategorie_archiviert():
    assert kategorie(kontakt(archiviert=True, bereiche=("bav",))) == ARCHIV == "_archiv"


def test_kategorien_ordnernamen():
    assert list(KATEGORIEN.values()) == ["bAV", "Mitarbeiter", "Private Vorsorge und Versicherung", "Immobilien",
                                         "IT-Lösung", "Homelab"]


def test_frontmatter_und_kopf():
    text = _sb(kontakt(bereiche=("bav", "it"), art="person"))
    fm = _frontmatter(text)
    assert "erzeugt_von: rowboat-geschaeftsdaten" in fm and f"schluessel: {ID}" in fm
    assert "quelle: sales-claw" in fm and "laden: sales" in fm and "art: person" in fm
    assert "schlagwoerter: [person, it]" in fm and "stand: 2026-10-20" in fm
    assert "Automatisch erzeugt, Quelle sales-claw, Stand 20.10.2026" in text
    assert "Nicht hier bearbeiten" in text


def test_keine_verbotenen_schluessel():
    fm = _frontmatter(_sb())
    for schluessel in ("live:", "live_note:", "curated_at:"):
        assert schluessel not in fm
    assert "@rowboat" not in _sb()


def test_gesperrt_wirft():
    with pytest.raises(ValueError):
        _sb(kontakt(gesperrt=True))


def test_stand_aus_daten_nicht_heute():
    k = kontakt(updated_at=datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc))
    ev = [ereignis(zeit=datetime(2026, 10, 22, 23, 30, tzinfo=timezone.utc)),
          ereignis(lead_id="anderer", zeit=datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc))]
    assert stand(k, ev) == date(2026, 10, 23)
    assert "Stand 23.10.2026" in _sb(k, ev)


def test_stand_ohne_updated_at_und_ereignisse():
    k = kontakt(updated_at=None, created_at=datetime(2026, 3, 1, 23, 30, tzinfo=timezone.utc))
    assert stand(k, []) == date(2026, 3, 2)


def test_offene_termine_ohne_abgesagte_und_vergangene():
    ev = [ereignis(typ="termin", termin_uid="u1", termin_datum="2026-10-25", termin_uhrzeit="10:00"),
          ereignis(typ="termin", termin_uid="u2", termin_datum="2026-10-27", termin_uhrzeit="14:30"),
          ereignis(typ="termin", termin_uid="u3", termin_datum="2026-10-27", termin_uhrzeit="09:00"),
          ereignis(typ="termin_abgesagt", termin_uid="u3"),
          ereignis(typ="termin", termin_uid="u4", termin_datum="27.10.2026", termin_uhrzeit="09:00")]
    text = _sb(ev=ev)
    abschnitt = text.split("## Offene Termine", 1)[1]
    assert re.findall(r"^- .*$", abschnitt, re.M) == ["- 27.10.2026, 14:30"]


def test_hoechstens_fuenf_termine():
    ev = [ereignis(typ="termin", termin_uid=f"u{i}", termin_datum=f"2026-11-{i:02d}", termin_uhrzeit=None)
          for i in range(1, 9)]
    abschnitt = _sb(ev=ev).split("## Offene Termine", 1)[1]
    assert re.findall(r"^- .*$", abschnitt, re.M) == [f"- {i:02d}.11.2026" for i in range(1, 6)]


def test_letzter_kontakt_nur_datum_und_richtung():
    ein = [ereignis(typ="nachricht_ausgehend", zeit=datetime(2026, 10, 1, 9, tzinfo=timezone.utc)),
           ereignis(typ="kundenantwort", zeit=datetime(2026, 10, 2, 9, tzinfo=timezone.utc))]
    assert "Letzter Kontakt:** 02.10.2026 (eingehend)" in _sb(ev=ein)
    aus = [ereignis(typ="nachricht_ausgehend", zeit=datetime(2026, 10, 3, 9, tzinfo=timezone.utc))]
    assert "Letzter Kontakt:** 03.10.2026 (ausgehend)" in _sb(ev=aus)
    assert "Letzter Kontakt:** keiner" in _sb(ev=[ereignis(typ="stufenwechsel")])


def test_einwilligung():
    assert "Newsletter-Einwilligung:** ja" in _sb(kontakt(consent_status="opt_in"))
    for wert in ("unknown", "inbound", "existing_customer", None):
        assert "Newsletter-Einwilligung:** nein" in _sb(kontakt(consent_status=wert))


def test_stufe_und_bereiche_als_bezeichnung():
    text = _sb(kontakt(status="meeting", bereiche=("vorsorge", "bav", "quatsch")))
    assert "Pipeline-Stufe:** Termin" in text
    assert "Bereiche:** bAV, Private Vorsorge/Versicherung" in text
    assert "Pipeline-Stufe:** sonderstufe" in _sb(kontakt(status="sonderstufe"))


def test_archiv_hinweis_und_empfohlen_von():
    text = _sb(kontakt(archiviert=True), empfohlen_von="[[companys/VibeMind/Kontakte/bAV/Anna]]")
    assert "Archiviert" in text and "Empfohlen von:** [[companys/VibeMind/Kontakte/bAV/Anna]]" in text
    assert "Empfohlen von:** Bert B." in _sb(empfohlen_von="Bert\nB.")
    assert "Archiviert" not in _sb()


def test_siehe_auch_people():
    assert "Siehe auch: [[People/Max Muster]]" in _sb(people="People/Max Muster")
    assert "Siehe auch" not in _sb()


def test_keine_mail_im_text():
    text = _sb(kontakt(email_klein="a@b.de", name="Max (A@B.de)", company="a@b.de GmbH"))
    assert "a@b.de" not in text.casefold()


def test_nur_erlaubte_felder():
    text = _sb(kontakt(name="Max Muster", company="Muster AG", title="CEO", source="linkedin", art="firma"))
    assert "# Max Muster" in text and "Firma:** Muster AG" in text and "Rolle:** CEO" in text
    assert "Herkunft:** linkedin" in text and "Art:** Firma" in text


def _people(tmp_path: Path, name: str, frontmatter: str | None) -> None:
    p = tmp_path / "People" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text((f"---\n{frontmatter}\n---\n" if frontmatter is not None else "") + "# Notiz\n", encoding="utf-8")


def test_people_link_mail_vor_name_und_mehrdeutig_keiner(tmp_path: Path):
    _people(tmp_path, "Max Muster.md", "email: M@X.de\nrolle: x")
    _people(tmp_path, "Erika.md", "email: [e@x.de, e2@x.de]")
    _people(tmp_path, "Doppelt A.md", "email: d@x.de")
    _people(tmp_path, "Doppelt B.md", "email: \"d@x.de\"")
    _people(tmp_path, "Liste.md", "email:\n  - l@x.de\nanderes: 1")
    _people(tmp_path, "Müller.md", None)
    _people(tmp_path, "Veraltet/Anna.md", "email: anna@x.de")
    _people(tmp_path, "Unter.md", "meta:\n  email: unter@x.de")
    idx = people_index(tmp_path)
    assert idx.link("Erika", "m@x.de") == "People/Max Muster"        # Mail vor Name
    assert idx.link("Erika", None) == "People/Erika"
    assert idx.link("niemand", "e2@x.de") == "People/Erika"
    assert idx.link("niemand", "l@x.de") == "People/Liste"
    assert idx.link("Max Muster", "d@x.de") is None                   # Mail mehrdeutig -> kein Link
    assert idx.link("Müller", None) == "People/Müller"
    assert idx.link("Anna", "anna@x.de") is None                      # Veraltet zaehlt nicht
    assert idx.link("niemand", "unter@x.de") is None                  # nur oberste Ebene
    assert idx.link(None, None) is None


def test_people_name_mehrdeutig_keiner(tmp_path: Path):
    _people(tmp_path, "Müller.md", None)   # NFD und NFC: zwei Dateien, derselbe Name
    _people(tmp_path, "müller.md", None)
    idx = people_index(tmp_path)
    if len(list((tmp_path / "People").glob("*.md"))) < 2:
        pytest.skip("Dateisystem normalisiert Dateinamen")
    assert idx.link("Müller", None) is None


def test_people_index_ohne_ordner(tmp_path: Path):
    assert people_index(tmp_path).link("x", "y@z.de") is None


def test_keine_fremde_mail_in_freitexten():
    k = kontakt(name="Max info@max.de", company="Firma (kontakt@firma.de)", title="CEO ceo@x.io",
                email_klein=None)
    text = _sb(k, empfohlen_von="Bert bert@b.de")
    assert not re.search(r"[^\s@]+@[^\s@]+\.[a-z]{2,}", text, re.IGNORECASE)
    assert "Max" in text and "Bert" in text
