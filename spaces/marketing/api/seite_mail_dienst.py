"""Mail-Fassung einer Collage (sales-claw Plan 2026-10-20-freie-flaeche-stufe4-mail-versand, Task 4): einmal je
(Inhalt, Fassung) erzeugt und in marketing.seite_mailfassungen (081) gespeichert. Einreichen, Versand und die
Mail-Vorschau einer Collage lesen sie von dort.

erzeugen: Anrede-im-Bild-Pruefung, Bildstuecke rendern (headless Chromium ueber seite_bilder, Medien nur aus
MARKETING_MEDIEN_ORDNER und MARKETING_BILD_ORDNER), zuschneiden, als ms-<inhalt 8 hex>-<fassung>-<nr>.jpg in der
Ablage (MARKETING_BILD_ORDNER) ablegen und der Firma zuordnen wie die Export-Dateien (chat._ablegen_und_zuordnen,
Temp-Datei + atomarer Link, nie ueberschreiben), dann Versand-HTML (cid:), Vorschau-MJML und Textfassung bauen
und speichern. Die signierte Bildbasis der sales-ui laeuft ab und wird nie gespeichert: das Vorschau-MJML traegt
BILD_MARKE, vorschau_html setzt je Anfrage die Bildbasis ein.

Gleichzeitige Anfragen fuer dieselbe Fassung: jede legt eigene Dateien ab (ein vergebener Name bekommt -2, -3 ...),
die DB nimmt mit ON CONFLICT DO NOTHING nur die erste Zeile; wer verliert, loescht seine Dateien und liefert die
gespeicherte Zeile. Scheitert etwas nach dem Ablegen, werden die eben abgelegten Dateien geloescht.

Synchron (Chromium im Aufrufer-Thread, hoechstens einer gleichzeitig - seite_bilder sperrt): die Routen sind
sync def und laufen damit im Threadpool von FastAPI, wie der PDF-Druck."""
from __future__ import annotations

import html as _html
import json
import logging
import os
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
        except OSError:
            log.warning("Bildstueck %s liess sich nicht entfernen", n)


def _stuecke_rendern(s: dict, stuecke: list, ordner: str) -> list[bytes]:
    try:
        png = seite_bilder.render_png(s, _medien_leser(ordner))
        return seite_bilder.zuschneiden(png, stuecke)
    except seite_bilder.StueckFehler as e:
        raise HTTPException(422, str(e))
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - Chromium/Playwright fehlt oder bricht ab: nie den Rohtext nach aussen
        log.warning("Bildstuecke der Collage-Mail nicht gerendert", exc_info=True)
        raise HTTPException(503, NICHT_GERENDERT)


def erzeugen(i: str, fassung: int) -> dict:
    """Mail-Fassung der Collage-Fassung (inhalt, fassung) - die vorhandene, sonst neu erzeugt und gespeichert.
    Rueckgabe wie marketing.pult_seite_mail_lesen: inhalt, fassung, html_versand, html_vorschau_mjml, text,
    medien, bytes, erstellt_am. Fehler als HTTPException (422 mit deutschem Grund, 404, 503)."""
    zeile = _fassung_lesen(i, fassung)
    if isinstance(zeile.get("mail"), dict):
        return zeile["mail"]
    s = zeile.get("seite")
    try:
        if seite_mail.anrede_in_bild(s):
            raise HTTPException(422, ANREDE_IM_BILD)
        stuecke = seite_mail.stuecke(s)
    except seite.SeiteFehler as e:
        raise HTTPException(422, str(e))
    namen: list[str] = []
    ordner = ""
    if stuecke:
        ordner = _ordner()
        jpgs = _stuecke_rendern(s, stuecke, ordner)
        namen = _ablegen_und_zuordnen(
            ordner, i, [(f"ms-{i[:8]}-{int(fassung)}-{st.nr:02d}", roh) for st, roh in zip(stuecke, jpgs)])
        groesse = sum(len(roh) for roh in jpgs)
    else:
        groesse = 0
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
        versand = seite_mail.rendern(s, namen, betreff=betreff, vorschautext=vorschautext, pflichtteil=pflicht,
                                     bild_adresse=lambda n: bloecke_mjml.CID + urllib.parse.quote(n),
                                     fuer_versand=True)
        vorschau = seite_mail.mjml(s, namen, betreff=betreff, vorschautext=vorschautext, pflichtteil=pflicht,
                                   bild_adresse=lambda n: BILD_MARKE + urllib.parse.quote(n), fuer_versand=False)
        text = seite_mail.text_fassung(s, pflicht)
    except (bloecke_mjml.RenderFehler, seite.SeiteFehler) as e:
        raise HTTPException(422, str(e))
    medien = seite_mail.versand_medien(namen)
    ergebnis = _schreiben(lambda:
        f"SELECT marketing.pult_seite_mail_speichern({lit(i)}::uuid, {int(fassung)}, {lit(versand)}, "
        f"{lit(vorschau)}, {lit(text)}, {lit(json.dumps(medien, ensure_ascii=False))}::jsonb, {int(groesse)}) "
        "AS mail")
    mail = ergebnis.get("mail")
    if not isinstance(mail, dict):
        raise HTTPException(503, "Marketing-Datenbank nicht erreichbar")
    return mail


def vorschau_html(mail: dict, bild_basis: str) -> str:
    """Mail-HTML der Vorschau: das gespeicherte MJML mit der Bildbasis dieser Anfrage (geprueft von
    pult._bild_basis), Platzhalter als [Vorname]/[Abmeldelink]."""
    quelle = str(mail.get("html_vorschau_mjml") or "").replace(BILD_MARKE, _html.escape(bild_basis or "", quote=True))
    try:
        return bloecke_mjml.mjml_zu_html(quelle)
    except bloecke_mjml.RenderFehler as e:
        raise HTTPException(422, str(e))
