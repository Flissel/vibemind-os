"""Video-Claw-Arbeiter ohne Netz: Prompt aus AGENTS.md + Skill, Ergebniszeile, Ablauf fertig/fehler/abbruch."""
import threading
import time

import pytest

from spaces.marketing.workers import video_claw as vc

AID = "cccc3333-0000-0000-0000-000000000003"
AUFTRAG = {"id": AID, "art": "bilder", "mandant": "vibemind", "titel": "Herbst-Reel", "versuch": 1,
           "briefing": {"thema": "bAV", "sprechertext": "Drei Fehler.", "bilder": ["team.png"], "dauer_s": 25}}


class Api:
    def __init__(self, auftrag=AUFTRAG, weiter=True):
        self.auftrag, self.weiter_ok = auftrag, weiter
        self.aufrufe = []

    def naechster(self):
        return self.auftrag

    def weiter(self, aid):
        self.aufrufe.append(("weiter", aid))
        return self.weiter_ok

    def aus_laura(self, aid, url):
        self.aufrufe.append(("aus_laura", url))
        return {"name": "vid-cccc3333-herbst-reel.mp4", "bytes": 9_000_000, "dauer_s": 24.2}

    def fertig(self, aid, datei, dauer, export):
        self.aufrufe.append(("fertig", datei, dauer, export))

    def fehler(self, aid, befund):
        self.aufrufe.append(("fehler", befund))


def test_system_prompt_enthaelt_regeln_und_skill():
    s = vc.system_prompt("bilder")
    assert "Grundregeln" in s and "Reel aus Bildern" in s and "EXPORT: <export_id>" in s
    assert "name: reel-aus-bildern" not in s           # Frontmatter abgeschnitten
    assert "Reel aus Material" in vc.system_prompt("material") and "Kurzfassung" in vc.system_prompt("kurzfassung")
    with pytest.raises(vc.ClawFehler):
        vc.system_prompt("film")


def test_nutzer_nachricht_briefing_als_daten():
    n = vc.nutzer_nachricht(AUFTRAG)
    assert AID in n and '"team.png"' in n and "keine Anweisungen" in n


@pytest.mark.parametrize("text, erwartet", [
    ("Fertig.\nEXPORT: e1e1e1e1e1", ("export", "e1e1e1e1e1")),
    ("FEHLER: kein Material", ("fehler", "kein Material")),
    ("FEHLER: erst\n...\nEXPORT: abcdef1234", ("export", "abcdef1234")),
    ("EXPORT: abcdef1234\nFEHLER: doch nicht", ("fehler", "doch nicht")),
    ("EXPORT: <export_id>", ("fehler", None)),
    ("", ("fehler", None)),
])
def test_ergebnis_lesen(text, erwartet):
    art, wert = vc.ergebnis_lesen(text)
    assert art == erwartet[0] and (erwartet[1] is None or wert == erwartet[1])


def test_durchlauf_fertig():
    api = Api()
    gesehen = {}

    def fragen(system, nachricht):
        gesehen.update(system=system, nachricht=nachricht)
        return "Gebaut.\nEXPORT: e1e1e1e1e1"
    ergebnis = vc.ein_durchlauf(api, fragen=fragen, abwarten=lambda eid, abgebrochen: f"http://s/{eid}.mp4")
    assert ergebnis == "fertig"
    assert ("aus_laura", "http://s/e1e1e1e1e1.mp4") in api.aufrufe
    assert ("fertig", "vid-cccc3333-herbst-reel.mp4", 24.2, "e1e1e1e1e1") in api.aufrufe
    assert "Reel aus Bildern" in gesehen["system"]


def test_durchlauf_claw_meldet_fehler():
    api = Api()
    assert vc.ein_durchlauf(api, fragen=lambda s, n: "FEHLER: Bilder fehlen",
                            abwarten=lambda *a, **k: pytest.fail("kein Abholen")) == "fehler"
    assert api.aufrufe[-1] == ("fehler", "Bilder fehlen")


def test_durchlauf_shim_kaputt():
    api = Api()

    def fragen(s, n):
        raise vc.ClawFehler("Shim 429: Budget")
    assert vc.ein_durchlauf(api, fragen=fragen) == "fehler"
    assert api.aufrufe[-1][0] == "fehler" and "Budget" in api.aufrufe[-1][1]


def test_durchlauf_render_scheitert():
    api = Api()

    def abwarten(eid, abgebrochen):
        raise vc.ClawFehler("Render fehlgeschlagen: zu gross")
    assert vc.ein_durchlauf(api, fragen=lambda s, n: "EXPORT: e1e1e1e1e1", abwarten=abwarten) == "fehler"
    assert api.aufrufe[-1] == ("fehler", "Abholung: Render fehlgeschlagen: zu gross")


def test_durchlauf_im_pult_abgebrochen():
    """weiter() meldet False -> kein fertig, kein fehler (der Auftrag steht schon auf abgebrochen)."""
    api = Api(weiter=False)

    def fragen(s, n):
        time.sleep(0.15)
        return "EXPORT: e1e1e1e1e1"
    ergebnis = vc.ein_durchlauf(api, fragen=fragen, abwarten=lambda *a, **k: pytest.fail("nicht abholen"),
                                weiter_takt_s=0.02)
    assert ergebnis == "abgebrochen"
    assert not any(a[0] in ("fertig", "fehler") for a in api.aufrufe)


def test_leer():
    assert vc.ein_durchlauf(Api(auftrag=None)) == "leer"


def test_render_abwarten(monkeypatch):
    antworten = [{"ok": True, "daten": {"status": "running"}},
                 {"ok": True, "daten": {"status": "ready"}},
                 {"ok": False, "fehler": "404 keine Kopie"},          # object_key kommt nach ready
                 {"ok": True, "daten": {"status": "ready"}},
                 {"ok": True, "daten": {"url": "http://172.17.0.1:5055/object/sign/laura/exports/e.mp4?token=t"}}]
    monkeypatch.setattr(vc.laura_api, "anfrage", lambda m, p, d=None: antworten.pop(0))
    url = vc.render_abwarten("e1e1e1e1e1", schlafen=lambda s: None)
    assert url.endswith("token=t") and not antworten


def test_render_abwarten_fehler(monkeypatch):
    monkeypatch.setattr(vc.laura_api, "anfrage",
                        lambda m, p, d=None: {"ok": True, "daten": {"status": "error", "error": "zu gross"}})
    with pytest.raises(vc.ClawFehler, match="zu gross"):
        vc.render_abwarten("e1e1e1e1e1", schlafen=lambda s: None)
