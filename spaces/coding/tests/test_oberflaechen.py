"""Knoepfe der Seite: Adressen, Antwort, Rahmen-Erlaubnis."""

from __future__ import annotations

import socket

from spaces.coding import dienst, oberflaechen


def test_nur_die_vorschau_ist_ein_rahmen(monkeypatch) -> None:
    monkeypatch.delenv("CODING_OBERFLAECHE_VORSCHAU", raising=False)
    namen = [e["name"] for e in oberflaechen.adressen()]
    # Vibe-Coder und Engine sind keine Rahmen auf :5180 mehr (P6 a); die fremde Oberflaeche ist nicht eingebunden.
    assert namen == ["vorschau"]
    assert oberflaechen.adressen()[0]["url"].startswith("http://localhost:6090/vnc.html")
    assert "5180" not in oberflaechen.rahmen_quellen()


def test_nur_lokale_adressen(monkeypatch) -> None:
    monkeypatch.setenv("CODING_OBERFLAECHE_VORSCHAU", "http://127.0.0.1:6099/vnc.html")
    assert oberflaechen.adressen()[0]["url"] == "http://127.0.0.1:6099/vnc.html"
    monkeypatch.setenv("CODING_OBERFLAECHE_VORSCHAU", "https://fremd.example:443/")
    assert oberflaechen.adressen()[0]["url"].startswith("http://localhost:6090/")
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
    monkeypatch.delenv("CODING_OBERFLAECHE_VORSCHAU", raising=False)
    monkeypatch.setattr(oberflaechen, "antwortet", lambda url, warten=0.3: False)
    status, art, koerper = dienst.lesen("/api/oberflaechen")
    assert status == 200 and art.startswith("application/json")
    import json
    daten = {e["name"]: e for e in json.loads(koerper)}
    assert daten["vorschau"]["laeuft"] is False and daten["vorschau"]["satz_aus"]
    assert set(daten) == {"vorschau"}
