"""Telegram-Gruppe und Direktchat per Einmal-Code verbinden (system/verbinden.py,
db/006_verbinden.sql), dazu die Werkzeuge (`gruppe_melden`, `chat_melden`,
`gruppe_verbinden`) und die API-Routen.

Fachschicht ueber `db_app()` (RLS aktiv, Kontext je Transaktion), Nachlesen ueber
`db_admin`. Telegram nur ueber `SpeicherTelegram`; kein Netz. Chat-IDs sind Beispielwerte.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from spaces.event_management.system import (
    api, marke, mcp_server, schluessel, verbinden, werkzeuge, zugang,
)
from spaces.event_management.system.kontext import KeinRecht, Kontext, NichtGefunden
from spaces.event_management.system.mailer import SpeicherMailer
from spaces.event_management.system.telegram import SpeicherTelegram

BEGINN = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
CHAT = -1001234567890
CHAT_NEU = -1001234567891
CHAT_FREMD = -1001234567892
DIREKT = 123456789
DIREKT_ANDERS = 123456780
PW = "ein-langes-passwort-1"
GEHEIM = b"api-testgeheimnis-0123456789abcdef"
BASIS = "https://events.example.org"
CODE_MUSTER = re.compile(r"[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}")
PRUEFEN = "select event.verbindung_pruefcode_pruefen(%s, %s) as e"
PRUEF_MUSTER = re.compile(r"Event-System: ([ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{6}) ")


@pytest.fixture(autouse=True)
def _sperren_leeren() -> Iterator[None]:
    zugang._sperre_zuruecksetzen()
    verbinden._sperre_zuruecksetzen()
    yield
    zugang._sperre_zuruecksetzen()
    verbinden._sperre_zuruecksetzen()


# --- Testdaten --------------------------------------------------------------------


def _id(admin: Any, abfrage: str, werte: tuple[object, ...]) -> Any:
    return admin.execute(abfrage, werte).fetchone()["id"]


def _person(admin: Any, mail: str, *, betreiber: bool = False) -> UUID:
    return _id(admin, "insert into event.person (mail, name, passwort_hash, betreiber)"
                      " values (%s, %s, %s, %s) returning id",
               (mail, "Person " + mail.split("@")[0], zugang.passwort_hash(PW), betreiber))


def _event(admin: Any, firma: UUID, name: str, event_id: UUID | None = None) -> UUID:
    return _id(admin, "insert into event.event (id, firma_id, kategorie, name, beginn, ende)"
                      " values (coalesce(%s, gen_random_uuid()), %s, 'fest', %s, %s, %s)"
                      " returning id",
               (event_id, firma, name, BEGINN, BEGINN + timedelta(hours=3)))


@pytest.fixture
def telegram() -> SpeicherTelegram:
    t = SpeicherTelegram()
    t.chats[CHAT] = {"titel": "Beispielgruppe", "art": "supergroup", "bot_ist_mitglied": True}
    t.chats[CHAT_NEU] = {"titel": "Neue Gruppe", "art": "group", "bot_ist_mitglied": True}
    t.chats[CHAT_FREMD] = {"titel": "Ohne Bot", "art": "supergroup", "bot_ist_mitglied": False}
    t.chats[DIREKT] = {"titel": "Erika (@erika_beispiel)", "art": "private",
                       "bot_ist_mitglied": True}
    t.chats[DIREKT_ANDERS] = {"titel": None, "art": "private", "bot_ist_mitglied": True}
    return t


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
    return {
        "a": a, "b": b, "lea": lea, "mia": mia, "lb": lb, "bea": bea,
        "fest": fest, "anderes": anderes, "fremd": fremd,
        "leitung": Kontext(person_id=lea, betreiber=False, rollen={a: "leitung"}),
        "mitglied": Kontext(person_id=mia, betreiber=False, rollen={a: "mitglied"}),
        "fremd_leitung": Kontext(person_id=lb, betreiber=False, rollen={b: "leitung"}),
        "betreiber": Kontext(person_id=bea, betreiber=True),
    }


def _verbindungen(admin: Any) -> list[dict[str, Any]]:
    return admin.execute("select * from event.verbindung order by ablauf, id").fetchall()


def _auftraege(admin: Any) -> list[dict[str, Any]]:
    return admin.execute("select art, ziel, event_id, person_id, chat_id, agent_id, status"
                         " from event.bereitstellung order by angelegt, id").fetchall()


def _hex8(wert: object) -> str:
    return str(wert).replace("-", "")[:8]


def _pruefcode(t: SpeicherTelegram, chat: int) -> str:
    """Pruefcode aus der juengsten Nachricht des Dienstes an diesen Chat."""
    texte = [text for ziel, text in t.gesendet if ziel == chat]
    assert texte, "kein Pruefcode an diesen Chat gesendet"
    treffer = PRUEF_MUSTER.search(texte[-1])
    assert treffer is not None
    return treffer.group(1)


def _pc(admin: Any, t: SpeicherTelegram, verbindung_id: UUID) -> str:
    """Pruefcode der Verbindung (ueber ihren gemeldeten Chat)."""
    chat = admin.execute("select chat_id from event.verbindung where id = %s",
                         (verbindung_id,)).fetchone()["chat_id"]
    return _pruefcode(t, chat)


def _gruppe_gemeldet(app: Any, welt: dict[str, Any], t: SpeicherTelegram, *,
                     event: str = "fest", chat: int = CHAT, wer: str = "leitung") -> UUID:
    """Leitung erzeugt einen Code, der Empfang meldet ihn; Rueckgabe: Verbindungs-ID."""
    code = verbinden.code_erzeugen(app, welt[wer], art="gruppe", event_id=welt[event])
    assert verbinden.melden(app, code=code, chat_id=chat, art="gruppe",
                            telegram=t) == verbinden.DANKE
    gemeldet = [v for v in verbinden.offene(app, welt[wer], event_id=welt[event])
                if v["status"] == "gemeldet"]
    assert len(gemeldet) == 1
    return gemeldet[0]["id"]


# --- Code ---------------------------------------------------------------------------


def test_code_einmalig_und_15_min(db_admin, db_app, welt, telegram):
    app = db_app()
    code = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    assert CODE_MUSTER.fullmatch(code)
    [zeile] = _verbindungen(db_admin)
    # Nur der Hash steht in der DB, kein Klartext in irgendeiner Spalte.
    assert zeile["code_hash"] == hashlib.sha256(code.encode("utf-8")).hexdigest()
    assert code not in str(zeile)
    assert (zeile["art"], zeile["status"], zeile["event_id"], zeile["firma_id"]) == (
        "gruppe", "offen", welt["fest"], welt["a"])
    rest = zeile["ablauf"] - db_admin.execute("select now() as n").fetchone()["n"]
    assert timedelta(minutes=14, seconds=50) < rest <= timedelta(minutes=15)

    # Einmalig: der zweite Gebrauch schlaegt fehl (auch aus derselben Gruppe).
    assert verbinden.melden(app, code=code, chat_id=CHAT, art="gruppe",
                            telegram=telegram) == verbinden.DANKE
    assert verbinden.melden(app, code=code, chat_id=CHAT, art="gruppe",
                            telegram=telegram) == verbinden.UNGUELTIG
    assert verbinden.melden(app, code=code, chat_id=CHAT_NEU, art="gruppe",
                            telegram=telegram) == verbinden.UNGUELTIG

    # Ein neuer Code verwirft alte offene Codes desselben Events (gemeldete bleiben).
    zweiter = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    dritter = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    assert len({code, zweiter, dritter}) == 3
    status = {z["code_hash"]: z["status"] for z in _verbindungen(db_admin)}
    h = {c: hashlib.sha256(c.encode("utf-8")).hexdigest() for c in (code, zweiter, dritter)}
    assert (status[h[code]], status[h[zweiter]], status[h[dritter]]) == (
        "gemeldet", "verworfen", "offen")
    assert verbinden.melden(app, code=zweiter, chat_id=CHAT_NEU, art="gruppe",
                            telegram=telegram) == verbinden.UNGUELTIG

    # Nach 15 Minuten abgelaufen.
    db_admin.execute("update event.verbindung set ablauf = now() - interval '1 second'"
                     " where code_hash = %s", (h[dritter],))
    assert verbinden.melden(app, code=dritter, chat_id=CHAT_NEU, art="gruppe",
                            telegram=telegram) == verbinden.UNGUELTIG
    assert db_admin.execute("select status, chat_id from event.verbindung where code_hash = %s",
                            (h[dritter],)).fetchone() == {"status": "offen", "chat_id": None}


def test_code_ohne_gross_klein_und_leerzeichen(db_admin, db_app, welt, telegram):
    """Codes werden von Hand getippt: strip().upper() vor der Formatpruefung."""
    app = db_app()
    code = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    assert verbinden.melden(app, code=f"  {code.lower()}\n", chat_id=CHAT, art="gruppe",
                            telegram=telegram) == verbinden.DANKE
    for falsch in ("", "ABC", "ABCDEFGH1", "ABCDEFG0", "ÄBCDEFGH", None, 12345678):
        assert verbinden.melden(app, code=falsch, chat_id=CHAT_NEU,  # type: ignore[arg-type]
                                art="gruppe", telegram=telegram) == verbinden.UNGUELTIG


def test_code_nur_leitung(db_admin, db_app, welt):
    app = db_app()
    # Gruppe: nur Leitung der Firma (oder Betreiber); Mitglied KeinRecht, fremde Firma
    # sieht das Event nicht.
    with pytest.raises(KeinRecht):
        verbinden.code_erzeugen(app, welt["mitglied"], art="gruppe", event_id=welt["fest"])
    with pytest.raises(NichtGefunden):
        verbinden.code_erzeugen(app, welt["fremd_leitung"], art="gruppe", event_id=welt["fest"])
    # Gebundener Kontext: nur das eigene Event.
    gebunden = Kontext(person_id=welt["lea"], betreiber=False, rollen={welt["a"]: "leitung"},
                       event_id=welt["anderes"])
    with pytest.raises(NichtGefunden):
        verbinden.code_erzeugen(app, gebunden, art="gruppe", event_id=welt["fest"])
    assert _verbindungen(db_admin) == []
    verbinden.code_erzeugen(app, welt["betreiber"], art="gruppe", event_id=welt["fest"])
    # Archiviert oder Legacy: kein Code.
    db_admin.execute("update event.event set status = 'archiviert' where id = %s",
                     (welt["anderes"],))
    with pytest.raises(ValueError) as e:
        verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["anderes"])
    assert str(e.value) == "Event ist archiviert"

    # Direktchat: nur wer irgendwo Leitung oder Betreiber ist, und nur fuer sich selbst.
    with pytest.raises(KeinRecht):
        verbinden.code_erzeugen(app, welt["mitglied"], art="direkt", event_id=None)
    with pytest.raises(KeinRecht):
        verbinden.code_erzeugen(app, Kontext(person_id=None, betreiber=True), art="direkt",
                                event_id=None)
    verbinden.code_erzeugen(app, welt["leitung"], art="direkt", event_id=None)
    verbinden.code_erzeugen(app, welt["betreiber"], art="direkt", event_id=None)
    direkt = [z for z in _verbindungen(db_admin) if z["art"] == "direkt"]
    assert sorted(z["person_id"] for z in direkt) == sorted([welt["lea"], welt["bea"]])
    for falsch in ({"art": "direkt", "event_id": welt["fest"]},
                   {"art": "gruppe", "event_id": None}, {"art": "kanal", "event_id": None}):
        with pytest.raises(ValueError):
            verbinden.code_erzeugen(app, welt["leitung"], **falsch)


# --- Melden -------------------------------------------------------------------------


def test_melden_falsche_art_oder_falsches_vorzeichen(db_admin, db_app, welt, telegram):
    app = db_app()
    gruppe = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    direkt = verbinden.code_erzeugen(app, welt["leitung"], art="direkt", event_id=None)
    for code, chat_id, art in (
        (gruppe, DIREKT, "gruppe"),     # Gruppe nur mit negativer Chat-ID
        (gruppe, CHAT, "direkt"),       # Gruppen-Code ueber chat_melden
        (direkt, CHAT, "direkt"),       # Direktchat nur positiv
        (direkt, DIREKT, "gruppe"),     # Direkt-Code ueber gruppe_melden
        (direkt, 0, "direkt"),
    ):
        assert verbinden.melden(app, code=code, chat_id=chat_id, art=art,
                                telegram=telegram) == verbinden.UNGUELTIG, (chat_id, art)
    # Chat-Art laut Telegram muss passen (eine Gruppe ist kein Direktchat).
    telegram.chats[DIREKT_ANDERS] = {"titel": "Getarnt", "art": "group", "bot_ist_mitglied": True}
    assert verbinden.melden(app, code=direkt, chat_id=DIREKT_ANDERS, art="direkt",
                            telegram=telegram) == verbinden.UNGUELTIG
    telegram.chats[CHAT_NEU] = {"titel": "Privat", "art": "private", "bot_ist_mitglied": True}
    assert verbinden.melden(app, code=gruppe, chat_id=CHAT_NEU, art="gruppe",
                            telegram=telegram) == verbinden.UNGUELTIG
    assert {z["status"] for z in _verbindungen(db_admin)} == {"offen"}
    # Die Codes sind dadurch nicht verbraucht.
    assert verbinden.melden(app, code=gruppe, chat_id=CHAT, art="gruppe",
                            telegram=telegram) == verbinden.DANKE
    assert verbinden.melden(app, code=direkt, chat_id=DIREKT, art="direkt",
                            telegram=telegram) == verbinden.DANKE
    zeilen = {z["art"]: z for z in _verbindungen(db_admin)}
    assert (zeilen["gruppe"]["status"], zeilen["gruppe"]["chat_id"],
            zeilen["gruppe"]["chat_titel"]) == ("gemeldet", CHAT, "Beispielgruppe")
    assert (zeilen["direkt"]["status"], zeilen["direkt"]["chat_id"]) == ("gemeldet", DIREKT)
    with pytest.raises(ValueError):
        verbinden.melden(app, code=gruppe, chat_id=CHAT, art="kanal", telegram=telegram)


def test_melden_bot_nicht_in_gruppe_abgelehnt(db_admin, db_app, welt, telegram):
    app = db_app()
    code = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    assert verbinden.melden(app, code=code, chat_id=CHAT_FREMD, art="gruppe",
                            telegram=telegram) == verbinden.UNGUELTIG
    # Telegram kennt den Chat nicht (Fehler): ebenso abgelehnt.
    assert verbinden.melden(app, code=code, chat_id=-1001234567899, art="gruppe",
                            telegram=telegram) == verbinden.UNGUELTIG
    assert [z["status"] for z in _verbindungen(db_admin)] == ["offen"]
    # Ohne Telegram geht gar nichts (fester Text ohne Daten, kein Code-Orakel).
    with pytest.raises(ValueError) as e:
        verbinden.melden(app, code=code, chat_id=CHAT, art="gruppe", telegram=None)
    assert str(e.value) == "Telegram ist nicht eingerichtet"
    assert verbinden.melden(app, code=code, chat_id=CHAT, art="gruppe",
                            telegram=telegram) == verbinden.DANKE


def test_melden_titel_bereinigt(db_admin, db_app, welt, telegram):
    app = db_app()
    code = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    telegram.chats[CHAT]["titel"] = "Gruppe\nmit\x00Umbruch " + "x" * 300
    assert verbinden.melden(app, code=code, chat_id=CHAT, art="gruppe",
                            telegram=telegram) == verbinden.DANKE
    titel = _verbindungen(db_admin)[0]["chat_titel"]
    assert titel.startswith("GruppemitUmbruch x") and len(titel) == verbinden.TITEL_MAX


def test_sperre_nach_5_fehlmeldungen(db_admin, db_app, welt, telegram, caplog):
    app = db_app()
    code = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    jetzt = 1_000_000.0
    with caplog.at_level(logging.DEBUG):
        for i in range(5):
            assert verbinden.melden(app, code=f"FALSCH{i}A", chat_id=CHAT, art="gruppe",
                                    telegram=telegram, jetzt=jetzt + i) == verbinden.UNGUELTIG
        # Gesperrt: auch der richtige Code wird jetzt einheitlich abgelehnt.
        assert verbinden.melden(app, code=code, chat_id=CHAT, art="gruppe",
                                telegram=telegram, jetzt=jetzt + 10) == verbinden.UNGUELTIG
        assert verbinden.melden(app, code=code, chat_id=CHAT, art="gruppe", telegram=telegram,
                                jetzt=jetzt + 4 + 15 * 60 - 1) == verbinden.UNGUELTIG
    assert [z["status"] for z in _verbindungen(db_admin)] == ["offen"]
    # Nur dieser Chat ist gesperrt.
    andere = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe",
                                     event_id=welt["anderes"])
    assert verbinden.melden(app, code=andere, chat_id=CHAT_NEU, art="gruppe",
                            telegram=telegram, jetzt=jetzt + 20) == verbinden.DANKE
    # 15 Minuten nach der fuenften Fehlmeldung ist der Chat wieder frei.
    assert verbinden.melden(app, code=code, chat_id=CHAT, art="gruppe", telegram=telegram,
                            jetzt=jetzt + 4 + 15 * 60 + 1) == verbinden.DANKE
    # Logs nennen weder Codes noch Chat-IDs.
    assert code not in caplog.text and "FALSCH" not in caplog.text
    assert str(CHAT) not in caplog.text and str(CHAT)[1:] not in caplog.text


# --- Bestaetigen ----------------------------------------------------------------------


def test_ohne_bestaetigung_kein_auftrag(db_admin, db_app, welt, telegram):
    app = db_app()
    _gruppe_gemeldet(app, welt, telegram)
    code = verbinden.code_erzeugen(app, welt["leitung"], art="direkt", event_id=None)
    assert verbinden.melden(app, code=code, chat_id=DIREKT, art="direkt",
                            telegram=telegram) == verbinden.DANKE
    assert _auftraege(db_admin) == []
    assert {z["status"] for z in _verbindungen(db_admin)} == {"gemeldet"}
    # Gesendet wurden nur die beiden Pruefcodes, in die gemeldeten Chats (F1).
    assert [chat for chat, _ in telegram.gesendet] == [CHAT, DIREKT]


def test_bestaetigen_legt_genau_einen_auftrag_an(db_admin, db_app, welt, telegram):
    app = db_app()
    vid = _gruppe_gemeldet(app, welt, telegram)
    offene = verbinden.offene(app, welt["mitglied"], event_id=welt["fest"])
    assert [(v["status"], v["chat_titel"]) for v in offene] == [("gemeldet", "Beispielgruppe")]
    assert not {"chat_id", "code_hash", "person_id"} & set(offene[0])

    auftrag = verbinden.bestaetigen(app, welt["leitung"], vid, _pc(db_admin, telegram, vid))
    assert _auftraege(db_admin) == [{
        "art": "anlegen", "ziel": "gruppe", "event_id": welt["fest"], "person_id": welt["lea"],
        "chat_id": CHAT, "agent_id": "ev-" + _hex8(welt["fest"]), "status": "offen"}]
    assert db_admin.execute("select id from event.bereitstellung").fetchone()["id"] == auftrag
    zeile = db_admin.execute("select * from event.verbindung where id = %s", (vid,)).fetchone()
    assert zeile["status"] == "bestaetigt" and zeile["bestaetigt"] is not None
    assert zeile["chat_id"] == CHAT
    # Zweimal bestaetigen geht nicht, und es entsteht kein zweiter Auftrag.
    with pytest.raises(ValueError) as e:
        verbinden.bestaetigen(app, welt["leitung"], vid, _pc(db_admin, telegram, vid))
    assert str(e.value) == verbinden.NUR_GEMELDETE
    assert len(_auftraege(db_admin)) == 1
    verlauf = db_admin.execute("select quelle, baustein, aktion, ziel_id from event.verlauf"
                               ).fetchall()
    assert verlauf == [{"quelle": "oberflaeche", "baustein": "nachrichten",
                        "aktion": "gruppe_bestaetigt", "ziel_id": vid}]


def test_direktchat_leitung_und_betreiber(db_admin, db_app, welt, telegram):
    app = db_app()
    for k, chat, praefix, ziel in ((welt["leitung"], DIREKT, "ld", "leitung"),
                                   (welt["betreiber"], DIREKT_ANDERS, "bo", "betreiber")):
        code = verbinden.code_erzeugen(app, k, art="direkt", event_id=None)
        assert verbinden.melden(app, code=code, chat_id=chat, art="direkt",
                                telegram=telegram) == verbinden.DANKE
        [v] = [v for v in verbinden.offene(app, k) if v["status"] == "gemeldet"]
        # Nur die Person selbst sieht und bestaetigt ihren Direktchat.
        andere = welt["betreiber"] if k is welt["leitung"] else welt["leitung"]
        assert v["id"] not in {x["id"] for x in verbinden.offene(app, andere)}
        with pytest.raises(NichtGefunden):
            verbinden.bestaetigen(app, andere, v["id"], _pc(db_admin, telegram, v["id"]))
        verbinden.bestaetigen(app, k, v["id"], _pc(db_admin, telegram, v["id"]))
        assert _auftraege(db_admin)[-1] == {
            "art": "anlegen", "ziel": ziel, "event_id": None, "person_id": k.person_id,
            "chat_id": chat, "agent_id": f"{praefix}-" + _hex8(k.person_id), "status": "offen"}
    assert len(_auftraege(db_admin)) == 2


def test_fremde_firma_kann_nicht_bestaetigen(db_admin, db_app, welt, telegram):
    app = db_app()
    vid = _gruppe_gemeldet(app, welt, telegram)
    with pytest.raises(NichtGefunden):
        verbinden.bestaetigen(app, welt["fremd_leitung"], vid, _pc(db_admin, telegram, vid))
    with pytest.raises(NichtGefunden):
        verbinden.verwerfen(app, welt["fremd_leitung"], vid)
    with pytest.raises(NichtGefunden):
        verbinden.offene(app, welt["fremd_leitung"], event_id=welt["fest"])
    # Mitglied der eigenen Firma: keine Leitung, also KeinRecht.
    with pytest.raises(KeinRecht):
        verbinden.bestaetigen(app, welt["mitglied"], vid, _pc(db_admin, telegram, vid))
    with pytest.raises(NichtGefunden):
        verbinden.bestaetigen(app, welt["leitung"], uuid4(), "ABCDEF")
    assert _auftraege(db_admin) == []
    assert _verbindungen(db_admin)[0]["status"] == "gemeldet"


def test_bestaetigen_nie_aus_dem_bot(db_admin, db_app, welt, telegram):
    """Auch Leitungs-/Betreiber-Schluessel (quelle bot) bestaetigen und verwerfen nie."""
    app = db_app()
    vid = _gruppe_gemeldet(app, welt, telegram)
    for art, person in (("leitung", welt["lea"]), ("betreiber", welt["bea"])):
        _, klartext = schluessel.ausstellen(db_admin, art=art, bezeichnung="Test",
                                            person_id=person)
        k = schluessel.kontext_fuer(app, schluessel.pruefen(app, klartext))
        assert k.quelle == "bot"
        with pytest.raises(KeinRecht):
            verbinden.bestaetigen(app, k, vid, _pc(db_admin, telegram, vid))
        with pytest.raises(KeinRecht):
            verbinden.verwerfen(app, k, vid)
    assert _auftraege(db_admin) == [] and _verbindungen(db_admin)[0]["status"] == "gemeldet"


def test_neue_gruppe_baut_alte_ab(db_admin, db_app, welt, telegram):
    app = db_app()
    alt = _gruppe_gemeldet(app, welt, telegram)
    verbinden.bestaetigen(app, welt["leitung"], alt, _pc(db_admin, telegram, alt))
    neu = _gruppe_gemeldet(app, welt, telegram, chat=CHAT_NEU)
    # Daneben noch ein offener Code: er verfaellt mit der Bestaetigung.
    verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    verbinden.bestaetigen(app, welt["leitung"], neu, _pc(db_admin, telegram, neu))
    agent = "ev-" + _hex8(welt["fest"])
    assert _auftraege(db_admin) == [
        {"art": "anlegen", "ziel": "gruppe", "event_id": welt["fest"], "person_id": welt["lea"],
         "chat_id": CHAT, "agent_id": agent, "status": "offen"},
        {"art": "abbauen", "ziel": "gruppe", "event_id": welt["fest"], "person_id": welt["lea"],
         "chat_id": CHAT, "agent_id": agent, "status": "offen"},
        {"art": "anlegen", "ziel": "gruppe", "event_id": welt["fest"], "person_id": welt["lea"],
         "chat_id": CHAT_NEU, "agent_id": agent, "status": "offen"},
    ]
    status = {z["id"]: z["status"] for z in _verbindungen(db_admin)}
    assert status[alt] == "verworfen" and status[neu] == "bestaetigt"
    assert sorted(status.values()) == ["bestaetigt", "verworfen", "verworfen"]
    # Die alte Gruppe kann danach wieder verbunden werden (jetzt als Ersatz der neuen).
    wieder = _gruppe_gemeldet(app, welt, telegram)
    verbinden.bestaetigen(app, welt["leitung"], wieder, _pc(db_admin, telegram, wieder))
    assert [a["chat_id"] for a in _auftraege(db_admin)][-2:] == [CHAT_NEU, CHAT]


def test_neuer_direktchat_baut_alten_ab(db_admin, db_app, welt, telegram):
    app = db_app()
    for chat in (DIREKT, DIREKT_ANDERS):
        code = verbinden.code_erzeugen(app, welt["leitung"], art="direkt", event_id=None)
        verbinden.melden(app, code=code, chat_id=chat, art="direkt", telegram=telegram)
        [v] = [v for v in verbinden.offene(app, welt["leitung"]) if v["status"] == "gemeldet"]
        verbinden.bestaetigen(app, welt["leitung"], v["id"], _pc(db_admin, telegram, v["id"]))
    agent = "ld-" + _hex8(welt["lea"])
    assert [(a["art"], a["chat_id"], a["agent_id"]) for a in _auftraege(db_admin)] == [
        ("anlegen", DIREKT, agent), ("abbauen", DIREKT, agent),
        ("anlegen", DIREKT_ANDERS, agent)]


def test_gruppe_schon_mit_anderem_event_verbunden(db_admin, db_app, welt, telegram):
    app = db_app()
    erste = _gruppe_gemeldet(app, welt, telegram)
    verbinden.bestaetigen(app, welt["leitung"], erste, _pc(db_admin, telegram, erste))
    zweite = _gruppe_gemeldet(app, welt, telegram, event="anderes")
    with pytest.raises(ValueError) as e:
        verbinden.bestaetigen(app, welt["leitung"], zweite, _pc(db_admin, telegram, zweite))
    assert str(e.value) == verbinden.SCHON_VERBUNDEN
    assert len(_auftraege(db_admin)) == 1
    assert db_admin.execute("select status from event.verbindung where id = %s",
                            (zweite,)).fetchone()["status"] == "gemeldet"


def test_agent_kollision_value_error(db_admin, db_app, welt, telegram):
    """Ruling S5: gleiche 8 Hex bei einem anderen Event -> ValueError, nichts ueberschrieben."""
    app = db_app()
    zwilling = UUID(f"{_hex8(welt['fest'])}-0000-4000-8000-000000000001")
    assert zwilling != welt["fest"]
    welt["zwilling"] = _event(db_admin, welt["a"], "Zwilling", zwilling)
    erste = _gruppe_gemeldet(app, welt, telegram)
    verbinden.bestaetigen(app, welt["leitung"], erste, _pc(db_admin, telegram, erste))
    vid = _gruppe_gemeldet(app, welt, telegram, event="zwilling", chat=CHAT_NEU)
    with pytest.raises(ValueError) as e:
        verbinden.bestaetigen(app, welt["leitung"], vid, _pc(db_admin, telegram, vid))
    assert str(e.value) == verbinden.AGENT_KOLLISION
    assert len(_auftraege(db_admin)) == 1


def test_verwerfen(db_admin, db_app, welt, telegram):
    app = db_app()
    vid = _gruppe_gemeldet(app, welt, telegram)
    with pytest.raises(KeinRecht):
        verbinden.verwerfen(app, welt["mitglied"], vid)
    zeile = verbinden.verwerfen(app, welt["leitung"], vid)
    assert zeile["status"] == "verworfen"
    with pytest.raises(ValueError):
        verbinden.verwerfen(app, welt["leitung"], vid)
    with pytest.raises(ValueError):
        verbinden.bestaetigen(app, welt["leitung"], vid, _pc(db_admin, telegram, vid))
    assert _auftraege(db_admin) == []
    assert verbinden.offene(app, welt["leitung"], event_id=welt["fest"]) == []


def test_offene_liste(db_admin, db_app, welt, telegram):
    app = db_app()
    code = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    [v] = verbinden.offene(app, welt["mitglied"], event_id=welt["fest"])
    assert (v["status"], v["art"], v["chat_titel"]) == ("offen", "gruppe", None)
    assert set(v) == {"id", "art", "status", "chat_titel", "ablauf", "gemeldet", "bestaetigt",
                      "pruefcode_zugestellt"}
    # Abgelaufene offene Codes zaehlen nicht.
    db_admin.execute("update event.verbindung set ablauf = now() - interval '1 second'")
    assert verbinden.offene(app, welt["leitung"], event_id=welt["fest"]) == []
    assert verbinden.offene(app, welt["leitung"]) == []
    assert code


# --- Zweites Tor: RLS und SD-Funktionen (006) -----------------------------------------


def _roh_gruppe(conn: Any, welt: dict[str, Any], person: object, **extra: Any) -> None:
    spalten = {"art": "gruppe", "code_hash": hashlib.sha256(uuid4().bytes).hexdigest(),
               "event_id": welt["fest"], "firma_id": welt["a"], "person_id": person,
               "ablauf": datetime.now(timezone.utc) + timedelta(minutes=10), **extra}
    conn.execute(f"insert into event.verbindung ({', '.join(spalten)})"
                 f" values ({', '.join(['%s'] * len(spalten))})", list(spalten.values()))


def test_rls_kein_direktes_bestaetigen_oder_chat_id(db_admin, db_app, welt, telegram):
    leitung = db_app(firmen=[welt["a"]], person=welt["lea"])
    mitglied = db_app(firmen=[welt["a"]], person=welt["mia"])
    # Anlegen nur offen, ohne Chat, nur durch eine Leitung als sie selbst.
    for extra in ({"status": "bestaetigt"}, {"status": "gemeldet"}, {"chat_id": CHAT},
                  {"chat_titel": "x"}, {"bestaetigt": datetime.now(timezone.utc)},
                  {"gemeldet": datetime.now(timezone.utc)}):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _roh_gruppe(leitung, welt, welt["lea"], **extra)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _roh_gruppe(mitglied, welt, welt["mia"])           # Mitglied ist keine Leitung
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _roh_gruppe(leitung, welt, welt["mia"])            # nur als man selbst
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _roh_gruppe(leitung, welt, welt["lea"],            # hoechstens 15 Minuten gueltig
                    ablauf=datetime.now(timezone.utc) + timedelta(days=1))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # Direktchat ohne Leitung
        db_app(person=welt["mia"]).execute(
            "insert into event.verbindung (art, code_hash, person_id, ablauf)"
            " values ('direkt', %s, %s, now() + interval '5 minutes')",
            ("c" * 64, welt["mia"]))
    assert _verbindungen(db_admin) == []
    _roh_gruppe(leitung, welt, welt["lea"])               # Positivkontrolle
    # offen -> bestaetigt nie (kein gemeldeter Chat), auch nicht durch die Leitung.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        leitung.execute("update event.verbindung set status = 'bestaetigt', bestaetigt = now()"
                        " where status = 'offen'")

    # Aendern: chat_id/chat_titel/code_hash nie.
    app = db_app()
    vid = _gruppe_gemeldet(app, welt, telegram)
    for sql, werte in (
        ("update event.verbindung set chat_id = %s where id = %s", (CHAT_FREMD, vid)),
        ("update event.verbindung set chat_titel = 'x' where id = %s", (vid,)),
        ("update event.verbindung set code_hash = %s where id = %s", ("d" * 64, vid)),
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            leitung.execute(sql, werte)
    # gemeldet -> bestaetigt: Mitglied nicht, Leitung schon.
    bestaetigen = ("update event.verbindung set status = 'bestaetigt', bestaetigt = now()"
                   " where id = %s")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        mitglied.execute(bestaetigen, (vid,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        mitglied.execute("update event.verbindung set status = 'verworfen' where id = %s",
                         (vid,))
    # Auch die Leitung erst nach richtigem Pruefcode (008, F1).
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        leitung.execute(bestaetigen, (vid,))
    assert leitung.execute(PRUEFEN, (vid, _pruefcode(telegram, CHAT))).fetchone()["e"] == "ok"
    assert leitung.execute(bestaetigen, (vid,)).rowcount == 1
    zeile = db_admin.execute("select status, chat_id from event.verbindung where id = %s",
                             (vid,)).fetchone()
    assert zeile == {"status": "bestaetigt", "chat_id": CHAT}
    # Verworfen ist endgueltig.
    assert leitung.execute("update event.verbindung set status = 'verworfen' where id = %s",
                           (vid,)).rowcount == 1
    assert leitung.execute(bestaetigen, (vid,)).rowcount == 0


def test_rls_direktchat_nur_die_person_selbst(db_admin, db_app, welt, telegram):
    app = db_app()
    code = verbinden.code_erzeugen(app, welt["leitung"], art="direkt", event_id=None)
    verbinden.melden(app, code=code, chat_id=DIREKT, art="direkt", telegram=telegram)
    vid = db_admin.execute("select id from event.verbindung").fetchone()["id"]
    bestaetigen = ("update event.verbindung set status = 'bestaetigt', bestaetigt = now()"
                   " where id = %s")
    # Betreiber sieht die Zeile, darf sie aber nicht fuer die Person bestaetigen.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        db_app(betreiber=True, person=welt["bea"]).execute(bestaetigen, (vid,))
    lea = db_app(person=welt["lea"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # erst der Pruefcode (F1)
        lea.execute(bestaetigen, (vid,))
    # Der Betreiber kann den Pruefcode fuer die Person auch nicht pruefen.
    betreiber = db_app(betreiber=True, person=welt["bea"])
    assert betreiber.execute(PRUEFEN, (vid, _pruefcode(telegram, DIREKT))).fetchone()["e"] == (
        "falsch")
    assert lea.execute(PRUEFEN, (vid, _pruefcode(telegram, DIREKT))).fetchone()["e"] == "ok"
    assert lea.execute(bestaetigen, (vid,)).rowcount == 1


def test_rls_auftrag_nur_fuer_bestaetigten_chat(db_admin, db_app, welt, telegram):
    """Die Chat-ID eines Auftrags stammt aus einer bestaetigten Verbindung desselben Ziels."""
    leitung = db_app(firmen=[welt["a"]], person=welt["lea"])
    auftrag = ("insert into event.bereitstellung (art, ziel, event_id, person_id, chat_id,"
               " agent_id) values (%s, 'gruppe', %s, %s, %s, %s)")
    agent = "ev-" + _hex8(welt["fest"])
    for art in ("anlegen", "abbauen"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            leitung.execute(auftrag, (art, welt["fest"], welt["lea"], CHAT_FREMD, agent))
    app = db_app()
    vid = _gruppe_gemeldet(app, welt, telegram)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # nur gemeldet
        leitung.execute(auftrag, ("anlegen", welt["fest"], welt["lea"], CHAT, agent))
    verbinden.bestaetigen(app, welt["leitung"], vid, _pc(db_admin, telegram, vid))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # anderer Chat
        leitung.execute(auftrag, ("anlegen", welt["fest"], welt["lea"], CHAT_FREMD, agent))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # anderes Event
        leitung.execute(auftrag, ("anlegen", welt["anderes"], welt["lea"], CHAT,
                                  "ev-" + _hex8(welt["anderes"])))
    leitung.execute(auftrag, ("abbauen", welt["fest"], welt["lea"], CHAT, agent))


def test_sd_funktionen(db_admin, db_app, welt):
    app = db_app()
    code = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    h = hashlib.sha256(code.encode("utf-8")).hexdigest()
    ohne = db_app()
    # Ruling T7-c: Klartext rein, gehasht wird in der Funktion; Rueckgabe nur die Art.
    assert ohne.execute("select * from event.verbindung_finden(%s)", (code,)).fetchall() == [
        {"art": "gruppe"}]
    assert ohne.execute("select * from event.verbindung_finden(%s)",
                        (f" {code.lower()} ",)).fetchall() == [{"art": "gruppe"}]
    for falsch in ("AAAAAAAA", h, "", "0" * 8):
        assert ohne.execute("select * from event.verbindung_finden(%s)",
                            (falsch,)).fetchall() == [], falsch
    melden = "select event.verbindung_melden(%s, %s, %s, 'ABCDEF') as ok"
    # Vorzeichen in SQL geprueft, Titel ohne Steuerzeichen.
    assert ohne.execute(melden, ("AAAAAAAA", CHAT, "T")).fetchone()["ok"] is False
    assert ohne.execute(melden, (code, DIREKT, "T")).fetchone()["ok"] is False
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        ohne.execute(melden, (code, CHAT, "a\nb"))
    assert ohne.execute(melden, (code, CHAT, "Titel")).fetchone()["ok"] is True
    assert ohne.execute(melden, (code, CHAT, "Titel")).fetchone()["ok"] is False
    assert ohne.execute("select * from event.verbindung_finden(%s)", (code,)).fetchall() == []
    rechte = db_admin.execute(
        "select has_function_privilege('public',"
        " 'event.verbindung_melden(text, bigint, text, text)', 'execute') as p,"
        " has_function_privilege('event_app',"
        " 'event.verbindung_melden(text, bigint, text, text)', 'execute') as a").fetchone()
    assert rechte == {"p": False, "a": True}


def test_rls_hash_allein_reicht_nicht(db_admin, db_app, welt):
    """Ruling T7-c: Der Code-Hash ist fuer die Leitung per SELECT lesbar, reicht aber auf
    SQL-Ebene nicht zum Melden; nur der Klartext-Code."""
    leitung = db_app(firmen=[welt["a"]], person=welt["lea"])
    code = verbinden.code_erzeugen(db_app(), welt["leitung"], art="gruppe",
                                   event_id=welt["fest"])
    [zeile] = leitung.execute("select code_hash from event.verbindung").fetchall()
    h = zeile["code_hash"]
    assert h == hashlib.sha256(code.encode("utf-8")).hexdigest()
    ohne = db_app()
    assert ohne.execute("select * from event.verbindung_finden(%s)", (h,)).fetchall() == []
    assert ohne.execute("select event.verbindung_melden(%s, %s, %s, %s) as ok",
                        (h, CHAT, "T", "ABCDEF")).fetchone()["ok"] is False
    assert [z["status"] for z in _verbindungen(db_admin)] == ["offen"]


def test_meldung_verfaellt_nach_24_stunden(db_admin, db_app, welt, telegram):
    """Ruling T7-d: eine gemeldete Verbindung ist 24 h bestaetigbar, danach nicht mehr."""
    app = db_app()
    v = _gruppe_gemeldet(app, welt, telegram)
    db_admin.execute("update event.verbindung set gemeldet = now() - interval '24 hours 1 minute'"
                     " where id = %s", (v,))
    assert verbinden.offene(app, welt["leitung"], event_id=welt["fest"]) == []
    with pytest.raises(ValueError) as e:
        verbinden.bestaetigen(app, welt["leitung"], v, _pc(db_admin, telegram, v))
    assert str(e.value) == verbinden.MELDUNG_ABGELAUFEN
    assert _auftraege(db_admin) == []
    # Knapp unter 24 h geht es noch.
    db_admin.execute("update event.verbindung set gemeldet = now() - interval '23 hours'"
                     " where id = %s", (v,))
    verbinden.bestaetigen(app, welt["leitung"], v, _pc(db_admin, telegram, v))
    assert len(_auftraege(db_admin)) == 1


def test_direktchat_zeigt_anzeigenamen(db_admin, db_app, welt, telegram):
    """Ruling T7-b: der gemeldete Direktchat traegt den Namen aus Telegram, damit die
    Person in der Oberflaeche sieht, wessen Chat sie bestaetigt."""
    app = db_app()
    code = verbinden.code_erzeugen(app, welt["leitung"], art="direkt", event_id=None)
    assert verbinden.melden(app, code=code, chat_id=DIREKT, art="direkt",
                            telegram=telegram) == verbinden.DANKE
    [v] = verbinden.offene(app, welt["leitung"])
    assert (v["status"], v["chat_titel"]) == ("gemeldet", "Erika (@erika_beispiel)")


def test_betreiber_wird_leitung_alter_agent_bleibt_fuer_den_arbeiter(
        db_admin, db_app, welt, telegram, caplog):
    """Wer als Betreiber einen Direktchat (bo-) verbunden hat und jetzt nur noch Leitung ist,
    darf den bo-Abbau per RLS nicht anlegen. Der Abbau wird uebersprungen (der Arbeiter aus
    Task 8 raeumt Agenten verlorener Rollen mit der Admin-DSN ab), kein interner Fehler."""
    app = db_app()
    p = _person(db_admin, "wechsel@example.org", betreiber=True)
    db_admin.execute("insert into event.mitgliedschaft values (%s, %s, 'leitung')",
                     (p, welt["a"]))
    vorher = Kontext(person_id=p, betreiber=True, rollen={welt["a"]: "leitung"})
    code = verbinden.code_erzeugen(app, vorher, art="direkt", event_id=None)
    verbinden.melden(app, code=code, chat_id=DIREKT, art="direkt", telegram=telegram)
    [v] = [v for v in verbinden.offene(app, vorher) if v["status"] == "gemeldet"]
    verbinden.bestaetigen(app, vorher, v["id"], _pc(db_admin, telegram, v["id"]))

    db_admin.execute("update event.person set betreiber = false where id = %s", (p,))
    nachher = Kontext(person_id=p, betreiber=False, rollen={welt["a"]: "leitung"})
    code = verbinden.code_erzeugen(app, nachher, art="direkt", event_id=None)
    verbinden.melden(app, code=code, chat_id=DIREKT_ANDERS, art="direkt", telegram=telegram)
    [v] = [v for v in verbinden.offene(app, nachher) if v["status"] == "gemeldet"]
    with caplog.at_level(logging.INFO):
        verbinden.bestaetigen(app, nachher, v["id"], _pc(db_admin, telegram, v["id"]))
    assert [(a["art"], a["ziel"], a["chat_id"], a["agent_id"]) for a in _auftraege(db_admin)] == [
        ("anlegen", "betreiber", DIREKT, "bo-" + _hex8(p)),
        ("anlegen", "leitung", DIREKT_ANDERS, "ld-" + _hex8(p))]
    assert [(z["chat_id"], z["status"]) for z in _verbindungen(db_admin)
            if z["chat_id"] is not None] == [(DIREKT, "verworfen"), (DIREKT_ANDERS, "bestaetigt")]
    assert "Abbau uebersprungen" in caplog.text
    assert str(DIREKT) not in caplog.text


def test_gleichzeitige_gruppenbestaetigung_passende_meldung(db_admin, db_app, welt, telegram):
    """Zwei Bestaetigungen derselben Event-Gruppe im Wettlauf: die zweite scheitert am
    eindeutigen Index je Event und meldet das passend (nicht "Chat anderweitig verbunden")."""
    app, beobachter = db_app(), db_app()
    v = _gruppe_gemeldet(app, welt, telegram)
    pruef = _pc(db_admin, telegram, v)
    pid = app.execute("select pg_backend_pid() as pid").fetchone()["pid"]
    ergebnis: dict[str, object] = {}

    def bestaetigen() -> None:
        try:
            verbinden.bestaetigen(app, welt["leitung"], v, pruef)
        except Exception as e:  # noqa: BLE001 - der Test prueft die Art
            ergebnis["fehler"] = e

    faden = threading.Thread(target=bestaetigen)
    with db_admin.transaction():
        # Die "andere" Bestaetigung: noch nicht festgeschrieben, haelt den Index-Eintrag.
        _roh_gruppe(db_admin, welt, welt["lea"], status="bestaetigt", chat_id=CHAT_NEU,
                    chat_titel="Neue Gruppe", gemeldet=datetime.now(timezone.utc),
                    bestaetigt=datetime.now(timezone.utc))
        faden.start()
        for _ in range(400):
            warten = beobachter.execute(
                "select wait_event_type from pg_stat_activity where pid = %s",
                (pid,)).fetchone()
            if warten and warten["wait_event_type"] == "Lock":
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Bestaetigung wartet nicht auf die andere")
    faden.join(timeout=20)
    assert not faden.is_alive()
    fehler = ergebnis.get("fehler")
    assert isinstance(fehler, ValueError), fehler
    assert str(fehler) == verbinden.GRUPPE_SCHON_BESTAETIGT
    assert len(_auftraege(db_admin)) == 0


# --- Rueckkanal-Pruefcode (Abschluss-Review F1, Ruling F-a) -----------------------------


def test_pruefcode_geht_in_genau_den_gemeldeten_chat(db_admin, db_app, welt, telegram, caplog):
    app = db_app()
    code = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    with caplog.at_level(logging.DEBUG):
        antwort = verbinden.melden(app, code=code, chat_id=CHAT, art="gruppe", telegram=telegram)
    assert antwort == verbinden.DANKE          # die Antwort an den Bot bleibt einheitlich
    [(chat, text)] = telegram.gesendet
    assert chat == CHAT
    pruef = _pruefcode(telegram, CHAT)
    assert text == verbinden.PRUEFCODE_TEXT.format(pruefcode=pruef)
    # Der Text nennt weder Event noch Firma (Fremde im Chat erfahren nichts).
    assert "Sommerfest" not in text and "Beispiel GmbH" not in text
    assert pruef not in antwort and pruef not in caplog.text
    # Oberflaeche: zugestellt, aber kein Code und kein Hash in der Liste.
    [v] = verbinden.offene(app, welt["leitung"], event_id=welt["fest"])
    assert v["pruefcode_zugestellt"] is True
    assert pruef not in str(v)
    # Gespeichert nur als Hash, in einer Tabelle, die event_app nicht lesen darf.
    [zeile] = db_admin.execute("select * from event.verbindung_pruefung").fetchall()
    assert zeile["pruef_hash"] == hashlib.sha256(pruef.encode("utf-8")).hexdigest()
    assert zeile["fehlversuche"] == 0 and zeile["geprueft"] is None
    for conn in (db_app(firmen=[welt["a"]], person=welt["lea"]),
                 db_app(betreiber=True, person=welt["bea"]), db_app()):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select pruef_hash from event.verbindung_pruefung")
    rechte = db_admin.execute(
        "select has_table_privilege('event_app', 'event.verbindung_pruefung', 'select') as s,"
        " has_table_privilege('event_app', 'event.verbindung_pruefung', 'update') as u,"
        " has_column_privilege('event_app', 'event.verbindung', 'pruefcode_gesendet',"
        " 'update') as g").fetchone()
    assert rechte == {"s": False, "u": False, "g": False}


def test_fremder_chat_ohne_pruefcode_nicht_bestaetigbar(db_admin, db_app, welt, telegram):
    """Angriff aus F1: Leitung B meldet ihren Code mit der Chat-ID der Gruppe A (der Bot ist
    dort Mitglied, getChat passt). Der Pruefcode landet in Gruppe A; ohne ihn geht nichts."""
    app = db_app()
    vid = _gruppe_gemeldet(app, welt, telegram, event="fremd", chat=CHAT, wer="fremd_leitung")
    assert [c for c, _ in telegram.gesendet] == [CHAT]
    # Vier Versuche (der fuenfte wuerde die Verbindung verwerfen, siehe Sperre-Test).
    for falsch in ("ZZZZZZ", "  ", "ABCDEFGH", None):
        with pytest.raises(ValueError) as e:
            verbinden.bestaetigen(app, welt["fremd_leitung"], vid, falsch)  # type: ignore[arg-type]
        assert str(e.value) == verbinden.PRUEFCODE_FALSCH
    assert _auftraege(db_admin) == []
    zeile = db_admin.execute("select status from event.verbindung where id = %s",
                             (vid,)).fetchone()
    assert zeile["status"] == "gemeldet"
    # Die Gruppe A bleibt frei: A kann sie fuer das eigene Event verbinden.
    eigen = _gruppe_gemeldet(app, welt, telegram)
    verbinden.bestaetigen(app, welt["leitung"], eigen, _pc(db_admin, telegram, eigen))
    assert [a["chat_id"] for a in _auftraege(db_admin)] == [CHAT]


def test_mit_pruefcode_bestaetigbar(db_admin, db_app, welt, telegram):
    app = db_app()
    vid = _gruppe_gemeldet(app, welt, telegram)
    pruef = _pruefcode(telegram, CHAT)
    # Von Hand getippt: Kleinbuchstaben und Rand sind egal.
    verbinden.bestaetigen(app, welt["leitung"], vid, f" {pruef.lower()}\n")
    assert [a["art"] for a in _auftraege(db_admin)] == ["anlegen"]
    zeile = db_admin.execute("select geprueft from event.verbindung_pruefung"
                             " where verbindung_id = %s", (vid,)).fetchone()
    assert zeile["geprueft"] is not None


def test_sperre_nach_5_falschen_pruefcodes(db_admin, db_app, welt, telegram):
    app = db_app()
    vid = _gruppe_gemeldet(app, welt, telegram)
    pruef = _pruefcode(telegram, CHAT)
    falsch = "ZZZZZZ" if pruef != "ZZZZZZ" else "YYYYYY"
    for _ in range(4):
        with pytest.raises(ValueError) as e:
            verbinden.bestaetigen(app, welt["leitung"], vid, falsch)
        assert str(e.value) == verbinden.PRUEFCODE_FALSCH
    assert db_admin.execute("select fehlversuche from event.verbindung_pruefung").fetchone() == {
        "fehlversuche": 4}
    # Der fuenfte Fehlversuch verwirft die Verbindung (dieselbe Meldung, keine Zusatzinfo).
    with pytest.raises(ValueError) as e:
        verbinden.bestaetigen(app, welt["leitung"], vid, falsch)
    assert str(e.value) == verbinden.PRUEFCODE_FALSCH
    assert db_admin.execute("select status from event.verbindung where id = %s",
                            (vid,)).fetchone()["status"] == "verworfen"
    # Danach hilft auch der richtige Code nicht mehr.
    with pytest.raises(ValueError) as e:
        verbinden.bestaetigen(app, welt["leitung"], vid, pruef)
    assert str(e.value) == verbinden.NUR_GEMELDETE
    assert _auftraege(db_admin) == []

    # Vier Fehlversuche, dann der richtige: geht.
    vid = _gruppe_gemeldet(app, welt, telegram, chat=CHAT_NEU)
    for _ in range(4):
        with pytest.raises(ValueError):
            verbinden.bestaetigen(app, welt["leitung"], vid, "ZZZZZZ" if _pc(
                db_admin, telegram, vid) != "ZZZZZZ" else "YYYYYY")
    verbinden.bestaetigen(app, welt["leitung"], vid, _pc(db_admin, telegram, vid))
    assert [a["chat_id"] for a in _auftraege(db_admin)] == [CHAT_NEU]


def test_pruefcode_nicht_zustellbar(db_admin, db_app, welt, telegram, caplog):
    """Senden scheitert: die Meldung bleibt (eigene Transaktion), wird als nicht zustellbar
    angezeigt und laesst sich verwerfen; die Antwort an den Bot bleibt dieselbe."""
    app = db_app()
    code = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    telegram.fehler = "Telegram-Fehler: Forbidden: bot was kicked"
    with caplog.at_level(logging.DEBUG):
        assert verbinden.melden(app, code=code, chat_id=CHAT, art="gruppe",
                                telegram=telegram) == verbinden.DANKE
    assert "nicht zustellbar" in caplog.text and str(CHAT) not in caplog.text
    [v] = verbinden.offene(app, welt["leitung"], event_id=welt["fest"])
    assert (v["status"], v["pruefcode_zugestellt"]) == ("gemeldet", False)
    with pytest.raises(ValueError) as e:
        verbinden.bestaetigen(app, welt["leitung"], v["id"], "ABCDEF")
    assert str(e.value) == verbinden.PRUEFCODE_NICHT_ZUSTELLBAR
    # Kein Fehlversuch gezaehlt; Verwerfen geht.
    assert db_admin.execute("select fehlversuche from event.verbindung_pruefung").fetchone() == {
        "fehlversuche": 0}
    assert verbinden.verwerfen(app, welt["leitung"], v["id"])["status"] == "verworfen"
    assert _auftraege(db_admin) == []


def test_rls_bestaetigt_nur_mit_geprueftem_pruefcode(db_admin, db_app, welt, telegram):
    """Zweites Tor: auch mit App-DSN setzt eine Leitung `bestaetigt` nicht am Pruefcode
    vorbei, und das Vergleichen per SD-Funktion verraet Fremden nichts."""
    app = db_app()
    vid = _gruppe_gemeldet(app, welt, telegram)
    pruef = _pruefcode(telegram, CHAT)
    leitung = db_app(firmen=[welt["a"]], person=welt["lea"])
    bestaetigen = ("update event.verbindung set status = 'bestaetigt', bestaetigt = now()"
                   " where id = %s")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        leitung.execute(bestaetigen, (vid,))
    pruefen = "select event.verbindung_pruefcode_pruefen(%s, %s) as e"
    # Fremde Leitung, ohne Kontext, unbekannte id: 'falsch' -- auch mit dem richtigen Code,
    # und ohne zu zaehlen.
    for conn, wer in ((db_app(firmen=[welt["b"]], person=welt["lb"]), "fremd"),
                      (db_app(), "ohne"), (db_app(firmen=[welt["a"]], person=welt["mia"]),
                                           "mitglied")):
        assert conn.execute(pruefen, (vid, pruef)).fetchone()["e"] == "falsch", wer
    assert leitung.execute(pruefen, (uuid4(), pruef)).fetchone()["e"] == "falsch"
    assert db_admin.execute("select fehlversuche, geprueft from event.verbindung_pruefung"
                            ).fetchone() == {"fehlversuche": 0, "geprueft": None}
    # Die Leitung selbst: richtig -> geprueft, danach darf sie bestaetigen.
    assert leitung.execute(pruefen, (vid, pruef)).fetchone()["e"] == "ok"
    assert leitung.execute(bestaetigen, (vid,)).rowcount == 1
    # verbindung_melden verlangt einen gueltigen Pruefcode.
    code = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["anderes"])
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        db_app().execute("select event.verbindung_melden(%s, %s, %s, %s)",
                         (code, CHAT_NEU, "T", "kurz"))


# --- Trennen (F1) -----------------------------------------------------------------------


def test_trennen_gibt_den_chat_frei_und_baut_ab(db_admin, db_app, welt, telegram):
    app = db_app()
    vid = _gruppe_gemeldet(app, welt, telegram)
    verbinden.bestaetigen(app, welt["leitung"], vid, _pc(db_admin, telegram, vid))
    with pytest.raises(KeinRecht):
        verbinden.trennen(app, welt["mitglied"], vid)
    with pytest.raises(NichtGefunden):
        verbinden.trennen(app, welt["fremd_leitung"], vid)
    ergebnis = verbinden.trennen(app, welt["leitung"], vid)
    assert ergebnis["status"] == "verworfen" and ergebnis["bereitstellung"]
    agent = "ev-" + _hex8(welt["fest"])
    assert [(a["art"], a["chat_id"], a["agent_id"]) for a in _auftraege(db_admin)] == [
        ("anlegen", CHAT, agent), ("abbauen", CHAT, agent)]
    assert db_admin.execute("select id from event.bereitstellung where art = 'abbauen'"
                            ).fetchone()["id"] == ergebnis["bereitstellung"]
    assert db_admin.execute("select status from event.verbindung where id = %s",
                            (vid,)).fetchone()["status"] == "verworfen"
    verlauf = db_admin.execute("select aktion from event.verlauf order by id").fetchall()
    assert [z["aktion"] for z in verlauf] == ["gruppe_bestaetigt", "gruppe_getrennt"]
    # Nur bestaetigte Verbindungen lassen sich trennen.
    with pytest.raises(ValueError) as e:
        verbinden.trennen(app, welt["leitung"], vid)
    assert str(e.value) == verbinden.NUR_BESTAETIGTE_TRENNEN
    # Der Chat ist frei: dieselbe Gruppe fuer ein anderes Event.
    zweite = _gruppe_gemeldet(app, welt, telegram, event="anderes")
    verbinden.bestaetigen(app, welt["leitung"], zweite, _pc(db_admin, telegram, zweite))
    assert _auftraege(db_admin)[-1]["agent_id"] == "ev-" + _hex8(welt["anderes"])


def test_trennen_direktchat_nur_die_person_selbst(db_admin, db_app, welt, telegram):
    app = db_app()
    code = verbinden.code_erzeugen(app, welt["leitung"], art="direkt", event_id=None)
    verbinden.melden(app, code=code, chat_id=DIREKT, art="direkt", telegram=telegram)
    [v] = verbinden.offene(app, welt["leitung"])
    verbinden.bestaetigen(app, welt["leitung"], v["id"], _pruefcode(telegram, DIREKT))
    with pytest.raises(NichtGefunden):
        verbinden.trennen(app, welt["betreiber"], v["id"])
    verbinden.trennen(app, welt["leitung"], v["id"])
    agent = "ld-" + _hex8(welt["lea"])
    assert [(a["art"], a["ziel"], a["chat_id"], a["agent_id"]) for a in _auftraege(db_admin)] == [
        ("anlegen", "leitung", DIREKT, agent), ("abbauen", "leitung", DIREKT, agent)]
    assert verbinden.offene(app, welt["leitung"]) == []


def test_bot_kann_weder_bestaetigen_noch_trennen(db_admin, db_app, welt, telegram):
    app = db_app()
    vid = _gruppe_gemeldet(app, welt, telegram)
    pruef = _pruefcode(telegram, CHAT)
    infos = [schluessel.ausstellen(db_admin, art=art, bezeichnung="Test", **arg)[1]
             for art, arg in (("leitung", {"person_id": welt["lea"]}),
                              ("betreiber", {"person_id": welt["bea"]}),
                              ("event", {"event_id": welt["fest"]}))]
    for klartext in infos:
        k = schluessel.kontext_fuer(app, schluessel.pruefen(app, klartext))
        with pytest.raises(KeinRecht):
            verbinden.bestaetigen(app, k, vid, pruef)
    verbinden.bestaetigen(app, welt["leitung"], vid, pruef)
    for klartext in infos:
        k = schluessel.kontext_fuer(app, schluessel.pruefen(app, klartext))
        with pytest.raises(KeinRecht):
            verbinden.trennen(app, k, vid)
    assert [a["art"] for a in _auftraege(db_admin)] == ["anlegen"]


# --- Archivieren und Loeschen geben die Gruppe frei (F2) ---------------------------------


@pytest.mark.parametrize("weg", ["archivieren", "loeschen"])
def test_archiv_oder_loeschen_gibt_die_gruppe_frei(db_admin, db_app, welt, telegram, weg):
    from spaces.event_management.system import events

    app = db_app()
    vid = _gruppe_gemeldet(app, welt, telegram)
    verbinden.bestaetigen(app, welt["leitung"], vid, _pc(db_admin, telegram, vid))
    if weg == "archivieren":
        events.event_status(app, welt["leitung"], welt["fest"], "archiviert")
        assert db_admin.execute("select status from event.verbindung where id = %s",
                                (vid,)).fetchone()["status"] == "verworfen"
    else:
        events.event_loeschen(app, welt["leitung"], welt["fest"])
    assert [a["art"] for a in _auftraege(db_admin)] == ["anlegen", "abbauen"]
    # Dieselbe Gruppe laesst sich fuer ein neues Event bestaetigen.
    neu = _gruppe_gemeldet(app, welt, telegram, event="anderes")
    verbinden.bestaetigen(app, welt["leitung"], neu, _pc(db_admin, telegram, neu))
    assert _auftraege(db_admin)[-1]["agent_id"] == "ev-" + _hex8(welt["anderes"])


# --- Werkzeuge ------------------------------------------------------------------------

MELDEN = {"gruppe_melden", "chat_melden"}


def _info(admin: Any, art: str, **argumente: Any) -> schluessel.SchluesselInfo:
    _, klartext = schluessel.ausstellen(admin, art=art, bezeichnung="Test", **argumente)
    return schluessel.pruefen(admin, klartext)


def test_melden_werkzeug_nur_mit_melden_schluessel(db_admin, db_app, welt, telegram):
    app = db_app()
    infos = {
        "event": _info(db_admin, "event", event_id=welt["fest"]),
        "leitung": _info(db_admin, "leitung", person_id=welt["lea"]),
        "betreiber": _info(db_admin, "betreiber", person_id=welt["bea"]),
        "betreiber_lesen": _info(db_admin, "betreiber_lesen", person_id=welt["bea"]),
        "melden": _info(db_admin, "melden"),
    }
    for art, info in infos.items():
        namen = {w.name for w in werkzeuge.katalog(app, info)}
        assert (namen & MELDEN) == (MELDEN if art == "melden" else set()), art
        assert ("gruppe_verbinden" in namen) is (art in ("leitung", "betreiber")), art
    katalog = {w.name: w for w in werkzeuge.katalog(app, infos["melden"])}
    assert set(katalog) == MELDEN
    for w in katalog.values():
        assert w.eingabe["required"] == ["code", "chat_id"] and w.schreibend
        assert w.eingabe["properties"]["chat_id"]["type"] == "integer"

    code = verbinden.code_erzeugen(app, welt["leitung"], art="gruppe", event_id=welt["fest"])
    for art in ("event", "leitung", "betreiber", "betreiber_lesen"):
        with pytest.raises(NichtGefunden) as e:
            werkzeuge.ausfuehren(app, infos[art], "gruppe_melden",
                                 {"code": code, "chat_id": CHAT}, telegram=telegram)
        assert str(e.value) == "unbekanntes Werkzeug"
    assert [z["status"] for z in _verbindungen(db_admin)] == ["offen"]
    falsch = werkzeuge.ausfuehren(app, infos["melden"], "chat_melden",
                                  {"code": code, "chat_id": DIREKT}, telegram=telegram)
    assert falsch == {"antwort": "Code ungültig oder abgelaufen"}
    ok = werkzeuge.ausfuehren(app, infos["melden"], "gruppe_melden",
                              {"code": code, "chat_id": CHAT}, telegram=telegram)
    assert ok == {"antwort": "Danke, bitte in der Oberfläche bestätigen"}
    for argumente in ({"code": code}, {"code": code, "chat_id": "1"},
                      {"code": code, "chat_id": True}, {"code": code, "chat_id": 2**63},
                      {"code": code, "chat_id": CHAT, "event_id": str(welt["fest"])}):
        with pytest.raises(ValueError):
            werkzeuge.ausfuehren(app, infos["melden"], "gruppe_melden", argumente,
                                 telegram=telegram)


def test_gruppe_verbinden_werkzeug(db_admin, db_app, welt):
    app = db_app()
    lea = _info(db_admin, "leitung", person_id=welt["lea"])
    m = marke.marke_ausstellen(app, lea, welt["fest"])
    erg = werkzeuge.ausfuehren(app, lea, "gruppe_verbinden", {"marke": m})
    assert erg["event"] == {"name": "Sommerfest", "firma": "Beispiel GmbH"}
    assert CODE_MUSTER.fullmatch(erg["code"]) and erg["gueltig_minuten"] == 15
    assert erg["code"] in erg["anleitung"]
    assert "Prüfcode" in erg["anleitung"]    # F1: Bestaetigen nur mit dem Code aus dem Chat
    [zeile] = _verbindungen(db_admin)
    assert (zeile["art"], zeile["event_id"], zeile["person_id"]) == (
        "gruppe", welt["fest"], welt["lea"])
    # Leitungs-Schluessel ohne Leitung der Firma: Event gar nicht waehlbar.
    lb = _info(db_admin, "leitung", person_id=welt["lb"])
    with pytest.raises(NichtGefunden):
        werkzeuge.ausfuehren(app, lb, "gruppe_verbinden", {"marke": m})
    with pytest.raises(ValueError):
        werkzeuge.ausfuehren(app, lea, "gruppe_verbinden", {})
    # Betreiber-Schluessel ohne Person: kann keinen Code anlegen, sieht das Werkzeug nicht.
    ohne_person = _info(db_admin, "betreiber")
    assert ohne_person.person_id is None
    assert "gruppe_verbinden" not in {w.name for w in werkzeuge.katalog(app, ohne_person)}
    with pytest.raises(NichtGefunden):
        werkzeuge.ausfuehren(app, ohne_person, "gruppe_verbinden", {"marke": m})


def test_mcp_melden_ueber_http(db_admin, app_dsn, db_app, welt, telegram):
    code = verbinden.code_erzeugen(db_app(), welt["leitung"], art="gruppe",
                                   event_id=welt["fest"])
    _, key = schluessel.ausstellen(db_admin, art="melden", bezeichnung="Empfang")
    app = mcp_server.erstelle_mcp_app(app_dsn, telegram=telegram)

    def aufruf(c: TestClient, name: str, argumente: dict[str, Any], nr: int) -> dict[str, Any]:
        r = c.post("/mcp", headers={
            "Accept": "application/json, text/event-stream", "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"}, json={
            "jsonrpc": "2.0", "id": nr, "method": "tools/call",
            "params": {"name": name, "arguments": argumente}})
        assert r.status_code == 200, r.text
        return r.json()["result"]

    with TestClient(app, base_url="http://127.0.0.1:8171") as c:
        r = c.post("/mcp", headers={
            "Accept": "application/json, text/event-stream", "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert {t["name"] for t in r.json()["result"]["tools"]} == MELDEN
        falsch = aufruf(c, "gruppe_melden", {"code": "ZZZZZZZZ", "chat_id": CHAT}, 2)
        assert falsch["isError"] is False
        assert "Code ungültig oder abgelaufen" in falsch["content"][0]["text"]
        ok = aufruf(c, "gruppe_melden", {"code": code.lower(), "chat_id": CHAT}, 3)
        assert "Danke, bitte in der Oberfläche bestätigen" in ok["content"][0]["text"]
        assert "Sommerfest" not in ok["content"][0]["text"]
    assert _verbindungen(db_admin)[0]["status"] == "gemeldet"
    ohne = mcp_server.erstelle_mcp_app(app_dsn)
    with TestClient(ohne, base_url="http://127.0.0.1:8171") as c:
        erg = aufruf(c, "gruppe_melden", {"code": code, "chat_id": CHAT}, 4)
    assert erg["isError"] is True
    assert erg["content"][0]["text"] == "Telegram ist nicht eingerichtet"


# --- API --------------------------------------------------------------------------------

Fabrik = Callable[..., TestClient]


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


def test_api_gruppe_ablauf(db_admin, db_app, welt, fabrik, telegram):
    lea, mia, lb = (fabrik("lea@example.org"), fabrik("mia@example.org"),
                    fabrik("leitung-b@example.org"))
    basis = f"/api/events/{welt['fest']}/verbinden"
    assert mia.post(basis).status_code == 403
    assert lb.post(basis).status_code == 404
    ohne_csrf = fabrik("lea@example.org")
    del ohne_csrf.headers["X-CSRF"]
    assert ohne_csrf.post(basis).status_code == 403
    assert _verbindungen(db_admin) == []

    r = lea.post(basis)
    assert r.status_code == 201, r.text
    assert set(r.json()) == {"code", "gueltig_bis"}
    code = r.json()["code"]
    [offen] = mia.get(basis).json()
    assert offen["status"] == "offen" and code not in str(offen)
    assert lb.get(basis).status_code == 404

    assert verbinden.melden(db_app(), code=code, chat_id=CHAT, art="gruppe",
                            telegram=telegram) == verbinden.DANKE
    [gemeldet] = lea.get(basis).json()
    assert (gemeldet["status"], gemeldet["chat_titel"]) == ("gemeldet", "Beispielgruppe")
    url = f"/api/verbindungen/{gemeldet['id']}"
    pruef = {"pruefcode": _pruefcode(telegram, CHAT)}
    assert mia.post(f"{url}/bestaetigen", json=pruef).status_code == 403
    assert lb.post(f"{url}/bestaetigen", json=pruef).status_code == 404
    assert ohne_csrf.post(f"{url}/bestaetigen", json=pruef).status_code == 403
    # Ohne Pruefcode: Validierung; falsch: 422 mit fester Meldung, ohne den Code zu nennen.
    ohne = lea.post(f"{url}/bestaetigen")
    assert ohne.status_code == 422
    r = lea.post(f"{url}/bestaetigen", json={"pruefcode": "ZZZZZZ"})
    if pruef["pruefcode"] != "ZZZZZZ":
        assert (r.status_code, r.json()) == (422, {"detail": verbinden.PRUEFCODE_FALSCH})
    assert pruef["pruefcode"] not in r.text + ohne.text
    assert _auftraege(db_admin) == []
    r = lea.post(f"{url}/bestaetigen", json=pruef)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "bestaetigt" and r.json()["bereitstellung"]
    assert pruef["pruefcode"] not in r.text
    assert pruef["pruefcode"] not in lea.get(basis).text
    assert len(_auftraege(db_admin)) == 1
    assert lea.post(f"{url}/bestaetigen", json=pruef).status_code == 422
    assert lea.post(f"{url}/verwerfen").status_code == 422
    assert lea.post(f"/api/verbindungen/{uuid4()}/bestaetigen", json=pruef).status_code == 404
    # Trennen: nur Leitung der Firma, gibt die Gruppe frei und legt den Abbau an.
    assert mia.post(f"{url}/trennen").status_code == 403
    assert lb.post(f"{url}/trennen").status_code == 404
    assert ohne_csrf.post(f"{url}/trennen").status_code == 403
    r = lea.post(f"{url}/trennen")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "verworfen" and r.json()["bereitstellung"]
    assert [a["art"] for a in _auftraege(db_admin)] == ["anlegen", "abbauen"]
    assert lea.post(f"{url}/trennen").status_code == 422
    assert lea.get(basis).json() == []


def test_api_direktchat(db_admin, db_app, welt, fabrik, telegram):
    lea, mia = fabrik("lea@example.org"), fabrik("mia@example.org")
    assert mia.post("/api/ich/verbinden").status_code == 403
    assert mia.get("/api/ich/verbinden").json() == []
    r = lea.post("/api/ich/verbinden")
    assert r.status_code == 201, r.text
    verbinden.melden(db_app(), code=r.json()["code"], chat_id=DIREKT, art="direkt",
                     telegram=telegram)
    [v] = lea.get("/api/ich/verbinden").json()
    assert v["status"] == "gemeldet" and v["art"] == "direkt"
    assert mia.post(f"/api/verbindungen/{v['id']}/bestaetigen",
                    json={"pruefcode": _pruefcode(telegram, DIREKT)}).status_code == 404
    assert mia.post(f"/api/verbindungen/{v['id']}/trennen").status_code == 404
    assert lea.post(f"/api/verbindungen/{v['id']}/verwerfen").json()["status"] == "verworfen"
    assert lea.get("/api/ich/verbinden").json() == []
