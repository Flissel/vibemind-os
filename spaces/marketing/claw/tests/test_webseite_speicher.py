"""Zwischenspeicher der gemerkten Webseite (Spec sales-claw 2026-10-09-marke-exakt-logo-wissen §2)."""
import os

from spaces.marketing.claw import webseite_speicher as sp
from spaces.marketing.claw.webseite import Fund, Seite

URL = "https://r.example/"
FUND = Fund(seiten=[Seite(url=URL, text="Text", ueberschriften=["H"])], farben=["#B45309"],
            schriften=["Playfair Display"], logos=["https://r.example/logo.png"], hinweise=["alt"])


def test_frisch_abgelaufen_andere_url_andere_firma(tmp_path):
    sp.ablegen(str(tmp_path), "radhaus", URL, FUND, 1000.0)
    f = sp.laden(str(tmp_path), "radhaus", URL, 1000.0 + 23 * 3600)
    assert f.seiten[0].text == "Text" and f.seiten[0].ueberschriften == ["H"] and f.farben == ["#B45309"]
    assert f.logos == ["https://r.example/logo.png"] and f.hinweise == []
    assert sp.laden(str(tmp_path), "radhaus", URL, 1000.0 + 24 * 3600) is None
    assert sp.laden(str(tmp_path), "radhaus", "https://andere.example/", 1001.0) is None
    assert sp.laden(str(tmp_path), "fin2gether", URL, 1001.0) is None


def test_ohne_seiten_wird_nichts_gemerkt(tmp_path):
    sp.ablegen(str(tmp_path), "radhaus", URL, Fund(hinweise=["weg"]), 1000.0)
    assert sp.laden(str(tmp_path), "radhaus", URL, 1000.0) is None


def test_kaputte_datei_und_fremde_namen(tmp_path):
    (tmp_path / "webseiten").mkdir()
    (tmp_path / "webseiten" / "radhaus.json").write_text("{kaputt", encoding="utf-8")
    assert sp.laden(str(tmp_path), "radhaus", URL, 1.0) is None
    sp.ablegen(str(tmp_path), "../boese", URL, FUND, 1.0)
    assert sp.laden(str(tmp_path), "../boese", URL, 1.0) is None
    assert sorted(p.name for p in (tmp_path / "webseiten").iterdir()) == ["radhaus.json"]


def test_ordner_aus_der_umgebung(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_ARBEITER_ORDNER", str(tmp_path))
    assert sp.ordner() == str(tmp_path)
    monkeypatch.delenv("MARKETING_ARBEITER_ORDNER")
    assert sp.ordner().endswith(os.path.join(".vibemind", "marketing-arbeiter"))