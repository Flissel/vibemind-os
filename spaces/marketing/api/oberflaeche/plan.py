"""Kampagnenplan. Vorbild: sales-claw ui_plan.plan.

Oben die Kampagnen der Firma mit ihren Beitraegen, darunter der Kalender eines Monats als Liste
nach Tagen, mit Reitern je Art und Monatsleiste. Daten: /api/pult/plan und /api/pult/kampagnen.

Seit Stufe B, Tag 3 (25.11.2026) auch schreibend, ohne Skript:
- Eintrag anlegen (als Betreiber 'geplant'), aendern, verschieben, Vorschlaege des Agenten bestaetigen
  oder absagen. Ein neues Datum macht aus 'geplant' 'verschoben' (Regel der DB).
- Kampagne anlegen ('in_planung'; der Kampagnen-Arbeiter schlaegt danach Beitraege vor) und ihren
  Status setzen.
Ein Planeintrag und eine Kampagne versenden nichts. Jeder Schritt prueft die Firma (Cookie) gegen
den Eintrag bzw. die Kampagne; ein Eintrag wird ueber sein bisheriges Datum gefunden, so faellt auch
auf, wenn ihn inzwischen jemand anders verschoben hat."""
from __future__ import annotations

import urllib.parse
import uuid
from datetime import date, timedelta

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.oberflaeche import daten, huelle
from spaces.marketing.api.oberflaeche.huelle import PFAD, Kontext, e, reiter, tag

SEITE = f"{PFAD}/plan"
ARTEN = ("newsletter", "post", "podcast", "video", "aktion", "sonstiges")
STATUS = ("vorschlag", "geplant", "verschoben", "erledigt", "abgesagt")
STATUS_BETREIBER = ("geplant", "verschoben", "erledigt", "abgesagt")
STATUS_CHIP = {"vorschlag": "akzent", "geplant": "", "verschoben": "warn", "erledigt": "gut", "abgesagt": "schlecht"}
K_STATUS = ("in_planung", "geplant", "laeuft", "abgeschlossen", "abgesagt")
ZIELGRUPPEN = ("sales", "oeffentlich", "verteiler")
VON = "Betreiber (Marketing-Reiter)"
TITEL_MAX, NOTIZ_MAX, NAME_MAX, ZIEL_MAX = 120, 1000, 120, 2000

TEXTE = {
    "de": {
        "titel": "Kampagnenplan",
        "hinweis": "Hier planst du Termine und Kampagnen. Ein Planeintrag versendet nichts; was hinausgeht, "
                   "gibt Sales frei.",
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
        "vorschlaege": "Vorschläge vom Agenten in diesem Monat: {n}. Bestätigen oder absagen direkt am Eintrag.",
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
        # Stufe B, Tag 3
        "neu_eintrag": "Neuer Eintrag",
        "neu_kampagne": "Neue Kampagne",
        "kampagne_hinweis": "Eine neue Kampagne ist „in Planung“: der Agent schlägt danach Beiträge vor, die du "
                            "hier im Kalender bestätigst.",
        "f_datum": "Datum", "f_art": "Art", "f_titel": "Titel", "f_notiz": "Notiz", "f_status": "Status",
        "f_name": "Name", "f_ziel": "Ziel", "f_zielgruppe": "Zielgruppe", "f_von": "Von", "f_bis": "Bis",
        "status_bleibt": "unverändert ({status})",
        "anlegen": "Anlegen",
        "speichern": "Speichern",
        "bearbeiten": "Bearbeiten oder verschieben",
        "bestaetigen": "Bestätigen",
        "absagen": "Absagen",
        "status_setzen": "Status setzen",
        "angelegt": "Eintrag angelegt.",
        "geaendert": "Eintrag geändert.",
        "unveraendert": "Nichts geändert.",
        "k_angelegt": "Kampagne angelegt. Der Agent schlägt jetzt Beiträge vor.",
        "k_geaendert": "Status der Kampagne gesetzt.",
        "f_datum_ungueltig": "Das Datum fehlt oder ist ungültig.",
        "f_art_ungueltig": "Bitte eine Art wählen.",
        "f_titel_laenge": "Der Titel braucht 1 bis {max} Zeichen.",
        "f_notiz_laenge": "Die Notiz darf höchstens {max} Zeichen haben.",
        "f_status_ungueltig": "Diesen Status gibt es nicht.",
        "f_name_laenge": "Der Name braucht 1 bis {max} Zeichen.",
        "f_ziel_laenge": "Das Ziel darf höchstens {max} Zeichen haben.",
        "f_zielgruppe_ungueltig": "Bitte eine Zielgruppe wählen.",
        "f_zeitraum": "„Bis“ darf nicht vor „Von“ liegen.",
        "f_eintrag_weg": "Diesen Eintrag gibt es an seinem Datum nicht mehr (oder er gehört zu einer anderen "
                         "Firma). Bitte die Seite neu laden.",
        "f_kampagne_weg": "Diese Kampagne gibt es bei dieser Firma nicht.",
    },
    "en": {
        "titel": "Campaign plan",
        "hinweis": "Plan dates and campaigns here. A plan entry sends nothing; whatever goes out is approved "
                   "in Sales.",
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
        "vorschlaege": "Suggestions from the agent this month: {n}. Confirm or cancel them right at the entry.",
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
        "neu_eintrag": "New entry",
        "neu_kampagne": "New campaign",
        "kampagne_hinweis": "A new campaign is “being planned”: the agent then suggests items, which you confirm "
                            "here in the calendar.",
        "f_datum": "Date", "f_art": "Type", "f_titel": "Title", "f_notiz": "Note", "f_status": "Status",
        "f_name": "Name", "f_ziel": "Goal", "f_zielgruppe": "Audience", "f_von": "From", "f_bis": "To",
        "status_bleibt": "unchanged ({status})",
        "anlegen": "Create",
        "speichern": "Save",
        "bearbeiten": "Edit or move",
        "bestaetigen": "Confirm",
        "absagen": "Cancel",
        "status_setzen": "Set status",
        "angelegt": "Entry created.",
        "geaendert": "Entry changed.",
        "unveraendert": "Nothing changed.",
        "k_angelegt": "Campaign created. The agent is now suggesting items.",
        "k_geaendert": "Campaign status set.",
        "f_datum_ungueltig": "The date is missing or invalid.",
        "f_art_ungueltig": "Please choose a type.",
        "f_titel_laenge": "The title needs 1 to {max} characters.",
        "f_notiz_laenge": "The note may have at most {max} characters.",
        "f_status_ungueltig": "This status does not exist.",
        "f_name_laenge": "The name needs 1 to {max} characters.",
        "f_ziel_laenge": "The goal may have at most {max} characters.",
        "f_zielgruppe_ungueltig": "Please choose an audience.",
        "f_zeitraum": "“To” must not be before “From”.",
        "f_eintrag_weg": "This entry no longer exists on its date (or belongs to another company). Please "
                         "reload the page.",
        "f_kampagne_weg": "This campaign does not exist for this company.",
    },
}


def tx(s: str, schluessel: str, **werte) -> str:
    return huelle._sprache.text(TEXTE, s, schluessel, **werte)


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


# --- Formularbausteine (ohne Skript) ------------------------------------------------------------

def _csrf() -> str:
    return f'<input type="hidden" name="csrf" value="{e(huelle.csrf_marke())}">'


def _monat_feld(erster: date) -> str:
    return f'<input type="hidden" name="monat" value="{erster.year:04d}-{erster.month:02d}">'


def _auswahl(name: str, werte: list[tuple[str, str]], gewaehlt: str) -> str:
    return (f'<select name="{e(name)}">'
            + "".join(f'<option value="{e(w)}"{" selected" if w == gewaehlt else ""}>{e(t)}</option>'
                      for w, t in werte) + "</select>")


def _feld(k: Kontext, beschriftung: str, inhalt: str) -> str:
    return f'<label>{e(k.t(TEXTE, beschriftung))} {inhalt}</label>'


def neu_eintrag_html(k: Kontext, erster: date) -> str:
    vorgabe = date.today() if date.today().replace(day=1) == erster else erster
    arten = [(a, k.t(TEXTE, "art_" + a)) for a in ARTEN]
    return (f'<details class="aufklapp"><summary>{e(k.t(TEXTE, "neu_eintrag"))}</summary>'
            f'<form method="post" action="{SEITE}/anlegen" class="editor">{_csrf()}{_monat_feld(erster)}'
            '<div class="zeile">'
            + _feld(k, "f_datum", f'<input type="date" name="datum" value="{vorgabe.isoformat()}" required>')
            + _feld(k, "f_art", _auswahl("art", arten, "newsletter")) + '</div>'
            + _feld(k, "f_titel", f'<input type="text" name="titel" maxlength="{TITEL_MAX}" required>')
            + _feld(k, "f_notiz", f'<textarea name="notiz" rows="3" maxlength="{NOTIZ_MAX}"></textarea>')
            + f'<p><button type="submit">{e(k.t(TEXTE, "anlegen"))}</button></p></form></details>')


def neu_kampagne_html(k: Kontext, erster: date) -> str:
    gruppen = [(g, k.t(TEXTE, "z_" + g)) for g in ZIELGRUPPEN]
    return (f'<details class="aufklapp"><summary>{e(k.t(TEXTE, "neu_kampagne"))}</summary>'
            f'<form method="post" action="{SEITE}/kampagne" class="editor">{_csrf()}{_monat_feld(erster)}'
            f'<p class="meta">{e(k.t(TEXTE, "kampagne_hinweis"))}</p>'
            + _feld(k, "f_name", f'<input type="text" name="name" maxlength="{NAME_MAX}" required>')
            + _feld(k, "f_ziel", f'<textarea name="ziel" rows="3" maxlength="{ZIEL_MAX}"></textarea>')
            + '<div class="zeile">'
            + _feld(k, "f_zielgruppe", _auswahl("zielgruppe", gruppen, "verteiler"))
            + _feld(k, "f_von", f'<input type="date" name="von" value="{erster.isoformat()}" required>')
            + _feld(k, "f_bis", f'<input type="date" name="bis" value="{monat_ende(erster).isoformat()}" required>')
            + f'</div><p><button type="submit">{e(k.t(TEXTE, "anlegen"))}</button></p></form></details>')


def _kampagne_status_form(k: Kontext, kp: dict, erster: date) -> str:
    kid = str(kp.get("id") or "")
    if not _uuid_ok(kid):
        return ""
    st = str(kp.get("status") or "")
    return (f'<form method="post" action="{SEITE}/kampagne/status" class="anlegen">{_csrf()}{_monat_feld(erster)}'
            f'<input type="hidden" name="id" value="{e(kid)}">'
            + _auswahl("status", [(s, k.t(TEXTE, "k_" + s)) for s in K_STATUS], st)
            + f'<button type="submit" class="leise">{e(k.t(TEXTE, "status_setzen"))}</button></form>')


def _eintrag_formulare(k: Kontext, p: dict, erster: date) -> str:
    pid = str(p.get("id") or "")
    tag_ = _tag(p.get("datum"))
    if not _uuid_ok(pid) or tag_ is None:
        return ""
    st = str(p.get("status") or "")
    kopf = (f'{_csrf()}{_monat_feld(erster)}<input type="hidden" name="id" value="{e(pid)}">'
            f'<input type="hidden" name="alt" value="{tag_.isoformat()}">')
    schnell = ""
    if st == "vorschlag":
        schnell = (f'<form method="post" action="{SEITE}/aendern" class="anlegen">{kopf}'
                   f'<button type="submit" name="status" value="geplant">{e(k.t(TEXTE, "bestaetigen"))}</button>'
                   f'<button type="submit" name="status" value="abgesagt" class="leise">'
                   f'{e(k.t(TEXTE, "absagen"))}</button></form>')
    wahl = [("", k.t(TEXTE, "status_bleibt", status=k.t(TEXTE, "st_" + st) if st in STATUS else st))]
    wahl += [(s, k.t(TEXTE, "st_" + s)) for s in STATUS_BETREIBER if s != st]
    return (schnell + f'<details class="aufklapp"><summary>{e(k.t(TEXTE, "bearbeiten"))}</summary>'
            f'<form method="post" action="{SEITE}/aendern" class="editor">{kopf}<div class="zeile">'
            + _feld(k, "f_datum", f'<input type="date" name="datum" value="{tag_.isoformat()}" required>')
            + _feld(k, "f_status", _auswahl("status", wahl, "")) + '</div>'
            + _feld(k, "f_titel", f'<input type="text" name="titel" value="{e(str(p.get("titel") or ""))}" '
                                  f'maxlength="{TITEL_MAX}" required>')
            + _feld(k, "f_notiz", f'<textarea name="notiz" rows="3" maxlength="{NOTIZ_MAX}">'
                                  f'{e(str(p.get("notiz") or ""))}</textarea>')
            + f'<p><button type="submit">{e(k.t(TEXTE, "speichern"))}</button></p></form></details>')


def kampagnen_html(k: Kontext, kampagnen: list[dict], erster: date | None = None) -> str:
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
        form = _kampagne_status_form(k, kp, erster) if erster is not None else ""
        karten.append(f'<article class="karte"><p class="titel">{e(str(kp.get("name") or ""))} {chip}</p>'
                      f'<p class="meta">{e(" · ".join(meta))}</p>'
                      + (f'<p>{e(ziel)}</p>' if ziel else "") + liste + form + '</article>')
    return f'<div class="karten">{"".join(karten)}</div>'


def eintrag_html(k: Kontext, p: dict, erster: date | None = None) -> str:
    notiz = str(p.get("notiz") or "").strip()
    form = _eintrag_formulare(k, p, erster) if erster is not None else ""
    return (f'<article class="karte"><p class="titel">{e(str(p.get("titel") or ""))}</p>'
            f'<p>{_chip(k, str(p.get("status") or ""))} <span class="meta">{e(k.t(TEXTE, "art_" + _art(p)))}</span></p>'
            + (f'<p class="meta">{e(notiz)}</p>' if notiz else "") + form + '</article>')


def _meldung(k: Kontext) -> str:
    q = k.request.query_params
    for schluessel in ("angelegt", "geaendert", "unveraendert", "k_angelegt", "k_geaendert"):
        if q.get(schluessel) == "1":
            return f'<p class="erfolg">{e(k.t(TEXTE, schluessel))}</p>'
    return ""


async def bauen(k: Kontext) -> tuple[str, str]:
    k.hinweis = k.t(TEXTE, "hinweis")
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
                       + "".join(eintrag_html(k, p, erster) for p in gruppen[d]) + '</div></section>')
    else:
        agenda = f'<p class="leer">{e(k.t(TEXTE, "leer_auswahl" if eintraege else "leer_monat"))}</p>'

    rumpf = (_meldung(k)
             + f'<h2>{e(k.t(TEXTE, "kampagnen"))}</h2>{neu_kampagne_html(k, erster)}'
             f'{kampagnen_html(k, kampagnen, erster)}'
             f'<h2>{e(k.t(TEXTE, "kalender"))}</h2><p class="meta">{e(unter)}</p>{neu_eintrag_html(k, erster)}'
             f'{nav}<nav class="blaettern">{"".join(leiste)}</nav>{agenda}')
    return k.t(TEXTE, "titel"), rumpf


# --- Formulare ----------------------------------------------------------------------------------

class Abgelehnt(ValueError):
    """Eingabe passt nicht; die Nachricht ist schon der Satz fuer die Seite."""


def _uuid_ok(roh) -> bool:
    try:
        uuid.UUID(str(roh))
        return True
    except ValueError:
        return False


def _datum_form(roh, s: str) -> str:
    try:
        d = date.fromisoformat(str(roh or "").strip())
    except ValueError:
        raise Abgelehnt(tx(s, "f_datum_ungueltig"))
    if not 2000 <= d.year <= 2100:
        raise Abgelehnt(tx(s, "f_datum_ungueltig"))
    return d.isoformat()


def _text(roh, s: str, hoechstens: int, fehler: str, pflicht: bool, einzeilig: bool) -> str:
    text = str(roh or "").replace("\r\n", "\n").replace("\r", "\n")
    text = " ".join(text.split("\n")).strip() if einzeilig else text.strip()
    if len(text) > hoechstens or (pflicht and not text):
        raise Abgelehnt(tx(s, fehler, max=hoechstens))
    return text


def eintrag_lesen(form: dict, s: str) -> dict:
    """Neuer Eintrag aus dem Formular: {datum, art, titel, notiz}."""
    art = str(form.get("art") or "")
    if art not in ARTEN:
        raise Abgelehnt(tx(s, "f_art_ungueltig"))
    return {"datum": _datum_form(form.get("datum"), s), "art": art,
            "titel": _text(form.get("titel"), s, TITEL_MAX, "f_titel_laenge", True, True),
            "notiz": _text(form.get("notiz"), s, NOTIZ_MAX, "f_notiz_laenge", False, False)}


def aenderung_lesen(form: dict, alt: dict, s: str) -> dict:
    """Nur was sich gegenueber `alt` aendert (status, datum, titel, notiz). Fehlende Felder bleiben."""
    aus: dict = {}
    status = str(form.get("status") or "")
    if status:
        if status not in STATUS_BETREIBER:
            raise Abgelehnt(tx(s, "f_status_ungueltig"))
        if status != alt.get("status"):
            aus["status"] = status
    if form.get("datum") is not None:
        datum = _datum_form(form.get("datum"), s)
        if datum != str(alt.get("datum") or "")[:10]:
            aus["datum"] = datum
    if form.get("titel") is not None:
        titel = _text(form.get("titel"), s, TITEL_MAX, "f_titel_laenge", True, True)
        if titel != str(alt.get("titel") or ""):
            aus["titel"] = titel
    if form.get("notiz") is not None:
        notiz = _text(form.get("notiz"), s, NOTIZ_MAX, "f_notiz_laenge", False, False)
        if notiz != str(alt.get("notiz") or "").strip():
            aus["notiz"] = notiz
    return aus


def kampagne_lesen(form: dict, s: str) -> dict:
    gruppe = str(form.get("zielgruppe") or "")
    if gruppe not in ZIELGRUPPEN:
        raise Abgelehnt(tx(s, "f_zielgruppe_ungueltig"))
    von, bis = _datum_form(form.get("von"), s), _datum_form(form.get("bis"), s)
    if bis < von:
        raise Abgelehnt(tx(s, "f_zeitraum"))
    return {"name": _text(form.get("name"), s, NAME_MAX, "f_name_laenge", True, True),
            "ziel": _text(form.get("ziel"), s, ZIEL_MAX, "f_ziel_laenge", False, False),
            "zielgruppe": gruppe, "von": von, "bis": bis}


def _zurueck(form: dict, meldung: str, datum: str | None = None) -> RedirectResponse:
    """Zurueck in den Monat des Formulars (oder des neuen Datums), mit Meldung."""
    erster = monat_lesen(datum[:7] if datum else form.get("monat"))
    return RedirectResponse(f"{pfad(erster, None, False)}&{meldung}=1", status_code=303)


async def anlegen(request: Request) -> Response:
    fehler, s, form, firma = await huelle.formular_mit_firma(request)
    if fehler is not None:
        return fehler
    try:
        neu = eintrag_lesen(form, s)
    except Abgelehnt as f:
        return huelle.abgelehnt(s, str(f))
    try:
        await run_in_threadpool(daten.plan_anlegen, firma, neu["datum"], neu["art"], neu["titel"], neu["notiz"])
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return _zurueck(form, "angelegt", neu["datum"])


async def aendern(request: Request) -> Response:
    fehler, s, form, firma = await huelle.formular_mit_firma(request)
    if fehler is not None:
        return fehler
    pid = str(form.get("id") or "")
    try:
        if not _uuid_ok(pid):
            raise Abgelehnt(tx(s, "f_eintrag_weg"))
        alt_tag = _datum_form(form.get("alt"), s)
    except Abgelehnt as f:
        return huelle.abgelehnt(s, str(f))
    try:
        am_tag = await run_in_threadpool(daten.plan, firma, alt_tag, alt_tag)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    alt = next((p for p in am_tag if str(p.get("id")) == pid), None)
    if alt is None:
        return huelle.abgelehnt(s, tx(s, "f_eintrag_weg"))
    try:
        aenderung = aenderung_lesen(form, alt, s)
    except Abgelehnt as f:
        return huelle.abgelehnt(s, str(f))
    if not aenderung:
        return _zurueck(form, "unveraendert")
    try:
        await run_in_threadpool(daten.plan_aendern, pid, aenderung)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return _zurueck(form, "geaendert", aenderung.get("datum"))


async def kampagne_anlegen(request: Request) -> Response:
    fehler, s, form, firma = await huelle.formular_mit_firma(request)
    if fehler is not None:
        return fehler
    try:
        neu = kampagne_lesen(form, s)
    except Abgelehnt as f:
        return huelle.abgelehnt(s, str(f))
    try:
        await run_in_threadpool(daten.kampagne_anlegen, firma, neu["name"], neu["ziel"], neu["zielgruppe"],
                                neu["von"], neu["bis"], VON)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return _zurueck(form, "k_angelegt", neu["von"])


async def kampagne_status(request: Request) -> Response:
    fehler, s, form, firma = await huelle.formular_mit_firma(request)
    if fehler is not None:
        return fehler
    kid, status = str(form.get("id") or ""), str(form.get("status") or "")
    if status not in K_STATUS:
        return huelle.abgelehnt(s, tx(s, "f_status_ungueltig"))
    if not _uuid_ok(kid):
        return huelle.abgelehnt(s, tx(s, "f_kampagne_weg"))
    try:
        kampagnen = await run_in_threadpool(daten.kampagnen, firma)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    alt = next((kp for kp in kampagnen if str(kp.get("id")) == kid), None)
    if alt is None:
        return huelle.abgelehnt(s, tx(s, "f_kampagne_weg"))
    if alt.get("status") == status:
        return _zurueck(form, "unveraendert")
    try:
        await run_in_threadpool(daten.kampagne_status, kid, status)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    return _zurueck(form, "k_geaendert")
