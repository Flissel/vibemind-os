"""Video-Stimme: Szene Satz fuer Satz sprechen lassen, Dauern aus den geschriebenen Daten, keine gemischten Stimmen."""
import io
import wave

import pytest

from spaces.marketing.workers import video_stimme as vs


def _wav(dauer_s: float, rate: int = 24000) -> bytes:
    puffer = io.BytesIO()
    with wave.open(puffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * round(dauer_s * rate))
    return puffer.getvalue()


class Fake:
    """Ersetzt den HTTP-Aufruf: (status, kopf, rumpf). Merkt sich jede Nutzlast."""

    def __init__(self, dauer=(1.0,), ausfall_bei=None, rate=24000, raten=None, lokal=False, engine_je_satz=None):
        self.dauer, self.ausfall_bei, self.rate, self.raten, self.lokal = dauer, ausfall_bei, rate, raten, lokal
        self.engine_je_satz = engine_je_satz
        self.nutzlasten: list[dict] = []

    def __call__(self, nutzlast):
        self.nutzlasten.append(nutzlast)
        n = len(self.nutzlasten)
        if nutzlast["engine"] == "chatterbox" or self.lokal:
            nr = sum(1 for p in self.nutzlasten if p["engine"] == "chatterbox" or self.lokal)
            return 200, {"X-Ton-Engine": "chatterbox"}, _wav(self.dauer[(nr - 1) % len(self.dauer)], self.rate)
        if self.ausfall_bei == n:
            return 502, {"X-Ton-Engine-Fehler": "elevenlabs"}, b"ElevenLabs HTTP 429 geheim"
        engine = (self.engine_je_satz or {}).get(n, "elevenlabs")
        rate = (self.raten or {}).get(n, 44100 if engine == "elevenlabs" else self.rate)
        return 200, {"X-Ton-Engine": engine}, _wav(self.dauer[(n - 1) % len(self.dauer)], rate)

    def aufrufe_mit_engine(self, engine):
        return sum(1 for p in self.nutzlasten if p["engine"] == engine)


def test_dauern_und_startzeiten(tmp_path):
    fake = Fake(dauer=[1.0, 2.0, 0.5], lokal=True)
    g = vs.szene_sprechen("Eins. Zwei. Drei.", "eigene-c", tmp_path, senden=fake)
    assert [round(s.start_s, 2) for s in g.saetze] == [0.0, 1.15, 3.3]
    assert [round(s.dauer_s, 2) for s in g.saetze] == [1.0, 2.0, 0.5]
    assert round(g.dauer_s, 2) == 3.8 and [s.text for s in g.saetze] == ["Eins.", "Zwei.", "Drei."]
    assert g.engine == "chatterbox" and g.rueckfall is False
    with wave.open(str(g.wav)) as w:    # Dauer kommt aus der geschriebenen Datei
        assert round(w.getnframes() / w.getframerate(), 2) == 3.8


def test_jeder_satz_bekommt_voriges_und_naechstes_als_kontext(tmp_path):
    fake = Fake(dauer=[0.5])
    vs.szene_sprechen("Eins. Zwei. Drei.", "eigene-c", tmp_path, senden=fake)
    assert [(p["vorher"], p["text"], p["nachher"]) for p in fake.nutzlasten] == [
        ("", "Eins.", "Zwei."), ("Eins.", "Zwei.", "Drei."), ("Zwei.", "Drei.", "")]
    assert all(p["stimme"] == "eigene-c" and p["sprache"] == "de" and p["engine"] == "auto"
               for p in fake.nutzlasten)


def test_elevenlabs_szene_meldet_engine_ohne_rueckfall(tmp_path):
    g = vs.szene_sprechen("Eins. Zwei.", "eigene-c", tmp_path, senden=Fake(dauer=[1.0, 1.0]))
    assert g.engine == "elevenlabs" and g.rueckfall is False


def test_ausfall_mitten_in_der_szene_spricht_alles_lokal(tmp_path):
    fake = Fake(dauer=[1.0, 1.0, 1.0], ausfall_bei=2)
    g = vs.szene_sprechen("Eins. Zwei. Drei.", "eigene-c", tmp_path, senden=fake)
    assert g.engine == "chatterbox" and g.rueckfall is True
    assert fake.aufrufe_mit_engine("chatterbox") == 3        # alle drei neu, nichts gemischt
    assert len(g.saetze) == 3 and round(g.dauer_s, 2) == 3.3


def test_unterschiedliche_engines_in_einer_szene_werden_nicht_gemischt(tmp_path):
    fake = Fake(dauer=[1.0], engine_je_satz={2: "chatterbox"})
    g = vs.szene_sprechen("Eins. Zwei. Drei.", "eigene-c", tmp_path, senden=fake)
    assert g.engine == "chatterbox" and g.rueckfall is True and fake.aufrufe_mit_engine("chatterbox") == 3


def test_abweichende_rate_wird_auf_48k_vereinheitlicht(tmp_path, monkeypatch):
    aufgerufen = []

    def resample(quelle, ziel):
        aufgerufen.append(quelle.name)
        with wave.open(str(quelle)) as w:
            sekunden = w.getnframes() / w.getframerate()
        ziel.write_bytes(_wav(sekunden, 48000))

    monkeypatch.setattr(vs, "_auf_48k", resample)
    fake = Fake(dauer=[1.0, 1.0], raten={1: 44100, 2: 24000}, engine_je_satz={1: "chatterbox", 2: "chatterbox"})
    g = vs.szene_sprechen("Eins. Zwei.", "eigene-c", tmp_path, senden=fake)
    assert aufgerufen and round(g.dauer_s, 2) == 2.15
    with wave.open(str(g.wav)) as w:
        assert w.getframerate() == 48000


def test_dienst_weg_ist_stimmefehler(tmp_path):
    def weg(nutzlast):
        raise ConnectionRefusedError("127.0.0.1:8138 /pfad/zum/geheimen/Text")
    with pytest.raises(vs.StimmeFehler) as e:
        vs.szene_sprechen("Eins.", "eigene-c", tmp_path, senden=weg)
    assert "ConnectionRefusedError" in str(e.value) or "nicht erreichbar" in str(e.value)
    assert "geheim" not in str(e.value) and "8138" not in str(e.value)


def test_fehlerantwort_ist_stimmefehler_ohne_text_und_pfad(tmp_path):
    def kaputt(nutzlast):
        return 500, {}, b"Traceback C:\\geheim\\x.py Text: Eins."
    with pytest.raises(vs.StimmeFehler) as e:
        vs.szene_sprechen("Eins.", "eigene-c", tmp_path, senden=kaputt)
    assert "500" in str(e.value) and "geheim" not in str(e.value) and "Eins" not in str(e.value)


def test_502_im_lokalen_durchlauf_ist_stimmefehler(tmp_path):
    def immer_502(nutzlast):
        return 502, {"X-Ton-Engine-Fehler": "elevenlabs"}, b""
    with pytest.raises(vs.StimmeFehler):
        vs.szene_sprechen("Eins. Zwei.", "eigene-c", tmp_path, senden=immer_502)


def test_leerer_text_und_kaputtes_audio(tmp_path):
    with pytest.raises(vs.StimmeFehler):
        vs.szene_sprechen("  ", "eigene-c", tmp_path, senden=Fake())
    with pytest.raises(vs.StimmeFehler):
        vs.szene_sprechen("Eins.", "eigene-c", tmp_path, senden=lambda p: (200, {"X-Ton-Engine": "chatterbox"}, b"kein wav"))
