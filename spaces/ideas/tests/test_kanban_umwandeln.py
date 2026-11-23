"""'Kanban' baut Spalten mit Karten aus dem Text, ohne Modell (Lauf 5, Punkt 15). Namen erfunden."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest import mock

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_ideas_mcp_server_kanban", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _namen(board):
    return [(s["name"], [k["title"] for k in s["cards"]]) for s in board["columns"]]


class KanbanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = load_server()

    def test_ohne_spalten_steht_alles_in_offen(self) -> None:
        board = self.server._kanban_from_text("Aufgaben\n- Logo\n- [x] Plakat drucken\n- Bühne mieten", "Aufgaben")
        self.assertEqual(_namen(board), [("Offen", ["Logo", "Bühne mieten"]), ("In Arbeit", []),
                                         ("Erledigt", ["Plakat drucken"])])

    def test_ueberschriften_und_statuszeilen_werden_spalten(self) -> None:
        text = "## Offen\n- Logo\n- Sponsoren\nIn Arbeit:\n- Bühnenplan\nErledigt: Druckerei anfragen"
        self.assertEqual(_namen(self.server._kanban_from_text(text, "")),
                         [("Offen", ["Logo", "Sponsoren"]), ("In Arbeit", ["Bühnenplan"]),
                          ("Erledigt", ["Druckerei anfragen"])])

    def test_spalten_sind_in_der_huelle_sichtbar(self) -> None:
        # Lauf 8: ohne column.color stand der weisse Spaltenkopf auf Hellgrau.
        board = self.server._kanban_from_text("Ideen:\n- Logo\nIn Arbeit:\n- Bühnenplan\nErledigt: Druck", "")
        farben = [s["color"] for s in board["columns"]]
        self.assertTrue(all(isinstance(f, str) and f.startswith("#") and f.lower() != "#e9ecef" for f in farben))
        self.assertEqual(len(set(farben)), 3)
        # Diagramm-Ansicht: todo/doing/done.
        self.assertEqual(board["todo"], [{"title": "Logo"}])
        self.assertEqual(board["doing"], [{"title": "Bühnenplan"}])
        self.assertEqual(board["done"], [{"title": "Druck"}])

    def test_fertig_und_erledigt_sind_eine_spalte(self) -> None:
        # Lauf 10 (34): "Offen, In Arbeit, Fertig, Offen, In Arbeit, Erledigt" in der App.
        text = "Offen:\n- Logo\nTodo:\n- Sponsoren\nDoing:\n- Bühnenplan\nFertig:\n- Druck\nErledigt:\n- Plakat"
        board = self.server._kanban_from_text(text, "")
        self.assertEqual(_namen(board), [("Offen", ["Logo", "Sponsoren"]), ("In Arbeit", ["Bühnenplan"]),
                                         ("Erledigt", ["Druck", "Plakat"])])

    def test_nie_doppelte_spaltennamen(self) -> None:
        texte = ["## Fertig\n- Druck\n## Erledigt\n- Plakat\n## offen\n- Logo\n## OFFEN\n- Bühne",
                 "Done: Druck\nfertig: Plakat\nläuft: Logo\nIn Arbeit: Bühne\n- lose Zeile",
                 "- Logo\n- [x] Druck"]
        for text in texte:
            namen = [s["name"].casefold() for s in self.server._kanban_from_text(text, "")["columns"]]
            self.assertEqual(len(namen), len(set(namen)), text)

    def test_zweites_umwandeln_ergibt_dasselbe_board(self) -> None:
        # Eine Karte, die schon Kanban war: der Text bleibt, das Board entsteht neu (keine alten Spalten dazu).
        text = "Offen: Logo\nIn Arbeit: Bühnenplan\nFertig: Druck"
        erstes = self.server._kanban_from_text(text, "")
        self.assertEqual(self.server._kanban_from_text(text, ""), erstes)
        self.assertEqual([s["name"] for s in erstes["columns"]], ["Offen", "In Arbeit", "Erledigt"])

    def test_gespeichertes_doppeltes_board_zeigt_jede_spalte_einmal(self) -> None:
        board = {"columns": [{"name": "Offen", "cards": [{"title": "Logo"}]}, {"name": "In Arbeit", "cards": []},
                             {"name": "Fertig", "cards": [{"title": "Druck"}]}, {"name": "Offen", "cards": []},
                             {"name": "In Arbeit", "cards": []}, {"name": "Erledigt", "cards": []}]}
        sections, count = self.server._structured_sections("kanban", board, 200)
        self.assertEqual(sections, [("Offen", ["Logo"]), ("In Arbeit", []), ("Erledigt", ["Druck"])])
        self.assertEqual(count, "2 Karten in 3 Spalten")

    def test_zurueckgeschriebenes_board_zaehlt_karten_nicht_doppelt(self) -> None:
        # Lauf 11: "2 Karten in 6 Spalten", zwei "Offen"-Spalten mit demselben Text.
        offen = [{"title": "Logo"}, {"title": "Bühne mieten"}]
        board = {"columns": [{"name": "Offen", "cards": offen}, {"name": "In Arbeit", "cards": []},
                             {"name": "Erledigt", "cards": []}, {"name": "Offen", "cards": offen},
                             {"name": "In Arbeit", "cards": []}, {"name": "Erledigt", "cards": []}]}
        sections, count = self.server._structured_sections("kanban", board, 200)
        self.assertEqual(sections, [("Offen", ["Logo", "Bühne mieten"]), ("In Arbeit", []), ("Erledigt", [])])
        self.assertEqual(count, "2 Karten in 3 Spalten")

    def test_zeilen_ohne_statuszeile_gehen_nach_offen(self) -> None:
        # Lauf 8: die erste Zeile landete in "In Arbeit", eine Spalte "Offen" gab es nicht.
        board = self.server._kanban_from_text("Logo entwerfen\nIn Arbeit: Bühnenplan\n- [x] Druckerei", "")
        self.assertEqual(_namen(board), [("Offen", ["Logo entwerfen"]), ("In Arbeit", ["Bühnenplan", "Druckerei"])])

    def test_beliebige_ueberschrift_ist_keine_spalte(self) -> None:
        board = self.server._kanban_from_text("Aufgaben fürs Stadtfest:\n- Logo\n- Sponsoren", "")
        self.assertEqual(_namen(board), [("Offen", ["Logo", "Sponsoren"]), ("In Arbeit", []), ("Erledigt", [])])

    def test_offen_steht_immer_vorn(self) -> None:
        board = self.server._kanban_from_text("Erledigt: Druck\nIn Arbeit: Bühne", "")
        self.assertEqual(_namen(board), [("Offen", []), ("Erledigt", ["Druck"]), ("In Arbeit", ["Bühne"])])

    def test_name_wert_zeile_ist_eine_karte_keine_spalte(self) -> None:
        board = self.server._kanban_from_text("Preis: 900 Euro", "")
        self.assertEqual(_namen(board)[0], ("Offen", ["Preis: 900 Euro"]))

    def test_nur_titel_gibt_kein_board(self) -> None:
        self.assertIsNone(self.server._kanban_from_text("Aufgaben", "Aufgaben"))

    def test_kartenansicht_liest_das_gebaute_board(self) -> None:
        board = self.server._kanban_from_text("- Logo\n- [x] Plakat", "")
        sections, count = self.server._structured_sections("kanban", board, 200)
        self.assertEqual(count, "2 Karten in 3 Spalten")

    def _format(self, node):
        aufrufe = []

        def antwort(method, path, **kwargs):
            aufrufe.append((method, kwargs))
            return [dict(node)] if method == "GET" else [{**node, **kwargs["body"]}]
        with mock.patch.object(self.server, "_request", side_effect=antwort):
            self.server.call_tool("idea_format", {"id": "a0000001", "format": "kanban"})
        return [k["body"] for m, k in aufrufe if m == "PATCH"]

    def test_format_kanban_schreibt_spalten(self) -> None:
        node = {"id": "a0000001", "title": "Aufgaben", "content": "- Logo\n- Bühne mieten", "content_json": None}
        (body,) = self._format(node)
        self.assertEqual(body["content_json"]["type"], "kanban")
        self.assertEqual(_namen(body["content_json"])[0], ("Offen", ["Logo", "Bühne mieten"]))
        self.assertEqual(body["content_json"]["text"], "- Logo\n- Bühne mieten")


if __name__ == "__main__":
    unittest.main()
