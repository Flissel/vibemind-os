"""Aufgabe 5 - die Verifikation eines Credentials beim Anbieter: das Tor,
das verhindert, dass OpenFang je einen Blindgaenger uebernimmt (D3).

Alles hier laeuft gegen ein eingespritztes `fetch` -- kein echter
Netzwerkaufruf in der Suite. Der eine echte Lauf (Beleg, dass die Aufrufform
stimmt) wird separat ausgefuehrt und im Bericht dokumentiert, nicht als Test.

Regeln, die diese Datei selbst befolgt (siehe Global Constraints im Brief):
  - Keine echten Geheimniswerte, nur ein offensichtlich erfundener String.
  - Der Wert wird nie geprintet und nie in eine Assertion-Nachricht gehaengt.
"""
from __future__ import annotations

import json
import logging

import pytest

from pruefung import pruefe

# Offensichtlich erfunden -- nie ein echtes Secret.
_FAKE_WERT = "offensichtlich-erfunden-kein-echtes-secret-a1b2c3"


class _FakeFetch:
    """Eingespritztes fetch: zeichnet jeden Aufruf auf, antwortet aus einer
    festen Warteschlange von Ergebnissen (Status-Code oder Exception)."""

    def __init__(self, *ergebnisse):
        self._ergebnisse = list(ergebnisse)
        self.aufrufe: list[dict] = []

    def __call__(self, method, url, *, headers, body, timeout):
        self.aufrufe.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body, "timeout": timeout}
        )
        ergebnis = self._ergebnisse.pop(0)
        if isinstance(ergebnis, Exception):
            raise ergebnis
        return ergebnis


# --- Schritt 1: die fuenf verlangten Faelle -------------------------------


def test_200_ist_gut():
    fetch = _FakeFetch(200)
    ergebnis = pruefe("bearer", "GITHUB_PAT_TOKEN", _FAKE_WERT, "https://api.github.com/user", fetch=fetch)
    assert ergebnis == {"gut": True, "status": 200}


def test_401_ist_nicht_gut_status_wird_durchgereicht():
    fetch = _FakeFetch(401)
    ergebnis = pruefe("bearer", "GITHUB_PAT_TOKEN", _FAKE_WERT, "https://api.github.com/user", fetch=fetch)
    assert ergebnis == {"gut": False, "status": 401}


def test_zeitueberschreitung_ist_nicht_gut_und_stuerzt_nicht_ab():
    fetch = _FakeFetch(TimeoutError("timed out"))
    # Wirft nichts -- das ist die Zusicherung von "kein Absturz".
    ergebnis = pruefe("bearer", "GITHUB_PAT_TOKEN", _FAKE_WERT, "https://api.github.com/user", fetch=fetch)
    assert ergebnis["gut"] is False
    assert isinstance(ergebnis["status"], int)


def test_wert_taucht_in_keinem_rueckgabefeld_und_keinem_protokoll_auf(caplog, capsys):
    """Die eigentliche Zusicherung der Aufgabe: der Wert reist zum Anbieter
    (im injizierten fetch-Aufruf steht er im Authorization-Header -- das ist
    der einzige Ort, an dem er hingehoert), aber er kommt nirgendwo sonst
    wieder heraus. Deckt beide Leckpfade ab: `logging` (caplog) UND
    stdout/stderr (capsys) -- ein kuenftiges `print(f"... {wert}")` wuerde
    an caplog allein vorbeirutschen."""
    fetch = _FakeFetch(401)
    with caplog.at_level(logging.DEBUG):
        ergebnis = pruefe("bearer", "GITHUB_PAT_TOKEN", _FAKE_WERT, "https://api.github.com/user", fetch=fetch)

    # 1. Er ist tatsaechlich beim Anbieter angekommen (sonst waere die
    #    Pruefung wirkungslos) -- im Request an fetch, nicht im Rueckgabewert.
    gesendete_header = fetch.aufrufe[0]["headers"]
    assert any(_FAKE_WERT in wert for wert in gesendete_header.values())

    # 2. Er steckt in KEINEM Feld des Rueckgabewerts.
    rueckgabe_text = json.dumps(ergebnis)
    assert _FAKE_WERT not in rueckgabe_text
    assert set(ergebnis.keys()) == {"gut", "status"}

    # 3. Er steckt in KEINER Protokollzeile (dieses Modul loggt ueberhaupt
    #    nichts -- das ist der Beweis dafuer, per capture statt Kommentar).
    assert _FAKE_WERT not in caplog.text

    # 4. Er steckt in KEINER stdout/stderr-Ausgabe -- die andere Haelfte des
    #    Beweises, die caplog allein nicht abdeckt (print statt logging).
    erfasst = capsys.readouterr()
    assert _FAKE_WERT not in erfasst.out
    assert _FAKE_WERT not in erfasst.err


def test_genau_ein_versuch_kein_wiederholen():
    fetch = _FakeFetch(401)
    pruefe("bearer", "GITHUB_PAT_TOKEN", _FAKE_WERT, "https://api.github.com/user", fetch=fetch)
    assert len(fetch.aufrufe) == 1

    fetch_bei_timeout = _FakeFetch(TimeoutError("timed out"))
    pruefe("bearer", "GITHUB_PAT_TOKEN", _FAKE_WERT, "https://api.github.com/user", fetch=fetch_bei_timeout)
    assert len(fetch_bei_timeout.aufrufe) == 1


# --- Dispatch je Pruefform, gegen die D3-Tabelle ---------------------------


def test_bearer_ruft_github_user_ab():
    fetch = _FakeFetch(200)
    pruefe("bearer", "GITHUB_PAT_TOKEN", _FAKE_WERT, "https://api.github.com/user", fetch=fetch)
    aufruf = fetch.aufrufe[0]
    assert aufruf["method"] == "GET"
    assert aufruf["url"] == "https://api.github.com/user"
    assert aufruf["headers"]["Authorization"] == f"Bearer {_FAKE_WERT}"


def test_oauth_ruft_mcp_initialize_gegen_die_ressource_auf():
    fetch = _FakeFetch(200)
    ziel = "https://mcp.example.com/mcp"
    pruefe("oauth", "OAUTH_BEARER_MCP_EXAMPLE_COM", _FAKE_WERT, ziel, fetch=fetch)
    aufruf = fetch.aufrufe[0]
    assert aufruf["method"] == "POST"
    assert aufruf["url"] == ziel
    assert aufruf["headers"]["Authorization"] == f"Bearer {_FAKE_WERT}"
    body = json.loads(aufruf["body"])
    assert body["method"] == "initialize"


def test_connector_ruft_responses_api_mit_connector_id_auf(monkeypatch):
    monkeypatch.delenv("OPENAI_RESPONSES_MODEL", raising=False)
    fetch = _FakeFetch(200)
    pruefe("connector", "CONNECTOR_CANVA", _FAKE_WERT, "connector_canva", fetch=fetch)
    aufruf = fetch.aufrufe[0]
    assert aufruf["method"] == "POST"
    assert aufruf["url"] == "https://api.openai.com/v1/responses"
    assert aufruf["headers"]["Authorization"] == f"Bearer {_FAKE_WERT}"
    body = json.loads(aufruf["body"])
    assert body["tools"][0]["connector_id"] == "connector_canva"

    # Review-Fix: kein totverdrahtetes Modell ohne Guthaben. Ohne env-Var
    # gilt derselbe Default, den Rowboats DI-Schicht schon verwendet
    # (apps/rowboat/di/plugins-container.ts, OPENAI_RESPONSES_MODEL).
    assert body["model"] == "gpt-5.6"
    # So wenig wie moeglich erzeugen -- eine echte Anfrage, kein Auth-Check.
    assert body["max_output_tokens"] == 1


def test_connector_liest_modell_aus_der_umgebung(monkeypatch):
    monkeypatch.setenv("OPENAI_RESPONSES_MODEL", "ein-anderes-modell")
    fetch = _FakeFetch(200)
    pruefe("connector", "CONNECTOR_CANVA", _FAKE_WERT, "connector_canva", fetch=fetch)
    body = json.loads(fetch.aufrufe[0]["body"])
    assert body["model"] == "ein-anderes-modell"


def test_unbekannte_pruefform_ist_fail_closed():
    fetch = _FakeFetch()
    ergebnis = pruefe("irgendwas", "X", _FAKE_WERT, "https://example.com", fetch=fetch)
    assert ergebnis == {"gut": False, "status": 0}
    assert fetch.aufrufe == []
