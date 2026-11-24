"""Laura-Client des Musik-Dienstes: kein Netz, `laura._roh` wird ersetzt."""
import json

import pytest

from djset.dienst import laura


@pytest.fixture(autouse=True)
def _umgebung(monkeypatch):
    monkeypatch.setenv("LAURA_API_URL", "http://laura.test:8765")
    monkeypatch.setenv("LAURA_TOKEN", "geheim123")


def _antwort(daten, status=200):
    return status, json.dumps(daten).encode()


def _router(monkeypatch, routen, aufrufe=None):
    """routen: {(METHODE, pfad): antwort | [antworten] | callable}."""
    def roh(methode, url, daten, kopf):
        pfad = url.split("laura.test:8765", 1)[-1]
        if aufrufe is not None:
            aufrufe.append((methode, pfad, daten, dict(kopf)))
        wert = routen[(methode, pfad)]
        if isinstance(wert, list):
            return wert.pop(0) if len(wert) > 1 else wert[0]
        return wert(daten) if callable(wert) else wert
    monkeypatch.setattr(laura, "_roh", roh)


def test_token_steht_in_keinem_fehler(monkeypatch):
    def roh(methode, url, daten, kopf):
        raise OSError("geheim123 kaputt")
    monkeypatch.setattr(laura, "_roh", roh)
    r = laura.anfrage("GET", "/projects")
    assert r["ok"] is False
    assert "geheim123" not in r["fehler"]
    assert len(r["fehler"]) <= 300


def test_token_header_wird_gesendet(monkeypatch):
    aufrufe = []
    _router(monkeypatch, {("GET", "/x"): _antwort({})}, aufrufe)
    laura.anfrage("GET", "/x")
    assert aufrufe[0][3]["X-Laura-Token"] == "geheim123"


def test_zeitleiste_liest_frames_und_bildrate(monkeypatch):
    clips = [{"seq_out_frame_exclusive": 300}, {"seq_out_frame_exclusive": 900}]
    _router(monkeypatch, {
        ("GET", "/timelines/t1"): _antwort({"id": "t1", "project_id": "p1", "name": "Schnitt", "clips": clips}),
        ("GET", "/projects/p1"): _antwort({"id": "p1", "sequence_rate_num": 30000, "sequence_rate_den": 1001}),
    })
    r = laura.zeitleiste("t1")
    assert r["ok"] is True
    assert r["frames"] == 900
    assert (r["fps_num"], r["fps_den"]) == (30000, 1001)
    assert r["project_id"] == "p1" and r["name"] == "Schnitt"


def test_videos_suchen_findet_teilwort_ohne_gross_klein(monkeypatch):
    _router(monkeypatch, {
        ("GET", "/projects?limit=200"): _antwort([
            {"id": "p1", "name": "Sommerfest Reel"}, {"id": "p2", "name": "Produkt"}]),
        ("GET", "/projects/p1/timelines"): _antwort([{"id": "t1", "name": "Schnitt 1", "clips": [
            {"seq_out_frame_exclusive": 600}]}]),
        ("GET", "/projects/p1"): _antwort({"sequence_rate_num": 30, "sequence_rate_den": 1}),
        ("GET", "/projects/p1/exports"): _antwort([
            {"id": "e1", "timeline_id": "t1", "status": "ready", "format": "mp4"}]),
        ("GET", "/projects/p2/timelines"): _antwort([{"id": "t2", "name": "Cut", "clips": []}]),
    })
    r = laura.videos_suchen("sommer")
    assert r["ok"] is True
    assert len(r["treffer"]) == 1
    t = r["treffer"][0]
    assert t["project_id"] == "p1" and t["timeline_id"] == "t1"
    assert t["projekt"] == "Sommerfest Reel" and t["zeitleiste"] == "Schnitt 1"
    assert t["sekunden"] == 20.0
    assert t["letzter_export"]["export_id"] == "e1"


def test_asset_warten_verlangt_ton(monkeypatch):
    _router(monkeypatch, {
        ("GET", "/assets/a1"): _antwort({"id": "a1", "sha256": "abc", "type": "video", "codec_audio": None}),
        ("GET", "/assets/a1/import-status"): _antwort({"phase": "done"}),
    })
    zeit = [0.0]
    r = laura.asset_warten("a1", max_s=10, takt_s=5,
                           schlafen=lambda s: zeit.__setitem__(0, zeit[0] + s), uhr=lambda: zeit[0])
    assert r["ok"] is False
    assert "kein Ton" in r["fehler"]


def test_asset_warten_ok_mit_audio(monkeypatch):
    _router(monkeypatch, {("GET", "/assets/a1"): _antwort({"sha256": "abc", "type": "audio"})})
    r = laura.asset_warten("a1")
    assert r["ok"] is True and r["asset"]["sha256"] == "abc"


def test_importieren_schickt_url(monkeypatch):
    aufrufe = []
    _router(monkeypatch, {("POST", "/projects/p1/assets/import"):
                          _antwort({"asset_id": "a9", "job_id": "j1"}, 202)}, aufrufe)
    r = laura.importieren("p1", "https://x.test/a.wav", "Videomusik")
    assert r == {"ok": True, "asset_id": "a9"}
    assert json.loads(aufrufe[0][2]) == {"source_url": "https://x.test/a.wav", "display_name": "Videomusik"}


def test_clip_anlegen_und_liste(monkeypatch):
    aufrufe = []
    _router(monkeypatch, {
        ("POST", "/timelines/t1/audio-clips"): _antwort({"id": "c1"}, 201),
        ("GET", "/timelines/t1/audio-clips"): _antwort([{"id": "c1"}]),
    }, aufrufe)
    assert laura.clip_anlegen("t1", {"asset_id": "a1"}) == {"ok": True, "clip": {"id": "c1"}}
    assert laura.audio_clips("t1") == {"ok": True, "clips": [{"id": "c1"}]}


def test_rendern_mit_reel_optionen_nimmt_render_reel(monkeypatch):
    aufrufe = []
    _router(monkeypatch, {("POST", "/timelines/t1/render-reel"): _antwort({"export_id": "e3", "job_id": "j3"}, 202)},
            aufrufe)
    opt = {"vertical": True, "hook_text": "Hallo", "captions": False}
    r = laura.rendern("t1", reel_optionen=opt)
    assert r == {"ok": True, "export_id": "e3", "job_id": "j3"}
    assert aufrufe[0][1] == "/timelines/t1/render-reel"
    assert json.loads(aufrufe[0][2]) == opt


def test_rendern_ohne_optionen_einfacher_render(monkeypatch):
    aufrufe = []
    _router(monkeypatch, {("POST", "/timelines/t1/render"): _antwort({"export_id": "e2", "job_id": "j2"}, 202)},
            aufrufe)
    assert laura.rendern("t1")["ok"] is True
    assert aufrufe[0][1] == "/timelines/t1/render"


def _weiterleiten(ziel):
    import urllib.request
    req = urllib.request.Request("http://laura.test:8765/exports/e1/download-url",
                                 headers={"X-Laura-Token": "geheim123"})
    h = laura._WeiterleitungOhneToken()
    return h.redirect_request(req, None, 302, "Found", {}, ziel)


def test_weiterleitung_fremder_host_ohne_token():
    neu = _weiterleiten("http://speicher.test/x")
    assert neu is not None
    assert "x-laura-token" not in {k.lower() for k in neu.headers} | {k.lower() for k in neu.unredirected_hdrs}


def test_weiterleitung_anderes_schema_ohne_token():
    neu = _weiterleiten("https://laura.test:8765/x")
    assert "x-laura-token" not in {k.lower() for k in neu.headers} | {k.lower() for k in neu.unredirected_hdrs}


def test_weiterleitung_gleicher_host_behaelt_token():
    neu = _weiterleiten("http://laura.test:8765/andere")
    assert neu.get_header("X-laura-token") == "geheim123"


def test_rendern_nimmt_plain_render(monkeypatch):
    aufrufe = []
    _router(monkeypatch, {("POST", "/timelines/t1/render"): _antwort({"export_id": "e2", "job_id": "j2"}, 202)},
            aufrufe)
    r = laura.rendern("t1")
    assert r == {"ok": True, "export_id": "e2", "job_id": "j2"}


def test_export_warten_wiederholt_bis_link_da(monkeypatch):
    _router(monkeypatch, {
        ("GET", "/exports/e1"): _antwort({"id": "e1", "status": "ready", "size_bytes": 1234}),
        ("GET", "/exports/e1/download-url"): [
            (409, b'{"detail":"nicht bereit"}'),
            _antwort({"url": "http://speicher.test/x?sig=1", "expires_in_seconds": 900, "object_key": "k"}),
        ],
    })
    r = laura.export_warten("e1", takt_s=0, schlafen=lambda s: None)
    assert r == {"ok": True, "url": "http://speicher.test/x?sig=1", "size_bytes": 1234}


def test_export_warten_bricht_bei_fehlgeschlagenem_export_ab(monkeypatch):
    _router(monkeypatch, {("GET", "/exports/e1"): _antwort({"status": "error", "error": "boom"})})
    r = laura.export_warten("e1", schlafen=lambda s: None)
    assert r["ok"] is False and "boom" in r["fehler"]


def test_herunterladen_schreibt_atomar(monkeypatch, tmp_path):
    ziel = tmp_path / "out.mp4"

    def roh(methode, url, daten, kopf):
        raise ConnectionError("abgebrochen")
    monkeypatch.setattr(laura, "_roh", roh)
    r = laura.herunterladen("http://speicher.test/x", ziel)
    assert r["ok"] is False
    assert not ziel.exists()
    assert list(tmp_path.iterdir()) == []


def test_herunterladen_relativ_mit_token_fremd_ohne(monkeypatch, tmp_path):
    aufrufe = []

    def roh(methode, url, daten, kopf):
        aufrufe.append((url, dict(kopf)))
        return 200, b"DATEN"
    monkeypatch.setattr(laura, "_roh", roh)
    z1 = tmp_path / "a.bin"
    assert laura.herunterladen("/files/x", z1) == {"ok": True, "bytes": 5}
    assert z1.read_bytes() == b"DATEN"
    assert aufrufe[0][0] == "http://laura.test:8765/files/x"
    assert aufrufe[0][1].get("X-Laura-Token") == "geheim123"
    laura.herunterladen("http://speicher.test/y", tmp_path / "b.bin")
    assert "X-Laura-Token" not in aufrufe[1][1]


def test_herunterladen_http_fehler_laesst_keine_datei(monkeypatch, tmp_path):
    monkeypatch.setattr(laura, "_roh", lambda *a: (404, b"nein"))
    ziel = tmp_path / "o.bin"
    r = laura.herunterladen("http://speicher.test/y", ziel)
    assert r["ok"] is False and not ziel.exists()
