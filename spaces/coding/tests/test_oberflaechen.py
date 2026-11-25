"""Knoepfe der Seite: Adressen, Antwort, Rahmen-Erlaubnis."""

from __future__ import annotations

import socket

from spaces.coding import dienst, oberflaechen


def test_vorgaben_und_reihenfolge(monkeypatch) -> None:
    for variable in ("CODING_OBERFLAECHE_VORSCHAU", "CODING_OBERFLAECHE_VIBE", "CODING_OBERFLAECHE_ENGINE"):
        monkeypatch.delenv(variable, raising=False)
    namen = [e["name"] for e in oberflaechen.adressen()]
    assert namen == ["vorschau", "vibe", "engine"]
    assert oberflaechen.adressen()[0]["url"].startswith("http://localhost:6090/vnc.html")


def test_nur_lokale_adressen(monkeypatch) -> None:
    monkeypatch.setenv("CODING_OBERFLAECHE_VIBE", "http://127.0.0.1:5199/")
    monkeypatch.setenv("CODING_OBERFLAECHE_ENGINE", "https://fremd.example:443/")
    vibe, engine = oberflaechen.adressen()[1:]
    assert vibe["url"] == "http://127.0.0.1:5199/"
    assert engine["url"] == "http://localhost:5180/engine"
    assert "fremd.example" not in oberflaechen.rahmen_quellen()


def test_antwortet_erkennt_offenen_port() -> None:
    lauscher = socket.socket()
    lauscher.bind(("127.0.0.1", 0))
    lauscher.listen(1)
    port = lauscher.getsockname()[1]
    try:
        assert oberflaechen.antwortet(f"http://127.0.0.1:{port}/") is True
    finally:
        lauscher.close()
    assert oberflaechen.antwortet(f"http://127.0.0.1:{port}/") is False
    assert oberflaechen.antwortet("http://fremd.example:80/") is False


def test_api_liefert_stand(monkeypatch) -> None:
    monkeypatch.setattr(oberflaechen, "antwortet", lambda url, warten=0.3: url.startswith("http://localhost:6090"))
    status, art, koerper = dienst.lesen("/api/oberflaechen")
    assert status == 200 and art.startswith("application/json")
    import json
    daten = {e["name"]: e for e in json.loads(koerper)}
    assert daten["vorschau"]["laeuft"] is True and daten["vibe"]["laeuft"] is False
    assert daten["vibe"]["satz_aus"]
