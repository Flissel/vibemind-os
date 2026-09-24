"""Unit tests for spaces.marketing.sync._db's psql argv construction.

Fix round 1 (Task 6, 2026-09-24-terminkarten, Ruling 1): `streng=True` must
add `-v ON_ERROR_STOP=1` to the psql invocation, so an SQL-level error makes
psql exit non-zero instead of the previous silent exit-0/empty-stdout. The
default (streng=False) must stay exactly as before — every other `_db`
caller relies on today's lenient behaviour. No real psql/docker call here,
only the argv that would be built.
"""
import unittest

from spaces.marketing.sync import _db


class PsqlArgv(unittest.TestCase):
    def test_default_hat_kein_on_error_stop(self):
        argv = _db._psql_argv("mein-container")
        self.assertNotIn("ON_ERROR_STOP=1", argv)

    def test_streng_setzt_on_error_stop(self):
        argv = _db._psql_argv("mein-container", streng=True)
        self.assertIn("-v", argv)
        self.assertIn("ON_ERROR_STOP=1", argv)
        # -v muss unmittelbar vor dem Wert stehen, sonst liest psql ihn nicht
        # als dessen Argument.
        self.assertEqual(argv[argv.index("-v") + 1], "ON_ERROR_STOP=1")

    def test_streng_false_explizit_verhaelt_sich_wie_default(self):
        self.assertEqual(_db._psql_argv("c", streng=False), _db._psql_argv("c"))


if __name__ == "__main__":
    unittest.main()
