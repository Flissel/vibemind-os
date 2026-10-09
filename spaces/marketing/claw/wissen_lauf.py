"""Rowboat-Lauf: Kandidaten, Ersetzungen, Sicherung (Spec sales-claw 2026-10-09-marke-exakt-logo-wissen §3).

Wurzel ist die ganze Wissensbasis (~/.rowboat/knowledge). Kandidaten sind die Texte im eigenen Firmenordner
(ohne Marke-Verlauf/, Wissen-Verlauf/, Marke.md, Markenhandbuch.md) und ausserhalb nur .md-Dateien, die den
Firmennamen als ganzes Wort nennen. Gesperrt: People/, Bewerbung/, Diary/, Voice Memos/, die Ordner anderer
Firmen, jede Datei, die eine andere aktive Firma nennt, und Verknuepfungen (Link-Sperre wie markenwissen).
Geschrieben wird nur nach einer Sicherung unter companys/<Firma>/Wissen-Verlauf/<Stempel>/<relativer Pfad>,
nur wenn die Datei seit dem Lesen unveraendert ist, und immer atomar. Kein Modell, kein Netz."""
from __future__ import annotations

import datetime
import os
import re
import tempfile
from dataclasses import dataclass, field

from spaces.marketing.claw import markenprofil
from spaces.marketing.claw import markenwissen as mw

MAX_DOKUMENTE = 40
MAX_BYTES = 20 * 1024
GESAMT_MAX = 300_000          # Zeichen aller Kandidaten im Prompt
FIRMA_PUNKTE = 100
TOLERANZ_S = 120
SPERRORDNER = ("people", "bewerbung", "diary", "voice memos")
VERLAUF = "Wissen-Verlauf"
HANDBUCH = "Markenhandbuch.md"
NEU_MAX = 20 * 1024            # je Ersetzung (Bytes UTF-8)
HANDBUCH_MAX = 40 * 1024       # Markenhandbuch (Bytes UTF-8)
_FIRMA_ORDNER_AUS = (markenprofil.VERLAUF.casefold(), VERLAUF.casefold())
_FIRMA_DATEIEN_AUS = (markenprofil.DATEI.casefold(), HANDBUCH.casefold())
_FIRMA_ENDUNGEN = (".md", ".txt")
_ALT_MAX = 200_000


class LaufFehler(Exception):
    """Sicherung oder Schreiben nicht moeglich (Meldung fuer den Betreiber)."""


@dataclass
class Kandidat:
    rel: str
    pfad: str
    text: str
    crlf: bool = False
    bom: bool = False
    punkte: int = 0


@dataclass
class Ergebnis:
    geschrieben: list[str] = field(default_factory=list)
    verworfen: list[str] = field(default_factory=list)
    abbruch: str | None = None
    handbuch_rel: str | None = None


def wissen_wurzel() -> str:
    return os.environ.get("ROWBOAT_KNOWLEDGE_ORDNER") or os.path.dirname(os.path.normpath(mw.wurzel()))


def _muster(namen: list[str]) -> re.Pattern | None:
    namen = sorted({n.strip() for n in namen if isinstance(n, str) and n.strip()}, key=len, reverse=True)
    if not namen:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in namen) + r")(?!\w)", re.IGNORECASE)


def nennungen(text: str, namen: list[str]) -> int:
    muster = _muster(namen)
    return len(muster.findall(text)) if muster else 0


def lesen(pfad: str) -> tuple[str, bool, bool] | None:
    try:
        with open(pfad, "rb") as f:
            roh = f.read(MAX_BYTES + 1)
    except OSError:
        return None
    if len(roh) > MAX_BYTES:
        return None
    bom = roh.startswith(b"\xef\xbb\xbf")
    try:
        text = roh[3 if bom else 0:].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text.replace("\r\n", "\n"), "\r\n" in text, bom


def _rel(pfad: str, wurzel: str) -> str:
    return os.path.relpath(pfad, wurzel).replace(os.sep, "/")


def _norm(pfad: str) -> str:
    return os.path.normcase(os.path.normpath(pfad))


def kandidaten(wurzel: str, firma_ordner: str, namen: list[str], fremde: list[str],
               hinweise: list[str]) -> list[Kandidat]:
    wurzel = os.path.normpath(wurzel)
    wurzel_real = os.path.realpath(wurzel)
    wurzel_n, firma = _norm(wurzel), _norm(firma_ordner)
    firmen_wurzel = os.path.dirname(firma)
    gefunden: list[Kandidat] = []
    for ort, unterordner, dateien in os.walk(wurzel, followlinks=False):
        ort_n = _norm(ort)
        unterordner.sort()
        echte = []
        for u in unterordner:
            pfad = os.path.join(ort, u)
            if u.casefold() in SPERRORDNER:
                continue                                   # People, Diary ... in jeder Tiefe
            if ort_n == firmen_wurzel and _norm(pfad) != firma:
                continue                                   # Ordner anderer Firmen sind tabu
            if ort_n == firma and u.casefold() in _FIRMA_ORDNER_AUS:
                continue
            if not mw._echt(pfad, ort, wurzel_real):
                hinweise.append(f"{_rel(pfad, wurzel)} übersprungen (Verknüpfung)")
                continue
            echte.append(u)
        unterordner[:] = echte                            # Junctions sonst durchlaufen
        in_firma = ort_n == firma or ort_n.startswith(firma + os.sep)
        for name in sorted(dateien):
            klein = name.casefold()
            if in_firma:
                if not klein.endswith(_FIRMA_ENDUNGEN) or (ort_n == firma and klein in _FIRMA_DATEIEN_AUS):
                    continue
            elif not klein.endswith(".md"):
                continue
            pfad = os.path.join(ort, name)
            rel = _rel(pfad, wurzel)
            if not mw._echt(pfad, ort, wurzel_real):
                hinweise.append(f"{rel} übersprungen (Verknüpfung)")
                continue
            gelesen = lesen(pfad)
            if gelesen is None:
                if in_firma:
                    hinweise.append(f"{rel} übersprungen (größer als 20 KB oder kein UTF-8)")
                continue
            text, crlf, bom = gelesen
            treffer = nennungen(text, namen)
            if not in_firma and treffer == 0:
                continue
            if nennungen(text, fremde):
                hinweise.append(f"{rel} übersprungen (nennt eine andere Firma)")
                continue
            punkte = treffer + (FIRMA_PUNKTE if in_firma else 0) - rel.count("/")
            gefunden.append(Kandidat(rel, pfad, text, crlf, bom, punkte))
    gefunden.sort(key=lambda k: (-k.punkte, k.rel))
    genommen, summe = [], 0
    for k in gefunden:
        if len(genommen) < MAX_DOKUMENTE and summe + len(k.text) <= GESAMT_MAX:
            genommen.append(k)
            summe += len(k.text)
    if len(genommen) < len(gefunden):
        hinweise.append(f"Wissen-Lauf: {len(genommen)} von {len(gefunden)} Dokumenten (Obergrenze {MAX_DOKUMENTE})")
    return genommen


def alt_profil(firma_ordner: str, seit: float | None) -> str:
    """Profil vor der (ersten) Uebernahme dieses Laufs: die aelteste Marke-Verlauf-Sicherung, die ab `seit`
    (minus TOLERANZ_S) entstand; ohne `seit` oder ohne solche die neueste. '' ohne Verlauf."""
    ordner = os.path.join(firma_ordner, markenprofil.VERLAUF)
    firma_real = os.path.realpath(firma_ordner)
    try:
        if not os.path.isdir(ordner) or not mw._echt(ordner, firma_ordner, firma_real):
            return ""
        dateien = [(os.stat(p).st_mtime, p) for p in (os.path.join(ordner, n) for n in os.listdir(ordner))
                   if p.lower().endswith(".md") and os.path.isfile(p) and mw._echt(p, ordner, firma_real)]
    except OSError:
        return ""
    if not dateien:
        return ""
    passend = sorted(d for d in dateien if seit is not None and d[0] >= seit - TOLERANZ_S)
    _, pfad = passend[0] if passend else max(dateien)
    try:
        with open(pfad, "rb") as f:
            return f.read(_ALT_MAX).decode("utf-8-sig", errors="replace")
    except OSError:
        return ""


def ersetzen(text: str, alt: str, neu: str) -> str | None:
    if not alt:
        return None
    treffer = [i for i in range(len(text)) if text.startswith(alt, i)]   # auch ueberlappende zaehlen
    if len(treffer) != 1:
        return None
    i = treffer[0]
    return text[:i] + neu + text[i + len(alt):]


def _atomar(pfad: str, roh: bytes) -> None:
    fd, temp = tempfile.mkstemp(dir=os.path.dirname(pfad), prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(roh)
        os.replace(temp, pfad)
    except OSError:
        try:
            os.remove(temp)
        except OSError:
            pass
        raise


def schreiben(pfad: str, text: str, crlf: bool, bom: bool) -> None:
    inhalt = text.replace("\n", "\r\n") if crlf else text
    _atomar(pfad, (b"\xef\xbb\xbf" if bom else b"") + inhalt.encode("utf-8"))


def _ordner_anlegen(eltern: str, name: str, firma_real: str) -> str:
    if name in ("", ".", ".."):
        raise LaufFehler("ungültiger Pfad")
    pfad = os.path.join(eltern, name)
    try:
        os.mkdir(pfad)
    except FileExistsError:
        pass
    if not os.path.isdir(pfad) or not mw._echt(pfad, eltern, firma_real):
        raise LaufFehler(f"{name} ist kein gewöhnlicher Ordner")
    return pfad


class _Sicherung:
    """Sicherungsordner companys/<Firma>/Wissen-Verlauf/<YYYY-MM-DD-HHMM>[-n]/, erst beim ersten Bedarf."""

    def __init__(self, firma_ordner: str, jetzt: datetime.datetime):
        self.firma, self.jetzt, self.basis = firma_ordner, jetzt, None
        self.real = os.path.realpath(firma_ordner)

    def _stempel(self) -> str:
        if self.basis is None:
            verlauf = _ordner_anlegen(self.firma, VERLAUF, self.real)
            for nummer in range(1, 100):
                name = f"{self.jetzt:%Y-%m-%d-%H%M}" + ("" if nummer == 1 else f"-{nummer}")
                pfad = os.path.join(verlauf, name)
                try:
                    os.mkdir(pfad)
                except FileExistsError:
                    continue
                if not mw._echt(pfad, verlauf, self.real):
                    raise LaufFehler("Sicherungsordner ist eine Verknüpfung")
                self.basis = pfad
                break
            else:
                raise LaufFehler("Sicherungsordner nicht anlegbar")
        return self.basis

    def sichern(self, rel: str, quelle: str) -> None:
        ort = self._stempel()
        teile = rel.split("/")
        for teil in teile[:-1]:
            ort = _ordner_anlegen(ort, teil, self.real)
        ziel = os.path.join(ort, teile[-1])
        if not mw._echt(ziel, ort, self.real):
            raise LaufFehler("Sicherung wäre eine Verknüpfung")
        with open(quelle, "rb") as f:
            roh = f.read()
        with open(ziel, "xb") as f:
            f.write(roh)


def _kodierbar(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _form_fehler(e: object) -> str | None:
    if not isinstance(e, dict):
        return "Ersetzung ist kein Objekt"
    for schluessel in ("pfad", "alt", "neu"):
        if not isinstance(e.get(schluessel), str):
            return f"Feld {schluessel} fehlt oder ist kein Text"
    if not _kodierbar(e["neu"]) or not _kodierbar(e["alt"]):
        return "Text nicht kodierbar"
    if len(e["neu"].encode("utf-8")) > NEU_MAX:
        return "Ersetzung größer als 20 KB"
    return None


def _firma_pruefen(wurzel: str, firma_ordner: str) -> None:
    """Firmenordner und alle Eltern bis zur Wissenswurzel duerfen keine Verknuepfung sein."""
    wurzel_n = _norm(wurzel)
    pfad = os.path.normpath(firma_ordner)
    while _norm(pfad) != wurzel_n:
        if not mw._selbst_echt(pfad):
            raise LaufFehler(f"Firmenordner ist eine Verknüpfung: {_rel(pfad, wurzel)}")
        eltern = os.path.dirname(pfad)
        if eltern == pfad:
            raise LaufFehler("Firmenordner liegt nicht unter der Wissenswurzel")
        pfad = eltern


def anwenden(wurzel: str, firma_ordner: str, kandidaten: dict[str, Kandidat], ersetzungen: list[dict],
             handbuch: str, jetzt: datetime.datetime, schreiben_=None) -> Ergebnis:
    """Ersetzungen pruefen, sichern, schreiben; zum Schluss das Markenhandbuch. Ein Fehler beim Sichern oder
    Schreiben bricht ab: Geschriebenes bleibt stehen (gesichert), Ergebnis.abbruch nennt Datei und Grund."""
    schreiben_ = schreiben_ or schreiben
    erg = Ergebnis()
    try:
        _firma_pruefen(wurzel, firma_ordner)
    except LaufFehler as e:
        erg.abbruch = str(e)[:200]
        return erg
    sicherung = _Sicherung(firma_ordner, jetzt)
    gruppen: dict[str, list[dict]] = {}
    for e in ersetzungen if isinstance(ersetzungen, list) else []:
        grund = _form_fehler(e)
        if grund:
            pfad = e.get("pfad") if isinstance(e, dict) and isinstance(e.get("pfad"), str) else "?"
            erg.verworfen.append(f"{pfad[:120]}: {grund} – verworfen")
            continue
        gruppen.setdefault(e["pfad"], []).append(e)
    for rel, liste in gruppen.items():
        try:
            k = kandidaten.get(rel)
            if k is None:
                erg.verworfen.append(f"{rel[:120]}: nicht in der Kandidatenliste – verworfen")
                continue
            text = k.text
            for e in liste:
                neu = ersetzen(text, e["alt"], e["neu"])
                if neu is None:
                    erg.verworfen.append(f"{rel}: Ausschnitt nicht genau einmal gefunden – verworfen")
                    continue
                text = neu
            if text == k.text:
                continue
            aktuell = lesen(k.pfad)
            if aktuell is None or aktuell[0] != k.text:
                erg.verworfen.append(f"{rel}: inzwischen geändert – nicht geschrieben")
                continue
            sicherung.sichern(rel, k.pfad)
            schreiben_(k.pfad, text, k.crlf, k.bom)
        except (OSError, LaufFehler) as e:
            erg.abbruch = f"{rel} ({type(e).__name__}: {e})"[:200]
            return erg
        except Exception as e:                                # eine kaputte Datei kippt den Lauf nicht
            erg.verworfen.append(f"{rel[:120]}: {type(e).__name__} – nicht geschrieben")
            continue
        erg.geschrieben.append(rel)
    if not isinstance(handbuch, str):
        erg.verworfen.append(f"{HANDBUCH}: kein Text – nicht geschrieben")
        return erg
    if not _kodierbar(handbuch) or len(handbuch.encode("utf-8")) > HANDBUCH_MAX:
        erg.verworfen.append(f"{HANDBUCH}: zu groß (über 40 KB) oder nicht kodierbar – nicht geschrieben")
        return erg
    ziel = os.path.join(firma_ordner, HANDBUCH)
    rel = _rel(ziel, wurzel)
    try:
        if os.path.lexists(ziel):
            if not (os.path.isfile(ziel) and mw._echt(ziel, firma_ordner, os.path.realpath(firma_ordner))):
                raise LaufFehler(f"{HANDBUCH} ist keine gewöhnliche Datei")
            sicherung.sichern(rel, ziel)
        schreiben_(ziel, handbuch.replace("\r\n", "\n"), False, False)
    except (OSError, LaufFehler) as e:
        erg.abbruch = f"{rel} ({type(e).__name__}: {e})"[:200]
        return erg
    erg.handbuch_rel = rel
    return erg
