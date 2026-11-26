"""Bubble-Bewertung durch fuenf Rollen: Anfrage, Strukturgraph, Raster, alles oder nichts (ohne Modell)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
KARTENREGELN = Path(__file__).resolve().parents[4] / "_contract" / "kartenregeln.py"


def _laden(name: str, pfad: Path):
    spec = importlib.util.spec_from_file_location(name, pfad)
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    spec.loader.exec_module(modul)
    return modul


bb = _laden("bubble_bewertung_test", APP / "services" / "bubble_bewertung.py")
task = _laden("mirofish_task_test", APP / "models" / "task.py")

STAND = "sha256:" + "ab" * 32


def _anfrage(**mehr):
    roh = {
        "titel": "Probelauf Stadtfest",
        "kartenstand": STAND,
        "karten": [
            {"k": "K1", "titel": "Bühnenplan", "text": "Zwei Bühnen, Hauptbühne am Marktplatz, Aufbau ab Freitag."},
            {"k": "K2", "titel": "Sponsoren", "text": "Druckerei und Getränkehandel angefragt, Antwort offen."},
            {"k": "K3", "titel": "Messe", "text": ""},
            {"k": "K4", "titel": "Plakate", "text": "Format A1"},
        ],
        "verbindungen": [["K1", "K2", "related"], ["K2", "K1"]],
        "white_paper": "Das Stadtfest hat einen Bühnenplan und Sponsoren.",
    }
    roh.update(mehr)
    return roh


def _urteil(sicht, punkte=(3, 3, 3, 3, 3), todos=None, **mehr):
    aus = {
        "kriterien": [{"name": name, "punkte": p, "begruendung": "Steht so im Dokument."}
                      for (name, _), p in zip(bb.KRITERIEN[sicht], punkte)],
        "urteil": f"{bb.SICHT_NAMEN[sicht]} ist zur Hälfte da.",
        "todos": todos if todos is not None else [
            {"titel": "Messe füllen", "fehlt": "Die Karte Messe ist leer.", "karten": ["K3"], "punkte": 4,
             "art": "karte_fuellen"}],
    }
    aus.update(mehr)
    return aus


class _Llm:
    """Wie LLMClient: chat_json ruft self.chat, bei kaputtem JSON genau eine Nachfrage."""

    def __init__(self, antworten):
        self.antworten = antworten

    def _sicht(self, messages):
        kopf = messages[0]["content"]
        return next(s for s, name in bb.SICHT_NAMEN.items() if f"„{name}“" in kopf)

    def chat(self, messages, **kwargs):
        liste = self.antworten[self._sicht(messages)]
        wert = liste.pop(0)
        if isinstance(wert, BaseException):
            raise wert
        return wert if isinstance(wert, str) else json.dumps(wert, ensure_ascii=False)

    def chat_json(self, messages, temperature=0.3, max_tokens=4096):
        text = self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        zweite = self.chat(messages + [{"role": "user", "content": "nur JSON"}])
        try:
            return json.loads(zweite)
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON format from LLM")


def _dienst(antworten):
    return bb.BubbleBewertung(task.TaskManager(), lambda: _Llm(antworten), status_laeuft=task.TaskStatus.PROCESSING)


def _gut():
    return {s: [_urteil(s)] for s in bb.SICHTEN}


# ── Grenzen wie in den Kartenregeln ──────────────────────────────────────────

@pytest.mark.skipif(not KARTENREGELN.exists(), reason="Kartenregeln nur im Repo, nicht im Abbild")
def test_grenzen_wie_in_den_kartenregeln():
    regeln = _laden("kartenregeln_test", KARTENREGELN)
    for name in ("SICHTEN", "SICHT_NAMEN", "SICHT_MAX", "TODO_ARTEN", "TODOS_JE_SICHT", "TODO_TITEL_ZEICHEN",
                 "TODO_FEHLT_ZEICHEN", "TODO_KARTEN_MAX", "URTEIL_ZEICHEN", "LEER_UNTER_WOERTERN",
                 "DUENN_UNTER_ZEICHEN"):
        assert getattr(bb, name) == getattr(regeln, name), name
    assert len(bb.KRITERIEN) == len(regeln.SICHTEN)
    assert all(len(k) * bb.KRITERIUM_MAX == regeln.SICHT_MAX for k in bb.KRITERIEN.values())


# ── Anfrage ──────────────────────────────────────────────────────────────────

def test_anfrage_wird_geprueft_und_sortiert():
    roh = _anfrage()
    roh["karten"] = list(reversed(roh["karten"]))
    anfrage = bb.anfrage_pruefen(roh)
    assert [c["k"] for c in anfrage["karten"]] == ["K1", "K2", "K3", "K4"]
    assert anfrage["verbindungen"] == [("K1", "K2", "related"), ("K2", "K1", "related")]
    assert anfrage["nur_schaetzen"] is False


@pytest.mark.parametrize("aenderung, satz", [
    ({"titel": " "}, "Titel"),
    ({"kartenstand": "sha256:kaputt"}, "kartenstand"),
    ({"karten": []}, "keine Karten"),
    ({"karten": [{"k": "K1", "titel": "a"}, {"k": "K1", "titel": "b"}]}, "doppelt"),
    ({"karten": [{"k": "1", "titel": "a"}]}, "Kürzel"),
    ({"verbindungen": [["K1", "K9"]]}, "nicht gibt"),
    ({"verbindungen": [["K1"]]}, "Form"),
    ({"white_paper": 3}, "white_paper"),
    ({"nur_schaetzen": "ja"}, "nur_schaetzen"),
])
def test_falsche_anfrage_wird_abgesagt(aenderung, satz):
    with pytest.raises(bb.AnfrageFehler, match=satz):
        bb.anfrage_pruefen(_anfrage(**aenderung))


def test_zu_viele_karten_und_zu_gross_werden_abgesagt_nicht_gekuerzt():
    viele = [{"k": f"K{n}", "titel": f"Karte {n}", "text": ""} for n in range(1, bb.KARTEN_MAX + 2)]
    with pytest.raises(bb.AnfrageFehler, match="höchstens"):
        bb.anfrage_pruefen(_anfrage(karten=viele))
    gross = [{"k": "K1", "titel": "Druckerei", "text": "x" * (bb.DOKUMENT_BYTES_MAX + 10)}]
    with pytest.raises(bb.AnfrageFehler, match="gekürzt wird nicht"):
        bb.anfrage_pruefen(_anfrage(karten=gross, verbindungen=[]))


def test_ein_300_kb_dokument_geht_ungekuerzt_durch():
    karten = [{"k": f"K{n}", "titel": f"Karte {n}", "text": "Stadtfest " * 250} for n in range(1, 121)]
    anfrage = bb.anfrage_pruefen(_anfrage(karten=karten, verbindungen=[]))
    assert 280 * 1024 < bb.dokument_bytes(anfrage) < bb.DOKUMENT_BYTES_MAX
    assert all(c["text"] == ("Stadtfest " * 250).strip() for c in anfrage["karten"])


def test_dokument_zaeunt_ein_und_nennt_alles():
    anfrage = bb.anfrage_pruefen(_anfrage(white_paper="Text mit >>> und <<<KARTEN"))
    text = bb.dokument(anfrage)
    assert "[K3] Messe\n(leer)" in text
    assert "K1 -> K2 (related)" in text
    assert text.count("<<<KARTEN") == 1 and "›››" in text


# ── Strukturgraph ────────────────────────────────────────────────────────────

def test_strukturgraph_zaehlt_ohne_modell():
    graph = bb.strukturgraph(bb.anfrage_pruefen(_anfrage()))
    assert graph["knoten"] == 4 and graph["kanten"] == 1      # K1-K2 in beide Richtungen ist eine Kante
    assert graph["teilgraphen"] == 3
    assert graph["lose"] == ["K3", "K4"]
    assert graph["grad"] == {"K1": 1, "K2": 1, "K3": 0, "K4": 0}
    assert graph["leer"] == ["K3", "K4"] and graph["duenn"] == []
    assert graph["ohne_white_paper"] == ["K3", "K4"]


def test_strukturgraph_ohne_white_paper_und_duenne_karte():
    karten = [{"k": "K1", "titel": "Druckerei", "text": "Angebot kommt bis Freitag"}]
    graph = bb.strukturgraph(bb.anfrage_pruefen(_anfrage(karten=karten, verbindungen=[], white_paper="")))
    assert graph["white_paper"] is False and graph["ohne_white_paper"] == []
    assert graph["duenn"] == ["K1"]
    assert "kein White Paper" in bb.kennzahlen_text(graph)


def test_graph_wird_je_kartenstand_wiederverwendet():
    dienst = _dienst({})
    anfrage = bb.anfrage_pruefen(_anfrage())
    erst, wieder1 = dienst.graph(anfrage)
    zweit, wieder2 = dienst.graph(anfrage)
    assert (wieder1, wieder2) == (False, True) and erst is zweit
    anders = bb.anfrage_pruefen(_anfrage(kartenstand="sha256:" + "cd" * 32))
    assert dienst.graph(anders)[1] is False


# ── Rollen ───────────────────────────────────────────────────────────────────

def test_prompt_je_rolle_hat_raster_dokument_und_kennzahlen():
    anfrage = bb.anfrage_pruefen(_anfrage())
    graph = bb.strukturgraph(anfrage)
    for sicht in bb.SICHTEN:
        system, nutzer = bb.rollen_nachrichten(sicht, anfrage, graph)
        assert f"„{bb.SICHT_NAMEN[sicht]}“" in system["content"]
        assert all(name in system["content"] for name, _ in bb.KRITERIEN[sicht])
        assert "nenne keine Summe" in system["content"]
        assert "Karten ohne Verbindung: K3, K4." in nutzer["content"]
        assert bb.dokument(anfrage) in nutzer["content"]


def test_urteil_summe_rechnet_der_dienst():
    geprueft = bb.urteil_pruefen("coding", _urteil("coding", punkte=(5, 4, 3, 2, 1), punkte_gesamt=25),
                                 {"K1", "K3"})
    assert geprueft["punkte"] == 15
    assert [c["punkte"] for c in geprueft["kriterien"]] == [5, 4, 3, 2, 1]
    assert set(geprueft) == {"punkte", "urteil", "kriterien", "todos"}
    assert geprueft["todos"][0]["karten"] == ["K3"]


@pytest.mark.parametrize("kaputt, satz", [
    (lambda u: u["kriterien"].pop(), "Raster"),
    (lambda u: u["kriterien"].append(dict(u["kriterien"][0])), "doppelt"),
    (lambda u: u["kriterien"][0].update(punkte=6), "0 bis 5"),
    (lambda u: u["kriterien"][0].update(punkte=True), "0 bis 5"),
    (lambda u: u.update(urteil=""), "Urteil"),
    (lambda u: u["todos"][0].update(karten=["K9"]), "nicht gibt"),
    (lambda u: u["todos"][0].update(karten=[]), "nennt keine Karte"),
    (lambda u: u["todos"][0].update(art="raten"), "falsch geformt"),
])
def test_falsches_urteil_ist_ein_fehler(kaputt, satz):
    urteil = _urteil("research")
    kaputt(urteil)
    with pytest.raises(bb.RollenFehler, match=satz):
        bb.urteil_pruefen("research", urteil, {"K1", "K3"})


def test_hoechstens_fuenf_todos_und_kriterien_in_anderer_reihenfolge():
    urteil = _urteil("ideas", todos=[{"titel": f"T{n}", "fehlt": "x", "karten": [], "punkte": 1,
                                      "art": "entscheidung"} for n in range(8)])
    urteil["kriterien"].reverse()
    geprueft = bb.urteil_pruefen("ideas", urteil, {"K1"})
    assert len(geprueft["todos"]) == bb.TODOS_JE_SICHT
    assert [c["name"] for c in geprueft["kriterien"]] == [n for n, _ in bb.KRITERIEN["ideas"]]


# ── Ablauf: alles oder nichts ────────────────────────────────────────────────

def test_fuenf_gute_rollen_ergeben_ein_ergebnis():
    dienst = _dienst(_gut())
    task_id = dienst.starten(bb.anfrage_pruefen(_anfrage()), im_hintergrund=False)
    stand = dienst.stand(task_id)
    assert stand["status"] == "completed" and stand["error"] is None
    ergebnis = stand["result"]
    assert set(ergebnis["sichten"]) == set(bb.SICHTEN)
    assert ergebnis["score"] == 5 * 15 and ergebnis["aufrufe"] == 5 and stand["aufrufe"] == 5
    assert ergebnis["kartenstand"] == STAND and ergebnis["graph"]["lose"] == ["K3", "K4"]


def test_nachfrage_zaehlt_als_aufruf():
    antworten = _gut()
    antworten["research"] = ["Hier ist meine Bewertung, kein JSON.", _urteil("research")]
    dienst = _dienst(antworten)
    stand = dienst.stand(dienst.starten(bb.anfrage_pruefen(_anfrage()), im_hintergrund=False))
    assert stand["status"] == "completed" and stand["result"]["aufrufe"] == 6


@pytest.mark.parametrize("schlecht, satz", [
    (["kein JSON", "wieder kein JSON"], "kein gültiges JSON"),
    ([{"kriterien": []}], "Raster"),
    ([RuntimeError("connection refused")], "connection refused"),
    ([RuntimeError("Budget mirofish-simulation erschöpft")], "budget"),
])
def test_eine_schlechte_rolle_ergibt_keine_bewertung(schlecht, satz):
    antworten = _gut()
    antworten["rowboat"] = schlecht
    dienst = _dienst(antworten)
    stand = dienst.stand(dienst.starten(bb.anfrage_pruefen(_anfrage()), im_hintergrund=False))
    assert stand["status"] == "failed" and stand["result"] is None
    assert satz in stand["error"] and "Wissenslage" in stand["error"]


def test_schaetzen_ruft_kein_modell():
    dienst = _dienst({})
    geschaetzt = dienst.schaetzen(bb.anfrage_pruefen(_anfrage(nur_schaetzen=True)))
    assert geschaetzt["aufrufe_geschaetzt"] == 5 and geschaetzt["aufrufe_hoechstens"] == 10
    assert geschaetzt["graph_aufrufe"] == 0 and geschaetzt["graph"]["knoten"] == 4


def test_fremde_aufgabe_ist_keine_bewertung():
    dienst = _dienst({})
    fremd = dienst.aufgaben.create_task("graph_build")
    assert dienst.stand(fremd) is None and dienst.stand("gibt-es-nicht") is None


# ── Routen ───────────────────────────────────────────────────────────────────

def _app(dienst):
    flask = pytest.importorskip("flask")
    app = flask.Flask("test")
    bp = flask.Blueprint("bubble_test", "test")
    bb.routen_anmelden(bp, lambda: dienst)
    app.register_blueprint(bp, url_prefix="/api/bubble")
    return app.test_client()


def test_route_schaetzen_starten_abfragen(monkeypatch):
    dienst = _dienst(_gut())
    monkeypatch.setattr(dienst, "starten", lambda anfrage: bb.BubbleBewertung.starten(dienst, anfrage, False))
    client = _app(dienst)
    antwort = client.post("/api/bubble/evaluate", json=_anfrage(nur_schaetzen=True))
    assert antwort.status_code == 200 and antwort.get_json()["data"]["aufrufe_geschaetzt"] == 5
    antwort = client.post("/api/bubble/evaluate", json=_anfrage())
    assert antwort.status_code == 202
    daten = antwort.get_json()["data"]
    assert "graph" not in daten and daten["aufrufe_hoechstens"] == 10
    stand = client.get(f"/api/bubble/evaluate/{daten['task_id']}").get_json()["data"]
    assert stand["status"] == "completed" and stand["result"]["score"] == 75


def test_route_falsche_anfrage_und_unbekannte_aufgabe():
    client = _app(_dienst({}))
    antwort = client.post("/api/bubble/evaluate", json={"titel": "x"})
    assert antwort.status_code == 400 and "kartenstand" in antwort.get_json()["error"]
    assert client.get("/api/bubble/evaluate/unbekannt").status_code == 404
