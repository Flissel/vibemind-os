"""Event-Wahl und Verwaltungswerkzeuge des event-mcp (system/werkzeuge.py, Task 5).

Wie in test_werkzeuge.py laufen die Werkzeuge ueber `db_app()` (App-DSN, ohne gesetzten
Kontext); nachgelesen wird mit `db_admin`. Zweistufige Aktionen: der erste Aufruf ohne
`bestaetigung` aendert nichts, erst der zweite mit dem Code fuehrt aus (Review Focus 3).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from spaces.event_management.system import marke, schluessel, werkzeuge
from spaces.event_management.system.kontext import NichtGefunden
from spaces.event_management.system.mailer import SpeicherMailer

BEGINN = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
BASIS_URL = "https://events.example.org"
VERBOTEN = {"event_id", "firma_id", "person_id"}
VERWALTUNG = {"event_anlegen", "einladen", "event_absagen", "event_archivieren",
              "event_loeschen", "kontakt_entfernen", "rolle_aendern", "firma_anlegen"}
RISKANT = ("event_absagen", "event_archivieren", "event_loeschen", "kontakt_entfernen",
           "rolle_aendern")
FIRMA_A = "Beispiel firma-a"


def _firma(admin: Any, kurz: str) -> UUID:
    return admin.execute(
        "insert into event.firma (name, kurz) values (%s, %s) returning id",
        ("Beispiel " + kurz, kurz),
    ).fetchone()["id"]


def _event(admin: Any, firma: UUID, name: str, *, kategorie: str = "team_meeting",
           quelle: str = "neu", tage: int = 0) -> UUID:
    beginn = BEGINN + timedelta(days=tage)
    return admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende, quelle)"
        " values (%s, %s, %s, %s, %s, %s) returning id",
        (firma, kategorie, name, beginn, beginn + timedelta(hours=3), quelle),
    ).fetchone()["id"]


def _person(admin: Any, mail: str, name: str = "Erika Beispiel", *,
            betreiber: bool = False) -> UUID:
    return admin.execute(
        "insert into event.person (mail, name, betreiber) values (%s, %s, %s) returning id",
        (mail, name, betreiber),
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


def _anzahl(admin: Any, tabelle: str) -> int:
    n: int = admin.execute(f"select count(*) as n from event.{tabelle}").fetchone()["n"]
    return n


def _stand(admin: Any) -> dict[str, Any]:
    """Fachlicher Stand fuer 'aendert nichts' (ohne marke/bestaetigung)."""
    return {
        "events": admin.execute(
            "select id, name, status, geaendert from event.event order by id").fetchall(),
        "kontakte": admin.execute("select id, name from event.kontakt order by id").fetchall(),
        "mitglieder": admin.execute(
            "select person_id, firma_id, rolle from event.mitgliedschaft"
            " order by person_id, firma_id").fetchall(),
        "verlauf": _anzahl(admin, "verlauf"),
        "einladungen": _anzahl(admin, "einladung"),
        "personen": _anzahl(admin, "person"),
        "firmen": _anzahl(admin, "firma"),
    }


def _schluessel_im_schema(schema: Any) -> set[str]:
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


@pytest.fixture
def welt(db_admin: Any) -> dict[str, Any]:
    a = _firma(db_admin, "firma-a")
    b = _firma(db_admin, "firma-b")
    c = _firma(db_admin, "firma-c")
    lea = _person(db_admin, "lea@example.org", "Lea Beispiel")
    mia = _person(db_admin, "mia@example.org", "Mia Beispiel")
    bea = _person(db_admin, "bea@example.org", "Bea Beispiel", betreiber=True)
    _mitglied(db_admin, lea, a, "leitung")
    _mitglied(db_admin, lea, c, "mitglied")  # nur Mitglied: fuer den Leitungs-Schluessel unsichtbar
    _mitglied(db_admin, mia, a, "mitglied")
    return {
        "a": a, "b": b, "c": c, "lea": lea, "mia": mia, "bea": bea,
        "nord": _event(db_admin, a, "Sommerfest Nord"),
        "sued": _event(db_admin, a, "Sommerfest Sued", tage=1),
        "runde": _event(db_admin, a, "Teamrunde", tage=2),
        "runde_extra": _event(db_admin, a, "Teamrunde Extra", tage=3),
        "fremd": _event(db_admin, b, "Fremdes Fest"),
        "club": _event(db_admin, c, "Clubabend"),
        "karl": _kontakt(db_admin, a, "Karl Beispiel", "karl@example.org"),
        "karla": _kontakt(db_admin, a, "Karla Muster"),
        "karl_b": _kontakt(db_admin, b, "Karl Beispiel"),
    }


@pytest.fixture
def lea(db_admin: Any, welt: dict[str, Any]) -> schluessel.SchluesselInfo:
    return _info(db_admin, "leitung", person_id=welt["lea"])


# --- Event-Wahl ---------------------------------------------------------------------


def test_waehlen_eindeutig_mehrdeutig_keiner(db_admin, db_app, welt, lea):
    app = db_app()

    eindeutig = werkzeuge.ausfuehren(app, lea, "event_waehlen", {"suche": "nord"})
    assert set(eindeutig) == {"marke", "event"}
    assert eindeutig["event"] == {"name": "Sommerfest Nord", "firma": FIRMA_A, "beginn": BEGINN}
    assert marke.marke_aufloesen(app, lea, eindeutig["marke"]) == welt["nord"]

    # Exakter Name gewinnt, auch wenn die Suche noch mehr trifft ("Teamrunde Extra").
    exakt = werkzeuge.ausfuehren(app, lea, "event_waehlen", {"suche": "  teamrunde "})
    assert exakt["event"]["name"] == "Teamrunde"
    assert marke.marke_aufloesen(app, lea, exakt["marke"]) == welt["runde"]

    marken_vorher = _anzahl(db_admin, "marke")
    mehrdeutig = werkzeuge.ausfuehren(app, lea, "event_waehlen", {"suche": "Sommerfest"})
    assert set(mehrdeutig) == {"treffer", "hinweis"}
    assert mehrdeutig["hinweis"] == "Bitte genauer"
    assert mehrdeutig["treffer"] == [
        {"name": "Sommerfest Nord", "firma": FIRMA_A, "beginn": BEGINN},
        {"name": "Sommerfest Sued", "firma": FIRMA_A, "beginn": BEGINN + timedelta(days=1)},
    ]
    assert _anzahl(db_admin, "marke") == marken_vorher  # keine Marke ausgestellt

    with pytest.raises(NichtGefunden):
        werkzeuge.ausfuehren(app, lea, "event_waehlen", {"suche": "Gibt es nicht"})
    with pytest.raises(ValueError):
        werkzeuge.ausfuehren(app, lea, "event_waehlen", {"suche": "   "})

    # Hoechstens 20 Treffer.
    for i in range(25):
        _event(db_admin, welt["a"], f"Serie {i:02d}", tage=10 + i)
    viele = werkzeuge.ausfuehren(app, lea, "event_waehlen", {"suche": "Serie"})
    assert len(viele["treffer"]) == 20 and "marke" not in viele


def test_waehlen_nur_eigene_firmen(db_admin, db_app, welt, lea):
    app = db_app()
    # Fremde Firma und Firma, in der die Person nur Mitglied ist: unsichtbar.
    for suche in ("Fremdes Fest", "Clubabend"):
        with pytest.raises(NichtGefunden):
            werkzeuge.ausfuehren(app, lea, "event_waehlen", {"suche": suche})
    namen = {e["name"] for e in werkzeuge.ausfuehren(app, lea, "events_suchen", {})["events"]}
    assert namen == {"Sommerfest Nord", "Sommerfest Sued", "Teamrunde", "Teamrunde Extra"}

    # Betreiber (auch lesend) sieht alle Firmen.
    for art in ("betreiber", "betreiber_lesen"):
        bea = _info(db_admin, art, person_id=welt["bea"])
        gewaehlt = werkzeuge.ausfuehren(app, bea, "event_waehlen", {"suche": "Fremdes Fest"})
        assert gewaehlt["event"]["firma"] == "Beispiel firma-b"
        assert marke.marke_aufloesen(app, bea, gewaehlt["marke"]) == welt["fremd"]


def test_events_suchen_ohne_ids(db_admin, db_app, welt, lea):
    app = db_app()
    _event(db_admin, welt["a"], "Bergfest", kategorie="festival", tage=5)
    for i in range(25):
        _event(db_admin, welt["a"], f"Serie {i:02d}", tage=10 + i)

    alle = werkzeuge.ausfuehren(app, lea, "events_suchen", {})
    assert set(alle) == {"events"}
    assert len(alle["events"]) == 20
    for e in alle["events"]:
        assert set(e) == {"name", "firma", "kategorie", "beginn", "ende", "phase", "status"}

    festival = werkzeuge.ausfuehren(app, lea, "events_suchen", {"kategorie": "festival"})
    assert [e["name"] for e in festival["events"]] == ["Bergfest"]
    gesucht = werkzeuge.ausfuehren(app, lea, "events_suchen", {"suche": "Bergfest"})
    assert gesucht["events"] == [{
        "name": "Bergfest", "firma": FIRMA_A, "kategorie": "festival",
        "beginn": BEGINN + timedelta(days=5), "ende": BEGINN + timedelta(days=5, hours=3),
        "phase": "kommend", "status": "geplant",
    }]
    lesen = _info(db_admin, "betreiber_lesen", person_id=welt["bea"])
    assert werkzeuge.ausfuehren(app, lesen, "events_suchen", {"suche": "Fremdes"})["events"][0][
        "firma"] == "Beispiel firma-b"


# --- Anlegen, Einladen, Firma -------------------------------------------------------


def test_anlegen_liefert_marke(db_admin, db_app, welt, lea):
    app = db_app()
    ergebnis = werkzeuge.ausfuehren(app, lea, "event_anlegen", {
        "firma": " FIRMA-A ", "kategorie": "team_meeting", "name": "Planungsrunde",
        "beginn": "2027-03-01T10:00", "ende": "2027-03-01T12:00",
        "ort_adresse": "Musterstrasse 1", "beschreibung": "Kurz"})
    assert ergebnis["event"] == {"name": "Planungsrunde", "firma": FIRMA_A}
    assert not VERBOTEN & set(ergebnis) and "id" not in ergebnis
    neu = marke.marke_aufloesen(app, lea, ergebnis["marke"])
    zeile = db_admin.execute("select firma_id, name, beginn, ort_adresse from event.event"
                             " where id = %s", (neu,)).fetchone()
    assert zeile["firma_id"] == welt["a"] and zeile["name"] == "Planungsrunde"
    assert zeile["beginn"] == datetime(2027, 3, 1, 9, 0, tzinfo=timezone.utc)  # Berlin
    verlauf = db_admin.execute("select quelle, actor, actor_person_id from event.verlauf"
                               " where event_id = %s", (neu,)).fetchone()
    assert verlauf == {"quelle": "bot", "actor": "bot:leitung", "actor_person_id": welt["lea"]}

    # Mit der Marke direkt weiterarbeiten.
    aufgabe = werkzeuge.ausfuehren(app, lea, "aufgaben_anlegen",
                                   {"marke": ergebnis["marke"], "titel": "Raum buchen"})
    assert aufgabe["event"] == {"name": "Planungsrunde", "firma": FIRMA_A}

    vorher = _stand(db_admin)
    for argumente, fehler in (
        ({"firma": "firma-b"}, NichtGefunden),  # fremde Firma
        ({"firma": "firma-c"}, NichtGefunden),  # nur Mitglied
        ({"firma": "gibt-es-nicht"}, NichtGefunden),
        ({"kategorie": "gibt_es_nicht"}, ValueError),
        ({"ende": "2027-03-01T09:00"}, ValueError),
    ):
        basis = {"firma": "firma-a", "kategorie": "team_meeting", "name": "Nein",
                 "beginn": "2027-03-01T10:00", "ende": "2027-03-01T12:00"}
        with pytest.raises(fehler):
            werkzeuge.ausfuehren(app, lea, "event_anlegen", {**basis, **argumente})
    assert _stand(db_admin) == vorher


def test_einladen_mit_dem_mailer_der_app(db_admin, db_app, welt, lea):
    app = db_app()
    argumente = {"firma": "firma-a", "mail": "neu@example.org", "name": "Nora Neu",
                 "rolle": "mitglied"}

    # Ohne Mailer oder Basis-URL: sauberer ValueError, nichts angelegt.
    vorher = _stand(db_admin)
    for mailer, basis in ((None, BASIS_URL), (SpeicherMailer(), None)):
        with pytest.raises(ValueError) as e:
            werkzeuge.ausfuehren(app, lea, "einladen", argumente, mailer=mailer,
                                 basis_url=basis)
        assert str(e.value) == "Einladen ist nicht eingerichtet"
    assert _stand(db_admin) == vorher

    mailer = SpeicherMailer()
    ergebnis = werkzeuge.ausfuehren(app, lea, "einladen", argumente, mailer=mailer,
                                    basis_url=BASIS_URL)
    assert ergebnis == {"firma": {"name": FIRMA_A, "kurz": "firma-a"}, "eingeladen": True}
    assert [m.an for m in mailer.gesendet] == ["neu@example.org"]
    assert f"{BASIS_URL}/einladung/" in mailer.gesendet[0].text
    einladung = db_admin.execute("select firma_id, rolle from event.einladung").fetchone()
    assert einladung == {"firma_id": welt["a"], "rolle": "mitglied"}

    # Fremde Firma: NichtGefunden, keine Mail.
    with pytest.raises(NichtGefunden):
        werkzeuge.ausfuehren(app, lea, "einladen", {**argumente, "firma": "firma-b"},
                             mailer=mailer, basis_url=BASIS_URL)
    assert len(mailer.gesendet) == 1 and _anzahl(db_admin, "einladung") == 1


def test_firma_anlegen_nur_betreiber(db_admin, db_app, welt, lea):
    app = db_app()
    bea = _info(db_admin, "betreiber", person_id=welt["bea"])
    assert "firma_anlegen" in _namen(app, bea)
    neu = werkzeuge.ausfuehren(app, bea, "firma_anlegen",
                               {"name": "Neue Beispiel GmbH", "kurz": "neu-gmbh"})
    assert neu == {"firma": {"name": "Neue Beispiel GmbH", "kurz": "neu-gmbh"}}
    assert db_admin.execute("select name from event.firma where kurz = 'neu-gmbh'"
                            ).fetchone()["name"] == "Neue Beispiel GmbH"
    with pytest.raises(ValueError) as e:
        werkzeuge.ausfuehren(app, bea, "firma_anlegen", {"name": "Doppelt", "kurz": "neu-gmbh"})
    assert str(e.value) == "Kürzel bereits vergeben"

    andere = [lea, _info(db_admin, "betreiber_lesen", person_id=welt["bea"]),
              _info(db_admin, "event", event_id=welt["nord"]), _info(db_admin, "melden")]
    for info in andere:
        assert "firma_anlegen" not in _namen(app, info), info.art
        with pytest.raises(NichtGefunden) as e:
            werkzeuge.ausfuehren(app, info, "firma_anlegen", {"name": "Nein", "kurz": "nein"})
        assert str(e.value) == "unbekanntes Werkzeug"
    assert _anzahl(db_admin, "firma") == 4


def test_betreiber_lesen_hat_keine_verwaltung(db_admin, db_app, welt, lea):
    app = db_app()
    lesen = _info(db_admin, "betreiber_lesen", person_id=welt["bea"])
    assert not VERWALTUNG & _namen(app, lesen)
    assert {"events_suchen", "event_waehlen"} <= _namen(app, lesen)
    assert _namen(app, _info(db_admin, "melden")) == set()
    assert not VERWALTUNG & _namen(app, _info(db_admin, "event", event_id=welt["nord"]))
    assert _namen(app, lea) & VERWALTUNG == VERWALTUNG - {"firma_anlegen"}
    assert VERWALTUNG <= _namen(app, _info(db_admin, "betreiber", person_id=welt["bea"]))

    m = marke.marke_ausstellen(app, lesen, welt["nord"])
    vorher = _stand(db_admin)
    for name in sorted(VERWALTUNG):
        with pytest.raises(NichtGefunden) as e:
            werkzeuge.ausfuehren(app, lesen, name, {"marke": m})
        assert str(e.value) == "unbekanntes Werkzeug"
    assert _stand(db_admin) == vorher

    # Kein Verwaltungswerkzeug nimmt eine ID; die riskanten nehmen `bestaetigung`.
    bea = _info(db_admin, "betreiber", person_id=welt["bea"])
    katalog = {w.name: w for w in werkzeuge.katalog(app, bea)}
    for name in VERWALTUNG:
        assert not VERBOTEN & _schluessel_im_schema(katalog[name].eingabe), name
        assert katalog[name].schreibend is True, name
    for name in RISKANT:
        eingabe = katalog[name].eingabe
        assert "bestaetigung" in eingabe["properties"]
        assert "bestaetigung" not in eingabe.get("required", [])
    assert katalog["rolle_aendern"].eingabe["properties"]["rolle"]["enum"] == [
        "leitung", "mitglied"]


# --- Zweistufige Aktionen -----------------------------------------------------------


def _riskant_argumente(name: str, m: str) -> dict[str, Any]:
    if name == "kontakt_entfernen":
        return {"marke": m, "kontakt": "Karl Beispiel"}
    if name == "rolle_aendern":
        return {"firma": "firma-a", "mail": "Mia@Example.org", "rolle": "leitung"}
    return {"marke": m}


def _ausgefuehrt(name: str, admin: Any, welt: dict[str, Any]) -> bool:
    def status() -> str | None:
        z = admin.execute("select status from event.event where id = %s",
                          (welt["nord"],)).fetchone()
        return None if z is None else str(z["status"])

    if name == "event_absagen":
        return status() == "abgesagt"
    if name == "event_archivieren":
        return status() == "archiviert"
    if name == "event_loeschen":
        return status() is None
    if name == "kontakt_entfernen":
        return admin.execute("select 1 from event.kontakt where id = %s",
                             (welt["karl"],)).fetchone() is None
    return admin.execute("select rolle from event.mitgliedschaft where person_id = %s"
                         " and firma_id = %s", (welt["mia"], welt["a"])).fetchone()[
        "rolle"] == "leitung"


@pytest.mark.parametrize("name", RISKANT)
def test_zweistufig_aendert_im_ersten_schritt_nichts(db_admin, db_app, welt, lea, name):
    app = db_app()
    m = marke.marke_ausstellen(app, lea, welt["nord"])
    argumente = _riskant_argumente(name, m)

    vorher = _stand(db_admin)
    erster = werkzeuge.ausfuehren(app, lea, name, argumente)
    assert _stand(db_admin) == vorher  # Admin-Nachlesen: nichts geaendert
    assert not _ausgefuehrt(name, db_admin, welt)
    assert erster["bestaetigung"].startswith("bs_")
    assert erster["gueltig_minuten"] == 5
    if name == "rolle_aendern":
        assert FIRMA_A in erster["zusammenfassung"]
        assert "mia@example.org" in erster["zusammenfassung"]
        assert erster["firma"] == {"name": FIRMA_A, "kurz": "firma-a"}
    else:
        assert f"Sommerfest Nord ({FIRMA_A})" in erster["zusammenfassung"]
        assert erster["event"] == {"name": "Sommerfest Nord", "firma": FIRMA_A}
    if name == "kontakt_entfernen":
        assert "Karl Beispiel" in erster["zusammenfassung"]

    zweiter = werkzeuge.ausfuehren(app, lea, name,
                                   {**argumente, "bestaetigung": erster["bestaetigung"]})
    assert _ausgefuehrt(name, db_admin, welt)
    assert "bestaetigung" not in zweiter
    if name != "rolle_aendern":
        assert zweiter["event"] == {"name": "Sommerfest Nord", "firma": FIRMA_A}
    # Der Kontakt gleichen Namens der anderen Firma bleibt.
    assert db_admin.execute("select 1 from event.kontakt where id = %s",
                            (welt["karl_b"],)).fetchone() is not None


def test_bestaetigung_fuer_anderes_event_abgelehnt(db_admin, db_app, welt, lea):
    app = db_app()
    m_nord = marke.marke_ausstellen(app, lea, welt["nord"])
    m_sued = marke.marke_ausstellen(app, lea, welt["sued"])
    code = werkzeuge.ausfuehren(app, lea, "event_absagen", {"marke": m_nord})["bestaetigung"]

    vorher = _stand(db_admin)
    falsch: list[tuple[str, dict[str, Any]]] = [
        ("event_absagen", {"marke": m_sued}),  # anderes Event
        ("event_loeschen", {"marke": m_nord}),  # andere Aktion
        ("event_archivieren", {"marke": m_nord}),
        ("kontakt_entfernen", {"marke": m_nord, "kontakt": "Karl Beispiel"}),
    ]
    for name, argumente in falsch:
        with pytest.raises(ValueError) as e:
            werkzeuge.ausfuehren(app, lea, name, {**argumente, "bestaetigung": code})
        assert str(e.value) == marke.CODE_UNGUELTIG, name
    # Anderer Schluessel derselben Person.
    lea2 = _info(db_admin, "leitung", person_id=welt["lea"])
    m2 = marke.marke_ausstellen(app, lea2, welt["nord"])
    with pytest.raises(ValueError):
        werkzeuge.ausfuehren(app, lea2, "event_absagen", {"marke": m2, "bestaetigung": code})
    assert _stand(db_admin) == vorher

    # Kontakt: Code fuer Karl gilt nicht fuer Karla.
    k_code = werkzeuge.ausfuehren(app, lea, "kontakt_entfernen",
                                  {"marke": m_nord, "kontakt": "Karl Beispiel"})["bestaetigung"]
    with pytest.raises(ValueError):
        werkzeuge.ausfuehren(app, lea, "kontakt_entfernen",
                             {"marke": m_nord, "kontakt": "Karla Muster", "bestaetigung": k_code})
    # Rolle: Code fuer "leitung" gilt nicht fuer "mitglied" oder eine andere Person.
    r_args = {"firma": "firma-a", "mail": "mia@example.org", "rolle": "leitung"}
    r_code = werkzeuge.ausfuehren(app, lea, "rolle_aendern", r_args)["bestaetigung"]
    for anders in ({"rolle": "mitglied"}, {"mail": "lea@example.org"}):
        with pytest.raises(ValueError):
            werkzeuge.ausfuehren(app, lea, "rolle_aendern",
                                 {**r_args, **anders, "bestaetigung": r_code})
    assert _stand(db_admin) == vorher

    # Fehlversuche verbrauchen den Code nicht: richtig eingesetzt wirkt er.
    werkzeuge.ausfuehren(app, lea, "event_absagen", {"marke": m_nord, "bestaetigung": code})
    assert db_admin.execute("select status from event.event where id = %s",
                            (welt["nord"],)).fetchone()["status"] == "abgesagt"
    assert db_admin.execute("select status from event.event where id = %s",
                            (welt["sued"],)).fetchone()["status"] == "geplant"


def test_bestaetigung_nur_einmal(db_admin, db_app, welt, lea):
    app = db_app()
    m = marke.marke_ausstellen(app, lea, welt["nord"])
    code = werkzeuge.ausfuehren(app, lea, "event_absagen", {"marke": m})["bestaetigung"]
    erst = werkzeuge.ausfuehren(app, lea, "event_absagen", {"marke": m, "bestaetigung": code})
    assert erst["status"] == "abgesagt"
    verlauf = _anzahl(db_admin, "verlauf")
    with pytest.raises(ValueError) as e:
        werkzeuge.ausfuehren(app, lea, "event_absagen",
                             {"marke": m, "bestaetigung": code.upper() + " "})
    assert str(e.value) == marke.CODE_UNGUELTIG
    assert _anzahl(db_admin, "verlauf") == verlauf

    with pytest.raises(ValueError) as e:  # erfundener Code
        werkzeuge.ausfuehren(app, lea, "event_loeschen", {"marke": m, "bestaetigung": "bs_aaaaaaaa"})
    assert str(e.value) == marke.CODE_UNGUELTIG
    assert db_admin.execute("select 1 from event.event where id = %s",
                            (welt["nord"],)).fetchone() is not None


def test_kontakt_entfernen_per_name_suche(db_admin, db_app, welt, lea):
    app = db_app()
    m = marke.marke_ausstellen(app, lea, welt["nord"])
    vorher = _stand(db_admin)

    mehrdeutig = werkzeuge.ausfuehren(app, lea, "kontakt_entfernen", {"marke": m, "kontakt": "karl"})
    assert "bestaetigung" not in mehrdeutig and mehrdeutig["hinweis"] == "Bitte genauer"
    assert mehrdeutig["event"] == {"name": "Sommerfest Nord", "firma": FIRMA_A}
    assert sorted(t["name"] for t in mehrdeutig["treffer"]) == ["Karl Beispiel", "Karla Muster"]
    assert not VERBOTEN & set(mehrdeutig["treffer"][0])

    with pytest.raises(NichtGefunden):  # nur in der Firma des Events
        werkzeuge.ausfuehren(app, lea, "kontakt_entfernen", {"marke": m, "kontakt": "Niemand"})
    with pytest.raises(ValueError):
        werkzeuge.ausfuehren(app, lea, "kontakt_entfernen", {"marke": m, "kontakt": "  "})
    assert _stand(db_admin) == vorher

    # Per Mail oder ID (aus kontakte_suchen) eindeutig.
    per_mail = werkzeuge.ausfuehren(app, lea, "kontakt_entfernen",
                                    {"marke": m, "kontakt": "karl@example.org"})
    assert "Karl Beispiel" in per_mail["zusammenfassung"]
    per_id = werkzeuge.ausfuehren(app, lea, "kontakt_entfernen",
                                  {"marke": m, "kontakt": str(welt["karla"])})
    assert "Karla Muster" in per_id["zusammenfassung"]
    werkzeuge.ausfuehren(app, lea, "kontakt_entfernen", {
        "marke": m, "kontakt": str(welt["karla"]), "bestaetigung": per_id["bestaetigung"]})
    assert db_admin.execute("select 1 from event.kontakt where id = %s",
                            (welt["karla"],)).fetchone() is None


def test_leitung_von_a_kann_nichts_in_b(db_admin, db_app, welt, lea):
    app = db_app()
    mailer = SpeicherMailer()
    vorher = _stand(db_admin)
    # Fremde Firma per Kuerzel: NichtGefunden.
    for name, argumente in (
        ("einladen", {"firma": "firma-b", "mail": "x@example.org", "name": "X",
                      "rolle": "mitglied"}),
        ("rolle_aendern", {"firma": "firma-b", "mail": "mia@example.org", "rolle": "leitung"}),
        ("rolle_aendern", {"firma": "firma-c", "mail": "lea@example.org", "rolle": "leitung"}),
        ("event_anlegen", {"firma": "firma-b", "kategorie": "team_meeting", "name": "X",
                           "beginn": "2027-01-01T10:00", "ende": "2027-01-01T11:00"}),
    ):
        with pytest.raises(NichtGefunden):
            werkzeuge.ausfuehren(app, lea, name, argumente, mailer=mailer, basis_url=BASIS_URL)
    # Fremdes Event: keine Marke zu bekommen, fremde Marke gilt nicht.
    with pytest.raises(NichtGefunden):
        marke.marke_ausstellen(app, lea, welt["fremd"])
    bea = _info(db_admin, "betreiber", person_id=welt["bea"])
    fremde_marke = marke.marke_ausstellen(app, bea, welt["fremd"])
    for name in ("event_absagen", "event_archivieren", "event_loeschen"):
        with pytest.raises(NichtGefunden) as e:
            werkzeuge.ausfuehren(app, lea, name, {"marke": fremde_marke})
        assert str(e.value) == marke.MARKE_UNGUELTIG
    # Unbekannte Mail in der eigenen Firma: NichtGefunden.
    with pytest.raises(NichtGefunden):
        werkzeuge.ausfuehren(app, lea, "rolle_aendern",
                             {"firma": "firma-a", "mail": "fremd@example.org", "rolle": "leitung"})
    assert _stand(db_admin) == vorher and mailer.gesendet == []


def test_marke_archiviertes_event_nach_wahl(db_admin, db_app, welt, lea):
    """T5-a: Event nach der Wahl archiviert -> Baustein-Schreiben abgelehnt, Lesen geht."""
    app = db_app()
    m = werkzeuge.ausfuehren(app, lea, "event_waehlen", {"suche": "Sommerfest Nord"})["marke"]
    zeile = werkzeuge.ausfuehren(app, lea, "aufgaben_anlegen", {"marke": m, "titel": "Vorher"})
    zeile_id = str(zeile["zeile"]["id"])

    code = werkzeuge.ausfuehren(app, lea, "event_archivieren", {"marke": m})["bestaetigung"]
    werkzeuge.ausfuehren(app, lea, "event_archivieren", {"marke": m, "bestaetigung": code})
    assert db_admin.execute("select status from event.event where id = %s",
                            (welt["nord"],)).fetchone()["status"] == "archiviert"

    event_key = _info(db_admin, "event", event_id=welt["nord"])
    for info, extra in ((lea, {"marke": m}), (event_key, {})):
        for name, argumente in (("aufgaben_anlegen", {"titel": "Nachher"}),
                                ("aufgaben_aendern", {"zeile": zeile_id, "titel": "Neu"}),
                                ("aufgaben_loeschen", {"zeile": zeile_id})):
            with pytest.raises(ValueError) as e:
                werkzeuge.ausfuehren(app, info, name, {**extra, **argumente})
            assert str(e.value) == "Event ist archiviert", (info.art, name)
        liste = werkzeuge.ausfuehren(app, info, "aufgaben_liste", extra)
        assert [z["titel"] for z in liste["zeilen"]] == ["Vorher"]
    assert db_admin.execute("select titel from event.aufgabe").fetchall() == [{"titel": "Vorher"}]
    # Neu waehlen geht fuer ein archiviertes Event nicht (Marke bleibt aber gueltig).
    with pytest.raises(ValueError):
        werkzeuge.ausfuehren(app, lea, "event_waehlen", {"suche": "Sommerfest Nord"})
    assert marke.marke_aufloesen(app, lea, m) == welt["nord"]
