"""Markenwissen je Firma aus der Rowboat-Ablage lesen und Agent-Notizen dort ablegen.

Die Ablage liegt unter ~/.rowboat/knowledge/companys/<Firma>/ (oder unter
ROWBOAT_WISSEN_ORDNER). Der Chat-Arbeiter am PC liest daraus den Abschnitt
fuer den Gestaltungs-Agenten und schreibt dessen Notizen nach
<Firma>/Agent-Notizen/.

WARUM DIE LINK-SPERRE. Die Trennung der Firmen ist der ganze Zweck: ein
VibeMind-Lauf darf nie fin2gether-Dateien lesen. Ein Symlink oder eine
Windows-Junction im Firmenordner wuerde genau das unbemerkt tun. Python 3.11
meldet Junctions als gewoehnliche Ordner (`is_symlink()` ist False), und
`os.walk(followlinks=False)` steigt trotzdem hinein — gemessen 06.10.2026.
Deshalb zaehlt allein der Vergleich ueber `os.path.realpath`: ein Eintrag
gilt nur, wenn sein realer Pfad genau dort liegt, wo sein Name ihn erwartet.

`laden` und `notizen_schreiben` werfen nie; was schiefgeht, wird Hinweis.
"""
import datetime
import os
import re
import sys
from dataclasses import dataclass

from spaces.marketing.claw import wissen

WURZEL_VORGABE = os.path.join(os.path.expanduser("~"), ".rowboat", "knowledge", "companys")
MAX_DATEIEN, MAX_DATEI_BYTES, MARKE_MAX, BUDGET, NOTIZEN_MAX, LEER_MIN = 200, 200_000, 8_000, 30_000, 10, 40
NOTIZ_ORDNER, NOTIZEN_JE_ANTWORT, TITEL_MAX, TEXT_MAX = "Agent-Notizen", 3, 80, 4_000

_ENDUNGEN = (".md", ".txt")
_MARKE = "marke.md"
_VERMERK = " … (gekürzt)"
_TRENNER = "\n\n"
_BITTE_MAX = 300
_SLUG_MAX = 60
_SUFFIX_MAX = 99
# Unter dieser Restlaenge lohnt ein angeschnittenes Stueck nicht mehr.
_MIN_ANSCHNITT = 200
_KOMMENTAR = re.compile(r"<!--.*?-->", re.DOTALL)
_NICHT_SLUG = re.compile(r"[^a-z0-9äöüß]+")

_VORLAGE_ABSCHNITTE = (
    ("Wer wir sind", "Zwei, drei Sätze: Was macht die Firma?"),
    ("Zielgruppe", "Wen wollen wir erreichen? Branche, Größe, typische Fragen."),
    ("Ton", "Duzen oder Siezen? Locker oder sachlich? Ein Beispielsatz hilft."),
    ("Angebote", "Was verkaufen wir? Je Angebot eine Zeile mit Nutzen."),
    ("Do & Don'ts", "Was gehört in jeden Newsletter, was nie?"),
    ("Fakten und Zahlen", "Belegbare Zahlen, Auszeichnungen, Gründungsjahr. Nur Geprüftes."),
)


@dataclass
class Wissen:
    text: str            # fertiger Prompt-Abschnitt ohne Kopfzeile ("" = keins)
    hinweise: list[str]
    ordner: str | None   # gefundener Firmenordner (fuer Notizen)


def wurzel() -> str:
    """Ablage der Firmenordner: ROWBOAT_WISSEN_ORDNER oder die Rowboat-Vorgabe."""
    return os.environ.get("ROWBOAT_WISSEN_ORDNER") or WURZEL_VORGABE


# --- Link-Sperre ----------------------------------------------------------------

def _echt(pfad: str, eltern: str, basis_real: str) -> bool:
    """Liegt `pfad` real genau dort, wo sein Name ihn erwartet, und unter `basis_real`?

    Faengt Symlinks und Junctions auf Ordner wie Dateien: deren realer Pfad
    weicht vom erwarteten ab. Fuer noch nicht vorhandene Pfade gilt derselbe
    Vergleich (realpath laesst den letzten Teil dann stehen).
    """
    try:
        real = os.path.normcase(os.path.realpath(pfad))
        erwartet = os.path.normcase(os.path.join(os.path.realpath(eltern), os.path.basename(pfad)))
        if real != erwartet:
            return False
        basis = os.path.normcase(basis_real)
        return os.path.commonpath([real, basis]) == basis
    except (OSError, ValueError):
        return False


def _selbst_echt(ordner: str) -> bool:
    """Ist der Ordner selbst keine Verknuepfung (gemessen an seinem Elternordner)?"""
    ordner = os.path.normpath(ordner)
    eltern = os.path.dirname(ordner)
    return _echt(ordner, eltern, os.path.realpath(eltern))


# --- Firmenordner ---------------------------------------------------------------

def ordner_finden(wurzel: str, mandant: str, name: str) -> str | None:
    """Direkter Unterordner der Wurzel, dessen Name Mandanten-id oder -Name ist.

    Ohne Gross-/Kleinschreibung und ohne Leerraum am Rand; bei mehreren
    Treffern der alphabetisch erste. Verknuepfungen zaehlen nicht.
    """
    gesucht = {str(mandant or "").strip().casefold(), str(name or "").strip().casefold()} - {""}
    try:
        eintraege = sorted(os.scandir(wurzel), key=lambda e: e.name)
        wurzel_real = os.path.realpath(wurzel)
    except OSError:
        return None
    for eintrag in eintraege:
        if eintrag.name.strip().casefold() not in gesucht:
            continue
        pfad = os.path.join(wurzel, eintrag.name)
        if os.path.isdir(pfad) and _echt(pfad, wurzel, wurzel_real):
            return pfad
    return None


def ist_leer(text: str) -> bool:
    """Hat der Text weniger als LEER_MIN Zeichen Inhalt (ohne Ueberschriften, Kommentare, Leerraum)?"""
    ohne_kommentar = _KOMMENTAR.sub("", text or "")
    zeilen = [z for z in ohne_kommentar.splitlines() if not z.lstrip().startswith("#")]
    return len("".join("".join(zeilen).split())) < LEER_MIN


# --- Lesen ----------------------------------------------------------------------

def _kein_wissen(name: str) -> str:
    return f"Kein Markenwissen für {name} hinterlegt (companys/{name} fehlt/leer)"


def _gekuerzt(genommen: int, gesamt: int) -> str:
    return f"Markenwissen gekürzt ({genommen} von {gesamt} Dateien)"


def _ist_notiz(rel: str) -> bool:
    return rel.split("/", 1)[0].casefold() == NOTIZ_ORDNER.casefold() and "/" in rel


def _dateien(ordner: str, hinweise: list[str]) -> list[tuple[str, str]]:
    """(rel, pfad) aller .md/.txt im Firmenordner, nach rel sortiert, ohne Verknuepfungen."""
    firma_real = os.path.realpath(ordner)

    def rel(pfad: str) -> str:
        return os.path.relpath(pfad, ordner).replace(os.sep, "/")

    gefunden = []
    for ort, unterordner, namen in os.walk(ordner, followlinks=False):
        unterordner.sort()
        echte = []
        for u in unterordner:
            pfad = os.path.join(ort, u)
            if _echt(pfad, ort, firma_real):
                echte.append(u)
            else:
                hinweise.append(f"{rel(pfad)} übersprungen (Verknüpfung)")
        unterordner[:] = echte  # Junctions sonst durchlaufen
        for n in namen:
            if not n.lower().endswith(_ENDUNGEN):
                continue
            pfad = os.path.join(ort, n)
            if _echt(pfad, ort, firma_real):
                gefunden.append((rel(pfad), pfad))
            else:
                hinweise.append(f"{rel(pfad)} übersprungen (Verknüpfung)")
    return sorted(gefunden)


def _mtime(pfad: str) -> float:
    try:
        return os.stat(pfad).st_mtime
    except OSError:
        return 0.0


def _neueste_notizen(dateien: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Aus Agent-Notizen/ nur die NOTIZEN_MAX neuesten; Reihenfolge bleibt nach rel."""
    notizen = [d for d in dateien if _ist_notiz(d[0])]
    neueste = sorted(notizen, key=lambda d: (_mtime(d[1]), d[0]), reverse=True)[:NOTIZEN_MAX]
    weg = set(notizen) - set(neueste)
    return [d for d in dateien if d not in weg]


def _lesen(rel: str, pfad: str, hinweise: list[str]) -> str | None:
    """Dateitext (utf-8-sig, sonst latin-1) oder None mit Hinweis."""
    try:
        with open(pfad, "rb") as f:
            roh = f.read(MAX_DATEI_BYTES + 1)
    except OSError:
        hinweise.append(f"{rel} übersprungen (nicht lesbar)")
        return None
    if len(roh) > MAX_DATEI_BYTES:
        hinweise.append(f"{rel} übersprungen (größer als 200 KB)")
        return None
    try:
        text = roh.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = roh.decode("latin-1")
    return text.replace("\r\n", "\n")  # unter Windows gespeicherte Dateien


def _abschnitt(rel: str, text: str) -> str:
    return f"### {rel}\n{text}"


def _kappen(stuecke: list[dict], belegt: int) -> list[dict]:
    """Stuecke in gegebener Reihenfolge nehmen, solange der Abschnitt <= BUDGET bleibt.

    `belegt` ist die Laenge des schon vorhandenen Abschnitts (Marke.md). Ein
    erstes Stueck, das allein nicht passt, wird angeschnitten statt verworfen —
    wie in `wissen.auswaehlen` kommt das beste immer durch.
    """
    genommen = []
    for s in stuecke:
        trenner = len(_TRENNER) if belegt else 0
        laenge = trenner + len(_abschnitt(s["dokument"], s["text"]))
        if belegt + laenge <= BUDGET:
            genommen.append(s)
            belegt += laenge
            continue
        frei = BUDGET - belegt - trenner - len(_abschnitt(s["dokument"], "")) - len(_VERMERK)
        if not genommen and frei >= _MIN_ANSCHNITT:
            genommen.append(dict(s, text=s["text"][:frei] + _VERMERK))
            belegt = BUDGET
    return genommen


def _laden(ordner: str, name: str, frage: str) -> Wissen:
    hinweise: list[str] = []
    dateien = _neueste_notizen(_dateien(ordner, hinweise))
    # Marke.md vor dem Kappen herausnehmen: sonst verdraengen 200 Dateien, die
    # alphabetisch davor liegen (Archiv/…), genau die eine, die immer gilt.
    marke_datei = next((d for d in dateien if d[0].casefold() == _MARKE), None)
    uebrige = [d for d in dateien if d is not marke_datei]
    platz = MAX_DATEIEN - (marke_datei is not None)
    if len(uebrige) > platz:
        hinweise.append(_gekuerzt(MAX_DATEIEN, len(dateien)))
        uebrige = uebrige[:platz]

    marke: tuple[str, str] | None = None
    if marke_datei is not None:
        text = _lesen(*marke_datei, hinweise)
        if text is not None and not ist_leer(text):
            marke = (marke_datei[0], text if len(text) <= MARKE_MAX else text[:MARKE_MAX] + _VERMERK)
    stuecke = []
    for rel, pfad in uebrige:
        text = _lesen(rel, pfad, hinweise)
        if text is None or ist_leer(text):
            continue
        stuecke.append({"quelle": name, "dokument": rel, "text": text})

    vorhanden = len(stuecke) + (marke is not None)
    if not vorhanden:
        return Wissen(text="", hinweise=hinweise + [_kein_wissen(name)], ordner=ordner)

    kopf = [_abschnitt(*marke)] if marke else []
    belegt = len(kopf[0]) if kopf else 0
    alle = [_abschnitt(s["dokument"], s["text"]) for s in stuecke]
    if len(_TRENNER.join(kopf + alle)) <= BUDGET:
        gewaehlt = stuecke
    else:
        rest = BUDGET - belegt
        gewaehlt = _kappen(wissen.auswaehlen(frage, stuecke, budget=rest), belegt)
        gewaehlt.sort(key=lambda s: s["dokument"])
    genommen = len(gewaehlt) + (marke is not None)
    if genommen < vorhanden:
        hinweise.append(_gekuerzt(genommen, vorhanden))
    teile = kopf + [_abschnitt(s["dokument"], s["text"]) for s in gewaehlt]
    return Wissen(text=_TRENNER.join(teile), hinweise=hinweise, ordner=ordner)


def laden(wurzel: str, mandant: str, name: str, frage: str) -> Wissen:
    """Markenwissen der Firma als Prompt-Abschnitt. Wirft nie.

    `Marke.md` steht immer vorn (hoechstens MARKE_MAX Zeichen), der Rest
    passt ganz hinein oder wird nach Bezug zur Frage ausgewaehlt; der ganze
    Abschnitt bleibt <= BUDGET Zeichen.
    """
    ordner = None
    try:
        ordner = ordner_finden(wurzel, mandant, name)
        if ordner is None:
            return Wissen(text="", hinweise=[_kein_wissen(name)], ordner=None)
        return _laden(ordner, name, frage)
    except Exception:  # Fehler jeder Art = kein Markenwissen, der Lauf geht weiter
        return Wissen(text="", hinweise=[_kein_wissen(name)], ordner=ordner)


# --- Notizen --------------------------------------------------------------------

def slug(titel: str) -> str:
    """Dateinamen-Teil: klein, nur [a-z0-9äöüß-], hoechstens 60 Zeichen, leer => 'notiz'."""
    s = _NICHT_SLUG.sub("-", str(titel or "").lower()).strip("-")
    return s[:_SLUG_MAX].strip("-") or "notiz"


def _einzeilig(wert: object) -> str:
    return " ".join(str(wert or "").split())


def _gueltig(eintrag: object) -> tuple[str, str] | None:
    """(titel, text) einer gueltigen Notiz, sonst None."""
    if not isinstance(eintrag, dict):
        return None
    titel, text = eintrag.get("titel"), eintrag.get("text")
    if not isinstance(titel, str) or not isinstance(text, str):
        return None
    titel = _einzeilig(titel)
    if not 1 <= len(titel) <= TITEL_MAX or not text.strip() or len(text) > TEXT_MAX:
        return None
    return titel, text


def _inhalt(titel: str, text: str, kopf: dict, heute: datetime.date) -> str:
    return (f"# {titel}\n\n"
            f"- Datum: {heute:%Y-%m-%d}\n"
            f"- Newsletter: {_einzeilig(kopf.get('newsletter'))}\n"
            f"- Bitte des Betreibers: {_einzeilig(kopf.get('bitte'))[:_BITTE_MAX]}\n\n"
            f"{text}\n")


def _ablegen(ziel: str, firma_real: str, titel: str, inhalt: str, heute: datetime.date) -> bool:
    """Exklusiv neu anlegen, bei Kollision -2, -3 … bis -99. Nie ueberschreiben.

    Der Inhalt wird VOR dem Anlegen kodiert: ein einzelnes Surrogat (in JSON
    gueltig, in UTF-8 nicht) scheitert so, bevor eine leere Datei entstehen kann.
    """
    roh = inhalt.encode("utf-8")
    basis = f"{heute:%Y-%m-%d} {slug(titel)}"
    for nummer in range(1, _SUFFIX_MAX + 1):
        pfad = os.path.join(ziel, f"{basis}.md" if nummer == 1 else f"{basis}-{nummer}.md")
        if not _echt(pfad, ziel, firma_real):
            continue  # verwaister Link unter diesem Namen: nicht hindurchschreiben
        try:
            f = open(pfad, "xb")
        except FileExistsError:
            continue
        try:
            with f:
                f.write(roh)
        except OSError:
            _entfernen(pfad)  # keine halbe Notiz liegen lassen
            raise
        return True
    return False


def _entfernen(pfad: str) -> None:
    try:
        os.remove(pfad)
    except OSError:
        pass


def notizen_schreiben(ordner: str | None, name: str, notizen: list[dict], kopf: dict,
                      heute: datetime.date) -> tuple[list[str], list[str]]:
    """Notizen nach <Firma>/Agent-Notizen/ schreiben -> (geschriebene Titel, Hinweise). Wirft nie.

    Der Firmenordner wird nie angelegt; nur Agent-Notizen/ darin, und auch
    das nur, wenn es real im Firmenordner liegt.
    """
    fehlt = f"Notiz nicht abgelegt: companys/{name} fehlt"
    gescheitert = "Notiz konnte nicht abgelegt werden"
    if ordner is None:
        return [], [fehlt]
    try:
        if not os.path.isdir(ordner) or not _selbst_echt(ordner):
            return [], [fehlt]
        firma_real = os.path.realpath(ordner)
        ziel = os.path.join(ordner, NOTIZ_ORDNER)
        try:
            os.mkdir(ziel)
        except FileExistsError:
            pass
        if not os.path.isdir(ziel) or not _echt(ziel, ordner, firma_real):
            return [], [gescheitert]
    except (OSError, ValueError):
        return [], [gescheitert]

    kopf = kopf if isinstance(kopf, dict) else {}
    geschrieben: list[str] = []
    hinweise: list[str] = []
    for eintrag in (notizen if isinstance(notizen, list) else [])[:NOTIZEN_JE_ANTWORT]:
        notiz = _gueltig(eintrag)
        if notiz is None:
            continue
        titel, text = notiz
        try:
            ok = _ablegen(ziel, firma_real, titel, _inhalt(titel, text, kopf, heute), heute)
        except (OSError, ValueError):  # UnicodeEncodeError ist ein ValueError
            ok = False
        if ok:
            geschrieben.append(titel)
        else:
            hinweise.append(gescheitert)
    return geschrieben, hinweise


# --- Vorlagen -------------------------------------------------------------------

def _vorlage(name: str) -> str:
    teile = [f"# {name}\n<!-- Diese Datei liest der Newsletter-Agent bei jedem Lauf zuerst. "
             "Schreib unter jede Überschrift ein paar Sätze. -->"]
    for titel, hilfe in _VORLAGE_ABSCHNITTE:
        teile.append(f"## {titel}\n<!-- {hilfe} -->")
    return "\n\n".join(teile) + "\n"


def vorlagen_anlegen(wurzel: str, namen: list[str]) -> list[str]:
    """Fuer jede Firma ohne Ordner: <Name>/Marke.md als leere Vorlage. Bestehendes bleibt unberuehrt."""
    os.makedirs(wurzel, exist_ok=True)
    angelegt = []
    for name in namen:
        name = str(name or "").strip()
        if not name or name in (".", "..") or os.path.basename(name) != name or "/" in name:
            continue
        if ordner_finden(wurzel, name, name) is not None:
            continue
        pfad = os.path.join(wurzel, name)
        try:
            os.mkdir(pfad)
        except FileExistsError:
            continue  # z. B. eine Verknuepfung unter dem Namen: nicht anfassen
        with open(os.path.join(pfad, "Marke.md"), "x", encoding="utf-8") as f:
            f.write(_vorlage(name))
        angelegt.append(pfad)
    return angelegt


def main(argv: list[str]) -> int:
    """CLI: `python -m spaces.marketing.claw.markenwissen vorlagen VibeMind fin2gether`."""
    if len(argv) < 2 or argv[0] != "vorlagen":
        print("Aufruf: python -m spaces.marketing.claw.markenwissen vorlagen <Name> [<Name> …]",
              file=sys.stderr)
        return 2
    for pfad in vorlagen_anlegen(wurzel(), argv[1:]):
        print(f"Vorlage angelegt: {pfad}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
