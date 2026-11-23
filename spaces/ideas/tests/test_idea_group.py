"""idea_group: Gruppen als Spalten mit Ueberschrift-Karte, eine Aenderung (Attrappe statt Datenbank)."""

from __future__ import annotations

import unittest
from unittest import mock

from test_feste_werkzeuge import Recorder, load_server


NODES = [
    {"id": "a", "title": "Bühne", "x": 100.0, "y": 100.0, "metadata": {}},
    {"id": "b", "title": "Sponsoren", "x": 340.0, "y": 100.0, "metadata": {}},
    {"id": "c", "title": "Licht", "x": 580.0, "y": 100.0, "metadata": None},
    {"id": "d", "title": "Budget", "x": 100.0, "y": 240.0, "metadata": {}},
    {"id": "leer1", "title": "Leer", "x": 2000.0, "y": 2000.0, "metadata": {}},
]
GRUPPEN = [{"name": "Technik", "ids": ["a", "c"]}, {"name": "Geld", "ids": ["b", "d"]}]


def _call(arguments, nodes=None):
    server = load_server()
    request = Recorder({("GET", "canvas_nodes"): [dict(n) for n in (nodes or NODES)]})
    with mock.patch.object(server, "_request", side_effect=request):
        result = server.call_tool("idea_group", arguments)
    return server, result, request


class IdeaGroupTests(unittest.TestCase):
    def test_columns_with_heading_cards_and_empty_cards_stay(self) -> None:
        _, result, request = _call({"bubble_id": "bub1", "gruppen": GRUPPEN})

        lage = {p["id"]: (p["x"], p["y"]) for p in result["positionen"]}
        k1, k2 = (g["kopf_id"] for g in result["gruppen"])
        self.assertEqual(lage[k1], (100, 100))
        self.assertEqual(lage[k2], (340, 100))
        self.assertEqual(lage["a"], (100, 240))
        self.assertEqual(lage["c"], (100, 380))
        self.assertEqual(lage["b"], (340, 240))
        self.assertEqual(lage["d"], (340, 380))
        self.assertNotIn("leer1", lage)
        neu = request.of("POST", "canvas_nodes")
        self.assertEqual([n["body"]["title"] for n in neu], ["Technik", "Geld"])
        self.assertTrue(all(n["body"]["metadata"]["gruppen_kopf"] is True for n in neu))
        self.assertTrue(all(n["body"]["linked_idea_id"] == "bub1" for n in neu))
        self.assertNotIn("eq.leer1", [c["params"]["id"] for c in request.of("PATCH", "canvas_nodes")])
        self.assertEqual(result["kanten"], [])
        self.assertEqual(request.of("POST", "canvas_edges"), [])

    def test_connect_heading_to_its_cards(self) -> None:
        _, result, request = _call({"bubble_id": "bub1", "gruppen": GRUPPEN, "verbinden": True})
        k1, k2 = (g["kopf_id"] for g in result["gruppen"])
        self.assertEqual(result["kanten"], [{"from_id": k1, "to_id": "a"}, {"from_id": k1, "to_id": "c"},
                                            {"from_id": k2, "to_id": "b"}, {"from_id": k2, "to_id": "d"}])
        self.assertEqual({e["body"]["edge_type"] for e in request.of("POST", "canvas_edges")}, {"related"})

    def test_earlier_heading_cards_are_reused_and_surplus_removed(self) -> None:
        alt = [{"id": "h1", "title": "Alt 1", "x": 100.0, "y": 0.0, "metadata": {"gruppen_kopf": True}},
               {"id": "h2", "title": "Alt 2", "x": 340.0, "y": 0.0, "metadata": {"gruppen_kopf": True}}]
        _, result, request = _call({"bubble_id": "bub1", "gruppen": [{"name": "Alles", "ids": ["a", "b"]}]},
                                   nodes=NODES + alt)
        self.assertEqual(result["gruppen"][0]["kopf_id"], "h1")
        self.assertEqual(result["entfernt"], ["h2"])
        self.assertEqual(request.of("POST", "canvas_nodes"), [])
        self.assertEqual([c["params"] for c in request.of("DELETE", "canvas_nodes")], [{"id": "eq.h2"}])
        geloescht = [c["params"] for c in request.of("DELETE", "canvas_edges")]
        self.assertIn({"from_node_id": "eq.h1"}, geloescht)
        self.assertIn({"to_node_id": "eq.h2"}, geloescht)
        titel = [c["body"] for c in request.of("PATCH", "canvas_nodes") if c["params"] == {"id": "eq.h1"}]
        self.assertEqual(titel[0]["title"], "Alles")

    def test_block_starts_below_a_staying_card_in_the_way(self) -> None:
        nodes = [*NODES, {"id": "steht", "title": "Leer", "x": 340.0, "y": 300.0, "metadata": {}}]
        _, result, _ = _call({"bubble_id": "bub1", "gruppen": GRUPPEN}, nodes=nodes)
        lage = {p["id"]: (p["x"], p["y"]) for p in result["positionen"]}
        self.assertEqual(lage[result["gruppen"][0]["kopf_id"]], (100, 440))

    def test_large_group_wraps_into_a_second_column(self) -> None:
        nodes = [{"id": f"n{i}", "title": f"K{i}", "x": 0.0, "y": float(i), "metadata": {}} for i in range(12)]
        _, result, _ = _call({"bubble_id": "bub1", "gruppen": [{"name": "Viel", "ids": [f"n{i}" for i in range(12)]}]},
                             nodes=nodes)
        lage = {p["id"]: (p["x"], p["y"]) for p in result["positionen"]}
        self.assertEqual(lage["n9"], (0, 1400))
        self.assertEqual(lage["n10"], (240, 140))

    def test_cards_from_elsewhere_and_too_many_cards_write_nothing(self) -> None:
        server = load_server()
        request = Recorder({("GET", "canvas_nodes"): [dict(n) for n in NODES]})
        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(server.ToolError, "^idea_not_in_bubble: fremd$"):
                server.call_tool("idea_group", {"bubble_id": "bub1", "gruppen": [{"name": "X", "ids": ["fremd"]}]})
        many = [{"id": f"n{i}", "title": "t", "x": 0, "y": 0, "metadata": {}} for i in range(server.ARRANGE_NODE_CAP + 1)]
        request2 = Recorder({("GET", "canvas_nodes"): many})
        with mock.patch.object(server, "_request", side_effect=request2):
            with self.assertRaisesRegex(server.ToolError, "^too_many_ideas: 101 ideas in this bubble, limit 100$"):
                server.call_tool("idea_group", {"bubble_id": "bub1", "gruppen": [{"name": "X", "ids": ["n1"]}]})
        for r in (request, request2):
            self.assertEqual([c for c in r.calls if c[0] != "GET"], [])

    def test_invalid_arguments_issue_no_request(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_request") as request:
            for arguments in (
                {"bubble_id": "bub1"},
                {"bubble_id": "bub1", "gruppen": []},
                {"bubble_id": "bub1", "gruppen": [{"name": "A", "ids": ["a"]}] * 13},
                {"bubble_id": "bub1", "gruppen": [{"name": "", "ids": ["a"]}]},
                {"bubble_id": "bub1", "gruppen": [{"name": "A\nB", "ids": ["a"]}]},
                {"bubble_id": "bub1", "gruppen": [{"name": "A" * 61, "ids": ["a"]}]},
                {"bubble_id": "bub1", "gruppen": [{"name": "A", "ids": []}]},
                {"bubble_id": "bub1", "gruppen": [{"name": "A", "ids": ["a,b"]}]},
                {"bubble_id": "bub1", "gruppen": [{"name": "A", "ids": ["a"]}, {"name": "B", "ids": ["a"]}]},
                {"bubble_id": "bub1", "gruppen": [{"name": "A", "ids": ["a"], "farbe": "rot"}]},
                {"bubble_id": "bub1", "gruppen": [{"name": "A", "ids": ["a"]}], "verbinden": "ja"},
                {"bubble_id": "bub1", "gruppen": [{"name": "A", "ids": ["a"]}], "x": 1},
            ):
                with self.assertRaisesRegex(server.ToolError, "^invalid_arguments"):
                    server.call_tool("idea_group", arguments)
        request.assert_not_called()

    def test_writes_carry_the_change_id(self) -> None:
        server = load_server()
        self.assertIn("idea_group", server.CHANGE_WRITE_TOOLS)
        schema = next(t for t in server.TOOLS if t["name"] == "idea_group")["inputSchema"]
        self.assertIn("aenderung_id", schema["properties"])


if __name__ == "__main__":
    unittest.main()
