"""Tests for the fixed (model-free) canvas tools: format, arrange, auto-link, rate."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest import mock


SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_ideas_mcp_server_fest", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Recorder:
    """Stand-in for _request: answers by (method, path) and records every call."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        answer = self.answers.get((method, path), [])
        return answer(kwargs) if callable(answer) else answer

    def of(self, method, path):
        return [kwargs for m, p, kwargs in self.calls if (m, p) == (method, path)]


class IdeaFormatTests(unittest.TestCase):
    def test_format_keeps_text_and_remembers_the_previous_format(self) -> None:
        server = load_server()
        node = {"id": "n1", "title": "Preise", "content": "A;1\nB;2", "node_type": "note",
                "content_json": None, "previous_content_json": None}
        request = Recorder({
            ("GET", "canvas_nodes"): [node],
            ("PATCH", "canvas_nodes"): lambda kwargs: [{**node, **kwargs["body"]}],
        })
        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("idea_format", {"id": "n1", "format": "table"})

        (patch,) = request.of("PATCH", "canvas_nodes")
        self.assertEqual(patch["params"], {"id": "eq.n1"})
        self.assertEqual(patch["body"]["format_schema"], {"type": "table"})
        self.assertEqual(patch["body"]["content_json"], {"type": "table", "title": "Preise", "text": "A;1\nB;2",
                                                               "columns": ["A", "1"], "rows": [["B", "2"]]})
        self.assertEqual(patch["body"]["previous_content_json"], {"type": "note", "text": "A;1\nB;2"})
        self.assertIn("last_formatted", patch["body"])
        self.assertNotIn("content", patch["body"])
        self.assertEqual(result["format_schema"], {"type": "table"})

    def test_revert_restores_the_previous_format(self) -> None:
        server = load_server()
        node = {"id": "n1", "title": "Preise", "content": "x", "node_type": "note",
                "content_json": {"type": "table", "text": "x"},
                "previous_content_json": {"type": "pros_cons", "text": "x"}}
        request = Recorder({
            ("GET", "canvas_nodes"): [node],
            ("PATCH", "canvas_nodes"): lambda kwargs: [{**node, **kwargs["body"]}],
        })
        with mock.patch.object(server, "_request", side_effect=request):
            server.call_tool("idea_format", {"id": "n1", "format": "revert"})

        (patch,) = request.of("PATCH", "canvas_nodes")
        self.assertEqual(patch["body"], {
            "format_schema": {"type": "pros_cons"},
            "content_json": {"type": "pros_cons", "text": "x"},
        })

    def test_revert_without_history_and_missing_idea_fail_without_writing(self) -> None:
        server = load_server()
        for rows, error in (
            ([{"id": "n1", "previous_content_json": None}], "idea_format_nothing_to_revert"),
            ([], "idea_not_found"),
        ):
            request = Recorder({("GET", "canvas_nodes"): rows})
            with mock.patch.object(server, "_request", side_effect=request):
                with self.assertRaisesRegex(server.ToolError, f"^{error}$"):
                    server.call_tool("idea_format", {"id": "n1", "format": "revert"})
            self.assertEqual(request.of("PATCH", "canvas_nodes"), [])

    def test_invalid_arguments_issue_no_request(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_request") as request:
            for arguments in (
                {"id": "n1"},
                {"id": "n1", "format": "bunt"},
                {"id": "eq.n1", "format": "table"},
                {"id": "n1,n2", "format": "table"},
                {"id": "n1", "format": "table", "content": "neu"},
            ):
                with self.assertRaisesRegex(server.ToolError, "^invalid_arguments"):
                    server.call_tool("idea_format", arguments)
        request.assert_not_called()


class IdeaArrangeTests(unittest.TestCase):
    NODES = [
        {"id": "a", "title": "Zebra", "x": 510.0, "y": 95.0, "metadata": {"bewertung": {"wert": 10}}},
        {"id": "b", "title": "apfel", "x": 100.0, "y": 100.0, "metadata": {}},
        {"id": "c", "title": "Birne", "x": 130.0, "y": 420.0, "metadata": {"bewertung": {"wert": 90}}},
        {"id": "d", "title": "Dattel", "x": 900.0, "y": 400.0, "metadata": None},
    ]

    def _arrange(self, arguments):
        server = load_server()
        request = Recorder({("GET", "canvas_nodes"): [dict(node) for node in self.NODES]})
        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("idea_arrange", arguments)
        return result, request

    def test_grid_keeps_reading_order_and_anchors_top_left(self) -> None:
        result, request = self._arrange({"bubble_id": "bub1"})

        self.assertEqual(result["positionen"], [
            {"id": "b", "x": 100, "y": 95},
            {"id": "a", "x": 340, "y": 95},
            {"id": "c", "x": 100, "y": 235},
            {"id": "d", "x": 340, "y": 235},
        ])
        self.assertEqual(result["anzahl"], 4)
        self.assertEqual(result["verschoben"], 4)
        (read,) = request.of("GET", "canvas_nodes")
        self.assertEqual(read["params"]["linked_idea_id"], "eq.bub1")
        self.assertEqual(
            sorted(call["params"]["id"] for call in request.of("PATCH", "canvas_nodes")),
            ["eq.a", "eq.b", "eq.c", "eq.d"],
        )

    def test_order_by_title_and_by_score(self) -> None:
        by_title, _ = self._arrange({"bubble_id": "bub1", "order": "title", "columns": 4})
        self.assertEqual([p["id"] for p in by_title["positionen"]], ["b", "c", "d", "a"])
        self.assertEqual({p["y"] for p in by_title["positionen"]}, {95})

        by_score, _ = self._arrange({"bubble_id": "bub1", "order": "score", "columns": 1})
        self.assertEqual([p["id"] for p in by_score["positionen"]], ["c", "a", "b", "d"])
        self.assertEqual({p["x"] for p in by_score["positionen"]}, {100})

    def test_second_run_moves_nothing(self) -> None:
        server = load_server()
        first, _ = self._arrange({"bubble_id": "bub1"})
        placed = {p["id"]: p for p in first["positionen"]}
        rows = [{**node, "x": placed[node["id"]]["x"], "y": placed[node["id"]]["y"]} for node in self.NODES]
        request = Recorder({("GET", "canvas_nodes"): rows})
        with mock.patch.object(server, "_request", side_effect=request):
            second = server.call_tool("idea_arrange", {"bubble_id": "bub1"})

        self.assertEqual(second["verschoben"], 0)
        self.assertEqual(second["positionen"], first["positionen"])
        self.assertEqual(request.of("PATCH", "canvas_nodes"), [])

    def test_empty_bubble_and_cut_off_list(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=Recorder({})):
            self.assertEqual(
                server.call_tool("idea_arrange", {"bubble_id": "bub1"}),
                {"bubble_id": "bub1", "anzahl": 0, "verschoben": 0, "positionen": []},
            )
        many = [{"id": f"n{i}", "title": "t", "x": 0, "y": 0} for i in range(server.ARRANGE_NODE_CAP + 1)]
        request = Recorder({("GET", "canvas_nodes"): many})
        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(server.ToolError, "^too_many_ideas"):
                server.call_tool("idea_arrange", {"bubble_id": "bub1"})
        self.assertEqual(request.of("PATCH", "canvas_nodes"), [])

    def test_invalid_arguments_issue_no_request(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_request") as request:
            for arguments in (
                {},
                {"bubble_id": "bub 1"},
                {"bubble_id": "bub1", "order": "farbe"},
                {"bubble_id": "bub1", "columns": 0},
                {"bubble_id": "bub1", "columns": True},
                {"bubble_id": "bub1", "x": 3},
            ):
                with self.assertRaisesRegex(server.ToolError, "^invalid_arguments"):
                    server.call_tool("idea_arrange", arguments)
        request.assert_not_called()


class IdeaAutoLinkTests(unittest.TestCase):
    NODES = [
        {"id": "n1", "title": "Preise Druckerei Januar"},
        {"id": "n2", "title": "Druckerei Preise"},
        {"id": "n3", "title": "Stand zu Messe"},
        {"id": "n4", "title": "Messe Stand"},
        {"id": "n5", "title": "Urlaub"},
    ]

    def test_links_pairs_with_shared_title_words_and_skips_existing(self) -> None:
        server = load_server()
        request = Recorder({
            ("GET", "canvas_nodes"): [dict(node) for node in self.NODES],
            ("GET", "canvas_edges"): [{"from_node_id": "n4", "to_node_id": "n3"}],
            ("POST", "canvas_edges"): lambda kwargs: [kwargs["body"]],
        })
        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("idea_auto_link", {"bubble_id": "bub1"})

        self.assertEqual(result, {
            "bubble_id": "bub1",
            "anzahl": 1,
            "angelegt": [{"from_id": "n1", "to_id": "n2", "ueberlappung": 0.67}],
        })
        (edge_read,) = request.of("GET", "canvas_edges")
        self.assertEqual(edge_read["params"]["from_node_id"], "in.(n1,n2,n3,n4,n5)")
        (post,) = request.of("POST", "canvas_edges")
        self.assertEqual(
            {k: post["body"][k] for k in ("from_node_id", "to_node_id", "edge_type")},
            {"from_node_id": "n1", "to_node_id": "n2", "edge_type": "related"},
        )

    def test_limit_takes_the_strongest_pairs_first(self) -> None:
        server = load_server()
        request = Recorder({
            ("GET", "canvas_nodes"): [dict(node) for node in self.NODES],
            ("POST", "canvas_edges"): lambda kwargs: [kwargs["body"]],
        })
        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("idea_auto_link", {"bubble_id": "bub1", "limit": 1})

        # Woerter bis zwei Buchstaben zaehlen nicht: "Stand zu Messe" und "Messe Stand" decken sich ganz.
        self.assertEqual(result["angelegt"], [{"from_id": "n3", "to_id": "n4", "ueberlappung": 1.0}])

    def test_no_candidates_means_no_edge_requests(self) -> None:
        server = load_server()
        request = Recorder({("GET", "canvas_nodes"): [{"id": "n1", "title": "Eins"}, {"id": "n2", "title": "Zwei"}]})
        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("idea_auto_link", {"bubble_id": "bub1"})

        self.assertEqual(result, {"bubble_id": "bub1", "anzahl": 0, "angelegt": []})
        self.assertEqual([(m, p) for m, p, _k in request.calls], [("GET", "canvas_nodes")])

    def test_unsafe_stored_id_never_reaches_a_filter(self) -> None:
        server = load_server()
        request = Recorder({("GET", "canvas_nodes"): [{"id": "n1),x", "title": "A B C"}]})
        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(server.ToolError, "^supabase_invalid_response$"):
                server.call_tool("idea_auto_link", {"bubble_id": "bub1"})
        self.assertEqual(request.of("GET", "canvas_edges"), [])


class RateTests(unittest.TestCase):
    def test_idea_rate_keeps_other_metadata(self) -> None:
        server = load_server()
        node = {"id": "n1", "metadata": {"width": 200.0, "bewertung": {"wert": 5}}}
        request = Recorder({
            ("GET", "canvas_nodes"): [node],
            ("PATCH", "canvas_nodes"): lambda kwargs: [{"id": "n1", **kwargs["body"]}],
        })
        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("idea_rate", {"id": "n1", "score": 80})

        self.assertEqual(result["metadata"], {"width": 200.0, "bewertung": {"wert": 80}})
        (patch,) = request.of("PATCH", "canvas_nodes")
        self.assertEqual(patch["params"], {"id": "eq.n1"})

    def test_idea_rate_takes_no_reason_the_card_shows_the_number_only(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_request") as request:
            with self.assertRaisesRegex(server.ToolError, "^invalid_arguments"):
                server.call_tool("idea_rate", {"id": "n1", "score": 80, "begruendung": "x"})
        request.assert_not_called()

    def test_idea_rate_fails_for_a_missing_idea_without_writing(self) -> None:
        server = load_server()
        request = Recorder({})
        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(server.ToolError, "^idea_not_found$"):
                server.call_tool("idea_rate", {"id": "n1", "score": 1})
        self.assertEqual(request.of("PATCH", "canvas_nodes"), [])

    def test_bubble_rate_is_scoped_to_top_level_rows(self) -> None:
        server = load_server()
        request = Recorder({("PATCH", "ideas"): lambda kwargs: [{"id": "bub1", **kwargs["body"]}]})
        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("bubble_rate", {"id": "bub1", "score": 70})

        self.assertEqual(result, {"id": "bub1", "score": 70})
        (patch,) = request.of("PATCH", "ideas")
        self.assertEqual(patch["params"], {"id": "eq.bub1", "parent_id": "is.null"})

        with mock.patch.object(server, "_request", side_effect=Recorder({})):
            with self.assertRaisesRegex(server.ToolError, "^bubble_not_found$"):
                server.call_tool("bubble_rate", {"id": "bub1", "score": 70})

    def test_scores_outside_0_to_100_issue_no_request(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_request") as request:
            for name in ("idea_rate", "bubble_rate"):
                for score in (-1, 101, 7.5, "80", True, None):
                    with self.assertRaisesRegex(server.ToolError, "^invalid_arguments"):
                        server.call_tool(name, {"id": "x1", "score": score})
        request.assert_not_called()


class ReasonTests(unittest.TestCase):
    """Die Begruendung steht nicht an der Karte, sondern an der Bubble, mit Verweisen."""

    def _answers(self, metadata):
        return {
            ("GET", "ideas"): [{"id": "bub1", "metadata": metadata}],
            ("GET", "canvas_nodes"): [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}],
            ("PATCH", "ideas"): lambda kwargs: [{"id": "bub1", **kwargs["body"]}],
        }

    def test_reason_is_appended_to_the_bubble_with_links_to_its_cards(self) -> None:
        server = load_server()
        old = {"id": "alt1", "text": "frueher", "karten": []}
        request = Recorder(self._answers({"farbe": "blau", "bewertungen": [old]}))
        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("bewertung_begruenden", {
                "bubble_id": "bub1", "text": " Preise und Druckerei tragen die Idee. ",
                "idea_ids": ["n1", "n3"], "score": 70,
            })

        entry = result["bewertung"]
        self.assertEqual(
            {k: entry[k] for k in ("text", "karten", "wert")},
            {"text": "Preise und Druckerei tragen die Idee.", "karten": ["n1", "n3"], "wert": 70},
        )
        self.assertRegex(entry["id"], r"^[0-9a-f]{8}$")
        self.assertIn("erstellt_am", entry)
        (patch,) = request.of("PATCH", "ideas")
        self.assertEqual(patch["params"], {"id": "eq.bub1", "parent_id": "is.null"})
        self.assertEqual(patch["body"], {"metadata": {"farbe": "blau", "bewertungen": [old, entry]}})
        self.assertEqual(request.of("PATCH", "canvas_nodes"), [])

    def test_reason_without_cards_reads_no_cards_and_keeps_only_the_newest_entries(self) -> None:
        server = load_server()
        full = [{"id": f"e{i}", "text": "t", "karten": []} for i in range(server.REASON_ENTRY_CAP)]
        request = Recorder(self._answers({"bewertungen": full}))
        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("bewertung_begruenden", {"bubble_id": "bub1", "text": "Gesamturteil"})

        self.assertNotIn("wert", result["bewertung"])
        self.assertEqual(request.of("GET", "canvas_nodes"), [])
        stored = request.of("PATCH", "ideas")[0]["body"]["metadata"]["bewertungen"]
        self.assertEqual(len(stored), server.REASON_ENTRY_CAP)
        self.assertEqual(stored[0]["id"], "e1")
        self.assertEqual(stored[-1], result["bewertung"])

    def test_link_to_a_card_outside_the_bubble_or_a_missing_bubble_writes_nothing(self) -> None:
        server = load_server()
        request = Recorder(self._answers({}))
        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(server.ToolError, "^idea_not_in_bubble: fremd$"):
                server.call_tool("bewertung_begruenden", {"bubble_id": "bub1", "text": "x", "idea_ids": ["n1", "fremd"]})
        self.assertEqual(request.of("PATCH", "ideas"), [])

        request = Recorder({})
        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(server.ToolError, "^bubble_not_found$"):
                server.call_tool("bewertung_begruenden", {"bubble_id": "bub1", "text": "x"})
        self.assertEqual(request.of("PATCH", "ideas"), [])

    def test_remove_counts_newest_first_and_keeps_the_rest(self) -> None:
        server = load_server()
        alt = [{"id": "e1", "text": "eins", "karten": ["n1"]}, {"id": "e2", "text": "zwei", "karten": []},
               {"id": "e3", "text": "drei", "karten": ["n1"]}]
        request = Recorder(self._answers({"farbe": "blau", "bewertungen": alt}))
        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("bewertung_entfernen", {"bubble_id": "bub1", "nummer": 2})
        self.assertEqual(result, {"bubble_id": "bub1", "entfernt": alt[1]})
        (patch,) = request.of("PATCH", "ideas")
        self.assertEqual(patch["params"], {"id": "eq.bub1", "parent_id": "is.null"})
        self.assertEqual(patch["body"], {"metadata": {"farbe": "blau", "bewertungen": [alt[0], alt[2]]}})

        request = Recorder(self._answers({"bewertungen": alt}))
        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("bewertung_entfernen", {"bubble_id": "bub1", "nummer": 2, "idea_id": "n1"})
        self.assertEqual(result["entfernt"]["id"], "e1")                       # zweite zu n1, neueste zuerst

    def test_remove_missing_number_writes_nothing(self) -> None:
        server = load_server()
        request = Recorder(self._answers({"bewertungen": [{"id": "e1", "text": "eins", "karten": []}]}))
        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(server.ToolError, "^bewertung_not_found$"):
                server.call_tool("bewertung_entfernen", {"bubble_id": "bub1", "nummer": 2})
        self.assertEqual(request.of("PATCH", "ideas"), [])
        with mock.patch.object(server, "_request") as request:
            for arguments in ({"bubble_id": "bub1"}, {"bubble_id": "bub1", "nummer": 0},
                              {"bubble_id": "bub1", "nummer": True}, {"bubble_id": "bub1", "nummer": "1"},
                              {"bubble_id": "bub1", "nummer": 1, "text": "x"}):
                with self.assertRaisesRegex(server.ToolError, "^invalid_arguments"):
                    server.call_tool("bewertung_entfernen", arguments)
        request.assert_not_called()

    def test_invalid_arguments_issue_no_request(self) -> None:
        server = load_server()
        with mock.patch.object(server, "_request") as request:
            for arguments in (
                {"bubble_id": "bub1"},
                {"bubble_id": "bub1", "text": "   "},
                {"bubble_id": "bub1", "text": "x" * 2001},
                {"bubble_id": "bub1", "text": "x", "idea_ids": "n1"},
                {"bubble_id": "bub1", "text": "x", "idea_ids": ["n1", "n1"]},
                {"bubble_id": "bub1", "text": "x", "idea_ids": ["n1,n2"]},
                {"bubble_id": "bub1", "text": "x", "idea_ids": [f"n{i}" for i in range(21)]},
                {"bubble_id": "bub1", "text": "x", "score": 101},
                {"bubble_id": "bub1", "text": "x", "id": "n1"},
            ):
                with self.assertRaisesRegex(server.ToolError, "^invalid_arguments"):
                    server.call_tool("bewertung_begruenden", arguments)
        request.assert_not_called()


class IdeaCreateWithGivenIdTests(unittest.TestCase):
    def test_given_id_is_stored_and_unsafe_id_is_rejected(self) -> None:
        server = load_server()
        request = Recorder({("POST", "canvas_nodes"): lambda kwargs: [kwargs["body"]]})
        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("idea_create", {"bubble_id": "bub1", "title": "Preise", "id": "vor-7f3a"})
        self.assertEqual(result[0]["id"], "vor-7f3a")

        with mock.patch.object(server, "_request") as blocked:
            with self.assertRaisesRegex(server.ToolError, "^invalid_arguments"):
                server.call_tool("idea_create", {"bubble_id": "bub1", "title": "Preise", "id": "a,b"})
        blocked.assert_not_called()

    def test_change_id_groups_every_write_of_one_arrange(self) -> None:
        server = load_server()
        seen = []

        def request(method, path, **kwargs):
            seen.append((method, server._CHANGE_ID.get()))
            if method == "GET":
                return [{"id": "a", "x": 5, "y": 5}, {"id": "b", "x": 900, "y": 900}]
            return [{}]

        with mock.patch.object(server, "_request", side_effect=request):
            server.call_tool("idea_arrange", {"bubble_id": "bub1", "aenderung_id": "plan_9"})

        self.assertEqual([change for method, change in seen if method == "PATCH"], ["plan_9"])
        self.assertIsNone(server._CHANGE_ID.get())


if __name__ == "__main__":
    unittest.main()


class Arbeitslauf20261123Tests(unittest.TestCase):
    """Befunde aus dem Ideen-Arbeitslauf vom 23.11.2026."""

    def test_format_without_text_is_refused_instead_of_an_empty_table(self) -> None:
        server = load_server()
        for content in ("", None, "Preise"):  # "Preise" wiederholt nur den Titel
            node = {"id": "n1", "title": "Preise", "content": content, "node_type": "note",
                    "content_json": None, "previous_content_json": None}
            request = Recorder({("GET", "canvas_nodes"): [node]})
            with mock.patch.object(server, "_request", side_effect=request):
                with self.assertRaisesRegex(server.ToolError, "^idea_format_no_content$"):
                    server.call_tool("idea_format", {"id": "n1", "format": "kanban"})
            self.assertEqual(request.of("PATCH", "canvas_nodes"), [])
        # Als Notiz darstellen geht immer.
        node = {"id": "n1", "title": "Preise", "content": "", "node_type": "table",
                "content_json": None, "previous_content_json": None}
        request = Recorder({("GET", "canvas_nodes"): [node],
                            ("PATCH", "canvas_nodes"): lambda kwargs: [{**node, **kwargs["body"]}]})
        with mock.patch.object(server, "_request", side_effect=request):
            server.call_tool("idea_format", {"id": "n1", "format": "note"})

    def test_too_many_ideas_names_the_real_count_and_the_limit(self) -> None:
        server = load_server()
        many = [{"id": f"n{i}", "title": "t", "x": 0, "y": 0} for i in range(server.ARRANGE_NODE_CAP + 1)]
        request = Recorder({("GET", "canvas_nodes"): many})
        with mock.patch.object(server, "_request", side_effect=request):
            with self.assertRaisesRegex(server.ToolError, r"^too_many_ideas: 101 ideas in this bubble, limit 100$"):
                server.call_tool("idea_arrange", {"bubble_id": "bub1"})

    def test_auto_link_works_beyond_100_cards_and_reads_edges_in_chunks(self) -> None:
        server = load_server()
        nodes = [{"id": f"n{i:03d}", "title": f"Messe Stand {i}" if i < 2 else f"Wort{i}"} for i in range(250)]
        request = Recorder({("GET", "canvas_nodes"): nodes, ("GET", "canvas_edges"): []})
        with mock.patch.object(server, "_request", side_effect=request):
            result = server.call_tool("idea_auto_link", {"bubble_id": "bub1"})
        self.assertEqual(result["anzahl"], 1)
        self.assertEqual(request.of("GET", "canvas_nodes")[0]["params"]["limit"], str(server.AUTO_LINK_NODE_CAP + 1))
        self.assertEqual(len(request.of("GET", "canvas_edges")), 3)  # 250 Kennungen in Teilen zu 100

    def test_reason_with_cards_reads_only_the_named_cards(self) -> None:
        server = load_server()
        request = Recorder({
            ("GET", "ideas"): [{"id": "bub1", "metadata": {}}],
            ("GET", "canvas_nodes"): [{"id": "n1"}],
            ("PATCH", "ideas"): lambda kwargs: [{"id": "bub1", **kwargs["body"]}],
        })
        with mock.patch.object(server, "_request", side_effect=request):
            server.call_tool("bewertung_begruenden", {"bubble_id": "bub1", "text": "x", "idea_ids": ["n1"]})
        (lesen,) = request.of("GET", "canvas_nodes")
        self.assertEqual(lesen["params"]["id"], "in.(n1)")
        self.assertEqual(lesen["params"]["linked_idea_id"], "eq.bub1")

    def test_content_append_adds_to_the_stored_text(self) -> None:
        server = load_server()
        for stored, expected in (("Alt", "Alt\n\nNeu"), ("Preise", "Neu"), ("", "Neu")):
            request = Recorder({
                ("GET", "canvas_nodes"): [{"id": "n1", "title": "Preise", "content": stored}],
                ("PATCH", "canvas_nodes"): lambda kwargs: [{"id": "n1", **kwargs["body"]}],
            })
            with mock.patch.object(server, "_request", side_effect=request):
                server.call_tool("idea_update", {"id": "n1", "content_append": " Neu "})
            (patch,) = request.of("PATCH", "canvas_nodes")
            self.assertEqual(patch["body"], {"content": expected})
        with mock.patch.object(server, "_request") as request:
            with self.assertRaises(server.ToolError):
                server.call_tool("idea_update", {"id": "n1", "content": "a", "content_append": "b"})
            request.assert_not_called()
