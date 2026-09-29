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
