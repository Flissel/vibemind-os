"""Echter Code auf der Folie (26.11.2026). Ein Schritt mit der Vorlage `code` nennt nur die STELLE
(`quelle: {"datei": "src/x.py", "von": 84, "bis": 93}`); die Zeilen liest die Werkbank am PC selbst aus der Datei.
So steht auf einer Code-Folie nie Code, den das Modell sich ausgedacht hat.

Gelesen wird nur aus erlaubten Code-Ordnern: WERKBANK_CODE_WURZELN = "name=C:/pfad;name2=D:/pfad" (leer = keine,
dann gibt es keine Code-Folien). `datei` ist relativ zu einem der Ordner, wahlweise mit dessen Namen davor
("la_fungus_search/src/x.py"). Nichts ausserhalb der Ordner, keine Verweise nach oben, keine versteckten Dateien
(.env, .git), nur Textdateien bis 400 KB. Fehler sind kurze deutsche Saetze fuer die Nachfrage beim Modell."""
from __future__ import annotations

import os
from pathlib import Path

from spaces.marketing.claw import video_slides

DATEI_MAX_BYTES = 400_000
KUERZEL = "…"


def wurzeln() -> dict[str, Path]:
    """{Name: Ordner} aus WERKBANK_CODE_WURZELN; nur vorhandene Ordner."""
    aus: dict[str, Path] = {}
    for teil in os.environ.get("WERKBANK_CODE_WURZELN", "").split(";"):
        name, _, pfad = teil.partition("=")
        name, pfad = name.strip(), pfad.strip()
        if name and pfad and Path(pfad).is_dir():
            aus[name] = Path(pfad).resolve()
    return aus


def erlaubt() -> bool:
    return bool(wurzeln())


def _finden(datei: str) -> tuple[Path, str] | None:
    """(Datei, Anzeigename) in einem erlaubten Ordner, sonst None."""
    sauber = datei.replace("\\", "/").strip().lstrip("/")
    teile = [t for t in sauber.split("/") if t]
    if not teile or any(t in ("..", ".") or t.startswith(".") for t in teile) or ":" in sauber:
        return None
    for name, wurzel in wurzeln().items():
        for rel in ([teile[1:]] if teile[0] == name and len(teile) > 1 else []) + [teile]:
            kandidat = wurzel.joinpath(*rel).resolve()
            if wurzel in kandidat.parents and kandidat.is_file():
                return kandidat, "/".join(rel)
    return None


def zeilen_lesen(datei: str, von: int, bis: int) -> tuple[list[str], str] | str:
    """(Zeilen von..bis, Anzeigename) oder ein Fehlersatz."""
    if any(isinstance(x, bool) or not isinstance(x, int) for x in (von, bis)) or von < 1 or bis < von:
        return "code: quelle braucht von und bis als Zeilennummern (von höchstens bis)"
    hoechstens = video_slides.CODE_ZEILEN_MAX
    if bis - von + 1 > hoechstens or bis - von + 1 < video_slides.CODE_ZEILEN_MIN:
        return f"code: eine Code-Folie zeigt {video_slides.CODE_ZEILEN_MIN} bis {hoechstens} Zeilen"
    gefunden = _finden(datei) if isinstance(datei, str) else None
    if gefunden is None:
        return "code: die Datei gibt es in den erlaubten Code-Ordnern nicht"
    pfad, anzeige = gefunden
    try:
        if pfad.stat().st_size > DATEI_MAX_BYTES:
            return "code: die Datei ist zu gross"
        alle = pfad.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return "code: die Datei lässt sich nicht als Text lesen"
    if bis > len(alle):
        return f"code: die Datei hat nur {len(alle)} Zeilen"
    grenze = video_slides.CODE_ZEILE_MAX
    zeilen = [z.expandtabs(4).rstrip() for z in alle[von - 1:bis]]
    zeilen = [z if len(z) <= grenze else z[:grenze - 1] + KUERZEL for z in zeilen]
    if not any(z.strip() for z in zeilen):
        return "code: die genannten Zeilen sind leer"
    return zeilen, anzeige


def aufloesen(szene: dict) -> list[str]:
    """Fuellt in jedem Code-Schritt der Szene `zeilen`, `ab_zeile` und `datei` aus der Datei (an Ort und Stelle).
    Gibt Fehlersaetze zurueck; leer = gut. Ein Code-Schritt ohne `quelle` ist ein Fehler: Code kommt nur aus der Datei."""
    bild = szene.get("bild") if isinstance(szene, dict) else None
    schritte = bild.get("schritte") if isinstance(bild, dict) else None
    fehler: list[str] = []
    for i, st in enumerate(schritte if isinstance(schritte, list) else [], 1):
        if not isinstance(st, dict) or st.get("vorlage") != "code":
            continue
        if not erlaubt():
            fehler.append(f"Schritt {i}: Code-Folien sind nicht eingerichtet. Nimm eine andere Vorlage.")
            continue
        q = st.get("quelle")
        if not isinstance(q, dict):
            fehler.append(f"Schritt {i}: code braucht quelle mit datei, von und bis. Schreibe keinen Code selbst.")
            continue
        ergebnis = zeilen_lesen(q.get("datei"), q.get("von"), q.get("bis"))
        if isinstance(ergebnis, str):
            fehler.append(f"Schritt {i}: {ergebnis}")
            continue
        zeilen, anzeige = ergebnis
        markiert = st.get("markiert") if isinstance(st.get("markiert"), list) else []
        st["zeilen"], st["ab_zeile"] = zeilen, q["von"]
        st["datei"] = f"{anzeige}:{q['von']}-{q['bis']}"[-video_slides.CODE_DATEI_MAX:]
        st["markiert"] = [m for m in markiert if isinstance(m, int) and not isinstance(m, bool) and 0 <= m < len(zeilen)]
    return fehler
