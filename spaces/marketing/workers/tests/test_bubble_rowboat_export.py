"""Bubble-Export nach Rowboat: klassifizierte Bubbles werden Metadaten,
die BEIDE claws nachschlagen koennen (sales wie marketing).

Ohne Netz, ohne DB: die Datenbanktuer und der MCP-Griff werden ersetzt.
Der Test haelt drei Zusagen fest: (1) je Bubble ein Dokument mit stabilem
Namen, (2) unveraenderte Bubbles werden nicht erneut geschrieben
(idempotent), (3) der Schluessel steht in keiner Ausgabe.
"""
import json
import unittest
from unittest import mock

from spaces.marketing.workers import bubble_rowboat_export as ex


BUBBLES = [
    {"id": "b1", "title": "Landingpage bauen", "description": "Text",
     "category": "code_project", "confidence": 0.91,
     "metadata": {"classifier": {"reason": "Code"}},
     "classified_at": "2026-09-02T10:00:00Z"},
    {"id": "b2", "title": "Herbstkampagne", "description": "",
     "category": "marketing", "confidence": 0.77,
     "metadata": {"classifier": {"reason": "Werbung"}},
     "classified_at": "2026-09-02T11:00:00Z"},
]


class TestExport(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict("os.environ", {
            "ROWBOAT_URL": "http://vm:3100",
            "ROWBOAT_PROJECT_ID": "proj-1",
            "ROWBOAT_API_KEY": "geheim-999",
            "BUBBLE_EXPORT_SOURCE_ID": "src-1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_je_bubble_ein_dokument_mit_stabilem_namen(self):
        geschrieben = []

        def mcp(werkzeug, argumente):
            if werkzeug == "rowboat_dokumente":
                return {"ok": True, "daten": []}
            geschrieben.append((werkzeug, argumente))
            return {"ok": True, "daten": {"geschrieben": len(argumente.get("dokumente", []))}}

        with mock.patch.object(ex, "_rowboat", mcp):
            anzahl = ex.exportieren(BUBBLES)

        self.assertEqual(anzahl, 2)
        werkzeug, argumente = geschrieben[0]
        self.assertEqual(werkzeug, "rowboat_dokumente_schreiben")
        self.assertEqual(argumente["sourceId"], "src-1")
        namen = [d["name"] for d in argumente["dokumente"]]
        self.assertEqual(namen, ["bubble-b1", "bubble-b2"])
        inhalt = argumente["dokumente"][0]["inhalt"]
        for teil in ("b1", "Landingpage bauen", "code_project", "0.91", "Code"):
            self.assertIn(teil, inhalt)

    def test_unveraenderte_bubbles_werden_uebersprungen(self):
        vorhanden = [{"id": "d1", "name": "bubble-b1", "typ": "text",
                      "content": ex.dokument_inhalt(BUBBLES[0])}]
        geschrieben = []

        def mcp(werkzeug, argumente):
            if werkzeug == "rowboat_dokumente":
                return {"ok": True, "daten": vorhanden}
            geschrieben.append(argumente)
            return {"ok": True, "daten": {}}

        with mock.patch.object(ex, "_rowboat", mcp):
            anzahl = ex.exportieren(BUBBLES)

        self.assertEqual(anzahl, 1)
        namen = [d["name"] for d in geschrieben[0]["dokumente"]]
        self.assertEqual(namen, ["bubble-b2"])

    def test_frisch_geschriebenes_wird_nicht_verdoppelt(self):
        """Der rag-worker fuellt `content` erst verzoegert. Ein Dokument, das
        mit dem Namen schon dasteht, aber noch keinen Inhalt hat, darf NICHT
        erneut geschrieben werden — sonst verdoppelt jeder Lauf die Quelle."""
        vorhanden = [{"id": "d1", "name": "bubble-b1", "typ": "text",
                      "status": "pending", "content": None},
                     {"id": "d2", "name": "bubble-b2", "typ": "text",
                      "status": "pending", "content": None}]
        geschrieben = []

        def mcp(werkzeug, argumente):
            if werkzeug == "rowboat_dokumente":
                return {"ok": True, "daten": vorhanden}
            geschrieben.append(argumente)
            return {"ok": True, "daten": {}}

        with mock.patch.object(ex, "_rowboat", mcp):
            anzahl = ex.exportieren(BUBBLES)

        self.assertEqual(anzahl, 0)
        self.assertEqual(geschrieben, [])

    def test_geaenderte_bubble_wird_neu_geschrieben(self):
        vorhanden = [{"id": "d1", "name": "bubble-b1", "typ": "text",
                      "status": "ready", "content": "veralteter Stand"}]
        geschrieben = []

        def mcp(werkzeug, argumente):
            if werkzeug == "rowboat_dokumente":
                return {"ok": True, "daten": vorhanden}
            geschrieben.append(argumente)
            return {"ok": True, "daten": {}}

        with mock.patch.object(ex, "_rowboat", mcp):
            anzahl = ex.exportieren(BUBBLES)

        self.assertEqual(anzahl, 2)
        self.assertEqual([d["name"] for d in geschrieben[0]["dokumente"]],
                         ["bubble-b1", "bubble-b2"])

    def test_ohne_quellkennung_hoerbarer_fehler(self):
        with mock.patch.dict("os.environ", {"BUBBLE_EXPORT_SOURCE_ID": ""}):
            with self.assertRaises(RuntimeError) as f:
                ex.exportieren(BUBBLES)
        self.assertIn("BUBBLE_EXPORT_SOURCE_ID", str(f.exception))

    def test_kein_schluessel_in_der_ausgabe(self):
        def mcp(werkzeug, argumente):
            return {"ok": False, "fehler": "Bearer <schluessel> abgelehnt"}

        with mock.patch.object(ex, "_rowboat", mcp):
            with self.assertRaises(RuntimeError) as f:
                ex.exportieren(BUBBLES)
        self.assertNotIn("geheim-999", str(f.exception))


if __name__ == "__main__":
    unittest.main()
