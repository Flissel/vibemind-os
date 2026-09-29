import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core.knowledge.schema import Beleg, Dokument, Fakt
from core.knowledge.tresor import Tresor

T = datetime.now(timezone.utc)


def _mod(name):
    p = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def dok(id, wert, alter_h=0, deutung=""):
    g = T - timedelta(hours=alter_h)
    return Dokument(typ="bubble", id=id, titel=f"B{id}", stand=g,
                    fakten=[Fakt(schluessel="status", wert=wert, beleg=1)],
                    belege=[Beleg(nr=1, quelle="supabase", ziel=f"x{id}", feld="status",
                                  wert=wert, gemessen=g)], deutung=deutung)


def test_messen(tmp_path):
    t = Tresor(tmp_path)
    t.schreiben(dok("aaaaaa1", "raw", alter_h=2, deutung="Roh [B1]."))
    t.schreiben(dok("bbbbbb2", "raw", alter_h=30))
    m = _mod("wissen_messen")
    antworten = {"xaaaaaa1": True, "xbbbbbb2": False}
    r = m.messen(t, nachfragen=lambda b: antworten[b.ziel])
    assert r["dokumente"] == 2 and r["belege"] == 2 and r["belege_stimmen"] == 1
    assert r["nachfrage_quote"] == 0.5
    assert r["beleg_quote"] == 1.0
    assert 29.5 < r["aeltester_beleg_h"] < 30.5


def test_messen_nicht_pruefbar_ausgeschlossen(tmp_path):
    """nachfragen()==None (Quelle ausgefallen) zaehlt nicht in die Quote."""
    t = Tresor(tmp_path)
    t.schreiben(dok("cccccc3", "raw", alter_h=1, deutung="Roh [B1]."))
    t.schreiben(dok("dddddd4", "raw", alter_h=1))
    m = _mod("wissen_messen")
    antworten = {"xcccccc3": True, "xdddddd4": None}
    r = m.messen(t, nachfragen=lambda b: antworten[b.ziel])
    assert r["belege"] == 2
    assert r["nicht_pruefbar"] == 1
    assert r["belege_stimmen"] == 1
    assert r["nachfrage_quote"] == 1.0  # 1 von 1 pruefbaren stimmt


# ---------------------------------------------------------------------
# archivieren.py: gegen ein Fake-Qdrant (requests.post/put in-modul gepatcht).
# Der Stack ist aus - keine echten Netzwerkaufrufe, kein --ausfuehren gegen
# echte Pfade in diesem Task (Controller-Ruling).
# ---------------------------------------------------------------------

class FakeResp:
    def __init__(self, data, ok=True):
        self._data = data
        self._ok = ok

    def json(self):
        return self._data

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("http fehler")


def _archiv_argv(ausfuehren=False, von="brain-episodic", nach="brain-episodic-archive"):
    argv = ["--von", von, "--nach", nach, "--filter", "source=continuous_thinking"]
    if ausfuehren:
        argv.append("--ausfuehren")
    return argv


def test_archivieren_trockenlauf_aendert_nichts():
    m = _mod("archivieren")
    mock_post = patch.object(m.requests, "post",
                              return_value=FakeResp({"result": {"count": 3}})).start()
    mock_put = patch.object(m.requests, "put").start()
    try:
        rc = m.main(_archiv_argv(ausfuehren=False))
    finally:
        patch.stopall()
    assert rc == 0
    assert mock_post.call_count == 1  # nur die Vorab-Zaehlung
    assert mock_put.call_count == 0


def test_archivieren_happy_path_loescht_genau_die_kopierten_ids():
    m = _mod("archivieren")
    punkte = [
        {"id": "id1", "vector": [0.1, 0.2], "payload": {"a": 1}},
        {"id": "id2", "vector": [0.3, 0.4], "payload": {"b": 2}},
    ]

    def fake_post(url, json=None, timeout=None):
        basis = url.split("?", 1)[0]
        if basis.endswith("/collections/brain-episodic/points/count"):
            return FakeResp({"result": {"count": 2}})
        if basis.endswith("/collections/brain-episodic/points/scroll"):
            return FakeResp({"result": {"points": punkte, "next_page_offset": None}})
        if basis.endswith("/collections/brain-episodic-archive/points"):
            # ID-Rueckholung zur Bestaetigung: beide gefunden.
            angefragt = set(json["ids"])
            return FakeResp({"result": [{"id": i} for i in ("id1", "id2") if i in angefragt]})
        if basis.endswith("/collections/brain-episodic/points/delete"):
            return FakeResp({"result": None})
        raise AssertionError(f"unerwartete POST-URL: {url}")

    def fake_put(url, json=None, timeout=None):
        basis = url.split("?", 1)[0]
        assert basis.endswith("/collections/brain-episodic-archive/points")
        assert [p["id"] for p in json["points"]] == ["id1", "id2"]
        return FakeResp({"result": None})

    mock_post = patch.object(m.requests, "post", side_effect=fake_post).start()
    mock_put = patch.object(m.requests, "put", side_effect=fake_put).start()
    try:
        rc = m.main(_archiv_argv(ausfuehren=True))
    finally:
        gel_calls = [c for c in mock_post.call_args_list
                     if c.args[0].split("?", 1)[0].endswith("/points/delete")]
        patch.stopall()
    assert rc == 0
    assert mock_put.call_count == 1
    assert len(gel_calls) == 1
    assert sorted(gel_calls[0].kwargs["json"]["points"]) == ["id1", "id2"]


def test_archivieren_quelle_veraendert_waehrend_lauf_loescht_nichts():
    """Vorab-Zaehlung und tatsaechlich kopierte Punkte weichen ab (Quelle hat
    sich waehrend des Laufs veraendert) -> Abbruch vor der Archiv-Bestaetigung,
    nichts geloescht."""
    m = _mod("archivieren")
    punkte = [{"id": "id1", "vector": [0.1], "payload": {}}]  # nur 1 statt gezaehlter 2

    def fake_post(url, json=None, timeout=None):
        basis = url.split("?", 1)[0]
        if basis.endswith("/collections/brain-episodic/points/count"):
            return FakeResp({"result": {"count": 2}})
        if basis.endswith("/collections/brain-episodic/points/scroll"):
            return FakeResp({"result": {"points": punkte, "next_page_offset": None}})
        raise AssertionError(f"Bestaetigung/Loeschung duerfen hier nicht laufen: {url}")

    def fake_put(url, json=None, timeout=None):
        return FakeResp({"result": None})

    mock_post = patch.object(m.requests, "post", side_effect=fake_post).start()
    mock_put = patch.object(m.requests, "put", side_effect=fake_put).start()
    try:
        rc = m.main(_archiv_argv(ausfuehren=True))
    finally:
        patch.stopall()
    assert rc == 1
    assert mock_put.call_count == 1  # die eine Seite wurde noch kopiert


def test_archivieren_retry_ohne_archiv_wachstum_erfolgreich():
    """Fix-Runde 1, Finding 1: ein erneuter Lauf, bei dem das Archiv die IDs
    schon vollstaendig enthaelt (kein Wachstum durch das erneute Upsert), muss
    trotzdem erfolgreich abschliessen und aus der Quelle loeschen - die alte
    Vorher/Nachher-Zaehlung waere hier fuer immer haengengeblieben (Finding),
    weil ein erneuter Lauf das Archiv nie mehr waechst."""
    m = _mod("archivieren")
    punkte = [
        {"id": "id1", "vector": [0.1], "payload": {}},
        {"id": "id2", "vector": [0.2], "payload": {}},
    ]

    def fake_post(url, json=None, timeout=None):
        basis = url.split("?", 1)[0]
        if basis.endswith("/collections/brain-episodic/points/count"):
            return FakeResp({"result": {"count": 2}})
        if basis.endswith("/collections/brain-episodic/points/scroll"):
            return FakeResp({"result": {"points": punkte, "next_page_offset": None}})
        if basis.endswith("/collections/brain-episodic-archive/points"):
            # Archiv hatte die IDs schon VOR diesem Upsert - waechst also
            # nicht, ist aber trotzdem vollstaendig bestaetigt.
            return FakeResp({"result": [{"id": "id1"}, {"id": "id2"}]})
        if basis.endswith("/collections/brain-episodic/points/delete"):
            return FakeResp({"result": None})
        raise AssertionError(f"unerwartete POST-URL: {url}")

    def fake_put(url, json=None, timeout=None):
        return FakeResp({"result": None})  # Upsert - idempotent, kein Fehler

    mock_post = patch.object(m.requests, "post", side_effect=fake_post).start()
    patch.object(m.requests, "put", side_effect=fake_put).start()
    try:
        rc = m.main(_archiv_argv(ausfuehren=True))
    finally:
        gel_calls = [c for c in mock_post.call_args_list
                     if c.args[0].split("?", 1)[0].endswith("/points/delete")]
        patch.stopall()
    assert rc == 0
    assert len(gel_calls) == 1
    assert sorted(gel_calls[0].kwargs["json"]["points"]) == ["id1", "id2"]


def test_archivieren_teilweise_bestaetigt_loescht_nichts():
    """Nur ein Teil der kopierten IDs laesst sich im Archiv per ID
    zurueckholen (z.B. eine Seite kam nicht durch) -> Abbruch, nichts
    geloescht."""
    m = _mod("archivieren")
    punkte = [
        {"id": "id1", "vector": [0.1], "payload": {}},
        {"id": "id2", "vector": [0.2], "payload": {}},
    ]

    def fake_post(url, json=None, timeout=None):
        basis = url.split("?", 1)[0]
        if basis.endswith("/collections/brain-episodic/points/count"):
            return FakeResp({"result": {"count": 2}})
        if basis.endswith("/collections/brain-episodic/points/scroll"):
            return FakeResp({"result": {"points": punkte, "next_page_offset": None}})
        if basis.endswith("/collections/brain-episodic-archive/points"):
            return FakeResp({"result": [{"id": "id1"}]})  # id2 fehlt im Archiv
        if basis.endswith("/collections/brain-episodic/points/delete"):
            raise AssertionError("darf bei unvollstaendiger Bestaetigung nicht aufgerufen werden")
        raise AssertionError(f"unerwartete POST-URL: {url}")

    def fake_put(url, json=None, timeout=None):
        return FakeResp({"result": None})

    mock_post = patch.object(m.requests, "post", side_effect=fake_post).start()
    mock_put = patch.object(m.requests, "put", side_effect=fake_put).start()
    try:
        rc = m.main(_archiv_argv(ausfuehren=True))
    finally:
        patch.stopall()
    assert rc == 1
    assert mock_put.call_count == 1


def test_archivieren_benannte_vektoren_unveraendert_durchgereicht():
    """Fix-Runde 1, Finding 1 (c): Punkte mit benannten Vektoren (dict statt
    Liste) werden unveraendert in den PUT-Body kopiert."""
    m = _mod("archivieren")
    vektor = {"semantic": [0.1, 0.2], "neural": [0.3, 0.4]}
    punkte = [{"id": "id1", "vector": vektor, "payload": {}}]

    def fake_post(url, json=None, timeout=None):
        basis = url.split("?", 1)[0]
        if basis.endswith("/collections/brain-episodic/points/count"):
            return FakeResp({"result": {"count": 1}})
        if basis.endswith("/collections/brain-episodic/points/scroll"):
            return FakeResp({"result": {"points": punkte, "next_page_offset": None}})
        if basis.endswith("/collections/brain-episodic-archive/points"):
            return FakeResp({"result": [{"id": "id1"}]})
        if basis.endswith("/collections/brain-episodic/points/delete"):
            return FakeResp({"result": None})
        raise AssertionError(f"unerwartete POST-URL: {url}")

    gesehene_vektoren = []

    def fake_put(url, json=None, timeout=None):
        gesehene_vektoren.append(json["points"][0]["vector"])
        return FakeResp({"result": None})

    patch.object(m.requests, "post", side_effect=fake_post).start()
    patch.object(m.requests, "put", side_effect=fake_put).start()
    try:
        rc = m.main(_archiv_argv(ausfuehren=True))
    finally:
        patch.stopall()
    assert rc == 0
    assert gesehene_vektoren == [vektor]


# ---------------------------------------------------------------------
# wissen_initialisieren.py: Finding 2 (Fix-Runde 1) - wenn QdrantKG()
# gelingt, aber ensure_collections() wirft, muss kg trotzdem auf None
# zurueckfallen (Datei-Modus), statt mit einer halb eingerichteten KG in
# index.neu_aufbauen zu laufen.
# ---------------------------------------------------------------------

def test_wissen_initialisieren_ensure_collections_faellt_kg_bleibt_none(monkeypatch):
    m = _mod("wissen_initialisieren")

    class KaputtesQdrantKG:
        def __init__(self):
            pass

        def ensure_collections(self):
            raise RuntimeError("qdrant weg")

    aufrufe = {}

    class FakeKurator:
        def __init__(self, tresor, kg=None, deuten=None):
            aufrufe["kg"] = kg

        def voll_durchlauf(self):
            return {"geschrieben": 0}

    monkeypatch.setattr("core.qdrant_kg.QdrantKG", KaputtesQdrantKG)
    monkeypatch.setattr(m.kurator, "Kurator", FakeKurator)
    monkeypatch.setattr(m.index, "neu_aufbauen",
                         lambda *a, **kw: aufrufe.setdefault("index_aufgerufen", True))

    rc = m.main([])
    assert rc == 0
    assert aufrufe["kg"] is None
    assert "index_aufgerufen" not in aufrufe  # Datei-Modus: kein Index-Aufbau


def test_messen_fluechtige_belege_zaehlen_nicht_in_die_quote(tmp_path):
    """Schlusspruefung I6: #agent:* (gleitendes Audit-Fenster) und *last_active
    aendern sich staendig - sie werden separat ausgewiesen, nicht in der Quote."""
    t = Tresor(tmp_path)
    g = T
    d = Dokument(typ="agent", id="agent0001", titel="Coder", stand=g,
                 fakten=[Fakt(schluessel="zustand", wert="Running", beleg=1),
                         Fakt(schluessel="zuletzt_aktiv", wert="t1", beleg=2),
                         Fakt(schluessel="aktionen_ok", wert="5", beleg=3)],
                 belege=[Beleg(nr=1, quelle="openfang", ziel="/api/agents",
                               feld="[name=Coder].state", wert="Running", gemessen=g),
                         Beleg(nr=2, quelle="openfang", ziel="/api/agents",
                               feld="[name=Coder].last_active", wert="t1", gemessen=g),
                         Beleg(nr=3, quelle="openfang", ziel="/api/audit/recent?n=500",
                               feld="#agent:agent0001:ok", wert="5", gemessen=g)])
    t.schreiben(d)
    m = _mod("wissen_messen")
    antworten = {"[name=Coder].state": True, "[name=Coder].last_active": False,
                 "#agent:agent0001:ok": True}
    r = m.messen(t, nachfragen=lambda b: antworten[b.feld])
    assert r["nachfrage_quote"] == 1.0
    assert r["belege_stimmen"] == 1
    assert r["fluechtig_gesamt"] == 2
    assert r["fluechtig_stimmen"] == 1
