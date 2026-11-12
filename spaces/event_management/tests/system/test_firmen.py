"""Tests fuer Kontext, Rechte, Firmen und Mitgliedschaften (echte Postgres-DB)."""
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from spaces.event_management.system import firmen, rechte
from spaces.event_management.system.kontext import (
    Kontext, KeinRecht, NichtGefunden, transaktion,
)


def _firma(admin: Any, kurz: str) -> UUID:
    return admin.execute(
        "insert into event.firma (name, kurz) values (%s, %s) returning id",
        ("Beispiel " + kurz, kurz),
    ).fetchone()["id"]


def _person(admin: Any, mail: str, betreiber: bool = False) -> UUID:
    return admin.execute(
        "insert into event.person (mail, name, betreiber) values (%s, %s, %s) returning id",
        (mail, "Person " + mail, betreiber),
    ).fetchone()["id"]


def _mitglied(admin: Any, p: UUID, f: UUID, rolle: str) -> None:
    admin.execute("insert into event.mitgliedschaft values (%s, %s, %s)", (p, f, rolle))


def _ctx(p: UUID, rollen: dict[UUID, str], betreiber: bool = False) -> Kontext:
    return Kontext(person_id=p, betreiber=betreiber, rollen=rollen)


class _Stolperdraht:
    """Verbindungs-Attrappe: jeder Zugriff laesst den Test scheitern."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"SQL-Zugriff vor der Rechtepruefung: {name}")


def test_kontext_ist_unveraenderlich() -> None:
    f = uuid4()
    k = _ctx(uuid4(), {f: "leitung"})
    assert k.firmen == frozenset({f})
    with pytest.raises(TypeError):
        k.rollen[uuid4()] = "leitung"  # type: ignore[index]
    with pytest.raises(AttributeError):
        k.betreiber = True  # type: ignore[misc]
    assert Kontext.system().firmen == frozenset()


def test_pruefe_tabelle_und_unbekannte_aktion() -> None:
    f, g = uuid4(), uuid4()
    k = _ctx(uuid4(), {f: "mitglied", g: "leitung"})
    rechte.pruefe(k, "lesen", f)
    rechte.pruefe(k, "event_aendern", f)
    with pytest.raises(KeinRecht):
        rechte.pruefe(k, "event_loeschen", f)
    rechte.pruefe(k, "event_loeschen", g)
    with pytest.raises(KeinRecht):
        rechte.pruefe(k, "firma_anlegen", g)
    rechte.pruefe(_ctx(uuid4(), {}, betreiber=True), "firma_anlegen")
    with pytest.raises(ValueError):
        rechte.pruefe(k, "gibt_es_nicht", f)


def test_meldung_nennt_keine_daten() -> None:
    f = uuid4()
    with pytest.raises(KeinRecht) as ei:
        rechte.pruefe(_ctx(uuid4(), {f: "mitglied"}), "einladen", f)
    assert str(f) not in str(ei.value) and "einladen" in str(ei.value)
    with pytest.raises(NichtGefunden) as en:
        rechte.pruefe(_ctx(uuid4(), {}), "lesen", f)
    assert str(f) not in str(en.value)


def test_mitglied_darf_nicht_einladen(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "firma-a")
    p, q = _person(db_admin, "a@example.org"), _person(db_admin, "b@example.org")
    _mitglied(db_admin, p, f, "mitglied")
    with pytest.raises(KeinRecht):
        firmen.mitgliedschaft_setzen(db_app(), _ctx(p, {f: "mitglied"}), q, f, "mitglied")
    assert db_admin.execute("select count(*) n from event.mitgliedschaft").fetchone()["n"] == 1


def test_leitung_darf_keine_firma_anlegen(db_app: Any) -> None:
    f = uuid4()
    with pytest.raises(KeinRecht):
        firmen.firma_anlegen(db_app(), _ctx(uuid4(), {f: "leitung"}), "Beispiel GmbH", "beispiel")


def test_betreiber_legt_firma_an_und_listet(db_admin: Any, db_app: Any) -> None:
    b = _person(db_admin, "op@example.org", betreiber=True)
    k = firmen.kontext_fuer_person(db_app(), b)
    assert k.betreiber
    neu = firmen.firma_anlegen(db_app(), k, "Beispiel GmbH", "beispiel")
    assert neu["kurz"] == "beispiel"
    assert [x["id"] for x in firmen.firmen(db_app(), k)] == [neu["id"]]
    with pytest.raises(ValueError):
        firmen.firma_anlegen(db_app(), k, "X", "UNGUELTIG!")


def test_firma_kuerzel_doppelt_und_zeilenumbruch(db_admin: Any, db_app: Any) -> None:
    k = Kontext(person_id=None, betreiber=True)
    firmen.firma_anlegen(db_app(), k, "Beispiel GmbH", "beispiel")
    with pytest.raises(ValueError) as e:
        firmen.firma_anlegen(db_app(), k, "Andere GmbH", "beispiel")
    assert str(e.value) == "Kürzel bereits vergeben"  # ohne Daten, ohne psycopg-DETAIL
    assert e.value.__cause__ is None and e.value.__suppress_context__
    stolper: Any = _Stolperdraht()
    for kurz in ("neu\n", "neu\r\n", "\nneu", "NEU", "n"):
        with pytest.raises(ValueError, match="Kurzname"):
            firmen.firma_anlegen(stolper, k, "Neu GmbH", kurz)
    assert db_admin.execute("select count(*) n from event.firma").fetchone()["n"] == 1


def test_leitung_fremder_firma_ohne_recht(db_admin: Any, db_app: Any) -> None:
    f1, f2 = _firma(db_admin, "firma-a"), _firma(db_admin, "firma-b")
    p, q = _person(db_admin, "a@example.org"), _person(db_admin, "b@example.org")
    _mitglied(db_admin, p, f1, "leitung")
    k = _ctx(p, {f1: "leitung"})
    with pytest.raises(NichtGefunden):
        firmen.mitgliedschaft_setzen(db_app(), k, q, f2, "mitglied")
    with pytest.raises(NichtGefunden):
        firmen.mitglieder(db_app(), k, f2)
    assert [x["id"] for x in firmen.firmen(db_app(), k)] == [f1]


def test_doppelte_wache_keinrecht_vor_sql() -> None:
    f = uuid4()
    zu_weit = Kontext(person_id=uuid4(), betreiber=False, rollen={f: "mitglied"}, actor="x")
    stolper: Any = _Stolperdraht()
    with pytest.raises(KeinRecht):
        firmen.mitgliedschaft_setzen(stolper, zu_weit, uuid4(), f, "leitung")
    with pytest.raises(KeinRecht):
        firmen.rolle_aendern(stolper, zu_weit, uuid4(), f, "leitung")
    with pytest.raises(NichtGefunden):
        firmen.rolle_aendern(stolper, zu_weit, uuid4(), uuid4(), "leitung")
    with pytest.raises(KeinRecht):
        firmen.mitglied_entfernen(stolper, zu_weit, uuid4(), f)
    with pytest.raises(KeinRecht):
        firmen.firma_anlegen(stolper, zu_weit, "Beispiel GmbH", "beispiel")
    with pytest.raises(NichtGefunden):
        firmen.mitglieder(stolper, zu_weit, uuid4())


def test_doppelte_wache_rls_haelt_bei_zu_weitem_kontext(db_admin: Any, db_app: Any) -> None:
    """Auch ohne Fachschicht sperrt die RLS: Leitung darf keine Firma anlegen."""
    f = _firma(db_admin, "firma-a")
    p = _person(db_admin, "a@example.org")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with transaktion(db_app(), _ctx(p, {f: "leitung"})) as cur:
            cur.execute("insert into event.firma (name, kurz) values ('x', 'xx')")
    assert db_admin.execute("select count(*) n from event.firma").fetchone()["n"] == 1


def test_letzte_leitung_nicht_entfernbar(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "firma-a")
    p, q = _person(db_admin, "a@example.org"), _person(db_admin, "b@example.org")
    _mitglied(db_admin, p, f, "leitung")
    _mitglied(db_admin, q, f, "mitglied")
    k = _ctx(p, {f: "leitung"})
    with pytest.raises(ValueError):
        firmen.mitglied_entfernen(db_app(), k, p, f)
    with pytest.raises(ValueError, match="letzte Leitung"):
        firmen.rolle_aendern(db_app(), k, p, f, "mitglied")
    firmen.mitglied_entfernen(db_app(), k, q, f)
    assert [m["person_id"] for m in firmen.mitglieder(db_app(), k, f)] == [p]
    with pytest.raises(ValueError, match="Rolle"):
        firmen.rolle_aendern(db_app(), k, p, f, "chef")
    with pytest.raises(ValueError, match="Rolle"):
        firmen.mitgliedschaft_setzen(db_app(), k, q, f, "chef")


def test_mitgliedschaft_neu_und_rolle_aendern(db_admin: Any, db_app: Any) -> None:
    f = _firma(db_admin, "firma-a")
    p, q = _person(db_admin, "a@example.org"), _person(db_admin, "b@example.org")
    _mitglied(db_admin, p, f, "leitung")
    k = _ctx(p, {f: "leitung"})
    firmen.mitgliedschaft_setzen(db_app(), k, q, f, "mitglied")
    # Setzen legt nur neu an: eine bestehende Rolle wird nie still ueberschrieben.
    with pytest.raises(ValueError, match="existiert bereits") as e:
        firmen.mitgliedschaft_setzen(db_app(), k, q, f, "leitung")
    assert str(q) not in str(e.value) and str(f) not in str(e.value)
    rollen = {m["mail"]: m["rolle"] for m in firmen.mitglieder(db_app(), k, f)}
    assert rollen == {"a@example.org": "leitung", "b@example.org": "mitglied"}
    firmen.rolle_aendern(db_app(), k, q, f, "leitung")
    rollen = {m["mail"]: m["rolle"] for m in firmen.mitglieder(db_app(), k, f)}
    assert rollen == {"a@example.org": "leitung", "b@example.org": "leitung"}
    firmen.rolle_aendern(db_app(), k, p, f, "mitglied")  # zweite Leitung vorhanden


def test_rolle_aendern_nur_bestehende_mitgliedschaft(db_admin: Any, db_app: Any) -> None:
    f1, f2 = _firma(db_admin, "firma-a"), _firma(db_admin, "firma-b")
    p, q = _person(db_admin, "a@example.org"), _person(db_admin, "b@example.org")
    _mitglied(db_admin, p, f1, "leitung")
    _mitglied(db_admin, q, f2, "mitglied")  # q ist nur in Firma B
    k = _ctx(p, {f1: "leitung"})
    vorher = db_admin.execute("select * from event.mitgliedschaft order by 1, 2").fetchall()
    for person in (q, uuid4()):
        with pytest.raises(NichtGefunden):
            firmen.rolle_aendern(db_app(), k, person, f1, "leitung")
    # Superuser-Verbindung ohne RLS: auch die Fachschicht legt nichts an.
    with pytest.raises(NichtGefunden):
        firmen.rolle_aendern(db_admin, k, q, f1, "leitung")
    assert db_admin.execute("select * from event.mitgliedschaft order by 1, 2").fetchall() == vorher


def test_person_finden_oder_anlegen(db_app: Any) -> None:
    a = firmen.person_finden_oder_anlegen(db_app(), "  Neu@Example.ORG ", "Nina Neu")
    b = firmen.person_finden_oder_anlegen(db_app(), "neu@example.org", "Anderer Name")
    assert a["id"] == b["id"] and a["mail"] == "neu@example.org" and b["name"] == "Nina Neu"
    assert set(a) == {"id", "mail", "name"}
    for mail, name in (("", "x"), ("x@example.org", "  ")):
        with pytest.raises(ValueError):
            firmen.person_finden_oder_anlegen(db_app(), mail, name)


def test_kontext_fuer_person_mit_zwei_firmen(db_admin: Any, db_app: Any) -> None:
    f1, f2 = _firma(db_admin, "firma-a"), _firma(db_admin, "firma-b")
    p = _person(db_admin, "a@example.org")
    _mitglied(db_admin, p, f1, "leitung")
    _mitglied(db_admin, p, f2, "mitglied")
    k = firmen.kontext_fuer_person(db_app(), p)
    assert dict(k.rollen) == {f1: "leitung", f2: "mitglied"}
    assert k.firmen == {f1, f2} and not k.betreiber and k.person_id == p


def test_kontext_fuer_person_unbekannt_oder_inaktiv(db_admin: Any, db_app: Any) -> None:
    with pytest.raises(NichtGefunden):
        firmen.kontext_fuer_person(db_app(), uuid4())
    p = _person(db_admin, "a@example.org")
    db_admin.execute("update event.person set aktiv = false where id = %s", (p,))
    with pytest.raises(NichtGefunden):
        firmen.kontext_fuer_person(db_app(), p)


def test_transaktion_setzt_nur_lokal(db_app: Any) -> None:
    f, p, e = uuid4(), uuid4(), uuid4()
    conn = db_app()
    k = Kontext(person_id=p, betreiber=True, rollen={f: "leitung"}, event_id=e)
    with transaktion(conn, k) as cur:
        cur.execute(
            "select current_setting('event.firmen') fi, current_setting('event.betreiber') b,"
            " current_setting('event.person') p, current_setting('event.event') e"
        )
        r = cur.fetchone()
        assert (r["fi"], r["b"], r["p"], r["e"]) == (str(f), "an", str(p), str(e))
    for name in ("firmen", "betreiber", "person", "event"):
        wert = conn.execute("select current_setting(%s, true) v", ("event." + name,)).fetchone()["v"]
        assert wert in ("", None)


def test_transaktion_verschachtelt_wirft(db_app: Any) -> None:
    conn = db_app()
    with transaktion(conn, Kontext.system()):
        with pytest.raises(RuntimeError):
            with transaktion(conn, Kontext(person_id=None, betreiber=True)):
                pass


def test_transaktion_auf_freier_nicht_autocommit_verbindung(db_app: Any) -> None:
    conn = db_app()
    conn.autocommit = False
    with transaktion(conn, Kontext(person_id=uuid4(), betreiber=True)) as cur:
        cur.execute("select current_setting('event.betreiber') b")
        assert cur.fetchone()["b"] == "an"
    for name in ("firmen", "betreiber", "person", "event"):
        wert = conn.execute("select current_setting(%s, true) v", ("event." + name,)).fetchone()["v"]
        assert wert in ("", None)
    conn.rollback()


def test_transaktion_in_offener_transaktion_wirft(db_app: Any) -> None:
    conn = db_app()
    conn.autocommit = False
    conn.execute("select 1")
    with pytest.raises(RuntimeError):
        with transaktion(conn, Kontext.system()):
            pass
    conn.rollback()


def test_mitgliedschaft_setzen_und_rolle_aendern_pruefen_ihr_recht(
    db_admin: Any, db_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = _firma(db_admin, "firma-a")
    p, q, r = (_person(db_admin, m) for m in ("a@example.org", "b@example.org", "c@example.org"))
    _mitglied(db_admin, p, f, "leitung")
    _mitglied(db_admin, q, f, "mitglied")
    k = _ctx(p, {f: "leitung"})
    alle = rechte.AKTIONEN - {"firma_anlegen"}
    # Leitung ohne rolle_aendern: Rollenwechsel scheitert, Neuanlage geht.
    monkeypatch.setitem(rechte._ROLLEN, "leitung", alle - {"rolle_aendern"})
    with pytest.raises(KeinRecht):
        firmen.rolle_aendern(db_app(), k, q, f, "leitung")
    firmen.mitgliedschaft_setzen(db_app(), k, r, f, "mitglied")
    # Leitung ohne einladen: Neuanlage scheitert, Rollenwechsel geht.
    monkeypatch.setitem(rechte._ROLLEN, "leitung", alle - {"einladen"})
    with pytest.raises(KeinRecht):
        firmen.mitgliedschaft_setzen(db_app(), k, uuid4(), f, "mitglied")
    firmen.rolle_aendern(db_app(), k, q, f, "leitung")
    rollen = {m["mail"]: m["rolle"] for m in firmen.mitglieder(db_app(), k, f)}
    assert rollen == {"a@example.org": "leitung", "b@example.org": "leitung",
                      "c@example.org": "mitglied"}
