import io

import pytest
from PIL import Image

from spaces.marketing.claw import bild_comfy
from spaces.marketing.workers import bild_worker as bw

DOC = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["kopf", "neben", "t"]}},
       "kopf": {"type": "Image", "data": {"style": {"padding": {"left": 0, "right": 0}},
                "props": {"url": "medien:platzhalter-2x1.png", "width": 600, "height": 300, "alt": "Team"}}},
       "neben": {"type": "Image", "data": {"style": {"padding": {"left": 0, "right": 0}},
                 "props": {"url": "medien:eigen.jpg", "width": 600, "height": 300}}},
       "t": {"type": "Text", "data": {"props": {"text": "Herbst"}}}}


def png(w, h):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (20, 60, 60)).save(b, "PNG")
    return b.getvalue()


ALT = png(64, 32)                          # echtes Quellbild: der Arbeiter normalisiert mit PIL


class Api:
    def __init__(self, auftrag):
        self.auftrag, self.log, self.quellen, self.messung = auftrag, [], {}, None
        self.hochgeladen = []

    def naechster(self):
        a, self.auftrag = self.auftrag, None
        return a

    def weiter(self, aid):
        self.log.append(("weiter", aid)); return True

    def bild(self, aid, platz, daten, format="jpg"):
        if format == "png":
            assert daten[:8] == b"\x89PNG\r\n\x1a\n" and len(daten) <= 4 * 1024 * 1024
            self.hochgeladen.append((aid, platz, format))
            self.log.append(("bild", platz)); return f"nl-{aid[:8]}-{platz}-frei.png"
        assert daten[:3] == b"\xff\xd8\xff" and len(daten) < 1024 * 1024
        self.log.append(("bild", platz)); return f"nl-{aid[:8]}-{platz}.jpg"

    def quelle(self, aid, platz):
        self.log.append(("quelle", platz)); return self.quellen.get(platz)

    def fertig(self, aid, ergebnis, befund, messung=None):
        self.log.append(("fertig", ergebnis, befund)); self.messung = messung; return {"fassung": 2}

    def zurueck(self, aid, befund, endgueltig):
        self.log.append(("zurueck", befund, endgueltig)); return "offen"


class Comfy:
    ComfyFehler = bild_comfy.ComfyFehler

    def __init__(self, laeuft=True, fehler=None):
        self._laeuft, self.fehler, self.masse, self.frei, self.ueber = laeuft, fehler, [], 0, []
        self.freigestellt, self.frei_png = [], None

    def laeuft(self):
        return self._laeuft

    def erzeugen(self, prompt, b, h, seed, zeitlimit_s=300):
        if self.fehler:
            raise self.fehler
        self.masse.append((b, h)); return png(b, h)

    def ueberarbeiten(self, prompt, quelle, b, h, seed, staerke, zeitlimit_s=300):
        self.ueber.append((b, h, staerke, quelle)); return png(b, h)

    def freistellen(self, quelle, zeitlimit_s=300):
        self.freigestellt.append(quelle)
        if self.fehler:
            raise self.fehler
        return self.frei_png

    def freigeben(self):
        self.frei += 1


class Prompt:
    def __init__(self, urteile=None):
        self.urteile, self.nah = list(urteile or []), []

    def laeuft(self):
        return True

    def prompt_schreiben(self, platz, titel, hinweis):
        return f"bild fuer {platz['id']}"

    def bearbeitungs_prompt(self, beschreibung, platz, titel, hinweis, nah=True):
        self.nah.append(nah)
        return f"edit {platz['id']} | {beschreibung}"

    def pruefen(self, png_, prompt):
        return self.urteile.pop(0) if self.urteile else (True, "")


@pytest.fixture(autouse=True)
def _start_zustand(monkeypatch):
    monkeypatch.setattr(bw, "START", {"arbeiter": None, "dienste": None})


AUFTRAG = {"id": "0123abcd-0000-0000-0000-000000000000", "platz": None, "nur_leere": True, "hinweis": "",
           "bloecke": DOC, "titel": "Oktober", "fassung": 1,
           "modus": "neu"}                     # "neu": bestehende Tests rufen nie Sehmodell/CLIP


def test_leer():
    assert bw.ein_durchlauf(Api(None), Comfy(), Prompt(), starten=lambda: None) == "leer"


def test_nur_leere_plaetze_werden_gefuellt():
    api, comfy = Api(dict(AUFTRAG)), Comfy()
    assert bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None) == "fertig"
    assert ("bild", "kopf") in api.log and ("bild", "neben") not in api.log
    assert comfy.masse == [(1200, 608)] and comfy.frei >= 1
    assert api.log[-1] == ("fertig", {"kopf": "nl-0123abcd-kopf.jpg"}, "")


def test_selbstpruefung_zweimal_durchgefallen_dann_gut():
    api, comfy = Api(dict(AUFTRAG)), Comfy()
    bw.ein_durchlauf(api, comfy, Prompt([(False, "Schrift im Bild"), (False, "entstellte Figuren"), (True, "")]),
                     starten=lambda: None)
    assert len(comfy.masse) == 3 and api.log[-1][0] == "fertig"


def test_dreimal_durchgefallen_endgueltig_fehler():
    api = Api(dict(AUFTRAG))
    bw.ein_durchlauf(api, Comfy(), Prompt([(False, "Schrift im Bild")] * 3), starten=lambda: None)
    assert api.log[-1] == ("zurueck", "kopf: Schrift im Bild", True)


class Uhr:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class ZaehlApi(Api):
    def __init__(self, auftrag):
        super().__init__(auftrag)
        self.gefragt = 0

    def naechster(self):
        self.gefragt += 1
        return super().naechster()


def test_dienste_aus_kein_auftrag_genommen_und_start_gedrosselt():
    uhr, gestartet = Uhr(1000.0), []
    api = ZaehlApi(dict(AUFTRAG))

    def lauf():
        return bw.ein_durchlauf(api, Comfy(laeuft=False), Prompt(), starten=lambda: gestartet.append(uhr.t), uhr=uhr)

    assert lauf() == "wartet"                 # Arbeiter gerade gestartet: noch kein Start
    assert gestartet == [] and api.gefragt == 0
    uhr.t += 119
    assert lauf() == "wartet" and gestartet == []
    uhr.t += 2                                # > 120 s nach Arbeiterstart
    assert lauf() == "wartet" and gestartet == [1121.0]
    uhr.t += 599                              # innerhalb von 10 min kein zweiter Start
    assert lauf() == "wartet" and gestartet == [1121.0]
    uhr.t += 1
    assert lauf() == "wartet" and gestartet == [1121.0, 1721.0]
    assert api.gefragt == 0 and api.log == []


def test_ollama_aus_haelt_comfy_laufende_auftraege_nicht_auf():
    """Ollama ist normalerweise aus (Betreiber 24.09.): vor dem Auftrag zaehlt nur ComfyUI."""
    class P(Prompt):
        def laeuft(self):
            raise AssertionError("vor dem Auftrag wird Ollama nicht geprueft")
    api = ZaehlApi(None)
    assert bw.ein_durchlauf(api, Comfy(), P(), starten=lambda: None, uhr=Uhr()) == "leer"
    assert api.gefragt == 1


class OllamaAus(Prompt):
    """Ollama aus; kommt nach `hoch_nach` Abfragen hoch (None = bleibt aus)."""
    def __init__(self, hoch_nach=None):
        super().__init__()
        self.hoch_nach, self.abfragen = hoch_nach, 0

    def laeuft(self):
        self.abfragen += 1
        return self.hoch_nach is not None and self.abfragen > self.hoch_nach


class Schlaf:
    def __init__(self, uhr):
        self.uhr, self.n = uhr, 0

    def __call__(self, s):
        self.n += 1
        self.uhr.t += s


def test_freistellen_laeuft_bei_ollama_aus_ohne_ollama_zu_starten():
    class P(VerbotenPrompt):
        def laeuft(self):
            raise AssertionError("Freistellen fragt Ollama nie")
    comfy, api, start = Comfy(), Api(dict(FREI)), []
    comfy.frei_png = _rgba(0.5)
    api.quellen["neben"] = ALT
    uhr = Uhr()
    r = bw.ein_durchlauf(api, comfy, P(), starten=lambda: None, sehen=VerbotenSehen(), messen=Messen([]),
                         ollama_starten=lambda: start.append(1), uhr=uhr, schlafen=Schlaf(uhr))
    assert r == "fertig" and start == []


def test_neu_bei_ollama_aus_startet_ollama_einmal_und_wartet():
    uhr, start = Uhr(), []
    schlaf, prompt, api = Schlaf(uhr), OllamaAus(hoch_nach=3), Api(dict(AUFTRAG))
    r = bw.ein_durchlauf(api, Comfy(), prompt, starten=lambda: None, uhr=uhr, schlafen=schlaf,
                         ollama_starten=lambda: start.append(1))
    assert r == "fertig" and start == [1] and schlaf.n >= 1
    assert api.log.index(("weiter", AUFTRAG["id"])) < api.log.index(("bild", "kopf"))


def test_neu_ollama_bleibt_aus_gibt_auftrag_zurueck():
    uhr, start = Uhr(), []
    schlaf, api, comfy = Schlaf(uhr), Api(dict(AUFTRAG)), Comfy()
    r = bw.ein_durchlauf(api, comfy, OllamaAus(None), starten=lambda: None, uhr=uhr, schlafen=schlaf,
                         ollama_starten=lambda: start.append(1))
    assert r == "zurueck" and start == [1] and comfy.masse == []
    assert api.log[-1] == ("zurueck", "Ollama nicht erreichbar – Bildbeschreibung nicht moeglich", False)
    assert uhr.t >= bw.OLLAMA_WARTE_S


def test_ueberarbeiten_bei_ollama_aus_startet_ollama():
    uhr, start = Uhr(), []
    api = Api(dict(AUFTRAG, modus="ueberarbeiten"))
    bw.ein_durchlauf(api, Comfy(), OllamaAus(None), starten=lambda: None, uhr=uhr, schlafen=Schlaf(uhr),
                     ollama_starten=lambda: start.append(1))
    assert start == [1] and api.log[-1][0] == "zurueck"


def test_ollama_wartet_nicht_wenn_comfy_aus():
    api = ZaehlApi(dict(AUFTRAG))
    assert bw.ein_durchlauf(api, Comfy(laeuft=False), OllamaAus(), starten=lambda: None, uhr=Uhr()) == "wartet"
    assert api.gefragt == 0


def test_dienste_starten_nicht_blockierend(monkeypatch):
    aufrufe = []

    class P:
        def __init__(self, *a, **k):
            aufrufe.append((a, k))

        def wait(self, *a, **k):
            raise AssertionError("nicht warten")
        communicate = poll = wait

    def kein_run(*a, **k):
        raise AssertionError("subprocess.run blockiert")
    monkeypatch.setattr(bw.subprocess, "Popen", P)
    monkeypatch.setattr(bw.subprocess, "run", kein_run)
    bw.dienste_starten()
    (a, k), = aufrufe
    assert str(bw.STARTER) in a[0]
    assert k["stdin"] == k["stdout"] == k["stderr"] == bw.subprocess.DEVNULL
    assert "capture_output" not in k and "timeout" not in k
    if bw.os.name == "nt":
        assert k["creationflags"] == bw.subprocess.DETACHED_PROCESS | bw.subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert k["start_new_session"] is True


def test_ollama_starten_nimmt_ollama_app_oder_standardpfad(monkeypatch, tmp_path):
    aufrufe = []
    monkeypatch.setattr(bw.subprocess, "Popen", lambda *a, **k: aufrufe.append((a, k)))
    exe = tmp_path / "ollama app.exe"
    monkeypatch.setenv("OLLAMA_APP", str(exe))
    bw.ollama_starten()
    assert aufrufe == []                      # Datei fehlt: nichts tun
    exe.write_bytes(b"")
    bw.ollama_starten()
    (a, k), = aufrufe
    assert a[0] == [str(exe)] and k["stdout"] == bw.subprocess.DEVNULL
    monkeypatch.delenv("OLLAMA_APP")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    ziel = tmp_path / "Programs" / "Ollama"
    ziel.mkdir(parents=True)
    (ziel / "ollama app.exe").write_bytes(b"")
    bw.ollama_starten()
    assert aufrufe[1][0][0] == [str(ziel / "ollama app.exe")]


def test_comfy_fehler_mitten_drin():
    api = Api(dict(AUFTRAG))
    bw.ein_durchlauf(api, Comfy(fehler=bild_comfy.ComfyFehler("Zeitlimit 300 s")), Prompt(), starten=lambda: None)
    assert api.log[-1][0] == "zurueck" and "Zeitlimit" in api.log[-1][1] and api.log[-1][2] is False


def test_einzelner_platz_auch_wenn_belegt():
    a = dict(AUFTRAG, platz="neben", nur_leere=False, hinweis="waermer")
    api = Api(a)
    bw.ein_durchlauf(api, Comfy(), Prompt(), starten=lambda: None)
    assert ("bild", "neben") in api.log and ("bild", "kopf") not in api.log


def test_kein_passender_platz_ist_fehler_mit_befund():
    a = dict(AUFTRAG, platz="gibtsnicht", nur_leere=False)
    api = Api(a)
    bw.ein_durchlauf(api, Comfy(), Prompt(), starten=lambda: None)
    assert api.log[-1] == ("zurueck", "Keine passenden Bildplaetze", True)


def test_fertig_abgelehnt_weil_verworfen_ist_kein_absturz():
    class Verworfen(Api):
        def fertig(self, aid, ergebnis, befund, messung=None):
            raise bw.ApiFehler(422, "Auftrag ist nicht in Arbeit (verworfen)")
    assert bw.ein_durchlauf(Verworfen(dict(AUFTRAG)), Comfy(), Prompt(), starten=lambda: None) == "verworfen"


def test_fertig_abgelehnt_serverwortlaut_nicht_mehr_in_arbeit():
    class Verworfen(Api):
        def fertig(self, aid, ergebnis, befund, messung=None):
            raise bw.ApiFehler(422, "Auftrag ist nicht (mehr) in Arbeit")
    api = Verworfen(dict(AUFTRAG))
    assert bw.ein_durchlauf(api, Comfy(), Prompt(), starten=lambda: None) == "verworfen"
    assert not [e for e in api.log if e[0] == "zurueck"]


def test_bild_422_nicht_mehr_in_arbeit_ist_verworfen_ohne_zurueck():
    class A(Api):
        def bild(self, aid, platz, jpeg):
            raise bw.ApiFehler(422, "Auftrag ist nicht (mehr) in Arbeit")
    api = A(dict(AUFTRAG))
    assert bw.ein_durchlauf(api, Comfy(), Prompt(), starten=lambda: None) == "verworfen"
    assert not [e for e in api.log if e[0] in ("zurueck", "fertig")]


def test_bild_422_anderer_grund_ueberspringt_nur_den_platz():
    zwei = dict(AUFTRAG, nur_leere=False)       # kopf und neben
    class A(Api):
        def bild(self, aid, platz, jpeg):
            if platz == "kopf":
                raise bw.ApiFehler(422, "Bildplatz gibt es nicht")
            return super().bild(aid, platz, jpeg)
    api = A(zwei)
    assert bw.ein_durchlauf(api, Comfy(), Prompt(), starten=lambda: None) == "fertig"
    assert api.log[-1][0] == "fertig" and api.log[-1][1] == {"neben": "nl-0123abcd-neben.jpg"}
    assert "kopf: Bildplatz gibt es nicht" in api.log[-1][2]


def test_weiter_false_ist_verworfen_ohne_weitere_erzeugung():
    class A(Api):
        def weiter(self, aid):
            self.log.append(("weiter", aid)); return False
    api, comfy = A(dict(AUFTRAG)), Comfy()
    assert bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None) == "verworfen"
    assert comfy.masse == [] and not [e for e in api.log if e[0] in ("zurueck", "fertig", "bild")]


def test_weiter_false_vor_dem_abliefern_ist_verworfen():
    class A(Api):
        def weiter(self, aid):
            self.log.append(("weiter", aid))
            return len([e for e in self.log if e[0] == "weiter"]) < 2
    api, comfy = A(dict(AUFTRAG)), Comfy()
    assert bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None) == "verworfen"
    assert len(comfy.masse) == 1 and not [e for e in api.log if e[0] in ("bild", "zurueck", "fertig")]


def test_prompt_valueerror_wird_zurueck_nicht_endgueltig():
    class P(Prompt):
        def prompt_schreiben(self, platz, titel, hinweis):
            raise ValueError("Expecting value: line 1 column 1")
    api = Api(dict(AUFTRAG))
    assert bw.ein_durchlauf(api, Comfy(), P(), starten=lambda: None) == "zurueck"
    assert api.log[-1][0] == "zurueck" and api.log[-1][2] is False and "Expecting value" in api.log[-1][1]


def test_api_fehler_500_und_kaputtes_png_werden_zurueck():
    class A(Api):
        def bild(self, aid, platz, jpeg):
            raise bw.ApiFehler(500, "Internal Server Error")
    api = A(dict(AUFTRAG))
    assert bw.ein_durchlauf(api, Comfy(), Prompt(), starten=lambda: None) == "zurueck"
    assert api.log[-1][0] == "zurueck" and api.log[-1][2] is False

    class KaputtComfy(Comfy):
        def erzeugen(self, prompt, b, h, seed, zeitlimit_s=300):
            return b"kein-png-kaputt"
    api = Api(dict(AUFTRAG))
    assert bw.ein_durchlauf(api, KaputtComfy(), Prompt(), starten=lambda: None) == "zurueck"
    assert api.log[-1][0] == "zurueck" and api.log[-1][2] is False


def test_freigeben_auch_wenn_erzeugen_scheitert():
    comfy = Comfy(fehler=bild_comfy.ComfyFehler("Zeitlimit"))
    bw.ein_durchlauf(Api(dict(AUFTRAG)), comfy, Prompt(), starten=lambda: None)
    assert comfy.frei == 1


def test_verkleinern_trifft_masse_und_groesse():
    j = bw.verkleinern(png(1216, 624), 1200, 608)
    with Image.open(io.BytesIO(j)) as b:
        assert b.format == "JPEG" and b.size == (1200, 608)
    assert len(j) <= 250 * 1024


def test_weiter_vor_jedem_versuch_und_zeitlimit_540():
    zeitlimits = []

    class C(Comfy):
        def erzeugen(self, prompt, b, h, seed, zeitlimit_s=300):
            zeitlimits.append(zeitlimit_s)
            return super().erzeugen(prompt, b, h, seed, zeitlimit_s)

    api = Api(dict(AUFTRAG))
    bw.ein_durchlauf(api, C(), Prompt([(False, "x"), (False, "y"), (True, "")]), starten=lambda: None)
    # vor jedem Versuch und noch einmal vor dem Abliefern
    assert [e for e in api.log if e[0] == "weiter"] == [("weiter", AUFTRAG["id"])] * 4
    assert zeitlimits == [540, 540, 540]


def test_tls_kontext_nimmt_certifi_wenn_vorhanden(monkeypatch):
    """Die Tailnet-Kette der VM endet fuer Windows-Python ueber eine abgelaufene
    Querzertifizierung; mit dem certifi-Buendel geht es (gemessen 30.09.)."""
    import ssl
    import sys
    import types
    geladen = []
    falsch = types.SimpleNamespace(where=lambda: "C:/certifi/cacert.pem")
    monkeypatch.setitem(sys.modules, "certifi", falsch)
    monkeypatch.setattr(ssl, "create_default_context", lambda cafile=None: geladen.append(cafile) or "ctx")
    assert bw.tls_kontext() == "ctx" and geladen == ["C:/certifi/cacert.pem"]
    monkeypatch.setitem(sys.modules, "certifi", None)          # import certifi -> ImportError
    assert bw.tls_kontext() == "ctx" and geladen[-1] is None


def test_modelle_einmal_je_auftrag_laden(monkeypatch):
    """Gemessen 30.09.: ~118 s von 124 s je Bild waren Modell-Laden von der HDD.
    Ohne Selbstpruefung: erst alle Prompts, dann alle Bilder, EIN /free am Ende."""
    monkeypatch.delenv("BILD_SELBSTPRUEFUNG", raising=False)
    folge = []

    class C(Comfy):
        def erzeugen(self, prompt, b, h, seed, zeitlimit_s=300):
            folge.append("bild")
            return super().erzeugen(prompt, b, h, seed, zeitlimit_s)

        def freigeben(self):
            folge.append("frei")

    class P(Prompt):
        def prompt_schreiben(self, platz, titel, hinweis):
            folge.append("prompt")
            return super().prompt_schreiben(platz, titel, hinweis)

    a = dict(AUFTRAG, nur_leere=False)                       # beide Plaetze kopf + neben
    assert bw.ein_durchlauf(Api(a), C(), P(), starten=lambda: None) == "fertig"
    assert folge == ["prompt", "prompt", "bild", "bild", "frei"]


def test_mit_selbstpruefung_je_bild_freigeben(monkeypatch):
    monkeypatch.setenv("BILD_SELBSTPRUEFUNG", "1")
    comfy = Comfy()
    bw.ein_durchlauf(Api(dict(AUFTRAG, nur_leere=False)), comfy, Prompt(), starten=lambda: None)
    assert comfy.frei == 3                                     # je Bild + einmal am Ende


class Sehen:
    def __init__(self, text="a skyline"):
        self.text, self.gesehen = text, []

    def beschreiben(self, bild):
        self.gesehen.append(bild)
        return self.text


class Messen:
    def __init__(self, werte):
        self.werte = list(werte)

    def messen(self, alt, neu, hinweis):
        return self.werte.pop(0) if self.werte else {"aehnlich_original": 0.9, "naeher_am_hinweis": 0.02}


UEBER = dict(AUFTRAG, platz="neben", nur_leere=False, hinweis="waermer", staerke=40, modus="ueberarbeiten")


def test_ueberarbeiten_ablauf_und_messung():
    api, comfy, sehen = Api(dict(UEBER)), Comfy(), Sehen()
    api.quellen["neben"] = ALT
    assert bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None, sehen=sehen,
                            messen=Messen([{"aehnlich_original": 0.88, "naeher_am_hinweis": 0.04}])) == "fertig"
    # Betreiber-Entscheid 30.09. "neu mit Motiv": Text-zu-Bild in Platzmassen, nie Bild-zu-Bild
    assert [Image.open(io.BytesIO(b)).size for b in sehen.gesehen] == [(64, 32)] and comfy.masse == [(1200, 608)] and comfy.ueber == []
    assert api.messung == {"neben": {"aehnlich_original": 0.88, "naeher_am_hinweis": 0.04}}


def test_leerer_platz_wird_neu_erzeugt_ohne_quelle():
    a = dict(UEBER, platz="kopf")                          # kopf ist Platzhalter = leer
    api, comfy = Api(a), Comfy()
    bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None, sehen=Sehen(), messen=Messen([]))
    assert comfy.ueber == [] and comfy.masse == [(1200, 608)] and ("quelle", "kopf") not in api.log


def test_staerke_100_ist_neu():
    api, comfy = Api(dict(UEBER, staerke=100)), Comfy()
    api.quellen["neben"] = ALT
    bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None, sehen=Sehen(), messen=Messen([]))
    assert comfy.ueber == [] and len(comfy.masse) == 1


def test_quelle_fehlt_neu_mit_befund():
    api, comfy = Api(dict(UEBER)), Comfy()                  # quellen leer -> None
    bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None, sehen=Sehen(), messen=Messen([]))
    assert comfy.ueber == [] and len(comfy.masse) == 1
    assert "neben: Quellbild fehlt - neu erzeugt" in api.log[-1][2]


def test_ohne_beschreibung_mit_befund():
    api, comfy = Api(dict(UEBER)), Comfy()
    api.quellen["neben"] = ALT
    bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None, sehen=Sehen(""), messen=Messen([]))
    assert len(comfy.masse) == 1 and comfy.ueber == [] and "neben: ohne Bildbeschreibung" in api.log[-1][2]


def test_zu_unaehnlich_wiederholen_dann_bestes():
    api, comfy = Api(dict(UEBER)), Comfy()
    api.quellen["neben"] = ALT
    werte = [{"aehnlich_original": 0.5, "naeher_am_hinweis": 0.1}, {"aehnlich_original": 0.7, "naeher_am_hinweis": 0.1},
             {"aehnlich_original": 0.6, "naeher_am_hinweis": 0.1}]
    assert bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None, sehen=Sehen(), messen=Messen(werte)) == "fertig"
    assert len(comfy.masse) == 3 and comfy.ueber == [] and api.messung["neben"]["aehnlich_original"] == 0.7
    assert "Aehnlichkeit 0.60 unter 0.75 - bestes Bild (0.70) genommen" in api.log[-1][2]


def test_hohe_staerke_keine_aehnlichkeitsschwelle():
    api, comfy = Api(dict(UEBER, staerke=80)), Comfy()
    api.quellen["neben"] = ALT
    bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None, sehen=Sehen(),
                     messen=Messen([{"aehnlich_original": 0.4, "naeher_am_hinweis": 0.2}]))
    assert len(comfy.masse) == 1 and comfy.ueber == [] and api.log[-1][2] == ""


def test_ueberarbeiten_ohne_hinweis():
    api, comfy = Api(dict(UEBER, hinweis="")), Comfy()
    api.quellen["neben"] = ALT
    assert bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None, sehen=Sehen(),
                            messen=Messen([{"aehnlich_original": 0.9, "naeher_am_hinweis": None}])) == "fertig"
    assert api.messung["neben"]["naeher_am_hinweis"] is None


def test_erst_sehen_dann_flux_einmal_freigeben(monkeypatch):
    monkeypatch.delenv("BILD_SELBSTPRUEFUNG", raising=False)
    folge = []

    class S(Sehen):
        def beschreiben(self, bild):
            folge.append("sehen")
            return "x"

    class C(Comfy):
        def ueberarbeiten(self, *a, **k):
            folge.append("flux")
            return super().ueberarbeiten(*a, **k)

        def erzeugen(self, *a, **k):
            folge.append("flux")
            return super().erzeugen(*a, **k)

        def freigeben(self):
            folge.append("frei")

    api = Api(dict(UEBER, platz=None))                     # kopf (leer, neu) + neben (ueberarbeiten)
    api.quellen["neben"] = ALT
    bw.ein_durchlauf(api, C(), Prompt(), starten=lambda: None, sehen=S(), messen=Messen([]))
    assert folge == ["sehen", "flux", "flux", "frei"]


@pytest.mark.parametrize("staerke, nah", [(0, True), (40, True), (60, True), (61, False), (99, False)])
def test_nah_bis_grenze_staerke(staerke, nah):
    api, prompt = Api(dict(UEBER, staerke=staerke)), Prompt()
    api.quellen["neben"] = ALT
    bw.ein_durchlauf(api, Comfy(), prompt, starten=lambda: None, sehen=Sehen(), messen=Messen([]))
    assert prompt.nah == [nah]


def test_selbstpruefung_notiz_bleibt_befund_beim_ueberarbeiten():
    """Wie bisher beim Neu-Erzeugen: 'ungeprueft eingesetzt' geht nicht verloren."""
    api = Api(dict(UEBER))
    api.quellen["neben"] = ALT
    bw.ein_durchlauf(api, Comfy(), Prompt([(True, "Selbstpruefung unlesbar - ungeprueft eingesetzt")]),
                     starten=lambda: None, sehen=Sehen(), messen=Messen([]))
    assert api.log[-1][0] == "fertig" and "neben: Selbstpruefung unlesbar" in api.log[-1][2]


def test_umgebung_laden_holt_auch_fastembed_cache(monkeypatch, tmp_path):
    """Ohne FASTEMBED_CACHE_PATH laedt CLIP bei jedem Start neu in ein Temp-Verzeichnis."""
    import os
    (tmp_path / ".env").write_text('MARKETING_BILD_URL=https://vm\nMARKETING_BILD_KEY="k"\n'
                                   "FASTEMBED_CACHE_PATH=E:/cache/fastembed\n", encoding="utf-8")
    monkeypatch.setattr(bw, "REPO_ROOT", tmp_path)
    for k in ("MARKETING_BILD_URL", "MARKETING_BILD_KEY", "FASTEMBED_CACHE_PATH"):
        monkeypatch.delenv(k, raising=False)
    bw.umgebung_laden()
    assert os.environ["FASTEMBED_CACHE_PATH"] == "E:/cache/fastembed" and os.environ["MARKETING_BILD_KEY"] == "k"
    monkeypatch.delenv("FASTEMBED_CACHE_PATH")            # Marketing-Schluessel schon gesetzt
    bw.umgebung_laden()
    assert os.environ["FASTEMBED_CACHE_PATH"] == "E:/cache/fastembed"


DOC2 = dict(DOC, root={"type": "EmailLayout", "data": {"childrenIds": ["neben", "unten", "t"]}},
            unten=DOC["neben"])                          # zwei belegte Plaetze


def test_quelle_422_nicht_mehr_in_arbeit_ist_verworfen():
    class A(Api):
        def quelle(self, aid, platz):
            raise bw.ApiFehler(422, "Auftrag ist nicht (mehr) in Arbeit")
    api, comfy = A(dict(UEBER)), Comfy()
    assert bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None, sehen=Sehen(), messen=Messen([])) == "verworfen"
    assert comfy.masse == [] and not [e for e in api.log if e[0] in ("zurueck", "fertig", "bild")]


def test_weiter_vor_jeder_quelle_in_phase_1():
    api = Api(dict(UEBER, platz=None, bloecke=DOC2))
    api.quellen.update(neben=ALT, unten=ALT)
    assert bw.ein_durchlauf(api, Comfy(), Prompt(), starten=lambda: None, sehen=Sehen(), messen=Messen([])) == "fertig"
    phase1 = [e[0] for e in api.log][:4]
    assert phase1 == ["weiter", "quelle", "weiter", "quelle"]


def test_weiter_false_in_phase_1_ist_verworfen_ohne_erzeugen():
    class A(Api):
        def weiter(self, aid):
            self.log.append(("weiter", aid)); return False
    api, comfy, sehen = A(dict(UEBER)), Comfy(), Sehen()
    api.quellen["neben"] = ALT
    assert bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None, sehen=sehen, messen=Messen([])) == "verworfen"
    assert comfy.masse == [] and sehen.gesehen == [] and ("quelle", "neben") not in api.log
    assert not [e for e in api.log if e[0] in ("zurueck", "fertig", "bild")]


def test_bestes_bild_befund_nennt_aehnlichkeit_nicht_selbstpruefung():
    api = Api(dict(UEBER))
    api.quellen["neben"] = ALT
    bw.ein_durchlauf(api, Comfy(), Prompt([(True, ""), (False, "Schrift im Bild"), (False, "Schrift im Bild")]),
                     starten=lambda: None, sehen=Sehen(),
                     messen=Messen([{"aehnlich_original": 0.5, "naeher_am_hinweis": 0.1}]))
    befund = api.log[-1][2]
    assert api.log[-1][0] == "fertig" and "Schrift im Bild - bestes" not in befund
    assert "neben: Aehnlichkeit 0.50 unter 0.75 - bestes Bild (0.50) genommen" in befund


def test_ohne_messung_befund_bei_niedriger_staerke():
    api = Api(dict(UEBER))
    api.quellen["neben"] = ALT
    assert bw.ein_durchlauf(api, Comfy(), Prompt(), starten=lambda: None, sehen=Sehen(),
                            messen=Messen([{}])) == "fertig"
    assert "neben: ohne Messung" in api.log[-1][2] and api.messung == {}


def test_quelle_zu_gross_neu_erzeugt():
    api, comfy, sehen = Api(dict(UEBER)), Comfy(), Sehen()
    api.quellen["neben"] = b"x" * (bw.QUELLE_MAX + 1)
    bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None, sehen=sehen, messen=Messen([]))
    assert sehen.gesehen == [] and len(comfy.masse) == 1
    assert "neben: Quellbild zu gross - neu erzeugt" in api.log[-1][2]


def test_arbeiter_api_quelle_422_grund_und_groessendeckel(monkeypatch):
    import urllib.error
    gelesen = []

    def urlopen(req, timeout=None, context=None):
        if "platz=weg" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 422, "x", {},
                                         io.BytesIO(b'{"detail": "Auftrag ist nicht (mehr) in Arbeit"}'))
        if "platz=kaputt" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 500, "x", {}, io.BytesIO(b"<html>"))
        return _Antwort(b"y" * (bw.QUELLE_MAX + 50), gelesen)

    monkeypatch.setattr(bw.urllib.request, "urlopen", urlopen)
    api = bw.ArbeiterApi("https://vm", "k")
    with pytest.raises(bw.ApiFehler) as e:
        api.quelle("a1", "weg")
    assert e.value.code == 422 and e.value.grund == "Auftrag ist nicht (mehr) in Arbeit"
    with pytest.raises(bw.ApiFehler) as e:
        api.quelle("a1", "kaputt")
    assert e.value.code == 500 and e.value.grund == "Quellbild nicht abrufbar"
    assert len(api.quelle("a1", "gross")) == bw.QUELLE_MAX + 1 and gelesen == [bw.QUELLE_MAX + 1]


class _Antwort:
    def __init__(self, rumpf, gelesen=None):
        self.rumpf, self.gelesen = rumpf, gelesen

    def read(self, n=-1):
        if self.gelesen is not None:
            self.gelesen.append(n)
        return self.rumpf if n is None or n < 0 else self.rumpf[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_arbeiter_api_quelle_404_ist_none_und_fertig_mit_messung(monkeypatch):
    import json
    import urllib.error
    gesehen = []

    def urlopen(req, timeout=None, context=None):
        gesehen.append(req)
        if "/quelle" in req.full_url:
            if "platz=fehlt" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
            return _Antwort(b"ALT")
        return _Antwort(b"{}")

    monkeypatch.setattr(bw.urllib.request, "urlopen", urlopen)
    api = bw.ArbeiterApi("https://vm/", "geheim")
    assert api.quelle("a1", "neben") == b"ALT" and api.quelle("a1", "fehlt") is None
    assert gesehen[0].full_url == "https://vm/api/bilder/arbeiter/a1/quelle?platz=neben"
    assert gesehen[0].unredirected_hdrs.get("X-bild-key") == "geheim"
    api.fertig("a1", {"neben": "x.jpg"}, "", {"neben": {"aehnlich_original": 0.9}})
    assert json.loads(gesehen[-1].data)["messung"] == {"neben": {"aehnlich_original": 0.9}}
    api.fertig("a1", {}, "")
    assert json.loads(gesehen[-1].data)["messung"] == {}


class MerkMessen(Messen):
    def __init__(self, werte=()):
        super().__init__(werte)
        self.alt = []

    def messen(self, alt, neu, hinweis):
        self.alt.append(alt)
        return super().messen(alt, neu, hinweis)


class MerkComfy(Comfy):
    def __init__(self):
        super().__init__()
        self.prompts = []

    def erzeugen(self, prompt, b, h, seed, zeitlimit_s=300):
        self.prompts.append(prompt)
        return super().erzeugen(prompt, b, h, seed, zeitlimit_s)


def _grosses_jpeg() -> bytes:
    import os
    roh = os.urandom(2000 * 1500 * 3)
    b = io.BytesIO()
    Image.frombytes("RGB", (2000, 1500), roh).save(b, "JPEG", quality=95)
    return b.getvalue()


def test_quelle_bis_16_mb():
    assert bw.QUELLE_MAX == 16 * 1024 * 1024


def test_grosse_jpeg_quelle_wird_am_pc_normalisiert():
    gross = _grosses_jpeg()
    assert 1024 * 1024 < len(gross) <= bw.QUELLE_MAX            # frueher: "zu gross"
    api, sehen, messen = Api(dict(UEBER)), Sehen(), MerkMessen()
    api.quellen["neben"] = gross
    assert bw.ein_durchlauf(api, Comfy(), Prompt(), starten=lambda: None, sehen=sehen, messen=messen) == "fertig"
    assert len(sehen.gesehen) == 1 and sehen.gesehen[0][:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(io.BytesIO(sehen.gesehen[0])) as bild:
        assert max(bild.size) <= 1536 and bild.size == (1536, 1152) and bild.mode == "RGB"
    assert messen.alt == [sehen.gesehen[0]]                    # CLIP misst dieselben Bytes
    assert "zu gross" not in api.log[-1][2] and "unlesbar" not in api.log[-1][2]


def test_kleine_quelle_wird_nicht_vergroessert_aber_rgb_png():
    b = io.BytesIO()
    Image.new("P", (300, 200)).save(b, "GIF")
    api, sehen = Api(dict(UEBER)), Sehen()
    api.quellen["neben"] = b.getvalue()
    bw.ein_durchlauf(api, Comfy(), Prompt(), starten=lambda: None, sehen=sehen, messen=Messen([]))
    with Image.open(io.BytesIO(sehen.gesehen[0])) as bild:
        assert bild.format == "PNG" and bild.size == (300, 200) and bild.mode == "RGB"


def test_unlesbare_quelle_neu_erzeugt_mit_befund():
    api, comfy, sehen, messen = Api(dict(UEBER)), MerkComfy(), Sehen(), MerkMessen()
    api.quellen["neben"] = b"\xff\xd8\xffkein bild"
    assert bw.ein_durchlauf(api, comfy, Prompt(), starten=lambda: None, sehen=sehen, messen=messen) == "fertig"
    assert sehen.gesehen == [] and messen.alt == [] and comfy.prompts == ["bild fuer neben"]
    assert "neben: Quellbild unlesbar - neu erzeugt" in api.log[-1][2]


def test_dekompressionsbombe_ist_unlesbar(monkeypatch):
    echt, aufrufe = Image.open, []

    def bombe(*a, **k):                                   # nur die Quelle ist die Bombe
        aufrufe.append(1)
        if len(aufrufe) == 1:
            raise Image.DecompressionBombError("zu viele Pixel")
        return echt(*a, **k)
    monkeypatch.setattr(Image, "open", bombe)
    api, sehen = Api(dict(UEBER)), Sehen()
    api.quellen["neben"] = ALT
    bw.ein_durchlauf(api, Comfy(), Prompt(), starten=lambda: None, sehen=sehen, messen=Messen([]))
    assert sehen.gesehen == [] and "neben: Quellbild unlesbar - neu erzeugt" in api.log[-1][2]


def test_ohne_beschreibung_prompt_aus_alt_kontext_hinweis():
    """Spec §7: scheitert das Sehen, schreibt der Arbeiter den Prompt wie beim
    Neu-Erzeugen (alt + Kontext + Hinweis), nicht aus einer leeren Beschreibung."""
    api, comfy, prompt = Api(dict(UEBER)), MerkComfy(), Prompt()
    api.quellen["neben"] = ALT
    bw.ein_durchlauf(api, comfy, prompt, starten=lambda: None, sehen=Sehen(""), messen=Messen([]))
    assert comfy.prompts == ["bild fuer neben"] and prompt.nah == []
    assert "neben: ohne Bildbeschreibung" in api.log[-1][2]


# --- Freistellen (Newsletter-Bild ohne Hintergrund) ---------------------------------

def _rgba(anteil: float, groesse=(100, 100)) -> bytes:
    bild = Image.new("RGBA", groesse, (0, 0, 0, 0))
    voll = int(groesse[0] * groesse[1] * anteil)
    px = bild.load()
    for i in range(voll):
        px[i % groesse[0], i // groesse[0]] = (200, 100, 50, 255)
    b = io.BytesIO()
    bild.save(b, "PNG")
    return b.getvalue()


FREI = dict(AUFTRAG, platz="neben", nur_leere=False, staerke=0, modus="freistellen")


class VerbotenPrompt(Prompt):
    def prompt_schreiben(self, *a, **k):
        raise AssertionError("Freistellen braucht keinen Prompt")

    bearbeitungs_prompt = pruefen = prompt_schreiben


class VerbotenSehen:
    def beschreiben(self, bild):
        raise AssertionError("Freistellen braucht kein Sehmodell")


def _frei_lauf(api, comfy):
    def kein_flux(*a, **k):
        raise AssertionError("kein FLUX beim Freistellen")
    comfy.erzeugen = comfy.ueberarbeiten = kein_flux
    return bw.ein_durchlauf(api, comfy, VerbotenPrompt(), starten=lambda: None, sehen=VerbotenSehen(),
                            messen=Messen([]))


def test_vordergrund_anteil():
    assert abs(bw.vordergrund_anteil(_rgba(0.5)) - 0.5) < 0.02


def test_png_passend_laesst_kleines_unveraendert():
    roh = _rgba(0.5)
    assert bw.png_passend(roh) is roh


def test_png_passend_verkleinert_grosses():
    gross = Image.effect_noise((2400, 1600), 80).convert("RGBA")
    b = io.BytesIO()
    gross.save(b, "PNG")
    roh = b.getvalue()
    klein = bw.png_passend(roh, grenze=len(roh) - 1)
    with Image.open(io.BytesIO(klein)) as k:
        assert max(k.size) == 1600 and k.mode == "RGBA"


@pytest.mark.parametrize("anteil,erwartet", [(0.5, "fertig"), (0.005, "zurueck"), (0.995, "zurueck")])
def test_freistellen_qualitaetsregel(anteil, erwartet):
    api, comfy = Api(dict(FREI)), Comfy()
    api.quellen["neben"] = ALT
    comfy.frei_png = _rgba(anteil)
    assert _frei_lauf(api, comfy) == erwartet
    assert comfy.frei == 1
    if erwartet == "fertig":
        assert api.hochgeladen == [(FREI["id"], "neben", "png")]
        assert api.log[-1][:2] == ("fertig", {"neben": "nl-0123abcd-neben-frei.png"})
        with Image.open(io.BytesIO(comfy.freigestellt[0])) as q:      # normalisierte Quelle
            assert q.mode == "RGB" and q.size == (64, 32)
    else:
        assert api.hochgeladen == [] and api.log[-1] == ("zurueck", "Kein klares Motiv gefunden", True)


def test_freistellen_ohne_modell_gibt_zurueck():
    api = Api(dict(FREI))
    api.quellen["neben"] = ALT
    comfy = Comfy(fehler=bild_comfy.ComfyFehler("Erzeugung in ComfyUI fehlgeschlagen"))
    assert _frei_lauf(api, comfy) == "zurueck"
    assert api.log[-1][0] == "zurueck" and "Freistellen nicht verfügbar" in api.log[-1][1]
    assert api.log[-1][2] is True and comfy.frei == 1


def test_freistellen_quelle_fehlt_oder_unlesbar():
    for roh in (None, b"\xff\xd8\xffkein bild"):
        api, comfy = Api(dict(FREI)), Comfy()
        api.quellen["neben"] = roh
        assert _frei_lauf(api, comfy) == "zurueck"
        assert api.log[-1] == ("zurueck", "neben: Quellbild fehlt oder unlesbar", True)
        assert comfy.freigestellt == []


def test_freistellen_platzhalter_ist_fehler():
    api, comfy = Api(dict(FREI, platz="kopf")), Comfy()           # kopf = Platzhalter
    assert _frei_lauf(api, comfy) == "zurueck"
    assert api.log[-1][1] == "Freistellen braucht einen Bildplatz mit Bild" and comfy.freigestellt == []


def test_freistellen_weiter_false_ist_verworfen():
    class A(Api):
        def weiter(self, aid):
            self.log.append(("weiter", aid)); return False
    api, comfy = A(dict(FREI)), Comfy()
    assert _frei_lauf(api, comfy) == "verworfen" and comfy.freigestellt == []
    assert not [e for e in api.log if e[0] in ("zurueck", "fertig", "bild")]


def test_freistellen_png_zu_gross_auch_nach_verkleinern(monkeypatch):
    monkeypatch.setattr(bw, "PNG_MAX", 10)
    api, comfy = Api(dict(FREI)), Comfy()
    api.quellen["neben"] = ALT
    comfy.frei_png = _rgba(0.5)
    assert _frei_lauf(api, comfy) == "zurueck"
    assert api.log[-1] == ("zurueck", "neben: freigestelltes Bild zu groß", True)


def test_arbeiter_api_bild_format_png_und_jpg(monkeypatch):
    gesehen = []

    def urlopen(req, timeout=None, context=None):
        gesehen.append(req)
        return _Antwort(b'{"name": "x"}')

    monkeypatch.setattr(bw.urllib.request, "urlopen", urlopen)
    api = bw.ArbeiterApi("https://vm", "k")
    api.bild("a1", "neben", b"P", format="png")
    assert gesehen[-1].full_url.endswith("/a1/bild?platz=neben&format=png")
    assert gesehen[-1].get_header("Content-type") == "image/png"
    api.bild("a1", "neben", b"J")
    assert gesehen[-1].get_header("Content-type") == "image/jpeg" and "format=png" not in gesehen[-1].full_url


def test_comfy_freistellen_laedt_hoch_und_setzt_bildname(monkeypatch):
    hoch, lauf = [], []
    monkeypatch.setattr(bild_comfy, "_hochladen", lambda name, daten: hoch.append((name, daten)) or "hoch.png")
    monkeypatch.setattr(bild_comfy, "_ausfuehren", lambda ablauf, z: lauf.append((ablauf, z)) or b"PNG")
    assert bild_comfy.freistellen(b"quelle", zeitlimit_s=77) == b"PNG"
    assert hoch[0][1] == b"quelle" and lauf[0][0]["1"]["inputs"]["image"] == "hoch.png" and lauf[0][1] == 77
    assert lauf[0][0]["2"]["inputs"]["bg_removal_name"] == "birefnet.safetensors"
    with pytest.raises(bild_comfy.ComfyFehler):
        bild_comfy.freistellen(b"")
