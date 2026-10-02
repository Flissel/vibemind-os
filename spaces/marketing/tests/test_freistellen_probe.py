"""Argumentpruefung der Freistell-Probe, ohne ComfyUI."""
from PIL import Image

from spaces.marketing.scripts import freistellen_probe as probe


def test_ohne_argumente_gibt_2(capsys):
    assert probe.main([]) == 2
    assert "Aufruf" in capsys.readouterr().err


def test_fehlendes_bild_gibt_2(tmp_path, capsys):
    assert probe.main([str(tmp_path / "nein.jpg"), str(tmp_path / "o.png")]) == 2
    assert "nicht gefunden" in capsys.readouterr().err


def test_ausgabe_muss_png_sein(tmp_path, capsys):
    q = tmp_path / "a.png"
    Image.new("RGB", (8, 8)).save(q)
    assert probe.main([str(q), str(tmp_path / "o.jpg")]) == 2
    assert ".png" in capsys.readouterr().err


def test_unlesbares_bild_gibt_2(tmp_path, capsys):
    q = tmp_path / "a.jpg"
    q.write_bytes(b"kein bild")
    assert probe.main([str(q), str(tmp_path / "o.png")]) == 2
    assert "nicht lesbar" in capsys.readouterr().err
