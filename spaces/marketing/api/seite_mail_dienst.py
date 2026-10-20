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
sync def und laufen damit im Threadpool von FastAPI, wie der PDF-Druck."""
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
from spaces.marketing.claw import bloecke_mjml, seite, seite_bilder, seite_mail

log = logging.getLogger(__name__)

ANREDE_IM_BILD = "Die Anrede steht in einem Bildbereich – sie muss in frei stehendem Fließtext stehen"
LINK_IM_BILD = "Ein Link steht in einem Bildbereich – er muss in frei stehendem Fließtext stehen"
STUECKE_FEHLEN = "Bildstücke fehlen – bitte Mail-Vorschau neu erzeugen"
BESCHAEFTIGT = "Gerade wird eine andere Mail-Fassung erzeugt – bitte gleich noch einmal versuchen"
ERZEUG_WARTEN_S = 90.0         # Warten auf eine laufende Erzeugung (Chromium hat selbst bis zu 60 s Zeitlimit)
VERWAIST_ALTER_S = 86400       # verwaiste ms-Dateien erst nach einem Tag loeschen (nie eine eben gerenderte)
_VERWIESEN_JE_ABFRAGE = 500
NICHT_GERENDERT = "Bildstücke konnten nicht gerendert werden"
# Platzhalter-Adresse der Bildstuecke im gespeicherten Vorschau-MJML (keine Anfrage erreicht sie je: .invalid)
BILD_MARKE = "https://bildbasis.vibemind.invalid/"
# Platz des Pflicht-Fusses im gespeicherten MJML (Nutzertext wird escaped und kann die Marke nie bilden)
FUSS_MARKE = "<!--vibemind:pflichtfuss-->"
FEHLER_MERKEN_S = 600
_MS_NAME = re.compile(r"ms-[0-9a-f]{8}-[0-9]{1,6}-[0-9]{2,3}(?:-[0-9]{1,3})?\.jpg")
_fehler: dict[tuple[str, int], tuple[float, str]] = {}
_fehler_sperre = threading.Lock()
_erzeug_sperre = threading.Lock()   # hoechstens eine Erzeugung gleichzeitig (mit Neu-Lesen nach dem Warten)


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


def _neu(i: str, fassung: int, zeile: dict) -> dict:
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
