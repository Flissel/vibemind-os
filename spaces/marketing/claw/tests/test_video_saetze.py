"""Satztrenner der Video-Werkbank: Abkuerzungen und Zahlen trennen nicht, alles <= TEXT_MAX."""
from spaces.marketing.claw.ton_dienst import TEXT_MAX
from spaces.marketing.claw.video_saetze import saetze


def test_abkuerzung_und_zahl_trennen_nicht():
    assert saetze("Das spart z. B. 3,5 % Zeit. Am 1. Januar ging es los! Und dann?") == [
        "Das spart z. B. 3,5 % Zeit.", "Am 1. Januar ging es los!", "Und dann?"]


def test_tausenderpunkt_bleibt():
    assert saetze("Es waren 3.500 Mails. Viele.") == ["Es waren 3.500 Mails.", "Viele."]


def test_weitere_abkuerzungen_trennen_nicht():
    text = "Dr. Meyer prueft ca. 20 Mails, d. h. fast alle, u. a. Nr. 5 inkl. Anhang, ggf. mehr, bzw. weniger. Fertig."
    assert saetze(text) == [text[:-len(" Fertig.")], "Fertig."]


def test_ellipse_ausruf_und_anfuehrungszeichen():
    assert saetze("Wirklich… Ja! \"Dann los.\" „Gut.“ 3 Dinge bleiben.") == [
        "Wirklich…", "Ja!", "\"Dann los.\"", "„Gut.“", "3 Dinge bleiben."]


def test_kleinbuchstabe_nach_punkt_trennt_nicht():
    assert saetze("Es gibt www.beispiel.de und mehr. aber nicht hier.") == [
        "Es gibt www.beispiel.de und mehr. aber nicht hier."]


def test_zeilenumbrueche_werden_zu_leerraum():
    assert saetze("Eins.\n\nZwei.\r\nDrei.") == ["Eins.", "Zwei.", "Drei."]


def test_ueberlanger_satz_wird_am_komma_geteilt():
    teil = "und noch ein Stueck Text, " * 20
    lang = ("Das ist ein sehr langer Satz, " + teil) * 4 + "Ende."
    assert len(lang) > TEXT_MAX
    teile = saetze(lang)
    assert len(teile) > 1 and all(0 < len(t) <= TEXT_MAX for t in teile)
    assert all(t.endswith(",") or t.endswith(".") for t in teile)
    assert " ".join(teile) == lang


def test_ueberlanger_satz_ohne_komma_wird_am_leerzeichen_geteilt():
    lang = " ".join(["wort"] * 600) + "."
    teile = saetze(lang)
    assert all(len(t) <= TEXT_MAX for t in teile) and " ".join(teile) == lang


def test_leerer_text_leere_liste():
    assert saetze("  ") == []
    assert saetze("") == []
