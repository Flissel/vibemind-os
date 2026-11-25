"""Reels-Galerie (lesend). Vorbild: sales-claw ui_reels.galerie.

Reiter Fertig, In Arbeit, Fehlgeschlagen, Archiv mit Karten (Titel, Status, Art, Format, Dauer,
Datum). Seit Stufe B, Tag 4 spielen fertige Reels direkt in der Karte (medien.py, eigene Medienablage
je Firma). Seit Punkt 2 bestellen, neu bauen und abbrechen (bestellen.py, Kosten-Schalter); seit Punkt 5
teilen als Versandauftrag an Sales (teilen.py, derselbe Schalter).
Daten: GET /api/pult/video."""
from __future__ import annotations

from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.oberflaeche import bestellen, daten, medien, teilen
from spaces.marketing.api.oberflaeche.huelle import PFAD, Kontext, e, reiter

SEITE = f"{PFAD}/reels"
KI_VORSTUFEN = ("ideen", "bilder_laufen", "bilder_pruefen")
REITER = (("fertig", ("fertig",)), ("arbeit", ("offen", "in_arbeit") + KI_VORSTUFEN),
          ("fehler", ("fehler", "abgebrochen")), ("archiv", None))
STATUS = ("offen", "in_arbeit", "fertig", "fehler", "abgebrochen") + KI_VORSTUFEN
CHIP = {"fertig": "gut", "fehler": "schlecht", "abgebrochen": "", "bilder_pruefen": "akzent"}
ARTEN = ("bilder", "material", "kurzfassung", "ki")
FORMATE = ("reel", "quadrat", "quer")

TEXTE = {
    "de": {
        "titel": "Reels",
        "bereich": "Bereich",
        "r_fertig": "Fertig", "r_arbeit": "In Arbeit", "r_fehler": "Fehlgeschlagen", "r_archiv": "Archiv",
        "st_offen": "wartet", "st_in_arbeit": "wird gebaut", "st_fertig": "fertig", "st_fehler": "Fehler",
        "st_abgebrochen": "abgebrochen", "st_ideen": "Bildideen werden geschrieben",
        "st_bilder_laufen": "Bilder entstehen", "st_bilder_pruefen": "Bilder prüfen",
        "a_bilder": "Aus Bildern", "a_material": "Aus Videomaterial", "a_kurzfassung": "Kurzfassung eines Videos",
        "a_ki": "Aus KI-Bildern",
        "fm_reel": "Hochformat 9:16", "fm_quadrat": "Quadrat 1:1", "fm_quer": "Querformat 16:9",
        "sekunden": "{n} s",
        "leer": "Noch keine Reels in diesem Bereich.",
        "abspielen": "Fertige Reels spielen direkt hier. Teilen legt einen Versandauftrag an Sales an.",
        "hinweis": "Reels der Firma ansehen und bestellen. Versendet wird hier nichts.",
        "aenderung": "Änderungswunsch: {text}",
    },
    "en": {
        "titel": "Reels",
        "bereich": "Section",
        "r_fertig": "Finished", "r_arbeit": "In progress", "r_fehler": "Failed", "r_archiv": "Archive",
        "st_offen": "waiting", "st_in_arbeit": "being built", "st_fertig": "finished", "st_fehler": "error",
        "st_abgebrochen": "cancelled", "st_ideen": "writing image ideas",
        "st_bilder_laufen": "images being made", "st_bilder_pruefen": "review images",
        "a_bilder": "From images", "a_material": "From video footage", "a_kurzfassung": "Short cut of a video",
        "a_ki": "From AI images",
        "fm_reel": "Portrait 9:16", "fm_quadrat": "Square 1:1", "fm_quer": "Landscape 16:9",
        "sekunden": "{n} s",
        "leer": "No reels in this section yet.",
        "abspielen": "Finished reels play right here. Sharing creates a sending request for Sales.",
        "hinweis": "Watch and order the company's reels. Nothing is sent from here.",
        "aenderung": "Change request: {text}",
    },
}


def _briefing(a: dict) -> dict:
    b = a.get("briefing")
    return b if isinstance(b, dict) else {}


def art_von(a: dict) -> str:
    if isinstance(_briefing(a).get("ki"), dict):
        return "ki"
    art = str(a.get("art") or "")
    return art if art in ARTEN else ""


def format_von(a: dict) -> str:
    fmt = _briefing(a).get("format")
    return fmt if fmt in FORMATE else "reel"


def dauer(k: Kontext, sekunden) -> str:
    if isinstance(sekunden, bool) or not isinstance(sekunden, (int, float)) or sekunden <= 0:
        return ""
    n = int(sekunden) if float(sekunden).is_integer() else round(float(sekunden), 1)
    text = str(n)
    if k.sprache == "de":
        text = text.replace(".", ",")
    return k.t(TEXTE, "sekunden", n=text)


def karte(k: Kontext, a: dict, stand: dict | None = None) -> str:
    st = str(a.get("status") or "")
    chip = (f'<span class="chip {CHIP.get(st, "warn")}">{e(k.t(TEXTE, "st_" + st))}</span>' if st in STATUS else "")
    art = art_von(a)
    meta = [x for x in (k.t(TEXTE, "a_" + art) if art else "", k.t(TEXTE, "fm_" + format_von(a)),
                        dauer(k, a.get("dauer_s")), k.datum(a.get("erstellt_am"))) if x]
    befund = str(a.get("befund_text") or "").strip() if st in ("fehler", "abgebrochen") else ""
    wunsch = str(a.get("aenderungswunsch") or "").strip()
    titel = str(a.get("titel") or "")
    fmt = format_von(a)
    spieler = (medien.video_html(str(k.firma or ""), a.get("datei"), a.get("poster"), titel,
                                 "" if fmt == "reel" else fmt) if st == "fertig" else "")
    return (f'<article class="karte"><p class="titel">{e(titel)}</p><p>{chip}</p>'
            + (f'<div class="abspielen">{spieler}</div>' if spieler else "")
            + f'<p class="meta">{e(" · ".join(meta))}</p>'
            + (f'<p class="fehler">{e(befund)}</p>' if befund else "")
            + (f'<p class="meta">{e(k.t(TEXTE, "aenderung", text=wunsch))}</p>' if wunsch else "")
            + bestellen.reel_knoepfe(k, a)
            + teilen.formular(k, "reel", a, stand)
            + '</article>')


async def bauen(k: Kontext) -> tuple[str, str]:
    k.hinweis = k.t(TEXTE, "hinweis")
    aktuell = await run_in_threadpool(daten.reels, k.firma, False)
    archiv = await run_in_threadpool(daten.reels, k.firma, True)
    je = {schluessel: [a for a in aktuell if a.get("status") in st] for schluessel, st in REITER if st}
    je["archiv"] = archiv
    vorgabe = "fertig" if je["fertig"] else "arbeit"
    gewaehlt = k.request.query_params.get("reiter", vorgabe)
    gewaehlt = gewaehlt if gewaehlt in je else vorgabe
    nav = reiter([(s, k.t(TEXTE, "r_" + s), len(je[s])) for s, _ in REITER], gewaehlt,
                 lambda s: f"{SEITE}?reiter={s}", k.t(TEXTE, "bereich"))
    stand = None
    if any(a.get("status") == "fertig" for a in je[gewaehlt]):
        try:
            stand = teilen.je_auftrag(await run_in_threadpool(daten.geteilt, str(k.firma), "reel"))
        except daten.DatenFehler:
            stand = None
    karten = "".join(karte(k, a, stand) for a in je[gewaehlt])
    inhalt = f'<div class="karten">{karten}</div>' if karten else f'<p class="leer">{e(k.t(TEXTE, "leer"))}</p>'
    try:
        namen = await run_in_threadpool(daten.bilder, str(k.firma)) if bestellen.an() else []
    except daten.DatenFehler:
        namen = None
    return k.t(TEXTE, "titel"), (bestellen.meldung(k) + teilen.meldung(k) + bestellen.reel_formular(k, namen) + nav
                                 + f'<p class="meta">{e(k.t(TEXTE, "abspielen"))}</p>' + inhalt)
