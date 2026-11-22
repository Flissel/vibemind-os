"""Werkzeug-Dienst ueber den echten HTTP-Weg (Starlette-TestClient, rohe JSON-RPC-POSTs)."""
from __future__ import annotations

import hashlib
import io
import itertools
import json
import logging
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from starlette.testclient import TestClient

from spaces.auftragsbuch import mcp_server
from spaces.auftragsbuch.db import Abgelehnt
from spaces.auftragsbuch.vertrag import lade_auftragsarten

ARTEN = lade_auftragsarten(Path(__file__).resolve().parents[2] / "_contract")
SCHLUESSEL = "geheim-123"
HASH = hashlib.sha256(SCHLUESSEL.encode()).hexdigest()
BASIS = "http://127.0.0.1:8172"
EINGABE = {"zeitraum": "heute", "entwuerfe_anlegen": True}
_IDS = itertools.count(1)


class FalschesBuch:
    def __init__(self) -> None:
        self.aufrufe = 0
        self.angelegt: list[tuple] = []
        self.fehler: Exception | None = None

    def anlegen(self, an, art, version, eingabe, plan_id, frist_min):
        self.aufrufe += 1
        if self.fehler is not None:
            raise self.fehler
        self.angelegt.append((an, art, version, eingabe, plan_id, frist_min))
        return str(uuid.uuid4())

    def lesen(self, auftrag_id):
        self.aufrufe += 1
        return None

    def probe_erledigen(self, auftrag_id):
        self.aufrufe += 1
        return True


@pytest.fixture
def buch() -> FalschesBuch:
    return FalschesBuch()


@pytest.fixture
def client(buch):
    with TestClient(mcp_server.app_bauen(buch, ARTEN, HASH, []), base_url=BASIS) as c:
        yield c


def _rpc(client, key, methode, params=None, *, header=None):
    kopf = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    if header is not None:
        kopf["Authorization"] = header
    elif key is not None:
        kopf["Authorization"] = f"Bearer {key}"
    nachricht: dict[str, Any] = {"jsonrpc": "2.0", "id": next(_IDS), "method": methode}
    if params is not None:
        nachricht["params"] = params
    return client.post("/mcp", headers=kopf, content=json.dumps(nachricht))


def _aufruf(client, name, argumente):
    antwort = _rpc(client, SCHLUESSEL, "tools/call", {"name": name, "arguments": argumente})
    assert antwort.status_code == 200, antwort.text
    return antwort.json()["result"]


def test_ohne_schluessel_401(client, buch, caplog):
    caplog.set_level(logging.INFO)
    for header in (None, "", "Bearer", "Bearer ", "Basic abc"):
        antwort = _rpc(client, None, "tools/call", {"name": "auftrag_probe", "arguments": {}}, header=header)
        assert antwort.status_code == 401, header
        assert antwort.headers["www-authenticate"].startswith("Bearer")
        assert "result" not in antwort.text
    assert buch.aufrufe == 0


def test_falscher_schluessel_401(client, buch, caplog):
    caplog.set_level(logging.INFO)
    for falsch in ("geheim-124", "Geheim-123", HASH):
        antwort = _rpc(client, falsch, "tools/call", {"name": "auftrag_probe", "arguments": {}})
        assert antwort.status_code == 401
        assert falsch not in antwort.text and falsch not in caplog.text
    assert buch.aufrufe == 0
    assert SCHLUESSEL not in caplog.text


def test_richtiger_schluessel_geht(client):
    assert _rpc(client, SCHLUESSEL, "tools/list", {}).status_code == 200


def test_gesund_ohne_schluessel(client):
    assert client.get("/gesund").json() == {"ok": True}


def test_tools_list_zeigt_den_katalog(client):
    antwort = _rpc(client, SCHLUESSEL, "tools/list", {})
    assert [t["name"] for t in antwort.json()["result"]["tools"]] == [
        "auftrag_probe", "auftrag_lesen", "rowboat_vorschlaege_anlegen", "sales_post_pruefen"]


def test_erfolg_gibt_json_text(client, buch):
    r = _aufruf(client, "sales_post_pruefen", {"eingabe": EINGABE, "plan_id": "plan_1"})
    assert r["isError"] is False
    nutz = json.loads(r["content"][0]["text"])
    assert nutz["stand"] == "offen" and nutz["an"] == "sales"
    assert buch.angelegt == [("sales", "post_pruefen", 1, EINGABE, "plan_1", 15)]


def test_form_fehler_ohne_wert(client, buch):
    r = _aufruf(client, "sales_post_pruefen", {"eingabe": {"zeitraum": "gestern-xyz", "entwuerfe_anlegen": True}})
    assert r["isError"] is True
    text = r["content"][0]["text"]
    assert text.startswith("Eingabe passt nicht zum Vertrag") and "zeitraum" in text
    assert "gestern-xyz" not in text and buch.aufrufe == 0


def test_abgelehnt_gibt_den_festen_text(client, buch):
    buch.fehler = Abgelehnt("Unbekannte Auftragsart")
    r = _aufruf(client, "sales_post_pruefen", {"eingabe": EINGABE})
    assert r["isError"] is True and r["content"][0]["text"] == "Unbekannte Auftragsart"


def test_unbekannt_ist_nicht_gefunden(client):
    for name, args in (("sales_alles_loeschen", {}), ("auftrag_lesen", {"id": str(uuid.uuid4())})):
        r = _aufruf(client, name, args)
        assert r["isError"] is True and r["content"][0]["text"] == "nicht gefunden"


def test_datenbank_weg_ist_interner_fehler(client, buch, caplog):
    caplog.set_level(logging.INFO)
    buch.fehler = psycopg.OperationalError("connection to server at 10.0.0.9 failed")
    r = _aufruf(client, "sales_post_pruefen", {"eingabe": EINGABE})
    assert r["isError"] is True and r["content"][0]["text"] == "interner Fehler"
    assert "10.0.0.9" not in json.dumps(r) and "10.0.0.9" not in caplog.text
    zeilen = [z for z in caplog.records if z.name == "auftragsbuch"]
    assert len(zeilen) == 1 and "intern" in zeilen[0].getMessage() and "OperationalError" in zeilen[0].getMessage()


def test_log_nennt_keine_eingaben(client, caplog):
    caplog.set_level(logging.INFO)
    r = _aufruf(client, "sales_post_pruefen", {"eingabe": EINGABE, "plan_id": "plan-geheim-7"})
    assert r["isError"] is False
    assert "sales_post_pruefen" in caplog.text
    assert "heute" not in caplog.text and "plan-geheim-7" not in caplog.text
    zeilen = [z for z in caplog.records if z.name == "auftragsbuch"]
    assert len(zeilen) == 1 and zeilen[0].levelno == logging.INFO and "ergebnis=ok" in zeilen[0].getMessage()


def test_fremder_host_wird_abgelehnt(buch):
    with TestClient(mcp_server.app_bauen(buch, ARTEN, HASH, []), base_url="http://boese.example") as c:
        antwort = _rpc(c, SCHLUESSEL, "tools/call", {"name": "auftrag_probe", "arguments": {}})
    assert antwort.status_code == 421 and buch.aufrufe == 0


def test_erlaubte_hosts_und_zusatz(buch):
    for host in ("http://auftragsbuch:8172", "http://localhost:8172"):
        with TestClient(mcp_server.app_bauen(buch, ARTEN, HASH, []), base_url=host) as c:
            assert _rpc(c, SCHLUESSEL, "tools/list", {}).status_code == 200
    with TestClient(mcp_server.app_bauen(buch, ARTEN, HASH, ["Brain.example.invalid"]),
                    base_url="http://brain.example.invalid:8172") as c:
        assert _rpc(c, SCHLUESSEL, "tools/list", {}).status_code == 200
    with pytest.raises(ValueError):
        mcp_server.app_bauen(buch, ARTEN, HASH, ["https://nein/pfad"])


def test_start_ohne_umgebung_nennt_die_variable(monkeypatch):
    for name in ("AUFTRAGSBUCH_DSN_FILE", "AUFTRAGSBUCH_SCHLUESSEL_HASH_FILE", "AUFTRAGSBUCH_LADEN",
                 "AUFTRAGSBUCH_MANDANT", "AUFTRAGSBUCH_VERTRAG_ORDNER"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as e:
        mcp_server.main()
    assert "AUFTRAGSBUCH_DSN_FILE" in str(e.value)


_DSN_GEHEIM = "postgresql://dienst:GEHEIMPASS@db.beispiel.invalid:5432/postgres"


@pytest.fixture
def start_umgebung(tmp_path, monkeypatch):
    """Vollstaendige Umgebung fuer main(); Logging und uvicorn bleiben unberuehrt."""
    dsn = tmp_path / "dsn"
    dsn.write_text(_DSN_GEHEIM + "\n", encoding="utf-8")
    hashdatei = tmp_path / "hash"
    hashdatei.write_text(HASH + "\n", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "log_einrichten", lambda strom=None: None)
    import uvicorn

    def kein_start(*_a, **_k):
        raise AssertionError("uvicorn darf nicht starten")

    monkeypatch.setattr(uvicorn, "run", kein_start)
    return {"AUFTRAGSBUCH_DSN_FILE": str(dsn), "AUFTRAGSBUCH_SCHLUESSEL_HASH_FILE": str(hashdatei),
            "AUFTRAGSBUCH_LADEN": "laden-1", "AUFTRAGSBUCH_MANDANT": "mandant-1",
            "AUFTRAGSBUCH_VERTRAG_ORDNER": str(Path(__file__).resolve().parents[2] / "_contract")}


def _ohne_stapel(e: pytest.ExceptionInfo) -> None:
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_start_datenbank_nicht_erreichbar_ohne_stapel_und_dsn(start_umgebung, monkeypatch):
    import psycopg_pool

    geschlossen: list[bool] = []

    class Pool:
        def __init__(self, *_a, **_k) -> None:
            pass

        def open(self, wait: bool = False, timeout: float = 0) -> None:
            raise psycopg_pool.PoolTimeout(f"pool initialization incomplete: {_DSN_GEHEIM}")

        def close(self) -> None:
            geschlossen.append(True)

    monkeypatch.setattr(psycopg_pool, "ConnectionPool", Pool)
    with pytest.raises(SystemExit) as e:
        mcp_server.main(start_umgebung)
    assert str(e.value) == "auftragsbuch: Datenbank nicht erreichbar (PoolTimeout)"
    _ohne_stapel(e)
    assert geschlossen == [True]


def test_start_pool_nicht_baubar_ohne_dsn(start_umgebung, monkeypatch):
    import psycopg
    import psycopg_pool

    def kaputt(*_a, **_k):
        raise psycopg.ProgrammingError(f"invalid connection option in {_DSN_GEHEIM}")

    monkeypatch.setattr(psycopg_pool, "ConnectionPool", kaputt)
    with pytest.raises(SystemExit) as e:
        mcp_server.main(start_umgebung)
    assert str(e.value) == "auftragsbuch: Datenbank nicht erreichbar (ProgrammingError)"
    _ohne_stapel(e)


def test_start_vertrag_nicht_ladbar_nennt_nur_den_typ(start_umgebung, tmp_path):
    ordner = tmp_path / "vertraege"
    ordner.mkdir()
    (ordner / "kaputt.contract.yaml").write_text("id: x\nauftragsarten: GEHEIM-INHALT\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        mcp_server.main({**start_umgebung, "AUFTRAGSBUCH_VERTRAG_ORDNER": str(ordner)})
    assert str(e.value) == "auftragsbuch: Vertrag nicht ladbar (VertragsFehler)"
    _ohne_stapel(e)


def test_konfiguration_nennt_jede_fehlende_variable(tmp_path):
    dsn = tmp_path / "dsn"
    dsn.write_text("postgresql://x\n", encoding="utf-8")
    hashdatei = tmp_path / "hash"
    hashdatei.write_text(HASH + "\r\n", encoding="utf-8")
    voll = {"AUFTRAGSBUCH_DSN_FILE": str(dsn), "AUFTRAGSBUCH_SCHLUESSEL_HASH_FILE": str(hashdatei),
            "AUFTRAGSBUCH_LADEN": "laden-1", "AUFTRAGSBUCH_MANDANT": "mandant-1",
            "AUFTRAGSBUCH_VERTRAG_ORDNER": str(tmp_path)}
    k = mcp_server.konfiguration(voll)
    assert (k.host, k.port, k.schluessel_hash, k.zusatz_hosts) == ("127.0.0.1", 8172, HASH, ())
    assert "postgresql" not in repr(k)
    for fehlt in voll:
        rest = {n: w for n, w in voll.items() if n != fehlt}
        with pytest.raises(ValueError) as e:
            mcp_server.konfiguration(rest)
        assert fehlt in str(e.value)
    k = mcp_server.konfiguration({**voll, "AUFTRAGSBUCH_PORT": "9000", "AUFTRAGSBUCH_HOST": "0.0.0.0",
                                  "AUFTRAGSBUCH_ZUSATZ_HOSTS": "a.example.invalid, b.example.invalid:8172"})
    assert (k.host, k.port, k.zusatz_hosts) == ("0.0.0.0", 9000, ("a.example.invalid", "b.example.invalid:8172"))
    with pytest.raises(ValueError):
        mcp_server.konfiguration({**voll, "AUFTRAGSBUCH_PORT": "0"})


# --- Log-Setup: nur der Logger `auftragsbuch` schreibt ------------------------------------


@pytest.fixture
def log_strom():
    """Setzt `log_einrichten` auf einen eigenen Strom und stellt den Logging-Zustand danach wieder her."""
    wurzel, eigener = logging.getLogger(), logging.getLogger("auftragsbuch")
    vorher = (wurzel.handlers[:], wurzel.level, eigener.handlers[:], eigener.level, eigener.propagate)
    strom = io.StringIO()
    mcp_server.log_einrichten(strom)
    try:
        yield strom
    finally:
        wurzel.handlers[:], wurzel.level = vorher[0], vorher[1]
        eigener.handlers[:], eigener.level, eigener.propagate = vorher[2], vorher[3], vorher[4]


def test_log_argumente_erreichen_das_log_nicht(buch, log_strom):
    with TestClient(mcp_server.app_bauen(buch, ARTEN, HASH, []), base_url=BASIS) as c:
        antwort = _rpc(c, SCHLUESSEL, "tools/call", {"name": "auftrag_probe", "arguments": "GEHEIMWERT-ARG"})
    assert antwort.status_code < 500
    assert "GEHEIMWERT-ARG" not in log_strom.getvalue()


def test_log_fremder_host_erreicht_das_log_nicht(buch, log_strom):
    with TestClient(mcp_server.app_bauen(buch, ARTEN, HASH, []), base_url="http://boese-GEHEIMHOST.example") as c:
        assert _rpc(c, SCHLUESSEL, "tools/list", {}).status_code == 421
    assert "GEHEIMHOST" not in log_strom.getvalue()


def test_log_pool_warnungen_erreichen_das_log_nicht(log_strom):
    logging.getLogger("psycopg.pool").warning("error connecting in 'x': connection to server at \"10.9.9.9\" failed")
    logging.warning("root 10.9.9.9")
    assert "10.9.9.9" not in log_strom.getvalue()


def test_log_eigener_logger_schreibt(buch, log_strom):
    with TestClient(mcp_server.app_bauen(buch, ARTEN, HASH, []), base_url=BASIS) as c:
        _aufruf(c, "auftrag_probe", {})
    assert "werkzeug=auftrag_probe ergebnis=ok" in log_strom.getvalue()
