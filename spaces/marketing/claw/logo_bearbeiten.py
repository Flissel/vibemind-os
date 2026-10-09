"""Logo bearbeiten (Spec sales-claw 2026-10-09-marke-exakt-logo-wissen §1). Reine Bildrechnung am PC mit
Pillow und numpy: Freistellen ueber den Farbabstand zur Randfarbe (weiche Kante), Zuschneiden auf die
Inhaltsgrenzen plus 4 % Rand, Einfarbig-Erkennung und zwei Fassungen (hell / dunkel). Das KI-Freistellen
(BiRefNet ueber ComfyUI) reicht der Aufrufer als Funktion herein. Jeder Fehler ist LogoFehler mit einem
Grund, den der Arbeiter als Hinweis meldet. Nie auf der VM importieren (numpy)."""
from __future__ import annotations

import io
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from spaces.marketing.claw.schoenheit import leuchtdichte

RAND_ANTEIL = 0.04          # Rand um das Zeichen, Anteil der laengeren Inhaltskante
RAND_STREIFEN = 0.02        # Breite des Randstreifens fuer die Hintergrundfarbe
ABSTAND_HART = 40.0         # Farbabstand (RGB, euklidisch): darunter ganz durchsichtig
ABSTAND_WEICH = 80.0        # darueber ganz deckend, dazwischen weiche Kante
DECKEND = 128
SICHTBAR = 16
EINFARBIG_ANTEIL = 0.90
EINFARBIG_TOLERANZ = 48.0
DUNKEL_GRUND = "#1a1a1a"
KONTRAST_DUNKEL = 3.0
MAX_KANTE = 1200
ARBEIT_KANTE = 2000
PNG_MAX = 1_900_000         # unter der 2-MB-Grenze der Arbeiter-Route /logo
MIN_DECKEND = 0.005
MAX_PIXEL = 50_000_000
FREISTELLEN = ("farbe", "ki", "nein")


class LogoFehler(ValueError):
    """Logo nicht bearbeitbar; die Meldung ist der Grund fuer den Hinweis."""


@dataclass
class Fassungen:
    hell: bytes
    dunkel: bytes
    einfarbig: bool
    hinweise: list[str] = field(default_factory=list)


def oeffnen(roh: bytes) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(roh)) as quelle:
                if quelle.width * quelle.height > MAX_PIXEL:
                    raise LogoFehler("Das Bild hat zu viele Bildpunkte")
                bild = quelle.convert("RGBA")
    except LogoFehler:
        raise
    except Exception as e:  # noqa: BLE001 - jedes unlesbare Bild ist derselbe Fall
        raise LogoFehler("Das Bild ist nicht lesbar") from e
    bild.thumbnail((ARBEIT_KANTE, ARBEIT_KANTE), Image.LANCZOS)
    return bild


def randfarbe(bild: Image.Image) -> tuple[int, int, int] | None:
    a = np.asarray(bild)
    hoehe, breite = a.shape[:2]
    s = max(1, round(min(hoehe, breite) * RAND_STREIFEN))
    rand = np.concatenate([a[:s].reshape(-1, 4), a[-s:].reshape(-1, 4),
                           a[:, :s].reshape(-1, 4), a[:, -s:].reshape(-1, 4)])
    deckend = rand[rand[:, 3] >= DECKEND]
    if len(deckend) < max(4, len(rand) // 4):          # Rand ueberwiegend durchsichtig: schon freigestellt
        return None
    return tuple(int(round(x)) for x in deckend[:, :3].astype(np.float64).mean(axis=0))


def _abstand(a: np.ndarray, farbe: tuple[int, int, int]) -> np.ndarray:
    return np.sqrt(((a[..., :3].astype(np.float32) - np.array(farbe, dtype=np.float32)) ** 2).sum(axis=2))


def farbe_freistellen(bild: Image.Image) -> Image.Image:
    grund = randfarbe(bild)
    if grund is None:
        return bild.copy()
    a = np.asarray(bild).astype(np.float32)
    deckung = np.clip((_abstand(a, grund) - ABSTAND_HART) / (ABSTAND_WEICH - ABSTAND_HART), 0.0, 1.0) * 255.0
    neu = a.copy()
    neu[..., 3] = np.minimum(a[..., 3], deckung)
    return Image.fromarray(neu.round().astype(np.uint8))


def zuschnitt(bild: Image.Image, frei: bool) -> Image.Image:
    a = np.asarray(bild)
    if frei:
        maske = a[..., 3] >= SICHTBAR
        fuellung = (0, 0, 0, 0)
    else:
        grund = randfarbe(bild) or (255, 255, 255)
        maske = (_abstand(a, grund) > ABSTAND_HART) & (a[..., 3] >= SICHTBAR)
        fuellung = (*grund, 255)
    zeilen = np.where(maske.any(axis=1))[0]
    spalten = np.where(maske.any(axis=0))[0]
    if not len(zeilen):
        raise LogoFehler("Kein Zeichen erkannt")
    oben, unten = int(zeilen[0]), int(zeilen[-1]) + 1
    links, rechts = int(spalten[0]), int(spalten[-1]) + 1
    breite, hoehe = rechts - links, unten - oben
    rand = max(1, round(RAND_ANTEIL * max(breite, hoehe)))
    neu = Image.new("RGBA", (breite + 2 * rand, hoehe + 2 * rand), fuellung)
    neu.paste(bild.crop((links, oben, rechts, unten)), (rand, rand))
    return neu


def _deckende_farben(bild: Image.Image) -> np.ndarray:
    a = np.asarray(bild.convert("RGBA"))
    return a[a[..., 3] >= DECKEND][:, :3].astype(np.float64)


def einfarbig(bild: Image.Image) -> bool:
    farben = _deckende_farben(bild)
    if not len(farben):
        raise LogoFehler("Kein Zeichen erkannt")
    stufen = (farben // 32).astype(np.int32)
    werte, zahl = np.unique(stufen, axis=0, return_counts=True)
    mitte = farben[(stufen == werte[zahl.argmax()]).all(axis=1)].mean(axis=0)
    nah = np.sqrt(((farben - mitte) ** 2).sum(axis=1)) <= EINFARBIG_TOLERANZ
    return float(nah.mean()) > EINFARBIG_ANTEIL


def _leuchtdichte(rgb: np.ndarray) -> np.ndarray:
    c = rgb / 255.0
    lin = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def logo_kontrast(bild: Image.Image, grund: str) -> float:
    """Kontrast der mittleren Leuchtdichte der deckenden Pixel gegen den Grund (WCAG-Formel)."""
    farben = _deckende_farben(bild)
    if not len(farben):
        return 1.0
    logo, flaeche = float(_leuchtdichte(farben).mean()), leuchtdichte(grund)
    hell, dunkel = max(logo, flaeche), min(logo, flaeche)
    return (hell + 0.05) / (dunkel + 0.05)


def einfaerben(bild: Image.Image, farbe: str) -> Image.Image:
    r, g, b = (int(farbe[i:i + 2], 16) for i in (1, 3, 5))
    a = np.asarray(bild.convert("RGBA")).copy()
    a[..., 0], a[..., 1], a[..., 2] = r, g, b
    return Image.fromarray(a)


def _png(bild: Image.Image) -> bytes:
    bild = bild.copy()
    bild.thumbnail((MAX_KANTE, MAX_KANTE), Image.LANCZOS)
    while True:
        puffer = io.BytesIO()
        bild.save(puffer, "PNG", optimize=True)
        if puffer.tell() <= PNG_MAX or max(bild.size) <= 64:
            return puffer.getvalue()
        bild = bild.resize((max(1, int(bild.width * 0.8)), max(1, int(bild.height * 0.8))), Image.LANCZOS)


def als_png(roh: bytes) -> bytes:
    """Beliebiges Quellbild als PNG unter der Groessengrenze (fuer das 'Original' in der Vorschau)."""
    return _png(oeffnen(roh))


def _hat_transparenz(bild: Image.Image) -> bool:
    return float((np.asarray(bild)[..., 3] < 255).mean()) > 0.01


def fassungen(roh: bytes, *, freistellen: str, zuschneiden: bool, textfarbe: str,
              ki: Callable[[bytes], bytes] | None = None) -> Fassungen:
    if freistellen not in FREISTELLEN:
        raise LogoFehler(f"Unbekannte Freistell-Art {str(freistellen)[:20]!r}")
    bild = oeffnen(roh)
    hinweise: list[str] = []
    if freistellen == "farbe":
        bild = farbe_freistellen(bild)
    elif freistellen == "ki":
        if ki is None:
            raise LogoFehler("KI-Freistellen nicht verfügbar")
        try:
            bild = oeffnen(ki(_png(bild)))
        except LogoFehler:
            raise
        except Exception as e:  # noqa: BLE001 - ComfyUI, Netz, Modell: alles derselbe Rueckfall
            raise LogoFehler(f"KI-Freistellen gescheitert ({type(e).__name__})") from None
    frei = freistellen != "nein" or _hat_transparenz(bild)
    if zuschneiden:
        bild = zuschnitt(bild, frei)
    if float((np.asarray(bild)[..., 3] >= DECKEND).mean()) < MIN_DECKEND:
        raise LogoFehler("Kein Zeichen erkannt")
    if not frei:
        hinweise.append("Logo mit Fläche: für dunkle Flächen nicht angepasst")
        png = _png(bild)
        return Fassungen(png, png, False, hinweise)
    ein = einfarbig(bild)
    hell = einfaerben(bild, textfarbe) if ein else bild
    if ein:
        dunkel = einfaerben(bild, "#ffffff")
    elif logo_kontrast(bild, DUNKEL_GRUND) >= KONTRAST_DUNKEL:
        dunkel = bild
    else:
        dunkel = einfaerben(bild, "#ffffff")
        hinweise.append("Mehrfarbiges Logo zu dunkel für dunkle Flächen: weiße Silhouette")
    return Fassungen(_png(hell), _png(dunkel), ein, hinweise)
