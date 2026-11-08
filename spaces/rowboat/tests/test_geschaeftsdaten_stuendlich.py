"""Tests fuer den stuendlichen Lauf (Post und Termine) und die gemeinsame Sperre."""
from __future__ import annotations

import contextlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "geschaeftsdaten"))

from geschaeftsdaten_fake import kontakt, post_mail  # noqa: E402
from test_geschaeftsdaten_lauf import (A, B, LAEDEN, _alle, _id, _lauf, _quelle, _termin, _termine,  # noqa: E402
                                      _wissen)

import lauf as lauf_mod  # noqa: E402
import stuendlich  # noqa: E402
from sperre import sperre  # noqa: E402

JETZT = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
STICHTAG = date(2026, 10, 1)
TERMINE = "companys/VibeMind/Termine"


@pytest.fixture(autouse=True)
def _sperre_im_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("GESCHAEFTSDATEN_SPERRE", str(tmp_path / "sperre" / "lauf.lock"))


def _stunde(quelle, wissen, **kw):
    werte = dict(jetzt=JETZT, stichtag=None, trocken=False, gestoert=frozenset())
    werte.update(kw)
    return stuendlich.stunde(quelle, LAEDEN, wissen, **werte)


def test_stuendlich_legt_termin_an_loescht_aber_nie(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.ereignisse_["sales"] += [_termin("alt", "2026-10-27", "09:00"), _termin("u2", "2026-10-28", "11:00")]
    _lauf(q, wissen)
    assert _termine(wissen) == ["2026-10-27 0900 Anna Alpha.md", "2026-10-28 1100 Anna Alpha.md", "_overview.md"]
    # "alt" verschwindet aus der Quelle (Nacht: Archiv), u2 wird abgesagt (Nacht: Loeschung), u1 ist neu.
    q.ereignisse_["sales"] = [e for e in q.ereignisse_["sales"] if e.termin_uid != "alt"]
    q.ereignisse_["sales"] += [_termin("u2", "2026-10-28", "11:00", typ="termin_abgesagt", stunde=12),
                               _termin("u1", "2026-10-29", "15:00")]
    erg = _stunde(q, wissen)
    assert erg.exit_code == 0
    assert _termine(wissen) == ["2026-10-27 0900 Anna Alpha.md", "2026-10-28 1100 Anna Alpha.md",
                                "2026-10-29 1500 Anna Alpha.md", "_overview.md"]
    z = erg.je_firma["VibeMind"]
    assert z["entfernt"] == z["archiviert"] == z["verschoben"] == 0
    assert not (wissen / TERMINE / "_archiv").exists()


def test_stuendlich_neuer_pfad_wird_neu_geschrieben_alte_bleibt(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.ereignisse_["sales"].append(_termin("u1", "2026-10-28", "10:00"))
    _lauf(q, wissen)
    q.ereignisse_["sales"].append(_termin("u1", "2026-10-28", "14:30", stunde=12))
    erg = _stunde(q, wissen)
    assert erg.exit_code == 0 and erg.je_firma["VibeMind"]["verschoben"] == 0
    assert _termine(wissen) == ["2026-10-28 1000 Anna Alpha.md", "2026-10-28 1430 Anna Alpha.md", "_overview.md"]
    assert "**Uhrzeit:** 14:30" in (wissen / TERMINE / "2026-10-28 1430 Anna Alpha.md").read_text(encoding="utf-8")
    nacht = _lauf(q, wissen)  # die Nacht raeumt die alte Notiz weg
    assert nacht.exit_code == 0
    assert _termine(wissen) == ["2026-10-28 1430 Anna Alpha.md", "_overview.md"]


def test_stuendlich_zweimal_hintereinander_null_schreibungen(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    _steckbrief_da(wissen)
    q.ereignisse_["sales"].append(_termin("u1", "2026-10-28"))
    q.post_ = [post_mail()]
    _stunde(q, wissen, stichtag=STICHTAG)
    vorher = _alle(wissen)
    erg = _stunde(q, wissen, stichtag=STICHTAG)
    assert all(z["geschrieben"] == 0 for z in erg.je_firma.values())
    assert _alle(wissen) == vorher


def test_stuendlich_schreibt_post_notiz_nur_fuer_laden_sales(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.kontakte_["sales_ivan"] = [kontakt(id=B, name="Bert Beta", email_klein="bert@beta.de", bereiche=())]
    q.kontakte_["sales"][0] = kontakt(id=A, name="Anna Alpha", email_klein="anna@alpha.de", bereiche=("bav",))
    q.post_ = [post_mail(id="m1", absender_klein="anna@alpha.de", datum="2026-10-20T08:00:00+00:00"),
               post_mail(id="m2", absender_klein="bert@beta.de", datum="2026-10-21T08:00:00+00:00")]
    _steckbrief_da(wissen)  # verlinkt wird nur, was als Datei existiert
    erg = _stunde(q, wissen, stichtag=STICHTAG)
    assert erg.exit_code == 0
    text = (wissen / "Post" / "Zu beantworten.md").read_text(encoding="utf-8")
    assert "[[companys/VibeMind/Kontakte/bAV/Anna Alpha]]" in text
    assert text.count("Absender") == 2  # beide Mails stehen da ...
    assert "fin2gether" not in text and "Kontakte/_ohne" not in text  # ... aber ohne Steckbrief-Link
    assert (wissen / "Post" / "_overview.md").is_file()
    assert "parent: " not in (wissen / "Post" / "_overview.md").read_text(encoding="utf-8")  # keine Company.md


def test_stuendlich_company_md_gibt_der_uebersicht_einen_parent(tmp_path):
    wissen = _wissen(tmp_path)
    (wissen / "Company.md").write_text("# Company\n", encoding="utf-8")
    _stunde(_quelle(), wissen, stichtag=STICHTAG)
    assert 'parent: "[[Company]]"' in (wissen / "Post" / "_overview.md").read_text(encoding="utf-8")


def test_stuendlich_ohne_stichtag_wird_post_offen_nicht_gelesen(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.post_ = [post_mail()]
    _stunde(q, wissen, stichtag=None)
    assert "post_offen" not in q.aufrufe
    assert "Offen: 0" in (wissen / "Post" / "Zu beantworten.md").read_text(encoding="utf-8")


def test_stuendlich_liest_nur_was_er_braucht(tmp_path):
    q = _quelle()
    _stunde(q, _wissen(tmp_path), stichtag=STICHTAG, trocken=True)
    assert not {"versand", "newsletter_inhalte", "medien"} & set(q.aufrufe)


def test_stuendlich_db_fehler_schreibt_nichts(tmp_path):
    wissen = _wissen(tmp_path)
    erg = _stunde(_quelle(fehler=True), wissen, stichtag=STICHTAG)
    assert erg.exit_code == 2 and _alle(wissen) == {}


def test_stuendlich_db_fehler_bei_post_schreibt_nichts(tmp_path):
    wissen = _wissen(tmp_path)

    class PostFehler(type(_quelle())):
        def post_offen(self, stichtag):
            from quelle import DbFehler
            raise DbFehler("psql Exit 2")

    q = _quelle()
    q.__class__ = PostFehler
    q.ereignisse_["sales"].append(_termin("u1", "2026-10-28"))
    erg = _stunde(q, wissen, stichtag=STICHTAG)
    assert erg.exit_code == 2 and _alle(wissen) == {}


def test_stuendlich_gestoerte_marke_und_laden_ohne_sichten_werden_nicht_angefasst(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.ereignisse_["sales"].append(_termin("u1", "2026-10-28"))
    erg = _stunde(q, wissen, gestoert=frozenset({"vibemind"}))
    assert erg.exit_code == 1 and "Marke vibemind" in erg.uebersprungen
    assert _termine(wissen) == []
    q = _quelle(ohne_sichten=("sales",))
    erg = _stunde(q, wissen)
    assert erg.exit_code == 1 and not (wissen / "companys" / "VibeMind" / "Termine").exists()


def test_stuendlich_trocken_schreibt_nichts(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.ereignisse_["sales"].append(_termin("u1", "2026-10-28"))
    q.post_ = [post_mail()]
    erg = _stunde(q, wissen, stichtag=STICHTAG, trocken=True)
    assert erg.exit_code == 0 and _alle(wissen) == {}


def _umgebung(tmp_path, monkeypatch, *, stichtag: str | None = "2026-10-01") -> Path:
    wissen = _wissen(tmp_path)
    sales_claw = tmp_path / "sales-claw"
    sales_claw.mkdir()
    zeilen = ["MARKETING_MANDANT=vibemind"] + ([f"POST_AUFRAEUMEN_AB={stichtag}"] if stichtag else [])
    (sales_claw / ".env").write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    monkeypatch.setenv("GESCHAEFTSDATEN_WISSEN", str(wissen))
    monkeypatch.setenv("GESCHAEFTSDATEN_SALES_CLAW", str(sales_claw))
    monkeypatch.setenv("GESCHAEFTSDATEN_STAND", str(tmp_path / "zustand" / "stand.json"))
    return wissen


def test_stuendlich_main_schreibt_post_und_termine_ohne_stand_json(tmp_path, monkeypatch):
    wissen = _umgebung(tmp_path, monkeypatch)
    q = _quelle()
    q.ereignisse_["sales"].append(_termin("u1", "2026-10-28"))
    _steckbrief_da(wissen)
    q.post_ = [post_mail()]
    monkeypatch.setattr(stuendlich, "PsqlQuelle", lambda c: q)
    assert stuendlich.main(["--jetzt", "2026-10-26T12:00:00+00:00"]) == 0
    assert (wissen / "Post" / "Zu beantworten.md").is_file()
    assert _termine(wissen) == ["2026-10-28 1000 Anna Alpha.md", "_overview.md"]
    assert not (tmp_path / "zustand").exists()  # stand.json gehoert dem naechtlichen Lauf


def test_stuendlich_main_trocken(tmp_path, monkeypatch):
    wissen = _umgebung(tmp_path, monkeypatch)
    monkeypatch.setattr(stuendlich, "PsqlQuelle", lambda c: _quelle())
    assert stuendlich.main(["--trocken", "--jetzt", "2026-10-26T12:00:00"]) == 0
    assert _alle(wissen) == {}


def test_stuendlich_main_nimmt_die_sperre_zuerst(tmp_path, monkeypatch):
    @contextlib.contextmanager
    def belegt(pfad, *, warten_s):
        assert warten_s == 0
        yield False

    def nie(*a, **k):
        raise AssertionError("darf nicht angefasst werden")

    monkeypatch.setattr(stuendlich, "sperre", belegt)
    monkeypatch.setattr(stuendlich, "PsqlQuelle", nie)
    monkeypatch.setattr(stuendlich, "pruefen", nie)
    monkeypatch.setattr(stuendlich, "stichtag_lesen", nie)
    assert stuendlich.main([]) == 0


def test_lauf_main_ohne_sperre_exit_1_und_stand_fehler(tmp_path, monkeypatch):
    wissen = _umgebung(tmp_path, monkeypatch)
    gewartet: list[float] = []

    @contextlib.contextmanager
    def belegt(pfad, *, warten_s):
        gewartet.append(warten_s)
        yield False

    monkeypatch.setattr(lauf_mod, "sperre", belegt)
    monkeypatch.setattr(lauf_mod, "PsqlQuelle", lambda c: pytest.fail("Quelle darf nicht gebaut werden"))
    assert lauf_mod.main(["--heute", "2026-10-26"]) == 1
    assert gewartet == [600]
    daten = json.loads((tmp_path / "zustand" / "stand.json").read_text(encoding="utf-8"))
    assert daten["fehler"] == "Sperre" and daten["ergebnis"] == "fehler"
    assert _alle(wissen) == {}


def test_lauf_main_ohne_sperre_trocken_schreibt_keinen_stand(tmp_path, monkeypatch):
    _umgebung(tmp_path, monkeypatch)

    @contextlib.contextmanager
    def belegt(pfad, *, warten_s):
        yield False

    monkeypatch.setattr(lauf_mod, "sperre", belegt)
    assert lauf_mod.main(["--trocken"]) == 1
    assert not (tmp_path / "zustand").exists()


def test_stuendlich_setzt_bei_sperre_aus(tmp_path, monkeypatch):
    fcntl = pytest.importorskip("fcntl")
    lock = tmp_path / "lauf.lock"
    with open(lock, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        monkeypatch.setenv("GESCHAEFTSDATEN_SPERRE", str(lock))
        monkeypatch.setenv("GESCHAEFTSDATEN_WISSEN", str(tmp_path))
        assert stuendlich.main(["--trocken"]) == 0   # setzt aus, ohne die Quelle anzufassen


def test_sperre_zweiter_versuch_scheitert(tmp_path):
    pytest.importorskip("fcntl")
    with sperre(tmp_path / "l", warten_s=0) as a, sperre(tmp_path / "l", warten_s=0) as b:
        assert a is True and b is False
    with sperre(tmp_path / "l", warten_s=0) as c:  # nach dem Loslassen wieder frei
        assert c is True
    # Ruling 7: legt den Elternordner an und scheitert geschlossen, wenn die Datei nicht zu oeffnen ist
    with sperre(tmp_path / "neu" / "unter" / "l", warten_s=0) as a:
        assert a is True
    (tmp_path / "datei").write_text("x", encoding="utf-8")
    with sperre(tmp_path / "datei" / "l", warten_s=0) as b:  # Elternteil ist eine Datei
        assert b is False


def _steckbrief_da(wissen: Path) -> Path:
    pfad = wissen / "companys/VibeMind/Kontakte/bAV/Anna Alpha.md"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text("# Anna Alpha\n", encoding="utf-8")
    return pfad


def test_umzuege_in_der_stunde_loesen_den_nachtwaechter_nicht_aus(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    tage = [f"2026-10-{10 + i:02d}" for i in range(6)]
    q.ereignisse_["sales"] += [_termin(f"u{i}", t, "10:00") for i, t in enumerate(tage)]
    _lauf(q, wissen)
    q.ereignisse_["sales"] += [_termin(f"u{i}", t, "15:00", stunde=12) for i, t in enumerate(tage)]
    _stunde(q, wissen)
    assert len(_termine(wissen)) == 13  # alte und neue Kopien, dazu die Uebersicht
    neu = sorted([f"{t} 1500 Anna Alpha.md" for t in tage] + ["_overview.md"])
    for _ in range(2):
        erg = _lauf(q, wissen)
        assert erg.exit_code == 0
        assert not any("Massenarchivierung" in u for u in erg.uebersprungen)
        assert _termine(wissen) == neu  # schon nach der ersten Nacht sind die alten Kopien weg
    assert not (wissen / TERMINE / "_archiv").exists()


def test_echte_massenarchivierung_greift_trotz_duplikat_ausnahme(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    tage = [f"2026-10-{10 + i:02d}" for i in range(10)]
    q.ereignisse_["sales"] += [_termin(f"u{i}", t, "10:00") for i, t in enumerate(tage)]
    _lauf(q, wissen)
    q.ereignisse_["sales"] += [_termin("u0", tage[0], "15:00", stunde=12)]
    _stunde(q, wissen)
    q.ereignisse_["sales"] = q.ereignisse_["sales"][:1]  # alle Termine verschwinden aus der Quelle
    erg = _lauf(q, wissen)
    assert "Massenarchivierung VibeMind" in erg.uebersprungen
    assert erg.je_firma["VibeMind"]["archiviert"] == 0


def test_gleiche_uid_bei_zwei_kontakten_stuendlich_und_nachts(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.kontakte_["sales"].append(kontakt(id=_id(7), name="Doris Delta", bereiche=()))
    q.ereignisse_["sales"] += [_termin("gemeinsam", "2026-10-28", lead=A), _termin("gemeinsam", "2026-10-28", lead=_id(7))]
    _lauf(q, wissen)
    beide = ["2026-10-28 1000 Anna Alpha.md", "2026-10-28 1000 Doris Delta.md", "_overview.md"]
    assert _termine(wissen) == beide
    q.ereignisse_["sales"].append(_termin("gemeinsam", "2026-10-28", lead=A, typ="termin_abgesagt", stunde=12))
    _stunde(q, wissen)
    assert _termine(wissen) == beide  # stuendlich wird nie geloescht
    _lauf(q, wissen)
    assert _termine(wissen) == ["2026-10-28 1000 Doris Delta.md", "_overview.md"]


def test_gleiche_uid_bei_zwei_kontakten_stuendlich_neu(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.kontakte_["sales"].append(kontakt(id=_id(7), name="Doris Delta", bereiche=()))
    _lauf(q, wissen)  # legt die Steckbriefe an
    q.ereignisse_["sales"] += [_termin("gemeinsam", "2026-10-28", lead=A), _termin("gemeinsam", "2026-10-28", lead=_id(7))]
    _stunde(q, wissen)
    assert _termine(wissen) == ["2026-10-28 1000 Anna Alpha.md", "2026-10-28 1000 Doris Delta.md", "_overview.md"]


def test_stuendlich_pflegt_gitignore_vor_dem_schreiben(tmp_path):
    wissen = tmp_path / "knowledge"
    wissen.mkdir()
    erg = _stunde(_quelle(), wissen, stichtag=STICHTAG)
    text = (wissen / ".gitignore").read_text(encoding="utf-8")
    assert "companys/*/Termine/" in text and "Post/" in text
    assert erg.gitignore == "angelegt"


def test_stuendlich_trocken_legt_kein_gitignore_an(tmp_path):
    wissen = _wissen(tmp_path)
    _stunde(_quelle(), wissen, stichtag=STICHTAG, trocken=True)
    assert not (wissen / ".gitignore").exists()


def test_stuendlich_fremde_gitignore_bleibt_unberuehrt(tmp_path, caplog):
    wissen = _wissen(tmp_path)
    (wissen / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    with caplog.at_level("WARNING", logger="geschaeftsdaten"):
        erg = _stunde(_quelle(), wissen, stichtag=STICHTAG)
    assert erg.gitignore == "fremd"
    assert (wissen / ".gitignore").read_text(encoding="utf-8") == "node_modules/\n"
    assert "fremd" in caplog.text


def test_stuendlich_verlinkt_nur_vorhandene_steckbriefe(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.ereignisse_["sales"].append(_termin("u1", "2026-10-28"))
    q.post_ = [post_mail(id="m1", absender_klein="anna@alpha.de", datum="2026-10-20T08:00:00+00:00")]
    erg = _stunde(q, wissen, stichtag=STICHTAG)
    assert erg.exit_code == 0
    post = wissen / "Post" / "Zu beantworten.md"
    assert "[[companys/VibeMind/Kontakte" not in post.read_text(encoding="utf-8")
    assert _termine(wissen) == []  # ohne Steckbrief entsteht (noch) keine Termin-Notiz; sie folgt nach der Nacht
    _steckbrief_da(wissen)
    _stunde(q, wissen, stichtag=STICHTAG)
    assert "[[companys/VibeMind/Kontakte/bAV/Anna Alpha]]" in post.read_text(encoding="utf-8")
    notiz = (wissen / TERMINE / "2026-10-28 1000 Anna Alpha.md").read_text(encoding="utf-8")
    assert 'kontakt: "[[companys/VibeMind/Kontakte/bAV/Anna Alpha]]"' in notiz


def test_stuendlich_ein_firmenfehler_haelt_den_rest_nicht_auf(tmp_path, monkeypatch, caplog):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.post_ = [post_mail()]
    echt = stuendlich.planen
    aufrufe = []

    def planen(*a, **k):
        aufrufe.append(1)
        if len(aufrufe) == 1:
            raise ValueError("Anna Alpha kaputt")
        return echt(*a, **k)

    monkeypatch.setattr(stuendlich, "planen", planen)
    with caplog.at_level("WARNING", logger="geschaeftsdaten"):
        erg = _stunde(q, wissen, stichtag=STICHTAG)
    assert erg.exit_code == 1 and len(erg.uebersprungen) == 1
    assert (wissen / "Post" / "Zu beantworten.md").is_file()
    assert "ValueError" in caplog.text and "Anna Alpha" not in caplog.text
