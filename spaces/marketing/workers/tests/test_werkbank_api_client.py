"""Client der Werkbank-API: Upload in Teilen und Fehlerform; `_senden` ist der einzige gefakte Punkt."""
import json

import pytest

from spaces.marketing.workers import werkbank_api as wa
from spaces.marketing.workers.bild_worker import ApiFehler

AID_A = "0123abcd-1111-4222-8333-444455556666"


# --- Hochladen --------------------------------------------------------------------------------

class UploadApi(wa.WerkbankApi):
    def __init__(self, skript):
        self.skript, self.log, self.schlafen = list(skript), [], lambda s: None

    def _senden(self, methode, pfad, koerper=None, typ="application/json", zeit=60):
        self.log.append((methode, pfad, koerper if methode != "PUT" else len(koerper)))
        aktion = self.skript.pop(0) if self.skript else None
        if callable(aktion):
            return aktion(methode, pfad, koerper)
        if methode == "POST" and pfad.endswith("/beginnen"):
            return 200, {"ok": True, "laenge": 0}
        if methode == "PUT":
            off = int(pfad.rsplit("=", 1)[1])
            return 200, {"laenge": off + len(koerper)}
        return 200, {"ok": True}


def test_hochladen_in_teilen_mit_sha256(tmp_path, monkeypatch):
    monkeypatch.setattr(wa, "TEIL_BYTES", 10)
    f = tmp_path / "wb-0123abcd-s01-f0.mp4"
    f.write_bytes(bytes(range(25)))
    api = UploadApi([])
    api.hochladen(AID_A, f.name, f)
    puts = [x for x in api.log if x[0] == "PUT"]
    assert [p[1].rsplit("=", 1)[1] for p in puts] == ["0", "10", "20"] and [p[2] for p in puts] == [10, 10, 5]
    import hashlib
    assert json.loads(api.log[-1][2]) == {"sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
    assert json.loads(api.log[0][2]) == {"bytes": 25}


def test_hochladen_setzt_nach_409_an_der_gemeldeten_laenge_fort(tmp_path, monkeypatch):
    monkeypatch.setattr(wa, "TEIL_BYTES", 10)
    f = tmp_path / "x.mp4"
    f.write_bytes(bytes(range(25)))
    api = UploadApi([None, lambda m, p, k: (200, {"laenge": 10}), lambda m, p, k: (409, {"detail": "Offset", "laenge": 20})])
    api.hochladen(AID_A, "x.mp4", f)
    puts = [x[1].rsplit("=", 1)[1] for x in api.log if x[0] == "PUT"]
    assert puts == ["0", "10", "20"]          # nach dem 409 geht es bei 20 weiter, nicht bei 10 noch einmal


def test_hochladen_gibt_nach_drei_wiederholungen_auf(tmp_path, monkeypatch):
    monkeypatch.setattr(wa, "TEIL_BYTES", 10)
    f = tmp_path / "x.mp4"
    f.write_bytes(bytes(range(25)))
    kaputt = lambda m, p, k: (503, {})    # noqa: E731
    api = UploadApi([None] + [kaputt] * 10)
    with pytest.raises(ApiFehler):
        api.hochladen(AID_A, "x.mp4", f)
    assert len([x for x in api.log if x[0] == "PUT"]) == 1 + wa.WIEDERHOLUNGEN


def test_hochladen_netzfehler_wird_wiederholt(tmp_path, monkeypatch):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"abc")

    def weg(m, p, k):
        raise ConnectionResetError()
    api = UploadApi([None, weg, weg])
    api.hochladen(AID_A, "x.mp4", f)
    assert len([x for x in api.log if x[0] == "PUT"]) == 3 and api.log[-1][1].endswith("/abschliessen")


def test_api_fehler_traegt_laenge_bei_409():
    class A(wa.WerkbankApi):
        def __init__(self):
            pass

        def _senden(self, *a, **k):
            return 409, {"detail": "falscher Offset", "laenge": 7}
    with pytest.raises(ApiFehler) as e:
        A()._anfrage("PUT", "/x")
    assert e.value.code == 409 and e.value.laenge == 7
