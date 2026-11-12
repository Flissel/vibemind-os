"""Tests fuer Kategorien mit Vererbung."""
from pathlib import Path

import pytest

from spaces.event_management.system.bausteine import KERN, ZUSAETZE
from spaces.event_management.system.kategorien import (
    KategorieFehler,
    lade_kategorien,
    waehlbare,
    wirksames_schema,
)

ALLE = {
    "basis", "veranstaltung", "fest", "festival", "weihnachtsfeier",
    "messe", "online_event", "webinar", "hackathon", "team_meeting",
}


def test_alle_kategorien_laden_und_sind_gueltig():
    assert set(lade_kategorien()) == ALLE


def test_basis_ist_abstrakt_und_nicht_waehlbar():
    k = lade_kategorien()
    assert "basis" not in [x.id for x in waehlbare(k)]
    assert len(waehlbare(k)) == 9
    namen = [x.name for x in waehlbare(k)]
    assert namen == sorted(namen)


def test_festival_erbt_ueber_fest_und_veranstaltung():
    s = wirksames_schema(lade_kategorien(), "festival")
    assert s.kette == ("basis", "veranstaltung", "fest", "festival")
    assert {"programm", "verpflegung", "helferschichten", "buehnen"} <= set(s.bausteine)
    assert set(KERN) <= set(s.bausteine)
    assert s.begriffe["ablauf"] == "Timetable"
    assert "kapazitaet" in {f.id for f in s.felder}


def test_team_meeting_ohne_budget_mit_tagesordnung():
    s = wirksames_schema(lade_kategorien(), "team_meeting")
    assert "budget" not in s.bausteine
    assert s.begriffe["ablauf"] == "Tagesordnung"


def test_webinar_verlangt_online_link():
    s = wirksames_schema(lade_kategorien(), "webinar")
    assert "ort_link" in s.eckdaten_pflicht
    assert "plattform" in {f.id for f in s.felder}
    assert "anmeldung_noetig" in {f.id for f in s.felder}


def test_jeder_baustein_ist_bekannt():
    k = lade_kategorien()
    for kid in k:
        s = wirksames_schema(k, kid)
        assert set(s.bausteine) <= set(KERN + ZUSAETZE)
        assert "verlauf" in s.bausteine


def test_feld_ueberschreiben_behaelt_typ(tmp_path: Path):
    _basis(tmp_path)
    _schreibe(tmp_path, "kind", "id: kind\nname: Kind\neltern: basis\n"
              "felder:\n  - {id: a, typ: text, beschriftung: A}\n")
    _schreibe(tmp_path, "enkel", "id: enkel\nname: Enkel\neltern: kind\n"
              "felder:\n  - {id: a, typ: text, beschriftung: Neu, pflicht: true}\n")
    s = wirksames_schema(lade_kategorien(tmp_path), "enkel")
    (f,) = [x for x in s.felder if x.id == "a"]
    assert f.beschriftung == "Neu" and f.pflicht is True


# --- kaputte Dateien ---------------------------------------------------

def _schreibe(ordner: Path, name: str, text: str) -> None:
    (ordner / f"{name}.yaml").write_text(text, encoding="utf-8")


def _basis(ordner: Path) -> None:
    _schreibe(ordner, "basis",
              "id: basis\nname: Basis\neltern: null\nabstrakt: true\n"
              "bausteine: [aufgaben, verlauf]\n")


def test_zyklus_wird_erkannt(tmp_path: Path):
    _basis(tmp_path)
    _schreibe(tmp_path, "a", "id: a\nname: A\neltern: b\n")
    _schreibe(tmp_path, "b", "id: b\nname: B\neltern: a\n")
    with pytest.raises(KategorieFehler, match=r"(?s)(?=.*Zyklus)(?=.*\ba\b)"):
        lade_kategorien(tmp_path)


def test_unbekannter_elternteil(tmp_path: Path):
    _basis(tmp_path)
    _schreibe(tmp_path, "waise", "id: waise\nname: Waise\neltern: gibtsnicht\n")
    with pytest.raises(KategorieFehler, match=r"(?s)(?=.*waise)(?=.*gibtsnicht)"):
        lade_kategorien(tmp_path)


def test_feldtyp_darf_nicht_wechseln(tmp_path: Path):
    _basis(tmp_path)
    _schreibe(tmp_path, "eins", "id: eins\nname: Eins\neltern: basis\n"
              "felder:\n  - {id: x, typ: text, beschriftung: X}\n")
    _schreibe(tmp_path, "zwei", "id: zwei\nname: Zwei\neltern: eins\n"
              "felder:\n  - {id: x, typ: zahl, beschriftung: X}\n")
    with pytest.raises(KategorieFehler, match=r"(?s)(?=.*zwei)(?=.*typ)"):
        lade_kategorien(tmp_path)


def test_unbekannter_baustein(tmp_path: Path):
    _basis(tmp_path)
    _schreibe(tmp_path, "komisch", "id: komisch\nname: K\neltern: basis\n"
              "bausteine: [zauberei]\n")
    with pytest.raises(KategorieFehler, match=r"(?s)(?=.*komisch)(?=.*zauberei)"):
        lade_kategorien(tmp_path)


def test_verlauf_nicht_abwaehlbar(tmp_path: Path):
    _basis(tmp_path)
    _schreibe(tmp_path, "ohnev", "id: ohnev\nname: O\neltern: basis\nohne: [verlauf]\n")
    with pytest.raises(KategorieFehler, match=r"(?s)(?=.*ohnev)(?=.*verlauf)"):
        lade_kategorien(tmp_path)


def test_ohne_nur_fuer_geerbte(tmp_path: Path):
    _basis(tmp_path)
    _schreibe(tmp_path, "ohneg", "id: ohneg\nname: O\neltern: basis\nohne: [jury]\n")
    with pytest.raises(KategorieFehler, match=r"(?s)(?=.*ohneg)(?=.*jury)"):
        lade_kategorien(tmp_path)


def test_id_muss_dateiname_sein(tmp_path: Path):
    _basis(tmp_path)
    _schreibe(tmp_path, "datei", "id: andere\nname: A\neltern: basis\n")
    with pytest.raises(KategorieFehler, match=r"(?s)(?=.*andere)(?=.*datei\.yaml)"):
        lade_kategorien(tmp_path)


def test_basis_mit_elternteil(tmp_path: Path):
    _schreibe(tmp_path, "basis", "id: basis\nname: B\neltern: x\n")
    with pytest.raises(KategorieFehler, match="basis"):
        lade_kategorien(tmp_path)


def test_doppelte_feld_id_und_unbekannter_feldtyp(tmp_path: Path):
    _basis(tmp_path)
    _schreibe(tmp_path, "dop", "id: dop\nname: D\neltern: basis\nfelder:\n"
              "  - {id: x, typ: text, beschriftung: X}\n"
              "  - {id: x, typ: text, beschriftung: Y}\n")
    with pytest.raises(KategorieFehler, match=r"(?s)(?=.*dop)(?=.*doppelt)"):
        lade_kategorien(tmp_path)
    (tmp_path / "dop.yaml").unlink()
    _schreibe(tmp_path, "typ", "id: typ\nname: T\neltern: basis\nfelder:\n"
              "  - {id: x, typ: gibtsnicht, beschriftung: X}\n")
    with pytest.raises(KategorieFehler, match=r"(?s)(?=.*typ)(?=.*gibtsnicht)"):
        lade_kategorien(tmp_path)


def test_auswahl_braucht_optionen(tmp_path: Path):
    _basis(tmp_path)
    _schreibe(tmp_path, "au", "id: au\nname: A\neltern: basis\nfelder:\n"
              "  - {id: x, typ: auswahl, beschriftung: X}\n")
    with pytest.raises(KategorieFehler, match=r"(?s)(?=.*au)(?=.*Option)"):
        lade_kategorien(tmp_path)


# --- Vorgabe passt zum Typ (R2), fail-closed beim Laden ------------------------------


@pytest.mark.parametrize(("typ", "vorgabe"), [
    ("ja_nein", "'ja'"), ("ja_nein", "1"), ("zahl", "'viele'"), ("zahl", "true"),
    ("zahl", ".nan"), ("zahl", ".inf"), ("text", "5"), ("langtext", "[a]"),
    ("url", "'ftp://example.org'"), ("url", "'javascript:alert(1)'"),
    ("datum", "'kein-datum'"), ("datum", "2027-01-01"),  # YAML-Datum ist kein ISO-Text
    ("datumzeit", "'morgen'"), ("auswahl", "c"),
])
def test_vorgabe_muss_zum_typ_passen(tmp_path: Path, typ: str, vorgabe: str):
    _basis(tmp_path)
    extra = ", optionen: [a, b]" if typ == "auswahl" else ""
    _schreibe(tmp_path, "vg", "id: vg\nname: V\neltern: basis\nfelder:\n"
              f"  - {{id: x, typ: {typ}, beschriftung: X, vorgabe: {vorgabe}{extra}}}\n")
    with pytest.raises(KategorieFehler, match=r"(?s)(?=.*vg)(?=.*vorgabe)"):
        lade_kategorien(tmp_path)


def test_passende_vorgaben_laden(tmp_path: Path):
    _basis(tmp_path)
    _schreibe(tmp_path, "ok", "id: ok\nname: O\neltern: basis\nfelder:\n"
              "  - {id: a, typ: ja_nein, beschriftung: A, vorgabe: false}\n"
              "  - {id: b, typ: zahl, beschriftung: B, vorgabe: 1.5}\n"
              "  - {id: c, typ: url, beschriftung: C, vorgabe: 'https://example.org'}\n"
              "  - {id: d, typ: datum, beschriftung: D, vorgabe: '2027-01-01'}\n"
              "  - {id: e, typ: auswahl, beschriftung: E, vorgabe: b, optionen: [a, b]}\n"
              "  - {id: f, typ: text, beschriftung: F}\n")
    felder = {f.id: f.vorgabe for f in wirksames_schema(lade_kategorien(tmp_path), "ok").felder}
    assert felder == {"a": False, "b": 1.5, "c": "https://example.org", "d": "2027-01-01",
                      "e": "b", "f": None}


def test_fehlender_oder_leerer_ordner(tmp_path: Path):
    with pytest.raises(KategorieFehler):
        lade_kategorien(tmp_path / "gibtsnicht")
    with pytest.raises(KategorieFehler):
        lade_kategorien(tmp_path)  # leer


def test_fehlende_basis(tmp_path: Path):
    _schreibe(tmp_path, "kind", "id: kind\nname: Kind\neltern: basis\n")
    with pytest.raises(KategorieFehler, match="basis"):
        lade_kategorien(tmp_path)
