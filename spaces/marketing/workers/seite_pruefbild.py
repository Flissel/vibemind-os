"""Pruefbild der Collage-Seite (Stufe 3): rendert die Seite wie der Export in headless Chromium,
fotografiert nur den 600-px-Seitencontainer und schneidet ihn in JPEG-Kacheln fuer das Modell."""
from __future__ import annotations

import base64
import io
import math
from typing import Callable

from spaces.marketing.claw import seite_html
from spaces.marketing.workers.export_worker import BASIS, EchterBrowser, Routen

KACHEL_HOEHE = 1200
MIN_BREITE = 300
JPEG_QUALITAET = 85
SEITE_CONTAINER = "body > div > div"   # aeusserer 700-px-Rahmen > die 600 x hoehe Seite (seite_html.html)


class PruefbildFehler(RuntimeError):
    pass


def kacheln(seite: dict, html_render: Callable[[str, int], bytes], max_kacheln: int = 4
            ) -> tuple[list[bytes], float]:
    """(jpeg_kacheln, faktor). faktor = Seiteneinheiten je Kachelpixel (1.0 ohne Verkleinerung).
    html_render(html, hoehe) liefert das PNG des Seitencontainers."""
    from PIL import Image
    try:
        html = seite_html.html(seite, betreff="", pflichtteil={}, bild_basis=BASIS + "medien/",
                               schrift_basis=BASIS + "schrift/")
        png = html_render(html, int(seite["hoehe"]))
        with Image.open(io.BytesIO(png)) as quelle:
            bild = quelle.convert("RGB")
    except PruefbildFehler:
        raise
    except Exception as e:  # noqa: BLE001
        raise PruefbildFehler(f"Prüfbild nicht möglich: {str(e)[:150]}") from e
    breite, hoehe = bild.size
    if breite <= 0 or hoehe <= 0:
        raise PruefbildFehler("Prüfbild nicht möglich: leeres Bild")
    faktor = 1.0
    grenze = max_kacheln * KACHEL_HOEHE
    if hoehe > grenze:
        s = grenze / hoehe
        if round(breite * s) < MIN_BREITE:
            raise PruefbildFehler("Prüfbild nicht möglich: Seite ist zu hoch für ein lesbares Prüfbild")
        bild = bild.resize((round(breite * s), grenze), Image.LANCZOS)
        faktor = hoehe / grenze
        hoehe = grenze
    teile = []
    for oben in range(0, hoehe, KACHEL_HOEHE):
        puffer = io.BytesIO()
        bild.crop((0, oben, bild.size[0], min(oben + KACHEL_HOEHE, hoehe))).save(
            puffer, "JPEG", quality=JPEG_QUALITAET)
        teile.append(puffer.getvalue())
    assert len(teile) == math.ceil(hoehe / KACHEL_HOEHE) <= max_kacheln
    return teile, faktor


def bilder_fuer_modell(anhaenge: list[dict], kacheln: list[bytes], max_bilder: int = 6) -> list[dict]:
    """Anhaenge (fertige image_url-Teile) zuerst, dann Kacheln bis zum Budget."""
    teile = [a for a in anhaenge][:max_bilder]
    for k in kacheln[:max(0, max_bilder - len(teile))]:
        url = "data:image/jpeg;base64," + base64.b64encode(k).decode("ascii")
        teile.append({"type": "image_url", "image_url": {"url": url}})
    return teile


def browser_render(api, aid, browser_starten=None) -> Callable[[str, int], bytes]:
    """Echter Renderer: html + hoehe -> PNG des Seitencontainers. Medien ueber api.medium(aid, name),
    Schriften lokal (Routen aus export_worker). Der Browser lebt nur je Aufruf."""
    def render(html: str, hoehe: int) -> bytes:
        browser = (browser_starten or EchterBrowser)()
        try:
            seite = browser.new_page(viewport={"width": seite_html.BREITE_PX, "height": max(1, hoehe)})
            try:
                routen = Routen(api, aid)
                seite.route("**/*", routen)
                seite.set_content(html, wait_until="networkidle")
                if routen.fehler is not None:
                    raise routen.fehler
                if routen.fehlt:
                    raise PruefbildFehler(f"Bild {routen.fehlt[0]} fehlt in den Medien")
                seite.evaluate("document.fonts.ready")
                return seite.locator(SEITE_CONTAINER).first.screenshot(type="png")
            finally:
                _zu(seite)
        finally:
            _zu(browser)
    return render


def _zu(objekt) -> None:
    try:
        objekt.close()
    except Exception:  # noqa: BLE001 - Schliessen darf den Ursprungsfehler nie ueberdecken
        pass
