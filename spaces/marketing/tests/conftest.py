"""Gemeinsame pytest-Einstellungen der Marketing-Tests."""


def pytest_configure(config):
    # Tests mit echtem headless Chromium (z. B. Pruefbild-Rauchtest); ohne Chromium ueberspringen sie sich selbst.
    config.addinivalue_line("markers", "browser: braucht ein echtes headless Chromium (Playwright)")


import sys  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _collage_fehler_vergessen():
    """seite_mail_dienst merkt Inhaltsfehler je (Inhalt, Fassung) im Prozess - jeder Test beginnt ohne."""
    def leeren():
        dienst = sys.modules.get("spaces.marketing.api.seite_mail_dienst")
        if dienst is not None:
            dienst._fehler.clear()
    leeren()
    yield
    leeren()
