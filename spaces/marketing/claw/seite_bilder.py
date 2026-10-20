"""Bildstuecke der Collage-Mail: rendert die Seite in headless Chromium (Geraetefaktor 2, Medien und Schriften nur
von der lokalen Platte) und schneidet aus dem Foto des Seitencontainers je ein JPEG pro Bildband (Stueck aus
seite_mail.stuecke). Laeuft in der Marketing-API auf der VM (Playwright+Chromium wie pdf_druck) und am PC.
Platzhalter-Bilder gibt es hier nicht: in einer Mail waeren sie ein Fehler."""
from __future__ import annotations

import io
import os
import threading
from typing import Callable

from spaces.marketing.claw import bildplaetze, seite_html
from spaces.marketing.claw.export_routen import BASIS, EchterBrowser, Routen
from spaces.marketing.claw.pdf_druck import TIMEOUT_S
from spaces.marketing.claw.seite_mail import Stueck

FAKTOR = 2
MAX_BYTES = 8 * 1024 * 1024
QUALITAETEN = (82, 76, 70, 64, 60)
_SPERRE = threading.Lock()   # hoechstens ein Chromium gleichzeitig fuer Mail-Stuecke im API-Prozess
ZEITLIMIT = "Bildstücke konnten nicht gerendert werden (Zeitlimit)"


class StueckFehler(RuntimeError):
    pass


def _name_ok(name: str) -> bool:
    return bool(name) and name == os.path.basename(name) and not name.startswith(".") and "\\" not in name


def render_png(seite: dict, medien_lesen: Callable[[str], bytes | None], *, faktor: int = FAKTOR,
               browser_starten=None) -> bytes:
    """PNG des Seitencontainers (600 x hoehe Einheiten, mal faktor Pixel). Ein fehlendes Medium, ein Platzhalter
    oder ein unzulaessiger Name wirft StueckFehler("Bild <name> fehlt"); ein Zeitlimit StueckFehler(ZEITLIMIT).
    Rendert hoechstens einer gleichzeitig."""
    html = seite_html.html(seite, betreff="", pflichtteil={}, bild_basis=BASIS + "medien/",
                           schrift_basis=BASIS + "schrift/")

    def holen(name: str) -> bytes | None:
        if not _name_ok(name) or bildplaetze.PLATZHALTER.match("medien:" + name):
            return None
        return medien_lesen(name)

    ms = TIMEOUT_S * 1000
    with _SPERRE:
        try:
            browser = (browser_starten or EchterBrowser)()
            try:
                page = browser.new_page(viewport={"width": seite_html.BREITE_PX,
                                                  "height": max(1, int(seite["hoehe"]))},
                                        device_scale_factor=faktor, java_script_enabled=False)
                try:
                    page.set_default_timeout(ms)
                    routen = Routen(None, None, medium_holen=holen)
                    page.route("**/*", routen)
                    page.set_content(html, wait_until="networkidle", timeout=ms)
                    if routen.fehler is not None:
                        raise routen.fehler
                    if routen.fehlt:
                        raise StueckFehler(f"Bild {routen.fehlt[0]} fehlt")
                    return page.locator(seite_html.SEITE_CONTAINER).first.screenshot(type="png", timeout=ms)
                finally:
                    _zu(page)
            finally:
                _zu(browser)
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "TimeoutError":   # playwright.sync_api.TimeoutError
                raise StueckFehler(ZEITLIMIT) from e
            raise


def _zu(objekt) -> None:
    try:
        objekt.close()
    except Exception:  # noqa: BLE001 - Schliessen darf den Ursprungsfehler nie ueberdecken
        pass


def _kodieren(bild, puffer, qualitaet: int) -> None:
    bild.save(puffer, "JPEG", quality=qualitaet, optimize=True)


def _mb(n: int) -> str:
    return f"{n / 2**20:.1f}".replace(".", ",")


def zuschneiden(png: bytes, stuecke: list[Stueck], faktor: int = FAKTOR, max_bytes: int = MAX_BYTES
                ) -> list[bytes]:
    """Ein JPEG je Stueck (Hoehe (unten - oben) * faktor). Qualitaet 82, sinkt bis 60, bis alle zusammen in
    max_bytes passen; sonst StueckFehler."""
    from PIL import Image
    with Image.open(io.BytesIO(png)) as quelle:
        bild = quelle.convert("RGB")
    breite, hoehe = bild.size
    teile = []
    for st in stuecke:
        o, u = round(st.oben * faktor), min(round(st.unten * faktor), hoehe)
        if u <= o:
            raise StueckFehler(f"Bildbereich {st.nr} liegt außerhalb der Seite")
        teile.append(bild.crop((0, o, breite, u)))
    gesamt = 0
    for q in QUALITAETEN:
        ergebnis = []
        for t in teile:
            puffer = io.BytesIO()
            _kodieren(t, puffer, q)
            ergebnis.append(puffer.getvalue())
        gesamt = sum(len(e) for e in ergebnis)
        if gesamt <= max_bytes:
            return ergebnis
    raise StueckFehler(f"Die Mail wäre zu groß ({_mb(gesamt)} MB, höchstens {f'{max_bytes / 2**20:g}'.replace('.', ',')} MB) – "
                       "weniger oder kleinere Bildbereiche")
