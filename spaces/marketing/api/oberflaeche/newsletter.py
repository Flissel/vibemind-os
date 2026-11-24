"""Newsletter-Uebersicht je Firma (lesend). Vorbild: sales-claw ui_marketing.uebersicht.

Reiter je Status mit Zahl, darunter Karten (Titel, Fassung, Datum, Status). "zurueckgegeben" ist
kein DB-Status, sondern ein Entwurf mit offener Rueckmeldung. Vorschauen und Bearbeiten folgen in
Stufe B; die Daten kommen aus /api/pult/inhalte."""
from __future__ import annotations

import math
import urllib.parse

from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.oberflaeche import daten
from spaces.marketing.api.oberflaeche.huelle import PFAD, Kontext, e, reiter

SEITE = f"{PFAD}/newsletter"
STATUS = ("entwurf", "zurueckgegeben", "eingereicht", "freigegeben", "abgelehnt")
CHIP = {"entwurf": "", "zurueckgegeben": "warn", "eingereicht": "akzent", "freigegeben": "gut", "abgelehnt": "schlecht"}
JE_SEITE = 24

TEXTE = {
    "de": {
        "titel": "Newsletter",
        "status": "Status",
        "status_entwurf": "in Arbeit",
        "status_zurueckgegeben": "zurückgegeben",
        "status_eingereicht": "zur Freigabe",
        "status_freigegeben": "freigegeben",
        "status_abgelehnt": "verworfen",
        "leer": "Keine Newsletter mit diesem Status.",
        "fassung": "Fassung {n}",
        "keine_fassung": "Noch keine Fassung",
        "erstellt": "erstellt {datum}",
        "format_seite": "Collage",
        "format_bloecke": "Blöcke",
        "format_felder": "Felder",
        "rueckmeldungen": "Offene Rückmeldungen: {n}",
        "zurueck_seite": "‹ Zurück",
        "weiter_seite": "Weiter ›",
        "seite_von": "Seite {seite} von {letzte}",
    },
    "en": {
        "titel": "Newsletters",
        "status": "Status",
        "status_entwurf": "in progress",
        "status_zurueckgegeben": "returned",
        "status_eingereicht": "awaiting approval",
        "status_freigegeben": "approved",
        "status_abgelehnt": "discarded",
        "leer": "No newsletters with this status.",
        "fassung": "Version {n}",
        "keine_fassung": "No version yet",
        "erstellt": "created {datum}",
        "format_seite": "Collage",
        "format_bloecke": "Blocks",
        "format_felder": "Fields",
        "rueckmeldungen": "Open feedback: {n}",
        "zurueck_seite": "‹ Previous",
        "weiter_seite": "Next ›",
        "seite_von": "Page {seite} of {letzte}",
    },
}


def _zahl(roh) -> int:
    if isinstance(roh, bool):
        return 0
    try:
        return int(str(roh))
    except (TypeError, ValueError):
        return 0


def reiter_von(i: dict) -> str:
    st = str(i.get("status") or "")
    return "zurueckgegeben" if st == "entwurf" and _zahl(i.get("offene_rueckmeldungen")) > 0 else st


def verweis(status: str, seite: int = 1) -> str:
    q = [("status", status)] if status != "entwurf" else []
    if seite > 1:
        q.append(("seite", str(seite)))
    return SEITE + ("?" + urllib.parse.urlencode(q) if q else "")


def karte(k: Kontext, i: dict) -> str:
    n = _zahl(i.get("fassungen"))
    st = reiter_von(i)
    fmt = str(i.get("format") or "")
    teile = [k.t(TEXTE, "fassung", n=n) if n >= 1 else k.t(TEXTE, "keine_fassung"),
             k.t(TEXTE, "erstellt", datum=k.datum(i.get("erstellt_am")))]
    if fmt in ("seite", "bloecke", "felder"):
        teile.append(k.t(TEXTE, f"format_{fmt}"))
    offen = _zahl(i.get("offene_rueckmeldungen"))
    rueck = f'<p class="meta">{e(k.t(TEXTE, "rueckmeldungen", n=offen))}</p>' if offen else ""
    chip = (f'<span class="chip {CHIP.get(st, "")}">{e(k.t(TEXTE, "status_" + st))}</span>'
            if st in STATUS else "")
    return (f'<article class="karte"><p class="titel">{e(str(i.get("titel") or ""))}</p>'
            f'<p class="meta">{e(" · ".join(teile))}</p>{rueck}<p>{chip}</p></article>')


async def bauen(k: Kontext) -> tuple[str, str]:
    status = k.request.query_params.get("status", "entwurf")
    if status not in STATUS:
        status = "entwurf"
    alle = [i for i in await run_in_threadpool(daten.newsletter, k.firma) if i.get("art", "newsletter") == "newsletter"]
    zahlen = {s: sum(1 for i in alle if reiter_von(i) == s) for s in STATUS}
    gewaehlt = [i for i in alle if reiter_von(i) == status]
    letzte = max(1, math.ceil(len(gewaehlt) / JE_SEITE))
    try:
        seite = int(k.request.query_params.get("seite", "1"))
    except ValueError:
        seite = 1
    seite = min(max(seite, 1), letzte)
    nav = reiter([(s, k.t(TEXTE, "status_" + s), zahlen[s]) for s in STATUS], status,
                 lambda s: verweis(s), k.t(TEXTE, "status"))
    karten = "".join(karte(k, i) for i in gewaehlt[(seite - 1) * JE_SEITE:seite * JE_SEITE])
    inhalt = f'<div class="karten">{karten}</div>' if karten else f'<p class="leer">{e(k.t(TEXTE, "leer"))}</p>'
    blaettern = ""
    if letzte > 1:
        teile = []
        if seite > 1:
            teile.append(f'<a href="{e(verweis(status, seite - 1))}">{e(k.t(TEXTE, "zurueck_seite"))}</a>')
        teile.append(f'<span class="meta">{e(k.t(TEXTE, "seite_von", seite=seite, letzte=letzte))}</span>')
        if seite < letzte:
            teile.append(f'<a href="{e(verweis(status, seite + 1))}">{e(k.t(TEXTE, "weiter_seite"))}</a>')
        blaettern = f'<nav class="blaettern">{"".join(teile)}</nav>'
    return k.t(TEXTE, "titel"), nav + inhalt + blaettern
