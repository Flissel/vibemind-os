"""Werkzeugkatalog je Schluessel und Ausfuehrung (system/werkzeuge.py), MCP-frei.

Die Werkzeuge laufen im Betrieb mit der App-DSN des event-mcp; deshalb rufen die Tests
sie ueber `db_app()` (ohne gesetzten Kontext) auf und lesen mit `db_admin` nach.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from spaces.event_management.system import marke, schluessel, werkzeuge
from spaces.event_management.system.bausteine import KERN, REGISTRY, ZUSAETZE
from spaces.event_management.system.kontext import NichtGefunden

BEGINN = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
VERBOTEN = {"event_id", "firma_id", "person_id"}
SCHREIB_ENDUNGEN = ("_anlegen", "_aendern", "_loeschen")


def _firma(admin: Any, kurz: str) -> UUID:
    return admin.execute(
        "insert into event.firma (name, kurz) values (%s, %s) returning id",
        ("Beispiel " + kurz, kurz),
    ).fetchone()["id"]


def _event(admin: Any, firma: UUID, kategorie: str, name: str, *, quelle: str = "neu") -> UUID:
    return admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende, quelle)"
        " values (%s, %s, %s, %s, %s, %s) returning id",
        (firma, kategorie, name, BEGINN, BEGINN + timedelta(hours=3), quelle),
    ).fetchone()["id"]


def _person(admin: Any, mail: str, *, betreiber: bool = False) -> UUID:
    return admin.execute(
        "insert into event.person (mail, name, betreiber) values (%s, 'Erika Beispiel', %s)"
        " returning id",
        (mail, betreiber),
    ).fetchone()["id"]


def _mitglied(admin: Any, person: UUID, firma: UUID, rolle: str) -> None:
    admin.execute("insert into event.mitgliedschaft values (%s, %s, %s)", (person, firma, rolle))


def _kontakt(admin: Any, firma: UUID, name: str, mail: str | None = None) -> UUID:
    return admin.execute(
        "insert into event.kontakt (firma_id, name, mail) values (%s, %s, %s) returning id",
        (firma, name, mail),
    ).fetchone()["id"]


def _info(admin: Any, art: str, **argumente: Any) -> schluessel.SchluesselInfo:
    _, klartext = schluessel.ausstellen(admin, art=art, bezeichnung="Test", **argumente)
    return schluessel.pruefen(admin, klartext)


def _namen(app: Any, info: schluessel.SchluesselInfo) -> set[str]:
    return {w.name for w in werkzeuge.katalog(app, info)}


def _schluessel_im_schema(schema: Any) -> set[str]:
    """Alle Property-Namen eines JSON-Schemas (rekursiv)."""
    gefunden: set[str] = set()
    if isinstance(schema, dict):
        for name, wert in schema.items():
            if name == "properties" and isinstance(wert, dict):
                gefunden |= set(wert)
            gefunden |= _schluessel_im_schema(wert)
    elif isinstance(schema, list):
        for wert in schema:
            gefunden |= _schluessel_im_schema(wert)
    return gefunden


def _objekte_im_schema(schema: Any) -> list[dict[str, Any]]:
    gefunden: list[dict[str, Any]] = []
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            gefunden.append(schema)
        for wert in schema.values():
            gefunden += _objekte_im_schema(wert)
    elif isinstance(schema, list):
        for wert in schema:
            gefunden += _objekte_im_schema(wert)
    return gefunden


@pytest.fixture
def welt(db_admin: Any) -> dict[str, Any]:
    a = _firma(db_admin, "firma-a")
    b = _firma(db_admin, "firma-b")
    lea = _person(db_admin, "lea@example.org")
    bea = _person(db_admin, "bea@example.org", betreiber=True)
    _mitglied(db_admin, lea, a, "leitung")
    return {
        "a": a, "b": b, "lea": lea, "bea": bea,
        "meeting": _event(db_admin, a, "team_meeting", "Teamrunde Beispiel"),
        "festival": _event(db_admin, a, "festival", "Testfestival"),
        "fremd": _event(db_admin, b, "team_meeting", "Fremde Runde"),
        "legacy": _event(db_admin, a, "weihnachtsfeier", "Alte Feier", quelle="legacy"),
    }


# --- Katalog ------------------------------------------------------------------------


def test_event_schluessel_sieht_nur_eigene_bausteine(db_admin, db_app, welt):
    app = db_app()
    info = _info(db_admin, "event", event_id=welt["meeting"])
    katalog = {w.name: w for w in werkzeuge.katalog(app, info)}

    for op in ("liste", "anlegen", "aendern", "loeschen"):
        assert f"protokoll_{op}" in katalog
        assert f"beschluesse_{op}" in katalog
        assert f"aufgaben_kategorien_{op}" in katalog
        assert f"budget_{op}" not in katalog  # team_meeting: ohne budget
        assert f"buehnen_{op}" not in katalog
    assert {"event_info", "verlauf_lesen", "kontakte_suchen", "kontakt_anlegen"} <= set(katalog)
    # S1: Kontakte aendern/entfernen gibt es fuer den Bot nicht.
    assert not {n for n in katalog if n.startswith("kontakt_") and n != "kontakt_anlegen"}
    # Event-Schluessel: Event steht fest, keine Marke und keine Event-Wahl.
    assert "events_suchen" not in katalog and "event_waehlen" not in katalog
    for w in katalog.values():
        assert "marke" not in _schluessel_im_schema(w.eingabe), w.name
    assert katalog["protokoll_liste"].schreibend is False
    assert katalog["protokoll_anlegen"].schreibend is True
    assert katalog["kontakt_anlegen"].schreibend is True


def test_begriffe_in_beschreibung(db_admin, db_app, welt):
    app = db_app()
    festival = {w.name: w for w in werkzeuge.katalog(
        app, _info(db_admin, "event", event_id=welt["festival"]))}
    meeting = {w.name: w for w in werkzeuge.katalog(
        app, _info(db_admin, "event", event_id=welt["meeting"]))}
    assert festival["ablauf_liste"].beschreibung == "Timetable des Events anzeigen"
    assert "Timetable" in festival["ablauf_anlegen"].beschreibung
    assert "Tagesordnung" in meeting["ablauf_liste"].beschreibung
    assert "buehnen_liste" in festival  # festival erbt buehnen, team_meeting nicht


def test_feldtypen_als_json_schema(db_admin, db_app, welt):
    app = db_app()
    katalog = {w.name: w for w in werkzeuge.katalog(
        app, _info(db_admin, "event", event_id=welt["festival"]))}

    anlegen = katalog["aufgaben_anlegen"].eingabe
    assert anlegen["type"] == "object" and anlegen["additionalProperties"] is False
    assert anlegen["required"] == ["titel"]
    p = anlegen["properties"]
    assert p["titel"] == {"type": "string", "maxLength": 2000}
    assert p["status"] == {"type": "string", "enum": ["offen", "in_arbeit", "erledigt"]}
    assert p["faellig"]["type"] == "string" and p["faellig"]["format"] == "date-time"
    uuid = {"type": "string", "format": "uuid"}
    assert uuid.items() <= p["verantwortlich_id"].items()  # kontakt
    assert uuid.items() <= p["kategorie_id"].items()  # ref:*
    assert katalog["helferschichten_anlegen"].eingabe["properties"]["bedarf"] == {
        "type": "integer", "minimum": 0}

    aendern = katalog["aufgaben_aendern"].eingabe
    assert aendern["required"] == ["zeile"]
    assert uuid.items() <= aendern["properties"]["zeile"].items()
    assert set(aendern["properties"]) == {"zeile", *REGISTRY["aufgaben"].felder}
    loeschen = katalog["aufgaben_loeschen"].eingabe
    assert loeschen["required"] == ["zeile"] and set(loeschen["properties"]) == {"zeile"}
    assert katalog["aufgaben_liste"].eingabe["properties"] == {}

    leitung = {w.name: w for w in werkzeuge.katalog(
        app, _info(db_admin, "leitung", person_id=welt["lea"]))}
    link = leitung["online_raum_anlegen"].eingabe["properties"]["link"]
    assert link["type"] == "string" and link["pattern"] == "^https?://"
    # additionalProperties: false ueberall, in jedem Katalog.
    for w in [*katalog.values(), *leitung.values()]:
        for objekt in _objekte_im_schema(w.eingabe):
            assert objekt.get("additionalProperties") is False, w.name


def test_leitung_und_betreiber_vereinigung_mit_marke(db_admin, db_app, welt):
    app = db_app()
    for info in (_info(db_admin, "leitung", person_id=welt["lea"]),
                 _info(db_admin, "betreiber", person_id=welt["bea"])):
        katalog = {w.name: w for w in werkzeuge.katalog(app, info)}
        for eintrag in REGISTRY:  # nicht schema-gefiltert
            for op in ("liste", "anlegen", "aendern", "loeschen"):
                w = katalog[f"{eintrag}_{op}"]
                assert "marke" in w.eingabe["required"], w.name
        assert {"event_info", "verlauf_lesen", "kontakte_suchen", "kontakt_anlegen",
                "events_suchen", "event_waehlen"} <= set(katalog)
        assert "marke" in katalog["event_info"].eingabe["required"]
        assert "marke" not in katalog["events_suchen"].eingabe["properties"]


def test_melden_hat_keine_baustein_werkzeuge(db_admin, db_app, welt):
    """Der Empfangs-Agent meldet nur Codes (Task 7)."""
    app = db_app()
    assert _namen(app, _info(db_admin, "melden")) == {"gruppe_melden", "chat_melden"}


def test_event_id_argument_gibt_es_nicht(db_admin, db_app, welt):
    app = db_app()
    infos = [
        _info(db_admin, "event", event_id=welt["meeting"]),
        _info(db_admin, "event", event_id=welt["festival"]),
        _info(db_admin, "leitung", person_id=welt["lea"]),
        _info(db_admin, "betreiber", person_id=welt["bea"]),
        _info(db_admin, "betreiber_lesen", person_id=welt["bea"]),
        _info(db_admin, "melden"),
    ]
    for info in infos:
        for w in werkzeuge.katalog(app, info):
            assert not VERBOTEN & _schluessel_im_schema(w.eingabe), (info.art, w.name)


def test_standard_begriffe_passen_zu_den_bausteinen():
    """Drift-Waechter: jeder Baustein hat einen Standardnamen, kein Name ist verwaist."""
    assert set(werkzeuge.STANDARD_BEGRIFFE) == set(KERN) | set(ZUSAETZE)
    registry_bausteine = {t.baustein or n for n, t in REGISTRY.items()}
    assert registry_bausteine <= set(werkzeuge.STANDARD_BEGRIFFE)
    hilfs = {n for n, t in REGISTRY.items() if t.baustein is not None}
    assert {n: werkzeuge.begriff(n) for n in hilfs} == {"aufgaben_kategorien": "Aufgaben-Kategorien"}


# --- Ausfuehren ---------------------------------------------------------------------


def test_schreibantwort_nennt_event(db_admin, db_app, welt):
    app = db_app()
    info = _info(db_admin, "event", event_id=welt["meeting"])
    kopf = {"name": "Teamrunde Beispiel", "firma": "Beispiel firma-a"}

    neu = werkzeuge.ausfuehren(app, info, "protokoll_anlegen", {"punkt": "Begruessung"})
    assert neu["event"] == kopf
    zeile_id = UUID(str(neu["zeile"]["id"]))
    assert neu["zeile"]["punkt"] == "Begruessung"
    assert "event_id" not in neu["zeile"] and "firma_id" not in neu["zeile"]

    geaendert = werkzeuge.ausfuehren(app, info, "protokoll_aendern",
                                     {"zeile": str(zeile_id), "notiz": "kurz"})
    assert geaendert["event"] == kopf and geaendert["zeile"]["notiz"] == "kurz"

    liste = werkzeuge.ausfuehren(app, info, "protokoll_liste", {})
    assert liste["event"] == kopf
    assert [z["punkt"] for z in liste["zeilen"]] == ["Begruessung"]

    weg = werkzeuge.ausfuehren(app, info, "protokoll_loeschen", {"zeile": str(zeile_id)})
    assert weg["event"] == kopf
    assert db_admin.execute("select count(*) as n from event.protokoll_punkt").fetchone()["n"] == 0

    angelegt = werkzeuge.ausfuehren(app, info, "kontakt_anlegen",
                                    {"name": "Max Beispiel", "mail": "max@example.org"})
    assert angelegt["event"] == kopf
    zeile = db_admin.execute("select firma_id from event.kontakt where id = %s",
                             (angelegt["zeile"]["id"],)).fetchone()
    assert zeile["firma_id"] == welt["a"]


def test_verlauf_quelle_bot(db_admin, db_app, welt):
    app = db_app()
    info = _info(db_admin, "event", event_id=welt["meeting"])
    neu = werkzeuge.ausfuehren(app, info, "aufgaben_anlegen", {"titel": "Raum buchen"})

    eintrag = db_admin.execute(
        "select quelle, actor, actor_person_id, baustein, aktion, ziel_id from event.verlauf"
        " where event_id = %s", (welt["meeting"],)).fetchone()
    assert eintrag["quelle"] == "bot" and eintrag["actor"] == "bot:event"
    assert eintrag["actor_person_id"] is None
    assert (eintrag["baustein"], eintrag["aktion"]) == ("aufgaben", "angelegt")
    assert str(eintrag["ziel_id"]) == str(neu["zeile"]["id"])

    gelesen = werkzeuge.ausfuehren(app, info, "verlauf_lesen", {"limit": 5})
    assert gelesen["event"]["name"] == "Teamrunde Beispiel"
    assert [(e["quelle"], e["actor"]) for e in gelesen["eintraege"]] == [("bot", "bot:event")]
    assert not VERBOTEN & set(gelesen["eintraege"][0])

    # Leitung: actor_person_id ist die Person des Schluessels.
    lea = _info(db_admin, "leitung", person_id=welt["lea"])
    m = marke.marke_ausstellen(app, lea, welt["meeting"])
    werkzeuge.ausfuehren(app, lea, "aufgaben_anlegen", {"marke": m, "titel": "Agenda"})
    zeile = db_admin.execute(
        "select actor, actor_person_id from event.verlauf where event_id = %s"
        " and actor = 'bot:leitung'", (welt["meeting"],)).fetchone()
    assert zeile["actor_person_id"] == welt["lea"]


def test_leitung_braucht_marke(db_admin, db_app, welt):
    app = db_app()
    info = _info(db_admin, "leitung", person_id=welt["lea"])
    with pytest.raises(ValueError):
        werkzeuge.ausfuehren(app, info, "aufgaben_anlegen", {"titel": "Ohne Marke"})
    with pytest.raises(NichtGefunden):
        werkzeuge.ausfuehren(app, info, "aufgaben_anlegen",
                             {"marke": "mk_aaaaaaaaaaaa", "titel": "Falsche Marke"})
    assert db_admin.execute("select count(*) as n from event.aufgabe").fetchone()["n"] == 0

    m = marke.marke_ausstellen(app, info, welt["festival"])
    neu = werkzeuge.ausfuehren(app, info, "aufgaben_anlegen", {"marke": m, "titel": "Mit Marke"})
    assert neu["event"] == {"name": "Testfestival", "firma": "Beispiel firma-a"}
    zeile = db_admin.execute("select event_id, titel from event.aufgabe").fetchone()
    assert zeile == {"event_id": welt["festival"], "titel": "Mit Marke"}
    # Das Festival hat Buehnen, das Meeting nicht.
    werkzeuge.ausfuehren(app, info, "buehnen_anlegen", {"marke": m, "name": "Hauptbuehne"})


def test_baustein_den_das_event_nicht_hat(db_admin, db_app, welt):
    app = db_app()
    info = _info(db_admin, "leitung", person_id=welt["lea"])
    m = marke.marke_ausstellen(app, info, welt["meeting"])
    with pytest.raises(ValueError) as e:
        werkzeuge.ausfuehren(app, info, "budget_liste", {"marke": m})
    assert str(e.value) == "Dieses Event hat keinen Bereich Budget"
    with pytest.raises(ValueError) as e:
        werkzeuge.ausfuehren(app, info, "buehnen_anlegen", {"marke": m, "name": "X"})
    assert str(e.value) == "Dieses Event hat keinen Bereich Bühnen"


def test_betreiber_lesen_nur_lesend(db_admin, db_app, welt):
    app = db_app()
    info = _info(db_admin, "betreiber_lesen", person_id=welt["bea"])
    namen = _namen(app, info)
    assert not [n for n in namen if n.endswith(SCHREIB_ENDUNGEN)]
    assert {f"{e}_liste" for e in REGISTRY} <= namen
    assert {"event_info", "verlauf_lesen", "events_suchen", "event_waehlen",
            "kontakte_suchen"} <= namen
    assert all(not w.schreibend for w in werkzeuge.katalog(app, info))

    m = marke.marke_ausstellen(app, info, welt["meeting"])
    for name, argumente in (("aufgaben_anlegen", {"marke": m, "titel": "Nein"}),
                            ("kontakt_anlegen", {"marke": m, "name": "Nein"})):
        with pytest.raises(NichtGefunden) as e:
            werkzeuge.ausfuehren(app, info, name, argumente)
        assert str(e.value) == "unbekanntes Werkzeug"
    assert db_admin.execute("select count(*) as n from event.aufgabe").fetchone()["n"] == 0
    assert werkzeuge.ausfuehren(app, info, "aufgaben_liste", {"marke": m})["zeilen"] == []


def test_unbekanntes_werkzeug(db_admin, db_app, welt):
    app = db_app()
    info = _info(db_admin, "event", event_id=welt["meeting"])
    for name in ("gibt_es_nicht", "budget_liste", "kontakt_entfernen", "kontakt_aendern"):
        with pytest.raises(NichtGefunden) as e:
            werkzeuge.ausfuehren(app, info, name, {})
        assert str(e.value) == "unbekanntes Werkzeug"


def test_unbekannte_argumente_abgelehnt(db_admin, db_app, welt):
    app = db_app()
    info = _info(db_admin, "event", event_id=welt["meeting"])
    fremd = str(welt["fremd"])
    falsch: list[tuple[str, Any]] = [
        ("aufgaben_anlegen", {"titel": "x", "event_id": fremd}),
        ("aufgaben_anlegen", {"titel": "x", "firma_id": str(welt["b"])}),
        ("aufgaben_anlegen", {"titel": "x", "marke": "mk_aaaaaaaaaaaa"}),
        ("aufgaben_anlegen", {"titel": "x", "id": str(uuid4())}),
        ("aufgaben_liste", {"event_id": fremd}),
        ("aufgaben_anlegen", ["titel"]),
        ("aufgaben_anlegen", {}),  # Pflicht fehlt
        ("aufgaben_anlegen", {"titel": 5}),
        ("aufgaben_anlegen", {"titel": "x" * 2001}),
        ("aufgaben_anlegen", {"titel": "a\x00b"}),
        ("aufgaben_anlegen", {"titel": "x", "status": "fertig"}),
        ("aufgaben_anlegen", {"titel": "x", "verantwortlich_id": "kein-uuid"}),
        ("aufgaben_anlegen", {"titel": "x", "faellig": "morgen"}),
        ("protokoll_anlegen", {"punkt": None}),
        ("aufgaben_aendern", {"titel": "ohne zeile"}),
    ]
    for name, argumente in falsch:
        with pytest.raises(ValueError) as e:
            werkzeuge.ausfuehren(app, info, name, argumente)
        assert fremd not in str(e.value)
    assert db_admin.execute("select count(*) as n from event.aufgabe").fetchone()["n"] == 0

    festival = _info(db_admin, "event", event_id=welt["festival"])
    for argumente in ({"titel": "x", "bedarf": -1, "beginn": "2027-01-01T10:00",
                       "ende": "2027-01-01T12:00"},
                      {"titel": "x", "bedarf": True, "beginn": "2027-01-01T10:00",
                       "ende": "2027-01-01T12:00"}):
        with pytest.raises(ValueError):
            werkzeuge.ausfuehren(app, festival, "helferschichten_anlegen", argumente)
    # Naive Zeit gilt als Berlin.
    neu = werkzeuge.ausfuehren(app, festival, "helferschichten_anlegen", {
        "titel": "Einlass", "beginn": "2027-01-01T10:00", "ende": "2027-01-01T12:00",
        "bedarf": 3})
    assert neu["zeile"]["beginn"] == datetime(2027, 1, 1, 9, 0, tzinfo=timezone.utc)


def test_legacy_abgelehnt(db_admin, db_app, welt):
    app = db_app()
    info = _info(db_admin, "event", event_id=welt["legacy"])
    for name, argumente in (("aufgaben_anlegen", {"titel": "x"}), ("aufgaben_liste", {}),
                            ("event_info", {}), ("kontakt_anlegen", {"name": "x"})):
        with pytest.raises(ValueError) as e:
            werkzeuge.ausfuehren(app, info, name, argumente)
        assert str(e.value) == "Legacy-Event: bitte die alte App nutzen"
    assert db_admin.execute("select count(*) as n from event.aufgabe").fetchone()["n"] == 0
    assert db_admin.execute("select count(*) as n from event.kontakt").fetchone()["n"] == 0


def test_event_info_mit_begriffen(db_admin, db_app, welt):
    app = db_app()
    info = _info(db_admin, "event", event_id=welt["festival"])
    ergebnis = werkzeuge.ausfuehren(app, info, "event_info", {})
    assert ergebnis["event"] == {"name": "Testfestival", "firma": "Beispiel firma-a"}
    assert ergebnis["kategorie"] == "festival"
    assert ergebnis["begriffe"]["ablauf"] == "Timetable"
    assert ergebnis["begriffe"]["buehnen"] == "Bühnen"
    assert ergebnis["begriffe"]["budget"] == "Budget"
    assert ergebnis["begriffe"]["verlauf"] == "Verlauf"
    assert "protokoll" not in ergebnis["begriffe"]  # nur Bausteine des Events
    assert ergebnis["beginn"] == BEGINN
    assert not VERBOTEN & set(ergebnis) and "id" not in ergebnis


# --- Kontakte (Ruling S1) -----------------------------------------------------------


def test_kontakte_suchen_nur_firma_des_events(db_admin, db_app, welt):
    app = db_app()
    eigen = _kontakt(db_admin, welt["a"], "Anna Beispiel", "anna@example.org")
    _kontakt(db_admin, welt["a"], "Bert Muster")
    _kontakt(db_admin, welt["b"], "Anna Fremd", "anna@fremd.example.org")
    for i in range(25):
        _kontakt(db_admin, welt["a"], f"Zoe {i:02d}")
    info = _info(db_admin, "event", event_id=welt["meeting"])

    treffer = werkzeuge.ausfuehren(app, info, "kontakte_suchen", {"suche": "anna"})["kontakte"]
    assert [t["name"] for t in treffer] == ["Anna Beispiel"]
    assert treffer[0]["id"] == eigen and treffer[0]["mail"] == "anna@example.org"
    assert not VERBOTEN & set(treffer[0])
    per_mail = werkzeuge.ausfuehren(app, info, "kontakte_suchen", {"suche": "anna@example"})
    assert [t["name"] for t in per_mail["kontakte"]] == ["Anna Beispiel"]
    per_id = werkzeuge.ausfuehren(app, info, "kontakte_suchen", {"suche": str(eigen)})
    assert [t["id"] for t in per_id["kontakte"]] == [eigen]
    alle = werkzeuge.ausfuehren(app, info, "kontakte_suchen", {})["kontakte"]
    assert len(alle) == 20  # max 20

    # Leitung mit Marke: Firma des gewaehlten Events.
    lea = _info(db_admin, "leitung", person_id=welt["lea"])
    m = marke.marke_ausstellen(app, lea, welt["festival"])
    treffer = werkzeuge.ausfuehren(app, lea, "kontakte_suchen", {"marke": m, "suche": "Anna"})
    assert [t["name"] for t in treffer["kontakte"]] == ["Anna Beispiel"]
    neu = werkzeuge.ausfuehren(app, lea, "kontakt_anlegen", {"marke": m, "name": "Neu Beispiel"})
    assert db_admin.execute("select firma_id from event.kontakt where id = %s",
                            (neu["zeile"]["id"],)).fetchone()["firma_id"] == welt["a"]


# --- Event-Wahl (minimal; Task 5 vervollstaendigt) ----------------------------------


def test_events_suchen_und_waehlen(db_admin, db_app, welt):
    app = db_app()
    info = _info(db_admin, "leitung", person_id=welt["lea"])

    gefunden = werkzeuge.ausfuehren(app, info, "events_suchen", {})["events"]
    namen = {e["name"] for e in gefunden}
    assert {"Teamrunde Beispiel", "Testfestival"} <= namen
    assert "Fremde Runde" not in namen  # andere Firma
    for e in gefunden:
        assert "id" not in e and not VERBOTEN & set(e)
        assert e["firma"] == "Beispiel firma-a"

    gewaehlt = werkzeuge.ausfuehren(app, info, "event_waehlen", {"suche": "Testfestival"})
    assert gewaehlt["event"]["name"] == "Testfestival"
    assert marke.marke_aufloesen(app, info, gewaehlt["marke"]) == welt["festival"]

    mehrere = werkzeuge.ausfuehren(app, info, "event_waehlen", {"suche": "e"})
    assert "marke" not in mehrere and len(mehrere["treffer"]) >= 2
    with pytest.raises(NichtGefunden):
        werkzeuge.ausfuehren(app, info, "event_waehlen", {"suche": "Fremde Runde"})
