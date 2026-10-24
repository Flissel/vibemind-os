"""Wissens-Spiegel: reine Abgleichslogik zwischen PC-Kopie und VM von `knowledge/`.

Keine Ein-/Ausgabe, nur Planung. Die Bestaende (Pfad -> sha256/mtime) liefert der Aufrufer, das
Ausfuehren der Aktionen (Kopieren, Loeschen, Sichern nach `_konflikte/<JJJJ-MM-TT>/<pfad>`) ebenfalls.

Regeln (Besitzer-Tabelle im Plan 2026-10-24-rowboat-zentrale-teil-a-mini-pc):
- Jeder Pfad hat genau einen Besitzer (`pc` oder `vm`); der Besitzer gewinnt bei Abweichung.
- Loeschen wirkt nur vom Besitzer aus; fehlt die Datei beim Nicht-Besitzer, wird sie zurueckgespielt.
- Eine neue Datei beim Nicht-Besitzer wandert zum Besitzer.
- Hat der Nicht-Besitzer eine bestehende Datei seit dem letzten Lauf geaendert, wird seine Fassung
  vor dem Ueberschreiben/Loeschen gesichert (`konflikt_sichern` steht vor der Aktion).
- Verglichen wird nur ueber sha256; mtime dient der Anzeige. Pfade werden NFC-normalisiert.
- `loeschgrenze_pruefen` bricht bei Massenloeschung ab (leerer/umbenannter Ordner).
"""
from __future__ import annotations

import unicodedata
from typing import Literal, NamedTuple

Seite = Literal["pc", "vm"]
ArtT = Literal["nach_vm", "nach_pc", "loeschen_vm", "loeschen_pc", "konflikt_sichern"]


class Eintrag(NamedTuple):
    sha256: str
    mtime: float


class Aktion(NamedTuple):
    art: ArtT
    pfad: str


Bestand = dict[str, Eintrag]

PC_ORDNER = frozenset({
    "Projects", "Agents", "Areas", "Topics", "Bubbles", "Domains", "Hubs",
    "Coding-Projects", "Workflows", "System", "Videos", "vibemind-discourse",
})
VM_FIRMENORDNER = frozenset({"Kontakte", "Newsletter", "Wochenbild"})


class ZuVieleLoeschungen(Exception):
    """Ein Lauf wuerde mehr Dateien loeschen als erlaubt."""


def _nfc(pfad: str) -> str:
    return unicodedata.normalize("NFC", pfad)


def besitzer(pfad: str) -> Seite:
    """Besitzer eines Pfads (relativ zu `knowledge/`, POSIX)."""
    teile = _nfc(pfad).split("/")
    if teile[0] == "companys":
        if len(teile) >= 4 and teile[2] in VM_FIRMENORDNER:
            return "vm"
        return "pc"
    if len(teile) > 1 and teile[0] in PC_ORDNER:
        return "pc"
    return "vm"


def _normalisiert(bestand: Bestand) -> Bestand:
    return {_nfc(p): eintrag for p, eintrag in bestand.items()}


def abgleich_planen(pc: Bestand, vm: Bestand, zuletzt: Bestand) -> list[Aktion]:
    """Plant den Abgleich; `zuletzt` ist der gemeinsame Stand nach dem letzten erfolgreichen Lauf."""
    pc, vm, zuletzt = _normalisiert(pc), _normalisiert(vm), _normalisiert(zuletzt)
    aktionen: list[Aktion] = []
    for pfad in sorted(set(pc) | set(vm)):
        eigner = besitzer(pfad)
        seiten = {"pc": pc.get(pfad), "vm": vm.get(pfad)}
        nicht = "vm" if eigner == "pc" else "pc"
        hat, gegen = seiten[eigner], seiten[nicht]
        alt = zuletzt.get(pfad)
        ziel_kopie: ArtT = "nach_vm" if eigner == "pc" else "nach_pc"
        ziel_loeschen: ArtT = "loeschen_vm" if eigner == "pc" else "loeschen_pc"
        if hat is not None:
            if gegen is None:
                aktionen.append(Aktion(ziel_kopie, pfad))
            elif gegen.sha256 != hat.sha256:
                if alt is None or gegen.sha256 != alt.sha256:
                    aktionen.append(Aktion("konflikt_sichern", pfad))
                aktionen.append(Aktion(ziel_kopie, pfad))
        elif gegen is not None:
            if alt is None:  # neu beim Nicht-Besitzer: zum Besitzer holen
                aktionen.append(Aktion("nach_pc" if nicht == "vm" else "nach_vm", pfad))
            else:  # Besitzer hat geloescht
                if gegen.sha256 != alt.sha256:
                    aktionen.append(Aktion("konflikt_sichern", pfad))
                aktionen.append(Aktion(ziel_loeschen, pfad))
    return aktionen


def loeschgrenze_pruefen(
    aktionen: list[Aktion], gesamt: int, max_anzahl: int = 20, max_anteil: float = 0.10
) -> None:
    """Wirft `ZuVieleLoeschungen`, wenn Loeschungen `max_anzahl` oder `max_anteil` von `gesamt` uebersteigen."""
    n = sum(1 for a in aktionen if a.art.startswith("loeschen_"))
    if n == 0:
        return
    if n > max_anzahl or gesamt <= 0 or n / gesamt > max_anteil:
        raise ZuVieleLoeschungen(f"{n} Loeschungen bei {gesamt} Dateien (Grenze {max_anzahl} / {max_anteil:.0%})")
