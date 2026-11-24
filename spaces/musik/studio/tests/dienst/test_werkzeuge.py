"""Werkzeuge des Musik-Dienstes: Bestellen, Anzeigen, Laura-Anschluss. Kein Netzgriff:
Laura ist ein aufzeichnendes Ersatzmodul, der Laeufer ein Ersatz."""
import json
import threading
import time

import pytest

from djset.bestand import Bestand
from djset.dienst.auftraege import Ablage
from djset.dienst.werkzeuge import Werkzeuge
from djset.video.vertrag import VideomusikErgebnis, laura_clip

BASIS = "http://link.test:8146"
SCHLUESSEL = b"test-schluessel-nur-fuer-tests"
LAUF = "2026-11-24-video-test-01"
JETZT = 1_800_000_000.0


class FakeLaura:
    """Schreibt jeden Aufruf mit; Antworten sind einstellbar."""

    def __init__(self):
        self.aufrufe: list[tuple] = []
        self.zeit = {"ok": True, "project_id": "proj1", "name": "Reel", "frames": 900,
                     "fps_num": 30, "fps_den": 1}
        self.clips: list[dict] = []
        self.export = {"ok": True, "url": "/exports/e1/download", "size_bytes": 1234}
        self.bremse: threading.Event | None = None
        self.drin = threading.Event()

    def _s(self, *a):
        self.aufrufe.append(a)

    def namen(self):
        return [a[0] for a in self.aufrufe]

    def zeitleiste(self, timeline_id):
        self._s("zeitleiste", timeline_id)
        return dict(self.zeit)

    def videos_suchen(self, suchwort, hoechstens=10):
        self._s("videos_suchen", suchwort)
        return {"ok": True, "treffer": [{"timeline_id": "t1"}]}

    def importieren(self, project_id, url, display_name):
        self._s("importieren", project_id, url, display_name)
        return {"ok": True, "asset_id": "asset1"}

    def asset_warten(self, asset_id, **kw):
        self._s("asset_warten", asset_id)
        if self.bremse is not None:
            self.drin.set()
            self.bremse.wait(5)
        return {"ok": True, "asset": {}}

    def audio_clips(self, timeline_id):
        self._s("audio_clips", timeline_id)
        return {"ok": True, "clips": list(self.clips)}

    def clip_anlegen(self, timeline_id, koerper):
        self._s("clip_anlegen", timeline_id, koerper)
        clip = {"id": "clip1", **koerper}
        self.clips.append(clip)
        return {"ok": True, "clip": clip}

    def rendern(self, timeline_id, reel_optionen=None):
        self._s("rendern", timeline_id, reel_optionen)
        return {"ok": True, "export_id": "e1", "job_id": "j1"}

    def export_warten(self, export_id, **kw):
        self._s("export_warten", export_id)
        return dict(self.export)

    def herunterladen(self, url, ziel):
        self._s("herunterladen", url, ziel)
        ziel.write_bytes(b"MP4")
        return {"ok": True, "bytes": 3}


class FakeLaeufer:
    def __init__(self, antwort=True):
        self.antwort = antwort
        self.ids: list[str] = []

    def abbrechen(self, auftrag_id):
        self.ids.append(auftrag_id)
        return self.antwort


@pytest.fixture
def laura():
    return FakeLaura()


@pytest.fixture
def ablage(tmp_path):
    return Ablage(tmp_path)


@pytest.fixture
def w(tmp_path, ablage, laura):
    return Werkzeuge(tmp_path, ablage, FakeLaeufer(), laura, BASIS, SCHLUESSEL,
                     uhr=lambda: JETZT, reel_ordner=tmp_path / "reels")


def _fertiges_video(ablage, tmp_path, frames=900, fps=(30, 1), titel="Mein Song",
                    zustand="fertig", sekunden=None, mit_laura=True):
    num, den = fps
    sek = sekunden if sekunden is not None else frames * den / num
    erg = VideomusikErgebnis(
        datei="videomusik.wav", mp3="videomusik.mp3", sekunden=sek, samples=round(sek * 44100),
        sample_rate=44100, frames=frames if mit_laura else None,
        fps_num=num if mit_laura else None, fps_den=den if mit_laura else None,
        song_id="mein-song", titel=titel, herkunft="suno-eigen", start_im_song_s=0.0,
        schluss_s=sek - 2, takte=8, bpm=120.0, gesang=False, lufs=-14.0, true_peak=-1.5,
        laura=laura_clip(frames, num, den, titel) if mit_laura else None,
    )
    ordner = tmp_path / "runs" / LAUF
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "videomusik.json").write_text(erg.model_dump_json(), encoding="utf-8")
    (ordner / "videomusik.wav").write_bytes(b"RIFF")
    (ordner / "videomusik.mp3").write_bytes(b"ID3")
    (ordner / "bericht.md").write_text(
        "# Videomusik: x\n\n- Song: Mein Song\n\n## Pruefung\n\n- Pegel zu leise\n", encoding="utf-8")
    a = ablage.anlegen("video", {"wunsch": "x"}, "mcp")
    a.zustand = zustand
    a.lauf = LAUF
    ablage.speichern(a)
    return a


# ---------------------------------------------------------------- Bestellen


def test_videomusik_aus_zeitleiste_uebernimmt_frames(w, laura, ablage):
    laura.zeit.update(frames=899, fps_num=30000, fps_den=1001)
    r = w.videomusik_bestellen("ruhig", laura_timeline_id="tl1")
    assert r["ok"] is True
    assert (r["frames"], r["fps"]) == (899, "30000/1001")
    e = ablage.hole(r["auftrag"]).eingabe
    assert e["frames"] == 899 and e["fps"] == "30000/1001" and e["laura_timeline_id"] == "tl1"
    assert "sekunden" not in e


def test_videomusik_sekunden_und_gesang(w, ablage):
    r = w.videomusik_bestellen("x", sekunden=20, songs=["a"], gesang=False)
    e = ablage.hole(r["auftrag"]).eingabe
    assert e["sekunden"] == 20 and e["songs"] == ["a"] and e["ohne_gesang"] is True
    assert "mit_gesang" not in e and "frames" not in e
    r = w.videomusik_bestellen("x", frames=600, fps="30", gesang=True)
    e = ablage.hole(r["auftrag"]).eingabe
    assert e["mit_gesang"] is True and "ohne_gesang" not in e and e["fps"] == "30"
    assert r["position"] >= 0


def test_genau_eine_laengenangabe(w, ablage):
    assert w.videomusik_bestellen("x")["ok"] is False
    assert w.videomusik_bestellen("x", sekunden=10, frames=300, fps="30")["ok"] is False
    assert w.videomusik_bestellen("x", sekunden=10, laura_timeline_id="t")["ok"] is False
    assert w.videomusik_bestellen("x", frames=300)["ok"] is False  # Frames ohne fps
    assert w.videomusik_bestellen("x", sekunden=-1)["ok"] is False
    assert w.videomusik_bestellen("", sekunden=10)["ok"] is False
    assert w.videomusik_bestellen("x", sekunden=10, songs="a")["ok"] is False
    assert ablage.alle() == []


def test_song_aufnehmen_ohne_pfad(w, ablage, tmp_path):
    for datei in ("..\\x.wav", "C:/x.wav", "../x.wav", "a/b.wav", "", "x.txt", "fehlt.wav"):
        r = w.song_aufnehmen(datei, "Titel", "suno-eigen")
        assert r["ok"] is False, datei
    assert w.song_aufnehmen("x.wav", "Titel", "gekauft")["ok"] is False  # Herkunft
    assert ablage.alle() == []


def test_song_aufnehmen_legt_auftrag_an(w, ablage, tmp_path):
    (tmp_path / "eingang").mkdir(exist_ok=True)
    (tmp_path / "eingang" / "lied.wav").write_bytes(b"RIFF")
    r = w.song_aufnehmen("lied.wav", "Lied", "suno-eigen", suno_id="abc", kanal="telegram")
    assert r["ok"] is True
    a = ablage.hole(r["auftrag"])
    assert a.art == "aufnehmen" and a.kanal == "telegram"
    assert a.eingabe == {"datei": "lied.wav", "titel": "Lied", "herkunft": "suno-eigen", "suno_id": "abc"}


def test_set_bestellen_und_position(w, ablage):
    r1 = w.set_bestellen("Abend", 30)
    r2 = w.set_bestellen("Nacht", 20.5, songs=["a", "b"], ohne_gesang=True)
    assert r1["ok"] and r2["ok"]
    assert r2["position"] == r1["position"] + 1
    e = ablage.hole(r2["auftrag"]).eingabe
    assert e == {"wunsch": "Nacht", "length": 20.5, "songs": ["a", "b"], "ohne_gesang": True}
    assert w.set_bestellen("x", 0)["ok"] is False
    # laeuft gerade etwas: Position steigt um 1
    a = ablage.hole(r1["auftrag"])
    a.zustand = "laeuft"
    ablage.speichern(a)
    assert w.set_bestellen("y", 5)["position"] == 2  # r2 wartet davor, r1 laeuft


def test_bestand_zeigen(w, tmp_path):
    r = w.bestand_zeigen()
    assert r == {"ok": True, "songs": []}
    assert Bestand(tmp_path).alle() == []


# ---------------------------------------------------------------- in_laura_legen


def test_in_laura_bildrate_29_97_rechnet_neu(w, laura, ablage, tmp_path):
    a = _fertiges_video(ablage, tmp_path, frames=900, fps=(30, 1))
    laura.zeit.update(frames=899, fps_num=30000, fps_den=1001)
    r = w.in_laura_legen(a.id, "tl1")
    assert r == {"ok": True, "clip_id": "clip1", "asset_id": "asset1"}
    koerper = [x for x in laura.aufrufe if x[0] == "clip_anlegen"][0][2]
    assert koerper["seq_out_frame_exclusive"] == 899


def test_in_laura_falsche_laenge_legt_nichts_an(w, laura, ablage, tmp_path):
    a = _fertiges_video(ablage, tmp_path, frames=900, fps=(30, 1))
    laura.zeit.update(frames=1200)
    r = w.in_laura_legen(a.id, "tl1")
    assert r["ok"] is False
    assert r["fehler"] == "Musik passt nicht zur Zeitleiste (900 statt 1200 Frames)"
    assert "importieren" not in laura.namen() and "clip_anlegen" not in laura.namen()


def test_in_laura_zweimal_legt_einmal_an(w, laura, ablage, tmp_path):
    a = _fertiges_video(ablage, tmp_path)
    r1 = w.in_laura_legen(a.id, "tl1")
    laura.clips = [{"id": "clip1", "asset_id": "asset1", "label": f"Mein Song · musik:{a.id}"}]
    r2 = w.in_laura_legen(a.id, "tl1")
    assert r1["ok"] and r2 == {"ok": True, "clip_id": "clip1", "asset_id": "asset1"}
    assert laura.namen().count("importieren") == 1
    assert laura.namen().count("clip_anlegen") == 1


def test_in_laura_gleichzeitig_legt_einmal_an(w, laura, ablage, tmp_path):
    a = _fertiges_video(ablage, tmp_path)
    laura.bremse = threading.Event()
    ergebnisse: list[dict] = []
    t1 = threading.Thread(target=lambda: ergebnisse.append(w.in_laura_legen(a.id, "tl1")))
    t2 = threading.Thread(target=lambda: ergebnisse.append(w.in_laura_legen(a.id, "tl1")))
    t1.start()
    assert laura.drin.wait(5)  # t1 steckt im asset_warten
    t2.start()
    time.sleep(0.3)  # t2 muss an der Sperre stehen, nicht im Import
    laura.bremse.set()
    t1.join(10)
    t2.join(10)
    assert len(ergebnisse) == 2 and all(r["ok"] for r in ergebnisse), ergebnisse
    assert laura.namen().count("importieren") == 1
    assert laura.namen().count("clip_anlegen") == 1
    assert {r["clip_id"] for r in ergebnisse} == {"clip1"}


def test_in_laura_ein_frame_zu_lang_wird_gekappt(w, laura, ablage, tmp_path):
    a = _fertiges_video(ablage, tmp_path, frames=900)
    laura.zeit.update(frames=899)  # Musik 1 Frame zu lang
    assert w.in_laura_legen(a.id, "tl1")["ok"] is True
    koerper = [x for x in laura.aufrufe if x[0] == "clip_anlegen"][0][2]
    assert koerper["seq_out_frame_exclusive"] == 899
    laura.aufrufe.clear()
    laura.clips.clear()
    laura.zeit.update(frames=901)  # 1 Frame zu kurz bleibt
    assert w.in_laura_legen(a.id, "tl1")["ok"] is True
    koerper = [x for x in laura.aufrufe if x[0] == "clip_anlegen"][0][2]
    assert koerper["seq_out_frame_exclusive"] == 900


def test_in_laura_ducking_immer_100_und_label_mit_auftrag(w, laura, ablage, tmp_path):
    a = _fertiges_video(ablage, tmp_path, titel="T" * 250)
    r = w.in_laura_legen(a.id, "tl1", gain_percent=25)
    assert r["ok"] is True, r
    koerper = [x for x in laura.aufrufe if x[0] == "clip_anlegen"][0][2]
    assert koerper["ducking_percent"] == 100 and koerper["gain_percent"] == 25
    assert koerper["asset_id"] == "asset1" and koerper["mix_mode"] == "mix"
    assert len(koerper["label"]) == 200 and koerper["label"].endswith(f"musik:{a.id}")
    imp = [x for x in laura.aufrufe if x[0] == "importieren"][0]
    assert imp[1] == "proj1" and imp[3].endswith(".wav")
    assert imp[2].startswith(f"{BASIS}/datei/{a.id}/videomusik.wav?ablauf=")


def test_in_laura_gain_ausserhalb_und_nicht_fertig(w, laura, ablage, tmp_path):
    a = _fertiges_video(ablage, tmp_path)
    assert w.in_laura_legen(a.id, "tl1", gain_percent="laut")["ok"] is False
    assert w.in_laura_legen(a.id, "tl1", gain_percent=True)["ok"] is False
    b = _fertiges_video(ablage, tmp_path, zustand="gescheitert")
    assert w.in_laura_legen(b.id, "tl1")["ok"] is False
    c = _fertiges_video(ablage, tmp_path, mit_laura=False)
    assert w.in_laura_legen(c.id, "tl1")["ok"] is False
    assert w.in_laura_legen(a.id, "../x")["ok"] is False
    assert w.in_laura_legen("nix", "tl1")["ok"] is False
    assert "importieren" not in laura.namen()


def test_in_laura_ohne_link_host_fehler_ohne_import(tmp_path, ablage, laura):
    ohne = Werkzeuge(tmp_path, ablage, FakeLaeufer(), laura, None, None)
    a = _fertiges_video(ablage, tmp_path)
    r = ohne.in_laura_legen(a.id, "tl1")
    assert r["ok"] is False and "Link" in r["fehler"]
    assert "importieren" not in laura.namen() and "clip_anlegen" not in laura.namen()


# ---------------------------------------------------------------- Anzeigen


def test_auftrag_zeigen_ohne_pfade(w, ablage, tmp_path):
    a = _fertiges_video(ablage, tmp_path)
    (tmp_path / "runs" / LAUF / "bericht.md").write_text(
        "\n".join(f"zeile {i}" for i in range(100)) + "\n## Pruefung\n\n- Pegel zu leise\n",
        encoding="utf-8")
    r = w.auftrag_zeigen(a.id)
    assert r["ok"] is True and r["id"] == a.id and r["zustand"] == "fertig"
    assert "videomusik.wav" in r["dateien"] and "videomusik.json" in r["dateien"]
    assert len(r["bericht"].splitlines()) <= 40
    assert r["befunde"] == ["Pegel zu leise"]
    assert r["titel"] == "Mein Song" and r["laenge_s"] == pytest.approx(30.0)
    text = json.dumps(r, ensure_ascii=False)
    assert ":\\\\" not in text and ":\\" not in text and "/runs/" not in text
    assert str(tmp_path) not in text and str(tmp_path).replace("\\", "/") not in text


def test_auftrag_zeigen_grund_ohne_pfade(w, ablage, tmp_path):
    a = _fertiges_video(ablage, tmp_path, zustand="gescheitert")
    a.grund = 'File "C:\\Users\\x\\djset\\cli.py", line 3'
    ablage.speichern(a)
    (tmp_path / "runs" / LAUF / "bericht.md").write_text(
        "- Quelle \\\\server\\share\\x.wav\n- siehe /home/u/lauf/a.wav und /tmp/x\n- ok\n",
        encoding="utf-8")
    text = json.dumps(w.auftrag_zeigen(a.id), ensure_ascii=False)
    for roh in ("C:\\\\Users", "cli.py", "\\\\\\\\server", "/home/u", "/tmp/x"):
        assert roh not in text, roh
    assert "<pfad>" in text
    assert "<pfad>" in w.auftraege_offen("mcp")["auftraege"][0]["grund"]


def test_auftrag_zeigen_ohne_lauf_und_unbekannt(w, ablage):
    a = ablage.anlegen("set", {"wunsch": "x", "length": 5}, "mcp")
    r = w.auftrag_zeigen(a.id)
    assert r["ok"] is True and r["dateien"] == [] and r["bericht"] == "" and r["befunde"] == []
    assert r["laenge_s"] is None and r["titel"] is None
    assert w.auftrag_zeigen("kaputt")["ok"] is False


def test_offen_gemeldet_abbrechen(w, ablage, tmp_path):
    a = _fertiges_video(ablage, tmp_path)
    b = ablage.anlegen("set", {}, "telegram")
    assert [x["id"] for x in w.auftraege_offen("mcp")["auftraege"]] == [a.id]
    assert w.auftraege_offen("telegram")["auftraege"] == []  # b wartet noch
    assert w.gemeldet(b.id)["ok"] is False
    assert w.gemeldet(a.id) == {"ok": True}
    assert w.auftraege_offen("mcp")["auftraege"] == []
    r = w.auftrag_abbrechen(b.id)
    assert r["ok"] is True and w.laeufer.ids == [b.id]
    w.laeufer.antwort = False
    assert w.auftrag_abbrechen(a.id)["ok"] is False


# ---------------------------------------------------------------- Laura-Render


def test_laura_videos_suchen_und_rendern(w, ablage, laura):
    assert w.laura_videos_suchen("reel") == {"ok": True, "treffer": [{"timeline_id": "t1"}]}
    r = w.laura_rendern("tl1", kanal="telegram")
    assert r["ok"] is True
    a = ablage.hole(r["auftrag"])
    assert a.art == "laura_render" and a.eingabe["timeline_id"] == "tl1" and a.kanal == "telegram"
    assert w.laura_rendern("../x")["ok"] is False
    assert w.laura_rendern("tl1", musik_auftrag="unbekannt")["ok"] is False


def test_render_ausfuehren_laedt_herunter(w, ablage, laura, tmp_path):
    a = ablage.anlegen("laura_render", {"timeline_id": "tl1"}, "mcp")
    r = w.render_ausfuehren(a)
    assert r == {"ok": True, "export_id": "e1", "size_bytes": 1234, "datei": "laura-export.mp4"}
    assert (ablage.ordner(a.id) / "laura-export.mp4").read_bytes() == b"MP4"
    assert [x for x in laura.aufrufe if x[0] == "rendern"][0][2] is None
    assert w.auftrag_zeigen(a.id)["dateien"] == ["laura-export.mp4"]


def test_render_ausfuehren_reel_optionen(w, ablage, laura, tmp_path):
    (tmp_path / "reels").mkdir()
    (tmp_path / "reels" / "reel-tl1.json").write_text('{"format": "9:16"}', encoding="utf-8")
    a = ablage.anlegen("laura_render", {"timeline_id": "tl1"}, "mcp")
    assert w.render_ausfuehren(a)["ok"] is True
    assert [x for x in laura.aufrufe if x[0] == "rendern"][0][2] == {"format": "9:16"}
    # Kein Objekt oder kaputt: einfacher Render
    (tmp_path / "reels" / "reel-tl2.json").write_text("[1]", encoding="utf-8")
    b = ablage.anlegen("laura_render", {"timeline_id": "tl2"}, "mcp")
    w.render_ausfuehren(b)
    assert [x for x in laura.aufrufe if x[0] == "rendern"][1][2] is None


def test_render_ausfuehren_ungueltige_zeitleiste(w, ablage, laura):
    a = ablage.anlegen("laura_render", {"timeline_id": "../x"}, "mcp")
    assert w.render_ausfuehren(a)["ok"] is False
    assert laura.namen() == []


def test_render_ausfuehren_ohne_groesse_kein_download(w, ablage, laura):
    for export in ({"ok": True, "url": "/u"}, {"ok": True, "url": "/u", "size_bytes": None}):
        laura.export = export
        a = ablage.anlegen("laura_render", {"timeline_id": "tl1"}, "mcp")
        r = w.render_ausfuehren(a)
        assert r["ok"] is False and "unbekannt" in r["fehler"]
    assert "herunterladen" not in laura.namen()


def test_render_legt_musik_ein_dann_rendert(w, ablage, laura, tmp_path):
    """I1(b): mit musik_auftrag legt der Render die Musik zuerst ein, dann rendert er."""
    m = _fertiges_video(ablage, tmp_path)
    m.eingabe = {"wunsch": "x", "laura_timeline_id": "tl1"}
    ablage.speichern(m)
    a = ablage.anlegen("laura_render", {"timeline_id": "tl1", "musik_auftrag": m.id}, "telegram")
    r = w.render_ausfuehren(a)
    assert r["ok"] is True, r
    wichtig = [n for n in laura.namen() if n in ("importieren", "clip_anlegen", "rendern")]
    assert wichtig == ["importieren", "clip_anlegen", "rendern"]
    # ein zweiter Lauf (z. B. nach Neustart) legt dank der Marke nichts doppelt an
    laura.aufrufe.clear()
    assert w.render_ausfuehren(a)["ok"] is True
    assert "importieren" not in laura.namen() and "clip_anlegen" not in laura.namen()


@pytest.mark.parametrize("zustand", ["gescheitert", "abgebrochen"])
def test_render_scheitert_wenn_musik_scheitert(w, ablage, laura, tmp_path, zustand):
    """I1(b): gescheiterte/abgebrochene Musik -> kein Render ohne Musik."""
    m = _fertiges_video(ablage, tmp_path, zustand=zustand)
    m.grund = "kein Song passt"
    ablage.speichern(m)
    a = ablage.anlegen("laura_render", {"timeline_id": "tl1", "musik_auftrag": m.id}, "telegram")
    r = w.render_ausfuehren(a)
    assert r["ok"] is False
    assert r["fehler"].startswith(f"Musik-Auftrag {m.id} {zustand}")
    assert "kein Song passt" in r["fehler"]
    assert "rendern" not in laura.namen()


def test_render_scheitert_wenn_einlegen_scheitert(w, ablage, laura, tmp_path):
    m = _fertiges_video(ablage, tmp_path)
    laura.zeit.update(frames=1200)          # Musik passt nicht
    a = ablage.anlegen("laura_render", {"timeline_id": "tl1", "musik_auftrag": m.id}, "telegram")
    r = w.render_ausfuehren(a)
    assert r["ok"] is False and r["fehler"].startswith(f"Musik-Auftrag {m.id}")
    assert "passt nicht" in r["fehler"]
    assert "rendern" not in laura.namen()


# ---------------------------------------------------------------- Lautstaerke (I2)


def _gain(laura):
    return [x for x in laura.aufrufe if x[0] == "clip_anlegen"][0][2]["gain_percent"]


def test_gain_25_bei_sprache(w, ablage, laura, tmp_path):
    a = _fertiges_video(ablage, tmp_path)
    laura.clips = [{"id": "v1", "label": "Sprecher"}]          # Audio, das keine Musik ist
    assert w.in_laura_legen(a.id, "tl1")["ok"] is True
    assert _gain(laura) == 25
    # Reel-Datei des Video-Claws reicht auch ohne Audio-Clip
    laura.aufrufe.clear()
    laura.clips = []
    (tmp_path / "reels").mkdir()
    (tmp_path / "reels" / "reel-tl2.json").write_text("{}", encoding="utf-8")
    assert w.in_laura_legen(a.id, "tl2")["ok"] is True
    assert _gain(laura) == 25


def test_gain_100_ohne_audio(w, ablage, laura, tmp_path):
    a = _fertiges_video(ablage, tmp_path)
    laura.clips = [{"id": "m0", "label": "Alt · musik:20261101-101010-video-abcd"}]  # nur Musik
    assert w.in_laura_legen(a.id, "tl1")["ok"] is True
    assert _gain(laura) == 100


@pytest.mark.parametrize("wunsch, erwartet", [(80, 80), (0, 0), (500, 400), (-5, 0)])
def test_gain_explizit_bleibt(w, ablage, laura, tmp_path, wunsch, erwartet):
    a = _fertiges_video(ablage, tmp_path)
    laura.clips = [{"id": "v1", "label": "Sprecher"}]
    assert w.in_laura_legen(a.id, "tl1", gain_percent=wunsch)["ok"] is True
    assert _gain(laura) == erwartet          # Laura erlaubt 0-400; geklemmt, nicht ersetzt


# ---------------------------------------------------------------- Zeitleiste der Musik (I5)


def test_in_laura_fremde_zeitleiste_ohne_laura_aufruf(w, ablage, laura, tmp_path):
    a = _fertiges_video(ablage, tmp_path)
    a.eingabe = {"wunsch": "x", "laura_timeline_id": "tlA"}
    ablage.speichern(a)
    r = w.in_laura_legen(a.id, "tlB")
    assert r == {"ok": False, "fehler": "Musik-Auftrag gehoert zu einer anderen Zeitleiste"}
    assert laura.namen() == []
    # derselbe Fehler schon beim Bestellen des Renders, nicht erst im Laeufer
    assert w.laura_rendern("tlB", musik_auftrag=a.id)["fehler"] == r["fehler"]
    assert w.laura_rendern("tlA", musik_auftrag=a.id)["ok"] is True
    assert w.in_laura_legen(a.id, "tlA")["ok"] is True


# ---------------------------------------------------------------- Kleinkram (M1, M3)


def test_auftrag_zeigen_set_zeigt_qa_bericht(w, ablage, tmp_path):
    lauf = tmp_path / "runs" / "set-lauf-01"
    lauf.mkdir(parents=True)
    (lauf / "qa-report.md").write_text(
        "# QA\n" + "\n".join(f"zeile {i} {tmp_path}" for i in range(60)), encoding="utf-8")
    (lauf / "tracklist.md").write_text("# Tracklist\n", encoding="utf-8")
    a = ablage.anlegen("set", {"wunsch": "x", "length": 5}, "mcp")
    a.zustand, a.lauf = "fertig", "set-lauf-01"
    ablage.speichern(a)
    r = w.auftrag_zeigen(a.id)
    assert r["bericht"].startswith("# QA")
    assert len(r["bericht"].splitlines()) == 40
    assert str(tmp_path) not in r["bericht"]
    (lauf / "qa-report.md").unlink()
    assert w.auftrag_zeigen(a.id)["bericht"] == "# Tracklist"


def test_laura_fehler_ohne_link(w, ablage, laura, tmp_path, monkeypatch):
    a = _fertiges_video(ablage, tmp_path)

    def import_kaputt(project_id, url, name):
        return {"ok": False, "fehler": f"Laura HTTP 400: kann {url} nicht laden (Basis {BASIS})"}
    monkeypatch.setattr(laura, "importieren", import_kaputt)
    r = w.in_laura_legen(a.id, "tl1")
    assert r["ok"] is False and "<link>" in r["fehler"]
    assert BASIS not in r["fehler"] and "ablauf=" not in r["fehler"] and "link.test" not in r["fehler"]


def test_render_ausfuehren_zu_gross_kein_download(w, ablage, laura):
    laura.export = {"ok": True, "url": "/u", "size_bytes": 500 * 1024 * 1024 + 1}
    a = ablage.anlegen("laura_render", {"timeline_id": "tl1"}, "mcp")
    r = w.render_ausfuehren(a)
    assert r["ok"] is False and "500" in r["fehler"]
    assert "herunterladen" not in laura.namen()
