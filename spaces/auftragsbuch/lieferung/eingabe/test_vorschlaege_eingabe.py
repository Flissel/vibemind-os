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


def _kennung(titel: str) -> str:
    return hashlib.sha256(("vorschlag:" + titel).encode("utf-8")).hexdigest()[:16]


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
        ergebnis = argumente(roh, PLAN)
        assert ergebnis[1] is None, roh
        assert ergebnis[0]["plan_id"] == PLAN
        assert _titel(ergebnis) == ["Milch kaufen", "Steuer machen", "Oma anrufen"], roh
        for punkt in ergebnis[0]["eingabe"]["punkte"]:
            assert set(punkt) == {"punkt_id", "titel", "frist", "link"}
            assert punkt["frist"] is None and punkt["link"] == ""
            assert punkt["punkt_id"] == _kennung(punkt["titel"].lower())


def test_gleicher_titel_gleiche_kennung():
    a = argumente(["Milch  kaufen"], PLAN)[0]["eingabe"]["punkte"][0]
    b = argumente(["  MILCH kaufen "], PLAN)[0]["eingabe"]["punkte"][0]
    assert a["punkt_id"] == b["punkt_id"]
    # Gleiche Titel fallen zusammen; der erste bleibt mit seiner Schreibweise stehen.
    ergebnis = argumente("Milch  kaufen; milch KAUFEN; Brot", PLAN)
    assert _titel(ergebnis) == ["Milch kaufen", "Brot"]


def test_leer_ergibt_rueckfrage():
    for roh in ("", "   \n ; ", [], [" "], {"titel": []}, "[]", None):
        ergebnis = argumente(roh, PLAN)
        assert ergebnis[0] is None and isinstance(ergebnis[1], str) and ergebnis[1], roh


def test_einundfuenfzig_ergibt_rueckfrage():
    assert argumente([f"Punkt {i}" for i in range(50)], PLAN)[1] is None
    ergebnis = argumente([f"Punkt {i}" for i in range(51)], PLAN)
    assert ergebnis[0] is None and "50" in ergebnis[1]
    # Nach dem Zusammenfallen gleicher Titel zaehlt die Zahl der verbleibenden Punkte.
    assert argumente([f"Punkt {i % 50}" for i in range(80)], PLAN)[1] is None


def test_falsche_form_ergibt_rueckfrage_und_wirft_nie():
    for roh in ([1, 2], ["ok", 3], {"punkte": ["x"]}, {"titel": "x"}, {"titel": [None]}, 42, ["x" * 201],
                "[1, 2", '{"titel": 5}', object()):
        ergebnis = argumente(roh, PLAN)
        assert ergebnis[0] is None and ergebnis[1], roh


def test_ergebnis_passt_zum_vertrag():
    from spaces.auftragsbuch.vertrag import lade_auftragsarten, pruefe_eingabe

    arten = lade_auftragsarten(WURZEL / "spaces" / "_contract")
    art = arten[("rowboat", "vorschlaege_anlegen")]
    assert art.version == 2
    ergebnis = argumente("1. Milch kaufen\n2. Straße fegen\n3. Äpfel; Milch kaufen", PLAN)
    assert ergebnis[1] is None
    pruefe_eingabe(art, ergebnis[0]["eingabe"])
    assert set(ergebnis[0]) == {"eingabe", "plan_id"}
    json.dumps(ergebnis[0])


def test_kennung_passt_zum_abholer():
    import re
    punkt = argumente(["Eine Sache"], PLAN)[0]["eingabe"]["punkte"][0]
    assert re.fullmatch(r"[a-f0-9]{16}", punkt["punkt_id"])
