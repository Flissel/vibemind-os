"""Recherche-Lauf nach dem Rueckweg-Muster (24.11.2026): der Server holt, der Agent liest.

Kein echter Agent, kein echtes Netz: Modell, Seiten und Waechter sind ersetzt. Beispiele erfunden.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

LAUF_PATH = Path(__file__).resolve().parents[1] / "lauf.py"


def load_lauf():
    spec = importlib.util.spec_from_file_location("spaces_research_lauf_unter_test", LAUF_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lauf = load_lauf()

BRIEF = "[Auftrag]\nTicketpreise anderer Stadtfeste vergleichen."
SEITE_A = "https://beispiel-stadtfest.example/preise"
SEITE_B = "https://druckerei.example/angebot"


class _Modul:
    def __init__(self, erlaubt=True, grund="ok"):
        self.pruefungen = 0
        self.buchungen = []
        self.erlaubt, self.grund = erlaubt, grund

    def pruefen(self, agent):
        self.pruefungen += 1
        return SimpleNamespace(erlaubt=self.erlaubt, grund=self.grund)

    def buchen(self, agent, ergebnis, dauer_s):
        self.buchungen.append((agent, ergebnis))


class _Modell:
    def __init__(self, *antworten):
        self.antworten = list(antworten)
        self.auftraege = []

    def __call__(self, text):
        self.auftraege.append(text)
        return self.antworten.pop(0)


class AgentNameTests(unittest.TestCase):
    def test_finds_exactly_one_agent_by_name(self) -> None:
        liste = [{"id": "a0000001", "name": "brain-researcher"}, {"id": "a0000002", "name": "brain-writer"}]
        self.assertEqual(lauf.agent_id_aufloesen(liste), "a0000001")
        self.assertEqual(lauf.agent_id_aufloesen({"agents": liste}), "a0000001")

    def test_missing_or_ambiguous_agent_fails_closed(self) -> None:
        for liste, grund in (([], "agent_nicht_gefunden"),
                             ([{"id": "a1", "name": "brain-researcher"},
                               {"id": "a2", "name": "Brain-Researcher"}], "agent_mehrdeutig"),
                             ([{"name": "brain-researcher"}], "agent_ohne_kennung"),
                             ("kaputt", "agentenliste_ungueltig")):
            with self.assertRaises(lauf.LaufFehler) as ctx:
                lauf.agent_id_aufloesen(liste)
            self.assertIn(grund, str(ctx.exception))


class AblaufTests(unittest.TestCase):
    def _laufen(self, modell, holen, modul=None):
        modul = modul or _Modul()
        return lauf.laufen(BRIEF, senden=modell, waechter=lauf.Waechter(modul), holen=holen), modul

    def test_two_model_runs_server_fetches_report_cites_fetched_page(self) -> None:
        modell = _Modell(f"{SEITE_A}\n{SEITE_B}", f"# Bericht\nPreise laut {SEITE_A}.")
        geholt = []
        (bericht, zitiert), modul = self._laufen(modell, lambda url: geholt.append(url) or f"Text von {url}")
        self.assertEqual(geholt, [SEITE_A, SEITE_B])
        self.assertEqual(zitiert, [SEITE_A])
        self.assertIn("Preise laut", bericht)
        self.assertEqual(modul.pruefungen, 2)
        self.assertEqual(modul.buchungen, [("brain-researcher", "ok"), ("brain-researcher", "ok")])
        self.assertIn(f"Text von {SEITE_B}", modell.auftraege[1])   # der Agent liest, was der Server holte
        self.assertIn("keine Werkzeuge", modell.auftraege[0])

    def test_at_most_max_pages_are_fetched(self) -> None:
        adressen = "\n".join(f"https://seite{i}.example/" for i in range(9))
        geholt = []
        modell = _Modell(adressen, "Bericht mit https://seite0.example/")
        self._laufen(modell, lambda url: geholt.append(url) or "Text")
        self.assertEqual(len(geholt), lauf.MAX_SEITEN)

    def test_budget_refusal_stops_before_any_model_run(self) -> None:
        modell = _Modell("egal")
        with self.assertRaises(lauf.LaufFehler) as ctx:
            self._laufen(modell, lambda url: "x", _Modul(erlaubt=False, grund="tagesgrenze"))
        self.assertIn("budget_abgelehnt: tagesgrenze", str(ctx.exception))
        self.assertEqual(modell.auftraege, [])

    def test_report_without_a_fetched_page_is_not_accepted(self) -> None:
        modell = _Modell(SEITE_A, "Bericht mit erfundener Quelle https://anderswo.example/")
        with self.assertRaises(lauf.LaufFehler) as ctx:
            self._laufen(modell, lambda url: "Text")
        self.assertIn("no citation", str(ctx.exception))

    def test_no_readable_page_means_no_second_model_run(self) -> None:
        modell = _Modell(SEITE_A, "nie gefragt")
        modul = _Modul()
        with self.assertRaises(lauf.LaufFehler):
            self._laufen(modell, lambda url: None, modul)
        self.assertEqual(len(modell.auftraege), 1)
        self.assertEqual(modul.pruefungen, 1)

    def test_failed_model_run_is_booked_as_error(self) -> None:
        def kaputt(text):
            raise OSError("Abo-Grenze")
        with self.assertRaises(lauf.LaufFehler):
            _, modul = self._laufen(kaputt, lambda url: "x")
        # Buchung als Fehler, kein zweiter Lauf
        modul = _Modul()
        with self.assertRaises(lauf.LaufFehler):
            lauf.laufen(BRIEF, senden=kaputt, waechter=lauf.Waechter(modul), holen=lambda url: "x")
        self.assertEqual(modul.buchungen, [("brain-researcher", "fehler")])


class WaechterTests(unittest.TestCase):
    def test_without_guard_module_nothing_runs(self) -> None:
        with self.assertRaises(lauf.LaufFehler) as ctx:
            lauf.Waechter.laden("  ")
        self.assertIn("budget_ohne_waechter", str(ctx.exception))

    def test_guard_error_fails_closed(self) -> None:
        modul = SimpleNamespace(pruefen=mock.Mock(side_effect=RuntimeError("x")))
        with self.assertRaises(lauf.LaufFehler) as ctx:
            lauf.Waechter(modul).pruefen()
        self.assertIn("budget_waechter_fehler", str(ctx.exception))


def _aufloesen(adresse):
    def aufloesen(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (adresse, 0))]
    return aufloesen


class AdressTests(unittest.TestCase):
    def test_only_public_http_addresses(self) -> None:
        oeffentlich = _aufloesen("93.184.216.34")
        self.assertTrue(lauf.url_erlaubt("https://stadtfest.example/", oeffentlich))
        self.assertFalse(lauf.url_erlaubt("ftp://stadtfest.example/", oeffentlich))
        self.assertFalse(lauf.url_erlaubt("file:///etc/passwd", oeffentlich))
        self.assertFalse(lauf.url_erlaubt("https://nutzer:pw@stadtfest.example/", oeffentlich))
        # Private Netze deckt ipaddress.is_global ab; hier Loopback, Link-local, CGNAT, IPv6-lokal.
        for intern in ("127.0.0.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "::1", "fd00::1"):
            self.assertFalse(lauf.url_erlaubt("http://intern.example/", _aufloesen(intern)), intern)

    def test_blocked_address_is_never_opened(self) -> None:
        oeffner = mock.Mock()
        self.assertIsNone(lauf.seite_holen("http://intern.example/", aufloesen=_aufloesen("127.0.0.1"),
                                           oeffner=oeffner))
        oeffner.open.assert_not_called()

    def test_sources_are_deduplicated_and_cut(self) -> None:
        text = f"{SEITE_A}.\n- {SEITE_A}\n{SEITE_B}, und sonst nichts"
        self.assertEqual(lauf.quellen_aus_antwort(text), [SEITE_A, SEITE_B])
        self.assertEqual(lauf.quellen_aus_antwort(None), [])

    def test_html_becomes_text_without_scripts(self) -> None:
        roh = "<html><head><title>x</title><script>boese()</script></head><body><p>Bühnenplan</p></body></html>"
        self.assertEqual(lauf.text_aus_html(roh), "Bühnenplan")


class MainTests(unittest.TestCase):
    def test_main_writes_the_report_only_on_success(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as roh:
            ordner = Path(roh)
            anfrage = ordner / "research_job.request.json"
            anfrage.write_text(json.dumps({"message": BRIEF}), encoding="utf-8")
            bericht = ordner / "research_job.md"
            modell = _Modell(SEITE_A, f"Bericht nach {SEITE_A}")
            with mock.patch.object(lauf.Waechter, "laden", return_value=lauf.Waechter(_Modul())), \
                 mock.patch.object(lauf, "_openfang",
                                   return_value=(lambda pfad: [{"id": "a0000001", "name": "brain-researcher"}],
                                                 lambda kennung, text: modell(text))), \
                 mock.patch.object(lauf, "seite_holen", return_value="Text"):
                self.assertEqual(lauf.main([str(bericht), str(anfrage)], {"VIBEMIND_BUDGET_MODUL": "x"}), 0)
            self.assertEqual(bericht.read_text(encoding="utf-8"), f"Bericht nach {SEITE_A}")
            self.assertFalse(bericht.with_suffix(".teil").exists())

    def test_main_without_guard_writes_no_report(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as roh:
            ordner = Path(roh)
            anfrage = ordner / "research_job.request.json"
            anfrage.write_text(json.dumps({"message": BRIEF}), encoding="utf-8")
            bericht = ordner / "research_job.md"
            with mock.patch("sys.stderr") as fehler:
                self.assertEqual(lauf.main([str(bericht), str(anfrage)], {}), 1)
            self.assertFalse(bericht.exists())
            self.assertIn("budget_ohne_waechter", "".join(str(c) for c in fehler.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
