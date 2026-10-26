"""Tests fuer den naechtlichen Geschaeftsdaten-Lauf (Teil B, Task 9)."""
from __future__ import annotations

import json
import logging
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
OHNE_PAUSE = {"schlafen": lambda s: None}


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
    werte = dict(heute=HEUTE, trocken=False, wochenbild_neu=False, ablage_optionen=OHNE_PAUSE)
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
    monkeypatch.setattr(lauf_mod, "ABLAGE_OPTIONEN", OHNE_PAUSE)
    assert lauf_mod.main(["--heute", "2026-10-26"]) == 0
    assert container == ["debian-supabase-db-1"]
    daten = json.loads(stand.read_text(encoding="utf-8"))
    assert daten["ergebnis"] == "ok" and daten["exit_code"] == 0
    assert daten["je_firma"]["VibeMind"]["geschrieben"] == 3
    assert daten["uebersprungen"] == []
    assert [p.name for p in stand.parent.iterdir()] == ["stand.json"]  # kein Rest einer tmp-Datei


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
