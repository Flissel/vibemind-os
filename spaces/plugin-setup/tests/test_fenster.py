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


def test_referenz_wird_escapiert_in_seite_fuer():
    """Bösartige Referenz-Namen (mit < > &) müssen escapiert werden."""
    a = anfragen.anlegen("proj", "demo", "<script>alert(1)</script>", "bearer", "")
    html = fenster.seite_fuer(a)
    # Raw markup darf NICHT im HTML sein
    assert "<script>alert(1)</script>" not in html
    # Escapierte Form MUSS im HTML sein
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_referenz_wird_escapiert_in_ergebnisseite():
    """Bösartige Referenz-Namen müssen auch in der Ergebnisseite escapiert sein."""
    html_ok = fenster.ergebnisseite(True, "<script>alert(1)</script>", "")
    assert "<script>alert(1)</script>" not in html_ok
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_ok

    html_fail = fenster.ergebnisseite(False, "<img src=x onerror=alert(1)>", "test")
    assert "<img src=x onerror=alert(1)>" not in html_fail
    assert "&lt;img src=x onerror=alert(1)&gt;" in html_fail


def test_statuscode_wird_angezeigt_nicht_der_fehlertext():
    """Der Status-Code selber muss in der Antwort stehen, nicht nur der Fehlertext."""
    def _schreiber_mit_code(**kwargs):
        # Fehler-Meldung mit ANDEREN Ziffern als Status, damit der Test wirklich
        # beweist dass der Status-Code angezeigt wird
        return {
            "ok": False,
            "referenz": kwargs["referenz"],
            "status": 403,
            "fehler": "Zugriff verweigert (Code: neunundzwanzig)"
        }

    a = anfragen.anlegen("proj", "demo", "SECRET", "bearer", "")
    status, html = fenster.entgegennehmen(a.token, _FAKE, _schreiber_mit_code)
    assert status == 200
    # Der Status-Code selber muss sichtbar sein
    assert "403" in html
    # Der Fehlertext mit seinen anderen Ziffern/Text darf NICHT sichtbar sein
    assert "neunundzwanzig" not in html
    assert "Zugriff verweigert" not in html


def test_oauth_seite_hat_keine_password_eingabe():
    """OAuth-Flow sollte einen Button haben, keine Passwort-Eingabe."""
    a = anfragen.anlegen("proj", "demo", "OAUTH_CODE", "oauth", "")
    html = fenster.seite_fuer(a)
    # Keine Password-Eingabe
    assert 'type="password"' not in html
    # Aber die Referenz sollte trotzdem sichtbar sein
    assert "OAUTH_CODE" in html
    # Und es sollte einen Button geben
    assert "<button>" in html or "<button " in html
