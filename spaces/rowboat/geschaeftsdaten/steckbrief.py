"""Steckbrief je Kontakt: Kategorie, Stand, People-Link und Markdown (rein, ohne DB).

Inhalt nach Plan Teil B ("Inhalt des Steckbriefs", Betreiber-Entscheid 2): Name, Art, Firma, Rolle,
Bereiche, Pipeline-Stufe, Herkunft, letzter Kontakt (nur Datum und Richtung), offene Termine (nur Datum
und Uhrzeit, hoechstens 5), Newsletter-Einwilligung (ja/nein), Empfohlen von, Archiv-Hinweis,
"Siehe auch" auf eine People-Notiz. NIE E-Mail, Telefon, Kontaktprofil, Vertraege/Bedarf,
Nachrichtentexte, Termin-Thema oder -Ort. Die E-Mail dient nur dem People-Abgleich und wird aus jedem
geschriebenen Feld entfernt, falls sie dort vorkommt.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from namen import dateistamm
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
STUFEN = {"new": "Neu", "researched": "Recherchiert", "qualified": "Qualifiziert", "contacted": "Kontaktiert",
          "replied": "Geantwortet", "meeting": "Termin", "won": "Gewonnen", "lost": "Verloren"}
ARTEN = {"person": "Person", "firma": "Firma"}
EINGEHEND = frozenset({"kundenantwort", "nachricht"})
AUSGEHEND = frozenset({"nachricht_ausgehend", "versand"})
MAX_TERMINE = 5
_UHRZEIT = re.compile(r"^\d{1,2}:\d{2}$")
_LEERRAUM = re.compile(r"\s+")
_AUSLOESER = re.compile(r"@(rowboat)", re.IGNORECASE)
_KOPF_MAX = 4096


def _bereiche(k: Kontakt) -> list[str]:
    return [b for b in KATEGORIEN if b in k.bereiche]


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
    """Freitext einzeilig; eine darin vorkommende E-Mail-Adresse des Kontakts wird entfernt."""
    if wert is None:
        return None
    text = _LEERRAUM.sub(" ", wert).strip()
    if email:
        text = re.sub(re.escape(email), "[E-Mail entfernt]", text, flags=re.IGNORECASE)
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
    eigene = sorted((e for e in ereignisse if e.lead_id == k.id), key=lambda e: e.zeit)
    abgesagt = {e.termin_uid for e in eigene if e.typ == "termin_abgesagt" and e.termin_uid}
    je_uid: dict[str, Ereignis] = {}
    ohne_uid: list[Ereignis] = []
    for e in eigene:
        if e.typ != "termin":
            continue
        if e.termin_uid:
            je_uid[e.termin_uid] = e  # spaeterer Eintrag derselben uid gewinnt
        else:
            ohne_uid.append(e)
    termine: list[tuple[date, str]] = []
    for uid_e in [*je_uid.values(), *ohne_uid]:
        if uid_e.termin_uid in abgesagt:
            continue
        try:
            tag = date.fromisoformat(uid_e.termin_datum or "")
        except ValueError:
            continue
        if tag < heute:
            continue
        uhr = (uid_e.termin_uhrzeit or "").strip()
        termine.append((tag, uhr if _UHRZEIT.match(uhr) else ""))
    termine.sort(key=lambda t: (t[0], t[1].zfill(5)))
    return [f"{tag:%d.%m.%Y}, {uhr}" if uhr else f"{tag:%d.%m.%Y}" for tag, uhr in termine[:MAX_TERMINE]]


def _empfohlen(wert: str | None, email: str | None) -> str | None:
    text = _einzeilig(wert, email)
    if text and text.startswith("[[") and text.endswith("]]"):
        return text
    return text.replace("[[", "[ [") if text else None


def steckbrief(k: Kontakt, ereignisse: list[Ereignis], *, laden: str, heute: date, people: str | None,
               empfohlen_von: str | None) -> str:
    """Markdown des Steckbriefs. `people` ist ein Link-Ziel `People/<stamm>`; `empfohlen_von` ist ein
    fertiger Wiki-Link `[[...]]` oder ein Name. Wirft `ValueError` fuer gesperrte Kontakte."""
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
