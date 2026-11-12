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


# --- Fremde Firma: RLS (zweites Tor) und Fachschicht (erstes Tor) ------------------


class _Stolperdraht:
    """Verbindungs-Attrappe: jeder Zugriff laesst den Test scheitern."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"SQL-Zugriff vor der Pruefung: {name}")


def _event_admin(admin: Any, f: UUID, name: str = "Feier") -> UUID:
    b, e = _zeiten()
    return admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende)"
        " values (%s, 'fest', %s, %s, %s) returning id", (f, name, b, e),
    ).fetchone()["id"]


def _verlauf_admin(admin: Any, ev: UUID, f: UUID) -> None:
    admin.execute(
        "insert into event.verlauf (event_id, firma_id, actor, quelle, baustein, aktion)"
        " values (%s, %s, 'Erika', 'test', 'event', 'angelegt')", (ev, f))


def _stand(admin: Any) -> tuple[list[Any], list[Any]]:
    return (admin.execute("select * from event.event order by id").fetchall(),
            admin.execute("select * from event.verlauf order by id").fetchall())


@pytest.fixture
def welt(db_admin: Any) -> dict[str, Any]:
    fa, fb = _firma(db_admin, "bsp-a"), _firma(db_admin, "bsp-b")
    p = _person(db_admin, "a@example.org")
    w = {"fa": fa, "fb": fb, "p": p, "k": _k(p, {fa: "leitung"}),
         "ea": _event_admin(db_admin, fa, "Eigen"), "ea2": _event_admin(db_admin, fa, "Zweit"),
         "eb": _event_admin(db_admin, fb, "Fremd")}
    for ev, f in ((w["ea"], fa), (w["ea2"], fa), (w["eb"], fb)):
        _verlauf_admin(db_admin, ev, f)
    return w


EVENT_OPS = ["event", "event_aendern", "event_status", "event_loeschen", "verlauf_lesen"]


def _event_op(conn: Any, k: Kontext, op: str, event_id: UUID) -> Any:
    if op == "event":
        return events.event(conn, k, event_id)
    if op == "event_aendern":
        return events.event_aendern(conn, k, event_id, name="Umbenannt")
    if op == "event_status":
        return events.event_status(conn, k, event_id, "abgesagt")
    if op == "event_loeschen":
        return events.event_loeschen(conn, k, event_id)
    if op == "verlauf_lesen":
        return verlauf.lesen(conn, k, event_id)
    raise AssertionError(op)


@pytest.mark.parametrize("schicht", ["rls", "fachschicht"])
@pytest.mark.parametrize("op", EVENT_OPS)
def test_mandantenmatrix_events(db_admin: Any, db_app: Any, welt: dict[str, Any],
                                op: str, schicht: str) -> None:
    """Leitung A gegen Event B. `rls`: Verbindung sieht nur A (zweites Tor).
    `fachschicht`: Superuser-Verbindung ohne RLS, nur die Fachschicht haelt (erstes Tor)."""
    conn = db_app(firmen=[welt["fa"]]) if schicht == "rls" else db_admin
    vorher = _stand(db_admin)
    with pytest.raises(NichtGefunden):
        _event_op(conn, welt["k"], op, welt["eb"])
    assert _stand(db_admin) == vorher
    _event_op(conn, welt["k"], op, welt["ea"])  # Positivkontrolle: eigene Firma geht


@pytest.mark.parametrize("schicht", ["rls", "fachschicht"])
def test_katalog_ohne_firma_nur_eigene_matrix(db_admin: Any, db_app: Any, welt: dict[str, Any],
                                              schicht: str) -> None:
    conn = db_app(firmen=[welt["fa"]]) if schicht == "rls" else db_admin
    assert {r["id"] for r in events.katalog(conn, welt["k"])} == {welt["ea"], welt["ea2"]}
    with pytest.raises(NichtGefunden):
        events.katalog(conn, welt["k"], firma_id=welt["fb"])


@pytest.mark.parametrize("op", EVENT_OPS)
def test_event_bindung_fachschicht(db_admin: Any, welt: dict[str, Any], op: str) -> None:
    """Kontext auf Event A1 gebunden: Event A2 derselben Firma ist unsichtbar (ohne RLS)."""
    k = _k(welt["p"], {welt["fa"]: "leitung"}, event_id=welt["ea"])
    vorher = _stand(db_admin)
    with pytest.raises(NichtGefunden):
        _event_op(db_admin, k, op, welt["ea2"])
    assert _stand(db_admin) == vorher
    _event_op(db_admin, k, op, welt["ea"])  # Positivkontrolle: das gebundene Event geht


def test_event_bindung_katalog_und_anlegen(db_admin: Any, welt: dict[str, Any]) -> None:
    k = _k(welt["p"], {welt["fa"]: "leitung"}, event_id=welt["ea"])
    assert [r["id"] for r in events.katalog(db_admin, k)] == [welt["ea"]]
    assert [r["id"] for r in events.katalog(db_admin, k, firma_id=welt["fa"])] == [welt["ea"]]
    b, e = _zeiten()
    stolper: Any = _Stolperdraht()
    with pytest.raises(KeinRecht):
        events.event_anlegen(stolper, k, welt["fa"], "fest", "Neu", b, e)


# --- ort_link nur http(s) -------------------------------------------------------------


@pytest.mark.parametrize("link", [
    "javascript:alert(1)", "JAVASCRIPT:alert(1)", "data:text/html,x", "ftp://example.org/x",
    "example.org", "", " https://example.org", "//example.org",
])
def test_ort_link_nur_http_oder_https(db_admin: Any, db_app: Any, link: str) -> None:
    f = _firma(db_admin, "bsp-a")
    k = _k(_person(db_admin, "a@example.org"), {f: "leitung"})
    b, e = _zeiten()
    conn = db_app()
    with pytest.raises(ValueError, match="ort_link") as fehler:
        events.event_anlegen(conn, k, f, "fest", "F", b, e, ort_link=link)
    assert not link or link not in str(fehler.value)
    row = events.event_anlegen(conn, k, f, "fest", "F", b, e, ort_link="https://example.org/a")
    with pytest.raises(ValueError, match="ort_link"):
        events.event_aendern(conn, k, row["id"], ort_link=link)
    assert events.event_aendern(conn, k, row["id"], ort_link="http://example.org/b"
                                )["ort_link"] == "http://example.org/b"
    assert events.event_aendern(conn, k, row["id"], ort_link=None)["ort_link"] is None
    assert db_admin.execute("select count(*) as n from event.event").fetchone()["n"] == 1


# --- Verlauf: actor ist Anzeige, actor_person_id ist beweiskraeftig ----------------


def test_verlauf_actor_person_id_trennt_gleichnamige(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "bsp-a")
    echt = _person(db_admin, "echt@example.org", "Erika Beispiel")
    falsch = _person(db_admin, "falsch@example.org", "VibeMind")  # nennt sich wie das System
    b, e = _zeiten()
    conn = db_app()
    row = events.event_anlegen(conn, _k(echt, {f: "leitung"}), f, "fest", "F", b, e)
    eid = row["id"]
    events.event_aendern(conn, _k(falsch, {f: "leitung"}), eid, name="Von der Person")
    system = Kontext(person_id=None, betreiber=True, actor="VibeMind", quelle="werkzeug_server")
    events.event_aendern(conn, system, eid, name="Vom System")
    k = _k(echt, {f: "leitung"})
    alle = verlauf.lesen(conn, k, eid)
    assert [(x["actor"], x["actor_person_id"]) for x in alle] == [
        ("VibeMind", None), ("VibeMind", falsch), ("Erika Beispiel", echt)]
    # Der Name ist nicht beweiskraeftig: beide "VibeMind"-Eintraege passen.
    assert len(verlauf.lesen(conn, k, eid, actor="VibeMind")) == 2
    nur_person = verlauf.lesen(conn, k, eid, actor_person_id=falsch)
    assert [(x["aktion"], x["actor_person_id"]) for x in nur_person] == [("geaendert", falsch)]
    assert [x["aktion"] for x in verlauf.lesen(conn, k, eid, actor_person_id=echt)] == [
        "angelegt"]
    assert verlauf.lesen(conn, k, eid, actor_person_id=_person(db_admin, "c@example.org")) == []


# --- Status: Absage/Archiv zuruecknehmen nur mit event_absagen --------------------


@pytest.mark.parametrize("endstatus", ["abgesagt", "archiviert"])
def test_mitglied_kann_absage_nicht_zuruecknehmen(db_admin: Any, db_app: Any,
                                                  endstatus: str) -> None:
    f = _firma(db_admin, "bsp-a")
    p = _person(db_admin, "a@example.org")
    leitung, mitglied = _k(p, {f: "leitung"}), _k(p, {f: "mitglied"})
    b, e = _zeiten()
    conn = db_app()
    row = events.event_anlegen(conn, leitung, f, "fest", "F", b, e)
    events.event_status(conn, leitung, row["id"], endstatus)
    for ziel in ("geplant", "laeuft", "entwurf"):
        with pytest.raises(KeinRecht):
            events.event_status(conn, mitglied, row["id"], ziel)
    assert db_admin.execute("select status from event.event where id = %s",
                            (row["id"],)).fetchone()["status"] == endstatus
    assert events.event_status(conn, leitung, row["id"], "geplant")["status"] == "geplant"
