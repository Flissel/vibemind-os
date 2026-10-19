"""Paket C2: Ergebnisse des Agenten passen das Markenlogo mit den echten Dateimassen in seinen Kasten ein - wie beim
Anlegen aus einer Vorlage (pult._vorlage_fuellen). logo_einsetzen setzt nur den Kasten der Vorlagen."""
import copy

from PIL import Image

from spaces.marketing.api import chat

DOK = {
    "root": {"type": "EmailLayout", "data": {"childrenIds": ["marke_logo"],
                                             "rollen": {"marke_logo/data/props/url": "logo"}}},
    "marke_logo": {"type": "Image", "data": {"style": {}, "props": {
        "url": "medien:logo-x-quadrat.png", "alt": "Logo", "width": 132, "height": 44, "grafik": True}}},
}


def _ablage(tmp_path, monkeypatch, groesse=(300, 300)):
    Image.new("RGB", groesse, "white").save(tmp_path / "logo-x-quadrat.png")
    monkeypatch.setattr(chat, "_erzeugt_ordner", lambda: str(tmp_path))
    monkeypatch.delenv("MARKETING_MEDIEN_ORDNER", raising=False)
    monkeypatch.delenv("MARKETING_BILD_ORDNER", raising=False)


def test_quadratisches_logo_wird_eingepasst(tmp_path, monkeypatch):
    _ablage(tmp_path, monkeypatch)
    p = chat._logo_einpassen(DOK)["marke_logo"]["data"]["props"]
    assert (p["width"], p["height"]) == (44, 44)
    assert DOK["marke_logo"]["data"]["props"]["width"] == 132   # Original unberuehrt


def test_zweiter_lauf_aendert_nichts(tmp_path, monkeypatch):
    _ablage(tmp_path, monkeypatch, (600, 200))
    einmal = chat._logo_einpassen(DOK)
    assert chat._logo_einpassen(einmal) == einmal


def test_ohne_datei_bleibt_alles(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "_erzeugt_ordner", lambda: str(tmp_path))
    assert chat._logo_einpassen(DOK) == DOK


def test_ohne_logo_block_bleibt_alles(tmp_path, monkeypatch):
    _ablage(tmp_path, monkeypatch)
    dok = copy.deepcopy(DOK)
    dok["marke_logo"]["data"]["props"]["url"] = "https://fremd.example/logo.png"
    assert chat._logo_einpassen(dok) == dok
    assert chat._logo_einpassen({"root": {"data": {"childrenIds": []}}}) == {"root": {"data": {"childrenIds": []}}}


def test_rechnen_und_pruefen_passt_ein(tmp_path, monkeypatch):
    _ablage(tmp_path, monkeypatch)
    monkeypatch.setattr(chat, "_bloecke_fehler", lambda b: None)
    monkeypatch.setattr(chat, "gestaltungen_rechnen", lambda b: (b, []))   # keine Flaechen, keine Bildablage
    bloecke, _, fehler = chat._rechnen_und_pruefen(copy.deepcopy(DOK))
    assert fehler is None
    assert bloecke["marke_logo"]["data"]["props"]["width"] == 44
