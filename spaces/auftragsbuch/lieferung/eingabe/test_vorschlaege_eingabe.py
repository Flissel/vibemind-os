"""Tests fuer vorschlaege_eingabe.py. Lauffaehig von der Repo-Wurzel: pytest <diese Datei>."""
import hashlib
import json
import sys
from pathlib import Path

HIER = Path(__file__).resolve().parent
WURZEL = HIER.parents[3]
sys.path.insert(0, str(HIER))
if str(WURZEL) not in sys.path:
    sys.path.append(str(WURZEL))

from vorschlaege_eingabe import argumente  # noqa: E402

PLAN = "plan-test-1"
TAG = "2026-11-23"


def _kennung(titel: str, tag: str = TAG) -> str:
    return hashlib.sha256(("vorschlag:" + tag + ":" + titel).encode("utf-8")).hexdigest()[:16]


def _titel(ergebnis) -> list[str]:
    return [p["titel"] for p in ergebnis[0]["eingabe"]["punkte"]]


def test_drei_titel_drei_punkte():
    for roh in (
        '["Milch kaufen", "Steuer machen", "Oma anrufen"]',
        ["Milch kaufen", "Steuer machen", "Oma anrufen"],
        {"titel": ["Milch kaufen", "Steuer machen", "Oma anrufen"]},
        "Milch kaufen\nSteuer machen\nOma anrufen",
        "1. Milch kaufen\n2) Steuer machen\n3. Oma anrufen",
        "- Milch kaufen\n* Steuer machen\n- Oma anrufen",
        "Milch kaufen; Steuer machen; Oma anrufen",
    ):
        ergebnis = argumente(roh, PLAN, TAG)
        assert ergebnis[1] is None, roh
        assert ergebnis[0]["plan_id"] == PLAN
        assert _titel(ergebnis) == ["Milch kaufen", "Steuer machen", "Oma anrufen"], roh
        for punkt in ergebnis[0]["eingabe"]["punkte"]:
            assert set(punkt) == {"punkt_id", "titel", "frist", "link"}
            assert punkt["frist"] is None and punkt["link"] == ""
            assert punkt["punkt_id"] == _kennung(punkt["titel"].lower())


def test_gleicher_titel_gleiche_kennung():
    a = argumente(["Milch  kaufen"], PLAN, TAG)[0]["eingabe"]["punkte"][0]
    b = argumente(["  MILCH kaufen "], PLAN, TAG)[0]["eingabe"]["punkte"][0]
    assert a["punkt_id"] == b["punkt_id"]
    # Gleiche Titel fallen zusammen; der erste bleibt mit seiner Schreibweise stehen.
    ergebnis = argumente("Milch  kaufen; milch KAUFEN; Brot", PLAN, TAG)
    assert _titel(ergebnis) == ["Milch kaufen", "Brot"]


def test_leer_ergibt_rueckfrage():
    for roh in ("", "   \n ; ", [], [" "], {"titel": []}, "[]", None):
        ergebnis = argumente(roh, PLAN, TAG)
        assert ergebnis[0] is None and isinstance(ergebnis[1], str) and ergebnis[1], roh


def test_einundfuenfzig_ergibt_rueckfrage():
    assert argumente([f"Punkt {i}" for i in range(50)], PLAN, TAG)[1] is None
    ergebnis = argumente([f"Punkt {i}" for i in range(51)], PLAN, TAG)
    assert ergebnis[0] is None and "50" in ergebnis[1]
    # Nach dem Zusammenfallen gleicher Titel zaehlt die Zahl der verbleibenden Punkte.
    assert argumente([f"Punkt {i % 50}" for i in range(80)], PLAN, TAG)[1] is None


def test_falsche_form_ergibt_rueckfrage_und_wirft_nie():
    for roh in ([1, 2], ["ok", 3], {"punkte": ["x"]}, {"titel": "x"}, {"titel": [None]}, 42, ["x" * 201],
                "[1, 2", '{"titel": 5}', object()):
        ergebnis = argumente(roh, PLAN, TAG)
        assert ergebnis[0] is None and ergebnis[1], roh


def test_ergebnis_passt_zum_vertrag():
    from spaces.auftragsbuch.vertrag import lade_auftragsarten, pruefe_eingabe

    arten = lade_auftragsarten(WURZEL / "spaces" / "_contract")
    art = arten[("rowboat", "vorschlaege_anlegen")]
    assert art.version == 2
    ergebnis = argumente("1. Milch kaufen\n2. Straße fegen\n3. Äpfel; Milch kaufen", PLAN, TAG)
    assert ergebnis[1] is None
    pruefe_eingabe(art, ergebnis[0]["eingabe"])
    assert set(ergebnis[0]) == {"eingabe", "plan_id"}
    json.dumps(ergebnis[0])


def test_kennung_passt_zum_abholer():
    import re
    punkt = argumente(["Eine Sache"], PLAN, TAG)[0]["eingabe"]["punkte"][0]
    assert re.fullmatch(r"[a-f0-9]{16}", punkt["punkt_id"])


# --- Fix-Runde 1: Listenname ist kein Titel, aus_satz schneidet den Inhalt aus dem Satz ---

def test_listenname_allein_ergibt_rueckfrage():
    from vorschlaege_eingabe import argumente as arg
    for roh in ("To-do-Liste", "Liste.", "Aufgabenliste", "die To-do-Liste!", "meine Liste", "Todo Liste",
                ["Milch", "To-do-Liste"], "Milch; Aufgabenliste", {"titel": ["Brot", "Liste."]}):
        ergebnis = arg(roh, PLAN, TAG)
        assert ergebnis[0] is None and ergebnis[1], roh
    # Ein Titel, der nur mit Liste endet, bleibt gueltig.
    assert arg(["Einkaufsliste schreiben"], PLAN, TAG)[1] is None


def test_aus_satz_schneidet_den_inhalt_aus():
    from vorschlaege_eingabe import aus_satz
    assert aus_satz("Leg mir Milch kaufen auf die To-do-Liste") == "Milch kaufen"
    assert aus_satz("Setz Brot; Eier auf meine Liste.") == "Brot; Eier"
    assert aus_satz("Schreib bitte Zahnarzt auf die Aufgabenliste!") == "Zahnarzt"
    assert aus_satz("Lege mir bitte 1. Brot\n2. Käse auf die Todo-Liste") == "1. Brot\n2. Käse"
    ergebnis = argumente(aus_satz("Setz Brot; Eier auf meine Liste."), PLAN, TAG)
    assert ergebnis[1] is None and _titel(ergebnis) == ["Brot", "Eier"]


def test_aus_satz_ohne_anker_ist_none_und_wirft_nie():
    from vorschlaege_eingabe import aus_satz
    for satz in ("Schreib eine Mail an Oma", "Setz einen Timer auf 5 Minuten", "", "   ",
                 "Leg mir auf die To-do-Liste", None, 5, ["x"]):
        assert aus_satz(satz) is None, satz


# --- Endpruefung: nachgestelltes Wort wird nie zum Punkt, Feldubergabe bleibt unberuehrt ---

def test_aus_satz_vertraegt_nachgestellte_hoeflichkeit():
    from vorschlaege_eingabe import aus_satz
    for satz in ("Leg mir Milch auf die To-do-Liste, danke", "Leg mir Milch auf die To-do-Liste bitte",
                 "Leg mir Milch auf die To-do-Liste, bitte!", "Leg mir Milch auf die To-do-Liste!",
                 "Leg mir Milch auf die To-do-Liste, danke schön."):
        assert aus_satz(satz) == "Milch", satz


def test_hop_argument_danke_ohne_treffer_ergibt_rueckfrage():
    from vorschlaege_eingabe import RUECKFRAGE_KEIN_INHALT, waehle_roh
    assert waehle_roh("danke", "Mach mir bitte was auf die Liste, danke") is None
    assert isinstance(RUECKFRAGE_KEIN_INHALT, str) and RUECKFRAGE_KEIN_INHALT


def test_feldubergabe_bleibt_unveraendert():
    from vorschlaege_eingabe import waehle_roh
    liste = ["Milch", "Brot"]
    assert waehle_roh(liste, "Leg mir die fälligen Mails auf die To-do-Liste") is liste
    assert waehle_roh({"titel": ["Milch"]}, "Leg mir die fälligen Mails auf die To-do-Liste") == {"titel": ["Milch"]}
    assert waehle_roh("Milch; Brot", "Leg mir die fälligen Mails auf die To-do-Liste") == "Milch; Brot"
    assert waehle_roh("Milch", "Leg mir Brot auf die To-do-Liste") == "Milch"


def test_kurzweg_argument_wird_durch_den_satz_ersetzt():
    from vorschlaege_eingabe import waehle_roh
    assert waehle_roh("To-do-Liste", "Leg mir Milch kaufen auf die To-do-Liste") == "Milch kaufen"
    assert waehle_roh("Liste.", "Setz Brot; Eier auf meine Liste.") == "Brot; Eier"
    assert waehle_roh("danke", "Leg mir Milch auf die To-do-Liste, danke") == "Milch"
    assert waehle_roh("TODO-LISTE", "Schreib Zahnarzt auf die Aufgabenliste") == "Zahnarzt"


def test_waehle_roh_wirft_nie():
    from vorschlaege_eingabe import waehle_roh
    for hop, satz in ((None, None), (5, "x"), ("danke", None), ("x", 5), (object(), "Leg mir x auf die Liste")):
        waehle_roh(hop, satz)


# --- Tag in der Kennung (von Felix am 23.11. entschieden) ---

def _id(titel, tag):
    return argumente([titel], PLAN, tag)[0]["eingabe"]["punkte"][0]["punkt_id"]


def test_gleicher_tag_gleiche_kennung():
    assert _id("Milch kaufen", "2026-11-23") == _id("  MILCH   kaufen ", "2026-11-23")
    assert _id("Milch kaufen", "2026-11-23") == _kennung("milch kaufen", "2026-11-23")


def test_anderer_tag_neue_kennung():
    a, b = _id("Milch kaufen", "2026-11-23"), _id("Milch kaufen", "2026-11-24")
    assert a != b
    import re
    assert re.fullmatch(r"[a-f0-9]{16}", b)


def test_ohne_tag_heute(monkeypatch):
    import datetime
    import vorschlaege_eingabe

    class FesterTag(datetime.date):
        @classmethod
        def today(cls):
            return cls(2030, 1, 2)

    monkeypatch.setattr(vorschlaege_eingabe.datetime, "date", FesterTag)
    ohne = argumente(["Milch kaufen"], PLAN)[0]["eingabe"]["punkte"][0]["punkt_id"]
    assert ohne == _kennung("milch kaufen", "2030-01-02")
    assert ohne == argumente(["Milch kaufen"], PLAN, None)[0]["eingabe"]["punkte"][0]["punkt_id"]


def test_kaputter_tag_rueckfrage():
    for tag in ("heute", "2026-13-01", "2026-02-30", "26-11-23", "2026-11-23 ", "2026/11/23", "", 20261123, "٢٠٢٦-١١-٢٣"):
        ergebnis = argumente(["Milch kaufen"], PLAN, tag)
        assert ergebnis[0] is None and isinstance(ergebnis[1], str) and ergebnis[1], tag
