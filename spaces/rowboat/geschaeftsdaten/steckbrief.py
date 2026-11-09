"""Steckbrief je Kontakt: Kategorie, Stand, People-Link und Markdown (rein, ohne DB).

Inhalt nach Plan Teil B ("Inhalt des Steckbriefs", Betreiber-Entscheid 2): Name, Art, Firma, Rolle,
Bereiche, Pipeline-Stufe, Herkunft, letzter Kontakt (nur Datum und Richtung), offene Termine (nur Datum
und Uhrzeit, hoechstens 5), Newsletter-Einwilligung (ja/nein), Empfohlen von, Archiv-Hinweis,
"Siehe auch" auf eine People-Notiz. NIE E-Mail, Telefon, Kontaktprofil, Vertraege/Bedarf,
Nachrichtentexte. Termin-Thema und -Ort sind seit 08.11.2026 erlaubt (Felix hebt Betreiber-Entscheid 2
dafuer auf), sonst gilt die Regel weiter. Die E-Mail dient nur dem People-Abgleich und wird aus jedem
geschriebenen Feld entfernt, falls sie dort vorkommt.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from namen import MAIL_MUSTER, dateistamm
from quelle import Ereignis, Kontakt

BERLIN = ZoneInfo("Europe/Berlin")
OHNE = "_ohne"
ARCHIV = "_archiv"
ERZEUGT_VON = "rowboat-geschaeftsdaten"

# Reihenfolge und Schluessel wie sales-claw server.BEREICHE; Ordnernamen ohne "/".
KATEGORIEN: dict[str, str] = {
    "bav": "bAV",
    "mitarbeiter": "Mitarbeiter",
    "vorsorge": "Private Vorsorge und Versicherung",
    "immobilien": "Immobilien",
    "it": "IT-Lösung",
    "homelab": "Homelab",
}
# Anzeige wie sales-claw server.BEREICHE.
BEREICHE: dict[str, str] = {
    "bav": "bAV",
    "mitarbeiter": "Mitarbeiter",
    "vorsorge": "Private Vorsorge/Versicherung",
    "immobilien": "Immobilien",
    "it": "IT-Lösung",
    "homelab": "Homelab",
}
# Ausgeschrieben fuer die Bedeutungssuche (Teil C): die Abkuerzung allein findet "Altersvorsorge" nicht.
THEMEN: dict[str, str] = {
    "bav": "betriebliche Altersvorsorge (bAV)",
    "mitarbeiter": "Mitarbeiter und Personal",
    "vorsorge": "private Altersvorsorge und Versicherungen",
    "immobilien": "Immobilien und Immobilienkauf",
    "it": "IT-Lösungen und IT-Dienstleistungen",
    "homelab": "Homelab und eigene Server",
}
STUFEN = {"new": "Neu", "researched": "Recherchiert", "qualified": "Qualifiziert", "contacted": "Kontaktiert",
          "replied": "Geantwortet", "meeting": "Termin", "won": "Gewonnen", "lost": "Verloren"}
ARTEN = {"person": "Person", "firma": "Firma"}
EINGEHEND = frozenset({"kundenantwort", "nachricht"})
AUSGEHEND = frozenset({"nachricht_ausgehend", "versand"})
MAX_TERMINE = 5
_LEERRAUM = re.compile(r"\s+")
_AUSLOESER = re.compile(r"@(rowboat)", re.IGNORECASE)
_KOPF_MAX = 4096


# Bereichs-Uebersichten unter `companys/<Firma>/Bereiche/`: jeder Steckbrief verweist per `parent:` auf
# die Uebersicht seines Ordners, damit er im Rowboat-Graph haengt (Rowboat zieht Kanten nur aus [[...]]).
UEBERSICHTEN = "Bereiche"
UEBERSICHT_GESAMT = "_overview"
_UEBERSICHT_TITEL = {OHNE: "Ohne Bereich", ARCHIV: "Archiv"}


def _bereiche(k: Kontakt) -> list[str]:
    return [b for b in KATEGORIEN if b in k.bereiche]


def uebersicht_stamm(ordner: str) -> str:
    """Dateistamm der Uebersicht zu einem Ordner unter `Kontakte/` (`_ohne`/`_archiv` lesbar benannt)."""
    return _UEBERSICHT_TITEL.get(ordner, ordner)


def weitere_kategorien(k: Kontakt) -> list[str]:
    """Ordnernamen der Bereiche nach dem Hauptbereich; archivierte Kontakte haben keine."""
    return [] if k.archiviert else [KATEGORIEN[b] for b in _bereiche(k)[1:]]


def _links(ziele: list[str]) -> str:
    return '"' + ", ".join(f"[[{z}]]" for z in ziele) + '"'


def kategorie(k: Kontakt) -> str:
    """Ordner unter `Kontakte/`: `_archiv` fuer archivierte Kontakte, sonst der erste bekannte Bereich
    in fester Reihenfolge, sonst `_ohne`."""
    if k.archiviert:
        return ARCHIV
    bereiche = _bereiche(k)
    return KATEGORIEN[bereiche[0]] if bereiche else OHNE


def _berlin(t: datetime) -> date:
    return t.astimezone(BERLIN).date()


def stand(k: Kontakt, ereignisse: list[Ereignis]) -> date:
    """Juengstes Datum aus `updated_at` und den Ereignissen des Kontakts (Berlin), sonst `created_at`.
    Bewusst aus den Daten, nicht aus der Uhr: sonst aenderte sich jede Datei jede Nacht."""
    zeiten = [e.zeit for e in ereignisse if e.lead_id == k.id]
    if k.updated_at is not None:
        zeiten.append(k.updated_at)
    return _berlin(max(zeiten) if zeiten else k.created_at)


# --- People-Index -----------------------------------------------------------------------------------

def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip().casefold()


def _frontmatter_zeilen(pfad: Path) -> list[str]:
    try:
        with pfad.open("r", encoding="utf-8", errors="replace") as f:
            kopf = f.read(_KOPF_MAX)
    except OSError:
        return []
    zeilen = kopf.splitlines()
    if not zeilen or zeilen[0].strip() != "---":
        return []
    for i, zeile in enumerate(zeilen[1:], start=1):
        if zeile.strip() == "---":
            return zeilen[1:i]
    return []


def _ohne_quotes(wert: str) -> str:
    return wert.strip().strip("\"'").strip()


def _mails(zeilen: list[str]) -> list[str]:
    """`email:` auf oberster Ebene: Einzelwert, `[a, b]` oder Blockliste `- a`."""
    mails: list[str] = []
    for i, zeile in enumerate(zeilen):
        if not zeile.startswith("email:"):
            continue
        wert = zeile[len("email:"):].strip()
        if wert.startswith("[") and wert.endswith("]"):
            mails += [_ohne_quotes(w) for w in wert[1:-1].split(",")]
        elif wert:
            mails.append(_ohne_quotes(wert))
        else:
            for folge in zeilen[i + 1:]:
                if not folge.startswith((" ", "\t")):
                    break
                eintrag = folge.strip()
                if eintrag.startswith("- "):
                    mails.append(_ohne_quotes(eintrag[2:]))
    return [_norm(m) for m in mails if "@" in m]


@dataclass
class PeopleIndex:
    nach_mail: dict[str, set[str]] = field(default_factory=dict)
    nach_name: dict[str, set[str]] = field(default_factory=dict)

    @staticmethod
    def _eindeutig(treffer: set[str] | None) -> str | None:
        return f"People/{next(iter(treffer))}" if treffer and len(treffer) == 1 else None

    def link(self, name: str | None, email: str | None) -> str | None:
        """`People/<stamm>`: erst gleiche E-Mail, dann gleicher Dateistamm. Mehrdeutig -> kein Link."""
        if email:
            treffer = self.nach_mail.get(_norm(email))
            if treffer:
                return self._eindeutig(treffer)
        if name and name.strip():
            kandidaten = {_norm(name), _norm(dateistamm(name, "", 200))} - {""}
            treffer = set().union(*(self.nach_name.get(c, set()) for c in kandidaten))
            return self._eindeutig(treffer)
        return None


def people_index(wissen: Path) -> PeopleIndex:
    """Index ueber `wissen/People/*.md` (nur oberste Ebene, also ohne `People/Veraltet/`)."""
    index = PeopleIndex()
    ordner = wissen / "People"
    if not ordner.is_dir() or ordner.is_symlink():
        return index
    for pfad in sorted(ordner.glob("*.md")):
        if pfad.is_symlink() or not pfad.is_file():
            continue
        stamm = unicodedata.normalize("NFC", pfad.stem)
        index.nach_name.setdefault(_norm(stamm), set()).add(stamm)
        for mail in set(_mails(_frontmatter_zeilen(pfad))):
            index.nach_mail.setdefault(mail, set()).add(stamm)
    return index


# --- Steckbrief -------------------------------------------------------------------------------------

def _einzeilig(wert: str | None, email: str | None) -> str | None:
    """Freitext einzeilig; die E-Mail-Adresse des Kontakts und jede andere Adresse werden entfernt."""
    if wert is None:
        return None
    text = _LEERRAUM.sub(" ", wert).strip()
    if email:
        text = re.sub(re.escape(email), "[E-Mail entfernt]", text, flags=re.IGNORECASE)
    text = MAIL_MUSTER.sub("[E-Mail entfernt]", text)
    text = _AUSLOESER.sub(r"@ \1", text)  # Rowboat-InlineTasks reagieren auf @rowboat
    return text or None


def _letzter_kontakt(k: Kontakt, ereignisse: list[Ereignis]) -> str:
    relevant = [e for e in ereignisse if e.lead_id == k.id and e.typ in EINGEHEND | AUSGEHEND]
    if not relevant:
        return "keiner"
    e = max(relevant, key=lambda e: e.zeit)
    richtung = "eingehend" if e.typ in EINGEHEND else "ausgehend"
    return f"{_berlin(e.zeit):%d.%m.%Y} ({richtung})"


def _offene_termine(k: Kontakt, ereignisse: list[Ereignis], heute: date) -> list[str]:
    from termine import termine_aus  # spaet: termine.py importiert seinerseits aus diesem Modul

    aktiv, _ = termine_aus([e for e in ereignisse if e.lead_id == k.id])
    kommend = [t for t in aktiv if t.datum >= heute]  # `termine_aus` sortiert nach Datum und Uhrzeit
    return [f"{t.datum:%d.%m.%Y}, {t.uhrzeit}" if t.uhrzeit else f"{t.datum:%d.%m.%Y}"
            for t in kommend[:MAX_TERMINE]]


def _empfohlen(wert: str | None, email: str | None) -> str | None:
    text = _einzeilig(wert, email)
    if text and text.startswith("[[") and text.endswith("]]"):
        return text
    return text.replace("[[", "[ [") if text else None


def steckbrief(k: Kontakt, ereignisse: list[Ereignis], *, laden: str, heute: date, people: str | None,
               empfohlen_von: str | None, uebersicht: str | None = None, weitere: list[str] | None = None) -> str:
    """Markdown des Steckbriefs. `people` ist ein Link-Ziel `People/<stamm>`; `empfohlen_von` ist ein
    fertiger Wiki-Link `[[...]]` oder ein Name; `uebersicht`/`weitere` sind Link-Ziele der
    Bereichs-Uebersichten (`parent:` bzw. `weitere_bereiche:`). Wirft `ValueError` fuer gesperrte Kontakte."""
    if k.gesperrt:
        raise ValueError("gesperrter Kontakt bekommt keinen Steckbrief")
    mail = k.email_klein
    bereiche = _bereiche(k)
    art = k.art if k.art in ARTEN else "person"
    schlagwoerter = [art, *bereiche[1:]]
    st = stand(k, ereignisse)
    name = _einzeilig(k.name, mail) or f"Kontakt {k.id[:8]}"

    zeilen = [
        "---",
        f"erzeugt_von: {ERZEUGT_VON}",
        "quelle: sales-claw",
        f"schluessel: {k.id}",
        f"laden: {laden}",
        f"art: {art}",
        f"schlagwoerter: [{', '.join(schlagwoerter)}]",
        f"stand: {st.isoformat()}",
    ]
    if uebersicht:
        zeilen.append(f"parent: {_links([uebersicht])}")
    if weitere:
        zeilen.append(f"weitere_bereiche: {_links(weitere)}")
    zeilen += [
        "---",
        "",
        f"> Automatisch erzeugt, Quelle sales-claw, Stand {st:%d.%m.%Y}. Nicht hier bearbeiten – der "
        "nächtliche Lauf überschreibt; eigene Anmerkungen in eine eigene Notiz.",
        "",
        f"# {name}",
        "",
    ]
    if k.archiviert:
        zeilen += ["> Archiviert in sales-claw (nicht gelöscht).", ""]

    felder: list[tuple[str, str | None]] = [
        ("Art", ARTEN[art]),
        ("Firma", _einzeilig(k.company, mail)),
        ("Rolle", _einzeilig(k.title, mail)),
        ("Bereiche", ", ".join(BEREICHE[b] for b in bereiche) or None),
        ("Themen", ", ".join(THEMEN[b] for b in bereiche if b in THEMEN) or None),
        ("Pipeline-Stufe", STUFEN.get(k.status or "", _einzeilig(k.status, mail))),
        ("Herkunft", _einzeilig(k.source, mail)),
        ("Letzter Kontakt", _letzter_kontakt(k, ereignisse)),
        ("Newsletter-Einwilligung", "ja" if k.consent_status == "opt_in" else "nein"),
        ("Empfohlen von", _empfohlen(empfohlen_von, mail)),
    ]
    zeilen += [f"- **{titel}:** {wert}" for titel, wert in felder if wert]

    termine = _offene_termine(k, ereignisse, heute)
    zeilen += ["", "## Offene Termine", ""]
    zeilen += [f"- {t}" for t in termine] if termine else ["Keine."]
    if people:
        zeilen += ["", f"Siehe auch: [[{people}]]"]
    return "\n".join(zeilen) + "\n"


# --- Bereichs-Uebersichten --------------------------------------------------------------------------

def _uebersicht_kopf(schluessel: str, eltern: str | None) -> list[str]:
    zeilen = ["---", f"erzeugt_von: {ERZEUGT_VON}", "quelle: sales-claw", f"schluessel: {schluessel}",
              "art: uebersicht"]
    if eltern:
        zeilen.append(f"parent: {_links([eltern])}")
    return zeilen + ["---", "",
                     "> Automatisch erzeugt aus sales-claw. Nicht hier bearbeiten – der nächtliche Lauf überschreibt; "
                     "eigene Anmerkungen in eine eigene Notiz.", ""]


def bereich_uebersicht(*, schluessel: str, firma: str, ordner: str, anzahl: int, neben: int, eltern: str) -> str:
    """Uebersicht eines Ordners unter `Kontakte/`. Bewusst ohne Datum und ohne Namen: sie aendert sich nur,
    wenn sich die Zahlen aendern, und enthaelt keine Personendaten."""
    firma_text = _einzeilig(firma, None) or firma
    zeilen = _uebersicht_kopf(schluessel, eltern) + [
        f"# Kontakte – {uebersicht_stamm(ordner)} ({firma_text})",
        "",
        f"- **Kontakte mit diesem Bereich als Hauptbereich:** {anzahl}",
        f"- **Kontakte mit diesem Bereich als Nebenbereich:** {neben}",
        "",
        f"Die Steckbriefe liegen im Ordner `Kontakte/{ordner}/` und verweisen hierher (`parent`); Kontakte mit "
        "diesem Bereich als Nebenbereich verweisen über `weitere_bereiche`.",
    ]
    return "\n".join(zeilen) + "\n"


def bereiche_uebersicht(*, schluessel: str, firma: str, bereiche: list[tuple[str, str, int]],
                        eltern: str | None) -> str:
    """Uebersicht aller Bereichs-Uebersichten einer Firma; `bereiche` = (Link-Ziel, Titel, Hauptbereich-Zahl)."""
    firma_text = _einzeilig(firma, None) or firma
    zeilen = _uebersicht_kopf(schluessel, eltern) + [f"# Kontakte nach Bereich ({firma_text})", ""]
    zeilen += [f"- [[{ziel}]] ({anzahl})" for ziel, _titel, anzahl in bereiche] or ["Keine Kontakte."]
    return "\n".join(zeilen) + "\n"
