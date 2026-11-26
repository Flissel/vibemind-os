"""Teilen im Marketing-Reiter (Stufe C, Punkt 5): ein fertiges Reel oder eine fertige Folge als
Versandauftrag an Sales geben. Vorbild: das Teilen in sales-claw ui_reels.

Hier wird NIE gesendet. Es entsteht ein Versandauftrag (api/teilen.py, marketing.versandauftrag_anlegen);
sales-claw macht daraus hoechstens einen Entwurf, den der Betreiber in Sales freigibt. Alle Kanaele, die
sales-claw annimmt, stehen zur Wahl (Betreiber-Entscheid S7). Teilen steht hinter demselben Kosten-Schalter
wie Bestellen (MARKETING_BESTELLEN, S8): ist er aus, ist das Formular gesperrt und der POST-Weg lehnt mit
422 ab, bevor ein Auftrag entsteht. Die Karte zeigt den Stand, den Sales an den Auftrag zurueckschreibt
(S9): eingereicht, Entwurf bei Sales, abgelehnt mit Grund."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.oberflaeche import bestellen, daten, huelle
from spaces.marketing.api.oberflaeche.huelle import PFAD, Kontext, e

REELS, PODCAST = f"{PFAD}/reels", f"{PFAD}/podcast"
KANAELE_BEITRAG = ("linkedin_post", "bluesky_post", "mastodon_post", "telegram_kanal_post", "discord_post",
                   "devto_post", "youtube_post")
KANAELE_NACHRICHT = ("email", "whatsapp", "linkedin", "telegram")
KANAELE = KANAELE_BEITRAG + KANAELE_NACHRICHT
NACHRICHT_MAX, BETREFF_MAX, EMPFAENGER_MAX = 5000, 300, 300
STAND_KARTE = 5          # so viele Auftraege je Karte, neueste zuerst
FORM_GRENZE = 32 * 1024

TEXTE = {
    "de": {
        "teilen": "Teilen",
        "hinweis": "Es entsteht ein Versandauftrag an Sales. Sales legt daraus einen Entwurf an; "
                   "hinaus geht er erst nach deiner Freigabe in Sales.",
        "kanal": "Kanal",
        "gruppe_beitrag": "Beitrag auf dem eigenen Kanal",
        "gruppe_nachricht": "Nachricht an einen Kontakt",
        "k_linkedin_post": "LinkedIn-Beitrag", "k_bluesky_post": "Bluesky-Beitrag",
        "k_mastodon_post": "Mastodon-Beitrag", "k_telegram_kanal_post": "Telegram-Kanal",
        "k_discord_post": "Discord-Beitrag", "k_devto_post": "dev.to-Artikel", "k_youtube_post": "YouTube-Short",
        "k_email": "E-Mail", "k_whatsapp": "WhatsApp", "k_linkedin": "LinkedIn-Nachricht",
        "k_telegram": "Telegram-Nachricht",
        "empfaenger": "Empfänger (nur bei einer Nachricht: E-Mail, Telefon oder Telegram-Chat)",
        "betreff": "Thema oder Titel (bei Beiträgen Pflicht)",
        "nachricht": "Text",
        "los": "Versandauftrag anlegen",
        "stand": "Geteilt",
        "st_offen": "eingereicht, Sales legt den Entwurf an",
        "st_angenommen": "Entwurf bei Sales, wartet auf deine Freigabe",
        "st_abgelehnt": "von Sales abgelehnt",
        "stand_fehlt": "Wie es mit dem Teilen steht, lässt sich gerade nicht abrufen.",
        "geteilt": "Versandauftrag angelegt. Sales legt daraus einen Entwurf an; gesendet wird erst nach "
                   "deiner Freigabe.",
        "wiederholung": "Derselbe Auftrag besteht schon (letzte 24 Stunden); es wurde keiner dazugelegt.",
        "m_abgesagt": "Nicht angelegt",
        "dein_text": "Dein Text bleibt hier zum Kopieren:",
        "zurueck_seite": "Zurück",
        "f_kanal": "Bitte einen Kanal wählen.",
        "f_feld": "{feld}: höchstens {n} Zeichen.",
        "f_pflicht": "{feld} fehlt.",
        "f_auftrag": "Diesen Auftrag gibt es bei dieser Firma nicht.",
        "f_fertig": "Nur Fertiges lässt sich teilen.",
    },
    "en": {
        "teilen": "Share",
        "hinweis": "This creates a sending request for Sales. Sales turns it into a draft; it only goes out "
                   "after you approve it in Sales.",
        "kanal": "Channel",
        "gruppe_beitrag": "Post on your own channel",
        "gruppe_nachricht": "Message to a contact",
        "k_linkedin_post": "LinkedIn post", "k_bluesky_post": "Bluesky post",
        "k_mastodon_post": "Mastodon post", "k_telegram_kanal_post": "Telegram channel",
        "k_discord_post": "Discord post", "k_devto_post": "dev.to article", "k_youtube_post": "YouTube Short",
        "k_email": "Email", "k_whatsapp": "WhatsApp", "k_linkedin": "LinkedIn message",
        "k_telegram": "Telegram message",
        "empfaenger": "Recipient (messages only: email, phone or Telegram chat)",
        "betreff": "Topic or title (required for posts)",
        "nachricht": "Text",
        "los": "Create sending request",
        "stand": "Shared",
        "st_offen": "submitted, Sales is creating the draft",
        "st_angenommen": "draft at Sales, waiting for your approval",
        "st_abgelehnt": "declined by Sales",
        "stand_fehlt": "The sharing status isn't available right now.",
        "geteilt": "Sending request created. Sales turns it into a draft; nothing is sent before you approve it.",
        "wiederholung": "The same request already exists (last 24 hours); no new one was added.",
        "m_abgesagt": "Not created",
        "dein_text": "Your text stays here so you can copy it:",
        "zurueck_seite": "Back",
        "f_kanal": "Please choose a channel.",
        "f_feld": "{feld}: at most {n} characters.",
        "f_pflicht": "{feld} is missing.",
        "f_auftrag": "This job doesn't exist for this company.",
        "f_fertig": "Only finished items can be shared.",
    },
}


def tx(s: str, schluessel: str, **werte) -> str:
    return huelle._sprache.text(TEXTE, s, schluessel, **werte)


def meldung(k: Kontext) -> str:
    q = k.request.query_params.get("geteilt")
    if q in ("1", "wiederholung"):
        return f'<p class="erfolg">{e(k.t(TEXTE, "geteilt" if q == "1" else "wiederholung"))}</p>'
    return ""


def je_auftrag(stand: list[dict] | None) -> dict[str, list[dict]] | None:
    if stand is None:
        return None
    je: dict[str, list[dict]] = {}
    for z in stand:
        je.setdefault(str(z.get("auftrag") or ""), []).append(z)
    return je


def _stand_html(k: Kontext, eintraege: list[dict] | None) -> str:
    if eintraege is None:
        return f'<p class="meta">{e(k.t(TEXTE, "stand_fehlt"))}</p>'
    zeilen = []
    for z in eintraege[:STAND_KARTE]:
        kanal, st = str(z.get("kanal") or ""), str(z.get("status") or "")
        name = k.t(TEXTE, "k_" + kanal) if kanal in KANAELE else kanal
        satz = k.t(TEXTE, "st_" + st) if st in ("offen", "angenommen", "abgelehnt") else st
        grund = str(z.get("grund") or "").strip() if st == "abgelehnt" else ""
        zeilen.append(f'<li>{e(name)}: {e(satz)} · {e(k.datum(z.get("angelegt")))}'
                      + (f'<br><span class="fehler">{e(grund)}</span>' if grund else "") + "</li>")
    if not zeilen:
        return ""
    return f'<p class="meta">{e(k.t(TEXTE, "stand"))}</p><ul class="geteilt">{"".join(zeilen)}</ul>'


def formular(k: Kontext, art: str, a: dict, stand: dict[str, list[dict]] | None) -> str:
    """Unter einer FERTIGEN Karte: Stand des Teilens und das Formular (gesperrt, wenn der Schalter aus ist)."""
    aid = str(a.get("id") or "")
    if a.get("status") != "fertig" or not bestellen._gueltige_id(aid) or a.get("archiviert"):
        return ""
    ziel = f"{REELS if art == 'reel' else PODCAST}/{aid}/teilen"
    sperre = "" if bestellen.an() else " disabled"
    satz = "" if bestellen.an() else f'<p class="meta">{e(k.t(bestellen.TEXTE, "aus_kurz"))}</p>'
    beitrag = "".join(f'<option value="{x}">{e(k.t(TEXTE, "k_" + x))}</option>' for x in KANAELE_BEITRAG)
    nachricht = "".join(f'<option value="{x}">{e(k.t(TEXTE, "k_" + x))}</option>' for x in KANAELE_NACHRICHT)
    eintraege = None if stand is None else stand.get(aid, [])
    return (_stand_html(k, eintraege)
            + f'<details class="aufklapp bestellen teilen"><summary>{e(k.t(TEXTE, "teilen"))}</summary>{satz}'
              f'<form method="post" action="{ziel}" class="editor">{bestellen._csrf()}<fieldset{sperre}>'
              f'<p class="meta">{e(k.t(TEXTE, "hinweis"))}</p>'
              f'<label>{e(k.t(TEXTE, "kanal"))} <select name="kanal" required>'
              f'<optgroup label="{e(k.t(TEXTE, "gruppe_beitrag"))}">{beitrag}</optgroup>'
              f'<optgroup label="{e(k.t(TEXTE, "gruppe_nachricht"))}">{nachricht}</optgroup></select></label>'
              f'<label>{e(k.t(TEXTE, "betreff"))} <input type="text" name="betreff" maxlength="{BETREFF_MAX}"></label>'
              f'<label>{e(k.t(TEXTE, "empfaenger"))} <input type="text" name="empfaenger" '
              f'maxlength="{EMPFAENGER_MAX}"></label>'
              f'<label>{e(k.t(TEXTE, "nachricht"))} <textarea name="nachricht" maxlength="{NACHRICHT_MAX}" rows="5" '
              f'required></textarea></label>'
              f'<p><button type="submit">{e(k.t(TEXTE, "los"))}</button></p></fieldset></form></details>')


# --- POST-Wege ----------------------------------------------------------------------------------------

def _feld(form: dict, name: str, grenze: int, s: str, pflicht: bool, einzeilig: bool) -> str:
    roh = str(form.get(name) or "").replace("\r\n", "\n")
    wert = " ".join(roh.split()) if einzeilig else roh.strip()
    if pflicht and not wert:
        raise bestellen.Abgelehnt(tx(s, "f_pflicht", feld=tx(s, name)))
    if len(wert) > grenze:
        raise bestellen.Abgelehnt(tx(s, "f_feld", feld=tx(s, name), n=grenze))
    return wert


def _abgesagt(s: str, ziel: str, grund: str, nachricht: str) -> Response:
    """Die DB-Funktion hat abgelehnt: Grund zeigen, der Text bleibt zum Kopieren (nichts geht verloren)."""
    titel = tx(s, "m_abgesagt")
    kopf = f'<nav class="seite" aria-label="{e(huelle.t(s, "menue"))}">{huelle._marke(s)}</nav>'
    rumpf = (f'<h1>{e(titel)}</h1><p class="fehler">{e(grund)}</p>'
             + (f'<p class="meta">{e(tx(s, "dein_text"))}</p><textarea readonly rows="6">{e(nachricht)}</textarea>'
                if nachricht else "")
             + f'<p><a href="{e(ziel)}">{e(tx(s, "zurueck_seite"))}</a></p>')
    return huelle._koepfe(HTMLResponse(huelle._dokument(s, titel, kopf, rumpf), status_code=422), s)


async def _teilen(request: Request, art: str, aid: str) -> Response:
    seite = REELS if art == "reel" else PODCAST
    fehler, s, form, firma = await huelle.formular_mit_firma(request, FORM_GRENZE)
    if fehler is not None:
        return fehler
    if not bestellen.an():
        return huelle.abgelehnt(s, bestellen.tx(s, "aus_kurz"))
    try:
        if not bestellen._gueltige_id(aid):
            raise bestellen.Abgelehnt(tx(s, "f_auftrag"))
        liste = (await run_in_threadpool(daten.reels, str(firma), False) if art == "reel"
                 else await run_in_threadpool(daten.podcast, str(firma)))
        eintrag = next((a for a in liste if str(a.get("id")) == aid), None)
        if eintrag is None:
            raise bestellen.Abgelehnt(tx(s, "f_auftrag"))
        if eintrag.get("status") != "fertig":
            raise bestellen.Abgelehnt(tx(s, "f_fertig"))
        kanal = str(form.get("kanal") or "")
        if kanal not in KANAELE:
            raise bestellen.Abgelehnt(tx(s, "f_kanal"))
        betreff = _feld(form, "betreff", BETREFF_MAX, s, False, True)
        empfaenger = _feld(form, "empfaenger", EMPFAENGER_MAX, s, False, True)
        nachricht = _feld(form, "nachricht", NACHRICHT_MAX, s, True, False)
        antwort = await run_in_threadpool(daten.teilen, str(firma), art, aid, kanal, empfaenger, betreff, nachricht)
    except bestellen.Abgelehnt as f:
        return huelle.abgelehnt(s, str(f))
    except daten.DatenFehler as f:
        if f.art == "abgelehnt":
            return _abgesagt(s, seite, f.grund, str(form.get("nachricht") or "").strip())
        return huelle.daten_fehlerseite(s, f)
    if not antwort.get("ok"):
        return _abgesagt(s, seite, str(antwort.get("grund") or ""), nachricht)
    marke = "wiederholung" if antwort.get("wiederholung") else "1"
    return RedirectResponse(f"{seite}{'?reiter=fertig&' if art == 'reel' else '?'}geteilt={marke}", status_code=303)


async def reel_teilen(request: Request, aid: str) -> Response:
    return await _teilen(request, "reel", aid)


async def folge_teilen(request: Request, aid: str) -> Response:
    return await _teilen(request, "folge", aid)
