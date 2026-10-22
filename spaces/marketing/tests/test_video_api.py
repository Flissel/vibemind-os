"""Reel-Auftraege (Video-Claw, 17.10.2026, Migration 072) ohne echte DB - FalscheDB wie test_ton_api."""
import time

import pytest
from fastapi.testclient import TestClient

from spaces.marketing.api import medien_mandant, server
from spaces.marketing.api import video
from spaces.marketing.sync import _db
from spaces.marketing.tests.test_bilder_api import FalschePsql
from spaces.marketing.tests.test_pult_api import FalscheDB

AID = "0123abcd-3333-3333-3333-333333333333"
BK, AK, PK = "bild-k", "api-k", "pult-k"
A, B, P = {"X-API-Key": AK}, {"X-Bild-Key": BK}, {"X-Pult-Key": PK}
MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 3000
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 2000
BILDER = {"thema": "bAV in 30 Sekunden", "sprechertext": "Drei Fehler, die fast jede Firma macht.",
          "bilder": ["team.png", "buero.jpg"], "logo": "logo-vibemind-0123456789.png",
          "farben": {"hintergrund": "#0f2422", "text": "#e9fbf6"}}


@pytest.fixture
def db(monkeypatch, tmp_path):
    f = FalscheDB()
    f.psql = FalschePsql(f)
    monkeypatch.setattr(_db, "query_via_docker", f.query)
    monkeypatch.setattr(_db, "query_one", f.one)
    monkeypatch.setattr(_db, "_run_psql", f.psql)
    monkeypatch.setenv("MARKETING_BILD_KEY", BK)
    monkeypatch.setenv("MARKETING_PULT_KEY", PK)
    monkeypatch.setenv("MARKETING_BILD_ORDNER", str(tmp_path))
    monkeypatch.delenv("MARKETING_MEDIEN_ORDNER", raising=False)
    monkeypatch.delenv("MARKETING_VIDEO_MEDIEN_BASIS", raising=False)
    monkeypatch.delenv("MARKETING_STIMMEN", raising=False)
    monkeypatch.setattr(server, "API_KEY", AK)
    monkeypatch.setattr(medien_mandant, "sicht", lambda m: {"name": m, "eigene": set(), "gemeinsam": set(),
                                                            "fremde": {"fremd.png"}})
    f.ordner = tmp_path
    video._material_zuruecksetzen()
    return f


@pytest.fixture
def c():
    return TestClient(server.app)


# --- Briefing ----------------------------------------------------------------

def test_briefing_bilder_wird_gekuerzt():
    b = video.briefing_pruefen("bilder", {**BILDER, "unbekannt": "weg"})
    assert "unbekannt" not in b and b["dauer_s"] == 30 and b["stimme"] == "standard"
    assert b["bilder"] == ["team.png", "buero.jpg"] and b["hook"] == ""


@pytest.mark.parametrize("art, b, grund", [
    ("film", BILDER, "art"),
    ("bilder", {**BILDER, "bilder": []}, "bilder"),
    ("bilder", {**BILDER, "bilder": ["../x.png"]}, "bilder"),
    ("bilder", {**BILDER, "bilder": ["a.gif"]}, "bilder"),
    ("bilder", {**BILDER, "farben": {"hintergrund": "red"}}, "farben"),
    ("bilder", {**BILDER, "dauer_s": 90}, "dauer_s"),
    ("bilder", {**BILDER, "dauer_s": True}, "dauer_s"),
    ("bilder", {**BILDER, "stimme": "../anna"}, "stimme"),
    ("bilder", {**BILDER, "sprechertext": "x" * 901}, "sprechertext"),
    ("bilder", {**BILDER, "thema": ""}, "thema"),
    ("material", {"thema": "t", "sprechertext": "s", "videos": ["nicht hex!"]}, "videos"),
    ("kurzfassung", {"thema": "t"}, "video"),
])
def test_briefing_formen(art, b, grund):
    with pytest.raises(Exception) as e:
        video.briefing_pruefen(art, b)
    assert getattr(e.value, "status_code", None) == 422 and grund in e.value.detail


def test_kurzfassung_ohne_sprechertext():
    b = video.briefing_pruefen("kurzfassung", {"thema": "t", "video": "a1b2c3d4e5f6"})
    assert b["sprechertext"] == "" and b["video"] == "a1b2c3d4e5f6"


# --- Anlegen -----------------------------------------------------------------

def test_agent_legt_an_ueber_funktion(db, c):
    db.antworten = [[], [{"id": AID}]]          # erst der Markenspiegel (keine Stimme), dann das Anlegen
    r = c.post("/api/video/auftraege", headers=A,
               json={"firma": "vibemind", "art": "bilder", "titel": "Herbst-Reel", "briefing": BILDER})
    assert r.status_code == 200 and r.json() == {"auftrag": AID}
    assert "marketing.video_auftrag_anlegen(" in db.sql[-1] and "'agent'" in db.sql[-1]
    assert "INSERT" not in db.sql[-1]


def test_fremdes_bild_abgelehnt(db, c):
    r = c.post("/api/video/auftraege", headers=A, json={
        "firma": "vibemind", "art": "bilder", "titel": "T", "briefing": {**BILDER, "bilder": ["fremd.png"]}})
    assert r.status_code == 422 and "anderen Firma" in r.json()["detail"] and not db.sql


def test_pult_legt_als_betreiber_an(db, c):
    db.antworten = [[], [{"id": AID}]]
    r = c.post("/api/pult/video", headers=P, json={"mandant": "vibemind", "art": "material", "titel": "T",
                                                   "briefing": {"thema": "t", "sprechertext": "s"}})
    assert r.status_code == 200 and "'betreiber'" in db.sql[-1]


def test_pult_abbrechen(db, c):
    db.antworten = [[{"s": "abgebrochen"}]]
    r = c.post(f"/api/pult/video/{AID}/abbrechen", headers=P)
    assert r.status_code == 200 and r.json() == {"status": "abgebrochen"}
    assert "marketing.video_abbrechen(" in db.sql[-1]


def test_schluessel_pflicht(db, c):
    assert c.get("/api/pult/video?mandant=vibemind").status_code == 401
    assert c.get("/api/video/auftraege?firma=vibemind").status_code == 401
    assert c.post("/api/video/arbeiter/naechster").status_code == 401
    assert c.get("/api/pult/video/bilder?mandant=vibemind").status_code == 401
    assert c.get("/api/pult/video/material").status_code == 401
    assert c.post(f"/api/pult/video/{AID}/nochmal", json={}).status_code == 401
    assert c.post(f"/api/pult/video/{AID}/archivieren", json={}).status_code == 401
    assert c.post(f"/api/video/arbeiter/{AID}/poster", content=JPEG).status_code == 401
    assert c.post(f"/api/video/arbeiter/{AID}/poster", headers=P, content=JPEG).status_code == 401
    assert not db.sql


# --- Arbeiter ----------------------------------------------------------------

def test_naechster_und_weiter(db, c):
    db.antworten = [[{"a": {"id": AID, "art": "bilder"}}], [{"ok": True}]]
    assert c.post("/api/video/arbeiter/naechster", headers=B).json()["auftrag"]["id"] == AID
    assert c.post(f"/api/video/arbeiter/{AID}/weiter", headers=B).json() == {"ok": True}


def _in_arbeit(db, status="in_arbeit"):
    return [{"titel": "Herbst-Reel im Laden", "mandant": "vibemind", "status": status, "briefing": {}}]


def test_datei_ablegen(db, c):
    db.antworten = [_in_arbeit(db)]
    r = c.post(f"/api/video/arbeiter/{AID}/datei", headers=B, content=MP4)
    assert r.status_code == 200
    name = r.json()["name"]
    assert name == "vid-0123abcd-herbst-reel-im-laden.mp4" and (db.ordner / name).read_bytes() == MP4


def test_datei_nur_mp4(db, c):
    r = c.post(f"/api/video/arbeiter/{AID}/datei", headers=B, content=b"GIF89a" + b"\x00" * 100)
    assert r.status_code == 422


def test_datei_zu_gross(db, c):
    r = c.post(f"/api/video/arbeiter/{AID}/datei", headers=B, content=MP4 + b"\x00" * video.MP4_MAX)
    assert r.status_code == 413


def test_datei_nicht_in_arbeit(db, c):
    db.antworten = [_in_arbeit(db, "abgebrochen")]
    assert c.post(f"/api/video/arbeiter/{AID}/datei", headers=B, content=MP4).status_code == 409


def test_fertig(db, c):
    name = "vid-0123abcd-herbst.mp4"
    (db.ordner / name).write_bytes(MP4)
    db.antworten = [[{"v": "fertig"}]]
    r = c.post(f"/api/video/arbeiter/{AID}/fertig", headers=B,
               json={"datei": name, "dauer_s": 24.5, "laura_export_id": "a1b2c3d4e5f6"})
    assert r.status_code == 200 and r.json() == {"status": "fertig"}
    assert f"{len(MP4)}" in db.sql[-1] and "'a1b2c3d4e5f6'" in db.sql[-1]


@pytest.mark.parametrize("koerper", [
    {"datei": "vid-ffffffff-fremd.mp4", "dauer_s": 10},
    {"datei": "../x.mp4", "dauer_s": 10},
    {"datei": "vid-0123abcd-herbst.mp4", "dauer_s": 500},
    {"befund": 5},
    {"laura_export_id": "x y"},
])
def test_fertig_formen(db, c, koerper):
    (db.ordner / "vid-0123abcd-herbst.mp4").write_bytes(MP4)
    assert c.post(f"/api/video/arbeiter/{AID}/fertig", headers=B, json=koerper).status_code == 422


def test_fehler_ohne_datei(db, c):
    db.antworten = [[{"v": "fehler"}]]
    r = c.post(f"/api/video/arbeiter/{AID}/fertig", headers=B, json={"befund": "Laura 503"})
    assert r.json() == {"status": "fehler"} and "NULL" in db.sql[-1]


# --- signierter Medienlink fuer Laura ----------------------------------------

def test_medienlink_und_abruf(db, c, monkeypatch):
    (db.ordner / "team.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    monkeypatch.setenv("MARKETING_VIDEO_MEDIEN_BASIS", "https://vm.example:8446")
    db.antworten = [_in_arbeit(db)]
    r = c.post(f"/api/video/arbeiter/{AID}/medien-link", headers=B, json={"name": "team.png"})
    assert r.status_code == 200
    url = r.json()["url"]
    assert url.startswith("https://vm.example:8446/api/video/medien/") and url.endswith("/team.png")
    pfad = url[len("https://vm.example:8446"):]
    abruf = c.get(pfad)        # ohne jeden Schluessel - die Signatur ist die Berechtigung
    assert abruf.status_code == 200 and abruf.content.startswith(b"\x89PNG")


def test_medienlink_fremdes_bild(db, c):
    (db.ordner / "fremd.png").write_bytes(b"\x89PNG")
    db.antworten = [_in_arbeit(db)]
    r = c.post(f"/api/video/arbeiter/{AID}/medien-link", headers=B, json={"name": "fremd.png"})
    assert r.status_code == 422


def test_abruf_gefaelscht_oder_abgelaufen(db, c):
    (db.ordner / "team.png").write_bytes(b"\x89PNG")
    ablauf = int(time.time()) + 600
    assert c.get(f"/api/video/medien/{ablauf}/{'0' * 40}/team.png").status_code == 403
    alt = int(time.time()) - 10
    assert c.get(f"/api/video/medien/{alt}/{video._signatur(alt, 'team.png')}/team.png").status_code == 403
    weit = int(time.time()) + 86400
    assert c.get(f"/api/video/medien/{weit}/{video._signatur(weit, 'team.png')}/team.png").status_code == 403
    gut = video._signatur(ablauf, "team.png")
    assert c.get(f"/api/video/medien/{ablauf}/{gut}/andere.png").status_code in (403, 404)


@pytest.mark.parametrize("name", ["Fremd.PNG", "FREMD.png", "fremd.png:x.png", "team.png:fremd.png"])
def test_medienlink_keine_namensumgehung(db, c, name):
    """NTFS oeffnet Gross-/Kleinvarianten und Datenstroeme - die Firmenpruefung vergleicht exakt."""
    (db.ordner / "fremd.png").write_bytes(b"\x89PNG")
    (db.ordner / "team.png").write_bytes(b"\x89PNG")
    db.antworten = [_in_arbeit(db)]
    r = c.post(f"/api/video/arbeiter/{AID}/medien-link", headers=B, json={"name": name})
    assert r.status_code in (404, 422)


def test_abruf_keine_namensumgehung(db, c):
    (db.ordner / "fremd.png").write_bytes(b"\x89PNG")
    ablauf = int(time.time()) + 600
    for name in ("Fremd.PNG", "fremd.png:x.png"):
        r = c.get(f"/api/video/medien/{ablauf}/{video._signatur(ablauf, name)}/{name}")
        assert r.status_code == 404, name


# --- Abholung aus Lauras Objektspeicher + Dauer --------------------------------

def _mvhd_mp4(skala: int, dauer: int) -> bytes:
    import struct
    mvhd_inhalt = b"\x00\x00\x00\x00" + b"\x00" * 8 + struct.pack(">II", skala, dauer) + b"\x00" * 80
    mvhd = struct.pack(">I4s", 8 + len(mvhd_inhalt), b"mvhd") + mvhd_inhalt
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    ftyp = struct.pack(">I4s", 16, b"ftyp") + b"isom\x00\x00\x02\x00"
    return ftyp + moov


GUT = "http://172.17.0.1:5055/object/sign/laura/exports/0a1b2c3d-e4f5.mp4?token=eyJhbGciOiJIUzI1NiJ9.x.y"


def test_mp4_dauer_aus_mvhd():
    assert video.mp4_dauer(_mvhd_mp4(1000, 24500)) == 24.5
    assert video.mp4_dauer(b"\x00\x00\x00\x10ftypisom\x00\x00\x02\x00") is None
    assert video.mp4_dauer(b"kaputt") is None


def test_mp4_dauer_echt(tmp_path):
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg fehlt")
    d = tmp_path / "x.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=30", "-t", "3",
                    "-c:v", "libx264", str(d)], check=True)
    assert abs(video.mp4_dauer(d.read_bytes()) - 3.0) < 0.1


def test_aus_laura_nur_erlaubte_quelle(db, c, monkeypatch):
    monkeypatch.setenv("MARKETING_VIDEO_LAURA_QUELLEN", "172.17.0.1:5055")
    monkeypatch.setattr(video, "_abrufen", lambda url: pytest.fail("darf nicht abrufen"))
    for url in ("http://169.254.169.254/latest", "http://172.17.0.1:5056/x", "file:///etc/passwd",
                "http://user:pw@172.17.0.1:5055/x",
                # richtiger Host, falscher Pfad (Sicherheitspruefung: Host allein genuegt nicht)
                "http://172.17.0.1:5055/bucket", "http://172.17.0.1:5055/object/sign/anderer/exports/a.mp4?token=abcdefghijk",
                "http://172.17.0.1:5055/object/sign/laura/assets/a.mp4?token=abcdefghijk",
                "http://172.17.0.1:5055/object/sign/laura/exports/../x.mp4?token=abcdefghijk",
                "http://172.17.0.1:5055/object/sign/laura/exports/a%2e.mp4?token=abcdefghijk",
                "http://172.17.0.1:5055/object/sign/laura/exports/a.mp4",
                "http://172.17.0.1:5055/object/sign/laura/exports/a.mp4?token=abcdefghijk&x=1",
                GUT + "#frag"):
        r = c.post(f"/api/video/arbeiter/{AID}/aus-laura", headers=B, json={"url": url})
        assert r.status_code == 422, url


def test_aus_laura_ohne_liste_gesperrt(db, c, monkeypatch):
    monkeypatch.delenv("MARKETING_VIDEO_LAURA_QUELLEN", raising=False)
    r = c.post(f"/api/video/arbeiter/{AID}/aus-laura", headers=B, json={"url": "http://172.17.0.1:5055/x"})
    assert r.status_code == 422


def test_aus_laura_holt_und_legt_ab(db, c, monkeypatch):
    monkeypatch.setenv("MARKETING_VIDEO_LAURA_QUELLEN", "172.17.0.1:5055")
    inhalt = _mvhd_mp4(1000, 21000)
    monkeypatch.setattr(video, "_abrufen", lambda url: inhalt)
    db.antworten = [_in_arbeit(db), _in_arbeit(db)]
    r = c.post(f"/api/video/arbeiter/{AID}/aus-laura", headers=B,
               json={"url": GUT})
    assert r.status_code == 200, r.text
    assert r.json()["dauer_s"] == 21.0 and (db.ordner / r.json()["name"]).read_bytes() == inhalt


def test_aus_laura_zu_gross(db, c, monkeypatch):
    monkeypatch.setenv("MARKETING_VIDEO_LAURA_QUELLEN", "172.17.0.1:5055")
    monkeypatch.setattr(video, "_abrufen", lambda url: MP4 + b"\x00" * video.MP4_MAX)
    db.antworten = [_in_arbeit(db)]
    r = c.post(f"/api/video/arbeiter/{AID}/aus-laura", headers=B, json={"url": GUT})
    assert r.status_code == 413


# --- Marke in Reels (Paket C1, 19.10.2026) -------------------------------------------------------

def test_marke_des_auftrags(db, c, monkeypatch):
    monkeypatch.setattr(video, "_markenlogos", lambda m: ("medien:logo-vibemind-5ca856231b.png", None))
    werte = {"akzent": "#5b3fd6", "zweitfarbe": "#f3e6e3", "schrift_anzeige": "oxanium", "schrift_text": "dm-sans"}
    db.antworten = [_in_arbeit(db), [{"w": werte}]]
    r = c.post(f"/api/video/arbeiter/{AID}/marke", headers=B)
    assert r.status_code == 200
    assert r.json() == {"mandant": "vibemind", "akzent": "#5b3fd6", "schrift_anzeige": "oxanium",
                        "schrift_text": "dm-sans", "logo": "logo-vibemind-5ca856231b.png",
                        "format": "reel"}


def test_marke_nur_gueltige_werte(db, c, monkeypatch):
    monkeypatch.setattr(video, "_markenlogos", lambda m: (None, None))
    db.antworten = [_in_arbeit(db), [{"w": {"akzent": "rot", "schrift_anzeige": "comic", "schrift_text": "dm-sans"}}]]
    assert c.post(f"/api/video/arbeiter/{AID}/marke", headers=B).json() == {
        "mandant": "vibemind", "akzent": None, "schrift_anzeige": None, "schrift_text": "dm-sans", "logo": None,
        "format": "reel"}


def test_marke_ohne_spiegel(db, c, monkeypatch):
    monkeypatch.setattr(video, "_markenlogos", lambda m: (None, None))
    db.antworten = [_in_arbeit(db), []]
    assert c.post(f"/api/video/arbeiter/{AID}/marke", headers=B).json()["akzent"] is None


def test_marke_braucht_arbeiterschluessel(db, c):
    assert c.post(f"/api/video/arbeiter/{AID}/marke").status_code in (401, 403)


# --- Reels Stufe 1 (21.10.2026, Migration 085): Befund, Liste, nochmal, Archiv, Material, Stimme, Poster ----

@pytest.mark.parametrize("befund, satz", [
    ("Zwei Versuche ohne Ergebnis", "Der Bau ist zweimal hängen geblieben."),
    ("zwei versuche ohne ergebnis", "Der Bau ist zweimal hängen geblieben."),
    ("Keine Stimmprobe fuer 'anna' hinterlegt", "Die gewählte Stimme gibt es nicht."),
    ("Laura: Render fehlgeschlagen", "Laura konnte das Video nicht fertig rendern."),
    ("Zeitlimit des Claws erreicht", "Der Bau hat zu lange gedauert."),
    ("shim TIMEOUT", "Der Bau hat zu lange gedauert."),
    ("Das dauerte zu lange", "Der Bau hat zu lange gedauert."),
    ("Vom Betreiber abgebrochen", "Der Auftrag wurde abgebrochen."),
    ("", ""),
    ("   ", ""),
    (None, ""),
    ("Laura 503", "Das Reel konnte nicht gebaut werden."),
    # Reihenfolge: das erste passende Muster gewinnt
    ("Stimmprobe fehlt, Render abgebrochen", "Die gewählte Stimme gibt es nicht."),
])
def test_befund_satz(befund, satz):
    assert video.befund_satz(befund) == satz


def _zeile(**mehr):
    z = {"id": AID, "mandant": "vibemind", "art": "bilder", "titel": "T", "briefing": {}, "status": "fehler",
         "datei": None, "befund": "Zwei Versuche ohne Ergebnis", "vorgaenger": None, "aenderungswunsch": "",
         "archiviert": False, "poster": None, "vorgaenger_titel": None}
    z.update(mehr)
    return z


def test_liste_hat_neue_felder_und_archivfilter(db, c):
    db.antworten = [[_zeile()], [_zeile(archiviert=True, befund="")]]
    r = c.get("/api/pult/video?mandant=vibemind", headers=P)
    assert r.status_code == 200
    zeile = r.json()["auftraege"][0]
    assert zeile["befund_text"] == "Der Bau ist zweimal hängen geblieben."
    for feld in ("vorgaenger", "vorgaenger_titel", "aenderungswunsch", "archiviert", "poster"):
        assert feld in db.sql[-1]
    assert "archiviert = false" in db.sql[-1]
    r = c.get("/api/pult/video?mandant=vibemind&archiv=true", headers=P)
    assert r.json()["auftraege"][0]["befund_text"] == "" and "archiviert = true" in db.sql[-1]


def test_agent_liste_ohne_archivfilter(db, c):
    db.antworten = [[_zeile()]]
    r = c.get("/api/video/auftraege?firma=vibemind", headers=A)
    assert r.status_code == 200 and "archiviert =" not in db.sql[-1]


NEU = "0123abcd-4444-4444-4444-444444444444"


def test_nochmal_regeln(db, c):
    db.antworten = [[{"mandant": "vibemind"}], [], [{"id": NEU}]]
    r = c.post(f"/api/pult/video/{AID}/nochmal", headers=P, json={"wunsch": "kürzer, anderer Einstieg",
                                                                  "mandant": "vibemind"})
    assert r.status_code == 200 and r.json() == {"auftrag": NEU}
    assert "marketing.video_nochmal(" in db.sql[-1] and "'kürzer, anderer Einstieg'" in db.sql[-1]
    assert "'betreiber'" in db.sql[-1]
    # DB-Ablehnung (die Regeln stehen in 085) -> 422 mit der Meldung
    db.antworten = [[{"mandant": "vibemind"}], []]
    db.fehler = [None, None, RuntimeError("ERROR:  Nur fertige oder fehlgeschlagene Reels lassen sich neu bauen")]
    r = c.post(f"/api/pult/video/{AID}/nochmal", headers=P, json={"wunsch": "", "mandant": "vibemind"})
    assert r.status_code == 422
    assert r.json()["detail"] == "Nur fertige oder fehlgeschlagene Reels lassen sich neu bauen"


def test_nochmal_ohne_wunsch_ist_neu_versuchen(db, c):
    db.antworten = [[{"mandant": "vibemind"}], [], [{"id": NEU}]]
    r = c.post(f"/api/pult/video/{AID}/nochmal", headers=P, json={"mandant": "vibemind"})
    assert r.status_code == 200 and "''" in db.sql[-1]


@pytest.mark.parametrize("koerper", [{"wunsch": 5, "mandant": "vibemind"},
                                     {"wunsch": "x" * 501, "mandant": "vibemind"},
                                     {"wunsch": "x", "mandant": "../x"}, {"wunsch": "x"}])
def test_nochmal_formen(db, c, koerper):
    db.antworten = [[{"mandant": "vibemind"}]]
    r = c.post(f"/api/pult/video/{AID}/nochmal", headers=P, json=koerper)
    assert r.status_code == 422 and not any("video_nochmal" in s for s in db.sql)


def test_nochmal_fremder_mandant_404(db, c):
    db.antworten = [[{"mandant": "andere"}]]
    r = c.post(f"/api/pult/video/{AID}/nochmal", headers=P, json={"wunsch": "kürzer", "mandant": "vibemind"})
    assert r.status_code == 404 and not any("video_nochmal" in s for s in db.sql)
    db.antworten = [[]]            # Auftrag gibt es nicht
    r = c.post(f"/api/pult/video/{AID}/nochmal", headers=P, json={"wunsch": "kürzer", "mandant": "vibemind"})
    assert r.status_code == 404
    r = c.post("/api/pult/video/kein-uuid/nochmal", headers=P, json={"wunsch": "kürzer", "mandant": "vibemind"})
    assert r.status_code == 404
    assert not any("video_nochmal" in s for s in db.sql)


def test_archivieren(db, c):
    db.antworten = [[{"mandant": "vibemind"}], [{"ok": True}]]
    r = c.post(f"/api/pult/video/{AID}/archivieren", headers=P, json={"an": True, "mandant": "vibemind"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "marketing.video_archivieren(" in db.sql[-1] and "true" in db.sql[-1]
    db.antworten = [[{"mandant": "vibemind"}], [{"ok": False}]]
    r = c.post(f"/api/pult/video/{AID}/archivieren", headers=P, json={"an": False, "mandant": "vibemind"})
    assert r.json() == {"ok": False} and "false" in db.sql[-1]
    # fremde Firma -> 404, kein SQL-Aufruf
    db.antworten = [[{"mandant": "andere"}]]
    r = c.post(f"/api/pult/video/{AID}/archivieren", headers=P, json={"an": True, "mandant": "vibemind"})
    assert r.status_code == 404 and "video_archivieren" not in db.sql[-1]
    # an muss bool sein
    r = c.post(f"/api/pult/video/{AID}/archivieren", headers=P, json={"an": "ja", "mandant": "vibemind"})
    assert r.status_code == 422


def test_abbrechen_fremder_mandant_404(db, c):
    db.antworten = [[{"mandant": "andere"}]]
    r = c.post(f"/api/pult/video/{AID}/abbrechen", headers=P, json={"mandant": "vibemind"})
    assert r.status_code == 404 and not any("video_abbrechen" in s for s in db.sql)
    db.antworten = [[{"mandant": "vibemind"}], [{"s": "abgebrochen"}]]
    r = c.post(f"/api/pult/video/{AID}/abbrechen", headers=P, json={"mandant": "vibemind"})
    assert r.status_code == 200 and "marketing.video_abbrechen(" in db.sql[-1]


# --- Material-Listen -------------------------------------------------------------------------------

def test_bilder_der_firma_liste(db, c, tmp_path, monkeypatch):
    import os
    medien = tmp_path / "medien"
    medien.mkdir()
    monkeypatch.setenv("MARKETING_MEDIEN_ORDNER", str(medien))
    for n in ("team.png", "buero.JPG", "fremd.png", "notiz.txt", "film.mp4", "vid-0123abcd-reel.jpg"):
        (medien / n).write_bytes(b"x")
    (db.ordner / "nl-0123abcd-held.jpg").write_bytes(b"x")
    os.utime(medien / "team.png", (1, 1))                      # aelteste Datei steht am Ende
    r = c.get("/api/pult/video/bilder?mandant=vibemind", headers=P)
    assert r.status_code == 200
    namen = [b["name"] for b in r.json()["bilder"]]
    assert set(namen) == {"team.png", "buero.JPG", "nl-0123abcd-held.jpg"}
    assert namen[-1] == "team.png"


def test_bilder_der_firma_hoechstens_200(db, c):
    for i in range(205):
        (db.ordner / f"b{i:03d}.png").write_bytes(b"x")
    r = c.get("/api/pult/video/bilder?mandant=vibemind", headers=P)
    assert len(r.json()["bilder"]) == 200


def test_material_liste(db, c, monkeypatch):
    monkeypatch.setattr(video.laura, "alle_videos", lambda: {"ok": True, "daten": [
        {"kennung": "a1b2c3d4-0000-0000-0000-000000000001", "name": "Messe", "projekt": "P", "projekt_id": "p",
         "dauer_s": 95, "aufloesung": "1920x1080"},
        {"kennung": "a1b2c3d4-0000-0000-0000-000000000002", "name": "Logo", "projekt": "P", "projekt_id": "p",
         "dauer_s": 0, "aufloesung": "512x512"},
        {"kennung": "a1b2c3d4-0000-0000-0000-000000000003", "name": "Bild", "projekt": "P", "projekt_id": "p",
         "dauer_s": None, "aufloesung": None}]})
    r = c.get("/api/pult/video/material", headers=P)
    assert r.status_code == 200
    assert r.json() == {"videos": [{"kennung": "a1b2c3d4-0000-0000-0000-000000000001", "name": "Messe",
                                    "projekt": "P", "dauer_s": 95}]}


@pytest.mark.parametrize("ausfall", ["wirft", "fail-soft"])
def test_material_laura_aus(db, c, monkeypatch, ausfall):
    def alle():
        if ausfall == "wirft":
            raise OSError("weg")
        return {"ok": False, "fehler": "Laura nicht erreichbar"}
    monkeypatch.setattr(video.laura, "alle_videos", alle)
    r = c.get("/api/pult/video/material", headers=P)
    assert r.status_code == 200
    assert r.json() == {"videos": [], "hinweis": "Laura ist gerade nicht erreichbar."}


# --- Markenstimme beim Anlegen ---------------------------------------------------------------------

MATERIAL = {"thema": "t", "sprechertext": "s"}


def _anlegen(c, briefing):
    return c.post("/api/pult/video", headers=P, json={"mandant": "vibemind", "art": "material", "titel": "T",
                                                      "briefing": briefing})


def _briefing_aus(sql: str) -> dict:
    import json
    import re
    roh = re.search(r"'(\{.*\})'::jsonb", sql).group(1).replace("''", "'")
    return json.loads(roh)


def test_anlegen_stimme_aus_marke_und_rueckfall(db, c):
    # Marke hat eine bekannte Stimme -> sie gilt, auch gegen das Briefing
    db.antworten = [[{"s": "eigene-b"}], [{"id": AID}]]
    assert _anlegen(c, {**MATERIAL, "stimme": "eigene-a"}).status_code == 200
    assert "marken_spiegel" in db.sql[-2]
    b = _briefing_aus(db.sql[-1])
    assert b["stimme"] == "eigene-b" and "stimme_hinweis" not in b
    # Markenstimme gibt es nicht mehr -> standard + Hinweis, der Auftrag scheitert nicht
    db.antworten = [[{"s": "weg"}], [{"id": AID}]]
    assert _anlegen(c, MATERIAL).status_code == 200
    b = _briefing_aus(db.sql[-1])
    assert b["stimme"] == "standard"
    assert b["stimme_hinweis"] == "Die Markenstimme 'weg' gibt es nicht mehr – Standard genommen."
    # ohne Marke: unbekannte Stimme im Briefing -> 422, nichts angelegt
    db.antworten = [[]]
    r = _anlegen(c, {**MATERIAL, "stimme": "anna"})
    assert r.status_code == 422 and r.json()["detail"] == "Diese Stimme gibt es nicht"
    assert "video_auftrag_anlegen" not in db.sql[-1]
    # ohne Marke: standard und Listenstimmen sind erlaubt
    db.antworten = [[{"s": None}], [{"id": AID}]]
    assert _anlegen(c, {**MATERIAL, "stimme": "eigene-c"}).status_code == 200
    assert _briefing_aus(db.sql[-1])["stimme"] == "eigene-c"
    db.antworten = [[], [{"id": AID}]]
    assert _anlegen(c, MATERIAL).status_code == 200
    assert _briefing_aus(db.sql[-1])["stimme"] == "standard"


def test_anlegen_stimme_liste_aus_env(db, c, monkeypatch):
    monkeypatch.setenv("MARKETING_STIMMEN", " anna ,\r\n")
    db.antworten = [[], [{"id": AID}]]
    assert _anlegen(c, {**MATERIAL, "stimme": "anna"}).status_code == 200
    db.antworten = [[]]
    assert _anlegen(c, {**MATERIAL, "stimme": "eigene-a"}).status_code == 422


# --- Vorschaubild (Arbeiter) -----------------------------------------------------------------------

def _poster_auftrag(status="in_arbeit", datei=None):
    return [{"titel": "Herbst-Reel im Laden", "mandant": "vibemind", "status": status, "datei": datei}]


def test_poster_route(db, c):
    db.antworten = [_poster_auftrag(), [{"ok": True}]]
    r = c.post(f"/api/video/arbeiter/{AID}/poster", headers=B, content=JPEG)
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    assert name == "vid-0123abcd-herbst-reel-im-laden.jpg" and (db.ordner / name).read_bytes() == JPEG
    assert "marketing.video_poster(" in db.sql[-1] and f"'{name}'" in db.sql[-1]
    assert db.psql.sql and name in db.psql.sql[-1]            # der Firma zugeordnet


def test_poster_stamm_wie_datei(db, c):
    db.antworten = [_poster_auftrag("fertig", "vid-0123abcd-erster-titel.mp4"), [{"ok": True}]]
    r = c.post(f"/api/video/arbeiter/{AID}/poster", headers=B, content=JPEG)
    assert r.status_code == 200 and r.json()["name"] == "vid-0123abcd-erster-titel.jpg"


def test_poster_formen(db, c):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    assert c.post(f"/api/video/arbeiter/{AID}/poster", headers=B, content=png).status_code == 422
    gross = JPEG + b"\x00" * video.POSTER_MAX
    assert c.post(f"/api/video/arbeiter/{AID}/poster", headers=B, content=gross).status_code == 413
    assert c.post(f"/api/video/arbeiter/{AID}/poster", content=JPEG).status_code == 401
    assert not db.sql


def test_poster_nicht_in_arbeit(db, c):
    db.antworten = [_poster_auftrag("offen")]
    assert c.post(f"/api/video/arbeiter/{AID}/poster", headers=B, content=JPEG).status_code == 409
    db.antworten = [[]]
    assert c.post(f"/api/video/arbeiter/{AID}/poster", headers=B, content=JPEG).status_code == 404


def test_poster_db_lehnt_ab(db, c):
    db.antworten = [_poster_auftrag(), [{"ok": False}]]
    assert c.post(f"/api/video/arbeiter/{AID}/poster", headers=B, content=JPEG).status_code == 409
    db.antworten = [_poster_auftrag()]
    db.fehler = [None, RuntimeError('ERROR:  new row for relation "video_auftraege" violates check constraint')]
    assert c.post(f"/api/video/arbeiter/{AID}/poster", headers=B, content=JPEG).status_code == 422


# --- Review-Fixes Task 2: Laura-Frist, Zwischenspeicher, stumme Projekte, Kappung, Markenstimme standard ----

LANGSAM = "Laura antwortet gerade zu langsam – versuch es gleich nochmal."
VIDEO = {"kennung": "a1b2c3d4-0000-0000-0000-000000000001", "name": "Messe", "projekt": "P", "projekt_id": "p",
         "dauer_s": 95, "aufloesung": "1920x1080"}


def test_material_laura_langsam_frist_und_nur_ein_lauf(db, c, monkeypatch):
    import threading
    frei, aufrufe = threading.Event(), []

    def langsam():
        aufrufe.append(1)
        frei.wait(5)
        return {"ok": True, "daten": [VIDEO]}
    monkeypatch.setattr(video, "MATERIAL_FRIST_S", 0.2)
    monkeypatch.setattr(video.laura, "alle_videos", langsam)
    try:
        start = time.monotonic()
        r = c.get("/api/pult/video/material", headers=P)
        assert time.monotonic() - start < 1.5
        assert r.status_code == 200 and r.json() == {"videos": [], "hinweis": LANGSAM}
        # zweite Anfrage waehrend des Laufs: Hinweis, KEIN zweiter Lauf
        assert c.get("/api/pult/video/material", headers=P).json()["hinweis"] == LANGSAM
        assert len(aufrufe) == 1
    finally:
        frei.set()
    video._MATERIAL["lauf"].result(timeout=2)
    # der spaete Lauf fuellt den Zwischenspeicher
    r = c.get("/api/pult/video/material", headers=P)
    assert r.json() == {"videos": [{"kennung": VIDEO["kennung"], "name": "Messe", "projekt": "P", "dauer_s": 95}]}
    assert len(aufrufe) == 1


def test_material_zwischenspeicher_60_s(db, c, monkeypatch):
    uhr, aufrufe = [1000.0], []
    monkeypatch.setattr(video, "_jetzt", lambda: uhr[0])
    monkeypatch.setattr(video.laura, "alle_videos", lambda: aufrufe.append(1) or {"ok": True, "daten": [VIDEO]})
    assert len(c.get("/api/pult/video/material", headers=P).json()["videos"]) == 1
    uhr[0] += 59
    assert len(c.get("/api/pult/video/material", headers=P).json()["videos"]) == 1
    assert len(aufrufe) == 1
    uhr[0] += 2
    c.get("/api/pult/video/material", headers=P)
    assert len(aufrufe) == 2


def test_material_alter_stand_wenn_neuer_lauf_zu_langsam(db, c, monkeypatch):
    import threading
    uhr, frei = [1000.0], threading.Event()
    monkeypatch.setattr(video, "_jetzt", lambda: uhr[0])
    monkeypatch.setattr(video, "MATERIAL_FRIST_S", 0.2)
    monkeypatch.setattr(video.laura, "alle_videos", lambda: {"ok": True, "daten": [VIDEO]})
    c.get("/api/pult/video/material", headers=P)
    uhr[0] += 120
    monkeypatch.setattr(video.laura, "alle_videos", lambda: frei.wait(5) and {"ok": True, "daten": []})
    try:
        assert len(c.get("/api/pult/video/material", headers=P).json()["videos"]) == 1
    finally:
        frei.set()
    video._MATERIAL["lauf"].result(timeout=2)       # kein Faden laeuft in den naechsten Test hinein


def test_material_fehler_wird_nicht_gespeichert(db, c, monkeypatch):
    aufrufe = []
    monkeypatch.setattr(video.laura, "alle_videos", lambda: aufrufe.append(1) or {"ok": False, "fehler": "x"})
    c.get("/api/pult/video/material", headers=P)
    c.get("/api/pult/video/material", headers=P)
    assert len(aufrufe) == 2


def test_material_stumme_projekte_hinweis(db, c, monkeypatch):
    monkeypatch.setattr(video.laura, "alle_videos", lambda: {
        "ok": True, "daten": [VIDEO], "stumme_projekte": [{"projekt": "Q", "fehler": "503"}]})
    r = c.get("/api/pult/video/material", headers=P).json()
    assert len(r["videos"]) == 1 and r["hinweis"] == "Einige Laura-Projekte antworten gerade nicht."


def test_material_namen_gekappt(db, c, monkeypatch):
    monkeypatch.setattr(video.laura, "alle_videos", lambda: {
        "ok": True, "daten": [{**VIDEO, "name": "n" * 300, "projekt": "p" * 300}]})
    v = c.get("/api/pult/video/material", headers=P).json()["videos"][0]
    assert v["name"] == "n" * 120 and v["projekt"] == "p" * 120


def test_anlegen_markenstimme_standard_ohne_hinweis(db, c):
    db.antworten = [[{"s": "standard"}], [{"id": AID}]]
    assert _anlegen(c, {**MATERIAL, "stimme": "eigene-a"}).status_code == 200
    b = _briefing_aus(db.sql[-1])
    assert b["stimme"] == "standard" and "stimme_hinweis" not in b


# --- Final-Review-Fixes: Datei fuer das Vorschaubild, Stimme beim Neubau --------------------------------

def _datei_auftrag(status="in_arbeit"):
    return [{"titel": "Herbst-Reel im Laden", "status": status}]


def test_datei_abholen_fuer_vorschaubild(db, c):
    (db.ordner / "vid-0123abcd-herbst-reel-im-laden.mp4").write_bytes(MP4)
    for status in ("in_arbeit", "fertig"):
        db.antworten = [_datei_auftrag(status)]
        r = c.get(f"/api/video/arbeiter/{AID}/datei", headers=B)
        assert r.status_code == 200, r.text
        assert r.content == MP4 and r.headers["content-type"] == "video/mp4"


def test_datei_abholen_regeln(db, c):
    # ohne/mit falschem Schluessel 401, ohne X-API-Key kein globaler Riegel (Arbeiter kennt ihn nicht)
    assert c.get(f"/api/video/arbeiter/{AID}/datei").status_code == 401
    assert c.get(f"/api/video/arbeiter/{AID}/datei", headers=P).status_code == 401
    # Datei fehlt -> 404
    db.antworten = [_datei_auftrag()]
    assert c.get(f"/api/video/arbeiter/{AID}/datei", headers=B).status_code == 404
    # falscher Status -> 409, unbekannter Auftrag -> 404, kaputte id -> 404
    (db.ordner / "vid-0123abcd-herbst-reel-im-laden.mp4").write_bytes(MP4)
    for status in ("offen", "abgebrochen", "fehler"):
        db.antworten = [_datei_auftrag(status)]
        assert c.get(f"/api/video/arbeiter/{AID}/datei", headers=B).status_code == 409
    db.antworten = [[]]
    assert c.get(f"/api/video/arbeiter/{AID}/datei", headers=B).status_code == 404
    assert c.get("/api/video/arbeiter/kein-uuid/datei", headers=B).status_code in (404, 422)


def test_datei_abholen_ohne_globalen_api_schluessel(db, c):
    (db.ordner / "vid-0123abcd-herbst-reel-im-laden.mp4").write_bytes(MP4)
    db.antworten = [_datei_auftrag()]
    r = c.get(f"/api/video/arbeiter/{AID}/datei", headers=B)          # kein X-API-Key
    assert r.status_code == 200


def _nochmal_stimme(db, c, marke):
    db.antworten = [[{"mandant": "vibemind"}], [{"s": marke}] if marke is not ... else [], [{"id": NEU}]]
    r = c.post(f"/api/pult/video/{AID}/nochmal", headers=P, json={"wunsch": "kürzer", "mandant": "vibemind"})
    assert r.status_code == 200, r.text
    assert "marken_spiegel" in db.sql[-2]
    return db.sql[-1]


def test_nochmal_nimmt_aktuelle_markenstimme(db, c, monkeypatch):
    monkeypatch.setenv("MARKETING_STIMMEN", "anna")
    sql = _nochmal_stimme(db, c, "anna")
    assert sql.endswith("'betreiber', 'anna', NULL) AS id")
    sql = _nochmal_stimme(db, c, "standard")
    assert sql.endswith("'betreiber', 'standard', NULL) AS id")
    # Markenstimme gibt es nicht mehr -> standard + Hinweis
    sql = _nochmal_stimme(db, c, "weg")
    assert ("'betreiber', 'standard', 'Die Markenstimme ''weg'' gibt es nicht mehr – Standard genommen.') AS id"
            in sql)
    # keine Markenstimme -> NULL: die Stimme des alten Briefings bleibt
    sql = _nochmal_stimme(db, c, None)
    assert sql.endswith("'betreiber', NULL, NULL) AS id")
    sql = _nochmal_stimme(db, c, ...)
    assert sql.endswith("'betreiber', NULL, NULL) AS id")


# --- Reels Stufe 2: Formate (22.10.2026) ---------------------------------------------------------

@pytest.mark.parametrize("art, basis", [("bilder", BILDER), ("material", MATERIAL),
                                        ("kurzfassung", {"thema": "t", "video": "a1b2c3d4e5f6"})])
def test_briefing_format_wird_geprueft(art, basis):
    assert video.briefing_pruefen(art, {**basis, "format": "quadrat"})["format"] == "quadrat"
    assert video.briefing_pruefen(art, {**basis, "format": "quer"})["format"] == "quer"
    assert "format" not in video.briefing_pruefen(art, {**basis, "format": "reel"})
    assert "format" not in video.briefing_pruefen(art, {**basis, "format": None})
    assert "format" not in video.briefing_pruefen(art, {**basis, "format": ""})
    for unsinn in ("9:16", "<x>", "Quadrat", 1, True, False, 0, [], {}, ["quer"], {"quer": 1}):
        with pytest.raises(Exception) as e:
            video.briefing_pruefen(art, {**basis, "format": unsinn})
        assert e.value.status_code == 422 and e.value.detail == "format: reel, quadrat oder quer"


def test_briefing_ohne_format_bleibt_ohne_format():
    """Review Focus 1: Ein heutiges Briefing (ohne format) wird genau so gespeichert wie vorher."""
    assert video.briefing_pruefen("bilder", BILDER) == {
        "thema": "bAV in 30 Sekunden", "sprechertext": "Drei Fehler, die fast jede Firma macht.", "hook": "",
        "dauer_s": 30, "stimme": "standard", "bilder": ["team.png", "buero.jpg"],
        "logo": "logo-vibemind-0123456789.png", "farben": {"hintergrund": "#0f2422", "text": "#e9fbf6"}}


def test_format_ungueltig_422(db, c):
    r = _anlegen(c, {**MATERIAL, "format": "9:16"})
    assert r.status_code == 422 and r.json()["detail"] == "format: reel, quadrat oder quer"
    assert not any("video_auftrag_anlegen" in s for s in db.sql)


def test_anlegen_speichert_format(db, c):
    db.antworten = [[], [{"id": AID}]]
    assert _anlegen(c, {**MATERIAL, "format": "quer"}).status_code == 200
    assert _briefing_aus(db.sql[-1])["format"] == "quer"
    db.antworten = [[], [{"id": AID}]]
    assert _anlegen(c, {**MATERIAL, "format": "reel"}).status_code == 200
    assert "format" not in _briefing_aus(db.sql[-1])


def test_nochmal_behaelt_format(db, c):
    """Review Focus 4: video_nochmal kopiert das Briefing in der DB (085: nur stimme_hinweis faellt weg);
    die API reicht kein eigenes Briefing hinein, und die Liste zeigt das Format unveraendert."""
    from pathlib import Path
    sql_085 = (Path(video.__file__).parents[1] / "db" / "085_reels_galerie.sql").read_text(encoding="utf-8")
    assert "b := a.briefing - 'stimme_hinweis';" in sql_085
    db.antworten = [[{"mandant": "vibemind"}], [], [{"id": NEU}]]
    r = c.post(f"/api/pult/video/{AID}/nochmal", headers=P, json={"wunsch": "kürzer", "mandant": "vibemind"})
    assert r.status_code == 200
    assert "marketing.video_nochmal(" in db.sql[-1] and "briefing" not in db.sql[-1] and "format" not in db.sql[-1]
    db.antworten = [[_zeile(id=NEU, vorgaenger=AID, briefing={"thema": "t", "format": "quadrat"})]]
    zeile = c.get("/api/pult/video?mandant=vibemind", headers=P).json()["auftraege"][0]
    assert zeile["briefing"]["format"] == "quadrat" and zeile["vorgaenger"] == AID


@pytest.mark.parametrize("briefing, erwartet", [({}, "reel"), ({"format": "quadrat"}, "quadrat"),
                                                ({"format": "quer"}, "quer"), ({"format": "9:16"}, "reel"),
                                                ('{"format": "quer"}', "quer"), (None, "reel")])
def test_marke_nennt_format_des_auftrags(db, c, monkeypatch, briefing, erwartet):
    monkeypatch.setattr(video, "_markenlogos", lambda m: (None, None))
    auftrag = _in_arbeit(db)
    auftrag[0]["briefing"] = briefing
    db.antworten = [auftrag, []]
    r = c.post(f"/api/video/arbeiter/{AID}/marke", headers=B)
    assert r.status_code == 200 and r.json()["format"] == erwartet
