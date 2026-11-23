"""bubble_wurzel: Wurzel der Bubble setzen und loeschen (White-Paper-Zusatz 1). Erfundene Namen."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from unittest import mock

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_ideas_mcp_server_wurzel", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WurzelTests(unittest.TestCase):
    def _lauf(self, argumente, *, karte=True, metadata=None):
        server = load_server()
        aufrufe = []

        def antwort(method, path, **kwargs):
            aufrufe.append((method, path, kwargs))
            if method == "GET" and path == "ideas":
                return [{"id": "b0b1e001", "metadata": metadata if metadata is not None else {"bewertungen": []}}]
            if method == "GET" and path == "canvas_nodes":
                return [{"id": "a0000002"}] if karte else []
            return [{"id": "b0b1e001", **kwargs["body"]}]
        with mock.patch.object(server, "_request", side_effect=antwort):
            ergebnis = server.call_tool("bubble_wurzel", argumente)
        return server, ergebnis, aufrufe

    def test_setzen_behaelt_die_uebrigen_metadaten(self) -> None:
        _s, ergebnis, aufrufe = self._lauf({"bubble_id": "b0b1e001", "id": "a0000002"})
        self.assertEqual(ergebnis, {"bubble_id": "b0b1e001", "wurzel": "a0000002"})
        (patch,) = [k for m, p, k in aufrufe if m == "PATCH"]
        self.assertEqual(patch["body"], {"metadata": {"bewertungen": [], "wurzel": "a0000002"}})
        karte = [k for m, p, k in aufrufe if m == "GET" and p == "canvas_nodes"][0]
        self.assertEqual(karte["params"]["linked_idea_id"], "eq.b0b1e001")

    def test_loeschen_ohne_id(self) -> None:
        _s, ergebnis, aufrufe = self._lauf({"bubble_id": "b0b1e001"}, metadata={"wurzel": "a0000002"})
        self.assertEqual(ergebnis["wurzel"], None)
        (patch,) = [k for m, p, k in aufrufe if m == "PATCH"]
        self.assertEqual(patch["body"], {"metadata": {}})

    def test_fremde_karte_wird_abgelehnt_und_nichts_geschrieben(self) -> None:
        with self.assertRaisesRegex(Exception, "^idea_not_in_bubble") as fehler:
            self._lauf({"bubble_id": "b0b1e001", "id": "a0000009"}, karte=False)
        self.assertEqual(type(fehler.exception).__name__, "ToolError")

    def test_ist_ruecknehmbar(self) -> None:
        server = load_server()
        self.assertIn("bubble_wurzel", server.CHANGE_WRITE_TOOLS)


if __name__ == "__main__":
    unittest.main()
