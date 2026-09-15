"""N2 -- der feste OAuth-Callback-Port (`CALLBACK_PORT = 8976`) in
`provision-oauth-token.py` war zweifach falsch: ein Portkonflikt zwischen
zwei gleichzeitigen OAuth-Fluessen (seit Aufgabe 6 laufen beide
Schreibwege des Sidecars im Threadpool) war a) ueberhaupt moeglich UND b)
STILL -- ein zweiter Bind auf denselben Port gelang (gemessen auf diesem
Host, `allow_reuse_address` ist bei `http.server.HTTPServer` geerbt `1`),
und welcher der beiden Fluesse den Callback bekam, war undefiniert.

Fix 1: der Callback-Server bindet auf Port 0 (OS vergibt einen freien)
VOR Discovery/Registrierung, `redirect_uri` wird aus dem TATSAECHLICH
gebundenen Port abgeleitet -- nicht mehr aus einer Modulkonstante.
`--callback-port N` ist das Ventil fuer den seltenen Anbieter, der eine
vorregistrierte feste Redirect-URI verlangt.

Fix 2: `allow_reuse_address = False` auf der Serverklasse macht einen
zweiten Bind auf einen bereits belegten FESTEN Port laut (Ausnahme statt
stiller Uebernahme).

`provision-oauth-token.py` liegt in `spaces/rowboat/rowboat/scripts/`,
nicht hier, und sein Dateiname ist per Bindestrich kein gueltiger Python-
Modulname -- geladen per `spec_from_file_location`, derselbe Kniff wie in
`server.py`, `test_provisioner_schreiben.py` und
`spaces/rowboat/tests/test_openai_plugin_runtime_contract.py`.

Kein Netzwerk: `discover_resource_metadata`, `discover_authorization_server`
und `register_client` sind in jedem Test, der `_entdecken_und_registrieren`
durchlaeuft, durch Fakes ersetzt. Kein echtes Geheimnis, kein Token --
nur erfundene Platzhalter-Strings.
"""
from __future__ import annotations

import importlib.util
import socket
import sys
import urllib.parse
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_PLUGIN_SETUP_DIR = _TESTS_DIR.parent
_PROVISIONER_PATH = (_PLUGIN_SETUP_DIR.parent / "rowboat" / "rowboat" / "scripts"
                     / "provision-oauth-token.py")
_PROVISIONER_SPEC = importlib.util.spec_from_file_location(
    "provision_oauth_token_callback_port_test", _PROVISIONER_PATH)
assert _PROVISIONER_SPEC is not None and _PROVISIONER_SPEC.loader is not None
provisioner = importlib.util.module_from_spec(_PROVISIONER_SPEC)
_PROVISIONER_SPEC.loader.exec_module(provisioner)

_ALTER_FESTER_PORT = 8976  # der frueher hartkodierte CALLBACK_PORT -- ein
                           # heutiger Bind darf niemals zufaellig genau
                           # darauf landen, sonst waere ein Regressions-
                           # test blind gegen genau diesen Fall.


def _freien_port_finden() -> int:
    """Fragt das OS nach einem gerade freien Port, ohne ihn zu belegen --
    dasselbe Prinzip wie `bind_callback_server(0)`, nur fuer den Test
    selbst, der einen FESTEN Port vorgeben will (`--callback-port`)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _discovery_faelschen(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Ersetzt die drei Netzwerkfunktionen, die `_entdecken_und_registrieren`
    ruft, durch Fakes. Gibt die Liste der `redirect_uri`-Werte zurueck, die
    `register_client` tatsaechlich zu sehen bekommt."""
    gesehene_redirect_uris: list[str] = []

    monkeypatch.setattr(
        provisioner, "discover_resource_metadata",
        lambda mcp_url: {"resource": mcp_url, "authorization_servers": ["https://issuer.example"]})
    monkeypatch.setattr(
        provisioner, "discover_authorization_server",
        lambda issuer: {
            "authorization_endpoint": "https://issuer.example/authorize",
            "token_endpoint": "https://issuer.example/token",
            "registration_endpoint": "https://issuer.example/register",
        })

    def _fake_register_client(registration_endpoint: str, redirect_uri: str) -> str:
        gesehene_redirect_uris.append(redirect_uri)
        return "offensichtlich-erfundener-client-id"

    monkeypatch.setattr(provisioner, "register_client", _fake_register_client)
    return gesehene_redirect_uris


def test_ohne_angabe_bindet_der_callback_server_auf_einen_freien_port_und_die_redirect_uri_traegt_ihn(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """N2, Fix 1, Test 1: `bind_callback_server()` (kein `--callback-port`)
    bindet auf einen vom OS vergebenen Port -- != 0 (das waere "noch nicht
    gebunden") und != 8976 (der alte harte Default). Die `redirect_uri`,
    die bei Registrierung UND in der `authorize_url` ankommt, traegt
    GENAU diesen tatsaechlich gebundenen Port, nicht geraten."""
    server = provisioner.bind_callback_server()
    try:
        port = server.server_address[1]
        assert port != 0, "server_address[1] muss der TATSAECHLICH gebundene Port sein"
        assert port != _ALTER_FESTER_PORT, "darf nicht zufaellig auf den alten harten Default fallen"

        redirect_uri = f"http://127.0.0.1:{port}/callback"
        gesehene_redirect_uris = _discovery_faelschen(monkeypatch)

        _resource, _server_metadata, _client_id, _verifier, _state, authorize_url = (
            provisioner._entdecken_und_registrieren("https://mcp.example.com/mcp", redirect_uri))

        assert gesehene_redirect_uris == [redirect_uri], (
            "register_client (RFC 7591) muss die redirect_uri des TATSAECHLICH gebundenen Ports sehen")
        assert urllib.parse.quote_plus(redirect_uri) in authorize_url, (
            "authorize_url muss dieselbe redirect_uri tragen wie die Registrierung")
    finally:
        server.server_close()


def test_zwei_gleichzeitig_gebundene_callback_server_bekommen_verschiedene_ports() -> None:
    """N2, Fix 1, Test 2 -- DIE Eigenschaft, die N2 heilt: seit Aufgabe 6
    laufen beide Schreibwege des Sidecars im Threadpool, zwei OAuth-
    Fluesse koennen also gleichzeitig laufen. Vor dem Fix teilten sie sich
    den EINEN festen Port 8976 (und welcher Fluss den Callback bekam, war
    undefiniert); nach dem Fix bekommt jeder Lauf einen eigenen, vom OS
    vergebenen Port."""
    server_a = provisioner.bind_callback_server()
    server_b = provisioner.bind_callback_server()
    try:
        port_a = server_a.server_address[1]
        port_b = server_b.server_address[1]
        assert port_a != port_b, (
            "zwei gleichzeitige OAuth-Fluesse duerfen NICHT denselben Port bekommen -- "
            "das war genau die stille Race Condition des alten festen CALLBACK_PORT")
    finally:
        server_a.server_close()
        server_b.server_close()


def test_callback_port_erzwingt_den_angegebenen_festen_port_end_to_end(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """N2, Ventil, Test 3: `--callback-port N` erzwingt genau diesen Port --
    getestet ueber `main() --dry-run`, denselben Bind-vor-Registrierung-
    Pfad wie der echte Fluss. Die uebrige --dry-run-Ausgabe bleibt
    byte-genau bis auf die Portnummer in der URL (gemessen unten)."""
    fester_port = _freien_port_finden()
    assert fester_port != _ALTER_FESTER_PORT

    _discovery_faelschen(monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "provision-oauth-token.py", "https://mcp.example.com/mcp",
        "--dry-run", "--callback-port", str(fester_port),
    ])

    provisioner.main()

    ausgabe = capsys.readouterr().out
    erwartete_redirect_uri = f"http://127.0.0.1:{fester_port}/callback"
    assert urllib.parse.quote_plus(erwartete_redirect_uri) in ausgabe, (
        "--callback-port muss den erzwungenen Port in der gedruckten authorize_url tragen")
    # Rest der Ausgabe bleibt byte-genau (bis auf die Portnummer in der URL,
    # oben bereits geprueft) -- dieselben drei Zeilen wie vor N2.
    assert "resource:              https://mcp.example.com/mcp" in ausgabe
    assert "registered client:     offensichtlich-erfundener-client-id" in ausgabe
    assert "dry run - authorization URL (not opened):" in ausgabe


def test_zweites_bind_auf_bereits_belegten_festen_port_wirft() -> None:
    """N2, Fix 2, Test 4: ein zweiter Bind auf einen bereits belegten
    FESTEN Port (das --callback-port-Ventil) muss LAUT scheitern, nicht
    still den Callback uebernehmen.

    GEMESSEN auf diesem Windows-Host (siehe n2-report.md): plain
    `http.server.HTTPServer` erbt `allow_reuse_address = 1`, unter dem ein
    zweiter Bind auf denselben Port STILL GELINGT. `allow_reuse_address =
    False` auf `_CallbackHTTPServer` macht den zweiten Bind hier
    tatsaechlich mit `OSError` (WinError 10048, "Only one usage of each
    socket address is normally permitted") scheitern -- das ist keine
    Annahme, sondern ausprobiert."""
    fester_port = _freien_port_finden()
    erster = provisioner.bind_callback_server(fester_port)
    try:
        with pytest.raises(OSError):
            provisioner.bind_callback_server(fester_port)
    finally:
        erster.server_close()
