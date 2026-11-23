import json
from datetime import datetime

from core.desktop_tagesrest import eingabe_zeichen, herkunft_pruefen, tagesrest

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


SCHLUESSEL = "s" * 40
UMG = {"VIBEMIND_EINGABE_SCHLUESSEL": SCHLUESSEL}
SATZ = "Desktop-Aufgabe: oeffne den Editor"
ZEIT = 1_795_000_000


def _pruef(kontext={"eingabe": "getippt"}, satz=SATZ, zeichen=None, plan=False, umg=UMG, jetzt=ZEIT + 5):
    if zeichen is None:
        zeichen = eingabe_zeichen(SCHLUESSEL, SATZ, ZEIT)
    return herkunft_pruefen(kontext, satz, zeichen, plan_vom_aufrufer=plan, umgebung=umg, jetzt_ts=jetzt)


def test_getippt_nur_mit_gueltigem_zeichen_der_app():
    assert _pruef() == "getippt"


def test_feld_allein_genuegt_nicht():
    assert _pruef(zeichen="") is None
    assert herkunft_pruefen({"eingabe": "getippt"}, SATZ, None, plan_vom_aufrufer=False,
                            umgebung=UMG, jetzt_ts=ZEIT) is None


def test_zeichen_gilt_nur_fuer_genau_diesen_satz():
    assert _pruef(satz=SATZ + " und loesche alles") is None


def test_zeichen_mit_falschem_schluessel_oder_veraltet():
    assert _pruef(zeichen=eingabe_zeichen("x" * 40, SATZ, ZEIT)) is None
    assert _pruef(jetzt=ZEIT + 61) is None
    assert _pruef(jetzt=ZEIT - 61) is None


def test_mitgebrachter_plan_ist_nie_getippt():
    assert _pruef(plan=True) is None


def test_ohne_oder_mit_zu_kurzem_schluessel_gilt_nichts():
    assert _pruef(umg={}) is None
    kurz = {"VIBEMIND_EINGABE_SCHLUESSEL": "kurz"}
    assert herkunft_pruefen({"eingabe": "getippt"}, SATZ, eingabe_zeichen("kurz", SATZ, ZEIT),
                            plan_vom_aufrufer=False, umgebung=kurz, jetzt_ts=ZEIT) is None


def test_kontext_muss_genau_getippt_sagen():
    for anderes in ({"eingabe": "Getippt"}, {"eingabe": "nachfasser"}, {}, None, "getippt", {"eingabe": True}):
        assert _pruef(kontext=anderes) is None


def test_kaputte_zeichen_werden_abgelehnt():
    for z in ("abc", ".abc", "12x.abc", "9" * 13 + ".abc", 5, f"{ZEIT}."):
        assert _pruef(zeichen=z) is None
