"""Laden des Sprachmodells unter Nebenlaeufigkeit (12.11.2026) - nur Fakes, kein torch/chatterbox."""
import json
import logging
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from spaces.marketing.claw import ton_dienst as td


class _Modell:
    sr = 24000

    def generate(self, text, **kw):
        return types.SimpleNamespace(cpu=lambda: b"PCM")


class _Fake:
    """Steht fuer ChatterboxMultilingualTTS; zaehlt Aufbauten, Aufbau kann langsam sein oder scheitern."""
    aufbauten = 0
    gleichzeitig = 0
    max_gleichzeitig = 0
    dauer = 0.0
    fehler: list = []
    schranke: threading.Event | None = None

    @classmethod
    def from_pretrained(cls, device):
        cls.aufbauten += 1
        cls.gleichzeitig += 1
        cls.max_gleichzeitig = max(cls.max_gleichzeitig, cls.gleichzeitig)
        try:
            if cls.schranke is not None:
                cls.schranke.wait(10)
            time.sleep(cls.dauer)
            if cls.fehler:
                raise cls.fehler.pop(0)
            return _Modell()
        finally:
            cls.gleichzeitig -= 1


def _fake_module_eintragen(monkeypatch):
    mod = types.ModuleType("chatterbox.mtl_tts")
    mod.ChatterboxMultilingualTTS = _Fake
    monkeypatch.setitem(sys.modules, "chatterbox", types.ModuleType("chatterbox"))
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", mod)


@pytest.fixture
def fake(monkeypatch):
    _Fake.aufbauten = _Fake.gleichzeitig = _Fake.max_gleichzeitig = 0
    _Fake.dauer, _Fake.fehler, _Fake.schranke = 0.0, [], None
    _fake_module_eintragen(monkeypatch)
    monkeypatch.setattr(td, "_modell", None)
    monkeypatch.setattr(td, "_laedt", False)
    monkeypatch.setattr(td, "_freigeben_geraet", lambda: None)
    monkeypatch.delenv("TON_MODELL_ORDNER", raising=False)
    ta = types.SimpleNamespace(save=lambda puffer, wav, sr, **kw: puffer.write(b"RIFFfake"))
    monkeypatch.setattr(td, "_torchaudio_holen", lambda: ta)
    return _Fake


def _warte_bis_laedt():
    ende = time.monotonic() + 10
    while td.modell_zustand() != "laedt":
        assert time.monotonic() < ende
        time.sleep(0.01)


def test_zwei_threads_laden_genau_einmal(fake):
    fake.dauer = 0.3
    ergebnisse = []
    start = threading.Barrier(2)

    def lauf():
        start.wait()
        ergebnisse.append(td._modell_laden())
    ts = [threading.Thread(target=lauf) for _ in range(2)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert fake.aufbauten == 1 and fake.max_gleichzeitig == 1
    assert len(ergebnisse) == 2 and ergebnisse[0] is ergebnisse[1]


def test_anfrage_waehrend_langsamem_laden_wartet_und_gelingt(fake):
    fake.dauer = 0.4
    vorladen = threading.Thread(target=td._modell_laden)
    vorladen.start()
    _warte_bis_laedt()
    wav = td._sprechen_chatterbox("Hallo", "de", None)     # echter Weg, Fakes fuer Modell/torchaudio
    vorladen.join(10)
    assert wav == b"RIFFfake" and fake.aufbauten == 1


def test_wartezeit_abgelaufen_gibt_modell_laedt(fake):
    fake.schranke = threading.Event()
    vorladen = threading.Thread(target=td._modell_laden)
    vorladen.start()
    _warte_bis_laedt()
    try:
        with pytest.raises(td.ModellLaedt):
            td._modell_laden(0.1)
    finally:
        fake.schranke.set()
        vorladen.join(10)
    assert td.modell_zustand() == "geladen" and fake.aufbauten == 1


def test_scheitern_vergiftet_nicht_und_logt_nur_den_typ(fake, caplog, monkeypatch):
    fake.fehler = [OSError("geheimer Pfad C:/x")]
    monkeypatch.setitem(sys.modules, "chatterbox.teil", types.ModuleType("chatterbox.teil"))  # halb importierter Rest
    fremd = types.ModuleType("torch_fremd")
    monkeypatch.setitem(sys.modules, "torch_fremd", fremd)
    with caplog.at_level(logging.INFO, logger=td.LOG.name):
        with pytest.raises(OSError):
            td._modell_laden()
    assert td._modell is None and td.modell_zustand() == "nicht-geladen"
    assert "OSError" in caplog.text and "geheim" not in caplog.text
    assert "chatterbox.teil" not in sys.modules and sys.modules["torch_fremd"] is fremd   # nur chatterbox*
    # zweiter Versuch: chatterbox wird frisch importiert (hier wieder eingetragen) und gelingt
    _fake_module_eintragen(monkeypatch)
    assert isinstance(td._modell_laden(), _Modell) and fake.aufbauten == 2


def test_import_fehler_beim_laden_wird_wie_ein_ladefehler_behandelt(fake, monkeypatch):
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", None)       # Import wirft ImportError
    with pytest.raises(ImportError):
        td._modell_laden()
    assert td._modell is None and td._laedt is False


def _server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), td.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _anfrage(server, pfad, nutzlast=None):
    url = f"http://127.0.0.1:{server.server_port}{pfad}"
    req = urllib.request.Request(url, data=json.dumps(nutzlast).encode() if nutzlast is not None else None,
                                 method="POST" if nutzlast is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_healthz_zeigt_nicht_geladen_dann_geladen(fake):
    server = _server()
    try:
        status, _, rumpf = _anfrage(server, "/healthz")
        assert status == 200 and json.loads(rumpf) == {"ok": True, "modell": "nicht-geladen"}
        td._modell_laden()
        status, _, rumpf = _anfrage(server, "/healthz")
        assert status == 200 and json.loads(rumpf) == {"ok": True, "modell": "geladen"}
    finally:
        server.shutdown()
        server.server_close()


def test_healthz_waehrend_des_ladens(fake):
    fake.schranke = threading.Event()
    server = _server()
    vorladen = threading.Thread(target=td._modell_laden)
    vorladen.start()
    try:
        _warte_bis_laedt()
        status, _, rumpf = _anfrage(server, "/healthz")
        assert status == 200 and json.loads(rumpf)["modell"] == "laedt"
    finally:
        fake.schranke.set()
        vorladen.join(10)
        server.shutdown()
        server.server_close()


def test_satz_beim_laden_wartet_oder_bekommt_503(fake, monkeypatch):
    monkeypatch.setattr(td, "_stille_gekuerzt", lambda wav: wav)
    monkeypatch.setattr(td, "_wav_dauer_s", lambda wav: 1.0)
    server = _server()
    fake.schranke = threading.Event()
    vorladen = threading.Thread(target=td._modell_laden)
    vorladen.start()
    try:
        _warte_bis_laedt()
        monkeypatch.setattr(td, "LADEN_WARTEN_S", 0.2)
        status, kopf, rumpf = _anfrage(server, "/satz", {"text": "Hallo."})
        assert status == 503 and kopf["Retry-After"] == "30" and "geladen" in rumpf.decode("utf-8")
        assert b"KeyError" not in rumpf
        # mit ausreichender Wartezeit: das Laden endet waehrenddessen, die Anfrage gelingt
        monkeypatch.setattr(td, "LADEN_WARTEN_S", 10.0)
        threading.Timer(0.3, fake.schranke.set).start()
        status, kopf, rumpf = _anfrage(server, "/satz", {"text": "Hallo."})
        assert status == 200 and rumpf == b"RIFFfake" and fake.aufbauten == 1
    finally:
        fake.schranke.set()
        vorladen.join(10)
        server.shutdown()
        server.server_close()


def test_sprechen_beim_laden_503(fake, monkeypatch):
    fake.schranke = threading.Event()
    server = _server()
    vorladen = threading.Thread(target=td._modell_laden)
    vorladen.start()
    try:
        _warte_bis_laedt()
        monkeypatch.setattr(td, "LADEN_WARTEN_S", 0.1)
        status, kopf, _ = _anfrage(server, "/sprechen", {"text": "Hallo."})
        assert status == 503 and kopf["Retry-After"] == "30"
    finally:
        fake.schranke.set()
        vorladen.join(10)
        server.shutdown()
        server.server_close()
