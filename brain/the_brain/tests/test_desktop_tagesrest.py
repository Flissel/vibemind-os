import json
from datetime import datetime

from core.desktop_tagesrest import herkunft_aus_kontext, tagesrest

JETZT = datetime(2026, 11, 23, 12, 0, 0)


def _aufbau(tmp_path, grenze=30, zeilen=()):
    config = tmp_path / "agent_budget.yaml"
    config.write_text(
        "agenten:\n  brain-desktop: {klasse: auftrag, tagesgrenze: %s}\n" % grenze, encoding="utf-8")
    ordner = tmp_path / "budget"
    ordner.mkdir()
    if zeilen:
        (ordner / "2026-11-23.jsonl").write_text(
            "\n".join(z if isinstance(z, str) else json.dumps(z) for z in zeilen), encoding="utf-8")
    return {"VIBEMIND_BUDGET_CONFIG": str(config), "VIBEMIND_BUDGET_DIR": str(ordner)}


def _lauf(agent="brain-desktop", erlaubt=True, art="pruefung"):
    return {"art": art, "agent": agent, "erlaubt": erlaubt}


def test_ohne_angaben_ist_der_rest_null():
    assert tagesrest(umgebung={}, jetzt=JETZT) == 0


def test_voller_rest_ohne_tagesdatei(tmp_path):
    assert tagesrest(umgebung=_aufbau(tmp_path), jetzt=JETZT) == 30


def test_zaehlt_nur_erlaubte_pruefungen_des_agenten(tmp_path):
    u = _aufbau(tmp_path, zeilen=[
        _lauf(), _lauf(), _lauf(erlaubt=False), _lauf(agent="brain-coder"),
        _lauf(art="buchung"), "kaputte zeile {",
    ])
    assert tagesrest(umgebung=u, jetzt=JETZT) == 28


def test_rest_wird_nie_negativ(tmp_path):
    u = _aufbau(tmp_path, grenze=2, zeilen=[_lauf(), _lauf(), _lauf()])
    assert tagesrest(umgebung=u, jetzt=JETZT) == 0


def test_not_aus_heisst_kein_rest(tmp_path):
    u = _aufbau(tmp_path)
    (tmp_path / "budget" / "STOP").write_text("", encoding="utf-8")
    assert tagesrest(umgebung=u, jetzt=JETZT) == 0


def test_unbekannter_agent_oder_kaputte_config_heisst_null(tmp_path):
    u = _aufbau(tmp_path)
    assert tagesrest("gibt-es-nicht", umgebung=u, jetzt=JETZT) == 0
    (tmp_path / "agent_budget.yaml").write_text("agenten: [", encoding="utf-8")
    assert tagesrest(umgebung=u, jetzt=JETZT) == 0


def test_grenze_muss_eine_positive_ganze_zahl_sein(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert tagesrest(umgebung=_aufbau(a, grenze="true"), jetzt=JETZT) == 0
    assert tagesrest(umgebung=_aufbau(b, grenze=0), jetzt=JETZT) == 0


def test_herkunft_nur_bei_genau_getippt():
    assert herkunft_aus_kontext({"eingabe": "getippt"}) == "getippt"
    for anderes in ({"eingabe": "Getippt"}, {"eingabe": "nachfasser"}, {}, None, "getippt", {"eingabe": True}):
        assert herkunft_aus_kontext(anderes) is None
