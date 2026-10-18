"""Video-Claw-Werkzeuge ohne echte Laura: laura_api._roh wird ersetzt (Rekorder)."""
import json

import pytest

from spaces.marketing.video_claw import laura_api, werkzeuge as w

PID = "aaaa1111-0000-0000-0000-000000000001"
AID = "bbbb2222-0000-0000-0000-000000000002"
AUF = "cccc3333-0000-0000-0000-000000000003"


class Laura:
    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.aufrufe = []

    def __call__(self, methode, url, daten, kopf):
        self.aufrufe.append((methode, url.split("8765", 1)[-1], json.loads(daten) if daten else None, kopf))
        status, rumpf = self.antworten.pop(0)
        return status, json.dumps(rumpf).encode("utf-8")


@pytest.fixture
def laura(monkeypatch):
    monkeypatch.setenv("LAURA_API_URL", "http://vm:8765")
    monkeypatch.setenv("LAURA_TOKEN", "geheim-token")

    def bauen(antworten):
        rek = Laura(antworten)
        monkeypatch.setattr(laura_api, "_roh", rek)
        monkeypatch.setattr(laura_api.time, "sleep", lambda s: None)
        return rek
    return bauen


def test_projekt_anlegen(laura):
    rek = laura([(201, {"id": PID})])
    assert w.projekt_anlegen(" Herbst ") == {"ok": True, "projekt_id": PID}
    m, pfad, daten, kopf = rek.aufrufe[0]
    assert (m, pfad) == ("POST", "/projects") and daten["name"] == "Herbst" and daten["sequence_rate_num"] == 30
    assert kopf["X-Laura-Token"] == "geheim-token"


def test_fehler_ohne_token_im_text(laura):
    laura([(500, {"detail": "kaputt geheim-token"})])
    r = w.projekt_anlegen("x")
    assert not r["ok"] and "geheim-token" not in r["fehler"] and "<token>" in r["fehler"]


def test_standbild_wartet_auf_job(laura):
    rek = laura([(202, {"job_id": "j1"}), (200, {"status": "running"}),
                 (200, {"status": "succeeded", "result_json": json.dumps({"asset_id": AID})})])
    assert w.standbild(PID, AID, 3, "schwenk") == {"ok": True, "asset_id": AID}
    assert rek.aufrufe[0][1] == f"/projects/{PID}/stills"
    assert rek.aufrufe[0][2] == {"asset_id": AID, "dauer_s": 3.0, "bewegung": "schwenk"}


@pytest.mark.parametrize("args", [(PID, AID, 0.5, "ruhig"), (PID, AID, 3, "drehen"), ("x", AID, 3, "ruhig"),
                                  (PID, AID, "lang", "ruhig")])
def test_standbild_formen(laura, args):
    rek = laura([])
    assert not w.standbild(*args)["ok"] and rek.aufrufe == []


def test_titelkarte_farben_geprueft(laura):
    rek = laura([])
    assert not w.titelkarte(PID, "Hallo", "rot")["ok"] and rek.aufrufe == []


def test_job_fehlgeschlagen(laura):
    laura([(202, {"job_id": "j1"}), (200, {"status": "failed", "error_json": "ffmpeg kaputt"})])
    r = w.titelkarte(PID, "Hallo", "#000000")
    assert not r["ok"] and "ffmpeg kaputt" in r["fehler"]


def test_reel_bauen_zwei_schritte(laura):
    rek = laura([(202, {"timeline_id": "tl1", "job_id": "j1"}),
                 (200, {"status": "succeeded", "result_json": json.dumps({"warnings": ["beat 2 gekuerzt"]})}),
                 (202, {"export_id": "e1e1e1e1e1", "job_id": "j2"})])
    r = w.reel_bauen(PID, [{"text": "Drei Fehler.", "asset_id": AID}], stimme="felix", hook="Drei Fehler",
                     logo_asset_id=AID)
    assert r == {"ok": True, "export_id": "e1e1e1e1e1", "timeline_id": "tl1", "warnungen": ["beat 2 gekuerzt"]}
    nr = rek.aufrufe[0][2]
    assert nr["render"] is False and nr["backend"] == "sidecar" and nr["voice_id"] == "felix"
    assert nr["beats"][0] == {"text": "Drei Fehler.", "asset_id": AID, "src_in_frame": 0, "pad_frames": 12}
    m, pfad, reel, _ = rek.aufrufe[2]
    assert pfad == "/timelines/tl1/render-reel"
    assert reel["kennzeichnung"] == "auto" and reel["max_bytes"] == 15_000_000 and reel["vertical"] is True
    assert reel["hook_text"] == "Drei Fehler" and reel["logo_asset_id"] == AID


def test_reel_bauen_standardstimme_ohne_voice_id(laura):
    rek = laura([(202, {"timeline_id": "tl1", "job_id": "j1"}), (200, {"status": "succeeded", "result_json": "{}"}),
                 (202, {"export_id": "e1e1e1e1e1"})])
    w.reel_bauen(PID, [{"text": "Ein Satz.", "asset_id": AID}])
    assert rek.aufrufe[0][2]["voice_id"] is None and "hook_text" not in rek.aufrufe[2][2]


@pytest.mark.parametrize("beats", [[], [{"text": "", "asset_id": AID}], [{"text": "x", "asset_id": "nope"}],
                                   [{"text": "x", "asset_id": AID, "src_in_frame": -1}], ["kein objekt"],
                                   [{"text": "x", "asset_id": AID}] * 13])
def test_reel_bauen_beats_geprueft(laura, beats):
    rek = laura([])
    assert not w.reel_bauen(PID, beats)["ok"] and rek.aufrufe == []


def test_material_uebernehmen_gleiches_projekt(laura):
    laura([(200, {"id": AID, "project_id": PID, "type": "video", "duration_frames": 300})])
    assert w.material_uebernehmen(PID, AID) == {"ok": True, "asset_id": AID, "dauer_frames": 300}


def test_material_uebernehmen_importiert(laura):
    neu = "dddd4444-0000-0000-0000-000000000004"
    rek = laura([(200, {"id": AID, "project_id": "anderes", "type": "video", "source_path": "/data/v.mp4",
                        "display_name": "v.mp4"}),
                 (202, {"asset_id": neu, "job_id": "j"}),
                 (200, {"id": neu, "sha256": "abc", "duration_frames": 450})])
    assert w.material_uebernehmen(PID, AID) == {"ok": True, "asset_id": neu, "dauer_frames": 450}
    assert rek.aufrufe[1][2] == {"source_path": "/data/v.mp4", "display_name": "v.mp4"}


def test_bild_importieren(laura, monkeypatch):
    neu = "dddd4444-0000-0000-0000-000000000004"
    gesehen = []
    monkeypatch.setattr(w, "_marketing", lambda pfad, daten: gesehen.append((pfad, daten)) or
                        {"ok": True, "daten": {"url": "https://vm/api/video/medien/1/s/team.png"}})
    rek = laura([(202, {"asset_id": neu, "job_id": "j"}), (200, {"id": neu, "sha256": "x", "type": "image"})])
    assert w.bild_importieren(AUF, PID, "team.png") == {"ok": True, "asset_id": neu}
    assert gesehen == [(f"/{AUF}/medien-link", {"name": "team.png"})]
    assert rek.aufrufe[0][2]["source_url"].endswith("/team.png")


def test_bild_kommt_nicht_als_bild(laura, monkeypatch):
    monkeypatch.setattr(w, "_marketing", lambda p, d: {"ok": True, "daten": {"url": "https://vm/x.png"}})
    laura([(202, {"asset_id": AID, "job_id": "j"}), (200, {"id": AID, "sha256": "x", "type": "video"})])
    assert not w.bild_importieren(AUF, PID, "x.png")["ok"]


def test_kein_generalwerkzeug():
    namen = {f.__name__ for f in w.WERKZEUGE}
    assert "laura_api" not in namen and not any("raw" in n for n in namen)
    mcp = json.loads((w.__file__.rsplit("werkzeuge.py", 1)[0] + "video-mcp.json").replace("\\", "/")
                     and open(w.__file__.rsplit("werkzeuge.py", 1)[0] + "video-mcp.json", encoding="utf-8").read())
    assert sorted(mcp["allowedTools"]) == sorted(f"mcp__video__{n}" for n in namen)
    assert mcp["mcpServers"]["video"]["url"] == "http://127.0.0.1:8137/mcp"


def test_mcp_zeitlimit_reicht_fuer_reel_bauen():
    """Die Claude-CLI bricht stille MCP-Aufrufe nach ~300 s ab (Probe-Reel 18.10.: zweimal knapp
    vorbei). Der Server braucht ein eigenes Zeitlimit ueber der Wartezeit von reel_bauen (600 s)."""
    pfad = w.__file__.rsplit("werkzeuge.py", 1)[0] + "video-mcp.json"
    mcp = json.loads(open(pfad, encoding="utf-8").read())
    assert mcp["mcpServers"]["video"]["timeout"] >= 900_000
