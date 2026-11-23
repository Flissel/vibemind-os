"""Dritter Lauf, Punkte 2 bis 6: "Zurück" nach Ablehnung, Vorschlag, Stand des Auftrags,
"angenommen" ohne Dauer, Hinweis und Rückfrage als eigenes Feld.

Nur Attrappen: keine Datenbank, kein Agent, kein Modell. Namen und Kennungen erfunden.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import agent_auftraege as aa  # noqa: E402
from core import auftrag_stand as st  # noqa: E402
from core import ideen_vorschlag as vs  # noqa: E402
from core import zurueck_vorsicht as zv  # noqa: E402
from core.ideen_abkuerzung import fehlerart  # noqa: E402
from web.routers.introspection import router  # noqa: E402

A1 = "00000000-0000-4000-8000-0000000000a1"
A2 = "00000000-0000-4000-8000-0000000000a2"
KARTE_ID = "00000000-0000-4000-8000-00000000c001"
BUBBLE_ID = "00000000-0000-4000-8000-00000000b001"
T0 = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def _iso(sekunden: float) -> str:
    return (T0 + timedelta(seconds=sekunden)).isoformat()


@pytest.fixture(autouse=True)
def _sauber():
    zv._offen.clear()
    zv._voriger.clear()
    yield
    zv._offen.clear()
    zv._voriger.clear()


# ── Punkt 2: "Zurück" nach einem abgelehnten Satz oder Hinweis ───────────

def _frische_gruppe(alter_s: float = 30.0):
    jetzt = datetime.now(timezone.utc)
    return lambda: [{"id": 1, "gruppe": "ideen_9-r", "erstellt_am": (jetzt - timedelta(seconds=alter_s)).isoformat(),
                     "tabelle": "ideas", "vorgang": "UPDATE", "nachher_titel": "Stadtfest"}]


def test_zurueck_nach_erfolg_geht_sofort() -> None:
    zv.satz_merken("erfolg")
    assert zv.entscheiden("", "Zurück", ist_abgelehnt=lambda g: False, lesen=_frische_gruppe()) == {
        "aktion": "weiter", "gruppe": "ideen_9-r"}


@pytest.mark.parametrize("art, anfang", [("abgelehnt", "Dein letzter Satz wurde nicht ausgeführt"),
                                          ("hinweis", "Auf deinen letzten Satz kam nur ein Hinweis"),
                                          ("rueckfrage", "Auf deinen letzten Satz kam eine Rückfrage")])
def test_zurueck_nach_ablehnung_oder_hinweis_fragt_immer(art, anfang) -> None:
    # Dritter Lauf: Tabellen-Satz abgelehnt (keine Zeile im Protokoll), die Begründung davor
    # war < 2 min alt -> wurde ohne Frage zurückgenommen.
    zv.satz_merken(art)
    wahl = zv.entscheiden("", "Zurück", ist_abgelehnt=lambda g: False, lesen=_frische_gruppe())
    assert wahl["aktion"] == "fragen" and wahl["satz"].startswith(anfang)
    assert "„Stadtfest“ geändert" in wahl["satz"] and "„Ja, zurück“" in wahl["satz"]
    # "Ja, zurück" (selbst nach der Rückfrage) nimmt genau diese Gruppe zurück.
    zv.satz_merken("rueckfrage")
    assert zv.entscheiden("", "Ja, zurück", ist_abgelehnt=lambda g: False, lesen=_frische_gruppe()) == {
        "aktion": "weiter", "gruppe": "ideen_9-r"}


def test_art_der_antwort() -> None:
    assert zv.art_der_antwort({"ok": True}) == "erfolg"
    assert zv.art_der_antwort({"ok": False}) == "abgelehnt"
    assert zv.art_der_antwort({"ok": False, "hinweis": True}) == "hinweis"
    assert zv.art_der_antwort({"ok": False, "rueckfrage": True}) == "rueckfrage"
    assert zv.art_der_antwort({"ok": True, "pending": True, "status": "angenommen"}) == "angenommen"


# ── Punkt 6: Hinweis und Rückfrage als eigenes Feld ─────────────────────

@pytest.mark.parametrize("fehler, art", [
    ("RuntimeError: Ideen-Werkzeug meldet einen Fehler (idea_format_table_no_columns)", "hinweis"),
    ("Ideen-Werkzeug meldet einen Fehler (idea_format_no_content)", "hinweis"),
    ("aenderung_zurueck_frage: Die letzte Änderung ... Sag „Ja, zurück“", "rueckfrage"),
    ("ideen_rueckweg: Das White Paper bräuchte 14 Läufe; die Grenze ist 12. Sag ...", "hinweis"),
    ("ideen_rueckweg: Ein White Paper starte ich nur nach Rückfrage. Sag ...", "hinweis"),
    ("Ideen-Werkzeug meldet einen Fehler (idea_not_found)", None),
    ("timeout", None),
])
def test_fehlerart(fehler, art) -> None:
    assert fehlerart(fehler) == art


def test_plan_completed_hinweis_und_angenommen_ohne_dauer() -> None:
    from core.plan_executor import HopResult, _abschluss_ereignis

    hinweis = {"s1": HopResult(step_id="s1", ok=False, error="Ideen-Werkzeug meldet einen Fehler "
                                                              "(idea_format_table_no_columns)")}
    nutzlast = _abschluss_ereignis("ideen_1", False, {"elapsed_s": 1.6}, hinweis)
    assert nutzlast["hinweis"] is True and nutzlast["ok"] is False and nutzlast["elapsed_s"] == 1.6
    fehler = {"s1": HopResult(step_id="s1", ok=False, error="boom")}
    assert "hinweis" not in _abschluss_ereignis("ideen_2", False, {"elapsed_s": 1.0}, fehler)
    # Punkt 5: angenommen ist nicht fertig; die Dauer der Annahme geht nicht hinaus.
    wartet = {"s1": HopResult(step_id="s1", ok=False, pending=True)}
    angenommen = _abschluss_ereignis("ideen_3", False, {"elapsed_s": 2.0, "pending": True}, wartet)
    assert angenommen == {"plan_id": "ideen_3", "ok": True, "pending": True, "status": "angenommen",
                          "fertig": False, "hop_count": 1}


# ── Router: Felder in der Antwort, Ausgang fuer "Zurück", Annahme ohne Dauer ──

class _Recorder:
    def __getattr__(self, _name):
        return lambda *a, **k: None


class _Ausfuehrer:
    recorder = _Recorder()

    def __init__(self, schritt):
        self.schritt = schritt
        self.plaene = []

    def execute(self, plan, **kw):
        self.plaene.append(plan)
        aus = {"ok": bool(self.schritt.get("ok")), "plan": plan.to_dict(), "executed": {"s1": dict(self.schritt)},
               "state": {}, "elapsed_s": 2.0, "replans": 0}
        if self.schritt.get("pending"):
            aus.update({"ok": False, "pending": True})
        return aus


def _client(monkeypatch, schritt):
    import core.difficulty_router as dr

    class _K:
        def classify(self, intent):
            return {"level": "medium", "score": 0.9, "method": "qwen-cosine", "reason": "x"}

    class _Planer:
        async def aplan(self, intent, context=None):
            raise AssertionError("kein Planer")

    monkeypatch.setattr(dr, "get_router", lambda: _K())
    monkeypatch.setenv("SOM_NOPLAN_FALLBACK", "0")
    monkeypatch.setenv("MULTI_VERB_ROUTING", "0")
    app = FastAPI()
    app.include_router(router)
    app.state.plan_executor = _Ausfuehrer(schritt)
    app.state.multihop_planner = _Planer()
    app.state.final_synthesizer = None
    return TestClient(app), app.state.plan_executor


KONTEXT = {"space": "ideas", "bubble": {"id": "b0b1e001", "title": "Stadtfest"},
           "cards": [{"id": "a0000001", "title": "Sponsoren"}]}


def test_tabelle_ohne_spalten_ist_hinweis_und_zurueck_fragt_danach(monkeypatch) -> None:
    client, _ = _client(monkeypatch, {"ok": False, "result": None,
                                      "error": "RuntimeError: Ideen-Werkzeug meldet einen Fehler "
                                               "(idea_format_table_no_columns)"})
    body = client.post("/api/multihop/execute",
                       json={"intent": "Mach aus Sponsoren eine Tabelle", "context": KONTEXT}).json()
    assert body["ok"] is False and body["hinweis"] is True
    assert body["final_text"].startswith("Der Text dieser Karte hat keine Spalten")
    assert zv._voriger == {"art": "hinweis"}
    wahl = zv.entscheiden("", "Zurück", ist_abgelehnt=lambda g: False, lesen=_frische_gruppe())
    assert wahl["aktion"] == "fragen"


def test_zurueck_rueckfrage_ist_als_rueckfrage_gekennzeichnet(monkeypatch) -> None:
    client, _ = _client(monkeypatch, {"ok": False, "result": None,
                                      "error": zv.FRAGE_MARKE + "Die letzte Änderung ist älter. Sag „Ja, zurück“."})
    body = client.post("/api/multihop/execute", json={"intent": "Zurück", "context": KONTEXT}).json()
    assert body["ok"] is False and body["rueckfrage"] is True and "hinweis" not in body


def test_angenommener_auftrag_ohne_dauer(monkeypatch) -> None:
    client, _ = _client(monkeypatch, {"ok": False, "pending": True, "capability": "idea_expand",
                                      "result": {"auftrag_id": A1, "agent": "brain-ideas"}})
    body = client.post("/api/multihop/execute",
                       json={"intent": "Formuliere Sponsoren aus", "context": KONTEXT}).json()
    assert body["status"] == "angenommen" and body["ok"] is True and "elapsed_s" not in body
    assert zv._voriger == {"art": "angenommen"}


# ── Punkt 4: Stand eines Auftrags (die App liest ihn, es gibt keine Zustellung an "api") ──

def _zeile(status, pruefung=None, beendet=None, **mehr):
    return {"id": A1, "capability": "idea_explain", "status": status, "angelegt": _iso(0),
            "beendet": beendet, "pruefung": pruefung, **mehr}


@pytest.mark.parametrize("zeile, erwartet", [
    (_zeile("offen"), {"stand": "laeuft"}),
    (_zeile("fertig"), {"stand": "laeuft"}),                                         # noch nicht geprueft
    (_zeile("fertig", {"rueckweg": "geschrieben", "fortsetzung": "laeuft"}, _iso(80)), {"stand": "laeuft"}),
    (_zeile("fertig", {"rueckweg": "geschrieben", "meldung": "Die Einschätzung steht an der Bubble, mit Verweis "
                                                             "auf die Notiz.", "fortsetzung": "fertig"}, _iso(84)),
     {"stand": "fertig", "ok": True, "dauer_s": 84.0,
      "meldung": "Die Einschätzung steht an der Bubble, mit Verweis auf die Notiz."}),
    (_zeile("abgelehnt", {"rueckweg": "abgebrochen", "meldung": "Das White Paper wurde abgebrochen."}, _iso(4)),
     {"stand": "abgebrochen", "ok": True, "hinweis": True, "dauer_s": 4.0,
      "meldung": "Das White Paper wurde abgebrochen."}),
    (_zeile("laeuft", {"rueckweg": "abgebrochen", "meldung": "Das White Paper wurde abgebrochen."}),
     {"stand": "abgebrochen", "ok": True, "hinweis": True, "meldung": "Das White Paper wurde abgebrochen."}),
    (_zeile("abgelaufen", {"verified": None, "reason": "abgelaufen"}, _iso(600)),
     {"stand": "fehler", "ok": False, "dauer_s": 600.0,
      "meldung": "Der Auftrag kam nicht rechtzeitig zurück; es wurde nichts geändert."}),
])
def test_stand_einer_zeile(zeile, erwartet) -> None:
    assert st.stand(A1, lambda i: zeile) == {**erwartet, "auftrag_id": A1}


@pytest.mark.parametrize("pruefung, ergebnis, meldung", [
    # Lauf 7 (falsch gruen): Werkzeug fehlte, Pruefung UNVERIFIED (placeholder) -> stand "fertig".
    ({"verified": None, "reason": "placeholder", "kind": "UNVERIFIED", "fortsetzung": "keine"},
     "Werkzeug bubble_evaluate nicht verfügbar", st.WERKZEUG_FEHLT_SATZ),
    ({"verified": True, "reason": "ok", "fortsetzung": "keine"},
     {"text": "Error: unknown tool bubble_evaluate"}, st.WERKZEUG_FEHLT_SATZ),
    ({"verified": None, "reason": "placeholder", "kind": "UNVERIFIED", "fortsetzung": "keine"},
     "Bewertung gesetzt.", st.UNGEPRUEFT_SATZ),
    ({"verified": None, "reason": "kein Pruefer", "kind": None, "fortsetzung": "keine"}, None, st.UNGEPRUEFT_SATZ),
])
def test_stand_fertig_nur_mit_bestaetigter_pruefung(pruefung, ergebnis, meldung) -> None:
    zeile = {**_zeile("fertig", pruefung, _iso(78)), "capability": "bubble_evaluate", "ergebnis": ergebnis}
    aus = st.stand(A1, lambda i: zeile)
    assert aus["stand"] == "fehler" and aus["ok"] is False and aus["meldung"] == meldung


def test_stand_fertig_mit_bestaetigter_pruefung() -> None:
    zeile = {**_zeile("fertig", {"verified": True, "reason": "ok", "fortsetzung": "keine"}, _iso(12)),
             "ergebnis": "Bewertung gesetzt."}
    assert st.stand(A1, lambda i: zeile) == {"stand": "fertig", "ok": True, "meldung": "Der Auftrag ist fertig.",
                                             "dauer_s": 12.0, "auftrag_id": A1}


def test_tabelle_liest_das_ergebnis_fuer_den_stand(monkeypatch) -> None:
    gesendet = []
    tabelle = aa.AuftragsTabelle.__new__(aa.AuftragsTabelle)
    monkeypatch.setattr(tabelle, "_req", lambda methode, params=None, **kw: gesendet.append(params) or [],
                        raising=False)
    tabelle.lesen(A1)
    assert "ergebnis" in gesendet[0]["select"].split(",")


def test_stand_vorschlag_nennt_die_saetze() -> None:
    zeile = _zeile("fertig", {"rueckweg": "vorschlag", "fortsetzung": "fertig",
                              "meldung": vs.meldung_geaendert("Sponsoren")}, _iso(43))
    aus = st.stand(A1, lambda i: zeile)
    assert aus["stand"] == "vorschlag" and aus["hinweis"] is True and aus["dauer_s"] == 43.0
    assert aus["meldung"].startswith("Die Notiz „Sponsoren“ wurde inzwischen geändert")
    # Ohne Rueckweg in der Zeile laesst sich nichts uebernehmen: kein Satz, der nur so tut.
    assert aus["saetze"] == ["Vorschlag zeigen", "Vorschlag verwerfen"]


@pytest.mark.parametrize("vertrag, werte, schreibt", [
    ("ausformulieren", None, "Vorschlag trotzdem übernehmen"),   # ersetzt die Notiz: nur dieser Satz schreibt
    ("einschaetzen", None, "Vorschlag übernehmen"),               # geht an die Bubble, ohne Warnrunde
    ("bewerten", {"score": 70, "begruendung": "Trägt."}, None),   # zwei Aufrufe: nicht uebernehmbar
])
def test_stand_vorschlag_nennt_den_satz_der_wirklich_schreibt(vertrag, werte, schreibt) -> None:
    mehr = {"werte": werte} if werte else {}
    zeile = {**_vorschlag_zeile(vertrag, **mehr), "status": "fertig", "angelegt": _iso(0)}
    zeile["pruefung"] = {**zeile["pruefung"], "fortsetzung": "fertig"}
    aus = st.stand(A1, lambda i: zeile)
    erwartet = ["Vorschlag zeigen", schreibt, "Vorschlag verwerfen"] if schreibt else [
        "Vorschlag zeigen", "Vorschlag verwerfen"]
    assert aus["stand"] == "vorschlag" and aus["saetze"] == erwartet


def test_stand_folgt_der_kette_des_white_papers() -> None:
    zeilen = {A1: _zeile("fertig", {"rueckweg": "weiter", "naechster_auftrag": A2,
                                    "meldung": "White Paper: Abschnitt 1 von 2 fertig."}, _iso(60)),
              A2: {**_zeile("laeuft"), "id": A2}}
    assert st.stand(A1, zeilen.get) == {"stand": "laeuft", "auftrag_id": A1}
    zeilen[A2] = {**_zeile("fertig", {"rueckweg": "geschrieben", "meldung": "Das White Paper steht.",
                                      "fortsetzung": "fertig"}, _iso(150)), "id": A2}
    assert st.stand(A1, zeilen.get) == {"stand": "fertig", "ok": True, "meldung": "Das White Paper steht.",
                                        "dauer_s": 150.0, "auftrag_id": A1}


def test_stand_unbekannt_und_nicht_lesbar() -> None:
    assert st.stand("kein-auftrag", lambda i: None)["stand"] == "unbekannt"
    assert st.stand(A1, lambda i: None)["stand"] == "unbekannt"

    def kaputt(_i):
        raise RuntimeError("netz")
    assert st.stand(A1, kaputt)["meldung"] == "Der Stand lässt sich gerade nicht lesen."


def test_stand_ueber_den_router(monkeypatch) -> None:
    class Tabelle:
        def lesen(self, i):
            return _zeile("fertig", {"rueckweg": "geschrieben", "meldung": "Die Notiz ist ausformuliert.",
                                     "fortsetzung": "fertig"}, _iso(165))
    monkeypatch.setattr(aa, "tabelle_aus_umgebung", lambda: Tabelle())
    client, _ = _client(monkeypatch, {"ok": True})
    body = client.get(f"/api/agent-auftraege/{A1}/stand").json()
    assert body == {"stand": "fertig", "ok": True, "meldung": "Die Notiz ist ausformuliert.", "dauer_s": 165.0,
                    "auftrag_id": A1}


# ── Punkt 3: Vorschlag zeigen, übernehmen, verwerfen ──────────────────────

def _vorschlag_zeile(vertrag="ausformulieren", **pruefung):
    return {"id": A1, "capability": "idea_expand", "beendet": _iso(43),
            "uebergabe": {"rueckweg": {"vertrag": vertrag, "karte_id": KARTE_ID, "bubble_id": BUBBLE_ID}},
            "pruefung": {"rueckweg": "vorschlag", "reason": "card changed since order", "titel": "Sponsoren",
                         "vorschlag": "Drei Zusagen, Frist bis März.", "werte": {"text": "Drei Zusagen, Frist bis März."},
                         **pruefung}}


@pytest.mark.parametrize("satz, art", [("Vorschlag zeigen", "zeigen"), ("Zeig mir den Vorschlag", "zeigen"),
                                       ("Vorschlag übernehmen", "uebernehmen"), ("Übernimm den Vorschlag", "uebernehmen"),
                                       ("Vorschlag trotzdem übernehmen", "trotzdem"),
                                       ("Übernimm den Vorschlag trotzdem", "trotzdem"),
                                       ("Vorschlag verwerfen", "verwerfen"), ("Zeig die Notizen", None)])
def test_vorschlag_saetze(satz, art) -> None:
    assert vs.satz_art(satz) == art


def test_vorschlag_zeigen_und_uebernehmen_rein() -> None:
    zeile = _vorschlag_zeile()
    # Ausformulieren ersetzt den Text der Notiz: das sagt schon das Zeigen, mit dem Satz zum Ersetzen.
    assert vs.zeigen(zeile).startswith("Vorschlag für „Sponsoren“: Drei Zusagen, Frist bis März. Er würde den "
                                       "jetzigen Text der Notiz ersetzen, auch deine Änderung. "
                                       "Sag „Vorschlag trotzdem übernehmen“")
    assert vs.zeigen(_vorschlag_zeile("einschaetzen")).endswith(
        "Sag „Vorschlag übernehmen“, dann schreibe ich ihn an die Notiz, oder „Vorschlag verwerfen“.")
    assert "ersetzen" in vs.meldung_geaendert("Sponsoren", ueberschreibt=True)
    assert "ersetzen" not in vs.meldung_geaendert("Sponsoren")
    aufrufe, satz = vs.uebernehmen(zeile)
    assert aufrufe == [("idea.update", {"id": KARTE_ID, "content": "Drei Zusagen, Frist bis März."})]
    assert satz == "Der Vorschlag steht jetzt in „Sponsoren“."
    # Bewerten braucht zwei Aufrufe: nur zeigen, mit dem festen Satz zum Setzen.
    aufrufe, satz = vs.uebernehmen(_vorschlag_zeile("bewerten", werte={"score": 70, "begruendung": "Trägt."}))
    assert aufrufe == [] and "Bewerte" in satz
    assert vs.uebernehmen(_vorschlag_zeile(reason="card missing"))[0] == []
    assert vs.zeigen(None) == vs.KEINER


class _VorschlagTabelle:
    def __init__(self, zeile):
        self.zeile, self.erledigt, self.abfragen = zeile, [], []

    def letzter_vorschlag(self, seit, bubble_id=None):
        self.abfragen.append(bubble_id)
        return None if self.erledigt or self.zeile is None else self.zeile

    def vorschlag_erledigen(self, zeile, wie):
        if self.erledigt:
            return False
        self.erledigt.append(wie)
        return True


def test_router_vorschlag_uebernehmen_ein_fester_aufruf(monkeypatch) -> None:
    tabelle = _VorschlagTabelle(_vorschlag_zeile())
    monkeypatch.setattr(aa, "tabelle_aus_umgebung", lambda: tabelle)
    client, ausfuehrer = _client(monkeypatch, {"ok": True, "result": {"content": [{"type": "text", "text": "{}"}]}})
    body = client.post("/api/multihop/execute", json={"intent": "Vorschlag zeigen", "context": KONTEXT}).json()
    assert ausfuehrer.plaene == [] and body["final_text"].startswith("Vorschlag für „Sponsoren“")
    # Lauf 5: "Vorschlag übernehmen" warnt zuerst und schreibt nichts (die Notiz wurde geändert).
    body = client.post("/api/multihop/execute", json={"intent": "Vorschlag übernehmen", "context": KONTEXT}).json()
    assert ausfuehrer.plaene == [] and tabelle.erledigt == [] and body["hinweis"] is True
    assert body["final_text"].startswith("Achtung: Der Vorschlag ersetzt den jetzigen Text der Notiz „Sponsoren“")
    assert "Geändert habe ich nichts." in body["final_text"]
    body = client.post("/api/multihop/execute",
                       json={"intent": "Vorschlag trotzdem übernehmen", "context": KONTEXT}).json()
    (plan,) = ausfuehrer.plaene
    assert plan.hops[0].capability == "idea.update"
    assert json.loads(plan.hops[0].arg_template) == {"id": KARTE_ID, "content": "Drei Zusagen, Frist bis März."}
    assert tabelle.erledigt == ["uebernommen"] and body["final_text"] == "Der Vorschlag steht jetzt in „Sponsoren“."
    # Zweites Uebernehmen: nichts mehr offen, kein zweiter Aufruf.
    body = client.post("/api/multihop/execute",
                       json={"intent": "Vorschlag trotzdem übernehmen", "context": KONTEXT}).json()
    assert len(ausfuehrer.plaene) == 1 and body["final_text"] == vs.KEINER and body["hinweis"] is True


@pytest.mark.parametrize("vertrag, ereignis", [("ausformulieren", "idea.update"),
                                               ("einschaetzen", "idea.rate_reason")])
def test_router_trotzdem_schreibt_direkt_ohne_warnrunde(monkeypatch, vertrag, ereignis) -> None:
    # Abgleich mit der Huelle: Sie zeigt die Warnung selbst und sendet beim zweiten Klick gleich
    # "Vorschlag trotzdem übernehmen". Das gilt sofort, ohne vorheriges "Vorschlag übernehmen".
    tabelle = _VorschlagTabelle(_vorschlag_zeile(vertrag))
    monkeypatch.setattr(aa, "tabelle_aus_umgebung", lambda: tabelle)
    client, ausfuehrer = _client(monkeypatch, {"ok": True, "result": {"content": [{"type": "text", "text": "{}"}]}})
    body = client.post("/api/multihop/execute",
                       json={"intent": "Vorschlag trotzdem übernehmen", "context": KONTEXT}).json()
    (plan,) = ausfuehrer.plaene
    assert plan.hops[0].capability == ereignis and tabelle.erledigt == ["uebernommen"]
    assert body.get("hinweis") is not True and not body["final_text"].startswith("Achtung")


def test_router_vorschlag_einschaetzen_schreibt_ohne_warnung(monkeypatch) -> None:
    # Die Einschaetzung geht an die Bubble, die Notiz bleibt: kein Ueberschreiben, keine Warnung.
    tabelle = _VorschlagTabelle(_vorschlag_zeile("einschaetzen"))
    monkeypatch.setattr(aa, "tabelle_aus_umgebung", lambda: tabelle)
    client, ausfuehrer = _client(monkeypatch, {"ok": True, "result": {"content": [{"type": "text", "text": "{}"}]}})
    body = client.post("/api/multihop/execute", json={"intent": "Vorschlag übernehmen", "context": KONTEXT}).json()
    (plan,) = ausfuehrer.plaene
    assert plan.hops[0].capability == "idea.rate_reason" and tabelle.erledigt == ["uebernommen"]


def test_router_vorschlag_verwerfen(monkeypatch) -> None:
    tabelle = _VorschlagTabelle(_vorschlag_zeile())
    monkeypatch.setattr(aa, "tabelle_aus_umgebung", lambda: tabelle)
    client, ausfuehrer = _client(monkeypatch, {"ok": True})
    body = client.post("/api/multihop/execute", json={"intent": "Vorschlag verwerfen", "context": {}}).json()
    assert ausfuehrer.plaene == [] and tabelle.erledigt == ["verworfen"] and body["ok"] is True
    assert body["final_text"].startswith("Der Vorschlag ist verworfen")


class _ErledigtTabelle(_VorschlagTabelle):
    """Merkt das Erledigen an der Zeile, wie die echte Tabelle (pruefung.vorschlag_erledigt)."""

    def vorschlag_erledigen(self, z, wie):
        ok = super().vorschlag_erledigen(z, wie)
        if ok:
            z["pruefung"] = {**z["pruefung"], "vorschlag_erledigt": wie}
        return ok

    def letzter_erledigter_vorschlag(self, seit, bubble_id=None):
        return self.zeile if self.erledigt else None

    def lesen(self, i):
        return {**self.zeile, "status": "fertig", "angelegt": _iso(0)}


def test_verwerfen_nach_uebernehmen_sagt_es_ehrlich_und_stand_folgt(monkeypatch) -> None:
    # Lauf 7: "Vorschlag verwerfen" nach dem Uebernehmen sagte "nichts geändert", /stand blieb "vorschlag".
    tabelle = _ErledigtTabelle(_vorschlag_zeile("einschaetzen"))
    monkeypatch.setattr(aa, "tabelle_aus_umgebung", lambda: tabelle)
    client, ausfuehrer = _client(monkeypatch, {"ok": True, "result": {"content": [{"type": "text", "text": "{}"}]}})
    assert client.get(f"/api/agent-auftraege/{A1}/stand").json()["stand"] == "vorschlag"
    client.post("/api/multihop/execute", json={"intent": "Vorschlag übernehmen", "context": KONTEXT})
    assert len(ausfuehrer.plaene) == 1
    body = client.get(f"/api/agent-auftraege/{A1}/stand").json()
    assert body["stand"] == "uebernommen" and body["ok"] is True and "saetze" not in body
    body = client.post("/api/multihop/execute", json={"intent": "Vorschlag verwerfen", "context": KONTEXT}).json()
    assert body["final_text"] == vs.SCHON_UEBERNOMMEN and "nichts geändert" not in body["final_text"]
    assert body["hinweis"] is True and len(ausfuehrer.plaene) == 1 and tabelle.erledigt == ["uebernommen"]
    # Ein zweites Uebernehmen sagt dasselbe, statt "kein offener Vorschlag".
    body = client.post("/api/multihop/execute",
                       json={"intent": "Vorschlag trotzdem übernehmen", "context": KONTEXT}).json()
    assert body["final_text"] == vs.SCHON_UEBERNOMMEN and len(ausfuehrer.plaene) == 1


def test_tabelle_letzter_erledigter_vorschlag_fragt_nur_erledigte(monkeypatch) -> None:
    gesendet = []
    tabelle = aa.AuftragsTabelle.__new__(aa.AuftragsTabelle)
    monkeypatch.setattr(tabelle, "_req", lambda methode, params=None, **kw: gesendet.append((methode, params)) or [],
                        raising=False)
    assert tabelle.letzter_erledigter_vorschlag("2026-11-24T00:00:00+00:00", "b0b1e001") is None
    ((methode, params),) = gesendet
    assert methode == "GET" and params["pruefung->>vorschlag_erledigt"] == "not.is.null"
    assert params["pruefung->>rueckweg"] == "eq.vorschlag" and params["uebergabe->rueckweg->>bubble_id"] == "eq.b0b1e001"


def test_verwerfen_nach_verwerfen_sagt_schon_verworfen(monkeypatch) -> None:
    tabelle = _ErledigtTabelle(_vorschlag_zeile())
    monkeypatch.setattr(aa, "tabelle_aus_umgebung", lambda: tabelle)
    client, _ = _client(monkeypatch, {"ok": True})
    client.post("/api/multihop/execute", json={"intent": "Vorschlag verwerfen", "context": KONTEXT})
    body = client.post("/api/multihop/execute", json={"intent": "Vorschlag verwerfen", "context": KONTEXT}).json()
    assert body["final_text"] == vs.SCHON_VERWORFEN
    body = client.post("/api/multihop/execute", json={"intent": "Vorschlag zeigen", "context": KONTEXT}).json()
    assert body["final_text"] == vs.KEINER


def test_stand_nach_vorschlag_verwerfen_sagt_verworfen(monkeypatch) -> None:
    # Nachmessung 23.11.2026: /stand sagte nach "Vorschlag verwerfen" weiter "vorschlag".
    zeile = _vorschlag_zeile()

    class _Tabelle(_VorschlagTabelle):
        def vorschlag_erledigen(self, z, wie):
            ok = super().vorschlag_erledigen(z, wie)
            z["pruefung"] = {**z["pruefung"], "vorschlag_erledigt": wie}
            return ok

        def lesen(self, i):
            return self.zeile

    tabelle = _Tabelle(zeile)
    monkeypatch.setattr(aa, "tabelle_aus_umgebung", lambda: tabelle)
    client, _ = _client(monkeypatch, {"ok": True})
    assert client.get(f"/api/agent-auftraege/{A1}/stand").json()["stand"] == "vorschlag"
    client.post("/api/multihop/execute", json={"intent": "Vorschlag verwerfen", "context": {}})
    body = client.get(f"/api/agent-auftraege/{A1}/stand").json()
    assert body["stand"] == "verworfen" and body["ok"] is True and body["hinweis"] is True
    assert body["meldung"] == "Der Vorschlag ist verworfen; an der Notiz ist nichts geändert."
    assert "saetze" not in body


@pytest.mark.parametrize("kette", [False, True], ids=["einzeln", "zweiter-teil-der-kette"])
def test_stand_abgelehnter_dokument_auftrag_ist_fehler_mit_satz(monkeypatch, kette) -> None:
    # Lauf 5 der Pruefung: der Agent schrieb statt der Zusammenfassung etwas anderes, die Pruefung
    # lehnte ab. /stand muss "fehler" mit dem deutschen Satz liefern, sonst haengt "arbeitet ...".
    from core import ideen_dokument
    from core.nachfasser import Nachfasser

    zeile = _zeile("fertig", None, _iso(83))
    zeile["uebergabe"] = {"rueckweg": {"vertrag": "zusammenfassen", "bubble_id": BUBBLE_ID}}
    zeilen = {A1: {**zeile, "id": A1}}

    class _Tabelle:
        def zu_pruefen(self):
            return [z for z in zeilen.values() if z["pruefung"] is None]

        def beendete_ohne_meldung(self):
            return []

        def abgelaufene_markieren(self):
            return 0

        def pruefung_setzen(self, i, p):
            zeilen[i]["pruefung"] = dict(p)
            return True

        def pruefung_ergaenzen(self, i, p):
            zeilen[i]["pruefung"] = {**zeilen[i]["pruefung"], **p}

        def lesen(self, i):
            return zeilen.get(i)

        def anlegen(self, **kw):
            raise AssertionError("kein naechster Teil nach einer Ablehnung")

    monkeypatch.setattr(ideen_dokument, "verarbeiten", lambda a, **kw: {
        "verified": False, "reason": "result rejected", "kind": "ideen_dokument", "rueckweg": "abgelehnt",
        "meldung": ideen_dokument._FORM})
    tabelle = _Tabelle()
    Nachfasser(tabelle, validator=None, router=None, plan_executor=None,
               dokument_anschluss=lambda a, anlegen: {}).runde()
    erste = A1
    if kette:
        erste = A2
        zeilen[A2] = {**_zeile("fertig", {"rueckweg": "weiter", "naechster_auftrag": A1, "fortsetzung": "fertig",
                                          "meldung": "Abschnitt 1 von 2 fertig."}, _iso(40)), "id": A2}
    monkeypatch.setattr(aa, "tabelle_aus_umgebung", lambda: tabelle)
    client, _ = _client(monkeypatch, {"ok": True})
    body = client.get(f"/api/agent-auftraege/{erste}/stand").json()
    assert body["stand"] == "fehler" and body["ok"] is False
    assert body["meldung"] == "Der Agent hat nicht in der vereinbarten Form geantwortet; es wurde nichts geändert."


def test_nachfasser_vorschlag_ist_kein_fehlschlag() -> None:
    from core.nachfasser import Nachfasser

    class Tabelle:
        def __init__(self):
            self.pruefungen = []

        def pruefung_setzen(self, i, p):
            return True

        def pruefung_ergaenzen(self, i, p):
            self.pruefungen.append(p)

    tabelle = Tabelle()
    n = Nachfasser(tabelle, validator=None, router=None, plan_executor=None,
                   rueckweg_anschluss=lambda a: {"lies_karte": lambda k: None, "aufruf": None, "pruefe": None})
    import core.ideen_rueckweg as rw
    original = rw.zurueckschreiben
    try:
        rw.zurueckschreiben = lambda a, **k: {"verified": None, "reason": "card changed since order",
                                              "kind": "ideen_rueckweg", "rueckweg": "vorschlag",
                                              "meldung": vs.meldung_geaendert("Sponsoren"), "vorschlag": "x"}
        n._rueckweg({"id": A1, "capability": "idea_expand", "uebergabe": {"rueckweg": {}}},
                    {"geprueft": 0, "bestaetigt": 0, "abgelehnt_pruefung": 0})
    finally:
        rw.zurueckschreiben = original
    (abschluss,) = tabelle.pruefungen
    assert abschluss["fortsetzung"] == "fertig" and "fortsetzung_fehler" not in abschluss
