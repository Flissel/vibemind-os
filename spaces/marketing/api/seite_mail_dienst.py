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

aufraeumen (stuendlich ueber gestaltung.aufraeumen_falls_faellig): pult_seite_mail_aufraeumen (081) loescht die
Zeilen alter, nicht mehr gebrauchter Mail-Fassungen, erst danach werden ihre ms-Dateien geloescht.

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
NICHT_GERENDERT = "Bildstücke konnten nicht gerendert werden"
# Platzhalter-Adresse der Bildstuecke im gespeicherten Vorschau-MJML (keine Anfrage erreicht sie je: .invalid)
BILD_MARKE = "https://bildbasis.vibemind.invalid/"
# Platz des Pflicht-Fusses im gespeicherten MJML (Nutzertext wird escaped und kann die Marke nie bilden)
FUSS_MARKE = "<!--vibemind:pflichtfuss-->"
FEHLER_MERKEN_S = 600
_MS_NAME = re.compile(r"ms-[0-9a-f]{8}-[0-9]{1,6}-[0-9]{2,3}(?:-[0-9]{1,3})?\.jpg")
_fehler: dict[tuple[str, int], tuple[float, str]] = {}
_fehler_sperre = threading.Lock()


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


def _stuecke_rendern(i: str, fassung: int, s: dict, stuecke: list, ordner: str) -> list[bytes]:
    try:
        png = seite_bilder.render_png(s, _medien_leser(ordner))
        return seite_bilder.zuschneiden(png, stuecke)
    except seite_bilder.StueckFehler as e:
        if str(e) == seite_bilder.ZEITLIMIT:      # kein Inhaltsfehler: beim naechsten Mal neu versuchen
            raise HTTPException(422, str(e))
        raise _fehler_merken(i, fassung, str(e))
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - Chromium/Playwright fehlt oder bricht ab: nie den Rohtext nach aussen
        log.warning("Bildstuecke der Collage-Mail nicht gerendert", exc_info=True)
        raise HTTPException(503, NICHT_GERENDERT)


def _holen(i: str, fassung: int) -> tuple[dict, dict]:
    """(Fassungszeile, Mail-Fassung) - die vorhandene, sonst neu erzeugt und gespeichert."""
    zeile = _fassung_lesen(i, fassung)
    if isinstance(zeile.get("mail"), dict):
        return zeile, zeile["mail"]
    gemerkt = _fehler_gemerkt(i, fassung)
    if gemerkt is not None:
        raise HTTPException(422, gemerkt)
    return zeile, _neu(i, fassung, zeile)


def erzeugen(i: str, fassung: int) -> dict:
    """Mail-Fassung der Collage-Fassung (inhalt, fassung) - die vorhandene, sonst neu erzeugt und gespeichert.
    Rueckgabe wie marketing.pult_seite_mail_lesen: inhalt, fassung, mjml_versand, html_vorschau_mjml, text,
    medien, bytes, erstellt_am. Fehler als HTTPException (422 mit deutschem Grund, 404, 503)."""
    return _holen(i, fassung)[1]


def _neu(i: str, fassung: int, zeile: dict) -> dict:
    s = zeile.get("seite")
    try:
        if seite_mail.anrede_in_bild(s):
            raise _fehler_merken(i, fassung, ANREDE_IM_BILD)
        stuecke = seite_mail.stuecke(s)
    except seite.SeiteFehler as e:
        raise _fehler_merken(i, fassung, str(e))
    namen: list[str] = []
    ordner = ""
    groesse = 0
    if stuecke:
        ordner = _ordner()
        jpgs = _stuecke_rendern(i, fassung, s, stuecke, ordner)
        namen = _ablegen_und_zuordnen(
            ordner, i, [(f"ms-{i[:8]}-{int(fassung)}-{st.nr:02d}", roh) for st, roh in zip(stuecke, jpgs)])
        groesse = sum(len(roh) for roh in jpgs)
    try:
        gespeichert = _speichern(i, fassung, zeile, s, namen, groesse)
    except BaseException:
        _wegraeumen(ordner, namen)
        raise
    eigene = [n for n in namen if n not in {m.get("name") for m in gespeichert.get("medien") or []}]
    if eigene:   # gleichzeitig erzeugt, die andere Anfrage war zuerst gespeichert
        _wegraeumen(ordner, eigene)
    return gespeichert


def _speichern(i: str, fassung: int, zeile: dict, s: dict, namen: list[str], groesse: int) -> dict:
    felder = zeile.get("felder") or {}
    betreff, vorschautext = str(felder.get("betreff") or ""), str(felder.get("vorschautext") or "")
    pflicht = zeile.get("pflichtteil") or {}
    try:
        versand_mjml = seite_mail.mjml(s, namen, betreff=betreff, vorschautext=vorschautext, pflichtteil=pflicht,
                                       bild_adresse=lambda n: bloecke_mjml.CID + urllib.parse.quote(n),
                                       fuer_versand=True, fuss_ersatz=FUSS_MARKE)
        vorschau_mjml = seite_mail.mjml(s, namen, betreff=betreff, vorschautext=vorschautext, pflichtteil=pflicht,
                                        bild_adresse=lambda n: BILD_MARKE + urllib.parse.quote(n),
                                        fuer_versand=False, fuss_ersatz=FUSS_MARKE)
        _rendern(versand_mjml, s, pflicht, True)       # setzt mrml es nicht, wird nichts gespeichert
        text = seite_mail.text_fassung(s, pflicht)
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
    if namen:
        _wegraeumen(_ordner(), namen)
    return namen
