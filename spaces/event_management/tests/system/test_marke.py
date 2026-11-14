"""Marke (Event-Wahl) und Bestaetigungscode (system/marke.py) gegen den Wegwerf-Cluster.

Im Betrieb laufen alle Funktionen mit der App-DSN des Werkzeug-Servers (event_app hat
keine Grants auf marke/bestaetigung, nur die SD-Funktionen aus 004); deshalb laufen die
Tests ueber `db_app()`, die Fachschicht-Pruefungen zusaetzlich ueber `db_admin`.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from spaces.event_management.system import kern, marke, schluessel
from spaces.event_management.system.kontext import NichtGefunden

BEGINN = datetime(2027, 3, 1, 18, 0, tzinfo=timezone.utc)
MARKE_UNGUELTIG = "Marke ungültig oder abgelaufen – bitte Event neu wählen"
CODE_UNGUELTIG = "Bestätigungscode ungültig, abgelaufen oder für eine andere Aktion"
_FORMAT_MARKE = re.compile(r"mk_[a-hjkmnp-z2-9]{12}")
_FORMAT_CODE = re.compile(r"bs_[a-hjkmnp-z2-9]{8}")


def _firma(admin: Any, kurz: str) -> UUID:
    return admin.execute(
        "insert into event.firma (name, kurz) values (%s, %s) returning id",
        ("Beispiel " + kurz, kurz),
    ).fetchone()["id"]


def _event(admin: Any, firma: UUID, *, quelle: str = "neu", status: str = "geplant") -> UUID:
    return admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende, quelle, status)"
        " values (%s, 'weihnachtsfeier', 'Feier', %s, %s, %s, %s) returning id",
        (firma, BEGINN, BEGINN + timedelta(hours=3), quelle, status),
    ).fetchone()["id"]


def _person(admin: Any, mail: str, betreiber: bool = False) -> UUID:
    return admin.execute(
        "insert into event.person (mail, name, betreiber) values (%s, 'Erika Beispiel', %s)"
        " returning id",
        (mail, betreiber),
    ).fetchone()["id"]


def _mitglied(admin: Any, person: UUID, firma: UUID, rolle: str) -> None:
    admin.execute("insert into event.mitgliedschaft values (%s, %s, %s)", (person, firma, rolle))


def _sha(wert: str) -> str:
    return hashlib.sha256(wert.encode("utf-8")).hexdigest()


def _info(admin: Any, art: str, **argumente: Any) -> schluessel.SchluesselInfo:
    _, klartext = schluessel.ausstellen(admin, art=art, bezeichnung="Test", **argumente)
    return schluessel.pruefen(admin, klartext)


def _anzahl(admin: Any, tabelle: str) -> int:
    return admin.execute(f"select count(*) as n from event.{tabelle}").fetchone()["n"]


@pytest.fixture
def welt(db_admin: Any) -> dict[str, Any]:
    a = _firma(db_admin, "firma-a")
    b = _firma(db_admin, "firma-b")
    lea = _person(db_admin, "lea@example.org")
    zweite = _person(db_admin, "zweite@example.org")
    _mitglied(db_admin, lea, a, "leitung")
    _mitglied(db_admin, lea, b, "mitglied")  # Mitglied reicht fuer die Event-Wahl nicht
    _mitglied(db_admin, zweite, a, "leitung")
    return {
        "a": a, "b": b, "lea": lea, "zweite": zweite,
        "ea": _event(db_admin, a), "ea2": _event(db_admin, a), "eb": _event(db_admin, b),
        "legacy": _event(db_admin, a, quelle="legacy"),
        "archiviert": _event(db_admin, a, status="archiviert"),
    }


# --- Marke --------------------------------------------------------------------------


def test_marke_gilt_nur_fuer_ausstellenden_schluessel(db_admin, db_app, welt):
    app = db_app()
    lea = _info(db_admin, "leitung", person_id=welt["lea"])
    zweite = _info(db_admin, "leitung", person_id=welt["zweite"])

    wert = marke.marke_ausstellen(app, lea, welt["ea"])
    assert _FORMAT_MARKE.fullmatch(wert)
    assert marke.marke_ausstellen(app, lea, welt["ea"]) != wert  # jedes Mal neu
    # Gespeichert ist nur der Hash.
    zeilen = db_admin.execute("select row_to_json(m)::text as j, m.wert_hash"
                              " from event.marke m").fetchall()
    assert _sha(wert) in [z["wert_hash"] for z in zeilen]
    assert all(wert not in z["j"] and wert[3:] not in z["j"] for z in zeilen)

    assert marke.marke_aufloesen(app, lea, wert) == welt["ea"]
    assert marke.marke_aufloesen(db_admin, lea, wert) == welt["ea"]
    # Gleiche Firma, gleiche Rolle, anderer Schluessel: die Marke gilt nicht.
    with pytest.raises(NichtGefunden) as e:
        marke.marke_aufloesen(app, zweite, wert)
    assert str(e.value) == MARKE_UNGUELTIG
    assert lea.schluessel_hash not in str(e.value) and wert not in str(e.value)

    # Widerrufener Schluessel: seine Marken gelten nicht mehr.
    schluessel.widerrufen(db_admin, lea.id)
    with pytest.raises(NichtGefunden) as e:
        marke.marke_aufloesen(app, lea, wert)
    assert str(e.value) == MARKE_UNGUELTIG


def test_marke_laeuft_nach_30_min_ab(db_admin, db_app, welt):
    app = db_app()
    assert marke.MARKE_MINUTEN == 30
    info = _info(db_admin, "leitung", person_id=welt["lea"])
    wert = marke.marke_ausstellen(app, info, welt["ea"])
    rest = db_admin.execute("select ablauf - now() as rest from event.marke where wert_hash = %s",
                            (_sha(wert),)).fetchone()["rest"]
    assert timedelta(minutes=29) < rest <= timedelta(minutes=30)
    assert marke.marke_aufloesen(app, info, wert) == welt["ea"]

    db_admin.execute("update event.marke set ablauf = now() - interval '1 minute'"
                     " where wert_hash = %s", (_sha(wert),))
    unbekannt = "mk_" + "a" * 12
    schlecht: list[Any] = [wert, unbekannt, "", wert[3:], wert + "a", wert[:-1] + "0",
                           wert[:-1] + "\x00", None, 42]
    for w in schlecht:
        with pytest.raises(NichtGefunden) as e:
            marke.marke_aufloesen(app, info, w)
        assert str(e.value) == MARKE_UNGUELTIG


def test_marke_tolerant_gegen_gross_und_leerzeichen(db_admin, db_app, welt):
    """Telegram schreibt den Anfang gross, ein LLM haengt Leerzeichen an."""
    app = db_app()
    info = _info(db_admin, "leitung", person_id=welt["lea"])
    wert = marke.marke_ausstellen(app, info, welt["ea"])
    for variante in (wert.upper(), wert.capitalize(), "  " + wert + "\n",
                     " " + wert.upper() + "\t"):
        assert marke.marke_aufloesen(app, info, variante) == welt["ea"]
    for kaputt in (wert[:3] + " " + wert[3:], wert.replace("_", "-"), "mk_" + wert[3:-1] + "0",
                   "mk__" + wert[3:], "  "):
        with pytest.raises(NichtGefunden) as e:
            marke.marke_aufloesen(app, info, kaputt)
        assert str(e.value) == MARKE_UNGUELTIG


def test_marke_fuer_fremde_firma_nicht_ausstellbar(db_admin, db_app, welt):
    """Leitung A waehlt Event B nicht (auch nicht als Mitglied von B); ebenso wenig ein
    Event-Schluessel ein fremdes Event oder ein Melde-Schluessel irgendein Event."""
    lea = _info(db_admin, "leitung", person_id=welt["lea"])
    ev_schluessel = _info(db_admin, "event", event_id=welt["ea"])
    melden = _info(db_admin, "melden")
    faelle = [(lea, welt["eb"]), (lea, uuid4()), (ev_schluessel, welt["ea2"]),
              (ev_schluessel, welt["eb"]), (melden, welt["ea"])]
    for conn in (db_app(), db_admin):
        for info, event_id in faelle:
            with pytest.raises(NichtGefunden):
                marke.marke_ausstellen(conn, info, event_id)
    assert _anzahl(db_admin, "marke") == 0

    # Gegenprobe: Betreiber waehlt jedes Event, der Event-Schluessel sein eigenes.
    betreiber = _info(db_admin, "betreiber")
    app = db_app()
    wert = marke.marke_ausstellen(app, betreiber, welt["eb"])
    assert marke.marke_aufloesen(app, betreiber, wert) == welt["eb"]
    assert marke.marke_aufloesen(
        app, ev_schluessel, marke.marke_ausstellen(app, ev_schluessel, welt["ea"])) == welt["ea"]

    # Leitung verloren: Ausstellen scheitert, bestehende Marke gilt nicht mehr.
    alt = marke.marke_ausstellen(app, lea, welt["ea"])
    db_admin.execute("update event.mitgliedschaft set rolle = 'mitglied'"
                     " where person_id = %s and firma_id = %s", (welt["lea"], welt["a"]))
    with pytest.raises(NichtGefunden):
        marke.marke_ausstellen(app, lea, welt["ea"])
    with pytest.raises(NichtGefunden):
        marke.marke_aufloesen(app, lea, alt)


def test_marke_legacy_und_archiviert_abgelehnt(db_admin, db_app, welt):
    lea = _info(db_admin, "leitung", person_id=welt["lea"])
    betreiber = _info(db_admin, "betreiber")
    for conn in (db_app(), db_admin):
        for info in (lea, betreiber):
            with pytest.raises(ValueError) as e:
                marke.marke_ausstellen(conn, info, welt["legacy"])
            assert str(e.value) == "Legacy-Event: bitte die alte App nutzen"
            with pytest.raises(ValueError) as e:
                marke.marke_ausstellen(conn, info, welt["archiviert"])
            assert str(e.value) == "Event ist archiviert"
    assert _anzahl(db_admin, "marke") == 0


# --- Bestaetigung -------------------------------------------------------------------


def test_bestaetigung_einmalig(db_admin, db_app, welt):
    app = db_app()
    assert marke.BESTAETIGUNG_MINUTEN == 5
    info = _info(db_admin, "leitung", person_id=welt["lea"])
    ziel = {"event_id": str(welt["ea"])}
    code = marke.bestaetigung_ausstellen(app, info, "event_loeschen", ziel)
    assert _FORMAT_CODE.fullmatch(code)
    zeile = db_admin.execute("select row_to_json(b)::text as j, b.code_hash, b.ablauf - now()"
                             " as rest from event.bestaetigung b").fetchone()
    assert zeile["code_hash"] == _sha(code) and code[3:] not in zeile["j"]
    assert timedelta(minutes=4) < zeile["rest"] <= timedelta(minutes=5)

    assert marke.bestaetigung_einloesen(app, info, code, "event_loeschen", dict(ziel)) is None
    with pytest.raises(ValueError) as e:
        marke.bestaetigung_einloesen(app, info, code, "event_loeschen", ziel)
    assert str(e.value) == CODE_UNGUELTIG
    assert code not in str(e.value)

    zwei = {"firma_id": str(welt["a"]), "kontakt_id": str(uuid4())}
    code2 = marke.bestaetigung_ausstellen(app, info, "kontakt_entfernen", zwei)
    # Reihenfolge der Ziel-Schluessel egal (jsonb-Gleichheit).
    marke.bestaetigung_einloesen(
        app, info, code2, "kontakt_entfernen", dict(reversed(list(zwei.items()))))


def test_bestaetigung_falsche_aktion_oder_ziel(db_admin, db_app, welt):
    """Review Focus 3: ein Code gilt nur fuer genau seinen Schluessel, seine Aktion und
    sein Ziel; Fehlversuche verbrauchen ihn nicht."""
    app = db_app()
    lea = _info(db_admin, "leitung", person_id=welt["lea"])
    zweite = _info(db_admin, "leitung", person_id=welt["zweite"])
    ziel = {"firma_id": str(welt["a"]), "kontakt_id": str(uuid4())}
    code = marke.bestaetigung_ausstellen(app, lea, "kontakt_entfernen", ziel)

    falsch: list[tuple[Any, str, Any, Any]] = [
        (lea, code, "event_loeschen", ziel),                                  # andere Aktion
        (lea, code, "kontakt_entfernen", {**ziel, "kontakt_id": str(uuid4())}),  # anderes Ziel
        (lea, code, "kontakt_entfernen", {"kontakt_id": ziel["kontakt_id"]}),   # Teilziel
        (lea, code, "kontakt_entfernen", {**ziel, "event_id": str(welt["ea"])}),  # Zusatz
        (lea, code, "kontakt_entfernen", {}),
        (zweite, code, "kontakt_entfernen", ziel),                            # anderer Schluessel
        (lea, code[:-1] + "0", "kontakt_entfernen", ziel),                  # Zeichen fremd
        (lea, code[3:], "kontakt_entfernen", ziel),
        (lea, "bs_" + "a" * 8, "kontakt_entfernen", ziel),
        (lea, None, "kontakt_entfernen", ziel),
        (lea, code, "Kontakt-Entfernen", ziel),                               # Aktion kaputt
        (lea, code, "kontakt_entfernen", {"kontakt_id": 7}),                  # Ziel kein Text
        (lea, code, "kontakt_entfernen", ["kontakt_id"]),
    ]
    for info, c, aktion, z in falsch:
        with pytest.raises(ValueError) as e:
            marke.bestaetigung_einloesen(app, info, c, aktion, z)
        assert str(e.value) == CODE_UNGUELTIG
    marke.bestaetigung_einloesen(app, lea, code, "kontakt_entfernen", ziel)

    # Ausstellen mit kaputter Aktion oder kaputtem Ziel: ValueError, nichts gespeichert.
    vorher = _anzahl(db_admin, "bestaetigung")
    for aktion, z in [("Event-Loeschen", ziel), ("", ziel), ("a" * 65, ziel),
                      ("kontakt_entfernen", {"kontakt_id": 7}),
                      ("kontakt_entfernen", {"kontakt_id": "x\x00"}),
                      ("kontakt_entfernen", {1: "x"}), ("kontakt_entfernen", "kontakt")]:
        with pytest.raises(ValueError):
            marke.bestaetigung_ausstellen(app, lea, aktion, z)  # type: ignore[arg-type]
    assert _anzahl(db_admin, "bestaetigung") == vorher

    # Widerrufener Schluessel: weder ausstellen noch einloesen.
    offen = marke.bestaetigung_ausstellen(app, zweite, "event_loeschen", {"event_id": "x"})
    schluessel.widerrufen(db_admin, zweite.id)
    with pytest.raises(ValueError):
        marke.bestaetigung_einloesen(app, zweite, offen, "event_loeschen", {"event_id": "x"})
    with pytest.raises(NichtGefunden):
        marke.bestaetigung_ausstellen(app, zweite, "event_loeschen", {"event_id": "x"})


def test_bestaetigung_tolerant_gegen_gross_und_leerzeichen(db_admin, db_app, welt):
    app = db_app()
    info = _info(db_admin, "leitung", person_id=welt["lea"])
    ziel = {"event_id": str(welt["ea"])}
    code = marke.bestaetigung_ausstellen(app, info, "event_loeschen", ziel)
    for kaputt in (code[:3] + " " + code[3:], code.replace("_", "-"), code[:-1] + "0", "   "):
        with pytest.raises(ValueError) as e:
            marke.bestaetigung_einloesen(app, info, kaputt, "event_loeschen", ziel)
        assert str(e.value) == CODE_UNGUELTIG
    # Kaputte Varianten haben ihn nicht verbraucht; Telegram-Grossschreibung + Leerzeichen geht.
    marke.bestaetigung_einloesen(app, info, " " + code.capitalize() + "\n", "event_loeschen", ziel)
    with pytest.raises(ValueError):
        marke.bestaetigung_einloesen(app, info, code.upper(), "event_loeschen", ziel)  # einmalig


def test_bestaetigung_abgelaufen(db_admin, db_app, welt):
    app = db_app()
    info = _info(db_admin, "betreiber")
    ziel = {"event_id": str(welt["ea"])}
    code = marke.bestaetigung_ausstellen(app, info, "event_loeschen", ziel)
    db_admin.execute("update event.bestaetigung set ablauf = now() - interval '1 minute'"
                     " where code_hash = %s", (_sha(code),))
    with pytest.raises(ValueError) as e:
        marke.bestaetigung_einloesen(app, info, code, "event_loeschen", ziel)
    assert str(e.value) == CODE_UNGUELTIG
    assert db_admin.execute("select benutzt from event.bestaetigung").fetchone()["benutzt"] is None


# --- Kontext mit Event --------------------------------------------------------------


def test_kontext_mit_event_setzt_event_bindung(db_admin, db_app, welt):
    app = db_app()
    lea = _info(db_admin, "leitung", person_id=welt["lea"])
    wert = marke.marke_ausstellen(app, lea, welt["ea"])
    k = marke.kontext_mit_event(app, lea, wert)
    assert k.event_id == welt["ea"]
    assert (k.person_id, k.actor, k.quelle, dict(k.rollen)) == (
        welt["lea"], "bot:leitung", "bot", {welt["a"]: "leitung"})
    assert kern.liste(app, k, welt["ea"], "aufgaben") == []
    # Gleiche Firma, Leitungsrecht vorhanden, aber die Marke bindet an Event ea.
    with pytest.raises(NichtGefunden):
        kern.liste(app, k, welt["ea2"], "aufgaben")
    with pytest.raises(NichtGefunden):
        kern.anlegen(app, k, welt["ea2"], "aufgaben", {"titel": "Saal buchen"})
    assert _anzahl(db_admin, "aufgabe") == 0

    # Andere Arten: Marke Pflicht und gueltig.
    with pytest.raises(ValueError):
        marke.kontext_mit_event(app, lea, None)
    with pytest.raises(NichtGefunden) as e:
        marke.kontext_mit_event(app, lea, "mk_" + "a" * 12)
    assert str(e.value) == MARKE_UNGUELTIG

    # Art event: Marke wird ignoriert, das Event steht fest.
    ev_info = _info(db_admin, "event", event_id=welt["ea2"])
    for m in (None, wert, "kaputt"):
        assert marke.kontext_mit_event(app, ev_info, m).event_id == welt["ea2"]
