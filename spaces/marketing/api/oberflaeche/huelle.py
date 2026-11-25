"""Seitenhuelle der eigenen Marketing-Oberflaeche (Stufe A, 24.11.2026).

Rahmen, Fehlerseite, CSRF fuer Formulare, strenge CSP ohne Skript und die Pruefung eines
Bearer-Schluessels. Die App haengt den Schluessel nur an die Adresse dieses Space
(`bearerEnv` in electron-app/external-spaces.js); die Seite selbst sieht ihn nie.

- Schluessel: Env MARKETING_UI_KEY. Fehlt er, ist die Oberflaeche zu (503), nie offen. Ein
  falscher oder fehlender Kopf gibt 401. Der Wert steht nie im Log, nur der Name der Variable.
- CSRF: eine Marke je Schluessel (HMAC), in jedem Formular als Feld `csrf`. Ein POST ohne
  passende Marke oder mit `Sec-Fetch-Site: cross-site` wird abgewiesen (403).
- CSP: `default-src 'none'`, kein Skript; Stil nur aus /oberflaeche/stil.css, Bilder nur von
  hier oder als data:-URL (Logo der Marke). Farbfelder sind SVG-Attribute, keine style-Attribute.
- Sprache: siehe sprache.py. Firma: Cookie `mk_firma`, nur gueltig fuer eine aktive Firma.

Versendet wird hier nichts. Was hinausgeht, entscheidet Sales in seinen Freigaben."""
from __future__ import annotations

import hashlib
import hmac
import html
import logging
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from spaces.marketing.api.oberflaeche import daten, sprache as _sprache

log = logging.getLogger(__name__)

PFAD = "/oberflaeche"
SCHLUESSEL_ENV = "MARKETING_UI_KEY"
COOKIE_FIRMA = "mk_firma"
VORGABE_FIRMA = "vibemind"
COOKIE_JAHR = 365 * 24 * 3600
FORM_MAX = 4096
CSP = ("default-src 'none'; style-src 'self'; img-src 'self' data:; form-action 'self'; "
       "frame-ancestors 'none'; base-uri 'none'")
# Nur Seiten mit Vorschau (vorlagen.py): der Rahmen laedt eine Vorschau-Adresse dieser Oberflaeche, sonst nichts
CSP_RAHMEN = CSP + "; frame-src 'self'"
_CSRF_ZWECK = b"marketing-oberflaeche/csrf/v1"
_FARBE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_AKTIV = ' class="aktiv"'

# (Pfad unter PFAD, Textschluessel) in der Reihenfolge des Menues
MENUE = (("/newsletter", "menue_newsletter"), ("/entwuerfe", "menue_entwuerfe"), ("/vorlagen", "menue_vorlagen"),
         ("/plan", "menue_plan"), ("/marke", "menue_marke"),
         ("/reels", "menue_reels"), ("/podcast", "menue_podcast"))

TEXTE = {
    "de": {
        "space": "Marketing",
        "menue": "Bereiche",
        "menue_newsletter": "Newsletter",
        "menue_entwuerfe": "Entwürfe",
        "menue_vorlagen": "Vorlagen",
        "menue_plan": "Kampagnenplan",
        "menue_marke": "Marke",
        "menue_reels": "Reels",
        "menue_podcast": "Podcast",
        "sprache": "Sprache",
        "firma": "Firma",
        "nur_ansicht": "Nur Ansicht. Bearbeiten, Bestellen und Einreichen gehen vorerst noch in der Sales-Oberfläche. "
                       "Was hinausgeht, gibt Sales frei.",
        "keine_firma": "Es gibt noch keine aktive Firma im Marketing.",
        "zurueck": "Zur Übersicht",
        "f_zu_titel": "Marketing-Oberfläche nicht eingerichtet",
        "f_zu_text": "Auf dem Server fehlt der Schlüssel für diese Oberfläche. Sie bleibt deshalb geschlossen.",
        "f_anmeldung_titel": "Nicht angemeldet",
        "f_anmeldung_text": "Der Schlüssel fehlt oder stimmt nicht. Diese Seite öffnet sich im Marketing-Reiter der App.",
        "f_csrf_titel": "Abgewiesen",
        "f_csrf_text": "Fehlende oder falsche Formularmarke. Bitte die Seite neu laden.",
        "f_eingabe_titel": "Nicht möglich",
        "f_firma_text": "Diese Firma gibt es nicht oder sie ist nicht aktiv.",
        "f_sprache_text": "Diese Sprache gibt es hier nicht.",
        "f_verbunden_titel": "Marketing-Daten nicht verbunden",
        "f_verbunden_text": "Der Zugang zu den Marketing-Daten ist auf dem Server nicht eingerichtet.",
        "f_erreichbar_titel": "Marketing-Daten gerade nicht erreichbar",
        "f_erreichbar_text": "Die Datenbank antwortet nicht. Bitte später neu laden.",
        "f_abgelehnt_text": "Die Marketing-API lehnt die Anfrage ab. Grund: {grund}",
        "f_unbekannt_titel": "Nicht gefunden",
        "f_unbekannt_text": "Diese Seite gibt es nicht.",
        "monat_1": "Jan.", "monat_2": "Feb.", "monat_3": "März", "monat_4": "Apr.", "monat_5": "Mai",
        "monat_6": "Juni", "monat_7": "Juli", "monat_8": "Aug.", "monat_9": "Sept.", "monat_10": "Okt.",
        "monat_11": "Nov.", "monat_12": "Dez.",
        "datum": "{tag:02d}.{monat:02d}.{jahr}",
    },
    "en": {
        "space": "Marketing",
        "menue": "Sections",
        "menue_newsletter": "Newsletters",
        "menue_entwuerfe": "Drafts",
        "menue_vorlagen": "Templates",
        "menue_plan": "Campaign plan",
        "menue_marke": "Brand",
        "menue_reels": "Reels",
        "menue_podcast": "Podcast",
        "sprache": "Language",
        "firma": "Company",
        "nur_ansicht": "View only. Editing, ordering and submitting still happen in the Sales interface for now. "
                       "Sales approves everything that goes out.",
        "keine_firma": "There is no active company in Marketing yet.",
        "zurueck": "Back to overview",
        "f_zu_titel": "Marketing interface not set up",
        "f_zu_text": "The key for this interface is missing on the server, so it stays closed.",
        "f_anmeldung_titel": "Not signed in",
        "f_anmeldung_text": "The key is missing or wrong. This page opens in the app's Marketing tab.",
        "f_csrf_titel": "Rejected",
        "f_csrf_text": "Missing or wrong form token. Please reload the page.",
        "f_eingabe_titel": "Not possible",
        "f_firma_text": "This company does not exist or is not active.",
        "f_sprache_text": "This language is not available here.",
        "f_verbunden_titel": "Marketing data not connected",
        "f_verbunden_text": "Access to the marketing data is not set up on the server.",
        "f_erreichbar_titel": "Marketing data currently unavailable",
        "f_erreichbar_text": "The database is not responding. Please reload later.",
        "f_abgelehnt_text": "The marketing API declined the request. Reason: {grund}",
        "f_unbekannt_titel": "Not found",
        "f_unbekannt_text": "This page does not exist.",
        "monat_1": "Jan", "monat_2": "Feb", "monat_3": "Mar", "monat_4": "Apr", "monat_5": "May",
        "monat_6": "Jun", "monat_7": "Jul", "monat_8": "Aug", "monat_9": "Sep", "monat_10": "Oct",
        "monat_11": "Nov", "monat_12": "Dec",
        "datum": "{tag} {monatsname} {jahr}",
    },
}

e = html.escape


def t(sprache: str, schluessel: str, **werte) -> str:
    return _sprache.text(TEXTE, sprache, schluessel, **werte)


# --- Schluessel und CSRF -----------------------------------------------------------------------

def _erwartet() -> str:
    return os.environ.get(SCHLUESSEL_ENV, "").strip()


def schluessel_pruefen(request: Request) -> int | None:
    """None, wenn der Bearer-Schluessel passt; sonst 503 (nicht eingerichtet) oder 401."""
    erwartet = _erwartet()
    if not erwartet:
        log.warning("Marketing-Oberflaeche zu: %s ist nicht gesetzt", SCHLUESSEL_ENV)
        return 503
    art, _, wert = (request.headers.get("authorization") or "").strip().partition(" ")
    kandidat = wert.strip()
    # Bytevergleich: compare_digest wirft bei zwei str mit Nicht-ASCII-Zeichen
    if art.lower() != "bearer" or not kandidat or not hmac.compare_digest(
            kandidat.encode("utf-8", errors="replace"), erwartet.encode("utf-8", errors="replace")):
        log.info("Marketing-Oberflaeche: Schluessel fehlt oder falsch (%s %s)", request.method, request.url.path)
        return 401
    return None


def csrf_marke() -> str:
    """Formularmarke, abgeleitet vom Schluessel: gleich ueber Neustarts, ohne Schluessel leer."""
    erwartet = _erwartet()
    if not erwartet:
        return ""
    return hmac.new(erwartet.encode("utf-8", errors="replace"), _CSRF_ZWECK, hashlib.sha256).hexdigest()


def csrf_ok(request: Request, form) -> bool:
    if (request.headers.get("sec-fetch-site") or "").lower() == "cross-site":
        return False
    marke, erwartet = str(form.get("csrf") or ""), csrf_marke()
    return bool(erwartet) and hmac.compare_digest(marke.encode("utf-8", errors="replace"), erwartet.encode())


def ziel_ok(ziel: object) -> bool:
    """Nur Ziele dieser Oberflaeche: kein offener Redirect."""
    if not isinstance(ziel, str) or not ziel.startswith(PFAD):
        return False
    rest = ziel[len(PFAD):]
    return ((rest == "" or rest[0] in "/?") and "//" not in ziel and "\\" not in ziel
            and not any(ord(c) < 32 or ord(c) == 127 for c in ziel) and len(ziel) <= 500)


# --- Firma -------------------------------------------------------------------------------------

def aktive(firmen: list[dict]) -> list[dict]:
    return [f for f in firmen if f.get("aktiv")]


def firma_waehlen(cookie: str | None, firmen: list[dict]) -> str | None:
    """Cookie, wenn es eine aktive Firma ist; sonst die Vorgabe, wenn aktiv; sonst die erste aktive."""
    ids = [f["id"] for f in aktive(firmen)]
    if cookie in ids:
        return cookie
    if VORGABE_FIRMA in ids:
        return VORGABE_FIRMA
    return ids[0] if ids else None


# --- Anzeige-Helfer ----------------------------------------------------------------------------

def datum(sprache: str, roh) -> str:
    """ISO-Datum (auch mit Zeit) als Tagesdatum in der Sprache der Seite; Unlesbares gekuerzt."""
    text = str(roh or "")
    try:
        d = date.fromisoformat(text[:10])
    except ValueError:
        return text[:10]
    return tag(sprache, d)


def tag(sprache: str, d: date) -> str:
    return t(sprache, "datum", tag=d.day, monat=d.month, jahr=d.year, monatsname=t(sprache, f"monat_{d.month}"))


def farbfeld(wert) -> str:
    """Farbfeld als SVG (Attribut statt style, damit die CSP ohne 'unsafe-inline' auskommt)."""
    w = str(wert or "")
    if not _FARBE.fullmatch(w):
        return ""
    return (f'<svg class="farbfeld" width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">'
            f'<rect width="18" height="18" rx="3" fill="{w}" stroke="#8888"/></svg>')


def reiter(eintraege: list[tuple[str, str, int | None]], aktiv: str, verweis: Callable[[str], str],
           beschriftung: str) -> str:
    """Reiterleiste aus Verweisen: (schluessel, name, zahl oder None)."""
    teile = []
    for k, name, zahl in eintraege:
        z = f" ({zahl})" if zahl is not None else ""
        an = ' class="aktiv" aria-current="page"' if k == aktiv else ""
        teile.append(f'<a href="{e(verweis(k))}"{an}>{e(name)}{z}</a>')
    return f'<nav class="reiter" aria-label="{e(beschriftung)}">{"".join(teile)}</nav>'


# --- Seiten ------------------------------------------------------------------------------------

@dataclass
class Kontext:
    request: Request
    sprache: str
    firmen: list[dict] = field(default_factory=list)
    firma: str | None = None
    # Stufe B: eine Seite mit Vorschau-Rahmen erlaubt frame-src 'self' (nur fuer sie); hinweis ersetzt "nur Ansicht"
    rahmen: bool = False
    hinweis: str | None = None

    def t(self, texte: dict, schluessel: str, **werte) -> str:
        return _sprache.text(texte, self.sprache, schluessel, **werte)

    def datum(self, roh) -> str:
        return datum(self.sprache, roh)

    @property
    def hier(self) -> str:
        url = self.request.url
        return url.path + (f"?{url.query}" if url.query else "")


def _koepfe(antwort: Response, sprache: str, csp: str = CSP) -> Response:
    antwort.headers["Content-Security-Policy"] = csp
    antwort.headers["X-Frame-Options"] = "DENY"
    antwort.headers["X-Content-Type-Options"] = "nosniff"
    antwort.headers["Referrer-Policy"] = "no-referrer"
    antwort.headers["Cache-Control"] = "private, no-store"
    antwort.headers["Content-Language"] = sprache
    return antwort


def _dokument(sprache: str, titel: str, kopf: str, rumpf: str) -> str:
    return (f'<!doctype html><html lang="{sprache}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{e(t(sprache, "space"))} – {e(titel)}</title>'
            f'<link rel="stylesheet" href="{PFAD}/stil.css"></head>'
            f'<body><div class="rahmen">{kopf}<main>{rumpf}</main></div></body></html>')


def _marke(sprache: str) -> str:
    return (f'<div class="marke"><span class="punkt" aria-hidden="true"></span>'
            f'<span class="marke-name">{e(t(sprache, "space"))}</span></div>')


def _sprach_umschalter(sprache: str, zurueck: str) -> str:
    knoepfe = "".join(
        f'<button type="submit" name="sprache" value="{s}" aria-pressed="{"true" if s == sprache else "false"}"'
        f'{_AKTIV if s == sprache else ""}>{s.upper()}</button>' for s in _sprache.SPRACHEN)
    return (f'<form method="post" action="{PFAD}/sprache" class="umschalter" aria-label="{e(t(sprache, "sprache"))}">'
            f'<input type="hidden" name="csrf" value="{e(csrf_marke())}">'
            f'<input type="hidden" name="zurueck" value="{e(zurueck)}">{knoepfe}</form>')


def _firmen_umschalter(k: Kontext) -> str:
    liste = aktive(k.firmen)
    if len(liste) < 2:
        return ""
    knoepfe = "".join(
        f'<button type="submit" name="firma" value="{e(f["id"])}" '
        f'aria-pressed="{"true" if f["id"] == k.firma else "false"}"'
        f'{_AKTIV if f["id"] == k.firma else ""}>{e(str(f.get("name") or f["id"]))}</button>'
        for f in liste)
    return (f'<form method="post" action="{PFAD}/firma" class="umschalter firmen" '
            f'aria-label="{e(t(k.sprache, "firma"))}">'
            f'<input type="hidden" name="csrf" value="{e(csrf_marke())}">'
            f'<input type="hidden" name="zurueck" value="{e(k.hier)}">{knoepfe}</form>')


def seite(k: Kontext, aktiv: str, titel: str, rumpf: str) -> Response:
    s = k.sprache
    menue = "".join(
        f'<a href="{PFAD}{p}"' + (' class="aktiv" aria-current="page"' if p == aktiv else "")
        + f'>{e(t(s, schluessel))}</a>' for p, schluessel in MENUE)
    # Aufbau wie die Sales-Oberflaeche (Seitenleiste links, Inhalt rechts), Farben aus VibeMind (STIL)
    kopf = (f'<nav class="seite" aria-label="{e(t(s, "menue"))}">{_marke(s)}'
            f'<div class="gruppe menue">{menue}</div>'
            f'<div class="unten">{_sprach_umschalter(s, k.hier)}</div></nav>')
    inhalt = (f'<h1>{e(titel)}</h1>{_firmen_umschalter(k)}'
              f'<p class="hinweis">{e(k.hinweis if k.hinweis is not None else t(s, "nur_ansicht"))}</p>{rumpf}')
    return _koepfe(HTMLResponse(_dokument(s, titel, kopf, inhalt)), s, CSP_RAHMEN if k.rahmen else CSP)


def fehlerseite(sprache: str, status: int, titel: str, text: str, mit_zurueck: bool = True) -> Response:
    zurueck = f'<p><a href="{PFAD}/newsletter">{e(t(sprache, "zurueck"))}</a></p>' if mit_zurueck else ""
    kopf = f'<nav class="seite" aria-label="{e(t(sprache, "menue"))}">{_marke(sprache)}</nav>'
    rumpf = f'<h1>{e(titel)}</h1><p class="fehler">{e(text)}</p>{zurueck}'
    return _koepfe(HTMLResponse(_dokument(sprache, titel, kopf, rumpf), status_code=status), sprache)


def sprache_von(request: Request) -> str:
    return _sprache.waehlen(request.cookies.get(_sprache.COOKIE), request.headers.get("accept-language"))


def zugang_fehler(request: Request) -> Response | None:
    """Fehlerseite, wenn der Schluessel fehlt oder nicht passt; sonst None."""
    status = schluessel_pruefen(request)
    if status is None:
        return None
    s = sprache_von(request)
    if status == 503:
        return fehlerseite(s, 503, t(s, "f_zu_titel"), t(s, "f_zu_text"), mit_zurueck=False)
    antwort = fehlerseite(s, 401, t(s, "f_anmeldung_titel"), t(s, "f_anmeldung_text"), mit_zurueck=False)
    antwort.headers["WWW-Authenticate"] = 'Bearer realm="marketing"'
    return antwort


def daten_fehlerseite(sprache: str, f: daten.DatenFehler) -> Response:
    if f.art == "nicht_verbunden":
        return fehlerseite(sprache, 503, t(sprache, "f_verbunden_titel"), t(sprache, "f_verbunden_text"))
    if f.art == "abgelehnt":
        return fehlerseite(sprache, 422, t(sprache, "f_eingabe_titel"), t(sprache, "f_abgelehnt_text", grund=f.grund))
    return fehlerseite(sprache, 503, t(sprache, "f_erreichbar_titel"), t(sprache, "f_erreichbar_text"))


Bauer = Callable[[Kontext], Awaitable[tuple[str, str]]]


def seiten_route(aktiv: str, bauen: Bauer):
    """Eine lesende Seite: Schluessel, Sprache, Firmenliste, dann der Inhalt des Moduls im Rahmen.
    `bauen` liefert (Titel, HTML-Rumpf) und darf DatenFehler werfen."""
    async def endpunkt(request: Request) -> Response:
        fehler = zugang_fehler(request)
        if fehler is not None:
            return fehler
        k = Kontext(request=request, sprache=sprache_von(request))
        try:
            k.firmen = await run_in_threadpool(daten.firmen)
            k.firma = firma_waehlen(request.cookies.get(COOKIE_FIRMA), k.firmen)
            if k.firma is None:
                return seite(k, aktiv, t(k.sprache, "space"), f'<p class="leer">{e(t(k.sprache, "keine_firma"))}</p>')
            titel, rumpf = await bauen(k)
        except daten.DatenFehler as f:
            return daten_fehlerseite(k.sprache, f)
        return seite(k, aktiv, titel, rumpf)
    endpunkt.__name__ = f"oberflaeche_{aktiv.strip('/') or 'start'}"
    return endpunkt


async def _formular(request: Request, grenze: int = FORM_MAX) -> tuple[Response | None, str, dict]:
    """Gemeinsamer Anfang jedes POST: Schluessel, Sprache, Formular, CSRF. grenze: hoechste Formulargroesse
    in Bytes (der Block-Editor braucht mehr als die Umschalter)."""
    fehler = zugang_fehler(request)
    s = sprache_von(request)
    if fehler is not None:
        return fehler, s, {}
    # Kleine urlencodierte Formulare selbst lesen: kein python-multipart auf der VM noetig
    roh = await request.body()
    form = {} if len(roh) > grenze else {
        k: v[0] for k, v in urllib.parse.parse_qs(roh.decode("utf-8", errors="replace"),
                                                  keep_blank_values=True).items()}
    if not csrf_ok(request, form):
        return fehlerseite(s, 403, t(s, "f_csrf_titel"), t(s, "f_csrf_text")), s, {}
    return None, s, form


async def formular_mit_firma(request: Request, grenze: int = FORM_MAX
                             ) -> tuple[Response | None, str, dict, str | None]:
    """Wie _formular, dazu die gewaehlte Firma (Cookie gegen die aktiven Firmen). Ohne Firma: Fehlerseite."""
    fehler, s, form = await _formular(request, grenze)
    if fehler is not None:
        return fehler, s, form, None
    try:
        firmen = await run_in_threadpool(daten.firmen)
    except daten.DatenFehler as f:
        return daten_fehlerseite(s, f), s, form, None
    firma = firma_waehlen(request.cookies.get(COOKIE_FIRMA), firmen)
    if firma is None:
        return fehlerseite(s, 422, t(s, "f_eingabe_titel"), t(s, "keine_firma")), s, form, None
    return None, s, form, firma


def abgelehnt(s: str, text: str) -> Response:
    return fehlerseite(s, 422, t(s, "f_eingabe_titel"), text)


def _zurueck(form: dict) -> str:
    ziel = form.get("zurueck")
    return ziel if ziel_ok(ziel) else f"{PFAD}/newsletter"


async def sprache_setzen(request: Request) -> Response:
    fehler, s, form = await _formular(request)
    if fehler is not None:
        return fehler
    wahl = str(form.get("sprache") or "")
    if wahl not in _sprache.SPRACHEN:
        return fehlerseite(s, 422, t(s, "f_eingabe_titel"), t(s, "f_sprache_text"))
    antwort = RedirectResponse(_zurueck(form), status_code=303)
    antwort.set_cookie(_sprache.COOKIE, wahl, max_age=COOKIE_JAHR, httponly=True, samesite="lax", path=PFAD)
    return antwort


async def firma_setzen(request: Request) -> Response:
    fehler, s, form = await _formular(request)
    if fehler is not None:
        return fehler
    wahl = str(form.get("firma") or "")
    try:
        firmen = await run_in_threadpool(daten.firmen)
    except daten.DatenFehler as f:
        return daten_fehlerseite(s, f)
    if wahl not in [f["id"] for f in aktive(firmen)]:
        return fehlerseite(s, 422, t(s, "f_eingabe_titel"), t(s, "f_firma_text"))
    antwort = RedirectResponse(_zurueck(form), status_code=303)
    antwort.set_cookie(COOKIE_FIRMA, wahl, max_age=COOKIE_JAHR, httponly=True, samesite="lax", path=PFAD)
    return antwort


# Aufbau wie die Sales-Oberflaeche (Seitenleiste 15rem, Karten mit Radius, Reiter, Statusmarken), Farben aus
# dem VibeMind-Renderer (renderer/styles/base.css) mit dem Akzent des Marketing-Space (0xff7eb6). Immer dunkel
# wie die App. Unter 768 px wird die Seitenleiste zur Zeile oben.
STIL = """
:root { color-scheme: dark;
  --grund: #0a0812; --grund2: #12101e; --balken: #12101e; --flaeche: rgba(24,28,46,.92);
  --flaeche2: rgba(30,27,48,.88); --text: #f0e8ff; --leise: rgba(230,220,245,.65); --still: rgba(200,190,220,.40);
  --akzent-rgb: 255,126,182; --akzent: rgb(var(--akzent-rgb)); --akzent-auf: #0a0812;
  --linie: rgba(var(--akzent-rgb), .22); --linie-leise: rgba(240,232,255,.10);
  --gut: #5eff8a; --warn: #ffc145; --schlecht: #ff5a6e; --info: #6eb8ff; }
* { box-sizing: border-box; }
html, body { overflow-x: hidden; }
body { margin: 0; font: 16px/1.5 "Segoe UI", Tahoma, system-ui, sans-serif; color: var(--text);
  background: linear-gradient(180deg, var(--grund2), var(--grund)) fixed; overflow-wrap: anywhere; }
.rahmen { display: flex; align-items: flex-start; min-height: 100vh; }
nav.seite { width: 15rem; flex: none; background: var(--balken); border-right: 1px solid var(--linie);
  display: flex; flex-direction: column; gap: 1.1rem; padding: 1rem .75rem; position: sticky; top: 0;
  height: 100vh; overflow-y: auto; }
nav.seite .marke { display: flex; align-items: center; gap: .6rem; padding: 0 .5rem; font-weight: 700; }
nav.seite .punkt { width: 1.1rem; height: 1.1rem; border-radius: 50%; background: var(--akzent);
  box-shadow: 0 0 12px rgba(var(--akzent-rgb), .55); }
.gruppe { display: flex; flex-direction: column; gap: .15rem; }
nav.seite a { color: var(--text); text-decoration: none; font-weight: 600; display: flex; align-items: center;
  min-height: 44px; padding: .5rem .7rem; border-radius: 10px; }
nav.seite a:hover { background: rgba(var(--akzent-rgb), .10); }
nav.seite a.aktiv { background: var(--akzent); color: var(--akzent-auf); }
nav.seite .unten { margin-top: auto; padding: 0 .5rem; }
main { flex: 1 1 auto; min-width: 0; max-width: 80rem; padding: 1.2rem 2rem 4rem; }
h1 { font-size: 1.5rem; margin: .2rem 0 .8rem; } h2 { font-size: 1.1rem; margin: 2rem 0 .6rem; }
h3 { font-size: 1rem; margin: 0 0 6px; }
a { color: var(--akzent); }
.umschalter { display: inline-flex; gap: 4px; margin: 0; }
.umschalter button { font: inherit; font-size: 12px; padding: 3px 10px; border: 1px solid var(--linie);
  border-radius: 999px; background: transparent; color: var(--text); cursor: pointer; }
.umschalter button.aktiv { background: var(--akzent); border-color: var(--akzent); color: var(--akzent-auf); }
.firmen { margin: 0 0 8px; flex-wrap: wrap; }
.hinweis { color: var(--leise); font-size: .9rem; margin: 4px 0 16px; padding: .6rem .9rem;
  border-left: 3px solid var(--akzent); background: var(--flaeche2); border-radius: 0 10px 10px 0; }
.fehler { color: var(--schlecht); } .leer, .meta { color: var(--leise); }
.reiter { display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 8px 0 16px; border-bottom: 1px solid var(--linie); }
.reiter a { text-decoration: none; color: var(--leise); padding: 4px 0; font-weight: 600; }
.reiter a.aktiv { color: var(--akzent); border-bottom: 2px solid var(--akzent); }
.karten { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: .9rem; }
.karte { background: var(--flaeche); border: 1px solid var(--linie); border-radius: 14px; padding: 12px 14px; }
.karte p { margin: 4px 0; } .karte .titel { font-weight: 600; } .karte .titel a { color: var(--text); }
.karte.gewaehlt { border-color: var(--akzent); box-shadow: 0 0 0 1px var(--akzent); }
.chip { display: inline-block; font-size: .8rem; font-weight: 700; padding: 1px 10px; border-radius: 999px;
  border: 1px solid var(--linie-leise); color: var(--leise); }
.chip.gut { color: var(--gut); border-color: var(--gut); } .chip.warn { color: var(--warn); border-color: var(--warn); }
.chip.schlecht { color: var(--schlecht); border-color: var(--schlecht); }
.chip.akzent { color: var(--akzent); border-color: var(--akzent); }
.blaettern { display: flex; gap: 12px; margin: 16px 0; }
.tag { margin: 16px 0; } .tag h3 { color: var(--leise); font-weight: 600; }
.farben { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; }
.farbe { display: inline-flex; gap: 6px; align-items: center; }
.logos { display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0; }
.logos div { padding: 12px; border-radius: 10px; border: 1px solid var(--linie); background: #fff; }
.logos div.dunkel { background: #1a1a1a; }
.logos img { max-height: 64px; max-width: 220px; display: block; }
dl.angaben dt { font-weight: 600; margin-top: 6px; } dl.angaben dd { margin: 0; white-space: pre-wrap; }
pre { white-space: pre-wrap; font: inherit; margin: 6px 0; }
.editor { display: flex; flex-direction: column; gap: 10px; max-width: 720px; margin: 8px 0 16px; }
.editor label { display: flex; flex-direction: column; gap: 4px; font-weight: 600; }
.editor input[type=text], .editor input[type=url], .editor textarea { font: inherit; font-weight: 400;
  padding: 6px 10px; border: 1px solid var(--linie); border-radius: 10px; background: var(--flaeche2); color: var(--text); }
.editor textarea { resize: vertical; min-height: 6rem; }
.editor fieldset.block { border: 1px solid var(--linie); border-radius: 14px; background: var(--flaeche);
  padding: 10px 14px 12px; margin: 0; display: flex; flex-direction: column; gap: 8px; }
.editor legend { color: var(--leise); font-size: .85rem; font-weight: 700; padding: 0 6px; }
.editor button, .anlegen button { font: inherit; font-weight: 600; padding: 6px 16px; min-height: 40px;
  border-radius: 10px; border: 1px solid var(--akzent); background: var(--akzent); color: var(--akzent-auf); cursor: pointer; }
.erfolg { color: var(--gut); font-weight: 600; }
.rueckmeldungen ul { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
.rueckmeldungen li { border-left: 3px solid var(--warn); background: var(--flaeche2); padding: 8px 12px;
  border-radius: 0 10px 10px 0; } .rueckmeldungen li p { margin: 2px 0; }
.vorschau { margin: 8px 0 24px; } .vorschau .reiter { margin-top: 0; }
.vorschau iframe { display: block; width: 100%; max-width: 680px; height: 860px; border: 1px solid var(--linie);
  border-radius: 14px; background: #fff; }
.vorschau iframe.handy { max-width: 400px; height: 760px; }
.anlegen { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 12px 0; }
.anlegen input[type=text] { font: inherit; padding: 6px 10px; min-width: 260px; border: 1px solid var(--linie);
  border-radius: 10px; background: var(--flaeche2); color: var(--text); }
.anlegen button { font: inherit; font-weight: 600; padding: 6px 16px; min-height: 40px; border-radius: 10px;
  border: 1px solid var(--akzent); background: var(--akzent); color: var(--akzent-auf); cursor: pointer; }
.editor select, .editor input[type=date], .anlegen select { font: inherit; font-weight: 400; padding: 6px 10px;
  border: 1px solid var(--linie); border-radius: 10px; background: var(--flaeche2); color: var(--text); }
.editor .zeile { display: flex; flex-wrap: wrap; gap: 10px; } .editor .zeile label { flex: 1 1 10rem; }
details.aufklapp { margin: 8px 0; } details.aufklapp > summary { cursor: pointer; color: var(--akzent);
  font-weight: 600; padding: 4px 0; } details.aufklapp[open] > summary { margin-bottom: 4px; }
.karte .editor { margin: 4px 0 0; } .karte .anlegen { margin: 6px 0 0; }
.anlegen button.leise, .editor button.leise { background: transparent; color: var(--text); border-color: var(--linie); }
table.aenderungen { border-collapse: collapse; width: 100%; max-width: 900px; margin: 6px 0 10px; }
table.aenderungen th, table.aenderungen td { text-align: left; vertical-align: top; padding: 6px 8px;
  border-bottom: 1px solid var(--linie); white-space: pre-wrap; }
table.aenderungen th { color: var(--leise); font-size: .85rem; }
@media (max-width: 767px) {
  .rahmen { display: block; }
  nav.seite { width: auto; height: auto; position: static; flex-direction: row; flex-wrap: wrap; align-items: center;
    gap: .4rem .8rem; border-right: 0; border-bottom: 1px solid var(--linie); }
  nav.seite .gruppe { flex-direction: row; flex-wrap: wrap; }
  nav.seite .unten { margin-top: 0; }
  main { padding: 1rem 16px 3rem; }
}
"""


async def stil(request: Request) -> Response:
    # Ohne Schluessel: das Stylesheet verraet nichts, und so bleibt auch die Fehlerseite gestaltet.
    return Response(STIL, media_type="text/css; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"})
