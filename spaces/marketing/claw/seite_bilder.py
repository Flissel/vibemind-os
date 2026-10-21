"""Bildstuecke der Collage-Mail: rendert die Seite in headless Chromium (Geraetefaktor 2, Medien und Schriften nur
von der lokalen Platte) und schneidet aus dem Foto des Seitencontainers je ein JPEG pro Bildband (Stueck aus
seite_mail.stuecke) und misst dabei die gerenderten Ebenen (render_und_messen), damit Planer und Zuschnitt die
echte Ausdehnung kennen. Laeuft in der Marketing-API auf der VM (Playwright+Chromium wie pdf_druck) und am PC.
Platzhalter-Bilder gibt es hier nicht: in einer Mail waeren sie ein Fehler."""
from __future__ import annotations

import io
import os
import threading
from typing import Callable

from spaces.marketing.claw import bildplaetze, seite_html, seite_streifen
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


# Huellen aller Ebenen (data-ebene aus seite_html) relativ zum Seitencontainer. getBoundingClientRect liefert
# CSS-Pixel (unabhaengig vom Geraetefaktor) = Seiteneinheiten, und bei Drehung die achsenparallele Huelle.
# Dazu je Ebene mit Schrift (Text, Knopf; Bilder und Formen haben keine Textknoten) die Zeilenkaesten der Schrift (Range.getClientRects ueber ihre Textknoten): die
# Glyphen-Ausdehnung, an der die Schoenheitspruefung Kontrast und Ruhezone misst - ein schmales, mittig gesetztes
# "NEU" in einem breiten Kasten liegt nur dort, wo seine Zeile steht. Leere Kaesten (Breite oder Hoehe 0) fallen weg.
# Laeuft auch mit java_script_enabled=False (das sperrt nur Skripte der Seite, nicht evaluate).
_MESSEN = """async (sel) => {
  if (document.fonts && document.fonts.ready) { await document.fonts.ready; }
  const c = document.querySelector(sel);
  if (!c) { return {huellen: {}, zeilen: {}}; }
  const b = c.getBoundingClientRect();
  const huellen = {}, zeilen = {};
  const kasten = (r) => [r.left - b.left, r.top - b.top, r.right - b.left, r.bottom - b.top];
  for (const el of c.querySelectorAll('[data-ebene]')) {
    const id = el.getAttribute('data-ebene');
    huellen[id] = kasten(el.getBoundingClientRect());
    const liste = [];
    const gang = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = gang.nextNode(); n; n = gang.nextNode()) {
      if (!n.nodeValue || !n.nodeValue.trim()) { continue; }
      const bereich = document.createRange();
      bereich.selectNodeContents(n);
      for (const r of bereich.getClientRects()) {
        if (r.width > 0 && r.height > 0) { liste.push(kasten(r)); }
      }
    }
    if (liste.length) { zeilen[id] = liste; }
  }
  return {huellen: huellen, zeilen: zeilen};
}"""


def render_png(seite: dict, medien_lesen: Callable[[str], bytes | None], *, faktor: int = FAKTOR,
               browser_starten=None) -> bytes:
    """Nur das PNG (siehe render_und_messen)."""
    return render_und_messen(seite, medien_lesen, faktor=faktor, browser_starten=browser_starten)[0]


def render_und_messen(seite: dict, medien_lesen: Callable[[str], bytes | None], *, faktor: int = FAKTOR,
                      browser_starten=None, platzhalter_kacheln: bool = False, mit_zeilen: bool = False):
    """(PNG des Seitencontainers, masse). PNG: 600 x hoehe Einheiten, mal faktor Pixel. masse: je Ebenen-id die
    gerenderte achsenparallele Huelle (links, oben, rechts, unten) in Seiteneinheiten - Text waechst in seite_html
    ueber seine deklarierte hoehe hinaus (min-height), der Streifen-Planer braucht die echte Ausdehnung.
    Ein fehlendes Medium, ein Platzhalter oder ein unzulaessiger Name wirft StueckFehler("Bild <name> fehlt");
    ein Zeitlimit StueckFehler(ZEITLIMIT). Rendert hoechstens einer gleichzeitig.
    platzhalter_kacheln=True (nur die Schoenheitspruefung, wie das Pruefbild): Platzhalter werden als neutrale
    graue Kachel gezeichnet statt abgelehnt; ein fehlendes echtes Medium bleibt StueckFehler. Nie fuer die Mail.
    mit_zeilen=True (Schoenheitspruefung): (PNG, masse, zeilen) - zeilen je Text-/Knopf-Ebene die Zeilenkaesten
    der Schrift (messen_alles), an denen seite_schoenheit Kontrast und Ruhezone misst."""
    html = seite_html.html(seite, betreff="", pflichtteil={}, bild_basis=BASIS + "medien/",
                           schrift_basis=BASIS + "schrift/")

    def holen(name: str) -> bytes | None:
        if not _name_ok(name) or (not platzhalter_kacheln and bildplaetze.PLATZHALTER.match("medien:" + name)):
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
                    routen = Routen(None, None, pruefbild=platzhalter_kacheln, medium_holen=holen)
                    page.route("**/*", routen)
                    page.set_content(html, wait_until="networkidle", timeout=ms)
                    if routen.fehler is not None:
                        raise routen.fehler
                    if routen.fehlt:
                        raise StueckFehler(f"Bild {routen.fehlt[0]} fehlt")
                    # erst messen (wartet auf document.fonts.ready), dann fotografieren: beide sehen dieselbe,
                    # fertig gesetzte Schrift
                    masse, zeilen = messen_alles(page)
                    png = page.locator(seite_html.SEITE_CONTAINER).first.screenshot(type="png", timeout=ms)
                    return (png, masse, zeilen) if mit_zeilen else (png, masse)
                finally:
                    _zu(page)
            finally:
                _zu(browser)
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "TimeoutError":   # playwright.sync_api.TimeoutError
                raise StueckFehler(ZEITLIMIT) from e
            raise


Zeilen = dict[str, list[tuple[float, float, float, float]]]


def messen(page) -> dict[str, tuple[float, float, float, float]]:
    """Gerenderte Huellen aller Ebenen einer geladenen Playwright-Seite (auch fuer das Pruefbild am PC)."""
    return messen_alles(page)[0]


def messen_alles(page) -> tuple[dict[str, tuple[float, float, float, float]], Zeilen]:
    """(Huellen, Zeilenkaesten der Schrift) einer geladenen Playwright-Seite, beides in Seiteneinheiten."""
    roh = page.evaluate(_MESSEN, seite_html.SEITE_CONTAINER)
    if isinstance(roh, dict) and isinstance(roh.get("huellen"), dict) and isinstance(roh.get("zeilen"), dict):
        return _masse(roh["huellen"]), zeilen_pruefen(roh["zeilen"])
    return _masse(roh), {}          # alte Antwortform (nur Huellen)


def _masse(roh) -> dict[str, tuple[float, float, float, float]]:
    """Antwort von _MESSEN pruefen: nur id -> 4 endliche Zahlen (auf 1/100 gerundet)."""
    if not isinstance(roh, dict):
        return {}
    return {k: tuple(round(z, 2) for z in v) for k, v in seite_streifen.masse_pruefen(roh).items()}


def zeilen_pruefen(roh) -> Zeilen:
    """Zeilenkaesten pruefen: je id eine Liste gueltiger, nicht leerer Kaesten (wie masse_pruefen, auf 1/100
    gerundet); eine id
    ohne gueltigen Kasten faellt weg (dann gilt die Huelle der Ebene)."""
    aus: Zeilen = {}
    if not isinstance(roh, dict):
        return aus
    for eid, liste in roh.items():
        if not isinstance(eid, str) or not isinstance(liste, (list, tuple)):
            continue
        gut = seite_streifen.masse_pruefen({str(i): k for i, k in enumerate(liste[:200])})
        kaesten = [tuple(round(z, 2) for z in gut[str(i)]) for i in range(len(liste[:200]))
                   if str(i) in gut and gut[str(i)][2] > gut[str(i)][0] and gut[str(i)][3] > gut[str(i)][1]]
        if kaesten:
            aus[eid] = kaesten
    return aus


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
