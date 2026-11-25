"""Stufe 1: Bewertungslauf, To-do, ein White Paper je Bubble, Schwelle fuers Befoerdern.

Nur Attrappen, alle Namen erfunden. Den ganzen Weg vom Vertrag bis zum echten Ideen-Server
prueft test_ideen_vertrag_bis_werkzeug (Abschnitt "Stufe 1").
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import befoerdern_bestaetigung as bf  # noqa: E402
from core import ideen_bewertung as bew  # noqa: E402
from core import ideen_dokument as dok  # noqa: E402
from core import todo_bestaetigung as td  # noqa: E402
from core.ideen_rueckweg import RueckwegFehler  # noqa: E402

BUBBLE = {"id": "b0b1e001", "title": "Stadtfest"}
KURZ = {"K1": "a0000001", "K2": "a0000002"}
TITEL = {"K1": "Bühnenplan", "K2": "Sponsoren"}


def _sicht(punkte, todos=()):
    return {"punkte": punkte, "urteil": "Kurz begründet.", "todos": list(todos)}


def _antwort(**anders):
    sichten = {n: _sicht(20) for n in ("coding", "swe_design", "research", "rowboat", "ideas")}
    sichten.update(anders)
    return json.dumps({"sichten": sichten}, ensure_ascii=False)


def _todo(**mehr):
    return {"titel": "Sponsoren füllen", "fehlt": "K2 ist leer.", "karten": ["K2"], "punkte": 4,
            "art": "karte_fuellen", **mehr}


# ── Ergebnis des Bewertungslaufs pruefen ─────────────────────────────────

def test_gueltige_antwort_ergibt_den_lauf_mit_kennungen() -> None:
    lauf, satz = bew.bewertung_pruefen(_antwort(research=_sicht(12, [_todo()])), KURZ, TITEL)
    assert satz == "" and lauf["todos"] == [{"sicht": "research", "titel": "Sponsoren füllen",
                                             "fehlt": "„Sponsoren“ ist leer.", "karten": ["a0000002"],
                                             "punkte": 4, "art": "karte_fuellen"}]
    assert bew._regeln().lauf_score(lauf) == 92


@pytest.mark.parametrize("anders, teil", [
    ({"coding": _sicht(26)}, "keine Punktzahl"),
    ({"coding": {"punkte": "viel", "urteil": "", "todos": []}}, "keine Punktzahl"),
    ({"ideas": _sicht(5, [_todo(art="umbauen")])}, "vereinbarten Form"),
    ({"ideas": _sicht(5, [_todo(karten=["K7"])])}, "Karte bezogen, die es nicht gibt"),
    ({"ideas": _sicht(5, [_todo(karten=[])])}, "vereinbarten Form"),       # karte_fuellen braucht eine Karte
])
def test_falsche_antworten_werden_abgelehnt(anders, teil) -> None:
    lauf, satz = bew.bewertung_pruefen(_antwort(**anders), KURZ, TITEL)
    assert lauf is None and teil in satz


def test_fehlende_sicht_und_absage_werden_abgelehnt() -> None:
    ohne = json.loads(_antwort())
    del ohne["sichten"]["rowboat"]
    assert bew.bewertung_pruefen(json.dumps(ohne), KURZ, TITEL)[0] is None
    assert bew.bewertung_pruefen("Das kann ich leider nicht.", KURZ, TITEL)[0] is None


def test_mehr_als_fuenf_todos_je_sicht_werden_gekappt() -> None:
    lauf, _ = bew.bewertung_pruefen(_antwort(ideas=_sicht(5, [_todo(titel=f"T{i}") for i in range(8)])), KURZ, TITEL)
    assert [t["titel"] for t in lauf["todos"]] == ["T0", "T1", "T2", "T3", "T4"]


def test_meldung_nennt_score_sichten_und_top_drei() -> None:
    lauf, _ = bew.bewertung_pruefen(_antwort(
        research=_sicht(10, [_todo(punkte=2, titel="Quellen"), _todo(punkte=7, titel="Wettbewerb")]),
        ideas=_sicht(15, [_todo(punkte=5, titel="Tiefe"), _todo(punkte=1, titel="Klein")])), KURZ, TITEL)
    meldung = bew.bewertung_meldung(lauf, 85)
    assert meldung.startswith("Bewertung: 85 von 100 (Coding 20, SWE Design 20, Research 10, Wissenslage 20, Ideas 15).")
    assert "4 To-dos; am meisten bringen: 2. Wettbewerb (+7, Karte füllen); 3. Tiefe (+5" in meldung
    assert "„Erledige To-do N“" in meldung and "„To-do N erledigt“" in meldung


# ── Schwelle fuers Befoerdern ────────────────────────────────────────────

KARTEN = [{"id": "a0000001", "title": "Bühnenplan", "content": "Zwei Bühnen am Samstag ab Mittag.",
           "node_type": "note"},
          {"id": "a0000002", "title": "Sponsoren", "content": "Drei Zusagen, Frist bis März.", "node_type": "note"},
          {"id": "a0000003", "title": "White Paper: Stadtfest", "content": "# White Paper", "node_type": "dokument",
           "metadata": {"art": "white_paper", "quellen": ["a0000001", "a0000002"]}, "updated_at": "2026-11-25"}]
KANTEN = [{"from_node_id": "a0000001", "to_node_id": "a0000002", "edge_type": "related"},
          {"from_node_id": "a0000002", "to_node_id": "b0000099", "edge_type": "related"}]   # in eine andere Bubble


def _bubble(score, stand=None, **meta):
    regeln = bf.regeln()
    lauf = {"score": score, "kartenstand": stand or regeln.kartenstand(KARTEN, KANTEN), "zeit": "2026-11-25T09:00:00",
            "todos": [{"nr": 1, "titel": "Quellen", "punkte": 3, "erledigt": False},
                      {"nr": 2, "titel": "Wettbewerb", "punkte": 6, "erledigt": False},
                      {"nr": 3, "titel": "Erledigt", "punkte": 9, "erledigt": True}]}
    return {**BUBBLE, "score": score, "metadata": {"bewertung_lauf": lauf, **meta}}


def test_ab_der_schwelle_und_aktuell_darf_gefragt_werden(monkeypatch) -> None:
    monkeypatch.delenv(bf.SCHWELLE_EINSTELLUNG, raising=False)
    geprueft = bf.pruefen(_bubble(95), KARTEN, KANTEN)
    assert geprueft["bestaetigbar"] is True and geprueft["satz"].endswith("Befördern? Sag „Ja“.")


def test_unter_der_schwelle_nennt_score_schwelle_und_offene_todos(monkeypatch) -> None:
    monkeypatch.delenv(bf.SCHWELLE_EINSTELLUNG, raising=False)
    satz = bf.pruefen(_bubble(80, handwert={"score": 100}), KARTEN, KANTEN)["satz"]
    assert "80 von 100 Punkten, befördert wird ab 95" in satz
    assert "To-do 2 „Wettbewerb“ (+6); To-do 1 „Quellen“ (+3)" in satz and "Erledigt" not in satz
    assert satz.endswith("Befördert ist nichts.")


def test_einstellung_aendert_die_schwelle(monkeypatch) -> None:
    monkeypatch.setenv(bf.SCHWELLE_EINSTELLUNG, "80")
    assert bf.pruefen(_bubble(80), KARTEN, KANTEN)["bestaetigbar"] is True
    monkeypatch.setenv(bf.SCHWELLE_EINSTELLUNG, "kaputt")
    assert bf.schwelle_einstellung() == 95


def test_ohne_lauf_oder_veraltet_wird_nicht_befoerdert(monkeypatch) -> None:
    monkeypatch.delenv(bf.SCHWELLE_EINSTELLUNG, raising=False)
    nur_hand = {**BUBBLE, "score": 100, "metadata": {"handwert": {"score": 100}}}
    assert "noch keine Bewertung" in bf.pruefen(nur_hand, KARTEN, KANTEN)["satz"]
    alt = _bubble(99, stand="sha256:" + "0" * 64)
    assert "veraltet" in bf.pruefen(alt, KARTEN, KANTEN)["satz"]


def test_kartenstand_ignoriert_lage_und_fremde_kanten_aber_nicht_inhalt() -> None:
    regeln = bf.regeln()
    stand = regeln.kartenstand(KARTEN, KANTEN)
    verschoben = [{**k, "x": 999, "updated_at": "2027-01-01"} for k in KARTEN]
    assert regeln.kartenstand(verschoben, KANTEN[:1]) == stand
    geaendert = [{**KARTEN[0], "content": "Eine Bühne."}, *KARTEN[1:]]
    assert regeln.kartenstand(geaendert, KANTEN) != stand
    assert regeln.kartenstand(KARTEN, []) != stand


# ── To-do: Rueckfrage und "Ja, erledigen" ────────────────────────────────

def test_ja_erledigen_gilt_nur_direkt_nach_der_rueckfrage_und_einmal() -> None:
    td.verwerfen()
    assert td.satz_pruefen("Ja, erledigen") == {"satz": td.OHNE_FRAGE}

    class Lesen:
        def bubble(self, kennung):
            return {**BUBBLE, "metadata": {"bewertung_lauf": {"score": 60, "kartenstand": "sha256:" + "1" * 64,
                                                               "todos": [{"nr": 1, "titel": "Sponsoren füllen",
                                                                          "fehlt": "Leer.", "punkte": 4,
                                                                          "art": "karte_fuellen"}]}}}

    frage = td.rueckfrage({"bubble_id": "b0b1e001", "nr": 1}, lesen=Lesen())
    assert "To-do 1 „Sponsoren füllen“" in frage and "ein Abo-Aufruf" in frage
    antwort = td.satz_pruefen("Ja, erledigen")
    schluessel = antwort["argumente"].pop("bestaetigung")
    assert antwort["argumente"] == {"bubble_id": "b0b1e001", "nr": 1}
    assert td.einloesen(schluessel) is True and td.einloesen(schluessel) is False
    assert "Ein To-do 7 gibt es" in td.rueckfrage({"bubble_id": "b0b1e001", "nr": 7}, lesen=Lesen())
    assert td.satz_pruefen("Ja, erledigen") == {"satz": td.OHNE_FRAGE}        # nichts offen gemerkt


def test_white_paper_todo_geht_nicht_an_das_modell() -> None:
    bubble = {**BUBBLE, "metadata": {"bewertung_lauf": {"score": 60, "kartenstand": "x", "todos": [
        {"nr": 1, "titel": "Neu schreiben", "fehlt": "Veraltet.", "punkte": 5, "art": "white_paper"}]}}}
    with pytest.raises(RueckwegFehler, match="Erstelle ein White Paper"):
        bew.todo_frage(bubble, 1)


def test_todo_ergebnis_mit_doppeltem_titel_oder_fremder_karte_wird_abgelehnt() -> None:
    rueckweg = {"kuerzel": KURZ, "kuerzel_titel": TITEL}
    doppelt = json.dumps({"aenderungen": [{"art": "neu", "titel": "Sponsoren", "text": "Mehr.", "verbinden": ["K1"]}]})
    assert "die es schon gibt" in bew.todo_pruefen(doppelt, rueckweg)[1]
    fremd = json.dumps({"aenderungen": [{"art": "ergaenzen", "karte": "K9", "text": "Mehr."}]})
    assert bew.todo_pruefen(fremd, rueckweg)[0] is None
    lose = json.dumps({"aenderungen": [{"art": "neu", "titel": "Getränke", "text": "Mehr.", "verbinden": []}]})
    assert bew.todo_pruefen(lose, rueckweg)[0] is None


# ── Genau ein White Paper je Bubble ──────────────────────────────────────

def _papier(i, wann):
    return {"id": i, "title": "White Paper: Stadtfest", "content": "# alt", "node_type": "dokument", "x": 0, "y": 0,
            "linked_idea_id": BUBBLE["id"], "metadata": {"art": "white_paper"}, "updated_at": wann}


def test_vorhandene_white_paper_neuestes_wird_ersetzt_aeltere_sind_doppel() -> None:
    zeilen = [_papier("a0000101", "2026-11-20"), _papier("a0000102", "2026-11-24"), _papier("a0000103", "2026-11-22")]
    assert dok.vorhandene_white_paper(zeilen) == ("a0000102", ["a0000103", "a0000101"])
    assert dok.vorhandene_white_paper([]) == (None, [])


def test_white_paper_ersetzt_das_neueste_und_loescht_die_doppel() -> None:
    karten = [{"id": "a0000001", "title": "Bühnenplan", "content": "Zwei Bühnen am Samstag.", "x": 0, "y": 0,
               "node_type": "idea", "linked_idea_id": BUBBLE["id"]},
              _papier("a0000101", "2026-11-20"), _papier("a0000102", "2026-11-24")]

    class Lesen(dok.Lesen):
        def bubble(self, kennung):
            return dict(BUBBLE)

        def bubbles_mit_titel(self, titel):
            return [dict(BUBBLE)]

        def karten_der_bubble(self, kennung):
            return [dict(k) for k in karten]

        def karten(self, kennungen):
            return [dict(k) for k in karten if k["id"] in kennungen]

        def kanten(self, kennungen):
            return []

    frage, _ = dok.white_paper_frage({"bubble_id": BUBBLE["id"]}, lesen=Lesen())
    assert "Es ersetzt das vorhandene White Paper; 1 ältere Fassung wird gelöscht" in frage
    _, rueckweg = dok.auftrag_vorbereiten("idea.whitepaper", {"bubble_id": BUBBLE["id"]}, "", lesen=Lesen(),
                                          neue_kennung=lambda: "00000000-0000-4000-8000-000000000d01")
    assert rueckweg["dokument_id"] == "a0000102" and rueckweg["doppel"] == ["a0000101"] and rueckweg["ersetzt"]
    aufrufe = []

    def aufruf(ereignis, argumente):
        aufrufe.append((ereignis, argumente))
        return {"id": argumente["id"]}

    roh = json.dumps({"ueberschrift": "Bühne", "text": "Zwei Bühnen laut K1.", "verweise": ["K1"]})
    auftrag = {"id": "f0000001", "status": "fertig", "capability": "idea_whitepaper", "agent": "brain-ideas",
               "trace_id": "t1", "plan_id": "ideen_9", "hop_id": "s1", "ergebnis": roh,
               "uebergabe": {"rueckweg": rueckweg}}
    p = dok.verarbeiten(auftrag, lies_karten=Lesen().karten, anlegen=lambda **kw: "x", aufruf=aufruf,
                        pruefe=lambda e, a, q: (True, ""))
    assert p["rueckweg"] == "geschrieben" and p["ersetzt"] is True
    assert [(e, a["id"]) for e, a in aufrufe] == [("idea.document", "a0000102"), ("idea.delete", "a0000101")]
    assert "ersetzt das vorhandene" in p["meldung"] and "1 ältere Fassung ist gelöscht" in p["meldung"]


@pytest.mark.parametrize("faehigkeit,argument,gesperrt", [
    ("idea.bewertung_speichern", {"bubble_id": "b0b1e001", "lauf": {}, "kartenstand": "sha256:" + "0" * 64}, True),
    ("idea.todo_abhaken", {"bubble_id": "b0b1e001", "nr": 1, "durch": "agent"}, True),
    ("idea.todo_abhaken", {"bubble_id": "b0b1e001", "nr": 1}, True),
    ("idea.todo_abhaken", {"bubble_id": "b0b1e001", "nr": 1, "durch": "hand"}, False),
])
def test_plan_schritt_speichert_keinen_lauf_und_hakt_nicht_als_agent_ab(monkeypatch, faehigkeit, argument,
                                                                         gesperrt):
    """Einen Lauf (und damit einen Score über der Schwelle) schreibt nur der Rückweg, nie ein Plan."""
    from core import plan_executor as pe
    from core.plan_schema import HopSpec

    gebaut = []

    class _Router:
        def get_capability(self, _capability):
            return {"execution_target": "mcp:brain-ideas:spaces-ideas:x"}

    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: [])
    monkeypatch.setattr("core.capability_targets.build_executor", lambda target: gebaut.append(target))
    hop = HopSpec(step_id="s1", description="per Plan", capability=faehigkeit, arg_template=json.dumps(argument))
    ergebnis = pe.PlanExecutor(capability_router=_Router())._exec_hop(
        hop, {}, plan_ctx={"antwortkanal": {"art": "api"}, "plan_id": "p1", "trace_id": "t1"})
    if gesperrt:
        assert ergebnis.ok is False and ("nie per Plan" in ergebnis.error or "nur von Hand" in ergebnis.error)
        assert gebaut == []
    else:
        assert not (ergebnis.error or "").startswith("idea.todo_abhaken: per Plan")


@pytest.mark.parametrize("ziel,argument,gesperrt", [
    ("mcp:Brain-Ideas:Spaces-Ideas:Bubble-Bewertung", {"bubble_id": "b0b1e001"}, True),
    ("mcp:brain-ideas:spaces_ideas:bubble_bewertung", {"bubble_id": "b0b1e001"}, True),
    ("mcp:brain-ideas:spaces-ideas:bewertung-todo", {"bubble_id": "b0b1e001", "nr": 1, "durch": "agent"}, True),
    ("mcp:brain-ideas:spaces-ideas:bewertung_todo", {"bubble_id": "b0b1e001", "nr": 1, "durch": "hand"}, False),
])
def test_anderer_faehigkeitsname_mit_passendem_ziel_rutscht_nicht_durch(monkeypatch, ziel, argument, gesperrt):
    """Gesperrt wird nach Ereignis UND nach Ziel des Schritts, in jeder Schreibweise des Ziels."""
    from core import plan_executor as pe
    from core.plan_schema import HopSpec

    gebaut = []
    monkeypatch.setattr("core.plan_executor.PlanExecutor._capture_kg_hits", lambda *args: [])
    monkeypatch.setattr("core.capability_targets.build_executor", lambda target: gebaut.append(target))
    hop = HopSpec(step_id="s1", description="per Plan", capability="stadtfest.sonstwas",
                  execution_target=ziel, arg_template=json.dumps(argument))
    ergebnis = pe.PlanExecutor(capability_router=None)._exec_hop(
        hop, {}, plan_ctx={"antwortkanal": {"art": "api"}, "plan_id": "p1", "trace_id": "t1"})
    fehler = ergebnis.error or ""
    if gesperrt:
        assert ergebnis.ok is False and ("nie per Plan" in fehler or "nur von Hand" in fehler), fehler
        assert gebaut == []
    else:
        assert "nie per Plan" not in fehler and "nur von Hand" not in fehler


# ── Groesse: OpenFang nimmt hoechstens 64 KB je Nachricht (413 darueber) ──

GRENZE_OPENFANG = 64 * 1024
SATZ = "Für das Stadtfest prüfen wir Bühnenplan, Sponsoren und Druckerei; Größe und Maße folgen. "


def _grosse_bubble(anzahl, zeichen_je_karte, papier_zeichen=0, kanten_je_karte=0):
    karten = [{"id": f"a{n:07x}", "title": f"Stadtfest Punkt {n} – Bühne", "node_type": "note",
               "content": (SATZ * (zeichen_je_karte // len(SATZ) + 1))[:zeichen_je_karte],
               "x": n % 10 * 300, "y": n // 10 * 200, "linked_idea_id": BUBBLE["id"]} for n in range(1, anzahl + 1)]
    if papier_zeichen:
        karten.append({**_papier("a1000001", "2026-11-24"), "content": (SATZ * (papier_zeichen // len(SATZ) + 1))
                       [:papier_zeichen]})
    kanten = [{"from_node_id": f"a{n:07x}", "to_node_id": f"a{(n + d - 1) % anzahl + 1:07x}", "edge_type": "related"}
              for n in range(1, anzahl + 1) for d in range(1, kanten_je_karte + 1)]

    class Lesen:
        def karten_der_bubble(self, kennung):
            return [dict(k) for k in karten]

        def kanten(self, kennungen):
            return [dict(k) for k in kanten]

    return Lesen()


def test_kleine_bubble_geht_ungekuerzt_hinaus() -> None:
    nachricht, rueckweg = bew.bewerten_vorbereiten("idea.bewerten", BUBBLE, _grosse_bubble(5, 400, 2000, 1))
    k = rueckweg["kuerzung"]
    assert rueckweg["gekuerzt"] is False and k["gekuerzt"] is False and k["karten_gekuerzt"] == 0
    assert k["kanten_weggelassen"] == 0 and k["white_paper_gekuerzt"] is False
    assert "gekürzt" not in nachricht and bew.groesse(nachricht) == k["bytes"] < GRENZE_OPENFANG


def test_grosse_bubble_bleibt_unter_64_kb_und_zaehlt_ehrlich_was_fehlt() -> None:
    # 120 Karten zu je 3000 Zeichen mit Umlauten, ein langes White Paper, je Karte zwei Verbindungen.
    nachricht, rueckweg = bew.bewerten_vorbereiten("idea.bewerten", BUBBLE, _grosse_bubble(120, 3000, 30000, 2))
    k = rueckweg["kuerzung"]
    assert bew.groesse(nachricht) <= bew.NACHRICHT_BYTES < GRENZE_OPENFANG
    assert rueckweg["gekuerzt"] is True and k["white_paper_gekuerzt"] is True
    assert k["karten"] == 120 and k["karten_gekuerzt"] == 120 and bew.KARTE_MIN <= k["karte_zeichen"] < 3000
    assert k["kanten"] == 240 and 0 <= k["kanten_weggelassen"] < 240
    assert "Die Bubble ist gekürzt" in nachricht and len(rueckweg["kuerzel"]) == 120   # keine Karte fehlt ganz
    satz = bew.kuerzung_satz(k)
    assert satz.startswith(f"Gekürzt bewertet: bei 120 von 120 Karten nur der Anfang (je bis {k['karte_zeichen']} "
                           "Zeichen), ")
    assert "das White Paper nur gekürzt." in satz
    assert (f"{k['kanten_weggelassen']} von 240 Verbindungen fehlen" in satz) == (k["kanten_weggelassen"] > 0)


def test_mittlere_bubble_kuerzt_nur_karten_und_laesst_alle_verbindungen() -> None:
    nachricht, rueckweg = bew.bewerten_vorbereiten("idea.bewerten", BUBBLE, _grosse_bubble(60, 3000, 0, 1))
    k = rueckweg["kuerzung"]
    assert bew.groesse(nachricht) <= bew.NACHRICHT_BYTES
    assert k["karten_gekuerzt"] == 60 and k["karte_zeichen"] > bew.KARTE_MIN and k["kanten_weggelassen"] == 0
    assert k["white_paper_gekuerzt"] is False
    assert bew.kuerzung_satz(k).endswith("Fürs Befördern zählt nur eine ungekürzte Bewertung.")


def test_reicht_die_kuerzung_der_karten_nicht_fehlen_verbindungen_und_werden_gezaehlt() -> None:
    nachricht, rueckweg = bew.bewerten_vorbereiten("idea.bewerten", BUBBLE, _grosse_bubble(100, 2000, 0, 12))
    k = rueckweg["kuerzung"]
    assert bew.groesse(nachricht) <= bew.NACHRICHT_BYTES
    assert k["karte_zeichen"] == bew.KARTE_MIN and 0 < k["kanten_weggelassen"] < k["kanten"] == 1200
    assert f"{k['kanten_weggelassen']} von 1200 Verbindungen fehlen" in bew.kuerzung_satz(k)


def test_zu_gross_auch_gekuerzt_legt_keinen_auftrag_an() -> None:
    with pytest.raises(RueckwegFehler, match="zu groß, auch gekürzt"):
        bew.bewerten_vorbereiten("idea.bewerten", BUBBLE, _grosse_bubble(400, 2000))


def test_meldung_und_ergebnis_tragen_die_kuerzung() -> None:
    lauf, _ = bew.bewertung_pruefen(_antwort(), KURZ, TITEL, gekuerzt=True)
    kuerzung = {"gekuerzt": True, "karten": 120, "karten_gekuerzt": 80, "karte_zeichen": 420, "kanten": 300,
                "kanten_weggelassen": 0, "white_paper_gekuerzt": False}
    meldung = bew.bewertung_meldung(lauf, 100, kuerzung)
    assert "Gekürzt bewertet: bei 80 von 120 Karten nur der Anfang (je bis 420 Zeichen)." in meldung
    assert lauf["gekuerzt"] is True
    rueckweg = {"vertrag": "bewerten", "bubble_id": BUBBLE["id"], "kuerzel": KURZ, "kuerzel_titel": TITEL,
                "kartenstand": "sha256:" + "2" * 64, "gekuerzt": True, "kuerzung": kuerzung}
    status, ok, _, meldung, mehr = bew.verarbeiten({"ergebnis": _antwort()}, rueckweg, lies_karten=lambda i: [],
                                                   aufruf=lambda e, a: {}, pruefe=lambda e, a, q: (True, ""))
    assert status == "geschrieben" and "Gekürzt bewertet" in meldung and mehr["kuerzung"]["karten_gekuerzt"] == 80


def test_gekuerzte_bewertung_schaltet_befoerdern_nicht_frei(monkeypatch) -> None:
    monkeypatch.delenv(bf.SCHWELLE_EINSTELLUNG, raising=False)
    bubble = _bubble(100)
    bubble["metadata"]["bewertung_lauf"]["gekuerzt"] = True
    geprueft = bf.pruefen(bubble, KARTEN, KANTEN)
    assert geprueft["bestaetigbar"] is False and geprueft["pruefung"]["schwelle"]["grund"] == "gekuerzt"
    assert "ist aber gekürzt" in geprueft["satz"] and "ungekürzten Bewertung" in geprueft["satz"]
    darunter = _bubble(70)
    darunter["metadata"]["bewertung_lauf"]["gekuerzt"] = True
    assert "Sie ist außerdem gekürzt" in bf.pruefen(darunter, KARTEN, KANTEN)["satz"]
    # Nur der Betreiber kann das anders entscheiden (Schalter in den Kartenregeln, Vorgabe aus).
    erlaubt = bf.regeln().schwelle_pruefen(bubble, KARTEN, KANTEN, 95, gekuerzt_erlaubt=True)
    assert erlaubt["bestanden"] is True


def test_todo_nachricht_bleibt_unter_64_kb() -> None:
    lesen = _grosse_bubble(200, 3000)
    todo = {"nr": 1, "titel": "Bühne füllen", "fehlt": "Zu dünn.", "punkte": 4, "art": "karte_ergaenzen",
            "karten": [f"a{n:07x}" for n in range(1, 21)]}
    bubble = {**BUBBLE, "metadata": {"bewertung_lauf": {"score": 60, "kartenstand": "x", "todos": [todo]}}}
    nachricht, rueckweg = bew.todo_vorbereiten("idea.todo_erledigen", bubble, 1, lesen)
    assert bew.groesse(nachricht) <= bew.NACHRICHT_BYTES
    assert set(rueckweg["kuerzel"].values()) >= set(todo["karten"])          # betroffene Karten bleiben
    assert set(rueckweg["stand"]) == set(rueckweg["kuerzel"].values())


def test_413_des_agenten_wird_als_zu_gross_gemeldet() -> None:
    from core import auftrag_stand
    zeile = {"status": "fehler", "pruefung": {"verified": None, "reason": "fehler"},
             "ergebnis": {"error": "HTTP 413: Message too large (max 64KB)"}}
    assert auftrag_stand._stand_einer_zeile(zeile) == {"stand": "fehler", "ok": False,
                                                       "meldung": auftrag_stand.ZU_GROSS_SATZ}


def test_fertiges_ergebnis_mit_zu_gross_im_text_bleibt_fertig() -> None:
    from core import auftrag_stand
    zeile = {"status": "fertig", "pruefung": {"verified": True, "reason": "ok"},
             "ergebnis": "Die Bühne ist zu groß für den Platz."}
    assert auftrag_stand._stand_einer_zeile(zeile)["stand"] == "fertig"

def test_fehlt_der_herkunftsbeleg_sagt_die_meldung_das_klar() -> None:
    rueckweg = {"vertrag": "bewerten", "bubble_id": BUBBLE["id"], "kuerzel": KURZ, "kuerzel_titel": TITEL,
                "kartenstand": "sha256:" + "2" * 64, "gekuerzt": False}

    def aufruf(ereignis, argumente):
        raise RuntimeError("Ideen-Werkzeug meldet einen Fehler (herkunft_unbelegt)")

    status, ok, grund, meldung, _ = bew.verarbeiten({"ergebnis": _antwort()}, rueckweg, lies_karten=lambda i: [],
                                                    aufruf=aufruf, pruefe=lambda e, a, q: (True, ""))
    assert status == "fehler" and meldung == bew.HERKUNFT_SATZ and "herkunft_unbelegt" in grund


def test_mit_beleg_nur_wo_der_server_ihn_verlangt() -> None:
    from core import ideen_rueckweg as rw
    hand = {"bubble_id": "b0b1e001", "nr": 1, "durch": "hand"}
    assert rw.mit_beleg("bewertung_todo", dict(hand), "p1-r", schluessel="") == hand
    assert rw.mit_beleg("idea_update", {"id": "a0000001"}, "p1-r", schluessel="") == {"id": "a0000001"}
    agent = rw.mit_beleg("bewertung_todo", {**hand, "durch": "agent"}, "p1-r", schluessel="t\r\n", jetzt=1000)
    assert agent["beleg"].startswith("v1.1300.") and len(agent["beleg"]) == len("v1.1300.") + 64
    with pytest.raises(RuntimeError, match="schluessel_fehlt"):
        rw.mit_beleg("bubble_bewertung", {"bubble_id": "b0b1e001"}, "p1-r", schluessel=" \r\n")


def test_gehirn_liest_nur_den_namen_mit_secret(monkeypatch) -> None:
    # Endung _SECRET: der Claude-Code-Treiber von OpenFang vererbt solche Namen nicht an Agenten.
    from core import config
    from core import ideen_rueckweg as rw
    assert bf.regeln().BELEG_EINSTELLUNG == "VIBEMIND_RUECKWEG_SECRET"
    monkeypatch.setattr(config, "_load_env_file", lambda *a, **k: None)
    monkeypatch.setattr(config, "_SECRETS_DIR", str(Path(__file__).parent / "gibt_es_nicht"))
    for name in ("VIBEMIND_RUECKWEG_SECRET", "VIBEMIND_RUECKWEG_SECRET_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VIBEMIND_RUECKWEG_" + "SCHLUESSEL", "testschluessel-stadtfest")
    with pytest.raises(RuntimeError, match="schluessel_fehlt"):
        rw.mit_beleg("bubble_bewertung", {"bubble_id": "b0b1e001"}, "p1-r")
    monkeypatch.setenv("VIBEMIND_RUECKWEG_SECRET", "testschluessel-stadtfest\r\n")
    assert rw.mit_beleg("bubble_bewertung", {"bubble_id": "b0b1e001"}, "p1-r")["beleg"].startswith("v1.")
