"""Tests fuer die Ablage: Bestand, Plan und Ausfuehren in Paketen (Teil B, Task 8)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "geschaeftsdaten"))

from ablage import Ist, Plan, Soll, Takt, aufraeumen, ausfuehren, bestand, planen  # noqa: E402


def _id(n: int) -> str:
    return f"aaaaaaaa-0000-4000-8000-{n:012d}"


def _inhalt(schluessel: str, text: str = "Text") -> str:
    return f"---\nerzeugt_von: rowboat-geschaeftsdaten\nschluessel: {schluessel}\n---\n\n{text}\n"


def _archiv(ist: Ist) -> str:
    return _inhalt(ist.schluessel, "archiviert")


def _lauf(firma: Path, soll: list[Soll], gesperrt: set[str] = frozenset(), **kw) -> dict[str, int]:
    plan = planen(soll, bestand(firma), set(gesperrt), _archiv)
    return ausfuehren(plan, firma, **kw)


def _leer(plan: Plan) -> bool:
    return not (plan.schreiben or plan.verschieben or plan.archivieren or plan.entfernen)


def _schreiben(firma: Path, rel: str, text: str) -> Path:
    p = firma / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="")
    return p


def test_zweiter_lauf_schreibt_nichts(tmp_path):
    soll = [Soll(f"Kontakte/bAV/K{i}.md", _id(i), _inhalt(_id(i))) for i in range(5)]
    soll += [Soll("Newsletter/2026-10-25 N.md", "nl:x", _inhalt("nl:x")),
             Soll("Wochenbild/2026-KW43.md", "kw:2026-W43", _inhalt("kw:2026-W43"))]
    z = _lauf(tmp_path, soll)
    assert z["geschrieben"] == 7
    plan = planen(soll, bestand(tmp_path), set(), _archiv)
    assert _leer(plan) and plan.unveraendert == 7
    z2 = ausfuehren(plan, tmp_path)
    assert z2["geschrieben"] == 0 and z2["unveraendert"] == 7


def test_bestand_liest_nur_eigene(tmp_path):
    _schreiben(tmp_path, "Kontakte/bAV/A.md", _inhalt(_id(1)))
    _schreiben(tmp_path, "Kontakte/_archiv/B.md", _inhalt(_id(2)))
    _schreiben(tmp_path, "Newsletter/N.md", _inhalt("nl:1"))
    _schreiben(tmp_path, "Newsletter/tief/X.md", _inhalt("nl:2"))  # nur oberste Ebene
    _schreiben(tmp_path, "People/P.md", _inhalt(_id(3)))  # anderer Ordner
    _schreiben(tmp_path, "Kontakte/bAV/kaputt.md", "erzeugt_von: rowboat-geschaeftsdaten\n")
    ist = {i.rel: i.schluessel for i in bestand(tmp_path)}
    assert ist == {"Kontakte/bAV/A.md": _id(1), "Kontakte/_archiv/B.md": _id(2), "Newsletter/N.md": "nl:1"}


def test_geaenderter_inhalt_wird_atomar_ersetzt(tmp_path):
    _lauf(tmp_path, [Soll("Kontakte/bAV/A.md", _id(1), _inhalt(_id(1), "alt"))])
    z = _lauf(tmp_path, [Soll("Kontakte/bAV/A.md", _id(1), _inhalt(_id(1), "neu"))])
    assert z["geschrieben"] == 1
    assert (tmp_path / "Kontakte/bAV/A.md").read_text(encoding="utf-8").endswith("neu\n")
    assert not list(tmp_path.rglob("*.gd.tmp"))


def test_kategorie_wechsel_verschiebt(tmp_path):
    _lauf(tmp_path, [Soll("Kontakte/bAV/A.md", _id(1), _inhalt(_id(1), "alt"))])
    z = _lauf(tmp_path, [Soll("Kontakte/IT-Lösung/A.md", _id(1), _inhalt(_id(1), "neu"))])
    assert z["verschoben"] == 1
    assert not (tmp_path / "Kontakte/bAV/A.md").exists()
    assert (tmp_path / "Kontakte/IT-Lösung/A.md").read_text(encoding="utf-8").endswith("neu\n")


def test_verschwundener_kontakt_ins_archiv(tmp_path):
    _lauf(tmp_path, [Soll("Kontakte/bAV/Anna.md", _id(1), _inhalt(_id(1)))])
    z = _lauf(tmp_path, [])
    assert z["archiviert"] == 1
    assert not (tmp_path / "Kontakte/bAV/Anna.md").exists()
    assert (tmp_path / "Kontakte/_archiv/Anna.md").read_text(encoding="utf-8").endswith("archiviert\n")
    plan = planen([], bestand(tmp_path), set(), _archiv)
    assert _leer(plan) and plan.unveraendert == 1


def test_archivname_kollidiert_bekommt_id8(tmp_path):
    _schreiben(tmp_path, "Kontakte/_archiv/Anna.md", _inhalt(_id(2)))
    _schreiben(tmp_path, "Kontakte/bAV/Anna.md", _inhalt(_id(1)))
    plan = planen([], bestand(tmp_path), set(), _archiv)
    assert [(i.rel, ziel) for i, ziel in plan.archivieren] == [
        ("Kontakte/bAV/Anna.md", f"Kontakte/_archiv/Anna ({_id(1)[:8]}).md")]
    ausfuehren(plan, tmp_path)
    assert (tmp_path / f"Kontakte/_archiv/Anna ({_id(1)[:8]}).md").is_file()
    assert (tmp_path / "Kontakte/_archiv/Anna.md").read_text(encoding="utf-8") == _inhalt(_id(2))


def test_gesperrt_entfernt_auch_aus_archiv(tmp_path):
    _schreiben(tmp_path, "Kontakte/bAV/Geheim.md", _inhalt(_id(1)))
    _schreiben(tmp_path, "Kontakte/_archiv/Geheim alt.md", _inhalt(_id(1)))
    z = _lauf(tmp_path, [], gesperrt={_id(1)})
    assert z["entfernt"] == 2 and z["archiviert"] == 0
    assert not list((tmp_path / "Kontakte").rglob("*.md"))


def test_gesperrt_fremde_datei_bleibt(tmp_path):
    fremd = _schreiben(tmp_path, "Kontakte/bAV/Notiz.md", f"---\nschluessel: {_id(1)}\n---\nmeins\n")
    _lauf(tmp_path, [], gesperrt={_id(1)})
    assert fremd.read_text(encoding="utf-8").endswith("meins\n")


def test_eigene_doppel_werden_entfernt(tmp_path):
    _schreiben(tmp_path, "Kontakte/bAV/A.md", _inhalt(_id(1)))
    _schreiben(tmp_path, "Kontakte/_ohne/A alt.md", _inhalt(_id(1)))
    plan = planen([Soll("Kontakte/bAV/A.md", _id(1), _inhalt(_id(1)))], bestand(tmp_path), set(), _archiv)
    assert [i.rel for i in plan.entfernen] == ["Kontakte/_ohne/A alt.md"] and plan.unveraendert == 1


def test_fremde_dateien_unberuehrt(tmp_path):
    fremde = {
        "Kontakte/bAV/Notiz.md": b"# Notiz vom Betreiber\n",
        "Newsletter/eigen.md": b"---\nerzeugt_von: jemand-anderes\nschluessel: nl:1\n---\nx\n",
        "Kontakte/bAV/bild.png": b"\x89PNG\r\n\x1a\n",
    }
    for rel, daten in fremde.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(daten)
    ist = bestand(tmp_path)
    assert ist == []
    soll = [Soll("Kontakte/IT-Lösung/A.md", _id(1), _inhalt(_id(1)))]
    plan = planen(soll, ist, {"nl:1"}, _archiv)
    beruehrt = {s.rel for s in plan.schreiben} | {i.rel for i, _ in plan.verschieben} \
        | {i.rel for i, _ in plan.archivieren} | {i.rel for i in plan.entfernen}
    assert not beruehrt & set(fremde)
    ausfuehren(plan, tmp_path)
    for rel, daten in fremde.items():
        assert (tmp_path / rel).read_bytes() == daten


def test_fremde_datei_am_ziel_wird_nicht_ueberschrieben(tmp_path):
    fremd = _schreiben(tmp_path, "Kontakte/bAV/A.md", "# meins\n")
    z = _lauf(tmp_path, [Soll("Kontakte/bAV/A.md", _id(1), _inhalt(_id(1)))])
    assert z["abweichung"] == 1 and z["geschrieben"] == 0
    assert fremd.read_text(encoding="utf-8") == "# meins\n"


def test_hoechstens_15_loeschungen_je_lauf_rest_spaeter(tmp_path):
    alt = [Soll(f"Kontakte/bAV/K{i:02d}.md", _id(i), _inhalt(_id(i))) for i in range(40)]
    _lauf(tmp_path, alt)
    neu = [Soll(f"Kontakte/IT-Lösung/K{i:02d}.md", _id(i), _inhalt(_id(i))) for i in range(40)]
    import ablage
    original, loeschungen = ablage._loeschen, []

    def zaehlend(pfad: Path) -> None:
        loeschungen.append(pfad.name)
        original(pfad)

    ablage._loeschen = zaehlend
    try:
        laeufe = [_lauf(tmp_path, neu) for _ in range(3)]
    finally:
        ablage._loeschen = original
    assert [z["verschoben"] for z in laeufe] == [15, 15, 10]
    assert [z["zurueckgestellt"] for z in laeufe] == [25, 10, 0]
    assert len(loeschungen) == 40
    # stabile Reihenfolge: zuerst K00..K14, dann K15..K29
    assert loeschungen[:15] == [f"K{i:02d}.md" for i in range(15)]
    assert not list((tmp_path / "Kontakte/bAV").glob("*.md"))
    assert len(list((tmp_path / "Kontakte/IT-Lösung").glob("*.md"))) == 40


def test_obergrenze_gilt_ueber_mehrere_firmen(tmp_path):
    takt = Takt(paket=15)
    zaehler = []
    for firma in ("A", "B"):
        ordner = tmp_path / firma
        _lauf(ordner, [Soll(f"Kontakte/bAV/K{i}.md", _id(i), _inhalt(_id(i))) for i in range(10)])
        plan = planen([], bestand(ordner), set(), _archiv)
        zaehler.append(ausfuehren(plan, ordner, takt=takt))
    assert [z["archiviert"] for z in zaehler] == [10, 5]
    assert zaehler[1]["zurueckgestellt"] == 5


def test_neuer_kontakt_auf_pfad_eines_zurueckgestellten_wartet(tmp_path):
    # Anna (id1) verschwindet (Archiv zurueckgestellt), ein neuer Kontakt (id2) heisst ebenfalls Anna.
    _lauf(tmp_path, [Soll("Kontakte/bAV/Anna.md", _id(1), _inhalt(_id(1)))])
    plan = planen([Soll("Kontakte/bAV/Anna.md", _id(2), _inhalt(_id(2)))], bestand(tmp_path), set(), _archiv)
    voll = Takt(paket=1)
    assert voll.nimm()
    z = ausfuehren(plan, tmp_path, takt=voll)
    assert z["zurueckgestellt"] == 2 and z["geschrieben"] == 0
    assert (tmp_path / "Kontakte/bAV/Anna.md").read_text(encoding="utf-8") == _inhalt(_id(1))


def test_namenstausch_ohne_falsche_abweichung(tmp_path):
    _lauf(tmp_path, [Soll("Kontakte/bAV/A.md", _id(1), _inhalt(_id(1), "eins")),
                     Soll("Kontakte/bAV/B.md", _id(2), _inhalt(_id(2), "zwei"))])
    z = _lauf(tmp_path, [Soll("Kontakte/bAV/B.md", _id(1), _inhalt(_id(1), "eins neu")),
                         Soll("Kontakte/bAV/A.md", _id(2), _inhalt(_id(2), "zwei neu"))])
    assert z["verschoben"] == 2 and z["abweichung"] == 0
    assert (tmp_path / "Kontakte/bAV/B.md").read_text(encoding="utf-8").endswith("eins neu\n")
    assert (tmp_path / "Kontakte/bAV/A.md").read_text(encoding="utf-8").endswith("zwei neu\n")
    assert not list(tmp_path.rglob("*.gd.tmp"))
    plan = planen([Soll("Kontakte/bAV/B.md", _id(1), _inhalt(_id(1), "eins neu")),
                   Soll("Kontakte/bAV/A.md", _id(2), _inhalt(_id(2), "zwei neu"))], bestand(tmp_path), set(), _archiv)
    assert _leer(plan)


def test_aufraeumen_entfernt_nur_eigene_zwischendateien(tmp_path):
    _schreiben(tmp_path, "Kontakte/bAV/.A.md.gd.tmp", "x")
    _schreiben(tmp_path, "Kontakte/bAV/.B.md.tausch.gd.tmp", "x")
    _schreiben(tmp_path, "Newsletter/.N.md.gd.tmp", "x")
    _schreiben(tmp_path, "Kontakte/bAV/fremd.gd.tmp", "x")  # ohne Punkt vorne: nicht unseres
    _schreiben(tmp_path, "People/.P.md.gd.tmp", "x")  # anderer Ordner
    assert aufraeumen(tmp_path) == 3
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.tmp")) == [
        "Kontakte/bAV/fremd.gd.tmp", "People/.P.md.gd.tmp"]


def test_abweichung_quelle_geaendert_bleibt_liegen(tmp_path):
    _lauf(tmp_path, [Soll("Kontakte/bAV/A.md", _id(1), _inhalt(_id(1), "alt"))])
    plan = planen([Soll("Kontakte/IT-Lösung/A.md", _id(1), _inhalt(_id(1), "neu"))], bestand(tmp_path), set(),
                  _archiv)
    _schreiben(tmp_path, "Kontakte/bAV/A.md", _inhalt(_id(1), "zwischendurch geaendert"))
    z = ausfuehren(plan, tmp_path)
    assert z["abweichung"] == 1 and z["verschoben"] == 0
    assert (tmp_path / "Kontakte/IT-Lösung/A.md").read_text(encoding="utf-8").endswith("neu\n")
    assert (tmp_path / "Kontakte/bAV/A.md").is_file()


def test_trocken_schreibt_nichts(tmp_path):
    _schreiben(tmp_path, "Kontakte/bAV/Alt.md", _inhalt(_id(9)))
    vorher = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    z = _lauf(tmp_path, [Soll(f"Kontakte/bAV/K{i}.md", _id(i), _inhalt(_id(i))) for i in range(3)],
              trocken=True)
    assert z["geschrieben"] == 3 and z["archiviert"] == 1
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == vorher


def test_newsletter_ohne_soll_bleibt(tmp_path):
    _schreiben(tmp_path, "Newsletter/2026-10-25 N.md", _inhalt("nl:1"))
    _schreiben(tmp_path, "Wochenbild/2026-KW42.md", _inhalt("kw:2026-W42"))
    plan = planen([], bestand(tmp_path), set(), _archiv)
    assert _leer(plan) and plan.unveraendert == 2


def test_symlink_wird_nicht_verfolgt(tmp_path):
    ziel = tmp_path / "anderswo"
    ziel.mkdir()
    (ziel / "X.md").write_text(_inhalt(_id(1)), encoding="utf-8")
    try:
        (tmp_path / "firma" / "Kontakte").mkdir(parents=True)
        (tmp_path / "firma" / "Kontakte" / "bAV").symlink_to(ziel, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("keine Symlink-Rechte")
    assert bestand(tmp_path / "firma") == []
    z = _lauf(tmp_path / "firma", [Soll("Kontakte/bAV/Y.md", _id(2), _inhalt(_id(2)))])
    assert z["abweichung"] == 1 and not (ziel / "Y.md").exists()


def test_doppelte_soll_pfade_werfen():
    with pytest.raises(ValueError):
        planen([Soll("Kontakte/bAV/A.md", _id(1), "x"), Soll("Kontakte/bAV/a.md", _id(2), "y")], [], set(), _archiv)
