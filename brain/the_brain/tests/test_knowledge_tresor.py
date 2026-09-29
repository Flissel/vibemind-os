from datetime import datetime, timezone
from pathlib import Path

from core.knowledge.schema import Beleg, Dokument, Fakt
from core.knowledge.tresor import Tresor

T = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def dok(status="raw", deutung="", links=None):
    return Dokument(typ="bubble", id="a1b2c3d4", titel="Marketing", stand=T,
                    fakten=[Fakt(schluessel="status", wert=status, beleg=1)],
                    belege=[Beleg(nr=1, quelle="supabase", ziel="ideas?id=eq.a1b2c3d4&select=status",
                                  feld="status", wert=status, gemessen=T)],
                    deutung=deutung, links=links or [])


def test_schreibt_in_den_typordner(tmp_path):
    t = Tresor(tmp_path)
    ok, probleme = t.schreiben(dok())
    assert ok and probleme == []
    assert (tmp_path / "Bubbles" / "Marketing (a1b2c3).md").exists()


def test_ungueltiges_wird_nicht_geschrieben(tmp_path):
    t = Tresor(tmp_path)
    ok, probleme = t.schreiben(dok(deutung="Ohne Beleg."))
    assert not ok and probleme
    assert not list(tmp_path.rglob("*.md"))


def test_handbearbeitete_deutung_bleibt_wenn_neue_leer(tmp_path):
    t = Tresor(tmp_path)
    t.schreiben(dok(deutung="Von Hand: noch roh [B1]."))
    ok, _ = t.schreiben(dok())  # Kurator liefert nur Fakten
    assert ok
    assert t.lesen_von(dok()).deutung == "Von Hand: noch roh [B1]."


def test_alte_deutung_faellt_weg_wenn_ihr_beleg_nicht_mehr_stimmt(tmp_path):
    t = Tresor(tmp_path)
    t.schreiben(dok(deutung="Noch roh [B1]."))
    ok, _ = t.schreiben(dok(status="promoted"))
    assert ok
    # B1 sagt jetzt "promoted"; die alte Deutung beruhte auf "raw" -> verworfen
    assert t.lesen_von(dok()).deutung == ""


def test_atomar_keine_halbe_datei(tmp_path, monkeypatch):
    t = Tresor(tmp_path)
    t.schreiben(dok())
    import os
    def kaputt(*a, **k):
        raise OSError("Platte voll")
    monkeypatch.setattr(os, "replace", kaputt)
    ok, probleme = t.schreiben(dok(status="promoted"))
    assert not ok and "Platte voll" in probleme[0]
    assert t.lesen_von(dok()).fakten[0].wert == "raw"
    assert not list(tmp_path.rglob("*.tmp"))
    assert not list(tmp_path.rglob(".*"))  # auch keine versteckte Temp-Datei liegen geblieben


def test_atomare_temp_datei_ist_versteckt_und_endet_nicht_auf_md(tmp_path, monkeypatch):
    t = Tresor(tmp_path)
    import os
    aufgerufen = {}
    echter_replace = os.replace

    def spion(src, dst):
        aufgerufen["src"] = Path(src).name
        return echter_replace(src, dst)

    monkeypatch.setattr(os, "replace", spion)
    ok, _ = t.schreiben(dok())
    assert ok
    assert aufgerufen["src"] == ".Marketing (a1b2c3).md.tmp"
    assert not aufgerufen["src"].endswith(".md")


def test_alle_und_namen(tmp_path):
    t = Tresor(tmp_path)
    t.schreiben(dok())
    (tmp_path / "Bubbles" / "fremd.md").write_text("# freie Notiz ohne Kopf\n", encoding="utf-8")
    assert [d.id for d in t.alle()] == ["a1b2c3d4"]
    assert t.bekannte_namen() == {"Marketing (a1b2c3)"}


# ── Fix-Runde 1, Finding 1: gueltige Deutung bleibt auch bei neuer Deutung ──

def test_gueltige_deutung_bleibt_auch_wenn_neue_nicht_leer_ist(tmp_path):
    t = Tresor(tmp_path)
    t.schreiben(dok(deutung="Von Hand: noch roh [B1]."))
    ok, _ = t.schreiben(dok(deutung="LLM meint: alles im gruenen Bereich [B1]."))
    assert ok
    assert t.lesen_von(dok()).deutung == "Von Hand: noch roh [B1]."


def test_neue_deutung_gilt_wenn_alte_nicht_mehr_getragen_wird(tmp_path):
    t = Tresor(tmp_path)
    t.schreiben(dok(deutung="Noch roh [B1]."))
    ok, _ = t.schreiben(dok(status="promoted", deutung="Jetzt promoted [B1]."))
    assert ok
    assert t.lesen_von(dok()).deutung == "Jetzt promoted [B1]."


def test_deutung_noch_gueltig(tmp_path):
    t = Tresor(tmp_path)
    assert t.deutung_noch_gueltig(dok()) is False  # noch keine Datei
    t.schreiben(dok())  # keine Deutung
    assert t.deutung_noch_gueltig(dok()) is False  # alte Deutung leer
    t.schreiben(dok(deutung="Noch roh [B1]."))
    assert t.deutung_noch_gueltig(dok()) is True  # gleicher Belegstand
    assert t.deutung_noch_gueltig(dok(status="promoted")) is False  # Beleg geaendert


# ── Fix-Runde 1, Finding 2: unbekannter Link wird verworfen, Rest bleibt ──

def test_unbekannter_link_wird_verworfen_bekannter_bleibt(tmp_path):
    t = Tresor(tmp_path)
    t.schreiben(dok())  # legt "Marketing (a1b2c3)" als bekanntes Ziel an
    andere = Dokument(
        typ="bubble", id="ffffff11", titel="Sales", stand=T,
        fakten=[Fakt(schluessel="status", wert="raw", beleg=1)],
        belege=[Beleg(nr=1, quelle="supabase", ziel="ideas?id=eq.ffffff11&select=status",
                      feld="status", wert="raw", gemessen=T)],
        links=["Marketing (a1b2c3)", "Nicht Vorhanden (zzzzzz)"],
    )
    ok, probleme = t.schreiben(andere)
    assert ok and probleme == []
    gespeichert = t.lesen_von(andere)
    assert gespeichert.links == ["Marketing (a1b2c3)"]
