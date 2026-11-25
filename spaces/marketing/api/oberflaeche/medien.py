"""Abspielen im Reiter (Stufe B, Tag 4): Reels, Vorschaubilder und Podcast-Folgen aus der Medienablage
je Firma (api/medienablage.py), ohne Skript ueber <video> und <audio>.

Die Huelle haengt den Bearer-Schluessel nur an Anfragen der Seite selbst, nicht an die Datei-Abrufe von
<video>, <audio> und Vorschaubild. Darum traegt jede Adresse eine kurzlebige HMAC-Signatur ueber Firma, Name
und Ablauf (Schluessel aus MARKETING_UI_KEY, eigener Zweck). Die Seite stellt sie nur fuer Dateien aus den
Auftraegen der gewaehlten Firma aus; die Signatur ist dann die Berechtigung. Spulen geht ueber Range-Abrufe
(FileResponse)."""
from __future__ import annotations

import hashlib
import hmac
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


def _signatur(firma: str, name: str, bis: str) -> str:
    schluessel = huelle._erwartet()
    if not schluessel:
        return ""
    nachricht = _ZWECK + b"\n" + f"{firma}\n{name}\n{bis}".encode("utf-8", errors="replace")
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


def signatur_ok(firma: str, name: str, bis: str, sig: str, jetzt: float | None = None) -> bool:
    if not bis.isdigit() or len(bis) > 12:
        return False
    nun = int(jetzt if jetzt is not None else time.time())
    if not nun <= int(bis) <= nun + GUELTIG_S + 60:
        return False
    erwartet = _signatur(firma, name, bis)
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
