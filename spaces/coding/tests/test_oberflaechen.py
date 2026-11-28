"""Knoepfe der Seite: Adressen, Antwort, Rahmen-Erlaubnis."""

from __future__ import annotations

import socket

from spaces.coding import dienst, oberflaechen


def test_nur_die_vorschau_ist_ein_rahmen(monkeypatch) -> None:
    monkeypatch.delenv("CODING_OBERFLAECHE_VORSCHAU", raising=False)
    namen = [e["name"] for e in oberflaechen.adressen()]
    # Vibe-Coder und Engine sind keine Rahmen auf :5180 mehr (P6 a); die fremde Oberflaeche ist nicht eingebunden.
    assert namen == ["vorschau"]
    assert oberflaechen.adressen()[0]["url"].startswith("http://localhost:6090/vnc_lite.html")
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
    assert daten["vorschau"]["laeuft"] is False and daten["vorschau"]["satz_aus"] == oberflaechen.KEINE
    assert daten["vorschau"]["hinweis"] and daten["vorschau"]["auftrag"] is None
    assert set(daten) == {"vorschau"}


def _sandbox(monkeypatch, ordner, laeuft=True) -> None:
    monkeypatch.setattr(oberflaechen, "antwortet", lambda url, warten=0.3: True)
    monkeypatch.setattr(oberflaechen.vorschau, "lage",
                        lambda: {"laeuft": laeuft, "gesund": "healthy", "ordner": str(ordner) if ordner else None})


def _auftrag(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("CODING_PIPELINE_DIR", str(tmp_path / "auftraege"))
    monkeypatch.setenv("CODING_WORKSPACE_ROOT", str(tmp_path / "arbeit"))
    from spaces.coding import pipeline
    out = pipeline.arbeitswurzel() / "c-test-0001" / "out"
    out.mkdir(parents=True)
    pipeline.auftragsordner().mkdir(parents=True, exist_ok=True)
    (pipeline.auftragsordner() / "c-test-0001.json").write_text(json.dumps({
        "id": "c-test-0001", "art": "engine", "titel": "Rechner reparieren", "angelegt": 1.0, "zustand": "fertig",
        "projektordner": str(out.parent), "zielordner": str(out), "pakete": []}), encoding="utf-8")
    return out


def test_vorschau_eines_bekannten_laufs_nennt_ihn(tmp_path, monkeypatch) -> None:
    out = _auftrag(tmp_path, monkeypatch)
    _sandbox(monkeypatch, out)
    eintrag = oberflaechen.stand()[0]
    assert eintrag["laeuft"] is True and eintrag["auftrag"] == {"id": "c-test-0001", "titel": "Rechner reparieren"}
    assert "vnc_lite.html" in eintrag["url"] and "vnc.html" not in eintrag["url"]


def test_sandbox_mit_fremdem_ordner_ist_keine_vorschau(tmp_path, monkeypatch) -> None:
    _auftrag(tmp_path, monkeypatch)
    _sandbox(monkeypatch, tmp_path / "pytest-rest" / "out")
    eintrag = oberflaechen.stand()[0]
    assert eintrag["laeuft"] is False and eintrag["auftrag"] is None
    assert "keine App eines Laufs" in eintrag["hinweis"]


def test_ohne_laufende_sandbox_karte_mit_hinweis(tmp_path, monkeypatch) -> None:
    _auftrag(tmp_path, monkeypatch)
    _sandbox(monkeypatch, None, laeuft=False)
    eintrag = oberflaechen.stand()[0]
    assert eintrag["laeuft"] is False and eintrag["satz_aus"] == "Gerade läuft keine Vorschau."
    assert "Autostart" in eintrag["hinweis"]
    monkeypatch.setenv("CODING_VORSCHAU_AUTOSTART", "1")
    assert "Abnahme besteht" in oberflaechen.stand()[0]["hinweis"]
