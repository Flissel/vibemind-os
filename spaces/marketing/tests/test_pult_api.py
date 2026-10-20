"""Pult-Router der Marketing-API (Spec §3.4). Ohne echte Datenbank: _db wird
gefaelscht; geprueft werden Schluesselpflicht, Formen und Weitergabe an die
DB-Funktionen aus 050/051.

Abweichung vom Brief (task-3-brief.md): 051 aenderte marketing.pult_entscheiden
auf die 5-arg-Form (p_inhalt, p_fassung, p_urteil, p_von, p_grund) -- die
4-arg-Form gibt es nicht mehr. test_entscheiden schickt deshalb "fassung" im
Body und prueft dessen Weitergabe als 2. SQL-Argument; zusaetzlich prueft
test_entscheiden_ohne_gueltige_fassung_422, dass eine fehlende/ungueltige
fassung mit 422 ohne DB-Zugriff abgewiesen wird.

Fix-Runde 1 (Controller-Weisung, task-3-report.md "Fix round 1"): weitere
Tests fuer strenge (streng=True) DB-Zugriffe ueberall, fail-closed 503 bei
Verbindungs-/SSH-/Container-Fehlern statt Rohtext oder stillschweigend
leeren Antworten, die fail-closed 503-Gestaltpruefung in /layouts/vorschau
(der eigentliche Sicherheitsbefund -- eine fehlgeschlagene Pruefung durfte
NIE als "keine Beanstandung" durchgehen), die X-Pult-Key/X-API-Key-Trennung
in server.py, und die hmac/ValueError-Randfaelle."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from spaces.marketing.api import pult, server
from spaces.marketing.sync import _db

KEY = "test-pult-key"
GESTALT = {"grund": "#0f2422", "text": "#cfe3df", "akzent": "#5eead4",
           "flaeche": "#1d3b39", "text_hell": "#e9fbf6", "text_leise": "#8aa3a0",
           "gold": "#fbbf24", "handlung_text": "#0f2422"}
FELDER = {"betreff": "B", "vorschautext": "", "abschnitte": [{"titel": "", "text": "T"}],
          "knopf_text": "", "knopf_link": ""}
IID = "11111111-1111-1111-1111-111111111111"


class FalscheDB:
    """Faelschung von sync._db: protokolliert SQL, liefert vorbereitete
    Zeilen, und kann auf Wunsch (Fix-Runde 1) beim n-ten Aufruf werfen --
    fuer Verbindungs-/SSH-/Container-Fehler-Szenarien, die query_via_docker
    bei streng=True als Exception statt als leere Liste zeigt."""
    def __init__(self):
        self.sql = []
        self.antworten = []          # Liste von Listen (Zeilen) in Aufrufreihenfolge
        self.fehler = []             # Liste von Exception|None in Aufrufreihenfolge, ueberstimmt antworten

    def _naechste(self, sql):
        self.sql.append(sql)
        if self.fehler:
            e = self.fehler.pop(0)
            if e is not None:
                raise e
        return self.antworten.pop(0) if self.antworten else []

    def query(self, sql, params=None, container=None, streng=False):
        return self._naechste(sql)

    def one(self, sql, params=None, container=None, streng=False):
        zeilen = self._naechste(sql)
        return zeilen[0] if zeilen else None


@pytest.fixture
def db(monkeypatch):
    f = FalscheDB()
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setenv("MARKETING_PULT_KEY", KEY)
    return f


@pytest.fixture
def c():
    return TestClient(server.app)


H = {"X-Pult-Key": KEY}


def test_ohne_schluessel_401(db, c):
    assert c.get("/api/pult/inhalte").status_code == 401
    assert c.get("/api/pult/inhalte", headers={"X-Pult-Key": "falsch"}).status_code == 401
    assert db.sql == []                          # nichts erreicht die DB


def test_ohne_konfiguration_503(monkeypatch, c):
    monkeypatch.delenv("MARKETING_PULT_KEY", raising=False)
    assert c.get("/api/pult/inhalte", headers=H).status_code == 503


def test_inhalte_liste(db, c):
    db.antworten = [[{"id": "a1", "art": "newsletter", "titel": "X", "status": "entwurf",
                      "erstellt_am": "2026-09-04", "fassungen": 1, "layout": "dunkel"}]]
    r = c.get("/api/pult/inhalte?mandant=vibemind&status=entwurf", headers=H)
    assert r.status_code == 200 and r.json()["inhalte"][0]["id"] == "a1"
    assert "mandant = 'vibemind'" in db.sql[0] and "status = 'entwurf'" in db.sql[0]


def test_inhalte_liste_mit_format_der_neuesten_fassung(db, c):
    db.antworten = [[{"id": "a1", "art": "newsletter", "titel": "X", "status": "entwurf",
                      "erstellt_am": "2026-09-04", "fassungen": 2, "layout": None, "format": "seite"}]]
    r = c.get("/api/pult/inhalte?mandant=vibemind", headers=H)
    assert r.status_code == 200 and r.json()["inhalte"][0]["format"] == "seite"
    assert ("(SELECT f.format FROM marketing.inhalt_fassungen f WHERE f.inhalt = i.id "
            "ORDER BY f.fassung DESC LIMIT 1) AS format") in db.sql[0]


def test_filter_wird_nicht_blind_eingesetzt(db, c):
    r = c.get("/api/pult/inhalte?art=newsletter';drop table x;--", headers=H)
    assert r.status_code == 422 and db.sql == []


def test_fassung_speichern_ruft_db_funktion(db, c):
    db.antworten = [[{"fassung": 5}]]
    r = c.post(f"/api/pult/inhalte/{IID}/fassungen",
               headers=H, json={"felder": FELDER, "layout": "dunkel", "von": "felix"})
    assert r.status_code == 200 and r.json() == {"fassung": 5}
    assert "marketing.pult_fassung_speichern(" in db.sql[0] and "'betreiber'" in db.sql[0]


def test_ungueltige_id_404_ohne_db(db, c):
    assert c.get("/api/pult/inhalte/keine-uuid", headers=H).status_code == 404
    assert db.sql == []


def test_db_ablehnung_wird_422_mit_grund(db, c, monkeypatch):
    def wirft(*a, **k):
        raise RuntimeError("ERROR:  Nur Entwuerfe lassen sich bearbeiten")
    monkeypatch.setattr(_db, "query_one", wirft)
    r = c.post(f"/api/pult/inhalte/{IID}/fassungen",
               headers=H, json={"felder": FELDER, "layout": "dunkel", "von": "felix"})
    assert r.status_code == 422 and "Nur Entwuerfe" in r.json()["detail"]


def test_vorschau_html_und_pdf(db, c):
    zeile = {"felder": FELDER, "gestalt": GESTALT,
             "pflichtteil": {"impressum": "I", "abmelde_hinweis": "A"}}
    db.antworten = [[zeile]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=mail", headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    db.antworten = [[zeile]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=pdf", headers=H)
    assert r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")


def test_layout_vorschau_prueft_gestalt(db, c):
    db.antworten = [[{"fehler": "rundung muss eine Zahl von 0 bis 24 sein"}]]
    r = c.post("/api/pult/layouts/vorschau", headers=H,
               json={"gestalt": dict(GESTALT, rundung=999), "mandant": "vibemind", "format": "mail"})
    assert r.status_code == 422 and "rundung" in r.json()["detail"]


def test_layout_vorschau_nutzt_beispiel(db, c):
    db.antworten = [[{"fehler": None}], [{"pflichtteil": {"impressum": "I", "abmelde_hinweis": ""}}]]
    r = c.post("/api/pult/layouts/vorschau", headers=H,
               json={"gestalt": GESTALT, "mandant": "vibemind", "format": "handy"})
    assert r.status_code == 200 and "Neuigkeiten aus der Werkstatt" in r.text
    assert 'width="380"' in r.text


def test_entscheiden(db, c):
    db.antworten = [[{"status": "freigegeben"}]]
    r = c.post(f"/api/pult/inhalte/{IID}/entscheiden",
               headers=H, json={"fassung": 2, "urteil": "freigeben", "von": "felix", "grund": ""})
    assert r.json() == {"status": "freigegeben"}
    assert "marketing.pult_entscheiden(" in db.sql[0] and ", 2, " in db.sql[0]


def test_entscheiden_ohne_gueltige_fassung_422(db, c):
    r = c.post(f"/api/pult/inhalte/{IID}/entscheiden",
               headers=H, json={"urteil": "freigeben", "von": "felix", "grund": ""})
    assert r.status_code == 422 and db.sql == []
    r = c.post(f"/api/pult/inhalte/{IID}/entscheiden",
               headers=H, json={"fassung": 0, "urteil": "freigeben", "von": "felix", "grund": ""})
    assert r.status_code == 422 and db.sql == []
    r = c.post(f"/api/pult/inhalte/{IID}/entscheiden",
               headers=H, json={"fassung": "zwei", "urteil": "freigeben", "von": "felix", "grund": ""})
    assert r.status_code == 422 and db.sql == []


# ─── Fix-Runde 1 ─────────────────────────────────────────────────────────
# Befund 1 (wichtig, planpflichtig): /layouts/vorschau rendert eine vom
# Aufrufer geschickte, NOCH NICHT gespeicherte Gestalt. Schlaegt die
# Gestalt-Pruefung selbst fehl (DB-Fehler), MUSS das 503 sein -- niemals
# ungeprueft rendern.

def test_layout_vorschau_pruefung_db_fehler_rendert_nicht(db, c, monkeypatch):
    aufgerufen = []
    monkeypatch.setattr(pult, "_rendern", lambda *a, **k: aufgerufen.append(1))
    db.fehler = [RuntimeError("connection refused")]  # kein "ERROR:" -> Verbindungsfehler, nicht Ablehnung
    r = c.post("/api/pult/layouts/vorschau", headers=H,
               json={"gestalt": GESTALT, "mandant": "vibemind", "format": "mail"})
    assert r.status_code == 503
    assert aufgerufen == []                       # _rendern wurde nie aufgerufen
    assert len(db.sql) == 1                        # nur die Pruefung, kein Mandant-Nachschlag danach


def test_layout_vorschau_pruefung_keine_zeile_503(db, c):
    db.antworten = [[]]                            # query_one -> None, aber KEIN Fehler geworfen
    r = c.post("/api/pult/layouts/vorschau", headers=H,
               json={"gestalt": GESTALT, "mandant": "vibemind", "format": "mail"})
    assert r.status_code == 503


def test_layout_vorschau_nul_zeichen_wird_nie_gerendert(db, c):
    # Realer Ausloeser (Review-Befund): Postgres lehnt "\u0000" beim
    # ::jsonb-Cast ab ("unsupported Unicode escape sequence"); die Pruefung
    # selbst schlaegt fehl. Vorher lief das lax -> [] -> fehler=None ->
    # ungeprueft gerendert (Farben landen roh im style-Attribut).
    db.fehler = [RuntimeError("ERROR:  unsupported Unicode escape sequence")]
    boese = dict(GESTALT, grund="#0f2422\u0000")
    r = c.post("/api/pult/layouts/vorschau", headers=H,
               json={"gestalt": boese, "mandant": "vibemind", "format": "mail"})
    assert r.status_code == 503
    assert "0f2422" not in r.text


def test_layout_vorschau_mandant_nachschlag_db_fehler_503(db, c):
    db.antworten = [[{"fehler": None}]]             # Gestalt-Pruefung ok
    db.fehler = [None, RuntimeError("connection refused")]  # 2. Aufruf (Mandant) schlaegt fehl
    r = c.post("/api/pult/layouts/vorschau", headers=H,
               json={"gestalt": GESTALT, "mandant": "vibemind", "format": "mail"})
    assert r.status_code == 503


# Befund 2 (wichtig): /api/pult/* muss von der X-API-Key-Middleware
# ausgenommen sein -- das Sales-UI schickt nur X-Pult-Key.

def test_pult_route_ausgenommen_von_api_key_middleware(db, c, monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "geheimer-dienst-schluessel")
    r = c.get("/api/pult/inhalte?mandant=vibemind", headers=H)   # nur X-Pult-Key
    assert r.status_code == 200


def test_pult_route_verlangt_weiterhin_x_pult_key(db, c, monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "geheimer-dienst-schluessel")
    r = c.get("/api/pult/inhalte?mandant=vibemind")              # gar kein Key-Header
    assert r.status_code == 401
    assert db.sql == []


def test_nicht_pult_route_verlangt_weiterhin_x_api_key(db, c, monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "geheimer-dienst-schluessel")
    ohne = c.get("/api/stats")
    assert ohne.status_code == 401
    mit = c.get("/api/stats", headers={"X-API-Key": "geheimer-dienst-schluessel"})
    assert mit.status_code != 401                                # Middleware liess durch


# Minors

def test_hmac_vergleich_nicht_ascii_header_401_nicht_500(db, c):
    # httpx verweigert einen rohen nicht-ASCII str als Header-Wert bereits
    # client-seitig (UnicodeEncodeError) -- ueber die Leitung kommen Header
    # als Bytes an und werden von Starlette per latin-1 dekodiert (ASGI/HTTP-
    # Spec), was durchaus zu einem nicht-ASCII Python-str fuehren kann. Bytes
    # hier simulieren genau das.
    r = c.get("/api/pult/inhalte", headers={"X-Pult-Key": "schlüssel-falsch".encode("utf-8")})
    assert r.status_code == 401


def test_db_verbindungsfehler_wird_503_ohne_rohtext(db, c):
    db.fehler = [RuntimeError("ssh: connect to host offload-vm.example port 22: Connection refused")]
    r = c.post(f"/api/pult/inhalte/{IID}/fassungen",
               headers=H, json={"felder": FELDER, "layout": "dunkel", "von": "felix"})
    assert r.status_code == 503
    assert r.json()["detail"] == "Marketing-Datenbank nicht erreichbar"
    assert "offload-vm" not in r.text and "Connection refused" not in r.text


def test_nul_byte_in_layout_wird_422_ohne_db(db, c):
    r = c.post(f"/api/pult/inhalte/{IID}/fassungen",
               headers=H, json={"felder": FELDER, "layout": "dun\x00kel", "von": "felix"})
    assert r.status_code == 422 and db.sql == []


def test_nul_byte_in_entscheiden_feldern_wird_422_ohne_db(db, c):
    r = c.post(f"/api/pult/inhalte/{IID}/entscheiden",
               headers=H, json={"fassung": 1, "urteil": "freigeben", "von": "fe\x00lix", "grund": ""})
    assert r.status_code == 422 and db.sql == []
    r = c.post(f"/api/pult/inhalte/{IID}/entscheiden",
               headers=H, json={"fassung": 1, "urteil": "freigeben", "von": "felix", "grund": "gr\x00und"})
    assert r.status_code == 422 and db.sql == []


def test_uebersicht_db_fehler_503(db, c):
    db.fehler = [RuntimeError("connection refused")]
    assert c.get("/api/pult/uebersicht?mandant=vibemind", headers=H).status_code == 503


def test_inhalte_liste_db_fehler_503(db, c):
    db.fehler = [RuntimeError("connection refused")]
    assert c.get("/api/pult/inhalte?mandant=vibemind", headers=H).status_code == 503


def test_inhalt_db_fehler_503(db, c):
    db.fehler = [RuntimeError("connection refused")]
    assert c.get(f"/api/pult/inhalte/{IID}", headers=H).status_code == 503


def test_vorschau_db_fehler_503(db, c):
    db.fehler = [RuntimeError("connection refused")]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=mail", headers=H)
    assert r.status_code == 503


def test_layouts_liste_db_fehler_503(db, c):
    db.fehler = [RuntimeError("connection refused")]
    assert c.get("/api/pult/layouts?mandant=vibemind", headers=H).status_code == 503


def test_layout_fassung_speichern_verlangt_gestalt_dict(db, c):
    r = c.post("/api/pult/layouts/dunkel/fassungen", headers=H,
               json={"gestalt": "nicht-ein-dict", "von": "felix"})
    assert r.status_code == 422 and db.sql == []
    r = c.post("/api/pult/layouts/dunkel/fassungen", headers=H, json={"von": "felix"})
    assert r.status_code == 422 and db.sql == []


def test_layout_fassung_speichern_ruft_db_funktion(db, c):
    db.antworten = [[{"fassung": 3}]]
    r = c.post("/api/pult/layouts/dunkel/fassungen", headers=H,
               json={"gestalt": GESTALT, "von": "felix"})
    assert r.status_code == 200 and r.json() == {"fassung": 3}
    assert "marketing.pult_layout_speichern(" in db.sql[0]


# ─── Final-Review Fix-Welle (final-fix-findings.md) ──────────────────────
# I2: eine alte Fassung wird mit IHRER Layout-Fassung gezeigt, nicht mit dem
# heutigen Aussehen; die gerenderte Gestalt wird vorher geprueft.

def test_vorschau_nutzt_gepinnte_layout_fassung(db, c):
    gepinnt = dict(GESTALT, grund="#123456", text="#abcdef")
    db.antworten = [[{"felder": FELDER, "gestalt": gepinnt, "gestalt_fehler": None,
                      "pflichtteil": {"impressum": "I", "abmelde_hinweis": "A"}}]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=mail", headers=H)
    assert r.status_code == 200
    sql = db.sql[0]
    assert "LEFT JOIN marketing.layout_fassungen lf" in sql
    assert "lf.layout = f.layout" in sql and "lf.fassung = f.layout_fassung" in sql
    assert "coalesce(lf.gestalt, l.gestalt) AS gestalt" in sql
    assert "marketing.pult_gestalt_fehler(coalesce(lf.gestalt, l.gestalt))" in sql
    assert "#123456" in r.text and "#abcdef" in r.text
    assert GESTALT["grund"] not in r.text


def test_vorschau_ungueltige_gespeicherte_gestalt_422(db, c, monkeypatch):
    aufgerufen = []
    monkeypatch.setattr(pult, "_rendern", lambda *a, **k: aufgerufen.append(1))
    db.antworten = [[{"felder": FELDER, "gestalt": dict(GESTALT, grund="rot"),
                      "gestalt_fehler": "grund muss eine Farbe #rrggbb sein",
                      "pflichtteil": {"impressum": "I", "abmelde_hinweis": "A"}}]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=mail", headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "grund muss eine Farbe #rrggbb sein"
    assert aufgerufen == []


# I3: der alte Freigabeweg (broadcast_proposals) wird mitgeliefert.

def test_inhalt_liefert_alten_weg(db, c):
    db.antworten = [[{"id": IID, "mandant": "vibemind", "art": "post", "titel": "X",
                      "status": "entwurf", "alter_weg": {"status": "pending_approval", "kanal": "linkedin"}}],
                    [{"fassung": 1, "felder": FELDER, "layout": "dunkel", "urheber": "agent",
                      "erstellt_am": "x"}]]
    r = c.get(f"/api/pult/inhalte/{IID}", headers=H)
    assert r.status_code == 200
    j = r.json()
    assert j["alter_weg"] == {"status": "pending_approval", "kanal": "linkedin"}
    assert "alter_weg" not in j["inhalt"]
    assert "LEFT JOIN marketing.broadcast_proposals p ON p.id = i.herkunft_proposal" in db.sql[0]
    assert "p.status" in db.sql[0] and "p.channel" in db.sql[0]


def test_inhalt_ohne_herkunft_alter_weg_null(db, c):
    db.antworten = [[{"id": IID, "mandant": "vibemind", "art": "newsletter", "titel": "X",
                      "status": "entwurf", "alter_weg": None}], []]
    r = c.get(f"/api/pult/inhalte/{IID}", headers=H)
    assert r.status_code == 200 and r.json()["alter_weg"] is None


# --- Task 3 (Newsletter-Editor E1): Bloecke, Vorlagen, Editor-Vorschau ---
DOK = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["t"]}},
       "t": {"type": "Text", "data": {"props": {"text": "Hallo Pult"}}}}


def test_bloecke_speichern(db, c):
    db.antworten = [[{"fassung": 4}]]
    r = c.post(f"/api/pult/inhalte/{IID}/bloecke", headers=H,
               json={"basis_fassung": 3, "betreff": "B", "vorschautext": "", "bloecke": DOK})
    assert r.status_code == 200 and r.json() == {"fassung": 4}
    sql = db.sql[-1]
    assert "marketing.pult_bloecke_speichern(" in sql and ", 3, " in sql and "'betreiber'" in sql and "false" in sql


def test_bloecke_als_kopie_und_agent(db, c):
    db.antworten = [[{"fassung": 5}]]
    c.post(f"/api/pult/inhalte/{IID}/bloecke", headers=H,
           json={"basis_fassung": 3, "betreff": "B", "vorschautext": "", "bloecke": DOK,
                 "als_kopie": True, "urheber": "agent"})
    assert "true" in db.sql[-1] and "'agent'" in db.sql[-1]


def test_bloecke_ohne_dict_oder_basis_422_ohne_db(db, c):
    for body in ({"basis_fassung": 1, "betreff": "B", "bloecke": "nein"},
                 {"basis_fassung": "x", "betreff": "B", "bloecke": DOK},
                 {"basis_fassung": True, "betreff": "B", "bloecke": DOK},
                 {"basis_fassung": -1, "betreff": "B", "bloecke": DOK},
                 {"basis_fassung": 1, "betreff": "B", "bloecke": DOK, "als_kopie": "ja"},
                 {"basis_fassung": 1, "betreff": "B", "bloecke": DOK, "urheber": "fremd"}):
        assert c.post(f"/api/pult/inhalte/{IID}/bloecke", headers=H, json=body).status_code == 422
    assert db.sql == []


def test_bloecke_ohne_schluessel_401(db, c):
    r = c.post(f"/api/pult/inhalte/{IID}/bloecke", json={"basis_fassung": 1, "betreff": "B", "bloecke": DOK})
    assert r.status_code == 401 and db.sql == []


def test_konflikt_wird_422_mit_text(db, c, monkeypatch):
    def wirft(*a, **k):
        raise RuntimeError("ERROR:  Inzwischen gibt es Fassung 7 - neu laden oder als Kopie behalten")
    monkeypatch.setattr(_db, "query_one", wirft)
    r = c.post(f"/api/pult/inhalte/{IID}/bloecke", headers=H,
               json={"basis_fassung": 3, "betreff": "B", "vorschautext": "", "bloecke": DOK})
    assert r.status_code == 422 and r.json()["detail"].startswith("Inzwischen gibt es Fassung 7")


def _bloecke_zeile(**extra):
    z = {"felder": {"betreff": "B", "vorschautext": ""}, "format": "bloecke", "bloecke": DOK,
         "gestalt": GESTALT, "pflichtteil": {"impressum": "I", "abmelde_hinweis": ""}, "gestalt_fehler": None}
    z.update(extra)
    return z


def test_vorschau_bloecke_nutzt_bild_basis(db, c):
    db.antworten = [[_bloecke_zeile()]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=mail&bild_basis=https://h.ts.net/marketing/bild/t/", headers=H)
    assert r.status_code == 200 and "Hallo Pult" in r.text


def test_vorschau_bloecke_ueberspringt_gestaltpruefung(db, c):
    # ein (fuer felder gueltiger) gestalt_fehler darf den Bloecke-Zweig nicht stoppen
    db.antworten = [[_bloecke_zeile(gestalt_fehler="Farbe ungueltig")]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=handy", headers=H)
    assert r.status_code == 200 and "Hallo Pult" in r.text


def test_vorschau_bloecke_pdf(db, c, monkeypatch):
    """Paket C3: Editor-Newsletter als PDF - dasselbe Mail-HTML, von Chromium gesetzt; laden darf er nur
    die eigenen Bild- und Schriftadressen."""
    from spaces.marketing.claw import pdf_druck
    gesehen = {}

    def druck(html, erlaubt):
        gesehen["html"], gesehen["erlaubt"] = html, erlaubt
        return b"%PDF-1.7 probe"
    monkeypatch.setattr(pdf_druck, "html_zu_pdf", druck)
    monkeypatch.setenv("MARKETING_PDF_HOSTS", "h.ts.net")
    db.antworten = [[_bloecke_zeile()]]
    basis = "https://h.ts.net/marketing/bild/1760000000." + "a" * 64 + "/"
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=pdf&bild_basis={basis}", headers=H)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content == b"%PDF-1.7 probe"
    assert "Hallo Pult" in gesehen["html"]
    assert gesehen["erlaubt"] == [basis, "https://h.ts.net/marketing/schrift/"]


def test_vorschau_bloecke_pdf_fremder_host_laedt_nichts(db, c, monkeypatch):
    """Sicherheitspruefung 19.10.: die erlaubten Adressen kommen aus der Server-Konfiguration, nicht aus der
    Anfrage - ein bild_basis auf einen anderen Host darf Chromium nichts laden lassen."""
    from spaces.marketing.claw import pdf_druck
    gesehen = {}
    monkeypatch.setattr(pdf_druck, "html_zu_pdf", lambda html, erlaubt: gesehen.setdefault("e", erlaubt) and b"%PDF" or b"%PDF")
    monkeypatch.setenv("MARKETING_PDF_HOSTS", "h.ts.net")
    db.antworten = [[_bloecke_zeile()]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=pdf&bild_basis=https://intern.example/marketing/bild/t/",
              headers=H)
    assert r.status_code == 200 and gesehen["e"] == []
    monkeypatch.delenv("MARKETING_PDF_HOSTS")
    db.antworten = [[_bloecke_zeile()]]
    c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=pdf&bild_basis=https://h.ts.net/marketing/bild/t/", headers=H)
    assert gesehen["e"] == []


def test_pdf_erlaubt_nur_erwartete_pfade(monkeypatch):
    """Sicherheitspruefung 19.10. (2): auch auf erlaubtem Host nur /marketing/bild/<seg>/ und /marketing/schrift/."""
    from spaces.marketing.api import pult
    monkeypatch.setenv("MARKETING_PDF_HOSTS", "h.ts.net")
    tok = "1760000000." + "a" * 64
    bild = f"https://h.ts.net/marketing/bild/{tok}/"
    assert pult._pdf_erlaubt(bild, "https://h.ts.net/marketing/schrift/") == [bild, "https://h.ts.net/marketing/schrift/"]
    assert pult._pdf_erlaubt("https://h.ts.net/", "https://h.ts.net/admin/") == []
    assert pult._pdf_erlaubt("https://h.ts.net/marketing/bild/t/", "https://h.ts.net/marketing/schrift/a/") == []
    # Sicherheitspruefung 19.10. (3): kodierte Punkt-Segmente, Abfrage, Anker
    for boese in ("https://h.ts.net/marketing/bild/%2e%2e/", "https://h.ts.net/marketing/bild/../",
                  f"https://h.ts.net/marketing/bild/{tok}/?x=1", "https://h.ts.net/marketing/schrift/#a",
                  "https://h.ts.net/marketing/schrift/%2e%2e/"):
        assert pult._pdf_erlaubt(boese) == [], boese


def test_vorschau_bloecke_pdf_ohne_chromium_503(db, c, monkeypatch):
    from spaces.marketing.claw import pdf_druck

    def druck(html, erlaubt):
        raise pdf_druck.PdfDruckFehler("PDF-Druck ist hier nicht eingerichtet")
    monkeypatch.setattr(pdf_druck, "html_zu_pdf", druck)
    db.antworten = [[_bloecke_zeile()]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=pdf", headers=H)
    assert r.status_code == 503 and "nicht eingerichtet" in r.json()["detail"]


def test_vorschau_bloecke_renderfehler_422(db, c, monkeypatch):
    from spaces.marketing.claw import bloecke_mjml
    def wirft(*a, **k):
        raise bloecke_mjml.RenderFehler("Unbekannter Block")
    monkeypatch.setattr(bloecke_mjml, "rendern", wirft)
    db.antworten = [[_bloecke_zeile()]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=mail", headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Unbekannter Block"


def test_bild_basis_wird_geprueft(db, c):
    for schlecht in ("http://h/", "https://h/x", 'https://h/"/', "javascript:x/"):
        r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=mail&bild_basis={schlecht}", headers=H)
        assert r.status_code == 422, schlecht
    assert db.sql == []


def test_inhalt_liefert_format_und_bloecke(db, c):
    db.antworten = [[{"id": IID, "mandant": "vibemind", "art": "newsletter", "titel": "T", "status": "entwurf",
                      "freigegebene_fassung": None, "entschieden_von": None, "entschieden_am": None,
                      "grund": None, "alter_weg": None}],
                    [{"fassung": 1, "felder": {}, "layout": None, "urheber": "betreiber",
                      "erstellt_am": "x", "format": "bloecke", "bloecke": DOK}]]
    r = c.get(f"/api/pult/inhalte/{IID}", headers=H)
    assert r.status_code == 200 and r.json()["fassungen"][0]["format"] == "bloecke"
    import re
    assert re.search(r"SELECT[^;]*\bformat\b[^;]*\bbloecke\b[^;]*FROM marketing\.inhalt_fassungen", db.sql[1])


def test_vorlagen_liste_und_aus_vorlage(db, c):
    db.antworten = [[{"name": "leer", "beschreibung": "L", "status": "freigegeben", "fassung": 1}]]
    r = c.get("/api/pult/vorlagen?mandant=vibemind", headers=H)
    assert r.json()["vorlagen"][0]["name"] == "leer"
    db.antworten = [[{"bloecke": DOK}], [{"laden": "L", "layout": None, "gestalt": None}], [{"id": IID}]]
    r = c.post("/api/pult/inhalte/aus_vorlage", headers=H, json={"vorlage": "leer", "titel": "Oktober"})
    assert r.json() == {"id": IID} and "marketing.pult_inhalt_aus_vorlage(" in db.sql[-1]


def test_aus_vorlage_name_geprueft(db, c):
    r = c.post("/api/pult/inhalte/aus_vorlage", headers=H, json={"vorlage": "../x", "titel": "T"})
    assert r.status_code == 422 and db.sql == []


def test_vorlage_vorschau(db, c):
    db.antworten = [[{"bloecke": DOK, "beschreibung": "Beschr", "pflichtteil": {"impressum": "I"}}],
                    [{"laden": "L", "layout": None, "gestalt": None}]]
    r = c.get("/api/pult/vorlagen/leer/vorschau?format=mail", headers=H)
    assert r.status_code == 200 and "Hallo Pult" in r.text
    assert c.get("/api/pult/vorlagen/../x/vorschau", headers=H).status_code in (404, 422)
    assert c.get("/api/pult/vorlagen/leer/vorschau?format=pdf", headers=H).status_code == 422
    db.antworten = [[]]
    assert c.get("/api/pult/vorlagen/leer/vorschau", headers=H).status_code == 404


def test_vorlagen_status_filter(db, c):
    db.antworten = [[]]
    assert c.get("/api/pult/vorlagen?status=vorschlag", headers=H).status_code == 200
    assert "status = 'vorschlag'" in db.sql[-1]
    n = len(db.sql)
    assert c.get("/api/pult/vorlagen?status=entwurf", headers=H).status_code == 422
    assert len(db.sql) == n


def test_aus_vorlage_titel_laenge(db, c):
    for titel in ("", "   ", "x" * 201):
        r = c.post("/api/pult/inhalte/aus_vorlage", headers=H, json={"vorlage": "leer", "titel": titel})
        assert r.status_code == 422
    assert db.sql == []
    db.antworten = [[{"bloecke": DOK}], [{"laden": "L", "layout": None, "gestalt": None}], [{"id": IID}]]
    assert c.post("/api/pult/inhalte/aus_vorlage", headers=H, json={"vorlage": "leer", "titel": "x" * 200}).status_code == 200

def test_aus_vorlage_fuellt_rollen_und_uebergibt_dokument(db, c, monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    vorlage = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["marke_wort", "band"], "canvasColor": "#ffffff",
               "rollen": {"band/data/style/backgroundColor": "akzent", "marke_wort/data/props/text": "laden"}}},
               "marke_wort": {"type": "Heading", "data": {"props": {"text": "[Laden]"}}},
               "band": {"type": "Text", "data": {"style": {"backgroundColor": "#c2410c"}, "props": {"text": "x"}}}}
    db.antworten = [[{"bloecke": vorlage}],
                    [{"laden": "Radhaus Jena", "layout": "radhaus-nl", "gestalt": {"akzent": "#123456"}}],
                    [{"id": "11111111-1111-1111-1111-111111111111"}]]
    r = c.post("/api/pult/inhalte/aus_vorlage", json={"vorlage": "studio", "titel": "Oktober", "mandant": "radhaus"},
               headers=H)
    assert r.status_code == 200, r.text
    sql = db.sql[2]
    assert "pult_inhalt_aus_vorlage" in sql and "#123456" in sql and "Radhaus Jena" in sql and "'radhaus-nl'" in sql
    assert "fuer_alle" in db.sql[0]


def test_aus_vorlage_ohne_layout_ersatzpalette(db, c, monkeypatch, tmp_path):
    monkeypatch.delenv("MARKETING_BILD_ORDNER", raising=False)
    vorlage = {"root": {"type": "EmailLayout", "data": {"childrenIds": [], "rollen": {}}}}
    db.antworten = [[{"bloecke": vorlage}], [{"laden": "L", "layout": None, "gestalt": None}], [{"id": "x"}]]
    assert c.post("/api/pult/inhalte/aus_vorlage", json={"vorlage": "studio", "titel": "T", "mandant": "radhaus"},
                  headers=H).status_code == 200


def test_aus_vorlage_unbekannt_422(db, c):
    db.antworten = [[]]
    r = c.post("/api/pult/inhalte/aus_vorlage", json={"vorlage": "studio", "titel": "T"}, headers=H)
    assert r.status_code == 422


def test_vorlagenliste_eigene_und_fuer_alle(db, c):
    db.antworten = [[{"name": "studio", "beschreibung": "", "status": "freigegeben", "fassung": 1}]]
    assert c.get("/api/pult/vorlagen?mandant=radhaus", headers=H).status_code == 200
    assert "fuer_alle" in db.sql[0] and "zurueckgezogen" in db.sql[0]


def test_erzeugt_ordner_nur_wenn_beschreibbar(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    assert pult._erzeugt_ordner() == str(tmp_path)
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path / "gibtsnicht"))
    assert pult._erzeugt_ordner() == ""
    monkeypatch.delenv("MARKETING_BILD_ORDNER", raising=False)
    assert pult._erzeugt_ordner() == ""


# --- Schlussrunde E1 (final-fix-findings.md) ---

def test_api_laedt_ohne_mjml_und_feldvorschau_geht(tmp_path):
    """I6: fehlt mjml-python, startet die API trotzdem; die Feld-Vorschau geht,
    eine Bloecke-Vorschau ist 422 mit deutschem Grund (eigener Prozess, damit
    der Import wirklich frisch laeuft)."""
    import subprocess
    import sys
    import textwrap
    from pathlib import Path
    wurzel = Path(__file__).resolve().parents[3]
    skript = tmp_path / "ohne_mjml.py"
    skript.write_text(textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(wurzel)!r})
        sys.modules["mjml"] = None
        os.environ["MARKETING_PULT_KEY"] = "k"
        from fastapi.testclient import TestClient
        from spaces.marketing.api import server
        from spaces.marketing.sync import _db
        G = {GESTALT!r}
        zeilen = [
            [{{"felder": {FELDER!r}, "format": "felder", "bloecke": None, "gestalt": G,
               "pflichtteil": {{"impressum": "I", "abmelde_hinweis": "A"}}, "gestalt_fehler": None}}],
            [{{"felder": {{"betreff": "B"}}, "format": "bloecke", "bloecke": {DOK!r}, "gestalt": G,
               "pflichtteil": {{"impressum": "I"}}, "gestalt_fehler": None}}]]
        _db.query_one = lambda sql, streng=False, **k: zeilen.pop(0)[0]
        c = TestClient(server.app)
        h = {{"X-Pult-Key": "k"}}
        r1 = c.get("/api/pult/inhalte/{IID}/vorschau?fassung=1&format=mail", headers=h)
        r2 = c.get("/api/pult/inhalte/{IID}/vorschau?fassung=2&format=mail", headers=h)
        print(r1.status_code, r2.status_code, r2.json()["detail"])
    """), encoding="utf-8")
    p = subprocess.run([sys.executable, str(skript)], capture_output=True, text=True, encoding="utf-8",
                       cwd=str(wurzel), timeout=120)
    assert p.returncode == 0, p.stderr[-2000:]
    zeile = p.stdout.strip().splitlines()[-1]
    assert zeile.startswith("200 422 ") and "mjml-python fehlt" in zeile, zeile


def test_inhalt_bloecke_nur_fuer_neueste_fassung(db, c):
    db.antworten = [[{"id": IID, "mandant": "vibemind", "art": "newsletter", "titel": "T", "status": "entwurf",
                      "alter_weg": None}],
                    [{"fassung": 3, "felder": {}, "layout": None, "urheber": "betreiber", "erstellt_am": "x",
                      "format": "bloecke", "bloecke": DOK},
                     {"fassung": 2, "felder": {}, "layout": None, "urheber": "agent", "erstellt_am": "x",
                      "format": "bloecke", "bloecke": DOK}]]
    r = c.get(f"/api/pult/inhalte/{IID}", headers=H)
    f = r.json()["fassungen"]
    assert f[0]["bloecke"] == DOK and f[1]["bloecke"] is None
    assert "max(fassung) OVER ()" in db.sql[1]


def test_in_bloecke_uebernehmen(db, c):
    db.antworten = [[{"fassung": 4}]]
    r = c.post(f"/api/pult/inhalte/{IID}/in_bloecke", headers=H, json={"von": "felix"})
    assert r.status_code == 200 and r.json() == {"fassung": 4}
    assert f"marketing.pult_in_bloecke_uebernehmen('{IID}'::uuid, 'felix')" in db.sql[-1]


def test_in_bloecke_ohne_von_ist_betreiber(db, c):
    db.antworten = [[{"fassung": 2}]]
    assert c.post(f"/api/pult/inhalte/{IID}/in_bloecke", headers=H, json={}).status_code == 200
    assert ", 'betreiber')" in db.sql[-1]


def test_in_bloecke_ablehnung_422_mit_grund(db, c, monkeypatch):
    def wirft(*a, **k):
        raise RuntimeError("ERROR:  Dieser Newsletter ist schon im Editor-Format")
    monkeypatch.setattr(_db, "query_one", wirft)
    r = c.post(f"/api/pult/inhalte/{IID}/in_bloecke", headers=H, json={"von": "felix"})
    assert r.status_code == 422 and r.json()["detail"] == "Dieser Newsletter ist schon im Editor-Format"


def test_in_bloecke_schluessel_id_und_verbindung(db, c, monkeypatch):
    assert c.post(f"/api/pult/inhalte/{IID}/in_bloecke", json={}).status_code == 401
    assert c.post("/api/pult/inhalte/kein-uuid/in_bloecke", headers=H, json={}).status_code == 404
    assert c.post(f"/api/pult/inhalte/{IID}/in_bloecke", headers=H, json={"von": 5}).status_code == 422
    assert db.sql == []
    def wirft(*a, **k):
        raise RuntimeError("ssh: connect to host offload-vm: Connection refused")
    monkeypatch.setattr(_db, "query_one", wirft)
    r = c.post(f"/api/pult/inhalte/{IID}/in_bloecke", headers=H, json={})
    assert r.status_code == 503 and "offload" not in r.text


# --- Medien-Verweise (Betreiber 01.10.2026: Bilder aus der Bibliothek loeschen) ---

def test_medien_verweise_ohne_schluessel_401(db, c):
    assert c.get("/api/pult/medien/verweise?name=a.jpg").status_code == 401
    assert db.sql == []


def test_medien_verweise_liefert_treffer_einmal_je_inhalt(db, c):
    db.antworten = [[{"id": "a1", "titel": "Herbst", "status": "entwurf", "art": "newsletter", "wo": "aktuell"},
                     {"id": "a1", "titel": "Herbst", "status": "entwurf", "art": "newsletter", "wo": "aktuell"},
                     {"id": "b2", "titel": "Sommer", "status": "freigegeben", "art": "newsletter",
                      "wo": "freigegeben"}]]
    r = c.get("/api/pult/medien/verweise", params={"name": "nl-0cc7a17f-kopf_bild.jpg"}, headers=H)
    assert r.status_code == 200
    assert [v["id"] for v in r.json()["verweise"]] == ["a1", "b2"]
    sql = db.sql[0]
    # exakt der gespeicherte Wert samt Anfuehrungszeichen - kein Praefix-Treffer auf aehnliche Namen
    assert '"medien:nl-0cc7a17f-kopf_bild.jpg"' in sql
    assert "abgelehnt" in sql and "freigegebene_fassung" in sql and "ORDER BY fassung DESC" in sql


def test_medien_verweise_umlaut_wie_jsonb_text(db, c):
    db.antworten = [[]]
    r = c.get("/api/pult/medien/verweise", params={"name": "Bäckerei Foto.png"}, headers=H)
    assert r.status_code == 200 and r.json() == {"verweise": []}
    assert '"medien:Bäckerei Foto.png"' in db.sql[0]


@pytest.mark.parametrize("name", ["", "../x.jpg", "a/b.jpg", "a\b.jpg", 'a".jpg', "..", "x" * 201])
def test_medien_verweise_ungueltiger_name_422(db, c, name):
    r = c.get("/api/pult/medien/verweise", params={"name": name}, headers=H)
    assert r.status_code == 422 and db.sql == []


def test_medien_verweise_db_weg_503(db, c):
    db.fehler = [RuntimeError("ssh kaputt")]
    r = c.get("/api/pult/medien/verweise", params={"name": "a.jpg"}, headers=H)
    assert r.status_code == 503 and "ssh" not in r.text


def test_schrift_basis_aus_bild_basis():
    assert pult.schrift_basis_aus("https://ui.tail.ts.net:8445/marketing/bild/123.abc/") == \
        "https://ui.tail.ts.net:8445/marketing/schrift/"
    assert pult.schrift_basis_aus("") == "" and pult.schrift_basis_aus("https://x/andere/") == ""



# --- Task 7 Fix-Runde 1: Vorlagen-Vorschau zeigt die gefuellte Vorlage des Ladens ---

VORLAGE_ROLLEN = {"root": {"type": "EmailLayout", "data": {
    "childrenIds": ["marke_logo", "marke_wort", "band", "glow"], "canvasColor": "#ffffff",
    "rollen": {"band/data/style/backgroundColor": "akzent", "marke_wort/data/props/text": "laden",
               "marke_logo/data/props/url": "logo", "glow/data/props/url": "glow_bild"}}},
    "marke_logo": {"type": "Image", "data": {"style": {}, "props": {"url": "medien:platzhalter-3x1.png", "width": 120}}},
    "marke_wort": {"type": "Heading", "data": {"style": {}, "props": {"text": "[Laden]"}}},
    "band": {"type": "Text", "data": {"style": {"backgroundColor": "#c2410c"}, "props": {"text": "Band"}}},
    "glow": {"type": "Image", "data": {"style": {}, "props": {"url": "medien:tech-glow-c2410c.jpg", "width": 600,
                                                              "grafik": True}}}}


def test_vorlage_vorschau_fuellt_fuer_den_laden(db, c, monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    db.antworten = [[{"bloecke": VORLAGE_ROLLEN, "beschreibung": "B", "pflichtteil": {"impressum": "I"}}],
                    [{"laden": "Radhaus Jena", "layout": "radhaus-nl", "gestalt": {"akzent": "#123456"}}]]
    r = c.get("/api/pult/vorlagen/studio/vorschau?format=mail&mandant=radhaus"
              "&bild_basis=https://h.ts.net/marketing/bild/t/", headers=H)
    assert r.status_code == 200, r.text
    assert "#123456" in r.text and "Radhaus Jena" in r.text and "[Laden]" not in r.text
    assert "tech-glow-c2410c" not in r.text and "tech-glow-123456.jpg" in r.text
    assert "platzhalter-3x1" not in r.text                  # kein Logo: Logo-Block faellt weg
    assert "newsletter_vorlagen" in db.sql[0] and "layout_vorlagen" in db.sql[1]


def test_fuellen_ist_ein_gemeinsamer_helfer(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    fertig, layout = pult._vorlage_fuellen(VORLAGE_ROLLEN, "radhaus",
                                           {"laden": "Radhaus Jena", "layout": "nl", "gestalt": {"akzent": "#123456"}})
    assert layout == "nl" and fertig["band"]["data"]["style"]["backgroundColor"] == "#123456"
    assert fertig["marke_wort"]["data"]["props"]["text"] == "Radhaus Jena" and "marke_logo" not in fertig


# --- Task 2 (Newsletter-Freigabe): Verwerfen mit Grund, Einreich-Felder, Rueckmeldungen ---

@pytest.mark.parametrize("grund", ["", "   ", None])
def test_ablehnen_ohne_grund_422_ohne_sql(db, c, grund):
    body = {"fassung": 1, "urteil": "ablehnen", "von": "Anna"}
    if grund is not None:
        body["grund"] = grund
    r = c.post(f"/api/pult/inhalte/{IID}/entscheiden", json=body, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Bitte gib einen Grund an" and db.sql == []


def test_ablehnen_mit_grund_geht_durch_und_freigeben_ohne_grund_auch(db, c):
    db.antworten = [[{"status": "abgelehnt"}], [{"status": "freigegeben"}]]
    r = c.post(f"/api/pult/inhalte/{IID}/entscheiden",
               json={"fassung": 1, "urteil": "ablehnen", "von": "Anna", "grund": "passt nicht"}, headers=H)
    assert r.status_code == 200 and r.json() == {"status": "abgelehnt"}
    r = c.post(f"/api/pult/inhalte/{IID}/entscheiden",
               json={"fassung": 1, "urteil": "freigeben", "von": "Anna"}, headers=H)
    assert r.status_code == 200 and len(db.sql) == 2


def test_inhalt_liefert_einreichfelder_und_rueckmeldungen(db, c):
    rm = [{"id": "r2", "fassung": 2, "text": "Preis fehlt", "von": "Anna", "am": "2026-10-07", "erledigt": False},
          {"id": "r1", "fassung": 1, "text": "Betreff", "von": "Anna", "am": "2026-10-06", "erledigt": True}]
    db.antworten = [[{"id": IID, "mandant": "vibemind", "art": "newsletter", "titel": "X", "status": "eingereicht",
                      "eingereichte_fassung": 2, "eingereicht_am": "2026-10-07", "eingereicht_von": "Anna",
                      "alter_weg": None}],
                    [{"fassung": 2, "felder": FELDER, "layout": "dunkel", "urheber": "agent", "erstellt_am": "x"}],
                    rm]
    r = c.get(f"/api/pult/inhalte/{IID}", headers=H)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["inhalt"]["eingereichte_fassung"] == 2 and j["inhalt"]["eingereicht_von"] == "Anna"
    assert j["rueckmeldungen"] == rm
    assert "i.eingereichte_fassung" in db.sql[0] and "i.eingereicht_am" in db.sql[0] and "i.eingereicht_von" in db.sql[0]
    assert "marketing.rueckmeldungen" in db.sql[2] and "ORDER BY r.am DESC" in db.sql[2]


def test_inhalt_ohne_rueckmeldungen_leere_liste_und_lesefehler_503(db, c):
    db.antworten = [[{"id": IID, "mandant": "vibemind", "art": "newsletter", "titel": "X", "status": "entwurf",
                      "alter_weg": None}], []]
    r = c.get(f"/api/pult/inhalte/{IID}", headers=H)
    assert r.status_code == 200 and r.json()["rueckmeldungen"] == []
    db.antworten = [[{"id": IID, "mandant": "vibemind", "art": "newsletter", "titel": "X", "status": "entwurf",
                      "alter_weg": None}], []]
    db.fehler = [None, None, RuntimeError("weg")]
    assert c.get(f"/api/pult/inhalte/{IID}", headers=H).status_code == 503


def test_inhalte_filter_eingereicht_und_uebersicht_zaehlt_ihn(db, c):
    db.antworten = [[{"id": "a1", "art": "newsletter", "titel": "X", "status": "eingereicht",
                      "erstellt_am": "2026-10-07", "fassungen": 2, "layout": None}]]
    r = c.get("/api/pult/inhalte?status=eingereicht", headers=H)
    assert r.status_code == 200 and r.json()["inhalte"][0]["status"] == "eingereicht"
    assert "i.status = 'eingereicht'" in db.sql[0]
    db.antworten = [[], [{"status": "eingereicht", "n": 2}]]
    z = c.get("/api/pult/uebersicht", headers=H).json()["zaehler"]
    assert z == {"entwurf": 0, "eingereicht": 2, "freigegeben": 0, "abgelehnt": 0, "zurueckgegeben": 0}


def test_inhalte_liefert_offene_rueckmeldungen_je_zeile(db, c):
    db.antworten = [[{"id": "a1", "art": "newsletter", "titel": "X", "status": "entwurf",
                      "erstellt_am": "2026-10-07", "fassungen": 2, "layout": None,
                      "offene_rueckmeldungen": 2}]]
    r = c.get("/api/pult/inhalte", headers=H)
    assert r.json()["inhalte"][0]["offene_rueckmeldungen"] == 2
    sql = db.sql[0]
    assert "marketing.rueckmeldungen" in sql and "erledigt_am IS NULL" in sql and "AS offene_rueckmeldungen" in sql


def test_uebersicht_zaehlt_zurueckgegeben_getrennt_von_entwurf(db, c):
    db.antworten = [[], [{"status": "entwurf", "n": 3}, {"status": "zurueckgegeben", "n": 1}]]
    z = c.get("/api/pult/uebersicht", headers=H).json()["zaehler"]
    assert z["entwurf"] == 3 and z["zurueckgegeben"] == 1
    sql = db.sql[1]
    assert "'zurueckgegeben'" in sql and "erledigt_am IS NULL" in sql and "i.status = 'entwurf'" in sql


# --- Marke per Chat (064): Schriftpaar in der Vorlage, Markierung am Inhalt ---

def test_fuellen_setzt_schriftpaar_aus_der_gestalt(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    gestalt = {"akzent": "#123456", "schriften": {"anzeige": "playfair", "text": "manrope"}}
    fertig, _ = pult._vorlage_fuellen(VORLAGE_ROLLEN, "radhaus", {"laden": "L", "layout": None, "gestalt": gestalt})
    assert fertig["root"]["data"]["schriften"] == {"anzeige": "playfair", "text": "manrope"}
    assert "schriften" not in VORLAGE_ROLLEN["root"]["data"]       # Eingabe unveraendert


def test_fuellen_ohne_schriften_laesst_die_vorlage_unveraendert(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    vorlage = {"root": {"type": "EmailLayout", "data": {"childrenIds": [], "rollen": {},
                                                        "schriften": {"anzeige": "cormorant", "text": "dm-sans"}}}}
    for gestalt in ({"akzent": "#123456"}, {"schriften": {"anzeige": "gibtsnicht", "text": "manrope"}},
                    {"schriften": {"anzeige": "playfair"}}, {"schriften": "x"}):
        fertig, _ = pult._vorlage_fuellen(vorlage, "radhaus", {"laden": "L", "layout": None, "gestalt": gestalt})
        assert fertig["root"]["data"]["schriften"] == {"anzeige": "cormorant", "text": "dm-sans"}, gestalt


def test_fuellen_mit_logo_verweis(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    vorlage = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["marke_logo", "marke_wort"],
                        "rollen": {"marke_logo/data/props/url": "logo", "marke_wort/data/props/text": "laden"}}},
               "marke_logo": {"type": "Image", "data": {"props": {"url": "medien:platzhalter-3x1.png"}}},
               "marke_wort": {"type": "Heading", "data": {"props": {"text": "[Laden]"}}}}
    fertig, _ = pult._vorlage_fuellen(vorlage, "radhaus", {"laden": "L", "layout": None, "gestalt": None},
                                      logo="medien:marke-radhaus-logo-0123456789.png")
    assert fertig["marke_logo"]["data"]["props"]["url"] == "medien:marke-radhaus-logo-0123456789.png"
    assert "marke_wort" not in fertig


def test_inhalt_liefert_marke_geaendert_am(db, c):
    db.antworten = [[{"id": IID, "mandant": "vibemind", "status": "entwurf", "alter_weg": None,
                      "marke_geaendert_am": "2026-10-07 10:00:00+00"}], [], []]
    r = c.get(f"/api/pult/inhalte/{IID}", headers=H)
    assert r.status_code == 200 and r.json()["inhalt"]["marke_geaendert_am"] == "2026-10-07 10:00:00+00"
    assert "i.marke_geaendert_am::text AS marke_geaendert_am" in db.sql[0]


def _png_bytes(b, h):
    import io
    from PIL import Image
    puffer = io.BytesIO()
    Image.new("RGBA", (b, h), (10, 20, 30, 255)).save(puffer, "PNG")
    return puffer.getvalue()


def _kasten_vorlage(grund="#ffffff"):
    return {"root": {"type": "EmailLayout", "data": {"canvasColor": grund, "childrenIds": ["marke_logo", "marke_wort"],
                        "rollen": {"marke_logo/data/props/url": "logo", "marke_wort/data/props/text": "laden"}}},
            "marke_logo": {"type": "Image", "data": {"props": {"url": "medien:platzhalter-3x1.png",
                                                               "width": 150, "height": 50}}},
            "marke_wort": {"type": "Heading", "data": {"props": {"text": "[Laden]"}}}}


def test_fuellen_passt_logo_verweis_in_den_kasten(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    (tmp_path / "marke-radhaus-logo-0123456789.png").write_bytes(_png_bytes(218, 184))
    fertig, _ = pult._vorlage_fuellen(_kasten_vorlage(), "radhaus", {"laden": "L", "layout": None, "gestalt": None},
                                      logo="medien:marke-radhaus-logo-0123456789.png")
    p = fertig["marke_logo"]["data"]["props"]
    assert (p["width"], p["height"]) == (59, 50)


def test_fuellen_dunkle_flaeche_passt_logo_dunkel_ein(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    (tmp_path / "hell.png").write_bytes(_png_bytes(218, 184))
    (tmp_path / "dunkel.png").write_bytes(_png_bytes(600, 100))
    fertig, _ = pult._vorlage_fuellen(_kasten_vorlage("#080b13"), "radhaus",
                                      {"laden": "L", "layout": None, "gestalt": None},
                                      logo="medien:hell.png", logo_dunkel="medien:dunkel.png")
    p = fertig["marke_logo"]["data"]["props"]
    assert p["url"] == "medien:dunkel.png" and (p["width"], p["height"]) == (150, 25)


def test_fuellen_logo_datei_fehlt_laesst_kasten(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    fertig, _ = pult._vorlage_fuellen(_kasten_vorlage(), "radhaus", {"laden": "L", "layout": None, "gestalt": None},
                                      logo="medien:weg.png")
    p = fertig["marke_logo"]["data"]["props"]
    assert (p["width"], p["height"]) == (150, 50)


def test_versand_nur_freigegebene_bloecke(db, c):
    dok = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["t"]}},
           "t": {"type": "Text", "data": {"style": {}, "props": {"text": "Hallo {vorname},"}}}}
    zeile = {"status": "freigegeben", "fassung": 3, "format": "bloecke", "bloecke": dok, "mandant": "vibemind",
             "felder": {"betreff": "Herbst", "vorschautext": "Neu"},
             "pflichtteil": {"impressum": "I", "abmelde_hinweis": "Abmelden: {abmeldelink}"}}
    db.antworten = [[zeile]]
    r = c.get(f"/api/pult/inhalte/{IID}/versand", headers=H)
    assert r.status_code == 200
    j = r.json()
    assert j["betreff"] == "Herbst" and j["fassung"] == 3 and j["mandant"] == "vibemind"
    assert "{vorname}" in j["html"] and "{abmeldelink}" in j["html"] and "{vorname}" in j["text"]
    assert j["medien"] == []
    assert "status = 'freigegeben'" in db.sql[0] or "freigegeben" in db.sql[0]


def test_versand_nicht_freigegeben_409(db, c):
    db.antworten = [[{"status": "eingereicht", "fassung": 3, "format": "bloecke", "bloecke": {}, "mandant": "v",
                      "felder": {}, "pflichtteil": {}}]]
    assert c.get(f"/api/pult/inhalte/{IID}/versand", headers=H).status_code == 409


def test_versand_ohne_schluessel_401(db, c):
    assert c.get(f"/api/pult/inhalte/{IID}/versand").status_code == 401


# --- Editor, Entwuerfe, Bilder (Plan 2026-10-19) Task 2: Inhalt loeschen, Verweise je Firma ---

def test_loeschen_ruft_db_funktion_mit_mandant(db, c):
    db.antworten = [[{"id": "vibemind"}], [{"titel": "T"}]]
    r = c.post(f"/api/pult/inhalte/{IID}/loeschen", headers=H, json={"mandant": "vibemind", "von": "felix"})
    assert r.status_code == 200 and r.json() == {"geloescht": IID, "titel": "T"}
    assert "marketing.mandanten" in db.sql[0] and "pult_inhalt_loeschen" not in db.sql[0]
    assert "pult_inhalt_loeschen(" in db.sql[1] and "'vibemind'" in db.sql[1] and "'felix'" in db.sql[1]


@pytest.mark.parametrize("body", [{}, {"mandant": ""}, {"mandant": None}, {"mandant": 5},
                                  {"mandant": "Vibe Mind"}, {"mandant": "x';drop"}])
def test_loeschen_ohne_mandant_422_ohne_sql(db, c, body):
    r = c.post(f"/api/pult/inhalte/{IID}/loeschen", headers=H, json=body)
    assert r.status_code == 422 and db.sql == []


def test_loeschen_db_ablehnung_422(db, c):
    db.antworten = [[{"id": "vibemind"}]]
    db.fehler = [None, RuntimeError("psql:<stdin>:1: ERROR:  Liegt zur Freigabe – erst zurückziehen\nCONTEXT: x")]
    r = c.post(f"/api/pult/inhalte/{IID}/loeschen", headers=H, json={"mandant": "vibemind", "von": "f"})
    assert r.status_code == 422 and r.json()["detail"] == "Liegt zur Freigabe – erst zurückziehen"


def test_loeschen_schluessel_id_und_verbindung(db, c):
    assert c.post(f"/api/pult/inhalte/{IID}/loeschen", json={"mandant": "vibemind"}).status_code == 401
    assert c.post("/api/pult/inhalte/kein-uuid/loeschen", headers=H,
                  json={"mandant": "vibemind"}).status_code == 404
    assert db.sql == []
    db.fehler = [RuntimeError("ssh: connect to host offload-vm")]
    r = c.post(f"/api/pult/inhalte/{IID}/loeschen", headers=H, json={"mandant": "vibemind"})
    assert r.status_code == 503 and "offload" not in r.text


def _verweis(iid, mandant, titel, fmt="bloecke", fassung=4, status="entwurf", wo="aktuell"):
    return {"id": iid, "titel": titel, "status": status, "art": "newsletter", "wo": wo,
            "mandant": mandant, "format": fmt, "fassung": fassung}


def test_verweise_maskiert_fremde_titel(db, c):
    db.antworten = [[{"id": "vibemind"}],
                    [_verweis("a1", "fin2gether", "Geheim"), _verweis("b2", "vibemind", "Herbst", "felder", 2)]]
    r = c.get("/api/pult/medien/verweise", params={"name": "a.jpg", "mandant": "vibemind"}, headers=H)
    assert r.status_code == 200
    fremd, eigen = r.json()["verweise"]
    assert fremd["titel"] == "" and fremd["eigen"] is False and "Geheim" not in r.text
    assert eigen == {"id": "b2", "titel": "Herbst", "status": "entwurf", "art": "newsletter", "wo": "aktuell",
                     "format": "felder", "eigen": True, "fassung": 2}
    assert "marketing.mandanten" in db.sql[0] and "i.mandant" in db.sql[1] and "format" in db.sql[1]


def test_verweise_ohne_mandant_wie_bisher(db, c):
    db.antworten = [[_verweis("a1", "fin2gether", "Geheim")]]
    r = c.get("/api/pult/medien/verweise", params={"name": "a.jpg"}, headers=H)
    assert r.status_code == 200
    v = r.json()["verweise"][0]
    assert v["titel"] == "Geheim" and v["eigen"] is True and v["format"] == "bloecke" and v["fassung"] == 4


def test_verweise_ungueltiger_mandant_422_ohne_sql(db, c):
    r = c.get("/api/pult/medien/verweise", params={"name": "a.jpg", "mandant": "X Y"}, headers=H)
    assert r.status_code == 422 and db.sql == []

# --- Editor, Entwuerfe, Bilder (Plan 2026-10-19) Task 4: Vorlage anwenden, Fassung wiederherstellen ---

VORLAGE_DOK = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["t"], "canvasColor": "#ffffff"}},
               "t": {"type": "Text", "data": {"style": {}, "props": {"text": "Aus der Vorlage"}}}}


def _kopf(status="entwurf", mandant="fin2gether", art="newsletter"):
    return {"mandant": mandant, "status": status, "art": art, "fassung": 7,
            "felder": {"betreff": "Alter Betreff", "vorschautext": "Alter Vorschautext"}}


def test_vorlage_anwenden_nutzt_mandant_des_inhalts(db, c, monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    db.antworten = [[_kopf()], [{"bloecke": VORLAGE_DOK}], [{"laden": "fin2gether", "layout": None, "gestalt": None}],
                    [{"fassung": 8}]]
    r = c.post(f"/api/pult/inhalte/{IID}/vorlage", headers=H,
               json={"vorlage": "studio", "basis_fassung": 7, "von": "felix", "mandant": "vibemind"})
    assert r.status_code == 200, r.text
    assert r.json() == {"fassung": 8}
    assert "newsletter_vorlagen" in db.sql[1] and "'fin2gether'" in db.sql[1] and "'vibemind'" not in db.sql[1]
    assert "status = 'freigegeben'" in db.sql[1] and "fuer_alle" in db.sql[1]
    s = db.sql[3]
    assert "pult_bloecke_speichern(" in s and f"'{IID}'::uuid, 7, 'Alter Betreff', 'Alter Vorschautext'" in s
    assert "Aus der Vorlage" in s and "'betreiber', false" in s


def test_vorlage_anwenden_nicht_im_entwurf_422_ohne_speichern(db, c):
    db.antworten = [[_kopf(status="eingereicht")]]
    r = c.post(f"/api/pult/inhalte/{IID}/vorlage", headers=H, json={"vorlage": "studio", "basis_fassung": 7})
    assert r.status_code == 422 and r.json()["detail"] == "Vorlagen lassen sich nur auf Newsletter-Entwürfe anwenden"
    assert len(db.sql) == 1 and not any("pult_bloecke_speichern" in s for s in db.sql)


def test_vorlage_anwenden_nur_newsletter(db, c):
    db.antworten = [[_kopf(art="post")]]
    r = c.post(f"/api/pult/inhalte/{IID}/vorlage", headers=H, json={"vorlage": "studio", "basis_fassung": 7})
    assert r.status_code == 422 and len(db.sql) == 1


def test_vorlage_anwenden_unbekannter_inhalt_404(db, c):
    db.antworten = [[]]
    r = c.post(f"/api/pult/inhalte/{IID}/vorlage", headers=H, json={"vorlage": "studio", "basis_fassung": 7})
    assert r.status_code == 404


def test_vorlage_anwenden_fremde_oder_unfreigegebene_vorlage_422(db, c):
    db.antworten = [[_kopf()], []]
    r = c.post(f"/api/pult/inhalte/{IID}/vorlage", headers=H, json={"vorlage": "studio", "basis_fassung": 7})
    assert r.status_code == 422 and "studio" in r.json()["detail"]
    assert not any("pult_bloecke_speichern" in s for s in db.sql)


def test_vorlage_anwenden_veraltete_basis_reicht_db_grund_durch(db, c, monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    db.antworten = [[_kopf()], [{"bloecke": VORLAGE_DOK}], [{"laden": "x", "layout": None, "gestalt": None}]]
    db.fehler = [None, None, None, RuntimeError("ERROR:  Inzwischen gibt es Fassung 8 – bitte neu laden")]
    r = c.post(f"/api/pult/inhalte/{IID}/vorlage", headers=H, json={"vorlage": "studio", "basis_fassung": 7})
    assert r.status_code == 422 and r.json()["detail"] == "Inzwischen gibt es Fassung 8 – bitte neu laden"


def test_wiederherstellen_speichert_alte_bloecke(db, c):
    alt = {"root": {"type": "EmailLayout", "data": {"childrenIds": ["t"]}},
           "t": {"type": "Text", "data": {"style": {}, "props": {"text": "Stand drei"}}}}
    db.antworten = [[{"format": "bloecke", "bloecke": alt, "felder": {"betreff": "Betreff drei",
                                                                      "vorschautext": "Vorschau drei"}}],
                    [{"fassung": 9}]]
    r = c.post(f"/api/pult/inhalte/{IID}/wiederherstellen", headers=H,
               json={"fassung": 3, "basis_fassung": 8, "von": "felix"})
    assert r.status_code == 200 and r.json() == {"fassung": 9, "platzhalter": []}
    assert "fassung = 3" in db.sql[0]
    s = db.sql[1]
    assert "pult_bloecke_speichern(" in s and f"'{IID}'::uuid, 8, 'Betreff drei', 'Vorschau drei'" in s
    assert "Stand drei" in s and "'betreiber', false" in s


def _alt_mit_bildern(bild="weg.jpg", hintergrund="weg.jpg", text=""):
    return {"root": {"type": "EmailLayout", "data": {"childrenIds": ["b", "d", "k", "p", "t"]}},
            "b": {"type": "Image", "data": {"style": {}, "props": {"url": f"medien:{bild}", "width": 600,
                                                                     "height": 300}}},
            "d": {"type": "Image", "data": {"style": {}, "props": {"url": "medien:da.jpg"}}},
            "k": {"type": "Container", "data": {"style": {}, "props": {"url": f"medien:{hintergrund}"}}},
            "p": {"type": "Image", "data": {"style": {}, "props": {"url": "medien:platzhalter-4x3.png"}}},
            "t": {"type": "Text", "data": {"style": {}, "props": {"text": text or "Stand drei"}}}}


def _gespeichert(sql: str) -> dict:
    """Bloecke-JSON aus dem pult_bloecke_speichern-Aufruf."""
    roh = sql.split("'betreiber', false")[0].rsplit("::jsonb", 1)[0]
    roh = roh[roh.index("'{") + 1:roh.rindex("}'") + 1].replace("''", "'")
    return json.loads(roh)


def test_wiederherstellen_geloeschte_bilder_werden_platzhalter(db, c, monkeypatch, tmp_path):
    """Final-Review I2: alte Fassung zeigt auf ein geloeschtes Bild -> Platzhalter, Name in der Antwort."""
    mensch, erzeugt = tmp_path / "mensch", tmp_path / "erzeugt"
    mensch.mkdir(); erzeugt.mkdir()
    (erzeugt / "da.jpg").write_bytes(b"x")
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(mensch))
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(erzeugt))
    db.antworten = [[{"format": "bloecke", "bloecke": _alt_mit_bildern(hintergrund="alt-hg.png"),
                      "felder": {"betreff": "B", "vorschautext": "V"}}], [{"fassung": 9}]]
    r = c.post(f"/api/pult/inhalte/{IID}/wiederherstellen", headers=H, json={"fassung": 3, "basis_fassung": 8})
    assert r.status_code == 200, r.text
    assert r.json() == {"fassung": 9, "platzhalter": ["alt-hg.png", "weg.jpg"]}
    s = db.sql[1]
    assert "medien:weg.jpg" not in s and "medien:alt-hg.png" not in s
    neu = _gespeichert(s)
    assert neu["b"]["data"]["props"]["url"] == "medien:platzhalter-2x1.png"
    assert "url" not in neu["k"]["data"]["props"]
    assert neu["d"]["data"]["props"]["url"] == "medien:da.jpg"
    assert neu["p"]["data"]["props"]["url"] == "medien:platzhalter-4x3.png"


def test_wiederherstellen_vorhandene_bilder_bleiben(db, c, monkeypatch, tmp_path):
    for n in ("weg.jpg", "da.jpg"):
        (tmp_path / n).write_bytes(b"x")
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(tmp_path))
    monkeypatch.delenv("MARKETING_BILD_ORDNER", raising=False)
    db.antworten = [[{"format": "bloecke", "bloecke": _alt_mit_bildern(), "felder": {}}], [{"fassung": 9}]]
    r = c.post(f"/api/pult/inhalte/{IID}/wiederherstellen", headers=H, json={"fassung": 3, "basis_fassung": 8})
    assert r.status_code == 200 and r.json() == {"fassung": 9, "platzhalter": []}
    assert _gespeichert(db.sql[1]) == _alt_mit_bildern()


def test_wiederherstellen_name_exakt_wie_vergessen(db, c, monkeypatch, tmp_path):
    """Wie _datei_existiert: exakter Name im Ordner, keine Gross/Klein-Faltung."""
    (tmp_path / "WEG.jpg").write_bytes(b"x")
    (tmp_path / "da.jpg").write_bytes(b"x")
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(tmp_path))
    monkeypatch.delenv("MARKETING_BILD_ORDNER", raising=False)
    db.antworten = [[{"format": "bloecke", "bloecke": _alt_mit_bildern(), "felder": {}}], [{"fassung": 9}]]
    r = c.post(f"/api/pult/inhalte/{IID}/wiederherstellen", headers=H, json={"fassung": 3, "basis_fassung": 8})
    assert r.status_code == 200 and r.json()["platzhalter"] == ["weg.jpg"]


def test_wiederherstellen_fehlendes_bild_ausserhalb_bildplatz_422(db, c, monkeypatch, tmp_path):
    (tmp_path / "da.jpg").write_bytes(b"x")
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(tmp_path))
    monkeypatch.delenv("MARKETING_BILD_ORDNER", raising=False)
    alt = _alt_mit_bildern()
    alt["t"]["data"]["props"]["url"] = "medien:weg.jpg"
    alt["t"]["type"] = "Button"
    db.antworten = [[{"format": "bloecke", "bloecke": alt, "felder": {}}]]
    r = c.post(f"/api/pult/inhalte/{IID}/wiederherstellen", headers=H, json={"fassung": 3, "basis_fassung": 8})
    assert r.status_code == 422 and "weg.jpg" in r.json()["detail"]
    assert len(db.sql) == 1 and not any("pult_bloecke_speichern" in s for s in db.sql)


def test_wiederherstellen_ohne_medienordner_503_ohne_speichern(db, c, monkeypatch):
    monkeypatch.delenv("MARKETING_MEDIEN_ORDNER", raising=False)
    monkeypatch.delenv("MARKETING_BILD_ORDNER", raising=False)
    db.antworten = [[{"format": "bloecke", "bloecke": _alt_mit_bildern(), "felder": {}}]]
    r = c.post(f"/api/pult/inhalte/{IID}/wiederherstellen", headers=H, json={"fassung": 3, "basis_fassung": 8})
    assert r.status_code == 503
    assert not any("pult_bloecke_speichern" in s for s in db.sql)


def test_wiederherstellen_feldfassung_422(db, c):
    db.antworten = [[{"format": "felder", "bloecke": None, "felder": {"betreff": "x"}}]]
    r = c.post(f"/api/pult/inhalte/{IID}/wiederherstellen", headers=H, json={"fassung": 1, "basis_fassung": 8})
    assert r.status_code == 422 and r.json()["detail"] == "Nur Editor-Fassungen lassen sich wiederherstellen"
    assert len(db.sql) == 1


def test_wiederherstellen_unbekannte_fassung_404(db, c):
    db.antworten = [[]]
    r = c.post(f"/api/pult/inhalte/{IID}/wiederherstellen", headers=H, json={"fassung": 99, "basis_fassung": 8})
    assert r.status_code == 404


@pytest.mark.parametrize("pfad,body", [
    ("vorlage", {"vorlage": "studio", "basis_fassung": "7"}),
    ("vorlage", {"vorlage": "studio", "basis_fassung": True}),
    ("vorlage", {"vorlage": "studio", "basis_fassung": -1}),
    ("vorlage", {"vorlage": "studio"}),
    ("vorlage", {"vorlage": "Nicht gut", "basis_fassung": 7}),
    ("wiederherstellen", {"fassung": 3, "basis_fassung": 1.5}),
    ("wiederherstellen", {"fassung": 3}),
    ("wiederherstellen", {"fassung": "3", "basis_fassung": 8}),
    ("wiederherstellen", {"fassung": 0, "basis_fassung": 8}),
])
def test_basis_fassung_kein_int_422_ohne_sql(db, c, pfad, body):
    r = c.post(f"/api/pult/inhalte/{IID}/{pfad}", headers=H, json=body)
    assert r.status_code == 422 and db.sql == []


@pytest.mark.parametrize("pfad", ["vorlage", "wiederherstellen"])
def test_vorlage_und_wiederherstellen_schluessel_und_id(db, c, pfad):
    body = {"vorlage": "studio", "fassung": 3, "basis_fassung": 7}
    assert c.post(f"/api/pult/inhalte/{IID}/{pfad}", json=body).status_code == 401
    assert c.post(f"/api/pult/inhalte/kein-uuid/{pfad}", headers=H, json=body).status_code == 404
    assert db.sql == []


# --- Fix-Runde 1 (Review M1): gut geformter, aber unbekannter Mandant -> 422 vor der schreibenden Funktion ---

def test_loeschen_unbekannter_mandant_422_ohne_loeschen(db, c):
    db.antworten = [[]]
    r = c.post(f"/api/pult/inhalte/{IID}/loeschen", headers=H, json={"mandant": "foo", "von": "f"})
    assert r.status_code == 422 and r.json()["detail"] == "Unbekannter Mandant"
    assert len(db.sql) == 1 and "pult_inhalt_loeschen" not in db.sql[0]


def test_verweise_unbekannter_mandant_422(db, c):
    db.antworten = [[]]
    r = c.get("/api/pult/medien/verweise", params={"name": "a.jpg", "mandant": "foo"}, headers=H)
    assert r.status_code == 422 and len(db.sql) == 1 and "inhalt_fassungen" not in db.sql[0]

# --- Freie Flaeche Stufe 1 (Plan 2026-10-20) Task 4: Collage-Newsletter (format 'seite') ---

from spaces.marketing.claw import seite as _seite  # noqa: E402

SEITE = _seite.leer(None)
SEITE_KOPF = {"id": IID, "mandant": "vibemind", "art": "newsletter", "titel": "T", "status": "entwurf",
              "alter_weg": None}


def _jsonb_aus(sql: str) -> dict:
    treffer = re.search(r"E?'(\{.*\})'::jsonb", sql)
    assert treffer, sql
    return json.loads(treffer.group(1).replace("''", "'"))


def _seite_zeile(**extra):
    z = {"felder": {"betreff": "Herbst", "vorschautext": ""}, "format": "seite", "bloecke": None, "seite": SEITE,
         "gestalt": None, "pflichtteil": {"impressum": "I", "abmelde_hinweis": ""}, "gestalt_fehler": None}
    z.update(extra)
    return z


def test_collage_anlegen_ruft_db_funktion(db, c):
    gestalt = {"akzent": "#112233", "schriften": {"anzeige": "dm-sans", "text": "dm-sans"}}
    db.antworten = [[{"laden": "Radhaus", "layout": None, "gestalt": gestalt}], [{"id": IID}]]
    r = c.post("/api/pult/inhalte/collage", headers=H,
               json={"mandant": "radhaus", "titel": " Herbst ", "betreff": "Neu im Laden"})
    assert r.status_code == 200, r.text
    assert r.json() == {"inhalt": IID, "fassung": 1}
    sql = db.sql[1]
    assert "marketing.pult_collage_anlegen(" in sql and "'radhaus'" in sql and "'Herbst'" in sql
    assert "'Neu im Laden'" in sql
    dok = _jsonb_aus(sql)
    assert _seite.pruefen(dok) == dok
    assert dok["ebenen"][2]["farbe"] == "#112233"          # Knopf in der Markenfarbe


def test_collage_anlegen_ohne_marke_und_eingaben(db, c):
    db.antworten = [[], [{"id": IID}]]                     # Laden ohne Layout/Marke -> leer(None)
    r = c.post("/api/pult/inhalte/collage", headers=H, json={"mandant": "radhaus", "titel": "T"})
    assert r.status_code == 200 and _jsonb_aus(db.sql[1]) == _seite.pruefen(_seite.leer(None))
    db.sql.clear()
    for body in ({"titel": "T"}, {"mandant": "radhaus"}, {"mandant": "radhaus", "titel": "x" * 201},
                 {"mandant": "radhaus", "titel": "T", "betreff": 5}):
        assert c.post("/api/pult/inhalte/collage", headers=H, json=body).status_code == 422, body
    assert db.sql == []
    assert c.post("/api/pult/inhalte/collage", json={"mandant": "radhaus", "titel": "T"}).status_code == 401


def test_seite_speichern_prueft_vorher(db, c):
    schlecht = dict(SEITE, breite=599)
    r = c.post(f"/api/pult/inhalte/{IID}/seite", headers=H,
               json={"basis_fassung": 1, "seite": schlecht, "betreff": "B", "vorschautext": ""})
    assert r.status_code == 422 and r.json()["detail"] == "Seite breite: muss 600 sein"
    assert db.sql == []
    # Anrede nur in Fliesstext: prueft nur seite.py, nicht die DB
    anrede = json.loads(json.dumps(SEITE))
    anrede["ebenen"][0]["text"] = "Hallo {vorname}"
    r = c.post(f"/api/pult/inhalte/{IID}/seite", headers=H,
               json={"basis_fassung": 1, "seite": anrede, "betreff": "B", "vorschautext": ""})
    assert r.status_code == 422 and db.sql == []
    db.antworten = [[{"fassung": 2}]]
    gut = json.loads(json.dumps(SEITE))
    gut["ebenen"][1]["x"] = 310.0
    r = c.post(f"/api/pult/inhalte/{IID}/seite", headers=H,
               json={"basis_fassung": 1, "seite": gut, "betreff": "B", "vorschautext": "V",
                     "urheber": "agent", "als_kopie": True})
    assert r.status_code == 200 and r.json() == {"fassung": 2}
    sql = db.sql[0]
    assert "marketing.pult_seite_speichern(" in sql and f"'{IID}'::uuid, 1, 'B', 'V'" in sql
    assert "'agent', true)" in sql
    assert _jsonb_aus(sql) == _seite.pruefen(gut)          # das normalisierte Dokument


def test_seite_speichern_formen_und_db_ablehnung(db, c, monkeypatch):
    for body in ({"seite": SEITE}, {"basis_fassung": -1, "seite": SEITE}, {"basis_fassung": 1},
                 {"basis_fassung": 1, "seite": SEITE, "urheber": "x"},
                 {"basis_fassung": 1, "seite": SEITE, "als_kopie": "ja"},
                 {"basis_fassung": 1, "seite": SEITE, "betreff": 3}):
        assert c.post(f"/api/pult/inhalte/{IID}/seite", headers=H, json=body).status_code == 422, body
    assert db.sql == []

    def wirft(*a, **k):
        raise RuntimeError("ERROR:  Inzwischen gibt es Fassung 7 - neu laden oder als Kopie behalten")
    monkeypatch.setattr(_db, "query_one", wirft)
    r = c.post(f"/api/pult/inhalte/{IID}/seite", headers=H, json={"basis_fassung": 3, "seite": SEITE})
    assert r.status_code == 422 and r.json()["detail"].startswith("Inzwischen gibt es Fassung 7")


def test_inhalt_liefert_seite_der_neuesten_fassung(db, c):
    db.antworten = [[SEITE_KOPF],
                    [{"fassung": 2, "felder": {}, "layout": None, "urheber": "betreiber", "erstellt_am": "x",
                      "format": "seite", "bloecke": None, "seite": SEITE},
                     {"fassung": 1, "felder": {}, "layout": None, "urheber": "betreiber", "erstellt_am": "x",
                      "format": "seite", "bloecke": None, "seite": SEITE}]]
    f = c.get(f"/api/pult/inhalte/{IID}", headers=H).json()["fassungen"]
    assert f[0]["seite"] == SEITE and f[1]["seite"] is None
    assert "THEN seite END AS seite" in db.sql[1]


def test_vorschau_waehlt_format_je_fassung(db, c):
    db.antworten = [[_bloecke_zeile()]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=mail", headers=H)
    assert r.status_code == 200 and "Hallo Pult" in r.text and "position:relative;width:600px" not in r.text
    assert "f.seite" in db.sql[-1]
    # Stufe 4: mail und handy einer Collage zeigen die Mail-Fassung (test_vorschau_mail_collage), pdf weiter die
    # Seite selbst (test_pdf_hoehe_folgt_der_seite)


def test_pdf_hoehe_folgt_der_seite(db, c, monkeypatch):
    from spaces.marketing.claw import pdf_druck
    gesehen = {}

    def druck(html, erlaubt):
        gesehen["html"], gesehen["erlaubt"] = html, erlaubt
        return b"%PDF-1.7 seite"
    monkeypatch.setattr(pdf_druck, "html_zu_pdf", druck)
    monkeypatch.setenv("MARKETING_PDF_HOSTS", "h.ts.net")
    db.antworten = [[_seite_zeile(seite=dict(SEITE, hoehe=6000))]]
    basis = "https://h.ts.net/marketing/bild/1760000000." + "a" * 64 + "/"
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=2&format=pdf&bild_basis={basis}", headers=H)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content == b"%PDF-1.7 seite"
    assert "position:relative;width:600px;height:6000px" in gesehen["html"]
    assert gesehen["erlaubt"] == [basis, "https://h.ts.net/marketing/schrift/"]


def test_seite_pdf_ohne_chromium_503(db, c, monkeypatch):
    from spaces.marketing.claw import pdf_druck

    def druck(html, erlaubt):
        raise pdf_druck.PdfDruckFehler("PDF-Druck ist hier nicht eingerichtet")
    monkeypatch.setattr(pdf_druck, "html_zu_pdf", druck)
    db.antworten = [[_seite_zeile()]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=2&format=pdf", headers=H)
    assert r.status_code == 503 and "nicht eingerichtet" in r.json()["detail"]


def test_einreichen_bloecke_unveraendert_eine_abfrage(db, c):
    """Block-Newsletter: Formatsperre und pult_einreichen weiter in EINER Abfrage (Collagen: test_freigabe_api)."""
    db.antworten = [[{"fassung": 4, "gesperrt": False}]]
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", headers=H, json={"von": "Anna"})
    assert r.status_code == 200 and r.json() == {"status": "eingereicht", "fassung": 4} and len(db.sql) == 1
    assert re.search(r"CASE WHEN \(SELECT f\.format .*\) = 'seite' THEN NULL ELSE marketing\.pult_einreichen\(",
                     db.sql[0])
    assert not hasattr(pult, "COLLAGE_EINREICHEN")


@pytest.mark.parametrize("pfad,kopf", [
    (f"/api/pult/inhalte/{IID}/chat", {"X-Pult-Key": KEY}),
    (f"/api/agent/inhalte/{IID}/chat", {"X-Agent-Key": "agent-k"}),
])
def test_chat_auf_collage_erlaubt(db, c, monkeypatch, pfad, kopf):
    """Stufe 3: der Chat sperrt Collagen nicht mehr - keine Formatsperre, pult_chat_senden laeuft direkt."""
    monkeypatch.setenv("MARKETING_AGENT_KEY", "agent-k")
    monkeypatch.setattr(server, "API_KEY", "")
    db.antworten = [[{"s": {"id": "22222222-2222-2222-2222-222222222222", "status": "offen"}}]]
    r = c.post(pfad, headers=kopf, json={"nachricht": "Mach es wärmer"})
    assert r.status_code == 200, r.text
    assert r.json() == {"auftrag": "22222222-2222-2222-2222-222222222222", "status": "offen"}
    assert len(db.sql) == 1 and db.sql[0].startswith("SELECT marketing.pult_chat_senden(")
    assert "'seite'" not in db.sql[0] and "gesperrt" not in db.sql[0]


BASIS = "https://h.ts.net/marketing/bild/1760000000." + "a" * 64 + "/"
PFLICHT_A = {"impressum": "Alte GmbH, Bonn", "abmelde_hinweis": "Abmelden: {abmeldelink}"}


def _mail(s=None):
    """Mail-Fassung wie seite_mail_dienst sie speichert (MJML mit Bild- und Fuss-Marke)."""
    from spaces.marketing.api import seite_mail_dienst as dienst
    from spaces.marketing.claw import seite_mail
    s = s or SEITE
    namen = [f"ms-11111111-2-{k:02d}.jpg" for k in range(1, len(seite_mail.stuecke(s)) + 1)]
    gemeinsam = dict(betreff="Herbst", vorschautext="Neu", pflichtteil={}, fuss_ersatz=dienst.FUSS_MARKE)
    return {"inhalt": IID, "fassung": 2, "text": "alt", "bytes": 10, "medien": seite_mail.versand_medien(namen),
            "mjml_versand": seite_mail.mjml(s, namen, bild_adresse=lambda n: "cid:" + n, fuer_versand=True,
                                            **gemeinsam),
            "html_vorschau_mjml": seite_mail.mjml(s, namen, bild_adresse=lambda n: dienst.BILD_MARKE + n,
                                                  fuer_versand=False, **gemeinsam)}


def _versand_kopf(**extra):
    z = {"status": "freigegeben", "fassung": 2, "format": "seite", "bloecke": None, "mandant": "radhaus",
         "felder": {"betreff": "Herbst", "vorschautext": "Neu"}, "pflichtteil": PFLICHT_A}
    z.update(extra)
    return z


def _ablage(monkeypatch, tmp_path, mail):
    """Die ms-Dateien der Mail-Fassung liegen in der Ablage (sonst gilt sie als unvollstaendig)."""
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    for m in mail["medien"]:
        (tmp_path / m["name"]).write_bytes(b"x")


def _dienst_zeile(mail, pflichtteil=None, s=None):
    return {"format": "seite", "seite": s or SEITE, "felder": {"betreff": "Herbst"},
            "pflichtteil": PFLICHT_A if pflichtteil is None else pflichtteil, "mail": mail}


def test_versand_collage_form_wie_bloecke(db, c, monkeypatch, tmp_path):
    mail = _mail()
    _ablage(monkeypatch, tmp_path, mail)
    db.antworten = [[_versand_kopf()], [_dienst_zeile(mail)]]
    r = c.get(f"/api/pult/inhalte/{IID}/versand", headers=H)
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) == {"inhalt", "mandant", "fassung", "betreff", "vorschautext", "html", "text", "medien"}
    assert {k: j[k] for k in ("inhalt", "mandant", "fassung", "betreff", "vorschautext", "medien")} == {
        "inhalt": IID, "mandant": "radhaus", "fassung": 2, "betreff": "Herbst", "vorschautext": "Neu",
        "medien": mail["medien"]}
    assert j["html"].lstrip().lower().startswith("<!doctype html") and "cid:ms-11111111-2-01.jpg" in j["html"]
    assert "Alte GmbH, Bonn" in j["html"] and "{abmeldelink}" in j["html"] and "Alte GmbH, Bonn" in j["text"]
    # die Mail-Fassung der FREIGEGEBENEN Fassung
    assert "marketing.pult_seite_mail_lesen(" in db.sql[1] and f"'{IID}'::uuid AND f.fassung = 2" in db.sql[1]


def test_versand_collage_fuss_folgt_dem_aktuellen_pflichtteil(db, c, monkeypatch, tmp_path):
    """Aendert die Firma ihren Pflichtteil nach dem Erzeugen, traegt der Versand den neuen Fuss."""
    mail = _mail()
    _ablage(monkeypatch, tmp_path, mail)
    neu = {"impressum": "Neue GmbH, Köln", "abmelde_hinweis": "Abmelden: {abmeldelink}"}
    db.antworten = [[_versand_kopf()], [_dienst_zeile(mail, pflichtteil=neu)]]
    j = c.get(f"/api/pult/inhalte/{IID}/versand", headers=H).json()
    assert "Neue GmbH, Köln" in j["html"] and "Alte GmbH" not in j["html"] and "Neue GmbH, Köln" in j["text"]


def test_versand_collage_erzeugt_bei_bedarf(db, c, monkeypatch):
    """Ohne gespeicherte Mail-Fassung wird sie erzeugt (reine Textseite: nur zum Messen gerendert, ohne Ablage
    und ohne Dateien)."""
    from spaces.marketing.claw import seite as _s, seite_bilder, seite_mail
    monkeypatch.setattr(seite_bilder, "render_und_messen", lambda s, lesen, **k: (b"", {}))
    nur_text = _s.pruefen(dict(SEITE, ebenen=[e for e in SEITE["ebenen"] if e["id"] in ("text", "knopf")]))
    assert seite_mail.stuecke(nur_text) == []
    db.antworten = [[_versand_kopf()], [_dienst_zeile(None, s=nur_text)], [{"mail": _mail(nur_text)}]]
    r = c.get(f"/api/pult/inhalte/{IID}/versand", headers=H)
    assert r.status_code == 200, r.text
    assert "Hier steht Ihr Text." in r.json()["html"] and r.json()["medien"] == []
    assert "marketing.pult_seite_mail_speichern(" in db.sql[2]


def test_versand_collage_nicht_freigegeben_409(db, c):
    db.antworten = [[_versand_kopf(status="eingereicht")]]
    assert c.get(f"/api/pult/inhalte/{IID}/versand", headers=H).status_code == 409 and len(db.sql) == 1


def test_vorschau_mail_collage(db, c, monkeypatch, tmp_path):
    mail = _mail()
    _ablage(monkeypatch, tmp_path, mail)
    for fmt in ("mail", "handy", ""):          # ohne Format: die Mail (wie bisher die Vorgabe)
        db.antworten = [[_seite_zeile()], [_dienst_zeile(mail)]]
        r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=2&format={fmt}&bild_basis={BASIS}", headers=H)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html"), r.text
        assert BASIS + "ms-11111111-2-01.jpg" in r.text and "bildbasis.vibemind.invalid" not in r.text
        assert "position:relative;width:600px" not in r.text      # nicht die Seite selbst
        assert "<title>Herbst</title>" in r.text and "Alte GmbH, Bonn" in r.text and "[Abmeldelink]" in r.text
        assert "f.fassung = 2" in db.sql[-1] and "pult_seite_mail_lesen" in db.sql[-1]


def test_vorschau_format_seite_billig(db, c, monkeypatch):
    """format=seite: die Seite selbst als HTML, ohne Mail-Fassung (keine zweite Abfrage, kein Chromium)."""
    from spaces.marketing.claw import seite_bilder

    def nie(*a, **k):
        raise AssertionError("kein Chromium fuer format=seite")
    monkeypatch.setattr(seite_bilder, "render_und_messen", nie)
    db.antworten = [[_seite_zeile()]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=2&format=seite&bild_basis={BASIS}", headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "position:relative;width:600px" in r.text and "Überschrift" in r.text and "<title>Herbst</title>" in r.text
    assert len(db.sql) == 1 and "pult_seite_mail_lesen" not in db.sql[0]
    # fuer Block-Newsletter gibt es format=seite nicht
    db.antworten = [[_bloecke_zeile()]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=seite", headers=H)
    assert r.status_code == 422 and r.json()["detail"] == pult.COLLAGE_IST_NICHT


def test_vorschau_mail_collage_fehler_422(db, c):
    anrede = dict(SEITE, ebenen=[
        {"id": "b", "art": "bild", "x": 300, "y": 300, "breite": 400, "hoehe": 300, "quelle": "medien:a.png"},
        {"id": "t", "art": "text", "x": 300, "y": 300, "breite": 300, "hoehe": 60, "text": "Hallo {vorname}",
         "rolle": "TEXT", "groesse": 17, "farbe": "#333333"}])
    db.antworten = [[_seite_zeile(seite=anrede)], [_dienst_zeile(None, s=anrede)]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=2&format=mail", headers=H)
    assert r.status_code == 422
    assert r.json()["detail"] == "Die Anrede steht in einem Bildbereich – sie muss in frei stehendem Fließtext stehen"
    assert len(db.sql) == 2


def test_verweise_schuetzen_bildstuecke_der_mail_fassung(db, c):
    """Ein ms-Stueck der neuesten, eingereichten oder freigegebenen Collage-Fassung sperrt das Loeschen."""
    db.antworten = [[{"id": IID, "titel": "Herbst", "status": "freigegeben", "art": "newsletter",
                      "wo": "freigegeben", "mandant": "vibemind", "format": "seite", "fassung": 2}]]
    r = c.get("/api/pult/medien/verweise?name=ms-11111111-2-01.jpg", headers=H)
    assert r.status_code == 200 and r.json()["verweise"][0]["wo"] == "freigegeben"
    sql = db.sql[0]
    assert "JOIN marketing.seite_mailfassungen m ON m.inhalt = i.id" in sql
    assert ("m.medien @> jsonb_build_array(jsonb_build_object('name', 'ms-11111111-2-01.jpg'::text))" in sql)
    for teil in ("m.fassung = i.eingereichte_fassung", "m.fassung = i.freigegebene_fassung",
                 "SELECT max(f.fassung) FROM marketing.inhalt_fassungen f"):
        assert teil in sql


# --- Gesamtpruefung Stufe 1: Befunde ---

def test_collage_anlegen_ohne_betreff_schickt_leer_die_db_nimmt_den_titel(db, c):
    # Die API reicht "" durch; pult_collage_anlegen (077) setzt dann den Titel als Betreff (verify_077 Teil 2)
    db.antworten = [[], [{"id": IID}]]
    r = c.post("/api/pult/inhalte/collage", headers=H, json={"mandant": "radhaus", "titel": " Herbst "})
    assert r.status_code == 200, r.text
    assert "marketing.pult_collage_anlegen('radhaus', 'Herbst', '', " in db.sql[1]


def test_collage_startknopf_fuehrt_auf_die_webseite_der_marke(db, c):
    db.antworten = [[{"laden": "Radhaus", "layout": None, "gestalt": None, "webseite": "https://radhaus.de/neu"}],
                    [{"id": IID}]]
    r = c.post("/api/pult/inhalte/collage", headers=H, json={"mandant": "radhaus", "titel": "T"})
    assert r.status_code == 200, r.text
    assert "marken_spiegel" in db.sql[0] and "webseite" in db.sql[0]
    assert _jsonb_aus(db.sql[1])["ebenen"][2]["link"] == "https://radhaus.de/neu"
    for schlecht in ("http://radhaus.de", "https://", "https://a.de/\"x", None, 5):
        db.sql.clear()
        db.antworten = [[{"laden": "Radhaus", "layout": None, "gestalt": None, "webseite": schlecht}],
                        [{"id": IID}]]
        r = c.post("/api/pult/inhalte/collage", headers=H, json={"mandant": "radhaus", "titel": "T"})
        assert r.status_code == 200
        assert _jsonb_aus(db.sql[1])["ebenen"][2]["link"] == "https://example.com", schlecht


def test_verweise_suchen_auch_in_seiten(db, c):
    db.antworten = [[]]
    assert c.get("/api/pult/medien/verweise", params={"name": "a.jpg"}, headers=H).status_code == 200
    sql = db.sql[0]
    assert "strpos(coalesce(n.seite::text, ''), " in sql and "strpos(coalesce(f.seite::text, ''), " in sql
    assert "SELECT bloecke, felder, seite, format, fassung" in sql


def test_seite_speichern_nur_auf_collage(db, c):
    db.antworten = [[{"fassung": None, "gesperrt": True}]]
    r = c.post(f"/api/pult/inhalte/{IID}/seite", headers=H, json={"basis_fassung": 1, "seite": SEITE, "betreff": "B"})
    assert r.status_code == 409 and r.json()["detail"] == "Das ist kein Collage-Newsletter"
    assert len(db.sql) == 1
    # gesperrt nur, wenn es eine neueste Fassung gibt und sie nicht 'seite' ist - ohne Fassung laeuft der ELSE-Zweig
    assert re.search(r"CASE WHEN \(SELECT f\.format .*\) <> 'seite' THEN NULL ELSE marketing\.pult_seite_speichern\(",
                     db.sql[0])
    assert "coalesce((SELECT f.format" in db.sql[0] and "<> 'seite', false) AS gesperrt" in db.sql[0]


_BLOCK_ROUTEN = [
    (f"/api/pult/inhalte/{IID}/bloecke", {"basis_fassung": 3, "betreff": "B", "bloecke": DOK},
     "pult_bloecke_speichern"),
    (f"/api/pult/inhalte/{IID}/in_bloecke", {"von": "felix"}, "pult_in_bloecke_uebernehmen"),
    (f"/api/pult/inhalte/{IID}/fassungen", {"felder": FELDER, "layout": "dunkel"}, "pult_fassung_speichern"),
]


@pytest.mark.parametrize("pfad,body,funktion", _BLOCK_ROUTEN)
def test_block_routen_sperren_collage(db, c, pfad, body, funktion):
    db.antworten = [[{"fassung": None, "gesperrt": True}]]
    r = c.post(pfad, headers=H, json=body)
    assert r.status_code == 409 and r.json()["detail"] == "Das ist ein Collage-Newsletter"
    assert len(db.sql) == 1
    assert re.search(r"CASE WHEN \(SELECT f\.format .*\) = 'seite' THEN NULL ELSE marketing\." + funktion + r"\(",
                     db.sql[0])
    assert "= 'seite', false) AS gesperrt" in db.sql[0]


def test_vorlage_sperrt_collage_vor_dem_fuellen(db, c):
    db.antworten = [[{**_kopf(), "format": "seite"}]]
    r = c.post(f"/api/pult/inhalte/{IID}/vorlage", headers=H, json={"vorlage": "studio", "basis_fassung": 7})
    assert r.status_code == 409 and r.json()["detail"] == "Das ist ein Collage-Newsletter"
    assert len(db.sql) == 1 and "format" in db.sql[0]


def test_vorlage_sperrt_collage_auch_beim_schreiben(db, c, monkeypatch, tmp_path):
    # Wird die neueste Fassung zwischen Lesen und Schreiben eine Collage, sperrt die Schreib-Abfrage selbst
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    db.antworten = [[_kopf()], [{"bloecke": VORLAGE_DOK}], [{"laden": "x", "layout": None, "gestalt": None}],
                    [{"fassung": None, "gesperrt": True}]]
    r = c.post(f"/api/pult/inhalte/{IID}/vorlage", headers=H, json={"vorlage": "studio", "basis_fassung": 7})
    assert r.status_code == 409 and r.json()["detail"] == "Das ist ein Collage-Newsletter"
    assert re.search(r"CASE WHEN \(SELECT f\.format .*\) = 'seite' THEN NULL ELSE marketing\.pult_bloecke_speichern\(",
                     db.sql[3])


# --- Stufe 5: Collage-Vorlagen, Vorlagen-Freigabe, nur noch Collagen ---

NUR_COLLAGEN = "Neue Newsletter entstehen als Collage – bitte eine Collage-Vorlage wählen"


def _logo_daten_url() -> str:
    import base64
    import io
    from PIL import Image
    p = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 30, 30)).save(p, "PNG")
    return "data:image/png;base64," + base64.b64encode(p.getvalue()).decode()


SEITEN_VORLAGE = {
    "version": 1, "breite": 600, "hoehe": 900, "hintergrund": {"farbe": "#f4efe6", "bild": None},
    "schriften": {"anzeige": "playfair", "text": "dm-sans"},
    "ebenen": [
        {"id": "motiv", "art": "bild", "x": 300, "y": 250, "breite": 600, "hoehe": 400,
         "quelle": "medien:platzhalter-3x2.png"},
        {"id": "logo", "art": "bild", "x": 300, "y": 520, "breite": 160, "hoehe": 60, "quelle": "medien:logo-vm.png"},
        {"id": "name", "art": "text", "x": 300, "y": 620, "breite": 480, "hoehe": 60, "text": "VibeMind",
         "rolle": "ANZEIGE", "groesse": 32, "farbe": "#1a1a1a"},
        {"id": "text", "art": "text", "x": 300, "y": 720, "breite": 480, "hoehe": 80, "text": "Hallo {vorname}",
         "rolle": "TEXT", "groesse": 17, "farbe": "#1a1a1a"},
        {"id": "knopf", "art": "knopf", "x": 300, "y": 830, "breite": 220, "hoehe": 48, "text": "Mehr",
         "link": "https://vibemind.de", "farbe": "#5b3fd6", "textfarbe": "#ffffff"},
    ]}
SEITEN_ROLLEN = {"farben": {"hintergrund.farbe": "grund", "ebenen.knopf.farbe": "akzent",
                            "ebenen.knopf.textfarbe": "auf_akzent", "ebenen.name.farbe": "text",
                            "ebenen.text.farbe": "text"},
                 "ebenen": {"motiv": "motiv:Herbstlaub im Gegenlicht", "logo": "logo", "name": "name"}}
CID = "22222222-2222-2222-2222-222222222222"


def _seiten_vorlage_zeile(**extra):
    return {"format": "seite", "bloecke": None, "seite": SEITEN_VORLAGE, "rollen": SEITEN_ROLLEN,
            "nur_collagen": True, **extra}


def _laden_radhaus(logo=True):
    gestalt = {"akzent": "#c2410c", "schriften": {"anzeige": "dm-sans", "text": "dm-sans"}}
    if logo:
        gestalt["logo"] = _logo_daten_url()
    return {"laden": "Radhaus Jena", "layout": "radhaus-nl", "gestalt": gestalt,
            "webseite": "https://radhaus.de", "werte": {"grund": "#ffffff", "text": "#202020", "zweitfarbe": "#fde7d9"}}


def _seite_aus_sql(sql: str) -> dict:
    return json.loads(re.search(r"'(\{.*\})'::jsonb", sql, re.S).group(1).replace("''", "'"))


def test_aus_seiten_vorlage_fuellt_legt_an_und_beauftragt_motive(db, c, monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    db.antworten = [[_seiten_vorlage_zeile()], [_laden_radhaus()], [{"id": CID}],
                    [{"id": "33333333-3333-3333-3333-333333333333"}]]
    r = c.post("/api/pult/inhalte/aus_vorlage", headers=H,
               json={"vorlage": "herbst-split", "titel": "Oktober", "mandant": "radhaus"})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": CID, "warnungen": []}
    # 1. Vorlage mit Format und "nur noch Collagen" in einer Abfrage, 2. Laden mit Webseite und Profilwerten
    assert "format" in db.sql[0] and "marketing.pult_hat_collage_vorlage('radhaus')" in db.sql[0]
    assert "s.profil->'werte' AS werte" in db.sql[1]
    # 3. angelegt ueber pult_collage_aus_vorlage mit der gefuellten Seite (nie die Block-Kette)
    sql = db.sql[2]
    assert sql.startswith("SELECT marketing.pult_collage_aus_vorlage('herbst-split', 'Oktober', 'radhaus', ")
    assert sql.endswith("::jsonb, 'betreiber') AS id")
    assert "pult_inhalt_aus_vorlage" not in " ".join(db.sql)
    s = _seite_aus_sql(sql)
    nach_id = {e["id"]: e for e in s["ebenen"]}
    assert nach_id["name"]["text"] == "Radhaus Jena"
    assert nach_id["knopf"]["farbe"] == "#c2410c" and nach_id["knopf"]["link"] == "https://radhaus.de"
    assert s["hintergrund"]["farbe"] == "#ffffff" and nach_id["text"]["farbe"] == "#202020"
    assert s["schriften"] == {"anzeige": "dm-sans", "text": "dm-sans"}
    assert nach_id["logo"]["quelle"].startswith("medien:logo-radhaus-")              # Logo als Mediendatei
    assert (tmp_path / nach_id["logo"]["quelle"][len("medien:"):]).is_file()
    assert nach_id["motiv"]["quelle"].startswith("medien:platzhalter-")
    # 4. Bildauftrag fuer die Motiv-Ebene ueber die bestehende Bildkette (bilder._anlegen)
    assert db.sql[3].startswith(f"SELECT marketing.pult_bild_auftrag('{CID}'::uuid, 'motiv', false, "
                                "'Herbstlaub im Gegenlicht', 'system', 55, 'neu', ")
    assert len(db.sql) == 4


def test_aus_seiten_vorlage_ohne_logo_und_ohne_webseite_warnt(db, c, monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    laden = {**_laden_radhaus(logo=False), "webseite": None}
    db.antworten = [[_seiten_vorlage_zeile()], [laden], [{"id": CID}], [{"id": "a"}]]
    r = c.post("/api/pult/inhalte/aus_vorlage", headers=H,
               json={"vorlage": "herbst-split", "titel": "Oktober", "mandant": "radhaus"})
    assert r.status_code == 200, r.text
    assert r.json()["warnungen"] == ["Keine Webseite der Firma – Knopf-Link bitte setzen"]
    s = _seite_aus_sql(db.sql[2])
    assert "logo" not in [e["id"] for e in s["ebenen"]]            # die Logo-Ebene entfaellt
    assert [e["link"] for e in s["ebenen"] if e["art"] == "knopf"] == ["https://vibemind.de"]   # Link bleibt


def test_aus_seiten_vorlage_bildauftrag_scheitert_nur_warnung(db, c, monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    db.antworten = [[_seiten_vorlage_zeile()], [_laden_radhaus()], [{"id": CID}]]
    db.fehler = [None, None, None, RuntimeError("ERROR:  Bilder gibt es nur fuer Newsletter")]
    r = c.post("/api/pult/inhalte/aus_vorlage", headers=H,
               json={"vorlage": "herbst-split", "titel": "Oktober", "mandant": "radhaus"})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": CID, "warnungen": [
        "Bild für 'motiv' konnte nicht beauftragt werden: Bilder gibt es nur fuer Newsletter"]}


def test_aus_seiten_vorlage_db_lehnt_ab_422_ohne_bildauftrag(db, c, monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    db.antworten = [[_seiten_vorlage_zeile()], [_laden_radhaus()]]
    db.fehler = [None, None,
                 RuntimeError("ERROR:  Vorlage herbst-split gibt es nicht oder sie ist nicht freigegeben")]
    r = c.post("/api/pult/inhalte/aus_vorlage", headers=H,
               json={"vorlage": "herbst-split", "titel": "Oktober", "mandant": "radhaus"})
    assert r.status_code == 422 and "nicht freigegeben" in r.json()["detail"]
    assert len(db.sql) == 3 and not any("pult_bild_auftrag" in q for q in db.sql)


def test_aus_block_vorlage_bei_collage_vorlage_409(db, c):
    """Nur noch Collagen: gibt es eine freigegebene Collage-Vorlage fuer die Firma, keine neuen Block-Newsletter."""
    db.antworten = [[{"format": "bloecke", "bloecke": DOK, "seite": None, "rollen": None, "nur_collagen": True}]]
    r = c.post("/api/pult/inhalte/aus_vorlage", headers=H, json={"vorlage": "leer", "titel": "Oktober"})
    assert r.status_code == 409 and r.json()["detail"] == NUR_COLLAGEN
    assert len(db.sql) == 1                                     # nichts gefuellt, nichts geschrieben


def test_block_anlegen_ohne_collage_vorlage_erlaubt(db, c):
    """Review Focus 5: Block-Vorlagen, aber keine freigegebene Collage-Vorlage - Anlegen aus Block-Vorlage geht."""
    db.antworten = [[{"format": "bloecke", "bloecke": DOK, "seite": None, "rollen": None, "nur_collagen": False}],
                    [{"laden": "L", "layout": None, "gestalt": None}], [{"id": IID}]]
    r = c.post("/api/pult/inhalte/aus_vorlage", headers=H, json={"vorlage": "leer", "titel": "Oktober"})
    assert r.status_code == 200 and r.json() == {"id": IID}
    assert "marketing.pult_inhalt_aus_vorlage(" in db.sql[-1]


def test_vorlage_anwenden_mit_seiten_vorlage_422_ohne_schreiben(db, c):
    db.antworten = [[_kopf()], [{"format": "seite", "bloecke": None}]]
    r = c.post(f"/api/pult/inhalte/{IID}/vorlage", headers=H, json={"vorlage": "herbst-split", "basis_fassung": 7})
    assert r.status_code == 422 and "Collage-Vorlagen lassen sich nicht" in r.json()["detail"]
    assert len(db.sql) == 2 and "format" in db.sql[1]


def test_vorlagenliste_liefert_format(db, c):
    db.antworten = [[{"name": "herbst-split", "beschreibung": "", "status": "vorschlag", "fassung": 1,
                      "format": "seite"}]]
    r = c.get("/api/pult/vorlagen?mandant=radhaus&status=vorschlag", headers=H)
    assert r.status_code == 200 and r.json()["vorlagen"][0]["format"] == "seite"
    assert "format FROM marketing.newsletter_vorlagen" in db.sql[0] and "status = 'vorschlag'" in db.sql[0]


def test_vorschau_seiten_vorlage_liefert_gefuellte_seite(db, c, monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    db.antworten = [[{**_seiten_vorlage_zeile(), "beschreibung": "Split", "pflichtteil": {}}], [_laden_radhaus()]]
    r = c.get("/api/pult/vorlagen/herbst-split/vorschau?format=seite&mandant=radhaus", headers=H)
    assert r.status_code == 200, r.text
    assert "Radhaus Jena" in r.text and "VibeMind" not in r.text and "#c2410c" in r.text
    assert "v.seite, v.rollen" in db.sql[0] and not any("pult_bild_auftrag" in q for q in db.sql)


def test_vorschau_block_vorlage_als_seite_422(db, c):
    db.antworten = [[{"format": "bloecke", "bloecke": DOK, "beschreibung": "B", "pflichtteil": {}}]]
    r = c.get("/api/pult/vorlagen/leer/vorschau?format=seite", headers=H)
    assert r.status_code == 422 and "Block-Vorlagen haben keine Seitenansicht" in r.json()["detail"]


def test_vorlage_entscheiden(db, c):
    db.antworten = [[{"status": "freigegeben"}]]
    r = c.post("/api/pult/vorlagen/herbst-split/entscheiden", headers=H, json={"urteil": "freigeben", "von": "Felix"})
    assert r.status_code == 200 and r.json() == {"name": "herbst-split", "status": "freigegeben"}
    assert db.sql[0] == ("SELECT marketing.pult_vorlage_entscheiden('herbst-split', 'freigeben', 'Felix') "
                         "AS status")


@pytest.mark.parametrize("body", [{"urteil": "loeschen", "von": "F"}, {"urteil": "freigeben", "von": "  "},
                                  {"urteil": "freigeben"}, {}])
def test_vorlage_entscheiden_formfehler_422_ohne_sql(db, c, body):
    r = c.post("/api/pult/vorlagen/herbst-split/entscheiden", headers=H, json=body)
    assert r.status_code == 422 and db.sql == []


def test_vorlage_entscheiden_db_grund_und_schluessel(db, c):
    db.fehler = [RuntimeError("ERROR:  Vorlage herbst-split ist schon freigegeben")]
    r = c.post("/api/pult/vorlagen/herbst-split/entscheiden", headers=H, json={"urteil": "freigeben", "von": "F"})
    assert r.status_code == 422 and r.json()["detail"] == "Vorlage herbst-split ist schon freigegeben"
    assert c.post("/api/pult/vorlagen/Herbst/entscheiden", headers=H,
                  json={"urteil": "freigeben", "von": "F"}).status_code == 404
    assert c.post("/api/pult/vorlagen/herbst-split/entscheiden",
                  json={"urteil": "freigeben", "von": "F"}).status_code == 401


def test_db_grund_format_check_wird_deutsch():
    roh = RuntimeError('ERROR:  new row for relation "newsletter_vorlagen" violates check constraint '
                       '"newsletter_vorlagen_format_check"\nDETAIL:  Failing row contains (...)')
    assert pult._db_grund(roh) == pult.VORLAGEN_NAME_BELEGT
    assert pult._db_grund(RuntimeError("ERROR:  Ohne Titel kein Newsletter")) == "Ohne Titel kein Newsletter"


def test_vorschau_seiten_vorlage_auch_bei_format_mail_als_seite(db, c, monkeypatch, tmp_path):
    """sales (ui_editor.vorlage_bild) fragt heute jede Vorlage mit format=mail an - Seiten-Vorlagen kommen dann
    trotzdem als Seiten-HTML, nicht als 422 (Fix-Runde 1, Entscheidung im Report)."""
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    db.antworten = [[{**_seiten_vorlage_zeile(), "beschreibung": "Split", "pflichtteil": {}}], [_laden_radhaus()]]
    r = c.get("/api/pult/vorlagen/herbst-split/vorschau?format=mail&mandant=radhaus", headers=H)
    assert r.status_code == 200 and "Radhaus Jena" in r.text
