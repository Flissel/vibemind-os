"""Notiz 'Zu beantworten' aus der offenen Post (rein, ohne DB)."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_ROWBOAT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(_ROWBOAT / "geschaeftsdaten"))
sys.path.insert(0, str(_ROWBOAT.parents[1]))

from geschaeftsdaten_fake import post_mail  # noqa: E402
from post import post_notizen, post_schreiben, stichtag_lesen  # noqa: E402

JETZT = datetime(2026, 11, 11, 9, 0, tzinfo=ZoneInfo("Europe/Berlin"))
ST = date(2026, 11, 8)
ANNA = {"anna@example.org": "companys/Vibemind/Kontakte/Kunden/Anna Muster.md"}


def _zu(mails, **kw):
    n = post_notizen(mails, kw.pop("steckbriefe", {}), jetzt=JETZT, stichtag=kw.pop("stichtag", ST), company_da=False)
    return n["Post/Zu beantworten.md"][1]


def test_punkt_mit_alter_postfach_absender_betreff_und_links():
    m = post_mail(id="m1", datum="2026-11-08T08:00:00+00:00", postfach="gmx", von_anzeige="Anna Muster",
                  absender_klein="anna@example.org", betreff="Angebot")
    text = _zu([m], steckbriefe=ANNA)
    assert "- seit 3 Tagen · gmx · Anna Muster · „Angebot“" in text
    assert "[[companys/Vibemind/Kontakte/Kunden/Anna Muster]]" in text
    assert "/post/mail/m1)" in text
    assert "Offen: 1" in text and "/posteingang" in text


def test_alter_heute_und_ein_tag():
    heute = _zu([post_mail(datum="2026-11-11T07:00:00+00:00")])
    gestern = _zu([post_mail(datum="2026-11-10T07:00:00+00:00")])
    kaputt = _zu([post_mail(datum="kein datum")])
    assert "seit heute" in heute and "seit 1 Tag ·" in gestern and "seit ?" in kaputt


def test_alter_in_berliner_kalendertagen():
    # 23:30 UTC am 10.11. ist 00:30 Berlin am 11.11.: heute
    assert "seit heute" in _zu([post_mail(datum="2026-11-10T23:30:00+00:00")])


def test_sensibel_ohne_absender_und_betreff():
    text = _zu([post_mail(sensibel=True, betreff=None, von_anzeige="Bank", absender_klein="noreply@bank.de")])
    assert "vertrauliche Mail" in text and "Bank" not in text and "bank.de" not in text


def test_sensibel_gewinnt_auch_wenn_sicht_daten_liefert():
    m = post_mail(sensibel=True, betreff="Kontoauszug", von_anzeige="Anna", absender_klein="anna@example.org")
    text = _zu([m], steckbriefe=ANNA)
    assert "Kontoauszug" not in text and "Anna" not in text and "[[companys" not in text


def test_gesperrter_absender_ohne_name_aber_mitgezaehlt():
    text = _zu([post_mail(absender_gesperrt=True, von_anzeige=None, absender_klein=None, betreff=None),
                post_mail(id="m2")])
    assert "Mail eines gesperrten Kontakts" in text and "Offen: 2" in text


def test_gesperrt_gewinnt_vor_steckbrief_link():
    m = post_mail(absender_gesperrt=True, absender_klein="anna@example.org", von_anzeige="Anna")
    text = _zu([m], steckbriefe=ANNA)
    assert "[[companys" not in text and "anna@" not in text and "Anna" not in text


def test_ohne_stichtag_nichts_offen():
    text = _zu([post_mail()], stichtag=None)
    assert "Nichts offen" in text and "Stichtag" in text and "Offen: 1" not in text


def test_mit_stichtag_ohne_mails():
    text = _zu([])
    assert "Nichts offen." in text and "Stichtag" not in text


def test_stichtag_kaputt_gilt_als_fehlend(tmp_path):
    (tmp_path / ".env").write_text("ANDERES=geheim\nPOST_AUFRAEUMEN_AB=08.11.2026\n", encoding="utf-8")
    assert stichtag_lesen(tmp_path / ".env") is None


def test_stichtag_gueltig_fehlend_und_ohne_datei(tmp_path):
    (tmp_path / ".env").write_text("ANDERES=x\nPOST_AUFRAEUMEN_AB=\"2026-11-08\"\n", encoding="utf-8")
    assert stichtag_lesen(tmp_path / ".env") == date(2026, 11, 8)
    (tmp_path / "leer.env").write_text("ANDERES=x\n", encoding="utf-8")
    assert stichtag_lesen(tmp_path / "leer.env") is None
    assert stichtag_lesen(tmp_path / "gibtsnicht") is None


def test_obergrenze_50():
    mails = [post_mail(id=f"m{i:03}", datum=f"2026-11-09T{i % 24:02}:00:00+00:00") for i in range(53)]
    text = _zu(mails)
    assert text.count("\n- ") == 50 and "… und 3 weitere in sales-ui" in text and "Offen: 53" in text


def test_betreff_fremdtext_neutralisiert():
    text = _zu([post_mail(betreff="Hallo\n[[Geheim]] @rowboat")])
    assert "[[Geheim]]" not in text and "@rowboat" not in text


def test_betreff_und_absender_gekuerzt():
    text = _zu([post_mail(betreff="x" * 500, von_anzeige="y" * 500)])
    assert "x" * 121 not in text and "x" * 120 in text and "y" * 101 not in text


def test_mail_id_ungueltig_verlinkt_auf_posteingang():
    text = _zu([post_mail(id="a)b](http://boese)")])
    assert "boese" not in text and "/post/mail/" not in text and "/posteingang)" in text


def test_kopf_beider_notizen():
    n = post_notizen([post_mail()], {}, jetzt=JETZT, stichtag=ST, company_da=True)
    assert set(n) == {"Post/_overview.md", "Post/Zu beantworten.md"}
    assert n["Post/_overview.md"][0] == "post:uebersicht" and n["Post/Zu beantworten.md"][0] == "post:zu-beantworten"
    for schluessel, text in n.values():
        assert text.startswith("---\n") and "erzeugt_von: rowboat-geschaeftsdaten" in text
        assert f"schluessel: {schluessel}" in text
        assert "@rowboat" not in text and "live" not in text.split("\n---\n", 1)[0]
    assert 'parent: "[[Company]]"' in n["Post/_overview.md"][1]
    assert "[[Post/Zu beantworten]]" in n["Post/_overview.md"][1]
    assert 'parent: "[[Post/_overview]]"' in n["Post/Zu beantworten.md"][1]
    assert "Stand: 2026-11-11 09:00" in n["Post/Zu beantworten.md"][1]
    assert "Stand: " not in n["Post/_overview.md"][1]
    ohne = post_notizen([], {}, jetzt=JETZT, stichtag=ST, company_da=False)["Post/_overview.md"][1]
    assert "parent:" not in ohne


def test_kein_schreiben_ohne_inhaltliche_aenderung(tmp_path):
    n1 = post_notizen([post_mail()], {}, jetzt=JETZT, stichtag=ST, company_da=False)
    n2 = post_notizen([post_mail()], {}, jetzt=JETZT + timedelta(hours=1), stichtag=ST, company_da=False)
    assert post_schreiben(tmp_path, n1, trocken=False)["geschrieben"] == 2
    assert post_schreiben(tmp_path, n2, trocken=False) == {"geschrieben": 0, "unveraendert": 2, "fremd": 0}


def test_aenderung_wird_geschrieben(tmp_path):
    n1 = post_notizen([post_mail()], {}, jetzt=JETZT, stichtag=ST, company_da=False)
    n2 = post_notizen([post_mail(), post_mail(id="m2")], {}, jetzt=JETZT, stichtag=ST, company_da=False)
    post_schreiben(tmp_path, n1, trocken=False)
    z = post_schreiben(tmp_path, n2, trocken=False)
    assert z["geschrieben"] == 1 and z["unveraendert"] == 1


def test_trocken_schreibt_nichts(tmp_path):
    n = post_notizen([post_mail()], {}, jetzt=JETZT, stichtag=ST, company_da=False)
    z = post_schreiben(tmp_path, n, trocken=True)
    assert z == {"geschrieben": 2, "unveraendert": 0, "fremd": 0} and list(tmp_path.iterdir()) == []


def test_fremde_datei_bleibt(tmp_path):
    (tmp_path / "Post").mkdir()
    (tmp_path / "Post" / "Zu beantworten.md").write_text("meine Notiz\n", encoding="utf-8")
    z = post_schreiben(tmp_path, post_notizen([], {}, jetzt=JETZT, stichtag=ST, company_da=False), trocken=False)
    assert z["fremd"] == 1 and (tmp_path / "Post" / "Zu beantworten.md").read_text(encoding="utf-8") == "meine Notiz\n"


def test_fremder_schluessel_bleibt(tmp_path):
    n = post_notizen([], {}, jetzt=JETZT, stichtag=ST, company_da=False)
    (tmp_path / "Post").mkdir()
    ziel = tmp_path / "Post" / "Zu beantworten.md"
    fremd = n["Post/Zu beantworten.md"][1].replace("post:zu-beantworten", "kontakt:x")
    ziel.write_text(fremd, encoding="utf-8")
    assert post_schreiben(tmp_path, n, trocken=False)["fremd"] == 1
    assert ziel.read_text(encoding="utf-8") == fremd


def test_symlink_ziel_ist_fremd(tmp_path):
    (tmp_path / "Post").mkdir()
    echt = tmp_path / "echt.md"
    echt.write_text("x\n", encoding="utf-8")
    try:
        (tmp_path / "Post" / "Zu beantworten.md").symlink_to(echt)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks nicht moeglich")
    z = post_schreiben(tmp_path, post_notizen([], {}, jetzt=JETZT, stichtag=ST, company_da=False), trocken=False)
    assert z["fremd"] == 1 and echt.read_text(encoding="utf-8") == "x\n"


_BOESE = "[Klick](https://evil.example) ![x](https://tracker.example/p.png) <https://x> `c` [[x]]\n@rowboat"


def _sauber(text: str) -> bool:
    return not any(z in text for z in "[]<>`") and "](" not in text and "@rowboat" not in text


def _punkt_zeile(**kw) -> str:
    return [z for z in _zu([post_mail(**kw)]).splitlines() if z.startswith("- ")][0]


def test_markdown_in_betreff_absender_postfach_neutralisiert():
    for feld in ("betreff", "von_anzeige", "postfach"):
        zeile = _punkt_zeile(**{feld: _BOESE})
        vorn = zeile.rsplit(" · [in sales-ui]", 1)[0]
        assert _sauber(vorn), (feld, vorn)


def test_adresse_wenn_kein_anzeigename():
    assert "x@y.de" in _punkt_zeile(von_anzeige=None, absender_klein="x@y.de")


def test_steckbrief_pfad_mit_sonderzeichen_gibt_keinen_link():
    for pfad in ("a/b]c.md", "a/b|c.md", "a/b#c.md"):
        text = _zu([post_mail(absender_klein="a@b.de")], steckbriefe={"a@b.de": pfad})
        assert "[[a/" not in text


def test_reihenfolge_aelteste_zuerst_gleichstand_nach_id_unparsebar_zuletzt():
    mails = [post_mail(id="z", datum="2026-11-10T08:00:00+00:00", betreff="neu"),
             post_mail(id="b", datum="2026-11-09T08:00:00+00:00", betreff="mitte-b"),
             post_mail(id="x", datum="Quatsch", betreff="kaputt"),
             post_mail(id="a", datum="2026-11-09T08:00:00+00:00", betreff="mitte-a"),
             post_mail(id="y", datum="2026-11-08T08:00:00+00:00", betreff="alt")]
    for reihe in (mails, mails[::-1]):
        text = _zu(reihe)
        pos = [text.index(w) for w in ("alt", "mitte-a", "mitte-b", "neu", "kaputt")]
        assert pos == sorted(pos)


def test_abschnitt_schneidet_die_neuesten_ab():
    mails = [post_mail(id=f"m{i:02}", datum=f"2026-11-{9 + i // 10:02}T{i % 10:02}:00:00+00:00", betreff=f"B{i:02}")
             for i in range(52)]
    text = _zu(mails[::-1])
    assert "B00" in text and "B49" in text and "B50" not in text and "B51" not in text


def test_stichtag_nur_iso_ascii(tmp_path):
    for wert in ("2026-W45-1", "20261108", "٢٠٢٦-١١-٠٨"):
        (tmp_path / ".env").write_text(f"POST_AUFRAEUMEN_AB={wert}\n", encoding="utf-8")
        assert stichtag_lesen(tmp_path / ".env") is None
