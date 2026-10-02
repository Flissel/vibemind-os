import io
import json
import urllib.error

import pytest

from spaces.marketing.workers import chat_worker as cw

DOC = {"root": {"type": "EmailLayout", "data": {"backdropColor": "#ffffff", "childrenIds": ["t"]}},
       "t": {"type": "Text", "data": {"props": {"text": "Herbst"}}}}
AUFTRAG = {"id": "a1", "art": "chat", "bloecke": DOC, "medien": ["a.jpg"], "kontext": {}, "nachricht": "Hintergrund rot"}

GUT = json.dumps({"antwort": "Erledigt.", "aenderungen": [{"werkzeug": "farben_setzen", "backdropColor": "#ff0000"}]})
NUR_TEXT = json.dumps({"antwort": "Da ist nichts zu tun.", "aenderungen": []})
SCHLECHT_WERKZEUG = json.dumps({"antwort": "x", "aenderungen": [{"werkzeug": "farben_setzen", "backdropColor": "rot"}]})
EXPORT = json.dumps({"antwort": "Vorschlag", "aenderungen": [{"werkzeug": "export_vorschlagen",
                                                                "newsletter": True, "flaechen": []}]})


class Api:
    def __init__(self, auftrag=None, pruefen=None, fertig=None):
        self.auftrag, self.log, self._pruefen, self._fertig = auftrag, [], pruefen or [], fertig or {"fassung": 3}

    def naechster(self):
        a, self.auftrag = self.auftrag, None
        return a

    def weiter(self, aid):
        self.log.append(("weiter", aid)); return True

    def pruefen(self, aid, bloecke):
        self.log.append(("pruefen", aid, bloecke))
        return self._pruefen.pop(0) if self._pruefen else None

    def fertig(self, aid, daten):
        self.log.append(("fertig", aid, daten)); return self._fertig

    def zurueck(self, aid, antwort):
        self.log.append(("zurueck", aid, antwort)); return "offen"

    def aufrufe(self, name):
        return [e for e in self.log if e[0] == name]


class Fragen:
    """Fake fuer frage(): liefert Antworten der Reihe nach; LlmFehler-Instanzen werden geworfen."""
    def __init__(self, *antworten):
        self.antworten, self.gesehen = list(antworten), []

    def __call__(self, system, nachrichten):
        self.gesehen.append((system, [dict(n) for n in nachrichten]))
        a = self.antworten.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def test_gueltige_antwort_wird_fertig_mit_bloecken():
    api, fragen = Api(), Fragen(GUT)
    assert cw.chat_bearbeiten(api, AUFTRAG, fragen) == "fertig"
    (_, aid, daten), = api.aufrufe("fertig")
    assert aid == "a1" and daten["antwort"] == "Erledigt."
    assert daten["bloecke"]["root"]["data"]["backdropColor"] == "#ff0000"
    assert daten["bildauftraege"] == [] and daten["export_vorschlag"] is None and daten["notiz"] == ""
    assert api.aufrufe("pruefen")[0][2] == daten["bloecke"]
    assert fragen.gesehen[0][1][0]["role"] == "user"
    assert DOC["root"]["data"]["backdropColor"] == "#ffffff"      # Eingabe bleibt unveraendert


def test_nur_text_ohne_aenderungen_sendet_keine_bloecke_und_pruefen_entfaellt():
    api = Api()
    assert cw.chat_bearbeiten(api, AUFTRAG, Fragen(NUR_TEXT)) == "fertig"
    assert api.aufrufe("fertig")[0][2]["bloecke"] is None
    assert api.aufrufe("pruefen") == []


def test_export_vorschlag_wird_durchgereicht():
    api = Api()
    assert cw.chat_bearbeiten(api, AUFTRAG, Fragen(EXPORT)) == "fertig"
    daten = api.aufrufe("fertig")[0][2]
    assert daten["export_vorschlag"] == {"newsletter": True, "flaechen": []} and daten["bloecke"] is None


def test_ungueltiges_json_dann_gueltig_haengt_korrektur_an():
    api, fragen = Api(), Fragen("Das ist kein JSON", GUT)
    assert cw.chat_bearbeiten(api, AUFTRAG, fragen) == "fertig"
    zweite = fragen.gesehen[1][1]
    assert [n["role"] for n in zweite] == ["user", "assistant", "user"]
    assert zweite[1]["content"] == "Das ist kein JSON"
    assert "konnte nicht umgesetzt werden" in zweite[2]["content"]
    assert api.aufrufe("zurueck") == []


def test_zweimal_ungueltig_gibt_zurueck():
    api, fragen = Api(), Fragen("kein json", "immer noch nicht")
    assert cw.chat_bearbeiten(api, AUFTRAG, fragen) == "fehler"
    (_, _, text), = api.aufrufe("zurueck")
    assert text.startswith("Das habe ich nicht umsetzen können: ")
    assert api.aufrufe("fertig") == [] and len(fragen.gesehen) == 2


def test_werkzeugfehler_dann_gueltig():
    api, fragen = Api(), Fragen(SCHLECHT_WERKZEUG, GUT)
    assert cw.chat_bearbeiten(api, AUFTRAG, fragen) == "fertig"
    assert "backdropColor" in fragen.gesehen[1][1][2]["content"]


def test_werkzeugfehler_zweimal_gibt_zurueck():
    api = Api()
    assert cw.chat_bearbeiten(api, AUFTRAG, Fragen(SCHLECHT_WERKZEUG, SCHLECHT_WERKZEUG)) == "fehler"
    assert "Das habe ich nicht umsetzen können: " in api.aufrufe("zurueck")[0][2]
    assert api.aufrufe("fertig") == []


def test_pruefen_fehler_zaehlt_wie_werkzeugfehler_und_verbraucht_die_runde():
    api, fragen = Api(pruefen=["Block t: kaputt"]), Fragen(GUT, GUT)
    assert cw.chat_bearbeiten(api, AUFTRAG, fragen) == "fertig"
    assert "Block t: kaputt" in fragen.gesehen[1][1][2]["content"]
    assert len(api.aufrufe("pruefen")) == 2


def test_pruefen_fehler_zweimal_gibt_mit_grund_zurueck():
    api = Api(pruefen=["Grund eins", "Grund zwei"])
    assert cw.chat_bearbeiten(api, AUFTRAG, Fragen(GUT, GUT)) == "fehler"
    assert api.aufrufe("zurueck")[0][2] == "Das habe ich nicht umsetzen können: Grund zwei"
    assert api.aufrufe("fertig") == []


def test_vm_lehnt_fertig_ab_ergibt_fehler():
    api = Api(fertig={"status": "fehler"})
    assert cw.chat_bearbeiten(api, AUFTRAG, Fragen(GUT)) == "fehler"


class Uhr:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def schlafen(self, s):
        self.t += s


def test_shim_dauerhaft_down_gibt_nach_180_s_zurueck_und_verlaengert_dazwischen():
    uhr, api = Uhr(), Api()

    def tot(system, nachrichten):
        raise cw.LlmFehler("down")
    assert cw.chat_bearbeiten(api, AUFTRAG, tot, uhr, uhr.schlafen) == "fehler"
    assert api.aufrufe("zurueck")[0][2] == "Der Assistent ist gerade nicht erreichbar"
    assert uhr.t >= cw.SHIM_BIS_S
    assert len(api.aufrufe("weiter")) >= 10


def test_shim_erholt_sich_nach_kurzem_ausfall():
    uhr, api = Uhr(), Api()
    assert cw.chat_bearbeiten(api, AUFTRAG, Fragen(cw.LlmFehler("x"), cw.LlmFehler("y"), GUT),
                              uhr, uhr.schlafen) == "fertig"
    assert len(api.aufrufe("weiter")) == 4 and api.aufrufe("zurueck") == []   # 2 Wiederholungen + vor pruefen + vor fertig


def test_verlorene_vergabe_bricht_ohne_zurueck_ab():
    uhr, api = Uhr(), Api()
    api.weiter = lambda aid: False

    def tot(system, nachrichten):
        raise cw.LlmFehler("down")
    assert cw.chat_bearbeiten(api, AUFTRAG, tot, uhr, uhr.schlafen) == "fehler"
    assert api.aufrufe("zurueck") == []


def test_ein_durchlauf_leer_und_chat_und_export():
    assert cw.ein_durchlauf(Api(None), Fragen()) == "leer"
    api = Api(dict(AUFTRAG))
    assert cw.ein_durchlauf(api, Fragen(GUT)) == "fertig" and api.aufrufe("fertig")
    gesehen = []
    api = Api({"id": "e1", "art": "export"})
    assert cw.ein_durchlauf(api, Fragen(), exportieren=lambda a, auf: gesehen.append((a, auf)) or "fertig") == "fertig"
    assert gesehen == [(api, {"id": "e1", "art": "export"})]


class _Antwort(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_frage_baut_openai_request(monkeypatch):
    gesehen = {}

    def fake(req, timeout=None, context=None):
        gesehen.update(url=req.full_url, body=json.loads(req.data), timeout=timeout, methode=req.get_method())
        return _Antwort(json.dumps({"choices": [{"message": {"content": "hallo"}}]}).encode())
    monkeypatch.setattr(cw.urllib.request, "urlopen", fake)
    assert cw.frage("SYS", [{"role": "user", "content": "hi"}]) == "hallo"
    assert gesehen["url"] == cw.LLM_URL + "/chat/completions" and gesehen["methode"] == "POST"
    assert gesehen["body"] == {"model": cw.MODELL, "messages": [{"role": "system", "content": "SYS"},
                                                                {"role": "user", "content": "hi"}]}
    assert gesehen["timeout"] == 300


@pytest.mark.parametrize("fehler", [urllib.error.URLError("refused"), TimeoutError(), ConnectionResetError()])
def test_frage_uebersetzt_netzfehler_in_llmfehler(monkeypatch, fehler):
    def fake(*a, **k):
        raise fehler
    monkeypatch.setattr(cw.urllib.request, "urlopen", fake)
    with pytest.raises(cw.LlmFehler):
        cw.frage("S", [])


@pytest.mark.parametrize("roh", [b"kein json", b"{}", json.dumps({"choices": []}).encode(),
                                 json.dumps({"choices": [{"message": {"content": "  "}}]}).encode()])
def test_frage_unbrauchbare_antwort_ist_llmfehler(monkeypatch, roh):
    monkeypatch.setattr(cw.urllib.request, "urlopen", lambda *a, **k: _Antwort(roh))
    with pytest.raises(cw.LlmFehler):
        cw.frage("S", [])


def _api_mit_antwort(monkeypatch, roh=b"{}"):
    gesehen = {}

    def fake(req, timeout=None, context=None):
        gesehen.update(url=req.full_url, methode=req.get_method(), data=req.data,
                       key=req.get_header("X-bild-key"))
        return _Antwort(roh)
    monkeypatch.setattr(cw.urllib.request, "urlopen", fake)
    return cw.ChatApi("https://vm/", "GEHEIM"), gesehen


def test_chatapi_pruefen(monkeypatch):
    api, g = _api_mit_antwort(monkeypatch, b'{"fehler": "Block x kaputt"}')
    assert api.pruefen("a1", DOC) == "Block x kaputt"
    assert g["url"] == "https://vm/api/chat/arbeiter/a1/pruefen" and g["methode"] == "POST"
    assert json.loads(g["data"]) == {"bloecke": DOC} and g["key"] == "GEHEIM"
    api, _ = _api_mit_antwort(monkeypatch, b'{"fehler": null}')
    assert api.pruefen("a1", DOC) is None


def test_chatapi_routen(monkeypatch):
    api, g = _api_mit_antwort(monkeypatch, b'{"auftrag": null, "ok": true, "status": "offen", "name": "x.jpg"}')
    assert api.naechster() is None and g["url"].endswith("/api/chat/arbeiter/naechster")
    assert api.weiter("a1") is True and g["url"].endswith("/a1/weiter")
    assert api.zurueck("a1", "nein") == "offen" and json.loads(g["data"]) == {"antwort": "nein"}
    assert api.fertig("a1", {"antwort": "x"})["ok"] is True and g["url"].endswith("/a1/fertig")
    assert api.datei("a1", "n-handy.jpg", b"\xff\xd8\xff") == "x.jpg"
    assert g["url"].endswith("/a1/datei?name=n-handy.jpg") and g["data"] == b"\xff\xd8\xff"


def test_chatapi_medium_404_ist_none(monkeypatch):
    api, g = _api_mit_antwort(monkeypatch, b"BYTES")
    assert api.medium("a1", "a.jpg") == b"BYTES" and g["methode"] == "GET"

    def weg(req, timeout=None, context=None):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b'{"detail": "Unbekannte Datei"}'))
    monkeypatch.setattr(cw.urllib.request, "urlopen", weg)
    assert api.medium("a1", "a.jpg") is None


def test_chatapi_http_fehler_wird_apifehler(monkeypatch):
    api, _ = _api_mit_antwort(monkeypatch)

    def kaputt(req, timeout=None, context=None):
        raise urllib.error.HTTPError(req.full_url, 422, "x", {}, io.BytesIO(b'{"detail": "Auftrag nicht in Arbeit"}'))
    monkeypatch.setattr(cw.urllib.request, "urlopen", kaputt)
    with pytest.raises(cw.ApiFehler) as e:
        api.weiter("a1")
    assert e.value.code == 422 and "nicht in Arbeit" in e.value.grund


# ---- Fix round 1 ----------------------------------------------------------------------------
import threading
import time


def test_keepalive_ruft_weiter_waehrend_langsamer_frage():
    api, auf, frei = Api(), threading.Event(), threading.Event()

    def langsam(system, nachrichten):
        deadline = time.monotonic() + 5
        while len(api.aufrufe("weiter")) < 3 and time.monotonic() < deadline:
            time.sleep(0.005)
        return GUT
    assert cw.chat_bearbeiten(api, AUFTRAG, langsam, halten_takt_s=0.01) == "fertig"
    assert len(api.aufrufe("weiter")) >= 3
    # direkt vor pruefen und fertig wird die Vergabe noch einmal verlaengert
    namen = [e[0] for e in api.log]
    assert namen.index("pruefen") > 0 and namen[namen.index("pruefen") - 1] == "weiter"
    assert namen[namen.index("fertig") - 1] == "weiter"


def test_keepalive_auch_in_der_korrekturfrage():
    api = Api()
    zaehler = []

    def langsam(system, nachrichten):
        zaehler.append(len(api.aufrufe("weiter")))
        deadline = time.monotonic() + 5
        start = len(api.aufrufe("weiter"))
        while len(api.aufrufe("weiter")) < start + 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        return "kein json" if len(zaehler) == 1 else GUT
    assert cw.chat_bearbeiten(api, AUFTRAG, langsam, halten_takt_s=0.01) == "fertig"
    assert zaehler[1] >= zaehler[0] + 2


def test_verlorene_vergabe_waehrend_der_frage_kein_fertig():
    api = Api()
    api.weiter = lambda aid: (api.log.append(("weiter", aid)), False)[1]

    def langsam(system, nachrichten):
        deadline = time.monotonic() + 5
        while not api.aufrufe("weiter") and time.monotonic() < deadline:
            time.sleep(0.005)
        time.sleep(0.05)
        return GUT
    assert cw.chat_bearbeiten(api, AUFTRAG, langsam, halten_takt_s=0.01) == "fehler"
    assert api.aufrufe("fertig") == [] and api.aufrufe("zurueck") == []


def test_vergabe_vor_fertig_verloren_kein_fertig():
    api = Api()
    api.weiter = lambda aid: (api.log.append(("weiter", aid)), False)[1]
    assert cw.chat_bearbeiten(api, AUFTRAG, Fragen(NUR_TEXT), halten_takt_s=60) == "fehler"
    assert api.aufrufe("fertig") == [] and api.aufrufe("zurueck") == []


def test_shim_fenster_startet_je_frage_neu():
    uhr, api = Uhr(), Api()
    n = []

    def fragen(system, nachrichten):
        n.append(1)
        if len(n) == 1:
            uhr.t += 170           # erste Frage dauert lange, klappt aber
            return "kein json"
        if len(n) in (2, 3):
            raise cw.LlmFehler("kurz weg")   # waere nach 180 s seit Start sofort abgelaufen
        return GUT
    assert cw.chat_bearbeiten(api, AUFTRAG, fragen, uhr, uhr.schlafen, halten_takt_s=60) == "fertig"
    assert api.aufrufe("zurueck") == []


def test_pruefen_apifehler_503_gibt_einmal_zurueck():
    api = Api()
    api.pruefen = lambda aid, b: (_ for _ in ()).throw(cw.ApiFehler(503, "db"))
    assert cw.chat_bearbeiten(api, AUFTRAG, Fragen(GUT), halten_takt_s=60) == "fehler"
    assert [e[2] for e in api.aufrufe("zurueck")] == ["Der Assistent ist gerade nicht erreichbar"]


def test_fertig_urlerror_gibt_einmal_zurueck():
    api = Api()
    api.fertig = lambda aid, d: (_ for _ in ()).throw(urllib.error.URLError("weg"))
    assert cw.chat_bearbeiten(api, AUFTRAG, Fragen(GUT), halten_takt_s=60) == "fehler"
    assert len(api.aufrufe("zurueck")) == 1


@pytest.mark.parametrize("code", [404, 409, 422])
def test_fertig_422_auftrag_nicht_mehr_unser_kein_zurueck(code):
    api = Api()
    api.fertig = lambda aid, d: (_ for _ in ()).throw(cw.ApiFehler(code, "nicht in Arbeit"))
    assert cw.chat_bearbeiten(api, AUFTRAG, Fragen(GUT), halten_takt_s=60) == "fehler"
    assert api.aufrufe("zurueck") == []


def test_zurueck_selbst_scheitert_wird_verschluckt():
    api = Api()
    api.fertig = lambda aid, d: (_ for _ in ()).throw(urllib.error.URLError("weg"))
    api.zurueck = lambda aid, t: (_ for _ in ()).throw(urllib.error.URLError("auch weg"))
    assert cw.chat_bearbeiten(api, AUFTRAG, Fragen(GUT), halten_takt_s=60) == "fehler"


def test_export_fehler_gibt_einmal_zurueck():
    api = Api({"id": "e1", "art": "export"})

    def kaputt(a, auf):
        raise RuntimeError("Playwright fehlt " + "x" * 500)
    assert cw.ein_durchlauf(api, Fragen(), exportieren=kaputt) == "fehler"
    (_, aid, text), = api.aufrufe("zurueck")
    assert aid == "e1" and text.startswith("Export nicht möglich: ") and len(text) < 300


def test_export_import_fehler_gibt_einmal_zurueck(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "spaces.marketing.workers.export_worker", None)
    api = Api({"id": "e1", "art": "export"})
    assert cw.ein_durchlauf(api, Fragen()) == "fehler"
    assert api.aufrufe("zurueck")[0][2].startswith("Export nicht möglich: ")


def test_schleifenschritt_ueberlebt_ausnahme_und_kuerzt(capsys):
    def boom(api):
        raise RuntimeError("X-Bild-Key: GEHEIM " + "y" * 500)
    cw.schleifenschritt(None, boom)
    assert cw.STAND["letztes_ergebnis"].startswith("fehler: RuntimeError")
    assert len(cw.STAND["letztes_ergebnis"]) <= 200 and cw.STAND["letzter_lauf"]
    cw.schleifenschritt(None, lambda api: "leer")
    assert cw.STAND["letztes_ergebnis"] == "leer"
