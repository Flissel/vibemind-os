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
