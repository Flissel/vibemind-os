"""Marken-Profil. Vorbild: sales-claw ui_marke, Reiter "Profil".

Farben, Schriften, Logos (als data:-URL aus dem Spiegel), Stand, Stimme, Pflichtangaben und die
Abschnitte der Marke.md. Die Abschnitte und ihre Namen sind Inhalt und bleiben in ihrer Sprache.
Daten: GET /api/pult/marke.

Seit Stufe B, Tag 3 (25.11.2026) auch bearbeitbar, ohne Skript und wie in Sales nie direkt:
- "Profil bearbeiten" legt nur einen Bearbeitungs-Auftrag an (/api/pult/marke/bearbeiten). Der
  Marken-Assistent macht daraus einen Vorschlag; reine Textaenderungen darf er wie bisher selbst
  uebernehmen.
- Ein offener Vorschlag steht oben mit seinen Aenderungen (vorher/nachher) und wird hier
  uebernommen oder verworfen. Die Datenbank prueft dabei die Firma noch einmal.
- Logo, Pflichtangaben, Rueckgaengig und der Marken-Chat bleiben vorerst in Sales."""
from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.oberflaeche import daten, huelle
from spaces.marketing.api.oberflaeche.huelle import PFAD, Kontext, e, farbfeld
from spaces.marketing.claw.markenprofil import ABSCHNITT_REIHENFOLGE
from spaces.marketing.claw.schriften import REGISTER as SCHRIFTEN

LOGO_BASE64_MAX = 210_000
_BILD_DATEN = re.compile(rf"data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=]{{1,{LOGO_BASE64_MAX}}}")
_SCHRIFT = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
PFLICHT = ("impressum", "vermittlerangaben", "abmelde_hinweis")
SEITE = f"{PFAD}/marke"
VON = "Betreiber (Marketing-Reiter)"
# Dieselben Grenzen wie api/marke.py (FORMULAR_GRENZEN, FORMULAR_OPTIONAL, ABSCHNITT_FORM_MAX); ein Test
# vergleicht sie, damit das Formular nie mehr annimmt als die API.
WERTE = {"akzent": 20, "zweitfarbe": 20, "grund": 20, "text": 20, "schrift_anzeige": 40, "schrift_text": 40,
         "webseite": 300, "stimme": 40}
FARBEN = ("akzent", "zweitfarbe", "grund", "text")
ABSCHNITT_MAX = 8000
FORM_GRENZE = 256 * 1024

TEXTE = {
    "de": {
        "titel": "Marke",
        "kein_profil": "Noch kein Branding für diese Firma.",
        "farben": "Farben",
        "f_akzent": "Akzent", "f_zweitfarbe": "Fläche", "f_grund": "Grund", "f_text": "Text",
        "schriften": "Schriften",
        "s_anzeige": "Überschrift: {name}",
        "s_text": "Text: {name}",
        "logo": "Logo",
        "logo_dunkel": "Logo für dunkle Flächen",
        "stand": "Stand: {stand}",
        "spiegel_nie": "Spiegel noch nie gesetzt: {grund}",
        "spiegel_alt": "Spiegel veraltet seit {datum}: {grund}",
        "stimme": "Stimme für Reels und Podcast",
        "keine_stimme": "Noch keine festgelegt",
        "pflicht": "Pflichtangaben",
        "p_impressum": "Impressum",
        "p_vermittlerangaben": "Vermittlerangaben",
        "p_abmelde_hinweis": "Abmeldehinweis",
        "fehlt": "fehlt",
        "abschnitte": "Profil",
        "noch_leer": "Noch leer",
        # Stufe B, Tag 3
        "hinweis": "Hier bearbeitest du das Markenprofil. Jede Änderung geht als Auftrag an den "
                   "Marken-Assistenten; du übernimmst oder verwirfst seinen Vorschlag.",
        "bearbeiten": "Profil bearbeiten",
        "bearbeiten_hinweis": "Der Assistent prüft die Angaben und macht daraus einen Vorschlag. Reine "
                              "Textänderungen übernimmt er selbst, alles andere wartet hier auf dich.",
        "w_akzent": "Akzentfarbe", "w_zweitfarbe": "Flächenfarbe", "w_grund": "Grundfarbe", "w_text": "Textfarbe",
        "w_schrift_anzeige": "Schrift für Überschriften", "w_schrift_text": "Schrift für Text",
        "w_webseite": "Webseite", "w_stimme": "Stimme für Reels und Podcast",
        "keine_schrift": "keine",
        "senden": "An den Assistenten geben",
        "gesendet": "Auftrag angelegt. Der Assistent arbeitet daran; sein Vorschlag erscheint hier.",
        "unveraendert": "Nichts geändert, kein Auftrag angelegt.",
        "laeuft": "Der Assistent arbeitet gerade an einem Auftrag.",
        "uebernahme_laeuft": "Eine Übernahme läuft; das Profil ist gleich auf dem neuen Stand.",
        "vorschlag": "Vorschlag des Assistenten",
        "vorschlag_rueckgaengig": "Dieser Vorschlag macht eine frühere Änderung rückgängig.",
        "vorschlag_logo": "Der Vorschlag ändert auch das Logo.",
        "vorschlag_ohne_liste": "Der Vorschlag nennt keine Änderungsliste.",
        "feld": "Feld", "vorher": "Vorher", "nachher": "Nachher",
        "uebernehmen": "Übernehmen",
        "verwerfen": "Verwerfen",
        "uebernommen": "Vorschlag übernommen. Die Übernahme läuft.",
        "verworfen": "Vorschlag verworfen.",
        "f_zu_lang": "Ein Feld ist zu lang ({feld}, höchstens {max} Zeichen).",
        "f_vorschlag_weg": "Diesen Vorschlag gibt es nicht mehr offen. Bitte die Seite neu laden.",
    },
    "en": {
        "titel": "Brand",
        "kein_profil": "No branding for this company yet.",
        "farben": "Colours",
        "f_akzent": "Accent", "f_zweitfarbe": "Surface", "f_grund": "Background", "f_text": "Text",
        "schriften": "Fonts",
        "s_anzeige": "Headings: {name}",
        "s_text": "Body: {name}",
        "logo": "Logo",
        "logo_dunkel": "Logo for dark backgrounds",
        "stand": "As of: {stand}",
        "spiegel_nie": "Mirror never set: {grund}",
        "spiegel_alt": "Mirror out of date since {datum}: {grund}",
        "stimme": "Voice for reels and podcast",
        "keine_stimme": "None chosen yet",
        "pflicht": "Legal information",
        "p_impressum": "Imprint",
        "p_vermittlerangaben": "Intermediary details",
        "p_abmelde_hinweis": "Unsubscribe notice",
        "fehlt": "missing",
        "abschnitte": "Profile",
        "noch_leer": "Still empty",
        "hinweis": "Edit the brand profile here. Every change goes to the brand assistant as a task; you "
                   "accept or discard its suggestion.",
        "bearbeiten": "Edit profile",
        "bearbeiten_hinweis": "The assistant checks the details and turns them into a suggestion. It applies pure "
                              "text changes itself; everything else waits here for you.",
        "w_akzent": "Accent colour", "w_zweitfarbe": "Surface colour", "w_grund": "Background colour",
        "w_text": "Text colour", "w_schrift_anzeige": "Heading font", "w_schrift_text": "Body font",
        "w_webseite": "Website", "w_stimme": "Voice for reels and podcast",
        "keine_schrift": "none",
        "senden": "Send to the assistant",
        "gesendet": "Task created. The assistant is working on it; its suggestion will appear here.",
        "unveraendert": "Nothing changed, no task created.",
        "laeuft": "The assistant is working on a task right now.",
        "uebernahme_laeuft": "A takeover is running; the profile will be up to date shortly.",
        "vorschlag": "Suggestion from the assistant",
        "vorschlag_rueckgaengig": "This suggestion undoes an earlier change.",
        "vorschlag_logo": "The suggestion also changes the logo.",
        "vorschlag_ohne_liste": "The suggestion has no list of changes.",
        "feld": "Field", "vorher": "Before", "nachher": "After",
        "uebernehmen": "Accept",
        "verwerfen": "Discard",
        "uebernommen": "Suggestion accepted. The takeover is running.",
        "verworfen": "Suggestion discarded.",
        "f_zu_lang": "A field is too long ({feld}, at most {max} characters).",
        "f_vorschlag_weg": "This suggestion is no longer open. Please reload the page.",
    },
}


def _dict(wert) -> dict:
    return wert if isinstance(wert, dict) else {}


def profil_quelle(d: dict) -> tuple[dict, dict]:
    """(werte, abschnitte): das echte Profil der Marke.md, sonst der zuletzt uebernommene Vorschlag."""
    p = d.get("profil") if isinstance(d.get("profil"), dict) else _dict(d.get("aktuell"))
    return _dict(p.get("werte")), _dict(p.get("abschnitte"))


def abschnitt_namen(d: dict, abschnitte: dict) -> list[str]:
    namen = [n for n in d.get("abschnitt_namen") or [] if isinstance(n, str) and n.strip()] \
        if isinstance(d.get("abschnitt_namen"), list) else []
    return namen + [n for n in abschnitte if isinstance(n, str) and n not in namen]


def _absatz(text: str) -> str:
    return e(text).replace("\n", "<br>")


def profil_html(k: Kontext, d: dict) -> str:
    sp = _dict(d.get("spiegel"))
    g = _dict(sp.get("gestalt"))
    stand = str(sp.get("stand") or "")
    werte, ab = profil_quelle(d)
    teile = []
    if not g and not stand:
        teile.append(f'<p><b>{e(k.t(TEXTE, "kein_profil"))}</b></p>')
    else:
        quelle = {"akzent": g.get("akzent"), "zweitfarbe": g.get("flaeche"),
                  "grund": werte.get("grund"), "text": werte.get("text")}
        farben = "".join(
            f'<span class="farbe">{farbfeld(quelle[n])}{e(k.t(TEXTE, "f_" + n))} {e(str(quelle[n]))}</span>'
            for n in ("akzent", "zweitfarbe", "grund", "text") if farbfeld(quelle[n]))
        if farben:
            teile.append(f'<h2>{e(k.t(TEXTE, "farben"))}</h2><div class="farben">{farben}</div>')
        schrift = _dict(g.get("schriften"))
        zeilen = [k.t(TEXTE, "s_" + art, name=str(schrift[art])) for art in ("anzeige", "text")
                  if isinstance(schrift.get(art), str) and _SCHRIFT.fullmatch(schrift[art])]
        if zeilen:
            teile.append(f'<h2>{e(k.t(TEXTE, "schriften"))}</h2><p>{"<br>".join(e(z) for z in zeilen)}</p>')
        logos = []
        for schluessel, klasse in (("logo", ""), ("logo_dunkel", "dunkel")):
            bild = str(g.get(schluessel) or "")
            if _BILD_DATEN.fullmatch(bild):
                logos.append(f'<div class="{klasse}"><img src="{bild}" alt="{e(k.t(TEXTE, schluessel))}"></div>')
        if logos:
            teile.append(f'<div class="logos">{"".join(logos)}</div>')
        if stand:
            teile.append(f'<p class="meta">{e(k.t(TEXTE, "stand", stand=stand))}</p>')
    if sp.get("fehler"):
        grund = str(sp["fehler"])
        satz = (k.t(TEXTE, "spiegel_alt", datum=k.datum(sp.get("gespiegelt_am")), grund=grund)
                if sp.get("gespiegelt_am") else k.t(TEXTE, "spiegel_nie", grund=grund))
        teile.append(f'<p class="fehler">{e(satz)}</p>')
    hinweise = [str(h) for h in d.get("profil_hinweise") or [] if str(h).strip()]
    if hinweise:
        teile.append('<ul class="fehler">' + "".join(f"<li>{e(h)}</li>" for h in hinweise) + "</ul>")

    stimme = werte.get("stimme") if isinstance(werte.get("stimme"), str) else ""
    karten = [f'<article class="karte"><h3>{e(k.t(TEXTE, "stimme"))}</h3>'
              + (f'<p>{e(stimme)}</p>' if stimme.strip() else f'<p class="leer">{e(k.t(TEXTE, "keine_stimme"))}</p>')
              + '</article>']
    pflicht = _dict(d.get("pflichtteil"))
    angaben = ""
    for p in PFLICHT:
        wert = pflicht.get(p).strip() if isinstance(pflicht.get(p), str) else ""
        angaben += (f'<dt>{e(k.t(TEXTE, "p_" + p))}</dt>'
                    + (f'<dd>{e(wert)}</dd>' if wert else f'<dd class="leer">{e(k.t(TEXTE, "fehlt"))}</dd>'))
    karten.append(f'<article class="karte"><h3>{e(k.t(TEXTE, "pflicht"))}</h3>'
                  f'<dl class="angaben">{angaben}</dl></article>')
    for n in abschnitt_namen(d, ab):
        text = ab.get(n) if isinstance(ab.get(n), str) else ""
        karten.append(f'<article class="karte"><h3>{e(n)}</h3>'
                      + (f'<p>{_absatz(text)}</p>' if text.strip() else f'<p class="leer">{e(k.t(TEXTE, "noch_leer"))}</p>')
                      + '</article>')
    return "".join(teile) + f'<h2>{e(k.t(TEXTE, "abschnitte"))}</h2><div class="karten">{"".join(karten)}</div>'


def tx(s: str, schluessel: str, **werte) -> str:
    return huelle._sprache.text(TEXTE, s, schluessel, **werte)


def _csrf() -> str:
    return f'<input type="hidden" name="csrf" value="{e(huelle.csrf_marke())}">'


def formular_werte(d: dict) -> tuple[dict, dict]:
    """Vorbefuellung: (werte, abschnitte) aus dem Profil, Farben und Schriften sonst aus dem Spiegel."""
    werte, ab = profil_quelle(d)
    g = _dict(_dict(d.get("spiegel")).get("gestalt"))
    schrift = _dict(g.get("schriften"))
    ersatz = {"akzent": g.get("akzent"), "zweitfarbe": g.get("flaeche"),
              "schrift_anzeige": schrift.get("anzeige"), "schrift_text": schrift.get("text")}
    aus = {}
    for name in WERTE:
        wert = werte.get(name) if isinstance(werte.get(name), str) else ersatz.get(name)
        aus[name] = wert.strip() if isinstance(wert, str) else ""
    return aus, {n: (ab[n].strip() if isinstance(ab.get(n), str) else "") for n in ABSCHNITT_REIHENFOLGE}


def _schrift_wahl(k: Kontext, name: str, aktuell: str) -> str:
    namen = sorted(SCHRIFTEN)
    if aktuell and aktuell not in namen and _SCHRIFT.fullmatch(aktuell):
        namen.insert(0, aktuell)
    optionen = [("", k.t(TEXTE, "keine_schrift"))] + [(n, SCHRIFTEN.get(n, {}).get("familie", n)) for n in namen]
    return (f'<select name="{name}">' + "".join(
        f'<option value="{e(w)}"{" selected" if w == aktuell else ""}>{e(str(t))}</option>' for w, t in optionen)
        + "</select>")


def bearbeiten_html(k: Kontext, d: dict) -> str:
    werte, ab = formular_werte(d)
    felder = []
    for name, grenze in WERTE.items():
        if name.startswith("schrift_"):
            eingabe = _schrift_wahl(k, name, werte[name])
        else:
            eingabe = f'<input type="text" name="{name}" value="{e(werte[name])}" maxlength="{grenze}">'
        vorschau = farbfeld(werte[name]) if name in FARBEN else ""
        felder.append(f'<label><span>{vorschau} {e(k.t(TEXTE, "w_" + name))}</span> {eingabe}</label>')
    abschnitte = "".join(
        f'<label>{e(n)} <textarea name="ab{i}" rows="4" maxlength="{ABSCHNITT_MAX}">{e(ab[n])}</textarea></label>'
        for i, n in enumerate(ABSCHNITT_REIHENFOLGE))
    return (f'<details class="aufklapp"><summary>{e(k.t(TEXTE, "bearbeiten"))}</summary>'
            f'<form method="post" action="{SEITE}/bearbeiten" class="editor">{_csrf()}'
            f'<p class="meta">{e(k.t(TEXTE, "bearbeiten_hinweis"))}</p>'
            f'<div class="zeile">{"".join(felder)}</div>'
            f'<h3>{e(k.t(TEXTE, "abschnitte"))}</h3>{abschnitte}'
            f'<p><button type="submit">{e(k.t(TEXTE, "senden"))}</button></p></form></details>')


def _aenderungen(inhalt: dict) -> list[dict]:
    roh = inhalt.get("aenderungen")
    return [a for a in roh if isinstance(a, dict) and isinstance(a.get("feld"), str)] if isinstance(roh, list) else []


def vorschlag_html(k: Kontext, d: dict) -> str:
    v = _dict(d.get("vorschlag"))
    vid = str(v.get("id") or "")
    if not vid:
        return ""
    inhalt = _dict(v.get("vorschlag"))
    liste = _aenderungen(inhalt)
    teile = [f'<h2>{e(k.t(TEXTE, "vorschlag"))}</h2>']
    if v.get("ist_rueckgaengig"):
        teile.append(f'<p class="meta">{e(k.t(TEXTE, "vorschlag_rueckgaengig"))}</p>')
    if any(inhalt.get(s) is not None for s in ("logo", "logo_bearbeiten")):
        teile.append(f'<p class="meta">{e(k.t(TEXTE, "vorschlag_logo"))}</p>')
    if liste:
        zeilen = "".join(
            f'<tr><td>{e(k.t(TEXTE, "w_" + a["feld"]) if a["feld"] in WERTE else a["feld"])}</td>'
            f'<td>{e(str(a.get("vorher") or ""))}</td><td>{e(str(a.get("nachher") or ""))}</td></tr>' for a in liste)
        teile.append(f'<table class="aenderungen"><thead><tr><th>{e(k.t(TEXTE, "feld"))}</th>'
                     f'<th>{e(k.t(TEXTE, "vorher"))}</th><th>{e(k.t(TEXTE, "nachher"))}</th></tr></thead>'
                     f'<tbody>{zeilen}</tbody></table>')
    else:
        teile.append(f'<p class="meta">{e(k.t(TEXTE, "vorschlag_ohne_liste"))}</p>')
    kopf = f'{_csrf()}<input type="hidden" name="id" value="{e(vid)}">'
    teile.append(f'<form method="post" action="{SEITE}/vorschlag" class="anlegen">{kopf}'
                 f'<button type="submit" name="wahl" value="uebernehmen">{e(k.t(TEXTE, "uebernehmen"))}</button>'
                 f'<button type="submit" name="wahl" value="verwerfen" class="leise">{e(k.t(TEXTE, "verwerfen"))}'
                 '</button></form>')
    return '<section class="karte">' + "".join(teile) + "</section>"


def _meldung(k: Kontext) -> str:
    q = k.request.query_params
    for schluessel in ("gesendet", "unveraendert", "uebernommen", "verworfen"):
        if q.get(schluessel) == "1":
            return f'<p class="erfolg">{e(k.t(TEXTE, schluessel))}</p>'
    return ""


async def bauen(k: Kontext) -> tuple[str, str]:
    k.hinweis = k.t(TEXTE, "hinweis")
    d = await run_in_threadpool(daten.marke, k.firma)
    name = str(d.get("name") or "").strip()
    titel = k.t(TEXTE, "titel") + (f" · {name}" if name else "")
    stand = []
    if d.get("laeuft"):
        stand.append(f'<p class="meta">{e(k.t(TEXTE, "laeuft"))}</p>')
    if d.get("uebernahme") == "laeuft":
        stand.append(f'<p class="meta">{e(k.t(TEXTE, "uebernahme_laeuft"))}</p>')
    return titel, (_meldung(k) + "".join(stand) + vorschlag_html(k, d) + bearbeiten_html(k, d)
                   + profil_html(k, d))


# --- Formulare ----------------------------------------------------------------------------------

class Abgelehnt(ValueError):
    """Eingabe passt nicht; die Nachricht ist schon der Satz fuer die Seite."""


def formular_lesen(form: dict, s: str) -> dict:
    """Das Formular in der Form, die /api/pult/marke/bearbeiten erwartet. Fehlende Felder sind leer."""
    aus: dict = {}
    for name, grenze in WERTE.items():
        wert = " ".join(str(form.get(name) or "").split())
        if len(wert) > grenze:
            raise Abgelehnt(tx(s, "f_zu_lang", feld=tx(s, "w_" + name), max=grenze))
        aus[name] = wert
    ab = {}
    for i, n in enumerate(ABSCHNITT_REIHENFOLGE):
        text = str(form.get(f"ab{i}") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(text) > ABSCHNITT_MAX:
            raise Abgelehnt(tx(s, "f_zu_lang", feld=n, max=ABSCHNITT_MAX))
        ab[n] = text
    aus["abschnitte"] = ab
    return aus


def unveraendert(neu: dict, d: dict) -> bool:
    werte, ab = formular_werte(d)
    gleich = all((neu[n].lower() == werte[n].lower()) if n in FARBEN else neu[n] == werte[n] for n in WERTE)
    return gleich and neu["abschnitte"] == ab


async def bearbeiten(request: Request) -> Response:
    fehler, s, form, firma = await huelle.formular_mit_firma(request, FORM_GRENZE)
    if fehler is not None:
        return fehler
    try:
        neu = formular_lesen(form, s)
    except Abgelehnt as f:
        return huelle.abgelehnt(s, str(f))
    try:
        d = await run_in_threadpool(daten.marke, firma)
        if unveraendert(neu, d):
            return RedirectResponse(f"{SEITE}?unveraendert=1", status_code=303)
        await run_in_threadpool(daten.marke_bearbeiten, firma, neu)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return RedirectResponse(f"{SEITE}?gesendet=1", status_code=303)


async def vorschlag(request: Request) -> Response:
    fehler, s, form, firma = await huelle.formular_mit_firma(request)
    if fehler is not None:
        return fehler
    vid, wahl = str(form.get("id") or ""), str(form.get("wahl") or "")
    if wahl not in ("uebernehmen", "verwerfen"):
        return huelle.abgelehnt(s, tx(s, "f_vorschlag_weg"))
    try:
        d = await run_in_threadpool(daten.marke, firma)
        offen = str(_dict(d.get("vorschlag")).get("id") or "")
        if not vid or vid != offen:
            return huelle.abgelehnt(s, tx(s, "f_vorschlag_weg"))
        if wahl == "uebernehmen":
            await run_in_threadpool(daten.marke_uebernehmen, firma, vid, VON)
        else:
            await run_in_threadpool(daten.marke_verwerfen, firma, vid, VON)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    ziel = "uebernommen" if wahl == "uebernehmen" else "verworfen"
    return RedirectResponse(f"{SEITE}?{ziel}=1", status_code=303)
