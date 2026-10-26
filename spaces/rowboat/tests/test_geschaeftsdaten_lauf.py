"""Tests fuer den naechtlichen Geschaeftsdaten-Lauf (Teil B, Task 9)."""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "geschaeftsdaten"))

from geschaeftsdaten_fake import FakeQuelle, ereignis, inhalt, kontakt  # noqa: E402
from laeden import Laden  # noqa: E402
from quelle import Medium, Versand  # noqa: E402

import lauf as lauf_mod  # noqa: E402

UTC = timezone.utc
HEUTE = date(2026, 10, 26)
A, B, C, SAMMEL, GESPERRT = (f"aaaaaaaa-0000-4000-8000-{n:012d}" for n in range(1, 6))
NL_V, NL_F = "bbbbbbbb-0000-4000-8000-000000000001", "bbbbbbbb-0000-4000-8000-000000000002"
LAEDEN = [Laden("sales", "vibemind", frozenset({SAMMEL})), Laden("sales_ivan", "fin2gether", frozenset())]
OPTIONEN: dict = {}


def _quelle(**kw) -> FakeQuelle:
    werte = dict(
        marken={"vibemind": "VibeMind", "fin2gether": "fin2gether"},
        kontakte={
            "sales": [
                kontakt(id=A, name="Anna Alpha", email_klein="anna@alpha.de", bereiche=("bav",)),
                kontakt(id=SAMMEL, name="Sammelkontakt Inbox", bereiche=()),
                kontakt(id=C, name="System Konto", source="system", bereiche=()),
                kontakt(id=GESPERRT, gesperrt=True, name=None, status=None, bereiche=()),
            ],
            "sales_ivan": [kontakt(id=B, name="Bert Beta", bereiche=())],
        },
        ereignisse={"sales": [ereignis(lead_id=A, typ="kundenantwort",
                                       zeit=datetime(2026, 10, 21, 9, 0, tzinfo=UTC))]},
        versand={"sales": [Versand(NL_V, datetime(2026, 10, 22, 8, 0, tzinfo=UTC), 2),
                           Versand(NL_F, datetime(2026, 10, 22, 8, 0, tzinfo=UTC), 1)]},
        inhalte=[inhalt(id=NL_V, mandant="vibemind", felder={"betreff": "Neues im Oktober"}),
                 inhalt(id=NL_F, mandant="fin2gether", felder={"betreff": "Fremde Marke"})],
        medien=[Medium("reel", "vibemind", "Reel eins", 42.0, datetime(2026, 10, 23, 9, 0, tzinfo=UTC))],
    )
    werte.update(kw)
    return FakeQuelle(**werte)


def _wissen(tmp_path: Path) -> Path:
    wissen = tmp_path / "knowledge"
    (wissen / "companys" / "VibeMind").mkdir(parents=True)
    return wissen


def _lauf(quelle, wissen, **kw):
    werte = dict(heute=HEUTE, trocken=False, wochenbild_neu=False, ablage_optionen=OPTIONEN)
    werte.update(kw)
    return lauf_mod.lauf(quelle, LAEDEN, wissen, **werte)


def _alle(wissen: Path) -> dict[str, bytes]:
    return {p.relative_to(wissen).as_posix(): p.read_bytes() for p in wissen.rglob("*") if p.is_file()}


def test_ende_zu_ende_zwei_firmen(tmp_path):
    wissen = _wissen(tmp_path)
    erg = _lauf(_quelle(), wissen)
    assert erg.exit_code == 0 and erg.uebersprungen == []
    kontakte = sorted(r for r in _alle(wissen) if "/Kontakte/" in r)
    assert kontakte == ["companys/VibeMind/Kontakte/bAV/Anna Alpha.md",
                        "companys/fin2gether/Kontakte/_ohne/Bert Beta.md"]
    text = (wissen / "companys/VibeMind/Kontakte/bAV/Anna Alpha.md").read_text(encoding="utf-8")
    assert "laden: sales" in text and "anna@alpha.de" not in text
    assert (wissen / "companys/VibeMind/Newsletter/2026-10-22 Neues im Oktober.md").is_file()
    assert (wissen / "companys/VibeMind/Wochenbild/2026-KW43.md").is_file()
    assert erg.je_firma["VibeMind"]["geschrieben"] == 3
    assert erg.je_firma["fin2gether"]["geschrieben"] == 2  # Steckbrief + Wochenbild


def test_zweimal_hintereinander_null_schreibungen(tmp_path):
    wissen = _wissen(tmp_path)
    _lauf(_quelle(), wissen)
    vorher = _alle(wissen)
    erg = _lauf(_quelle(), wissen)
    assert all(z["geschrieben"] == 0 and z["verschoben"] == 0 and z["archiviert"] == 0 and z["entfernt"] == 0
               for z in erg.je_firma.values())
    assert _alle(wissen) == vorher


def test_laden_ohne_sichten_uebersprungen_exit_1(tmp_path):
    wissen = _wissen(tmp_path)
    erg = _lauf(_quelle(ohne_sichten=("sales_ivan",)), wissen)
    assert erg.exit_code == 1 and erg.uebersprungen == ["sales_ivan"]
    assert (wissen / "companys/VibeMind/Kontakte/bAV/Anna Alpha.md").is_file()
    assert not (wissen / "companys/fin2gether").exists()


def test_dbfehler_vor_schreiben_exit_2_nichts_geschrieben(tmp_path):
    wissen = _wissen(tmp_path)

    class SpaeterFehler(FakeQuelle):
        def medien(self, von, bis):  # letzte Lesung scheitert
            from quelle import DbFehler
            raise DbFehler("psql Exit 2")

    q = _quelle()
    q.__class__ = SpaeterFehler
    erg = _lauf(q, wissen)
    assert erg.exit_code == 2
    assert _alle(wissen) == {}
    erg = _lauf(_quelle(fehler=True), wissen)
    assert erg.exit_code == 2 and _alle(wissen) == {}


def test_wochenbild_nur_wenn_fehlt(tmp_path):
    wissen = _wissen(tmp_path)
    _lauf(_quelle(), wissen)
    pfad = wissen / "companys/VibeMind/Wochenbild/2026-KW43.md"
    erst = pfad.read_bytes()
    anders = _quelle(medien=[])
    _lauf(anders, wissen)
    assert pfad.read_bytes() == erst
    _lauf(anders, wissen, wochenbild_neu=True)
    assert pfad.read_bytes() != erst and b"Reel eins" not in pfad.read_bytes()


def test_kontakt_verschwindet_ins_archiv_und_gesperrt_wird_entfernt(tmp_path):
    wissen = _wissen(tmp_path)
    _lauf(_quelle(), wissen)
    q = _quelle()
    q.kontakte_["sales"] = [kontakt(id=A, gesperrt=True, name=None, bereiche=())]
    q.kontakte_["sales_ivan"] = []
    erg = _lauf(q, wissen)
    assert not list((wissen / "companys/VibeMind/Kontakte").rglob("*.md"))
    assert [p.name for p in (wissen / "companys/fin2gether/Kontakte/_archiv").glob("*.md")] == ["Bert Beta.md"]
    assert erg.je_firma["VibeMind"]["entfernt"] == 1 and erg.je_firma["fin2gether"]["archiviert"] == 1


def test_empfohlen_von_link_in_derselben_firma(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.kontakte_["sales"].append(kontakt(id=C.replace("3", "6"), name="Clara Empfohlen", bereiche=("bav",),
                                        source="empfehlung"))
    q.ereignisse_["sales"].append(ereignis(lead_id=C.replace("3", "6"), typ="empfehlung",
                                           empfohlen_von_lead=A, empfohlen_von_name="Anna Alpha"))
    q.ereignisse_["sales"].append(ereignis(lead_id=A, typ="empfehlung", empfohlen_von_lead=B,
                                           empfohlen_von_name="Bert Beta"))
    _lauf(q, wissen)
    clara = (wissen / "companys/VibeMind/Kontakte/bAV/Clara Empfohlen.md").read_text(encoding="utf-8")
    assert "[[companys/VibeMind/Kontakte/bAV/Anna Alpha]]" in clara
    anna = (wissen / "companys/VibeMind/Kontakte/bAV/Anna Alpha.md").read_text(encoding="utf-8")
    assert "Empfohlen von:** Bert Beta" in anna and "[[" not in anna.split("Empfohlen von")[1].splitlines()[0]


def test_zu_langer_pfad_ueberspringt_nur_den_kontakt(tmp_path, monkeypatch):
    wissen = _wissen(tmp_path)
    echt = lauf_mod.relpfad

    def relpfad(ordner, stamm, endung=".md"):
        if stamm.startswith("Anna"):
            raise ValueError("Pfad zu lang")
        return echt(ordner, stamm, endung)

    monkeypatch.setattr(lauf_mod, "relpfad", relpfad)
    erg = _lauf(_quelle(), wissen)
    assert erg.exit_code == 1 and erg.je_firma["VibeMind"]["uebersprungen"] == 1
    assert (wissen / "companys/fin2gether/Kontakte/_ohne/Bert Beta.md").is_file()


def test_protokoll_ohne_personendaten(tmp_path, caplog):
    wissen = _wissen(tmp_path)
    caplog.set_level(logging.DEBUG)
    q = _quelle(ohne_sichten=("sales_ivan",))
    _lauf(q, wissen)
    _lauf(q, wissen)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert text  # es wird protokolliert
    for verboten in ("Anna", "Alpha", "anna@alpha.de", "Bert", "Kontakte/", "Sammelkontakt", "Neues im Oktober"):
        assert verboten not in text


def test_stand_json(tmp_path, monkeypatch):
    wissen = _wissen(tmp_path)
    sales_claw = tmp_path / "sales-claw"
    (sales_claw / "deploy" / "laeden").mkdir(parents=True)
    (sales_claw / ".env").write_text(f"MARKETING_MANDANT=vibemind\nINBOX_UNBEKANNT_LEAD_ID={SAMMEL}\n",
                                     encoding="utf-8")
    (sales_claw / "deploy" / "laeden" / "ivan.env").write_text(
        "SALES_DB_SCHEMA=sales_ivan\nLADEN_MARKE=fin2gether\n", encoding="utf-8")
    stand = tmp_path / "zustand" / "stand.json"
    monkeypatch.setenv("GESCHAEFTSDATEN_WISSEN", str(wissen))
    monkeypatch.setenv("GESCHAEFTSDATEN_SALES_CLAW", str(sales_claw))
    monkeypatch.setenv("GESCHAEFTSDATEN_STAND", str(stand))
    container: list[str] = []
    monkeypatch.setattr(lauf_mod, "PsqlQuelle", lambda c: container.append(c) or _quelle())
    assert lauf_mod.main(["--heute", "2026-10-26"]) == 0
    assert container == ["debian-supabase-db-1"]
    daten = json.loads(stand.read_text(encoding="utf-8"))
    assert daten["ergebnis"] == "ok" and daten["exit_code"] == 0
    assert daten["je_firma"]["VibeMind"]["geschrieben"] == 3
    assert daten["uebersprungen"] == []
    assert [p.name for p in stand.parent.iterdir()] == ["stand.json"]  # kein Rest einer tmp-Datei
    assert daten["gitignore"] == "angelegt"


def test_trocken_schreibt_weder_wissen_noch_stand(tmp_path, monkeypatch):
    wissen = _wissen(tmp_path)
    sales_claw = tmp_path / "sales-claw"
    sales_claw.mkdir()
    (sales_claw / ".env").write_text("MARKETING_MANDANT=vibemind\n", encoding="utf-8")
    stand = tmp_path / "stand.json"
    monkeypatch.setenv("GESCHAEFTSDATEN_WISSEN", str(wissen))
    monkeypatch.setenv("GESCHAEFTSDATEN_SALES_CLAW", str(sales_claw))
    monkeypatch.setenv("GESCHAEFTSDATEN_STAND", str(stand))
    monkeypatch.setattr(lauf_mod, "PsqlQuelle", lambda c: _quelle())
    assert lauf_mod.main(["--trocken", "--heute", "2026-10-26"]) == 0
    assert _alle(wissen) == {} and not stand.exists()


def test_newsletter_nur_eigene_marke(tmp_path):
    wissen = _wissen(tmp_path)
    _lauf(_quelle(), wissen)
    alle = _alle(wissen)
    assert not [r for r in alle if "Fremde Marke" in r]
    assert not (wissen / "companys/fin2gether/Newsletter").exists()
    wb = (wissen / "companys/VibeMind/Wochenbild/2026-KW43.md").read_text(encoding="utf-8")
    assert "[[companys/VibeMind/Newsletter/2026-10-22 Neues im Oktober]] – 2 Empfänger" in wb
    assert "Reel eins" in wb
    assert "Reel eins" not in (wissen / "companys/fin2gether/Wochenbild/2026-KW43.md").read_text(encoding="utf-8")


def test_lange_gleiche_namen_bekommen_eigene_pfade(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    lang = "Sehr langer Name " * 10
    q.kontakte_["sales"] = [kontakt(id=A, name=lang + "eins", bereiche=("bav",)),
                            kontakt(id=C.replace("3", "7"), name=lang + "zwei", bereiche=("bav",))]
    _lauf(q, wissen)
    namen = sorted(p.name for p in (wissen / "companys/VibeMind/Kontakte/bAV").glob("*.md"))
    assert len(namen) == 2 and len({n.casefold() for n in namen}) == 2


def test_belegter_endpfad_ueberspringt_den_zweiten(tmp_path, monkeypatch):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.kontakte_["sales"].append(kontakt(id=C.replace("3", "7"), name="Zweite Anna", bereiche=("bav",)))
    monkeypatch.setattr(lauf_mod, "relpfad", lambda ordner, stamm, endung=".md": f"{ordner}/Gleich{endung}")
    erg = _lauf(q, wissen)
    assert erg.je_firma["VibeMind"]["uebersprungen"] == 1 and erg.exit_code == 1
    assert [p.name for p in (wissen / "companys/VibeMind/Kontakte/bAV").glob("*.md")] == ["Gleich.md"]


def _kontakte(wissen: Path, firma: str) -> list[str]:
    return sorted(p.relative_to(wissen / "companys" / firma).as_posix()
                  for p in (wissen / "companys" / firma / "Kontakte").rglob("*.md"))


def _id(n: int) -> str:
    return f"cccccccc-0000-4000-8000-{n:012d}"


def test_ein_laden_ohne_sichten_sperrt_die_ganze_firma(tmp_path):
    wissen = _wissen(tmp_path)
    laeden = LAEDEN + [Laden("sales_zwei", "vibemind", frozenset())]
    lauf_mod.lauf(_quelle(), laeden, wissen, heute=HEUTE, trocken=False, wochenbild_neu=False, ablage_optionen={})
    vorher = _alle(wissen)
    q = _quelle(ohne_sichten=("sales_zwei",))
    q.kontakte_["sales"] = []  # waere ohne Schutz: Anna ins Archiv
    erg = lauf_mod.lauf(q, laeden, wissen, heute=HEUTE, trocken=False, wochenbild_neu=False, ablage_optionen={})
    assert erg.exit_code == 1 and "VibeMind" not in erg.je_firma and "fin2gether" in erg.je_firma
    assert "sales_zwei" in erg.uebersprungen and "Marke vibemind" in erg.uebersprungen
    assert {k: v for k, v in _alle(wissen).items() if "VibeMind" in k} == \
        {k: v for k, v in vorher.items() if "VibeMind" in k}


def test_gestoerte_marke_aus_der_laden_konfiguration(tmp_path):
    wissen = _wissen(tmp_path)
    erg = _lauf(_quelle(), wissen, gestoert=frozenset({"vibemind"}))
    assert erg.exit_code == 1 and list(erg.je_firma) == ["fin2gether"]
    assert not (wissen / "companys/VibeMind/Kontakte").exists()
    erg = _lauf(_quelle(), wissen, gestoert=frozenset({"*"}))
    assert erg.exit_code == 1 and erg.je_firma == {}


def test_gesperrt_vor_sammel_und_system(tmp_path):
    wissen = _wissen(tmp_path)
    _lauf(_quelle(), wissen)
    q = _quelle()
    q.kontakte_["sales"][0] = kontakt(id=A, gesperrt=True, name=None, source="system", bereiche=())
    erg = _lauf(q, wissen)
    assert erg.je_firma["VibeMind"]["entfernt"] == 1 and erg.je_firma["VibeMind"]["archiviert"] == 0
    assert _kontakte(wissen, "VibeMind") == []


def test_obergrenze_je_lauf_gesperrte_zuerst(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.kontakte_["sales_ivan"] = [kontakt(id=B, name="Bert Beta", bereiche=()),
                                 kontakt(id=_id(1), name="Berta Zwei", bereiche=())]
    _lauf(q, wissen)
    q.kontakte_["sales_ivan"] = [kontakt(id=B, name="Bert Beta", bereiche=("bav",)),
                                 kontakt(id=_id(1), name="Berta Zwei", bereiche=("bav",))]
    q.kontakte_["sales"][0] = kontakt(id=A, gesperrt=True, name=None, bereiche=())
    erg = _lauf(q, wissen, ablage_optionen={"paket": 1})
    assert erg.je_firma["VibeMind"]["entfernt"] == 1  # fin2gether kommt alphabetisch zuerst, trotzdem
    assert erg.je_firma["fin2gether"]["verschoben"] == 0 and erg.je_firma["fin2gether"]["zurueckgestellt"] == 2
    erg = _lauf(q, wissen, ablage_optionen={"paket": 1})
    assert erg.je_firma["fin2gether"]["verschoben"] == 1
    assert _kontakte(wissen, "fin2gether") == ["Kontakte/_ohne/Berta Zwei.md", "Kontakte/bAV/Bert Beta.md"]


GI_BLOCK = "# rowboat-geschaeftsdaten: Anfang"


def test_gitignore_wird_angelegt_und_gepflegt(tmp_path):
    wissen = _wissen(tmp_path)
    gi = wissen / ".gitignore"
    erg = _lauf(_quelle(), wissen)
    text = gi.read_text(encoding="utf-8")
    assert erg.gitignore == "angelegt" and GI_BLOCK in text and "companys/*/Kontakte/\n" in text
    erg = _lauf(_quelle(), wissen)
    assert erg.gitignore == "aktuell" and gi.read_text(encoding="utf-8") == text
    # eigene Datei mit Ergaenzung des Betreibers: Ergaenzung bleibt, Block wird repariert
    gi.write_text("meins/\n" + text.replace("companys/*/Kontakte/", "veraltet/"), encoding="utf-8")
    erg = _lauf(_quelle(), wissen)
    neu = gi.read_text(encoding="utf-8")
    assert erg.gitignore == "ergaenzt" and neu.startswith("meins/\n") and "companys/*/Kontakte/\n" in neu
    assert "veraltet/" not in neu


def test_fremde_gitignore_bleibt_unberuehrt(tmp_path, caplog):
    wissen = _wissen(tmp_path)
    gi = wissen / ".gitignore"
    gi.write_text("node_modules/\n", encoding="utf-8")
    caplog.set_level(logging.WARNING)
    erg = _lauf(_quelle(), wissen)
    assert erg.gitignore == "fremd" and gi.read_text(encoding="utf-8") == "node_modules/\n"
    assert any(".gitignore" in r.getMessage() for r in caplog.records)
    assert erg.je_firma["VibeMind"]["geschrieben"] == 3  # nur Warnung, Lauf geht weiter


def test_trocken_legt_keine_gitignore_an(tmp_path):
    wissen = _wissen(tmp_path)
    _lauf(_quelle(), wissen, trocken=True)
    assert not (wissen / ".gitignore").exists()


def test_oserror_in_einer_firma_trifft_nur_diese(tmp_path, monkeypatch, caplog):
    wissen = _wissen(tmp_path)
    echt = lauf_mod.bestand

    def bestand(firma):
        if firma.name == "VibeMind":
            raise PermissionError(13, "kein Zugriff", str(firma / "Kontakte/bAV/Anna Alpha.md"))
        return echt(firma)

    monkeypatch.setattr(lauf_mod, "bestand", bestand)
    caplog.set_level(logging.DEBUG)
    erg = _lauf(_quelle(), wissen)
    assert erg.exit_code == 1 and "VibeMind" not in erg.je_firma
    assert erg.je_firma["fin2gether"]["geschrieben"] == 2
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "PermissionError" in text and "Anna" not in text and "Kontakte/" not in text


def test_stand_json_auch_bei_absturz(tmp_path, monkeypatch):
    wissen = _wissen(tmp_path)
    sales_claw = tmp_path / "sales-claw"
    sales_claw.mkdir()
    (sales_claw / ".env").write_text("MARKETING_MANDANT=vibemind\n", encoding="utf-8")
    stand = tmp_path / "zustand" / "stand.json"
    monkeypatch.setenv("GESCHAEFTSDATEN_WISSEN", str(wissen))
    monkeypatch.setenv("GESCHAEFTSDATEN_SALES_CLAW", str(sales_claw))
    monkeypatch.setenv("GESCHAEFTSDATEN_STAND", str(stand))
    monkeypatch.setattr(lauf_mod, "PsqlQuelle", lambda c: _quelle())

    def kaputt(*a, **kw):
        raise RuntimeError("Anna Alpha")

    monkeypatch.setattr(lauf_mod, "lauf", kaputt)
    assert lauf_mod.main(["--heute", "2026-10-26"]) == 1
    daten = json.loads(stand.read_text(encoding="utf-8"))
    assert daten["ergebnis"] == "fehler" and daten["fehler"] == "RuntimeError"
    assert "Anna" not in stand.read_text(encoding="utf-8")


def test_stand_ordner_privat(tmp_path, monkeypatch):
    wissen = _wissen(tmp_path)
    sales_claw = tmp_path / "sales-claw"
    sales_claw.mkdir()
    (sales_claw / ".env").write_text("MARKETING_MANDANT=vibemind\n", encoding="utf-8")
    stand = tmp_path / "zustand" / "stand.json"
    monkeypatch.setenv("GESCHAEFTSDATEN_WISSEN", str(wissen))
    monkeypatch.setenv("GESCHAEFTSDATEN_SALES_CLAW", str(sales_claw))
    monkeypatch.setenv("GESCHAEFTSDATEN_STAND", str(stand))
    monkeypatch.setattr(lauf_mod, "PsqlQuelle", lambda c: _quelle())
    echt, aufrufe = os.chmod, []

    def chmod(pfad, modus, *a, **kw):
        aufrufe.append((Path(pfad), modus))
        return echt(pfad, modus, *a, **kw)

    monkeypatch.setattr(lauf_mod.os, "chmod", chmod)
    lauf_mod.main(["--heute", "2026-10-26"])
    assert (stand.parent, 0o700) in aufrufe
    if os.name == "posix":
        assert stand.parent.stat().st_mode & 0o777 == 0o700


def test_alte_zwischendateien_werden_aufgeraeumt(tmp_path):
    wissen = _wissen(tmp_path)
    rest = wissen / "companys/VibeMind/Kontakte/bAV/.Alt.md.gd.tmp"
    rest.parent.mkdir(parents=True)
    rest.write_text("x", encoding="utf-8")
    _lauf(_quelle(), wissen)
    assert not rest.exists()


def test_massenarchivierung_wird_gestoppt(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    viele = [kontakt(id=_id(i), name=f"Person {i:02d}", bereiche=("bav",)) for i in range(10)]
    q.kontakte_["sales"] = [*viele, kontakt(id=A, name="Anna Alpha", bereiche=("bav",))]
    _lauf(q, wissen)
    q.kontakte_["sales"] = [*viele[:4], kontakt(id=A, gesperrt=True, name=None, bereiche=())]  # 6 weg, Anna gesperrt
    erg = _lauf(q, wissen)
    assert erg.exit_code == 1 and "Massenarchivierung VibeMind" in erg.uebersprungen
    z = erg.je_firma["VibeMind"]
    assert z["archiviert"] == 0 and z["entfernt"] == 1  # Datenschutz geht trotzdem vor
    assert len(_kontakte(wissen, "VibeMind")) == 10
    assert not (wissen / "companys/VibeMind/Kontakte/_archiv").exists()


def test_wenige_archivierungen_sind_erlaubt(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.kontakte_["sales_ivan"] = []  # fin2gether: 1 von 1 Steckbrief weg (100 %, aber unter der Mindestzahl)
    _lauf(_quelle(), wissen)
    erg = _lauf(q, wissen)
    assert erg.je_firma["fin2gether"]["archiviert"] == 1 and erg.exit_code == 0


def test_rowboat_im_namen_link_passt_zum_dateinamen(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    q.kontakte_["sales"][0] = kontakt(id=A, name="@rowboat Fan", bereiche=("bav",))
    q.kontakte_["sales"].append(kontakt(id=_id(1), name="Clara", bereiche=("bav",)))
    q.ereignisse_["sales"].append(ereignis(lead_id=_id(1), typ="empfehlung", empfohlen_von_lead=A,
                                           empfohlen_von_name="@rowboat Fan"))
    _lauf(q, wissen)
    namen = _kontakte(wissen, "VibeMind")
    assert "Kontakte/bAV/@ rowboat Fan.md" in namen
    clara = (wissen / "companys/VibeMind/Kontakte/bAV/Clara.md").read_text(encoding="utf-8")
    assert "[[companys/VibeMind/Kontakte/bAV/@ rowboat Fan]]" in clara
    assert "@rowboat" not in clara.lower()


def _massen_lage(tmp_path):
    wissen = _wissen(tmp_path)
    q = _quelle()
    viele = [kontakt(id=_id(i), name=f"Person {i:02d}", bereiche=("bav",)) for i in range(10)]
    q.kontakte_["sales"] = list(viele)
    _lauf(q, wissen)
    q.kontakte_["sales"] = viele[:4]
    return wissen, q


def test_massen_ok_gibt_einmalig_frei(tmp_path, caplog):
    wissen, q = _massen_lage(tmp_path)
    caplog.set_level(logging.INFO)
    erg = _lauf(q, wissen, massen_ok=frozenset({"vibemind"}))  # ohne Gross-/Kleinschreibung
    assert erg.je_firma["VibeMind"]["archiviert"] == 6 and "Massenarchivierung VibeMind" not in erg.uebersprungen
    freigabe = [r.getMessage() for r in caplog.records if "freigegeben" in r.getMessage()]
    assert freigabe == ["VibeMind: Massen-Waechter fuer diesen Lauf freigegeben"]
    assert all(r.levelno == logging.INFO for r in caplog.records if "freigegeben" in r.getMessage())


def test_massen_ok_gilt_nur_fuer_die_genannte_firma(tmp_path):
    wissen, q = _massen_lage(tmp_path)
    erg = _lauf(q, wissen, massen_ok=frozenset({"fin2gether"}))
    assert erg.je_firma["VibeMind"]["archiviert"] == 0 and "Massenarchivierung VibeMind" in erg.uebersprungen


def test_massen_ok_ueber_die_kommandozeile(tmp_path, monkeypatch):
    wissen, q = _massen_lage(tmp_path)
    sales_claw = tmp_path / "sales-claw"
    sales_claw.mkdir()
    (sales_claw / ".env").write_text("MARKETING_MANDANT=vibemind\n", encoding="utf-8")
    monkeypatch.setenv("GESCHAEFTSDATEN_WISSEN", str(wissen))
    monkeypatch.setenv("GESCHAEFTSDATEN_SALES_CLAW", str(sales_claw))
    monkeypatch.setenv("GESCHAEFTSDATEN_STAND", str(tmp_path / "stand" / "stand.json"))
    monkeypatch.setattr(lauf_mod, "PsqlQuelle", lambda c: q)
    gesehen = {}
    echt = lauf_mod.lauf

    def lauf(*a, **kw):
        gesehen.update(kw)
        return echt(*a, **kw)

    monkeypatch.setattr(lauf_mod, "lauf", lauf)
    lauf_mod.main(["--heute", "2026-10-26", "--massen-ok", "VibeMind", "--massen-ok", "fin2gether"])
    assert gesehen["massen_ok"] == frozenset({"VibeMind", "fin2gether"})
    assert len(list((wissen / "companys/VibeMind/Kontakte/_archiv").glob("*.md"))) == 6


def test_gitignore_ohne_endmarke_behaelt_fremde_zeilen(tmp_path, caplog):
    wissen = _wissen(tmp_path)
    gi = wissen / ".gitignore"
    gi.write_text("# rowboat-geschaeftsdaten: Anfang (alt)\ncompanys/*/Kontakte/\nmeins/\nauch-meins/\n",
                  encoding="utf-8")
    caplog.set_level(logging.WARNING)
    erg = _lauf(_quelle(), wissen)
    text = gi.read_text(encoding="utf-8")
    assert erg.gitignore == "ergaenzt"
    assert "meins/\nauch-meins/\n" in text and text.endswith("# rowboat-geschaeftsdaten: Ende\n")
    assert any("Endmarke" in r.getMessage() for r in caplog.records)
    erg = _lauf(_quelle(), wissen)
    assert erg.gitignore == "aktuell" and gi.read_text(encoding="utf-8") == text
