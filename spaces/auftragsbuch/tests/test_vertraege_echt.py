"""Tests gegen die echten Vertraege unter spaces/_contract (nicht gegen Beispiele)."""
from pathlib import Path

import pytest
import yaml

from spaces.auftragsbuch.vertrag import FormFehler, art_hash, lade_auftragsarten, pruefe_eingabe, pruefe_ergebnis
from spaces.auftragsbuch.werkzeuge import katalog

ORDNER = Path(__file__).resolve().parents[2] / "_contract"

PUNKT_OHNE_ENTWURF = {
    "punkt_id": "0123456789abcdef",
    "titel": "Rueckfrage zum Angebot beantworten",
    "frist": "2026-12-01",
    "link": "/sales/punkt/1",
    "entwurf_id": None,
}
PUNKT_MIT_ENTWURF = {
    **PUNKT_OHNE_ENTWURF,
    "punkt_id": "fedcba9876543210",
    "titel": "E-Mail an Beispiel Muster: Terminvorschlag",
    "frist": None,
    "entwurf_id": "entwurf-42",
}
EINGABE_PUNKT = {
    "punkt_id": "0123456789abcdef",
    "titel": "Rueckfrage zum Angebot beantworten",
    "frist": "2026-12-01",
    "link": "https://beispiel.invalid/sales/punkt/1",
}


def test_genau_die_drei_auftragsarten():
    assert set(lade_auftragsarten(ORDNER)) == {
        ("sales", "post_pruefen"), ("sales", "kontakt_finden"), ("rowboat", "vorschlaege_anlegen")}


def test_werte_aus_dem_entwurf():
    arten = lade_auftragsarten(ORDNER)
    p, v = arten[("sales", "post_pruefen")], arten[("rowboat", "vorschlaege_anlegen")]
    assert (p.version, p.veraendert, p.frist_min, p.nachpruefung) == (2, True, 15, "beleg")
    assert (v.version, v.veraendert, v.frist_min, v.nachpruefung) == (2, True, 5, "beleg")


def test_post_pruefen_form():
    p = lade_auftragsarten(ORDNER)[("sales", "post_pruefen")]
    pruefe_eingabe(p, {"zeitraum": "heute", "entwuerfe_anlegen": True})
    pruefe_ergebnis(p, {"faellig": [PUNKT_MIT_ENTWURF, PUNKT_OHNE_ENTWURF], "anzahl_gelesen": 7, "kontakte_neu": 1})
    pruefe_ergebnis(p, {"faellig": [], "anzahl_gelesen": 0, "kontakte_neu": 0})
    for falsch in ({"zeitraum": "gestern", "entwuerfe_anlegen": True},
                   {"zeitraum": "heute"},
                   {"zeitraum": "heute", "entwuerfe_anlegen": True, "postfach": "x"}):
        with pytest.raises(FormFehler):
            pruefe_eingabe(p, falsch)
    with pytest.raises(FormFehler):      # Mail-Text ist kein Feld
        pruefe_ergebnis(p, {"faellig": [{**PUNKT_OHNE_ENTWURF, "text": "x"}], "anzahl_gelesen": 1, "kontakte_neu": 0})


def test_vorschlaege_anlegen_form():
    v = lade_auftragsarten(ORDNER)[("rowboat", "vorschlaege_anlegen")]
    pruefe_eingabe(v, {"punkte": [EINGABE_PUNKT]})
    pruefe_ergebnis(v, {"punkte": [{"punkt_id": "0123456789abcdef", "stand": "schon_vorhanden"}]})
    for falsch in ({"punkte": []},
                   {"punkte": [{**EINGABE_PUNKT, "link": "https://beispiel.invalid/freigaben#fg-1"}]},
                   {"punkte": [{**EINGABE_PUNKT, "punkt_id": "kurz"}]}):
        with pytest.raises(FormFehler):
            pruefe_eingabe(v, falsch)


def test_sales_vertrag_hat_noch_keine_capabilities():
    roh = yaml.safe_load((ORDNER / "sales.contract.yaml").read_text(encoding="utf-8"))
    assert roh["id"] == "sales" and roh["prefixes"] == ["sales."] and roh["capabilities"] == []


def test_art_hash_post_pruefen_v2_festgehalten():
    # v1 hatte 75fb9ae6496d8cde21531cda7109ce4e647ca298a24e7959f43be57467b54189
    a = lade_auftragsarten(ORDNER)[("sales", "post_pruefen")]
    assert a.version == 2
    assert art_hash(a) == "a2e72e7575ba93d138d34eea07eaa9da35c6398104092bc121e67bf482cde58f"


def test_art_hash_vorschlaege_anlegen_v2_festgehalten():
    a = lade_auftragsarten(ORDNER)[("rowboat", "vorschlaege_anlegen")]
    assert art_hash(a) == "56a91ce06f16caf222bb5c09509bbbde4fa43ec11f3a386400164be0f56fa15a"


def test_art_hash_kontakt_finden():
    a = lade_auftragsarten(ORDNER)[("sales", "kontakt_finden")]
    assert (a.version, a.veraendert, a.nachpruefung) == (1, False, "form")
    assert art_hash(a) == "861ecd0af8279ca2c5164e005d07bee24fa38e315dcab1b5b3d26051f32a43b8"


@pytest.mark.parametrize("link, ok", [
    ("", True),
    ("/sales/punkt/1", True),
    ("https://beispiel.invalid/x", True),
    ("javascript:alert(1)", False),
    ("http://beispiel.invalid/x", False),
    ("/freigaben#fg-1", False),
    ("https://beispiel.invalid/freigaben#fg-1", False),
    ("/mit leerzeichen", False),
])
def test_vorschlaege_anlegen_link_muster(link, ok):
    v = lade_auftragsarten(ORDNER)[("rowboat", "vorschlaege_anlegen")]
    eingabe = {"punkte": [{**EINGABE_PUNKT, "link": link}]}
    if ok:
        pruefe_eingabe(v, eingabe)
    else:
        with pytest.raises(FormFehler):
            pruefe_eingabe(v, eingabe)


def test_link_javascript_abgelehnt():
    v = lade_auftragsarten(ORDNER)[("rowboat", "vorschlaege_anlegen")]
    with pytest.raises(FormFehler):
        pruefe_eingabe(v, {"punkte": [{**EINGABE_PUNKT, "link": "javascript:alert(1)"}]})


def test_link_leer_erlaubt():
    v = lade_auftragsarten(ORDNER)[("rowboat", "vorschlaege_anlegen")]
    pruefe_eingabe(v, {"punkte": [{**EINGABE_PUNKT, "link": ""}]})


def test_link_freigabe_abgelehnt():
    v = lade_auftragsarten(ORDNER)[("rowboat", "vorschlaege_anlegen")]
    with pytest.raises(FormFehler):
        pruefe_eingabe(v, {"punkte": [{**EINGABE_PUNKT, "link": "/freigaben#fg-1"}]})


def test_vorschlaege_anlegen_ist_version_2_und_nennt_den_abholer():
    v = lade_auftragsarten(ORDNER)[("rowboat", "vorschlaege_anlegen")]
    assert v.version == 2
    assert "Abholer" in v.beschreibung and "Rowboat-Agent" not in v.beschreibung


@pytest.mark.parametrize("link, entwurf_id, ok", [
    ("/sales/punkt/1", None, True),
    ("/sales/punkt/1", "entwurf-42", True),
    ("https://beispiel.invalid/x", None, False),
    ("javascript:alert(1)", None, False),
    ("", None, False),
    ("/sales/punkt/1", "mit leerzeichen", False),
    ("/sales/punkt/1", "x" * 65, False),
    ("/sales/punkt/1", "", False),
])
def test_post_pruefen_v2_muster_fuer_link_und_entwurf_id(link, entwurf_id, ok):
    p = lade_auftragsarten(ORDNER)[("sales", "post_pruefen")]
    assert p.version == 2
    ergebnis = {"faellig": [{**PUNKT_OHNE_ENTWURF, "link": link, "entwurf_id": entwurf_id}],
                "anzahl_gelesen": 1, "kontakte_neu": 0}
    if ok:
        pruefe_ergebnis(p, ergebnis)
    else:
        with pytest.raises(FormFehler):
            pruefe_ergebnis(p, ergebnis)


def test_kontakt_finden_form():
    k = lade_auftragsarten(ORDNER)[("sales", "kontakt_finden")]
    pruefe_eingabe(k, {"suche": "Mu"})
    kennung = "0123abcd-0123-4abc-8def-0123456789ab"
    treffer = {"kontakt_id": kennung, "titel": "Beispiel Muster", "link": f"/kontakte/{kennung}"}
    pruefe_ergebnis(k, {"treffer": [treffer], "anzahl": 1})
    for falsch in ({"suche": "M"}, {"suche": "x" * 101}, {}):
        with pytest.raises(FormFehler):
            pruefe_eingabe(k, falsch)
    with pytest.raises(FormFehler):
        pruefe_ergebnis(k, {"treffer": [{**treffer, "adresse": "x"}], "anzahl": 1})
    with pytest.raises(FormFehler):
        pruefe_ergebnis(k, {"treffer": [treffer] * 11, "anzahl": 10})


def test_werkzeuge_aus_den_echten_arten_feste_und_je_art():
    namen = [w["name"] for w in katalog(lade_auftragsarten(ORDNER))]
    assert namen == ["auftrag_probe", "auftrag_lesen",
                     "rowboat_vorschlaege_anlegen", "sales_kontakt_finden", "sales_post_pruefen"]
