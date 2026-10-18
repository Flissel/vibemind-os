"""ComfyUI-Client fuer Newsletter-Bilder (sales-claw Spec 2026-09-29-newsletter-
bilder-und-gestaltung-design.md §7.2). Das Modell steckt in der Arbeitsablauf-
Datei (Standard bilder/flux_schnell_api.json, Env COMFYUI_ABLAUF): ein anderes
offenes Modell ist ein Dateitausch, kein Code. Knoten-Nummern der Datei:
4 = Prompt, 6 = Latent-Groesse, 7 = Sampler (seed), 9 = Speichern.
FLUX.2 klein (bilder/flux2_klein_api.json, Env COMFYUI_ABLAUF_FLUX2): 4 = Prompt, 6/7 = Groesse,
8 = Seed; Referenzbilder haengt graph_flux2 als ReferenceLatent-Kette an."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
ABLAUF = Path(os.environ.get("COMFYUI_ABLAUF") or
              Path(__file__).resolve().parents[1] / "bilder" / "flux_schnell_api.json")
TAKT_S = 2
POLL_PAUSE_MAX_S = 10   # Pause nach fehlgeschlagenem Poll waechst bis hierhin


PROMPT_ZEITLIMIT_S = 120   # POST /prompt: ComfyUI laedt beim Annehmen u. U. Modelle von der HDD
GNADE_S = 90               # nach einem Sende-Timeout: so lange darf die id in History und Warteschlange fehlen


class ComfyFehler(Exception):
    pass


class ComfyVoruebergehend(ComfyFehler):
    """Zeitueberschreitung oder Verbindungsfehler: ein Neuversuch kann gelingen (nie endgueltig)."""


def _UHR() -> float:
    return time.monotonic()


def _SCHLAF(s: float) -> None:
    time.sleep(s)


def _http(methode: str, pfad: str, daten: dict | None = None, zeitlimit: int = 30) -> tuple[int, bytes]:
    koerper = json.dumps(daten).encode("utf-8") if daten is not None else None
    req = urllib.request.Request(URL + pfad, data=koerper, method=methode,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=zeitlimit) as r:
        return r.status, r.read()


def laeuft() -> bool:
    try:
        status, _ = _http("GET", "/system_stats", zeitlimit=5)
        return status == 200
    except Exception:  # noqa: BLE001 - jede Stoerung heisst "laeuft nicht"
        return False


def freigeben() -> None:
    """Modelle aus dem Grafikspeicher werfen (Ollama braucht ihn danach)."""
    _http("POST", "/free", {"unload_models": True, "free_memory": True})


def erzeugen(prompt: str, breite: int, hoehe: int, seed: int, zeitlimit_s: int = 300) -> bytes:
    _masse_pruefen(breite, hoehe)
    ablauf = json.loads(ABLAUF.read_text(encoding="utf-8"))
    ablauf["4"]["inputs"]["text"] = prompt
    ablauf["6"]["inputs"]["width"], ablauf["6"]["inputs"]["height"] = breite, hoehe
    ablauf["7"]["inputs"]["seed"] = int(seed)
    return _ausfuehren(ablauf, zeitlimit_s)


def _abfragen(pid: str) -> bytes | None:
    """Ein Poll: fertiges PNG, None solange ComfyUI noch rechnet; ComfyFehler bei
    gemeldetem Fehler. Netzfehler (OSError) reicht er an _ausfuehren weiter."""
    _, rumpf = _http("GET", f"/history/{pid}")
    eintrag = json.loads(rumpf or b"{}").get(pid)
    if not eintrag:
        return None
    if (eintrag.get("status") or {}).get("status_str") == "error":
        meldung = ""
        for m in (eintrag.get("status") or {}).get("messages") or []:
            if isinstance(m, (list, tuple)) and len(m) > 1 and m[0] == "execution_error" and isinstance(m[1], dict):
                art, text = str(m[1].get("exception_type") or ""), str(m[1].get("exception_message") or "")
                if "OutOfMemory" in art or "out of memory" in text.lower():
                    raise ComfyFehler(SPEICHER_VOLL)
                meldung = text
                break
        raise ComfyFehler("Erzeugung in ComfyUI fehlgeschlagen" + (f": {meldung[:200]}" if meldung else ""))
    for ausgabe in (eintrag.get("outputs") or {}).values():
        for bild in ausgabe.get("images") or []:
            q = urllib.parse.urlencode({"filename": bild["filename"],
                                        "subfolder": bild.get("subfolder", ""),
                                        "type": bild.get("type", "output")})
            _, png = _http("GET", f"/view?{q}", zeitlimit=60)
            return png
    return None


def _in_warteschlange(pid: str) -> bool | None:
    """True/False: GET /queue nennt die id (laufend oder wartend) bzw. nicht; None: /queue nicht erreichbar."""
    try:
        _, rumpf = _http("GET", "/queue")
    except OSError:
        return None
    return pid.encode() in (rumpf or b"")


def _ausfuehren(ablauf: dict, zeitlimit_s: int) -> bytes:
    """Schluss-Fix I2: die prompt_id erzeugt der Client (ComfyUI verlangt eine kanonische UUID). Laeuft POST /prompt
    in eine Zeitueberschreitung, hat ComfyUI den Auftrag vielleicht doch angenommen: NIE neu senden (zweiter
    5-10-min-Auftrag auf der GPU), sondern diese id pollen. Fehlt sie nach GNADE_S weder in History noch in /queue,
    kam der Auftrag nie an -> ComfyVoruebergehend."""
    pid = str(uuid.uuid4())
    gesendet_ohne_antwort = False
    try:
        _, rumpf = _http("POST", "/prompt", {"prompt": ablauf, "prompt_id": pid}, zeitlimit=PROMPT_ZEITLIMIT_S)
    except urllib.error.HTTPError as e:
        try:
            text = e.read().decode("utf-8", "replace")
        except OSError:
            text = ""
        if "not in" in text and any(n in text for n in _FLUX2_DATEIEN):
            raise ComfyFehler(MODELL_FEHLT) from None
        raise ComfyFehler(f"ComfyUI lehnt den Auftrag ab: {text[:200]}") from None
    except OSError:     # TimeoutError, URLError, ConnectionError: Antwort fehlt, Auftrag evtl. angenommen
        gesendet_ohne_antwort, rumpf = True, b""
    if not gesendet_ohne_antwort:
        try:
            pid = json.loads(rumpf or b"{}").get("prompt_id") or ""
        except ValueError:
            pid = ""
        if not pid:
            raise ComfyFehler("ComfyUI hat keinen Auftrag angenommen")
    beginn = _UHR()
    ende = beginn + zeitlimit_s
    fehlschlaege = 0
    while True:
        try:
            png = _abfragen(pid)
            fehlschlaege = 0
        except ComfyFehler:
            raise
        except OSError:   # TimeoutError, URLError, ConnectionError: ComfyUI rechnet vielleicht noch
            png = None    # (kalter Modellstart von der HDD dauerte 400 s) - weiter pollen
            fehlschlaege += 1
        if png is not None:
            return png
        if gesendet_ohne_antwort and _UHR() - beginn >= GNADE_S:
            bekannt = _in_warteschlange(pid)
            if bekannt:
                gesendet_ohne_antwort = False      # angenommen: ab jetzt normal bis zum Zeitlimit
            elif bekannt is False:                 # letzter History-Blick: evtl. gerade fertig geworden
                try:
                    png = _abfragen(pid)
                except OSError:
                    png, bekannt = None, None      # unklar: weiter pollen
                if png is not None:
                    return png
                if bekannt is False:
                    raise ComfyVoruebergehend("ComfyUI antwortet nicht (Zeitueberschreitung beim Auftrag)")
        if _UHR() >= ende:
            break
        _SCHLAF(min(POLL_PAUSE_MAX_S, TAKT_S * fehlschlaege) if fehlschlaege else TAKT_S)
    raise ComfyVoruebergehend(f"Zeitlimit {zeitlimit_s} s ueberschritten")

ABLAUF_UEBERARBEITEN = Path(os.environ.get("COMFYUI_ABLAUF_UEBERARBEITEN") or
                            Path(__file__).resolve().parents[1] / "bilder" / "flux_schnell_img2img_api.json")
SCHRITTE_UEBERARBEITEN = 8
MIN_DENOISE = 0.05


def _hochladen(name: str, daten: bytes) -> str:
    """Ausgangsbild an ComfyUI (POST /upload/image, multipart). Gibt den Namen zurueck,
    unter dem LoadImage es findet."""
    grenze = "----vibemind" + uuid.uuid4().hex
    kopf = (f"--{grenze}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode()
    rumpf = (kopf + daten + f"\r\n--{grenze}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\n"
             f"true\r\n--{grenze}--\r\n".encode())
    req = urllib.request.Request(URL + "/upload/image", data=rumpf, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={grenze}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return str(json.loads(r.read() or b"{}").get("name") or name)
    except urllib.error.HTTPError as e:      # vor OSError: HTTPError ist eine Unterklasse
        art = ComfyVoruebergehend if e.code >= 500 else ComfyFehler   # 4xx: Bild abgelehnt, 5xx: Stoerung
        raise art(f"Vorlage/Quelle nicht an ComfyUI uebergeben: {e.code}") from None
    except OSError:                          # TimeoutError, URLError, ConnectionError
        raise ComfyVoruebergehend("Vorlage/Quelle nicht an ComfyUI uebergeben: Zeitueberschreitung") from None
    except ValueError:                       # Antwort kein JSON
        raise ComfyVoruebergehend("Vorlage/Quelle nicht an ComfyUI uebergeben: Antwort unlesbar") from None


def ueberarbeiten(prompt: str, quelle: bytes, breite: int, hoehe: int, seed: int, staerke: int,
                  zeitlimit_s: int = 300) -> bytes:
    _masse_pruefen(breite, hoehe)
    if not quelle:
        raise ComfyFehler("Ausgangsbild fehlt")
    denoise = min(1.0, max(MIN_DENOISE, int(staerke) / 100))
    name = _hochladen(f"nl-quelle-{uuid.uuid4().hex[:12]}.img", quelle)
    ablauf = json.loads(ABLAUF_UEBERARBEITEN.read_text(encoding="utf-8"))
    ablauf["4"]["inputs"]["text"] = prompt
    ablauf["10"]["inputs"]["image"] = name
    ablauf["11"]["inputs"]["width"], ablauf["11"]["inputs"]["height"] = breite, hoehe
    ablauf["7"]["inputs"].update(seed=int(seed), denoise=round(denoise, 2), steps=SCHRITTE_UEBERARBEITEN)
    return _ausfuehren(ablauf, zeitlimit_s)


ABLAUF_FREISTELLEN = Path(os.environ.get("COMFYUI_ABLAUF_FREISTELLEN") or
                          Path(__file__).resolve().parents[1] / "bilder" / "freistellen_api.json")


def freistellen(quelle: bytes, zeitlimit_s: int = 300) -> bytes:
    """Hintergrund entfernen (BiRefNet, Knoten 2-5 der Ablaufdatei); PNG mit Alpha
    in Originalgroesse. Fehlt das Modell, meldet ComfyUI das als ComfyFehler."""
    if not quelle:
        raise ComfyFehler("Ausgangsbild fehlt")
    name = _hochladen(f"nl-frei-{uuid.uuid4().hex[:12]}.img", quelle)
    ablauf = json.loads(ABLAUF_FREISTELLEN.read_text(encoding="utf-8"))
    ablauf["1"]["inputs"]["image"] = name
    return _ausfuehren(ablauf, zeitlimit_s)


ABLAUF_FLUX2 = Path(os.environ.get("COMFYUI_ABLAUF_FLUX2") or
                    Path(__file__).resolve().parents[1] / "bilder" / "flux2_klein_api.json")
MAX_REFERENZEN = 3
MODELL_FEHLT = "Bildmodell FLUX.2 fehlt am PC"
SPEICHER_VOLL = "Zu viele Vorlagen für die Grafikkarte – mit weniger Vorlagen noch einmal"
_FLUX2_DATEIEN = ("flux-2-klein-4b-fp8", "qwen_3_4b", "flux2-vae")


def _masse_pruefen(breite: int, hoehe: int) -> None:
    if breite % 16 or hoehe % 16 or not (256 <= breite <= 2048 and 256 <= hoehe <= 2048):
        raise ComfyFehler(f"Masse {breite}x{hoehe}: je 256-2048 und Vielfache von 16")


def graph_flux2(prompt: str, breite: int, hoehe: int, seed: int, namen: list[str]) -> dict:
    """FLUX.2-klein-Graph (bilder/flux2_klein_api.json) mit Referenzbildern als Kette ReferenceLatent:
    Text -> c1 -> c2 -> ... -> Guider. Knoten je Referenz i: r<i> Laden, s<i> auf 1 MP, e<i> VAE, c<i> Referenz."""
    g = json.loads(ABLAUF_FLUX2.read_text(encoding="utf-8"))
    g["4"]["inputs"]["text"] = prompt
    g["6"]["inputs"].update(width=breite, height=hoehe)
    g["7"]["inputs"].update(width=breite, height=hoehe)
    g["8"]["inputs"]["noise_seed"] = int(seed)
    vorher = ["4", 0]
    for i, name in enumerate(namen, 1):
        g[f"r{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        g[f"s{i}"] = {"class_type": "ImageScaleToTotalPixels",
                      "inputs": {"image": [f"r{i}", 0], "upscale_method": "lanczos", "megapixels": 1.0,
                                 "resolution_steps": 1}}
        g[f"e{i}"] = {"class_type": "VAEEncode", "inputs": {"pixels": [f"s{i}", 0], "vae": ["3", 0]}}
        g[f"c{i}"] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": vorher, "latent": [f"e{i}", 0]}}
        vorher = [f"c{i}", 0]
    g["5"]["inputs"]["conditioning"] = vorher
    return g


def erzeugen_flux2(prompt: str, breite: int, hoehe: int, seed: int, referenzen=(), zeitlimit_s: int = 300) -> bytes:
    referenzen = list(referenzen)
    if len(referenzen) > MAX_REFERENZEN:
        raise ComfyFehler(f"Höchstens {MAX_REFERENZEN} Referenzbilder")
    _masse_pruefen(breite, hoehe)
    namen = [_hochladen(f"nl-ref-{uuid.uuid4().hex[:12]}.png", r) for r in referenzen]
    return _ausfuehren(graph_flux2(prompt, breite, hoehe, seed, namen), zeitlimit_s)
