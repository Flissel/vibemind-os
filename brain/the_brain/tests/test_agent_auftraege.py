import json
import pytest
from core import agent_auftraege as aa


class Antwort:
    def __init__(self, status=200, daten=None):
        self.status_code, self._d = status, daten
    def json(self):
        return self._d
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Attrappe:
    def __init__(self, antworten):
        self.aufrufe, self.antworten = [], list(antworten)
    def request(self, methode, url, *, headers=None, params=None, json=None, timeout=None):
        self.aufrufe.append({"methode": methode, "url": url, "headers": headers, "params": params, "json": json})
        return self.antworten.pop(0)


def test_anlegen_schickt_service_header_und_liefert_id():
    h = Attrappe([Antwort(201, [{"id": "abc"}])])
    t = aa.AuftragsTabelle("http://kong:8000", "geheim", http=h)
    i = t.anlegen(capability="rowboat_search", agent="rowboat-chat", auftrag="suche x",
                  trace_id="test_1", plan_id="p", hop_id="s1", antwortkanal={"art": "telegram"})
    assert i == "abc"
    a = h.aufrufe[0]
    assert a["methode"] == "POST" and a["url"].endswith("/rest/v1/brain_agent_auftraege")
    assert a["headers"]["Authorization"] == "Bearer geheim" and a["headers"]["Prefer"] == "return=representation"
    assert a["json"]["status"] == "offen" and a["json"]["agent"] == "rowboat-chat"
    assert a["json"]["antwortkanal"] == {"art": "telegram"}


def test_zu_pruefen_filtert_fertige_ohne_pruefung():
    h = Attrappe([Antwort(200, [{"id": "1"}])])
    aa.AuftragsTabelle("http://k", "s", http=h).zu_pruefen()
    p = h.aufrufe[0]["params"]
    assert p["status"] == "eq.fertig" and p["pruefung"] == "is.null"


def test_abgelaufene_markieren_nur_offen_oder_laeuft_ueber_frist():
    h = Attrappe([Antwort(200, [{"id": "1"}, {"id": "2"}])])
    n = aa.AuftragsTabelle("http://k", "s", http=h).abgelaufene_markieren("2026-10-08T10:00:00+00:00")
    a = h.aufrufe[0]
    assert n == 2 and a["methode"] == "PATCH"
    assert a["params"]["status"] == "in.(offen,laeuft)" and a["params"]["frist"] == "lt.2026-10-08T10:00:00+00:00"
    assert a["json"]["status"] == "abgelaufen"


def test_fehlender_schluessel(monkeypatch):
    monkeypatch.setattr(aa, "get_secret", lambda n: "")
    monkeypatch.setenv("SUPABASE_URL", "http://k")
    with pytest.raises(RuntimeError):
        aa.tabelle_aus_umgebung()


def test_http_fehler_wird_nicht_verschluckt():
    h = Attrappe([Antwort(500, {"message": "x"})])
    with pytest.raises(RuntimeError):
        aa.AuftragsTabelle("http://k", "s", http=h).pruefung_setzen("1", {"verified": True})
