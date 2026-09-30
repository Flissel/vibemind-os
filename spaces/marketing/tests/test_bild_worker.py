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


class Api:
    def __init__(self, auftrag):
        self.auftrag, self.log = auftrag, []

    def naechster(self):
        a, self.auftrag = self.auftrag, None
        return a

    def weiter(self, aid):
        self.log.append(("weiter", aid)); return True

    def bild(self, aid, platz, jpeg):
        assert jpeg[:3] == b"\xff\xd8\xff" and len(jpeg) < 1024 * 1024
        self.log.append(("bild", platz)); return f"nl-{aid[:8]}-{platz}.jpg"

    def fertig(self, aid, ergebnis, befund):
        self.log.append(("fertig", ergebnis, befund)); return {"fassung": 2}

    def zurueck(self, aid, befund, endgueltig):
        self.log.append(("zurueck", befund, endgueltig)); return "offen"


class Comfy:
    ComfyFehler = bild_comfy.ComfyFehler

    def __init__(self, laeuft=True, fehler=None):
        self._laeuft, self.fehler, self.masse, self.frei = laeuft, fehler, [], 0

    def laeuft(self):
        return self._laeuft

    def erzeugen(self, prompt, b, h, seed, zeitlimit_s=300):
        if self.fehler:
            raise self.fehler
        self.masse.append((b, h)); return png(b, h)

    def freigeben(self):
        self.frei += 1


class Prompt:
    def __init__(self, urteile=None):
        self.urteile = list(urteile or [])

    def laeuft(self):
        return True

    def prompt_schreiben(self, platz, titel, hinweis):
        return f"bild fuer {platz['id']}"

    def pruefen(self, png_, prompt):
        return self.urteile.pop(0) if self.urteile else (True, "")


@pytest.fixture(autouse=True)
def _start_zustand(monkeypatch):
    monkeypatch.setattr(bw, "START", {"arbeiter": None, "dienste": None})


AUFTRAG = {"id": "0123abcd-0000-0000-0000-000000000000", "platz": None, "nur_leere": True, "hinweis": "",
           "bloecke": DOC, "titel": "Oktober", "fassung": 1}


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


def test_ollama_aus_nimmt_auch_keinen_auftrag():
    class P(Prompt):
        def laeuft(self):
            return False
    api = ZaehlApi(dict(AUFTRAG))
    assert bw.ein_durchlauf(api, Comfy(), P(), starten=lambda: None, uhr=Uhr()) == "wartet"
    assert api.gefragt == 0


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
        def fertig(self, aid, ergebnis, befund):
            raise bw.ApiFehler(422, "Auftrag ist nicht in Arbeit (verworfen)")
    assert bw.ein_durchlauf(Verworfen(dict(AUFTRAG)), Comfy(), Prompt(), starten=lambda: None) == "verworfen"


def test_fertig_abgelehnt_serverwortlaut_nicht_mehr_in_arbeit():
    class Verworfen(Api):
        def fertig(self, aid, ergebnis, befund):
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
