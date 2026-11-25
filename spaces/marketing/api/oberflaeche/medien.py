"""Abspielen im Reiter (Stufe B, Tag 4): Reels, Vorschaubilder und Podcast-Folgen aus der Medienablage
je Firma (api/medienablage.py), ohne Skript ueber <video> und <audio>.

Die Huelle haengt den Bearer-Schluessel nur an Anfragen der Seite selbst, nicht an die Datei-Abrufe von
<video>, <audio> und Vorschaubild. Darum traegt jede Adresse eine kurzlebige HMAC-Signatur ueber Firma, Name
und Ablauf (Schluessel aus MARKETING_UI_KEY, eigener Zweck). Die Seite stellt sie nur fuer Dateien aus den
Auftraegen der gewaehlten Firma aus; die Signatur ist dann die Berechtigung. Spulen geht ueber Range-Abrufe
(FileResponse).

Seit Tag 5 auch Bilder der Firma (Vorschau im Editor und Bildwahl): eigener Zweck in der Signatur, Namen wie
medien:<name> in Bloecken und Collagen, Datei aus den flachen Medienordnern (gestaltung.quellen). Signiert
wird nur, was die Seite der Firma zeigen darf (daten.bilder: eigene und gemeinsame, nie fremde)."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
import urllib.parse

from fastapi import Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from spaces.marketing.api import medienablage
from spaces.marketing.api.oberflaeche import huelle
from spaces.marketing.api.oberflaeche.huelle import PFAD, e

MEDIEN = f"{PFAD}/medien"
GUELTIG_S = 2 * 60 * 60          # eine offene Seite spielt zwei Stunden lang ab
_ZWECK = b"marketing-oberflaeche/medien/v1"
MEDIEN_CSP = "default-src 'none'; sandbox"


def _signatur(firma: str, name: str, bis: str, zweck: bytes = _ZWECK) -> str:
    schluessel = huelle._erwartet()
    if not schluessel:
        return ""
    nachricht = zweck + b"\n" + f"{firma}\n{name}\n{bis}".encode("utf-8", errors="replace")
    return hmac.new(schluessel.encode("utf-8", errors="replace"), nachricht, hashlib.sha256).hexdigest()


def adresse(firma: str, name: str, jetzt: float | None = None) -> str | None:
    """Signierte Adresse einer Datei dieser Firma; None bei ungueltigem Namen oder ohne Schluessel."""
    if not medienablage.firma_ok(firma) or not medienablage.name_ok(name):
        return None
    bis = str(int(jetzt if jetzt is not None else time.time()) + GUELTIG_S)
    sig = _signatur(firma, name, bis)
    if not sig:
        return None
    return f"{MEDIEN}/{name}?" + urllib.parse.urlencode({"firma": firma, "bis": bis, "sig": sig})


def signatur_ok(firma: str, name: str, bis: str, sig: str, jetzt: float | None = None,
                zweck: bytes = _ZWECK) -> bool:
    if not bis.isdigit() or len(bis) > 12:
        return False
    nun = int(jetzt if jetzt is not None else time.time())
    if not nun <= int(bis) <= nun + GUELTIG_S + 60:
        return False
    erwartet = _signatur(firma, name, bis, zweck)
    return bool(erwartet) and hmac.compare_digest(sig.encode("utf-8", errors="replace"), erwartet.encode())


def _nein(status: int) -> Response:
    return Response(status_code=status, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


async def abrufen(request: Request) -> Response:
    """GET /oberflaeche/medien/{name}?firma=&bis=&sig= (ohne Bearer: die Signatur ist die Berechtigung)."""
    name = str(request.path_params.get("name", ""))
    q = request.query_params
    firma = q.get("firma", "")
    if not medienablage.name_ok(name) or not medienablage.firma_ok(firma):
        return _nein(404)
    if not signatur_ok(firma, name, q.get("bis", ""), q.get("sig", "")):
        return _nein(403)
    pfad = await run_in_threadpool(medienablage.finden, firma, name)
    if pfad is None:
        return _nein(404)
    return FileResponse(pfad, media_type=medienablage.typ(name), headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": MEDIEN_CSP, "Content-Disposition": "inline"})


def video_html(firma: str, datei, poster, beschriftung: str, klasse: str = "") -> str:
    """<video> ohne Skript; leer, wenn die Datei keinen gueltigen Namen hat."""
    src = adresse(firma, datei) if isinstance(datei, str) else None
    if src is None:
        return ""
    bild = adresse(firma, poster) if isinstance(poster, str) else None
    vorschau = f' poster="{e(bild)}"' if bild else ""
    k = f' class="{e(klasse)}"' if klasse else ""
    return (f'<video controls preload="none" playsinline{k}{vorschau} src="{e(src)}" '
            f'aria-label="{e(beschriftung)}"></video>')


def audio_html(firma: str, datei, beschriftung: str) -> str:
    src = adresse(firma, datei) if isinstance(datei, str) else None
    if src is None:
        return ""
    return f'<audio controls preload="none" src="{e(src)}" aria-label="{e(beschriftung)}"></audio>'


# --- Stufe B, Tag 5: Bilder der Firma im Editor -------------------------------------------------------

BILD = f"{PFAD}/bild"
_BILD_ZWECK = b"marketing-oberflaeche/bild/v1"
# Wie chat._MEDIEN_NAME: die Namen, die Bloecke und Collagen als medien:<name> tragen
_BILD_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.(?:png|jpe?g|gif|webp)")
BILD_TYPEN = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
              ".webp": "image/webp"}


def bild_name_ok(name) -> bool:
    return isinstance(name, str) and _BILD_NAME.fullmatch(name) is not None and ".." not in name


def bild_adresse(firma: str, name: str, jetzt: float | None = None) -> str | None:
    if not medienablage.firma_ok(firma) or not bild_name_ok(name):
        return None
    bis = str(int(jetzt if jetzt is not None else time.time()) + GUELTIG_S)
    sig = _signatur(firma, name, bis, _BILD_ZWECK)
    if not sig:
        return None
    return f"{BILD}/{name}?" + urllib.parse.urlencode({"firma": firma, "bis": bis, "sig": sig})


def _bild_datei(name: str) -> str | None:
    from spaces.marketing.api.gestaltung import quellen   # im Rumpf: gestaltung zieht die DB-Helfer nach
    for o in quellen():
        if os.path.isdir(o):
            pfad = medienablage._datei(o, name)
            if pfad is not None:
                return pfad
    return None


async def bild_abrufen(request: Request) -> Response:
    """GET /oberflaeche/bild/{name}?firma=&bis=&sig= (ohne Bearer: die Signatur ist die Berechtigung)."""
    name = str(request.path_params.get("name", ""))
    q = request.query_params
    firma = q.get("firma", "")
    if not bild_name_ok(name) or not medienablage.firma_ok(firma):
        return _nein(404)
    if not signatur_ok(firma, name, q.get("bis", ""), q.get("sig", ""), zweck=_BILD_ZWECK):
        return _nein(403)
    pfad = await run_in_threadpool(_bild_datei, name)
    if pfad is None:
        return _nein(404)
    return FileResponse(pfad, media_type=BILD_TYPEN[os.path.splitext(name)[1].lower()], headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": MEDIEN_CSP, "Content-Disposition": "inline"})


def bild_html(firma: str, name, alt: str, klasse: str = "bildchen") -> str:
    """<img> eines Bildes der Firma; leer bei ungueltigem Namen."""
    src = bild_adresse(firma, name) if isinstance(name, str) else None
    if src is None:
        return ""
    return f'<img class="{e(klasse)}" src="{e(src)}" alt="{e(alt)}" loading="lazy" decoding="async">'
