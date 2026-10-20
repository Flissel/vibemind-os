"""Gemeinsame pytest-Einstellungen der Marketing-Tests."""


def pytest_configure(config):
    # Tests mit echtem headless Chromium (z. B. Pruefbild-Rauchtest); ohne Chromium ueberspringen sie sich selbst.
    config.addinivalue_line("markers", "browser: braucht ein echtes headless Chromium (Playwright)")
