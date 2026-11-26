"""Shared card rules and promotion gate (spaces/_contract/kartenregeln.py). All cards are invented."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location("kartenregeln", Path(__file__).resolve().parents[1] / "kartenregeln.py")
regeln = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(regeln)


def _karte(kennung, titel, text="", **meta):
    return {"id": kennung, "node_type": "note", "title": titel, "content": text, "metadata": meta}


PAPIER = {"id": "wp", "node_type": "dokument", "title": "White Paper", "content": "# Ziel",
          "metadata": {"art": "white_paper", "quellen": ["k1"]}}
GUT = [_karte("k1", "Ziel", "Ein Ticket fuer alle Verkehrsmittel der Region kaufen."),
       _karte("k2", "Nutzer", "Pendler und Gelegenheitsfahrer mit Smartphone im Alltag."), PAPIER]
KANTE = [{"from_node_id": "k1", "to_node_id": "k2"}]


def test_besteht() -> None:
    s = regeln.sinnpruefung(GUT, KANTE, {"score": 7})
    assert s["bestanden"] and s["gruende"] == [] and s["zahlen"]["anteil_im_white_paper"] == 0.5


def test_faellt_mit_allen_gruenden_in_fester_reihenfolge() -> None:
    s = regeln.sinnpruefung(GUT[:2] + [_karte("k3", "API", "")], KANTE, None)
    assert s["gruende"] == ["leere_karten:1", "lose_karten:1", "keine_bewertung", "kein_white_paper"]
    assert s["leer"] == ["k3"] and s["lose"] == ["k3"]
    assert regeln.sinnpruefung([], [], {"score": 1}, white_paper_pflicht=False)["gruende"] == ["keine_karten"]


def test_white_paper_schalter() -> None:
    s = regeln.sinnpruefung(GUT[:2], KANTE, {"score": 1}, white_paper_pflicht=False)
    assert s["bestanden"] and s["hinweise"] == ["kein_white_paper"]


def test_bewertung_null_ist_eine_bewertung() -> None:
    assert regeln.hat_bewertung({"score": 0}) and regeln.hat_bewertung(0)
    assert not regeln.hat_bewertung(None) and not regeln.hat_bewertung({"score": None})


def test_gruppenkopf_zaehlt_nicht() -> None:
    kopf = _karte("g", "Sponsoren", "", gruppen_kopf=True)
    karte = _karte("k1", "Hauptsponsor", "Die Brauerei zahlt das Buehnenzelt und die Getraenke.")
    s = regeln.sinnpruefung([kopf, karte, PAPIER], [{"from_node_id": "g", "to_node_id": "k1"}], {"score": 5})
    assert s["bestanden"] and s["zahlen"]["notizen"] == 1 and s["leer"] == [] and s["lose"] == []


def test_duenn_ist_nur_hinweis() -> None:
    karten = [_karte("a", "Tarif", "Normalpreis 58 Euro im Monat"), _karte("b", "Ziel", "Ein Ticket fuer die ganze Region."), PAPIER]
    s = regeln.sinnpruefung(karten, [{"from_node_id": "a", "to_node_id": "b"}], {"score": 3})
    assert s["bestanden"] and s["duenn"] == ["a", "b"] and s["hinweise"] == ["duenne_karten:2"]


def test_bewertung_der_bubble_f2b() -> None:
    """ideas.score ist ab Werk 0.0: eine 0 ohne Begruendung ist keine Bewertung, mit Begruendung schon."""
    assert regeln.bewertung_der_bubble({"score": 0.0, "metadata": {}}) is None
    assert regeln.bewertung_der_bubble({"score": None}) is None
    assert regeln.bewertung_der_bubble({"score": True}) is None
    assert regeln.bewertung_der_bubble({"score": 0, "metadata": {"bewertungen": [{"id": "x"}]}}) == {
        "score": 0, "begruendungen": 1}
    assert regeln.bewertung_der_bubble({"score": 55}) == {"score": 55, "begruendungen": 0}
    assert not regeln.hat_bewertung(regeln.bewertung_der_bubble({"score": 0.0}))


# ── kartentext: content, sonst summary, sonst content_json (formatierte Karten) ──

def _formatiert(kennung, titel, struktur, text=None, **mehr):
    karte = _karte(kennung, titel, titel if text is None else text)
    karte["content_json"] = struktur
    karte.update(mehr)
    return karte


SPECS = {"type": "technical_specs", "title": "Technische Spezifikation", "component": "Buehnenplan",
         "metadata": {"formatted_by": "SpecsAgent"},
         "specifications": [{"category": "Performance", "priority": "must_have",
                             "requirement": "Der Plan laedt auf dem Handy in unter zwei Sekunden.",
                             "acceptance_criteria": "Messung mit zehn Aufrufen am Festplatz."}]}


def test_kartentext_liest_strukturierten_inhalt() -> None:
    karte = _formatiert("k1", "Buehnenplan", SPECS)
    text = regeln.kartentext(karte)
    assert "unter zwei Sekunden" in text and "Messung mit zehn Aufrufen" in text
    assert "Performance" not in text and "must_have" not in text and "SpecsAgent" not in text
    assert not regeln.ist_leer(karte) and not regeln.ist_duenn(karte)


def test_kartentext_content_geht_vor_und_bleibt_unveraendert() -> None:
    karte = _formatiert("k1", "Buehnenplan", SPECS, text="Zwei Buehnen am Markt, eine am Hafen.")
    assert regeln.kartentext(karte) == "Zwei Buehnen am Markt, eine am Hafen."


def test_kartentext_summary_vor_content_json() -> None:
    karte = _formatiert("k1", "Sponsoren", SPECS, summary="Drei Sponsoren tragen die Bühnenkosten.")
    assert regeln.kartentext(karte) == "Drei Sponsoren tragen die Bühnenkosten."


def test_absagen_und_vorlagen_sind_leer() -> None:
    absage = _formatiert("k1", "Druckerei", {
        "type": "technical_specs", "component": "Druckerei", "specifications": [],
        "extraction_notes": "Keine technischen Anforderungen im bereitgestellten Inhalt gefunden.",
        "recommendation": "Bitte stellen Sie den vollständigen Inhalt bereit."})
    vorlage = _formatiert("k2", "Sponsoren", {
        "type": "technical_specs", "title": "Technische Spezifikation", "component": "Betroffene Komponente",
        "specifications": [{"category": "Security", "requirement": "Konkrete Anforderung für Sicherheit",
                            "acceptance_criteria": "Wie wird es getestet?"}],
        "implementation_notes": "Hinweise zur Implementierung"})
    swot = _formatiert("k3", "Helferliste", {"type": "swot", "subject": "Was analysiert wird",
                                             "summary": {"key_actions": ["Aktion 1", "Aktion 2"]}})
    titel_nur = _karte("k4", "Getraenkestand Nord", "Getraenkestand_Nord")
    for karte in (absage, vorlage, swot, titel_nur):
        assert regeln.kartentext(karte) == "", karte["id"]
        assert regeln.ist_leer(karte), karte["id"]


def test_sinnpruefung_mit_formatierten_karten() -> None:
    gut = _formatiert("k1", "Buehnenplan", SPECS)
    leer = _formatiert("k2", "Druckerei", {"type": "note", "text": "", "title": "Notiz-Titel"})
    s = regeln.sinnpruefung([gut, leer, PAPIER], [{"from_node_id": "k1", "to_node_id": "k2"}], {"score": 4})
    assert s["leer"] == ["k2"] and s["gruende"] == ["leere_karten:1"]


def test_content_json_als_zeichenkette() -> None:
    import json
    karte = _formatiert("k1", "Buehnenplan", json.dumps(SPECS))
    assert "unter zwei Sekunden" in regeln.kartentext(karte)


def test_ergaenzte_formatierte_karte_zaehlt_den_text_einmal():
    # "Ergänze bei X" auf einer formatierten Karte: der Server setzt content und content_json.text
    # gleich. kartentext nimmt den Inhalt, die Karte ist nicht leer und nichts steht doppelt.
    kurz = "Drei Bühnen, Aufbau am Freitag ab acht Uhr."
    karte = _formatiert("a0000009", "Buehnenplan", {**SPECS, "text": kurz}, text=kurz)
    text = regeln.kartentext(karte)
    assert text == kurz
    assert not regeln.ist_leer(karte) and not regeln.ist_duenn(karte)


# ── Lauf von MiroFish: Raster und Zahl der Abo-Aufrufe (26.11.2026) ─────────

def _mf_sicht(punkte=(3, 3, 3, 3, 3)):
    return {"punkte": sum(punkte), "urteil": "Steht so da.",
            "kriterien": [{"name": f"Kriterium {n}", "punkte": p, "begruendung": ""}
                          for n, p in enumerate(punkte, start=1)]}


def _mf_lauf(**anders):
    lauf = {"sichten": {name: _mf_sicht() for name in regeln.SICHTEN}, "todos": [], "gekuerzt": False,
            "quelle": "mirofish", "aufrufe": 5}
    lauf.update(anders)
    return lauf


def test_lauf_mit_raster_und_aufrufen_ist_gueltig() -> None:
    assert regeln.lauf_fehler(_mf_lauf()) == ""
    ohne = _mf_lauf()
    del ohne["aufrufe"]
    for sicht in ohne["sichten"].values():
        del sicht["kriterien"]
    assert regeln.lauf_fehler(ohne) == ""      # ein Lauf von brain-ideas hat beides nicht


def test_lauf_raster_muss_aufgehen() -> None:
    lauf = _mf_lauf()
    lauf["sichten"]["coding"]["punkte"] = 16             # Summe der Kriterien ist 15
    assert regeln.lauf_fehler(lauf) == "sichten.coding.kriterien"
    lauf = _mf_lauf()
    lauf["sichten"]["ideas"]["kriterien"].pop()
    assert regeln.lauf_fehler(lauf) == "sichten.ideas.kriterien"
    lauf = _mf_lauf()
    lauf["sichten"]["research"]["kriterien"][1]["name"] = "Kriterium 1"
    assert regeln.lauf_fehler(lauf) == "sichten.research.kriterien"
    lauf = _mf_lauf()
    lauf["sichten"]["rowboat"]["kriterien"][0]["punkte"] = 6
    assert regeln.lauf_fehler(lauf) == "sichten.rowboat.kriterien"


def test_lauf_aufrufe_in_grenzen() -> None:
    assert regeln.lauf_fehler(_mf_lauf(aufrufe=regeln.AUFRUFE_MAX + 1)) == "aufrufe"
    assert regeln.lauf_fehler(_mf_lauf(aufrufe=True)) == "aufrufe"
    assert regeln.lauf_fehler(_mf_lauf(aufrufe=0)) == ""


# ── To-dos bringen hoechstens, was der Sicht fehlt (erster echter Lauf, 26.11.2026) ──

def test_todo_punkte_werden_auf_das_fehlende_umgerechnet() -> None:
    # Coding 9 von 25: es fehlen 16, die To-dos versprachen 67.
    assert regeln.todo_punkte_umrechnen([20, 15, 12, 12, 8], 16) == [5, 3, 3, 3, 2]
    assert sum(regeln.todo_punkte_umrechnen([30, 20, 15, 10, 7], 17)) == 17
    assert regeln.todo_punkte_umrechnen([4, 3], 10) == [4, 3]          # passt schon: unveraendert
    assert regeln.todo_punkte_umrechnen([4, 3], 0) == [0, 0]
    assert regeln.todo_punkte_umrechnen([10, 1, 1], 2) == [2, 0, 0]


def test_todos_begrenzen_laesst_nullen_weg() -> None:
    todos = [{"titel": "A", "punkte": 10}, {"titel": "B", "punkte": 1}, {"titel": "C", "punkte": 1}]
    assert regeln.todos_begrenzen(todos, 23) == [{"titel": "A", "punkte": 2}]
    assert regeln.todos_begrenzen(todos, 25) == []
    assert regeln.todos_begrenzen(todos, 5) == todos


# ── Score in Prozent (Entscheid des Betreibers, Variante A, 26.11.2026) ──

def test_score_ist_prozent_der_125_punkte() -> None:
    assert [regeln.score_prozent(s) for s in (0, 1, 40, 75, 118, 119, 124, 125)] == [0, 1, 32, 60, 94, 95, 99, 100]
    assert regeln.score_prozent(2) == 2                 # 1,6 -> 2
    assert regeln.score_prozent(130) == 100 and regeln.score_prozent(-3) == 0


def test_lauf_score_rechnet_die_summe_in_prozent() -> None:
    lauf = {"sichten": {n: {"punkte": 25} for n in regeln.SICHTEN}}
    assert regeln.lauf_summe(lauf) == 125 and regeln.lauf_score(lauf) == 100
    lauf["sichten"]["coding"]["punkte"] = 19          # 119 von 125: genau an der Schwelle 95
    assert regeln.lauf_score(lauf) == 95 == regeln.BEFOERDERN_SCHWELLE
