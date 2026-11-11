"""Satztrenner der Video-Werkbank (11.11.2026): zerlegt einen Szenentext in Saetze, die der Ton-Dienst
einzeln spricht (/satz). Reine Textarbeit, kein Modell.

Trennt nach . ! ? … (auch mit schliessenden Anfuehrungszeichen), wenn Leerraum und dann Grossbuchstabe,
Ziffer oder Anfuehrungszeichen folgt. Nicht getrennt wird nach deutschen Abkuerzungen (z. B., d. h., u. a.,
ca., Nr., Dr., bzw., ggf., inkl., usw.) und nach Ordnungszahlen ("1. Januar"). Tausenderpunkte (3.500)
haben keinen Leerraum dahinter und fallen von selbst nicht auf. Jeder Satz hat hoechstens TEXT_MAX Zeichen:
Laengeres wird am letzten Komma geteilt (sonst am letzten Leerzeichen)."""
from __future__ import annotations

import re

from spaces.marketing.claw.ton_dienst import TEXT_MAX

_ENDE = re.compile(r"[.!?…]+[\"“”„’')»«]*(?=\s)")
_ANFANG = "\"„“»«'‘(["
_ABKUERZUNG = re.compile(r"(?<![^\W\d_])(?:z\. ?B|d\. ?h|u\. ?a|ca|Nr|Dr|bzw|ggf|inkl|usw)\.$", re.IGNORECASE)
_ORDNUNGSZAHL = re.compile(r"(?<![\w,.])\d{1,2}\.$")   # 1. Januar; Jahreszahlen (2020.) enden Saetze
_Z_B = re.compile(r"(?<!\w)z\.$", re.IGNORECASE)


def _trennt(davor: str, zeichen: str) -> bool:
    """Ob nach `davor` (Text bis einschliesslich Satzzeichen) vor `zeichen` ein Satz endet."""
    if not (zeichen.isupper() or zeichen.isdigit() or zeichen in _ANFANG):
        return False
    if zeichen == "B" and _Z_B.search(davor):     # "z. B." - das Leerzeichen steht mitten in der Abkuerzung
        return False
    kern = davor.rstrip("\"“”„’')»«")
    if kern.endswith(".") and not kern.endswith(".."):
        if _ABKUERZUNG.search(kern) or _ORDNUNGSZAHL.search(kern):
            return False
    return True


def _kuerzen(satz: str) -> list[str]:
    teile = []
    while len(satz) > TEXT_MAX:
        fenster = satz[:TEXT_MAX]
        schnitt = fenster.rfind(",") + 1
        if schnitt <= 0:
            schnitt = fenster.rfind(" ")
        if schnitt <= 0:
            schnitt = TEXT_MAX
        teile.append(satz[:schnitt].strip())
        satz = satz[schnitt:].strip()
    if satz:
        teile.append(satz)
    return [t for t in teile if t]


def saetze(text: str) -> list[str]:
    """Szenentext -> Saetze (jeder 1..TEXT_MAX Zeichen). Leerer Text -> []."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    ergebnis, start = [], 0
    for treffer in _ENDE.finditer(text):
        naechstes = treffer.end() + 1          # (?=\s) -> genau ein Leerzeichen nach dem Satzende
        if naechstes < len(text) and _trennt(text[start:treffer.end()], text[naechstes]):
            ergebnis += _kuerzen(text[start:treffer.end()].strip())
            start = naechstes
    ergebnis += _kuerzen(text[start:].strip())
    return ergebnis
