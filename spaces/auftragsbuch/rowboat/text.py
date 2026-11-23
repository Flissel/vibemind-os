"""Text eines Rowboat-Vorschlags bauen und die Kennung wieder herauslesen (reine Funktionen).

Format: `[Gehirn] <Titel> · fällig <JJJJ-MM-TT> · [Öffnen](<Link>) · #<punkt_id>`;
`fällig` nur mit Frist, `[Öffnen](...)` nur mit brauchbarem Link.
"""
from __future__ import annotations

import re
from urllib.parse import unquote

TITEL_MAX = 160
_KENNUNG_AM_ENDE = re.compile(r"#([a-f0-9]{16})$")
_TITEL_VERBOTEN = re.compile(r"[@\[\]()]")
_LINK_KODIEREN = {"@": "%40", "[": "%5B", "]": "%5D", "(": "%28", ")": "%29"}
_FREIGABE = "/freigaben#fg-"


def kennung_in(text: object) -> str | None:
    """`#<16 hex>` am Ende, nachdem Leerraum zusammengefasst und klein geschrieben wurde.

    So liefert Rowboat seine Signale (normalisierter Schluessel); kein str -> None.
    """
    if not isinstance(text, str):
        return None
    treffer = _KENNUNG_AM_ENDE.search(re.sub(r"\s+", " ", text).strip().lower())
    return treffer.group(1) if treffer else None


def _titel(roh: str) -> str:
    ohne = _TITEL_VERBOTEN.sub(" ", roh)
    return re.sub(r"\s+", " ", ohne).strip()[:TITEL_MAX].rstrip()


def _ist_freigabe_link(link: str) -> bool:
    # Der Vertrag sieht `%23` nicht; darum hier mehrfach dekodieren (bis stabil, hoechstens 3x).
    for _ in range(3):
        if _FREIGABE in link.lower():
            return True
        neu = unquote(link)
        if neu == link:
            break
        link = neu
    return _FREIGABE in link.lower()


def _link(roh: str) -> str:
    if not roh or _ist_freigabe_link(roh):
        return ""
    return "".join(_LINK_KODIEREN.get(z, z) for z in roh)


def vorschlag(punkt: dict) -> str:
    teile = [f"[Gehirn] {_titel(punkt['titel'])}"]
    if punkt.get("frist"):
        teile.append(f"fällig {punkt['frist']}")
    link = _link(punkt.get("link") or "")
    if link:
        teile.append(f"[Öffnen]({link})")
    teile.append(f"#{punkt['punkt_id']}")
    return " · ".join(teile)
