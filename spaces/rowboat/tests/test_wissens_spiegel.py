"""Tests fuer den Abgleichsplan des Wissens-Spiegels (reine Logik)."""
from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import wissens_spiegel as ws  # noqa: E402
from wissens_spiegel import Aktion, Eintrag  # noqa: E402


def e(sha: str, mtime: float = 1.0) -> Eintrag:
    return Eintrag(sha, mtime)


def test_besitzer():
    assert ws.besitzer("Projects/x.md") == "pc"
    assert ws.besitzer("companys/VibeMind/Agent-Notizen/a.md") == "pc"
    assert ws.besitzer("companys/VibeMind/Kontakte/bav/Max.md") == "vm"
    assert ws.besitzer("companys/VibeMind/Newsletter/n.md") == "vm"
    assert ws.besitzer("companys/VibeMind/Wochenbild/w.md") == "vm"
    assert ws.besitzer("Notes/Einkauf.md") == "vm"
    assert ws.besitzer("Company.md") == "vm"
    assert ws.besitzer("Welcome.md") == "vm"
    assert ws.besitzer("Unbekannt/x.md") == "vm"
    assert ws.besitzer("vibemind-discourse/t.md") == "pc"


def test_besitzer_pc_ausnahmen_brain():
    # exakte Dateien
    assert ws.besitzer("People/User (user).md") == "pc"
    assert ws.besitzer("Diary/Blue Rose Diary.md") == "pc"
    assert ws.besitzer(".bubble_sync_hashes.json") == "pc"  # schreibt ein PC-Werkzeug
    # Ordner
    assert ws.besitzer("People/Veraltet/alt.md") == "pc"
    assert ws.besitzer("People/Veraltet/tief/x.md") == "pc"
    assert ws.besitzer("Simulations/run1/s.md") == "pc"
    # exakte Segmente: Nachbarn bleiben VM
    assert ws.besitzer("People/Max.md") == "vm"
    assert ws.besitzer("People/User (user).md.bak") == "vm"
    assert ws.besitzer("People/Veraltet.md") == "vm"
    assert ws.besitzer("People/VeraltetX/a.md") == "vm"
    assert ws.besitzer("Diary/2026-10-24.md") == "vm"
    assert ws.besitzer("x/Diary/Blue Rose Diary.md") == "vm"
    assert ws.besitzer("Simulations.md") == "vm"
    assert ws.besitzer("SimulationsX/a.md") == "vm"
    assert ws.besitzer("Notes/.bubble_sync_hashes.json") == "vm"


def test_git_ordner_wird_nie_gespiegelt():
    pc = {".git/HEAD": e("A"), ".git/objects/ab/cd": e("B"), "Notes/n.md": e("N")}
    vm = {".git/HEAD": e("C"), ".git/index": e("D"), "Notes/n.md": e("N")}
    assert ws.abgleich_planen(pc, vm, {}) == []
    nur_git_vm = {p: x for p, x in vm.items() if p.startswith(".git/")}
    assert ws.abgleich_planen({}, nur_git_vm, nur_git_vm) == []
    assert ws.abgleich_planen(pc, {}, pc) == [Aktion("loeschen_pc", "Notes/n.md")]
    assert ws.doppelte_pfade(pc, {".git/head": e("A")}, {}) == []
    # nur die oberste Ebene: ein .git tiefer ist normaler Inhalt
    assert ws.abgleich_planen({"Notes/.git/x": e("A")}, {}, {}) == [Aktion("nach_vm", "Notes/.git/x")]


def test_git_ordner_fehlt_im_inventar(tmp_path):
    schreibe(tmp_path, ".git/HEAD", "ref")
    schreibe(tmp_path, ".git/objects/ab/cd", "obj")
    schreibe(tmp_path, "Notes/n.md", "n")
    bestand, _ = ws.bestand_lokal(tmp_path)
    assert set(bestand) == {"Notes/n.md"}
    vm_bestand, _ = ws.bestand_vm(ZIEL, str(tmp_path), laeufer=FakeVm())
    assert set(vm_bestand) == {"Notes/n.md"}


class _FakeKernel32:
    def __init__(self, handle: int, exit_code: int = 259):
        self.handle, self.exit_code, self.geschlossen = handle, exit_code, False

    def OpenProcess(self, _zugriff, _erben, _pid):  # noqa: N802 - Windows-API-Name
        return self.handle

    def GetExitCodeProcess(self, _h, ref):  # noqa: N802
        ref._obj.value = self.exit_code
        return 1

    def CloseHandle(self, _h):  # noqa: N802
        self.geschlossen = True
        return 1


def test_pid_lebt_windows_zugriff_verweigert_gilt_als_lebend():
    assert ws._pid_lebt_windows(4711, _FakeKernel32(0), lambda: 5) is True  # ERROR_ACCESS_DENIED
    assert ws._pid_lebt_windows(4711, _FakeKernel32(0), lambda: 87) is False  # ERROR_INVALID_PARAMETER: weg
    k = _FakeKernel32(99, 259)
    assert ws._pid_lebt_windows(4711, k, lambda: 0) is True and k.geschlossen
    assert ws._pid_lebt_windows(4711, _FakeKernel32(99, 0), lambda: 0) is False


def test_besitzer_doku_ordner_pc_und_urteile_vm():
    # Doku-Loop schreibt am PC: Ordner Doku gehoert dem PC (Spiegel PC -> VM)
    assert ws.besitzer("Doku/K1.md") == "pc"
    assert ws.besitzer("Doku/Ketten/K1.md") == "pc"
    # anderer Top-Level-Ordner: bleibt VM-eigen
    assert ws.besitzer("Doku-Urteile/Urteile.md") == "vm"


def test_besitzer_aenderung_gewinnt():
    plan = ws.abgleich_planen(
        {"Projects/p.md": e("B")}, {"Projects/p.md": e("C")}, {"Projects/p.md": e("A")}
    )
    assert plan == [Aktion("konflikt_sichern", "Projects/p.md"), Aktion("nach_vm", "Projects/p.md")]


def test_besitzer_aenderung_ohne_gegenaenderung_kein_konflikt():
    plan = ws.abgleich_planen(
        {"Projects/p.md": e("B")}, {"Projects/p.md": e("A")}, {"Projects/p.md": e("A")}
    )
    assert plan == [Aktion("nach_vm", "Projects/p.md")]


def test_vm_besitz_pc_aenderung_wird_ueberschrieben():
    plan = ws.abgleich_planen(
        {"Notes/n.md": e("C")}, {"Notes/n.md": e("B")}, {"Notes/n.md": e("A")}
    )
    assert plan == [Aktion("konflikt_sichern", "Notes/n.md"), Aktion("nach_pc", "Notes/n.md")]


def test_neue_datei_beim_nichtbesitzer_wandert_zum_besitzer():
    assert ws.abgleich_planen({"Notes/neu.md": e("A")}, {}, {}) == [Aktion("nach_vm", "Notes/neu.md")]
    assert ws.abgleich_planen({}, {"Projects/neu.md": e("A")}, {}) == [Aktion("nach_pc", "Projects/neu.md")]


def test_neue_datei_beim_besitzer_wird_kopiert():
    assert ws.abgleich_planen({"Projects/n.md": e("A")}, {}, {}) == [Aktion("nach_vm", "Projects/n.md")]
    assert ws.abgleich_planen({}, {"Notes/n.md": e("A")}, {}) == [Aktion("nach_pc", "Notes/n.md")]


def test_loeschen_nur_vom_besitzer():
    a = {"Projects/alt.md": e("A")}
    # VM-Loeschung eines PC-Besitz-Files wird zurueckgespielt
    assert ws.abgleich_planen(a, {}, a) == [Aktion("nach_vm", "Projects/alt.md")]
    # PC-Loeschung wirkt auf die VM
    assert ws.abgleich_planen({}, a, a) == [Aktion("loeschen_vm", "Projects/alt.md")]
    # Besitzer VM: PC loescht eine VM-Datei -> zurueckgespielt
    n = {"Notes/x.md": e("A")}
    assert ws.abgleich_planen({}, n, n) == [Aktion("nach_pc", "Notes/x.md")]


def test_loeschen_vm_besitz_wirkt_auf_pc():
    n = {"Notes/x.md": e("A")}
    # Besitzer VM fehlt Datei, PC hat sie (unveraendert) -> PC-Kopie loeschen
    assert ws.abgleich_planen(n, {}, n) == [Aktion("loeschen_pc", "Notes/x.md")]


def test_besitzer_loescht_nicht_wenn_nichtbesitzer_geaendert_hat():
    plan = ws.abgleich_planen({}, {"Projects/a.md": e("B")}, {"Projects/a.md": e("A")})
    assert plan == [Aktion("konflikt_sichern", "Projects/a.md"), Aktion("loeschen_vm", "Projects/a.md")]


def test_unveraendert_keine_aktion():
    b = {"Projects/p.md": e("A", 1.0), "Notes/n.md": e("B", 2.0)}
    vm = {"Projects/p.md": e("A", 9.0), "Notes/n.md": e("B", 8.0)}
    assert ws.abgleich_planen(b, vm, b) == []
    assert ws.abgleich_planen({}, {}, {}) == []


def test_einseitige_aenderung_beim_nichtbesitzer_wandert_zum_pc_besitzer():
    # VM aendert eine PC-eigene Datei, der PC nicht: die VM-Fassung kommt an, kein Konflikt
    plan = ws.abgleich_planen({"Projects/p.md": e("A")}, {"Projects/p.md": e("B")}, {"Projects/p.md": e("A")})
    assert plan == [Aktion("nach_pc", "Projects/p.md")]


def test_einseitige_aenderung_beim_nichtbesitzer_wandert_zum_vm_besitzer():
    plan = ws.abgleich_planen({"Notes/n.md": e("B")}, {"Notes/n.md": e("A")}, {"Notes/n.md": e("A")})
    assert plan == [Aktion("nach_vm", "Notes/n.md")]


def test_beide_geaendert_besitzer_gewinnt_mit_sicherung():
    p = "Projects/p.md"
    assert ws.abgleich_planen({p: e("B")}, {p: e("C")}, {p: e("A")}) == [
        Aktion("konflikt_sichern", p), Aktion("nach_vm", p)]
    n = "Notes/n.md"
    assert ws.abgleich_planen({n: e("B")}, {n: e("C")}, {n: e("A")}) == [
        Aktion("konflikt_sichern", n), Aktion("nach_pc", n)]


def test_beide_gleich_geaendert_keine_aktion():
    for pfad in ("Projects/p.md", "Notes/n.md"):
        assert ws.abgleich_planen({pfad: e("B", 1.0)}, {pfad: e("B", 2.0)}, {pfad: e("A")}) == []


def _anwenden(pfad: str, pc, vm, plan: list[Aktion]):
    """Spielt den Plan fuer einen Pfad durch: (pc danach, vm danach, gesicherte Fassungen)."""
    gesichert: list[str] = []
    eigner = ws.besitzer(pfad)
    for a in plan:
        assert a.pfad == pfad
        if a.art == "konflikt_sichern":
            fassung = vm if eigner == "pc" else pc
            assert fassung is not None
            gesichert.append(fassung)
        elif a.art == "nach_vm":
            assert pc is not None
            vm = pc
        elif a.art == "nach_pc":
            assert vm is not None
            pc = vm
        elif a.art == "loeschen_vm":
            vm = None
        elif a.art == "loeschen_pc":
            pc = None
    return pc, vm, gesichert


@pytest.mark.parametrize("pfad", ["Projects/p.md", "Notes/n.md", "Projects/a (Konflikt 2026-10-25 14-03).md",
                                  "Notes/n (Konflikt 2026-10-25 14-03 2).md"])
def test_fallstabelle_verliert_keine_aenderung(pfad):
    """Alle Kombinationen (fehlt/A/B/C) x pc/vm/zuletzt: jede geaenderte Fassung, die ueberschrieben oder
    geloescht wird, ist danach auf beiden Seiten oder in `_konflikte/` gesichert; die Seiten sind gleich,
    und der Folgestand entspricht dem Ergebnis."""
    werte = [None, "A", "B", "C"]
    faelle = 0
    for p in werte:
        for v in werte:
            for alt in werte:
                stand = lambda x: {} if x is None else {pfad: e(x)}  # noqa: E731
                plan = ws.abgleich_planen(stand(p), stand(v), stand(alt))
                assert len([a for a in plan if a.art != "konflikt_sichern"]) <= 1, (p, v, alt, plan)
                if any(a.art == "konflikt_sichern" for a in plan):
                    assert plan[0].art == "konflikt_sichern", (p, v, alt, plan)
                pc_n, vm_n, gesichert = _anwenden(pfad, p, v, plan)
                assert pc_n == vm_n, (p, v, alt, plan)
                for fassung in (p, v):
                    if fassung is not None and fassung != alt:  # geaendert oder neu
                        assert fassung == pc_n or fassung in gesichert, (p, v, alt, plan)
                if None not in (p, v, alt) and (p == alt) != (v == alt):  # nur eine Seite geaendert
                    assert gesichert == [] and pc_n == (v if p == alt else p), (p, v, alt, plan)
                neu = ws._stand_nach_abgleich(stand(p), stand(v), stand(alt), set())
                erwartet = {} if pc_n is None else {pfad: pc_n}
                assert {k: x.sha256 for k, x in neu.items()} == erwartet, (p, v, alt)
                faelle += 1
    assert faelle == 64


def test_loeschgrenze():
    loesch = [Aktion("loeschen_vm", f"p{i}.md") for i in range(21)]
    with pytest.raises(ws.ZuVieleLoeschungen):
        ws.loeschgrenze_pruefen(loesch, 1000)
    with pytest.raises(ws.ZuVieleLoeschungen):
        ws.loeschgrenze_pruefen(loesch[:5], 20)
    ws.loeschgrenze_pruefen(loesch[:20], 1000)
    ws.loeschgrenze_pruefen(loesch[:2], 20)
    ws.loeschgrenze_pruefen([Aktion("nach_vm", "a.md")] * 50, 10)  # nur Loeschungen zaehlen
    with pytest.raises(ws.ZuVieleLoeschungen):
        ws.loeschgrenze_pruefen(loesch[:1], 0)


def test_pfade_mit_umlauten_und_leerzeichen():
    nfd = unicodedata.normalize("NFD", "Agent Notes/Über (neu).md")
    nfc = unicodedata.normalize("NFC", nfd)
    assert nfd != nfc
    assert ws.abgleich_planen({nfd: e("A")}, {nfc: e("A")}, {nfc: e("A")}) == []
    plan = ws.abgleich_planen({nfd: e("A")}, {}, {})
    assert plan == [Aktion("nach_vm", nfc)]


def test_gross_kleinschreibung_doppel_wird_uebersprungen():
    pc = {"Projects/x.md": e("A")}
    vm = {"Projects/x.md": e("A"), "projects/x.md": e("B")}
    assert ws.abgleich_planen(pc, vm, {}) == []
    assert ws.doppelte_pfade(pc, vm, {}) == [["Projects/x.md", "projects/x.md"]]
    # der Rest wird weiter abgeglichen
    pc["Notes/n.md"] = e("A")
    assert ws.abgleich_planen(pc, vm, {}) == [Aktion("nach_vm", "Notes/n.md")]


def test_doppel_aus_zuletzt_zaehlt_mit():
    pc = {"Projects/x.md": e("A")}
    assert ws.abgleich_planen(pc, {}, {"projects/x.md": e("A")}) == []
    assert len(ws.doppelte_pfade(pc, {}, {"projects/x.md": e("A")})) == 1


def test_nfc_nfd_im_selben_bestand_wird_gemeldet():
    nfc = "Notes/Über.md"
    nfd = unicodedata.normalize("NFD", nfc)
    pc = {nfc: e("A"), nfd: e("B")}
    assert ws.abgleich_planen(pc, {}, {}) == []
    assert ws.doppelte_pfade(pc, {}, {}) == [sorted([nfc, nfd])]
    # NFD am PC gegen NFC auf der VM ist KEIN Doppel
    assert ws.doppelte_pfade({nfd: e("A")}, {nfc: e("A")}, {}) == []


def test_konflikte_ordner_wird_nie_gesynct():
    pc = {"_konflikte/2026-10-24/a.md": e("A")}
    vm = {"_konflikte/2026-10-24/b.md": e("B")}
    assert ws.abgleich_planen(pc, vm, {}) == []
    assert ws.abgleich_planen({}, vm, vm) == []
    assert ws.doppelte_pfade(pc, vm, {}) == []


# --- Task 2: Uebertragung, Zustand, Lauf (Fake-ssh fuehrt alles lokal aus) -------------------------

import hashlib  # noqa: E402
import io  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import shlex  # noqa: E402
import subprocess  # noqa: E402
import shutil  # noqa: E402
import tarfile  # noqa: E402

ZIEL = "offload-vm"
HEUTE = "2026-10-24"


class FakeVm:
    """Ersetzt `subprocess.run` fuer `ssh offload-vm ...` und fuehrt die Fernbefehle lokal aus.

    Der Fernbefehl wird wie von der entfernten Shell zerlegt (shlex), damit falsches Quoten auffaellt.
    `python3 -` laeuft mit dem lokalen Interpreter; `tar` wird mit `tarfile` nachgebildet
    (GNU-Optionen `-C`, `-xf -`, `-cf - --null --verbatim-files-from --no-recursion -T -`).
    """

    def __init__(self, fehler_bei: int | None = None, ausnahme: BaseException | None = None,
                 vor_aufruf=None, upload_abbrechen: bool = False):
        self.aufrufe: list[tuple[list[str], dict]] = []
        self.fehler_bei = fehler_bei  # 1-basierter Aufruf, der scheitert
        self.ausnahme = ausnahme
        self.vor_aufruf = vor_aufruf  # f(n) vor dem n-ten Aufruf: simuliert Schreiber zwischen Bestand und Aktion
        self.upload_abbrechen = upload_abbrechen  # tar -xf schreibt die erste Datei halb und bricht ab

    def __call__(self, cmd, **kw):
        self.aufrufe.append((list(cmd), kw))
        if self.vor_aufruf is not None:
            self.vor_aufruf(len(self.aufrufe))
        if self.ausnahme is not None:
            raise self.ausnahme
        if self.fehler_bei is not None and len(self.aufrufe) == self.fehler_bei:
            raise subprocess.CalledProcessError(255, cmd, b"", b"ssh: connect to host offload-vm: timed out")
        assert cmd[0] == "ssh" and ZIEL in cmd
        fern = shlex.split(" ".join(cmd[cmd.index(ZIEL) + 1:]))
        daten = kw.get("input") or b""
        if fern == ["python3", "-"]:
            return subprocess.run([sys.executable, "-"], input=daten, capture_output=True, timeout=60, check=True)
        teile: list[list[str]] = [[]]
        for t in fern:
            if t == "&&":
                teile.append([])
            else:
                teile[-1].append(t)
        ausgabe = b""
        for befehl in teile:
            if befehl[:2] == ["mkdir", "-p"]:
                Path(befehl[2]).mkdir(parents=True, exist_ok=True)
            elif befehl[0] == "tar" and "-xf" in befehl:
                wurzel = Path(befehl[befehl.index("-C") + 1])
                with tarfile.open(fileobj=io.BytesIO(daten), mode="r:", encoding="utf-8") as tf:
                    for m in tf:
                        ziel = wurzel / m.name
                        ziel.parent.mkdir(parents=True, exist_ok=True)
                        inhalt = tf.extractfile(m).read()
                        if self.upload_abbrechen:  # wie GNU tar bei abgerissenem Strom: halbe Datei, Exit 2
                            ziel.write_bytes(inhalt[: len(inhalt) // 2])
                            raise subprocess.CalledProcessError(2, cmd, b"", b"tar: Unexpected EOF in archive")
                        ziel.write_bytes(inhalt)
            elif befehl[0] == "tar" and "-cf" in befehl:
                assert {"--null", "--verbatim-files-from", "--no-recursion", "--hard-dereference", "-T"} <= set(befehl)
                wurzel = Path(befehl[befehl.index("-C") + 1])
                puffer = io.BytesIO()
                with tarfile.open(fileobj=puffer, mode="w", format=tarfile.GNU_FORMAT, encoding="utf-8") as tf:
                    for name in daten.decode("utf-8").split("\0"):
                        if name:
                            tf.add(str(wurzel / name), arcname=name, recursive=False)
                ausgabe = puffer.getvalue()
            else:
                raise AssertionError(f"unerwarteter Fernbefehl: {befehl}")
        return subprocess.CompletedProcess(cmd, 0, ausgabe, b"")


def schreibe(wurzel: Path, rel: str, inhalt: bytes | str) -> None:
    pfad = wurzel / rel
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_bytes(inhalt.encode("utf-8") if isinstance(inhalt, str) else inhalt)


def sha(inhalt: str) -> str:
    return hashlib.sha256(inhalt.encode("utf-8")).hexdigest()


def dateien(wurzel: Path) -> dict[str, bytes]:
    return {p.relative_to(wurzel).as_posix(): p.read_bytes() for p in sorted(wurzel.rglob("*")) if p.is_file()}


@pytest.fixture
def seiten(tmp_path):
    pc = tmp_path / "pc"
    vm = tmp_path / "vm wurzel (test)"  # Leerzeichen/Klammern: der Fernbefehl muss quoten
    pc.mkdir()
    vm.mkdir()
    return pc, vm, tmp_path / "zustand" / "zuletzt.json"


def test_bestand_lokal_nfc_schluessel_rohnamen_und_ausnahmen(tmp_path):
    nfd = unicodedata.normalize("NFD", "Agent Notes/Über (neu).md")
    schreibe(tmp_path, nfd, "a")
    schreibe(tmp_path, "Projects/p.md", "p")
    for weg in ("_konflikte/2026-10-01/x.md", ".spiegel-eingang/1-2/Notes/halb.md", "Notes/.DS_Store", "Thumbs.db", "Notes/desktop.ini",
                "Notes/n.md~", "Notes/n.tmp"):
        schreibe(tmp_path, weg, "weg")
    schreibe(tmp_path, "Notes/_konflikte/bleibt.md", "b")  # nur die oberste Ebene ist ausgenommen
    bestand, roh = ws.bestand_lokal(tmp_path)
    nfc = unicodedata.normalize("NFC", nfd)
    assert sorted(bestand) == sorted([nfc, "Projects/p.md", "Notes/_konflikte/bleibt.md"])
    assert bestand[nfc].sha256 == sha("a")
    assert roh[nfc] == nfd


def test_bestand_vm_ein_ssh_aufruf_ohne_pfade_in_argv(seiten):
    _, vm, _ = seiten
    schreibe(vm, "Notes/a b.md", "x")
    schreibe(vm, "Notes/.DS_Store", "weg")
    fake = FakeVm()
    bestand, roh = ws.bestand_vm(ZIEL, str(vm), laeufer=fake)
    assert list(bestand) == ["Notes/a b.md"] and roh == {"Notes/a b.md": "Notes/a b.md"}
    (cmd, kw), = fake.aufrufe
    assert cmd[-3:] == [ZIEL, "python3", "-"]
    assert kw["timeout"] == 30
    assert "vm wurzel" not in " ".join(cmd)


def test_bestand_vm_fehlender_ordner_ist_leer(seiten):
    _, vm, _ = seiten
    assert ws.bestand_vm(ZIEL, str(vm / "gibt-es-nicht"), laeufer=FakeVm()) == ({}, {})


def test_zustand_lesen_schreiben(tmp_path):
    ziel = tmp_path / "a" / "zuletzt.json"
    assert ws.zustand_lesen(ziel) == {}
    b = {"Notes/Über.md": Eintrag("abc", 1.5)}
    ws.zustand_schreiben(ziel, b)
    assert ws.zustand_lesen(ziel) == b
    assert [p.name for p in ziel.parent.iterdir()] == ["zuletzt.json"]


def test_vm_nicht_erreichbar_aendert_nichts(seiten):
    pc, vm, zustand = seiten
    schreibe(pc, "Projects/p.md", "neu")
    ws.zustand_schreiben(zustand, {"Projects/alt.md": Eintrag("x", 1.0)})
    vorher_zustand, vorher_pc = zustand.read_bytes(), dateien(pc)
    fake = FakeVm(ausnahme=subprocess.TimeoutExpired(["ssh"], 30))
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=fake) == 2
    assert zustand.read_bytes() == vorher_zustand
    assert dateien(pc) == vorher_pc
    assert dateien(vm) == {}


def _drei_aktionen(pc: Path, vm: Path) -> None:
    schreibe(pc, "Projects/a.md", "a")            # nach_vm
    schreibe(vm, "Notes/b.md", "b")               # nach_pc
    schreibe(pc, "Projects/k.md", "pc-fassung")   # konflikt_sichern + nach_vm
    schreibe(vm, "Projects/k.md", "vm-fassung")


def test_zustand_nur_nach_vollem_erfolg(seiten):
    pc, vm, zustand = seiten
    _drei_aktionen(pc, vm)
    ws.zustand_schreiben(zustand, {"Projects/k.md": Eintrag(sha("alt"), 1.0)})
    vorher = zustand.read_bytes()
    # Aufruf 1 = Bestand, 2 = Konfliktsicherung, 3 = Upload (scheitert), dann nur noch Eingang aufraeumen
    fake = FakeVm(fehler_bei=3)
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=fake, heute=HEUTE) == 2
    assert not any("-cf" in " ".join(cmd) for cmd, _ in fake.aufrufe)  # kein Download mehr
    assert zustand.read_bytes() == vorher
    assert not (pc / "Notes/b.md").exists()


def test_zustand_nach_erfolg_ist_besitzerstand(seiten):
    pc, vm, zustand = seiten
    _drei_aktionen(pc, vm)
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE) == 0
    stand = ws.zustand_lesen(zustand)
    assert {p: e.sha256 for p, e in stand.items()} == {
        "Projects/a.md": sha("a"), "Notes/b.md": sha("b"), "Projects/k.md": sha("pc-fassung"),
    }
    assert (vm / "Projects/k.md").read_text() == "pc-fassung"
    assert (vm / "_konflikte" / HEUTE / "Projects/k.md").read_text() == "vm-fassung"
    assert (pc / "Notes/b.md").read_text() == "b"
    fake = FakeVm()  # zweiter Lauf: nur der Bestand, sonst nichts zu tun
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=fake, heute=HEUTE) == 0
    assert len(fake.aufrufe) == 1


def test_trockenlauf_schreibt_nichts(seiten, monkeypatch, caplog):
    pc, vm, zustand = seiten
    _drei_aktionen(pc, vm)

    def verboten(*a, **k):
        raise AssertionError("ausfuehren im Trockenlauf")

    monkeypatch.setattr(ws, "ausfuehren", verboten)
    vorher_pc, vorher_vm = dateien(pc), dateien(vm)
    fake = FakeVm()
    with caplog.at_level(logging.INFO, logger="wissens_spiegel"):
        assert ws.lauf(pc, ZIEL, str(vm), zustand, trocken=True, laeufer=fake) == 0
    assert len(fake.aufrufe) == 1
    assert not zustand.exists()
    assert dateien(pc) == vorher_pc and dateien(vm) == vorher_vm
    assert "nach_vm Projects/a.md" in caplog.text
    assert "nach_pc Notes/b.md" in caplog.text
    assert "konflikt_sichern Projects/k.md" in caplog.text


def test_tar_uebertraegt_sonderzeichen(seiten):
    pc, vm, zustand = seiten
    hin = "Agent Notes/Über (neu).md"                          # VM-Besitz, neu am PC -> nach_vm
    her = "Projects/2026-10-23 split-vorlage-vibemind (Ä).md"   # PC-Besitz, neu auf der VM -> nach_pc
    inhalt_hin = "äöü ß\r\nZeile 2".encode("utf-8") + bytes(range(256))
    inhalt_her = "Ünïcödé — \U0001f6a3\n".encode("utf-8")
    schreibe(pc, hin, inhalt_hin)
    schreibe(vm, her, inhalt_her)
    fake = FakeVm()
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=fake, heute=HEUTE) == 0
    assert (vm / hin).read_bytes() == inhalt_hin
    assert (pc / her).read_bytes() == inhalt_her
    for cmd, _ in fake.aufrufe:  # keine Wissenspfade in argv
        assert "Über" not in " ".join(cmd) and "split-vorlage" not in " ".join(cmd)


def test_konflikt_wird_gesichert(seiten):
    pc, vm, zustand = seiten
    alt = {"Projects/p.md": Eintrag(sha("alt"), 1.0), "Notes/n.md": Eintrag(sha("alt"), 1.0)}
    ws.zustand_schreiben(zustand, alt)
    schreibe(pc, "Projects/p.md", "pc-neu")   # PC-Besitz: VM-Fassung sichern
    schreibe(vm, "Projects/p.md", "vm-neu")
    schreibe(pc, "Notes/n.md", "pc-neu")      # VM-Besitz: PC-Fassung auf die VM sichern
    schreibe(vm, "Notes/n.md", "vm-neu")
    schreibe(vm, f"_konflikte/{HEUTE}/Notes/n.md", "frueherer Konflikt")
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE) == 0
    k = vm / "_konflikte" / HEUTE
    assert (k / "Projects/p.md").read_text() == "vm-neu"
    assert (k / "Notes/n.md").read_text() == "pc-neu"
    assert (k / "Notes/n.md.1").read_text() == "frueherer Konflikt"  # nichts geht still verloren
    assert (vm / "Projects/p.md").read_text() == "pc-neu"
    assert (pc / "Notes/n.md").read_text() == "vm-neu"
    assert not (pc / "_konflikte").exists()


def test_loeschungen_auf_beiden_seiten(seiten):
    pc, vm, zustand = seiten
    zuletzt = {}
    for i in range(20):  # 2 Loeschungen bei 22 Dateien < 10 %
        schreibe(pc, f"Projects/u{i}.md", "u")
        schreibe(vm, f"Projects/u{i}.md", "u")
        zuletzt[f"Projects/u{i}.md"] = Eintrag(sha("u"), 1.0)
    schreibe(vm, "Projects/weg.md", "w")   # PC hat geloescht -> loeschen_vm
    schreibe(pc, "Notes/weg.md", "w")      # VM hat geloescht -> loeschen_pc
    zuletzt |= {"Projects/weg.md": Eintrag(sha("w"), 1.0), "Notes/weg.md": Eintrag(sha("w"), 1.0)}
    ws.zustand_schreiben(zustand, zuletzt)
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE) == 0
    assert not (vm / "Projects/weg.md").exists()
    assert not (pc / "Notes/weg.md").exists()
    stand = ws.zustand_lesen(zustand)
    assert "Projects/weg.md" not in stand and "Notes/weg.md" not in stand and len(stand) == 20


def test_loeschgrenze_bricht_ohne_aenderung_ab(seiten):
    pc, vm, zustand = seiten
    zuletzt = {}
    for i in range(30):
        schreibe(vm, f"Projects/p{i}.md", "x")
        zuletzt[f"Projects/p{i}.md"] = Eintrag(sha("x"), 1.0)
    ws.zustand_schreiben(zustand, zuletzt)  # PC-Ordner leer (z. B. umbenannt)
    vorher = zustand.read_bytes()
    fake = FakeVm()
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=fake) == 3
    assert len(fake.aufrufe) == 1
    assert len(dateien(vm)) == 30 and zustand.read_bytes() == vorher


def test_doppelte_pfade_werden_gewarnt_und_behalten_ihren_zustand(seiten, monkeypatch, caplog):
    pc, vm, zustand = seiten
    schreibe(pc, "Notes/n.md", "n")
    vm_bestand = {"Projects/x.md": Eintrag(sha("A"), 1.0), "projects/x.md": Eintrag(sha("B"), 1.0)}
    monkeypatch.setattr(ws, "bestand_vm", lambda *a, **k: (vm_bestand, {p: p for p in vm_bestand}))
    ws.zustand_schreiben(zustand, {"Projects/x.md": Eintrag("alt", 1.0)})
    with caplog.at_level(logging.WARNING, logger="wissens_spiegel"):
        assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE) == 0
    assert "Projects/x.md" in caplog.text and "projects/x.md" in caplog.text
    assert not (pc / "Projects").exists()
    stand = ws.zustand_lesen(zustand)
    assert stand["Projects/x.md"] == Eintrag("alt", 1.0)
    assert stand["Notes/n.md"].sha256 == sha("n")


def test_windows_untaugliche_namen_werden_uebersprungen(seiten, monkeypatch, caplog):
    pc, vm, zustand = seiten
    schreibe(pc, "Notes/ok.md", "x")
    vm_bestand = {"Notes/a:b.md": Eintrag(sha("v"), 1.0), "Notes/ok.md": Eintrag(sha("x"), 1.0),
                  "Notes/punkt.": Eintrag(sha("v"), 1.0), "Notes/CON.md": Eintrag(sha("v"), 1.0)}
    monkeypatch.setattr(ws, "bestand_vm", lambda *a, **k: (vm_bestand, {p: p for p in vm_bestand}))
    fake = FakeVm()
    with caplog.at_level(logging.WARNING, logger="wissens_spiegel"):
        assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=fake, heute=HEUTE) == 0
    assert fake.aufrufe == []
    for name in ("Notes/a:b.md", "Notes/punkt.", "Notes/CON.md"):
        assert name in caplog.text
        assert name not in ws.zustand_lesen(zustand)


def test_rohname_der_zielseite_wird_ersetzt(seiten):
    pc, vm, zustand = seiten
    nfc = "Notes/Über.md"
    nfd = unicodedata.normalize("NFD", nfc)
    schreibe(vm, nfc, "vm")   # VM-Besitz, NFC, geaendert
    schreibe(pc, nfd, "alt")  # PC-Kopie unter NFD-Namen, unveraendert seit zuletzt
    ws.zustand_schreiben(zustand, {nfc: Eintrag(sha("alt"), 1.0)})
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE) == 0
    namen = [p.name for p in (pc / "Notes").iterdir()]
    assert namen == ["Über.md"] and unicodedata.normalize("NFC", namen[0]) == namen[0]
    assert (pc / nfc).read_text() == "vm"


def test_main_vorgaben(monkeypatch, tmp_path):
    gesehen = {}

    def fake_lauf(pc_wurzel, ssh_ziel, vm_wurzel, zustand, trocken=False, **_):
        gesehen.update(pc=pc_wurzel, ziel=ssh_ziel, vm=vm_wurzel, zustand=zustand, trocken=trocken)
        return 0

    monkeypatch.setattr(ws, "lauf", fake_lauf)
    monkeypatch.setenv("VIBEMIND_ROOT", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    assert ws.main(["--trocken"]) == 0
    assert gesehen["pc"] == Path.home() / ".rowboat" / "knowledge"
    assert gesehen["ziel"] == "offload-vm"
    assert gesehen["vm"] == "/home/debian/rowboat-data/knowledge"
    assert gesehen["zustand"] == tmp_path / "lad" / "VibeMind" / "wissens_spiegel" / "zuletzt.json"
    assert gesehen["trocken"] is True
    assert (tmp_path / "logs" / "rowboat" / "wissens_spiegel.log").exists()
    log = logging.getLogger("wissens_spiegel")
    for h in list(log.handlers):
        h.close()
        log.removeHandler(h)


# --- Fix-Runde 1: Eingangsordner, Pruefung vor dem Ueberschreiben, Sperrdatei, PC-Wurzel -----------

def _unveraendert(pc: Path, vm: Path, n: int = 20) -> dict[str, Eintrag]:
    """n gleiche PC-Besitz-Dateien auf beiden Seiten (haelt Einzel-Loeschungen unter der Loeschgrenze)."""
    stand = {}
    for i in range(n):
        schreibe(pc, f"Projects/u{i}.md", "u")
        schreibe(vm, f"Projects/u{i}.md", "u")
        stand[f"Projects/u{i}.md"] = Eintrag(sha("u"), 1.0)
    return stand


def test_eingangsordner_wird_nie_geplant():
    pc = {".spiegel-eingang/1/Notes/a.md": e("A")}
    assert ws.abgleich_planen(pc, {}, {}) == []
    assert ws.doppelte_pfade(pc, {}, {}) == []


def test_abgerissener_upload_hinterlaesst_keine_halbe_datei(seiten):
    pc, vm, zustand = seiten
    schreibe(pc, "Projects/p.md", "neue lange Fassung " * 50)  # PC-Besitz geaendert -> nach_vm
    schreibe(vm, "Projects/p.md", "alt")
    schreibe(pc, "Notes/neu.md", "ganz neu " * 50)              # neu -> nach_vm
    ws.zustand_schreiben(zustand, {"Projects/p.md": Eintrag(sha("alt"), 1.0)})
    vorher = zustand.read_bytes()
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(upload_abbrechen=True), heute=HEUTE) == 2
    assert (vm / "Projects/p.md").read_text() == "alt"
    assert not (vm / "Notes/neu.md").exists()
    assert not (vm / ".spiegel-eingang").exists()  # Eingang aufgeraeumt
    assert zustand.read_bytes() == vorher
    # naechster Lauf holt alles nach
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE) == 0
    assert (vm / "Projects/p.md").read_bytes() == (pc / "Projects/p.md").read_bytes()
    assert (vm / "Notes/neu.md").read_bytes() == (pc / "Notes/neu.md").read_bytes()
    assert not (vm / ".spiegel-eingang").exists()


def test_eingang_reste_werden_nicht_gespiegelt(seiten):
    pc, vm, zustand = seiten
    schreibe(vm, ".spiegel-eingang/alt-1/Notes/halb.md", "ha")
    fake = FakeVm()
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=fake, heute=HEUTE) == 0
    assert len(fake.aufrufe) == 1 and not (pc / ".spiegel-eingang").exists()


def test_pc_ueberschreiben_prueft_aktuellen_stand(seiten):
    pc, vm, zustand = seiten
    schreibe(vm, "Notes/n.md", "vm-neu")   # VM-Besitz geaendert -> nach_pc
    schreibe(pc, "Notes/n.md", "alt")
    ws.zustand_schreiben(zustand, {"Notes/n.md": Eintrag(sha("alt"), 1.0)})
    vorher = zustand.read_bytes()

    def spaet(n):  # PC-Schreiber nach dem PC-Bestand (Aufruf 1 = VM-Bestand)
        if n == 1:
            schreibe(pc, "Notes/n.md", "pc-spaet")

    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(vor_aufruf=spaet), heute=HEUTE) == 4
    assert (pc / "Notes/n.md").read_text() == "pc-spaet"
    assert zustand.read_bytes() == vorher
    # naechster Lauf: jetzt ein Konflikt -> PC-Fassung gesichert, Besitzer gewinnt
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE) == 0
    assert (vm / "_konflikte" / HEUTE / "Notes/n.md").read_text() == "pc-spaet"
    assert (pc / "Notes/n.md").read_text() == "vm-neu"


def test_vm_aenderung_an_pc_datei_kommt_am_pc_an(seiten):
    pc, vm, zustand = seiten
    schreibe(pc, "Projects/p.md", "alt")
    schreibe(vm, "Projects/p.md", "vm-neu")  # Nicht-Besitzer VM aendert allein -> nach_pc
    ws.zustand_schreiben(zustand, {"Projects/p.md": Eintrag(sha("alt"), 1.0)})
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE) == 0
    assert (pc / "Projects/p.md").read_text() == "vm-neu"
    assert not (vm / "_konflikte").exists()
    assert {p: x.sha256 for p, x in ws.zustand_lesen(zustand).items()} == {"Projects/p.md": sha("vm-neu")}
    fake = FakeVm()  # zweiter Lauf: nichts zu tun
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=fake, heute=HEUTE) == 0
    assert len(fake.aufrufe) == 1


def test_vm_aenderung_an_pc_datei_prueft_pc_stand(seiten):
    pc, vm, zustand = seiten
    schreibe(pc, "Projects/p.md", "alt")
    schreibe(vm, "Projects/p.md", "vm-neu")
    ws.zustand_schreiben(zustand, {"Projects/p.md": Eintrag(sha("alt"), 1.0)})
    vorher = zustand.read_bytes()

    def spaet(n):  # PC-Besitzer schreibt nach dem PC-Bestand
        if n == 1:
            schreibe(pc, "Projects/p.md", "pc-spaet")

    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(vor_aufruf=spaet), heute=HEUTE) == 4
    assert (pc / "Projects/p.md").read_text() == "pc-spaet"
    assert zustand.read_bytes() == vorher
    # naechster Lauf: beide geaendert -> Besitzer PC gewinnt, VM-Fassung gesichert
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE) == 0
    assert (vm / "Projects/p.md").read_text() == "pc-spaet"
    assert (vm / "_konflikte" / HEUTE / "Projects/p.md").read_text() == "vm-neu"


def test_vm_ueberschreiben_prueft_aktuellen_stand(seiten):
    pc, vm, zustand = seiten
    schreibe(pc, "Projects/p.md", "pc-neu")  # PC-Besitz geaendert -> nach_vm
    schreibe(vm, "Projects/p.md", "alt")
    schreibe(pc, "Projects/q.md", "q")       # unabhaengige Aktion laeuft trotzdem
    ws.zustand_schreiben(zustand, {"Projects/p.md": Eintrag(sha("alt"), 1.0)})
    vorher = zustand.read_bytes()

    def spaet(n):  # Aufruf 2 = Upload in den Eingang: VM-Schreiber kommt dazwischen
        if n == 2:
            schreibe(vm, "Projects/p.md", "vm-spaet")

    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(vor_aufruf=spaet), heute=HEUTE) == 4
    assert (vm / "Projects/p.md").read_text() == "vm-spaet"
    assert (vm / "Projects/q.md").read_text() == "q"
    assert not (vm / ".spiegel-eingang").exists()
    assert zustand.read_bytes() == vorher


def test_pc_loeschen_prueft_aktuellen_stand(seiten):
    pc, vm, zustand = seiten
    stand = _unveraendert(pc, vm)
    schreibe(pc, "Notes/x.md", "alt")  # VM hat geloescht -> loeschen_pc
    stand["Notes/x.md"] = Eintrag(sha("alt"), 1.0)
    ws.zustand_schreiben(zustand, stand)
    vorher = zustand.read_bytes()

    def spaet(n):
        if n == 1:
            schreibe(pc, "Notes/x.md", "pc-spaet")

    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(vor_aufruf=spaet), heute=HEUTE) == 4
    assert (pc / "Notes/x.md").read_text() == "pc-spaet"
    assert zustand.read_bytes() == vorher


def test_vm_loeschen_prueft_aktuellen_stand(seiten):
    pc, vm, zustand = seiten
    stand = _unveraendert(pc, vm)
    schreibe(vm, "Projects/y.md", "alt")  # PC hat geloescht -> loeschen_vm
    stand["Projects/y.md"] = Eintrag(sha("alt"), 1.0)
    ws.zustand_schreiben(zustand, stand)
    vorher = zustand.read_bytes()

    def spaet(n):  # Aufruf 2 = Loeschskript
        if n == 2:
            schreibe(vm, "Projects/y.md", "vm-spaet")

    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(vor_aufruf=spaet), heute=HEUTE) == 4
    assert (vm / "Projects/y.md").read_text() == "vm-spaet"
    assert zustand.read_bytes() == vorher


def test_sperrdatei_haelt_zweiten_lauf_ab(seiten, caplog):
    pc, vm, zustand = seiten
    schreibe(pc, "Projects/a.md", "a")
    sperre = zustand.parent / "lauf.lock"
    sperre.parent.mkdir(parents=True, exist_ok=True)
    sperre.write_text(str(os.getpid()))  # lebender Prozess haelt die Sperre
    fake = FakeVm()
    with caplog.at_level(logging.INFO, logger="wissens_spiegel"):
        assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=fake) == 0
    assert fake.aufrufe == [] and not zustand.exists()
    assert "laeuft bereits" in caplog.text
    assert sperre.exists()


def test_verwaiste_sperrdatei_wird_uebernommen(seiten):
    pc, vm, zustand = seiten
    schreibe(pc, "Projects/a.md", "a")
    tot = subprocess.Popen([sys.executable, "-c", "pass"])
    tot.wait()
    sperre = zustand.parent / "lauf.lock"
    sperre.parent.mkdir(parents=True, exist_ok=True)
    sperre.write_text(str(tot.pid))
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE) == 0
    assert (vm / "Projects/a.md").read_text() == "a"
    assert not sperre.exists()  # nach dem Lauf wieder frei


def test_sperrdatei_wird_auch_nach_fehler_freigegeben(seiten):
    pc, vm, zustand = seiten
    fake = FakeVm(ausnahme=subprocess.TimeoutExpired(["ssh"], 30))
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=fake) == 2
    assert not (zustand.parent / "lauf.lock").exists()


def test_fehlende_pc_wurzel_ist_fehler(seiten, caplog):
    _, vm, zustand = seiten
    fake = FakeVm()
    assert ws.lauf(vm.parent / "gibt-es-nicht", ZIEL, str(vm), zustand, laeufer=fake) == 1
    assert fake.aufrufe == [] and not zustand.exists()


# --- Zusammenfuehren gleichzeitiger Aenderungen (Basis-Ablage, 3-Wege, Konflikt daneben, Meldung) ---------

from datetime import datetime  # noqa: E402

JETZT = datetime(2026, 10, 25, 14, 3)
TEXT = "".join(f"Zeile {i}\n" for i in range(1, 9))


@pytest.fixture(autouse=True)
def meldungen(monkeypatch):
    """Kein Test schickt echte Telegram-Nachrichten; gesendete Texte landen hier."""
    gesendet: list[str] = []
    monkeypatch.setattr(ws, "_telegram_melden", gesendet.append)
    return gesendet


def _basis(zustand: Path) -> Path:
    return zustand.parent / "basis"


def _gemeinsamer_start(pc: Path, vm: Path, zustand: Path, pfad: str, inhalt: str | bytes = TEXT) -> None:
    """Beide Seiten gleich, ein erfolgreicher Lauf legt Zustand und Basis an."""
    schreibe(pc, pfad, inhalt)
    schreibe(vm, pfad, inhalt)
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=JETZT) == 0


def test_drei_wege_getrennte_zeilen_und_ueberlappung():
    basis = "a\nb\nc\nd\ne\n"
    assert ws._drei_wege(basis, "A\nb\nc\nd\ne\n", "a\nb\nc\nd\nE\n") == "A\nb\nc\nd\nE\n"
    assert ws._drei_wege(basis, "A\nb\nc\nd\ne\n", "X\nb\nc\nd\ne\n") is None
    assert ws._drei_wege(basis, "A\nb\nc\nd\ne\n", "A\nb\nc\nd\ne\n") == "A\nb\nc\nd\ne\n"
    assert ws._drei_wege(basis, basis + "f\n", "a\nc\nd\ne\n") == "a\nc\nd\ne\nf\n"


def test_konfliktname():
    assert ws.konfliktname("Notes/a b.md", JETZT, set()) == "Notes/a b (Konflikt 2026-10-25 14-03).md"
    assert ws.konfliktname("README", JETZT, set()) == "README (Konflikt 2026-10-25 14-03)"
    belegt = {"Notes/A (Konflikt 2026-10-25 14-03).md"}
    assert ws.konfliktname("Notes/a.md", JETZT, belegt) == "Notes/a (Konflikt 2026-10-25 14-03) 2.md"


def test_basis_wird_nach_erfolg_angelegt_und_aufgeraeumt(seiten):
    pc, vm, zustand = seiten
    schreibe(pc, "Projects/bin.dat", b"\x00\x01binaer")
    schreibe(pc, ".git/HEAD", "ref")
    _unveraendert(pc, vm)  # genug Dateien, damit eine Loeschung unter der Loeschgrenze bleibt
    _gemeinsamer_start(pc, vm, zustand, "Projects/p.md")
    b = _basis(zustand)
    assert (b / "Projects/p.md").read_text(encoding="utf-8") == TEXT
    assert not (b / "Projects/bin.dat").exists()  # binaer: keine Basis
    assert not (b / ".git").exists()
    (pc / "Projects/p.md").unlink()  # Besitzer loescht -> Pfad weg, Basis weg
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE) == 0
    assert not (b / "Projects/p.md").exists()


def test_basis_bleibt_nach_gescheitertem_lauf(seiten):
    pc, vm, zustand = seiten
    _gemeinsamer_start(pc, vm, zustand, "Projects/p.md")
    schreibe(pc, "Projects/p.md", "neu\n")
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(fehler_bei=2), heute=HEUTE) == 2
    assert (_basis(zustand) / "Projects/p.md").read_text(encoding="utf-8") == TEXT
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE) == 0
    assert (_basis(zustand) / "Projects/p.md").read_text(encoding="utf-8") == "neu\n"


@pytest.mark.parametrize("pfad", ["Projects/p.md", "Notes/n.md"])
def test_getrennte_aenderungen_werden_zusammengefuehrt(seiten, meldungen, pfad):
    pc, vm, zustand = seiten
    _gemeinsamer_start(pc, vm, zustand, pfad)
    schreibe(pc, pfad, TEXT.replace("Zeile 1\n", "Zeile 1 vom PC\n"))
    schreibe(vm, pfad, TEXT.replace("Zeile 7\n", "Zeile 7 von der VM\n"))
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=JETZT) == 0
    erwartet = TEXT.replace("Zeile 1\n", "Zeile 1 vom PC\n").replace("Zeile 7\n", "Zeile 7 von der VM\n")
    assert (pc / pfad).read_text(encoding="utf-8") == erwartet
    assert (vm / pfad).read_text(encoding="utf-8") == erwartet
    assert sorted(dateien(pc)) == [pfad] and sorted(dateien(vm)) == [pfad]  # keine Konfliktdatei, kein _konflikte
    assert meldungen == []
    assert ws.zustand_lesen(zustand)[pfad].sha256 == sha(erwartet)
    assert (_basis(zustand) / pfad).read_text(encoding="utf-8") == erwartet
    fake = FakeVm()
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=fake, heute=HEUTE, jetzt=JETZT) == 0
    assert len(fake.aufrufe) == 1


@pytest.mark.parametrize("pfad,eigner", [("Projects/p.md", "pc"), ("Notes/n.md", "vm")])
def test_ueberlappende_aenderung_legt_konfliktdatei_daneben(seiten, meldungen, pfad, eigner):
    pc, vm, zustand = seiten
    _gemeinsamer_start(pc, vm, zustand, pfad)
    pc_text = TEXT.replace("Zeile 3\n", "Zeile 3 PC\n")
    vm_text = TEXT.replace("Zeile 3\n", "Zeile 3 VM\n")
    schreibe(pc, pfad, pc_text)
    schreibe(vm, pfad, vm_text)
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=JETZT) == 0
    sieger, verlierer = (pc_text, vm_text) if eigner == "pc" else (vm_text, pc_text)
    k = pfad[:-3] + " (Konflikt 2026-10-25 14-03).md"
    for seite in (pc, vm):
        assert (seite / pfad).read_text(encoding="utf-8") == sieger
        assert (seite / k).read_text(encoding="utf-8") == verlierer
    assert (vm / "_konflikte" / HEUTE / pfad).read_text(encoding="utf-8") == verlierer
    assert meldungen == [f"Wissens-Spiegel: Konflikt in {pfad} (Konfliktdatei: {k}) – beide Fassungen liegen "
                         "nebeneinander."]
    stand = ws.zustand_lesen(zustand)
    assert stand[k].sha256 == sha(verlierer) and stand[pfad].sha256 == sha(sieger)
    fake = FakeVm()  # Folgelauf: alles gleich, keine weitere Meldung
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=fake, heute=HEUTE, jetzt=JETZT) == 0
    assert len(fake.aufrufe) == 1 and len(meldungen) == 1


def test_ohne_basis_gewinnt_der_besitzer_wie_bisher(seiten, meldungen):
    pc, vm, zustand = seiten
    _gemeinsamer_start(pc, vm, zustand, "Projects/p.md")
    shutil.rmtree(_basis(zustand))
    schreibe(pc, "Projects/p.md", TEXT.replace("Zeile 1\n", "PC\n"))
    schreibe(vm, "Projects/p.md", TEXT.replace("Zeile 7\n", "VM\n"))
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=JETZT) == 0
    assert (vm / "Projects/p.md").read_text(encoding="utf-8") == TEXT.replace("Zeile 1\n", "PC\n")
    assert (vm / "_konflikte" / HEUTE / "Projects/p.md").read_text(encoding="utf-8") == TEXT.replace("Zeile 7\n", "VM\n")
    assert not any("Konflikt" in n for n in dateien(pc)) and meldungen == []


def test_basis_mit_falschem_sha_wird_nicht_benutzt(seiten, meldungen):
    pc, vm, zustand = seiten
    _gemeinsamer_start(pc, vm, zustand, "Projects/p.md")
    (_basis(zustand) / "Projects/p.md").write_text("fremd\n", encoding="utf-8")
    schreibe(pc, "Projects/p.md", TEXT.replace("Zeile 1\n", "PC\n"))
    schreibe(vm, "Projects/p.md", TEXT.replace("Zeile 7\n", "VM\n"))
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=JETZT) == 0
    assert (vm / "Projects/p.md").read_text(encoding="utf-8") == TEXT.replace("Zeile 1\n", "PC\n")
    assert (vm / "_konflikte" / HEUTE / "Projects/p.md").exists()


def test_binaer_gewinnt_der_besitzer(seiten, meldungen):
    pc, vm, zustand = seiten
    _gemeinsamer_start(pc, vm, zustand, "Projects/b.dat", b"\x00alt")
    schreibe(pc, "Projects/b.dat", b"\x00pc")
    schreibe(vm, "Projects/b.dat", b"\x00vm")
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=JETZT) == 0
    assert (vm / "Projects/b.dat").read_bytes() == b"\x00pc"
    assert (vm / "_konflikte" / HEUTE / "Projects/b.dat").read_bytes() == b"\x00vm"
    assert not any("Konflikt" in n for n in dateien(vm)) and meldungen == []


def test_meldung_scheitert_aendert_exit_nicht(seiten, monkeypatch, caplog):
    pc, vm, zustand = seiten

    def kaputt(_text):
        raise subprocess.CalledProcessError(2, ["python"], b"", b"token=GEHEIM")

    monkeypatch.setattr(ws, "_telegram_melden", kaputt)
    _gemeinsamer_start(pc, vm, zustand, "Projects/p.md")
    schreibe(pc, "Projects/p.md", TEXT.replace("Zeile 3\n", "PC\n"))
    schreibe(vm, "Projects/p.md", TEXT.replace("Zeile 3\n", "VM\n"))
    with caplog.at_level(logging.WARNING, logger="wissens_spiegel"):
        assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=JETZT) == 0
    assert "Meldung" in caplog.text and "GEHEIM" not in caplog.text


def test_meldung_hoechstens_fuenf_pfade():
    texte = ws._meldungstexte([(f"Projects/{i}.md", f"Projects/{i} (Konflikt x).md") for i in range(7)])
    assert len(texte) == 2
    assert texte[0].count("Konfliktdatei:") == 5 and texte[1].count("Konfliktdatei:") == 2


def test_telegram_melden_ruft_den_melder_mit_der_nachricht(monkeypatch, tmp_path):
    aufrufe = []

    def fake_run(cmd, **kw):
        aufrufe.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(ws.subprocess, "run", fake_run)
    monkeypatch.setenv("VIBEMIND_ROOT", str(tmp_path))
    ws.telegram_melden("Hallo")  # die echte Funktion; `_telegram_melden` ist in Tests ersetzt
    cmd, kw = aufrufe[0]
    assert cmd[1:] == [str(tmp_path / "scripts" / "notify_telegram.py"), "Hallo"]
    assert kw["timeout"] and kw["check"] is True


def test_zusammenfuehren_prueft_stand_vor_dem_schreiben(seiten):
    pc, vm, zustand = seiten
    _gemeinsamer_start(pc, vm, zustand, "Projects/p.md")
    schreibe(pc, "Projects/p.md", TEXT.replace("Zeile 1\n", "PC\n"))
    schreibe(vm, "Projects/p.md", TEXT.replace("Zeile 7\n", "VM\n"))
    vorher = zustand.read_bytes()

    def spaet(n):  # Aufruf 1 = VM-Bestand, 2 = VM-Inhalte holen; danach schreibt der PC noch einmal
        if n == 2:
            schreibe(pc, "Projects/p.md", "pc-spaet\n")

    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(vor_aufruf=spaet), heute=HEUTE, jetzt=JETZT) == 4
    assert (pc / "Projects/p.md").read_text(encoding="utf-8") == "pc-spaet\n"
    assert zustand.read_bytes() == vorher


# --- Folgerunde: Konfliktdateien loeschbar, Tagesgrenze, offene Meldungen, Grenzen, Basis robust ----------


def test_konfliktdatei_erbt_den_besitzer_des_originals():
    assert ws.besitzer("People/User (user) (Konflikt 2026-10-25 14-03).md") == "pc"
    assert ws.besitzer("People/Max (Konflikt 2026-10-25 14-03).md") == "vm"
    assert ws.besitzer("Projects/a (Konflikt 2026-10-25 14-03 2).md") == "pc"
    assert ws.besitzer("Notes/n (Konflikt 2026-10-25 14-03)") == "vm"
    assert ws.besitzer(".bubble_sync_hashes (Konflikt 2026-10-25 14-03).json") == "pc"
    assert ws.ist_konfliktdatei("Notes/n (Konflikt 2026-10-25 14-03).md")
    assert not ws.ist_konfliktdatei("Notes/n (Konflikt).md")


def test_konfliktdatei_darf_auf_beiden_seiten_geloescht_werden():
    k = {"Projects/a (Konflikt 2026-10-25 14-03).md": e("A")}  # PC-Besitz
    assert ws.abgleich_planen(k, {}, k) == [Aktion("loeschen_pc", "Projects/a (Konflikt 2026-10-25 14-03).md")]  # VM loescht
    assert ws.abgleich_planen({}, k, k) == [Aktion("loeschen_vm", "Projects/a (Konflikt 2026-10-25 14-03).md")]  # PC loescht
    # inzwischen geaendert: nichts geht verloren, die Aenderung wird zurueckgespielt bzw. gesichert
    assert ws.abgleich_planen({"Projects/a (Konflikt 2026-10-25 14-03).md": e("B")}, {}, k) == [Aktion("nach_vm", "Projects/a (Konflikt 2026-10-25 14-03).md")]
    n = {"Notes/n (Konflikt 2026-10-25 14-03).md": e("A")}  # VM-Besitz, PC loescht
    assert ws.abgleich_planen({}, n, n) == [Aktion("loeschen_vm", "Notes/n (Konflikt 2026-10-25 14-03).md")]


def test_konfliktdatei_loeschen_am_lauf(seiten, meldungen):
    pc, vm, zustand = seiten
    _unveraendert(pc, vm)
    _gemeinsamer_start(pc, vm, zustand, "Projects/p.md")
    schreibe(pc, "Projects/p.md", TEXT.replace("Zeile 3\n", "PC\n"))
    schreibe(vm, "Projects/p.md", TEXT.replace("Zeile 3\n", "VM\n"))
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=JETZT) == 0
    k = "Projects/p (Konflikt 2026-10-25 14-03).md"
    (vm / k).unlink()  # Nicht-Besitzer raeumt die Konfliktdatei weg
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=JETZT) == 0
    assert not (pc / k).exists() and not (vm / k).exists()


def test_hoechstens_eine_konfliktdatei_je_pfad_und_tag(seiten, meldungen):
    pc, vm, zustand = seiten
    _gemeinsamer_start(pc, vm, zustand, "Projects/p.md")
    schreibe(pc, "Projects/p.md", TEXT.replace("Zeile 3\n", "PC\n"))
    schreibe(vm, "Projects/p.md", TEXT.replace("Zeile 3\n", "VM\n"))
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=JETZT) == 0
    schreibe(pc, "Projects/p.md", TEXT.replace("Zeile 3\n", "PC 2\n"))
    schreibe(vm, "Projects/p.md", TEXT.replace("Zeile 3\n", "VM 2\n"))
    spaeter = datetime(2026, 10, 25, 18, 30)
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=spaeter) == 0
    assert [n for n in dateien(pc) if "Konflikt" in n] == ["Projects/p (Konflikt 2026-10-25 14-03).md"]
    assert len(meldungen) == 1
    assert (vm / "Projects/p.md").read_text(encoding="utf-8") == TEXT.replace("Zeile 3\n", "PC 2\n")
    k = vm / "_konflikte" / HEUTE
    assert (k / "Projects/p.md").read_text(encoding="utf-8") == TEXT.replace("Zeile 3\n", "VM 2\n")
    assert (k / "Projects/p.md.1").read_text(encoding="utf-8") == TEXT.replace("Zeile 3\n", "VM\n")


def test_offene_meldung_wird_nach_dem_naechsten_erfolg_gesendet(seiten, monkeypatch):
    pc, vm, zustand = seiten
    gesendet: list[str] = []
    kaputt = {"an": True}

    def melden(text):
        if kaputt["an"]:
            raise OSError("netz weg")
        gesendet.append(text)

    monkeypatch.setattr(ws, "_telegram_melden", melden)
    _gemeinsamer_start(pc, vm, zustand, "Projects/p.md")
    schreibe(pc, "Projects/p.md", TEXT.replace("Zeile 3\n", "PC\n"))
    schreibe(vm, "Projects/p.md", TEXT.replace("Zeile 3\n", "VM\n"))
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=JETZT) == 0
    offen = zustand.parent / ws.MELDUNGEN_OFFEN
    assert offen.exists() and gesendet == []
    kaputt["an"] = False
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=JETZT) == 0
    assert len(gesendet) == 1 and "Projects/p.md" in gesendet[0]
    assert not offen.exists()


def test_grosse_datei_wird_nicht_zusammengefuehrt(seiten, meldungen):
    pc, vm, zustand = seiten
    viele = "".join(f"Zeile {i}\n" for i in range(6000))  # > 5000 Zeilen
    _gemeinsamer_start(pc, vm, zustand, "Projects/gross.md", viele)
    schreibe(pc, "Projects/gross.md", viele.replace("Zeile 1\n", "PC\n"))
    schreibe(vm, "Projects/gross.md", viele.replace("Zeile 5000\n", "VM\n"))
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=JETZT) == 0
    assert (vm / "Projects/gross.md").read_text(encoding="utf-8") == viele.replace("Zeile 1\n", "PC\n")
    assert (vm / "_konflikte" / HEUTE / "Projects/gross.md").exists()
    assert not any("Konflikt" in n for n in dateien(pc)) and meldungen == []


def test_zu_viele_bytes_werden_nicht_zusammengefuehrt():
    gross = ("x" * 1000 + "\n").encode() * 300  # ~300 KB, nur 300 Zeilen
    plan = [Aktion("konflikt_sichern", "Projects/g.md"), Aktion("nach_vm", "Projects/g.md")]
    neu, inhalte, konflikte = ws.zusammenfuehren_planen(
        plan, {"Projects/g.md": (gross, b"a\n" + gross, gross + b"b\n")}, set(), JETZT)
    assert neu == plan and inhalte == {} and konflikte == []


def test_json_wird_nicht_zusammengefuehrt(seiten, meldungen):
    pc, vm, zustand = seiten
    alt = '{\n"a": 1,\n"b": 2,\n"c": 3,\n"d": 4\n}\n'
    _gemeinsamer_start(pc, vm, zustand, "Projects/x.json", alt)
    schreibe(pc, "Projects/x.json", alt.replace('"a": 1', '"a": 9'))
    schreibe(vm, "Projects/x.json", alt.replace('"d": 4', '"d": 9'))
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=FakeVm(), heute=HEUTE, jetzt=JETZT) == 0
    assert (vm / "Projects/x.json").read_text(encoding="utf-8") == alt.replace('"a": 1', '"a": 9')
    assert (vm / "_konflikte" / HEUTE / "Projects/x.json").exists()
    assert meldungen == []


def test_basis_aktualisieren_raeumt_zuerst_auf_und_ist_robust(tmp_path, monkeypatch):
    pc, basis = tmp_path / "pc", tmp_path / "basis"
    for name, inhalt in (("Projects/a.md", "a\n"), ("Projects/b.md", "b\n")):
        schreibe(pc, name, inhalt)
    schreibe(basis, "Projects/weg.md", "alt\n")   # Pfad gibt es nicht mehr
    schreibe(basis, "projects/B.md", "b\n")        # gleiche Datei unter anderer Schreibweise
    stand = {"Projects/a.md": e(sha("a\n")), "Projects/b.md": e(sha("b\n"))}
    echt = ws._atomar_schreiben

    def zickig(ziel, daten):
        if ziel.name == "a.md":
            raise PermissionError("gesperrt")
        echt(ziel, daten)

    monkeypatch.setattr(ws, "_atomar_schreiben", zickig)
    ws.basis_aktualisieren(basis, stand, pc, lambda p: [p], set())  # wirft nicht
    assert not (basis / "Projects/weg.md").exists()
    assert (basis / "projects/B.md").exists()  # Gross-/Kleinschreibung: nicht als veraltet geloescht
    assert (basis / "Projects/b.md").read_text() == "b\n"
    assert not (basis / "Projects/a.md").exists()


def test_lange_pfade():
    assert not ws._windows_tauglich("Notes/" + "x" * 240 + ".md")
    assert ws._windows_tauglich("Notes/" + "x" * 200 + ".md")
    lang = "Projects/" + "y" * 228 + ".md"  # 240 Zeichen
    assert ws._windows_tauglich(lang)
    k = ws.konfliktname(lang, JETZT, set())
    assert len(k) <= 240 and ws._windows_tauglich(k) and k.endswith(" (Konflikt 2026-10-25 14-03).md")
    assert ws.ist_konfliktdatei(k) and ws.besitzer(k) == "pc"
