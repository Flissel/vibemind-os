import io

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


def test_comfy_tot_wird_gestartet_sonst_zurueck_nicht_endgueltig():
    gestartet = []
    api = Api(dict(AUFTRAG))
    assert bw.ein_durchlauf(api, Comfy(laeuft=False), Prompt(), starten=lambda: gestartet.append(1)) == "zurueck"
    assert gestartet == [1] and api.log[-1][0] == "zurueck" and api.log[-1][2] is False


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
    assert [e for e in api.log if e[0] == "weiter"] == [("weiter", AUFTRAG["id"])] * 3
    assert zeitlimits == [540, 540, 540]
