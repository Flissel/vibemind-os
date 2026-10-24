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


def test_vorladen_nur_auf_wunsch(monkeypatch):
    """24.10.2026: Standard ist Laden bei Bedarf (spart ~6 GB); TON_VORLADEN=1 laedt beim Start."""
    monkeypatch.delenv("TON_VORLADEN", raising=False)
    monkeypatch.setattr(td, "_modell_laden", lambda: pytest.fail("darf nicht laden"))
    assert td.vorladen_starten() is None


def test_vorladen_laedt_im_hintergrund(monkeypatch):
    geladen = []
    monkeypatch.setenv("TON_VORLADEN", "1")
    monkeypatch.setattr(td, "_modell_laden", lambda: geladen.append(1))
    t = td.vorladen_starten()
    t.join(2)
    assert geladen == [1] and t.daemon


def test_vorladen_fehler_kippt_nichts(monkeypatch):
    monkeypatch.setenv("TON_VORLADEN", "1")

    def kaputt():
        raise RuntimeError("CUDA weg")
    monkeypatch.setattr(td, "_modell_laden", kaputt)
    td.vorladen_starten().join(2)       # keine Ausnahme nach aussen


# --- Ausspracheverzeichnis (18.10.2026: "Sales"/"Bot" deutsch ausgesprochen) ------------

VERZ = {"Sales": "Sejls", "Bot": "Bott", "Bots": "Botts", "LinkedIn": "Linkt-inn"}


@pytest.mark.parametrize("text, erwartet", [
    ("Mein Sales-Bot macht Vorschläge.", "Mein Sejls-Bott macht Vorschläge."),
    ("Ein guter Bot, zwei Bots!", "Ein guter Bott, zwei Botts!"),
    ("Angebot und Botschaft bleiben.", "Angebot und Botschaft bleiben."),
    ("sales klein bleibt", "sales klein bleibt"),
    ("Auf LinkedIn posten", "Auf Linkt-inn posten"),
])
def test_aussprache_anwenden(text, erwartet):
    assert td.aussprache_anwenden(text, VERZ) == erwartet


def test_laengerer_eintrag_zuerst():
    assert td.aussprache_anwenden("Sales-Bot", {"Sales": "X", "Sales-Bot": "Sejls-Bott"}) == "Sejls-Bott"


def test_aussprache_laden(tmp_path, monkeypatch):
    monkeypatch.delenv("TON_AUSSPRACHE", raising=False)
    monkeypatch.setenv("TON_STIMMEN_ORDNER", str(tmp_path))
    assert td.aussprache_laden() == {}
    (tmp_path / "aussprache.json").write_text('{"Sales": "Sejls", "": "x", "Bot": 5}', encoding="utf-8")
    assert td.aussprache_laden() == {"Sales": "Sejls"}
    (tmp_path / "aussprache.json").write_text("kaputt", encoding="utf-8")
    assert td.aussprache_laden() == {}


# --- Regler (18.10.2026, Betonung) -------------------------------------------------

def test_regler_pruefen():
    assert td.regler_pruefen(None) == {}
    assert td.regler_pruefen({"ausdruck": 0.3, "taktung": 0.4, "temperatur": 0.7}) == {
        "exaggeration": 0.3, "cfg_weight": 0.4, "temperature": 0.7}


@pytest.mark.parametrize("werte, grund", [
    ({"ausdruck": 5}, "ausdruck"), ({"taktung": -1}, "taktung"), ({"laut": 1}, "unbekannt"),
    ({"ausdruck": True}, "ausdruck"), ([0.3], "Objekt")])
def test_regler_fehler(werte, grund):
    with pytest.raises(td.Anfragefehler, match=grund):
        td.regler_pruefen(werte)


def test_einstellungen_je_stimme(tmp_path, monkeypatch):
    monkeypatch.setenv("TON_STIMMEN_ORDNER", str(tmp_path))
    assert td.einstellungen_laden("eigene-b") == {}
    (tmp_path / "einstellungen.json").write_text(
        '{"eigene-b": {"ausdruck": 0.3, "taktung": 0.35}, "kaputt": {"ausdruck": 9}}', encoding="utf-8")
    assert td.einstellungen_laden("eigene-b") == {"exaggeration": 0.3, "cfg_weight": 0.35}
    assert td.einstellungen_laden("standard") == {}
    assert td.einstellungen_laden("kaputt") == {}


# --- Standardstimme (User-Wahl 19.10.2026: eigene-c) -------------------------------

def test_standard_nimmt_gewaehlte_probe(tmp_path, monkeypatch):
    (tmp_path / "eigene-c.wav").write_bytes(b"RIFF....WAVE")
    monkeypatch.setenv("TON_STIMMEN_ORDNER", str(tmp_path))
    monkeypatch.setenv("TON_STANDARD_STIMME", "eigene-c")
    assert td.anfrage_pruefen({"text": "Hallo"})[2] == tmp_path / "eigene-c.wav"
    assert td.anfrage_pruefen({"text": "Hallo", "stimme": "standard"})[2] == tmp_path / "eigene-c.wav"
    assert td.anfrage_pruefen({"text": "Hallo", "stimme": "eingebaut"})[2] is None
    assert td.voiceover_pruefen(_vo())["probe"] == tmp_path / "eigene-c.wav"     # auch fuer Laura


def test_standard_ohne_datei_faellt_auf_eingebaut(tmp_path, monkeypatch):
    monkeypatch.setenv("TON_STIMMEN_ORDNER", str(tmp_path))
    monkeypatch.setenv("TON_STANDARD_STIMME", "fehlt")
    assert td.anfrage_pruefen({"text": "Hallo"})[2] is None


def test_tailnet_adresse_wird_nachgebunden_wenn_tailscale_spaeter_kommt():
    """22.10.2026: Nach dem PC-Neustart war Tailscale noch nicht da - bind auf 100.84.x warf
    WinError 10049 und der ganze Dienst startete nicht. Die Adresse wird jetzt nachgebunden."""
    versuche, pausen, gestartet = [], [], []

    class Server:
        def serve_forever(self):
            gestartet.append(True)

    def erzeugen(adresse):
        versuche.append(adresse)
        if len(versuche) < 3:
            raise OSError(10049, "Die angeforderte Adresse ist in diesem Kontext ungueltig")
        return Server()

    td.nachbinden("100.84.127.84", erzeugen, warten=pausen.append, takt_s=7)
    assert versuche == ["100.84.127.84"] * 3
    assert pausen == [7, 7]
    assert gestartet == [True]


def test_nachbinden_gibt_nach_der_frist_auf(caplog):
    def erzeugen(adresse):
        raise OSError(10049, "weg")

    pausen = []
    td.nachbinden("100.84.127.84", erzeugen, warten=pausen.append, takt_s=10, frist_s=30)
    assert pausen == [10, 10, 10]
    assert "100.84.127.84" in caplog.text


# --- Laden bei Bedarf, Freigeben nach Leerlauf (24.10.2026) -----------------

class _FalschesModell:
    geladen = []

    @classmethod
    def from_local(cls, ordner, device):
        cls.geladen.append(("lokal", str(ordner), device))
        return cls()

    @classmethod
    def from_pretrained(cls, device):
        cls.geladen.append(("hub", device))
        return cls()


@pytest.fixture
def falsches_modell(monkeypatch):
    import sys
    import types
    _FalschesModell.geladen = []
    mod = types.ModuleType("chatterbox.mtl_tts")
    mod.ChatterboxMultilingualTTS = _FalschesModell
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", mod)
    monkeypatch.setattr(td, "_modell", None)
    monkeypatch.setattr(td, "_freigeben_geraet", lambda: None)
    return _FalschesModell


def test_modell_aus_lokalem_ordner(falsches_modell, monkeypatch, tmp_path):
    monkeypatch.setenv("TON_MODELL_ORDNER", str(tmp_path))
    monkeypatch.setenv("TON_GERAET", "cuda")
    td._modell_laden()
    assert falsches_modell.geladen == [("lokal", str(tmp_path), "cuda")]


def test_ohne_ordner_vom_hub(falsches_modell, monkeypatch):
    monkeypatch.delenv("TON_MODELL_ORDNER", raising=False)
    monkeypatch.setenv("TON_GERAET", "cpu")
    td._modell_laden()
    assert falsches_modell.geladen == [("hub", "cpu")]


def test_leerlauf_gibt_modell_frei(falsches_modell, monkeypatch):
    monkeypatch.delenv("TON_MODELL_ORDNER", raising=False)
    monkeypatch.setenv("TON_LEERLAUF_S", "600")
    td._modell_laden()
    td._genutzt(jetzt=1000.0)
    assert td.entladen_wenn_ruhig(jetzt=1000.0 + 599) is False and td._modell is not None
    assert td.entladen_wenn_ruhig(jetzt=1000.0 + 601) is True and td._modell is None
    assert td.entladen_wenn_ruhig(jetzt=1000.0 + 9999) is False      # nichts mehr zu tun


def test_nicht_freigeben_waehrend_gesprochen_wird(falsches_modell, monkeypatch):
    monkeypatch.setenv("TON_LEERLAUF_S", "1")
    td._modell_laden()
    td._genutzt(jetzt=0.0)
    with td._sprech_lock:
        assert td.entladen_wenn_ruhig(jetzt=1e9) is False
    assert td._modell is not None


def test_leerlauf_null_heisst_nie_freigeben(falsches_modell, monkeypatch):
    monkeypatch.setenv("TON_LEERLAUF_S", "0")
    td._modell_laden()
    td._genutzt(jetzt=0.0)
    assert td.entladen_wenn_ruhig(jetzt=1e9) is False and td._modell is not None


def test_nach_freigabe_laedt_naechste_anfrage_neu(falsches_modell, monkeypatch):
    monkeypatch.setenv("TON_LEERLAUF_S", "10")
    td._modell_laden()
    td._genutzt(jetzt=0.0)
    td.entladen_wenn_ruhig(jetzt=100.0)
    td._modell_laden()
    assert len(falsches_modell.geladen) == 2
