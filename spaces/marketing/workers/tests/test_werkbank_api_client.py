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


def test_neue_routen_senden_pfad_und_koerper():
    """C1/I1/I3/M10: Aufgeben, gescheitertes Video und Bild mit Quellen/Belegen gehen an die richtigen Routen."""
    api = UploadApi([])
    sid = "9999abcd-1111-4222-8333-444455556666"
    assert api.szene_aufgegeben(sid, "Grund", "W") is True
    assert api.gliederung_aufgegeben(AID_A, "Grund", "") is True
    assert api.video_gescheitert(AID_A, 2, "Grund") is True
    assert api.szene_bild(sid, "slide", {"schritte": []}, ["auftrag"], [{"aussage": "a", "quelle": "auftrag"}])
    assert api.szene_bild(sid, "slide", {"schritte": []})
    pfade = [(m, p, json.loads(k)) for m, p, k in api.log]
    assert pfade[0] == ("POST", f"/szene/{sid}/aufgegeben", {"grund": "Grund", "wunsch": "W"})
    assert pfade[1] == ("POST", f"/{AID_A}/gliederung-aufgegeben", {"grund": "Grund", "wunsch": ""})
    assert pfade[2] == ("POST", f"/{AID_A}/video-gescheitert", {"grund": "Grund", "versuch": 2})
    assert pfade[3][2] == {"bildart": "slide", "bild": {"schritte": []}, "quellen": ["auftrag"],
                           "belege": [{"aussage": "a", "quelle": "auftrag"}]}
    assert pfade[4][2] == {"bildart": "slide", "bild": {"schritte": []}}   # ohne Listen: alte Werte bleiben


def test_api_fehler_traegt_laenge_bei_409():
    class A(wa.WerkbankApi):
        def __init__(self):
            pass

        def _senden(self, *a, **k):
            return 409, {"detail": "falscher Offset", "laenge": 7}
    with pytest.raises(ApiFehler) as e:
        A()._anfrage("PUT", "/x")
    assert e.value.code == 409 and e.value.laenge == 7


def test_hochladen_faengt_abgebrochene_antworten_wie_netzfehler(tmp_path):
    import http.client
    f = tmp_path / "x.mp4"
    f.write_bytes(b"abc")

    def abgebrochen(m, p, k):
        raise http.client.IncompleteRead(b"")
    api = UploadApi([None, abgebrochen])
    api.hochladen(AID_A, "x.mp4", f)
    assert len([x for x in api.log if x[0] == "PUT"]) == 2


# --- Holen -----------------------------------------------------------------------------------

class _Antwort:
    def __init__(self, daten, laenge=None, bruch=False):
        self.daten, self.bruch = daten, bruch
        self.headers = {"Content-Length": str(len(daten) if laenge is None else laenge)}
        self._pos = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        if self.bruch and self._pos:
            raise ConnectionResetError
        stueck = self.daten[self._pos:self._pos + (n if n and n > 0 else len(self.daten))]
        self._pos += len(stueck)
        return stueck


def _holer(antwort):
    api = wa.WerkbankApi("https://vm", "k")
    api.gerufen = []
    api._oeffnen = lambda pfad, zeit=300: (api.gerufen.append(pfad), antwort)[1]
    return api


def test_holen_streamt_in_datei_und_nennt_bytes(tmp_path):
    api = _holer(_Antwort(b"x" * 3_000_000))
    ziel = tmp_path / "wb-0123abcd-s01-f0.mp4"
    assert api.holen(AID_A, ziel.name, ziel) == 3_000_000
    assert ziel.read_bytes() == b"x" * 3_000_000 and api.gerufen == [f"/{AID_A}/datei/{ziel.name}"]
    assert not list(tmp_path.glob("*.teil"))


def test_holen_unvollstaendig_hinterlaesst_nichts(tmp_path):
    api = _holer(_Antwort(b"abc", laenge=10))
    ziel = tmp_path / "a.mp4"
    with pytest.raises(ApiFehler):
        api.holen(AID_A, "a.mp4", ziel)
    assert list(tmp_path.iterdir()) == []


def test_holen_http_fehler_wird_api_fehler(tmp_path):
    import io
    import urllib.error

    def wirft(pfad, zeit=300):
        raise urllib.error.HTTPError("u", 404, "n", {}, io.BytesIO(b"{}"))
    api = wa.WerkbankApi("https://vm", "k")
    api._oeffnen = wirft
    with pytest.raises(ApiFehler) as e:
        api.holen(AID_A, "a.mp4", tmp_path / "a.mp4")
    assert e.value.code == 404 and list(tmp_path.iterdir()) == []
