"""Macht die Testsuite unabhaengig vom Arbeitsverzeichnis.

`tests/` hat bewusst kein `__init__.py`. Damit setzt pytest fuer jedes
Testmodul dessen Basisverzeichnis auf sys.path - also `tests/` -, nicht die
Projektwurzel. Dass `from tests.fixtures.make_audio import ...` trotzdem
funktioniert, lag allein daran, dass `python -m pytest` das aktuelle
Verzeichnis vorn an sys.path stellt: aus der Projektwurzel gestartet ging es,
aus jedem anderen Verzeichnis (oder ueber den blossen Aufruf `pytest`) nicht.

Eine conftest.py in der Wurzel behebt das doppelt: pytest laedt sie vor allen
Testmodulen und legt dabei ihr Verzeichnis auf sys.path, und der Eintrag
unten schreibt es zusaetzlich ausdruecklich hin. Das zweite ist unter dem
Standard-Importmodus redundant, unter --import-mode=importlib aber der
einzige Mechanismus, der bleibt.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
