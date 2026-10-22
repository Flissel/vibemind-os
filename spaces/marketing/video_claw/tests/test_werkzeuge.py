"""Video-Claw-Werkzeuge ohne echte Laura: laura_api._roh wird ersetzt (Rekorder)."""
import json

import pytest

from spaces.marketing.video_claw import laura_api, werkzeuge as w

PID = "aaaa1111-0000-0000-0000-000000000001"
AID = "bbbb2222-0000-0000-0000-000000000002"
AUF = "cccc3333-0000-0000-0000-000000000003"
OHNE_MARKE = {"mandant": "vibemind", "akzent": None, "schrift_anzeige": None, "schrift_text": None, "logo": None}


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

    monkeypatch.setattr(w, "_marke", lambda auftrag: dict(OHNE_MARKE))   # Firma ohne Marke

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
    assert w.standbild(PID, AID, 3, "schwenk", auftrag=AUF) == {"ok": True, "asset_id": AID}
    assert rek.aufrufe[0][1] == f"/projects/{PID}/stills"
    assert rek.aufrufe[0][2] == {"asset_id": AID, "dauer_s": 3.0, "bewegung": "schwenk"}


@pytest.mark.parametrize("args", [(PID, AID, 0.5, "ruhig"), (PID, AID, 3, "drehen"), ("x", AID, 3, "ruhig"),
                                  (PID, AID, "lang", "ruhig")])
def test_standbild_formen(laura, args):
    rek = laura([])
    assert not w.standbild(*args, auftrag=AUF)["ok"] and rek.aufrufe == []


def test_titelkarte_farben_geprueft(laura):
    rek = laura([])
    assert not w.titelkarte(AUF, PID, "Hallo", "rot")["ok"] and rek.aufrufe == []


def test_job_fehlgeschlagen(laura):
    laura([(202, {"job_id": "j1"}), (200, {"status": "failed", "error_json": "ffmpeg kaputt"})])
    r = w.titelkarte(AUF, PID, "Hallo", "#000000")
    assert not r["ok"] and "ffmpeg kaputt" in r["fehler"]


def test_reel_bauen_zwei_schritte(laura):
    rek = laura([(202, {"timeline_id": "tl1", "job_id": "j1"}),
                 (200, {"status": "succeeded", "result_json": json.dumps({"warnings": ["beat 2 gekuerzt"]})}),
                 (202, {"export_id": "e1e1e1e1e1", "job_id": "j2"})])
    r = w.reel_bauen(AUF, PID, [{"text": "Drei Fehler.", "asset_id": AID}], stimme="felix", hook="Drei Fehler",
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
    w.reel_bauen(AUF, PID, [{"text": "Ein Satz.", "asset_id": AID}])
    assert rek.aufrufe[0][2]["voice_id"] is None and "hook_text" not in rek.aufrufe[2][2]


@pytest.mark.parametrize("beats", [[], [{"text": "", "asset_id": AID}], [{"text": "x", "asset_id": "nope"}],
                                   [{"text": "x", "asset_id": AID, "src_in_frame": -1}], ["kein objekt"],
                                   [{"text": "x", "asset_id": AID}] * 13])
def test_reel_bauen_beats_geprueft(laura, beats):
    rek = laura([])
    assert not w.reel_bauen(AUF, PID, beats)["ok"] and rek.aufrufe == []


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


# --- Phase F: Originalton ---------------------------------------------------------

def test_reel_bauen_originalton_und_gemischte_untertitel(laura):
    rek = laura([(202, {"timeline_id": "tl1", "job_id": "j1"}), (200, {"status": "succeeded", "result_json": "{}"}),
                 (202, {"export_id": "e1e1e1e1e1"})])
    r = w.reel_bauen(AUF, PID, [{"text": "Darum geht es.", "asset_id": AID},
                           {"originalton": True, "asset_id": AID, "src_in_frame": 300, "src_out_frame": 600}])
    assert r["ok"]
    beats = rek.aufrufe[0][2]["beats"]
    assert beats[1] == {"asset_id": AID, "src_in_frame": 300, "src_out_frame": 600, "originalton": True}
    assert "text" not in beats[1]
    assert rek.aufrufe[2][2]["caption_source"] == "gemischt"


@pytest.mark.parametrize("beat", [
    {"originalton": True, "asset_id": AID, "src_in_frame": 300},
    {"originalton": True, "asset_id": AID, "src_in_frame": 300, "src_out_frame": 300},
    {"originalton": True, "asset_id": AID, "src_in_frame": 0, "src_out_frame": 1801},
    {"originalton": True, "asset_id": AID, "src_in_frame": 0, "src_out_frame": True},
])
def test_originalton_beat_geprueft(laura, beat):
    rek = laura([])
    assert not w.reel_bauen(AUF, PID, [beat])["ok"] and rek.aufrufe == []


def test_skills_kennen_originalton():
    from pathlib import Path
    ordner = Path(w.__file__).parent / "skills"
    for name in ("kurzfassung", "reel-aus-material"):
        text = (ordner / name / "SKILL.md").read_text(encoding="utf-8")
        assert '"originalton": true' in text and "Projekt" in text


# --- Feinschliff: Rahmung, Lautheit, Untertitel-Korrektur ---------------------------

def test_reel_bauen_einpassen_lautheit_korrektur(laura):
    rek = laura([(202, {"timeline_id": "tl1", "job_id": "j1"}), (200, {"status": "succeeded", "result_json": "{}"}),
                 (202, {"export_id": "e1e1e1e1e1"})])
    w.reel_bauen(AUF, PID, [{"originalton": True, "asset_id": AID, "src_in_frame": 0, "src_out_frame": 300}],
                 rahmung="einpassen", untertitel_ersetzen={"Kastermann": "Kunden"})
    reel = rek.aufrufe[2][2]
    assert reel["rahmung"] == "einpassen" and reel["loudnorm"] is True
    assert reel["untertitel_ersetzen"] == {"Kastermann": "Kunden"}


def test_reel_bauen_vorgaben(laura):
    rek = laura([(202, {"timeline_id": "tl1", "job_id": "j1"}), (200, {"status": "succeeded", "result_json": "{}"}),
                 (202, {"export_id": "e1e1e1e1e1"})])
    w.reel_bauen(AUF, PID, [{"text": "Satz.", "asset_id": AID}])
    reel = rek.aufrufe[2][2]
    assert reel["rahmung"] == "zuschnitt" and reel["loudnorm"] is True and "untertitel_ersetzen" not in reel


@pytest.mark.parametrize("kw", [{"rahmung": "quer"}, {"untertitel_ersetzen": {"zwei woerter": "x"}},
                                {"untertitel_ersetzen": {f"w{i}": "x" for i in range(31)}},
                                {"untertitel_ersetzen": ["kein", "dict"]}])
def test_reel_bauen_feinschliff_geprueft(laura, kw):
    rek = laura([])
    assert not w.reel_bauen(AUF, PID, [{"text": "Satz.", "asset_id": AID}], **kw)["ok"] and rek.aufrufe == []


# --- kompaktes Transkript (Probe 18.10.: Wortlisten liessen den Lauf haengen) -----------

def _segmente(monkeypatch, segmente):
    monkeypatch.setattr(w.laura_lesen, "transkript",
                        lambda aid: {"ok": True, "daten": {"segmente": segmente, "text": "x"}})


def test_transkript_kompakt_ohne_woerter(monkeypatch):
    _segmente(monkeypatch, [{"start_frame": 0, "end_frame": 90, "text": " Hallo ", "idx": 1,
                             "words": [{"start_sample": 1, "confidence": 0.9}] * 50}])
    r = w.transkript(AID)
    assert r["daten"]["segmente"] == [{"start_frame": 0, "end_frame": 90, "text": "Hallo"}]
    assert "words" not in str(r)


def test_transkript_zeitbereich(monkeypatch):
    _segmente(monkeypatch, [{"start_frame": f, "end_frame": f + 60, "text": str(f)} for f in range(0, 3000, 300)])
    r = w.transkript(AID, von_s=30, bis_s=60)
    assert [s["text"] for s in r["daten"]["segmente"]] == ["900", "1200", "1500", "1800"]


def test_transkript_deckel(monkeypatch):
    _segmente(monkeypatch, [{"start_frame": i, "end_frame": i, "text": "w"} for i in range(500)])
    r = w.transkript(AID)
    assert len(r["daten"]["segmente"]) == 400 and r["daten"]["anzahl"] == 500 and "hinweis" in r["daten"]



# --- Marke in Reels (Paket C1, 19.10.2026) -------------------------------------------------------
MARKE = {"mandant": "vibemind", "akzent": "#5b3fd6", "schrift_anzeige": "oxanium", "schrift_text": "dm-sans",
         "logo": "logo-vibemind-5ca856231b.png"}
LOGO_ASSET = "eeee5555-0000-0000-0000-000000000005"


def _reel_antworten():
    return [(202, {"timeline_id": "tl1", "job_id": "j1"}), (200, {"status": "succeeded", "result_json": "{}"}),
            (202, {"export_id": "e1e1e1e1e1"})]


def test_reel_bauen_traegt_die_marke(laura, monkeypatch):
    monkeypatch.setattr(w, "_marke", lambda auftrag: dict(MARKE))
    importe = []
    monkeypatch.setattr(w, "bild_importieren",
                        lambda auftrag, projekt_id, medien_name: importe.append((auftrag, projekt_id, medien_name))
                        or {"ok": True, "asset_id": LOGO_ASSET})
    rek = laura(_reel_antworten())
    r = w.reel_bauen(AUF, PID, [{"text": "Ein Satz.", "asset_id": AID}], hook="Hallo")
    assert r["ok"]
    reel = rek.aufrufe[2][2]
    assert (reel["caption_schrift"], reel["caption_akzent"], reel["hook_schrift"]) == ("dm-sans", "#5b3fd6", "oxanium")
    assert reel["logo_asset_id"] == LOGO_ASSET
    assert importe == [(AUF, PID, "logo-vibemind-5ca856231b.png")]


def test_reel_bauen_eigenes_logo_bleibt(laura, monkeypatch):
    monkeypatch.setattr(w, "_marke", lambda auftrag: dict(MARKE))
    monkeypatch.setattr(w, "bild_importieren", lambda *a: pytest.fail("kein Import bei eigenem Logo"))
    rek = laura(_reel_antworten())
    w.reel_bauen(AUF, PID, [{"text": "Ein Satz.", "asset_id": AID}], logo_asset_id=AID)
    assert rek.aufrufe[2][2]["logo_asset_id"] == AID


def test_reel_bauen_ohne_marke_wie_bisher(laura):
    rek = laura(_reel_antworten())
    r = w.reel_bauen(AUF, PID, [{"text": "Ein Satz.", "asset_id": AID}])
    assert r["ok"]
    assert not {"caption_schrift", "caption_akzent", "hook_schrift", "logo_asset_id"} & set(rek.aufrufe[2][2])


def test_reel_bauen_logo_import_scheitert_reel_entsteht_trotzdem(laura, monkeypatch):
    monkeypatch.setattr(w, "_marke", lambda auftrag: dict(MARKE))
    monkeypatch.setattr(w, "bild_importieren", lambda *a: {"ok": False, "fehler": "Bilddatei fehlt"})
    rek = laura(_reel_antworten())
    r = w.reel_bauen(AUF, PID, [{"text": "Ein Satz.", "asset_id": AID}])
    assert r["ok"] and "logo_asset_id" not in rek.aufrufe[2][2]
    assert any("Logo" in x for x in r["warnungen"])


def test_reel_bauen_auftrag_geprueft(laura):
    rek = laura([])
    assert not w.reel_bauen("kaputt", PID, [{"text": "x", "asset_id": AID}])["ok"] and rek.aufrufe == []


def test_titelkarte_in_markenschrift_auf_akzent(laura, monkeypatch):
    monkeypatch.setattr(w, "_marke", lambda auftrag: dict(MARKE))
    rek = laura([(202, {"job_id": "j1"}), (200, {"status": "succeeded", "result_json": json.dumps({"asset_id": AID})})])
    assert w.titelkarte(AUF, PID, "Hallo")["ok"]
    koerper = rek.aufrufe[0][2]
    assert koerper["hintergrund"] == "#5b3fd6" and koerper["schrift"] == "oxanium"


def test_titelkarte_eigener_hintergrund_bleibt(laura, monkeypatch):
    monkeypatch.setattr(w, "_marke", lambda auftrag: dict(MARKE))
    rek = laura([(202, {"job_id": "j1"}), (200, {"status": "succeeded", "result_json": json.dumps({"asset_id": AID})})])
    w.titelkarte(AUF, PID, "Hallo", "#000000")
    assert rek.aufrufe[0][2]["hintergrund"] == "#000000"


def test_titelkarte_ohne_marke_braucht_hintergrund(laura):
    rek = laura([])
    assert not w.titelkarte(AUF, PID, "Hallo")["ok"] and rek.aufrufe == []


def test_reel_bauen_marke_nicht_geladen_warnt(laura, monkeypatch):
    monkeypatch.setattr(w, "_marke", lambda auftrag: {})
    rek = laura(_reel_antworten())
    r = w.reel_bauen(AUF, PID, [{"text": "Ein Satz.", "asset_id": AID}])
    assert r["ok"] and any("Marke nicht geladen" in x for x in r["warnungen"])


# --- Reels Stufe 2: Formate (22.10.2026) ---------------------------------------------------------

def _karte_antworten():
    return [(202, {"job_id": "j1"}), (200, {"status": "succeeded", "result_json": json.dumps({"asset_id": AID})})]


def _mit_format(monkeypatch, fmt):
    monkeypatch.setattr(w, "_marke", lambda auftrag: {**OHNE_MARKE, "format": fmt})


def test_reel_bauen_schickt_format_nur_wenn_nicht_reel(laura, monkeypatch):
    rek = laura(_reel_antworten())
    w.reel_bauen(AUF, PID, [{"text": "Satz.", "asset_id": AID}])          # Marke ohne Format-Feld
    assert "format" not in rek.aufrufe[2][2] and "format" not in rek.aufrufe[0][2]
    _mit_format(monkeypatch, "reel")
    rek = laura(_reel_antworten())
    w.reel_bauen(AUF, PID, [{"text": "Satz.", "asset_id": AID}], format="reel")
    assert "format" not in rek.aufrufe[2][2]
    for fmt in ("quadrat", "quer"):
        _mit_format(monkeypatch, fmt)
        rek = laura(_reel_antworten())
        assert w.reel_bauen(AUF, PID, [{"text": "Satz.", "asset_id": AID}], format=fmt)["ok"]
        assert rek.aufrufe[2][2]["format"] == fmt
        assert "format" not in rek.aufrufe[0][2]           # narrated-reel kennt kein format (rendert nicht)


def test_standbild_titelkarte_schicken_format(laura, monkeypatch):
    _mit_format(monkeypatch, "quer")
    rek = laura(_karte_antworten())
    assert w.standbild(PID, AID, 3, "ruhig", format="quer", auftrag=AUF)["ok"]
    assert rek.aufrufe[0][2] == {"asset_id": AID, "dauer_s": 3.0, "bewegung": "ruhig", "format": "quer"}
    _mit_format(monkeypatch, "reel")
    rek = laura(_karte_antworten())
    assert w.standbild(PID, AID, 3, "ruhig", format="reel", auftrag=AUF)["ok"]
    assert "format" not in rek.aufrufe[0][2]
    _mit_format(monkeypatch, "quadrat")
    rek = laura(_karte_antworten())
    assert w.titelkarte(AUF, PID, "Hallo", "#000000", format="quadrat")["ok"]
    assert rek.aufrufe[0][2]["format"] == "quadrat"
    _mit_format(monkeypatch, "reel")
    rek = laura(_karte_antworten())
    w.titelkarte(AUF, PID, "Hallo", "#000000")
    assert "format" not in rek.aufrufe[0][2]


def test_werkzeuge_nehmen_format_des_auftrags(laura, monkeypatch):
    """Review Focus 3: Fehlt der Parameter, gilt das Format des Auftrags (nicht stillschweigend reel);
    widerspricht er dem Auftrag, bricht das Werkzeug ab, bevor Laura etwas baut."""
    gefragt = []
    monkeypatch.setattr(w, "_marke", lambda auftrag: gefragt.append(auftrag) or {**OHNE_MARKE, "format": "quer"})
    rek = laura(_karte_antworten())
    assert w.titelkarte(AUF, PID, "Hallo", "#000000")["ok"] and rek.aufrufe[0][2]["format"] == "quer"
    rek = laura(_karte_antworten())
    assert w.standbild(PID, AID, 3, "ruhig", auftrag=AUF)["ok"] and rek.aufrufe[0][2]["format"] == "quer"
    rek = laura(_reel_antworten())
    assert w.reel_bauen(AUF, PID, [{"text": "Satz.", "asset_id": AID}])["ok"]
    assert rek.aufrufe[2][2]["format"] == "quer"
    assert gefragt == [AUF, AUF, AUF]                       # reel_bauen fragt die Marke nur einmal
    for aufruf in (lambda: w.titelkarte(AUF, PID, "Hallo", "#000000", format="reel"),
                   lambda: w.standbild(PID, AID, 3, "ruhig", format="quadrat", auftrag=AUF),
                   lambda: w.reel_bauen(AUF, PID, [{"text": "Satz.", "asset_id": AID}], format="reel")):
        rek = laura([])
        r = aufruf()
        assert not r["ok"] and "quer" in r["fehler"] and rek.aufrufe == []


@pytest.mark.parametrize("kw", [{}, {"auftrag": ""}, {"auftrag": None}, {"format": "quer"}])
def test_standbild_ohne_auftrag_abgelehnt(laura, monkeypatch, kw):
    """Review T2: ohne auftrag kennt standbild das Format des Auftrags nicht - kein stilles reel."""
    monkeypatch.setattr(w, "_marke", lambda auftrag: pytest.fail("ohne auftrag keine Marke"))
    rek = laura([])
    r = w.standbild(PID, AID, 3, "ruhig", **kw)
    assert r == {"ok": False, "fehler": "auftrag: ID aus dem Briefing - das Format des Auftrags gilt"}
    assert rek.aufrufe == []


def test_reel_bauen_marke_nicht_geladen_format_warnt(laura, monkeypatch):
    monkeypatch.setattr(w, "_marke", lambda auftrag: {})
    rek = laura(_reel_antworten())
    r = w.reel_bauen(AUF, PID, [{"text": "Satz.", "asset_id": AID}])
    assert r["ok"] and "format" not in rek.aufrufe[2][2]
    assert any("Format" in x for x in r["warnungen"])
    rek = laura(_reel_antworten())
    assert w.reel_bauen(AUF, PID, [{"text": "Satz.", "asset_id": AID}], format="quer")["ok"]
    assert rek.aufrufe[2][2]["format"] == "quer"


@pytest.mark.parametrize("fmt", ["9:16", "<x>", "Quer", 1, True])
def test_format_ungueltig(laura, fmt):
    rek = laura([])
    assert not w.reel_bauen(AUF, PID, [{"text": "Satz.", "asset_id": AID}], format=fmt)["ok"]
    assert not w.titelkarte(AUF, PID, "Hallo", "#000000", format=fmt)["ok"]
    assert not w.standbild(PID, AID, 3, "ruhig", format=fmt, auftrag=AUF)["ok"]
    assert rek.aufrufe == []


def test_standbild_auftrag_geprueft(laura):
    rek = laura([])
    assert not w.standbild(PID, AID, 3, "ruhig", auftrag="kaputt")["ok"] and rek.aufrufe == []


def test_prompt_nennt_format_regel():
    from pathlib import Path
    ordner = Path(w.__file__).parent
    agents = (ordner / "AGENTS.md").read_text(encoding="utf-8")
    assert "im Format aus dem Briefing" in agents
    assert "Gib JEDEM titelkarte-, standbild- und reel_bauen-Aufruf dasselbe format aus dem Briefing" in agents
    assert "Hochformat" not in agents
    for name in ("reel-aus-bildern", "reel-aus-material", "kurzfassung"):
        text = (ordner / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert "Hochformat" not in text, name
        assert "format" in text, name
    bilder = (ordner / "skills" / "reel-aus-bildern" / "SKILL.md").read_text(encoding="utf-8")
    assert "`standbild`" in bilder and "auftrag" in bilder
    assert "Jedes `standbild` mit `auftrag`" in bilder
    assert "standbild" in agents and "immer `auftrag`" in agents
    for f in (w.standbild, w.titelkarte, w.reel_bauen):
        assert "Hochformat" not in (f.__doc__ or "") and "format" in (f.__doc__ or "")


# --- Gesamtreview: Format des Auftrags unbekannt -> kein stilles reel ---------------------------------
FORMAT_UNBEKANNT = {"ok": False, "fehler": "Format des Auftrags nicht geladen – format aus dem Briefing angeben"}


def test_standbild_format_unbekannt_bricht_ab(laura, monkeypatch):
    monkeypatch.setattr(w, "_marke", lambda auftrag: {})
    rek = laura([])
    assert w.standbild(PID, AID, 3, "ruhig", auftrag=AUF) == FORMAT_UNBEKANNT and rek.aufrufe == []
    rek = laura(_karte_antworten())
    assert w.standbild(PID, AID, 3, "ruhig", format="quer", auftrag=AUF)["ok"]
    assert rek.aufrufe[0][2]["format"] == "quer"


def test_titelkarte_format_unbekannt_bricht_ab(laura, monkeypatch):
    monkeypatch.setattr(w, "_marke", lambda auftrag: {})
    rek = laura([])
    assert w.titelkarte(AUF, PID, "Hallo", "#000000") == FORMAT_UNBEKANNT and rek.aufrufe == []
    rek = laura(_karte_antworten())
    assert w.titelkarte(AUF, PID, "Hallo", "#000000", format="quadrat")["ok"]
    assert rek.aufrufe[0][2]["format"] == "quadrat"


def test_reel_bauen_format_unbekannt_warnt_nur(laura, monkeypatch):
    monkeypatch.setattr(w, "_marke", lambda auftrag: {})
    rek = laura(_reel_antworten())
    r = w.reel_bauen(AUF, PID, [{"text": "Satz.", "asset_id": AID}])
    assert r["ok"] and "format" not in rek.aufrufe[2][2]
    assert "Format des Auftrags nicht geladen - Reel als reel (9:16)" in r["warnungen"]
