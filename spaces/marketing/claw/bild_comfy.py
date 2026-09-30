"""ComfyUI-Client fuer Newsletter-Bilder (sales-claw Spec 2026-09-29-newsletter-
bilder-und-gestaltung-design.md §7.2). Das Modell steckt in der Arbeitsablauf-
Datei (Standard bilder/flux_schnell_api.json, Env COMFYUI_ABLAUF): ein anderes
offenes Modell ist ein Dateitausch, kein Code. Knoten-Nummern der Datei:
4 = Prompt, 6 = Latent-Groesse, 7 = Sampler (seed), 9 = Speichern."""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
ABLAUF = Path(os.environ.get("COMFYUI_ABLAUF") or
              Path(__file__).resolve().parents[1] / "bilder" / "flux_schnell_api.json")
TAKT_S = 2


class ComfyFehler(Exception):
    pass


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
    if breite % 16 or hoehe % 16 or not (256 <= breite <= 2048 and 256 <= hoehe <= 2048):
        raise ComfyFehler(f"Masse {breite}x{hoehe}: je 256-2048 und Vielfache von 16")
    ablauf = json.loads(ABLAUF.read_text(encoding="utf-8"))
    ablauf["4"]["inputs"]["text"] = prompt
    ablauf["6"]["inputs"]["width"], ablauf["6"]["inputs"]["height"] = breite, hoehe
    ablauf["7"]["inputs"]["seed"] = int(seed)
    return _ausfuehren(ablauf, zeitlimit_s)


def _ausfuehren(ablauf: dict, zeitlimit_s: int) -> bytes:
    _, rumpf = _http("POST", "/prompt", {"prompt": ablauf})
    pid = json.loads(rumpf or b"{}").get("prompt_id")
    if not pid:
        raise ComfyFehler("ComfyUI hat keinen Auftrag angenommen")
    ende = time.monotonic() + zeitlimit_s
    runden = max(1, zeitlimit_s // TAKT_S)
    for _ in range(runden):
        _, rumpf = _http("GET", f"/history/{pid}")
        eintrag = json.loads(rumpf or b"{}").get(pid)
        if eintrag:
            if (eintrag.get("status") or {}).get("status_str") == "error":
                raise ComfyFehler("Erzeugung in ComfyUI fehlgeschlagen")
            for ausgabe in (eintrag.get("outputs") or {}).values():
                for bild in ausgabe.get("images") or []:
                    q = urllib.parse.urlencode({"filename": bild["filename"],
                                                "subfolder": bild.get("subfolder", ""),
                                                "type": bild.get("type", "output")})
                    _, png = _http("GET", f"/view?{q}", zeitlimit=60)
                    return png
        if time.monotonic() > ende:
            break
        _SCHLAF(TAKT_S)
    raise ComfyFehler(f"Zeitlimit {zeitlimit_s} s ueberschritten")

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
    with urllib.request.urlopen(req, timeout=60) as r:
        return str(json.loads(r.read() or b"{}").get("name") or name)


def ueberarbeiten(prompt: str, quelle: bytes, breite: int, hoehe: int, seed: int, staerke: int,
                  zeitlimit_s: int = 300) -> bytes:
    if breite % 16 or hoehe % 16 or not (256 <= breite <= 2048 and 256 <= hoehe <= 2048):
        raise ComfyFehler(f"Masse {breite}x{hoehe}: je 256-2048 und Vielfache von 16")
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
