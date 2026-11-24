"""Musik unter jedem Reel (Plan 3, Task 7): reel_bauen bestellt, reel_fertigstellen legt ein und rendert.
Weder Laura noch der Musik-Dienst werden angerufen: laura_api._roh und musik_studio._roh sind Rekorder,
das Optionen-Verzeichnis liegt in tmp_path."""
import json

import pytest

from spaces.marketing.video_claw import laura_api, musik_studio, werkzeuge as w

PID = "aaaa1111-0000-0000-0000-000000000001"
AID = "bbbb2222-0000-0000-0000-000000000002"
AUF = "cccc3333-0000-0000-0000-000000000003"
MA = "20261124-101500-video-ab12"          # Musik-Auftrag
BEATS = [{"text": "Ein Satz.", "asset_id": AID}]
OHNE_MARKE = {"mandant": "vibemind", "akzent": None, "schrift_anzeige": None, "schrift_text": None, "logo": None}


class Rekorder:
    """Antwortet je Pfad aus einer Liste; schreibt jeden Aufruf in das gemeinsame Protokoll."""

    def __init__(self, name, log, antworten):
        self.name, self.log, self.antworten, self.aufrufe = name, log, antworten, []

    def __call__(self, methode, url, daten, kopf, zeitlimit_s=None):
        self.limits = getattr(self, "limits", []) + [zeitlimit_s]
        pfad = "/" + url.split("//", 1)[1].split("/", 1)[1] if url.count("/") > 2 else "/"
        körper = json.loads(daten) if daten else None
        self.aufrufe.append((methode, pfad, körper, kopf))
        self.log.append((self.name, methode, pfad))
        antwort = self.antworten.pop(0)
        if isinstance(antwort, Exception):
            raise antwort
        status, rumpf = antwort
        return status, json.dumps(rumpf).encode("utf-8")


@pytest.fixture
def welt(monkeypatch, tmp_path):
    monkeypatch.setenv("LAURA_API_URL", "http://vm:8765")
    monkeypatch.setenv("LAURA_TOKEN", "geheim-token")
    monkeypatch.delenv("MUSIK_STUDIO_URL", raising=False)
    monkeypatch.setattr(w, "_marke", lambda auftrag: dict(OHNE_MARKE))
    monkeypatch.setattr(w, "_optionen_ordner", lambda: tmp_path)
    monkeypatch.setattr(laura_api.time, "sleep", lambda s: None)
    monkeypatch.setattr(musik_studio.time, "sleep", lambda s: None)
    log: list = []

    class Welt:
        pass
    e = Welt()
    e.log, e.ordner = log, tmp_path

    def laura(antworten):
        e.laura = Rekorder("laura", log, list(antworten))
        monkeypatch.setattr(laura_api, "_roh", e.laura)

    def musik(antworten):
        e.musik = Rekorder("musik", log, list(antworten))
        monkeypatch.setattr(musik_studio, "_roh", e.musik)
    e.setze_laura, e.setze_musik = laura, musik
    return e


def _bauen_antworten():
    return [(202, {"timeline_id": "tl1", "job_id": "j1"}), (200, {"status": "succeeded", "result_json": "{}"})]


def _render_antwort():
    return (202, {"export_id": "e1e1e1e1e1", "job_id": "j2"})


def _gebaut(welt, **kw):
    welt.setze_laura(_bauen_antworten())
    welt.setze_musik([(200, {"ok": True, "auftrag": MA, "position": 1})])
    return w.reel_bauen(AUF, PID, BEATS, hook="Hallo", musik="ruhig, warm, ohne Gesang", **kw)


# --- reel_bauen ----------------------------------------------------------------------------------

def test_reel_ohne_musik_unveraendert(welt):
    for leer in ("", "aus", "AUS"):
        welt.log.clear()
        welt.setze_laura(_bauen_antworten() + [_render_antwort()])
        welt.setze_musik([])
        r = w.reel_bauen(AUF, PID, BEATS, musik=leer) if leer != "" else w.reel_bauen(AUF, PID, BEATS)
        assert r == {"ok": True, "export_id": "e1e1e1e1e1", "timeline_id": "tl1", "warnungen": []}
        assert [p for _, _, p in welt.log] == ["/projects/" + PID + "/narrated-reel", "/jobs/j1",
                                               "/timelines/tl1/render-reel"]
        assert welt.musik.aufrufe == []
    assert list(welt.ordner.iterdir()) == []


def test_reel_mit_musik_rendert_nicht(welt):
    r = _gebaut(welt)
    assert r["ok"] and r["musik_auftrag"] == MA and r["timeline_id"] == "tl1" and "export_id" not in r
    assert r["warnungen"] == [] and r["reel"]["hook_text"] == "Hallo" and r["reel"]["loudnorm"] is True
    assert all("render-reel" not in p for _, _, p in welt.log)
    m, pfad, body, kopf = welt.musik.aufrufe[0]
    assert (m, pfad) == ("POST", "/auftraege/video")
    assert body == {"wunsch": "ruhig, warm, ohne Gesang", "laura_timeline_id": "tl1", "gesang": False,
                    "kanal": "video-claw"}
    assert kopf["Content-Type"] == "application/json" and "Origin" not in kopf
    gemerkt = json.loads((welt.ordner / "reel-tl1.json").read_text(encoding="utf-8"))
    assert gemerkt == r["reel"]
    assert [p.name for p in welt.ordner.iterdir()] == ["reel-tl1.json"]       # kein Temp-Rest


def test_reel_mit_musik_dienst_bestellt_nicht_rendert_trotzdem(welt):
    welt.setze_laura(_bauen_antworten() + [_render_antwort()])
    welt.setze_musik([(200, {"ok": False, "fehler": "Speicher knapp"})])
    r = w.reel_bauen(AUF, PID, BEATS, musik="ruhig")
    assert r["ok"] and r["export_id"] == "e1e1e1e1e1"
    assert r["warnungen"] == ["ohne Musik: Speicher knapp"]
    assert list(welt.ordner.iterdir()) == []


def test_musik_wunsch_kein_text_ist_fehler(welt):
    welt.setze_laura([])
    assert not w.reel_bauen(AUF, PID, BEATS, musik=5)["ok"] and welt.laura.aufrufe == []


# --- reel_fertigstellen ----------------------------------------------------------------------------

def test_fertigstellen_legt_ein_und_rendert(welt):
    _gebaut(welt)
    welt.log.clear()
    welt.setze_laura([_render_antwort()])
    welt.setze_musik([(200, {"ok": True, "zustand": "laeuft"}), (200, {"ok": True, "zustand": "fertig"}),
                      (200, {"ok": True, "clip_id": "c1", "asset_id": "a1"})])
    r = w.reel_fertigstellen("tl1", MA)
    assert r == {"ok": True, "export_id": "e1e1e1e1e1", "timeline_id": "tl1", "warnungen": []}
    assert welt.log == [("musik", "GET", f"/auftraege/{MA}"), ("musik", "GET", f"/auftraege/{MA}"),
                        ("musik", "POST", f"/auftraege/{MA}/in-laura"),
                        ("laura", "POST", "/timelines/tl1/render-reel")]
    assert welt.musik.aufrufe[2][2] == {"timeline_id": "tl1", "gain_percent": 25}
    gemerkt = json.loads((welt.ordner / "reel-tl1.json").read_text(encoding="utf-8"))
    assert welt.laura.aufrufe[0][2] == gemerkt and gemerkt["hook_text"] == "Hallo"


def test_fertigstellen_fertig_mit_befund_zaehlt_als_musik(welt):
    _gebaut(welt)
    welt.setze_laura([_render_antwort()])
    welt.setze_musik([(200, {"ok": True, "zustand": "fertig_mit_befund"}),
                      (200, {"ok": True, "clip_id": "c1", "asset_id": "a1"})])
    assert w.reel_fertigstellen("tl1", MA)["warnungen"] == []


def test_fertigstellen_musik_gescheitert_rendert_ohne(welt):
    _gebaut(welt)
    welt.setze_laura([_render_antwort()])
    welt.setze_musik([(200, {"ok": True, "zustand": "gescheitert", "grund": "kein Song passt"})])
    r = w.reel_fertigstellen("tl1", MA)
    assert r["ok"] and r["export_id"] == "e1e1e1e1e1"
    assert len(r["warnungen"]) == 1 and r["warnungen"][0].startswith("ohne Musik:")
    assert "kein Song passt" in r["warnungen"][0]
    assert len(welt.musik.aufrufe) == 1                      # nie eingelegt


def test_fertigstellen_musik_dienst_ok_false_rendert_ohne(welt):
    _gebaut(welt)
    welt.setze_laura([_render_antwort()])
    welt.setze_musik([(200, {"ok": False, "fehler": "unbekannter Auftrag"})] * 3)
    r = w.reel_fertigstellen("tl1", MA)
    assert r["ok"] and r["warnungen"][0].startswith("ohne Musik:")


def test_fertigstellen_einlegen_scheitert_rendert_ohne(welt):
    _gebaut(welt)
    welt.setze_laura([_render_antwort()])
    welt.setze_musik([(200, {"ok": True, "zustand": "fertig"}),
                      (200, {"ok": False, "fehler": "Laura Clip-Fehler"})])
    r = w.reel_fertigstellen("tl1", MA)
    assert r["ok"] and r["warnungen"] == ["ohne Musik: Laura Clip-Fehler"]
    assert welt.laura.aufrufe[0][1] == "/timelines/tl1/render-reel"


def test_fertigstellen_musikdienst_aus(welt):
    _gebaut(welt)
    welt.setze_laura([_render_antwort()])
    welt.setze_musik([ConnectionRefusedError("nein")] * 3)
    r = w.reel_fertigstellen("tl1", MA)
    assert r["ok"] and r["export_id"] == "e1e1e1e1e1"
    assert r["warnungen"][0].startswith("ohne Musik: Musik-Dienst nicht erreichbar")


@pytest.mark.parametrize("status", [400, 403, 404, 415, 500])
def test_anfrage_nicht_2xx_wird_fehler(welt, status):
    welt.setze_musik([(status, {"ok": False, "fehler": "x" * 500})])
    r = musik_studio.anfrage("GET", f"/auftraege/{MA}")
    assert r["ok"] is False and str(status) in r["fehler"] and len(r["fehler"]) <= 300


def test_fertigstellen_ohne_gemerkte_optionen_ist_fehler(welt):
    welt.setze_laura([])
    welt.setze_musik([])
    r = w.reel_fertigstellen("tl1", MA)
    assert r["ok"] is False and welt.laura.aufrufe == [] and welt.musik.aufrufe == []


@pytest.mark.parametrize("inhalt", ["kein json", "[1, 2]", ""])
def test_fertigstellen_kaputte_optionen_ist_fehler(welt, inhalt):
    (welt.ordner / "reel-tl1.json").write_text(inhalt, encoding="utf-8")
    welt.setze_laura([])
    welt.setze_musik([])
    r = w.reel_fertigstellen("tl1", MA)
    assert r["ok"] is False and welt.laura.aufrufe == [] and welt.musik.aufrufe == []


@pytest.mark.parametrize("tl", ["../x", "a/b", "", "a b", "x" * 129, None])
def test_timeline_id_geprueft(welt, tl):
    welt.setze_laura([])
    welt.setze_musik([])
    assert w.reel_fertigstellen(tl, MA)["ok"] is False
    assert welt.laura.aufrufe == [] and welt.musik.aufrufe == []


def test_fertigstellen_auftrag_geprueft(welt):
    welt.setze_laura([])
    welt.setze_musik([])
    assert w.reel_fertigstellen("tl1", "../x")["ok"] is False and welt.musik.aufrufe == []


def test_fertigstellen_render_fehler_wird_gemeldet(welt):
    _gebaut(welt)
    welt.setze_laura([(500, {"detail": "kaputt"})])
    welt.setze_musik([(200, {"ok": True, "zustand": "fertig"}), (200, {"ok": True, "clip_id": "c1"})])
    r = w.reel_fertigstellen("tl1", MA)
    assert r["ok"] is False


def test_fertigstellen_wartet_hoechstens_480_s(monkeypatch):
    gesehen = {}

    def warten(auftrag, max_s=600, **kw):
        gesehen["max_s"] = max_s
        return {"ok": False, "fehler": "zu lang"}
    monkeypatch.setattr(w.musik_studio, "warten", warten)
    monkeypatch.setattr(w, "_optionen_lesen", lambda tl: {"vertical": True})
    monkeypatch.setattr(w.laura_api, "anfrage", lambda *a, **k: {"ok": True, "daten": {"export_id": "e1e1e1e1e1"}})
    r = w.reel_fertigstellen("tl1", MA)
    assert gesehen["max_s"] == 480 and r["warnungen"] == ["ohne Musik: zu lang"]


# --- musik_studio.warten ---------------------------------------------------------------------------

def test_warten_tackt_alle_5_s_und_gibt_auf(welt):
    welt.setze_musik([(200, {"ok": True, "zustand": "wartet"})] * 10)
    jetzt = [0.0]
    schlaf = []

    def schlafen(s):
        schlaf.append(s)
        jetzt[0] += s
    r = musik_studio.warten(MA, max_s=12, schlafen=schlafen, uhr=lambda: jetzt[0])
    assert r["ok"] is False and "noch nicht fertig" in r["fehler"]
    assert schlaf == [5.0, 5.0, 5.0]


def test_warten_poll_mit_kurzem_zeitlimit(welt):
    welt.setze_musik([(200, {"ok": True, "zustand": "laeuft"})] * 2 + [(200, {"ok": True, "zustand": "fertig"})])
    jetzt = [0.0]

    def schlafen(s):
        jetzt[0] += s
    assert musik_studio.warten(MA, max_s=480, schlafen=schlafen, uhr=lambda: jetzt[0])["ok"]
    assert all(0 < g <= 15 for g in welt.musik.limits) and len(welt.musik.limits) == 3


def test_warten_poll_limit_nie_ueber_restzeit(welt):
    welt.setze_musik([(200, {"ok": True, "zustand": "laeuft"})] * 3)
    jetzt = [0.0]

    def schlafen(s):
        jetzt[0] += s
    r = musik_studio.warten(MA, max_s=7, schlafen=schlafen, uhr=lambda: jetzt[0])
    assert not r["ok"] and welt.musik.limits[0] == 7 and welt.musik.limits[1] == 2


def test_warten_zwei_aussetzer_in_folge_sind_ok(welt):
    aus = ConnectionRefusedError("nein")
    welt.setze_musik([aus, aus, (200, {"ok": True, "zustand": "fertig"})])
    assert musik_studio.warten(MA, schlafen=lambda s: None)["ok"]


def test_warten_dritter_aussetzer_in_folge_beendet(welt):
    aus = ConnectionRefusedError("nein")
    welt.setze_musik([aus, aus, aus, (200, {"ok": True, "zustand": "fertig"})])
    r = musik_studio.warten(MA, schlafen=lambda s: None)
    assert not r["ok"] and "nicht erreichbar" in r["fehler"] and len(welt.musik.aufrufe) == 3


def test_warten_erfolg_setzt_aussetzer_zurueck(welt):
    aus = ConnectionRefusedError("nein")
    lauf = (200, {"ok": True, "zustand": "laeuft"})
    welt.setze_musik([aus, aus, lauf, aus, aus, (200, {"ok": True, "zustand": "fertig"})])
    assert musik_studio.warten(MA, schlafen=lambda s: None)["ok"]


@pytest.mark.parametrize("vergangen, erwartet", [(100, 330), (600, 180), (749, 31)])
def test_fertigstellen_einlegen_bekommt_restbudget(welt, monkeypatch, vergangen, erwartet):
    _gebaut(welt)
    welt.setze_laura([_render_antwort()])
    welt.setze_musik([(200, {"ok": True, "clip_id": "c1", "asset_id": "a1"})])
    zeiten = iter([1000.0, 1000.0 + vergangen])
    monkeypatch.setattr(w, "_uhr", lambda: next(zeiten))
    monkeypatch.setattr(musik_studio, "warten", lambda auftrag, max_s=600, **k: {"ok": True, "zustand": "fertig"})
    r = w.reel_fertigstellen("tl1", MA)
    assert r["warnungen"] == [] and welt.musik.limits == [erwartet]


def test_fertigstellen_zu_wenig_restzeit_ueberspringt_einlegen(welt, monkeypatch):
    _gebaut(welt)
    welt.setze_laura([_render_antwort()])
    welt.setze_musik([])
    zeiten = iter([1000.0, 1000.0 + 760])
    monkeypatch.setattr(w, "_uhr", lambda: next(zeiten))
    monkeypatch.setattr(musik_studio, "warten", lambda auftrag, max_s=600, **k: {"ok": True, "zustand": "fertig"})
    r = w.reel_fertigstellen("tl1", MA)
    assert r["ok"] and r["export_id"] == "e1e1e1e1e1" and r["warnungen"] == ["ohne Musik: keine Zeit mehr"]
    assert welt.musik.aufrufe == []


def test_anfrage_nutzt_vorgabe_adresse_und_umgebung(welt, monkeypatch):
    welt.setze_musik([(200, {"ok": True}), (200, {"ok": True})])
    musik_studio.anfrage("GET", "/x")
    monkeypatch.setenv("MUSIK_STUDIO_URL", "http://127.0.0.1:9999/")
    musik_studio.anfrage("GET", "/y")
    assert welt.musik.aufrufe[0][1] == "/x"
    assert welt.musik.aufrufe[1][1] == "/y"


def test_vorgabe_adresse_ist_127_0_0_1_8148(monkeypatch):
    monkeypatch.delenv("MUSIK_STUDIO_URL", raising=False)
    assert musik_studio._basis() == "http://127.0.0.1:8148"


# --- Werkzeugliste und Regeln ---------------------------------------------------------------------

def test_werkzeugliste_und_mcp_freigabe():
    namen = {f.__name__ for f in w.WERKZEUGE}
    assert "reel_fertigstellen" in namen
    pfad = w.__file__.rsplit("werkzeuge.py", 1)[0] + "video-mcp.json"
    mcp = json.loads(open(pfad, encoding="utf-8").read())
    assert "mcp__video__reel_fertigstellen" in mcp["allowedTools"]


def test_agents_md_hat_musikregel():
    from pathlib import Path
    agents = (Path(w.__file__).parent / "AGENTS.md").read_text(encoding="utf-8")
    assert "ohne Musik" in agents and "reel_fertigstellen" in agents and "musik_auftrag" in agents
