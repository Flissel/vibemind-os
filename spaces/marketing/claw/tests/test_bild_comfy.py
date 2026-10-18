"""ComfyUI-Client ohne echten Dienst: ein gefaelschter HTTP-Weg."""
import json

import pytest

from spaces.marketing.claw import bild_comfy


class FalschesHttp:
    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.anfragen = []

    def __call__(self, methode, pfad, daten=None, zeitlimit=30):
        self.anfragen.append((methode, pfad, daten))
        return self.antworten.pop(0)


PNG = b"\x89PNG\r\n\x1a\nrest"


def test_erzeugen_setzt_prompt_masse_seed_und_liefert_png(monkeypatch):
    http = FalschesHttp([
        (200, json.dumps({"prompt_id": "p1"}).encode()),
        (200, json.dumps({}).encode()),                                   # noch nicht fertig
        (200, json.dumps({"p1": {"outputs": {"9": {"images": [
            {"filename": "a.png", "subfolder": "", "type": "output"}]}}}}).encode()),
        (200, PNG),
    ])
    monkeypatch.setattr(bild_comfy, "_http", http)
    monkeypatch.setattr(bild_comfy, "_SCHLAF", lambda s: None)
    assert bild_comfy.erzeugen("a teal network", 1104, 560, 42) == PNG
    ablauf = http.anfragen[0][2]["prompt"]
    assert ablauf["4"]["inputs"]["text"] == "a teal network"
    assert (ablauf["6"]["inputs"]["width"], ablauf["6"]["inputs"]["height"]) == (1104, 560)
    assert ablauf["7"]["inputs"]["seed"] == 42
    assert http.anfragen[3][1].startswith("/view?filename=a.png")


def test_masse_muessen_vielfache_von_16_sein():
    with pytest.raises(bild_comfy.ComfyFehler):
        bild_comfy.erzeugen("x", 1000, 512, 1)


def test_fehler_in_der_ausfuehrung_wird_comfyfehler(monkeypatch):
    http = FalschesHttp([
        (200, json.dumps({"prompt_id": "p1"}).encode()),
        (200, json.dumps({"p1": {"status": {"status_str": "error"}, "outputs": {}}}).encode()),
    ])
    monkeypatch.setattr(bild_comfy, "_http", http)
    monkeypatch.setattr(bild_comfy, "_SCHLAF", lambda s: None)
    with pytest.raises(bild_comfy.ComfyFehler, match="fehlgeschlagen"):
        bild_comfy.erzeugen("x", 512, 512, 1)


def test_zeitlimit(monkeypatch):
    http = FalschesHttp([(200, b'{"prompt_id": "p1"}')] + [(200, b"{}")] * 50)
    monkeypatch.setattr(bild_comfy, "_http", http)
    uhr = [0.0]
    monkeypatch.setattr(bild_comfy, "_UHR", lambda: uhr[0])
    monkeypatch.setattr(bild_comfy, "_SCHLAF", lambda s: uhr.__setitem__(0, uhr[0] + s))
    with pytest.raises(bild_comfy.ComfyFehler, match="Zeitlimit"):
        bild_comfy.erzeugen("x", 512, 512, 1, zeitlimit_s=4)


def test_freigeben_und_laeuft(monkeypatch):
    http = FalschesHttp([(200, b""), (200, b"{}")])
    monkeypatch.setattr(bild_comfy, "_http", http)
    bild_comfy.freigeben()
    assert http.anfragen[0][:2] == ("POST", "/free")
    assert http.anfragen[0][2] == {"unload_models": True, "free_memory": True}
    assert bild_comfy.laeuft() is True
    monkeypatch.setattr(bild_comfy, "_http", lambda *a, **k: (_ for _ in ()).throw(OSError("weg")))
    assert bild_comfy.laeuft() is False


def test_ueberarbeiten_laedt_hoch_und_setzt_denoise(monkeypatch):
    hochgeladen = []
    monkeypatch.setattr(bild_comfy, "_hochladen", lambda name, daten: hochgeladen.append((name, daten)) or name)
    http = FalschesHttp([
        (200, json.dumps({"prompt_id": "p2"}).encode()),
        (200, json.dumps({"p2": {"outputs": {"9": {"images": [{"filename": "b.png", "subfolder": "", "type": "output"}]}}}}).encode()),
        (200, PNG),
    ])
    monkeypatch.setattr(bild_comfy, "_http", http)
    monkeypatch.setattr(bild_comfy, "_SCHLAF", lambda s: None)
    assert bild_comfy.ueberarbeiten("warm skyline", b"\xff\xd8\xffJ", 1040, 592, 9, 55) == PNG
    ablauf = http.anfragen[0][2]["prompt"]
    assert ablauf["10"]["inputs"]["image"] == hochgeladen[0][0] and hochgeladen[0][1] == b"\xff\xd8\xffJ"
    assert (ablauf["11"]["inputs"]["width"], ablauf["11"]["inputs"]["height"]) == (1040, 592)
    assert ablauf["7"]["inputs"]["denoise"] == 0.55 and ablauf["7"]["inputs"]["steps"] == 8
    assert ablauf["7"]["inputs"]["seed"] == 9 and ablauf["4"]["inputs"]["text"] == "warm skyline"


def test_ueberarbeiten_denoise_grenzen(monkeypatch):
    monkeypatch.setattr(bild_comfy, "_hochladen", lambda name, daten: name)
    gesehen = []
    monkeypatch.setattr(bild_comfy, "_ausfuehren", lambda ablauf, z: gesehen.append(ablauf["7"]["inputs"]["denoise"]) or PNG)
    bild_comfy.ueberarbeiten("x", b"j", 512, 512, 1, 0)
    bild_comfy.ueberarbeiten("x", b"j", 512, 512, 1, 100)
    assert gesehen == [bild_comfy.MIN_DENOISE, 1.0]
    with pytest.raises(bild_comfy.ComfyFehler):
        bild_comfy.ueberarbeiten("x", b"j", 500, 512, 1, 50)
    with pytest.raises(bild_comfy.ComfyFehler):
        bild_comfy.ueberarbeiten("x", b"", 512, 512, 1, 50)


class Uhr:
    """Falsche Uhr: Schlafen schiebt sie vor, nichts wartet wirklich."""
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def schlafen(self, s):
        self.t += s


FERTIG = json.dumps({"p1": {"outputs": {"9": {"images": [
    {"filename": "a.png", "subfolder": "", "type": "output"}]}}}}).encode()


def _uhr_einhaengen(monkeypatch):
    uhr = Uhr()
    monkeypatch.setattr(bild_comfy, "_UHR", uhr)
    monkeypatch.setattr(bild_comfy, "_SCHLAF", uhr.schlafen)
    return uhr


def test_einzelner_poll_timeout_bricht_den_auftrag_nicht_ab(monkeypatch):
    """09.10.: ComfyUI lud kalt 400 s, ein History-Poll lief in TimeoutError, der Auftrag
    starb, obwohl der Prompt fertig wurde. Poll-Fehler -> weiter pollen bis zum Zeitlimit."""
    _uhr_einhaengen(monkeypatch)
    antworten = [(200, b'{"prompt_id": "p1"}')] + [TimeoutError("timed out")] * 7 + [
        ConnectionResetError("reset"), (200, b"{}"), (200, FERTIG), (200, PNG)]
    anfragen = []

    def http(methode, pfad, daten=None, zeitlimit=30):
        anfragen.append(pfad)
        a = antworten.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(bild_comfy, "_http", http)
    assert bild_comfy.erzeugen("x", 512, 512, 1, zeitlimit_s=540) == PNG
    assert anfragen[0] == "/prompt" and len(anfragen) == 12


def test_poll_timeouts_ohne_ende_scheitern_am_gesamtzeitlimit(monkeypatch):
    uhr = _uhr_einhaengen(monkeypatch)

    def http(methode, pfad, daten=None, zeitlimit=30):
        if pfad == "/prompt":
            return 200, b'{"prompt_id": "p1"}'
        raise TimeoutError("timed out")
    monkeypatch.setattr(bild_comfy, "_http", http)
    with pytest.raises(bild_comfy.ComfyFehler, match="Zeitlimit 540 s"):
        bild_comfy.erzeugen("x", 512, 512, 1, zeitlimit_s=540)
    assert 540 <= uhr.t < 540 + 3 * bild_comfy.POLL_PAUSE_MAX_S


def test_submit_timeout_ohne_comfy_ist_voruebergehend(monkeypatch):
    """Schluss-Fix I2: ComfyUI antwortet gar nicht -> wiederholbarer ComfyVoruebergehend, kein roher TimeoutError,
    und POST /prompt genau einmal (kein zweiter GPU-Auftrag)."""
    _uhr_einhaengen(monkeypatch)
    posts = []

    def http(methode, pfad, daten=None, zeitlimit=30):
        if pfad == "/prompt":
            posts.append(daten)
        raise TimeoutError("timed out")
    monkeypatch.setattr(bild_comfy, "_http", http)
    with pytest.raises(bild_comfy.ComfyVoruebergehend):
        bild_comfy.erzeugen("x", 512, 512, 1)
    assert len(posts) == 1


def test_prompt_id_vom_client_kanonische_uuid(monkeypatch):
    import uuid
    _uhr_einhaengen(monkeypatch)
    gesehen = []

    def http(methode, pfad, daten=None, zeitlimit=30):
        gesehen.append((pfad, daten))
        if pfad == "/prompt":
            return 200, json.dumps({"prompt_id": daten["prompt_id"], "number": 1}).encode()
        if pfad.startswith("/history/"):
            pid = pfad.rsplit("/", 1)[1]
            return 200, json.dumps({pid: {"outputs": {"9": {"images": [{"filename": "a.png"}]}}}}).encode()
        return 200, PNG
    monkeypatch.setattr(bild_comfy, "_http", http)
    assert bild_comfy.erzeugen("x", 512, 512, 1) == PNG
    pid = gesehen[0][1]["prompt_id"]
    assert str(uuid.UUID(pid)) == pid                     # ComfyUI verlangt kleingeschrieben mit Bindestrichen
    assert gesehen[1][0] == f"/history/{pid}"


def test_submit_timeout_dann_fertig_ohne_neuen_auftrag(monkeypatch):
    """POST /prompt laeuft in TimeoutError, ComfyUI hat den Auftrag aber angenommen: weiter pollen, nicht neu senden."""
    _uhr_einhaengen(monkeypatch)
    posts, gesendet = [], {}

    def http(methode, pfad, daten=None, zeitlimit=30):
        if pfad == "/prompt":
            posts.append(daten)
            gesendet["pid"] = daten["prompt_id"]
            raise TimeoutError("timed out")
        if pfad.startswith("/history/"):
            if len([1 for p in http.pfade if p.startswith("/history/")]) < 3:
                http.pfade.append(pfad)
                return 200, b"{}"
            return 200, json.dumps({gesendet["pid"]: {"outputs": {"9": {"images": [
                {"filename": "a.png"}]}}}}).encode()
        if pfad == "/queue":
            return 200, json.dumps({"queue_running": [[1, gesendet["pid"], {}, {}, []]],
                                    "queue_pending": []}).encode()
        return 200, PNG
    http.pfade = []
    monkeypatch.setattr(bild_comfy, "_http", http)
    assert bild_comfy.erzeugen("x", 512, 512, 1, zeitlimit_s=1200) == PNG
    assert len(posts) == 1


def test_submit_timeout_nicht_in_warteschlange_voruebergehend(monkeypatch):
    """Nach der Gnadenfrist: weder History noch /queue kennen die id -> ComfyVoruebergehend, lange vor dem Zeitlimit."""
    uhr = _uhr_einhaengen(monkeypatch)
    posts = []

    def http(methode, pfad, daten=None, zeitlimit=30):
        if pfad == "/prompt":
            posts.append(daten)
            raise TimeoutError("timed out")
        if pfad == "/queue":
            return 200, b'{"queue_running": [], "queue_pending": []}'
        return 200, b"{}"
    monkeypatch.setattr(bild_comfy, "_http", http)
    with pytest.raises(bild_comfy.ComfyVoruebergehend, match="antwortet nicht"):
        bild_comfy.erzeugen("x", 512, 512, 1, zeitlimit_s=1200)
    assert len(posts) == 1 and bild_comfy.GNADE_S <= uhr.t < 1200


def test_zeitlimit_ist_voruebergehend(monkeypatch):
    http = FalschesHttp([(200, b'{"prompt_id": "p1"}')] + [(200, b"{}")] * 50)
    monkeypatch.setattr(bild_comfy, "_http", http)
    _uhr_einhaengen(monkeypatch)
    with pytest.raises(bild_comfy.ComfyVoruebergehend, match="Zeitlimit 4 s"):
        bild_comfy.erzeugen("x", 512, 512, 1, zeitlimit_s=4)


def test_http_fehler_ohne_lesbaren_rumpf(monkeypatch):
    import urllib.error

    class Kaputt:
        def read(self, *a):
            raise OSError("weg")

        def close(self):
            pass

    def http(methode, pfad, daten=None, zeitlimit=30):
        raise urllib.error.HTTPError("u", 400, "Bad", {}, Kaputt())
    monkeypatch.setattr(bild_comfy, "_http", http)
    with pytest.raises(bild_comfy.ComfyFehler, match="lehnt den Auftrag ab"):
        bild_comfy.erzeugen("x", 512, 512, 1)


def test_hochladen_timeout_voruebergehend_http_fehler_nicht(monkeypatch):
    import io
    import urllib.error

    def langsam(req, timeout=None):
        raise TimeoutError("timed out")
    monkeypatch.setattr(bild_comfy.urllib.request, "urlopen", langsam)
    with pytest.raises(bild_comfy.ComfyVoruebergehend, match="Zeitueberschreitung"):
        bild_comfy._hochladen("a.png", b"x")
    with pytest.raises(bild_comfy.ComfyVoruebergehend):
        bild_comfy.freistellen(b"quelle")

    def abgelehnt(req, timeout=None):
        raise urllib.error.HTTPError("u", 413, "zu gross", {}, io.BytesIO(b""))
    monkeypatch.setattr(bild_comfy.urllib.request, "urlopen", abgelehnt)
    with pytest.raises(bild_comfy.ComfyFehler) as e:
        bild_comfy._hochladen("a.png", b"x")
    assert not isinstance(e.value, bild_comfy.ComfyVoruebergehend) and "413" in str(e.value)


# --- FLUX.2 klein mit Referenzbildern ---------------------------------------

def test_graph_flux2_ohne_referenzen_setzt_prompt_masse_seed():
    g = bild_comfy.graph_flux2("a red barn", 768, 512, 42, [])
    assert g["4"]["inputs"]["text"] == "a red barn"
    assert (g["6"]["inputs"]["width"], g["6"]["inputs"]["height"]) == (768, 512)
    assert (g["7"]["inputs"]["width"], g["7"]["inputs"]["height"]) == (768, 512)
    assert g["8"]["inputs"]["noise_seed"] == 42
    assert g["5"]["inputs"]["conditioning"] == ["4", 0]
    assert not [k for k in g if k.startswith(("r", "c"))]


def test_graph_flux2_haengt_referenzen_als_kette_an():
    g = bild_comfy.graph_flux2("p", 512, 512, 1, ["a.png", "b.png"])
    assert g["r1"]["inputs"]["image"] == "a.png" and g["r2"]["inputs"]["image"] == "b.png"
    assert g["s1"]["inputs"] == {"image": ["r1", 0], "upscale_method": "lanczos", "megapixels": 1.0, "resolution_steps": 1}
    assert g["e1"]["inputs"] == {"pixels": ["s1", 0], "vae": ["3", 0]}
    assert g["c1"]["inputs"] == {"conditioning": ["4", 0], "latent": ["e1", 0]}
    assert g["c2"]["inputs"] == {"conditioning": ["c1", 0], "latent": ["e2", 0]}
    assert g["5"]["inputs"]["conditioning"] == ["c2", 0]
    assert g["c1"]["class_type"] == "ReferenceLatent" and g["r1"]["class_type"] == "LoadImage"


def test_erzeugen_flux2_mehr_als_drei_referenzen_abgelehnt():
    with pytest.raises(bild_comfy.ComfyFehler):
        bild_comfy.erzeugen_flux2("p", 512, 512, 1, [b"x"] * 4)


def test_erzeugen_flux2_masse_wie_flux1_geprueft():
    with pytest.raises(bild_comfy.ComfyFehler):
        bild_comfy.erzeugen_flux2("p", 500, 512, 1, [])


def test_erzeugen_flux2_laedt_referenzen_hoch_und_fuehrt_aus(monkeypatch):
    hoch, gesendet = [], {}
    monkeypatch.setattr(bild_comfy, "_hochladen", lambda name, daten: hoch.append(daten) or f"up-{len(hoch)}.png")
    monkeypatch.setattr(bild_comfy, "_ausfuehren", lambda ablauf, z: gesendet.setdefault("g", ablauf) and b"PNG")
    assert bild_comfy.erzeugen_flux2("p", 512, 512, 7, [b"eins", b"zwei"]) == b"PNG"
    assert hoch == [b"eins", b"zwei"]
    assert gesendet["g"]["r1"]["inputs"]["image"] == "up-1.png"


def test_fehlendes_modell_klarer_befund(monkeypatch):
    import io, urllib.error
    rumpf = b'{"error": {"message": "Prompt outputs failed validation"}, "node_errors": {"1": {"errors": [{"details": "unet_name: \'flux-2-klein-4b-fp8.safetensors\' not in []"}]}}}'
    def http(methode, pfad, daten=None, zeitlimit=30):
        raise urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(rumpf))
    monkeypatch.setattr(bild_comfy, "_http", http)
    with pytest.raises(bild_comfy.ComfyFehler, match="Bildmodell FLUX.2 fehlt am PC"):
        bild_comfy._ausfuehren(bild_comfy.graph_flux2("p", 512, 512, 1, []), 5)


def _historie(status: dict) -> bytes:
    return json.dumps({"pid": {"status": status, "outputs": {}}}).encode()


def test_speicher_voll_klarer_befund(monkeypatch):
    st = {"status_str": "error", "messages": [["execution_error", {"exception_type": "torch.OutOfMemoryError",
                                                                    "exception_message": "CUDA out of memory"}]]}
    monkeypatch.setattr(bild_comfy, "_http", lambda *a, **k: (200, _historie(st)))
    with pytest.raises(bild_comfy.ComfyFehler, match="Zu viele Vorlagen für die Grafikkarte"):
        bild_comfy._abfragen("pid")


def test_fehler_ohne_meldung_bleibt_allgemein(monkeypatch):
    monkeypatch.setattr(bild_comfy, "_http", lambda *a, **k: (200, _historie({"status_str": "error"})))
    with pytest.raises(bild_comfy.ComfyFehler, match="Erzeugung in ComfyUI fehlgeschlagen"):
        bild_comfy._abfragen("pid")
