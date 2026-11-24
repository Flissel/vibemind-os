"""Kampagnenplan (lesend). Vorbild: sales-claw ui_plan.plan.

Oben die Kampagnen der Firma mit ihren Beitraegen, darunter der Kalender eines Monats als Liste
nach Tagen, mit Reitern je Art und Monatsleiste. Ein Planeintrag versendet nichts. Bestaetigen,
Verschieben und Anlegen folgen in Stufe B. Daten: /api/pult/plan und /api/pult/kampagnen."""
from __future__ import annotations

import urllib.parse
from datetime import date, timedelta

from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.oberflaeche import daten
from spaces.marketing.api.oberflaeche.huelle import PFAD, Kontext, e, reiter, tag

SEITE = f"{PFAD}/plan"
ARTEN = ("newsletter", "post", "podcast", "video", "aktion", "sonstiges")
STATUS = ("vorschlag", "geplant", "verschoben", "erledigt", "abgesagt")
STATUS_CHIP = {"vorschlag": "akzent", "geplant": "", "verschoben": "warn", "erledigt": "gut", "abgesagt": "schlecht"}
K_STATUS = ("in_planung", "geplant", "laeuft", "abgeschlossen", "abgesagt")
ZIELGRUPPEN = ("sales", "oeffentlich", "verteiler")

TEXTE = {
    "de": {
        "titel": "Kampagnenplan",
        "kampagnen": "Kampagnen",
        "keine_kampagnen": "Noch keine Kampagne angelegt.",
        "kalender": "Kalender",
        "art": "Art",
        "alle": "Alle",
        "art_newsletter": "Newsletter", "art_post": "Post", "art_podcast": "Podcast", "art_video": "Video",
        "art_aktion": "Aktion", "art_sonstiges": "Sonstiges",
        "st_vorschlag": "Vorschlag vom Agenten", "st_geplant": "geplant", "st_verschoben": "verschoben",
        "st_erledigt": "erledigt", "st_abgesagt": "abgesagt",
        "k_in_planung": "in Planung", "k_geplant": "geplant", "k_laeuft": "läuft",
        "k_abgeschlossen": "abgeschlossen", "k_abgesagt": "abgesagt",
        "z_sales": "Sales-Kontakte", "z_oeffentlich": "öffentlich", "z_verteiler": "Verteiler",
        "zeitraum": "{von} bis {bis}",
        "zielgruppe": "Zielgruppe: {gruppe}",
        "beitraege": "Beiträge: {n}",
        "keine_beitraege": "Noch keine Beiträge.",
        "vorschlaege": "Vorschläge vom Agenten in diesem Monat: {n}. Bestätigt wird vorerst in der Sales-Oberfläche.",
        "nichts_wartet": "Nichts wartet auf eine Bestätigung.",
        "vormonat": "‹ Vormonat",
        "naechster": "Nächster Monat ›",
        "heute": "Heute",
        "abgesagte_zeigen": "Abgesagte zeigen",
        "abgesagte_aus": "Abgesagte ausblenden",
        "monat": "{name} {jahr}",
        "ohne_datum": "Ohne Datum",
        "leer_monat": "In diesem Monat ist noch nichts geplant.",
        "leer_auswahl": "Keine passenden Einträge in diesem Monat.",
        "m_1": "Januar", "m_2": "Februar", "m_3": "März", "m_4": "April", "m_5": "Mai", "m_6": "Juni",
        "m_7": "Juli", "m_8": "August", "m_9": "September", "m_10": "Oktober", "m_11": "November",
        "m_12": "Dezember",
        "w_0": "Mo", "w_1": "Di", "w_2": "Mi", "w_3": "Do", "w_4": "Fr", "w_5": "Sa", "w_6": "So",
    },
    "en": {
        "titel": "Campaign plan",
        "kampagnen": "Campaigns",
        "keine_kampagnen": "No campaign yet.",
        "kalender": "Calendar",
        "art": "Type",
        "alle": "All",
        "art_newsletter": "Newsletter", "art_post": "Post", "art_podcast": "Podcast", "art_video": "Video",
        "art_aktion": "Promotion", "art_sonstiges": "Other",
        "st_vorschlag": "Suggested by the agent", "st_geplant": "planned", "st_verschoben": "postponed",
        "st_erledigt": "done", "st_abgesagt": "cancelled",
        "k_in_planung": "being planned", "k_geplant": "planned", "k_laeuft": "running",
        "k_abgeschlossen": "finished", "k_abgesagt": "cancelled",
        "z_sales": "Sales contacts", "z_oeffentlich": "public", "z_verteiler": "mailing list",
        "zeitraum": "{von} to {bis}",
        "zielgruppe": "Audience: {gruppe}",
        "beitraege": "Items: {n}",
        "keine_beitraege": "No items yet.",
        "vorschlaege": "Suggestions from the agent this month: {n}. For now they are confirmed in the Sales interface.",
        "nichts_wartet": "Nothing is waiting for confirmation.",
        "vormonat": "‹ Previous month",
        "naechster": "Next month ›",
        "heute": "Today",
        "abgesagte_zeigen": "Show cancelled",
        "abgesagte_aus": "Hide cancelled",
        "monat": "{name} {jahr}",
        "ohne_datum": "No date",
        "leer_monat": "Nothing is planned for this month yet.",
        "leer_auswahl": "No matching entries this month.",
        "m_1": "January", "m_2": "February", "m_3": "March", "m_4": "April", "m_5": "May", "m_6": "June",
        "m_7": "July", "m_8": "August", "m_9": "September", "m_10": "October", "m_11": "November",
        "m_12": "December",
        "w_0": "Mon", "w_1": "Tue", "w_2": "Wed", "w_3": "Thu", "w_4": "Fri", "w_5": "Sat", "w_6": "Sun",
    },
}


def monat_lesen(roh: str | None, heute: date | None = None) -> date:
    """Erster Tag des Monats aus JJJJ-MM; sonst der aktuelle Monat."""
    heute = heute or date.today()
    try:
        jahr, monat = (int(x) for x in str(roh or "").split("-"))
        if 2000 <= jahr <= 2100:
            return date(jahr, monat, 1)
    except ValueError:
        pass
    return heute.replace(day=1)


def monat_ende(erster: date) -> date:
    return (erster.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def nachbar(erster: date, schritt: int) -> date:
    n = erster.year * 12 + erster.month - 1 + schritt
    return date(n // 12, n % 12 + 1, 1)


def pfad(erster: date, art: str | None, abgesagte: bool) -> str:
    q = [("monat", f"{erster.year:04d}-{erster.month:02d}")]
    if art:
        q.append(("art", art))
    if abgesagte:
        q.append(("abgesagt", "1"))
    return SEITE + "?" + urllib.parse.urlencode(q)


def _art(p: dict) -> str:
    a = p.get("art")
    return a if a in ARTEN else "sonstiges"


def _tag(roh) -> date | None:
    try:
        return date.fromisoformat(str(roh or "")[:10])
    except ValueError:
        return None


def _chip(k: Kontext, status: str) -> str:
    if status not in STATUS:
        return ""
    return f'<span class="chip {STATUS_CHIP[status]}">{e(k.t(TEXTE, "st_" + status))}</span>'


def kampagnen_html(k: Kontext, kampagnen: list[dict]) -> str:
    if not kampagnen:
        return f'<p class="leer">{e(k.t(TEXTE, "keine_kampagnen"))}</p>'
    karten = []
    for kp in kampagnen:
        st = str(kp.get("status") or "")
        gruppe = str(kp.get("zielgruppe") or "")
        beitraege = [b for b in kp.get("beitraege") or [] if isinstance(b, dict)]
        meta = [k.t(TEXTE, "zeitraum", von=k.datum(kp.get("von")), bis=k.datum(kp.get("bis")))]
        if gruppe in ZIELGRUPPEN:
            meta.append(k.t(TEXTE, "zielgruppe", gruppe=k.t(TEXTE, "z_" + gruppe)))
        zeilen = "".join(
            f'<li>{e(k.datum(b.get("datum")))} · {e(k.t(TEXTE, "art_" + _art(b)))} · '
            f'{e(str(b.get("titel") or ""))} {_chip(k, str(b.get("status") or ""))}</li>' for b in beitraege)
        liste = (f'<p class="meta">{e(k.t(TEXTE, "beitraege", n=len(beitraege)))}</p><ul>{zeilen}</ul>' if beitraege
                 else f'<p class="leer">{e(k.t(TEXTE, "keine_beitraege"))}</p>')
        chip = f'<span class="chip">{e(k.t(TEXTE, "k_" + st))}</span>' if st in K_STATUS else ""
        ziel = str(kp.get("ziel") or "").strip()
        karten.append(f'<article class="karte"><p class="titel">{e(str(kp.get("name") or ""))} {chip}</p>'
                      f'<p class="meta">{e(" · ".join(meta))}</p>'
                      + (f'<p>{e(ziel)}</p>' if ziel else "") + liste + '</article>')
    return f'<div class="karten">{"".join(karten)}</div>'


def eintrag_html(k: Kontext, p: dict) -> str:
    notiz = str(p.get("notiz") or "").strip()
    return (f'<article class="karte"><p class="titel">{e(str(p.get("titel") or ""))}</p>'
            f'<p>{_chip(k, str(p.get("status") or ""))} <span class="meta">{e(k.t(TEXTE, "art_" + _art(p)))}</span></p>'
            + (f'<p class="meta">{e(notiz)}</p>' if notiz else "") + '</article>')


async def bauen(k: Kontext) -> tuple[str, str]:
    q = k.request.query_params
    erster = monat_lesen(q.get("monat"))
    art = q.get("art") if q.get("art") in ARTEN else None
    abgesagte = q.get("abgesagt") == "1"
    letzter = monat_ende(erster)
    eintraege = await run_in_threadpool(daten.plan, k.firma, erster.isoformat(), letzter.isoformat())
    try:
        kampagnen = await run_in_threadpool(daten.kampagnen, k.firma)
    except daten.DatenFehler:
        kampagnen = []      # Kampagnen sind ein Zusatz: der Kalender bleibt sichtbar
    sichtbar = [p for p in eintraege if abgesagte or p.get("status") != "abgesagt"]
    zahlen = {a: sum(1 for p in sichtbar if _art(p) == a) for a in ARTEN}
    gewaehlt = [p for p in sichtbar if art is None or _art(p) == art]
    vorschlaege = sum(1 for p in eintraege if p.get("status") == "vorschlag")
    monat_name = k.t(TEXTE, "monat", name=k.t(TEXTE, f"m_{erster.month}"), jahr=erster.year)

    unter = (k.t(TEXTE, "vorschlaege", n=vorschlaege) if vorschlaege else k.t(TEXTE, "nichts_wartet"))
    nav = reiter([("", k.t(TEXTE, "alle"), len(sichtbar))]
                 + [(a, k.t(TEXTE, "art_" + a), zahlen[a]) for a in ARTEN],
                 art or "", lambda a: pfad(erster, a or None, abgesagte), k.t(TEXTE, "art"))
    heute = date.today()
    leiste = [f'<a href="{e(pfad(nachbar(erster, -1), art, abgesagte))}">{e(k.t(TEXTE, "vormonat"))}</a>',
              f'<strong>{e(monat_name)}</strong>',
              f'<a href="{e(pfad(nachbar(erster, 1), art, abgesagte))}">{e(k.t(TEXTE, "naechster"))}</a>']
    if erster != heute.replace(day=1):
        leiste.append(f'<a href="{e(pfad(heute.replace(day=1), art, abgesagte))}">{e(k.t(TEXTE, "heute"))}</a>')
    leiste.append(f'<a href="{e(pfad(erster, art, not abgesagte))}">'
                  f'{e(k.t(TEXTE, "abgesagte_aus" if abgesagte else "abgesagte_zeigen"))}</a>')

    if gewaehlt:
        gruppen: dict[date | None, list[dict]] = {}
        for p in gewaehlt:
            gruppen.setdefault(_tag(p.get("datum")), []).append(p)
        agenda = ""
        for d in sorted(gruppen, key=lambda x: (x is None, x or date.min)):
            kopf = (k.t(TEXTE, "ohne_datum") if d is None
                    else f'{k.t(TEXTE, f"w_{d.weekday()}")} {tag(k.sprache, d)}')
            agenda += (f'<section class="tag"><h3>{e(kopf)}</h3><div class="karten">'
                       + "".join(eintrag_html(k, p) for p in gruppen[d]) + '</div></section>')
    else:
        agenda = f'<p class="leer">{e(k.t(TEXTE, "leer_auswahl" if eintraege else "leer_monat"))}</p>'

    rumpf = (f'<h2>{e(k.t(TEXTE, "kampagnen"))}</h2>{kampagnen_html(k, kampagnen)}'
             f'<h2>{e(k.t(TEXTE, "kalender"))}</h2><p class="meta">{e(unter)}</p>'
             f'{nav}<nav class="blaettern">{"".join(leiste)}</nav>{agenda}')
    return k.t(TEXTE, "titel"), rumpf
