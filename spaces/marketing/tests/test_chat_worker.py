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
    assert len(api.aufrufe("weiter")) == 2 and api.aufrufe("zurueck") == []


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
