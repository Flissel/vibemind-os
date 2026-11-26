"""Erklaervideo in der Werkbank (26.11.2026): Code-Folie, Rahmen mit Stufe und Figur, Mund im Takt der Tonspur."""
import math
import struct
import wave

import pytest

from spaces.marketing.claw import video_mund, video_slides
from spaces.marketing.claw.tests.slide_hilfen import S, html_fuer
from spaces.marketing.claw.video_slides import bild_pruefen, figur_dateien

ERKLAER = {"firma": "Pexon <Consulting>", "titel": "Chunking erklärt", "stufe": "Schnitt nach Struktur",
           "stufe_nr": 2, "stufen": 4, "figur": "tux", "figur_name": "Tux"}


def ton(pfad, abschnitte, rate=16000, kanaele=1):
    """WAV aus (Sekunden, Lautstaerke 0..1)-Abschnitten: ein 220-Hz-Ton mit wechselnder Lautstaerke."""
    daten = []
    for sekunden, pegel in abschnitte:
        for i in range(int(rate * sekunden)):
            wert = int(32000 * pegel * math.sin(2 * math.pi * 220 * i / rate))
            daten += [wert] * kanaele
    with wave.open(str(pfad), "wb") as w:
        w.setnchannels(kanaele)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(daten)}h", *daten))
    return pfad


# --- Code-Folie --------------------------------------------------------------------------------

def test_code_folie_zeigt_zeilennummern_und_markierung():
    html = html_fuer([S("titel", 0), S("code", 1)])
    assert "t-code" in html and "corpus.py:84-86" in html
    assert '<span class="nr">84</span><span class="text">def teile(text):</span>' in html
    assert 'class="markiert"' in html and html.count('class="markiert"') == 1
    assert '<span class="nr">86</span><span class="text">&nbsp;</span>' in html        # leere Zeile bleibt eine Zeile


def test_code_wird_maskiert_und_einrueckung_bleibt():
    html = html_fuer([S("titel", 0), S("code", 1, zeilen=["    if a < b and c > d:", '        x = "<script>"'], markiert=[])])
    assert "<script>" not in html and "    if a &lt; b and c &gt; d:" in html and "&quot;&lt;script&gt;&quot;" in html


@pytest.mark.parametrize("felder", [
    {"zeilen": ["nur eine"]}, {"zeilen": ["x"] * 15}, {"zeilen": ["x" * 93, "y"]}, {"zeilen": ["a\nb", "c"]},
    {"zeilen": ["", " "]}, {"zeilen": "kein Feld"}, {"zeilen": ["a", 5]}, {"markiert": [3]}, {"markiert": [-1]},
    {"markiert": [True]}, {"markiert": "0"}, {"ab_zeile": 0}, {"ab_zeile": "84"}, {"datei": ""}, {"datei": "x" * 81},
    {"ueberschrift": ""}])
def test_code_folie_falsche_form(felder):
    assert bild_pruefen({"schritte": [S("titel", 0), S("code", 1, **felder)]}, 3)


def test_code_folie_gute_form_und_lange_zeilen_werden_kleiner():
    assert bild_pruefen({"schritte": [S("titel", 0), S("code", 1, zeilen=["x" * 92] * 14, markiert=[0, 13])]}, 3) == []
    assert bild_pruefen({"schritte": [S("titel", 0), S("code", 1)]}, 3) == []
    kurz = html_fuer([S("titel", 0), S("code", 1)])
    lang = html_fuer([S("titel", 0), S("code", 1, zeilen=["x" * 92] * 14, markiert=[])])
    assert 'style="--s:1.00"' in kurz and 'style="--s:0.67"' in lang


# --- Rahmen des Erklaervideos ------------------------------------------------------------------

def test_ohne_erklaer_bleibt_die_szene_wie_bisher():
    html = html_fuer([S("titel", 0), S("zahl", 2)])
    assert 'class="wurzel"' in html and 'class="kopfzeile"' not in html and 'class="figur' not in html
    assert "@keyframes mund" not in html


def test_rahmen_zeigt_firma_stufe_und_figur():
    html = html_fuer([S("titel", 0), S("zahl", 2)], erklaer=None)
    assert 'class="wurzel"' in html
    html = video_slides.komposition({"bildart": "slide", "variante": 0}, video_slides.zeitplan(
        {"schritte": [S("titel", 0), S("zahl", 2)]}, __import__("spaces.marketing.claw.tests.slide_hilfen", fromlist=["x"]).SAETZE_1_2_3, 6.0),
        {}, "ton.wav", 6.0, erklaer=ERKLAER, mund_css="@keyframes mund0 { 0% { opacity: 1; } }")
    assert 'class="wurzel erklaer"' in html
    assert "Pexon &lt;Consulting&gt;" in html and "<Consulting>" not in html
    assert '<div class="schild">Schnitt nach Struktur</div>' in html
    assert "Chunking erklärt · Stufe 2/4" in html and html.count('<i class="an"></i>') == 2 and html.count('<i class=""></i>') == 2
    assert 'src="figur_zu.png"' in html and 'src="figur_halb.png"' in html and 'src="figur_offen.png"' in html
    assert "Tux · <b>KI-Figur</b>" in html and "@keyframes mund0" in html


@pytest.mark.parametrize("anders", [{"figur": "gibtsnicht"}, {"figur": "../tux"}, {"figur": None}, {"figur": 5}])
def test_unbekannte_figur_erscheint_nicht(anders):
    from spaces.marketing.claw.tests.slide_hilfen import SAETZE_1_2_3
    plan = video_slides.zeitplan({"schritte": [S("titel", 0), S("zahl", 2)]}, SAETZE_1_2_3, 6.0)
    html = video_slides.komposition({"variante": 0}, plan, {}, "ton.wav", 6.0, erklaer={**ERKLAER, **anders})
    assert 'class="figur' not in html and 'class="kopfzeile"' in html
    assert figur_dateien(anders["figur"]) == {}


def test_tux_hat_seine_drei_mundbilder():
    bilder = figur_dateien("tux")
    assert sorted(bilder) == ["figur_halb.png", "figur_offen.png", "figur_zu.png"]
    assert all(q.is_file() and q.stat().st_size > 10_000 for q in bilder.values())


@pytest.mark.parametrize("anders", [{"stufe_nr": 0}, {"stufe_nr": 5}, {"stufen": 13}, {"stufe_nr": True}, {"stufen": None}])
def test_unsinnige_stufenzahl_zeigt_keine_anzeige(anders):
    from spaces.marketing.claw.tests.slide_hilfen import SAETZE_1_2_3
    plan = video_slides.zeitplan({"schritte": [S("titel", 0), S("zahl", 2)]}, SAETZE_1_2_3, 6.0)
    html = video_slides.komposition({"variante": 0}, plan, {}, "ton.wav", 6.0, erklaer={**ERKLAER, **anders})
    assert 'class="stufen"' not in html and 'class="schild"' in html


# --- Mund im Takt ------------------------------------------------------------------------------

def test_mund_ist_in_pausen_zu_und_beim_sprechen_offen(tmp_path):
    wav = ton(tmp_path / "t.wav", [(0.5, 0.0), (1.0, 0.8), (0.5, 0.0), (1.0, 0.8)])
    plan = video_mund.wechsel(wav)
    assert plan[0] == (0.0, video_mund.ZU)
    def stellung(t):
        return [s for z, s in plan if z <= t][-1]
    assert stellung(0.2) == video_mund.ZU and stellung(1.75) == video_mund.ZU
    assert stellung(1.0) != video_mund.ZU and stellung(2.5) != video_mund.ZU
    assert all(b[0] > a[0] for a, b in zip(plan, plan[1:]))


def test_stereo_und_unlesbarer_ton(tmp_path):
    stereo = ton(tmp_path / "s.wav", [(0.3, 0.0), (0.6, 0.8)], kanaele=2)
    assert len(video_mund.lautstaerken(stereo)) == 22            # 0,9 s in Fenstern von 40 ms, nicht doppelt
    kaputt = tmp_path / "k.wav"
    kaputt.write_bytes(b"kein wav")
    assert video_mund.wechsel(kaputt) == [(0.0, video_mund.ZU)]
    assert video_mund.wechsel(tmp_path / "fehlt.wav") == [(0.0, video_mund.ZU)]


def test_keyframes_zeigen_immer_genau_ein_bild():
    css = video_mund.keyframes([(0.0, 0), (1.0, 2), (1.5, 1), (2.0, 0)], 4.0)
    assert "@keyframes mund0 { 0.000% { opacity: 1; } 25.000% { opacity: 0; } 37.500% { opacity: 0; } 50.000% { opacity: 1; } 100% { opacity: 1; } }" in css
    assert "25.000% { opacity: 1; } 37.500% { opacity: 0; }" in css.split("@keyframes mund2")[1].split("}")[0] + "}" \
        or "@keyframes mund2 { 0.000% { opacity: 0; } 25.000% { opacity: 1; } 37.500% { opacity: 0; }" in css
    assert css.count("step-end both") == 3 and ".figur .m1 { animation: mund1 4.000s" in css
    for prozent in ("0.000%", "25.000%", "37.500%", "50.000%"):
        assert sum(f"{prozent} {{ opacity: 1; }}" in teil for teil in css.split("@keyframes")[1:]) == 1


def test_keyframes_ohne_plan_und_mit_zeiten_hinter_dem_ende():
    assert "@keyframes mund0 { 0.000% { opacity: 1; } 100% { opacity: 1; } }" in video_mund.keyframes([], 3.0)
    css = video_mund.keyframes([(0.5, 2), (9.0, 1)], 2.0)
    assert "0.000% { opacity: 1; }" in css.split("@keyframes mund0")[1] and "450" not in css
