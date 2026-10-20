"""Mail-Fassung einer Collage (sales-claw Plan 2026-10-20-freie-flaeche-stufe4-mail-versand, Task 4): einmal je
(Inhalt, Fassung) erzeugt und in marketing.seite_mailfassungen (081) gespeichert. Einreichen, Versand und die
Mail-Vorschau einer Collage lesen sie von dort.

erzeugen: Anrede-im-Bild-Pruefung, Bildstuecke rendern (headless Chromium ueber seite_bilder, Medien nur aus
MARKETING_MEDIEN_ORDNER und MARKETING_BILD_ORDNER), zuschneiden, als ms-<inhalt 8 hex>-<fassung>-<nr>.jpg in der
Ablage (MARKETING_BILD_ORDNER) ablegen und der Firma zuordnen wie die Export-Dateien (chat._ablegen_und_zuordnen,
Temp-Datei + atomarer Link, nie ueberschreiben), dann Versand- und Vorschau-MJML und Textfassung bauen und
speichern. Die signierte Bildbasis der sales-ui laeuft ab und wird nie gespeichert: das Vorschau-MJML traegt
BILD_MARKE. Gespeichert wird MJML ohne Pflicht-Fuss (FUSS_MARKE); versand und vorschau setzen je Anfrage den
AKTUELLEN Pflichtteil der Firma (und die Bildbasis) ein und rendern mit mrml - der Fuss ist immer aktuell, die
Textfassung wird dafuer ebenfalls je Anfrage gebaut (Fix-Runde 1).

Inhaltliche Fehler beim Erzeugen (Anrede im Bild, Seite ungueltig, Bild fehlt, zu gross) haengen nur an der
unveraenderlichen Fassung: sie werden je (Inhalt, Fassung) FEHLER_MERKEN_S im Prozess gemerkt und ohne neues
Rendern als dieselbe 422 gemeldet. Das Zeitlimit zaehlt nicht dazu.

Gleichzeitige Anfragen fuer dieselbe Fassung: jede legt eigene Dateien ab (ein vergebener Name bekommt -2, -3 ...),
die DB nimmt mit ON CONFLICT DO NOTHING nur die erste Zeile; wer verliert, loescht seine Dateien und liefert die
gespeicherte Zeile. Scheitert etwas nach dem Ablegen, werden die eben abgelegten Dateien geloescht.

Eine gespeicherte Mail-Fassung gilt nur, solange alle ihre ms-Dateien in der Ablage liegen; fehlt eine, wird
die Zeile verworfen (pult_seite_mail_verwerfen) und die Mail-Fassung neu erzeugt. Erzeugt wird unter einer Sperre
(hoechstens eine Erzeugung gleichzeitig, Warten hoechstens ERZEUG_WARTEN_S, sonst 503); wer warten musste, liest
die Fassung danach neu und nimmt eine inzwischen erzeugte Mail-Fassung, statt doppelt zu rendern.

aufraeumen (stuendlich ueber gestaltung.aufraeumen_falls_faellig): pult_seite_mail_aufraeumen (081) loescht die
Zeilen alter, nicht mehr gebrauchter Mail-Fassungen, erst danach werden ihre ms-Dateien geloescht. Danach gehen
verwaiste ms-Dateien (aelter als VERWAIST_ALTER_S, von keiner Mail-Fassung mehr verwiesen - z. B. nach dem
Loeschen einer Collage) weg; welche noch verwiesen sind, sagt pult_seite_mail_verwiesen.

Reihenfolge beim Erzeugen (Fix nach der Live-Abnahme): erst die deklarierte Anrede-/Link-Pruefung (billig, ohne
Chromium), dann rendern UND messen (seite_bilder.render_und_messen), dann mit den gemessenen Ebenen-Huellen planen
(Anrede/Link erneut, Stuecke), zuschneiden und das MJML mit denselben Massen bauen. Text waechst in seite_html
ueber seine deklarierte hoehe hinaus; ein Plan aus der deklarierten Hoehe schnitt einen gedrehten zweizeiligen
Titel ab. In der DB steht nichts Neues - das gespeicherte MJML spiegelt den gemessenen Plan. Gerendert wird jetzt
auch fuer reine Textseiten (die Abstaende der Textbaender kommen aus der Messung); nur eine leere Seite (ohne
Ebenen und Hintergrundbild) braucht kein Chromium. Aendert sich seite_mail, werden alle gespeicherten Mail-Fassungen verworfen
(scripts/seite_mail_neu.py) - sie entstehen bei Bedarf neu.

Synchron (Chromium im Aufrufer-Thread, hoechstens einer gleichzeitig - seite_bilder sperrt): die Routen sind
sync def und laufen damit im Threadpool von FastAPI, wie der PDF-Druck.

Pruefung (Stufe 5, pruefung): Schoenheitspruefung einer Collage-Fassung vor dem Einreichen. Rendert einmal voll
(mit Massen, seite_bilder.render_und_messen) und - nur wenn eine Textebene ohne eigene Unterlage die Pixel braucht -
einmal ohne Text- und Knopfebenen (seite_schoenheit.seite_ohne_text), unter derselben Sperre wie die Erzeugung
(_erzeug_sperre, Warten hoechstens ERZEUG_WARTEN_S) und mit den Zeitlimits von seite_bilder. Befunde:
Anrede/Link im Bildbereich (wie Stufe 4, gemessen) zuerst, dann seite_schoenheit.pruefen. Das Ergebnis liegt je
(Inhalt, Fassung) PRUEFUNG_MERKEN_S im Speicher (Fassungen sind unveraenderlich). Technischer Fehler (Chromium weg,
Zeitlimit, Ablage fehlt, Sperre belegt) -> 503 (nie gemerkt); ein fehlendes Bild oder eine ungueltige Seite -> 422.
Die Route (pruefung) zeichnet Platzhalter als neutrale Kacheln (wie das Pruefbild); Text ueber einem Platzhalter
bekommt keinen Pixel-Kontrast, dafuer den weichen Hinweis BILDER_IN_ARBEIT.

Einreichen (einreichen_vorbereiten, Fix-Runde 1): Pruefung und Mail-Fassung unter EINER Sperre mit EINEM vollen
Rendering (plus hoechstens einem ohne Text); das volle Rendering geht als (png, masse) in _neu, die Erzeugung rendert
nicht noch einmal. Zeitlimit/belegte Sperre -> 503 sofort; ein anderer technischer Fehler der Pruefung -> weiter wie
Stufe 4 (_holen). Seiten mit Platzhaltern werden nicht geprueft, die Erzeugung blockiert sie wie in Stufe 4."""
from __future__ import annotations

import html as _html
import json
import logging
import os
import re
import threading
import time
import urllib.parse

from fastapi import HTTPException

from spaces.marketing.api.bilder import _ordner
from spaces.marketing.api.chat import _ablegen_und_zuordnen
from spaces.marketing.api.pult import COLLAGE_IST_NICHT, _lesen_einer, _medien_datei, _schreiben, lit
from spaces.marketing.claw import bildplaetze, bloecke_mjml, seite, seite_bilder, seite_mail, seite_schoenheit
from spaces.marketing.claw.schoenheit import befund

log = logging.getLogger(__name__)

ANREDE_IM_BILD = "Die Anrede steht in einem Bildbereich – sie muss in frei stehendem Fließtext stehen"
LINK_IM_BILD = "Ein Link steht in einem Bildbereich – er muss in frei stehendem Fließtext stehen"
STUECKE_FEHLEN = "Bildstücke fehlen – bitte Mail-Vorschau neu erzeugen"
BESCHAEFTIGT = "Gerade wird eine andere Mail-Fassung erzeugt – bitte gleich noch einmal versuchen"
ERZEUG_WARTEN_S = 90.0         # Warten auf eine laufende Erzeugung (Chromium hat selbst bis zu 60 s Zeitlimit)
VERWAIST_ALTER_S = 86400       # verwaiste ms-Dateien erst nach einem Tag loeschen (nie eine eben gerenderte)
_VERWIESEN_JE_ABFRAGE = 500
NICHT_GERENDERT = "Bildstücke konnten nicht gerendert werden"
BILDER_IN_ARBEIT = "Bilder werden noch erzeugt – Kontrast wird danach geprüft"
# Platzhalter-Adresse der Bildstuecke im gespeicherten Vorschau-MJML (keine Anfrage erreicht sie je: .invalid)
BILD_MARKE = "https://bildbasis.vibemind.invalid/"
# Platz des Pflicht-Fusses im gespeicherten MJML (Nutzertext wird escaped und kann die Marke nie bilden)
FUSS_MARKE = "<!--vibemind:pflichtfuss-->"
FEHLER_MERKEN_S = 600
PRUEFUNG_MERKEN_S = 600
PRUEFUNG_FAKTOR = 1            # Rendering ohne Text: nur fuer den Median der Unterlage, 1 Pixel je Einheit reicht
_MS_NAME = re.compile(r"ms-[0-9a-f]{8}-[0-9]{1,6}-[0-9]{2,3}(?:-[0-9]{1,3})?\.jpg")
_fehler: dict[tuple[str, int], tuple[float, str]] = {}
_fehler_sperre = threading.Lock()
_erzeug_sperre = threading.Lock()   # hoechstens eine Erzeugung gleichzeitig (mit Neu-Lesen nach dem Warten)
_pruefungen: dict[tuple[str, int], tuple[float, list[dict]]] = {}
_pruefungen_sperre = threading.Lock()


def _fehler_gemerkt(i: str, fassung: int) -> str | None:
    with _fehler_sperre:
        eintrag = _fehler.get((i, int(fassung)))
        if eintrag and time.monotonic() - eintrag[0] < FEHLER_MERKEN_S:
            return eintrag[1]
        _fehler.pop((i, int(fassung)), None)
        return None


def _fehler_merken(i: str, fassung: int, grund: str) -> HTTPException:
    with _fehler_sperre:
        jetzt = time.monotonic()
        for k in [k for k, (t, _) in _fehler.items() if jetzt - t >= FEHLER_MERKEN_S]:
            del _fehler[k]
        _fehler[(i, int(fassung))] = (jetzt, grund)
    return HTTPException(422, grund)


def _fassung_lesen(i: str, fassung: int) -> dict:
    zeile = _lesen_einer(lambda:
        "SELECT f.format, f.seite, f.felder, m.pflichtteil, "
        "marketing.pult_seite_mail_lesen(f.inhalt, f.fassung) AS mail "
        "FROM marketing.inhalt_fassungen f JOIN marketing.inhalte i ON i.id = f.inhalt "
        "JOIN marketing.mandanten m ON m.id = i.mandant "
        f"WHERE f.inhalt = {lit(i)}::uuid AND f.fassung = {int(fassung)}")
    if not zeile:
        raise HTTPException(404, "Unbekannte Fassung")
    if zeile.get("format") != "seite":
        raise HTTPException(422, COLLAGE_IST_NICHT)
    return zeile


def _medien_leser(ordner: str):
    def lesen(name: str) -> bytes | None:
        pfad = _medien_datei("medien:" + name, ordner)
        if pfad is None:
            return None
        with open(pfad, "rb") as f:
            return f.read()
    return lesen


def _wegraeumen(ordner: str, namen: list[str]) -> None:
    for n in namen:
        try:
            os.remove(os.path.join(ordner, n))
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("Bildstueck %s liess sich nicht entfernen", n)


def _bild_fehler(i: str, fassung: int, aufruf):
    """aufruf() ausfuehren; Render-/Zuschnittfehler als HTTPException (Inhaltsfehler gemerkt, Zeitlimit nicht)."""
    try:
        return aufruf()
    except seite_bilder.StueckFehler as e:
        if str(e) == seite_bilder.ZEITLIMIT:      # kein Inhaltsfehler: beim naechsten Mal neu versuchen
            raise HTTPException(422, str(e))
        raise _fehler_merken(i, fassung, str(e))
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - Chromium/Playwright fehlt oder bricht ab: nie den Rohtext nach aussen
        log.warning("Bildstuecke der Collage-Mail nicht gerendert", exc_info=True)
        raise HTTPException(503, NICHT_GERENDERT)


def _stuecke_da(mail: dict) -> bool:
    """Liegen alle ms-Dateien der Mail-Fassung in der Ablage? (Ohne Stuecke: ja, ohne Ablage zu fragen.)"""
    namen = [m.get("name") if isinstance(m, dict) else None for m in mail.get("medien") or []]
    if not namen:
        return True
    ordner = _ordner()
    return all(isinstance(n, str) and _MS_NAME.fullmatch(n) and os.path.isfile(os.path.join(ordner, n))
               for n in namen)


def _brauchbar(zeile: dict) -> dict | None:
    mail = zeile.get("mail")
    return mail if isinstance(mail, dict) and _stuecke_da(mail) else None


def _verwerfen(i: str, fassung: int) -> None:
    """Mail-Fassung mit fehlenden Stuecken verwerfen (DB zuerst), dann ihre restlichen ms-Dateien."""
    log.warning("Bildstuecke der Mail-Fassung %s/%s fehlen - sie wird neu erzeugt", i[:8], int(fassung))
    zeile = _schreiben(lambda:
        f"SELECT marketing.pult_seite_mail_verwerfen({lit(i)}::uuid, {int(fassung)}) AS namen")
    namen = [n for n in (zeile.get("namen") or []) if isinstance(n, str) and _MS_NAME.fullmatch(n)]
    if namen:
        _wegraeumen(_ordner(), namen)


def _holen(i: str, fassung: int) -> tuple[dict, dict]:
    """(Fassungszeile, Mail-Fassung) - die vorhandene (mit allen Stuecken in der Ablage), sonst neu erzeugt und
    gespeichert. Erzeugt wird unter _erzeug_sperre; wer warten musste, liest danach neu."""
    zeile = _fassung_lesen(i, fassung)
    mail = _brauchbar(zeile)
    if mail is not None:
        return zeile, mail
    gemerkt = _fehler_gemerkt(i, fassung)
    if gemerkt is not None:
        raise HTTPException(422, gemerkt)
    gewartet = not _erzeug_sperre.acquire(blocking=False)
    if gewartet and not _erzeug_sperre.acquire(timeout=ERZEUG_WARTEN_S):
        raise HTTPException(503, BESCHAEFTIGT)
    try:
        if gewartet:          # inzwischen erzeugt (oder gescheitert)? dann nicht doppelt rendern
            zeile = _fassung_lesen(i, fassung)
            mail = _brauchbar(zeile)
            if mail is not None:
                return zeile, mail
            gemerkt = _fehler_gemerkt(i, fassung)
            if gemerkt is not None:
                raise HTTPException(422, gemerkt)
        if isinstance(zeile.get("mail"), dict):    # gespeichert, aber Stuecke fehlen in der Ablage
            _verwerfen(i, fassung)
        return zeile, _neu(i, fassung, zeile)
    finally:
        _erzeug_sperre.release()


def erzeugen(i: str, fassung: int) -> dict:
    """Mail-Fassung der Collage-Fassung (inhalt, fassung) - die vorhandene, sonst neu erzeugt und gespeichert.
    Rueckgabe wie marketing.pult_seite_mail_lesen: inhalt, fassung, mjml_versand, html_vorschau_mjml, text,
    medien, bytes, erstellt_am. Fehler als HTTPException (422 mit deutschem Grund, 404, 503)."""
    return _holen(i, fassung)[1]


def _plan_pruefen(i: str, fassung: int, s, masse) -> list:
    """Anrede/Link im Bildbereich blockieren, sonst die Stuecke (alles mit denselben masse)."""
    try:
        if seite_mail.anrede_in_bild(s, masse):
            raise _fehler_merken(i, fassung, ANREDE_IM_BILD)
        if seite_mail.links_in_bild(s, masse):
            raise _fehler_merken(i, fassung, LINK_IM_BILD)
        return seite_mail.stuecke(s, masse)
    except seite.SeiteFehler as e:
        raise _fehler_merken(i, fassung, str(e))


def _neu(i: str, fassung: int, zeile: dict, gerendert: tuple[bytes, dict] | None = None) -> dict:
    """gerendert: (png, masse) eines schon gemachten vollen Renderings derselben Seite (Einreichen nach der
    Pruefung, ohne Platzhalter-Kacheln) - dann rendert die Erzeugung nicht noch einmal."""
    s = zeile.get("seite")
    # 1. deklariert pruefen (billig, ohne Chromium): gemessene Huellen sind nie kleiner als die deklarierten, was
    #    hier im Bild liegt, liegt auch gemessen im Bild
    deklariert = _plan_pruefen(i, fassung, s, None)
    namen: list[str] = []
    ordner = ""
    groesse = 0
    masse = None
    if deklariert or s.get("ebenen"):   # nur eine leere Seite ohne Hintergrundbild braucht kein Chromium
        # 2. rendern und messen, 3. mit den Massen planen, 4. zuschneiden
        def medium(name: str) -> bytes | None:
            # Ablage (MARKETING_BILD_ORDNER) erst beim ersten gelesenen Medium aufloesen: eine reine Textseite
            # wird nur gemessen und braucht keine Ablage. Fehlt sie, wirft _ordner() 503 (HTTPException) und
            # _bild_fehler reicht das unveraendert durch.
            return _medien_leser(_ordner())(name)
        if gerendert is not None:
            png, masse = gerendert
        else:
            png, masse = _bild_fehler(i, fassung, lambda: seite_bilder.render_und_messen(s, medium))
        stuecke = _plan_pruefen(i, fassung, s, masse)
        if stuecke:
            ordner = _ordner()
            jpgs = _bild_fehler(i, fassung, lambda: seite_bilder.zuschneiden(png, stuecke))
            namen = _ablegen_und_zuordnen(
                ordner, i, [(f"ms-{i[:8]}-{int(fassung)}-{st.nr:02d}", roh) for st, roh in zip(stuecke, jpgs)])
            groesse = sum(len(roh) for roh in jpgs)
    try:
        gespeichert = _speichern(i, fassung, zeile, s, namen, groesse, masse)
    except BaseException:
        _wegraeumen(ordner, namen)
        raise
    eigene = [n for n in namen if n not in {m.get("name") for m in gespeichert.get("medien") or []}]
    if eigene:   # gleichzeitig erzeugt, die andere Anfrage war zuerst gespeichert
        _wegraeumen(ordner, eigene)
    return gespeichert


def _speichern(i: str, fassung: int, zeile: dict, s: dict, namen: list[str], groesse: int,
               masse: dict | None = None) -> dict:
    felder = zeile.get("felder") or {}
    betreff, vorschautext = str(felder.get("betreff") or ""), str(felder.get("vorschautext") or "")
    pflicht = zeile.get("pflichtteil") or {}
    try:
        versand_mjml = seite_mail.mjml(s, namen, betreff=betreff, vorschautext=vorschautext, pflichtteil=pflicht,
                                       bild_adresse=lambda n: bloecke_mjml.CID + urllib.parse.quote(n),
                                       fuer_versand=True, fuss_ersatz=FUSS_MARKE, masse=masse)
        vorschau_mjml = seite_mail.mjml(s, namen, betreff=betreff, vorschautext=vorschautext, pflichtteil=pflicht,
                                        bild_adresse=lambda n: BILD_MARKE + urllib.parse.quote(n),
                                        fuer_versand=False, fuss_ersatz=FUSS_MARKE, masse=masse)
        _rendern(versand_mjml, s, pflicht, True)       # setzt mrml es nicht, wird nichts gespeichert
        text = seite_mail.text_fassung(s, pflicht, masse)
    except (bloecke_mjml.RenderFehler, seite.SeiteFehler) as e:
        raise HTTPException(422, str(e))
    medien = seite_mail.versand_medien(namen)
    ergebnis = _schreiben(lambda:
        f"SELECT marketing.pult_seite_mail_speichern({lit(i)}::uuid, {int(fassung)}, {lit(versand_mjml)}, "
        f"{lit(vorschau_mjml)}, {lit(text)}, {lit(json.dumps(medien, ensure_ascii=False))}::jsonb, "
        f"{int(groesse)}) AS mail")
    mail = ergebnis.get("mail")
    if not isinstance(mail, dict):
        raise HTTPException(503, "Marketing-Datenbank nicht erreichbar")
    return mail


def _rendern(quelle: str, s: dict, pflicht: dict, fuer_versand: bool) -> str:
    """Gespeichertes MJML mit dem aktuellen Pflicht-Fuss -> HTML (wirft RenderFehler/SeiteFehler)."""
    return bloecke_mjml.mjml_zu_html(quelle.replace(FUSS_MARKE, seite_mail.fuss(s, pflicht, fuer_versand)))


def _setzen(quelle: str, s: dict, pflicht: dict, fuer_versand: bool) -> str:
    try:
        return _rendern(quelle, s, pflicht, fuer_versand)
    except (bloecke_mjml.RenderFehler, seite.SeiteFehler) as e:
        raise HTTPException(422, str(e))


def versand(i: str, fassung: int) -> dict:
    """html (cid:, Platzhalter bleiben), text und medien fuer den Versand - Fuss und Text mit dem aktuellen
    Pflichtteil der Firma."""
    zeile, mail = _holen(i, fassung)
    if not _stuecke_da(mail):        # nie eine Mail mit fehlenden eingebetteten Bildern herausgeben
        raise HTTPException(422, STUECKE_FEHLEN)
    s, pflicht = zeile.get("seite"), zeile.get("pflichtteil") or {}
    html = _setzen(str(mail.get("mjml_versand") or ""), s, pflicht, True)
    try:
        text = seite_mail.text_fassung(s, pflicht)
    except seite.SeiteFehler as e:
        raise HTTPException(422, str(e))
    return {"html": html, "text": text, "medien": mail.get("medien") or []}


def vorschau(i: str, fassung: int, bild_basis: str) -> str:
    """Mail-HTML der Vorschau mit der Bildbasis dieser Anfrage (geprueft von pult._bild_basis) und dem aktuellen
    Pflicht-Fuss; Platzhalter als [Vorname]/[Abmeldelink]."""
    zeile, mail = _holen(i, fassung)
    return vorschau_html(zeile, mail, bild_basis)


def vorschau_html(zeile: dict, mail: dict, bild_basis: str) -> str:
    quelle = str(mail.get("html_vorschau_mjml") or "").replace(BILD_MARKE,
                                                                _html.escape(bild_basis or "", quote=True))
    return _setzen(quelle, zeile.get("seite"), zeile.get("pflichtteil") or {}, False)


def aufraeumen() -> list[str]:
    """Alte, nicht mehr gebrauchte Mail-Fassungen loeschen (DB zuerst, dann die ms-Dateien in der Ablage).
    Wirft HTTPException (DB/Ablage nicht erreichbar) - der Aufrufer schluckt das."""
    zeile = _schreiben(lambda: "SELECT marketing.pult_seite_mail_aufraeumen() AS namen")
    namen = [n for n in (zeile.get("namen") or []) if isinstance(n, str) and _MS_NAME.fullmatch(n)]
    ordner = _ordner()
    if namen:
        _wegraeumen(ordner, namen)
    return namen + _waisen_aufraeumen(ordner, time.time())


def _waisen_aufraeumen(ordner: str, jetzt: float) -> list[str]:
    """ms-Dateien, die aelter als VERWAIST_ALTER_S sind und auf die keine Mail-Fassung mehr verweist (z. B. nach
    dem Loeschen der Collage, CASCADE). Ein DB-Fehler wirft (HTTPException) - dann wird nichts geloescht."""
    kandidaten = []
    for n in sorted(os.listdir(ordner)):
        if not _MS_NAME.fullmatch(n):
            continue
        try:
            if jetzt - os.path.getmtime(os.path.join(ordner, n)) >= VERWAIST_ALTER_S:
                kandidaten.append(n)
        except OSError:
            continue
    weg: list[str] = []
    for k in range(0, len(kandidaten), _VERWIESEN_JE_ABFRAGE):
        teil = kandidaten[k:k + _VERWIESEN_JE_ABFRAGE]
        zeile = _lesen_einer(lambda: "SELECT marketing.pult_seite_mail_verwiesen("
                                     f"{lit(json.dumps(teil))}::jsonb) AS namen")
        verwiesen = (zeile or {}).get("namen")
        if not isinstance(verwiesen, list):      # unklare Antwort: lieber nichts loeschen
            return weg
        frei = [n for n in teil if n not in set(verwiesen)]
        _wegraeumen(ordner, frei)
        weg += frei
    return weg


# --- Schoenheitspruefung (Stufe 5) -----------------------------------------------------------------------------


def _pruefung_gemerkt(i: str, fassung: int) -> list[dict] | None:
    with _pruefungen_sperre:
        eintrag = _pruefungen.get((i, int(fassung)))
        if eintrag and time.monotonic() - eintrag[0] < PRUEFUNG_MERKEN_S:
            return [dict(b) for b in eintrag[1]]
        _pruefungen.pop((i, int(fassung)), None)
        return None


def _pruefung_merken(i: str, fassung: int, befunde: list[dict]) -> None:
    with _pruefungen_sperre:
        jetzt = time.monotonic()
        for k in [k for k, (t, _) in _pruefungen.items() if jetzt - t >= PRUEFUNG_MERKEN_S]:
            del _pruefungen[k]
        _pruefungen[(i, int(fassung))] = (jetzt, [dict(b) for b in befunde])


def _pruef_render(aufruf):
    """aufruf() (Chromium) ausfuehren: fehlendes Bild -> 422 mit Grund, Zeitlimit und jeder andere Fehler -> 503."""
    try:
        return aufruf()
    except seite_bilder.StueckFehler as e:
        if str(e) == seite_bilder.ZEITLIMIT:
            raise HTTPException(503, str(e))
        raise HTTPException(422, str(e))
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - Chromium/Playwright fehlt oder bricht ab: nie den Rohtext nach aussen
        log.warning("Pruefbild der Collage nicht gerendert", exc_info=True)
        raise HTTPException(503, NICHT_GERENDERT)


def _medium(name: str) -> bytes | None:
    return _medien_leser(_ordner())(name)   # Ablage erst beim ersten Medium (wie _neu)


def _braucht_render(s: dict) -> bool:
    """Wie _neu: nur eine leere Seite ohne Hintergrundbild braucht kein Chromium."""
    return bool(s.get("ebenen")) or bool((s.get("hintergrund") or {}).get("bild"))


def _hat_platzhalter(s: dict) -> bool:
    return bool(seite_schoenheit.platzhalter_ids(s)) or bool(
        bildplaetze.PLATZHALTER.match(str((s.get("hintergrund") or {}).get("bild") or "")))


def _rendern_fuer_pruefung(roh: dict, s: dict, kacheln: bool) -> tuple[bytes | None, dict, bytes, set[str]]:
    """(png, masse, png_ohne_text, ohne_pixel). roh = die Seite, wie _neu sie rendert (das volle Rendering wird
    beim Einreichen fuer die Mail wiederverwendet), s = dieselbe Seite normalisiert. Hoechstens ein volles
    Rendering und - nur wenn eine Textebene ohne eigene Unterlage die Pixel braucht - eines ohne Text."""
    if not _braucht_render(s):
        return None, {}, b"", set()
    extra = {"platzhalter_kacheln": True} if kacheln else {}
    png, masse = _pruef_render(lambda: seite_bilder.render_und_messen(roh, _medium, **extra))
    ohne_pixel = seite_schoenheit.ohne_pixel(s, masse)
    png_ohne_text = b""
    if seite_schoenheit.braucht_pixel(s, ohne_pixel):
        ohne = seite_schoenheit.seite_ohne_text(roh)
        png_ohne_text = _pruef_render(lambda: seite_bilder.render_und_messen(
            ohne, _medium, faktor=PRUEFUNG_FAKTOR, **extra)[0])
    return png, masse, png_ohne_text, ohne_pixel


def _befunde(s: dict, masse: dict, png_ohne_text: bytes, ohne_pixel: set[str]) -> list[dict]:
    m = masse or None
    befunde: list[dict] = []
    anrede = seite_mail.anrede_in_bild(s, m)
    if anrede:
        befunde.append(befund("hart", anrede[0], ANREDE_IM_BILD))
    links = seite_mail.links_in_bild(s, m)
    if links:
        befunde.append(befund("hart", links[0], LINK_IM_BILD))
    befunde += seite_schoenheit.pruefen(s, masse, png_ohne_text, PRUEFUNG_FAKTOR, ohne_pixel)
    if ohne_pixel:
        befunde.append(befund("weich", "seite", BILDER_IN_ARBEIT))
    return befunde


def _normalisiert(roh) -> dict:
    try:
        return seite.pruefen(roh)
    except seite.SeiteFehler as e:
        raise HTTPException(422, str(e))


def _sperre_nehmen() -> bool:
    """_erzeug_sperre nehmen (Warten hoechstens ERZEUG_WARTEN_S, sonst 503 BESCHAEFTIGT); True = musste warten."""
    gewartet = not _erzeug_sperre.acquire(blocking=False)
    if gewartet and not _erzeug_sperre.acquire(timeout=ERZEUG_WARTEN_S):
        raise HTTPException(503, BESCHAEFTIGT)
    return gewartet


def pruefung(i: str, fassung: int) -> list[dict]:
    """Befunde {schwere, punkt, satz} der Collage-Fassung (inhalt, fassung), harte zuerst (Anrede/Link im Bild vor
    den Kontrasten). Platzhalter werden als neutrale Kacheln gerendert; Text darueber bekommt keinen Pixel-
    Kontrast, dafuer einen weichen Hinweis. 404 unbekannte Fassung, 422 keine Collage / Seite ungueltig / Bild
    fehlt, 503 technisch."""
    gemerkt = _pruefung_gemerkt(i, fassung)
    if gemerkt is not None:
        return gemerkt
    zeile = _lesen_einer(lambda:
        f"SELECT format, seite FROM marketing.inhalt_fassungen WHERE inhalt = {lit(i)}::uuid "
        f"AND fassung = {int(fassung)}")
    if not zeile:
        raise HTTPException(404, "Unbekannte Fassung")
    if zeile.get("format") != "seite":
        raise HTTPException(422, COLLAGE_IST_NICHT)
    s = _normalisiert(zeile.get("seite"))
    _sperre_nehmen()
    try:
        _, masse, png_ohne_text, ohne_pixel = _rendern_fuer_pruefung(zeile.get("seite"), s, kacheln=True)
        befunde = _befunde(s, masse, png_ohne_text, ohne_pixel)
    except seite.SeiteFehler as e:
        raise HTTPException(422, str(e))
    finally:
        _erzeug_sperre.release()
    _pruefung_merken(i, fassung, befunde)
    return befunde


def _ablehnen_wenn_hart(befunde: list[dict]) -> None:
    grund = harte_ablehnung(befunde)
    if grund:
        raise HTTPException(422, grund)


def einreichen_vorbereiten(i: str, fassung: int) -> dict:
    """Vor dem Einreichen einer Collage-Fassung: Schoenheitspruefung und Mail-Fassung unter EINER Sperre mit
    EINEM vollen Rendering (plus hoechstens einem ohne Text), das die Erzeugung der Mail wiederverwendet.
    Harte Befunde -> eine 422 (erster Satz, "(weitere: n)"). Gibt die Mail-Fassung zurueck.
    - Pruefung schon gemerkt: nur ihr Urteil, dann der Weg aus Stufe 4 (vorhandene Mail oder ein Rendering).
    - Seite mit Platzhaltern: keine Pruefung - die Erzeugung blockiert wie in Stufe 4 ("Bild ... fehlt").
    - Zeitlimit oder belegte Sperre: 503 sofort, kein zweiter Versuch (das Zeitbudget der Anfrage ist verbraucht).
    - Anderer technischer Fehler der Pruefung (Chromium weg): weiter wie in Stufe 4 - die Erzeugung braucht
      Chromium ohnehin und meldet dann selbst 503; ein Inhalt wird nie wegen der Technik der Pruefung blockiert."""
    gemerkt = _pruefung_gemerkt(i, fassung)
    if gemerkt is not None:
        _ablehnen_wenn_hart(gemerkt)
        return _holen(i, fassung)[1]
    zeile = _fassung_lesen(i, fassung)
    mail = _brauchbar(zeile)
    if mail is None:
        fehler = _fehler_gemerkt(i, fassung)
        if fehler is not None:
            raise HTTPException(422, fehler)
    s = _normalisiert(zeile.get("seite"))
    if _hat_platzhalter(s):
        return _holen(i, fassung)[1]
    gewartet = _sperre_nehmen()
    try:
        if gewartet:           # inzwischen kann die Mail-Fassung entstanden sein
            zeile = _fassung_lesen(i, fassung)
            mail = _brauchbar(zeile)
        try:
            png, masse, png_ohne_text, ohne_pixel = _rendern_fuer_pruefung(zeile.get("seite"), s, kacheln=False)
            befunde = _befunde(s, masse, png_ohne_text, ohne_pixel)
        except seite.SeiteFehler as e:
            raise HTTPException(422, str(e))
        except HTTPException as e:
            if e.status_code != 503 or e.detail in (seite_bilder.ZEITLIMIT, BESCHAEFTIGT):
                raise
            log.warning("Pruefung vor dem Einreichen von %s nicht moeglich - weiter wie Stufe 4", i[:8])
            befunde = None
        if befunde is not None:
            _pruefung_merken(i, fassung, befunde)
            _ablehnen_wenn_hart(befunde)
            if mail is not None:
                return mail
            if isinstance(zeile.get("mail"), dict):    # gespeichert, aber Stuecke fehlen in der Ablage
                _verwerfen(i, fassung)
            return _neu(i, fassung, zeile, gerendert=(png, masse) if png is not None else None)
    finally:
        _erzeug_sperre.release()
    return _holen(i, fassung)[1]


def harte_ablehnung(befunde: list[dict]) -> str | None:
    """Text der 422 beim Einreichen: der erste harte Satz, bei mehreren mit "(weitere: n)"; None ohne harte."""
    harte = [b for b in befunde if b.get("schwere") == "hart"]
    if not harte:
        return None
    rest = len(harte) - 1
    return harte[0]["satz"] + (f" (weitere: {rest})" if rest else "")
