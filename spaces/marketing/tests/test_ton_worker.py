"""Ton-Arbeiter (Podcast, 17.10.2026): Ablauf mit Attrappen, Zusammenfuegen mit echtem ffmpeg."""
import io
import math
import shutil
import struct
import wave

import pytest

from spaces.marketing.workers import ton_worker as tw

AID = "0123abcd-1111-1111-1111-111111111111"


def wav(sekunden=0.5, rate=24000) -> bytes:
    puffer = io.BytesIO()
    with wave.open(puffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 20))) for i in range(int(rate * sekunden))))
    return puffer.getvalue()


class Api:
    def __init__(self, auftrag):
        self.auftrag, self.aufrufe = auftrag, []

    def naechster(self):
        self.aufrufe.append(("naechster",))
        return self.auftrag

    def weiter(self, aid):
        self.aufrufe.append(("weiter", aid))
        return True

    def datei(self, aid, mp3):
        self.aufrufe.append(("datei", aid, len(mp3)))
        self.mp3 = mp3
        return f"pod-{aid[:8]}-test.mp3"

    def fertig(self, aid, name, dauer):
        self.aufrufe.append(("fertig", aid, name, round(dauer, 1)))

    def fehler(self, aid, befund):
        self.aufrufe.append(("fehler", aid, befund))


AUFTRAG = {"id": AID, "sprache": "de", "skript": [{"sprecher": "Anna", "text": "Hallo."},
                                                    {"sprecher": "Ben", "text": "Willkommen."}]}

braucht_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg fehlt")


@braucht_ffmpeg
def test_folge_wird_gesprochen_zusammengefuegt_und_abgeliefert():
    api, gesprochen = Api(AUFTRAG), []

    def sprich(text, sprache, stimme):
        gesprochen.append((text, sprache, stimme))
        return wav(0.5)
    ergebnis = tw.ein_durchlauf(api, sprich=sprich, bereit=lambda: True)
    assert ergebnis.startswith("fertig: pod-0123abcd-test.mp3")
    assert gesprochen == [("Hallo.", "de", None), ("Willkommen.", "de", None)]
    assert api.mp3[:3] == b"ID3" or (api.mp3[0] == 0xFF and api.mp3[1] & 0xE0 == 0xE0)
    fertig = [a for a in api.aufrufe if a[0] == "fertig"][0]
    assert fertig[3] == pytest.approx(0.5 + 0.45 + 0.5, abs=0.1)


def test_ohne_sprech_dienst_wird_nichts_beansprucht():
    api = Api(AUFTRAG)
    assert tw.ein_durchlauf(api, sprich=None, bereit=lambda: False) == "Sprech-Dienst nicht bereit"
    assert api.aufrufe == []


def test_fehler_beim_sprechen_wird_befund():
    api = Api(AUFTRAG)

    def sprich(text, sprache, stimme):
        raise RuntimeError("Sprech-Dienst 500: CUDA out of memory")
    assert tw.ein_durchlauf(api, sprich=sprich, bereit=lambda: True).startswith("fehler")
    befund = [a for a in api.aufrufe if a[0] == "fehler"][0][2]
    assert "CUDA out of memory" in befund


def test_nichts_zu_tun():
    assert tw.ein_durchlauf(Api(None), sprich=None, bereit=lambda: True) == "nichts zu tun"


def test_stimme_nur_mit_hinterlegter_probe(tmp_path, monkeypatch):
    (tmp_path / "juergen.wav").write_bytes(b"RIFF")
    monkeypatch.setenv("TON_STIMMEN_ORDNER", str(tmp_path))
    assert tw.stimme_fuer("Jürgen") == "juergen"
    assert tw.stimme_fuer("Juergen") == "juergen"
    assert tw.stimme_fuer("Anna") is None


def test_lange_folge_verlaengert_die_frist(monkeypatch):
    auftrag = {"id": AID, "sprache": "de", "skript": [{"sprecher": "A", "text": f"Satz {i}."} for i in range(10)]}
    api = Api(auftrag)
    monkeypatch.setattr(tw, "zusammenfuegen", lambda teile, ffmpeg="ffmpeg": (b"ID3xx", 9.0))
    tw.ein_durchlauf(api, sprich=lambda *a: wav(0.1), bereit=lambda: True)
    assert sum(1 for a in api.aufrufe if a[0] == "weiter") == 2


def float_wav(sekunden=0.5, rate=24000) -> bytes:
    """32-Bit-Float-WAV (Format 3), wie torchaudio es ohne Vorgabe schreibt - das wave-Modul liest es nicht."""
    daten = b"".join(struct.pack("<f", 0.2 * math.sin(i / 20)) for i in range(int(rate * sekunden)))
    fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(daten)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(daten)) + daten)


def test_dauer_auch_fuer_float_wav():
    assert tw._dauer(float_wav(1.5)) == pytest.approx(1.5, abs=0.01)
    assert tw._dauer(wav(0.5)) == pytest.approx(0.5, abs=0.01)


@braucht_ffmpeg
def test_float_wav_laesst_sich_zusammenfuegen():
    mp3, dauer = tw.zusammenfuegen([float_wav(0.5), float_wav(0.5)])
    assert len(mp3) > 1000 and dauer == pytest.approx(1.45, abs=0.05)
