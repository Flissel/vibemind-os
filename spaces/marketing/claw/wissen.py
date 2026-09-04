"""Auswahl aus der Wissensbasis — rein, ohne Netz und ohne Modell.

WARUM DIESE STUFE UEBERHAUPT EXISTIERT. Rowboat hat keine Suche: sein MCP
kann Quellen auflisten und Dokumente lesen, mehr nicht (6 Werkzeuge,
gemessen 04.09.2026). Die Chat-Route waere der bequeme Weg gewesen, sie
landet aber beim Agenten `memory_responder`, der die Wissensquellen gar
nicht liest — auf eine Produktfrage antwortete er, er kenne den Begriff
nicht. Also waehlt der Sidecar selbst aus, bevor er das Modell fragt.

WARUM BEGRIFFSUEBERLAPPUNG UND KEINE EINBETTUNGEN. Die ganze Wissensbasis
sind 199.038 Zeichen in 134 Dokumenten (gemessen). Bei der Groesse ist ein
Vektorspeicher Aufwand ohne Ertrag: die Auswahl muss nur das Offensichtliche
finden, das Modell liest danach ganze Dokumente statt Schnipsel. Der Preis
ist ehrlich benannt — Synonyme findet diese Stufe nicht. Wenn das eines
Tages weh tut, ist der Ersatz genau eine Funktion: `auswaehlen`.

Diese Datei macht KEIN I/O. Das Sammeln steht in `werkzeuge._wissen_sammeln`,
damit es denselben `_rowboat`-Weg und dieselbe Fehlerbehandlung nutzt.
"""
import re

# Woerter unter dieser Laenge tragen nichts zur Auswahl bei ("ist", "der",
# "was") und wuerden jedes Dokument zum Treffer machen.
MINDESTLAENGE = 4

# Ein Treffer im Dokument- oder Quellennamen wiegt schwerer als einer im
# Text: wer eine Datei "Preise.md" nennt, sagt damit, worum es geht.
GEWICHT_NAME = 3

# Ein Begriff, der in mehr als diesem Anteil der Dokumente vorkommt,
# unterscheidet nichts mehr. Gemessen 04.09.2026: alle 14 Quellen heissen
# "VibeMind ...", der Begriff traf also jedes Dokument und zog acht Seiten
# aus einem fremden Kundenprojekt in die Antwort.
ANTEIL_ALLERWELT = 0.5

# Ein Frage-Begriff zaehlt auch, wenn er ein Wort im Dokument beginnt oder von
# ihm begonnen wird — ab dieser Laenge. Ohne das findet "Ideaspace" die Stelle
# nicht, an der "ideas.space" steht (gemessen 04.09.2026). Unter fuenf Zeichen
# ist ein Wortanfang zu wenig Absicht: "Konto" traefe sonst "Kontinent".
PRAEFIX_MINDESTLAENGE = 5

BUDGET_ZEICHEN = 60_000

_WORT = re.compile(r"[^\wäöüßÄÖÜ]+", re.UNICODE)


def begriffe(text: str) -> set:
    """Die tragenden Woerter eines Textes, kleingeschrieben."""
    return {w for w in _WORT.split((text or "").lower()) if len(w) >= MINDESTLAENGE}


def treffer(gesucht: set, vorhanden: set) -> int:
    """Wie viele der gesuchten Begriffe in der Menge vorkommen.

    Gleichheit zaehlt immer; darueber hinaus zaehlt ein gemeinsamer Wortanfang
    ab PRAEFIX_MINDESTLAENGE. Das faengt Zusammensetzungen und Beugungen, die
    im Deutschen den Normalfall bilden, ohne einen Stemmer mitzuschleppen.
    """
    anzahl = 0
    for wort in gesucht:
        if wort in vorhanden:
            anzahl += 1
            continue
        if len(wort) >= PRAEFIX_MINDESTLAENGE and any(
                len(anderes) >= PRAEFIX_MINDESTLAENGE
                and (anderes.startswith(wort) or wort.startswith(anderes))
                for anderes in vorhanden):
            anzahl += 1
    return anzahl


def bewerten(frage_begriffe: set, stueck: dict) -> int:
    """Wie viele Frage-Begriffe kommen vor — im Namen gewichtet, im Text einfach."""
    name = begriffe(f"{stueck.get('quelle', '')} {stueck.get('dokument', '')}")
    text = begriffe(stueck.get("text", ""))
    return GEWICHT_NAME * treffer(frage_begriffe, name) + treffer(frage_begriffe, text)


def allerweltsbegriffe(wortmengen: list) -> set:
    """Begriffe, die in mehr als ANTEIL_ALLERWELT der Dokumente stehen.

    Das ist der billigste Teil dessen, was ein Suchindex mit IDF taete: ein
    Wort, das ueberall steht, sagt nichts darueber, welches Dokument gemeint
    ist. Ohne diesen Schritt gewinnt der Firmenname jede Frage.
    """
    if not wortmengen:
        return set()
    zaehler: dict = {}
    for menge in wortmengen:
        for wort in menge:
            zaehler[wort] = zaehler.get(wort, 0) + 1
    grenze = len(wortmengen) * ANTEIL_ALLERWELT
    return {wort for wort, anzahl in zaehler.items() if anzahl > grenze}


def _punkten(gesucht: set, stuecke: list, zerlegt: list) -> list:
    bewertet = []
    for i, (stueck, (name, text)) in enumerate(zip(stuecke, zerlegt)):
        punkte = GEWICHT_NAME * treffer(gesucht, name) + treffer(gesucht, text)
        if punkte > 0:
            bewertet.append((punkte, -len(stueck.get("text", "")), i, stueck))
    bewertet.sort(key=lambda b: (-b[0], b[1], b[2]))
    return bewertet


def auswaehlen(frage: str, stuecke: list, budget: int = BUDGET_ZEICHEN) -> list:
    """Die Dokumente mit Bezug zur Frage, beste zuerst, bis das Budget voll ist.

    ZWEI RUNDEN, und die Reihenfolge ist der ganze Punkt. Erst zaehlen nur die
    unterscheidenden Begriffe — das haelt ein fremdes Kundenprojekt draussen,
    dessen Quellenname zufaellig den Firmennamen traegt. Findet diese Runde
    NICHTS, zaehlen alle Begriffe der Frage. Gar nichts zu finden ist
    schlechter als zu viel zu finden: das eine ist eine unbrauchbare Antwort,
    das andere nur eine unscharfe.

    Dokumente ohne einen einzigen Treffer kommen nie mit — lieber eine
    ehrliche Fehlanzeige als eine Antwort aus Fuellmaterial. Das beste Stueck
    kommt auch dann durch, wenn es allein das Budget sprengt.
    """
    gesucht = begriffe(frage)
    if not gesucht:
        return []

    # Einmal zerlegen, mehrfach gebraucht: Haeufigkeit und beide Runden.
    zerlegt = [(begriffe(f"{s.get('quelle', '')} {s.get('dokument', '')}"),
                begriffe(s.get("text", ""))) for s in stuecke]
    haeufig = allerweltsbegriffe([name | text for name, text in zerlegt])

    bewertet = []
    unterscheidend = gesucht - haeufig
    if unterscheidend:
        bewertet = _punkten(unterscheidend, stuecke, zerlegt)
    if not bewertet:
        bewertet = _punkten(gesucht, stuecke, zerlegt)

    gewaehlt, verbraucht = [], 0
    for _, _, _, stueck in bewertet:
        laenge = len(stueck.get("text", ""))
        if gewaehlt and verbraucht + laenge > budget:
            continue
        gewaehlt.append(stueck)
        verbraucht += laenge
        if verbraucht >= budget:
            break
    return gewaehlt


def bezeichnen(stueck: dict) -> str:
    """Wie ein Dokument im Beleg heisst: 'Quelle — Dokument'."""
    return f"{stueck.get('quelle', '?')} — {stueck.get('dokument', '?')}"


def auftrag(frage: str, gewaehlt: list) -> tuple:
    """(system, nutzer) fuer das Modell. Die Belegpflicht steht im System-Teil."""
    system = (
        "Du beantwortest Fragen ausschliesslich aus den mitgelieferten Dokumenten. "
        "Regeln, ohne Ausnahme:\n"
        "1. Jede Aussage nennt in Klammern das Dokument, aus dem sie stammt.\n"
        "2. Was in keinem Dokument steht, erfindest du nicht. Es kommt unter die "
        "Ueberschrift 'Zu klaeren' — als Frage, nicht als Behauptung.\n"
        "3. Keine Werbesprache. Sag, was da steht.\n"
        "4. Antworte auf Deutsch."
    )
    teile = [f"### {bezeichnen(s)}\n{s.get('text', '')}" for s in gewaehlt]
    nutzer = "Frage: " + frage + "\n\nDokumente:\n\n" + "\n\n".join(teile)
    return system, nutzer
