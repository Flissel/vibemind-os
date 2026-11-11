"""Slide-Vorlagen der Video-Werkbank: Bildplan, Zeitplan, HTML-Komposition (ohne HyperFrames)."""
import re

import pytest

from spaces.marketing.claw import video_slides
from spaces.marketing.claw.video_slides import (FELDER, STILL_MAX_S, VORLAGEN, bild_pruefen, komposition,
                                                marke_fuer, zeitplan)
from spaces.marketing.claw.tests.slide_hilfen import SAETZE_1_2_3, S, html_fuer, szene
from spaces.marketing.workers.video_stimme import Satz

def test_schritt_startet_an_der_satzgrenze():
    plan = zeitplan({"schritte": [S("titel", 0), S("zahl", 2)]}, SAETZE_1_2_3, 6.0)
    assert [p.start_s for p in plan] == [0.0, SAETZE_1_2_3[2].start_s] and sum(p.dauer_s for p in plan) == 6.0


def test_stichpunkte_erscheinen_an_den_satzgrenzen_im_schritt():
    plan = zeitplan({"schritte": [S("titel", 0), S("stichpunkte", 1)]}, SAETZE_1_2_3, 6.0)
    assert plan[1].einblendungen == [SAETZE_1_2_3[2].start_s] and plan[0].einblendungen == []


def test_langer_schritt_bekommt_bewegung():
    saetze = [Satz("a", 0.0, 4.5), Satz("b", 9.0, 4.5)]          # erster Schritt: 9 s ohne Einblendung
    plan = zeitplan({"schritte": [S("titel", 0), S("zahl", 1)]}, saetze, 13.5)
    assert plan[0].dauer_s == 9.0 and plan[0].dauer_s > STILL_MAX_S
    html = komposition(szene(), plan, {}, "ton.wav", 13.5)
    assert html.count("bewegung zoom") == 2 and "scale(1.04)" in html and "--zoom-dauer:9.00s" in html


def test_kurzer_schritt_bleibt_ruhig():
    assert "bewegung zoom" not in html_fuer([S("titel", 0), S("zahl", 1)], saetze=[Satz("a", 0.0, 2.0), Satz("b", 2.5, 2.0)], dauer=4.5)


def test_stichpunkte_mit_einblendung_zoomen_nicht_trotz_langer_dauer():
    saetze = [Satz("a", 0.0, 2.0), Satz("b", 3.0, 2.0), Satz("c", 6.0, 2.0), Satz("d", 9.0, 2.0)]
    plan = zeitplan({"schritte": [S("titel", 0), S("stichpunkte", 1)]}, saetze, 11.5)
    assert "bewegung zoom" not in komposition(szene(), plan[1:], {}, "ton.wav", 11.5)


def test_ab_satz_ausserhalb_ist_fehler():
    assert bild_pruefen({"schritte": [S("titel", 0), S("zahl", 9)]}, 3)


def test_ab_satz_beginnt_bei_null_und_steigt_streng():
    assert bild_pruefen({"schritte": [S("titel", 1), S("zahl", 2)]}, 3)
    assert bild_pruefen({"schritte": [S("titel", 0), S("zahl", 0)]}, 3)
    assert bild_pruefen({"schritte": [S("titel", 0), S("zahl", 2)]}, 3) == []


def test_ein_schritt_ist_zu_wenig_und_sechs_zu_viel():
    assert bild_pruefen({"schritte": [S("titel", 0)]}, 3)
    assert bild_pruefen({"schritte": [S("titel", i) for i in range(6)]}, 8)


def test_fehlendes_pflichtfeld_wird_genannt():
    kaputt = S("zahl", 2)
    del kaputt["einheit"]
    assert any("zahl: einheit fehlt" in f for f in bild_pruefen({"schritte": [S("titel", 0), kaputt]}, 3))


def test_textlaengen_sind_begrenzt():
    assert bild_pruefen({"schritte": [S("titel", 0, zeile="x" * 61), S("zahl", 2)]}, 3)
    assert bild_pruefen({"schritte": [S("titel", 0), S("stichpunkte", 1, punkte=["x" * 81, "y"])]}, 3)
    assert bild_pruefen({"schritte": [S("titel", 0, zeile="x" * 60), S("stichpunkte", 1, punkte=["x" * 80, "y"])]}, 3) == []


def test_stichpunkte_zwei_bis_fuenf_und_agenda_drei():
    assert bild_pruefen({"schritte": [S("titel", 0), S("stichpunkte", 1, punkte=["nur einer"])]}, 3)
    assert bild_pruefen({"schritte": [S("titel", 0), S("stichpunkte", 1, punkte=list("abcdef"))]}, 3)
    assert bild_pruefen({"schritte": [S("titel", 0), S("agenda", 1, punkte=["a", "b"])]}, 3)
    assert bild_pruefen({"schritte": [S("titel", 0), S("agenda", 1, aktiv=4)]}, 3)


def test_jede_vorlage_hat_pflichtfelder_und_html():
    assert set(FELDER) == set(VORLAGEN) == {"titel", "stichpunkte", "zahl", "zitat", "vergleich", "agenda"}
    for v in VORLAGEN:
        html = html_fuer([S("titel", 0), S(v, 1)])
        assert f"t-{v}" in html


def test_text_wird_maskiert():
    html = html_fuer([S("titel", 0, zeile="<script>x</script>"), S("zahl", 2, einheit='"><img src=x onerror=1>')])
    assert "<script>" not in html and "&lt;script&gt;" in html and "<img src=x" not in html


def test_text_mit_platzhalter_wird_nicht_ersetzt():
    assert "%%ton%%" in html_fuer([S("titel", 0, zeile="%%ton%%"), S("zahl", 2)])


def test_kein_netz_im_html():
    html = html_fuer([S(v, i) for i, v in enumerate(VORLAGEN[:5])], saetze=[Satz("s", float(i), 0.9) for i in range(6)], dauer=6.0)
    assert "http://" not in html and "https://" not in html and "//cdn" not in html


def test_bildart_clip_beginnt_mit_titel():
    html = html_fuer([S("zahl", 0), S("zitat", 2)], bildart="clip")
    assert html.index("t-titel") < html.index("t-zitat") and "t-zahl" not in html
    assert "t-zahl" in html_fuer([S("zahl", 0), S("zitat", 2)], bildart="avatar_slide")


def test_format_im_wurzelelement():
    html = html_fuer([S("titel", 0), S("zahl", 2)])
    assert 'data-width="1920"' in html and 'data-height="1080"' in html and 'data-composition-id="szene"' in html
    assert html.count("<audio") == 1 and 'src="ton.wav"' in html and html.count('class="clip schritt') == 2


def test_variante_aendert_das_layout():
    a, b, c = (html_fuer([S("titel", 0), S("zahl", 2)], variante=v) for v in (0, 1, 2))
    assert len({a, b, c}) == 3 and "v0" in a and "v1" in b and "v2" in c
    assert html_fuer([S("titel", 0), S("zahl", 2)], variante=3) == a      # variante % 3


def test_uebergang_blende_hat_ein_und_ausblendung():
    assert 'class="blende ein"' in html_fuer([S("titel", 0), S("zahl", 2)], uebergang="blende")
    assert "blende ein" not in html_fuer([S("titel", 0), S("zahl", 2)], uebergang="schnitt")


def test_langes_wort_wird_verkleinert_und_darf_umbrechen():
    wort = "Datenschutzgrundverordnungs"        # 27 Zeichen
    html = html_fuer([S("titel", 0, zeile=wort), S("zahl", 2)])
    skala = float(re.search(r'class="kopf rein" style="--s:([0-9.]+)"', html).group(1))
    assert skala < 0.75 and "overflow-wrap: anywhere" in html
    assert 112 * skala * 0.6 * len(wort) < 1480      # grob: Wort passt in die Breite des Textblocks


def test_schriften_stehen_lokal_im_html():
    html = html_fuer([S("titel", 0), S("zahl", 2)], marke={"schrift_anzeige": "poppins", "schrift_text": "dm-sans"})
    assert 'url("schriften/poppins-700-normal.woff2")' in html and 'url("schriften/dm-sans-400-normal.woff2")' in html
    assert video_slides.schrift_dateien({"schrift_anzeige": "poppins", "schrift_text": "dm-sans"})
    for name in video_slides.schrift_dateien({"schrift_anzeige": "poppins", "schrift_text": "dm-sans"}):
        assert (video_slides.SCHRIFTEN / name).is_file()


def test_marke_mit_fehlenden_oder_ungueltigen_werten_nimmt_vorgaben():
    html = html_fuer([S("titel", 0), S("zahl", 2)], marke={"akzent": "rot; background:url(x)", "schrift_anzeige": "gibt-es-nicht",
                                                        "schrift_text": None, "logo": "/nicht/da.png"})
    assert "--akzent: #4C8DFF" in html and "url(x)" not in html and "<img" not in html
    assert 'url("schriften/manrope-700-normal.woff2")' in html


def test_dunkler_akzent_bekommt_helle_textfassung():
    html = html_fuer([S("titel", 0), S("zahl", 2)], marke={"akzent": "#101060"})
    akzent_text = re.search(r"--akzent-text: (#[0-9A-F]{6})", html).group(1)
    assert akzent_text != "#101060" and video_slides._helligkeit(video_slides._farbe(akzent_text)) >= 0.25


def test_marke_fuer_ohne_ordner_liefert_leere_werte(tmp_path):
    assert marke_fuer("Gibt-Es-Nicht", wurzel=str(tmp_path)) == {"akzent": "", "schrift_anzeige": "", "schrift_text": "", "logo": ""}


def test_marke_fuer_liest_marke_md(tmp_path):
    firma = tmp_path / "Beispiel"
    firma.mkdir()
    (firma / "Marke.md").write_text("---\nakzent: #E8A33D\nschrift_anzeige: poppins\nschrift_text: dm-sans\n---\n# Ton\nruhig\n",
                                    encoding="utf-8")
    m = marke_fuer("Beispiel", wurzel=str(tmp_path))
    assert m["akzent"] == "#E8A33D" and m["schrift_anzeige"] == "poppins" and m["schrift_text"] == "dm-sans" and m["logo"] == ""


def test_import_zieht_nichts_schweres():
    import subprocess
    import sys
    code = ("import sys; import spaces.marketing.claw.video_slides; "
            "bad = [m for m in ('PIL', 'torch', 'urllib.request', 'spaces.marketing.workers.video_stimme', "
            "'spaces.marketing.workers.video_rendern') if m in sys.modules]; print(bad)")
    aus = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60).stdout.strip()
    assert aus == "[]", aus


def test_umlaute_und_eszett_bleiben_zeichen_nur_markup_wird_maskiert():
    text = "Größe: ÄÖÜ äöü ß \u201eheiß\u201c & <b> \"q\""
    html = html_fuer([S("titel", 0, zeile=text), S("zahl", 2)])
    assert "Größe: ÄÖÜ äöü ß \u201eheiß\u201c" in html
    assert "&amp; &lt;b&gt; &quot;q&quot;" in html


def test_zahl_einheit_bleibt_groesser_als_erklaerung_auch_bei_langem_text():
    kurz = html_fuer([S("titel", 0), S("zahl", 1, einheit="Monate")], saetze=SAETZE_1_2_3[:2], dauer=4.5)
    lang = html_fuer([S("titel", 0), S("zahl", 1, einheit="weniger Aufwand im Angebotswesen und im Vertrieb")],
                     saetze=SAETZE_1_2_3[:2], dauer=4.5)
    css = kurz.split("<style>")[1].split("</style>")[0]
    einheit_px = int(re.search(r"\.einheit \{[^}]*?font-size: calc\((\d+)px", css).group(1))
    erklaerung_px = int(re.search(r"\.unter \{[^}]*?font-size: (\d+)px", css).group(1))
    for html in (kurz, lang):
        skala = float(re.search(r'class="einheit" style="--s:([0-9.]+)"', html).group(1))
        assert einheit_px * skala > erklaerung_px, (einheit_px, skala)
    assert "overflow-wrap: anywhere" in css


def test_erster_schritt_startet_bei_null_auch_wenn_der_satz_spaeter_beginnt():
    plan = zeitplan({"schritte": [S("titel", 0), S("zahl", 1)]}, [Satz("a", 0.4, 2.0), Satz("b", 2.6, 2.0)], 4.6)
    assert plan[0].start_s == 0.0 and sum(p.dauer_s for p in plan) == 4.6


def test_v2_agenda_ohne_schrittnummer_andere_vorlagen_mit():
    assert 'class="nummer"' not in html_fuer([S("agenda", 0), S("zahl", 2)], variante=2).split('t-zahl')[0]
    assert 'class="nummer"' in html_fuer([S("agenda", 0), S("zahl", 2)], variante=2).split('t-zahl')[1]


def test_v1_vergleich_ohne_zierstrich():
    css = html_fuer([S("vergleich", 0), S("zahl", 2)], variante=1)
    assert ".v1.t-vergleich .inhalt::before { display: none; }" in css and "v1 t-vergleich" in css


def test_v1_agenda_hat_feste_breite_und_linksbuendigen_text():
    html = html_fuer([S("agenda", 0), S("zahl", 2)], variante=1)
    css = html.split("<style>")[1].split("</style>")[0]
    assert re.search(r"^\.v1 \.agenda \{[^}]*width: 1240px[^}]*text-align: left", css, re.M)
    assert "v1 t-agenda" in html and '<ol class="agenda">' in html
