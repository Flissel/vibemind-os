"""Schreiben der Video-Werkbank (11.11.2026): Prompts, Parser und die mechanische Quellenpruefung fuer Gliederung
und Szenentexte. Reine Textarbeit: kein Modellaufruf, kein Netz, kein Log (Prompts und Dokumenttexte werden nie
protokolliert). Der Arbeiter, der das Modell fragt, ruft diese Funktionen auf.

Die Quellenpruefung ist das Sicherheitsnetz des Produkts ("nichts erfunden"): streng und woertlich. Zahlen werden
nach Normalisierung von Tausenderpunkt und Dezimalkomma verglichen; eine Zahl im Sprechtext ohne Beleg faellt durch,
ein Beleg, dessen Zahl nicht woertlich in seiner Quelle steht, faellt durch, eine nicht geholte Quelle faellt durch.
Zahlen in Worten ("vierzig") sieht die Pruefung nicht; der Prompt verlangt deshalb Ziffern fuer belegte Zahlen.
Fehlersaetze sind deutsch: sie gehen an das Modell zurueck und werden dem Auftraggeber gezeigt.
"""
from __future__ import annotations

import json
import re

from spaces.marketing.claw import video_slides
from spaces.marketing.claw.video_kontext import Kontext
from spaces.marketing.claw.video_saetze import saetze

BLOCK = 10
SZENEN_MIN, SZENEN_MAX = 8, 15
WOERTER_MIN, WOERTER_MAX = 35, 110
SAETZE_MIN, SAETZE_MAX = 2, 8
BILDARTEN = ("slide", "avatar_gross", "avatar_slide", "clip")
UEBERGAENGE = ("schnitt", "blende")
AUFTRAG = "auftrag"
KI_HINWEIS = "Dieses Video wurde mit KI erstellt."
FEHLT_PRAEFIX = "dazu fehlt mir Material:"

_ZAHL = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*(?:\s?(?:%|€|Prozent|Euro|EUR)(?!\w))?", re.IGNORECASE)
_TEIL = re.compile(r"(\d+(?:[.,]\d+)*)\s?(%|€|prozent|euro|eur)?", re.IGNORECASE)
_TAUSEND = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?")


# --- Zahlen -----------------------------------------------------------------------------------

def _normal(zahl: str) -> str:
    """'1.500,50' -> '1500.5', '40,0' -> '40', '3.5' -> '3.5'; Punkt vor genau drei Ziffern ist Tausenderpunkt."""
    n = zahl.replace(".", "").replace(",", ".") if _TAUSEND.fullmatch(zahl) else zahl.replace(",", ".")
    return n.rstrip("0").rstrip(".") if "." in n else n


def _einheit(roh: str | None) -> str:
    e = (roh or "").casefold()
    return "%" if e in ("%", "prozent") else "€" if e in ("€", "euro", "eur") else ""


def _zahlen(text: str) -> list[tuple[str, str]]:
    """(normalisierte Zahl, Einheit '%'|'€'|'') aller Zahlen im Text."""
    ergebnis = []
    for t in _ZAHL.finditer(text or ""):
        m = _TEIL.fullmatch(t.group(0))
        if m:
            ergebnis.append((_normal(m.group(1)), _einheit(m.group(2))))
    return ergebnis


def _nur_zahlen(text: str) -> set[str]:
    return {z for z, _ in _zahlen(text)}


def _zahl_text(paar: tuple[str, str]) -> str:
    return paar[0].replace(".", ",") + (" " + paar[1] if paar[1] else "")


# --- JSON lesen -------------------------------------------------------------------------------

def _json(text: str) -> object:
    """Erstes JSON-Objekt (oder Liste) der Antwort, auch in ```-Zaeunen; sonst None."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    for auf, zu in (("{", "}"), ("[", "]")):
        a, b = t.find(auf), t.rfind(zu)
        if a != -1 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except ValueError:
                continue
    return None


def _text(wert: object) -> str:
    return wert.strip() if isinstance(wert, str) else ""


# --- Prompts ----------------------------------------------------------------------------------

_REGELN = """Drei Regeln gelten ohne Ausnahme:
1. Kein Sachinhalt ohne Beleg. Jede Tatsache, Zahl, Preis, Zeitangabe, Fähigkeit oder Aussage über Kunden
   steht wörtlich im Auftrag oder in einem mitgelieferten Dokument. Du nennst die Quelle mit ihrem exakten Namen
   (Dokumentname oder "auftrag"). Was nicht belegt ist, sagst du nicht.
2. Fehlt dir Material, erfindest du nichts. Du schreibst "dazu fehlt mir Material: <was genau>" (in der
   Gliederung unter "fehlt") und lässt die Aussage weg.
3. Kundennamen, Firmen- und Personennamen aus Kundenbeziehungen nennst du nur, wenn sie im Auftragstext stehen.
   Sie stehen nicht in Szenen, nur weil sie im Material vorkommen.
Du antwortest ausschließlich mit einem JSON-Objekt, ohne Text davor oder danach, ohne Markdown-Zaun."""


def _system(brief: str) -> str:
    return f"{_REGELN}\n\n# Schreibvorgabe\n\n{brief.strip()}"


def _auftrag_text(eingabe: dict) -> str:
    return (f"Thema: {eingabe.get('thema', '')}\nBeschreibung des Auftraggebers: {eingabe.get('beschreibung', '')}\n"
            f"Länge: {eingabe.get('minuten', '')} Minuten\nZielgruppe: {eingabe.get('zielgruppe', '')}")


def auftragstext(eingabe: dict) -> str:
    """Was der Auftraggeber selbst geschrieben hat (Quelle "auftrag" der Pruefung)."""
    return f"{_text(eingabe.get('thema'))}\n{_text(eingabe.get('beschreibung'))}".strip()


def _dokumente_text(kontext: Kontext) -> str:
    if not kontext.dokumente:
        return "(keine Dokumente gefunden)"
    return "\n\n".join(f"### {d.name}\n{d.text}" for d in kontext.dokumente)


GLIEDERUNG_FORM = """{"titel": "…", "hook": "ein Satz, der am Ende wiederholt wird",
 "stufen": [{"name": "Einstieg", "szenen": [{"kurz": "worum es in der Szene geht", "bildart": "slide|avatar_gross|avatar_slide|clip", "quellen": ["Dokumentname oder auftrag"]}]}],
 "fehlt": ["dazu fehlt mir Material: …"]}"""


def gliederung_prompt(eingabe: dict, kontext: Kontext, brief: str, wunsch: str = "",
                      vorige: dict | None = None) -> tuple[str, str]:
    """(system, nutzer) fuer die Gliederung. Mit `vorige` und `wunsch` ist es eine Ueberarbeitung."""
    minuten = eingabe.get("minuten")
    woerter = f" (etwa {int(minuten) * 200} gesprochene Wörter)" if isinstance(minuten, int) else ""
    teile = [
        "Schreibe die Gliederung eines Erklärvideos.", "# Auftrag\n" + _auftrag_text(eingabe),
        "# Material (Quellen mit ihren Namen)\n" + _dokumente_text(kontext),
        f"# Aufgabe\n- Gesamt {SZENEN_MIN} bis {SZENEN_MAX} Szenen über alle Stufen{woerter}.\n"
        "- Die erste Stufe heißt \"Einstieg\" (Ergebnis, Einwand, Agenda), die letzte enthält die ehrlichen Grenzen "
        "und die Wiederholung des Hooks. Dazwischen Stufen mit eigenem Ergebnis.\n"
        "- Jede Szene hat ein kurzes `kurz` (ein Satz, was gesagt und gezeigt wird), eine `bildart` und die "
        "`quellen`, aus denen ihre Sachaussagen kommen (leer, wenn sie keine hat). Erlaubte Quellen: "
        f"\"{AUFTRAG}\" und die Dokumentnamen oben, exakt geschrieben.\n"
        f"- `fehlt`: je fehlendem Material ein Satz \"{FEHLT_PRAEFIX} …\". Leere Liste, wenn nichts fehlt.",
        "# Antwortform (nur dieses JSON)\n" + GLIEDERUNG_FORM,
    ]
    if vorige is not None:
        teile.append("# Bisherige Gliederung\n" + json.dumps(vorige, ensure_ascii=False))
    if wunsch.strip():
        teile.append("# Wunsch des Auftraggebers zur Überarbeitung\n" + wunsch.strip()
                     + "\nÄndere nur, was der Wunsch verlangt; alles andere bleibt wie in der bisherigen Gliederung.")
    return _system(brief), "\n\n".join(teile)


def gliederung_lesen(text: str, geholt: set[str]) -> tuple[dict | None, list[str]]:
    """Gliederung aus der Modellantwort. (Gliederung, []) oder (None, Fehlersaetze). `geholt` = Namen der
    Dokumente im Kontext; als Quelle ist ausserdem "auftrag" erlaubt."""
    roh = _json(text)
    if not isinstance(roh, dict):
        return None, ["Die Antwort war kein JSON-Objekt."]
    fehler: list[str] = []
    titel, hook = _text(roh.get("titel")), _text(roh.get("hook"))
    if not titel:
        fehler.append("Der Titel fehlt.")
    if not hook:
        fehler.append("Der Hook fehlt.")
    stufen_roh = roh.get("stufen")
    if not isinstance(stufen_roh, list) or not stufen_roh:
        return None, fehler + ["Die Gliederung braucht mindestens eine Stufe."]
    stufen, anzahl = [], 0
    for i, st in enumerate(stufen_roh, 1):
        name = _text(st.get("name")) if isinstance(st, dict) else ""
        szenen_roh = st.get("szenen") if isinstance(st, dict) else None
        if not name or not isinstance(szenen_roh, list):
            fehler.append(f"Stufe {i}: Name oder Szenen fehlen.")
            continue
        szenen = []
        for s in szenen_roh:
            anzahl += 1
            if not isinstance(s, dict) or not _text(s.get("kurz")):
                fehler.append(f"Szene {anzahl}: Jede Szene ist ein Objekt mit einem nicht leeren `kurz`.")
                continue
            if s.get("bildart") not in BILDARTEN:
                fehler.append(f"Szene {anzahl}: Unbekannte Bildart {s.get('bildart')!r} "
                              f"(erlaubt: {', '.join(BILDARTEN)}).")
            quellen = s.get("quellen") if isinstance(s.get("quellen"), list) else []
            for q in quellen:
                if q != AUFTRAG and q not in geholt:
                    fehler.append(f"Szene {anzahl}: Die Quelle \"{q}\" wurde nicht geholt. Erlaubt sind "
                                  f"\"{AUFTRAG}\" und die gelieferten Dokumentnamen.")
            szenen.append({"kurz": _text(s["kurz"]), "bildart": s.get("bildart"),
                           "quellen": [q for q in quellen if isinstance(q, str)]})
        stufen.append({"name": name, "szenen": szenen})
    if not SZENEN_MIN <= anzahl <= SZENEN_MAX:
        fehler.append(f"Die Gliederung hat {anzahl} Szenen, erlaubt sind {SZENEN_MIN} bis {SZENEN_MAX}.")
    fehlt = [_text(f) for f in roh.get("fehlt", []) if _text(f)] if isinstance(roh.get("fehlt"), list) else []
    if fehler:
        return None, fehler
    return {"titel": titel, "hook": hook, "stufen": stufen, "fehlt": fehlt}, []


def szenen_liste(gliederung: dict) -> list[dict]:
    """Die Szenen der Gliederung flach, mit Nummer (ab 1) und Stufe: [{nr, stufe, kurz, bildart, quellen}]."""
    flach: list[dict] = []
    for st in gliederung.get("stufen", []):
        for s in st.get("szenen", []):
            flach.append({"nr": len(flach) + 1, "stufe": st.get("name", ""), **s})
    return flach


def _bildregeln() -> str:
    z, p = video_slides.ZEILE_MAX, video_slides.PUNKT_MAX
    return (
        f"Das Bild ist `{{\"schritte\": [...]}}` mit {video_slides.SCHRITTE_MIN} bis {video_slides.SCHRITTE_MAX} "
        "Schritten, auch bei Avatar-Szenen. Jeder Schritt: `vorlage`, `ab_satz` und die Felder der Vorlage flach "
        "daneben. `ab_satz` zählt die Sätze des Sprechtexts ab 0: Schritt 1 beginnt bei 0, jeder weitere liegt "
        "höher als der vorige und vor dem letzten Satz. Der Schritt steht, bis der nächste beginnt. Vorlagen:\n"
        f"- titel: zeile (höchstens {z} Zeichen), unterzeile (höchstens {z})\n"
        f"- stichpunkte: ueberschrift (höchstens {z}), punkte (2 bis 5 Texte, je höchstens {p})\n"
        f"- zahl: zahl (höchstens 12 Zeichen, z. B. \"40\"), einheit (höchstens {z}, z. B. \"% weniger Aufwand\"), "
        f"erklaerung (höchstens {p})\n"
        f"- zitat: zitat (höchstens {p}), von (höchstens {z})\n"
        f"- vergleich: links und rechts, je {{\"titel\": höchstens {z} Zeichen, \"punkte\": 1 bis 4 Texte, je höchstens {p}}}\n"
        f"- agenda: punkte (genau 3 Texte, je höchstens {p}), aktiv (1, 2 oder 3: welcher Punkt gerade dran ist)\n"
        "Alle Felder sind Pflicht und nicht leer. Beispiel: "
        "{\"vorlage\": \"zahl\", \"ab_satz\": 2, \"zahl\": \"40\", \"einheit\": \"%\", \"erklaerung\": \"…\"}")


SZENEN_FORM = """{"szenen": [{"nr": 1, "sprechtext": "2 bis 8 Sätze, 35 bis 110 Wörter",
 "bildart": "slide|avatar_gross|avatar_slide|clip", "bild": {"schritte": [{"vorlage": "titel", "ab_satz": 0, "zeile": "…", "unterzeile": "…"}]},
 "uebergang": "schnitt|blende", "belege": [{"aussage": "Satz aus der Quelle, mit ihrer Zahl", "quelle": "Dokumentname oder auftrag"}],
 "ohne_sachaussage": false, "aendert_text": true}]}"""


def szenen_prompt(eingabe: dict, gliederung: dict, szenen: list[dict], kontext: Kontext,
                  brief: str) -> tuple[str, str]:
    """(system, nutzer) fuer bis zu BLOCK Szenen. Zurueckgegebene Szenen tragen `wunsch`, `rueckgabe_von` und
    den alten Text (`sprechtext`, `bild`)."""
    block = szenen[:BLOCK]
    plan = "\n".join(f"{s['nr']}. [{s.get('stufe', '')}] {s.get('kurz', '')} ({s.get('bildart', '')})"
                     for s in szenen_liste(gliederung))
    aufgaben = []
    for s in block:
        zeilen = [f"Szene {s.get('nr')}: {s.get('kurz', '')}", f"  Bildart: {s.get('bildart', '')}",
                  f"  Geplante Quellen: {', '.join(s.get('quellen') or []) or 'keine'}"]
        if s.get("wunsch"):
            zeilen += [f"  ZURÜCKGEGEBEN von {s.get('rueckgabe_von') or 'dem Auftraggeber'}. Wunsch: {s['wunsch']}",
                       f"  Bisheriger Sprechtext: {s.get('sprechtext', '')}",
                       "  Bisheriges Bild: " + json.dumps(s.get("bild"), ensure_ascii=False)]
        aufgaben.append("\n".join(zeilen))
    nutzer = "\n\n".join([
        "Schreibe die folgenden Szenen eines Erklärvideos.",
        "# Auftrag\n" + _auftrag_text(eingabe),
        f"# Gliederung (Titel: {gliederung.get('titel', '')}; Hook: {gliederung.get('hook', '')})\n" + plan,
        "# Material (Quellen mit ihren Namen)\n" + _dokumente_text(kontext),
        "# Zu schreibende Szenen\n" + "\n\n".join(aufgaben),
        "# Regeln für jede Szene\n"
        f"- `sprechtext`: {SAETZE_MIN} bis {SAETZE_MAX} Sätze, {WOERTER_MIN} bis {WOERTER_MAX} Wörter, gesprochene "
        "Sprache.\n"
        "- Jede Zahl im Sprechtext (Preise, Prozent, Mengen, Jahre) steht als Ziffer da und hat einen Beleg, dessen "
        "`aussage` dieselbe Zahl enthält und dessen Quelle sie wörtlich enthält. Zahlen ohne Quelle schreibst du gar "
        "nicht; Kleinzahlen ohne Sachaussage (\"drei Punkte\") schreibst du als Wort.\n"
        "- `belege`: je Sachaussage eine Zeile `{aussage, quelle}`. `quelle` ist exakt \"auftrag\" oder ein "
        "Dokumentname aus dem Material. Hat die Szene keine Sachaussage (Gruß, Überleitung, Frage), steht "
        "`belege` leer und `ohne_sachaussage` ist true; sonst ist `ohne_sachaussage` false.\n"
        "- Zahlen in einer Folie der Vorlage `zahl` kommen aus den Belegen derselben Szene.\n"
        "- `aendert_text` ist true, wenn der Sprechtext neu oder geändert ist; bei einer zurückgegebenen Szene, "
        "deren Wunsch nur das Bild betrifft, bleibt der Sprechtext Wort für Wort gleich und `aendert_text` ist false.\n"
        f"- `uebergang` ist {' oder '.join(UEBERGAENGE)}.\n- " + _bildregeln(),
        "# Antwortform (nur dieses JSON, genau eine Zeile je angefragter Szene, `nr` wie oben)\n" + SZENEN_FORM,
    ])
    return _system(brief), nutzer


def szenen_lesen(text: str) -> list[dict] | None:
    """Szenen aus der Modellantwort; None, wenn sie nicht lesbar ist. Je Szene
    {nr, sprechtext, bildart, bild, uebergang, belege, ohne_sachaussage, aendert_text}."""
    roh = _json(text)
    liste = roh.get("szenen") if isinstance(roh, dict) else roh
    if not isinstance(liste, list) or not liste:
        return None
    ergebnis = []
    for s in liste:
        if not isinstance(s, dict):
            return None
        nr = s.get("nr")
        if isinstance(nr, bool) or not isinstance(nr, int) or not _text(s.get("sprechtext")):
            return None
        belege = [{"aussage": _text(b.get("aussage")), "quelle": _text(b.get("quelle"))}
                  for b in (s.get("belege") if isinstance(s.get("belege"), list) else []) if isinstance(b, dict)]
        ergebnis.append({
            "nr": nr, "sprechtext": _text(s["sprechtext"]), "bildart": s.get("bildart"),
            "bild": s.get("bild") if isinstance(s.get("bild"), dict) else {},
            "uebergang": s.get("uebergang") if s.get("uebergang") in UEBERGAENGE else "schnitt",
            "belege": belege, "ohne_sachaussage": s.get("ohne_sachaussage") is True,
            "aendert_text": s.get("aendert_text") is not False})
    return ergebnis


# --- Pruefung ---------------------------------------------------------------------------------

def _bild_zahlen(bild: dict) -> list[str]:
    """Die Zahl-Felder (`zahl`) der Vorlage `zahl` im Bildplan."""
    schritte = bild.get("schritte") if isinstance(bild, dict) else None
    return [str(s.get("zahl", "")) for s in schritte or []
            if isinstance(s, dict) and s.get("vorlage") == "zahl" and not isinstance(s.get("zahl"), (dict, list))]


def szene_pruefen(szene: dict, auftragstext: str, kontext: Kontext) -> list[str]:
    """Mechanische Pruefung einer Szene; leer = darf ans Text-Tor."""
    fehler: list[str] = []
    quellen = {d.name: d.text for d in kontext.dokumente}
    quellen[AUFTRAG] = auftragstext or ""
    belege = [b for b in szene.get("belege") or [] if isinstance(b, dict)]
    sprech = szene.get("sprechtext") if isinstance(szene.get("sprechtext"), str) else ""

    # Regel 1: Quelle ist "auftrag" oder ein geholtes Dokument
    for b in belege:
        q = b.get("quelle")
        if q not in quellen:
            fehler.append(f"Die Quelle \"{q}\" wurde nicht geholt. Erlaubt sind \"{AUFTRAG}\" und die gelieferten "
                          "Dokumentnamen.")
    # Regel 2: ohne Belege nur bei ohne_sachaussage
    if not belege and szene.get("ohne_sachaussage") is not True:
        fehler.append("Die Szene hat keinen Beleg. Nenne für jede Sachaussage eine Quelle oder lass die Aussage "
                      "weg und setze ohne_sachaussage auf true.")
    # Regel 3: jede Zahl im Sprechtext steht in der Aussage eines Belegs (Einheit % / € muss mitpassen)
    belegt = [z for b in belege for z in _zahlen(_text(b.get("aussage")))]
    for zahl, einheit in dict.fromkeys(_zahlen(sprech)):
        if not any(z == zahl and (not einheit or e == einheit) for z, e in belegt):
            fehler.append(f"Die Zahl \"{_zahl_text((zahl, einheit))}\" im Sprechtext hat keinen Beleg. Belege sie "
                          "mit Quelle oder nimm sie heraus.")
    for z in _bild_zahlen(szene.get("bild") or {}):
        for zahl in _nur_zahlen(z):
            if zahl not in {n for n, _ in belegt}:
                fehler.append(f"Die Zahl \"{z}\" auf der Folie hat keinen Beleg.")
    # Regel 4: jede Zahl einer Aussage steht woertlich im Text ihrer Quelle
    for b in belege:
        q = b.get("quelle")
        if q not in quellen:
            continue
        vorhanden = _nur_zahlen(quellen[q])
        for zahl in dict.fromkeys(n for n, _ in _zahlen(_text(b.get("aussage")))):
            if zahl not in vorhanden:
                fehler.append(f"Die Zahl \"{zahl.replace('.', ',')}\" aus dem Beleg steht nicht in der Quelle "
                              f"\"{q}\". Nimm nur Zahlen, die dort wörtlich stehen.")
    # Regel 5: Wortzahl und Satzzahl
    woerter, anzahl = len(sprech.split()), len(saetze(sprech))
    if not WOERTER_MIN <= woerter <= WOERTER_MAX:
        fehler.append(f"Der Sprechtext hat {woerter} Wörter, erlaubt sind {WOERTER_MIN} bis {WOERTER_MAX}.")
    if not SAETZE_MIN <= anzahl <= SAETZE_MAX:
        fehler.append(f"Der Sprechtext hat {anzahl} Sätze, erlaubt sind {SAETZE_MIN} bis {SAETZE_MAX}.")
    # Regel 6: Bildart und Bildplan
    if szene.get("bildart") not in BILDARTEN:
        fehler.append(f"Unbekannte Bildart {szene.get('bildart')!r} (erlaubt: {', '.join(BILDARTEN)}).")
    if szene.get("uebergang") not in UEBERGAENGE:
        fehler.append(f"Unbekannter Übergang (erlaubt: {', '.join(UEBERGAENGE)}).")
    fehler += video_slides.bild_pruefen(szene.get("bild") or {}, anzahl)
    return fehler


# --- Kopf -------------------------------------------------------------------------------------

def _zeit(sekunden: float) -> str:
    s = int(sekunden + 0.5)
    return f"{s // 60}:{s % 60:02d}"


def kopf(gliederung: dict, szenen: list[dict]) -> dict:
    """{titel, beschreibung, zeitmarken: [{zeit: "m:ss", name}]}: eine Marke je Stufe, Zeit = Summe der
    Szenendauern (`dauer_s`, Reihenfolge wie in der Gliederung) vor der ersten Szene der Stufe. Ohne Modell."""
    t, marken, rest = 0.0, [], list(szenen)
    for st in gliederung.get("stufen", []):
        marken.append({"zeit": _zeit(t), "name": st.get("name", "")})
        for _ in st.get("szenen", []):
            if rest:
                dauer = rest.pop(0).get("dauer_s")
                t += float(dauer) if isinstance(dauer, (int, float)) and not isinstance(dauer, bool) else 0.0
    zeilen = [_text(gliederung.get("hook")), "", *[f"{m['zeit']} {m['name']}" for m in marken], "", KI_HINWEIS]
    return {"titel": gliederung.get("titel", ""), "beschreibung": "\n".join(zeilen).strip(), "zeitmarken": marken}
