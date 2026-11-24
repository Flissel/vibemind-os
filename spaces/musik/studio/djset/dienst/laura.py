"""Laura-Client des Musik-Dienstes. Fail-soft: keine Funktion wirft, Rueckgabe immer
{"ok": ...}; in keinem Fehlertext steht der Token. Laura wird nur ueber ihre HTTP-API
angesprochen (LAURA_API_URL, Header X-Laura-Token aus LAURA_TOKEN); es wird nichts
geloescht und kein vorhandener Clip veraendert."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FEHLER_MAXLAENGE = 300
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
ZEITLIMIT_S = 30


def _basis() -> str:
    return os.environ.get("LAURA_API_URL", "http://127.0.0.1:8765").strip().rstrip("/")


def _token() -> str:
    return os.environ.get("LAURA_TOKEN", "").strip()


def _ohne_token(text: str) -> str:
    wert = _token()
    return text.replace(wert, "<token>") if wert else text


def _id_ok(*ids: object) -> bool:
    """Jede ID, die in einen URL-Pfad kommt, muss durch diese Pruefung."""
    return all(isinstance(i, str) and _ID.fullmatch(i) for i in ids)


def _ungueltig() -> dict:
    return {"ok": False, "fehler": "ungueltige ID"}


def _fehler(text: str) -> dict:
    return {"ok": False, "fehler": _ohne_token(text)[:FEHLER_MAXLAENGE]}


def _gleicher_host(url: str, basis: str) -> bool:
    a, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(basis)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


class _WeiterleitungOhneToken(urllib.request.HTTPRedirectHandler):
    """Folgt Weiterleitungen, laesst den Token aber an fremden Zielen weg."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        neu = super().redirect_request(req, fp, code, msg, headers, newurl)
        if neu is not None and not _gleicher_host(newurl, _basis()):
            for name in list(neu.headers) + list(neu.unredirected_hdrs):
                if name.lower() == "x-laura-token":
                    neu.headers.pop(name, None)
                    neu.unredirected_hdrs.pop(name, None)
        return neu


def _roh(methode: str, url: str, daten: bytes | None, kopf: dict) -> tuple[int, bytes]:
    """Der einzige echte Netzgriff - Tests ersetzen genau diese Funktion."""
    req = urllib.request.Request(url, data=daten, method=methode, headers=kopf)
    opener = urllib.request.build_opener(_WeiterleitungOhneToken())
    try:
        with opener.open(req, timeout=ZEITLIMIT_S) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def anfrage(methode: str, pfad: str, daten: dict | None = None) -> dict:
    kopf = {"Content-Type": "application/json"}
    if _token():
        kopf["X-Laura-Token"] = _token()
    try:
        roh = json.dumps(daten).encode("utf-8") if daten is not None else (b"{}" if methode == "POST" else None)
        status, rumpf = _roh(methode, _basis() + pfad, roh, kopf)
    except Exception as e:  # noqa: BLE001 - fail-soft
        return _fehler(f"Laura nicht erreichbar: {type(e).__name__}: {e}")
    text = rumpf.decode("utf-8", "replace")
    if not 200 <= status < 300:
        return _fehler(f"Laura HTTP {status}: {text}")
    try:
        return {"ok": True, "daten": json.loads(text) if text.strip() else {}}
    except ValueError:
        return {"ok": False, "fehler": "Laura-Antwort ist kein JSON"}


def _fps(projekt: dict) -> tuple[int, int]:
    num = int(projekt.get("sequence_rate_num") or 30)
    den = int(projekt.get("sequence_rate_den") or 1)
    return num, den or 1


def _frames(zeitleiste_daten: dict) -> int:
    return max((int(c.get("seq_out_frame_exclusive") or 0) for c in zeitleiste_daten.get("clips") or []),
               default=0)


def zeitleiste(timeline_id: str) -> dict:
    """Laenge (Frames des spaetesten Videoclips) und Bildrate der Projekt-Sequenz."""
    if not _id_ok(timeline_id):
        return _ungueltig()
    t = anfrage("GET", f"/timelines/{timeline_id}")
    if not t["ok"]:
        return t
    tl = t["daten"] or {}
    if not _id_ok(tl.get("project_id")):
        return _ungueltig()
    p = anfrage("GET", f"/projects/{tl.get('project_id')}")
    if not p["ok"]:
        return p
    num, den = _fps(p["daten"] or {})
    return {"ok": True, "project_id": tl.get("project_id"), "name": tl.get("name"),
            "frames": _frames(tl), "fps_num": num, "fps_den": den}


def _letzter_export(project_id: str, timeline_id: str) -> dict | None:
    if not _id_ok(project_id, timeline_id):
        return None
    r = anfrage("GET", f"/projects/{project_id}/exports")  # neueste zuerst
    if not r["ok"]:
        return None
    for e in r["daten"] or []:
        if e.get("timeline_id") == timeline_id:
            return {"export_id": e.get("id"), "status": e.get("status"), "format": e.get("format"),
                    "erstellt": e.get("created_at")}
    return None


def videos_suchen(suchwort: str, hoechstens: int = 10) -> dict:
    """Zeitleisten, deren Projekt- oder Zeitleistenname das Suchwort enthaelt (ohne Gross/Klein).
    Sieht nur die ersten 200 Projekte (Seitengrenze von Lauras /projects)."""
    wort = (suchwort or "").strip().lower()
    if not wort:
        return {"ok": False, "fehler": "Suchwort fehlt"}
    p = anfrage("GET", "/projects?limit=200")
    if not p["ok"]:
        return p
    treffer: list[dict] = []
    for projekt in p["daten"] or []:
        if len(treffer) >= hoechstens:
            break
        if not _id_ok(projekt.get("id")):
            continue
        projekt_passt = wort in str(projekt.get("name") or "").lower()
        t = anfrage("GET", f"/projects/{projekt.get('id')}/timelines")
        if not t["ok"]:
            continue
        num, den = _fps(projekt)
        for tl in t["daten"] or []:
            if len(treffer) >= hoechstens:
                break
            if not (projekt_passt or wort in str(tl.get("name") or "").lower()):
                continue
            treffer.append({
                "project_id": projekt.get("id"), "projekt": projekt.get("name"),
                "timeline_id": tl.get("id"), "zeitleiste": tl.get("name"),
                "sekunden": round(_frames(tl) * den / num, 3),
                "letzter_export": _letzter_export(projekt.get("id"), tl.get("id")),
            })
    return {"ok": True, "treffer": treffer}


def importieren(project_id: str, url: str, display_name: str) -> dict:
    if not _id_ok(project_id):
        return _ungueltig()
    r = anfrage("POST", f"/projects/{project_id}/assets/import",
                {"source_url": url, "display_name": display_name})
    if not r["ok"]:
        return r
    asset_id = (r["daten"] or {}).get("asset_id")
    return {"ok": True, "asset_id": asset_id} if asset_id else {"ok": False, "fehler": "Laura gab keine asset_id"}


def asset_warten(asset_id: str, max_s: float = 300, takt_s: float = 2.0, schlafen=time.sleep,
                 uhr=time.monotonic) -> dict:
    """Warten, bis das Asset geprobt ist (sha256) UND Ton hat (type audio oder codec_audio)."""
    if not _id_ok(asset_id):
        return _ungueltig()
    ende = uhr() + max_s
    while True:
        r = anfrage("GET", f"/assets/{asset_id}")
        if not r["ok"]:
            return r
        asset = r["daten"] or {}
        geprobt = bool(asset.get("sha256"))
        if geprobt and (asset.get("type") == "audio" or asset.get("codec_audio")):
            return {"ok": True, "asset": asset}
        s = anfrage("GET", f"/assets/{asset_id}/import-status")
        if s["ok"] and (s["daten"] or {}).get("phase") in ("error", "cancelled"):
            return _fehler(f"Import fehlgeschlagen: {(s['daten'] or {}).get('error') or ''}")
        if uhr() >= ende:
            if geprobt:
                return {"ok": False, "fehler": "Asset hat kein Ton (kein Audio-Strom nach der Probe)"}
            return {"ok": False, "fehler": f"Import nach {int(max_s)} s nicht fertig"}
        schlafen(takt_s)


def audio_clips(timeline_id: str) -> dict:
    if not _id_ok(timeline_id):
        return _ungueltig()
    r = anfrage("GET", f"/timelines/{timeline_id}/audio-clips")
    return {"ok": True, "clips": r["daten"] or []} if r["ok"] else r


def clip_anlegen(timeline_id: str, koerper: dict) -> dict:
    if not _id_ok(timeline_id):
        return _ungueltig()
    r = anfrage("POST", f"/timelines/{timeline_id}/audio-clips", koerper)
    return {"ok": True, "clip": r["daten"]} if r["ok"] else r


def rendern(timeline_id: str, reel_optionen: dict | None = None) -> dict:
    """Export anstossen. Ohne `reel_optionen`: einfacher Render (POST /timelines/{id}/render,
    Standardoptionen). Mit `reel_optionen`: POST /timelines/{id}/render-reel mit genau diesem
    Dict als Body. Fruehere Exporte werden nicht erkannt (Lauras API zeigt keine Optionen)."""
    if not _id_ok(timeline_id):
        return _ungueltig()
    if reel_optionen is not None:
        r = anfrage("POST", f"/timelines/{timeline_id}/render-reel", reel_optionen)
    else:
        r = anfrage("POST", f"/timelines/{timeline_id}/render", {})
    if not r["ok"]:
        return r
    d = r["daten"] or {}
    if not d.get("export_id"):
        return {"ok": False, "fehler": "Laura gab keine export_id"}
    return {"ok": True, "export_id": d["export_id"], "job_id": d.get("job_id")}


def export_warten(export_id: str, max_s: float = 900, takt_s: float = 5.0, schlafen=time.sleep,
                  uhr=time.monotonic) -> dict:
    """Warten, bis der Export fertig ist und sein Download-Link da ist (status 'ready' heisst
    noch nicht, dass die Kopie im Speicher liegt - der Link wird dann erneut angefragt)."""
    if not _id_ok(export_id):
        return _ungueltig()
    ende = uhr() + max_s
    letzter = "Export noch nicht fertig"
    while True:
        e = anfrage("GET", f"/exports/{export_id}")
        if not e["ok"]:
            return e
        exp = e["daten"] or {}
        if exp.get("status") == "error":
            return _fehler(f"Export fehlgeschlagen: {exp.get('error') or ''}")
        if exp.get("status") == "ready":
            d = anfrage("GET", f"/exports/{export_id}/download-url")
            url = (d["daten"] or {}).get("url") if d["ok"] else None
            if url:
                return {"ok": True, "url": url, "size_bytes": exp.get("size_bytes")}
            letzter = d.get("fehler") or "Download-Link fehlt noch"
        if uhr() >= ende:
            return _fehler(f"Export nach {int(max_s)} s nicht abholbereit: {letzter}")
        schlafen(takt_s)


def herunterladen(url: str, ziel: Path) -> dict:
    """URL (absolut oder relativ zu LAURA_API_URL) in `ziel` schreiben, atomar ueber eine
    Nachbardatei. Der Token geht nur an Lauras eigenen Host."""
    ziel = Path(ziel)
    basis = _basis()
    voll = urllib.parse.urljoin(basis + "/", url)
    kopf: dict = {}
    if _token() and _gleicher_host(voll, basis):
        kopf["X-Laura-Token"] = _token()
    tmp = ziel.with_name(ziel.name + ".teil")
    try:
        status, rumpf = _roh("GET", voll, None, kopf)
        if not 200 <= status < 300:
            return _fehler(f"Download HTTP {status}")
        ziel.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(rumpf)
        os.replace(tmp, ziel)
        return {"ok": True, "bytes": len(rumpf)}
    except Exception as e:  # noqa: BLE001 - fail-soft
        return _fehler(f"Download fehlgeschlagen: {type(e).__name__}: {e}")
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
