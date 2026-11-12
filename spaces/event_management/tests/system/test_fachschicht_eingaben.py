"""Eingabepruefung in der Fachschicht selbst (nicht nur in der API): Text, Zahlen, None.

Jeder Aufrufer der Fachschicht (auch der spaetere Werkzeug-Server) bekommt einen
ValueError mit Feldnamen, nie einen Datenbankfehler und nie den Wert in der Meldung.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from spaces.event_management.system import events, firmen, kern, pruefung, verlauf, zugang
from spaces.event_management.system.kontext import Kontext
from spaces.event_management.system.mailer import SpeicherMailer

BERLIN = events.ZEITZONE
NUL = "a\x00b"
SURROGAT = "a\ud800b"
BASIS = "https://events.example.org"


class _Stolperdraht:
    """Verbindungs-Attrappe: jeder Zugriff laesst den Test scheitern."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"SQL-Zugriff vor der Pruefung: {name}")


STOLPER: Any = _Stolperdraht()


def _zeiten() -> tuple[datetime, datetime]:
    b = datetime.now(BERLIN).replace(microsecond=0) + timedelta(days=10)
    return b, b + timedelta(hours=3)


@pytest.fixture
def welt(db_admin: Any) -> dict[str, Any]:
    f = db_admin.execute("insert into event.firma (name, kurz) values ('Beispiel A', 'bsp-a')"
                         " returning id").fetchone()["id"]
    p = db_admin.execute("insert into event.person (mail, name) values ('a@example.org', 'Erika')"
                         " returning id").fetchone()["id"]
    db_admin.execute("insert into event.mitgliedschaft values (%s, %s, 'leitung')", (p, f))
    k = Kontext(person_id=p, betreiber=False, rollen={f: "leitung"})
    b, e = _zeiten()
    ev = db_admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende)"
        " values (%s, 'weihnachtsfeier', 'Feier', %s, %s) returning id", (f, b, e),
    ).fetchone()["id"]
    return {"f": f, "p": p, "k": k, "ev": ev}


def _anzahl(admin: Any, tabelle: str) -> int:
    return admin.execute(f"select count(*) as n from event.{tabelle}").fetchone()["n"]


# --- pruefung (Einheit) --------------------------------------------------------------


@pytest.mark.parametrize("schlecht", [NUL, SURROGAT, "\x00"])
def test_text_lehnt_nul_und_surrogate_ab(schlecht: str) -> None:
    with pytest.raises(ValueError, match="'titel'") as e:
        pruefung.text("titel", schlecht)
    assert schlecht not in str(e.value)


def test_text_typen() -> None:
    assert pruefung.text("x", "Grüße ✓") == "Grüße ✓"
    assert pruefung.text_oder_none("x", None) is None
    for schlecht in (None, 1, b"bytes", ["a"]):
        with pytest.raises(ValueError, match="'x'"):
            pruefung.text("x", schlecht)


@pytest.mark.parametrize("schlecht", [float("nan"), float("inf"), float("-inf"), True, "1"])
def test_zahl_lehnt_nicht_endliche_ab(schlecht: Any) -> None:
    with pytest.raises(ValueError, match="'betrag'"):
        pruefung.zahl("betrag", schlecht)


def test_zahl_ok() -> None:
    assert pruefung.zahl("x", 0) == 0 and pruefung.zahl("x", 1.5) == 1.5


@pytest.mark.parametrize(("typ", "wert"), [
    ("zahl", float("nan")), ("zahl", float("inf")), ("text", NUL), ("langtext", SURROGAT),
    ("auswahl", NUL),
])
def test_metadatum_lehnt_kaputte_werte_ab(typ: str, wert: Any) -> None:
    assert pruefung.metadatum_ok(typ, (NUL,), wert) is False


# --- events ----------------------------------------------------------------------------


@pytest.mark.parametrize(("feld", "wert"), [
    ("name", None), ("name", 5), ("name", NUL), ("name", SURROGAT),
    ("beginn", None), ("beginn", "2030-01-01T10:00:00"), ("ende", None),
    ("ort_adresse", NUL), ("ort_adresse", 7), ("beschreibung", SURROGAT),
    ("kategorie", NUL), ("kategorie", ["fest"]),
    ("metadaten", {"motto": NUL}), ("metadaten", {"kapazitaet": float("nan")}),
    ("metadaten", {"kapazitaet": float("inf")}), ("metadaten", ["kein", "objekt"]),
])
def test_event_anlegen_prueft_eingaben(db_admin: Any, db_app: Any, welt: dict[str, Any],
                                       feld: str, wert: Any) -> None:
    b, e = _zeiten()
    args: dict[str, Any] = {"kategorie": "weihnachtsfeier", "name": "Feier", "beginn": b,
                            "ende": e}
    args[feld] = wert
    vorher = _anzahl(db_admin, "event")
    with pytest.raises(ValueError) as fehler:
        events.event_anlegen(db_app(), welt["k"], welt["f"], **args)
    assert not isinstance(wert, str) or wert not in str(fehler.value)
    assert _anzahl(db_admin, "event") == vorher


@pytest.mark.parametrize(("feld", "wert"), [
    ("name", None), ("name", 5), ("name", NUL), ("beginn", None), ("ende", None),
    ("beginn", "2030-01-01T10:00:00"), ("ort_adresse", NUL), ("ort_adresse", 7),
    ("beschreibung", SURROGAT), ("metadaten", {"motto": NUL}),
    ("metadaten", {"kapazitaet": float("nan")}), ("metadaten", ["kein", "objekt"]),
])
def test_event_aendern_prueft_eingaben(db_admin: Any, db_app: Any, welt: dict[str, Any],
                                       feld: str, wert: Any) -> None:
    vorher = db_admin.execute("select * from event.event").fetchall()
    with pytest.raises(ValueError):
        events.event_aendern(db_app(), welt["k"], welt["ev"], **{feld: wert})
    assert db_admin.execute("select * from event.event").fetchall() == vorher
    assert _anzahl(db_admin, "verlauf") == 0


def test_katalog_und_verlauf_pruefen_text(db_app: Any, welt: dict[str, Any]) -> None:
    conn = db_app()
    for kw in ({"suche": NUL}, {"suche": SURROGAT}, {"kategorie": NUL}):
        with pytest.raises(ValueError):
            events.katalog(conn, welt["k"], **kw)
    with pytest.raises(ValueError):
        verlauf.lesen(conn, welt["k"], welt["ev"], actor=NUL)


@pytest.mark.parametrize("limit", [0, -1, 501, True, 1.5, "10"])
def test_verlauf_limit_grenzen(db_app: Any, welt: dict[str, Any], limit: Any) -> None:
    with pytest.raises(ValueError, match="limit"):
        verlauf.lesen(db_app(), welt["k"], welt["ev"], limit=limit)


def test_verlauf_limit_ok(db_app: Any, welt: dict[str, Any]) -> None:
    for limit in (1, 500):
        assert verlauf.lesen(db_app(), welt["k"], welt["ev"], limit=limit) == []


# --- kern ------------------------------------------------------------------------------


@pytest.mark.parametrize(("baustein", "werte"), [
    ("aufgaben", {"titel": NUL}), ("aufgaben", {"titel": SURROGAT}),
    ("aufgaben", {"titel": "ok", "beschreibung": NUL}),
    ("budget", {"titel": "B", "art": "kosten", "betrag_cent": float("nan")}),
    ("budget", {"titel": "B", "art": "kosten", "betrag_cent": float("inf")}),
    ("ablauf", {"titel": "A", "beginn": "2030-01-01T10:00:00", "ort": NUL}),
])
def test_kern_prueft_text_und_zahlen(db_admin: Any, db_app: Any, welt: dict[str, Any],
                                     baustein: str, werte: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        kern.anlegen(db_app(), welt["k"], welt["ev"], baustein, werte)
    assert _anzahl(db_admin, "verlauf") == 0


@pytest.mark.parametrize(("feld", "wert"), [
    ("name", None), ("name", 3), ("name", NUL), ("mail", NUL), ("telegram", SURROGAT),
])
def test_kontakt_anlegen_prueft_eingaben(db_admin: Any, db_app: Any, welt: dict[str, Any],
                                         feld: str, wert: Any) -> None:
    args: dict[str, Any] = {"name": "Max Muster"}
    args[feld] = wert
    with pytest.raises(ValueError, match=feld):
        kern.kontakt_anlegen(db_app(), welt["k"], welt["f"], **args)
    assert _anzahl(db_admin, "kontakt") == 0


def test_kontakte_suche_prueft_text(db_app: Any, welt: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        kern.kontakte(db_app(), welt["k"], welt["f"], suche=NUL)


# --- firmen und zugang ---------------------------------------------------------------


def test_firma_und_person_pruefen_text(db_admin: Any, db_app: Any) -> None:
    b = Kontext(person_id=None, betreiber=True)
    for name, kurz in ((NUL, "neu-a"), (SURROGAT, "neu-b"), (None, "neu-c"), ("Neu", None)):
        with pytest.raises(ValueError):
            firmen.firma_anlegen(db_app(), b, name, kurz)  # type: ignore[arg-type]
    for mail, name in ((NUL + "@example.org", "X"), ("x@example.org", SURROGAT)):
        with pytest.raises(ValueError):
            firmen.person_finden_oder_anlegen(db_app(), mail, name)
    assert _anzahl(db_admin, "firma") == 0 and _anzahl(db_admin, "person") == 0


@pytest.mark.parametrize(("mail", "name"), [
    ("x@example.org", NUL), ("x@example.org", SURROGAT), (NUL + "@example.org", "X"),
    ("x@example.org", None),
])
def test_einladen_prueft_text_vor_sql(welt: dict[str, Any], mail: Any, name: Any) -> None:
    m = SpeicherMailer()
    with pytest.raises(ValueError):
        zugang.einladen(STOLPER, welt["k"], m, mail, name, welt["f"], "mitglied", BASIS)
    assert m.gesendet == []


def test_einloesen_prueft_namen(db_admin: Any, db_app: Any, welt: dict[str, Any]) -> None:
    m = SpeicherMailer()
    zugang.einladen(db_app(), welt["k"], m, "neu@example.org", "Neu", welt["f"], "mitglied",
                    BASIS)
    token = m.gesendet[-1].text.split("/einladung/")[1].split()[0]
    with pytest.raises(ValueError):
        zugang.einladung_einloesen(db_app(), token, "ein-langes-passwort-1", NUL)
    zeile = db_admin.execute("select name, passwort_hash from event.person"
                             " where mail = 'neu@example.org'").fetchone()
    assert zeile["name"] == "Neu" and zeile["passwort_hash"] is None
    assert zugang.einladung_einloesen(db_app(), token, "ein-langes-passwort-1", "Nina") is not None
