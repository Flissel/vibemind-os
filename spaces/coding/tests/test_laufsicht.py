"""Laufsicht: Protokoll und Aufgaben eines Laufs, nur gelesen, nur unter der Arbeitswurzel."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spaces.coding import dienst, laufsicht, pipeline


@pytest.fixture(autouse=True)
def _ablage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CODING_PIPELINE_DIR", str(tmp_path / "auftraege"))
    monkeypatch.setenv("CODING_WORKSPACE_ROOT", str(tmp_path / "arbeit"))
    return tmp_path


def _auftrag(auftrag_id: str = "c-test-0001", projekt: Path | None = None, art: str = "engine") -> Path:
    projekt = projekt or pipeline.arbeitswurzel() / auftrag_id
    (projekt / "out").mkdir(parents=True, exist_ok=True)
    (projekt / "tasks").mkdir(exist_ok=True)
    daten = {
        "id": auftrag_id, "art": art, "titel": "Probe", "angelegt": 1.0, "zustand": "wartet", "stufe": "plan",
        "projektordner": str(projekt), "zielordner": str(projekt / "out"),
        "pakete": [{"paket": "AP-01", "titel": "Rechner reparieren", "epic": "EPIC-001"}],
    }
    (pipeline.auftragsordner() / f"{auftrag_id}.json").write_text(json.dumps(daten), encoding="utf-8")
    return projekt


def _aufgaben(projekt: Path, *zustaende: str) -> None:
    arten = ["api_service", "test_unit", "verify_unit"]
    aufgaben = [{"id": f"EPIC-001-{i}", "type": arten[i % 3], "title": "AP-01 Rechner reparieren", "status": z,
                 "output_files": [f"src/datei{i}.js"], "command": "node --test" if i % 3 == 2 else None,
                 "error_message": "api_key=geheim123456 kaputt" if z == "failed" else None, "retry_count": 1}
                for i, z in enumerate(zustaende)]
    (projekt / "tasks" / "epic-001-tasks.json").write_text(json.dumps({"tasks": aufgaben}), encoding="utf-8")


def test_protokoll_ende_und_weiterlesen() -> None:
    projekt = _auftrag()
    (projekt / "lauf.log").write_bytes(b"erste Zeile\nzweite Zeile\n")
    erst = laufsicht.protokoll("c-test-0001")
    assert erst["text"] == "erste Zeile\nzweite Zeile\n" and erst["neu"] is True
    assert erst["weiter"] == erst["groesse"] == 25
    with open(projekt / "lauf.log", "ab") as f:
        f.write(b"dritte Zeile\n")
    dann = laufsicht.protokoll("c-test-0001", ab=erst["weiter"])
    assert dann["text"] == "dritte Zeile\n" and dann["neu"] is False and dann["weiter"] == 38


def test_protokoll_kuerzer_geworden_beginnt_neu() -> None:
    projekt = _auftrag()
    (projekt / "lauf.log").write_bytes(b"neu\n")
    antwort = laufsicht.protokoll("c-test-0001", ab=500)
    assert antwort["neu"] is True and antwort["text"] == "neu\n"


def test_protokoll_grenze_schneidet_an_zeilenanfang() -> None:
    projekt = _auftrag()
    (projekt / "lauf.log").write_bytes(b"".join(f"zeile {i}\n".encode() for i in range(200)))
    antwort = laufsicht.protokoll("c-test-0001", grenze=100)
    assert antwort["gekuerzt"] is True
    assert antwort["text"].startswith("zeile ") and antwort["text"].endswith("zeile 199\n")
    assert len(antwort["text"].encode()) <= 100


def test_protokoll_schwaerzt_schluessel() -> None:
    projekt = _auftrag()
    (projekt / "lauf.log").write_text(
        "Authorization: Bearer abcdefghijklmnop\nOPENFANG_API_KEY=xyz9876543\nsk-ant-abcdefghijklmnopq\n",
        encoding="utf-8")
    text = laufsicht.protokoll("c-test-0001")["text"]
    for geheim in ("abcdefghijklmnop", "xyz9876543", "abcdefghijklmnopq"):
        assert geheim not in text
    assert text.count("•••") == 3


def test_ohne_protokoll_und_ohne_engine() -> None:
    _auftrag()
    assert laufsicht.protokoll("c-test-0001")["text"] == ""
    _auftrag("c-test-0002", art="klein")
    assert "kein Protokoll" in laufsicht.protokoll("c-test-0002")["satz"]
    assert laufsicht.aufgaben("c-test-0002")["pakete"] == []


def test_projektordner_ausserhalb_der_wurzel_wird_nie_gelesen(tmp_path: Path) -> None:
    draussen = tmp_path / "draussen"
    _auftrag(projekt=draussen)
    (draussen / "lauf.log").write_text("geheim\n", encoding="utf-8")
    assert laufsicht.protokoll("c-test-0001")["text"] == ""
    assert laufsicht.aufgaben("c-test-0001")["gesamt"] == 0


def test_aufgaben_zaehlen_und_ohne_kennung() -> None:
    projekt = _auftrag()
    _aufgaben(projekt, "completed", "running", "pending", "failed")
    daten = laufsicht.aufgaben("c-test-0001")
    assert (daten["gesamt"], daten["fertig"], daten["laeuft"], daten["fehlgeschlagen"]) == (4, 1, 1, 1)
    assert daten["anteil"] == 25
    paket = daten["pakete"][0]
    assert paket["name"] == "Rechner reparieren"
    assert [a["art"] for a in paket["aufgaben"]] == ["Code schreiben", "Tests schreiben", "Prüfen", "Code schreiben"]
    assert "AP-01" not in json.dumps(daten)
    assert "geheim123456" not in json.dumps(daten)


def test_dienst_liefert_beide_wege() -> None:
    projekt = _auftrag()
    (projekt / "lauf.log").write_bytes(b"hallo\n")
    _aufgaben(projekt, "completed")
    status, _, koerper = dienst.lesen("/api/protokoll?auftrag=c-test-0001&ab=0")
    assert status == 200 and json.loads(koerper)["text"] == "hallo\n"
    status, _, koerper = dienst.lesen("/api/aufgaben?auftrag=c-test-0001")
    assert status == 200 and json.loads(koerper)["anteil"] == 100
    assert dienst.lesen("/api/protokoll?auftrag=c-test-0001&ab=x")[0] == 400
    assert dienst.lesen("/api/protokoll?auftrag=../../etwas")[0] in (400, 404)
    assert dienst.lesen("/vm-chat.js")[0] == 200
