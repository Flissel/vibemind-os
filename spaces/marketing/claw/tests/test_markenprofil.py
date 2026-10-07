"""markenprofil: Marke.md (Kopfteil + Abschnitte) und Logo lesen, schreiben, spiegeln.

Alle Tests arbeiten nur in tmp_path, nie in der echten ~/.rowboat-Ablage.
"""
import base64
import datetime
import io
import os
import subprocess
import sys

import pytest
from PIL import Image

from spaces.marketing.claw import markenprofil as mp

JETZT = datetime.datetime(2026, 10, 7, 14, 5)
WERTE = {"akzent": "#C8102E", "zweitfarbe": "#F4EFE6", "grund": "#FFFFFF", "text": "#222222",
         "schrift_anzeige": "playfair", "schrift_text": "dm-sans"}
ABSCHNITTE = {"Wer wir sind": "Wir bauen Werkzeuge.", "Ton": "Locker, Du-Form."}


def _bild(format_, groesse=(40, 30), modus="RGB", rauschen=False):
    if rauschen:
        img = Image.frombytes(modus, groesse, os.urandom(groesse[0] * groesse[1] * len(modus)))
    else:
        img = Image.new(modus, groesse, (200, 16, 46, 128)[:len(modus)])
    buf = io.BytesIO()
    img.save(buf, format_)
    return buf.getvalue()


PNG = _bild("PNG")
JPG = _bild("JPEG")


def _junction(link, ziel):
    if sys.platform != "win32":
        pytest.skip("Junctions gibt es nur unter Windows")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(ziel)],
                   check=True, capture_output=True)


@pytest.fixture
def wurzel(tmp_path):
    w = tmp_path / "companys"
    w.mkdir()
    return str(w)


def _marke(wurzel, text, ordner="VibeMind"):
    os.makedirs(os.path.join(wurzel, ordner), exist_ok=True)
    with open(os.path.join(wurzel, ordner, "Marke.md"), "w", encoding="utf-8") as f:
        f.write(text)


# --- lesen ----------------------------------------------------------------------

def test_lesen_ohne_ordner_ist_leeres_profil(wurzel):
    p = mp.lesen(wurzel, "m1", "VibeMind")
    assert (p.werte, p.abschnitte, p.ordner, p.logo_pfad) == ({}, {}, None, None)


def test_lesen_ohne_marke_md_hat_ordner_aber_nichts(wurzel):
    os.makedirs(os.path.join(wurzel, "VibeMind"))
    p = mp.lesen(wurzel, "m1", "VibeMind")
    assert p.ordner and p.werte == {} and p.abschnitte == {}


def test_lesen_gueltiger_kopfteil_und_abschnitte(wurzel):
    _marke(wurzel, "---\nakzent: #C8102E\nzweitfarbe: #F4EFE6\nschrift_anzeige: playfair\n"
                   "stand: 2026-10-07 14:05 von Felix\n---\n\n# VibeMind\n\n## Wer wir sind\nWir bauen.\n\n"
                   "## Ton\nLocker.\n")
    p = mp.lesen(wurzel, "m1", "VibeMind")
    assert p.werte == {"akzent": "#C8102E", "zweitfarbe": "#F4EFE6", "schrift_anzeige": "playfair",
                       "stand": "2026-10-07 14:05 von Felix"}
    assert p.abschnitte == {"Wer wir sind": "Wir bauen.", "Ton": "Locker."}
    assert p.hinweise == []


def test_lesen_kaputte_farbe_verworfen_rest_gueltig(wurzel):
    _marke(wurzel, "---\nakzent: #12\ngrund: #FFFFFF\n---\n## Ton\nLocker.\n")
    p = mp.lesen(wurzel, "m1", "VibeMind")
    assert "akzent" not in p.werte and p.werte["grund"] == "#FFFFFF"
    assert "Marke.md: akzent ungültig" in p.hinweise
    assert p.abschnitte == {"Ton": "Locker."}


def test_lesen_unbekannte_schrift_verworfen(wurzel):
    _marke(wurzel, "---\nschrift_anzeige: comic-sans\nschrift_text: dm-sans\n---\n")
    p = mp.lesen(wurzel, "m1", "VibeMind")
    assert p.werte == {"schrift_text": "dm-sans"}
    assert "Marke.md: schrift_anzeige ungültig" in p.hinweise


def test_lesen_ohne_kopfteil_gilt_nur_der_textteil(wurzel):
    _marke(wurzel, "# VibeMind\n<!-- Hilfe -->\n\n## Wer wir sind\nWir bauen.\n\n## Ton\n<!-- leer -->\n")
    p = mp.lesen(wurzel, "m1", "VibeMind")
    assert p.werte == {} and p.hinweise == []
    assert p.abschnitte == {"Wer wir sind": "Wir bauen."}


def test_lesen_unbekannte_kopfschluessel_und_ungeschlossener_kopf(wurzel):
    _marke(wurzel, "---\nfoo: bar\nakzent: #C8102E\n")  # nie geschlossen: kein Kopfteil
    assert mp.lesen(wurzel, "m1", "VibeMind").werte == {}
    _marke(wurzel, "---\nfoo: bar\nakzent: #C8102E\n---\n")
    assert mp.lesen(wurzel, "m1", "VibeMind").werte == {"akzent": "#C8102E"}


def test_lesen_logo_vorhanden_und_fehlend(wurzel):
    _marke(wurzel, "---\nlogo: logo.png\n---\n")
    p = mp.lesen(wurzel, "m1", "VibeMind")
    assert p.logo_pfad is None and "Marke.md: logo ungültig" in p.hinweise
    with open(os.path.join(wurzel, "VibeMind", "logo.png"), "wb") as f:
        f.write(PNG)
    p = mp.lesen(wurzel, "m1", "VibeMind")
    assert p.logo_pfad == os.path.join(wurzel, "VibeMind", "logo.png") and p.hinweise == []


def test_lesen_logo_name_mit_pfad_ungueltig(wurzel):
    _marke(wurzel, "---\nlogo: ../x.png\n---\n")
    p = mp.lesen(wurzel, "m1", "VibeMind")
    assert "logo" not in p.werte and "Marke.md: logo ungültig" in p.hinweise


def test_lesen_wirft_nie(wurzel):
    os.makedirs(os.path.join(wurzel, "VibeMind", "Marke.md"))  # Ordner statt Datei
    assert mp.lesen(wurzel, "m1", "VibeMind").werte == {}
    assert mp.lesen(os.path.join(wurzel, "gibtsnicht"), "m1", "X").ordner is None


def test_lesen_marke_md_als_symlink_wird_nicht_gelesen(wurzel, tmp_path):
    fremd = tmp_path / "fremd"
    fremd.mkdir()
    (fremd / "Marke.md").write_text("---\nakzent: #000000\n---\n", encoding="utf-8")
    os.makedirs(os.path.join(wurzel, "VibeMind"))
    try:
        os.symlink(fremd / "Marke.md", os.path.join(wurzel, "VibeMind", "Marke.md"))
    except OSError:
        pytest.skip("Symlinks nicht erlaubt")
    assert mp.lesen(wurzel, "m1", "VibeMind").werte == {}


# --- schreiben ------------------------------------------------------------------

def test_schreiben_legt_ordner_an_und_rundlauf(wurzel):
    pfad = mp.schreiben(wurzel, "m1", "VibeMind", WERTE, ABSCHNITTE, (PNG, "image/png"), "Felix", JETZT)
    assert pfad == os.path.join(wurzel, "VibeMind", "Marke.md")
    p = mp.lesen(wurzel, "m1", "VibeMind")
    assert p.werte == dict(WERTE, logo="logo.png", stand="2026-10-07 14:05 von Felix")
    assert p.abschnitte == ABSCHNITTE and p.hinweise == []
    assert open(p.logo_pfad, "rb").read() == PNG


def test_schreiben_bisherige_marke_kommt_in_den_verlauf(wurzel):
    mp.schreiben(wurzel, "m1", "VibeMind", WERTE, ABSCHNITTE, None, "Felix", JETZT)
    alt = open(os.path.join(wurzel, "VibeMind", "Marke.md"), encoding="utf-8").read()
    mp.schreiben(wurzel, "m1", "VibeMind", dict(WERTE, akzent="#000000"), ABSCHNITTE, None, "Felix", JETZT)
    mp.schreiben(wurzel, "m1", "VibeMind", dict(WERTE, akzent="#111111"), ABSCHNITTE, None, "Felix", JETZT)
    verlauf = os.path.join(wurzel, "VibeMind", "Marke-Verlauf")
    assert sorted(os.listdir(verlauf)) == ["2026-10-07-1405-2.md", "2026-10-07-1405.md"]
    assert open(os.path.join(verlauf, "2026-10-07-1405.md"), encoding="utf-8").read() == alt
    assert mp.lesen(wurzel, "m1", "VibeMind").werte["akzent"] == "#111111"
    assert not [n for n in os.listdir(os.path.join(wurzel, "VibeMind")) if ".tmp" in n]


def test_schreiben_erstes_mal_kein_verlauf(wurzel):
    mp.schreiben(wurzel, "m1", "VibeMind", WERTE, {}, None, "Felix", JETZT)
    assert not os.path.exists(os.path.join(wurzel, "VibeMind", "Marke-Verlauf"))


def test_schreiben_ersetzt_altes_logo(wurzel):
    mp.schreiben(wurzel, "m1", "VibeMind", WERTE, {}, (PNG, "image/png"), "F", JETZT)
    mp.schreiben(wurzel, "m1", "VibeMind", WERTE, {}, (JPG, "image/jpeg"), "F", JETZT)
    ordner = os.path.join(wurzel, "VibeMind")
    assert os.path.exists(os.path.join(ordner, "logo.jpg"))
    assert not os.path.exists(os.path.join(ordner, "logo.png"))
    assert mp.lesen(wurzel, "m1", "VibeMind").werte["logo"] == "logo.jpg"


def test_schreiben_ohne_neues_logo_behaelt_vorhandenes(wurzel):
    mp.schreiben(wurzel, "m1", "VibeMind", WERTE, {}, (JPG, "image/jpeg"), "F", JETZT)
    mp.schreiben(wurzel, "m1", "VibeMind", dict(WERTE, logo="logo.jpg"), {}, None, "F", JETZT)
    p = mp.lesen(wurzel, "m1", "VibeMind")
    assert p.logo_pfad and p.logo_pfad.endswith("logo.jpg")


def test_schreiben_nutzt_vorhandenen_ordner_per_id_oder_name(wurzel):
    os.makedirs(os.path.join(wurzel, "vibemind"))
    mp.schreiben(wurzel, "m1", "VibeMind", WERTE, {}, None, "F", JETZT)
    assert os.listdir(wurzel) == ["vibemind"]


def test_schreiben_firmenordner_als_junction_ist_fehler(wurzel, tmp_path):
    fremd = tmp_path / "fremd"
    fremd.mkdir()
    _junction(os.path.join(wurzel, "VibeMind"), fremd)
    with pytest.raises(mp.MarkenFehler):
        mp.schreiben(wurzel, "m1", "VibeMind", WERTE, ABSCHNITTE, None, "F", JETZT)
    assert os.listdir(fremd) == []


def test_schreiben_verlauf_als_junction_ist_fehler(wurzel, tmp_path):
    mp.schreiben(wurzel, "m1", "VibeMind", WERTE, {}, None, "F", JETZT)
    fremd = tmp_path / "fremd"
    fremd.mkdir()
    _junction(os.path.join(wurzel, "VibeMind", "Marke-Verlauf"), fremd)
    with pytest.raises(mp.MarkenFehler):
        mp.schreiben(wurzel, "m1", "VibeMind", WERTE, {}, None, "F", JETZT)
    assert os.listdir(fremd) == []


def test_schreiben_logo_zu_gross_oder_kein_bild(wurzel):
    gross = PNG + b"\0" * (2 * 1024 * 1024)
    for logo in ((gross, "image/png"), (b"GIF89a....", "image/gif"), (b"", "image/png")):
        with pytest.raises(mp.MarkenFehler):
            mp.schreiben(wurzel, "m1", "VibeMind", WERTE, {}, logo, "F", JETZT)
    assert not os.path.exists(os.path.join(wurzel, "VibeMind", "Marke.md"))


def test_schreiben_ungueltiger_wert_ist_fehler(wurzel):
    with pytest.raises(mp.MarkenFehler):
        mp.schreiben(wurzel, "m1", "VibeMind", {"akzent": "rot"}, {}, None, "F", JETZT)
    with pytest.raises(mp.MarkenFehler):
        mp.schreiben(wurzel, "m1", "VibeMind", {"schrift_text": "comic-sans"}, {}, None, "F", JETZT)


def test_schreiben_name_darf_kein_pfad_sein(wurzel):
    with pytest.raises(mp.MarkenFehler):
        mp.schreiben(wurzel, "m1", "../x", WERTE, {}, None, "F", JETZT)


# --- text / fuer_prompt ---------------------------------------------------------

def test_text_hat_kopfteil_und_abschnitte():
    t = mp.text({"akzent": "#C8102E"}, {"Ton": "Locker."})
    assert t.startswith("---\nakzent: #C8102E\n---\n") and "## Ton\nLocker." in t


def test_fuer_prompt_nennt_farben_schriften_und_abschnitte(wurzel):
    mp.schreiben(wurzel, "m1", "VibeMind", WERTE, ABSCHNITTE, (PNG, "image/png"), "F", JETZT)
    s = mp.fuer_prompt(mp.lesen(wurzel, "m1", "VibeMind"))
    assert "Akzentfarbe #C8102E" in s and "Zweitfarbe #F4EFE6" in s
    assert "Playfair Display" in s and "DM Sans" in s
    assert "## Wer wir sind\nWir bauen Werkzeuge." in s


# --- gestalt --------------------------------------------------------------------

def test_gestalt_schluessel_und_werte():
    g = mp.gestalt(WERTE, PNG, "image/png")
    assert g["akzent"] == "#C8102E" and g["flaeche"] == "#F4EFE6"
    assert g["schriften"] == {"anzeige": "playfair", "text": "dm-sans"}
    assert g["logo"].startswith("data:image/")
    assert set(g) == {"akzent", "flaeche", "logo", "schriften"}


def test_gestalt_laesst_ungueltiges_weg():
    assert mp.gestalt({"akzent": "#12", "schrift_anzeige": "x"}, None, None) == {}
    assert mp.gestalt({"akzent": "#C8102E"}, b"kein bild", "image/png") == {"akzent": "#C8102E"}
    assert mp.gestalt({"schrift_text": "dm-sans"}, None, None) == {}      # I2: nur als Paar


def _daten(url):
    kopf, _, b64 = url.partition(",")
    return kopf, base64.b64decode(b64)


def test_gestalt_logo_wird_verkleinert_png_behaelt_alpha():
    gross = _bild("PNG", (640, 640), "RGBA", rauschen=True)
    assert 600 * 1024 < len(gross) <= 2 * 1024 * 1024
    url = mp.gestalt({}, gross, "image/png")["logo"]
    kopf, roh = _daten(url)
    assert kopf == "data:image/png;base64" and len(url) <= 140 * 1024
    img = Image.open(io.BytesIO(roh))
    assert max(img.size) <= 600 and img.mode == "RGBA"


def test_gestalt_jpeg_bleibt_jpeg_und_klein():
    gross = _bild("JPEG", (1000, 800), "RGB", rauschen=True)
    assert 140 * 1024 < len(gross) <= 2 * 1024 * 1024
    url = mp.gestalt({}, gross, "image/jpeg")["logo"]
    kopf, roh = _daten(url)
    assert kopf == "data:image/jpeg;base64" and len(url) <= 140 * 1024
    assert max(Image.open(io.BytesIO(roh)).size) <= 600


def test_gestalt_png_ohne_alpha_wird_jpeg():
    url = mp.gestalt({}, _bild("PNG", (100, 100), "RGB"), "image/png")["logo"]
    assert url.startswith("data:image/jpeg;base64,")


def test_gestalt_kleines_logo_wird_nicht_vergroessert():
    _, roh = _daten(mp.gestalt({}, PNG, "image/png")["logo"])
    assert Image.open(io.BytesIO(roh)).size[0] <= 40


# --- Fix-Runde 1 ----------------------------------------------------------------

def test_schreiben_fehlschlag_laesst_profil_unveraendert(wurzel, tmp_path):
    mp.schreiben(wurzel, "m1", "VibeMind", WERTE, {}, (PNG, "image/png"), "F", JETZT)
    ordner = os.path.join(wurzel, "VibeMind")
    fremd = tmp_path / "fremd"
    fremd.mkdir()
    _junction(os.path.join(ordner, "Marke-Verlauf"), fremd)
    with pytest.raises(mp.MarkenFehler):
        mp.schreiben(wurzel, "m1", "VibeMind", WERTE, {}, (JPG, "image/jpeg"), "F", JETZT)
    assert os.path.exists(os.path.join(ordner, "logo.png"))
    assert not os.path.exists(os.path.join(ordner, "logo.jpg"))
    p = mp.lesen(wurzel, "m1", "VibeMind")
    assert p.hinweise == [] and p.werte["logo"] == "logo.png"
    assert not [n for n in os.listdir(ordner) if n.startswith(".tmp-")]


def test_schreiben_signatur_aber_nicht_dekodierbar(wurzel):
    with pytest.raises(mp.MarkenFehler):
        mp.schreiben(wurzel, "m1", "VibeMind", WERTE, {}, (b"\x89PNG\r\n\x1a\nmuell", "image/png"), "F", JETZT)


def test_gestalt_ueber_pixelgrenze_ohne_logo():
    buf = io.BytesIO()
    Image.new("1", (7000, 7000)).save(buf, "PNG")  # 49 MP, winzige Datei
    assert "logo" not in mp.gestalt({"akzent": "#C8102E"}, buf.getvalue(), "image/png")
    with pytest.raises(mp.MarkenFehler):
        mp.schreiben("x", "m", "N", {}, {}, (buf.getvalue(), "image/png"), "F", JETZT)


def test_schreiben_firmenordner_als_symlink_ist_fehler(wurzel, tmp_path):
    fremd = tmp_path / "fremd"
    fremd.mkdir()
    try:
        os.symlink(fremd, os.path.join(wurzel, "VibeMind"), target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks nicht erlaubt")
    with pytest.raises(mp.MarkenFehler):
        mp.schreiben(wurzel, "m1", "VibeMind", WERTE, ABSCHNITTE, None, "F", JETZT)
    assert os.listdir(fremd) == []


def test_ueberschrift_im_abschnittstext_ueberlebt_rundlauf(wurzel):
    abschnitte = {"Ton": "Vorher\n## Falsche Ueberschrift\n# Titel\nNachher"}
    mp.schreiben(wurzel, "m1", "VibeMind", WERTE, abschnitte, None, "F", JETZT)
    p = mp.lesen(wurzel, "m1", "VibeMind")
    assert list(p.abschnitte) == ["Ton"]
    assert p.abschnitte["Ton"] == "Vorher\n ## Falsche Ueberschrift\n # Titel\nNachher"


def test_i2_schriften_nur_als_vollstaendiges_paar():
    """Ein kaputter Schriftwert darf den ganzen Spiegel nicht blockieren: ohne Paar keine schriften,
    die uebrigen gueltigen Werte bleiben."""
    g = mp.gestalt({"akzent": "#C8102E", "zweitfarbe": "#3b2f2f", "schrift_anzeige": "playfair",
                    "schrift_text": "comic"}, None, None)
    assert g == {"akzent": "#C8102E", "flaeche": "#3b2f2f"}
    assert mp.gestalt({"schrift_anzeige": "playfair", "schrift_text": "manrope"}, None, None) == \
        {"schriften": {"anzeige": "playfair", "text": "manrope"}}
