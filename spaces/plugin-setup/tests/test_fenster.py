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


def test_oauth_seite_sagt_ehrlich_dass_der_token_nie_im_fenster_erscheint():
    """Aufgabe 6: der Provisioner ist jetzt angebunden, der Disclaimer der
    Vorgaengerfassung ("noch nicht angebunden") ist also nicht mehr wahr --
    ohne diesen Test waere das stille Wegfallen JEDER Zusicherung an dieser
    Stelle eine unbemerkte Mutation. Die neue Zusicherung: der beschaffte
    Token erscheint in keiner Antwort dieses Fensters (s. server.py,
    _fenster_annehmen -> fenster.oauth_entgegennehmen)."""
    a = anfragen.anlegen("proj", "demo", "OAUTH_DISCLAIMER", "oauth", "")
    html = fenster.seite_fuer(a)
    assert "erscheint in keiner Antwort" in html


def test_oauth_reicht_den_beschafften_token_durch_ohne_ihn_zu_zeigen():
    a = anfragen.anlegen("proj", "demo", "OAUTH_BEARER_MCP_LINEAR_APP_MCP",
                         "oauth", "https://mcp.linear.app/mcp")
    erfunden = "offensichtlich-erfunden-oauth-token"

    def beschaffer(mcp_url):
        assert mcp_url == "https://mcp.linear.app/mcp"
        return "OAUTH_BEARER_MCP_LINEAR_APP_MCP", erfunden

    gesehen = {}

    def schreiber(**kwargs):
        gesehen.update(kwargs)
        return {"ok": True, "referenz": kwargs["referenz"]}

    status, html = fenster.oauth_entgegennehmen(a.token, beschaffer, schreiber)
    assert status == 200
    assert gesehen["wert"] == erfunden
    assert gesehen["art"] == "oauth"
    assert erfunden not in html


def test_oauth_fehlschlag_zeigt_keinen_token_und_keine_ursache_im_klartext():
    a = anfragen.anlegen("proj", "demo", "OAUTH_BEARER_MCP_LINEAR_APP_MCP",
                         "oauth", "https://mcp.linear.app/mcp")

    def beschaffer(mcp_url):
        raise RuntimeError("offensichtlich-erfunden-oauth-token im Fehlertext")

    status, html = fenster.oauth_entgegennehmen(a.token, beschaffer, lambda **k: {"ok": True})
    assert status == 200
    assert "offensichtlich-erfunden-oauth-token" not in html


def test_oauth_seite_zeigt_das_ziel_html_escaped():
    """F3 (W7, Schluss-Fix): `ziel` ist agentenverfasst und bindet die
    `authorize_url`, die `webbrowser.open()` im echten Browser des
    Betreibers oeffnet. Ohne diese Anzeige klickt der Betreiber blind auf
    eine Adresse, die er nie gesehen hat -- die Seite muss sie zeigen,
    escaped wie jede andere agentenverfasste Zeichenkette in diesem Space."""
    boesartig = "https://evil.example/mcp?x=<script>alert(1)</script>&y=1"
    a = anfragen.anlegen("proj", "demo", "OAUTH_ZIEL_TEST", "oauth", boesartig)
    html = fenster.seite_fuer(a)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    # Die Adresse selbst (escaped) muss sichtbar sein, nicht nur die Referenz.
    assert "evil.example" in html


def test_ergebnisseite_zwei_verwahrstellen_nennt_die_abhilfe_und_nicht_die_falschen_saetze():
    """F2 (W2, Schluss-Fix): der schlimmste Fall -- OpenFang hat den Wert
    schon uebernommen, der Supabase-Uebergang ist endgueltig gescheitert.
    Die drei Saetze, die fuer jeden ANDEREN Fehlschlag richtig sind, sind
    hier alle drei falsch und duerfen nicht erscheinen; die tatsaechliche
    Abhilfe (Supabase-Uebergang erneut ausloesen, NICHT das Formular erneut
    absenden) muss erscheinen."""
    def _schreiber_zwei_verwahrstellen(**kwargs):
        return {"ok": False, "referenz": kwargs["referenz"],
                "zwei_verwahrstellen": True, "retryable": False,
                "fehler": "ZWEI VERWAHRSTELLEN: ... (enthaelt keinen Wert)"}

    a = anfragen.anlegen("proj", "demo", "GITHUB_PAT_TOKEN", "bearer", "")
    status, html = fenster.entgegennehmen(a.token, _FAKE, _schreiber_zwei_verwahrstellen)
    assert status == 200
    # (a) die Abhilfe wird genannt
    assert "Supabase" in html
    assert "NICHT erneut absenden" in html
    # (b) die drei falschen Aussagen fuer den Normalfall duerfen NICHT stehen
    assert "vom Anbieter abgelehnt" not in html
    assert "Der Wert wurde nicht uebergeben" not in html
    assert "Der Agent kann eine neue Eingabe anfordern" not in html
    # weiterhin gilt: kein Wert in der Antwort
    assert _FAKE not in html


def test_ergebnisseite_erfordert_betreiber_entscheidung_nennt_die_kollision_und_nicht_die_falschen_saetze():
    """N4: seit dem N1-Fix (`588384f8`) ist der 409-/`erfordert_betreiber_
    entscheidung`-Pfad ERSTMALS erreichbar -- vorher war er toter Code und
    trug ohnehin den (dann folgenlosen) Normalfall-Text. Dieselben drei
    Saetze, die fuer `zwei_verwahrstellen` falsch sind, sind auch hier
    falsch, aus einem anderen Grund: es hat niemand etwas abgelehnt, es
    liegt eine Kollision bei OpenFang vor (die Referenz ist dort schon
    belegt), und die braucht eine Entscheidung des Betreibers, kein
    einfaches Wiederholen."""
    def _schreiber_kollision(**kwargs):
        return {"ok": False, "referenz": kwargs["referenz"],
                "erfordert_betreiber_entscheidung": True, "retryable": False,
                "fehler": "OpenFang meldet 409 (reference_exists) (enthaelt keinen Wert)"}

    a = anfragen.anlegen("proj", "demo", "GITHUB_PAT_TOKEN", "bearer", "")
    status, html = fenster.entgegennehmen(a.token, _FAKE, _schreiber_kollision)
    assert status == 200
    # (a) der richtige Hinweis: Kollision bei OpenFang, Betreiber muss entscheiden
    assert "bereits" in html
    assert "Betreiber" in html
    # (b) die drei falschen Aussagen des Normalfalls duerfen NICHT stehen
    assert "vom Anbieter abgelehnt" not in html
    assert "Der Wert wurde nicht uebergeben" not in html
    assert "Der Agent kann eine neue Eingabe anfordern" not in html
    assert _FAKE not in html


def test_oauth_entgegennehmen_erfordert_betreiber_entscheidung_ebenfalls_richtig_benannt():
    """Derselbe `ergebnis`-Vertrag gilt fuer den oauth-Zweig -- ein per
    OAuth beschaffter Token kann genauso an der OpenFang-409-Kollision
    scheitern wie ein per Formular eingetragener Wert."""
    erfunden = "offensichtlich-erfunden-oauth-token-kollision"

    def beschaffer(mcp_url):
        return "OAUTH_X", erfunden

    def schreiber(**kwargs):
        return {"ok": False, "referenz": kwargs["referenz"],
                "erfordert_betreiber_entscheidung": True, "retryable": False,
                "fehler": "OpenFang meldet 409 (reference_exists) (enthaelt keinen Wert)"}

    a = anfragen.anlegen("proj", "demo", "OAUTH_X", "oauth", "https://mcp.linear.app/mcp")
    status, html = fenster.oauth_entgegennehmen(a.token, beschaffer, schreiber)
    assert status == 200
    assert "bereits" in html
    assert "Betreiber" in html
    assert "vom Anbieter abgelehnt" not in html
    assert "Der Wert wurde nicht uebergeben" not in html
    assert "Der Agent kann eine neue Eingabe anfordern" not in html
    assert erfunden not in html


def test_oauth_entgegennehmen_zwei_verwahrstellen_ebenfalls_richtig_benannt():
    """Derselbe `ergebnis`-Vertrag gilt fuer den oauth-Zweig -- der
    beschaffte Token kann genauso in OpenFang landen und danach am
    Supabase-Uebergang scheitern wie ein per Formular eingetragener Wert."""
    erfunden = "offensichtlich-erfunden-oauth-token-verwahrstellen"

    def beschaffer(mcp_url):
        return "OAUTH_X", erfunden

    def schreiber(**kwargs):
        return {"ok": False, "referenz": kwargs["referenz"],
                "zwei_verwahrstellen": True, "retryable": False,
                "fehler": "ZWEI VERWAHRSTELLEN: ... (enthaelt keinen Wert)"}

    a = anfragen.anlegen("proj", "demo", "OAUTH_X", "oauth", "https://mcp.linear.app/mcp")
    status, html = fenster.oauth_entgegennehmen(a.token, beschaffer, schreiber)
    assert status == 200
    assert "Supabase" in html
    assert "vom Anbieter abgelehnt" not in html
    assert "Der Wert wurde nicht uebergeben" not in html
    assert "Der Agent kann eine neue Eingabe anfordern" not in html
    assert erfunden not in html
