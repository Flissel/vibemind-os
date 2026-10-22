"""einrichtung: Stand der Einrichtung einer neuen Marke (rein) und Pruefung der Pflichtangaben.

Keine DB, kein Netz: alles Noetige kommt als Argument. `profil` hat die Form von
`marken_spiegel.profil` (`{"werte": {...}, "abschnitte": {...}}`), `einrichtung_stand` die Form
`{"uebersprungen": [ints], "vermittler": bool | None}` (Migration 086).
"""
from __future__ import annotations

import re

SCHRITTE: tuple[tuple[int, str], ...] = (
    (1, "Name und Webseite"),
    (2, "Pflichtangaben für Newsletter"),
    (3, "Wer und was"),
    (4, "Aussehen"),
    (5, "Stimme"),
    (6, "Belege"),
    (7, "Zusammenfassung und Bestätigung"),
)

# Schritt 2 wird aus dem Pflichtteil berechnet, Schritt 7 ist nie vorab erledigt.
FELDER: dict[int, tuple[str, ...]] = {
    1: ("webseite",),
    3: ("Kurz gesagt", "Wer wir sind", "Angebote", "Zielgruppe", "Abgrenzung"),
    4: ("akzent", "zweitfarbe", "grund", "text", "schrift_anzeige", "schrift_text", "logo"),
    5: ("Ton", "Wörter", "Do & Don'ts"),   # Sprechstimme ist optional
    6: ("Fakten und Zahlen", "Kanäle", "Menschen", "Bildstil"),
}

START_NACHRICHT = "Neue Marke einrichten:"   # Anfang der ersten Runde, von der API (/marke_neu) geschrieben
ABMELDE_VORGABE ="Du bekommst diese Mail, weil du dich eingetragen hast. Abmelden: {abmeldelink}"
_PLATZHALTER = "{abmeldelink}"
_UEBERSPRINGBAR = (1, 3, 4, 5, 6)
_PFLICHT_SCHLUESSEL = ("impressum", "abmelde_hinweis", "vermittlerangaben")
_ANZEIGE = {"impressum": "Impressum", "vermittlerangaben": "Vermittlerangaben"}


def _leer(wert: object) -> bool:
    return not str(wert or "").strip()


def _normal(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _uebersprungen(einrichtung_stand: dict) -> set[int]:
    roh = (einrichtung_stand or {}).get("uebersprungen")
    if not isinstance(roh, list):
        return set()
    return {n for n in roh if isinstance(n, int) and not isinstance(n, bool) and n in _UEBERSPRINGBAR}


def _offene_felder(nr: int, profil: dict, hat_logo: bool) -> list[str]:
    werte = profil.get("werte") if isinstance(profil.get("werte"), dict) else {}
    abschnitte = profil.get("abschnitte") if isinstance(profil.get("abschnitte"), dict) else {}
    offen = []
    for feld in FELDER[nr]:
        if feld == "logo":
            gefuellt = bool(hat_logo)
        elif nr in (3, 5, 6):
            gefuellt = not _leer(abschnitte.get(feld))
        else:
            gefuellt = not _leer(werte.get(feld))
        if not gefuellt:
            offen.append(feld)
    return offen


def _pflichtteil_offen(pflichtteil: dict, einrichtung_stand: dict) -> list[str]:
    offen = []
    if _leer(pflichtteil.get("impressum")):
        offen.append("impressum")
    if _PLATZHALTER not in str(pflichtteil.get("abmelde_hinweis") or ""):
        offen.append("abmelde_hinweis")
    vermittler = (einrichtung_stand or {}).get("vermittler")
    if not isinstance(vermittler, bool):
        offen.append("vermittler")
    elif vermittler and _leer(pflichtteil.get("vermittlerangaben")):
        offen.append("vermittlerangaben")
    return offen


def stand(name: str, profil: dict | None, hat_logo: bool, pflichtteil: dict, einrichtung_stand: dict) -> dict:
    """Stand je Schritt, naechster offener Schritt und ob die Einrichtung abschliessbar ist."""
    profil = profil if isinstance(profil, dict) else {}
    pflichtteil = pflichtteil if isinstance(pflichtteil, dict) else {}
    einrichtung_stand = einrichtung_stand if isinstance(einrichtung_stand, dict) else {}
    uebersprungen = _uebersprungen(einrichtung_stand)
    schritte = []
    for nr, titel in SCHRITTE:
        if nr == 2:
            offen = _pflichtteil_offen(pflichtteil, einrichtung_stand)
        elif nr == 7:
            offen = ["bestaetigung"]
        else:
            offen = _offene_felder(nr, profil, hat_logo)
            if nr == 1 and _leer(name):
                offen.insert(0, "name")
            if nr in uebersprungen and not (nr == 1 and _leer(name)):
                offen = []
        schritte.append({"nr": nr, "titel": titel, "erledigt": not offen, "offen": offen})
    erledigt = {s["nr"]: s["erledigt"] for s in schritte}
    naechster = next((s["nr"] for s in schritte if not s["erledigt"]), 7)
    return {"schritte": schritte, "naechster": naechster, "abschliessbar": erledigt[1] and erledigt[2]}


def _wert_zeile(titel: str, wert: object) -> str:
    """Gespeicherter Text woertlich (Zeilenumbrueche bleiben) unter seinem Titel, leer = 'fehlt'."""
    return f"{titel}: fehlt" if _leer(wert) else f"{titel}:\n{str(wert).strip()}"


def stand_text(s: dict, pflichtteil: dict | None = None, einrichtung_stand: dict | None = None) -> str:
    """Block 'STAND DER EINRICHTUNG' fuer den Prompt. Mit `pflichtteil` zusaetzlich die gespeicherten
    Pflichtangaben woertlich und die Vermittler-Antwort: Schritt 7 fasst genau das zusammen, nichts Erinnertes."""
    zeilen = ["STAND DER EINRICHTUNG"]
    for x in s["schritte"]:
        status = "erledigt" if x["erledigt"] else f"offen ({', '.join(x['offen'])})"
        zeilen.append(f"Schritt {x['nr']} von {len(SCHRITTE)} – {x['titel']}: {status}")
    zeilen.append(f"Nächster Schritt: {s['naechster']}")
    if pflichtteil is None:
        return "\n".join(zeilen)
    pflichtteil = pflichtteil if isinstance(pflichtteil, dict) else {}
    vermittler = (einrichtung_stand if isinstance(einrichtung_stand, dict) else {}).get("vermittler")
    zeilen += ["", "GESPEICHERTE PFLICHTANGABEN (wörtlich, so stehen sie in der Datenbank):",
               _wert_zeile("Impressum", pflichtteil.get("impressum")),
               _wert_zeile("Abmeldehinweis", pflichtteil.get("abmelde_hinweis")),
               "Vermittler: " + ("ja" if vermittler is True else "nein" if vermittler is False else "noch offen")]
    if vermittler is True:
        zeilen.append(_wert_zeile("Vermittlerangaben", pflichtteil.get("vermittlerangaben")))
    return "\n".join(zeilen)


def pflichtteil_pruefen(roh: object, belege: list[str]) -> tuple[dict, list[str]]:
    """Uebernehmbare Pflichtfelder und Hinweise. Rechtsangaben nur, wenn sie in einem Beleg stehen."""
    if not isinstance(roh, dict):
        return {}, ["Pflichtangaben nicht übernommen: erwartet ein Objekt mit impressum, abmelde_hinweis, "
                    "vermittlerangaben"]
    ergebnis: dict = {}
    hinweise: list[str] = []
    normale_belege = [_normal(b) for b in (belege or [])]
    for schluessel, wert in roh.items():
        if schluessel not in _PFLICHT_SCHLUESSEL:
            hinweise.append(f"Unbekanntes Feld '{schluessel}' ignoriert")
            continue
        if not isinstance(wert, str) or _leer(wert):
            continue
        text = _normal(wert)
        if schluessel == "abmelde_hinweis":
            if _PLATZHALTER in wert:
                ergebnis[schluessel] = wert.strip()
            else:
                hinweise.append(f"Abmeldehinweis nicht übernommen: er muss {_PLATZHALTER} enthalten")
        elif any(text in b for b in normale_belege):
            ergebnis[schluessel] = wert.strip()
        else:
            hinweise.append(f"{_ANZEIGE[schluessel]} nicht übernommen: steht weder in deiner Nachricht "
                            "noch auf einer gelesenen Seite")
    return ergebnis, hinweise
