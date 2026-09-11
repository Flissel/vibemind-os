"""Aufgabe 6 -- die vier MCP-Werkzeuge des plugin-setup-Space.

Alles hier laeuft gegen eingespritzte/monkeypatchte Ersatzfunktionen: kein
echter Netzaufruf, kein echter docker-exec/psql (ablage.py wird komplett
gemockt). Das Zusammenspiel Supabase -> Verifikation -> OpenFang wird
geprueft, nicht die einzelnen Bausteine selbst (die haben ihre eigenen
Tests: test_eingang.py, test_pruefung.py, test_ablage.py).

Regeln, die diese Datei selbst befolgt (Global Constraints im Brief):
  - Keine echten Geheimniswerte, nur offensichtlich erfundene Test-Strings.
  - Kein Wert wird je geprintet oder in eine Assertion-Nachricht gehaengt.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import werkzeuge

_FAKE_WERT = "offensichtlich-erfunden-kein-echtes-secret-w1"


def _alle_werte_in(objekt) -> list:
    """Rekursiv alle string-Werte eines JSON-artigen Objekts einsammeln --
    fuer die Verbotsliste: keiner davon darf je _FAKE_WERT sein/enthalten."""
    gefunden = []
    if isinstance(objekt, dict):
        for v in objekt.values():
            gefunden.extend(_alle_werte_in(v))
    elif isinstance(objekt, list):
        for v in objekt:
            gefunden.extend(_alle_werte_in(v))
    elif isinstance(objekt, str):
        gefunden.append(objekt)
    return gefunden


class _Aufrufe:
    """Zeichnet Aufrufe eines gemockten Callables auf."""

    def __init__(self, ergebnisse=None, ergebnis=None):
        self._ergebnisse = list(ergebnisse) if ergebnisse is not None else None
        self._ergebnis = ergebnis
        self.aufrufe: list[tuple] = []

    def __call__(self, *args, **kwargs):
        self.aufrufe.append((args, kwargs))
        if self._ergebnisse is not None:
            return self._ergebnisse.pop(0)
        return self._ergebnis


# ─── Schritt 1: die Verbotsliste ───────────────────────────────────────────


def test_schluessel_entgegennehmen_erfolg_gibt_wert_nirgends_zurueck(monkeypatch, caplog, capsys):
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen",
                        _Aufrufe(ergebnis={"ok": True, "referenz": "X"}))
    monkeypatch.setattr(werkzeuge, "pruefe",
                        _Aufrufe(ergebnis={"gut": True, "status": 200}))
    monkeypatch.setattr(werkzeuge, "_openfang_uebernehmen",
                        _Aufrufe(ergebnis={"ok": True, "status": 200}))
    monkeypatch.setattr(werkzeuge.ablage, "verifizieren",
                        _Aufrufe(ergebnis={"ok": True}))
    monkeypatch.setattr(werkzeuge.ablage, "uebernommen",
                        _Aufrufe(ergebnis={"ok": True}))

    ergebnis = werkzeuge.schluessel_entgegennehmen(
        "proj", "demo-plugin", "X", "bearer", _FAKE_WERT)

    assert ergebnis == {"ok": True, "referenz": "X"}
    assert _FAKE_WERT not in caplog.text
    erfasst = capsys.readouterr()
    assert _FAKE_WERT not in erfasst.out
    assert _FAKE_WERT not in erfasst.err


def test_schluessel_entgegennehmen_fehlschlag_gibt_wert_nirgends_zurueck(monkeypatch, caplog, capsys):
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen",
                        _Aufrufe(ergebnis={"ok": True, "referenz": "X"}))
    monkeypatch.setattr(werkzeuge, "pruefe",
                        _Aufrufe(ergebnis={"gut": False, "status": 401}))
    fehlschlagen = _Aufrufe(ergebnis={"ok": True})
    monkeypatch.setattr(werkzeuge.ablage, "fehlschlagen", fehlschlagen)

    ergebnis = werkzeuge.schluessel_entgegennehmen(
        "proj", "demo-plugin", "X", "bearer", _FAKE_WERT)

    assert ergebnis["ok"] is False
    for text in _alle_werte_in(ergebnis):
        assert _FAKE_WERT not in text
    assert _FAKE_WERT not in caplog.text
    erfasst = capsys.readouterr()
    assert _FAKE_WERT not in erfasst.out
    assert _FAKE_WERT not in erfasst.err

    # ablage.fehlschlagen bekommt den Statuscode als String, nie `wert`.
    assert fehlschlagen.aufrufe[0][0] == ("X", "401")


def test_alle_vier_werkzeuge_geben_nie_ein_feld_namens_wert_zurueck(monkeypatch):
    """Verbotsliste als Formcheck: kein Rueckgabeobjekt traegt je ein Feld
    `wert`/`value`/`secret` -- unabhaengig vom Inhalt."""
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen",
                        _Aufrufe(ergebnis={"ok": True, "referenz": "X"}))
    monkeypatch.setattr(werkzeuge, "pruefe", _Aufrufe(ergebnis={"gut": True, "status": 200}))
    monkeypatch.setattr(werkzeuge, "_openfang_uebernehmen", _Aufrufe(ergebnis={"ok": True, "status": 200}))
    monkeypatch.setattr(werkzeuge.ablage, "verifizieren", _Aufrufe(ergebnis={"ok": True}))
    monkeypatch.setattr(werkzeuge.ablage, "uebernommen", _Aufrufe(ergebnis={"ok": True}))

    ergebnis = werkzeuge.schluessel_entgegennehmen("proj", "demo-plugin", "X", "bearer", _FAKE_WERT)
    verbotene_felder = {"wert", "value", "secret", "token_value"}
    assert verbotene_felder.isdisjoint(ergebnis.keys())


# ─── Reihenfolge: nichts an OpenFang ohne bestandene Verifikation ─────────


def test_bei_fehlgeschlagener_verifikation_wird_openfang_nie_aufgerufen(monkeypatch):
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen",
                        _Aufrufe(ergebnis={"ok": True, "referenz": "X"}))
    monkeypatch.setattr(werkzeuge, "pruefe", _Aufrufe(ergebnis={"gut": False, "status": 403}))
    openfang = _Aufrufe(ergebnis={"ok": True, "status": 200})
    monkeypatch.setattr(werkzeuge, "_openfang_uebernehmen", openfang)
    fehlschlagen = _Aufrufe(ergebnis={"ok": True})
    monkeypatch.setattr(werkzeuge.ablage, "fehlschlagen", fehlschlagen)
    uebernommen = _Aufrufe(ergebnis={"ok": True})
    monkeypatch.setattr(werkzeuge.ablage, "uebernommen", uebernommen)

    ergebnis = werkzeuge.schluessel_entgegennehmen("proj", "demo-plugin", "X", "bearer", _FAKE_WERT)

    assert ergebnis["ok"] is False
    assert openfang.aufrufe == [], "OpenFang darf ohne bestandene Verifikation nie aufgerufen werden"
    assert uebernommen.aufrufe == []
    assert fehlschlagen.aufrufe == [(("X", "403"), {})]


def test_supabase_kopie_bleibt_bei_fehlschlag_zur_fehlersuche_stehen(monkeypatch):
    """fehlschlagen() loescht den Vault-Eintrag NICHT (s. 0002_state_machine.sql)
    -- dieses Werkzeug muss also NIE `uebernommen()` aufrufen, wenn die
    Verifikation scheitert, sonst wuerde die Diagnosekopie verschwinden."""
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen",
                        _Aufrufe(ergebnis={"ok": True, "referenz": "X"}))
    monkeypatch.setattr(werkzeuge, "pruefe", _Aufrufe(ergebnis={"gut": False, "status": 500}))
    monkeypatch.setattr(werkzeuge.ablage, "fehlschlagen", _Aufrufe(ergebnis={"ok": True}))
    uebernommen = _Aufrufe(ergebnis={"ok": True})
    monkeypatch.setattr(werkzeuge.ablage, "uebernommen", uebernommen)

    werkzeuge.schluessel_entgegennehmen("proj", "demo-plugin", "X", "bearer", _FAKE_WERT)
    assert uebernommen.aufrufe == []


def test_aufnahme_scheitert_pruefung_wird_nie_aufgerufen(monkeypatch):
    """Scheitert schon die Supabase-Aufnahme (z.B. referenz doppelt
    vergeben), wird nicht einmal die Anbieter-Verifikation versucht --
    fail closed, kein halber Weg."""
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen",
                        _Aufrufe(ergebnis={"ok": False, "fehler": "referenz schon vergeben"}))
    pruefen = _Aufrufe(ergebnis={"gut": True, "status": 200})
    monkeypatch.setattr(werkzeuge, "pruefe", pruefen)

    ergebnis = werkzeuge.schluessel_entgegennehmen("proj", "demo-plugin", "X", "bearer", _FAKE_WERT)
    assert ergebnis == {"ok": False, "fehler": "referenz schon vergeben"}
    assert pruefen.aufrufe == []


def test_art_unbekannt_wird_abgelehnt_bevor_irgendetwas_in_supabase_landet(monkeypatch):
    """C2: `art="unbekannt"` (der jetzt-fail-closed-Default von
    `plugin_bedarf` fuer nicht ableitbare Namen) darf niemals bis zur
    Anbieter-Pruefung oder gar zu OpenFang durchkommen. Laeuft gegen die
    ECHTE `ablage.entgegennehmen` (kein Mock) -- ihre eigene Validierung
    ist die erste Instanz, die `art` prueft, VOR jedem SQL-Aufruf, also
    braucht dieser Test keine laufende Datenbank."""
    pruefen = _Aufrufe(ergebnis={"gut": True, "status": 200})
    monkeypatch.setattr(werkzeuge, "pruefe", pruefen)
    openfang = _Aufrufe(ergebnis={"ok": True, "status": 200})
    monkeypatch.setattr(werkzeuge, "_openfang_uebernehmen", openfang)

    ergebnis = werkzeuge.schluessel_entgegennehmen(
        "proj", "demo-plugin", "PYTEST_UNBEKANNTE_ART", "unbekannt", _FAKE_WERT)

    assert ergebnis["ok"] is False
    assert pruefen.aufrufe == [], "unbekannte art darf nie bis zur Anbieter-Pruefung kommen"
    assert openfang.aufrufe == [], "unbekannte art darf OpenFang nie erreichen"


@pytest.mark.parametrize("art", ["oauth", "connector"])
def test_ziel_pflicht_fuer_oauth_und_connector_lehnt_vor_jedem_schreibzugriff_ab(monkeypatch, art):
    """I3 (Review Runde 2): `werkzeuge.py:228-229` lehnt ein leeres `ziel`
    fuer `art in {oauth, connector}` ab -- aber ohne diesen Test haette
    jeder bestehende Ordnungstest weiter gruen geblieben, wenn genau diese
    zwei Zeilen geloescht wuerden (sie arbeiten alle mit `art="bearer"`
    oder `"unbekannt"`, fuer die `ziel` nie Pflicht ist). Dieser Test
    beweist beides: die Ablehnung selbst UND dass sie VOR jedem
    Schreibzugriff greift -- `ablage.entgegennehmen` wird nie aufgerufen."""
    aufnahme = _Aufrufe(ergebnis={"ok": True, "referenz": "X"})
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen", aufnahme)
    pruefen = _Aufrufe(ergebnis={"gut": True, "status": 200})
    monkeypatch.setattr(werkzeuge, "pruefe", pruefen)

    ergebnis = werkzeuge.schluessel_entgegennehmen(
        "proj", "demo-plugin", "X", art, _FAKE_WERT, ziel="")

    assert ergebnis["ok"] is False
    assert "ziel" in ergebnis["fehler"].lower()
    assert aufnahme.aufrufe == [], "ein fehlendes ziel darf nie zu einem Supabase-Schreibzugriff fuehren"
    assert pruefen.aufrufe == [], "ein fehlendes ziel darf nie bis zur Anbieter-Pruefung kommen"


_OAUTH_ZIEL = "https://mcp.example.com/mcp"
_OAUTH_REFERENZ = "OAUTH_BEARER_MCP_EXAMPLE_COM_MCP"


def test_ziel_gesetzt_fuer_oauth_kommt_durch(monkeypatch):
    """Gegenprobe zum I3-Test oben UND zum C3-Fix darunter: ein `ziel`, das
    https ist und sich nach der geteilten Namensregel auf `referenz`
    zurueckrechnet, muss unveraendert durchkommen -- sonst waere die Wache
    zu breit, nicht nur zu schmal."""
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen",
                        _Aufrufe(ergebnis={"ok": True, "referenz": _OAUTH_REFERENZ}))
    pruefen = _Aufrufe(ergebnis={"gut": True, "status": 200})
    monkeypatch.setattr(werkzeuge, "pruefe", pruefen)
    monkeypatch.setattr(werkzeuge.ablage, "verifizieren", _Aufrufe(ergebnis={"ok": True}))
    monkeypatch.setattr(werkzeuge, "_openfang_uebernehmen", _Aufrufe(ergebnis={"ok": True, "status": 200}))
    monkeypatch.setattr(werkzeuge.ablage, "uebernommen", _Aufrufe(ergebnis={"ok": True}))

    ergebnis = werkzeuge.schluessel_entgegennehmen(
        "proj", "demo-plugin", _OAUTH_REFERENZ, "oauth", _FAKE_WERT, ziel=_OAUTH_ZIEL)

    assert ergebnis == {"ok": True, "referenz": _OAUTH_REFERENZ}
    assert pruefen.aufrufe, "mit gesetztem ziel muss die Pruefung tatsaechlich erreicht werden"


# --- C3 (Schluss-Review): `ziel` entscheidet, WOHIN ein Geheimnis reist ----


def test_klartext_ziel_wird_vor_jedem_schreibzugriff_abgelehnt(monkeypatch):
    """C3: `ziel` wird in pruefung._oauth_aufruf zur Zieladresse eines
    Aufrufs, der den Wert im `Authorization: Bearer`-Kopf traegt -- ein
    `http:`-Ziel hiesse: das Geheimnis reist im Klartext. Abgelehnt, bevor
    irgendetwas in Supabase landet."""
    aufnahme = _Aufrufe(ergebnis={"ok": True, "referenz": _OAUTH_REFERENZ})
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen", aufnahme)
    pruefen = _Aufrufe(ergebnis={"gut": True, "status": 200})
    monkeypatch.setattr(werkzeuge, "pruefe", pruefen)
    openfang = _Aufrufe(ergebnis={"ok": True, "status": 200})
    monkeypatch.setattr(werkzeuge, "_openfang_uebernehmen", openfang)

    ergebnis = werkzeuge.schluessel_entgegennehmen(
        "proj", "demo-plugin", _OAUTH_REFERENZ, "oauth", _FAKE_WERT,
        ziel="http://mcp.example.com/mcp")

    assert ergebnis["ok"] is False
    assert "https" in ergebnis["fehler"]
    assert aufnahme.aufrufe == [], "ein Klartext-ziel darf nie zu einem Supabase-Schreibzugriff fuehren"
    assert pruefen.aufrufe == [], "ein Klartext-ziel darf nie bis zum Anbieter-Aufruf kommen"
    assert openfang.aufrufe == []


def test_oauth_ziel_das_nicht_auf_die_referenz_zurueckrechnet_wird_abgelehnt(monkeypatch):
    """C3, der eigentliche Punkt: `ziel` ist agentenverfasster Freitext
    (`plugin_bedarf` liefert es nicht). Ein https-Ziel auf einem FREMDEN
    Host ist technisch einwandfrei und trotzdem falsch -- der Wert ginge
    woandershin, als sein Referenzname sagt. Die geteilte Regel
    (deriveOAuthBearerReference) entscheidet, nicht Vertrauen."""
    aufnahme = _Aufrufe(ergebnis={"ok": True, "referenz": _OAUTH_REFERENZ})
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen", aufnahme)
    pruefen = _Aufrufe(ergebnis={"gut": True, "status": 200})
    monkeypatch.setattr(werkzeuge, "pruefe", pruefen)

    ergebnis = werkzeuge.schluessel_entgegennehmen(
        "proj", "demo-plugin", _OAUTH_REFERENZ, "oauth", _FAKE_WERT,
        ziel="https://sammelstelle.beispiel-angreifer.test/mcp")

    assert ergebnis["ok"] is False
    assert _OAUTH_REFERENZ in ergebnis["fehler"]
    assert aufnahme.aufrufe == []
    assert pruefen.aufrufe == []


def test_oauth_ziel_prueft_auch_query_fragment_und_benutzerangabe(monkeypatch):
    """Dieselben Bedingungen, die Rowboat auf genau diesen Wert legt
    (`secureUrl`, components/mcp-normalizer.ts:37-53). Jede davon fuehrt
    zur Ablehnung vor jedem Schreibzugriff."""
    aufnahme = _Aufrufe(ergebnis={"ok": True, "referenz": _OAUTH_REFERENZ})
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen", aufnahme)
    for ziel in ("https://mcp.example.com/mcp?abfluss=1",
                 "https://mcp.example.com/mcp#anker",
                 "https://benutzer:geheim@mcp.example.com/mcp"):
        ergebnis = werkzeuge.schluessel_entgegennehmen(
            "proj", "demo-plugin", _OAUTH_REFERENZ, "oauth", _FAKE_WERT, ziel=ziel)
        assert ergebnis["ok"] is False, ziel
    assert aufnahme.aufrufe == []


def test_connector_ziel_ist_eine_connector_id_keine_url(monkeypatch):
    """Fuer `art="connector"` ist `ziel` KEINE Adresse: pruefung.py schickt
    den Aufruf an das fest verdrahtete `https://api.openai.com/v1/responses`
    und legt `ziel` als `connector_id` in den Rumpf. Geprueft wird darum die
    Form, die Rowboat selbst verlangt (AppDeclarationSchema +
    connector-bridge-provider.ts:128) -- eine URL waere hier falsch."""
    aufnahme = _Aufrufe(ergebnis={"ok": True, "referenz": "CONNECTOR_CANVA"})
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen", aufnahme)
    pruefen = _Aufrufe(ergebnis={"gut": True, "status": 200})
    monkeypatch.setattr(werkzeuge, "pruefe", pruefen)
    monkeypatch.setattr(werkzeuge.ablage, "verifizieren", _Aufrufe(ergebnis={"ok": True}))
    monkeypatch.setattr(werkzeuge, "_openfang_uebernehmen", _Aufrufe(ergebnis={"ok": True, "status": 200}))
    monkeypatch.setattr(werkzeuge.ablage, "uebernommen", _Aufrufe(ergebnis={"ok": True}))

    schlecht = werkzeuge.schluessel_entgegennehmen(
        "proj", "demo-plugin", "CONNECTOR_CANVA", "connector", _FAKE_WERT,
        ziel="https://mcp.example.com/mcp")
    assert schlecht["ok"] is False
    assert aufnahme.aufrufe == []

    gut = werkzeuge.schluessel_entgegennehmen(
        "proj", "demo-plugin", "CONNECTOR_CANVA", "connector", _FAKE_WERT,
        ziel="connector_68df038e0ba48191908c8434991bbac2")
    assert gut == {"ok": True, "referenz": "CONNECTOR_CANVA"}
    assert pruefen.aufrufe, "eine gueltige connector_id muss bis zur Pruefung durchkommen"


def test_oauth_ableitung_stimmt_mit_rowboats_deriveoauthbearerreference_ueberein():
    """`_oauth_bearer_referenz` ist eine SPIEGELUNG von
    credential-naming.ts::deriveOAuthBearerReference, keine zweite Regel.
    Dieser Test bindet sie an den TS-Rumpf (inkl. des dort dokumentierten
    Beispielfalls) und prueft die Stellen, an denen die beiden
    Implementierungen auseinanderlaufen koennten."""
    if not _CREDENTIAL_NAMING_TS.is_file():
        pytest.skip(f"rowboat-Submodul nicht ausgecheckt: {_CREDENTIAL_NAMING_TS}")
    inhalt = _CREDENTIAL_NAMING_TS.read_text(encoding="utf-8")
    assert "OAUTH_BEARER_MCP_LINEAR_APP_MCP" in inhalt, (
        "der in credential-naming.ts dokumentierte Beispielfall hat sich geaendert "
        "-- werkzeuge.py::_oauth_bearer_referenz muss nachgezogen werden")
    assert '.replace(/[^A-Z0-9]+/gu, "_")' in inhalt, (
        "der Rumpf von deriveOAuthBearerReference hat sich geaendert")
    assert '.replace(/^_+|_+$/gu, "")' in inhalt, (
        "der Trim in deriveOAuthBearerReference hat sich geaendert")

    # Der in credential-naming.ts dokumentierte Fall, woertlich.
    assert werkzeuge._oauth_bearer_referenz("https://mcp.linear.app/mcp") == "OAUTH_BEARER_MCP_LINEAR_APP_MCP"
    # Leerer Pfad: `new URL()` macht daraus "/", das abschliessende "_" faellt
    # durch den Trim wieder weg -- beide Seiten liefern dasselbe.
    assert werkzeuge._oauth_bearer_referenz("https://mcp.notion.com") == "OAUTH_BEARER_MCP_NOTION_COM"
    assert werkzeuge._oauth_bearer_referenz("https://mcp.notion.com/") == "OAUTH_BEARER_MCP_NOTION_COM"
    # Standard-Port faellt weg (wie `URL.host`), ein anderer nicht.
    assert werkzeuge._oauth_bearer_referenz("https://mcp.linear.app:443/mcp") == "OAUTH_BEARER_MCP_LINEAR_APP_MCP"
    assert werkzeuge._oauth_bearer_referenz("https://mcp.linear.app:8443/mcp") == "OAUTH_BEARER_MCP_LINEAR_APP_8443_MCP"
    # Fail closed statt raten, wo die beiden Implementierungen abweichen
    # koennten (Prozentkodierung, Punkt-Segmente) bzw. gar keine URL vorliegt.
    assert werkzeuge._oauth_bearer_referenz("https://mcp.example.com/%41") is None
    assert werkzeuge._oauth_bearer_referenz("https://mcp.example.com/a/../b") is None
    assert werkzeuge._oauth_bearer_referenz("kein-url-text") is None


def test_openfang_uebergabe_scheitert_bleibt_auf_verifiziert_stehen(monkeypatch):
    """I2-Fix (Review Runde 1, restaurierte Brief-Reihenfolge): verifiziert,
    aber OpenFang lehnt ab (z.B. 503 store_unavailable) -- das ist KEIN
    Credential-Fehler. `verifizieren()` WIRD aufgerufen (Reihenfolge:
    verifizieren -> OpenFang), `fehlschlagen()` wird NICHT aufgerufen (die
    Zeile bleibt auf `verifiziert` stehen, die Vault-Kopie bleibt), und die
    Antwort markiert den Fehler als `retryable`."""
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen",
                        _Aufrufe(ergebnis={"ok": True, "referenz": "X"}))
    monkeypatch.setattr(werkzeuge, "pruefe", _Aufrufe(ergebnis={"gut": True, "status": 200}))
    verifizieren = _Aufrufe(ergebnis={"ok": True})
    monkeypatch.setattr(werkzeuge.ablage, "verifizieren", verifizieren)
    monkeypatch.setattr(werkzeuge, "_openfang_uebernehmen",
                        _Aufrufe(ergebnis={"ok": False, "status": 503, "fehler": "OpenFang HTTP 503"}))
    fehlschlagen = _Aufrufe(ergebnis={"ok": True})
    monkeypatch.setattr(werkzeuge.ablage, "fehlschlagen", fehlschlagen)
    uebernommen = _Aufrufe(ergebnis={"ok": True})
    monkeypatch.setattr(werkzeuge.ablage, "uebernommen", uebernommen)

    ergebnis = werkzeuge.schluessel_entgegennehmen("proj", "demo-plugin", "X", "bearer", _FAKE_WERT)

    assert ergebnis["ok"] is False
    assert ergebnis.get("retryable") is True
    assert verifizieren.aufrufe == [(("X",), {})]
    assert fehlschlagen.aufrufe == [], "OpenFang-Ausfall ist kein Credential-Fehler -- kein fehlschlagen()"
    assert uebernommen.aufrufe == []


def test_openfang_409_meldet_betreiber_entscheidung_kein_autooverwrite(monkeypatch):
    """409 (reference_exists) heisst: die Referenz koennte bei OpenFang
    schon einen anderen, bewusst gesetzten Wert tragen. Kein automatisches
    Ueberschreiben, keine Loeschung der Supabase-Kopie -- eine Entscheidung
    des Betreibers wird verlangt, nicht getroffen.

    Minor-Fix (Review Runde 2): geprueft wird das maschinenlesbare Signal
    (`erfordert_betreiber_entscheidung`, `retryable=False`), NICHT das
    deutsche Wort "Betreiber" im Fliesstext -- ein Aufrufer, der nur den
    Fliesstext prueft, koennte 409 sonst mit einem generischen Fehlschlag
    verwechseln und blind automatisch wiederholen."""
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen",
                        _Aufrufe(ergebnis={"ok": True, "referenz": "X"}))
    monkeypatch.setattr(werkzeuge, "pruefe", _Aufrufe(ergebnis={"gut": True, "status": 200}))
    monkeypatch.setattr(werkzeuge.ablage, "verifizieren", _Aufrufe(ergebnis={"ok": True}))
    monkeypatch.setattr(werkzeuge, "_openfang_uebernehmen",
                        _Aufrufe(ergebnis={"ok": False, "status": 409, "fehler": "OpenFang HTTP 409"}))
    fehlschlagen = _Aufrufe(ergebnis={"ok": True})
    monkeypatch.setattr(werkzeuge.ablage, "fehlschlagen", fehlschlagen)
    uebernommen = _Aufrufe(ergebnis={"ok": True})
    monkeypatch.setattr(werkzeuge.ablage, "uebernommen", uebernommen)

    ergebnis = werkzeuge.schluessel_entgegennehmen("proj", "demo-plugin", "X", "bearer", _FAKE_WERT)

    assert ergebnis["ok"] is False
    assert ergebnis.get("erfordert_betreiber_entscheidung") is True
    assert ergebnis.get("retryable") is False, "409 darf sich nicht als blind automatisch wiederholbar ausgeben"
    assert fehlschlagen.aufrufe == []
    assert uebernommen.aufrufe == []


def test_uebernommen_scheitert_endgueltig_meldet_zwei_verwahrstellen(monkeypatch):
    """I5 (Schluss-Review): OpenFang hat den Wert (200), der Supabase-
    Uebergang `uebernommen()` scheitert dauerhaft. Dann liegt DERSELBE Wert
    in zwei Tresoren -- die "zwei Widerrufsflaechen", die D2 verbietet.
    Vorher gab es dafuer nur `ok: False` mit Fliesstext: kein Retry, kein
    maschinenlesbares Signal, und der naheliegende Retry des Betreibers
    (`schluessel_entgegennehmen` nochmal) stirbt an der UNIQUE-Constraint.

    Geprueft wird beides: dass der Uebergang BEGRENZT wiederholt wird, und
    dass die Antwort das eigene Kennzeichen `zwei_verwahrstellen` traegt --
    nicht `retryable` (dieses Werkzeug zu wiederholen ist genau das, was
    hier NICHT hilft) und nicht `erfordert_betreiber_entscheidung` (das
    gehoert dem 409-Zweig)."""
    monkeypatch.setattr(werkzeuge, "_UEBERNOMMEN_PAUSE_SEKUNDEN", 0)
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen",
                        _Aufrufe(ergebnis={"ok": True, "referenz": "X"}))
    monkeypatch.setattr(werkzeuge, "pruefe", _Aufrufe(ergebnis={"gut": True, "status": 200}))
    monkeypatch.setattr(werkzeuge.ablage, "verifizieren", _Aufrufe(ergebnis={"ok": True}))
    monkeypatch.setattr(werkzeuge, "_openfang_uebernehmen", _Aufrufe(ergebnis={"ok": True, "status": 200}))
    uebernommen = _Aufrufe(ergebnis={"ok": False, "fehler": "Supabase nicht erreichbar (TimeoutExpired)"})
    monkeypatch.setattr(werkzeuge.ablage, "uebernommen", uebernommen)

    ergebnis = werkzeuge.schluessel_entgegennehmen("proj", "demo-plugin", "X", "bearer", _FAKE_WERT)

    assert ergebnis["ok"] is False
    assert ergebnis.get("zwei_verwahrstellen") is True
    assert ergebnis.get("retryable") is False
    assert "erfordert_betreiber_entscheidung" not in ergebnis
    # Die Abhilfe muss in der Meldung STEHEN, nicht nur gemeint sein.
    assert "ablage.uebernommen" in ergebnis["fehler"]
    assert len(uebernommen.aufrufe) == werkzeuge._UEBERNOMMEN_VERSUCHE, (
        "der Uebergang muss begrenzt wiederholt werden -- genau so oft, "
        "wie _UEBERNOMMEN_VERSUCHE sagt")
    for text in _alle_werte_in(ergebnis):
        assert _FAKE_WERT not in text


def test_uebernommen_gelingt_im_zweiten_versuch_meldet_erfolg(monkeypatch):
    """Gegenprobe: der Retry ist nicht Dekoration. Scheitert `uebernommen()`
    einmal und gelingt dann, ist das Ergebnis ein gewoehnlicher Erfolg --
    die Supabase-Kopie ist weg, es bleibt genau eine Verwahrstelle."""
    monkeypatch.setattr(werkzeuge, "_UEBERNOMMEN_PAUSE_SEKUNDEN", 0)
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen",
                        _Aufrufe(ergebnis={"ok": True, "referenz": "X"}))
    monkeypatch.setattr(werkzeuge, "pruefe", _Aufrufe(ergebnis={"gut": True, "status": 200}))
    monkeypatch.setattr(werkzeuge.ablage, "verifizieren", _Aufrufe(ergebnis={"ok": True}))
    monkeypatch.setattr(werkzeuge, "_openfang_uebernehmen", _Aufrufe(ergebnis={"ok": True, "status": 200}))
    uebernommen = _Aufrufe(ergebnisse=[{"ok": False, "fehler": "kurze Stoerung"}, {"ok": True}])
    monkeypatch.setattr(werkzeuge.ablage, "uebernommen", uebernommen)

    ergebnis = werkzeuge.schluessel_entgegennehmen("proj", "demo-plugin", "X", "bearer", _FAKE_WERT)

    assert ergebnis == {"ok": True, "referenz": "X"}
    assert len(uebernommen.aufrufe) == 2


def test_openfang_409_und_sonstiger_fehlschlag_haben_unterschiedliche_signale(monkeypatch):
    """Gegenprobe: ein NICHT-409-Fehlschlag (z.B. 503) muss weiterhin
    `retryable=True` und KEIN `erfordert_betreiber_entscheidung` tragen --
    sonst waere die Unterscheidung nur fuer 409 sichtbar, nicht als
    generelles Vertragsmerkmal beider Zweige."""
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen",
                        _Aufrufe(ergebnis={"ok": True, "referenz": "X"}))
    monkeypatch.setattr(werkzeuge, "pruefe", _Aufrufe(ergebnis={"gut": True, "status": 200}))
    monkeypatch.setattr(werkzeuge.ablage, "verifizieren", _Aufrufe(ergebnis={"ok": True}))
    monkeypatch.setattr(werkzeuge, "_openfang_uebernehmen",
                        _Aufrufe(ergebnis={"ok": False, "status": 503, "fehler": "OpenFang HTTP 503"}))

    ergebnis = werkzeuge.schluessel_entgegennehmen("proj", "demo-plugin", "X", "bearer", _FAKE_WERT)

    assert ergebnis.get("retryable") is True
    assert "erfordert_betreiber_entscheidung" not in ergebnis


# ─── Fail-soft ohne konfigurierte Ziele (Muster marketing/claw/werkzeuge.py) ─


def test_plugin_bedarf_ohne_rowboat_env_scheitert_freundlich(monkeypatch):
    monkeypatch.delenv("ROWBOAT_URL", raising=False)
    monkeypatch.delenv("ROWBOAT_API_KEY", raising=False)
    ergebnis = werkzeuge.plugin_bedarf("proj", "demo-plugin")
    assert ergebnis["ok"] is False
    assert "ROWBOAT_URL" in ergebnis["fehler"] or "ROWBOAT_API_KEY" in ergebnis["fehler"]


def test_plugin_installieren_ohne_rowboat_env_scheitert_freundlich(monkeypatch):
    monkeypatch.delenv("ROWBOAT_URL", raising=False)
    monkeypatch.delenv("ROWBOAT_API_KEY", raising=False)
    ergebnis = werkzeuge.plugin_installieren("proj", "demo-plugin")
    assert ergebnis["ok"] is False


def test_plugin_werkzeug_binden_ohne_rowboat_env_scheitert_freundlich(monkeypatch):
    monkeypatch.delenv("ROWBOAT_URL", raising=False)
    monkeypatch.delenv("ROWBOAT_API_KEY", raising=False)
    ergebnis = werkzeuge.plugin_werkzeug_binden("proj", "demo-plugin", "a" * 64)
    assert ergebnis["ok"] is False


def test_schluessel_entgegennehmen_ohne_openfang_env_scheitert_freundlich_bleibt_verifiziert(monkeypatch):
    """OpenFang nicht eingerichtet ist ein Uebergabe-Fehler, kein
    Credential-Fehler (I2) -- fail-soft, aber `fehlschlagen()` wird NICHT
    aufgerufen; `verifizieren()` schon (restaurierte Reihenfolge)."""
    monkeypatch.delenv("PLUGIN_SETUP_OPENFANG_URL", raising=False)
    monkeypatch.delenv("PLUGIN_SETUP_OPENFANG_API_KEY", raising=False)
    monkeypatch.setattr(werkzeuge.ablage, "entgegennehmen",
                        _Aufrufe(ergebnis={"ok": True, "referenz": "X"}))
    monkeypatch.setattr(werkzeuge, "pruefe", _Aufrufe(ergebnis={"gut": True, "status": 200}))
    verifizieren = _Aufrufe(ergebnis={"ok": True})
    monkeypatch.setattr(werkzeuge.ablage, "verifizieren", verifizieren)
    fehlschlagen = _Aufrufe(ergebnis={"ok": True})
    monkeypatch.setattr(werkzeuge.ablage, "fehlschlagen", fehlschlagen)

    ergebnis = werkzeuge.schluessel_entgegennehmen("proj", "demo-plugin", "X", "bearer", _FAKE_WERT)
    assert ergebnis["ok"] is False
    assert ergebnis.get("retryable") is True
    assert verifizieren.aufrufe == [(("X",), {})]
    assert fehlschlagen.aufrufe == []


# ─── plugin_bedarf: art-Ableitung aus dem Referenznamen ────────────────────


# ─── I4 (Review Runde 1): Kopplung an Rowboats Namensform, nicht nur Vertrauen ─

# spaces/plugin-setup/tests -> spaces
_SPACES_DIR = Path(__file__).resolve().parents[2]
_CREDENTIAL_NAMING_TS = (
    _SPACES_DIR / "rowboat" / "rowboat" / "packages" / "openai-plugin-runtime"
    / "src" / "providers" / "credential-naming.ts"
)
_CATALOG_TS = (
    _SPACES_DIR / "rowboat" / "rowboat" / "packages" / "openai-plugin-runtime"
    / "src" / "domain" / "catalog.ts"
)


def test_praefixe_stimmen_mit_rowboats_credential_naming_ts_ueberein():
    """`_art_aus_referenzname` ist eine VIERTE Ableitung derselben
    Namensform (Python, invertiert: Name -> art statt art -> Name) -- die
    eigentliche Reparatur waere, dass Rowboats Preview `kind` mitliefert
    (repo-uebergreifend, NICHT Teil dieser Runde, s. Bericht). Bis dahin:
    dieser Test koppelt die beiden hartcodierten Praefixe hart an
    credential-naming.ts und schlaegt fehl, sobald sie auseinanderlaufen
    -- mit C2 behoben (kein Fallback auf "bearer" mehr) wird eine
    Fehlklassifikation dadurch ein sicherer Fehlschlag (`art="unbekannt"`
    wird von schluessel_entgegennehmen abgelehnt), keine Leckage mehr --
    die Kopplung ist trotzdem proportional, kein Ersatz fuer die
    eigentliche Reparatur."""
    if not _CREDENTIAL_NAMING_TS.is_file():
        pytest.skip(f"rowboat-Submodul nicht ausgecheckt: {_CREDENTIAL_NAMING_TS}")
    inhalt = _CREDENTIAL_NAMING_TS.read_text(encoding="utf-8")
    assert "`OAUTH_BEARER_${stem}`" in inhalt, (
        "credential-naming.ts::deriveOAuthBearerReference hat das Praefix "
        "'OAUTH_BEARER_' geaendert -- werkzeuge.py::_art_aus_referenzname "
        "muss nachgezogen werden")
    assert "`CONNECTOR_${" in inhalt, (
        "credential-naming.ts::deriveConnectorReference hat das Praefix "
        "'CONNECTOR_' geaendert -- werkzeuge.py::_art_aus_referenzname "
        "muss nachgezogen werden")


def test_pinned_catalog_digest_stimmt_mit_rowboats_catalog_ts_ueberein():
    """`_PINNED_CATALOG_DIGEST_DEFAULT` ist von Hand aus catalog.ts
    abgeschrieben (11.09.2026) -- dieser Test haelt beide synchron, statt
    sich auf das Abschreiben selbst zu verlassen."""
    if not _CATALOG_TS.is_file():
        pytest.skip(f"rowboat-Submodul nicht ausgecheckt: {_CATALOG_TS}")
    inhalt = _CATALOG_TS.read_text(encoding="utf-8")
    treffer = re.search(r'PINNED_PLUGIN_CATALOG_DIGEST\s*=\s*\n?\s*"([0-9a-f]+)"', inhalt)
    assert treffer, "PINNED_PLUGIN_CATALOG_DIGEST nicht in catalog.ts gefunden (Format geaendert?)"
    assert treffer.group(1) == werkzeuge._PINNED_CATALOG_DIGEST_DEFAULT, (
        "Rowboats gepinnter Katalog-Digest hat sich geaendert -- "
        "werkzeuge.py::_PINNED_CATALOG_DIGEST_DEFAULT muss nachgezogen werden "
        "(oder ROWBOAT_CATALOG_DIGEST wird produktiv gesetzt)")


def test_plugin_bedarf_bildet_preview_auf_art_und_vorhanden_ab_ohne_sie_zu_deuten(monkeypatch):
    """Was dieser Test pinnt: die ABBILDUNG Preview -> Werkzeug-Rueckgabe,
    NICHT ein Verhalten des laufenden Systems.

    Die Antwort unten ist von Hand gebaut, und ein Teil davon kann das echte
    System heute gar nicht senden: C5 (Schluss-Review, gemessen 11.09.2026)
    -- `configured` kommt in der Preview aus
    `listCredentialSlots(installation.id)`, und der einzige Schreiber im
    Produktivpfad, `slotsFrom` (plugin-service.shared.ts:234), liefert
    unbedingt `Object.freeze([])`; `putCredentialSlot` hat keinen
    Produktiv-Aufrufer. `configured` ist damit strukturell immer `false`,
    also `vorhanden` immer `False`.

    Die frueher hier stehende Zeile `vorhanden is True` pinnte darum ein
    Verhalten, das das System nicht erzeugen kann. Sie bleibt stehen -- aber
    als das, was sie ist: die Zusicherung, dass dieses Werkzeug `configured`
    unveraendert durchreicht, falls Rowboats Installationspfad das Feld
    eines Tages wirklich fuellt. Als KRITERIUM taugt `vorhanden` heute
    nicht; AGENTS.md steuert deshalb nicht mehr danach."""
    monkeypatch.setenv("ROWBOAT_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("ROWBOAT_API_KEY", "fake-projekt-schluessel")
    antwort_json = json.dumps({
        "credentialSlots": [
            {"name": "OAUTH_BEARER_MCP_EXAMPLE_COM", "configured": False},
            {"name": "CONNECTOR_CANVA", "configured": True},
            {"name": "OPENAI_API_KEY", "configured": True},
        ]
    }).encode("utf-8")
    monkeypatch.setattr(werkzeuge, "_roh_anfrage", _Aufrufe(ergebnis=(200, antwort_json.decode("utf-8"))))

    ergebnis = werkzeuge.plugin_bedarf("proj", "demo-plugin")
    assert ergebnis["ok"] is True
    nach_name = {e["name"]: e for e in ergebnis["daten"]}
    assert nach_name["OAUTH_BEARER_MCP_EXAMPLE_COM"]["art"] == "oauth"
    assert nach_name["OAUTH_BEARER_MCP_EXAMPLE_COM"]["vorhanden"] is False
    assert nach_name["CONNECTOR_CANVA"]["art"] == "connector"
    # Reine Feld-Abbildung von `configured`, s. Docstring -- kein Signal.
    assert nach_name["CONNECTOR_CANVA"]["vorhanden"] is True
    # C2-Fix: ein unerkannter Name faellt NIE auf "bearer" zurueck (das
    # wuerde ihn Richtung api.github.com schicken) -- er wird "unbekannt",
    # explizit fail closed statt geraten.
    assert nach_name["OPENAI_API_KEY"]["art"] == "unbekannt"
    assert all(e["quelle"] == "rowboat-preview" for e in ergebnis["daten"])


def test_vorhanden_ist_heute_strukturell_immer_falsch(monkeypatch):
    """C5, die Messung selbst als Test: `slotsFrom` -- der einzige Schreiber
    von `credentialSlots` im Produktivpfad -- liefert unbedingt eine LEERE,
    eingefrorene Liste, und `putCredentialSlot` hat keinen Produktiv-
    Aufrufer. Solange das so ist, kann `configured` (und damit `vorhanden`)
    nie `true` werden. Dieser Test schlaegt fehl, sobald sich das aendert --
    dann darf (und soll) AGENTS.md wieder danach steuern."""
    shared = (_SPACES_DIR / "rowboat" / "rowboat" / "apps" / "rowboat" / "src"
              / "application" / "use-cases" / "plugins" / "plugin-service.shared.ts")
    if not shared.is_file():
        pytest.skip(f"rowboat-Submodul nicht ausgecheckt: {shared}")
    inhalt = shared.read_text(encoding="utf-8")
    treffer = re.search(r"export function slotsFrom\([^)]*\)[^{]*\{(.*?)\n\}", inhalt, re.S)
    assert treffer, "slotsFrom nicht gefunden (Format geaendert?)"
    assert "Object.freeze([])" in treffer.group(1), (
        "slotsFrom liefert nicht mehr unbedingt die leere Liste -- "
        "`vorhanden` koennte jetzt ein echtes Signal sein; AGENTS.md und "
        "werkzeuge.plugin_bedarf muessen neu bewertet werden")
    assert "return Object.freeze([]);" in treffer.group(1)


# ─── plugin_installieren / plugin_werkzeug_binden: Anfrageform ────────────


_ZUGELASSEN_DIGEST = "a" * 64
_ABGELEHNT_DIGEST = "b" * 64
_ZWEITER_ZUGELASSEN_DIGEST = "c" * 64

_VORSCHAU_MIT_GEMISCHTEN_KOMPONENTEN = json.dumps({
    "credentialSlots": [{"name": "OAUTH_BEARER_MCP_EXAMPLE_COM_MCP", "configured": False}],
    "components": [
        {"componentDigest": _ZUGELASSEN_DIGEST, "name": "demo", "kind": "mcp",
         "admission": {"status": "admitted", "policyVersion": "p1"},
         "availability": {"status": "available"}, "status": "available"},
        {"componentDigest": _ABGELEHNT_DIGEST, "name": "demo-skill", "kind": "skill",
         "admission": {"status": "review_required", "reason": "write_review_required",
                       "policyVersion": "p1"},
         "availability": {"status": "review_required", "reason": "write_review_required"},
         "status": "review_required", "reason": "write_review_required"},
        {"componentDigest": _ZWEITER_ZUGELASSEN_DIGEST, "name": "logo.png", "kind": "asset",
         "admission": {"status": "admitted", "policyVersion": "p1"},
         "availability": {"status": "available"}, "status": "available"},
    ],
})

_EMPFANGSSCHEIN = json.dumps({
    "type": "install", "receiptId": "r1", "projectId": "proj",
    "pluginName": "demo-plugin", "status": "success", "redactions": [],
})


def test_plugin_bedarf_liefert_komponenten_mit_digest_und_zulassung(monkeypatch):
    """C4 (Schluss-Review): die Preview traegt `components` samt
    `componentDigest` und `admission` -- `plugin_bedarf` hat sie bis hierher
    weggeworfen. Damit war KEIN Werkzeug in der Lage, einen
    `componentDigest` zu liefern (der Install-Empfangsschein traegt keinen),
    und `plugin_werkzeug_binden` war vom Agenten aus nicht erreichbar, ohne
    einen Digest zu erfinden."""
    monkeypatch.setenv("ROWBOAT_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("ROWBOAT_API_KEY", "fake-projekt-schluessel")
    monkeypatch.setattr(werkzeuge, "_roh_anfrage",
                        _Aufrufe(ergebnis=(200, _VORSCHAU_MIT_GEMISCHTEN_KOMPONENTEN)))

    ergebnis = werkzeuge.plugin_bedarf("proj", "demo-plugin")
    assert ergebnis["ok"] is True
    nach_digest = {k["digest"]: k for k in ergebnis["komponenten"]}
    assert set(nach_digest) == {_ZUGELASSEN_DIGEST, _ABGELEHNT_DIGEST, _ZWEITER_ZUGELASSEN_DIGEST}
    assert nach_digest[_ZUGELASSEN_DIGEST]["zugelassen"] is True
    assert nach_digest[_ZUGELASSEN_DIGEST]["kind"] == "mcp"
    assert nach_digest[_ABGELEHNT_DIGEST]["zugelassen"] is False


def test_plugin_installieren_waehlt_ohne_angabe_NUR_zugelassene_komponenten(monkeypatch):
    """C4, der Kern: ohne `komponenten` liess dieses Werkzeug den Parameter
    weg, der Server setzte dann JEDE Komponente
    (install-plugin.use-case.ts:45) und `assertSelectionAdmitted`
    (plugin-service.shared.ts:203) lehnte mit `component_not_admitted` ab,
    sobald auch nur eine davon nicht zugelassen war -- was am gepinnten
    Katalog fuer 52 der 180 Eintraege gilt, darunter github, cloudflare und
    notion. Der in AGENTS.md beschriebene Weg konnte fuer sie also gar nicht
    gelingen. Jetzt enthaelt die Default-Auswahl AUSSCHLIESSLICH zugelassene
    Digests."""
    monkeypatch.setenv("ROWBOAT_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("ROWBOAT_API_KEY", "fake-projekt-schluessel")
    aufruf = _Aufrufe(ergebnisse=[(200, _VORSCHAU_MIT_GEMISCHTEN_KOMPONENTEN),
                                  (201, _EMPFANGSSCHEIN)])
    monkeypatch.setattr(werkzeuge, "_roh_anfrage", aufruf)

    ergebnis = werkzeuge.plugin_installieren("proj", "demo-plugin")
    assert ergebnis["ok"] is True
    assert ergebnis["daten"]["status"] == "success"

    (vorschau_url, _, vorschau_method), _ = aufruf.aufrufe[0]
    assert vorschau_method == "GET"
    assert vorschau_url.startswith("http://127.0.0.1:3000/api/v1/projects/proj/plugins/demo-plugin?")

    (url, daten, method), kwargs = aufruf.aufrufe[1]
    assert url == "http://127.0.0.1:3000/api/v1/projects/proj/plugins"
    assert method == "POST"
    body = json.loads(daten)
    assert body["pluginName"] == "demo-plugin"
    assert body["expectedRevision"] == 0
    assert body["catalogDigest"]
    assert sorted(body["componentDigests"]) == sorted([_ZUGELASSEN_DIGEST, _ZWEITER_ZUGELASSEN_DIGEST])
    assert _ABGELEHNT_DIGEST not in body["componentDigests"], (
        "eine nicht zugelassene Komponente darf nie in der Default-Auswahl stehen")
    assert kwargs["kopfzeilen"]["Idempotency-Key"]
    assert kwargs["kopfzeilen"]["Authorization"] == "Bearer fake-projekt-schluessel"


def test_plugin_installieren_ohne_eine_einzige_zugelassene_komponente_scheitert_ehrlich(monkeypatch):
    """Kein leeres `componentDigests` senden (das waere `request_invalid`,
    plugin-component-selection.ts:17) und erst recht nicht auf "alle"
    zurueckfallen -- sondern sagen, was Sache ist."""
    monkeypatch.setenv("ROWBOAT_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("ROWBOAT_API_KEY", "fake-projekt-schluessel")
    nichts_zugelassen = json.dumps({
        "credentialSlots": [],
        "components": [
            {"componentDigest": _ABGELEHNT_DIGEST, "name": "demo-skill", "kind": "skill",
             "admission": {"status": "rejected", "reason": "license_rejected",
                           "policyVersion": "p1"},
             "availability": {"status": "unavailable", "reason": "component_unsupported"},
             "status": "rejected", "reason": "license_rejected"},
        ],
    })
    aufruf = _Aufrufe(ergebnisse=[(200, nichts_zugelassen)])
    monkeypatch.setattr(werkzeuge, "_roh_anfrage", aufruf)

    ergebnis = werkzeuge.plugin_installieren("proj", "demo-plugin")
    assert ergebnis["ok"] is False
    assert "zugelassene" in ergebnis["fehler"]
    assert len(aufruf.aufrufe) == 1, "ohne zugelassene Komponente darf gar nicht erst installiert werden"


def test_plugin_installieren_ohne_komponenten_im_plugin_laesst_das_feld_weg(monkeypatch):
    """Ein Plugin ganz ohne Komponenten: `componentDigests` muss FEHLEN --
    eine leere Liste waere `request_invalid`."""
    monkeypatch.setenv("ROWBOAT_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("ROWBOAT_API_KEY", "fake-projekt-schluessel")
    aufruf = _Aufrufe(ergebnisse=[
        (200, json.dumps({"credentialSlots": [], "components": []})),
        (201, _EMPFANGSSCHEIN),
    ])
    monkeypatch.setattr(werkzeuge, "_roh_anfrage", aufruf)

    ergebnis = werkzeuge.plugin_installieren("proj", "demo-plugin")
    assert ergebnis["ok"] is True
    (_, daten, _), _ = aufruf.aufrufe[1]
    assert "componentDigests" not in json.loads(daten)


def test_plugin_installieren_gibt_ausdrueckliche_komponenten_unveraendert_weiter(monkeypatch):
    """Eine ausdrueckliche Liste wird NICHT ueberstimmt -- und es wird auch
    keine Preview dafuer geholt (ein Betreiber, der bewusst waehlt,
    entscheidet, nicht dieses Werkzeug)."""
    monkeypatch.setenv("ROWBOAT_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("ROWBOAT_API_KEY", "fake-projekt-schluessel")
    aufruf = _Aufrufe(ergebnis=(201, _EMPFANGSSCHEIN))
    monkeypatch.setattr(werkzeuge, "_roh_anfrage", aufruf)

    werkzeuge.plugin_installieren("proj", "demo-plugin", [_ABGELEHNT_DIGEST])
    assert len(aufruf.aufrufe) == 1, "eine ausdrueckliche Auswahl braucht keine Preview"
    (_, daten, _), _ = aufruf.aufrufe[0]
    assert json.loads(daten)["componentDigests"] == [_ABGELEHNT_DIGEST]


def test_plugin_werkzeug_binden_schickt_nur_componentdigest(monkeypatch):
    monkeypatch.setenv("ROWBOAT_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("ROWBOAT_API_KEY", "fake-projekt-schluessel")
    aufruf = _Aufrufe(ergebnis=(200, json.dumps({"toolName": "plugin__demo__tool", "added": True})))
    monkeypatch.setattr(werkzeuge, "_roh_anfrage", aufruf)

    digest = "b" * 64
    ergebnis = werkzeuge.plugin_werkzeug_binden("proj", "demo-plugin", digest)
    assert ergebnis == {"ok": True, "daten": {"toolName": "plugin__demo__tool", "added": True}}

    (url, daten, method), kwargs = aufruf.aufrufe[0]
    assert url == "http://127.0.0.1:3000/api/v1/projects/proj/plugins/demo-plugin/tools"
    assert method == "POST"
    assert json.loads(daten) == {"componentDigest": digest}
    assert kwargs["kopfzeilen"]["Authorization"] == "Bearer fake-projekt-schluessel"


def test_rowboat_http_fehler_leckt_keinen_schluessel(monkeypatch):
    """Die Fake-Antwort ECHOT den Schluessel im Fehlerkoerper zurueck (ein
    realistischer Worst-Case -- ein verbosener Fehler-Proxy, der die
    Anfrage spiegelt) -- ohne das koennte `_ohne_schluessel` komplett
    entfernt werden und dieser Test bliebe gruen (Review Runde 1: 'kann
    nicht rot werden'). Mit dem Echo IST er die Zusicherung: der Schluessel
    steckt im simulierten Rohkoerper, aber NICHT mehr in `ergebnis['fehler']`."""
    monkeypatch.setenv("ROWBOAT_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("ROWBOAT_API_KEY", "fake-projekt-schluessel-xyz")
    monkeypatch.setattr(werkzeuge, "_roh_anfrage", _Aufrufe(ergebnis=(
        401, json.dumps({"error": "unauthenticated",
                         "empfangene_headers": {"Authorization": "Bearer fake-projekt-schluessel-xyz"}}))))
    ergebnis = werkzeuge.plugin_bedarf("proj", "demo-plugin")
    assert ergebnis["ok"] is False
    assert "fake-projekt-schluessel-xyz" not in ergebnis["fehler"]


def test_openfang_uebernehmen_schickt_referenz_wert_overwrite_und_bearer(monkeypatch):
    """M1: bisher patchte jeder Ordnungstest `_openfang_uebernehmen` selbst
    -- ein Tippfehler im Pfad/Body/Header waere nie aufgefallen. Dieser
    Test patcht stattdessen `_roh_anfrage` (den einzigen echten Netzgriff)
    und prueft die tatsaechliche Anfrageform gegen die Aufgabe-1-Schnittstelle
    aus dem Brief: `POST /api/credentials/store`, Body
    `{reference, value, overwrite: false}`, `Authorization: Bearer <key>`."""
    monkeypatch.setenv("PLUGIN_SETUP_OPENFANG_URL", "http://127.0.0.1:4273")
    monkeypatch.setenv("PLUGIN_SETUP_OPENFANG_API_KEY", "fake-daemon-schluessel")
    aufruf = _Aufrufe(ergebnis=(200, json.dumps({"reference": "X", "issuable": True})))
    monkeypatch.setattr(werkzeuge, "_roh_anfrage", aufruf)

    ergebnis = werkzeuge._openfang_uebernehmen("X", _FAKE_WERT)
    assert ergebnis == {"ok": True, "status": 200}

    (url, daten, method, kopfzeilen), kwargs = aufruf.aufrufe[0]
    assert url == "http://127.0.0.1:4273/api/credentials/store"
    assert method == "POST"
    assert kopfzeilen == {"Authorization": "Bearer fake-daemon-schluessel"}
    body = json.loads(daten)
    assert body == {"reference": "X", "value": _FAKE_WERT, "overwrite": False}
