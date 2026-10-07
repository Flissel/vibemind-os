"""Pult-API der Newsletter-Freigabe (Plan Task 2): Einreichen, Zurueckziehen, Zurueckgeben,
Freigeben (mit Flaechen-Export), Export nachholen, Freigaben-Liste. Ohne echte DB (FalscheDB)."""
import pytest

from spaces.marketing.tests.test_chat_api import (  # noqa: F401 - umg ist eine Fixture
    AID, G_WEG, H, IID, PK, _db_fehler, _dok, umg)

UNBEKANNT = "99999999-9999-9999-9999-999999999999"
ROUTEN = [
    ("post", f"/api/pult/inhalte/{IID}/einreichen", {"von": "x"}),
    ("post", f"/api/pult/inhalte/{IID}/zurueckziehen", {"von": "x"}),
    ("post", f"/api/pult/inhalte/{IID}/zurueckgeben", {"fassung": 1, "von": "x", "text": "t"}),
    ("post", f"/api/pult/inhalte/{IID}/freigeben", {"fassung": 1, "von": "x"}),
    ("post", f"/api/pult/inhalte/{IID}/export_nachholen", {}),
    ("get", "/api/pult/freigaben", None),
]


def _freigegeben(bloecke=None, art="newsletter", fmt="bloecke", status="freigegeben", titel="Oktober-Angebot!"):
    return [{"status": status, "art": art, "format": fmt, "titel": titel, "bloecke": bloecke}]


@pytest.mark.parametrize("methode,pfad,body", ROUTEN)
def test_ohne_schluessel_401(umg, methode, pfad, body):
    f, _, c = umg
    aufruf = getattr(c, methode)
    r = aufruf(pfad) if body is None else aufruf(pfad, json=body)
    assert r.status_code == 401
    falsch = {"X-Pult-Key": "falsch"}
    r = aufruf(pfad, headers=falsch) if body is None else aufruf(pfad, json=body, headers=falsch)
    assert r.status_code == 401 and f.sql == []


# --- einreichen / zurueckziehen ---


def test_einreichen_ok(umg):
    f, _, c = umg
    f.antworten.append([{"fassung": 3}])
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "eingereicht", "fassung": 3}
    assert "marketing.pult_einreichen(" in f.sql[0] and "'Anna'" in f.sql[0] and IID in f.sql[0]


def test_einreichen_db_ablehnung_422(umg):
    f, _, c = umg
    f.fehler.append(_db_fehler("Der Assistent arbeitet gerade"))
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Der Assistent arbeitet gerade"


def test_einreichen_db_weg_503(umg):
    f, _, c = umg
    f.fehler.append(RuntimeError("ssh: connection refused"))
    r = c.post(f"/api/pult/inhalte/{IID}/einreichen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 503 and "ssh" not in r.text


def test_zurueckziehen_ok_und_ablehnung(umg):
    f, _, c = umg
    f.antworten.append([{"s": "entwurf"}])
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckziehen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 200 and r.json() == {"status": "entwurf"}
    assert "marketing.pult_zurueckziehen(" in f.sql[0]
    f.fehler += [_db_fehler("Schon entschieden (freigegeben)")]
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckziehen", json={"von": "Anna"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Schon entschieden (freigegeben)"


def test_unbekannte_id_404(umg):
    f, _, c = umg
    r = c.post("/api/pult/inhalte/keine-uuid/einreichen", json={"von": "x"}, headers=H)
    assert r.status_code == 404 and f.sql == []


# --- zurueckgeben ---


def test_zurueckgeben_ok(umg):
    f, _, c = umg
    f.antworten.append([{"id": AID}])
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckgeben", json={"fassung": 2, "von": "Anna", "text": "Preis fehlt"},
               headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "entwurf", "rueckmeldung": AID}
    assert "marketing.pult_zurueckgeben(" in f.sql[0] and ", 2, 'Anna', 'Preis fehlt')" in f.sql[0]


@pytest.mark.parametrize("text", ["", "   ", "\n\t ", None])
def test_zurueckgeben_leer_422_ohne_sql(umg, text):
    """Review Focus 3: leerer Kommentar erreicht die DB nie."""
    f, _, c = umg
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckgeben", json={"fassung": 2, "von": "Anna", "text": text}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Bitte sag kurz, was fehlt" and f.sql == []


def test_zurueckgeben_zu_lang_422_ohne_sql(umg):
    f, _, c = umg
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckgeben", json={"fassung": 2, "von": "A", "text": "x" * 2001}, headers=H)
    assert r.status_code == 422 and "2000" in r.json()["detail"] and f.sql == []
    f.antworten.append([{"id": AID}])
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckgeben", json={"fassung": 2, "von": "A", "text": "x" * 2000}, headers=H)
    assert r.status_code == 200


def test_zurueckgeben_ohne_fassung_422_ohne_sql(umg):
    f, _, c = umg
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckgeben", json={"von": "A", "text": "t"}, headers=H)
    assert r.status_code == 422 and f.sql == []


def test_zurueckgeben_db_ablehnung_422(umg):
    f, _, c = umg
    f.fehler.append(_db_fehler("Inzwischen gibt es Fassung 4 – bitte neu laden"))
    r = c.post(f"/api/pult/inhalte/{IID}/zurueckgeben", json={"fassung": 3, "von": "A", "text": "t"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Inzwischen gibt es Fassung 4 – bitte neu laden"


# --- freigeben ---


def test_freigeben_entscheidet_zuerst_dann_flaechen_dann_export_auftrag(umg):
    f, ordner, c = umg
    f.antworten += [[{"status": "freigegeben"}],
                    _freigegeben(_dok()),                                    # festgeschriebene Fassung
                    [{"titel": "Oktober-Angebot!", "bloecke": _dok()}],      # Flaechen-Export liest den Inhalt
                    [{"titel": "Oktober-Angebot!", "bloecke": _dok()}],      # Newsletter-Auftrag
                    [{"id": AID}]]
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 2, "von": "Anna"}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d == {"status": "freigegeben",
                 "flaechen": ["oktober-angebot-f-handy.jpg", "oktober-angebot-f-tablet.jpg", "oktober-angebot-f-pc.jpg"],
                 "export_auftrag": AID, "export_fehler": None}
    assert "marketing.pult_entscheiden(" in f.sql[0] and ", 2, 'freigeben', 'Anna'" in f.sql[0]
    assert sorted(p.name for p in ordner.iterdir()) == sorted(d["flaechen"])             # Dateien liegen
    assert len(f.psql.sql) == 1 and "marketing.medien_mandant" in f.psql.sql[0]          # Zuordnung
    anlegen = [n for n, s in enumerate(f.sql) if "pult_chat_anlegen(" in s]
    assert len(anlegen) == 1 and "'export', ''" in f.sql[anlegen[0]] and '"slug": "oktober-angebot"' in f.sql[anlegen[0]]
    assert anlegen[0] == len(f.sql) - 1                                                  # zuletzt


def test_freigeben_veraltete_fassung_422_ohne_export(umg):
    """Review Focus 2: pult_entscheiden lehnt ab -> kein Export-SQL, keine Datei."""
    f, ordner, c = umg
    f.fehler.append(_db_fehler("Inzwischen gibt es Fassung 4 – bitte neu laden"))
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 3, "von": "Anna"}, headers=H)
    assert r.status_code == 422 and r.json()["detail"] == "Inzwischen gibt es Fassung 4 – bitte neu laden"
    assert len(f.sql) == 1 and f.psql.sql == [] and list(ordner.iterdir()) == []


def test_freigeben_export_fehler_bleibt_freigabe_200(umg):
    f, ordner, c = umg
    f.antworten += [[{"status": "freigegeben"}], _freigegeben(_dok(G_WEG)),
                    [{"titel": "T", "bloecke": _dok(G_WEG)}]]
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 2, "von": "Anna"}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "freigegeben" and d["flaechen"] == [] and d["export_auftrag"] is None
    assert "weg.png fehlt" in d["export_fehler"]
    assert list(ordner.iterdir()) == []


def test_freigeben_zuordnung_scheitert_export_fehler_200(umg):
    f, ordner, c = umg
    f.antworten += [[{"status": "freigegeben"}], _freigegeben(_dok()), [{"titel": "T", "bloecke": _dok()}]]
    f.psql.fehler.append(RuntimeError("psql weg"))
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 2, "von": "Anna"}, headers=H)
    assert r.status_code == 200 and r.json()["export_fehler"] and r.json()["status"] == "freigegeben"
    assert list(ordner.iterdir()) == []


def test_freigeben_lesefehler_nach_entscheidung_ist_export_fehler(umg):
    f, _, c = umg
    f.antworten.append([{"status": "freigegeben"}])
    f.fehler += [None, RuntimeError("db weg")]
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 2, "von": "Anna"}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "freigegeben" and r.json()["export_fehler"]


def test_freigeben_post_nur_fassung_festschreiben(umg):
    f, ordner, c = umg
    f.antworten += [[{"status": "freigegeben"}], _freigegeben(None, art="post", fmt="felder")]
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 1, "von": "Anna"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "freigegeben", "flaechen": [], "export_auftrag": None, "export_fehler": None}
    assert all("pult_chat_anlegen" not in s for s in f.sql) and list(ordner.iterdir()) == []


def test_freigeben_newsletter_im_feldformat_ohne_export_auftrag(umg):
    f, _, c = umg
    f.antworten += [[{"status": "freigegeben"}], _freigegeben(None, fmt="felder")]
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 1, "von": "Anna"}, headers=H)
    assert r.json()["export_auftrag"] is None and all("pult_chat_anlegen" not in s for s in f.sql)


def test_freigeben_ohne_flaechen_nur_newsletter_auftrag(umg):
    f, ordner, c = umg
    kein_bild = {"root": {"type": "EmailLayout", "data": {"childrenIds": []}}}
    f.antworten += [[{"status": "freigegeben"}], _freigegeben(kein_bild),
                    [{"titel": "Heft", "bloecke": kein_bild}], [{"id": AID}]]
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 1, "von": "Anna"}, headers=H)
    assert r.json() == {"status": "freigegeben", "flaechen": [], "export_auftrag": AID, "export_fehler": None}
    assert f.psql.sql == [] and list(ordner.iterdir()) == []


def test_freigeben_ohne_fassung_422_ohne_sql(umg):
    f, _, c = umg
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"von": "Anna"}, headers=H)
    assert r.status_code == 422 and f.sql == []


# --- export_nachholen ---


def test_export_nachholen_bei_freigegeben(umg):
    f, ordner, c = umg
    f.antworten += [_freigegeben(_dok()), [{"titel": "Oktober-Angebot!", "bloecke": _dok()}],
                    [{"vorhanden": False}],
                    [{"titel": "Oktober-Angebot!", "bloecke": _dok()}], [{"id": AID}]]
    r = c.post(f"/api/pult/inhalte/{IID}/export_nachholen", json={}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "freigegeben" and len(d["flaechen"]) == 3 and d["export_auftrag"] == AID
    assert d["flaechen_uebersprungen"] == [] and d["auftrag_vorhanden"] is False
    assert d["export_fehler"] is None
    assert all("pult_entscheiden" not in s for s in f.sql)


def test_export_nachholen_bei_entwurf_422(umg):
    f, ordner, c = umg
    f.antworten.append(_freigegeben(_dok(), status="entwurf"))
    r = c.post(f"/api/pult/inhalte/{IID}/export_nachholen", json={}, headers=H)
    assert r.status_code == 422 and len(f.sql) == 1 and list(ordner.iterdir()) == []


def test_export_nachholen_unbekannt_404(umg):
    f, _, c = umg
    r = c.post(f"/api/pult/inhalte/{UNBEKANNT}/export_nachholen", json={}, headers=H)
    assert r.status_code == 404


def test_export_nachholen_fehler_im_export_200(umg):
    f, _, c = umg
    f.antworten += [_freigegeben(_dok(G_WEG)), [{"titel": "T", "bloecke": _dok(G_WEG)}]]
    r = c.post(f"/api/pult/inhalte/{IID}/export_nachholen", json={}, headers=H)
    assert r.status_code == 200 and "weg.png fehlt" in r.json()["export_fehler"]


# --- Freigaben-Liste ---

ZEILE = {"id": IID, "mandant": "vibemind", "mandant_name": "Vibemind", "art": "newsletter", "titel": "Oktober",
         "betreff": "Hallo", "format": "bloecke", "status": "eingereicht", "eingereichte_fassung": 2, "eingereicht_am": "2026-10-07 10:00",
         "eingereicht_von": "Anna", "entschieden_von": None, "entschieden_am": None, "grund": None,
         "rueckmeldungen": [{"text": "Preis", "von": "Anna", "am": "2026-10-06", "fassung": 1, "erledigt": True}],
         "export": {"auftrag_status": None}}


def test_freigaben_form_und_mandantenname(umg):
    f, _, c = umg
    f.antworten.append([ZEILE])
    r = c.get("/api/pult/freigaben", headers=H)
    assert r.status_code == 200, r.text
    z = r.json()["freigaben"][0]
    for k in ("id", "mandant", "mandant_name", "art", "titel", "betreff", "format", "status", "eingereichte_fassung",
              "eingereicht_am", "eingereicht_von", "entschieden_von", "entschieden_am", "grund", "rueckmeldungen",
              "export"):
        assert k in z
    assert z["mandant_name"] == "Vibemind" and z["rueckmeldungen"][0]["erledigt"] is True
    sql = f.sql[0]
    assert "status = 'eingereicht'" in sql and "marketing.mandanten" in sql and "LIMIT 20" in sql
    assert "i.art = " not in sql            # alle Arten (R7)
    assert "marketing.rueckmeldungen" in sql and "LIMIT 3" in sql and "art = 'export'" in sql


def test_freigaben_format_der_neuesten_fassung(umg):
    """F2b: format (bloecke|felder) der neuesten Fassung, damit sales-ui den Export-Knopf bei felder weglaesst."""
    f, _, c = umg
    f.antworten.append([dict(ZEILE, format="felder")])
    r = c.get("/api/pult/freigaben?status=entschieden", headers=H)
    assert r.json()["freigaben"][0]["format"] == "felder"
    sql = f.sql[0]
    teil = sql[sql.index("f.format"):sql.index("AS format")]
    assert "ORDER BY f.fassung DESC LIMIT 1" in teil


def test_freigaben_export_status_nur_seit_der_entscheidung(umg):
    """F3: bei freigegeben zaehlt nur ein Export-Auftrag ab der Entscheidung - ein alter aus der
    Entwurfszeit verdeckt sonst "Export offen – erneut anstoßen"."""
    f, _, c = umg
    f.antworten.append([])
    c.get("/api/pult/freigaben?status=entschieden", headers=H)
    sql = f.sql[0]
    export = sql[sql.index("'auftrag_status'"):sql.index("AS export")]
    assert "a.erstellt_am >= i.entschieden_am" in export
    assert "i.status <> 'freigegeben' OR a.erstellt_am >= i.entschieden_am" in export
    assert export.index("entschieden_am") < export.index("ORDER BY a.erstellt_am DESC LIMIT 1")


def test_freigaben_entschieden_und_limit(umg):
    f, _, c = umg
    f.antworten.append([])
    r = c.get("/api/pult/freigaben?status=entschieden&limit=5", headers=H)
    assert r.status_code == 200 and r.json() == {"freigaben": []}
    sql = f.sql[0]
    assert "'freigegeben'" in sql and "'abgelehnt'" in sql and "30 days" in sql and "LIMIT 5" in sql
    assert "marketing.rueckmeldungen" in sql and "status = 'entwurf'" in sql


@pytest.mark.parametrize("abfrage", ["status=offen", "limit=0", "limit=101", "limit=x"])
def test_freigaben_ungueltige_parameter_422(umg, abfrage):
    f, _, c = umg
    assert c.get(f"/api/pult/freigaben?{abfrage}", headers=H).status_code == 422 and f.sql == []


def test_freigaben_db_weg_503(umg):
    f, _, c = umg
    f.fehler.append(RuntimeError("weg"))
    assert c.get("/api/pult/freigaben", headers=H).status_code == 503


# --- Fix-Runde 1 ---


def test_nachholen_ueberspringt_vorhandene_flaeche_und_laufenden_auftrag(umg):
    """B: liegt schon <slug>-<id>-<geraet>.jpg, wird nichts neu gerendert; vorhandener Export-Auftrag -> kein neuer."""
    f, ordner, c = umg
    (ordner / "oktober-angebot-f-tablet.jpg").write_bytes(b"alt")
    f.antworten += [_freigegeben(_dok()), [{"vorhanden": True}]]
    r = c.post(f"/api/pult/inhalte/{IID}/export_nachholen", json={}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["flaechen"] == [] and d["flaechen_uebersprungen"] == ["f"]
    assert d["export_auftrag"] is None and d["auftrag_vorhanden"] is True and d["export_fehler"] is None
    assert [p.name for p in ordner.iterdir()] == ["oktober-angebot-f-tablet.jpg"] and f.psql.sql == []
    assert all("pult_chat_anlegen" not in s for s in f.sql)
    sql = f.sql[1]
    assert "art = 'export'" in sql and "'offen', 'in_arbeit'" in sql and "'fertig'" in sql and "entschieden_am" in sql


def test_freigeben_exportiert_immer_auch_bei_vorhandener_datei(umg):
    """B gilt nur fuer nachholen: frisches Freigeben fragt nicht nach vorhandenen Dateien/Auftraegen."""
    f, ordner, c = umg
    (ordner / "oktober-angebot-f-tablet.jpg").write_bytes(b"alt")
    f.antworten += [[{"status": "freigegeben"}], _freigegeben(_dok(), art="post"),
                    [{"titel": "Oktober-Angebot!", "bloecke": _dok()}]]
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 2, "von": "Anna"}, headers=H)
    assert len(r.json()["flaechen"]) == 3 and "flaechen_uebersprungen" not in r.json()


def test_freigaben_entschieden_nur_offene_rueckmeldung(umg):
    """C: eine zurueckgegebene, wieder eingereichte und zurueckgezogene Fassung (Rueckmeldung erledigt) fehlt."""
    f, _, c = umg
    f.antworten.append([])
    c.get("/api/pult/freigaben?status=entschieden", headers=H)
    assert "erledigt_am IS NULL" in f.sql[0] and f.sql[0].index("erledigt_am IS NULL") > f.sql[0].index("status = 'entwurf'")


def test_freigeben_mehr_als_20_flaechen_in_stuecken(umg, monkeypatch):
    """D: mehr Flaechen als FLAECHEN_MAX werden in Stuecken exportiert, kein export_fehler."""
    from spaces.marketing.api import chat, freigabe
    monkeypatch.setattr(chat, "FLAECHEN_MAX", 2)
    monkeypatch.setattr(freigabe, "FLAECHEN_MAX", 2)
    f, ordner, c = umg
    dok = _dok()
    for n in range(4):
        dok[f"g{n}"] = dict(dok["f"])
    f.antworten += [[{"status": "freigegeben"}], _freigegeben(dok, art="post")] +                    [[{"titel": "Oktober", "bloecke": dok}]] * 3
    r = c.post(f"/api/pult/inhalte/{IID}/freigeben", json={"fassung": 2, "von": "Anna"}, headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["export_fehler"] is None and len(d["flaechen"]) == 15           # 5 Flaechen x 3 Geraete
    assert len(f.psql.sql) == 3                                              # 3 Stuecke (2+2+1)
