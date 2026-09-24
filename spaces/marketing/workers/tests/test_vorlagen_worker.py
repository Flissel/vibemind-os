"""Tests fuer den Vorlagen-Arbeiter (Task 6, Terminkarten).

Deckt Brief-Step-1 (vier Grundfaelle) plus Controller-Ruling 22 ab:
  1) entwerfen()/vorlegen duerfen den Auftrag nie in_arbeit stehenlassen -
     JEDE Ausnahme wird abgefangen und stellt zurueck (deutscher Fehlertext
     mit dem Ausnahmetyp).
  3) ein_durchlauf ruft marketing.vorlagenauftraege_wiederaufnehmen('15
     minutes') zuerst auf, VOR vorlagenauftrag_uebernehmen().
"""
import unittest

from spaces.marketing.workers import vorlagen_worker as w


class _Db:
    def __init__(self, auftrag, vorlegen_ok=True):
        self.auftrag, self.vorlegen_ok, self.sql = auftrag, vorlegen_ok, []

    def _sql_literal(self, s):
        return "'" + str(s).replace("'", "''") + "'"

    def query_via_docker(self, sql, *a, **k):
        self.sql.append(sql)
        return [self.auftrag] if self.auftrag else []

    def query_one(self, sql, *a, **k):
        self.sql.append(sql)
        if "vorlagenauftrag_vorlegen" in sql:
            return {"ergebnis": {"ok": self.vorlegen_ok, "grund": "liegt nicht auf der Seite"}}
        return {"ergebnis": {"ok": True, "status": "neu"}}


AUFTRAG = {"id": "a1", "art": "terminkarte", "runde": 1, "bild_b64": None, "bild_typ": None,
           "beschreibung": "Kunde, Datum", "anmerkung": "", "rueckmeldungen": []}


class Durchlauf(unittest.TestCase):
    def test_ohne_auftrag_passiert_nichts_und_kein_modellaufruf(self):
        aufgerufen = []
        self.assertEqual(w.ein_durchlauf(_Db(None), lambda a: aufgerufen.append(a)), "leer")
        self.assertEqual(aufgerufen, [])

    def test_gelungener_entwurf_wird_vorgelegt(self):
        db = _Db(AUFTRAG)
        self.assertEqual(w.ein_durchlauf(db, lambda a: ({"felder": []}, "")), "vorgelegt")
        self.assertTrue(any("vorlagenauftrag_vorlegen" in s for s in db.sql))

    def test_fehlgeschlagener_entwurf_wird_zurueckgestellt(self):
        db = _Db(AUFTRAG)
        self.assertEqual(w.ein_durchlauf(db, lambda a: (None, "kein JSON")), "zurueckgestellt")
        self.assertTrue(any("vorlagenauftrag_zurueckstellen" in s for s in db.sql))

    def test_von_der_datenbank_abgewiesene_gestalt_wird_zurueckgestellt(self):
        db = _Db(AUFTRAG, vorlegen_ok=False)
        self.assertEqual(w.ein_durchlauf(db, lambda a: ({"felder": []}, "")), "zurueckgestellt")
        self.assertTrue(any("liegt nicht auf der Seite" in s for s in db.sql))


class Ruling22(unittest.TestCase):
    """Controller ruling 22 (2026-09-24, Task 6): siehe Modul-Docstring."""

    def test_entwerfen_wirft_ausnahme_wird_zurueckgestellt(self):
        def entwerfen_kaputt(_auftrag):
            raise RuntimeError("claude -p ist abgestuerzt")

        db = _Db(AUFTRAG)
        self.assertEqual(w.ein_durchlauf(db, entwerfen_kaputt), "zurueckgestellt")
        zurueckstellen_sql = [s for s in db.sql if "vorlagenauftrag_zurueckstellen" in s]
        self.assertTrue(zurueckstellen_sql)
        # Deutscher Fehlertext nennt den Ausnahmetyp - sonst bleibt der Auftrag
        # bei einer unerwarteten Ausnahme fuer immer auf in_arbeit stehen.
        self.assertTrue(any("RuntimeError" in s for s in zurueckstellen_sql))

    def test_vorlegen_aufruf_wirft_ausnahme_wird_ebenfalls_zurueckgestellt(self):
        class _DbKaputterVorlegenAufruf(_Db):
            def query_one(self, sql, *a, **k):
                self.sql.append(sql)
                if "vorlagenauftrag_vorlegen" in sql:
                    raise ConnectionError("psql failed: Verbindung weg")
                return {"ergebnis": {"ok": True, "status": "neu"}}

        db = _DbKaputterVorlegenAufruf(AUFTRAG)
        self.assertEqual(w.ein_durchlauf(db, lambda a: ({"felder": []}, "")), "zurueckgestellt")
        zurueckstellen_sql = [s for s in db.sql if "vorlagenauftrag_zurueckstellen" in s]
        self.assertTrue(zurueckstellen_sql)
        self.assertTrue(any("ConnectionError" in s for s in zurueckstellen_sql))

    def test_wiederaufnehmen_wird_vor_uebernehmen_aufgerufen(self):
        db = _Db(AUFTRAG)
        w.ein_durchlauf(db, lambda a: ({"felder": []}, ""))
        self.assertTrue(db.sql, "kein SQL ausgefuehrt")
        self.assertIn("vorlagenauftraege_wiederaufnehmen", db.sql[0])
        self.assertNotIn("vorlagenauftrag_uebernehmen", db.sql[0])
        # ...und erst danach uebernehmen().
        uebernehmen_stellen = [i for i, s in enumerate(db.sql) if "vorlagenauftrag_uebernehmen" in s]
        wiederaufnehmen_stellen = [i for i, s in enumerate(db.sql) if "vorlagenauftraege_wiederaufnehmen" in s]
        self.assertTrue(uebernehmen_stellen and wiederaufnehmen_stellen)
        self.assertLess(min(wiederaufnehmen_stellen), min(uebernehmen_stellen))

    def test_wiederaufnehmen_auch_ohne_wartenden_auftrag_aufgerufen(self):
        db = _Db(None)
        w.ein_durchlauf(db, lambda a: ({"felder": []}, ""))
        self.assertTrue(any("vorlagenauftraege_wiederaufnehmen" in s for s in db.sql))


if __name__ == "__main__":
    unittest.main()
