"""API-Ergaenzungen fuer die Oberflaeche: Bausteine-Registry, Kontakte aendern/entfernen,
Rechte in /api/ich (FastAPI TestClient gegen den Wegwerf-Cluster)."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from spaces.event_management.system import api, zugang
from spaces.event_management.system.bausteine import REGISTRY
from spaces.event_management.system.mailer import SpeicherMailer

BASIS = "https://events.example.org"
PW = "ein-langes-passwort-1"
GEHEIM = b"api-testgeheimnis-0123456789abcdef"  # >= 32 Byte
BEGINN = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)

Fabrik = Callable[..., TestClient]


@pytest.fixture(autouse=True)
def _sperre_leeren() -> Iterator[None]:
    zugang._sperre_zuruecksetzen()
    yield
    zugang._sperre_zuruecksetzen()


def _firma(admin: Any, kurz: str) -> UUID:
    return admin.execute(
        "insert into event.firma (name, kurz) values (%s, %s) returning id",
        ("Beispiel " + kurz, kurz),
    ).fetchone()["id"]


def _person(admin: Any, mail: str, *, betreiber: bool = False) -> UUID:
    return admin.execute(
        "insert into event.person (mail, name, passwort_hash, betreiber)"
        " values (%s, %s, %s, %s) returning id",
        (mail, "Person " + mail.split("@")[0], zugang.passwort_hash(PW), betreiber),
    ).fetchone()["id"]


def _mitglied(admin: Any, person: UUID, firma: UUID, rolle: str) -> None:
    admin.execute("insert into event.mitgliedschaft values (%s, %s, %s)", (person, firma, rolle))


def _kontakt(admin: Any, firma: UUID, name: str) -> UUID:
    return admin.execute(
        "insert into event.kontakt (firma_id, name) values (%s, %s) returning id", (firma, name),
    ).fetchone()["id"]


def _kontakte(admin: Any) -> list[dict[str, Any]]:
    """Alle Kontakte aller Firmen (Superuser, ohne RLS)."""
    return admin.execute("select * from event.kontakt order by id").fetchall()


@pytest.fixture
def fabrik(app_dsn: str) -> Iterator[Fabrik]:
    """``fabrik(mail=None)``: neuer Client; mit Mail angemeldet und X-CSRF gesetzt."""
    app: FastAPI = api.erstelle_app(app_dsn, GEHEIM, SpeicherMailer(), BASIS,
                                    cookie_sicher=False)
    with TestClient(app):
        offen: list[TestClient] = []

        def neu(mail: str | None = None) -> TestClient:
            c = TestClient(app)
            offen.append(c)
            if mail is not None:
                r = c.post("/api/anmelden", json={"mail": mail, "passwort": PW})
                assert r.status_code == 200, r.text
                c.headers["X-CSRF"] = r.json()["csrf"]
            return c

        try:
            yield neu
        finally:
            for c in offen:
                c.close()


@pytest.fixture
def welt(db_admin: Any) -> dict[str, Any]:
    fa, fb = _firma(db_admin, "bsp-a"), _firma(db_admin, "bsp-b")
    for mail, firma, rolle in (("leitung-a@example.org", fa, "leitung"),
                               ("mitglied-a@example.org", fa, "mitglied"),
                               ("leitung-b@example.org", fb, "leitung")):
        _mitglied(db_admin, _person(db_admin, mail), firma, rolle)
    _person(db_admin, "betreiber@example.org", betreiber=True)
    ea = db_admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende)"
        " values (%s, 'weihnachtsfeier', 'Feier', %s, %s) returning id",
        (fa, BEGINN, BEGINN + timedelta(hours=3)),
    ).fetchone()["id"]
    return {"fa": fa, "fb": fb, "ea": ea,
            "ka": _kontakt(db_admin, fa, "Kontakt a"), "kb": _kontakt(db_admin, fb, "Kontakt b")}


# --- Bausteine --------------------------------------------------------------------


def test_bausteine_route_liefert_registry_mit_typen(welt: dict[str, Any],
                                                    fabrik: Fabrik) -> None:
    r = fabrik("mitglied-a@example.org").get("/api/bausteine")
    assert r.status_code == 200, r.text
    daten = r.json()
    assert set(daten) == set(REGISTRY)
    assert daten["aufgaben"]["felder"]["kategorie_id"] == "ref:aufgaben_kategorien"
    assert daten["orga_team"]["felder"]["kontakt_id"] == "kontakt"
    assert "betrag_cent" in daten["budget"]["pflicht"]
    assert daten["aufgaben_kategorien"]["baustein"] == "aufgaben"
    assert daten["budget"]["baustein"] == "budget"
    for name, t in REGISTRY.items():
        eintrag = daten[name]
        assert set(eintrag) == {"baustein", "felder", "pflicht", "reihenfolge"}
        assert eintrag["felder"] == dict(t.felder)
        assert eintrag["reihenfolge"] == list(t.felder)  # Einfuegereihenfolge
        assert eintrag["pflicht"] == sorted(t.pflicht)
    assert daten["ablauf"]["reihenfolge"][:2] == ["beginn", "ende"]


def test_bausteine_route_ohne_cookie_401(welt: dict[str, Any], fabrik: Fabrik) -> None:
    r = fabrik().get("/api/bausteine")
    assert r.status_code == 401
    assert "aufgaben" not in r.text


# --- Kontakte ---------------------------------------------------------------------


def test_kontakt_aendern_und_entfernen(welt: dict[str, Any], fabrik: Fabrik,
                                       db_admin: Any) -> None:
    fa, ka = welt["fa"], welt["ka"]
    leitung, mitglied = fabrik("leitung-a@example.org"), fabrik("mitglied-a@example.org")
    url = f"/api/firmen/{fa}/kontakte/{ka}"

    r = leitung.patch(url, json={"name": "Max Beispiel", "mail": "max@example.org"})
    assert r.status_code == 200, r.text
    assert (r.json()["id"], r.json()["name"], r.json()["mail"]) == (
        str(ka), "Max Beispiel", "max@example.org")
    r = mitglied.patch(url, json={"telegram": "@max_bsp"})
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["telegram"]) == ("Max Beispiel", "@max_bsp")

    vorher = _kontakte(db_admin)
    for daten in ({}, {"name": "   "}, {"name": None}, {"firma_id": str(welt["fb"])},
                  {"name": "a\x00b"}, {"name": "x" * 201}):
        assert leitung.patch(url, json=daten).status_code == 422, daten
    r = mitglied.delete(url)
    assert r.status_code == 403 and "Berechtigung" in r.json()["detail"]
    assert _kontakte(db_admin) == vorher

    benutzt = _kontakt(db_admin, fa, "Im Orga-Team")
    r = leitung.post(f"/api/events/{welt['ea']}/b/orga_team", json={"kontakt_id": str(benutzt)})
    assert r.status_code == 201, r.text
    r = leitung.delete(f"/api/firmen/{fa}/kontakte/{benutzt}")
    assert r.status_code == 422 and r.json() == {"detail": "Kontakt wird noch verwendet"}

    r = leitung.delete(url)
    assert r.status_code == 204 and r.content == b""
    assert [k["id"] for k in _kontakte(db_admin) if k["id"] == ka] == []
    assert leitung.delete(url).status_code == 404
    assert leitung.patch(url, json={"name": "Weg"}).status_code == 404


def test_kontakt_routen_mandantenmatrix(welt: dict[str, Any], fabrik: Fabrik,
                                        db_admin: Any) -> None:
    la = fabrik("leitung-a@example.org")
    vorher = _kontakte(db_admin)
    for firma in (welt["fb"], welt["fa"]):  # fremde Firma, und fremder Kontakt ueber A
        url = f"/api/firmen/{firma}/kontakte/{welt['kb']}"
        r = la.patch(url, json={"name": "Gekapert"})
        assert r.status_code == 404 and r.json() == {"detail": "Nicht gefunden"}, url
        r = la.delete(url)
        assert r.status_code == 404 and r.json() == {"detail": "Nicht gefunden"}, url
    assert _kontakte(db_admin) == vorher
    # Positivkontrolle: Leitung B aendert den eigenen Kontakt.
    r = fabrik("leitung-b@example.org").patch(
        f"/api/firmen/{welt['fb']}/kontakte/{welt['kb']}", json={"name": "Bea Beispiel"})
    assert r.status_code == 200 and r.json()["name"] == "Bea Beispiel"


# --- Rechte in /api/ich -----------------------------------------------------------


def test_ich_liefert_rechte(welt: dict[str, Any], fabrik: Fabrik, db_admin: Any) -> None:
    fa, fb = welt["fa"], welt["fb"]
    p = _person(db_admin, "doppelt@example.org")
    _mitglied(db_admin, p, fa, "leitung")
    _mitglied(db_admin, p, fb, "mitglied")
    ich = fabrik("doppelt@example.org").get("/api/ich").json()
    assert ich["rechte"] == {"event_anlegen": [str(fa)], "verwalten": [str(fa)]}

    ich = fabrik("mitglied-a@example.org").get("/api/ich").json()
    assert ich["rechte"] == {"event_anlegen": [], "verwalten": []}

    ich = fabrik("betreiber@example.org").get("/api/ich").json()
    alle = [f["id"] for f in ich["firmen"]]
    assert sorted(alle) == sorted([str(fa), str(fb)])
    assert ich["rechte"] == {"event_anlegen": alle, "verwalten": alle}

    # Die Anmeldung liefert dieselbe Form wie /api/ich.
    r = fabrik().post("/api/anmelden", json={"mail": "doppelt@example.org", "passwort": PW})
    assert r.json()["rechte"] == {"event_anlegen": [str(fa)], "verwalten": [str(fa)]}
