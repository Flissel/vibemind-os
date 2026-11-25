"""Marketing, Stufe B Tag 4: eigene Medienablage je Firma (api/medienablage.py) und Abspielen im Reiter
(oberflaeche/medien.py, Reels und Podcast). Ohne Datenbank, mit Ordnern in tmp_path; alle Firmen, Titel und
Dateien sind erfunden. Versendet wird nichts."""
from __future__ import annotations

import html
import os
import re
import time
import urllib.parse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from spaces.marketing.api import medienablage, oberflaeche
from spaces.marketing.api.oberflaeche import daten, huelle, medien

KEY = "test-ui-schluessel"
H = {"Authorization": f"Bearer {KEY}"}
FIRMEN = [{"id": "alpha", "name": "Alpha GmbH", "aktiv": True}, {"id": "beta", "name": "Beta AG", "aktiv": True}]
MP4 = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 8
MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 400
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
REEL = "vid-0a1b2c3d-fruehling.mp4"
POSTER = "vid-0a1b2c3d-fruehling.jpg"
FOLGE = "pod-1a2b3c4d-erste-folge.mp3"


@pytest.fixture
def ordner(tmp_path, monkeypatch):
    laeden, eigen = tmp_path / "laeden", tmp_path / "eigen"
    laeden.mkdir()
    eigen.mkdir()
    monkeypatch.setenv(medienablage.LAEDEN_ENV, str(laeden))
    monkeypatch.setenv(medienablage.ENV, str(eigen))
    return laeden, eigen


class Attrappe:
    def __init__(self):
        self.reels = [{"id": "r1", "titel": "Frühling", "status": "fertig", "art": "bilder", "datei": REEL,
                       "poster": POSTER, "briefing": {"format": "quadrat"}, "dauer_s": 12},
                      {"id": "r2", "titel": "Im Bau", "status": "in_arbeit", "art": "bilder", "datei": None},
                      {"id": "r3", "titel": "Kaputter Name", "status": "fertig", "art": "bilder", "datei": "../x.mp4"}]
        self.folgen = [{"id": "p1", "titel": "Erste Folge", "status": "fertig", "datei": FOLGE, "dauer_s": 95},
                       {"id": "p2", "titel": "Wartet", "status": "offen", "datei": None}]

    def einsetzen(self, monkeypatch):
        monkeypatch.setattr(daten, "firmen", lambda: FIRMEN)
        monkeypatch.setattr(daten, "reels", lambda m, archiv: [] if archiv else self.reels)
        monkeypatch.setattr(daten, "podcast", lambda m: self.folgen)


@pytest.fixture
def a(monkeypatch):
    monkeypatch.setenv(huelle.SCHLUESSEL_ENV, KEY)
    attrappe = Attrappe()
    attrappe.einsetzen(monkeypatch)
    return attrappe


@pytest.fixture
def c():
    app = FastAPI()
    app.include_router(oberflaeche.router)
    return TestClient(app)


# --- Medienablage -------------------------------------------------------------------------------

@pytest.mark.parametrize("name, gut", [(REEL, True), (POSTER, True), (FOLGE, True),
                                       ("vid-0a1b2c3d-x.mp3", False), ("../vid-0a1b2c3d-x.mp4", False),
                                       ("logo-alpha-0123456789.png", False), ("pod-1a2b3c4d-X.mp3", False),
                                       ("vid-0a1b2c3d-fruehling.mp4/..", False), (None, False)])
def test_namen(name, gut):
    assert medienablage.name_ok(name) is gut


def test_finden_eigene_ablage_vor_der_der_laeden(ordner):
    laeden, eigen = ordner
    (laeden / REEL).write_bytes(b"alt")
    assert medienablage.finden("alpha", REEL) == str(laeden / REEL)
    (eigen / "alpha").mkdir()
    (eigen / "alpha" / REEL).write_bytes(b"neu")
    assert medienablage.finden("alpha", REEL) == str(eigen / "alpha" / REEL)
    assert medienablage.finden("alpha", "../" + REEL) is None and medienablage.finden("Alpha", REEL) is None
    assert medienablage.finden("beta", FOLGE) is None


def test_finden_folgt_keinem_link(ordner, tmp_path):
    laeden, _ = ordner
    geheim = tmp_path / "geheim.mp4"
    geheim.write_bytes(b"x")
    try:
        os.symlink(geheim, laeden / REEL)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks hier nicht erlaubt")
    assert medienablage.finden("alpha", REEL) is None


def test_ablegen_in_den_firmenordner(ordner):
    _, eigen = ordner
    assert medienablage.ablegen("alpha", REEL, MP4) is True
    assert (eigen / "alpha" / REEL).read_bytes() == MP4
    assert not list((eigen / "alpha").glob("*.teil"))
    assert medienablage.ablegen("alpha", "../boese.mp4", MP4) is False
    assert medienablage.ablegen("../x", REEL, MP4) is False


def test_ablegen_ohne_ablage_tut_nichts(monkeypatch, tmp_path):
    monkeypatch.delenv(medienablage.ENV, raising=False)
    assert medienablage.ablegen("alpha", REEL, MP4) is False
    monkeypatch.setenv(medienablage.ENV, str(tmp_path / "gibt-es-nicht"))
    assert medienablage.ablegen("alpha", REEL, MP4) is False and not (tmp_path / "gibt-es-nicht").exists()


def test_uebernehmen_probe_und_ausfuehren(ordner):
    laeden, eigen = ordner
    (laeden / REEL).write_bytes(MP4)
    (laeden / FOLGE).write_bytes(MP3)
    paare = [("alpha", REEL), ("beta", FOLGE), ("alpha", POSTER), ("alpha", "../x"), ("alpha", REEL)]
    assert medienablage.uebernehmen(paare) == {"kopiert": 2, "schon_da": 0, "fehlt": 1, "ungueltig": 1}
    assert not (eigen / "alpha").exists()                     # Probe kopiert nichts
    assert medienablage.uebernehmen(paare, ausfuehren=True)["kopiert"] == 2
    assert (eigen / "alpha" / REEL).read_bytes() == MP4 and (eigen / "beta" / FOLGE).read_bytes() == MP3
    assert medienablage.uebernehmen(paare, ausfuehren=True)["schon_da"] == 2


def test_uebernehmen_braucht_beide_ordner(monkeypatch, tmp_path):
    monkeypatch.delenv(medienablage.ENV, raising=False)
    monkeypatch.setenv(medienablage.LAEDEN_ENV, str(tmp_path))
    with pytest.raises(RuntimeError):
        medienablage.uebernehmen([("alpha", REEL)])


def test_main_zaehlt(ordner, monkeypatch, capsys):
    laeden, _ = ordner
    (laeden / REEL).write_bytes(MP4)
    monkeypatch.setattr(medienablage, "paare_aus_db", lambda: [("alpha", REEL)])
    assert medienablage.main([]) == 0
    assert "Probe (nichts kopiert): kopiert 1" in capsys.readouterr().out


# --- Arbeiter legen zusaetzlich ab --------------------------------------------------------------

def test_video_arbeiter_legt_zusaetzlich_ab(ordner, monkeypatch):
    from spaces.marketing.api import medien_mandant, video
    laeden, eigen = ordner
    aid = "0a1b2c3d-0000-4000-8000-000000000000"
    monkeypatch.setattr(video, "_in_arbeit", lambda a: {"mandant": "alpha", "titel": "Frühling"})
    monkeypatch.setattr(medien_mandant, "zuordnen", lambda namen, m: len(namen))
    ergebnis = video._speichern(aid, MP4)
    assert ergebnis["name"] == REEL
    assert (laeden / REEL).read_bytes() == MP4 and (eigen / "alpha" / REEL).read_bytes() == MP4


def test_video_arbeiter_ohne_eigene_ablage_wie_bisher(ordner, monkeypatch):
    from spaces.marketing.api import medien_mandant, video
    laeden, eigen = ordner
    monkeypatch.delenv(medienablage.ENV)
    monkeypatch.setattr(video, "_in_arbeit", lambda a: {"mandant": "alpha", "titel": "Frühling"})
    monkeypatch.setattr(medien_mandant, "zuordnen", lambda namen, m: len(namen))
    video._speichern("0a1b2c3d-0000-4000-8000-000000000000", MP4)
    assert (laeden / REEL).exists() and not (eigen / "alpha").exists()


# --- Signierte Adressen -------------------------------------------------------------------------

def test_adresse_und_signatur(monkeypatch):
    monkeypatch.setenv(huelle.SCHLUESSEL_ENV, KEY)
    jetzt = 1_800_000_000
    adr = medien.adresse("alpha", REEL, jetzt)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(adr).query))
    assert adr.startswith(f"/oberflaeche/medien/{REEL}?") and q["firma"] == "alpha"
    assert medien.signatur_ok("alpha", REEL, q["bis"], q["sig"], jetzt)
    assert not medien.signatur_ok("beta", REEL, q["bis"], q["sig"], jetzt)
    assert not medien.signatur_ok("alpha", POSTER, q["bis"], q["sig"], jetzt)
    assert not medien.signatur_ok("alpha", REEL, q["bis"], q["sig"], jetzt + medien.GUELTIG_S + 1)
    assert medien.adresse("alpha", "../x.mp4") is None
    monkeypatch.delenv(huelle.SCHLUESSEL_ENV)
    assert medien.adresse("alpha", REEL) is None


def _abrufen(c, adr: str, **mehr):
    return c.get(html.unescape(adr), **mehr)                   # ohne Bearer, wie <video>


def test_abrufen_spielt_und_spult(c, a, ordner):
    laeden, _ = ordner
    (laeden / REEL).write_bytes(MP4)
    adr = medien.adresse("alpha", REEL)
    r = _abrufen(c, adr)
    assert r.status_code == 200 and r.content == MP4 and r.headers["content-type"] == "video/mp4"
    assert r.headers["cache-control"] == "private, no-store" and "sandbox" in r.headers["content-security-policy"]
    r = _abrufen(c, adr, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.content == MP4[:100]


def test_abrufen_abgewiesen(c, a, ordner):
    laeden, _ = ordner
    (laeden / REEL).write_bytes(MP4)
    adr = medien.adresse("alpha", REEL)
    assert _abrufen(c, adr.replace("sig=", "sig=0")).status_code == 403
    assert _abrufen(c, adr.replace("firma=alpha", "firma=beta")).status_code == 403
    bis = re.search(r"bis=(\d+)", adr).group(1)
    assert _abrufen(c, adr.replace(f"bis={bis}", f"bis={int(bis) + 10}")).status_code == 403
    alt = medien.adresse("alpha", REEL, time.time() - medien.GUELTIG_S - 10)
    assert _abrufen(c, alt).status_code == 403
    assert c.get(f"/oberflaeche/medien/{REEL}").status_code == 404          # ohne firma
    assert c.get(f"/oberflaeche/medien/{REEL}?firma=alpha&bis=1&sig=x").status_code == 403
    assert c.get("/oberflaeche/medien/geheim.txt?firma=alpha&bis=1&sig=x").status_code == 404
    weg = medien.adresse("alpha", FOLGE)
    assert _abrufen(c, weg).status_code == 404                # gibt es nirgends


# --- Seiten -------------------------------------------------------------------------------------

def test_reels_spielen_in_der_karte(c, a, ordner):
    r = c.get("/oberflaeche/reels", headers=H)
    assert r.status_code == 200 and "<script" not in r.text
    assert "media-src 'self'" in r.headers["content-security-policy"]
    videos = re.findall(r"<video [^>]*>", r.text)
    assert len(videos) == 1                                    # nur das fertige mit gueltigem Namen
    assert 'class="quadrat"' in videos[0] and 'preload="none"' in videos[0] and "controls" in videos[0]
    src = html.unescape(re.search(r' src="([^"]+)"', videos[0]).group(1))
    poster = html.unescape(re.search(r' poster="([^"]+)"', videos[0]).group(1))
    assert src.startswith(f"/oberflaeche/medien/{REEL}?firma=alpha&") and POSTER in poster
    assert "Fertige Reels spielen direkt hier." in r.text and "Versendet wird hier nichts." in r.text


def test_reels_fuer_gewaehlte_firma_signiert(c, a, ordner):
    c.cookies.set(huelle.COOKIE_FIRMA, "beta")
    r = c.get("/oberflaeche/reels", headers=H)
    assert "firma=beta" in r.text and "firma=alpha" not in r.text


def test_reel_ohne_poster_und_hochformat(c, a, ordner):
    a.reels = [{"id": "r1", "titel": "Hoch", "status": "fertig", "datei": REEL, "poster": None, "briefing": {}}]
    r = c.get("/oberflaeche/reels", headers=H)
    video = re.search(r"<video [^>]*>", r.text).group(0)
    assert "poster=" not in video and "class=" not in video


def test_podcast_spielt_in_der_karte(c, a, ordner):
    r = c.get("/oberflaeche/podcast", headers={**H, "Accept-Language": "en"})
    audios = re.findall(r"<audio [^>]*>", r.text)
    assert len(audios) == 1 and FOLGE in audios[0] and 'aria-label="Erste Folge"' in audios[0]
    assert "Finished episodes play right here." in r.text


def test_server_haengt_die_medien_ein():
    from spaces.marketing.api import server
    assert "/oberflaeche/medien/{name}" in {getattr(r, "path", "") for r in server.app.routes}
