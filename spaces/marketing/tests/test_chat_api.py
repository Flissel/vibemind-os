"""Chat- und Export-Routen ohne echte DB (FalscheDB wie test_pult_api)."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from spaces.marketing.api import server
from spaces.marketing.tests.test_pult_api import FalscheDB

PK, BK, AK = "pult-k", "bild-k", "api-k"
IID = "11111111-1111-1111-1111-111111111111"
AID = "22222222-2222-2222-2222-222222222222"
BID = "33333333-3333-3333-3333-333333333333"
H = {"X-Pult-Key": PK}
HB = {"X-Bild-Key": BK}
G = {"version": 1, "format": "quer", "hintergrund": "#FFFFFF", "ebenen": []}
G_WEG = dict(G, ebenen=[{"id": "b", "art": "bild", "quelle": "medien:weg.png", "x": 1, "y": 1, "breite": 10, "drehung": 0}])
JOB = {"inhalt": IID, "art": "chat", "status": "in_arbeit", "kontext": {}, "gueltig": True}


def _dok(g=G):
    return {"root": {"type": "EmailLayout", "data": {"childrenIds": ["f"]}},
            "f": {"type": "Image", "data": {"style": {}, "props": {"url": None, "alt": "x", "width": 600,
                                                                   "height": 1, "gestaltung": g}}}}


@pytest.fixture
def umg(monkeypatch, tmp_path):
    from spaces.marketing.api import gestaltung as ga
    from spaces.marketing.sync import _db
    f = FalscheDB()
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setenv("MARKETING_PULT_KEY", PK)
    monkeypatch.setenv("MARKETING_BILD_KEY", BK)
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    monkeypatch.delenv("MARKETING_MEDIEN_ORDNER", raising=False)
    monkeypatch.setattr(server, "API_KEY", AK)
    monkeypatch.setattr(ga, "_zuletzt", 1e18)   # kein Aufraeumen (eigene SQL) in diesen Tests
    return f, tmp_path, TestClient(server.app)


def _db_fehler(text):
    return RuntimeError(f"ERROR:  {text}")


# ─── Pult: Chat ─────────────────────────────────────────────────────────


def test_anlegen_reicht_durch(umg):
    f, _, c = umg
    f.antworten.append([{"id": AID}])
    r = c.post(f"/api/pult/inhalte/{IID}/chat", json={"nachricht": "Mach es wärmer", "kontext": {"fenster": "newsletter"}},
               headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"auftrag": AID}
    assert "marketing.pult_chat_anlegen(" in f.sql[0] and "'chat'" in f.sql[0] and "newsletter" in f.sql[0]


def test_zweites_anlegen_waehrend_laufendem_auftrag_422(umg):
    """Review Focus 4: zweiter Tab, waehrend der Assistent arbeitet."""
    f, _, c = umg
    f.antworten.append([{"id": AID}])
    f.fehler += [None, _db_fehler("Der Assistent arbeitet gerade")]
    assert c.post(f"/api/pult/inhalte/{IID}/chat", json={"nachricht": "eins"}, headers=H).status_code == 200
    r = c.post(f"/api/pult/inhalte/{IID}/chat", json={"nachricht": "zwei"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Der Assistent arbeitet gerade"


def test_anlegen_zu_lang_ohne_sql(umg):
    f, _, c = umg
    r = c.post(f"/api/pult/inhalte/{IID}/chat", json={"nachricht": "x" * 2001}, headers=H)
    assert r.status_code == 422 and f.sql == []


def test_anlegen_ohne_pult_schluessel_401(umg):
    _, _, c = umg
    assert c.post(f"/api/pult/inhalte/{IID}/chat", json={"nachricht": "x"}).status_code == 401


def test_stand_raeumt_zuerst_auf(umg):
    f, _, c = umg
    f.antworten += [[{"ok": True}], [
        {"id": BID, "art": "chat", "nachricht": "a", "antwort": "b", "status": "fertig", "hinweise": [], "ergebnis": {},
         "fassung_vorher": 1, "fassung_nachher": 2, "erstellt_am": "1", "sortiert_am": "1"},
        {"id": AID, "art": "chat", "nachricht": "c", "antwort": "", "status": "in_arbeit", "hinweise": [], "ergebnis": {},
         "fassung_vorher": 2, "fassung_nachher": None, "erstellt_am": "2", "sortiert_am": "2"}]]
    r = c.get(f"/api/pult/inhalte/{IID}/chat", headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "marketing.pult_chat_aufraeumen(" in f.sql[0]
    assert "chat_auftraege" in f.sql[1] and "LIMIT 30" in f.sql[1]
    assert d["laeuft"] is True and [z["id"] for z in d["verlauf"]] == [BID, AID]
    assert "sortiert_am" not in d["verlauf"][0] and d["verlauf"][0]["fassung_nachher"] == 2


def test_stand_sql_ohne_spaltenname_t(umg):
    # query_via_docker haengt die Abfrage als Unterabfrage `t` ein; eine Spalte `t` bricht den Wrapper (503).
    import re
    f, _, c = umg
    f.antworten += [[{"ok": True}], []]
    assert c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).status_code == 200
    assert not re.search(r"AS t", f.sql[1], re.I), f.sql[1]
    assert not re.search(r"ORDER BY t", f.sql[1], re.I), f.sql[1]


def test_rueckgaengig_nimmt_fassung_vorher(umg):
    f, _, c = umg
    alt = {"root": {"type": "EmailLayout", "data": {"childrenIds": []}}}
    f.antworten += [[{"fassung_vorher": 3, "fassung_nachher": 5, "bloecke": alt, "betreff": "B", "vorschautext": "V", "neueste": 5}],
                    [{"fassung": 6}]]
    r = c.post(f"/api/pult/inhalte/{IID}/chat/rueckgaengig", json={"auftrag": AID}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"fassung": 6}
    assert "fassung_vorher" in f.sql[0] and "status = 'fertig'" in f.sql[0]
    assert "marketing.pult_bloecke_speichern(" in f.sql[1] and ", 5, 'B', 'V'," in f.sql[1]
    assert "EmailLayout" in f.sql[1] and "'betreiber', false" in f.sql[1]


def test_rueckgaengig_nur_auf_der_neuesten_fassung(umg):
    f, _, c = umg
    alt = {"root": {"type": "EmailLayout", "data": {"childrenIds": []}}}
    f.antworten += [[{"fassung_vorher": 3, "fassung_nachher": 4, "bloecke": alt, "betreff": "B",
                      "vorschautext": "V", "neueste": 5}]]
    r = c.post(f"/api/pult/inhalte/{IID}/chat/rueckgaengig", json={"auftrag": AID}, headers=H)
    assert r.status_code == 422, r.text
    assert "neuere Fassung" in r.json()["detail"] and len(f.sql) == 1   # nichts gespeichert


def test_rueckgaengig_ohne_passenden_auftrag_422(umg):
    f, _, c = umg
    r = c.post(f"/api/pult/inhalte/{IID}/chat/rueckgaengig", json={"auftrag": AID}, headers=H)
    assert r.status_code == 422 and len(f.sql) == 1


# ─── Pult: Export ───────────────────────────────────────────────────────


def test_slug():
    from spaces.marketing.api.chat import slug
    assert slug("Oktober-Angebot!") == "oktober-angebot"
    assert slug("Grüße aus Köln – Maß & Übermaß") == "gruesse-aus-koeln-mass-uebermass"
    assert slug("!!!") == "newsletter" and slug("") == "newsletter"
    assert len(slug("a" * 100)) == 60
    assert slug("a" * 59 + " b") == "a" * 59


def test_export_ohne_bestaetigung_422(umg):
    f, _, c = umg
    r = c.post(f"/api/pult/inhalte/{IID}/export", json={"newsletter": True, "flaechen": []}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Export nur mit Bestätigung" and f.sql == []
    r = c.post(f"/api/pult/inhalte/{IID}/export", json={"newsletter": True, "bestaetigt": "ja"}, headers=H)
    assert r.status_code == 422 and f.sql == []


def test_export_flaeche_drei_dateien_mit_slug_und_kollision(umg):
    f, ordner, c = umg
    (ordner / "oktober-angebot-f-handy.jpg").write_bytes(b"alt")
    f.antworten.append([{"titel": "Oktober-Angebot!", "bloecke": _dok()}])
    r = c.post(f"/api/pult/inhalte/{IID}/export", json={"flaechen": ["f"], "bestaetigt": True}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["auftrag"] is None
    assert d["dateien"] == ["oktober-angebot-f-handy-2.jpg", "oktober-angebot-f-tablet.jpg", "oktober-angebot-f-pc.jpg"]
    assert (ordner / "oktober-angebot-f-handy.jpg").read_bytes() == b"alt"
    groessen = {n: Image.open(ordner / n).size for n in d["dateien"]}
    assert groessen == {"oktober-angebot-f-handy-2.jpg": (1200, 1500), "oktober-angebot-f-tablet.jpg": (1200, 1200),
                        "oktober-angebot-f-pc.jpg": (1200, 800)}
    assert not any(n.startswith("gs-") or n.endswith(".teil") for n in (p.name for p in ordner.iterdir()))
    assert all("pult_chat_anlegen" not in s for s in f.sql)


def test_export_keine_flaeche_422_ohne_dateien(umg):
    f, ordner, c = umg
    f.antworten.append([{"titel": "T", "bloecke": _dok()}])
    r = c.post(f"/api/pult/inhalte/{IID}/export", json={"flaechen": ["root"], "bestaetigt": True}, headers=H)
    assert r.status_code == 422 and "root ist keine Fläche" in r.json()["detail"]
    assert list(ordner.iterdir()) == []


def test_export_fehlende_quelle_legt_nichts_an(umg):
    f, ordner, c = umg
    f.antworten.append([{"titel": "T", "bloecke": _dok(G_WEG)}])
    r = c.post(f"/api/pult/inhalte/{IID}/export", json={"newsletter": True, "flaechen": ["f"], "bestaetigt": True},
               headers=H)
    assert r.status_code == 422 and "weg.png fehlt" in r.json()["detail"]
    assert list(ordner.iterdir()) == [] and all("pult_chat_anlegen" not in s for s in f.sql)


def test_export_newsletter_legt_auftrag_mit_slug_an(umg):
    f, _, c = umg
    f.antworten += [[{"titel": "Oktober-Angebot!", "bloecke": _dok()}], [{"id": AID}]]
    r = c.post(f"/api/pult/inhalte/{IID}/export", json={"newsletter": True, "bestaetigt": True}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"dateien": [], "auftrag": AID}
    assert "marketing.pult_chat_anlegen(" in f.sql[1] and "'export', ''" in f.sql[1]
    assert '"slug": "oktober-angebot"' in f.sql[1] and '"geraete": ["handy", "tablet", "pc"]' in f.sql[1]


def test_export_vorschau_drei_entwurfsbilder(umg):
    f, ordner, c = umg
    f.antworten.append([{"titel": "T", "bloecke": _dok()}])
    r = c.post(f"/api/pult/inhalte/{IID}/export/vorschau", json={"flaechen": ["f"]}, headers=H)
    assert r.status_code == 200, r.text
    urls = r.json()["flaechen"]["f"]
    assert set(urls) == {"handy", "tablet", "pc"} and len(set(urls.values())) == 3
    for u in urls.values():
        assert u.startswith("medien:gs-") and (ordner / u[7:]).exists()


# ─── Arbeiter ───────────────────────────────────────────────────────────


def test_arbeiter_schluessel(umg):
    f, _, c = umg
    assert c.post("/api/chat/arbeiter/naechster").status_code == 401
    assert c.post("/api/chat/arbeiter/naechster", headers={"X-Bild-Key": "falsch"}).status_code == 401
    assert c.post(f"/api/chat/arbeiter/{AID}/fertig", json={"antwort": "x"}).status_code == 401
    assert f.sql == []
    # ohne X-API-Key: von der globalen Middleware ausgenommen
    f.antworten.append([{"a": None}])
    assert c.post("/api/chat/arbeiter/naechster", headers=HB).json() == {"auftrag": None}


def test_naechster_mit_medienliste_ohne_entwuerfe(umg, monkeypatch, tmp_path_factory):
    f, ordner, c = umg
    mensch = tmp_path_factory.mktemp("mensch")
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(mensch))
    (mensch / "foto.jpg").write_bytes(b"x")
    (mensch / "mit leerzeichen.jpg").write_bytes(b"x")
    (ordner / "logo.png").write_bytes(b"x")
    (ordner / ("gs-" + "a" * 12 + ".jpg")).write_bytes(b"x")
    (ordner / "notiz.txt").write_bytes(b"x")
    f.antworten.append([{"a": {"id": AID, "art": "chat", "nachricht": "n"}}])
    r = c.post("/api/chat/arbeiter/naechster", headers=HB)
    assert r.status_code == 200, r.text
    a = r.json()["auftrag"]
    assert a["id"] == AID and a["medien"] == ["foto.jpg", "logo.png"]
    assert "marketing.pult_chat_naechster('5 minutes'::interval)" in f.sql[0]


def test_weiter_und_zurueck(umg):
    f, _, c = umg
    f.antworten += [[{"ok": True}], [{"s": "fehler"}]]
    assert c.post(f"/api/chat/arbeiter/{AID}/weiter", headers=HB).json() == {"ok": True}
    assert "pult_chat_verlaengern(" in f.sql[0]
    r = c.post(f"/api/chat/arbeiter/{AID}/zurueck", json={"antwort": "Shim weg"}, headers=HB)
    assert r.json() == {"status": "fehler"} and "pult_chat_zurueck(" in f.sql[1] and "Shim weg" in f.sql[1]


def test_fertig_ungueltige_gestaltung_geht_zurueck(umg):
    f, _, c = umg
    f.antworten += [[JOB], [{"s": "fehler"}]]
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json={"antwort": "Erledigt", "bloecke": _dok(G_WEG)}, headers=HB)
    assert r.status_code == 200 and r.json() == {"status": "fehler"}
    assert "pult_chat_zurueck(" in f.sql[-1]
    assert "Das habe ich nicht umsetzen können: f: Bild weg.png fehlt in den Medien" in f.sql[-1]
    assert all("pult_chat_fertig" not in s for s in f.sql)


def test_fertig_validator_lehnt_ab_geht_zurueck(umg):
    f, _, c = umg
    f.antworten += [[JOB], [{"f": "Unbekannter Blocktyp in x"}], [{"s": "fehler"}]]
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json={"antwort": "ok", "bloecke": _dok()}, headers=HB)
    assert r.json() == {"status": "fehler"}
    assert "pult_bloecke_fehler(" in f.sql[1]
    assert "Das habe ich nicht umsetzen können: Unbekannter Blocktyp in x" in f.sql[2]


def test_fertig_gueltig_fassung_dann_bildauftraege(umg):
    f, ordner, c = umg
    f.antworten += [[JOB], [{"f": None}], [{"e": {"fassung": 7}}], [{"id": BID}]]
    body = {"antwort": "Fertig", "bloecke": _dok(), "notiz": "wärmer",
            "export_vorschlag": {"newsletter": True, "flaechen": []},
            "bildauftraege": [{"platz": "held", "modus": "neu", "hinweis": "Kerzenlicht"}, {"platz": "../x", "modus": "neu"}]}
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json=body, headers=HB)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["fassung"] == 7 and d["bildauftraege"] == [BID, None]
    assert any("Bildplatz" in h for h in d["hinweise"])
    fertig = next(i for i, s in enumerate(f.sql) if "marketing.pult_chat_fertig(" in s)
    bild = next(i for i, s in enumerate(f.sql) if "marketing.pult_bild_auftrag(" in s)
    assert fertig < bild
    assert "medien:gs-" in f.sql[fertig] and '"notiz": "wärmer"' in f.sql[fertig]
    assert '"export_vorschlag": {"newsletter": true' in f.sql[fertig]
    assert "'agent'" in f.sql[bild] and "'neu'" in f.sql[bild] and "Kerzenlicht" in f.sql[bild]
    assert any(p.name.startswith("gs-") for p in ordner.iterdir())


def test_fertig_schoenheit_als_hinweis(umg):
    f, _, c = umg
    dok = _dok()
    dok["t"] = {"type": "Text", "data": {"style": {"color": "#ffffff"}, "props": {"text": "hallo"}}}
    f.antworten += [[JOB], [{"f": None}], [{"e": {"fassung": 8}}]]
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json={"antwort": "x", "bloecke": dok}, headers=HB)
    assert r.status_code == 200, r.text
    assert any(h.startswith("t: #ffffff") for h in r.json()["hinweise"])
    assert "t: #ffffff" in f.sql[2]


def test_fertig_ohne_bloecke_keine_fassung(umg):
    f, _, c = umg
    f.antworten += [[JOB], [{"e": {"fassung": None}}]]
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json={"antwort": "Nur eine Frage"}, headers=HB)
    assert r.json() == {"fassung": None, "hinweise": [], "bildauftraege": []}
    assert "marketing.pult_chat_fertig(" in f.sql[1] and ", NULL," in f.sql[1]


def test_fertig_neuere_fassung_wird_zurueck(umg):
    f, _, c = umg
    f.antworten += [[JOB], [{"f": None}], [{"s": "fehler"}]]
    f.fehler += [None, None, _db_fehler("Inzwischen gibt es Fassung 9 - neu laden oder als Kopie behalten")]
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json={"antwort": "x", "bloecke": _dok()}, headers=HB)
    assert r.status_code == 200 and r.json() == {"status": "fehler"}
    assert "pult_chat_zurueck(" in f.sql[-1]
    assert "Der Newsletter wurde inzwischen geändert – bitte schick die Nachricht noch einmal." in f.sql[-1]


def test_fertig_abgelaufene_vergabe_409(umg):
    f, _, c = umg
    f.antworten.append([dict(JOB, gueltig=False)])
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json={"antwort": "x", "bloecke": _dok()}, headers=HB)
    assert r.status_code == 409 and len(f.sql) == 1


def test_pruefen(umg):
    f, _, c = umg
    kaputt = _dok(dict(G, format="rund"))
    f.antworten.append([JOB])
    r = c.post(f"/api/chat/arbeiter/{AID}/pruefen", json={"bloecke": kaputt}, headers=HB)
    assert r.json()["fehler"].startswith("f: Format muss") and len(f.sql) == 1
    f.antworten += [[JOB], [{"f": "Kaputt"}]]
    r = c.post(f"/api/chat/arbeiter/{AID}/pruefen", json={"bloecke": _dok()}, headers=HB)
    assert r.json() == {"fehler": "Kaputt"} and "pult_bloecke_fehler(" in f.sql[-1]
    f.antworten += [[JOB], [{"f": None}]]
    assert c.post(f"/api/chat/arbeiter/{AID}/pruefen", json={"bloecke": _dok()}, headers=HB).json() == {"fehler": None}
    assert all(s.lstrip().upper().startswith("SELECT") for s in f.sql)


def test_pruefen_nur_in_arbeit(umg):
    f, _, c = umg
    assert c.post(f"/api/chat/arbeiter/{AID}/pruefen", json={"bloecke": {}}, headers=HB).status_code == 404
    f.antworten.append([dict(JOB, status="fertig")])
    assert c.post(f"/api/chat/arbeiter/{AID}/pruefen", json={"bloecke": {}}, headers=HB).status_code == 409


def test_medien_liefert_datei(umg):
    f, ordner, c = umg
    (ordner / "foto.jpg").write_bytes(b"JPEGDATEN")
    f.antworten.append([JOB])
    r = c.get(f"/api/chat/arbeiter/{AID}/medien/foto.jpg", headers=HB)
    assert r.status_code == 200 and r.content == b"JPEGDATEN"
    for boese in ("..%2Fx", "..%2F..%2Fgeheim.jpg", ".versteckt.jpg"):
        assert c.get(f"/api/chat/arbeiter/{AID}/medien/{boese}", headers=HB).status_code == 404
    f.antworten.append([dict(JOB, status="fertig")])
    assert c.get(f"/api/chat/arbeiter/{AID}/medien/foto.jpg", headers=HB).status_code == 404


def _jpeg(w=375, h=900):
    p = io.BytesIO()
    Image.new("RGB", (w, h), "#336699").save(p, "JPEG", quality=80)
    return p.getvalue()


EXPORT_JOB = dict(JOB, art="export", kontext={"geraete": ["handy", "tablet", "pc"], "slug": "oktober-angebot"})


def test_datei_speichert_sichtbar_mit_kollision(umg):
    f, ordner, c = umg
    f.antworten += [[EXPORT_JOB], [EXPORT_JOB]]
    url = f"/api/chat/arbeiter/{AID}/datei?name=oktober-angebot-handy.jpg"
    assert c.post(url, content=_jpeg(), headers=HB).json() == {"name": "oktober-angebot-handy.jpg"}
    assert c.post(url, content=_jpeg(), headers=HB).json() == {"name": "oktober-angebot-handy-2.jpg"}
    assert Image.open(ordner / "oktober-angebot-handy-2.jpg").size == (375, 900)


def test_datei_zu_gross_422(umg):
    f, _, c = umg
    r = c.post(f"/api/chat/arbeiter/{AID}/datei?name=oktober-angebot-pc.jpg", content=b"\xff\xd8\xff" + b"0" * (4 * 1024 * 1024),
               headers=HB)
    assert r.status_code == 422 and f.sql == []


def test_datei_ablehnungen(umg):
    f, ordner, c = umg
    basis = f"/api/chat/arbeiter/{AID}/datei?name="
    assert c.post(basis + "gs-aaaaaaaaaaaa.jpg", content=_jpeg(), headers=HB).status_code == 422
    assert c.post(basis + "x-handy.png", content=_jpeg(), headers=HB).status_code == 422
    assert c.post(basis + "oktober-angebot-pc.jpg", content=_jpeg(1300, 900), headers=HB).status_code == 422
    assert c.post(basis + "oktober-angebot-pc.jpg", content=b"kein jpeg", headers=HB).status_code == 422
    f.antworten.append([EXPORT_JOB])
    assert c.post(basis + "anderer-titel-pc.jpg", content=_jpeg(), headers=HB).status_code == 422
    f.antworten.append([dict(EXPORT_JOB, art="chat")])
    assert c.post(basis + "oktober-angebot-pc.jpg", content=_jpeg(), headers=HB).status_code == 422
    assert list(ordner.iterdir()) == []
