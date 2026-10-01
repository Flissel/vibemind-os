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


def test_vorschau_bloecke_pdf_422(db, c):
    db.antworten = [[_bloecke_zeile()]]
    r = c.get(f"/api/pult/inhalte/{IID}/vorschau?fassung=1&format=pdf", headers=H)
    assert r.status_code == 422 and "PDF" in r.json()["detail"]


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
    assert re.search(r"SELECT[^;]*\bformat\b[^;]*\bbloecke\b[^;]*FROM marketing\.inhalt_fassungen", db.sql[-1])


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
    assert "max(fassung) OVER ()" in db.sql[-1]


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
