"""Sprech-Dienst: Eingangspruefung ohne Torch (das Modell laeuft nur in der Chatterbox-Umgebung)."""
import pytest

from spaces.marketing.claw import ton_dienst as td


def test_standardstimme_ohne_probe():
    assert td.anfrage_pruefen({"text": " Hallo ", "sprache": "de"}) == ("Hallo", "de", None)
    assert td.anfrage_pruefen({"text": "Hi", "stimme": "standard"})[2] is None


def test_eigene_stimme_aus_dem_ordner(tmp_path, monkeypatch):
    (tmp_path / "anna.wav").write_bytes(b"RIFF....WAVE")
    monkeypatch.setenv("TON_STIMMEN_ORDNER", str(tmp_path))
    assert td.anfrage_pruefen({"text": "Hallo", "stimme": "anna"})[2] == tmp_path / "anna.wav"


@pytest.mark.parametrize("nutzlast, grund", [
    ({"text": ""}, "text"),
    ({"text": "x" * 1001}, "text"),
    ({"text": "Hallo", "sprache": "xx"}, "sprache"),
    ({"text": "Hallo", "stimme": "../boese"}, "stimme"),
    ({"text": "Hallo", "stimme": "fehlt"}, "Stimmprobe"),
    ([1, 2], "Objekt"),
])
def test_fehler(nutzlast, grund, monkeypatch, tmp_path):
    monkeypatch.setenv("TON_STIMMEN_ORDNER", str(tmp_path))
    with pytest.raises(td.Anfragefehler, match=grund):
        td.anfrage_pruefen(nutzlast)


def test_dienst_hoert_nur_lokal():
    import inspect
    assert '("127.0.0.1", PORT)' in inspect.getsource(td.main)
