"""einrichtung: Stand der Einrichtung (rein) und Pruefung der Pflichtangaben."""
from spaces.marketing.claw.einrichtung import ABMELDE_VORGABE, pflichtteil_pruefen, stand, stand_text

FARBEN_SCHRIFTEN = {k: "x" for k in ("akzent", "zweitfarbe", "grund", "text", "schrift_anzeige", "schrift_text")}
PFLICHT = {"impressum": "Muster GmbH, Beispielweg 1", "abmelde_hinweis": ABMELDE_VORGABE, "vermittlerangaben": ""}


def _stand(profil=None, hat_logo=False, pflichtteil=None, einrichtung_stand=None, name="Muster"):
    return stand(name, profil, hat_logo, pflichtteil or {}, einrichtung_stand or {})


def _schritt(s, nr):
    return next(x for x in s["schritte"] if x["nr"] == nr)


def test_stand_leer():
    s = _stand()
    assert _schritt(s, 1)["erledigt"] is False
    assert _schritt(s, 1)["offen"] == ["webseite"]
    assert s["naechster"] == 1
    assert s["abschliessbar"] is False


def test_stand_uebersprungen_zaehlt():
    s = _stand(einrichtung_stand={"uebersprungen": [1, 3]})
    assert _schritt(s, 1)["erledigt"] and _schritt(s, 3)["erledigt"]
    assert not _schritt(s, 4)["erledigt"]


def test_schritt2_braucht_vermittler_antwort():
    assert _schritt(_stand(pflichtteil=PFLICHT, einrichtung_stand={}), 2)["erledigt"] is False
    assert _schritt(_stand(pflichtteil=PFLICHT, einrichtung_stand={"vermittler": None}), 2)["erledigt"] is False
    assert _schritt(_stand(pflichtteil=PFLICHT, einrichtung_stand={"vermittler": False}), 2)["erledigt"] is True
    assert _schritt(_stand(pflichtteil=PFLICHT, einrichtung_stand={"vermittler": True}), 2)["erledigt"] is False
    mit = dict(PFLICHT, vermittlerangaben="Reg.-Nr. D-1234")
    assert _schritt(_stand(pflichtteil=mit, einrichtung_stand={"vermittler": True}), 2)["erledigt"] is True


def test_schritt2_abmeldehinweis_braucht_platzhalter():
    kaputt = dict(PFLICHT, abmelde_hinweis="Abmelden bitte")
    assert _schritt(_stand(pflichtteil=kaputt, einrichtung_stand={"vermittler": False}), 2)["erledigt"] is False


def test_abschliessbar_nur_mit_1_und_2():
    s = _stand(profil={"werte": {"webseite": "https://m.de"}, "abschnitte": {}},
               pflichtteil=PFLICHT, einrichtung_stand={"vermittler": False})
    assert s["abschliessbar"] is True
    assert s["naechster"] == 3
    assert _stand(name="")["abschliessbar"] is False


def test_schritt4_logo():
    profil = {"werte": dict(FARBEN_SCHRIFTEN), "abschnitte": {}}
    s4 = _schritt(_stand(profil=profil, hat_logo=False), 4)
    assert s4["erledigt"] is False and s4["offen"] == ["logo"]
    assert _schritt(_stand(profil=profil, hat_logo=True), 4)["erledigt"] is True


def test_schritt7_nie_vorab_erledigt():
    s = _stand(einrichtung_stand={"uebersprungen": [1, 3, 4, 5, 6]})
    assert _schritt(s, 7)["erledigt"] is False


def test_naechster_sieben_wenn_alles_erledigt():
    profil = {"werte": dict(FARBEN_SCHRIFTEN, webseite="https://m.de"), "abschnitte": {
        n: "t" for n in ("Kurz gesagt", "Wer wir sind", "Angebote", "Zielgruppe", "Abgrenzung", "Ton", "Wörter",
                          "Do & Don'ts", "Fakten und Zahlen", "Kanäle", "Menschen", "Bildstil")}}
    s = _stand(profil=profil, hat_logo=True, pflichtteil=PFLICHT, einrichtung_stand={"vermittler": False})
    assert s["naechster"] == 7


def test_stand_text_format():
    t = stand_text(_stand(pflichtteil=PFLICHT))
    assert "Schritt 2 von 7 – Pflichtangaben für Newsletter: offen" in t
    assert "Nächster Schritt: 1" in t
    assert "Schritt 1 von 7 – Name und Webseite: offen (webseite)" in t


def test_pflichtteil_aus_nachricht_uebernommen():
    ok, hinweise = pflichtteil_pruefen({"impressum": "Muster GmbH, Weg 1"}, ["Unser Impressum: Muster GmbH, Weg 1 ok"])
    assert ok == {"impressum": "Muster GmbH, Weg 1"} and hinweise == []


def test_pflichtteil_leerraum_egal():
    beleg = "Muster GmbH\n  Weg   1\n\n12345 Stadt"
    ok, hinweise = pflichtteil_pruefen({"impressum": "Muster GmbH Weg 1 12345 Stadt"}, [beleg])
    assert "impressum" in ok and hinweise == []


def test_pflichtteil_erfunden_verworfen():
    ok, hinweise = pflichtteil_pruefen({"impressum": "Erfunden AG"}, ["etwas ganz anderes"])
    assert "impressum" not in ok
    assert hinweise[0].startswith("Impressum nicht übernommen")


def test_vermittlerangaben_erfunden_verworfen():
    ok, hinweise = pflichtteil_pruefen({"vermittlerangaben": "D-999"}, [])
    assert ok == {} and hinweise[0].startswith("Vermittlerangaben nicht übernommen")


def test_abmeldehinweis_ohne_platzhalter_verworfen():
    ok, hinweise = pflichtteil_pruefen({"abmelde_hinweis": "Abmelden per Mail"}, [])
    assert ok == {} and len(hinweise) == 1 and "{abmeldelink}" in hinweise[0]


def test_abmeldehinweis_vorgabe_ohne_beleg_ok():
    ok, hinweise = pflichtteil_pruefen({"abmelde_hinweis": ABMELDE_VORGABE}, [])
    assert ok == {"abmelde_hinweis": ABMELDE_VORGABE} and hinweise == []


def test_pflichtteil_nicht_objekt_und_unbekannte_schluessel():
    ok, hinweise = pflichtteil_pruefen("text", [])
    assert ok == {} and hinweise
    ok, hinweise = pflichtteil_pruefen({"foo": "x"}, [])
    assert ok == {} and hinweise
