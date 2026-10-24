"""Wissens-Spiegel: gleicht die PC-Kopie von `~/.rowboat/knowledge` und die VM nach Besitzer ab.

Teil 1 (rein, ohne Ein-/Ausgabe): `besitzer`, `abgleich_planen`, `doppelte_pfade`, `loeschgrenze_pruefen`.
Teil 2 (Ein-/Ausgabe): `bestand_lokal`/`bestand_vm` (Bestand + Rohnamen), `ausfuehren` (tar ueber ssh),
`zustand_lesen`/`zustand_schreiben`, `lauf` und die CLI
`python spaces/rowboat/scripts/wissens_spiegel.py [--trocken]` (Aufgabe alle 5 min, siehe
`wissens_spiegel_aufgabe.ps1`). Als Datei aufrufen, nicht per `-m spaces.rowboat.scripts...`:
`spaces/rowboat/__init__.py` importiert ein fehlendes `.config`; dieses Modul braucht nur stdlib. Exit: 0 ok (auch: anderer Lauf haelt `lauf.lock`), 1 lokaler Fehler oder
PC-Ordner fehlt, 2 VM nicht erreichbar/Uebertragung gescheitert, 3 Loeschgrenze, 4 ein Ziel hat sich zwischen
Bestand und Aktion geaendert (unberuehrt gelassen, naechster Lauf plant neu). Der Zustand (`zuletzt`) wird nur
nach vollem Erfolg geschrieben. Uploads landen erst in `<vm>/.spiegel-eingang/<lauf-id>/` und werden per
`os.replace` eingesetzt; vor jedem Ueberschreiben/Loeschen wird der aktuelle sha gegen den Bestand geprueft.

Regeln (Besitzer-Tabelle im Plan 2026-10-24-rowboat-zentrale-teil-a-mini-pc):
- Jeder Pfad hat genau einen Besitzer (`pc` oder `vm`); der Besitzer gewinnt bei Abweichung.
- Loeschen wirkt nur vom Besitzer aus; fehlt die Datei beim Nicht-Besitzer, wird sie zurueckgespielt.
- Eine neue Datei beim Nicht-Besitzer wandert zum Besitzer.
- Hat der Nicht-Besitzer eine bestehende Datei seit dem letzten Lauf geaendert, wird seine Fassung
  vor dem Ueberschreiben/Loeschen gesichert (`konflikt_sichern` steht vor der Aktion).
- Verglichen wird nur ueber sha256; mtime dient der Anzeige. Pfade werden NFC-normalisiert.
- Pfade, die sich nur in Gross-/Kleinschreibung unterscheiden (gleiche Datei auf dem PC), oder die im
  selben Bestand als NFC- und NFD-Fassung vorkommen, werden nicht geplant; `doppelte_pfade` meldet sie.
- `_konflikte/`, `.spiegel-eingang/` und `.git/` (oberste Ebene) werden nie gespiegelt.
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
import time
import unicodedata
from datetime import date, datetime
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
    "Coding-Projects", "Workflows", "System", "Videos", "vibemind-discourse", "Simulations",
})
# Brain-Ausnahmen innerhalb sonst VM-eigener Ordner (exakte Segmente, wie PC_ORDNER)
PC_UNTERORDNER = frozenset({("People", "Veraltet")})
PC_DATEIEN = frozenset({
    ("People", "User (user).md"),
    ("Diary", "Blue Rose Diary.md"),
    (".bubble_sync_hashes.json",),  # schreibt ein PC-Werkzeug (Bubble-Abgleich)
})
KONFLIKTE = "_konflikte"
EINGANG = ".spiegel-eingang"  # Upload-Zwischenablage auf der VM, nie gespiegelt
GIT = ".git"  # Rowboats eigene Historie je Seite; reist nur beim Umzug einmal mit
NIE_SPIEGELN = (KONFLIKTE, EINGANG, GIT)  # oberste Ebene
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
    if tuple(teile) in PC_DATEIEN:
        return "pc"
    if len(teile) > 2 and tuple(teile[:2]) in PC_UNTERORDNER:
        return "pc"
    return "vm"


def _ausgenommen(pfad: str) -> bool:
    return _nfc(pfad).split("/")[0] in NIE_SPIEGELN


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


class Abweichung(Exception):
    """Ziele haben sich zwischen Bestand und Aktion geaendert; diese Pfade wurden nicht angefasst."""

    def __init__(self, pfade: list[str]):
        super().__init__(f"{len(pfade)} Ziel(e) seit dem Bestand geaendert, uebersprungen: {', '.join(pfade)}")
        self.pfade = pfade


def _datei_sha(pfad):
    """sha256 der Datei oder None, wenn es sie nicht gibt; laeuft lokal UND auf der VM (eigenstaendig)."""
    import hashlib
    import os

    if not os.path.lexists(pfad):
        return None
    h = hashlib.sha256()
    with open(pfad, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _inventar(wurzel, oben_aus, namen, endungen):
    """[[roher POSIX-Pfad, sha256, mtime], ...] und [nicht-UTF-8-Namen]; laeuft lokal UND auf der VM.

    Eigenstaendig (Importe innen), weil der Quelltext per `inspect.getsource` an `ssh ... python3 -` geht.
    `oben_aus`: Ordner der obersten Ebene, die nie gespiegelt werden (`_konflikte`, `.spiegel-eingang`).
    """
    import hashlib
    import os

    dateien, unlesbar = [], []
    if not os.path.isdir(wurzel):
        return dateien, unlesbar
    for ordner, unter, namen_hier in os.walk(wurzel):
        rel_ordner = os.path.relpath(ordner, wurzel)
        if rel_ordner == ".":
            unter[:] = [u for u in unter if u not in oben_aus]
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
    """Bestand der PC-Kopie (ohne `_konflikte/`, `.spiegel-eingang/` oben, ohne System-/Temp-Dateien)."""
    dateien, unlesbar = _inventar(str(wurzel), NIE_SPIEGELN, IGNORIERTE_NAMEN, IGNORIERTE_ENDUNGEN)
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
    "\nd, u = _inventar(P['wurzel'], P['oben_aus'], P['namen'], P['endungen'])\n"
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

_EINGANG_RAEUMEN = """
import os, shutil
e = os.path.join(P['wurzel'], P['eingang'])
shutil.rmtree(e, ignore_errors=True)
try:
    os.rmdir(os.path.dirname(e))  # .spiegel-eingang selbst, wenn leer
except OSError:
    pass
"""

# Setzt die hochgeladenen Dateien aus dem Eingang an ihren Platz (os.replace = atomar), aber nur, wenn das
# Ziel noch den sha aus dem Bestand hat (bzw. noch fehlt). Der Eingang wird in jedem Fall geraeumt.
_VM_EINSETZEN = inspect.getsource(_datei_sha) + """
import os
abw = []
try:
    for name, soll in P['dateien']:
        z = os.path.join(P['wurzel'], name)
        if _datei_sha(z) != soll:
            abw.append(name)
            continue
        os.makedirs(os.path.dirname(z), exist_ok=True)
        os.replace(os.path.join(P['wurzel'], P['eingang'], name), z)
finally:
""" + "\n".join("    " + zeile for zeile in _EINGANG_RAEUMEN.strip().splitlines()) + """
json.dump(abw, sys.stdout)
"""

_VM_LOESCHEN = inspect.getsource(_datei_sha) + """
import os
abw = []
for rel, soll in P['pfade']:
    p = os.path.join(P['wurzel'], rel)
    ist = _datei_sha(p)
    if ist is None:
        continue
    if ist != soll:
        abw.append(rel)
        continue
    os.remove(p)
json.dump(abw, sys.stdout)
"""


def bestand_vm(ssh_ziel: str, vm_wurzel: str, laeufer: Laeufer = subprocess.run) -> tuple[Bestand, Rohnamen]:
    """Bestand der VM mit einem `ssh ziel python3 -` (Skript ueber stdin); fehlender Ordner = leer."""
    params = {"wurzel": vm_wurzel, "oben_aus": list(NIE_SPIEGELN), "namen": list(IGNORIERTE_NAMEN),
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


def _tar_entpacken(daten: bytes, wurzel: Path, erwartet: dict[str, str | None]) -> list[str]:
    """Entpackt nur die angeforderten regulaeren Dateien (sonst Abbruch, bevor geschrieben wird).

    `erwartet`: Name -> sha, den das Ziel am PC laut Bestand hat (None = fehlt). Weicht das Ziel jetzt
    ab, bleibt es unberuehrt; die Namen kommen zurueck.
    """
    abweichend: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(daten), mode="r:", encoding="utf-8") as tf:
        mitglieder = tf.getmembers()
        namen = {m.name for m in mitglieder}
        fremd = [m.name for m in mitglieder if not m.isfile() or m.name not in erwartet]
        if fremd or namen != set(erwartet):
            raise VmFehler(f"tar-Antwort passt nicht: {len(fremd)} fremd, {len(set(erwartet) - namen)} fehlen")
        for m in mitglieder:
            ziel = wurzel / m.name
            if _datei_sha(ziel) != erwartet[m.name]:
                abweichend.append(m.name)
                continue
            quelle = tf.extractfile(m)
            if quelle is None:
                raise VmFehler(f"tar-Antwort ohne Inhalt: {m.name}")
            _atomar_schreiben(ziel, quelle.read())
    return abweichend


def _lauf_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}"


def _sha_von(stand: Bestand, pfad: str) -> str | None:
    e = stand.get(pfad)
    return None if e is None else e.sha256


def hochladen(
    quellen: list[tuple[Path, str, str | None]],
    ssh_ziel: str,
    vm_wurzel: str,
    laeufer: Laeufer = subprocess.run,
    *,
    eingang_ordner: str = EINGANG,
    tar_filter: Callable[[tarfile.TarInfo], tarfile.TarInfo | None] | None = None,
) -> list[str]:
    """Laedt `(lokale Datei, Name unter vm_wurzel, erwarteter sha dort oder None)` hoch.

    Erst per tar ueber ssh nach `<vm_wurzel>/<eingang_ordner>/<lauf-id>/`, dann setzt ein Fernskript jede
    Datei per `os.replace` ein, aber nur, wenn das Ziel noch den erwarteten sha hat (None = fehlt noch).
    Zurueck kommen die Namen, deren Ziel abwich (unberuehrt gelassen). Der Eingang wird immer geraeumt.
    """
    eingang = f"{eingang_ordner}/{_lauf_id()}"
    puffer = io.BytesIO()
    with tarfile.open(fileobj=puffer, mode="w", format=tarfile.PAX_FORMAT, encoding="utf-8") as tf:
        for quelle, ziel, _ in quellen:
            tf.add(str(quelle), arcname=ziel, recursive=False, filter=tar_filter)
    stufe = shlex.quote(f"{vm_wurzel.rstrip('/')}/{eingang}")
    params = {"wurzel": vm_wurzel, "eingang": eingang}
    try:
        _ssh(ssh_ziel, [f"mkdir -p {stufe} && tar -C {stufe} -xf -"], laeufer,
             eingabe=puffer.getvalue(), timeout=TIMEOUT_UEBERTRAGUNG)
        return list(_vm_python(ssh_ziel, _VM_EINSETZEN, params | {"dateien": [[z, s] for _, z, s in quellen]},
                               laeufer, TIMEOUT_UEBERTRAGUNG))
    except VmFehler:
        try:  # Reste raeumen; scheitert das auch, ignoriert der Bestand den Eingang trotzdem
            _vm_python(ssh_ziel, _EINGANG_RAEUMEN + "print('{}')\n", params, laeufer, TIMEOUT_BESTAND)
        except VmFehler as exc:
            log.warning("Eingang %s nicht geraeumt: %s", eingang, exc)
        raise


def ausfuehren(
    aktionen: list[Aktion],
    pc_wurzel: Path,
    ssh_ziel: str,
    vm_wurzel: str,
    laeufer: Laeufer = subprocess.run,
    *,
    roh_pc: Rohnamen | None = None,
    roh_vm: Rohnamen | None = None,
    stand_pc: Bestand | None = None,
    stand_vm: Bestand | None = None,
    heute: str | None = None,
) -> None:
    """Fuehrt den Plan aus: Sichern (VM) -> Upload in den Eingang + Einsetzen -> Download -> Loeschen VM/PC.

    Jeder Pfad hat hoechstens eine Kopier-/Loeschaktion, also wirkt `konflikt_sichern` immer vorher.
    Gelesen/geloescht wird unter dem Rohnamen der jeweiligen Seite, geschrieben unter dem Rohnamen der
    Quelle; hatte die Zielseite den Pfad unter einem anderen Rohnamen (NFC/NFD), wird jener danach entfernt.
    Vor jedem Ueberschreiben und Loeschen wird geprueft, dass das Ziel noch den sha aus `stand_pc`/`stand_vm`
    hat (bzw. noch fehlt); sonst bleibt es unberuehrt, und am Ende kommt `Abweichung` (kein Zustand).
    Upload geht erst in `<vm_wurzel>/.spiegel-eingang/<lauf-id>/`, ein abgerissener Strom erreicht nie
    einen echten Namen.
    """
    roh_pc, roh_vm = roh_pc or {}, roh_vm or {}
    stand_pc, stand_vm = stand_pc or {}, stand_vm or {}
    datum = heute or date.today().isoformat()

    def soll(stand: Bestand, roh: Rohnamen, pfad: str, name: str) -> str | None:
        """Erwarteter sha unter `name`: der Bestand, wenn der Pfad dort unter genau diesem Namen lag."""
        return _sha_von(stand, pfad) if roh.get(pfad, pfad) == name else None

    sichern_vm: list[list[str]] = []                 # [VM-Rohname, Ziel]: Kopie auf der VM
    hoch: list[tuple[str, str, str | None]] = []     # (PC-Rohname, Name auf der VM, erwarteter sha dort)
    runter: dict[str, str | None] = {}               # VM-Rohname -> erwarteter sha am PC unter dem Namen
    weg_vm: list[tuple[str, str | None, str]] = []   # (VM-Rohname, erwarteter sha, haengt an Schreibziel)
    weg_pc: list[tuple[str, str | None, str]] = []
    for a in aktionen:
        pc_name, vm_name = roh_pc.get(a.pfad, a.pfad), roh_vm.get(a.pfad, a.pfad)
        if a.art == "konflikt_sichern":
            if besitzer(a.pfad) == "pc":  # Nicht-Besitzer VM: VM-Fassung auf der VM sichern
                sichern_vm.append([vm_name, f"{KONFLIKTE}/{datum}/{vm_name}"])
            else:  # Nicht-Besitzer PC: PC-Fassung auf die VM hochladen (Ziel ist nach dem Sichern frei)
                hoch.append((pc_name, f"{KONFLIKTE}/{datum}/{pc_name}", None))
        elif a.art == "nach_vm":
            hoch.append((pc_name, pc_name, soll(stand_vm, roh_vm, a.pfad, pc_name)))
            if a.pfad in roh_vm and vm_name != pc_name:
                weg_vm.append((vm_name, _sha_von(stand_vm, a.pfad), pc_name))
        elif a.art == "nach_pc":
            runter[vm_name] = soll(stand_pc, roh_pc, a.pfad, vm_name)
            if a.pfad in roh_pc and pc_name != vm_name:
                weg_pc.append((pc_name, _sha_von(stand_pc, a.pfad), vm_name))
        elif a.art == "loeschen_vm":
            weg_vm.append((vm_name, _sha_von(stand_vm, a.pfad), ""))
        elif a.art == "loeschen_pc":
            weg_pc.append((pc_name, _sha_von(stand_pc, a.pfad), ""))

    abweichend: list[str] = []
    q = shlex.quote(vm_wurzel)
    ziele = [z for _, z in sichern_vm] + [z for _, z, _ in hoch if z.startswith(KONFLIKTE + "/")]
    if ziele:
        _vm_python(ssh_ziel, _VM_SICHERN, {"wurzel": vm_wurzel, "ziele": ziele, "kopien": sichern_vm},
                   laeufer, TIMEOUT_UEBERTRAGUNG)
    if hoch:
        abweichend += hochladen([(pc_wurzel / q, z, s) for q, z, s in hoch], ssh_ziel, vm_wurzel, laeufer)
    if runter:
        daten = _ssh(
            ssh_ziel,
            [f"tar -C {q} -cf - --null --verbatim-files-from --no-recursion --hard-dereference -T -"],
            laeufer, eingabe="\0".join(runter).encode("utf-8"), timeout=TIMEOUT_UEBERTRAGUNG,
        )
        abweichend += _tar_entpacken(daten, pc_wurzel, runter)
    # Alte Rohnamen nur entfernen, wenn der neue Name auch geschrieben wurde
    weg_vm = [w for w in weg_vm if w[2] not in abweichend]
    weg_pc = [w for w in weg_pc if w[2] not in abweichend]
    if weg_vm:
        abweichend += _vm_python(ssh_ziel, _VM_LOESCHEN, {"wurzel": vm_wurzel, "pfade": [[n, s] for n, s, _ in weg_vm]},
                                 laeufer, TIMEOUT_UEBERTRAGUNG)
    for name, erwartet, _ in weg_pc:
        pfad = pc_wurzel / name
        ist = _datei_sha(pfad)
        if ist is None:
            continue
        if ist != erwartet:
            abweichend.append(name)
            continue
        pfad.unlink()
    if abweichend:
        raise Abweichung(abweichend)


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
    """Ein Spiegellauf. Exit 0 ok (auch: anderer Lauf haelt die Sperre), 1 lokaler Fehler/PC-Ordner fehlt,
    2 VM nicht erreichbar/abgebrochen, 3 Loeschgrenze, 4 Ziel(e) waehrend des Laufs geaendert."""
    if not Path(pc_wurzel).is_dir():
        log.error("PC-Ordner fehlt: %s - nichts geaendert", pc_wurzel)
        return 1
    sperre = Path(zustand).parent / SPERRDATEI
    if not _sperre_nehmen(sperre):
        log.info("Wissens-Spiegel laeuft bereits (Sperre %s), dieser Lauf entfaellt", sperre)
        return 0
    try:
        return _lauf_gesperrt(pc_wurzel, ssh_ziel, vm_wurzel, zustand, trocken, laeufer, heute)
    finally:
        _sperre_freigeben(sperre)


SPERRDATEI = "lauf.lock"
SPERRE_MAX_ALTER = 3600  # s; die Aufgabe bricht nach 30 min ab, aeltere Sperren sind verwaist


ERROR_ACCESS_DENIED = 5
STILL_ACTIVE = 259


def _pid_lebt_windows(pid: int, k: Any, letzter_fehler: Callable[[], int]) -> bool:
    """`k` = kernel32 (oder Fake). Zugriff verweigert heisst: den Prozess gibt es (fremder Benutzer/erhoeht)."""
    import ctypes
    from ctypes import wintypes

    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return letzter_fehler() == ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(h, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        k.CloseHandle(h)


def _pid_lebt(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":  # os.kill(pid, 0) wuerde unter Windows den Prozess beenden
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        return _pid_lebt_windows(pid, k, ctypes.get_last_error)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _sperre_lebt(pfad: Path) -> bool:
    try:
        alter = time.time() - pfad.stat().st_mtime
        text = pfad.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return False
    except OSError:
        return True  # gerade in Arbeit/gesperrt: lieber diesen Lauf auslassen
    if alter > SPERRE_MAX_ALTER:
        return False
    if not text.isdigit():
        return alter < 60  # frisch angelegt, PID noch nicht geschrieben
    return _pid_lebt(int(text))


def _sperre_nehmen(pfad: Path) -> bool:
    """Sperrdatei per O_CREAT|O_EXCL mit eigener PID; verwaiste (toter Prozess, zu alt) wird uebernommen."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(pfad, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _sperre_lebt(pfad):
                return False
            log.warning("verwaiste Sperre %s entfernt", pfad)
            pfad.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(str(os.getpid()))
        return True
    return False


def _sperre_freigeben(pfad: Path) -> None:
    try:
        if pfad.read_text(encoding="ascii").strip() == str(os.getpid()):
            pfad.unlink()
    except OSError:
        pass


def _lauf_gesperrt(
    pc_wurzel: Path, ssh_ziel: str, vm_wurzel: str, zustand: Path, trocken: bool, laeufer: Laeufer,
    heute: str | None,
) -> int:
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
            ausfuehren(aktionen, pc_wurzel, ssh_ziel, vm_wurzel, laeufer, roh_pc=roh_pc, roh_vm=roh_vm,
                       stand_pc=pc, stand_vm=vm, heute=heute)
        except Abweichung as exc:
            log.warning("Abgleich unvollstaendig, Zustand bleibt (naechster Lauf plant neu): %s", exc)
            return 4
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


def zustand_standard() -> Path:
    """`%LOCALAPPDATA%\\VibeMind\\wissens_spiegel\\zuletzt.json` (auch vom Umzug geschrieben)."""
    lokal = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return lokal / "VibeMind" / "wissens_spiegel" / "zuletzt.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Wissens-Spiegel: PC-Kopie und VM von knowledge/ nach Besitzer abgleichen.")
    ap.add_argument("--trocken", action="store_true", help="nur planen, Aktionen ins Log, nichts schreiben")
    ap.add_argument("--pc-wurzel", type=Path, default=Path.home() / ".rowboat" / "knowledge")
    ap.add_argument("--ssh-ziel", default="offload-vm")
    ap.add_argument("--vm-wurzel", default="/home/debian/rowboat-data/knowledge")
    ap.add_argument("--zustand", type=Path, default=None)
    args = ap.parse_args(argv)
    root = Path(os.environ.get("VIBEMIND_ROOT") or "C:/Users/User/Desktop/Vibemind_V1")
    zustand = args.zustand or zustand_standard()
    _log_einrichten(root)
    try:
        return lauf(args.pc_wurzel, args.ssh_ziel, args.vm_wurzel, zustand, trocken=args.trocken)
    except Exception:  # noqa: BLE001 - die Aufgabe soll einen lesbaren Grund im Log hinterlassen
        log.exception("Wissens-Spiegel abgebrochen")
        return 1


if __name__ == "__main__":
    sys.exit(main())
