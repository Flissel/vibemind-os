"""plugin-setup-Werkzeuge -- der eigenstaendige Space aus Aufgabe 6.

Bindet die Bausteine der Aufgaben 1-5 zu vier MCP-Werkzeugen zusammen:

  plugin_bedarf(projekt, plugin)             -- was braucht das Plugin?
  schluessel_entgegennehmen(projekt, plugin, referenz, art, wert, ziel="")
                                              -- Supabase -> pruefen -> OpenFang
  plugin_installieren(projekt, plugin, komponenten=None) -- Rowboat-Install
  plugin_werkzeug_binden(projekt, plugin, komponente)    -- Rowboat-Tool-Bindung

VERBOTSLISTE (Global Constraint, s. Brief): kein Werkzeug gibt je einen
Credential-WERT zurueck. `schluessel_entgegennehmen` protokolliert/gibt nur
die `referenz` (den Namen) zurueck, nie `wert`. Getestet in
tests/test_werkzeuge.py.

REIHENFOLGE in `schluessel_entgegennehmen` -- Global Constraint #4,
BUCHSTAEBLICH wie im Brief (Review Runde 1, I2: eine fruehere Fassung
dieser Datei tauschte `verifizieren()` und die OpenFang-Uebergabe, das
wurde per Review zurueckgewiesen und ist hier restauriert):
  1. ablage.entgegennehmen(...)          Supabase: Status `entgegengenommen`
  2. pruefung.pruefe(...)                 Aufgabe 5, externer Anbieter-Check
  3a. NICHT gut  -> ablage.fehlschlagen(referenz, str(status)) -- Supabase-
      Kopie bleibt zur Fehlersuche stehen (entgegengenommen -> fehlgeschlagen
      ist ein gueltiger DB-Uebergang).
  3b. GUT        -> ablage.verifizieren(referenz) (Status `verifiziert`) ->
      _openfang_uebernehmen(...) (Aufgabe 1) -> bei dessen Erfolg
      ablage.uebernommen(referenz) (loescht die Supabase-Kopie, Status
      `uebernommen`).
  Scheitert die OpenFang-Uebergabe NACH bestandener Verifikation: die Zeile
  bleibt bewusst auf `verifiziert` stehen, die Vault-Kopie bleibt erhalten
  -- das ist KEIN Credential-Fehler (der Wert ist gut, nur die Uebergabe
  scheiterte), also wird `fehlschlagen()` hier NICHT aufgerufen (0002
  erlaubt sie ohnehin nur aus `entgegengenommen`, und diese Kante absichtlich
  NICHT zu oeffnen ist eine 0002-Entscheidung, die dieser Task nicht neu
  aufrollt). `uebernommen()` bleibt aus `verifiziert` heraus aufrufbar --
  ein spaeterer, manueller Retry (z.B. per `ablage.uebernommen(referenz)`,
  sobald OpenFang wieder erreichbar ist) kann also noch gelingen; ein
  erneuter Aufruf von `schluessel_entgegennehmen` selbst mit derselben
  `referenz` kollidiert dagegen an der UNIQUE-Constraint des ERSTEN
  Schritts (kein SELECT-Recht fuer `plugin_setup_agent`, s. `ablage.py`,
  daher kein automatisches Wiederaufsetzen ueber dieses Werkzeug -- ein
  bewusst nicht geschlossener Rand, s. Bericht). Meldet OpenFang `409`
  (`reference_exists`), wird NICHT automatisch mit `overwrite=true`
  ueberschrieben und die Supabase-Kopie bleibt stehen -- ein belegter Name
  kann ein anderer, bewusst vom Betreiber gesetzter Wert sein; das braucht
  eine Entscheidung des Betreibers, keine automatische Annahme.
  Nichts wird an OpenFang uebergeben, wenn Schritt 2 nicht `gut` ist.

FAIL-SOFT (Muster spaces/marketing/claw/werkzeuge.py): keine Funktion wirft;
Rueckgabe ist immer ein Objekt mit `ok`. Ohne konfigurierte Ziele
(ROWBOAT_URL/PLUGIN_SETUP_OPENFANG_URL fehlt) scheitert ein Werkzeug
freundlich -- nie in einem Teilzustand.
"""
from __future__ import annotations

import json
import os
import uuid
import urllib.request

from pruefung import pruefe
import ablage

FEHLER_MAXLAENGE = 300

# Der pinned Catalog-Digest des OpenAI-Plugin-Katalogs, den Rowboats v1-API
# fuer jede Preview-/Install-Anfrage verlangt (kein optionaler Parameter --
# `query()` in _responses.ts wirft `request_invalid`, fehlt er). Default =
# der Wert aus spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/
# domain/catalog.ts::PINNED_PLUGIN_CATALOG_DIGEST (gelesen 11.09.2026); per
# ROWBOAT_CATALOG_DIGEST override-bar, falls der Katalog neu gepinnt wird.
_PINNED_CATALOG_DIGEST_DEFAULT = "5c9ea0690406824b3e78751ee0bc7765e3d4a7d0ae40afdbd9f4757c22666c94"


def _ohne_schluessel(text: str) -> str:
    for name in ("ROWBOAT_API_KEY", "PLUGIN_SETUP_OPENFANG_API_KEY"):
        wert = os.environ.get(name, "")
        if wert and wert in text:
            text = text.replace(wert, "<schluessel>")
    return text


def _roh_anfrage(url: str, daten, method: str, kopfzeilen: dict) -> tuple:
    """Der einzige echte Netzgriff -- Tests ersetzen genau diese Funktion."""
    anfrage = urllib.request.Request(
        url, data=daten, method=method,
        headers={"Content-Type": "application/json", **kopfzeilen})
    with urllib.request.urlopen(anfrage, timeout=30) as antwort:
        return antwort.status, antwort.read().decode("utf-8", "replace")


def _rowboat_catalog_digest() -> str:
    return os.environ.get("ROWBOAT_CATALOG_DIGEST", "").strip() or _PINNED_CATALOG_DIGEST_DEFAULT


def _rowboat(pfad: str, method: str = "GET", nutzlast: dict | None = None,
             extra_kopfzeilen: dict | None = None) -> dict:
    """Ein Aufruf gegen Rowboats v1-Plugin-API. Wirft nie."""
    basis = os.environ.get("ROWBOAT_URL", "").strip().rstrip("/")
    schluessel = os.environ.get("ROWBOAT_API_KEY", "").strip()
    if not basis or not schluessel:
        fehlt = [n for n, w in (("ROWBOAT_URL", basis), ("ROWBOAT_API_KEY", schluessel)) if not w]
        return {"ok": False, "fehler": "Rowboat nicht eingerichtet, es fehlt: " + ", ".join(fehlt)}
    daten = None if nutzlast is None else json.dumps(nutzlast).encode("utf-8")
    kopfzeilen = {"Authorization": f"Bearer {schluessel}", **(extra_kopfzeilen or {})}
    try:
        status, rumpf = _roh_anfrage(basis + pfad, daten, method, kopfzeilen=kopfzeilen)
        try:
            geparst = json.loads(rumpf) if rumpf else {}
        except Exception:  # noqa: BLE001
            geparst = {"roh": rumpf[:FEHLER_MAXLAENGE]}
        if status not in (200, 201):
            return {"ok": False, "fehler": _ohne_schluessel(
                f"Rowboat HTTP {status}: {json.dumps(geparst)[:FEHLER_MAXLAENGE]}")}
        return {"ok": True, "daten": geparst}
    except Exception as e:  # noqa: BLE001 -- fail-soft ist der Vertrag
        return {"ok": False, "fehler": _ohne_schluessel(
            f"Rowboat nicht erreichbar ({type(e).__name__}: {e})")}


def _art_aus_referenzname(name: str) -> str:
    """Leitet `art` (oauth/connector) aus dem OpenFang-Referenznamen ab --
    derselben Namensform, die Rowboats credential-naming.ts erzeugt
    (OAUTH_BEARER_<host>..., CONNECTOR_<app>). NUR diese beiden Praefixe
    sind sicher ableitbar.

    C2-FIX (Review Runde 1, 2026-09-11): frueher fiel jeder unerkannte Name
    (z.B. `OPENAI_API_KEY`) auf `"bearer"` zurueck -- und `art=bearer`
    bedeutet in pruefung.py hartcodiert "ruf https://api.github.com/user
    mit diesem Wert auf". Ueber `plugin_bedarf` haette das jeden
    nicht-GitHub-Bearer-Wert (z.B. einen OpenAI-Key) automatisch Richtung
    GitHub geschickt, sobald ein Agent (s. AGENTS.md: "art direkt
    weitergeben") die geratene `art` unbesehen uebernimmt -- ein Wert, der
    seinen Pfad verlaesst. Fail closed statt raten: alles ausser den zwei
    sicher ableitbaren Praefixen wird `"unbekannt"`, nie `"bearer"`.
    `schluessel_entgegennehmen`/`ablage.entgegennehmen` lehnen `art` ausser-
    halb von {bearer, oauth, connector} ohnehin ab, BEVOR irgendetwas in
    Supabase geschrieben wird -- ein Aufrufer, der `art="bearer"` fuer
    einen echten GitHub-Token explizit waehlt, bleibt davon unberuehrt
    (das ist der legitime, im Task 5 vorgesehene Anwendungsfall)."""
    if name.startswith("OAUTH_BEARER_"):
        return "oauth"
    if name.startswith("CONNECTOR_"):
        return "connector"
    return "unbekannt"


def plugin_bedarf(projekt: str, plugin: str) -> dict:
    """Welche Credentials braucht `plugin` fuer `projekt`? Liest Rowboats
    Preview (`credentialSlots`) -- nur lesend, legt nichts an.

    Rueckgabe: {"ok": True, "daten": [{"name", "art", "quelle", "vorhanden"}]}
    `quelle` benennt, woher die Angabe stammt (hier immer "rowboat-preview",
    da Rowboat bislang die einzige Quelle ist, die Aufgabe 6 kennt); die
    dritte Pruefform "connector" bringt zusaetzlich OPENAI_API_KEY mit (s.
    plugin-service.shared.ts) -- das ist normal, kein Fehler. `art` kann
    `"unbekannt"` sein (C2-Fix: NIE automatisch `"bearer"` geraten, s.
    `_art_aus_referenzname`) -- `schluessel_entgegennehmen` lehnt eine
    solche `art` ab; ein Mensch muss sie dann bewusst waehlen.
    """
    if not projekt or not plugin:
        return {"ok": False, "fehler": "projekt und plugin sind Pflicht"}
    digest = _rowboat_catalog_digest()
    antwort = _rowboat(
        f"/api/v1/projects/{projekt}/plugins/{plugin}?catalogDigest={digest}")
    if not antwort["ok"]:
        return antwort
    slots = (antwort["daten"] or {}).get("credentialSlots") or []
    bedarf = [
        {
            "name": slot.get("name", ""),
            "art": _art_aus_referenzname(str(slot.get("name", ""))),
            "quelle": "rowboat-preview",
            "vorhanden": bool(slot.get("configured", False)),
        }
        for slot in slots if isinstance(slot, dict)
    ]
    return {"ok": True, "daten": bedarf}


def _openfang_uebernehmen(referenz: str, wert: str) -> dict:
    """Aufgabe 1: POST /api/credentials/store gegen den ISOLIERTEN OpenFang
    (127.0.0.1:4273, eigener OPENFANG_HOME -- NIE :4200/~/.openfang/, s.
    Global Constraints). Wirft nie; `wert` erscheint in keinem Fehlertext."""
    basis = os.environ.get("PLUGIN_SETUP_OPENFANG_URL", "").strip().rstrip("/")
    schluessel = os.environ.get("PLUGIN_SETUP_OPENFANG_API_KEY", "").strip()
    if not basis or not schluessel:
        fehlt = [n for n, w in (
            ("PLUGIN_SETUP_OPENFANG_URL", basis),
            ("PLUGIN_SETUP_OPENFANG_API_KEY", schluessel)) if not w]
        return {"ok": False, "status": 0,
                "fehler": "OpenFang nicht eingerichtet, es fehlt: " + ", ".join(fehlt)}
    nutzlast = json.dumps({"reference": referenz, "value": wert, "overwrite": False}).encode("utf-8")
    try:
        status, rumpf = _roh_anfrage(
            basis + "/api/credentials/store", nutzlast, "POST",
            {"Authorization": f"Bearer {schluessel}"})
    except Exception as e:  # noqa: BLE001
        # Verteidigung in der Tiefe: eine Netzwerkfehlermeldung sollte nie
        # `wert` enthalten (er reist nur im Anfragekoerper), aber wir
        # scrubben trotzdem, statt uns darauf zu verlassen.
        text = _ohne_schluessel(f"OpenFang nicht erreichbar ({type(e).__name__}: {e})")
        if wert and wert in text:
            text = text.replace(wert, "<wert>")
        return {"ok": False, "status": 0, "fehler": text}
    if status != 200:
        return {"ok": False, "status": status,
                "fehler": f"OpenFang HTTP {status}"}
    return {"ok": True, "status": status}


def schluessel_entgegennehmen(projekt: str, plugin: str, referenz: str, art: str,
                              wert: str, ziel: str = "") -> dict:
    """Nimmt einen Credential-Wert entgegen: Supabase (Status
    `entgegengenommen`) -> Verifikation beim Anbieter (Aufgabe 5) -> bei
    Erfolg Uebergabe an OpenFang (Aufgabe 1) + Supabase-Kopie loeschen
    (Status `uebernommen`) -- bei Misserfolg Status `fehlgeschlagen` samt
    Statuscode, Supabase-Kopie bleibt zur Fehlersuche stehen.

    `wert` erscheint in KEINEM Feld der Rueckgabe und KEINER Fehlermeldung
    -- protokolliert/zurueckgegeben wird ausschliesslich `referenz`.
    `ziel` ist fuer `art in {oauth, connector}` PFLICHT (die Pruefadresse:
    MCP-Ressource bzw. connector_id) -- ohne sie wuerde pruefung.pruefe()
    stillschweigend gegen eine leere/falsche Adresse pruefen, darum wird
    hier VOR jedem Schreibzugriff abgelehnt (I3-Fix, Review Runde 1). Fuer
    `art == "bearer"` bleibt `ziel` leer (pruefung.py ignoriert sie dort).
    """
    if art in ("oauth", "connector") and not (ziel or "").strip():
        return {"ok": False, "fehler": f"ziel ist fuer art={art!r} Pflicht (I3)"}

    aufnahme = ablage.entgegennehmen(projekt, plugin, referenz, art, wert)
    if not aufnahme["ok"]:
        return aufnahme  # enthaelt nie `wert` (ablage.py scrubt es)

    ergebnis = pruefe(art, referenz, wert, ziel)
    if not ergebnis["gut"]:
        fehlschlag = ablage.fehlschlagen(referenz, str(ergebnis["status"]))
        return {"ok": False, "referenz": referenz, "status": ergebnis["status"],
                "fehler": "Verifikation beim Anbieter fehlgeschlagen",
                **({} if fehlschlag["ok"] else {"ablage_fehler": fehlschlag["fehler"]})}

    # I2-Fix (Review Runde 1): die urspruengliche Reihenfolge des Briefs --
    # `verifizieren()` VOR der OpenFang-Uebergabe. `uebernommen()` bleibt
    # aus `verifiziert` heraus aufrufbar, ein Fehlschlag hier ist also
    # KEIN Sackgassenzustand (die fruehere Umkehrung dieser Reihenfolge
    # beruhte auf der falschen Annahme, dass er einer waere).
    verifiziert = ablage.verifizieren(referenz)
    if not verifiziert["ok"]:
        return {"ok": False, "referenz": referenz,
                "fehler": "Verifikation bestanden, aber der Supabase-Uebergang "
                          "verifizieren() scheiterte: " + verifiziert["fehler"]}

    uebergabe = _openfang_uebernehmen(referenz, wert)
    if not uebergabe["ok"]:
        # Verifiziert, aber OpenFang hat die Uebergabe abgelehnt/ist nicht
        # erreichbar: das ist KEIN Credential-Fehler -- der Wert ist gut,
        # nur die Uebergabe scheiterte. Die Zeile bleibt bewusst auf
        # `verifiziert` stehen, die Vault-Kopie bleibt erhalten;
        # `fehlschlagen()` wird NICHT gerufen (0002 erlaubt sie nur aus
        # `entgegengenommen`, und diese Kante zu oeffnen ist keine
        # Entscheidung, die dieser Task trifft). `409` (reference_exists)
        # heisst insbesondere: NICHT automatisch ueberschreiben -- die
        # Referenz koennte bei OpenFang schon einen anderen, bewusst vom
        # Betreiber gesetzten Wert tragen; das braucht eine
        # Betreiber-Entscheidung, keine automatische Annahme.
        # Minor-Fix (Review Runde 2): 409 trug vorher dasselbe
        # `retryable: True` wie jeder andere Fehlschlag, obwohl der Text
        # daneben richtig sagte, dass ein Betreiber entscheiden muss -- ein
        # Aufrufer, der nur auf `retryable` reagiert (nicht den deutschen
        # Fliesstext liest), haette 409 endlos automatisch wiederholt, was
        # bei `overwrite=false` immer wieder 409 ergeben haette. Jetzt hat
        # 409 sein eigenes, maschinenlesbares Signal statt desselben Flags.
        if uebergabe.get("status") == 409:
            hinweis = (
                "OpenFang meldet 409 (reference_exists): die Referenz ist dort schon "
                "belegt -- moeglicherweise ein anderer, vom Betreiber gesetzter Wert. "
                "Das braucht eine Entscheidung des Betreibers, kein automatisches "
                "Ueberschreiben. Die Verifikation war gut, der Wert bleibt in Supabase "
                "(Status 'verifiziert') erhalten.")
            return {"ok": False, "referenz": referenz, "retryable": False,
                    "erfordert_betreiber_entscheidung": True, "fehler": hinweis}
        hinweis = (
            f"OpenFang-Uebergabe nicht erfolgreich (Status {uebergabe.get('status')}) -- "
            "die Verifikation selbst war gut, der Wert bleibt in Supabase (Status "
            "'verifiziert') erhalten. Retryable: sobald OpenFang wieder erreichbar "
            "ist, kann die Uebergabe fuer dieselbe Referenz erneut versucht werden.")
        return {"ok": False, "referenz": referenz, "retryable": True, "fehler": hinweis}

    freigabe = ablage.uebernommen(referenz)
    if not freigabe["ok"]:
        return {"ok": False, "referenz": referenz,
                "fehler": "OpenFang hat den Wert, Supabase-Uebergang uebernommen scheiterte: "
                          + freigabe["fehler"]}
    return {"ok": True, "referenz": referenz}


def plugin_installieren(projekt: str, plugin: str, komponenten: list | None = None) -> dict:
    """Installiert `plugin` (oder nur `komponenten` davon, componentDigest-
    Liste) in `projekt` -- Rowboats v1-Install-Route. `komponenten=None`
    oder leer bedeutet: alle Komponenten (Server-Default). Nur ein
    Erstinstall wird unterstuetzt (expectedRevision fest 0) -- ein Update
    einer bestehenden Installation braucht die tatsaechliche `revision` und
    ist nicht Teil dieses Werkzeugs; Rowboat antwortet in dem Fall
    `installation_conflict`, was hier als gewoehnlicher `ok: False`
    durchgereicht wird, nicht als Absturz.

    Rueckgabe: {"ok": True, "daten": <Empfangsschein>} -- der Schein
    (receiptId/status/...) enthaelt nie einen Credential-Wert.
    """
    if not projekt or not plugin:
        return {"ok": False, "fehler": "projekt und plugin sind Pflicht"}
    body = {
        "pluginName": plugin,
        "catalogDigest": _rowboat_catalog_digest(),
        "expectedRevision": 0,
        **({"componentDigests": komponenten} if komponenten else {}),
    }
    idem = uuid.uuid4().hex
    return _rowboat(f"/api/v1/projects/{projekt}/plugins", "POST", body,
                    extra_kopfzeilen={"Idempotency-Key": idem})


def plugin_werkzeug_binden(projekt: str, plugin: str, komponente: str) -> dict:
    """Bindet die Komponente `komponente` (componentDigest) von `plugin`
    als Tool in `projekt`. Der Tool-Name wird server-seitig abgeleitet --
    dieses Werkzeug nimmt ihn nie entgegen, nur entgegen.

    Rueckgabe: {"ok": True, "daten": {"toolName": ..., "added": bool}}
    """
    if not projekt or not plugin or not komponente:
        return {"ok": False, "fehler": "projekt, plugin und komponente sind Pflicht"}
    return _rowboat(f"/api/v1/projects/{projekt}/plugins/{plugin}/tools",
                    "POST", {"componentDigest": komponente})
