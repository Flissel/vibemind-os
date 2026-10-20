"""Pult-Router der Marketing-API (sales-claw Spec 2026-09-29-marketing-pult-
design.md §3.4). Einziger Gespraechspartner: die Sales-Oberflaeche, mit Header
X-Pult-Key. Ohne konfigurierten Schluessel ist der Router zu (503), nie offen.
Regeln (Fassungen unveraenderlich, ein Urteil, Gestalt-Pruefung) stehen in den
DB-Funktionen aus 050/051 -- hier nur Formen und Weitergabe.

Abweichung vom Brief (task-3-brief.md, siehe task-3-report.md): 051 aenderte
marketing.pult_entscheiden auf die 5-arg-Form (p_inhalt, p_fassung, p_urteil,
p_von, p_grund) -- die frueher geplante 4-arg-Form gibt es nicht mehr. Die
Fassung, die der Mensch tatsaechlich gesehen hat, kommt jetzt als Pflichtfeld
"fassung" im Entscheiden-Body und wird VOR jedem DB-Zugriff als int >= 1
geprueft (422 ohne SQL bei fehlend/ungueltig) -- die DB-Funktion selbst weist
danach noch eine inzwischen veraltete Fassung zurueck (422 mit ihrem
deutschen Grund).

Fix-Runde 1 (task-3-report.md "Fix round 1", Controller-Weisung, bindend):
jeder DB-Zugriff dieses Routers laeuft ueber _lesen/_lesen_einer/_schreiben
und ist DAMIT IMMER streng=True -- ein SQL-Fehler wirft, statt still eine
leere Liste/kein Ergebnis zu liefern (sonst koennte z.B. eine fehlgeschlagene
Gestalt-Pruefung als "keine Beanstandung" durchgehen). Jede dieser drei
Huellen baut die SQL-Zeichenkette ERST INNERHALB des try (die uebergebene
Closure), damit ein ValueError aus _sql_literal (NUL-Byte in einem rohen
Textfeld wie layout/urteil/von/grund) denselben 422-Pfad nimmt wie jeder
andere Eingabefehler, statt unbehandelt als 500 durchzuschlagen. Ein
DB-Fehler ohne "ERROR:"-Marker (SSH/Container/Verbindung abgebrochen) wird
NIE mit seinem Rohtext gezeigt (der koennte Host-/Containernamen enthalten)
-- er wird 503 "Marketing-Datenbank nicht erreichbar". Lesende Endpunkte
(uebersicht, inhalte, inhalt, vorschau, layouts, Mandant-Nachschlag) liefern
bei einem DB-Fehler ebenfalls 503, nie eine leere Liste oder ein
untergeschobenes 404 -- eine leere/keine Zeile OHNE Fehler bleibt weiterhin
der legitime Weg zu 404 (unbekannte ID/Fassung).

Der schaerfste Einzelfund: /layouts/vorschau prueft eine vom Aufrufer
mitgeschickte, NOCH NICHT gespeicherte Gestalt gegen marketing.
pult_gestalt_fehler, BEVOR sie gerendert wird (Vorschau, nicht gespeichert).
Lief diese Pruefung lax (streng=False), konnte ein SQL-Fehler bei der
Pruefung selbst (z.B. ein "\u0000" in einer Farbe -- Postgres lehnt das
Unicode-Escape beim ::jsonb-Cast ab) still als "keine Zeile" durchgehen,
_db.query_one() liefert dann None, `if fehler and fehler.get(...)` ist falsch,
und die UNGEPRUEFTE Gestalt landet direkt im gerenderten HTML (Farben wandern
roh in style-Attribute). _pruefen_gestalt() unten ist deshalb fail-closed:
jeder Fehler ODER jede fehlende Zeile bei der Pruefung selbst -> 503, niemals
rendern."""
from __future__ import annotations

import hmac
import json
import os
import re
import uuid as _uuid
from typing import Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from spaces.marketing.claw import (bildplaetze, bloecke_mjml, markenprofil, pdf_druck, pult_render, schriften, seite,
                                   seite_html, seite_vorlagen, vorlagen_grafik, vorlagen_marke)
from spaces.marketing.sync import _db

router = APIRouter(prefix="/api/pult")
_ARTEN = ("newsletter", "post", "material")
_STATUS = ("entwurf", "eingereicht", "freigegeben", "abgelehnt")
_FORMATE = ("mail", "handy", "pdf")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
_VORLAGE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_BILD_BASIS = re.compile(r'^https://[^\s"\'<>]+/$')
_URHEBER = ("betreiber", "agent")
_NICHT_ERREICHBAR = "Marketing-Datenbank nicht erreichbar"
_UNGUELTIGE_EINGABE = "Ungueltige Eingabe (unzulaessiges Zeichen)"
COLLAGE_IST = "Das ist ein Collage-Newsletter"
COLLAGE_IST_NICHT = "Das ist kein Collage-Newsletter"
NUR_COLLAGEN = "Neue Newsletter entstehen als Collage – bitte eine Collage-Vorlage wählen"
SEITEN_VORLAGE_NICHT_ANWENDBAR = ("Collage-Vorlagen lassen sich nicht auf einen Block-Newsletter anwenden – "
                                  "bitte „Neu aus Vorlage“ nehmen")
BLOCK_VORLAGE_OHNE_SEITE = "Block-Vorlagen haben keine Seitenansicht – bitte format=mail oder handy"
VORLAGEN_NAME_BELEGT = ("Den Namen trägt schon eine Vorlage im anderen Format (Block oder Collage) – "
                        "bitte einen anderen Namen wählen")
_VORLAGEN_FORMAT_CHECKS = ("newsletter_vorlagen_format_check", "newsletter_vorlagen_fassungen_format_check")


def _schluessel(x_pult_key: str | None) -> None:
    erwartet = os.environ.get("MARKETING_PULT_KEY", "").strip()
    if not erwartet:
        raise HTTPException(503, "misconfigured: MARKETING_PULT_KEY fehlt")
    kandidat = (x_pult_key or "").strip()
    # .encode() auf beiden Seiten: hmac.compare_digest wirft TypeError bei
    # zwei str mit Nicht-ASCII-Zeichen -- ein falscher Header waere dann ein
    # 500 statt eines 401. Byte-Vergleich funktioniert fuer jede Eingabe.
    if not kandidat or not hmac.compare_digest(
            kandidat.encode("utf-8", errors="replace"),
            erwartet.encode("utf-8", errors="replace")):
        raise HTTPException(401, "Pult-Schluessel fehlt oder falsch")


def _uuid_oder_404(wert: str) -> str:
    try:
        return str(_uuid.UUID(wert))
    except ValueError:
        raise HTTPException(404, "Unbekannter Inhalt")


def _auswahl(wert: str | None, erlaubt: tuple, name: str) -> str | None:
    if wert is None or wert == "":
        return None
    if wert not in erlaubt:
        raise HTTPException(422, f"{name} muss eines von {', '.join(erlaubt)} sein")
    return wert


def _mandant(wert: str | None) -> str:
    wert = wert or "vibemind"
    if not isinstance(wert, str) or not _NAME.match(wert):
        raise HTTPException(422, "Unbekannter Mandant")
    return wert


def _fassung_pflicht(wert) -> int:
    if isinstance(wert, bool) or not isinstance(wert, int) or wert < 1:
        raise HTTPException(422, "fassung fehlt oder muss eine ganze Zahl >= 1 sein")
    return wert


def _db_grund(fehler: Exception) -> str | None:
    """Deutscher Grund aus einer DB-ABLEHNUNG (psql-Ausgabe enthaelt
    "ERROR:  <plpgsql RAISE EXCEPTION>"), oder None, wenn der Fehler keine
    Ablehnung ist -- Verbindungsabbruch, SSH- oder Container-Fehler. Der
    Rohtext eines Nicht-Ablehnungsfehlers wird NIE nach aussen gegeben (kann
    Host-/Containernamen enthalten); der Aufrufer antwortet dann 503."""
    treffer = re.search(r"ERROR:\s*(.+)", str(fehler))
    if not treffer:
        return None
    zeile = treffer.group(1).strip().splitlines()[0]
    # Stufe 5: eine Block-Vorlage unter dem Namen einer Collage-Vorlage (oder umgekehrt) scheitert in der DB an
    # der Format-Regel (082) - statt des rohen check_violation-Texts ein deutscher Grund
    if any(c in zeile for c in _VORLAGEN_FORMAT_CHECKS):
        return VORLAGEN_NAME_BELEGT
    return zeile[:300]


def _lesen(bauen: Callable[[], str]) -> list:
    """Liste lesen, IMMER streng. SQL wird INNERHALB des try gebaut, damit
    ein ValueError aus _sql_literal (NUL-Byte) denselben 422-Pfad nimmt wie
    jeder andere Eingabefehler. Jeder DB-Fehler -> 503, NIE eine leere Liste
    (die saehe sonst wie "keine Treffer" statt "DB nicht erreichbar" aus)."""
    try:
        sql = bauen()
        return _db.query_via_docker(sql, streng=True)
    except ValueError:
        raise HTTPException(422, _UNGUELTIGE_EINGABE)
    except Exception:
        raise HTTPException(503, _NICHT_ERREICHBAR)


def _lesen_einer(bauen: Callable[[], str]) -> dict | None:
    """Wie _lesen, aber eine Zeile. None (keine Zeile, KEIN Fehler) bleibt
    der legitime Weg zu 404 beim Aufrufer -- nur ein tatsaechlicher
    DB-Fehler wird 503."""
    try:
        sql = bauen()
        return _db.query_one(sql, streng=True)
    except ValueError:
        raise HTTPException(422, _UNGUELTIGE_EINGABE)
    except Exception:
        raise HTTPException(503, _NICHT_ERREICHBAR)


def _schreiben(bauen: Callable[[], str]) -> dict:
    """Ruft eine schreibende DB-Funktion aus 050/051 auf (setzt die
    eigentlichen Regeln durch). SQL wird INNERHALB des try gebaut (siehe
    _lesen). Eine ERROR:-Ablehnung -> 422 mit ihrem deutschen Grund; jeder
    andere Fehler (Verbindung/SSH/Container, oder ein ValueError aus
    _sql_literal) ist fail-closed -- nie der Rohtext, nie stillschweigend
    durchgelassen."""
    try:
        sql = bauen()
        zeile = _db.query_one(sql, streng=True)
    except ValueError:
        raise HTTPException(422, _UNGUELTIGE_EINGABE)
    except Exception as e:
        grund = _db_grund(e)
        if grund is None:
            raise HTTPException(503, _NICHT_ERREICHBAR)
        raise HTTPException(422, grund)
    if zeile is None:
        raise HTTPException(503, _NICHT_ERREICHBAR)
    return zeile


def _pruefen_gestalt(gestalt: dict) -> str | None:
    """marketing.pult_gestalt_fehler streng aufrufen. FAIL-CLOSED: schlaegt
    die Pruefung selbst fehl (DB-Fehler -- z.B. lehnt Postgres ein "\\u0000"
    in einer Farbe beim ::jsonb-Cast ab) oder liefert sie keine Zeile, ist
    das IMMER 503 -- die Gestalt gilt dann als nicht geprueft und wird nie
    gerendert. Nur eine tatsaechlich durchgelaufene Pruefung mit Ergebnis
    (auch "kein Fehler") darf weiter zum Rendern fuehren."""
    try:
        zeile = _db.query_one(
            f"SELECT marketing.pult_gestalt_fehler("
            f"{lit(json.dumps(gestalt, ensure_ascii=False))}::jsonb) AS fehler",
            streng=True)
    except ValueError:
        raise HTTPException(422, _UNGUELTIGE_EINGABE)
    except Exception:
        raise HTTPException(503, _NICHT_ERREICHBAR)
    if zeile is None:
        raise HTTPException(503, _NICHT_ERREICHBAR)
    return zeile.get("fehler")


lit = _db._sql_literal


def neuestes_format_sql(i: str) -> str:
    """Format der neuesten Fassung als SQL-Ausdruck (NULL ohne Fassung) - Grundlage der Formatsperre."""
    return (f"(SELECT f.format FROM marketing.inhalt_fassungen f WHERE f.inhalt = {lit(i)}::uuid "
            "ORDER BY f.fassung DESC LIMIT 1)")


def mit_formatsperre(i: str, aufruf: Callable[[], str], *, collage: bool = False, grund: str = COLLAGE_IST,
                     spalte: str = "fassung") -> dict:
    """Schreibenden DB-Aufruf nur, wenn das Format der neuesten Fassung passt - Pruefung und Aufruf in
    DERSELBEN Abfrage (CASE wertet den ELSE-Zweig mit der volatilen Funktion nur bei Bedarf aus).
    collage=False (Bloecke, Felder, Einreichen, Vorlage): gesperrt, wenn die neueste Fassung 'seite' ist.
    collage=True (Seite speichern): gesperrt, wenn es eine neueste Fassung gibt und sie nicht 'seite' ist;
    ohne Fassung ist der Vergleich NULL und der ELSE-Zweig laeuft. Gesperrt -> 409 mit grund, sonst die Zeile
    mit dem Ergebnis unter `spalte`. aufruf baut das SQL erst im try von _schreiben (NUL-Byte -> 422)."""
    fmt = neuestes_format_sql(i)
    vergleich = f"{fmt} <> 'seite'" if collage else f"{fmt} = 'seite'"
    zeile = _schreiben(lambda:
        f"SELECT CASE WHEN {vergleich} THEN NULL ELSE {aufruf()} END AS {spalte}, "
        f"coalesce({vergleich}, false) AS gesperrt")
    if zeile.get("gesperrt"):
        raise HTTPException(409, grund)
    return zeile


@router.get("/uebersicht")
def uebersicht(mandant: str | None = None, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant(mandant)
    mandanten = _lesen(lambda:
        "SELECT id, name, aktiv FROM marketing.mandanten ORDER BY aktiv DESC, name")
    zahlen = _lesen(lambda:
        "SELECT CASE WHEN i.status = 'entwurf' AND EXISTS (SELECT 1 FROM marketing.rueckmeldungen r "
        "  WHERE r.inhalt = i.id AND r.erledigt_am IS NULL) THEN 'zurueckgegeben' ELSE i.status END AS status, "
        f"count(*)::int AS n FROM marketing.inhalte i WHERE i.mandant = {lit(m)} GROUP BY 1")
    # zurueckgegeben = Entwurf mit offener Rueckmeldung; "entwurf" zaehlt diese NICHT mit.
    zaehler = {s: 0 for s in (*_STATUS, "zurueckgegeben")}
    zaehler.update({z["status"]: z["n"] for z in zahlen})
    return {"mandanten": mandanten, "zaehler": zaehler}


@router.get("/inhalte")
def inhalte(mandant: str | None = None, art: str | None = None, status: str | None = None,
            x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant(mandant)
    a = _auswahl(art, _ARTEN, "art")
    s = _auswahl(status, _STATUS, "status")
    wo = [f"i.mandant = {lit(m)}"]
    if a:
        wo.append(f"i.art = {lit(a)}")
    if s:
        wo.append(f"i.status = {lit(s)}")
    zeilen = _lesen(lambda:
        "SELECT i.id, i.art, i.titel, i.status, i.erstellt_am::text AS erstellt_am, "
        "  (SELECT count(*) FROM marketing.inhalt_fassungen f WHERE f.inhalt = i.id)::int AS fassungen, "
        "  (SELECT f.layout FROM marketing.inhalt_fassungen f WHERE f.inhalt = i.id "
        "     ORDER BY f.fassung DESC LIMIT 1) AS layout, "
        "  (SELECT f.format FROM marketing.inhalt_fassungen f WHERE f.inhalt = i.id "
        "ORDER BY f.fassung DESC LIMIT 1) AS format, "
        "  (SELECT count(*) FROM marketing.rueckmeldungen r "
        "     WHERE r.inhalt = i.id AND r.erledigt_am IS NULL)::int AS offene_rueckmeldungen "
        f"FROM marketing.inhalte i WHERE {' AND '.join(wo)} ORDER BY i.erstellt_am DESC")
    return {"inhalte": zeilen}


_BILD_BASIS_HOST = re.compile(r"^(https://[^/\s\"'<>]+)/marketing/bild/[^/\s\"'<>]+/$")


def schrift_basis_aus(bild_basis: str) -> str:
    m = _BILD_BASIS_HOST.match(bild_basis or "")
    return f"{m.group(1)}/marketing/schrift/" if m else ""


def _bild_basis(wert: str | None) -> str:
    wert = wert or ""
    if wert and not _BILD_BASIS.match(wert):
        raise HTTPException(422, "bild_basis muss mit https:// beginnen, auf / enden und darf keine Anfuehrungszeichen oder Leerzeichen enthalten")
    return wert


# Signierte Bildbasis der sales-ui: /marketing/bild/<ablauf>.<hex-hmac>/ (sales-mcp/ui_editor.bild_token)
_PDF_BILD_PFAD = re.compile(r"^/marketing/bild/[0-9]{1,12}\.[0-9a-f]{64}/$")


def _pdf_erlaubt(*basen: str) -> list[str]:
    """Bild-/Schriftbasis nur, wenn ihr Host (mit Port) in MARKETING_PDF_HOSTS steht (Komma-Liste) und der Pfad die
    erwartete Form hat (/marketing/bild/<seg>/ bzw. /marketing/schrift/). Ohne
    Eintrag laedt der PDF-Druck nichts nach - Bilder fehlen dann, aber nichts verlaesst die VM."""
    hosts = {h.strip().lower() for h in os.environ.get("MARKETING_PDF_HOSTS", "").split(",") if h.strip()}
    aus = []
    for b in basen:
        try:
            teile = urlsplit(b or "")
        except ValueError:
            continue
        if any(z in (b or "") for z in "%?#\\") or teile.scheme != "https" or teile.netloc.lower() not in hosts:
            continue
        if _PDF_BILD_PFAD.match(teile.path) or teile.path == "/marketing/schrift/":
            aus.append(b)
    return aus


def _bloecke_html(dok, betreff: str, vorschautext: str, pflichtteil: dict, fmt: str, bild_basis: str) -> Response:
    schrift_basis = schrift_basis_aus(bild_basis)
    try:
        html = bloecke_mjml.rendern(
            dok, betreff, vorschautext, pflichtteil or {},
            bild_basis=bild_basis, handy=(fmt == "handy"), schrift_basis=schrift_basis)
    except bloecke_mjml.RenderFehler as e:
        raise HTTPException(422, str(e))
    if fmt != "pdf":
        return HTMLResponse(html)
    # Paket C3: dasselbe Mail-HTML als PDF (Chromium); laden darf es nur die eigenen Bild-/Schriftadressen -
    # und nur auf Hosts aus der Server-Konfiguration (Sicherheitspruefung 19.10.), nie aus der Anfrage allein
    return _pdf(html, bild_basis, schrift_basis)


def _pdf(html: str, bild_basis: str, schrift_basis: str) -> Response:
    try:
        roh = pdf_druck.html_zu_pdf(html, _pdf_erlaubt(bild_basis, schrift_basis))
    except pdf_druck.PdfDruckFehler as e:
        raise HTTPException(503, str(e))
    return Response(roh, media_type="application/pdf")


def _seite_antwort(dok, betreff: str, pflichtteil: dict, fmt: str, bild_basis: str) -> Response:
    """Collage-Fassung (format 'seite', Plan 2026-10-20 Stufe 1) als Seite: fmt 'seite' liefert das Seiten-HTML,
    pdf dasselbe HTML in voller Hoehe - mit denselben Ladegrenzen wie der Block-Weg. mail und handy zeigen seit
    Stufe 4 die Mail-Fassung (_seite_mail_antwort)."""
    schrift_basis = schrift_basis_aus(bild_basis)
    try:
        html = seite_html.html(dok, betreff=betreff, pflichtteil=pflichtteil or {},
                               bild_basis=bild_basis, schrift_basis=schrift_basis)
    except seite.SeiteFehler as e:
        raise HTTPException(422, str(e))
    if fmt != "pdf":
        return HTMLResponse(html)
    return _pdf(html, bild_basis, schrift_basis)


def _seite_mail_antwort(i: str, fassung: int, bild_basis: str) -> Response:
    """Mail-Vorschau einer Collage-Fassung (mail und handy, Stufe 4): die Mail-Fassung (bei Bedarf erzeugt,
    Fehler 422 mit Grund) mit der Bildbasis dieser Anfrage. Import erst hier: seite_mail_dienst braucht chat,
    chat braucht pult."""
    from spaces.marketing.api import seite_mail_dienst
    return HTMLResponse(seite_mail_dienst.vorschau(i, fassung, bild_basis))


def _erzeugt_ordner() -> str:
    """Ablage fuer erzeugte Medien (MARKETING_BILD_ORDNER) -- nur wenn es ein
    beschreibbarer Ordner ist, sonst "". Bewusst ohne api.bilder (Zirkelimport,
    und dessen _ordner() wirft 503): "Neu" darf an fehlender Ablage nie scheitern."""
    ordner = os.environ.get("MARKETING_BILD_ORDNER", "").strip()
    if ordner and os.path.isdir(ordner) and os.access(ordner, os.W_OK):
        return ordner
    return ""


def _medien_datei(verweis: str, ordner: str) -> str | None:
    """Pfad der Datei zu "medien:<name>" in der Ablage bzw. den Medienordnern; None, wenn es sie nicht gibt.
    Bewusst ohne api.bilder/api.gestaltung (Zirkelimport)."""
    name = verweis[len("medien:"):] if verweis.startswith("medien:") else ""
    if not name:
        return None
    for o in (ordner, os.environ.get("MARKETING_MEDIEN_ORDNER", ""), os.environ.get("MARKETING_BILD_ORDNER", "")):
        if not o or not o.strip():
            continue
        basis = os.path.realpath(o.strip())
        pfad = os.path.realpath(os.path.join(basis, name))
        try:
            drin = os.path.commonpath([basis, pfad]) == basis and pfad != basis
        except ValueError:
            continue
        if drin and os.path.isfile(pfad):
            return pfad
    return None


def _datei_existiert(name: str) -> bool:
    """Steht GENAU dieser Name noch in einem Medienordner (MARKETING_MEDIEN_ORDNER, MARKETING_BILD_ORDNER - wie
    gestaltung.quellen und bilder)? Exakter Eintrag der Verzeichnisliste, keine Gross/Klein-Faltung (wie
    bilder._im_ordner). Fail-closed: ohne konfigurierten oder ohne lesbaren Ordner laesst sich das nicht
    belegen -> 503."""
    ordner = [o.strip() for o in (os.environ.get("MARKETING_MEDIEN_ORDNER", ""),
                                  os.environ.get("MARKETING_BILD_ORDNER", "")) if o.strip()]
    if not ordner:
        raise HTTPException(503, "misconfigured: MARKETING_MEDIEN_ORDNER fehlt")
    for o in ordner:
        try:
            if name in os.listdir(o):
                return True
        except OSError:
            raise HTTPException(503, "Medienordner nicht lesbar")
    return False


def _medien_verweise(wert) -> set[str]:
    """Alle Dateinamen, auf die ein Wert "medien:<name>" irgendwo im Dokument zeigt (ohne Platzhalter)."""
    namen: set[str] = set()
    if isinstance(wert, dict):
        for x in wert.values():
            namen |= _medien_verweise(x)
    elif isinstance(wert, list):
        for x in wert:
            namen |= _medien_verweise(x)
    elif isinstance(wert, str) and wert.startswith("medien:") and not bildplaetze.PLATZHALTER.match(wert):
        name = wert[len("medien:"):]
        if name:
            namen.add(name)
    return namen


def _fehlende_bilder_ersetzen(bloecke: dict) -> tuple[dict, list[str]]:
    """Final-Review I2: Verweise auf Dateien, die es in keinem Medienordner mehr gibt, werden zu Platzhaltern
    (bildplaetze.platzhalter_einsetzen). Rueckgabe: neue Bloecke und die ersetzten Namen (sortiert). Steckt
    ein fehlender Name danach noch an einer Stelle, die kein Bildplatz ist -> 422 (wie /medien/platzhalter).
    Ohne konfigurierten Medienordner laesst sich nichts belegen -> 503 (fail-closed, wie _datei_existiert)."""
    fehlend = sorted(n for n in _medien_verweise(bloecke) if not _datei_existiert(n))
    neu = bloecke
    for name in fehlend:
        neu, _ = bildplaetze.platzhalter_einsetzen(neu, name)
        rest = re.compile(r"medien:" + re.escape(name) + r"(?![\w.\-])")
        if rest.search(json.dumps(neu, ensure_ascii=False)):
            raise HTTPException(422, f"Bild {name} gibt es nicht mehr und steckt an einer Stelle, die kein "
                                     "Bildplatz ist – diese Fassung lässt sich nicht wiederherstellen")
    return neu, fehlend


def _laden_lesen(m: str, webseite: bool = False) -> dict:
    """Name und Standard-Newsletter-Layout (Gestalt) des Ladens; {} wenn es keins gibt. webseite=True liest
    dazu die Webseite und die Kopfwerte (werte: Farben, Schriften) aus dem Markenprofil (marken_spiegel.profil,
    066) - nur die Collage braucht sie."""
    spalte, spiegel = ((", s.profil->'werte'->>'webseite' AS webseite, s.profil->'werte' AS werte",
                        "LEFT JOIN marketing.marken_spiegel s ON s.mandant = m.id ") if webseite else ("", ""))
    return _lesen_einer(lambda:
        f"SELECT m.name AS laden, l.name AS layout, l.gestalt{spalte} FROM marketing.mandanten m "
        "LEFT JOIN marketing.layout_vorlagen l ON l.mandant = m.id AND l.standard AND l.inhaltsart = 'newsletter' "
        f"{spiegel}WHERE m.id = {lit(m)}") or {}


def _vorlage_fuellen(dok: dict, m: str, laden: dict, logo: str | None = None,
                    logo_dunkel: str | None = None) -> tuple[dict, str | None]:
    """Vorlage mit der Marke des Ladens fuellen (Spec 2026-10-01 §5): Farbrollen, Name, Logo-Datei,
    tech-Grafiken. Gemeinsam fuer "Neu aus Vorlage" und die Vorlagen-Vorschau. Gibt das fertige
    Dokument und den Namen des Standard-Layouts (oder None) zurueck. Das Schriftpaar der Marke
    (gestalt.schriften, nur zwei Register-Ids) landet in root.data.schriften; ohne bleibt die Vorlage
    unveraendert. logo = fertiger Medienverweis (medien:<name>), der das Logo aus der Gestalt ersetzt."""
    grund = ((dok.get("root") or {}).get("data") or {}).get("canvasColor") or "#ffffff"
    werte: dict = vorlagen_marke.rollen(laden.get("gestalt"), grund)
    ordner = _erzeugt_ordner()
    werte["laden"] = str(laden.get("laden") or m)
    gestalt = laden.get("gestalt")
    if logo is None:         # Logo aus der Gestalt: dann gilt auch deren dunkle Fassung
        logo = vorlagen_marke.logo_ablegen(gestalt, m, ordner)
        if logo and logo_dunkel is None:
            logo_dunkel = vorlagen_marke.logo_ablegen(gestalt, m, ordner, "logo_dunkel")
    if logo:
        werte["logo"] = logo
    rollen_tab = ((dok.get("root") or {}).get("data") or {}).get("rollen") or {}
    if "signal_bild" in rollen_tab.values():
        werte["signal_bild"] = vorlagen_grafik.signal(werte["akzent"], ordner) or "medien:platzhalter-4x3.png"
    if "glow_bild" in rollen_tab.values():
        werte["glow_bild"] = vorlagen_grafik.glow(werte["akzent"], ordner) or "medien:platzhalter-4x3.png"
    fertig = vorlagen_marke.einsetzen(dok, werte)
    if logo and logo_dunkel and vorlagen_marke.ist_dunkel(vorlagen_marke.logo_grund(fertig)):
        logo = logo_dunkel
        fertig = vorlagen_marke.einsetzen(dok, {**werte, "logo": logo_dunkel})   # dunkle Flaeche: logo_dunkel
    if logo:   # Logo in den Kasten der Vorlage einpassen (Seitenverhaeltnis bleibt)
        fertig = vorlagen_marke.logo_einpassen(fertig, vorlagen_marke.logo_masse(_medien_datei(logo, ordner)))
    paar = _schriftpaar(laden.get("gestalt"))
    if paar:
        fertig.setdefault("root", {}).setdefault("data", {})["schriften"] = paar
    return fertig, laden.get("layout")


def _schriftpaar(gestalt) -> dict | None:
    sw = gestalt.get("schriften") if isinstance(gestalt, dict) else None
    if not isinstance(sw, dict):
        return None
    anzeige, text = sw.get("anzeige"), sw.get("text")
    if isinstance(anzeige, str) and isinstance(text, str) and anzeige in schriften.REGISTER and text in schriften.REGISTER:
        return {"anzeige": anzeige, "text": text}
    return None


@router.post("/inhalte/aus_vorlage")
def inhalt_aus_vorlage(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    vorlage = payload.get("vorlage")
    if not isinstance(vorlage, str) or not _VORLAGE.match(vorlage):
        raise HTTPException(422, "Unbekannte Vorlage")
    titel = payload.get("titel")
    if not isinstance(titel, str) or not titel.strip() or len(titel.strip()) > 200:
        raise HTTPException(422, "titel fehlt oder ist laenger als 200 Zeichen")
    m = _mandant(payload.get("mandant"))
    # Format der Vorlage und "nur noch Collagen" (gibt es eine freigegebene Collage-Vorlage fuer die Firma?) in
    # derselben Leseabfrage
    v = _lesen_einer(lambda:
        "SELECT format, bloecke, seite, rollen, "
        f"marketing.pult_hat_collage_vorlage({lit(m)}) AS nur_collagen FROM marketing.newsletter_vorlagen "
        f"WHERE name = {lit(vorlage)} AND status = 'freigegeben' AND (mandant = {lit(m)} OR fuer_alle)")
    if not v:
        raise HTTPException(422, f"Vorlage {vorlage} gibt es nicht oder sie ist nicht freigegeben")
    if v.get("format") == "seite":
        return _collage_aus_vorlage(vorlage, titel.strip(), m, v)
    if v.get("nur_collagen") is True:
        raise HTTPException(409, NUR_COLLAGEN)
    fertig, layout = _vorlage_fuellen(v["bloecke"], m, _laden_lesen(m))
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_inhalt_aus_vorlage({lit(vorlage)}, {lit(titel.strip())}, {lit(m)}, "
        f"{lit(json.dumps(fertig, ensure_ascii=False))}::jsonb, {lit(layout) if layout else 'NULL'}) AS id")
    return {"id": str(zeile["id"])}


def _marke_fuer_vorlage(m: str, laden: dict, ordner: str) -> dict:
    """Marke fuer seite_vorlagen.fuellen: Akzent, Schriftpaar und Webseite wie _marke_fuer_seite (Gestalt des
    Standard-Layouts), Zweitfarbe/Grund/Text (und fehlende Akzent/Schriften) aus den Kopfwerten des Markenprofils,
    Name = Name des Ladens. Logo und dunkles Logo aus der Gestalt als Mediendatei abgelegt wie bei den Block-
    Vorlagen (vorlagen_marke.logo_ablegen) - ohne Ablage oder Logo: None (die Logo-Ebene entfaellt)."""
    marke = _marke_fuer_seite(laden)
    werte = laden.get("werte") if isinstance(laden.get("werte"), dict) else {}
    for k in ("akzent", "zweitfarbe", "grund", "text", "schrift_anzeige", "schrift_text"):
        if not marke.get(k) and isinstance(werte.get(k), str):
            marke[k] = werte[k]
    marke["name"] = str(laden.get("laden") or "").strip() or None
    gestalt = laden.get("gestalt")
    logo = vorlagen_marke.logo_ablegen(gestalt, m, ordner)
    marke["logo"] = logo
    marke["logo_dunkel"] = vorlagen_marke.logo_ablegen(gestalt, m, ordner, "logo_dunkel") if logo else None
    return marke


def _seiten_vorlage_fuellen(v: dict, m: str) -> tuple[dict, list[dict], list[str]]:
    """Seiten-Vorlage (Zeile mit seite, rollen) mit der Marke der Firma m fuellen: (Seite, Bildauftraege,
    Warnungen). Eine Seite, die danach nicht gueltig ist -> 422 mit Grund."""
    marke = _marke_fuer_vorlage(m, _laden_lesen(m, webseite=True), _erzeugt_ordner())
    rollen = v.get("rollen") if isinstance(v.get("rollen"), dict) else {}
    try:
        s, auftraege, warnungen = seite_vorlagen.fuellen(v.get("seite"), rollen, marke, marke.get("logo"))
    except seite.SeiteFehler as e:
        raise HTTPException(422, str(e))
    return _seite_pflicht(s), auftraege, warnungen


def _collage_aus_vorlage(vorlage: str, titel: str, m: str, v: dict) -> dict:
    """Neuer Collage-Newsletter aus einer Seiten-Vorlage: fuellen (seite_vorlagen.fuellen), anlegen ueber
    marketing.pult_collage_aus_vorlage (082, prueft Freigabe und Firma noch einmal unter Sperre), danach fuer
    jede Motiv-Ebene ein Bildauftrag ueber die bestehende Bildkette (bilder._anlegen, modus neu). Ein Auftrag,
    der nicht angelegt werden kann, macht den Newsletter nicht ungueltig - er steht in den Warnungen."""
    from spaces.marketing.api import bilder      # im Rumpf: bilder importiert pult
    s, auftraege, warnungen = _seiten_vorlage_fuellen(v, m)
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_collage_aus_vorlage({lit(vorlage)}, {lit(titel)}, {lit(m)}, "
        f"{lit(json.dumps(s, ensure_ascii=False))}::jsonb, 'betreiber') AS id")
    iid = str(zeile["id"])
    for a in auftraege:
        try:
            bilder._anlegen(iid, {"platz": a["platz"], "modus": "neu", "hinweis": a.get("hinweis") or ""}, "system")
        except HTTPException as e:
            warnungen.append(f"Bild für '{a['platz']}' konnte nicht beauftragt werden: {e.detail}")
    return {"id": iid, "warnungen": warnungen}


def _basis_pflicht(wert) -> int:
    if isinstance(wert, bool) or not isinstance(wert, int) or wert < 0:
        raise HTTPException(422, "basis_fassung muss eine ganze Zahl >= 0 sein")
    return wert


def _bloecke_als_fassung(i: str, basis: int, felder, bloecke: dict) -> dict:
    """Neue Editor-Fassung (Urheber betreiber) mit Betreff/Vorschautext aus felder. Die Regeln (nur
    Entwurf, Basis = neueste Fassung, kein laufender Assistent) setzt pult_bloecke_speichern durch; eine
    Collage (neueste Fassung 'seite') sperrt mit_formatsperre in derselben Abfrage."""
    felder = felder if isinstance(felder, dict) else {}
    betreff, vorschau = str(felder.get("betreff") or ""), str(felder.get("vorschautext") or "")
    zeile = mit_formatsperre(i, lambda:
        f"marketing.pult_bloecke_speichern({lit(i)}::uuid, {int(basis)}, {lit(betreff)}, "
        f"{lit(vorschau)}, {lit(json.dumps(bloecke, ensure_ascii=False))}::jsonb, 'betreiber', false)")
    return {"fassung": int(zeile["fassung"])}


@router.post("/inhalte/{iid}/vorlage")
def vorlage_anwenden(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """Vorlage auf einen Newsletter-Entwurf anwenden (Plan 2026-10-19 E9): Aufbau, Gestaltung, Texte und
    Bilder kommen vollstaendig aus der Vorlage (mit der Marke der Firma DES INHALTS gefuellt), Betreff und
    Vorschautext der neuesten Fassung bleiben. Legt eine neue Fassung an."""
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    vorlage = payload.get("vorlage")
    if not isinstance(vorlage, str) or not _VORLAGE.match(vorlage):
        raise HTTPException(422, "Unbekannte Vorlage")
    basis = _basis_pflicht(payload.get("basis_fassung"))
    _von(payload)
    kopf = _lesen_einer(lambda:
        "SELECT i.mandant, i.status, i.art, n.fassung, n.felder, n.format FROM marketing.inhalte i "
        "LEFT JOIN LATERAL (SELECT fassung, felder, format FROM marketing.inhalt_fassungen f WHERE f.inhalt = i.id "
        f"ORDER BY fassung DESC LIMIT 1) n ON true WHERE i.id = {lit(i)}::uuid")
    if not kopf:
        raise HTTPException(404, "Unbekannter Inhalt")
    if kopf.get("format") == "seite":   # frueh, damit nichts gefuellt wird; die Schreib-Abfrage sperrt ohnehin
        raise HTTPException(409, COLLAGE_IST)
    if kopf.get("art") != "newsletter" or kopf.get("status") != "entwurf":
        raise HTTPException(422, "Vorlagen lassen sich nur auf Newsletter-Entwürfe anwenden")
    m = _mandant(kopf.get("mandant"))
    v = _lesen_einer(lambda:
        "SELECT format, bloecke FROM marketing.newsletter_vorlagen "
        f"WHERE name = {lit(vorlage)} AND status = 'freigegeben' AND (mandant = {lit(m)} OR fuer_alle)")
    if not v:
        raise HTTPException(422, f"Vorlage {vorlage} gibt es nicht oder sie ist nicht freigegeben")
    if v.get("format") == "seite" or not isinstance(v.get("bloecke"), dict):
        raise HTTPException(422, SEITEN_VORLAGE_NICHT_ANWENDBAR)
    fertig, _ = _vorlage_fuellen(v["bloecke"], m, _laden_lesen(m))
    return _bloecke_als_fassung(i, basis, kopf.get("felder"), fertig)


@router.post("/inhalte/{iid}/wiederherstellen")
def fassung_wiederherstellen(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """Alte Editor-Fassung als neue Fassung anlegen (Plan 2026-10-19 E12), samt ihrem Betreff und
    Vorschautext. Die alte Fassung selbst bleibt unveraendert. Bilder, die es nicht mehr gibt, werden zu
    Platzhaltern; ihre Namen stehen in "platzhalter"."""
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    n = _fassung_pflicht(payload.get("fassung"))
    basis = _basis_pflicht(payload.get("basis_fassung"))
    _von(payload)
    alt = _lesen_einer(lambda:
        "SELECT format, bloecke, felder FROM marketing.inhalt_fassungen "
        f"WHERE inhalt = {lit(i)}::uuid AND fassung = {int(n)}")
    if not alt:
        raise HTTPException(404, "Unbekannte Fassung")
    if alt.get("format") != "bloecke" or not isinstance(alt.get("bloecke"), dict):
        raise HTTPException(422, "Nur Editor-Fassungen lassen sich wiederherstellen")
    # Final-Review I2: alte Fassungen tragen nach dem Loeschen eines Bildes noch medien:<geloescht> (E4) -
    # diese Plaetze werden Platzhalter, damit kein totes Bild in den versendbaren Stand kommt.
    bloecke, platzhalter = _fehlende_bilder_ersetzen(alt["bloecke"])
    return {**_bloecke_als_fassung(i, basis, alt.get("felder"), bloecke), "platzhalter": platzhalter}


@router.get("/vorlagen")
def vorlagen(mandant: str | None = None, status: str | None = None,
             x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant(mandant)
    s = _auswahl(status, ("vorschlag", "freigegeben", "zurueckgezogen"), "status")
    wo = [f"(mandant = {lit(m)} OR fuer_alle)"] + (
        [f"status = {lit(s)}"] if s else ["status <> 'zurueckgezogen'"])
    zeilen = _lesen(lambda:
        "SELECT name, beschreibung, status, fassung, format FROM marketing.newsletter_vorlagen "
        f"WHERE {' AND '.join(wo)} ORDER BY name")
    return {"vorlagen": zeilen}


@router.get("/vorlagen/{name}/vorschau")
def vorlage_vorschau(name: str, format: str = "mail", bild_basis: str = "",
                     mandant: str | None = None, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    if not _VORLAGE.match(name):
        raise HTTPException(404, "Unbekannte Vorlage")
    fmt = _auswahl(format, ("mail", "handy", "seite"), "format") or "mail"
    basis = _bild_basis(bild_basis)
    m = _mandant(mandant)
    zeile = _lesen_einer(lambda:
        "SELECT v.format, v.bloecke, v.seite, v.rollen, v.beschreibung, m.pflichtteil "
        "FROM marketing.newsletter_vorlagen v JOIN marketing.mandanten m ON m.id = "
        f"{lit(m)} WHERE v.name = {lit(name)} AND (v.mandant = {lit(m)} OR v.fuer_alle)")
    if not zeile:
        raise HTTPException(404, "Unbekannte Vorlage")
    if zeile.get("format") == "seite":
        # Collage-Vorlage: immer die gefuellte Seite als Seiten-HTML, auch bei format=mail|handy - eine Mail-Fassung
        # gibt es erst nach dem Anlegen, und die sales-Oberflaeche (ui_editor.vorlage_bild/editor_vorlage_bild)
        # fragt heute jede Vorlage mit format=mail an (Fix-Runde 1: bewusst KEIN 422). Motive stehen als
        # Platzhalter da, ihre Bilder entstehen erst beim Anlegen.
        s, _, _ = _seiten_vorlage_fuellen(zeile, m)
        return _seite_antwort(s, zeile.get("beschreibung") or "", zeile.get("pflichtteil"), "seite", basis)
    if fmt == "seite" or not isinstance(zeile.get("bloecke"), dict):
        raise HTTPException(422, BLOCK_VORLAGE_OHNE_SEITE)
    # Vorschau fuer den anfragenden Laden: dieselbe Fuellung wie "Neu aus Vorlage" (keine
    # Musterfarben, keine Muster-Grafiknamen, nicht Logo und Wortmarke zugleich)
    fertig, _ = _vorlage_fuellen(zeile["bloecke"], m, _laden_lesen(m))
    return _bloecke_html(fertig, zeile.get("beschreibung") or "", "", zeile.get("pflichtteil"), fmt, basis)


@router.post("/vorlagen/{name}/entscheiden")
def vorlage_entscheiden(name: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """Vorlage freigeben oder zurueckziehen (Betreiber in sales). Die Uebergaenge setzt
    marketing.pult_vorlage_entscheiden (082) durch; Ablehnung 422 mit ihrem Grund."""
    _schluessel(x_pult_key)
    if not _VORLAGE.match(name):
        raise HTTPException(404, "Unbekannte Vorlage")
    urteil = payload.get("urteil")
    if urteil not in ("freigeben", "zurueckziehen"):
        raise HTTPException(422, "urteil muss freigeben oder zurueckziehen sein")
    von = payload.get("von")
    if not isinstance(von, str) or not von.strip():
        raise HTTPException(422, "Ohne Namen keine Entscheidung")
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_vorlage_entscheiden({lit(name)}, {lit(urteil)}, {lit(von.strip())}) AS status")
    return {"name": name, "status": str(zeile["status"])}


@router.post("/inhalte/{iid}/bloecke")
def bloecke_speichern(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    basis = payload.get("basis_fassung")
    if isinstance(basis, bool) or not isinstance(basis, int) or basis < 0:
        raise HTTPException(422, "basis_fassung muss eine ganze Zahl >= 0 sein")
    bloecke = payload.get("bloecke")
    if not isinstance(bloecke, dict):
        raise HTTPException(422, "bloecke fehlt")
    urheber = payload.get("urheber", "betreiber")
    if urheber not in _URHEBER:
        raise HTTPException(422, "urheber muss betreiber oder agent sein")
    als_kopie = payload.get("als_kopie", False)
    if not isinstance(als_kopie, bool):
        raise HTTPException(422, "als_kopie muss true oder false sein")
    betreff = payload.get("betreff", "")
    vorschautext = payload.get("vorschautext", "")
    if not isinstance(betreff, str) or not isinstance(vorschautext, str):
        raise HTTPException(422, "betreff und vorschautext muessen Text sein")
    zeile = mit_formatsperre(i, lambda:
        f"marketing.pult_bloecke_speichern({lit(i)}::uuid, {int(basis)}, {lit(betreff)}, "
        f"{lit(vorschautext)}, {lit(json.dumps(bloecke, ensure_ascii=False))}::jsonb, "
        f"{lit(urheber)}, {'true' if als_kopie else 'false'})")
    return {"fassung": int(zeile["fassung"])}


def _seite_pflicht(wert) -> dict:
    """Jede Seite laeuft VOR dem Schreiben durch seite.pruefen (die DB prueft die Anrede-/Link-Regel nicht);
    gespeichert wird das normalisierte Dokument. Fehler -> 422 mit dem deutschen Grund, ohne SQL."""
    try:
        return seite.pruefen(wert)
    except seite.SeiteFehler as e:
        raise HTTPException(422, str(e))


def _marke_fuer_seite(laden: dict) -> dict:
    """Marke fuer seite.leer aus derselben Gestalt, die _vorlage_fuellen nutzt (Akzent, Schriftpaar), dazu
    die Webseite aus dem Markenprofil als Ziel des Startknopfs (nur gueltiges https, sonst example.com)."""
    gestalt = laden.get("gestalt") if isinstance(laden.get("gestalt"), dict) else {}
    paar = _schriftpaar(gestalt) or {}
    web = laden.get("webseite")
    return {"akzent": gestalt.get("akzent"), "schrift_anzeige": paar.get("anzeige"),
            "schrift_text": paar.get("text"), "webseite": web if markenprofil.webseite_gueltig(web) else None}


@router.post("/inhalte/collage")
def collage_anlegen(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """Neuer Collage-Newsletter (format 'seite'): Startseite aus seite.leer mit der Marke der Firma,
    Inhalt und Fassung 1 legt marketing.pult_collage_anlegen (077) an."""
    _schluessel(x_pult_key)
    m = _mandant_pflicht(payload.get("mandant"))
    titel = payload.get("titel")
    if not isinstance(titel, str) or not titel.strip() or len(titel.strip()) > 200:
        raise HTTPException(422, "titel fehlt oder ist laenger als 200 Zeichen")
    betreff = payload.get("betreff", "")
    if not isinstance(betreff, str):
        raise HTTPException(422, "betreff muss Text sein")
    dok = _seite_pflicht(seite.leer(_marke_fuer_seite(_laden_lesen(m, webseite=True))))
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_collage_anlegen({lit(m)}, {lit(titel.strip())}, {lit(betreff)}, "
        f"{lit(json.dumps(dok, ensure_ascii=False))}::jsonb, 'betreiber') AS id")
    return {"inhalt": str(zeile["id"]), "fassung": 1}


@router.post("/inhalte/{iid}/seite")
def seite_speichern(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """Neue Collage-Fassung. Die Sperren (nur Entwurf, Basis = neueste Fassung, kein laufender Assistent)
    setzt marketing.pult_seite_speichern (077) durch, wie pult_bloecke_speichern fuer Bloecke. Nur auf einer
    Collage oder einem Inhalt ohne Fassung - sonst 409 (mit_formatsperre, dieselbe Abfrage)."""
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    basis = _basis_pflicht(payload.get("basis_fassung"))
    if not isinstance(payload.get("seite"), dict):
        raise HTTPException(422, "seite fehlt")
    urheber = payload.get("urheber", "betreiber")
    if urheber not in _URHEBER:
        raise HTTPException(422, "urheber muss betreiber oder agent sein")
    als_kopie = payload.get("als_kopie", False)
    if not isinstance(als_kopie, bool):
        raise HTTPException(422, "als_kopie muss true oder false sein")
    betreff = payload.get("betreff", "")
    vorschautext = payload.get("vorschautext", "")
    if not isinstance(betreff, str) or not isinstance(vorschautext, str):
        raise HTTPException(422, "betreff und vorschautext muessen Text sein")
    dok = _seite_pflicht(payload["seite"])
    zeile = mit_formatsperre(i, lambda:
        f"marketing.pult_seite_speichern({lit(i)}::uuid, {int(basis)}, {lit(betreff)}, "
        f"{lit(vorschautext)}, {lit(json.dumps(dok, ensure_ascii=False))}::jsonb, "
        f"{lit(urheber)}, {'true' if als_kopie else 'false'})", collage=True, grund=COLLAGE_IST_NICHT)
    return {"fassung": int(zeile["fassung"])}


@router.get("/inhalte/{iid}")
def inhalt(iid: str, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    # alter_weg: stammt der Inhalt aus dem alten Freigabeweg (broadcast_
    # proposals -> Telegram -> n8n), steht dort sein eigener Status. Die
    # Bruecke spiegelt nur alt -> Pult; ein Urteil hier stoppt den alten Weg
    # NICHT - die Oberflaeche muss das zeigen (final-fix-findings.md I3).
    kopf = _lesen_einer(lambda:
        "SELECT i.id, i.mandant, i.art, i.titel, i.status, i.freigegebene_fassung, i.entschieden_von, "
        "i.entschieden_am::text AS entschieden_am, i.grund, "
        "i.eingereichte_fassung, i.eingereicht_am::text AS eingereicht_am, i.eingereicht_von, "
        "i.marke_geaendert_am::text AS marke_geaendert_am, "
        "CASE WHEN i.herkunft_proposal IS NULL THEN NULL "
        "ELSE jsonb_build_object('status', p.status, 'kanal', p.channel) END AS alter_weg "
        "FROM marketing.inhalte i "
        "LEFT JOIN marketing.broadcast_proposals p ON p.id = i.herkunft_proposal "
        f"WHERE i.id = {lit(i)}::uuid")
    if not kopf:
        raise HTTPException(404, "Unbekannter Inhalt")
    alter_weg = kopf.pop("alter_weg", None)
    # bloecke nur fuer die neueste Fassung (Entwurfsseite und Editor brauchen
    # nur sie) - aeltere Dokumente wuerden die Antwort nur aufblaehen.
    fassungen = _lesen(lambda:
        "SELECT fassung, felder, layout, urheber, erstellt_am::text AS erstellt_am, format, "
        "CASE WHEN fassung = max(fassung) OVER () THEN bloecke END AS bloecke, "
        "CASE WHEN fassung = max(fassung) OVER () THEN seite END AS seite "
        f"FROM marketing.inhalt_fassungen WHERE inhalt = {lit(i)}::uuid ORDER BY fassung DESC")
    for nr, f in enumerate(fassungen):
        if isinstance(f, dict):
            f.setdefault("seite", None)
            if nr:
                f["bloecke"] = f["seite"] = None
    rueckmeldungen = _lesen(lambda:
        "SELECT r.id::text AS id, r.fassung, r.text, r.von, r.am::text AS am, "
        "r.erledigt_am::text AS erledigt_am, (r.erledigt_am IS NOT NULL) AS erledigt "
        f"FROM marketing.rueckmeldungen r WHERE r.inhalt = {lit(i)}::uuid ORDER BY r.am DESC")
    return {"inhalt": kopf, "fassungen": fassungen, "alter_weg": alter_weg or None,
            "rueckmeldungen": rueckmeldungen}


@router.post("/inhalte/{iid}/in_bloecke")
def in_bloecke(iid: str, payload: dict = Body(default={}), x_pult_key: str | None = Header(None)):
    """Feld-Newsletter (Bruecke aus dem alten Weg) ins Editor-Format
    uebernehmen: marketing.pult_in_bloecke_uebernehmen (055) legt eine neue
    Block-Fassung an und lehnt alles andere mit deutschem Grund ab (422)."""
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    von = payload.get("von", "betreiber") if isinstance(payload, dict) else "betreiber"
    if not isinstance(von, str):
        raise HTTPException(422, "von muss Text sein")
    von = von.strip() or "betreiber"
    zeile = mit_formatsperre(i, lambda: f"marketing.pult_in_bloecke_uebernehmen({lit(i)}::uuid, {lit(von)})")
    return {"fassung": int(zeile["fassung"])}


@router.post("/inhalte/{iid}/fassungen")
def fassung_speichern(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    felder = payload.get("felder")
    if not isinstance(felder, dict):
        raise HTTPException(422, "felder fehlt")
    layout = str(payload.get("layout") or "")
    zeile = mit_formatsperre(i, lambda:
        f"marketing.pult_fassung_speichern({lit(i)}::uuid, "
        f"{lit(json.dumps(felder, ensure_ascii=False))}::jsonb, "
        f"{lit(layout)}, 'betreiber')")
    return {"fassung": int(zeile["fassung"])}


@router.get("/inhalte/{iid}/versand")
def versand(iid: str, x_pult_key: str | None = Header(None)):
    """Versandfassung eines freigegebenen Editor-Newsletters fuer sales-claw (Plan 2026-10-15-newsletter-
    versand-an-kontakte.md): HTML mit eingebetteten Bildern (cid:), Textfassung und die Medienliste.
    {vorname} und {abmeldelink} bleiben stehen - eingesetzt wird je Empfaenger in sales-claw, Marketing
    sieht keine Namen. Nur die freigegebene Fassung, nie ein Entwurf. Collagen (Stufe 4): dieselbe Form aus
    der Mail-Fassung der freigegebenen Fassung (seite_mail_dienst, bei Bedarf erzeugt)."""
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    zeile = _lesen_einer(lambda:
        "SELECT i.status, i.mandant, f.fassung, f.format, f.bloecke, f.felder, m.pflichtteil "
        "FROM marketing.inhalte i "
        "JOIN marketing.mandanten m ON m.id = i.mandant "
        "LEFT JOIN marketing.inhalt_fassungen f ON f.inhalt = i.id AND f.fassung = i.freigegebene_fassung "
        f"WHERE i.id = {lit(i)}::uuid")
    if not zeile:
        raise HTTPException(404, "Unbekannter Inhalt")
    if zeile.get("status") != "freigegeben" or zeile.get("fassung") is None:
        raise HTTPException(409, "Nur freigegebene Newsletter lassen sich versenden")
    felder = zeile.get("felder") or {}
    kopf = {"inhalt": i, "mandant": zeile.get("mandant"), "fassung": int(zeile["fassung"]),
            "betreff": felder.get("betreff", ""), "vorschautext": felder.get("vorschautext", "")}
    if zeile.get("format") == "seite":
        from spaces.marketing.api import seite_mail_dienst   # erst hier: seite_mail_dienst -> chat -> pult
        return {**kopf, **seite_mail_dienst.versand(i, int(zeile["fassung"]))}
    if zeile.get("format") != "bloecke":
        raise HTTPException(422, "Versand gibt es nur fuer Editor-Newsletter")
    pflicht, dok = zeile.get("pflichtteil") or {}, zeile["bloecke"]
    try:
        html = bloecke_mjml.rendern(dok, felder.get("betreff", ""), felder.get("vorschautext", ""), pflicht,
                                    fuer_versand=True)
    except bloecke_mjml.RenderFehler as e:
        raise HTTPException(422, str(e))
    return {**kopf, "html": html, "text": bloecke_mjml.text_fassung(dok, pflicht),
            "medien": bloecke_mjml.versand_medien(dok)}


@router.get("/inhalte/{iid}/vorschau")
def vorschau(iid: str, fassung: int = Query(..., ge=1), format: str = "mail", bild_basis: str = "",
             x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    # format=seite (Fix-Runde 1): die Collage-Seite selbst als HTML - billig (kein Chromium, keine Dateien), fuer
    # Galeriekarten und die sich selbst erneuernde Entwurfsvorschau. mail/handy einer Collage = die echte Mail.
    fmt = _auswahl(format, (*_FORMATE, "seite"), "format")
    basis = _bild_basis(bild_basis)
    # Die Fassung zeigt das Layout in der Fassung, mit der sie gespeichert
    # wurde (layout_fassungen, unveraenderlich) - nicht das heutige Aussehen.
    # Fehlt der Pin (layout_fassung NULL), bleibt die aktuelle Gestalt. Die
    # zu rendernde Gestalt wird in DERSELBEN strengen Abfrage geprueft: eine
    # gespeicherte, ungueltige Gestalt ist 422 mit Grund, nie ein 500 und nie
    # ungeprueft im style-Attribut.
    gestalt_sql = "coalesce(lf.gestalt, l.gestalt)"
    zeile = _lesen_einer(lambda:
        f"SELECT f.felder, f.format, f.bloecke, f.seite, {gestalt_sql} AS gestalt, m.pflichtteil, "
        f"CASE WHEN f.format = 'felder' THEN marketing.pult_gestalt_fehler({gestalt_sql}) END AS gestalt_fehler "
        "FROM marketing.inhalt_fassungen f "
        "JOIN marketing.inhalte i ON i.id = f.inhalt "
        "JOIN marketing.mandanten m ON m.id = i.mandant "
        "LEFT JOIN marketing.layout_vorlagen l ON l.name = f.layout "
        "LEFT JOIN marketing.layout_fassungen lf ON lf.layout = f.layout AND lf.fassung = f.layout_fassung "
        f"WHERE f.inhalt = {lit(i)}::uuid AND f.fassung = {int(fassung)}")
    if not zeile:
        raise HTTPException(404, "Unbekannte Fassung")
    if fmt == "seite" and zeile.get("format") != "seite":
        raise HTTPException(422, COLLAGE_IST_NICHT)
    if zeile.get("format") == "bloecke":
        felder = zeile.get("felder") or {}
        return _bloecke_html(zeile["bloecke"], felder.get("betreff", ""), felder.get("vorschautext", ""),
                             zeile.get("pflichtteil"), fmt, basis)
    if zeile.get("format") == "seite" and fmt in (None, "mail", "handy"):
        return _seite_mail_antwort(i, int(fassung), basis)
    if zeile.get("format") == "seite":
        return _seite_antwort(zeile.get("seite"), (zeile.get("felder") or {}).get("betreff", ""),
                              zeile.get("pflichtteil"), fmt or "mail", basis)
    if zeile.get("gestalt_fehler"):
        raise HTTPException(422, str(zeile["gestalt_fehler"]))
    return _rendern(zeile["felder"], zeile["gestalt"], zeile["pflichtteil"], fmt)


def _rendern(felder: dict, gestalt: dict, pflichtteil: dict, fmt: str) -> Response:
    if fmt == "pdf":
        return Response(pult_render.pdf_bytes(felder, gestalt), media_type="application/pdf")
    baue = pult_render.handy_html if fmt == "handy" else pult_render.mail_html
    return HTMLResponse(baue(felder, gestalt, pflichtteil))


@router.post("/inhalte/{iid}/entscheiden")
def entscheiden(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    f = _fassung_pflicht(payload.get("fassung"))
    urteil = str(payload.get("urteil") or "")
    von = str(payload.get("von") or "")
    grund = str(payload.get("grund") or "")
    if urteil == "ablehnen" and not grund.strip():
        raise HTTPException(422, "Bitte gib einen Grund an")      # Verwerfen: Grund Pflicht (R6)
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_entscheiden({lit(i)}::uuid, {f}, {lit(urteil)}, "
        f"{lit(von)}, {lit(grund)}) AS status")
    return {"status": zeile["status"]}


def _mandant_pflicht(wert) -> str:
    """Firma ausdruecklich angeben, ohne Rueckfall auf vibemind (fail-closed): fehlt sie oder passt das
    Format nicht, 422 ohne SQL. Einzige Fassung - medien_mandant importiert sie von hier."""
    if not isinstance(wert, str) or not _NAME.match(wert):
        raise HTTPException(422, "Unbekannter Mandant")
    return wert


def _mandant_bekannt(wert) -> str:
    """Wie _mandant_pflicht, dazu muss die Firma in marketing.mandanten stehen - geprueft mit einer reinen
    Leseabfrage, BEVOR eine schreibende Funktion laeuft (Review M1). Unbekannt -> 422 "Unbekannter Mandant"."""
    m = _mandant_pflicht(wert)
    if not _lesen_einer(lambda: f"SELECT id FROM marketing.mandanten WHERE id = {lit(m)}"):
        raise HTTPException(422, "Unbekannter Mandant")
    return m


def _von(payload: dict) -> str:
    von = payload.get("von", "betreiber")
    if not isinstance(von, str):
        raise HTTPException(422, "von muss Text sein")
    return von.strip()[:100] or "betreiber"


@router.post("/inhalte/{iid}/loeschen")
def inhalt_loeschen(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    """Entwurf endgueltig loeschen (Plan 2026-10-19 E1/E2). Die Regeln (nur entwurf/abgelehnt, eigene
    Firma, kein laufender Assistent/Bildauftrag, alter Freigabeweg erledigt) setzt
    marketing.pult_inhalt_loeschen (075) durch; ihre Ablehnung kommt als 422 mit Grund."""
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    m = _mandant_pflicht(payload.get("mandant"))
    von = _von(payload)
    _mandant_bekannt(m)
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_inhalt_loeschen({lit(i)}::uuid, {lit(m)}, {lit(von)}) AS titel")
    return {"geloescht": i, "titel": zeile.get("titel") or ""}


@router.get("/layouts")
def layouts(mandant: str | None = None, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant(mandant)
    zeilen = _lesen(lambda:
        "SELECT name, beschreibung, inhaltsart, fassung, standard, status, gestalt "
        f"FROM marketing.layout_vorlagen WHERE art = 'layout' AND mandant = {lit(m)} "
        "ORDER BY inhaltsart, standard DESC, name")
    return {"layouts": zeilen}


@router.post("/layouts/vorschau")
def layout_vorschau(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    gestalt = payload.get("gestalt")
    if not isinstance(gestalt, dict):
        raise HTTPException(422, "gestalt fehlt")
    fmt = _auswahl(payload.get("format") or "mail", _FORMATE, "format")
    m = _mandant(payload.get("mandant"))
    fehler = _pruefen_gestalt(gestalt)
    if fehler:
        raise HTTPException(422, fehler)
    mand = _lesen_einer(lambda: f"SELECT pflichtteil FROM marketing.mandanten WHERE id = {lit(m)}")
    return _rendern(pult_render.BEISPIEL_FELDER, gestalt, (mand or {}).get("pflichtteil") or {}, fmt)


@router.post("/layouts/{name}/fassungen")
def layout_speichern(name: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    if not _NAME.match(name):
        raise HTTPException(404, "Unbekanntes Layout")
    gestalt = payload.get("gestalt")
    if not isinstance(gestalt, dict):
        raise HTTPException(422, "gestalt fehlt")
    von = str(payload.get("von") or "betreiber")
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_layout_speichern({lit(name)}, "
        f"{lit(json.dumps(gestalt, ensure_ascii=False))}::jsonb, "
        f"{lit(von)}) AS fassung")
    return {"fassung": int(zeile["fassung"])}


@router.post("/layouts/{name}/standard")
def layout_standard(name: str, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    if not _NAME.match(name):
        raise HTTPException(404, "Unbekanntes Layout")
    _schreiben(lambda: f"SELECT marketing.pult_layout_als_standard({lit(name)}) IS NULL AS ok")
    return {"ok": True}


# Medien-Verweise (Betreiber 01.10.2026: Bilder aus der Bibliothek loeschen).
# sales-ui fragt VOR dem Loeschen einer Datei, welche Inhalte sie noch tragen.
# Gesperrt ist, was die Datei in der NEUESTEN Fassung eines nicht abgelehnten
# Inhalts oder in der FREIGEGEBENEN (= versendbaren/versendeten) Fassung
# nutzt (Bloecke, Felder oder Collage-Seite); aeltere, ersetzte Fassungen
# sperren nicht. Gesucht wird der exakt gespeicherte JSON-Wert "medien:<name>" samt Anfuehrungszeichen, damit
# "a.jpg" nicht in "aa.jpg" trifft; ensure_ascii=False wie jsonb::text.
_MEDIEN_NAME = re.compile(r'^[^/\\"\x00-\x1f]{1,200}$')
_VERWEISE_SQL = (
    "SELECT i.id::text AS id, i.titel, i.status, i.art, 'aktuell' AS wo, i.mandant, n.format, n.fassung "
    "FROM marketing.inhalte i "
    "JOIN LATERAL (SELECT bloecke, felder, seite, format, fassung FROM marketing.inhalt_fassungen f "
    "WHERE f.inhalt = i.id ORDER BY fassung DESC LIMIT 1) n ON true "
    "WHERE i.status <> 'abgelehnt' "
    "AND (strpos(coalesce(n.bloecke::text, ''), {w}) > 0 OR strpos(coalesce(n.felder::text, ''), {w}) > 0 "
    "OR strpos(coalesce(n.seite::text, ''), {w}) > 0) "
    "UNION ALL "
    "SELECT i.id::text, i.titel, i.status, i.art, 'freigegeben', i.mandant, f.format, f.fassung "
    "FROM marketing.inhalte i "
    "JOIN marketing.inhalt_fassungen f ON f.inhalt = i.id AND f.fassung = i.freigegebene_fassung "
    "WHERE i.status = 'freigegeben' "
    "AND (strpos(coalesce(f.bloecke::text, ''), {w}) > 0 OR strpos(coalesce(f.felder::text, ''), {w}) > 0 "
    "OR strpos(coalesce(f.seite::text, ''), {w}) > 0) "
    "UNION ALL "
    "SELECT i.id::text, i.titel, i.status, i.art, "
    "CASE WHEN m.fassung = i.freigegebene_fassung AND i.status = 'freigegeben' THEN 'freigegeben' ELSE 'aktuell' END, "
    "i.mandant, 'seite', m.fassung "
    "FROM marketing.inhalte i "
    "JOIN marketing.seite_mailfassungen m ON m.inhalt = i.id "
    "WHERE m.medien @> jsonb_build_array(jsonb_build_object('name', {n}::text)) "
    "AND ((i.status <> 'abgelehnt' AND m.fassung = (SELECT max(f.fassung) FROM marketing.inhalt_fassungen f "
    "WHERE f.inhalt = i.id)) "
    "OR (i.status = 'eingereicht' AND m.fassung = i.eingereichte_fassung) "
    "OR (i.status = 'freigegeben' AND m.fassung = i.freigegebene_fassung))")


def medien_name_pruefen(name) -> str:
    """Dateiname einer Mediendatei pruefen (kein Pfad, keine Steuerzeichen) - sonst 422 ohne SQL."""
    if not isinstance(name, str) or not _MEDIEN_NAME.match(name) or name.strip(".") == "":
        raise HTTPException(422, "Unbekannter Dateiname")
    return name


def verweise_lesen(name: str, mandant: str | None) -> list[dict]:
    """Inhalte, deren neueste oder freigegebene Fassung "medien:<name>" exakt traegt, einmal je Inhalt.
    Je Eintrag id, titel, status, art, wo, format (bloecke|felder|seite), fassung und eigen. Mit mandant
    kommen Inhalte anderer Firmen ohne Titel (eigen False); ohne mandant ist eigen immer True
    (Altaufrufer). Der Name muss vorher geprueft sein (medien_name_pruefen)."""
    wert = json.dumps("medien:" + name, ensure_ascii=False)
    zeilen = _lesen(lambda: _VERWEISE_SQL.format(w=lit(wert), n=lit(name)))
    gesehen, verweise = set(), []
    for z in zeilen:
        if not isinstance(z, dict) or z.get("id") in gesehen:
            continue
        gesehen.add(z.get("id"))
        eigen = mandant is None or z.get("mandant") == mandant
        v = {k: z.get(k) for k in ("id", "titel", "status", "art", "wo", "format")}
        v["eigen"] = eigen
        f = z.get("fassung")
        v["fassung"] = int(f) if f is not None and str(f).isdigit() else None
        if not eigen:
            v["titel"] = ""
        verweise.append(v)
    return verweise


@router.get("/medien/verweise")
def medien_verweise(name: str = Query(""), mandant: str | None = None,
                    x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    medien_name_pruefen(name)
    m = None if mandant in (None, "") else _mandant_bekannt(mandant)
    return {"verweise": verweise_lesen(name, m)}
