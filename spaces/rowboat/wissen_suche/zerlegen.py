"""Zerlegt eine Markdown-Notiz in Abschnitte fuer die Einbettung.

Abschnitte folgen den Ueberschriften ``#`` bis ``###``. Zu lange Abschnitte werden an Absaetzen
geteilt (hoechstens ``MAX_ZEICHEN``), zu kurze mit dem naechsten verschmolzen. Jedem Abschnitt wird
``Titel / Ordner / Ueberschrift`` vorangestellt, damit kurze Steckbriefe ueber Namen und Kategorie
(Ordner) auffindbar sind.
"""
from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath
from typing import NamedTuple

log = logging.getLogger(__name__)

MAX_ZEICHEN = 2400
MIN_ZEICHEN = 200
MAX_ABSCHNITTE = 200

_UEBERSCHRIFT = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
_ZAUN = re.compile(r"^\s*(```|~~~)")


class Abschnitt(NamedTuple):
    nr: int
    ueberschrift: str
    text: str


class _Teil(NamedTuple):
    ueberschrift: str
    kopfzeile: str      # Ueberschrift-Zeile wie in der Datei ("" fuer Text vor der ersten Ueberschrift)
    rumpf: str


def _bloecke(inhalt: str, titel: str) -> list[_Teil]:
    """Zeilenweise Zustandsmaschine: Ueberschriften ausserhalb von Codebloecken und YAML-Kopf."""
    zeilen = inhalt.split("\n")
    teile: list[_Teil] = []
    stapel: list[tuple[int, str]] = []
    ueberschrift, kopfzeile, rumpf = titel, "", []
    im_code = False
    start = 0
    # YAML-Kopf gehoert zum Text vor der ersten Ueberschrift; "#" darin ist keine Ueberschrift.
    if zeilen and zeilen[0].strip() == "---":
        for i in range(1, len(zeilen)):
            if zeilen[i].strip() in ("---", "..."):
                rumpf = zeilen[: i + 1]
                start = i + 1
                break
    for zeile in zeilen[start:]:
        if _ZAUN.match(zeile):
            im_code = not im_code
            rumpf.append(zeile)
            continue
        treffer = None if im_code else _UEBERSCHRIFT.match(zeile)
        if treffer is None:
            rumpf.append(zeile)
            continue
        teile.append(_Teil(ueberschrift, kopfzeile, "\n".join(rumpf).strip()))
        ebene, name = len(treffer.group(1)), treffer.group(2).strip()
        while stapel and stapel[-1][0] >= ebene:
            stapel.pop()
        stapel.append((ebene, name))
        ueberschrift, kopfzeile, rumpf = " > ".join(n for _, n in stapel), zeile.strip(), []
    teile.append(_Teil(ueberschrift, kopfzeile, "\n".join(rumpf).strip()))
    return teile


def _hart_teilen(absatz: str) -> list[str]:
    """Ein einzelner Absatz ueber MAX_ZEICHEN: an der letzten Satzgrenze '. ' vor der Grenze teilen."""
    stuecke: list[str] = []
    rest = absatz
    while len(rest) > MAX_ZEICHEN:
        schnitt = rest.rfind(". ", 0, MAX_ZEICHEN)
        schnitt = schnitt + 1 if schnitt > 0 else MAX_ZEICHEN
        stuecke.append(rest[:schnitt].strip())
        rest = rest[schnitt:].strip()
    if rest:
        stuecke.append(rest)
    return stuecke


def _an_absaetzen_teilen(rumpf: str) -> list[str]:
    if len(rumpf) <= MAX_ZEICHEN:
        return [rumpf]
    stuecke: list[str] = []
    aktuell = ""
    for absatz in (a.strip() for a in re.split(r"\n\s*\n", rumpf)):
        if not absatz:
            continue
        for teil in _hart_teilen(absatz):
            if aktuell and len(aktuell) + 2 + len(teil) > MAX_ZEICHEN:
                stuecke.append(aktuell)
                aktuell = teil
            else:
                aktuell = f"{aktuell}\n\n{teil}" if aktuell else teil
    if aktuell:
        stuecke.append(aktuell)
    return stuecke


def _verbinden(*teile: str) -> str:
    return "\n\n".join(t for t in teile if t)


def zerlegen(rel_pfad: str, inhalt: str) -> list[Abschnitt]:
    """Zerlegt die Notiz ``rel_pfad`` (relativ zu ``knowledge/``) in Abschnitte."""
    inhalt = inhalt.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not inhalt.strip():
        return []
    pfad = PurePosixPath(rel_pfad)
    titel = pfad.stem
    ordner = "" if str(pfad.parent) == "." else str(pfad.parent)

    # 1. Lange Abschnitte teilen; die Kopfzeile gehoert nur zum ersten Stueck eines Abschnitts.
    stuecke: list[tuple[str, str, str]] = []          # (ueberschrift, kopfzeile, rumpf)
    for teil in _bloecke(inhalt, titel):
        if not teil.rumpf and not teil.kopfzeile:
            continue
        for i, rumpf in enumerate(_an_absaetzen_teilen(teil.rumpf)):
            stuecke.append((teil.ueberschrift, "" if i else teil.kopfzeile, rumpf))

    # 2. Kleine Stuecke mit dem naechsten verschmelzen, solange MAX_ZEICHEN nicht ueberschritten wird.
    #    Die Kopfzeile des Nachfolgers wandert in den Text, damit seine Ueberschrift erhalten bleibt.
    zusammen: list[list[str]] = []                     # [ueberschrift, rumpf, kopfzeile]
    for ueberschrift, kopf, rumpf in stuecke:
        if zusammen and len(zusammen[-1][1]) < MIN_ZEICHEN:
            neu = _verbinden(zusammen[-1][1], kopf, rumpf)
            if len(neu) <= MAX_ZEICHEN:
                zusammen[-1][1] = neu
                continue
        zusammen.append([ueberschrift, rumpf, kopf])
    # Ein kleiner letzter Abschnitt wandert in den vorherigen.
    if len(zusammen) > 1 and len(zusammen[-1][1]) < MIN_ZEICHEN:
        neu = _verbinden(zusammen[-2][1], zusammen[-1][2], zusammen[-1][1])
        if len(neu) <= MAX_ZEICHEN:
            zusammen.pop()
            zusammen[-1][1] = neu

    zusammen = [z for z in zusammen if z[1].strip()]
    if len(zusammen) > MAX_ABSCHNITTE:
        log.warning("%s: %d Abschnitte, nur die ersten %d werden eingebettet",
                    rel_pfad, len(zusammen), MAX_ABSCHNITTE)
        zusammen = zusammen[:MAX_ABSCHNITTE]
    return [Abschnitt(nr, u, f"{titel} / {ordner} / {u}\n\n{r}") for nr, (u, r, _) in enumerate(zusammen)]
