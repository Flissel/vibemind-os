"""Sprech-Dienst: Eingangspruefung ohne Torch (das Modell laeuft nur in der Chatterbox-Umgebung)."""
import pytest

from spaces.marketing.claw import ton_dienst as td


def test_elevenlabs_aus_ohne_schluessel(monkeypatch):
    """28.10.2026: vorbereitet, aber ohne Schluessel immer Chatterbox."""
    monkeypatch.setenv("TON_ENGINE", "elevenlabs")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert td.engine() == "chatterbox"
    monkeypatch.setenv("ELEVENLABS_API_KEY", "geheim")
    assert td.engine() == "elevenlabs"


def test_elevenlabs_spricht_wav_und_verraet_den_schluessel_nicht(monkeypatch):
    import io
    import wave
    monkeypatch.setenv("ELEVENLABS_API_KEY", "geheim-123")
    monkeypatch.setenv("TON_ELEVENLABS_STIMMEN", '{"standard": "Abcdefgh12345678"}')
    monkeypatch.setattr(td, "aussprache_laden", lambda *a: {})
    gesehen = {}

    class Antwort(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def oeffnen(anfrage, timeout):
        gesehen["url"] = anfrage.full_url
        gesehen["kopf"] = dict(anfrage.header_items())
        return Antwort(b"\x00\x01" * 100)

    wav = td.sprechen_elevenlabs("Hallo", "de", "standard", oeffnen=oeffnen)
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getnchannels() == 1 and w.getframerate() == 44100 and w.getnframes() == 100
    assert "Abcdefgh12345678" in gesehen["url"] and "pcm_44100" in gesehen["url"]
    assert gesehen["kopf"]["Xi-api-key"] == "geheim-123"


def test_elevenlabs_ohne_stimme_und_rueckfall(monkeypatch):
    monkeypatch.setenv("TON_ENGINE", "elevenlabs")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "geheim")
    monkeypatch.delenv("TON_ELEVENLABS_STIMMEN", raising=False)
    with pytest.raises(td.ElevenLabsFehler, match="Stimme"):
        td.elevenlabs_stimme("standard")
    monkeypatch.setattr(td, "_sprechen_chatterbox", lambda *a, **k: b"CHATTERBOX")
    assert td.sprechen("Hallo", "de", None) == b"CHATTERBOX"
    monkeypatch.setenv("TON_ELEVENLABS_RUECKFALL", "0")
    with pytest.raises(td.ElevenLabsFehler):
        td.sprechen("Hallo", "de", None)


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
    assert a[a.index("-af") + 1] == td.STILLE_FILTER + ",apad" and a[a.index("-t") + 1] == "3.000000"
    assert a[a.index("-c:a") + 1] == "pcm_s16le" and a[-1] == "aus.wav"
    b = td.anpassen_argumente("roh.wav", "aus.wav", sample_rate=16000, fit=False, sekunden=3.0)
    assert "-t" not in b and "apad" not in " ".join(b) and b[b.index("-af") + 1] == td.STILLE_FILTER


def test_voiceover_stille_filter_abschaltbar(monkeypatch):
    """09.11.2026 Viral-Video-Guideline: Chatterbox setzt 0,4-0,7 s Stille an Kommas/Satzenden -> Reels
    mit 20-30 Pausen/min. Nur der Laura-Weg kuerzt; TON_VOICEOVER_STILLE=0 schaltet es ab (Rueckweg)."""
    assert "silenceremove" in td.STILLE_FILTER and "stop_periods=-1" in td.STILLE_FILTER
    monkeypatch.setenv("TON_VOICEOVER_STILLE", "0")
    a = td.anpassen_argumente("roh.wav", "aus.wav", sample_rate=48000, fit=True, sekunden=3.0)
    assert a[a.index("-af") + 1] == "apad"
    b = td.anpassen_argumente("roh.wav", "aus.wav", sample_rate=48000, fit=False, sekunden=3.0)
    assert "-af" not in b


def test_voiceover_stille_echt_gekuerzt(tmp_path):
    """Echtes ffmpeg: 0,3 s Stille + 1 s Ton + 1 s Stille + 1 s Ton + 0,8 s Stille -> Ton bleibt ganz,
    jede Stille hoechstens ~0,25 s."""
    import io
    import shutil
    import subprocess
    import wave
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg fehlt")
    roh = tmp_path / "roh.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "aevalsrc='if(between(t,0.3,1.3)+between(t,2.3,3.3),0.5*sin(2*PI*440*t),0)':s=24000:d=4.1",
                    str(roh)], check=True)
    daten = td.anpassen(roh.read_bytes(), sample_rate=24000, fit=False, sekunden=10)
    with wave.open(io.BytesIO(daten)) as w:
        dauer = w.getnframes() / 24000
    assert 2.0 <= dauer <= 2.0 + 0.05 + 0.27 + 0.27, dauer   # Ton ganz, vorher 4,1 s


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


def test_fehlgeschlagenes_laden_raeumt_auf(falsches_modell, monkeypatch, tmp_path):
    """24.10.2026: ein Ladeabbruch (os error 1455) hielt 5,5 GB fest - danach muss aufgeraeumt werden."""
    aufgeraeumt = []
    monkeypatch.setattr(td, "_freigeben_geraet", lambda: aufgeraeumt.append(1))
    monkeypatch.setenv("TON_MODELL_ORDNER", str(tmp_path))

    def kaputt(ordner, device):
        raise OSError("Die Auslagerungsdatei ist zu klein (os error 1455)")
    monkeypatch.setattr(falsches_modell, "from_local", kaputt)
    with pytest.raises(OSError):
        td._modell_laden()
    assert aufgeraeumt == [1] and td._modell is None


# --- /satz (Video-Werkbank, 11.11.2026) ---------------------------------------------------------

def _wav(rate=24000, frames=2400):
    return td._pcm_zu_wav(b"\x01\x02" * frames, rate)


def test_satz_pruefen_felder_und_grenzen(tmp_path, monkeypatch):
    monkeypatch.setenv("TON_STIMMEN_ORDNER", str(tmp_path))
    (tmp_path / "eigene-c.wav").write_bytes(b"x")
    p = td.satz_pruefen({"text": " Hallo. ", "vorher": "Davor.", "nachher": "", "stimme": "eigene-c"})
    assert p == {"text": "Hallo.", "vorher": "Davor.", "nachher": "", "sprache": "de",
                 "probe": tmp_path / "eigene-c.wav", "engine": "auto"}
    assert td.satz_pruefen({"text": "Hi"})["vorher"] == ""      # vorher/nachher duerfen fehlen
    for schlecht in ({"text": ""}, {"text": "x" * 1001}, {"text": "Hi", "vorher": "x" * 1001},
                     {"text": "Hi", "nachher": "x" * 1001}, {"text": "Hi", "vorher": 5},
                     {"text": "Hi", "sprache": "xx"}, {"text": "Hi", "stimme": "../x"}):
        with pytest.raises(td.Anfragefehler):
            td.satz_pruefen(schlecht)
    with pytest.raises(td.Anfragefehler):
        td.satz_pruefen([])


def test_satz_pruefen_engine_nur_auto_oder_chatterbox():
    assert td.satz_pruefen({"text": "Hi", "engine": "chatterbox"})["engine"] == "chatterbox"
    assert td.satz_pruefen({"text": "Hi", "engine": "auto"})["engine"] == "auto"
    for schlecht in ("elevenlabs", "", None, 3):
        with pytest.raises(td.Anfragefehler):
            td.satz_pruefen({"text": "Hi", "engine": schlecht})


def test_satz_elevenlabs_bekommt_nachbarsaetze(monkeypatch):
    import io
    import json
    monkeypatch.setenv("ELEVENLABS_API_KEY", "geheim")
    monkeypatch.setenv("TON_ELEVENLABS_STIMMEN", '{"standard": "Abcdefgh12345678"}')
    monkeypatch.setattr(td, "aussprache_laden", lambda *a: {})
    rumpf = {}

    class Antwort(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def oeffnen(anfrage, timeout):
        rumpf.update(json.loads(anfrage.data))
        return Antwort(b"\x00\x01" * 100)

    wav = td.sprechen_elevenlabs("Zwei.", "de", "standard", oeffnen=oeffnen, vorher="Eins.", nachher="Drei.")
    assert wav[:4] == b"RIFF"
    assert rumpf["previous_text"] == "Eins." and rumpf["next_text"] == "Drei." and rumpf["text"] == "Zwei."
    rumpf.clear()
    td.sprechen_elevenlabs("Zwei.", "de", "standard", oeffnen=oeffnen)
    assert "previous_text" not in rumpf and "next_text" not in rumpf   # leer -> gar nicht gesendet


def test_satz_sprechen_reicht_kontext_an_elevenlabs_und_kuerzt_stille(monkeypatch):
    monkeypatch.setenv("TON_ENGINE", "elevenlabs")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "geheim")
    gesehen = {}

    def eleven(text, sprache, stimme, oeffnen=None, vorher="", nachher=""):
        gesehen.update(text=text, stimme=stimme, vorher=vorher, nachher=nachher)
        return _wav()

    monkeypatch.setattr(td, "sprechen_elevenlabs", eleven)
    monkeypatch.setattr(td, "_stille_gekuerzt", lambda wav: b"GEKUERZT")
    p = td.satz_pruefen({"text": "Zwei.", "vorher": "Eins.", "nachher": "Drei."})
    assert td.satz_sprechen(p) == (b"GEKUERZT", "elevenlabs")
    assert gesehen == {"text": "Zwei.", "stimme": "standard", "vorher": "Eins.", "nachher": "Drei."}


def test_satz_chatterbox_ohne_elevenlabs_aufruf(monkeypatch):
    monkeypatch.setenv("TON_ENGINE", "elevenlabs")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "geheim")
    monkeypatch.setattr(td, "sprechen_elevenlabs", lambda *a, **k: pytest.fail("darf nicht gerufen werden"))
    monkeypatch.setattr(td, "_sprechen_chatterbox", lambda *a, **k: _wav())
    monkeypatch.setattr(td, "_stille_gekuerzt", lambda wav: wav)
    wav, eng = td.satz_sprechen(td.satz_pruefen({"text": "Hi", "engine": "chatterbox"}))
    assert eng == "chatterbox" and wav[:4] == b"RIFF"
    monkeypatch.delenv("TON_ENGINE")
    assert td.satz_sprechen(td.satz_pruefen({"text": "Hi"}))[1] == "chatterbox"   # auto ohne ElevenLabs


def test_satz_ohne_stillen_rueckfall(monkeypatch):
    """Faellt ElevenLabs aus, spricht /satz NICHT leise Chatterbox - der Client holt die Szene neu."""
    monkeypatch.setenv("TON_ENGINE", "elevenlabs")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "geheim")
    monkeypatch.delenv("TON_ELEVENLABS_RUECKFALL", raising=False)

    def kaputt(*a, **k):
        raise td.ElevenLabsFehler("ElevenLabs HTTP 429")

    monkeypatch.setattr(td, "sprechen_elevenlabs", kaputt)
    monkeypatch.setattr(td, "_sprechen_chatterbox", lambda *a, **k: pytest.fail("kein stiller Rueckfall"))
    with pytest.raises(td.ElevenLabsFehler):
        td.satz_sprechen(td.satz_pruefen({"text": "Hi"}))


def test_stille_kuerzen_folgt_dem_schalter(monkeypatch):
    gesehen = []
    monkeypatch.setattr(td, "anpassen", lambda wav, **kw: gesehen.append(kw) or b"AUS")
    wav = _wav(rate=44100)
    monkeypatch.setenv("TON_VOICEOVER_STILLE", "0")
    assert td._stille_gekuerzt(wav) == wav and gesehen == []
    monkeypatch.setenv("TON_VOICEOVER_STILLE", "1")
    assert td._stille_gekuerzt(wav) == b"AUS"
    assert gesehen == [{"sample_rate": 44100, "fit": False, "sekunden": 0.0}]


def _server():
    import threading
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), td.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _post(server, pfad, nutzlast):
    import json
    import urllib.error
    import urllib.request
    anfrage = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{pfad}",
                                     data=json.dumps(nutzlast).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(anfrage, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_post_satz_liefert_wav_mit_kopfzeilen_und_502_bei_elevenlabs_ausfall(monkeypatch):
    server = _server()
    try:
        monkeypatch.setattr(td, "satz_sprechen", lambda p: (_wav(rate=24000, frames=12000), "chatterbox"))
        status, kopf, rumpf = _post(server, "/satz", {"text": "Hallo."})
        assert status == 200 and kopf["Content-Type"] == "audio/wav" and rumpf[:4] == b"RIFF"
        assert kopf["X-Ton-Engine"] == "chatterbox" and kopf["X-Ton-Dauer-S"] == "0.500"

        def kaputt(p):
            raise td.ElevenLabsFehler("ElevenLabs HTTP 429 geheim")
        monkeypatch.setattr(td, "satz_sprechen", kaputt)
        status, kopf, rumpf = _post(server, "/satz", {"text": "Hallo."})
        assert status == 502 and kopf["X-Ton-Engine-Fehler"] == "elevenlabs" and b"geheim" not in rumpf

        status, _, _ = _post(server, "/satz", {"text": ""})
        assert status == 422
    finally:
        server.shutdown()
        server.server_close()


def test_sprechen_und_voiceover_unveraendert(monkeypatch):
    """/sprechen faellt weiter still auf Chatterbox zurueck, /voiceover kuerzt weiter per anpassen()."""
    server = _server()
    try:
        monkeypatch.setenv("TON_ENGINE", "elevenlabs")
        monkeypatch.setenv("ELEVENLABS_API_KEY", "geheim")
        monkeypatch.delenv("TON_ELEVENLABS_RUECKFALL", raising=False)

        def kaputt(*a, **k):
            raise td.ElevenLabsFehler("aus")
        monkeypatch.setattr(td, "sprechen_elevenlabs", kaputt)
        monkeypatch.setattr(td, "_sprechen_chatterbox", lambda *a, **k: b"CHATTERBOX")
        status, kopf, rumpf = _post(server, "/sprechen", {"text": "Hallo."})
        assert status == 200 and rumpf == b"CHATTERBOX" and "X-Ton-Engine" not in kopf
        monkeypatch.setattr(td, "anpassen", lambda wav, **kw: b"ANGEPASST")
        status, _, rumpf = _post(server, "/voiceover", {"text": "Hallo.", "duration_frames": 30, "fps_num": 30,
                                                       "fps_den": 1, "sample_rate": 24000})
        assert status == 200 and rumpf == b"ANGEPASST"
    finally:
        server.shutdown()
        server.server_close()
