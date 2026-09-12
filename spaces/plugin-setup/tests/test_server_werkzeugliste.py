"""Die Werkzeugliste IST die Sicherheitsgrenze -- also wird sie geprueft."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402


def test_der_agent_sieht_genau_fuenf_werkzeuge():
    namen = {fn.__name__ for fn in server.WERKZEUGE}
    assert namen == {
        "plugin_bedarf",
        "eingabe_anfordern",
        "einrichtung_status",
        "plugin_installieren",
        "plugin_werkzeug_binden",
    }


def test_schluessel_entgegennehmen_ist_kein_werkzeug_mehr():
    """Der Kern dieses ganzen Entwurfs: es gibt keinen Weg mehr, ueber den
    ein Agent einen Wert uebergeben koennte. Die Funktion existiert
    weiterhin -- als interner Schreibweg des Formulars."""
    namen = {fn.__name__ for fn in server.WERKZEUGE}
    assert "schluessel_entgegennehmen" not in namen
    import werkzeuge
    assert callable(werkzeuge.schluessel_entgegennehmen)
