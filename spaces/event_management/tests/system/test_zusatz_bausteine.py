"""Zusatz-Bausteine (Registry + 003_zusatz_bausteine.sql) und Tabelle `nachricht`.

Fachschicht ueber `kern` (db_app ohne Kontext und db_admin), Schema-Checks direkt als Admin.
Die RLS-Mandantenmatrix der neuen Tabellen steht in test_rls.py, die Fachschicht-Matrix
in test_kern.py (beide laufen ueber alle Registry-Eintraege bzw. neuen Tabellen).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import psycopg
import pytest

from spaces.event_management.db import anwenden
from spaces.event_management.system import events, kern
from spaces.event_management.system.bausteine import REGISTRY, ZUSAETZE
from spaces.event_management.system.kontext import Kontext

BEGINN = datetime(2027, 6, 1, 10, 0, tzinfo=timezone.utc)

# Verbindlich aus dem Plan (Etappe 3, Task 1): Eintrag -> (Tabelle, Felder, Pflicht, Sortierung).
ERWARTET: dict[str, tuple[str, dict[str, str], set[str], str]] = {
    "wettbewerb_teams": ("wettbewerb_team",
                         {"name": "text", "thema": "text", "ansprech_id": "kontakt"},
                         {"name"}, "name, id"),
    "abgaben": ("abgabe",
                {"team_id": "ref:wettbewerb_teams", "titel": "text", "link": "url",
                 "eingereicht": "datumzeit"},
                {"team_id", "titel"}, "angelegt, id"),
    "jury": ("jury", {"kontakt_id": "kontakt", "rolle": "text"}, {"kontakt_id"},
             "angelegt, id"),
    "aussteller": ("aussteller",
                   {"name": "text", "ansprech_id": "kontakt",
                    "status": "auswahl:angefragt,zugesagt,abgesagt"},
                   {"name"}, "name, id"),
    "staende": ("stand",
                {"bezeichnung": "text", "aussteller_id": "ref:aussteller", "groesse": "text"},
                {"bezeichnung"}, "bezeichnung, id"),
    "sprecher": ("sprecher",
                 {"kontakt_id": "kontakt", "thema": "text",
                  "status": "auswahl:angefragt,zugesagt,abgesagt"},
                 {"kontakt_id"}, "angelegt, id"),
    "online_raum": ("online_raum", {"titel": "text", "link": "url", "zugang": "text"},
                    {"titel", "link"}, "angelegt, id"),
    "aufzeichnung": ("aufzeichnung",
                     {"titel": "text", "link": "url",
                      "status": "auswahl:geplant,aufgenommen,veroeffentlicht"},
                     {"titel"}, "angelegt, id"),
    "protokoll": ("protokoll_punkt",
                  {"punkt": "text", "notiz": "text", "verantwortlich_id": "kontakt"},
                  {"punkt"}, "angelegt, id"),
    "beschluesse": ("beschluss",
                    {"text": "text", "gefasst": "datumzeit", "verantwortlich_id": "kontakt"},
                    {"text"}, "angelegt, id"),
    "verpflegung": ("verpflegung",
                    {"titel": "text", "menge": "ganzzahl", "anbieter": "text",
                     "betrag_cent": "ganzzahl"},
                    {"titel"}, "angelegt, id"),
    "programm": ("programm_punkt",
                 {"titel": "text", "beginn": "datumzeit", "ende": "datumzeit",
                  "beschreibung": "text"},
                 {"titel"}, "beginn, id"),
    "helferschichten": ("helferschicht",
                        {"titel": "text", "beginn": "datumzeit", "ende": "datumzeit",
                         "kontakt_id": "kontakt", "bedarf": "ganzzahl"},
                        {"titel", "beginn", "ende"}, "beginn, id"),
    "buehnen": ("buehne", {"name": "text", "ort": "text"}, {"name"}, "name, id"),
}

# Kategorie, deren wirksames Schema den Baustein enthaelt (kategorien/*.yaml).
KATEGORIE = {
    "wettbewerb_teams": "hackathon", "abgaben": "hackathon", "jury": "hackathon",
    "aussteller": "messe", "staende": "messe",
    "sprecher": "online_event", "online_raum": "online_event", "aufzeichnung": "online_event",
    "protokoll": "team_meeting", "beschluesse": "team_meeting",
    "verpflegung": "veranstaltung", "programm": "veranstaltung",
    "helferschichten": "fest", "buehnen": "festival",
}

# Aus der Plan-Tabelle abgeleitet (nicht aus der Registry, die sie erst erfuellen muss).
KONTAKT_FELDER = sorted(
    (name, feld) for name, (_, felder, _, _) in ERWARTET.items()
    for feld, typ in felder.items() if typ == "kontakt")
URL_FELDER = sorted(
    (name, feld) for name, (_, felder, _, _) in ERWARTET.items()
    for feld, typ in felder.items() if typ == "url")


# --- Testdaten --------------------------------------------------------------------


def _firma(admin: Any, kurz: str) -> UUID:
    return admin.execute(
        "insert into event.firma (name, kurz) values (%s, %s) returning id",
        ("Beispiel " + kurz, kurz),
    ).fetchone()["id"]


def _event(admin: Any, firma: UUID, kategorie: str) -> UUID:
    return admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende)"
        " values (%s, %s, 'Beispiel-Event', %s, %s) returning id",
        (firma, kategorie, BEGINN, BEGINN + timedelta(hours=8)),
    ).fetchone()["id"]


def _kontakt(admin: Any, firma: UUID, name: str = "Max Muster") -> UUID:
    return admin.execute(
        "insert into event.kontakt (firma_id, name) values (%s, %s) returning id", (firma, name),
    ).fetchone()["id"]


def _team(admin: Any, firma: UUID, ev: UUID, name: str = "Team Beispiel") -> UUID:
    return admin.execute(
        "insert into event.wettbewerb_team (event_id, firma_id, name) values (%s, %s, %s)"
        " returning id", (ev, firma, name),
    ).fetchone()["id"]


def _aussteller(admin: Any, firma: UUID, ev: UUID) -> UUID:
    return admin.execute(
        "insert into event.aussteller (event_id, firma_id, name)"
        " values (%s, %s, 'Beispiel GmbH') returning id", (ev, firma),
    ).fetchone()["id"]


def _pflicht(baustein: str, admin: Any, firma: UUID, ev: UUID) -> dict[str, Any]:
    """Nur die Pflichtfelder (Werte wie aus JSON: UUIDs und Zeiten als Text)."""
    if baustein == "abgaben":
        return {"team_id": str(_team(admin, firma, ev)), "titel": "Prototyp"}
    if baustein in ("jury", "sprecher"):
        return {"kontakt_id": str(_kontakt(admin, firma))}
    if baustein == "helferschichten":
        return {"titel": "Einlass", "beginn": "2027-06-01T10:00:00",
                "ende": "2027-06-01T12:00:00"}
    return {
        "wettbewerb_teams": {"name": "Team Beispiel"},
        "aussteller": {"name": "Beispiel GmbH"},
        "staende": {"bezeichnung": "A1"},
        "online_raum": {"titel": "Hauptraum", "link": "https://meet.example.org/raum"},
        "aufzeichnung": {"titel": "Eröffnung"},
        "protokoll": {"punkt": "Begrüßung"},
        "beschluesse": {"text": "Budget freigegeben"},
        "verpflegung": {"titel": "Mittagessen"},
        "programm": {"titel": "Eröffnung"},
        "buehnen": {"name": "Hauptbühne"},
    }[baustein]


def _voll(baustein: str, admin: Any, firma: UUID, ev: UUID) -> dict[str, Any]:
    """Alle Felder des Eintrags mit gueltigen Werten (Python-Typen fuer den Vergleich)."""
    kt = _kontakt(admin, firma, "Volle Werte")
    spaeter = BEGINN + timedelta(hours=2)
    return {
        "wettbewerb_teams": {"name": "Team Beispiel", "thema": "Mobilität", "ansprech_id": kt},
        "abgaben": {"team_id": _team(admin, firma, ev), "titel": "Prototyp",
                    "link": "https://example.org/abgabe", "eingereicht": BEGINN},
        "jury": {"kontakt_id": kt, "rolle": "Vorsitz"},
        "aussteller": {"name": "Beispiel GmbH", "ansprech_id": kt, "status": "zugesagt"},
        "staende": {"bezeichnung": "A1", "aussteller_id": _aussteller(admin, firma, ev),
                    "groesse": "3 x 3 m"},
        "sprecher": {"kontakt_id": kt, "thema": "Barrierefreiheit", "status": "zugesagt"},
        "online_raum": {"titel": "Hauptraum", "link": "http://meet.example.org/raum",
                        "zugang": "Code 1234"},
        "aufzeichnung": {"titel": "Keynote", "link": "https://video.example.org/1",
                         "status": "veroeffentlicht"},
        "protokoll": {"punkt": "Begrüßung", "notiz": "kurz", "verantwortlich_id": kt},
        "beschluesse": {"text": "Budget freigegeben", "gefasst": BEGINN,
                        "verantwortlich_id": kt},
        "verpflegung": {"titel": "Mittagessen", "menge": 40, "anbieter": "Beispiel Catering",
                        "betrag_cent": 120000},
        "programm": {"titel": "Eröffnung", "beginn": BEGINN, "ende": spaeter,
                     "beschreibung": "Begrüßung aller Gäste"},
        "helferschichten": {"titel": "Einlass", "beginn": BEGINN, "ende": spaeter,
                            "kontakt_id": kt, "bedarf": 3},
        "buehnen": {"name": "Hauptbühne", "ort": "Halle 1"},
    }[baustein]


def _text_feld(baustein: str) -> str:
    return next(f for f, typ in REGISTRY[baustein].felder.items() if typ == "text")


def _anzahl(admin: Any, tabelle: str) -> int:
    return admin.execute(f"select count(*) as n from event.{tabelle}").fetchone()["n"]


@pytest.fixture
def welt(db_admin: Any) -> dict[str, Any]:
    fa, fb = _firma(db_admin, "bsp-a"), _firma(db_admin, "bsp-b")
    p = db_admin.execute(
        "insert into event.person (mail, name) values ('a@example.org', 'Erika Beispiel')"
        " returning id").fetchone()["id"]
    return {"fa": fa, "fb": fb, "k": Kontext(person_id=p, betreiber=False, rollen={fa: "leitung"})}


# --- Registry ---------------------------------------------------------------------


def test_registry_hat_alle_zusaetze() -> None:
    assert set(ZUSAETZE) <= {t.baustein or t.name for t in REGISTRY.values()}


def test_registry_zusaetze_wie_im_plan() -> None:
    assert set(ERWARTET) == set(ZUSAETZE)
    for name, (tabelle, felder, pflicht, sortierung) in ERWARTET.items():
        t = REGISTRY[name]
        assert (t.tabelle, dict(t.felder), set(t.pflicht), t.sortierung) == (
            tabelle, felder, pflicht, sortierung), name
        assert list(t.felder) == list(felder), name  # Anzeige-Reihenfolge
        assert (t.baustein or t.name) == name


def test_kategorie_zuordnung_passt_zum_schema() -> None:
    for baustein, kategorie in KATEGORIE.items():
        assert baustein in events.schema_fuer(kategorie, None).bausteine, baustein
    assert set(KATEGORIE) == set(ZUSAETZE)


# --- CRUD ueber kern --------------------------------------------------------------


@pytest.mark.parametrize("baustein", sorted(ZUSAETZE))
def test_zusatz_crud_ueber_kern(db_admin: Any, db_app: Any, welt: dict[str, Any],
                                baustein: str) -> None:
    conn, k, fa = db_app(), welt["k"], welt["fa"]
    t = REGISTRY[baustein]
    ev = _event(db_admin, fa, KATEGORIE[baustein])
    werte = _pflicht(baustein, db_admin, fa, ev)
    assert set(werte) == t.pflicht

    row = kern.anlegen(conn, k, ev, baustein, werte)
    assert row["event_id"] == ev and row["firma_id"] == fa
    assert [r["id"] for r in kern.liste(conn, k, ev, baustein)] == [row["id"]]
    feld = _text_feld(baustein)
    neu = kern.aendern(conn, k, ev, baustein, row["id"], {feld: "geändert"})
    assert neu[feld] == "geändert"
    kern.loeschen(conn, k, ev, baustein, row["id"])
    assert kern.liste(conn, k, ev, baustein) == []
    aktionen = db_admin.execute(
        "select aktion from event.verlauf where event_id = %s and baustein = %s"
        " and ziel_id = %s order by zeitpunkt, id", (ev, baustein, row["id"])).fetchall()
    assert [a["aktion"] for a in aktionen] == ["angelegt", "geaendert", "geloescht"]

    # Event ohne diesen Baustein im Schema: jede Operation abgelehnt, nichts geschrieben.
    anders = "hackathon" if KATEGORIE[baustein] == "team_meeting" else "team_meeting"
    ev_anders = _event(db_admin, fa, anders)
    zeile = db_admin.execute(
        f"insert into event.{t.tabelle} (event_id, firma_id, {', '.join(werte)})"
        f" values (%s, %s{', %s' * len(werte)}) returning id",
        [ev_anders, fa, *_pflicht(baustein, db_admin, fa, ev_anders).values()],
    ).fetchone()["id"]
    vorher = db_admin.execute(f"select * from event.{t.tabelle} order by id").fetchall()
    verlauf_vorher = _anzahl(db_admin, "verlauf")
    with pytest.raises(ValueError, match="gehört nicht zu Kategorie"):
        kern.liste(conn, k, ev_anders, baustein)
    with pytest.raises(ValueError, match="gehört nicht zu Kategorie"):
        kern.anlegen(conn, k, ev_anders, baustein, _pflicht(baustein, db_admin, fa, ev_anders))
    with pytest.raises(ValueError, match="gehört nicht zu Kategorie"):
        kern.aendern(conn, k, ev_anders, baustein, zeile, {feld: "x"})
    with pytest.raises(ValueError, match="gehört nicht zu Kategorie"):
        kern.loeschen(conn, k, ev_anders, baustein, zeile)
    assert db_admin.execute(f"select * from event.{t.tabelle} order by id").fetchall() == vorher
    assert _anzahl(db_admin, "verlauf") == verlauf_vorher


@pytest.mark.parametrize("baustein", sorted(ZUSAETZE))
def test_zusatz_alle_felder_rundweg(db_admin: Any, db_app: Any, welt: dict[str, Any],
                                    baustein: str) -> None:
    conn, k, fa = db_app(), welt["k"], welt["fa"]
    ev = _event(db_admin, fa, KATEGORIE[baustein])
    werte = _voll(baustein, db_admin, fa, ev)
    assert list(werte) == list(REGISTRY[baustein].felder)
    row = kern.anlegen(conn, k, ev, baustein, werte)
    assert {f: row[f] for f in werte} == werte
    # Optionale Felder lassen sich wieder leeren (Pflichtfelder nicht).
    optional = [f for f in werte if f not in REGISTRY[baustein].pflicht
                and not REGISTRY[baustein].felder[f].startswith("auswahl:")]
    if optional:
        leer = kern.aendern(conn, k, ev, baustein, row["id"], {f: None for f in optional})
        assert all(leer[f] is None for f in optional)
    for f in REGISTRY[baustein].pflicht:
        with pytest.raises(ValueError, match=f):
            kern.aendern(conn, k, ev, baustein, row["id"], {f: None})


def test_liste_sortierung(db_admin: Any, db_app: Any, welt: dict[str, Any]) -> None:
    conn, k, fa = db_app(), welt["k"], welt["fa"]
    ev = _event(db_admin, fa, "festival")
    for name in ("Zeltbühne", "Hauptbühne"):
        kern.anlegen(conn, k, ev, "buehnen", {"name": name})
    assert [r["name"] for r in kern.liste(conn, k, ev, "buehnen")] == ["Hauptbühne", "Zeltbühne"]
    for titel, stunde in (("Spät", 14), ("Früh", 9)):
        kern.anlegen(conn, k, ev, "programm",
                     {"titel": titel, "beginn": f"2027-06-01T{stunde:02d}:00:00"})
    assert [r["titel"] for r in kern.liste(conn, k, ev, "programm")] == ["Früh", "Spät"]


# --- Verweise ---------------------------------------------------------------------


def test_ref_nur_im_selben_event(db_admin: Any, db_app: Any, welt: dict[str, Any]) -> None:
    conn, k, fa, fb = db_app(), welt["k"], welt["fa"], welt["fb"]
    e1, e2 = _event(db_admin, fa, "hackathon"), _event(db_admin, fa, "hackathon")
    team_e2 = _team(db_admin, fa, e2)
    team_fremd = _team(db_admin, fb, _event(db_admin, fb, "hackathon"))
    for team in (team_e2, team_fremd):
        with pytest.raises(ValueError, match="^Verweis ungültig: team_id$") as e:
            kern.anlegen(conn, k, e1, "abgaben", {"team_id": str(team), "titel": "X"})
        assert str(team) not in str(e.value)
    assert _anzahl(db_admin, "abgabe") == 0
    assert _anzahl(db_admin, "verlauf") == 0

    # Positivkontrolle, dann Umhaengen auf ein fremdes Team: abgelehnt, Zeile unveraendert.
    team_e1 = _team(db_admin, fa, e1)
    abgabe = kern.anlegen(conn, k, e1, "abgaben", {"team_id": team_e1, "titel": "Prototyp"})
    with pytest.raises(ValueError, match="^Verweis ungültig: team_id$"):
        kern.aendern(conn, k, e1, "abgaben", abgabe["id"], {"team_id": team_e2})
    assert db_admin.execute("select team_id from event.abgabe").fetchone()["team_id"] == team_e1

    m1, m2 = _event(db_admin, fa, "messe"), _event(db_admin, fa, "messe")
    with pytest.raises(ValueError, match="^Verweis ungültig: aussteller_id$"):
        kern.anlegen(conn, k, m1, "staende",
                     {"bezeichnung": "A1", "aussteller_id": _aussteller(db_admin, fa, m2)})
    assert _anzahl(db_admin, "stand") == 0


def test_verwiesene_zeile_loeschen(db_admin: Any, db_app: Any, welt: dict[str, Any]) -> None:
    conn, k, fa = db_app(), welt["k"], welt["fa"]
    ev = _event(db_admin, fa, "hackathon")
    team = kern.anlegen(conn, k, ev, "wettbewerb_teams", {"name": "Team Beispiel"})
    kern.anlegen(conn, k, ev, "abgaben", {"team_id": team["id"], "titel": "Prototyp"})
    verlauf_vorher = _anzahl(db_admin, "verlauf")
    # Team mit Abgaben: kein stilles Mitloeschen, sondern eine klare Ablehnung.
    with pytest.raises(ValueError, match="^Eintrag wird noch verwendet$"):
        kern.loeschen(conn, k, ev, "wettbewerb_teams", team["id"])
    assert _anzahl(db_admin, "wettbewerb_team") == 1 and _anzahl(db_admin, "abgabe") == 1
    assert _anzahl(db_admin, "verlauf") == verlauf_vorher

    # Aussteller loeschen leert nur aussteller_id am Stand.
    messe = _event(db_admin, fa, "messe")
    aus = kern.anlegen(conn, k, messe, "aussteller", {"name": "Beispiel GmbH"})
    stand = kern.anlegen(conn, k, messe, "staende",
                         {"bezeichnung": "A1", "aussteller_id": str(aus["id"])})
    kern.loeschen(conn, k, messe, "aussteller", aus["id"])
    zeile = db_admin.execute("select event_id, aussteller_id from event.stand where id = %s",
                             (stand["id"],)).fetchone()
    assert zeile == {"event_id": messe, "aussteller_id": None}

    # Event loeschen raeumt Team und Abgabe gemeinsam ab.
    db_admin.execute("delete from event.event where id = %s", (ev,))
    assert _anzahl(db_admin, "wettbewerb_team") == 0 and _anzahl(db_admin, "abgabe") == 0


@pytest.mark.parametrize(("baustein", "feld"), KONTAKT_FELDER)
def test_kontakt_fremder_firma_abgelehnt(db_admin: Any, db_app: Any, welt: dict[str, Any],
                                         baustein: str, feld: str) -> None:
    conn, k, fa = db_app(), welt["k"], welt["fa"]
    ev = _event(db_admin, fa, KATEGORIE[baustein])
    fremd = _kontakt(db_admin, welt["fb"], "Fremd Beispiel")
    werte = {**_pflicht(baustein, db_admin, fa, ev), feld: str(fremd)}
    tabelle = REGISTRY[baustein].tabelle
    vorher = _anzahl(db_admin, tabelle)
    with pytest.raises(ValueError, match=f"^Verweis ungültig: {feld}$") as e:
        kern.anlegen(conn, k, ev, baustein, werte)
    assert str(fremd) not in str(e.value)
    assert _anzahl(db_admin, tabelle) == vorher
    assert _anzahl(db_admin, "verlauf") == 0
    # Positivkontrolle: Kontakt der eigenen Firma.
    eigen = _kontakt(db_admin, fa, "Eigen Beispiel")
    row = kern.anlegen(conn, k, ev, baustein, {**werte, feld: str(eigen)})
    assert row[feld] == eigen
    with pytest.raises(ValueError, match=f"^Verweis ungültig: {feld}$"):
        kern.aendern(conn, k, ev, baustein, row["id"], {feld: fremd})
    assert db_admin.execute(f"select {feld} from event.{tabelle} where id = %s",
                            (row["id"],)).fetchone()[feld] == eigen


# --- Werte und Checks -------------------------------------------------------------


@pytest.mark.parametrize(("baustein", "feld"), URL_FELDER)
@pytest.mark.parametrize("schlecht", ["ftp://example.org/x", "javascript:alert(1)",
                                      "example.org", "HTTPS://example.org", " https://x"])
def test_url_nur_http(db_admin: Any, db_app: Any, welt: dict[str, Any], baustein: str,
                      feld: str, schlecht: str) -> None:
    conn, k, fa = db_app(), welt["k"], welt["fa"]
    ev = _event(db_admin, fa, KATEGORIE[baustein])
    werte = _pflicht(baustein, db_admin, fa, ev)
    with pytest.raises(ValueError, match=feld):
        kern.anlegen(conn, k, ev, baustein, {**werte, feld: schlecht})
    tabelle = REGISTRY[baustein].tabelle
    assert _anzahl(db_admin, tabelle) == 0
    # Zweites Netz: der Check in der Tabelle (Admin umgeht die Fachschicht).
    spalten = {**werte, feld: schlecht}
    with pytest.raises(psycopg.errors.CheckViolation):
        db_admin.execute(
            f"insert into event.{tabelle} (event_id, firma_id, {', '.join(spalten)})"
            f" values (%s, %s{', %s' * len(spalten)})", [ev, fa, *spalten.values()])


def test_zeitspannen(db_admin: Any, db_app: Any, welt: dict[str, Any]) -> None:
    conn, k, fa = db_app(), welt["k"], welt["fa"]
    ev = _event(db_admin, fa, "fest")
    with pytest.raises(ValueError, match="Ende"):
        kern.anlegen(conn, k, ev, "helferschichten", {
            "titel": "Einlass", "beginn": "2027-06-01T12:00:00", "ende": "2027-06-01T10:00:00"})
    with pytest.raises(ValueError, match="Ende"):
        kern.anlegen(conn, k, ev, "programm", {
            "titel": "Eröffnung", "beginn": "2027-06-01T12:00:00",
            "ende": "2027-06-01T10:00:00"})
    # Ein Zeitpunkt allein ist im Programm erlaubt.
    kern.anlegen(conn, k, ev, "programm", {"titel": "Nur Ende", "ende": "2027-06-01T10:00:00"})
    schicht = kern.anlegen(conn, k, ev, "helferschichten", {
        "titel": "Einlass", "beginn": "2027-06-01T10:00:00", "ende": "2027-06-01T12:00:00"})
    # Nur das Ende aendern: die Tabelle prueft gegen den gespeicherten Beginn.
    with pytest.raises(ValueError):
        kern.aendern(conn, k, ev, "helferschichten", schicht["id"],
                     {"ende": "2027-06-01T09:00:00"})
    for tabelle, spalten in (("helferschicht", "titel, beginn, ende"),
                             ("programm_punkt", "titel, beginn, ende")):
        with pytest.raises(psycopg.errors.CheckViolation):
            db_admin.execute(
                f"insert into event.{tabelle} (event_id, firma_id, {spalten})"
                " values (%s, %s, 'X', %s, %s)", (ev, fa, BEGINN, BEGINN - timedelta(hours=1)))


@pytest.mark.parametrize(("baustein", "feld"), [
    ("verpflegung", "menge"), ("verpflegung", "betrag_cent"), ("helferschichten", "bedarf"),
])
def test_zahlen_nicht_negativ(db_admin: Any, db_app: Any, welt: dict[str, Any], baustein: str,
                              feld: str) -> None:
    conn, k, fa = db_app(), welt["k"], welt["fa"]
    ev = _event(db_admin, fa, KATEGORIE[baustein])
    werte = _pflicht(baustein, db_admin, fa, ev)
    for schlecht in (-1, True, 1.5, "3"):
        with pytest.raises(ValueError, match=feld):
            kern.anlegen(conn, k, ev, baustein, {**werte, feld: schlecht})
    assert kern.anlegen(conn, k, ev, baustein, {**werte, feld: 0})[feld] == 0
    spalten = {**werte, feld: -1}
    with pytest.raises(psycopg.errors.CheckViolation):
        db_admin.execute(
            f"insert into event.{REGISTRY[baustein].tabelle}"
            f" (event_id, firma_id, {', '.join(spalten)})"
            f" values (%s, %s{', %s' * len(spalten)})", [ev, fa, *spalten.values()])


def test_auswahl_vorgaben(db_admin: Any, db_app: Any, welt: dict[str, Any]) -> None:
    conn, k, fa = db_app(), welt["k"], welt["fa"]
    messe, online = _event(db_admin, fa, "messe"), _event(db_admin, fa, "online_event")
    assert kern.anlegen(conn, k, messe, "aussteller", {"name": "A"})["status"] == "angefragt"
    sp = kern.anlegen(conn, k, online, "sprecher", {"kontakt_id": _kontakt(db_admin, fa)})
    assert sp["status"] == "angefragt"
    assert kern.anlegen(conn, k, online, "aufzeichnung", {"titel": "A"})["status"] == "geplant"
    with pytest.raises(ValueError, match="status"):
        kern.anlegen(conn, k, messe, "aussteller", {"name": "A", "status": "vielleicht"})


# --- nachricht --------------------------------------------------------------------


def test_nachricht_tabelle(db_admin: Any, welt: dict[str, Any]) -> None:
    fa = welt["fa"]
    ev = _event(db_admin, fa, "fest")
    row = db_admin.execute(
        "insert into event.nachricht (event_id, firma_id, kanal, text)"
        " values (%s, %s, 'gruppe', 'Hallo zusammen') returning *", (ev, fa)).fetchone()
    assert row["status"] == "entwurf" and row["empfaenger_filter"] == {}
    assert row["angelegt"] is not None and row["betreff"] is None
    db_admin.execute(
        "update event.nachricht set status = 'freigegeben', freigegeben = now(),"
        " freigegeben_von = %s, empfaenger_filter = %s where id = %s",
        (welt["k"].person_id, '{"zusage": ["zugesagt"]}', row["id"]))
    for spalte, wert in (("kanal", "sms"), ("status", "gesendet")):
        with pytest.raises(psycopg.errors.CheckViolation):
            db_admin.execute(f"update event.nachricht set {spalte} = %s where id = %s",
                             (wert, row["id"]))
    with pytest.raises(psycopg.errors.NotNullViolation):
        db_admin.execute("insert into event.nachricht (event_id, firma_id, kanal)"
                         " values (%s, %s, 'mail')", (ev, fa))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db_admin.execute("update event.nachricht set freigegeben_von = gen_random_uuid()"
                         " where id = %s", (row["id"],))
    # Event einer fremden Firma: der zusammengesetzte Fremdschluessel haelt.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db_admin.execute(
            "insert into event.nachricht (event_id, firma_id, kanal, text)"
            " values (%s, %s, 'mail', 'X')", (ev, welt["fb"]))


# --- Migration --------------------------------------------------------------------


def test_migration_003_idempotent_ueber_anwenden(db_admin: Any) -> None:
    assert anwenden.anwenden(db_admin) == []
    zeilen = db_admin.execute("select nr, name from event.migration order by nr").fetchall()
    assert (3, "003_zusatz_bausteine.sql") in [(z["nr"], z["name"]) for z in zeilen]

    def stand() -> list[Any]:
        return db_admin.execute(
            "select tablename, policyname, qual, with_check from pg_policies"
            " where schemaname = 'event' order by 1, 2").fetchall()

    vorher = stand()
    # Die Datei selbst ist wiederholbar (if not exists / drop policy if exists).
    pfad = next(p for nr, _, p in anwenden.migrationen() if nr == 3)
    with db_admin.transaction():
        db_admin.execute(pfad.read_text(encoding="utf-8"))  # type: ignore[call-overload]
    assert stand() == vorher
    assert anwenden.anwenden(db_admin) == []


def test_migration_003_auf_leerer_db(leere_db_dsn: str) -> None:
    with psycopg.connect(leere_db_dsn, autocommit=True) as conn:
        neu = anwenden.anwenden(conn)
        assert neu[:3] == ["001_schema.sql", "002_rls.sql", "003_zusatz_bausteine.sql"]
        assert anwenden.anwenden(conn) == []
