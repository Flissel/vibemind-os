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
    nach_zaehlungen = iter([0, 2])  # vorher=0, nachher=0+2 (Kopie schlug voll durch)

    def fake_post(url, json=None, timeout=None):
        basis = url.split("?", 1)[0]
        if basis.endswith("/collections/brain-episodic/points/count"):
            return FakeResp({"result": {"count": 2}})
        if basis.endswith("/collections/brain-episodic-archive/points/count"):
            return FakeResp({"result": {"count": next(nach_zaehlungen)}})
        if basis.endswith("/collections/brain-episodic/points/scroll"):
            return FakeResp({"result": {"points": punkte, "next_page_offset": None}})
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
    assert gel_calls[0].kwargs["json"]["points"] == ["id1", "id2"]


def test_archivieren_zaehlung_stimmt_nicht_loescht_nichts():
    """Archiv waechst um weniger als kopiert -> Abbruch, kein Loeschen."""
    m = _mod("archivieren")
    punkte = [
        {"id": "id1", "vector": [0.1], "payload": {}},
        {"id": "id2", "vector": [0.2], "payload": {}},
    ]
    nach_zaehlungen = iter([0, 1])  # vorher=0, nachher=1 statt erwarteter 2

    def fake_post(url, json=None, timeout=None):
        basis = url.split("?", 1)[0]
        if basis.endswith("/collections/brain-episodic/points/count"):
            return FakeResp({"result": {"count": 2}})
        if basis.endswith("/collections/brain-episodic-archive/points/count"):
            return FakeResp({"result": {"count": next(nach_zaehlungen)}})
        if basis.endswith("/collections/brain-episodic/points/scroll"):
            return FakeResp({"result": {"points": punkte, "next_page_offset": None}})
        if basis.endswith("/collections/brain-episodic/points/delete"):
            raise AssertionError("darf bei Zaehl-Mismatch nicht aufgerufen werden")
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
    assert mock_put.call_count == 1  # kopiert wurde noch
    # keine Loesch-POSTs unter den aufgezeichneten Aufrufen
    assert not any(c.args[0].split("?", 1)[0].endswith("/points/delete")
                   for c in mock_post.call_args_list)
