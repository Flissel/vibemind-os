"""plugin-setup-Werkzeuge -- der eigenstaendige Space aus Aufgabe 6.

Bindet die Bausteine der Aufgaben 1-5 zu vier MCP-Werkzeugen zusammen:

  plugin_bedarf(projekt, plugin)             -- was braucht das Plugin, und
                                              aus welchen Komponenten besteht es?
  schluessel_entgegennehmen(projekt, plugin, referenz, art, wert, ziel="")
                                              -- Supabase -> pruefen -> OpenFang
  plugin_installieren(projekt, plugin, komponenten=None) -- Rowboat-Install;
                                              ohne Angabe: alle ZUGELASSENEN
  plugin_werkzeug_binden(projekt, plugin, komponente)    -- Rowboat-Tool-Bindung

WOHIN EIN WERT REIST (C3, Schluss-Review): `ziel` ist agentenverfasster
Freitext -- `plugin_bedarf` liefert es nicht. Fuer `art="oauth"` wird daraus
in pruefung.py die ZIELADRESSE eines Aufrufs, der den Wert im
`Authorization: Bearer`-Kopf traegt. `schluessel_entgegennehmen` prueft es
darum VOR jedem Schreibzugriff: https (sonst reist das Geheimnis im
Klartext), kein Benutzer/Query/Fragment (wie Rowboats `secureUrl` auf genau
diesem Wert), und die Adresse muss sich nach der GETEILTEN Namensregel
(`deriveOAuthBearerReference`) auf `referenz` zurueckrechnen lassen. Damit
ist "ein Wert geht nur dorthin, wo sein Referenzname es sagt" strukturell
statt konventionell. Siehe `_ziel_pruefen`/`_oauth_bearer_referenz`.

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
import re
import time
import uuid
import urllib.parse
import urllib.request

from pruefung import pruefe
import ablage

FEHLER_MAXLAENGE = 300

# --- Die Namensform, in der OpenFang ein Credential kennt --------------------
# Woertlich aus spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/
# providers/credential-naming.ts (`OPENFANG_REFERENCE`, `CONNECTOR_NAME_INVALID`
# und der Rumpf von `deriveOAuthBearerReference`) gespiegelt -- KEINE zweite
# Regel, s. `_oauth_bearer_referenz`.
_OPENFANG_REFERENZ_MUSTER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_NICHT_ALNUM = re.compile(r"[^A-Z0-9]+")

# Der Pfadteil einer oauth-Ressource darf fuer die Spiegelung NUR aus
# unreservierten URL-Zeichen bestehen (RFC 3986 2.3, plus "/" als Trenner).
# Grund steht bei `_oauth_bearer_referenz`: WHATWGs `new URL()` normalisiert
# Prozentkodierung und Punkt-Segmente, Pythons `urlsplit` nicht -- fuer alles,
# was hier durchkommt, sind beide nachweislich gleich; alles andere wird
# abgelehnt statt geraten.
_PFAD_UNRESERVIERT = re.compile(r"^(?:/[A-Za-z0-9._~-]*)*$")

# Die Form einer connector_id -- ebenfalls gespiegelt, nicht erfunden:
# `AppDeclarationSchema` (packages/openai-plugin-runtime/src/schema/
# component-schemas.ts:11) erlaubt `^(?:connector|asdk_app|templated_apps)_
# [a-f0-9]+$`, und `connector-bridge-provider.ts:128` laesst von diesen drei
# NUR `connector_` zu (alles andere: provider_unavailable:not_a_connector).
_CONNECTOR_ID_MUSTER = re.compile(r"^connector_[a-f0-9]+$")

# Wie oft `ablage.uebernommen()` wiederholt wird, wenn OpenFang den Wert
# bereits hat (s. I5-Fix in `schluessel_entgegennehmen`). Bewusst klein und
# endlich: der Zweck ist, eine kurze Supabase-Stoerung zu ueberbruecken, nicht
# eine Datenbank wachzuklopfen.
_UEBERNOMMEN_VERSUCHE = 3
_UEBERNOMMEN_PAUSE_SEKUNDEN = 0.5

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


def _oauth_bearer_referenz(ressource_url: str) -> str | None:
    """Die PYTHON-SPIEGELUNG von `deriveOAuthBearerReference`
    (spaces/rowboat/rowboat/packages/openai-plugin-runtime/src/providers/
    credential-naming.ts:30-43), Zeichen fuer Zeichen derselbe Rumpf:

        stem = (host + pathname).toUpperCase()
                 .replace(/[^A-Z0-9]+/gu, "_").replace(/^_+|_+$/gu, "")
        -> "OAUTH_BEARER_" + stem, sofern er OpenFangs Referenzmuster genuegt.

    Gibt `None` zurueck, wo die TS-Funktion `undefined` zurueckgibt: keine
    URL, oder ein Name, der OpenFangs Grenze sprengt -- und zusaetzlich
    ueberall dort, wo Pythons `urlsplit` und WHATWGs `new URL()`
    auseinanderlaufen KOENNTEN, statt dort zu raten:

      - Prozentkodierung und Punkt-Segmente: `new URL()` normalisiert beides
        ("%41" -> "A", "/a/../b" -> "/b"), `urlsplit` nicht. Ein Pfad, der
        weder "%" noch ein "."/".."-Segment enthaelt, ist unter beiden
        identisch -- genau das erzwingt `_PFAD_UNRESERVIERT` plus die
        Segmentpruefung unten. Alles andere: `None`, nie eine zweite Regel.
      - Standard-Port: `URL.host` laesst den Schema-Standardport weg. Diese
        Funktion wird nur fuer `https` aufgerufen (s. `_ziel_pruefen`), also
        faellt genau `:443` weg -- was `urlsplit().port` hier nachbildet.

    Keine Kopie der Regel, sondern eine Spiegelung EINER Regel: dieselbe
    Bindung, die `tests/test_werkzeuge.py` gegen credential-naming.ts haelt,
    haelt auch diese Funktion.
    """
    try:
        teile = urllib.parse.urlsplit(ressource_url)
        port = teile.port
    except ValueError:
        return None
    if teile.scheme == "" or teile.hostname in (None, ""):
        return None
    pfad = teile.path or "/"
    if not _PFAD_UNRESERVIERT.match(pfad):
        return None
    if any(seg in (".", "..") for seg in pfad.split("/")):
        return None
    host = teile.hostname
    if port is not None and not (teile.scheme == "https" and port == 443):
        host = f"{host}:{port}"
    stamm = _NICHT_ALNUM.sub("_", f"{host}{pfad}".upper()).strip("_")
    abgeleitet = f"OAUTH_BEARER_{stamm}"
    return abgeleitet if _OPENFANG_REFERENZ_MUSTER.match(abgeleitet) else None


def _ziel_pruefen(art: str, referenz: str, ziel: str) -> str | None:
    """C3-FIX (Schluss-Review der ganzen Linie, 2026-09-11). Gibt einen
    Fehlertext zurueck, wenn `ziel` nicht akzeptabel ist -- sonst `None`.

    Der Befund: `ziel` war bis hierher nur auf "nicht leer" geprueft, und
    `pruefung._oauth_aufruf` macht daraus die ZIELADRESSE eines Aufrufs, der
    den Credential-Wert im `Authorization: Bearer`-Kopf traegt. `ziel` kommt
    aber nicht aus `plugin_bedarf` (das liefert es gar nicht) -- ein LLM
    verfasst diesen String. Damit entschied ein LLM-Text, WOHIN ein Geheimnis
    reist, und nichts verlangte TLS. Dieselbe Fehlerklasse, die der C2-Fix
    fuer `art` geschlossen hat, auf dem Geschwisterfeld offen gelassen --
    und schlimmer: beliebiges Ziel UND Klartext moeglich.

    Zwei Formen, weil `ziel` zwei verschiedene Dinge bezeichnet:

      art="oauth"     -- `ziel` IST die Adresse (pruefung.py POSTet MCP
                         `initialize` dorthin). Also die Bedingungen, die
                         Rowboat auf genau diesen Wert legt (`secureUrl`,
                         components/mcp-normalizer.ts:37-53): https, kein
                         Benutzer/Passwort, kein Query, kein Fragment. UND
                         zusaetzlich: die Adresse muss sich nach der
                         GETEILTEN Regel auf `referenz` zurueckrechnen
                         lassen. Damit ist "der Wert geht dorthin, wo sein
                         Referenzname es sagt" strukturell, nicht nur
                         Konvention: ein erfundenes Ziel traegt nicht mehr
                         den Namen, unter dem der Wert entgegengenommen wird.
      art="connector" -- `ziel` ist KEINE Adresse, sondern die `connector_id`
                         im Rumpf eines Aufrufs an die fest verdrahtete
                         `https://api.openai.com/v1/responses`
                         (pruefung._connector_aufruf). Hier https zu
                         verlangen waere falsch (es ist keine URL) und
                         wuerde den Pfad schlicht zerstoeren; geprueft wird
                         darum die Form, die Rowboat selbst verlangt
                         (`_CONNECTOR_ID_MUSTER`). Das Ziel des Aufrufs ist
                         hier ohnehin nicht agentenwaehlbar.
      art="bearer"    -- `ziel` wird von pruefung.py ignoriert (die Adresse
                         ist fest `https://api.github.com/user`); ein
                         mitgegebenes `ziel` waere irrefuehrend, also wird
                         es abgelehnt statt stillschweigend verworfen.
    """
    ziel = (ziel or "").strip()
    if art in ("oauth", "connector") and not ziel:
        return f"ziel ist fuer art={art!r} Pflicht (I3)"
    if art == "bearer":
        if ziel:
            return ("ziel ist fuer art='bearer' nicht erlaubt -- die Pruefadresse "
                    "ist dort fest (api.github.com), ein ziel waere wirkungslos "
                    "und irrefuehrend")
        return None
    if art == "connector":
        if not _CONNECTOR_ID_MUSTER.match(ziel):
            return ("ziel ist fuer art='connector' keine gueltige connector_id "
                    "(erwartet ^connector_[a-f0-9]+$, s. AppDeclarationSchema + "
                    "connector-bridge-provider.ts) -- es ist KEINE URL, sondern "
                    "die connector_id im Rumpf des Aufrufs an api.openai.com")
        return None
    if art == "oauth":
        try:
            teile = urllib.parse.urlsplit(ziel)
            teile.port  # wirft ValueError bei kaputtem Port
        except ValueError:
            return "ziel ist keine gueltige URL"
        if teile.scheme != "https":
            return ("ziel muss https sein -- der Credential-Wert reist in dessen "
                    "Authorization-Kopf, und ohne TLS reist er im Klartext "
                    f"(bekommen: {teile.scheme or '<kein Schema>'!r})")
        if teile.username or teile.password:
            return "ziel darf keinen Benutzer/kein Passwort enthalten (secureUrl)"
        if teile.query or teile.fragment:
            return "ziel darf keinen Query und kein Fragment enthalten (secureUrl)"
        abgeleitet = _oauth_bearer_referenz(ziel)
        if abgeleitet is None:
            return ("aus ziel laesst sich kein OpenFang-Referenzname ableiten "
                    "(deriveOAuthBearerReference) -- fail closed statt raten")
        if abgeleitet != referenz:
            return (f"ziel gehoert nicht zu referenz {referenz!r}: die geteilte "
                    f"Namensregel leitet aus ziel {abgeleitet!r} ab. Ein Wert "
                    "geht nur dorthin, wo sein Referenzname es sagt.")
        return None
    # Jede andere `art` (insbesondere "unbekannt") faellt weiter unten an
    # ablage.entgegennehmen's eigener Pruefung durch -- hier nichts annehmen.
    return None


def plugin_bedarf(projekt: str, plugin: str) -> dict:
    """Welche Credentials braucht `plugin` fuer `projekt`, und aus welchen
    Komponenten besteht es? Liest Rowboats Preview -- nur lesend, legt
    nichts an.

    Rueckgabe:
      {"ok": True,
       "daten":       [{"name", "art", "quelle", "vorhanden"}],
       "komponenten": [{"digest", "name", "kind", "zugelassen"}]}

    `quelle` benennt, woher die Angabe stammt (hier immer "rowboat-preview",
    da Rowboat bislang die einzige Quelle ist, die Aufgabe 6 kennt); die
    dritte Pruefform "connector" bringt zusaetzlich OPENAI_API_KEY mit (s.
    plugin-service.shared.ts) -- das ist normal, kein Fehler. `art` kann
    `"unbekannt"` sein (C2-Fix: NIE automatisch `"bearer"` geraten, s.
    `_art_aus_referenzname`) -- `schluessel_entgegennehmen` lehnt eine
    solche `art` ab; ein Mensch muss sie dann bewusst waehlen.

    C4-FIX (Schluss-Review, 2026-09-11): `komponenten` ist NEU. Die Preview
    traegt die `components`-Liste samt `componentDigest` und `admission`
    laengst (preview-plugin-installation.use-case.ts:37,
    `componentDtosFrom`) -- dieses Werkzeug hat sie bis hierher weggeworfen
    und NUR `credentialSlots` gelesen. Folge: AGENTS.md nannte
    `plugin_bedarf` als Quelle fuer `componentDigest`s, und keine der vier
    Werkzeug-Rueckgaben lieferte je einen (der Install-Empfangsschein
    besteht aus `{type, receiptId, projectId, pluginName, status,
    redactions}`). `plugin_werkzeug_binden` war damit vom Agenten aus nicht
    erreichbar, ohne einen Digest zu erfinden.

    `zugelassen` ist `admission.status == "admitted"` -- genau das Kriterium,
    an dem `assertSelectionAdmitted` (plugin-service.shared.ts:196-206) eine
    Installation ablehnt.

    VORSICHT MIT `vorhanden` (C5, gemessen 11.09.2026): das Feld ist HEUTE
    strukturell immer `False`. `configured` in der Preview kommt aus
    `listCredentialSlots(installation.id)`; der einzige Schreiber im
    Produktivpfad, `slotsFrom` (plugin-service.shared.ts:234), liefert
    unbedingt `Object.freeze([])`, und `putCredentialSlot` hat keinen
    Produktiv-Aufrufer. `vorhanden` ist damit eine Konstante, kein Signal --
    es taugt NICHT als Kriterium fuer "was fehlt noch". Das Feld bleibt
    stehen (es ist die ehrliche Abbildung dessen, was Rowboat sendet), aber
    weder dieses Werkzeug noch AGENTS.md steuern danach. Die eigentliche
    Reparatur liegt in Rowboats Installationspfad und ist eine eigene,
    groessere Aenderung.
    """
    if not projekt or not plugin:
        return {"ok": False, "fehler": "projekt und plugin sind Pflicht"}
    digest = _rowboat_catalog_digest()
    antwort = _rowboat(
        f"/api/v1/projects/{projekt}/plugins/{plugin}?catalogDigest={digest}")
    if not antwort["ok"]:
        return antwort
    vorschau = antwort["daten"] or {}
    slots = vorschau.get("credentialSlots") or []
    bedarf = [
        {
            "name": slot.get("name", ""),
            "art": _art_aus_referenzname(str(slot.get("name", ""))),
            "quelle": "rowboat-preview",
            "vorhanden": bool(slot.get("configured", False)),
        }
        for slot in slots if isinstance(slot, dict)
    ]
    return {"ok": True, "daten": bedarf,
            "komponenten": _komponenten_aus_vorschau(vorschau)}


def _komponenten_aus_vorschau(vorschau: dict) -> list:
    """Die `components`-Liste einer Rowboat-Preview in die Form, die dieser
    Space benutzt. Form der Quelle: `{componentDigest, name, kind,
    admission: {status, reason?, policyVersion}, availability, status}` --
    s. `Component`/`serializedComponents` in
    app/api/v1/projects/[projectId]/plugins/_responses.ts."""
    roh = (vorschau or {}).get("components") or []
    komponenten = []
    for eintrag in roh:
        if not isinstance(eintrag, dict):
            continue
        zulassung = eintrag.get("admission")
        status = zulassung.get("status") if isinstance(zulassung, dict) else None
        komponenten.append({
            "digest": str(eintrag.get("componentDigest", "")),
            "name": str(eintrag.get("name", "")),
            "kind": str(eintrag.get("kind", "")),
            "zugelassen": status == "admitted",
        })
    return komponenten


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
    Seit dem C3-Fix (Schluss-Review) wird `ziel` nicht mehr nur auf "nicht
    leer" geprueft, sondern vollstaendig -- https, secureUrl-Bedingungen und
    Rueckrechnung auf `referenz` fuer oauth, connector_id-Form fuer
    connector. Begruendung und Regelquellen: `_ziel_pruefen`.
    """
    ziel_fehler = _ziel_pruefen(art, referenz, ziel)
    if ziel_fehler is not None:
        return {"ok": False, "fehler": ziel_fehler}

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

    # I5-FIX (Schluss-Review der ganzen Linie, 2026-09-11): ab hier hat
    # OpenFang den Wert. Scheitert jetzt noch der Supabase-Uebergang, liegt
    # derselbe Wert in ZWEI Tresoren -- genau die "zwei Widerrufsflaechen",
    # die D2 verbietet: ein Widerruf in OpenFang laesst die Supabase-Kopie
    # stehen. Bis hierher meldete das Werkzeug dafuer nur `ok: False` mit
    # Fliesstext -- kein Wiederholen, kein maschinenlesbares Signal, und
    # AGENTS.md beschrieb den Fall gar nicht, also meldete der Agent einen
    # gewoehnlichen Fehlschlag. Der naheliegende Retry des Betreibers
    # (`schluessel_entgegennehmen` nochmal) stirbt dann an der
    # UNIQUE-Constraint des ERSTEN Schritts.
    #
    # Zwei Teile: (1) ein begrenzter Retry genau dieses einen Uebergangs --
    # er ist idempotent-genug (`plugin_setup.uebernommen()` waecht selbst
    # ueber den Ausgangszustand) und ueberbrueckt eine kurze Stoerung;
    # (2) scheitert er endgueltig, traegt die Antwort das eigene Kennzeichen
    # `zwei_verwahrstellen: True` samt der EINEN Abhilfe, die hilft --
    # nicht `retryable`, denn `schluessel_entgegennehmen` selbst zu
    # wiederholen ist genau das, was hier NICHT hilft.
    letzter_fehler = ""
    for versuch in range(_UEBERNOMMEN_VERSUCHE):
        freigabe = ablage.uebernommen(referenz)
        if freigabe["ok"]:
            return {"ok": True, "referenz": referenz}
        letzter_fehler = freigabe.get("fehler", "")
        if versuch < _UEBERNOMMEN_VERSUCHE - 1:
            time.sleep(_UEBERNOMMEN_PAUSE_SEKUNDEN)
    return {
        "ok": False,
        "referenz": referenz,
        "zwei_verwahrstellen": True,
        "retryable": False,
        "fehler": (
            "ZWEI VERWAHRSTELLEN: OpenFang hat den Wert bereits uebernommen, aber "
            f"der Supabase-Uebergang uebernommen() ist nach {_UEBERNOMMEN_VERSUCHE} "
            "Versuchen gescheitert -- die verschluesselte Supabase-Kopie steht also "
            "NOCH, und derselbe Wert liegt in zwei Tresoren (D2 verbietet genau das: "
            "zwei Widerrufsflaechen). Abhilfe, genau eine: `ablage.uebernommen("
            f"{referenz!r})` erneut ausfuehren, sobald Supabase wieder erreichbar ist "
            "-- das loescht die Kopie. `schluessel_entgegennehmen` NICHT wiederholen: "
            "der erste Schritt kollidiert an der UNIQUE-Constraint auf referenz_name. "
            "Letzter Fehler: " + letzter_fehler),
    }


def plugin_installieren(projekt: str, plugin: str, komponenten: list | None = None) -> dict:
    """Installiert `plugin` in `projekt` -- Rowboats v1-Install-Route. Nur
    ein Erstinstall wird unterstuetzt (expectedRevision fest 0) -- ein
    Update einer bestehenden Installation braucht die tatsaechliche
    `revision` und ist nicht Teil dieses Werkzeugs; Rowboat antwortet in dem
    Fall `installation_conflict`, was hier als gewoehnlicher `ok: False`
    durchgereicht wird, nicht als Absturz.

    C4-FIX (Schluss-Review, 2026-09-11) -- `komponenten=None` heisst jetzt
    ALLE ZUGELASSENEN, nicht mehr "alle":

    Vorher liess ein weggelassenes `komponenten` den Parameter aus dem Rumpf
    fallen, und der Server setzt dann `entryComponentDigests(entry)` =
    JEDE Komponente (install-plugin.use-case.ts:45). Direkt danach lehnt
    `assertSelectionAdmitted` (plugin-service.shared.ts:203) mit
    `component_not_admitted` ab, sobald auch nur EINE davon nicht zugelassen
    ist. Am gepinnten Katalog gemessen (11.09.2026, 180 Eintraege): 52
    Eintraege scheitern an genau dieser Regel, darunter 3 der 4 Plugins mit
    einer zugelassenen HTTP-MCP-Komponente -- also drei der vier, um
    derentwillen dieser Agent ueberhaupt existiert (github: 8 Komponenten,
    1 zugelassen; cloudflare 14/1; notion 10/1; nur linear 5/5). Der in
    AGENTS.md beschriebene Weg (`plugin_installieren(projekt, plugin)`)
    konnte fuer sie also gar nicht gelingen.

    Jetzt: ohne `komponenten` holt dieses Werkzeug zuerst die Preview und
    waehlt daraus genau die zugelassenen Digests. Sonderfaelle, beide
    fail-soft statt halbgar:
      - Das Plugin hat ueberhaupt keine Komponenten -> `componentDigests`
        bleibt weg (eine LEERE Liste waere `request_invalid`,
        plugin-component-selection.ts:17).
      - Das Plugin hat Komponenten, aber keine zugelassene -> ehrlicher
        Fehlschlag mit Begruendung, statt eine Ablehnung des Servers
        abzuwarten, die dann `component_not_admitted` heisst und aussieht,
        als haette der Aufrufer etwas falsch gewaehlt.
    Eine ausdrueckliche `komponenten`-Liste wird unveraendert durchgereicht
    -- ein Betreiber, der bewusst waehlt, wird nicht ueberstimmt.

    Rueckgabe: {"ok": True, "daten": <Empfangsschein>} -- der Schein
    (receiptId/status/...) enthaelt nie einen Credential-Wert und auch
    KEINEN componentDigest (s. `installReceipt`); Digests kommen
    ausschliesslich aus `plugin_bedarf`.
    """
    if not projekt or not plugin:
        return {"ok": False, "fehler": "projekt und plugin sind Pflicht"}
    if not komponenten:
        bedarf = plugin_bedarf(projekt, plugin)
        if not bedarf["ok"]:
            return {"ok": False, "fehler":
                    "Komponentenauswahl nicht moeglich, die Preview scheiterte: "
                    + str(bedarf.get("fehler", ""))}
        alle = bedarf.get("komponenten") or []
        zugelassen = [k["digest"] for k in alle if k.get("zugelassen") and k.get("digest")]
        if alle and not zugelassen:
            return {"ok": False, "fehler":
                    f"{plugin!r} hat {len(alle)} Komponente(n), aber keine einzige "
                    "zugelassene (admission != 'admitted') -- eine Installation "
                    "waere hier in jedem Fall abgelehnt. Das ist eine Entscheidung "
                    "der Katalog-Richtlinie, kein Fehler des Aufrufers."}
        komponenten = zugelassen
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
