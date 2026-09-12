"""Die Werkzeugliste IST die Sicherheitsgrenze -- also wird sie geprueft.

Review Runde 3 (Important): die urspruengliche Fassung dieser Datei pruefte
nur das `WERKZEUGE`-Tupel, nie das, was der FastMCP-Server TATSAECHLICH
registriert. Ein `server.tool()(werkzeuge.schluessel_entgegennehmen)`
irgendwo ausserhalb der Schleife in `_baue_server()` haette das Tupel gar
nicht beruehrt und waere hier unentdeckt geblieben; ebenso ein `wert`-
Parameter, der einem der fuenf Werkzeuge angehaengt wird. Beide Luecken
sind jetzt eigene Tests.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402

_ERWARTETE_NAMEN = {
    "plugin_bedarf",
    "eingabe_anfordern",
    "einrichtung_status",
    "plugin_installieren",
    "plugin_werkzeug_binden",
}


def test_der_agent_sieht_genau_fuenf_werkzeuge():
    namen = {fn.__name__ for fn in server.WERKZEUGE}
    assert namen == _ERWARTETE_NAMEN


def test_schluessel_entgegennehmen_ist_kein_werkzeug_mehr():
    """Der Kern dieses ganzen Entwurfs: es gibt keinen Weg mehr, ueber den
    ein Agent einen Wert uebergeben koennte. Die Funktion existiert
    weiterhin -- als interner Schreibweg des Formulars."""
    namen = {fn.__name__ for fn in server.WERKZEUGE}
    assert "schluessel_entgegennehmen" not in namen
    import werkzeuge
    assert callable(werkzeuge.schluessel_entgegennehmen)


def test_der_server_registriert_tatsaechlich_genau_fuenf_werkzeuge():
    """Prueft die ECHTE Registrierung auf einem gebauten FastMCP-Server,
    nicht nur das `WERKZEUGE`-Tupel -- ein Aufruf von `server.tool()(...)`
    ausserhalb der Schleife in `_baue_server()` wuerde nur hier auffallen."""
    s = server._baue_server()
    registriert = {t.name for t in s._tool_manager.list_tools()}
    assert registriert == _ERWARTETE_NAMEN


def test_kein_registriertes_werkzeug_hat_einen_wert_parameter():
    """Selbst wenn der Name eines der fuenf Werkzeuge staende: ein
    `wert`-Parameter an irgendeinem von ihnen waere derselbe verbotene
    Schreibweg unter neuem Namen."""
    s = server._baue_server()
    for t in s._tool_manager.list_tools():
        params = inspect.signature(t.fn).parameters
        assert "wert" not in params, f"{t.name} hat einen wert-Parameter"


def test_kein_werkzeug_im_tupel_hat_einen_wert_parameter():
    for fn in server.WERKZEUGE:
        params = inspect.signature(fn).parameters
        assert "wert" not in params, f"{fn.__name__} hat einen wert-Parameter"
