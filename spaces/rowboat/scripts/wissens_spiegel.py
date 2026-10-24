"""Wissens-Spiegel: gleicht die PC-Kopie von `~/.rowboat/knowledge` und die VM nach Besitzer ab.

Teil 1 (rein, ohne Ein-/Ausgabe): `besitzer`, `abgleich_planen`, `doppelte_pfade`, `loeschgrenze_pruefen`.
Teil 2 (Ein-/Ausgabe): `bestand_lokal`/`bestand_vm` (Bestand + Rohnamen), `ausfuehren` (tar ueber ssh),
`zustand_lesen`/`zustand_schreiben`, `lauf` und die CLI
`python spaces/rowboat/scripts/wissens_spiegel.py [--trocken]` (Aufgabe alle 5 min, siehe
`wissens_spiegel_aufgabe.ps1`). Als Datei aufrufen, nicht per `-m spaces.rowboat.scripts...`:
`spaces/rowboat/__init__.py` importiert ein fehlendes `.config`; dieses Modul braucht nur stdlib. Exit: 0 ok, 1 lokaler Fehler, 2 VM nicht erreichbar/Uebertragung
gescheitert, 3 Loeschgrenze. Der Zustand (`zuletzt`) wird nur nach vollem Erfolg geschrieben.

Regeln (Besitzer-Tabelle im Plan 2026-10-24-rowboat-zentrale-teil-a-mini-pc):
- Jeder Pfad hat genau einen Besitzer (`pc` oder `vm`); der Besitzer gewinnt bei Abweichung.
- Loeschen wirkt nur vom Besitzer aus; fehlt die Datei beim Nicht-Besitzer, wird sie zurueckgespielt.
- Eine neue Datei beim Nicht-Besitzer wandert zum Besitzer.
- Hat der Nicht-Besitzer eine bestehende Datei seit dem letzten Lauf geaendert, wird seine Fassung
  vor dem Ueberschreiben/Loeschen gesichert (`konflikt_sichern` steht vor der Aktion).
- Verglichen wird nur ueber sha256; mtime dient der Anzeige. Pfade werden NFC-normalisiert.
- Pfade, die sich nur in Gross-/Kleinschreibung unterscheiden (gleiche Datei auf dem PC), oder die im
  selben Bestand als NFC- und NFD-Fassung vorkommen, werden nicht geplant; `doppelte_pfade` meldet sie.
- `_konflikte/` (oberste Ebene) wird nie gespiegelt.
- `loeschgrenze_pruefen` bricht bei Massenloeschung ab (leerer/umbenannter Ordner).
"""
from __future__ import annotations

import argparse
import inspect
import io
import json
import logging
import logging.handlers
import os
import shlex
import subprocess
import sys
import tarfile
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Callable, Literal, NamedTuple

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
KONFLIKTE = "_konflikte"
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


def _ausgenommen(pfad: str) -> bool:
    return _nfc(pfad).split("/")[0] == KONFLIKTE


def _normalisiert(bestand: Bestand) -> Bestand:
    return {_nfc(p): eintrag for p, eintrag in bestand.items() if not _ausgenommen(p)}


def _doppel_gruppen(pc: Bestand, vm: Bestand, zuletzt: Bestand) -> list[tuple[set[str], list[str]]]:
    """Gruppen (normalisierte Schluessel, rohe Schreibweisen) mit Gross-/Kleinschreibungs- oder NFC-Doppeln."""
    gruppen: dict[str, tuple[set[str], set[str]]] = {}
    kollision: set[str] = set()
    for bestand in (pc, vm, zuletzt):
        je_nfc: dict[str, str] = {}
        for roh in bestand:
            if _ausgenommen(roh):
                continue
            norm = _nfc(roh)
            schluessel = norm.casefold()
            normen, rohe = gruppen.setdefault(schluessel, (set(), set()))
            normen.add(norm)
            rohe.add(roh)
            if norm in je_nfc and je_nfc[norm] != roh:
                kollision.add(schluessel)
            je_nfc[norm] = roh
    return [
        (normen, sorted(rohe))
        for schluessel, (normen, rohe) in sorted(gruppen.items())
        if len(normen) > 1 or schluessel in kollision
    ]


def doppelte_pfade(pc: Bestand, vm: Bestand, zuletzt: Bestand) -> list[list[str]]:
    """Gruppen von Pfaden, die auf dem PC dieselbe Datei waeren; `abgleich_planen` ueberspringt sie."""
    return [rohe for _, rohe in _doppel_gruppen(pc, vm, zuletzt)]


def abgleich_planen(pc: Bestand, vm: Bestand, zuletzt: Bestand) -> list[Aktion]:
    """Plant den Abgleich; `zuletzt` ist der gemeinsame Stand nach dem letzten erfolgreichen Lauf."""
    uebersprungen: set[str] = set()
    for normen, _ in _doppel_gruppen(pc, vm, zuletzt):
        uebersprungen |= normen
    pc, vm, zuletzt = _normalisiert(pc), _normalisiert(vm), _normalisiert(zuletzt)
    aktionen: list[Aktion] = []
    for pfad in sorted((set(pc) | set(vm)) - uebersprungen):
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
                aktionen.append(Aktion("nach_pc" if eigner == "pc" else "nach_vm", pfad))
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


# --- Teil 2: Bestand, Uebertragung, Zustand, Lauf ------------------------------------------------------

log = logging.getLogger("wissens_spiegel")

Laeufer = Callable[..., "subprocess.CompletedProcess[bytes]"]
Rohnamen = dict[str, str]  # Bestand-Schluessel -> roher POSIX-Pfad auf der Platte dieser Seite

IGNORIERTE_NAMEN = (".DS_Store", "Thumbs.db", "desktop.ini")
IGNORIERTE_ENDUNGEN = ("~", ".tmp")
SSH_OPTIONEN = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
TIMEOUT_BESTAND = 30
TIMEOUT_UEBERTRAGUNG = 300

_WIN_VERBOTEN = frozenset('<>:"|?*\\') | frozenset(chr(i) for i in range(32))
_WIN_RESERVIERT = frozenset(
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
)


class VmFehler(Exception):
    """ssh/VM-Schritt gescheitert (nicht erreichbar, Zeitueberschreitung, Fernbefehl mit Fehler)."""


def _inventar(wurzel, konflikte, namen, endungen):
    """[[roher POSIX-Pfad, sha256, mtime], ...] und [nicht-UTF-8-Namen]; laeuft lokal UND auf der VM.

    Eigenstaendig (Importe innen), weil der Quelltext per `inspect.getsource` an `ssh ... python3 -` geht.
    """
    import hashlib
    import os

    dateien, unlesbar = [], []
    if not os.path.isdir(wurzel):
        return dateien, unlesbar
    for ordner, unter, namen_hier in os.walk(wurzel):
        rel_ordner = os.path.relpath(ordner, wurzel)
        if rel_ordner == ".":
            unter[:] = [u for u in unter if u != konflikte]
        unter.sort()
        for name in sorted(namen_hier):
            if name in namen or name.endswith(tuple(endungen)):
                continue
            voll = os.path.join(ordner, name)
            rel = name if rel_ordner == "." else rel_ordner.replace(os.sep, "/") + "/" + name
            try:
                rel.encode("utf-8")
            except UnicodeEncodeError:
                unlesbar.append(rel.encode("utf-8", "backslashreplace").decode("ascii", "replace"))
                continue
            if os.path.islink(voll) or not os.path.isfile(voll):
                continue
            h = hashlib.sha256()
            try:
                with open(voll, "rb") as fh:
                    for block in iter(lambda: fh.read(1 << 20), b""):
                        h.update(block)
                mtime = os.stat(voll).st_mtime
            except OSError:  # waehrend des Laufs verschwunden/gesperrt: naechster Lauf holt nach
                continue
            dateien.append([rel, h.hexdigest(), mtime])
    return dateien, unlesbar


def _bestand_aus(dateien: list[list[Any]], seite: str, unlesbar: list[str]) -> tuple[Bestand, Rohnamen]:
    """NFC-Schluessel + Rohnamen. Ein zweiter Rohname zum selben NFC-Schluessel behaelt seinen Rohnamen
    als Schluessel, damit `doppelte_pfade` die Gruppe meldet und `abgleich_planen` sie ueberspringt."""
    for name in unlesbar:
        log.warning("%s: Name nicht UTF-8, nicht abgeglichen: %s", seite, name)
    bestand: Bestand = {}
    roh: Rohnamen = {}
    for rel, sha, mtime in sorted(dateien, key=lambda d: (_nfc(d[0]) != d[0], d[0])):
        schluessel = _nfc(rel)
        if schluessel in bestand:
            schluessel = rel
        bestand[schluessel] = Eintrag(str(sha), float(mtime))
        roh[schluessel] = rel
    return bestand, roh


def bestand_lokal(wurzel: Path) -> tuple[Bestand, Rohnamen]:
    """Bestand der PC-Kopie (ohne `_konflikte/` oben und ohne System-/Temp-Dateien) und Rohnamen."""
    dateien, unlesbar = _inventar(str(wurzel), KONFLIKTE, IGNORIERTE_NAMEN, IGNORIERTE_ENDUNGEN)
    return _bestand_aus(dateien, "pc", unlesbar)


def _kurz(text: bytes | str | None, n: int = 200) -> str:
    if not text:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return " ".join(text.split())[:n]


def _ssh(ssh_ziel: str, fern: list[str], laeufer: Laeufer, *, eingabe: bytes, timeout: int) -> bytes:
    """Ein ssh-Aufruf; Daten und Pfade nur ueber stdin, nie in argv (ausser der gequoteten VM-Wurzel)."""
    cmd = ["ssh", *SSH_OPTIONEN, ssh_ziel, *fern]
    kw: dict[str, Any] = {"input": eingabe, "capture_output": True, "timeout": timeout, "check": True}
    if os.name == "nt":  # pythonw: kein aufblitzendes Konsolenfenster fuer ssh.exe
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        return laeufer(cmd, **kw).stdout or b""
    except subprocess.TimeoutExpired as exc:
        raise VmFehler(f"ssh {ssh_ziel}: Zeitueberschreitung nach {timeout} s") from exc
    except subprocess.CalledProcessError as exc:
        raise VmFehler(f"ssh {ssh_ziel}: Exit {exc.returncode} {_kurz(exc.stderr)}".rstrip()) from exc
    except OSError as exc:
        raise VmFehler(f"ssh nicht startbar: {type(exc).__name__}") from exc


def _vm_python(ssh_ziel: str, code: str, params: dict[str, Any], laeufer: Laeufer, timeout: int) -> Any:
    """Fuehrt `code` per `ssh ziel python3 -` aus (Skript ueber stdin, Parameter als `P`), JSON zurueck."""
    skript = f"import json, sys\nP = json.loads({json.dumps(params)!r})\n{code}"
    ausgabe = _ssh(ssh_ziel, ["python3", "-"], laeufer, eingabe=skript.encode("utf-8"), timeout=timeout)
    try:
        return json.loads(ausgabe.decode("utf-8"))
    except ValueError as exc:
        raise VmFehler(f"ssh {ssh_ziel}: Antwort kein JSON ({_kurz(ausgabe, 80)})") from exc


_VM_BESTAND = inspect.getsource(_inventar) + (
    "\nd, u = _inventar(P['wurzel'], P['konflikte'], P['namen'], P['endungen'])\n"
    "json.dump({'dateien': d, 'unlesbar': u}, sys.stdout)\n"
)

_VM_SICHERN = """
import os, shutil
w = P['wurzel']
for ziel in P['ziele']:  # aeltere Sicherung desselben Tages nicht ueberschreiben
    z = os.path.join(w, ziel)
    if os.path.lexists(z):
        n = 1
        while os.path.lexists(z + '.' + str(n)):
            n += 1
        os.replace(z, z + '.' + str(n))
for quelle, ziel in P['kopien']:
    z = os.path.join(w, ziel)
    os.makedirs(os.path.dirname(z), exist_ok=True)
    shutil.copy2(os.path.join(w, quelle), z)
print('{}')
"""

_VM_LOESCHEN = """
import os
for rel in P['pfade']:
    try:
        os.remove(os.path.join(P['wurzel'], rel))
    except FileNotFoundError:
        pass
print('{}')
"""


def bestand_vm(ssh_ziel: str, vm_wurzel: str, laeufer: Laeufer = subprocess.run) -> tuple[Bestand, Rohnamen]:
    """Bestand der VM mit einem `ssh ziel python3 -` (Skript ueber stdin); fehlender Ordner = leer."""
    params = {"wurzel": vm_wurzel, "konflikte": KONFLIKTE, "namen": list(IGNORIERTE_NAMEN),
              "endungen": list(IGNORIERTE_ENDUNGEN)}
    antwort = _vm_python(ssh_ziel, _VM_BESTAND, params, laeufer, TIMEOUT_BESTAND)
    try:
        return _bestand_aus(antwort["dateien"], "vm", antwort["unlesbar"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VmFehler(f"ssh {ssh_ziel}: Bestand unvollstaendig ({type(exc).__name__})") from exc


def _atomar_schreiben(ziel: Path, daten: bytes) -> None:
    ziel.parent.mkdir(parents=True, exist_ok=True)
    tmp = ziel.with_name(f".{ziel.name}.spiegel.tmp")  # Endung .tmp: nie im Bestand
    tmp.write_bytes(daten)
    os.replace(tmp, ziel)


def _tar_entpacken(daten: bytes, wurzel: Path, erwartet: set[str]) -> None:
    """Entpackt nur die angeforderten regulaeren Dateien; alles andere bricht ab, bevor geschrieben wird."""
    with tarfile.open(fileobj=io.BytesIO(daten), mode="r:", encoding="utf-8") as tf:
        mitglieder = tf.getmembers()
        namen = {m.name for m in mitglieder}
        fremd = [m.name for m in mitglieder if not m.isfile() or m.name not in erwartet]
        if fremd or namen != erwartet:
            raise VmFehler(f"tar-Antwort passt nicht: {len(fremd)} fremd, {len(erwartet - namen)} fehlen")
        for m in mitglieder:
            quelle = tf.extractfile(m)
            if quelle is None:
                raise VmFehler(f"tar-Antwort ohne Inhalt: {m.name}")
            _atomar_schreiben(wurzel / m.name, quelle.read())


def ausfuehren(
    aktionen: list[Aktion],
    pc_wurzel: Path,
    ssh_ziel: str,
    vm_wurzel: str,
    laeufer: Laeufer = subprocess.run,
    *,
    roh_pc: Rohnamen | None = None,
    roh_vm: Rohnamen | None = None,
    heute: str | None = None,
) -> None:
    """Fuehrt den Plan aus: Sichern (VM) -> Upload (tar) -> Download (tar) -> Loeschen VM -> Loeschen PC.

    Jeder Pfad hat hoechstens eine Kopier-/Loeschaktion, also wirkt `konflikt_sichern` immer vorher.
    Gelesen/geloescht wird unter dem Rohnamen der jeweiligen Seite, geschrieben unter dem Rohnamen der
    Quelle; hatte die Zielseite den Pfad unter einem anderen Rohnamen (NFC/NFD), wird jener danach entfernt.
    """
    roh_pc, roh_vm = roh_pc or {}, roh_vm or {}
    datum = heute or date.today().isoformat()
    sichern_vm: list[list[str]] = []   # [VM-Rohname, Ziel]: Kopie auf der VM
    hoch: list[tuple[str, str]] = []   # (PC-Rohname, Name auf der VM)
    runter: list[str] = []             # VM-Rohnamen
    weg_vm: list[str] = []
    weg_pc: list[str] = []
    for a in aktionen:
        pc_name, vm_name = roh_pc.get(a.pfad, a.pfad), roh_vm.get(a.pfad, a.pfad)
        if a.art == "konflikt_sichern":
            if besitzer(a.pfad) == "pc":  # Nicht-Besitzer VM: VM-Fassung auf der VM sichern
                sichern_vm.append([vm_name, f"{KONFLIKTE}/{datum}/{vm_name}"])
            else:  # Nicht-Besitzer PC: PC-Fassung auf die VM hochladen
                hoch.append((pc_name, f"{KONFLIKTE}/{datum}/{pc_name}"))
        elif a.art == "nach_vm":
            hoch.append((pc_name, pc_name))
            if a.pfad in roh_vm and vm_name != pc_name:
                weg_vm.append(vm_name)
        elif a.art == "nach_pc":
            runter.append(vm_name)
            if a.pfad in roh_pc and pc_name != vm_name:
                weg_pc.append(pc_name)
        elif a.art == "loeschen_vm":
            weg_vm.append(vm_name)
        elif a.art == "loeschen_pc":
            weg_pc.append(pc_name)

    q = shlex.quote(vm_wurzel)
    ziele = [z for _, z in sichern_vm] + [z for _, z in hoch if z.startswith(KONFLIKTE + "/")]
    if ziele:
        _vm_python(ssh_ziel, _VM_SICHERN, {"wurzel": vm_wurzel, "ziele": ziele, "kopien": sichern_vm},
                   laeufer, TIMEOUT_UEBERTRAGUNG)
    if hoch:
        puffer = io.BytesIO()
        with tarfile.open(fileobj=puffer, mode="w", format=tarfile.PAX_FORMAT, encoding="utf-8") as tf:
            for quelle, ziel in hoch:
                tf.add(str(pc_wurzel / quelle), arcname=ziel, recursive=False)
        _ssh(ssh_ziel, [f"mkdir -p {q} && tar -C {q} -xf -"], laeufer,
             eingabe=puffer.getvalue(), timeout=TIMEOUT_UEBERTRAGUNG)
    if runter:
        daten = _ssh(ssh_ziel, [f"tar -C {q} -cf - --null --verbatim-files-from --no-recursion -T -"],
                     laeufer, eingabe="\0".join(runter).encode("utf-8"), timeout=TIMEOUT_UEBERTRAGUNG)
        _tar_entpacken(daten, pc_wurzel, set(runter))
    if weg_vm:
        _vm_python(ssh_ziel, _VM_LOESCHEN, {"wurzel": vm_wurzel, "pfade": weg_vm}, laeufer, TIMEOUT_UEBERTRAGUNG)
    for name in weg_pc:
        (pc_wurzel / name).unlink(missing_ok=True)


def zustand_lesen(pfad: Path) -> Bestand:
    """Gemeinsamer Stand nach dem letzten erfolgreichen Lauf; fehlende Datei = leer."""
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {p: Eintrag(str(e[0]), float(e[1])) for p, e in daten["eintraege"].items()}


def zustand_schreiben(pfad: Path, bestand: Bestand) -> None:
    """Schreibt den Stand atomar (temp + replace)."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    inhalt = {"version": 1, "eintraege": {p: [e.sha256, e.mtime] for p, e in sorted(bestand.items())}}
    tmp = pfad.with_name(pfad.name + ".tmp")
    tmp.write_text(json.dumps(inhalt, ensure_ascii=False, indent=0), encoding="utf-8")
    os.replace(tmp, pfad)


def _windows_tauglich(pfad: str) -> bool:
    """Laesst sich der Pfad am PC (NTFS) anlegen? Schliesst auch `..` und leere Teile aus."""
    for teil in pfad.split("/"):
        if not teil or teil in (".", "..") or teil[-1] in ". ":
            return False
        if any(c in _WIN_VERBOTEN for c in teil) or teil.split(".")[0].upper() in _WIN_RESERVIERT:
            return False
    return True


def _stand_nach_abgleich(pc: Bestand, vm: Bestand, zuletzt: Bestand, uebersprungen: set[str]) -> Bestand:
    """Stand nach vollem Erfolg: Besitzer-Eintrag je Pfad (spiegelt `abgleich_planen`); uebersprungene
    Doppel-Pfade behalten ihren alten Eintrag."""
    pc, vm, zuletzt = _normalisiert(pc), _normalisiert(vm), _normalisiert(zuletzt)
    neu: Bestand = {}
    for pfad in (set(pc) | set(vm)) - uebersprungen:
        hat, gegen = (pc.get(pfad), vm.get(pfad)) if besitzer(pfad) == "pc" else (vm.get(pfad), pc.get(pfad))
        if hat is not None:
            neu[pfad] = hat
        elif gegen is not None and pfad not in zuletzt:  # neu beim Nicht-Besitzer, zum Besitzer kopiert
            neu[pfad] = gegen
    for pfad in uebersprungen & set(zuletzt):
        neu[pfad] = zuletzt[pfad]
    return neu


def lauf(
    pc_wurzel: Path,
    ssh_ziel: str,
    vm_wurzel: str,
    zustand: Path,
    trocken: bool = False,
    laeufer: Laeufer = subprocess.run,
    heute: str | None = None,
) -> int:
    """Ein Spiegellauf. Exit 0 ok, 1 lokaler Fehler, 2 VM nicht erreichbar/abgebrochen, 3 Loeschgrenze."""
    try:
        zuletzt = zustand_lesen(zustand)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        # Ohne letzten Stand wird nichts geloescht und jede Abweichung gesichert: sicherer Rueckfall.
        log.warning("Zustand %s unlesbar (%s), Abgleich ohne letzten Stand", zustand, type(exc).__name__)
        zuletzt = {}
    pc, roh_pc = bestand_lokal(pc_wurzel)
    try:
        vm, roh_vm = bestand_vm(ssh_ziel, vm_wurzel, laeufer=laeufer)
    except VmFehler as exc:
        log.error("VM nicht erreichbar, nichts geaendert: %s", exc)
        return 2

    untauglich = {p for p in set(pc) | set(vm) if not _windows_tauglich(p)}
    for p in sorted(untauglich):
        log.warning("Name am PC unzulaessig, nicht abgeglichen: %s", p)
    pc = {p: e for p, e in pc.items() if p not in untauglich}
    vm = {p: e for p, e in vm.items() if p not in untauglich}

    gruppen = _doppel_gruppen(pc, vm, zuletzt)
    for _, rohe in gruppen:
        log.warning("Doppelte Pfade (Gross-/Kleinschreibung oder NFC/NFD), nicht abgeglichen: %s", " | ".join(rohe))
    uebersprungen: set[str] = set().union(*(normen for normen, _ in gruppen))

    aktionen = abgleich_planen(pc, vm, zuletzt)
    try:
        loeschgrenze_pruefen(aktionen, len(set(pc) | set(vm)))
    except ZuVieleLoeschungen as exc:
        log.error("Abbruch vor jeder Aenderung: %s", exc)
        return 3
    for a in aktionen:
        log.info("%s%s %s", "[trocken] " if trocken else "", a.art, a.pfad)
    if trocken:
        log.info("Trockenlauf: %d Aktionen geplant, nichts geschrieben", len(aktionen))
        return 0
    if aktionen:
        try:
            ausfuehren(aktionen, pc_wurzel, ssh_ziel, vm_wurzel, laeufer, roh_pc=roh_pc, roh_vm=roh_vm, heute=heute)
        except VmFehler as exc:
            log.error("Abgleich abgebrochen, Zustand bleibt: %s", exc)
            return 2
        except OSError as exc:
            log.error("Abgleich abgebrochen (lokal), Zustand bleibt: %s: %s", type(exc).__name__, exc)
            return 1
    neu = _stand_nach_abgleich(pc, vm, zuletzt, uebersprungen)
    zustand_schreiben(zustand, neu)
    log.info("Abgleich fertig: %d Aktionen, %d Dateien im Stand", len(aktionen), len(neu))
    return 0


def _log_einrichten(root: Path) -> None:
    ordner = root / "logs" / "rowboat"
    ordner.mkdir(parents=True, exist_ok=True)
    datei = logging.handlers.RotatingFileHandler(
        ordner / "wissens_spiegel.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    datei.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(datei)
    if sys.stderr is not None:  # unter pythonw gibt es kein stderr
        log.addHandler(logging.StreamHandler())
    log.setLevel(logging.INFO)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Wissens-Spiegel: PC-Kopie und VM von knowledge/ nach Besitzer abgleichen.")
    ap.add_argument("--trocken", action="store_true", help="nur planen, Aktionen ins Log, nichts schreiben")
    ap.add_argument("--pc-wurzel", type=Path, default=Path.home() / ".rowboat" / "knowledge")
    ap.add_argument("--ssh-ziel", default="offload-vm")
    ap.add_argument("--vm-wurzel", default="/home/debian/rowboat-data/knowledge")
    ap.add_argument("--zustand", type=Path, default=None)
    args = ap.parse_args(argv)
    root = Path(os.environ.get("VIBEMIND_ROOT") or "C:/Users/User/Desktop/Vibemind_V1")
    lokal = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    zustand = args.zustand or lokal / "VibeMind" / "wissens_spiegel" / "zuletzt.json"
    _log_einrichten(root)
    try:
        return lauf(args.pc_wurzel, args.ssh_ziel, args.vm_wurzel, zustand, trocken=args.trocken)
    except Exception:  # noqa: BLE001 - die Aufgabe soll einen lesbaren Grund im Log hinterlassen
        log.exception("Wissens-Spiegel abgebrochen")
        return 1


if __name__ == "__main__":
    sys.exit(main())
