"""Eigene Medienablage je Firma (Stufe B, Tag 4, 25.11.2026).

Reels (MP4), ihre Vorschaubilder (JPG) und Podcast-Folgen (MP3) lagen bisher nur in der Ablage der Laeden
(MARKETING_BILD_ORDNER, die Medien von Sales). Marketing bekommt eine eigene Ablage mit einem Ordner je Firma:

    MARKETING_FIRMEN_ABLAGE/<firma>/<datei>

- **Ablegen:** Video- und Ton-Arbeiter schreiben wie bisher in die Ablage der Laeden (Sales braucht die Datei
  fuer Freigabe und Versand, bis der Versandauftrag sie mitnimmt, Stufe C) und legen sie zusaetzlich hier ab.
  Ohne MARKETING_FIRMEN_ABLAGE passiert hier nichts; ein Fehler hier bricht den Arbeiter nie ab.
- **Herueberholen:** `python -m spaces.marketing.api.medienablage` zaehlt, was einmal kopiert wuerde;
  mit `--ausfuehren` kopiert es. Nur Dateien fertiger Auftraege, nur in den Ordner ihrer Firma.
- **Lesen:** `finden(firma, name)` nimmt die eigene Ablage, sonst (bis zum Herueberholen) die der Laeden.
  Wer darf, entscheidet der Aufrufer (die Oberflaeche signiert Firma und Name).

Erlaubt sind nur die Namen, die die Arbeiter selbst vergeben (vid-/pod-), nie Pfade.

Eigener Umgebungsname: MARKETING_MEDIEN_ORDNER ist schon der flache Medienordner (pult, bilder, gestaltung,
video lesen ihn ohne Firmenordner). Diese Ablage darf nie dorthin zeigen."""
from __future__ import annotations

import logging
import os
import re
import shutil
import sys

log = logging.getLogger(__name__)

ENV = "MARKETING_FIRMEN_ABLAGE"
LAEDEN_ENV = "MARKETING_BILD_ORDNER"
_NAME = re.compile(r"^(?:vid-[0-9a-f]{8}-[a-z0-9-]{1,40}\.(?:mp4|jpg)|pod-[0-9a-f]{8}-[a-z0-9-]{1,60}\.mp3)$")
_FIRMA = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
TYPEN = {".mp4": "video/mp4", ".mp3": "audio/mpeg", ".jpg": "image/jpeg"}


def name_ok(name) -> bool:
    return isinstance(name, str) and _NAME.fullmatch(name) is not None


def firma_ok(firma) -> bool:
    return isinstance(firma, str) and _FIRMA.fullmatch(firma) is not None


def typ(name: str) -> str:
    return TYPEN[os.path.splitext(name)[1]]


def _ordner_aus(env: str) -> str | None:
    o = os.environ.get(env, "").strip()
    return o if o and os.path.isdir(o) else None


FLACHE_ENVS = ("MARKETING_MEDIEN_ORDNER", LAEDEN_ENV)


def wurzel() -> str | None:
    """Die eigene Ablage, wenn eingerichtet (Ordner existiert); sonst None. Zeigt sie auf einen der flachen
    Medienordner, gilt sie als nicht eingerichtet (sonst laegen Firmenordner zwischen den Mediendateien)."""
    eigen = _ordner_aus(ENV)
    if eigen is None:
        return None
    flach = {os.path.realpath(o) for o in (os.environ.get(n, "").strip() for n in FLACHE_ENVS) if o}
    return None if os.path.realpath(eigen) in flach else eigen


def _datei(ordner: str, name: str) -> str | None:
    """Pfad einer echten Datei direkt in `ordner` (kein Link, nicht ausserhalb)."""
    pfad = os.path.join(ordner, name)
    if os.path.islink(pfad) or not os.path.isfile(pfad):
        return None
    if os.path.dirname(os.path.realpath(pfad)) != os.path.realpath(ordner):
        return None
    return pfad


def finden(firma: str, name: str) -> str | None:
    if not firma_ok(firma) or not name_ok(name):
        return None
    eigen = wurzel()
    if eigen is not None and os.path.isdir(os.path.join(eigen, firma)):
        pfad = _datei(os.path.join(eigen, firma), name)
        if pfad is not None:
            return pfad
    laeden = _ordner_aus(LAEDEN_ENV)
    return _datei(laeden, name) if laeden is not None else None


def _firmenordner(firma: str) -> str | None:
    eigen = wurzel()
    if eigen is None or not firma_ok(firma):
        return None
    ziel = os.path.join(eigen, firma)
    os.makedirs(ziel, mode=0o755, exist_ok=True)
    return ziel


def _atomar(ziel: str, schreiben) -> None:
    zwischen = ziel + ".teil"
    try:
        schreiben(zwischen)
        os.chmod(zwischen, 0o644)
        os.replace(zwischen, ziel)
    except OSError:
        try:
            os.remove(zwischen)
        except OSError:
            pass
        raise


def ablegen(firma: str, name: str, roh: bytes) -> bool:
    """Zusaetzlich in die eigene Ablage legen. Nie eine Ausnahme: ohne Ablage oder bei Fehlern False."""
    if not name_ok(name):
        return False
    try:
        ordner = _firmenordner(firma)
        if ordner is None:
            return False

        def schreiben(pfad: str) -> None:
            with open(pfad, "wb") as f:
                f.write(roh)
        _atomar(os.path.join(ordner, name), schreiben)
        return True
    except OSError as f:
        log.warning("Medienablage: %s nicht abgelegt (%s)", name, type(f).__name__)
        return False


def uebernehmen(paare: list[tuple[str, str]], ausfuehren: bool = False) -> dict:
    """(firma, name)-Paare aus der Ablage der Laeden in die eigene kopieren. Zaehlt kopiert/schon_da/fehlt/
    ungueltig; ohne `ausfuehren` wird nur gezaehlt (kopiert = wuerde kopiert)."""
    zahl = {"kopiert": 0, "schon_da": 0, "fehlt": 0, "ungueltig": 0}
    eigen, laeden = wurzel(), _ordner_aus(LAEDEN_ENV)
    if eigen is None or laeden is None:
        raise RuntimeError(f"{ENV} und {LAEDEN_ENV} muessen auf vorhandene Ordner zeigen")
    for firma, name in dict.fromkeys(paare):
        if not firma_ok(firma) or not name_ok(name):
            zahl["ungueltig"] += 1
            continue
        quelle = _datei(laeden, name)
        if quelle is None:
            zahl["fehlt"] += 1
            continue
        ziel = os.path.join(eigen, firma, name)
        if os.path.isfile(ziel) and os.path.getsize(ziel) == os.path.getsize(quelle):
            zahl["schon_da"] += 1
            continue
        if ausfuehren:
            ordner = _firmenordner(firma)
            _atomar(os.path.join(ordner, name), lambda pfad: shutil.copyfile(quelle, pfad))
        zahl["kopiert"] += 1
    return zahl


def paare_aus_db() -> list[tuple[str, str]]:
    """Alle Dateien fertiger Reels (MP4 und Vorschaubild) und Folgen mit ihrer Firma."""
    from spaces.marketing.api.pult import _lesen
    zeilen = _lesen(lambda:
        "SELECT mandant, datei FROM marketing.video_auftraege WHERE status = 'fertig' AND datei IS NOT NULL "
        "UNION ALL SELECT mandant, poster FROM marketing.video_auftraege "
        "WHERE status = 'fertig' AND poster IS NOT NULL "
        "UNION ALL SELECT mandant, datei FROM marketing.ton_auftraege WHERE status = 'fertig' AND datei IS NOT NULL")
    return [(str(z.get("mandant")), str(z.get("datei"))) for z in zeilen if isinstance(z, dict)]


def main(argv: list[str]) -> int:
    ausfuehren = "--ausfuehren" in argv
    zahl = uebernehmen(paare_aus_db(), ausfuehren)
    print(("kopiert" if ausfuehren else "Probe (nichts kopiert)") + ": "
          + ", ".join(f"{k} {v}" for k, v in zahl.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
