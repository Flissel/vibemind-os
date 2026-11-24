"""Higgsfield-Arbeiter und Programm-Huelle ohne Netz, ohne Programm und ohne Credits: alles Aeussere ist gefaelscht.
Geprueft wird vor allem, WANN bestellt wird - und wann auf keinen Fall."""
import json
from pathlib import Path

import pytest

from spaces.marketing.claw import hf_motive
from spaces.marketing.workers import higgsfield_cli, werkbank_hf
from spaces.marketing.workers.bild_worker import ApiFehler
from spaces.marketing.workers.higgsfield_cli import HfFehler, HfUnklar

AID = "0123abcd-4444-4444-4444-444444444444"
HID = "0123abcd-7777-7777-7777-777777777777"
NUMMER = "8323a0b9-2ef6-4665-89a2-2d20ee770dfb"
START, ENDE = "wb-0123abcd-s02-f0-z.jpg", "wb-0123abcd-s03-f1-a.jpg"
FEHLEND = {"nach_szene": "s3", "nach_nr": 3, "nach_datei": "wb-0123abcd-s03-f1.mp4", "nach_variante": 1,
           "nach_kurz": "Der Parser", "von_szene": "s2", "von_nr": 2, "von_datei": "wb-0123abcd-s02-f0.mp4",
           "von_variante": 0, "von_kurz": "Feste Größe"}
WERK = {"auftrag": AID, "mandant": "fin2gether", "titel": "Chunking erklärt", "thema": "Chunking", "fehlend": [FEHLEND]}


def clip(**anders) -> dict:
    return {"id": HID, "auftrag": AID, "prompt": "Ein Schnitt", "dauer_s": 3, "modell": "kling3_0", "preis": 5.25,
            "start_bild": START, "end_bild": ENDE, "nach_nr": 3, "von_nr": 2, "versuch": 2, "hf_auftrag": None, **anders}


class Api:
    def __init__(self, werke=None, nehmen=None):
        self.rufe, self.werke, self.clip = [], werke or [], nehmen
        self.preis_ok, self.auftrag_ok, self.fertig_ok, self.weiter_ok = True, True, True, True
        self.auftrag_fehler: list = []

    def hf_abgleich(self):
        self.rufe.append(("abgleich",))
        return self.werke

    def holen(self, aid, name, ziel):
        self.rufe.append(("holen", name))
        Path(ziel).write_bytes(b"clip")
        return 4

    def bild_ablegen(self, aid, name, pfad):
        self.rufe.append(("bild_ablegen", name, Path(pfad).read_bytes()))

    def bild_holen(self, aid, name, ziel):
        self.rufe.append(("bild_holen", name))
        Path(ziel).write_bytes(b"bild")
        return 4

    def hf_vorschlag(self, aid, teil):
        self.rufe.append(("vorschlag", aid, teil))
        return "neu"

    def hf_nehmen(self):
        self.rufe.append(("nehmen",))
        return self.clip

    def hf_preis(self, hid, preis):
        self.rufe.append(("preis", hid, preis))
        return self.preis_ok

    def hf_auftrag(self, hid, nummer, preis):
        self.rufe.append(("auftrag", hid, nummer, preis))
        if self.auftrag_fehler:
            raise self.auftrag_fehler.pop(0)
        return self.auftrag_ok

    def hf_weiter(self, hid):
        self.rufe.append(("weiter", hid))
        return self.weiter_ok

    def hf_fertig(self, hid, datei, dauer):
        self.rufe.append(("fertig", hid, datei, dauer))
        return self.fertig_ok

    def hf_fehler(self, hid, grund):
        self.rufe.append(("fehler", hid, grund))
        return True

    def hochladen(self, aid, name, pfad):
        self.rufe.append(("hochladen", name, Path(pfad).read_bytes()))

    def hinweis(self, aid, key, satz):
        self.rufe.append(("hinweis", key, satz))
        return True

    def namen(self):
        return [r[0] for r in self.rufe]


class Cli:
    MODELL = "kling3_0"

    def __init__(self, preis=5.25, nummer=NUMMER, staende=None):
        self.rufe, self._preis, self._nummer = [], preis, nummer
        self.staende = staende or [{"zustand": "fertig", "url": "https://d8j0.cloudfront.net/x.mp4"}]
        self.gesucht = None

    def preis(self, prompt, start, ende, dauer, modell="kling3_0"):
        self.rufe.append(("preis", prompt, Path(start).name, Path(ende).name, dauer, modell))
        if isinstance(self._preis, Exception):
            raise self._preis
        return self._preis

    def bestellen(self, prompt, start, ende, dauer, modell="kling3_0"):
        self.rufe.append(("bestellen", prompt, Path(start).name, Path(ende).name, dauer, modell))
        if isinstance(self._nummer, Exception):
            raise self._nummer
        return self._nummer

    def nummer_suchen(self, prompt, seit, modell="kling3_0"):
        self.rufe.append(("suchen", prompt, seit, modell))
        return self.gesucht

    def stand(self, nummer):
        self.rufe.append(("stand", nummer))
        return self.staende.pop(0) if len(self.staende) > 1 else self.staende[0]

    def bestellt(self):
        return [r for r in self.rufe if r[0] == "bestellen"]


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    uhr = {"t": 0.0}

    def warte(s):
        uhr["t"] += s

    def lauf(args):                       # gefaelschtes ffmpeg: schreibt die Zieldatei
        Path(args[-1]).write_bytes(b"\xff\xd8\xff" + b"bild" if args[-1].endswith(".jpg") else b"video")

    def laden(url, ziel):
        Path(ziel).write_bytes(b"roh")
        return 3

    monkeypatch.setattr(werkbank_hf, "pruefen", lambda p: {"breite": 1920, "hoehe": 1080, "fps": 30, "dauer_s": 3.0, "ton": True}
                        if str(p).endswith(".roh.mp4") else {"breite": 1920, "hoehe": 1080, "fps": 30, "dauer_s": 2.0, "ton": True})
    return {"lauf": lauf, "laden": laden, "warte": warte, "jetzt": lambda: uhr["t"],
            "ordner": lambda aid8: tmp_path / aid8 / "hf", "gedaechtnis": {"geparkt": {}, "versuche": {}}, "tmp": tmp_path}


def durchlauf(api, cli, u):
    return werkbank_hf.ein_durchlauf(api, cli, lauf=u["lauf"], laden=u["laden"], warte=u["warte"], jetzt=u["jetzt"],
                                     ordner=u["ordner"], gedaechtnis=u["gedaechtnis"])


# ─── Vorschlagen: kostet nichts, bestellt nichts ─────────────────────────


def test_vorschlag_zieht_bilder_fragt_den_preis_und_bestellt_nicht(umgebung):
    api, cli = Api(werke=[WERK]), Cli()
    assert durchlauf(api, cli, umgebung) == "vorschlag"
    assert api.namen() == ["abgleich", "holen", "bild_ablegen", "holen", "bild_ablegen", "vorschlag", "nehmen"]
    assert [r[1] for r in api.rufe if r[0] == "holen"] == ["wb-0123abcd-s02-f0.mp4", "wb-0123abcd-s03-f1.mp4"]
    assert [r[1] for r in api.rufe if r[0] == "bild_ablegen"] == [START, ENDE]
    teil = next(r for r in api.rufe if r[0] == "vorschlag")[2]
    assert teil == {"nach_nr": 3, "von_variante": 0, "nach_variante": 1, "start_bild": START, "end_bild": ENDE,
                    "prompt": teil["prompt"], "dauer_s": 3, "preis": 5.25, "modell": "kling3_0"}
    assert "paper cutter" in teil["prompt"]                      # Motiv zu "Chunking"
    assert cli.rufe == [("preis", teil["prompt"], START, ENDE, 3, "kling3_0")] and cli.bestellt() == []
    ordner = umgebung["ordner"]("0123abcd")
    assert sorted(p.name for p in ordner.iterdir()) == [START, ENDE]      # die Szenen-Clips sind wieder weg


def test_vorschlag_ohne_preis_parkt_die_stelle_und_meldet_die_anmeldung(umgebung):
    api, cli = Api(werke=[WERK]), Cli(preis=HfFehler("kein Preis"))
    assert durchlauf(api, cli, umgebung) == "fehler"
    assert "vorschlag" not in api.namen() and ("hinweis", "hf_anmeldung", werkbank_hf.SATZ_ANMELDUNG) in api.rufe
    api.rufe.clear()
    durchlauf(api, cli, umgebung)                                # naechste Runde: die Stelle ruht
    assert api.namen() == ["abgleich", "nehmen"]
    umgebung["warte"](werkbank_hf.PARK_S + 1)
    api.rufe.clear()
    durchlauf(api, cli, umgebung)
    assert "holen" in api.namen()


def test_vorschlag_mit_fremdem_clipnamen_holt_nichts(umgebung):
    werk = {**WERK, "fehlend": [{**FEHLEND, "von_datei": "../../geheim.mp4"}]}
    api, cli = Api(werke=[werk]), Cli()
    assert durchlauf(api, cli, umgebung) == "fehler"
    assert "holen" not in api.namen() and cli.rufe == []


def test_gescheiterter_abgleich_haelt_das_bestellen_nicht_auf(umgebung):
    api, cli = Api(nehmen=clip()), Cli()
    api.hf_abgleich = lambda: (_ for _ in ()).throw(OSError("netz"))
    assert durchlauf(api, cli, umgebung) == "clip"


# ─── Bestellen ───────────────────────────────────────────────────────────


def test_ohne_freigegebenen_clip_wird_nie_bestellt(umgebung):
    api, cli = Api(), Cli()
    assert durchlauf(api, cli, umgebung) == "leer"
    assert cli.rufe == [] and api.namen() == ["abgleich", "nehmen"]


def test_bestellen_ganzer_weg(umgebung):
    api, cli = Api(nehmen=clip()), Cli()
    assert durchlauf(api, cli, umgebung) == "clip"
    assert api.namen() == ["abgleich", "nehmen", "bild_holen", "bild_holen", "preis", "auftrag", "hochladen", "fertig"]
    assert [r[0] for r in cli.rufe] == ["preis", "bestellen", "stand"]
    assert cli.bestellt() == [("bestellen", "Ein Schnitt", START, ENDE, 3, "kling3_0")]
    assert ("preis", HID, 5.25) in api.rufe and ("auftrag", HID, NUMMER, 5.25) in api.rufe
    assert ("fertig", HID, "wb-0123abcd-h03-f2.mp4", 2.0) in api.rufe
    assert next(r for r in api.rufe if r[0] == "hochladen")[1:] == ("wb-0123abcd-h03-f2.mp4", b"video")
    zeilen = (umgebung["tmp"] / "VibeMind" / "werkbank" / "hf-bestellungen.jsonl").read_text(encoding="utf-8").splitlines()
    eintrag = json.loads(zeilen[0])
    assert len(zeilen) == 1 and eintrag["nummer"] == NUMMER and eintrag["preis"] == 5.25 and "prompt" not in eintrag
    assert "Ein Schnitt" not in zeilen[0]
    reste = [p.name for p in umgebung["ordner"]("0123abcd").iterdir()]
    assert sorted(reste) == [START, ENDE]                        # Roh-Clip und Uebergang sind wieder weg


def test_die_nummer_wird_vor_dem_warten_vermerkt(umgebung):
    api = Api(nehmen=clip())
    cli = Cli(staende=[{"zustand": "laeuft", "url": None}, {"zustand": "fertig", "url": "https://x.cloudfront.net/a.mp4"}])
    reihenfolge = []
    alt_auftrag, alt_stand = api.hf_auftrag, cli.stand
    api.hf_auftrag = lambda *a: (reihenfolge.append("auftrag"), alt_auftrag(*a))[1]
    cli.stand = lambda n: (reihenfolge.append("stand"), alt_stand(n))[1]
    assert durchlauf(api, cli, umgebung) == "clip"
    assert reihenfolge[0] == "auftrag" and reihenfolge.count("stand") == 2


def test_anderer_preis_bestellt_nicht(umgebung):
    api, cli = Api(nehmen=clip()), Cli(preis=8.75)
    api.preis_ok = False                                         # die Datenbank lehnt den abweichenden Preis ab
    assert durchlauf(api, cli, umgebung) == "preis"
    assert cli.bestellt() == [] and "auftrag" not in api.namen() and "fehler" not in api.namen()
    assert ("preis", HID, 8.75) in api.rufe


def test_kein_preis_vom_programm_bestellt_nicht_und_meldet_den_fehler(umgebung):
    api, cli = Api(nehmen=clip()), Cli(preis=HfFehler("Higgsfield nannte keinen Preis."))
    assert durchlauf(api, cli, umgebung) == "fehler"
    assert cli.bestellt() == []
    assert api.rufe[-1] == ("fehler", HID, "Nicht bestellt: Higgsfield nannte keinen Preis.")


def test_fremdes_standbild_bestellt_nicht(umgebung):
    api, cli = Api(nehmen=clip(start_bild="wb-99999999-s02-f0-z.jpg")), Cli()
    assert durchlauf(api, cli, umgebung) == "fehler"
    assert cli.rufe == [] and api.rufe[-1][0] == "fehler" and api.rufe[-1][2].startswith("Nicht bestellt")


def test_clip_mit_nummer_wird_nie_neu_bestellt(umgebung):
    api, cli = Api(nehmen=clip(hf_auftrag=NUMMER)), Cli()
    assert durchlauf(api, cli, umgebung) == "clip"
    assert [r[0] for r in cli.rufe] == ["stand"] and cli.rufe[0] == ("stand", NUMMER)
    assert "preis" not in api.namen() and "auftrag" not in api.namen() and "fertig" in api.namen()
    assert not (umgebung["tmp"] / "VibeMind" / "werkbank" / "hf-bestellungen.jsonl").exists()


def test_unklare_bestellung_sucht_die_nummer_und_bestellt_nicht_neu(umgebung):
    api, cli = Api(nehmen=clip()), Cli(nummer=HfUnklar("keine Nummer"))
    cli.gesucht = NUMMER
    assert durchlauf(api, cli, umgebung) == "clip"
    assert len(cli.bestellt()) == 1 and [r[0] for r in cli.rufe] == ["preis", "bestellen", "suchen", "stand"]
    assert ("auftrag", HID, NUMMER, 5.25) in api.rufe


def test_unklare_bestellung_ohne_fund_geht_auf_fehler(umgebung):
    api, cli = Api(nehmen=clip()), Cli(nummer=HfUnklar("keine Nummer"))
    assert durchlauf(api, cli, umgebung) == "fehler"
    assert len(cli.bestellt()) == 1 and api.rufe[-1] == ("fehler", HID, werkbank_hf.SATZ_UNKLAR)
    assert "auftrag" not in api.namen() and "stand" not in [r[0] for r in cli.rufe]


def test_programm_fehlt_ist_sicher_nicht_bestellt(umgebung):
    api, cli = Api(nehmen=clip()), Cli(nummer=HfFehler("Das Higgsfield-Programm wurde nicht gefunden."))
    assert durchlauf(api, cli, umgebung) == "fehler"
    assert api.rufe[-1] == ("fehler", HID, "Nicht bestellt: Das Higgsfield-Programm wurde nicht gefunden.")
    assert "suchen" not in [r[0] for r in cli.rufe]


def test_nummer_meldung_wird_bei_netzfehlern_wiederholt(umgebung):
    api, cli = Api(nehmen=clip()), Cli()
    api.auftrag_fehler = [OSError("netz"), ApiFehler(502, "x")]
    assert durchlauf(api, cli, umgebung) == "clip"
    assert api.namen().count("auftrag") == 3 and len(cli.bestellt()) == 1


def test_nummer_nicht_angenommen_hoert_auf_und_steht_im_tagebuch(umgebung):
    api, cli = Api(nehmen=clip()), Cli()
    api.auftrag_fehler = [ApiFehler(409, "nimmt nicht")]
    assert durchlauf(api, cli, umgebung) == "fehler"
    assert api.namen().count("auftrag") == 1 and "stand" not in [r[0] for r in cli.rufe]
    assert NUMMER in (umgebung["tmp"] / "VibeMind" / "werkbank" / "hf-bestellungen.jsonl").read_text(encoding="utf-8")


def test_higgsfield_meldet_gescheitert(umgebung):
    api, cli = Api(nehmen=clip()), Cli(staende=[{"zustand": "gescheitert", "url": None}])
    assert durchlauf(api, cli, umgebung) == "fehler"
    assert api.rufe[-1] == ("fehler", HID, werkbank_hf.SATZ_HF_GESCHEITERT) and "hochladen" not in api.namen()


def test_warten_laeuft_ab_und_laesst_den_clip_bestellt(umgebung):
    api, cli = Api(nehmen=clip()), Cli(staende=[{"zustand": "laeuft", "url": None}])
    assert durchlauf(api, cli, umgebung) == "wartet"
    assert "fehler" not in api.namen() and "fertig" not in api.namen()
    assert api.namen().count("weiter") >= 10                     # die Leihe wurde beim Warten verlaengert
    assert len(cli.bestellt()) == 1


def test_leihe_verloren_hoert_auf(umgebung):
    api, cli = Api(nehmen=clip()), Cli(staende=[{"zustand": "laeuft", "url": None}])
    api.weiter_ok = False
    assert durchlauf(api, cli, umgebung) == "abgegeben"
    assert api.namen().count("weiter") == 1 and "fehler" not in api.namen()


def test_laden_scheitert_dreimal_dann_fehler(umgebung):
    api, cli = Api(nehmen=clip(hf_auftrag=NUMMER)), Cli()
    umgebung["laden"] = lambda url, ziel: (_ for _ in ()).throw(OSError("netz"))
    assert durchlauf(api, cli, umgebung) == "spaeter"
    assert durchlauf(api, cli, umgebung) == "spaeter"
    assert "fehler" not in api.namen()
    assert durchlauf(api, cli, umgebung) == "fehler"
    assert api.rufe[-1] == ("fehler", HID, werkbank_hf.SATZ_LADEN) and cli.bestellt() == []


def test_falsches_format_nach_dem_angleichen_wird_nicht_hochgeladen(umgebung, monkeypatch):
    monkeypatch.setattr(werkbank_hf, "pruefen", lambda p: {"breite": 1280, "hoehe": 720, "fps": 30, "dauer_s": 2.0, "ton": True})
    api, cli = Api(nehmen=clip(hf_auftrag=NUMMER)), Cli()
    assert durchlauf(api, cli, umgebung) == "spaeter"
    assert "hochladen" not in api.namen() and "fertig" not in api.namen()


# ─── ffmpeg-Argumente und Laden ──────────────────────────────────────────


def test_uebergang_argumente_format_der_szenen():
    a = werkbank_hf.uebergang_argumente(Path("roh.mp4"), Path("ziel.mp4"), 2 / 3)
    assert a[0] == "ffmpeg" and a[-1] == "ziel.mp4"
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in a and "-shortest" in a
    assert a[a.index("-vf") + 1] == "setpts=0.6667*PTS,scale=1920:1080,setsar=1"
    for paar in (["-r", "30"], ["-c:v", "libx264"], ["-pix_fmt", "yuv420p"], ["-c:a", "aac"], ["-ar", "48000"], ["-ac", "2"]):
        assert a[a.index(paar[0]) + 1] == paar[1]


def test_angleichen_beschleunigt_statt_abzuschneiden(umgebung):
    rufe = []
    dauer = werkbank_hf.angleichen(lambda args: rufe.append(args), Path("x.roh.mp4"), umgebung["tmp"] / "z.mp4")
    assert dauer == 2.0 and "setpts=0.6667*PTS" in rufe[0][rufe[0].index("-vf") + 1] and "-t" not in rufe[0]


def test_standbild_argumente():
    assert "-frames:v" in werkbank_hf.standbild_argumente(Path("c.mp4"), Path("a.jpg"), "a")
    z = werkbank_hf.standbild_argumente(Path("c.mp4"), Path("z.jpg"), "z")
    assert z[z.index("-sseof") + 1] == "-0.5" and "-update" in z and z.index("-sseof") < z.index("-i")


@pytest.mark.parametrize("url", ["http://d8j0.cloudfront.net/x.mp4", "https://example.com/x.mp4", "file:///c:/x.mp4",
                                 "https://cloudfront.net.boese.de/x.mp4", "https://127.0.0.1/x.mp4", ""])
def test_url_laden_nur_von_higgsfield(url, tmp_path):
    with pytest.raises(HfFehler):
        werkbank_hf.url_laden(url, tmp_path / "x.mp4")
    assert list(tmp_path.iterdir()) == []


# ─── Programm-Huelle ─────────────────────────────────────────────────────


@pytest.fixture
def lauf(monkeypatch):
    rufe = []
    antwort = {"code": 0, "text": "{}"}
    monkeypatch.setattr(higgsfield_cli, "programm", lambda: "hf.exe")
    monkeypatch.setattr(higgsfield_cli, "_lauf", lambda args, zeit: (rufe.append(args), (antwort["code"], antwort["text"]))[1])
    return rufe, antwort


def test_preis_argumente_text_als_ein_argument(lauf):
    rufe, antwort = lauf
    antwort["text"] = '{"credits": 5.25}'
    text = '--mode 4k "; rm -rf / & echo %PATH%'
    assert higgsfield_cli.preis(text, Path("a.jpg"), Path("b.jpg"), 3) == 5.25
    a = rufe[0]
    assert a[:3] == ["generate", "cost", "kling3_0"] and a[3] == "--prompt=" + text
    assert a[a.index("--start-image") + 1] == "a.jpg" and a[a.index("--end-image") + 1] == "b.jpg"
    assert a[a.index("--duration") + 1] == "3" and a[a.index("--mode") + 1] == "pro" and a[a.index("--sound") + 1] == "off"
    assert a.count("--mode") == 1 and "--wait" not in a and a[-1] == "--json"


@pytest.mark.parametrize("text, code", [("kein json", 0), ('{"credits": 5.25}', 1), ('{"credits": "5"}', 0),
                                        ('{"credits": true}', 0), ('{"credits": -1}', 0), ("[]", 0)])
def test_preis_unbrauchbare_antwort(lauf, text, code):
    lauf[1].update(text=text, code=code)
    with pytest.raises(HfFehler):
        higgsfield_cli.preis("x", Path("a.jpg"), Path("b.jpg"), 3)


@pytest.mark.parametrize("prompt", ["", "   ", "x" * 1001, "zwei\nZeilen", "mit\x00null", None])
def test_ungueltiger_text_startet_das_programm_nicht(lauf, prompt):
    with pytest.raises(HfFehler):
        higgsfield_cli.bestellen(prompt, Path("a.jpg"), Path("b.jpg"), 3)
    assert lauf[0] == []


@pytest.mark.parametrize("text", ['[{"id": "%s", "status": "queued"}]', '{"id": "%s"}', '{"jobs": [{"id": "%s"}]}',
                                  '{"job": {"id": "%s"}}', '{"job_id": "%s"}'])
def test_bestellen_findet_die_nummer(lauf, text):
    lauf[1]["text"] = text % NUMMER
    assert higgsfield_cli.bestellen("x", Path("a.jpg"), Path("b.jpg"), 3) == NUMMER
    a = lauf[0][0]
    assert a[:2] == ["generate", "create"] and "--wait" not in a


@pytest.mark.parametrize("text", ["", "Fehler: not logged in", "{}", "[]", '{"id": "kurz"}', '{"id": 5}'])
def test_bestellen_ohne_nummer_ist_unklar(lauf, text):
    lauf[1]["text"] = text
    with pytest.raises(HfUnklar):
        higgsfield_cli.bestellen("x", Path("a.jpg"), Path("b.jpg"), 3)


def test_bestellen_abgebrochener_lauf_ist_unklar(monkeypatch):
    monkeypatch.setattr(higgsfield_cli, "programm", lambda: "hf.exe")
    monkeypatch.setattr(higgsfield_cli, "_lauf", lambda a, z: (_ for _ in ()).throw(HfFehler("Zeitlimit")))
    with pytest.raises(HfUnklar):
        higgsfield_cli.bestellen("x", Path("a.jpg"), Path("b.jpg"), 3)


def test_bestellen_ohne_programm_ist_sicher_nicht_bestellt(monkeypatch):
    monkeypatch.setattr(higgsfield_cli, "programm", lambda: (_ for _ in ()).throw(HfFehler("fehlt")))
    with pytest.raises(HfFehler) as e:
        higgsfield_cli.bestellen("x", Path("a.jpg"), Path("b.jpg"), 3)
    assert not isinstance(e.value, HfUnklar)


def test_nummer_suchen_nimmt_nur_den_passenden_juengsten_auftrag(lauf):
    def job(nr, prompt, zeit, typ="kling3_0"):
        return {"id": nr, "job_type": typ, "params": {"prompt": prompt}, "created_at": zeit, "status": "queued"}
    lauf[1]["text"] = json.dumps([
        job("alt-00000001", "Text", "2026-11-24T09:00:00.1Z"), job("neu-00000002", "Text", "2026-11-24T10:00:05.5Z"),
        job("neu-00000003", "Text", "2026-11-24T10:00:01.0Z"), job("anders-00004", "Anderer Text", "2026-11-24T10:00:09Z"),
        job("bild-0000005", "Text", "2026-11-24T10:00:08Z", "nano_banana_pro")])
    assert higgsfield_cli.nummer_suchen("Text", "2026-11-24T10:00:00") == "neu-00000002"
    assert higgsfield_cli.nummer_suchen("Text", "2026-11-24T11:00:00") is None
    lauf[1]["text"] = "kaputt"
    assert higgsfield_cli.nummer_suchen("Text", "2026-11-24T10:00:00") is None


@pytest.mark.parametrize("antwort, zustand", [
    ({"status": "completed", "result_url": "https://x/y.mp4"}, "fertig"), ({"status": "completed", "result_url": None}, "laeuft"),
    ({"status": "in_progress"}, "laeuft"), ({"status": "queued"}, "laeuft"), ({"status": "failed"}, "gescheitert"),
    ({"status": "nsfw"}, "gescheitert"), ({"status": "CANCELED"}, "gescheitert")])
def test_stand(lauf, antwort, zustand):
    lauf[1]["text"] = json.dumps(antwort)
    assert higgsfield_cli.stand(NUMMER)["zustand"] == zustand
    assert lauf[0][0] == ["generate", "get", NUMMER, "--json"]


def test_stand_falsche_nummer_startet_nichts(lauf):
    with pytest.raises(HfFehler):
        higgsfield_cli.stand("--help")
    assert lauf[0] == []


def test_programm_lehnt_cmd_ab(monkeypatch, tmp_path):
    cmd = tmp_path / "higgsfield.cmd"
    cmd.write_text("@echo off")
    monkeypatch.setenv("HIGGSFIELD_EXE", str(cmd))
    with pytest.raises(HfFehler):
        higgsfield_cli.programm()
    exe = tmp_path / "hf.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("HIGGSFIELD_EXE", str(exe))
    assert higgsfield_cli.programm() == str(exe)


# ─── Motive ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("texte, wort", [
    (("Chunking erklärt", None, "a", "b"), "paper cutter"), (("MCP in fünf Minuten", None, None, None), "plug"),
    (("Evals", "Messen statt raten", None, None), "measuring tape"), ((None, None, "Embeddings", None), "dots"),
    (("Firmenvorstellung", None, "Wer wir sind", "Was wir tun"), "folds away")])
def test_motiv_nach_stichwort(texte, wort):
    t = hf_motive.auftragstext(*texte)
    assert wort in t and len(t) <= 1000 and "\n" not in t and all(ord(c) >= 32 for c in t)


def test_auftragstext_enthaelt_keine_texte_des_auftrags():
    t = hf_motive.auftragstext("Geheimes Chunking-Projekt Alpha", "Kunde Mustermann", "x", "y")
    assert "Geheim" not in t and "Mustermann" not in t and "Alpha" not in t
