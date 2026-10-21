"""Pruefbild der Collage-Seite (Stufe 3): rendert die Seite wie der Export in headless Chromium,
fotografiert nur den 600-px-Seitencontainer und schneidet ihn in JPEG-Kacheln fuer das Modell.

Stufe 5: dazu die Schoenheitspruefung (claw/seite_schoenheit) wie beim Einreichen - die Huellen misst dasselbe
Rendering per DOM (seite_bilder.messen), die Unterlage der Texte ein zweites Rendering ohne Text- und Knopfebenen
(nur wenn eine Textebene die Pixel braucht). Platzhalter sind graue Kacheln; Text darueber bekommt keinen
Pixel-Kontrast. Scheitert die Pruefung, steht nur PRUEFUNG_NICHT_MOEGLICH in den Saetzen - die Runde laeuft weiter."""
from __future__ import annotations

import base64
import io
import math
from typing import Callable

from spaces.marketing.claw import seite_bilder, seite_html, seite_schoenheit
from spaces.marketing.workers.export_worker import BASIS, EchterBrowser, Routen, kachel_png

__all__ = ["PruefbildFehler", "kacheln", "bilder_fuer_modell", "browser_render", "fehlt_hinweis", "kachel_png",
           "pruefbild_mit", "befund_saetze", "PRUEFUNG_NICHT_MOEGLICH", "MAX_BEFUNDE"]

KACHEL_HOEHE = 1200
MIN_BREITE = 300
JPEG_QUALITAET = 85
SEITE_CONTAINER = seite_html.SEITE_CONTAINER   # aeusserer 700-px-Rahmen > die 600 x hoehe Seite
PRUEF_FAKTOR = 1       # browser_render rendert mit Geraetefaktor 1: 1 Pixel = 1 Seiteneinheit
MAX_BEFUNDE = 8
PRUEFUNG_NICHT_MOEGLICH = "Schönheit: Prüfung nicht möglich – prüfe Kontrast und Regeln selbst am Prüfbild"


class PruefbildFehler(RuntimeError):
    pass


def fehlt_hinweis(name: str) -> str:
    """Hinweis an das Modell fuer ein Medium, das im Pruefbild als graue Kachel steht."""
    return f"Bild {name} fehlt in den Medien – im Prüfbild als graue Kachel"


def kacheln(seite: dict, html_render: Callable[[str, int], bytes], max_kacheln: int = 4
            ) -> tuple[list[bytes], float]:
    """(jpeg_kacheln, faktor). faktor = Seiteneinheiten je Kachelpixel (1.0 ohne Verkleinerung).
    html_render(html, hoehe) liefert das PNG des Seitencontainers."""
    from PIL import Image
    try:
        png = html_render(_html(seite), int(seite["hoehe"]))
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


def _html(seite: dict) -> str:
    return seite_html.html(seite, betreff="", pflichtteil={}, bild_basis=BASIS + "medien/",
                           schrift_basis=BASIS + "schrift/")


def befund_saetze(seite: dict, html_render: Callable[[str, int], bytes], masse: dict | None,
                  fehlt=(), zeilen: dict | None = None) -> list[str]:
    """Saetze der Schoenheitspruefung, harte zuerst, hoechstens MAX_BEFUNDE. masse: gemessene Huellen aus dem
    Pruefbild-Rendering (None = Messen gescheitert), zeilen: Zeilenkaesten der Schrift aus demselben Rendering.
    fehlt: Namen der Medien, die dort als graue Kachel standen - Text darueber wird wie ueber einem Platzhalter
    nicht am Pixel gemessen (seine Unterlage ist unbekannt). html_render rendert bei Bedarf die Seite ohne Text (mit
    Platzhaltern als grauen Kacheln). Jeder Fehler ergibt nur [PRUEFUNG_NICHT_MOEGLICH]."""
    try:
        if masse is None:
            raise PruefbildFehler("keine Maße")
        ohne = seite_schoenheit.ohne_pixel(seite, masse, fehlt)
        png_ohne_text = b""
        if seite_schoenheit.braucht_pixel(seite, ohne):
            png_ohne_text = html_render(_html(seite_schoenheit.seite_ohne_text(seite)), int(seite["hoehe"]))
        befunde = seite_schoenheit.pruefen(seite, masse, png_ohne_text, PRUEF_FAKTOR, ohne, zeilen)
    except Exception:  # noqa: BLE001 - die Pruefung ist eine Hilfe, sie kippt die Runde nie
        return [PRUEFUNG_NICHT_MOEGLICH]
    return [str(b["satz"]) for b in befunde][:MAX_BEFUNDE]


def pruefbild_mit(html_render: Callable[[str, int], bytes]
                  ) -> Callable[[dict], tuple[list[bytes], float, list[str], list[str]]]:
    """pruefbild(seite) -> (kacheln, faktor, fehlende Medien, Befund-Saetze) fuer die Pruefrunde im chat_worker.
    html_render wie browser_render (setzt nach jedem Aufruf .fehlt und .masse). Beide Renderings laufen im selben
    Aufruf - der Aufrufer haelt _PRUEFBILD_SPERRE und die Vergabe ueber den ganzen Aufruf."""
    def pruefbild(s: dict) -> tuple[list[bytes], float, list[str], list[str]]:
        teile, faktor = kacheln(s, html_render)
        fehlt = list(getattr(html_render, "fehlt", []))
        masse = getattr(html_render, "masse", None)
        zeilen = getattr(html_render, "zeilen", None)
        return teile, faktor, fehlt, befund_saetze(s, html_render, masse, fehlt, zeilen)
    return pruefbild


def bilder_fuer_modell(anhaenge: list[dict], kacheln: list[bytes], max_bilder: int = 6) -> list[dict]:
    """Anhaenge (fertige image_url-Teile) zuerst, dann Kacheln bis zum Budget."""
    teile = [a for a in anhaenge][:max_bilder]
    for k in kacheln[:max(0, max_bilder - len(teile))]:
        url = "data:image/jpeg;base64," + base64.b64encode(k).decode("ascii")
        teile.append({"type": "image_url", "image_url": {"url": url}})
    return teile


def browser_render(api, aid, browser_starten=None) -> Callable[[str, int], bytes]:
    """Echter Renderer: html + hoehe -> PNG des Seitencontainers. Medien ueber api.medium(aid, name),
    Schriften lokal (Routen aus export_worker). Der Browser lebt nur je Aufruf. Platzhalter zeichnet die Route
    lokal als graue Kachel; ein fehlendes Medium ebenfalls - sein Name steht danach in render.fehlt (je Aufruf
    neu), der Aufrufer gibt ihn als fehlt_hinweis an das Modell. Beides ist nie ein PruefbildFehler.
    render.masse: gemessene Huellen je Ebene (seite_bilder.messen_alles, dazu render.zeilen) oder None, wenn das Messen scheiterte - auch
    das ist kein PruefbildFehler, nur die Schoenheitspruefung entfaellt."""
    def render(html: str, hoehe: int) -> bytes:
        render.fehlt, render.masse, render.zeilen = [], None, None
        browser = (browser_starten or EchterBrowser)()
        try:
            seite = browser.new_page(viewport={"width": seite_html.BREITE_PX, "height": max(1, hoehe)})
            try:
                routen = Routen(api, aid, pruefbild=True)
                seite.route("**/*", routen)
                seite.set_content(html, wait_until="networkidle")
                if routen.fehler is not None:
                    raise routen.fehler
                render.fehlt = list(routen.fehlt)
                seite.evaluate("document.fonts.ready")
                try:
                    render.masse, render.zeilen = seite_bilder.messen_alles(seite)
                except Exception:  # noqa: BLE001 - ohne Masse nur keine Schoenheitspruefung
                    render.masse, render.zeilen = None, None
                return seite.locator(SEITE_CONTAINER).first.screenshot(type="png")
            finally:
                _zu(seite)
        finally:
            _zu(browser)
    render.fehlt, render.masse, render.zeilen = [], None, None
    return render


def _zu(objekt) -> None:
    try:
        objekt.close()
    except Exception:  # noqa: BLE001 - Schliessen darf den Ursprungsfehler nie ueberdecken
        pass
