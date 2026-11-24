"""Tests fuer werkzeuge.py und db.py (PgBuch gegen einen aufzeichnenden Falsch-Pool)."""
from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import psycopg.errors
import pytest
from psycopg.types.json import Jsonb

from spaces.auftragsbuch import db
from spaces.auftragsbuch.gleichstand import gleichstand
from spaces.auftragsbuch.vertrag import FormFehler, lade_auftragsarten, vertrag_hash
from spaces.auftragsbuch.werkzeuge import NichtGefunden, ausfuehren, katalog

SPACES = Path(__file__).resolve().parents[2]
ARTEN = lade_auftragsarten(SPACES / "_contract")
EINGABE = {"zeitraum": "heute", "entwuerfe_anlegen": True}
FREMDE_ID = "11111111-1111-4111-8111-111111111111"
UNBEKANNTE_ID = "22222222-2222-4222-8222-222222222222"


class FalschesBuch:
    """Merkt Aufrufe und liefert vorbereitete Zeilen."""

    def __init__(self) -> None:
        self.angelegt: list[tuple] = []
        self.erledigt: list[str] = []
        self.zeilen: dict[str, dict] = {}
        self.letzte_id = ""
        self.erledigt_ergebnis = True

    def anlegen(self, an, art, version, eingabe, plan_id, frist_min):
        self.angelegt.append((an, art, version, eingabe, plan_id, frist_min))
        self.letzte_id = str(uuid.uuid4())
        return self.letzte_id

    def lesen(self, auftrag_id):
        return self.zeilen.get(auftrag_id)

    def probe_erledigen(self, auftrag_id):
        self.erledigt.append(auftrag_id)
        return self.erledigt_ergebnis


@pytest.fixture
def buch() -> FalschesBuch:
    return FalschesBuch()


def test_katalog_hat_feste_und_vertragswerkzeuge():
    namen = [w["name"] for w in katalog(ARTEN)]
    assert namen == ["auftrag_probe", "auftrag_lesen", "rowboat_vorschlaege_anlegen", "sales_freigaben_zaehlen",
                     "sales_kontakt_finden", "sales_post_offen", "sales_post_pruefen", "sales_termine_heute"]
    assert gleichstand(ARTEN, {}, [], dienst_werkzeuge=namen) == []


def test_katalog_schema_einer_auftragsart():
    w = {w["name"]: w for w in katalog(ARTEN)}["sales_post_pruefen"]
    assert w["inputSchema"] == {
        "type": "object", "additionalProperties": False, "required": ["eingabe"],
        "properties": {"eingabe": ARTEN[("sales", "post_pruefen")].eingabe,
                       "plan_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"}},
    }
    assert w["description"] == ARTEN[("sales", "post_pruefen")].beschreibung


def test_auftragsart_legt_mit_werten_aus_dem_vertrag_an(buch):
    r = ausfuehren("sales_post_pruefen", {"eingabe": EINGABE, "plan_id": "plan_1"}, buch, ARTEN)
    assert buch.angelegt == [("sales", "post_pruefen", 2, EINGABE, "plan_1", 15)]
    assert r == {"auftrag_id": buch.letzte_id, "an": "sales", "art": "post_pruefen", "version": 2,
                 "stand": "offen"}


def test_falsche_eingabe_erreicht_die_datenbank_nicht(buch):
    with pytest.raises(FormFehler):
        ausfuehren("sales_post_pruefen", {"eingabe": {"zeitraum": "gestern", "entwuerfe_anlegen": True}},
                   buch, ARTEN)
    assert buch.angelegt == []


@pytest.mark.parametrize("argumente", [
    {},
    {"eingabe": EINGABE, "plan_id": "ungueltig!"},
    {"eingabe": EINGABE, "plan_id": "x" * 65},
    {"eingabe": EINGABE, "plan_id": 7},
    {"eingabe": EINGABE, "plan_id": "plan_1\n"},
    {"eingabe": EINGABE, "mehr": 1},
    "kein dict",
])
def test_formfehler_bei_argumenten_ohne_den_wert(buch, argumente):
    with pytest.raises(FormFehler) as e:
        ausfuehren("sales_post_pruefen", argumente, buch, ARTEN)
    assert "ungueltig!" not in str(e.value) and "mehr" not in str(e.value)
    assert buch.angelegt == []


def test_probe_legt_an_erledigt_und_nennt_den_vertragsstand(buch):
    r = ausfuehren("auftrag_probe", {}, buch, ARTEN)
    assert buch.angelegt == [("brain", "probe", 1, {}, None, 5)] and buch.erledigt == [r["auftrag_id"]]
    assert r["stand"] == "fertig" and r["vertrag_hash"] == vertrag_hash(ARTEN)


def test_probe_reicht_die_plan_id_durch(buch):
    ausfuehren("auftrag_probe", {"plan_id": "plan-2"}, buch, ARTEN)
    assert buch.angelegt == [("brain", "probe", 1, {}, "plan-2", 5)]


def test_probe_nicht_erledigt_ist_interner_fehler(buch):
    buch.erledigt_ergebnis = False
    with pytest.raises(RuntimeError):
        ausfuehren("auftrag_probe", {}, buch, ARTEN)


def test_lesen_gibt_die_felder_des_vertrags(buch):
    kennung = str(uuid.uuid4())
    buch.zeilen[kennung] = {
        "id": kennung, "an": "sales", "art": "kontakt_finden", "art_version": 1, "stand": "offen",
        "ergebnis": {"a": 1}, "fehler": None, "plan_id": "p", "frist_bis": "2026-11-22T10:00:00+00:00",
        "eingabe": {"geheim": 1}, "von": "brain",
    }
    r = ausfuehren("auftrag_lesen", {"id": kennung.upper()}, buch, ARTEN)
    assert r == {"auftrag_id": kennung, "an": "sales", "art": "kontakt_finden", "version": 1,
                 "stand": "offen", "ergebnis": {"a": 1}, "fehler": None, "plan_id": "p",
                 "frist_bis": "2026-11-22T10:00:00+00:00", "beleg": "entfaellt"}


def test_lesen_fremd_und_unbekannt_sind_gleich(buch):
    buch.zeilen = {}
    for kennung in (FREMDE_ID, UNBEKANNTE_ID, "kein-uuid", ""):
        with pytest.raises(NichtGefunden) as e:
            ausfuehren("auftrag_lesen", {"id": kennung}, buch, ARTEN)
        assert str(e.value) == "nicht gefunden"


def test_lesen_ohne_id_ist_formfehler(buch):
    with pytest.raises(FormFehler):
        ausfuehren("auftrag_lesen", {}, buch, ARTEN)


def test_unbekanntes_werkzeug(buch):
    with pytest.raises(NichtGefunden) as e:
        ausfuehren("sales_alles_loeschen", {}, buch, ARTEN)
    assert str(e.value) == "nicht gefunden"


# --- db.PgBuch gegen einen Falsch-Pool ---------------------------------------------------


class _Zeiger:
    def __init__(self, zeile):
        self._zeile = zeile

    def fetchone(self):
        return self._zeile


class _Verbindung:
    def __init__(self, pool):
        self._pool = pool

    def execute(self, sql, params=None):
        self._pool.aufrufe.append((sql, params))
        if self._pool.fehler is not None:
            raise self._pool.fehler
        return _Zeiger(self._pool.zeile)


class _Pool:
    def __init__(self, zeile=None, fehler=None):
        self.aufrufe: list[tuple] = []
        self.zeile = zeile
        self.fehler = fehler

    @contextmanager
    def connection(self):
        yield _Verbindung(self)


class _Raise(psycopg.errors.RaiseException):
    """RaiseException mit vorbereiteter Meldung (ohne echte Datenbank)."""

    def __init__(self, text):
        super().__init__("x")
        self._text = text

    @property
    def diag(self):
        return SimpleNamespace(message_primary=self._text)


def test_pgbuch_anlegen_nutzt_nur_die_funktion_mit_parametern():
    kennung = uuid.uuid4()
    pool = _Pool(zeile=(kennung,))
    eingabe = {"zeitraum": "heute'; drop table x; --"}
    r = db.PgBuch(pool, "laden-1", "mandant-1").anlegen("sales", "post_pruefen", 1, eingabe, "plan_1", 15)
    assert r == str(kennung)
    [(sql, params)] = pool.aufrufe
    assert sql.startswith("select auftrag.brain_anlegen(") and "drop" not in sql and "laden-1" not in sql
    assert sql.count("%s") == len(params) == 8
    assert params[:5] == ("sales", "post_pruefen", 1, "laden-1", "mandant-1")
    assert isinstance(params[5], Jsonb) and params[5].obj == eingabe
    assert params[6:] == ("plan_1", 15)


def test_pgbuch_lesen_und_erledigen():
    kennung = str(uuid.uuid4())
    pool = _Pool(zeile=({"id": kennung},))
    buch = db.PgBuch(pool, "l", "m")
    assert buch.lesen(kennung) == {"id": kennung}
    pool.zeile = (None,)
    assert buch.lesen(kennung) is None
    pool.zeile = (True,)
    assert buch.probe_erledigen(kennung) is True
    pool.zeile = (False,)
    assert buch.probe_erledigen(kennung) is False
    assert [re.match(r"select (auftrag\.\w+)\(", s).group(1) for s, _ in pool.aufrufe] == [
        "auftrag.brain_lesen", "auftrag.brain_lesen", "auftrag.brain_probe_erledigen",
        "auftrag.brain_probe_erledigen"]
    assert all(p == (kennung,) for _, p in pool.aufrufe)


@pytest.mark.parametrize("text", db.ABLEHNUNGSTEXTE)
def test_pgbuch_feste_ablehnung_wird_abgelehnt(text):
    pool = _Pool(fehler=_Raise(text))
    with pytest.raises(db.Abgelehnt) as e:
        db.PgBuch(pool, "l", "m").anlegen("brain", "probe", 1, {}, None, 5)
    assert str(e.value) == text


def test_pgbuch_fremde_meldung_und_andere_fehler_laufen_unveraendert_durch():
    with pytest.raises(psycopg.errors.RaiseException):
        db.PgBuch(_Pool(fehler=_Raise("etwas anderes")), "l", "m").anlegen("brain", "probe", 1, {}, None, 5)
    with pytest.raises(psycopg.OperationalError):
        db.PgBuch(_Pool(fehler=psycopg.OperationalError("weg")), "l", "m").lesen(str(uuid.uuid4()))


def test_ablehnungstexte_stehen_woertlich_in_der_sql_datei():
    sql = (SPACES / "marketing" / "db" / "100_auftragsbuch_brain.sql").read_text(encoding="utf-8")
    assert len(db.ABLEHNUNGSTEXTE) == 6
    for text in db.ABLEHNUNGSTEXTE:
        assert f"RAISE EXCEPTION '{text}'" in sql


# --- Lesen: Form und Beleg ------------------------------------------------------------

PUNKT = "0123456789abcdef"


def _eingabe(*ids):
    return {"punkte": [{"punkt_id": i, "titel": "t", "frist": None, "link": ""} for i in ids]}


def _fertig(buch, an, art, version, ergebnis, eingabe=None):
    kennung = str(uuid.uuid4())
    buch.zeilen[kennung] = {
        "id": kennung, "an": an, "art": art, "art_version": version, "stand": "fertig",
        "ergebnis": ergebnis, "fehler": None, "plan_id": None, "frist_bis": None, "eingabe": eingabe or {},
    }
    return kennung


def _todo(*texte):
    return {"list": {"blocks": [{"kind": "item", "item": {"key": "k", "text": t, "checked": False}}
                                for t in texte]}, "suggestions": []}


def test_lesen_falsche_form_ohne_inhalt(buch):
    kennung = _fertig(buch, "rowboat", "vorschlaege_anlegen", 2,
                      {"punkte": [{"punkt_id": "x", "stand": "neu", "titel": "geheim"}]})
    r = ausfuehren("auftrag_lesen", {"id": kennung}, buch, ARTEN)
    assert (r["stand"], r["fehler"], r["ergebnis"]) == ("fehler", "Ergebnis in falscher Form", None)
    assert "geheim" not in str(r)


def test_lesen_aeltere_version_bleibt_wie_gespeichert(buch):
    kennung = _fertig(buch, "sales", "post_pruefen", 1, {"alt": 1})
    r = ausfuehren("auftrag_lesen", {"id": kennung}, buch, ARTEN)
    assert (r["stand"], r["ergebnis"], r["fehler"], r["beleg"]) == ("fertig", {"alt": 1}, None, "nicht_pruefbar")


def _leser_null(todo):
    def leser(kanal, koerper):
        assert kanal == "todo:get" and koerper is None  # Rowboat: req ist z.null()
        return todo
    return leser


def test_lesen_beleg_bestaetigt(buch):
    kennung = _fertig(buch, "rowboat", "vorschlaege_anlegen", 2,
                      {"punkte": [{"punkt_id": PUNKT, "stand": "neu"}]}, _eingabe(PUNKT))
    r = ausfuehren("auftrag_lesen", {"id": kennung}, buch, ARTEN,
                   _leser_null(_todo(f"Etwas  tun #{PUNKT.upper()}")))
    assert r["beleg"] == "bestaetigt" and r["stand"] == "fertig"


def test_lesen_beleg_nicht_bestaetigt(buch):
    kennung = _fertig(buch, "rowboat", "vorschlaege_anlegen", 2,
                      {"punkte": [{"punkt_id": PUNKT, "stand": "neu"}]}, _eingabe(PUNKT))
    r = ausfuehren("auftrag_lesen", {"id": kennung}, buch, ARTEN,
                   _leser_null(_todo("Etwas #ffffffffffffffff")))
    assert r["beleg"] == "nicht_bestaetigt"


def test_lesen_beleg_nicht_pruefbar(buch):
    kennung = _fertig(buch, "rowboat", "vorschlaege_anlegen", 2,
                      {"punkte": [{"punkt_id": PUNKT, "stand": "neu"}]}, _eingabe(PUNKT))

    def leser(kanal, koerper):
        raise RuntimeError("kaputt")

    assert ausfuehren("auftrag_lesen", {"id": kennung}, buch, ARTEN, leser)["beleg"] == "nicht_pruefbar"
    assert ausfuehren("auftrag_lesen", {"id": kennung}, buch, ARTEN)["beleg"] == "nicht_pruefbar"


def test_beleg_entfaellt_bei_form():
    from spaces.auftragsbuch.belege import beleg
    assert beleg(ARTEN[("sales", "kontakt_finden")], {}, {}, None) == "entfaellt"
