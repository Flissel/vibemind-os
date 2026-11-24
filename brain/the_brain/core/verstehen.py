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
    ), ("idea.", "idea_", "ideen_", "bubble.", "bubble_", "aenderung_zurueck", "bewertung_begruenden",
        "bewertung_entfernen")),
    "projects": Space("Project Space", (
        "Wie ist der Stand im Project Space?",
        "Zeig mir die letzten Läufe",
        "Zeig mir das Ergebnis des letzten Laufs",
    ), ("code_", "coding_")),
    "desktop": Space("Desktop", ("Welche Fenster sind offen?",), ("desktop_", "desktop.")),
    "swedesign": Space("SWE Design"),
    "roarboot": Space("Rowboat", ("Welche Agenten gibt es in Rowboat?",), ("rowboat_", "rowboat.")),
    "clawport": Space("Dashboard", ("Welche geplanten Aufgaben gibt es?",), ("schedule_", "schedule.")),
    "video": Space("Video Studio", (), ("video_", "video.")),
    "agentfarm": Space("Agent Farm", (), ("agentfarm_", "agentfarm.")),
    "thebrain": Space("The Brain", ("Woran denkt das Gehirn gerade?",), ("thebrain_", "thebrain.")),
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
        if kennung == "ideas" and not ideen_kontext_belegt(kontext):
            # Verteidigung gegen falschen Kontext (Nachmessung 23.11.2026: die App schickte aus
            # jedem Reiter 'ideas'). Im Ideen-Bereich schickt sie immer bubble, cards oder bubbles
            # mit; fehlt alles, gilt der Bereich als unbekannt und der Planer bleibt zu.
            return None
        return kennung if kennung in SPACES else None
    except Exception:  # noqa: BLE001
        return None


_IDEEN_BELEGE = ("bubble", "cards", "bubbles")


def ideen_kontext_belegt(kontext: Any) -> bool:
    """True, wenn der Kontext bubble, cards oder bubbles mitbringt (nicht None)."""
    try:
        return isinstance(kontext, dict) and any(kontext.get(k) is not None for k in _IDEEN_BELEGE)
    except Exception:  # noqa: BLE001
        return None


def space_name(space: Optional[str]) -> str:
    eintrag = SPACES.get(space or "")
    return eintrag.name if eintrag else ""


# Spaces ohne laufenden Weg (Rundgang 23.11.2026 und Inventur der Lesewege): kein Dienst,
# kein Werkzeug oder kein Agent, der eine Lesefrage dort beantworten kann.
# The Brain hat seit 23.11. den festen Satz "Woran denkt das Gehirn gerade?" (core/feste_lesesaetze.py).
OHNE_WEG = frozenset({"swedesign", "projects", "learning", "events", "marketing", "agentfarm"})


def _beispielteil(space: Optional[str]) -> str:
    eintrag = SPACES.get(space or "")
    if eintrag is None:
        return ""
    if eintrag.beispiele:
        saetze = " oder ".join(f"„{b}“" for b in eintrag.beispiele[:3])
        return f" In {eintrag.name} kann ich z. B. {saetze}."
    if space in OHNE_WEG:
        return f" Im Bereich „{eintrag.name}“ gibt es dafür noch keinen Weg.{AUSWEG}"
    # Lauf 4 der Pruefung: aus Rowboat, Blue Rose, MiroFish und Sales kam nur das nackte
    # "nicht verstanden". Ohne belegten Beispielsatz: Bereich nennen und ehrlich bleiben.
    # Lauf 8: dazu ein Satz, der in jedem Bereich geht, als Ausweg.
    return f" Im Bereich „{eintrag.name}“ kenne ich noch keinen Satz, den ich sicher ausführen kann.{AUSWEG}"


AUSWEG = " Was überall geht: „Welche Bubbles gibt es?“"


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
    "roarboot": ("rowboat", "roarboot"),
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
# Lauf 7: "In der Bubble Y: Halt fest: X" am Satzanfang. Ohne eigenes Muster las _IN_BUBBLE
# den ganzen Rest als Bubble-Namen ("Y: Halt fest: X"). Der Name endet am Doppelpunkt/Komma.
_BUBBLE_VORNE = re.compile(
    r"^\s*(?:in|zu|an|bei|für|fuer)\s+(?:der|die|diese[rn]?)\s+bubble\s+[„\"']?"
    r"(?P<name>[^„“”\"':,.!?]{1,80}?)[“”\"']?\s*[:,]\s*(?P<rest>\S.*)$", re.IGNORECASE | re.DOTALL)


def bubble_name_im_satz(satz: Any) -> Tuple[Optional[str], str]:
    """("Stadtfest", "Halt fest: Preise prüfen") aus "Halt fest: Preise prüfen in der Bubble Stadtfest"
    oder aus "In der Bubble Stadtfest: Halt fest: Preise prüfen".

    ``(None, satz)``, wenn der Satz keine Bubble beim Namen nennt. Wirft nie.
    """
    try:
        if not isinstance(satz, str):
            return None, ""
        vorne = _BUBBLE_VORNE.match(satz)
        if vorne and vorne.group("name").strip():
            return vorne.group("name").strip(), vorne.group("rest").strip()
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
        kandidaten = [{"id": z["id"], "title": z.get("title") or name} for z in treffer.values()][:9]
        liste = "; ".join(f"{n}. „{k['title']}“" for n, k in enumerate(kandidaten, start=1))
        return None, (f"Es gibt {len(treffer)} Bubbles mit dem Titel „{name}“: {liste}. "
                      "Welche meinst du? Sag z. B. „die erste“ oder „2“.")
    return None, f"Ich finde keine Bubble mit dem Titel „{name}“."


# ── Mehrere Bubbles gleichen Titels aus einem fremden Reiter: Antwort "die zweite" ──
BUBBLE_WAHL_GILT_S = 300.0
_bubble_offen: Dict[str, Any] = {}       # {"rest": str, "kandidaten": [...], "bis": float}


def bubble_frage_merken(rest: str, name: str, zeilen: Any) -> None:
    """Nach der Rueckfrage aus ``bubble_waehlen`` (mehrere Treffer) die Kandidaten merken."""
    import time

    kandidaten = [{"id": z["id"], "title": z.get("title") or name} for z in (zeilen or [])
                  if isinstance(z, dict) and z.get("id") and _titel_gleich(z.get("title"), name)]
    kandidaten = list({k["id"]: k for k in kandidaten}.values())[:9]
    _bubble_offen.clear()
    if len(kandidaten) >= 2:
        _bubble_offen.update({"rest": rest, "kandidaten": kandidaten, "bis": time.time() + BUBBLE_WAHL_GILT_S})


def bubble_antwort(satz: Any) -> Optional[Dict[str, Any]]:
    """Fuer jeden Satz zuerst aufrufen. ``None``: keine Antwort (eine offene Frage verfaellt);
    ``{"bubble": ..., "rest": ...}``: gewaehlt; ``{"frage": ...}``: passt zu keiner."""
    import time

    from .ideen_abkuerzung import _wahl

    offen = dict(_bubble_offen)
    _bubble_offen.clear()
    if not offen or offen.get("bis", 0) < time.time() or not isinstance(satz, str):
        return None
    # Nur eine eindeutige Wahl ("die zweite", "2", "die letzte") gilt; jeder andere Satz ist
    # ein neuer Satz und laesst die Frage verfallen (nie raten, nie einen Satz verschlucken).
    wahl = _wahl(satz.strip().rstrip(".!"), offen["kandidaten"])
    if wahl is None or wahl < 0:
        return None
    return {"bubble": offen["kandidaten"][wahl], "rest": offen["rest"]}


# ── Bereichswoerter (Nachmessung 23.11.2026) ───────────────────────────────
# "Was steht in meinem Journal?" wurde als Notizname gelesen ("Ich finde keine Karte
# meinem Journal"), "Welche Events stehen an?" lief zu den Auftraegen. Diese Woerter sind
# Bereiche, keine Kartentitel (Liste in core/ideen_abkuerzung.BEREICHSWOERTER): ohne Karte
# genau dieses Namens kommt "nicht verstanden" mit dem Space des Wortes als Signal.
_FRAGEANFANG = (r"^(?:was\s+steht\s+(?:in|im|bei|auf)|was\s+gibt\s+es\s+(?:in|im|bei)|welche|"
                r"zeig(?:e)?(?:\s+mir)?|liste|wie\s+viele)\s+")


def bereichsfrage(satz: Any) -> Optional[Dict[str, Optional[str]]]:
    """Lesefrage nach einem Bereich ("Welche Events stehen an?") -> ``{"wort", "space"}``, sonst ``None``.

    ``space`` ist der Space des Wortes (Events -> "events") als Signal fuer die App, sonst ``None``.
    Nennt der Satz selbst einen Space ("Kontakte im Sales"), entscheidet dieser Name, nicht das Wort.
    """
    try:
        from .ideen_abkuerzung import BEREICHSWOERTER, BESITZ

        if not isinstance(satz, str) or genannter_space(satz):
            return None
        m = re.match(rf"{_FRAGEANFANG}(?:{BESITZ}\s+)?(?:n(?:ä|ae)chsten\s+|kommenden\s+|alle\s+)?"
                     r"(?P<wort>[a-zäöü]+)\b", re.sub(r"\s+", " ", satz).strip(), re.IGNORECASE)
        wort = m.group("wort").casefold() if m else ""
        if wort not in BEREICHSWOERTER:
            return None
        return {"wort": wort, "space": BEREICHSWOERTER[wort]}
    except Exception:  # noqa: BLE001
        return None


_FESTHALTEN = re.compile(r"^\s*(?:halt(?:e)?\s+fest|notiz|notiere|schreib(?:e)?\s+auf|merk(?:e)?\s+dir)\b",
                         re.IGNORECASE)


def bubble_aus_dem_satz(satz: Any) -> bool:
    """"Halt fest: X in der Bubble Y": die Bubble kommt aus dem Satz, nie aus dem Kontext.

    Nachmessung 23.11.2026: Die App schickte aus anderen Reitern alten Ideen-Kontext mit; der
    Satz lief dann in der alten Bubble, und der Titel behielt "in der Bubble Y". Solche Saetze
    gehen darum immer ueber die Bubble-Liste (wie aus einem fremden Reiter). Wirft nie.
    """
    try:
        if not isinstance(satz, str):
            return False
        name, rest = bubble_name_im_satz(satz)
        # Der Festhalte-Satz steht vorne ("Halt fest: X in der Bubble Y") oder nach der Bubble
        # ("In der Bubble Y: Halt fest: X").
        return bool(name) and bool(_FESTHALTEN.match(rest))
    except Exception:  # noqa: BLE001
        return False


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
# 0,675). Im Betrieb (Embedding-Dienst des Stacks, gleiche Messung) liegen echte Saetze bei
# 0,378-0,423 und Unsinn bei 0,353-0,420; auch eine untere Schranke (etwa 0,45) wiese dort jeden
# Satz ab und bleibt darum aus (55c, 24.11.2026). Solange das so ist, entscheidet er nicht. Ein Satz ohne festen Treffer und ohne
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


_KEINE_FAEHIGKEIT = frozenset({"", "unknown", "none", "null", "?", "unbekannt"})


def plan_ohne_faehigkeit(faehigkeiten: Iterable[Any], bekannte: Optional[Iterable[str]] = None) -> bool:
    """True, wenn ein Planer-Schritt keine Faehigkeit traegt, die das Gehirn kennt.

    Nachmessung nach Lauf 19 (55): Ein Unsinnssatz im Ideen-Bereich bekam vom Planer einen Schritt
    ohne Faehigkeit ("unknown"), lief 18 s, buchte eine Pruefung bei einem Agenten und endete mit dem
    Fehlersatz. Ein solcher Plan ist kein Auftrag, sondern "nicht verstanden". ``bekannte`` sind die
    Faehigkeiten des Registers; ohne Register zaehlt nur der leere oder "unknown"-Name.
    """
    try:
        bekannt = set(bekannte) if bekannte is not None else None
        for cap in faehigkeiten:
            name = str(cap or "").strip()
            if name.lower() in _KEINE_FAEHIGKEIT:
                return True
            if bekannt is not None and name not in bekannt:
                return True
        return False
    except Exception:  # noqa: BLE001
        return False


# ── Feste Lesesaetze ────────────────────────────────────────────────────────

_ZEIG = r"(?:zeig(?:e)?|liste|nenn(?:e)?|was sind|welche)"
_FESTE_LESEFRAGEN = (
    # Aufträge / Dashboard: geplante Aufgaben aus dem Schedule-Space (direct, laeuft).
    (re.compile(rf"^(?:{_ZEIG}\s+(?:mir\s+)?(?:meine|die|alle)?\s*(?:aufträge|auftraege|geplanten aufgaben)"
                r"|welche geplanten aufgaben gibt es|was ist geplant|was steht an geplanten aufgaben)\b",
                re.IGNORECASE),
     "schedule_list", None),
)
# Desktop, The Brain, Rowboat (23.11.2026): eigene Datei, hier nur angehaengt.
from .feste_lesesaetze import (  # noqa: E402
    antworten as _weitere_antworten, fehlersaetze as _weitere_fehlersaetze, lesefragen as _weitere_lesefragen,
    protokolle as _weitere_protokolle, vorpruefungen as _weitere_vorpruefungen,
)
# Die weiteren Saetze gelten nicht im Ideen-Bereich (dort entscheidet die Ideen-Abkuerzung),
# ausser denen, die den Space beim Namen nennen ("auf dem Desktop", "in Rowboat"): die gelten
# ueberall (core/feste_lesesaetze.py, Lauf 17).
_FESTE_LESEFRAGEN = _FESTE_LESEFRAGEN + _weitere_lesefragen(tuple(s for s in SPACES if s != "ideas") + (None,))

# Saetze, deren Weg nachweislich nicht laeuft: ehrlich sagen statt Planer.
_NOCH_NICHT = (
    (re.compile(r"\bvideo-?projekte?\b", re.IGNORECASE), ("video", None),
     "Die Liste der Videoprojekte kann ich noch nicht lesen; der Editor im Video Studio zeigt sie. "
     "Geändert habe ich nichts."),
)


_ZUSATZ_AM_ENDE = re.compile(r"(?:\s*[\(\[][^()\[\]]{0,60}[\)\]])+\s*$")


def feste_lesefrage(satz: Any, space: Optional[str]) -> Optional[Dict[str, str]]:
    """Haeufigste Lesefrage eines Space: ``{"capability": ...}`` oder ``{"antwort": ...}``.

    ``None``: kein fester Satz. Feste Lesesaetze gelten in jedem Reiter, unabhaengig von
    ``context.space`` (Nachmessung 23.11.2026: die App schickte aus jedem Reiter 'ideas', und
    "Welche geplanten Aufgaben gibt es?" lief ueber den Planer). Sie sind am Satzanfang
    verankert und treffen keinen Ideen-Satz. Die ehrlichen "noch nicht"-Saetze suchen dagegen
    irgendwo im Text und gelten nur in ihrem Space (nie im Ideen-Space, "Halt fest: ...").
    """
    try:
        if not isinstance(satz, str):
            return None
        text = re.sub(r"\s+", " ", satz).strip().rstrip("?.!").strip()
        if not text or len(text) > 200:
            return None
        # Lauf 16 (48): "Woran denkt das Gehirn gerade? (Skript 1)" lief ueber den Planer, weil die
        # Muster am Satzende verankert sind. Erst der Satz wie getippt (eine Klammer kann Inhalt sein,
        # etwa beim Bestaetigen), dann ohne Zusatz in Klammern am Ende.
        ohne = _ZUSATZ_AM_ENDE.sub("", text).rstrip("?.!").strip()
        for kandidat in dict.fromkeys(k for k in (text, ohne) if k):
            for muster, faehigkeit, spaces in _FESTE_LESEFRAGEN:
                # spaces None: gilt in jedem Reiter; sonst nur in den genannten.
                if (spaces is None or space in spaces) and muster.search(kandidat):
                    return {"capability": faehigkeit}
        if space == "ideas":
            return None
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


FESTE_ANTWORTEN = {"schedule_list": antwort_schedule_list, **_weitere_antworten()}

# Lauf 8: "Zeig meine Aufträge" antwortete mit den geplanten Aufgaben, ohne zu sagen, dass
# Auftraege an Agenten etwas anderes sind und in dieser Liste fehlen.
NUR_GEPLANT = " Aufträge an Agenten stehen nicht in dieser Liste."
_AUFTRAEGE_WORT = re.compile(r"\bauftr(?:ä|ae)ge\b", re.IGNORECASE)


def lese_zusatz(satz: Any, capability: str) -> str:
    """Ehrlicher Zusatz zu einer festen Leseantwort, abhaengig vom gefragten Satz; sonst ``""``."""
    if capability == "schedule_list" and isinstance(satz, str) and _AUFTRAEGE_WORT.search(satz):
        return NUR_GEPLANT
    return ""
# Satz fuer Plan-Verlauf und Protokolle, wenn die Antwort Bildschirminhalt traegt (Fenstertitel).
FESTE_PROTOKOLLE = _weitere_protokolle()
# Genauer Satz statt FEHLERSATZ, wenn ein fester Weg sagen kann, was fehlt (Ergebnis, Fehler).
FESTE_FEHLERSAETZE = _weitere_fehlersaetze()
# Vor dem Plan: Satz, wenn der Weg sicher nicht geht (Rowboat ohne Karten). Kein Plan, kein Sprung.
FESTE_VORPRUEFUNGEN = _weitere_vorpruefungen()


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


# Englische Auffangtexte (Chat-Handler, Synthese) verlassen das Gehirn nie (Nachmessung
# 23.11.2026: "hmm" -> "I'm not entirely sure, but the user asks about: hmm ..."). Erkannt an
# Funktionswoertern, nicht an Fachbegriffen: "Die Seite ist offen" bleibt deutsch.
_EN_WOERTER = frozenset(
    "the and is are was were you your i i'm it's not but what which this that with about "
    "for from have has can could would should will there here sure user asks sorry".split())
_DE_WOERTER = frozenset(
    "der die das den dem des und ist sind war nicht ich du sie es ein eine einen einem "
    "zu mit was wie im in auf für von hat habe kann gibt noch schon".split())


def englisch(text: Any) -> bool:
    """True, wenn ein Text ueberwiegend englisch ist (mindestens zwei englische Funktionswoerter
    und mehr englische als deutsche)."""
    if not isinstance(text, str):
        return False
    woerter = re.findall(r"[a-zäöüß']+", text.lower())
    en = sum(1 for w in woerter if w in _EN_WOERTER)
    de = sum(1 for w in woerter if w in _DE_WOERTER)
    return en >= 2 and en > de


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


def kein_schritt_gelungen(executed: Any) -> bool:
    """True, wenn ein Lauf keinen einzigen gelungenen oder angenommenen Schritt hat.

    Nachmessung (55), 24.11.2026: Ein Unsinnssatz im Ideen-Bereich bekam vom Planer einen
    Schritt auf eine bekannte Faehigkeit, der scheiterte; die Antwort war der Fehlersatz.
    Ohne jeden gelungenen Schritt hat der Planer den Satz nicht verstanden.
    """
    try:
        if not isinstance(executed, dict):
            return True
        for s in executed.values():
            if isinstance(s, dict) and (s.get("ok") or s.get("pending")):
                return False
        return True
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
