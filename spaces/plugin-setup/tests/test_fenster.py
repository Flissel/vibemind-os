"""Das Formular: nimmt den Wert entgegen und gibt ihn nirgends wieder her."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import anfragen  # noqa: E402
import fenster  # noqa: E402

_FAKE = "offensichtlich-erfunden-kein-echtes-secret-fenster"


def _schreiber_ok(**kwargs):
    _schreiber_ok.gesehen = kwargs
    return {"ok": True, "referenz": kwargs["referenz"]}


def _schreiber_fehl(**kwargs):
    return {"ok": False, "referenz": kwargs["referenz"], "status": 401,
            "fehler": "Verifikation fehlgeschlagen: 401"}


def test_seite_zeigt_die_referenz_und_niemals_einen_wert():
    a = anfragen.anlegen("proj", "demo", "GITHUB_PAT_TOKEN", "bearer", "")
    html = fenster.seite_fuer(a)
    assert "GITHUB_PAT_TOKEN" in html
    assert 'method="post"' in html.lower()
    assert "type=\"password\"" in html
    assert _FAKE not in html


def test_entgegennehmen_reicht_genau_die_felder_der_anfrage_durch():
    a = anfragen.anlegen("proj-x", "demo-plugin", "GITHUB_PAT_TOKEN", "bearer", "")
    status, html = fenster.entgegennehmen(a.token, _FAKE, _schreiber_ok)
    assert status == 200
    assert _schreiber_ok.gesehen == {
        "projekt": "proj-x", "plugin": "demo-plugin",
        "referenz": "GITHUB_PAT_TOKEN", "art": "bearer",
        "wert": _FAKE, "ziel": "",
    }


def test_der_wert_steht_in_keiner_antwort():
    a = anfragen.anlegen("proj", "demo", "GITHUB_PAT_TOKEN", "bearer", "")
    _, html = fenster.entgegennehmen(a.token, _FAKE, _schreiber_ok)
    assert _FAKE not in html
    b = anfragen.anlegen("proj", "demo", "GITHUB_PAT_TOKEN2", "bearer", "")
    _, html2 = fenster.entgegennehmen(b.token, _FAKE, _schreiber_fehl)
    assert _FAKE not in html2


def test_fehlschlag_zeigt_den_statuscode_und_keinen_antwortkoerper():
    a = anfragen.anlegen("proj", "demo", "GITHUB_PAT_TOKEN", "bearer", "")
    status, html = fenster.entgegennehmen(a.token, _FAKE, _schreiber_fehl)
    assert status == 200
    assert "401" in html


def test_token_ist_einmalig():
    a = anfragen.anlegen("proj", "demo", "GITHUB_PAT_TOKEN", "bearer", "")
    fenster.entgegennehmen(a.token, _FAKE, _schreiber_ok)
    status, html = fenster.entgegennehmen(a.token, _FAKE, _schreiber_ok)
    assert status == 404


def test_unbekanntes_token_gibt_404_ohne_den_wert_anzufassen():
    gerufen = []
    status, _ = fenster.entgegennehmen("gibt-es-nicht", _FAKE,
                                       lambda **k: gerufen.append(k))
    assert status == 404
    assert gerufen == []
