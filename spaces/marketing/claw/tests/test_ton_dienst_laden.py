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
    monkeypatch.setattr(td, "_defekt", False)
    monkeypatch.setattr(td, "_letzter_fehler", None)
    monkeypatch.setattr(td, "_torchaudio", None)
    monkeypatch.setattr(td, "_freigeben_geraet", lambda: None)
    monkeypatch.delenv("TON_MODELL_ORDNER", raising=False)
    _Fake.torchaudio_echt = td._torchaudio_holen
    ta = types.SimpleNamespace(save=lambda puffer, wav, sr, **kw: puffer.write(b"RIFFfake"))
    monkeypatch.setattr(td, "_torchaudio_holen", lambda *a: ta)
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
    # zweiter Versuch nach der Abkuehlzeit: chatterbox wird frisch importiert (hier wieder eingetragen) und gelingt
    monkeypatch.setattr(td, "_letzter_fehler", time.monotonic() - td.ABKUEHLEN_S - 1)
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


# --- Review-Nacharbeit 12.11.2026: Importe unter Lock, defekt, Abkuehlzeit ---------------------------

def test_alle_torch_chatterbox_importe_laufen_unter_dem_lock(fake, monkeypatch):
    """Faengt den eigentlichen Fehler: ein Import von torch*/torchaudio*/chatterbox* ausserhalb von
    _modell_lock (Vorladen und jeder Anfrageweg). Echte Pakete werden nie importiert."""
    import builtins
    echt = builtins.__import__
    gesehen: list[tuple[str, bool]] = []
    mtl = types.ModuleType("chatterbox.mtl_tts")
    mtl.ChatterboxMultilingualTTS = _Fake
    ta = types.ModuleType("torchaudio")
    ta.save = lambda puffer, wav, sr, **kw: puffer.write(b"RIFFfake")

    def hook(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name.split(".")[0] in ("torch", "torchaudio", "chatterbox"):
            gesehen.append((name, td._modell_lock.locked()))
            return {"chatterbox.mtl_tts": mtl, "torchaudio": ta}.get(name, types.ModuleType(name))
        return echt(name, globals, locals, fromlist, level)

    monkeypatch.setattr(td, "_torchaudio_holen", fake.torchaudio_echt)   # echte Funktion
    monkeypatch.setattr(td, "_stille_gekuerzt", lambda wav: wav)
    monkeypatch.setattr(td, "_wav_dauer_s", lambda wav: 1.0)
    monkeypatch.setattr(td, "anpassen", lambda wav, **kw: b"ANGEPASST")
    monkeypatch.setenv("TON_VORLADEN", "1")
    monkeypatch.setattr(builtins, "__import__", hook)

    def zuruecksetzen():
        td._modell = None
        td._torchaudio = None
    server = _server()
    try:
        # Vorladen
        zuruecksetzen()
        td.vorladen_starten().join(10)
        assert td.modell_zustand() == "geladen"
        vorladen = list(gesehen)
        assert vorladen and all(lock for _, lock in vorladen), vorladen
        # jeder Anfrageweg, jeweils mit leerem Zustand
        for pfad, nutzlast in (("/satz", {"text": "Hallo."}), ("/sprechen", {"text": "Hallo."}),
                               ("/voiceover", {"text": "Hallo.", "duration_frames": 30, "fps_num": 30,
                                               "fps_den": 1, "sample_rate": 24000})):
            zuruecksetzen()
            gesehen.clear()
            status, _, _ = _anfrage(server, pfad, nutzlast)
            assert status == 200, (pfad, status)
            namen = {n for n, _ in gesehen}
            assert "chatterbox.mtl_tts" in namen and "torchaudio" in namen, (pfad, namen)
            assert all(lock for _, lock in gesehen), (pfad, gesehen)
    finally:
        monkeypatch.setattr(builtins, "__import__", echt)
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("pfad, nutzlast", [
    ("/sprechen", {"text": "Hallo."}),
    ("/voiceover", {"text": "Hallo.", "duration_frames": 30, "fps_num": 30, "fps_den": 1, "sample_rate": 24000}),
])
def test_sprechen_und_voiceover_503_waehrend_des_ladens(fake, monkeypatch, pfad, nutzlast):
    fake.schranke = threading.Event()
    server = _server()
    vorladen = threading.Thread(target=td._modell_laden)
    vorladen.start()
    try:
        _warte_bis_laedt()
        monkeypatch.setattr(td, "LADEN_WARTEN_S", 0.1)
        status, kopf, rumpf = _anfrage(server, pfad, nutzlast)
        assert status == 503 and kopf["Retry-After"] == "30" and b"KeyError" not in rumpf
    finally:
        fake.schranke.set()
        vorladen.join(10)
        server.shutdown()
        server.server_close()


def test_halb_importiertes_torch_ist_defekt_503_und_prozessende(fake, monkeypatch, caplog):
    monkeypatch.delitem(sys.modules, "torch", raising=False)          # Python hat torch entfernt ...
    monkeypatch.setitem(sys.modules, "torch._C", types.ModuleType("torch._C"))   # ... der Rest blieb
    ende = threading.Event()
    monkeypatch.setattr(td, "_beenden", lambda: ende.set())
    monkeypatch.setattr(td, "DEFEKT_VERZUG_S", 0.05)
    monkeypatch.setattr(td, "LADEN_WARTEN_S", 10.0)
    fake.schranke = threading.Event()
    fake.fehler = [ImportError("cannot import name tqdm geheim")]
    ergebnis = []

    def lader():
        try:
            td._modell_laden()
        except Exception as e:  # noqa: BLE001
            ergebnis.append(type(e).__name__)
    t = threading.Thread(target=lader)
    server = _server()
    try:
        with caplog.at_level(logging.INFO, logger=td.LOG.name):
            t.start()
            _warte_bis_laedt()
            antwort = []
            w = threading.Thread(target=lambda: antwort.append(_anfrage(server, "/satz", {"text": "Hallo."})))
            w.start()
            time.sleep(0.2)              # die Anfrage steht jetzt hinter dem Lock
            fake.schranke.set()
            t.join(10)
            w.join(15)
        assert ergebnis == ["ModellDefekt"]
        status, kopf, rumpf = antwort[0]
        assert status == 503 and kopf["Retry-After"] == "60" and b"neu starten" in rumpf
        assert ende.wait(2), "Prozessende wurde nicht angefordert"
        assert td.modell_zustand() == "defekt" and fake.aufbauten == 1       # die Wartende hat NICHT erneut geladen
        _, _, hz = _anfrage(server, "/healthz")
        assert json.loads(hz) == {"ok": True, "modell": "defekt"}
        with pytest.raises(td.ModellDefekt):
            td._modell_laden()
        assert fake.aufbauten == 1
        zeilen = [r for r in caplog.records if "fehlgeschlagen" in r.getMessage()]
        assert len(zeilen) == 1 and "ImportError" in zeilen[0].getMessage() and "geheim" not in caplog.text
    finally:
        fake.schranke.set()
        server.shutdown()
        server.server_close()


def test_vollstaendiges_torch_bei_ladefehler_ist_nicht_defekt(fake, monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", types.ModuleType("torch"))
    monkeypatch.setattr(td, "_beenden", lambda: pytest.fail("darf nicht beenden"))
    fake.fehler = [MemoryError("os error 1455")]
    with pytest.raises(MemoryError):
        td._modell_laden()
    assert td.modell_zustand() == "nicht-geladen"


def test_abkuehlzeit_nach_gescheitertem_laden(fake, monkeypatch):
    fake.fehler = [RuntimeError("weg")]
    with pytest.raises(RuntimeError):
        td._modell_laden()
    with pytest.raises(td.ModellLaedt):          # sofort danach: kein weiterer Ladeversuch
        td._modell_laden()
    assert fake.aufbauten == 1
    monkeypatch.setattr(td, "_letzter_fehler", time.monotonic() - td.ABKUEHLEN_S - 1)
    _fake_module_eintragen(monkeypatch)          # der Fehlschlag hat chatterbox* verworfen
    assert isinstance(td._modell_laden(), _Modell) and fake.aufbauten == 2


def test_gesamtbudget_je_anfrage(fake, monkeypatch):
    """Eine Anfrage wartet insgesamt nicht laenger als LADEN_WARTEN_S, auch wenn mehrere Schritte warten."""
    fake.schranke = threading.Event()
    vorladen = threading.Thread(target=td._modell_laden)
    vorladen.start()
    _warte_bis_laedt()
    monkeypatch.setattr(td, "_torchaudio_holen", fake.torchaudio_echt)
    monkeypatch.setattr(td, "LADEN_WARTEN_S", 0.3)
    beginn = time.monotonic()
    try:
        with pytest.raises(td.ModellLaedt):
            td._sprechen_chatterbox("Hallo", "de", None)
        assert time.monotonic() - beginn < 1.5
    finally:
        fake.schranke.set()
        vorladen.join(10)
