import math
from pathlib import Path

import pytest

from djset.video.laenge import (
    VideoFehler,
    Ziellaenge,
    aus_frames,
    aus_sekunden,
    aus_video,
)
from tests.fixtures.make_audio import stereo_tone
from tests.fixtures.make_video import kurzes_video


def test_frames_ergeben_die_exakte_dauer() -> None:
    z = aus_frames(900, "30000/1001")
    assert z.sekunden == 900 * 1001 / 30000
    assert z.samples(44100) == round(30.03 * 44100)


def test_aus_sekunden() -> None:
    z = aus_sekunden(12.5)
    assert isinstance(z, Ziellaenge)
    assert z.frames is None
    assert z.samples(1000) == 12500


@pytest.mark.parametrize(
    "aufruf",
    [
        lambda: aus_sekunden(0),
        lambda: aus_sekunden(float("nan")),
        lambda: aus_sekunden(math.inf),
        lambda: aus_frames(0, "30"),
        lambda: aus_frames(10, "abc"),
        lambda: aus_frames(10, "30/0"),
    ],
)
def test_ungueltige_angaben_sind_ein_videofehler(aufruf) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(VideoFehler):
        aufruf()


def test_dauer_eines_videos(tmp_path: Path) -> None:
    z = aus_video(kurzes_video(tmp_path, 2.5, 25))
    assert z.frames in (62, 63)
    assert (z.fps_num, z.fps_den) == (25, 1)
    assert z.frames is not None
    assert z.sekunden == z.frames / 25


def test_video_ohne_ton(tmp_path: Path) -> None:
    z = aus_video(kurzes_video(tmp_path, 1.0, 25, mit_ton=False))
    assert z.frames in (24, 25)


def test_audiodatei_ist_kein_video(tmp_path: Path) -> None:
    wav = tmp_path / "ton.wav"
    stereo_tone(wav, 1.0)
    with pytest.raises(VideoFehler) as e:
        aus_video(wav)
    assert "ton.wav" in str(e.value)
    assert "keine Videospur" in str(e.value)


def test_fehlendes_video(tmp_path: Path) -> None:
    with pytest.raises(VideoFehler) as e:
        aus_video(tmp_path / "weg.mp4")
    assert "weg.mp4" in str(e.value)


def test_unlesbare_datei(tmp_path: Path) -> None:
    kaputt = tmp_path / "kaputt.mp4"
    kaputt.write_bytes(b"das ist kein video")
    with pytest.raises(VideoFehler) as e:
        aus_video(kaputt)
    assert "kaputt.mp4" in str(e.value)


def test_ffprobe_fehlt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from djset.render.stretch import RenderToolMissing

    datei = tmp_path / "v.mp4"
    datei.write_bytes(b"x")
    monkeypatch.setattr("djset.render.stretch.shutil.which", lambda _n: None)
    with pytest.raises(RenderToolMissing):
        aus_video(datei)


def test_audiodatei_mit_cover_ist_kein_video(tmp_path: Path) -> None:
    import subprocess

    from djset.render.stretch import ffmpeg_path

    mp3 = tmp_path / "mit_cover.mp3"
    subprocess.run(
        [
            ffmpeg_path(), "-v", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-f", "lavfi", "-i", "color=c=red:size=64x64:rate=1:duration=1",
            "-map", "0:a", "-map", "1:v", "-frames:v", "1", "-c:a", "libmp3lame",
            "-c:v", "mjpeg", "-disposition:v", "attached_pic", str(mp3),
        ],
        check=True, capture_output=True,
    )
    with pytest.raises(VideoFehler) as e:
        aus_video(mp3)
    assert "keine Videospur" in str(e.value)


def test_haengendes_ffprobe_ist_ein_videofehler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    datei = tmp_path / "v.mp4"
    datei.write_bytes(b"x")

    def haengt(*_a: object, **_k: object) -> None:
        raise subprocess.TimeoutExpired("ffprobe", 60)

    monkeypatch.setattr("djset.video.laenge.subprocess.run", haengt)
    with pytest.raises(VideoFehler, match="v.mp4"):
        aus_video(datei)
