"""dienst_bereit (Ton-Arbeiter) und stimme_bereit (Werkbank): /healthz des Sprech-Dienstes mit Modellzustand."""
import urllib.error

import pytest

from spaces.marketing.workers import ton_worker, video_stimme


class _Antwort:
    def __init__(self, status, rumpf):
        self.status, self._rumpf = status, rumpf

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._rumpf


FAELLE = [
    (b'{"ok": true, "modell": "geladen"}', True),
    (b'{"ok": true, "modell": "nicht-geladen"}', True),      # Laden bei Bedarf: der erste Auftrag laedt
    (b'{"ok": true, "modell": "laedt"}', False),
    (b'{"ok": true, "modell": "defekt"}', False),
    (b"ok", True),                                           # alter Dienst
]


@pytest.mark.parametrize("modul, name", [(ton_worker, "dienst_bereit"), (video_stimme, "stimme_bereit")])
@pytest.mark.parametrize("rumpf, erwartet", FAELLE)
def test_bereit_je_modellzustand(monkeypatch, modul, name, rumpf, erwartet):
    monkeypatch.setattr(modul.urllib.request, "urlopen", lambda url, timeout=None: _Antwort(200, rumpf))
    assert getattr(modul, name)() is erwartet


@pytest.mark.parametrize("modul, name", [(ton_worker, "dienst_bereit"), (video_stimme, "stimme_bereit")])
def test_nicht_erreichbar_oder_kein_200_ist_nicht_bereit(monkeypatch, modul, name):
    def weg(url, timeout=None):
        raise urllib.error.URLError("weg")
    monkeypatch.setattr(modul.urllib.request, "urlopen", weg)
    assert getattr(modul, name)() is False
    monkeypatch.setattr(modul.urllib.request, "urlopen", lambda url, timeout=None: _Antwort(500, b"ok"))
    assert getattr(modul, name)() is False
