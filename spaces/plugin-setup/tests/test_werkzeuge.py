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
    des Betreibers wird verlangt, nicht getroffen."""
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
    assert "Betreiber" in ergebnis["fehler"]
    assert fehlschlagen.aufrufe == []
    assert uebernommen.aufrufe == []


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


def test_plugin_bedarf_leitet_art_aus_dem_namen_ab(monkeypatch):
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
    assert nach_name["CONNECTOR_CANVA"]["vorhanden"] is True
    # C2-Fix: ein unerkannter Name faellt NIE auf "bearer" zurueck (das
    # wuerde ihn Richtung api.github.com schicken) -- er wird "unbekannt",
    # explizit fail closed statt geraten.
    assert nach_name["OPENAI_API_KEY"]["art"] == "unbekannt"
    assert all(e["quelle"] == "rowboat-preview" for e in ergebnis["daten"])


# ─── plugin_installieren / plugin_werkzeug_binden: Anfrageform ────────────


def test_plugin_installieren_schickt_catalogdigest_und_idempotency_key(monkeypatch):
    monkeypatch.setenv("ROWBOAT_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("ROWBOAT_API_KEY", "fake-projekt-schluessel")
    aufruf = _Aufrufe(ergebnis=(201, json.dumps({
        "type": "install", "receiptId": "r1", "projectId": "proj",
        "pluginName": "demo-plugin", "status": "success", "redactions": [],
    })))
    monkeypatch.setattr(werkzeuge, "_roh_anfrage", aufruf)

    ergebnis = werkzeuge.plugin_installieren("proj", "demo-plugin")
    assert ergebnis["ok"] is True
    assert ergebnis["daten"]["status"] == "success"

    (url, daten, method), kwargs = aufruf.aufrufe[0]
    assert url == "http://127.0.0.1:3000/api/v1/projects/proj/plugins"
    assert method == "POST"
    body = json.loads(daten)
    assert body["pluginName"] == "demo-plugin"
    assert body["expectedRevision"] == 0
    assert body["catalogDigest"]
    assert "componentDigests" not in body
    assert kwargs["kopfzeilen"]["Idempotency-Key"]
    assert kwargs["kopfzeilen"]["Authorization"] == "Bearer fake-projekt-schluessel"


def test_plugin_installieren_gibt_komponenten_weiter(monkeypatch):
    monkeypatch.setenv("ROWBOAT_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("ROWBOAT_API_KEY", "fake-projekt-schluessel")
    aufruf = _Aufrufe(ergebnis=(201, json.dumps({
        "type": "install", "receiptId": "r1", "projectId": "proj",
        "pluginName": "demo-plugin", "status": "success", "redactions": [],
    })))
    monkeypatch.setattr(werkzeuge, "_roh_anfrage", aufruf)

    digest = "a" * 64
    werkzeuge.plugin_installieren("proj", "demo-plugin", [digest])
    (_, daten, _), _ = aufruf.aufrufe[0]
    assert json.loads(daten)["componentDigests"] == [digest]


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
