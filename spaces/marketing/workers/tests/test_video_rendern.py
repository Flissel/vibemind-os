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


class FakeProzess:
    """Ersetzt subprocess.Popen: schreibt optional in die Ausgabedatei, endet mit `code` oder laeuft in den Timeout."""
    letzter = None

    def __init__(self, args, **kw):
        self.args, self.kw, self.pid = args, kw, 4711
        FakeProzess.letzter = self
        self.code, self.haengt, self.text = 0, False, b""

    def wait(self, timeout=None):
        if self.haengt:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if self.text:
            self.kw["stdout"].write(self.text)
        return self.code


def _fake(monkeypatch, code=0, haengt=False, text=b"", ziel=None):
    def bauen(args, **kw):
        p = FakeProzess(args, **kw)
        p.code, p.haengt, p.text = code, haengt, text
        if ziel is not None and code == 0 and not haengt:
            ziel.write_bytes(b"x")
        return p

    monkeypatch.setattr(video_rendern.subprocess, "Popen", bauen)
    monkeypatch.setattr(video_rendern.shutil, "which", lambda n: n)
    aufrufe = []
    monkeypatch.setattr(video_rendern, "_baum_beenden", lambda pid: aufrufe.append(pid))
    return aufrufe


def test_render_fehler_ohne_pfad_im_text(monkeypatch, tmp_path):
    _fake(monkeypatch, code=1, text=rb"Fehler in C:\Users\geheim\x.html")
    with pytest.raises(video_rendern.RenderFehler) as e:
        video_rendern.rendern(tmp_path / "index.html", tmp_path / "aus.mp4")
    assert "C:" not in e.value.grund and chr(92) not in e.value.grund and e.value.grund


def test_render_setzt_do_not_track_und_nutzt_datei_statt_pipe(monkeypatch, tmp_path):
    _fake(monkeypatch, ziel=tmp_path / "aus.mp4")
    video_rendern.rendern(tmp_path / "index.html", tmp_path / "aus.mp4")
    kw = FakeProzess.letzter.kw
    assert kw["env"]["DO_NOT_TRACK"] == "1"
    assert kw["stdout"] is not subprocess.PIPE and kw["stderr"] == subprocess.STDOUT


def test_timeout_beendet_den_prozessbaum_ueber_die_pid(monkeypatch, tmp_path):
    beendet = _fake(monkeypatch, haengt=True)
    with pytest.raises(video_rendern.RenderFehler) as e:
        video_rendern.rendern(tmp_path / "index.html", tmp_path / "aus.mp4", zeitlimit_s=1)
    assert beendet == [4711]
    assert "C:" not in e.value.grund and chr(92) not in e.value.grund and "Zeitlimit" in e.value.grund


def test_baum_kill_nutzt_taskkill_mit_pid_ohne_namensmuster(monkeypatch):
    gesehen = []
    monkeypatch.setattr(video_rendern.os, "name", "nt")
    monkeypatch.setattr(video_rendern.subprocess, "run", lambda a, **kw: gesehen.append(a))
    video_rendern._baum_beenden(4711)
    assert gesehen == [["taskkill", "/T", "/F", "/PID", "4711"]]


def test_abbruch_waehrend_des_wartens_beendet_ebenfalls_den_baum(monkeypatch, tmp_path):
    beendet = _fake(monkeypatch)

    def abbruch(self, timeout=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(FakeProzess, "wait", abbruch)
    with pytest.raises(KeyboardInterrupt):
        video_rendern.rendern(tmp_path / "index.html", tmp_path / "aus.mp4")
    assert beendet == [4711]


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
