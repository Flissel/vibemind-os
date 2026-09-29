from spaces.marketing.claw import server, werkzeuge

IID = "11111111-1111-1111-1111-111111111111"


def test_bildplaetze_liest_die_agent_route(monkeypatch):
    gerufen = []
    monkeypatch.setattr(werkzeuge, "_api", lambda pfad, nutzlast=None: gerufen.append((pfad, nutzlast)) or
                        {"ok": True, "daten": {"fassung": 2, "plaetze": [{"id": "kopf"}], "auftraege": []}})
    r = werkzeuge.newsletter_bildplaetze(IID)
    assert r["ok"] and r["plaetze"][0]["id"] == "kopf" and gerufen == [(f"/api/bilder/agent/{IID}/plaetze", None)]


def test_beauftragen_schickt_formen(monkeypatch):
    gerufen = []
    monkeypatch.setattr(werkzeuge, "_api", lambda pfad, nutzlast=None: gerufen.append((pfad, nutzlast)) or
                        {"ok": True, "daten": {"auftrag": "a1"}})
    r = werkzeuge.newsletter_bild_beauftragen(IID, platz="kopf", hinweis="waermer")
    assert r == {"ok": True, "auftrag": "a1"}
    assert gerufen[0] == (f"/api/bilder/agent/{IID}/auftrag", {"platz": "kopf", "hinweis": "waermer", "nur_leere": False})
    werkzeuge.newsletter_bild_beauftragen(IID, nur_leere=True)
    assert gerufen[1][1] == {"platz": None, "hinweis": "", "nur_leere": True}


def test_ungueltige_id_ohne_netz(monkeypatch):
    monkeypatch.setattr(werkzeuge, "_api", lambda *a, **k: (_ for _ in ()).throw(AssertionError("kein Netz")))
    assert werkzeuge.newsletter_bildplaetze("../x")["ok"] is False
    assert werkzeuge.newsletter_bild_beauftragen("x")["ok"] is False


def test_im_werkzeugkasten():
    assert werkzeuge.newsletter_bildplaetze in server.WERKZEUGE
    assert werkzeuge.newsletter_bild_beauftragen in server.WERKZEUGE
