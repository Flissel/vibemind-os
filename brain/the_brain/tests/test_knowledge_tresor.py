from datetime import datetime, timezone

from core.knowledge.schema import Beleg, Dokument, Fakt
from core.knowledge.tresor import Tresor

T = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def dok(status="raw", deutung=""):
    return Dokument(typ="bubble", id="a1b2c3d4", titel="Marketing", stand=T,
                    fakten=[Fakt(schluessel="status", wert=status, beleg=1)],
                    belege=[Beleg(nr=1, quelle="supabase", ziel="ideas?id=eq.a1b2c3d4&select=status",
                                  feld="status", wert=status, gemessen=T)],
                    deutung=deutung)


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


def test_alle_und_namen(tmp_path):
    t = Tresor(tmp_path)
    t.schreiben(dok())
    (tmp_path / "Bubbles" / "fremd.md").write_text("# freie Notiz ohne Kopf\n", encoding="utf-8")
    assert [d.id for d in t.alle()] == ["a1b2c3d4"]
    assert t.bekannte_namen() == {"Marketing (a1b2c3)"}
