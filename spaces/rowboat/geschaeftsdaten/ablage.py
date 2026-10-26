"""Ablage der erzeugten Notizen: Bestand eigener Dateien, Plan und atomares Ausfuehren mit Obergrenze.

- Eigene Dateien sind nur `.md` unter `Kontakte/**`, `Newsletter/*`, `Wochenbild/*` mit
  `erzeugt_von: rowboat-geschaeftsdaten` im Frontmatter (hoechstens 4 KB gelesen). Alles andere wird nie
  ueberschrieben, verschoben oder entfernt; vor jedem Ueberschreiben/Entfernen wird der Kopf erneut
  geprueft. Symlinks werden nie verfolgt.
- Aenderungserkennung per sha256 gegen die Platte: ein zweiter Lauf ohne Datenaenderung schreibt nichts.
- Hart geloescht werden nur eigene Kopien gesperrter Kontakte (Betreiber-Entscheid 1) und eigene Doppel.
  Verschwundene Kontakte gehen nach `Kontakte/_archiv/`.
- Jede Verschiebung, Archivierung und Entfernung ist fuer den Wissens-Spiegel eine Loeschung. Ein Lauf
  macht davon hoechstens `paket` (Vorgabe 15, ueber alle Firmen per gemeinsamem `Takt`); der Rest bleibt
  in stabiler Reihenfolge fuer die naechste Nacht liegen. So greift die Loeschgrenze des Spiegels (20 je
  Abgleich) auch dann nicht, wenn der Spiegel zwischen zwei Laeufen nicht laeuft.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from namen import relpfad

ERZEUGT_VON = "rowboat-geschaeftsdaten"
ARCHIV_ORDNER = "Kontakte/_archiv"
KOPF_MAX = 4096
TMP_ENDUNG = ".gd.tmp"
_ZAEHLER = ("geschrieben", "verschoben", "archiviert", "entfernt", "unveraendert", "abweichung",
            "zurueckgestellt")


@dataclass(frozen=True)
class Soll:
    rel: str
    schluessel: str
    inhalt: str


@dataclass(frozen=True)
class Ist:
    rel: str
    schluessel: str
    sha: str


@dataclass
class Plan:
    schreiben: list[Soll]
    verschieben: list[tuple[Ist, Soll]]
    archivieren: list[tuple[Ist, str]]  # (Quelle, Ziel-rel)
    entfernen: list[Ist]
    unveraendert: int
    archiv_inhalte: dict[str, str] = field(default_factory=dict)  # Ziel-rel -> Inhalt


class Takt:
    """Obergrenze fuer Loeschungen (Entfernen, Archivieren, Verschieben) je Lauf, ueber alle Firmen.
    Der Wissens-Spiegel bricht bei mehr als 20 Loeschungen je Abgleich ab; laeuft er zwischen zwei
    Laeufen nicht, addiert sich trotzdem nichts ueber `paket` hinaus."""

    def __init__(self, paket: int = 15):
        if paket < 1:
            raise ValueError("paket muss >= 1 sein")
        self.paket = paket
        self.genutzt = 0

    def rest(self) -> int:
        return self.paket - self.genutzt

    def nimm(self) -> bool:
        if self.genutzt >= self.paket:
            return False
        self.genutzt += 1
        return True


# --- Bestand ----------------------------------------------------------------------------------------

def _sha(daten: bytes) -> str:
    return hashlib.sha256(daten).hexdigest()


def _kopf(daten: bytes) -> dict[str, str] | None:
    """Frontmatter-Schluessel der obersten Ebene, nur bis zur zweiten `---`-Zeile, hoechstens 4 KB."""
    zeilen = daten[:KOPF_MAX].decode("utf-8", errors="replace").splitlines()
    if not zeilen or zeilen[0].strip() != "---":
        return None
    werte: dict[str, str] = {}
    for zeile in zeilen[1:]:
        if zeile.strip() == "---":
            return werte
        if ":" in zeile and not zeile.startswith((" ", "\t")):
            name, _, wert = zeile.partition(":")
            werte.setdefault(name.strip(), wert.strip())
    return None


def _eigen(daten: bytes) -> str | None:
    """Schluessel, wenn die Datei vom Lauf erzeugt ist, sonst None."""
    kopf = _kopf(daten)
    if not kopf or kopf.get("erzeugt_von") != ERZEUGT_VON or not kopf.get("schluessel"):
        return None
    return kopf["schluessel"]


def _lesen(pfad: Path) -> bytes | None:
    try:
        if pfad.is_symlink() or not pfad.is_file():
            return None
        return pfad.read_bytes()
    except OSError:
        return None


def _dateien(ordner: Path, rekursiv: bool) -> list[Path]:
    if ordner.is_symlink() or not ordner.is_dir():
        return []
    if not rekursiv:
        return sorted(p for p in ordner.iterdir() if p.suffix == ".md" and not p.is_symlink() and p.is_file())
    gefunden: list[Path] = []
    for wurzel, ordner_namen, namen in os.walk(ordner, followlinks=False):
        basis = Path(wurzel)
        ordner_namen[:] = sorted(n for n in ordner_namen if not (basis / n).is_symlink())
        gefunden += [basis / n for n in sorted(namen) if n.endswith(".md")]
    return [p for p in gefunden if not p.is_symlink() and p.is_file()]


def bestand(firma: Path) -> list[Ist]:
    """Eigene Dateien unter `Kontakte/**`, `Newsletter/*`, `Wochenbild/*`; `rel` relativ zu `firma`."""
    if firma.is_symlink() or not firma.is_dir():
        return []
    ist: list[Ist] = []
    for teil, rekursiv in (("Kontakte", True), ("Newsletter", False), ("Wochenbild", False)):
        for pfad in _dateien(firma / teil, rekursiv):
            daten = _lesen(pfad)
            schluessel = _eigen(daten) if daten is not None else None
            if schluessel:
                ist.append(Ist(pfad.relative_to(firma).as_posix(), schluessel, _sha(daten)))
    return sorted(ist, key=lambda i: i.rel)


# --- Plan -------------------------------------------------------------------------------------------

def _ist_kontakt(rel: str) -> bool:
    return rel.startswith("Kontakte/")


def _im_archiv(rel: str) -> bool:
    return rel.startswith(ARCHIV_ORDNER + "/")


def planen(soll: list[Soll], ist: list[Ist], gesperrt: set[str], archiv_inhalt: Callable[[Ist], str]) -> Plan:
    """Vergleicht Soll und Ist je Schluessel. Gesperrte Schluessel: alle eigenen Kopien entfernen.
    Kontakt ohne Soll -> `Kontakte/_archiv/<stamm>.md` (eindeutig, sonst ` (<id8>)`); liegt er schon im
    Archiv, bleibt er. Newsletter/Wochenbild ohne Soll bleiben. Eigene Doppel werden entfernt."""
    belegt: set[str] = set()
    for s in soll:
        if s.rel.casefold() in belegt:
            raise ValueError("doppelter Soll-Pfad")
        belegt.add(s.rel.casefold())
    belegt |= {i.rel.casefold() for i in ist}

    je_schluessel: dict[str, list[Ist]] = {}
    for i in sorted(ist, key=lambda i: i.rel):
        je_schluessel.setdefault(i.schluessel, []).append(i)

    plan = Plan([], [], [], [], 0)
    soll_schluessel = {s.schluessel for s in soll}
    for s in soll:
        if s.schluessel in gesperrt:
            raise ValueError("Soll fuer gesperrten Schluessel")
        kandidaten = je_schluessel.get(s.schluessel, [])
        gleich = [i for i in kandidaten if i.rel == s.rel]
        if gleich:
            haupt = gleich[0]
            if haupt.sha == _sha(s.inhalt.encode("utf-8")):
                plan.unveraendert += 1
            else:
                plan.schreiben.append(s)
        elif kandidaten:
            haupt = kandidaten[0]
            plan.verschieben.append((haupt, s))
        else:
            plan.schreiben.append(s)
            continue
        plan.entfernen += [i for i in kandidaten if i is not haupt]

    for schluessel, kandidaten in je_schluessel.items():
        if schluessel in soll_schluessel:
            continue
        if schluessel in gesperrt:
            plan.entfernen += kandidaten
            continue
        if not _ist_kontakt(kandidaten[0].rel):
            plan.unveraendert += len(kandidaten)  # Newsletter/Wochenbild bleiben stehen
            continue
        im_archiv = [i for i in kandidaten if _im_archiv(i.rel)]
        if im_archiv:
            plan.unveraendert += 1
            plan.entfernen += [i for i in kandidaten if i is not im_archiv[0]]
            continue
        haupt = kandidaten[0]
        ziel = _archivziel(haupt, belegt)
        belegt.add(ziel.casefold())
        plan.archivieren.append((haupt, ziel))
        plan.archiv_inhalte[ziel] = archiv_inhalt(haupt)
        plan.entfernen += kandidaten[1:]
    # Stabile Reihenfolge: was wegen der Obergrenze je Lauf liegen bleibt, kommt in der naechsten Nacht dran.
    plan.schreiben.sort(key=lambda s: s.rel)
    plan.verschieben.sort(key=lambda v: v[0].rel)
    plan.archivieren.sort(key=lambda a: a[0].rel)
    plan.entfernen.sort(key=lambda i: i.rel)
    return plan


def _archivziel(ist: Ist, belegt: set[str]) -> str:
    stamm = PurePosixPath(ist.rel).stem
    for kandidat in (stamm, f"{stamm} ({ist.schluessel[:8]})", f"{stamm} ({ist.schluessel})"):
        ziel = relpfad(ARCHIV_ORDNER, kandidat)
        if ziel.casefold() not in belegt:
            return ziel
    raise ValueError("kein freier Archivname")


# --- Ausfuehren -------------------------------------------------------------------------------------

def _pfad(firma: Path, rel: str) -> Path | None:
    """Pfad unter `firma` ohne `..` und ohne Symlink auf dem Weg, sonst None."""
    teile = PurePosixPath(rel).parts
    if not teile or PurePosixPath(rel).is_absolute() or any(t in ("", ".", "..") for t in teile):
        return None
    pfad = firma
    for teil in teile:
        pfad = pfad / teil
        if pfad.is_symlink():
            return None
    return pfad


def _loeschen(pfad: Path) -> None:
    pfad.unlink()


def _tmp_name(ziel: Path, art: str = "") -> Path:
    return ziel.with_name(f".{ziel.name}{art}{TMP_ENDUNG}")


def _schreiben_atomar(ziel: Path, inhalt: str) -> None:
    ziel.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_name(ziel)
    try:
        with open(tmp, "wb") as f:
            f.write(inhalt.encode("utf-8"))
        os.chmod(tmp, 0o644)
        os.replace(tmp, ziel)
    finally:
        if tmp.exists():
            tmp.unlink()


def aufraeumen(firma: Path) -> int:
    """Entfernt liegengebliebene eigene Zwischendateien (`.<name>*.gd.tmp`) unter den Job-Ordnern."""
    n = 0
    for teil in ("Kontakte", "Newsletter", "Wochenbild"):
        ordner = firma / teil
        if ordner.is_symlink() or not ordner.is_dir():
            continue
        for wurzel, ordner_namen, namen in os.walk(ordner, followlinks=False):
            basis = Path(wurzel)
            ordner_namen[:] = [o for o in ordner_namen if not (basis / o).is_symlink()]
            for name in namen:
                pfad = basis / name
                if name.startswith(".") and name.endswith(TMP_ENDUNG) and not pfad.is_symlink() and pfad.is_file():
                    pfad.unlink()
                    n += 1
    return n


def _ziel_stand(ziel: Path | None, schluessel: str) -> str:
    """'frei' (fehlt oder eigene Datei desselben Schluessels), 'fremd' (fremde Datei, Symlink, ungueltig)
    oder 'belegt' (eigene Datei eines anderen Schluessels, deren Umzug noch aussteht)."""
    if ziel is None or ziel.is_symlink():
        return "fremd"
    if not ziel.exists():
        return "frei"  # Symlinks auf dem Weg hat `_pfad` schon ausgeschlossen
    daten = _lesen(ziel)
    anderer = _eigen(daten) if daten is not None else None
    if anderer is None:
        return "fremd"
    return "frei" if anderer == schluessel else "belegt"


def _quelle_entfernen(quelle: Path | None, ist: Ist, *, sha_pruefen: bool) -> bool:
    """Entfernt die Quelle nur, wenn sie noch eine eigene Datei ist (und, falls verlangt, ihr sha passt)."""
    if quelle is None:
        return False
    daten = _lesen(quelle)
    if daten is None or _eigen(daten) is None:
        return False
    if sha_pruefen and _sha(daten) != ist.sha:
        return False
    _loeschen(quelle)
    return True


def _laufende_umzuege(umzuege: list[tuple[Ist, Soll]], rest: int, wartend: set[str]) -> list[tuple[Ist, Soll]]:
    """Die ersten `rest` Umzuege (stabile Reihenfolge) ohne solche, deren Ziel die Quelle eines Umzugs oder
    einer Archivierung ist, die in diesem Lauf liegen bleibt (bis zum Fixpunkt)."""
    laufend = list(umzuege[:max(rest, 0)])
    while True:
        drin = {id(i) for i, _ in laufend}
        bleibt = wartend | {i.rel.casefold() for i, _ in umzuege if id(i) not in drin}
        neu = [(i, s) for i, s in laufend if s.rel.casefold() not in bleibt]
        if len(neu) == len(laufend):
            return laufend
        laufend = neu


def ausfuehren(plan: Plan, firma: Path, *, paket: int = 15, trocken: bool = False,
               takt: Takt | None = None) -> dict[str, int]:
    """Fuehrt den Plan aus. Reihenfolge: entfernen (Datenschutz zuerst), archivieren, verschieben,
    schreiben. Entfernen, Archivieren und Verschieben verbrauchen je eine Loeschung aus `takt` (geteilt
    ueber Firmen, Vorgabe `paket` je Lauf); was nicht mehr passt, wird `zurueckgestellt` und kommt in
    einer spaeteren Nacht. Tauschen Kontakte ihre Pfade, wird die belegte Quelle unter einem Zwischennamen
    geparkt statt ueberschrieben. `trocken` zaehlt nur."""
    z = dict.fromkeys(_ZAEHLER, 0)
    z["unveraendert"] = plan.unveraendert
    takt = takt or Takt(paket)

    for ist in plan.entfernen:
        if not takt.nimm():
            z["zurueckgestellt"] += 1
            continue
        if trocken:
            z["entfernt"] += 1
        # Gesperrte Kopien: entfernen, solange es eine eigene Datei ist (Kopf), auch wenn sie sich geaendert hat.
        elif _quelle_entfernen(_pfad(firma, ist.rel), ist, sha_pruefen=False):
            z["entfernt"] += 1
        else:
            z["abweichung"] += 1

    wartend: set[str] = set()  # Quellen (casefold), die nach diesem Lauf noch liegen bleiben
    for ist, ziel_rel in plan.archivieren:
        if not takt.nimm():
            z["zurueckgestellt"] += 1
            wartend.add(ist.rel.casefold())
            continue
        if trocken:
            z["archiviert"] += 1
            continue
        ziel = _pfad(firma, ziel_rel)
        if ziel is None or ziel.is_symlink() or ziel.exists():
            z["abweichung"] += 1
            wartend.add(ist.rel.casefold())
            continue
        _schreiben_atomar(ziel, plan.archiv_inhalte[ziel_rel])
        if _quelle_entfernen(_pfad(firma, ist.rel), ist, sha_pruefen=True):
            z["archiviert"] += 1
        else:
            z["abweichung"] += 1
            wartend.add(ist.rel.casefold())

    laufend = _laufende_umzuege(plan.verschieben, takt.rest(), wartend)
    drin = {id(i) for i, _ in laufend}
    for ist, _ in plan.verschieben:
        if id(ist) not in drin:
            z["zurueckgestellt"] += 1
            wartend.add(ist.rel.casefold())
    offen = {i.rel.casefold(): i for i, _ in laufend}  # Quelle -> Ist, solange der Umzug offen ist
    geparkt: dict[int, Path] = {}  # id(Ist) -> Zwischenname
    for ist, soll in laufend:
        takt.nimm()
        if trocken:
            z["verschoben"] += 1
            continue
        ziel = _pfad(firma, soll.rel)
        stand = _ziel_stand(ziel, soll.schluessel)
        besetzer = offen.get(soll.rel.casefold())
        if stand == "belegt" and besetzer is not None and besetzer is not ist:
            # Tausch: den Besetzer (offener Umzug, noch unveraendert) unter einem Zwischennamen parken.
            daten = _lesen(ziel)
            if daten is not None and _sha(daten) == besetzer.sha:
                park = _tmp_name(ziel, ".tausch")
                os.replace(ziel, park)
                geparkt[id(besetzer)] = park
                stand = "frei"
        if stand != "frei":
            z["abweichung"] += 1
            wartend.add(ist.rel.casefold())
            continue
        _schreiben_atomar(ziel, soll.inhalt)
        quelle = geparkt.pop(id(ist), None) or _pfad(firma, ist.rel)
        if _quelle_entfernen(quelle, ist, sha_pruefen=True):
            z["verschoben"] += 1
        else:
            z["abweichung"] += 1
        offen.pop(ist.rel.casefold(), None)
    for park in geparkt.values():  # nur nach einer Abweichung uebrig: eigene Zwischendatei, Inhalt kommt neu
        if park.exists():
            park.unlink()

    for soll in plan.schreiben:
        if soll.rel.casefold() in wartend:
            z["zurueckgestellt"] += 1
            continue
        if trocken:
            z["geschrieben"] += 1
            continue
        ziel = _pfad(firma, soll.rel)
        stand = _ziel_stand(ziel, soll.schluessel)
        if stand == "belegt":
            z["zurueckgestellt"] += 1
            continue
        if stand == "fremd":
            z["abweichung"] += 1
            continue
        _schreiben_atomar(ziel, soll.inhalt)
        z["geschrieben"] += 1
    return z
