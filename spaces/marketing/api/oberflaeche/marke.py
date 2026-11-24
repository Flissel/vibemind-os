"""Marken-Profil (lesend). Vorbild: sales-claw ui_marke, Reiter "Profil".

Farben, Schriften, Logos (als data:-URL aus dem Spiegel), Stand, Stimme, Pflichtangaben und die
Abschnitte der Marke.md. Die Abschnitte und ihre Namen sind Inhalt und bleiben in ihrer Sprache.
Chat, Bearbeiten und Vorschlaege folgen in Stufe B. Daten: GET /api/pult/marke."""
from __future__ import annotations

import re

from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.oberflaeche import daten
from spaces.marketing.api.oberflaeche.huelle import Kontext, e, farbfeld

LOGO_BASE64_MAX = 210_000
_BILD_DATEN = re.compile(rf"data:image/(?:png|jpeg);base64,[A-Za-z0-9+/=]{{1,{LOGO_BASE64_MAX}}}")
_SCHRIFT = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
PFLICHT = ("impressum", "vermittlerangaben", "abmelde_hinweis")

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


async def bauen(k: Kontext) -> tuple[str, str]:
    d = await run_in_threadpool(daten.marke, k.firma)
    name = str(d.get("name") or "").strip()
    titel = k.t(TEXTE, "titel") + (f" · {name}" if name else "")
    return titel, profil_html(k, d)
