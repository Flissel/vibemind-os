# Eingabefenster Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Geheimnis erreicht den Kontext des Agenten nie mehr — der Agent nennt einen Einmal-Link, der Mensch trägt den Wert in seinem eigenen Browser ein, der Sidecar nimmt ihn direkt entgegen.

**Architecture:** Der bestehende MCP-Sidecar (`spaces/plugin-setup/server.py`, FastMCP auf `127.0.0.1:8131`) bekommt neben dem MCP-Endpunkt eigene HTTP-Routen (`FastMCP.custom_route`, im installierten Paket vorhanden — geprüft). Eine schwebende Anfrage lebt als Eintrag im Prozessspeicher und trägt nie einen Wert. Der Wert reist genau einmal: aus dem Browser des Menschen per POST in den Sidecar, der die bereits bewiesene Kette (Aufnahme → Verifikation → Übergabe → Kopie löschen) unverändert weiterbenutzt.

**Tech Stack:** Python 3.11, FastMCP (`mcp.server.fastmcp`), Starlette-Routen über `custom_route`, Postgres/Supabase mit `supabase_vault`, `docker exec … psql` als Transport (Muster der bestehenden Dateien), stdlib-only im Provisioner.

**Spec:** `docs/superpowers/specs/2026-09-12-eingabefenster-design.md`

## Global Constraints

- Ein Credential-Wert darf nie in einem Rückgabefeld, einer Logzeile, einer Zustandsspalte, einer Fehlermeldung oder einer HTML-Seite erscheinen. Tests behaupten das, statt es zu versprechen.
- Der Wert reist per POST. Nie als Query-Parameter — der landet in Zugriffslogs.
- Bei Fehlschlag wird der **Statuscode** des Anbieters gezeigt, nie der Antwortkörper (D3 der Vorgänger-Spec).
- `einrichtung_status` und `eingabe_anfordern` geben nie einen Wert zurück, weder hinein noch heraus.
- Die schwebende Anfrage enthält **kein** Wertfeld. Das ist eine strukturelle Zusicherung, kein Vorsatz.
- Die Bindung bleibt Loopback (`PLUGIN_SETUP_MCP_HOST`, Vorgabe `127.0.0.1`). Nichts wird nach außen veröffentlicht.
- `SECURITY DEFINER`-Funktionen pinnen `search_path` und bauen kein dynamisches SQL.
- Migrationen sind idempotent (zweimal anwendbar) und kommen als **neue** Datei — `0001`–`0004` liegen bereits auf der laufenden Instanz.
- Datenbanktests laufen gegen die **echte** laufende Supabase (Container per `supabase-db` gematcht) und räumen auf, was sie anlegen, per uuid-präfigiertem Namen und **als `postgres`** — die Rolle `plugin_setup_agent` darf absichtlich nicht löschen.
- Unit-Tests machen keine Netzaufrufe. `pruefe` wird über seinen `fetch`-Parameter eingespritzt, ausgehende HTTP-Aufrufe über ihre jeweilige Naht.
- Nur offensichtlich erfundene Testwerte, nie ein echtes Geheimnis.
- Mutationsproben mit `PYTHONDONTWRITEBYTECODE=1` und geleertem `__pycache__` fahren — zwei Mutationen gleicher Bytezahl lassen CPython sonst veralteten Bytecode wiederverwenden und den falschen Test rot melden.
- Verifikation und Commit in **einer** Kette (`pytest … && git commit …`).
- Alle 81 bestehenden Tests in `spaces/plugin-setup/tests` bleiben grün.
- Conventional Commits, direkt auf `master`, nicht pushen — der Controller pusht.

---

## Dateien

| Datei | Verantwortung |
| --- | --- |
| `spaces/plugin-setup/db/0005_fenster_oberflaeche.sql` (neu) | Die zwei bewachten Funktionen, die das Fenster braucht: lesen (`zustand`) und wiederholen (`neu_aufnehmen`) |
| `spaces/plugin-setup/anfragen.py` (neu) | Die schwebende Anfrage: anlegen, per Token finden, verbrauchen, verfallen. Kennt keinen Wert und keine Datenbank |
| `spaces/plugin-setup/fenster.py` (neu) | Die HTTP-Oberfläche: Formular ausliefern, Wert entgegennehmen, Ergebnis zeigen. Kennt keine Datenbank, nur den internen Schreibweg |
| `spaces/plugin-setup/ablage.py` (ändern) | Zwei dünne Hüllen um die neuen SQL-Funktionen |
| `spaces/plugin-setup/werkzeuge.py` (ändern) | Die zwei neuen Agenten-Werkzeuge |
| `spaces/plugin-setup/server.py` (ändern) | Werkzeugliste ohne `schluessel_entgegennehmen`, Routen montiert |
| `spaces/plugin-setup/config/workspace/AGENTS.md` (ändern) | Der Agent nennt einen Link, statt ein Fenster zu treiben |
| `spaces/rowboat/rowboat/scripts/provision-oauth-token.py` (ändern) | Token-Beschaffung vom Dateischreiben trennen, damit sie wiederverwendbar wird |

---

## Task 1: Die Datenbank-Oberfläche des Fensters

**Files:**
- Create: `spaces/plugin-setup/db/0005_fenster_oberflaeche.sql`
- Modify: `spaces/plugin-setup/ablage.py`
- Test: `spaces/plugin-setup/tests/test_ablage.py`

**Interfaces:**
- Consumes: `plugin_setup.einrichtungen` und die Zustände aus `db/0002_state_machine.sql`; die Rolle `plugin_setup_agent` aus `0003`/`0004`.
- Produces:
  - SQL `plugin_setup.zustand(referenz text) RETURNS TABLE(status text, hinweis text)`
  - SQL `plugin_setup.neu_aufnehmen(referenz text) RETURNS void`
  - Python `ablage.zustand(referenz: str) -> dict` → `{"ok": True, "zustand": str, "hinweis": str}` oder `{"ok": False, "fehler": str}`
  - Python `ablage.neu_aufnehmen(referenz: str) -> dict` → `{"ok": True}` oder `{"ok": False, "fehler": str}`

**Warum eine Lesefunktion.** `plugin_setup_agent` hat absichtlich **kein** SELECT auf der Tabelle — `tests/test_ablage.py::test_rolle_kann_die_tabelle_nicht_lesen` hält das fest. `einrichtung_status` kann also nicht einfach abfragen. Die Funktionen bleiben die einzige Oberfläche, und `zustand` gibt nur zwei Spalten heraus, nie `vault_secret_id` und nie einen Wert.

- [ ] **Step 1: Write the failing tests**

In `spaces/plugin-setup/tests/test_ablage.py` anhängen:

```python
def test_zustand_liest_status_und_hinweis_als_agent_rolle():
    referenz = f"PYTEST_ZUSTAND_{uuid.uuid4().hex[:8].upper()}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    try:
        assert ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)["ok"] is True
        gelesen = ablage.zustand(referenz)
        assert gelesen["ok"] is True
        assert gelesen["zustand"] == "entgegengenommen"
        assert gelesen["hinweis"] == ""
        assert _FAKE_WERT not in repr(gelesen)

        assert ablage.fehlschlagen(referenz, "401")["ok"] is True
        nach = ablage.zustand(referenz)
        assert nach["zustand"] == "fehlgeschlagen"
        assert nach["hinweis"] == "401"
    finally:
        _cleanup(referenz)


def test_zustand_ist_fail_closed_bei_unbekannter_referenz():
    ergebnis = ablage.zustand(f"PYTEST_NIE_{uuid.uuid4().hex[:8].upper()}")
    assert ergebnis["ok"] is False


def test_neu_aufnehmen_gibt_nur_aus_fehlgeschlagen_frei():
    referenz = f"PYTEST_NEUAUF_{uuid.uuid4().hex[:8].upper()}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    try:
        assert ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)["ok"] is True

        # Aus 'entgegengenommen' heraus MUSS es scheitern -- sonst waere es
        # ein Weg, eine laufende Aufnahme zu verwerfen.
        zu_frueh = ablage.neu_aufnehmen(referenz)
        assert zu_frueh["ok"] is False
        assert ablage.zustand(referenz)["zustand"] == "entgegengenommen"

        assert ablage.fehlschlagen(referenz, "401")["ok"] is True
        assert ablage.neu_aufnehmen(referenz)["ok"] is True

        # Zeile UND Vault-Kopie sind weg, eine neue Aufnahme geht wieder.
        assert _psql_als_postgres_ok(
            f"SELECT count(*) FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';"
        ).strip() == "0"
        assert ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)["ok"] is True
    finally:
        _cleanup(referenz)


def test_neu_aufnehmen_laesst_keinen_verwaisten_vault_eintrag():
    referenz = f"PYTEST_NEUAUF2_{uuid.uuid4().hex[:8].upper()}"
    projekt = f"pytest-projekt-{uuid.uuid4().hex[:8]}"
    try:
        assert ablage.entgegennehmen(projekt, "demo-plugin", referenz, "bearer", _FAKE_WERT)["ok"] is True
        assert ablage.fehlschlagen(referenz, "401")["ok"] is True
        vorher = _psql_als_postgres_ok("SELECT count(*) FROM vault.secrets;").strip()
        assert ablage.neu_aufnehmen(referenz)["ok"] is True
        nachher = _psql_als_postgres_ok("SELECT count(*) FROM vault.secrets;").strip()
        assert int(nachher) == int(vorher) - 1, "neu_aufnehmen muss die Vault-Kopie mitloeschen"
    finally:
        _cleanup(referenz)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests/test_ablage.py -q -k "zustand or neu_aufnehmen"`
Expected: FAIL mit `AttributeError: module 'ablage' has no attribute 'zustand'`

- [ ] **Step 3: Write the migration**

`spaces/plugin-setup/db/0005_fenster_oberflaeche.sql`:

```sql
-- ============================================================================
-- plugin_setup: die zwei Funktionen, die das Eingabefenster braucht
-- ============================================================================
-- Eigene Datei, weil 0001-0004 bereits auf der laufenden Instanz angewandt
-- sind. Idempotent: CREATE OR REPLACE FUNCTION und wiederholbare GRANTs.
--
-- 1. zustand(referenz) -- lesen OHNE SELECT-Recht. Die Rolle
--    plugin_setup_agent hat absichtlich kein SELECT auf der Tabelle
--    (0004, belegt durch tests/test_ablage.py). einrichtung_status braucht
--    aber den Zustand. Diese Funktion gibt GENAU zwei Spalten heraus --
--    status und hinweis -- und nie vault_secret_id, nie einen Wert.
--
-- 2. neu_aufnehmen(referenz) -- ein Fehlschlag muss wiederholbar sein.
--    Ohne sie ist eine Referenz nach einem Tippfehler dauerhaft verbrannt:
--    die Zeile steht auf 'fehlgeschlagen', und ein zweiter Versuch
--    kollidiert an der UNIQUE-Constraint auf referenz_name.
--    NUR aus 'fehlgeschlagen' heraus -- aus 'entgegengenommen' waere es ein
--    Weg, eine laufende Aufnahme zu verwerfen, und aus 'uebernommen' ein Weg,
--    eine bereits uebergebene Einrichtung stillschweigend zu vergessen.
--    Sie LOESCHT Zeile und Vault-Kopie, statt den Status zurueckzusetzen:
--    dann bleibt `entgegennehmen` ein reines INSERT und muss nicht zwei
--    Formen kennen. Preis, bewusst getragen: der Fehlschlag-Eintrag geht
--    verloren. Der Agent hat ihn vorher ueber einrichtung_status gesehen.

BEGIN;

CREATE OR REPLACE FUNCTION plugin_setup.zustand(referenz text)
RETURNS TABLE(status text, hinweis text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = plugin_setup, pg_temp
AS $fn$
BEGIN
    RETURN QUERY
    SELECT e.status, COALESCE(e.hinweis, '')
    FROM plugin_setup.einrichtungen e
    WHERE e.referenz_name = referenz;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'plugin_setup.zustand: unbekannte referenz_name %', referenz
            USING ERRCODE = 'no_data_found';
    END IF;
END;
$fn$;

REVOKE ALL ON FUNCTION plugin_setup.zustand(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION plugin_setup.zustand(text) TO plugin_setup_agent;

CREATE OR REPLACE FUNCTION plugin_setup.neu_aufnehmen(referenz text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = plugin_setup, vault, pg_temp
AS $fn$
DECLARE
    v_status   text;
    v_vault_id uuid;
BEGIN
    SELECT status, vault_secret_id INTO v_status, v_vault_id
    FROM plugin_setup.einrichtungen
    WHERE referenz_name = referenz
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'plugin_setup.neu_aufnehmen: unbekannte referenz_name %', referenz
            USING ERRCODE = 'no_data_found';
    END IF;

    IF v_status <> 'fehlgeschlagen' THEN
        RAISE EXCEPTION 'plugin_setup.neu_aufnehmen: referenz % ist im Status %, erwartet fehlgeschlagen',
            referenz, v_status
            USING ERRCODE = 'invalid_parameter_value';
    END IF;

    IF v_vault_id IS NOT NULL THEN
        DELETE FROM vault.secrets WHERE id = v_vault_id;
    END IF;

    DELETE FROM plugin_setup.einrichtungen WHERE referenz_name = referenz;
END;
$fn$;

REVOKE ALL ON FUNCTION plugin_setup.neu_aufnehmen(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION plugin_setup.neu_aufnehmen(text) TO plugin_setup_agent;

COMMIT;
```

- [ ] **Step 4: Add the Python wrappers**

In `spaces/plugin-setup/ablage.py`, neben `verifizieren`/`fehlschlagen`/`uebernommen` (sie benutzen bereits `_uebergang`; `zustand` braucht eine eigene Hülle, weil sie eine Zeile liest):

```python
def zustand(referenz: str) -> dict:
    """Liest Zustand und Hinweis ueber die bewachte Funktion -- die Rolle
    hat kein SELECT auf der Tabelle. Gibt NIE vault_secret_id und nie
    einen Wert zurueck; die Funktion liefert nur zwei Spalten."""
    rc, out = _psql(
        f"SELECT status || '|' || hinweis FROM plugin_setup.zustand({_sql_literal(referenz)});"
    )
    if rc != 0:
        return {"ok": False, "fehler": f"Zustand nicht lesbar: {_erste_fehlerzeile(out)}"}
    zeile = out.strip()
    if "|" not in zeile:
        return {"ok": False, "fehler": "Zustand nicht lesbar: unerwartete Antwortform"}
    status, hinweis = zeile.split("|", 1)
    return {"ok": True, "zustand": status, "hinweis": hinweis}


def neu_aufnehmen(referenz: str) -> dict:
    """Gibt eine fehlgeschlagene Referenz fuer eine neue Aufnahme frei.
    Die Wache sitzt in der Datenbank (nur aus 'fehlgeschlagen')."""
    return _uebergang(f"SELECT plugin_setup.neu_aufnehmen({_sql_literal(referenz)});")
```

Und `_MIGRATION_PATHS` in `tests/test_eingang.py` sowie die Migrationsschleife in `deploy/bootstrap.sh` um `0005_fenster_oberflaeche.sql` ergänzen.

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests -q`
Expected: PASS, 85 Tests

- [ ] **Step 6: Mutation check**

Die Wache in `neu_aufnehmen` entfernen (den `IF v_status <> 'fehlgeschlagen'`-Block), Migration anwenden, `test_neu_aufnehmen_gibt_nur_aus_fehlgeschlagen_frei` fahren — muss ROT werden. Danach zurücksetzen und Migration erneut anwenden.

- [ ] **Step 7: Commit**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests -q && git add spaces/plugin-setup/db/0005_fenster_oberflaeche.sql spaces/plugin-setup/ablage.py spaces/plugin-setup/tests/test_ablage.py spaces/plugin-setup/tests/test_eingang.py spaces/plugin-setup/deploy/bootstrap.sh && git commit -m "feat(plugin-setup): the window's database surface -- read state, retry a failure"
```

---

## Task 2: Die schwebende Anfrage

**Files:**
- Create: `spaces/plugin-setup/anfragen.py`
- Test: `spaces/plugin-setup/tests/test_anfragen.py`

**Interfaces:**
- Consumes: nichts. Reines Prozessgedächtnis, keine Datenbank, kein Netz.
- Produces:
  - `anfragen.GUELTIGKEIT_SEKUNDEN = 900`
  - `anfragen.anlegen(projekt, plugin, referenz, art, ziel) -> Anfrage`
  - `anfragen.holen(token) -> Anfrage | None`
  - `anfragen.verbrauchen(token) -> Anfrage | None`
  - `anfragen.offen_fuer(referenz) -> Anfrage | None`
  - `Anfrage` mit den Feldern `token, projekt, plugin, referenz, art, ziel, ablauf` — **kein Wertfeld**

- [ ] **Step 1: Write the failing tests**

`spaces/plugin-setup/tests/test_anfragen.py`:

```python
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
    for verboten in ("wert", "secret", "token_wert", "value"):
        assert verboten not in felder or verboten == "token"


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests/test_anfragen.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'anfragen'`

- [ ] **Step 3: Write the module**

`spaces/plugin-setup/anfragen.py`:

```python
"""Die schwebende Anfrage: zwischen `eingabe_anfordern` und dem Absenden
des Formulars.

WARUM IM SPEICHER UND NICHT IN SUPABASE: eine schwebende Anfrage ist
Prozesszustand mit 15 Minuten Lebensdauer. Sie in die Datenbank zu legen
hiesse, einen Zustand zu persistieren, der einen Neustart nicht ueberleben
SOLL -- der Agent fordert dann einfach neu an.

WARUM DIESES MODUL KEINEN WERT KENNT: es gibt hier kein Feld, in dem ein
Geheimnis liegen koennte. Das ist eine strukturelle Zusicherung, kein
Vorsatz -- `test_anfrage_hat_strukturell_kein_wertfeld` haelt sie fest.
Der Wert existiert erst im POST des Formulars und verlaesst die
Verarbeitung nie wieder.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

GUELTIGKEIT_SEKUNDEN = 900  # 15 Minuten: lang genug fuer ein Anbieter-Portal,
                            # kurz genug, dass ein vergessener Link nicht
                            # wochenlang scharf bleibt.

_SPERRE = threading.Lock()
_OFFEN: dict[str, "Anfrage"] = {}


@dataclass(frozen=True)
class Anfrage:
    token: str
    projekt: str
    plugin: str
    referenz: str
    art: str
    ziel: str
    ablauf: float


def _abgelaufen(a: "Anfrage", jetzt: float) -> bool:
    return a.ablauf <= jetzt


def anlegen(projekt: str, plugin: str, referenz: str, art: str, ziel: str) -> Anfrage:
    a = Anfrage(
        token=secrets.token_urlsafe(32),
        projekt=projekt, plugin=plugin, referenz=referenz,
        art=art, ziel=ziel,
        ablauf=time.time() + GUELTIGKEIT_SEKUNDEN,
    )
    with _SPERRE:
        jetzt = time.time()
        for tot in [t for t, x in _OFFEN.items() if _abgelaufen(x, jetzt)]:
            del _OFFEN[tot]
        _OFFEN[a.token] = a
    return a


def holen(token: str) -> Anfrage | None:
    with _SPERRE:
        a = _OFFEN.get(token)
        if a is None:
            return None
        if _abgelaufen(a, time.time()):
            del _OFFEN[token]
            return None
        return a


def verbrauchen(token: str) -> Anfrage | None:
    with _SPERRE:
        a = _OFFEN.pop(token, None)
        if a is None or _abgelaufen(a, time.time()):
            return None
        return a


def offen_fuer(referenz: str) -> Anfrage | None:
    with _SPERRE:
        jetzt = time.time()
        for a in _OFFEN.values():
            if a.referenz == referenz and not _abgelaufen(a, jetzt):
                return a
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests/test_anfragen.py -q`
Expected: PASS, 6 Tests

- [ ] **Step 5: Commit**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests -q && git add spaces/plugin-setup/anfragen.py spaces/plugin-setup/tests/test_anfragen.py && git commit -m "feat(plugin-setup): the pending request, which structurally cannot hold a value"
```

---

## Task 3: Die zwei Agenten-Werkzeuge

**Files:**
- Modify: `spaces/plugin-setup/werkzeuge.py`
- Test: `spaces/plugin-setup/tests/test_werkzeuge.py`

**Interfaces:**
- Consumes: `anfragen.anlegen/offen_fuer/GUELTIGKEIT_SEKUNDEN` (Task 2); `ablage.zustand` (Task 1); `werkzeuge._ziel_pruefen(art, referenz, ziel)` (vorhanden, gibt `None` bei OK oder einen Fehlertext).
- Produces:
  - `werkzeuge.eingabe_anfordern(projekt, plugin, referenz, art, ziel="") -> dict`
  - `werkzeuge.einrichtung_status(referenz) -> dict`
  - `werkzeuge.FENSTER_BASIS` — die Basis-URL für Links, aus `PLUGIN_SETUP_FENSTER_BASIS`, Vorgabe `http://127.0.0.1:8131`

- [ ] **Step 1: Write the failing tests**

In `spaces/plugin-setup/tests/test_werkzeuge.py` anhängen:

```python
def test_eingabe_anfordern_gibt_link_und_ablauf_aber_nie_einen_wert():
    ergebnis = werkzeuge.eingabe_anfordern("proj", "demo", "OAUTH_BEARER_MCP_LINEAR_APP_MCP",
                                           "oauth", "https://mcp.linear.app/mcp")
    assert ergebnis["ok"] is True
    assert ergebnis["url"].startswith("http://127.0.0.1:8131/fenster/")
    assert len(ergebnis["url"].rsplit("/", 1)[1]) >= 32
    assert "T" in ergebnis["ablauf_iso"]
    for verboten in ("wert", "value", "secret"):
        assert verboten not in ergebnis


def test_eingabe_anfordern_prueft_ziel_vor_dem_anlegen(monkeypatch):
    """Dieselbe Wache wie schluessel_entgegennehmen -- sonst waere der
    Link der Weg, sie zu umgehen."""
    gelegt = []
    monkeypatch.setattr(werkzeuge.anfragen, "anlegen",
                        lambda *a, **k: gelegt.append(a) or (_ for _ in ()).throw(AssertionError))
    ergebnis = werkzeuge.eingabe_anfordern("proj", "demo", "OAUTH_BEARER_MCP_LINEAR_APP_MCP",
                                           "oauth", "http://mcp.linear.app/mcp")
    assert ergebnis["ok"] is False
    assert gelegt == [], "bei ungueltigem ziel darf keine Anfrage entstehen"


def test_einrichtung_status_meldet_angefordert_solange_der_link_offen_ist(monkeypatch):
    gerufen = []
    monkeypatch.setattr(werkzeuge.ablage, "zustand", lambda r: gerufen.append(r) or {"ok": True, "zustand": "x", "hinweis": ""})
    werkzeuge.eingabe_anfordern("proj", "demo", "PYTEST_STATUS_A", "bearer", "")
    ergebnis = werkzeuge.einrichtung_status("PYTEST_STATUS_A")
    assert ergebnis == {"ok": True, "zustand": "angefordert", "hinweis": ""}
    assert gerufen == [], "solange die Anfrage schwebt, wird die DB nicht gefragt"


def test_einrichtung_status_reicht_den_datenbankzustand_durch(monkeypatch):
    monkeypatch.setattr(werkzeuge.ablage, "zustand",
                        lambda r: {"ok": True, "zustand": "fehlgeschlagen", "hinweis": "401"})
    ergebnis = werkzeuge.einrichtung_status("PYTEST_STATUS_B")
    assert ergebnis == {"ok": True, "zustand": "fehlgeschlagen", "hinweis": "401"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests/test_werkzeuge.py -q -k "eingabe_anfordern or einrichtung_status"`
Expected: FAIL mit `AttributeError: module 'werkzeuge' has no attribute 'eingabe_anfordern'`

- [ ] **Step 3: Implement**

In `spaces/plugin-setup/werkzeuge.py`, oben `import anfragen` und `from datetime import datetime, timezone` ergänzen, dazu:

```python
FENSTER_BASIS = os.environ.get("PLUGIN_SETUP_FENSTER_BASIS", "http://127.0.0.1:8131").rstrip("/")


def eingabe_anfordern(projekt: str, plugin: str, referenz: str, art: str, ziel: str = "") -> dict:
    """Fordert eine Eingabe an und gibt einen EINMAL-LINK zurueck.

    Der Agent bekommt hier einen Zeiger, keinen Wert -- und kann auch
    keinen hineingeben. Der Link darf im Transkript landen: nach Gebrauch
    oder nach Ablauf ist er wertlos.

    Die Wachen sind dieselben wie in `schluessel_entgegennehmen` und laufen
    VOR dem Anlegen. Sonst waere dieser Weg genau die Luecke, die dort
    geschlossen wurde.
    """
    if art not in _ART_ERLAUBT:
        return {"ok": False, "fehler": f"unbekannte art: {art!r} (erlaubt: {sorted(_ART_ERLAUBT)})"}
    fehler = _ziel_pruefen(art, referenz, ziel)
    if fehler is not None:
        return {"ok": False, "fehler": fehler}

    a = anfragen.anlegen(projekt, plugin, referenz, art, ziel)
    ablauf = datetime.fromtimestamp(a.ablauf, tz=timezone.utc).isoformat()
    return {"ok": True, "referenz": referenz, "url": f"{FENSTER_BASIS}/fenster/{a.token}",
            "ablauf_iso": ablauf}


def einrichtung_status(referenz: str) -> dict:
    """Zustand einer Einrichtung -- nie ein Wert, nie ein Antwortkoerper.

    Schwebt noch eine Anfrage, ist der Zustand `angefordert`; die Datenbank
    kennt die Referenz dann naemlich noch gar nicht.
    """
    if anfragen.offen_fuer(referenz) is not None:
        return {"ok": True, "zustand": "angefordert", "hinweis": ""}
    return ablage.zustand(referenz)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests -q`
Expected: PASS, 95 Tests

- [ ] **Step 5: Mutation check**

Den `_ziel_pruefen`-Aufruf in `eingabe_anfordern` entfernen — `test_eingabe_anfordern_prueft_ziel_vor_dem_anlegen` muss ROT werden. Zurücksetzen.

- [ ] **Step 6: Commit**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests -q && git add spaces/plugin-setup/werkzeuge.py spaces/plugin-setup/tests/test_werkzeuge.py && git commit -m "feat(plugin-setup): the agent asks for input and reads state, and can do nothing else"
```

---

## Task 4: Das Formular

**Files:**
- Create: `spaces/plugin-setup/fenster.py`
- Test: `spaces/plugin-setup/tests/test_fenster.py`

**Interfaces:**
- Consumes: `anfragen.holen/verbrauchen` (Task 2).
- Produces:
  - `fenster.seite_fuer(anfrage) -> str` (HTML)
  - `fenster.ergebnisseite(ok: bool, referenz: str, hinweis: str) -> str`
  - `fenster.entgegennehmen(token: str, wert: str, schreiber) -> tuple[int, str]` — `schreiber` ist die Naht: eine Funktion mit der Signatur von `schluessel_entgegennehmen`. Gibt HTTP-Status und HTML zurück.

**Warum `schreiber` eingespritzt wird:** damit die Tests des Formulars ohne Datenbank und ohne Netz laufen — und damit der Testlauf beweisen kann, WELCHE Argumente ankommen.

- [ ] **Step 1: Write the failing tests**

`spaces/plugin-setup/tests/test_fenster.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests/test_fenster.py -q`
Expected: FAIL mit `ModuleNotFoundError: No module named 'fenster'`

- [ ] **Step 3: Implement**

`spaces/plugin-setup/fenster.py`:

```python
"""Das Eingabefenster -- die einzige Stelle, an der ein Wert entsteht.

Drei Regeln, alle aus E5 der Spec und alle von Tests gehalten:
  - Der Wert reist per POST. Nie als Query-Parameter -- der landet in
    Zugriffslogs, die niemand als Geheimnisspeicher betrachtet.
  - Keine Antwort spiegelt den Wert zurueck, auch nicht maskiert.
  - Ein Fehlschlag zeigt den STATUSCODE des Anbieters, nie den
    Antwortkoerper (D3 der Vorgaenger-Spec).

Dieses Modul kennt weder Datenbank noch OpenFang. Es bekommt den
Schreibweg als Argument -- so laufen seine Tests ohne Netz, und sie
koennen pruefen, WELCHE Argumente ankommen.
"""
from __future__ import annotations

import html as _html

import anfragen

_KOPF = (
    "<!doctype html><meta charset='utf-8'>"
    "<title>Plugin-Einrichtung</title>"
    "<style>body{font:16px system-ui;margin:3rem auto;max-width:34rem}"
    "input{width:100%;padding:.6rem;font:inherit}"
    "button{margin-top:1rem;padding:.6rem 1.2rem;font:inherit}"
    "code{background:#f2f2f2;padding:.1rem .3rem}</style>"
)


def seite_fuer(a: "anfragen.Anfrage") -> str:
    ref = _html.escape(a.referenz)
    if a.art == "oauth":
        return (f"{_KOPF}<h1>Anmeldung noetig</h1>"
                f"<p>Fuer <code>{ref}</code> meldest du dich beim Anbieter an. "
                f"Der Token wird direkt hier entgegengenommen und nie angezeigt.</p>"
                f"<form method='post'><button>Anmeldung starten</button></form>")
    return (f"{_KOPF}<h1>Schluessel eintragen</h1>"
            f"<p>Fuer <code>{ref}</code>. Der Wert wird sofort geprueft und dann an "
            f"OpenFang uebergeben; er erscheint in keiner Antwort und in keinem Protokoll.</p>"
            f"<form method='post'>"
            f"<input name='wert' type='password' autocomplete='off' autofocus>"
            f"<button>Eintragen</button></form>")


def ergebnisseite(ok: bool, referenz: str, hinweis: str) -> str:
    ref = _html.escape(referenz)
    if ok:
        return (f"{_KOPF}<h1>Uebernommen</h1><p><code>{ref}</code> ist geprueft und bei "
                f"OpenFang. Du kannst dieses Fenster schliessen.</p>")
    return (f"{_KOPF}<h1>Nicht uebernommen</h1>"
            f"<p><code>{ref}</code> wurde vom Anbieter abgelehnt: "
            f"<code>{_html.escape(hinweis)}</code>. Der Wert wurde nicht uebergeben.</p>"
            f"<p>Der Agent kann eine neue Eingabe anfordern.</p>")


def entgegennehmen(token: str, wert: str, schreiber) -> tuple[int, str]:
    """Verbraucht das Token und reicht den Wert an `schreiber` weiter.

    Ein unbekanntes, verbrauchtes oder abgelaufenes Token fuehrt zu 404 --
    OHNE den Wert anzufassen.
    """
    a = anfragen.verbrauchen(token)
    if a is None:
        return 404, f"{_KOPF}<h1>Link ungueltig</h1><p>Abgelaufen oder schon benutzt.</p>"
    ergebnis = schreiber(projekt=a.projekt, plugin=a.plugin, referenz=a.referenz,
                         art=a.art, wert=wert, ziel=a.ziel)
    if ergebnis.get("ok"):
        return 200, ergebnisseite(True, a.referenz, "")
    hinweis = str(ergebnis.get("status", ergebnis.get("fehler", "unbekannt")))
    return 200, ergebnisseite(False, a.referenz, hinweis)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests -q`
Expected: PASS, 101 Tests

- [ ] **Step 5: Mutation check**

In `entgegennehmen` `anfragen.verbrauchen` durch `anfragen.holen` ersetzen — `test_token_ist_einmalig` muss ROT werden. Zurücksetzen.

- [ ] **Step 6: Commit**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests -q && git add spaces/plugin-setup/fenster.py spaces/plugin-setup/tests/test_fenster.py && git commit -m "feat(plugin-setup): the form -- the one place a value exists"
```

---

## Task 5: Verdrahtung — der Agent verliert den Schreibweg

**Files:**
- Modify: `spaces/plugin-setup/server.py`
- Modify: `spaces/plugin-setup/config/workspace/AGENTS.md`
- Test: `spaces/plugin-setup/tests/test_server_werkzeugliste.py` (neu)

**Interfaces:**
- Consumes: `werkzeuge.eingabe_anfordern`, `werkzeuge.einrichtung_status` (Task 3); `fenster.seite_fuer`, `fenster.entgegennehmen` (Task 4); `anfragen.holen` (Task 2).
- Produces: `server.WERKZEUGE` — das Tupel der fünf Werkzeuge, die der Agent sieht.

- [ ] **Step 1: Write the failing test**

`spaces/plugin-setup/tests/test_server_werkzeugliste.py`:

```python
"""Die Werkzeugliste IST die Sicherheitsgrenze -- also wird sie geprueft."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402


def test_der_agent_sieht_genau_fuenf_werkzeuge():
    namen = {fn.__name__ for fn in server.WERKZEUGE}
    assert namen == {
        "plugin_bedarf",
        "eingabe_anfordern",
        "einrichtung_status",
        "plugin_installieren",
        "plugin_werkzeug_binden",
    }


def test_schluessel_entgegennehmen_ist_kein_werkzeug_mehr():
    """Der Kern dieses ganzen Entwurfs: es gibt keinen Weg mehr, ueber den
    ein Agent einen Wert uebergeben koennte. Die Funktion existiert
    weiterhin -- als interner Schreibweg des Formulars."""
    namen = {fn.__name__ for fn in server.WERKZEUGE}
    assert "schluessel_entgegennehmen" not in namen
    import werkzeuge
    assert callable(werkzeuge.schluessel_entgegennehmen)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests/test_server_werkzeugliste.py -q`
Expected: FAIL — `schluessel_entgegennehmen` ist noch in `WERKZEUGE`

- [ ] **Step 3: Change the tool list and mount the routes**

In `spaces/plugin-setup/server.py` das Tupel ersetzen:

```python
WERKZEUGE = (
    werkzeuge.plugin_bedarf,
    werkzeuge.eingabe_anfordern,
    werkzeuge.einrichtung_status,
    werkzeuge.plugin_installieren,
    werkzeuge.plugin_werkzeug_binden,
)
```

und in `main()` nach dem Registrieren der Werkzeuge die Routen montieren:

```python
        from starlette.responses import HTMLResponse

        @server.custom_route("/fenster/{token}", methods=["GET"])
        async def _fenster_zeigen(request):
            a = anfragen.holen(request.path_params["token"])
            if a is None:
                return HTMLResponse("Link ungueltig.", status_code=404)
            return HTMLResponse(fenster.seite_fuer(a))

        @server.custom_route("/fenster/{token}", methods=["POST"])
        async def _fenster_annehmen(request):
            formular = await request.form()
            status, html = fenster.entgegennehmen(
                request.path_params["token"],
                str(formular.get("wert", "")),
                werkzeuge.schluessel_entgegennehmen,
            )
            return HTMLResponse(html, status_code=status)
```

Dazu oben `import anfragen` und `import fenster`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests -q`
Expected: PASS, 103 Tests

- [ ] **Step 5: Rewrite the agent brief**

In `spaces/plugin-setup/config/workspace/AGENTS.md`: den Abschnitt „Dein Fenster" samt UNGEKLAERT-Block **ersetzen** durch eine Beschreibung des neuen Ablaufs — der Agent ruft `eingabe_anfordern`, nennt dem Betreiber den Link **und den Referenznamen im Klartext**, wartet, fragt `einrichtung_status`. Ausdrücklich aufnehmen: der Agent nimmt **nie** einen Wert entgegen, und wenn der Betreiber ihm einen schickt, lehnt er ab und verweist auf den Link. Die vier D7-Verbote bleiben wörtlich stehen. Die Beschreibung von `schluessel_entgegennehmen` entfällt, dafür kommen die beiden neuen Werkzeuge mit ihren echten Rückgabefeldern hinein (`url`, `ablauf_iso`; `zustand`, `hinweis`).

- [ ] **Step 6: Commit**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests -q && git add spaces/plugin-setup/server.py spaces/plugin-setup/config/workspace/AGENTS.md spaces/plugin-setup/tests/test_server_werkzeugliste.py && git commit -m "feat(plugin-setup): the agent loses the write path, and the window gets it"
```

---

## Task 6: OAuth erbt den Provisioner

**Files:**
- Modify: `spaces/rowboat/rowboat/scripts/provision-oauth-token.py`
- Modify: `spaces/plugin-setup/fenster.py`
- Test: `spaces/plugin-setup/tests/test_fenster.py`

**Interfaces:**
- Consumes: `derive_name`, `discover_resource_metadata`, `discover_authorization_server`, `register_client`, `wait_for_callback` (alle vorhanden).
- Produces:
  - `provision_oauth_token.token_holen(mcp_url: str) -> tuple[str, str]` — `(referenzname, token)`; macht Discovery, Registrierung, Browser-Anmeldung, Callback und Token-Tausch, schreibt **nichts**.
  - `fenster.oauth_entgegennehmen(token, beschaffer, schreiber) -> tuple[int, str]`

**Warum ein Refactor und kein Abschreiben.** Token-Tausch und Dateischreiben stecken heute zusammen in `main()`. Die dreissig Zeilen zu kopieren wäre genau die Driftklasse, gegen die dieser ganze Space gebaut wurde. Also wird die Beschaffung einmal herausgezogen; `main()` ruft sie danach selbst auf und schreibt wie bisher.

- [ ] **Step 1: Write the failing test**

In `spaces/plugin-setup/tests/test_fenster.py` anhängen:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests/test_fenster.py -q -k oauth`
Expected: FAIL mit `AttributeError: module 'fenster' has no attribute 'oauth_entgegennehmen'`

- [ ] **Step 3: Split the provisioner**

In `spaces/rowboat/rowboat/scripts/provision-oauth-token.py` aus `main()` alles von der Discovery bis zum Token-Tausch in eine neue Funktion ziehen:

```python
def token_holen(mcp_url: str) -> tuple[str, str]:
    """Beschafft einen OAuth-Bearer fuer `mcp_url` und gibt
    (referenzname, token) zurueck -- schreibt NICHTS.

    Herausgezogen aus main(), damit das Eingabefenster denselben Fluss
    benutzt, statt ihn abzuschreiben. main() ruft diese Funktion und
    schreibt danach wie bisher in --out.
    """
```

`main()` besteht danach aus: Argumente parsen, `token_holen(...)` rufen, Datei schreiben. Der Wert wird weiterhin nirgends geprintet.

- [ ] **Step 4: Add the oauth branch to the form**

In `spaces/plugin-setup/fenster.py`:

```python
def oauth_entgegennehmen(token: str, beschaffer, schreiber) -> tuple[int, str]:
    """Wie `entgegennehmen`, nur dass der Wert nicht aus dem Formular kommt,
    sondern aus dem OAuth-Fluss des Anbieters.

    `beschaffer(mcp_url) -> (name, token)` ist die Naht zum Provisioner.
    Eine Ausnahme daraus wird NICHT durchgereicht: ihre Nachricht koennte
    den Token enthalten.
    """
    a = anfragen.verbrauchen(token)
    if a is None:
        return 404, f"{_KOPF}<h1>Link ungueltig</h1><p>Abgelaufen oder schon benutzt.</p>"
    try:
        _name, wert = beschaffer(a.ziel)
    except Exception:  # noqa: BLE001 -- die Nachricht koennte den Token tragen
        return 200, ergebnisseite(False, a.referenz, "Anmeldung abgebrochen oder fehlgeschlagen")
    ergebnis = schreiber(projekt=a.projekt, plugin=a.plugin, referenz=a.referenz,
                         art=a.art, wert=wert, ziel=a.ziel)
    if ergebnis.get("ok"):
        return 200, ergebnisseite(True, a.referenz, "")
    return 200, ergebnisseite(False, a.referenz,
                              str(ergebnis.get("status", "unbekannt")))
```

Und in `server.py` die POST-Route nach `a.art` verzweigen lassen: bei `oauth` `fenster.oauth_entgegennehmen(token, provision_oauth_token.token_holen, werkzeuge.schluessel_entgegennehmen)`, sonst wie bisher.

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests -q`
Expected: PASS, 105 Tests

- [ ] **Step 6: Commit**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests -q && git add spaces/rowboat/rowboat/scripts/provision-oauth-token.py spaces/plugin-setup/fenster.py spaces/plugin-setup/tests/test_fenster.py spaces/plugin-setup/server.py && git commit -m "feat(plugin-setup): oauth inherits the provisioner instead of copying it"
```

---

## Task 7: Der Beweis

**Files:**
- Create: `spaces/plugin-setup/tests/test_fenster_live.py`
- Modify: `E2E-PROOF.md` (Repo-Wurzel — **nicht** unter `spaces/rowboat/`)

**Interfaces:**
- Consumes: alles aus Task 1–6; die laufende Supabase; einen laufenden Sidecar.

- [ ] **Step 1: Write the live test**

`spaces/plugin-setup/tests/test_fenster_live.py` — opt-in ueber
`PLUGIN_SETUP_FENSTER_LIVE=1`, sonst `pytest.skip`. Der Test faehrt den
echten Sidecar in einem Unterprozess hoch, geht den ganzen Weg durch das
Formular und behauptet an sechs Stellen, dass der Wert nicht auftaucht:

```python
"""Live: der Wert geht durch das Formular in den Vault -- und sonst nirgends.

Opt-in wie die uebrigen Live-Beweise: ohne PLUGIN_SETUP_FENSTER_LIVE=1
wird uebersprungen, nicht bestanden. Ein Beweis, der sich ohne Umgebung
selbst gruen meldet, ist wertlos.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_LIVE = os.environ.get("PLUGIN_SETUP_FENSTER_LIVE", "").strip() == "1"
_HIER = Path(__file__).resolve().parents[1]
_FAKE = f"offensichtlich-erfunden-fenster-{uuid.uuid4().hex}"

pytestmark = pytest.mark.skipif(not _LIVE, reason="PLUGIN_SETUP_FENSTER_LIVE != 1")


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _psql_als_postgres(sql: str) -> str:
    behaelter = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                               capture_output=True, text=True, check=True)
    name = next(z.strip() for z in behaelter.stdout.splitlines() if "supabase-db" in z)
    res = subprocess.run(["docker", "exec", "-i", name, "psql", "-U", "postgres",
                          "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-tA"],
                         input=sql, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout


def _mcp(basis: str, methode: str, params: dict, sid: str = "") -> tuple[dict, str]:
    kopf = {"Content-Type": "application/json",
            "Accept": "application/json, text/event-stream"}
    if sid:
        kopf["Mcp-Session-Id"] = sid
    rumpf = json.dumps({"jsonrpc": "2.0", "id": 1, "method": methode,
                        "params": params}).encode()
    anfrage = urllib.request.Request(f"{basis}/mcp", data=rumpf, headers=kopf,
                                     method="POST")
    with urllib.request.urlopen(anfrage, timeout=30) as antwort:
        neue_sid = antwort.headers.get("Mcp-Session-Id", sid)
        roh = antwort.read().decode("utf-8", "replace")
    for zeile in roh.splitlines():
        if zeile.startswith("data: "):
            roh = zeile[6:]
            break
    return json.loads(roh), neue_sid


def test_der_wert_erreicht_den_vault_und_steht_in_keiner_antwort():
    port = _freier_port()
    referenz = f"PYTEST_FENSTER_{uuid.uuid4().hex[:8].upper()}"
    umgebung = dict(os.environ,
                    PLUGIN_SETUP_MCP_HOST="127.0.0.1",
                    PLUGIN_SETUP_MCP_PORT=str(port),
                    PLUGIN_SETUP_FENSTER_BASIS=f"http://127.0.0.1:{port}",
                    PYTHONDONTWRITEBYTECODE="1")
    protokoll = Path(os.environ.get("TMP", "/tmp")) / f"fenster-live-{port}.log"
    with protokoll.open("w", encoding="utf-8") as aus:
        kind = subprocess.Popen([sys.executable, "server.py"], cwd=str(_HIER),
                                env=umgebung, stdout=aus, stderr=subprocess.STDOUT)
    try:
        basis = f"http://127.0.0.1:{port}"
        frist = time.time() + 60
        while time.time() < frist:
            try:
                urllib.request.urlopen(f"{basis}/mcp", timeout=2)
            except Exception as fehler:  # noqa: BLE001 -- 406 heisst: er lebt
                if "406" in str(fehler):
                    break
            time.sleep(1)
        else:
            pytest.fail("Sidecar ist nicht hochgekommen")

        _, sid = _mcp(basis, "initialize",
                      {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "live", "version": "1"}})
        antwort, _ = _mcp(basis, "tools/call",
                          {"name": "eingabe_anfordern",
                           "arguments": {"projekt": "pytest-live", "plugin": "demo-plugin",
                                         "referenz": referenz, "art": "bearer", "ziel": ""}},
                          sid)
        text = antwort["result"]["content"][0]["text"]
        assert _FAKE not in text, "die Anforderung darf keinen Wert kennen"
        url = json.loads(text)["url"]

        with urllib.request.urlopen(url, timeout=15) as seite:
            seite_html = seite.read().decode("utf-8", "replace")
        assert referenz in seite_html
        assert _FAKE not in seite_html

        daten = urllib.parse.urlencode({"wert": _FAKE}).encode()
        with urllib.request.urlopen(urllib.request.Request(url, data=daten),
                                    timeout=90) as ergebnis:
            ergebnis_html = ergebnis.read().decode("utf-8", "replace")
        assert _FAKE not in ergebnis_html

        status = _psql_als_postgres(
            "SELECT status FROM plugin_setup.einrichtungen "
            f"WHERE referenz_name = '{referenz}';").strip()
        assert status == "fehlgeschlagen", (
            "ein erfundener Wert MUSS beim Anbieter durchfallen -- alles andere "
            "hiesse, die Verifikation prueft nicht wirklich")

        leck = _psql_als_postgres(
            "SELECT count(*) FROM plugin_setup.einrichtungen "
            f"WHERE hinweis LIKE '%{_FAKE}%';").strip()
        assert leck == "0"
        assert _FAKE not in protokoll.read_text(encoding="utf-8", errors="replace")
    finally:
        kind.terminate()
        kind.wait(timeout=30)
        _psql_als_postgres(
            "DELETE FROM vault.secrets WHERE id IN (SELECT vault_secret_id FROM "
            f"plugin_setup.einrichtungen WHERE referenz_name = '{referenz}' "
            "AND vault_secret_id IS NOT NULL); "
            f"DELETE FROM plugin_setup.einrichtungen WHERE referenz_name = '{referenz}';")
        protokoll.unlink(missing_ok=True)
```

Die Zusicherung `status == "fehlgeschlagen"` ist Absicht und diskriminierend:
ein erfundener Wert MUSS beim echten Anbieter durchfallen. Stuende dort
`uebernommen`, waere die Verifikation kaputt — deshalb wird nicht
`in ("fehlgeschlagen", "uebernommen")` geprueft.

- [ ] **Step 2: Run it both ways**

Run ohne Opt-in: `PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests/test_fenster_live.py -q` → skipped.
Run mit Opt-in: `PLUGIN_SETUP_FENSTER_LIVE=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests/test_fenster_live.py -q` → passed.

Beide Richtungen sind Pflicht: ein Live-Beweis, der sich ohne Umgebung selbst grün meldet, ist wertlos.

- [ ] **Step 3: Write Part VI**

In `E2E-PROOF.md` einen Teil VI anhängen: was gelaufen ist, mit den echten Ausgabezeilen, und ein Abschnitt „was das NICHT beweist". Dort gehört mindestens hinein: ob der OAuth-Zweig live gelaufen ist oder nur gemockt, und ob das Formular im Container-Betrieb geprüft wurde oder nur am Host.

- [ ] **Step 4: Commit**

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest spaces/plugin-setup/tests -q && git add spaces/plugin-setup/tests/test_fenster_live.py E2E-PROOF.md && git commit -m "test(plugin-setup): live proof that the value reaches the vault and no answer"
```

---

## Reihenfolge und Abhängigkeiten

1 und 2 sind unabhängig voneinander. 3 braucht 1 und 2. 4 braucht 2. 5 braucht 3 und 4. 6 braucht 4 und 5. 7 braucht alles.

## Was diesen Plan zum Scheitern brächte

- **`schluessel_entgegennehmen` bleibt in der Werkzeugliste.** Dann ist alles andere Kosmetik — der Agent hätte weiterhin einen Weg, den Wert zu übergeben. Deshalb ist es in Task 5 ein eigener Test, nicht eine Zeile im Commit.
- **Die schwebende Anfrage bekommt „vorübergehend" ein Wertfeld.** Dann ist die Zusicherung wieder Vorsatz statt Struktur. Der Feldtest in Task 2 existiert genau dafür.
- **Der OAuth-Fluss wird abgeschrieben statt geteilt.** Dann driften Provisioner und Fenster auseinander, und der Referenzname wird an zwei Stellen anders abgeleitet — dieselbe Klasse, die diesen Space schon zweimal getroffen hat.
- **Eine Ausnahme aus dem Beschaffer wird durchgereicht.** Ihre Nachricht kann den Token tragen. Deshalb fängt Task 6 sie stumpf ab.
