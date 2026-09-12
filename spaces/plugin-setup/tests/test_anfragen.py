"""Die schwebende Anfrage -- reines Prozessgedaechtnis, kein Wert, keine DB."""
from __future__ import annotations

import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import anfragen  # noqa: E402


def test_anfrage_hat_strukturell_kein_wertfeld():
    """Die staerkste Zusicherung dieses Moduls: es GIBT keinen Ort, an dem
    ein Wert liegen koennte. Nicht 'wir schreiben keinen hinein'."""
    a = anfragen.anlegen("p", "demo", "REF_A", "bearer", "")
    felder = {f.name for f in dataclasses.fields(a)}
    assert felder == {"token", "projekt", "plugin", "referenz", "art", "ziel", "ablauf"}


def test_token_ist_nicht_erratbar_und_je_anfrage_verschieden():
    a = anfragen.anlegen("p", "demo", "REF_B", "bearer", "")
    b = anfragen.anlegen("p", "demo", "REF_C", "bearer", "")
    assert a.token != b.token
    assert len(a.token) >= 32


def test_holen_findet_die_anfrage_und_verbrauchen_entfernt_sie():
    a = anfragen.anlegen("p", "demo", "REF_D", "bearer", "")
    assert anfragen.holen(a.token).referenz == "REF_D"
    assert anfragen.verbrauchen(a.token).referenz == "REF_D"
    assert anfragen.holen(a.token) is None, "ein verbrauchtes Token ist tot"
    assert anfragen.verbrauchen(a.token) is None


def test_abgelaufene_anfrage_ist_nicht_mehr_auffindbar(monkeypatch):
    monkeypatch.setattr(anfragen, "GUELTIGKEIT_SEKUNDEN", 0)
    a = anfragen.anlegen("p", "demo", "REF_E", "bearer", "")
    time.sleep(0.01)
    assert anfragen.holen(a.token) is None
    assert anfragen.verbrauchen(a.token) is None


def test_unbekanntes_token_gibt_none():
    assert anfragen.holen("gibt-es-nicht") is None


def test_offen_fuer_findet_die_schwebende_anfrage_einer_referenz():
    a = anfragen.anlegen("p", "demo", "REF_F", "bearer", "")
    assert anfragen.offen_fuer("REF_F").token == a.token
    anfragen.verbrauchen(a.token)
    assert anfragen.offen_fuer("REF_F") is None
