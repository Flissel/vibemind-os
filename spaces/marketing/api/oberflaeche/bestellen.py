"""Bestellen im Marketing-Reiter (Stufe B, Punkt 2): neues Reel aus Bildern der Firma, neue Podcast-Folge,
ein Reel neu bauen lassen, offene Auftraege abbrechen. Vorbild: sales-claw ui_reels und ui_podcast.

Bestellungen kosten (Laura rendert, die Stimme spricht, der PC rechnet). Darum stehen sie hinter einem
Kosten-Schalter: nur MARKETING_BESTELLEN=an schaltet sie ein, alles andere laesst sie aus (Vorgabe aus).
Ist er aus, zeigen die Seiten die Formulare gesperrt mit Hinweis, und die POST-Wege lehnen mit 422 ab,
bevor etwas bestellt wird. Abbrechen kostet nichts und geht immer.

Geschrieben wird nur ueber die vorhandenen Pult-Funktionen (video.pult_anlegen, pult_nochmal,
pult_abbrechen, ton.pult_anlegen, ton.pult_abbrechen). Versendet wird hier nichts: fertige Reels und
Folgen gehen weiter nur ueber den Versandweg mit Freigabe durch einen Menschen."""
from __future__ import annotations

import os
import uuid

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.oberflaeche import daten, huelle, medien
from spaces.marketing.api.oberflaeche.huelle import PFAD, Kontext, e

SCHALTER_ENV = "MARKETING_BESTELLEN"
REELS, PODCAST = f"{PFAD}/reels", f"{PFAD}/podcast"
# Grenzen wie in video.py / ton.py (die Pult-Funktionen pruefen sie noch einmal)
TITEL_MAX, THEMA_MAX, TEXT_MAX, HOOK_MAX, WUNSCH_MAX = 120, 300, 900, 120, 500
DAUER_MIN, DAUER_MAX, DAUER_VORGABE = 15, 60, 30
BILDER_MIN, BILDER_MAX = 1, 8
FORMATE = ("reel", "quadrat", "quer")
SKRIPT_MAX = 12000
REEL_ABBRECHBAR = ("offen", "in_arbeit")
FOLGE_ABBRECHBAR = ("offen", "in_arbeit")
FORM_GRENZE = 64 * 1024

TEXTE = {
    "de": {
        "aus": "Bestellen ist ausgeschaltet (Kosten-Schalter). Bestellungen kosten Rechenzeit und Dienste; "
               "eingeschaltet wird nur auf ausdrücklichen Wunsch.",
        "aus_kurz": "Bestellen ist ausgeschaltet (Kosten-Schalter).",
        "reel_neu": "Neues Reel bestellen",
        "reel_hinweis": "Ein Reel aus 1 bis 8 Bildern der Firma. Material, Kurzfassung und KI-Bilder gehen "
                        "vorerst in der Sales-Oberfläche.",
        "titel": "Titel", "thema": "Thema", "sprechertext": "Sprechertext", "hook": "Aufhänger (optional)",
        "dauer": "Dauer in Sekunden ({von} bis {bis})", "format": "Format",
        "fm_reel": "Hochformat 9:16", "fm_quadrat": "Quadrat 1:1", "fm_quer": "Querformat 16:9",
        "bilder": "Bilder ({von} bis {bis})", "bilder_fehlen": "Die Bilder der Firma sind gerade nicht lesbar.",
        "bilder_leer": "Die Firma hat noch keine Bilder.",
        "bestellen": "Bestellen",
        "nochmal": "Neu bauen lassen", "wunsch": "Änderungswunsch (bei Fehlern optional)",
        "nochmal_los": "Neu bauen",
        "abbrechen": "Abbrechen",
        "folge_neu": "Neue Folge bestellen",
        "folge_hinweis": "Eine Zeile je Abschnitt: „Sprecher: Text“. Zeilen ohne Doppelpunkt gehören zum "
                         "Abschnitt davor.",
        "sprache": "Sprache der Folge (zwei Buchstaben)", "skript": "Skript",
        "bestellt": "Bestellt. Der Auftrag steht unter „In Arbeit“.",
        "folge_bestellt": "Bestellt. Die Folge wird gesprochen, sobald der PC frei ist.",
        "neu_gebaut": "Neuer Bau bestellt.",
        "abgebrochen": "Abgebrochen.",
        "f_titel": "Titel: 1 bis {n} Zeichen.",
        "f_feld": "{feld}: höchstens {n} Zeichen.",
        "f_pflicht": "{feld} fehlt.",
        "f_dauer": "Dauer: {von} bis {bis} Sekunden.",
        "f_format": "Format: Hochformat, Quadrat oder Querformat.",
        "f_bilder": "Bitte {von} bis {bis} Bilder der Firma wählen.",
        "f_sprache": "Sprache: zwei Kleinbuchstaben, z. B. de.",
        "f_skript": "Skript: jede Zeile beginnt mit „Sprecher: “, höchstens {n} Zeichen insgesamt.",
        "f_auftrag": "Diesen Auftrag gibt es bei dieser Firma nicht.",
        "f_status": "Dieser Auftrag lässt sich so nicht mehr ändern.",
    },
    "en": {
        "aus": "Ordering is switched off (cost switch). Orders cost computing time and services; it is only "
               "switched on when explicitly wanted.",
        "aus_kurz": "Ordering is switched off (cost switch).",
        "reel_neu": "Order a new reel",
        "reel_hinweis": "A reel from 1 to 8 of the company's images. Footage, short cuts and AI images still go "
                        "through the Sales interface for now.",
        "titel": "Title", "thema": "Topic", "sprechertext": "Voice-over text", "hook": "Hook (optional)",
        "dauer": "Length in seconds ({von} to {bis})", "format": "Format",
        "fm_reel": "Portrait 9:16", "fm_quadrat": "Square 1:1", "fm_quer": "Landscape 16:9",
        "bilder": "Images ({von} to {bis})", "bilder_fehlen": "The company's images can't be read right now.",
        "bilder_leer": "The company has no images yet.",
        "bestellen": "Order",
        "nochmal": "Have it rebuilt", "wunsch": "Change request (optional after an error)",
        "nochmal_los": "Rebuild",
        "abbrechen": "Cancel",
        "folge_neu": "Order a new episode",
        "folge_hinweis": "One line per section: “Speaker: text”. Lines without a colon belong to the section "
                         "before.",
        "sprache": "Episode language (two letters)", "skript": "Script",
        "bestellt": "Ordered. The job is listed under “In progress”.",
        "folge_bestellt": "Ordered. The episode will be recorded as soon as the PC is free.",
        "neu_gebaut": "Rebuild ordered.",
        "abgebrochen": "Cancelled.",
        "f_titel": "Title: 1 to {n} characters.",
        "f_feld": "{feld}: at most {n} characters.",
        "f_pflicht": "{feld} is missing.",
        "f_dauer": "Length: {von} to {bis} seconds.",
        "f_format": "Format: portrait, square or landscape.",
        "f_bilder": "Please choose {von} to {bis} of the company's images.",
        "f_sprache": "Language: two lowercase letters, e.g. en.",
        "f_skript": "Script: every line starts with “Speaker: ”, at most {n} characters in total.",
        "f_auftrag": "This job doesn't exist for this company.",
        "f_status": "This job can no longer be changed this way.",
    },
}


def an() -> bool:
    """Kosten-Schalter: nur der ausdrueckliche Wert 'an' schaltet Bestellen ein."""
    return os.environ.get(SCHALTER_ENV, "").strip().lower() == "an"


def tx(s: str, schluessel: str, **werte) -> str:
    return huelle._sprache.text(TEXTE, s, schluessel, **werte)


class Abgelehnt(ValueError):
    """Eingabe passt nicht; die Nachricht ist schon der Satz fuer die Seite."""


def _csrf() -> str:
    return f'<input type="hidden" name="csrf" value="{e(huelle.csrf_marke())}">'


def _gesperrt(k: Kontext) -> tuple[str, str]:
    """(Hinweis, Attribut fuer das fieldset): bei ausgeschaltetem Schalter gesperrt mit Satz."""
    if an():
        return "", ""
    return f'<p class="fehler">{e(k.t(TEXTE, "aus"))}</p>', " disabled"


def meldung(k: Kontext) -> str:
    q = k.request.query_params
    for schluessel in ("bestellt", "folge_bestellt", "neu_gebaut", "abgebrochen"):
        if q.get(schluessel) == "1":
            return f'<p class="erfolg">{e(k.t(TEXTE, schluessel))}</p>'
    return ""


# --- Formulare --------------------------------------------------------------------------------------

def _bildwahl(k: Kontext, namen: list[str] | None) -> str:
    if namen is None:
        return f'<p class="meta">{e(k.t(TEXTE, "bilder_fehlen"))}</p>'
    if not namen:
        return f'<p class="meta">{e(k.t(TEXTE, "bilder_leer"))}</p>'
    karten = "".join(f'<label class="bildwahl"><input type="checkbox" name="bild:{e(n)}" value="1">'
                     f'{medien.bild_html(str(k.firma or ""), n, n)}<span>{e(n)}</span></label>' for n in namen)
    return (f'<p class="meta">{e(k.t(TEXTE, "bilder", von=BILDER_MIN, bis=BILDER_MAX))}</p>'
            f'<div class="galerie">{karten}</div>')


def reel_formular(k: Kontext, namen: list[str] | None) -> str:
    satz, sperre = _gesperrt(k)
    formate = "".join(f'<option value="{f}">{e(k.t(TEXTE, "fm_" + f))}</option>' for f in FORMATE)
    return (f'<details class="aufklapp bestellen"><summary>{e(k.t(TEXTE, "reel_neu"))}</summary>{satz}'
            f'<form method="post" action="{REELS}/bestellen" class="editor">{_csrf()}<fieldset{sperre}>'
            f'<p class="meta">{e(k.t(TEXTE, "reel_hinweis"))}</p>'
            f'<label>{e(k.t(TEXTE, "titel"))} <input type="text" name="titel" maxlength="{TITEL_MAX}" required></label>'
            f'<label>{e(k.t(TEXTE, "thema"))} <input type="text" name="thema" maxlength="{THEMA_MAX}" required></label>'
            f'<label>{e(k.t(TEXTE, "sprechertext"))} <textarea name="sprechertext" maxlength="{TEXT_MAX}" '
            f'required></textarea></label>'
            f'<label>{e(k.t(TEXTE, "hook"))} <input type="text" name="hook" maxlength="{HOOK_MAX}"></label>'
            f'<div class="zeile"><label>{e(k.t(TEXTE, "dauer", von=DAUER_MIN, bis=DAUER_MAX))} '
            f'<input type="number" name="dauer_s" min="{DAUER_MIN}" max="{DAUER_MAX}" value="{DAUER_VORGABE}"></label>'
            f'<label>{e(k.t(TEXTE, "format"))} <select name="format">{formate}</select></label></div>'
            f'{_bildwahl(k, namen)}'
            f'<p><button type="submit">{e(k.t(TEXTE, "bestellen"))}</button></p></fieldset></form></details>')


def reel_knoepfe(k: Kontext, a: dict) -> str:
    """Unter der Karte: Neu bauen (fertig/fehler, hinter dem Schalter), Abbrechen (offen/in Arbeit)."""
    aid, st = str(a.get("id") or ""), str(a.get("status") or "")
    if not _gueltige_id(aid):
        return ""
    if st in REEL_ABBRECHBAR:
        return (f'<form method="post" action="{REELS}/{aid}/abbrechen" class="anlegen">{_csrf()}'
                f'<button type="submit">{e(k.t(TEXTE, "abbrechen"))}</button></form>')
    if st in ("fertig", "fehler") and not a.get("archiviert"):
        _, sperre = _gesperrt(k)
        return (f'<details class="aufklapp bestellen"><summary>{e(k.t(TEXTE, "nochmal"))}</summary>'
                f'<form method="post" action="{REELS}/{aid}/nochmal" class="editor">{_csrf()}<fieldset{sperre}>'
                + ("" if an() else f'<p class="meta">{e(k.t(TEXTE, "aus_kurz"))}</p>')
                + f'<label>{e(k.t(TEXTE, "wunsch"))} <textarea name="wunsch" maxlength="{WUNSCH_MAX}"></textarea>'
                  f'</label><p><button type="submit">{e(k.t(TEXTE, "nochmal_los"))}</button></p>'
                  f'</fieldset></form></details>')
    return ""


def folge_formular(k: Kontext) -> str:
    satz, sperre = _gesperrt(k)
    sprache = "en" if k.sprache == "en" else "de"
    return (f'<details class="aufklapp bestellen"><summary>{e(k.t(TEXTE, "folge_neu"))}</summary>{satz}'
            f'<form method="post" action="{PODCAST}/bestellen" class="editor">{_csrf()}<fieldset{sperre}>'
            f'<label>{e(k.t(TEXTE, "titel"))} <input type="text" name="titel" maxlength="{TITEL_MAX}" required></label>'
            f'<label>{e(k.t(TEXTE, "sprache"))} <input type="text" name="sprache" maxlength="2" value="{sprache}" '
            f'pattern="[a-z]{{2}}" required></label>'
            f'<label>{e(k.t(TEXTE, "skript"))} <textarea name="skript" maxlength="{SKRIPT_MAX}" rows="10" '
            f'required></textarea></label><p class="meta">{e(k.t(TEXTE, "folge_hinweis"))}</p>'
            f'<p><button type="submit">{e(k.t(TEXTE, "bestellen"))}</button></p></fieldset></form></details>')


def folge_knoepfe(k: Kontext, a: dict) -> str:
    aid, st = str(a.get("id") or ""), str(a.get("status") or "")
    if not _gueltige_id(aid) or st not in FOLGE_ABBRECHBAR:
        return ""
    return (f'<form method="post" action="{PODCAST}/{aid}/abbrechen" class="anlegen">{_csrf()}'
            f'<button type="submit">{e(k.t(TEXTE, "abbrechen"))}</button></form>')


# --- Eingaben pruefen ---------------------------------------------------------------------------------

def _gueltige_id(roh) -> bool:
    try:
        return str(uuid.UUID(str(roh))) == str(roh).lower()
    except ValueError:
        return False


def _text(form: dict, name: str, grenze: int, s: str, beschriftung: str, pflicht: bool, einzeilig: bool) -> str:
    roh = str(form.get(name) or "").replace("\r\n", "\n")
    wert = " ".join(roh.split()) if einzeilig else roh.strip()
    if pflicht and not wert:
        raise Abgelehnt(tx(s, "f_pflicht", feld=beschriftung))
    if len(wert) > grenze:
        raise Abgelehnt(tx(s, "f_feld", feld=beschriftung, n=grenze))
    return wert


def reel_briefing(form: dict, s: str, namen: list[str]) -> tuple[str, dict]:
    """(titel, briefing) fuer video.pult_anlegen mit art 'bilder'; Bilder nur aus den Bildern der Firma."""
    titel = " ".join(str(form.get("titel") or "").split())
    if not titel or len(titel) > TITEL_MAX:
        raise Abgelehnt(tx(s, "f_titel", n=TITEL_MAX))
    briefing: dict = {"thema": _text(form, "thema", THEMA_MAX, s, tx(s, "thema"), True, True),
                      "sprechertext": _text(form, "sprechertext", TEXT_MAX, s, tx(s, "sprechertext"), True, False),
                      "hook": _text(form, "hook", HOOK_MAX, s, tx(s, "hook"), False, True)}
    try:
        dauer = int(str(form.get("dauer_s") or DAUER_VORGABE).strip())
    except ValueError:
        dauer = 0
    if not DAUER_MIN <= dauer <= DAUER_MAX:
        raise Abgelehnt(tx(s, "f_dauer", von=DAUER_MIN, bis=DAUER_MAX))
    briefing["dauer_s"] = dauer
    fmt = str(form.get("format") or "reel")
    if fmt not in FORMATE:
        raise Abgelehnt(tx(s, "f_format"))
    if fmt != "reel":
        briefing["format"] = fmt
    erlaubt = set(namen)
    gewaehlt = [n[5:] for n, v in form.items() if n.startswith("bild:") and v == "1"]
    if not BILDER_MIN <= len(gewaehlt) <= BILDER_MAX or any(n not in erlaubt for n in gewaehlt):
        raise Abgelehnt(tx(s, "f_bilder", von=BILDER_MIN, bis=BILDER_MAX))
    briefing["bilder"] = sorted(gewaehlt)
    return titel, briefing


def skript_lesen(roh: str, s: str) -> list[dict]:
    """'Sprecher: Text' je Zeile; Zeilen ohne Doppelpunkt haengen am Abschnitt davor. Die Feinregeln
    (Namen, Laengen, Zahl der Abschnitte) prueft ton.pult_anlegen."""
    text = str(roh or "").replace("\r\n", "\n")
    if not text.strip() or len(text) > SKRIPT_MAX:
        raise Abgelehnt(tx(s, "f_skript", n=SKRIPT_MAX))
    abschnitte: list[dict] = []
    for zeile in (z.strip() for z in text.split("\n")):
        if not zeile:
            continue
        sprecher, doppelpunkt, rest = zeile.partition(":")
        if doppelpunkt and sprecher.strip() and len(sprecher.strip()) <= 30 and rest.strip():
            abschnitte.append({"sprecher": sprecher.strip(), "text": rest.strip()})
        elif abschnitte:
            abschnitte[-1]["text"] += " " + zeile
        else:
            raise Abgelehnt(tx(s, "f_skript", n=SKRIPT_MAX))
    if not abschnitte:
        raise Abgelehnt(tx(s, "f_skript", n=SKRIPT_MAX))
    return abschnitte


# --- POST-Wege ----------------------------------------------------------------------------------------

async def _anfang(request: Request, kostet: bool) -> tuple[Response | None, str, dict, str | None]:
    """Schluessel, CSRF, Firma; bei Bestellungen zuerst der Kosten-Schalter (aus = 422, nichts bestellt)."""
    fehler, s, form, firma = await huelle.formular_mit_firma(request, FORM_GRENZE)
    if fehler is not None:
        return fehler, s, form, None
    if kostet and not an():
        return huelle.abgelehnt(s, tx(s, "aus_kurz")), s, form, None
    return None, s, form, firma


async def reel_bestellen(request: Request) -> Response:
    fehler, s, form, firma = await _anfang(request, kostet=True)
    if fehler is not None:
        return fehler
    try:
        namen = await run_in_threadpool(daten.bilder, str(firma))
        titel, briefing = reel_briefing(form, s, namen)
        await run_in_threadpool(daten.reel_bestellen, str(firma), titel, briefing)
    except Abgelehnt as f:
        return huelle.abgelehnt(s, str(f))
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return RedirectResponse(f"{REELS}?reiter=arbeit&bestellt=1", status_code=303)


async def _reel_der_firma(firma: str, aid: str, s: str) -> dict:
    if not _gueltige_id(aid):
        raise Abgelehnt(tx(s, "f_auftrag"))
    for a in await run_in_threadpool(daten.reels, firma, False):
        if str(a.get("id")) == aid:
            return a
    raise Abgelehnt(tx(s, "f_auftrag"))


async def reel_nochmal(request: Request, aid: str) -> Response:
    fehler, s, form, firma = await _anfang(request, kostet=True)
    if fehler is not None:
        return fehler
    try:
        a = await _reel_der_firma(str(firma), aid, s)
        if a.get("status") not in ("fertig", "fehler"):
            raise Abgelehnt(tx(s, "f_status"))
        wunsch = _text(form, "wunsch", WUNSCH_MAX, s, tx(s, "wunsch"), a.get("status") == "fertig", False)
        await run_in_threadpool(daten.reel_nochmal, str(firma), aid, wunsch)
    except Abgelehnt as f:
        return huelle.abgelehnt(s, str(f))
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return RedirectResponse(f"{REELS}?reiter=arbeit&neu_gebaut=1", status_code=303)


async def reel_abbrechen(request: Request, aid: str) -> Response:
    fehler, s, _form, firma = await _anfang(request, kostet=False)
    if fehler is not None:
        return fehler
    try:
        a = await _reel_der_firma(str(firma), aid, s)
        if a.get("status") not in REEL_ABBRECHBAR:
            raise Abgelehnt(tx(s, "f_status"))
        await run_in_threadpool(daten.reel_abbrechen, str(firma), aid)
    except Abgelehnt as f:
        return huelle.abgelehnt(s, str(f))
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return RedirectResponse(f"{REELS}?reiter=fehler&abgebrochen=1", status_code=303)


async def folge_bestellen(request: Request) -> Response:
    fehler, s, form, firma = await _anfang(request, kostet=True)
    if fehler is not None:
        return fehler
    try:
        titel = " ".join(str(form.get("titel") or "").split())
        if not titel or len(titel) > TITEL_MAX:
            raise Abgelehnt(tx(s, "f_titel", n=TITEL_MAX))
        sprache = str(form.get("sprache") or "").strip()
        if len(sprache) != 2 or not sprache.isascii() or not sprache.isalpha() or not sprache.islower():
            raise Abgelehnt(tx(s, "f_sprache"))
        skript = skript_lesen(str(form.get("skript") or ""), s)
        await run_in_threadpool(daten.folge_bestellen, str(firma), titel, sprache, skript)
    except Abgelehnt as f:
        return huelle.abgelehnt(s, str(f))
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return RedirectResponse(f"{PODCAST}?folge_bestellt=1", status_code=303)


async def folge_abbrechen(request: Request, aid: str) -> Response:
    """ton.pult_abbrechen prueft die Firma nicht; darum zuerst: gehoert die Folge zur gewaehlten Firma?"""
    fehler, s, _form, firma = await _anfang(request, kostet=False)
    if fehler is not None:
        return fehler
    try:
        if not _gueltige_id(aid):
            raise Abgelehnt(tx(s, "f_auftrag"))
        folge = next((a for a in await run_in_threadpool(daten.podcast, str(firma)) if str(a.get("id")) == aid),
                     None)
        if folge is None:
            raise Abgelehnt(tx(s, "f_auftrag"))
        if folge.get("status") not in FOLGE_ABBRECHBAR:
            raise Abgelehnt(tx(s, "f_status"))
        await run_in_threadpool(daten.folge_abbrechen, aid)
    except Abgelehnt as f:
        return huelle.abgelehnt(s, str(f))
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return RedirectResponse(f"{PODCAST}?abgebrochen=1", status_code=303)
