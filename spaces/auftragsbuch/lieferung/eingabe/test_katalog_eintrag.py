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


def _katalog_roh() -> list[dict]:
    return yaml.safe_load(KATALOG.read_text(encoding="utf-8"))


def _katalog() -> list[dict]:
    return _katalog_roh()


def _trifft(eintrag: dict, satz: str) -> bool:
    return any(re.search(m, satz, re.I) for m in eintrag.get("match_patterns") or [])


def test_eintrag_ist_neu_und_hat_pflichtfelder():
    e = _eintrag()
    assert e["capability"] == "rowboat_vorschlaege_anlegen"
    # Noch nicht eingebaut: der Name ist frei. Schon eingebaut: der Eintrag im Katalog ist der gelieferte.
    vorhanden = [c for c in _katalog() if c["capability"] == e["capability"]]
    assert len(vorhanden) <= 1
    for c in vorhanden:
        for feld in ("execution_target", "match_patterns", "validator"):
            assert c.get(feld) == e.get(feld), feld
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
        andere = [c["capability"] for c in _katalog()
                  if c["capability"] != "rowboat_vorschlaege_anlegen" and _trifft(c, satz)]
        assert andere == [], (satz, andere)


# --- Fix-Runde 1: Die Tests duerfen nicht rot werden, sobald der Eintrag eingebaut ist ---

def test_eingebauter_eintrag_macht_die_tests_nicht_rot(monkeypatch):
    import sys
    modul = sys.modules[__name__]
    eingebaut = _katalog() + [_eintrag()]
    monkeypatch.setattr(modul, "_katalog", lambda: eingebaut)
    test_eintrag_ist_neu_und_hat_pflichtfelder()
    test_kein_anderer_katalog_eintrag_trifft_die_saetze()


def test_eingebauter_eintrag_der_abweicht_wird_gemeldet(monkeypatch):
    import sys
    import pytest
    modul = sys.modules[__name__]
    abweichend = dict(_eintrag(), execution_target="mcp:anderer:auftragsbuch:x")
    monkeypatch.setattr(modul, "_katalog", lambda: _katalog_roh() + [abweichend])
    with pytest.raises(AssertionError):
        test_eintrag_ist_neu_und_hat_pflichtfelder()


def test_schnipsel_nutzt_aus_satz_und_plan_intent():
    text = LIEFERUNG.read_text(encoding="utf-8")
    assert "_vorschlaege_aus_satz(plan_ctx.get(\"plan_intent\", \"\"))" in text
    assert "```yaml\n  rowboat_vorschlaege_anlegen: external" in text
