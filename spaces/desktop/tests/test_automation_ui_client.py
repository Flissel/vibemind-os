from __future__ import annotations

import unittest

from spaces.desktop.automation_ui_client import AutomationUIClient, get_automation_client


class AutomationUIClientTests(unittest.TestCase):
    def test_the_import_the_app_uses_works_and_returns_one_client(self) -> None:
        self.assertIs(get_automation_client(), get_automation_client())

    def test_an_unreachable_service_is_not_available(self) -> None:
        self.assertFalse(AutomationUIClient("http://127.0.0.1:9").is_available(timeout=0.5))


if __name__ == "__main__":
    unittest.main()
