from __future__ import annotations

from spaces.auftragsbuch.rowboat.abgleich import einordnen, nach_einstellen

A, B, C, D = ("a" * 16, "b" * 16, "c" * 16, "d" * 16)


def _p(pid: str) -> dict:
    return {"punkt_id": pid, "titel": "t", "frist": None, "link": ""}


def _sig(kind: str, pid: str) -> dict:
    return {"kind": kind, "key": f"[gehirn] t · #{pid}", "at": "2026-11-23T10:00:00Z"}


def test_offener_vorschlag_ist_schon_vorhanden():
    stand, neu = einordnen([_p(A)], [f"[Gehirn] t · #{A}"], [], [], set())
    assert stand == {A: "schon_vorhanden"} and neu == []


def test_in_liste_ist_schon_vorhanden():
    stand, neu = einordnen([_p(A)], [], [f"[Gehirn] t · #{A}"], [], set())
    assert stand == {A: "schon_vorhanden"} and neu == []


def test_abgelehnt_dismissed_und_taught():
    stand, neu = einordnen([_p(A), _p(B)], [], [], [_sig("dismissed", A), _sig("taught", B)], set())
    assert stand == {A: "frueher_abgelehnt", B: "frueher_abgelehnt"} and neu == []


def test_letztes_signal_gewinnt():
    sig = [_sig("dismissed", A), _sig("kept", A)]
    stand, neu = einordnen([_p(A)], [], [], sig, set())
    assert stand == {} and [p["punkt_id"] for p in neu] == [A]


def test_dismissed_nach_kept_zaehlt():
    sig = [_sig("kept", A), _sig("dismissed", A)]
    stand, _ = einordnen([_p(A)], [], [], sig, set())
    assert stand == {A: "frueher_abgelehnt"}


def test_vorhanden_geht_vor_abgelehnt():
    stand, _ = einordnen([_p(A)], [f"x #{A}"], [], [_sig("dismissed", A)], set())
    assert stand == {A: "schon_vorhanden"}


def test_archivierter_punkt_ueber_auftragsbuch():
    stand, neu = einordnen([_p(A)], [], [], [], {A})
    assert stand == {A: "schon_vorhanden"} and neu == []


def test_abgelehnt_geht_vor_bekannt():
    stand, _ = einordnen([_p(A)], [], [], [_sig("dismissed", A)], {A})
    assert stand == {A: "frueher_abgelehnt"}


def test_neu():
    stand, neu = einordnen([_p(A)], [], [], [], set())
    assert stand == {} and neu == [_p(A)]


def test_doppelte_kennung_einmal():
    stand, neu = einordnen([_p(A), _p(A), _p(B)], [], [], [], set())
    assert [p["punkt_id"] for p in neu] == [A, B]
    stand, neu = einordnen([_p(A), _p(A)], [], [], [], {A})
    assert stand == {A: "schon_vorhanden"}


def test_signal_ohne_kennung_wird_ignoriert():
    sig = [{"kind": "dismissed", "key": "irgendwas", "at": "x"}, {"kind": "dismissed"}, {}]
    stand, neu = einordnen([_p(A)], [], [], sig, set())
    assert stand == {} and len(neu) == 1


def test_nach_einstellen():
    stand, fehlen = nach_einstellen([_p(A), _p(B), _p(C)], [A, C, D])
    assert stand == {A: "neu", C: "neu"}
    assert fehlen == [B]


def test_nach_einstellen_alles_da():
    assert nach_einstellen([_p(A)], [A]) == ({A: "neu"}, [])


def test_nach_einstellen_mit_rowboat_texten():
    # Rowboat gibt normalisierte Volltexte zurueck (Leerraum zusammengefasst, getrimmt)
    texte = [f"[Gehirn]  Foo   ·  #{A}  ", f"[Gehirn] Bar\t· #{C.upper()}"]
    stand, fehlen = nach_einstellen([_p(A), _p(B), _p(C)], texte)
    assert stand == {A: "neu", C: "neu"}
    assert fehlen == [B]
