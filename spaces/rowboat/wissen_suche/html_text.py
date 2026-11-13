"""Sichtbarer Text und Titel eines HTML-Konzepts fuer die Wissenssuche.

Nur was man auf der Seite liest, kommt in den Index: ``script`` (auch der Datenblock
``rowboat-konzept``), ``style``, Kommentare und Tags bleiben draussen. Gelesen werden hoechstens
``MAX_ZEICHEN`` Zeichen. Nur Standardbibliothek (``html.parser``).
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

MAX_ZEICHEN = 1_048_576

_UEBERSPRINGEN = frozenset({"script", "style"})
_LEERRAUM = re.compile(r"\s+")


class _Sammler(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stuecke: list[str] = []
        self.titel_stuecke: list[str] = []
        self.titel_gesehen = False
        self._tiefe = 0          # > 0 innerhalb von script/style
        self._im_titel = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _UEBERSPRINGEN:
            self._tiefe += 1
        elif tag == "title" and not self.titel_gesehen:
            self._im_titel = True

    def handle_endtag(self, tag: str) -> None:
        if tag in _UEBERSPRINGEN:
            self._tiefe = max(0, self._tiefe - 1)
        elif tag == "title" and self._im_titel:
            self._im_titel = False
            self.titel_gesehen = True

    def handle_data(self, data: str) -> None:
        if self._tiefe:
            return
        self.stuecke.append(data)
        if self._im_titel:
            self.titel_stuecke.append(data)


def _parsen(html: str) -> _Sammler:
    sammler = _Sammler()
    sammler.feed(html[:MAX_ZEICHEN])
    sammler.close()
    return sammler


def _glatt(text: str) -> str:
    return _LEERRAUM.sub(" ", text).strip()


def sichtbarer_text(html: str) -> str:
    """Der lesbare Text (inkl. Titel), Leerraum zusammengezogen."""
    return _glatt(" ".join(_parsen(html).stuecke))


def titel(html: str) -> str | None:
    """Inhalt des ersten ``<title>``; ``None``, wenn es keinen (nicht leeren) gibt."""
    text = _glatt("".join(_parsen(html).titel_stuecke))
    return text or None
