import pytest
from pydantic import ValidationError

from djset.video.laenge import VideoFehler
from djset.video.vertrag import LauraClip, VideomusikErgebnis, laura_clip


def test_laura_clip_traegt_frames():
    clip = laura_clip(900, 30000, 1001, "Videomusik")
    assert clip.seq_out_frame_exclusive == 900
    assert clip.fade_out_frames == 0
    assert clip.ducking_percent == 100
    ergebnis = VideomusikErgebnis(
        datei="videomusik.wav", mp3=None, sekunden=30.03, samples=1_324_323,
        sample_rate=44_100, frames=900, fps_num=30000, fps_den=1001, song_id="a",
        titel="A", herkunft="suno-eigen", start_im_song_s=1.0, schluss_s=29.0,
        takte=15, bpm=124.0, gesang=False, lufs=-14.0, true_peak=-1.5, laura=clip,
    )
    assert VideomusikErgebnis.model_validate_json(ergebnis.model_dump_json()) == ergebnis


def test_laura_clip_grenzen():
    with pytest.raises(ValidationError):
        LauraClip(seq_out_frame_exclusive=0, label="x")
    with pytest.raises(ValidationError):
        LauraClip(seq_out_frame_exclusive=10, gain_percent=401, label="x")
    with pytest.raises(ValidationError):
        LauraClip(seq_out_frame_exclusive=10, ducking_percent=101, label="x")
    with pytest.raises(VideoFehler):
        laura_clip(10, 0, 1001, "x")
