"""Chat- und Export-Routen ohne echte DB (FalscheDB wie test_pult_api)."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from spaces.marketing.api import server
from spaces.marketing.tests.test_medien_mandant import FalschePsql
from spaces.marketing.tests.test_pult_api import FalscheDB

PK, BK, AK = "pult-k", "bild-k", "api-k"
IID = "11111111-1111-1111-1111-111111111111"
AID = "22222222-2222-2222-2222-222222222222"
BID = "33333333-3333-3333-3333-333333333333"
H = {"X-Pult-Key": PK}
HB = {"X-Bild-Key": BK}
G = {"version": 1, "format": "quer", "hintergrund": "#FFFFFF", "ebenen": []}
G_WEG = dict(G, ebenen=[{"id": "b", "art": "bild", "quelle": "medien:weg.png", "x": 1, "y": 1, "breite": 10, "drehung": 0}])
JOB = {"inhalt": IID, "art": "chat", "status": "in_arbeit", "kontext": {}, "gueltig": True, "mandant": "vibemind"}
LOGO_F = "logo-laura-0123456789.png"
LOGO_E = "logo-vibemind-0123456789.png"


def _sicht(eigene=(), fremde=(), gemeinsam=()):
    """Antwortzeile der Sichtabfrage (medien_mandant.sicht) fuer Mandant vibemind."""
    return [{"name": "Vibemind", "eigene": list(eigene), "fremde": list(fremde), "gemeinsam": list(gemeinsam)}]


def _dok_bilder(*namen):
    d = _dok()
    for i, n in enumerate(namen):
        d[f"b{i}"] = {"type": "Image", "data": {"style": {}, "props": {"url": f"medien:{n}", "alt": "x"}}}
    return d


def _dok(g=G):
    return {"root": {"type": "EmailLayout", "data": {"childrenIds": ["f"]}},
            "f": {"type": "Image", "data": {"style": {}, "props": {"url": None, "alt": "x", "width": 600,
                                                                   "height": 1, "gestaltung": g}}}}


@pytest.fixture
def umg(monkeypatch, tmp_path):
    from spaces.marketing.api import gestaltung as ga
    from spaces.marketing.sync import _db
    f = FalscheDB()
    f.psql = FalschePsql()          # Schreibweg der Bildzuordnung (medien_mandant._schreiben_zahl)
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setattr(_db, "_run_psql", f.psql)
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
    f.antworten.append([{"s": {"id": AID, "status": "wartet"}}])
    r = c.post(f"/api/pult/inhalte/{IID}/chat", json={"nachricht": "Mach es wärmer", "kontext": {"fenster": "newsletter"}},
               headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"auftrag": AID, "status": "wartet"}
    assert "marketing.pult_chat_senden(" in f.sql[0] and "Mach es wärmer" in f.sql[0] and "newsletter" in f.sql[0]


def test_sechste_wartende_runde_422(umg):
    f, _, c = umg
    f.fehler.append(_db_fehler("Bitte warten, bis eine Runde fertig ist"))
    r = c.post(f"/api/pult/inhalte/{IID}/chat", json={"nachricht": "noch eine"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Bitte warten, bis eine Runde fertig ist"


def test_vormerk_routen_gibt_es_nicht_mehr(umg):
    f, _, c = umg
    basis = f"/api/pult/inhalte/{IID}/chat/vormerkung"
    assert c.put(basis, json={"nachricht": "x"}, headers=H).status_code in (404, 405)
    assert c.delete(basis, headers=H).status_code in (404, 405)
    assert c.post(basis + "/starten", headers=H).status_code in (404, 405)
    assert f.sql == []


def test_zweites_anlegen_waehrend_laufendem_auftrag_422(umg):
    """Review Focus 4: zweiter Tab, waehrend der Assistent arbeitet."""
    f, _, c = umg
    f.antworten.append([{"s": {"id": AID, "status": "offen"}}])
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




def test_stand_sql_ohne_spaltenname_t(umg):
    # query_via_docker haengt die Abfrage als Unterabfrage `t` ein; eine Spalte `t` bricht den Wrapper (503).
    import re
    f, _, c = umg
    f.antworten += [[{"ok": True}], [], [], []]
    assert c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).status_code == 200
    assert len(f.sql) == 4
    for s in f.sql:
        assert not re.search(r"\bAS t\b", s, re.I), s
        assert not re.search(r"ORDER BY t\b", s, re.I), s


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
    # alle drei Geraetebilder gehen in EINER Zuordnung an den Mandanten des Inhalts
    assert len(f.psql.sql) == 1 and "marketing.medien_mandant" in f.psql.sql[0]
    for n in d["dateien"]:
        assert f"'{n}'" in f.psql.sql[0]
    assert IID in f.psql.sql[0]


def test_export_zuordnung_scheitert_loescht_dateien_503(umg):
    f, ordner, c = umg
    f.antworten.append([{"titel": "Oktober-Angebot!", "bloecke": _dok()}])
    f.psql.fehler.append(RuntimeError("psql weg"))
    r = c.post(f"/api/pult/inhalte/{IID}/export", json={"flaechen": ["f"], "bestaetigt": True}, headers=H)
    assert r.status_code == 503, r.text
    assert list(ordner.iterdir()) == []      # nie eine Datei ohne Zuordnung (= Gemeinsam) liegen lassen


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
    f.antworten += [[{"a": {"id": AID, "art": "chat", "nachricht": "n", "mandant": "vibemind"}}], _sicht()]
    r = c.post("/api/chat/arbeiter/naechster", headers=HB)
    assert r.status_code == 200, r.text
    a = r.json()["auftrag"]
    assert a["id"] == AID and a["medien"] == ["foto.jpg", "logo.png"]
    assert a["mandant_name"] == "Vibemind" and "medien_hinweis" not in a
    assert "marketing.pult_chat_naechster('5 minutes'::interval)" in f.sql[0]
    assert "marketing.medien_mandant" in f.sql[1]


def test_naechster_filtert_fremde_bilder_und_fremdes_logo(umg, monkeypatch, tmp_path_factory):
    f, ordner, c = umg
    mensch = tmp_path_factory.mktemp("mensch")
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(mensch))
    for n in ("eigen.jpg", "fremd.jpg", "gemeinsam.jpg", "ohne-zeile.jpg", LOGO_E, LOGO_F):
        (mensch / n).write_bytes(b"x")
    f.antworten += [[{"a": {"id": AID, "art": "chat", "nachricht": "n", "mandant": "vibemind"}}],
                    _sicht(eigene=["eigen.jpg"], fremde=["fremd.jpg"], gemeinsam=["gemeinsam.jpg"])]
    a = c.post("/api/chat/arbeiter/naechster", headers=HB).json()["auftrag"]
    assert a["medien"] == ["eigen.jpg", "gemeinsam.jpg", LOGO_E, "ohne-zeile.jpg"]   # kein fremd.jpg, kein Laura-Logo
    assert a["mandant_name"] == "Vibemind"


def test_naechster_kappt_erst_nach_dem_filtern(umg, monkeypatch, tmp_path_factory):
    from spaces.marketing.api import chat
    f, ordner, c = umg
    mensch = tmp_path_factory.mktemp("mensch")
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(mensch))
    monkeypatch.setattr(chat, "MEDIEN_MAX", 2)
    for n in ("a-fremd.jpg", "b-fremd.jpg", "c-eigen.jpg", "d-eigen.jpg", "e-eigen.jpg"):
        (mensch / n).write_bytes(b"x")
    f.antworten += [[{"a": {"id": AID, "art": "chat", "nachricht": "n", "mandant": "vibemind"}}],
                    _sicht(fremde=["a-fremd.jpg", "b-fremd.jpg"])]
    a = c.post("/api/chat/arbeiter/naechster", headers=HB).json()["auftrag"]
    assert a["medien"] == ["c-eigen.jpg", "d-eigen.jpg"]


def test_naechster_sicht_ausfall_liefert_auftrag_ohne_bilder(umg):
    f, ordner, c = umg
    (ordner / "foto.jpg").write_bytes(b"x")
    f.antworten += [[{"a": {"id": AID, "art": "chat", "nachricht": "n", "mandant": "laura"}}]]
    f.fehler += [None, RuntimeError("db weg")]          # pult_chat_naechster ok, Sichtabfrage scheitert
    r = c.post("/api/chat/arbeiter/naechster", headers=HB)
    assert r.status_code == 200, r.text
    a = r.json()["auftrag"]
    assert a["id"] == AID and a["medien"] == [] and a["mandant_name"] == "laura"
    assert a["medien_hinweis"] == "Bildzuordnung nicht erreichbar"


def test_weiter_und_zurueck(umg):
    f, _, c = umg
    f.antworten += [[{"ok": True}], [{"s": "fehler"}]]
    assert c.post(f"/api/chat/arbeiter/{AID}/weiter", headers=HB).json() == {"ok": True}
    assert "pult_chat_verlaengern(" in f.sql[0]
    r = c.post(f"/api/chat/arbeiter/{AID}/zurueck", json={"antwort": "Shim weg"}, headers=HB)
    assert r.json() == {"status": "fehler"} and "pult_chat_zurueck(" in f.sql[1] and "Shim weg" in f.sql[1]


def test_fertig_ungueltige_gestaltung_geht_zurueck(umg):
    f, _, c = umg
    f.antworten += [[JOB], _sicht(), [{"s": "fehler"}]]
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json={"antwort": "Erledigt", "bloecke": _dok(G_WEG)}, headers=HB)
    assert r.status_code == 200 and r.json() == {"status": "fehler"}
    assert "pult_chat_zurueck(" in f.sql[-1]
    assert "Das habe ich nicht umsetzen können: f: Bild weg.png fehlt in den Medien" in f.sql[-1]
    assert all("pult_chat_fertig" not in s for s in f.sql)


def test_fertig_neues_fremdes_bild_geht_zurueck(umg):
    f, _, c = umg
    f.antworten += [[JOB], _sicht(fremde=["fremd.jpg"]), [{"bloecke": _dok_bilder("eigen.jpg")}], [{"s": "fehler"}]]
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json={"antwort": "x", "bloecke": _dok_bilder("fremd.jpg")}, headers=HB)
    assert r.status_code == 200 and r.json() == {"status": "fehler"}
    assert "pult_chat_zurueck(" in f.sql[-1]
    assert "Das habe ich nicht umsetzen können: Bild nicht verfügbar: fremd.jpg" in f.sql[-1]
    assert all("pult_chat_fertig" not in s for s in f.sql)


def test_fertig_fremdes_bild_aus_basisfassung_bleibt_erlaubt(umg):
    """Review Focus 1: Altbestand (schon in der Basisfassung) blockiert keine Aenderung."""
    f, _, c = umg
    f.antworten += [[JOB], _sicht(fremde=["fremd.jpg"]), [{"bloecke": _dok_bilder("fremd.jpg")}],
                    [{"f": None}], [{"e": {"fassung": 3}}]]
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json={"antwort": "x", "bloecke": _dok_bilder("fremd.jpg")}, headers=HB)
    assert r.status_code == 200 and r.json()["fassung"] == 3, r.text
    assert "FROM marketing.chat_auftraege a JOIN marketing.inhalt_fassungen f" in f.sql[2]


def test_fertig_sicht_ausfall_503_ohne_fassung(umg):
    f, _, c = umg
    f.antworten += [[JOB]]
    f.fehler += [None, RuntimeError("db weg")]
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json={"antwort": "x", "bloecke": _dok_bilder("a.jpg")}, headers=HB)
    assert r.status_code == 503
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


def test_pruefen_neues_fremdes_bild_fehler(umg):
    f, _, c = umg
    f.antworten += [[JOB], _sicht(fremde=["fremd.jpg"]), [{"bloecke": _dok_bilder("eigen.jpg")}]]
    r = c.post(f"/api/chat/arbeiter/{AID}/pruefen", json={"bloecke": _dok_bilder("fremd.jpg")}, headers=HB)
    assert r.status_code == 200 and r.json()["fehler"].startswith("Bild nicht verfügbar"), r.text
    assert "fremd.jpg" in r.json()["fehler"]
    # Logo der anderen Firma ohne Zeile zaehlt ebenfalls
    f.antworten += [[JOB], _sicht(), [{"bloecke": _dok()}]]
    r = c.post(f"/api/chat/arbeiter/{AID}/pruefen", json={"bloecke": _dok_bilder(LOGO_F)}, headers=HB)
    assert r.json()["fehler"] == f"Bild nicht verfügbar: {LOGO_F}"


def test_pruefen_fremdes_bild_aus_basisfassung_kein_fehler(umg):
    f, _, c = umg
    f.antworten += [[JOB], _sicht(fremde=["fremd.jpg"]), [{"bloecke": _dok_bilder("fremd.jpg")}], [{"f": None}]]
    r = c.post(f"/api/chat/arbeiter/{AID}/pruefen", json={"bloecke": _dok_bilder("fremd.jpg")}, headers=HB)
    assert r.json() == {"fehler": None}


def test_pruefen_nur_in_arbeit(umg):
    f, _, c = umg
    assert c.post(f"/api/chat/arbeiter/{AID}/pruefen", json={"bloecke": {}}, headers=HB).status_code == 404
    f.antworten.append([dict(JOB, status="fertig")])
    assert c.post(f"/api/chat/arbeiter/{AID}/pruefen", json={"bloecke": {}}, headers=HB).status_code == 409


def test_medien_liefert_datei(umg):
    f, ordner, c = umg
    (ordner / "foto.jpg").write_bytes(b"JPEGDATEN")
    f.antworten += [[JOB], _sicht()]
    r = c.get(f"/api/chat/arbeiter/{AID}/medien/foto.jpg", headers=HB)
    assert r.status_code == 200 and r.content == b"JPEGDATEN"
    for boese in ("..%2Fx", "..%2F..%2Fgeheim.jpg", ".versteckt.jpg"):
        assert c.get(f"/api/chat/arbeiter/{AID}/medien/{boese}", headers=HB).status_code == 404
    f.antworten.append([dict(JOB, status="fertig")])
    assert c.get(f"/api/chat/arbeiter/{AID}/medien/foto.jpg", headers=HB).status_code == 404


def test_medien_fremd_404_eigen_und_gemeinsam_200(umg):
    f, ordner, c = umg
    for n in ("fremd.jpg", "eigen.jpg", "gemeinsam.jpg", LOGO_F):
        (ordner / n).write_bytes(b"BILD")
    sicht = _sicht(eigene=["eigen.jpg"], fremde=["fremd.jpg"], gemeinsam=["gemeinsam.jpg"])
    for name, status in (("fremd.jpg", 404), ("eigen.jpg", 200), ("gemeinsam.jpg", 200), (LOGO_F, 404)):
        f.antworten += [[JOB], sicht]
        assert c.get(f"/api/chat/arbeiter/{AID}/medien/{name}", headers=HB).status_code == status, name


def test_medien_sicht_ausfall_503(umg):
    f, ordner, c = umg
    (ordner / "foto.jpg").write_bytes(b"BILD")
    f.antworten += [[JOB]]
    f.fehler += [None, RuntimeError("db weg")]
    assert c.get(f"/api/chat/arbeiter/{AID}/medien/foto.jpg", headers=HB).status_code == 503


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
    assert len(f.psql.sql) == 2 and "marketing.medien_mandant" in f.psql.sql[0]
    assert "'oktober-angebot-handy.jpg'" in f.psql.sql[0] and IID in f.psql.sql[0]
    assert "'oktober-angebot-handy-2.jpg'" in f.psql.sql[1]


def test_datei_zuordnung_scheitert_loescht_datei_503(umg):
    f, ordner, c = umg
    f.antworten += [[EXPORT_JOB]]
    f.psql.fehler.append(RuntimeError("psql weg"))
    r = c.post(f"/api/chat/arbeiter/{AID}/datei?name=oktober-angebot-handy.jpg", content=_jpeg(), headers=HB)
    assert r.status_code == 503, r.text
    assert list(ordner.iterdir()) == []


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


# ─── Live: Zwischenstand, Vormerken, Stopp (Spec 2026-10-02-newsletter-agent-live) ──


ZW = f"/api/chat/arbeiter/{AID}/zwischenstand"
GESTOPPT = f"/api/chat/arbeiter/{AID}/gestoppt"


def test_zwischenstand_reicht_durch_und_gibt_weiter(umg):
    f, _, c = umg
    f.antworten.append([{"z": {"weiter": True}}])
    r = c.post(ZW, json={"bloecke": _dok(), "schritt": "Titel setzen", "nr": 3}, headers=HB)
    assert r.status_code == 200, r.text
    assert r.json() == {"weiter": True}
    assert len(f.sql) == 1 and "marketing.pult_chat_zwischenstand(" in f.sql[0]
    assert f"'{AID}'::uuid" in f.sql[0] and "'Titel setzen', 3, '5 minutes'::interval" in f.sql[0]
    assert "EmailLayout" in f.sql[0]
    assert "pult_bloecke_fehler" not in f.sql[0]      # Zwischenstand wird nicht validiert


def test_zwischenstand_meldet_stopp(umg):
    f, _, c = umg
    f.antworten.append([{"z": {"weiter": False, "grund": "stopp", "stopp": "behalten"}}])
    r = c.post(ZW, json={"bloecke": {}, "schritt": "", "nr": 0}, headers=HB)
    assert r.json() == {"weiter": False, "grund": "stopp", "stopp": "behalten"}


def test_zwischenstand_ohne_schluessel_401(umg):
    f, _, c = umg
    assert c.post(ZW, json={"bloecke": {}, "schritt": "x", "nr": 1}).status_code == 401
    assert c.post(ZW, json={"bloecke": {}, "schritt": "x", "nr": 1}, headers={"X-Bild-Key": "falsch"}).status_code == 401
    assert f.sql == []


def test_zwischenstand_zu_gross_ohne_sql(umg):
    f, _, c = umg
    gross = {"root": {"type": "Text", "data": {"props": {"text": "x" * (300 * 1024)}}}}
    r = c.post(ZW, json={"bloecke": gross, "schritt": "x", "nr": 1}, headers=HB)
    assert r.status_code == 413 and f.sql == []

    # ohne Content-Length (chunked): gekappt gelesen
    def stuecke():
        for _ in range(40):
            yield b"x" * 10_000
    r = c.post(ZW, content=stuecke(), headers=dict(HB, **{"Content-Type": "application/json"}))
    assert r.status_code == 413 and f.sql == []


def test_zwischenstand_formen_422_ohne_sql(umg):
    f, _, c = umg
    for body in ({"bloecke": [], "schritt": "x", "nr": 1}, {"bloecke": {}, "schritt": "x" * 81, "nr": 1},
                 {"bloecke": {}, "schritt": 5, "nr": 1}, {"bloecke": {}, "schritt": "x", "nr": -1},
                 {"bloecke": {}, "schritt": "x", "nr": True}, {"bloecke": {}, "schritt": "x", "nr": "2"}, []):
        assert c.post(ZW, json=body, headers=HB).status_code == 422, body
    assert c.post(ZW, content=b"{kaputt", headers=HB).status_code == 422
    assert c.post("/api/chat/arbeiter/nicht-uuid/zwischenstand", json={"bloecke": {}, "schritt": "", "nr": 0},
                  headers=HB).status_code == 404
    assert f.sql == []


def test_zwischenstand_db_ablehnung_422(umg):
    f, _, c = umg
    f.fehler.append(_db_fehler("Zwischenstand zu groß"))
    r = c.post(ZW, json={"bloecke": {}, "schritt": "", "nr": 0}, headers=HB)
    assert r.status_code == 422 and r.json()["detail"] == "Zwischenstand zu groß"


def test_stopp_arten_validiert(umg):
    f, _, c = umg
    basis = f"/api/pult/inhalte/{IID}/chat/stopp"
    assert c.post(basis, json={"art": "behalten"}).status_code == 401
    for art in (None, "", "weg", 1):
        assert c.post(basis, json={"art": art}, headers=H).status_code == 422
    assert c.post(basis, json={"art": "behalten", "auftrag": 5}, headers=H).status_code == 422
    assert f.sql == []
    f.antworten += [[{"s": {"abgeschlossen": False, "id": AID}}], [{"s": {"abgeschlossen": True, "id": AID}}]]
    r = c.post(basis, json={"art": "behalten"}, headers=H)
    assert r.status_code == 200 and r.json() == {"abgeschlossen": False}
    assert "marketing.pult_chat_stoppen(" in f.sql[0] and "'behalten', NULL)" in f.sql[0]
    r = c.post(basis, json={"art": "verwerfen", "auftrag": AID}, headers=H)
    assert r.json() == {"abgeschlossen": True} and f"'verwerfen', '{AID}'::uuid)" in f.sql[1]


def test_stopp_veraltet_und_nichts_laeuft(umg):
    f, _, c = umg
    basis = f"/api/pult/inhalte/{IID}/chat/stopp"
    f.antworten.append([{"s": {"abgeschlossen": False, "veraltet": True}}])
    assert c.post(basis, json={"art": "verwerfen", "auftrag": AID}, headers=H).json() == \
        {"abgeschlossen": False, "veraltet": True}
    f.fehler.append(_db_fehler("Der Assistent arbeitet gerade nicht"))
    r = c.post(basis, json={"art": "verwerfen"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Der Assistent arbeitet gerade nicht"


def _faellig(f, stopp, zwischenstand):
    """GET …/chat mit einem faelligen Stopp: aufraeumen, faellig, Auftrag lesen, [Validator], abschliessen, ..."""
    f.antworten += [[{"ok": True}], [{"id": AID}],
                    [{"stopp": stopp, "status": "in_arbeit", "zwischenstand": zwischenstand}]]


def test_faelliger_stopp_behalten_gueltig_mit_bloecken(umg):
    f, _, c = umg
    _faellig(f, "behalten", _dok())
    f.antworten += [[{"f": None}], [{"e": {"status": "fertig", "fassung": 9}}], [], []]
    r = c.get(f"/api/pult/inhalte/{IID}/chat", headers=H)
    assert r.status_code == 200, r.text
    ab = [s for s in f.sql if "marketing.pult_chat_stopp_abschliessen(" in s]
    assert len(ab) == 1 and f"'{AID}'::uuid" in ab[0]
    assert "medien:gs-" in ab[0]                       # Flaechen gerechnet
    assert any("pult_bloecke_fehler(" in s for s in f.sql)
    assert f.sql.index(ab[0]) < next(i for i, s in enumerate(f.sql) if "LIMIT 30" in s)


def test_faelliger_stopp_behalten_ungueltig_mit_null_und_hinweis(umg):
    f, _, c = umg
    _faellig(f, "behalten", _dok())
    f.antworten += [[{"f": "Unbekannter Blocktyp in x"}], [{"e": {"status": "fehler"}}], [], []]
    assert c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).status_code == 200
    ab = next(s for s in f.sql if "marketing.pult_chat_stopp_abschliessen(" in s)
    assert f"'{AID}'::uuid, NULL, " in ab and "Unbekannter Blocktyp in x" in ab and "nicht übernommen" in ab


def test_faelliger_stopp_behalten_flaeche_kaputt_mit_null(umg):
    f, _, c = umg
    _faellig(f, "behalten", _dok(G_WEG))
    f.antworten += [[{"e": {"status": "fehler"}}], [], []]
    assert c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).status_code == 200
    ab = next(s for s in f.sql if "marketing.pult_chat_stopp_abschliessen(" in s)
    assert ", NULL, " in ab and "weg.png fehlt" in ab
    assert all("pult_bloecke_fehler" not in s for s in f.sql)


def test_faelliger_stopp_verwerfen_ohne_rechnen(umg):
    f, ordner, c = umg
    _faellig(f, "verwerfen", _dok())
    f.antworten += [[{"e": {"status": "fehler"}}], [], []]
    assert c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).status_code == 200
    ab = next(s for s in f.sql if "marketing.pult_chat_stopp_abschliessen(" in s)
    assert f"'{AID}'::uuid, NULL, ''" in ab
    assert all("pult_bloecke_fehler" not in s for s in f.sql) and list(ordner.iterdir()) == []


def test_faelliger_stopp_inzwischen_gespeichert_zweiter_versuch_mit_null(umg):
    f, _, c = umg
    _faellig(f, "behalten", _dok())
    f.antworten += [[{"f": None}], [{"e": {"status": "fehler"}}], [], []]
    f.fehler += [None, None, None, None, _db_fehler("Inzwischen gibt es Fassung 9 - neu laden oder als Kopie behalten")]
    assert c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).status_code == 200
    ab = [s for s in f.sql if "marketing.pult_chat_stopp_abschliessen(" in s]
    assert len(ab) == 2 and "medien:gs-" in ab[0]
    assert ", NULL, 'Inzwischen gespeichert – Zwischenstand verworfen'" in ab[1]


def test_faelliger_stopp_db_ablehnung_beim_speichern_schliesst_mit_null_und_hinweis(umg):
    f, _, c = umg
    _faellig(f, "behalten", _dok())
    f.antworten += [[{"f": None}], [{"e": {"status": "fehler"}}], [], []]
    f.fehler += [None, None, None, None, _db_fehler("Block-Dokument ungültig")]
    assert c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).status_code == 200
    ab = [s for s in f.sql if "marketing.pult_chat_stopp_abschliessen(" in s]
    assert len(ab) == 2 and "medien:gs-" in ab[0]
    assert ", NULL, 'Zwischenstand nicht übernommen: Block-Dokument ungültig'" in ab[1]


def test_faelliger_stopp_db_weg_beim_speichern_bleibt_wiederholbar(umg):
    f, _, c = umg
    _faellig(f, "behalten", _dok())
    f.antworten += [[{"f": None}], [], []]
    f.fehler += [None, None, None, None, RuntimeError("ssh weg")]
    assert c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).status_code == 200
    ab = [s for s in f.sql if "marketing.pult_chat_stopp_abschliessen(" in s]
    assert len(ab) == 1                       # 503: kein Ersatzversuch mit NULL, spaeter erneut


def test_faelliger_stopp_fehler_bricht_stand_nicht_ab(umg):
    f, _, c = umg
    f.antworten += [[{"ok": True}], [{"id": AID}, {"id": BID}],
                    [{"stopp": "verwerfen", "status": "in_arbeit", "zwischenstand": None}],
                    [{"stopp": "verwerfen", "status": "in_arbeit", "zwischenstand": None}],
                    [{"e": {"status": "fehler"}}], [], []]
    f.fehler += [None, None, None, RuntimeError("ssh weg")]
    r = c.get(f"/api/pult/inhalte/{IID}/chat", headers=H)
    assert r.status_code == 200, r.text
    ab = [s for s in f.sql if "marketing.pult_chat_stopp_abschliessen(" in s]
    assert len(ab) == 2 and f"'{BID}'::uuid" in ab[1]       # der zweite laeuft trotzdem
    # auch ein Fehler beim Abfragen der faelligen Stopps bricht den Stand nicht ab
    f.sql.clear()
    f.antworten += [[{"ok": True}], [], []]
    f.fehler += [None, RuntimeError("ssh weg")]
    assert c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).status_code == 200


def test_gestoppt_behalten_gueltig(umg):
    f, _, c = umg
    f.antworten += [[{"stopp": "behalten", "status": "in_arbeit", "zwischenstand": None}], [{"f": None}],
                    [{"e": {"status": "fertig", "fassung": 9}}]]
    r = c.post(GESTOPPT, json={"bloecke": _dok()}, headers=HB)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "fertig", "fassung": 9}
    assert "marketing.pult_chat_stopp_abschliessen(" in f.sql[-1] and "medien:gs-" in f.sql[-1]


def test_gestoppt_behalten_ungueltig_mit_null(umg):
    f, _, c = umg
    f.antworten += [[{"stopp": "behalten", "status": "in_arbeit", "zwischenstand": None}],
                    [{"f": "Kaputt"}], [{"e": {"status": "fehler"}}]]
    r = c.post(GESTOPPT, json={"bloecke": _dok()}, headers=HB)
    assert r.json() == {"status": "fehler"}
    assert ", NULL, " in f.sql[-1] and "Kaputt" in f.sql[-1]


def test_gestoppt_ohne_bloecke_nimmt_letzten_zwischenstand(umg):
    f, _, c = umg
    f.antworten += [[{"stopp": "behalten", "status": "in_arbeit", "zwischenstand": _dok()}], [{"f": None}],
                    [{"e": {"status": "fertig", "fassung": 4}}]]
    assert c.post(GESTOPPT, json={"bloecke": None}, headers=HB).json() == {"status": "fertig", "fassung": 4}
    assert "medien:gs-" in f.sql[-1]


def test_gestoppt_verwerfen_und_schon_abgeschlossen(umg):
    f, _, c = umg
    f.antworten += [[{"stopp": "verwerfen", "status": "in_arbeit", "zwischenstand": _dok()}],
                    [{"e": {"status": "fehler"}}]]
    assert c.post(GESTOPPT, json={"bloecke": _dok()}, headers=HB).json() == {"status": "fehler"}
    assert len(f.sql) == 2 and ", NULL, ''" in f.sql[-1]
    # VM war schneller: nichts mehr rechnen, die DB meldet den Endstand
    f.antworten += [[{"stopp": "behalten", "status": "fertig", "zwischenstand": None}],
                    [{"e": {"status": "fertig", "fassung": 9}}]]
    assert c.post(GESTOPPT, json={"bloecke": _dok()}, headers=HB).json() == {"status": "fertig", "fassung": 9}
    assert all("pult_bloecke_fehler" not in s for s in f.sql)


def test_gestoppt_formen_und_schluessel(umg):
    f, _, c = umg
    assert c.post(GESTOPPT, json={"bloecke": None}).status_code == 401
    assert c.post(GESTOPPT, json={"bloecke": []}, headers=HB).status_code == 422
    assert f.sql == []
    assert c.post(GESTOPPT, json={"bloecke": None}, headers=HB).status_code == 404     # unbekannter Auftrag


def test_stopp_zwischenstand_kaputt_schliesst_mit_null_ab(umg, monkeypatch):
    """Jede Nicht-HTTP-Ausnahme beim Rechnen/Pruefen heisst ungueltig: NULL + Hinweis, nie haengen."""
    from spaces.marketing.api import chat as ch
    def kaputt(_b):
        raise TypeError("kaputter Zwischenstand")
    monkeypatch.setattr(ch, "gestaltungen_rechnen", kaputt)
    f, _, c = umg
    _faellig(f, "behalten", _dok())
    f.antworten += [[{"e": {"status": "fehler"}}], [], []]
    assert c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).status_code == 200
    ab = next(s for s in f.sql if "marketing.pult_chat_stopp_abschliessen(" in s)
    assert ", NULL, " in ab and "Zwischenstand nicht übernommen" in ab


def test_stopp_db_nicht_erreichbar_bleibt_zum_wiederholen(umg, monkeypatch):
    """Ein 503 (DB weg) ist keine Ungueltigkeit: nicht mit NULL abschliessen."""
    from spaces.marketing.api import chat as ch
    from fastapi import HTTPException
    def weg(_b):
        raise HTTPException(503, "DB nicht erreichbar")
    monkeypatch.setattr(ch, "_rechnen_und_pruefen", weg)
    f, _, c = umg
    _faellig(f, "behalten", _dok())
    assert c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).status_code == 200
    assert all("pult_chat_stopp_abschliessen(" not in s for s in f.sql)


def _drei_faellige(f):
    f.antworten += [[{"id": AID}, {"id": BID}, {"id": IID}]]
    for _ in range(3):
        f.antworten += [[{"stopp": "verwerfen", "status": "in_arbeit", "zwischenstand": None}],
                        [{"e": {"status": "fehler"}}]]


def test_stopps_hoechstens_zwei_je_stand_abruf(umg):
    f, _, c = umg
    f.antworten += [[{"ok": True}]]
    _drei_faellige(f)
    assert c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).status_code == 200
    assert len([s for s in f.sql if "marketing.pult_chat_stopp_abschliessen(" in s]) == 2


def test_naechster_schliesst_faellige_stopps_ab(umg):
    f, _, c = umg
    f.antworten += [[{"a": None}]]
    _drei_faellige(f)
    r = c.post("/api/chat/arbeiter/naechster", headers=HB)
    assert r.status_code == 200 and r.json() == {"auftrag": None}
    assert len([s for s in f.sql if "marketing.pult_chat_stopp_abschliessen(" in s]) == 2


def test_naechster_ignoriert_fehler_beim_stopp_abschliessen(umg):
    f, _, c = umg
    f.antworten += [[{"a": None}]]
    f.fehler += [None, RuntimeError("ssh weg")]
    r = c.post("/api/chat/arbeiter/naechster", headers=HB)
    assert r.status_code == 200 and r.json() == {"auftrag": None}


def test_zwischenstand_zu_tief_verschachtelt_422(umg):
    f, _, c = umg
    tief = b'{"bloecke":' + b"[" * 100000 + b"]" * 100000 + b',"schritt":"x","nr":1}'
    assert c.post(ZW, content=tief, headers=HB).status_code == 422
    assert f.sql == []


def test_stopp_auftrag_keine_uuid_422(umg):
    f, _, c = umg
    r = c.post(f"/api/pult/inhalte/{IID}/chat/stopp", json={"art": "behalten", "auftrag": "nicht-uuid"}, headers=H)
    assert r.status_code == 422 and f.sql == []


# ─── Kontext: Auswahl und Anhaenge ──────────────────────────────────────


def _anlegen_mit(c, kontext):
    return c.post(f"/api/pult/inhalte/{IID}/chat", json={"nachricht": "x", "kontext": kontext}, headers=H)


GUTE_AUSWAHL = [{"art": "block", "id": "b-1", "flaeche": "f_2", "kurz": "Titel"}, {"art": "ebene", "id": "e1", "kurz": ""}]
GUTE_ANHAENGE = [{"name": "foto.jpg", "art": "bild"}, {"name": "preise.pdf", "art": "dokument"}]


def test_kontext_gueltige_auswahl_und_anhaenge(umg):
    f, _, c = umg
    f.antworten.append([{"s": {"id": AID, "status": "offen"}}])
    r = _anlegen_mit(c, {"auswahl": GUTE_AUSWAHL, "anhaenge": GUTE_ANHAENGE})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("auswahl", [None, "Titel", [], "b-1"])
def test_kontext_auswahl_altform_bleibt_erlaubt(umg, auswahl):
    f, _, c = umg
    f.antworten.append([{"s": {"id": AID, "status": "offen"}}])
    assert _anlegen_mit(c, {"auswahl": auswahl}).status_code == 200


@pytest.mark.parametrize("kontext", [
    {"auswahl": [{"art": "block", "id": f"b{i}", "kurz": "x"} for i in range(9)]},
    {"auswahl": [{"art": "bild", "id": "b1", "kurz": "x"}]},
    {"auswahl": [{"art": "block", "id": "../b", "kurz": "x"}]},
    {"auswahl": [{"art": "block", "id": "b" * 65, "kurz": "x"}]},
    {"auswahl": [{"art": "block", "id": "b1", "flaeche": "a b", "kurz": "x"}]},
    {"auswahl": [{"art": "block", "id": "b1", "kurz": "x" * 81}]},
    {"auswahl": [{"art": "block", "id": "b1", "kurz": 5}]},
    {"auswahl": ["b1"]},
    {"auswahl": {"art": "block"}},
    {"anhaenge": [{"name": f"a{i}.png", "art": "bild"} for i in range(6)]},
    {"anhaenge": [{"name": "foto.jpg", "art": "video"}]},
    {"anhaenge": [{"name": "../x.pdf", "art": "dokument"}]},
    {"anhaenge": [{"name": "x.exe", "art": "dokument"}]},
    {"anhaenge": ["foto.jpg"]},
    {"anhaenge": "foto.jpg"},
])
def test_kontext_ungueltig_422_ohne_sql(umg, kontext):
    f, _, c = umg
    r = _anlegen_mit(c, kontext)
    assert r.status_code == 422 and r.json()["detail"], r.text
    assert f.sql == []


def test_kontext_ueber_4kb_422(umg):
    f, _, c = umg
    assert _anlegen_mit(c, {"fenster": "x" * 4100}).status_code == 422 and f.sql == []


def test_medien_liefert_dokumente_nur_ueber_arbeiterroute(umg):
    f, ordner, c = umg
    (ordner / "preise.pdf").write_bytes(b"%PDF-1.4")
    f.antworten += [[JOB], _sicht()]
    r = c.get(f"/api/chat/arbeiter/{AID}/medien/preise.pdf", headers=HB)
    assert r.status_code == 200 and r.content == b"%PDF-1.4"
    assert "pdf" in r.headers["content-type"]
    for boese in ("..%2Fx.pdf", ".x.pdf", "x.exe"):
        f.antworten.append([JOB])
        assert c.get(f"/api/chat/arbeiter/{AID}/medien/{boese}", headers=HB).status_code == 404
    # die Medienliste (Bilder fuer den Agenten) bleibt bilderrein
    from spaces.marketing.api import chat
    assert "preise.pdf" not in chat._medien(lambda n: True)


def test_kontext_anhaenge_null_gilt_als_leer(umg):
    f, _, c = umg
    f.antworten.append([{"id": AID}])
    assert _anlegen_mit(c, {"anhaenge": None}).status_code == 200


# --- Task 2 (Newsletter-Freigabe): offene Rueckmeldungen im Auftrag ---


def test_naechster_traegt_offene_rueckmeldungen(umg):
    f, ordner, c = umg
    rm = [{"text": "Preis fehlt", "von": "Anna", "am": "2026-10-07", "fassung": 2}]
    f.antworten += [[{"a": {"id": AID, "inhalt": IID, "art": "chat", "nachricht": "n", "mandant": "vibemind"}}],
                    _sicht(), rm]
    a = c.post("/api/chat/arbeiter/naechster", headers=HB).json()["auftrag"]
    assert a["rueckmeldungen_offen"] == rm
    assert "marketing.rueckmeldungen" in f.sql[2] and "erledigt_am IS NULL" in f.sql[2] and IID in f.sql[2]


def test_naechster_ohne_rueckmeldungen_leere_liste(umg):
    f, ordner, c = umg
    f.antworten += [[{"a": {"id": AID, "inhalt": IID, "art": "chat", "nachricht": "n", "mandant": "vibemind"}}],
                    _sicht(), []]
    assert c.post("/api/chat/arbeiter/naechster", headers=HB).json()["auftrag"]["rueckmeldungen_offen"] == []


def test_naechster_rueckmeldungen_lesefehler_verliert_auftrag_nicht(umg):
    f, ordner, c = umg
    f.antworten += [[{"a": {"id": AID, "inhalt": IID, "art": "chat", "nachricht": "n", "mandant": "vibemind"}}],
                    _sicht()]
    f.fehler += [None, None, RuntimeError("db weg")]
    r = c.post("/api/chat/arbeiter/naechster", headers=HB)
    assert r.status_code == 200, r.text
    a = r.json()["auftrag"]
    assert a["id"] == AID and a["rueckmeldungen_offen"] == [] and a["mandant_name"] == "Vibemind"


# --- Marke per Chat, Schlussrunde I5: Markenlogo als Mediendatei der Firma -----------


def _png_b64():
    import base64
    puffer = io.BytesIO()
    Image.new("RGB", (20, 10), "#b45309").save(puffer, "PNG")
    return puffer.getvalue(), base64.b64encode(puffer.getvalue()).decode("ascii")


def test_naechster_traegt_das_markenlogo_als_medium(umg):
    import hashlib
    f, ordner, c = umg
    roh, b64 = _png_b64()
    f.antworten += [[{"a": {"id": AID, "art": "chat", "nachricht": "n", "mandant": "vibemind"}}], _sicht(),
                    [{"laden": "Vibemind", "layout": "marke-vibemind",
                      "gestalt": {"akzent": "#b45309", "logo": f"data:image/png;base64,{b64}"}}]]
    a = c.post("/api/chat/arbeiter/naechster", headers=HB).json()["auftrag"]
    name = f"logo-vibemind-{hashlib.sha256(roh).hexdigest()[:10]}.png"
    assert a["markenlogo"] == f"medien:{name}"
    assert (ordner / name).read_bytes() == roh
    assert a["medien"][0] == name                                       # der Agent darf es setzen
    assert "layout_vorlagen" in f.sql[2] and "'vibemind'" in f.sql[2]


def test_naechster_ohne_markenlogo_und_lesefehler_verliert_nichts(umg):
    f, ordner, c = umg
    f.antworten += [[{"a": {"id": AID, "art": "chat", "nachricht": "n", "mandant": "vibemind"}}], _sicht(),
                    [{"laden": "Vibemind", "layout": None, "gestalt": None}]]
    a = c.post("/api/chat/arbeiter/naechster", headers=HB).json()["auftrag"]
    assert a["markenlogo"] is None and a["markenlogo_dunkel"] is None
    f.antworten += [[{"a": {"id": AID, "art": "chat", "nachricht": "n", "mandant": "vibemind"}}], _sicht()]
    f.fehler += [None, None, RuntimeError("db weg")]
    r = c.post("/api/chat/arbeiter/naechster", headers=HB)
    assert r.status_code == 200 and r.json()["auftrag"]["markenlogo"] is None


def test_naechster_traegt_auch_die_dunkle_fassung(umg):
    import base64
    import hashlib
    f, ordner, c = umg
    roh, b64 = _png_b64()
    puffer = io.BytesIO()
    Image.new("RGBA", (20, 10), (255, 255, 255, 255)).save(puffer, "PNG")
    dunkel = puffer.getvalue()
    f.antworten += [[{"a": {"id": AID, "art": "chat", "nachricht": "n", "mandant": "vibemind"}}], _sicht(),
                    [{"laden": "Vibemind", "layout": "marke-vibemind",
                      "gestalt": {"akzent": "#b45309", "logo": f"data:image/png;base64,{b64}",
                                  "logo_dunkel": "data:image/png;base64," + base64.b64encode(dunkel).decode()}}]]
    a = c.post("/api/chat/arbeiter/naechster", headers=HB).json()["auftrag"]
    hell_name = f"logo-vibemind-{hashlib.sha256(roh).hexdigest()[:10]}.png"
    dunkel_name = f"logo-vibemind-{hashlib.sha256(dunkel).hexdigest()[:10]}.png"
    assert a["markenlogo"] == f"medien:{hell_name}" and a["markenlogo_dunkel"] == f"medien:{dunkel_name}"
    assert a["medien"][:2] == [hell_name, dunkel_name]
    assert (ordner / dunkel_name).read_bytes() == dunkel


# ─── Denkspur (Spec 2026-10-09, Migration 065) ──────────────────────────

DENKEN = f"/api/chat/arbeiter/{AID}/denken"
SCHRITT = {"zeit": "08:03:41", "text": "Frage an Claude"}


def test_denken_route_schreibt_und_meldet_ok(umg):
    f, _, c = umg
    f.antworten.append([{"ok": True}])
    r = c.post(DENKEN, headers=HB, json={"denken": "Let me think", "schritte": [SCHRITT]})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert len(f.sql) == 1 and "marketing.pult_chat_denken(" in f.sql[0]
    assert f"'{AID}'::uuid" in f.sql[0] and "Let me think" in f.sql[0] and "Frage an Claude" in f.sql[0]


def test_denken_route_verloren_ist_409(umg):
    f, _, c = umg
    f.antworten.append([{"ok": False}])
    r = c.post(DENKEN, headers=HB, json={"denken": "", "schritte": []})
    assert r.status_code == 409 and r.json()["detail"] == "Auftrag nicht mehr in Arbeit"


@pytest.mark.parametrize("body", [
    {"denken": 5, "schritte": []},
    {"denken": "x", "schritte": {}},
    {"denken": "x" * 20101, "schritte": []},
    {"denken": "x", "schritte": [{"zeit": "08:00:00", "text": "t" * 201}]},
    {"denken": "x", "schritte": [{"zeit": "08:00:00"}]},
    {"denken": "x", "schritte": [{"zeit": "08:00:00", "text": "s"}] * 61},
])
def test_denken_route_form_422(umg, body):
    f, _, c = umg
    assert c.post(DENKEN, headers=HB, json=body).status_code == 422
    assert f.sql == []


def test_denken_route_ohne_schluessel_401(umg):
    f, _, c = umg
    assert c.post(DENKEN, json={"denken": "", "schritte": []}).status_code == 401
    assert c.post(DENKEN, json={"denken": "", "schritte": []}, headers=H).status_code == 401
    assert f.sql == []


def test_denken_route_zu_gross_413(umg):
    f, _, c = umg
    assert c.post(DENKEN, headers=HB, json={"denken": "x" * (300 * 1024), "schritte": []}).status_code == 413
    assert f.sql == []


def _lauf(id_, status, **extra):
    return {"id": id_, "art": "chat", "nachricht": "n", "antwort": "", "status": status, "hinweise": [], "ergebnis": {},
            "fassung_vorher": 2, "fassung_nachher": None, "denken": "", "schritte": [], "schritt": "", "schritt_nr": 0,
            "stopp": None, "bild_hinweise": [], "erstellt_am": id_, "sortiert_am": id_, **extra}


def test_stand_raeumt_zuerst_auf(umg):
    f, _, c = umg
    fertig = _lauf(BID, "fertig", fassung_nachher=2, bild_hinweise=["Bild für held nicht erzeugt: Zeitüberschreitung"])
    f.antworten += [[{"ok": True}], [], [fertig, _lauf(AID, "wartet")], [{"n": 2}]]
    r = c.get(f"/api/pult/inhalte/{IID}/chat", headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "marketing.pult_chat_aufraeumen(" in f.sql[0] and "marketing.pult_chat_stopp_faellig()" in f.sql[1]
    assert "chat_auftraege" in f.sql[2] and "LIMIT 30" in f.sql[2] and "wartet" not in f.sql[2]
    assert "marketing.pult_chat_bild_hinweise(ergebnis)" in f.sql[2] and "max(fassung)" in f.sql[3]
    assert d["live"] is None and "vorgemerkt" not in d and d["neueste_fassung"] == 2
    assert d["laeuft"] is True and [z["id"] for z in d["verlauf"]] == [BID, AID]
    assert d["verlauf"][0]["bild_hinweise"] == ["Bild für held nicht erzeugt: Zeitüberschreitung"]
    assert "sortiert_am" not in d["verlauf"][0] and d["verlauf"][1]["status"] == "wartet"


def test_stand_live_nur_bei_genau_einer_laufenden_runde(umg):
    f, _, c = umg
    eine = _lauf(AID, "in_arbeit", schritt="Titel setzen", schritt_nr=3, stopp="verwerfen", denken="d", schritte=[SCHRITT],
                 fassung_vorher=5)
    f.antworten += [[{"ok": True}], [], [eine], [{"n": 5}], [{"zwischenstand": _dok()}]]
    d = c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).json()
    assert d["live"] == {"schritt": "Titel setzen", "schritt_nr": 3, "zwischenstand": _dok(), "stopp": "verwerfen",
                         "denken": "d", "schritte": [SCHRITT]}
    assert "zwischenstand" in f.sql[4] and AID in f.sql[4]
    f.sql.clear()
    f.antworten += [[{"ok": True}], [], [_lauf(AID, "in_arbeit"), _lauf(BID, "offen")], [{"n": 5}]]
    d = c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).json()
    assert d["live"] is None and d["laeuft"] is True and len(f.sql) == 4
    assert [z["schritt_nr"] for z in d["verlauf"]] == [0, 0]


def test_stand_ohne_live_wenn_die_einzige_runde_auf_einer_alten_fassung_aufbaut(umg):
    """Final-Review I4: A und B starten auf Fassung 5, A speichert 6. B laeuft allein weiter, sein Zwischenstand baut
    aber auf 5 auf - als Live-Stand verschwaende A's Aenderung von der Flaeche."""
    f, _, c = umg
    b = _lauf(AID, "in_arbeit", schritt="Farbe", schritt_nr=2, fassung_vorher=5)
    a = _lauf(BID, "fertig", fassung_vorher=5, fassung_nachher=6)
    f.antworten += [[{"ok": True}], [], [a, b], [{"n": 6}]]
    d = c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).json()
    assert d["live"] is None and d["laeuft"] is True and len(f.sql) == 4
    f.sql.clear()
    f.antworten += [[{"ok": True}], [], [a, _lauf(AID, "in_arbeit", fassung_vorher=6)], [{"n": 6}],
                    [{"zwischenstand": _dok()}]]
    d = c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).json()
    assert d["live"] is not None and d["live"]["zwischenstand"] == _dok()


def test_chat_stand_liefert_denken_und_schritte(umg):
    f, _, c = umg
    alt = _lauf(BID, "fertig", fassung_nachher=2, denken=None, schritte=None)
    neu = _lauf(AID, "in_arbeit", denken="Let me think", schritte=[SCHRITT])
    f.antworten += [[{"ok": True}], [], [alt, neu], [{"n": 2}], [{"zwischenstand": None}]]
    j = c.get(f"/api/pult/inhalte/{IID}/chat", headers=H).json()
    assert j["verlauf"][0]["denken"] == "" and j["verlauf"][0]["schritte"] == []
    assert j["verlauf"][1]["denken"] == "Let me think" and j["verlauf"][1]["schritte"] == [SCHRITT]
    assert j["live"]["denken"] == "Let me think" and j["live"]["schritte"][0]["text"] == "Frage an Claude"
    assert "coalesce(denken, '')" in f.sql[2]


def test_neueste_liefert_die_neueste_fassung(umg):
    f, _, c = umg
    f.antworten += [[JOB], [{"fassung": 5, "bloecke": _dok()}]]
    r = c.get(f"/api/chat/arbeiter/{AID}/neueste", headers=HB)
    assert r.status_code == 200 and r.json() == {"fassung": 5, "bloecke": _dok()}
    assert "ORDER BY fassung DESC LIMIT 1" in f.sql[1] and IID in f.sql[1]
    assert c.get(f"/api/chat/arbeiter/{AID}/neueste").status_code == 401
    f.antworten.append([dict(JOB, gueltig=False)])
    assert c.get(f"/api/chat/arbeiter/{AID}/neueste", headers=HB).status_code == 409


def test_fertig_neuere_fassung_meldet_veraltet_statt_zurueck(umg):
    f, _, c = umg
    f.antworten += [[JOB], [{"f": None}]]
    f.fehler += [None, None, _db_fehler("Inzwischen gibt es Fassung 9 - neu laden oder als Kopie behalten")]
    body = {"antwort": "x", "bloecke": _dok(), "basis": 8, "hinweise": ["Übersprungen: Titel"]}
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json=body, headers=HB)
    assert r.status_code == 200 and r.json() == {"status": "veraltet"}
    assert not any("pult_chat_zurueck(" in s for s in f.sql)
    assert f.sql[-1].rstrip().endswith(", 8) AS e") and "Übersprungen: Titel" in f.sql[-1]


@pytest.mark.parametrize("extra", [{"basis": 0}, {"basis": "5"}, {"basis": True}, {"hinweise": "x"},
                                   {"hinweise": ["x" * 301]}, {"hinweise": ["x"] * 41}, {"hinweise": [5]}])
def test_fertig_basis_und_hinweise_formen_422(umg, extra):
    f, _, c = umg
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json={"antwort": "x", **extra}, headers=HB)
    assert r.status_code == 422 and f.sql == []


def test_fertig_merkt_die_bildauftraege_an_der_runde(umg):
    f, _, c = umg
    f.antworten += [[JOB], [{"f": None}], [{"e": {"fassung": 7}}], [{"id": BID}], [{"ok": True}]]
    body = {"antwort": "Fertig", "bloecke": _dok(), "bildauftraege": [{"platz": "held", "modus": "neu", "hinweis": "K"}]}
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json=body, headers=HB)
    assert r.status_code == 200 and r.json()["bildauftraege"] == [BID]
    assert "marketing.pult_chat_bild_ids(" in f.sql[-1] and BID in f.sql[-1]


def test_fertig_bildauftraege_merken_scheitert_still(umg):
    f, _, c = umg
    f.antworten += [[JOB], [{"f": None}], [{"e": {"fassung": 7}}], [{"id": BID}]]
    f.fehler += [None, None, None, None, RuntimeError("ssh weg")]
    body = {"antwort": "Fertig", "bloecke": _dok(), "bildauftraege": [{"platz": "held", "modus": "neu", "hinweis": "K"}]}
    r = c.post(f"/api/chat/arbeiter/{AID}/fertig", json=body, headers=HB)
    assert r.status_code == 200 and r.json()["fassung"] == 7


def test_gestoppt_mit_basis_und_hinweisen(umg):
    f, _, c = umg
    f.antworten += [[{"stopp": "behalten", "status": "in_arbeit", "zwischenstand": None}], [{"f": None}],
                    [{"e": {"status": "fertig", "fassung": 9}}]]
    r = c.post(GESTOPPT, json={"bloecke": _dok(), "basis": 7, "hinweise": ["Übersprungen: Titel"]}, headers=HB)
    assert r.status_code == 200 and r.json() == {"status": "fertig", "fassung": 9}
    assert f.sql[-1].rstrip().endswith(", 7) AS e") and "Übersprungen: Titel" in f.sql[-1]
