"""Termin-Notizen und Termin-Uebersicht je Firma (rein, ohne DB)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_ROWBOAT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(_ROWBOAT / "geschaeftsdaten"))
sys.path.insert(0, str(_ROWBOAT.parents[1]))

from geschaeftsdaten_fake import ereignis  # noqa: E402
from termine import Termin, gesperrte_termin_schluessel, termine_aus, termine_soll  # noqa: E402

HEUTE = date(2026, 11, 8)
PFADE = {"l1": "companys/Vibemind/Kontakte/Kunden/Anna Muster.md"}
NAMEN = {"l1": "Anna Muster"}


def _soll(ts, archiv=False):
    return termine_soll("Vibemind", ts, PFADE, NAMEN, heute=HEUTE, archiv_vorhanden=archiv)


def test_termine_aus_absage_und_spaeterer_eintrag():
    ev = [ereignis(lead_id="l1", typ="termin", termin_uid="u1", termin_datum="2026-11-10", termin_uhrzeit="09:00"),
          ereignis(lead_id="l1", typ="termin", termin_uid="u1", termin_datum="2026-11-12", termin_uhrzeit="14:30"),
          ereignis(lead_id="l1", typ="termin", termin_uid="u2", termin_datum="2026-11-13"),
          ereignis(lead_id="l1", typ="termin_abgesagt", termin_uid="u2")]
    aktiv, abgesagt = termine_aus(ev)
    assert [(t.uid, t.datum, t.uhrzeit) for t in aktiv] == [("u1", date(2026, 11, 12), "14:30")]
    assert abgesagt == {"u2"}


def test_termine_aus_dauer_und_ungueltiges():
    ev = [ereignis(lead_id="l1", typ="termin", termin_uid="a", termin_datum="2026-11-10", termin_dauer="45",
                   termin_thema="T", termin_ort="O"),
          ereignis(lead_id="l1", typ="termin", termin_uid="b", termin_datum="2026-11-11", termin_dauer="abc"),
          ereignis(lead_id="l1", typ="termin", termin_uid="c", termin_datum="2026-11-12", termin_dauer="0"),
          ereignis(lead_id="l1", typ="termin", termin_uid="d", termin_datum="2026-11-13", termin_dauer=""),
          ereignis(lead_id="l1", typ="termin", termin_uid="e", termin_datum="27.10.2026")]
    aktiv, _ = termine_aus(ev)
    assert {t.uid: t.dauer_minuten for t in aktiv} == {"a": 45, "b": None, "c": None, "d": None}
    a = aktiv[0]
    assert (a.thema, a.ort, a.lead_id) == ("T", "O", "l1")


def test_termin_notiz_pfad_kopf_und_text():
    t = Termin("l1", "u1abcdef", date(2026, 11, 12), "14:30", 45, "Erstgespraech", "https://meet.jit.si/raum")
    soll = {s.schluessel: s for s in _soll([t])}
    notiz = soll["termin:l1:u1abcdef"]
    assert notiz.rel == "Termine/2026-11-12 1430 Anna Muster.md"
    assert notiz.inhalt.startswith("---\n")
    assert 'parent: "[[companys/Vibemind/Termine/_overview]]"' in notiz.inhalt
    assert 'kontakt: "[[companys/Vibemind/Kontakte/Kunden/Anna Muster]]"' in notiz.inhalt
    assert "kontakt_id: l1" in notiz.inhalt and "termin_uid: u1abcdef" in notiz.inhalt
    assert "Erstgespraech" in notiz.inhalt and "(https://meet.jit.si/raum)" in notiz.inhalt
    assert "nicht bearbeiten" in notiz.inhalt
    ueb = soll["termine:uebersicht"]
    assert ueb.rel == "Termine/_overview.md"
    assert 'parent: "[[companys/Vibemind/Bereiche/_overview]]"' in ueb.inhalt
    assert "[[companys/Vibemind/Termine/2026-11-12 1430 Anna Muster]]" in ueb.inhalt
    assert "/kalender" in ueb.inhalt


def test_reihenfolge_notizen_zuerst_uebersicht_zuletzt():
    ts = [Termin("l1", "b", date(2026, 11, 12), None, None, None, None),
          Termin("l1", "a", date(2026, 11, 10), None, None, None, None)]
    soll = _soll(ts)
    assert [s.rel for s in soll] == ["Termine/2026-11-10 Anna Muster.md", "Termine/2026-11-12 Anna Muster.md",
                                     "Termine/_overview.md"]


def test_uebersicht_nur_kommende_14_tage_und_zahl_vergangener():
    ts = [Termin("l1", "a", date(2026, 11, 1), "10:00", None, None, None),
          Termin("l1", "b", date(2026, 11, 22), "10:00", None, None, None),
          Termin("l1", "c", date(2026, 11, 23), "10:00", None, None, None)]
    ueb = {s.schluessel: s for s in _soll(ts)}["termine:uebersicht"]
    assert "2026-11-22 1000" in ueb.inhalt and "2026-11-23 1000" not in ueb.inhalt
    assert "Vergangene Termine: 1" in ueb.inhalt


def test_gleicher_name_bekommt_uid_zusatz():
    ts = [Termin("l1", "aaaaaaaa1", date(2026, 11, 12), "14:30", None, None, None),
          Termin("l1", "bbbbbbbb2", date(2026, 11, 12), "14:30", None, None, None)]
    rels = sorted(s.rel for s in _soll(ts))
    assert "Termine/2026-11-12 1430 Anna Muster (bbbbbbbb).md" in rels


def test_ohne_steckbrief_kein_termin_und_ohne_termine_keine_uebersicht():
    t = Termin("fremd", "u9", date(2026, 11, 12), None, None, None, None)
    assert _soll([t]) == []


def test_archiv_vorhanden_erzeugt_leere_uebersicht():
    soll = _soll([], archiv=True)
    assert [s.schluessel for s in soll] == ["termine:uebersicht"]
    assert "Vergangene Termine: 0" in soll[0].inhalt


def test_name_fehlt_ersatzname():
    t = Termin("l1", "u1", date(2026, 11, 12), "9:05", None, None, None)
    soll = termine_soll("Vibemind", [t], PFADE, {}, heute=HEUTE, archiv_vorhanden=False)
    assert soll[0].rel == "Termine/2026-11-12 0905 Kontakt l1.md"


def test_termin_fremdtext_neutralisiert():
    t = Termin("l1", "u1", date(2026, 11, 12), None, None, "Thema\n[[Geheim]] @rowboat los", "javascript:alert(1)")
    inhalt = _soll([t])[0].inhalt
    assert "[[Geheim]]" not in inhalt and "@rowboat" not in inhalt.lower() and "\nlos" not in inhalt
    assert "](javascript:" not in inhalt   # nur https-Orte werden Link


def test_thema_gekuerzt_und_ort_mit_leerzeichen_kein_link():
    t = Termin("l1", "u1", date(2026, 11, 12), None, None, "x" * 500, "https://a.de/ b")
    inhalt = _soll([t])[0].inhalt
    assert "x" * 201 not in inhalt and "](https://a.de" not in inhalt


def test_gesperrte_termin_schluessel():
    ist = ["termin:l1:u1", "termin:l2:u2", "termin:l3:u3", "l1", "termine:uebersicht"]
    assert gesperrte_termin_schluessel(ist, {"l2"}, {"u3"}) == {"termin:l2:u2", "termin:l3:u3"}
