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


def test_dienst_hoert_lokal_und_nur_auf_genannten_adressen(monkeypatch):
    """Lokal immer; weitere Adressen nur aus TON_DIENST_ADRESSEN (E5: Tailscale-Adresse fuer die VM)."""
    monkeypatch.delenv("TON_DIENST_ADRESSEN", raising=False)
    assert td.lausch_adressen() == ["127.0.0.1"]
    monkeypatch.setenv("TON_DIENST_ADRESSEN", " 100.84.127.84 , ,127.0.0.1")
    assert td.lausch_adressen() == ["127.0.0.1", "100.84.127.84"]
    monkeypatch.setenv("TON_DIENST_ADRESSEN", "0.0.0.0")
    with pytest.raises(ValueError, match="0.0.0.0"):
        td.lausch_adressen()


@pytest.mark.parametrize("absender, erlaubt, ok", [
    ("127.0.0.1", "", True),
    ("::1", "", True),
    ("100.67.177.45", "", False),
    ("100.67.177.45", "100.67.177.45", True),
    ("100.67.177.46", "100.67.177.45", False),
])
def test_absender_pruefung(absender, erlaubt, ok, monkeypatch):
    monkeypatch.setenv("TON_DIENST_ERLAUBT", erlaubt)
    assert td.absender_erlaubt(absender) is ok


# --- /voiceover: Lauras Sidecar-Vertrag (Video-Claw, 17.10.2026) ---------------

def _vo(**extra):
    return {"text": "Hallo Welt", "duration_frames": 90, "fps_num": 30, "fps_den": 1,
            "sample_rate": 48000, **extra}


def test_voiceover_vertrag():
    p = td.voiceover_pruefen(_vo(language="de", fit_to_slot=True))
    assert p == {"text": "Hallo Welt", "sprache": "de", "probe": None, "sample_rate": 48000,
                 "fit": True, "sekunden": 3.0}


def test_voiceover_vorgaben():
    p = td.voiceover_pruefen(_vo())
    assert p["sprache"] == "de" and p["fit"] is False and p["probe"] is None


def test_voiceover_stimme_als_name(tmp_path, monkeypatch):
    (tmp_path / "felix.wav").write_bytes(b"RIFF....WAVE")
    monkeypatch.setenv("TON_STIMMEN_ORDNER", str(tmp_path))
    assert td.voiceover_pruefen(_vo(voice_id="felix"))["probe"] == tmp_path / "felix.wav"


@pytest.mark.parametrize("aenderung, grund", [
    ({"duration_frames": 0}, "duration_frames"),
    ({"duration_frames": 10**9}, "hoechstens"),
    ({"duration_frames": 9001}, "hoechstens"),          # 300,03 s bei 30 fps
    ({"fps_num": True}, "fps_num"),
    ({"fps_den": "1"}, "fps_den"),
    ({"sample_rate": 4000}, "sample_rate"),
    ({"text": " "}, "text"),
    ({"language": "xx"}, "sprache"),
    ({"voice_id": "C:/boese.wav"}, "stimme"),
])
def test_voiceover_fehler(aenderung, grund, tmp_path, monkeypatch):
    monkeypatch.setenv("TON_STIMMEN_ORDNER", str(tmp_path))
    with pytest.raises(td.Anfragefehler, match=grund):
        td.voiceover_pruefen(_vo(**aenderung))


def test_anpassen_ffmpeg_argumente():
    a = td.anpassen_argumente("roh.wav", "aus.wav", sample_rate=48000, fit=True, sekunden=3.0)
    assert a[a.index("-ac") + 1] == "1" and a[a.index("-ar") + 1] == "48000"
    assert a[a.index("-af") + 1] == "apad" and a[a.index("-t") + 1] == "3.000000"
    assert a[a.index("-c:a") + 1] == "pcm_s16le" and a[-1] == "aus.wav"
    b = td.anpassen_argumente("roh.wav", "aus.wav", sample_rate=16000, fit=False, sekunden=3.0)
    assert "-t" not in b and "apad" not in b


def test_anpassen_echt(tmp_path):
    """Echtes ffmpeg: 1 s Ton -> auf 2,5 s aufgefuellt, mono, 16 kHz, 16 Bit."""
    import shutil
    import subprocess
    import wave
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg fehlt")
    roh = tmp_path / "roh.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-ac", "2",
                    "-ar", "24000", str(roh)], check=True)
    daten = td.anpassen(roh.read_bytes(), sample_rate=16000, fit=True, sekunden=2.5)
    import io
    with wave.open(io.BytesIO(daten)) as w:
        assert w.getnchannels() == 1 and w.getframerate() == 16000 and w.getsampwidth() == 2
        assert abs(w.getnframes() / 16000 - 2.5) < 0.01


def test_vorladen_laedt_im_hintergrund(monkeypatch):
    geladen = []
    monkeypatch.delenv("TON_VORLADEN", raising=False)
    monkeypatch.setattr(td, "_modell_laden", lambda: geladen.append(1))
    t = td.vorladen_starten()
    t.join(2)
    assert geladen == [1] and t.daemon


def test_vorladen_abschaltbar(monkeypatch):
    monkeypatch.setenv("TON_VORLADEN", "0")
    monkeypatch.setattr(td, "_modell_laden", lambda: pytest.fail("darf nicht laden"))
    assert td.vorladen_starten() is None


def test_vorladen_fehler_kippt_nichts(monkeypatch):
    monkeypatch.delenv("TON_VORLADEN", raising=False)

    def kaputt():
        raise RuntimeError("CUDA weg")
    monkeypatch.setattr(td, "_modell_laden", kaputt)
    td.vorladen_starten().join(2)       # keine Ausnahme nach aussen
