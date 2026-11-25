"""Podcast-Liste (lesend). Vorbild: sales-claw ui_podcast.podcast.

Je Folge Status, Titel, Datum, Inhaltssprache, Sprecher, Laenge, Befund und das Skript zum
Aufklappen. Seit Stufe B, Tag 4 spielen fertige Folgen direkt in der Karte (medien.py, eigene
Medienablage je Firma); der Freigabestand kommt mit Stufe C. Daten: GET /api/pult/ton."""
from __future__ import annotations

from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.oberflaeche import daten, medien
from spaces.marketing.api.oberflaeche.huelle import Kontext, e

STATUS = ("offen", "in_arbeit", "fertig", "fehler", "abgebrochen")
CHIP = {"offen": "", "in_arbeit": "warn", "fertig": "gut", "fehler": "schlecht", "abgebrochen": ""}

TEXTE = {
    "de": {
        "titel": "Podcast",
        "folgen": "Folgen",
        "st_offen": "wartet", "st_in_arbeit": "wird gesprochen", "st_fertig": "fertig", "st_fehler": "Fehler",
        "st_abgebrochen": "abgebrochen",
        "agent": "vom Agenten",
        "betreiber": "von dir",
        "sprache_inhalt": "Sprache der Folge: {code}",
        "sprecher": "Sprecher: {namen}",
        "laenge": "{zeit} min",
        "skript": "Skript",
        "leer": "Noch keine Folgen.",
        "anhoeren": "Fertige Folgen spielen direkt hier. Bestellen und Versenden gehen vorerst in der Sales-Oberfläche.",
        "hinweis": "Podcast-Folgen der Firma anhören. Versendet wird hier nichts.",
    },
    "en": {
        "titel": "Podcast",
        "folgen": "Episodes",
        "st_offen": "waiting", "st_in_arbeit": "being recorded", "st_fertig": "finished", "st_fehler": "error",
        "st_abgebrochen": "cancelled",
        "agent": "by the agent",
        "betreiber": "by you",
        "sprache_inhalt": "Episode language: {code}",
        "sprecher": "Speakers: {namen}",
        "laenge": "{zeit} min",
        "skript": "Script",
        "leer": "No episodes yet.",
        "anhoeren": "Finished episodes play right here. Ordering and sending still happen in the Sales interface for now.",
        "hinweis": "Listen to the company's podcast episodes. Nothing is sent from here.",
    },
}


def laenge(sekunden) -> str:
    if isinstance(sekunden, bool):
        return ""
    try:
        s = int(round(float(sekunden)))
    except (TypeError, ValueError):
        return ""
    return f"{s // 60}:{s % 60:02d}" if s > 0 else ""


def karte(k: Kontext, a: dict) -> str:
    st = str(a.get("status") or "")
    skript = [x for x in a.get("skript") or [] if isinstance(x, dict)] if isinstance(a.get("skript"), list) else []
    sprecher = list(dict.fromkeys(str(x.get("sprecher") or "").strip() for x in skript if x.get("sprecher")))
    meta = [k.datum(a.get("erstellt_am")), k.t(TEXTE, "agent" if a.get("angelegt_von") == "agent" else "betreiber")]
    code = str(a.get("sprache") or "").strip()
    if code:
        meta.append(k.t(TEXTE, "sprache_inhalt", code=code))
    if sprecher:
        meta.append(k.t(TEXTE, "sprecher", namen=", ".join(sprecher)))
    zeit = laenge(a.get("dauer_s")) if st == "fertig" else ""
    if zeit:
        meta.append(k.t(TEXTE, "laenge", zeit=zeit))
    chip = f'<span class="chip {CHIP[st]}">{e(k.t(TEXTE, "st_" + st))}</span>' if st in STATUS else ""
    befund = str(a.get("befund") or "").strip()
    text = "\n".join(f'{x.get("sprecher") or ""}: {x.get("text") or ""}' for x in skript)
    titel = str(a.get("titel") or "")
    spieler = medien.audio_html(str(k.firma or ""), a.get("datei"), titel) if st == "fertig" else ""
    return (f'<article class="karte"><p class="titel">{e(titel)}</p><p>{chip}</p>'
            + (f'<div class="abspielen">{spieler}</div>' if spieler else "")
            + f'<p class="meta">{e(" · ".join(x for x in meta if x))}</p>'
            + (f'<p class="fehler">{e(befund)}</p>' if befund else "")
            + (f'<details><summary>{e(k.t(TEXTE, "skript"))}</summary><pre>{e(text)}</pre></details>' if text else "")
            + '</article>')


async def bauen(k: Kontext) -> tuple[str, str]:
    k.hinweis = k.t(TEXTE, "hinweis")
    folgen = await run_in_threadpool(daten.podcast, k.firma)
    karten = "".join(karte(k, a) for a in folgen)
    inhalt = f'<div class="karten">{karten}</div>' if karten else f'<p class="leer">{e(k.t(TEXTE, "leer"))}</p>'
    return k.t(TEXTE, "titel"), (f'<p class="meta">{e(k.t(TEXTE, "anhoeren"))}</p>'
                                 f'<h2>{e(k.t(TEXTE, "folgen"))}</h2>{inhalt}')
