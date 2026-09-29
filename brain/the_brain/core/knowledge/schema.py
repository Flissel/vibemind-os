"""Typisiertes Wissensdokument: Fakten mit Beleg, Deutung mit Belegpflicht.

Aufbau einer Datei:
  ---                      <- YAML-Kopf: Maschinendaten (Typ, ID, Fakten, Belege)
  ...
  ---
  # Titel
  ## Fakten                <- aus dem Kopf gerendert, nur zum Lesen
  ## Deutung               <- vom LLM oder von Hand; jeder Satz braucht [Bn]
  ## Verbindungen          <- [[Wikilinks]] auf andere Dokumente
  ## Belege                <- aus dem Kopf gerendert, nur zum Lesen
Beim Lesen gelten Kopf, Deutung und Verbindungen. Fakten und Belege im Text
sind Anzeige.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Literal, Optional, Set

import yaml
from pydantic import BaseModel, Field

DokTyp = Literal["bubble", "coding_projekt", "agent", "pc_zustand", "user"]

ORDNER: Dict[str, str] = {
    "bubble": "Bubbles",
    "coding_projekt": "Coding-Projects",
    "agent": "Agents",
    "pc_zustand": "System",
    "user": "People",
}

_VERBOTEN = re.compile(r'[<>:"/\\|?*\[\]#^]')
_BELEG_REF = re.compile(r"\[B(\d+)\]")
_SATZENDE = re.compile(r"(?<=[.!?])\s+")


class Beleg(BaseModel):
    nr: int = Field(ge=1)
    quelle: Literal["supabase", "openfang", "http"]
    ziel: str = Field(min_length=1)
    feld: str = Field(min_length=1)
    wert: str
    gemessen: datetime


class Fakt(BaseModel):
    schluessel: str = Field(min_length=1)
    wert: str
    beleg: int = Field(ge=1)


class Dokument(BaseModel):
    typ: DokTyp
    id: str = Field(min_length=1)
    titel: str = Field(min_length=1)
    stand: datetime
    fakten: List[Fakt]
    belege: List[Beleg]
    deutung: str = ""
    links: List[str] = Field(default_factory=list)


def dateiname(dok: Dokument) -> str:
    sicher = " ".join(_VERBOTEN.sub(" ", dok.titel).split()) or "Ohne Titel"
    return f"{sicher} ({dok.id[:6]})"


def deutung_saetze(text: str) -> List[str]:
    return [s.strip() for s in _SATZENDE.split((text or "").strip()) if s.strip()]


def pruefen(dok: Dokument, bekannte_dokumente: Optional[Set[str]] = None) -> List[str]:
    probleme: List[str] = []
    nrs = [b.nr for b in dok.belege]
    for n in sorted({n for n in nrs if nrs.count(n) > 1}):
        probleme.append(f"Belegnummer B{n} doppelt")
    belege = {b.nr: b for b in dok.belege}
    for f in dok.fakten:
        b = belege.get(f.beleg)
        if b is None:
            probleme.append(f"Fakt '{f.schluessel}' verweist auf fehlenden Beleg B{f.beleg}")
        elif b.wert != f.wert:
            probleme.append(f"Fakt '{f.schluessel}' weicht von B{f.beleg} ab "
                            f"({f.wert!r} gegen {b.wert!r})")
    for satz in deutung_saetze(dok.deutung):
        refs = [int(n) for n in _BELEG_REF.findall(satz)]
        if not refs:
            probleme.append(f"Deutungssatz ohne Beleg: {satz[:80]!r}")
        for n in refs:
            if n not in belege:
                probleme.append(f"Deutung verweist auf fehlenden Beleg B{n}")
    if bekannte_dokumente is not None:
        for link in dok.links:
            if link not in bekannte_dokumente:
                probleme.append(f"Link auf unbekanntes Dokument: {link!r}")
    return probleme


def rendern(dok: Dokument) -> str:
    kopf = dok.model_dump(mode="json", exclude={"deutung", "links"})
    zeilen = ["---", yaml.safe_dump(kopf, allow_unicode=True, sort_keys=False).rstrip(), "---",
              f"# {dok.titel}", "", "## Fakten", "",
              "| Schluessel | Wert | Beleg |", "| --- | --- | --- |"]
    zeilen += [f"| {f.schluessel} | {f.wert} | [B{f.beleg}] |" for f in dok.fakten]
    zeilen += ["", "## Deutung", "", dok.deutung.strip(), "", "## Verbindungen", ""]
    zeilen += [f"- [[{l}]]" for l in dok.links]
    zeilen += ["", "## Belege", ""]
    zeilen += [f"- B{b.nr}: {b.quelle} `{b.ziel}` {b.feld} = `{b.wert}` @ "
               f"{b.gemessen.isoformat()}" for b in dok.belege]
    return "\n".join(zeilen) + "\n"


def _abschnitt(text: str, titel: str) -> str:
    m = re.search(rf"^## {re.escape(titel)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


def lesen(text: str) -> Dokument:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise ValueError("kein YAML-Kopf")
    kopf = yaml.safe_load(m.group(1)) or {}
    links = re.findall(r"^- \[\[(.+?)\]\]\s*$", _abschnitt(text, "Verbindungen"), re.M)
    return Dokument(**kopf, deutung=_abschnitt(text, "Deutung"), links=links)
