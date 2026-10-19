"""Werkzeuge des Video-Claws (17.10.2026). Jede Huelle prueft ihre Eingaben, kennt genau ihre Laura-Route
und gibt kompakt zurueck. Es gibt bewusst KEIN Generalwerkzeug auf die Laura-API.

Ablauf eines Reels (die Skills beschreiben ihn genauer):
  projekt_anlegen -> Material holen (material_uebernehmen / bild_importieren -> standbild, titelkarte)
  -> reel_bauen (Stimme, Schnitt, 9:16, Untertitel, KI-Hinweis nach Inhalt, <= 15 MB) -> Export-ID
  Die Export-ID nennt der Claw am Ende als 'EXPORT: <id>'; abgeholt wird sie vom Arbeiter.
Versendet wird nie etwas.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from spaces.marketing.claw import laura as laura_lesen
from spaces.marketing.video_claw import laura_api
from spaces.marketing.workers.bild_worker import tls_kontext

REEL_MAX_BYTES = 15_000_000
_ID = re.compile(r"^[0-9a-f][0-9a-f-]{7,63}$")
_STIMME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_FARBE = re.compile(r"^#[0-9a-fA-F]{6}$")
BEWEGUNGEN = ("ruhig", "zoom_rein", "zoom_raus", "schwenk")
LOGO_POSITIONEN = ("oben_rechts", "oben_links", "oben_mitte")


def _fehler(text: str) -> dict:
    return {"ok": False, "fehler": text}


def _id(wert, feld: str) -> str | None:
    return wert if isinstance(wert, str) and _ID.fullmatch(wert) else None


# --- Marketing-API (Arbeiter-Schluessel) ------------------------------------------------------------

def _marketing(pfad: str, daten: dict) -> dict:
    basis = os.environ.get("MARKETING_BILD_URL", "").strip().rstrip("/")
    schluessel = os.environ.get("MARKETING_BILD_KEY", "").strip()
    if not basis or not schluessel:
        return _fehler("Marketing-API nicht eingerichtet (MARKETING_BILD_URL/KEY)")
    req = urllib.request.Request(basis + "/api/video/arbeiter" + pfad, data=json.dumps(daten).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    req.add_unredirected_header("X-Bild-Key", schluessel)
    try:
        # TLS-Kontext des Bild-Arbeiters: die Tailnet-Kette der VM endet fuer Windows-Python sonst
        # ueber eine abgelaufene Querzertifizierung (bild_worker.tls_kontext).
        with urllib.request.urlopen(req, timeout=30, context=tls_kontext()) as r:
            return {"ok": True, "daten": json.loads(r.read() or b"{}")}
    except urllib.error.HTTPError as e:
        try:
            grund = json.loads(e.read() or b"{}").get("detail", "")
        except (ValueError, OSError):
            grund = ""
        return _fehler(f"Marketing-API {e.code}: {str(grund)[:200]}")
    except (urllib.error.URLError, OSError) as e:
        return _fehler(f"Marketing-API nicht erreichbar: {type(e).__name__}")


def notiz(auftrag: str, text: str) -> dict:
    """Haelt im Auftrag fest, was du gewaehlt hast und warum (sieht der Betreiber im Pult). Kurz, ein Satz."""
    if not _id(auftrag, "auftrag"):
        return _fehler("auftrag: ID aus dem Briefing")
    if not isinstance(text, str) or not text.strip():
        return _fehler("text fehlt")
    return _marketing(f"/{auftrag}/notiz", {"text": text.strip()[:500]})


# --- Lesen (Material finden) ------------------------------------------------------------------------

def material_suchen(suchwort: str = "", anzahl: int = 10) -> dict:
    """Videos in Laura finden (Name/Projekt enthaelt das Suchwort; leer = die neuesten).
    Liefert je Video kennung (= asset_id fuer die anderen Werkzeuge), projekt_id, name, dauer_s."""
    r = laura_lesen.alle_videos()
    if not r.get("ok"):
        return r
    wort = (suchwort or "").strip().lower()
    videos = [v for v in (r.get("daten") or []) if isinstance(v, dict)]
    if wort:
        videos = [v for v in videos if wort in json.dumps(v, ensure_ascii=False).lower()]
    return {"ok": True, "daten": videos[:max(1, min(int(anzahl or 10), 30))]}


def transkript(asset_id: str, von_s: float = 0, bis_s: float = 0) -> dict:
    """Transkript eines Videos, kompakt: je Segment start_frame, end_frame, text (Frame / 30 = Sekunde).
    Daraus waehlst du die Stellen fuer die Beats. von_s/bis_s grenzen auf einen Zeitbereich ein
    (0 = vom Anfang / bis zum Ende) - bei langen Videos erst grob ueberfliegen, dann eingrenzen."""
    if not _id(asset_id, "asset_id"):
        return _fehler("asset_id ungueltig")
    r = laura_lesen.transkript(asset_id)
    if not r.get("ok"):
        return r
    # Laura liefert je Segment auch jedes Wort mit Samples, IDs und Konfidenz - bei einer 8-Minuten-
    # Aufnahme so viel, dass die CLI es in eine Datei auslagerte und der Claw sie stueckweise las
    # (Probe 18.10.: Lauf blieb danach haengen). Hier nur, was fuer die Stellenwahl zaehlt.
    try:
        von_f, bis_f = max(0.0, float(von_s or 0)) * 30, max(0.0, float(bis_s or 0)) * 30
    except (TypeError, ValueError):
        return _fehler("von_s/bis_s: Sekunden als Zahl")
    segmente = []
    for s in (r.get("daten") or {}).get("segmente") or []:
        if not isinstance(s, dict):
            continue
        start, ende = int(s.get("start_frame") or 0), int(s.get("end_frame") or s.get("start_frame") or 0)
        if (von_f and ende < von_f) or (bis_f and start > bis_f):
            continue
        segmente.append({"start_frame": start, "end_frame": ende, "text": str(s.get("text") or "").strip()})
    return {"ok": True, "daten": {"segmente": segmente[:400], "anzahl": len(segmente),
                                  **({"hinweis": "gekuerzt auf 400 Segmente - mit von_s/bis_s eingrenzen"}
                                     if len(segmente) > 400 else {})}}


# --- Bauen ------------------------------------------------------------------------------------------

def projekt_anlegen(name: str) -> dict:
    """Neues Laura-Projekt fuer dieses Reel (30 fps). Liefert projekt_id."""
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        return _fehler("name: 1 bis 120 Zeichen")
    r = laura_api.anfrage("POST", "/projects", {"name": name.strip(), "sequence_rate_num": 30,
                                                "sequence_rate_den": 1})
    return {"ok": True, "projekt_id": r["daten"].get("id")} if r["ok"] else r


def material_uebernehmen(projekt_id: str, asset_id: str) -> dict:
    """Ein vorhandenes Laura-Video in das Reel-Projekt holen (narrated-reel braucht Assets im selben
    Projekt). Liefert die neue asset_id und die Dauer in Frames."""
    if not _id(projekt_id, "projekt_id") or not _id(asset_id, "asset_id"):
        return _fehler("projekt_id/asset_id ungueltig")
    quelle = laura_api.anfrage("GET", f"/assets/{asset_id}")
    if not quelle["ok"]:
        return quelle
    a = quelle["daten"] or {}
    if a.get("project_id") == projekt_id:
        return {"ok": True, "asset_id": asset_id, "dauer_frames": a.get("duration_frames")}
    if a.get("type") != "video" or not a.get("source_path"):
        return _fehler("Quelle ist kein Video")
    r = laura_api.anfrage("POST", f"/projects/{projekt_id}/assets/import",
                          {"source_path": a["source_path"], "display_name": a.get("display_name") or "video"})
    if not r["ok"]:
        return r
    neu = (r["daten"] or {}).get("asset_id")
    w = laura_api.asset_warten(neu)
    if not w["ok"]:
        return w
    return {"ok": True, "asset_id": neu, "dauer_frames": w["asset"].get("duration_frames")}


def bild_importieren(auftrag: str, projekt_id: str, medien_name: str) -> dict:
    """Ein Bild aus den Marketing-Medien (Name aus dem Briefing) nach Laura holen. Liefert asset_id."""
    if not _id(auftrag, "auftrag") or not _id(projekt_id, "projekt_id"):
        return _fehler("auftrag/projekt_id ungueltig")
    if not isinstance(medien_name, str) or not medien_name.strip():
        return _fehler("medien_name fehlt")
    link = _marketing(f"/{auftrag}/medien-link", {"name": medien_name})
    if not link["ok"]:
        return link
    r = laura_api.anfrage("POST", f"/projects/{projekt_id}/assets/import",
                          {"source_url": link["daten"]["url"], "display_name": medien_name})
    if not r["ok"]:
        return r
    neu = (r["daten"] or {}).get("asset_id")
    w = laura_api.asset_warten(neu)
    if not w["ok"]:
        return w
    if w["asset"].get("type") != "image":
        return _fehler(f"'{medien_name}' kam nicht als Bild an")
    return {"ok": True, "asset_id": neu}


def standbild(projekt_id: str, bild_asset_id: str, dauer_s: float = 3.0, bewegung: str = "zoom_rein") -> dict:
    """Aus einem Bild einen Hochformat-Clip machen (Bild unbeschnitten auf weichem Hintergrund).
    bewegung: ruhig | zoom_rein | zoom_raus | schwenk. dauer_s 1-10 - mindestens so lang wie der Satz dazu."""
    if not _id(projekt_id, "projekt_id") or not _id(bild_asset_id, "bild_asset_id"):
        return _fehler("projekt_id/bild_asset_id ungueltig")
    if bewegung not in BEWEGUNGEN:
        return _fehler(f"bewegung: {', '.join(BEWEGUNGEN)}")
    try:
        dauer = float(dauer_s)
    except (TypeError, ValueError):
        return _fehler("dauer_s: Zahl 1 bis 10")
    if not 1 <= dauer <= 10:
        return _fehler("dauer_s: 1 bis 10")
    r = laura_api.anfrage("POST", f"/projects/{projekt_id}/stills",
                          {"asset_id": bild_asset_id, "dauer_s": dauer, "bewegung": bewegung})
    if not r["ok"]:
        return r
    j = laura_api.job_warten(r["daten"]["job_id"], max_s=240)
    return {"ok": True, "asset_id": j["ergebnis"].get("asset_id")} if j["ok"] else j


def _marke(auftrag: str) -> dict:
    """Marke der Auftragsfirma (Paket C1): akzent, schrift_anzeige, schrift_text, logo - einzelne Werte koennen
    None sein. Scheitert der Abruf, {} - das Reel entsteht dann im bisherigen Aussehen."""
    r = _marketing(f"/{auftrag}/marke", {})
    return r["daten"] if r.get("ok") and isinstance(r.get("daten"), dict) else {}


def titelkarte(auftrag: str, projekt_id: str, text: str, hintergrund: str = "", textfarbe: str = "#ffffff",
               unterzeile: str = "", logo_asset_id: str = "", dauer_s: float = 2.5) -> dict:
    """Titel- oder Schlusskarte (Farbflaeche, Text bis 90 Zeichen, optional Unterzeile und Logo) in der
    Anzeigeschrift der Marke. auftrag: die Auftragsnummer. hintergrund leer = Akzentfarbe der Marke, sonst
    #rrggbb aus dem Briefing. Liefert asset_id."""
    if not _id(auftrag, "auftrag") or not _id(projekt_id, "projekt_id"):
        return _fehler("auftrag/projekt_id ungueltig")
    marke = _marke(auftrag)
    hintergrund = hintergrund or marke.get("akzent") or ""
    if not isinstance(hintergrund, str) or not _FARBE.fullmatch(hintergrund) \
            or not isinstance(textfarbe, str) or not _FARBE.fullmatch(textfarbe):
        return _fehler("hintergrund/textfarbe: #rrggbb")
    if logo_asset_id and not _id(logo_asset_id, "logo_asset_id"):
        return _fehler("logo_asset_id ungueltig")
    koerper = {"text": text, "hintergrund": hintergrund, "textfarbe": textfarbe, "dauer_s": dauer_s}
    if marke.get("schrift_anzeige"):
        koerper["schrift"] = marke["schrift_anzeige"]
    if unterzeile:
        koerper["unterzeile"] = unterzeile
    if logo_asset_id:
        koerper["logo_asset_id"] = logo_asset_id
    r = laura_api.anfrage("POST", f"/projects/{projekt_id}/titelkarte", koerper)
    if not r["ok"]:
        return r
    j = laura_api.job_warten(r["daten"]["job_id"], max_s=240)
    return {"ok": True, "asset_id": j["ergebnis"].get("asset_id")} if j["ok"] else j


def _beats_pruefen(beats) -> list[dict] | str:
    if not isinstance(beats, list) or not 1 <= len(beats) <= 12:
        return "beats: 1 bis 12 Eintraege {text, asset_id, src_in_frame} oder {originalton, asset_id, src_in_frame, src_out_frame}"
    aus = []
    for i, b in enumerate(beats):
        if not isinstance(b, dict):
            return f"beat {i}: Objekt erwartet"
        asset, start = b.get("asset_id"), b.get("src_in_frame", 0)
        if not _id(asset, "asset_id"):
            return f"beat {i}: asset_id ungueltig"
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            return f"beat {i}: src_in_frame ganze Zahl >= 0"
        if b.get("originalton") is True:
            # Phase F: die Person im Video spricht selbst - kein Voiceover, Clip src_in..src_out.
            ende = b.get("src_out_frame")
            if isinstance(ende, bool) or not isinstance(ende, int) or ende <= start:
                return f"beat {i}: originalton braucht src_out_frame > src_in_frame"
            if ende - start > 60 * 30:
                return f"beat {i}: Originalton-Ausschnitt hoechstens 60 s"
            aus.append({"asset_id": asset, "src_in_frame": start, "src_out_frame": ende, "originalton": True})
            continue
        text = b.get("text")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 300:
            return f"beat {i}: text 1 bis 300 Zeichen (oder originalton: true mit src_out_frame)"
        aus.append({"text": text.strip(), "asset_id": asset, "src_in_frame": start, "pad_frames": 12})
    return aus


def reel_bauen(auftrag: str, projekt_id: str, beats: list, stimme: str = "standard", hook: str = "",
               logo_asset_id: str = "", logo_position: str = "oben_rechts", rahmung: str = "zuschnitt",
               untertitel_ersetzen: dict | None = None) -> dict:
    """Das Reel bauen: je Beat ein gesprochener Satz ueber einem Clip, deutsche Stimme, Untertitel,
    Hochformat, KI-Hinweis nach Inhalt, hoechstens 15 MB. Startet den Render und liefert export_id -
    nenne sie am Ende als 'EXPORT: <export_id>'. Der Render selbst laeuft danach weiter.
    auftrag: die Auftragsnummer - damit traegt das Reel die Marke (Untertitel in ihrer Schrift, das gesprochene Wort
    in ihrer Akzentfarbe, Hook in der Anzeigeschrift, ihr Logo, wenn du kein eigenes logo_asset_id angibst).
    beats: [{"text": "Satz, der gesprochen wird", "asset_id": "<Clip>", "src_in_frame": 0}, ...]
    (Feldnamen genau so). Spricht eine Person im Video selbst: {"originalton": true, "asset_id": ...,
    "src_in_frame": <Start>, "src_out_frame": <Ende>} - dann kein Satz, ihr eigener Ton bleibt, die
    Untertitel kommen aus ihrem Transkript (Bildrate 30: Sekunde x 30 = Frame). Originalton geht nur mit
    Videos im SELBEN Projekt wie dessen Analyse - baue solche Reels im Projekt des Videos.
    rahmung: "zuschnitt" (Bildmitte, fuer Personen vor der Kamera) oder "einpassen" (ganzes Bild auf
    weichem Hintergrund - fuer Bildschirmaufnahmen, Folien, breite Videos, sonst ist nichts lesbar).
    untertitel_ersetzen: Erkennungsfehler aus dem Transkript korrigieren, {"Kastermann": "Kunden"} -
    je Eintrag EIN Wort, hoechstens 30; nur offensichtliche Hoerfehler, nichts umformulieren.
    Die Lautstaerke wird immer angeglichen. Dauert einige Minuten (die Stimme wird je Satz erzeugt) - nicht abbrechen
    und NICHT ein zweites Mal aufrufen."""
    if not _id(auftrag, "auftrag") or not _id(projekt_id, "projekt_id"):
        return _fehler("auftrag/projekt_id ungueltig")
    geprueft = _beats_pruefen(beats)
    if isinstance(geprueft, str):
        return _fehler(geprueft)
    if not isinstance(stimme, str) or not _STIMME.fullmatch(stimme):
        return _fehler("stimme: Name oder 'standard'")
    if not isinstance(hook, str) or len(hook) > 120:
        return _fehler("hook: hoechstens 120 Zeichen")
    if logo_asset_id and not _id(logo_asset_id, "logo_asset_id"):
        return _fehler("logo_asset_id ungueltig")
    if logo_position not in LOGO_POSITIONEN:
        return _fehler(f"logo_position: {', '.join(LOGO_POSITIONEN)}")
    if rahmung not in ("zuschnitt", "einpassen"):
        return _fehler("rahmung: zuschnitt oder einpassen")
    ersatz = untertitel_ersetzen or {}
    if not isinstance(ersatz, dict) or len(ersatz) > 30 or any(
            not isinstance(k, str) or not isinstance(v, str) or not k or not v or len(k) > 40 or len(v) > 40
            or any(z.isspace() for z in k) for k, v in ersatz.items()):
        return _fehler("untertitel_ersetzen: hoechstens 30 Eintraege, je ein Wort -> Ersatz (1-40 Zeichen)")
    r = laura_api.anfrage("POST", f"/projects/{projekt_id}/narrated-reel", {
        "beats": geprueft, "backend": "sidecar", "language": "de", "render": False,
        "voice_id": None if stimme == "standard" else stimme, "crossfade_frames": 6, "final_fade_frames": 12})
    if not r["ok"]:
        return r
    timeline = (r["daten"] or {}).get("timeline_id")
    j = laura_api.job_warten(r["daten"]["job_id"], max_s=600)
    if not j["ok"]:
        return j
    warnungen = list((j["ergebnis"] or {}).get("warnings") or [])
    marke = _marke(auftrag)
    if not marke:
        warnungen.append("Marke nicht geladen - Reel im bisherigen Aussehen")
    if not logo_asset_id and marke.get("logo"):
        logo = bild_importieren(auftrag, projekt_id, marke["logo"])
        if logo.get("ok"):
            logo_asset_id = logo["asset_id"]
        else:
            warnungen.append(f"Logo der Marke nicht geholt: {logo.get('fehler')}")
    reel = {"vertical": True, "captions": True, "caption_preset": "reels", "kennzeichnung": "auto",
            "caption_source": "gemischt", "max_bytes": REEL_MAX_BYTES, "loudnorm": True,
            "rahmung": rahmung}
    if ersatz:
        reel["untertitel_ersetzen"] = ersatz
    for feld, wert in (("caption_schrift", marke.get("schrift_text")), ("caption_akzent", marke.get("akzent")),
                       ("hook_schrift", marke.get("schrift_anzeige"))):
        if wert:
            reel[feld] = wert
    if hook.strip():
        reel["hook_text"] = hook.strip()
    if logo_asset_id:
        reel.update(logo_asset_id=logo_asset_id, logo_position=logo_position)
    e = laura_api.anfrage("POST", f"/timelines/{timeline}/render-reel", reel)
    if not e["ok"]:
        return e
    return {"ok": True, "export_id": e["daten"].get("export_id"), "timeline_id": timeline,
            "warnungen": warnungen[:10]}


def reel_status(export_id: str) -> dict:
    """Stand eines Renders (queued/running/ready/error, Groesse)."""
    if not _id(export_id, "export_id"):
        return _fehler("export_id ungueltig")
    r = laura_api.anfrage("GET", f"/exports/{export_id}")
    if not r["ok"]:
        return r
    d = r["daten"] or {}
    return {"ok": True, "status": d.get("status"), "size_bytes": d.get("size_bytes"), "fehler": d.get("error")}


WERKZEUGE = (notiz, material_suchen, transkript, projekt_anlegen, material_uebernehmen, bild_importieren,
             standbild, titelkarte, reel_bauen, reel_status)
