"""Verstehen vor Planen: ehrliche Saetze statt Planer-Lauf, Space der App als Signal.

Befund (Rundgang 23.11.2026): 13 von 16 Lesesaetzen ausserhalb des Ideen-Bereichs gingen
mit schwachem Cosinus (0,35 bis 0,72) als ``medium`` an den LLM-Planer; der antwortete mit
Plaenen aus fremden Spaces, Zeitueberschreitung oder Rohtext ("Multi-hop plan: ...").

Hier steht nur Entscheidung, kein Aufruf:

* :func:`space_aus_kontext` liest den Space, in dem die App gerade steht (``context.space``).
* :func:`unter_schwelle` sagt, ob eine Klassifikation zu schwach ist, um einen Planer zu starten.
* :func:`nicht_verstanden` / :func:`kein_weg` bauen den deutschen Satz mit Beispielen aus dem Space.
* :func:`feste_lesefrage` bindet die haeufigste Lesefrage eines Space an einen Weg, der laeuft.
* :func:`genannter_space` erkennt einen Space beim Namen (starkes Signal vor dem Planer).
* :func:`gemischter_plan` erkennt Planer-Plaene ohne eindeutigen Space. Der Space der App ist
  ein Gewicht, keine Grenze: ein eindeutiger Plan in einen anderen Space laeuft (mit Sprung).
* :func:`ohne_antwort` / :func:`rohtext` sind das Auffangnetz: kein Erfolg ohne Ergebnis,
  kein Rohtext im Chat.

Alles wirft nie.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple


@dataclass(frozen=True)
class Space:
    name: str
    # Saetze, die in diesem Space nachweislich laufen (fester Weg, kein Planer).
    beispiele: Tuple[str, ...] = ()
    # Faehigkeiten, die zu diesem Space gehoeren (Vorsilben oder ganze Namen).
    faehigkeiten: Tuple[str, ...] = field(default=())


# Kennungen wie die App sie fuehrt (renderer/index.html, external-spaces.js).
SPACES: Dict[str, Space] = {
    "ideas": Space("Ideen", (
        "Zeig die Notizen dieser Bubble",
        "Halt fest: Preise im Januar prüfen",
        "Ordne die Notizen im Raster",
    ), ("idea.", "idea_", "ideen_", "bubble.", "bubble_", "aenderung_zurueck", "bewertung_begruenden")),
    "projects": Space("Project Space", (), ("code_", "coding_")),
    "desktop": Space("Desktop", (), ("desktop_", "desktop.")),
    "swedesign": Space("SWE Design"),
    "roarboot": Space("Rowboat", (), ("rowboat_", "rowboat.")),
    "clawport": Space("Dashboard", ("Welche geplanten Aufgaben gibt es?",), ("schedule_", "schedule.")),
    "video": Space("Video Studio", (), ("video_", "video.")),
    "agentfarm": Space("Agent Farm", (), ("agentfarm_", "agentfarm.")),
    "thebrain": Space("The Brain"),
    "flowzen": Space("Blue Rose", (), ("rose.", "rose_", "flowzen")),
    "mirofish": Space("MiroFish", (), ("mirofish.", "mirofish_")),
    "sales": Space("Sales", (), ("sales.", "sales_")),
    "marketing": Space("Marketing", (), ("marketing.", "marketing_")),
    "learning": Space("Lernen", (), ("learning.", "learning_")),
    "events": Space("Events", (), ("event.", "event_")),
    "orders": Space("Aufträge", ("Zeig meine Aufträge",), ("schedule_", "schedule.")),
}

# Andere Schreibweisen (Verzeichnis, aeltere App-Staende) auf die Kennung der App.
_ALIASE = {
    "ideen": "ideas", "bubbles": "ideas", "coding": "projects", "project": "projects",
    "rowboat": "roarboot", "dashboard": "clawport", "bluerose": "flowzen", "wellness": "flowzen",
    "lernen": "learning", "auftraege": "orders", "aufträge": "orders", "schedule": "orders",
    "event-management": "events", "swe_design": "swedesign", "swe-design": "swedesign",
}

FEHLERSATZ = "Das hat nicht geklappt. Geändert habe ich nichts, soweit ich sehe."
ROH_ERFOLG_SATZ = "Der Schritt lief, aber die Antwort kann ich nicht lesbar wiedergeben."


def regel_an() -> bool:
    """Kill-Switch ``VERSTEHEN_ROUTING=0`` fuer Schwelle, Space-Wache und Saetze ohne Weg."""
    return os.environ.get("VERSTEHEN_ROUTING", "1") not in ("0", "false", "False")


def space_aus_kontext(kontext: Any) -> Optional[str]:
    """Kennung des Space aus ``context.space`` oder ``None`` (unbekannt oder nicht gesendet)."""
    try:
        if not isinstance(kontext, dict):
            return None
        roh = kontext.get("space")
        if not isinstance(roh, str):
            return None
        kennung = roh.strip().lower()
        kennung = _ALIASE.get(kennung, kennung)
        return kennung if kennung in SPACES else None
    except Exception:  # noqa: BLE001
        return None


def space_name(space: Optional[str]) -> str:
    eintrag = SPACES.get(space or "")
    return eintrag.name if eintrag else ""


# Spaces ohne laufenden Weg (Rundgang 23.11.2026 und Inventur der Lesewege): kein Dienst,
# kein Werkzeug oder kein Agent, der eine Lesefrage dort beantworten kann.
OHNE_WEG = frozenset({"swedesign", "projects", "learning", "events", "marketing", "agentfarm", "thebrain"})


def _beispielteil(space: Optional[str]) -> str:
    eintrag = SPACES.get(space or "")
    if eintrag is None:
        return ""
    if eintrag.beispiele:
        saetze = " oder ".join(f"„{b}“" for b in eintrag.beispiele[:3])
        return f" In {eintrag.name} kann ich z. B. {saetze}."
    if space in OHNE_WEG:
        return f" Im Bereich „{eintrag.name}“ gibt es dafür noch keinen Weg."
    return ""


def nicht_verstanden(space: Optional[str]) -> str:
    """"Das habe ich nicht verstanden. In <Space> kann ich z. B. ..." (ohne Space: nur der Anfang)."""
    return "Das habe ich nicht verstanden." + _beispielteil(space)


def kein_weg(space: Optional[str]) -> str:
    """Ehrlicher Satz fuer einen genannten Space ohne laufenden Weg, statt eines fremden Ergebnisses."""
    name = space_name(space) or "diesem Bereich"
    return f"Im Bereich „{name}“ kann ich das noch nicht; dafür gibt es noch keinen Weg. Geändert habe ich nichts."


# ── Space beim Namen ───────────────────────────────────────────────────────
# Eigene, unverwechselbare Namen gelten ueberall im Satz; Allerweltswoerter (Desktop,
# Video, Lernen ...) nur mit "im/in/aus dem ..." davor, mit Space/Bereich/Reiter danach
# oder als Wortteil mit Bindestrich ("Marketing-Kampagnen").
_EIGENE_NAMEN = {
    "swedesign": ("swe design", "swe-design", "swedesign"),
    "roarboot": ("rowboat",),
    "agentfarm": ("agent farm", "agent-farm", "agentfarm"),
    "thebrain": ("the brain",),
    "flowzen": ("blue rose", "blaue rose", "flowzen"),
    "mirofish": ("mirofish", "miro fish"),
    "video": ("video studio", "video-studio"),
    "projects": ("project space", "projekt-space", "projekt space"),
    "clawport": ("dashboard", "clawport"),
}
_ALLERWELTSNAMEN = {
    "ideas": ("ideen",), "desktop": ("desktop",), "video": ("video",), "sales": ("sales",),
    "marketing": ("marketing",), "learning": ("lernen",), "events": ("events", "event"),
    "orders": ("aufträgen", "aufträge", "auftraege"),
}
_VOR = r"(?:im|in|ins|aus|zum|zur|beim|bei)\s+(?:dem\s+|der\s+|den\s+|die\s+|das\s+)?(?:bereich\s+|space\s+|reiter\s+)?"
_NACH = r"[- ]?(?:space|bereich|reiter|tab)\b"


_BUBBLE = re.compile(r"\bbubbles?\b", re.IGNORECASE)
_IN_BUBBLE = re.compile(
    r"\s*\b(?:in|zu|an|bei|für|fuer)\s+(?:der|die|diese[rn]?)\s+bubble\s+[„\"']?"
    r"(?P<name>[^„“”\"'.,!?]{1,80}?)[“”\"']?\s*(?=$|[.,!?])", re.IGNORECASE)


def bubble_name_im_satz(satz: Any) -> Tuple[Optional[str], str]:
    """("Stadtfest", "Halt fest: Preise prüfen") aus "Halt fest: Preise prüfen in der Bubble Stadtfest".

    ``(None, satz)``, wenn der Satz keine Bubble beim Namen nennt. Wirft nie.
    """
    try:
        if not isinstance(satz, str):
            return None, ""
        m = _IN_BUBBLE.search(satz)
        if not m or not m.group("name").strip():
            return None, satz
        rest = (satz[:m.start()] + satz[m.end():]).strip()
        return m.group("name").strip(), rest
    except Exception:  # noqa: BLE001
        return None, satz if isinstance(satz, str) else ""


def _titel_gleich(a: Any, b: Any) -> bool:
    return re.sub(r"\s+", " ", str(a or "")).strip().casefold() == re.sub(r"\s+", " ", str(b or "")).strip().casefold()


def bubble_waehlen(name: str, zeilen: Any) -> Tuple[Optional[dict], str]:
    """Genau eine Bubble mit diesem Titel, sonst eine Rueckfrage. ``zeilen=None``: Liste nicht lesbar."""
    if zeilen is None:
        return None, "Die Liste der Bubbles kann ich gerade nicht lesen. Öffne die Bubble im Ideen-Bereich."
    treffer = {z["id"]: z for z in zeilen
               if isinstance(z, dict) and z.get("id") and _titel_gleich(z.get("title"), name)}
    if len(treffer) == 1:
        z = next(iter(treffer.values()))
        return {"id": z["id"], "title": z.get("title") or name}, ""
    if len(treffer) > 1:
        return None, (f"Es gibt {len(treffer)} Bubbles mit dem Titel „{name}“. "
                      "Öffne die gemeinte im Ideen-Bereich und sag es dort noch einmal.")
    return None, f"Ich finde keine Bubble mit dem Titel „{name}“."


def ideen_satz_von_aussen(satz: Any) -> bool:
    """Darf ein Satz aus einem anderen Reiter die festen Ideen-Saetze versuchen?

    Nur wenn er eine Bubble oder den Ideen-Bereich nennt ("Halt fest: ... in der Bubble X");
    sonst griffen kurze Ideen-Saetze wie "Zurück" aus jedem Reiter ins Ideen-Canvas.
    """
    try:
        return isinstance(satz, str) and (bool(_BUBBLE.search(satz)) or genannter_space(satz) == "ideas")
    except Exception:  # noqa: BLE001
        return False


def genannter_space(satz: Any) -> Optional[str]:
    """Space, den der Satz beim Namen nennt, oder ``None``. Wirft nie."""
    try:
        if not isinstance(satz, str):
            return None
        text = " " + re.sub(r"\s+", " ", satz.lower()) + " "
        for kennung, namen in _EIGENE_NAMEN.items():
            for name in namen:
                if re.search(rf"(?<![a-zäöü]){re.escape(name)}(?![a-zäöü])", text):
                    return kennung
        for kennung, namen in _ALLERWELTSNAMEN.items():
            for name in namen:
                n = re.escape(name)
                if (re.search(rf"\b{_VOR}{n}\b", text) or re.search(rf"\b{n}{_NACH}", text)
                        or re.search(rf"\b{n}-[a-zäöü]", text)):
                    return kennung
        return None
    except Exception:  # noqa: BLE001
        return None


# ── Schwelle ────────────────────────────────────────────────────────────────

# Geeicht 23.11.2026 mit echten Vektoren (scripts/verstehen_schwelle_messen.py, 22 echte und
# 20 Unsinn-Saetze, Standard-Anker): hoechster Unsinn-Satz auf einer Planer-Stufe lag bei
# 0,675 ("lorem ipsum ..." -> hard). 0,68 laesst keinen Unsinn an den Planer.
SCHWELLE_VORGABE = 0.68


def schwelle() -> float:
    """Cosinus, unter dem ein Satz als nicht verstanden gilt (Env ``VERSTEHEN_MIN_COS``)."""
    try:
        return float(os.environ.get("VERSTEHEN_MIN_COS", str(SCHWELLE_VORGABE)))
    except ValueError:
        return SCHWELLE_VORGABE


def unter_schwelle(klassifikation: Any) -> bool:
    """True, wenn der naechste Anker zu weit weg ist, um einen Planer zu starten.

    Nur fuer Cosinus-Ergebnisse (``qwen-cosine``/``qwen-cosine-lowconf``) auf den Stufen,
    die sonst zum Planer gingen. Heuristik ohne Cosinus wird nicht beurteilt: dort gibt es
    keine ehrliche Zahl. ``VERSTEHEN_ROUTING=0`` schaltet die Regel ab.
    """
    try:
        if os.environ.get("VERSTEHEN_ROUTING", "1") in ("0", "false", "False"):
            return False
        if not isinstance(klassifikation, dict):
            return False
        if klassifikation.get("method") not in ("qwen-cosine", "qwen-cosine-lowconf"):
            return False
        if klassifikation.get("level") not in ("medium", "hard", "insane"):
            return False
        score = klassifikation.get("score")
        return isinstance(score, (int, float)) and float(score) < schwelle()
    except Exception:  # noqa: BLE001
        return False


# ── Regel "Planer nur im Space mit Weg" ─────────────────────────────────────
# Eichung 23.11.2026: Der Cosinus trennt Sinn und Unsinn kaum (Umschreibung 0,62, Unsinn bis
# 0,675). Solange das so ist, entscheidet er nicht. Ein Satz ohne festen Treffer und ohne
# Space-Namen darf nur dort an den Planer, wo es einen laufenden Weg gibt (heute: Ideen), und
# nur, wenn er kein Kurztext ist. Ueberall sonst: "nicht verstanden" mit Beispielen, kein
# teurer Fehllauf. Schalter PLANER_NUR_MIT_WEG (Vorgabe an), Spaces VERSTEHEN_PLANER_SPACES.

def planer_nur_mit_weg() -> bool:
    return regel_an() and os.environ.get("PLANER_NUR_MIT_WEG", "1") not in ("0", "false", "False")


def planer_spaces() -> frozenset:
    roh = os.environ.get("VERSTEHEN_PLANER_SPACES", "ideas")
    return frozenset(_ALIASE.get(s.strip().lower(), s.strip().lower()) for s in roh.split(",") if s.strip())


def kurztext(satz: Any) -> bool:
    """Unter drei Woertern: offensichtlicher Kurztext, kein Auftrag fuer den Planer."""
    return not isinstance(satz, str) or len(re.findall(r"[\wäöüÄÖÜß-]+", satz)) < 3


def planer_erlaubt(space: Optional[str], satz: Any) -> bool:
    """Darf ein Satz ohne festen Treffer an den Planer? Nur im Space mit Weg und kein Kurztext."""
    return space in planer_spaces() and not kurztext(satz)


# ── Feste Lesesaetze ────────────────────────────────────────────────────────

_ZEIG = r"(?:zeig(?:e)?|liste|nenn(?:e)?|was sind|welche)"
_FESTE_LESEFRAGEN = (
    # Aufträge / Dashboard: geplante Aufgaben aus dem Schedule-Space (direct, laeuft).
    (re.compile(rf"^(?:{_ZEIG}\s+(?:mir\s+)?(?:meine|die|alle)?\s*(?:aufträge|auftraege|geplanten aufgaben)"
                r"|welche geplanten aufgaben gibt es|was ist geplant|was steht an geplanten aufgaben)\b",
                re.IGNORECASE),
     "schedule_list", ("orders", "clawport", None)),
)

# Saetze, deren Weg nachweislich nicht laeuft: ehrlich sagen statt Planer.
_NOCH_NICHT = (
    (re.compile(r"\bvideo-?projekte?\b", re.IGNORECASE), ("video", None),
     "Die Liste der Videoprojekte kann ich noch nicht lesen; der Editor im Video Studio zeigt sie. "
     "Geändert habe ich nichts."),
)


def feste_lesefrage(satz: Any, space: Optional[str]) -> Optional[Dict[str, str]]:
    """Haeufigste Lesefrage eines Space: ``{"capability": ...}`` oder ``{"antwort": ...}``.

    ``None``: kein fester Satz. Ein Satz aus dem Ideen-Space wird hier nie gebunden (dort
    entscheidet die Ideen-Abkuerzung).
    """
    try:
        if not isinstance(satz, str) or space == "ideas":
            return None
        text = re.sub(r"\s+", " ", satz).strip().rstrip("?.!").strip()
        if not text or len(text) > 200:
            return None
        for muster, faehigkeit, spaces in _FESTE_LESEFRAGEN:
            if space in spaces and muster.search(text):
                return {"capability": faehigkeit}
        for muster, spaces, antwort in _NOCH_NICHT:
            if space in spaces and muster.search(text):
                return {"antwort": antwort}
        return None
    except Exception:  # noqa: BLE001
        return None


_STATUS = {"active": "aktiv", "paused": "pausiert", "completed": "erledigt",
           "cancelled": "abgebrochen", "failed": "fehlgeschlagen"}


def _titel(wert: Any) -> str:
    text = re.sub(r"[\x00-\x1f]+", " ", str(wert or "")).strip()
    return (text[:80] + " …") if len(text) > 80 else text


def antwort_schedule_list(ergebnis: Any) -> Optional[str]:
    """Deutscher Satz fuer ``schedule_list``; ``None`` wenn das Ergebnis nicht lesbar ist."""
    try:
        if isinstance(ergebnis, dict) and isinstance(ergebnis.get("result"), dict):
            ergebnis = ergebnis["result"]
        if not isinstance(ergebnis, dict) or ergebnis.get("success") is not True:
            return None
        aufgaben = ergebnis.get("tasks")
        if not isinstance(aufgaben, list):
            return None
        if not aufgaben:
            return "Es sind keine geplanten Aufgaben eingetragen."
        teile = []
        for aufgabe in aufgaben[:10]:
            if not isinstance(aufgabe, dict):
                continue
            titel = _titel(aufgabe.get("title")) or "ohne Titel"
            zustand = _STATUS.get(str(aufgabe.get("status") or ""), "")
            teile.append(f"„{titel}“" + (f" ({zustand})" if zustand else ""))
        if not teile:
            return None
        anzahl = len(aufgaben)
        kopf = "Eine geplante Aufgabe" if anzahl == 1 else f"{anzahl} geplante Aufgaben"
        rest = f" und {anzahl - len(teile)} weitere" if anzahl > len(teile) else ""
        return f"{kopf}: " + ", ".join(teile) + rest + "."
    except Exception:  # noqa: BLE001
        return None


FESTE_ANTWORTEN = {"schedule_list": antwort_schedule_list}


# ── Space-Wache ────────────────────────────────────────────────────────────

def faehigkeit_space(capability: Any) -> Optional[str]:
    """Space einer Faehigkeit nach Vorsilbe, oder ``None`` (allgemein/unbekannt)."""
    name = str(capability or "")
    if not name:
        return None
    for kennung, eintrag in SPACES.items():
        for vorsilbe in eintrag.faehigkeiten:
            if name == vorsilbe or name.startswith(vorsilbe):
                return kennung
    return None


def plan_spaces(faehigkeiten: Iterable[Any]) -> set:
    """Die Spaces, denen die Schritte eines Plans sicher gehoeren (Aufträge = Dashboard)."""
    spaces = set()
    for cap in faehigkeiten:
        heimat = faehigkeit_space(cap)
        if heimat is not None:
            spaces.add("orders" if heimat == "clawport" else heimat)
    return spaces


def gemischter_plan(faehigkeiten: Iterable[Any]) -> bool:
    """True, wenn ein Planer-Plan mehrere Spaces etwa gleich gut trifft: keine eindeutige Zuordnung.

    Der Space der App ist ein Gewicht, keine Grenze: Ein Plan, der eindeutig in einem
    anderen Space liegt, laeuft (mit Sprung). Nur ein Plan ueber mehrere Spaces ist unsicher.
    Allgemeine Faehigkeiten (Browser, Chat) zaehlen nicht.
    """
    try:
        return len(plan_spaces(faehigkeiten)) > 1
    except Exception:  # noqa: BLE001
        return False


# ── Auffangnetz ────────────────────────────────────────────────────────────

_ROH = re.compile(
    r"Multi-hop plan|steps succeeded|Traceback|\bTimeout\b|timed out|Exception\b|Error:|"
    r"\bNone\b|\{\s*'[a-z_]+'\s*:|^\s*[\[{]|<tool_call>|unavailable|unresolv",
    re.IGNORECASE)


def rohtext(text: Any) -> bool:
    """True, wenn der Text roh aus Technik stammt und nicht in den Chat gehoert."""
    if not isinstance(text, str):
        return True
    t = text.strip()
    if not t:
        return True
    return bool(_ROH.search(t))


_KEINE_ANTWORT = re.compile(r"timeout|timed out|unavailable|not reachable|nicht erreichbar|"
                            r"keine lesbare antwort|no response", re.IGNORECASE)


def ohne_antwort(ergebnis: Any) -> bool:
    """True, wenn das Ziel eines Schritts nichts Verwertbares geantwortet hat.

    Leer (None, "", {}, []), ausdrueckliche Absage (success/ok False, nur ``error``) oder
    ein Text, der Zeitueberschreitung/Unerreichbarkeit meldet.
    """
    try:
        if ergebnis is None:
            return True
        if isinstance(ergebnis, str):
            return not ergebnis.strip() or bool(_KEINE_ANTWORT.search(ergebnis[:300]))
        if isinstance(ergebnis, (list, tuple)):
            return False
        if isinstance(ergebnis, dict):
            if not ergebnis:
                return True
            if ergebnis.get("success") is False or ergebnis.get("ok") is False:
                return True
            if ergebnis.get("isError") is True:
                return True
            if set(ergebnis) <= {"error", "message", "status"} and ergebnis.get("error"):
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


def schritte_ohne_antwort(executed: Any) -> list:
    """Kennungen der Schritte, die als ok galten, deren Ziel aber nichts geantwortet hat."""
    try:
        if not isinstance(executed, dict):
            return []
        return [sid for sid, s in executed.items()
                if isinstance(s, dict) and s.get("ok") and not s.get("pending")
                and "result" in s and ohne_antwort(s["result"])]
    except Exception:  # noqa: BLE001
        return []
