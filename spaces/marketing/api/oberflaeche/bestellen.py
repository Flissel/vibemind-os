"""Bestellen im Marketing-Reiter (Stufe B, Punkt 2): neues Reel aus Bildern der Firma, neue Podcast-Folge,
ein Reel neu bauen lassen, offene Auftraege abbrechen. Seit F5 (vor dem Abbau in Sales) auch Reels aus
Videomaterial, Kurzfassungen eines langen Videos und Reels aus KI-Bildern samt Bildpruefung (einzelne Bilder
neu erzeugen, freigeben). Vorbild: sales-claw ui_reels und ui_podcast.

Bestellungen kosten (Laura rendert, die Stimme spricht, der PC rechnet). Darum stehen sie hinter einem
Kosten-Schalter: nur MARKETING_BESTELLEN=an schaltet sie ein, alles andere laesst sie aus (Vorgabe aus).
Ist er aus, zeigen die Seiten die Formulare gesperrt mit Hinweis, und die POST-Wege lehnen mit 422 ab,
bevor etwas bestellt wird. Abbrechen kostet nichts und geht immer.

Geschrieben wird nur ueber die vorhandenen Pult-Funktionen (video.pult_anlegen, pult_nochmal,
pult_abbrechen, reel_bilder.pult_ki_neu, pult_ki_freigeben, ton.pult_anlegen, ton.pult_abbrechen). Versendet wird hier nichts: fertige Reels und
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
REEL_ABBRECHBAR = ("offen", "in_arbeit", "ideen", "bilder_laufen", "bilder_pruefen")
ARTEN = ("bilder", "material", "kurzfassung", "ki")     # wie video.ARTEN; ki ist art bilder mit briefing.ki
KI_MIN, KI_MAX, KI_VORGABE = 3, 5, 4
VIDEOS_MAX = 8
IDEE_MAX = 600
FOLGE_ABBRECHBAR = ("offen", "in_arbeit")
FORM_GRENZE = 64 * 1024

TEXTE = {
    "de": {
        "aus": "Bestellen ist ausgeschaltet (Kosten-Schalter). Bestellungen kosten Rechenzeit und Dienste; "
               "eingeschaltet wird nur auf ausdrücklichen Wunsch.",
        "aus_kurz": "Bestellen ist ausgeschaltet (Kosten-Schalter).",
        "reel_neu": "Neues Reel bestellen",
        "reel_hinweis": "Ein Reel aus 1 bis 8 Bildern der Firma.",
        "neu_material": "Reel aus Videomaterial bestellen",
        "neu_kurzfassung": "Kurzfassung eines Videos bestellen",
        "neu_ki": "Reel aus KI-Bildern bestellen",
        "material_hinweis": "Bis zu 8 Videos aus Laura. Ohne Auswahl sucht der Video-Claw passende aus.",
        "kurzfassung_hinweis": "Ein langes Video aus Laura wird zum Reel gekürzt. Der Sprechertext ist "
                               "hier freiwillig.",
        "ki_hinweis": "Der Bild-Arbeiter entwirft und erzeugt {von} bis {bis} Bilder. Du prüfst sie, bevor das "
                      "Reel gebaut wird.",
        "videos": "Videos aus Laura", "video": "Langes Video aus Laura",
        "videos_leer": "Laura liefert gerade keine Videos.",
        "anzahl": "Zahl der Bilder ({von} bis {bis})",
        "sprechertext_frei": "Sprechertext (optional)",
        "ki_pruefen": "Bilder prüfen",
        "ki_pruefen_hinweis": "Erst nach der Freigabe wird das Reel gebaut. Ein Bild neu erzeugen kostet "
                              "wieder Rechenzeit.",
        "ki_idee": "Neue Bildidee (leer = dieselbe)",
        "ki_neu": "Neu erzeugen",
        "ki_freigeben": "Bilder freigeben und Reel bauen",
        "ki_bild_fehlt": "Dieses Bild ist noch nicht fertig.",
        "kb_offen": "wartet", "kb_in_arbeit": "entsteht", "kb_fertig": "fertig", "kb_fehler": "Fehler",
        "ki_neu_ok": "Das Bild wird neu erzeugt.",
        "ki_frei_ok": "Freigegeben. Das Reel wird gebaut.",
        "f_art": "Unbekannte Art.",
        "f_anzahl": "Zahl der Bilder: {von} bis {bis}.",
        "f_videos": "Bitte höchstens {n} Videos aus der Liste wählen.",
        "f_video": "Bitte ein Video aus der Liste wählen.",
        "f_bild": "Dieses Bild gehört nicht zu diesem Reel.",
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
        "reel_hinweis": "A reel from 1 to 8 of the company's images.",
        "neu_material": "Order a reel from video footage",
        "neu_kurzfassung": "Order a short cut of a video",
        "neu_ki": "Order a reel from AI images",
        "material_hinweis": "Up to 8 videos from Laura. Without a choice the video claw picks suitable ones.",
        "kurzfassung_hinweis": "A long video from Laura is cut down to a reel. The voice-over is optional here.",
        "ki_hinweis": "The image worker drafts and creates {von} to {bis} images. You review them before the "
                      "reel is built.",
        "videos": "Videos from Laura", "video": "Long video from Laura",
        "videos_leer": "Laura isn't delivering any videos right now.",
        "anzahl": "Number of images ({von} to {bis})",
        "sprechertext_frei": "Voice-over text (optional)",
        "ki_pruefen": "Review images",
        "ki_pruefen_hinweis": "The reel is only built after approval. Recreating an image costs computing time "
                              "again.",
        "ki_idee": "New image idea (empty = the same)",
        "ki_neu": "Recreate",
        "ki_freigeben": "Approve images and build the reel",
        "ki_bild_fehlt": "This image isn't finished yet.",
        "kb_offen": "waiting", "kb_in_arbeit": "being made", "kb_fertig": "finished", "kb_fehler": "error",
        "ki_neu_ok": "The image is being recreated.",
        "ki_frei_ok": "Approved. The reel is being built.",
        "f_art": "Unknown type.",
        "f_anzahl": "Number of images: {von} to {bis}.",
        "f_videos": "Please choose at most {n} videos from the list.",
        "f_video": "Please choose a video from the list.",
        "f_bild": "This image doesn't belong to this reel.",
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
    for schluessel in ("bestellt", "folge_bestellt", "neu_gebaut", "abgebrochen", "ki_neu_ok", "ki_frei_ok"):
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
            f'{_bildwahl(k, namen) if an() else ""}'
            f'<p><button type="submit">{e(k.t(TEXTE, "bestellen"))}</button></p></fieldset></form></details>')


def _grundfelder(k: Kontext, sprechertext_pflicht: bool = True) -> str:
    formate = "".join(f'<option value="{f}">{e(k.t(TEXTE, "fm_" + f))}</option>' for f in FORMATE)
    text = "sprechertext" if sprechertext_pflicht else "sprechertext_frei"
    return (f'<label>{e(k.t(TEXTE, "titel"))} <input type="text" name="titel" maxlength="{TITEL_MAX}" required></label>'
            f'<label>{e(k.t(TEXTE, "thema"))} <input type="text" name="thema" maxlength="{THEMA_MAX}" required></label>'
            f'<label>{e(k.t(TEXTE, text))} <textarea name="sprechertext" maxlength="{TEXT_MAX}"'
            f'{" required" if sprechertext_pflicht else ""}></textarea></label>'
            f'<label>{e(k.t(TEXTE, "hook"))} <input type="text" name="hook" maxlength="{HOOK_MAX}"></label>'
            f'<div class="zeile"><label>{e(k.t(TEXTE, "dauer", von=DAUER_MIN, bis=DAUER_MAX))} '
            f'<input type="number" name="dauer_s" min="{DAUER_MIN}" max="{DAUER_MAX}" value="{DAUER_VORGABE}"></label>'
            f'<label>{e(k.t(TEXTE, "format"))} <select name="format">{formate}</select></label></div>')


def _videowahl(k: Kontext, material: dict | None, mehrere: bool) -> str:
    videos = [v for v in (material or {}).get("videos") or [] if isinstance(v, dict) and v.get("kennung")]
    hinweis = str((material or {}).get("hinweis") or "")
    if not videos:
        return f'<p class="meta">{e(hinweis or k.t(TEXTE, "videos_leer"))}</p>'
    zeilen = []
    for v in videos:
        kennung = str(v["kennung"])
        feld = (f'<input type="checkbox" name="video:{e(kennung)}" value="1">' if mehrere
                else f'<input type="radio" name="video" value="{e(kennung)}" required>')
        beschreibung = " · ".join(x for x in (str(v.get("name") or ""), str(v.get("projekt") or "")) if x)
        zeilen.append(f'<label class="videowahl">{feld} {e(beschreibung)}</label>')
    return (f'<p class="meta">{e(k.t(TEXTE, "videos" if mehrere else "video"))}</p>'
            + (f'<p class="meta">{e(hinweis)}</p>' if hinweis else "") + "".join(zeilen))


def weitere_formulare(k: Kontext, material: dict | None) -> str:
    """Material, Kurzfassung, KI-Bilder: je ein eigenes Formular (die Felder unterscheiden sich)."""
    satz, sperre = _gesperrt(k)

    def formular(art: str, titel: str, inhalt: str) -> str:
        return (f'<details class="aufklapp bestellen"><summary>{e(k.t(TEXTE, titel))}</summary>{satz}'
                f'<form method="post" action="{REELS}/bestellen" class="editor">{_csrf()}'
                f'<input type="hidden" name="art" value="{art}"><fieldset{sperre}>{inhalt}'
                f'<p><button type="submit">{e(k.t(TEXTE, "bestellen"))}</button></p></fieldset></form></details>')
    return (formular("material", "neu_material", f'<p class="meta">{e(k.t(TEXTE, "material_hinweis"))}</p>'
                     + _grundfelder(k) + (_videowahl(k, material, True) if an() else ""))
            + formular("kurzfassung", "neu_kurzfassung",
                       f'<p class="meta">{e(k.t(TEXTE, "kurzfassung_hinweis"))}</p>'
                       + _grundfelder(k, sprechertext_pflicht=False) + (_videowahl(k, material, False) if an() else ""))
            + formular("ki", "neu_ki", f'<p class="meta">{e(k.t(TEXTE, "ki_hinweis", von=KI_MIN, bis=KI_MAX))}</p>'
                       + _grundfelder(k)
                       + f'<label>{e(k.t(TEXTE, "anzahl", von=KI_MIN, bis=KI_MAX))} <input type="number" name="anzahl" '
                         f'min="{KI_MIN}" max="{KI_MAX}" value="{KI_VORGABE}"></label>'))


def ki_pruefen(k: Kontext, a: dict) -> str:
    """Bei bilder_pruefen: die KI-Bilder mit Idee und Stand, je Bild neu erzeugen, alle freigeben."""
    aid = str(a.get("id") or "")
    if a.get("status") != "bilder_pruefen" or not _gueltige_id(aid):
        return ""
    _, sperre = _gesperrt(k)
    bilder = [b for b in a.get("ki_bilder") or [] if isinstance(b, dict)] if isinstance(a.get("ki_bilder"), list) else []
    karten = []
    for b in bilder:
        bid, st = str(b.get("id") or ""), str(b.get("status") or "")
        bild = (medien.bild_html(str(k.firma or ""), b.get("datei"), str(b.get("idee") or ""))
                if st == "fertig" else f'<p class="meta">{e(k.t(TEXTE, "ki_bild_fehlt"))}</p>')
        stand = k.t(TEXTE, "kb_" + st) if st in ("offen", "in_arbeit", "fertig", "fehler") else st
        neu = ""
        if st in ("fertig", "fehler") and _gueltige_id(bid):
            neu = (f'<form method="post" action="{REELS}/{aid}/ki/{bid}/neu" class="editor">{_csrf()}'
                   f'<fieldset{sperre}><label>{e(k.t(TEXTE, "ki_idee"))} <textarea name="idee" '
                   f'maxlength="{IDEE_MAX}" rows="2"></textarea></label>'
                   f'<p><button type="submit">{e(k.t(TEXTE, "ki_neu"))}</button></p></fieldset></form>')
        befund = str(b.get("befund") or "").strip() if st == "fehler" else ""
        karten.append(f'<li>{bild}<p class="meta">{e(str(b.get("nr") or ""))}. {e(str(b.get("idee") or ""))} · '
                      f'{e(stand)}</p>' + (f'<p class="fehler">{e(befund)}</p>' if befund else "") + neu + "</li>")
    return (f'<details class="aufklapp bestellen kipruefen" open><summary>{e(k.t(TEXTE, "ki_pruefen"))}</summary>'
            + ("" if an() else f'<p class="meta">{e(k.t(TEXTE, "aus_kurz"))}</p>')
            + f'<p class="meta">{e(k.t(TEXTE, "ki_pruefen_hinweis"))}</p><ol class="kibilder">{"".join(karten)}</ol>'
              f'<form method="post" action="{REELS}/{aid}/ki/freigeben" class="anlegen">{_csrf()}<fieldset{sperre}>'
              f'<button type="submit">{e(k.t(TEXTE, "ki_freigeben"))}</button></fieldset></form></details>')


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


def _grund_briefing(form: dict, s: str, sprechertext_pflicht: bool) -> tuple[str, dict]:
    titel = " ".join(str(form.get("titel") or "").split())
    if not titel or len(titel) > TITEL_MAX:
        raise Abgelehnt(tx(s, "f_titel", n=TITEL_MAX))
    briefing: dict = {"thema": _text(form, "thema", THEMA_MAX, s, tx(s, "thema"), True, True),
                      "sprechertext": _text(form, "sprechertext", TEXT_MAX, s, tx(s, "sprechertext"),
                                            sprechertext_pflicht, False),
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
    return titel, briefing


def reel_auftrag(form: dict, s: str, kennungen: list[str]) -> tuple[str, str, dict]:
    """(art fuer video.pult_anlegen, titel, briefing) fuer Material, Kurzfassung und KI-Bilder.
    Videos nur aus der Laura-Liste (``kennungen``); die Feinregeln prueft video.briefing_pruefen."""
    art = str(form.get("art") or "")
    if art not in ("material", "kurzfassung", "ki"):
        raise Abgelehnt(tx(s, "f_art"))
    titel, briefing = _grund_briefing(form, s, sprechertext_pflicht=art != "kurzfassung")
    erlaubt = set(kennungen)
    if art == "material":
        gewaehlt = sorted(n[6:] for n, v in form.items() if n.startswith("video:") and v == "1")
        if len(gewaehlt) > VIDEOS_MAX or any(n not in erlaubt for n in gewaehlt):
            raise Abgelehnt(tx(s, "f_videos", n=VIDEOS_MAX))
        briefing["videos"] = gewaehlt
        return "material", titel, briefing
    if art == "kurzfassung":
        video = str(form.get("video") or "")
        if video not in erlaubt:
            raise Abgelehnt(tx(s, "f_video"))
        briefing["video"] = video
        return "kurzfassung", titel, briefing
    try:
        anzahl = int(str(form.get("anzahl") or KI_VORGABE).strip())
    except ValueError:
        anzahl = 0
    if not KI_MIN <= anzahl <= KI_MAX:
        raise Abgelehnt(tx(s, "f_anzahl", von=KI_MIN, bis=KI_MAX))
    briefing["ki"] = {"anzahl": anzahl}
    return "bilder", titel, briefing


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
        if str(form.get("art") or "bilder") == "bilder":
            namen = await run_in_threadpool(daten.bilder, str(firma))
            titel, briefing = reel_briefing(form, s, namen)
            await run_in_threadpool(daten.reel_bestellen, str(firma), titel, briefing)
        else:
            kennungen: list[str] = []
            if form.get("art") in ("material", "kurzfassung"):
                material = await run_in_threadpool(daten.material)
                kennungen = [str(v.get("kennung")) for v in material.get("videos") or []
                             if isinstance(v, dict) and v.get("kennung")]
            art, titel, briefing = reel_auftrag(form, s, kennungen)
            await run_in_threadpool(daten.reel_bestellen_art, str(firma), art, titel, briefing)
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


async def reel_ki_neu(request: Request, aid: str, bild: str) -> Response:
    """Ein KI-Bild neu erzeugen (kostet: hinter dem Schalter). Bild und Reel muessen zur Firma gehoeren."""
    fehler, s, form, firma = await _anfang(request, kostet=True)
    if fehler is not None:
        return fehler
    try:
        a = await _reel_der_firma(str(firma), aid, s)
        if a.get("status") != "bilder_pruefen":
            raise Abgelehnt(tx(s, "f_status"))
        ids = {str(b.get("id")) for b in a.get("ki_bilder") or [] if isinstance(b, dict)}
        if not _gueltige_id(bild) or bild not in ids:
            raise Abgelehnt(tx(s, "f_bild"))
        idee = _text(form, "idee", IDEE_MAX, s, tx(s, "ki_idee"), False, False)
        await run_in_threadpool(daten.reel_ki_neu, str(firma), aid, bild, idee)
    except Abgelehnt as f:
        return huelle.abgelehnt(s, str(f))
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return RedirectResponse(f"{REELS}?reiter=arbeit&ki_neu_ok=1", status_code=303)


async def reel_ki_freigeben(request: Request, aid: str) -> Response:
    """Die fertigen KI-Bilder freigeben; danach baut der Video-Claw das Reel (kostet: hinter dem Schalter)."""
    fehler, s, _form, firma = await _anfang(request, kostet=True)
    if fehler is not None:
        return fehler
    try:
        a = await _reel_der_firma(str(firma), aid, s)
        if a.get("status") != "bilder_pruefen":
            raise Abgelehnt(tx(s, "f_status"))
        await run_in_threadpool(daten.reel_ki_freigeben, str(firma), aid)
    except Abgelehnt as f:
        return huelle.abgelehnt(s, str(f))
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return RedirectResponse(f"{REELS}?reiter=arbeit&ki_frei_ok=1", status_code=303)
