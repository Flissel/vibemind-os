"""Bewertung einer Bubble durch MiroFish: Anlegen, Starten, Ergebnis pruefen, Nachfassen (ohne Modell).

Nur Attrappen, alle Namen erfunden. MiroFish, Auftragstabelle und Ideen-Server sind ersetzt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import agent_auftraege as aa  # noqa: E402
from core import ideen_abkuerzung as abk  # noqa: E402
from core import ideen_bewertung_mirofish as mf  # noqa: E402
from core import ideen_dokument as dok  # noqa: E402
from core.ideen_rueckweg import RueckwegFehler  # noqa: E402

BUBBLE = {"id": "b0b1e001", "title": "Stadtfest"}
KARTEN = [
    {"id": "a0000001", "title": "Bühnenplan", "content": "Zwei Bühnen am Samstag ab Mittag.", "node_type": "note",
     "linked_idea_id": "b0b1e001", "x": 0, "y": 0},
    {"id": "a0000002", "title": "Sponsoren", "content": "Drei Zusagen, Frist bis März.", "node_type": "note",
     "linked_idea_id": "b0b1e001", "x": 0, "y": 100},
    {"id": "a0000003", "title": "White Paper: Stadtfest", "content": "# White Paper\nBühnen und Sponsoren.",
     "node_type": "dokument", "linked_idea_id": "b0b1e001", "metadata": {"art": "white_paper"},
     "updated_at": "2026-11-25"},
]
KANTEN = [{"id": "e1", "from_node_id": "a0000001", "to_node_id": "a0000002", "edge_type": "related"},
          {"id": "e2", "from_node_id": "a0000002", "to_node_id": "b0000099", "edge_type": "related"}]
TASK = "0f1e2d3c4b5a49878a6b5c4d"


class _Lesen(dok.Lesen):
    def __init__(self, karten=None):
        self._karten = karten if karten is not None else KARTEN

    def bubble(self, kennung):
        return dict(BUBBLE) if kennung == BUBBLE["id"] else None

    def bubbles_mit_titel(self, titel):
        return [dict(BUBBLE)]

    def karten_der_bubble(self, kennung):
        return [dict(k) for k in self._karten]

    def karten(self, kennungen):
        return [dict(k) for k in self._karten if k["id"] in kennungen]

    def kanten(self, kennungen):
        return [dict(k) for k in KANTEN]


class _Tabelle:
    def __init__(self, laufend=None, kaputt=False):
        self.angelegt, self.uebergaben, self.geschlossen, self.beansprucht = [], [], [], []
        self.laufend = laufend or []
        self.kaputt = kaputt

    def anlegen(self, **kw):
        if self.kaputt:
            raise RuntimeError("weg")
        self.angelegt.append(kw)
        return "c0ffee0001"

    def uebergabe_setzen(self, auftrag_id, uebergabe):
        self.uebergaben.append((auftrag_id, uebergabe))

    def recherche_abschliessen(self, auftrag_id, status, pruefung):
        self.geschlossen.append((auftrag_id, status, pruefung))
        return True

    def pruefung_setzen(self, auftrag_id, pruefung):
        if auftrag_id in self.beansprucht:
            return False
        self.beansprucht.append(auftrag_id)
        return True

    def mirofish_laufend(self):
        return self.laufend


def _vorbereitet(**umgebung):
    return mf.vorbereiten(BUBBLE, _Lesen(), umgebung=umgebung)


# ── Einstellungen ────────────────────────────────────────────────────────────

def test_jede_bewertung_geht_an_mirofish():
    # Entscheid des Betreibers (26.11.2026): ein Knopf, der alte Weg ueber brain-ideas ist stillgelegt.
    assert mf.ueber_mirofish({}, {}) is True
    assert mf.ueber_mirofish({"schnell": True}, {"VIBEMIND_BEWERTUNG_QUELLE": "agent",
                                                 "VIBEMIND_SCHNELL_BEWERTEN": "1"}) is True
    assert mf.grenze_bytes({}) == 400 * 1024 and mf.grenze_bytes({mf.GRENZE_EINSTELLUNG: "50"}) == 50 * 1024
    assert mf.grenze_bytes({mf.GRENZE_EINSTELLUNG: "viel"}) == 400 * 1024


def test_schnell_bewerten_gibt_es_nicht_mehr(monkeypatch):
    kontext = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Stadtfest"}, "cards": []}
    monkeypatch.setenv("VIBEMIND_SCHNELL_BEWERTEN", "1")
    for satz in ("Schnell bewerten", "Bewerte diese Bubble schnell"):
        treffer = abk.loese(satz, kontext)
        assert treffer is None or "schnell" not in (treffer.get("argumente") or {}), satz
    treffer = abk.loese("Bewerte diese Bubble", kontext)
    assert treffer["ereignis"] == "idea.bewerten" and treffer["argumente"] == {"bubble_id": "b0b1e001"}

def test_vorbereiten_gibt_kuerzel_vollen_text_und_kartenstand():
    anfrage, rueckweg = _vorbereitet()
    assert [k["k"] for k in anfrage["karten"]] == ["K1", "K2"]
    assert anfrage["karten"][1] == {"k": "K2", "titel": "Sponsoren", "text": "Drei Zusagen, Frist bis März."}
    assert anfrage["verbindungen"] == [["K1", "K2", "related"]]      # die Kante in eine andere Bubble faellt weg
    assert "Bühnen und Sponsoren." in anfrage["white_paper"]
    assert "a0000001" not in str(anfrage)                              # MiroFish sieht keine Kennungen
    assert rueckweg["kuerzel"] == {"K1": "a0000001", "K2": "a0000002"}
    assert rueckweg["kartenstand"] == anfrage["kartenstand"] and mf.rueckweg_gueltig(rueckweg)


def test_zu_gross_wird_abgesagt_ohne_kuerzen():
    lang = [{**KARTEN[0], "content": "Bühne " * 400}, *KARTEN[1:]]
    with pytest.raises(RueckwegFehler, match=r"mit \d KB zu groß für eine Bewertung \(höchstens 1 KB\); "
                                             "gekürzt wird nicht"):
        mf.vorbereiten(BUBBLE, _Lesen(karten=lang), umgebung={mf.GRENZE_EINSTELLUNG: "1"})
    mf.vorbereiten(BUBBLE, _Lesen(karten=lang), umgebung={})          # mit der Vorgabe 400 KB geht sie durch


def test_ohne_karten_nichts_zu_bewerten():
    with pytest.raises(RueckwegFehler, match="keine Karten"):
        mf.vorbereiten(BUBBLE, _Lesen(karten=[KARTEN[2]]), umgebung={})


# ── Starten ──────────────────────────────────────────────────────────────────

def _bewertung(tabelle, senden):
    anfrage, rueckweg = _vorbereitet()
    return mf.MiroFishBewertung(anfrage, rueckweg, trace_id="t1", plan_id="p1", hop_id="s1",
                                antwortkanal={"art": "api"}, tabelle=tabelle, senden=senden)


def test_start_legt_laufende_zeile_an_und_merkt_die_aufgabe():
    gesendet = []

    def senden(methode, pfad, koerper):
        gesendet.append((methode, pfad, koerper))
        return 202, {"success": True, "data": {"task_id": TASK, "aufrufe_geschaetzt": 5, "aufrufe_hoechstens": 10}}

    tabelle = _Tabelle()
    aus = _bewertung(tabelle, senden).call_with_arg("egal")
    assert aus["pending"] is True and aus["result"]["auftrag_id"].startswith("c0ffee00")
    assert "5 Abo-Aufrufe, höchstens 10" in aus["result"]["hinweis"]
    zeile = tabelle.angelegt[0]
    assert zeile["status"] == "laeuft" and zeile["agent"] == "mirofish" and zeile["capability"] == "idea.bewerten"
    assert gesendet[0][:2] == ("POST", "/api/bubble/evaluate") and gesendet[0][2]["karten"][0]["k"] == "K1"
    uebergabe = tabelle.uebergaben[0][1]["mirofish_bewertung"]
    assert uebergabe["task_id"] == TASK and uebergabe["rueckweg"]["vertrag"] == "bewerten_mirofish"
    assert tabelle.geschlossen == []


@pytest.mark.parametrize("antwort, satz", [
    ((0, None), "MiroFish ist gerade nicht erreichbar"),
    ((503, None), "MiroFish ist gerade nicht erreichbar"),
    ((400, {"success": False, "error": "Die Bubble hat 401 Karten, höchstens 400 gehen."}), "höchstens 400 gehen"),
    ((202, {"success": True, "data": {}}), "nicht angenommen"),
])
def test_start_scheitert_ehrlich_ohne_ausweichen(antwort, satz):
    tabelle = _Tabelle()
    aus = _bewertung(tabelle, lambda *a: antwort).call_with_arg(None)
    assert aus["ok"] is False and aus.get("pending") is not True and satz in aus["error"]
    assert aus["retryable"] is False
    (_, status, pruefung), = tabelle.geschlossen
    assert status == "fehler" and satz in pruefung["meldung"] and pruefung["kind"] == "mirofish_bewertung"


def test_ohne_tabelle_wird_mirofish_nicht_gefragt():
    gefragt = []
    aus = _bewertung(_Tabelle(kaputt=True), lambda *a: gefragt.append(a)).call_with_arg(None)
    assert aus["ok"] is False and "Auftragstabelle" in aus["error"] and gefragt == []


def test_quittung_nennt_den_hinweis():
    exec_result = {"pending": True, "executed": {"s1": {"pending": True, "capability": "idea.bewerten", "result": {
        "auftrag_id": "c0ffee00-1", "agent": "mirofish", "hinweis": "MiroFish bewertet, 5 Abo-Aufrufe."}}}}
    offen = aa.offene_auftraege(exec_result)
    assert offen[0]["hinweis"] == "MiroFish bewertet, 5 Abo-Aufrufe."
    assert aa.quittung(offen) == "Auftrag angenommen, Nr. c0ffee00. MiroFish bewertet, 5 Abo-Aufrufe."
    assert "zuständig: brain-ideas" in aa.quittung([{"auftrag_id": "x1", "agent": "brain-ideas"}])


# ── Ergebnis pruefen ─────────────────────────────────────────────────────────

def _sicht(punkte=(3, 3, 3, 3, 3), todos=None):
    return {"punkte": sum(punkte), "urteil": "K2 ist dünn.",
            "kriterien": [{"name": f"Kriterium {n}", "punkte": p, "begruendung": "Steht in K1."}
                          for n, p in enumerate(punkte, start=1)],
            "todos": todos if todos is not None else [
                {"titel": "K2 füllen", "fehlt": "Die Frist fehlt.", "karten": ["K2"], "punkte": 4,
                 "art": "karte_fuellen"}]}


def _ergebnis(rueckweg, **anders):
    sichten = {n: _sicht() for n in ("coding", "swe_design", "research", "rowboat", "ideas")}
    sichten.update(anders)
    return {"kartenstand": rueckweg["kartenstand"], "aufrufe": 6, "graph": {"knoten": 2}, "sichten": sichten,
            "score": 75}


def test_gutes_ergebnis_wird_ein_lauf_von_mirofish():
    _, rueckweg = _vorbereitet()
    lauf, satz = mf.ergebnis_pruefen(_ergebnis(rueckweg), rueckweg)
    assert satz == "" and lauf["quelle"] == "mirofish" and lauf["gekuerzt"] is False and lauf["aufrufe"] == 6
    assert lauf["sichten"]["coding"]["punkte"] == 15
    assert lauf["sichten"]["coding"]["urteil"] == "„Sponsoren“ ist dünn."
    assert lauf["sichten"]["coding"]["kriterien"][0]["begruendung"] == "Steht in „Bühnenplan“."
    assert lauf["todos"][0]["karten"] == ["a0000002"] and len(lauf["todos"]) == 5
    assert mf._regeln().lauf_fehler(lauf) == ""


@pytest.mark.parametrize("aendern, satz", [
    (lambda e: e.update(kartenstand="sha256:" + "0" * 64), "anderen Kartenstand"),
    (lambda e: e["sichten"]["coding"].update(punkte=16), "passen Punkte und Raster nicht"),
    (lambda e: e["sichten"]["research"]["kriterien"].pop(), "kein vollständiges Raster"),
    (lambda e: e["sichten"]["ideas"]["kriterien"][0].update(punkte=6), "vereinbarten Form"),
    (lambda e: e["sichten"]["ideas"]["todos"][0].update(karten=["K9"]), "Karte bezogen, die es nicht gibt"),
    (lambda e: e["sichten"].pop("rowboat"), "vereinbarten Form"),
    (lambda e: e.update(aufrufe=-1), "vereinbarten Form"),
])
def test_falsches_ergebnis_wird_nicht_gespeichert(aendern, satz):
    _, rueckweg = _vorbereitet()
    ergebnis = _ergebnis(rueckweg)
    aendern(ergebnis)
    lauf, text = mf.ergebnis_pruefen(ergebnis, rueckweg)
    assert lauf is None and satz in text


# ── Nachfassen ───────────────────────────────────────────────────────────────

def _zeile(rueckweg, task_id=TASK):
    return {"id": "c0ffee0001", "trace_id": "t1", "plan_id": "p1", "hop_id": "s1",
            "uebergabe": {"mirofish_bewertung": {"rueckweg": rueckweg, "task_id": task_id}}}


def _stand(status, daten=None):
    return lambda methode, pfad, koerper: (status, {"success": True, "data": daten} if daten is not None else None)


class _Rueckweg:
    def __init__(self, quittung_ok=True, fehler=None):
        self.aufrufe = []
        self.quittung_ok, self.fehler = quittung_ok, fehler

    def __call__(self, zeile):
        def aufruf(ereignis, argumente):
            if self.fehler:
                raise RuntimeError(self.fehler)
            self.aufrufe.append((ereignis, argumente))
            return {"ok": True}

        def pruefe(ereignis, argumente, quittung):
            return (True, "ok") if self.quittung_ok else (False, "nicht nachpruefbar")

        return {"aufruf": aufruf, "pruefe": pruefe}


def test_laeuft_noch_wartet():
    _, rueckweg = _vorbereitet()
    tabelle = _Tabelle([_zeile(rueckweg)])
    z = mf.runde(tabelle, stand=_stand(200, {"status": "processing", "aufrufe": 2}), anschluss=_Rueckweg())
    assert z["mirofish_wartet"] == 1 and tabelle.geschlossen == [] and tabelle.beansprucht == []


def test_fertig_wird_gespeichert_mit_quelle_und_aufrufen():
    _, rueckweg = _vorbereitet()
    tabelle = _Tabelle([_zeile(rueckweg)])
    schreiber = _Rueckweg()
    daten = {"status": "completed", "aufrufe": 6, "result": _ergebnis(rueckweg)}
    z = mf.runde(tabelle, stand=_stand(200, daten), anschluss=schreiber)
    assert z["mirofish_fertig"] == 1
    (ereignis, argumente), = schreiber.aufrufe
    assert ereignis == "idea.bewertung_speichern" and argumente["bubble_id"] == "b0b1e001"
    assert argumente["lauf"]["quelle"] == "mirofish" and argumente["kartenstand"] == rueckweg["kartenstand"]
    (_, status, pruefung), = tabelle.geschlossen
    assert status == "fertig" and pruefung["verified"] is True and pruefung["rueckweg"] == "geschrieben"
    assert pruefung["meldung"].startswith("Bewertung: 60 von 100 (75 von 125 Punkten: Coding 15, ")
    assert "Bewertet von MiroFish mit fünf Rollen, 6 Abo-Aufrufe." in pruefung["meldung"]
    assert "befördert wird erst" not in pruefung["meldung"]
    assert pruefung["aufrufe"] == 6 and pruefung["score"] == 60


def test_schon_beansprucht_wird_nicht_zweimal_geschrieben():
    _, rueckweg = _vorbereitet()
    tabelle = _Tabelle([_zeile(rueckweg)])
    tabelle.beansprucht.append("c0ffee0001")
    schreiber = _Rueckweg()
    z = mf.runde(tabelle, stand=_stand(200, {"status": "completed", "aufrufe": 5, "result": _ergebnis(rueckweg)}),
                 anschluss=schreiber)
    assert schreiber.aufrufe == [] and tabelle.geschlossen == [] and z["mirofish_wartet"] == 1


@pytest.mark.parametrize("daten, satz", [
    ({"status": "failed", "aufrufe": 3, "error": "Wissenslage: kein gültiges JSON"},
     "MiroFish hat die Bewertung nicht abgeschlossen: Wissenslage: kein gültiges JSON Gespeichert ist nichts. "
     "Verbraucht: 3 Abo-Aufrufe."),
    ({"status": "failed", "aufrufe": 0, "error": "Coding: budget: Das Tagesbudget des Abo-Shims ist erschöpft."},
     "Das Tagesbudget für MiroFish ist erschöpft."),
])
def test_fehlgeschlagen_wird_ehrlich_geschlossen(daten, satz):
    _, rueckweg = _vorbereitet()
    tabelle = _Tabelle([_zeile(rueckweg)])
    schreiber = _Rueckweg()
    mf.runde(tabelle, stand=_stand(200, daten), anschluss=schreiber)
    (_, status, pruefung), = tabelle.geschlossen
    assert status == "fehler" and satz in pruefung["meldung"] and schreiber.aufrufe == []


def test_unbekannte_aufgabe_und_kaputte_uebergabe():
    _, rueckweg = _vorbereitet()
    tabelle = _Tabelle([_zeile(rueckweg)])
    mf.runde(tabelle, stand=_stand(404), anschluss=_Rueckweg())
    assert tabelle.geschlossen[0][2]["meldung"] == mf.UNBEKANNT
    tabelle = _Tabelle([_zeile(rueckweg, task_id=None)])
    mf.runde(tabelle, stand=_stand(200, {"status": "processing"}), anschluss=_Rueckweg())
    assert tabelle.geschlossen[0][2]["meldung"] == mf.NICHT_VERFOLGBAR


def test_nicht_erreichbar_bleibt_offen_fuer_die_naechste_runde():
    _, rueckweg = _vorbereitet()
    tabelle = _Tabelle([_zeile(rueckweg)])
    z = mf.runde(tabelle, stand=_stand(0), anschluss=_Rueckweg())
    assert z["fehler"] == 1 and tabelle.geschlossen == []


def test_falsches_ergebnis_und_herkunft_ohne_schluessel():
    _, rueckweg = _vorbereitet()
    kaputt = _ergebnis(rueckweg)
    kaputt["sichten"]["coding"]["punkte"] = 3
    tabelle = _Tabelle([_zeile(rueckweg)])
    mf.runde(tabelle, stand=_stand(200, {"status": "completed", "aufrufe": 5, "result": kaputt}),
             anschluss=_Rueckweg())
    assert "passen Punkte und Raster nicht" in tabelle.geschlossen[0][2]["meldung"]
    tabelle = _Tabelle([_zeile(rueckweg)])
    mf.runde(tabelle, stand=_stand(200, {"status": "completed", "aufrufe": 5, "result": _ergebnis(rueckweg)}),
             anschluss=_Rueckweg(fehler="herkunft_unbelegt: schluessel_fehlt"))
    (_, status, pruefung), = tabelle.geschlossen
    assert status == "fehler" and "Rückweg-Schlüssel" in pruefung["meldung"]


def test_nachfasser_fragt_mirofish_im_takt():
    from core.nachfasser import Nachfasser

    _, rueckweg = _vorbereitet()

    class Tabelle(_Tabelle):
        def zu_pruefen(self):
            return []

        def beendete_ohne_meldung(self):
            return []

        def abgelaufene_markieren(self):
            return 0

    tabelle = Tabelle([_zeile(rueckweg)])
    n = Nachfasser(tabelle, validator=None, router=None, plan_executor=None,
                   mirofish_anschluss=lambda: {"stand": _stand(200, {"status": "processing"}),
                                               "anschluss": _Rueckweg()})
    assert n.runde()["mirofish_wartet"] == 1


def test_todos_einer_sicht_bringen_hoechstens_das_fehlende():
    _, rueckweg = _vorbereitet()
    todos = [{"titel": f"T{n}", "fehlt": "x", "karten": ["K1"], "punkte": p, "art": "verbindung"}
             for n, p in enumerate((20, 15, 12, 12, 8))]
    ergebnis = _ergebnis(rueckweg, coding=_sicht(punkte=(2, 2, 2, 2, 1), todos=todos))
    lauf, _ = mf.ergebnis_pruefen(ergebnis, rueckweg)
    coding = [t["punkte"] for t in lauf["todos"] if t["sicht"] == "coding"]
    assert coding == [5, 3, 3, 3, 2] and sum(coding) == 25 - 9


# ── Ablehnung mit genauem Grund (erster Lauf im Betrieb, 26.11.2026) ─────────

@pytest.mark.parametrize("anders, grund", [
    (lambda e: e.pop("aufrufe"), "aufrufe fehlt"),
    (lambda e: e["sichten"].pop("ideas"), "fehlen: ideas"),
    (lambda e: e["sichten"]["coding"]["kriterien"].pop(), "raster unvollstaendig in Sicht coding (kriterien: 4"),
    (lambda e: e["sichten"]["research"]["kriterien"][1].update(punkte=9),
     "kriterium 2 in Sicht research: punkte (0-5) ungueltig"),
    (lambda e: e["sichten"]["rowboat"].update(punkte=3), "punkte der Sicht rowboat (3) ungleich Summe des Rasters (15)"),
    (lambda e: e["sichten"]["ideas"].update(urteil=""), "urteil in Sicht ideas"),
    (lambda e: e["sichten"]["swe_design"]["todos"][0].update(punkte=40),
     "todo 1 in Sicht swe_design ungueltig: punkte 40 ausserhalb 1-25"),
    (lambda e: e["sichten"]["coding"]["todos"][0].update(karten=["K99"]),
     "todo 1 in Sicht coding ungueltig: unbekannte Kuerzel K99"),
])
def test_ablehnung_nennt_den_genauen_grund(anders, grund):
    _, rueckweg = _vorbereitet()
    ergebnis = _ergebnis(rueckweg)
    anders(ergebnis)
    lauf, satz, ablehnung = mf.ergebnis_grund(ergebnis, rueckweg)
    assert lauf is None and satz and grund in ablehnung
    assert mf.ergebnis_pruefen(ergebnis, rueckweg) == (None, satz)


def test_abgelehntes_ergebnis_steht_mit_grund_in_pruefung_und_protokoll(caplog):
    _, rueckweg = _vorbereitet()
    ergebnis = _ergebnis(rueckweg)
    ergebnis["sichten"]["coding"]["kriterien"].pop()
    tabelle = _Tabelle([_zeile(rueckweg)])
    schreiber = _Rueckweg()
    with caplog.at_level("WARNING", logger=mf.logger.name):
        mf.runde(tabelle, stand=_stand(200, {"status": "completed", "aufrufe": 5, "result": ergebnis}),
                 anschluss=schreiber)
    assert schreiber.aufrufe == []
    (_, status, pruefung), = tabelle.geschlossen
    assert status == "fehler"
    assert pruefung["reason"].startswith("result rejected: raster unvollstaendig in Sicht coding")
    assert pruefung["ablehnung"].startswith("raster unvollstaendig in Sicht coding")
    assert any("raster unvollstaendig in Sicht coding" in r.getMessage() for r in caplog.records)

