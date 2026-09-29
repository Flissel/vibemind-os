"""Dokumentmodell der Wissensschicht: Fakten mit Beleg, Deutung mit Belegpflicht."""
from datetime import datetime, timezone

import pytest

from core.knowledge.schema import (Beleg, Dokument, Fakt, dateiname, lesen,
                                   pruefen, rendern)

T = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def dok(**kw):
    basis = dict(
        typ="bubble", id="a1b2c3d4e5", titel="Marketing Q4", stand=T,
        fakten=[Fakt(schluessel="status", wert="raw", beleg=1),
                Fakt(schluessel="knoten", wert="12", beleg=2)],
        belege=[Beleg(nr=1, quelle="supabase", ziel="ideas?id=eq.a1b2c3d4e5&select=status",
                      feld="status", wert="raw", gemessen=T),
                Beleg(nr=2, quelle="supabase", ziel="canvas_nodes?linked_idea_id=eq.a1b2c3d4e5",
                      feld="#count", wert="12", gemessen=T)],
    )
    basis.update(kw)
    return Dokument(**basis)


def test_gueltiges_dokument_hat_keine_probleme():
    assert pruefen(dok(deutung="Die Bubble ist noch roh [B1]. Sie hat 12 Ideen [B2].")) == []


def test_fakt_ohne_beleg():
    d = dok(fakten=[Fakt(schluessel="status", wert="raw", beleg=7)])
    assert any("B7" in p for p in pruefen(d))


def test_fakt_wert_muss_dem_beleg_entsprechen():
    d = dok(fakten=[Fakt(schluessel="status", wert="promoted", beleg=1)])
    assert any("weicht" in p for p in pruefen(d))


def test_deutungssatz_ohne_beleg_wird_abgelehnt():
    probleme = pruefen(dok(deutung="Die Bubble ist vielversprechend. Sie ist roh [B1]."))
    assert any("ohne Beleg" in p for p in probleme)


def test_deutung_mit_unbekannter_belegnummer():
    assert any("B9" in p for p in pruefen(dok(deutung="Stark gewachsen [B9].")))


def test_link_auf_unbekanntes_dokument():
    d = dok(links=["Gibt es nicht (ffffff)"])
    assert any("unbekannt" in p for p in pruefen(d, bekannte_dokumente={"Andere (aaaaaa)"}))


def test_doppelte_belegnummer():
    b = dok().belege
    assert any("doppelt" in p for p in pruefen(dok(belege=[b[0], b[0]])))


def test_dateiname_sicher_und_eindeutig():
    assert dateiname(dok(titel="Q4: Plan / Budget?")) == "Q4 Plan Budget (a1b2c3)"
    assert dateiname(dok(id="zzzzzz9")) != dateiname(dok(id="yyyyyy9"))


def test_rundreise_rendern_lesen():
    d = dok(deutung="Roh [B1].", links=["Andere (aaaaaa)"])
    text = rendern(d)
    assert "[[Andere (aaaaaa)]]" in text and "| status | raw | [B1] |" in text
    assert lesen(text) == d


def test_lesen_uebernimmt_handbearbeitete_deutung():
    text = rendern(dok(deutung="Roh [B1]."))
    text = text.replace("Roh [B1].", "Von Hand ergaenzt, immer noch roh [B1].")
    assert lesen(text).deutung == "Von Hand ergaenzt, immer noch roh [B1]."


def test_unbekannter_typ_scheitert_schon_beim_bauen():
    with pytest.raises(Exception):
        dok(typ="rezept")
