"""Render-Huelle fuer die Video-Werkbank: Argumente und Fehler ohne HyperFrames, Probe-Render nur wenn eingerichtet."""
import re
import subprocess
from pathlib import Path

import pytest

from spaces.marketing.workers import video_rendern


def test_render_argumente_ohne_netzinstallation():
    a = video_rendern.render_argumente(Path("k/index.html"), Path("aus.mp4"))
    assert a[:4] == ["npx", "--no-install", "hyperframes", "render"] and a[a.index("--fps") + 1] == "30"


def test_normal_argumente_sind_fuer_concat_gleichfoermig():
    a = " ".join(video_rendern.normal_argumente(Path("a.mp4"), Path("b.mp4")))
    assert "yuv420p" in a and "-r 30" in a and "-ar 48000" in a and "+faststart" in a
    assert "libx264" in a and "aac" in a and "-ac 2" in a and "1920" in a and "1080" in a


def test_normal_argumente_mit_ton_nimmt_tonspur_aus_wav():
    a = video_rendern.normal_argumente(Path("a.mp4"), Path("b.mp4"), Path("ton.wav"))
    assert a.count("-i") == 2 and "-map" in a and "1:a:0" in a


def test_render_fehler_ohne_pfad_im_text(monkeypatch, tmp_path):
    def fake_run(args, **kw):
        kw["stdout"].write(rb"Fehler in C:\Users\geheim\x.html")
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(video_rendern.subprocess, "run", fake_run)
    with pytest.raises(video_rendern.RenderFehler) as e:
        video_rendern.rendern(tmp_path / "index.html", tmp_path / "aus.mp4")
    assert "C:\\" not in e.value.grund and "\\" not in e.value.grund and e.value.grund


def test_render_setzt_do_not_track_und_nutzt_datei_statt_pipe(monkeypatch, tmp_path):
    gesehen = {}

    def fake_run(args, **kw):
        gesehen.update(kw)
        (tmp_path / "aus.mp4").write_bytes(b"x")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(video_rendern.subprocess, "run", fake_run)
    video_rendern.rendern(tmp_path / "index.html", tmp_path / "aus.mp4")
    assert gesehen["env"]["DO_NOT_TRACK"] == "1"
    assert gesehen["stdout"] is not subprocess.PIPE and not re.search("PIPE", repr(gesehen.get("stderr")))


@pytest.mark.skipif(not video_rendern.eingerichtet(), reason="HyperFrames nicht installiert")
def test_probe_render_zehn_sekunden_mit_ton(tmp_path):
    probe = video_rendern.WERKBANK / "probe"
    if not (probe / "ton.wav").exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
                        "-ar", "48000", str(probe / "ton.wav")], check=True)
    video_rendern.rendern(probe / "index.html", tmp_path / "p.mp4")
    w = video_rendern.pruefen(tmp_path / "p.mp4")
    assert (w["breite"], w["hoehe"], w["fps"]) == (1920, 1080, 30)
    assert abs(w["dauer_s"] - 10) < 0.3 and w["ton"] is True
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(tmp_path / "p.mp4"), "-af", "volumedetect", "-vn",
                        "-f", "null", "-"], capture_output=True, text=True)
    pegel = float(re.search(r"mean_volume: (-?[\d.]+) dB", r.stderr).group(1))
    assert pegel > -50
