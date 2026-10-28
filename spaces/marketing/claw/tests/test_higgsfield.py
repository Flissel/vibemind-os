"""Higgsfield-Client (vorbereitet 28.10.2026): ohne Netz, mit nachgebauten Antworten."""
import io
import json

import pytest

from spaces.marketing.claw import higgsfield as hf


class Antwort(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def eingerichtet(monkeypatch):
    monkeypatch.setenv("HIGGSFIELD_API_KEY_ID", "kid")
    monkeypatch.setenv("HIGGSFIELD_API_KEY_SECRET", "geheim")
    monkeypatch.setenv("HIGGSFIELD_MODELL_PFAD", "kling/image-to-video/v1")


def test_aus_ohne_schluessel(monkeypatch):
    for k in ("HIGGSFIELD_API_KEY_ID", "HIGGSFIELD_API_KEY_SECRET", "HIGGSFIELD_MODELL_PFAD"):
        monkeypatch.delenv(k, raising=False)
    assert hf.bereit() is False
    with pytest.raises(hf.HiggsfieldFehler, match="nicht eingerichtet|MODELL_PFAD"):
        hf.bild_zu_video("https://x/b.png", "Kamera faehrt langsam heran")


def test_auftrag_warten_und_video_url(eingerichtet):
    gesehen = []
    antworten = [
        {"status": "queued", "request_id": "r1",
         "status_url": "https://api.higgsfield.ai/requests/r1/status"},
        {"status": "in_progress"},
        {"status": "completed", "video": {"url": "https://cdn.example/v.mp4"}},
    ]

    def oeffnen(req, timeout):
        gesehen.append((req.get_method(), req.full_url, req.get_header("Authorization"),
                        json.loads(req.data) if req.data else None))
        return Antwort(json.dumps(antworten.pop(0)).encode())

    url = hf.bild_zu_video("https://x/b.png", "Zoom", {"duration": 5}, oeffnen=oeffnen,
                           schlafen=lambda s: None)
    assert url == "https://cdn.example/v.mp4"
    assert gesehen[0][:3] == ("POST", "https://api.higgsfield.ai/kling/image-to-video/v1", "Key kid:geheim")
    assert gesehen[0][3] == {"image_url": "https://x/b.png", "prompt": "Zoom", "duration": 5}
    assert [g[0] for g in gesehen[1:]] == ["GET", "GET"]


def test_abgelehnt_und_fremde_adresse(eingerichtet):
    folge = [{"status": "queued", "status_url": "https://api.higgsfield.ai/requests/r2/status"},
             {"status": "nsfw"}]
    with pytest.raises(hf.HiggsfieldFehler, match="nsfw"):
        hf.bild_zu_video("https://x/b.png", "x",
                         oeffnen=lambda r, timeout: Antwort(json.dumps(folge.pop(0)).encode()),
                         schlafen=lambda s: None)
    with pytest.raises(hf.HiggsfieldFehler, match="fremde Adresse"):
        hf.warten("https://boese.example/status", oeffnen=lambda r, timeout: None)
