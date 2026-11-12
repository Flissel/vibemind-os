"""Tests fuer Events, Metadaten-Pruefung, Phase, Katalog und Verlauf (echte Postgres-DB)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from spaces.event_management.system import events, verlauf
from spaces.event_management.system.kontext import KeinRecht, Kontext, NichtGefunden

BERLIN = events.ZEITZONE


def _firma(admin: Any, kurz: str, name: str | None = None) -> UUID:
    return admin.execute(
        "insert into event.firma (name, kurz) values (%s, %s) returning id",
        (name or "Beispiel " + kurz, kurz),
    ).fetchone()["id"]


def _person(admin: Any, mail: str, name: str = "Erika Beispiel") -> UUID:
    return admin.execute(
        "insert into event.person (mail, name) values (%s, %s) returning id", (mail, name),
    ).fetchone()["id"]


def _k(p: UUID, rollen: dict[UUID, str], **kw: Any) -> Kontext:
    return Kontext(person_id=p, betreiber=False, rollen=rollen, **kw)


def _zeiten(tage: int = 10) -> tuple[datetime, datetime]:
    b = datetime.now(BERLIN).replace(microsecond=0) + timedelta(days=tage)
    return b, b + timedelta(hours=3)


def test_anlegen_mit_vererbten_feldern(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "bsp-a")
    p = _person(db_admin, "a@example.org")
    k = _k(p, {f: "leitung"})
    b, e = _zeiten()
    conn = db_app()
    row = events.event_anlegen(conn, k, f, "weihnachtsfeier", "Feier", b, e,
                               metadaten={"motto": "Winter", "kapazitaet": 40})
    assert row["metadaten"] == {"motto": "Winter", "kapazitaet": 40, "wichteln": False}
    assert row["status"] == "geplant"
    db = db_admin.execute("select metadaten from event.event where id=%s", (row["id"],)).fetchone()
    assert db["metadaten"]["wichteln"] is False


def test_unbekanntes_metadatenfeld_abgelehnt(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "bsp-a")
    k = _k(_person(db_admin, "a@example.org"), {f: "leitung"})
    b, e = _zeiten()
    with pytest.raises(ValueError, match="geheim"):
        events.event_anlegen(db_app(), k, f, "weihnachtsfeier", "X", b, e,
                             metadaten={"geheim": "x"})
    assert db_admin.execute("select count(*) as n from event.event").fetchone()["n"] == 0


def test_falscher_feldtyp_abgelehnt(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "bsp-a")
    k = _k(_person(db_admin, "a@example.org"), {f: "leitung"})
    b, e = _zeiten()
    conn = db_app()
    with pytest.raises(ValueError, match="kapazitaet"):
        events.event_anlegen(conn, k, f, "weihnachtsfeier", "X", b, e,
                             metadaten={"kapazitaet": "viele"})
    with pytest.raises(ValueError, match="kapazitaet"):
        events.event_anlegen(conn, k, f, "weihnachtsfeier", "X", b, e,
                             metadaten={"kapazitaet": True})
    with pytest.raises(ValueError, match="wichteln"):
        events.event_anlegen(conn, k, f, "weihnachtsfeier", "X", b, e,
                             metadaten={"wichteln": "ja"})


def test_webinar_ohne_link_abgelehnt(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "bsp-a")
    k = _k(_person(db_admin, "a@example.org"), {f: "leitung"})
    b, e = _zeiten()
    conn = db_app()
    with pytest.raises(ValueError, match="ort_link"):
        events.event_anlegen(conn, k, f, "webinar", "W", b, e)
    with pytest.raises(ValueError, match="plattform"):
        events.event_anlegen(conn, k, f, "webinar", "W", b, e, ort_link="https://example.org/w",
                             metadaten={"plattform": "Skype"})
    row = events.event_anlegen(conn, k, f, "webinar", "W", b, e, ort_link="https://example.org/w",
                               metadaten={"plattform": "Jitsi"})
    assert row["metadaten"]["anmeldung_noetig"] is True


def test_abstrakte_und_unbekannte_kategorie_abgelehnt(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "bsp-a")
    k = _k(_person(db_admin, "a@example.org"), {f: "leitung"})
    b, e = _zeiten()
    with pytest.raises(ValueError):
        events.event_anlegen(db_app(), k, f, "basis", "X", b, e)
    with pytest.raises(ValueError):
        events.event_anlegen(db_app(), k, f, "gibt_es_nicht", "X", b, e)
    with pytest.raises(ValueError):
        events.event_anlegen(db_app(), k, f, "fest", "X", e, b)


def test_aendern_regeln(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "bsp-a")
    k = _k(_person(db_admin, "a@example.org"), {f: "leitung"})
    b, e = _zeiten()
    conn = db_app()
    row = events.event_anlegen(conn, k, f, "weihnachtsfeier", "Feier", b, e)
    neu = events.event_aendern(conn, k, row["id"], name="Feier 2", metadaten={"motto": "Eis"})
    assert neu["name"] == "Feier 2"
    assert neu["metadaten"] == {"motto": "Eis", "wichteln": False}
    assert neu["geaendert"] >= row["geaendert"]
    for falsch in ({"kategorie": "fest"}, {"firma_id": f}, {"quelle": "legacy"}, {"foo": 1}):
        with pytest.raises(ValueError):
            events.event_aendern(conn, k, row["id"], **falsch)
    with pytest.raises(ValueError):
        events.event_aendern(conn, k, row["id"], ende=b - timedelta(days=1))
    with pytest.raises(ValueError, match="kapazitaet"):
        events.event_aendern(conn, k, row["id"], metadaten={"kapazitaet": "x"})
    w = events.event_anlegen(conn, k, f, "webinar", "W", b, e, ort_link="https://example.org/w")
    with pytest.raises(ValueError, match="ort_link"):
        events.event_aendern(conn, k, w["id"], ort_link=None)


def test_phase_beachtet_berliner_zeit(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "bsp-a")
    k = _k(_person(db_admin, "a@example.org"), {f: "leitung"})
    heute = datetime.now(BERLIN).date()
    beginn = datetime(heute.year, heute.month, heute.day, 18, 0)  # naiv = Berlin
    ende = datetime(heute.year, heute.month, heute.day, 23, 30)
    conn = db_app()
    row = events.event_anlegen(conn, k, f, "fest", "Spaet", beginn, ende)
    assert row["ende"] == ende.replace(tzinfo=BERLIN)
    laeuft = events.katalog(conn, k, jetzt=datetime(heute.year, heute.month, heute.day, 23, 0))
    assert laeuft[0]["phase"] == "laeuft"
    vorbei = events.katalog(conn, k, jetzt=datetime(heute.year, heute.month, heute.day, 23, 45))
    assert vorbei[0]["phase"] == "vergangen"
    frueh = events.katalog(conn, k, jetzt=datetime(heute.year, heute.month, heute.day, 12, 0,
                                                   tzinfo=BERLIN))
    assert frueh[0]["phase"] == "kommend"
    events.event_status(conn, k, row["id"], "abgesagt")
    assert events.katalog(conn, k, jetzt=datetime(heute.year, heute.month, heute.day, 12, 0)
                          )[0]["phase"] == "vergangen"


def test_katalog_sortierung_und_kennzahlen(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "bsp-a")
    k = _k(_person(db_admin, "a@example.org"), {f: "leitung"})
    jetzt = datetime(2030, 6, 15, 12, 0, tzinfo=BERLIN)
    h = timedelta(hours=1)
    conn = db_app()

    def neu(name: str, b: datetime, e: datetime) -> dict[str, Any]:
        return events.event_anlegen(conn, k, f, "fest", name, b, e)

    alt1 = neu("alt1", jetzt - 10 * 24 * h, jetzt - 9 * 24 * h)
    alt2 = neu("alt2", jetzt - 5 * 24 * h, jetzt - 4 * 24 * h)
    spaet = neu("spaet", jetzt + 20 * 24 * h, jetzt + 21 * 24 * h)
    bald = neu("bald", jetzt + 2 * 24 * h, jetzt + 3 * 24 * h)
    jetztev = neu("jetzt", jetzt - h, jetzt + h)
    for t, st in (("a", "offen"), ("b", "erledigt"), ("c", "in_arbeit")):
        db_admin.execute("insert into event.aufgabe (event_id, firma_id, titel, status)"
                         " values (%s,%s,%s,%s)", (bald["id"], f, t, st))
    for i, z in enumerate(("zugesagt", "zugesagt", "offen")):
        kid = db_admin.execute("insert into event.kontakt (firma_id, name) values (%s,%s)"
                               " returning id", (f, f"K{i}")).fetchone()["id"]
        db_admin.execute("insert into event.teilnehmende (event_id, firma_id, kontakt_id, zusage)"
                         " values (%s,%s,%s,%s)", (bald["id"], f, kid, z))
    rows = events.katalog(conn, k, jetzt=jetzt)
    assert [r["name"] for r in rows] == ["jetzt", "bald", "spaet", "alt2", "alt1"]
    assert [r["phase"] for r in rows] == ["laeuft", "kommend", "kommend", "vergangen", "vergangen"]
    by = {r["name"]: r for r in rows}
    assert by["bald"]["kennzahlen"] == {"aufgaben_offen": 2, "aufgaben_gesamt": 3,
                                        "teilnehmende_zugesagt": 2}
    assert by["alt1"]["kennzahlen"] == {"aufgaben_offen": 0, "aufgaben_gesamt": 0,
                                        "teilnehmende_zugesagt": 0}
    assert by["bald"]["kategorie_name"] == "Fest"
    assert {x["id"] for x in rows} == {alt1["id"], alt2["id"], spaet["id"], bald["id"],
                                       jetztev["id"]}
    assert [r["name"] for r in events.katalog(conn, k, kategorie="webinar", jetzt=jetzt)] == []


def test_suche_findet_firmenname(db_admin: Any, db_app: Any) -> None:
    f1 = _firma(db_admin, "bsp-a", "Alpenglueck GmbH")
    f2 = _firma(db_admin, "bsp-b", "Nordlicht AG")
    k = _k(_person(db_admin, "a@example.org"), {f1: "leitung", f2: "leitung"})
    b, e = _zeiten()
    conn = db_app()
    e1 = events.event_anlegen(conn, k, f1, "fest", "Sommerfest", b, e, ort_adresse="Hafenstr. 5")
    events.event_anlegen(conn, k, f2, "fest", "Herbstfest 100%", b, e, ort_adresse="Markt_1")

    def namen(s: str) -> list[str]:
        return [r["name"] for r in events.katalog(conn, k, suche=s)]

    assert namen("alpenGLUECK") == ["Sommerfest"]
    assert namen("hafen") == ["Sommerfest"]
    assert namen("HERBST") == ["Herbstfest 100%"]
    assert namen("%") == ["Herbstfest 100%"]
    assert namen("_") == ["Herbstfest 100%"]
    assert namen("\\") == []
    assert namen("fest") != []
    assert e1["id"] is not None


def test_katalog_nur_eigene_firmen(db_admin: Any, db_app: Any) -> None:
    f1, f2 = _firma(db_admin, "bsp-a"), _firma(db_admin, "bsp-b")
    p = _person(db_admin, "a@example.org")
    b, e = _zeiten()
    kb = Kontext(person_id=None, betreiber=True)
    conn = db_app()
    events.event_anlegen(conn, kb, f1, "fest", "Eigen", b, e)
    fremd = events.event_anlegen(conn, kb, f2, "fest", "Fremd", b, e)
    k = _k(p, {f1: "mitglied"})
    assert [r["name"] for r in events.katalog(conn, k)] == ["Eigen"]
    with pytest.raises(NichtGefunden):
        events.katalog(conn, k, firma_id=f2)
    with pytest.raises(NichtGefunden):
        events.event(conn, k, fremd["id"])
    assert {r["name"] for r in events.katalog(conn, kb)} == {"Eigen", "Fremd"}
    ev = events.event(conn, kb, fremd["id"])
    assert ev["schema"]["kategorie"] == "fest"
    assert "helferschichten" in ev["schema"]["bausteine"]
    assert any(x["id"] == "kapazitaet" for x in ev["schema"]["felder"])
    assert ev["schema"]["begriffe"]["aufgaben"] == "Aufgaben"


def test_verlauf_bei_anlegen_und_aendern(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "bsp-a")
    p = _person(db_admin, "a@example.org", "Erika Beispiel")
    b, e = _zeiten()
    conn = db_app()
    k = _k(p, {f: "leitung"}, quelle="api")
    row = events.event_anlegen(conn, k, f, "fest", "F", b, e)
    events.event_aendern(conn, k, row["id"], name="F2")
    k2 = _k(p, {f: "leitung"}, actor="Bot")
    events.event_status(conn, k2, row["id"], "abgesagt")
    eintraege = verlauf.lesen(conn, k, row["id"])
    assert [x["aktion"] for x in eintraege] == ["status_abgesagt", "geaendert", "angelegt"]
    assert eintraege[0]["actor"] == "Bot"
    assert eintraege[1]["actor"] == "Erika Beispiel"
    assert eintraege[1]["quelle"] == "api"
    assert eintraege[2]["baustein"] == "event"
    assert eintraege[2]["ziel_id"] == row["id"]
    assert [x["actor"] for x in verlauf.lesen(conn, k, row["id"], actor="Bot")] == ["Bot"]
    assert len(verlauf.lesen(conn, k, row["id"], limit=2)) == 2
    assert verlauf.lesen(conn, k, row["id"], seit=datetime.now(timezone.utc) + timedelta(hours=1)
                         ) == []
    # System-Akteur ohne Person und ohne actor
    sk = Kontext(person_id=None, betreiber=True, quelle="system")
    events.event_aendern(conn, sk, row["id"], name="F3")
    assert verlauf.lesen(conn, sk, row["id"])[0]["actor"] == "System"
    # Rollback nimmt auch den Verlauf zurueck: ungueltige Aenderung schreibt nichts
    n = len(verlauf.lesen(conn, k, row["id"]))
    with pytest.raises(ValueError):
        events.event_aendern(conn, k, row["id"], ende=b - timedelta(days=5))
    assert len(verlauf.lesen(conn, k, row["id"])) == n
    fremd = _k(p, {_firma(db_admin, "bsp-b"): "leitung"})
    with pytest.raises(NichtGefunden):
        verlauf.lesen(conn, fremd, row["id"])


def test_mitglied_darf_nicht_loeschen(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "bsp-a")
    p = _person(db_admin, "a@example.org")
    b, e = _zeiten()
    conn = db_app()
    leitung = _k(p, {f: "leitung"})
    mitglied = _k(p, {f: "mitglied"})
    row = events.event_anlegen(conn, leitung, f, "fest", "F", b, e)
    with pytest.raises(KeinRecht):
        events.event_anlegen(conn, mitglied, f, "fest", "G", b, e)
    with pytest.raises(KeinRecht):
        events.event_loeschen(conn, mitglied, row["id"])
    with pytest.raises(KeinRecht):
        events.event_status(conn, mitglied, row["id"], "abgesagt")
    assert events.event_status(conn, mitglied, row["id"], "laeuft")["status"] == "laeuft"
    assert events.event_aendern(conn, mitglied, row["id"], name="H")["name"] == "H"
    with pytest.raises(NichtGefunden):
        events.event_loeschen(conn, leitung, uuid4())
    events.event_loeschen(conn, leitung, row["id"])
    assert db_admin.execute("select count(*) as n from event.event").fetchone()["n"] == 0
    assert db_admin.execute("select count(*) as n from event.verlauf").fetchone()["n"] == 0
