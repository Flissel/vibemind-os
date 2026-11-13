"""Nachrichten (system/nachrichten.py, db/005_nachrichten.sql): Gruppe per Telegram,
Mail-Entwuerfe mit Freigabe, Versand; dazu die Werkzeuge und die API-Routen.

Fachschicht ueber `db_app()` (RLS aktiv, Kontext je Transaktion), Nachlesen und Versand
(Admin-DSN) ueber `db_admin`. Telegram nur ueber `SpeicherTelegram`, Mail nur ueber
`SpeicherMailer`; kein Netz.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from spaces.event_management.system import (
    api, marke, mcp_server, nachrichten, schluessel, werkzeuge, zugang,
)
from spaces.event_management.system.kontext import KeinRecht, Kontext, NichtGefunden
from spaces.event_management.system.mailer import SpeicherMailer
from spaces.event_management.system.telegram import SpeicherTelegram

BEGINN = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
CHAT = -1001234567890
CHAT_ANDERS = -1001234567891
PW = "ein-langes-passwort-1"
GEHEIM = b"api-testgeheimnis-0123456789abcdef"
BASIS = "https://events.example.org"
KUNDENTEXT = "Geheimer Kundentext 4711"


@pytest.fixture(autouse=True)
def _sperre_leeren() -> Iterator[None]:
    zugang._sperre_zuruecksetzen()
    yield
    zugang._sperre_zuruecksetzen()


# --- Testdaten --------------------------------------------------------------------


def _id(admin: Any, abfrage: str, werte: tuple[object, ...]) -> Any:
    return admin.execute(abfrage, werte).fetchone()["id"]


def _person(admin: Any, mail: str, *, betreiber: bool = False) -> UUID:
    return _id(admin, "insert into event.person (mail, name, passwort_hash, betreiber)"
                      " values (%s, %s, %s, %s) returning id",
               (mail, "Person " + mail.split("@")[0], zugang.passwort_hash(PW), betreiber))


def _event(admin: Any, firma: UUID, name: str) -> UUID:
    return _id(admin, "insert into event.event (firma_id, kategorie, name, beginn, ende)"
                      " values (%s, 'fest', %s, %s, %s) returning id",
               (firma, name, BEGINN, BEGINN + timedelta(hours=3)))


def _teilnehmer(admin: Any, firma: UUID, event: UUID, name: str, mail: str | None,
                zusage: str) -> None:
    kontakt = _id(admin, "insert into event.kontakt (firma_id, name, mail) values (%s, %s, %s)"
                         " returning id", (firma, name, mail))
    admin.execute("insert into event.teilnehmende (event_id, firma_id, kontakt_id, zusage)"
                  " values (%s, %s, %s, %s)", (event, firma, kontakt, zusage))


def _gruppe(admin: Any, event: UUID, firma: UUID, person: UUID, *, status: str = "bestaetigt",
            chat: int = CHAT, code: str = "ABCD2345") -> None:
    admin.execute(
        "insert into event.verbindung (art, code_hash, event_id, firma_id, person_id, ablauf,"
        " chat_id, chat_titel, status, bestaetigt) values ('gruppe', %s, %s, %s, %s,"
        " now() + interval '15 minutes', %s, 'Beispielgruppe', %s,"
        " case when %s = 'bestaetigt' then now() end)",
        (hashlib.sha256(code.encode("utf-8")).hexdigest(), event, firma, person, chat, status,
         status))


@pytest.fixture
def welt(db_admin: Any) -> dict[str, Any]:
    a = _id(db_admin, "insert into event.firma (name, kurz) values (%s, %s) returning id",
            ("Beispiel GmbH", "firma-a"))
    b = _id(db_admin, "insert into event.firma (name, kurz) values (%s, %s) returning id",
            ("Muster AG", "firma-b"))
    lea, mia = _person(db_admin, "lea@example.org"), _person(db_admin, "mia@example.org")
    lb = _person(db_admin, "leitung-b@example.org")
    bea = _person(db_admin, "bea@example.org", betreiber=True)
    for person, firma, rolle in ((lea, a, "leitung"), (mia, a, "mitglied"), (lb, b, "leitung")):
        db_admin.execute("insert into event.mitgliedschaft values (%s, %s, %s)",
                         (person, firma, rolle))
    fest, anderes, fremd = (_event(db_admin, a, "Sommerfest"), _event(db_admin, a, "Winterfest"),
                            _event(db_admin, b, "Hoffest"))
    for name, mail, zusage in (("Anna", "anna@example.org", "zugesagt"),
                               ("Anna doppelt", " ANNA@example.org ", "zugesagt"),
                               ("Ben", "ben@example.org", "zugesagt"),
                               ("Cara", "cara@example.org", "vielleicht"),
                               ("Dora", "dora@example.org", "abgesagt"),
                               ("Emil", None, "zugesagt"),
                               ("Leer", "   ", "zugesagt")):
        _teilnehmer(db_admin, a, fest, name, mail, zusage)
    _teilnehmer(db_admin, a, anderes, "Fritz", "fritz@example.org", "zugesagt")
    _teilnehmer(db_admin, b, fremd, "Gerd", "gerd@example.org", "zugesagt")
    return {
        "a": a, "b": b, "lea": lea, "mia": mia, "lb": lb, "bea": bea,
        "fest": fest, "anderes": anderes, "fremd": fremd,
        "leitung": Kontext(person_id=lea, betreiber=False, rollen={a: "leitung"}),
        "mitglied": Kontext(person_id=mia, betreiber=False, rollen={a: "mitglied"}),
        "fremd_leitung": Kontext(person_id=lb, betreiber=False, rollen={b: "leitung"}),
        "betreiber": Kontext(person_id=bea, betreiber=True),
        "bot": Kontext(person_id=None, betreiber=False, rollen={a: "mitglied"}, event_id=fest,
                       actor="bot:event", quelle="bot"),
    }


def _nachrichten(admin: Any) -> list[dict[str, Any]]:
    return admin.execute("select * from event.nachricht order by angelegt, id").fetchall()


def _schnappschuss(admin: Any, nid: object) -> dict[str, str]:
    return {z["mail"]: z["status"] for z in admin.execute(
        "select mail, status from event.nachricht_empfaenger where nachricht_id = %s",
        (nid,)).fetchall()}


def _status(admin: Any, nid: object) -> str:
    return str(admin.execute("select status from event.nachricht where id = %s",
                             (nid,)).fetchone()["status"])


# --- Gruppe -------------------------------------------------------------------------


def test_an_gruppe_ohne_verbindung_abgelehnt(db_admin, db_app, welt):
    app, t = db_app(), SpeicherTelegram()
    # Offene/gemeldete/verworfene Verbindungen zaehlen nicht, die bestaetigte Gruppe eines
    # anderen Events auch nicht.
    _gruppe(db_admin, welt["fest"], welt["a"], welt["lea"], status="gemeldet", code="GEMELD23")
    _gruppe(db_admin, welt["fest"], welt["a"], welt["lea"], status="verworfen", code="VERWOR23")
    _gruppe(db_admin, welt["anderes"], welt["a"], welt["lea"], chat=CHAT_ANDERS)
    for k in (welt["leitung"], welt["bot"]):
        with pytest.raises(ValueError) as e:
            nachrichten.an_gruppe(app, k, welt["fest"], "Hallo", telegram=t)
        assert str(e.value) == "Keine Telegram-Gruppe verbunden"
    assert t.gesendet == [] and _nachrichten(db_admin) == []


def test_an_gruppe_sendet_an_bestaetigte_gruppe(db_admin, db_app, welt, caplog):
    app, t = db_app(), SpeicherTelegram()
    # person_id der Gruppen-Verbindung ist nicht vertrauenswuerdig (setzt der Melder) und
    # spielt keine Rolle: hier eine Person der fremden Firma.
    _gruppe(db_admin, welt["fest"], welt["a"], welt["lb"])
    with caplog.at_level(logging.DEBUG):
        neu = nachrichten.an_gruppe(app, welt["bot"], welt["fest"], f"  {KUNDENTEXT}  ",
                                    telegram=t)
    assert t.gesendet == [(CHAT, KUNDENTEXT)]
    assert neu["kanal"] == "gruppe" and neu["status"] == "versendet"
    assert neu["text"] == KUNDENTEXT and neu["empfaenger"] is None
    zeile = _nachrichten(db_admin)[0]
    assert (zeile["status"], zeile["erstellt_von"], zeile["event_id"]) == (
        "versendet", "bot:event", welt["fest"])
    assert zeile["versendet"] is not None and zeile["fehler"] is None
    verlauf = db_admin.execute("select actor, quelle, baustein, aktion, ziel_id"
                               " from event.verlauf").fetchall()
    assert verlauf == [{"actor": "bot:event", "quelle": "bot", "baustein": "nachrichten",
                        "aktion": "gesendet", "ziel_id": zeile["id"]}]
    assert KUNDENTEXT not in caplog.text and str(CHAT) not in caplog.text

    # Mitglied darf auch (baustein_schreiben); fremde Leitung sieht das Event nicht.
    nachrichten.an_gruppe(app, welt["mitglied"], welt["fest"], "Zweite", telegram=t)
    with pytest.raises(NichtGefunden):
        nachrichten.an_gruppe(app, welt["fremd_leitung"], welt["fest"], "Fremd", telegram=t)
    # Ein an ein Event gebundener Kontext erreicht ein anderes Event nicht.
    _gruppe(db_admin, welt["anderes"], welt["a"], welt["lea"], chat=CHAT_ANDERS, code="ANDER234")
    with pytest.raises(NichtGefunden):
        nachrichten.an_gruppe(app, welt["bot"], welt["anderes"], "Daneben", telegram=t)
    assert [c for c, _ in t.gesendet] == [CHAT, CHAT]


def test_an_gruppe_telegram_fehler_wird_gespeichert(db_admin, db_app, welt, caplog):
    app, t = db_app(), SpeicherTelegram()
    t.fehler = "Telegram-Fehler: Forbidden: bot was kicked"
    _gruppe(db_admin, welt["fest"], welt["a"], welt["lea"])
    with caplog.at_level(logging.DEBUG):
        neu = nachrichten.an_gruppe(app, welt["leitung"], welt["fest"], KUNDENTEXT, telegram=t)
    assert neu["status"] == "fehler" and "kicked" in neu["fehler"]
    assert _nachrichten(db_admin)[0]["status"] == "fehler"
    assert KUNDENTEXT not in caplog.text


def test_an_gruppe_ohne_telegram_und_leere_texte(db_admin, db_app, welt):
    app = db_app()
    _gruppe(db_admin, welt["fest"], welt["a"], welt["lea"])
    with pytest.raises(ValueError) as e:
        nachrichten.an_gruppe(app, welt["leitung"], welt["fest"], "Hallo", telegram=None)
    assert str(e.value) == "Telegram ist nicht eingerichtet"
    for text in ("", "   ", "a\x00b", "x" * (nachrichten.TEXT_MAX + 1)):
        with pytest.raises(ValueError):
            nachrichten.an_gruppe(app, welt["leitung"], welt["fest"], text,
                                  telegram=SpeicherTelegram())
    assert _nachrichten(db_admin) == []


# --- Mail-Entwuerfe -------------------------------------------------------------------


def test_empfaenger_nach_zusage_und_mail(db_admin, db_app, welt):
    app, k = db_app(), welt["mitglied"]
    for zusage, anzahl in ((["zugesagt"], 2), (["zugesagt", "vielleicht"], 3),
                           (["vielleicht", "zugesagt", "zugesagt"], 3), (["offen"], 0),
                           (["abgesagt"], 1)):
        neu = nachrichten.mail_entwerfen(app, k, welt["fest"], "Info", "Hallo", zusage)
        assert neu["empfaenger"] == anzahl, zusage
    assert neu["zusage"] == ["abgesagt"]
    gespeichert = _nachrichten(db_admin)[1]
    assert gespeichert["empfaenger_filter"] == {"zusage": ["zugesagt", "vielleicht"]}
    for zusage in ([], ["ja"], "zugesagt", [None], None):
        with pytest.raises(ValueError):
            nachrichten.mail_entwerfen(app, k, welt["fest"], "Info", "Hallo", zusage)
    for betreff in ("", "  ", "Zeile\nZwei", "x" * (nachrichten.BETREFF_MAX + 1)):
        with pytest.raises(ValueError):
            nachrichten.mail_entwerfen(app, k, welt["fest"], betreff, "Hallo", ["zugesagt"])
    assert len(_nachrichten(db_admin)) == 5


def test_entwurf_versendet_nichts(db_admin, db_app, welt):
    app, mailer, t = db_app(), SpeicherMailer(), SpeicherTelegram()
    neu = nachrichten.mail_entwerfen(app, welt["bot"], welt["fest"], "Einladung", KUNDENTEXT,
                                     ["zugesagt"])
    assert neu["status"] == "entwurf" and neu["kanal"] == "mail"
    assert neu["betreff"] == "Einladung" and neu["empfaenger"] == 2
    assert nachrichten.versenden(db_admin, mailer) == 0
    assert mailer.gesendet == [] and t.gesendet == []
    assert _status(db_admin, neu["id"]) == "entwurf"
    aktionen = [z["aktion"] for z in db_admin.execute("select aktion from event.verlauf")]
    assert aktionen == ["entworfen"]


def test_freigeben_nur_leitung(db_admin, db_app, welt):
    app = db_app()
    entwurf = nachrichten.mail_entwerfen(app, welt["mitglied"], welt["fest"], "Info", "Hallo",
                                         ["zugesagt"])
    nid = entwurf["id"]
    for k in (welt["mitglied"], welt["bot"]):
        with pytest.raises(KeinRecht):
            nachrichten.freigeben(app, k, nid)
    with pytest.raises(NichtGefunden):
        nachrichten.freigeben(app, welt["fremd_leitung"], nid)
    with pytest.raises(NichtGefunden):
        nachrichten.freigeben(app, welt["leitung"], uuid4())
    assert _status(db_admin, nid) == "entwurf"

    frei = nachrichten.freigeben(app, welt["leitung"], nid)
    assert frei["status"] == "freigegeben" and frei["empfaenger"] == 2
    zeile = _nachrichten(db_admin)[0]
    assert zeile["freigegeben_von"] == welt["lea"] and zeile["freigegeben"] is not None
    for k in (welt["leitung"], welt["betreiber"]):
        with pytest.raises(ValueError) as e:
            nachrichten.freigeben(app, k, nid)
        assert str(e.value) == "Nur Entwürfe können freigegeben werden"

    # Betreiber darf freigeben; ohne Empfaenger gibt es nichts freizugeben.
    zweiter = nachrichten.mail_entwerfen(app, welt["leitung"], welt["fest"], "B", "Text",
                                         ["vielleicht"])
    assert nachrichten.freigeben(app, welt["betreiber"], zweiter["id"])["status"] == "freigegeben"
    leer = nachrichten.mail_entwerfen(app, welt["leitung"], welt["fest"], "C", "Text", ["offen"])
    with pytest.raises(ValueError) as e:
        nachrichten.freigeben(app, welt["leitung"], leer["id"])
    assert str(e.value) == "Keine Empfänger mit Mail für diese Zusage"
    # Gruppen-Nachrichten gibt es nur versendet; freigeben geht nicht.
    _gruppe(db_admin, welt["fest"], welt["a"], welt["lea"])
    gruppe = nachrichten.an_gruppe(app, welt["leitung"], welt["fest"], "G",
                                   telegram=SpeicherTelegram())
    with pytest.raises(ValueError):
        nachrichten.freigeben(app, welt["leitung"], gruppe["id"])


def test_verwerfen(db_admin, db_app, welt):
    app = db_app()
    nid = nachrichten.mail_entwerfen(app, welt["leitung"], welt["fest"], "Info", "Hallo",
                                     ["zugesagt"])["id"]
    with pytest.raises(NichtGefunden):
        nachrichten.verwerfen(app, welt["fremd_leitung"], nid)
    assert nachrichten.verwerfen(app, welt["mitglied"], nid)["status"] == "verworfen"
    with pytest.raises(ValueError):
        nachrichten.verwerfen(app, welt["mitglied"], nid)
    with pytest.raises(ValueError):
        nachrichten.freigeben(app, welt["leitung"], nid)
    assert _status(db_admin, nid) == "verworfen"


def test_liste_neueste_zuerst_mit_empfaengern(db_admin, db_app, welt):
    app = db_app()
    _gruppe(db_admin, welt["fest"], welt["a"], welt["lea"])
    nachrichten.an_gruppe(app, welt["leitung"], welt["fest"], "Gruppe", telegram=SpeicherTelegram())
    nachrichten.mail_entwerfen(app, welt["leitung"], welt["fest"], "Mail", "Text",
                               ["zugesagt", "vielleicht"])
    nachrichten.mail_entwerfen(app, welt["leitung"], welt["anderes"], "Anderes", "Text",
                               ["zugesagt"])
    liste = nachrichten.liste(app, welt["mitglied"], welt["fest"])
    assert [(n["kanal"], n["empfaenger"]) for n in liste] == [("mail", 3), ("gruppe", None)]
    assert liste[0]["zusage"] == ["zugesagt", "vielleicht"]
    assert "firma_id" not in liste[0]
    with pytest.raises(NichtGefunden):
        nachrichten.liste(app, welt["fremd_leitung"], welt["fest"])
    with pytest.raises(NichtGefunden):
        nachrichten.liste(app, welt["bot"], welt["anderes"])


def test_archiviert_und_legacy_abgelehnt(db_admin, db_app, welt):
    app = db_app()
    nid = nachrichten.mail_entwerfen(app, welt["leitung"], welt["fest"], "Info", "Hallo",
                                     ["zugesagt"])["id"]
    _gruppe(db_admin, welt["fest"], welt["a"], welt["lea"])
    db_admin.execute("update event.event set status = 'archiviert' where id = %s",
                     (welt["fest"],))
    t = SpeicherTelegram()
    for aufruf in (lambda: nachrichten.an_gruppe(app, welt["leitung"], welt["fest"], "X",
                                                 telegram=t),
                   lambda: nachrichten.mail_entwerfen(app, welt["leitung"], welt["fest"], "X",
                                                      "Y", ["zugesagt"]),
                   lambda: nachrichten.freigeben(app, welt["leitung"], nid)):
        with pytest.raises(ValueError) as e:
            aufruf()
        assert str(e.value) == "Event ist archiviert"
    assert t.gesendet == [] and _status(db_admin, nid) == "entwurf"
    assert len(nachrichten.liste(app, welt["leitung"], welt["fest"])) == 1
    db_admin.execute("update event.event set status = 'geplant', quelle = 'legacy'"
                     " where id = %s", (welt["fest"],))
    with pytest.raises(ValueError):
        nachrichten.mail_entwerfen(app, welt["leitung"], welt["fest"], "X", "Y", ["zugesagt"])


# --- Versand ------------------------------------------------------------------------


def _freigegeben(app: Any, welt: dict[str, Any], event: str, betreff: str,
                 zusage: list[str]) -> UUID:
    nid = nachrichten.mail_entwerfen(app, welt["leitung"], welt[event], betreff, KUNDENTEXT,
                                     zusage)["id"]
    nachrichten.freigeben(app, welt["leitung"], nid)
    return UUID(str(nid))


def test_versenden_nur_freigegebene_und_nur_einmal(db_admin, db_app, welt, pg_cluster,
                                                    caplog):
    app = db_app()
    entwurf = nachrichten.mail_entwerfen(app, welt["leitung"], welt["fest"], "Entwurf", "X",
                                         ["zugesagt"])["id"]
    verworfen = nachrichten.mail_entwerfen(app, welt["leitung"], welt["fest"], "Weg", "X",
                                           ["zugesagt"])["id"]
    nachrichten.verwerfen(app, welt["leitung"], verworfen)
    frei = _freigegeben(app, welt, "fest", "Programm", ["zugesagt", "vielleicht"])

    # Zweiter Versandlauf mitten im ersten (eigene Verbindung): findet nichts mehr.
    zweite = psycopg.connect(
        f"host=127.0.0.1 port={pg_cluster['port']} user=postgres"
        f" dbname={db_admin.info.dbname}", autocommit=True)
    zweite_laeufe: list[int] = []

    class Mailer(SpeicherMailer):
        def senden(self, an: str, betreff: str, text: str) -> None:
            if not zweite_laeufe:
                zweite_laeufe.append(nachrichten.versenden(zweite, SpeicherMailer()))
            super().senden(an, betreff, text)

    mailer = Mailer()
    try:
        with caplog.at_level(logging.DEBUG):
            assert nachrichten.versenden(db_admin, mailer) == 1
    finally:
        zweite.close()
    assert zweite_laeufe == [0]
    assert sorted(m.an for m in mailer.gesendet) == [
        "anna@example.org", "ben@example.org", "cara@example.org"]
    assert {(m.betreff, m.text) for m in mailer.gesendet} == {("Programm", KUNDENTEXT)}
    zeile = db_admin.execute("select * from event.nachricht where id = %s", (frei,)).fetchone()
    assert (zeile["status"], zeile["fehler"]) == ("versendet", None)
    assert _schnappschuss(db_admin, frei) == {"anna@example.org": "versendet",
                                              "ben@example.org": "versendet",
                                              "cara@example.org": "versendet"}
    assert zeile["versendet"] is not None
    assert _status(db_admin, entwurf) == "entwurf" and _status(db_admin, verworfen) == "verworfen"
    for geheim in (KUNDENTEXT, "anna@example.org", "Programm"):
        assert geheim not in caplog.text

    # Nochmal: nichts mehr zu tun, keine Mail doppelt.
    assert nachrichten.versenden(db_admin, mailer) == 0
    assert len(mailer.gesendet) == 3
    liste = nachrichten.liste(app, welt["leitung"], welt["fest"])
    assert {n["id"]: n["empfaenger"] for n in liste}[frei] == 3


def test_versenden_fehler_je_empfaenger(db_admin, db_app, welt, caplog):
    app = db_app()
    nid = _freigegeben(app, welt, "fest", "Programm", ["zugesagt"])
    andere = _freigegeben(app, welt, "anderes", "Anders", ["zugesagt"])

    class Wackel(SpeicherMailer):
        def senden(self, an: str, betreff: str, text: str) -> None:
            if an.startswith("ben"):
                raise OSError(f"550 kein Postfach {an}")
            super().senden(an, betreff, text)

    mailer = Wackel()
    with caplog.at_level(logging.DEBUG):
        assert nachrichten.versenden(db_admin, mailer) == 2
    assert sorted(m.an for m in mailer.gesendet) == ["anna@example.org", "fritz@example.org"]
    zeile = db_admin.execute("select * from event.nachricht where id = %s", (nid,)).fetchone()
    assert zeile["status"] == "fehler" and zeile["fehler"] == "1 von 2 Mails nicht versendet"
    assert _status(db_admin, andere) == "versendet"
    assert "ben@example.org" not in caplog.text and "OSError" in caplog.text
    assert db_admin.execute(
        "select fehler from event.nachricht_empfaenger where mail = 'ben@example.org'"
    ).fetchone() == {"fehler": "OSError"}
    assert nachrichten.versenden(db_admin, mailer) == 0


def test_versenden_cli(db_admin, db_app, welt, pg_cluster, tmp_path: Path, monkeypatch,
                       capsys):
    app = db_app()
    _freigegeben(app, welt, "fest", "Programm", ["zugesagt"])
    monkeypatch.delenv("EVENT_ADMIN_DSN_FILE", raising=False)
    assert nachrichten.main(["versenden"]) == 2
    datei = tmp_path / "dsn"
    datei.write_text(f"host=127.0.0.1 port={pg_cluster['port']} user=postgres"
                     f" dbname={db_admin.info.dbname}\r\n", encoding="utf-8")
    monkeypatch.setenv("EVENT_ADMIN_DSN_FILE", str(datei))
    mailer = SpeicherMailer()
    monkeypatch.setattr(nachrichten.SmtpMailer, "aus_umgebung", classmethod(lambda cls: mailer))
    assert nachrichten.main(["versenden"]) == 0
    assert len(mailer.gesendet) == 2
    ausgabe = capsys.readouterr()
    assert "1" in ausgabe.out and "example.org" not in ausgabe.out + ausgabe.err


# --- Zweites Tor: RLS auf nachricht (005) ---------------------------------------------


def test_rls_freigabe_nur_leitung(db_admin, db_app, welt):
    entwurf = _id(db_admin, "insert into event.nachricht (event_id, firma_id, kanal, betreff,"
                            " text) values (%s, %s, 'mail', 'B', 'T') returning id",
                  (welt["fest"], welt["a"]))
    freigeben = ("update event.nachricht set status = 'freigegeben', freigegeben = now(),"
                 " freigegeben_von = %s where id = %s")
    mitglied = db_app(firmen=[welt["a"]], person=welt["mia"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        mitglied.execute(freigeben, (welt["mia"], entwurf))
    leitung = db_app(firmen=[welt["a"]], person=welt["lea"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # nur als man selbst
        leitung.execute(freigeben, (welt["mia"], entwurf))
    # Direkt freigegeben/versendet anlegen geht fuer event_app nicht.
    for kanal, status in (("mail", "freigegeben"), ("mail", "versendet")):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            mitglied.execute("insert into event.nachricht (event_id, firma_id, kanal, text,"
                             " status) values (%s, %s, %s, 'T', %s)",
                             (welt["fest"], welt["a"], kanal, status))
    assert _status(db_admin, entwurf) == "entwurf"
    assert leitung.execute(freigeben, (welt["lea"], entwurf)).rowcount == 1
    # Freigegeben: fuer event_app nicht mehr aenderbar (auch nicht zurueck).
    for conn in (leitung, db_app(betreiber=True, person=welt["bea"])):
        assert conn.execute("update event.nachricht set status = 'entwurf', freigegeben = null,"
                            " freigegeben_von = null where id = %s", (entwurf,)).rowcount == 0
        assert conn.execute("update event.nachricht set text = 'Anders' where id = %s",
                            (entwurf,)).rowcount == 0
    assert _status(db_admin, entwurf) == "freigegeben"
    # Betreiber darf freigeben (als er selbst).
    zweiter = _id(db_admin, "insert into event.nachricht (event_id, firma_id, kanal, betreff,"
                            " text) values (%s, %s, 'mail', 'B', 'T') returning id",
                  (welt["fest"], welt["a"]))
    betreiber = db_app(betreiber=True, person=welt["bea"])
    assert betreiber.execute(freigeben, (welt["bea"], zweiter)).rowcount == 1


# --- Werkzeuge ------------------------------------------------------------------------

NACHRICHTEN_WERKZEUGE = {"nachricht_an_gruppe", "mail_entwerfen", "nachrichten_liste"}


def _info(admin: Any, art: str, **argumente: Any) -> schluessel.SchluesselInfo:
    _, klartext = schluessel.ausstellen(admin, art=art, bezeichnung="Test", **argumente)
    return schluessel.pruefen(admin, klartext)


def test_werkzeuge_je_schluessel_art(db_admin, db_app, welt):
    app = db_app()
    infos = {
        "event": _info(db_admin, "event", event_id=welt["fest"]),
        "leitung": _info(db_admin, "leitung", person_id=welt["lea"]),
        "betreiber": _info(db_admin, "betreiber", person_id=welt["bea"]),
        "betreiber_lesen": _info(db_admin, "betreiber_lesen", person_id=welt["bea"]),
        "melden": _info(db_admin, "melden"),
    }
    for art, info in infos.items():
        katalog = {w.name: w for w in werkzeuge.katalog(app, info)}
        erwartet = (NACHRICHTEN_WERKZEUGE if art in ("event", "leitung", "betreiber")
                    else {"nachrichten_liste"} if art == "betreiber_lesen" else set())
        assert NACHRICHTEN_WERKZEUGE & set(katalog) == erwartet, art
        for name in erwartet:
            w = katalog[name]
            assert w.schreibend is (name != "nachrichten_liste")
            assert ("marke" in w.eingabe.get("required", [])) is (art != "event"), (art, name)
            assert not {"event_id", "firma_id", "person_id"} & set(w.eingabe["properties"])
    mail = {w.name: w for w in werkzeuge.katalog(app, infos["event"])}["mail_entwerfen"]
    assert mail.eingabe["properties"]["zusage"] == {
        "type": "array", "minItems": 1, "maxItems": 4,
        "items": {"type": "string", "enum": ["offen", "zugesagt", "vielleicht", "abgesagt"]}}
    assert mail.eingabe["required"] == ["betreff", "text", "zusage"]


def test_werkzeug_nachricht_an_gruppe(db_admin, db_app, welt, caplog):
    app, t = db_app(), SpeicherTelegram()
    info = _info(db_admin, "event", event_id=welt["fest"])
    with pytest.raises(ValueError) as e:
        werkzeuge.ausfuehren(app, info, "nachricht_an_gruppe", {"text": "Hallo"}, telegram=t)
    assert str(e.value) == "Keine Telegram-Gruppe verbunden"
    _gruppe(db_admin, welt["fest"], welt["a"], welt["lea"])
    with pytest.raises(ValueError) as e:
        werkzeuge.ausfuehren(app, info, "nachricht_an_gruppe", {"text": "Hallo"})
    assert str(e.value) == "Telegram ist nicht eingerichtet"
    with caplog.at_level(logging.DEBUG):
        erg = werkzeuge.ausfuehren(app, info, "nachricht_an_gruppe", {"text": KUNDENTEXT},
                                   telegram=t)
    assert erg["event"] == {"name": "Sommerfest", "firma": "Beispiel GmbH"}
    assert erg["nachricht"]["status"] == "versendet" and t.gesendet == [(CHAT, KUNDENTEXT)]
    assert "event_id" not in erg["nachricht"] and "firma_id" not in erg["nachricht"]
    assert KUNDENTEXT not in caplog.text

    # Leitung ueber die Marke; Archiv lehnt ab (T5-a).
    lea = _info(db_admin, "leitung", person_id=welt["lea"])
    m = marke.marke_ausstellen(app, lea, welt["fest"])
    werkzeuge.ausfuehren(app, lea, "nachricht_an_gruppe", {"marke": m, "text": "Hi"}, telegram=t)
    db_admin.execute("update event.event set status = 'archiviert' where id = %s",
                     (welt["fest"],))
    for i, argumente in ((info, {"text": "Spaet"}), (lea, {"marke": m, "text": "Spaet"})):
        with pytest.raises(ValueError) as e:
            werkzeuge.ausfuehren(app, i, "nachricht_an_gruppe", argumente, telegram=t)
        assert str(e.value) == "Event ist archiviert"
        with pytest.raises(ValueError) as e:
            werkzeuge.ausfuehren(app, i, "mail_entwerfen", {
                **argumente, "betreff": "B", "zusage": ["zugesagt"]})
        assert str(e.value) == "Event ist archiviert"
    assert len(t.gesendet) == 2


def test_werkzeug_mail_entwerfen_und_liste(db_admin, db_app, welt, caplog):
    app = db_app()
    lea = _info(db_admin, "leitung", person_id=welt["lea"])
    m = marke.marke_ausstellen(app, lea, welt["fest"])
    with caplog.at_level(logging.DEBUG):
        erg = werkzeuge.ausfuehren(app, lea, "mail_entwerfen", {
            "marke": m, "betreff": "Programm", "text": KUNDENTEXT,
            "zusage": ["zugesagt", "vielleicht"]})
    assert erg["event"] == {"name": "Sommerfest", "firma": "Beispiel GmbH"}
    assert erg["empfaenger"] == 3 and erg["nachricht"]["status"] == "entwurf"
    assert "Freigabe" in erg["hinweis"]
    assert KUNDENTEXT not in caplog.text and "Programm" not in caplog.text
    for falsch in ([], ["ja"], "zugesagt", [1], ["zugesagt", None], ["offen"] * 5):
        with pytest.raises(ValueError) as e:
            werkzeuge.ausfuehren(app, lea, "mail_entwerfen", {
                "marke": m, "betreff": "B", "text": "T", "zusage": falsch})
        assert "zusage" in str(e.value) and "ja" not in str(e.value).replace("'zusage'", "")
    lesen = _info(db_admin, "betreiber_lesen", person_id=welt["bea"])
    ml = marke.marke_ausstellen(app, lesen, welt["fest"])
    liste = werkzeuge.ausfuehren(app, lesen, "nachrichten_liste", {"marke": ml})
    assert liste["event"]["name"] == "Sommerfest"
    assert [(n["betreff"], n["empfaenger"], n["status"]) for n in liste["nachrichten"]] == [
        ("Programm", 3, "entwurf")]
    for name in ("mail_entwerfen", "nachricht_an_gruppe"):
        with pytest.raises(NichtGefunden) as e:
            werkzeuge.ausfuehren(app, lesen, name, {"marke": ml, "text": "x"})
        assert str(e.value) == "unbekanntes Werkzeug"


def test_mcp_reicht_telegram_durch(db_admin, app_dsn, welt):
    _gruppe(db_admin, welt["fest"], welt["a"], welt["lea"])
    _, key = schluessel.ausstellen(db_admin, art="event", bezeichnung="Test",
                                   event_id=welt["fest"])
    t = SpeicherTelegram()
    app = mcp_server.erstelle_mcp_app(app_dsn, telegram=t)
    with TestClient(app, base_url="http://127.0.0.1:8171") as c:
        antwort = c.post("/mcp", headers={
            "Accept": "application/json, text/event-stream", "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "nachricht_an_gruppe", "arguments": {"text": "Hallo"}}})
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["result"]["isError"] is False
    assert t.gesendet == [(CHAT, "Hallo")]
    ohne = mcp_server.erstelle_mcp_app(app_dsn)
    with TestClient(ohne, base_url="http://127.0.0.1:8171") as c:
        antwort = c.post("/mcp", headers={
            "Accept": "application/json, text/event-stream", "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"}, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "nachricht_an_gruppe", "arguments": {"text": "Hallo"}}})
    ergebnis = antwort.json()["result"]
    assert ergebnis["isError"] is True
    assert ergebnis["content"][0]["text"] == "Telegram ist nicht eingerichtet"


# --- API --------------------------------------------------------------------------------

Fabrik = Callable[..., TestClient]


@pytest.fixture
def telegram() -> SpeicherTelegram:
    return SpeicherTelegram()


@pytest.fixture
def fabrik(app_dsn: str, telegram: SpeicherTelegram) -> Iterator[Fabrik]:
    app = api.erstelle_app(app_dsn, GEHEIM, SpeicherMailer(), BASIS, cookie_sicher=False,
                           telegram=telegram)
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


def test_api_ablauf(db_admin, welt, fabrik, telegram):
    lea, mia = fabrik("lea@example.org"), fabrik("mia@example.org")
    basis = f"/api/events/{welt['fest']}/nachrichten"

    r = mia.post(f"{basis}/gruppe", json={"text": "Hallo"})
    assert r.status_code == 422 and r.json() == {"detail": "Keine Telegram-Gruppe verbunden"}
    _gruppe(db_admin, welt["fest"], welt["a"], welt["lea"])
    r = mia.post(f"{basis}/gruppe", json={"text": "Hallo"})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "versendet" and telegram.gesendet == [(CHAT, "Hallo")]

    r = mia.post(f"{basis}/mail", json={"betreff": "Programm", "text": "Text",
                                        "zusage": ["zugesagt"]})
    assert r.status_code == 201, r.text
    entwurf = r.json()
    assert (entwurf["status"], entwurf["empfaenger"]) == ("entwurf", 2)
    for falsch in ({"betreff": "B", "text": "T", "zusage": []},
                   {"betreff": "B", "text": "T", "zusage": ["ja"]},
                   {"betreff": "B", "text": "T"},
                   {"betreff": "B", "text": "T", "zusage": ["zugesagt"], "an": "x@example.org"}):
        assert mia.post(f"{basis}/mail", json=falsch).status_code == 422, falsch

    liste = lea.get(basis).json()
    assert [(n["kanal"], n["status"]) for n in liste] == [("mail", "entwurf"),
                                                         ("gruppe", "versendet")]

    url = f"/api/nachrichten/{entwurf['id']}"
    r = mia.post(f"{url}/freigeben")
    assert r.status_code == 403
    ohne_csrf = fabrik("lea@example.org")
    del ohne_csrf.headers["X-CSRF"]
    assert ohne_csrf.post(f"{url}/freigeben").status_code == 403
    assert _status(db_admin, entwurf["id"]) == "entwurf"
    # T6-a: die angezeigte Zahl muss noch stimmen.
    r = lea.post(f"{url}/freigeben", json={"erwartet": 3})
    assert r.status_code == 422
    assert r.json() == {"detail": "Empfängerzahl hat sich geändert – bitte neu prüfen"}
    for falsch in ({"erwartet": -1}, {"erwartet": "2"}, {"erwartet": True}, {"anders": 2}):
        assert lea.post(f"{url}/freigeben", json=falsch).status_code == 422, falsch
    assert _status(db_admin, entwurf["id"]) == "entwurf"
    r = lea.post(f"{url}/freigeben", json={"erwartet": 2})
    assert r.status_code == 200 and r.json()["status"] == "freigegeben"
    assert r.json()["empfaenger"] == 2
    assert lea.post(f"{url}/verwerfen").status_code == 422

    zweiter = mia.post(f"{basis}/mail", json={"betreff": "B", "text": "T",
                                              "zusage": ["zugesagt"]}).json()
    r = mia.post(f"/api/nachrichten/{zweiter['id']}/verwerfen")
    assert r.status_code == 200 and r.json()["status"] == "verworfen"
    assert lea.post(f"/api/nachrichten/{uuid4()}/freigeben").status_code == 404


# --- Fixrunde 1: Schnappschuss, erwartet, Bot, Abbruch, Event zu ---------------------


def test_freigabe_friert_empfaenger_ein(db_admin, db_app, welt):
    """T6-a: wer nach der Freigabe Teilnehmende anlegt (auch der Bot), bekommt keine Mail."""
    app = db_app()
    nid = _freigegeben(app, welt, "fest", "Programm", ["zugesagt"])
    assert _schnappschuss(db_admin, nid) == {"anna@example.org": "offen",
                                             "ben@example.org": "offen"}
    _teilnehmer(db_admin, welt["a"], welt["fest"], "Spaet", "spaet@example.org", "zugesagt")
    # Live waeren es jetzt 3; die Liste zeigt die eingefrorene Zahl.
    eintrag = {n["id"]: n for n in nachrichten.liste(app, welt["leitung"], welt["fest"])}[nid]
    assert (eintrag["status"], eintrag["empfaenger"]) == ("freigegeben", 2)

    # Nachtraeglich in den Schnappschuss schreiben geht fuer event_app nicht, auch nicht fuer
    # die freigebende Leitung; aendern/loeschen gibt es gar nicht.
    leitung = db_app(firmen=[welt["a"]], person=welt["lea"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        leitung.execute("insert into event.nachricht_empfaenger (nachricht_id, mail)"
                        " values (%s, 'spaet@example.org')", (nid,))
    for sql in ("update event.nachricht_empfaenger set mail = 'x@example.org'",
                "delete from event.nachricht_empfaenger"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            leitung.execute(sql)

    mailer = SpeicherMailer()
    assert nachrichten.versenden(db_admin, mailer) == 1
    assert sorted(m.an for m in mailer.gesendet) == ["anna@example.org", "ben@example.org"]


def test_freigeben_erwartet(db_admin, db_app, welt):
    app = db_app()
    nid = nachrichten.mail_entwerfen(app, welt["leitung"], welt["fest"], "Info", "Hallo",
                                     ["zugesagt"])["id"]
    _teilnehmer(db_admin, welt["a"], welt["fest"], "Neu", "neu@example.org", "zugesagt")
    for erwartet in (2, -1, True, "3"):
        with pytest.raises(ValueError):
            nachrichten.freigeben(app, welt["leitung"], nid, erwartet)  # type: ignore[arg-type]
    with pytest.raises(ValueError) as e:
        nachrichten.freigeben(app, welt["leitung"], nid, 2)
    assert str(e.value) == "Empfängerzahl hat sich geändert – bitte neu prüfen"
    assert _status(db_admin, nid) == "entwurf" and _schnappschuss(db_admin, nid) == {}
    assert nachrichten.freigeben(app, welt["leitung"], nid, 3)["empfaenger"] == 3


def test_freigeben_nie_aus_dem_bot(db_admin, db_app, welt):
    """Auch Leitungs-/Betreiber-Schluessel (quelle bot) geben nie frei."""
    app = db_app()
    nid = nachrichten.mail_entwerfen(app, welt["leitung"], welt["fest"], "Info", "Hallo",
                                     ["zugesagt"])["id"]
    for art, person in (("leitung", welt["lea"]), ("betreiber", welt["bea"]),
                        ("betreiber_lesen", welt["bea"])):
        k = schluessel.kontext_fuer(app, _info(db_admin, art, person_id=person))
        assert k.quelle == "bot"
        with pytest.raises(KeinRecht):
            nachrichten.freigeben(app, k, nid)
    assert _status(db_admin, nid) == "entwurf"


def test_versenden_abbruch_je_empfaenger_sichtbar(db_admin, db_app, welt):
    """T6-c: at-most-once; nach einem Abbruch sieht man je Empfaenger, was passiert ist."""
    app = db_app()
    nid = _freigegeben(app, welt, "fest", "Programm", ["zugesagt", "vielleicht"])

    class Absturz(BaseException):
        pass

    class Mailer(SpeicherMailer):
        def senden(self, an: str, betreff: str, text: str) -> None:
            if an.startswith("ben"):
                raise Absturz()
            super().senden(an, betreff, text)

    mailer = Mailer()
    with pytest.raises(Absturz):
        nachrichten.versenden(db_admin, mailer)
    assert [m.an for m in mailer.gesendet] == ["anna@example.org"]
    assert _schnappschuss(db_admin, nid) == {"anna@example.org": "versendet",
                                             "ben@example.org": "unterwegs",
                                             "cara@example.org": "offen"}
    # Kein Nachsenden: weder ben (unklar) noch cara (nicht gesendet).
    assert nachrichten.versenden(db_admin, SpeicherMailer()) == 0
    assert _status(db_admin, nid) == "versendet"


def test_versenden_abgesagt_oder_archiviert_geht_nicht_raus(db_admin, db_app, welt):
    app = db_app()
    abgesagt = _freigegeben(app, welt, "fest", "Programm", ["zugesagt"])
    archiviert = _freigegeben(app, welt, "anderes", "Anders", ["zugesagt"])
    db_admin.execute("update event.event set status = 'abgesagt' where id = %s",
                     (welt["fest"],))
    db_admin.execute("update event.event set status = 'archiviert' where id = %s",
                     (welt["anderes"],))
    mailer = SpeicherMailer()
    assert nachrichten.versenden(db_admin, mailer) == 2
    assert mailer.gesendet == []
    for nid in (abgesagt, archiviert):
        zeile = db_admin.execute("select status, fehler from event.nachricht where id = %s",
                                 (nid,)).fetchone()
        assert zeile == {"status": "fehler", "fehler": "Event abgesagt/archiviert"}
        assert set(_schnappschuss(db_admin, nid).values()) == {"offen"}
    assert nachrichten.versenden(db_admin, mailer) == 0


def test_rls_schnappschuss_mandantenmatrix(db_admin, db_app, welt):
    app = db_app()
    nid = _freigegeben(app, welt, "fest", "Programm", ["zugesagt"])
    alle = "select count(*) as n from event.nachricht_empfaenger"
    assert db_app(firmen=[welt["b"]]).execute(alle).fetchone()["n"] == 0
    assert db_app(firmen=[welt["a"]], event=welt["anderes"]).execute(alle).fetchone()["n"] == 0
    assert db_app().execute(alle).fetchone()["n"] == 0
    assert db_app(firmen=[welt["a"]], event=welt["fest"]).execute(alle).fetchone()["n"] == 2
    assert db_app(betreiber=True).execute(alle).fetchone()["n"] == 2
    rechte = db_admin.execute(
        "select has_table_privilege('event_app', 'event.nachricht_empfaenger', 'select') as s,"
        " has_table_privilege('event_app', 'event.nachricht_empfaenger', 'insert') as i,"
        " has_table_privilege('event_app', 'event.nachricht_empfaenger', 'update') as u,"
        " has_table_privilege('event_app', 'event.nachricht_empfaenger', 'delete') as d,"
        " has_table_privilege('public', 'event.nachricht_empfaenger', 'select') as p"
    ).fetchone()
    assert rechte == {"s": True, "i": True, "u": False, "d": False, "p": False}
    # Fremde Firma kann keinen Schnappschuss an eine Nachricht von A haengen.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db_app(firmen=[welt["b"]], person=welt["lb"]).execute(
            "insert into event.nachricht_empfaenger (nachricht_id, mail)"
            " values (%s, 'x@example.org')", (nid,))
