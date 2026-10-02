"""Export-Arbeiter am PC (sales-claw Spec 2026-10-02-newsletter-gestaltung-und-agent).
Setzt den Newsletter lokal zu HTML, fotografiert ihn in headless Chromium in Handy-/Tablet-/PC-Breite
und laedt die JPEGs in den Medienordner der VM. Wird von chat_worker.ein_durchlauf fuer art == "export"
gerufen. Playwright wird erst im Standard-Browserstarter importiert."""
from __future__ import annotations

import io
import os
import urllib.parse

from spaces.marketing.claw import bloecke_mjml, gestaltung, schriften
from spaces.marketing.workers.bild_worker import ApiFehler
from spaces.marketing.workers.chat_worker import FREMD, HALTEN_TAKT_S, _kurz, halten

GERAETE = {"handy": 375, "tablet": 768, "pc": 1200}
BASIS = "https://export.vibemind.invalid/"
JPEG_GRENZE = 4 * 1024 * 1024
MAX_HOEHE = 16000
HOEHE = 800
NICHT_MOEGLICH = "Export nicht möglich: "


def html_bauen(auftrag: dict) -> str:
    return bloecke_mjml.rendern(auftrag.get("bloecke") or {}, auftrag.get("betreff") or "",
                                auftrag.get("vorschautext") or "", auftrag.get("pflichtteil") or {},
                                bild_basis=BASIS + "medien/", schrift_basis=BASIS + "schrift/")


def schriften_css() -> str:
    """@font-face je Register-Datei; die URLs sind relativ zu <BASIS>schrift/schriften.css."""
    regeln = []
    for sid, eintrag in schriften.REGISTER.items():
        for gewicht, stil in eintrag["dateien"]:
            regeln.append("@font-face{font-family:'%s';font-weight:%s;font-style:%s;font-display:block;"
                          "src:url(%s-%s-%s.woff2) format('woff2');}" % (eintrag["familie"], gewicht, stil,
                                                                        sid, gewicht, stil))
    return "\n".join(regeln) + "\n"


def jpeg_passend(png: bytes, grenze: int = JPEG_GRENZE) -> bytes:
    from PIL import Image
    with Image.open(io.BytesIO(png)) as bild:
        bild = bild.convert("RGB")
    for qualitaet in range(88, 51, -6):
        puffer = io.BytesIO()
        bild.save(puffer, "JPEG", quality=qualitaet, optimize=True)
        if puffer.tell() <= grenze:
            return puffer.getvalue()
    raise ValueError(f"Das Bild bleibt auch bei Qualität 52 über {grenze / 2**20:.1f} MB")


class _Routen:
    """Beantwortet die Anfragen der Seite an BASIS; alles andere wird abgebrochen."""

    def __init__(self, api, aid):
        self.api, self.aid, self.medien, self.fehlt, self.fehler = api, aid, {}, [], None

    def __call__(self, route):
        try:
            if not route.request.url.startswith(BASIS):
                return route.abort()
            pfad = urllib.parse.unquote(urllib.parse.urlsplit(route.request.url).path)
            if pfad.startswith("/medien/"):
                name = pfad[len("/medien/"):]
                if name not in self.medien:
                    self.medien[name] = self.api.medium(self.aid, name)
                daten = self.medien[name]
                if daten is None:
                    self.fehlt.append(name)
                    return route.fulfill(status=404, body=b"")
                return route.fulfill(status=200, body=daten, content_type=_bildtyp(name))
            if pfad == "/schrift/schriften.css":
                return route.fulfill(status=200, body=schriften_css(), content_type="text/css")
            if pfad.startswith("/schrift/"):
                datei = os.path.basename(pfad)
                voll = os.path.join(gestaltung.ORDNER_SCHRIFTEN, datei)
                if datei.endswith(".woff2") and os.path.isfile(voll):
                    with open(voll, "rb") as f:
                        return route.fulfill(status=200, body=f.read(), content_type="font/woff2")
            return route.abort()
        except Exception as e:  # noqa: BLE001 - Seite nicht haengen lassen, Fehler nach set_content melden
            if self.fehler is None:
                self.fehler = e
            return route.abort()


def _bildtyp(name: str) -> str:
    return {"png": "image/png", "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml"}.get(
        name.rsplit(".", 1)[-1].lower(), "image/jpeg")


class _EchterBrowser:
    def __init__(self):
        from playwright.sync_api import sync_playwright   # erst hier: Unit-Tests brauchen keinen Browser
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=True)
        except Exception:
            self._pw.stop()
            raise

    def new_page(self, **kwargs):
        return self._browser.new_page(**kwargs)

    def close(self):
        try:
            self._browser.close()
        finally:
            self._pw.stop()


def exportieren(api, auftrag, browser_starten=None, halten_takt_s: float = HALTEN_TAKT_S) -> str:
    aid = str(auftrag["id"])
    try:
        slug = auftrag["kontext"]["slug"]
        html = html_bauen(auftrag)
        namen = _fotografieren(api, auftrag, aid, slug, html, browser_starten or _EchterBrowser, halten_takt_s)
    except _Verloren:
        return "fehler"
    except Exception as e:  # noqa: BLE001
        if isinstance(e, ApiFehler) and e.code in FREMD:
            return "fehler"      # Auftrag gehoert uns nicht mehr
        try:
            api.zurueck(aid, NICHT_MOEGLICH + _grund(e))
        except Exception:  # noqa: BLE001 - die VM gibt den Auftrag nach Ablauf selbst frei
            pass
        return "fehler"
    api.fertig(aid, {"antwort": "Export fertig: " + ", ".join(namen), "bloecke": None,
                     "bildauftraege": [], "export_vorschlag": None, "notiz": ""})
    return "fertig"


class _Verloren(Exception):
    pass


def _schliessen(objekt) -> None:
    """Schliessen darf einen Ursprungsfehler nie ueberdecken."""
    schliessen = getattr(objekt, "close", None)
    if schliessen:
        try:
            schliessen()
        except Exception:  # noqa: BLE001
            pass


def _hoehe_pruefen(png: bytes) -> None:
    from PIL import Image
    with Image.open(io.BytesIO(png)) as bild:
        if bild.size[1] > MAX_HOEHE:
            raise ValueError("Newsletter ist zu lang für ein Bild (über 16 000 px)")


def _grund(e: BaseException) -> str:
    return str(e)[:150] if isinstance(e, (bloecke_mjml.RenderFehler, ValueError)) else _kurz(e)


def _fotografieren(api, auftrag, aid, slug, html, browser_starten, halten_takt_s) -> list[str]:
    namen = []
    with halten(api, aid, halten_takt_s) as halter:
        browser = browser_starten()
        try:
            for geraet, breite in GERAETE.items():
                if halter.verloren.is_set():
                    raise _Verloren()
                seite = browser.new_page(viewport={"width": breite, "height": HOEHE})
                try:
                    routen = _Routen(api, aid)
                    seite.route("**/*", routen)
                    seite.set_content(html, wait_until="networkidle")
                    if routen.fehler is not None:
                        raise routen.fehler
                    if routen.fehlt:
                        raise ValueError(f"Bild {routen.fehlt[0]} fehlt in den Medien")
                    seite.evaluate("document.fonts.ready")
                    png = seite.screenshot(full_page=True, type="png")
                finally:
                    _schliessen(seite)
                _hoehe_pruefen(png)
                namen.append(api.datei(aid, f"{slug}-{geraet}.jpg", jpeg_passend(png)))
        finally:
            _schliessen(browser)
        if halter.verloren.is_set():
            raise _Verloren()
    return namen
