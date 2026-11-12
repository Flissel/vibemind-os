"""Tests fuer die event-api (FastAPI TestClient gegen den Wegwerf-Cluster, SpeicherMailer)."""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from spaces.event_management.system import api, events, zugang
from spaces.event_management.system.bausteine import REGISTRY
from spaces.event_management.system.kategorien import KategorieFehler
from spaces.event_management.system.mailer import SpeicherMailer

BASIS = "https://events.example.org"
PW = "ein-langes-passwort-1"
GEHEIM = b"api-testgeheimnis-0123456789abcdef"  # >= 32 Byte
BEGINN = datetime(2027, 3, 1, 18, 0, tzinfo=timezone.utc)
OEFFENTLICH = {("POST", "/api/anmelden"), ("GET", "/api/einladung/{token}"),
               ("POST", "/api/einladung/{token}")}
SCHREIBEND = {"POST", "PATCH", "PUT", "DELETE"}

Fabrik = Callable[..., TestClient]


@pytest.fixture(autouse=True)
def _sperre_leeren() -> Iterator[None]:
    zugang._sperre_zuruecksetzen()
    yield
    zugang._sperre_zuruecksetzen()


# --- Testdaten --------------------------------------------------------------------


def _firma(admin: Any, kurz: str) -> UUID:
    return admin.execute(
        "insert into event.firma (name, kurz) values (%s, %s) returning id",
        ("Beispiel " + kurz, kurz),
    ).fetchone()["id"]


def _person(admin: Any, mail: str, *, betreiber: bool = False, pw: str | None = PW) -> UUID:
    h = zugang.passwort_hash(pw) if pw is not None else None
    return admin.execute(
        "insert into event.person (mail, name, passwort_hash, betreiber)"
        " values (%s, %s, %s, %s) returning id",
        (mail, "Person " + mail.split("@")[0], h, betreiber),
    ).fetchone()["id"]


def _mitglied(admin: Any, person: UUID, firma: UUID, rolle: str) -> None:
    admin.execute("insert into event.mitgliedschaft values (%s, %s, %s)", (person, firma, rolle))


def _event(admin: Any, firma: UUID, kategorie: str = "weihnachtsfeier") -> UUID:
    return admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende)"
        " values (%s, %s, 'Feier', %s, %s) returning id",
        (firma, kategorie, BEGINN, BEGINN + timedelta(hours=3)),
    ).fetchone()["id"]


def _kontakt(admin: Any, firma: UUID, name: str) -> UUID:
    return admin.execute(
        "insert into event.kontakt (firma_id, name) values (%s, %s) returning id", (firma, name),
    ).fetchone()["id"]


def _admin_zeile(admin: Any, baustein: str, firma: UUID, event: UUID, kontakt: UUID) -> UUID:
    werte: dict[str, Any] = {
        "aufgaben": {"titel": "Aufgabe"},
        "aufgaben_kategorien": {"name": "Kat"},
        "orga_team": {"kontakt_id": kontakt},
        "teilnehmende": {"kontakt_id": kontakt},
        "ablauf": {"titel": "Punkt", "beginn": BEGINN},
        "budget": {"titel": "Posten", "art": "kosten", "betrag_cent": 5},
    }[baustein]
    spalten = ["event_id", "firma_id", *werte]
    return admin.execute(
        f"insert into event.{REGISTRY[baustein].tabelle} ({', '.join(spalten)})"
        f" values ({', '.join(['%s'] * len(spalten))}) returning id",
        [event, firma, *werte.values()],
    ).fetchone()["id"]


def _neue_werte(baustein: str, kontakt: UUID) -> dict[str, Any]:
    return {
        "aufgaben": {"titel": "Neue Aufgabe", "status": "in_arbeit"},
        "aufgaben_kategorien": {"name": "Neue Kategorie"},
        "orga_team": {"kontakt_id": str(kontakt), "rolle": "Technik"},
        "teilnehmende": {"kontakt_id": str(kontakt), "zusage": "zugesagt"},
        "ablauf": {"titel": "Neuer Punkt", "beginn": "2027-03-01T19:00:00"},
        "budget": {"titel": "Neuer Posten", "art": "kosten", "betrag_cent": 1500},
    }[baustein]


def _aenderung(baustein: str) -> dict[str, Any]:
    felder = REGISTRY[baustein].felder
    if "zusage" in felder:
        return {"zusage": "abgesagt"}
    feld = "titel" if "titel" in felder else "name" if "name" in felder else "rolle"
    return {feld: "geaendert"}


def _stand(admin: Any) -> dict[str, list[str]]:
    """Alle Zeilen aller Tabellen im Schema event (Superuser, ohne RLS)."""
    tabellen = [r["table_name"] for r in admin.execute(
        "select table_name from information_schema.tables where table_schema = 'event'"
        " and table_type = 'BASE TABLE' and table_name <> 'migration' order by 1")]
    assert {"firma", "person", "event", "verlauf", "einladung", "kontakt"} <= set(tabellen)
    return {t: sorted(r["z"] for r in admin.execute(
        f"select row_to_json(x)::text as z from event.{t} x")) for t in tabellen}


def _token_aus(m: SpeicherMailer) -> str:
    treffer = re.search(r"/einladung/([A-Za-z0-9_-]+)", m.gesendet[-1].text)
    assert treffer is not None
    return treffer.group(1)


# --- App und Clients --------------------------------------------------------------


@pytest.fixture
def mailer() -> SpeicherMailer:
    return SpeicherMailer()


@pytest.fixture
def app(app_dsn: str, mailer: SpeicherMailer) -> FastAPI:
    return api.erstelle_app(app_dsn, GEHEIM, mailer, BASIS, cookie_sicher=False)


@pytest.fixture
def fabrik(app: FastAPI) -> Iterator[Fabrik]:
    """``fabrik(mail=None)``: neuer Client; mit Mail angemeldet und X-CSRF gesetzt.

    Der erste Client laeuft als Kontextmanager (Lifespan oeffnet/schliesst den Pool),
    die weiteren teilen sich dieselbe App und damit denselben Pool.
    """
    with TestClient(app) as haupt:
        offen: list[TestClient] = []

        def neu(mail: str | None = None, pw: str = PW) -> TestClient:
            c = TestClient(app)
            offen.append(c)
            if mail is not None:
                r = c.post("/api/anmelden", json={"mail": mail, "passwort": pw})
                assert r.status_code == 200, r.text
                c.headers["X-CSRF"] = r.json()["csrf"]
            return c

        try:
            yield neu
        finally:
            for c in offen:
                c.close()
            assert haupt is not None


@pytest.fixture
def welt(db_admin: Any, fabrik: Fabrik) -> dict[str, Any]:
    fa, fb = _firma(db_admin, "bsp-a"), _firma(db_admin, "bsp-b")
    la = _person(db_admin, "leitung-a@example.org")
    ma = _person(db_admin, "mitglied-a@example.org")
    lb = _person(db_admin, "leitung-b@example.org")
    mb = _person(db_admin, "mitglied-b@example.org")
    _person(db_admin, "betreiber@example.org", betreiber=True)
    _mitglied(db_admin, la, fa, "leitung")
    _mitglied(db_admin, ma, fa, "mitglied")
    _mitglied(db_admin, lb, fb, "leitung")
    _mitglied(db_admin, mb, fb, "mitglied")
    ea, eb = _event(db_admin, fa), _event(db_admin, fb)
    w: dict[str, Any] = {"fa": fa, "fb": fb, "la": la, "ma": ma, "lb": lb, "mb": mb,
                         "ea": ea, "eb": eb}
    for seite, firma, ev in (("a", fa, ea), ("b", fb, eb)):
        k = _kontakt(db_admin, firma, "Kontakt " + seite)
        w["k" + seite + "_frei"] = _kontakt(db_admin, firma, "Frei " + seite)
        w["z" + seite] = {b: _admin_zeile(db_admin, b, firma, ev, k) for b in REGISTRY}
    w["c"] = {"la": fabrik("leitung-a@example.org"), "betreiber": fabrik("betreiber@example.org")}
    return w


# --- Ablauf -----------------------------------------------------------------------


def test_ablauf_einladen_einloesen_anmelden(
    db_admin: Any, fabrik: Fabrik, mailer: SpeicherMailer
) -> None:
    _person(db_admin, "betreiber@example.org", betreiber=True)
    b = fabrik("betreiber@example.org")
    ich = b.get("/api/ich").json()
    assert ich["betreiber"] is True and ich["person"]["mail"] == "betreiber@example.org"
    r = b.post("/api/firmen", json={"name": "Beispiel GmbH", "kurz": "beispiel"})
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    assert b.post("/api/firmen", json={"name": "Andere GmbH", "kurz": "andere"}).status_code == 201

    r = b.post(f"/api/firmen/{fid}/einladungen",
               json={"mail": "lea@example.org", "name": "Lea Beispiel", "rolle": "leitung"},
               headers={"Host": "angreifer.example.net"})
    assert r.status_code == 201, r.text
    assert mailer.gesendet[-1].an == "lea@example.org"
    token = _token_aus(mailer)
    # Der Link kommt aus der Konfiguration, nie aus dem Host-Header der Anfrage.
    assert f"{BASIS}/einladung/{token}" in mailer.gesendet[-1].text
    assert "angreifer" not in mailer.gesendet[-1].text

    anonym = fabrik()
    assert anonym.get(f"/api/einladung/{token}").json() == {
        "gueltig": True, "firma_name": "Beispiel GmbH", "braucht_passwort": True}
    r = anonym.post(f"/api/einladung/{token}", json={"passwort": PW, "name": "Lea B."})
    assert r.status_code == 200, r.text
    assert "set-cookie" not in r.headers  # Einloesen meldet nicht an
    assert anonym.get(f"/api/einladung/{token}").json() == {"gueltig": False}
    r = anonym.post(f"/api/einladung/{token}", json={"passwort": PW})
    assert r.status_code == 422 and r.json() == {"detail": "Einladung ungültig oder abgelaufen"}

    lea = fabrik("lea@example.org")
    ich = lea.get("/api/ich").json()
    assert ich["betreiber"] is False and ich["person"]["name"] == "Lea B."
    assert ich["firmen"] == [{"id": fid, "name": "Beispiel GmbH", "kurz": "beispiel",
                              "rolle": "leitung"}]
    assert ich["csrf"] == lea.headers["X-CSRF"]
    assert [f["id"] for f in lea.get("/api/firmen").json()] == [fid]
    mitglieder = lea.get(f"/api/firmen/{fid}/mitglieder").json()
    assert [(m["mail"], m["rolle"]) for m in mitglieder] == [("lea@example.org", "leitung")]
    assert {f["kurz"] for f in b.get("/api/firmen").json()} == {"beispiel", "andere"}


def test_cookie_attribute(db_admin: Any, app_dsn: str) -> None:
    _person(db_admin, "erika@example.org")
    for sicher in (False, True):
        app = api.erstelle_app(app_dsn, GEHEIM, SpeicherMailer(), BASIS, cookie_sicher=sicher)
        with TestClient(app) as c:
            r = c.post("/api/anmelden", json={"mail": "erika@example.org", "passwort": PW})
        assert r.status_code == 200
        kopf = r.headers["set-cookie"]
        teile = {t.strip().split("=")[0].lower() for t in kopf.split(";")}
        assert kopf.startswith("event_sitzung=")
        assert {"httponly", "path", "max-age", "samesite"} <= teile
        assert "max-age=43200" in kopf.lower() and "samesite=lax" in kopf.lower()
        assert "path=/" in kopf.lower()
        assert ("secure" in teile) is sicher


def test_anmelden_fehler_einheitlich(db_admin: Any, fabrik: Fabrik) -> None:
    _person(db_admin, "finn@example.org")
    _person(db_admin, "ohne-pw@example.org", pw=None)
    c = fabrik()
    antworten = [
        c.post("/api/anmelden", json={"mail": "finn@example.org", "passwort": "falsch-falsch"}),
        c.post("/api/anmelden", json={"mail": "nie@example.org", "passwort": PW}),
        c.post("/api/anmelden", json={"mail": "ohne-pw@example.org", "passwort": PW}),
        c.post("/api/anmelden", json={"mail": "fi\x00nn@example.org", "passwort": PW}),
    ]
    # Einzelnes Surrogat lehnt schon pydantic ab (422, unabhaengig vom Konto, ohne Echo).
    r = c.post("/api/anmelden", headers={"Content-Type": "application/json"},
               content='{"mail": "fi\\ud800nn@example.org", "passwort": "%s"}' % PW)
    assert r.status_code == 422 and PW not in r.text and "nn@example" not in r.text
    for _ in range(5):
        c.post("/api/anmelden", json={"mail": "finn@example.org", "passwort": "falsch-falsch"})
    antworten.append(  # gesperrt, obwohl jetzt richtig
        c.post("/api/anmelden", json={"mail": "finn@example.org", "passwort": PW}))
    for r in antworten:
        assert r.status_code == 401, r.text
        assert r.json() == {"detail": "Anmeldung fehlgeschlagen"}
        assert "set-cookie" not in r.headers


def test_validierungsfehler_ohne_eingabe_im_body(fabrik: Fabrik) -> None:
    c = fabrik()
    r = c.post("/api/anmelden", json={"mail": "x@example.org", "passwort": 12345678901234})
    assert r.status_code == 422
    assert "12345678901234" not in r.text
    r = c.post("/api/anmelden", json={"mail": "x@example.org", "passwort": PW, "extra": 1})
    assert r.status_code == 422


def test_ohne_cookie_401(app: FastAPI, fabrik: Fabrik, db_admin: Any) -> None:
    c = fabrik()
    geprueft = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for methode in route.methods:
            if (methode, route.path) in OEFFENTLICH:
                continue
            r = c.request(methode, _pfad(route.path), json={})
            assert r.status_code == 401, (methode, route.path, r.text)
            geprueft.add((methode, route.path))
    assert ("GET", "/api/ich") in geprueft and ("POST", "/api/abmelden") in geprueft
    assert len(geprueft) == 23  # neue Route? dann hier bewusst nachziehen
    # Gefaelschter oder kaputter Cookie
    p = _person(db_admin, "eva@example.org")
    echt = zugang.cookie_ausstellen(p, 1, GEHEIM)
    for wert in ("kaputt", zugang.cookie_ausstellen(p, 1, b"x" * 32), echt + "x",
                 zugang.cookie_ausstellen(p, 2, GEHEIM)):
        c.cookies.set("event_sitzung", wert)
        assert c.get("/api/ich").status_code == 401
    c.cookies.set("event_sitzung", echt)
    assert c.get("/api/ich").status_code == 200


def _pfad(muster: str) -> str:
    def ersetzen(m: re.Match[str]) -> str:
        return {"baustein": "aufgaben", "token": "x" * 43}.get(m.group(1), str(uuid4()))
    return re.sub(r"\{([a-z_]+)\}", ersetzen, muster)


def test_ohne_csrf_403(app: FastAPI, welt: dict[str, Any], db_admin: Any,
                       fabrik: Fabrik) -> None:
    la: TestClient = welt["c"]["la"]
    csrf = la.headers.pop("X-CSRF")
    geprueft = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for methode in route.methods & SCHREIBEND:
            if (methode, route.path) in OEFFENTLICH:
                continue
            r = la.request(methode, _pfad(route.path), json={})
            assert r.status_code == 403, (methode, route.path, r.text)
            geprueft.add((methode, route.path))
    assert len(geprueft) == 13 and ("POST", "/api/abmelden") in geprueft

    vorher = _stand(db_admin)
    url = f"/api/firmen/{welt['fa']}/kontakte"
    fremder_csrf = welt["c"]["betreiber"].headers["X-CSRF"]
    for kopf in ({}, {"X-CSRF": ""}, {"X-CSRF": csrf + "x"}, {"X-CSRF": fremder_csrf}):
        assert la.post(url, json={"name": "Neu"}, headers=kopf).status_code == 403
    assert la.post(f"/api/events/{welt['ea']}/b/aufgaben", json={"titel": "X"}).status_code == 403
    assert la.post("/api/abmelden").status_code == 403
    assert _stand(db_admin) == vorher
    assert la.get("/api/ich").status_code == 200  # nicht abgemeldet
    r = la.post(url, json={"name": "Neu"}, headers={"X-CSRF": csrf})
    assert r.status_code == 201, r.text


def test_abmelden_entwertet_cookie_ueberall(db_admin: Any, fabrik: Fabrik) -> None:
    _person(db_admin, "otto@example.org")
    eins, zwei = fabrik("otto@example.org"), fabrik("otto@example.org")
    alter_cookie = eins.cookies.get("event_sitzung")
    assert alter_cookie
    r = eins.post("/api/abmelden")
    assert r.status_code == 200, r.text
    assert "event_sitzung=" in r.headers["set-cookie"] and "max-age=0" in (
        r.headers["set-cookie"].lower())
    assert eins.get("/api/ich").status_code == 401
    assert zwei.get("/api/ich").status_code == 401  # andere Sitzung ebenfalls entwertet
    drei = fabrik()
    drei.cookies.set("event_sitzung", alter_cookie)
    assert drei.get("/api/ich").status_code == 401


# --- Mandantenmatrix --------------------------------------------------------------

# Familien mit Positivkontrolle (eigene Firma) und Fremdversuch (Firma B).
FAMILIEN = [
    "firma_anlegen", "einladen", "rolle_aendern", "mitglied_entfernen",
    "event_anlegen", "event_aendern", "event_status", "event_loeschen", "kontakt_anlegen",
    *[f"{op}:{b}" for b in sorted(REGISTRY) for op in ("b_anlegen", "b_aendern", "b_loeschen")],
]
# Nur Fremdversuch: fremde Person/Zeile ueber die eigene Firma/das eigene Event adressiert.
NUR_FREMD = [
    "rolle_aendern_fremde_person", "mitglied_entfernen_fremde_person",
    *[f"{op}:{b}" for b in sorted(REGISTRY)
      for op in ("b_aendern_fremde_zeile", "b_loeschen_fremde_zeile")],
]


def _anfrage(w: dict[str, Any], fall: str, eigen: bool) -> tuple[str, str, Any, str]:
    """(Methode, URL, JSON, Client). Fremd = Leitung von A gegen Ressourcen von B."""
    s = "a" if eigen else "b"
    f, e = w["f" + s], w["e" + s]
    op, _, b = fall.partition(":")
    if op == "firma_anlegen":
        return ("POST", "/api/firmen", {"name": "Neue Beispiel GmbH", "kurz": "neu-bsp"},
                "betreiber" if eigen else "la")
    if op == "einladen":
        return ("POST", f"/api/firmen/{f}/einladungen",
                {"mail": "neu@example.org", "name": "Neu Beispiel", "rolle": "mitglied"}, "la")
    if op == "rolle_aendern":
        return "PATCH", f"/api/firmen/{f}/mitglieder/{w['m' + s]}", {"rolle": "leitung"}, "la"
    if op == "rolle_aendern_fremde_person":
        return "PATCH", f"/api/firmen/{w['fa']}/mitglieder/{w['mb']}", {"rolle": "mitglied"}, "la"
    if op == "mitglied_entfernen":
        return "DELETE", f"/api/firmen/{f}/mitglieder/{w['m' + s]}", None, "la"
    if op == "mitglied_entfernen_fremde_person":
        return "DELETE", f"/api/firmen/{w['fa']}/mitglieder/{w['mb']}", None, "la"
    if op == "event_anlegen":
        return ("POST", "/api/events",
                {"firma_id": str(f), "kategorie": "weihnachtsfeier", "name": "Neue Feier",
                 "beginn": "2027-12-10T18:00:00", "ende": "2027-12-10T23:00:00"}, "la")
    if op == "event_aendern":
        return "PATCH", f"/api/events/{e}", {"name": "Umbenannt"}, "la"
    if op == "event_status":
        return "POST", f"/api/events/{e}/status", {"status": "abgesagt"}, "la"
    if op == "event_loeschen":
        return "DELETE", f"/api/events/{e}", None, "la"
    if op == "kontakt_anlegen":
        return "POST", f"/api/firmen/{f}/kontakte", {"name": "Neuer Kontakt"}, "la"
    if op == "b_anlegen":
        return "POST", f"/api/events/{e}/b/{b}", _neue_werte(b, w["k" + s + "_frei"]), "la"
    if op == "b_aendern":
        return "PATCH", f"/api/events/{e}/b/{b}/{w['z' + s][b]}", _aenderung(b), "la"
    if op == "b_loeschen":
        return "DELETE", f"/api/events/{e}/b/{b}/{w['z' + s][b]}", None, "la"
    if op == "b_aendern_fremde_zeile":
        return "PATCH", f"/api/events/{w['ea']}/b/{b}/{w['zb'][b]}", _aenderung(b), "la"
    if op == "b_loeschen_fremde_zeile":
        return "DELETE", f"/api/events/{w['ea']}/b/{b}/{w['zb'][b]}", None, "la"
    raise AssertionError(fall)


def test_matrix_deckt_alle_schreibenden_routen_ab(app: FastAPI, welt: dict[str, Any]) -> None:
    schreibende = {(m, r.path) for r in app.routes if isinstance(r, APIRoute)
                   for m in r.methods & SCHREIBEND} - OEFFENTLICH - {("POST", "/api/abmelden")}
    abgedeckt = set()
    for fall in FAMILIEN + NUR_FREMD:
        methode, url, _, _ = _anfrage(welt, fall, eigen=False)
        for m, muster in schreibende:
            regex = "^" + re.sub(r"\{[a-z_]+\}", "[^/]+", muster) + "$"
            if m == methode and re.match(regex, url):
                abgedeckt.add((m, muster))
    assert abgedeckt == schreibende


@pytest.mark.parametrize("fall", FAMILIEN + NUR_FREMD)
def test_mandantenmatrix_http(welt: dict[str, Any], db_admin: Any, mailer: SpeicherMailer,
                              fall: str) -> None:
    methode, url, daten, wer = _anfrage(welt, fall, eigen=False)
    vorher, mails = _stand(db_admin), len(mailer.gesendet)
    r = welt["c"][wer].request(methode, url, json=daten)
    assert r.status_code == (403 if fall == "firma_anlegen" else 404), r.text
    assert _stand(db_admin) == vorher
    assert len(mailer.gesendet) == mails


@pytest.mark.parametrize("fall", FAMILIEN)
def test_mandantenmatrix_http_positivkontrolle(welt: dict[str, Any], db_admin: Any,
                                               fall: str) -> None:
    methode, url, daten, wer = _anfrage(welt, fall, eigen=True)
    vorher = _stand(db_admin)
    r = welt["c"][wer].request(methode, url, json=daten)
    assert 200 <= r.status_code < 300, r.text
    assert _stand(db_admin) != vorher


# --- Kategorien, Katalog, Events, Bausteine ---------------------------------------


def test_kategorie_schema_route(welt: dict[str, Any]) -> None:
    c = welt["c"]["la"]
    schema = c.get("/api/kategorien/team_meeting/schema").json()
    assert "budget" not in schema["bausteine"] and "ablauf" in schema["bausteine"]
    assert schema["begriffe"]["ablauf"] == "Tagesordnung"
    assert schema["kategorie"] == "team_meeting"
    wf = c.get("/api/kategorien/weihnachtsfeier/schema").json()
    assert "budget" in wf["bausteine"] and {f["id"] for f in wf["felder"]} >= {"motto"}
    liste = c.get("/api/kategorien").json()
    ids = {k["id"] for k in liste}
    assert {"team_meeting", "weihnachtsfeier", "fest"} <= ids and "basis" not in ids
    assert all(set(k) == {"id", "name", "eltern"} for k in liste)
    assert c.get("/api/kategorien/basis/schema").status_code == 404  # abstrakt
    assert c.get("/api/kategorien/gibtsnicht/schema").status_code == 404


def test_katalog_route_gruppierbar(welt: dict[str, Any], db_admin: Any) -> None:
    c = welt["c"]["la"]
    r = c.post("/api/events", json={
        "firma_id": str(welt["fa"]), "kategorie": "team_meeting", "name": "Wochenrunde",
        "beginn": "2020-01-01T10:00:00", "ende": "2020-01-01T11:00:00"})
    assert r.status_code == 201, r.text
    zeilen = c.get("/api/events").json()
    assert {z["id"] for z in zeilen} == {str(welt["ea"]), r.json()["id"]}  # nie Firma B
    for z in zeilen:
        assert z["phase"] in {"laeuft", "kommend", "vergangen"}
        assert set(z["kennzahlen"]) == {"aufgaben_offen", "aufgaben_gesamt",
                                        "teilnehmende_zugesagt"}
    assert [z["phase"] for z in zeilen] == ["kommend", "vergangen"]
    gefiltert = c.get("/api/events", params={"kategorie": "team_meeting"}).json()
    assert [z["name"] for z in gefiltert] == ["Wochenrunde"]
    assert [z["name"] for z in c.get("/api/events", params={"suche": "wochen"}).json()] == [
        "Wochenrunde"]
    assert len(c.get("/api/events", params={"firma": str(welt["fa"])}).json()) == 2
    assert c.get("/api/events", params={"firma": str(welt["fb"])}).status_code == 404
    assert c.get("/api/events", params={"firma": "keine-uuid"}).status_code == 422
    assert c.get("/api/events", params={"suche": "a\x00b"}).status_code == 422
    alle = welt["c"]["betreiber"].get("/api/events").json()
    assert {z["firma_id"] for z in alle} == {str(welt["fa"]), str(welt["fb"])}


def test_event_routen_und_fehlerabbildung(welt: dict[str, Any]) -> None:
    c = welt["c"]["la"]
    neu = {"firma_id": str(welt["fa"]), "kategorie": "weihnachtsfeier", "name": "Feier 2027",
           "beginn": "2027-12-10T18:00:00", "ende": "2027-12-10T23:00:00",
           "metadaten": {"motto": "Winter"}}
    r = c.post("/api/events", json=neu)
    assert r.status_code == 201, r.text
    ev = r.json()
    # naiv = Europe/Berlin (Winterzeit, UTC+1)
    assert datetime.fromisoformat(ev["beginn"]) == datetime(2027, 12, 10, 17, tzinfo=timezone.utc)
    assert ev["metadaten"] == {"motto": "Winter", "wichteln": False}
    eid = ev["id"]
    detail = c.get(f"/api/events/{eid}").json()
    assert detail["name"] == "Feier 2027" and "budget" in detail["schema"]["bausteine"]
    # 422: Fachfehler mit Text der Meldung
    r = c.post("/api/events", json={**neu, "ende": "2027-12-09T18:00:00"})
    assert r.status_code == 422 and r.json() == {"detail": "Ende liegt vor dem Beginn"}
    roh = json.dumps({**neu, "metadaten": {"kapazitaet": float("nan")}})  # httpx kann kein NaN
    assert "NaN" in roh
    r = c.post("/api/events", content=roh, headers={"Content-Type": "application/json"})
    assert r.status_code == 422, r.text
    assert c.post("/api/events", json={**neu, "unbekannt": 1}).status_code == 422
    assert c.post("/api/events", json={**neu, "kategorie": "basis"}).status_code == 422
    assert c.patch(f"/api/events/{eid}", json={"name": None}).status_code == 422
    assert c.patch(f"/api/events/{eid}", json={}).status_code == 422
    r = c.patch(f"/api/events/{eid}", json={"name": "Feier neu", "ort_adresse": "Musterweg 1"})
    assert r.status_code == 200 and r.json()["name"] == "Feier neu"
    assert c.post(f"/api/events/{eid}/status", json={"status": "quatsch"}).status_code == 422
    assert c.post(f"/api/events/{eid}/status", json={"status": "laeuft"}).json()["status"] == (
        "laeuft")
    verlauf = c.get(f"/api/events/{eid}/verlauf").json()
    assert [v["aktion"] for v in verlauf] == ["status_laeuft", "geaendert", "angelegt"]
    assert verlauf[0]["quelle"] == "oberflaeche"
    assert len(c.get(f"/api/events/{eid}/verlauf", params={"limit": 1}).json()) == 1
    assert c.get(f"/api/events/{eid}/verlauf", params={"limit": 0}).status_code == 422
    assert c.get(f"/api/events/{uuid4()}").status_code == 404
    assert c.get(f"/api/events/{welt['eb']}").status_code == 404
    assert c.get(f"/api/events/{welt['eb']}/verlauf").status_code == 404
    assert c.delete(f"/api/events/{eid}").json() == {"ok": True}
    assert c.get(f"/api/events/{eid}").status_code == 404


def test_baustein_routen(welt: dict[str, Any], db_admin: Any) -> None:
    c = welt["c"]["la"]
    ea = welt["ea"]
    r = c.post(f"/api/events/{ea}/b/aufgaben", json={"titel": "Deko", "faellig":
                                                      "2027-02-01T12:00:00"})
    assert r.status_code == 201, r.text
    zid = r.json()["id"]
    titel = [z["titel"] for z in c.get(f"/api/events/{ea}/b/aufgaben").json()]
    assert titel == ["Aufgabe", "Deko"]
    r = c.patch(f"/api/events/{ea}/b/aufgaben/{zid}", json={"status": "erledigt"})
    assert r.status_code == 200 and r.json()["status"] == "erledigt"
    assert c.post(f"/api/events/{ea}/b/aufgaben", json={"id": str(uuid4()),
                                                        "titel": "X"}).status_code == 422
    assert c.post(f"/api/events/{ea}/b/aufgaben", json={"titel": "a\x00b"}).status_code == 422
    assert c.post(f"/api/events/{ea}/b/aufgaben", json=["kein", "objekt"]).status_code == 422
    assert c.get(f"/api/events/{ea}/b/gibtsnicht").status_code == 404
    assert c.post(f"/api/events/{ea}/b/verlauf", json={}).status_code == 404
    assert c.patch(f"/api/events/{ea}/b/gibtsnicht/{zid}", json={"x": 1}).status_code == 404
    assert c.delete(f"/api/events/{ea}/b/aufgaben/{zid}").json() == {"ok": True}
    assert c.delete(f"/api/events/{ea}/b/aufgaben/{zid}").status_code == 404
    tm = _event(db_admin, welt["fa"], "team_meeting")
    r = c.post(f"/api/events/{tm}/b/budget", json={"titel": "X", "art": "kosten",
                                                  "betrag_cent": 1})
    assert r.status_code == 422 and "budget" in r.json()["detail"]


def test_mitglied_rechte_403(welt: dict[str, Any], fabrik: Fabrik, db_admin: Any) -> None:
    m = fabrik("mitglied-a@example.org")
    vorher = _stand(db_admin)
    r = m.delete(f"/api/events/{welt['ea']}")
    assert r.status_code == 403 and "Berechtigung" in r.json()["detail"]
    assert m.post(f"/api/firmen/{welt['fa']}/einladungen", json={
        "mail": "x@example.org", "name": "X", "rolle": "mitglied"}).status_code == 403
    assert m.patch(f"/api/firmen/{welt['fa']}/mitglieder/{welt['la']}",
                   json={"rolle": "mitglied"}).status_code == 403
    assert _stand(db_admin) == vorher
    assert m.post(f"/api/events/{welt['ea']}/b/aufgaben", json={"titel": "Ok"}).status_code == 201


def test_kontakte_routen(welt: dict[str, Any]) -> None:
    c = welt["c"]["la"]
    url = f"/api/firmen/{welt['fa']}/kontakte"
    r = c.post(url, json={"name": "Max Muster", "mail": "max@example.org"})
    assert r.status_code == 201, r.text
    namen = [k["name"] for k in c.get(url).json()]
    assert "Max Muster" in namen and "Kontakt a" in namen
    assert [k["name"] for k in c.get(url, params={"suche": "muster"}).json()] == ["Max Muster"]
    assert c.post(url, json={"name": "   "}).status_code == 422
    assert c.post(url, json={"mail": "ohne-name@example.org"}).status_code == 422
    assert c.get(f"/api/firmen/{welt['fb']}/kontakte").status_code == 404
    assert c.get(f"/api/firmen/{welt['fb']}/mitglieder").status_code == 404


def test_letzte_leitung_422(welt: dict[str, Any]) -> None:
    c = welt["c"]["la"]
    r = c.delete(f"/api/firmen/{welt['fa']}/mitglieder/{welt['la']}")
    assert r.status_code == 422 and r.json() == {
        "detail": "Die letzte Leitung einer Firma bleibt bestehen"}


# --- 500, Start, Pool, Kommandozeile -----------------------------------------------


def test_500_ohne_details(welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
                          caplog: pytest.LogCaptureFixture) -> None:
    def kaputt(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("geheim")

    def server_log() -> str:
        # Ohne den httpx-Logger: der protokolliert clientseitig die Test-URL.
        return " | ".join(f"{rec.name} {rec.getMessage()}" for rec in caplog.records
                         if not rec.name.startswith("httpx"))

    caplog.set_level(logging.DEBUG)
    c = welt["c"]["la"]
    monkeypatch.setattr(events, "katalog", kaputt)
    monkeypatch.setattr(zugang, "einladung_info", kaputt)
    token = "tok-" + "z" * 40
    for url, muster in (("/api/events", "/api/events"),
                        (f"/api/einladung/{token}", "/api/einladung/{token}")):
        r = c.get(url)
        assert r.status_code == 500
        assert r.json() == {"detail": "interner Fehler"}
        assert "geheim" not in r.text and "Traceback" not in r.text
        assert f"GET {muster}" in server_log()
    # Echter psycopg-Fehler (doppelter Kurzname): DETAIL nennt die Daten, Body/Log nicht.
    r = welt["c"]["betreiber"].post("/api/firmen", json={"name": "Doppelt", "kurz": "bsp-a"})
    assert r.status_code == 500 and r.json() == {"detail": "interner Fehler"}
    log = server_log()
    assert "geheim" not in log and token not in log
    assert "bsp-a" not in log and "Key (" not in log
    assert "RuntimeError" in log and "UniqueViolation" in log
    assert all(rec.exc_info is None for rec in caplog.records)


def test_erstelle_app_prueft_start(app_dsn: str, tmp_path: Path) -> None:
    m = SpeicherMailer()
    with pytest.raises(ValueError, match="32"):
        api.erstelle_app(app_dsn, b"x" * 31, m, BASIS)
    with pytest.raises(ValueError):
        api.erstelle_app(app_dsn, GEHEIM, m, "events.example.org")
    (tmp_path / "basis.yaml").write_text("id: basis\nname: Basis\neltern: null\nbausteine: [quatsch]\n",
                                         encoding="utf-8")
    with pytest.raises(KategorieFehler):
        api.erstelle_app(app_dsn, GEHEIM, m, BASIS, tmp_path)


def test_pool_verbindung_ist_frei(app: FastAPI, fabrik: Fabrik) -> None:
    pool = app.state.pool
    for _ in range(3):
        with pool.connection() as conn:
            assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
            assert conn.autocommit is True
            assert conn.info.user == "event_app_test"


def test_kommandozeile_konfiguration(tmp_path: Path) -> None:
    dsn_datei = tmp_path / "dsn"
    dsn_datei.write_bytes(b"host=127.0.0.1 dbname=x user=event_app\r\n")
    geheim_datei = tmp_path / "geheim"
    geheim_datei.write_bytes(b"g" * 40 + b"\r\n")
    env = {"EVENT_APP_DSN_FILE": str(dsn_datei), "EVENT_SITZUNG_GEHEIMNIS_FILE": str(geheim_datei),
           "EVENT_BASIS_URL": "https://events.example.org"}
    k = api.konfiguration(env)
    assert k.dsn == "host=127.0.0.1 dbname=x user=event_app"
    assert k.geheimnis == b"g" * 40 and k.port == 8170 and k.cookie_sicher is True
    k = api.konfiguration({**env, "EVENT_API_PORT": "9001", "EVENT_COOKIE_UNSICHER": "1"})
    assert k.port == 9001 and k.cookie_sicher is False
    ohne_datei = {k2: v for k2, v in env.items() if k2 != "EVENT_APP_DSN_FILE"}
    assert api.konfiguration({**ohne_datei, "EVENT_APP_DSN": "dbname=y"}).dsn == "dbname=y"
    for fehlt in ("EVENT_SITZUNG_GEHEIMNIS_FILE", "EVENT_BASIS_URL"):
        with pytest.raises(ValueError, match=fehlt):
            api.konfiguration({k2: v for k2, v in env.items() if k2 != fehlt})
    with pytest.raises(ValueError, match="EVENT_APP_DSN") as e:
        api.konfiguration(ohne_datei)
    assert "event_app" not in str(e.value)
    with pytest.raises(ValueError, match="EVENT_API_PORT"):
        api.konfiguration({**env, "EVENT_API_PORT": "abc"})
