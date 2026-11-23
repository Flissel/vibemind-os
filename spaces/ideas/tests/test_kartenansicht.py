"""Kartenansicht: was eine Karte zugeklappt und aufgeklappt zeigt.

Die Beispiele sind erfunden; ihre Formen entsprechen dem Bestand (spaces/ideas/KARTEN.md).
Regel: nichts Technisches in dem, was ein Mensch liest.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import unittest
from unittest import mock


SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_ideas_mcp_server_karte", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ALTES_SCHEMA = {"type": "object", "required": ["type", "pros", "cons"],
                "properties": {"type": {"const": "pros_cons_table"}, "pros": {"type": "array"}}}

PRO_CONTRA = {
    "id": "a1b2c3d4", "title": "Festplatz_App", "content": "Festplatz_App", "node_type": "note",
    "metadata": {"width": 200.0, "height": 120.0}, "format_schema": ALTES_SCHEMA,
    "content_json": {
        "type": "pros_cons_table",
        "pros": [{"point": "Lageplan immer dabei", "weight": 3, "evidence": "schnell"},
                 {"point": "Programm ohne Papier", "weight": 2}],
        "cons": [{"point": "Ältere Gäste brauchen trotzdem Aushänge", "weight": 1}],
    },
}
VORLAGE = {
    "id": "b1b2c3d4", "title": "Buehnen_Plan", "content": "Buehnen_Plan", "node_type": "note",
    "metadata": {}, "format_schema": {"type": "object"},
    "content_json": {"type": "technical_specs", "title": "Technische Spezifikation", "component": "Betroffene Komponente",
                     "specifications": [{"category": "Performance", "priority": "must_have",
                                         "requirement": "Konkrete Anforderung"}]},
}
SPECS = {
    "id": "c1b2c3d4", "title": "Sponsoren_Paket_Spec", "content": "Sponsoren_Paket_Spec", "node_type": "note",
    "metadata": {},
    "content_json": {"type": "technical_specs", "specifications": [
        {"category": "Security", "priority": "must_have", "requirement": "Logo nur nach schriftlicher Freigabe",
         "acceptance_criteria": "Freigabe liegt vor dem Druck vor"},
        {"category": "Performance", "requirement": "Banner bis Freitag hängen"}]},
}
NOTIZ = {
    "id": "d1b2c3d4", "title": "Strom_Anschluss", "node_type": "note", "metadata": {}, "content_json": None,
    "content": "Strom fuer die Buehne klaeren.\n\nAnschluesse:\n- /festplatz/nord/kasten-1 (Buehne)\n"
               "- /festplatz/nord/kasten-2 (Getraenke)\n\nAbnahme: Elektriker am Vortag.",
}
NUR_TITEL = {"id": "e1b2c3d4", "title": "Helferliste", "content": "Helferliste", "node_type": "note",
             "metadata": {}, "content_json": None}
RECHERCHE = {
    "id": "00000000-0000-4000-8000-000000000101", "title": "Research: Laternenlauf 2026-01-15 10:00:00",
    "node_type": "research", "content_json": None, "format_schema": None,
    "metadata": {"width": 320.0, "height": 220.0, "artifact_ref": "artifact_v1_01ABC", "citation_count": 7},
    "content": "# Marktüberblick: Laternenlauf\n\n**Auftraggeber:** Laternenlauf (Verein)\n"
               "**Sprache:** Deutsch\n\n---\n\n## 0. Executive Summary\n\nDer Laternenlauf trifft auf "
               "**zwei** Feste am selben Abend.\n\nDas ergibt einen freien Termin im November.\n\n"
               "## 1. Andere Feste\n\n| Fest | Ort |\n|---|---|\n| Beispielfest | Nord |\n\n## 2. Quellen\n\n- Quelle A\n",
}
SWOT = {
    "id": "f1b2c3d4", "title": "Sponsoren_Paket", "content": "Sponsoren_Paket", "node_type": "note", "metadata": {},
    "content_json": {"type": "swot", "subject": "Was analysiert wird",
                     "strengths": [{"point": "Starke Marktposition", "impact": "high"}],
                     "weaknesses": [{"point": "Mangelnde Ressourcen"}], "opportunities": [], "threats": []},
}
NEU_FORMATIERT = {  # idea_format (fester Aufruf) setzt nur das Format, der Text bleibt
    "id": "a2b2c3d4", "title": "Preise", "content": "A kostet 1\nB kostet 2", "node_type": "note", "metadata": {},
    "format_schema": {"type": "table"}, "content_json": {"type": "table", "title": "Preise", "text": "A kostet 1\nB kostet 2"},
}
VORSCHLAG = {"id": "a3b2c3d4", "title": "Kinderecke", "content": "Eine Ecke mit Malen und Kasperle.",
             "node_type": "note", "content_json": None,
             "metadata": {"ai_generated": True, "source_title": "Familien_Programm"}}

ALLE = [PRO_CONTRA, VORLAGE, SPECS, NOTIZ, NUR_TITEL, RECHERCHE, SWOT, NEU_FORMATIERT, VORSCHLAG]
FELDNAMEN = re.compile(r"[{}\[\]]|\b[a-z]+_[a-z_]+\b|must_have|pros_cons|content_json|artifact_ref")


def lesbare_texte(ansicht):
    """Alle Strings, die ein Mensch sieht (die Kennung zum Weiterklicken ausgenommen)."""
    if isinstance(ansicht, dict):
        for key, value in ansicht.items():
            if key != "id":
                yield from lesbare_texte(value)
    elif isinstance(ansicht, list):
        for value in ansicht:
            yield from lesbare_texte(value)
    elif isinstance(ansicht, str):
        yield ansicht


class FormatTests(unittest.TestCase):
    def test_format_steht_im_strukturierten_inhalt_nicht_im_schema_typ(self) -> None:
        server = load_server()
        self.assertEqual(server._card_format(PRO_CONTRA), "pros_cons")
        self.assertEqual(server._card_format(VORLAGE), "technical_specs")
        self.assertEqual(server._card_format({"format_schema": ALTES_SCHEMA}), "pros_cons")
        self.assertEqual(server._card_format({"format_schema": {"type": "kanban"}}), "kanban")
        self.assertEqual(server._card_format({"format_schema": {"type": "object"}}), "note")
        self.assertEqual(server._card_format(RECHERCHE), "research")
        self.assertEqual(server._card_format(NOTIZ), "note")


class KarteZuTests(unittest.TestCase):
    def test_pro_und_contra_zeigt_punkte_mit_seite_und_anzahl(self) -> None:
        karte = load_server()._card_short(PRO_CONTRA)
        self.assertEqual(karte, {
            "titel": "Festplatz App", "art": "Pro und Contra",
            "zeilen": ["Pro: Lageplan immer dabei", "Pro: Programm ohne Papier",
                       "Contra: Ältere Gäste brauchen trotzdem Aushänge"],
            "umfang": "2 Pro · 1 Contra", "quelle": None, "leer": False,
        })

    def test_vorlagentext_gilt_als_leer(self) -> None:
        karte = load_server()._card_short(VORLAGE)
        self.assertEqual((karte["art"], karte["zeilen"], karte["umfang"], karte["leer"]),
                         ("Anforderungen", [], None, True))

    def test_anforderungen_zeigen_die_anforderung(self) -> None:
        karte = load_server()._card_short(SPECS)
        self.assertEqual(karte["zeilen"], ["Logo nur nach schriftlicher Freigabe", "Banner bis Freitag hängen"])
        self.assertEqual(karte["umfang"], "2 Anforderungen")

    def test_text_der_nur_den_titel_wiederholt_zeigt_nichts_doppelt(self) -> None:
        karte = load_server()._card_short(NUR_TITEL)
        self.assertEqual((karte["titel"], karte["zeilen"], karte["leer"]), ("Helferliste", [], True))

    def test_notiz_zeigt_die_ersten_zeilen_ohne_aufzaehlungszeichen(self) -> None:
        karte = load_server()._card_short(NOTIZ)
        self.assertEqual(karte["art"], "Notiz")
        self.assertEqual(karte["zeilen"], ["Strom fuer die Buehne klaeren.", "Anschluesse:",
                                           "/festplatz/nord/kasten-1 (Buehne)"])

    def test_recherche_zeigt_die_zusammenfassung_datum_und_quellen(self) -> None:
        karte = load_server()._card_short(RECHERCHE)
        self.assertEqual(karte["titel"], "Recherche: Laternenlauf")
        self.assertEqual(karte["art"], "Recherche")
        self.assertEqual(karte["zeilen"], ["Der Laternenlauf trifft auf zwei Feste am selben Abend.",
                                           "Das ergibt einen freien Termin im November."])
        self.assertEqual(karte["quelle"], "Recherche vom 15.01.2026 · 7 Quellen")
        self.assertEqual(karte["umfang"], "4 Abschnitte")

    def test_swot_ohne_vorlagentext(self) -> None:
        karte = load_server()._card_short(SWOT)
        self.assertEqual(karte["zeilen"], ["Stärken: Starke Marktposition", "Schwächen: Mangelnde Ressourcen"])
        self.assertEqual(karte["umfang"], "1 Stärke · 1 Schwäche")

    def test_neu_formatierte_karte_zeigt_ihren_text_unter_dem_neuen_format(self) -> None:
        karte = load_server()._card_short(NEU_FORMATIERT)
        self.assertEqual((karte["art"], karte["zeilen"]), ("Tabelle", ["A kostet 1", "B kostet 2"]))

    def test_vorschlag_nennt_seine_herkunft(self) -> None:
        self.assertEqual(load_server()._card_short(VORSCHLAG)["quelle"], "Vorschlag aus „Familien Programm“")

    def test_lange_zeilen_werden_gekuerzt(self) -> None:
        server = load_server()
        karte = server._card_short({**NOTIZ, "content": "Wort " * 100})
        self.assertLessEqual(len(karte["zeilen"][0]), server.CARD_LINE_CHARS)
        self.assertTrue(karte["zeilen"][0].endswith("…"))

    def test_weitere_formate(self) -> None:
        server = load_server()
        faelle = [
            ({"type": "action_list", "items": [{"task": "Angebot schicken", "status": "completed"}, {"task": "Anrufen"}]},
             ["Angebot schicken (erledigt)", "Anrufen"], "1 von 2 erledigt", "Aufgabenliste"),
            ({"type": "kanban", "columns": [{"name": "Offen", "cards": [{"title": "Logo"}]}, {"name": "Fertig", "cards": []}]},
             ["Offen: Logo"], "1 Karte in 2 Spalten", "Kanban"),
            ({"type": "table", "headers": ["Posten", "Preis"], "rows": [["Stand", "900"], ["Druck", "120"]]},
             ["Stand · 900", "Druck · 120"], "2 Zeilen", "Tabelle"),
            ({"type": "hierarchy", "levels": [{"items": [{"name": "Fragen", "children": [{"name": "Wer?"}]}]}]},
             ["Wer?"], "1 Punkt", "Gliederung"),
            ({"type": "user_story", "stories": [{"role": "Als Kunde", "want": "möchte ich teilen", "benefit": "x"}]},
             ["Als Kunde möchte ich teilen"], "1 Geschichte", "Nutzergeschichten"),
            ({"type": "flowchart", "nodes": [{"id": "s", "type": "start", "label": "Start"},
                                             {"id": "p", "type": "process", "label": "Antrag prüfen"}]},
             ["Antrag prüfen"], "1 Schritt", "Ablauf"),
            ({"type": "mindmap", "center": {"label": "Messe"}, "branches": [{"label": "Stand", "children": [{"label": "Licht"}]}]},
             ["Messe", "Licht"], "1 Zweig", "Mindmap"),
        ]
        for inhalt, zeilen, umfang, art in faelle:
            with self.subTest(inhalt["type"]):
                karte = server._card_short({"id": "a1b2c3d4", "title": "X", "content": "X", "content_json": inhalt})
                self.assertEqual((karte["zeilen"], karte["umfang"], karte["art"]), (zeilen, umfang, art))

    def test_nichts_technisches_auf_keiner_karte(self) -> None:
        server = load_server()
        for zeile in ALLE:
            for text in lesbare_texte(server._card_short(zeile)):
                if "/festplatz/" in text:  # Pfade, die ein Mensch selbst notiert hat, bleiben
                    continue
                with self.subTest(zeile["title"], text=text):
                    self.assertIsNone(FELDNAMEN.search(text))

    def test_kaputte_formen_brechen_nichts(self) -> None:
        server = load_server()
        for inhalt in ({"type": "pros_cons", "pros": "x"}, {"type": "swot", "strengths": [None, 3]},
                       {"type": "kanban", "columns": [{"cards": "x"}]}, {"type": 5}, "kein dict"):
            karte = server._card_short({"id": "a1b2c3d4", "title": None, "content": None, "content_json": inhalt})
            self.assertEqual(karte["titel"], "Ohne Titel")
            self.assertTrue(karte["leer"])


class IdeaListTests(unittest.TestCase):
    def test_kurze_liste_bringt_die_karte_mit_und_liest_kein_schema(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_request", return_value=[dict(PRO_CONTRA)]) as request:
            (eintrag,) = server.call_tool("idea_list", {"bubble_id": "000000b1"})["karten"]
        self.assertEqual(eintrag["format"], "pros_cons")
        self.assertEqual(eintrag["karte"]["umfang"], "2 Pro · 1 Contra")
        select = request.call_args.kwargs["params"]["select"]
        self.assertIn("content_json", select)
        self.assertNotIn("format_schema", select)

    def _bubble_mit(self, anzahl):
        """Eine Bubble mit ``anzahl`` Karten; die Datenbank haelt sich an limit."""
        karten = [{"id": f"{i:08x}", "title": f"K{i}", "content": "", "x": i, "y": 0,
                   "linked_idea_id": "000000b1"} for i in range(anzahl)]

        def antwort(method, path, **kwargs):
            params = kwargs["params"]
            zeilen = karten[: int(params["limit"])]
            return [{"id": z["id"]} for z in zeilen] if params["select"] == "id" else [dict(z) for z in zeilen]
        return antwort

    def test_volle_liste_nennt_die_wahre_gesamtzahl(self) -> None:
        # 23.11.2026: "Zeig die Notizen" sagte 50, obwohl die Bubble mehr Karten hatte.
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=self._bubble_mit(75)) as request:
            antwort = server.call_tool("idea_list", {"bubble_id": "000000b1"})
        self.assertEqual((antwort["gezeigt"], antwort["gesamt"], antwort["gekuerzt"]), (50, 75, True))
        self.assertEqual(len(antwort["karten"]), 50)
        liste, zaehlung = [c.kwargs["params"] for c in request.call_args_list]
        self.assertEqual(liste["order"], "y.asc,x.asc,id.asc")
        self.assertEqual(zaehlung["select"], "id")
        self.assertEqual(zaehlung["linked_idea_id"], "eq.000000b1")

    def test_genau_voll_ist_nicht_gekuerzt(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=self._bubble_mit(50)):
            antwort = server.call_tool("idea_list", {"bubble_id": "000000b1"})
        self.assertEqual((antwort["gezeigt"], antwort["gesamt"], antwort["gekuerzt"]), (50, 50, False))
        with mock.patch.object(server, "_request", side_effect=self._bubble_mit(75)):
            antwort = server.call_tool("idea_list", {"bubble_id": "000000b1", "limit": 100})
        self.assertEqual((antwort["gezeigt"], antwort["gesamt"], antwort["gekuerzt"]), (75, 75, False))

    def test_misslungene_zaehlung_sagt_gekuerzt_ohne_zahl(self) -> None:
        server = load_server()
        voll = self._bubble_mit(75)

        def antwort(method, path, **kwargs):
            if kwargs["params"]["select"] == "id":
                raise server.ToolError("supabase_unreachable")
            return voll(method, path, **kwargs)
        with mock.patch.object(server, "_request", side_effect=antwort):
            ergebnis = server.call_tool("idea_list", {"bubble_id": "000000b1"})
        self.assertEqual((ergebnis["gezeigt"], ergebnis["gesamt"], ergebnis["gekuerzt"]), (50, None, True))

    def test_volle_liste_bleibt_eine_rohe_liste(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=self._bubble_mit(3)):
            self.assertIsInstance(server.call_tool("idea_list", {"bubble_id": "000000b1", "full": True}), list)


class IdeaReadTests(unittest.TestCase):
    def _antworten(self, knoten, kanten, titel):
        def antwort(method, path, **kwargs):
            params = kwargs.get("params", {})
            if path == "canvas_edges":
                return kanten
            if params.get("id", "").startswith("in."):
                return titel
            return [knoten] if knoten else []
        return antwort

    def test_aufgeklappt_alles_lesbar_mit_verbindungen_nach_titel(self) -> None:
        server = load_server()
        kanten = [{"from_node_id": "a1b2c3d4", "to_node_id": "c1b2c3d4", "edge_type": "related"},
                  {"from_node_id": "d1b2c3d4", "to_node_id": "a1b2c3d4", "edge_type": "expanded_from"},
                  {"from_node_id": "a1b2c3d4", "to_node_id": "e1b2c3d4", "edge_type": "parent_child"},
                  {"from_node_id": "a1b2c3d4", "to_node_id": "geloescht", "edge_type": "related"}]
        titel = [{"id": "c1b2c3d4", "title": "Sponsoren_Paket_Spec"}, {"id": "d1b2c3d4", "title": "Strom_Anschluss"},
                 {"id": "e1b2c3d4", "title": "Helferliste"}]
        with mock.patch.object(server, "_request", side_effect=self._antworten(PRO_CONTRA, kanten, titel)) as request:
            ansicht = server.call_tool("idea_read", {"id": "a1b2c3d4"})
        self.assertEqual(ansicht["abschnitte"], [
            {"ueberschrift": "Pro", "zeilen": ["Lageplan immer dabei", "Programm ohne Papier"]},
            {"ueberschrift": "Contra", "zeilen": ["Ältere Gäste brauchen trotzdem Aushänge"]},
        ])
        self.assertEqual(ansicht["verbindungen"], [
            {"id": "c1b2c3d4", "titel": "Sponsoren Paket Spec", "art": "verwandt"},
            {"id": "d1b2c3d4", "titel": "Strom Anschluss", "art": "Ausgangspunkt"},
            {"id": "e1b2c3d4", "titel": "Helferliste", "art": "Unterpunkt"},
        ])  # eine Kante zu einer Karte, die es nicht mehr gibt, zeigt nichts
        self.assertIsNone(ansicht["text"])
        kanten_abfrage = [c.kwargs["params"] for c in request.call_args_list if c.args[1] == "canvas_edges"][0]
        self.assertEqual(kanten_abfrage["or"], "(from_node_id.eq.a1b2c3d4,to_node_id.eq.a1b2c3d4)")
        for text in lesbare_texte(ansicht):
            self.assertIsNone(FELDNAMEN.search(text), text)

    def test_recherche_aufgeklappt_hat_gliederung_und_den_vollen_text(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=self._antworten(RECHERCHE, [], [])):
            ansicht = server.call_tool("idea_read", {"id": RECHERCHE["id"]})
        self.assertEqual([a["ueberschrift"] for a in ansicht["abschnitte"]],
                         ["Marktüberblick: Laternenlauf", "0. Executive Summary", "1. Andere Feste", "2. Quellen"])
        self.assertEqual(ansicht["abschnitte"][2]["zeilen"], ["Fest · Ort", "Beispielfest · Nord"])
        self.assertEqual(ansicht["text"], RECHERCHE["content"])
        self.assertFalse(ansicht["leer"])

    def test_unbekannte_und_falsche_kennung(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_request", return_value=[]):
            with self.assertRaisesRegex(server.ToolError, "^idea_not_found$"):
                server.call_tool("idea_read", {"id": "a1b2c3d4"})
        with mock.patch.object(server, "_request") as request:
            for falsch in ("a1b2c3d4,x", "", "a b", 7):  # Filter-sicher ist Sache des Servers, DB-Form prueft das Gehirn
                with self.assertRaisesRegex(server.ToolError, "^invalid_arguments"):
                    server.call_tool("idea_read", {"id": falsch})
            with self.assertRaisesRegex(server.ToolError, "^invalid_arguments: unknown argument"):
                server.call_tool("idea_read", {"id": "a1b2c3d4", "full": True})
        request.assert_not_called()

    def test_ist_im_werkzeugverzeichnis_und_schreibt_nicht(self) -> None:
        server = load_server()
        namen = [tool["name"] for tool in server.TOOLS]
        self.assertIn("idea_read", namen)
        self.assertNotIn("idea_read", server.CHANGE_WRITE_TOOLS)


if __name__ == "__main__":
    unittest.main()
