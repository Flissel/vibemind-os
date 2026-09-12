"""`main()` von `provision-oauth-token.py` -- der Schreibweg, den bisher KEIN
Test abdeckte (weder vor noch nach Aufgabe 6).

Fix-Runde 1, Befund 2: `token_holen -> tuple[str, str]` hatte keinen Platz
fuer `refresh_token`/`expires_in`/`scope`, darum schrieb `main()` die
`{name}_REFRESH=...`-Zeile nicht mehr, die es vor `7d0b796a` schrieb. Der
Fix haelt die Signatur von `token_holen` UNVERAENDERT (das ist der Einstieg,
den `fenster.oauth_entgegennehmen` via `server.py` benutzt) und zieht die
volle Token-Antwort in eine neue `token_holen_voll`, die `main()` stattdessen
ruft. Diese Datei prueft NUR `main()`s Schreibweg -- `token_holen_voll`
selbst macht kein Netzwerk und wird hier komplett monkeypatched.

`provision-oauth-token.py` liegt in `spaces/rowboat/rowboat/scripts/`, nicht
hier, und sein Dateiname ist per Bindestrich kein gueltiger Python-
Modulname -- geladen per `spec_from_file_location`, derselbe Kniff wie in
`server.py` (und in `spaces/rowboat/tests/test_openai_plugin_runtime_contract.py`).

Kein Netzwerk: `token_holen_voll` ist in jedem Test durch eine Lambda
ersetzt, die nur ein erfundenes `(name, tokens)`-Paar zurueckgibt. Alle
Werte offensichtlich erfunden (`offensichtlich-erfunden-...`).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_PLUGIN_SETUP_DIR = _TESTS_DIR.parent
_PROVISIONER_PATH = (_PLUGIN_SETUP_DIR.parent / "rowboat" / "rowboat" / "scripts"
                     / "provision-oauth-token.py")
_PROVISIONER_SPEC = importlib.util.spec_from_file_location(
    "provision_oauth_token_schreiben_test", _PROVISIONER_PATH)
assert _PROVISIONER_SPEC is not None and _PROVISIONER_SPEC.loader is not None
provisioner = importlib.util.module_from_spec(_PROVISIONER_SPEC)
_PROVISIONER_SPEC.loader.exec_module(provisioner)


def test_main_schreibt_beide_zeilen_wenn_der_anbieter_einen_refresh_token_ausgibt(
        monkeypatch, tmp_path, capsys):
    """Der Anbieter liefert `refresh_token`/`expires_in`/`scope` mit --
    `main()` muss BEIDE Zeilen schreiben: `NAME=<access_token>` und
    `NAME_REFRESH=<refresh_token>`."""
    tokens = {
        "access_token": "offensichtlich-erfunden-access-eins",
        "refresh_token": "offensichtlich-erfunden-refresh-eins",
        "expires_in": 3599,
        "scope": "offline_access read",
    }
    monkeypatch.setattr(
        provisioner, "token_holen_voll",
        lambda mcp_url: ("PLUGIN_SETUP_TEST_MIT_REFRESH", tokens))
    out_pfad = tmp_path / "x.env"
    monkeypatch.setattr(sys, "argv", [
        "provision-oauth-token.py", "https://mcp.example.com/mcp",
        "--out", str(out_pfad),
    ])

    provisioner.main()

    zeilen = out_pfad.read_text(encoding="utf-8").splitlines()
    assert zeilen == [
        "PLUGIN_SETUP_TEST_MIT_REFRESH=offensichtlich-erfunden-access-eins",
        "PLUGIN_SETUP_TEST_MIT_REFRESH_REFRESH=offensichtlich-erfunden-refresh-eins",
    ]

    ausgabe = capsys.readouterr().out
    assert "offensichtlich-erfunden-access-eins" not in ausgabe, (
        "der access_token-WERT darf in keiner gedruckten Zeile stehen")
    assert "offensichtlich-erfunden-refresh-eins" not in ausgabe, (
        "der refresh_token-WERT darf in keiner gedruckten Zeile stehen")


def test_main_schreibt_genau_eine_zeile_wenn_kein_refresh_token_da_ist(
        monkeypatch, tmp_path, capsys):
    """Derselbe Aufbau OHNE `refresh_token` in der Anbieter-Antwort -- die
    Datei enthaelt genau EINE Zeile, keine `_REFRESH`-Zeile."""
    tokens = {
        "access_token": "offensichtlich-erfunden-access-zwei",
        "expires_in": 120,
        "scope": "read",
    }
    monkeypatch.setattr(
        provisioner, "token_holen_voll",
        lambda mcp_url: ("PLUGIN_SETUP_TEST_NUR_ACCESS", tokens))
    out_pfad = tmp_path / "x.env"
    monkeypatch.setattr(sys, "argv", [
        "provision-oauth-token.py", "https://mcp.example.com/mcp",
        "--out", str(out_pfad),
    ])

    provisioner.main()

    zeilen = out_pfad.read_text(encoding="utf-8").splitlines()
    assert zeilen == ["PLUGIN_SETUP_TEST_NUR_ACCESS=offensichtlich-erfunden-access-zwei"]
    assert len(zeilen) == 1, "ohne refresh_token darf keine zweite Zeile entstehen"

    ausgabe = capsys.readouterr().out
    assert "offensichtlich-erfunden-access-zwei" not in ausgabe, (
        "der access_token-WERT darf in keiner gedruckten Zeile stehen")
