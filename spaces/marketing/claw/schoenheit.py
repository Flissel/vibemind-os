"""Schoenheitspruefung — das Handwerk als Schritt, nicht als Ratschlag.

Betreiber-Auftrag (12.09.2026): „skills sollen fuer die schoenheit und
annehmbarkeit am kunden sorgen."

WARUM DIESES MODUL UND NICHT NUR DIE FERTIGKEIT: `unterlage-gestalten`
beschreibt das Handwerk in Prosa, und ein Agent kann Prosa ueberlesen. Was
hier steht, LAEUFT — bei jeder Unterlage und bei jedem Vorlagen-Vorschlag,
ohne dass jemand daran denken muss. Die Fertigkeit sagt WARUM, dieses Modul
setzt DASS durch.

ZWEI SCHWEREGRADE, und der Unterschied ist die ganze Zurueckhaltung:

  HART   Etwas ist nachweislich unbrauchbar: Text, den man nicht lesen kann;
         ein Platzhalter, der beim Kunden landet. Das wird abgewiesen.
  WEICH  Etwas ist wahrscheinlich schlechter, aber ein Mensch kann gute
         Gruende haben. Das wird GENANNT, nicht verhindert.

Eine Pruefung, die bei Geschmacksfragen blockiert, wird umgangen; eine, die
bei Unlesbarkeit durchwinkt, ist wertlos. Darum die Trennung.

REINE RECHNUNG, KEIN ZUGRIFF: dieses Modul liest keine Datei, ruft keinen
Dienst und kennt keine Datenbank. Es bekommt Farben und Text und gibt
Befunde zurueck. Deshalb ist es ohne alles testbar — und deshalb kann es in
der Vorlagenpruefung UND im Setzer stehen, ohne dass eins vom anderen
abhaengt.
"""
import re

# --- Kontrast nach WCAG 2.1 ------------------------------------------------
# Warum WCAG und nicht „sieht gut aus": es ist die einzige Zahl, die man
# nachrechnen kann. 4.5:1 fuer gewoehnlichen Text, 3:1 fuer grossen (ab 18pt
# oder 14pt fett) — das sind die Schwellen, ab denen eine Seite fuer Menschen
# mit durchschnittlichem Sehvermoegen bei durchschnittlichem Licht lesbar
# ist. Der Betreiber hat am 11.09.2026 genau in diese Richtung gezeigt:
# „leicht graeulich ist laut Studien besser fuers Auge zu lesen".
KONTRAST_TEXT = 4.5
KONTRAST_GROSS = 3.0


def _kanal(wert: float) -> float:
    return wert / 12.92 if wert <= 0.03928 else ((wert + 0.055) / 1.055) ** 2.4


def leuchtdichte(farbe: str) -> float:
    """Relative Leuchtdichte einer #rrggbb-Farbe (WCAG 2.1)."""
    h = (farbe or "").strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(f"'{farbe}' ist keine Farbe der Form #rrggbb")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * _kanal(r) + 0.7152 * _kanal(g) + 0.0722 * _kanal(b)


def kontrast(vorne: str, hinten: str) -> float:
    """Kontrastverhaeltnis zweier Farben, 1.0 bis 21.0."""
    a, b = leuchtdichte(vorne), leuchtdichte(hinten)
    hell, dunkel = (a, b) if a > b else (b, a)
    return (hell + 0.05) / (dunkel + 0.05)


# Was auf was liegt, und welche Schwelle dafuer gilt. Die Paare stammen aus
# pdf._stile — wer dort eine Farbe umhaengt, muss hier nachziehen, und dieser
# Kommentar ist der Grund, warum das auffaellt.
_PAARE = (
    ("text", "grund", KONTRAST_TEXT, "der Fliesstext"),
    ("text_hell", "grund", KONTRAST_GROSS, "der Titel (24pt fett, grosse Schrift)"),
    ("akzent", "grund", KONTRAST_TEXT, "der Untertitel"),
    ("gold", "grund", KONTRAST_TEXT, "die Rubriken BELEGE und ZU KLAEREN"),
    ("text_leise", "flaeche", KONTRAST_TEXT, "die Fusszeile"),
    ("handlung_text", "akzent", KONTRAST_TEXT, "die Schrift im Handlungskasten"),
)


def befund(schwere: str, punkt: str, satz: str) -> dict:
    return {"schwere": schwere, "punkt": punkt, "satz": satz}


def gestalt_pruefen(gestalt: dict) -> list:
    """Ist diese Farbtafel lesbar? Gibt Befunde zurueck, wirft nie.

    HART, wenn ein Kontrast unter der Schwelle liegt: eine Unterlage, die
    man nicht lesen kann, ist keine Geschmacksfrage. Der Satz nennt die
    gemessene Zahl und die Schwelle, damit der Agent weiss, wie weit er
    daneben liegt — „zu wenig Kontrast" allein hilft niemandem.
    """
    befunde = []
    for vorne, hinten, schwelle, wofuer in _PAARE:
        try:
            wert = kontrast(str(gestalt.get(vorne, "")), str(gestalt.get(hinten, "")))
        except ValueError as e:
            befunde.append(befund("hart", f"{vorne}/{hinten}", str(e)))
            continue
        if wert < schwelle:
            befunde.append(befund("hart", f"{vorne}/{hinten}", (
                f"{wofuer} hat nur {wert:.1f}:1 Kontrast gegen den Hintergrund; "
                f"noetig sind {schwelle}:1 (WCAG 2.1). So liest das niemand "
                f"laenger als zwei Zeilen.")))
        elif wert > 19.0 and schwelle == KONTRAST_TEXT:
            # Reines Schwarz auf reinem Weiss ist 21:1 — und genau das hat der
            # Betreiber am 11.09.2026 als unangenehm bezeichnet. Ein WEICHER
            # Befund: manchmal ist Haerte gewollt (Druck, Barrierefreiheit).
            befunde.append(befund("weich", f"{vorne}/{hinten}", (
                f"{wofuer} hat {wert:.1f}:1 — das ist fast der Hoechstwert und "
                f"flimmert beim Lesen. Ein leicht gebrochener Ton liest sich "
                f"ruhiger (Rueckmeldung eines Lesers, 11.09.2026).")))
    return befunde


# --- Platzhalter ------------------------------------------------------------
# Der teuerste Fehler dieser Kette: ein Platzhalter, der beim Kunden landet.
# Die Muster sind absichtlich ENG — ein falscher Treffer blockiert einen
# gueltigen Text, und das kostet mehr Vertrauen als er einbringt.
_PLATZHALTER = (
    (re.compile(r"\[[^\]]{2,40}\]"), "eckige Klammern"),
    (re.compile(r"\{\{[^}]{1,40}\}\}"), "doppelte geschweifte Klammern"),
    (re.compile(r"<[a-zA-ZäöüÄÖÜ][^<>]{2,40}>"), "spitze Klammern"),
    (re.compile(r"\bTBD\b|\bTODO\b|\bXXX+\b", re.I), "TBD/TODO/XXX"),
    (re.compile(r"\bPlatzhalter\b|\bhier\s+einf(ü|ue)gen\b", re.I), "das Wort Platzhalter"),
    (re.compile(r"\b(example|beispiel)\.(com|de|org)\b", re.I), "eine Beispiel-Domain"),
    (re.compile(r"\bLorem ipsum\b", re.I), "Blindtext"),
)


def platzhalter(text: str) -> list:
    """Welche Platzhalter-Muster in diesem Text stehen. Namen, nicht Stellen."""
    gefunden = []
    for muster, name in _PLATZHALTER:
        treffer = muster.search(text or "")
        if treffer:
            gefunden.append((name, treffer.group(0)[:40]))
    return gefunden


# --- Die Unterlage als Ganzes ----------------------------------------------

BETONUNG = re.compile(r"\*\*(.+?)\*\*", re.S)
STICHPUNKT = re.compile(r"^[-–—•*]\s+", re.M)


def unterlage_pruefen(titel: str = "", untertitel: str = "", text: str = "",
                      handlung: str = "", belege=None, zu_klaeren=None,
                      seiten: int = 1) -> list:
    """Die fuenf Fragen aus der Fertigkeit, soweit eine Maschine sie stellen kann.

    Was sie NICHT kann, steht weiter nur in der Fertigkeit: ob der Text gut
    ist, ob der eine Gedanke traegt, ob man das einem Menschen geben wuerde.
    Diese Pruefung ersetzt das Lesen nicht — sie faengt das ab, was man beim
    Lesen uebersieht, weil man den eigenen Text schon dreimal gesehen hat.
    """
    befunde = []
    belege = list(belege or [])
    zu_klaeren = list(zu_klaeren or [])

    # (1) HART: ein Platzhalter im HANDLUNGSKASTEN. Er ist die einzige
    # gefuellte Flaeche der Seite — dort faellt er am meisten auf, und dort
    # steht das, was der Kunde tun soll.
    for name, roh in platzhalter(handlung):
        befunde.append(befund("hart", "handlung", (
            f"Im Handlungskasten steht ein Platzhalter ({name}: {roh!r}). Das "
            f"ist der sichtbarste Platz der Seite. Entweder die echte Adresse "
            f"einsetzen oder den Kasten LEER lassen und die fehlende Adresse "
            f"unter zu_klaeren nennen.")))

    # (2) HART: Platzhalter im Text oder in der Ueberschrift.
    for feld, inhalt in (("titel", titel), ("untertitel", untertitel), ("text", text)):
        for name, roh in platzhalter(inhalt):
            befunde.append(befund("hart", feld, (
                f"In {feld} steht ein Platzhalter ({name}: {roh!r}). Eine "
                f"Unterlage mit Platzhalter darf einen Kunden nicht erreichen.")))

    # (3) WEICH: keine Betonung in den Stichpunkten.
    punkte = len(STICHPUNKT.findall(text or ""))
    if punkte >= 2 and not BETONUNG.search(text or ""):
        befunde.append(befund("weich", "betonung", (
            f"{punkte} Stichpunkte, aber kein einziges betontes Wort. Wer eine "
            f"Unterlage ueberfliegt, liest die fetten Woerter zuerst — schreib "
            f"das Substantiv, um das es geht, als **wort** (Rueckmeldung eines "
            f"Lesers, 11.09.2026).")))

    # (4) WEICH: zu viel Betonung. Wer alles betont, betont nichts.
    betont = len(BETONUNG.findall(text or ""))
    if betont and punkte and betont > punkte * 2:
        befunde.append(befund("weich", "betonung", (
            f"{betont} betonte Stellen auf {punkte} Stichpunkte. Wer alles "
            f"betont, betont nichts — ein bis zwei Woerter je Punkt reichen.")))

    # (5) WEICH: mehr als eine Seite.
    if seiten > 1:
        befunde.append(befund("weich", "umfang", (
            f"Die Unterlage hat {seiten} Seiten. Fuer eine Einladung oder einen "
            f"Einseiter ist das eine zu viel — kuerz den Text, nicht die "
            f"Schrift.")))

    # (6) WEICH: kein Beleg.
    if not belege:
        befunde.append(befund("weich", "belege", (
            "Keine Belege. Das ist ehrlich, sagt dem Betreiber aber: "
            "ungeprueft. Wenn die Aussagen aus der Wissensbasis stammen, "
            "nenn die Quellen.")))

    # (7) WEICH: kein Handlungsaufruf.
    if not (handlung or "").strip():
        befunde.append(befund("weich", "handlung", (
            "Kein Handlungskasten. Wenn der Leser etwas tun soll, sag ihm wo — "
            "fehlt nur die Adresse, gehoert sie unter zu_klaeren.")))

    return befunde


def urteil(befunde: list) -> dict:
    """Die Befunde zu einer Antwort buendeln, die ein Agent lesen kann."""
    hart = [b for b in befunde if b["schwere"] == "hart"]
    weich = [b for b in befunde if b["schwere"] == "weich"]
    return {
        "bestanden": not hart,
        "hart": [b["satz"] for b in hart],
        "weich": [b["satz"] for b in weich],
    }
