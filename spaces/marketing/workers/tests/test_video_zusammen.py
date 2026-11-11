"""Zusammenfuegen der Szenen-Clips, Lautheit, Messnotiz und Fertigstellen. ffmpeg-Teile nur mit erzeugten Testclips."""
import shutil
import subprocess
from pathlib import Path

import pytest

from spaces.marketing.workers import video_zusammen as vz
from spaces.marketing.workers.bild_worker import ApiFehler
from spaces.marketing.workers.video_rendern import RenderFehler
from spaces.marketing.workers.video_zusammen import (
    ZusammenFehler, concat_argumente, fertigstellen, lautheit_argumente, lautheit_messen_argumente, messnotiz,
    musik_datei, zusammenfuegen)

AID = "0123abcd-1111-4222-8333-444455556666"
GEMESSEN = {"input_i": "-23.40", "input_tp": "-4.10", "input_lra": "5.60", "input_thresh": "-33.90",
            "target_offset": "0.20"}
WERTE_LANG = {"format": "lang", "dauer_s": 180.0, "harte_schnitte": 10, "einstellung_s": 16.4, "stillstand_s": 0.0,
              "stillstand_ab_s": 0.0, "hook_pro_min": 3.0, "lufs": -14.1, "pausen_pro_min": 2.0, "laengste_pause_s": 0.6}


def szenen(lokal=3, elevenlabs=9, woerter=450, dauer_s=180):
    n = lokal + elevenlabs
    je, rest = divmod(woerter, n)
    return [{"nr": i + 1, "id": f"sid-{i}", "variante": 0, "fassung": 1, "stand": "fertig",
             "clip_datei": f"wb-0123abcd-s{i + 1:02d}-f0.mp4", "dauer_s": dauer_s / n,
             "engine": "chatterbox" if i < lokal else "elevenlabs", "sprechtext": " ".join(["wort"] * (je + (i < rest)))}
            for i in range(n)]


# --- Argumente --------------------------------------------------------------------------------

def test_concat_kopiert_ohne_neukodierung():
    assert "-c copy" in " ".join(concat_argumente(Path("l.txt"), Path("z.mp4")))


def test_lautheit_zielt_auf_minus_14():
    a = " ".join(lautheit_argumente(Path("a.mp4"), Path("b.mp4"), GEMESSEN))
    assert "I=-14" in a and "measured_I=-23.40" in a and "-c:v copy" in a and "-ar 48000" in a
    assert "+faststart" in a and "-c:a aac" in a and "amix" not in a


def test_lautheit_messlauf_nutzt_dasselbe_ziel():
    a = " ".join(lautheit_messen_argumente(Path("a.mp4")))
    assert "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json" in a and "-f null" in a


def test_musik_wird_leise_untergelegt_und_auf_videolaenge_geschnitten():
    a = " ".join(lautheit_argumente(Path("a.mp4"), Path("b.mp4"), GEMESSEN, musik=Path("m.mp3")))
    assert "volume=0.06" in a and "amix=inputs=2:duration=first" in a and "-stream_loop -1" in a
    assert a.index("amix") < a.index("loudnorm") and "-c:v copy" in a


def test_ohne_musikdatei_kein_amix(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETING_VIDEO_MUSIK_ORDNER", str(tmp_path))
    assert musik_datei("vibemind") is None
    (tmp_path / "vibemind.mp3").write_bytes(b"x")
    assert musik_datei("vibemind") == tmp_path / "vibemind.mp3"
    assert musik_datei("../vibemind") is None


# --- Zusammenfuegen ---------------------------------------------------------------------------

def test_ungleicher_clip_bricht_vor_dem_fuegen_ab(tmp_path, monkeypatch):
    clips = [tmp_path / f"wb-0123abcd-s{i:02d}-f0.mp4" for i in (1, 2, 3)]
    for c in clips:
        c.write_bytes(b"x")
    formate = {clips[0].name: (1920, 1080), clips[1].name: (1280, 720), clips[2].name: (1920, 1080)}
    monkeypatch.setattr(vz, "pruefen", lambda p: {"breite": formate[Path(p).name][0], "hoehe": formate[Path(p).name][1],
                                                  "fps": 30, "dauer_s": 4.0, "ton": True})
    monkeypatch.setattr(vz, "_lauf", lambda *a, **k: pytest.fail("ffmpeg darf nicht laufen"))
    with pytest.raises(ZusammenFehler) as e:
        zusammenfuegen(clips, tmp_path / "z.mp4")
    assert "Szene 2" in e.value.grund


def test_clip_ohne_ton_oder_falsche_fps_nennt_die_szene(tmp_path, monkeypatch):
    clips = [tmp_path / f"wb-0123abcd-s{i:02d}-f0.mp4" for i in (1, 2)]
    for c in clips:
        c.write_bytes(b"x")
    monkeypatch.setattr(vz, "pruefen", lambda p: {"breite": 1920, "hoehe": 1080, "fps": 25 if "s01" in str(p) else 30,
                                                  "dauer_s": 4.0, "ton": "s02" not in str(p)})
    with pytest.raises(ZusammenFehler) as e:
        zusammenfuegen(clips, tmp_path / "z.mp4")
    assert "Szene 1" in e.value.grund


def test_pruefen_scheitert_wird_zusammenfehler(tmp_path, monkeypatch):
    c = tmp_path / "wb-0123abcd-s07-f0.mp4"
    c.write_bytes(b"x")

    def wirft(p):
        raise RenderFehler("kaputt")
    monkeypatch.setattr(vz, "pruefen", wirft)
    with pytest.raises(ZusammenFehler) as e:
        zusammenfuegen([c], tmp_path / "z.mp4")
    assert "Szene 7" in e.value.grund


# --- Messnotiz --------------------------------------------------------------------------------

def test_messnotiz_zeigt_tempo_aufrufe_und_stimmenmischung():
    notiz, warn = messnotiz(WERTE_LANG, szenen(lokal=3, elevenlabs=9, woerter=450, dauer_s=180), aufrufe=7)
    assert "150 Wörter/min" in notiz and "Ziel 190–230" in notiz and "Modell-Aufrufe: 7" in notiz
    assert notiz.startswith("Messung (Guideline, lang)")
    assert warn == ["Stimmen gemischt: 3 Szenen lokal, 9 Szenen ElevenLabs"]
    assert len(notiz) <= 1500


def test_messnotiz_tempo_im_ziel_ohne_abweichung():
    notiz, _ = messnotiz(WERTE_LANG, szenen(0, 12, woerter=624, dauer_s=180), aufrufe=1)
    assert "im Ziel" in notiz


def test_messung_fehlt_ist_warnung_kein_fehler():
    notiz, warn = messnotiz(None, szenen(0, 6, woerter=300, dauer_s=90), aufrufe=2)
    assert warn == ["Messung nicht möglich"] and "Modell-Aufrufe: 2" in notiz and "200 Wörter/min" in notiz


# --- Fertigstellen ----------------------------------------------------------------------------

class FakeApi:
    def __init__(self, hochladen_fehler=None, fertig_fehler=None):
        self.log, self.hochladen_fehler, self.fertig_fehler = [], hochladen_fehler, fertig_fehler

    def rufe(self, name):
        return [a for n, a in self.log if n == name]

    def holen(self, aid, name, ziel):
        self.log.append(("holen", name))
        Path(ziel).write_bytes(b"clip" + name.encode())
        return 100

    def hochladen(self, aid, name, pfad):
        if self.hochladen_fehler:
            raise self.hochladen_fehler
        self.log.append(("hochladen", {"name": name, "existiert": Path(pfad).is_file()}))

    def fertig(self, aid, teil):
        if self.fertig_fehler:
            raise self.fertig_fehler
        self.log.append(("fertig", teil))
        return True

    def hinweis(self, aid, key, satz):
        self.log.append(("hinweis", {"key": key, "satz": satz}))
        return True

    def szene_fehler(self, sid, grund):
        self.log.append(("szene_fehler", sid))
        return True


GLIEDERUNG = {"titel": "Der Titel", "hook": "Der Hook.", "stufen": [
    {"name": "Einstieg", "szenen": [{"kurz": "a"}, {"kurz": "b"}, {"kurz": "c"}, {"kurz": "d"}]},
    {"name": "Schluss", "szenen": [{"kurz": "e"}, {"kurz": "f"}, {"kurz": "g"}, {"kurz": "h"}]}]}


def werk_alle_fertig(**kw):
    w = {"auftrag": AID, "mandant": "vibemind", "gliederung": GLIEDERUNG, "stimme": "anna", "aufrufe": 7,
         "szenen": list(reversed(szenen(2, 6, woerter=800, dauer_s=240))), "alle_fertig": True,
         "video_fassung": 0, "video_veraltet": False}
    w.update(kw)
    return w


@pytest.fixture
def ff(monkeypatch):
    """Zusammenfuegen und Messen gefaked: es wird nur der Ablauf von fertigstellen geprueft."""
    stand = {"clips": None, "messlimit": None}

    def fuegen(clips, ziel, musik=None, zeitlimit_s=1800):
        stand["clips"], stand["musik"] = [Path(c).name for c in clips], musik
        Path(ziel).write_bytes(b"video")

    def messen(pfad, ffmpeg="ffmpeg", melden=lambda g: None, zeitlimit_s=120):
        stand["messlimit"] = zeitlimit_s
        return "LOG"
    monkeypatch.setattr(vz, "zusammenfuegen", fuegen)
    monkeypatch.setattr(vz.video_claw, "video_messen", messen)
    monkeypatch.setattr(vz.video_claw, "messwerte", lambda log: dict(WERTE_LANG))
    monkeypatch.setattr(vz, "musik_datei", lambda mandant: None)
    monkeypatch.setattr(vz, "pruefen", lambda p: {"breite": 1920, "hoehe": 1080, "fps": 30, "dauer_s": 240.0, "ton": True})
    monkeypatch.setattr(vz, "_frei", lambda ordner: 10 * 1024 ** 3)
    return stand


def test_fertigstellen_reihenfolge_und_meldung(tmp_path, ff):
    api = FakeApi()
    assert fertigstellen(api, werk_alle_fertig(), tmp_path) == "video"
    assert [n for n in ff["clips"]] == [f"wb-0123abcd-s{i:02d}-f0.mp4" for i in range(1, 9)]
    f = api.rufe("fertig")[0]
    assert f["datei"] == "wb-0123abcd-video-f1.mp4" and f["zeitmarken"][0]["zeit"] == "0:00"
    assert f["zeitmarken"][1]["zeit"] == "2:00" and "Messung" in f["messnotiz"] and "Modell-Aufrufe: 7" in f["messnotiz"]
    assert f["titel"] == "Der Titel" and f["bytes"] == 5 and f["dauer_s"] == 240.0
    assert f["warnungen"] == ["Stimmen gemischt: 2 Szenen lokal, 6 Szenen ElevenLabs"]
    assert api.rufe("hochladen") == [{"name": "wb-0123abcd-video-f1.mp4", "existiert": True}]
    assert ff["messlimit"] == max(120, 2 * 240)
    assert list(tmp_path.iterdir()) == []      # Clips und Zwischendateien sind nach dem Hochladen weg


def test_veraltetes_video_wird_als_fassung_2_neu_gebaut(tmp_path, ff):
    api = FakeApi()
    assert fertigstellen(api, werk_alle_fertig(video_fassung=1, video_veraltet=True), tmp_path) == "video"
    assert api.rufe("fertig")[0]["datei"] == "wb-0123abcd-video-f2.mp4"


def test_fuegefehler_laesst_szenen_fertig(tmp_path, ff, monkeypatch):
    def kaputt(clips, ziel, musik=None, zeitlimit_s=1800):
        raise ZusammenFehler("Szene 3 hat ein anderes Format.")
    monkeypatch.setattr(vz, "zusammenfuegen", kaputt)
    api = FakeApi()
    assert fertigstellen(api, werk_alle_fertig(), tmp_path) == "fehler"
    assert api.rufe("szene_fehler") == [] and api.rufe("fertig") == [] and api.rufe("hochladen") == []
    h = api.rufe("hinweis")
    assert len(h) == 1 and h[0]["key"] == "video_f1" and h[0]["satz"].startswith("Das Video ließ sich nicht zusammenfügen.")
    assert "Szene 3" in h[0]["satz"] and list(tmp_path.iterdir()) == []


def test_upload_fehler_ist_ein_hinweis(tmp_path, ff):
    api = FakeApi(hochladen_fehler=ApiFehler(503, "weg"))
    assert fertigstellen(api, werk_alle_fertig(), tmp_path) == "fehler"
    assert api.rufe("hinweis")[0]["key"] == "video_f1" and api.rufe("fertig") == [] and list(tmp_path.iterdir()) == []


def test_meldefehler_ist_ein_hinweis(tmp_path, ff):
    api = FakeApi(fertig_fehler=ApiFehler(409, "nimmt kein Video an"))
    assert fertigstellen(api, werk_alle_fertig(), tmp_path) == "fehler"
    assert len(api.rufe("hinweis")) == 1


def test_messung_scheitert_video_geht_trotzdem(tmp_path, ff, monkeypatch):
    monkeypatch.setattr(vz.video_claw, "video_messen", lambda *a, **k: None)
    api = FakeApi()
    assert fertigstellen(api, werk_alle_fertig(), tmp_path) == "video"
    f = api.rufe("fertig")[0]
    assert "Messung nicht möglich" in f["warnungen"] and f["dauer_s"] == 240.0


def test_zu_wenig_platz_ist_ein_klarer_hinweis(tmp_path, ff, monkeypatch):
    monkeypatch.setattr(vz, "_frei", lambda ordner: 150)       # 8 Clips zu je 100 Byte -> 2400 noetig
    api = FakeApi()
    assert fertigstellen(api, werk_alle_fertig(), tmp_path) == "fehler"
    assert "Platz" in api.rufe("hinweis")[0]["satz"] and api.rufe("hochladen") == [] and list(tmp_path.iterdir()) == []


def test_clip_fehlt_im_werk_ist_ein_hinweis(tmp_path, ff):
    w = werk_alle_fertig()
    w["szenen"][3]["clip_datei"] = None
    api = FakeApi()
    assert fertigstellen(api, w, tmp_path) == "fehler" and "Clip" in api.rufe("hinweis")[0]["satz"]
    assert api.rufe("holen") == []


# --- echtes ffmpeg ----------------------------------------------------------------------------

def _clip(ziel: Path, farbe: str, ton_pegel: float, sekunden: int = 4) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color={farbe}:s=1920x1080:d={sekunden}:r=30",
         "-f", "lavfi", "-i", f"sine=f=440:d={sekunden}:sample_rate=48000",
         "-af", f"volume={ton_pegel}dB", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
         "-c:a", "aac", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(ziel)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=120)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")
def test_drei_echte_clips_ergeben_ein_video_mit_minus_14_lufs(tmp_path):
    from spaces.marketing.workers.video_claw import messwerte, video_messen
    clips = []
    for i, (farbe, pegel) in enumerate([("red", -6), ("blue", -18), ("green", -30)], 1):
        c = tmp_path / f"wb-0123abcd-s{i:02d}-f0.mp4"
        _clip(c, farbe, pegel)
        clips.append(c)
    summe = sum(vz.pruefen(c)["dauer_s"] for c in clips)
    vor = messwerte(video_messen(str(clips[0])))["lufs"]
    ziel = tmp_path / "video.mp4"
    zusammenfuegen(clips, ziel)
    p = vz.pruefen(ziel)
    w = messwerte(video_messen(str(ziel)))
    print(f"MESSUNG summe={summe:.3f} verbunden={p['dauer_s']:.3f} drift={p['dauer_s'] - summe:+.3f} "
          f"lufs_clip1_vorher={vor} lufs_nachher={w['lufs']}")
    assert abs(w["dauer_s"] - 12) < 0.5 and -15.0 <= w["lufs"] <= -13.0
    assert abs(p["dauer_s"] - summe) < 0.2
    assert sorted(x.name for x in tmp_path.iterdir() if x.name.endswith((".txt", ".roh.mp4", ".log"))) == []
