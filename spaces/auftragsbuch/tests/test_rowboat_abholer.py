"""Rowboat-Abholer: Datenbank-Schicht, Bearbeitung eines Auftrags, Start (Buch und Rowboat im Speicher).

Das falsche Rowboat lehnt jeden Koerper ab, den der echte Server ablehnen wuerde (zod je Kanal,
apps/x/packages/shared/src/ipc.ts): `todo:get` und `todo:plannerSignals` nur mit JSON null,
`todo:addSuggestions` nur als Objekt mit `texts: string[]`. Es stellt ein wie
`addExternalSuggestions` (core/src/todo/planner-memory.ts): abgelehnte und gelistete Texte
ueberspringen, Doppelte im Tray ueberspringen, `added` nennt die eingestellten Texte mit
zusammengefasstem Leerraum.
"""
from __future__ import annotations

import copy
import logging
import os
import re
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from spaces.auftragsbuch.rowboat import abholer
from spaces.auftragsbuch.rowboat.empfang import BEKANNT_MAX, PgEmpfang
from spaces.auftragsbuch.rowboat.rpc import GRUND_ABGELEHNT, GRUND_NICHT_ERREICHBAR, GRUND_UNERWARTET, RowboatFehler
from spaces.auftragsbuch.rowboat.text import kennung_in
from spaces.auftragsbuch.vertrag import art_hash, lade_auftragsarten

ALLE = lade_auftragsarten(Path(__file__).resolve().parents[2] / "_contract")
ART = ALLE[("rowboat", "vorschlaege_anlegen")]
ARTEN = {("rowboat", "vorschlaege_anlegen"): ART}
LADEN = "laden-test"
TITEL = ("Mail an Anna schreiben", "Steuererklaerung abgeben", "Brot kaufen")
LINK = "https://beispiel.invalid/geheimer-pfad"
IDS = ("0123456789abcdef", "fedcba9876543210", "00112233aabbccdd")


def _punkte(ids=IDS, titel=TITEL) -> list[dict]:
    return [{"punkt_id": i, "titel": t, "frist": "2026-12-01", "link": LINK} for i, t in zip(ids, titel)]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


# --- Buch im Speicher (Verhalten der vier Huellen aus Migration 101) -------------------------


class FalschesBuch:
    def __init__(self) -> None:
        self.auftraege: dict[str, dict] = {}
        self.abschluss_geht = True
        self.fehler_aufrufe: list[tuple[str, str]] = []

    def anlegen(self, eingabe: dict, art: str = "vorschlaege_anlegen", version: int = 2,
                stand: str = "offen") -> dict:
        auftrag_id = str(uuid.uuid4())
        self.auftraege[auftrag_id] = {"id": auftrag_id, "art": art, "art_version": version, "eingabe": eingabe,
                                      "plan_id": None, "frist_bis": "2026-11-23T12:05:00+01:00",
                                      "laden": LADEN, "stand": stand, "ergebnis": None, "fehler": None}
        return self._uebergabe(self.auftraege[auftrag_id])

    def uebernommen(self, eingabe: dict, **kw) -> dict:
        """Wie naechster: der Auftrag ist schon in_arbeit."""
        return self.anlegen(eingabe, stand="in_arbeit", **kw)

    @staticmethod
    def _uebergabe(a: dict) -> dict:
        return {k: copy.deepcopy(a[k]) for k in ("id", "art", "art_version", "eingabe", "plan_id", "frist_bis")}

    def naechster(self, laden: str, lease: timedelta) -> dict | None:
        assert timedelta(0) < lease <= timedelta(minutes=15)
        for a in self.auftraege.values():
            if a["laden"] == laden and a["stand"] == "offen":
                a["stand"] = "in_arbeit"
                return self._uebergabe(a)
        return None

    def abschliessen(self, auftrag_id: str, ergebnis: dict, laden: str) -> bool:
        a = self.auftraege.get(auftrag_id)
        if not self.abschluss_geht or a is None or a["laden"] != laden or a["stand"] != "in_arbeit":
            return False
        assert isinstance(ergebnis, dict)
        a.update(stand="fertig", ergebnis=copy.deepcopy(ergebnis))
        return True

    def fehler(self, auftrag_id: str, grund: str, laden: str) -> bool:
        self.fehler_aufrufe.append((auftrag_id, grund))
        a = self.auftraege.get(auftrag_id)
        if a is None or a["laden"] != laden or a["stand"] != "in_arbeit":
            return False
        a.update(stand="fehler", fehler=grund[:300])
        return True

    def bekannt(self, laden: str, punkt_ids: list[str]) -> set[str]:
        assert len(punkt_ids) <= BEKANNT_MAX
        da = {p["punkt_id"] for a in self.auftraege.values()
              if a["laden"] == laden and a["art"] == "vorschlaege_anlegen" and a["stand"] == "fertig"
              for p in a["ergebnis"]["punkte"]}
        return da & set(punkt_ids)


# --- Rowboat im Speicher ---------------------------------------------------------------------


class FalschesRowboat:
    def __init__(self) -> None:
        self.vorschlaege: list[str] = []
        self.liste: list[str] = []
        self.archiv: list[str] = []
        self.signale: list[dict] = []
        self.aufrufe: list[tuple[str, object]] = []
        self.fehler: dict[str, str] = {}
        self.schluckt: set[str] = set()  # Kennungen, deren Text addSuggestions still verwirft
        self.verschweigt: set[str] = set()  # Kennungen, die eingestellt, aber nicht in `added` genannt werden

    def _signal(self, kind: str, text: str) -> None:
        self.signale.append({"kind": kind, "key": _norm(text), "at": "2026-11-23T10:00:00.000Z"})

    def annehmen(self, text: str) -> None:
        self.vorschlaege.remove(text)
        self.liste.append(text)
        self._signal("kept", text)

    def ablehnen(self, text: str) -> None:
        self.vorschlaege.remove(text)
        self._signal("dismissed", text)

    def archivieren(self, text: str) -> None:
        self.liste.remove(text)
        self.archiv.append(text)

    def _sticky(self) -> set[str]:
        letzte: dict[str, str] = {}
        for s in self.signale:
            letzte[s["key"]] = s["kind"]
        return {k for k, v in letzte.items() if v in ("dismissed", "taught")}

    def aufruf(self, kanal: str, koerper: object) -> object:
        self.aufrufe.append((kanal, copy.deepcopy(koerper)))
        if kanal in self.fehler:
            raise RowboatFehler(self.fehler[kanal])
        if kanal in ("todo:get", "todo:plannerSignals"):
            if koerper is not None:  # z.null(): {} oder [] -> 400
                raise RowboatFehler(GRUND_UNERWARTET)
            if kanal == "todo:plannerSignals":
                return {"signals": copy.deepcopy(self.signale)}
            bloecke = [{"kind": "item", "item": {"key": _norm(t), "text": t, "checked": False, "receipts": [],
                                                  "children": []}} for t in self.liste]
            bloecke.insert(0, {"kind": "raw", "text": "# To-do"})
            return {"list": {"blocks": bloecke}, "running": [], "sessions": {},
                    "suggestions": list(self.vorschlaege)}
        if kanal == "todo:addSuggestions":
            if (not isinstance(koerper, dict) or not isinstance(koerper.get("texts"), list)
                    or not all(isinstance(t, str) for t in koerper["texts"])):
                raise RowboatFehler(GRUND_UNERWARTET)
            sticky = self._sticky()
            gelistet = {_norm(t) for t in self.liste}
            im_tray = {_norm(t) for t in self.vorschlaege}
            added = []
            for roh in koerper["texts"]:
                text = re.sub(r"\s+", " ", roh).strip()
                if not text or _norm(text) in sticky | gelistet | im_tray:
                    continue
                if kennung_in(text) in self.schluckt:
                    continue
                self.vorschlaege.append(text)
                im_tray.add(_norm(text))
                if kennung_in(text) not in self.verschweigt:
                    added.append(text)
            return {"added": added}
        raise RowboatFehler(GRUND_ABGELEHNT)  # Kanal nicht im engen Schluessel -> 403

    def kanaele(self, kanal: str) -> list[object]:
        return [k for name, k in self.aufrufe if name == kanal]


@pytest.fixture
def buch() -> FalschesBuch:
    return FalschesBuch()


@pytest.fixture
def rowboat() -> FalschesRowboat:
    return FalschesRowboat()


def _bearbeiten(buch, rowboat, auftrag, arten=ARTEN) -> str:
    return abholer.bearbeiten(buch, rowboat, auftrag, arten, LADEN)


def _stand(buch, auftrag) -> dict:
    return buch.auftraege[auftrag["id"]]


def _staende(buch, auftrag) -> list[str]:
    return [p["stand"] for p in _stand(buch, auftrag)["ergebnis"]["punkte"]]


# --- das falsche Rowboat selbst ---------------------------------------------------------------


def test_falsches_rowboat_lehnt_ab_was_der_echte_ablehnt(rowboat):
    for kanal, koerper in [("todo:get", {}), ("todo:plannerSignals", []), ("todo:addSuggestions", None),
                           ("todo:addSuggestions", {"text": ["a"]}), ("todo:addSuggestions", {"texts": [1]})]:
        with pytest.raises(RowboatFehler) as e:
            rowboat.aufruf(kanal, koerper)
        assert e.value.grund == GRUND_UNERWARTET
    with pytest.raises(RowboatFehler) as e:
        rowboat.aufruf("todo:save", {"list": {"blocks": []}})
    assert e.value.grund == GRUND_ABGELEHNT


# --- Bearbeitung ------------------------------------------------------------------------------


def test_drei_neue_punkte_fertig(buch, rowboat):
    auftrag = buch.uebernommen({"punkte": _punkte()})
    assert _bearbeiten(buch, rowboat, auftrag) == "fertig"
    a = _stand(buch, auftrag)
    assert a["stand"] == "fertig"
    assert a["ergebnis"] == {"punkte": [{"punkt_id": i, "stand": "neu"} for i in IDS]}
    (einstellen,) = rowboat.kanaele("todo:addSuggestions")
    assert len(einstellen["texts"]) == 3
    assert [kennung_in(t) for t in rowboat.vorschlaege] == list(IDS)
    assert all(t.startswith("[Gehirn] ") for t in rowboat.vorschlaege)
    assert rowboat.kanaele("todo:get") == [None] and rowboat.kanaele("todo:plannerSignals") == [None]
    assert buch.fehler_aufrufe == []


def test_zweiter_auftrag_gleiche_punkte_schon_vorhanden(buch, rowboat):
    erster = buch.uebernommen({"punkte": _punkte()})
    assert _bearbeiten(buch, rowboat, erster) == "fertig"
    zweiter = buch.uebernommen({"punkte": _punkte()})
    assert _bearbeiten(buch, rowboat, zweiter) == "fertig"
    assert _staende(buch, zweiter) == ["schon_vorhanden"] * 3
    assert len(rowboat.kanaele("todo:addSuggestions")) == 1
    # Angenommen, erledigt und archiviert: Rowboat kennt den Punkt nicht mehr, das Auftragsbuch schon.
    text = rowboat.vorschlaege[0]
    rowboat.annehmen(text)
    rowboat.archivieren(text)
    dritter = buch.uebernommen({"punkte": _punkte()})
    assert _bearbeiten(buch, rowboat, dritter) == "fertig"
    assert _staende(buch, dritter) == ["schon_vorhanden"] * 3
    assert len(rowboat.kanaele("todo:addSuggestions")) == 1
    assert text not in rowboat.vorschlaege and rowboat.liste == []


def test_abgelehnter_punkt(buch, rowboat):
    erster = buch.uebernommen({"punkte": _punkte()})
    assert _bearbeiten(buch, rowboat, erster) == "fertig"
    rowboat.ablehnen(rowboat.vorschlaege[1])
    zweiter = buch.uebernommen({"punkte": _punkte()})
    assert _bearbeiten(buch, rowboat, zweiter) == "fertig"
    assert _staende(buch, zweiter) == ["schon_vorhanden", "frueher_abgelehnt", "schon_vorhanden"]
    assert len(rowboat.kanaele("todo:addSuggestions")) == 1


def test_abgelehnter_punkt_wird_nicht_eingestellt(buch, rowboat):
    rowboat.signale.append({"kind": "dismissed", "key": f"[gehirn] alt · #{IDS[0]}", "at": "x"})
    auftrag = buch.uebernommen({"punkte": _punkte()})
    assert _bearbeiten(buch, rowboat, auftrag) == "fertig"
    assert _staende(buch, auftrag) == ["frueher_abgelehnt", "neu", "neu"]
    (einstellen,) = rowboat.kanaele("todo:addSuggestions")
    assert [kennung_in(t) for t in einstellen["texts"]] == list(IDS[1:])


def test_doppelte_kennung_steht_je_eingabe_im_ergebnis(buch, rowboat):
    punkte = _punkte(ids=(IDS[0], IDS[0]), titel=("A", "B"))
    auftrag = buch.uebernommen({"punkte": punkte})
    assert _bearbeiten(buch, rowboat, auftrag) == "fertig"
    assert _stand(buch, auftrag)["ergebnis"]["punkte"] == [{"punkt_id": IDS[0], "stand": "neu"}] * 2
    (einstellen,) = rowboat.kanaele("todo:addSuggestions")
    assert len(einstellen["texts"]) == 1


@pytest.mark.parametrize("kanal", ["todo:get", "todo:plannerSignals", "todo:addSuggestions"])
def test_rowboat_aus_fehler_fester_grund(buch, rowboat, kanal):
    rowboat.fehler[kanal] = GRUND_NICHT_ERREICHBAR
    auftrag = buch.uebernommen({"punkte": _punkte()})
    assert _bearbeiten(buch, rowboat, auftrag) == "fehler"
    a = _stand(buch, auftrag)
    assert (a["stand"], a["fehler"]) == ("fehler", "Rowboat nicht erreichbar")
    assert a["ergebnis"] is None


def test_schluessel_abgelehnt_fehler(buch, rowboat):
    rowboat.fehler["todo:addSuggestions"] = GRUND_ABGELEHNT
    auftrag = buch.uebernommen({"punkte": _punkte()})
    assert _bearbeiten(buch, rowboat, auftrag) == "fehler"
    assert _stand(buch, auftrag)["fehler"] == "Rowboat: Schlüssel abgelehnt"


@pytest.mark.parametrize("antwort", [[], {"list": {"blocks": "x"}, "suggestions": []},
                                     {"list": {"blocks": []}, "suggestions": [1]}])
def test_unerwartete_form_von_todo_get_fehler(buch, rowboat, antwort, monkeypatch):
    echt = rowboat.aufruf
    monkeypatch.setattr(rowboat, "aufruf", lambda k, b: antwort if k == "todo:get" else echt(k, b))
    auftrag = buch.uebernommen({"punkte": _punkte()})
    assert _bearbeiten(buch, rowboat, auftrag) == "fehler"
    assert _stand(buch, auftrag)["fehler"] == "Rowboat: unerwartete Antwort"


@pytest.mark.parametrize("art, version, arten", [
    ("vorschlaege_anlegen", 1, ARTEN),
    ("vorschlaege_anlegen", 3, ARTEN),
    ("unbekannt_art", 2, ARTEN),
    ("vorschlaege_anlegen", 2, {}),  # nicht freigeschaltet
])
def test_unbekannte_version_fehler(buch, rowboat, art, version, arten):
    auftrag = buch.uebernommen({"punkte": _punkte()}, art=art, version=version)
    assert _bearbeiten(buch, rowboat, auftrag, arten) == "fehler"
    assert _stand(buch, auftrag)["fehler"] == "Unbekannte Auftragsart oder Version"
    assert rowboat.aufrufe == []


def test_eingabe_gegen_den_vertrag(buch, rowboat):
    punkte = _punkte()
    punkte[0]["link"] = "javascript:alert(1)"
    auftrag = buch.uebernommen({"punkte": punkte})
    assert _bearbeiten(buch, rowboat, auftrag) == "fehler"
    assert _stand(buch, auftrag)["fehler"] == "Eingabe passt nicht zum Vertrag"
    assert rowboat.aufrufe == []


def test_rowboat_schluckt_punkt_fehler(buch, rowboat):
    rowboat.schluckt = {IDS[1]}
    auftrag = buch.uebernommen({"punkte": _punkte()})
    assert _bearbeiten(buch, rowboat, auftrag) == "fehler"
    a = _stand(buch, auftrag)
    assert (a["stand"], a["fehler"]) == ("fehler", "Rowboat hat einen Punkt nicht angenommen")
    assert len(rowboat.kanaele("todo:get")) == 2  # genau einmal neu gelesen
    assert len(rowboat.kanaele("todo:addSuggestions")) == 1


def test_verschwiegener_punkt_beim_neu_lesen_gefunden(buch, rowboat):
    rowboat.verschweigt = {IDS[2]}
    auftrag = buch.uebernommen({"punkte": _punkte()})
    assert _bearbeiten(buch, rowboat, auftrag) == "fertig"
    assert _staende(buch, auftrag) == ["neu", "neu", "schon_vorhanden"]
    assert len(rowboat.kanaele("todo:get")) == 2


def test_abschluss_zu_spaet_kein_absturz(buch, rowboat, caplog):
    buch.abschluss_geht = False
    auftrag = buch.uebernommen({"punkte": _punkte()})
    with caplog.at_level(logging.INFO, logger="auftragsbuch"):
        assert _bearbeiten(buch, rowboat, auftrag) == "fehler"
    assert buch.fehler_aufrufe == []  # nur ein Logeintrag
    assert [r.getMessage() for r in caplog.records if "abschluss" in r.getMessage()] == [
        f"auftrag={auftrag['id'][:8]} art=vorschlaege_anlegen abschluss=abgelehnt"]
    # Der Abholer starb gleichsam nach dem Einstellen: dieselben Punkte stehen beim naechsten Mal nicht doppelt.
    buch.abschluss_geht = True
    naechster = buch.uebernommen({"punkte": _punkte()})
    assert _bearbeiten(buch, rowboat, naechster) == "fertig"
    assert _staende(buch, naechster) == ["schon_vorhanden"] * 3
    assert len(rowboat.vorschlaege) == 3


def test_log_ohne_titel(buch, rowboat, caplog):
    with caplog.at_level(logging.DEBUG, logger="auftragsbuch"):
        logging.getLogger("auftragsbuch").info(abholer.startzeile(LADEN, ARTEN))
        _bearbeiten(buch, rowboat, buch.uebernommen({"punkte": _punkte()}))
        rowboat.schluckt = {"aaaaaaaaaaaaaaaa"}
        _bearbeiten(buch, rowboat, buch.uebernommen({"punkte": _punkte(ids=("a" * 16,))}))
        rowboat.fehler["todo:get"] = GRUND_ABGELEHNT
        _bearbeiten(buch, rowboat, buch.uebernommen({"punkte": _punkte()}))
        buch.abschluss_geht = False
        del rowboat.fehler["todo:get"]
        _bearbeiten(buch, rowboat, buch.uebernommen({"punkte": _punkte()}))
    zeilen = [r.getMessage() for r in caplog.records]
    assert len(zeilen) >= 5
    for zeile in zeilen:
        assert not any(t in zeile for t in TITEL), zeile
        assert "beispiel.invalid" not in zeile and "geheimer-pfad" not in zeile and "Gehirn]" not in zeile, zeile
        assert not any(i in zeile for i in IDS), zeile
        for kennung in buch.auftraege:
            assert kennung not in zeile, zeile  # nur die gekuerzte Auftragsnummer


# --- Durchgang und Schleife -------------------------------------------------------------------


def test_durchgang_holt_und_bearbeitet(buch, rowboat):
    auftrag = buch.anlegen({"punkte": _punkte()})
    assert abholer.durchgang(buch, rowboat, ARTEN, LADEN) == "fertig"
    assert _stand(buch, auftrag)["stand"] == "fertig"
    assert abholer.durchgang(buch, rowboat, ARTEN, LADEN) is None


def test_durchgang_ohne_freie_arten_holt_nichts(buch, rowboat):
    auftrag = buch.anlegen({"punkte": _punkte()})
    assert abholer.durchgang(buch, rowboat, {}, LADEN) is None
    assert _stand(buch, auftrag)["stand"] == "offen"


def test_durchgang_unerwarteter_fehler_kein_absturz(buch, rowboat, monkeypatch, caplog):
    auftrag = buch.anlegen({"punkte": _punkte()})
    monkeypatch.setattr(abholer, "bearbeiten", lambda *a, **k: 1 / 0)
    with caplog.at_level(logging.INFO, logger="auftragsbuch"):
        assert abholer.durchgang(buch, rowboat, ARTEN, LADEN) == "fehler"
    assert _stand(buch, auftrag)["fehler"] == "Abholer: interner Fehler"
    assert any("ZeroDivisionError" in r.getMessage() for r in caplog.records)


def test_lauf_schreibt_herzschlag_und_ueberlebt_die_datenbank(rowboat, tmp_path, caplog):
    herz = tmp_path / "herz"
    runden = {"n": 0}

    class Wackelig(FalschesBuch):
        def naechster(self, laden, lease):
            runden["n"] += 1
            if runden["n"] == 1:
                raise OSError("host=geheim password=x")
            if runden["n"] >= 3:
                stopp.set()
            return None

    import threading
    stopp = threading.Event()
    with caplog.at_level(logging.INFO, logger="auftragsbuch"):
        abholer.lauf(Wackelig(), rowboat, ARTEN, LADEN, takt_s=0.01, herzschlag=herz, stopp=stopp)
    assert runden["n"] >= 3 and herz.exists()
    meldungen = " ".join(r.getMessage() for r in caplog.records)
    assert "OSError" in meldungen and "geheim" not in meldungen and "password" not in meldungen


def test_gesund_nach_herzschlag(tmp_path):
    herz = tmp_path / "herz"
    assert abholer.gesund(herz) is False
    herz.write_text("", encoding="utf-8")
    assert abholer.gesund(herz) is True
    alt = time.time() - 120
    os.utime(herz, (alt, alt))
    assert abholer.gesund(herz) is False


# --- Start ------------------------------------------------------------------------------------


def test_erste_logzeile_nennt_laden_arten_und_hash():
    zeile = abholer.startzeile(LADEN, ARTEN)
    assert zeile == f"abholer=start laden={LADEN} arten=vorschlaege_anlegen:{art_hash(ART)[:8]}"
    assert abholer.startzeile(LADEN, {}) == f"abholer=start laden={LADEN} arten=-"


def test_freie_arten_vorgabe_leer_und_unbekannt():
    assert abholer.freie_arten(ALLE, None) == ARTEN
    assert abholer.freie_arten(ALLE, "vorschlaege_anlegen") == ARTEN
    assert abholer.freie_arten(ALLE, " ") == {}
    with pytest.raises(ValueError, match="ABHOLER_ARTEN_FREI"):
        abholer.freie_arten(ALLE, "vorschlaege_anlegen,unbekannt_art")


@pytest.fixture
def umgebung(tmp_path) -> dict:
    (tmp_path / "dsn").write_text("host=geheim password=x\n", encoding="utf-8")
    (tmp_path / "schluessel").write_text("rowboat-schluessel-42\n", encoding="utf-8")
    return {"ABHOLER_DSN_FILE": str(tmp_path / "dsn"), "ABHOLER_LADEN": LADEN,
            "ROWBOAT_RPC_URL": "http://127.0.0.1:3220", "ROWBOAT_SCHLUESSEL_DATEI": str(tmp_path / "schluessel")}


def _kein_pool(monkeypatch):
    import psycopg_pool

    def verboten(*_a, **_k):
        raise AssertionError("ohne gueltige Einstellung keine Datenbank")
    monkeypatch.setattr(psycopg_pool, "ConnectionPool", verboten)


@pytest.mark.parametrize("wert", [None, "", "  "])
def test_start_ohne_laden_bricht_ab(umgebung, monkeypatch, wert):
    _kein_pool(monkeypatch)
    if wert is None:
        del umgebung["ABHOLER_LADEN"]
    else:
        umgebung["ABHOLER_LADEN"] = wert
    with pytest.raises(SystemExit) as e:
        abholer.main(umgebung, [])
    assert str(e.value) == "rowboat-abholer: ABHOLER_LADEN fehlt"
    assert e.value.__cause__ is None and e.value.__suppress_context__


@pytest.mark.parametrize("name", ["ABHOLER_DSN_FILE", "ROWBOAT_RPC_URL", "ROWBOAT_SCHLUESSEL_DATEI"])
def test_start_nennt_jede_fehlende_variable_ohne_wert(umgebung, monkeypatch, name):
    _kein_pool(monkeypatch)
    del umgebung[name]
    with pytest.raises(SystemExit) as e:
        abholer.main(umgebung, [])
    assert name in str(e.value)
    assert "geheim" not in str(e.value) and "rowboat-schluessel-42" not in str(e.value)


def test_start_laden_muss_zum_auftragsbuch_passen(umgebung, monkeypatch):
    _kein_pool(monkeypatch)
    umgebung["AUFTRAGSBUCH_LADEN"] = "anderer-laden"
    with pytest.raises(SystemExit) as e:
        abholer.main(umgebung, [])
    assert str(e.value) == "rowboat-abholer: ABHOLER_LADEN weicht von AUFTRAGSBUCH_LADEN ab"


def test_start_schluessel_nie_in_meldung(umgebung, monkeypatch, tmp_path):
    _kein_pool(monkeypatch)
    umgebung["ROWBOAT_RPC_URL"] = "http://beispiel.invalid:3220"
    with pytest.raises(SystemExit) as e:
        abholer.main(umgebung, [])
    assert str(e.value) == "rowboat-abholer: Rowboat-URL ungueltig"


def test_gesund_als_kommando(tmp_path):
    herz = tmp_path / "herz"
    assert abholer.main({"ABHOLER_HERZSCHLAG_DATEI": str(herz)}, ["--gesund"]) == 1
    herz.write_text("", encoding="utf-8")
    assert abholer.main({"ABHOLER_HERZSCHLAG_DATEI": str(herz)}, ["--gesund"]) == 0


# --- Datenbank-Schicht ------------------------------------------------------------------------


class _Ergebnis:
    def __init__(self, zeile):
        self._zeile = zeile

    def fetchone(self):
        return self._zeile


class _Verbindung:
    def __init__(self, protokoll, antworten):
        self._protokoll, self._antworten = protokoll, antworten

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def execute(self, sql, parameter):
        self._protokoll.append((sql, parameter))
        return _Ergebnis(self._antworten.pop(0))


class _Pool:
    def __init__(self, *antworten):
        self.protokoll: list = []
        self._antworten = list(antworten)

    def connection(self):
        return _Verbindung(self.protokoll, self._antworten)


def test_empfang_ruft_nur_die_vier_huellen_mit_parametern():
    auftrag = {"id": "x", "art": "vorschlaege_anlegen"}
    pool = _Pool((auftrag,), (None,), (True,), (False,), (["0123456789abcdef"],))
    e = PgEmpfang(pool)
    assert e.naechster(LADEN, timedelta(minutes=2)) == auftrag
    assert e.naechster(LADEN, timedelta(minutes=2)) is None
    assert e.abschliessen("id-1", {"punkte": []}, LADEN) is True
    assert e.fehler("id-1", "Grund", LADEN) is False
    assert e.bekannt(LADEN, ["0123456789abcdef", "fedcba9876543210"]) == {"0123456789abcdef"}
    sqls = [s for s, _p in pool.protokoll]
    assert sqls == [
        "select auftrag.rowboat_empfang_naechster(%s::text, %s::interval)",
        "select auftrag.rowboat_empfang_naechster(%s::text, %s::interval)",
        "select auftrag.rowboat_empfang_abschliessen(%s::uuid, %s::jsonb, %s::text)",
        "select auftrag.rowboat_empfang_fehler(%s::uuid, %s::text, %s::text)",
        "select auftrag.rowboat_empfang_bekannt(%s::text, %s::text[])",
    ]
    assert pool.protokoll[0][1] == (LADEN, timedelta(minutes=2))
    assert pool.protokoll[3][1] == ("id-1", "Grund", LADEN)
    assert pool.protokoll[4][1] == (LADEN, ["0123456789abcdef", "fedcba9876543210"])


def test_empfang_bekannt_in_stuecken_und_leer():
    ids = [f"{i:016x}" for i in range(BEKANNT_MAX + 3)]
    pool = _Pool(([ids[0]],), (None,))
    assert PgEmpfang(pool).bekannt(LADEN, ids) == {ids[0]}
    assert [len(p[1]) for _s, p in pool.protokoll] == [BEKANNT_MAX, 3]
    leer = _Pool()
    assert PgEmpfang(leer).bekannt(LADEN, []) == set() and leer.protokoll == []
