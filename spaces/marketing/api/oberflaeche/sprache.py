"""Sprachwahl der Marketing-Oberflaeche (Stufe A, 24.11.2026).

Reihenfolge: eigener Cookie `sprache` (Umschalter DE/EN auf jeder Seite), sonst der Kopf
`Accept-Language` (den setzt spaeter die App je Space), sonst Deutsch. Texte kommen aus einem
Woerterbuch je Modul (`TEXTE = {"de": {...}, "en": {...}}`), nie aus f-Strings im Seitencode.

Uebersetzt wird nur die Anzeige. Inhalte (Titel, Skripte, Markenabschnitte, Gruende der API)
behalten ihre eigene Sprache."""
from __future__ import annotations

SPRACHEN = ("de", "en")
VORGABE = "de"
COOKIE = "sprache"
_KOPF_MAX = 500


def aus_accept_language(kopf: str | None) -> str | None:
    """Die beste unterstuetzte Sprache aus `Accept-Language` (q-Werte beachtet), sonst None.
    Unlesbare Teile werden uebergangen; bei gleichem q gewinnt die fruehere Angabe."""
    if not isinstance(kopf, str) or not kopf.strip():
        return None
    kandidaten: list[tuple[float, int, str]] = []
    for nr, teil in enumerate(kopf[:_KOPF_MAX].split(",")):
        stuecke = [s.strip() for s in teil.split(";")]
        tag = stuecke[0].lower()
        if not tag:
            continue
        q = 1.0
        for p in stuecke[1:]:
            name, _, wert = p.partition("=")
            if name.strip().lower() == "q":
                try:
                    q = float(wert.strip())
                except ValueError:
                    q = 0.0
        haupt = tag.split("-")[0]
        if q > 0 and haupt in SPRACHEN:
            kandidaten.append((-q, nr, haupt))
    if not kandidaten:
        return None
    return sorted(kandidaten)[0][2]


def waehlen(cookie: str | None, accept_language: str | None) -> str:
    """Cookie vor Accept-Language vor Deutsch. Nur bekannte Werte zaehlen."""
    if isinstance(cookie, str) and cookie.strip().lower() in SPRACHEN:
        return cookie.strip().lower()
    return aus_accept_language(accept_language) or VORGABE


def text(texte: dict, sprache: str, schluessel: str, **werte) -> str:
    """Ein Text aus dem Woerterbuch eines Moduls, mit Platzhaltern `{name}`."""
    vorlage = texte.get(sprache, texte[VORGABE])[schluessel]
    return vorlage.format(**werte) if werte else vorlage
