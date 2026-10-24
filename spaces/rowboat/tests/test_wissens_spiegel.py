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
import shlex  # noqa: E402
import subprocess  # noqa: E402
import tarfile  # noqa: E402

ZIEL = "offload-vm"
HEUTE = "2026-10-24"


class FakeVm:
    """Ersetzt `subprocess.run` fuer `ssh offload-vm ...` und fuehrt die Fernbefehle lokal aus.

    Der Fernbefehl wird wie von der entfernten Shell zerlegt (shlex), damit falsches Quoten auffaellt.
    `python3 -` laeuft mit dem lokalen Interpreter; `tar` wird mit `tarfile` nachgebildet
    (GNU-Optionen `-C`, `-xf -`, `-cf - --null --verbatim-files-from --no-recursion -T -`).
    """

    def __init__(self, fehler_bei: int | None = None, ausnahme: BaseException | None = None):
        self.aufrufe: list[tuple[list[str], dict]] = []
        self.fehler_bei = fehler_bei  # 1-basierter Aufruf, der scheitert
        self.ausnahme = ausnahme

    def __call__(self, cmd, **kw):
        self.aufrufe.append((list(cmd), kw))
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
                        ziel.write_bytes(tf.extractfile(m).read())
            elif befehl[0] == "tar" and "-cf" in befehl:
                assert {"--null", "--verbatim-files-from", "--no-recursion", "-T"} <= set(befehl)
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
    for weg in ("_konflikte/2026-10-01/x.md", "Notes/.DS_Store", "Thumbs.db", "Notes/desktop.ini",
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
    # Aufruf 1 = Bestand, 2 = Konfliktsicherung, 3 = Upload (scheitert), 4 = Download
    fake = FakeVm(fehler_bei=3)
    assert ws.lauf(pc, ZIEL, str(vm), zustand, laeufer=fake, heute=HEUTE) == 2
    assert len(fake.aufrufe) == 3
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
