import base64
import copy
import os
import stat

import pytest

from spaces.marketing.claw import schoenheit, vorlagen_marke as vm

WEISS = "#ffffff"


@pytest.mark.parametrize("akzent", ["#facc15", "#111111", "#c2410c", "#9ca3af"])
def test_rollen_immer_lesbar(akzent):
    r = vm.rollen({"akzent": akzent, "flaeche": "#ffffff"}, WEISS)
    assert schoenheit.kontrast(r["auf_akzent"], r["akzent"]) >= 3
    assert schoenheit.kontrast(r["akzent_text"], WEISS) >= 4.5
    assert schoenheit.kontrast(r["auf_zweit"], r["zweit"]) >= 4.5
    assert schoenheit.kontrast(r["zweit"], WEISS) >= 3          # zu helle flaeche wird ersetzt


def test_ohne_gestalt_ersatzpalette():
    r = vm.rollen(None, WEISS)
    assert r["akzent"] == vm.ERSATZ_AKZENT and all(v.startswith("#") and len(v) == 7 for v in r.values())


def test_akzent_hell_ist_toenung():
    r = vm.rollen({"akzent": "#c2410c"}, WEISS)
    assert schoenheit.kontrast(r["akzent_hell"], WEISS) < 1.3


def test_logo_wird_datei_mit_pruefsumme(tmp_path):
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 40).decode()
    g = {"logo": f"data:image/png;base64,{png}"}
    a = vm.logo_ablegen(g, "radhaus", str(tmp_path))
    b = vm.logo_ablegen(g, "radhaus", str(tmp_path))
    assert a == b and a.startswith("medien:logo-radhaus-") and a.endswith(".png")
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("gestalt", [None, {}, {"logo": "data:image/gif;base64,AAAA"}, {"logo": "https://x/y.png"}])
def test_kein_gueltiges_logo(tmp_path, gestalt):
    assert vm.logo_ablegen(gestalt, "radhaus", str(tmp_path)) is None


def test_logo_ohne_ordner(tmp_path):
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    assert vm.logo_ablegen({"logo": f"data:image/png;base64,{png}"}, "radhaus", "") is None


DOK = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["kopf"], "rollen": {
           "band/data/style/backgroundColor": "akzent", "marke_logo/data/props/url": "logo",
           "marke_wort/data/props/text": "laden"}}},
       "kopf": {"type": "Container", "data": {"style": {}, "props": {"childrenIds": ["marke_logo", "marke_wort", "band"]}}},
       "marke_logo": {"type": "Image", "data": {"style": {}, "props": {"url": "medien:platzhalter-1x1.png", "width": 120}}},
       "marke_wort": {"type": "Heading", "data": {"style": {}, "props": {"text": "[Laden]"}}},
       "band": {"type": "Text", "data": {"style": {"backgroundColor": "#c2410c"}, "props": {"text": "x"}}}}


def test_einsetzen_mit_logo():
    vorher = copy.deepcopy(DOK)
    d = vm.einsetzen(DOK, {"akzent": "#123456", "logo": "medien:logo-a-1.png", "laden": "Radhaus"})
    assert DOK == vorher                                    # Eingabe unveraendert
    assert d["band"]["data"]["style"]["backgroundColor"] == "#123456"
    assert d["marke_logo"]["data"]["props"]["url"] == "medien:logo-a-1.png"
    assert "marke_wort" not in d and "marke_wort" not in d["kopf"]["data"]["props"]["childrenIds"]


def test_einsetzen_ohne_logo_wortmarke():
    d = vm.einsetzen(DOK, {"akzent": "#123456", "laden": "Radhaus Jena"})
    assert "marke_logo" not in d and d["marke_wort"]["data"]["props"]["text"] == "Radhaus Jena"


def test_unbekannte_rolle_und_kaputter_pfad_stoeren_nicht():
    d = copy.deepcopy(DOK)
    d["root"]["data"]["rollen"]["gibtsnicht/data/x"] = "akzent"
    d["root"]["data"]["rollen"]["band/data/style/color"] = "unbekannt"
    assert vm.einsetzen(d, {"akzent": "#123456", "laden": "L"})["band"]["data"]["style"].get("color") is None


def test_auf_zweit_mindestens_4_5_gegen_zweit():
    """Mid-grey flaeche (#7c7c7c) meets 3:1 against white but neither weiss nor
    dunkelgrau reaches 4.5:1 against it. rollen() must darken zweit until weiss reaches 4.5:1."""
    r = vm.rollen({"akzent": "#c2410c", "flaeche": "#7c7c7c"}, WEISS)
    assert schoenheit.kontrast(r["auf_zweit"], r["zweit"]) >= 4.5


def test_logo_ablegen_fehler_gibt_none_ohne_tmp_rest(tmp_path, monkeypatch):
    """If write/replace fails (disk full, permission, Windows sharing), return None and clean up .tmp."""
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 40).decode()
    g = {"logo": f"data:image/png;base64,{png}"}

    def fail_replace(*args, **kwargs):
        raise OSError("mock write failure")

    monkeypatch.setattr("os.replace", fail_replace)
    result = vm.logo_ablegen(g, "radhaus", str(tmp_path))
    assert result is None
    # No .tmp file should be left behind
    tmp_files = [f for f in tmp_path.iterdir() if f.name.endswith(".tmp")]
    assert not tmp_files


@pytest.mark.skipif(os.name == "nt", reason="chmod ist auf Windows ein No-Op")
def test_logo_datei_lesbar(tmp_path):
    """Logo-Datei hat Modus 0644 (lesbar für andere)."""
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 40).decode()
    g = {"logo": f"data:image/png;base64,{png}"}
    vm.logo_ablegen(g, "radhaus", str(tmp_path))
    datei = list(tmp_path.glob("logo-radhaus-*.png"))[0]
    mode = stat.S_IMODE(os.stat(datei).st_mode)
    assert mode == 0o644


# --- Task 7 Fix-Runde 1 (Controller-Weisung) ---

@pytest.mark.parametrize("vorher,nachher", [("*[Laden]*", "*Radhaus Jena*"), ("[Laden]", "Radhaus Jena"),
                                            ("**[Laden]**", "Radhaus Jena"), ("*", "Radhaus Jena")])
def test_laden_behaelt_kursiv_huelle(vorher, nachher):
    d = copy.deepcopy(DOK)
    d["marke_wort"]["data"]["props"]["text"] = vorher
    assert vm.einsetzen(d, {"laden": "Radhaus Jena"})["marke_wort"]["data"]["props"]["text"] == nachher


def test_kursiv_huelle_nur_fuer_laden():
    d = copy.deepcopy(DOK)
    d["band"]["data"]["style"]["backgroundColor"] = "*x*"
    assert vm.einsetzen(d, {"akzent": "#123456"})["band"]["data"]["style"]["backgroundColor"] == "#123456"


INK = "#080b13"


@pytest.mark.parametrize("akzent", ["#111111", "#000000", "#1d2a44", "#2563eb", "#c2410c"])
def test_dunkler_grund_hellt_dunklen_akzent_auf(akzent):
    r = vm.rollen({"akzent": akzent}, INK)
    assert schoenheit.kontrast(r["akzent"], INK) >= 3
    assert schoenheit.kontrast(r["auf_akzent"], r["akzent"]) >= 3
    assert schoenheit.kontrast(r["akzent_text"], INK) >= 4.5


def test_dunkler_grund_laesst_lesbaren_akzent():
    assert vm.rollen({"akzent": "#b5f750"}, INK)["akzent"] == "#b5f750"


@pytest.mark.parametrize("akzent", ["#facc15", "#111111", "#9ca3af"])
def test_heller_grund_unveraendert(akzent):
    assert vm.rollen({"akzent": akzent}, WEISS)["akzent"] == akzent
    assert vm.rollen({"akzent": akzent}, "#faf7f2")["akzent"] == akzent


def test_logo_grund_und_dunkel():
    dok = {"root": {"type": "EmailLayout", "data": {"canvasColor": "#faf7f2", "childrenIds": ["kopf"]}},
           "kopf": {"type": "Container", "data": {"style": {"backgroundColor": "#080b13"},
                                                   "props": {"childrenIds": ["marke_logo"]}}},
           "marke_logo": {"type": "Image", "data": {"props": {"url": "x"}}}}
    assert vm.logo_grund(dok) == "#080b13" and vm.ist_dunkel("#080b13")
    dok["kopf"]["data"]["style"] = {}
    assert vm.logo_grund(dok) == "#faf7f2" and not vm.ist_dunkel("#faf7f2")
    dok["marke_logo"]["data"]["style"] = {"backgroundColor": "#1A1A1A"}
    assert vm.logo_grund(dok) == "#1a1a1a"
    assert vm.logo_grund({"root": {"data": {}}}) == "#ffffff"
    assert not vm.ist_dunkel("kaputt") and not vm.ist_dunkel(None)


def test_logo_ablegen_fuer_logo_dunkel(tmp_path):
    roh = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    g = {"logo_dunkel": "data:image/png;base64," + base64.b64encode(roh).decode()}
    assert vm.logo_ablegen(g, "radhaus", str(tmp_path), "logo_dunkel").startswith("medien:logo-radhaus-")
    assert vm.logo_ablegen(g, "radhaus", str(tmp_path)) is None


# --- Logo in den Kasten der Vorlage einpassen (contain, Seitenverhaeltnis bleibt) ---

def _png(b, h):
    import io
    from PIL import Image
    puffer = io.BytesIO()
    Image.new("RGBA", (b, h), (10, 20, 30, 255)).save(puffer, "PNG")
    return puffer.getvalue()


def _dok_kasten(b=150, h=50):
    return {"root": {"type": "EmailLayout", "data": {"childrenIds": ["marke_logo"],
                     "rollen": {"marke_logo/data/props/url": "logo"}}},
            "marke_logo": {"type": "Image", "data": {"props": {"url": "x", "width": b, "height": h}}}}


def test_logo_masse_aus_bytes_und_pfad(tmp_path):
    roh = _png(218, 184)
    assert vm.logo_masse(roh) == (218, 184)
    datei = tmp_path / "l.png"
    datei.write_bytes(roh)
    assert vm.logo_masse(str(datei)) == (218, 184)


@pytest.mark.parametrize("quelle", [None, b"kein bild", "/gibt/es/nicht.png", ""])
def test_logo_masse_unlesbar_gibt_none(quelle):
    assert vm.logo_masse(quelle) is None


@pytest.mark.parametrize("masse,erwartet", [((218, 184), (59, 50)), ((600, 100), (150, 25)),
                                            ((100, 100), (50, 50)), ((1, 1000), (1, 50))])
def test_logo_einpassen_contain(masse, erwartet):
    d = vm.logo_einpassen(_dok_kasten(), masse)
    p = d["marke_logo"]["data"]["props"]
    assert (p["width"], p["height"]) == erwartet


def test_logo_einpassen_ohne_masse_oder_kasten_unveraendert():
    dok = _dok_kasten()
    assert vm.logo_einpassen(dok, None) == dok
    ohne = _dok_kasten()
    del ohne["marke_logo"]["data"]["props"]["height"]
    assert vm.logo_einpassen(ohne, (218, 184)) == ohne
    assert dok["marke_logo"]["data"]["props"]["width"] == 150     # Eingabe nie veraendert
