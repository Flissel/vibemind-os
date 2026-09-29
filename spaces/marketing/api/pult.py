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

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from spaces.marketing.claw import bloecke_mjml, pult_render
from spaces.marketing.sync import _db

router = APIRouter(prefix="/api/pult")
_ARTEN = ("newsletter", "post", "material")
_STATUS = ("entwurf", "freigegeben", "abgelehnt")
_FORMATE = ("mail", "handy", "pdf")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
_VORLAGE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_BILD_BASIS = re.compile(r'^https://[^\s"\'<>]+/$')
_URHEBER = ("betreiber", "agent")
_NICHT_ERREICHBAR = "Marketing-Datenbank nicht erreichbar"
_UNGUELTIGE_EINGABE = "Ungueltige Eingabe (unzulaessiges Zeichen)"


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
    return treffer.group(1).strip().splitlines()[0][:300]


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


@router.get("/uebersicht")
def uebersicht(mandant: str | None = None, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant(mandant)
    mandanten = _lesen(lambda:
        "SELECT id, name, aktiv FROM marketing.mandanten ORDER BY aktiv DESC, name")
    zahlen = _lesen(lambda:
        f"SELECT status, count(*)::int AS n FROM marketing.inhalte WHERE mandant = {lit(m)} GROUP BY status")
    zaehler = {s: 0 for s in _STATUS}
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
        "     ORDER BY f.fassung DESC LIMIT 1) AS layout "
        f"FROM marketing.inhalte i WHERE {' AND '.join(wo)} ORDER BY i.erstellt_am DESC")
    return {"inhalte": zeilen}


def _bild_basis(wert: str | None) -> str:
    wert = wert or ""
    if wert and not _BILD_BASIS.match(wert):
        raise HTTPException(422, "bild_basis muss mit https:// beginnen, auf / enden und darf keine Anfuehrungszeichen oder Leerzeichen enthalten")
    return wert


def _bloecke_html(dok, betreff: str, vorschautext: str, pflichtteil: dict, fmt: str, bild_basis: str) -> HTMLResponse:
    if fmt == "pdf":
        raise HTTPException(422, "PDF gibt es fuer Editor-Newsletter noch nicht")
    try:
        return HTMLResponse(bloecke_mjml.rendern(
            dok, betreff, vorschautext, pflichtteil or {},
            bild_basis=bild_basis, handy=(fmt == "handy")))
    except bloecke_mjml.RenderFehler as e:
        raise HTTPException(422, str(e))


@router.post("/inhalte/aus_vorlage")
def inhalt_aus_vorlage(payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    vorlage = payload.get("vorlage")
    if not isinstance(vorlage, str) or not _VORLAGE.match(vorlage):
        raise HTTPException(422, "Unbekannte Vorlage")
    titel = payload.get("titel")
    if not isinstance(titel, str) or not titel.strip():
        raise HTTPException(422, "titel fehlt")
    m = _mandant(payload.get("mandant"))
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_inhalt_aus_vorlage({lit(vorlage)}, {lit(titel.strip())}, {lit(m)}) AS id")
    return {"id": str(zeile["id"])}


@router.get("/vorlagen")
def vorlagen(mandant: str | None = None, status: str | None = None,
             x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    m = _mandant(mandant)
    s = _auswahl(status, ("entwurf", "freigegeben"), "status")
    wo = [f"mandant = {lit(m)}"] + ([f"status = {lit(s)}"] if s else [])
    zeilen = _lesen(lambda:
        "SELECT name, beschreibung, status, fassung FROM marketing.newsletter_vorlagen "
        f"WHERE {' AND '.join(wo)} ORDER BY name")
    return {"vorlagen": zeilen}


@router.get("/vorlagen/{name}/vorschau")
def vorlage_vorschau(name: str, format: str = "mail", bild_basis: str = "",
                     mandant: str | None = None, x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    if not _VORLAGE.match(name):
        raise HTTPException(404, "Unbekannte Vorlage")
    fmt = _auswahl(format, ("mail", "handy"), "format") or "mail"
    basis = _bild_basis(bild_basis)
    m = _mandant(mandant)
    zeile = _lesen_einer(lambda:
        "SELECT v.bloecke, v.beschreibung, m.pflichtteil "
        "FROM marketing.newsletter_vorlagen v JOIN marketing.mandanten m ON m.id = v.mandant "
        f"WHERE v.name = {lit(name)} AND v.mandant = {lit(m)}")
    if not zeile:
        raise HTTPException(404, "Unbekannte Vorlage")
    return _bloecke_html(zeile["bloecke"], zeile.get("beschreibung") or "", "", zeile.get("pflichtteil"), fmt, basis)


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
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_bloecke_speichern({lit(i)}::uuid, {int(basis)}, {lit(betreff)}, "
        f"{lit(vorschautext)}, {lit(json.dumps(bloecke, ensure_ascii=False))}::jsonb, "
        f"{lit(urheber)}, {'true' if als_kopie else 'false'}) AS fassung")
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
        "CASE WHEN i.herkunft_proposal IS NULL THEN NULL "
        "ELSE jsonb_build_object('status', p.status, 'kanal', p.channel) END AS alter_weg "
        "FROM marketing.inhalte i "
        "LEFT JOIN marketing.broadcast_proposals p ON p.id = i.herkunft_proposal "
        f"WHERE i.id = {lit(i)}::uuid")
    if not kopf:
        raise HTTPException(404, "Unbekannter Inhalt")
    alter_weg = kopf.pop("alter_weg", None)
    fassungen = _lesen(lambda:
        "SELECT fassung, felder, layout, urheber, erstellt_am::text AS erstellt_am, format, bloecke "
        f"FROM marketing.inhalt_fassungen WHERE inhalt = {lit(i)}::uuid ORDER BY fassung DESC")
    return {"inhalt": kopf, "fassungen": fassungen, "alter_weg": alter_weg or None}


@router.post("/inhalte/{iid}/fassungen")
def fassung_speichern(iid: str, payload: dict = Body(...), x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    felder = payload.get("felder")
    if not isinstance(felder, dict):
        raise HTTPException(422, "felder fehlt")
    layout = str(payload.get("layout") or "")
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_fassung_speichern({lit(i)}::uuid, "
        f"{lit(json.dumps(felder, ensure_ascii=False))}::jsonb, "
        f"{lit(layout)}, 'betreiber') AS fassung")
    return {"fassung": int(zeile["fassung"])}


@router.get("/inhalte/{iid}/vorschau")
def vorschau(iid: str, fassung: int = Query(..., ge=1), format: str = "mail", bild_basis: str = "",
             x_pult_key: str | None = Header(None)):
    _schluessel(x_pult_key)
    i = _uuid_oder_404(iid)
    fmt = _auswahl(format, _FORMATE, "format")
    basis = _bild_basis(bild_basis)
    # Die Fassung zeigt das Layout in der Fassung, mit der sie gespeichert
    # wurde (layout_fassungen, unveraenderlich) - nicht das heutige Aussehen.
    # Fehlt der Pin (layout_fassung NULL), bleibt die aktuelle Gestalt. Die
    # zu rendernde Gestalt wird in DERSELBEN strengen Abfrage geprueft: eine
    # gespeicherte, ungueltige Gestalt ist 422 mit Grund, nie ein 500 und nie
    # ungeprueft im style-Attribut.
    gestalt_sql = "coalesce(lf.gestalt, l.gestalt)"
    zeile = _lesen_einer(lambda:
        f"SELECT f.felder, f.format, f.bloecke, {gestalt_sql} AS gestalt, m.pflichtteil, "
        f"CASE WHEN f.format = 'felder' THEN marketing.pult_gestalt_fehler({gestalt_sql}) END AS gestalt_fehler "
        "FROM marketing.inhalt_fassungen f "
        "JOIN marketing.inhalte i ON i.id = f.inhalt "
        "JOIN marketing.mandanten m ON m.id = i.mandant "
        "LEFT JOIN marketing.layout_vorlagen l ON l.name = f.layout "
        "LEFT JOIN marketing.layout_fassungen lf ON lf.layout = f.layout AND lf.fassung = f.layout_fassung "
        f"WHERE f.inhalt = {lit(i)}::uuid AND f.fassung = {int(fassung)}")
    if not zeile:
        raise HTTPException(404, "Unbekannte Fassung")
    if zeile.get("format") == "bloecke":
        felder = zeile.get("felder") or {}
        return _bloecke_html(zeile["bloecke"], felder.get("betreff", ""), felder.get("vorschautext", ""),
                             zeile.get("pflichtteil"), fmt, basis)
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
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_entscheiden({lit(i)}::uuid, {f}, {lit(urteil)}, "
        f"{lit(von)}, {lit(grund)}) AS status")
    return {"status": zeile["status"]}


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
