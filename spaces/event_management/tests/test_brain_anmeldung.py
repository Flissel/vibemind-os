"""Der Space `event-management` ist in allen Brain-Katalogen angemeldet.

Der Brain sieht grobe Events (`event.ask`, `event.do`); die feinen Werkzeuge
hat der Event-Bot. Alle Beispielsaetze sind erfunden.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
BRAIN = ROOT / "brain" / "the_brain"
for _p in (ROOT, BRAIN):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core.capability_executor import DirectExecutor  # noqa: E402
from core.capability_router import CapabilityRouter  # noqa: E402
from core.space_contract import load_space_contract, registry_health  # noqa: E402
from spaces._navigator.registry import SPACES, resolve_alias  # noqa: E402

REGISTRY = ROOT / "config" / "space_agent_registry.yml"
CAPABILITIES = BRAIN / "data" / "capabilities.yaml"
EFFECTS = BRAIN / "data" / "capability_effects.yaml"
VERTRAG = ROOT / "spaces" / "_contract" / "event-management.contract.yaml"


def _caps() -> dict[str, dict]:
    liste = yaml.safe_load(CAPABILITIES.read_text(encoding="utf-8"))
    return {c["capability"]: c for c in liste if c["capability"].startswith("event_")}


def test_event_space_im_brain_angemeldet():
    vertrag = load_space_contract(REGISTRY)
    assert "event-management" in vertrag.space_ids
    assert vertrag.event_space_map["event.ask"] == "event-management"
    assert vertrag.event_space_map["event.do"] == "event-management"

    caps = list(yaml.safe_load(CAPABILITIES.read_text(encoding="utf-8")))
    assert registry_health(vertrag, capabilities=caps)["issues"] == []

    eintrag = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["spaces"]["event-management"]
    assert eintrag["agent"] is None
    assert eintrag["generate_agent"] is False
    assert eintrag["runtime"]["token_env"] == "EVENT_GATEWAY_TOKEN"

    nav = SPACES["event-management"]
    assert nav["event_prefix"] == "event."
    assert nav["capabilities"] == ["event.ask", "event.do"]
    assert nav["renderer_id"] is None and nav["stream"] is None

    ziel = {n: c["execution_target"] for n, c in _caps().items()}
    assert ziel == {
        "event_ask": "direct:spaces.event_management.uebergabe:frage",
        "event_do": "direct:spaces.event_management.uebergabe:auftrag",
    }
    assert all(c["arg_kwarg"] == "text" and c["space"] == "event-management" for c in _caps().values())
    val = _caps()["event_do"]["validator"]
    assert val["kind"] == "truth:event_app_change" and val["on_fail"] == "report"
    assert val["postcondition"] == {"check": "event_app_change", "actor": "VibeMind", "since_s": 600}

    wirkung = yaml.safe_load(EFFECTS.read_text(encoding="utf-8"))
    wirkung = wirkung.get("effects", wirkung)
    assert wirkung["event_ask"] == "read" and wirkung["event_do"] == "external"


@pytest.mark.parametrize("name", ["event_ask", "event_do"])
def test_direct_ziel_loest_ueber_den_executor_auf(name):
    executor = DirectExecutor(_caps()[name]["execution_target"])
    assert executor.is_resolvable()


def test_vertrag_hat_genau_zwei_grobe_capabilities():
    vertrag = yaml.safe_load(VERTRAG.read_text(encoding="utf-8"))
    assert vertrag["id"] == "event-management"
    assert vertrag["prefixes"] == ["event."]
    assert {c["name"]: c["writes"] for c in vertrag["capabilities"]} == {
        "event_ask": False,
        "event_do": True,
    }


def test_aliasse_gehoeren_nur_dem_event_space():
    for alias in ("festival", "orga", "veranstaltung"):
        assert resolve_alias(alias) == "event-management"
    eintraege = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["spaces"]
    for sid, e in eintraege.items():
        if sid != "event-management":
            assert not {"festival", "orga", "veranstaltung"} & set(e.get("aliases") or [])
    for sid, meta in SPACES.items():
        if sid != "event-management":
            assert not {"festival", "orga", "veranstaltung"} & {a.lower() for a in meta["aliases"]}


POSITIV = {
    "event_ask": [
        "Was ist beim Festival noch bei den Aufgaben offen?",
        "Wie viele Gäste sind für die Veranstaltung gemeldet?",
        "Zeig mir das Budget vom Event",
    ],
    "event_do": [
        # Ein Auftrag braucht Schreibverb UND Orga-Objekt; "die Becher" allein
        # ist keins, darum steht hier "die Aufgabe Becher".
        "Trag beim Festival ein, dass die Aufgabe Becher erledigt ist",
        "Lege für das Festival eine neue Aufgabe an",
        "Weise beim Festival den Helfern die Aufgabe Becher zu",
    ],
}
# Etappe 4, Task 4: weitere Event-Kategorien des Katalogs. "Team-Meeting" (mit
# Trenner) bleibt beim Kalender-Ausschluss (Ruling, siehe NEGATIV); nur das
# zusammengeschriebene "Teammeeting" erreicht den Event-Bot.
POSITIV_KATEGORIEN = {
    "event_ask": [
        "Was ist beim Hackathon bei den Aufgaben offen?",
        "Wie steht das Budget der Weihnachtsfeier?",
        "Welche Helfer hat die Messe?",
        "Zeig mir das Budget vom Webinar",
        # ("Wer ist beim Fest im Team?" nimmt vorher knowledge_query, wie jede Wer-Frage.)
        "Zeig mir die Aufgaben vom Fest",
    ],
    "event_do": [
        "Lege für den Hackathon eine Aufgabe Catering an",
        "Trag beim Webinar die Aufgabe Technik-Check ein",
        "Weise bei der Weihnachtsfeier dem Team die Aufgabe Deko zu",
        "Lege für die Messe eine Schicht am Stand an",
        "Trag beim Fest die Aufgabe Grill ein",
        "Lege für das Teammeeting eine Aufgabe Raum an",
        # Ruling T4-a: "fest"/"messe" nur mit Artikel bzw. als Kompositum.
        "Lege für das Sommerfest eine Aufgabe Catering an",
        "Trag beim Hoffest die Aufgabe Grill ein",
        "Lege auf der Messe eine Schicht am Stand an",
        "Lege für die Buchmesse eine Aufgabe Standbau an",
    ],
}
POSITIV_KATEGORIEN["event_ask"] += [
    "Welche Aufgaben hat das Team für die Messe?",
    "Wie steht das Budget vom Sommerfest?",
]
for _name, _saetze in POSITIV_KATEGORIEN.items():
    POSITIV[_name] = POSITIV[_name] + _saetze
NEGATIV = [
    "Erinnere mich morgen an den Termin",
    "Erstelle eine Aufgabe im Projekt",
    "Zeige meine Termine",
    "Eventuell lege ich morgen eine Notiz an",
    "Wie viele Gäste hat das Hotel",
    "Erstelle ein Event im Kalender",
    "Erstelle ein Event für morgen",
    "Trag das Event in meinen Kalender ein",
    "Ändere die Veranstaltung im Kalender",
    "Welche Events stehen in meinem Kalender?",
    # Gesamtpruefung C1: Saetze anderer Spaces, die event_do frueher frass.
    "Erstelle eine neue Bubble zum Festival",
    "Erstelle ein Bubble-Projekt fürs Festival",
    "Erstelle einen Instagram-Post zum Festival",
    "Erstelle einen Collage-Newsletter zum Festival",
    "Erstelle einen Podcast über das Festival",
    "erstelle ein video über das festival",
    "Erstelle eine Idee zum Festival",
    "Lege einen Lead für das Festival an",
    "Erstelle ein Orga-Chart für mein Team",
    "Ändere die Orga meines Projekts",
    "Ist beim Festival schon alles erledigt?",  # Frage, kein Auftrag
    "Plane einen Post zum Event mit dem Team",
    # Etappe 4, Task 4: Kalender und fremde Spaces gewinnen auch gegen die neuen
    # Kategorien.
    "Was steht im Kalender für das Meeting?",
    # Ruling: der Kalender-Ausschluss (meeting\w*) gewinnt gegen team.?meeting. Kosten:
    # dieser Auftrag wird nicht erkannt.
    "Team-Meeting Aufgabe anlegen",
    "Lege für das Team-Meeting eine Aufgabe an",
    "Ich messe die Temperatur",
    "Ich messe die Temperatur und lege eine Notiz an",
    "Erstelle einen Post zum Hackathon",
    "Trag das Webinar in meinen Kalender ein",
    "Lege für die Weihnachtsfeier einen Termin an",
    "Erstelle ein Video über die Messe",
    # Review Task 4 (Ruling T4-a): "fest"/"messe" als Verbteil oder Verb sind kein Event.
    "Lege die Aufgabe fest",
    "Lege im Projekt fest, welche Aufgabe das Team macht",
    "Erstelle eine Aufgabe im Projekt und setze die Frist fest",
    "Setze das Budget fest",
    "Das Budget steht fest",
    "Halte fest: das Team braucht Budget",
    "Ich messe das Budget",
    "Lege im Manifest eine Aufgabe an",
    "Lege für das Manifest des Teams eine Aufgabe an",
]

# Ruling T4-a: die erlaubten Formen von "fest" und "messe" (im YAML-Text des Musters).
FEST_ARTIKEL = r"(?:das|dem|vom|beim|zum|aufs?|unser\w*|ein\w*)\s+fest\b"
FEST_KOMPOSITUM = r"(?!manifest)\w{3,}fest\b"
MESSE_ARTIKEL = r"(?:die|der|zur|auf\s+der|einer?)\s+messe\b"
MESSE_KOMPOSITUM = r"\w+messe\b"


@pytest.mark.parametrize("name", ["event_ask", "event_do"])
def test_neue_kategorien_in_jedem_muster(name):
    for p in _caps()[name]["match_patterns"]:
        for wort in ("webinar", "hackathon", "weihnachtsfeier", "team.?meeting"):
            assert wort in p, (name, wort)
        for form in (FEST_ARTIKEL, FEST_KOMPOSITUM, MESSE_ARTIKEL, MESSE_KOMPOSITUM):
            assert form in p, (name, form)
        # Kein nacktes "fest"/"messe" als eigene Alternative (Ruling T4-a).
        assert not re.search(r"[|(](?:\\b)?(?:fest|messe)\\b", p), (name, p)
        # Die bestehenden Ausschluesse bleiben vorn.
        assert p.startswith("^(?!.*\\b(?:kalender\\w*|calendar|termin\\w*|meeting\\w*)\\b)"), p


def test_registry_keywords_kennen_die_kategorien():
    eintrag = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["spaces"]["event-management"]
    (muster,) = eintrag["keywords"]
    for satz in ("Hackathon am Freitag", "das Webinar", "die Messe", "Weihnachtsfeier",
                 "Teammeeting", "das Fest im Hof", "Sommerfest", "Buchmesse", "Festival",
                 "Veranstaltung"):
        assert re.search(muster, satz, re.IGNORECASE), satz
    for satz in ("Festung", "Messer", "Ich messen", "Lege die Aufgabe fest", "Ich messe das Budget",
                 "Das Budget steht fest", "Halte fest", "Manifest"):
        assert not re.search(muster, satz, re.IGNORECASE), satz
    for form in (FEST_ARTIKEL, FEST_KOMPOSITUM, MESSE_ARTIKEL, MESSE_KOMPOSITUM):
        assert form in muster, form

# Gemessen am Basisstand (capabilities.yaml ohne event_*-Eintraege), nicht geraten.
BESTEHENDE_ZIELE = {
    "Erstelle eine neue Bubble zum Festival": "bubble_create",
    "Erstelle ein Bubble-Projekt fürs Festival": "bubble_create",
    "Plane einen Post zum Event mit dem Team": "som_plan",
}


@pytest.mark.parametrize("name", ["event_ask", "event_do"])
def test_patterns_greifen_bei_event_bezug(name):
    muster = _caps()[name]["match_patterns"]
    for satz in POSITIV[name]:
        assert any(re.search(p, satz, re.IGNORECASE) for p in muster), satz


def test_patterns_greifen_nicht_bei_fremden_absichten():
    for name, cap in _caps().items():
        for satz in NEGATIV:
            for p in cap["match_patterns"]:
                assert not re.search(p, satz, re.IGNORECASE), (name, satz)


def test_router_leitet_positive_saetze_an_event_capability():
    router = CapabilityRouter(CAPABILITIES)
    for name, saetze in POSITIV.items():
        for satz in saetze:
            treffer = router.route(satz)
            assert treffer is not None and treffer.capability == name, (satz, treffer)
    for satz in NEGATIV:
        treffer = router.route(satz)
        name = treffer.capability if treffer else None
        assert not (name or "").startswith("event_"), f"{satz!r} landet auf {name}"


def test_bestehende_capabilities_behalten_ihre_saetze():
    router = CapabilityRouter(CAPABILITIES)
    for satz, ziel in BESTEHENDE_ZIELE.items():
        treffer = router.route(satz)
        assert treffer is not None and treffer.capability == ziel, (satz, treffer)


def test_event_eintraege_stehen_am_ende():
    """Erster Regex-Treffer gewinnt: am Ende verdraengen sie keine bestehende Capability."""
    namen = [c["capability"] for c in yaml.safe_load(CAPABILITIES.read_text(encoding="utf-8"))]
    assert namen[-2:] == ["event_do", "event_ask"]
