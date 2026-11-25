"""Befoerdern mit Uebergabe: Sinnpruefung vorher, fester Stand im Projekt, Ziel Software-Design.

Nur erfundene Testdaten (Stadtfest). Die Regeln kommen aus spaces/_contract/kartenregeln.py,
derselben Datei, die die Software-Design-Ansicht liest.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

SERVER_PATH = Path(__file__).resolve().parents[1] / "mcp_server.py"
BUBBLE = "b0b1e001"


def load_server():
    spec = importlib.util.spec_from_file_location("spaces_ideas_mcp_server_befoerdern", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _karte(kennung, titel, inhalt, **mehr):
    return {"id": kennung, "node_type": mehr.pop("node_type", "note"), "title": titel, "content": inhalt,
            "content_json": None, "metadata": mehr.pop("metadata", {}), "updated_at": mehr.pop("am", "2026-11-20T10:00:00+00:00")}


GUTE_KARTEN = [
    _karte("a0000001", "Sponsoren", "", metadata={"gruppen_kopf": True}),
    _karte("a0000002", "Druckerei", "Die Druckerei liefert Plakate bis Freitag."),
    _karte("a0000003", "Bühnenplan", "Der Bühnenplan steht für Samstag und Sonntag fest."),
    _karte("a0000004", "White Paper", "Das Stadtfest braucht einen Bühnenplan und Sponsoren.",
           node_type="dokument", metadata={"art": "white_paper", "quellen": ["a0000002", "a0000003"]},
           am="2026-11-22T08:30:00+00:00"),
]
GUTE_KANTEN = [
    {"from_node_id": "a0000001", "to_node_id": "a0000002", "edge_type": "related"},
    {"from_node_id": "a0000002", "to_node_id": "a0000003", "edge_type": "related"},
]


class Datenbank:
    """Antwortet wie PostgREST auf genau die Lesewege des Befoerderns; schreibt mit."""

    def __init__(self, karten, kanten, bubble=None):
        self.karten, self.kanten = karten, kanten
        self.bubble = bubble or {"id": BUBBLE, "title": "Stadtfest", "description": "", "score": 70,
                                 "metadata": {}}
        self.aufrufe = []
        self.projekt = None

    def __call__(self, method, path, **kwargs):
        self.aufrufe.append((method, path))
        params = kwargs.get("params") or {}
        if method == "GET" and path == "ideas":
            return [self.bubble]
        if method == "GET" and path == "canvas_nodes":
            return list(self.karten)
        if method == "GET" and path == "canvas_edges":
            von = params["from_node_id"][len("in.("):-1].split(",")
            return [k for k in self.kanten if k["from_node_id"] in von]
        if method == "POST" and path == "projects":
            self.projekt = dict(kwargs["body"])
            return [self.projekt]
        if method == "PATCH" and path == "ideas":
            return [{"id": BUBBLE, "status": "promoted", "promoted_to_project_id": self.projekt["id"]}]
        raise AssertionError(f"unerwartet: {method} {path}")


class BefoerdernTests(unittest.TestCase):
    def _befoerdern(self, db, **argumente):
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=db):
            return server, server.call_tool("bubble_promote", {"bubble_id": BUBBLE, **argumente})

    def test_gute_bubble_wird_projekt_mit_festem_stand_und_ziel(self):
        db = Datenbank(GUTE_KARTEN, GUTE_KANTEN)
        _server, quittung = self._befoerdern(db)
        stand = db.projekt["metadata"]["uebergabe"]

        self.assertEqual(stand["version"], 1)
        self.assertEqual((stand["bubble_id"], stand["titel"]), (BUBBLE, "Stadtfest"))
        self.assertEqual(stand["bewertung"], {"score": 70, "begruendungen": 0})
        self.assertEqual(stand["white_paper"]["node_id"], "a0000004")
        self.assertIn("Bühnenplan", stand["white_paper"]["text"])
        self.assertEqual(stand["white_paper"]["quellen"], ["a0000002", "a0000003"])
        karten = {k["id"]: k for k in stand["karten"]}
        self.assertNotIn("a0000004", karten)                      # das White Paper steht eigens
        self.assertEqual(karten["a0000001"]["art"], "gruppe")
        self.assertEqual(karten["a0000002"]["gruppe"], "Sponsoren")
        self.assertIsNone(karten["a0000003"]["gruppe"])
        self.assertEqual(karten["a0000003"]["text"], "Der Bühnenplan steht für Samstag und Sonntag fest.")
        self.assertEqual(stand["kanten"], [{"von": "a0000001", "nach": "a0000002", "art": "related"},
                                           {"von": "a0000002", "nach": "a0000003", "art": "related"}])
        self.assertTrue(stand["pruefung"]["bestanden"])

        # Pruefsumme so, wie die Ansicht sie nachrechnet: ohne "am" und "pruefsumme".
        kern = {k: v for k, v in stand.items() if k not in ("am", "pruefsumme")}
        erwartet = hashlib.sha256(json.dumps(kern, sort_keys=True, ensure_ascii=False,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(stand["pruefsumme"], "sha256:" + erwartet)

        # Quittung: klein, mit Ziel fuer die App.
        self.assertEqual(quittung["ziel"], {"space": "swedesign", "projekt_id": db.projekt["id"]})
        self.assertEqual(quittung["karten"], 2)
        self.assertEqual(quittung["white_paper_vom"], "2026-11-22T08:30:00+00:00")
        self.assertNotIn("uebergabe", quittung["metadata"])
        self.assertEqual(db.aufrufe, [("GET", "ideas"), ("GET", "canvas_nodes"), ("GET", "canvas_edges"),
                                      ("POST", "projects"), ("PATCH", "ideas")])

    def _bubble_mit_lauf(self, stand=None):
        regeln = load_server()._kartenregeln()
        sichten = {name: {"punkte": 19, "urteil": f"Urteil {name}: Bühnenplan und Sponsoren sind belegt."}
                   for name in regeln.SICHTEN}
        lauf = {"version": 1, "zeit": "2026-11-25T09:00:00+00:00", "score": 95, "sichten": sichten,
                "todos": [{"nr": 1, "titel": "Druckerei", "punkte": 2, "erledigt": False}],
                "kartenstand": stand or regeln.kartenstand(GUTE_KARTEN, GUTE_KANTEN), "gekuerzt": False}
        return {"id": BUBBLE, "title": "Stadtfest", "description": "", "score": 95,
                "metadata": {"bewertung_lauf": lauf}}

    def test_begruendungen_des_gueltigen_laufs_gehen_mit(self):
        db = Datenbank(GUTE_KARTEN, GUTE_KANTEN, bubble=self._bubble_mit_lauf())
        self._befoerdern(db)
        stand = db.projekt["metadata"]["uebergabe"]
        begruendung = stand["bewertung_begruendung"]
        self.assertEqual((begruendung["score"], begruendung["zeit"], begruendung["gekuerzt"]),
                         (95, "2026-11-25T09:00:00+00:00", False))
        self.assertEqual(list(begruendung["sichten"]),
                         ["coding", "swe_design", "research", "rowboat", "ideas"])
        self.assertEqual(begruendung["sichten"]["research"],
                         {"name": "Research", "punkte": 19,
                          "urteil": "Urteil research: Bühnenplan und Sponsoren sind belegt."})
        self.assertNotIn("todos", begruendung)
        # Die Begruendung steht in der Pruefsumme.
        kern = {k: v for k, v in stand.items() if k not in ("am", "pruefsumme")}
        self.assertIn("bewertung_begruendung", kern)

    def test_veralteter_oder_fehlender_lauf_gibt_keine_begruendung_mit(self):
        for bubble in (self._bubble_mit_lauf(stand="sha256:" + "0" * 64), None):
            db = Datenbank(GUTE_KARTEN, GUTE_KANTEN, bubble=bubble)
            self._befoerdern(db)
            self.assertIsNone(db.projekt["metadata"]["uebergabe"]["bewertung_begruendung"])

    def test_durchgefallen_nennt_die_gruende_und_schreibt_nichts(self):
        karten = [
            _karte("a0000002", "Druckerei", "Plakate"),                          # leer, lose
            _karte("a0000003", "Bühnenplan", "Der Bühnenplan steht für Samstag fest."),
        ]
        bubble = {"id": BUBBLE, "title": "Stadtfest", "score": 0.0, "metadata": {}}
        db = Datenbank(karten, [], bubble=bubble)
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=db):
            with self.assertRaises(server.ToolError) as fehler:
                server.call_tool("bubble_promote", {"bubble_id": BUBBLE})
        text = str(fehler.exception)
        self.assertTrue(text.startswith(
            "befoerdern_nicht_bereit: leere_karten:1,lose_karten:2,keine_bewertung,kein_white_paper"), text)
        self.assertIn("leer=a0000002", text)
        self.assertIn("lose=a0000002,a0000003", text)
        self.assertNotIn(("POST", "projects"), db.aufrufe)
        self.assertNotIn(("PATCH", "ideas"), db.aufrufe)

    def test_bewertung_null_ohne_begruendung_fehlt_mit_begruendung_zaehlt(self):
        server = load_server()
        self.assertIsNone(server._bewertung({"score": 0.0, "metadata": {}}))
        self.assertIsNone(server._bewertung({"score": None}))
        self.assertEqual(server._bewertung({"score": 0, "metadata": {"bewertungen": [{"id": "x"}]}}),
                         {"score": 0, "begruendungen": 1})
        self.assertEqual(server._bewertung({"score": 55}), {"score": 55, "begruendungen": 0})

    def test_zu_grosser_stand_wird_nicht_befoerdert(self):
        db = Datenbank(GUTE_KARTEN, GUTE_KANTEN)
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=db), \
                mock.patch.object(server, "UEBERGABE_MAX_ZEICHEN", 500):
            with self.assertRaisesRegex(server.ToolError, "^befoerdern_zu_gross"):
                server.call_tool("bubble_promote", {"bubble_id": BUBBLE})
        self.assertNotIn(("POST", "projects"), db.aufrufe)

    def test_schon_befoerdert_liest_keine_karten(self):
        bubble = {"id": BUBBLE, "title": "Stadtfest", "score": 70, "promoted_to_project_id": "c0000001"}
        db = Datenbank(GUTE_KARTEN, GUTE_KANTEN, bubble=bubble)
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=db):
            with self.assertRaisesRegex(server.ToolError, "^bubble_already_promoted"):
                server.call_tool("bubble_promote", {"bubble_id": BUBBLE})
        self.assertEqual(db.aufrufe, [("GET", "ideas")])

    def test_regeln_kommen_aus_der_gemeinsamen_datei(self):
        server = load_server()
        regeln = server._kartenregeln()
        self.assertTrue(Path(regeln.__file__).as_posix().endswith("spaces/_contract/kartenregeln.py"))


if __name__ == "__main__":
    unittest.main()


class FormatierteKartenTests(unittest.TestCase):
    """Formatierte Karten: der Inhalt steht in content_json, content wiederholt nur den Titel."""

    def test_strukturierter_inhalt_zaehlt_und_steht_im_stand(self):
        specs = {"type": "technical_specs", "component": "Druckerei",
                 "specifications": [{"category": "Performance",
                                     "requirement": "Plakate liegen drei Tage vor dem Fest im Rathaus.",
                                     "acceptance_criteria": "Abholschein der Druckerei liegt vor."}]}
        karten = [dict(k) for k in GUTE_KARTEN]
        karten[1] = {**karten[1], "content": "Druckerei", "content_json": specs}
        db = Datenbank(karten, GUTE_KANTEN)
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=db):
            server.call_tool("bubble_promote", {"bubble_id": BUBBLE})
        stand = db.projekt["metadata"]["uebergabe"]
        text = {k["id"]: k["text"] for k in stand["karten"]}["a0000002"]
        self.assertIn("drei Tage vor dem Fest", text)
        self.assertNotIn("Performance", text)
        self.assertTrue(stand["pruefung"]["bestanden"])

    def test_absage_der_formatierer_ist_leer(self):
        absage = {"type": "technical_specs", "specifications": [],
                  "extraction_notes": "Keine technischen Anforderungen im bereitgestellten Inhalt gefunden."}
        karten = [dict(k) for k in GUTE_KARTEN]
        karten[1] = {**karten[1], "content": "Druckerei", "content_json": absage}
        db = Datenbank(karten, GUTE_KANTEN)
        server = load_server()
        with mock.patch.object(server, "_request", side_effect=db):
            with self.assertRaises(server.ToolError) as fehler:
                server.call_tool("bubble_promote", {"bubble_id": BUBBLE})
        self.assertIn("leere_karten:1", str(fehler.exception))
        self.assertIn("leer=a0000002", str(fehler.exception))


class IdeaListSeitenTests(unittest.TestCase):
    """idea_list blaettert mit offset und liefert auf Wunsch die Verbindungen mit."""

    def _liste(self, argumente, karten, kanten=(), gesamt=None):
        aufrufe = []

        def db(method, path, **kwargs):
            params = dict(kwargs.get("params") or {})
            aufrufe.append((method, path, params))
            if path == "canvas_nodes" and params.get("select") == "id":
                return [{"id": f"x{i}"} for i in range(gesamt)]
            if path == "canvas_nodes":
                start = int(params.get("offset", 0))
                return karten[start:start + int(params["limit"])]
            if path == "canvas_edges":
                von = params["from_node_id"][len("in.("):-1].split(",")
                return [k for k in kanten if k["from_node_id"] in von]
            raise AssertionError(f"unerwartet: {method} {path}")

        server = load_server()
        with mock.patch.object(server, "_request", side_effect=db):
            return server.call_tool("idea_list", {"bubble_id": BUBBLE, **argumente}), aufrufe

    def test_zweite_seite_mit_versatz(self):
        karten = [_karte(f"a{i:07d}", f"Stand {i}", "") for i in range(1, 6)]
        antwort, aufrufe = self._liste({"limit": 2, "offset": 2}, karten, gesamt=5)
        self.assertEqual([k["id"] for k in antwort["karten"]], ["a0000003", "a0000004"])
        self.assertEqual((antwort["versatz"], antwort["weiter_ab"], antwort["gesamt"]), (2, 4, 5))
        self.assertEqual(aufrufe[0][2]["offset"], "2")
        letzte, _ = self._liste({"limit": 2, "offset": 4}, karten, gesamt=5)
        self.assertEqual(([k["id"] for k in letzte["karten"]], letzte["weiter_ab"]), (["a0000005"], None))

    def test_kanten_nur_auf_wunsch(self):
        karten = [_karte("a0000001", "Druckerei", ""), _karte("a0000002", "Bühnenplan", "")]
        ohne, aufrufe = self._liste({}, karten, GUTE_KANTEN, gesamt=2)
        self.assertNotIn("kanten", ohne)
        self.assertFalse(any(path == "canvas_edges" for _m, path, _p in aufrufe))
        mit, _ = self._liste({"kanten": True}, karten, GUTE_KANTEN, gesamt=2)
        self.assertEqual(mit["kanten"], GUTE_KANTEN)

    def test_falscher_versatz_wird_abgelehnt(self):
        server = load_server()
        for falsch in (-1, True, "2"):
            with self.assertRaises(server.ToolError):
                server.call_tool("idea_list", {"bubble_id": BUBBLE, "offset": falsch})
