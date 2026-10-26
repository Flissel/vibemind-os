"""Dateinamen fuer Geschaeftsnotizen: Stamm aus Freitext, Eindeutigkeit, Pfadpruefung.

Regeln (Plan Teil B, "Namen"): NFC; Windows-verbotene Zeichen und Steuerzeichen werden zu
Leerzeichen, Leerraum wird zusammengefasst, Punkte und Leerzeichen am Rand fallen weg, reservierte
Geraetenamen bekommen `_`. Leer wird zum Ersatz. Gleiche Namen (casefold) behaelt der aelteste
Eintrag, alle weiteren heissen `<Name> (<id8>)`. Der ganze Pfad ab `knowledge/` muss
`wissens_spiegel._windows_tauglich` bestehen.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

try:
    from wissens_spiegel import MAX_PFAD, _WIN_RESERVIERT, _WIN_VERBOTEN, _windows_tauglich
except ImportError:  # Aufruf ohne vorbereiteten sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from wissens_spiegel import MAX_PFAD, _WIN_RESERVIERT, _WIN_VERBOTEN, _windows_tauglich

_VERBOTEN = re.compile("[" + re.escape("".join(sorted(_WIN_VERBOTEN | {"/", "\x7f"}))) + "]")
_LEERRAUM = re.compile(r"\s+")
_ZUSATZ = re.compile(r"^(?P<kopf>.*?)(?P<zusatz> \([0-9A-Za-z-]+\))?$", re.DOTALL)
MIN_STAMM = 8


def _saeubern(roh: str) -> str:
    s = unicodedata.normalize("NFC", roh)
    s = _VERBOTEN.sub(" ", s)
    s = _LEERRAUM.sub(" ", s)
    return s.strip(" .")


def _reserviert_schuetzen(s: str) -> str:
    erster, punkt, rest = s.partition(".")
    if erster.upper() in _WIN_RESERVIERT:
        return f"{erster}_{punkt}{rest}"
    return s


def dateistamm(roh: str | None, ersatz: str, max_len: int) -> str:
    """Dateistamm (ohne Endung) aus Freitext; leer wird zu `ersatz`, hoechstens `max_len` Zeichen."""
    s = _saeubern(roh or "")
    if not s:
        return ersatz
    s = s[:max_len].rstrip(" .")
    if _reserviert_schuetzen(s) != s:
        s = _reserviert_schuetzen(s[:max_len - 1].rstrip(" ."))
    return s or ersatz


def eindeutig(eintraege: list[tuple[str, str, datetime]]) -> dict[str, str]:
    """(schluessel, stamm, created_at) -> Endstamm. Gleiche Staemme (NFC, casefold): der aelteste
    (`created_at`, dann `schluessel`) behaelt den Namen, alle weiteren bekommen ` (<id8>)`."""
    gruppen: dict[str, list[tuple[datetime, str, str]]] = {}
    for schluessel, stamm, created_at in eintraege:
        stamm = unicodedata.normalize("NFC", stamm)
        gruppen.setdefault(stamm.casefold(), []).append((created_at, schluessel, stamm))
    ergebnis: dict[str, str] = {}
    belegt = set(gruppen)
    for gruppe in gruppen.values():
        gruppe.sort(key=lambda g: (g[0], g[1]))
        _, erster, stamm = gruppe[0]
        ergebnis[erster] = stamm
        for _, schluessel, stamm in gruppe[1:]:
            neu = f"{stamm} ({schluessel[:8]})"
            if neu.casefold() in belegt:
                neu = f"{stamm} ({schluessel})"
            belegt.add(neu.casefold())
            ergebnis[schluessel] = neu
    return ergebnis


def relpfad(ordner: str, stamm: str, endung: str = ".md") -> str:
    """`<ordner>/<stamm><endung>`, der Stamm so gekuerzt (vor einem ` (<id>)`-Zusatz), dass der Pfad
    `_windows_tauglich` ist. Wirft `ValueError`, wenn selbst `MIN_STAMM` Zeichen nicht passen."""
    pfad = f"{ordner}/{stamm}{endung}"
    if _windows_tauglich(pfad):
        return pfad
    teile = _ZUSATZ.match(stamm)
    kopf, zusatz = teile.group("kopf"), teile.group("zusatz") or ""
    platz = MAX_PFAD - len(ordner) - 1 - len(zusatz) - len(endung)
    if platz < MIN_STAMM:
        raise ValueError("Pfad zu lang fuer einen Dateinamen")
    kopf = kopf[:platz].rstrip(" .")
    pfad = f"{ordner}/{kopf}{zusatz}{endung}"
    if not kopf or not _windows_tauglich(pfad):
        raise ValueError("Pfad nicht Windows-tauglich")
    return pfad
