import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from djset import cli
from djset.agents.base import Budget
from djset.bestand import Bestand, SongEintrag
from djset.video.befehl import cmd_video
from djset.video.laenge import aus_frames, aus_sekunden, aus_video
from djset.video.vertrag import VideomusikErgebnis
from tests.fixtures.fake_client import ZaehlenderClient
from tests.fixtures.make_audio import click_track
from tests.fixtures.make_video import kurzes_video
from tests.fixtures.trenner import HalbTrenner, TonTrenner

HEUTE = date(2026, 11, 23)
SR = 44_100


def _wahl(song_id: str = "a", gesang: bool = False) -> str:
    return json.dumps(
        {
            "song_id": song_id,
            "einstieg": "anfang",
            "gesang": gesang,
            "begruendung": "passt zum Wunsch",
        }
    )


def _bestand(
    tmp_path: Path,
    ids: tuple[str, ...] = ("a",),
    intro_leise_s: float = 0.0,
    mit_sinus: bool = True,
) -> Path:
    """Songs aus Click-Tracks (126 BPM, 16 Takte, rund 30 s) ohne Messung:
    der Befehl misst sie selbst nach."""
    daten = tmp_path / "daten"
    bestand = Bestand(daten)
    for song_id in ids:
        wav = bestand.songs / f"{song_id}.wav"
        click_track(wav, bpm=126.0, bars=16)
        # Ein Clicktrack allein ist zu spitz: auf -14 LUFS gebracht, risse er
        # den True-Peak-Deckel. Ein tiefer Ton darunter macht ihn pegelbar,
        # die Klicks tragen weiter das Raster.
        signal, rate = sf.read(wav, dtype="float64")
        if mit_sinus:
            signal = signal + 0.6 * np.sin(2 * np.pi * 200.0 * np.arange(len(signal)) / rate)
        signal = signal / np.max(np.abs(signal))
        if intro_leise_s:
            # Fast leeres Intro (-50 dB) mit einem lauten Schlag kurz vor seinem Ende:
            # kein Fenster, das dort beginnt, kann den Zielpegel erreichen.
            signal[: round(intro_leise_s * rate)] *= 0.003
            schlag = round((intro_leise_s - 0.6) * rate)
            signal[schlag: schlag + 20] = 0.9
        sf.write(wav, signal, rate, subtype="FLOAT")
        eintrag = SongEintrag(
            id=song_id,
            titel=song_id,
            datei=f"{song_id}.wav",
            herkunft="suno-eigen",
            aufgenommen_am=HEUTE,
            pruefsumme=song_id.ljust(64, "0"),
        )
        (bestand.songs / f"{song_id}.json").write_text(
            eintrag.model_dump_json(indent=2), encoding="utf-8"
        )
    return daten


@pytest.fixture
def budget() -> Budget:
    return Budget(max_tokens=400_000, max_seconds=300.0)


@pytest.fixture(autouse=True)
def ohne_demucs(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_trenner", lambda: None)


def _lauf(daten: Path) -> Path:
    laeufe = sorted((daten / "runs").iterdir())
    assert len(laeufe) == 1
    return laeufe[0]


def _video(daten: Path, client, budget, ziel, **kwargs) -> int:
    angaben = dict(
        songs=None,
        gesang=None,
        lufs_ziel=-14.0,
        video=None,
        unterlegen_db=None,
        original_ersetzen=False,
    )
    angaben.update(kwargs)
    return cmd_video(
        daten,
        "ruhig",
        ziel,
        angaben["songs"],
        angaben["gesang"],
        angaben["lufs_ziel"],
        angaben["video"],
        angaben["unterlegen_db"],
        angaben["original_ersetzen"],
        client,
        budget,
        HEUTE,
    )


def test_video_aus_sekunden_ergibt_exakte_datei_und_vertrag(tmp_path, budget):
    daten = _bestand(tmp_path)
    client = ZaehlenderClient([_wahl()])

    code = _video(daten, client, budget, aus_sekunden(12))

    assert code == 0
    lauf = _lauf(daten)
    assert lauf.name == "2026-11-23-video-ruhig-01"
    info = sf.info(lauf / "videomusik.wav")
    assert info.frames == round(12 * SR)
    ergebnis = VideomusikErgebnis.model_validate_json(
        (lauf / "videomusik.json").read_text(encoding="utf-8")
    )
    assert ergebnis.laura is None
    assert ergebnis.samples == round(12 * SR)
    assert ergebnis.herkunft == "suno-eigen"
    for name in ("ziel.json", "wahl.json", "zuschnitt.json", "videomusik.mp3", "bericht.md"):
        assert (lauf / name).is_file(), name
    assert "suno-eigen" in (lauf / "bericht.md").read_text(encoding="utf-8")


def test_video_aus_frames_traegt_den_laura_block(tmp_path, budget):
    daten = _bestand(tmp_path)

    code = _video(daten, ZaehlenderClient([_wahl()]), budget, aus_frames(360, "30"))

    assert code == 0
    ergebnis = VideomusikErgebnis.model_validate_json(
        (_lauf(daten) / "videomusik.json").read_text(encoding="utf-8")
    )
    assert ergebnis.laura is not None
    assert ergebnis.laura.seq_out_frame_exclusive == 360
    assert (ergebnis.frames, ergebnis.fps_num, ergebnis.fps_den) == (360, 30, 1)


def test_gesang_aus_rendert_die_begleitung(tmp_path, budget, monkeypatch):
    daten = _bestand(tmp_path)
    monkeypatch.setattr(cli, "_trenner", lambda: TonTrenner())

    code = _video(daten, ZaehlenderClient([_wahl(gesang=False)]), budget, aus_sekunden(12))

    assert code == 0
    lauf = _lauf(daten)
    signal, sr = sf.read(lauf / "videomusik.wav", dtype="float64", always_2d=True)
    spektrum = np.abs(np.fft.rfft(signal[:, 0]))
    spitze_hz = np.argmax(spektrum) * sr / len(signal)
    assert abs(spitze_hz - 440.0) < 5.0
    ergebnis = VideomusikErgebnis.model_validate_json(
        (lauf / "videomusik.json").read_text(encoding="utf-8")
    )
    assert ergebnis.gesang is False


def test_mit_gesang_schlaegt_das_modell_und_rendert_den_song(tmp_path, budget, monkeypatch):
    daten = _bestand(tmp_path)
    monkeypatch.setattr(cli, "_trenner", lambda: TonTrenner())

    code = _video(
        daten, ZaehlenderClient([_wahl(gesang=False)]), budget, aus_sekunden(12), gesang=True
    )

    assert code == 0
    ergebnis = VideomusikErgebnis.model_validate_json(
        (_lauf(daten) / "videomusik.json").read_text(encoding="utf-8")
    )
    assert ergebnis.gesang is True


def test_ohne_getrennte_songs_und_offen_rendert_mit_gesang_und_sagt_warum(tmp_path, budget):
    daten = _bestand(tmp_path)

    code = _video(daten, ZaehlenderClient([_wahl(gesang=False)]), budget, aus_sekunden(12))

    assert code == 0
    lauf = _lauf(daten)
    ergebnis = VideomusikErgebnis.model_validate_json(
        (lauf / "videomusik.json").read_text(encoding="utf-8")
    )
    assert ergebnis.gesang is True
    assert "mit Gesang" in (lauf / "bericht.md").read_text(encoding="utf-8")


def test_zu_lang_fuer_jeden_song_bricht_vor_dem_modell_ab(tmp_path, budget, capsys):
    daten = _bestand(tmp_path)
    client = ZaehlenderClient([])

    code = _video(daten, client, budget, aus_sekunden(600))

    assert code == 1
    assert client.aufrufe == 0
    err = capsys.readouterr().err
    assert "tragbare" in err and " s" in err
    assert not (daten / "runs").exists()


def test_ohne_getrennte_songs_und_ohne_demucs_bricht_ab(tmp_path, budget, capsys):
    daten = _bestand(tmp_path)
    client = ZaehlenderClient([])

    code = _video(daten, client, budget, aus_sekunden(12), gesang=False)

    assert code == 1
    assert client.aufrufe == 0
    assert "pip install" in capsys.readouterr().err
    assert not (daten / "runs").exists()


def test_ohne_gesang_nimmt_nur_getrennte_kandidaten(tmp_path, budget, monkeypatch):
    daten = _bestand(tmp_path, ("a", "b"))
    # Nur "a" wird getrennt: der Trenner scheitert fuer "b".
    bestand = Bestand(daten)
    bestand.messen("a")
    bestand.messen("b")
    bestand.trennen("a", HalbTrenner())
    client = ZaehlenderClient([_wahl("a")])

    code = _video(daten, client, budget, aus_sekunden(12), gesang=False)

    assert code == 0
    assert "| b |" not in client.prompts[0]
    assert "| a |" in client.prompts[0]


def test_unterlegen_schreibt_neue_datei_und_laesst_das_original(tmp_path, budget):
    daten = _bestand(tmp_path)
    (tmp_path / "video").mkdir()
    video = kurzes_video(tmp_path / "video", sekunden=4, fps=25)
    vorher = hashlib.sha256(video.read_bytes()).hexdigest()

    code = _video(
        daten,
        ZaehlenderClient([_wahl()]),
        budget,
        aus_video(video),
        video=video,
        unterlegen_db=-6.0,
    )

    assert code == 0
    lauf = _lauf(daten)
    assert (lauf / "video-mit-musik.mp4").is_file()
    assert hashlib.sha256(video.read_bytes()).hexdigest() == vorher
    ergebnis = VideomusikErgebnis.model_validate_json(
        (lauf / "videomusik.json").read_text(encoding="utf-8")
    )
    assert ergebnis.laura is not None and ergebnis.laura.seq_out_frame_exclusive == 100


def test_unterlegen_ohne_video_ist_ein_fehler(tmp_path, budget, capsys):
    daten = _bestand(tmp_path)

    code = _video(
        daten, ZaehlenderClient([]), budget, aus_sekunden(12), unterlegen_db=-6.0
    )

    assert code == 1
    assert "--unterlegen braucht --video" in capsys.readouterr().err
    assert not (daten / "runs").exists()


def test_main_unterlegen_ohne_video_ist_exit_1(tmp_path, monkeypatch, capsys):
    daten = _bestand(tmp_path)
    monkeypatch.setenv("MUSIK_DATEN", str(daten))

    code = cli.main(["video", "--wunsch", "x", "--sekunden", "12", "--unterlegen"])

    assert code == 1
    assert "--unterlegen braucht --video" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argumente, text",
    [
        (["--frames", "100", "--fps", "abc"], "Bildrate"),
        (["--frames", "100"], "--fps"),
        (["--video", "gibt-es-nicht.mp4"], "nicht gefunden"),
    ],
)
def test_main_video_ungueltige_laenge_ist_exit_1_ohne_traceback(
    tmp_path, monkeypatch, capsys, argumente, text
):
    monkeypatch.setenv("MUSIK_DATEN", str(tmp_path))

    code = cli.main(["video", "--wunsch", "x", *argumente])

    assert code == 1
    err = capsys.readouterr().err
    assert text in err and "Traceback" not in err


def test_main_video_reicht_die_argumente_durch(tmp_path, monkeypatch):
    gesehen: dict[str, object] = {}

    def fake_video(
        daten, wunsch, ziel, songs, gesang, lufs_ziel, video, unterlegen_db,
        original_ersetzen, client, budget, today,
    ) -> int:
        gesehen.update(
            daten=daten, wunsch=wunsch, ziel=ziel, songs=songs, gesang=gesang,
            lufs=lufs_ziel, video=video, unterlegen_db=unterlegen_db,
            original_ersetzen=original_ersetzen, today=today,
        )
        return 0

    monkeypatch.setattr("djset.video.befehl.cmd_video", fake_video)
    monkeypatch.setenv("MUSIK_DATEN", str(tmp_path))

    code = cli.main(
        ["video", "--wunsch", "Sommer", "--frames", "360", "--fps", "30000/1001",
         "--songs", "a, b,"]
    )

    assert code == 0
    assert gesehen["daten"] == tmp_path
    assert gesehen["wunsch"] == "Sommer"
    ziel = gesehen["ziel"]
    assert (ziel.frames, ziel.fps_num, ziel.fps_den) == (360, 30000, 1001)
    assert gesehen["songs"] == ["a", "b"]
    assert gesehen["gesang"] is None
    assert gesehen["lufs"] == -14.0
    assert gesehen["video"] is None
    assert gesehen["unterlegen_db"] is None
    assert gesehen["original_ersetzen"] is False
    assert isinstance(gesehen["today"], date)

    cli.main(["video", "--wunsch", "x", "--sekunden", "12", "--ohne-gesang", "--lufs", "-16"])
    assert gesehen["gesang"] is False and gesehen["lufs"] == -16.0
    cli.main(["video", "--wunsch", "x", "--sekunden", "12", "--mit-gesang"])
    assert gesehen["gesang"] is True

    cli.main(["video", "--wunsch", "x", "--sekunden", "12", "--unterlegen"])
    assert gesehen["unterlegen_db"] == -6.0
    assert gesehen["video"] is None

def test_leises_intro_mit_schlag_wird_uebersprungen(tmp_path, budget):
    daten = _bestand(tmp_path, intro_leise_s=12.0)

    code = _video(daten, ZaehlenderClient([_wahl()]), budget, aus_sekunden(12))

    assert code == 0
    lauf = _lauf(daten)
    zuschnitt = json.loads((lauf / "zuschnitt.json").read_text(encoding="utf-8"))
    # Der erste Taktanfang (nahe 0) laege im leisen Intro: gewaehlt wird ein Start dahinter.
    assert zuschnitt["start_s"] > 3.0
    ergebnis = VideomusikErgebnis.model_validate_json(
        (lauf / "videomusik.json").read_text(encoding="utf-8")
    )
    assert abs(ergebnis.lufs - (-14.0)) <= 1.0
    bericht = (lauf / "bericht.md").read_text(encoding="utf-8")
    assert "kein Ausschnitt von" not in bericht  # der Rueckfall-Hinweis gilt nur ohne tauglichen Start
    assert f"Start auf {zuschnitt['start_s']:.2f} s verschoben" in bericht
    assert "Hinweise" in bericht and "Ab " in bericht  # nennt den verworfenen Start und den Grund
    assert "Luecke (Stille" in bericht or "nicht unter dem Spitzendeckel; Start" in bericht


def test_ohne_tauglichen_ausschnitt_laeuft_der_alte_weg_mit_hinweis(tmp_path, budget):
    # Ein Clicktrack ohne Untergrund ist ueberall zu spitz fuer -14 LUFS.
    daten = _bestand(tmp_path, mit_sinus=False)

    code = _video(daten, ZaehlenderClient([_wahl()]), budget, aus_sekunden(12))

    assert code == 2
    bericht = (_lauf(daten) / "bericht.md").read_text(encoding="utf-8")
    assert "kein Ausschnitt von a erreicht -14.0 LUFS unter dem Spitzendeckel" in bericht
    assert "bester erreichbarer Pegel" in bericht


@pytest.mark.parametrize("zu_kurz_s", [2.0, 0.3])
def test_zu_kurzes_ziel_bricht_vor_dem_modell_ab_und_nennt_eine_gehende_laenge(
    tmp_path, budget, capsys, zu_kurz_s
):
    import re

    daten = _bestand(tmp_path)
    client = ZaehlenderClient([_wahl()])

    code = _video(daten, client, budget, aus_sekunden(zu_kurz_s))

    assert code == 1
    assert client.aufrufe == 0
    assert not (daten / "runs").exists()
    err = capsys.readouterr().err
    treffer = re.search(r"mindestens (\d+\.\d) s", err)
    assert treffer is not None, err
    assert "Traceback" not in err

    # Der genannte Wert geht beim Wiederholen wirklich.
    nochmal = _video(daten, client, budget, aus_sekunden(float(treffer.group(1))))
    assert nochmal in (0, 2)
    assert client.aufrufe == 1


def test_am_deckel_begrenzter_pegel_in_toleranz_ist_kein_hinweis(tmp_path, budget):
    # Ohne Sinus-Untergrund ist der Clicktrack am Deckel; ein Ziel nahe am erreichbaren Pegel
    # liegt trotzdem in Toleranz.
    daten = _bestand(tmp_path, mit_sinus=False)

    code = _video(daten, ZaehlenderClient([_wahl()]), budget, aus_sekunden(12), lufs_ziel=-32.0)

    bericht = (_lauf(daten) / "bericht.md").read_text(encoding="utf-8")
    assert "Der Pegel ist am True-Peak-Deckel begrenzt" not in bericht
    assert "(am Spitzendeckel, in Toleranz)" in bericht
    assert code in (0, 2)
