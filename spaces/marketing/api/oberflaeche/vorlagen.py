"""Vorlagen und Vorschau (Stufe B, Tag 1, 25.11.2026): Vorlage waehlen, als Mail oder am Handy ansehen,
daraus einen Newsletter-Entwurf anlegen und ihn als Vorschau sehen.

- Geschrieben wird nur der Entwurf (pult.inhalt_aus_vorlage, Status 'entwurf'). Versendet wird nichts;
  Einreichen und Freigabe bleiben vorerst in Sales.
- Collage-Vorlagen (format 'seite') zeigen nur die Vorschau: ihr Anlegen beauftragt Bilder
  (bilder._anlegen), das kommt erst mit dem Bestellen-Schalter.
- Die Vorschau ist fremdes HTML (Mail mit Inline-Stil). Sie steht in einem Rahmen mit `sandbox` (kein
  Skript, eigener Ursprung) und kommt von einer eigenen Adresse mit eigener CSP. Die App haengt den
  Schluessel nur an Anfragen der Seite selbst, nicht an einen Rahmen; darum traegt die Rahmen-Adresse eine
  kurzlebige Signatur (HMAC mit MARKETING_UI_KEY), die genau diese Vorschau erlaubt.
- Bilder: ohne eigene Medienablage (spaeterer Schritt) stehen sie in der Vorschau als [Bild: name]."""
from __future__ import annotations

import hashlib
import hmac
import html
import re
import time
import urllib.parse
import uuid
from datetime import date

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.oberflaeche import daten, huelle
from spaces.marketing.api.oberflaeche.huelle import PFAD, Kontext, e, reiter

SEITE = f"{PFAD}/vorlagen"
VORSCHAU = f"{PFAD}/vorschau"
FORMATE = ("mail", "handy")
TITEL_MAX = 200
GUELTIG_S = 15 * 60
_NAME = re.compile(r"^[a-z][a-z0-9-]{1,40}$")        # wie pult._VORLAGE
_FIRMA = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_ZWECK = b"marketing-oberflaeche/vorschau/v1"
VORSCHAU_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
                "frame-ancestors 'self'; form-action 'none'; base-uri 'none'; sandbox")

TEXTE = {
    "de": {
        "titel": "Vorlagen",
        "hinweis": "Aus einer Vorlage entsteht ein Entwurf. Versendet wird nichts; einreichen und freigeben "
                   "geht vorerst noch in Sales.",
        "leer": "Für diese Firma gibt es noch keine freigegebene Vorlage.",
        "waehlen": "Vorlage wählen",
        "format": "Ansicht",
        "format_mail": "Mail",
        "format_handy": "Handy",
        "art_seite": "Collage",
        "art_bloecke": "Blöcke",
        "fuer_alle": "für alle Firmen",
        "unbekannt": "Diese Vorlage gibt es hier nicht.",
        "rahmen": "Vorschau von {name}",
        "bilder": "Bilder erscheinen hier noch als [Bild: …]; die eigene Bildablage folgt.",
        "titel_feld": "Titel des Entwurfs",
        "titel_vorgabe": "Newsletter {datum}",
        "anlegen": "Entwurf anlegen",
        "collage_spaeter": "Aus einer Collage-Vorlage legst du den Entwurf vorerst in Sales an: dabei werden "
                           "Bilder beauftragt.",
        "f_titel": "Der Titel fehlt oder ist länger als 200 Zeichen.",
        "f_collage": "Collage-Vorlagen legen hier noch keine Entwürfe an.",
        "entwurf_titel": "Entwurf",
        "angelegt": "Entwurf angelegt: {titel}",
        "fassung": "Fassung {n} · Status: in Arbeit",
        "status_anders": "Fassung {n} · Status: {status}",
        "keine_fassung": "Dieser Entwurf hat noch keine Fassung.",
        "collage_vorschau": "Collage-Entwürfe zeigt die Vorschau hier noch nicht.",
        "weiter": "Bearbeiten und Einreichen kommen im nächsten Schritt in diesen Reiter.",
        "zu_newslettern": "Zu den Newslettern",
        "andere_vorlage": "Andere Vorlage wählen",
        "f_fremd": "Dieser Entwurf gehört zu einer anderen Firma.",
        "f_kein_entwurf": "Diesen Entwurf gibt es nicht.",
        "f_vorschau": "Vorschau nicht verfügbar.",
        "f_abgelaufen": "Diese Vorschau ist abgelaufen. Bitte die Seite neu laden.",
    },
    "en": {
        "titel": "Templates",
        "hinweis": "A template becomes a draft. Nothing is sent; submitting and approving still happen in Sales "
                   "for now.",
        "leer": "There is no approved template for this company yet.",
        "waehlen": "Choose template",
        "format": "View",
        "format_mail": "Email",
        "format_handy": "Phone",
        "art_seite": "Collage",
        "art_bloecke": "Blocks",
        "fuer_alle": "for all companies",
        "unbekannt": "This template is not available here.",
        "rahmen": "Preview of {name}",
        "bilder": "Images still show as [Bild: …] here; the own media store comes next.",
        "titel_feld": "Draft title",
        "titel_vorgabe": "Newsletter {datum}",
        "anlegen": "Create draft",
        "collage_spaeter": "Drafts from a collage template are still created in Sales for now: that orders "
                           "images.",
        "f_titel": "The title is missing or longer than 200 characters.",
        "f_collage": "Collage templates do not create drafts here yet.",
        "entwurf_titel": "Draft",
        "angelegt": "Draft created: {titel}",
        "fassung": "Version {n} · Status: in progress",
        "status_anders": "Version {n} · Status: {status}",
        "keine_fassung": "This draft has no version yet.",
        "collage_vorschau": "Collage drafts are not previewed here yet.",
        "weiter": "Editing and submitting come to this tab in the next step.",
        "zu_newslettern": "Go to newsletters",
        "andere_vorlage": "Choose another template",
        "f_fremd": "This draft belongs to another company.",
        "f_kein_entwurf": "This draft does not exist.",
        "f_vorschau": "Preview not available.",
        "f_abgelaufen": "This preview has expired. Please reload the page.",
    },
}


# --- Signierte Vorschau-Adresse ------------------------------------------------------------------

def _signatur(teile: tuple[str, ...]) -> str:
    schluessel = huelle._erwartet()
    if not schluessel:
        return ""
    nachricht = _ZWECK + b"\n" + "\n".join(teile).encode("utf-8", errors="replace")
    return hmac.new(schluessel.encode("utf-8", errors="replace"), nachricht, hashlib.sha256).hexdigest()


def vorschau_adresse(art: str, firma: str, ref: str, fassung: int, fmt: str, jetzt: float | None = None) -> str:
    """Adresse fuer den Rahmen: gilt GUELTIG_S Sekunden und nur fuer genau diese Vorschau."""
    bis = str(int(jetzt if jetzt is not None else time.time()) + GUELTIG_S)
    teile = (art, firma, ref, str(fassung), fmt, bis)
    q = urllib.parse.urlencode({"firma": firma, "fassung": fassung, "format": fmt, "bis": bis,
                                "sig": _signatur(teile)})
    return f"{VORSCHAU}/{art}/{urllib.parse.quote(ref, safe='')}?{q}"


def signatur_ok(art: str, firma: str, ref: str, fassung: str, fmt: str, bis: str, sig: str,
                jetzt: float | None = None) -> bool:
    if not bis.isdigit() or len(bis) > 12:
        return False
    nun = int(jetzt if jetzt is not None else time.time())
    if not nun <= int(bis) <= nun + GUELTIG_S + 60:
        return False
    erwartet = _signatur((art, firma, ref, fassung, fmt, bis))
    return bool(erwartet) and hmac.compare_digest(sig.encode("utf-8", errors="replace"), erwartet.encode())


def _rahmen(k: Kontext, adresse: str, fmt: str, name: str) -> str:
    klasse = ' class="handy"' if fmt == "handy" else ""
    return (f'<iframe{klasse} src="{e(adresse)}" sandbox="" referrerpolicy="no-referrer" loading="lazy" '
            f'title="{e(k.t(TEXTE, "rahmen", name=name))}"></iframe>')


def _format_wahl(k: Kontext, fmt: str, verweis) -> str:
    return reiter([(f, k.t(TEXTE, "format_" + f), None) for f in FORMATE], fmt, verweis, k.t(TEXTE, "format"))


def _fmt(k: Kontext) -> str:
    fmt = k.request.query_params.get("format", "mail")
    return fmt if fmt in FORMATE else "mail"


# --- Seite: Vorlagen ----------------------------------------------------------------------------

def _verweis(name: str, fmt: str = "mail") -> str:
    q = [("wahl", name)] + ([("format", fmt)] if fmt != "mail" else [])
    return SEITE + "?" + urllib.parse.urlencode(q)


def _art(v: dict) -> str:
    return "seite" if v.get("format") == "seite" else "bloecke"


def karte(k: Kontext, v: dict, gewaehlt: bool) -> str:
    name = str(v["name"])
    meta = [k.t(TEXTE, "art_" + _art(v))] + ([k.t(TEXTE, "fuer_alle")] if v.get("fuer_alle") else [])
    beschreibung = str(v.get("beschreibung") or "")
    klasse = "karte gewaehlt" if gewaehlt else "karte"
    an = ' aria-current="true"' if gewaehlt else ""
    return (f'<article class="{klasse}"><p class="titel"><a href="{e(_verweis(name))}"{an}>{e(name)}</a></p>'
            + (f'<p>{e(beschreibung)}</p>' if beschreibung else "")
            + f'<p class="meta">{e(" · ".join(meta))}</p></article>')


def _anlegen_formular(k: Kontext, name: str) -> str:
    vorgabe = k.t(TEXTE, "titel_vorgabe", datum=huelle.tag(k.sprache, date.today()))
    return (f'<form method="post" action="{SEITE}/anlegen" class="anlegen">'
            f'<input type="hidden" name="csrf" value="{e(huelle.csrf_marke())}">'
            f'<input type="hidden" name="vorlage" value="{e(name)}">'
            f'<label>{e(k.t(TEXTE, "titel_feld"))} '
            f'<input type="text" name="titel" value="{e(vorgabe)}" maxlength="{TITEL_MAX}" required></label>'
            f'<button type="submit">{e(k.t(TEXTE, "anlegen"))}</button></form>')


async def bauen(k: Kontext) -> tuple[str, str]:
    k.rahmen = True
    k.hinweis = k.t(TEXTE, "hinweis")
    liste = await run_in_threadpool(daten.vorlagen, k.firma)
    if not liste:
        return k.t(TEXTE, "titel"), f'<p class="leer">{e(k.t(TEXTE, "leer"))}</p>'
    wahl = k.request.query_params.get("wahl", "")
    fmt = _fmt(k)
    gewaehlt = next((v for v in liste if v["name"] == wahl), None)
    oben = ""
    if wahl and gewaehlt is None:
        oben = f'<p class="fehler">{e(k.t(TEXTE, "unbekannt"))}</p>'
    elif gewaehlt is not None:
        name = str(gewaehlt["name"])
        adresse = vorschau_adresse("vorlage", str(k.firma), name, 0, fmt)
        unten = (_anlegen_formular(k, name) if _art(gewaehlt) == "bloecke"
                 else f'<p class="meta">{e(k.t(TEXTE, "collage_spaeter"))}</p>')
        beschreibung = str(gewaehlt.get("beschreibung") or "")
        oben = (f'<section class="vorschau"><h2>{e(name)}</h2>'
                + (f'<p>{e(beschreibung)}</p>' if beschreibung else "")
                + unten + _format_wahl(k, fmt, lambda f: _verweis(name, f))
                + _rahmen(k, adresse, fmt, name)
                + f'<p class="meta">{e(k.t(TEXTE, "bilder"))}</p></section>')
    karten = "".join(karte(k, v, gewaehlt is not None and v["name"] == gewaehlt["name"]) for v in liste)
    return k.t(TEXTE, "titel"), (oben + f'<h2>{e(k.t(TEXTE, "waehlen"))}</h2><div class="karten">{karten}</div>')


# --- Formular: Entwurf anlegen ------------------------------------------------------------------

async def anlegen(request: Request) -> Response:
    fehler, s, form = await huelle._formular(request)
    if fehler is not None:
        return fehler
    name = str(form.get("vorlage") or "")
    titel = str(form.get("titel") or "").strip()
    unmoeglich = huelle.t(s, "f_eingabe_titel")
    if not titel or len(titel) > TITEL_MAX:
        return huelle.fehlerseite(s, 422, unmoeglich, huelle._sprache.text(TEXTE, s, "f_titel"))
    if not _NAME.fullmatch(name):
        return huelle.fehlerseite(s, 422, unmoeglich, huelle._sprache.text(TEXTE, s, "unbekannt"))
    try:
        firmen = await run_in_threadpool(daten.firmen)
        firma = huelle.firma_waehlen(request.cookies.get(huelle.COOKIE_FIRMA), firmen)
        if firma is None:
            return huelle.fehlerseite(s, 422, unmoeglich, huelle.t(s, "keine_firma"))
        vorlage = next((v for v in await run_in_threadpool(daten.vorlagen, firma) if v["name"] == name), None)
        if vorlage is None:
            return huelle.fehlerseite(s, 422, unmoeglich, huelle._sprache.text(TEXTE, s, "unbekannt"))
        if _art(vorlage) != "bloecke":
            return huelle.fehlerseite(s, 422, unmoeglich, huelle._sprache.text(TEXTE, s, "f_collage"))
        iid = await run_in_threadpool(daten.aus_vorlage, firma, name, titel)
    except daten.DatenFehler as f:
        return huelle.daten_fehlerseite(s, f)
    try:
        iid = str(uuid.UUID(iid))
    except ValueError:
        return huelle.daten_fehlerseite(s, daten.DatenFehler("nicht_erreichbar"))
    return RedirectResponse(f"{SEITE}/entwurf/{iid}", status_code=303)


# --- Seite: angelegter Entwurf ------------------------------------------------------------------

async def entwurf_bauen(k: Kontext) -> tuple[str, str]:
    k.rahmen = True
    k.hinweis = k.t(TEXTE, "hinweis")
    try:
        iid = str(uuid.UUID(str(k.request.path_params.get("iid", ""))))
    except ValueError:
        raise daten.DatenFehler("abgelehnt", k.t(TEXTE, "f_kein_entwurf"))
    antwort = await run_in_threadpool(daten.entwurf, iid)
    kopf = antwort.get("inhalt") if isinstance(antwort.get("inhalt"), dict) else {}
    if kopf.get("mandant") != k.firma:
        raise daten.DatenFehler("abgelehnt", k.t(TEXTE, "f_fremd"))
    titel = str(kopf.get("titel") or "")
    fassungen = [f for f in (antwort.get("fassungen") or []) if isinstance(f, dict)]
    neueste = fassungen[0] if fassungen else None
    links = (f'<p><a href="{PFAD}/newsletter">{e(k.t(TEXTE, "zu_newslettern"))}</a> · '
             f'<a href="{SEITE}">{e(k.t(TEXTE, "andere_vorlage"))}</a></p>')
    rumpf = f'<h2>{e(k.t(TEXTE, "angelegt", titel=titel))}</h2>'
    n = neueste.get("fassung") if neueste else None
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        return k.t(TEXTE, "entwurf_titel"), rumpf + f'<p class="leer">{e(k.t(TEXTE, "keine_fassung"))}</p>' + links
    status = str(kopf.get("status") or "")
    zeile = (k.t(TEXTE, "fassung", n=n) if status == "entwurf"
             else k.t(TEXTE, "status_anders", n=n, status=status))
    rumpf += f'<p class="meta">{e(zeile)}</p><p>{e(k.t(TEXTE, "weiter"))}</p>' + links
    if neueste.get("format") == "seite":
        return k.t(TEXTE, "entwurf_titel"), rumpf + f'<p class="meta">{e(k.t(TEXTE, "collage_vorschau"))}</p>'
    fmt = _fmt(k)
    hier = f"{SEITE}/entwurf/{iid}"
    adresse = vorschau_adresse("entwurf", str(k.firma), iid, n, fmt)
    rumpf += ('<section class="vorschau">'
              + _format_wahl(k, fmt, lambda f: hier + ("?format=handy" if f == "handy" else ""))
              + _rahmen(k, adresse, fmt, titel)
              + f'<p class="meta">{e(k.t(TEXTE, "bilder"))}</p></section>')
    return k.t(TEXTE, "entwurf_titel"), rumpf


# --- Rahmen-Inhalt: die Vorschau selbst ---------------------------------------------------------

def _vorschau_antwort(inhalt: str, status: int = 200) -> Response:
    antwort = HTMLResponse(inhalt, status_code=status)
    antwort.headers["Content-Security-Policy"] = VORSCHAU_CSP
    antwort.headers["X-Frame-Options"] = "SAMEORIGIN"
    antwort.headers["X-Content-Type-Options"] = "nosniff"
    antwort.headers["Referrer-Policy"] = "no-referrer"
    antwort.headers["Cache-Control"] = "private, no-store"
    return antwort


def _vorschau_fehler(sprache: str, schluessel: str, status: int) -> Response:
    text = html.escape(huelle._sprache.text(TEXTE, sprache, schluessel))
    return _vorschau_antwort(f'<!doctype html><html lang="{sprache}"><meta charset="utf-8">'
                             f'<p>{text}</p></html>', status)


async def vorschau(request: Request, art: str) -> Response:
    s = huelle.sprache_von(request)
    if not huelle._erwartet():
        return _vorschau_fehler(s, "f_vorschau", 503)
    q = request.query_params
    ref = str(request.path_params.get("ref", ""))
    firma, fassung, fmt = q.get("firma", ""), q.get("fassung", ""), q.get("format", "")
    if not signatur_ok(art, firma, ref, fassung, fmt, q.get("bis", ""), q.get("sig", "")):
        return _vorschau_fehler(s, "f_abgelaufen", 403)
    if fmt not in FORMATE or not _FIRMA.fullmatch(firma):
        return _vorschau_fehler(s, "f_vorschau", 404)
    try:
        if art == "vorlage" and _NAME.fullmatch(ref):
            inhalt = await run_in_threadpool(daten.vorlage_vorschau, firma, ref, fmt)
        elif art == "entwurf" and fassung.isdigit() and int(fassung) >= 1:
            inhalt = await run_in_threadpool(daten.entwurf_vorschau, str(uuid.UUID(ref)), int(fassung), fmt)
        else:
            return _vorschau_fehler(s, "f_vorschau", 404)
    except (daten.DatenFehler, ValueError):
        return _vorschau_fehler(s, "f_vorschau", 404)
    return _vorschau_antwort(inhalt)


async def vorschau_vorlage(request: Request) -> Response:
    return await vorschau(request, "vorlage")


async def vorschau_entwurf(request: Request) -> Response:
    return await vorschau(request, "entwurf")
