"""Slide-Vorlagen der Video-Werkbank (11.11.2026): Bildplan pruefen, auf Satzgrenzen legen, als HyperFrames-
Komposition (HTML, 1920x1080, 30 fps) ausgeben.

Ein Bildplan (`bild`) hat 2 bis 5 Schritte `{"vorlage", "ab_satz", <Felder der Vorlage>}`. Ein Schritt beginnt
am Satz `ab_satz` der gesprochenen Szene und steht bis zum Beginn des naechsten Schritts. Die Vorlagen liegen als
HTML-Bausteine unter video_werkbank/vorlagen/ (rahmen.html, <vorlage>.html, stil.css); Schriften kommen aus
claw/schriftdateien (lokal, nie von einem CDN). Alle Texte laufen durch html.escape. Dieses Modul importiert nichts
Schweres (kein Sprechmodell, kein Netz, kein HyperFrames-Aufruf).
"""
from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from spaces.marketing.claw.schriften import REGISTER

if TYPE_CHECKING:  # nur fuer Typen; zur Laufzeit kein Import des Sprech-Moduls
    from spaces.marketing.workers.video_stimme import Satz

VORLAGEN = ("titel", "stichpunkte", "zahl", "zitat", "vergleich", "agenda")
FELDER: dict[str, tuple[str, ...]] = {
    "titel": ("zeile", "unterzeile"),
    "stichpunkte": ("ueberschrift", "punkte"),
    "zahl": ("zahl", "einheit", "erklaerung"),
    "zitat": ("zitat", "von"),
    "vergleich": ("links", "rechts"),
    "agenda": ("punkte", "aktiv"),
}
SCHRITTE_MIN, SCHRITTE_MAX = 2, 5
ZEILE_MAX, PUNKT_MAX = 60, 80
STILL_MAX_S = 4.0                      # laenger steht ein Bild nicht ohne Einblendung still
EINBLENDEN = ("stichpunkte", "vergleich")   # Vorlagen, deren Inhalt an Satzgrenzen erscheint
BLENDE_S = 0.2
VARIANTEN = 3

_ORDNER = Path(__file__).resolve().parent.parent / "video_werkbank" / "vorlagen"
SCHRIFTEN = Path(__file__).resolve().parent / "schriftdateien"
VORGABE_AKZENT = "#4C8DFF"
VORGABE_SCHRIFT = "manrope"
_FARBE = re.compile(r"#[0-9A-Fa-f]{6}")
_LOGO_ENDUNG = (".png", ".jpg", ".jpeg")
GRUND, TEXT, LEISE = "#0D1117", "#F3F5F8", "#A9B2BF"
_PUNKT_FELDER = {"punkte", "erklaerung", "zitat"}        # Obergrenze PUNKT_MAX; alle anderen Textfelder ZEILE_MAX


@dataclass
class Schritt:
    vorlage: str
    felder: dict
    start_s: float
    dauer_s: float
    einblendungen: list[float] = field(default_factory=list)   # Szenenzeit, an der weiterer Inhalt erscheint


# --- Pruefung -------------------------------------------------------------------------------

def _text_fehler(vorlage: str, name: str, wert, grenze: int, fehler: list[str]) -> None:
    if not isinstance(wert, str) or not wert.strip():
        fehler.append(f"{vorlage}: {name} fehlt")
    elif len(wert) > grenze:
        fehler.append(f"{vorlage}: {name} ist zu lang ({len(wert)} statt hoechstens {grenze} Zeichen)")


def _punkte_fehler(vorlage: str, name: str, punkte, kleinste: int, groesste: int, fehler: list[str]) -> None:
    if not isinstance(punkte, list) or not punkte:
        fehler.append(f"{vorlage}: {name} fehlt")
        return
    if not kleinste <= len(punkte) <= groesste:
        fehler.append(f"{vorlage}: {name} braucht {kleinste} bis {groesste} Punkte")
    for p in punkte:
        _text_fehler(vorlage, name, p, PUNKT_MAX, fehler)


def _felder_fehler(vorlage: str, f: dict) -> list[str]:
    fehler: list[str] = []
    if vorlage == "titel":
        _text_fehler(vorlage, "zeile", f.get("zeile"), ZEILE_MAX, fehler)
        _text_fehler(vorlage, "unterzeile", f.get("unterzeile"), ZEILE_MAX, fehler)
    elif vorlage == "stichpunkte":
        _text_fehler(vorlage, "ueberschrift", f.get("ueberschrift"), ZEILE_MAX, fehler)
        _punkte_fehler(vorlage, "punkte", f.get("punkte"), 2, 5, fehler)
    elif vorlage == "zahl":
        _text_fehler(vorlage, "zahl", f.get("zahl"), 12, fehler)
        _text_fehler(vorlage, "einheit", f.get("einheit"), ZEILE_MAX, fehler)
        _text_fehler(vorlage, "erklaerung", f.get("erklaerung"), PUNKT_MAX, fehler)
    elif vorlage == "zitat":
        _text_fehler(vorlage, "zitat", f.get("zitat"), PUNKT_MAX, fehler)
        _text_fehler(vorlage, "von", f.get("von"), ZEILE_MAX, fehler)
    elif vorlage == "vergleich":
        for seite in ("links", "rechts"):
            s = f.get(seite)
            if not isinstance(s, dict):
                fehler.append(f"{vorlage}: {seite} fehlt")
                continue
            _text_fehler(vorlage, f"{seite}.titel", s.get("titel"), ZEILE_MAX, fehler)
            _punkte_fehler(vorlage, f"{seite}.punkte", s.get("punkte"), 1, 4, fehler)
    elif vorlage == "agenda":
        _punkte_fehler(vorlage, "punkte", f.get("punkte"), 3, 3, fehler)
        aktiv = f.get("aktiv")
        if isinstance(aktiv, bool) or not isinstance(aktiv, int):
            fehler.append(f"{vorlage}: aktiv fehlt")
        elif not 1 <= aktiv <= 3:
            fehler.append(f"{vorlage}: aktiv muss 1 bis 3 sein")
    return fehler


def _felder(schritt: dict) -> dict:
    """Felder eines Schritts: flach neben `vorlage`/`ab_satz`; ein Unterobjekt `felder` gilt ebenfalls."""
    nested = schritt.get("felder")
    basis = {k: v for k, v in schritt.items() if k not in ("vorlage", "ab_satz", "felder")}
    return {**basis, **nested} if isinstance(nested, dict) else basis


def bild_pruefen(bild: dict, saetze: int) -> list[str]:
    """Fehlersaetze zum Bildplan; leer = gut."""
    schritte = bild.get("schritte") if isinstance(bild, dict) else None
    if not isinstance(schritte, list) or not SCHRITTE_MIN <= len(schritte) <= SCHRITTE_MAX:
        return [f"Ein Bild braucht {SCHRITTE_MIN} bis {SCHRITTE_MAX} Schritte."]
    fehler: list[str] = []
    letzter = -1
    for i, s in enumerate(schritte):
        if not isinstance(s, dict) or s.get("vorlage") not in VORLAGEN:
            fehler.append(f"Schritt {i + 1}: unbekannte Vorlage")
            continue
        ab = s.get("ab_satz")
        if isinstance(ab, bool) or not isinstance(ab, int):
            fehler.append(f"Schritt {i + 1}: ab_satz fehlt")
        else:
            if i == 0 and ab != 0:
                fehler.append("Schritt 1 muss bei Satz 0 beginnen")
            if ab <= letzter:
                fehler.append(f"Schritt {i + 1}: ab_satz steigt nicht")
            if ab >= saetze:
                fehler.append(f"Schritt {i + 1}: ab_satz {ab} liegt hinter dem letzten Satz")
            letzter = max(letzter, ab)
        fehler += [f"Schritt {i + 1}, {m}" for m in _felder_fehler(s["vorlage"], _felder(s))]
    return fehler


# --- Zeitplan -------------------------------------------------------------------------------

def zeitplan(bild: dict, saetze: list[Satz], dauer_s: float) -> list[Schritt]:
    """Schritte auf die Satzgrenzen legen. Der Start ist die Startzeit des Satzes `ab_satz`; der Schritt steht
    bis zum Start des naechsten (der letzte bis `dauer_s`). Die Einblendungen sind die Startzeiten der weiteren
    Saetze innerhalb des Schritts (nur bei Vorlagen, deren Inhalt nach und nach erscheint)."""
    roh = bild["schritte"]
    ab = [int(s["ab_satz"]) for s in roh]
    plan: list[Schritt] = []
    for i, s in enumerate(roh):
        start = round(saetze[ab[i]].start_s, 3)
        ende_satz = ab[i + 1] if i + 1 < len(roh) else len(saetze)
        ende = round(saetze[ab[i + 1]].start_s, 3) if i + 1 < len(roh) else round(dauer_s, 3)
        weitere = [round(saetze[k].start_s, 3) for k in range(ab[i] + 1, ende_satz)]
        plan.append(Schritt(s["vorlage"], _felder(s), start, round(ende - start, 3),
                            weitere if s["vorlage"] in EINBLENDEN else []))
    return plan


def _steht_still(p: Schritt) -> bool:
    """Laengster Abschnitt ohne neue Einblendung groesser als STILL_MAX_S?"""
    marken = [p.start_s, *[e for e in p.einblendungen if p.start_s < e < p.start_s + p.dauer_s], p.start_s + p.dauer_s]
    return max(b - a for a, b in zip(marken, marken[1:])) > STILL_MAX_S


# --- Marke ----------------------------------------------------------------------------------

def marke_fuer(mandant: str, name: str | None = None, wurzel: str | None = None) -> dict:
    """`marke` fuer `komposition` aus der Marke.md des Mandanten (companys/<Firma>/Marke.md); fehlende oder
    ungueltige Werte bleiben leer und fallen in `komposition` auf die Vorgaben zurueck. Wirft nie."""
    leer = {"akzent": "", "schrift_anzeige": "", "schrift_text": "", "logo": ""}
    try:
        from spaces.marketing.claw import markenprofil, markenwissen   # erst hier: schwerere Abhaengigkeiten
        profil = markenprofil.lesen(wurzel or markenwissen.wurzel(), mandant, name or mandant)
    except Exception:
        return leer
    w = profil.werte
    return {"akzent": w.get("akzent", ""), "schrift_anzeige": w.get("schrift_anzeige", ""),
            "schrift_text": w.get("schrift_text", ""), "logo": profil.logo_dunkel_pfad or profil.logo_pfad or ""}


def _farbe(wert: object) -> tuple[int, int, int] | None:
    if isinstance(wert, str) and _FARBE.fullmatch(wert):
        return int(wert[1:3], 16), int(wert[3:5], 16), int(wert[5:7], 16)
    return None


def _helligkeit(rgb: tuple[int, int, int]) -> float:
    def kanal(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (kanal(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02X}" for c in rgb)


def _aufhellen(rgb: tuple[int, int, int], bis: float = 0.28) -> tuple[float, float, float]:
    """Fuer Text auf dem dunklen Grund: Akzent mit Weiss mischen, bis er hell genug ist."""
    t = 0.0
    mix = rgb
    while _helligkeit(mix) < bis and t < 1:
        t += 0.05
        mix = tuple(c + (255 - c) * t for c in rgb)
    return mix


def _schrift(sid: object) -> str:
    return sid if isinstance(sid, str) and sid in REGISTER and _gewichte(sid) else VORGABE_SCHRIFT


@lru_cache(maxsize=None)
def _gewichte(sid: str) -> tuple[int, ...]:
    """Gewichte (normal), deren Datei lokal vorliegt."""
    return tuple(sorted(g for g, stil in REGISTER[sid]["dateien"]
                        if stil == "normal" and (SCHRIFTEN / f"{sid}-{g}-normal.woff2").is_file()))


def schrift_dateien(marke: dict) -> list[str]:
    """Dateinamen aus claw/schriftdateien, die `komposition` fuer diese Marke braucht (Aufrufer legt sie unter
    `schriften/` neben die HTML-Datei)."""
    namen = []
    for sid in dict.fromkeys((_schrift(marke.get("schrift_anzeige")), _schrift(marke.get("schrift_text")))):
        namen += [f"{sid}-{g}-normal.woff2" for g in _gewichte(sid)]
    return namen


def logo_name(marke: dict) -> str:
    """Dateiname des Logos neben der HTML-Datei ('' ohne Logo)."""
    pfad = str(marke.get("logo") or "")
    ende = Path(pfad).suffix.lower()
    return "logo" + ende if pfad and ende in _LOGO_ENDUNG and os.path.isfile(pfad) else ""


def _schriften_css(marke: dict) -> tuple[str, str]:
    """(@font-face-Regeln, CSS-Variablen der Schriften). Fehlt eine Datei, bleibt der Systemstapel des Registers."""
    regeln, variablen = [], []
    for rolle, vorgabe_gewicht in (("anzeige", None), ("text", 400)):
        sid = _schrift(marke.get(f"schrift_{rolle}"))
        gew = _gewichte(sid)
        stapel = REGISTER[sid]["stapel"]
        familie = f"Werkbank-{rolle}"
        for g in gew:
            regeln.append(f'@font-face {{ font-family: "{familie}"; font-weight: {g}; font-style: normal; '
                          f'src: url("schriften/{sid}-{g}-normal.woff2") format("woff2"); }}')
        if vorgabe_gewicht is None:
            gewicht = max(gew)
        else:
            gewicht = min(gew, key=lambda g: abs(g - vorgabe_gewicht))
        variablen.append(f'--schrift-{rolle}: "{familie}", {stapel}; --gewicht-{rolle}: {gewicht};')
    return "\n".join(dict.fromkeys(regeln)), " ".join(variablen)


# --- HTML -----------------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _baustein(name: str) -> str:
    return (_ORDNER / name).read_text(encoding="utf-8")


def _fuellen(vorlage: str, werte: dict[str, str]) -> str:
    # ein Durchgang: Text, der selbst %%name%% enthaelt, wird nicht ein zweites Mal ersetzt
    return re.sub(r"%%(\w+)%%", lambda m: werte.get(m.group(1), m.group(0)), vorlage)


def _e(wert: object) -> str:
    return html.escape(str(wert if wert is not None else ""), quote=True)


def _skala(text: object, ziel: int, kleinste: float = 0.5, kuerzel: int = 18) -> str:
    """Schriftskala (Faktor): lange Woerter und lange Zeilen verkleinern, damit nichts ueberlaeuft.
    `ziel` = Zeichenzahl, bis zu der die volle Groesse passt; `kuerzel` = laengstes Wort in Zeichen."""
    s = str(text or "")
    wort = max((len(w) for w in s.split()), default=0)
    faktor = min(1.0, kuerzel / wort if wort else 1.0, ziel / len(s) if s and len(s) > ziel else 1.0)
    return f"{max(kleinste, faktor):.2f}"


def _punkte(punkte: list, zeiten: list[float]) -> str:
    return "".join(f'<li class="rein" style="--d:{z:.2f}s"><span class="marke"></span><span>{_e(p)}</span></li>'
                   for p, z in zip(punkte, zeiten))


def _punkt_zeiten(n: int, p: Schritt, erste: float = 0.45) -> list[float]:
    """Einblendzeit je Punkt, relativ zum Schritt: der erste kurz nach dem Start, jeder weitere an der naechsten
    Satzgrenze des Schritts; gibt es keine Grenze mehr, folgt er 0,5 s nach dem vorigen."""
    zeiten = [erste]
    for k in range(1, n):
        if k - 1 < len(p.einblendungen):
            zeiten.append(max(zeiten[-1] + 0.1, p.einblendungen[k - 1] - p.start_s))
        else:
            zeiten.append(zeiten[-1] + 0.5)
    spaet = max(0.0, p.dauer_s - 0.6)
    return [min(z, spaet) for z in zeiten]


def _inhalt(p: Schritt) -> str:
    f, v = p.felder, p.vorlage
    if v == "titel":
        return _fuellen(_baustein("titel.html"), {
            "zeile": _e(f.get("zeile")), "unterzeile": _e(f.get("unterzeile")),
            "s_zeile": _skala(f.get("zeile"), 46)})
    if v == "stichpunkte":
        pk = list(f.get("punkte") or [])
        return _fuellen(_baustein("stichpunkte.html"), {
            "ueberschrift": _e(f.get("ueberschrift")), "s_ueberschrift": _skala(f.get("ueberschrift"), 40),
            "punkte": _punkte(pk, _punkt_zeiten(len(pk), p))})
    if v == "zahl":
        return _fuellen(_baustein("zahl.html"), {
            "zahl": _e(f.get("zahl")), "einheit": _e(f.get("einheit")), "erklaerung": _e(f.get("erklaerung")),
            "s_zahl": _skala(f.get("zahl"), 5, 0.4, 7), "s_einheit": _skala(f.get("einheit"), 14, 0.5, 14)})
    if v == "zitat":
        return _fuellen(_baustein("zitat.html"), {
            "zitat": _e(f.get("zitat")), "von": _e(f.get("von")), "s_zitat": _skala(f.get("zitat"), 70, 0.6, 22)})
    if v == "vergleich":
        seiten = {s: (f.get(s) if isinstance(f.get(s), dict) else {}) for s in ("links", "rechts")}
        links, rechts = (list(seiten[s].get("punkte") or []) for s in ("links", "rechts"))
        rechts_ab = (p.einblendungen[0] - p.start_s) if p.einblendungen else min(1.4, max(0.0, p.dauer_s - 0.6))
        return _fuellen(_baustein("vergleich.html"), {
            "links_titel": _e(seiten["links"].get("titel")), "rechts_titel": _e(seiten["rechts"].get("titel")),
            "s_links": _skala(seiten["links"].get("titel"), 20, 0.5, 14),
            "s_rechts": _skala(seiten["rechts"].get("titel"), 20, 0.5, 14),
            "rechts_d": f"{rechts_ab:.2f}",
            "links_punkte": _punkte(links, [0.3 + 0.12 * i for i in range(len(links))]),
            "rechts_punkte": _punkte(rechts, [rechts_ab + 0.3 + 0.12 * i for i in range(len(rechts))])})
    pk = list(f.get("punkte") or [])
    aktiv = f.get("aktiv") if isinstance(f.get("aktiv"), int) else 1
    zeilen = "".join(
        f'<li class="{"aktiv" if i + 1 == aktiv else "erledigt" if i + 1 < aktiv else ""}" style="--d:{0.1 * i:.2f}s">'
        f'<span class="nr">{i + 1:02d}</span><span>{_e(t)}</span></li>' for i, t in enumerate(pk))
    return _fuellen(_baustein("agenda.html"), {"punkte": zeilen})


def _als_titel(f: dict) -> dict:
    """Erster Schritt einer `clip`-Szene muss ein Titel sein: den ersten brauchbaren Text uebernehmen."""
    for k in ("zeile", "ueberschrift", "zitat", "zahl"):
        if f.get(k):
            return {"zeile": str(f[k]), "unterzeile": str(f.get("unterzeile") or f.get("einheit")
                                                          or f.get("erklaerung") or f.get("von") or "")}
    return {"zeile": "", "unterzeile": ""}


def komposition(szene: dict, plan: list[Schritt], marke: dict, ton_datei: str, dauer_s: float) -> str:
    """Komplettes HTML der Szene (siehe README der video_werkbank fuer das Schema). `ton_datei` steht relativ zur
    HTML-Datei; Schriften unter `schriften/` und das Logo (`logo_name`) liegen ebenfalls daneben."""
    marke = marke if isinstance(marke, dict) else {}
    plan = list(plan)
    if szene.get("bildart") == "clip" and plan and plan[0].vorlage != "titel":
        plan[0] = Schritt("titel", _als_titel(plan[0].felder), plan[0].start_s, plan[0].dauer_s, [])
    variante = int(szene.get("variante") or 0) % VARIANTEN
    akzent = _farbe(marke.get("akzent")) or _farbe(VORGABE_AKZENT)
    schriften, schrift_variablen = _schriften_css(marke)
    auf_akzent = GRUND if _helligkeit(akzent) > 0.30 else "#FFFFFF"
    variablen = (f"--grund: {GRUND}; --text: {TEXT}; --leise: {LEISE}; --akzent: {_hex(akzent)}; "
                 f"--akzent-text: {_hex(_aufhellen(akzent))}; --auf-akzent: {auf_akzent}; "
                 f"--linie: rgba(255,255,255,.12); --linie-stark: rgba(255,255,255,.34); {schrift_variablen}")

    teile = []
    for i, p in enumerate(plan):
        deko = ""
        if variante == 1:
            deko = '<div class="deko"></div>'
        elif variante == 2:
            deko = f'<div class="deko"><div class="nummer">{i + 1:02d}</div></div>'
        zoom = f' class="bewegung zoom" style="--zoom-dauer:{p.dauer_s:.2f}s"' if _steht_still(p) else ' class="bewegung"'
        teile.append(
            f'<div id="schritt{i + 1}" class="clip schritt v{variante} t-{p.vorlage}" data-start="{p.start_s:.3f}" '
            f'data-duration="{p.dauer_s:.3f}" data-track-index="1">{deko}'
            f'<div{zoom}>{_inhalt(p)}</div></div>')

    logo = logo_name(marke)
    blende = ""
    if szene.get("uebergang") == "blende":
        blende = (f'<div class="blende ein"></div>'
                  f'<div class="blende aus" style="animation-delay:{max(0.0, dauer_s - BLENDE_S):.3f}s"></div>')
    return _fuellen(_baustein("rahmen.html"), {
        "schriften": schriften, "variablen": variablen, "stil": _baustein("stil.css"),
        "dauer": f"{dauer_s:.3f}", "schritte": "\n".join(teile),
        "fortschritt": f'<div class="fortschritt"><i style="animation-duration:{dauer_s:.3f}s"></i></div>',
        "logo": f'<img class="logo" src="{_e(logo)}" alt="">' if logo else "",
        "blende": blende, "ton": _e(ton_datei)})
