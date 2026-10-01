import base64
import copy

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
