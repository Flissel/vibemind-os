"""Macht `spaces/plugin-setup/` importierbar, damit Tests hier `import
pruefung` (und Geschwister-Module) ohne Paket-Installation nutzen koennen.
Beruehrt nichts an test_eingang.py, das keine Imports braucht."""
from __future__ import annotations

import sys
from pathlib import Path

_PLUGIN_SETUP_DIR = Path(__file__).resolve().parents[1]
if str(_PLUGIN_SETUP_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SETUP_DIR))
