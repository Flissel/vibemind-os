"""ComfyUI-Client ohne echten Dienst: ein gefaelschter HTTP-Weg."""
import json

import pytest

from spaces.marketing.claw import bild_comfy


class FalschesHttp:
    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.anfragen = []

    def __call__(self, methode, pfad, daten=None, zeitlimit=30):
        self.anfragen.append((methode, pfad, daten))
        return self.antworten.pop(0)


PNG = b"\x89PNG\r\n\x1a\nrest"


def test_erzeugen_setzt_prompt_masse_seed_und_liefert_png(monkeypatch):
    http = FalschesHttp([
        (200, json.dumps({"prompt_id": "p1"}).encode()),
        (200, json.dumps({}).encode()),                                   # noch nicht fertig
        (200, json.dumps({"p1": {"outputs": {"9": {"images": [
            {"filename": "a.png", "subfolder": "", "type": "output"}]}}}}).encode()),
        (200, PNG),
    ])
    monkeypatch.setattr(bild_comfy, "_http", http)
    monkeypatch.setattr(bild_comfy, "_SCHLAF", lambda s: None)
    assert bild_comfy.erzeugen("a teal network", 1104, 560, 42) == PNG
    ablauf = http.anfragen[0][2]["prompt"]
    assert ablauf["4"]["inputs"]["text"] == "a teal network"
    assert (ablauf["6"]["inputs"]["width"], ablauf["6"]["inputs"]["height"]) == (1104, 560)
    assert ablauf["7"]["inputs"]["seed"] == 42
    assert http.anfragen[3][1].startswith("/view?filename=a.png")


def test_masse_muessen_vielfache_von_16_sein():
    with pytest.raises(bild_comfy.ComfyFehler):
        bild_comfy.erzeugen("x", 1000, 512, 1)


def test_fehler_in_der_ausfuehrung_wird_comfyfehler(monkeypatch):
    http = FalschesHttp([
        (200, json.dumps({"prompt_id": "p1"}).encode()),
        (200, json.dumps({"p1": {"status": {"status_str": "error"}, "outputs": {}}}).encode()),
    ])
    monkeypatch.setattr(bild_comfy, "_http", http)
    monkeypatch.setattr(bild_comfy, "_SCHLAF", lambda s: None)
    with pytest.raises(bild_comfy.ComfyFehler, match="fehlgeschlagen"):
        bild_comfy.erzeugen("x", 512, 512, 1)


def test_zeitlimit(monkeypatch):
    http = FalschesHttp([(200, b'{"prompt_id": "p1"}')] + [(200, b"{}")] * 50)
    monkeypatch.setattr(bild_comfy, "_http", http)
    monkeypatch.setattr(bild_comfy, "_SCHLAF", lambda s: None)
    with pytest.raises(bild_comfy.ComfyFehler, match="Zeitlimit"):
        bild_comfy.erzeugen("x", 512, 512, 1, zeitlimit_s=4)


def test_freigeben_und_laeuft(monkeypatch):
    http = FalschesHttp([(200, b""), (200, b"{}")])
    monkeypatch.setattr(bild_comfy, "_http", http)
    bild_comfy.freigeben()
    assert http.anfragen[0][:2] == ("POST", "/free")
    assert http.anfragen[0][2] == {"unload_models": True, "free_memory": True}
    assert bild_comfy.laeuft() is True
    monkeypatch.setattr(bild_comfy, "_http", lambda *a, **k: (_ for _ in ()).throw(OSError("weg")))
    assert bild_comfy.laeuft() is False


def test_ueberarbeiten_laedt_hoch_und_setzt_denoise(monkeypatch):
    hochgeladen = []
    monkeypatch.setattr(bild_comfy, "_hochladen", lambda name, daten: hochgeladen.append((name, daten)) or name)
    http = FalschesHttp([
        (200, json.dumps({"prompt_id": "p2"}).encode()),
        (200, json.dumps({"p2": {"outputs": {"9": {"images": [{"filename": "b.png", "subfolder": "", "type": "output"}]}}}}).encode()),
        (200, PNG),
    ])
    monkeypatch.setattr(bild_comfy, "_http", http)
    monkeypatch.setattr(bild_comfy, "_SCHLAF", lambda s: None)
    assert bild_comfy.ueberarbeiten("warm skyline", b"\xff\xd8\xffJ", 1040, 592, 9, 55) == PNG
    ablauf = http.anfragen[0][2]["prompt"]
    assert ablauf["10"]["inputs"]["image"] == hochgeladen[0][0] and hochgeladen[0][1] == b"\xff\xd8\xffJ"
    assert (ablauf["11"]["inputs"]["width"], ablauf["11"]["inputs"]["height"]) == (1040, 592)
    assert ablauf["7"]["inputs"]["denoise"] == 0.55 and ablauf["7"]["inputs"]["steps"] == 8
    assert ablauf["7"]["inputs"]["seed"] == 9 and ablauf["4"]["inputs"]["text"] == "warm skyline"


def test_ueberarbeiten_denoise_grenzen(monkeypatch):
    monkeypatch.setattr(bild_comfy, "_hochladen", lambda name, daten: name)
    gesehen = []
    monkeypatch.setattr(bild_comfy, "_ausfuehren", lambda ablauf, z: gesehen.append(ablauf["7"]["inputs"]["denoise"]) or PNG)
    bild_comfy.ueberarbeiten("x", b"j", 512, 512, 1, 0)
    bild_comfy.ueberarbeiten("x", b"j", 512, 512, 1, 100)
    assert gesehen == [bild_comfy.MIN_DENOISE, 1.0]
    with pytest.raises(bild_comfy.ComfyFehler):
        bild_comfy.ueberarbeiten("x", b"j", 500, 512, 1, 50)
    with pytest.raises(bild_comfy.ComfyFehler):
        bild_comfy.ueberarbeiten("x", b"", 512, 512, 1, 50)
