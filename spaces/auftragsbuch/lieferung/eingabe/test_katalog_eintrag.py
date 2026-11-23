"""Der Katalog-Eintrag aus lieferung/block-6-eingabe.md passt zum echten Katalog und zum Verzeichnis-Vorschlag."""
import re
from pathlib import Path

import yaml

HIER = Path(__file__).resolve().parent
WURZEL = HIER.parents[3]
LIEFERUNG = HIER.parent / "block-6-eingabe.md"
KATALOG = WURZEL / "brain" / "the_brain" / "data" / "capabilities.yaml"
ZIEL = "mcp:brain-rowboat:auftragsbuch:rowboat_vorschlaege_anlegen"

TREFFER = [
    "Leg mir Milch kaufen und Steuer machen auf die To-do-Liste",
    "Setz Oma anrufen auf meine Liste",
    "Schreib Zahnarzt, Auto waschen auf die Aufgabenliste",
    "Lege mir 1. Brot 2. Käse auf die Todo-Liste",
    "Put buy milk to my to-do list",
]
KEINE_TREFFER = [
    "Schreib eine Mail an Oma",
    "Setz einen Timer auf 5 Minuten",
    "Lege eine neue Idee an",
    "Zeig mir meine To-do-Liste",
    "Rowboat Status",
    "Setz die Zeit auf meine Liste der Ideen zurueck",
]


def _eintrag() -> dict:
    bloecke = re.findall(r"```yaml\n(.*?)```", LIEFERUNG.read_text(encoding="utf-8"), flags=re.S)
    eintraege = [yaml.safe_load(b) for b in bloecke if b.lstrip().startswith("- capability:")]
    assert len(eintraege) == 1
    return eintraege[0][0]


def _katalog() -> list[dict]:
    return yaml.safe_load(KATALOG.read_text(encoding="utf-8"))


def _trifft(eintrag: dict, satz: str) -> bool:
    return any(re.search(m, satz, re.I) for m in eintrag.get("match_patterns") or [])


def test_eintrag_ist_neu_und_hat_pflichtfelder():
    e = _eintrag()
    assert e["capability"] == "rowboat_vorschlaege_anlegen"
    assert e["capability"] not in {c["capability"] for c in _katalog()}
    assert e["execution_target"] == ZIEL
    assert e["match_patterns"] and e["anchor_phrases"] and e["validator"]["on_fail"] == "block"
    assert len(e["anchor_phrases"]) == 3


def test_ziel_passt_zu_block_1():
    block1 = (HIER.parent / "block-1-rechtetraeger.md").read_text(encoding="utf-8")
    assert ZIEL in block1


def test_muster_treffen_nur_die_richtigen_saetze():
    e = _eintrag()
    for satz in TREFFER:
        assert _trifft(e, satz), satz
    for satz in KEINE_TREFFER:
        assert not _trifft(e, satz), satz


def test_kein_anderer_katalog_eintrag_trifft_die_saetze():
    for satz in TREFFER:
        andere = [c["capability"] for c in _katalog() if _trifft(c, satz)]
        assert andere == [], (satz, andere)
