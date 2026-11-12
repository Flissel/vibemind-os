"""Tests fuer die Kern-Bausteine (generische CRUD-Fachschicht, echte Postgres-DB)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from spaces.event_management.system import kern, verlauf
from spaces.event_management.system.bausteine import REGISTRY
from spaces.event_management.system.kontext import KeinRecht, Kontext, NichtGefunden

BEGINN = datetime(2027, 3, 1, 18, 0, tzinfo=timezone.utc)


def _firma(admin: Any, kurz: str) -> UUID:
    return admin.execute(
        "insert into event.firma (name, kurz) values (%s, %s) returning id",
        ("Beispiel " + kurz, kurz),
    ).fetchone()["id"]


def _person(admin: Any, mail: str) -> UUID:
    return admin.execute(
        "insert into event.person (mail, name) values (%s, 'Erika Beispiel') returning id",
        (mail,),
    ).fetchone()["id"]


def _event(admin: Any, firma: UUID, kategorie: str = "weihnachtsfeier",
           quelle: str = "neu") -> UUID:
    return admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende, quelle)"
        " values (%s, %s, 'Feier', %s, %s, %s) returning id",
        (firma, kategorie, BEGINN, BEGINN + timedelta(hours=3), quelle),
    ).fetchone()["id"]


def _kontakt(admin: Any, firma: UUID, name: str = "Max Muster") -> UUID:
    return admin.execute(
        "insert into event.kontakt (firma_id, name) values (%s, %s) returning id", (firma, name),
    ).fetchone()["id"]


def _k(p: UUID, rollen: dict[UUID, str]) -> Kontext:
    return Kontext(person_id=p, betreiber=False, rollen=rollen)


@pytest.fixture
def welt(db_admin: Any) -> dict[str, Any]:
    fa, fb = _firma(db_admin, "bsp-a"), _firma(db_admin, "bsp-b")
    p = _person(db_admin, "a@example.org")
    return {
        "fa": fa, "fb": fb, "k": _k(p, {fa: "leitung"}),
        "ea": _event(db_admin, fa), "eb": _event(db_admin, fb),
    }


def _anzahl(admin: Any, tabelle: str) -> int:
    return admin.execute(f"select count(*) as n from event.{tabelle}").fetchone()["n"]


def test_aufgabe_anlegen_aendern_loeschen_mit_verlauf(db_admin: Any, db_app: Any,
                                                       welt: dict[str, Any]) -> None:
    conn, k, ev = db_app(), welt["k"], welt["ea"]
    row = kern.anlegen(conn, k, ev, "aufgaben", {"titel": "Saal buchen"})
    assert row["status"] == "offen"
    neu = kern.aendern(conn, k, ev, "aufgaben", row["id"], {"status": "erledigt"})
    assert neu["status"] == "erledigt"
    assert [r["titel"] for r in kern.liste(conn, k, ev, "aufgaben")] == ["Saal buchen"]
    kern.loeschen(conn, k, ev, "aufgaben", row["id"])
    assert kern.liste(conn, k, ev, "aufgaben") == []
    eintraege = verlauf.lesen(conn, k, ev)
    assert sorted(e["aktion"] for e in eintraege) == ["angelegt", "geaendert", "geloescht"]
    assert all(e["ziel_id"] == row["id"] and e["baustein"] == "aufgaben" for e in eintraege)


def test_budget_bei_team_meeting_abgelehnt(db_admin: Any, db_app: Any,
                                            welt: dict[str, Any]) -> None:
    ev = _event(db_admin, welt["fa"], "team_meeting")
    conn = db_app()
    with pytest.raises(ValueError, match="Baustein budget gehört nicht zu Kategorie team_meeting"):
        kern.anlegen(conn, welt["k"], ev, "budget",
                     {"titel": "X", "art": "kosten", "betrag_cent": 100})
    with pytest.raises(ValueError, match="gehört nicht zu Kategorie"):
        kern.liste(conn, welt["k"], ev, "budget")
    assert _anzahl(db_admin, "budget_posten") == 0
    assert _anzahl(db_admin, "verlauf") == 0


@pytest.mark.parametrize("werte", [
    {"firma_id": str(uuid4()), "titel": "X"},
    {"titel; drop table event.aufgabe": 1},
    {"id": str(uuid4()), "titel": "X"},
    {"event_id": str(uuid4()), "titel": "X"},
    {"angelegt": "2020-01-01T00:00:00+00:00", "titel": "X"},
])
def test_unbekanntes_feld_abgelehnt(db_admin: Any, db_app: Any, welt: dict[str, Any],
                                     werte: dict[str, Any]) -> None:
    conn = db_app()
    with pytest.raises(ValueError):
        kern.anlegen(conn, welt["k"], welt["ea"], "aufgaben", werte)
    row = kern.anlegen(conn, welt["k"], welt["ea"], "aufgaben", {"titel": "ok"})
    with pytest.raises(ValueError):
        kern.aendern(conn, welt["k"], welt["ea"], "aufgaben", row["id"], werte)
    assert _anzahl(db_admin, "aufgabe") == 1
    assert _anzahl(db_admin, "verlauf") == 1


def test_legacy_event_abgelehnt(db_admin: Any, db_app: Any, welt: dict[str, Any]) -> None:
    ev = _event(db_admin, welt["fa"], quelle="legacy")
    conn = db_app()
    with pytest.raises(ValueError, match="Legacy"):
        kern.anlegen(conn, welt["k"], ev, "aufgaben", {"titel": "X"})
    assert kern.liste(conn, welt["k"], ev, "aufgaben") == []
    zeile = db_admin.execute(
        "insert into event.aufgabe (event_id, firma_id, titel) values (%s,%s,'alt') returning id",
        (ev, welt["fa"])).fetchone()["id"]
    with pytest.raises(ValueError, match="Legacy"):
        kern.aendern(conn, welt["k"], ev, "aufgaben", zeile, {"titel": "neu"})
    with pytest.raises(ValueError, match="Legacy"):
        kern.loeschen(conn, welt["k"], ev, "aufgaben", zeile)
    assert db_admin.execute("select titel from event.aufgabe").fetchone()["titel"] == "alt"


def test_teilnehmende_zusage_werte(db_admin: Any, db_app: Any, welt: dict[str, Any]) -> None:
    conn, k, ev = db_app(), welt["k"], welt["ea"]
    kt = kern.kontakt_anlegen(conn, k, welt["fa"], "Max Muster", mail="max@example.org")
    row = kern.anlegen(conn, k, ev, "teilnehmende", {"kontakt_id": kt["id"]})
    assert row["zusage"] == "offen"
    for z in ("zugesagt", "vielleicht", "abgesagt", "offen"):
        assert kern.aendern(conn, k, ev, "teilnehmende", row["id"], {"zusage": z})["zusage"] == z
    with pytest.raises(ValueError, match="zusage") as e:
        kern.aendern(conn, k, ev, "teilnehmende", row["id"], {"zusage": "geheimwert"})
    assert "geheimwert" not in str(e.value)
    with pytest.raises(ValueError, match="bereits"):
        kern.anlegen(conn, k, ev, "teilnehmende", {"kontakt_id": kt["id"]})


def test_budget_betrag_validierung(db_app: Any, welt: dict[str, Any]) -> None:
    conn, k, ev = db_app(), welt["k"], welt["ea"]
    basis = {"titel": "Catering", "art": "kosten"}
    for schlecht in (True, -1, 1.5, "100", None):
        with pytest.raises(ValueError, match="betrag_cent"):
            kern.anlegen(conn, k, ev, "budget", {**basis, "betrag_cent": schlecht})
    with pytest.raises(ValueError, match="art"):
        kern.anlegen(conn, k, ev, "budget", {**basis, "art": "x", "betrag_cent": 1})
    row = kern.anlegen(conn, k, ev, "budget", {**basis, "betrag_cent": 12345})
    assert row["betrag_cent"] == 12345 and row["status"] == "geplant"


def test_ablauf_zeiten_und_naive_zeit_ist_berlin(db_app: Any, welt: dict[str, Any]) -> None:
    conn, k, ev = db_app(), welt["k"], welt["ea"]
    row = kern.anlegen(conn, k, ev, "ablauf",
                       {"titel": "Start", "beginn": datetime(2027, 3, 1, 18, 0)})
    assert row["beginn"].utcoffset() is not None
    assert row["beginn"].astimezone(timezone.utc).hour == 17  # Berlin im Maerz = UTC+1
    with pytest.raises(ValueError, match="Ende"):
        kern.anlegen(conn, k, ev, "ablauf", {"titel": "X", "beginn": "2027-03-01T10:00:00",
                                              "ende": "2027-03-01T09:00:00"})
    with pytest.raises(ValueError):
        kern.aendern(conn, k, ev, "ablauf", row["id"], {"ende": "2027-03-01T08:00:00"})


def test_aendern_leer_und_pflicht(db_app: Any, welt: dict[str, Any]) -> None:
    conn, k, ev = db_app(), welt["k"], welt["ea"]
    with pytest.raises(ValueError, match="titel"):
        kern.anlegen(conn, k, ev, "aufgaben", {})
    with pytest.raises(ValueError, match="titel"):
        kern.anlegen(conn, k, ev, "aufgaben", {"titel": "  "})
    row = kern.anlegen(conn, k, ev, "aufgaben", {"titel": "A"})
    with pytest.raises(ValueError):
        kern.aendern(conn, k, ev, "aufgaben", row["id"], {})
    with pytest.raises(ValueError, match="titel"):
        kern.aendern(conn, k, ev, "aufgaben", row["id"], {"titel": None})
    with pytest.raises(NichtGefunden):
        kern.aendern(conn, k, ev, "aufgaben", uuid4(), {"titel": "B"})
    with pytest.raises(NichtGefunden):
        kern.loeschen(conn, k, ev, "aufgaben", uuid4())


def test_verweise_firma_und_event_uebergreifend_abgelehnt(
        db_admin: Any, db_app: Any, welt: dict[str, Any]) -> None:
    conn, k, ev = db_app(), welt["k"], welt["ea"]
    fremd = _kontakt(db_admin, welt["fb"])
    with pytest.raises(ValueError, match="Verweis ungültig: verantwortlich_id") as e:
        kern.anlegen(conn, k, ev, "aufgaben", {"titel": "X", "verantwortlich_id": fremd})
    assert str(fremd) not in str(e.value)
    with pytest.raises(ValueError, match="Verweis ungültig: kontakt_id"):
        kern.anlegen(conn, k, ev, "orga_team", {"kontakt_id": str(fremd)})
    ev2 = _event(db_admin, welt["fa"])
    kat2 = kern.anlegen(conn, k, ev2, "aufgaben_kategorien", {"name": "Technik"})
    with pytest.raises(ValueError, match="Verweis ungültig: kategorie_id"):
        kern.anlegen(conn, k, ev, "aufgaben", {"titel": "X", "kategorie_id": kat2["id"]})
    kat = kern.anlegen(conn, k, ev, "aufgaben_kategorien", {"name": "Technik"})
    a = kern.anlegen(conn, k, ev, "aufgaben", {"titel": "X", "kategorie_id": str(kat["id"])})
    assert a["kategorie_id"] == kat["id"]
    assert _anzahl(db_admin, "aufgabe") == 1


def test_kontakte_anlegen_und_suche(db_admin: Any, db_app: Any, welt: dict[str, Any]) -> None:
    conn, k, fa = db_app(), welt["k"], welt["fa"]
    kern.kontakt_anlegen(conn, k, fa, "Anna 100%", mail="anna@example.org")
    kern.kontakt_anlegen(conn, k, fa, "Bert", telegram="@bert_x")
    assert [c["name"] for c in kern.kontakte(conn, k, fa)] == ["Anna 100%", "Bert"]
    assert [c["name"] for c in kern.kontakte(conn, k, fa, "100%")] == ["Anna 100%"]
    assert [c["name"] for c in kern.kontakte(conn, k, fa, "bert_x")] == ["Bert"]
    assert [c["name"] for c in kern.kontakte(conn, k, fa, "%")] == ["Anna 100%"]  # literal
    assert len(kern.kontakte(conn, k, fa, "_")) == 1  # nur "bert_x" enthaelt ein "_"
    assert _anzahl(db_admin, "verlauf") == 0
    with pytest.raises(NichtGefunden):
        kern.kontakt_anlegen(conn, k, welt["fb"], "Fremd")
    with pytest.raises(NichtGefunden):
        kern.kontakte(conn, k, welt["fb"])
    lese = _k(welt["k"].person_id, {fa: "mitglied"})
    kern.kontakt_anlegen(conn, lese, fa, "Mitglied darf")


def _kontakt_zeile(admin: Any, kontakt: UUID) -> dict[str, Any] | None:
    return admin.execute("select * from event.kontakt where id = %s", (kontakt,)).fetchone()


@pytest.mark.parametrize("schicht", ["rls", "fachschicht"])
def test_kontakt_aendern_und_entfernen(db_admin: Any, db_app: Any, welt: dict[str, Any],
                                       schicht: str) -> None:
    """`rls`: Verbindung sieht nur Firma A. `fachschicht`: Superuser ohne RLS, nur die
    Rechtepruefung und die Firmenbindung im SQL halten."""
    fa, fb, k = welt["fa"], welt["fb"], welt["k"]
    conn = db_app(firmen=[fa]) if schicht == "rls" else db_admin
    kt = _kontakt(db_admin, fa, "Max Muster")
    neu = kern.kontakt_aendern(conn, k, fa, kt, {"name": "  Max Beispiel ",
                                                 "mail": "max@example.org"})
    assert (neu["name"], neu["mail"], neu["telegram"]) == ("Max Beispiel", "max@example.org",
                                                          None)
    neu = kern.kontakt_aendern(conn, k, fa, kt, {"telegram": "@max_bsp", "mail": None})
    assert (neu["name"], neu["mail"], neu["telegram"]) == ("Max Beispiel", None, "@max_bsp")
    zeile = _kontakt_zeile(db_admin, kt)
    assert zeile is not None and zeile["telegram"] == "@max_bsp"

    mitglied = _k(k.person_id, {fa: "mitglied"})
    assert kern.kontakt_aendern(conn, mitglied, fa, kt, {"name": "Max M."})["name"] == "Max M."
    with pytest.raises(KeinRecht):
        kern.kontakt_entfernen(conn, mitglied, fa, kt)
    assert _kontakt_zeile(db_admin, kt) is not None

    for werte, meldung in (
        ({}, "Keine Werte"), ({"firma_id": str(fb)}, "Unbekanntes"),
        ({"person_id": str(uuid4())}, "Unbekanntes"), ({"name": "   "}, "name"),
        ({"name": None}, "name"), ({"mail": "a\x00b"}, "mail"), ({"telegram": 5}, "telegram"),
    ):
        with pytest.raises(ValueError, match=meldung):
            kern.kontakt_aendern(conn, k, fa, kt, werte)
    zeile = _kontakt_zeile(db_admin, kt)
    assert zeile is not None and zeile["name"] == "Max M."

    benutzt = _kontakt(db_admin, fa, "Im Orga-Team")
    kern.anlegen(conn, k, welt["ea"], "orga_team", {"kontakt_id": benutzt})
    with pytest.raises(ValueError, match="^Kontakt wird noch verwendet$"):
        kern.kontakt_entfernen(conn, k, fa, benutzt)
    assert _kontakt_zeile(db_admin, benutzt) is not None

    kern.kontakt_entfernen(conn, k, fa, kt)
    assert _kontakt_zeile(db_admin, kt) is None
    with pytest.raises(NichtGefunden):
        kern.kontakt_entfernen(conn, k, fa, kt)
    with pytest.raises(NichtGefunden):
        kern.kontakt_aendern(conn, k, fa, kt, {"name": "Weg"})


@pytest.mark.parametrize("schicht", ["rls", "fachschicht"])
def test_kontakt_aendern_entfernen_mandantenmatrix(db_admin: Any, db_app: Any,
                                                    welt: dict[str, Any], schicht: str) -> None:
    fa, fb, k = welt["fa"], welt["fb"], welt["k"]
    conn = db_app(firmen=[fa]) if schicht == "rls" else db_admin
    fremd = _kontakt(db_admin, fb, "Fremd Beispiel")
    vorher = _kontakt_zeile(db_admin, fremd)
    # Fremde Firma direkt adressiert, und der fremde Kontakt ueber die eigene Firma.
    for firma in (fb, fa):
        with pytest.raises(NichtGefunden):
            kern.kontakt_aendern(conn, k, firma, fremd, {"name": "Gekapert"})
        with pytest.raises(NichtGefunden):
            kern.kontakt_entfernen(conn, k, firma, fremd)
    assert _kontakt_zeile(db_admin, fremd) == vorher


def test_registry_typen_vokabular() -> None:
    erlaubt = {"text", "datumzeit", "ganzzahl", "kontakt", "url"}
    for t in REGISTRY.values():
        for typ in t.felder.values():
            if typ.startswith("ref:"):
                assert typ[len("ref:"):] in REGISTRY, typ
            else:
                assert typ in erlaubt or typ.startswith("auswahl:"), typ
    assert REGISTRY["aufgaben"].felder["kategorie_id"] == "ref:aufgaben_kategorien"
    for baustein, feld in (("aufgaben", "verantwortlich_id"), ("orga_team", "kontakt_id"),
                           ("teilnehmende", "kontakt_id"), ("ablauf", "verantwortlich_id")):
        assert REGISTRY[baustein].felder[feld] == "kontakt"


def test_feldtypen_kontakt_ref_url() -> None:
    u = uuid4()
    assert kern._wert("kontakt_id", "kontakt", str(u)) == u
    assert kern._wert("kategorie_id", "ref:aufgaben_kategorien", u) == u
    for typ in ("kontakt", "ref:aufgaben_kategorien"):
        with pytest.raises(ValueError, match="UUID"):
            kern._wert("x_id", typ, "keine-uuid")
    assert kern._wert("link", "url", "https://example.org/a") == "https://example.org/a"
    for schlecht in ("ftp://example.org", "example.org", 5, "https://a\x00"):
        with pytest.raises(ValueError, match="link"):
            kern._wert("link", "url", schlecht)


def test_registry_inhalt() -> None:
    assert set(REGISTRY) == {"aufgaben", "aufgaben_kategorien", "orga_team", "teilnehmende",
                             "ablauf", "budget"}
    assert REGISTRY["aufgaben_kategorien"].baustein == "aufgaben"
    for t in REGISTRY.values():
        assert not ({"id", "event_id", "firma_id", "angelegt"} & set(t.felder))
        assert t.pflicht <= set(t.felder)


def _beispielwerte(baustein: str, admin: Any, firma: UUID) -> dict[str, Any]:
    return {
        "aufgaben": {"titel": "Aufgabe"},
        "aufgaben_kategorien": {"name": "Kat"},
        "orga_team": {"kontakt_id": _kontakt(admin, firma)},
        "teilnehmende": {"kontakt_id": _kontakt(admin, firma, "Tn")},
        "ablauf": {"titel": "Punkt", "beginn": BEGINN},
        "budget": {"titel": "Posten", "art": "kosten", "betrag_cent": 5},
    }[baustein]


def _admin_zeile(admin: Any, baustein: str, firma: UUID, event: UUID) -> UUID:
    t = REGISTRY[baustein]
    w = _beispielwerte(baustein, admin, firma)
    spalten = ["event_id", "firma_id", *w]
    return admin.execute(
        f"insert into event.{t.tabelle} ({', '.join(spalten)})"
        f" values ({', '.join(['%s'] * len(spalten))}) returning id",
        [event, firma, *w.values()],
    ).fetchone()["id"]


def _aenderung(t: Any) -> dict[str, Any]:
    if "zusage" in t.felder:
        return {"zusage": "zugesagt"}
    feld = "titel" if "titel" in t.felder else "name" if "name" in t.felder else "rolle"
    return {feld: "x"}


@pytest.mark.parametrize("schicht", ["rls", "fachschicht"])
@pytest.mark.parametrize("baustein", sorted(REGISTRY))
@pytest.mark.parametrize("op", ["liste", "anlegen", "aendern", "loeschen"])
def test_mandantenmatrix_kern(db_admin: Any, db_app: Any, welt: dict[str, Any],
                              baustein: str, op: str, schicht: str) -> None:
    """Firma A gegen Event/Zeilen von Firma B. `rls`: Verbindung sieht nur A (zweites Tor).
    `fachschicht`: Superuser-Verbindung ohne RLS, nur die Rechtepruefung haelt (erstes Tor)."""
    t = REGISTRY[baustein]
    fremd_zeile = _admin_zeile(db_admin, baustein, welt["fb"], welt["eb"])
    # `fachschicht`: Superuser-Verbindung umgeht RLS, nichts wird versteckt; nur `pruefe` haelt.
    conn = db_app(firmen=[welt["fa"]]) if schicht == "rls" else db_admin
    k = welt["k"]
    # Positivkontrolle: die eigene Firma funktioniert.
    eigene = kern.anlegen(conn, k, welt["ea"], baustein,
                          _beispielwerte(baustein, db_admin, welt["fa"]))
    assert eigene["firma_id"] == welt["fa"]
    assert [r["id"] for r in kern.liste(conn, k, welt["ea"], baustein)] == [eigene["id"]]
    legacy_ev = _event(db_admin, welt["fb"], quelle="legacy")
    legacy_zeile = _admin_zeile(db_admin, baustein, welt["fb"], legacy_ev)
    vorher = db_admin.execute(
        f"select * from event.{t.tabelle} where firma_id = %s order by id", (welt["fb"],)
    ).fetchall()
    verlauf_vorher = _anzahl(db_admin, "verlauf")
    for ev, zeile in ((welt["eb"], fremd_zeile), (legacy_ev, legacy_zeile)):
        _fremd_versuch(conn, k, db_admin, welt, t, baustein, op, ev, zeile)
    nachher = db_admin.execute(
        f"select * from event.{t.tabelle} where firma_id = %s order by id", (welt["fb"],)
    ).fetchall()
    assert nachher == vorher
    assert _anzahl(db_admin, "verlauf") == verlauf_vorher


def _fremd_versuch(conn: Any, k: Kontext, db_admin: Any, welt: dict[str, Any], t: Any,
                   baustein: str, op: str, ev: UUID, fremd_zeile: UUID) -> None:
    with pytest.raises((KeinRecht, NichtGefunden)):
        if op == "liste":
            kern.liste(conn, k, ev, baustein)
        elif op == "anlegen":
            kern.anlegen(conn, k, ev, baustein, _beispielwerte(baustein, db_admin, welt["fb"]))
        elif op == "aendern":
            kern.aendern(conn, k, ev, baustein, fremd_zeile, _aenderung(t))
        else:
            kern.loeschen(conn, k, ev, baustein, fremd_zeile)
    if op in ("aendern", "loeschen"):  # fremde Zeile ueber das eigene Event adressiert
        with pytest.raises(NichtGefunden):
            if op == "aendern":
                kern.aendern(conn, k, welt["ea"], baustein, fremd_zeile, _aenderung(t))
            else:
                kern.loeschen(conn, k, welt["ea"], baustein, fremd_zeile)


@pytest.mark.parametrize("baustein", sorted(REGISTRY))
@pytest.mark.parametrize("op", ["liste", "anlegen", "aendern", "loeschen"])
def test_event_bindung_kern_fachschicht(db_admin: Any, welt: dict[str, Any],
                                        baustein: str, op: str) -> None:
    """Kontext auf Event A1 gebunden; Event A2 derselben Firma bleibt unerreichbar.
    Superuser-Verbindung ohne RLS: nur die Fachschicht haelt."""
    t = REGISTRY[baustein]
    ea2 = _event(db_admin, welt["fa"])
    zeile2 = _admin_zeile(db_admin, baustein, welt["fa"], ea2)
    k = Kontext(person_id=welt["k"].person_id, betreiber=False, rollen=welt["k"].rollen,
                event_id=welt["ea"])
    vorher = db_admin.execute(
        f"select * from event.{t.tabelle} where event_id = %s order by id", (ea2,)).fetchall()
    verlauf_vorher = _anzahl(db_admin, "verlauf")
    with pytest.raises(NichtGefunden):
        if op == "liste":
            kern.liste(db_admin, k, ea2, baustein)
        elif op == "anlegen":
            kern.anlegen(db_admin, k, ea2, baustein,
                         _beispielwerte(baustein, db_admin, welt["fa"]))
        elif op == "aendern":
            kern.aendern(db_admin, k, ea2, baustein, zeile2, _aenderung(t))
        else:
            kern.loeschen(db_admin, k, ea2, baustein, zeile2)
    assert db_admin.execute(
        f"select * from event.{t.tabelle} where event_id = %s order by id", (ea2,)
    ).fetchall() == vorher
    assert _anzahl(db_admin, "verlauf") == verlauf_vorher
    # Positivkontrolle: das gebundene Event funktioniert.
    eigene = kern.anlegen(db_admin, k, welt["ea"], baustein,
                          _beispielwerte(baustein, db_admin, welt["fa"]))
    assert [r["id"] for r in kern.liste(db_admin, k, welt["ea"], baustein)] == [eigene["id"]]
